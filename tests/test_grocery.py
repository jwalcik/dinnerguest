"""Tests for the Grocery List Aggregator Tool."""

import unittest
from dinnerguest.tools.grocery_tool import GroceryListAggregatorTool


class TestGroceryListAggregator(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tool = GroceryListAggregatorTool()

    # ── Test 1: Single recipe produces correct items ──

    def test_single_recipe_grocery_list(self):
        """A single recipe should produce a valid grocery list with all ingredients."""
        recipes = [
            {
                "title": "Classic Italian Meatballs",
                "ingredients": [
                    "1 lb ground beef",
                    "0.5 cup breadcrumbs",
                    "1 large egg",
                    "0.25 cup grated parmesan cheese",
                    "2 cloves garlic, minced",
                    "0.25 cup chopped fresh parsley",
                    "1 tsp salt",
                    "0.5 tsp black pepper",
                    "2 cups marinara sauce",
                ],
            }
        ]
        result = self.tool.generate(recipes)
        self.assertEqual(result.recipe_count, 1)
        self.assertGreaterEqual(len(result.items), 5)

        # Every item must have an aisle
        for item in result.items:
            self.assertTrue(item.aisle, f"Item '{item.canonical_name}' missing aisle")

        # Check dict serialization
        d = result.to_dict()
        self.assertIn("by_aisle", d)
        self.assertIn("total_items", d)

    # ── Test 2: Cross-recipe quantity aggregation ──

    def test_cross_recipe_aggregation_sugar(self):
        """Identical ingredients from two recipes should combine quantities."""
        recipes = [
            {
                "title": "Cookies",
                "ingredients": ["1 cup granulated sugar"],
            },
            {
                "title": "Apple Crisp",
                "ingredients": ["0.5 cup granulated sugar"],
            },
        ]
        result = self.tool.generate(recipes)
        self.assertEqual(result.recipe_count, 2)

        sugar_items = [i for i in result.items if "sugar" in i.canonical_name.lower()]
        self.assertEqual(len(sugar_items), 1, "Sugar should be merged into one line")

        sugar = sugar_items[0]
        # 1 cup + 0.5 cup = 1.5 cups (48 + 24 = 72 tsp → 1.5 cups)
        self.assertAlmostEqual(sugar.quantity, 1.5, places=1)
        self.assertIn("cups", sugar.unit)
        self.assertEqual(len(sugar.source_recipes), 2)

    # ── Test 3: Cross-recipe aggregation of weight units ──

    def test_cross_recipe_aggregation_weight(self):
        """Weight-based ingredients from multiple recipes should sum correctly."""
        recipes = [
            {
                "title": "Meatballs",
                "ingredients": ["1 lb ground beef"],
            },
            {
                "title": "Tacos",
                "ingredients": ["1 lb ground beef"],
            },
            {
                "title": "Bolognese",
                "ingredients": ["1 lb ground beef"],
            },
        ]
        result = self.tool.generate(recipes)
        beef_items = [i for i in result.items if "ground beef" in i.canonical_name.lower()]
        self.assertEqual(len(beef_items), 1, "Ground beef should be merged")

        beef = beef_items[0]
        # 1 + 1 + 1 = 3 lbs
        self.assertAlmostEqual(beef.quantity, 3.0, places=1)
        self.assertEqual(len(beef.source_recipes), 3)

    # ── Test 4: Scale factor applied ──

    def test_scale_factor_applied(self):
        """A scale_factor multiplier should increase quantities proportionally."""
        recipes = [
            {
                "title": "Meatballs (doubled)",
                "ingredients": ["1 lb ground beef", "0.5 cup breadcrumbs"],
                "scale_factor": 2.0,
            },
        ]
        result = self.tool.generate(recipes)

        beef = next(i for i in result.items if "ground beef" in i.canonical_name.lower())
        # 1 lb * 2 = 2 lbs
        self.assertAlmostEqual(beef.quantity, 2.0, places=1)

        bc = next(i for i in result.items if "breadcrumb" in i.canonical_name.lower())
        # 0.5 cup * 2 = 1 cup
        self.assertAlmostEqual(bc.quantity, 1.0, places=1)

    # ── Test 5: Aisle classification ──

    def test_aisle_classification(self):
        """Key ingredients should be assigned to expected aisles."""
        recipes = [
            {
                "title": "Mixed",
                "ingredients": [
                    "1 lb ground beef",
                    "2 medium tomatoes, diced",
                    "0.5 cup granulated sugar",
                    "1 cup unsalted butter, softened",
                    "1 tsp salt",
                    "2 tbsp olive oil",
                ],
            }
        ]
        result = self.tool.generate(recipes)

        aisle_map = {i.canonical_name: i.aisle for i in result.items}

        self.assertEqual(aisle_map.get("ground beef"), "Meat & Seafood")
        # "medium tomatoes" or "tomatoes"
        tomato_aisle = next(i.aisle for i in result.items if "tomato" in i.canonical_name)
        self.assertEqual(tomato_aisle, "Produce")
        self.assertEqual(aisle_map.get("granulated sugar"), "Baking")
        self.assertEqual(aisle_map.get("salt"), "Spices & Seasonings")
        self.assertEqual(aisle_map.get("olive oil"), "Oils & Vinegars")

    # ── Test 6: Non-quantified ingredients handled ──

    def test_non_quantified_ingredient(self):
        """Ingredients without quantities should appear with 'as needed'."""
        recipes = [
            {
                "title": "Simple",
                "ingredients": ["Salt and black pepper to taste"],
            }
        ]
        result = self.tool.generate(recipes)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].display_quantity, "as needed")

    # ── Test 7: By-aisle grouping in output ──

    def test_by_aisle_grouping(self):
        """The to_dict output should include by_aisle grouping."""
        recipes = [
            {
                "title": "Multi-aisle Recipe",
                "ingredients": [
                    "1 lb ground beef",
                    "2 cups all-purpose flour",
                    "1 cup unsalted butter, softened",
                ],
            }
        ]
        result = self.tool.generate(recipes)
        d = result.to_dict()
        aisles = d["by_aisle"]
        self.assertIn("Meat & Seafood", aisles)
        self.assertIn("Baking", aisles)
        self.assertIn("Dairy & Eggs", aisles)

    # ── Test 8: Unit conversion to readable output ──

    def test_volume_unit_conversion_to_cups(self):
        """Large tsp quantities should be displayed in cups."""
        recipes = [
            {
                "title": "Recipe A",
                "ingredients": ["24 tbsp olive oil"],  # 24 tbsp = 72 tsp = 1.5 cups
            }
        ]
        result = self.tool.generate(recipes)
        oil = next(i for i in result.items if "olive oil" in i.canonical_name.lower())
        self.assertEqual(oil.unit, "cups")
        self.assertAlmostEqual(oil.quantity, 1.5, places=1)


    # ── Test 9: Store packaging notes ──

    def test_store_packaging_notes(self):
        """Common items like butter, pasta, eggs should include packaging notes."""
        recipes = [
            {
                "title": "Baking Day",
                "ingredients": [
                    "1 cup unsalted butter, softened",
                    "2 large eggs",
                    "12 oz dry pasta",
                ],
            }
        ]
        result = self.tool.generate(recipes)
        items_by_name = {i.canonical_name: i for i in result.items}

        butter = next(i for i in result.items if "butter" in i.canonical_name)
        self.assertIsNotNone(butter.packaging_note)
        self.assertIn("stick", butter.packaging_note)

        pasta = next(i for i in result.items if "pasta" in i.canonical_name)
        self.assertIsNotNone(pasta.packaging_note)
        self.assertIn("box", pasta.packaging_note)

        egg = next(i for i in result.items if "egg" in i.canonical_name)
        self.assertIsNotNone(egg.packaging_note)
        self.assertIn("carton", egg.packaging_note)


if __name__ == "__main__":
    unittest.main()
