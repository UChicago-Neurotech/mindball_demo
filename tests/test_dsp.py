import numpy as np

from mindball import dsp
from mindball.sim import SyntheticHeadset

FS = 250.0


def noise(seconds=2.0, channels=3, sd=10.0, seed=0):
    return np.random.default_rng(seed).normal(size=(int(FS * seconds), channels)) * sd


def test_alpha_raises_relative_alpha():
    x = noise()
    t = np.arange(len(x)) / FS
    with_alpha = x + 20.0 * np.sin(2 * np.pi * 10.0 * t)[:, None]
    assert dsp.relative_alpha(with_alpha, FS).mean() > 2 * dsp.relative_alpha(x, FS).mean()


def test_muscle_noise_lowers_relative_alpha():
    t = np.arange(int(FS * 2)) / FS
    alpha = 20.0 * np.sin(2 * np.pi * 10.0 * t)[:, None] + noise(sd=5.0)
    emg = alpha + noise(sd=30.0, seed=1)  # broadband, mostly outside alpha
    assert dsp.relative_alpha(emg, FS).mean() < dsp.relative_alpha(alpha, FS).mean()


def test_channel_quality_flags_flat_and_noisy():
    x = noise(channels=3)
    x[:, 1] = 0.0
    x[:, 2] *= 30.0
    levels = [level for level, _ in dsp.channel_quality(x, FS)]
    assert levels == ["good", "bad", "bad"]


def test_synthetic_relaxed_player_has_more_alpha():
    sim = SyntheticHeadset(seed=0, fs=FS)
    sim.relax = 1.0
    relaxed = sim.chunk(int(FS * 2))[:, 5:7]
    sim.relax = 0.0
    tense = sim.chunk(int(FS * 2))[:, 5:7]
    assert dsp.relative_alpha(relaxed, FS).mean() > dsp.relative_alpha(tense, FS).mean()


def test_live_filter_notches_60hz_and_keeps_alpha():
    t = np.arange(int(FS * 10)) / FS
    f = dsp.LiveFilter(FS)
    y = f(np.column_stack([np.sin(2 * np.pi * 10 * t), np.sin(2 * np.pi * 60 * t)]))[int(FS * 2):]
    assert y[:, 0].std() > 0.6 and y[:, 1].std() < 0.05


def test_live_filter_chunked_matches_one_shot():
    x = noise(seconds=4.0)
    whole = dsp.LiveFilter(FS)(x)
    f = dsp.LiveFilter(FS)
    chunks = np.vstack([f(x[i:i + 37]) for i in range(0, len(x), 37)])
    assert np.allclose(whole, chunks)
