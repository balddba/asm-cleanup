"""Text helpers for cleaning remote/local command output."""

from __future__ import annotations

import re

# CSI / OSC-ish ANSI sequences commonly emitted by colored shells and oraenv.
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text.

    Args:
        text (str): Raw terminal text that may include color/style codes.

    Returns:
        str: Text with ANSI sequences removed.
    """
    if not text or "\x1b" not in text:
        return text
    return _ANSI_ESCAPE_RE.sub("", text)
