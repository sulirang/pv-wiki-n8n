from __future__ import annotations

import copy
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "wikijs-sync-products" / "scripts"))

from pv_wiki.catalogue_snapshot import replace_catalogue_snapshot
from pv_wiki.config import WikiSettings
from pv_wiki.datasheet_decision import normalize_optional_analysis, validate_datasheet_decision
from pv_wiki.evidence_receipt import issue_datasheet_receipt, issue_datasheet_view_receipt
from pv_wiki.hermes_store import (
    CompletionValidationError, HermesCompletionStore, PublicationFenceError,
    ProductSourceChangedError, RenderModel, publish_researched,
)
from pv_wiki.state import StateStore


KEY = b"datasheet-decision-tests-key-1234567890"
DOC = "a" * 64
PDF_URL = "https://manufacturer.example/files/series.pdf"
TEXT = "A series technical table whose layout need not become five exact fact quotes."


def product(product_id: str = "P-42", brand: str | None = "Unknown Maker") -> dict:
    return {"product_id": product_id, "brand_code": brand, "family_code": "inverter",
            "product_name": product_id.replace("P-", "PV-"), "unit_of_measure": "pcs",
            "created_at": None, "updated_at": None}


def evidence(*, model: str = "PV-42", content: str = TEXT, official_page: str | None = None,
             link_verified: bool = True, parameter_pages: tuple[int, ...] = (),
             pdf_url: str = PDF_URL, requested_pdf: str | None = None) -> dict:
    requested_pdf = requested_pdf or pdf_url
    page_evidence = None
    if official_page:
        page_evidence = {"requested_url": official_page, "final_url": official_page,
                         "redirect_chain": [official_page], "content_sha256": "b" * 64,
                         "link_status": "verified" if link_verified else "unverified",
                         "matched_link": pdf_url if link_verified else None,
                         "excerpt": "Manufacturer product downloads"}
    receipt = issue_datasheet_receipt(
        KEY, content=content, document_id=DOC, artifact_sha256=DOC,
        requested_url=requested_pdf, url=pdf_url, official_page_url=official_page,
        official_page_evidence=page_evidence,
        redirect_chain=[requested_pdf, pdf_url] if requested_pdf != pdf_url else [pdf_url],
        target_model=model,
        parameter_rows=[], parser_metadata={"contract_version": "test-datasheet-v1", "page_count": 3,
        "extracted_pages": 3 if content else 0, "truncated": False,
        "parse_status": "parsed" if content else "needs_visual"},
        page_manifest=[{"page": page, "text_chars": len(content), "has_parameters": page in parameter_pages} for page in (1, 2, 3)],
    )
    return {"url": pdf_url, "content": content, "receipt": receipt}


def view_receipt(page: int = 2, document_id: str = DOC) -> dict:
    return issue_datasheet_view_receipt(KEY, document_id=document_id, manifest=[{
        "page": page, "mime_type": "image/png", "sha256": "c" * 64,
        "width": 1200, "height": 1600, "region": None,
    }])


def decision(*, value: str = "5000", product_id: str = "P-42") -> dict:
    model = product_id.replace("P-", "PV-")
    return {"schema_version": "4", "product_id": product_id, "outcome": "publish",
            "manufacturer": "Unknown Maker", "model": model,
            "official_source": {"url": PDF_URL, "document_sha256": DOC,
                                "verification_reason": "The manufacturer's product page links this series datasheet."},
            "parameters": [{"parameter_id": "p001", "source_label": "Rated output power",
                            "value": value, "unit": "W", "section": "Output", "model": model,
                            "scope": "model", "page": 2, "status": "confirmed",
                            "conditions": ["At nominal grid voltage"], "footnotes": "Values at 25 °C"}],
            "visual_review": {"status": "text_only"}}


class DatasheetDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.path = self.folder / "state.sqlite3"
        with StateStore(self.path) as state:
            replace_catalogue_snapshot(state, [product()])

    def store(self) -> HermesCompletionStore:
        return HermesCompletionStore(self.path, evidence_hmac_key=KEY)

    def save(self, store: HermesCompletionStore, payload: dict | None = None,
             documents: list | None = None):
        payload = decision() if payload is None else payload
        with store._state._connection() as connection:
            row = connection.execute("SELECT source_hash FROM product_catalogue_snapshot WHERE product_id = ?",
                                     (payload["product_id"],)).fetchone()
        return store.save_completion(product_id=payload["product_id"], source_hash=row["source_hash"],
                                     decision=payload, evidence_documents=documents or [evidence()])

    def test_one_official_pdf_one_parameter_unknown_brand_without_legacy_gates(self) -> None:
        payload = decision()
        payload["confidence"] = 0.01
        payload["parameter_enrichment"] = {"translations": "invalid optional enhancement"}
        with self.store() as store:
            saved = self.save(store, payload)
            self.assertTrue(saved.created)
            self.assertEqual([], saved.completion.decision["facts"])
            self.assertEqual("unavailable", saved.completion.decision["analysis_status"])
            rendered = RenderModel.from_completion(saved.completion).decision
            self.assertEqual(4, rendered["_render_schema"])
            row = rendered["datasheet_parameters"][0]
            self.assertEqual("5000", row["value"])
            self.assertEqual(2, row["page"])
            self.assertEqual(["At nominal grid voltage"], row["conditions"])
            self.assertEqual("Values at 25 °C", row["footnotes"])

    def test_partial_optional_analysis_supports_500_parameters_without_legacy_metadata(self) -> None:
        payload = decision()
        template = payload["parameters"][0]
        payload["parameters"] = [{**template, "parameter_id": f"p{index:03d}",
                                   "source_label": f"Parameter {index}", "value": str(index)}
                                  for index in range(1, 501)]
        payload["analysis"] = {
            "translations": [{"parameter_id": "p500", "name_zh": "末项参数"},
                             {"parameter_id": "p999", "name_zh": "错误引用"}],
            "sections": [{"section_code": "product_positioning", "paragraphs": [
                {"analysis_zh": "可结合该参数判断安装空间与组件配置。", "basis_parameter_ids": ["p500"],
                 "conditions_zh": [], "limitations_zh": ["需结合实际工况。"]},
                {"analysis_zh": "不能使用不存在的参数。", "basis_parameter_ids": ["p999"]},
            ]}], "overall_limitations_zh": []}
        with self.store() as store:
            saved = self.save(store, payload).completion
            self.assertEqual(500, len(saved.decision["parameters"]))
            self.assertEqual("partial", saved.decision["analysis_status"])
            self.assertEqual(2, len(saved.decision["analysis_omissions"]))
            rendered = RenderModel.from_completion(saved).decision
            self.assertEqual("末项参数", rendered["datasheet_parameters"][-1]["name_zh"])
            self.assertNotIn("name_zh", rendered["datasheet_parameters"][0])
            self.assertNotIn("professional_analysis", rendered)
            self.assertEqual(1, len(saved.decision["analysis"]["sections"][0]["paragraphs"]))
            self.assertEqual("partial", rendered["translation_status"])
            self.assertEqual(1, len(rendered["translation_omissions"]))

    def test_bilingual_values_and_notes_roundtrip_without_changing_source_or_hash(self) -> None:
        payload = decision(value="Ja")
        first = payload["parameters"][0]
        first.update({"source_label": "Bescherming tegen eilandbedrijf", "unit": "",
                      "footnotes": ["Bij nominale netspanning", "Waarden bij 25 °C"]})
        payload["parameters"].append({**copy.deepcopy(first), "parameter_id": "p002",
            "source_label": "Instelbare vermogensfactor", "value": "0.8 geleidend ... 0.8 na-ijlend"})
        original = copy.deepcopy(payload)
        baseline, _ = validate_datasheet_decision(original, product_id="P-42", product=product(),
                                                 evidence_documents=[evidence()], evidence_hmac_key=KEY)
        payload["analysis"] = {"translations": [
            {"parameter_id": "p001", "name_zh": "防孤岛保护", "value_zh": "支持",
             "conditions_zh": "在标称电网电压下", "footnotes_zh": ["在标称电网电压下", "数值对应 25 °C 条件。"]},
            {"parameter_id": "p002", "value_zh": "0.8 超前～0.8 滞后"},
        ]}
        with self.store() as store:
            saved = self.save(store, payload).completion
            self.assertTrue(saved.publishable)
            self.assertEqual(baseline["parameters"], saved.decision["parameters"])
            self.assertEqual(baseline["parameter_set_sha256"], saved.decision["parameter_set_sha256"])
            self.assertEqual(payload["analysis"]["translations"], store.get_completion("P-42").decision["analysis"]["translations"])
            rendered = RenderModel.from_completion(saved).decision
            rows = rendered["datasheet_parameters"]
            self.assertEqual("Ja", rows[0]["value"])
            self.assertEqual("支持", rows[0]["value_zh"])
            self.assertEqual("在标称电网电压下", rows[0]["conditions_zh"])
            self.assertEqual(["在标称电网电压下", "数值对应 25 °C 条件。"], rows[0]["footnotes_zh"])
            self.assertEqual("0.8 geleidend ... 0.8 na-ijlend", rows[1]["value"])
            self.assertEqual("0.8 超前～0.8 滞后", rows[1]["value_zh"])
            rows[0]["footnotes"][0] = "display mutation"
            self.assertEqual("Bij nominale netspanning", saved.decision["parameters"][0]["footnotes"][0])
            self.assertEqual(original["parameters"], payload["parameters"])

    def test_invalid_optional_translation_fields_leave_valid_siblings_and_note_positions(self) -> None:
        payload = decision()
        payload["parameters"][0]["footnotes"] = ["First source note", "Second source note", "Third source note"]
        payload["analysis"] = {"translations": [{"parameter_id": "p001", "name_zh": 42,
            "section_zh": "交流输出", "value_zh": ["invalid"], "conditions_zh": "在标称电网电压下",
            "footnotes_zh": ["第一条脚注", 17, "第三条脚注"]}]}
        with self.store() as store:
            saved = self.save(store, payload).completion
            self.assertTrue(saved.publishable)
            self.assertEqual("partial", saved.decision["analysis_status"])
            translated = saved.decision["analysis"]["translations"][0]
            self.assertNotIn("name_zh", translated)
            self.assertNotIn("value_zh", translated)
            self.assertEqual("交流输出", translated["section_zh"])
            self.assertEqual("在标称电网电压下", translated["conditions_zh"])
            self.assertEqual(["第一条脚注", "", "第三条脚注"], translated["footnotes_zh"])
            self.assertEqual({"translations[0].name_zh", "translations[0].value_zh",
                              "translations[0].footnotes_zh[1]"},
                             {item["item"] for item in saved.decision["analysis_omissions"]})
            self.assertEqual(payload["parameters"][0]["footnotes"], saved.decision["parameters"][0]["footnotes"])

    def test_footnote_translation_alignment_handles_legacy_string_and_empty_placeholders(self) -> None:
        for source, translated in (("Legacy source note", ["历史脚注"]),
                                   ("", []), (["First", "", "Third"], ["第一条", "", "第三条"])):
            with self.subTest(source=source):
                rows = [{"parameter_id": "p001", "footnotes": source}]
                result, rejected = normalize_optional_analysis({"translations": [
                    {"parameter_id": "p001", "name_zh": "参数", "footnotes_zh": translated}]}, rows)
                self.assertEqual([], rejected)
                self.assertEqual(translated, result["translations"][0]["footnotes_zh"])
        result, rejected = normalize_optional_analysis({"translations": [
            {"parameter_id": "p001", "name_zh": "参数", "value_zh": "支持", "footnotes_zh": ["无法匹配"]}]},
            [{"parameter_id": "p001", "footnotes": ["First", "Second"]}])
        self.assertEqual("支持", result["translations"][0]["value_zh"])
        self.assertNotIn("footnotes_zh", result["translations"][0])
        self.assertEqual("translations[0].footnotes_zh", rejected[0]["item"])

    def test_datasheet_language_metadata_is_optional_and_cannot_block_confirmed_parameters(self) -> None:
        payload = decision()
        payload["official_source"].update({"datasheet_language": "nl",
            "language_selection_reason": "No official English edition for this exact model and market was found."})
        with self.store() as store:
            saved = self.save(store, payload).completion
            self.assertEqual("nl", saved.decision["official_source"]["datasheet_language"])
            self.assertEqual(payload["official_source"]["language_selection_reason"],
                             saved.decision["official_source"]["language_selection_reason"])
        payload["official_source"]["datasheet_language"] = ["invalid"]
        normalized, _ = validate_datasheet_decision(payload, product_id="P-42", product=product(),
                                                   evidence_documents=[evidence()], evidence_hmac_key=KEY)
        self.assertNotIn("datasheet_language", normalized["official_source"])
        self.assertEqual("official_source.datasheet_language", normalized["omissions"][-1]["source_label"])
        self.assertEqual(1, len(normalized["parameters"]))

    def test_missing_official_identity_reason_keeps_product_unfinished(self) -> None:
        payload = decision()
        payload["official_source"]["verification_reason"] = " "
        with self.store() as store:
            with self.assertRaisesRegex(CompletionValidationError, "verification_reason"):
                self.save(store, payload)
            self.assertIsNotNone(store.next_product())

    def test_brand_prefixed_spaced_catalogue_model_saves_only_the_exact_model(self) -> None:
        record = product("01073873", "HUAWEI")
        record["product_name"] = "HUAWEI SUN2000L - 3.68KTL"
        with StateStore(self.path) as state:
            replace_catalogue_snapshot(state, [record])
        for model in (
            "SUN2000L", "3.68KTL", "SUN2000-3.68KTL", "SUN2000L1-3.68KTL",
            "SUN2000L-3.6KTL", "SUN2000L-3.68KTL-L1", "SUN2000L-3.68KTL-M1",
            "SUN2000L-3.68KTL",
        ):
            payload = decision(product_id=record["product_id"])
            payload.update({"manufacturer": "Huawei", "model": model})
            payload["parameters"][0]["model"] = model
            with self.subTest(model=model), self.store() as store:
                if model == "SUN2000L-3.68KTL":
                    saved = self.save(store, payload, [evidence(model=model)]).completion
                    self.assertTrue(saved.publishable)
                    self.assertEqual(model, saved.decision["model"])
                    self.assertEqual(model, saved.decision["parameters"][0]["model"])
                else:
                    with self.assertRaisesRegex(CompletionValidationError,
                                                "model does not match the catalogue"):
                        self.save(store, payload, [evidence(model=model)])
                    self.assertEqual(0, store.counts()["completed"])

    def test_missing_known_parameter_page_is_partial_review_without_blocking_publication(self) -> None:
        payload = decision()
        payload["visual_review"] = {"status": "reviewed", "receipts": [view_receipt(2)]}
        with self.store() as store:
            saved = self.save(store, payload, [evidence(parameter_pages=(1, 2))]).completion
            self.assertTrue(saved.publishable)
            self.assertEqual("partial", saved.decision["visual_review"]["status"])

    def test_unknown_and_registry_category_brands_are_eligible_blank_variants_are_not(self) -> None:
        records = [product("P-1", None), product("P-2", ""), product("P-3", " \t\n"),
                   product("P-4", "NEVER-REGISTERED"), product("P-5", "DZJ")]
        with StateStore(self.path) as state:
            replace_catalogue_snapshot(state, records)
        with self.store() as store:
            self.assertEqual("P-4", store.next_product().product_id)
            self.assertEqual("P-5", store.next_product(after_product_id="P-4").product_id)
            self.assertEqual("P-4", store.next_product(after_product_id="P-5").product_id)
            self.assertEqual(3, store.counts()["skipped_blank_brand"])
            self.assertEqual(2, store.counts()["eligible_unresearched"])

    def test_blank_brand_later_filled_remains_researchable(self) -> None:
        with StateStore(self.path) as state:
            replace_catalogue_snapshot(state, [product(brand=" ")])
        with self.store() as store:
            self.assertIsNone(store.next_product())
            self.assertEqual(0, store.counts()["completed"])
        with StateStore(self.path) as state:
            replace_catalogue_snapshot(state, [product()])
        with self.store() as store:
            self.assertEqual("P-42", store.next_product().product_id)

    def test_uncertain_rows_are_omitted_and_final_ids_are_assigned(self) -> None:
        payload = decision()
        payload["parameters"].insert(0, {"status": "uncertain", "source_label": "Peak power", "value": "?"})
        with self.store() as store:
            saved = self.save(store, payload).completion.decision
            self.assertEqual(1, len(saved["parameters"]))
            self.assertEqual("p001", saved["parameters"][0]["parameter_id"])
            self.assertEqual("Peak power", saved["omissions"][0]["source_label"])

    def test_wrong_model_bad_page_and_implicit_shared_scope_cannot_publish(self) -> None:
        for changes in ({"model": "PV-43"}, {"page": 4}, {"scope": "shared"},
                        {"scope": "shared", "applies_to_models": ["PV-43"]}):
            with self.subTest(changes=changes), self.store() as store:
                payload = decision()
                payload["parameters"][0].update(changes)
                with self.assertRaises(CompletionValidationError):
                    self.save(store, payload)
                self.assertEqual(0, store.counts()["completed"])

    def test_explicit_shared_value_and_visual_correction_preserve_provenance(self) -> None:
        payload = decision()
        payload["parameters"][0].update({"scope": "shared", "applies_to_models": ["PV-42", "PV-43"],
                                         "source_kind": "visual", "correction": {
                                             "before": {"value": "500", "unit": "W"},
                                             "after": {"value": "5000", "unit": "W"},
                                             "reason": "The original PDF cell shows the additional zero."}})
        payload["visual_review"] = {"status": "reviewed", "receipts": [view_receipt()]}
        with self.store() as store:
            saved = self.save(store, payload, [evidence(content="")]).completion.decision
            # Only page 2 of this three-page scan was reviewed.
            self.assertEqual("partial", saved["visual_review"]["status"])
            row = saved["parameters"][0]
            self.assertEqual("500", row["correction"]["before"]["value"])
            self.assertEqual(DOC, row["document_sha256"])

    def test_visual_additions_and_corrections_require_the_same_document_page(self) -> None:
        for receipts_to_send in ([], [view_receipt(1)], [view_receipt(2, "d" * 64)]):
            with self.subTest(receipts=receipts_to_send), self.store() as store:
                payload = decision()
                payload["parameters"][0]["source_kind"] = "visual"
                payload["visual_review"] = {"status": "reviewed", "receipts": receipts_to_send}
                with self.assertRaises(CompletionValidationError):
                    self.save(store, payload)

    def test_image_only_pdf_does_not_publish_invented_text_parameters(self) -> None:
        with self.store() as store, self.assertRaisesRegex(CompletionValidationError, "image-only PDF"):
            self.save(store, documents=[evidence(content="")])

    def test_tampered_pdf_and_unverified_cross_domain_link_are_rejected(self) -> None:
        tampered = evidence()
        tampered["receipt"]["document_id"] = "d" * 64
        for documents in ([tampered], [evidence(official_page="https://brand.example/product", link_verified=False)]):
            with self.subTest(documents=documents), self.store() as store:
                with self.assertRaises(CompletionValidationError):
                    self.save(store, documents=documents)
        with self.store() as store:
            self.assertTrue(self.save(store, documents=[evidence(official_page="https://brand.example/product")]).created)

    def test_direct_brand_official_pdf_preserves_unverified_support_page(self) -> None:
        pdf_url = "https://solar.huawei.com/download?document=datasheet"
        official_page = "https://support.huawei.com/product"
        with StateStore(self.path) as state:
            replace_catalogue_snapshot(state, [product(brand="HUAWEI")])
        payload = decision()
        payload["manufacturer"] = "Huawei Technologies Co., Ltd."
        payload["official_source"].update({"url": pdf_url, "official_page_url": official_page})
        document = evidence(pdf_url=pdf_url, official_page=official_page, link_verified=False)
        with self.store() as store:
            saved = self.save(store, payload, [document]).completion
            self.assertTrue(saved.publishable)
            source = saved.decision["official_source"]
            self.assertEqual(pdf_url, source["url"])
            self.assertEqual(official_page, source["official_page_url"])
            self.assertEqual("unverified", source["official_page_evidence"]["link_status"])
            self.assertIsNone(source["official_page_evidence"]["matched_link"])

    def test_direct_official_pdf_exception_uses_catalogue_brand_and_final_exact_host(self) -> None:
        for brand, pdf_url, requested_pdf in (
            ("HUAWEI", "https://unknown-cdn.example/datasheet.pdf", None),
            ("HUAWEI", "https://solar.huawei.com.attacker.example/datasheet.pdf", None),
            ("HUAWEI", "https://unconfigured.solar.huawei.com/datasheet.pdf", None),
            ("HUAWEI", "https://aikosolar.com/datasheet.pdf", None),
            ("AIKO", "https://solar.huawei.com/datasheet.pdf", None),
            ("UNKNOWN-BRAND", "https://solar.huawei.com/datasheet.pdf", None),
            ("HUAWEI", "https://unknown-cdn.example/datasheet.pdf",
             "https://solar.huawei.com/download"),
        ):
            with self.subTest(brand=brand, pdf_url=pdf_url, requested_pdf=requested_pdf):
                with StateStore(self.path) as state:
                    replace_catalogue_snapshot(state, [product(brand=brand)])
                payload = decision()
                # The proposal cannot choose the trusted brand for itself.
                payload["manufacturer"] = "Huawei"
                payload["official_source"]["url"] = pdf_url
                document = evidence(pdf_url=pdf_url, requested_pdf=requested_pdf,
                                    official_page="https://support.huawei.com/product",
                                    link_verified=False)
                with self.store() as store:
                    with self.assertRaisesRegex(CompletionValidationError,
                                                "verified official-page download link"):
                        self.save(store, payload, [document])
                    self.assertEqual(0, store.counts()["completed"])

    def test_verified_official_page_link_still_allows_an_external_cdn(self) -> None:
        pdf_url = "https://unknown-cdn.example/datasheet.pdf"
        with StateStore(self.path) as state:
            replace_catalogue_snapshot(state, [product(brand="HUAWEI")])
        payload = decision()
        payload["official_source"]["url"] = pdf_url
        document = evidence(pdf_url=pdf_url, official_page="https://solar.huawei.com/product",
                            link_verified=True)
        with self.store() as store:
            source = self.save(store, payload, [document]).completion.decision["official_source"]
            self.assertEqual("verified", source["official_page_evidence"]["link_status"])
            self.assertEqual(pdf_url, source["official_page_evidence"]["matched_link"])

    def test_direct_brand_official_pdf_still_requires_valid_signed_evidence(self) -> None:
        pdf_url = "https://solar.huawei.com/datasheet.pdf"
        with StateStore(self.path) as state:
            replace_catalogue_snapshot(state, [product(brand="HUAWEI")])
        payload = decision()
        payload["official_source"]["url"] = pdf_url
        document = evidence(pdf_url=pdf_url, official_page="https://support.huawei.com/product",
                            link_verified=False)
        document["receipt"]["signature"] = "tampered"
        with self.store() as store:
            with self.assertRaises(CompletionValidationError):
                self.save(store, payload, [document])
            self.assertEqual(0, store.counts()["completed"])

    def test_reresearch_backs_up_once_then_replaces_with_revision_and_old_path(self) -> None:
        backup = self.folder / "before.json"
        with self.store() as store:
            old = self.save(store).completion
            store.mark_published("P-42", wiki_path="legacy/exact-page", wiki_action="created")
            marked = store.mark_for_reresearch_all_eligible(backup_path=backup)
            self.assertEqual(1, marked["marked"])
            self.assertTrue(backup.exists())
            self.assertEqual("5000", json.loads(json.loads(backup.read_text())["tables"]["product_research_completions"][0]["decision_json"])["parameters"][0]["value"])
            self.assertFalse(store.mark_for_reresearch_all_eligible()["created"])
            self.assertIsNone(store.pending_publication())
            self.assertEqual("reresearch_pending", publish_researched(store, "P-42")["reason"])
            selected = store.next_product()
            self.assertIsNotNone(selected.revision)
            failed = decision(value="6000")
            failed["expected_revision"] = selected.revision
            failed["parameters"][0]["status"] = "uncertain"
            with self.assertRaises(CompletionValidationError):
                self.save(store, failed)
            self.assertEqual(old.decision, store.get_completion("P-42").decision)
            with self.assertRaises(PublicationFenceError):
                self.save(store, decision(value="6000"))
            replacement = decision(value="6000")
            replacement["expected_revision"] = selected.revision
            replaced = self.save(store, replacement)
            self.assertTrue(replaced.created)
            self.assertEqual("legacy/exact-page", replaced.completion.wiki_path)
            self.assertIsNone(replaced.completion.published_at)
            self.assertEqual(0, store.counts()["reresearch_pending"])
            self.assertEqual(1, store.counts()["pending_publication"])
            self.assertIsNone(store.next_product())
            replay = copy.deepcopy(replacement)
            replay["parameters"][0]["value"] = "7000"
            self.assertFalse(self.save(store, replay).created)
            self.assertEqual("6000", store.get_completion("P-42").decision["parameters"][0]["value"])

    def test_backup_failure_cannot_mark_or_replace_old_completion(self) -> None:
        backup = self.folder / "already-exists.json"
        backup.write_text("keep", encoding="utf-8")
        with self.store() as store:
            old = self.save(store).completion
            with self.assertRaises(FileExistsError):
                store.mark_for_reresearch_all_eligible(backup_path=backup)
            self.assertEqual("keep", backup.read_text())
            self.assertEqual(old, store.get_completion("P-42"))
            self.assertIsNone(store.next_product())

    def test_pending_old_completion_is_not_dispatched_during_reresearch(self) -> None:
        with self.store() as store:
            self.save(store)
            self.assertEqual(1, store.counts()["pending_publication"])
            store.mark_for_reresearch_all_eligible(backup_path=self.folder / "pending.json")
            self.assertIsNone(store.pending_publication())
            self.assertEqual(0, store.counts()["pending_publication"])
            self.assertEqual(1, store.counts()["eligible_unresearched"])

    def test_reresearch_started_while_acquiring_publication_fence_prevents_old_io(self) -> None:
        with self.store() as store:
            self.save(store)
            original_fence = store.publication_fence
            def race(product_id):
                store.mark_for_reresearch_all_eligible(backup_path=self.folder / "race.json")
                return original_fence(product_id)
            client = mock.Mock()
            with mock.patch.object(store, "publication_fence", side_effect=race):
                result = publish_researched(store, "P-42", client_factory=client)
            self.assertEqual("reresearch_pending", result["reason"])
            client.assert_not_called()

    def test_replacement_publishes_to_existing_path_and_retry_does_not_research(self) -> None:
        settings = WikiSettings(base_url="https://wiki.example", token="test-token", locale="en",
                                path_prefix="products", home_path="home", home_title="Home", timeout=10,
                                new_page_private=True, new_page_published=False)
        paths = []
        class Client:
            def __init__(self, *args, **kwargs):
                pass
            def upsert_page(self, path, *args):
                paths.append(path)
                return {"action": "updated", "page": {"id": 7}}
        with self.store() as store:
            self.save(store)
            store.mark_published("P-42", wiki_path="original/human-assigned-path", wiki_action="created")
            store.mark_for_reresearch_all_eligible(backup_path=self.folder / "path.json")
            payload = decision(value="6000")
            payload["expected_revision"] = store.next_product().revision
            self.save(store, payload)
            result = publish_researched(store, "P-42", settings=settings, client_factory=Client)
            self.assertTrue(result["published"])
            self.assertEqual(["original/human-assigned-path"], paths)
            self.assertTrue(publish_researched(store, "P-42", settings=settings, client_factory=Client)["published"])
            self.assertEqual(1, len(paths))
            self.assertIsNone(store.next_product())

    def test_marking_includes_old_nonpublish_legacy_and_pending_records_only_if_branded(self) -> None:
        records = [product("P-42"), product("P-43"), product("P-44"), product("P-45", None)]
        with StateStore(self.path) as state:
            replace_catalogue_snapshot(state, records)
        with self.store() as store:
            self.save(store)
            with store._state._write_transaction() as connection:
                base = connection.execute("SELECT * FROM product_research_completions WHERE product_id='P-42'").fetchone()
                for product_id, published_at, outcome in (("P-43", None, "no_datasheet"),
                                                          ("P-44", base["completed_at"], "publish"),
                                                          ("P-45", base["completed_at"], "publish")):
                    row = connection.execute("SELECT * FROM product_catalogue_snapshot WHERE product_id=?", (product_id,)).fetchone()
                    connection.execute(
                        "INSERT INTO product_research_completions "
                        "(product_id,source_hash,product_json,decision_json,evidence_json,outcome,publishable,completed_at,published_at,wiki_path,wiki_action) "
                        "VALUES (?,?,?,'{}','[]',?,?,?,?,?,?)",
                        (product_id, row["source_hash"], row["payload_json"], outcome,
                         1 if outcome == "publish" else 0, base["completed_at"], published_at,
                         "legacy/" + product_id if published_at else None, "legacy" if published_at else None),
                    )
            result = store.mark_for_reresearch_all_eligible(backup_path=self.folder / "mixed.json")
            self.assertEqual(3, result["marked"])
            self.assertEqual(3, store.counts()["reresearch_pending"])
            selected = store.next_product()
            self.assertEqual("P-42", selected.product_id)
            self.assertEqual("P-44", store.next_product(after_product_id="P-43").product_id)

    def test_concurrent_replacements_commit_once(self) -> None:
        with self.store() as store:
            self.save(store)
            store.mark_for_reresearch_all_eligible(backup_path=self.folder / "concurrent.json")
            selected = store.next_product()
        barrier = threading.Barrier(2)
        def worker(value: str):
            with self.store() as store:
                payload = decision(value=value)
                payload["expected_revision"] = selected.revision
                barrier.wait(timeout=5)
                return self.save(store, payload)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(worker, ["6000", "7000"]))
        self.assertEqual(1, sum(result.created for result in results))
        self.assertEqual(results[0].completion.decision, results[1].completion.decision)


if __name__ == "__main__":
    unittest.main()
