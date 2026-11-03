# Akari

A little 3D anime girl who lives on top of your desktop and answers when you talk to her.
Transparent window, always on top, no taskbar entry. Say "hey akari" (or double-click her)
and she replies out loud, in character.

Akari is a descendant of [VPET](https://github.com/Ashkii-yt/VPET). Same idea (a character that
lives on your screen and chats through AI APIs), but rebuilt around a 3D VRM model, a wake word and
a voice, and aimed at Linux instead of a PyQt6 pet window.

```
mic -> Vosk (offline wake word) -> Groq Whisper (optional, better accuracy)
    -> LLM (free providers, falls back if one fails) -> edge-tts voice
    -> VRM model that blinks, looks at your cursor and changes expression
```

## Setup

```bash
./setup.sh          # apt packages, venv, pip install, downloads the wake-word model
```
(`PYTHON=python3.14 ./setup.sh` if you want a specific Python. 3.10 or newer works.)

Then:

1. Put a `.vrm` file in `models/`. [VRoid Studio](https://vroid.com/en/studio) is free and exports anime-style VRM;
   VRoid Hub and Booth have ready-made ones (check the licence). If you have several, set `VRM_FILE=name.vrm` in `.env`.
2. Put at least one free key in `.env`:
   - `GROQ_API_KEY`: no card needed, https://console.groq.com/keys. Also gives you free Whisper for speech-to-text.
   - `GEMINI_API_KEY`: free tier on the Flash models, https://aistudio.google.com/apikey
   - `OPENROUTER_API_KEY`: only works while your balance is above $0. Below that it answers 402 even for `:free` models.
   - `OLLAMA_MODEL`: optional unlimited local fallback (`ollama pull llama3.2`).
3. `venv/bin/python main.py --check` shows which keys work and which model names they can see.
4. `venv/bin/python main.py`

## Using her

- "Hey Akari, what's the capital of Peru?" works in one breath.
- "Hey Akari" and then a pause: she says "Yes?" and listens.
- Double-click her to talk without the wake word. Click her while she's speaking to shut her up.
- Drag to move her (she remembers where). Right-click, or use the tray icon, for the menu.
- Say "stop" or "never mind" to cancel.

For a global hotkey, add a keyboard shortcut (Settings > Keyboard > Application Shortcuts) that runs
`/path/to/venv/bin/python /path/to/main.py --talk`. `--stop` and `--quit` work the same way.
`./install-autostart.sh` makes her start when you log in.

## Tuning

It's all at the top of `src/config.py`: her name, wake phrases, personality prompt, voice, window size,
frame rates, which AI providers get tried and in what order.

If the wake word misfires or never fires, run `main.py --listen-test` to see what the mic actually hears,
then add those spellings to `WAKE_PHRASES` or nudge `WAKE_MATCH_THRESHOLD`.

## If it's laggy

Start `main.py` from a terminal and look for the `[gfx]` line a few seconds after she appears. It says which
renderer Chromium picked and how much work each frame costs.

- If it says llvmpipe / SwiftShader / software, the CPU is doing the 3D drawing. Install your GPU driver
  (Menu > Driver Manager on Mint) or try dropping `--ignore-gpu-blocklist` from `CHROMIUM_FLAGS`.
- Lower `FPS`, `IDLE_FPS`, or set `ANTIALIAS = False`. Keep `PIXEL_RATIO` at 1.
- Heavy models hurt. VRoid exports can have huge textures and dozens of materials; reduce those in VRoid Studio.
- She only listens closely when there's sound in the room. If CPU stays high while it's quiet, your mic's
  background noise may be above `MIC_MIN_LEVEL`, raise it. If she never wakes up, lower it.

## Troubleshooting

- Black box instead of see-through: turn on the compositor (Settings > Window Manager Tweaks > Compositor).
- Still shows in the taskbar: `sudo apt install wmctrl`.
- No tray icon: the panel needs a Status Tray / Notification Area plugin. Right-click her instead.
- No sound: `sudo apt install mpv`. The voice needs internet; without it you just get subtitles.
- Wrong mic: `main.py --mics`, then set `MIC_DEVICE`.

## Known limits

- The transparent area around her still catches clicks, because the window is a rectangle. Shrink `WINDOW_W` / `WINDOW_H` if it bugs you.
- The mouth flaps while she talks, it isn't real lip-sync.
- three.js and three-vrm are loaded from a CDN, so the very first launch needs internet.
