#!/usr/bin/env bash
# One-time setup for Linux (apt, pacman or dnf).   ./setup.sh     or    PYTHON=python3.14 ./setup.sh
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"

echo "installing system packages (mic, audio, window tweaks, Qt libs)..."
if command -v apt >/dev/null; then            # Mint, Ubuntu, Debian
  sudo apt update
  sudo apt install -y libportaudio2 mpv wmctrl libxcb-cursor0 libxcb-xinerama0 libnss3 libxkbfile1 \
    libxcomposite1 libxdamage1 libxrandr2 libxtst6 libgbm1
  sudo apt install -y libasound2t64 2>/dev/null || sudo apt install -y libasound2
elif command -v pacman >/dev/null; then       # Arch and friends (a lot of Hyprland users)
  sudo pacman -S --needed --noconfirm portaudio mpv wmctrl nss libxcb xcb-util-cursor alsa-lib python
elif command -v dnf >/dev/null; then          # Fedora
  sudo dnf install -y portaudio mpv wmctrl nss libxcb xcb-util-cursor alsa-lib
else
  echo "unknown package manager: install portaudio, mpv and wmctrl yourself, then rerun."
fi

echo "making the venv with $($PY --version)..."
[ -d venv ] || "$PY" -m venv venv
venv/bin/pip install -U pip
venv/bin/pip install -r requirements.txt

[ -f .env ] || cp .env.example .env

echo "downloading the offline wake-word model (~40 MB)..."
venv/bin/python -c "from src.stt import ensure_vosk_model; print(ensure_vosk_model())"

cat <<MSG

All set. Now:
  1. put a .vrm model in ./models/     (README says where to get one)
  2. add your free API key to ./.env   (Groq first: https://console.groq.com/keys)
  3. venv/bin/python main.py --check
  4. venv/bin/python main.py      (it asks which desktop you use the first time)
MSG
