"""Subset-OpenSCAD parser.

Produces a frozen AST from a `.scad` source string. Supports the subset
documented in `docs/plans/02-stage1-scad-spec.md`. Anything outside the
subset (e.g. `function`, `include`, `hull`, recursion) is rejected at
parse time with an actionable error.

The AST is plain dataclasses — no manifold, no env, no evaluation.
Evaluation lives in `scad_eval.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from lark import Lark, Transformer, v_args, exceptions as _lark_exc


# ---------------------------------------------------------------------------
# AST node types. All frozen + hashable so the diff layer can content-hash
# whole subtrees cheaply.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NumLit:
    value: float


@dataclass(frozen=True)
class StrLit:
    value: str


@dataclass(frozen=True)
class BoolLit:
    value: bool


@dataclass(frozen=True)
class VarRef:
    name: str  # may be "$fn" / "$fa" / "$fs"


@dataclass(frozen=True)
class Vector:
    elements: tuple["Expr", ...]


@dataclass(frozen=True)
class RangeLit:
    start: "Expr"
    end: "Expr"
    step: "Expr | None"  # None → step 1


@dataclass(frozen=True)
class BinOp:
    op: str  # "+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=", "&&", "||"
    lhs: "Expr"
    rhs: "Expr"


@dataclass(frozen=True)
class UnaryOp:
    op: str  # "-", "+", "!"
    operand: "Expr"


@dataclass(frozen=True)
class Conditional:
    cond: "Expr"
    then_e: "Expr"
    else_e: "Expr"


@dataclass(frozen=True)
class FunctionCall:
    name: str
    args: tuple["Arg", ...]


@dataclass(frozen=True)
class Indexing:
    target: "Expr"
    index: "Expr"


@dataclass(frozen=True)
class Arg:
    name: str | None  # None for positional
    value: "Expr"


@dataclass(frozen=True)
class LetBinding:
    name: str
    value: "Expr"


@dataclass(frozen=True)
class LetExpr:
    bindings: tuple[LetBinding, ...]
    body: "Expr"


Expr = Union[
    NumLit,
    StrLit,
    BoolLit,
    VarRef,
    Vector,
    RangeLit,
    BinOp,
    UnaryOp,
    Conditional,
    FunctionCall,
    Indexing,
    LetExpr,
]


# Statement types ------------------------------------------------------------


@dataclass(frozen=True)
class Assignment:
    name: str
    value: Expr


@dataclass(frozen=True)
class ParamSpec:
    name: str
    default: Expr | None


@dataclass(frozen=True)
class ModuleDef:
    name: str
    params: tuple[ParamSpec, ...]
    body: tuple["Stmt", ...]


@dataclass(frozen=True)
class ModuleCall:
    """A call to a module. As a statement, may carry children
    (other statements). As an expression child of another call, carries
    the children that follow it (e.g. translate(...) cube(...);).
    """

    callable: str
    args: tuple[Arg, ...]
    children: tuple["Stmt", ...] = ()


@dataclass(frozen=True)
class IfStmt:
    cond: Expr
    then_body: tuple["Stmt", ...]
    else_body: tuple["Stmt", ...]


@dataclass(frozen=True)
class ForStmt:
    var: str
    iterable: Expr
    body: tuple["Stmt", ...]


@dataclass(frozen=True)
class LetStmt:
    bindings: tuple[LetBinding, ...]
    body: tuple["Stmt", ...]


Stmt = Union[Assignment, ModuleDef, ModuleCall, IfStmt, ForStmt, LetStmt]


@dataclass(frozen=True)
class Source:
    stmts: tuple[Stmt, ...]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ScadParseError(Exception):
    """Raised when the source can't be parsed or uses an unsupported
    construct. Message is meant to be readable enough that the agent
    can fix and retry."""


# ---------------------------------------------------------------------------
# Grammar
# ---------------------------------------------------------------------------


# Keywords reserved so they don't match NAME.
_RESERVED = {
    "module",
    "function",
    "if",
    "else",
    "for",
    "let",
    "true",
    "false",
    "include",
    "use",
    "import",
    "each",
    "assert",
    "echo",
    "undef",
}


_GRAMMAR = r"""
start: stmt*

?stmt: assignment ";"
     | module_def
     | if_stmt
     | for_stmt
     | let_stmt
     | mod_call_stmt
     | block_stmt

assignment: name "=" expr

module_def: "module" CNAME "(" param_list? ")" block

param_list: param ("," param)*
param: CNAME ("=" expr)?

block: "{" stmt* "}"
?block_stmt: block

if_stmt: "if" "(" expr ")" body ("else" body)?
for_stmt: "for" "(" CNAME "=" expr ")" body
let_stmt: "let" "(" let_bindings ")" body

?body: block | stmt

mod_call_stmt: mod_call mod_tail
mod_call: CNAME "(" arg_list? ")"
?mod_tail: ";"                  -> empty_tail
        | block                 -> block_tail
        | mod_call_stmt         -> single_tail
        | if_stmt               -> single_tail
        | for_stmt              -> single_tail
        | let_stmt              -> single_tail

arg_list: arg ("," arg)*
?arg: expr                      -> arg_pos
    | CNAME "=" expr            -> arg_kw
    | SPECIAL_VAR "=" expr      -> arg_kw

// names: CNAME or special $-prefixed (only $fn / $fa / $fs allowed)
?name: CNAME -> bare_name
     | SPECIAL_VAR -> special_name

SPECIAL_VAR: /\$[a-zA-Z_][a-zA-Z0-9_]*/

// Expressions, precedence-climbing -----------------------------------------

?expr: ternary
?ternary: logic_or | logic_or "?" expr ":" expr -> cond
?logic_or: logic_and | logic_or "||" logic_and -> or_
?logic_and: equality | logic_and "&&" equality -> and_
?equality: comparison
         | equality "==" comparison -> eq
         | equality "!=" comparison -> neq
?comparison: addition
           | comparison "<" addition  -> lt
           | comparison "<=" addition -> le
           | comparison ">" addition  -> gt
           | comparison ">=" addition -> ge
?addition: multiplication
         | addition "+" multiplication -> add
         | addition "-" multiplication -> sub
?multiplication: unary
               | multiplication "*" unary -> mul
               | multiplication "/" unary -> div
               | multiplication "%" unary -> mod
?unary: postfix
      | "-" unary -> neg
      | "+" unary -> pos
      | "!" unary -> not_
?postfix: primary
        | postfix "[" expr "]" -> index

?primary: SIGNED_NUMBER         -> number
        | ESCAPED_STRING        -> string
        | "true"                -> true_lit
        | "false"               -> false_lit
        | SPECIAL_VAR           -> spv_ref
        | CNAME                 -> name_ref
        | "[" "]"               -> empty_vector
        | "[" range "]"         -> range_lit
        | "[" expr_list "]"     -> vector
        | "(" expr ")"
        | func_call
        | let_expr

range: expr ":" expr (":" expr)?

expr_list: expr ("," expr)*

func_call: CNAME "(" arg_list? ")"

let_expr: "let" "(" let_bindings ")" expr
let_bindings: let_binding ("," let_binding)*
let_binding: CNAME "=" expr

%import common.CNAME
%import common.SIGNED_NUMBER
%import common.ESCAPED_STRING
%import common.WS

%ignore WS
COMMENT_LINE:  /\/\/[^\n]*/
COMMENT_BLOCK: /\/\*(.|\n)*?\*\//
%ignore COMMENT_LINE
%ignore COMMENT_BLOCK
"""


# ---------------------------------------------------------------------------
# Transformer: lark Tree → AST.
# ---------------------------------------------------------------------------


@v_args(inline=True)
class _Builder(Transformer):
    # ---- top-level ----

    def start(self, *stmts: Stmt) -> Source:
        return Source(stmts=tuple(stmts))

    # ---- statements ----

    def assignment(self, name_token, value: Expr) -> Assignment:
        # name_token may already be a string (from bare_name / special_name)
        name = str(name_token)
        if name in _RESERVED:
            raise ScadParseError(f"reserved keyword cannot be used as variable name: {name!r}")
        return Assignment(name=name, value=value)

    def bare_name(self, tok) -> str:
        return str(tok)

    def special_name(self, tok) -> str:
        return str(tok)

    def module_def(self, name_token, *rest):
        # rest may be (param_list, block) or (block,)
        if len(rest) == 2:
            params, body_block = rest
            params_t = params
        else:
            params_t = ()
            body_block = rest[0]
        return ModuleDef(name=str(name_token), params=tuple(params_t), body=body_block)

    @v_args(inline=False)
    def param_list(self, items: list) -> tuple[ParamSpec, ...]:
        return tuple(items)

    def param(self, name_token, default=None) -> ParamSpec:
        return ParamSpec(name=str(name_token), default=default)

    @v_args(inline=False)
    def block(self, items: list) -> tuple[Stmt, ...]:
        return tuple(items)

    def if_stmt(self, cond: Expr, then_body, else_body=None) -> IfStmt:
        tb = then_body if isinstance(then_body, tuple) else (then_body,)
        eb = (
            else_body
            if isinstance(else_body, tuple)
            else (else_body,) if else_body is not None
            else ()
        )
        return IfStmt(cond=cond, then_body=tb, else_body=eb)

    def for_stmt(self, name_token, iterable: Expr, body) -> ForStmt:
        body_t = body if isinstance(body, tuple) else (body,)
        return ForStmt(var=str(name_token), iterable=iterable, body=body_t)

    def let_stmt(self, bindings, body) -> LetStmt:
        body_t = body if isinstance(body, tuple) else (body,)
        return LetStmt(bindings=tuple(bindings), body=body_t)

    # ---- module call statements ----

    def mod_call(self, name_token, arg_list=None) -> ModuleCall:
        return ModuleCall(
            callable=str(name_token),
            args=tuple(arg_list) if arg_list else (),
            children=(),
        )

    def mod_call_stmt(self, call: ModuleCall, tail) -> ModuleCall:
        # tail is one of: ("empty",), ("block", tuple), ("single", stmt)
        kind = tail[0]
        if kind == "empty":
            return call
        if kind == "block":
            return ModuleCall(callable=call.callable, args=call.args, children=tail[1])
        # single
        stmt = tail[1]
        return ModuleCall(callable=call.callable, args=call.args, children=(stmt,))

    def empty_tail(self) -> tuple:
        return ("empty",)

    def block_tail(self, body) -> tuple:
        return ("block", body)

    def single_tail(self, stmt) -> tuple:
        return ("single", stmt)

    @v_args(inline=False)
    def arg_list(self, items: list) -> tuple[Arg, ...]:
        return tuple(items)

    def arg_pos(self, value: Expr) -> Arg:
        return Arg(name=None, value=value)

    def arg_kw(self, name_token, value: Expr) -> Arg:
        return Arg(name=str(name_token), value=value)

    # ---- expressions ----

    def number(self, tok) -> NumLit:
        return NumLit(value=float(tok))

    def string(self, tok) -> StrLit:
        # tok includes surrounding quotes; strip them and unescape minimally.
        s = str(tok)
        return StrLit(value=s[1:-1].replace('\\"', '"').replace("\\\\", "\\"))

    def true_lit(self) -> BoolLit:
        return BoolLit(value=True)

    def false_lit(self) -> BoolLit:
        return BoolLit(value=False)

    def spv_ref(self, tok) -> VarRef:
        return VarRef(name=str(tok))

    def name_ref(self, tok) -> VarRef:
        name = str(tok)
        if name in _RESERVED:
            raise ScadParseError(f"reserved keyword used as expression: {name!r}")
        return VarRef(name=name)

    def empty_vector(self) -> Vector:
        return Vector(elements=())

    def vector(self, expr_list) -> Vector:
        return Vector(elements=tuple(expr_list))

    @v_args(inline=False)
    def expr_list(self, items: list) -> list:
        return items

    def range_lit(self, r) -> RangeLit:
        return r

    def range(self, *parts) -> RangeLit:
        if len(parts) == 2:
            return RangeLit(start=parts[0], end=parts[1], step=None)
        return RangeLit(start=parts[0], end=parts[2], step=parts[1])

    def func_call(self, name_token, arg_list=None) -> FunctionCall:
        return FunctionCall(name=str(name_token), args=tuple(arg_list) if arg_list else ())

    def let_expr(self, bindings, body: Expr) -> LetExpr:
        return LetExpr(bindings=tuple(bindings), body=body)

    @v_args(inline=False)
    def let_bindings(self, items: list) -> list:
        return items

    def let_binding(self, name_token, value: Expr) -> LetBinding:
        return LetBinding(name=str(name_token), value=value)

    # binary ops
    def add(self, a, b): return BinOp("+", a, b)
    def sub(self, a, b): return BinOp("-", a, b)
    def mul(self, a, b): return BinOp("*", a, b)
    def div(self, a, b): return BinOp("/", a, b)
    def mod(self, a, b): return BinOp("%", a, b)
    def eq(self, a, b): return BinOp("==", a, b)
    def neq(self, a, b): return BinOp("!=", a, b)
    def lt(self, a, b): return BinOp("<", a, b)
    def le(self, a, b): return BinOp("<=", a, b)
    def gt(self, a, b): return BinOp(">", a, b)
    def ge(self, a, b): return BinOp(">=", a, b)
    def or_(self, a, b): return BinOp("||", a, b)
    def and_(self, a, b): return BinOp("&&", a, b)

    # unary ops
    def neg(self, e): return UnaryOp("-", e)
    def pos(self, e): return UnaryOp("+", e)
    def not_(self, e): return UnaryOp("!", e)

    # postfix
    def index(self, target, idx): return Indexing(target=target, index=idx)

    # ternary
    def cond(self, c, t, e): return Conditional(cond=c, then_e=t, else_e=e)

# Lazy-built parser singleton.
_parser_cache: Lark | None = None


def _get_parser() -> Lark:
    global _parser_cache
    if _parser_cache is None:
        _parser_cache = Lark(
            _GRAMMAR,
            parser="earley",
            propagate_positions=False,
            maybe_placeholders=False,
        )
    return _parser_cache


_BANNED_KEYWORD_HINTS = (
    ("function ", "`function` definitions are not supported in fastcad's OpenSCAD subset. Use a `module` instead, or inline the expression."),
    ("include <", "`include` is not supported (no external libraries). Inline geometry or define modules directly."),
    ("use <", "`use` is not supported (no external libraries). Inline geometry or define modules directly."),
    ("import(", "`import` is not supported in v1. Use polyhedron(...) for raw mesh data."),
    ("hull(", "`hull()` is out of v1 scope. Build the geometry from primitives + booleans."),
    ("minkowski(", "`minkowski()` is out of v1 scope."),
    ("offset(", "`offset()` is out of v1 scope."),
    ("projection(", "`projection()` is out of v1 scope."),
    ("text(", "`text()` is out of v1 scope."),
    ("surface(", "`surface()` is out of v1 scope."),
)


def _check_banned_constructs(source: str) -> None:
    """Cheap pre-parse pass that catches a few v1-out-of-scope tokens
    and produces friendlier errors than the bare grammar would. Skips
    matches that fall inside string literals or comments."""
    stripped = _strip_comments_and_strings(source)
    low = stripped
    for needle, hint in _BANNED_KEYWORD_HINTS:
        idx = low.find(needle)
        if idx == -1:
            continue
        # Make sure the needle starts at a token boundary.
        if idx > 0 and (low[idx - 1].isalnum() or low[idx - 1] == "_"):
            continue
        raise ScadParseError(hint)


def _strip_comments_and_strings(source: str) -> str:
    """Replace contents of // line comments, /* */ block comments, and
    "..." string literals with spaces so banned-keyword detection
    doesn't fire on them. Preserves length and line offsets so any
    diagnostic referring to byte offsets stays usable."""
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        # Line comment
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            j = source.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
            continue
        # Block comment
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            j = source.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(" " * (j - i))
            i = j
            continue
        # String literal
        if ch == '"':
            j = i + 1
            while j < n and source[j] != '"':
                if source[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                j += 1
            if j < n:
                j += 1  # consume closing "
            out.append(" " * (j - i))
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse(source: str) -> Source:
    """Parse `.scad` source into an AST. Raises `ScadParseError` on
    syntax / unsupported-construct errors with a readable message."""
    _check_banned_constructs(source)
    parser = _get_parser()
    try:
        tree = parser.parse(source)
    except _lark_exc.UnexpectedInput as exc:
        raise ScadParseError(_format_lark_error(source, exc)) from None
    try:
        ast = _Builder().transform(tree)
    except _lark_exc.VisitError as exc:
        # Unwrap to the inner cause.
        if isinstance(exc.orig_exc, ScadParseError):
            raise exc.orig_exc from None
        raise ScadParseError(str(exc.orig_exc)) from None
    if not isinstance(ast, Source):
        raise ScadParseError(f"internal: parser produced {type(ast).__name__}, expected Source")
    return ast


def _format_lark_error(source: str, exc: _lark_exc.UnexpectedInput) -> str:
    line = getattr(exc, "line", "?")
    col = getattr(exc, "column", "?")
    snippet = ""
    try:
        snippet = exc.get_context(source, span=40).strip()
    except Exception:
        pass
    if snippet:
        return f"parse error at line {line}, col {col}:\n{snippet}"
    return f"parse error at line {line}, col {col}: {exc}"


__all__ = [
    "parse",
    "ScadParseError",
    # AST exports for downstream modules:
    "Source",
    "Assignment",
    "ModuleDef",
    "ModuleCall",
    "ParamSpec",
    "IfStmt",
    "ForStmt",
    "LetStmt",
    "LetBinding",
    "Stmt",
    "Expr",
    "NumLit",
    "StrLit",
    "BoolLit",
    "VarRef",
    "Vector",
    "RangeLit",
    "BinOp",
    "UnaryOp",
    "Conditional",
    "FunctionCall",
    "Indexing",
    "Arg",
    "LetExpr",
]
