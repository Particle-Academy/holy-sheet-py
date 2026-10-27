"""Bounded, immutable reader input. Mirrors PHP `HolySheet\\Archive\\SourceArchive`
and Node `zip/source-archive.ts`, check for check and reason for reason.

Every reader used to open uploads with a bare `zipfile.ZipFile` and read
whatever the central directory declared. This validates the whole container
BEFORE a byte is inflated: coherent local and central records, safe and unique
member names, archive / part / total / ratio ceilings, then CRC and exact sizes
while inflating, bounded so a forged declared size cannot drive an unbounded
allocation. Nothing is extracted to disk and nothing is fetched.

Refusals raise `ArchiveRefused` with a constant `reason` and never echo member
names or content.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any

from ..exceptions import ArchiveRefused, UnsupportedFormatException

DEFAULT_LIMITS: dict[str, int] = {
    "maxArchiveBytes": 67108864,
    "maxEntries": 16384,
    "maxPartBytes": 67108864,
    "maxExpandedBytes": 268435456,
    "maxCompressionRatio": 1000,
}


class SourceArchive:
    """Verified members of one zip, plus the original bytes untouched."""

    def __init__(self, original: bytes, parts: dict[str, bytes], records: list[dict[str, Any]], comment: bytes) -> None:
        self._original = original
        self._parts = parts
        self._records = records
        self._comment = comment

    @classmethod
    def open(cls, data: bytes, limits: dict[str, int] | None = None) -> "SourceArchive":
        merged = _limits(limits or {})
        data = bytes(data)
        if len(data) > merged["maxArchiveBytes"]:
            raise ArchiveRefused("archive_limit")
        records, comment = _index(data, merged)
        parts: dict[str, bytes] = {}
        for record in records:
            body = data[record["start"] : record["start"] + record["compressed"]]
            if record["method"] == 0:
                payload = body
            else:
                try:
                    inflater = zlib.decompressobj(-15)
                    # The extra byte detects a forged size without an unbounded allocation.
                    payload = inflater.decompress(body, record["size"] + 1)
                except zlib.error as error:
                    raise ArchiveRefused("archive_corrupt") from error
            if len(payload) != record["size"] or zlib.crc32(payload) != record["crc"]:
                raise ArchiveRefused("archive_corrupt")
            parts[record["name"]] = payload
        public = [{key: record[key] for key in _PUBLIC} for record in records]
        return cls(data, parts, public, comment)

    def names(self) -> list[str]:
        return list(self._parts)

    def read(self, name: str) -> bytes | None:
        return self._parts.get(name)

    def to_bytes(self) -> bytes:
        return self._original

    def entries(self) -> list[dict[str, Any]]:
        """Verified member records in central-directory order: where each local
        record starts and ends in `to_bytes()` (data descriptor included) and where
        its central record sits. Lets a caller copy a member as raw bytes."""
        return [dict(record) for record in self._records]

    def comment(self) -> bytes:
        return self._comment

    # A context manager only so it drops in where `zipfile.ZipFile` was used;
    # everything is already in memory, so there is nothing to close.
    def __enter__(self) -> "SourceArchive":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def open_for_reading(data: bytes, path: str | None = None) -> SourceArchive:
    """The readers' door: bytes that are not a zip at all are named as such
    (`UnsupportedFormatException.not_a_zip`, as before); a zip the guard refuses
    raises its typed `ArchiveRefused`, as PHP's and Node's readers do."""
    try:
        return SourceArchive.open(data)
    except ArchiveRefused as error:
        if error.reason == "archive_corrupt" and bytes(data[:2]) != b"PK":
            raise UnsupportedFormatException.not_a_zip(path) from error
        raise


_PUBLIC = ("name", "size", "crc", "local", "finish", "central", "central_length", "flags", "method", "local_extra")


def _limits(limits: dict[str, int]) -> dict[str, int]:
    for key, value in limits.items():
        if key not in DEFAULT_LIMITS or isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ArchiveRefused("archive_limits")
    return {**DEFAULT_LIMITS, **limits}


def _bounds(data: bytes, at: int, size: int, end: int | None = None) -> None:
    if at < 0 or size < 0 or at + size > (len(data) if end is None else end) or at + size > len(data):
        raise ArchiveRefused("archive_corrupt")


def _u16(data: bytes, at: int) -> int:
    _bounds(data, at, 2)
    return struct.unpack_from("<H", data, at)[0]


def _u32(data: bytes, at: int) -> int:
    _bounds(data, at, 4)
    return struct.unpack_from("<I", data, at)[0]


def _index(data: bytes, limits: dict[str, int]) -> tuple[list[dict[str, Any]], bytes]:
    length = len(data)
    end = None
    for at in range(length - 22, max(0, length - 65557) - 1, -1):
        if (
            data[at : at + 4] == b"PK\x05\x06"
            and at + 22 + _u16(data, at + 20) == length
            and _u32(data, at + 16) + _u32(data, at + 12) == at
        ):
            end = at
            break
    if end is None:
        raise ArchiveRefused("archive_corrupt")
    count = _u16(data, end + 10)
    if _u16(data, end + 4) != 0 or _u16(data, end + 6) != 0 or _u16(data, end + 8) != count or count == 65535:
        raise ArchiveRefused("archive_unsupported")
    if count > limits["maxEntries"]:
        raise ArchiveRefused("archive_limit")
    directory = _u32(data, end + 16)
    at = directory
    if end - at < count * 46:
        raise ArchiveRefused("archive_corrupt")

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    regions: list[tuple[int, int]] = []
    expanded = 0
    for _ in range(count):
        _bounds(data, at, 46, end)
        if _u32(data, at) != 0x02014B50:
            raise ArchiveRefused("archive_corrupt")
        flags = _u16(data, at + 8)
        method = _u16(data, at + 10)
        crc = _u32(data, at + 16)
        compressed = _u32(data, at + 20)
        size = _u32(data, at + 24)
        name_length = _u16(data, at + 28)
        extra_length = _u16(data, at + 30)
        comment_length = _u16(data, at + 32)
        local = _u32(data, at + 42)
        if (
            (flags & ~0x080E) != 0
            or method not in (0, 8)
            or (method == 0 and (flags & 6) != 0)
            or _u16(data, at + 34) != 0
            or 0xFFFFFFFF in (compressed, size, local)
            or _u16(data, at + 6) > 20
        ):
            raise ArchiveRefused("archive_unsupported")
        _bounds(data, at + 46, name_length + extra_length + comment_length, end)
        raw_name = data[at + 46 : at + 46 + name_length]
        name = _name(raw_name, flags)
        if name in seen:
            raise ArchiveRefused("archive_duplicate")
        seen.add(name)
        expanded += size
        if (
            size > limits["maxPartBytes"]
            or expanded > limits["maxExpandedBytes"]
            or size > max(1, compressed) * limits["maxCompressionRatio"]
        ):
            raise ArchiveRefused("archive_limit")
        if method == 0 and compressed != size:
            raise ArchiveRefused("archive_corrupt")
        _extra(data, at + 46 + name_length, extra_length)
        _bounds(data, local, 30, directory)
        if (
            _u32(data, local) != 0x04034B50
            or _u16(data, local + 4) != _u16(data, at + 6)
            or _u16(data, local + 6) != flags
            or _u16(data, local + 8) != method
        ):
            raise ArchiveRefused("archive_corrupt")
        local_name = _u16(data, local + 26)
        local_extra = _u16(data, local + 28)
        start = local + 30 + local_name + local_extra
        _bounds(data, local + 30, local_name + local_extra + compressed, directory)
        if data[local + 30 : local + 30 + local_name] != raw_name:
            raise ArchiveRefused("archive_corrupt")
        _extra(data, local + 30 + local_name, local_extra)
        for offset, expected in ((14, crc), (18, compressed), (22, size)):
            value = _u32(data, local + offset)
            if value != expected and not ((flags & 8) != 0 and value == 0):
                raise ArchiveRefused("archive_corrupt")
        finish = start + compressed
        if (flags & 8) != 0:
            finish = _descriptor(data, finish, directory, crc, compressed, size)
        records.append(
            {
                "name": name, "size": size, "crc": crc, "local": local, "finish": finish, "central": at,
                "central_length": 46 + name_length + extra_length + comment_length,
                "flags": flags, "method": method, "local_extra": local_extra,
                "start": start, "compressed": compressed,
            }
        )
        regions.append((local, finish))
        at += 46 + name_length + extra_length + comment_length
    if at != end:
        raise ArchiveRefused("archive_corrupt")
    previous = 0
    for region_start, region_finish in sorted(regions):
        if region_start < previous:
            raise ArchiveRefused("archive_corrupt")
        previous = region_finish
    return records, data[end + 22 : end + 22 + _u16(data, end + 20)]


def _name(raw: bytes, flags: int) -> str:
    try:
        name = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ArchiveRefused("archive_path") from error
    if name == "" or name.startswith("/") or "\\" in name or ":" in name:
        raise ArchiveRefused("archive_path")
    for byte in raw:
        if byte < 32 or byte == 127:
            raise ArchiveRefused("archive_path")
        if byte > 127 and (flags & 0x0800) == 0:
            raise ArchiveRefused("archive_unsupported")
    for part in (name[:-1] if name.endswith("/") else name).split("/"):
        if part in ("", ".", ".."):
            raise ArchiveRefused("archive_path")
    return name


def _extra(data: bytes, start: int, length: int) -> None:
    finish = start + length
    while start < finish:
        _bounds(data, start, 4, finish)
        ident = _u16(data, start)
        size = _u16(data, start + 2)
        _bounds(data, start + 4, size, finish)
        if ident in (1, 0x9901):
            raise ArchiveRefused("archive_unsupported")
        start += 4 + size


def _descriptor(data: bytes, at: int, end: int, crc: int, compressed: int, size: int) -> int:
    for prefix in (0, 4):
        if (
            at + prefix + 12 <= end
            and (prefix == 0 or _u32(data, at) == 0x08074B50)
            and _u32(data, at + prefix) == crc
            and _u32(data, at + prefix + 4) == compressed
            and _u32(data, at + prefix + 8) == size
        ):
            return at + prefix + 12
    raise ArchiveRefused("archive_corrupt")
