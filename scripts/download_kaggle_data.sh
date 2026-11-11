#!/usr/bin/env bash
#
# Download script for Cloud Shell environment
# Fetches the required Kaggle recipe and allergen datasets.
#
set -euo pipefail

TARGET_DIR="${1:-$HOME/dinnerguest_raw_data}"
mkdir -p "$TARGET_DIR"
cd "$TARGET_DIR"

echo "=== Dinnerguest: Downloading Kaggle Datasets into $TARGET_DIR ==="

if ! command -v kaggle &> /dev/null; then
    echo "Error: kaggle CLI is not installed."
    echo "Install via: pip install kaggle"
    echo "Ensure your Kaggle API key is placed at ~/.kaggle/kaggle.json with chmod 600"
    exit 1
fi

echo "[1/4] Downloading 2M+ Recipe Dataset..."
kaggle datasets download -d wilmerarltstrmberg/recipe-dataset-over-2m --unzip -p "$TARGET_DIR/recipe_2m"

echo "[2/4] Downloading Food.com Recipes & Interactions..."
kaggle datasets download -d shuyangli94/food-com-recipes-and-user-interactions --unzip -p "$TARGET_DIR/food_com"

echo "[3/4] Downloading Food Ingredients and Allergens..."
kaggle datasets download -d uom190346a/food-ingredients-and-allergens --unzip -p "$TARGET_DIR/ingredients_allergens"

echo "[4/4] Downloading Food Allergens and Allergies..."
kaggle datasets download -d boltcutters/food-allergens-and-allergies --unzip -p "$TARGET_DIR/allergens_allergies"

echo "=== All Kaggle datasets downloaded successfully into $TARGET_DIR ==="
