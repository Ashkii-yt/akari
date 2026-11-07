"""Talking to Hyprland over its IPC socket (no Qt in here, so it's easy to test).

On Wayland a program can't choose where its window goes, stay on top, or see the mouse outside
its own window. Hyprland lets us do all of that through a unix socket instead, which is what
`hyprctl` uses too. We find her window by its class, then float, pin, place and drag it from here.

Hyprland has changed its config format twice (windowrulev2 -> windowrule -> Lua), and the
dispatcher syntax changed with it. So every command is tried the old way first and the Lua way
second, and whichever works gets remembered. `config_snippets()` prints permanent window rules
for all three generations if you'd rather set it up in your config.
"""
import glob
import json
import os
import socket
import time

from . import config, state

IPC_TIMEOUT_S = 0.6
DRAG_MIN_INTERVAL_S = 0.012     # every drag step is a round trip, don't flood the compositor
GEOMETRY_CACHE_S = 1.0
SETUP_PROPS = ["no_anim", "no_shadow", "no_blur", "no_dim"]


def find_socket():
    """Path of Hyprland's command socket, or None if we're not inside a Hyprland session."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    candidates = []
    if signature:
        candidates += [f"{runtime}/hypr/{signature}/.socket.sock", f"/tmp/hypr/{signature}/.socket.sock"]
    # started from a terminal that doesn't have the variable? take the newest instance
    found = glob.glob(f"{runtime}/hypr/*/.socket.sock") + glob.glob("/tmp/hypr/*/.socket.sock")
    candidates += sorted(found, key=lambda p: os.path.getmtime(p), reverse=True)
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


class IPC:
    def __init__(self, path=None):
        self.path = path or find_socket()

    @property
    def ok(self):
        return self.path is not None

    def request(self, text):
        """One request, one connection. Hyprland wants the socket closed right after the reply."""
        if not self.path:
            return ""
        chunks = []
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(IPC_TIMEOUT_S)
                s.connect(self.path)
                s.sendall(text.encode())
                while True:
                    data = s.recv(65536)
                    if not data:
                        break
                    chunks.append(data)
        except (OSError, socket.timeout):
            pass        # return whatever arrived, an empty string means it failed
        return b"".join(chunks).decode(errors="replace")

    def json(self, command):
        try:
            return json.loads(self.request(f"j/{command}"))
        except ValueError:
            return None


class Window:
    """Controls her window through Hyprland. Create it once, call setup() until it returns True."""

    def __init__(self, ipc=None):
        self.ipc = ipc or IPC()
        self.address = None
        self.mode = None            # "legacy" or "lua", whichever the running Hyprland understands
        self.size = (config.WINDOW_W, config.WINDOW_H)
        self._drag = None
        self._last_drag = 0.0
        self._geo = None
        self._geo_time = 0.0

    # finding her

    def find(self):
        for client in self.ipc.json("clients") or []:
            if client.get("class") in (config.WINDOW_CLASS, config.WINDOW_CLASS.capitalize()) and client.get("mapped", True):
                self.address = client["address"]
                self.size = tuple(client.get("size") or self.size)
                return client
        return None

    # sending commands

    def _dispatch(self, legacy, *lua_variants):
        """Runs a dispatcher the old way, then the Lua way. True once one of them says ok."""
        attempts = []
        if self.mode in (None, "legacy"):
            attempts.append(("legacy", f"/dispatch {legacy}"))
        if self.mode in (None, "lua"):
            attempts += [("lua", f"/dispatch {v}") for v in lua_variants]
        for mode, text in attempts:
            if self.ipc.request(text).strip() == "ok":
                self.mode = mode
                return True
        return False

    def _target(self):
        return f"address:{self.address}"

    # one-time setup

    def setup(self, saved=None):
        """Float, pin and place her. Returns False while the window hasn't appeared yet."""
        client = self.find()
        if not client:
            return False
        a, t = self.address, self._target()

        if not client.get("floating"):
            self._dispatch(f"setfloating {t}", f'hl.dsp.window.float({{window="{t}", action="enable"}})')
        if not client.get("pinned"):      # pin toggles, so only do it when she isn't pinned yet
            self._dispatch(f"pin {t}", f'hl.dsp.window.pin({{window="{t}"}})')

        for prop in SETUP_PROPS:
            old = prop.replace("_", "")
            self._dispatch(f"setprop {t} {old} 1", f'hl.dsp.window.set_prop({{window="{t}", prop="{prop}", value=1}})')
        for prop in ("border_size", "rounding"):
            old = prop.replace("_", "")
            self._dispatch(f"setprop {t} {old} 0", f'hl.dsp.window.set_prop({{window="{t}", prop="{prop}", value=0}})')

        x, y = self.start_position(saved)
        self.move_to(x, y)
        self._dispatch(f"alterzorder top,{t}", f'hl.dsp.window.alter_zorder({{window="{t}", mode="top"}})')
        return True

    # where she goes

    def monitors(self):
        return self.ipc.json("monitors") or []

    @staticmethod
    def _logical(m):
        scale = m.get("scale") or 1.0
        return m["x"], m["y"], int(m["width"] / scale), int(m["height"] / scale)

    def start_position(self, saved=None):
        monitors = self.monitors()
        w, h = self.size
        if saved and "x" in saved and "y" in saved:
            for m in monitors:
                mx, my, mw, mh = self._logical(m)
                if mx <= saved["x"] + 50 < mx + mw and my <= saved["y"] + 50 < my + mh:
                    return int(saved["x"]), int(saved["y"])
        if not monitors:
            return config.WINDOW_MARGIN, config.WINDOW_MARGIN
        m = next((m for m in monitors if m.get("focused")), monitors[0])
        mx, my, mw, mh = self._logical(m)
        left, top, right, bottom = m.get("reserved") or (0, 0, 0, 0)      # bars live in here
        return (mx + mw - right - w - config.WINDOW_MARGIN,
                my + mh - bottom - h - config.WINDOW_MARGIN)

    def move_to(self, x, y):
        t = self._target()
        return self._dispatch(f"movewindowpixel exact {int(x)} {int(y)},{t}",
                              f'hl.dsp.window.move({{window="{t}", x={int(x)}, y={int(y)}}})')

    def position(self):
        client = self.find()
        return tuple(client["at"]) if client else None

    # dragging: the page only says "start / move / end", the real mouse position comes from Hyprland

    def drag(self, phase):
        if phase == "start":
            cursor, at = self.ipc.json("cursorpos"), self.position()
            if cursor and at:
                self._drag = (cursor["x"] - at[0], cursor["y"] - at[1])
        elif phase == "move" and self._drag:
            now = time.monotonic()
            if now - self._last_drag < DRAG_MIN_INTERVAL_S:
                return
            self._last_drag = now
            cursor = self.ipc.json("cursorpos")
            if cursor:
                self.move_to(cursor["x"] - self._drag[0], cursor["y"] - self._drag[1])
        elif phase == "end":
            self._drag = None
            self.save()

    # the eyes: where is the mouse relative to her, from -1 to 1

    def cursor_normalized(self):
        cursor = self.ipc.json("cursorpos")
        if not cursor:
            return None
        now = time.monotonic()
        if not self._geo or now - self._geo_time > GEOMETRY_CACHE_S:
            at, monitors = self.position(), self.monitors()
            if not at or not monitors:
                return None
            self._geo, self._geo_time = (at, monitors), now
        at, monitors = self._geo
        mx, my, mw, mh = next(
            (self._logical(m) for m in monitors
             if self._logical(m)[0] <= at[0] < self._logical(m)[0] + self._logical(m)[2]
             and self._logical(m)[1] <= at[1] < self._logical(m)[1] + self._logical(m)[3]),
            self._logical(monitors[0]))
        cx, cy = at[0] + self.size[0] / 2, at[1] + self.size[1] / 2
        x = max(-1.0, min(1.0, (cursor["x"] - cx) / (mw / 2)))
        y = max(-1.0, min(1.0, (cursor["y"] - cy) / (mh / 2)))
        return x, y

    # saving

    def save(self):
        at = self.position()
        if at:
            state.update(x=int(at[0]), y=int(at[1]))

    def reset(self):
        state.forget("x", "y")
        self.move_to(*self.start_position())


def config_snippets():
    """Permanent setup for hyprland.conf / hyprland.lua, for whichever version you run."""
    cls = config.WINDOW_CLASS
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    run = f"{here}/venv/bin/python {here}/main.py"
    return f"""\
Akari already floats and pins herself over Hyprland's IPC, so none of this is required.
Window rules just make it permanent and stop the very first frame from flashing in a tiled spot.
Use the block that matches your Hyprland version (run `hyprctl version`).

--- Hyprland 0.53 and newer, hyprland.conf (hyprlang) ---
windowrule = float on, match:class ^({cls})$
windowrule = pin on, match:class ^({cls})$
windowrule = no_initial_focus on, match:class ^({cls})$
windowrule = no_anim on, match:class ^({cls})$
windowrule = no_shadow on, match:class ^({cls})$
windowrule = no_blur on, match:class ^({cls})$
windowrule = no_dim on, match:class ^({cls})$
windowrule = border_size 0, match:class ^({cls})$
windowrule = rounding 0, match:class ^({cls})$
exec-once = {run}
bind = SUPER, A, exec, {run} --talk

--- Hyprland 0.52 and older ---
windowrulev2 = float, class:^({cls})$
windowrulev2 = pin, class:^({cls})$
windowrulev2 = noinitialfocus, class:^({cls})$
windowrulev2 = noanim, class:^({cls})$
windowrulev2 = noshadow, class:^({cls})$
windowrulev2 = noblur, class:^({cls})$
windowrulev2 = nodim, class:^({cls})$
windowrulev2 = bordersize 0, class:^({cls})$
windowrulev2 = rounding 0, class:^({cls})$
exec-once = {run}
bind = SUPER, A, exec, {run} --talk

--- Lua config ---
hl.window_rule({{
    match = {{ class = "^({cls})$" }},
    float = true, pin = true, no_initial_focus = true,
    no_anim = true, no_shadow = true, no_blur = true, no_dim = true,
    border_size = 0, rounding = 0,
}})
hl.on("hyprland.start", function() hl.exec_cmd("{run}") end)
hl.bind("SUPER + A", hl.dsp.exec_cmd("{run} --talk"))

Status bars: if Waybar's taskbar module lists her, add "{cls}" to its "ignore-list".
"""
