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

    # Attempt connecting via pg8000
    try:
        import pg8000.native

        if CLOUD_SQL_CONNECTION_NAME and os.path.exists(f"/cloudsql/{CLOUD_SQL_CONNECTION_NAME}"):
            # Unix socket path used in Cloud Run
            socket_path = f"/cloudsql/{CLOUD_SQL_CONNECTION_NAME}"
            return pg8000.native.Connection(
                user=CLOUD_SQL_USER,
                password=CLOUD_SQL_PASSWORD,
                database=CLOUD_SQL_DB,
                unix_sock=socket_path,
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
        logger.debug("Cloud SQL connection attempt via pg8000 failed: %s", exc)

    # Attempt connecting via psycopg2
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
        logger.debug("Cloud SQL connection attempt via psycopg2 failed: %s", exc)

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
