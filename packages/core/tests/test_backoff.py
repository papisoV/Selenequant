"""Resilience locks for selene_space_core.resilience.backoff — defaults,
exponential growth, delay cap, jitter bounds, custom parameters."""

from selene_space_core.resilience.backoff import BackoffPolicy


def test_factory_defaults():
    policy = BackoffPolicy()
    quartet = (policy.initial, policy.max_delay, policy.factor, policy.jitter)
    assert quartet == (1.0, 60.0, 2.0, True)


def test_doubling_ladder_without_jitter():
    ladder = BackoffPolicy(initial=1.0, factor=2.0, jitter=False)
    expected = {0: 1.0, 1: 2.0, 2: 4.0, 3: 8.0}
    for attempt, delay in expected.items():
        assert ladder.calculate_delay(attempt) == delay


def test_delay_never_exceeds_max():
    capped = BackoffPolicy(initial=1.0, factor=2.0, max_delay=5.0, jitter=False)
    assert capped.calculate_delay(10) == 5.0


def test_jitter_stays_within_quarter_band():
    wobbly = BackoffPolicy(initial=10.0, factor=1.0, jitter=True)
    for _ in range(50):
        delay = wobbly.calculate_delay(0)
        # 10 * [0.75, 1.25]
        assert 7.5 <= delay <= 12.5


def test_custom_growth_factor_applies():
    tripled = BackoffPolicy(initial=0.5, max_delay=30.0, factor=3.0, jitter=False)
    series = [tripled.calculate_delay(n) for n in range(3)]
    assert series == [0.5, 1.5, 4.5]
