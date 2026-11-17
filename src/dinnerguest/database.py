"""Cloud SQL PostgreSQL + pgvector database client for Dinnerguest.

Connects to Cloud SQL via Unix socket (/cloudsql/${CLOUD_SQL_CONNECTION_NAME})
or TCP host/port, and provides helper functions for vector RAG and GIN-indexed recipe lookups.
Falls back safely to local datasets when Cloud SQL is unconfigured or unreachable.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Cloud SQL Connection parameters
CLOUD_SQL_CONNECTION_NAME = os.getenv("CLOUD_SQL_CONNECTION_NAME")
CLOUD_SQL_DB = os.getenv("CLOUD_SQL_DB", "dinnerguest")
CLOUD_SQL_USER = os.getenv("CLOUD_SQL_USER", "dinnerguest_app")
CLOUD_SQL_PASSWORD = os.getenv("CLOUD_SQL_PASSWORD", "DinnerGuest2026!Secure")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", "5432"))


def is_cloud_sql_configured() -> bool:
    """Returns True if Cloud SQL connection parameters are defined."""
    return bool(CLOUD_SQL_CONNECTION_NAME or DB_HOST)


def get_db_connection() -> Any:
    """Establishes and returns a database connection to Cloud SQL PostgreSQL.

    Prefers pg8000 (pure Python) or psycopg2 if available.
    Returns None if drivers are missing or connection fails.
    """
    if not is_cloud_sql_configured():
        return None

    # 1. Attempt connecting via pg8000
    try:
        import pg8000.native

        if CLOUD_SQL_CONNECTION_NAME:
            base_dir = f"/cloudsql/{CLOUD_SQL_CONNECTION_NAME}"
            socket_file = f"{base_dir}/.s.PGSQL.5432"
            unix_sock = socket_file if os.path.exists(socket_file) else base_dir
            if os.path.exists(base_dir):
                return pg8000.native.Connection(
                    user=CLOUD_SQL_USER,
                    password=CLOUD_SQL_PASSWORD,
                    database=CLOUD_SQL_DB,
                    unix_sock=unix_sock,
                )
        elif DB_HOST:
            return pg8000.native.Connection(
                user=CLOUD_SQL_USER,
                password=CLOUD_SQL_PASSWORD,
                database=CLOUD_SQL_DB,
                host=DB_HOST,
                port=DB_PORT,
            )
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("Cloud SQL connection attempt via pg8000 failed: %s", exc)

    # 2. Attempt connecting via psycopg2
    try:
        import psycopg2

        if CLOUD_SQL_CONNECTION_NAME and os.path.exists(f"/cloudsql/{CLOUD_SQL_CONNECTION_NAME}"):
            return psycopg2.connect(
                dbname=CLOUD_SQL_DB,
                user=CLOUD_SQL_USER,
                password=CLOUD_SQL_PASSWORD,
                host=f"/cloudsql/{CLOUD_SQL_CONNECTION_NAME}",
            )
        elif DB_HOST:
            return psycopg2.connect(
                dbname=CLOUD_SQL_DB,
                user=CLOUD_SQL_USER,
                password=CLOUD_SQL_PASSWORD,
                host=DB_HOST,
                port=DB_PORT,
            )
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("Cloud SQL connection attempt via psycopg2 failed: %s", exc)

    return None


def get_database_status() -> Dict[str, Any]:
    """Returns runtime diagnostic connectivity status for Cloud SQL PostgreSQL."""
    configured = is_cloud_sql_configured()
    status: Dict[str, Any] = {
        "configured": configured,
        "instance_connection_name": CLOUD_SQL_CONNECTION_NAME,
        "database": CLOUD_SQL_DB,
        "user": CLOUD_SQL_USER,
        "connected": False,
        "driver": None,
        "recipe_count": 0,
        "error": None,
    }

    if not configured:
        status["error"] = "No Cloud SQL connection settings defined (CLOUD_SQL_CONNECTION_NAME or DB_HOST unset)."
        return status

    if CLOUD_SQL_CONNECTION_NAME:
        base_dir = f"/cloudsql/{CLOUD_SQL_CONNECTION_NAME}"
        status["socket_dir_exists"] = os.path.exists(base_dir)
        status["socket_file_exists"] = os.path.exists(f"{base_dir}/.s.PGSQL.5432")

    conn = None
    try:
        conn = get_db_connection()
        if conn is None:
            status["error"] = "Connection attempt failed (check Cloud SQL Auth Proxy socket and credentials)."
            return status

        status["connected"] = True
        if hasattr(conn, "run"):
            status["driver"] = "pg8000"
            rows = conn.run("SELECT COUNT(*) FROM recipes")
            if rows:
                status["recipe_count"] = rows[0][0]
        elif hasattr(conn, "cursor"):
            status["driver"] = "psycopg2"
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM recipes")
            row = cur.fetchone()
            if row:
                status["recipe_count"] = row[0]
            cur.close()

    except Exception as exc:
        status["error"] = str(exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return status


def init_and_seed_db() -> Dict[str, Any]:
    """Initializes tables and seeds canonical recipes if Cloud SQL database is empty."""
    conn = get_db_connection()
    if conn is None:
        return {"status": "skipped", "message": "Cloud SQL not connected"}

    try:
        from pathlib import Path
        canonical_path = Path(__file__).parent / "data" / "recipes_canonical.json"
        if not canonical_path.exists():
            return {"status": "skipped", "message": "recipes_canonical.json not found"}

        with open(canonical_path, "r", encoding="utf-8") as f:
            canonical_recipes = json.load(f)

        if hasattr(conn, "run"):
            # Ensure table exists
            conn.run("""
                CREATE TABLE IF NOT EXISTS recipes (
                    id VARCHAR(64) PRIMARY KEY,
                    title TEXT NOT NULL,
                    servings INTEGER NOT NULL DEFAULT 4,
                    ingredients_raw JSONB NOT NULL DEFAULT '[]'::jsonb,
                    instructions_raw JSONB NOT NULL DEFAULT '[]'::jsonb,
                    tags TEXT[] NOT NULL DEFAULT '{}'
                )
            """)
            count_rows = conn.run("SELECT COUNT(*) FROM recipes")
            curr_count = count_rows[0][0] if count_rows else 0

            # Insert or update canonical recipes
            for r in canonical_recipes:
                conn.run("""
                    INSERT INTO recipes (id, title, servings, ingredients_raw, instructions_raw, tags)
                    VALUES (:id, :title, :servings, :ing::jsonb, :inst::jsonb, :tags)
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        servings = EXCLUDED.servings,
                        ingredients_raw = EXCLUDED.ingredients_raw,
                        instructions_raw = EXCLUDED.instructions_raw,
                        tags = EXCLUDED.tags
                """,
                    id=r["id"],
                    title=r["title"],
                    servings=r.get("servings", 4),
                    ing=json.dumps(r.get("ingredients", [])),
                    inst=json.dumps(r.get("instructions", [])),
                    tags=r.get("tags", []),
                )
            conn.close()
            logger.info("Successfully synced %d recipes into Cloud SQL PostgreSQL database.", len(canonical_recipes))
            return {"status": "success", "synced_recipes": len(canonical_recipes), "previous_count": curr_count}

        elif hasattr(conn, "cursor"):
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS recipes (
                    id VARCHAR(64) PRIMARY KEY,
                    title TEXT NOT NULL,
                    servings INTEGER NOT NULL DEFAULT 4,
                    ingredients_raw JSONB NOT NULL DEFAULT '[]'::jsonb,
                    instructions_raw JSONB NOT NULL DEFAULT '[]'::jsonb,
                    tags TEXT[] NOT NULL DEFAULT '{}'
                )
            """)
            cur.execute("SELECT COUNT(*) FROM recipes")
            row = cur.fetchone()
            curr_count = row[0] if row else 0

            for r in canonical_recipes:
                cur.execute("""
                    INSERT INTO recipes (id, title, servings, ingredients_raw, instructions_raw, tags)
                    VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        servings = EXCLUDED.servings,
                        ingredients_raw = EXCLUDED.ingredients_raw,
                        instructions_raw = EXCLUDED.instructions_raw,
                        tags = EXCLUDED.tags
                """, (
                    r["id"],
                    r["title"],
                    r.get("servings", 4),
                    json.dumps(r.get("ingredients", [])),
                    json.dumps(r.get("instructions", [])),
                    r.get("tags", []),
                ))
            conn.commit()
            cur.close()
            conn.close()
            logger.info("Successfully synced %d recipes into Cloud SQL PostgreSQL database.", len(canonical_recipes))
            return {"status": "success", "synced_recipes": len(canonical_recipes), "previous_count": curr_count}

    except Exception as exc:
        logger.warning("Error initializing/seeding Cloud SQL database: %s", exc)
        try:
            conn.close()
        except Exception:
            pass
        return {"status": "error", "error": str(exc)}

    return {"status": "skipped"}


def search_recipes_in_db(
    query_text: Optional[str] = None,
    ingredient: Optional[str] = None,
    limit: int = 20,
) -> Optional[List[Dict[str, Any]]]:
    """Queries Cloud SQL PostgreSQL database directly for recipes matching query and ingredients."""
    conn = get_db_connection()
    if conn is None:
        return None

    try:
        q_term = f"%{query_text.lower()}%" if query_text else "%"
        ing_term = f"%{ingredient.lower()}%" if ingredient else "%"

        if hasattr(conn, "run"):
            rows = conn.run("""
                SELECT id, title, servings, ingredients_raw, instructions_raw, tags
                FROM recipes
                WHERE (title ILIKE :q OR ingredients_raw::text ILIKE :q OR :q_raw = ANY(tags))
                  AND (ingredients_raw::text ILIKE :ing OR title ILIKE :ing)
                LIMIT :l
            """,
                q=q_term,
                q_raw=query_text.lower() if query_text else "",
                ing=ing_term,
                l=limit,
            )
            recipes = []
            for row in rows:
                recipes.append({
                    "id": row[0],
                    "title": row[1],
                    "servings": row[2],
                    "ingredients": row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]"),
                    "instructions": row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]"),
                    "tags": row[5] or [],
                })
            conn.close()
            return recipes

        elif hasattr(conn, "cursor"):
            cur = conn.cursor()
            cur.execute("""
                SELECT id, title, servings, ingredients_raw, instructions_raw, tags
                FROM recipes
                WHERE (title ILIKE %s OR ingredients_raw::text ILIKE %s OR %s = ANY(tags))
                  AND (ingredients_raw::text ILIKE %s OR title ILIKE %s)
                LIMIT %s
            """, (
                q_term,
                q_term,
                query_text.lower() if query_text else "",
                ing_term,
                ing_term,
                limit,
            ))
            rows = cur.fetchall()
            recipes = []
            for row in rows:
                recipes.append({
                    "id": row[0],
                    "title": row[1],
                    "servings": row[2],
                    "ingredients": row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]"),
                    "instructions": row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]"),
                    "tags": row[5] or [],
                })
            cur.close()
            conn.close()
            return recipes

    except Exception as exc:
        logger.warning("Error running search_recipes_in_db in Cloud SQL: %s", exc)
        try:
            conn.close()
        except Exception:
            pass
        return None

    return None


def fetch_recipes_from_db(
    query_text: Optional[str] = None,
    limit: int = 1000,
) -> Optional[List[Dict[str, Any]]]:
    """Queries recipes from Cloud SQL PostgreSQL if available.

    Returns None if Cloud SQL is unreachable, allowing seamless fallback to local dataset.
    """
    conn = get_db_connection()
    if conn is None:
        return None

    try:
        # Check pg8000 connection
        if hasattr(conn, "run"):
            if query_text:
                rows = conn.run(
                    "SELECT id, title, servings, ingredients_raw, instructions_raw, tags FROM recipes WHERE title ILIKE :q OR :q = ANY(tags) LIMIT :l",
                    q=f"%{query_text}%",
                    l=limit,
                )
            else:
                rows = conn.run(
                    "SELECT id, title, servings, ingredients_raw, instructions_raw, tags FROM recipes LIMIT :l",
                    l=limit,
                )
            recipes = []
            for row in rows:
                recipes.append({
                    "id": row[0],
                    "title": row[1],
                    "servings": row[2],
                    "ingredients": row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]"),
                    "instructions": row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]"),
                    "tags": row[5] or [],
                })
            conn.close()
            return recipes if recipes else None

        # Check psycopg2 connection
        elif hasattr(conn, "cursor"):
            cur = conn.cursor()
            if query_text:
                cur.execute(
                    "SELECT id, title, servings, ingredients_raw, instructions_raw, tags FROM recipes WHERE title ILIKE %s OR %s = ANY(tags) LIMIT %s",
                    (f"%{query_text}%", query_text, limit),
                )
            else:
                cur.execute(
                    "SELECT id, title, servings, ingredients_raw, instructions_raw, tags FROM recipes LIMIT %s",
                    (limit,),
                )
            rows = cur.fetchall()
            recipes = []
            for row in rows:
                recipes.append({
                    "id": row[0],
                    "title": row[1],
                    "servings": row[2],
                    "ingredients": row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]"),
                    "instructions": row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]"),
                    "tags": row[5] or [],
                })
            cur.close()
            conn.close()
            return recipes if recipes else None

    except Exception as exc:
        logger.warning("Error querying recipes table in Cloud SQL: %s", exc)
        try:
            conn.close()
        except Exception:
            pass
        return None

    return None
