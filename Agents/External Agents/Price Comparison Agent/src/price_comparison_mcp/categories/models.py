"""Typed data model for a category profile.

A profile bundles all product-domain-specific knobs:

  domain_scores      Per-domain score overlay. The live scorer picks
                     max(base, overlay) so a category can *promote* a
                     domain but never demote one (preserves the hand-
                     tuned electrical-portal tier).

  manufacturer_domains
                     Additional brand/manufacturer domains to
                     deprioritise (combined with the base set).

  manufacturer_penalty_override
                     Per-category override of the -40 penalty. Chemistry
                     is the textbook case: Sigma-Aldrich is both the
                     manufacturer and a distributor, so -40 hurts there.

  query_expansions   Category-aware suffixes appended to the raw query
                     before hitting SearXNG ("{q} Datenblatt", etc.).

  rule_c_fillers     Words that Rule C of _foreign_model_qualifier_penalty
                     must NOT flag. Combined with the base filler set.

  price_band         (min, max) EUR pair -- offers outside are flagged as
                     outliers. None disables the bound.

  title_gate_antilex Words that, when present in the page title, force
                     match_confidence = low. Catches brand collisions
                     (Bosch tools vs Bosch dishwashers).

  preferred_shopping_engines / preferred_general_engines
                     When the shared SearXNG is configured with category-
                     specific engines (abebooks, sigmaaldrich, tecdoc),
                     this overrides the default routing.

Inheritance is resolved at load time; runtime code always sees a fully-
resolved profile.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class CategoryProfile:
    key: str
    display_name: str
    inherits: str | None = None

    domain_scores: Mapping[str, int] = field(default_factory=dict)
    manufacturer_domains: frozenset[str] = field(default_factory=frozenset)
    manufacturer_penalty_override: int | None = None

    query_expansions: tuple[str, ...] = ()
    rule_c_fillers: frozenset[str] = field(default_factory=frozenset)

    price_band: tuple[float, float] | None = None
    title_gate_antilex: frozenset[str] = field(default_factory=frozenset)

    preferred_shopping_engines: tuple[str, ...] | None = None
    preferred_general_engines: tuple[str, ...] | None = None


def merge_profiles(parent: CategoryProfile, child: dict) -> CategoryProfile:
    """Merge `child` (raw YAML dict) on top of `parent` profile.

    Semantics:
      - scalar values: child wins if present, otherwise parent keeps.
      - set-like fields (manufacturer_domains, rule_c_fillers,
        title_gate_antilex): unioned.
      - domain_scores: child overrides matching keys; union otherwise.
      - query_expansions: child replaces (if set) or inherits (if omitted).
      - preferred_*_engines: child replaces (if set).

    Unknown child keys are ignored silently -- forward compatibility for
    fields added in later releases.
    """
    def _fset(value, fallback: frozenset[str]) -> frozenset[str]:
        if value is None:
            return fallback
        return frozenset(str(x).lower() for x in value)

    def _tuple(value, fallback: tuple[str, ...]) -> tuple[str, ...]:
        if value is None:
            return fallback
        return tuple(str(x) for x in value)

    def _opt_tuple(value) -> tuple[str, ...] | None:
        if value is None:
            return None
        return tuple(str(x) for x in value)

    # key/display_name MUST come from child (identity)
    key = child.get("key") or parent.key
    display_name = child.get("display_name") or parent.display_name

    # Domain scores: merge dict
    merged_scores: dict[str, int] = dict(parent.domain_scores)
    for d, s in (child.get("domain_scores") or {}).items():
        merged_scores[str(d).lower()] = int(s)

    # Set fields: union (child expands parent)
    mfr_domains = parent.manufacturer_domains | _fset(
        child.get("manufacturer_domains"), frozenset()
    )
    rule_c_fillers = parent.rule_c_fillers | _fset(
        child.get("rule_c_fillers"), frozenset()
    )
    antilex = parent.title_gate_antilex | _fset(
        child.get("title_gate_antilex"), frozenset()
    )

    # Tuple fields: child replaces if set
    query_expansions = _tuple(child.get("query_expansions"), parent.query_expansions)

    # Optional single-value fields: child overrides iff key present
    if "manufacturer_penalty_override" in child:
        mfr_pen_override = child.get("manufacturer_penalty_override")
    else:
        mfr_pen_override = parent.manufacturer_penalty_override

    if "price_band" in child and child.get("price_band") is not None:
        band_raw = child["price_band"]
        price_band = (float(band_raw[0]), float(band_raw[1]))
    else:
        price_band = parent.price_band

    preferred_shopping = (
        _opt_tuple(child.get("preferred_shopping_engines"))
        if "preferred_shopping_engines" in child
        else parent.preferred_shopping_engines
    )
    preferred_general = (
        _opt_tuple(child.get("preferred_general_engines"))
        if "preferred_general_engines" in child
        else parent.preferred_general_engines
    )

    return CategoryProfile(
        key=key,
        display_name=display_name,
        inherits=None,  # resolved; store as None so the merged profile is terminal
        domain_scores=merged_scores,
        manufacturer_domains=mfr_domains,
        manufacturer_penalty_override=mfr_pen_override,
        query_expansions=query_expansions,
        rule_c_fillers=rule_c_fillers,
        price_band=price_band,
        title_gate_antilex=antilex,
        preferred_shopping_engines=preferred_shopping,
        preferred_general_engines=preferred_general,
    )
