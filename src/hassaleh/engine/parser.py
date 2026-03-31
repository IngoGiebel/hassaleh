"""GSL-Ops Parser — Parse deterministic rule text into Lark AST.

Uses Lark with PythonIndenter for Python-style colon blocks.

Usage:
    from hassaleh.engine.parser import parse_rule
    tree = parse_rule(rule_text)
"""

from __future__ import annotations

from pathlib import Path

from lark import Lark
from lark.indenter import PythonIndenter

# Grammar file lives next to this module
_GRAMMAR_PATH = Path(__file__).parent / "gsl_ops.lark"

# Singleton parser (thread-safe for reads, Lark is immutable after construction)
_parser: Lark | None = None


def _get_parser() -> Lark:
    """Lazy-initialize the Lark parser."""
    global _parser
    if _parser is None:
        _parser = Lark(
            _GRAMMAR_PATH.read_text(),
            parser="earley",
            postlex=PythonIndenter(),
            propagate_positions=True,
        )
    return _parser


def parse_rule(rule_text: str) -> "Tree":
    """Parse a GSL-Ops rule into a Lark Tree.

    Args:
        rule_text: GSL-Ops source code

    Returns:
        Lark Tree (AST)

    Raises:
        lark.exceptions.UnexpectedInput: Parse error
    """
    parser = _get_parser()
    # Ensure trailing newline (PythonIndenter needs it)
    if not rule_text.endswith("\n"):
        rule_text += "\n"
    return parser.parse(rule_text)
