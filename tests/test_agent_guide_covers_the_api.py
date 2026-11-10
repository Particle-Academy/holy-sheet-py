"""The agent guide names every public Agent function, or the function is on the
allowlist below with the reason it is left out.

A function the guide never names is one no model reading the guide will use:
0.9 to 0.13 added ``rows``, ``read``'s ``sheet`` and ``limits``, border styles
and data validation, and the guide said nothing about any of them.
The surface is every function defined in ``holy_sheet.agent`` that the package
exports in ``__all__``. "Names" means written as code: ``Agent::x``,
``Agent.x`` or a backticked ``x``, where ``x`` is the snake_case name or its
camelCase form (the guide is shared with PHP and Node). The PHP and Node
packages carry the same test over their own surfaces.
"""

from __future__ import annotations

import inspect
import re

import holy_sheet
from holy_sheet import agent, guide

ALLOWLIST: dict[str, str] = {
    "guide": "the guide itself: a model reading it already has it",
    "version": "package metadata; it plays no part in building or reading a workbook",
}


def _surface() -> list[str]:
    return [
        name
        for name, fn in inspect.getmembers(agent, inspect.isfunction)
        if fn.__module__ == agent.__name__ and name in holy_sheet.__all__
    ]


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _names(text: str, function: str) -> bool:
    forms = {function, _camel(function)}
    return any(re.search(r"(Agent::|Agent\.|`)" + re.escape(f) + r"\b", text) for f in forms)


def test_names_every_public_agent_function_or_allowlists_it() -> None:
    text = guide()
    missing = [f for f in _surface() if f not in ALLOWLIST and not _names(text, f)]
    assert missing == [], f"agent_guide.md never names: {', '.join(missing)}"


def test_allowlist_entries_exist_have_a_reason_and_are_not_named() -> None:
    text = guide()
    functions = _surface()
    for function, reason in ALLOWLIST.items():
        assert function in functions
        assert reason.strip() != ""
        assert not _names(text, function), f"{function} is named in the guide; drop it from the allowlist"
