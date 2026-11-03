"""Her voice. Each sentence is synthesized with edge-tts while the previous one is still playing,
so the reply starts quickly and doesn't have gaps."""
import asyncio
import os
import shutil
import tempfile

from . import config


def find_player():
    if shutil.which("mpv"):
        return ["mpv", "--no-video", "--really-quiet", "--no-terminal", f"--volume={config.VOLUME}"]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", str(config.VOLUME)]
    if shutil.which("cvlc"):
        return ["cvlc", "--play-and-exit", "--quiet"]
    return None


def _remove(path):
    try:
        os.unlink(path)
    except OSError:
        pass


class Speaker:
    def __init__(self, on_sentence=None):
        self.on_sentence = on_sentence      # called with each sentence right before it's spoken
        self.player = find_player()
        self.proc = None
        self.tmpdir = tempfile.mkdtemp(prefix="akari-tts-")
        self._warned = False
        if not self.player:
            print("[tts] no audio player found, install mpv (sudo apt install mpv). Subtitles only for now.")

    async def speak(self, sentences):
        """`sentences` is an async iterator of strings."""
        ready = asyncio.Queue(maxsize=2)

        async def producer():
            n = 0
            try:
                async for text in sentences:
                    n += 1
                    await ready.put((text, await self._synthesize(text, n)))
            finally:
                await ready.put(None)

        task = asyncio.create_task(producer())
        try:
            while (item := await ready.get()) is not None:
                text, path = item
                if self.on_sentence:
                    result = self.on_sentence(text)
                    if asyncio.iscoroutine(result):
                        await result
                if path and self.player:
                    await self._play(path)
                else:
                    # no audio, so just hold the subtitle for roughly reading time
                    await asyncio.sleep(min(6.0, 0.35 + len(text) * 0.055))
                if path:
                    _remove(path)
            await task      # re-raise anything the producer hit
        finally:
            task.cancel()
            while not ready.empty():
                item = ready.get_nowait()
                if item and item[1]:
                    _remove(item[1])

    async def _synthesize(self, text, n):
        if not self.player:
            return None
        import edge_tts
        path = os.path.join(self.tmpdir, f"{n}-{abs(hash(text)) % 10**8}.mp3")
        for attempt in (1, 2):
            try:
                comm = edge_tts.Communicate(text, config.VOICE, rate=config.VOICE_RATE, pitch=config.VOICE_PITCH)
                await comm.save(path)
                return path
            except Exception as e:
                if attempt == 2 and not self._warned:
                    self._warned = True
                    print(f"[tts] edge-tts failed ({type(e).__name__}: {e}), showing text only. "
                          "Updating it sometimes helps: pip install -U edge-tts")
        return None

    async def _play(self, path):
        self.proc = await asyncio.create_subprocess_exec(
            *self.player, path, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await self.proc.wait()
        finally:
            self.proc = None

    def stop(self):
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
