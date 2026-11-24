"""Compatibility adapter for the existing Pi Harness runtime.

Importing this module is an explicit compatibility choice.  The independent
``pulse_harness.core`` package never imports it and therefore never acquires
Pi's RPC, JSONL, extension, or process dependencies.
"""

from .adapter import (
    PI_ADAPTER_KIND,
    PiCompatibilityAdapter,
    PiCompatibilityError,
    PiHarnessHandle,
)

__all__ = [
    "PI_ADAPTER_KIND",
    "PiCompatibilityAdapter",
    "PiCompatibilityError",
    "PiHarnessHandle",
]
