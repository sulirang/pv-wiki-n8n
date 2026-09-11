from __future__ import annotations

import copy
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
                         "中文翻译未完成", "https://manufacturer.example/download?id=1"):
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

    def test_v4_translates_descriptive_values_and_keeps_original_in_a_separate_paragraph(self):
        decision = {"schema_version": "4", "datasheet_parameters": [
            {"name": "Beveiliging", "value": "Ja", "value_zh": "支持", "page": 4},
            {"name": "Vermogensfactor", "value": "0.8 geleidend ... 0.8 na-ijlend",
             "value_zh": "0.8 超前～0.8 滞后", "page": 4},
            {"name": "Power", "value": "3,680 W", "unit": "W", "page": 4},
        ]}
        original = copy.deepcopy(decision)
        page = render_product_page({"product_id": "X1"}, decision)
        self.assertIn("| 支持<br><br>原文：Ja |", page)
        self.assertIn("| 0.8 超前～0.8 滞后<br><br>原文：0.8 geleidend ... 0.8 na-ijlend |", page)
        self.assertIn("| 3,680 W |", page)
        self.assertEqual(original, decision)

    def test_v4_conditions_and_footnotes_pair_original_then_chinese_without_shifting_empty_items(self):
        decision = {"schema_version": "4", "datasheet_parameters": [{
            "name": "Voltage", "value": "600 V / 495 V", "page": 4,
            "conditions": "At 50% load", "conditions_zh": "在 50% 负载下",
            "footnotes": ["*1 PV string only", "", "*3 Connected to and operating with LG battery"],
            "footnotes_zh": ["*1 仅适用于光伏组串", "", "*3 连接 LG 电池并与其共同运行时"],
        }]}
        original = copy.deepcopy(decision)
        page = render_product_page({"product_id": "X1"}, decision)
        self.assertIn("条件（原文）：At 50% load<br><br>条件（中文）：在 50% 负载下", page)
        self.assertIn("脚注 1（原文）：\\*1 PV string only<br><br>脚注 1（中文）：\\*1 仅适用于光伏组串", page)
        self.assertIn("脚注 3（原文）：\\*3 Connected to and operating with LG battery<br><br>脚注 3（中文）：\\*3 连接 LG 电池并与其共同运行时", page)
        self.assertNotIn("脚注 2", page)
        self.assertEqual(original, decision)

    def test_bilingual_cells_escape_source_and_translation_text_and_keep_old_v4_readable(self):
        decision = {"schema_version": "4", "datasheet_parameters": [{
            "name": "Feature", "value": "Ja|<script>", "value_zh": "支持|<img>",
            "conditions": "Original|<b>", "conditions_zh": "译文|<svg>",
            "footnotes": ["Legacy footnote"],
        }]}
        page = render_product_page({"product_id": "X1"}, decision)
        self.assertIn("支持\\|&lt;img&gt;<br><br>原文：Ja\\|&lt;script&gt;", page)
        self.assertIn("条件（原文）：Original\\|&lt;b&gt;<br><br>条件（中文）：译文\\|&lt;svg&gt;", page)
        self.assertIn("脚注（原文）：Legacy footnote", page)
        self.assertNotIn("脚注（中文）", page)

    def test_values_with_complete_units_do_not_repeat_units_or_rewrite_raw_values(self):
        values = [
            ("190 V ~ 480 V", "V"), ("190V~480V", "V"), ("3,680 W", "W"),
            ("3,680 VA", "VA"), ("THD≤3%", "%"), ("−30 ~ +60 °C", "°C"),
            ("0 % RH～100 % RH", "% RH"), ("350~450 Vdc", "Vdc"),
        ]
        decision = {"schema_version": "4", "datasheet_parameters": [
            {"name": f"Value {i}", "value": value, "unit": unit, "page": 4}
            for i, (value, unit) in enumerate(values)
        ]}
        original = copy.deepcopy(decision)
        page = render_product_page({"product_id": "X1"}, decision)
        for value, unit in values:
            with self.subTest(value=value):
                self.assertIn(f"| {value} |", page)
                self.assertNotIn(f"{value} {unit} |", page)
        self.assertEqual(original, decision)

    def test_unit_suffix_must_match_the_complete_case_sensitive_unit(self):
        from pv_wiki.render import _value_with_unit
        for value, unit, expected in (
            ("16", "A", "16 A"), ("−40 … +85", "°C", "−40 … +85 °C"),
            ("2 kW", "W", "2 kW W"), ("2 mm", "m", "2 mm m"),
            ("2 N·m", "m", "2 N·m m"), ("2 mW", "MW", "2 mW MW"),
            ("2 kW (converted)", "W", "2 kW (converted) W"),
            ("2 mm (converted)", "m", "2 mm (converted) m"),
            ("2 N·m (converted)", "m", "2 N·m (converted) m"),
            ("2 m/s (converted)", "m", "2 m/s (converted) m"),
            ("2 W/m² (converted)", "W", "2 W/m² (converted) W"),
            ("2 m² (converted)", "m", "2 m² (converted) m"),
        ):
            self.assertEqual(expected, _value_with_unit(value, unit))

    def test_mixed_metric_and_imperial_values_keep_existing_units_before_conversion(self):
        from pv_wiki.render import _value_with_unit
        decision = {"schema_version": "4", "datasheet_parameters": [
            {"name": "Weight", "value": "10.6 kg (23.4 lb)", "unit": "kg", "page": 2},
            {"name": "Dimension", "value": "375 x 375 x 161.5 mm (14.8 x 14.8 x 6.4 inch)",
             "unit": "mm", "page": 2},
        ]}
        original = copy.deepcopy(decision)
        page = render_product_page({"product_id": "X1"}, decision)
        for row in decision["datasheet_parameters"]:
            self.assertEqual(row["value"], _value_with_unit(row["value"], row["unit"]))
            escaped = row["value"].replace("(", "\\(").replace(")", "\\)")
            self.assertIn(f"| {escaped} |", page)
            self.assertNotIn(f"{escaped} {row['unit']} |", page)
        self.assertEqual(original, decision)

    def test_recovered_rows_are_grouped_stably_without_displaying_historical_analysis(self):
        decision = {"schema_version": "4", "datasheet_parameters": [
            {"name": "First input", "value": "1", "unit": "V", "section": "Input", "subsection": "DC"},
            {"name": "Output row", "value": "2", "unit": "W", "section": "Output"},
            {"name": "Battery row", "value": "3", "unit": "V", "section": "Input", "subsection": "Battery"},
            {"name": "Later input", "value": "4 V", "unit": "V", "section": "Input", "section_zh": "输入", "subsection": "DC"},
            {"name": "Later output", "value": "5", "unit": "W", "section": "Output"},
        ], "professional_analysis": {"sections": [{"section_code": "dc_input", "paragraphs": [{
            "analysis_zh": "该参数来自数据表。", "basis_parameter_ids": ["p004"],
        }]}]}}
        original = copy.deepcopy(decision)
        page = render_product_page({"product_id": "X1"}, decision)
        self.assertEqual(1, page.count("#### Input｜输入"))
        self.assertEqual(1, page.count("#### Output"))
        self.assertEqual(1, page.count("##### DC"))
        positions = [page.index("| " + label + " |") for label in (
            "First input", "Later input", "Battery row", "Output row", "Later output",
        )]
        self.assertEqual(sorted(positions), positions)
        self.assertNotIn("## 产品分析", page)
        self.assertNotIn("该参数来自数据表。", page)
        self.assertNotIn("原数据表顺序", page)
        self.assertIn("按数据表参数分组展示", page)
        self.assertEqual(original, decision)

    def test_v4_historical_analysis_is_hidden_without_losing_bilingual_data_or_false_warnings(self):
        from pv_wiki.datasheet_decision import render_datasheet_decision
        decision = {"schema_version": "4", "analysis_status": "partial",
            "parameters": [{"parameter_id": "p001", "source_label": "Protection", "value": "Yes",
                "unit": "", "page": 2, "conditions": "At nominal voltage", "footnotes": ["PV string only"]}],
            "analysis": {"translations": [{"parameter_id": "p001", "name_zh": "保护", "value_zh": "支持",
                "conditions_zh": "在标称电压下", "footnotes_zh": ["仅适用于光伏组串"]}],
                "sections": "malformed historical analysis", "overall_limitations_zh": "historical advice"},
            "analysis_omissions": [{"item": "sections[0]", "reason": "old analysis failed"}],
            "professional_analysis": {"summary": "Historical recommendation"}}
        original = copy.deepcopy(decision)
        rendered = render_datasheet_decision(decision)
        page = render_product_page({"product_id": "X1"}, rendered)
        self.assertEqual("complete", rendered["translation_status"])
        self.assertEqual([], rendered["translation_omissions"])
        self.assertNotIn("professional_analysis", rendered)
        self.assertNotIn("## 产品分析", page)
        self.assertNotIn("Historical recommendation", page)
        self.assertNotIn("翻译未完成", page)
        self.assertNotIn("未通过校验", page)
        self.assertIn("支持<br><br>原文：Yes", page)
        self.assertIn("条件（原文）：At nominal voltage<br><br>条件（中文）：在标称电压下", page)
        self.assertIn("脚注（原文）：PV string only<br><br>脚注（中文）：仅适用于光伏组串", page)
        self.assertEqual(original, decision)

    def test_v4_translation_failures_remain_visible_when_analysis_is_disabled(self):
        from pv_wiki.datasheet_decision import render_datasheet_decision
        rendered = render_datasheet_decision({"schema_version": "4",
            "parameters": [{"parameter_id": "p001", "source_label": "Feature", "value": "Yes"}],
            "analysis": {"translations": [{"parameter_id": "p001", "name_zh": "功能", "value_zh": 17}]}})
        page = render_product_page({"product_id": "X1"}, rendered)
        self.assertEqual("partial", rendered["translation_status"])
        self.assertIn("部分中文翻译未通过校验", page)
        self.assertIn("Yes", page)
        self.assertNotIn("分析", page)

    def test_historical_malformed_introduction_uses_known_identity_without_changing_storage(self):
        from pv_wiki.datasheet_decision import render_datasheet_decision
        decision = {"schema_version": "4", "manufacturer": "Huawei", "model": "SUN2000L-3.68KTL",
                    "product_description_zh": "Huawei SUN2000L-3.68KTL\ufffd\ufffd"}
        original = copy.deepcopy(decision)
        rendered = render_datasheet_decision(decision)
        page = render_product_page({"product_id": "01073802"}, rendered)
        self.assertEqual("Huawei SUN2000L-3.68KTL", rendered["product_description_zh"])
        self.assertNotIn("\ufffd", page)
        self.assertEqual(original, decision)

    def test_v4_catalogue_subtitle_and_complete_introduction_preserve_company_punctuation(self):
        introduction = "Huawei Technologies Co., Ltd. SUN2000L-3.68KTL；参数来自官方数据表。"
        product = {"product_id": "01073802", "product_name": "HUAWEI SUN2000L - 3.68KTL", "brand_code": "HUAWEI"}
        decision = {"schema_version": "4", "manufacturer": "Huawei Technologies Co., Ltd.",
                    "model": "SUN2000L-3.68KTL", "product_description_zh": introduction}
        page = render_product_page(product, decision)
        self.assertIn("## HUAWEI SUN2000L - 3.68KTL\n", page)
        self.assertIn("| 产品描述 | " + introduction + " |", page)
        self.assertNotIn("｜. SUN2000L", page)


if __name__ == "__main__":
    unittest.main()
