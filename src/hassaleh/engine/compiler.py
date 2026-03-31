"""GSL-Ops Compiler — Transpile GSL-Ops parse trees to executable Python.

Takes a Lark Tree (from parser.parse_rule) and generates a Python
function that, when called with a RuleContext, evaluates the rule.

Generated code signature:
    def evaluate(ctx: RuleContext) -> None:
        # ... emits intents, logs, alerts to ctx

The generated source is human-readable and stored in the Rule node's
compiled_python property.

Reference: docs/CONCEPT.md v1.2, Section 4.14
"""

from __future__ import annotations

import re
from lark import Tree, Token
from hassaleh.engine.parser import parse_rule

COMPILER_VERSION = "gsl-ops-0.1"


class GSLOpsCompiler:
    """Compile a GSL-Ops parse tree to Python source code."""

    def __init__(self, rule_id: str = "unknown"):
        self.rule_id = rule_id
        self._indent = 0
        self._lines: list[str] = []

    def compile(self, tree: Tree) -> str:
        """Compile a Lark Tree (start node) to Python source."""
        self._indent = 0
        self._lines = []

        # Header — no import statement; RuleContext is pre-injected into
        # the exec() namespace by the Daemon to avoid needing __import__
        self._emit("")
        self._emit("")
        self._emit("def evaluate(ctx) -> None:")
        self._indent = 1
        self._emit(f'"""Compiled from GSL-Ops rule: {self.rule_id}"""')

        # Process top-level statements
        has_statements = False
        for child in tree.children:
            if isinstance(child, Tree):
                self._compile_node(child)
                has_statements = True

        if not has_statements:
            self._emit("pass")

        return "\n".join(self._lines) + "\n"

    # ── Dispatch ──

    def _compile_node(self, node: Tree) -> None:
        handler = getattr(self, f"_compile_{node.data}", None)
        if handler:
            handler(node)
        else:
            for child in node.children:
                if isinstance(child, Tree):
                    self._compile_node(child)

    # ── MATCH block ──

    def _compile_match_block(self, node: Tree) -> None:
        match_line = self._get_token(node, "MATCH_LINE")
        # Strip "MATCH " prefix and trailing ":"
        cypher = match_line[6:].rstrip().rstrip(":")

        self._emit(f'for _row in ctx.match({cypher!r}):')
        self._indent += 1

        # Extract variable names from Cypher pattern
        var_names = self._extract_cypher_vars(cypher)
        for var in var_names:
            self._emit(f'{var} = _row["{var}"]')

        # Compile body
        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")

        self._indent -= 1

    # ── IF / ELIF / ELSE ──

    def _compile_if_block(self, node: Tree) -> None:
        cond = self._extract_condition(node)
        self._emit(f"if {cond}:")
        self._indent += 1
        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")
        self._indent -= 1

    def _compile_elif_block(self, node: Tree) -> None:
        cond = self._extract_condition(node)
        self._emit(f"elif {cond}:")
        self._indent += 1
        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")
        self._indent -= 1

    def _compile_else_block(self, node: Tree) -> None:
        self._emit("else:")
        self._indent += 1
        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")
        self._indent -= 1

    # ── FOREACH block ──

    def _compile_foreach_block(self, node: Tree) -> None:
        name = self._get_token(node, "NAME")
        # Expression is everything between NAME and body
        expr_children = []
        body = None
        found_name = False
        for child in node.children:
            if isinstance(child, Tree) and child.data == "body":
                body = child
            elif isinstance(child, Token) and child.type == "NAME" and not found_name:
                found_name = True
            elif found_name and not (isinstance(child, Tree) and child.data == "body"):
                expr_children.append(child)

        collection_expr = self._compile_expr_list(expr_children)
        self._emit(f"for {name} in {collection_expr}:")
        self._indent += 1

        if body:
            self._compile_body(body)
        else:
            self._emit("pass")

        self._indent -= 1

    # ── EVERY block ──

    def _compile_every_block(self, node: Tree) -> None:
        # Extract duration
        duration = self._find_child(node, "duration")
        dur_str = self._compile_duration(duration) if duration else '"PT1M"'

        self._emit(f'if ctx.should_run_schedule({dur_str}):')
        self._indent += 1

        body = self._find_child(node, "body")
        if body:
            self._compile_body(body)
        else:
            self._emit("pass")

        self._indent -= 1

    # ── LET ──

    def _compile_let_stmt(self, node: Tree) -> None:
        # Grammar: let_stmt: "LET" NAME "=" expr
        # Children: [NAME_token, ...expr_nodes]
        # The first NAME token is the variable name; everything after is the expr.
        name = self._get_token(node, "NAME")
        # Find the index of the first NAME token, take everything after it
        name_idx = 0
        for i, c in enumerate(node.children):
            if isinstance(c, Token) and c.type == "NAME":
                name_idx = i
                break
        # Expr is everything after the NAME (Lark strips "LET" and "=" keywords)
        expr_children = node.children[name_idx + 1:]
        expr = self._compile_expr_list(expr_children)
        self._emit(f"{name} = {expr}")

    # ── Effects ──

    def _compile_effect_set(self, node: Tree) -> None:
        prop, expr = self._extract_effect(node)
        node_var, prop_name = prop.split(".", 1)
        self._emit(f'ctx.set_property({node_var}, "{prop_name}", {expr})')

    def _compile_effect_add(self, node: Tree) -> None:
        prop, expr = self._extract_effect(node)
        node_var, prop_name = prop.split(".", 1)
        self._emit(f'ctx.add_property({node_var}, "{prop_name}", {expr})')

    def _compile_effect_sub(self, node: Tree) -> None:
        prop, expr = self._extract_effect(node)
        node_var, prop_name = prop.split(".", 1)
        self._emit(f'ctx.sub_property({node_var}, "{prop_name}", {expr})')

    def _compile_effect_mul(self, node: Tree) -> None:
        prop, expr = self._extract_effect(node)
        node_var, prop_name = prop.split(".", 1)
        self._emit(f'ctx.mul_property({node_var}, "{prop_name}", {expr})')

    # ── Actions ──

    def _compile_submit_intent(self, node: Tree) -> None:
        cap_id = self._get_token(node, "STRING").strip('"').strip("'")
        target_var = self._get_token(node, "NAME")
        with_clause = self._find_child(node, "with_clause")

        if with_clause:
            kv_pairs = list(with_clause.find_data("kv_pair"))
            args_parts = []
            for kv in kv_pairs:
                key = self._get_token(kv, "NAME")
                val_children = [c for c in kv.children
                                if not (isinstance(c, Token) and c.type == "NAME")]
                val = self._compile_expr_list(val_children)
                args_parts.append(f'"{key}": {val}')
            args_dict = "{" + ", ".join(args_parts) + "}"
            self._emit(f'ctx.submit_intent("{cap_id}", {target_var}, {args_dict})')
        else:
            self._emit(f'ctx.submit_intent("{cap_id}", {target_var})')

    def _compile_log_stmt(self, node: Tree) -> None:
        strings = [c for c in node.children
                   if isinstance(c, Token) and c.type == "STRING"]
        message = strings[0].strip('"').strip("'") if strings else ""
        level_node = self._find_child(node, "log_level")
        if level_node:
            level_str = self._get_token(level_node, "STRING").strip('"').strip("'")
            self._emit(f'ctx.log("{message}", level="{level_str}")')
        else:
            self._emit(f'ctx.log("{message}")')

    def _compile_alert_stmt(self, node: Tree) -> None:
        msg = self._get_token(node, "STRING").strip('"').strip("'")
        target_var = self._get_token(node, "NAME")
        if target_var:
            self._emit(f'ctx.alert("{msg}", {target_var})')
        else:
            self._emit(f'ctx.alert("{msg}")')

    # ── Body ──

    def _compile_body(self, node: Tree) -> None:
        has_stmts = False
        for child in node.children:
            if isinstance(child, Tree):
                self._compile_node(child)
                has_stmts = True
        if not has_stmts:
            self._emit("pass")

    # ── Expression compilation ──

    def _extract_condition(self, node: Tree) -> str:
        """Extract the condition expression from an IF/ELIF node."""
        # Everything that's not the body is the condition
        cond_parts = []
        for child in node.children:
            if isinstance(child, Tree) and child.data == "body":
                continue
            if isinstance(child, Tree):
                cond_parts.append(child)
            elif isinstance(child, Token) and child.type not in (
                "_NEWLINE", "_INDENT", "_DEDENT"
            ):
                cond_parts.append(child)
        return self._compile_expr_list(cond_parts)

    def _extract_effect(self, node: Tree) -> tuple[str, str]:
        """Extract (prop_access, value_expr) from an effect node."""
        prop = self._get_token(node, "PROP_ACCESS")
        value_children = [c for c in node.children
                          if not (isinstance(c, Token) and c.type in
                                  ("PROP_ACCESS", "MOD_OP"))]
        value = self._compile_expr_list(value_children)
        return prop, value

    def _compile_expr_list(self, nodes: list) -> str:
        """Compile a list of AST nodes/tokens into a Python expression."""
        parts = []
        for node in nodes:
            if isinstance(node, Token):
                parts.append(self._compile_token(node))
            elif isinstance(node, Tree):
                parts.append(self._compile_expr_tree(node))
        result = " ".join(parts) if parts else "None"
        return result.strip()

    def _compile_expr_tree(self, node: Tree) -> str:
        """Compile a single expression tree node to Python."""
        d = node.data

        if d == "number":
            return str(node.children[0])

        if d == "string_literal":
            return str(node.children[0])

        if d == "prop_access":
            pa = str(node.children[0])
            var, prop = pa.split(".", 1)
            return f'ctx.prop({var}, "{prop}")'

        if d == "var_ref":
            name = str(node.children[0])
            return name

        if d == "bool_true":
            return "True"

        if d == "bool_false":
            return "False"

        if d == "null_literal":
            return "None"

        if d == "negation":
            # Grammar: not_expr: NOT_OP not_expr -> negation
            # children[0] = NOT_OP token, children[1] = expression tree
            tree_children = [c for c in node.children if isinstance(c, Tree)]
            inner = self._compile_expr_tree(tree_children[0]) if tree_children else "None"
            return f"(not {inner})"

        if d == "neg":
            # Grammar: factor: MINUS atom -> neg
            # children[0] = MINUS token, children[1] = atom tree
            tree_children = [c for c in node.children if isinstance(c, Tree)]
            inner = self._compile_expr_tree(tree_children[0]) if tree_children else "0"
            return f"(-{inner})"

        if d == "list_literal":
            return self._compile_list_literal(node)

        if d == "func_call":
            return self._compile_func_call(node)

        if d in ("comparison", "and_expr", "or_expr", "arith", "term"):
            parts = []
            for ch in node.children:
                if isinstance(ch, Token):
                    parts.append(self._compile_token(ch))
                elif isinstance(ch, Tree):
                    parts.append(self._compile_expr_tree(ch))
            return f"({' '.join(parts)})"

        # Fallback: recurse
        parts = []
        for ch in node.children:
            if isinstance(ch, Token):
                parts.append(self._compile_token(ch))
            elif isinstance(ch, Tree):
                parts.append(self._compile_expr_tree(ch))
        return " ".join(parts)

    def _compile_list_literal(self, node: Tree) -> str:
        """Compile [a, b, c] to a Python list."""
        items_node = self._find_child(node, "list_items")
        if not items_node:
            return "[]"
        items = [self._compile_expr_tree(ch) for ch in items_node.children
                 if isinstance(ch, Tree)]
        return f"[{', '.join(items)}]"

    def _compile_func_call(self, node: Tree) -> str:
        """Compile a function call."""
        func_name = self._get_token(node, "NAME")
        args_node = self._find_child(node, "func_args")

        # Map GSL-Ops function names to runtime methods
        FUNC_MAP = {
            "NOW": "ctx.now()",
            "DURATION": None,  # special handling
            "ABS": "abs",
            "MIN": "min",
            "MAX": "max",
            "CLAMP": "ctx.clamp",
            "LEN": "len",
            "STR": "str",
            "INT": "int",
            "FLOAT": "float",
            "RANGE": "range",
            "KEYS": "ctx.keys",
            "SORTED": "sorted",
            "LIST": "list",
        }

        if func_name == "NOW":
            return "ctx.now()"

        if func_name == "DURATION":
            if args_node:
                arg = self._compile_expr_list(list(args_node.children))
                return f"ctx.duration({arg})"
            return 'ctx.duration("PT0S")'

        mapped = FUNC_MAP.get(func_name)
        if mapped and args_node:
            args = [self._compile_expr_tree(ch) for ch in args_node.children
                    if isinstance(ch, Tree)]
            return f"{mapped}({', '.join(args)})"

        # Unknown function — reject at compile time (security: prevents
        # calling arbitrary ctx methods like ctx.session())
        raise ValueError(
            f"Unknown function '{func_name}' in rule '{self.rule_id}'. "
            f"Allowed: {', '.join(sorted(FUNC_MAP.keys()))}"
        )

    def _compile_token(self, token: Token) -> str:
        t = str(token)
        if token.type == "OR_OP":
            return "or"
        if token.type == "AND_OP":
            return "and"
        if token.type == "NOT_OP":
            return "not"
        if token.type == "COMP_OP":
            return t
        return t

    def _compile_duration(self, node: Tree) -> str:
        """Compile a duration node."""
        # Check for STRING child (ISO 8601)
        string_tok = self._get_token(node, "STRING")
        if string_tok:
            return string_tok

        # Check for DURATION_LITERAL (e.g. "5m", "1h")
        dur_lit = self._get_token(node, "DURATION_LITERAL")
        if dur_lit:
            return f'"{dur_lit}"'

        return '"PT0S"'

    # ── Helpers ──

    def _emit(self, line: str) -> None:
        self._lines.append("    " * self._indent + line)

    def _get_token(self, node: Tree, token_type: str) -> str:
        for ch in node.children:
            if isinstance(ch, Token) and ch.type == token_type:
                return str(ch)
        return ""

    def _find_child(self, node: Tree, data: str) -> Tree | None:
        for ch in node.children:
            if isinstance(ch, Tree) and ch.data == data:
                return ch
        return None

    def _extract_cypher_vars(self, cypher: str) -> list[str]:
        """Extract bound variable names from a Cypher pattern."""
        node_vars = re.findall(r'\((\w+)(?::\w+)?(?:\s*\{[^}]*\})?\)', cypher)
        rel_vars = re.findall(r'\[(\w+):\w+', cypher)
        return list(dict.fromkeys(node_vars + rel_vars))


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def compile_rule(rule_text: str, rule_id: str = "unknown") -> str:
    """Parse and compile a GSL-Ops rule to Python source.

    Args:
        rule_text: GSL-Ops source text
        rule_id: Rule identifier

    Returns:
        Python source code string
    """
    tree = parse_rule(rule_text)
    compiler = GSLOpsCompiler(rule_id=rule_id)
    return compiler.compile(tree)
