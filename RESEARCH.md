# Mindball clone — preliminary research

Goal: a two-player "who's more relaxed" EEG game (like Mindball at MSI) for a neurotech club info session.
Constraints: Brain Products X.on headset, little to no data collection, has to look good even when signals are bad.

---

## 1. The hardware: Brain Products X.on

| | |
|---|---|
| EEG channels | 7: **F3, F4, C3, Cz, C4, P3, P4** + ear clip (REF/GND) |
| Electrodes | Passive Ag/AgCl with **saline sponges** (60–90 min wear per wetting) |
| Sampling | 125 / 250 / 500 Hz, 16 or 24 bit |
| Extras | Built-in **accelerometer**, 1 AUX bipolar input (EOG/EMG/ECG) |
| Wireless | Bluetooth LE 5, battery ~7 h |
| Software | **Android app (Android 10+)** → LSL over WiFi; **PC app (Windows 11 only)** → LSL. No macOS app. |
| LSL stream | Named like `X.on-102111-0058`, includes a SampleCounter channel; Bluetooth latency already corrected |

What this means for us:
- **Decision: everything runs on the Windows 11 machine.** X.on PC app → LSL on localhost → our Python game on the same machine. Nothing touches the network, so campus WiFi/multicast stops being a problem.
- **Unverified, test this first:** can one X.on PC app (or two instances) stream **two headsets at once** on one Bluetooth adapter? I couldn't find it documented. Brain Products' own multi-headset guide used one Android phone per headset, which hints the PC app may be single-device. Fallbacks, in order:
  1. Headset A on the PC app, headset B on an Android phone (X.on app) streaming LSL to the Windows machine over the **Windows Mobile Hotspot** or a dedicated 5 GHz router. Avoid campus WiFi, which often blocks LSL multicast discovery; use `KnownPeers` in `lsl_api.cfg` if streams don't show up.
  2. Two phones, both streaming to the Windows machine the same way.
- No forehead electrodes, unlike the real Mindball. That's fine, because **P3/P4 sit over parietal cortex, where alpha is much stronger** than at the forehead.
- **250 Hz is plenty**, since we only care about frequencies below 40 Hz. Lower rates also hold up better against Bluetooth congestion with two headsets in a crowded room. Keep each headset near its receiver and turn off other Bluetooth devices nearby. "Amplifier Overflow" in the app means Bluetooth congestion.

## 2. How the real Mindball works

From the 2006 UChicago evaluation of the MSI exhibit (Nicholson, Tsvetkova, Vanderlinde):
- Forehead headband, measures alpha + theta amplitude. The ball rolls **toward whoever has lower alpha/theta**, so it gets pushed away from the more relaxed player.
- It's not a trained classifier, just band power compared between the two players.

Lessons from watching visitors play (these matter as much as the signal processing):
1. **Instructions:** a 3-step card on the table ("Put on headset → Press start → Relax"). Long paragraphs didn't get read.
2. **Display:** label the bars **"MORE RELAXED / LESS RELAXED"**. People couldn't tell whether higher meant more relaxed. Drop any confusing "combined" plot.
3. **Game length:** adult games ran **6+ minutes** and bored everyone. Fix: **slowly ramp the sensitivity** so close games still end within 1–2 min.
4. **Start moment:** the ball sliding back to center looked like someone was losing. Recenter first, then show a clear **"3-2-1 START!"**.
5. **After the match:** players talked more after the game than during it. Show a **full-match replay graph** at the end.
6. **Crowd heckling is part of the fun** ("make your opponent laugh"). Laughing moves the facial muscles, which wrecks the signal, so heckling actually works in our design.
7. **Keep it head-to-head.** The evaluators specifically warned against a separate-balls race format because it kills the social part.

## 3. Signal approach: no classifier, no training data

Use the most dependable effect in EEG instead of training anything: **posterior alpha (8–13 Hz) goes way up when you close your eyes and relax, and drops when you open your eyes, concentrate, or tense up** (the Berger effect). It works for nearly everyone with zero calibration. And tensing up, talking, laughing, or clenching your jaw adds broadband muscle noise (EMG) above ~20 Hz, which pulls relative alpha down. So "stress" gets penalized automatically, exactly the way the game is supposed to work.

Pipeline per player, recomputed every ~250 ms on a 2 s sliding window:
1. Channels **P3, P4, Cz** (optionally C3/C4). **Leave out F3/F4**, which pick up blinks and forehead muscle. They can still serve as artifact detectors.
2. 60 Hz notch, 1–40 Hz bandpass.
3. Welch PSD → **relative alpha = power(8–13) / power(2–40)**, averaged across channels, then log. Relative power cancels out sponge and impedance differences between headsets.
4. Optional, to stay faithful to Mindball: add theta (4–8 Hz).
5. Optional fairness step: a 20 s eyes-open baseline per player at game start, scored as a z-score against that baseline. This offsets "some people just have more alpha".

Being honest about it: this mostly measures *eyes closed + still + face relaxed*. That's basically what Mindball measures too, and it makes a good teaching moment for a neurotech crowd (show the live raw trace, have someone clench their jaw).

The only data collection needed: each team member wears it for ~2 min (1 min eyes open, 1 min eyes closed), recorded with LabRecorder. That's for **tuning thresholds and gains, not training**, and it gives us real recordings to replay during development.

## 4. Making it foolproof

The game logic smooths out bad signal:
- Smooth each score with an EMA (τ ≈ 1–2 s), then `ball_velocity = gain(t) * tanh((sA - sB) / c)` with some inertia. The ball always moves smoothly back and forth, never jitters, and someone always wins.
- `gain(t)` ramps up over time, so games end in roughly 60–120 s.
- A small deadband keeps the ball from twitching when scores are nearly equal.

Artifact handling:
- **Player's fault** (big head movement from the accelerometer, huge amplitude spikes): count it as "stressed". It makes sense to the crowd: "you moved, you lost ground."
- **Not the player's fault** (flatline, no samples for >1 s, railed channel): **pause and show "SIGNAL CHECK"** rather than wrongly punishing them. Auto-resume when it's back.
- If one channel goes bad, drop it and average the rest.

Pre-game check: per-channel green/yellow/red lights based on 60 Hz noise and variance. Don't allow START until they're green. The X.on app also has an impedance check. Re-wet sponges between players.

Fallbacks:
- A **replay source** that streams a recorded session over LSL, used for development without the headset, and live if the hardware dies.
- A keyboard override for testing.
- If either of these is ever used live, **say so out loud**. The people most likely to ask "wait, how does this work?" are exactly the ones you're recruiting, and getting caught faking is worse than a hardware glitch.

## 5. LSL synchronization: what actually matters

The game needs much less sync than it sounds like. Each player's score comes **only from their own stream**, and we compare two 1–2 s-smoothed scores ~4 times a second. A few tens of ms of misalignment between headsets changes nothing visible. If both headsets go through the PC app, they also share one clock.

What matters:
1. **Map stream → player by serial number**, not by discovery order. Streams are named `X.on-<serial>`; resolve by name from a config (`LEFT = X.on-102111-0058`), so the players never get swapped.
2. **Open inlets with `processing_flags=proc_ALL`** (clock sync + dejitter + monotonic), so timestamps from a phone-relayed stream land on the Windows clock too.
3. **Drain both inlets every frame** with non-blocking `pull_chunk()` into per-player ring buffers. A blocking `pull_sample()` on one headset would freeze the whole game.
4. **Per-stream health:** time since last sample, and gaps in the X.on `SampleCounter` channel (dropped samples). Either one triggers the "SIGNAL CHECK" pause.
5. **Publish a marker stream from the game** (`countdown`, `start`, `win_left`, …) and run **LabRecorder** to save both EEG streams + markers into one XDF. This is where LSL sync really earns its keep: every match is recorded and aligned, which gives us the post-match replay graph, data for tuning, and recordings to replay in dev mode.

## 6. Suggested stack

- Runs on the Windows 11 machine. Python: `pylsl` (Windows wheels bundle liblsl) + `numpy`/`scipy` for signal processing, **`pygame`** for game + live graphs. One process, few moving parts.
- Team members can still develop on Macs (or anywhere) against a **fake LSL source** that publishes two `X.on-SIM-*` streams from synthetic alpha or replayed XDF recordings. Only the real headset connection has to happen on Windows.

## 7. Prior art to borrow from

- [ethan-carlson/mindball](https://github.com/ethan-carlson/mindball): homebrew Mindball (NeuroSky)
- [Neural-Dynamics-Lab-VT/Two_Player_Pong](https://github.com/Neural-Dynamics-Lab-VT/Two_Player_Pong): two-player alpha game over LSL
- [PolyCortex/MindPong](https://github.com/PolyCortex/MindPong): two Muse headsets, physical ball with fans
- [sye8/MindGame](https://github.com/sye8/MindGame): two-player tug-of-war, BITalino
- [muse-lsl neurofeedback.py](https://github.com/alexandrebarachant/muse-lsl/blob/master/examples/neurofeedback.py): clean band-power-from-LSL example

## Sources

- [X.on specs (xon-eeg.com)](https://xon-eeg.com/), [X.on set contents](https://shop.brainproducts.com/product/xon-set/), [MindTecStore listing](https://www.mindtecstore.com/Brain-Products-Xon-EEG-Headset-7-Channel)
- [Hyperscanning with 10 X.ons via LSL (BCI+)](https://bci.plus/hyperscanning-xon/), [Brain Products LSL tips](https://www.brainproducts.com/support-resources/tips-and-tricks-for-lsl/)
- [MindBall Evaluation for MSI, UChicago 2006 (PDF)](https://mps.uchicago.edu/docs/2006-1/evaluations/Milena-Keith-Roscoe-MindBall-Eval.pdf), [Mindball (Wikipedia)](https://en.wikipedia.org/wiki/Mindball)
- [pylsl](https://github.com/labstreaminglayer/pylsl), [LSL homebrew tap](https://github.com/labstreaminglayer/homebrew-tap)
