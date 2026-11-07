#!/usr/bin/env bash
# Start Akari when you log in.
#   XFCE / GNOME / KDE: writes ~/.config/autostart/akari.desktop  (delete it to undo)
#   Hyprland: autostart lives in your Hyprland config, so this prints the lines to paste.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RUN="$DIR/venv/bin/python $DIR/main.py"

if [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ] || [[ "${XDG_CURRENT_DESKTOP:-}" == *Hyprland* ]]; then
  echo "You're on Hyprland. Add this to hyprland.conf (or the Lua equivalent from"
  echo "'$DIR/venv/bin/python $DIR/main.py --hypr-config'):"
  echo
  echo "  exec-once = $RUN"
  echo "  bind = SUPER, A, exec, $RUN --talk"
  exit 0
fi

mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/akari.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Akari
Exec=$RUN
Path=$DIR
Terminal=false
X-GNOME-Autostart-Delay=6
DESK
echo "installed ~/.config/autostart/akari.desktop"
