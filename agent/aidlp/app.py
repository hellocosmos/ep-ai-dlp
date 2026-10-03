"""Desktop entry point for the dedicated ChatGPT browser pilot."""
from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from .ui.dashboard import Dashboard
from .ui.theme import QSS, base_font, shield_icon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI DLP dedicated ChatGPT browser")
    parser.add_argument("--config", required=True, type=Path, help="Runtime configuration JSON")
    parser.add_argument("--start", action="store_true", help="Start the dedicated browser on launch")
    args = parser.parse_args(argv)

    app = QApplication([sys.argv[0]])
    app.setApplicationName("AI DLP")
    app.setOrganizationName("Fastpace")
    app.setFont(base_font())
    app.setStyleSheet(QSS)
    app.setWindowIcon(shield_icon())
    app.setQuitOnLastWindowClosed(False)

    try:
        from .runtime import RuntimeController

        controller = RuntimeController(args.config)
    except Exception:
        # Configuration paths or exception contents may contain sensitive data.
        QMessageBox.critical(
            None, "AI DLP 시작 실패",
            "실행 설정을 읽을 수 없습니다. 런처의 설정 파일과 설치 경로를 확인해 주세요.\n"
            "전용 브라우저 검사는 시작되지 않았습니다.",
        )
        return 1

    window = Dashboard(controller, auto_start=args.start)
    signal.signal(signal.SIGINT, lambda *_: window.request_shutdown())
    signal.signal(signal.SIGTERM, lambda *_: window.request_shutdown())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
