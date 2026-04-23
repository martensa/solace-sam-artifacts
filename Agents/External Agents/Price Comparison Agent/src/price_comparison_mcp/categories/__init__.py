"""Category profiles for the Price Comparison Agent (v1.0 foundation).

A category is a bundle of settings that specialises the agent for a
product domain (electronics vs fashion vs food vs book_media vs ...).
The bundle covers:

  - domain scoring overlay (additional B2B / retail portals per domain)
  - manufacturer deprioritization overrides
  - query expansion templates (per locale)
  - Rule C fillers (variant-suffix detection noise words)
  - variant detectors (fashion sizes, wine vintages, automotive OEM, ...)
  - outlier price bounds (min/max plausible price band)
  - preferred SearXNG engines (if the admin registered category-specific ones)
  - title-gate anti-lexicon (forbidden words that demote mc=low)

Design principles (carried over from P1-P5 in the v1.0 plan):
  - Additive only: a missing category falls back to `default`, which mirrors
    the legacy v2.3.x behaviour exactly. No silent behaviour change.
  - Data over code: every profile is a YAML file in `_data/`; the Python
    loader is small and typed.
  - Inheritance: profiles can extend each other (tools_hardware < industrial_mro
    < default) to avoid copy-paste of common settings.

This module is the registry + classifier surface. The actual data loader
and classifier will land in the next phase commits; this stub keeps the
package importable so downstream modules can reference its symbols without
circular-import headaches.
"""
from __future__ import annotations

# Public symbols populated as the registry / classifier modules land.
__all__: list[str] = []
