"""UI 색상, 스타일시트, 방패 아이콘."""
from __future__ import annotations

import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

GREEN = "#12A37F"
GREEN_DARK = "#0B7A5F"
ORANGE = "#F2994A"
RED = "#E5484D"
GRAY = "#8A97A3"
SIDEBAR = "#14212B"
SIDEBAR_HOVER = "#1E303D"
BG = "#F3F6F8"
CARD = "#FFFFFF"
TEXT = "#16232D"
SUBTEXT = "#5F6E7A"
BORDER = "#E3E8EC"

SEVERITY_COLORS = {"critical": RED, "high": ORANGE, "medium": "#E2B93B", "low": "#5B8DEF"}
SEVERITY_LABELS = {"critical": "심각", "high": "높음", "medium": "보통", "low": "낮음"}
ACTION_LABELS = {"logged": "기록", "redacted": "마스킹", "blocked": "차단"}
DIRECTION_LABELS = {"input": "입력", "output": "응답"}


def base_font() -> QFont:
    family = "Malgun Gothic" if sys.platform == "win32" else "Apple SD Gothic Neo"
    f = QFont(family)
    f.setPointSize(10 if sys.platform == "win32" else 13)
    return f


def shield_pixmap(size: int, color: str = GREEN, mark: str = "check") -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(size)
    path = QPainterPath()
    path.moveTo(s * 0.5, s * 0.04)
    path.lineTo(s * 0.88, s * 0.18)
    path.cubicTo(s * 0.88, s * 0.58, s * 0.74, s * 0.82, s * 0.5, s * 0.96)
    path.cubicTo(s * 0.26, s * 0.82, s * 0.12, s * 0.58, s * 0.12, s * 0.18)
    path.closeSubpath()
    p.fillPath(path, QColor(color))
    # 왼쪽 절반에 옅은 하이라이트
    hl = QPainterPath()
    hl.moveTo(s * 0.5, s * 0.04)
    hl.lineTo(s * 0.5, s * 0.96)
    hl.cubicTo(s * 0.26, s * 0.82, s * 0.12, s * 0.58, s * 0.12, s * 0.18)
    hl.closeSubpath()
    p.fillPath(hl, QColor(255, 255, 255, 38))

    pen = QPen(QColor("white"), max(2.0, s * 0.075), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    if mark == "check":
        p.drawPolyline([QPointF(s * 0.33, s * 0.5), QPointF(s * 0.46, s * 0.63), QPointF(s * 0.68, s * 0.38)])
    elif mark == "alert":
        p.drawLine(QPointF(s * 0.5, s * 0.3), QPointF(s * 0.5, s * 0.56))
        p.drawPoint(QPointF(s * 0.5, s * 0.7))
    elif mark == "pause":
        p.drawLine(QPointF(s * 0.42, s * 0.34), QPointF(s * 0.42, s * 0.64))
        p.drawLine(QPointF(s * 0.58, s * 0.34), QPointF(s * 0.58, s * 0.64))
    p.end()
    return pm


def shield_icon(color: str = GREEN, mark: str = "check") -> QIcon:
    icon = QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(shield_pixmap(sz, color, mark))
    return icon


def dot_pixmap(color: str, size: int = 10) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QRectF(0, 0, size, size))
    p.end()
    return pm


QSS = f"""
QMainWindow, QWidget#content {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}

QFrame#sidebar {{ background: {SIDEBAR}; }}
QLabel#brand {{ color: white; font-size: 17px; font-weight: 700; }}
QLabel#brandSub {{ color: #8FA3B1; font-size: 11px; }}
QPushButton#nav {{
    color: #C5D2DB; background: transparent; border: none; text-align: left;
    padding: 11px 18px; border-radius: 8px; font-size: 14px;
}}
QPushButton#nav:hover {{ background: {SIDEBAR_HOVER}; color: white; }}
QPushButton#nav:checked {{ background: {GREEN}; color: white; font-weight: 600; }}
QLabel#sidebarFoot {{ color: #6F8594; font-size: 11px; }}

QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#hero {{
    border-radius: 16px; border: none;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #E7F7F1, stop:1 #FFFFFF);
}}
QFrame#hero[state="warn"] {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FFF1E4, stop:1 #FFFFFF);
}}
QLabel#heroTitle {{ font-size: 30px; font-weight: 800; color: {GREEN_DARK}; }}
QLabel#heroTitle[state="warn"] {{ color: #C4621A; }}
QLabel#heroSub {{ font-size: 14px; color: {SUBTEXT}; }}
QLabel#chip {{
    background: white; border: 1px solid {BORDER}; border-radius: 13px;
    padding: 4px 12px; font-size: 12px; color: {SUBTEXT};
}}
QLabel#statValue {{ font-size: 28px; font-weight: 800; }}
QLabel#statLabel {{ font-size: 12px; color: {SUBTEXT}; }}
QLabel#sectionTitle {{ font-size: 16px; font-weight: 700; }}
QLabel#pageTitle {{ font-size: 22px; font-weight: 800; }}
QLabel#muted {{ color: {SUBTEXT}; font-size: 12px; }}
QLabel#preview {{ color: {SUBTEXT}; font-size: 12px; }}

QTableWidget {{
    background: white; border: 1px solid {BORDER}; border-radius: 10px;
    gridline-color: transparent; selection-background-color: #DDF3EC; selection-color: {TEXT};
}}
QTableWidget::item {{ padding: 6px; border-bottom: 1px solid #F0F3F5; }}
QHeaderView::section {{
    background: #F7F9FA; color: {SUBTEXT}; border: none; border-bottom: 1px solid {BORDER};
    padding: 8px 6px; font-weight: 600;
}}
QTextEdit {{ background: white; border: 1px solid {BORDER}; border-radius: 10px; padding: 8px; }}

QPushButton#primary {{
    background: {GREEN}; color: white; border: none; border-radius: 8px;
    padding: 9px 20px; font-weight: 600;
}}
QPushButton#primary:hover {{ background: {GREEN_DARK}; }}
QCheckBox, QRadioButton {{ spacing: 8px; font-size: 14px; }}
QComboBox {{ background: white; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 10px; }}
"""
