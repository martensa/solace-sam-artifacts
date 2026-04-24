"""Tests for discovery/domain_stats.py -- SQLite-backed learning."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from price_comparison_mcp.discovery import domain_stats as ds


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test_stats.db"
    store = ds.DomainStatsStore(db_path=db)
    yield store
    store.close()


class TestSchemaInit:
    def test_fresh_db_has_empty_table(self, store):
        assert store.all_rows() == []

    def test_repeated_init_safe(self, tmp_path):
        db = tmp_path / "foo.db"
        s1 = ds.DomainStatsStore(db_path=db)
        s2 = ds.DomainStatsStore(db_path=db)
        s1.close()
        s2.close()


class TestRecordHit:
    def test_first_insert(self, store):
        store.record_hit("tandmore.de", "industrial_mro", offers=1, match_conf=0.85)
        rows = store.all_rows()
        assert len(rows) == 1
        assert rows[0].domain == "tandmore.de"
        assert rows[0].category == "industrial_mro"
        assert rows[0].hit_count == 1
        assert abs(rows[0].avg_match_conf - 0.85) < 1e-6

    def test_second_insert_accumulates(self, store):
        store.record_hit("tandmore.de", "industrial_mro", match_conf=0.8)
        store.record_hit("tandmore.de", "industrial_mro", match_conf=1.0)
        rows = store.all_rows()
        assert rows[0].hit_count == 2
        # running mean (0.8 + 1.0) / 2 = 0.9
        assert abs(rows[0].avg_match_conf - 0.9) < 1e-6

    def test_different_category_separate_row(self, store):
        store.record_hit("amazon.de", "tools_hardware")
        store.record_hit("amazon.de", "fashion_apparel")
        rows = store.all_rows()
        assert len(rows) == 2


class TestPromotionOverlay:
    def test_below_threshold_not_promoted(self, store):
        """< threshold hits -> not in overlay."""
        store.record_hit("rare.de", "industrial_mro")
        overlay = store.get_promoted_overlay("industrial_mro", threshold=3.0)
        assert "rare.de" not in overlay

    def test_threshold_hits_promoted(self, store):
        for _ in range(5):
            store.record_hit("learned.de", "industrial_mro", match_conf=0.9)
        overlay = store.get_promoted_overlay("industrial_mro")
        assert "learned.de" in overlay
        # Score should be in the [50, 85] window
        assert 50 <= overlay["learned.de"] <= 85

    def test_decay_drops_old_hits(self, store):
        """Old hits decay with exp(-days/90)."""
        now = datetime.now(tz=timezone.utc)
        ancient = now - timedelta(days=300)  # half-life ~62 -> 5 halflives
        # Manually hack last_seen via raw SQL for test isolation
        for _ in range(10):
            store.record_hit("old.de", "industrial_mro", now=ancient)
        overlay = store.get_promoted_overlay("industrial_mro")
        # 10 * exp(-300/90) ~= 10 * 0.035 ~= 0.35 -> below threshold
        assert "old.de" not in overlay

    def test_category_isolation(self, store):
        """Hits in category X don't promote the domain in category Y."""
        for _ in range(5):
            store.record_hit("crossover.de", "fashion_apparel", match_conf=0.9)
        ov_fashion = store.get_promoted_overlay("fashion_apparel")
        ov_tools = store.get_promoted_overlay("tools_hardware")
        assert "crossover.de" in ov_fashion
        assert "crossover.de" not in ov_tools

    def test_score_capped_at_max(self, store):
        """Even with 1000 hits, score never exceeds _PROMOTION_SCORE_MAX (85)."""
        for _ in range(1000):
            store.record_hit("spam.de", "industrial_mro", match_conf=1.0)
        overlay = store.get_promoted_overlay("industrial_mro")
        assert overlay["spam.de"] <= ds._PROMOTION_SCORE_MAX


class TestPrune:
    def test_prune_old_rows(self, store):
        now = datetime.now(tz=timezone.utc)
        # insert row with ancient last_seen
        store.record_hit("gone.de", "industrial_mro", now=now - timedelta(days=400))
        deleted = store.prune(max_age_days=365, now=now)
        assert deleted == 1
        assert store.all_rows() == []

    def test_prune_keeps_fresh(self, store):
        store.record_hit("fresh.de", "industrial_mro")
        deleted = store.prune(max_age_days=365)
        assert deleted == 0
        assert len(store.all_rows()) == 1


class TestGracefulDegradation:
    def test_read_on_readonly_fs_returns_empty(self, tmp_path, monkeypatch):
        """When SQLite can't open the DB, the reader returns {} -- no crash."""
        # Point the DB at a path that can't be created
        bad = tmp_path / "subdir/that/does/not/exist/stats.db"
        store = ds.DomainStatsStore(db_path=bad)
        # Reader should return empty
        assert store.get_promoted_overlay("industrial_mro") == {}
        store.close()


class TestSingleton:
    def test_instance_reset(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PRICE_DOMAIN_STATS_DB_PATH", str(tmp_path / "singleton.db"))
        ds.reset()
        s1 = ds.instance()
        s2 = ds.instance()
        assert s1 is s2
        ds.reset()
        s3 = ds.instance()
        assert s3 is not s1
