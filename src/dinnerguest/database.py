"""Cloud SQL PostgreSQL + pgvector database client for Dinnerguest.

Connects to Cloud SQL via Unix socket (/cloudsql/${CLOUD_SQL_CONNECTION_NAME})
or TCP host/port, and provides helper functions for vector RAG and GIN-indexed recipe lookups.
Falls back safely to local datasets when Cloud SQL is unconfigured or unreachable.
"""

from __future__ import annotations

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

            # Insert or update canonical recipes
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
                        recipes = []
                        for row in rows:
                            recipes.append({
                                "id": row[0],
                                "title": row[1],
                                "servings": row[2],
                                "ingredients": row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]"),
                                "instructions": row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]"),
                                "tags": row[5] or [],
                                "score": max(0.1, 1.0 - float(row[6])) if row[6] is not None else 1.0,
                            })
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
                        recipes = []
                        for row in rows:
                            recipes.append({
                                "id": row[0],
                                "title": row[1],
                                "servings": row[2],
                                "ingredients": row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]"),
                                "instructions": row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]"),
                                "tags": row[5] or [],
                                "score": max(0.1, 1.0 - float(row[6])) if row[6] is not None else 1.0,
                            })
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
            recipes = []
            for row in rows:
                recipes.append({
                    "id": row[0],
                    "title": row[1],
                    "servings": row[2],
                    "ingredients": row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]"),
                    "instructions": row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]"),
                    "tags": row[5] or [],
                    "score": 1.0,
                })
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
            recipes = []
            for row in rows:
                recipes.append({
                    "id": row[0],
                    "title": row[1],
                    "servings": row[2],
                    "ingredients": row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]"),
                    "instructions": row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]"),
                    "tags": row[5] or [],
                    "score": 1.0,
                })
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


def fetch_pantry_candidates_from_db(
    ingredients: List[str],
    category: Optional[str] = None,
    limit: int = 100,
) -> Optional[List[Dict[str, Any]]]:
    """Queries candidate recipes from Cloud SQL using hybrid vector RAG and ingredient lookups.

    1. If category is provided (e.g. 'dessert', 'soup', 'breakfast') or ingredients are given,
       uses dense vector similarity (search_recipes_by_vector) to semantically retrieve recipes
       matching the category and ingredients from the embedded Cloud SQL recipes.
    2. Additionally queries recipes where ingredients_raw matches any of the primary ingredients.
    3. Merges candidate sets and deduplicates by recipe ID.
    """
    candidates_by_id: Dict[str, Dict[str, Any]] = {}

    # 1. Semantic Vector RAG Retrieval
    query_terms = []
    if category:
        query_terms.append(category)
    if ingredients:
        query_terms.extend(ingredients[:4])
    vibe_query = " ".join(query_terms).strip()

    if vibe_query:
        try:
            vector_results = search_recipes_by_vector(vibe_query, limit=min(limit, 50))
            if vector_results:
                for r in vector_results:
                    candidates_by_id[r["id"]] = r
        except Exception as vec_err:
            logger.debug("Vector candidate retrieval in fetch_pantry_candidates_from_db failed: %s", vec_err)

    # 2. SQL Ingredient Lookup to supplement vector candidates
    conn = get_db_connection()
    if conn is None:
        return list(candidates_by_id.values()) if candidates_by_id else None

    try:
        clean_ings = [re.sub(r"[^\w\s]", "", i).strip() for i in ingredients if i.strip()]
        clean_ings = [i for i in clean_ings if i and len(i) > 2][:5]

        if not clean_ings and not category:
            conn.close()
            return list(candidates_by_id.values()) if candidates_by_id else None

        # Build SQL conditions
        sql_conds = []
        params: Dict[str, Any] = {"limit": limit}

        if category:
            sql_conds.append("(title ILIKE :cat OR :cat_raw = ANY(tags) OR ingredients_raw::text ILIKE :cat)")
            params["cat"] = f"%{category.strip()}%"
            params["cat_raw"] = category.strip().lower()

        if clean_ings:
            ing_clauses = []
            for idx, ing in enumerate(clean_ings):
                key = f"ing_{idx}"
                ing_clauses.append(f"ingredients_raw::text ILIKE :{key}")
                params[key] = f"%{ing}%"
            sql_conds.append(f"({' OR '.join(ing_clauses)})")

        where_clause = " AND ".join(sql_conds) if sql_conds else "TRUE"
        sql_str = f"""
            SELECT id, title, servings, ingredients_raw, instructions_raw, tags
            FROM recipes
            WHERE {where_clause}
            LIMIT :limit
        """

        if hasattr(conn, "run"):
            rows = conn.run(sql_str, **params)
            for row in rows:
                r_id = row[0]
                if r_id not in candidates_by_id:
                    candidates_by_id[r_id] = {
                        "id": r_id,
                        "title": row[1],
                        "servings": row[2],
                        "ingredients": row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]"),
                        "instructions": row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]"),
                        "tags": row[5] or [],
                    }
            conn.close()

        elif hasattr(conn, "cursor"):
            cur = conn.cursor()
            # Convert named params to %s
            param_order = []
            formatted_sql = sql_str
            for k in params:
                pattern = f":{k}"
                if pattern in formatted_sql:
                    param_order.append(params[k])
                    formatted_sql = formatted_sql.replace(pattern, "%s")
            cur.execute(formatted_sql, tuple(param_order))
            rows = cur.fetchall()
            for row in rows:
                r_id = row[0]
                if r_id not in candidates_by_id:
                    candidates_by_id[r_id] = {
                        "id": r_id,
                        "title": row[1],
                        "servings": row[2],
                        "ingredients": row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]"),
                        "instructions": row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]"),
                        "tags": row[5] or [],
                    }
            cur.close()
            conn.close()

    except Exception as exc:
        logger.warning("Error fetching pantry candidates from Cloud SQL: %s", exc)
        try:
            conn.close()
        except Exception:
            pass

    return list(candidates_by_id.values()) if candidates_by_id else None

