"""Bounded caches of immutable NPC calculations, never world or actor state.

Direct edits to saved dictionaries remain visible on every query. Only dates
and scalar decay inputs are memoized; no actor/date-only view cache is used.
"""
import math
from datetime import date
from functools import lru_cache


@lru_cache(maxsize=8192)
def _parsed_day(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("NPC dates must use YYYY-MM-DD")
    return parsed


def parsed_day(value):
    # Validate before using a cache key, preserving the existing error contract
    # even for mutable/unhashable invalid input.
    if not isinstance(value, str):
        raise ValueError("NPC dates must use YYYY-MM-DD")
    return _parsed_day(value)


@lru_cache(maxsize=65536, typed=True)
def _decay(today, elapsed, enduring, reliability, salience, confidence):
    # Date and every scalar affecting decay are part of the key. Keep the
    # original arithmetic and multiplication order exactly.
    retention = .4 + .6 * math.exp(-elapsed / 7300) if enduring else math.exp(-elapsed / 730)
    return retention * reliability * salience * confidence


def memory_weight(memory, today):
    elapsed = max(0, (parsed_day(today) - parsed_day(memory["received_date"])).days)
    reliability, salience, confidence = memory["reliability"], memory["salience"], memory.get("confidence", 1)
    # Even typed LRU keys equate -0.0 with +0.0. Both are valid saved inputs,
    # and the original formula preserves their sign. Bypass caching only for
    # negative zero, preserving that exact calculation and positive-zero keys.
    if any(type(value) is float and value == 0.0 and math.copysign(1.0, value) < 0
           for value in (reliability, salience, confidence)):
        return _decay.__wrapped__(today, elapsed, bool(memory["enduring"]), reliability, salience, confidence)
    return _decay(today, elapsed, bool(memory["enduring"]), reliability, salience, confidence)
