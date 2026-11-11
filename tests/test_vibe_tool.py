"""Unit tests for VibeSearchTool."""

import unittest
from dinnerguest.tools.vibe_search_tool import VibeSearchTool


class TestVibeSearchTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = VibeSearchTool()

    def test_search_cozy_dishes(self):
        results = self.tool.search_by_vibe("It's going to be cold this weekend, what's something cozy I can make?")
        self.assertGreater(len(results), 0)
        # Verify recipes with cozy / comfort tags are returned
        titles = [r.title for r in results]
        self.assertTrue(
            any("Skillet" in t or "Meatballs" in t or "Crisp" in t or "Bolognese" in t for t in titles)
        )

    def test_search_spicy_pork_noodles_filter(self):
        results = self.tool.search_by_vibe(
            query="spicy pork",
            filters={"format": "noodle", "protein": "pork"},
        )
        self.assertGreaterEqual(len(results), 1)
        top = results[0]
        self.assertEqual(top.recipe_id, "recipe_003")
        self.assertIn("Noodles", top.title)

    def test_search_spicy_pork_tacos_filter(self):
        results = self.tool.search_by_vibe(
            query="spicy pork",
            filters={"format": "taco", "protein": "pork"},
        )
        self.assertGreaterEqual(len(results), 1)
        top = results[0]
        self.assertEqual(top.recipe_id, "recipe_011")
        self.assertIn("Tacos", top.title)

    def test_search_sweet_dessert_filter(self):
        results = self.tool.search_by_vibe(
            query="cozy sweet treat",
            filters={"flavor_profile": "sweet"},
        )
        self.assertGreaterEqual(len(results), 1)
        titles = [r.title for r in results]
        self.assertTrue(any("Cookies" in t or "Crisp" in t for t in titles))

    def test_allergen_report_in_vibe_results(self):
        results = self.tool.search_by_vibe("spicy noodles")
        self.assertGreaterEqual(len(results), 1)
        dan_dan = next(r for r in results if r.recipe_id == "recipe_003")
        self.assertIsNotNone(dan_dan.allergen_report)
        self.assertTrue(dan_dan.allergen_report.has_allergens)
        # Contains soy, sesame, wheat
        self.assertIn("Sesame", dan_dan.allergen_report.unique_allergen_names)


if __name__ == "__main__":
    unittest.main()
