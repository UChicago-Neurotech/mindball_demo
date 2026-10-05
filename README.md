# Mindball demo

Two-player "who's more relaxed" EEG game (à la the Mindball table at MSI) for Brain Products
**X.on** headsets. Background, design notes, and what we learned from the MSI exhibit: [RESEARCH.md](RESEARCH.md).

**Status:** playable. Both headsets → LSL → live-filtered EEG → relaxation score → ball.
Tested end to end with simulated headsets; not yet with two real X.on headsets.

```bat
python -m mindball.game --fullscreen    # the game (real headsets)
python -m mindball.game --sim           # the game with two fake headsets, any OS
```

## Run it on the Windows machine (real headsets)

The X.on software is Android + Windows 11 only, so the demo runs on the Windows PC the headsets
connect to.

1. **Python 3.12** from python.org (tick "Add python.exe to PATH").
2. Install:
   ```bat
   git clone https://github.com/UChicago-Neurotech/mindball_demo.git
   cd mindball_demo
   py -3.12 -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. **Headsets:** wet the sponges, put them on, clip the ear clip on. Connect each headset in the
   **X.on App for PC**, check impedances, set **250 Hz**, and start LSL streaming.
4. Check that both streams are visible:
   ```bat
   python -m mindball.list_streams
   ```
   You should see two streams named like `X.on-102801-0065` with labels `F3 F4 C3 Cz C4 P3 P4 BIP accX accY accZ`.
5. Live monitor:
   ```bat
   python -m mindball.monitor
   ```
   **Sanity check:** close your eyes for ~5 s. You should see ~10 Hz waves on P3/P4 and that player's
   alpha number should jump. Clench your jaw and it should drop.
6. Pin each headset to a side in `config.toml` (`left = "X.on-…"`) so they never swap. Press `S` in
   the monitor to swap on the fly.
7. Play:
   ```bat
   python -m mindball.game --fullscreen
   ```
   Keys: `SPACE` start/rematch · `R` lobby · `S` swap sides · `D` show numbers · `+/-` EEG scale ·
   `F` fullscreen · `Esc` quit.

## How the game decides who's more relaxed

No training and no baseline. Every 0.1 s, for each player:

1. EEG is filtered live as it arrives (1-40 Hz bandpass + 60 Hz notch, causal).
2. Take the last 2 s of P3, P4, Cz and drop channels flagged flat or noisy.
3. **Relative alpha** = 8-13 Hz power ÷ 2-40 Hz power, averaged. Eyes closed / relaxed → up;
   tense, jaw clench, talking (muscle noise) → down. Head moving (accelerometer) → forced low.
4. Smoothed over ~2 s.

The ball is pushed by `tanh(log(αL/αR) × ramp / 0.3)`, with momentum. The match ends only when
the ball is fully in a goal. Speed and sensitivity ramp up over time, so a clear win takes ~15 s and a
dead-even match ~30 s. Tune it in `config.toml` → `[game]`. If a player's signal is unusable for
more than 1.5 s, the match pauses with "SIGNAL CHECK".

The game also publishes `Mindball-Markers` (start/pause/win events) and `Mindball-Game` (ball
position, both alphas) on LSL, so LabRecorder can save them next to the EEG.

### If the PC app can only run one headset at a time

Not verified yet, so **test this first**. If one instance of the app can't hold two headsets, try a
second instance. If that fails too, stream headset #2 from an **Android phone** (X.on app, Android 10+):

- Turn on **Windows Mobile Hotspot** (Settings → Network → Mobile hotspot, 5 GHz if offered) and
  join it from the phone. Don't use campus WiFi; it usually blocks LSL discovery.
- The first time Python opens LSL, **allow it through Windows Firewall**. Without that, network
  streams are invisible (localhost streams still work).
- Still not showing up? Create `lsl_api.cfg` in the repo folder with the phone's IP:
  ```ini
  [lab]
  KnownPeers = {192.168.137.2}
  ```
- Keep each phone right next to its headset, and turn off other Bluetooth devices nearby.
  "Amplifier Overflow" in the X.on app means Bluetooth congestion.

The code doesn't care where a stream comes from. Inlets use LSL clock sync, so phone and PC
streams all land on the PC's clock. The monitor shows the sync offset between the two headsets.

### Recording sessions

Run **LabRecorder** alongside the monitor to save both X.on streams into one XDF. Any recording
can be replayed later as fake headsets (below), which helps for tuning the game.

## Develop without headsets (any OS, incl. Mac)

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # Windows: see above
pip install -r requirements.txt

python -m mindball.sim                      # terminal 1: two fake X.on headsets on LSL
python -m mindball.sim --replay rec.xdf     #   ...or replay a real recording (looped)
python -m mindball.monitor                  # terminal 2
python -m mindball.game --sim               # or: game + fake headsets in one command
pytest                                      # tests
```

Fake headsets are named `X.on-SIM-0001/0002` and have the same channels and metadata as the real
ones. Synthetic players drift between tense and relaxed on their own. A real X.on recording from
last year is in the [balloon_control_game](https://github.com/UChicago-Neurotech/balloon_control_game) repo.

## Code

| file | what |
|---|---|
| `mindball/game.py` | the game: ball table + both players' live EEG on one screen (pygame) |
| `mindball/match.py` | ball physics: push, momentum, speed ramp, win condition |
| `mindball/player.py` | per-player score: quality, movement, smoothed relative alpha |
| `mindball/streams.py` | find X.on streams, inlets with clock sync, live filter, ring buffers, health, LEFT/RIGHT |
| `mindball/dsp.py` | live filter, relative alpha, channel quality, movement |
| `mindball/monitor.py` | live two-player signal check (pygame) |
| `mindball/ui.py` | shared colours and EEG trace drawing |
| `mindball/sim.py` | fake headsets: synthetic or XDF replay |
| `mindball/list_streams.py` | print every visible LSL stream |
| `config.toml` | headset → side mapping, score channels, game speed |
| `docs/transcript.md` | the Claude Code conversation that built this |

## Troubleshooting

| symptom | fix |
|---|---|
| `list_streams` finds nothing | Is streaming started in the X.on app? Phone: same network as PC + firewall allowed |
| status `NO DATA` | Headset out of range / app stopped. It reconnects on its own when the stream comes back |
| status `SLOW` | Bluetooth congestion: move the phone/PC closer, fewer BT devices, 250 Hz not 500 |
| red `flat` dot | Electrode not touching. Re-wet the sponge, push through hair |
| red `noisy` dot | Bad contact or movement. Re-wet, check the ear clip |
| orange `60 Hz` | Line noise. Move away from chargers/power strips, check the ear clip |
