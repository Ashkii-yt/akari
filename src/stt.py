"""Listening: an offline wake-word recognizer (Vosk) behind a noise gate, plus the command capture.
The command audio can optionally be re-transcribed by Groq Whisper, which is a lot more accurate."""
import array
import collections
import io
import json
import math
import os
import queue
import threading
import time
import urllib.request
import wave
import zipfile

import aiohttp

from . import config
from .textutil import match_wake

IDLE, AWAKE, BUSY = "idle", "awake", "busy"
BLOCK_S = 0.1


def ensure_vosk_model():
    path = config.VOSK_DIR / config.VOSK_MODEL_NAME
    if path.exists():
        return str(path)
    config.VOSK_DIR.mkdir(parents=True, exist_ok=True)
    zpath = config.VOSK_DIR / "model.zip"
    print(f"[stt] downloading the speech model (~40 MB) from {config.VOSK_MODEL_URL}", flush=True)
    urllib.request.urlretrieve(config.VOSK_MODEL_URL, zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(config.VOSK_DIR)
    zpath.unlink(missing_ok=True)
    return str(path)


def to_wav(pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(config.SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


async def transcribe_groq(wav):
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    form = aiohttp.FormData()
    form.add_field("file", wav, filename="speech.wav", content_type="audio/wav")
    form.add_field("model", config.GROQ_STT_MODEL)
    form.add_field("language", "en")
    form.add_field("temperature", "0")
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(url, data=form, headers={"Authorization": f"Bearer {key}"}) as r:
                if r.status != 200:
                    print(f"[stt] groq whisper said {r.status}: {(await r.text())[:160]}")
                    return None
                return ((await r.json()).get("text") or "").strip()
    except Exception as e:
        print(f"[stt] groq whisper failed: {e}")
        return None


def loudness(block):
    """Rough RMS of a block of int16 samples. Only looks at every 4th sample, that's plenty."""
    samples = array.array("h")
    samples.frombytes(block)
    picked = samples[::4]
    if not picked:
        return 0.0
    return math.sqrt(sum(s * s for s in picked) / len(picked))


class NoiseGate:
    """Decides which mic blocks are worth sending to the recognizer.

    Quiet blocks are held back (we keep the last few so the first word isn't clipped when
    someone starts talking). Once the gate opens it stays open for a bit after the sound
    stops, so the recognizer sees the silence it needs to end a sentence.
    """

    def __init__(self):
        self.floor = 150.0          # running guess of the background noise level
        self.is_open = False
        self.just_closed = False
        self.quiet_for = 0.0
        self.lookback = collections.deque(maxlen=4)

    def reset(self):
        self.is_open = False
        self.just_closed = False
        self.quiet_for = 0.0
        self.lookback.clear()

    def push(self, block):
        """Returns the blocks the recognizer should process now (maybe none)."""
        self.just_closed = False
        level = loudness(block)
        if level > max(config.MIC_MIN_LEVEL, self.floor * config.MIC_GATE_FACTOR):
            self.quiet_for = 0.0
            if self.is_open:
                return [block]
            self.is_open = True
            blocks = [*self.lookback, block]
            self.lookback.clear()
            return blocks

        self.floor = self.floor * 0.97 + level * 0.03
        if not self.is_open:
            self.lookback.append(block)
            return []
        self.quiet_for += BLOCK_S
        if self.quiet_for > config.MIC_HANGOVER_S:
            self.is_open = False
            self.just_closed = True
        return [block]


class Listener(threading.Thread):
    """Owns the microphone and the recognizer on its own thread.

    Callbacks (they run on this thread, so hop to your own thread before touching anything):
      on_wake()                          heard something that looks like the wake phrase
      on_false_wake()                    ...never mind, it wasn't
      on_utterance(text, wav, only_wake) a finished command. only_wake=True if she was just called by name
      on_timeout()                       she woke up but you didn't say anything
    """

    def __init__(self, on_wake, on_false_wake, on_utterance, on_timeout):
        super().__init__(daemon=True, name="listener")
        self.on_wake = on_wake
        self.on_false_wake = on_false_wake
        self.on_utterance = on_utterance
        self.on_timeout = on_timeout
        self.wake_enabled = True
        self.ok = False

        self.state = IDLE
        self.awake_until = 0.0
        self.gate = NoiseGate()
        self.rec = None
        self.recent = collections.deque(maxlen=5)   # last half second, used to pad the start of a capture
        self.utt = bytearray()
        self.capturing = False
        self.woke_flagged = False

        self._commands = queue.Queue()
        self._audio = queue.Queue()
        self._halt = threading.Event()

    # these are called from other threads
    def resume(self, listen=False):
        self._commands.put(listen)

    def force_listen(self):
        self._commands.put(True)

    def stop(self):
        self._halt.set()

    def run(self):
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except (ImportError, OSError) as e:
            print(f"[stt] audio libraries missing ({e}). On Mint: sudo apt install libportaudio2")
            return
        SetLogLevel(-1)
        try:
            model = Model(ensure_vosk_model())
        except Exception as e:
            print(f"[stt] couldn't load the speech model: {e}")
            return
        self.rec = KaldiRecognizer(model, config.SAMPLE_RATE)

        try:
            stream = sd.RawInputStream(
                samplerate=config.SAMPLE_RATE, blocksize=int(config.SAMPLE_RATE * BLOCK_S),
                dtype="int16", channels=1, device=config.MIC_DEVICE,
                callback=lambda indata, frames, t, status: self._audio.put(bytes(indata)))
            stream.start()
        except Exception as e:
            print(f"[stt] can't open the microphone: {e}. `python main.py --mics` lists the options.")
            return

        self.ok = True
        print(f'[stt] listening for "{config.WAKE_PHRASES[0]}" (command transcription: {config.STT_ENGINE})', flush=True)
        try:
            self._loop()
        finally:
            stream.stop()
            stream.close()

    def _loop(self):
        while not self._halt.is_set():
            self._apply_commands()
            try:
                block = self._audio.get(timeout=0.2)
            except queue.Empty:
                block = None

            if self.state == BUSY:
                self.gate.reset()
                continue
            if self.state == AWAKE and not self.capturing and time.time() > self.awake_until:
                self.state = IDLE
                self._reset()
                self.on_timeout()
                continue
            if block is None:
                continue

            if self.state == AWAKE:
                self._feed(block)
                continue
            if not self.wake_enabled:
                continue

            was_open = self.gate.is_open
            for b in self.gate.push(block):
                self._feed(b)
                if self.state != IDLE:      # a phrase finished and changed our state
                    break
            if self.gate.just_closed:
                self._reset()
            if config.DEBUG_STT and was_open != self.gate.is_open:
                print(f"[mic] gate {'open' if self.gate.is_open else 'closed'} (background ~{self.gate.floor:.0f})")

    def _apply_commands(self):
        while True:
            try:
                listen = self._commands.get_nowait()
            except queue.Empty:
                return
            self._reset()
            self.gate.reset()
            if listen:
                self.state = AWAKE
                self.awake_until = time.time() + config.LISTEN_TIMEOUT_S
            else:
                self.state = IDLE

    def _reset(self):
        self.rec.Reset()
        self.woke_flagged = False
        self.capturing = False
        self.utt.clear()

    def _feed(self, block):
        self.recent.append(block)
        if self.capturing:
            self.utt += block
        if self.rec.AcceptWaveform(block):
            self._phrase_finished()
        else:
            self._phrase_in_progress()

    def _phrase_finished(self):
        text = (json.loads(self.rec.Result()).get("text") or "").strip()
        pcm = bytes(self.utt)
        was_flagged = self.woke_flagged
        self._reset()
        if not text:
            return
        if config.DEBUG_STT:
            print(f"[stt] heard: {text!r}")

        if self.state == AWAKE:
            self.state = BUSY
            self.on_utterance(text, to_wav(pcm), False)
            return

        found, rest = match_wake(text)
        if not found:
            if was_flagged:
                self.on_false_wake()
            return
        self.state = BUSY
        if len(rest) >= 2:
            self.on_utterance(rest, to_wav(pcm), False)     # "hey akari what time is it" in one breath
        else:
            self.on_utterance("", b"", True)

    def _phrase_in_progress(self):
        partial = (json.loads(self.rec.PartialResult()).get("partial") or "").strip()
        if not partial:
            return
        if not self.capturing:
            self.capturing = True
            self.utt.clear()
            for b in self.recent:
                self.utt += b
        if self.state == IDLE and not self.woke_flagged and match_wake(partial)[0]:
            self.woke_flagged = True
            self.on_wake()
        elif self.state == AWAKE:
            self.awake_until = time.time() + 30     # still talking, don't time out mid-sentence
