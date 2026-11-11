"""Data ingestion script for Kaggle datasets.

Designed to run in Cloud Shell or a Compute Engine / Batch VM to process raw
Kaggle recipe and allergen CSV/JSON files, normalize them into a canonical schema,
and export to SQLite, Parquet, or Google Cloud Storage.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional


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


def init_sqlite_db(db_path: Path) -> sqlite3.Connection:
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


def stream_recipes_from_csv(file_path: Path, limit: Optional[int] = None) -> Generator[Dict[str, Any], None, None]:
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        return

    count = 0
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = row.get("title") or row.get("name") or row.get("recipe_name") or "Untitled Recipe"
            rec_id = row.get("id") or str(count + 1)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Kaggle recipe datasets into Dinnerguest")
    parser.add_argument("--raw-dir", type=Path, default=Path.home() / "dinnerguest_raw_data", help="Raw data directory")
    parser.add_argument("--output-sqlite", type=Path, default=Path("data/processed/recipes.sqlite"), help="Target SQLite DB")
    parser.add_argument("--output-sample-json", type=Path, default=None, help="Export a small sample JSON for dev testing")
    parser.add_argument("--sample-size", type=int, default=50, help="Number of sample recipes to export")
    parser.add_argument("--limit", type=int, default=None, help="Max recipes to ingest")
    parser.add_argument("--gcs-bucket", type=str, default=None, help="Google Cloud Storage bucket name to stage data")

    args = parser.parse_args()

    args.output_sqlite.parent.mkdir(parents=True, exist_ok=True)
    conn = init_sqlite_db(args.output_sqlite)
    cur = conn.cursor()

    sample_recipes: List[Dict[str, Any]] = []
    total_inserted = 0

    csv_candidates = list(args.raw_dir.glob("**/*.csv"))
    if not csv_candidates:
        print(f"No CSV files found in {args.raw_dir}. Please run scripts/download_kaggle_data.sh first.")
        return

    print(f"Found {len(csv_candidates)} CSV files. Ingesting...")
    for csv_file in csv_candidates:
        print(f"Processing {csv_file.name}...")
        for recipe in stream_recipes_from_csv(csv_file, limit=args.limit):
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

            if total_inserted % 10000 == 0:
                conn.commit()
                print(f"  Inserted {total_inserted} recipes...")

    conn.commit()
    conn.close()
    print(f"Successfully indexed {total_inserted} recipes into {args.output_sqlite}")

    if args.output_sample_json and sample_recipes:
        args.output_sample_json.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_sample_json, "w", encoding="utf-8") as f:
            json.dump(sample_recipes, f, indent=2)
        print(f"Saved {len(sample_recipes)} sample recipes to {args.output_sample_json}")

    if args.gcs_bucket:
        print(f"Uploading {args.output_sqlite} to gs://{args.gcs_bucket}/...")
        os.system(f"gcloud storage cp '{args.output_sqlite}' 'gs://{args.gcs_bucket}/recipes.sqlite'")
        print("GCS upload complete.")


if __name__ == "__main__":
    main()
