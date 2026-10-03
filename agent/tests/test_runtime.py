import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from aidlp import runtime
from aidlp.detectors import detect
from aidlp.store import Store


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_lock_path", lambda: tmp_path / "user.lock")
    agent = tmp_path / "agent"
    (agent / "aidlp").mkdir(parents=True)
    (agent / "aidlp" / "worker.py").touch()
    paths = {"python": str(Path(sys.executable).resolve()), "agent_dir": str(agent),
             "data_dir": str(tmp_path / "data")}
    for key in ("proxy_executable", "chrome_executable", "origin_ca"):
        path = tmp_path / key
        path.touch()
        paths[key] = str(path)
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(paths), encoding="utf-8")
    return path


class FakeProcess:
    def __init__(self, ready=b'{"ready":true,"port":12345}\n'):
        self.stdout = io.BytesIO(ready)
        self.stdin = io.BytesIO()
        self.code = None
        self.pid = 100

    def poll(self):
        return self.code


class FakeOwner:
    instances = []

    def __init__(self, python):
        self.commands = []
        self.processes = []
        self.closed = False
        self.close_ok = True
        self.instances.append(self)

    def spawn(self, args, *, cwd):
        self.commands.append(args)
        process = FakeProcess()
        self.processes.append(process)
        return process

    def close(self):
        if not self.close_ok:
            return False
        self.closed = True
        for process in self.processes:
            process.code = 0
        return True


@pytest.fixture
def controller(config, monkeypatch):
    FakeOwner.instances.clear()
    monkeypatch.setattr(runtime, "_ProcessOwner", FakeOwner)
    control = runtime.RuntimeController(config)
    monkeypatch.setattr(control, "_certificate_pin", lambda: "test-session-spki")
    def healthy():
        Store(control._session / "events.db").close()
    monkeypatch.setattr(control, "_verify_inspection", healthy)
    yield control
    for owner in FakeOwner.instances:
        owner.close_ok = True
    control.stop()


@pytest.mark.parametrize("field,value,code", [
    ("python", "relative.exe", "config_path_not_local_absolute:python"),
    ("data_dir", "/", "config_root_forbidden:data_dir"),
    ("data_dir", "//server/share", "config_path_not_local_absolute:data_dir"),
    ("origin_ca", 42, "config_path_invalid:origin_ca"),
])
def test_invalid_config_diagnostics_are_fixed(config, field, value, code):
    if os.name == "nt" and value == "/":
        value = Path(config).anchor
    data = json.loads(config.read_text())
    data[field] = value
    config.write_text(json.dumps(data))
    with pytest.raises(runtime.RuntimeFailure, match=code):
        runtime.RuntimeController(config)


def test_unknown_config_fields_rejected_without_leaking_values(config):
    data = json.loads(config.read_text())
    data["secret"] = "private-token-do-not-show"
    config.write_text(json.dumps(data))
    with pytest.raises(runtime.RuntimeFailure) as error:
        runtime.RuntimeController(config)
    assert str(error.value) == "config_fields_invalid"


def test_start_stop_reopen_and_session_isolation(controller):
    controller.start()
    first = controller.snapshot()
    assert first["state"] == "running"
    assert first["browser_running"] is True
    owner = FakeOwner.instances[-1]
    proxy, chrome = owner.commands
    assert "--live-chatgpt" in proxy
    assert "--origin-ca" in proxy
    assert "--ignore-certificate-errors-spki-list=test-session-spki" in chrome
    assert "--proxy-server=http://127.0.0.1:12345" in chrome
    assert not any("remote-debugging" in item for item in chrome)
    assert "--ignore-certificate-errors" not in chrome
    assert any("browser-profile" in item for item in chrome)
    controller.start()
    controller.open_browser()
    assert len(owner.commands) == 2
    owner.processes[-1].code = 0
    controller.open_browser()
    assert len(owner.commands) == 3
    controller.stop()
    assert owner.closed
    assert controller.snapshot()["state"] == "stopped"
    assert controller.snapshot()["proxy_port"] is None
    controller.start()
    assert controller.snapshot()["session_dir"] != first["session_dir"]


def test_duplicate_runtime_cannot_spawn_second_tree(controller, config, monkeypatch):
    controller.start()
    second = runtime.RuntimeController(config)
    monkeypatch.setattr(second, "_certificate_pin", lambda: "pin")
    monkeypatch.setattr(second, "_verify_inspection", lambda: None)
    second.start()
    assert second.snapshot()["state"] == "error"
    assert second.snapshot()["errors"] == ["runtime_already_running"]
    assert len(FakeOwner.instances) == 1
    second.stop()
    assert not FakeOwner.instances[0].closed


def test_edge_launch_uses_own_profile_and_same_inspection_boundary(controller, tmp_path):
    edge = tmp_path / "msedge.exe"
    edge.touch()
    controller.config["edge_executable"] = edge
    controller.start("edge")
    snapshot = controller.snapshot()
    assert snapshot["browser_id"] == "edge"
    assert snapshot["browser_label"] == "Microsoft Edge"
    owner = FakeOwner.instances[-1]
    command = owner.commands[-1]
    assert command[0] == str(edge)
    assert "--proxy-server=http://127.0.0.1:12345" in command
    assert "--ignore-certificate-errors-spki-list=test-session-spki" in command
    assert "--disable-quic" in command
    assert not any("remote-debugging" in arg for arg in command)
    edge_profile = next(arg for arg in command if arg.startswith("--user-data-dir="))
    assert edge_profile.endswith(str(Path("browser-profiles") / "edge"))
    metadata = json.loads((Path(snapshot["session_dir"]) / "session.json").read_text())
    assert metadata["browser"] == "edge"
    controller.stop()
    controller.start("chrome")
    assert controller.snapshot()["browser_id"] == "chrome"
    chrome_profile = next(arg for arg in FakeOwner.instances[-1].commands[-1] if arg.startswith("--user-data-dir="))
    assert chrome_profile != edge_profile
    assert chrome_profile.endswith("browser-profile")


def test_cannot_switch_browser_while_any_owned_session_exists(controller, tmp_path):
    edge = tmp_path / "msedge.exe"
    edge.touch()
    controller.config["edge_executable"] = edge
    controller.start()
    owner = FakeOwner.instances[-1]
    with pytest.raises(runtime.RuntimeFailure, match="browser_switch_requires_stop"):
        controller.start("edge")
    assert controller.snapshot()["state"] == "running"
    assert controller.snapshot()["browser_id"] == "chrome"
    assert len(owner.commands) == 2 and not owner.closed


@pytest.mark.parametrize("browser", ["edge", "firefox", "../edge", [], 3])
def test_unavailable_browser_never_silently_falls_back_to_chrome(controller, browser):
    with pytest.raises(runtime.RuntimeFailure, match="browser_not_available"):
        controller.start(browser)
    assert not FakeOwner.instances


def test_browser_removed_after_config_load_fails_before_spawning(controller):
    controller.config["chrome_executable"].unlink()
    controller.start()
    assert controller.snapshot()["errors"] == ["browser_not_available"]
    assert not FakeOwner.instances


def test_edge_only_config_and_legacy_chrome_config_are_supported(config):
    assert runtime.RuntimeController(config).snapshot()["available_browsers"] == [
        {"id": "chrome", "label": "Google Chrome"}]
    data = json.loads(config.read_text())
    data["edge_executable"] = data.pop("chrome_executable")
    config.write_text(json.dumps(data))
    snapshot = runtime.RuntimeController(config).snapshot()
    assert snapshot["browser_id"] == "edge"
    assert snapshot["available_browsers"] == [{"id": "edge", "label": "Microsoft Edge"}]


def test_failure_closes_owned_tree_and_sanitizes_exception(controller, monkeypatch):
    def fail():
        raise OSError("private-token-do-not-show")
    monkeypatch.setattr(controller, "_certificate_pin", fail)
    controller.start()
    snapshot = controller.snapshot()
    assert snapshot["state"] == "error"
    assert snapshot["errors"] == ["runtime_start_failed"]
    assert "private-token" not in json.dumps(snapshot)
    assert FakeOwner.instances[0].closed
    assert controller._user_lock.handle is None


def test_failed_cleanup_retains_ownership_and_prevents_restart(controller):
    controller.start()
    owner = FakeOwner.instances[0]
    owner.close_ok = False
    with pytest.raises(runtime.RuntimeFailure, match="owned_process_cleanup_failed"):
        controller.stop()
    assert controller.snapshot()["state"] == "error"
    assert controller._user_lock.handle is not None
    controller.start()
    assert len(FakeOwner.instances) == 1
    owner.close_ok = True
    controller.stop()
    assert controller.snapshot()["state"] == "stopped"
    assert controller._user_lock.handle is None


@pytest.mark.parametrize("ready", [b"not JSON", b'{"ready":true,"port":true}',
                                    b'{"ready":false,"port":443}', b"x" * 4097])
def test_readiness_requires_exact_bounded_contract(ready):
    with pytest.raises(runtime.RuntimeFailure, match="proxy_readiness_invalid"):
        runtime.RuntimeController._await_ready(FakeProcess(ready), threading.Event())


def test_hung_readiness_is_bounded_and_cancellable(monkeypatch):
    release = threading.Event()
    class HungStream:
        def readline(self, limit):
            release.wait(3)
            return b""
    process = FakeProcess()
    process.stdout = HungStream()
    monkeypatch.setattr(runtime, "START_TIMEOUT", .1)
    started = time.monotonic()
    try:
        with pytest.raises(runtime.RuntimeFailure, match="proxy_readiness_timeout"):
            runtime.RuntimeController._await_ready(process, threading.Event())
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(runtime.RuntimeFailure, match="startup_cancelled"):
            runtime.RuntimeController._await_ready(process, cancel)
        assert time.monotonic() - started < 1
    finally:
        release.set()


def test_stop_cancels_concurrent_start(controller, monkeypatch):
    entered = threading.Event()
    def wait(process, cancelled):
        entered.set()
        assert cancelled.wait(2)
        raise runtime.RuntimeFailure("startup_cancelled")
    monkeypatch.setattr(controller, "_await_ready", wait)
    thread = threading.Thread(target=controller.start)
    thread.start()
    assert entered.wait(1)
    controller.stop()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert controller.snapshot()["state"] == "stopped"
    assert FakeOwner.instances[0].closed


def test_metadata_projection_and_counts_exclude_health_and_prompt_content(controller):
    controller.start()
    store = Store(controller._session / "events.db")
    secret = "alice@example.com"
    store.add("unexpected-sensitive-service", "input", secret, detect(secret),
              host="chatgpt.com", action="blocked")
    store.add("health", "input", secret, detect(secret), host="localhost", action="blocked", metadata_only=True)
    store.add("ChatGPT", "input", "ordinary prompt", [], host="chatgpt.com", metadata_only=True)
    # A legacy/malicious label must not pass through into the dashboard.
    store.conn.execute("UPDATE events SET findings = ? WHERE id = 1",
                       (json.dumps([{"rule": "email", "label": secret, "masked": secret, "value": secret}]),))
    store.conn.commit()
    store.close()
    snapshot = controller.snapshot()
    events = controller.recent_events()
    assert snapshot["event_count"] == 2
    assert snapshot["blocked_count"] == 1
    assert len(events) == 2
    assert events[-1]["findings"] == [{"rule": "email", "label": "이메일 주소", "severity": "low"}]
    assert secret not in json.dumps(events)
    assert "ordinary prompt" not in json.dumps(events)
    assert all("preview" not in event for event in events)
    assert all(event["service"] == "ChatGPT" for event in events)


def test_health_requires_new_committed_detector_event(controller, monkeypatch):
    controller.start()
    store = Store(controller._session / "events.db")
    store.add("health", "input", "", detect("old@example.test"), host="localhost", action="blocked", metadata_only=True)
    monkeypatch.setattr(runtime.ssl, "create_default_context", lambda **kwargs: object())
    calls = []
    class Connection:
        record = False
        def __init__(self, host, port, **kwargs):
            assert (host, port) == ("127.0.0.1", 12345)
        def set_tunnel(self, host, port):
            assert (host, port) == ("localhost", 443)
        def request(self, method, path, body, headers):
            calls.append((method, path))
            if self.record:
                store.add("health", "input", "", detect("new@example.test"), host="localhost", action="blocked", metadata_only=True)
        def getresponse(self):
            return type("Response", (), {"status": 403, "read": lambda self, n: b"blocked"})()
        def close(self):
            pass
    monkeypatch.setattr(runtime.http.client, "HTTPSConnection", Connection)
    try:
        with pytest.raises(runtime.RuntimeFailure, match="inspection_canary_failed"):
            runtime.RuntimeController._verify_inspection(controller)
        Connection.record = True
        runtime.RuntimeController._verify_inspection(controller)
        assert controller._last_health is not None
        assert len(calls) == 2
    finally:
        store.close()


def test_periodic_inspection_failure_closes_browser_and_proxy(controller, monkeypatch):
    monkeypatch.setattr(runtime, "HEALTH_INTERVAL", .01)
    controller.start()
    def fail():
        raise runtime.RuntimeFailure("inspection_health_failed")
    monkeypatch.setattr(controller, "_verify_inspection", fail)
    deadline = time.monotonic() + 2
    while controller.snapshot()["state"] == "running" and time.monotonic() < deadline:
        time.sleep(.02)
    assert controller.snapshot()["state"] == "error"
    assert FakeOwner.instances[0].closed
    assert not controller.snapshot()["browser_running"]


def test_missing_active_event_store_is_degraded_not_clean_zero(controller):
    controller.start()
    (controller._session / "events.db").unlink()
    snapshot = controller.snapshot()
    assert snapshot["state"] == "degraded"
    assert "event_store_read_failed" in snapshot["errors"]


def test_windows_gate_assigns_job_before_child_launch(monkeypatch, tmp_path):
    order = []
    class Handle:
        def __int__(self):
            return 456
        def Close(self):
            order.append("job_closed")
    class Input:
        def write(self, value):
            assert value == b"\n"
            order.append("gate_released")
        def flush(self):
            pass
    class Process(FakeProcess):
        _handle = 123
        def __init__(self, command, **kwargs):
            super().__init__()
            self.stdin = Input()
            self.command = command
            order.append("wrapper_started")
    def create_job(security, name):
        assert security is None
        assert isinstance(name, str) and name.startswith("Local\\Fastpace.AiDlp.BrowserMvp.")
        return Handle()
    job = SimpleNamespace(
        CreateJobObject=create_job,
        QueryInformationJobObject=lambda *_: {"BasicLimitInformation": {"LimitFlags": 0}},
        SetInformationJobObject=lambda *_: order.append("kill_on_close_enabled"),
        AssignProcessToJobObject=lambda *_: order.append("assigned_to_job"),
        JobObjectExtendedLimitInformation=9,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=8192,
    )
    monkeypatch.setitem(sys.modules, "win32job", job)
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(runtime, "subprocess", SimpleNamespace(Popen=Process, PIPE=-1, DEVNULL=-3, CREATE_NO_WINDOW=0))
    owner = runtime._ProcessOwner(Path(sys.executable))
    process = owner.spawn(["owned-proxy.exe", "--state", "pilot-state"], cwd=tmp_path)
    assert order == ["kill_on_close_enabled", "wrapper_started", "assigned_to_job", "gate_released"]
    assert process.command[-3:] == ["owned-proxy.exe", "--state", "pilot-state"]
    assert "os.read(0,1)" in process.command[3]


def test_windows_requires_job_support(monkeypatch):
    monkeypatch.setitem(sys.modules, "win32job", None)
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt"))
    with pytest.raises(runtime.RuntimeFailure, match="windows_job_support_required"):
        runtime._ProcessOwner(Path(sys.executable))


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group fallback; Windows acceptance validates Job Objects")
def test_real_cleanup_is_scoped_to_owned_process_group(tmp_path):
    owner = runtime._ProcessOwner(Path(sys.executable))
    # Other application's process must survive cleanup.
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        owned = owner.spawn([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path)
        assert owner.close()
        assert owned.poll() is not None
        assert unrelated.poll() is None
    finally:
        owner.close()
        unrelated.terminate()
        unrelated.wait(timeout=3)

@pytest.mark.parametrize('browser', ['brave', 'whale'])
def test_additional_chromium_browser_preserves_boundary(controller, tmp_path, browser):
    exe = tmp_path / (browser + '.exe')
    exe.touch()
    controller.config[browser + '_executable'] = exe
    controller.start(browser)
    assert controller.snapshot()['state'] == 'running'
    args = FakeOwner.instances[-1].commands[-1]
    assert args[0] == str(exe)
    assert '--proxy-server=http://127.0.0.1:12345' in args
    assert '--disable-quic' in args
    assert '--ignore-certificate-errors-spki-list=test-session-spki' in args
    assert next(a for a in args if a.startswith('--user-data-dir=')).endswith(str(Path('browser-profiles') / browser))


def test_firefox_trust_failure_prevents_browser_launch(controller, tmp_path, monkeypatch):
    exe = tmp_path / 'firefox.exe'
    exe.touch()
    controller.config['firefox_executable'] = exe
    def failed(profile):
        raise runtime.RuntimeFailure('firefox_profile_trust_failed')
    monkeypatch.setattr(controller, '_prepare_firefox', failed)
    controller.start('firefox')
    assert controller.snapshot()['state'] == 'error'
    assert controller.snapshot()['errors'] == ['firefox_profile_trust_failed']
    assert len(FakeOwner.instances[-1].commands) == 1
    assert FakeOwner.instances[-1].closed


def test_firefox_preferences_and_isolated_launch(controller, tmp_path, monkeypatch):
    exe = tmp_path / 'firefox.exe'
    exe.touch()
    controller.config['firefox_executable'] = exe
    monkeypatch.setattr(FakeProcess, 'communicate', lambda self, timeout: (b'{"ready":true}', None), raising=False)
    monkeypatch.setattr(FakeProcess, 'returncode', 0, raising=False)
    controller.start('firefox')
    assert controller.snapshot()['state'] == 'running'
    proxy, helper, browser = FakeOwner.instances[-1].commands
    assert 'aidlp.firefox_trust' in helper
    assert '-no-remote' in browser and '-new-instance' in browser
    assert '-wait-for-browser' in browser  # Headed Windows launcher lifetime.
    assert '-profile' in browser
    assert not any('ignore-certificate-errors' in arg for arg in browser)
    profile = Path(browser[browser.index('-profile') + 1])
    prefs = (profile / 'user.js').read_text()
    assert '"network.proxy.type", 1' in prefs
    assert '"network.proxy.ssl_port", 12345' in prefs
    assert '"network.proxy.failover_direct", false' in prefs
    assert '"network.http.http3.enable", false' in prefs
    assert '"security.enterprise_roots.enabled", false' in prefs
    assert profile.name == 'firefox'
    assert (profile / '.aidlp-owned-profile').is_file()
