"""Supervised, dedicated-browser pilot. No machine-wide network or trust changes."""
from __future__ import annotations

import atexit
import base64
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import queue
import signal
import sqlite3
import ssl
import subprocess
import threading
import time
import uuid

from .detectors import RULES_BY_ID

START_TIMEOUT = 20.0
HEALTH_INTERVAL = 30.0
HEALTH_TIMEOUT = 6.0
SCOPE = "dedicated ChatGPT browser"
BROWSERS = {"chrome": "Google Chrome", "edge": "Microsoft Edge",
            "firefox": "Mozilla Firefox", "whale": "NAVER Whale", "brave": "Brave"}
# A suspended-by-protocol launcher lets us assign the Windows Job before *any*
# proxy worker or Chrome child can be created. No shell or command interpolation.
_GATED_LAUNCHER = (
    "import os,subprocess,sys; "
    "gate=os.read(0,1); "
    "sys.exit(subprocess.call(sys.argv[1:]) if gate==b'\\n' else 125)"
)


class RuntimeFailure(RuntimeError):
    """A fixed diagnostic code, never an exception message or user data."""


def _load_config(path: Path) -> dict[str, Path]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        required = {"python", "agent_dir", "proxy_executable", "origin_ca", "data_dir"}
        browser_keys = {name + "_executable" for name in BROWSERS}
        if (not isinstance(raw, dict) or not required <= set(raw)
                or set(raw) - required - browser_keys or not set(raw) & browser_keys):
            raise RuntimeFailure("config_fields_invalid")
        result = {}
        for key in sorted(raw):
            value = raw[key]
            if not isinstance(value, str) or not value or "\x00" in value:
                raise RuntimeFailure("config_path_invalid:" + key)
            candidate = Path(value)
            # UNC/device paths and filesystem roots are never pilot directories.
            if not candidate.is_absolute() or value.startswith(("\\\\", "//")):
                raise RuntimeFailure("config_path_not_local_absolute:" + key)
            candidate = candidate.resolve()
            if candidate == Path(candidate.anchor):
                raise RuntimeFailure("config_root_forbidden:" + key)
            if key == "data_dir":
                if candidate.exists() and not candidate.is_dir():
                    raise RuntimeFailure("config_directory_invalid:" + key)
            elif key == "agent_dir":
                if not (candidate / "aidlp" / "worker.py").is_file():
                    raise RuntimeFailure("config_agent_missing")
            elif not candidate.is_file():
                raise RuntimeFailure("config_file_missing:" + key)
            result[key] = candidate
        return result
    except RuntimeFailure:
        raise
    except Exception:
        raise RuntimeFailure("config_unreadable") from None


def _lock_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    else:
        base = Path.home() / ".local" / "state"
    return base / "Fastpace" / "AiDlp" / "browser-mvp.lock"


class _UserLock:
    """Kernel-released user lock; a stale file is harmless after a crash."""

    def __init__(self):
        self.handle = None

    def acquire(self):
        path = _lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeFailure("runtime_already_running") from None
        self.handle = handle

    def release(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None
        # Never unlink: another waiting owner could hold the existing inode.


class _ProcessOwner:
    """Own exactly the launched process trees, with crash cleanup on Windows."""

    def __init__(self, python: Path):
        self.python = python
        self.processes: list[subprocess.Popen] = []
        self.job = None
        if os.name == "nt":
            try:
                import win32job
            except ImportError:
                raise RuntimeFailure("windows_job_support_required") from None
            else:
                # pywin32 builds differ in accepting None for lpName. A unique
                # session-local name is compatible and cannot attach an old Job.
                self.job = win32job.CreateJobObject(None, "Local\\Fastpace.AiDlp.BrowserMvp." + uuid.uuid4().hex)
                info = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
                info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, info)

    def spawn(self, args: list[str], *, cwd: Path) -> subprocess.Popen:
        command = ([str(self.python), "-u", "-c", _GATED_LAUNCHER, *args]
                   if self.job is not None else args)
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(command, cwd=str(cwd), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **options)
        self.processes.append(process)
        if self.job is not None:
            try:
                import win32job
                win32job.AssignProcessToJobObject(self.job, int(process._handle))
                process.stdin.write(b"\n")
                process.stdin.flush()
            except Exception:
                process.kill()  # Gate not released: there are no descendants yet.
                process.wait(timeout=3)
                raise RuntimeFailure("process_job_assignment_failed") from None
        return process

    def close(self) -> bool:
        ok = True
        remaining = []
        # Keep job ownership until explicit termination has completed, then close.
        if self.job is not None:
            try:
                import win32job
                win32job.TerminateJobObject(self.job, 0)
            except Exception:
                ok = False
            finally:
                self.job.Close()
                self.job = None
        for process in reversed(self.processes):
            try:
                if os.name != "nt":
                    # A dead root may still have live descendants in its group.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif process.poll() is None:
                    result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
                    if result.returncode != 0 and process.poll() is None:
                        ok = False
                process.wait(timeout=3)
            except Exception:
                ok = False
                remaining.append(process)
            finally:
                if process.poll() is not None:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            try:
                                stream.close()
                            except OSError:
                                pass
        self.processes = remaining
        return ok


class RuntimeController:
    """Thread-safe synchronous actions; callers run actions outside the GUI thread."""

    def __init__(self, config_path: Path):
        self.config = _load_config(Path(config_path))
        self._browser_id = next(name for name in BROWSERS if name + "_executable" in self.config)
        self._operation = threading.Lock()
        self._status = threading.RLock()
        self._cancel = threading.Event()
        self._user_lock = _UserLock()
        self._owner = None
        self._proxy = None
        self._browser = None
        self._monitor = None
        self._session: Path | None = None
        self._port: int | None = None
        self._pin = None
        self._state = "stopped"
        self._message = "Dedicated browser inspection is stopped."
        self._errors: list[str] = []
        self._last_health = None
        atexit.register(self.stop)

    def _set(self, state: str, message: str, error: str | None = None):
        with self._status:
            self._state, self._message = state, message
            if error and error not in self._errors:
                self._errors.append(error)

    def start(self, browser_id: str | None = None):
        with self._operation:
            selected = self._browser_id if browser_id is None else browser_id
            if not isinstance(selected, str) or selected not in BROWSERS or selected + "_executable" not in self.config:
                raise RuntimeFailure("browser_not_available")
            if self._owner is not None and selected != self._browser_id:
                raise RuntimeFailure("browser_switch_requires_stop")
            if self._owner is not None and "owned_process_cleanup_failed" in self._errors:
                self._set("error", "Retry stop before restarting; owned process cleanup is incomplete.")
                return
            if self._proxy is not None and self._proxy.poll() is None:
                return
            if self._owner is not None:
                self._cleanup()
                if self._owner is not None:
                    self._set("error", "Retry stop before restarting; owned process cleanup is incomplete.")
                    return
            self._cancel = threading.Event()
            self._errors = []
            self._last_health = None
            self._set("starting", "Starting the proxy and verifying local inspection.")
            try:
                if not self.config[selected + "_executable"].is_file():
                    raise RuntimeFailure("browser_not_available")
                self._browser_id = selected
                self._user_lock.acquire()
                self.config["data_dir"].mkdir(parents=True, exist_ok=True)
                self._session = self.config["data_dir"] / "sessions" / (
                    time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
                self._session.mkdir(parents=True)
                (self._session / "session.json").write_text(
                    json.dumps({"browser": selected, "scope": SCOPE}), encoding="utf-8")
                self._owner = _ProcessOwner(self.config["python"])
                self._proxy = self._owner.spawn([
                    str(self.config["proxy_executable"]), "--state", str(self._session),
                    "--origin-ca", str(self.config["origin_ca"]), "--python", str(self.config["python"]),
                    "--agent", str(self.config["agent_dir"]), "--delay-ms", "0", "--live-chatgpt",
                ], cwd=self.config["agent_dir"])
                self._port = self._await_ready(self._proxy, self._cancel)
                self._pin = self._certificate_pin()
                self._verify_inspection()
                if self._cancel.is_set():
                    raise RuntimeFailure("startup_cancelled")
                self._launch_browser()
                self._set("running", "Local inspection verified; only this dedicated browser is covered.")
                self._monitor = threading.Thread(target=self._supervise, args=(self._cancel,),
                                                 name="aidlp-supervisor", daemon=True)
                self._monitor.start()
            except Exception as exc:
                code = str(exc) if isinstance(exc, RuntimeFailure) else "runtime_start_failed"
                self._cleanup()
                message = ("Startup failed; owned process cleanup is incomplete."
                           if self._owner is not None else "Startup failed. The dedicated browser has been stopped.")
                self._set("error", message, code)

    @staticmethod
    def _await_ready(process, cancelled: threading.Event) -> int:
        result = queue.Queue(maxsize=1)

        def read():
            try:
                result.put(process.stdout.readline(4097))
            except Exception:
                result.put(b"")

        # Unlike a ThreadPoolExecutor, a timed-out read cannot block shutdown.
        threading.Thread(target=read, name="aidlp-readiness", daemon=True).start()
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if cancelled.is_set():
                raise RuntimeFailure("startup_cancelled")
            if process.poll() is not None:
                raise RuntimeFailure("proxy_exited_before_ready")
            try:
                line = result.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                ready = json.loads(line) if len(line) <= 4096 else None
                if (not isinstance(ready, dict) or ready.get("ready") is not True
                        or type(ready.get("port")) is not int or not 1 <= ready["port"] <= 65535):
                    raise ValueError()
                return ready["port"]
            except (ValueError, TypeError):
                raise RuntimeFailure("proxy_readiness_invalid") from None
        raise RuntimeFailure("proxy_readiness_timeout")

    def _certificate_pin(self) -> str:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        cert = x509.load_pem_x509_certificate((self._session / "proxy-ca.pem").read_bytes())
        spki = cert.public_key().public_bytes(serialization.Encoding.DER,
                                              serialization.PublicFormat.SubjectPublicKeyInfo)
        return base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")

    @contextmanager
    def _db(self):
        if self._session is None:
            raise RuntimeFailure("session_unavailable")
        connection = sqlite3.connect((self._session / "events.db").as_uri() + "?mode=ro",
                                     uri=True, timeout=.2)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _verify_inspection(self):
        """A blocked local-only canary must also have a newly committed email event."""
        try:
            # Worker initialization can race with the proxy's ready announcement.
            deadline = time.monotonic() + 2
            while True:
                try:
                    with self._db() as db:
                        before = db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
                    break
                except sqlite3.Error:
                    if self._cancel.wait(.05) or time.monotonic() >= deadline:
                        raise RuntimeFailure("inspection_store_unavailable") from None
            context = ssl.create_default_context(cafile=str(self._session / "proxy-ca.pem"))
            connection = http.client.HTTPSConnection("127.0.0.1", self._port,
                                                     context=context, timeout=HEALTH_TIMEOUT)
            connection.set_tunnel("localhost", 443)
            try:
                connection.request("POST", "/aidlp-runtime-health", body=b'{"prompt":"aidlp-health@example.test"}',
                                   headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                blocked = response.status == 403 and response.read(32) == b"blocked"
            finally:
                connection.close()
            with self._db() as db:
                rows = db.execute("SELECT findings FROM events WHERE id > ? AND host = 'localhost' AND action = 'blocked'",
                                  (before,)).fetchall()
            recorded = any(any(item.get("rule") == "email" for item in json.loads(row["findings"])) for row in rows)
            if not blocked or not recorded:
                raise RuntimeFailure("inspection_canary_failed")
            self._last_health = time.time()
        except RuntimeFailure:
            raise
        except Exception:
            raise RuntimeFailure("inspection_health_failed") from None

    def _launch_browser(self):
        if self._cancel.is_set():
            raise RuntimeFailure("startup_cancelled")
        # Preserve the original Chrome profile; each other browser owns its profile.
        profile = (self.config["data_dir"] / "browser-profile" if self._browser_id == "chrome"
                   else self.config["data_dir"] / "browser-profiles" / self._browser_id)
        profile.mkdir(parents=True, exist_ok=True)
        if self._browser_id == "firefox":
            self._prepare_firefox(profile)
            # Windows Firefox normally exits its launcher after handing off to
            # the UI process. Keep it alive so health/cleanup follow the browser.
            # Headless tests wait implicitly and would otherwise mask this bug.
            args = [str(self.config["firefox_executable"]), "-wait-for-browser", "-no-remote", "-new-instance",
                    "-profile", str(profile), "https://chatgpt.com/"]
        else:
            args = [
                str(self.config[self._browser_id + "_executable"]), "--user-data-dir=" + str(profile),
                "--no-first-run", "--no-default-browser-check", "--disable-quic",
                "--disable-background-mode",
                "--proxy-server=http://127.0.0.1:" + str(self._port),
                "--ignore-certificate-errors-spki-list=" + self._pin,
                "https://chatgpt.com/",
            ]
        self._browser = self._owner.spawn(args, cwd=self.config["data_dir"])
        # A stale profile owner or failed launch exits promptly. Never attach to it.
        if self._cancel.wait(.35):
            raise RuntimeFailure("startup_cancelled")
        if self._browser.poll() is not None:
            raise RuntimeFailure("dedicated_browser_start_failed")

    def _prepare_firefox(self, profile: Path):
        from .firefox_trust import MARKER
        expected = self.config["data_dir"] / "browser-profiles" / "firefox"
        if profile.resolve() != expected:
            raise RuntimeFailure("firefox_profile_invalid")
        (profile / ".aidlp-owned-profile").write_text(MARKER, encoding="utf-8")
        preferences = {
            "network.proxy.type": 1,
            "network.proxy.http": "127.0.0.1", "network.proxy.http_port": self._port,
            "network.proxy.ssl": "127.0.0.1", "network.proxy.ssl_port": self._port,
            "network.proxy.no_proxies_on": "", "network.proxy.allow_hijacking_localhost": True,
            "network.proxy.failover_direct": False,
            "network.http.http3.enable": False,
            "security.enterprise_roots.enabled": False,
            "browser.shell.checkDefaultBrowser": False,
            "browser.startup.homepage_override.mstone": "ignore",
            "browser.aboutwelcome.enabled": False,
            "browser.sessionstore.resume_from_crash": False,
        }
        (profile / "user.js").write_text(
            "// AI DLP application-owned profile only.\n" + "".join(
                "user_pref(" + json.dumps(key) + ", " + json.dumps(value) + ");\n"
                for key, value in preferences.items()), encoding="utf-8")
        helper = self._owner.spawn([
            str(self.config["python"]), "-m", "aidlp.firefox_trust",
            "--browser", str(self.config["firefox_executable"]),
            "--profile", str(profile), "--ca", str(self._session / "proxy-ca.pem"),
        ], cwd=self.config["agent_dir"])
        try:
            output, _ = helper.communicate(timeout=15)
            if helper.returncode != 0 or json.loads(output) != {"ready": True}:
                raise RuntimeFailure("firefox_profile_trust_failed")
        except Exception:
            raise RuntimeFailure("firefox_profile_trust_failed") from None

    def open_browser(self):
        with self._operation:
            if self._proxy is None or self._proxy.poll() is not None:
                self._set("error", "Start inspection before opening the dedicated browser.", "proxy_not_running")
                return
            if self._browser is not None and self._browser.poll() is None:
                return
            try:
                self._verify_inspection()
                self._launch_browser()
                self._set("running", "Local inspection verified; only this dedicated browser is covered.")
            except Exception as exc:
                code = str(exc) if isinstance(exc, RuntimeFailure) else "browser_open_failed"
                self._cancel.set()
                self._cleanup()
                message = ("Browser startup failed; owned process cleanup is incomplete."
                           if self._owner is not None else "The dedicated browser could not be opened safely.")
                self._set("error", message, code)

    def _supervise(self, cancelled: threading.Event):
        next_health = time.monotonic() + HEALTH_INTERVAL
        while not cancelled.wait(.5):
            if not self._operation.acquire(blocking=False):
                continue
            try:
                if cancelled.is_set():
                    return
                if self._proxy is None or self._proxy.poll() is not None:
                    raise RuntimeFailure("proxy_exited")
                if time.monotonic() >= next_health:
                    self._verify_inspection()
                    next_health = time.monotonic() + HEALTH_INTERVAL
                if self._browser is None or self._browser.poll() is not None:
                    self._set("degraded", "The dedicated browser is closed; reopen it to resume.")
            except Exception as exc:
                code = str(exc) if isinstance(exc, RuntimeFailure) else "runtime_health_failed"
                cancelled.set()
                self._cleanup()
                message = ("Inspection health failed; owned process cleanup is incomplete."
                           if self._owner is not None else "Inspection health failed. The dedicated browser has been stopped.")
                self._set("error", message, code)
                return
            finally:
                self._operation.release()

    def _cleanup(self):
        if self._owner is not None:
            try:
                closed = self._owner.close()
            except Exception:
                closed = False
            if not closed:
                if "owned_process_cleanup_failed" not in self._errors:
                    self._errors.append("owned_process_cleanup_failed")
                return  # Preserve ownership and the user lock so cleanup can retry.
            self._owner = None
        self._errors = [code for code in self._errors if code != "owned_process_cleanup_failed"]
        self._proxy = self._browser = None
        self._port = self._pin = None
        self._user_lock.release()

    def stop(self):
        self._cancel.set()  # Cancels a concurrent start before waiting for its lock.
        with self._operation:
            self._cleanup()
            if "owned_process_cleanup_failed" in self._errors:
                self._set("error", "Owned process cleanup could not be confirmed.")
                raise RuntimeFailure("owned_process_cleanup_failed")
            else:
                self._set("stopped", "Dedicated browser inspection is stopped.")

    def snapshot(self) -> dict:
        count = blocked = 0
        last = None
        read_error = False
        if self._session is not None and (self._session / "events.db").is_file():
            try:
                with self._db() as db:
                    row = db.execute("SELECT COUNT(*), COALESCE(SUM(action = 'blocked'), 0), MAX(ts) "
                                     "FROM events WHERE host = 'chatgpt.com'").fetchone()
                count, blocked, last = row
            except sqlite3.Error:
                read_error = True
        elif self._session is not None and self._state in {"running", "degraded"}:
            read_error = True
        with self._status:
            state, message = self._state, self._message
            errors = list(self._errors)
            if read_error:
                errors.append("event_store_read_failed")
                if state in {"running", "degraded"}:
                    state, message = "degraded", "Inspection event storage is unavailable."
            return {"state": state, "message": message, "proxy_port": self._port,
                    "browser_running": self._browser is not None and self._browser.poll() is None,
                    "session_dir": str(self._session) if self._session else None,
                    "event_count": count, "blocked_count": blocked, "last_event_ts": last,
                    "browser_id": self._browser_id, "browser_label": BROWSERS[self._browser_id],
                    "available_browsers": [{"id": name, "label": label} for name, label in BROWSERS.items()
                                           if name + "_executable" in self.config],
                    "scope": SCOPE, "errors": errors, "last_health_ts": self._last_health}

    def recent_events(self, limit=100) -> list[dict]:
        if self._session is None or not (self._session / "events.db").is_file():
            return []
        try:
            with self._db() as db:
                rows = db.execute("SELECT id, ts, action, severity, findings, chars FROM events "
                                  "WHERE host = 'chatgpt.com' ORDER BY id DESC LIMIT ?",
                                  (max(1, min(int(limit), 500)),)).fetchall()
            events = []
            for row in rows:
                # Reconstruct labels from shipped rules: never render stored preview,
                # match values, arbitrary labels, paths, URLs or other DB content.
                findings = []
                for item in json.loads(row["findings"]):
                    rule = RULES_BY_ID.get(item.get("rule"))
                    if rule:
                        findings.append({"rule": rule.id, "label": rule.label, "severity": rule.severity})
                events.append({"id": row["id"], "ts": row["ts"], "service": "ChatGPT",
                               "host": "chatgpt.com", "direction": "input",
                               "action": row["action"] if row["action"] in {"logged", "blocked"} else "blocked",
                               "severity": row["severity"] if row["severity"] in {"low", "medium", "high", "critical"} else None,
                               "findings": findings, "chars": row["chars"]})
            return events
        except (sqlite3.Error, ValueError, TypeError, AttributeError):
            return []
