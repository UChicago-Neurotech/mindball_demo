"""Find X.on headsets on LSL, keep a rolling buffer per headset, and assign them to players.

Each headset is one LSL stream named like ``X.on-102801-0065`` (11 float32 channels at
250 Hz: F3 F4 C3 Cz C4 P3 P4 BIP accX accY accZ). Inlets are opened with clock sync +
dejitter, so every timestamp is on this machine's LSL clock no matter whether the stream
comes from the X.on PC app or a phone on the network.
"""

from __future__ import annotations

from collections import deque

import numpy as np
import pylsl

EEG_LABELS = ("F3", "F4", "C3", "Cz", "C4", "P3", "P4")
X_ON_LABELS = EEG_LABELS + ("BIP", "accX", "accY", "accZ")

STALE_AFTER_S = 1.0  # no samples for this long -> "NO DATA"
REOPEN_AFTER_S = 5.0  # stale this long and a fresh stream with the same name exists -> reopen
RATE_WINDOW_S = 5.0


def channel_labels(info: pylsl.StreamInfo) -> list[str]:
    labels = []
    ch = info.desc().child("channels").child("channel")
    while not ch.empty():
        labels.append(ch.child_value("label"))
        ch = ch.next_sibling()
    if len(labels) == info.channel_count():
        return labels
    if info.channel_count() == len(X_ON_LABELS):
        return list(X_ON_LABELS)
    return [f"ch{i + 1}" for i in range(info.channel_count())]


class Headset:
    """One LSL EEG stream: inlet, ring buffer, and health stats."""

    def __init__(self, info: pylsl.StreamInfo, buffer_s: float = 30.0):
        self.inlet = pylsl.StreamInlet(
            info, max_buflen=int(buffer_s), processing_flags=pylsl.proc_ALL
        )
        try:
            full = self.inlet.info(timeout=2.0)  # resolver results don't carry channel labels
        except Exception:  # timeout / lost (exception classes moved between pylsl versions)
            full = info
        self.name = info.name()
        self.uid = info.uid()
        self.source_id = info.source_id()
        self.hostname = info.hostname()
        self.srate = info.nominal_srate()
        self.labels = channel_labels(full)
        self.index = {label: i for i, label in enumerate(self.labels)}
        self.eeg_labels = [label for label in EEG_LABELS if label in self.index]
        self.acc_idx = [self.index[a] for a in ("accX", "accY", "accZ") if a in self.index]

        n = int(buffer_s * self.srate)
        self.buf = np.zeros((n, len(self.labels)), dtype=np.float32)
        self.ts = np.zeros(n)
        self.count = 0
        self.last_arrival = pylsl.local_clock()  # count silence from when we opened it
        self.arrivals: deque[tuple[float, int]] = deque()

    def pull(self) -> int:
        """Drain everything waiting in the inlet without blocking. Returns samples read."""
        total = 0
        while True:
            chunk, stamps = self.inlet.pull_chunk(timeout=0.0, max_samples=1024)
            if not stamps:
                break
            self._append(np.asarray(chunk, dtype=np.float32), np.asarray(stamps))
            total += len(stamps)
        now = pylsl.local_clock()
        if total:
            self.last_arrival = now
            self.arrivals.append((now, total))
        while self.arrivals and now - self.arrivals[0][0] > RATE_WINDOW_S:
            self.arrivals.popleft()
        return total

    def _append(self, data: np.ndarray, stamps: np.ndarray) -> None:
        size = len(self.buf)
        if len(data) > size:
            self.count += len(data) - size
            data, stamps = data[-size:], stamps[-size:]
        idx = np.arange(self.count, self.count + len(data)) % size
        self.buf[idx] = data
        self.ts[idx] = stamps
        self.count += len(data)

    def latest(self, seconds: float, labels: list[str] | None = None):
        """Most recent `seconds` of data, oldest first: (samples x channels, timestamps)."""
        n = min(int(seconds * self.srate), self.count, len(self.buf))
        idx = np.arange(self.count - n, self.count) % len(self.buf)
        cols = [self.index[label] for label in labels] if labels else slice(None)
        return self.buf[idx][:, cols], self.ts[idx]

    def has(self, seconds: float) -> bool:
        return self.count >= int(seconds * self.srate)

    @property
    def age(self) -> float:
        """Seconds since samples last arrived."""
        return pylsl.local_clock() - self.last_arrival

    @property
    def rate(self) -> float:
        """Effective sample rate over the last few seconds."""
        if len(self.arrivals) < 2 or self.age > STALE_AFTER_S:
            return 0.0
        span = self.arrivals[-1][0] - self.arrivals[0][0]
        return sum(n for _, n in list(self.arrivals)[1:]) / span if span > 0 else 0.0

    @property
    def lag(self) -> float:
        """Seconds between the newest sample's timestamp and now."""
        if not self.count:
            return np.inf
        return pylsl.local_clock() - self.ts[(self.count - 1) % len(self.buf)]

    @property
    def status(self) -> str:
        if not self.count:
            return "CONNECTING"
        if self.age > STALE_AFTER_S:
            return "NO DATA"
        if self.rate < 0.8 * self.srate:
            return "SLOW"
        return "OK"

    def close(self) -> None:
        self.inlet.close_stream()


class HeadsetManager:
    """Keeps inlets open for every visible headset and maps two of them to LEFT / RIGHT."""

    def __init__(self, prefix: str = "X.on", left: str = "", right: str = ""):
        self.prefix = prefix
        self.resolver = pylsl.ContinuousResolver(prop="type", value="EEG")
        self.headsets: dict[str, Headset] = {}
        self.slots: list[str | None] = [left or None, right or None]
        self.visible: list[pylsl.StreamInfo] = []
        self._last_scan = -np.inf

    def update(self) -> None:
        now = pylsl.local_clock()
        if now - self._last_scan > 1.0:
            self._last_scan = now
            self._scan()
        for headset in self.headsets.values():
            headset.pull()

    def _scan(self) -> None:
        self.visible = self.resolver.results()
        for info in self.visible:
            name = info.name()
            if not name.startswith(self.prefix):
                continue
            current = self.headsets.get(name)
            if current is None:
                self.headsets[name] = Headset(info)
                print(f"[lsl] opened {name} from {info.hostname()} ({info.nominal_srate():g} Hz)")
            elif current.uid != info.uid() and current.age > REOPEN_AFTER_S:
                # The app was restarted with a new stream id; the old inlet can't recover.
                current.close()
                self.headsets[name] = Headset(info)
                print(f"[lsl] reopened {name} (stream restarted)")
        for name in sorted(self.headsets):
            if name in self.slots:
                continue
            for i, slot in enumerate(self.slots):
                if slot is None:
                    self.slots[i] = name
                    print(f"[lsl] {name} -> {'LEFT' if i == 0 else 'RIGHT'}")
                    break

    def players(self) -> tuple[Headset | None, Headset | None]:
        left, right = (self.headsets.get(slot) if slot else None for slot in self.slots)
        return left, right

    def swap(self) -> None:
        self.slots.reverse()

    def sync_offset(self) -> float | None:
        """Timestamp difference between the newest samples of the two players (seconds)."""
        left, right = self.players()
        if not (left and right and left.count and right.count):
            return None
        return left.lag - right.lag
