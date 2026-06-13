from __future__ import annotations

from typing import TextIO

from .runtime import main


def console_main() -> None:
    raise SystemExit(main())


__all__ = ["console_main", "main", "TextIO"]
