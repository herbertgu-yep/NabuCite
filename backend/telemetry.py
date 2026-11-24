"""Process-local, bounded-cardinality metrics for API and worker."""

from functools import wraps

from prometheus_client import Counter, Histogram

DURATION = Histogram("nabucite_stage_seconds", "Business stage latency", ["stage"])
FAILURES = Counter("nabucite_failures", "Failed operations", ["stage"])
CALLS = Counter("nabucite_upstream_calls", "Actual upstream requests", ["purpose"])


def timed(stage):
    def decorate(fn):
        @wraps(fn)
        async def run(*args, **kwargs):
            with DURATION.labels(stage).time():
                try:
                    return await fn(*args, **kwargs)
                except Exception:
                    FAILURES.labels(stage).inc()
                    raise

        return run

    return decorate
