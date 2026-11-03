#!/usr/bin/env bash
# Start Akari when you log in. Delete ~/.config/autostart/akari.desktop to undo.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/akari.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Akari
Exec=$DIR/venv/bin/python $DIR/main.py
Path=$DIR
Terminal=false
X-GNOME-Autostart-Delay=6
DESK
echo "installed ~/.config/autostart/akari.desktop"
