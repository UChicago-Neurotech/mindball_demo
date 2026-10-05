"""Live two-headset signal check: traces, per-channel quality, relative alpha, sync.

    python -m mindball.monitor              # windowed
    python -m mindball.monitor --fullscreen

Keys: S swap sides · +/- trace scale · F fullscreen · Esc quit
"""

from __future__ import annotations

import argparse
from collections import deque

import numpy as np
import pygame
import pylsl

from . import config, dsp
from .streams import Headset, HeadsetManager

TRACE_SECONDS = 5.0
HISTORY_SECONDS = 30.0
HISTORY_HOP = 0.25
EMA_TAU = 1.0
ALPHA_BAR_MAX = 0.5

BG = (11, 15, 23)
PANEL = (18, 24, 38)
GRID = (32, 40, 58)
TEXT = (232, 237, 245)
SUBTEXT = (138, 150, 168)
QUALITY = {"good": (62, 207, 142), "warn": (245, 165, 36), "bad": (240, 79, 95)}
STATUS = {"OK": QUALITY["good"], "SLOW": QUALITY["warn"], "CONNECTING": QUALITY["warn"], "NO DATA": QUALITY["bad"]}
SIDE = [("LEFT", (76, 201, 240)), ("RIGHT", (247, 37, 133))]


class PlayerView:
    """Per-headset display state (kept by stream name so swapping sides keeps history)."""

    def __init__(self, headset: Headset):
        self.filter = dsp.DisplayFilter(headset.srate)
        self.alpha = None
        self.history: deque[float] = deque(maxlen=int(HISTORY_SECONDS / HISTORY_HOP))
        self.next_hist = 0.0
        self.quality: list[tuple[str, str]] = []
        self.moving = 0.0


def update_view(view: PlayerView, headset: Headset, cfg: dict, dt: float) -> None:
    window = cfg["signal"]["window_seconds"]
    if not headset.has(window) or headset.status == "NO DATA":
        return
    eeg, _ = headset.latest(window, headset.eeg_labels)
    view.quality = dsp.channel_quality(eeg, headset.srate)
    if headset.acc_idx:
        acc, _ = headset.latest(1.0)
        view.moving = dsp.movement(acc[:, headset.acc_idx])

    usable = [
        i
        for i, label in enumerate(headset.eeg_labels)
        if label in cfg["signal"]["score_channels"] and view.quality[i][0] != "bad"
    ]
    if not usable:
        return
    rel = float(dsp.relative_alpha(eeg[:, usable], headset.srate).mean())
    k = 1.0 - np.exp(-dt / EMA_TAU)
    view.alpha = rel if view.alpha is None else view.alpha + k * (rel - view.alpha)
    now = pylsl.local_clock()
    if now >= view.next_hist:
        view.history.append(view.alpha)
        view.next_hist = now + HISTORY_HOP


class Monitor:
    def __init__(self, cfg: dict, fullscreen: bool):
        pygame.init()
        pygame.display.set_caption("Mindball — signal check")
        flags = pygame.FULLSCREEN if fullscreen else pygame.RESIZABLE
        self.screen = pygame.display.set_mode((1280, 720), flags)
        self.font = {size: pygame.font.Font(None, size) for size in (18, 22, 28, 40)}
        self.cfg = cfg
        self.manager = HeadsetManager(cfg["streams"]["prefix"], cfg["players"]["left"], cfg["players"]["right"])
        self.views: dict[str, PlayerView] = {}
        self.scale_uv = 50.0  # half-height of each trace row in uV
        self.last_log = 0.0

    def text(self, s: str, size: int, color, pos, anchor: str = "topleft") -> pygame.Rect:
        surf = self.font[size].render(s, True, color)
        rect = surf.get_rect(**{anchor: pos})
        self.screen.blit(surf, rect)
        return rect

    def run(self, seconds: float | None = None, screenshot: str | None = None) -> None:
        clock = pygame.time.Clock()
        start = pylsl.local_clock()
        running = True
        while running:
            dt = clock.tick(30) / 1000.0
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
                    elif event.key == pygame.K_s:
                        self.manager.swap()
                    elif event.key == pygame.K_f:
                        pygame.display.toggle_fullscreen()
                    elif event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                        self.scale_uv = max(5.0, self.scale_uv / 1.5)
                    elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                        self.scale_uv = min(500.0, self.scale_uv * 1.5)

            self.manager.update()
            for headset in self.manager.headsets.values():
                view = self.views.setdefault(headset.name, PlayerView(headset))
                update_view(view, headset, self.cfg, dt)
            self.draw()
            pygame.display.flip()
            self.log()
            if seconds is not None and pylsl.local_clock() - start > seconds:
                running = False
        if screenshot:
            pygame.image.save(self.screen, screenshot)
        pygame.quit()

    def log(self) -> None:
        now = pylsl.local_clock()
        if now - self.last_log < 5.0:
            return
        self.last_log = now
        parts = []
        for (side, _), headset in zip(SIDE, self.manager.players()):
            if headset is None:
                parts.append(f"{side}: waiting")
                continue
            view = self.views.get(headset.name)
            alpha = f"{view.alpha:.0%}" if view and view.alpha is not None else "--"
            parts.append(f"{side}: {headset.name} {headset.status} {headset.rate:.0f}Hz alpha {alpha}")
        offset = self.manager.sync_offset()
        sync = f" | sync {offset * 1000:+.0f} ms" if offset is not None else ""
        print(" | ".join(parts) + sync)

    def draw(self) -> None:
        w, h = self.screen.get_size()
        self.screen.fill(BG)
        self.text("MINDBALL", 40, TEXT, (24, 14))
        self.text("signal check", 28, SUBTEXT, (190, 22))
        offset = self.manager.sync_offset()
        if offset is not None:
            color = QUALITY["good"] if abs(offset) < 0.1 else QUALITY["warn"]
            self.text(f"headset sync offset {offset * 1000:+.0f} ms", 22, color, (w - 24, 24), "topright")

        top, bottom, gap = 60, h - 34, 16
        panel_w = (w - 3 * gap) // 2
        for i, headset in enumerate(self.manager.players()):
            rect = pygame.Rect(gap + i * (panel_w + gap), top, panel_w, bottom - top)
            self.draw_panel(rect, i, headset)

        self.text(
            f"S swap sides   +/- trace scale (±{self.scale_uv:.0f} µV)   F fullscreen   Esc quit",
            18, SUBTEXT, (24, h - 24),
        )

    def draw_panel(self, rect: pygame.Rect, side: int, headset: Headset | None) -> None:
        name, accent = SIDE[side]
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=10)
        pygame.draw.rect(self.screen, accent, (rect.x, rect.y, rect.w, 4), border_top_left_radius=10, border_top_right_radius=10)
        x, y = rect.x + 16, rect.y + 14
        self.text(name, 40, accent, (x, y))

        if headset is None:
            pinned = self.manager.slots[side]
            msg = f"waiting for {pinned}" if pinned else "waiting for a headset..."
            self.text(msg, 28, TEXT, rect.center, "center")
            others = [i.name() for i in self.manager.visible if i.name() not in self.manager.slots]
            if others:
                self.text("visible EEG streams: " + ", ".join(others), 18, SUBTEXT, (rect.centerx, rect.centery + 30), "center")
            return

        self.text(headset.name, 22, SUBTEXT, (x + 110, y + 4))
        self.text(f"from {headset.hostname}", 18, SUBTEXT, (x + 110, y + 24))
        status = headset.status
        lag = f" · lag {headset.lag * 1000:.0f} ms" if np.isfinite(headset.lag) else ""
        self.text(f"{status}  {headset.rate:.1f} Hz{lag}", 22, STATUS[status], (rect.right - 16, y + 8), "topright")

        view = self.views.get(headset.name)
        if view is None:
            return

        # --- traces
        traces = pygame.Rect(rect.x + 12, rect.y + 64, rect.w - 24, int(rect.h * 0.58))
        labels = headset.eeg_labels
        row_h = traces.h / max(1, len(labels))
        data = None
        if headset.has(0.5):
            raw, _ = headset.latest(TRACE_SECONDS, labels)
            data = view.filter(raw.astype(np.float64))
        plot_x, plot_w = traces.x + 70, traces.w - 70
        for r, label in enumerate(labels):
            cy = traces.y + row_h * (r + 0.5)
            pygame.draw.line(self.screen, GRID, (plot_x, cy), (plot_x + plot_w, cy))
            level, reason = view.quality[r] if r < len(view.quality) else ("warn", "")
            pygame.draw.circle(self.screen, QUALITY[level], (traces.x + 8, int(cy)), 6)
            self.text(label, 22, TEXT, (traces.x + 22, cy), "midleft")
            if reason:
                self.text(reason, 18, QUALITY[level], (plot_x + plot_w, cy - row_h / 2 + 2), "topright")
            if data is not None and len(data) > 1:
                n_full = int(TRACE_SECONDS * headset.srate)
                step = max(1, n_full // plot_w)
                ys = data[::step, r]
                xs = plot_x + plot_w * (np.arange(len(ys)) * step + (n_full - len(data))) / n_full
                ys = np.clip(cy - ys / self.scale_uv * (row_h / 2), cy - row_h / 2, cy + row_h / 2)
                pygame.draw.lines(self.screen, accent, False, np.column_stack([xs, ys]).tolist(), 1)

        # --- relative alpha
        ay = traces.bottom + 18
        chans = " ".join(c for c in self.cfg["signal"]["score_channels"] if c in labels)
        self.text(f"relative alpha ({chans})", 22, SUBTEXT, (x, ay))
        value = view.alpha
        self.text(f"{value:.0%}" if value is not None else "--", 40, TEXT, (rect.right - 16, ay - 6), "topright")
        bar = pygame.Rect(x, ay + 26, rect.w - 32 - 80, 16)
        pygame.draw.rect(self.screen, GRID, bar, border_radius=8)
        if value is not None:
            fill = bar.copy()
            fill.w = int(bar.w * min(1.0, value / ALPHA_BAR_MAX))
            pygame.draw.rect(self.screen, accent, fill, border_radius=8)

        # --- 30 s alpha history
        hist = pygame.Rect(x, bar.bottom + 12, rect.w - 32, max(30, rect.bottom - bar.bottom - 52))
        pygame.draw.rect(self.screen, GRID, hist, 1, border_radius=6)
        if len(view.history) > 1:
            vals = np.array(view.history)
            n_full = view.history.maxlen
            xs = hist.right - hist.w * (len(vals) - 1 - np.arange(len(vals))) / (n_full - 1)
            ys = hist.bottom - 4 - (hist.h - 8) * np.clip(vals / ALPHA_BAR_MAX, 0, 1)
            pygame.draw.lines(self.screen, accent, False, np.column_stack([xs, ys]).tolist(), 2)
        self.text(f"last {HISTORY_SECONDS:.0f} s", 18, SUBTEXT, (hist.x + 6, hist.y + 4))

        moving = view.moving > dsp.MOVING_MG
        self.text(
            "HEAD MOVING" if moving else "head still",
            22, QUALITY["warn"] if moving else SUBTEXT, (x, rect.bottom - 26),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--seconds", type=float, help="quit after this many seconds")
    parser.add_argument("--screenshot", metavar="PNG", help="save the last frame on exit")
    args = parser.parse_args()
    Monitor(config.load(), args.fullscreen).run(args.seconds, args.screenshot)


if __name__ == "__main__":
    main()
