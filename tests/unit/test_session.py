import pytest

from fastcad.session import SessionState
from fastcad.model.ops import AddPrimitive, Boolean


def _add_cube(s: SessionState, name: str | None = None, size=(10, 10, 10)) -> str:
    nid = name or s.fresh_id("cube")
    s.append(AddPrimitive(kind="cube", params={"size": list(size)}, node_id=nid))
    return nid


def test_append_advances_head():
    s = SessionState()
    _add_cube(s)
    assert s.head == 1
    assert len(s.log) == 1
    assert s.can_undo()
    assert not s.can_redo()


def test_undo_redo_round_trip():
    s = SessionState()
    a = _add_cube(s, name="a")
    b = _add_cube(s, name="b", size=(2, 2, 2))
    assert set(s.scene.order) == {"a", "b"}
    s.undo()
    assert set(s.scene.order) == {"a"}
    assert s.can_redo()
    s.redo()
    assert set(s.scene.order) == {"a", "b"}
    assert not s.can_redo()


def test_append_after_undo_truncates_tail():
    s = SessionState()
    _add_cube(s, name="a")
    _add_cube(s, name="b")
    s.undo()
    assert s.can_redo()
    _add_cube(s, name="c")
    assert not s.can_redo()
    assert [op.node_id for op in s.log] == ["a", "c"]
    assert set(s.scene.order) == {"a", "c"}


def test_undo_with_empty_log_is_noop():
    s = SessionState()
    cs = s.undo()
    assert cs.added == [] and cs.updated == [] and cs.removed == []
    assert s.head == 0


def test_undo_through_boolean():
    s = SessionState()
    _add_cube(s, name="a", size=(10, 10, 10))
    s.append(
        AddPrimitive(
            kind="cylinder",
            params={"height": 20, "radius": 2, "segments": 64},
            node_id="cyl",
            anchor_to="a",
            anchor="bottom",
        )
    )
    s.append(Boolean(kind="difference", target_id="a", with_id="cyl"))
    assert "cyl" not in s.scene.nodes
    s.undo()  # undoes the boolean -> cylinder is back, cube is whole again
    assert set(s.scene.order) == {"a", "cyl"}
    s.undo()  # undoes the cylinder add -> just cube
    assert set(s.scene.order) == {"a"}


def test_fresh_id_is_unique():
    s = SessionState()
    seen = {s.fresh_id("x") for _ in range(50)}
    assert len(seen) == 50
