"""Geometry service: LLM-free floor-plan generation.

Pipeline: program.json -> CP-SAT solver -> slicer -> validator (gate) -> rank
-> layout.json (Revit-ready). The solver owns all geometry; no LLM ever emits
coordinates.
"""

__version__ = "1.0.0"
