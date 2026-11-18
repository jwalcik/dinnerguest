"""Data ingestion script for Kaggle datasets.

Processes raw Kaggle recipe and allergen CSV/JSON files, normalizes them into
the Dinnerguest canonical schema, and streams them into Google Cloud SQL PostgreSQL
(with pgvector dense embeddings for RAG) or local SQLite for testing.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

# Ensure dinnerguest source package is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# Accommodate large text fields in Kaggle recipe CSVs without memory crashing
try:
    csv.field_size_limit(min(sys.maxsize, 2147483647))
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest_kaggle_data")


def parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    """Parses a gs://bucket-name/prefix URI into (bucket_name, prefix)."""
    cleaned = gcs_uri.strip()
    if not cleaned.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {gcs_uri}. Must begin with 'gs://'")
    path_part = cleaned[5:]
    parts = path_part.split("/", 1)
    bucket_name = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    return bucket_name, prefix


def discover_gcs_csv_blobs(gcs_uri: str) -> List[Any]:
    """Discovers all CSV blobs under the specified GCS URI prefix."""
    from google.cloud import storage

    bucket_name, prefix = parse_gcs_uri(gcs_uri)
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blobs = [b for b in bucket.list_blobs(prefix=prefix) if b.name.lower().endswith(".csv")]
    return blobs


def normalize_recipe_row(
    recipe_id: str,
    title: str,
    ingredients_raw: List[str] | str,
    instructions_raw: List[str] | str,
    servings: int = 4,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Normalizes recipe data into Dinnerguest standard schema."""
    if isinstance(ingredients_raw, str):
        try:
            ingredients = json.loads(ingredients_raw)
        except Exception:
            ingredients = [i.strip() for i in ingredients_raw.splitlines() if i.strip()]
    else:
        ingredients = ingredients_raw

    if isinstance(instructions_raw, str):
        try:
            instructions = json.loads(instructions_raw)
        except Exception:
            instructions = [i.strip() for i in instructions_raw.splitlines() if i.strip()]
    else:
        instructions = instructions_raw

    return {
        "id": str(recipe_id),
        "title": title.strip(),
        "servings": int(servings) if servings else 4,
        "ingredients": ingredients,
        "instructions": instructions,
        "tags": tags or [],
    }


def is_recipe_csv(fieldnames: Optional[List[str]]) -> bool:
    """Validates that a CSV actually contains recipe data rather than allergen taxonomies or user interactions."""
    if not fieldnames:
        return False
    lower_fields = {f.lower().strip() for f in fieldnames if f}
    has_ingredients = any(
        k in lower_fields
        for k in ["ingredients", "recipe_ingredients", "ingredients_raw", "ingredient_list"]
    )
    has_instructions = any(
        k in lower_fields
        for k in ["directions", "instructions", "steps", "recipe_instructions", "description"]
    )
    return has_ingredients and has_instructions


def stream_recipes_from_reader(reader: csv.DictReader, limit: Optional[int] = None) -> Generator[Dict[str, Any], None, None]:
    """Streams and normalizes recipe rows from an initialized csv.DictReader."""
    if not is_recipe_csv(reader.fieldnames):
        logger.warning(
            "Skipping non-recipe CSV (headers: %s). Only files containing both ingredients and instructions are ingested into recipes table.",
            reader.fieldnames,
        )
        return

    count = 0
    for row in reader:
        title = row.get("title") or row.get("name") or row.get("recipe_name") or "Untitled Recipe"
        rec_id = row.get("id") or f"kaggle_{count + 1}"
        raw_ing = row.get("ingredients") or "[]"
        raw_inst = row.get("directions") or row.get("instructions") or row.get("steps") or "[]"
        servings = row.get("servings") or 4

        tags = []
        if "tags" in row:
            try:
                tags = json.loads(row["tags"])
            except Exception:
                tags = [t.strip() for t in row["tags"].split(",") if t.strip()]

        yield normalize_recipe_row(
            recipe_id=rec_id,
            title=title,
            ingredients_raw=raw_ing,
            instructions_raw=raw_inst,
            servings=servings,
            tags=tags,
        )
        count += 1
        if limit and count >= limit:
            break


def stream_recipes_from_csv(file_path: Path, limit: Optional[int] = None) -> Generator[Dict[str, Any], None, None]:
    """Streams and normalizes recipe rows from a local CSV file."""
    if not file_path.exists():
        logger.warning("File not found: %s", file_path)
        return

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        yield from stream_recipes_from_reader(reader, limit=limit)


def stream_recipes_from_gcs_blob(blob: Any, limit: Optional[int] = None) -> Generator[Dict[str, Any], None, None]:
    """Streams and normalizes recipe rows directly from a Google Cloud Storage blob.

    Zero local disk footprint: uses blob.open() to stream HTTP chunks.
    """
    with blob.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        yield from stream_recipes_from_reader(reader, limit=limit)


class RecipeSource:
    """Wrapper around a local Path or GCS Blob for unified streaming."""

    def __init__(self, item: Any, is_gcs: bool):
        self.item = item
        self.is_gcs = is_gcs

    @property
    def display_name(self) -> str:
        if self.is_gcs:
            return f"gs://{self.item.bucket.name}/{self.item.name}"
        return self.item.name

    def stream(self, limit: Optional[int] = None) -> Generator[Dict[str, Any], None, None]:
        if self.is_gcs:
            yield from stream_recipes_from_gcs_blob(self.item, limit=limit)
        else:
            yield from stream_recipes_from_csv(self.item, limit=limit)


def init_sqlite_db(db_path: Path) -> sqlite3.Connection:
    """Initializes local SQLite database for offline testing."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            servings INTEGER NOT NULL,
            ingredients_json TEXT NOT NULL,
            instructions_json TEXT NOT NULL,
            tags_json TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_recipes_title ON recipes(title)")
    conn.commit()
    return conn


def init_postgres_db(conn: Any) -> None:
    """Initializes Cloud SQL PostgreSQL schema with pgvector extension and indexes."""
    if hasattr(conn, "run"):
        # pg8000 connection
        try:
            conn.run("CREATE EXTENSION IF NOT EXISTS vector;")
        except Exception as exc:
            logger.warning("Could not create vector extension (may already exist or lack superuser): %s", exc)

        conn.run(
            """
            CREATE TABLE IF NOT EXISTS recipes (
                id VARCHAR(64) PRIMARY KEY,
                title TEXT NOT NULL,
                servings INTEGER NOT NULL DEFAULT 4,
                ingredients_raw JSONB NOT NULL DEFAULT '[]'::jsonb,
                instructions_raw JSONB NOT NULL DEFAULT '[]'::jsonb,
                tags TEXT[] NOT NULL DEFAULT '{}',
                embedding vector(768),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        try:
            conn.run("ALTER TABLE recipes ADD COLUMN IF NOT EXISTS embedding vector(768);")
        except Exception:
            pass

        conn.run("CREATE INDEX IF NOT EXISTS idx_recipes_title ON recipes(title);")
        try:
            conn.run("CREATE INDEX IF NOT EXISTS idx_recipes_tags ON recipes USING GIN(tags);")
        except Exception:
            pass
    elif hasattr(conn, "cursor"):
        # psycopg2 connection
        cur = conn.cursor()
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        except Exception as exc:
            logger.warning("Could not create vector extension: %s", exc)
            conn.rollback()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS recipes (
                id VARCHAR(64) PRIMARY KEY,
                title TEXT NOT NULL,
                servings INTEGER NOT NULL DEFAULT 4,
                ingredients_raw JSONB NOT NULL DEFAULT '[]'::jsonb,
                instructions_raw JSONB NOT NULL DEFAULT '[]'::jsonb,
                tags TEXT[] NOT NULL DEFAULT '{}',
                embedding vector(768),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        try:
            cur.execute("ALTER TABLE recipes ADD COLUMN IF NOT EXISTS embedding vector(768);")
        except Exception:
            conn.rollback()

        cur.execute("CREATE INDEX IF NOT EXISTS idx_recipes_title ON recipes(title);")
        try:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_recipes_tags ON recipes USING GIN(tags);")
        except Exception:
            conn.rollback()

        conn.commit()
        cur.close()


def check_existing_postgres(conn: Any, ids: List[str]) -> Dict[str, bool]:
    """Returns a dict mapping recipe_id to whether it already has an embedding in PostgreSQL."""
    if not ids:
        return {}
    res: Dict[str, bool] = {}
    if hasattr(conn, "run"):
        # pg8000
        try:
            rows = conn.run(
                "SELECT id, (embedding IS NOT NULL) FROM recipes WHERE id = ANY(:ids)",
                ids=ids,
            )
            if isinstance(rows, (list, tuple)):
                for row in rows:
                    if len(row) >= 2:
                        res[str(row[0])] = bool(row[1])
        except Exception as exc:
            logger.debug("check_existing_postgres error: %s", exc)
    elif hasattr(conn, "cursor"):
        # psycopg2
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, (embedding IS NOT NULL) FROM recipes WHERE id = ANY(%s)", (ids,))
            rows = cur.fetchall()
            if isinstance(rows, (list, tuple)):
                for row in rows:
                    if len(row) >= 2:
                        res[str(row[0])] = bool(row[1])
            cur.close()
        except Exception as exc:
            logger.debug("check_existing_postgres error: %s", exc)
    return res


def check_existing_sqlite(conn: sqlite3.Connection, ids: List[str]) -> set[str]:
    """Returns a set of recipe_ids that already exist in SQLite."""
    if not ids:
        return set()
    placeholders = ",".join("?" * len(ids))
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT id FROM recipes WHERE id IN ({placeholders})", ids)
        rows = cur.fetchall()
        return {row[0] for row in rows}
    except Exception as exc:
        logger.debug("check_existing_sqlite error: %s", exc)
        return set()


def insert_batch_postgres(
    conn: Any,
    batch: List[Dict[str, Any]],
    generate_embeddings: bool = False,
    embedding_func: Optional[Any] = None,
    skip_existing: bool = False,
) -> int:
    """Inserts a batch of recipes into Cloud SQL PostgreSQL.

    If skip_existing is True, recipes already in the database are ignored.
    If a recipe already exists with an embedding, re-embedding is skipped to conserve quota.
    """
    if not batch:
        return 0

    batch_ids = [r["id"] for r in batch]
    existing_map = check_existing_postgres(conn, batch_ids)

    if skip_existing and existing_map:
        batch = [r for r in batch if r["id"] not in existing_map]
        if not batch:
            return 0

    inserted_count = 0

    if hasattr(conn, "run"):
        # pg8000 native
        conn.run("BEGIN")
        try:
            for r in batch:
                vec_str = None
                has_existing_embedding = existing_map.get(r["id"], False)
                if generate_embeddings and embedding_func and not has_existing_embedding:
                    vibe_text = f"{r['title']}. Tags: {', '.join(r.get('tags', []))}. Ingredients: {', '.join(r.get('ingredients', []))}."
                    vec = embedding_func(vibe_text)
                    if vec:
                        vec_str = "[" + ",".join(str(round(v, 6)) for v in vec) + "]"

                if vec_str:
                    conn.run(
                        """
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
                    conn.run(
                        """
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
                inserted_count += 1
            conn.run("COMMIT")
        except Exception as exc:
            conn.run("ROLLBACK")
            logger.error("Error inserting batch into PostgreSQL: %s", exc)
            raise

    elif hasattr(conn, "cursor"):
        # psycopg2
        cur = conn.cursor()
        try:
            for r in batch:
                vec_str = None
                has_existing_embedding = existing_map.get(r["id"], False)
                if generate_embeddings and embedding_func and not has_existing_embedding:
                    vibe_text = f"{r['title']}. Tags: {', '.join(r.get('tags', []))}. Ingredients: {', '.join(r.get('ingredients', []))}."
                    vec = embedding_func(vibe_text)
                    if vec:
                        vec_str = "[" + ",".join(str(round(v, 6)) for v in vec) + "]"

                if vec_str:
                    cur.execute(
                        """
                        INSERT INTO recipes (id, title, servings, ingredients_raw, instructions_raw, tags, embedding)
                        VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::vector)
                        ON CONFLICT (id) DO UPDATE SET
                            title = EXCLUDED.title,
                            servings = EXCLUDED.servings,
                            ingredients_raw = EXCLUDED.ingredients_raw,
                            instructions_raw = EXCLUDED.instructions_raw,
                            tags = EXCLUDED.tags,
                            embedding = EXCLUDED.embedding
                        """,
                        (
                            r["id"],
                            r["title"],
                            r.get("servings", 4),
                            json.dumps(r.get("ingredients", [])),
                            json.dumps(r.get("instructions", [])),
                            r.get("tags", []),
                            vec_str,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO recipes (id, title, servings, ingredients_raw, instructions_raw, tags)
                        VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            title = EXCLUDED.title,
                            servings = EXCLUDED.servings,
                            ingredients_raw = EXCLUDED.ingredients_raw,
                            instructions_raw = EXCLUDED.instructions_raw,
                            tags = EXCLUDED.tags
                        """,
                        (
                            r["id"],
                            r["title"],
                            r.get("servings", 4),
                            json.dumps(r.get("ingredients", [])),
                            json.dumps(r.get("instructions", [])),
                            r.get("tags", []),
                        ),
                    )
                inserted_count += 1
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.error("Error inserting batch into PostgreSQL: %s", exc)
            raise
        finally:
            cur.close()

    return inserted_count


def connect_postgres(args: argparse.Namespace) -> Any:
    """Establishes connection to Cloud SQL PostgreSQL database."""
    # Set explicit environment variables if CLI args provided
    if args.connection_name:
        os.environ["CLOUD_SQL_CONNECTION_NAME"] = args.connection_name
    if args.host:
        os.environ["DB_HOST"] = args.host
    if args.port:
        os.environ["DB_PORT"] = str(args.port)
    if args.db:
        os.environ["CLOUD_SQL_DB"] = args.db
    if args.user:
        os.environ["CLOUD_SQL_USER"] = args.user
    if args.password:
        os.environ["CLOUD_SQL_PASSWORD"] = args.password

    try:
        from dinnerguest.database import get_db_connection
        conn = get_db_connection()
        if conn is not None:
            return conn
    except Exception as exc:
        logger.debug("dinnerguest.database get_db_connection failed: %s", exc)

    # Standalone connection attempt
    db_name = os.getenv("CLOUD_SQL_DB", "dinnerguest")
    db_user = os.getenv("CLOUD_SQL_USER", "dinnerguest_app")
    db_password = os.getenv("CLOUD_SQL_PASSWORD", "DinnerGuest2026!Secure")
    conn_name = os.getenv("CLOUD_SQL_CONNECTION_NAME")
    db_host = os.getenv("DB_HOST")
    db_port = int(os.getenv("DB_PORT", "5432"))

    try:
        import pg8000.native
        if conn_name:
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

    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Kaggle recipe datasets into Cloud SQL PostgreSQL or local SQLite for Dinnerguest."
    )
    parser.add_argument(
        "--target",
        choices=["postgres", "sqlite"],
        default="postgres",
        help="Target database: 'postgres' for Cloud SQL (default) or 'sqlite' for local offline testing.",
    )
    parser.add_argument(
        "--raw-dir",
        type=str,
        default=str(Path.home() / "dinnerguest_raw_data"),
        help="Local directory path OR Google Cloud Storage URI (e.g. gs://bucket-name/raw_kaggle/) where CSV files are stored.",
    )
    parser.add_argument(
        "--raw-gcs",
        type=str,
        default=None,
        help="Google Cloud Storage URI (e.g. gs://bucket-name/raw_kaggle/) to stream CSVs directly without local disk usage.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum total recipes to ingest across all CSV files.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Number of recipes to skip from the beginning of the input stream before ingesting.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip recipes that already exist in the database (avoids re-processing and re-generating embeddings).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Batch size for database insertions (default: 500).",
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        help="Generate Vertex AI text-embedding-004 dense vector embeddings for RAG during ingestion.",
    )
    parser.add_argument(
        "--embed-limit",
        type=int,
        default=1000,
        help="Maximum number of recipes to generate vector embeddings for (default: 1000). Set 0 for all.",
    )
    parser.add_argument(
        "--output-sqlite",
        type=Path,
        default=Path("data/processed/recipes.sqlite"),
        help="Target SQLite DB path when using --target sqlite.",
    )
    parser.add_argument(
        "--output-sample-json",
        type=Path,
        default=None,
        help="Export a small sample JSON for dev testing.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=50,
        help="Number of sample recipes to export to JSON.",
    )
    parser.add_argument(
        "--gcs-bucket",
        type=str,
        default=None,
        help="Google Cloud Storage bucket name to stage data.",
    )

    # Cloud SQL Connection overrides
    parser.add_argument("--connection-name", type=str, default=None, help="Cloud SQL Instance Connection Name")
    parser.add_argument("--host", type=str, default=None, help="Cloud SQL host / IP / Auth Proxy host")
    parser.add_argument("--port", type=int, default=None, help="Cloud SQL port (default 5432)")
    parser.add_argument("--db", type=str, default=None, help="Cloud SQL database name")
    parser.add_argument("--user", type=str, default=None, help="Cloud SQL database user")
    parser.add_argument("--password", type=str, default=None, help="Cloud SQL database password")

    args = parser.parse_args()

    # Discover CSV files (either in Google Cloud Storage or local filesystem)
    raw_dir_str = str(args.raw_dir).strip()
    is_gcs = args.raw_gcs is not None or raw_dir_str.startswith("gs://")
    gcs_uri = args.raw_gcs or (raw_dir_str if raw_dir_str.startswith("gs://") else None)

    sources: List[RecipeSource] = []
    if is_gcs:
        logger.info("Scanning Google Cloud Storage for recipe CSVs at %s...", gcs_uri)
        try:
            blobs = discover_gcs_csv_blobs(gcs_uri)
            if not blobs:
                logger.error("No CSV blobs found under GCS URI '%s'.", gcs_uri)
                sys.exit(1)
            logger.info("Found %d CSV blob(s) in GCS: %s", len(blobs), [b.name for b in blobs])
            sources = [RecipeSource(b, is_gcs=True) for b in blobs]
        except Exception as exc:
            logger.error("Failed to list GCS blobs from '%s': %s", gcs_uri, exc)
            sys.exit(1)
    else:
        raw_path = Path(raw_dir_str).expanduser()
        csv_candidates = sorted(list(raw_path.glob("**/*.csv")))
        if not csv_candidates:
            logger.error(
                "No CSV files found in '%s'. Please run scripts/download_kaggle_data.sh or specify --raw-dir gs://...",
                raw_path,
            )
            sys.exit(1)
        logger.info("Found %d CSV file(s) in %s.", len(csv_candidates), raw_path)
        sources = [RecipeSource(p, is_gcs=False) for p in csv_candidates]

    # Embedding function setup if requested
    embedding_func = None
    if args.embed:
        try:
            from dinnerguest.database import generate_embedding
            embedding_func = generate_embedding
            logger.info("Vertex AI vector embedding generation enabled (text-embedding-004).")
        except Exception as exc:
            logger.warning("Could not load embedding generator: %s. Continuing without embeddings.", exc)

    sample_recipes: List[Dict[str, Any]] = []
    total_inserted = 0
    total_embedded = 0

    if args.target == "postgres":
        logger.info("Connecting to Cloud SQL PostgreSQL database...")
        pg_conn = connect_postgres(args)
        if pg_conn is None:
            logger.error(
                "Failed to connect to Cloud SQL PostgreSQL.\n"
                "Ensure either pg8000 or psycopg2 is installed (pip install pg8000) and Cloud SQL environment variables are set:\n"
                "  CLOUD_SQL_CONNECTION_NAME, CLOUD_SQL_DB, CLOUD_SQL_USER, CLOUD_SQL_PASSWORD (or DB_HOST)"
            )
            sys.exit(1)

        logger.info("Initializing PostgreSQL schema and pgvector extension...")
        init_postgres_db(pg_conn)

        batch: List[Dict[str, Any]] = []
        skipped_offset = 0
        for src in sources:
            logger.info("Streaming recipes from %s...", src.display_name)
            for recipe in src.stream(limit=None):
                if skipped_offset < args.offset:
                    skipped_offset += 1
                    continue

                batch.append(recipe)
                if len(sample_recipes) < args.sample_size:
                    sample_recipes.append(recipe)

                if len(batch) >= args.batch_size:
                    should_embed = args.embed and (args.embed_limit == 0 or total_embedded < args.embed_limit)
                    count = insert_batch_postgres(
                        pg_conn,
                        batch,
                        generate_embeddings=should_embed,
                        embedding_func=embedding_func,
                        skip_existing=args.skip_existing,
                    )
                    total_inserted += count
                    if should_embed:
                        total_embedded += count
                    logger.info("  Processed batch: %d inserted/updated in Cloud SQL (Total: %d)...", count, total_inserted)
                    batch = []

                if args.limit and total_inserted >= args.limit:
                    break
            if args.limit and total_inserted >= args.limit:
                break

        # Flush remaining batch
        if batch:
            should_embed = args.embed and (args.embed_limit == 0 or total_embedded < args.embed_limit)
            count = insert_batch_postgres(
                pg_conn,
                batch,
                generate_embeddings=should_embed,
                embedding_func=embedding_func,
                skip_existing=args.skip_existing,
            )
            total_inserted += count
            if should_embed:
                total_embedded += count

        try:
            if hasattr(pg_conn, "close"):
                pg_conn.close()
        except Exception:
            pass

        logger.info("Successfully ingested %d recipes into Cloud SQL PostgreSQL!", total_inserted)
        if total_embedded > 0:
            logger.info("Generated dense vector embeddings for %d recipes.", total_embedded)

    elif args.target == "sqlite":
        logger.info("Ingesting into SQLite database at %s...", args.output_sqlite)
        args.output_sqlite.parent.mkdir(parents=True, exist_ok=True)
        sqlite_conn = init_sqlite_db(args.output_sqlite)
        cur = sqlite_conn.cursor()

        skipped_offset = 0
        for src in sources:
            logger.info("Streaming recipes from %s...", src.display_name)
            for recipe in src.stream(limit=None):
                if skipped_offset < args.offset:
                    skipped_offset += 1
                    continue

                if args.skip_existing:
                    existing_set = check_existing_sqlite(sqlite_conn, [recipe["id"]])
                    if recipe["id"] in existing_set:
                        continue

                cur.execute(
                    """
                    INSERT OR REPLACE INTO recipes (id, title, servings, ingredients_json, instructions_json, tags_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        recipe["id"],
                        recipe["title"],
                        recipe["servings"],
                        json.dumps(recipe["ingredients"]),
                        json.dumps(recipe["instructions"]),
                        json.dumps(recipe["tags"]),
                    ),
                )
                total_inserted += 1
                if len(sample_recipes) < args.sample_size:
                    sample_recipes.append(recipe)

                if total_inserted % args.batch_size == 0:
                    sqlite_conn.commit()
                    logger.info("  Inserted %d recipes into SQLite...", total_inserted)

                if args.limit and total_inserted >= args.limit:
                    break
            if args.limit and total_inserted >= args.limit:
                break

        sqlite_conn.commit()
        sqlite_conn.close()
        logger.info("Successfully indexed %d recipes into %s", total_inserted, args.output_sqlite)

    # Optional export of sample JSON
    if args.output_sample_json and sample_recipes:
        args.output_sample_json.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_sample_json, "w", encoding="utf-8") as f:
            json.dump(sample_recipes, f, indent=2)
        logger.info("Saved %d sample recipes to %s", len(sample_recipes), args.output_sample_json)

    # Optional GCS stage
    if args.gcs_bucket:
        if args.target == "sqlite" and args.output_sqlite.exists():
            logger.info("Uploading %s to gs://%s/...", args.output_sqlite, args.gcs_bucket)
            os.system(f"gcloud storage cp '{args.output_sqlite}' 'gs://{args.gcs_bucket}/recipes.sqlite'")
        elif args.output_sample_json and args.output_sample_json.exists():
            os.system(f"gcloud storage cp '{args.output_sample_json}' 'gs://{args.gcs_bucket}/recipes_sample.json'")


if __name__ == "__main__":
    main()
