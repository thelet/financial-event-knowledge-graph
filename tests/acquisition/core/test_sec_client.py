"""Retry policy and rate limiting, with a mock transport and a fake clock.

No test here sleeps or touches the network.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest

from acquisition.core.sec_client import (
    PermanentHttpError,
    RateLimiter,
    RetriesExhausted,
    SecClient,
)

URL = "https://www.sec.gov/Archives/edgar/data/1801169/x/open.htm"


def _client(http_config, clock, handler) -> SecClient:
    transport = httpx.MockTransport(handler)
    return SecClient(
        http_config,
        clock=clock,
        client=httpx.Client(transport=transport, headers={"User-Agent": "test t@e.com"}),
        jitter=lambda: 0.0,
    )


# -- Success -----------------------------------------------------------------------------


def test_get_bytes_returns_content(http_config, fake_clock):
    client = _client(http_config, fake_clock, lambda r: httpx.Response(200, content=b"hello"))
    result = client.get_bytes(URL)
    assert result.content == b"hello"
    assert result.status == 200
    assert result.attempts == 1


def test_user_agent_is_sent(http_config, fake_clock):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("User-Agent")
        return httpx.Response(200, content=b"ok")

    _client(http_config, fake_clock, handler).get_bytes(URL)
    assert "@" in seen["ua"]


def test_download_to_hashes_and_sizes(http_config, fake_clock, tmp_path):
    payload = b"x" * 5000
    client = _client(http_config, fake_clock, lambda r: httpx.Response(200, content=payload))
    outcome = client.download_to(URL, tmp_path / "out.htm")

    assert outcome.size_bytes == 5000
    assert outcome.sha256 == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / "out.htm").read_bytes() == payload


def test_download_preserves_bytes_exactly(http_config, fake_clock, tmp_path):
    payload = bytes(range(256)) * 40
    client = _client(http_config, fake_clock, lambda r: httpx.Response(200, content=payload))
    client.download_to(URL, tmp_path / "bin.dat")
    assert (tmp_path / "bin.dat").read_bytes() == payload


def test_response_metadata_is_captured(http_config, fake_clock, tmp_path):
    headers = {"ETag": '"abc123"', "Last-Modified": "Thu, 19 Feb 2026 16:22:53 GMT"}
    client = _client(
        http_config, fake_clock, lambda r: httpx.Response(200, content=b"x", headers=headers)
    )
    outcome = client.download_to(URL, tmp_path / "a.htm")
    assert outcome.etag == '"abc123"'
    assert outcome.last_modified.startswith("Thu, 19 Feb 2026")


# -- Retry policy ------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_statuses_are_retried(http_config, fake_clock, status):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(status)
        return httpx.Response(200, content=b"recovered")

    result = _client(http_config, fake_clock, handler).get_bytes(URL)
    assert result.content == b"recovered"
    assert result.attempts == 3
    assert len(fake_clock.sleeps) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 451])
def test_permanent_statuses_are_not_retried(http_config, fake_clock, status):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(status)

    with pytest.raises(PermanentHttpError):
        _client(http_config, fake_clock, handler).get_bytes(URL)
    assert attempts["n"] == 1
    assert fake_clock.sleeps == []


def test_retries_exhausted_raises(http_config, fake_clock):
    client = _client(http_config, fake_clock, lambda r: httpx.Response(503))
    with pytest.raises(RetriesExhausted):
        client.get_bytes(URL)
    assert len(fake_clock.sleeps) == http_config.max_retries - 1


def test_transport_errors_are_retried(http_config, fake_clock):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, content=b"ok")

    assert _client(http_config, fake_clock, handler).get_bytes(URL).content == b"ok"
    assert attempts["n"] == 2


def test_backoff_is_exponential_and_capped(http_config, fake_clock):
    client = _client(http_config, fake_clock, lambda r: httpx.Response(503))
    with pytest.raises(RetriesExhausted):
        client.get_bytes(URL)
    assert fake_clock.sleeps == [1.0, 2.0]  # base 1.0, doubling, jitter pinned to 0


def test_retry_after_seconds_is_honored(http_config, fake_clock):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, content=b"ok")

    _client(http_config, fake_clock, handler).get_bytes(URL)
    assert fake_clock.sleeps == [7.0]


def test_retry_after_is_capped_at_backoff_max(http_config, fake_clock):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "99999"})

    with pytest.raises(RetriesExhausted):
        _client(http_config, fake_clock, handler).get_bytes(URL)
    assert all(s <= http_config.backoff_max_seconds for s in fake_clock.sleeps)


def test_retry_after_http_date_does_not_crash(http_config, fake_clock):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(
                503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
            )
        return httpx.Response(200, content=b"ok")

    assert _client(http_config, fake_clock, handler).get_bytes(URL).content == b"ok"


# -- Download retry cleanup --------------------------------------------------------------


def test_failed_download_leaves_no_partial_file(http_config, fake_clock, tmp_path):
    destination = tmp_path / "partial.htm"
    client = _client(http_config, fake_clock, lambda r: httpx.Response(503))
    with pytest.raises(RetriesExhausted):
        client.download_to(URL, destination)
    assert not destination.exists()


def test_download_retry_rewrites_from_scratch(http_config, fake_clock, tmp_path):
    """A retried download must not append to the previous attempt's bytes."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ReadError("truncated", request=request)
        return httpx.Response(200, content=b"complete-body")

    destination = tmp_path / "retry.htm"
    outcome = _client(http_config, fake_clock, handler).download_to(URL, destination)
    assert destination.read_bytes() == b"complete-body"
    assert outcome.sha256 == hashlib.sha256(b"complete-body").hexdigest()


def test_permanent_error_during_download_is_not_retried(http_config, fake_clock, tmp_path):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(404)

    with pytest.raises(PermanentHttpError):
        _client(http_config, fake_clock, handler).download_to(URL, tmp_path / "x.htm")
    assert attempts["n"] == 1


# -- Rate limiting -----------------------------------------------------------------------


def test_rate_limiter_spaces_requests(fake_clock):
    limiter = RateLimiter(requests_per_second=5.0, clock=fake_clock)
    for _ in range(4):
        limiter.acquire()
    # First is immediate; each subsequent one waits the 0.2s interval.
    assert fake_clock.sleeps == pytest.approx([0.2, 0.2, 0.2])


def test_rate_limiter_rejects_non_positive_rate(fake_clock):
    with pytest.raises(ValueError):
        RateLimiter(requests_per_second=0, clock=fake_clock)


def test_request_count_is_tracked(http_config, fake_clock):
    client = _client(http_config, fake_clock, lambda r: httpx.Response(200, content=b"x"))
    for _ in range(3):
        client.get_bytes(URL)
    assert client.request_count == 3


def test_retries_count_as_requests(http_config, fake_clock):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503 if attempts["n"] < 3 else 200, content=b"x")

    client = _client(http_config, fake_clock, handler)
    client.get_bytes(URL)
    assert client.request_count == 3
