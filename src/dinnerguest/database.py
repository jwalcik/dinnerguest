"""Cloud SQL PostgreSQL + pgvector database client for Dinnerguest.

Connects to Cloud SQL via Unix socket (/cloudsql/${CLOUD_SQL_CONNECTION_NAME})
or TCP host/port, and provides helper functions for vector RAG and GIN-indexed recipe lookups.
Falls back safely to local datasets when Cloud SQL is unconfigured or unreachable.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Cloud SQL Connection parameters
CLOUD_SQL_CONNECTION_NAME = os.getenv("CLOUD_SQL_CONNECTION_NAME")
CLOUD_SQL_DB = os.getenv("CLOUD_SQL_DB", "dinnerguest")
CLOUD_SQL_USER = os.getenv("CLOUD_SQL_USER", "dinnerguest_app")
CLOUD_SQL_PASSWORD = os.getenv("CLOUD_SQL_PASSWORD", "DinnerGuest2026!Secure")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", "5432"))


def clean_recipe_text_list(val: Any) -> List[str]:
    """Cleans and unpacks ingredients or instructions into a clean list of individual strings.

    Handles:
    - Lists/tuples of strings
    - JSON-encoded arrays
    - Python string literal repr arrays (e.g. "['item 1', 'item 2']") from datasets like Kaggle food.com
    - Singly nested stringified lists (e.g. ["['item 1', 'item 2']"])
    - Newline- or semicolon-separated text
    - Stripping trailing/leading brackets, quotes, and whitespace
    """
    if not val:
        return []

    # If it's a string, attempt JSON, AST literal_eval, or line splitting
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str or val_str in ("[]", "{}", "None", "null"):
            return []
        # 1. Try JSON decoding
        try:
            parsed = json.loads(val_str)
            if parsed != val_str:
                return clean_recipe_text_list(parsed)
        except Exception:
            pass

        # 2. Try Python AST literal evaluation for repr strings: "['a', 'b']"
        if (val_str.startswith("[") and val_str.endswith("]")) or (val_str.startswith("(") and val_str.endswith(")")):
            try:
                parsed = ast.literal_eval(val_str)
                if isinstance(parsed, (list, tuple)):
                    return clean_recipe_text_list(parsed)
            except Exception:
                pass

        # 3. Split by newlines if multi-line string
        if "\n" in val_str:
            lines = [line.strip() for line in val_str.splitlines() if line.strip()]
            return clean_recipe_text_list(lines)

        # 4. Split by semicolons if contains multiple items separated by semicolons
        if ";" in val_str and not val_str.startswith("&"):
            parts = [p.strip() for p in val_str.split(";") if p.strip()]
            return clean_recipe_text_list(parts)

        # 5. Single item: strip surrounding quotes
        cleaned = val_str.strip("'\" \t\r\n")
        return [cleaned] if cleaned else []

    # If it's a list or tuple
    if isinstance(val, (list, tuple)):
        items: List[str] = []
        for x in val:
            if isinstance(x, (list, tuple)):
                items.extend(clean_recipe_text_list(x))
            elif isinstance(x, str):
                x_str = x.strip()
                # Check if this string element itself is a stringified list like "['a', 'b']"
                if (x_str.startswith("[") and x_str.endswith("]")) or (x_str.startswith("(") and x_str.endswith(")")):
                    try:
                        parsed = ast.literal_eval(x_str)
                        if isinstance(parsed, (list, tuple)):
                            items.extend(clean_recipe_text_list(parsed))
                            continue
                    except Exception:
                        try:
                            parsed = json.loads(x_str)
                            if isinstance(parsed, (list, tuple)):
                                items.extend(clean_recipe_text_list(parsed))
                                continue
                        except Exception:
                            pass
                if "\n" in x_str:
                    items.extend(clean_recipe_text_list(x_str.splitlines()))
                else:
                    item = x_str.strip("'\" \t\r\n")
                    if item:
                        items.append(item)
            elif x is not None:
                items.append(str(x).strip())
        return items

    return [str(val).strip()]


def clean_ingredient_item(item: str) -> str:
    """Cleans an individual ingredient string, removing bullets and standardizing casing."""
    s = str(item).strip()
    s = re.sub(r"^(?:(?:\d+[\.\)]\s*)|(?:[\-\*•]\s*))+", "", s).strip()
    s = s.strip("'\"")
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    return s


def clean_instruction_step(step: str) -> str:
    """Cleans an individual instruction step string, standardizing capitalization and punctuation."""
    s = str(step).strip()
    s = re.sub(r"^(?:(?:\d+[\.\)]\s*)|(?:[\-\*•]\s*))+", "", s).strip()
    s = s.strip("'\"")
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    if s and not s.endswith((".", "!", "?", ":")):
        s += "."
    return s


def clean_recipe_title(title: str) -> str:
    """Normalizes recipe title presentation, converting all-lowercase titles to title case."""
    t = str(title).strip()
    if t.islower():
        return t.title()
    return t


def row_to_recipe_dict(row: tuple | list, score: Optional[float] = None) -> Dict[str, Any]:
    """Converts a database row into a canonical recipe dictionary with normalized fields."""
    recipe: Dict[str, Any] = {
        "id": str(row[0]),
        "title": clean_recipe_title(row[1]),
        "servings": int(row[2]) if row[2] else 4,
        "ingredients": clean_recipe_text_list(row[3]),
        "instructions": clean_recipe_text_list(row[4]),
        "tags": row[5] or [],
    }
    if score is not None:
        recipe["score"] = score
    elif len(row) > 6 and row[6] is not None:
        try:
            recipe["score"] = max(0.1, 1.0 - float(row[6]))
        except (ValueError, TypeError):
            pass
    return recipe


def is_cloud_sql_configured() -> bool:
    """Returns True if Cloud SQL connection parameters are defined."""
    return bool(
        os.getenv("CLOUD_SQL_CONNECTION_NAME")
        or os.getenv("DB_HOST")
        or os.getenv("CLOUD_SQL_INSTANCE")
        or os.getenv("CLOUD_SQL_DB")
        or CLOUD_SQL_CONNECTION_NAME
        or DB_HOST
    )


def get_db_connection() -> Any:
    """Establishes and returns a database connection to Cloud SQL PostgreSQL.

    Prefers pg8000 (pure Python) or psycopg2 if available.
    Returns None if drivers are missing or connection fails.
    """
    if not is_cloud_sql_configured():
        return None

    conn_name = os.getenv("CLOUD_SQL_CONNECTION_NAME", CLOUD_SQL_CONNECTION_NAME)
    db_name = os.getenv("CLOUD_SQL_DB", CLOUD_SQL_DB)
    db_user = os.getenv("CLOUD_SQL_USER", CLOUD_SQL_USER)
    db_password = os.getenv("CLOUD_SQL_PASSWORD", CLOUD_SQL_PASSWORD)
    db_host = os.getenv("DB_HOST", DB_HOST) or "127.0.0.1"
    db_port = int(os.getenv("DB_PORT", str(DB_PORT)))

    # 1. Attempt connecting via pg8000
    try:
        import pg8000.native

        if conn_name and os.path.exists(f"/cloudsql/{conn_name}"):
            base_dir = f"/cloudsql/{conn_name}"
            socket_file = f"{base_dir}/.s.PGSQL.5432"
            unix_sock = socket_file if os.path.exists(socket_file) else base_dir
            return pg8000.native.Connection(
                user=db_user,
                password=db_password,
                database=db_name,
                unix_sock=unix_sock,
            )
        elif db_host:
            return pg8000.native.Connection(
                user=db_user,
                password=db_password,
                database=db_name,
                host=db_host,
                port=db_port,
            )
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("Cloud SQL connection attempt via pg8000 failed: %s", exc)

    # 2. Attempt connecting via psycopg2
    try:
        import psycopg2

        if conn_name and os.path.exists(f"/cloudsql/{conn_name}"):
            return psycopg2.connect(
                dbname=db_name,
                user=db_user,
                password=db_password,
                host=f"/cloudsql/{conn_name}",
            )
        elif db_host:
            return psycopg2.connect(
                dbname=db_name,
                user=db_user,
                password=db_password,
                host=db_host,
                port=db_port,
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
            # Ensure vector extension and table exist
            try:
                conn.run("CREATE EXTENSION IF NOT EXISTS vector;")
            except Exception:
                pass

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

            try:
                conn.run("ALTER TABLE recipes ADD COLUMN IF NOT EXISTS embedding vector(768);")
            except Exception:
                pass

            count_rows = conn.run("SELECT COUNT(*) FROM recipes")
            curr_count = count_rows[0][0] if count_rows else 0

            # If Cloud SQL already has the Kaggle dataset (>= 100 rows), remove local JSON fixtures and skip seeding
            if curr_count >= 100:
                try:
                    conn.run("DELETE FROM recipes WHERE id LIKE 'recipe_%'")
                except Exception:
                    pass
                conn.close()
                return {"status": "skipped", "message": "Cloud SQL already populated with Kaggle recipes", "previous_count": curr_count}

            # Insert or update canonical recipes only when DB is empty/unseeded
            for r in canonical_recipes:
                # Try generating embedding for vector RAG
                vibe_text = f"{r['title']}. Tags: {', '.join(r.get('tags', []))}. Ingredients: {', '.join(r.get('ingredients', []))}."
                vec = generate_embedding(vibe_text)
                if vec:
                    vec_str = "[" + ",".join(str(round(v, 6)) for v in vec) + "]"
                    conn.run("""
                        INSERT INTO recipes (id, title, servings, ingredients_raw, instructions_raw, tags, embedding)
                        VALUES (:id, :title, :servings, :ing::jsonb, :inst::jsonb, :tags, :vec::vector)
                        ON CONFLICT (id) DO UPDATE SET
                            title = EXCLUDED.title,
                            servings = EXCLUDED.servings,
                            ingredients_raw = EXCLUDED.ingredients_raw,
                            instructions_raw = EXCLUDED.instructions_raw,
                            tags = EXCLUDED.tags,
                            embedding = EXCLUDED.embedding
                    """,
                        id=r["id"],
                        title=r["title"],
                        servings=r.get("servings", 4),
                        ing=json.dumps(r.get("ingredients", [])),
                        inst=json.dumps(r.get("instructions", [])),
                        tags=r.get("tags", []),
                        vec=vec_str,
                    )
                else:
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
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            except Exception:
                pass

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

            try:
                cur.execute("ALTER TABLE recipes ADD COLUMN IF NOT EXISTS embedding vector(768);")
            except Exception:
                pass

            cur.execute("SELECT COUNT(*) FROM recipes")
            row = cur.fetchone()
            curr_count = row[0] if row else 0

            if curr_count >= 100:
                try:
                    cur.execute("DELETE FROM recipes WHERE id LIKE 'recipe_%%'")
                    conn.commit()
                except Exception:
                    conn.rollback()
                cur.close()
                conn.close()
                return {"status": "skipped", "message": "Cloud SQL already populated with Kaggle recipes", "previous_count": curr_count}

            for r in canonical_recipes:
                vibe_text = f"{r['title']}. Tags: {', '.join(r.get('tags', []))}. Ingredients: {', '.join(r.get('ingredients', []))}."
                vec = generate_embedding(vibe_text)
                if vec:
                    vec_str = "[" + ",".join(str(round(v, 6)) for v in vec) + "]"
                    cur.execute("""
                        INSERT INTO recipes (id, title, servings, ingredients_raw, instructions_raw, tags, embedding)
                        VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::vector)
                        ON CONFLICT (id) DO UPDATE SET
                            title = EXCLUDED.title,
                            servings = EXCLUDED.servings,
                            ingredients_raw = EXCLUDED.ingredients_raw,
                            instructions_raw = EXCLUDED.instructions_raw,
                            tags = EXCLUDED.tags,
                            embedding = EXCLUDED.embedding
                    """, (
                        r["id"],
                        r["title"],
                        r.get("servings", 4),
                        json.dumps(r.get("ingredients", [])),
                        json.dumps(r.get("instructions", [])),
                        r.get("tags", []),
                        vec_str,
                    ))
                else:
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


_CACHED_GENAI_CLIENT = None
_CACHED_VERTEXAI_MODEL = None


def generate_embedding(text: str) -> Optional[List[float]]:
    """Generates a 768-dimensional embedding using Google GenAI or Vertex AI.

    Falls back safely to None if credentials or client libraries are unavailable.
    Caches the client/model instance across calls to avoid memory leaks and connection thrashing.
    """
    global _CACHED_GENAI_CLIENT, _CACHED_VERTEXAI_MODEL
    project = os.getenv("PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    # 1. Attempt using google.genai Client
    try:
        from google import genai
        if project:
            if _CACHED_GENAI_CLIENT is None:
                _CACHED_GENAI_CLIENT = genai.Client(vertexai=True, project=project, location=location)
            res = _CACHED_GENAI_CLIENT.models.embed_content(
                model="text-embedding-004",
                contents=text,
            )
            if hasattr(res, "embeddings") and res.embeddings:
                return list(res.embeddings[0].values)
    except Exception as exc:
        logger.debug("google.genai embedding generation unavailable: %s", exc)

    # 2. Attempt using vertexai TextEmbeddingModel
    try:
        from vertexai.language_models import TextEmbeddingModel
        if _CACHED_VERTEXAI_MODEL is None:
            _CACHED_VERTEXAI_MODEL = TextEmbeddingModel.from_pretrained("text-embedding-004")
        embeddings = _CACHED_VERTEXAI_MODEL.get_embeddings([text])
        if embeddings:
            return list(embeddings[0].values)
    except Exception as exc:
        logger.debug("vertexai TextEmbeddingModel unavailable: %s", exc)

    return None


def search_recipes_by_vector(
    query_text: str,
    limit: int = 5,
) -> Optional[List[Dict[str, Any]]]:
    """Queries Cloud SQL PostgreSQL using pgvector cosine distance (<=>).

    Returns None if Cloud SQL is unreachable/unconfigured, allowing seamless
    fallback to the local recipe dataset for tests and offline development.
    """
    conn = get_db_connection()
    if conn is None:
        return None

    try:
        # 1. Attempt dense vector search via pgvector if an embedding can be generated
        dish_words = [
            w.rstrip("s") for w in re.findall(r"\b[a-zA-Z]{3,}\b", query_text.lower())
            if w not in {"food", "dish", "make", "cook", "like", "want", "some", "sort", "kind", "recipe", "with", "and", "the", "for", "feel", "craving"}
        ]
        query_vector = generate_embedding(query_text)
        if query_vector is not None:
            vec_str = "[" + ",".join(str(round(v, 6)) for v in query_vector) + "]"
            if hasattr(conn, "run"):
                try:
                    rows = conn.run("""
                        SELECT id, title, servings, ingredients_raw, instructions_raw, tags,
                               (embedding <=> :vec::vector) AS distance
                        FROM recipes
                        WHERE embedding IS NOT NULL
                        ORDER BY distance ASC
                        LIMIT :limit
                    """, vec=vec_str, limit=limit)
                    if rows:
                        recipes = [row_to_recipe_dict(row) for row in rows]
                        # If embeddings are partially populated and top vector hit doesn't match the dish title,
                        # supplement with exact title matches from the full recipes table
                        if dish_words and not all(dw in recipes[0]["title"].lower() for dw in dish_words[:2]):
                            try:
                                dish_pattern = "%" + "%".join(dish_words[:3]) + "%"
                                title_rows = conn.run("""
                                    SELECT id, title, servings, ingredients_raw, instructions_raw, tags
                                    FROM recipes
                                    WHERE title ILIKE :dp
                                    LIMIT :limit
                                """, dp=dish_pattern, limit=limit)
                                if title_rows:
                                    seen_ids = set()
                                    merged = []
                                    for tr in title_rows:
                                        rd = row_to_recipe_dict(tr, score=0.95)
                                        seen_ids.add(rd["id"])
                                        merged.append(rd)
                                    for vr in recipes:
                                        if vr["id"] not in seen_ids:
                                            merged.append(vr)
                                    recipes = merged[:limit]
                            except Exception:
                                pass
                        conn.close()
                        return recipes
                except Exception as pg_err:
                    logger.debug("pgvector cosine query failed, falling back to text rank: %s", pg_err)

            elif hasattr(conn, "cursor"):
                try:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT id, title, servings, ingredients_raw, instructions_raw, tags,
                               (embedding <=> %s::vector) AS distance
                        FROM recipes
                        WHERE embedding IS NOT NULL
                        ORDER BY distance ASC
                        LIMIT %s
                    """, (vec_str, limit))
                    rows = cur.fetchall()
                    if rows:
                        recipes = [row_to_recipe_dict(row) for row in rows]
                        if dish_words and not all(dw in recipes[0]["title"].lower() for dw in dish_words[:2]):
                            try:
                                dish_pattern = "%" + "%".join(dish_words[:3]) + "%"
                                cur.execute("""
                                    SELECT id, title, servings, ingredients_raw, instructions_raw, tags
                                    FROM recipes
                                    WHERE title ILIKE %s
                                    LIMIT %s
                                """, (dish_pattern, limit))
                                title_rows = cur.fetchall()
                                if title_rows:
                                    seen_ids = set()
                                    merged = []
                                    for tr in title_rows:
                                        rd = row_to_recipe_dict(tr, score=0.95)
                                        seen_ids.add(rd["id"])
                                        merged.append(rd)
                                    for vr in recipes:
                                        if vr["id"] not in seen_ids:
                                            merged.append(vr)
                                    recipes = merged[:limit]
                            except Exception:
                                pass
                        cur.close()
                        conn.close()
                        return recipes
                    cur.close()
                except Exception as pg_err:
                    logger.debug("pgvector cosine query failed, falling back to text rank: %s", pg_err)

        # 2. Database rank fallback: rank by keyword overlap across title, tags, and ingredients
        q_term = f"%{query_text.lower().strip()}%"
        q_raw = query_text.lower().strip()
        words = [w for w in re.findall(r"\b[a-zA-Z]{3,}\b", q_raw) if w not in {"food", "dish", "make", "cook", "like", "want"}]
        first_word = f"%{words[0]}%" if words else q_term

        if hasattr(conn, "run"):
            rows = conn.run("""
                SELECT id, title, servings, ingredients_raw, instructions_raw, tags
                FROM recipes
                WHERE title ILIKE :q OR ingredients_raw::text ILIKE :q OR :q_raw = ANY(tags)
                   OR title ILIKE :first OR ingredients_raw::text ILIKE :first
                ORDER BY (CASE WHEN title ILIKE :q THEN 0 WHEN title ILIKE :first THEN 1 ELSE 2 END), id
                LIMIT :limit
            """, q=q_term, q_raw=q_raw, first=first_word, limit=limit)
            recipes = [row_to_recipe_dict(row, score=1.0) for row in rows]
            conn.close()
            return recipes if recipes else None

        elif hasattr(conn, "cursor"):
            cur = conn.cursor()
            cur.execute("""
                SELECT id, title, servings, ingredients_raw, instructions_raw, tags
                FROM recipes
                WHERE title ILIKE %s OR ingredients_raw::text ILIKE %s OR %s = ANY(tags)
                   OR title ILIKE %s OR ingredients_raw::text ILIKE %s
                ORDER BY (CASE WHEN title ILIKE %s THEN 0 WHEN title ILIKE %s THEN 1 ELSE 2 END), id
                LIMIT %s
            """, (q_term, q_term, q_raw, first_word, first_word, q_term, first_word, limit))
            rows = cur.fetchall()
            recipes = [row_to_recipe_dict(row, score=1.0) for row in rows]
            cur.close()
            conn.close()
            return recipes if recipes else None

    except Exception as exc:
        logger.warning("Error running search_recipes_by_vector in Cloud SQL: %s", exc)
        try:
            conn.close()
        except Exception:
            pass
        return None

    return None


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
            recipes = [row_to_recipe_dict(row) for row in rows]
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
            recipes = [row_to_recipe_dict(row) for row in rows]
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
            recipes = [row_to_recipe_dict(row) for row in rows]
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
            recipes = [row_to_recipe_dict(row) for row in rows]
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


def _execute_named_query(conn: Any, sql_str: str, params: Dict[str, Any]) -> List[Any]:
    """Executes a SQL query with :named parameters on either pg8000.native or DB-API 2.0 cursor."""
    if hasattr(conn, "run"):
        return conn.run(sql_str, **params)
    elif hasattr(conn, "cursor"):
        param_order: List[Any] = []

        def _repl(match: re.Match) -> str:
            key = match.group(1)
            param_order.append(params[key])
            return "%s"

        formatted_sql = re.sub(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)", _repl, sql_str)
        cur = conn.cursor()
        cur.execute(formatted_sql, tuple(param_order))
        rows = cur.fetchall()
        cur.close()
        return rows
    return []


def extract_sql_ingredient_stem(phrase: str) -> Optional[str]:
    """Extracts a high-recall singular core keyword from a user ingredient for SQL matching."""
    from dinnerguest.tools.pantry_search_tool import normalize_ingredient_phrase, stem_ingredient_word

    norm = normalize_ingredient_phrase(phrase)
    if not norm:
        return None

    # Skip generic spice/seasoning wildcards as required SQL stems
    if any(w in norm for w in ("dry spice", "spices", "seasoning")):
        return None

    words = [stem_ingredient_word(w) for w in norm.split() if len(w) >= 2]
    words = [w for w in words if w]
    if not words:
        return None

    # Map common multi-word ingredients to their primary discriminating SQL stem
    priority_stems = ("chicken", "beef", "pork", "turkey", "salmon", "tuna", "shrimp", "ranch", "bacon", "lettuce", "tomato", "onion", "apple", "oat", "carrot", "potato")
    for p in priority_stems:
        if p in words:
            return p

    if "bun" in words or "roll" in words:
        return "bun"
    if "sugar" in words:
        return "sugar"

    return words[-1]


def fetch_pantry_candidates_from_db(
    ingredients: List[str],
    category: Optional[str] = None,
    limit: int = 100,
) -> Optional[List[Dict[str, Any]]]:
    """Queries candidate recipes from Cloud SQL using hybrid vector RAG and multi-ingredient overlap SQL.

    1. Uses dense vector similarity (search_recipes_by_vector) across all provided ingredients
       (and specific dish category if not a generic meal label like 'dinner').
    2. Queries Cloud SQL recipes ranked by the number of matching ingredient stems
       (prioritizing recipes that match multiple user ingredients and have concise ingredient lists).
    3. Merges candidate sets and deduplicates by recipe ID.
    """
    candidates_by_id: Dict[str, Dict[str, Any]] = {}
    generic_meal_categories = {"dinner", "lunch", "supper", "main", "main course", "entree", "savory"}
    cat_lower = category.strip().lower() if category else None

    # 1. Semantic Vector RAG Retrieval (using all user ingredients)
    query_terms = []
    if cat_lower and cat_lower not in generic_meal_categories:
        query_terms.append(cat_lower)
    if ingredients:
        query_terms.extend([i.strip() for i in ingredients if i.strip()])
    vibe_query = ", ".join(query_terms).strip()

    if vibe_query:
        try:
            vector_results = search_recipes_by_vector(vibe_query, limit=min(limit, 50))
            if vector_results:
                for r in vector_results:
                    candidates_by_id[str(r["id"])] = r
        except Exception as vec_err:
            logger.debug("Vector candidate retrieval in fetch_pantry_candidates_from_db failed: %s", vec_err)

    # 2. Multi-Ingredient Overlap SQL Lookup
    conn = get_db_connection()
    if conn is None:
        return list(candidates_by_id.values()) if candidates_by_id else None

    try:
        stems: List[str] = []
        seen_stems = set()
        for ing in ingredients:
            st = extract_sql_ingredient_stem(ing)
            if st and len(st) > 2 and st not in seen_stems:
                seen_stems.add(st)
                stems.append(st)

        if not stems and not cat_lower:
            conn.close()
            return list(candidates_by_id.values()) if candidates_by_id else None

        params: Dict[str, Any] = {"limit": limit}
        base_conds: List[str] = []

        # Only apply SQL category restriction for specific course types (NEVER for generic 'dinner'/'lunch')
        if cat_lower and cat_lower not in generic_meal_categories:
            if cat_lower == "dessert":
                base_conds.append(
                    "(title ILIKE '%dessert%' OR title ILIKE '%crisp%' OR title ILIKE '%cookie%' "
                    "OR title ILIKE '%cake%' OR title ILIKE '%pie%' OR title ILIKE '%crumble%' "
                    "OR title ILIKE '%tart%' OR title ILIKE '%brownie%' OR title ILIKE '%cobbler%' "
                    "OR 'dessert' = ANY(tags) OR 'sweet' = ANY(tags))"
                )
            elif cat_lower in ("soup", "stew", "chowder"):
                base_conds.append(
                    "(title ILIKE '%soup%' OR title ILIKE '%stew%' OR title ILIKE '%chowder%' "
                    "OR 'soup' = ANY(tags) OR 'stew' = ANY(tags))"
                )
            elif cat_lower in ("breakfast", "brunch"):
                base_conds.append(
                    "(title ILIKE '%breakfast%' OR title ILIKE '%pancake%' OR title ILIKE '%waffle%' "
                    "OR title ILIKE '%omelet%' OR title ILIKE '%egg%' OR 'breakfast' = ANY(tags))"
                )
            else:
                base_conds.append("(title ILIKE :cat OR :cat_raw = ANY(tags))")
                params["cat"] = f"%{cat_lower}%"
                params["cat_raw"] = cat_lower

        ing_len_sql = "(CASE WHEN jsonb_typeof(ingredients_raw) = 'array' THEN jsonb_array_length(ingredients_raw) ELSE 99 END)"

        if stems:
            hit_exprs = []
            for idx, st in enumerate(stems):
                key = f"stem_{idx}"
                params[key] = f"%{st}%"
                hit_exprs.append(f"(CASE WHEN ingredients_raw::text ILIKE :{key} THEN 1 ELSE 0 END)")

            match_hits_sql = " + ".join(hit_exprs)
            num_stems = len(stems)
            params["max_ings"] = max(8, num_stems + 7)

            # Tier 1: High ingredient overlap (e.g. >= 4 of 6 stems, >= 3 of 4 stems, >= 2 of 2-3 stems)
            strict_min_hits = max(2, num_stems - 2) if num_stems >= 4 else min(2, num_stems)
            params["min_hits"] = strict_min_hits

            tier1_conds = list(base_conds) + [
                f"({match_hits_sql}) >= :min_hits",
                f"{ing_len_sql} BETWEEN 2 AND :max_ings",
            ]
            where_tier1 = " AND ".join(tier1_conds)
            sql_tier1 = f"""
                SELECT id, title, servings, ingredients_raw, instructions_raw, tags
                FROM recipes
                WHERE {where_tier1}
                ORDER BY ({match_hits_sql}) DESC, {ing_len_sql} ASC
                LIMIT :limit
            """
            rows = _execute_named_query(conn, sql_tier1, params)
            for row in rows:
                r_id = str(row[0])
                if r_id not in candidates_by_id:
                    candidates_by_id[r_id] = row_to_recipe_dict(row)

            # Tier 2 fallback if Tier 1 found fewer than 10 recipes and strict_min_hits > 1
            if len(rows) < 10 and strict_min_hits > 1:
                fallback_min = 2 if (num_stems >= 3 and cat_lower != "dessert") else 1
                if fallback_min < strict_min_hits:
                    params["min_hits"] = fallback_min
                    tier2_conds = list(base_conds) + [
                        f"({match_hits_sql}) >= :min_hits",
                        f"{ing_len_sql} BETWEEN 2 AND :max_ings",
                    ]
                    where_tier2 = " AND ".join(tier2_conds)
                    sql_tier2 = f"""
                        SELECT id, title, servings, ingredients_raw, instructions_raw, tags
                        FROM recipes
                        WHERE {where_tier2}
                        ORDER BY ({match_hits_sql}) DESC, {ing_len_sql} ASC
                        LIMIT :limit
                    """
                    rows2 = _execute_named_query(conn, sql_tier2, params)
                    for row in rows2:
                        r_id = str(row[0])
                        if r_id not in candidates_by_id:
                            candidates_by_id[r_id] = row_to_recipe_dict(row)
        else:
            where_clause = " AND ".join(base_conds) if base_conds else "TRUE"
            sql_str = f"""
                SELECT id, title, servings, ingredients_raw, instructions_raw, tags
                FROM recipes
                WHERE {where_clause}
                LIMIT :limit
            """
            rows = _execute_named_query(conn, sql_str, params)
            for row in rows:
                r_id = str(row[0])
                if r_id not in candidates_by_id:
                    candidates_by_id[r_id] = row_to_recipe_dict(row)

        conn.close()

    except Exception as exc:
        logger.warning("Error fetching pantry candidates from Cloud SQL: %s", exc)
        try:
            conn.close()
        except Exception:
            pass

    return list(candidates_by_id.values()) if candidates_by_id else None

