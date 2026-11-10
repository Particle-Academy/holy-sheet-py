"""The agent guide ships, and is the same bytes in all three engines.

Each engine pins this hash over LF-normalised content; edit all three copies,
then all three constants.
"""

from __future__ import annotations

import hashlib

from holy_sheet import guide

AGENT_GUIDE_SHA256 = "06aeb56a5555330ac87cc67455c6c7f1533194b669e0d0359e364ce20a4daaa0"


def test_returns_the_guide_and_it_is_the_shared_one() -> None:
    text = guide()
    assert hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest() == AGENT_GUIDE_SHA256
    assert text.startswith("# Agent guide:")
