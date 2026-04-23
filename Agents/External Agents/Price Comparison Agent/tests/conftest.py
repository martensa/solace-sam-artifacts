"""Pytest configuration + shared fixtures for the Price Comparison Agent.

Test layout:
  - tests/test_regression_fixes.py    Bestands-Fixes als CI-Gate (markiert @regression)
  - tests/test_variant_heuristics.py  Foreign-Qualifier Rules + Distinctive-Gate
  - tests/test_category_*.py          (v1.0) Kategorie-Registry + Klassifikator
  - tests/test_ean_*.py               (v1.0) EAN-Checksum + Enrichment
  - tests/test_domain_stats.py        (v1.0) Dynamische Domain-Discovery
  - tests/test_locale_detector.py     (v1.0) Locale-Erkennung
  - tests/test_integration_pipeline.py End-to-end mit respx-gemocktem SearXNG

Run all:            pytest
Run fast regression: pytest -m regression
Skip integration:   pytest -m "not integration"
"""
from __future__ import annotations

# No shared fixtures needed yet; module exists so pytest discovers the
# package and lets us add cross-cutting fixtures later without churn.
