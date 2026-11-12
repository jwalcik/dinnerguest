-- Dinnerguest Cloud SQL PostgreSQL + pgvector Initialization Schema
-- Supports dense vector RAG (text-embedding-005 / 768 dimensions),
-- GIN indexing for inverted ingredient / tag search, and menu persistence.

-- 1. Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 2. Recipe Catalog with Vector Embeddings
CREATE TABLE IF NOT EXISTS recipes (
    id VARCHAR(64) PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    servings INTEGER NOT NULL DEFAULT 4,
    ingredients_raw JSONB NOT NULL DEFAULT '[]'::jsonb,
    ingredients_normalized TEXT[] NOT NULL DEFAULT '{}',
    instructions_raw JSONB NOT NULL DEFAULT '[]'::jsonb,
    tags TEXT[] NOT NULL DEFAULT '{}',
    embedding vector(768),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for Fast Retrieval
CREATE INDEX IF NOT EXISTS idx_recipes_title ON recipes(title);
CREATE INDEX IF NOT EXISTS idx_recipes_tags ON recipes USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_recipes_ingredients ON recipes USING GIN(ingredients_normalized);

-- HNSW Cosine Distance Index for High-Performance Vector Similarity Search
CREATE INDEX IF NOT EXISTS idx_recipes_embedding 
ON recipes USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- 3. Optional SQL Backup for Event Menus (when not using Firestore)
CREATE TABLE IF NOT EXISTS saved_event_menus (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    guest_count INTEGER NOT NULL DEFAULT 4,
    dishes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_saved_menus_name ON saved_event_menus(name);
