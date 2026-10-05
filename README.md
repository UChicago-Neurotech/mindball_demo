# Mindball demo

Two-player "who's more relaxed" EEG game (à la the Mindball table at MSI) for Brain Products
**X.on** headsets. Background, design notes, and what we learned from the MSI exhibit: [RESEARCH.md](RESEARCH.md).

**Status:** acquisition is done. Both headsets → LSL → one live monitor (traces, per-channel
quality, relative alpha, stream health, sync). The game itself comes next.

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
pytest                                      # tests
```

Fake headsets are named `X.on-SIM-0001/0002` and have the same channels and metadata as the real
ones. Synthetic players drift between tense and relaxed on their own. A real X.on recording from
last year is in the [balloon_control_game](https://github.com/UChicago-Neurotech/balloon_control_game) repo.

## Code

| file | what |
|---|---|
| `mindball/streams.py` | find X.on streams, inlets with clock sync, ring buffers, health, LEFT/RIGHT assignment |
| `mindball/dsp.py` | relative alpha, channel quality, movement, display filter |
| `mindball/monitor.py` | live two-player signal check (pygame) |
| `mindball/sim.py` | fake headsets: synthetic or XDF replay |
| `mindball/list_streams.py` | print every visible LSL stream |
| `config.toml` | headset → side mapping, score channels |

## Troubleshooting

| symptom | fix |
|---|---|
| `list_streams` finds nothing | Is streaming started in the X.on app? Phone: same network as PC + firewall allowed |
| status `NO DATA` | Headset out of range / app stopped. It reconnects on its own when the stream comes back |
| status `SLOW` | Bluetooth congestion: move the phone/PC closer, fewer BT devices, 250 Hz not 500 |
| red `flat` dot | Electrode not touching. Re-wet the sponge, push through hair |
| red `noisy` dot | Bad contact or movement. Re-wet, check the ear clip |
| orange `60 Hz` | Line noise. Move away from chargers/power strips, check the ear clip |
