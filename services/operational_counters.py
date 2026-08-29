"""Bounded process-local counters for rejected traffic.

Rejected analytics requests are intentionally not written one-row-per-attempt,
which would let an unauthenticated attacker amplify database writes.
"""

from collections import Counter
from threading import Lock


_counts: Counter[str] = Counter()
_lock = Lock()


def increment(name: str) -> None:
    with _lock:
        _counts[name] += 1


def get(name: str) -> int:
    with _lock:
        return int(_counts[name])


def reset_for_tests() -> None:
    with _lock:
        _counts.clear()
