"""A1 addressing -- the one piece of arithmetic every other module depends on."""

from __future__ import annotations

import pytest

from holy_sheet import CellAddress


@pytest.mark.parametrize(
    ("index", "letters"),
    [(0, "A"), (25, "Z"), (26, "AA"), (27, "AB"), (51, "AZ"), (52, "BA"), (701, "ZZ"), (702, "AAA")],
)
def test_converts_an_index_to_letters(index: int, letters: str) -> None:
    assert CellAddress.letter(index) == letters


@pytest.mark.parametrize("index", [0, 1, 25, 26, 27, 701, 702, 1000, 16383])
def test_letters_and_index_are_inverses(index: int) -> None:
    assert CellAddress.index(CellAddress.letter(index)) == index


def test_index_accepts_lowercase_and_surrounding_space() -> None:
    assert CellAddress.index(" aa ") == 26


def test_rejects_a_negative_index() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        CellAddress.letter(-1)


@pytest.mark.parametrize("letters", ["", "A1", "1", "!"])
def test_rejects_malformed_letters(letters: str) -> None:
    with pytest.raises(ValueError, match="invalid column letters"):
        CellAddress.index(letters)


def test_parses_an_a1_address() -> None:
    assert CellAddress.parse("A1") == (0, 1)
    assert CellAddress.parse("AA10") == (26, 10)
    assert CellAddress.parse(" b2 ") == (1, 2)


@pytest.mark.parametrize("address", ["", "1A", "A", "12", "A1:B2"])
def test_returns_none_for_a_malformed_address(address: str) -> None:
    assert CellAddress.parse(address) is None
