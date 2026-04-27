"""
pipeline/resilience.py — Error handling, retry logic, and graceful degradation for API calls.

Provides:
  - RetryConfig: configurable retry parameters
  - RetryableError, PermanentError: exception hierarchy for API failures
  - retry_with_backoff(): decorator for automatic retry logic with exponential backoff
  - CircuitBreaker: prevents cascading failures by stopping repeated calls to failing services
  - FailedOperation: logs failed API calls for manual retry/recovery
  - FailureLog: persists failed operations to disk for auditing

All API calls should be wrapped in retry_with_backoff() and caught in a try-except
that distinguishes between PermanentError (fail immediately) and temporary failures.
"""

import json
import time
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps
from typing import Callable, Any, TypeVar, Optional
import traceback


# ── Setup logging ──────────────────────────────────────────────────────────────

logger = logging.getLogger("podcast_pipeline")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ── Exception hierarchy ────────────────────────────────────────────────────────

class PipelineError(Exception):
    """Base exception for pipeline errors."""
    pass


class RetryableError(PipelineError):
    """
    Temporary error that should be retried (timeouts, rate limits, 5xx errors).
    Wrapped around the original exception, preserving the full traceback.
    """
    def __init__(self, message: str, original_error: Exception, attempt: int = 1):
        self.message = message
        self.original_error = original_error
        self.attempt = attempt
        super().__init__(message)


class PermanentError(PipelineError):
    """
    Non-recoverable error (404, 401, invalid input). Should not retry.
    """
    pass


class CircuitBreakerOpen(PipelineError):
    """Raised when a circuit breaker is in OPEN state (service is down)."""
    pass


# ── Configuration ──────────────────────────────────────────────────────────────

@dataclass
class RetryConfig:
    """
    Configurable retry parameters.

    Args:
        max_attempts: total number of attempts (default: 5)
        initial_delay_sec: delay for first retry in seconds (default: 2)
        max_delay_sec: ceiling for exponential backoff (default: 120)
        backoff_factor: multiply delay by this each retry (default: 2.0)
        retry_on_status_codes: HTTP status codes to treat as retryable (default: {408, 429, 500, 502, 503, 504})
        timeout_sec: request timeout in seconds (default: 30)
    """
    max_attempts: int = 5
    initial_delay_sec: float = 2
    max_delay_sec: float = 120
    backoff_factor: float = 2.0
    retry_on_status_codes: set = None
    timeout_sec: float = 30

    def __post_init__(self):
        if self.retry_on_status_codes is None:
            self.retry_on_status_codes = {408, 429, 500, 502, 503, 504}


# Default config for most API calls
DEFAULT_RETRY_CONFIG = RetryConfig()

# Aggressive retries for unstable services (archive.org, buzzsprout)
ARCHIVE_RETRY_CONFIG = RetryConfig(
    max_attempts=8,
    initial_delay_sec=3,
    max_delay_sec=180,
    backoff_factor=2.0,
)

BUZZSPROUT_RETRY_CONFIG = RetryConfig(
    max_attempts=6,
    initial_delay_sec=2,
    max_delay_sec=120,
    backoff_factor=2.0,
)


# ── Retry decorator ────────────────────────────────────────────────────────────

T = TypeVar('T')


def retry_with_backoff(config: RetryConfig = DEFAULT_RETRY_CONFIG) -> Callable:
    """
    Decorator for automatic retry logic with exponential backoff.

    Catches RetryableError and PermanentError. Retries only RetryableError
    up to config.max_attempts. PermanentError is re-raised immediately.

    Usage:
        @retry_with_backoff(ARCHIVE_RETRY_CONFIG)
        def upload_to_archive(...):
            ...

    The decorated function should raise:
      - RetryableError(msg, original_error): for temporary failures
      - PermanentError(msg): for unrecoverable errors
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            delay = config.initial_delay_sec
            last_error = None

            for attempt in range(1, config.max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except PermanentError as e:
                    # No retry for permanent errors
                    logger.error(f"{func.__name__} failed permanently: {e}")
                    raise
                except RetryableError as e:
                    last_error = e
                    if attempt >= config.max_attempts:
                        logger.error(
                            f"{func.__name__} failed after {attempt} attempts: {e.message}"
                        )
                        raise
                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{config.max_attempts} failed: "
                        f"{e.message}. Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    delay = min(delay * config.backoff_factor, config.max_delay_sec)

            # Should never reach here, but just in case
            raise last_error or PipelineError(f"{func.__name__} failed for unknown reason")

        return wrapper
    return decorator


# ── Circuit Breaker ───────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Prevents cascading failures by stopping repeated calls to a failing service.

    States:
      CLOSED: normal operation, calls go through
      OPEN: service is down, calls fail immediately (CircuitBreakerOpen raised)
      HALF_OPEN: testing if service has recovered

    Transitions:
      CLOSED → OPEN: after N consecutive failures
      OPEN → HALF_OPEN: after timeout period
      HALF_OPEN → CLOSED: after successful call
      HALF_OPEN → OPEN: if next call fails

    Usage:
        breaker = CircuitBreaker("archive.org", failure_threshold=3, timeout_sec=60)

        try:
            result = breaker.call(upload_to_archive, file, metadata)
        except CircuitBreakerOpen:
            logger.error("Archive.org is down, skipping upload")
    """

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(self, name: str, failure_threshold: int = 3, timeout_sec: int = 300):
        self.name = name
        self.failure_threshold = failure_threshold
        self.timeout_sec = timeout_sec

        self.state = self.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self.last_error = None

    def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """
        Execute func(*args, **kwargs) through the circuit breaker.

        Raises:
            CircuitBreakerOpen: if breaker is OPEN
            Exception: the original exception from func if it fails
        """
        if self.state == self.OPEN:
            if self._should_attempt_reset():
                logger.info(f"[{self.name}] Circuit breaker attempting reset (HALF_OPEN)")
                self.state = self.HALF_OPEN
            else:
                raise CircuitBreakerOpen(
                    f"Circuit breaker OPEN for {self.name}. "
                    f"Last error: {self.last_error}"
                )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure(e)
            raise

    def _on_success(self):
        """Called when a call succeeds."""
        if self.state == self.HALF_OPEN:
            logger.info(f"[{self.name}] Circuit breaker recovered (CLOSED)")
        self.state = self.CLOSED
        self.failure_count = 0
        self.last_failure_time = None

    def _on_failure(self, error: Exception):
        """Called when a call fails."""
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        self.last_error = str(error)

        if self.failure_count >= self.failure_threshold:
            logger.error(
                f"[{self.name}] Circuit breaker OPEN after {self.failure_count} failures"
            )
            self.state = self.OPEN

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt recovery."""
        if self.last_failure_time is None:
            return False
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.timeout_sec

    def __repr__(self):
        return (
            f"<CircuitBreaker {self.name} state={self.state} "
            f"failures={self.failure_count}/{self.failure_threshold}>"
        )


# ── Failure logging for manual recovery ────────────────────────────────────────

@dataclass
class FailedOperation:
    """
    A record of a failed API operation that can be manually retried.

    Attributes:
        timestamp: when the operation failed
        service: which API failed (e.g., "archive.org", "buzzsprout")
        operation: human-readable name (e.g., "upload_episode")
        error: error message
        context: additional context (e.g., file path, metadata)
        stacktrace: full Python traceback for debugging
    """
    timestamp: str
    service: str
    operation: str
    error: str
    context: dict
    stacktrace: str


class FailureLog:
    """
    Persists failed operations to a JSON file for later review and manual retry.

    File location: .failures/YYYY-MM-DD.json

    Usage:
        log = FailureLog()
        try:
            upload_to_archive(...)
        except Exception as e:
            log.record_failure(
                service="archive.org",
                operation="upload_episode",
                error=str(e),
                context={"file": "ep42.mp3", "episode": 42},
            )
    """

    def __init__(self, log_dir: Path = Path(".failures")):
        self.log_dir = log_dir
        self.log_dir.mkdir(exist_ok=True)

    def record_failure(
        self,
        service: str,
        operation: str,
        error: str,
        context: dict,
    ) -> FailedOperation:
        """
        Record a failed operation.

        Returns the FailedOperation object that was logged.
        """
        failed_op = FailedOperation(
            timestamp=datetime.now().isoformat(),
            service=service,
            operation=operation,
            error=error,
            context=context,
            stacktrace=traceback.format_exc(),
        )

        # Log to file
        log_file = self.log_dir / f"{datetime.now().date()}.json"
        entries = []
        if log_file.exists():
            with open(log_file) as f:
                entries = json.load(f)

        entries.append(asdict(failed_op))

        with open(log_file, "w") as f:
            json.dump(entries, f, indent=2)

        logger.warning(
            f"Failure recorded to {log_file}: {service}/{operation}"
        )
        return failed_op

    def list_failures(self, service: Optional[str] = None) -> list[FailedOperation]:
        """List all recorded failures, optionally filtered by service."""
        failures = []
        for log_file in sorted(self.log_dir.glob("*.json")):
            with open(log_file) as f:
                entries = json.load(f)
                for entry in entries:
                    if service is None or entry["service"] == service:
                        failures.append(FailedOperation(**entry))
        return failures


# ── Global instances ──────────────────────────────────────────────────────────

archive_breaker = CircuitBreaker("archive.org", failure_threshold=3, timeout_sec=600)
buzzsprout_breaker = CircuitBreaker("buzzsprout", failure_threshold=3, timeout_sec=300)
failure_log = FailureLog()
