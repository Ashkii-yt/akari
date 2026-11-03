"""The window she lives in: frameless, see-through, always on top, and not in the taskbar.
It's just a Chromium view showing web/index.html, where three.js draws the model."""
import json
import os
import shutil
import subprocess

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import config


class Bridge(QObject):
    """The asyncio thread can't touch Qt widgets directly, so it emits these signals instead."""
    drag = Signal(str, int, int)
    menu = Signal()
    quit_ = Signal()


def make_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#ff8fb8"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(4, 4, 56, 56)
    p.setPen(QColor("white"))
    p.setFont(QFont("sans", 30, QFont.Weight.Bold))
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, config.NAME[:1])
    p.end()
    return QIcon(pm)


class Overlay(QWebEngineView):
    def __init__(self, bridge):
        super().__init__()
        self.setWindowTitle(config.NAME)
        # Tool windows don't get a taskbar button
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet("background: transparent;")
        self.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.resize(config.WINDOW_W, config.WINDOW_H)

        self._drag_start = QPoint()
        self._place()
        bridge.drag.connect(self._on_drag)
        self.load(QUrl(f"http://{config.HOST}:{config.PORT}/"))

    def _place(self):
        pos = None
        try:
            saved = json.loads(config.STATE_FILE.read_text())
            pos = QPoint(int(saved["x"]), int(saved["y"]))
        except Exception:
            pass
        # if the saved spot is off-screen (monitor unplugged, say) go back to the corner
        on_screen = pos is not None and any(
            s.availableGeometry().contains(pos + QPoint(50, 50)) for s in QGuiApplication.screens())
        if not on_screen:
            area = QGuiApplication.primaryScreen().availableGeometry()
            pos = QPoint(area.right() - config.WINDOW_W - config.WINDOW_MARGIN,
                         area.bottom() - config.WINDOW_H - config.WINDOW_MARGIN)
        self.move(pos)

    def reset_position(self):
        config.STATE_FILE.unlink(missing_ok=True)
        self._place()

    def save_position(self):
        try:
            config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            config.STATE_FILE.write_text(json.dumps({"x": self.x(), "y": self.y()}))
        except OSError:
            pass

    def _on_drag(self, phase, dx, dy):
        if phase == "start":
            self._drag_start = self.pos()
        elif phase == "move":
            self.move(self._drag_start + QPoint(dx, dy))
        else:
            self.save_position()

    def show_and_pin(self):
        self.show()
        if config.SKIP_TASKBAR_WITH_WMCTRL and shutil.which("wmctrl") and os.environ.get("DISPLAY"):
            QTimer.singleShot(700, self._tell_window_manager)

    def _tell_window_manager(self):
        # XFCE's panel sometimes shows tool windows anyway, so ask it nicely not to
        subprocess.run(["wmctrl", "-i", "-r", hex(int(self.winId())), "-b",
                        "add,skip_taskbar,skip_pager,above,sticky"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Tray:
    def __init__(self, overlay, bridge, on_talk, on_stop, on_mute, on_quit):
        self.icon = QSystemTrayIcon(make_icon())
        self.icon.setToolTip(config.NAME)
        self.menu = QMenu()
        self.menu.addAction(f"Talk to {config.NAME}").triggered.connect(on_talk)
        self.menu.addAction("Stop speaking").triggered.connect(on_stop)

        mute = QAction("Mute wake word", self.menu, checkable=True)
        mute.toggled.connect(on_mute)
        self.menu.addAction(mute)
        self.menu.addSeparator()

        visible = QAction("Show on screen", self.menu, checkable=True)
        visible.setChecked(True)
        visible.toggled.connect(lambda on: overlay.show_and_pin() if on else overlay.hide())
        self.menu.addAction(visible)
        self.menu.addAction("Reset position").triggered.connect(overlay.reset_position)
        self.menu.addSeparator()
        self.menu.addAction("Quit").triggered.connect(on_quit)

        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(
            lambda reason: on_talk() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        bridge.menu.connect(lambda: self.menu.popup(QCursor.pos()))
        self.icon.show()


def make_app():
    # Mint XFCE is X11 (this also keeps things working under XWayland)
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    extra = " ".join(config.CHROMIUM_FLAGS)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')} {extra}".strip()
    app = QApplication([config.NAME])
    app.setQuitOnLastWindowClosed(False)    # she keeps running even if you hide her
    app.setWindowIcon(make_icon())
    return app
