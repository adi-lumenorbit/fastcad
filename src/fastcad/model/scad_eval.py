"""Spec evaluator: parsed AST → per-top-level-call ModuleEval.

The evaluator walks the AST and dispatches to `kernel.py` for geometry.
It maintains a flat env (variables + module definitions) and a notion of
2D vs 3D values (CrossSection for 2D, Manifold for 3D). Most CSG /
transform / extrude operations dispatch on the value's type.

`evaluate_source(src)` returns `dict[node_id → ModuleEval]` covering
every top-level geometry-producing statement. `node_id` is the module
name when the statement is a single named-module call (the agent's
intended convention) or `_top_<n>` for anonymous top-level expressions.

Content hashes per ModuleEval encode (the call AST, every reachable
top-level Assignment, every reachable ModuleDef) so the diff layer can
reuse cached evals when a change doesn't touch them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import manifold3d as _m3

from . import kernel as k
from .scad_faces import faces_for
from .scad_parser import (
    Arg,
    Assignment,
    BinOp,
    BoolLit,
    Conditional,
    Expr,
    ForStmt,
    FunctionCall,
    IfStmt,
    Indexing,
    LetExpr,
    LetStmt,
    ModuleCall,
    ModuleDef,
    NumLit,
    RangeLit,
    Source,
    Stmt,
    StrLit,
    UnaryOp,
    VarRef,
    Vector,
)


class EvalError(Exception):
    """Raised when the source parses but cannot be evaluated (unknown
    module, bad arg type, recursion, etc.). Message is intended to be
    surfaced to the agent verbatim."""


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

# Python-side runtime values used during expression evaluation.
# - Numeric: int / float (we coerce ints from literals into floats lazily).
# - Boolean
# - String
# - Vector: tuple of values.
# - Range: a (start, end, step) triple used by `for`.
# - None for "undef".
# - Geometry values are Manifold or CrossSection, surfaced only by
#   geometry-producing statements; expressions don't see them.

Value = Any  # too dynamic to type usefully


# Geometry produced by a statement.
Geometry = "_m3.Manifold | _m3.CrossSection | None"


@dataclass(frozen=True)
class FacePoint:
    point: tuple[float, float, float]
    normal: tuple[float, float, float]


@dataclass
class ModuleEval:
    node_id: str
    manifold: Any  # _m3.Manifold | None
    bbox: k.BBox | None
    faces: dict[str, FacePoint]
    content_hash: int


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


@dataclass
class Env:
    vars: dict[str, Value] = field(default_factory=dict)
    modules: dict[str, ModuleDef] = field(default_factory=dict)
    # Assignments by name, kept as AST nodes for content-hashing
    var_defs: dict[str, Assignment] = field(default_factory=dict)
    # Currently-executing module names (cycle detection)
    call_stack: tuple[str, ...] = ()

    def child(self, **bindings) -> "Env":
        new_vars = dict(self.vars)
        new_vars.update(bindings)
        return Env(
            vars=new_vars,
            modules=self.modules,
            var_defs=self.var_defs,
            call_stack=self.call_stack,
        )

    def push_call(self, name: str) -> "Env":
        if name in self.call_stack:
            raise EvalError(
                f"recursive module call detected: {' → '.join(self.call_stack + (name,))}. "
                "Recursion is not supported in v1."
            )
        return Env(
            vars=dict(self.vars),
            modules=self.modules,
            var_defs=self.var_defs,
            call_stack=self.call_stack + (name,),
        )


# ---------------------------------------------------------------------------
# Built-in functions (used inside expressions)
# ---------------------------------------------------------------------------


def _to_num(v: Value, ctx: str = "") -> float:
    if isinstance(v, bool):  # bool is a subclass of int — exclude
        raise EvalError(f"expected number{(' for ' + ctx) if ctx else ''}, got bool")
    if isinstance(v, (int, float)):
        return float(v)
    raise EvalError(f"expected number{(' for ' + ctx) if ctx else ''}, got {type(v).__name__}")


def _builtin_funcs() -> dict[str, Any]:
    def _fn(name: str, fn):
        return name, fn

    funcs = dict([
        _fn("sin", lambda x: math.sin(math.radians(_to_num(x, "sin")))),
        _fn("cos", lambda x: math.cos(math.radians(_to_num(x, "cos")))),
        _fn("tan", lambda x: math.tan(math.radians(_to_num(x, "tan")))),
        _fn("asin", lambda x: math.degrees(math.asin(_to_num(x, "asin")))),
        _fn("acos", lambda x: math.degrees(math.acos(_to_num(x, "acos")))),
        _fn("atan", lambda x: math.degrees(math.atan(_to_num(x, "atan")))),
        _fn("atan2", lambda y, x: math.degrees(math.atan2(_to_num(y, "atan2"), _to_num(x, "atan2")))),
        _fn("sqrt", lambda x: math.sqrt(_to_num(x, "sqrt"))),
        _fn("pow", lambda b, e: math.pow(_to_num(b, "pow"), _to_num(e, "pow"))),
        _fn("exp", lambda x: math.exp(_to_num(x, "exp"))),
        _fn("ln", lambda x: math.log(_to_num(x, "ln"))),
        _fn("log", lambda x: math.log10(_to_num(x, "log"))),
        _fn("abs", lambda x: abs(_to_num(x, "abs"))),
        _fn("min", _min_var),
        _fn("max", _max_var),
        _fn("floor", lambda x: math.floor(_to_num(x, "floor"))),
        _fn("ceil", lambda x: math.ceil(_to_num(x, "ceil"))),
        _fn("round", lambda x: round(_to_num(x, "round"))),
        _fn("len", _len_value),
        _fn("concat", _concat_values),
        _fn("norm", _norm_value),
        _fn("cross", _cross_value),
    ])
    return funcs


def _min_var(*args):
    if len(args) == 1 and isinstance(args[0], tuple):
        return min(_to_num(x, "min") for x in args[0])
    return min(_to_num(x, "min") for x in args)


def _max_var(*args):
    if len(args) == 1 and isinstance(args[0], tuple):
        return max(_to_num(x, "max") for x in args[0])
    return max(_to_num(x, "max") for x in args)


def _len_value(v):
    if isinstance(v, (tuple, list, str)):
        return len(v)
    raise EvalError(f"len() on non-vector/string value: {type(v).__name__}")


def _concat_values(*vs):
    out: list[Value] = []
    for v in vs:
        if isinstance(v, tuple):
            out.extend(v)
        else:
            out.append(v)
    return tuple(out)


def _norm_value(v):
    if not isinstance(v, tuple):
        raise EvalError("norm() requires a vector")
    s = sum(_to_num(x, "norm") ** 2 for x in v)
    return math.sqrt(s)


def _cross_value(a, b):
    if not (isinstance(a, tuple) and isinstance(b, tuple) and len(a) == 3 and len(b) == 3):
        raise EvalError("cross() requires two 3-vectors")
    ax, ay, az = (_to_num(a[0]), _to_num(a[1]), _to_num(a[2]))
    bx, by, bz = (_to_num(b[0]), _to_num(b[1]), _to_num(b[2]))
    return (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)


_BUILTIN_FUNCS = _builtin_funcs()
_BUILTIN_CONSTS: dict[str, Value] = {"PI": math.pi, "undef": None}


# ---------------------------------------------------------------------------
# Expression evaluation
# ---------------------------------------------------------------------------


def eval_expr(e: Expr, env: Env) -> Value:
    if isinstance(e, NumLit):
        return float(e.value)
    if isinstance(e, StrLit):
        return e.value
    if isinstance(e, BoolLit):
        return e.value
    if isinstance(e, VarRef):
        if e.name in env.vars:
            return env.vars[e.name]
        if e.name in _BUILTIN_CONSTS:
            return _BUILTIN_CONSTS[e.name]
        raise EvalError(f"unknown variable: {e.name!r}")
    if isinstance(e, Vector):
        return tuple(eval_expr(c, env) for c in e.elements)
    if isinstance(e, RangeLit):
        start = _to_num(eval_expr(e.start, env), "range start")
        end = _to_num(eval_expr(e.end, env), "range end")
        step = _to_num(eval_expr(e.step, env), "range step") if e.step is not None else 1.0
        return _Range(start=start, end=end, step=step)
    if isinstance(e, BinOp):
        return _eval_binop(e, env)
    if isinstance(e, UnaryOp):
        v = eval_expr(e.operand, env)
        if e.op == "-":
            return -_to_num(v, "unary -")
        if e.op == "+":
            return _to_num(v, "unary +")
        if e.op == "!":
            return not bool(v)
        raise EvalError(f"unknown unary op: {e.op!r}")
    if isinstance(e, Conditional):
        return eval_expr(e.then_e, env) if bool(eval_expr(e.cond, env)) else eval_expr(e.else_e, env)
    if isinstance(e, FunctionCall):
        if e.name not in _BUILTIN_FUNCS:
            raise EvalError(f"unknown function: {e.name!r}")
        # All args are positional in built-ins (no kw-only).
        args = [eval_expr(a.value, env) for a in e.args]
        try:
            return _BUILTIN_FUNCS[e.name](*args)
        except EvalError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EvalError(f"in {e.name}(...): {exc}") from None
    if isinstance(e, Indexing):
        target = eval_expr(e.target, env)
        idx = int(_to_num(eval_expr(e.index, env), "index"))
        if isinstance(target, (tuple, list, str)):
            try:
                return target[idx]
            except IndexError:
                raise EvalError(f"index {idx} out of range for {len(target)}-element value")
        raise EvalError(f"cannot index into {type(target).__name__}")
    if isinstance(e, LetExpr):
        scope = env
        for binding in e.bindings:
            scope = scope.child(**{binding.name: eval_expr(binding.value, scope)})
        return eval_expr(e.body, scope)
    raise EvalError(f"unknown expression node: {type(e).__name__}")


@dataclass
class _Range:
    start: float
    end: float
    step: float

    def values(self) -> list[float]:
        out: list[float] = []
        step = self.step if self.step != 0 else 1.0
        if step > 0:
            v = self.start
            while v <= self.end + 1e-9:
                out.append(v)
                v += step
        else:
            v = self.start
            while v >= self.end - 1e-9:
                out.append(v)
                v += step
        return out


def _eval_binop(e: BinOp, env: Env) -> Value:
    a = eval_expr(e.lhs, env)
    b = eval_expr(e.rhs, env)
    op = e.op
    if op == "+":
        return _arith(a, b, lambda x, y: x + y, vec=lambda x, y: tuple(_to_num(p) + _to_num(q) for p, q in zip(x, y)))
    if op == "-":
        return _arith(a, b, lambda x, y: x - y, vec=lambda x, y: tuple(_to_num(p) - _to_num(q) for p, q in zip(x, y)))
    if op == "*":
        # Vector * scalar → scaled vector; scalar * vector likewise.
        if isinstance(a, tuple) and not isinstance(b, tuple):
            sb = _to_num(b)
            return tuple(_to_num(p) * sb for p in a)
        if isinstance(b, tuple) and not isinstance(a, tuple):
            sa = _to_num(a)
            return tuple(_to_num(p) * sa for p in b)
        return _to_num(a) * _to_num(b)
    if op == "/":
        if isinstance(a, tuple):
            sb = _to_num(b)
            return tuple(_to_num(p) / sb for p in a)
        return _to_num(a) / _to_num(b)
    if op == "%":
        return _to_num(a) % _to_num(b)
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == "<":
        return _to_num(a) < _to_num(b)
    if op == "<=":
        return _to_num(a) <= _to_num(b)
    if op == ">":
        return _to_num(a) > _to_num(b)
    if op == ">=":
        return _to_num(a) >= _to_num(b)
    if op == "&&":
        return bool(a) and bool(b)
    if op == "||":
        return bool(a) or bool(b)
    raise EvalError(f"unknown binary op: {op!r}")


def _arith(a, b, scalar_op, vec):
    if isinstance(a, tuple) and isinstance(b, tuple):
        if len(a) != len(b):
            raise EvalError(f"vector size mismatch: {len(a)} vs {len(b)}")
        return vec(a, b)
    return scalar_op(_to_num(a), _to_num(b))


# ---------------------------------------------------------------------------
# Statement evaluation
# ---------------------------------------------------------------------------


def eval_stmts(stmts: Sequence[Stmt], env: Env) -> Geometry:
    """Evaluate a sequence of statements and return the implicit union
    of their geometry. Returns None if no geometry was produced.

    Inside a module body, assignments and module defs leak into the
    local scope (we mutate `env.vars` / `env.modules` for the duration).
    """
    geometries: list[Any] = []
    for s in stmts:
        g = eval_single_stmt(s, env)
        if g is not None:
            geometries.append(g)
    return _implicit_union(geometries)


def eval_single_stmt(s: Stmt, env: Env) -> Geometry:
    if isinstance(s, Assignment):
        env.vars[s.name] = eval_expr(s.value, env)
        env.var_defs[s.name] = s
        return None
    if isinstance(s, ModuleDef):
        env.modules[s.name] = s
        return None
    if isinstance(s, ModuleCall):
        return eval_module_call(s, env)
    if isinstance(s, IfStmt):
        cond = eval_expr(s.cond, env)
        body = s.then_body if cond else s.else_body
        return eval_stmts(body, env)
    if isinstance(s, ForStmt):
        rng_val = eval_expr(s.iterable, env)
        items: Iterable
        if isinstance(rng_val, _Range):
            items = rng_val.values()
        elif isinstance(rng_val, tuple):
            items = rng_val
        else:
            raise EvalError(
                f"for(...) iterable must be a range or vector, got {type(rng_val).__name__}"
            )
        geometries: list[Any] = []
        for v in items:
            scope = env.child(**{s.var: v})
            g = eval_stmts(s.body, scope)
            if g is not None:
                geometries.append(g)
        return _implicit_union(geometries)
    if isinstance(s, LetStmt):
        scope = env
        for binding in s.bindings:
            scope = scope.child(**{binding.name: eval_expr(binding.value, scope)})
        return eval_stmts(s.body, scope)
    raise EvalError(f"unknown statement node: {type(s).__name__}")


# ---------------------------------------------------------------------------
# Module call dispatch (built-ins + user-defined)
# ---------------------------------------------------------------------------


def eval_module_call(call: ModuleCall, env: Env) -> Geometry:
    name = call.callable
    if name in _BUILTIN_MODULES:
        return _BUILTIN_MODULES[name](call, env)
    if name in env.modules:
        return _eval_user_module_call(call, env)
    raise EvalError(f"unknown module: {name!r}")


def _resolve_args(call: ModuleCall, env: Env) -> dict[str, Value]:
    """Evaluate all args and return a dict keyed by their declared name.
    For built-ins we don't have a param schema, so positional args are
    keyed by integer indices: 0, 1, 2, ... Kwargs go by their string name.
    """
    out: dict[Any, Value] = {}
    pos_idx = 0
    for a in call.args:
        v = eval_expr(a.value, env)
        if a.name is None:
            out[pos_idx] = v
            pos_idx += 1
        else:
            out[a.name] = v
    return out


def _bind_user_args(call: ModuleCall, defn: ModuleDef, env: Env) -> dict[str, Value]:
    """Bind positional + keyword call args to the module's declared
    parameter names. Defaults applied for missing params."""
    bindings: dict[str, Value] = {}
    pos_iter = iter(call.args)
    used_names: set[str] = set()
    # Positional first.
    name_iter = iter(defn.params)
    for arg in pos_iter:
        if arg.name is not None:
            # Once we hit a kwarg, the remainder must all be kwargs.
            kwargs_args = [arg, *pos_iter]
            for ka in kwargs_args:
                if ka.name is None:
                    raise EvalError(f"positional arg after keyword arg in call to {call.callable!r}")
                if ka.name not in {p.name for p in defn.params}:
                    raise EvalError(f"unknown parameter {ka.name!r} for module {call.callable!r}")
                if ka.name in used_names:
                    raise EvalError(f"duplicate keyword arg {ka.name!r}")
                bindings[ka.name] = eval_expr(ka.value, env)
                used_names.add(ka.name)
            break
        try:
            param = next(name_iter)
        except StopIteration:
            raise EvalError(
                f"too many positional args to module {call.callable!r}: "
                f"expected {len(defn.params)}"
            ) from None
        bindings[param.name] = eval_expr(arg.value, env)
        used_names.add(param.name)
    # Apply defaults for unbound params; error if no default and no value.
    for p in defn.params:
        if p.name in bindings:
            continue
        if p.default is None:
            raise EvalError(f"missing required parameter {p.name!r} for module {call.callable!r}")
        bindings[p.name] = eval_expr(p.default, env)
    return bindings


def _eval_user_module_call(call: ModuleCall, env: Env) -> Geometry:
    defn = env.modules[call.callable]
    bindings = _bind_user_args(call, defn, env)
    inner = env.push_call(call.callable).child(**bindings)
    # Children of the call become the module's `children` block — but
    # for v1 we don't support `children()` references inside modules;
    # rather, the module's own body produces its geometry. If the call
    # was written with children (e.g. `screw() { sphere(); }`), warn
    # and ignore.
    if call.children:
        # Implicit-union the children themselves and union with the
        # module body — matches OpenSCAD's behavior for modules that
        # don't reference `children()`. Practical for the agent.
        body_geom = eval_stmts(defn.body, inner)
        children_geom = eval_stmts(call.children, env)
        return _implicit_union([g for g in (body_geom, children_geom) if g is not None])
    return eval_stmts(defn.body, inner)


# ---------------------------------------------------------------------------
# Built-in modules
# ---------------------------------------------------------------------------


def _builtin_cube(call: ModuleCall, env: Env) -> Geometry:
    args = _resolve_args(call, env)
    size_v = args.get("size", args.get(0, [1, 1, 1]))
    center = bool(args.get("center", args.get(1, False)))
    if isinstance(size_v, (int, float)):
        size = (float(size_v),) * 3
    elif isinstance(size_v, tuple) and len(size_v) == 3:
        size = (_to_num(size_v[0]), _to_num(size_v[1]), _to_num(size_v[2]))
    else:
        raise EvalError("cube size must be a number or 3-vector")
    return k.cube(size, center=center)


def _builtin_sphere(call: ModuleCall, env: Env) -> Geometry:
    args = _resolve_args(call, env)
    if "r" in args:
        radius = _to_num(args["r"], "sphere r")
    elif "d" in args:
        radius = _to_num(args["d"], "sphere d") / 2.0
    elif 0 in args:
        radius = _to_num(args[0], "sphere r")
    else:
        raise EvalError("sphere requires r or d")
    segments = int(_to_num(args.get("$fn", env.vars.get("$fn", 32))))
    return k.sphere(radius, segments=max(3, segments))


def _builtin_cylinder(call: ModuleCall, env: Env) -> Geometry:
    args = _resolve_args(call, env)
    height = _to_num(args.get("h", args.get(0, 1)), "cylinder h")
    if "r" in args:
        radius = _to_num(args["r"], "cylinder r")
    elif "d" in args:
        radius = _to_num(args["d"], "cylinder d") / 2.0
    elif "r1" in args or "r2" in args:
        # Conic; simplification: use min of r1, r2 (manifold3d.cylinder
        # supports tapers via -1 default; we'd need the underlying call).
        # Defer cone support to a follow-up; for now use r1 as the radius.
        radius = _to_num(args.get("r1", args.get("r2", 1)), "cylinder r1/r2")
    else:
        radius = _to_num(args.get(1, 1), "cylinder r")
    segments = int(_to_num(args.get("$fn", env.vars.get("$fn", 32))))
    center = bool(args.get("center", False))
    return k.cylinder(height, radius, segments=max(3, segments), center=center)


def _builtin_circle(call: ModuleCall, env: Env) -> Geometry:
    args = _resolve_args(call, env)
    if "r" in args:
        radius = _to_num(args["r"], "circle r")
    elif "d" in args:
        radius = _to_num(args["d"], "circle d") / 2.0
    elif 0 in args:
        radius = _to_num(args[0], "circle r")
    else:
        raise EvalError("circle requires r or d")
    segments = int(_to_num(args.get("$fn", env.vars.get("$fn", 32))))
    return _m3.CrossSection.circle(radius, max(3, segments))


def _builtin_square(call: ModuleCall, env: Env) -> Geometry:
    args = _resolve_args(call, env)
    size_v = args.get("size", args.get(0, 1))
    center = bool(args.get("center", args.get(1, False)))
    if isinstance(size_v, (int, float)):
        sx = sy = float(size_v)
    elif isinstance(size_v, tuple) and len(size_v) == 2:
        sx, sy = _to_num(size_v[0]), _to_num(size_v[1])
    else:
        raise EvalError("square size must be number or 2-vector")
    return _m3.CrossSection.square([sx, sy], center)


def _builtin_polygon(call: ModuleCall, env: Env) -> Geometry:
    args = _resolve_args(call, env)
    points_v = args.get("points", args.get(0))
    if not isinstance(points_v, tuple):
        raise EvalError("polygon points must be a vector of [x,y] pairs")
    pts: list[tuple[float, float]] = []
    for p in points_v:
        if not (isinstance(p, tuple) and len(p) == 2):
            raise EvalError("polygon point must be [x, y]")
        pts.append((_to_num(p[0]), _to_num(p[1])))
    if len(pts) < 3:
        raise EvalError("polygon requires at least 3 points")
    return _m3.CrossSection([pts])


def _builtin_polyhedron(call: ModuleCall, env: Env) -> Geometry:
    args = _resolve_args(call, env)
    points_v = args.get("points", args.get(0))
    faces_v = args.get("faces", args.get(1))
    if not isinstance(points_v, tuple):
        raise EvalError("polyhedron points must be a vector of [x,y,z] triples")
    if not isinstance(faces_v, tuple):
        raise EvalError("polyhedron faces must be a vector of index lists")
    verts = [(_to_num(p[0]), _to_num(p[1]), _to_num(p[2])) for p in points_v]
    faces = [[int(_to_num(i)) for i in f] for f in faces_v]
    return k.polyhedron_from_mesh(verts, faces)


# Transforms accept a single child or a block.
def _builtin_translate(call: ModuleCall, env: Env) -> Geometry:
    args = _resolve_args(call, env)
    v = args.get("v", args.get(0))
    if not (isinstance(v, tuple) and len(v) == 3):
        raise EvalError("translate requires a 3-vector")
    child = eval_stmts(call.children, env)
    return _apply_translate(child, (_to_num(v[0]), _to_num(v[1]), _to_num(v[2])))


def _builtin_rotate(call: ModuleCall, env: Env) -> Geometry:
    args = _resolve_args(call, env)
    a = args.get("a", args.get(0))
    if isinstance(a, tuple) and len(a) == 3:
        rot = (_to_num(a[0]), _to_num(a[1]), _to_num(a[2]))
    else:
        # rotate(angle) → angle around Z
        rot = (0.0, 0.0, _to_num(a, "rotate angle"))
    child = eval_stmts(call.children, env)
    return _apply_rotate(child, rot)


def _builtin_scale(call: ModuleCall, env: Env) -> Geometry:
    args = _resolve_args(call, env)
    v = args.get("v", args.get(0))
    if isinstance(v, (int, float)):
        s = (float(v), float(v), float(v))
    elif isinstance(v, tuple) and len(v) == 3:
        s = (_to_num(v[0]), _to_num(v[1]), _to_num(v[2]))
    elif isinstance(v, tuple) and len(v) == 2:
        s = (_to_num(v[0]), _to_num(v[1]), 1.0)
    else:
        raise EvalError("scale requires a number or 2/3-vector")
    child = eval_stmts(call.children, env)
    return _apply_scale(child, s)


def _builtin_mirror(call: ModuleCall, env: Env) -> Geometry:
    args = _resolve_args(call, env)
    v = args.get("v", args.get(0))
    if not (isinstance(v, tuple) and len(v) == 3):
        raise EvalError("mirror requires a 3-vector normal")
    normal = (_to_num(v[0]), _to_num(v[1]), _to_num(v[2]))
    child = eval_stmts(call.children, env)
    return _apply_mirror(child, normal)


def _builtin_linear_extrude(call: ModuleCall, env: Env) -> Geometry:
    args = _resolve_args(call, env)
    height = _to_num(args.get("height", args.get(0, 1)), "linear_extrude height")
    twist = _to_num(args.get("twist", 0.0), "linear_extrude twist")
    scale_v = args.get("scale", (1.0, 1.0))
    if isinstance(scale_v, (int, float)):
        scale_top = (float(scale_v), float(scale_v))
    elif isinstance(scale_v, tuple) and len(scale_v) == 2:
        scale_top = (_to_num(scale_v[0]), _to_num(scale_v[1]))
    else:
        scale_top = (1.0, 1.0)

    # Twist resolution: prefer the explicit `slices` arg (real OpenSCAD's
    # primary control). Fall back to `$fn` (the call-local one beats the
    # global), then auto-pick from twist magnitude. With twist=0 we use
    # 0 divisions (manifold3d's default — single slice is sufficient
    # without rotation).
    if twist == 0:
        n_div = 0
    elif "slices" in args:
        n_div = max(1, int(_to_num(args["slices"], "linear_extrude slices")))
    else:
        local_fn = args.get("$fn")
        if local_fn is not None:
            local_fn_val = int(_to_num(local_fn, "linear_extrude $fn"))
        else:
            local_fn_val = int(_to_num(env.vars.get("$fn", 0)))
        # `$fn=1` in real OpenSCAD's linear_extrude is a no-op, not a
        # demand for one slice — treat it as "use the auto default."
        if local_fn_val > 1:
            n_div = local_fn_val
        else:
            # Auto: ~5° per slice, clamped to a reasonable range. Keeps
            # tight helices smooth without explosively-many triangles.
            n_div = max(32, min(2048, int(abs(twist) / 5)))

    child = eval_stmts(call.children, env)
    if child is None:
        raise EvalError("linear_extrude requires a 2D child")
    if not isinstance(child, _m3.CrossSection):
        raise EvalError("linear_extrude child must be 2D, got 3D manifold")
    polygons = child.to_polygons()
    return k.extrude_polygon(
        polygons, height=height, twist_deg=twist, n_divisions=n_div, scale_top=scale_top
    )


def _builtin_rotate_extrude(call: ModuleCall, env: Env) -> Geometry:
    args = _resolve_args(call, env)
    angle = _to_num(args.get("angle", 360.0), "rotate_extrude angle")
    fn = int(_to_num(args.get("$fn", env.vars.get("$fn", 64))))
    child = eval_stmts(call.children, env)
    if child is None:
        raise EvalError("rotate_extrude requires a 2D child")
    if not isinstance(child, _m3.CrossSection):
        raise EvalError("rotate_extrude child must be 2D, got 3D manifold")
    polygons = child.to_polygons()
    return k.revolve_polygon(polygons, segments=max(3, fn), revolve_deg=angle)


def _builtin_union(call: ModuleCall, env: Env) -> Geometry:
    return eval_stmts(call.children, env)  # implicit union


def _builtin_difference(call: ModuleCall, env: Env) -> Geometry:
    items: list[Any] = []
    for c in call.children:
        g = eval_single_stmt(c, env)
        if g is not None:
            items.append(g)
    if not items:
        return None
    base = items[0]
    for o in items[1:]:
        base = _apply_difference(base, o)
    return base


def _builtin_intersection(call: ModuleCall, env: Env) -> Geometry:
    items: list[Any] = []
    for c in call.children:
        g = eval_single_stmt(c, env)
        if g is not None:
            items.append(g)
    if not items:
        return None
    base = items[0]
    for o in items[1:]:
        base = _apply_intersection(base, o)
    return base


_BUILTIN_MODULES = {
    "cube": _builtin_cube,
    "sphere": _builtin_sphere,
    "cylinder": _builtin_cylinder,
    "circle": _builtin_circle,
    "square": _builtin_square,
    "polygon": _builtin_polygon,
    "polyhedron": _builtin_polyhedron,
    "translate": _builtin_translate,
    "rotate": _builtin_rotate,
    "scale": _builtin_scale,
    "mirror": _builtin_mirror,
    "linear_extrude": _builtin_linear_extrude,
    "rotate_extrude": _builtin_rotate_extrude,
    "union": _builtin_union,
    "difference": _builtin_difference,
    "intersection": _builtin_intersection,
}


# ---------------------------------------------------------------------------
# Geometry helpers (work polymorphically on Manifold | CrossSection)
# ---------------------------------------------------------------------------


def _is_2d(g) -> bool:
    return isinstance(g, _m3.CrossSection)


def _apply_translate(g, v: tuple[float, float, float]):
    if g is None:
        return None
    if _is_2d(g):
        return g.translate([v[0], v[1]])
    return g.translate(list(v))


def _apply_rotate(g, rot_xyz_deg: tuple[float, float, float]):
    if g is None:
        return None
    if _is_2d(g):
        # Only Z rotation is meaningful for 2D.
        return g.rotate(rot_xyz_deg[2])
    return g.rotate(list(rot_xyz_deg))


def _apply_scale(g, s: tuple[float, float, float]):
    if g is None:
        return None
    if _is_2d(g):
        return g.scale([s[0], s[1]])
    return g.scale(list(s))


def _apply_mirror(g, normal: tuple[float, float, float]):
    if g is None:
        return None
    if _is_2d(g):
        return g.mirror([normal[0], normal[1]])
    return g.mirror(list(normal))


def _apply_difference(a, b):
    if a is None:
        return None
    if b is None:
        return a
    if _is_2d(a) and _is_2d(b):
        return a - b
    if not _is_2d(a) and not _is_2d(b):
        return a - b
    raise EvalError("cannot mix 2D and 3D in difference")


def _apply_intersection(a, b):
    if a is None or b is None:
        return None
    if _is_2d(a) and _is_2d(b):
        return a ^ b
    if not _is_2d(a) and not _is_2d(b):
        return a ^ b
    raise EvalError("cannot mix 2D and 3D in intersection")


def _apply_union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if _is_2d(a) and _is_2d(b):
        return a + b
    if not _is_2d(a) and not _is_2d(b):
        return a + b
    raise EvalError("cannot mix 2D and 3D in union")


def _implicit_union(items: list) -> Geometry:
    out = None
    for g in items:
        if g is None:
            continue
        if out is None:
            out = g
            continue
        out = _apply_union(out, g)
    return out


# ---------------------------------------------------------------------------
# Top-level entry: source → ModuleEval map
# ---------------------------------------------------------------------------


def evaluate_source(src: Source) -> dict[str, ModuleEval]:
    """Top-level pipeline.

    1. Two-pass over top-level statements:
       - First pass: collect Assignments + ModuleDefs into env (in source order).
       - Second pass: evaluate geometry-producing statements; assign node ids.
    """
    env = Env()
    geometry_stmts: list[tuple[str, Stmt]] = []
    counter = 0
    for s in src.stmts:
        if isinstance(s, Assignment):
            env.vars[s.name] = eval_expr(s.value, env)
            env.var_defs[s.name] = s
            continue
        if isinstance(s, ModuleDef):
            env.modules[s.name] = s
            continue
        # Geometry-producing top-level statement.
        node_id = _node_id_for_top_level(s, counter)
        counter += 1
        geometry_stmts.append((node_id, s))

    out: dict[str, ModuleEval] = {}
    for node_id, s in geometry_stmts:
        geom = eval_single_stmt(s, env)
        if geom is None:
            continue
        if _is_2d(geom):
            # 2D top-level: skip with no error (they don't render in 3D).
            continue
        manifold = geom
        bbox = k.BBox.from_manifold(manifold)
        faces = faces_for(s, bbox, env)
        h = _content_hash(s, env)
        out[node_id] = ModuleEval(
            node_id=node_id,
            manifold=manifold,
            bbox=bbox,
            faces=faces,
            content_hash=h,
        )
    return out


def evaluate_top_level(
    src: Source,
    only_node_ids: set[str],
) -> dict[str, ModuleEval]:
    """Evaluate only a subset of top-level statements (by node_id),
    used by the spec_diff layer to avoid re-evaluating cache hits.
    Assignments + module defs are still processed in full (they're
    cheap and required for env). Returns evals for the requested ids
    that produced 3D geometry."""
    env = Env()
    todo: list[tuple[str, Stmt]] = []
    counter = 0
    for s in src.stmts:
        if isinstance(s, Assignment):
            env.vars[s.name] = eval_expr(s.value, env)
            env.var_defs[s.name] = s
            continue
        if isinstance(s, ModuleDef):
            env.modules[s.name] = s
            continue
        node_id = _node_id_for_top_level(s, counter)
        counter += 1
        if node_id in only_node_ids:
            todo.append((node_id, s))

    out: dict[str, ModuleEval] = {}
    for node_id, s in todo:
        geom = eval_single_stmt(s, env)
        if geom is None or _is_2d(geom):
            continue
        manifold = geom
        bbox = k.BBox.from_manifold(manifold)
        faces = faces_for(s, bbox, env)
        h = _content_hash(s, env)
        out[node_id] = ModuleEval(
            node_id=node_id,
            manifold=manifold,
            bbox=bbox,
            faces=faces,
            content_hash=h,
        )
    return out


def top_level_node_ids(src: Source) -> list[str]:
    """Return the ordered list of node ids the source would produce
    (without evaluating geometry — for diff comparison)."""
    ids: list[str] = []
    counter = 0
    for s in src.stmts:
        if isinstance(s, (Assignment, ModuleDef)):
            continue
        ids.append(_node_id_for_top_level(s, counter))
        counter += 1
    return ids


def _node_id_for_top_level(s: Stmt, counter: int) -> str:
    if isinstance(s, ModuleCall):
        return s.callable
    if isinstance(s, ForStmt):
        return "for" if counter == 0 else f"for_{counter}"
    if isinstance(s, IfStmt):
        return "if" if counter == 0 else f"if_{counter}"
    if isinstance(s, LetStmt):
        return "let" if counter == 0 else f"let_{counter}"
    return f"_top_{counter}"


# ---------------------------------------------------------------------------
# Content hashing
# ---------------------------------------------------------------------------


def content_hash_for_top_level(src: Source, node_id: str) -> int:
    """Compute the content hash for a single top-level node id without
    evaluating geometry. Used by spec_diff to compare against cached
    hashes cheaply."""
    env = Env()
    counter = 0
    for s in src.stmts:
        if isinstance(s, Assignment):
            env.vars[s.name] = None  # value doesn't matter for hashing
            env.var_defs[s.name] = s
            continue
        if isinstance(s, ModuleDef):
            env.modules[s.name] = s
            continue
        nid = _node_id_for_top_level(s, counter)
        counter += 1
        if nid == node_id:
            return _content_hash(s, env)
    raise EvalError(f"node id {node_id!r} not found in source")


def _content_hash(stmt: Stmt, env: Env) -> int:
    """Hash bundling (statement AST, every reachable Assignment AST,
    every reachable ModuleDef AST). Reachability is computed by walking
    the statement and any modules it (transitively) calls.
    """
    vars_used: set[str] = set()
    modules_used: set[str] = set()
    visited_modules: set[str] = set()

    def walk_expr(e: Expr) -> None:
        if isinstance(e, VarRef):
            vars_used.add(e.name)
            return
        if isinstance(e, BinOp):
            walk_expr(e.lhs); walk_expr(e.rhs); return
        if isinstance(e, UnaryOp):
            walk_expr(e.operand); return
        if isinstance(e, Conditional):
            walk_expr(e.cond); walk_expr(e.then_e); walk_expr(e.else_e); return
        if isinstance(e, Vector):
            for c in e.elements: walk_expr(c)
            return
        if isinstance(e, RangeLit):
            walk_expr(e.start); walk_expr(e.end)
            if e.step is not None: walk_expr(e.step)
            return
        if isinstance(e, FunctionCall):
            for a in e.args: walk_expr(a.value)
            return
        if isinstance(e, Indexing):
            walk_expr(e.target); walk_expr(e.index); return
        if isinstance(e, LetExpr):
            for b in e.bindings: walk_expr(b.value)
            walk_expr(e.body); return

    def walk_stmt(s: Stmt) -> None:
        if isinstance(s, ModuleCall):
            for a in s.args: walk_expr(a.value)
            if s.callable in env.modules and s.callable not in visited_modules:
                visited_modules.add(s.callable)
                modules_used.add(s.callable)
                for body_stmt in env.modules[s.callable].body:
                    walk_stmt(body_stmt)
            for c in s.children: walk_stmt(c)
            return
        if isinstance(s, Assignment):
            walk_expr(s.value); return
        if isinstance(s, ForStmt):
            walk_expr(s.iterable)
            for c in s.body: walk_stmt(c)
            return
        if isinstance(s, IfStmt):
            walk_expr(s.cond)
            for c in s.then_body: walk_stmt(c)
            for c in s.else_body: walk_stmt(c)
            return
        if isinstance(s, LetStmt):
            for b in s.bindings: walk_expr(b.value)
            for c in s.body: walk_stmt(c)
            return
        if isinstance(s, ModuleDef):
            for c in s.body: walk_stmt(c)
            return

    walk_stmt(stmt)

    parts: list[Any] = [stmt]
    for v in sorted(vars_used):
        if v in env.var_defs:
            parts.append(env.var_defs[v])
    for m in sorted(modules_used):
        if m in env.modules:
            parts.append(env.modules[m])
    return hash(tuple(parts))


__all__ = [
    "ModuleEval",
    "FacePoint",
    "EvalError",
    "evaluate_source",
    "evaluate_top_level",
    "top_level_node_ids",
    "content_hash_for_top_level",
]
