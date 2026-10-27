"""The `holy-sheet` console script (and `python -m holy_sheet`). One command
today, mirroring PHP `bin/holy-sheet` and Node's `holy-sheet` bin:

    holy-sheet template:convert <input> <output> [--to=document|template] [--force]

Exit codes: 0 converted, 1 refused (the reason is printed), 2 usage.
"""

from __future__ import annotations

import os
import secrets
import sys
from typing import TextIO

from .agent import convert_template
from .archive.office_template import source_kind
from .archive.source_archive import SourceArchive
from .exceptions import ArchiveRefused, OfficeTemplateRefused, UnsupportedFormatException

USAGE = (
    "Usage: holy-sheet template:convert <input> <output> [--to=document|template] [--force]\n"
    "  Converts an .xltx/.ots template to its .xlsx/.ods twin, or back.\n"
    "  Without --to it flips whichever the input is.\n"
)


def main(argv: list[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    command = args.pop(0) if args else None
    positional: list[str] = []
    to: str | None = None
    force = False
    for arg in args:
        if arg.startswith("--to="):
            to = arg[5:]
        elif arg == "--force":
            force = True
        elif arg.startswith("--"):
            err.write(f"Unknown option {arg}\n{USAGE}")
            return 2
        else:
            positional.append(arg)
    if command != "template:convert" or len(positional) != 2:
        err.write(USAGE)
        return 2
    source, target = positional

    if os.path.exists(target) and not force:
        err.write(f"Refused: {target} already exists (pass --force to replace it).\n")
        return 1
    if os.path.exists(source) and os.path.exists(target) and os.path.samefile(source, target):
        err.write("Refused: the output is the input.\n")
        return 1
    if not os.path.isfile(source):
        err.write(f"Refused: {source} is not a file.\n")
        return 1

    try:
        converted = convert_template(source, to)
    except (OfficeTemplateRefused, ArchiveRefused) as error:
        err.write(f"Refused ({error.reason}): {error}\n")
        return 1
    except UnsupportedFormatException as error:
        err.write(f"Refused (unsupported): {error}\n")
        return 1

    # Written beside the target and renamed, so a failure leaves no half file.
    temp = f"{target}.tmp-{secrets.token_hex(4)}"
    try:
        with open(temp, "wb") as handle:
            handle.write(converted)
        os.replace(temp, target)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass
        err.write(f"Could not write {target}.\n")
        return 1
    out.write(f"Wrote {target} ({source_kind(SourceArchive.open(converted))}).\n")
    return 0
