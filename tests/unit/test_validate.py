"""Channel-1 validator tests.

Each check is exercised with a passing case and at least one failing
case so the regression catches semantic drift in either direction
(false-pass would let bad geometry through; false-fail would block
legitimate output).
"""
from __future__ import annotations

import textwrap

import pytest

from fastcad.model import kernel as k
from fastcad.model.scad_eval import evaluate_source
from fastcad.model.scad_parser import parse
from fastcad.model.validate import (
    AcceptanceSchemaError,
    Defect,
    parse_acceptance_schema,
    validate_against_cache,
)


def _eval_cache(src: str):
    """Convenience: parse + evaluate, return (ast, eval_cache)."""
    ast = parse(src)
    return ast, evaluate_source(ast)


def _cache(text: str) -> str:
    """Wrap a JSON acceptance schema in a minimal cache markdown."""
    return textwrap.dedent(f"""
        # Test entry

        slug: test-entry

        ## Acceptance

        ```json
        {text}
        ```
    """).strip() + "\n"


# ---------------------------------------------------------------------------
# parse_acceptance_schema
# ---------------------------------------------------------------------------


def test_parse_returns_none_when_no_acceptance_section():
    md = "# Title\n\n## Key dimensions\n\n- foo: 5\n"
    assert parse_acceptance_schema(md) is None


def test_parse_extracts_json_block():
    md = _cache('{"bbox_z_extent": [10, 12]}')
    schema = parse_acceptance_schema(md)
    assert schema == {"bbox_z_extent": [10, 12]}


def test_parse_rejects_malformed_json():
    md = _cache("not json at all {{{")
    with pytest.raises(AcceptanceSchemaError):
        parse_acceptance_schema(md)


def test_parse_rejects_missing_code_block():
    md = "# T\n\n## Acceptance\n\nbbox_z_extent: [10, 12]\n"
    with pytest.raises(AcceptanceSchemaError):
        parse_acceptance_schema(md)


# ---------------------------------------------------------------------------
# bbox / volume range checks
# ---------------------------------------------------------------------------


def test_bbox_in_range_passes():
    src = "cube([10, 10, 10]);"
    ast, ec = _eval_cache(src)
    md = _cache('{"bbox_z_extent": [9, 11], "bbox_xy_max": [9, 11]}')
    assert validate_against_cache(ast, md, ec) == []


def test_bbox_z_too_small_fails():
    src = "cube([10, 10, 5]);"
    ast, ec = _eval_cache(src)
    md = _cache('{"bbox_z_extent": [9, 11]}')
    defects = validate_against_cache(ast, md, ec)
    assert len(defects) == 1
    assert defects[0].where == "bbox_z_extent"
    assert defects[0].severity == "error"


def test_volume_range_passes():
    src = "cube([10, 10, 10]);"
    ast, ec = _eval_cache(src)
    md = _cache('{"volume_range": [900, 1100]}')
    assert validate_against_cache(ast, md, ec) == []


def test_volume_out_of_range_fails():
    src = "cube([5, 5, 5]);"   # vol = 125 mm³
    ast, ec = _eval_cache(src)
    md = _cache('{"volume_range": [900, 1100]}')
    defects = validate_against_cache(ast, md, ec)
    assert any(d.where == "volume_range" for d in defects)


# ---------------------------------------------------------------------------
# expected_modules regex check
# ---------------------------------------------------------------------------


def test_expected_modules_match_passes():
    src = """
        module shaft() { cube([1, 1, 5]); }
        module head() { cube([2, 2, 1]); }
        module screw() { union() { shaft(); translate([0,0,5]) head(); } }
        screw();
    """
    ast, ec = _eval_cache(src)
    md = _cache('{"expected_modules": ["shaft|threaded", "head|cap"]}')
    assert validate_against_cache(ast, md, ec) == []


def test_expected_modules_missing_fails():
    src = """
        module shaft() { cube([1, 1, 5]); }
        shaft();
    """
    ast, ec = _eval_cache(src)
    md = _cache('{"expected_modules": ["shaft", "head|cap"]}')
    defects = validate_against_cache(ast, md, ec)
    where = [d.where for d in defects]
    assert any("head|cap" in w for w in where)


# ---------------------------------------------------------------------------
# connected_components
# ---------------------------------------------------------------------------


def test_connected_components_one_passes():
    src = "cube([5, 5, 5]);"
    ast, ec = _eval_cache(src)
    md = _cache('{"connected_components": 1}')
    assert validate_against_cache(ast, md, ec) == []


def test_connected_components_two_disjoint_fails():
    src = """
        module disjoint() {
          union() {
            cube([2, 2, 2]);
            translate([10, 0, 0]) cube([2, 2, 2]);
          }
        }
        disjoint();
    """
    ast, ec = _eval_cache(src)
    md = _cache('{"connected_components": 1}')
    defects = validate_against_cache(ast, md, ec)
    assert any(d.where == "connected_components" for d in defects)


# ---------------------------------------------------------------------------
# horizontal_slices_at_z + outer_protrusions (the regression-class check)
# ---------------------------------------------------------------------------


def test_smooth_cylinder_has_zero_protrusions():
    src = "$fn = 64; cylinder(h = 10, r = 2);"
    ast, ec = _eval_cache(src)
    md = _cache(
        '{"horizontal_slices_at_z": [{"z": 5.0, "outer_protrusions": 0}]}'
    )
    assert validate_against_cache(ast, md, ec) == []


def test_single_start_thread_passes():
    """A single triangular tooth swept around a core via twist — the
    canonical single-start thread shape — must report exactly one
    radial protrusion in any horizontal slice."""
    src = """
        $fn = 64;
        module thread_xs() {
          union() {
            circle(r = 1.0);
            translate([1.0, 0, 0])
              polygon([[0, -0.1], [0.3, 0], [0, 0.1]]);
          }
        }
        module helix() {
          linear_extrude(height = 5, twist = -1800, slices = 256)
            thread_xs();
        }
        helix();
    """
    ast, ec = _eval_cache(src)
    md = _cache(
        '{"horizontal_slices_at_z": [{"z": 2.5, "outer_protrusions": 1}]}'
    )
    defects = validate_against_cache(ast, md, ec)
    # Allow zero defects OR one non-protrusion defect — what matters
    # is that no protrusion-count mismatch fires.
    proto_defects = [d for d in defects if "outer_protrusions" in d.where]
    assert proto_defects == []


def test_multi_start_thread_fails_single_start_check():
    """A 6-tooth cross-section produces a 6-fold symmetric helix.
    Validator must catch this when single-start (1) was specified."""
    src = """
        $fn = 64;
        module thread_xs() {
          difference() {
            circle(r = 1.5);
            for (k = [0:5])
              rotate([0, 0, k * 60])
                translate([1.0, 0, 0])
                  polygon([[0, -0.15], [0.4, 0], [0, 0.15]]);
          }
        }
        module helix() {
          linear_extrude(height = 5, twist = 360, slices = 64)
            thread_xs();
        }
        helix();
    """
    ast, ec = _eval_cache(src)
    md = _cache(
        '{"horizontal_slices_at_z": [{"z": 2.5, "outer_protrusions": 1}]}'
    )
    defects = validate_against_cache(ast, md, ec)
    proto_defects = [d for d in defects if "outer_protrusions" in d.where]
    assert len(proto_defects) == 1
    assert proto_defects[0].severity == "error"
    # The hint should mention multi-start.
    assert (
        "start" in proto_defects[0].hint.lower()
        or "protrusion" in proto_defects[0].hint.lower()
    )


def test_slice_outside_bbox_warns():
    src = "cube([5, 5, 5]);"
    ast, ec = _eval_cache(src)
    md = _cache(
        '{"horizontal_slices_at_z": [{"z": 100, "outer_protrusions": 1}]}'
    )
    defects = validate_against_cache(ast, md, ec)
    assert any(d.severity == "warning" for d in defects)


# ---------------------------------------------------------------------------
# No-acceptance is a no-op (older cache files pre-Stage 3)
# ---------------------------------------------------------------------------


def test_cache_without_acceptance_returns_empty():
    src = "cube([5, 5, 5]);"
    ast, ec = _eval_cache(src)
    md = "# Old entry\n\n## Key dimensions\n\n- foo: 5\n"
    assert validate_against_cache(ast, md, ec) == []
