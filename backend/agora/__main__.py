"""Agora 1.0 command entrypoint.

The autonomous 0.5 council CLI is deliberately retired. All supported workflow
operations enter through the versioned ``agora task`` control-plane command.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence


_HELP = """Agora - local-first Task delivery control plane

Usage:
  agora task <command> [options]
  agora --help

The Agora 0.5 interactive council and QUICK / DISCUSS / EXECUTE commands are
retired. Use `agora task --help` for the authoritative Project -> Task -> Stage
workflow.
"""


def cli_main(argv: Sequence[str] | None = None) -> int:
    """Dispatch only the authoritative Task command family."""

    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args in (["--help"], ["-h"]):
        print(_HELP)
        return 0

    if args[0] == "task":
        from agora.orchestration.cli import main as task_main

        return int(task_main(args[1:]) or 0)

    print(
        f"Unsupported Agora command: {args[0]!r}. "
        "The autonomous 0.5 council is retired; use `agora task --help`.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(cli_main())
