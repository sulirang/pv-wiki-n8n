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
             link_verified: bool = True, parameter_pages: tuple[int, ...] = ()) -> dict:
    page_evidence = None
    if official_page:
        page_evidence = {"requested_url": official_page, "final_url": official_page,
                         "redirect_chain": [official_page], "content_sha256": "b" * 64,
                         "link_status": "verified" if link_verified else "unverified",
                         "matched_link": PDF_URL if link_verified else None,
                         "excerpt": "Manufacturer product downloads"}
    receipt = issue_datasheet_receipt(
        KEY, content=content, document_id=DOC, artifact_sha256=DOC,
        requested_url=PDF_URL, url=PDF_URL, official_page_url=official_page,
        official_page_evidence=page_evidence, redirect_chain=[PDF_URL], target_model=model,
        parameter_rows=[], parser_metadata={"contract_version": "test-datasheet-v1", "page_count": 3,
        "extracted_pages": 3 if content else 0, "truncated": False,
        "parse_status": "parsed" if content else "needs_visual"},
        page_manifest=[{"page": page, "text_chars": len(content), "has_parameters": page in parameter_pages} for page in (1, 2, 3)],
    )
    return {"url": PDF_URL, "content": content, "receipt": receipt}


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
            self.assertEqual(1, len(rendered["professional_analysis"]["sections"][0]["paragraphs"]))

    def test_missing_official_identity_reason_keeps_product_unfinished(self) -> None:
        payload = decision()
        payload["official_source"]["verification_reason"] = " "
        with self.store() as store:
            with self.assertRaisesRegex(CompletionValidationError, "verification_reason"):
                self.save(store, payload)
            self.assertIsNotNone(store.next_product())

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
