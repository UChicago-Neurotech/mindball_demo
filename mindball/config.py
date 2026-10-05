"""Load config.toml from the repo root (all keys optional)."""

from __future__ import annotations

import tomllib
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.toml"

DEFAULTS = {
    "players": {"left": "", "right": ""},
    "streams": {"prefix": "X.on"},
    "signal": {"score_channels": ["P3", "P4", "Cz"], "window_seconds": 2.0},
    "game": {"base_speed": 0.06, "ramp_seconds": 10.0, "inertia": 2.5, "contrast": 0.3, "smoothing_seconds": 2.0},
}


def load(path: Path = DEFAULT_PATH) -> dict:
    user = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {section: {**values, **user.get(section, {})} for section, values in DEFAULTS.items()}
