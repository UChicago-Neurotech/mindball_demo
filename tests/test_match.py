import numpy as np

from mindball.match import Match

DT = 0.1


def play(a_left, a_right, seconds=600):
    m = Match()
    for _ in range(int(seconds / DT)):
        m.step(DT, a_left(), a_right())
        if m.winner:
            break
    return m


def smoothed(mean, seed, tau=2.0):
    """Noisy alpha through the same EMA the game uses (slow drift, like real data)."""
    rng, k, state = np.random.default_rng(seed), 1 - np.exp(-DT / tau), [mean]

    def next_value():
        state[0] += k * (mean * (1 + 0.25 * rng.normal()) - state[0])
        return state[0]

    return next_value


def test_calmer_player_wins_in_reasonable_time():
    m = play(smoothed(0.30, 0), smoothed(0.12, 1))  # eyes closed vs eyes open
    assert m.winner == "LEFT" and 8 < m.t < 25


def test_even_match_still_ends():
    lengths = [play(smoothed(0.12, 2 * s), smoothed(0.12, 2 * s + 1)).t for s in range(10)]
    assert max(lengths) < 45


def test_ball_ends_fully_in_goal():
    m = play(lambda: 0.1, lambda: 0.4)
    assert m.winner == "RIGHT" and m.x == -1.0
