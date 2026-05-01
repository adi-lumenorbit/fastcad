"""Multi-critic orchestrator.

Each `critics/<name>.py` defines:
  - A `NAME` constant ("general", "threads", ...).
  - A module-level `DIRECTIVE` string used as the system prompt.
  - A `review(...)` function with the same signature as the others.

The orchestrator (`review_all`) dispatches all enabled critics in
parallel via a ThreadPoolExecutor — vision calls are I/O-bound, so a
plain thread pool keeps wall-clock ~= one round-trip regardless of
how many critics. Each critic's defects are tagged with its NAME in
the progress events so the UI can label them ("vision:threads ✗ …").

Critics don't talk to each other in v1. The agent receives the merged
defect list and is the integrator; if two critics disagree, the agent
sees both views and can revise accordingly. A future meta-critic
could observe the disagreements as signal of its own.

Cost: linear in number of critics × per-call tokens. Each iteration
costs ~N × ~10s of vision-model time, but in parallel so wall clock
stays ~10s. Disable with FASTCAD_AUTO_VALIDATE=structural.
"""
from __future__ import annotations

import concurrent.futures as _futures
import os
from typing import Any, Callable

from ...model.render import Render, persist_renders, render_scad_source
from ...model.validate import Defect

from . import fixit as _fixit
from . import general as _general
from . import threads as _threads


# Registered critics. Order is the priority for displaying defects in
# the UI; the first listed sees its results rendered first.
_REGISTRY: dict[str, "_Critic"] = {}


class _Critic:
    """Wraps a critics/<name>.py module so the orchestrator can call
    it with a uniform interface. Looks up `review` on the module at
    call time so monkeypatch / hot-swap during tests works."""

    def __init__(self, name: str, module):
        self.name = name
        self._module = module

    @property
    def directive(self) -> str:
        return getattr(self._module, "DIRECTIVE")

    def review(self, **kwargs):
        return getattr(self._module, "review")(**kwargs)


def _register(module) -> None:
    name = getattr(module, "NAME")
    _REGISTRY[name] = _Critic(name=name, module=module)


_register(_general)
_register(_threads)
_register(_fixit)


# Persistence threshold for P7/P8: how many consecutive iterations
# must show overlapping defect `where` fields before we escalate.
PERSISTENCE_THRESHOLD = 3


def detect_persistent_defects(
    history: list[list[dict]],
    threshold: int = PERSISTENCE_THRESHOLD,
) -> list[str]:
    """Return the set of defect `where` keys that have appeared in
    each of the last `threshold` iterations. Empty list = no
    persistence detected; agent is making progress.

    Compares by `where` prefix so e.g.
    `horizontal_slices_at_z[0].outer_protrusions` and
    `horizontal_slices_at_z[2].outer_protrusions` collapse to the
    same persistence key. The defects don't need to be identical
    across iterations — what matters is the same defect CLASS.
    """
    if len(history) < threshold:
        return []
    recent = history[-threshold:]
    if any(not it for it in recent):
        return []   # an empty iteration breaks the streak
    keys_per_iter: list[set[str]] = []
    for defects in recent:
        keys = set()
        for d in defects:
            where = str(d.get("where", ""))
            # Collapse [N] indexed wheres to the bare path so
            # axial_consistency at slice 0 and slice 2 are "same".
            key = where.split("[")[0] if "[" in where else where
            keys.add(key)
        keys_per_iter.append(keys)
    intersection = keys_per_iter[0]
    for k in keys_per_iter[1:]:
        intersection &= k
    return sorted(intersection)


def enabled_critics() -> list[str]:
    """Critic names enabled by env config. FASTCAD_VISION_CRITICS is
    a comma-separated allow-list; default is "all enabled."""
    raw = os.environ.get("FASTCAD_VISION_CRITICS", "").strip()
    if not raw:
        return list(_REGISTRY.keys())
    return [n.strip() for n in raw.split(",") if n.strip() in _REGISTRY]


def review_all(
    spec_source: str,
    cache_md: str,
    user_prompt: str,
    bbox: tuple[float, float, float, float, float, float],
    *,
    on_progress: Callable[[dict], None] | None = None,
    client_factory: Callable[[], Any] | None = None,
    render_fn: Callable | None = None,
    persist_slug: str | None = None,
    only: list[str] | None = None,
    prior_defects: list[list[dict]] | None = None,
) -> list[Defect]:
    """Render once, dispatch all enabled critics in parallel, merge
    defects.

    Renders are produced once (expensive) and shared across critics —
    every critic sees the same canonical-angle PNGs.
    """
    if on_progress is not None:
        on_progress({"type": "critics_started", "names": only or enabled_critics()})

    renderer = render_fn or render_scad_source
    renders: list[Render] = renderer(spec_source, bbox)
    if not renders:
        if on_progress is not None:
            on_progress({"type": "critic_skipped", "reason": "no renders produced"})
        return [
            Defect(
                severity="warning",
                where="critics_render",
                expected="3 renders from canonical angles",
                actual="0 renders (openscad missing or failed)",
                hint=(
                    "Install OpenSCAD CLI to enable visual critics. "
                    "Channel 1 (structural) ran regardless."
                ),
            )
        ]

    if persist_slug:
        persist_renders(renders, persist_slug)

    # Decide which critics fire this iteration.
    #  - The fix-it critic ONLY runs when persistence is detected
    #    (P7/P8 escalation). Otherwise it's noisy + costly.
    #  - All other critics run every iteration.
    names = only if only is not None else enabled_critics()
    persistent = detect_persistent_defects(prior_defects or [])
    if "fixit" in names and not persistent:
        names = [n for n in names if n != "fixit"]
    critics = [_REGISTRY[n] for n in names if n in _REGISTRY]

    if persistent and on_progress is not None:
        on_progress({
            "type": "critics_escalation",
            "persistent_keys": persistent,
            "fixit_will_fire": "fixit" in names,
        })

    def _run_one(critic: _Critic) -> tuple[str, list[Defect]]:
        client = (client_factory or _default_client)()
        try:
            kwargs = dict(
                spec_source=spec_source,
                cache_md=cache_md,
                user_prompt=user_prompt,
                renders=renders,
                client=client,
                directive=critic.directive,
            )
            # The fix-it critic needs prior_defects in escalation
            # mode to compose its directive. Other critics ignore
            # this kwarg (their `review` doesn't accept it).
            if critic.name == "fixit":
                kwargs["prior_defects"] = prior_defects or []
            return critic.name, critic.review(**kwargs)
        except Exception as exc:  # noqa: BLE001
            return critic.name, [
                Defect(
                    severity="warning",
                    where=f"critic_internal:{critic.name}",
                    expected="critic completes",
                    actual=f"{type(exc).__name__}: {exc}",
                    hint=f"The {critic.name} critic crashed; other critics ran.",
                )
            ]

    merged: list[Defect] = []
    if not critics:
        if on_progress is not None:
            on_progress({"type": "critics_done", "defect_count": 0})
        return merged

    # Wall-clock = one critic's runtime, not N × runtime.
    with _futures.ThreadPoolExecutor(max_workers=max(1, len(critics))) as ex:
        for name, defects in ex.map(_run_one, critics):
            for d in defects:
                merged.append(d)
                if on_progress is not None:
                    on_progress({
                        "type": "validation_defect",
                        "severity": d.severity,
                        "where": d.where,
                        "expected": d.expected,
                        "actual": d.actual,
                        "hint": d.hint,
                        "channel": f"vision:{name}",
                    })

    if on_progress is not None:
        on_progress({"type": "critics_done", "defect_count": len(merged)})
    return merged


def _default_client():
    """Default Anthropic client factory. Each thread gets its own to
    avoid sharing connection state across parallel critic calls."""
    from anthropic import Anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    return Anthropic(api_key=api_key) if api_key else Anthropic()


__all__ = ["review_all", "enabled_critics"]
