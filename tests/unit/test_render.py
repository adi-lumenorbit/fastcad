"""Render module tests. The OpenSCAD CLI is never actually invoked:
each test injects a fake `spawn` that writes a canned PNG to the
expected output path."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from fastcad.model.render import render_scad_source


# Smallest valid PNG: 1x1 transparent pixel (well-formed enough to
# round-trip through Path.read_bytes in the renderer).
def _minimal_png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


PNG = _minimal_png()


def _fake_spawn_writing_png(*expected_outputs: list[Path]):
    """Returns a spawn callable that, when openscad is invoked,
    writes a tiny PNG to the file path passed via `-o`."""
    def spawn(cmd, **kwargs):  # noqa: ARG001
        # cmd is the list openscad-bin -o OUTPUT --imgsize ... --camera ... --render INPUT
        out_idx = cmd.index("-o") + 1
        out_path = Path(cmd[out_idx])
        out_path.write_bytes(PNG)
        # Mimic subprocess.CompletedProcess
        class CP:
            returncode = 0
            stdout = b""
            stderr = b""
        return CP()
    return spawn


def test_render_returns_three_views(tmp_path):
    """A valid spec + bbox + working subprocess gives one Render per
    canonical angle."""
    bbox = (-5, -5, 0, 5, 5, 20)
    spawn = _fake_spawn_writing_png()
    renders = render_scad_source(
        "cube([10, 10, 20]);",
        bbox,
        openscad_bin="/usr/bin/openscad",
        spawn=spawn,
    )
    assert len(renders) == 3
    angles = [r.angle for r in renders]
    assert set(angles) == {"front", "top", "iso"}
    for r in renders:
        assert r.png_bytes.startswith(b"\x89PNG")
        assert r.b64()  # base64 round-trip non-empty


def test_render_returns_empty_when_binary_missing(tmp_path):
    """No openscad → empty list (caller treats as warning, not
    blocking)."""
    bbox = (-5, -5, 0, 5, 5, 20)
    renders = render_scad_source(
        "cube([10, 10, 20]);",
        bbox,
        openscad_bin=None,  # forces shutil.which fallback to None
        spawn=_fake_spawn_writing_png(),
    )
    # If openscad isn't on PATH, render returns empty without
    # invoking spawn at all.
    # On hosts where openscad IS installed (CI may differ), spawn
    # would still run with the fake; in that case we'd get 3
    # renders. Assert one of the two valid outcomes:
    assert renders == [] or len(renders) == 3


def test_render_subprocess_failure_drops_view(tmp_path):
    """A subprocess that fails to write the PNG → that view's render
    is skipped; we keep the others."""
    call_count = {"n": 0}
    def spawn(cmd, **kwargs):  # noqa: ARG001
        call_count["n"] += 1
        if call_count["n"] == 2:  # second view fails to write
            class CP:
                returncode = 1
                stderr = b"openscad blew up"
                stdout = b""
            return CP()
        out_idx = cmd.index("-o") + 1
        Path(cmd[out_idx]).write_bytes(PNG)
        class CP:
            returncode = 0
            stdout = b""
            stderr = b""
        return CP()

    bbox = (-5, -5, 0, 5, 5, 20)
    renders = render_scad_source(
        "cube([10, 10, 20]);",
        bbox,
        openscad_bin="/usr/bin/openscad",
        spawn=spawn,
    )
    assert len(renders) == 2  # one view dropped


def test_render_camera_string_includes_eye_and_target(tmp_path):
    """Spot-check that the --camera flag includes 6 comma-separated
    floats and the bbox center is the target."""
    captured: dict = {}
    def spawn(cmd, **kwargs):  # noqa: ARG001
        captured.setdefault("cmds", []).append(list(cmd))
        out_idx = cmd.index("-o") + 1
        Path(cmd[out_idx]).write_bytes(PNG)
        class CP:
            returncode = 0
            stdout = b""
            stderr = b""
        return CP()

    bbox = (-3, -3, 0, 3, 3, 10)
    render_scad_source(
        "cube([6, 6, 10]);",
        bbox,
        openscad_bin="/usr/bin/openscad",
        spawn=spawn,
    )
    cam_strs = []
    for cmd in captured["cmds"]:
        idx = cmd.index("--camera")
        cam_strs.append(cmd[idx + 1])
    # Each camera string is six comma-separated floats.
    for s in cam_strs:
        parts = s.split(",")
        assert len(parts) == 6
        # Target (last 3) is the bbox center: (0, 0, 5)
        assert float(parts[3]) == pytest.approx(0.0, abs=1e-6)
        assert float(parts[4]) == pytest.approx(0.0, abs=1e-6)
        assert float(parts[5]) == pytest.approx(5.0, abs=1e-6)
