"""Reconcile a validated program before solving.

The brief's numbers are soft estimates that Tasks 1-3 enforced as hard. Task 3
proved the house cannot reach its footprint target for two program-level reasons,
both fixed here (not in the solver/slicer, which are correct):

  1. The space targets need not sum to footprint_target_m2 (the example brief is
     10% over: 176 vs 160). reconcile_program rescales the HABITABLE targets to
     the footprint budget, holding the garage fixed — a garage is sized by car
     count (~36 m2 for two cars), not as a fraction of the house, and it sits
     outside the habitable headline (общая площадь) anyway.

  2. An LLM-guessed per-space min_w/min_h can veto a Neufert-legal, better-packing
     shape. That split (Neufert floor = hard, brief excess = soft preference) is
     left to the solver, which sees both the brief min and the standards floor.

This runs AFTER validation and BEFORE solve().
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import Program

GARAGE_ID = "garage"


def reconcile_program(
    program: Program, held: Iterable[str] = (GARAGE_ID,)
) -> tuple[Program, list[str]]:
    """Return (reconciled program, warnings). Rescales the FLEX space targets so
    the grand total equals footprint_target_m2, holding every id in `held` at its
    brief value. A no-op (returns the input) when there is nothing sensible to
    scale or the targets already reconcile.

    `held` defaults to the garage (sized by car count, outside the habitable
    headline). Task 5 also holds the injected `circulation` corridor so the
    derived corridor target survives the rescale and the other habitable rooms
    absorb the whole footprint delta."""
    warnings: list[str] = []
    held_ids = set(held)
    spaces = program.spaces
    total = sum(s.target_m2 for s in spaces)
    footprint = program.footprint_target_m2

    held_total = sum(s.target_m2 for s in spaces if s.id in held_ids)
    flex_total = total - held_total
    flex_budget = footprint - held_total

    if flex_total <= 0 or flex_budget <= 0:
        return program, warnings
    if abs(total - footprint) <= 1e-6:
        return program, warnings

    factor = flex_budget / flex_total
    new_spaces = [
        s if s.id in held_ids else s.model_copy(update={"target_m2": s.target_m2 * factor})
        for s in spaces
    ]
    reconciled = program.model_copy(update={"spaces": new_spaces})
    warnings.append(
        f"targets rescaled {total:.0f} -> {footprint:.0f} m2 "
        f"(flex x{factor:.3f}, held {sorted(held_ids)} at {held_total:.0f})"
    )
    return reconciled, warnings
