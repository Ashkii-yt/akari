"""The brain: connects the mic, the LLM, the voice and the on-screen character."""
import asyncio
import random

from . import config
from .llm import LLM, AllProvidersFailed
from .stt import Listener, transcribe_groq
from .textutil import SentenceStream, strip_leading_wake
from .tts import Speaker

STOP_WORDS = {"stop", "never mind", "nevermind", "be quiet", "cancel", "shut up", "quiet"}


class Assistant:
    def __init__(self, loop, hub):
        self.loop = loop
        self.hub = hub
        self.llm = LLM()
        self.speaker = Speaker(on_sentence=self._on_sentence)
        # the listener lives on its own thread, so everything it reports gets hopped onto the event loop
        self.listener = Listener(
            on_wake=lambda: self._on_loop(self._set_state, "listening"),
            on_false_wake=lambda: self._on_loop(self._set_state, "idle"),
            on_utterance=lambda text, wav, only_wake: self._on_loop(self._start_turn, text, wav, only_wake),
            on_timeout=lambda: self._on_loop(self._set_state, "idle"),
        )
        self.state = "idle"
        self._task = None
        self._first_sentence = True

        hub.on("talk", lambda _: self.talk())
        hub.on("stop", lambda _: self.stop())
        hub.on("gfx", self._report_gfx)
        hub.on_connect = self._hello

    def _on_loop(self, fn, *args):
        self.loop.call_soon_threadsafe(fn, *args)

    def start(self):
        self.listener.start()

    def shutdown(self):
        self.listener.stop()
        self.speaker.stop()

    def _send(self, msg):
        asyncio.ensure_future(self.hub.broadcast(msg), loop=self.loop)

    def _set_state(self, state):
        self.state = state
        self._send({"type": "state", "state": state})

    async def _hello(self):
        await self.hub.broadcast({"type": "state", "state": self.state})
        if not self.listener.ok:
            await self.hub.broadcast({"type": "notice", "text": "I can't hear you (no mic). Double-click me to talk instead."})

    def _report_gfx(self, msg):
        ms = msg.get("ms")
        cost = f", about {ms:.1f} ms of work per frame" if ms is not None else ""
        print(f"[gfx] renderer: {msg.get('renderer')}{cost}", flush=True)
        if msg.get("renderer") == "none":
            print("[gfx] the embedded browser couldn't get WebGL at all. Try removing '--ignore-gpu-blocklist' "
                  "(or adding '--disable-gpu') in CHROMIUM_FLAGS in src/config.py, and check your GPU driver.", flush=True)
        elif msg.get("software"):
            print("[gfx] that's a SOFTWARE renderer, so the CPU is doing all the 3D work and that's what makes "
                  "things laggy. Check your GPU driver (Driver Manager on Mint), or try removing "
                  "'--ignore-gpu-blocklist' from CHROMIUM_FLAGS in src/config.py.", flush=True)

    async def _on_sentence(self, text):
        self._set_state("speaking")
        self._send({"type": "subtitle", "who": "her", "text": text, "append": not self._first_sentence})
        self._first_sentence = False

    def talk(self):
        """Push to talk: double-click, tray menu, or `main.py --talk`."""
        if self._task and not self._task.done():
            self.stop()
        self._set_state("listening")
        self.listener.force_listen()

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self.speaker.stop()
        self._set_state("idle")
        self.listener.resume(False)

    def set_muted(self, muted):
        self.listener.wake_enabled = not muted

    def _start_turn(self, text, wav, only_wake):
        self._task = asyncio.ensure_future(self._turn(text, wav, only_wake), loop=self.loop)

    async def _turn(self, text, wav, only_wake):
        keep_listening = False
        try:
            self._first_sentence = True
            if only_wake:
                # she was just called by name, so answer and then wait for the actual question
                self._set_state("speaking")
                await self.speaker.speak(self._say(random.choice(config.ACK_PHRASES or ["Yes?"])))
                keep_listening = True
                self._set_state("listening")
                return

            self._set_state("thinking")
            if config.STT_ENGINE == "groq" and wav:
                better = await transcribe_groq(wav)
                if better:
                    text = better
            text = strip_leading_wake(text)
            if not text:
                return
            self._send({"type": "subtitle", "who": "you", "text": text})
            if text.lower().strip(" .!?") in STOP_WORDS:
                return
            await self.speaker.speak(self._reply(text))
        except asyncio.CancelledError:
            pass
        except AllProvidersFailed as e:
            print(f"[assistant] no AI provider worked: {e}")
            self._first_sentence = True
            await self._on_sentence("Ehh? I can't reach any AI service. Check your API keys and internet!")
            self._send({"type": "emotion", "name": "sad"})
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[assistant] something broke: {type(e).__name__}: {e}")
        finally:
            if not keep_listening:
                self._set_state("idle")
            self.listener.resume(keep_listening)

    async def _say(self, text):
        yield text

    async def _reply(self, text):
        """Ask the LLM and yield it a sentence at a time. The emotion tag goes to the character first."""
        stream = SentenceStream()
        told_emotion = False

        def emotion_msg():
            return {"type": "emotion", "name": stream.emotion or "neutral"}

        async for chunk in self.llm.stream(text):
            sentences = stream.feed(chunk)
            if stream.emotion and not told_emotion:
                told_emotion = True
                self._send(emotion_msg())
            for s in sentences:
                if not told_emotion:
                    told_emotion = True
                    self._send(emotion_msg())
                yield s
        for s in stream.flush():
            if not told_emotion:
                told_emotion = True
                self._send(emotion_msg())
            yield s
