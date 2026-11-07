"""Tiny settings file (~/.config/akari/state.json): where she was last standing and which desktop you picked."""
import json

from . import config


def load():
    try:
        return json.loads(config.STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def update(**values):
    data = load()
    data.update(values)
    try:
        config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.STATE_FILE.write_text(json.dumps(data))
    except OSError:
        pass


def forget(*keys):
    data = load()
    for k in keys:
        data.pop(k, None)
    try:
        config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.STATE_FILE.write_text(json.dumps(data))
    except OSError:
        pass
