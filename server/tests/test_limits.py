"""Rate limits and request sizing.

The limiter is the only thing standing between a public demo URL and an unbounded
clone-and-model bill, so the numbers have to be real, and they have to be
readable by whoever is tuning them: a limit string a reader cannot copy from
``/health`` into the environment is a limit that silently stays at its default.
"""

from __future__ import annotations

from app.core.limits import DEFAULT_LIMITS, RateLimiter, parse_limit


def test_a_plain_count_over_window_is_read():
    assert parse_limit("5/60", (1, 1)) == (5, 60)


def test_the_format_health_prints_is_accepted():
    """describe() writes "12/3600s". Pasting that back must not be ignored."""
    assert parse_limit("12/3600s", (1, 1)) == (12, 3600)
    assert parse_limit("40/1h", (1, 1)) == (40, 3600)
    assert parse_limit("5/2m", (1, 1)) == (5, 120)


def test_whitespace_and_case_are_tolerated():
    assert parse_limit(" 7 / 90 S ", (1, 1)) == (7, 90)


def test_anything_malformed_falls_back_instead_of_raising():
    for bad in ("", "abc", "5/", "/60", "5", "-5/60", "5/0", "0/60", "5;60", "5/60/60", "5/60s5"):
        assert parse_limit(bad, (7, 7)) == (7, 7), bad


def test_absent_configuration_keeps_the_default():
    assert parse_limit(None, DEFAULT_LIMITS["analyze"]) == DEFAULT_LIMITS["analyze"]


def test_a_bucket_allows_exactly_its_capacity_then_refuses():
    limiter = RateLimiter(3, 3600)

    assert [limiter.allow("client")[0] for _ in range(4)] == [True, True, True, False]


def test_buckets_are_per_client():
    limiter = RateLimiter(1, 3600)

    assert limiter.allow("a")[0] is True
    assert limiter.allow("b")[0] is True
    assert limiter.allow("a")[0] is False


def test_a_refused_caller_is_told_how_long_to_wait():
    limiter = RateLimiter(1, 3600)
    limiter.allow("client")

    allowed, retry_after = limiter.allow("client")

    assert allowed is False
    assert 0 < retry_after <= 3600


def test_a_bucket_refills_over_time():
    limiter = RateLimiter(2, 10)
    limiter.allow("client")
    limiter.allow("client")
    assert limiter.allow("client")[0] is False

    # Backdate the bucket as if half its window had passed.
    limiter._buckets["client"].updated -= 5

    assert limiter.allow("client")[0] is True


def test_every_endpoint_has_a_limit():
    assert set(DEFAULT_LIMITS) == {"analyze", "chat", "fix", "file"}
    assert all(count > 0 and window > 0 for count, window in DEFAULT_LIMITS.values())