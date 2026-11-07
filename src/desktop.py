"""Which desktop are we on? Window handling differs a lot between them:

  XFCE / other X11   Qt can place and drag her itself, always-on-top is a window hint.
  GNOME              same, but through XWayland, because Mutter ignores always-on-top from Wayland apps.
  Hyprland           Wayland: apps can't place windows or know where the cursor is, so we ask
                     Hyprland over its IPC socket (see hyprland.py).
  Windows            Qt does everything, no tray/wmctrl tricks needed.
"""
import os
import sys
from dataclasses import dataclass

from . import config, state


@dataclass(frozen=True)
class Desktop:
    key: str
    label: str
    qt_platform: str | None     # value for QT_QPA_PLATFORM, None = let Qt decide
    managed: bool = False       # True when the compositor places/moves her instead of Qt
    use_wmctrl: bool = False    # extra "skip taskbar" nudge for X11 panels


DESKTOPS = {d.key: d for d in [
    Desktop("xfce", "XFCE", "xcb", use_wmctrl=True),
    Desktop("gnome", "GNOME", config.GNOME_QT_PLATFORM),
    Desktop("hyprland", "Hyprland", config.HYPRLAND_QT_PLATFORM, managed=True),
    Desktop("windows", "Windows", None),
    Desktop("x11", "Other Linux desktop (KDE, Cinnamon, i3...)", "xcb", use_wmctrl=True),
]}


def detect():
    """Best guess from the environment. Cheap and good enough to use as the default answer."""
    if sys.platform.startswith("win"):
        return "windows"
    names = " ".join(os.environ.get(k, "") for k in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION")).lower()
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or "hyprland" in names:
        return "hyprland"
    if "gnome" in names or "ubuntu" in names:
        return "gnome"
    if "xfce" in names:
        return "xfce"
    return "x11"


def interactive():
    return sys.stdin is not None and sys.stdin.isatty() and sys.stdout.isatty()


def ask(guess, previous=None):
    keys = list(DESKTOPS)
    print("\nWhich desktop are you using?")
    for i, key in enumerate(keys, 1):
        tags = []
        if key == guess:
            tags.append("detected")
        if key == previous and previous != guess:
            tags.append("last time")
        print(f"  {i}) {DESKTOPS[key].label}" + (f"   <- {', '.join(tags)}" if tags else ""))
    default = keys.index(guess) + 1
    while True:
        answer = input(f"Pick 1-{len(keys)} [Enter = {default}]: ").strip().lower()
        if not answer:
            return guess
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        if answer in DESKTOPS:
            return answer
        print("  (type a number from the list)")


def choose(forced=None, ask_again=False):
    """Works out which desktop to run as.

    She asks once. The answer is remembered together with what was detected at the time, so if you
    log into a different session later (say XFCE one day, Hyprland the next) she asks again
    instead of silently using the wrong one. With no terminal (autostart) she just uses the detection.
    """
    if forced:
        return DESKTOPS[forced]

    guess = detect()
    saved = state.load()
    remembered = saved.get("desktop")
    same_session = saved.get("detected") == guess

    if remembered in DESKTOPS and same_session and not ask_again:
        return DESKTOPS[remembered]
    if not interactive():
        return DESKTOPS[guess]

    picked = ask(guess, remembered if remembered in DESKTOPS else None)
    state.update(desktop=picked, detected=guess)
    return DESKTOPS[picked]


def prepare_environment(desktop):
    """Set Qt/Chromium environment variables. Has to happen before the QApplication exists."""
    platform = desktop.qt_platform
    if platform == "wayland" and not os.environ.get("WAYLAND_DISPLAY"):
        print("[desktop] you picked a Wayland setup but WAYLAND_DISPLAY isn't set, falling back to X11.")
        platform = "xcb"
    if platform:
        os.environ.setdefault("QT_QPA_PLATFORM", platform)
    if desktop.managed:
        os.environ.setdefault("QT_WAYLAND_DISABLE_WINDOWDECORATION", "1")
    extra = " ".join(config.CHROMIUM_FLAGS)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')} {extra}".strip()
