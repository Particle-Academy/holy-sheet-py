"""`xl/commentsN.xml` -> {A1 address: CellComment}."""

from __future__ import annotations

from xml.etree.ElementTree import Element

from ..workbook.cell_comment import CellComment
from .xml import attr, find, find_all, parse_xml_or_none, text_of


class CommentsParser:
    @staticmethod
    def parse(comments_xml: bytes | str | None) -> dict[str, CellComment]:
        root = parse_xml_or_none(comments_xml)
        if root is None:
            return {}

        authors = [text_of(a) for a in find_all(find(root, "authors"), "author")]

        out: dict[str, CellComment] = {}
        for comment in find_all(find(root, "commentList"), "comment"):
            ref = attr(comment, "ref") or ""
            author_attr = attr(comment, "authorId")
            author_idx = int(author_attr) if author_attr is not None else -1
            author = authors[author_idx] if 0 <= author_idx < len(authors) else None
            out[ref] = CellComment(text=_extract_text(find(comment, "text")), author=author)
        return out


def _extract_text(node: Element | None) -> str:
    if node is None:
        return ""
    parts = [text_of(find(run, "t")) for run in find_all(node, "r")]
    parts += [text_of(t) for t in find_all(node, "t")]
    return "".join(parts)
