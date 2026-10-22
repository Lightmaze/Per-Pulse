"""Bounded, store-local memo for successful *pure* validation of exact values.

Not an authority cache: callers still read current rows and check relationships,
sequences and state on every full validation. Only immutable scalar/tuple keys
are admitted. No decoded mutable object, failure, decision or user input escapes
through this memo. Losing/evicting it changes performance only.
"""
from __future__ import annotations

from collections import OrderedDict
from sys import getsizeof


def _weight(value: object) -> int:
    """Conservative retained key size (shared references are counted again)."""
    if isinstance(value, tuple):
        return getsizeof(value) + sum(_weight(item) for item in value)
    if type(value) in {str, bytes, int, float, bool, type(None)}:
        return getsizeof(value)
    raise TypeError("validation keys must contain only immutable SQLite values")


class ValidationMemo:
    """Caller owns synchronization; bound retained keys as well as entry count."""
    def __init__(self, *, max_entries: int = 4096, max_bytes: int = 16 * 1024 * 1024):
        if type(max_entries) is not int or max_entries < 0 or type(max_bytes) is not int or max_bytes < 0:
            raise ValueError("memo bounds must be non-negative integers")
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self._entries: OrderedDict[tuple[object, ...], int] = OrderedDict()
        self.retained_bytes = 0

    def contains(self, key: tuple[object, ...]) -> bool:
        if key not in self._entries:
            return False
        self._entries.move_to_end(key)
        return True

    def remember(self, key: tuple[object, ...]) -> None:
        if not self.max_entries or not self.max_bytes or self.contains(key):
            return
        weight = _weight(key) + 128  # include node/size bookkeeping allowance
        if weight > self.max_bytes:
            return
        while self._entries and (len(self._entries) >= self.max_entries
                                 or self.retained_bytes + weight > self.max_bytes):
            _, removed = self._entries.popitem(last=False)
            self.retained_bytes -= removed
        self._entries[key] = weight
        self.retained_bytes += weight

    def clear(self) -> None:
        self._entries.clear()
        self.retained_bytes = 0
