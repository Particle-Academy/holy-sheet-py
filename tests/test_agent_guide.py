"""The agent guide ships, and is the same bytes in all three engines.

Each engine pins this hash over LF-normalised content; edit all three copies,
then all three constants.
"""

from __future__ import annotations

import hashlib

from holy_sheet import guide

AGENT_GUIDE_SHA256 = "09b137e5c8eb5a933e3575df4017fa62c0a907e15858d5970ae00f2ce59789e7"


def test_returns_the_guide_and_it_is_the_shared_one() -> None:
    text = guide()
    assert hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest() == AGENT_GUIDE_SHA256
    assert text.startswith("# Agent guide:")
