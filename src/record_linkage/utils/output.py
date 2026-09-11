"""Salida de consola tolerante a la codificación del host.

Windows y algunos capturadores abren stdout/stderr con cp1252. Un emoji en un
``print`` puede entonces abortar el pipeline con ``UnicodeEncodeError``. Esta
función conserva Unicode cuando el stream lo admite y escapa solamente los
caracteres que su codificación declarada no puede representar.
"""

from __future__ import annotations

import builtins
import sys
from typing import Any


def _text_for_encoding(value: object, encoding: str) -> str:
    """Render ``value`` once and escape only unencodable characters."""

    text = str(value)
    try:
        text.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        try:
            return text.encode(encoding, errors="backslashreplace").decode(encoding)
        except LookupError:
            return text
    return text


def safe_print(
    *objects: object,
    sep: str = " ",
    end: str = "\n",
    file: Any | None = None,
    flush: bool = False,
) -> None:
    """Behave like :func:`print` without failing on a narrow text encoding."""

    target = sys.stdout if file is None else file
    encoding = getattr(target, "encoding", None)
    rendered: tuple[object, ...]
    if isinstance(encoding, str) and encoding:
        rendered = tuple(_text_for_encoding(value, encoding) for value in objects)
        safe_sep = _text_for_encoding(sep, encoding)
        safe_end = _text_for_encoding(end, encoding)
    else:
        rendered = objects
        safe_sep = sep
        safe_end = end
    builtins.print(*rendered, sep=safe_sep, end=safe_end, file=target, flush=flush)
