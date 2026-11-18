"""Unit tests for Kaggle dataset ingestion pipeline."""

import csv
import json
import sqlite3
import tempfile
import unittest
import io
from pathlib import Path
from unittest.mock import MagicMock

from scripts.ingest_kaggle_data import (
    RecipeSource,
    discover_gcs_csv_blobs,
    init_postgres_db,
    init_sqlite_db,
    insert_batch_postgres,
    normalize_recipe_row,
    parse_gcs_uri,
    stream_recipes_from_csv,
    stream_recipes_from_gcs_blob,
)


class TestIngestKaggleData(unittest.TestCase):
    def test_normalize_recipe_row(self):
        row = normalize_recipe_row(
            recipe_id="123",
            title="Spicy Salmon Roll",
            ingredients_raw='["salmon", "sushi rice", "sriracha"]',
            instructions_raw='["roll rice", "slice roll"]',
            servings=2,
            tags=["spicy", "japanese"],
        )
        self.assertEqual(row["id"], "123")
        self.assertEqual(row["title"], "Spicy Salmon Roll")
        self.assertEqual(row["servings"], 2)
        self.assertEqual(row["ingredients"], ["salmon", "sushi rice", "sriracha"])
        self.assertEqual(row["instructions"], ["roll rice", "slice roll"])
        self.assertEqual(row["tags"], ["spicy", "japanese"])

    def test_normalize_recipe_row_newline_fallback(self):
        row = normalize_recipe_row(
            recipe_id="456",
            title="Pancake",
            ingredients_raw="1 cup flour\n1 cup milk\n1 egg",
            instructions_raw="Mix ingredients\nCook on griddle",
            servings=4,
        )
        self.assertEqual(len(row["ingredients"]), 3)
        self.assertEqual(row["ingredients"][0], "1 cup flour")
        self.assertEqual(len(row["instructions"]), 2)

    def test_stream_recipes_from_csv(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["id", "title", "ingredients", "directions", "servings", "tags"],
            )
            writer.writeheader()
            writer.writerow({
                "id": "rec_01",
                "title": "Avocado Toast",
                "ingredients": '["avocado", "sourdough bread", "salt"]',
                "directions": '["toast bread", "mash avocado", "top with salt"]',
                "servings": "1",
                "tags": '["breakfast", "easy"]',
            })
            csv_path = Path(f.name)

        try:
            recipes = list(stream_recipes_from_csv(csv_path))
            self.assertEqual(len(recipes), 1)
            self.assertEqual(recipes[0]["title"], "Avocado Toast")
            self.assertEqual(recipes[0]["servings"], 1)
            self.assertIn("avocado", recipes[0]["ingredients"])
        finally:
            if csv_path.exists():
                csv_path.unlink()

    def test_init_sqlite_db(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
            db_path = Path(f.name)

        try:
            conn = init_sqlite_db(db_path)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='recipes'")
            self.assertIsNotNone(cur.fetchone())
            conn.close()
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_insert_batch_postgres_pg8000_mock(self):
        mock_conn = MagicMock()
        batch = [
            {
                "id": "rec_pg_01",
                "title": "Seared Scallops",
                "servings": 2,
                "ingredients": ["scallops", "butter", "lemon"],
                "instructions": ["Sear scallops in butter", "Finish with lemon"],
                "tags": ["seafood", "quick"],
            }
        ]
        mock_embedding = MagicMock(return_value=[0.1, 0.2, 0.3])
        count = insert_batch_postgres(
            mock_conn,
            batch,
            generate_embeddings=True,
            embedding_func=mock_embedding,
        )
        self.assertEqual(count, 1)
        mock_conn.run.assert_any_call("BEGIN")
        mock_conn.run.assert_any_call("COMMIT")
        mock_embedding.assert_called_once()

    def test_insert_batch_postgres_psycopg2_mock(self):
        mock_conn = MagicMock(spec=["cursor", "commit", "rollback"])
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        batch = [
            {
                "id": "rec_psy_01",
                "title": "Tomato Basil Bruschetta",
                "servings": 4,
                "ingredients": ["baguette", "tomatoes", "basil", "garlic", "olive oil"],
                "instructions": ["Toast bread", "Mix tomatoes with basil", "Spoon onto toast"],
                "tags": ["appetizer", "italian"],
            }
        ]
        count = insert_batch_postgres(mock_conn, batch, generate_embeddings=False)
        self.assertEqual(count, 1)
        mock_conn.commit.assert_called_once()
        mock_cur.execute.assert_called_once()


    def test_parse_gcs_uri(self):
        bucket, prefix = parse_gcs_uri("gs://my-bucket/recipes/raw/")
        self.assertEqual(bucket, "my-bucket")
        self.assertEqual(prefix, "recipes/raw/")

        bucket2, prefix2 = parse_gcs_uri("gs://simple-bucket")
        self.assertEqual(bucket2, "simple-bucket")
        self.assertEqual(prefix2, "")

    def test_parse_gcs_uri_invalid(self):
        with self.assertRaises(ValueError):
            parse_gcs_uri("https://storage.googleapis.com/bucket")

    def test_stream_recipes_from_gcs_blob_mock(self):
        csv_data = "id,title,ingredients,directions,servings,tags\n" \
                   "gcs_01,GCS Clam Chowder,\"[\"\"clams\"\", \"\"cream\"\"]\",\"[\"\"simmer clams\"\", \"\"stir cream\"\"]\",6,\"[\"\"soup\"\"]\"\n"

        mock_blob = MagicMock()
        mock_blob.open.return_value.__enter__.return_value = io.StringIO(csv_data)

        recipes = list(stream_recipes_from_gcs_blob(mock_blob))
        self.assertEqual(len(recipes), 1)
        self.assertEqual(recipes[0]["id"], "gcs_01")
        self.assertEqual(recipes[0]["title"], "GCS Clam Chowder")
        self.assertEqual(recipes[0]["servings"], 6)
        self.assertIn("clams", recipes[0]["ingredients"])

    def test_recipe_source_display_name_and_stream(self):
        csv_data = "id,title,ingredients,directions,servings\n" \
                   "blob_01,Steamed Artichoke,\"[\"\"artichoke\"\", \"\"butter\"\"]\",\"[\"\"steam artichoke\"\"]\",2\n"

        mock_blob = MagicMock()
        mock_blob.bucket.name = "dinnerguest-bucket"
        mock_blob.name = "raw/artichoke.csv"
        mock_blob.open.return_value.__enter__.return_value = io.StringIO(csv_data)

        src = RecipeSource(mock_blob, is_gcs=True)
        self.assertEqual(src.display_name, "gs://dinnerguest-bucket/raw/artichoke.csv")
        results = list(src.stream())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Steamed Artichoke")


if __name__ == "__main__":
    unittest.main()
