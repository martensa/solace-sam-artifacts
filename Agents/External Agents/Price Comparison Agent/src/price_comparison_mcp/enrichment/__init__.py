"""EAN / GTIN / ISBN enrichment providers (v1.0 foundation).

Given a barcode-like input, this module resolves it to a canonical
product name + brand + category hint via a cascaded provider chain:

  1. Checksum validation       -- reject invalid digits fast, save 15s
  2. OpenFoodFacts (free)      -- food + cosmetic EAN database
  3. Wikidata SPARQL (free)    -- GTIN -> instance_of -> category map
  4. ean-search.org (token)    -- generic commercial barcode database

Each provider is async and returns `EnrichmentResult | None`. The
`EANEnricher` dispatcher tries them in order, caches hits for 90 days
in SQLite, and feeds the result into the category classifier (Stage 2)
to bypass the LLM call when a product family is unambiguously known.
"""
from __future__ import annotations

__all__: list[str] = []
