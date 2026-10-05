"""Fake X.on headsets on LSL, so everything can be developed without hardware (any OS).

    python -m mindball.sim                    # two synthetic headsets
    python -m mindball.sim --replay rec.xdf   # replay a real recording (looped) as two headsets

Streams look like the real thing: name ``X.on-SIM-000N``, type EEG, 11 float32 channels
(F3 F4 C3 Cz C4 P3 P4 BIP accX accY accZ) in uV / mg, same channel metadata.
Synthetic players drift between tense and relaxed on their own; relaxed means more
parietal alpha, tense adds muscle noise.
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import pylsl

from .streams import X_ON_LABELS

# Relative alpha strength per electrode (alpha is strongest over parietal cortex).
ALPHA_GAIN = np.array([0.3, 0.3, 0.6, 0.7, 0.6, 1.0, 1.0])
EMG_GAIN = np.array([1.0, 1.0, 0.5, 0.3, 0.5, 0.4, 0.4])


def make_info(name: str, source_id: str, fs: float) -> pylsl.StreamInfo:
    info = pylsl.StreamInfo(name, "EEG", len(X_ON_LABELS), fs, pylsl.cf_float32, source_id)
    channels = info.desc().append_child("channels")
    for label in X_ON_LABELS:
        ch = channels.append_child("channel")
        ch.append_child_value("label", label)
        is_acc = label.startswith("acc")
        ch.append_child_value("type", "AUX" if is_acc else "EEG")
        ch.append_child_value("unit", "mg" if is_acc else "μV")
    return info


def pink_noise(rng: np.random.Generator, n: int, channels: int, fs: float) -> np.ndarray:
    spectrum = rng.normal(size=(n // 2 + 1, channels)) + 1j * rng.normal(size=(n // 2 + 1, channels))
    f = np.arange(n // 2 + 1) / (n / fs)
    spectrum /= np.maximum(f, 1.0)[:, None] ** 0.9  # power ~ 1/f^1.8, like resting EEG
    spectrum[f < 1.0] = 0.0  # the X.on high-passes at 0.5 Hz
    x = np.fft.irfft(spectrum, n=n, axis=0)
    return x / x.std(axis=0)


class SyntheticHeadset:
    def __init__(self, seed: int, fs: float):
        self.rng = np.random.default_rng(seed)
        self.fs = fs
        self.alpha_hz = 9.5 + self.rng.uniform(0, 1.5)
        self.background = 10.0 * pink_noise(self.rng, int(fs * 64), 7, fs)
        self.pos = 0
        self.phase = self.rng.uniform(0, 2 * np.pi)
        self.relax = 0.5  # 0 = tense, 1 = relaxed; Ornstein-Uhlenbeck around 0.5
        self.blink_left = 0

    def chunk(self, n: int) -> np.ndarray:
        dt = 1.0 / self.fs
        out = np.zeros((n, len(X_ON_LABELS)), dtype=np.float32)
        # slow drift in relaxation (time constant ~8 s), occasionally jump (eyes close/open)
        self.relax += -(self.relax - 0.5) * n * dt / 8.0 + self.rng.normal() * np.sqrt(n * dt) * 0.25
        if self.rng.random() < n * dt / 15.0:
            self.relax = self.rng.choice([0.1, 0.95])
        self.relax = float(np.clip(self.relax, 0.0, 1.0))

        idx = (self.pos + np.arange(n)) % len(self.background)
        self.pos += n
        eeg = self.background[idx].copy()

        phase = self.phase + 2 * np.pi * self.alpha_hz * dt * np.arange(1, n + 1)
        self.phase = phase[-1] % (2 * np.pi)
        alpha_amp = 1.0 + 10.0 * self.relax**2.5
        wobble = 1.0 + 0.3 * self.rng.normal(size=(n, 1))
        eeg += alpha_amp * wobble * np.sin(phase)[:, None] * ALPHA_GAIN

        # muscle noise lives mostly above 20 Hz; differencing white noise tilts it high
        emg = np.diff(self.rng.normal(size=(n + 1, 7)), axis=0) * (1.0 - self.relax) ** 2 * 14.0
        eeg += emg * EMG_GAIN

        t = (self.pos - n + np.arange(n)) * dt
        eeg += 0.8 * np.sin(2 * np.pi * 60.0 * t)[:, None]

        if self.blink_left <= 0 and self.rng.random() < n * dt / 4.0:
            self.blink_left = int(0.3 * self.fs)
        if self.blink_left > 0:
            k = min(n, self.blink_left)
            total = int(0.3 * self.fs)
            s = np.arange(total - self.blink_left, total - self.blink_left + k)
            bump = 120.0 * np.sin(np.pi * s / total)
            eeg[:k, 0:2] += bump[:, None]
            self.blink_left -= k

        out[:, :7] = eeg
        out[:, 8:11] = np.array([0.0, 1000.0, 0.0]) + self.rng.normal(size=(n, 3)) * 8.0
        return out


class ReplayHeadset:
    def __init__(self, path: str, offset_s: float):
        import pyxdf

        streams, _ = pyxdf.load_xdf(path, select_streams=[{"type": "EEG"}])
        stream = streams[0]
        self.fs = float(stream["info"]["nominal_srate"][0])
        self.data = np.asarray(stream["time_series"], dtype=np.float32)
        if self.data.shape[1] != len(X_ON_LABELS):
            raise SystemExit(f"{path}: expected {len(X_ON_LABELS)} channels, got {self.data.shape[1]}")
        self.pos = int(offset_s * self.fs) % len(self.data)

    def chunk(self, n: int) -> np.ndarray:
        idx = (self.pos + np.arange(n)) % len(self.data)
        self.pos += n
        return self.data[idx]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", type=int, default=2, help="number of headsets (default 2)")
    parser.add_argument("--srate", type=float, default=250.0)
    parser.add_argument("--replay", metavar="XDF", help="stream a recording instead of synthetic data")
    args = parser.parse_args()

    headsets, outlets = [], []
    for i in range(args.n):
        if args.replay:
            source = ReplayHeadset(args.replay, offset_s=i * 300.0)  # different stretch per player
        else:
            source = SyntheticHeadset(seed=i, fs=args.srate)
        name = f"X.on-SIM-{i + 1:04d}"
        outlets.append(pylsl.StreamOutlet(make_info(name, f"SIM-{i + 1:04d} {int(source.fs)}", source.fs)))
        headsets.append(source)
        print(f"streaming {name} ({'replay' if args.replay else 'synthetic'}, {source.fs:g} Hz)")
    print("Ctrl+C to stop")

    fs = headsets[0].fs
    start, sent = pylsl.local_clock(), 0
    try:
        while True:
            due = int((pylsl.local_clock() - start) * fs) - sent
            if due > 0:
                for source, outlet in zip(headsets, outlets):
                    outlet.push_chunk(source.chunk(due))
                sent += due
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
