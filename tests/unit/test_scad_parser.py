"""Parser tests. Runs against the subset documented in
docs/plans/02-stage1-scad-spec.md.

The strategy is shape-matching against AST node types rather than
spelling out every literal — keeps the tests resilient to internal
restructuring while still catching regressions in semantics.
"""
from __future__ import annotations

import pytest

from fastcad.model.scad_parser import (
    Arg,
    Assignment,
    BinOp,
    BoolLit,
    Conditional,
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
    ScadParseError,
    Source,
    UnaryOp,
    VarRef,
    Vector,
    parse,
)


def test_empty_source():
    src = parse("")
    assert isinstance(src, Source)
    assert src.stmts == ()


def test_only_comments():
    src = parse("// hello\n/* block */\n")
    assert src.stmts == ()


def test_single_assignment():
    src = parse("length = 20;")
    assert len(src.stmts) == 1
    a = src.stmts[0]
    assert isinstance(a, Assignment)
    assert a.name == "length"
    assert a.value == NumLit(20.0)


def test_special_var_assignment():
    src = parse("$fn = 64;")
    assert isinstance(src.stmts[0], Assignment)
    assert src.stmts[0].name == "$fn"


def test_negative_literal():
    src = parse("x = -5;")
    a = src.stmts[0]
    assert isinstance(a, Assignment)
    assert a.value == NumLit(-5.0)


def test_arithmetic_precedence():
    src = parse("y = 1 + 2 * 3;")
    a = src.stmts[0]
    # Expect: 1 + (2 * 3)
    assert isinstance(a.value, BinOp) and a.value.op == "+"
    assert a.value.lhs == NumLit(1.0)
    assert isinstance(a.value.rhs, BinOp) and a.value.rhs.op == "*"


def test_parenthesised_grouping():
    src = parse("y = (1 + 2) * 3;")
    a = src.stmts[0]
    assert isinstance(a.value, BinOp) and a.value.op == "*"


def test_vector_literal():
    src = parse("v = [1, 2, 3];")
    a = src.stmts[0]
    assert isinstance(a.value, Vector)
    assert a.value.elements == (NumLit(1.0), NumLit(2.0), NumLit(3.0))


def test_empty_vector():
    src = parse("v = [];")
    a = src.stmts[0]
    assert isinstance(a.value, Vector)
    assert a.value.elements == ()


def test_range_literal():
    src = parse("r = [0:10];")
    a = src.stmts[0]
    assert isinstance(a.value, RangeLit)
    assert a.value.start == NumLit(0.0)
    assert a.value.end == NumLit(10.0)
    assert a.value.step is None


def test_range_with_step():
    src = parse("r = [0:2:10];")
    a = src.stmts[0]
    assert isinstance(a.value, RangeLit)
    assert a.value.step == NumLit(2.0)


def test_indexing():
    src = parse("y = v[2];")
    a = src.stmts[0]
    assert isinstance(a.value, Indexing)
    assert a.value.target == VarRef("v")
    assert a.value.index == NumLit(2.0)


def test_function_call():
    src = parse("y = sin(45);")
    a = src.stmts[0]
    assert isinstance(a.value, FunctionCall)
    assert a.value.name == "sin"
    assert a.value.args == (Arg(name=None, value=NumLit(45.0)),)


def test_ternary():
    src = parse("y = x > 0 ? 1 : -1;")
    a = src.stmts[0]
    assert isinstance(a.value, Conditional)


def test_module_call_leaf():
    src = parse("cube([10, 10, 10]);")
    s = src.stmts[0]
    assert isinstance(s, ModuleCall)
    assert s.callable == "cube"
    assert s.children == ()
    assert len(s.args) == 1


def test_module_call_kwarg():
    src = parse("cylinder(h = 20, r = 5);")
    s = src.stmts[0]
    assert isinstance(s, ModuleCall)
    assert s.args[0] == Arg(name="h", value=NumLit(20.0))
    assert s.args[1] == Arg(name="r", value=NumLit(5.0))


def test_module_call_with_block():
    src = parse("union() { cube([1,1,1]); sphere(1); }")
    s = src.stmts[0]
    assert isinstance(s, ModuleCall)
    assert s.callable == "union"
    assert len(s.children) == 2
    assert all(isinstance(c, ModuleCall) for c in s.children)


def test_module_call_with_implicit_child():
    src = parse("translate([0,0,5]) cube(2);")
    s = src.stmts[0]
    assert isinstance(s, ModuleCall) and s.callable == "translate"
    assert len(s.children) == 1
    inner = s.children[0]
    assert isinstance(inner, ModuleCall) and inner.callable == "cube"


def test_module_def():
    src = parse("module foo(a, b = 5) { cube([a, a, b]); }")
    m = src.stmts[0]
    assert isinstance(m, ModuleDef)
    assert m.name == "foo"
    assert len(m.params) == 2
    assert m.params[0].default is None
    assert m.params[1].default == NumLit(5.0)
    assert len(m.body) == 1


def test_for_stmt():
    src = parse("for (k = [0:11]) cube(1);")
    s = src.stmts[0]
    assert isinstance(s, ForStmt)
    assert s.var == "k"
    assert isinstance(s.iterable, RangeLit)
    assert len(s.body) == 1


def test_for_stmt_with_block():
    src = parse("for (k = [0:5]) { cube(1); sphere(1); }")
    s = src.stmts[0]
    assert isinstance(s, ForStmt)
    assert len(s.body) == 2


def test_if_else():
    src = parse("if (x > 0) cube(1); else sphere(1);")
    s = src.stmts[0]
    assert isinstance(s, IfStmt)
    assert len(s.then_body) == 1
    assert len(s.else_body) == 1


def test_let_expression_in_value():
    src = parse("y = let(a = 1, b = 2) a + b;")
    a = src.stmts[0]
    assert isinstance(a.value, LetExpr)
    assert len(a.value.bindings) == 2


def test_let_statement():
    src = parse("let(t = 5) cube(t);")
    s = src.stmts[0]
    assert isinstance(s, LetStmt)
    assert len(s.bindings) == 1
    assert len(s.body) == 1


def test_unary_negation_in_expr():
    src = parse("y = -x + 1;")
    a = src.stmts[0]
    assert isinstance(a.value, BinOp) and a.value.op == "+"
    assert isinstance(a.value.lhs, UnaryOp) and a.value.lhs.op == "-"


def test_string_literal_in_arg():
    src = parse('echo("hello");')
    s = src.stmts[0]
    assert isinstance(s, ModuleCall)
    assert s.callable == "echo"


def test_m3_screw_fixture_parses():
    """The full M3-screw spec from the plan must parse end-to-end."""
    source = """
diameter = 3;
length   = 20;
pitch    = 0.5;
$fn      = 64;

module thread_section(major, minor) {
  difference() {
    circle(d = major);
    for (k = [0:11])
      rotate([0, 0, k * 30])
        translate([minor / 2, 0, 0])
          polygon([[0, -0.15], [0.4, 0], [0, 0.15]]);
  }
}

module shaft() {
  linear_extrude(height = length, twist = 360 * length / pitch)
    thread_section(major = diameter, minor = diameter * 0.85);
}

module head() {
  translate([0, 0, length])
    linear_extrude(height = 2)
      circle(d = diameter * 1.6);
}

module screw() {
  union() { shaft(); head(); }
}

screw();
"""
    src = parse(source)
    # 4 assignments + 4 module defs + 1 top-level call
    kinds = [type(s).__name__ for s in src.stmts]
    assert kinds.count("Assignment") == 4
    assert kinds.count("ModuleDef") == 4
    assert kinds.count("ModuleCall") == 1


# ---- error cases -----------------------------------------------------------


def test_function_keyword_rejected():
    with pytest.raises(ScadParseError, match="function"):
        parse("function f(x) = x + 1;")


def test_include_rejected():
    with pytest.raises(ScadParseError, match="include"):
        parse("include <foo.scad>")


def test_use_rejected():
    with pytest.raises(ScadParseError, match="include|use"):
        parse("use <foo.scad>")


def test_import_rejected():
    with pytest.raises(ScadParseError, match="import"):
        parse('import("foo.stl");')


def test_missing_semicolon_errors():
    with pytest.raises(ScadParseError):
        parse("x = 5\ny = 6;")  # missing ; after first


def test_unbalanced_braces_errors():
    with pytest.raises(ScadParseError):
        parse("module foo() { cube(1);")
