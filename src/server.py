"""A tiny local web server. It hands the page and the .vrm file to the embedded browser,
and keeps a websocket open so python and the page can talk to each other."""
import inspect
import json

from aiohttp import WSMsgType, web

from . import config

WEB_DIR = config.ROOT / "web"


def find_vrm():
    if config.VRM_FILE and (config.MODELS_DIR / config.VRM_FILE).exists():
        return config.VRM_FILE
    found = sorted(config.MODELS_DIR.glob("*.vrm"))
    return found[0].name if found else None


class Hub:
    """Keeps track of connected pages and routes their messages to handlers by type."""

    def __init__(self):
        self.clients = set()
        self.handlers = {}
        self.on_connect = None

    def on(self, msg_type, fn):
        self.handlers[msg_type] = fn

    async def broadcast(self, msg):
        data = json.dumps(msg)
        for ws in list(self.clients):
            try:
                await ws.send_str(data)
            except ConnectionError:
                self.clients.discard(ws)

    async def dispatch(self, msg):
        fn = self.handlers.get(msg.get("type", ""))
        if fn is None:
            return
        result = fn(msg)
        # handlers can be plain functions or coroutines. Some return junk (Qt's emit() returns a bool)
        if inspect.isawaitable(result):
            await result


@web.middleware
async def no_cache(request, handler):
    resp = await handler(request)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def build_app(hub):
    app = web.Application(middlewares=[no_cache])

    async def index(_):
        return web.FileResponse(WEB_DIR / "index.html")

    async def page_config(_):
        vrm = find_vrm()
        return web.json_response({
            "name": config.NAME, "vrm": f"/models/{vrm}" if vrm else None, "framing": config.FRAMING,
            "fps": config.FPS, "idleFps": config.IDLE_FPS, "pixelRatio": config.PIXEL_RATIO,
            "antialias": config.ANTIALIAS, "armAngle": config.ARM_ANGLE, "scale": config.MODEL_SCALE,
        })

    async def ws_handler(request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        hub.clients.add(ws)
        if hub.on_connect:
            await hub.on_connect()
        async for m in ws:
            if m.type == WSMsgType.TEXT:
                try:
                    await hub.dispatch(json.loads(m.data))
                except json.JSONDecodeError:
                    pass
        hub.clients.discard(ws)
        return ws

    def remote_command(name):
        # POST /api/<name> is how `main.py --talk` and friends reach the running instance
        async def handler(_):
            await hub.dispatch({"type": name})
            return web.json_response({"ok": True})
        return handler

    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/config", page_config)
    app.router.add_get("/api/ping", lambda _: web.json_response({"name": config.NAME}))
    for name in ("talk", "stop", "quit"):
        app.router.add_post(f"/api/{name}", remote_command(name))
    app.router.add_static("/models/", config.MODELS_DIR, show_index=False)
    app.router.add_static("/", WEB_DIR, show_index=False)
    return app


async def start(hub):
    runner = web.AppRunner(build_app(hub))
    await runner.setup()
    await web.TCPSite(runner, config.HOST, config.PORT).start()
    return runner
