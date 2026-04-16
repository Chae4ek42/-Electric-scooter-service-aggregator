"""Shared text formatting utilities for Telegram HTML parse_mode.

Replaces per-file _md_escape() helpers. Use `e()` to HTML-escape any
user-supplied string before embedding it in a formatted Telegram message.

Only three characters are dangerous in HTML mode: < > &
These are extremely rare in Russian model names, addresses, etc.,
which is why HTML is far more robust than Markdown for this codebase.
"""

from __future__ import annotations

import html as _html


def e(text) -> str:
    """HTML-escape user-supplied content for safe use in HTML parse_mode messages."""
    return _html.escape(str(text))
