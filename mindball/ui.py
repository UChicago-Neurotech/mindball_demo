"""Colours and drawing helpers shared by the monitor and the game."""

from __future__ import annotations

import numpy as np
import pygame

BG = (11, 15, 23)
PANEL = (18, 24, 38)
GRID = (32, 40, 58)
TEXT = (232, 237, 245)
SUBTEXT = (138, 150, 168)
QUALITY = {"good": (62, 207, 142), "warn": (245, 165, 36), "bad": (240, 79, 95)}
STATUS = {"OK": QUALITY["good"], "SLOW": QUALITY["warn"], "CONNECTING": QUALITY["warn"], "NO DATA": QUALITY["bad"]}
SIDE = [("LEFT", (76, 201, 240)), ("RIGHT", (247, 37, 133))]


class Fonts:
    """Default font at sizes given for a 720 px tall window, scaled to the real height."""

    def __init__(self):
        self.scale = 1.0
        self.cache: dict[int, pygame.font.Font] = {}

    def set_height(self, h: int) -> None:
        scale = max(0.5, h / 720)
        if scale != self.scale:
            self.scale, self.cache = scale, {}

    def __call__(self, size: int) -> pygame.font.Font:
        px = max(8, int(size * self.scale))
        if px not in self.cache:
            self.cache[px] = pygame.font.Font(None, px)
        return self.cache[px]


def text(screen: pygame.Surface, font: pygame.font.Font, s: str, color, pos, anchor: str = "topleft") -> pygame.Rect:
    surf = font.render(s, True, color)
    rect = surf.get_rect(**{anchor: pos})
    screen.blit(surf, rect)
    return rect


def draw_traces(
    screen: pygame.Surface,
    rect: pygame.Rect,
    data: np.ndarray | None,
    labels: list[str],
    quality: list[tuple[str, str]],
    color,
    scale_uv: float,
    n_full: int,
    fonts: Fonts,
) -> None:
    """Stacked EEG rows (newest sample at the right edge) with a quality dot per channel."""
    row_h = rect.h / max(1, len(labels))
    label_w = int(62 * fonts.scale)
    plot_x, plot_w = rect.x + label_w, rect.w - label_w
    for r, label in enumerate(labels):
        cy = rect.y + row_h * (r + 0.5)
        pygame.draw.line(screen, GRID, (plot_x, cy), (plot_x + plot_w, cy))
        level, reason = quality[r] if r < len(quality) else ("warn", "")
        pygame.draw.circle(screen, QUALITY[level], (rect.x + 8, int(cy)), max(3, int(5 * fonts.scale)))
        text(screen, fonts(22), label, TEXT, (rect.x + 20, cy), "midleft")
        if reason:
            text(screen, fonts(18), reason, QUALITY[level], (plot_x + plot_w, cy - row_h / 2 + 2), "topright")
        if data is not None and len(data) > 1:
            step = max(1, n_full // max(1, plot_w))
            ys = data[::step, r]
            xs = plot_x + plot_w * (np.arange(len(ys)) * step + (n_full - len(data))) / n_full
            ys = np.clip(cy - ys / scale_uv * (row_h / 2), cy - row_h / 2, cy + row_h / 2)
            pygame.draw.lines(screen, color, False, np.column_stack([xs, ys]).tolist(), 1)
