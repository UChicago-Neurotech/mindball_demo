"""Per-player signal state shared by the monitor and the game."""

from __future__ import annotations

from collections import deque

import numpy as np
import pylsl

from . import dsp
from .streams import Headset

HISTORY_HOP = 0.25
MOVING_ALPHA = 0.05  # relative alpha assumed while the head is moving (moving = not relaxed)


class PlayerSignal:
    """Quality, movement, and smoothed relative alpha for one headset."""

    def __init__(self, headset: Headset, tau: float, history_s: float):
        self.tau = tau
        self.alpha: float | None = None
        self.history: deque[float] = deque(maxlen=int(history_s / HISTORY_HOP))
        self.next_hist = 0.0
        self.quality: list[tuple[str, str]] = []
        self.moving = 0.0
        self.usable = False  # False -> no trustworthy score right now (game pauses)

    @property
    def is_moving(self) -> bool:
        return self.moving > dsp.MOVING_MG

    def update(self, headset: Headset, cfg: dict, dt: float, penalize_movement: bool = False) -> None:
        self.usable = False
        window = cfg["signal"]["window_seconds"]
        if not headset.has(window) or headset.status == "NO DATA":
            return
        raw, _ = headset.latest(window, headset.eeg_labels)
        self.quality = dsp.channel_quality(raw, headset.srate)  # raw: the 60 Hz check needs the line noise
        if headset.acc_idx:
            acc, _ = headset.latest(1.0)
            self.moving = dsp.movement(acc[:, headset.acc_idx])

        chans = [
            i
            for i, label in enumerate(headset.eeg_labels)
            if label in cfg["signal"]["score_channels"] and self.quality[i][0] != "bad"
        ]
        if penalize_movement and self.is_moving:
            rel = MOVING_ALPHA
        elif chans:
            eeg, _ = headset.latest(window, headset.eeg_labels, filtered=True)
            rel = float(dsp.relative_alpha(eeg[:, chans], headset.srate).mean())
        else:
            return
        self.usable = True
        k = 1.0 - np.exp(-dt / self.tau)
        self.alpha = rel if self.alpha is None else self.alpha + k * (rel - self.alpha)
        now = pylsl.local_clock()
        if now >= self.next_hist:
            self.history.append(self.alpha)
            self.next_hist = now + HISTORY_HOP
