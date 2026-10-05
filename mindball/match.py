"""Ball physics for one Mindball match (pure logic, no drawing, easy to test).

The ball sits at x in [-1, 1] (0 = centre). The more relaxed player pushes it toward the
other player's goal: x = +1 means LEFT pushed it all the way right and wins.
The match only ends when the ball is fully in a goal (like the MSI table). Over time the
ball speeds up and smaller alpha differences count as a full push ("sudden death"), so even
two equally relaxed players finish in ~30 s.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class MatchSettings:
    base_speed: float = 0.06  # centre-to-goal distances per second at full advantage, at t=0
    ramp_seconds: float = 10.0  # speed doubles (and contrast halves) after this long, then ~t^2
    contrast: float = 0.3  # log alpha ratio that gives ~76% of full push (tanh(1)) at t=0
    inertia: float = 2.5  # momentum: seconds for the ball's velocity to catch up with the push


@dataclass
class Match:
    settings: MatchSettings = field(default_factory=MatchSettings)
    t: float = 0.0
    x: float = 0.0
    v: float = 0.0
    winner: str | None = None  # "LEFT" | "RIGHT"
    trace: list[tuple[float, float, float, float]] = field(default_factory=list)  # (t, x, aL, aR)

    def ramp(self) -> float:
        return 1.0 + (self.t / self.settings.ramp_seconds) ** 2

    def push(self, a_left: float, a_right: float) -> float:
        """-1..1: positive when LEFT is more relaxed (pushes the ball right)."""
        ratio = np.log(max(a_left, 1e-3) / max(a_right, 1e-3))
        return float(np.tanh(ratio * self.ramp() / self.settings.contrast))

    def gain(self) -> float:
        return self.settings.base_speed * self.ramp()

    def step(self, dt: float, a_left: float, a_right: float) -> None:
        if self.winner:
            return
        self.t += dt
        target = self.gain() * self.push(a_left, a_right)
        self.v += (target - self.v) * (1.0 - np.exp(-dt / self.settings.inertia))
        self.x = float(np.clip(self.x + self.v * dt, -1.0, 1.0))
        if not self.trace or self.t - self.trace[-1][0] >= 0.25:
            self.trace.append((self.t, self.x, a_left, a_right))
        if abs(self.x) >= 1.0:
            self.winner = "LEFT" if self.x > 0 else "RIGHT"
            self.trace.append((self.t, self.x, a_left, a_right))
