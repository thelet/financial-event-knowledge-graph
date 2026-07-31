"""Rate-limited, retrying HTTP client for SEC endpoints.

SEC requires a User-Agent naming the application with a contact email, and caps requests at
roughly 10/s; exceeding it results in IP-level blocking. This client is deliberately
conservative.

The retry policy is hand-rolled rather than delegated to a library (v0 plan section 13.6):
it is small, it must honor Retry-After, and the clock must be injectable so tests are
deterministic and instant.
"""

from __future__ import annotations

import email.utils
import hashlib
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from .config import HttpConfig

# Transient. Everything else in 4xx is permanent and must not be retried blindly.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class HttpError(RuntimeError):
    def __init__(self, url: str, status: int | None, detail: str) -> None:
        super().__init__(f"{detail} (url={url}, status={status})")
        self.url = url
        self.status = status
        self.detail = detail


class PermanentHttpError(HttpError):
    """A non-retryable response, such as 403 or 404."""


class RetriesExhausted(HttpError):
    """The request remained transiently unavailable after every attempt."""


class RateLimiter:
    """Serializes requests to a maximum average rate across threads."""

    def __init__(self, requests_per_second: float, clock: Clock) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self._min_interval = 1.0 / requests_per_second
        self._clock = clock
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = self._clock.monotonic()
            wait = self._next_allowed - now
            start = now + wait if wait > 0 else now
            self._next_allowed = start + self._min_interval
        if wait > 0:
            self._clock.sleep(wait)


@dataclass
class FetchResult:
    url: str
    status: int
    content: bytes
    headers: dict[str, str] = field(default_factory=dict)
    attempts: int = 1


@dataclass
class DownloadOutcome:
    url: str
    status: int
    path: Path
    size_bytes: int
    sha256: str
    etag: str | None = None
    last_modified: str | None = None
    attempts: int = 1


class SecClient:
    """Single HTTP client shared by all stages.

    httpx.Client is safe for concurrent use, and the rate limiter is lock-guarded, so one
    instance may be shared across a thread pool.
    """

    def __init__(
        self,
        http_config: HttpConfig,
        *,
        clock: Clock | None = None,
        client: httpx.Client | None = None,
        jitter: "callable[[], float] | None" = None,
    ) -> None:
        self._config = http_config
        self._clock = clock or RealClock()
        self._limiter = RateLimiter(http_config.requests_per_second, self._clock)
        self._jitter = jitter if jitter is not None else (lambda: random.uniform(0, 0.25))
        self._owns_client = client is None
        self._client = client or httpx.Client(
            headers={
                "User-Agent": http_config.user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=http_config.timeout_seconds,
            follow_redirects=True,
        )
        self._counter_lock = threading.Lock()
        self._request_count = 0

    # -- lifecycle ---------------------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "SecClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def request_count(self) -> int:
        with self._counter_lock:
            return self._request_count

    def _count_request(self) -> None:
        with self._counter_lock:
            self._request_count += 1

    # -- retry policy ------------------------------------------------------------------

    def _backoff_seconds(self, attempt: int, retry_after: str | None) -> float:
        parsed = _parse_retry_after(retry_after)
        if parsed is not None:
            return min(parsed, self._config.backoff_max_seconds)
        delay = self._config.backoff_base_seconds * (2 ** (attempt - 1))
        return min(delay, self._config.backoff_max_seconds) + self._jitter()

    # -- requests ----------------------------------------------------------------------

    def get_bytes(self, url: str) -> FetchResult:
        """Fetch a whole response into memory. For small metadata documents."""
        last_detail = "no attempt made"
        last_status: int | None = None

        for attempt in range(1, self._config.max_retries + 1):
            self._limiter.acquire()
            self._count_request()
            try:
                response = self._client.get(url)
            except httpx.HTTPError as exc:
                last_detail, last_status = f"transport error: {exc}", None
            else:
                if response.status_code == 200:
                    return FetchResult(
                        url=url,
                        status=response.status_code,
                        content=response.content,
                        headers=dict(response.headers),
                        attempts=attempt,
                    )
                last_status = response.status_code
                last_detail = f"HTTP {response.status_code}"
                if response.status_code not in RETRYABLE_STATUSES:
                    raise PermanentHttpError(url, response.status_code, last_detail)
                if attempt < self._config.max_retries:
                    self._clock.sleep(
                        self._backoff_seconds(attempt, response.headers.get("Retry-After"))
                    )
                continue

            if attempt < self._config.max_retries:
                self._clock.sleep(self._backoff_seconds(attempt, None))

        raise RetriesExhausted(url, last_status, f"retries exhausted: {last_detail}")

    def download_to(self, url: str, destination: Path) -> DownloadOutcome:
        """Stream a response to disk, hashing as it goes.

        Streaming keeps multi-megabyte primary documents out of memory. The destination is
        rewritten from scratch on every attempt so a failed partial write cannot survive.
        """
        last_detail = "no attempt made"
        last_status: int | None = None
        destination.parent.mkdir(parents=True, exist_ok=True)

        for attempt in range(1, self._config.max_retries + 1):
            self._limiter.acquire()
            self._count_request()
            try:
                with self._client.stream("GET", url) as response:
                    if response.status_code != 200:
                        response.close()
                        last_status = response.status_code
                        last_detail = f"HTTP {response.status_code}"
                        if response.status_code not in RETRYABLE_STATUSES:
                            raise PermanentHttpError(url, response.status_code, last_detail)
                        if attempt < self._config.max_retries:
                            self._clock.sleep(
                                self._backoff_seconds(
                                    attempt, response.headers.get("Retry-After")
                                )
                            )
                        continue

                    digest = hashlib.sha256()
                    size = 0
                    with destination.open("wb") as handle:
                        for chunk in response.iter_bytes(65536):
                            digest.update(chunk)
                            size += len(chunk)
                            handle.write(chunk)

                    return DownloadOutcome(
                        url=url,
                        status=response.status_code,
                        path=destination,
                        size_bytes=size,
                        sha256=digest.hexdigest(),
                        etag=response.headers.get("ETag"),
                        last_modified=response.headers.get("Last-Modified"),
                        attempts=attempt,
                    )
            except httpx.HTTPError as exc:
                last_detail, last_status = f"transport error: {exc}", None
                destination.unlink(missing_ok=True)
                if attempt < self._config.max_retries:
                    self._clock.sleep(self._backoff_seconds(attempt, None))

        destination.unlink(missing_ok=True)
        raise RetriesExhausted(url, last_status, f"retries exhausted: {last_detail}")


def _parse_retry_after(value: str | None) -> float | None:
    """Retry-After is either delay-seconds or an HTTP-date."""
    if not value:
        return None
    text = value.strip()
    if text.isdigit():
        return float(text)
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    delta = when.timestamp() - time.time()
    return max(0.0, delta)
