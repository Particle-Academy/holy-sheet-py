"""The agent guide ships, and is the same bytes in all three engines.

Each engine pins this hash over LF-normalised content; edit all three copies,
then all three constants.
"""

from __future__ import annotations

import hashlib

from holy_sheet import guide

AGENT_GUIDE_SHA256 = "9cc4d5138b6b15447397cc1dd18388c4b558a80c0332743da8b24a79f7223be8"


def test_returns_the_guide_and_it_is_the_shared_one() -> None:
    text = guide()
    assert hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest() == AGENT_GUIDE_SHA256
    assert text.startswith("# Agent guide:")
