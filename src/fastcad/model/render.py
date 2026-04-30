"""Channel 2 — render the agent's `.scad` to PNGs from canonical
angles via the OpenSCAD CLI.

OpenSCAD ships with a headless renderer (`openscad -o out.png`) that
takes camera + size flags. Using it directly has two big advantages
over a manifold-based renderer:

1. **Zero translation layer.** The thing we render is the exact same
   `.scad` text the user opens in OpenSCAD — what they see in the
   critic's images is what they'll see if they `Export .scad` and
   open it. No discrepancy between "fastcad's manifold" and "real
   OpenSCAD output."
2. **No fragile WSL deps.** `pyrender` / `trimesh.scene.save_image`
   need EGL or a display; the OpenSCAD CLI just writes a PNG. It's
   the path that works without ceremony.

Each render targets ~768x768 to keep payloads small for the vision
critic.
"""
from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


# Canonical view angles for the critic. (eye, target, up) tuples in
# OpenSCAD's --camera convention. All look at the bbox center.

@dataclass(frozen=True)
class _View:
    name: str
    eye: tuple[float, float, float]
    target: tuple[float, float, float]


def _views_for_bbox(bbox: tuple[float, float, float, float, float, float]) -> list[_View]:
    """Pick three orbit angles around the bbox center: front (-Y), top
    (+Z), and an isometric-ish 3/4 view. Eye distance scales with the
    object's bbox so small parts and large parts both fill the frame."""
    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    cx = (xmin + xmax) / 2
    cy = (ymin + ymax) / 2
    cz = (zmin + zmax) / 2
    diag = max(
        max(xmax - xmin, ymax - ymin, zmax - zmin),
        1.0,
    )
    d = diag * 3.0  # comfortable framing margin
    return [
        _View(
            name="front",
            eye=(cx, cy - d, cz),
            target=(cx, cy, cz),
        ),
        _View(
            name="top",
            eye=(cx, cy, cz + d),
            target=(cx, cy, cz),
        ),
        _View(
            name="iso",
            eye=(cx + d * 0.7, cy - d * 0.7, cz + d * 0.5),
            target=(cx, cy, cz),
        ),
    ]


@dataclass
class Render:
    angle: str
    png_bytes: bytes
    width: int
    height: int

    def b64(self) -> str:
        return base64.b64encode(self.png_bytes).decode("ascii")


def render_scad_source(
    source: str,
    bbox: tuple[float, float, float, float, float, float],
    *,
    width: int = 768,
    height: int = 768,
    timeout_s: float = 60.0,
    openscad_bin: str | None = None,
    spawn: Callable | None = None,
) -> list[Render]:
    """Render `source` to PNGs from three canonical angles.

    `bbox` sets the camera framing — caller passes the manifold's
    bounding box (xmin, ymin, zmin, xmax, ymax, zmax). If the
    OpenSCAD binary isn't found or any render fails, the caller gets
    an empty list (Channel 2 will then surface a warning defect).

    `spawn` is an injection point for tests; defaults to
    `subprocess.run`.
    """
    bin_path = openscad_bin or shutil.which("openscad")
    if bin_path is None:
        return []
    runner = spawn or subprocess.run

    out: list[Render] = []
    with tempfile.TemporaryDirectory(prefix="fastcad-render-") as tmpdir:
        scad_path = Path(tmpdir) / "scene.scad"
        scad_path.write_text(source, encoding="utf-8")

        for view in _views_for_bbox(bbox):
            out_png = Path(tmpdir) / f"{view.name}.png"
            # OpenSCAD 2021.01 doesn't accept `--render` (it prints help
            # and exits). Newer versions accept it. We use the form that
            # works in 2021.01 — preview rendering is fine for a vision
            # critic at this resolution.
            cmd = [
                bin_path,
                "-o", str(out_png),
                f"--imgsize={width},{height}",
                f"--camera={_camera_str(view)}",
                "--colorscheme=Tomorrow",
                str(scad_path),
            ]
            try:
                runner(cmd, timeout=timeout_s, capture_output=True, check=False)
            except subprocess.TimeoutExpired:
                continue
            except Exception:  # noqa: BLE001
                continue
            if not out_png.exists() or out_png.stat().st_size == 0:
                continue
            png = out_png.read_bytes()
            out.append(Render(angle=view.name, png_bytes=png, width=width, height=height))
    return out


def _camera_str(view: _View) -> str:
    """OpenSCAD's --camera takes 6 floats: eye_x, eye_y, eye_z,
    target_x, target_y, target_z. Up is implicit (+Z by convention,
    which matches fastcad)."""
    ex, ey, ez = view.eye
    tx, ty, tz = view.target
    return f"{ex},{ey},{ez},{tx},{ty},{tz}"


def persist_renders(renders: list[Render], slug: str) -> Path | None:
    """Write a debug copy of renders to tmp/research/<slug>-<ts>/ so
    the user can inspect what the critic actually saw when a defect
    fires. Returns the directory; None if there were no renders."""
    if not renders:
        return None
    repo_root = Path(__file__).resolve().parents[3]
    out_dir = repo_root / "tmp" / "research" / f"{slug}-{time.strftime('%Y%m%dT%H%M%S')}"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        for r in renders:
            (out_dir / f"{r.angle}.png").write_bytes(r.png_bytes)
    except OSError:
        return None
    return out_dir


__all__ = ["Render", "render_scad_source", "persist_renders"]
