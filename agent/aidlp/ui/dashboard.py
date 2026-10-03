"""Metadata-only dashboard and tray for the dedicated browser MVP.

All controller I/O runs on one worker thread. A running process is displayed as
runtime readiness, never as proof that a prompt was inspected.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QCloseEvent
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QMainWindow, QMenu, QPushButton, QScrollArea, QStackedWidget,
    QSystemTrayIcon, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..detectors import RULES
from ..runtime import BROWSERS
from .theme import (
    BORDER, GRAY, GREEN, GREEN_DARK, ORANGE, RED, SEVERITY_COLORS,
    SEVERITY_LABELS, SUBTEXT, shield_icon, shield_pixmap,
)


class Controller(Protocol):
    def start(self, browser_id: str | None = None): ...
    def open_browser(self): ...
    def stop(self): ...
    def snapshot(self) -> dict: ...
    def recent_events(self, limit: int = 100) -> list[dict]: ...


STATE_LABELS = {
    "stopped": "검사 중지됨", "starting": "검사 준비 중",
    "running": "검사 엔진 실행 중", "degraded": "검사 상태 확인 필요", "error": "실행 오류",
}
STATE_COLORS = {"stopped": GRAY, "starting": ORANGE, "running": GREEN, "degraded": ORANGE, "error": RED}
ERROR_LABELS = {
    "owned_process_cleanup_failed": "전용 프로세스 종료를 확인하지 못했습니다. 중지를 다시 시도해 주세요.",
    "runtime_already_running": "다른 AI DLP 창이 이미 실행 중입니다. 기존 창에서 검사를 관리해 주세요.",
    "event_store_read_failed": "검사 기록을 읽을 수 없습니다. 현재 검사 건수를 확인할 수 없습니다.",
    "inspection_store_unavailable": "검사 기록 저장소를 사용할 수 없어 검사를 시작할 수 없습니다.",
    "inspection_canary_failed": "로컬 검사 자체 점검에 실패했습니다. 중지 후 다시 시작해 주세요.",
    "inspection_health_failed": "로컬 검사 경로를 확인하지 못했습니다. 중지 후 다시 시작해 주세요.",
    "runtime_health_failed": "검사 엔진 상태 점검에 실패했습니다. 중지 후 다시 시작해 주세요.",
    "proxy_exited": "검사 엔진이 예기치 않게 종료되었습니다. 중지 후 다시 시작해 주세요.",
    "proxy_exited_before_ready": "검사 엔진이 준비되기 전에 종료되었습니다. 설치 상태를 확인해 주세요.",
    "proxy_readiness_invalid": "검사 엔진의 준비 상태를 확인하지 못했습니다. 설치 상태를 확인해 주세요.",
    "proxy_readiness_timeout": "검사 엔진 시작 시간이 초과되었습니다. 중지 후 다시 시작해 주세요.",
    "proxy_not_running": "검사 엔진이 중지되어 있습니다. 검사를 먼저 시작해 주세요.",
    "dedicated_browser_start_failed": "전용 브라우저를 시작하지 못했습니다. 선택한 브라우저 설치 상태를 확인해 주세요.",
    "firefox_profile_trust_failed": "Firefox 전용 인증서 설정에 실패하여 시작을 중단했습니다.",
    "firefox_profile_invalid": "Firefox 전용 프로필 경로를 확인할 수 없습니다.",
    "browser_not_available": "선택한 브라우저를 사용할 수 없습니다. 설치 경로를 확인해 주세요.",
    "browser_switch_requires_stop": "검사를 중지한 뒤 브라우저를 변경해 주세요.",
    "browser_open_failed": "전용 브라우저를 열지 못했습니다. 중지 후 다시 시작해 주세요.",
    "startup_cancelled": "검사 시작이 취소되었습니다.",
    "process_job_assignment_failed": "전용 프로세스를 안전하게 관리할 수 없어 시작을 취소했습니다.",
    "runtime_start_failed": "검사를 시작하지 못했습니다. 실행 설정과 설치 상태를 확인해 주세요.",
    "windows_job_support_required": "Windows 프로세스 관리 구성 요소가 없습니다. 설치 상태를 확인해 주세요.",
}


def _status_message(state: str, browser_running: bool, errors: list[str]) -> str:
    if "owned_process_cleanup_failed" in errors:
        return "전용 브라우저나 검사 프로세스가 남아 있을 수 있습니다. 중지를 다시 시도해 주세요."
    if "event_store_read_failed" in errors:
        return "검사 기록을 읽을 수 없어 현재 검사 결과를 확인할 수 없습니다."
    if state == "running":
        return "로컬 검사 경로의 자체 점검을 통과했습니다. 실제 요청 결과는 아래 기록에서 확인하세요."
    if state == "degraded":
        return ("전용 브라우저가 닫혀 있습니다. 다시 열면 검사를 이어갈 수 있습니다."
                if not browser_running else "검사 상태를 확인해야 합니다. 아래 안내를 확인해 주세요.")
    return {
        "stopped": "검사 시작을 누르면 전용 ChatGPT 브라우저가 열립니다.",
        "starting": "검사 엔진과 전용 브라우저를 준비하고 있습니다…",
        "error": "검사가 정상 동작한다고 판단할 수 없습니다. 아래 안내를 확인해 주세요.",
    }[state]


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    if name:
        label.setObjectName(name)
    return label


def _time(value, *, full: bool = False) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime("%m.%d %H:%M:%S" if full else "%H:%M:%S")
    except (ValueError, TypeError, OverflowError, OSError):
        return "—"


def _count(value) -> int:
    try:
        return max(0, int(value))
    except (ValueError, TypeError, OverflowError):
        return 0


class _RuntimeWorker(QObject):
    updated = Signal(dict, list)
    failed = Signal(str)
    completed = Signal(str)

    def __init__(self, controller: Controller):
        super().__init__()
        self.controller = controller

    @Slot(str)
    def execute(self, operation: str) -> None:
        try:
            if operation.startswith("start:"):
                self.controller.start(browser_id=operation.partition(":")[2])
            elif operation in ("start", "open_browser", "stop"):
                getattr(self.controller, operation)()
            self.updated.emit(self.controller.snapshot(), self.controller.recent_events(limit=100))
        except Exception:
            # Do not pass arbitrary exception text, paths or request data into UI.
            self.failed.emit(operation)
        finally:
            self.completed.emit(operation)


class Dashboard(QMainWindow):
    requested = Signal(str)

    def __init__(self, controller: Controller, *, auto_start: bool = False, enable_tray: bool = True):
        super().__init__()
        self.setWindowTitle("AI DLP · ChatGPT 전용 브라우저")
        self.resize(1210, 820)
        self.setMinimumSize(940, 660)
        self.setWindowIcon(shield_icon())
        self._busy = False
        self._quitting = False
        self._can_close = False
        self._state = "stopped"
        self._browser_running = False
        self._last_events: list[dict] | None = None
        self._last_snapshot: dict = {}
        self._block_session: str | None = None
        self._seen_blocks = 0
        self._operation_error = ""
        self._tray: QSystemTrayIcon | None = None
        self._tray_actions: dict[str, QAction] = {}
        self._build_layout()
        if enable_tray and QSystemTrayIcon.isSystemTrayAvailable():
            self._build_tray()

        self._thread = QThread(self)
        self._worker = _RuntimeWorker(controller)
        self._worker.moveToThread(self._thread)
        self.requested.connect(self._worker.execute)
        self._worker.updated.connect(self.refresh_state)
        self._worker.failed.connect(self._failed)
        self._worker.completed.connect(self._completed)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._finish_shutdown)
        self._thread.start()
        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(lambda: self.command("refresh"))
        self._timer.start()
        self.refresh_state({"state": "stopped", "message": "검사 시작을 누르면 전용 ChatGPT 브라우저가 열립니다."}, [])
        QTimer.singleShot(0, lambda: self.command("start" if auto_start else "refresh"))

    def _build_layout(self) -> None:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.setCentralWidget(container)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(208)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 30, 20, 24)
        brand_row = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(shield_pixmap(36))
        brand_row.addWidget(logo)
        brand_row.addWidget(_label("AI DLP", "brand"))
        brand_row.addStretch()
        side.addLayout(brand_row)
        side.addWidget(_label("FASTPACE  /  BROWSER MVP", "brandSub"))
        side.addSpacing(35)
        self._nav: list[QPushButton] = []
        for index, title in enumerate(("검사 현황", "검사 규칙 · 범위")):
            button = QPushButton(title)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, i=index: self._show_page(i))
            side.addWidget(button)
            self._nav.append(button)
        self._nav[0].setChecked(True)
        side.addStretch()
        side.addWidget(_label("전용 ChatGPT 브라우저\n민감정보 전송 차단", "sidebarFoot", True))
        side.addSpacing(12)
        side.addWidget(_label("PILOT · 로컬 검사", "sidebarFoot"))
        row.addWidget(sidebar)

        self._pages = QStackedWidget()
        self._pages.addWidget(self._overview())
        self._pages.addWidget(self._coverage())
        row.addWidget(self._pages, 1)

    def _overview(self) -> QWidget:
        page = QWidget()
        page.setObjectName("content")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(16)
        heading = QHBoxLayout()
        heading.addWidget(_label("ChatGPT 검사 현황", "pageTitle"))
        heading.addStretch()
        heading.addWidget(_label("민감정보 발견 시 차단", "chip"))
        layout.addLayout(heading)
        layout.addWidget(_label("이 앱에서 연 전용 ChatGPT 브라우저에만 적용됩니다.", "muted", True))

        self._hero = QFrame()
        self._hero.setObjectName("hero")
        hero = QVBoxLayout(self._hero)
        hero.setContentsMargins(22, 20, 22, 20)
        hero.setSpacing(10)
        status_row = QHBoxLayout()
        self._shield = QLabel()
        status_row.addWidget(self._shield)
        self.status_label = _label("", "heroTitle")
        self.status_label.setStyleSheet("font-size: 25px;")
        status_row.addWidget(self.status_label, 1)
        hero.addLayout(status_row)
        self.message_label = _label("", "heroSub", True)
        hero.addWidget(self.message_label)
        self.evidence_label = _label("", "muted", True)
        hero.addWidget(self.evidence_label)
        self.error_label = _label("", wrap=True)
        self.error_label.setStyleSheet(f"color: {RED}; font-size: 12px;")
        self.error_label.hide()
        hero.addWidget(self.error_label)
        browser_row = QHBoxLayout()
        browser_row.addWidget(_label("검사할 브라우저"))
        self.browser_select = QComboBox()
        self.browser_select.setMinimumWidth(175)
        self.browser_select.setMinimumHeight(34)
        self.browser_select.setAccessibleName("검사할 브라우저")
        self.browser_select.setToolTip("중지 상태에서 선택할 수 있습니다. 일반 브라우저 창에는 적용되지 않습니다.")
        browser_row.addWidget(self.browser_select)
        browser_row.addWidget(_label("브라우저 변경은 검사 중지 후 가능합니다.", "muted"), 1)
        hero.addLayout(browser_row)
        actions = QHBoxLayout()
        self.start_button = QPushButton("검사 시작")
        self.start_button.setObjectName("primary")
        self.start_button.setStyleSheet("QPushButton:disabled {background: #C4D8D2; color: #F5FAF8;}")
        self.open_button = QPushButton("브라우저 다시 열기")
        self.stop_button = QPushButton("중지")
        for button, operation in ((self.start_button, "start"), (self.open_button, "open_browser"), (self.stop_button, "stop")):
            button.setMinimumHeight(38)
            if operation != "start":
                button.setStyleSheet(
                    f"QPushButton {{background: white; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 14px;}}"
                    "QPushButton:disabled {color: #ACB6BD; background: #F5F7F8;}"
                )
            button.clicked.connect(lambda _checked=False, op=operation: self.command(op))
            actions.addWidget(button)
        actions.addStretch()
        hero.addLayout(actions)
        layout.addWidget(self._hero)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.event_count_label = self._stat_card(stats, "현재 세션 검사 요청", "0", GREEN_DARK)
        self.blocked_count_label = self._stat_card(stats, "현재 세션 차단 요청", "0", RED)
        self.last_event_label = self._stat_card(stats, "마지막 검사 · 로컬 시간", "—", SUBTEXT)
        layout.addLayout(stats)

        self.block_notice = _label("", wrap=True)
        self.block_notice.setStyleSheet(
            f"color: {RED}; background: #FFF1F0; border: 1px solid #FFD0CC; "
            "border-radius: 8px; padding: 10px 14px; font-size: 13px;"
        )
        self.block_notice.hide()
        layout.addWidget(self.block_notice)

        event_heading = QHBoxLayout()
        event_heading.addWidget(_label("최근 검사 기록", "sectionTitle"))
        event_heading.addStretch()
        event_heading.addWidget(_label("최근 100건 · 원문 저장 없음", "muted"))
        layout.addLayout(event_heading)
        self.event_table = QTableWidget(0, 4)
        self.event_table.setHorizontalHeaderLabels(["시간", "결정", "탐지 규칙 / 차단 사유", "심각도"])
        self.event_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.event_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.event_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.event_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.event_table.setShowGrid(False)
        self.event_table.verticalHeader().hide()
        self.event_table.verticalHeader().setDefaultSectionSize(42)
        self.event_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.event_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.event_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.event_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.event_table.setMinimumHeight(160)
        layout.addWidget(self.event_table, 1)
        self.empty_label = _label("아직 검사 기록이 없습니다. 시작 후 전용 브라우저에서 요청을 보내면 여기에 표시됩니다.", "muted", True)
        layout.addWidget(self.empty_label)
        layout.addWidget(_label(
            "기록 수에는 페이지의 백그라운드 요청도 포함됩니다. 대화 수와 다를 수 있습니다.\n"
            "일반 브라우저 · 다른 앱 · 다른 AI 서비스는 검사 범위에 포함되지 않습니다.", "muted", True,
        ))
        return page

    def _stat_card(self, parent: QHBoxLayout, title: str, value: str, color: str) -> QLabel:
        card = QFrame()
        card.setObjectName("card")
        box = QVBoxLayout(card)
        box.setContentsMargins(17, 13, 17, 13)
        metric = _label(value, "statValue")
        metric.setStyleSheet(f"color: {color};")
        box.addWidget(metric)
        box.addWidget(_label(title, "statLabel"))
        parent.addWidget(card, 1)
        return metric

    def _coverage(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        page.setObjectName("content")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        layout.addWidget(_label("검사 규칙과 적용 범위", "pageTitle"))
        layout.addWidget(_label("지원하는 요청에서 아래 규칙을 탐지하면 전송을 차단합니다.", "muted", True))
        rules_card = QFrame()
        rules_card.setObjectName("card")
        rules = QGridLayout(rules_card)
        rules.setContentsMargins(20, 16, 20, 16)
        rules.setVerticalSpacing(13)
        rules.setHorizontalSpacing(20)
        for index, rule in enumerate(RULES):
            row, column = divmod(index, 2)
            text = _label(rule.label)
            severity = _label(SEVERITY_LABELS.get(rule.severity, rule.severity))
            severity.setStyleSheet(f"color: {SEVERITY_COLORS.get(rule.severity, SUBTEXT)}; font-size: 12px;")
            rules.addWidget(text, row, column * 2)
            rules.addWidget(severity, row, column * 2 + 1)
        rules.setColumnStretch(0, 1)
        rules.setColumnStretch(2, 1)
        layout.addWidget(rules_card)

        sections = [
            ("어디에서 검사하나요?", "이 앱이 연 선택한 전용 브라우저의 chatgpt.com HTTPS 요청을 검사합니다. "
             "기존 브라우저, 다른 앱, 다른 AI 서비스에는 적용되지 않습니다. "
             "시스템 프록시나 Windows 인증서 저장소를 변경하지 않습니다."),
            ("무엇을 검사하나요?", "지원하는 UTF-8 JSON, 일반 텍스트, URL 인코딩 폼 요청을 전송 전에 검사합니다. "
             "파일 업로드, 지원하지 않는 인코딩, 크기 초과, 해석할 수 없는 요청 및 검사 오류는 차단합니다. "
             "파일 내용 분석과 응답 내용 검사는 지원하지 않습니다."),
            ("검사 결과를 어떻게 해석하나요?", "실행 중은 검사 엔진의 준비 상태입니다. 실제 검사는 요청 기록으로 확인하세요. "
             "정규식과 형식 검증을 사용하므로 모든 기밀을 식별하지는 못합니다. "
             "의미 기반 기밀, 메시지를 나눠 보내는 유출, 앱 내부 암호화는 탐지 범위 밖입니다."),
            ("어떤 데이터가 남나요?", "로컬 검사 기록에는 시간, 결정, 규칙 이름, 심각도 등의 메타데이터만 저장합니다. "
             "프롬프트·응답 원문과 탐지된 값은 저장하지 않습니다. 전용 브라우저에는 방문 기록과 사이트 데이터가 남을 수 있습니다."),
            ("중지하거나 종료하면?", "이 앱이 실행한 전용 브라우저와 검사 프로세스를 닫습니다. "
             "기존 브라우저는 그대로 유지됩니다. 창의 닫기 버튼은 앱을 종료합니다. "
             "트레이 메뉴의 ‘창 숨기기’는 검사를 계속 실행합니다. 사이트의 사람 확인이나 로그인은 자동으로 처리하지 않습니다."),
        ]
        for title, body in sections:
            card = QFrame()
            card.setObjectName("card")
            content = QVBoxLayout(card)
            content.setContentsMargins(18, 14, 18, 14)
            content.addWidget(_label(title, "sectionTitle"))
            content.addWidget(_label(body, "muted", True))
            layout.addWidget(card)
        layout.addStretch()
        scroll.setWidget(page)
        return scroll

    def _show_page(self, index: int) -> None:
        self._pages.setCurrentIndex(index)
        for item_index, button in enumerate(self._nav):
            button.setChecked(index == item_index)

    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(shield_icon(), self)
        menu = QMenu(self)
        show = menu.addAction("검사 현황 열기")
        show.triggered.connect(self._restore)
        hide = menu.addAction("창 숨기기 · 검사 유지")
        hide.triggered.connect(self.hide)
        menu.addSeparator()
        for text, command in (("검사 시작", "start"), ("브라우저 다시 열기", "open_browser"), ("검사 중지", "stop")):
            action = menu.addAction(text)
            action.triggered.connect(lambda _checked=False, op=command: self.command(op))
            self._tray_actions[command] = action
        menu.addSeparator()
        menu.addAction("종료 · 전용 브라우저 닫기").triggered.connect(self.request_shutdown)
        self._tray.setContextMenu(menu)
        self._tray.messageClicked.connect(self._restore)
        self._tray.activated.connect(lambda reason: self._restore() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self._tray.show()

    def _restore(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    @Slot(str)
    def command(self, operation: str) -> None:
        if self._busy or self._quitting or operation not in ("refresh", "start", "open_browser", "stop"):
            return
        self._busy = True
        if operation != "refresh":
            self._operation_error = ""
            self.error_label.hide()
            if operation == "start":
                self._state = "starting"
                self.status_label.setText(STATE_LABELS["starting"])
                self.status_label.setStyleSheet(f"font-size: 25px; color: {ORANGE};")
            self.message_label.setText({"start": "검사 엔진과 전용 브라우저를 준비하고 있습니다…",
                                        "open_browser": "전용 브라우저를 열고 있습니다…",
                                        "stop": "전용 브라우저와 검사 프로세스를 종료하고 있습니다…"}[operation])
        self._update_controls()
        selected = self.browser_select.currentData()
        self.requested.emit(f"start:{selected}" if operation == "start" and selected in BROWSERS else operation)

    @Slot(dict, list)
    def refresh_state(self, snapshot: dict, events: list[dict]) -> None:
        self._last_snapshot = dict(snapshot)
        state = snapshot.get("state", "error")
        self._state = state if state in STATE_LABELS else "error"
        self._browser_running = bool(snapshot.get("browser_running", False))
        options = snapshot.get("available_browsers") or []
        options = [(item.get("id"), BROWSERS[item["id"]]) for item in options
                   if isinstance(item, dict) and item.get("id") in BROWSERS]
        existing = [(self.browser_select.itemData(i), self.browser_select.itemText(i))
                    for i in range(self.browser_select.count())]
        if options != existing:
            selected = self.browser_select.currentData() or snapshot.get("browser_id")
            self.browser_select.clear()
            for browser_id, label in options:
                self.browser_select.addItem(label, browser_id)
            index = self.browser_select.findData(selected)
            if index >= 0:
                self.browser_select.setCurrentIndex(index)
        if self._state not in ("stopped", "error"):
            index = self.browser_select.findData(snapshot.get("browser_id"))
            if index >= 0:
                self.browser_select.setCurrentIndex(index)
        color = STATE_COLORS[self._state]
        self.status_label.setText(STATE_LABELS[self._state])
        self.status_label.setStyleSheet(f"font-size: 25px; color: {color};")
        self._shield.setPixmap(shield_pixmap(44, color, "check" if self._state == "running" else "pause" if self._state == "stopped" else "alert"))
        self._hero.setProperty("state", "warn" if self._state in ("degraded", "error", "starting") else "normal")
        self._hero.style().unpolish(self._hero)
        self._hero.style().polish(self._hero)
        errors = snapshot.get("errors") or []
        errors = [code for code in errors if isinstance(code, str)] if isinstance(errors, list) else []
        self.message_label.setText(_status_message(self._state, self._browser_running, errors))
        total, blocked = _count(snapshot.get("event_count")), _count(snapshot.get("blocked_count"))
        unknown_counts = "event_store_read_failed" in errors or snapshot.get("event_count", 0) is None
        self.event_count_label.setText("—" if unknown_counts else f"{total:,}")
        self.blocked_count_label.setText("—" if unknown_counts else f"{blocked:,}")
        self.last_event_label.setText("—" if unknown_counts else _time(snapshot.get("last_event_ts")))
        self._update_block_notice(snapshot, events, unknown_counts)
        name = BROWSERS.get(snapshot.get("browser_id"), "브라우저")
        browser = f"전용 {name} 열림" if self._browser_running else "전용 브라우저 닫힘"
        evidence = ("현재 검사 기록을 확인할 수 없습니다." if unknown_counts else
                    "아직 ChatGPT 검사 요청이 기록되지 않았습니다." if total == 0 else
                    "요청별 검사 결과는 아래 기록에서 확인하세요.")
        self.evidence_label.setText(f"{browser}  ·  {evidence}")
        error = self._operation_error or "\n".join(dict.fromkeys(
            ERROR_LABELS.get(code, "실행 중 오류가 발생했습니다. 중지 후 설치 상태를 확인해 주세요.") for code in errors[:3]
        ))
        self.error_label.setText(error)
        self.error_label.setVisible(bool(error))
        self._render_events([] if unknown_counts else events)
        unavailable_events = unknown_counts or (total > 0 and not events)
        self.empty_label.setText(
            "최근 검사 기록을 읽을 수 없습니다. 기록 없음이나 검사 0건을 의미하지 않습니다."
            if unavailable_events else
            "아직 검사 기록이 없습니다. 시작 후 전용 브라우저에서 요청을 보내면 여기에 표시됩니다."
        )
        self.empty_label.setVisible(unavailable_events or not events)
        if self._tray:
            self._tray.setIcon(shield_icon(color, "check" if self._state == "running" else "pause"))
            self._tray.setToolTip(f"AI DLP · {STATE_LABELS[self._state]}\n전용 ChatGPT 브라우저만 적용")
        if self._quitting:
            self.status_label.setText("종료 중")
            self.message_label.setText("전용 브라우저와 검사 프로세스가 종료될 때까지 기다려 주세요.")
        self._update_controls()

    def _update_block_notice(self, snapshot: dict, events: list[dict], unknown: bool) -> None:
        session = snapshot.get("session_dir")
        count = _count(snapshot.get("blocked_count"))
        if not session or unknown:
            self.block_notice.hide()
            return  # Do not consume an unknown count or emit a false new alert.
        new_session = session != self._block_session
        previous = 0 if new_session else self._seen_blocks
        self._block_session = session
        self._seen_blocks = count
        self.block_notice.setVisible(count > 0)
        if count == 0:
            return
        latest = next((event for event in events if event.get("action") in ("blocked", "block")), None)
        labels = []
        for finding in (latest or {}).get("findings", []) or []:
            if isinstance(finding, dict):
                known = next((rule for rule in RULES if rule.id == finding.get("rule")), None)
                if known and known.label not in labels:
                    labels.append(known.label)
        reason = ", ".join(labels) if labels else "검사 불가 또는 정책 규칙"
        when = _time(latest.get("ts")) if latest else "시간은 검사 기록 참조"
        message = (f"최근 차단 · {when} · {reason}\n"
                   "요청을 차단했습니다. 입력 내용을 확인하고 민감정보를 제거한 뒤 다시 보내세요. "
                   "ChatGPT 화면에는 차단 이유가 표시되지 않을 수 있습니다.")
        self.block_notice.setText(message)
        # Do not replay historical notifications on attach/session changes.
        # Count deltas survive a burst that pushes the event outside 100 rows.
        if not new_session and count > previous and self._state in ("running", "degraded") and self._tray:
            self._tray.showMessage("AI DLP · 요청 차단", message,
                                   QSystemTrayIcon.MessageIcon.Warning, 8000)

    def _render_events(self, events: list[dict]) -> None:
        # Select only whitelisted metadata; never render preview, matched values,
        # URLs, query strings or arbitrary event dictionaries.
        rows = []
        for event in events[:100]:
            findings = event.get("findings") or []
            labels = []
            for finding in findings if isinstance(findings, list) else []:
                if isinstance(finding, dict):
                    rule = str(finding.get("rule", ""))
                    known = next((item for item in RULES if item.id == rule), None)
                    label = known.label if known else "정책 규칙"
                    if label not in labels:
                        labels.append(label)
            action = event.get("action")
            decision = {"logged": "허용", "allow": "허용", "blocked": "차단", "block": "차단"}.get(action, "확인 필요")
            reason = ", ".join(labels) if labels else "검사 불가 / 정책 차단" if decision == "차단" else "탐지 없음" if decision == "허용" else "확인 필요"
            severity = event.get("severity")
            rows.append({"time": _time(event.get("ts"), full=True), "decision": decision, "reason": reason,
                         "severity": SEVERITY_LABELS.get(severity, "—"), "color": SEVERITY_COLORS.get(severity, SUBTEXT)})
        if rows == self._last_events:
            return
        self._last_events = rows
        self.event_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, key in enumerate(("time", "decision", "reason", "severity")):
                item = QTableWidgetItem(row[key])
                item.setToolTip(row[key])
                if key == "severity":
                    item.setForeground(QColor(row["color"]))
                elif key == "decision":
                    item.setForeground(QColor(RED if row[key] == "차단" else GREEN_DARK))
                self.event_table.setItem(row_index, column, item)
        self.empty_label.setVisible(not rows)

    def _update_controls(self) -> None:
        available = not self._busy and not self._quitting
        self.browser_select.setEnabled(available and self._state == "stopped" and self.browser_select.count() > 0)
        values = {
            "start": available and self._state in ("stopped", "error"),
            "open_browser": available and self._state in ("running", "degraded") and not self._browser_running,
            "stop": available and self._state != "stopped",
        }
        for operation, button in (("start", self.start_button), ("open_browser", self.open_button), ("stop", self.stop_button)):
            button.setEnabled(values[operation])
            if operation in self._tray_actions:
                self._tray_actions[operation].setEnabled(values[operation])

    @Slot(str)
    def _failed(self, operation: str) -> None:
        self._operation_error = (
            "종료 상태를 확인하지 못했습니다. 전용 브라우저와 검사 프로세스가 남아 있을 수 있습니다."
            if operation == "stop" else
            "실행 상태를 확인하지 못했습니다. 검사가 정상 동작한다고 판단할 수 없습니다. 중지 후 다시 시작해 주세요."
        )
        self._state = "error"
        self.status_label.setText(STATE_LABELS["error"])
        self.status_label.setStyleSheet(f"font-size: 25px; color: {RED};")
        self.error_label.setText(self._operation_error)
        self.error_label.show()
        if self._quitting and operation == "stop":
            # Keep the app alive so stop can be retried; never claim clean exit.
            self._quitting = False
            self._timer.start()
            self._restore()

    @Slot(str)
    def _completed(self, operation: str) -> None:
        self._busy = False
        if self._quitting and operation == "stop":
            stopped = (
                self._last_snapshot.get("state") == "stopped"
                and self._last_snapshot.get("browser_running") is False
                and self._last_snapshot.get("proxy_port") is None
                and "owned_process_cleanup_failed" not in (self._last_snapshot.get("errors") or [])
            )
            if stopped:
                self._thread.quit()
                return
            self._failed("stop")
        self._update_controls()

    @Slot()
    def request_shutdown(self) -> None:
        if self._quitting or self._can_close:
            return
        self._quitting = True
        self._timer.stop()
        self.status_label.setText("종료 중")
        self.message_label.setText("전용 브라우저와 검사 프로세스를 종료하고 있습니다…")
        self._update_controls()
        # Queued behind a bounded in-progress command; no GUI-thread wait.
        self.requested.emit("stop")

    @Slot()
    def _finish_shutdown(self) -> None:
        self._can_close = True
        if self._tray:
            self._tray.hide()
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._can_close:
            event.accept()
        else:
            event.ignore()
            self.request_shutdown()
