"""Dynamic domain-discovery via SQLite stats with exponential decay.

Concept:
  After each successful search, domains that returned high-confidence
  non-outlier offers get a hit logged. Over time, a lernende Tabelle
  der produktiven Domains entsteht -- even if they were never in the
  hardcoded `_PRICE_SITE_SCORES`.

  When the next query runs, the scorer consults this table: domains
  with >= N hits (with exp-decay on age) get promoted into the active
  overlay. The registry-based `_PRICE_SITE_SCORES` stays stable, but
  the agent learns new distributors without code pushes.

Schema:
  domain_stats(domain, category, hit_count, cumulative_offers,
               last_seen, first_seen, avg_match_conf)

Hit counts decay with half-life ~62 days (exp(-days/90)). A domain
with ~3 hits in the last 3 months and 0.8 avg match_confidence gets
a promotion score of ~50, enough to beat the generic-domain floor
but never displacing the hand-tuned top tier.

API:
  DomainStatsStore         -- sync CRUD + decay queries
  record_offers(..)        -- write hook after the pipeline
  get_promoted_overlay(..) -- read hook at the start of _score_url

Graceful degradation:
  SQLite errors (locked DB, corrupted file) are caught and the reader
  returns an empty overlay -- the agent simply stops learning but keeps
  serving. No write failure blocks the pipeline.
"""
from __future__ import annotations

import logging
import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


logger = logging.getLogger("price-comparison-mcp.domain_stats")


DEFAULT_DB_PATH = Path("/app/data/domain_stats.db")
_PROMOTION_SCORE_MIN = 50        # floor applied to any promoted domain
_PROMOTION_SCORE_MAX = 85        # cap so learned domains never beat top tier
_DEFAULT_HALFLIFE_DAYS = 62.0    # exp(-days/90) -> halflife ~62d
_DEFAULT_THRESHOLD = 3.0         # minimum decayed hits for promotion


@dataclass(frozen=True)
class DomainStat:
    domain: str
    category: str
    hit_count: int
    cumulative_offers: int
    last_seen: datetime
    first_seen: datetime
    avg_match_conf: float


class DomainStatsStore:
    """SQLite-backed store. Thread-safe via single connection + lock."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create DB + table if missing. Safe to call repeatedly."""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("domain_stats: cannot create dir %s: %s", self.db_path.parent, e)
            # Fall back to tmp so the writer doesn't crash the pipeline
            self.db_path = Path("/tmp") / self.db_path.name
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._get_conn() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS domain_stats (
                        domain TEXT NOT NULL,
                        category TEXT NOT NULL,
                        hit_count INTEGER NOT NULL DEFAULT 0,
                        cumulative_offers INTEGER NOT NULL DEFAULT 0,
                        last_seen TEXT NOT NULL,
                        first_seen TEXT NOT NULL,
                        avg_match_conf REAL NOT NULL DEFAULT 0.0,
                        PRIMARY KEY (domain, category)
                    );
                    CREATE INDEX IF NOT EXISTS idx_last_seen
                        ON domain_stats(last_seen);
                    CREATE INDEX IF NOT EXISTS idx_category
                        ON domain_stats(category);
                    """
                )
                conn.commit()
        except sqlite3.Error as e:
            logger.warning("domain_stats: schema init failed: %s", e)

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False, timeout=2.0,
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    @contextmanager
    def _cursor(self):
        with self._lock:
            conn = self._get_conn()
            try:
                yield conn
            except sqlite3.Error as e:
                logger.warning("domain_stats: sql error: %s", e)
                conn.rollback()
                raise

    # ----- Writer --------------------------------------------------

    def record_hit(
        self,
        domain: str,
        category: str,
        offers: int = 1,
        match_conf: float = 1.0,
        now: datetime | None = None,
    ) -> None:
        """Increment hit_count + cumulative_offers, update avg_match_conf."""
        now = now or datetime.now(tz=timezone.utc)
        now_iso = now.isoformat()
        try:
            with self._cursor() as conn:
                # Compute new avg_match_conf = running mean
                row = conn.execute(
                    "SELECT hit_count, avg_match_conf FROM domain_stats "
                    "WHERE domain = ? AND category = ?",
                    (domain, category),
                ).fetchone()
                if row is None:
                    conn.execute(
                        """
                        INSERT INTO domain_stats(
                            domain, category, hit_count, cumulative_offers,
                            last_seen, first_seen, avg_match_conf
                        ) VALUES (?, ?, 1, ?, ?, ?, ?)
                        """,
                        (domain, category, offers, now_iso, now_iso, match_conf),
                    )
                else:
                    old_hits = int(row["hit_count"])
                    old_avg = float(row["avg_match_conf"] or 0.0)
                    new_avg = (old_avg * old_hits + match_conf) / (old_hits + 1)
                    conn.execute(
                        """
                        UPDATE domain_stats
                        SET hit_count = hit_count + 1,
                            cumulative_offers = cumulative_offers + ?,
                            last_seen = ?,
                            avg_match_conf = ?
                        WHERE domain = ? AND category = ?
                        """,
                        (offers, now_iso, new_avg, domain, category),
                    )
                conn.commit()
        except sqlite3.Error:
            pass  # logged inside _cursor; keep pipeline alive

    # ----- Reader --------------------------------------------------

    def get_promoted_overlay(
        self,
        category: str,
        threshold: float = _DEFAULT_THRESHOLD,
        halflife_days: float = _DEFAULT_HALFLIFE_DAYS,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Return {domain: promoted_score} for a given category.

        Score formula: `hit_count * exp(-days / 90.0)` where days is
        the age of the last hit. Only domains whose decayed score >=
        `threshold` are returned. Promoted score is linearly interpolated
        into `[_PROMOTION_SCORE_MIN, _PROMOTION_SCORE_MAX]`.
        """
        now = now or datetime.now(tz=timezone.utc)
        try:
            with self._cursor() as conn:
                rows = conn.execute(
                    "SELECT domain, hit_count, last_seen, avg_match_conf "
                    "FROM domain_stats WHERE category = ?",
                    (category,),
                ).fetchall()
        except sqlite3.Error:
            return {}

        overlay: dict[str, int] = {}
        for r in rows:
            try:
                last_seen = datetime.fromisoformat(r["last_seen"])
                days = max(0.0, (now - last_seen).total_seconds() / 86400.0)
                decayed = r["hit_count"] * math.exp(-days / 90.0)
                if decayed < threshold:
                    continue
                # Clamp to [MIN, MAX]; give a small bonus for avg_match_conf
                conf_bonus = min(10.0, float(r["avg_match_conf"] or 0.0) * 10)
                score = int(min(_PROMOTION_SCORE_MAX,
                                _PROMOTION_SCORE_MIN + conf_bonus + (decayed - threshold)))
                overlay[r["domain"]] = score
            except (ValueError, TypeError):
                continue
        return overlay

    def prune(self, max_age_days: int = 365, now: datetime | None = None) -> int:
        """Delete rows not seen in > max_age_days. Returns row count deleted."""
        now = now or datetime.now(tz=timezone.utc)
        cutoff = (now - timedelta(days=max_age_days)).isoformat()
        try:
            with self._cursor() as conn:
                cur = conn.execute(
                    "DELETE FROM domain_stats WHERE last_seen < ?",
                    (cutoff,),
                )
                conn.commit()
                return cur.rowcount
        except sqlite3.Error:
            return 0

    def all_rows(self) -> list[DomainStat]:
        try:
            with self._cursor() as conn:
                rows = conn.execute(
                    "SELECT * FROM domain_stats ORDER BY last_seen DESC"
                ).fetchall()
        except sqlite3.Error:
            return []
        out: list[DomainStat] = []
        for r in rows:
            try:
                out.append(DomainStat(
                    domain=r["domain"],
                    category=r["category"],
                    hit_count=int(r["hit_count"]),
                    cumulative_offers=int(r["cumulative_offers"]),
                    last_seen=datetime.fromisoformat(r["last_seen"]),
                    first_seen=datetime.fromisoformat(r["first_seen"]),
                    avg_match_conf=float(r["avg_match_conf"]),
                ))
            except (ValueError, TypeError):
                continue
        return out

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None


# -----------------------------------------------------------------------------
# Phase P: S3 snapshot persistence
# -----------------------------------------------------------------------------
#
# Pod restarts wipe the SQLite emptyDir. To preserve the learned-domain
# table across restarts the store snapshots the DB to S3 (SeaweedFS in
# prod) on shutdown / periodic write, and restores it on startup. Both
# legs are best-effort: any S3 error is logged and the agent falls back
# to a fresh local DB. Never blocks startup.
#
# Activation: PRICE_DOMAIN_STATS_S3_SNAPSHOT=true (default true in the
# secret template). Bucket / endpoint / credentials come from the same
# AWS_* env vars used by the SAM runtime for artifact storage.

_S3_KEY_PREFIX = "domain_stats/"
_S3_KEY = "domain_stats.db"


def _s3_enabled() -> bool:
    return os.environ.get(
        "PRICE_DOMAIN_STATS_S3_SNAPSHOT", "false"
    ).strip().lower() in ("true", "1", "yes", "on")


def _s3_client():  # pragma: no cover -- pure plumbing
    """Construct a boto3 S3 client from AWS_* env. Returns None on failure."""
    try:
        import boto3
    except ImportError:
        logger.debug("boto3 not installed -- S3 snapshot disabled")
        return None
    endpoint = os.environ.get("S3_ENDPOINT_URL", "").strip()
    region = os.environ.get("AWS_REGION", "us-east-1")
    try:
        return boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            region_name=region,
        )
    except Exception as e:  # pragma: no cover
        logger.warning("boto3 client init failed: %s", e)
        return None


def _s3_bucket() -> str:
    return os.environ.get("S3_BUCKET_NAME", "").strip()


def restore_from_s3(target_path: Path) -> bool:
    """Try to download the snapshot from S3 to `target_path`.

    Returns True on success, False on any error or when S3 is disabled.
    Never raises -- callers fall back to an empty local DB.
    """
    if not _s3_enabled():
        return False
    bucket = _s3_bucket()
    if not bucket:
        logger.info("S3_BUCKET_NAME unset -- skipping domain_stats restore")
        return False
    client = _s3_client()
    if client is None:
        return False
    key = _S3_KEY_PREFIX + _S3_KEY
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(bucket, key, str(target_path))
        logger.info(
            "domain_stats restored from s3://%s/%s -> %s",
            bucket, key, target_path,
        )
        return True
    except Exception as e:
        # 404 (no snapshot yet) is the common path on first deploy.
        logger.info("no domain_stats snapshot in s3://%s/%s (%s)", bucket, key, e)
        return False


def snapshot_to_s3(source_path: Path) -> bool:
    """Upload `source_path` to the S3 snapshot key.

    Returns True on success, False on any error. Best-effort -- never
    raises into the caller.
    """
    if not _s3_enabled():
        return False
    bucket = _s3_bucket()
    if not bucket:
        return False
    client = _s3_client()
    if client is None:
        return False
    if not source_path.exists():
        logger.debug("domain_stats DB missing at %s -- skip snapshot", source_path)
        return False
    key = _S3_KEY_PREFIX + _S3_KEY
    try:
        client.upload_file(str(source_path), bucket, key)
        logger.info(
            "domain_stats snapshot uploaded to s3://%s/%s", bucket, key,
        )
        return True
    except Exception as e:
        logger.warning("S3 snapshot upload failed: %s", e)
        return False


# -----------------------------------------------------------------------------
# Singleton accessor
# -----------------------------------------------------------------------------

_INSTANCE: DomainStatsStore | None = None


def instance() -> DomainStatsStore:
    global _INSTANCE
    if _INSTANCE is None:
        db_path_env = os.environ.get("PRICE_DOMAIN_STATS_DB_PATH")
        db_path = Path(db_path_env) if db_path_env else DEFAULT_DB_PATH
        # Phase P: try to restore the latest snapshot from S3 BEFORE
        # the SQLite handle is created. If the local file already exists
        # we keep it (in-flight writes during restart take precedence).
        if not db_path.exists():
            restore_from_s3(db_path)
        _INSTANCE = DomainStatsStore(db_path=db_path)
    return _INSTANCE


def snapshot_now() -> bool:
    """Snapshot the current DB to S3. Safe to call from any thread."""
    if _INSTANCE is None:
        return False
    try:
        # Force a checkpoint so the on-disk file is fully consistent.
        with _INSTANCE._cursor() as conn:  # type: ignore[attr-defined]
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as e:  # pragma: no cover
        logger.debug("WAL checkpoint failed: %s", e)
    return snapshot_to_s3(_INSTANCE.db_path)


def reset() -> None:
    """Test-only: forget the cached singleton."""
    global _INSTANCE
    if _INSTANCE is not None:
        _INSTANCE.close()
    _INSTANCE = None
