"""Mindball: the calmer brain pushes the ball into the other player's goal.

    python -m mindball.game                 # real headsets (start streaming in the X.on app first)
    python -m mindball.game --sim           # also starts two simulated headsets (no hardware)
    python -m mindball.game --sim rec.xdf   # simulated headsets replaying a recording
    python -m mindball.game --fullscreen

Keys: SPACE start / rematch · R back to lobby · S swap sides (lobby) · +/- EEG scale
      D show numbers · F fullscreen · Esc quit
"""

from __future__ import annotations

import argparse
import atexit
import subprocess
import sys

import numpy as np
import pygame
import pylsl

from . import config, ui
from .match import Match, MatchSettings
from .player import PlayerSignal
from .streams import HeadsetManager
from .ui import BG, GRID, PANEL, QUALITY, SIDE, STATUS, SUBTEXT, TEXT, Fonts, draw_traces

TRACE_SECONDS = 4.0
SIGNAL_HOP = 0.1  # recompute alpha / quality this often (s)
COUNTDOWN_S = 3.0
GO_S = 0.8
LOST_AFTER_S = 1.5  # a player's signal unusable this long -> pause the match
CALM_BAR_MAX = 0.5  # relative alpha that fills the calm meter
LEAD_SHOW = 0.15  # |push| above this shows "MORE RELAXED"
BALL = (236, 240, 248)


def marker_outlet() -> pylsl.StreamOutlet:
    info = pylsl.StreamInfo("Mindball-Markers", "Markers", 1, 0, pylsl.cf_string, "mindball-markers")
    return pylsl.StreamOutlet(info)


def game_outlet() -> pylsl.StreamOutlet:
    info = pylsl.StreamInfo("Mindball-Game", "Game", 4, 0, pylsl.cf_float32, "mindball-game")
    channels = info.desc().append_child("channels")
    for label in ("ball_x", "alpha_left", "alpha_right", "push"):
        channels.append_child("channel").append_child_value("label", label)
    return pylsl.StreamOutlet(info)


def mix(a, b, t: float):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


class Game:
    def __init__(self, cfg: dict, fullscreen: bool, autostart: bool = False):
        pygame.init()
        pygame.display.set_caption("Mindball")
        flags = pygame.FULLSCREEN if fullscreen else pygame.RESIZABLE
        self.screen = pygame.display.set_mode((1280, 720), flags)
        self.fonts = Fonts()
        self.cfg = cfg
        g = cfg["game"]
        self.settings = MatchSettings(
            base_speed=g["base_speed"], ramp_seconds=g["ramp_seconds"], contrast=g["contrast"],
            inertia=g["inertia"],
        )
        self.manager = HeadsetManager(cfg["streams"]["prefix"], cfg["players"]["left"], cfg["players"]["right"])
        self.signals: dict[str, PlayerSignal] = {}
        self.signal_dt = 0.0
        self.match = Match(self.settings)
        self.state = "LOBBY"  # LOBBY -> COUNTDOWN -> PLAYING -> OVER
        self.state_since = pylsl.local_clock()
        self.lost_since: list[float | None] = [None, None]
        self.paused = False
        self.autostart = autostart
        self.scale_uv = 40.0
        self.show_numbers = False
        self.notice, self.notice_until = "", 0.0
        self.markers = marker_outlet()
        self.game_out = game_outlet()

    # ------------------------------------------------------------------ state

    def set_state(self, state: str, marker: str | None = None) -> None:
        self.state, self.state_since = state, pylsl.local_clock()
        if marker:
            self.mark(marker)

    def mark(self, marker: str) -> None:
        self.markers.push_sample([marker])
        print(f"[game] {marker}")

    def say(self, msg: str, seconds: float = 2.5) -> None:
        self.notice, self.notice_until = msg, pylsl.local_clock() + seconds

    def player_signals(self) -> list[PlayerSignal | None]:
        return [self.signals.get(h.name) if h else None for h in self.manager.players()]

    def ready(self) -> bool:
        return all(s is not None and s.alpha is not None and s.usable for s in self.player_signals())

    def start(self) -> None:
        if not self.ready():
            self.say("waiting for both headsets to have a good signal")
            return
        self.match = Match(self.settings)
        self.paused = False
        self.set_state("COUNTDOWN", "countdown")

    def update_signals(self, dt: float) -> None:
        self.manager.update()
        self.signal_dt += dt
        if self.signal_dt < SIGNAL_HOP:
            return
        for headset in self.manager.headsets.values():
            if headset.name not in self.signals:
                self.signals[headset.name] = PlayerSignal(headset, self.cfg["game"]["smoothing_seconds"], 30.0)
            self.signals[headset.name].update(headset, self.cfg, self.signal_dt, penalize_movement=True)
        self.signal_dt = 0.0

    def tick(self, dt: float) -> None:
        now = pylsl.local_clock()
        sigs = self.player_signals()
        for i, s in enumerate(sigs):
            if s is not None and s.usable:
                self.lost_since[i] = None
            elif self.lost_since[i] is None:
                self.lost_since[i] = now
        lost = [i for i, since in enumerate(self.lost_since) if since is not None and now - since > LOST_AFTER_S]

        if self.state == "LOBBY" and self.autostart and self.ready():
            self.start()
        elif self.state == "COUNTDOWN" and now - self.state_since >= COUNTDOWN_S:
            self.set_state("PLAYING", "start")
        elif self.state == "PLAYING":
            paused = bool(lost) or any(s is None or s.alpha is None for s in sigs)
            if paused != self.paused:
                self.mark("pause " + " ".join(SIDE[i][0] for i in lost) if paused else "resume")
            self.paused = paused
            if not paused:
                a_left, a_right = sigs[0].alpha, sigs[1].alpha
                self.match.step(dt, a_left, a_right)
                self.game_out.push_sample([self.match.x, a_left, a_right, self.match.push(a_left, a_right)])
                if self.match.winner:
                    self.set_state("OVER", f"win {self.match.winner} {self.match.t:.1f}s")

    def handle_key(self, key: int) -> bool:
        if key in (pygame.K_ESCAPE, pygame.K_q):
            return False
        if key == pygame.K_SPACE and self.state in ("LOBBY", "OVER"):
            self.start()
        elif key == pygame.K_r and self.state != "LOBBY":
            self.set_state("LOBBY", "abort" if self.state in ("COUNTDOWN", "PLAYING") else "lobby")
            self.match = Match(self.settings)
        elif key == pygame.K_s:
            if self.state == "LOBBY":
                self.manager.swap()
            else:
                self.say("swap sides from the lobby (R)")
        elif key == pygame.K_d:
            self.show_numbers = not self.show_numbers
        elif key == pygame.K_f:
            pygame.display.toggle_fullscreen()
        elif key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
            self.scale_uv = max(5.0, self.scale_uv / 1.5)
        elif key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self.scale_uv = min(500.0, self.scale_uv * 1.5)
        return True

    def run(self, seconds: float | None = None, screenshot: str | None = None) -> None:
        clock = pygame.time.Clock()
        start = pylsl.local_clock()
        running = True
        while running:
            dt = min(clock.tick(60) / 1000.0, 0.1)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    running = self.handle_key(event.key)
            self.update_signals(dt)
            self.tick(dt)
            self.draw()
            pygame.display.flip()
            if seconds is not None and pylsl.local_clock() - start > seconds:
                running = False
        if screenshot:
            pygame.image.save(self.screen, screenshot)
        pygame.quit()

    # ------------------------------------------------------------------ drawing

    def text(self, s: str, size: int, color, pos, anchor: str = "topleft") -> pygame.Rect:
        return ui.text(self.screen, self.fonts(size), s, color, pos, anchor)

    def push_now(self) -> float:
        sigs = self.player_signals()
        if any(s is None or s.alpha is None for s in sigs):
            return 0.0
        return self.match.push(sigs[0].alpha, sigs[1].alpha)

    def draw(self) -> None:
        w, h = self.screen.get_size()
        self.fonts.set_height(h)
        u = self.fonts.scale
        self.screen.fill(BG)
        gap = int(14 * u)

        arena = pygame.Rect(gap, gap, w - 2 * gap, int(h * 0.47))
        eeg = pygame.Rect(gap, arena.bottom + gap, w - 2 * gap, h - arena.bottom - 2 * gap - int(20 * u))
        self.draw_arena(arena)
        panel_w = (eeg.w - gap) // 2
        for i in range(2):
            self.draw_eeg(pygame.Rect(eeg.x + i * (panel_w + gap), eeg.y, panel_w, eeg.h), i)

        offset = self.manager.sync_offset()
        sync = f"   ·   headset sync {offset * 1000:+.0f} ms" if offset is not None else ""
        self.text(
            f"SPACE start   R lobby   S swap   D numbers   +/- EEG scale (±{self.scale_uv:.0f} µV)   F fullscreen   Esc quit{sync}",
            18, SUBTEXT, (gap, h - int(8 * u)), "bottomleft",
        )
        if self.notice and pylsl.local_clock() < self.notice_until:
            self.text(self.notice, 22, QUALITY["warn"], (w - gap, h - int(8 * u)), "bottomright")

    def draw_arena(self, rect: pygame.Rect) -> None:
        u = self.fonts.scale
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=int(14 * u))
        players = self.manager.players()
        sigs = self.player_signals()
        push = self.push_now()

        # title + clock
        self.text("MINDBALL", 34, SUBTEXT, (rect.centerx, rect.y + int(12 * u)), "midtop")
        if self.state in ("PLAYING", "COUNTDOWN"):
            t = self.match.t
            self.text(f"{int(t // 60)}:{int(t % 60):02d}", 56, TEXT, (rect.centerx, rect.y + int(40 * u)), "midtop")

        # track with a goal at each end; x = ±1 puts the ball dead centre in a goal
        r = int(rect.h * 0.085)
        track = pygame.Rect(0, 0, rect.w - int(48 * u), 2 * r + int(20 * u))
        track.center = (rect.centerx, rect.y + int(rect.h * 0.56))
        goal_w = track.h
        pygame.draw.rect(self.screen, BG, track, border_radius=track.h // 2)
        for i, (name, accent) in enumerate(SIDE):
            goal = pygame.Rect(track.x if i == 0 else track.right - goal_w, track.y, goal_w, track.h)
            winning_goal = self.state == "OVER" and self.match.winner != name
            flash = winning_goal and int(pygame.time.get_ticks() / 250) % 2 == 0
            pygame.draw.rect(self.screen, mix(BG, accent, 0.55 if flash else 0.18), goal, border_radius=track.h // 2)
            pygame.draw.rect(self.screen, accent, goal, max(2, int(3 * u)), border_radius=track.h // 2)
        pygame.draw.rect(self.screen, GRID, track, max(1, int(2 * u)), border_radius=track.h // 2)
        for f in (-0.5, 0.0, 0.5):
            cx = track.centerx + f * (track.w / 2 - goal_w / 2)
            pygame.draw.line(self.screen, GRID, (cx, track.y + 6), (cx, track.bottom - 6), 3 if f == 0 else 1)

        half = track.w / 2 - goal_w / 2
        bx = track.centerx + self.match.x * half
        if self.state == "PLAYING" and abs(push) > 0.05 and not self.paused:
            self.draw_push_arrow((bx, track.y - int(14 * u)), push, r)
        self.draw_ball((bx, track.centery), r, angle=self.match.x * half / r)

        # players at their ends: name, headset, calm meter, "MORE RELAXED"
        for i, (name, accent) in enumerate(SIDE):
            headset, sig = players[i], sigs[i]
            edge_x = rect.x + int(24 * u) if i == 0 else rect.right - int(24 * u)
            anchor = "topleft" if i == 0 else "topright"
            top = rect.y + int(14 * u)
            self.text(name, 56, accent, (edge_x, top), anchor)
            sub = headset.name if headset else "waiting for headset..."
            self.text(sub, 20, SUBTEXT, (edge_x, top + int(46 * u)), anchor)
            leading = (push > LEAD_SHOW) if i == 0 else (push < -LEAD_SHOW)
            if leading and self.state in ("LOBBY", "PLAYING", "COUNTDOWN"):
                self.text("MORE RELAXED", 30, accent, (edge_x, top + int(68 * u)), anchor)

            meter = pygame.Rect(0, 0, int(rect.w * 0.30), int(16 * u))
            meter.top = track.bottom + int(30 * u)
            if i == 0:
                meter.left = edge_x
            else:
                meter.right = edge_x
            label_pos = (meter.left, meter.top - int(4 * u)) if i == 0 else (meter.right, meter.top - int(4 * u))
            self.text("calm", 22, SUBTEXT, label_pos, "bottomleft" if i == 0 else "bottomright")
            pygame.draw.rect(self.screen, BG, meter, border_radius=meter.h // 2)
            if sig is not None and sig.alpha is not None:
                fill = meter.copy()
                fill.w = max(meter.h, int(meter.w * min(1.0, sig.alpha / CALM_BAR_MAX)))
                if i == 1:
                    fill.right = meter.right
                pygame.draw.rect(self.screen, accent, fill, border_radius=meter.h // 2)
                if self.show_numbers:
                    num_pos = (meter.right + int(10 * u), meter.centery) if i == 0 else (meter.left - int(10 * u), meter.centery)
                    self.text(f"{sig.alpha:.0%}", 22, TEXT, num_pos, "midleft" if i == 0 else "midright")
            if sig is not None and sig.is_moving and self.state != "OVER":
                pos = (meter.left, meter.bottom + int(6 * u)) if i == 0 else (meter.right, meter.bottom + int(6 * u))
                self.text("head moving", 20, QUALITY["warn"], pos, "topleft" if i == 0 else "topright")

        if self.show_numbers:
            self.text(
                f"push {push:+.2f}   speed {self.match.gain():.3f}/s   ball {self.match.x:+.2f}",
                20, SUBTEXT, (rect.centerx, rect.bottom - int(10 * u)), "midbottom",
            )

        # overlays
        now = pylsl.local_clock()
        if self.state == "LOBBY":
            self.draw_lobby(rect)
        elif self.state == "COUNTDOWN":
            n = int(np.ceil(COUNTDOWN_S - (now - self.state_since)))
            self.draw_big(rect, str(max(1, n)), TEXT)
        elif self.state == "PLAYING" and self.paused:
            who = [SIDE[i][0] for i, s in enumerate(self.lost_since) if s is not None] or ["a"]
            self.draw_banner(rect, "SIGNAL CHECK", f"adjust {' + '.join(who)} headset", QUALITY["warn"])
        elif self.state == "PLAYING" and now - self.state_since < GO_S:
            self.draw_big(rect, "GO!", QUALITY["good"])
        elif self.state == "OVER":
            self.draw_over(rect)

    def draw_ball(self, center, r: int, angle: float) -> None:
        cx, cy = center
        glow = pygame.Surface((4 * r, 4 * r), pygame.SRCALPHA)
        for k in range(4, 0, -1):
            pygame.draw.circle(glow, (*BALL, 7), (2 * r, 2 * r), int(r * (1 + 0.08 * k)))
        self.screen.blit(glow, (cx - 2 * r, cy - 2 * r))
        steps = 14
        for k in range(steps):
            t = k / (steps - 1)
            rad = int(r * (1 - 0.85 * t))
            off = r * 0.35 * t
            pygame.draw.circle(self.screen, mix((120, 130, 150), (255, 255, 255), t), (cx - off, cy - off), rad)
        for a in (angle, angle + np.pi):  # two spots that roll with the ball
            pygame.draw.circle(
                self.screen, (100, 110, 130),
                (cx + 0.55 * r * np.cos(a), cy + 0.55 * r * np.sin(a)), max(2, r // 7),
            )

    def draw_push_arrow(self, tip_base, push: float, r: int) -> None:
        x, y = tip_base
        color = SIDE[0][1] if push > 0 else SIDE[1][1]
        d = 1 if push > 0 else -1
        length = r * (0.6 + 1.6 * abs(push))
        pygame.draw.line(self.screen, color, (x - d * length / 2, y), (x + d * length / 2, y), max(3, r // 6))
        tip = x + d * (length / 2 + r * 0.35)
        pygame.draw.polygon(self.screen, color, [(tip, y), (tip - d * r * 0.4, y - r * 0.3), (tip - d * r * 0.4, y + r * 0.3)])

    def card(self, rect: pygame.Rect, w_frac: float, h_frac: float) -> pygame.Rect:
        card = pygame.Rect(0, 0, int(rect.w * w_frac), int(rect.h * h_frac))
        card.center = rect.center
        surf = pygame.Surface(card.size, pygame.SRCALPHA)
        pygame.draw.rect(surf, (*BG, 248), surf.get_rect(), border_radius=int(16 * self.fonts.scale))
        self.screen.blit(surf, card)
        pygame.draw.rect(self.screen, GRID, card, 2, border_radius=int(16 * self.fonts.scale))
        return card

    def draw_lobby(self, rect: pygame.Rect) -> None:
        u = self.fonts.scale
        card = self.card(rect, 0.42, 0.78)
        x, y = card.x + int(28 * u), card.y + int(22 * u)
        self.text("HOW TO PLAY", 34, TEXT, (card.centerx, y), "midtop")
        steps = [
            ("1", "Sit still, hands in your lap"),
            ("2", "Close your eyes and relax"),
            ("3", "The calmer brain pushes the ball"),
            ("", "into the other player's goal"),
        ]
        y += int(46 * u)
        for num, line in steps:
            if num:
                self.text(num, 34, SIDE[0][1], (x, y))
            self.text(line, 28, TEXT, (x + int(32 * u), y + int(4 * u)))
            y += int(34 * u)
        ready = self.ready()
        blink = int(pygame.time.get_ticks() / 600) % 2 == 0
        msg, color = ("press SPACE to start", QUALITY["good"]) if ready else ("waiting for both headsets...", QUALITY["warn"])
        if blink or not ready:
            self.text(msg, 30, color, (card.centerx, card.bottom - int(20 * u)), "midbottom")

    def draw_big(self, rect: pygame.Rect, s: str, color) -> None:
        surf = self.fonts(170).render(s, True, color)
        shadow = self.fonts(170).render(s, True, BG)
        pos = surf.get_rect(center=rect.center)
        self.screen.blit(shadow, pos.move(4, 4))
        self.screen.blit(surf, pos)

    def draw_banner(self, rect: pygame.Rect, title: str, sub: str, color) -> None:
        card = self.card(rect, 0.42, 0.36)
        self.text(title, 56, color, (card.centerx, card.centery - int(4 * self.fonts.scale)), "midbottom")
        self.text(sub, 28, TEXT, (card.centerx, card.centery + int(8 * self.fonts.scale)), "midtop")

    def draw_over(self, rect: pygame.Rect) -> None:
        u = self.fonts.scale
        winner = self.match.winner
        accent = dict(SIDE)[winner]
        card = self.card(rect, 0.60, 0.86)
        self.text(f"{winner} WINS", 72, accent, (card.centerx, card.y + int(14 * u)), "midtop")
        tr = np.array(self.match.trace)
        t = self.match.t
        left_share = float((tr[:, 2] > tr[:, 3]).mean()) if len(tr) else 0.5
        share = left_share if winner == "LEFT" else 1 - left_share
        self.text(
            f"more relaxed for {share:.0%} of the match   ·   {int(t // 60)}:{int(t % 60):02d}",
            24, TEXT, (card.centerx, card.y + int(72 * u)), "midtop",
        )
        graph = pygame.Rect(card.x + int(24 * u), card.y + int(104 * u), card.w - int(48 * u), card.h - int(150 * u))
        self.draw_replay(graph, tr)
        self.text("SPACE rematch   ·   R lobby", 24, SUBTEXT, (card.centerx, card.bottom - int(12 * u)), "midbottom")

    def draw_replay(self, rect: pygame.Rect, tr: np.ndarray) -> None:
        """Both players' calm over the match; the gap between the lines is coloured by who led."""
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=8)
        self.text("calm over the match", 18, SUBTEXT, (rect.x + 8, rect.y + 6))
        if len(tr) < 2:
            return
        t, a_left, a_right = tr[:, 0], tr[:, 2], tr[:, 3]
        top = max(a_left.max(), a_right.max()) * 1.15
        inner = rect.inflate(-16, -28).move(0, 8)
        xs = inner.x + inner.w * (t - t[0]) / max(t[-1] - t[0], 1e-6)

        def ys(a):
            return inner.bottom - inner.h * a / top

        y_left, y_right = ys(a_left), ys(a_right)
        fill = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        for i in range(len(t) - 1):
            color = SIDE[0][1] if a_left[i] > a_right[i] else SIDE[1][1]
            poly = [(xs[i], y_left[i]), (xs[i + 1], y_left[i + 1]), (xs[i + 1], y_right[i + 1]), (xs[i], y_right[i])]
            pygame.draw.polygon(fill, (*color, 70), poly)
        self.screen.blit(fill, (0, 0))
        for (_, color), y in zip(SIDE, (y_left, y_right)):
            pygame.draw.lines(self.screen, color, False, np.column_stack([xs, y]).tolist(), 3)

    def draw_eeg(self, rect: pygame.Rect, side: int) -> None:
        u = self.fonts.scale
        name, accent = SIDE[side]
        headset = self.manager.players()[side]
        sig = self.player_signals()[side]
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=int(10 * u))
        pygame.draw.rect(self.screen, accent, (rect.x, rect.y, rect.w, max(3, int(4 * u))),
                         border_top_left_radius=int(10 * u), border_top_right_radius=int(10 * u))
        x, y = rect.x + int(14 * u), rect.y + int(10 * u)
        self.text(f"{name} brain", 28, accent, (x, y))
        if headset is None:
            self.text("no headset", 24, SUBTEXT, rect.center, "center")
            return
        status = headset.status
        self.text(f"{status}  {headset.rate:.0f} Hz", 22, STATUS[status], (rect.right - int(14 * u), y + 2), "topright")
        self.text("live EEG · 1-40 Hz · 60 Hz notch", 18, SUBTEXT, (x + int(130 * u), y + int(5 * u)))
        traces = pygame.Rect(rect.x + int(8 * u), y + int(28 * u), rect.w - int(16 * u), rect.bottom - y - int(36 * u))
        data = None
        if headset.has(0.5):
            data, _ = headset.latest(TRACE_SECONDS, headset.eeg_labels, filtered=True)
        quality = sig.quality if sig else []
        draw_traces(self.screen, traces, data, headset.eeg_labels, quality, accent, self.scale_uv,
                    int(TRACE_SECONDS * headset.srate), self.fonts)


def start_simulator(replay: str) -> None:
    cmd = [sys.executable, "-m", "mindball.sim"] + (["--replay", replay] if replay else [])
    proc = subprocess.Popen(cmd)
    atexit.register(proc.terminate)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--sim", nargs="?", const="", metavar="XDF",
                        help="start two simulated headsets (optionally replaying an XDF)")
    parser.add_argument("--autostart", action="store_true", help="start a match as soon as both signals are good")
    parser.add_argument("--seconds", type=float, help="quit after this many seconds")
    parser.add_argument("--screenshot", metavar="PNG", help="save the last frame on exit")
    args = parser.parse_args()
    cfg = config.load()
    if args.sim is not None:
        start_simulator(args.sim)
        cfg["streams"]["prefix"] = "X.on-SIM"
        cfg["players"] = {"left": "", "right": ""}
    Game(cfg, args.fullscreen, args.autostart).run(args.seconds, args.screenshot)


if __name__ == "__main__":
    main()
