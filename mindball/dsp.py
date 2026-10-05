"""Signal processing: band power, relative alpha, channel quality, movement.

All inputs are (samples x channels) arrays in microvolts. Thresholds were picked from
last year's X.on recording (seated participant, saline sponges): per-channel SD over 2 s
windows ran 7-60 uV (99.9th pct ~93), the 60 Hz bin sat ~1.3x above its 50-58 Hz
neighbours (99th pct ~5x), and the accelerometer jitters ~8 mg at rest (it is quantized
in 15.6 mg steps).
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, iirnotch, sosfiltfilt, tf2sos, welch

ALPHA = (8.0, 13.0)
TOTAL = (2.0, 40.0)

FLAT_SD = 1.0  # uV; below this the channel isn't picking anything up
NOISY_SD = 100.0  # uV; above this it's bad contact or movement
NOISY_PTP = 500.0
WARN_SD = 60.0
WARN_LINE = 10.0  # 59-61 Hz PSD / 50-58 Hz PSD (a narrow peak, not broadband muscle noise)
MOVING_MG = 30.0  # SD of |acceleration| over the window


def psd(x: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    x = x - x.mean(axis=0)
    return welch(x, fs=fs, nperseg=min(len(x), int(fs)), axis=0)


def band(f: np.ndarray, p: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return p[(f >= lo) & (f < hi)].sum(axis=0)


def relative_alpha(x: np.ndarray, fs: float) -> np.ndarray:
    """Per-channel alpha power as a fraction of 2-40 Hz power."""
    f, p = psd(x, fs)
    return band(f, p, *ALPHA) / np.maximum(band(f, p, *TOTAL), 1e-12)


def channel_quality(x: np.ndarray, fs: float) -> list[tuple[str, str]]:
    """Per-channel ("good" | "warn" | "bad", reason)."""
    sd = x.std(axis=0)
    ptp = np.ptp(x, axis=0)
    f, p = psd(x, fs)
    near = p[(f >= 50.0) & (f < 58.0)].mean(axis=0)
    line = p[(f >= 59.0) & (f <= 61.0)].mean(axis=0) / np.maximum(near, 1e-12)
    out = []
    for s, pp, ln in zip(sd, ptp, line):
        if s < FLAT_SD:
            out.append(("bad", "flat"))
        elif s > NOISY_SD or pp > NOISY_PTP:
            out.append(("bad", "noisy"))
        elif ln > WARN_LINE:
            out.append(("warn", "60 Hz"))
        elif s > WARN_SD:
            out.append(("warn", "high"))
        else:
            out.append(("good", ""))
    return out


def movement(acc: np.ndarray) -> float:
    """SD of acceleration magnitude (mg) — rises when the head moves."""
    if acc.size == 0:
        return 0.0
    return float(np.linalg.norm(acc, axis=1).std())


class DisplayFilter:
    """1-40 Hz bandpass + 60 Hz notch for drawing traces (zero-phase, display only)."""

    def __init__(self, fs: float):
        self.sos = np.vstack(
            [
                butter(4, [1.0, 40.0], btype="band", fs=fs, output="sos"),
                tf2sos(*iirnotch(60.0, 30.0, fs=fs)),
            ]
        )

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if len(x) < 64:
            return x - x.mean(axis=0)
        return sosfiltfilt(self.sos, x - x.mean(axis=0), axis=0)
