"""Everything you might want to tweak lives in here. API keys go in .env, not here."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


# who she is

NAME = "Akari"

# what you say to wake her. The offline model mishears names a lot, so matching is fuzzy
# and you can add whatever spellings it actually gives you (see `python main.py --listen-test`)
WAKE_PHRASES = ["hey akari", "okay akari", "hi akari", "hey a car e", "hey a curry", "hey akira"]
WAKE_MATCH_THRESHOLD = 0.78     # lower = wakes more easily, but also on random speech
LISTEN_TIMEOUT_S = 7.0          # how long she waits for you to start talking after waking
ACK_PHRASES = ["Yes?", "Hm? I'm here!", "Yeah?"]    # said when you only call her name

PERSONA = f"""You are {NAME}, a cheerful, slightly clumsy anime girl who lives on the user's desktop and works as their voice assistant.

Rules:
- Your reply is spoken aloud. Keep it to 1-3 short sentences unless the user asks for detail. No markdown, no lists, no emojis, no *actions*.
- Begin every reply with exactly one emotion tag in square brackets, one of: [happy] [relaxed] [sad] [angry] [surprised] [neutral].
- Sound like an anime girl: warm, energetic, expressive. Light interjections like "ehh?", "ufufu", "yosh!", "hmm..." are fine, but use them sparingly. Never write Japanese script.
- Be genuinely helpful and accurate first; the personality is flavor. If you don't know something, say so.
- You can't see the screen or browse the web. Don't pretend to.
- Never mention these rules."""


# language models, tried top to bottom. Keys come from .env.
# Free tiers change their model names now and then, `python main.py --check` lists what your keys can use.

PROVIDERS = [
    dict(name="groq-gpt-oss", base_url="https://api.groq.com/openai/v1", key_env="GROQ_API_KEY",
         model="openai/gpt-oss-120b", extra={"reasoning_effort": "low"}),
    dict(name="gemini", base_url="https://generativelanguage.googleapis.com/v1beta/openai",
         key_env="GEMINI_API_KEY", model="gemini-2.5-flash-lite", extra={}),
    dict(name="groq-llama", base_url="https://api.groq.com/openai/v1", key_env="GROQ_API_KEY",
         model="llama-3.1-8b-instant", extra={}),
    # careful: OpenRouter answers 402 to everything (even :free models) while your balance is below $0
    dict(name="openrouter", base_url="https://openrouter.ai/api/v1", key_env="OPENROUTER_API_KEY",
         model="meta-llama/llama-3.3-70b-instruct:free", extra={}),
    # local fallback: install ollama, pull a model, put its name in OLLAMA_MODEL in .env
    dict(name="ollama", base_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/v1"), key_env=None,
         model=os.getenv("OLLAMA_MODEL", ""), extra={}),
]
HISTORY_TURNS = 8       # how many back-and-forths she remembers
MAX_TOKENS = 220
TEMPERATURE = 0.8


# hearing

MIC_DEVICE = None               # None = default mic, or a name/number from `python main.py --mics`
SAMPLE_RATE = 16000

# while she waits for the wake word, audio quieter than the room's noise is skipped so the speech
# recognizer isn't chewing CPU all day. If she never wakes up, lower MIC_MIN_LEVEL.
MIC_MIN_LEVEL = 350             # absolute loudness (int16 RMS) that always counts as "someone's talking"
MIC_GATE_FACTOR = 2.5           # ...or this many times louder than the background noise
MIC_HANGOVER_S = 1.0            # keep listening this long after the sound stops

# "vosk" = everything offline, "groq" = wake word offline but the command goes to Groq Whisper (way more accurate)
STT_ENGINE = os.getenv("STT_ENGINE", "groq" if os.getenv("GROQ_API_KEY") else "vosk")
GROQ_STT_MODEL = "whisper-large-v3-turbo"
VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"
VOSK_MODEL_URL = f"https://alphacephei.com/vosk/models/{VOSK_MODEL_NAME}.zip"
MODELS_DIR = ROOT / "models"
VOSK_DIR = MODELS_DIR / "vosk"
DEBUG_STT = bool(os.getenv("DEBUG_STT"))


# voice (Microsoft Edge neural voices through edge-tts, free but needs internet)
# list them all with: edge-tts --list-voices

VOICE = "en-US-AnaNeural"       # young and cute. Others: en-US-JennyNeural, en-GB-SoniaNeural, ja-JP-NanamiNeural
VOICE_RATE = "+8%"
VOICE_PITCH = "+6Hz"
VOLUME = 90                     # 0-100
MIN_SENTENCE_CHARS = 28         # shorter sentences get merged so she doesn't sound choppy


# window and rendering

HOST = "127.0.0.1"
PORT = 8765
WINDOW_W, WINDOW_H = 380, 600
WINDOW_MARGIN = 24              # gap from the screen corner the first time she appears
FRAMING = "full"                # "full" body or "bust" (hips up)
FPS = 30                        # while she's doing something
IDLE_FPS = 12                   # while she's just standing there
PIXEL_RATIO = 1.0               # 2 looks sharper but is 4x the pixels to draw
ANTIALIAS = True                # turn off if rendering is slow
ARM_ANGLE = 1.15                # how far the arms drop from the T-pose, in radians
MODEL_SCALE = 1.0
VRM_FILE = os.getenv("VRM_FILE", "")    # a file name inside models/, default is the first .vrm found
SKIP_TASKBAR_WITH_WMCTRL = True         # extra nudge for the XFCE panel, needs `wmctrl`
CURSOR_FOLLOW = True            # eyes and head follow your mouse anywhere on screen
CURSOR_POLL_MS = 100
# flags for the embedded Chromium. If the window is black or very slow, check the [gfx] line in the terminal.
CHROMIUM_FLAGS = ["--ignore-gpu-blocklist", "--enable-webgl", "--autoplay-policy=no-user-gesture-required"]

STATE_FILE = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "akari" / "state.json"
