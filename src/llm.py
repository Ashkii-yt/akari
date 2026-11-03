"""Chat with whichever free provider is working, streaming the reply as it arrives."""
import asyncio
import json
import os
import time

import aiohttp

from . import config


class ProviderError(Exception):
    def __init__(self, status, detail):
        super().__init__(f"{status}: {detail}")
        self.status = status


class AllProvidersFailed(Exception):
    pass


def _api_key(p):
    return os.getenv(p["key_env"]) if p["key_env"] else ""


def usable(p):
    """Has a model name and, if it needs one, a key."""
    return bool(p["model"]) and (p["key_env"] is None or bool(_api_key(p)))


class LLM:
    def __init__(self):
        self.history = []
        self.last_provider = ""
        self._skip_until = {}       # provider name -> time it's allowed again

    def available(self):
        now = time.time()
        return [p for p in config.PROVIDERS if usable(p) and self._skip_until.get(p["name"], 0) <= now]

    def _bench(self, p, err):
        """A provider that just failed sits out for a while."""
        status = getattr(err, "status", 0)
        secs = 900 if status in (401, 402, 403, 404) else 60 if status == 429 else 20
        self._skip_until[p["name"]] = time.time() + secs
        print(f"[llm] {p['name']} failed ({err}), skipping it for {secs}s", flush=True)

    async def stream(self, user_text):
        messages = [{"role": "system", "content": config.PERSONA}, *self.history,
                    {"role": "user", "content": user_text}]
        errors = []
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=8, sock_read=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for p in self.available():
                got = []
                try:
                    async for piece in self._stream_from(session, p, messages):
                        got.append(piece)
                        yield piece
                    if not got:
                        raise ProviderError(0, "empty reply")
                    self.last_provider = p["name"]
                    self._remember(user_text, "".join(got))
                    return
                except (ProviderError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                    self._bench(p, e)
                    errors.append(f"{p['name']}: {e}")
                    if got:     # died halfway: she already started talking, so keep what we have
                        self._remember(user_text, "".join(got))
                        return
        raise AllProvidersFailed("; ".join(errors) or "no provider has an API key (check .env)")

    def _remember(self, user, reply):
        self.history += [{"role": "user", "content": user}, {"role": "assistant", "content": reply}]
        self.history = self.history[-config.HISTORY_TURNS * 2:]

    async def _stream_from(self, session, p, messages):
        payload = {"model": p["model"], "messages": messages, "stream": True,
                   "max_tokens": config.MAX_TOKENS, "temperature": config.TEMPERATURE, **p["extra"]}
        headers = {"Content-Type": "application/json"}
        if _api_key(p):
            headers["Authorization"] = f"Bearer {_api_key(p)}"

        async with session.post(f"{p['base_url']}/chat/completions", json=payload, headers=headers) as r:
            if r.status != 200:
                raise ProviderError(r.status, (await r.text())[:240].replace("\n", " "))
            async for raw in r.content:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if obj.get("error"):
                    raise ProviderError(0, str(obj["error"])[:200])
                choices = obj.get("choices") or []
                piece = (choices[0].get("delta") or {}).get("content") if choices else None
                if piece:
                    yield piece


async def check():
    """`python main.py --check`: which keys are set, and which models can they see."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        done = set()
        for p in config.PROVIDERS:
            ok = usable(p)
            key_note = "yes" if ok else f"missing ({p['key_env']})"
            print(f"\n{p['name']}: model={p['model'] or '-'}  key={key_note}")
            if not ok or p["base_url"] in done:
                continue
            done.add(p["base_url"])
            headers = {"Authorization": f"Bearer {_api_key(p)}"} if _api_key(p) else {}
            try:
                async with session.get(f"{p['base_url']}/models", headers=headers) as r:
                    if r.status != 200:
                        print(f"  /models gave {r.status}: {(await r.text())[:160]}")
                        continue
                    ids = sorted(m.get("id", "") for m in (await r.json()).get("data", []))
                    print("  can see:", ", ".join(ids[:40]) + (" ..." if len(ids) > 40 else ""))
            except Exception as e:
                print("  couldn't reach it:", e)
    print("\nIf a model name above is gone, edit PROVIDERS in src/config.py.")
