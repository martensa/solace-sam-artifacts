"""YAML-backed category registry with inheritance resolution.

Single class `CategoryRegistry`:

  - loads every `*.yaml` in `_data/` into a dict of raw profiles
  - resolves inheritance transitively (child inherits parent inherits ...
    inherits default) using merge_profiles()
  - caches the resolved CategoryProfile instances

Safety invariants:
  - The 'default' profile MUST be present (fail fast at startup otherwise)
  - Cyclic inheritance raises CategoryInheritanceCycle at load time
  - get() on an unknown key falls back to 'default' silently -- no KeyError
    so the pipeline keeps running when the classifier emits a value the
    registry hasn't shipped yet (e.g. during live LLM upgrades)
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import yaml

from .models import CategoryProfile, merge_profiles


DEFAULT_DATA_DIR = Path(__file__).parent / "_data"
DEFAULT_KEY = "default"


class CategoryInheritanceCycle(Exception):
    """Raised when a category's inherit chain contains a cycle."""


class CategoryRegistry:
    """Resolved, immutable view over all category profiles."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self._raw: dict[str, dict] = {}
        self._resolved: dict[str, CategoryProfile] = {}
        self.load()

    def load(self) -> None:
        """Parse every *.yaml in `_data/` + resolve inheritance."""
        if not self._data_dir.is_dir():
            raise FileNotFoundError(
                f"Category data dir not found: {self._data_dir}"
            )

        # Pass 1: read raw YAML dicts keyed by the `key` field (not the filename)
        self._raw.clear()
        for yaml_file in sorted(self._data_dir.glob("*.yaml")):
            with yaml_file.open() as f:
                doc = yaml.safe_load(f) or {}
            key = doc.get("key")
            if not key:
                raise ValueError(f"{yaml_file}: missing 'key' field")
            if key in self._raw:
                raise ValueError(f"Duplicate category key '{key}' in {yaml_file}")
            self._raw[key] = doc

        if DEFAULT_KEY not in self._raw:
            raise ValueError(
                f"Registry must contain a profile with key='{DEFAULT_KEY}' "
                f"(checked {self._data_dir})"
            )

        # Pass 2: resolve every profile via inheritance
        self._resolved.clear()
        for key in self._raw:
            self._resolved[key] = self._resolve(key, _chain=())

    def _resolve(self, key: str, _chain: tuple[str, ...]) -> CategoryProfile:
        """Recursive resolver. `_chain` tracks visited keys for cycle detection."""
        if key in _chain:
            raise CategoryInheritanceCycle(
                f"Inheritance cycle: {' -> '.join(_chain + (key,))}"
            )
        if key in self._resolved:
            return self._resolved[key]

        doc = self._raw.get(key)
        if doc is None:
            # Unknown key -- return default (still resolved).
            return self.get(DEFAULT_KEY) if key != DEFAULT_KEY else self._empty_default()

        parent_key = doc.get("inherits")
        if key == DEFAULT_KEY or not parent_key:
            parent = self._empty_default()
        else:
            parent = self._resolve(parent_key, _chain + (key,))

        resolved = merge_profiles(parent, doc)
        self._resolved[key] = resolved
        return resolved

    @staticmethod
    def _empty_default() -> CategoryProfile:
        """Baseline profile used as the root of the inheritance chain."""
        return CategoryProfile(
            key="_empty_default",
            display_name="",
            inherits=None,
        )

    def get(self, key: str | None) -> CategoryProfile:
        """Return the resolved profile for `key`, or the default profile."""
        if key is None:
            return self._resolved[DEFAULT_KEY]
        return self._resolved.get(key, self._resolved[DEFAULT_KEY])

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._resolved.keys()))

    def __contains__(self, key: str) -> bool:
        return key in self._resolved

    # ------------------------------------------------------------------
    # Singleton accessor (used by hot-path code that doesn't want to
    # thread a registry instance through the call stack).
    # ------------------------------------------------------------------

    _SINGLETON: "CategoryRegistry | None" = None

    @classmethod
    def instance(cls) -> "CategoryRegistry":
        if cls._SINGLETON is None:
            cls._SINGLETON = cls()
        return cls._SINGLETON

    @classmethod
    def reset(cls) -> None:
        """Test helper: forget the cached singleton."""
        cls._SINGLETON = None
