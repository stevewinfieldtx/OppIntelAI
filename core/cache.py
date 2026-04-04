"""
TDP Cache Layer
Handles caching of Targeted Decomposition Profiles with TTL expiration.
Uses SQLite for persistence. Designed to swap to Postgres later.
Also tracks fit-check engagement for lead intelligence.
"""
import aiosqlite
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from core.config import DATABASE_PATH, TDP_TTL_DAYS

logger = logging.getLogger(__name__)

DB_PATH = DATABASE_PATH


async def init_db():
    """Initialize the cache database."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tdp_cache (
                cache_key TEXT PRIMARY KEY,
                tdp_type TEXT NOT NULL,
                label TEXT NOT NULL,
                data TEXT NOT NULL,
                citations TEXT DEFAULT '[]',
                token_cost INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                force_expired INTEGER DEFAULT 0
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS hydration_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                solution TEXT NOT NULL,
                customer_url TEXT NOT NULL,
                industry TEXT,
                status TEXT NOT NULL,
                total_tokens INTEGER DEFAULT 0,
                cache_hits TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                completed_at TEXT,
                result_summary TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS fit_check_engagement (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                prospect_url TEXT NOT NULL,
                prospect_name TEXT DEFAULT '',
                prospect_industry TEXT DEFAULT '',
                solution_url TEXT NOT NULL,
                solution_name TEXT DEFAULT '',
                fit_score INTEGER DEFAULT 0,
                fit_level TEXT DEFAULT '',
                sections_viewed TEXT DEFAULT '[]',
                time_on_page_seconds INTEGER DEFAULT 0,
                cta_clicked TEXT DEFAULT '',
                referrer TEXT DEFAULT '',
                user_agent TEXT DEFAULT '',
                ip_hash TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        await db.commit()
        logger.info("Database initialized")


def _make_key(tdp_type: str, identifier: str) -> str:
    """Generate a cache key from type and identifier."""
    clean = identifier.lower().strip().replace(" ", "_")
    return f"{tdp_type}:{clean}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ttl_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=TDP_TTL_DAYS)).isoformat()


async def get_tdp(tdp_type: str, identifier: str) -> Optional[dict]:
    """
    Retrieve a cached TDP if it exists and hasn't expired.
    Returns None if not found or expired.
    """
    key = _make_key(tdp_type, identifier)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM tdp_cache WHERE cache_key = ?", (key,)
        )
        row = await cursor.fetchone()

        if row is None:
            logger.info(f"Cache MISS: {key}")
            return None

        # Check TTL expiration
        expires_at = datetime.fromisoformat(row["expires_at"])
        now = datetime.now(timezone.utc)

        if row["force_expired"] or expires_at < now:
            logger.info(f"Cache EXPIRED: {key}")
            await db.execute("DELETE FROM tdp_cache WHERE cache_key = ?", (key,))
            await db.commit()
            return None

        logger.info(f"Cache HIT: {key}")
        return {
            "cache_key": row["cache_key"],
            "tdp_type": row["tdp_type"],
            "label": row["label"],
            "data": json.loads(row["data"]),
            "citations": json.loads(row["citations"]),
            "token_cost": row["token_cost"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "expires_at": row["expires_at"],
            "from_cache": True,
        }


async def store_tdp(
    tdp_type: str,
    identifier: str,
    label: str,
    data: dict,
    citations: list = None,
    token_cost: int = 0,
) -> dict:
    """Store a TDP in the cache."""
    key = _make_key(tdp_type, identifier)
    now = _now()
    expires = _ttl_expiry()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO tdp_cache 
            (cache_key, tdp_type, label, data, citations, token_cost, created_at, updated_at, expires_at, force_expired)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                key,
                tdp_type,
                label,
                json.dumps(data),
                json.dumps(citations or []),
                token_cost,
                now,
                now,
                expires,
            ),
        )
        await db.commit()

    logger.info(f"Cache STORE: {key} | tokens={token_cost} | expires={expires}")

    return {
        "cache_key": key,
        "tdp_type": tdp_type,
        "label": label,
        "data": data,
        "citations": citations or [],
        "token_cost": token_cost,
        "created_at": now,
        "updated_at": now,
        "expires_at": expires,
        "from_cache": False,
    }


async def expire_tdp(tdp_type: str, identifier: str) -> bool:
    """Force-expire a TDP (scanner major relevance trigger)."""
    key = _make_key(tdp_type, identifier)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE tdp_cache SET force_expired = 1 WHERE cache_key = ?", (key,)
        )
        await db.commit()

        if cursor.rowcount > 0:
            logger.info(f"Cache FORCE EXPIRED: {key}")
            return True
        return False


# === Hydration Log ===

async def log_hydration(
    solution: str,
    customer_url: str,
    industry: str = None,
    status: str = "started",
) -> int:
    """Log a hydration request. Returns the log ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO hydration_log (solution, customer_url, industry, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (solution, customer_url, industry or "", status, _now()),
        )
        await db.commit()
        return cursor.lastrowid


async def update_hydration_log(
    log_id: int,
    status: str,
    total_tokens: int = 0,
    cache_hits: list = None,
    result_summary: str = None,
):
    """Update a hydration log entry."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE hydration_log 
            SET status = ?, total_tokens = ?, cache_hits = ?, completed_at = ?, result_summary = ?
            WHERE id = ?
            """,
            (
                status,
                total_tokens,
                json.dumps(cache_hits or []),
                _now(),
                result_summary,
                log_id,
            ),
        )
        await db.commit()


# === Fit Check Engagement Tracking ===

async def log_fit_check(
    session_id: str,
    prospect_url: str,
    solution_url: str,
    prospect_name: str = "",
    prospect_industry: str = "",
    solution_name: str = "",
    fit_score: int = 0,
    fit_level: str = "",
    referrer: str = "",
    user_agent: str = "",
    ip_hash: str = "",
) -> int:
    """Log a fit check engagement event. Returns the engagement ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        now = _now()
        cursor = await db.execute(
            """
            INSERT INTO fit_check_engagement 
            (session_id, prospect_url, prospect_name, prospect_industry,
             solution_url, solution_name, fit_score, fit_level,
             referrer, user_agent, ip_hash, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id, prospect_url, prospect_name, prospect_industry,
                solution_url, solution_name, fit_score, fit_level,
                referrer, user_agent, ip_hash, now, now,
            ),
        )
        await db.commit()
        logger.info(f"Fit engagement logged: session={session_id} prospect={prospect_url}")
        return cursor.lastrowid


async def update_fit_engagement(
    session_id: str,
    sections_viewed: list = None,
    time_on_page_seconds: int = 0,
    cta_clicked: str = "",
):
    """Update engagement tracking for an active fit check session."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE fit_check_engagement 
            SET sections_viewed = ?, time_on_page_seconds = ?, cta_clicked = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (
                json.dumps(sections_viewed or []),
                time_on_page_seconds,
                cta_clicked,
                _now(),
                session_id,
            ),
        )
        await db.commit()


async def get_fit_check_leads(limit: int = 50) -> list:
    """Get fit check leads for the vendor dashboard."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM fit_check_engagement 
            ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "prospect_url": row["prospect_url"],
                "prospect_name": row["prospect_name"],
                "prospect_industry": row["prospect_industry"],
                "solution_url": row["solution_url"],
                "solution_name": row["solution_name"],
                "fit_score": row["fit_score"],
                "fit_level": row["fit_level"],
                "sections_viewed": json.loads(row["sections_viewed"]),
                "time_on_page_seconds": row["time_on_page_seconds"],
                "cta_clicked": row["cta_clicked"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]


# === Stats ===

async def get_cache_stats() -> dict:
    """Get cache statistics for the dashboard."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT tdp_type, COUNT(*) as count FROM tdp_cache WHERE force_expired = 0 GROUP BY tdp_type"
        )
        type_counts = {row[0]: row[1] for row in await cursor.fetchall()}

        cursor = await db.execute("SELECT COUNT(*) FROM hydration_log")
        total_hydrations = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COALESCE(SUM(total_tokens), 0) FROM hydration_log")
        total_tokens = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM fit_check_engagement")
        total_fit_checks = (await cursor.fetchone())[0]

        return {
            "cached_tdps": type_counts,
            "total_hydrations": total_hydrations,
            "total_tokens_spent": total_tokens,
            "total_fit_checks": total_fit_checks,
        }
