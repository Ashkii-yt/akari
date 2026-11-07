"""The window she lives in: frameless, see-through, always on top, and not in the taskbar.
It's just a Chromium view showing web/index.html, where three.js draws the model."""
import os
import shutil
import subprocess

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import config, state
from .hyprland import Window as HyprWindow


class Bridge(QObject):
    """The asyncio thread can't touch Qt widgets directly, so it emits these signals instead."""
    drag = Signal(str, int, int)
    menu = Signal(int, int)      # where in the window she was right-clicked
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
    def __init__(self, bridge, desktop):
        super().__init__()
        self.desktop = desktop
        # on Hyprland the compositor moves her around, so Qt only has to make a plain frameless window
        self.hypr = HyprWindow() if desktop.managed else None
        if self.hypr and not self.hypr.ipc.ok:
            print("[hyprland] couldn't find Hyprland's socket, she'll just sit wherever it puts her.")
            self.hypr = None
        self.setWindowTitle(config.NAME)
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        if not desktop.managed:
            # Tool windows don't get a taskbar button, and she shouldn't steal focus from what you're typing in
            flags |= Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet("background: transparent;")
        self.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.resize(config.WINDOW_W, config.WINDOW_H)

        self._drag_start = QPoint()
        self._setup_tries = 0
        if not self.hypr:
            self._place()
        bridge.drag.connect(self._on_drag)
        self.load(QUrl(f"http://{config.HOST}:{config.PORT}/"))

    def _place(self):
        pos = None
        saved = state.load()
        if "x" in saved and "y" in saved:
            pos = QPoint(int(saved["x"]), int(saved["y"]))
        # if the saved spot is off-screen (monitor unplugged, say) go back to the corner
        on_screen = pos is not None and any(
            s.availableGeometry().contains(pos + QPoint(50, 50)) for s in QGuiApplication.screens())
        if not on_screen:
            area = QGuiApplication.primaryScreen().availableGeometry()
            pos = QPoint(area.right() - config.WINDOW_W - config.WINDOW_MARGIN,
                         area.bottom() - config.WINDOW_H - config.WINDOW_MARGIN)
        self.move(pos)

    def reset_position(self):
        if self.hypr:
            self.hypr.reset()
            return
        state.forget("x", "y")
        self._place()

    def save_position(self):
        if self.hypr:
            self.hypr.save()
        else:
            state.update(x=self.x(), y=self.y())

    def _on_drag(self, phase, dx, dy):
        if self.hypr:
            self.hypr.drag(phase)       # the real mouse position comes from Hyprland, dx/dy aren't reliable here
        elif phase == "start":
            self._drag_start = self.pos()
        elif phase == "move":
            self.move(self._drag_start + QPoint(dx, dy))
        else:
            self.save_position()

    def cursor_normalized(self):
        """Mouse position relative to her, -1..1 on both axes. None if we can't tell right now."""
        if self.hypr:
            return self.hypr.cursor_normalized()
        screen = QGuiApplication.primaryScreen().geometry()
        pos, center = QCursor.pos(), self.geometry().center()
        return (max(-1.0, min(1.0, (pos.x() - center.x()) / (screen.width() / 2))),
                max(-1.0, min(1.0, (pos.y() - center.y()) / (screen.height() / 2))))

    @property
    def cursor_poll_ms(self):
        return config.CURSOR_POLL_MS_HYPRLAND if self.hypr else config.CURSOR_POLL_MS

    def show_and_pin(self):
        self.show()
        if self.hypr:
            # her window takes a moment to show up in Hyprland's list, so keep trying for a few seconds
            self._setup_tries = 0
            QTimer.singleShot(250, self._setup_hyprland)
        elif self.desktop.use_wmctrl and config.SKIP_TASKBAR_WITH_WMCTRL and shutil.which("wmctrl") and os.environ.get("DISPLAY"):
            QTimer.singleShot(700, self._tell_window_manager)

    def _setup_hyprland(self):
        saved = state.load() if self._setup_tries == 0 else None
        if self.hypr.setup(saved or state.load()):
            return
        self._setup_tries += 1
        if self._setup_tries < 24:
            QTimer.singleShot(250, self._setup_hyprland)
        else:
            print("[hyprland] I never found my window in `hyprctl clients`. Try `python main.py --hypr-config`.")

    def _tell_window_manager(self):
        # XFCE's panel sometimes shows tool windows anyway, so ask it nicely not to
        subprocess.run(["wmctrl", "-i", "-r", hex(int(self.winId())), "-b",
                        "add,skip_taskbar,skip_pager,above,sticky"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Tray:
    def __init__(self, overlay, bridge, on_talk, on_stop, on_mute, on_quit):
        self.icon = QSystemTrayIcon(make_icon())
        self.icon.setToolTip(config.NAME)
        self.menu = QMenu(overlay if overlay.desktop.managed else None)
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
        if overlay.desktop.managed:
            # on Wayland QCursor.pos() is useless, but a popup placed relative to our own window works
            bridge.menu.connect(lambda x, y: self.menu.popup(overlay.mapToGlobal(QPoint(x, y))))
        else:
            bridge.menu.connect(lambda x, y: self.menu.popup(QCursor.pos()))
        self.icon.show()


def make_app(desktop):
    from . import desktop as desktops
    desktops.prepare_environment(desktop)
    app = QApplication([config.NAME])
    app.setApplicationName(config.WINDOW_CLASS)
    QGuiApplication.setDesktopFileName(config.WINDOW_CLASS)     # becomes the Wayland app_id Hyprland rules match on
    app.setQuitOnLastWindowClosed(False)    # she keeps running even if you hide her
    app.setWindowIcon(make_icon())
    return app
