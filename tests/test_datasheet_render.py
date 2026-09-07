from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/wikijs-sync-products/scripts"))
from pv_wiki.render import render_product_page, merge_auto_block


class DatasheetRenderTests(unittest.TestCase):
    def test_v4_preserves_ranges_conditions_pages_and_visual_correction(self):
        decision = {
            "schema_version": "4", "manufacturer": "ACME", "model": "X1", "outcome": "publish",
            "datasheet_parameters": [{
                "name": "Temperature", "value": "−40 … +85", "unit": "°C", "page": 2,
                "conditions": "At 50% load", "footnotes": ["* Derate above +60 °C"],
                "scope": "shared", "source_kind": "visual",
                "correction": {"before": {"value": "40 … 85", "unit": "°C"},
                               "after": {"value": "−40 … +85", "unit": "°C"},
                               "reason": "Minus and plus signs missing from extraction"},
            }],
            "visual_review": {"status": "reviewed", "pages": [2]}, "analysis_status": "unavailable",
            "omissions": [{"source_label": "Power", "reason": "Adjacent model column is ambiguous"}],
            "datasheets": [{"url": "https://manufacturer.example/download?id=1"}],
        }
        page = render_product_page({"product_id": "X1", "product_name": "Component"}, decision)
        for expected in ("−40 … +85 °C", "At 50% load", "Derate above +60 °C", "数据表页码",
                         "| 2 |", "系列共用参数", "图片校正", "Power", "Adjacent model column",
                         "专业分析或翻译未完成", "https://manufacturer.example/download?id=1"):
            self.assertIn(expected, page)
        merged = merge_auto_block("Operator notes\n\n" + page + "\nKeep this footer", page)
        self.assertTrue(merged.startswith("Operator notes\n\n"))
        self.assertTrue(merged.endswith("\nKeep this footer"))

    def test_visual_degradation_is_explicit_without_hiding_parameters(self):
        for status in ("text_only", "unavailable", "failed", "partial"):
            page = render_product_page({"product_id": "X1"}, {
                "schema_version": "4", "visual_review": {"status": status},
                "datasheet_parameters": [{"name": "Mass", "value": "2", "unit": "kg", "page": 1}],
            })
            self.assertIn("2 kg", page)
            self.assertIn("文本解析结果", page)


if __name__ == "__main__":
    unittest.main()
