"""CLI output formatting for Hassaleh.

Provides consistent, colorful terminal output without heavy dependencies.
Uses ANSI escape codes directly (no click/rich dependency).
"""

from __future__ import annotations

import os
import sys


# ──────────────────────────────────────────────
# Color support
# ──────────────────────────────────────────────

def _supports_color() -> bool:
    """Check if stdout supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty"):
        return False
    return sys.stdout.isatty()


_COLOR = _supports_color()


def _c(code: str, text: str) -> str:
    """Apply ANSI color code if supported."""
    if not _COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


# ──────────────────────────────────────────────
# Color helpers
# ──────────────────────────────────────────────

def green(text: str) -> str:
    return _c("32", text)

def red(text: str) -> str:
    return _c("31", text)

def yellow(text: str) -> str:
    return _c("33", text)

def cyan(text: str) -> str:
    return _c("36", text)

def bold(text: str) -> str:
    return _c("1", text)

def dim(text: str) -> str:
    return _c("2", text)


# ──────────────────────────────────────────────
# Semantic formatters
# ──────────────────────────────────────────────

def fmt_ok(text: str) -> str:
    return green(f"✅ {text}")

def fmt_warn(text: str) -> str:
    return yellow(f"⚠️  {text}")

def fmt_error(text: str) -> str:
    return red(f"❌ {text}")

def fmt_header(text: str) -> None:
    print()
    print(bold(f"⭐ {text}"))
    print(dim("─" * (len(text) + 2)))

def fmt_section(title: str) -> str:
    return "\n" + cyan(f"  ── {title} ──")

def fmt_kv(key: str, value: str, indent: int = 4) -> str:
    return " " * indent + f"{bold(key)}: {value}"

def fmt_code(code: str) -> str:
    """Format a code block with line numbers."""
    lines = code.rstrip().split("\n")
    numbered = []
    for i, line in enumerate(lines, 1):
        num = dim(f"{i:4d} │ ")
        numbered.append(f"    {num}{line}")
    return "\n".join(numbered)


# ──────────────────────────────────────────────
# Table formatter
# ──────────────────────────────────────────────

def fmt_table(headers: list[str], rows: list[list[str]], indent: int = 4) -> str:
    """Format data as an aligned ASCII table.

    Args:
        headers: Column headers
        rows: List of row data (each row is a list of strings)
        indent: Left indent spaces

    Returns:
        Formatted table string
    """
    if not rows:
        return " " * indent + "(empty)"

    # Calculate column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))

    # Format header
    prefix = " " * indent
    header_line = prefix + "  ".join(
        bold(h.ljust(widths[i])) for i, h in enumerate(headers)
    )
    separator = prefix + "  ".join("─" * w for w in widths)

    # Format rows
    data_lines = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            if i < len(widths):
                cells.append(str(cell).ljust(widths[i]))
        data_lines.append(prefix + "  ".join(cells))

    return "\n".join([header_line, separator] + data_lines)
