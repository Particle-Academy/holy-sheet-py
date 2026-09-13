"""The cross-runtime oracle: the PHP holy-sheet writer, driven as a subprocess.

This module is why the parity suite is a TEST RESULT and not a claim. The
Python writer is held to the same OOXML parts the PHP writer emits for the same
input, and PHP is the reference for the trio: it shipped first, and the pair's
contract is "the same document whichever backend runs it".

Three rules, each traceable to a suite in this org that reported green while
covering nothing:

1. **A missing toolchain is a FAILURE in CI, not a skip.** `skipIf(!HAS_PHP)`
   is exactly how the holy-sheet and dark-slide parity suites reported success
   over zero cross-engine coverage for months -- their workflow installed Node
   only. Locally a skip is tolerated (and shouted about); under `CI` it raises.
2. **Never byte-compare the container.** PHP writes through `ZipArchive`
   (DEFLATE, real mtimes); this port writes a fixed 1980-01-01 DOS date. Those
   files can never match, and a reader sees parts, never the compression.
3. **On Windows `php` is usually a shim** (Herd ships `php.bat`), which is fine
   for a shell but not for a bare `exec`. `PHP_BIN` takes an absolute path to a
   real interpreter and is checked first.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PHP_SCRIPT = REPO_ROOT / "scripts" / "php_tobytes.php"

_PHP_SRC_ENV = "HOLY_SHEET_PHP_SRC"


def php_binary() -> str | None:
    """An interpreter that can actually be spawned, or None."""
    explicit = os.environ.get("PHP_BIN") or os.environ.get("CONFORMANCE_PHP")
    if explicit:
        return explicit if Path(explicit).exists() else None
    return shutil.which("php")


def php_src_root() -> Path | None:
    """The PHP package's `src/`. Env first, sibling checkout second.

    Never a hard-coded sibling path alone: that resolves inside the .agi
    envelope and nowhere else, so any other layout gets no parity run at all
    rather than an error.
    """
    override = os.environ.get(_PHP_SRC_ENV)
    if override:
        return Path(override) if Path(override).is_dir() else None
    sibling = REPO_ROOT.parent / "holy-sheet" / "src"
    return sibling if sibling.is_dir() else None


def oracle_available() -> tuple[bool, str]:
    """(usable, why-not). The reason is printed, never swallowed."""
    if php_binary() is None:
        return False, "no PHP interpreter (set PHP_BIN to an absolute path)"
    if php_src_root() is None:
        return False, f"the PHP holy-sheet sources are not where {_PHP_SRC_ENV} or the sibling checkout says"
    if not PHP_SCRIPT.is_file():
        return False, f"missing {PHP_SCRIPT}"
    return True, ""


def require_oracle() -> None:
    """Raise when the oracle cannot run and we are somewhere it must.

    Under `CI` this is unconditional. That is the whole point: a green build
    asserting nothing is worse than a red one, because nobody investigates
    green.
    """
    ok, why = oracle_available()
    if ok:
        return
    if os.environ.get("CI"):
        raise RuntimeError(
            f"cross-runtime parity cannot run: {why}. This suite is the parity "
            "guarantee for the PHP/Python pair; skipping it in CI would report "
            "success with no coverage."
        )


def php_to_bytes(payload: object) -> bytes:
    """Run the PHP writer over `payload` and return the xlsx bytes."""
    binary = php_binary()
    src = php_src_root()
    if binary is None or src is None:
        raise RuntimeError("the PHP oracle is not available; call oracle_available() first")

    with tempfile.TemporaryDirectory(prefix="holy-sheet-parity-") as tmp:
        json_path = Path(tmp) / "input.json"
        out_path = Path(tmp) / "out.xlsx"
        json_path.write_text(json.dumps(payload), encoding="utf-8")

        env = dict(os.environ)
        env[_PHP_SRC_ENV] = str(src)

        result = subprocess.run(
            [binary, str(PHP_SCRIPT), str(json_path), str(out_path)],
            capture_output=True,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"the PHP oracle exited {result.returncode}: "
                f"{result.stderr.decode('utf-8', 'replace')[:2000]}"
            )
        return out_path.read_bytes()


PHP_DESCRIBE_SCRIPT = REPO_ROOT / "scripts" / "php_describe.php"


def php_describe(path: Path) -> object:
    """Run the PHP READER over a file on disk and return its schema, parsed.

    Object key order is kept (JSON objects load into insertion-ordered dicts),
    and so is PHP's float/int distinction: the script encodes with
    JSON_PRESERVE_ZERO_FRACTION.
    """
    binary = php_binary()
    src = php_src_root()
    if binary is None or src is None or not PHP_DESCRIBE_SCRIPT.is_file():
        raise RuntimeError("the PHP oracle is not available; call oracle_available() first")

    env = dict(os.environ)
    env[_PHP_SRC_ENV] = str(src)
    result = subprocess.run([binary, str(PHP_DESCRIBE_SCRIPT), str(path)], capture_output=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            f"the PHP reader exited {result.returncode}: "
            f"{result.stderr.decode('utf-8', 'replace')[:2000]}"
        )
    return json.loads(result.stdout.decode("utf-8"))


def ods_fixtures_dir() -> Path:
    """The ODS fixtures, which live in the PHP repo so all three engines read the same bytes.

    Found beside the PHP sources (HOLY_SHEET_PHP_SRC, then the sibling
    checkout). Missing is an ERROR, never a skip: these tests need no PHP, only
    the files, and a suite that goes green without its fixtures asserts nothing.
    """
    src = php_src_root()
    fixtures = src.parent / "tests" / "fixtures" / "ods" if src is not None else None
    if fixtures is None or not fixtures.is_dir():
        raise RuntimeError(
            "ODS fixtures not found. They live in the PHP holy-sheet repo at "
            "tests/fixtures/ods; check it out beside this one or set "
            f"{_PHP_SRC_ENV} to its src/ directory."
        )
    return fixtures


def parts(data: bytes) -> dict[str, bytes]:
    """Unzip an OOXML container into `{part name: bytes}`."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def text_parts(data: bytes) -> dict[str, str]:
    return {name: raw.decode("utf-8", "replace") for name, raw in parts(data).items()}
