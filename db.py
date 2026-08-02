import json
import os
from contextlib import contextmanager
from datetime import datetime

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()


@contextmanager
def get_conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    conn = psycopg2.connect(url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _fetchall(conn, query, params=()):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def _fetchone(conn, query, params=()):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return dict(row) if row else None


def _execute(conn, query, params=()):
    with conn.cursor() as cur:
        cur.execute(query, params)


def _executemany(conn, query, params_list):
    with conn.cursor() as cur:
        cur.executemany(query, params_list)


def init_db():
    with get_conn() as conn:
        _execute(conn, """
            CREATE TABLE IF NOT EXISTS articles (
                id          SERIAL PRIMARY KEY,
                url         TEXT UNIQUE NOT NULL,
                title       TEXT NOT NULL,
                source      TEXT NOT NULL,
                published_at TEXT,
                seen_at     TEXT NOT NULL
            )
        """)
        _execute(conn, """
            CREATE TABLE IF NOT EXISTS clusters (
                id          SERIAL PRIMARY KEY,
                summary     TEXT NOT NULL,
                category    TEXT NOT NULL,
                outlets     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                posted      INTEGER DEFAULT 0
            )
        """)
        _execute(conn, """
            CREATE TABLE IF NOT EXISTS cluster_articles (
                cluster_id  INTEGER REFERENCES clusters(id),
                article_id  INTEGER REFERENCES articles(id),
                PRIMARY KEY (cluster_id, article_id)
            )
        """)
        _execute(conn, """
            CREATE TABLE IF NOT EXISTS subscriptions (
                guild_id        TEXT PRIMARY KEY,
                channel_id      TEXT NOT NULL,
                categories      TEXT NOT NULL DEFAULT '[]',
                blacklist       TEXT NOT NULL DEFAULT '[]',
                sources         TEXT NOT NULL DEFAULT '[]',
                digest_limit    INTEGER NOT NULL DEFAULT 10,
                interval_hours  INTEGER NOT NULL DEFAULT 24,
                last_posted_at  TEXT
            )
        """)
    # Add columns if upgrading from older schema — each in its own transaction
    for col, definition in [
        ("interval_hours", "INTEGER NOT NULL DEFAULT 24"),
        ("last_posted_at", "TEXT"),
        ("blacklist", "TEXT NOT NULL DEFAULT '[]'"),
        ("sources", "TEXT NOT NULL DEFAULT '[]'"),
        ("digest_limit", "INTEGER NOT NULL DEFAULT 10"),
    ]:
        try:
            with get_conn() as conn:
                _execute(conn, f"ALTER TABLE subscriptions ADD COLUMN {col} {definition}")
        except Exception:
            pass  # column already exists


# ── Articles ──────────────────────────────────────────────────────────────────

def url_seen(url: str) -> bool:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT 1 FROM articles WHERE url = %s", (url,))
        return row is not None


def insert_article(url: str, title: str, source: str, published_at: str) -> int:
    with get_conn() as conn:
        _execute(conn,
            "INSERT INTO articles (url, title, source, published_at, seen_at) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (url) DO NOTHING",
            (url, title, source, published_at, datetime.utcnow().isoformat()),
        )
        row = _fetchone(conn, "SELECT id FROM articles WHERE url = %s", (url,))
        return row["id"]


# ── Clusters ──────────────────────────────────────────────────────────────────

def insert_cluster(summary: str, category: str, outlets: list, article_ids: list) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO clusters (summary, category, outlets, created_at) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (summary, category, json.dumps(outlets), datetime.utcnow().isoformat()),
            )
            cluster_id = cur.fetchone()[0]
        _executemany(conn,
            "INSERT INTO cluster_articles VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(cluster_id, aid) for aid in article_ids],
        )
        return cluster_id


def get_unposted_clusters(category_filter: str = None) -> list:
    with get_conn() as conn:
        if category_filter:
            return _fetchall(conn,
                "SELECT * FROM clusters WHERE posted = 0 AND LOWER(category) LIKE %s ORDER BY created_at",
                (f"%{category_filter.lower()}%",),
            )
        return _fetchall(conn,
            "SELECT * FROM clusters WHERE posted = 0 ORDER BY created_at"
        )


def get_recent_clusters(category_filter: str = None, limit: int = 50) -> list:
    """For manual /digest — returns recent clusters regardless of posted status."""
    with get_conn() as conn:
        if category_filter:
            return _fetchall(conn,
                "SELECT * FROM clusters WHERE LOWER(category) LIKE %s ORDER BY created_at DESC LIMIT %s",
                (f"%{category_filter.lower()}%", limit),
            )
        return _fetchall(conn,
            "SELECT * FROM clusters ORDER BY created_at DESC LIMIT %s", (limit,)
        )


def mark_posted(cluster_ids: list):
    if not cluster_ids:
        return
    with get_conn() as conn:
        _execute(conn,
            f"UPDATE clusters SET posted = 1 WHERE id = ANY(%s)",
            (cluster_ids,),
        )


# ── Subscriptions ─────────────────────────────────────────────────────────────

def upsert_subscription(guild_id: str, channel_id: str, categories: list = None,
                        interval_hours: int = None, blacklist: list = None,
                        sources: list = None, digest_limit: int = None):
    with get_conn() as conn:
        # Build dynamic update based on what's provided
        fields = {"channel_id": channel_id, "categories": json.dumps(categories or [])}
        if interval_hours is not None:
            fields["interval_hours"] = interval_hours
        if blacklist is not None:
            fields["blacklist"] = json.dumps(blacklist)
        if sources is not None:
            fields["sources"] = json.dumps(sources)
        if digest_limit is not None:
            fields["digest_limit"] = digest_limit

        cols = ", ".join(fields.keys())
        placeholders = ", ".join(["%s"] * len(fields))
        updates = ", ".join(f"{k} = EXCLUDED.{k}" for k in fields)
        _execute(conn,
            f"INSERT INTO subscriptions (guild_id, {cols}) VALUES (%s, {placeholders}) "
            f"ON CONFLICT (guild_id) DO UPDATE SET {updates}",
            (guild_id, *fields.values()),
        )


def update_last_posted(guild_id: str):
    with get_conn() as conn:
        _execute(conn,
            "UPDATE subscriptions SET last_posted_at = %s WHERE guild_id = %s",
            (datetime.utcnow().isoformat(), guild_id),
        )


def get_subscription(guild_id: str) -> dict | None:
    with get_conn() as conn:
        return _fetchone(conn,
            "SELECT * FROM subscriptions WHERE guild_id = %s", (guild_id,)
        )


def get_cluster_articles(cluster_id: int) -> list:
    with get_conn() as conn:
        return _fetchall(conn,
            "SELECT a.source, a.url FROM articles a "
            "JOIN cluster_articles ca ON ca.article_id = a.id "
            "WHERE ca.cluster_id = %s", (cluster_id,)
        )


def delete_subscription(guild_id: str):
    with get_conn() as conn:
        _execute(conn, "DELETE FROM subscriptions WHERE guild_id = %s", (guild_id,))


def get_all_subscriptions() -> list:
    with get_conn() as conn:
        return _fetchall(conn, "SELECT * FROM subscriptions")
