#!/usr/bin/env python3
"""Akari, a desktop anime-girl voice assistant for Linux.

A descendant of VPET (https://github.com/Ashkii-yt/VPET): same idea of a little character
living on top of your desktop and chatting through AI APIs, rebuilt around a 3D VRM model,
a wake word and a voice. See README.md.
"""
import argparse
import asyncio
import sys
import threading
import time
import urllib.request

from src import config


def call_running_instance(path):
    url = f"http://{config.HOST}:{config.PORT}{path}"
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="POST", data=b""), timeout=2).read()
        return True
    except OSError:
        return False


def already_running():
    try:
        urllib.request.urlopen(f"http://{config.HOST}:{config.PORT}/api/ping", timeout=1).read()
        return True
    except OSError:
        return False


def remote(action):
    if not call_running_instance(f"/api/{action}"):
        print(f"{config.NAME} isn't running.")
        sys.exit(1)


def list_mics():
    import sounddevice as sd
    print(sd.query_devices())
    print("\nPut a name or number from that list into MIC_DEVICE in src/config.py.")


def listen_test():
    """Shows what the mic hears, handy for tuning WAKE_PHRASES and the noise gate."""
    config.DEBUG_STT = True
    from src.stt import Listener

    def got_phrase(text, wav, only_wake):
        print(f"  -> command: {text!r}  (only the wake word: {only_wake})")
        listener.resume(False)

    listener = Listener(on_wake=lambda: print("  -> wake phrase spotted"), on_false_wake=lambda: None,
                        on_utterance=got_phrase, on_timeout=lambda: None)
    listener.start()
    print("Say something, try your wake phrase. Ctrl+C to quit.\n")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        listener.stop()


def run(forced_desktop=None, ask_again=False):
    if already_running():
        print(f"{config.NAME} is already running. `python main.py --quit` stops her.")
        sys.exit(0)

    # the desktop has to be known before Qt starts, because it decides which Qt platform plugin loads
    from src import desktop as desktops
    desktop = desktops.choose(forced_desktop, ask_again)
    print(f"[desktop] running as: {desktop.label}")

    from PySide6.QtCore import QTimer
    from src.overlay import Bridge, Overlay, Tray, make_app

    # Qt owns the main thread, so the asyncio stuff (server, LLM, voice) runs on a second one
    loop = asyncio.new_event_loop()
    started = threading.Event()
    backend = {}

    def run_backend():
        asyncio.set_event_loop(loop)
        from src.assistant import Assistant
        from src.server import Hub, start

        async def boot():
            hub = Hub()
            backend["hub"] = hub
            backend["assistant"] = Assistant(loop, hub)
            await start(hub)
            backend["assistant"].start()

        try:
            loop.run_until_complete(boot())
        except OSError as e:
            backend["error"] = e
            started.set()
            return
        started.set()
        loop.run_forever()

    threading.Thread(target=run_backend, daemon=True, name="backend").start()
    started.wait()
    if "error" in backend:
        print(f"Couldn't start the local server on port {config.PORT}: {backend['error']}")
        sys.exit(1)

    hub, assistant = backend["hub"], backend["assistant"]
    app = make_app(desktop)
    bridge = Bridge()
    overlay = Overlay(bridge, desktop)

    # messages from the page that need the Qt thread go through the bridge
    hub.on("drag", lambda m: bridge.drag.emit(m.get("phase", "end"), int(m.get("dx", 0)), int(m.get("dy", 0))))
    hub.on("menu", lambda m: bridge.menu.emit(int(m.get("x", 0)), int(m.get("y", 0))))
    hub.on("quit", lambda _: bridge.quit_.emit())
    bridge.quit_.connect(app.quit)

    tray = Tray(
        overlay, bridge,
        on_talk=lambda: loop.call_soon_threadsafe(assistant.talk),
        on_stop=lambda: loop.call_soon_threadsafe(assistant.stop),
        on_mute=lambda muted: loop.call_soon_threadsafe(assistant.set_muted, muted),
        on_quit=app.quit,
    )

    timers = []
    if config.CURSOR_FOLLOW:
        last = [9.0, 9.0]

        def send_cursor():
            found = overlay.cursor_normalized()
            if not found:
                return
            x, y = found
            if abs(x - last[0]) + abs(y - last[1]) > 0.02:     # ignore tiny wiggles
                last[:] = [x, y]
                asyncio.run_coroutine_threadsafe(hub.broadcast({"type": "cursor", "x": x, "y": y}), loop)

        timer = QTimer()
        timer.timeout.connect(send_cursor)
        timer.start(overlay.cursor_poll_ms)
        timers.append(timer)    # without a reference Qt would garbage collect it

    def cleanup():
        overlay.save_position()
        assistant.shutdown()
        loop.call_soon_threadsafe(loop.stop)

    app.aboutToQuit.connect(cleanup)
    overlay.show_and_pin()
    print(f'{config.NAME} is up. Say "{config.WAKE_PHRASES[0]}", double-click her, or use the tray icon.')
    sys.exit(app.exec())


def main():
    ap = argparse.ArgumentParser(description=f"{config.NAME}, desktop voice assistant")
    ap.add_argument("--talk", action="store_true", help="make a running instance start listening (bind it to a hotkey)")
    ap.add_argument("--stop", action="store_true", help="make her stop talking")
    ap.add_argument("--quit", action="store_true", help="quit a running instance")
    ap.add_argument("--check", action="store_true", help="show which API keys work and what models they can use")
    ap.add_argument("--mics", action="store_true", help="list microphones")
    ap.add_argument("--desktop", choices=["xfce", "gnome", "hyprland", "windows", "x11"],
                    help="skip the question and run as this desktop")
    ap.add_argument("--choose-desktop", action="store_true", help="ask which desktop you use, even if she already knows")
    ap.add_argument("--hypr-config", action="store_true", help="print Hyprland window rules, keybind and autostart lines")
    ap.add_argument("--listen-test", action="store_true", help="print what the mic hears")
    args = ap.parse_args()

    if args.talk:
        remote("talk")
    elif args.stop:
        remote("stop")
    elif args.quit:
        remote("quit")
    elif args.check:
        from src.llm import check
        asyncio.run(check())
    elif args.mics:
        list_mics()
    elif args.hypr_config:
        from src.hyprland import config_snippets
        print(config_snippets())
    elif args.listen_test:
        listen_test()
    else:
        run(args.desktop, args.choose_desktop)


if __name__ == "__main__":
    main()
