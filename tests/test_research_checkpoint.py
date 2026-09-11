from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/wikijs-sync-products/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pv_wiki.catalogue_snapshot import replace_catalogue_snapshot
from pv_wiki.hermes_store import CompletionValidationError, HermesCompletionStore
from pv_wiki.n8n_api import DatasheetAPI
from pv_wiki.research_checkpoint import CHECKPOINT_TABLE, MAX_CONTEXT_BYTES, ResearchCheckpointStore, encode_context
from pv_wiki.state import StateStore
from test_datasheet_decision import KEY, decision, evidence, product


class ResearchCheckpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "state.sqlite3"
        with StateStore(self.path) as state:
            replace_catalogue_snapshot(state, [product(), product("P-43")])
        self.api = self.make_api()

    def make_api(self):
        return DatasheetAPI(store_factory=lambda: HermesCompletionStore(self.path, evidence_hmac_key=KEY),
                            evidence_hmac_key=KEY)

    def checkpoint(self, api, claim, *, stage="draft", context=None):
        selected = claim["work"]["product"]
        return api.call("pv_checkpoint_research", {
            "product_id": selected["product_id"], "source_hash": selected["source_hash"],
            "stage": stage, "context": context if context is not None else {"decision": decision()},
        }, claim["lease_token"])

    def test_restart_resumes_untrusted_context_before_next_product(self):
        first = self.api.claim("first")
        context = {"pdf": evidence(), "draft": decision(), "feedback": ["Check battery voltage"]}
        self.checkpoint(self.api, first, context=context)
        self.api.release(first["lease_token"])
        restarted = self.make_api()
        resumed = restarted.claim("after-restart")
        self.assertEqual("P-42", resumed["work"]["product"]["product_id"])
        self.assertEqual(context, resumed["work"]["checkpoint"]["context"])
        self.assertTrue(resumed["work"]["checkpoint"]["requires_revalidation"])
        result = restarted.call("pv_research_result", {}, "")
        self.assertFalse(result["completed"])
        self.assertFalse(result["published"])
        self.assertIsNone(result["markdown"])
        self.assertNotIn("context", result["checkpoint"])
        with HermesCompletionStore(self.path, evidence_hmac_key=KEY) as store:
            self.assertIsNone(store.get_completion("P-42"))

    def test_changed_or_blank_brand_checkpoint_never_resumes(self):
        claim = self.api.claim("first")
        self.checkpoint(self.api, claim)
        self.api.release(claim["lease_token"])
        for replacement in ({**product(), "product_name": "PV-420"}, product(brand="   ")):
            with StateStore(self.path) as state:
                replace_catalogue_snapshot(state, [replacement, product("P-43")])
            with HermesCompletionStore(self.path, evidence_hmac_key=KEY) as store:
                checkpoints = ResearchCheckpointStore(store)
                self.assertIsNone(checkpoints.resumable())
                self.assertFalse(checkpoints.summary("P-42")["resumable"])

    def test_source_refresh_between_claim_and_checkpoint_rejects_write(self):
        claim = self.api.claim("first")
        with StateStore(self.path) as state:
            replace_catalogue_snapshot(state, [{**product(), "product_name": "PV-420"}, product("P-43")])
        with self.assertRaisesRegex(ValueError, "source or revision changed"):
            self.checkpoint(self.api, claim)
        with HermesCompletionStore(self.path, evidence_hmac_key=KEY) as store:
            self.assertIsNone(ResearchCheckpointStore(store).summary("P-42"))

    def test_checkpoint_does_not_let_an_invalid_decision_save(self):
        claim = self.api.claim("first")
        wrong = decision()
        wrong["parameters"][0]["model"] = "PV-43"
        self.checkpoint(self.api, claim, stage="reviewed", context={"decision": wrong, "review": {"approved": True}})
        selected = claim["work"]["product"]
        with self.assertRaises(CompletionValidationError):
            self.api.call("pv_save_research", {"product_id": "P-42", "source_hash": selected["source_hash"],
                "decision": wrong, "evidence_documents": [evidence()]}, claim["lease_token"])
        with HermesCompletionStore(self.path, evidence_hmac_key=KEY) as store:
            self.assertIsNone(store.get_completion("P-42"))
            self.assertIsNotNone(ResearchCheckpointStore(store).summary("P-42"))

    def test_valid_save_clears_checkpoint_and_returns_actual_rendered_result(self):
        claim = self.api.claim("first")
        self.checkpoint(self.api, claim)
        selected = claim["work"]["product"]
        self.api.call("pv_save_research", {"product_id": "P-42", "source_hash": selected["source_hash"],
            "decision": decision(), "evidence_documents": [evidence()]}, claim["lease_token"])
        self.api.release(claim["lease_token"])
        result = self.api.call("pv_research_result", {}, "")
        self.assertTrue(result["completed"])
        self.assertTrue(result["publishable"])
        self.assertFalse(result["published"])
        self.assertIsNone(result["wiki_path"])
        self.assertIsNone(result["checkpoint"])
        self.assertEqual("5000", result["parameters"][0]["value"])
        self.assertIn("5000", result["markdown"])
        self.assertIn("25 °C", result["markdown"])
        self.assertEqual("publication", self.make_api().claim("publish-next")["work"]["kind"])

    def test_cleanup_crash_cannot_resume_an_accepted_completion(self):
        claim = self.api.claim("first")
        self.checkpoint(self.api, claim)
        with HermesCompletionStore(self.path, evidence_hmac_key=KEY) as store:
            selected = claim["work"]["product"]
            store.save_completion(product_id="P-42", source_hash=selected["source_hash"],
                                  decision=decision(), evidence_documents=[evidence()])
            checkpoints = ResearchCheckpointStore(store)
            self.assertIsNone(checkpoints.resumable())
            self.assertFalse(checkpoints.summary("P-42")["resumable"])

    def test_reresearch_revision_change_invalidates_old_checkpoint(self):
        claim = self.api.claim("first")
        self.checkpoint(self.api, claim)
        with HermesCompletionStore(self.path, evidence_hmac_key=KEY) as store:
            selected = claim["work"]["product"]
            store.save_completion(product_id="P-42", source_hash=selected["source_hash"],
                                  decision=decision(), evidence_documents=[evidence()])
            store.mark_for_reresearch_all_eligible(backup_path=self.path.parent / "before-reresearch.json")
            self.assertIsNone(ResearchCheckpointStore(store).resumable())
            self.assertFalse(ResearchCheckpointStore(store).summary("P-42")["resumable"])

    def test_context_limits_and_credential_rejection_preserve_pdf_receipts(self):
        retained = {"pdf": evidence(), "visual_receipts": [{"signature": "signed-proof"}]}
        self.assertEqual(retained, json.loads(encode_context(retained)))
        for context in (
            {"nested": {"lease_token": "secret"}}, {"credentials": {"id": "secret"}},
            {"nested": [{"X-API-Key": "secret"}]}, {"Authorization": "Bearer secret"},
            {"binary": {"page": "image"}}, {"image_url": "data:image/png;base64,AAAA"},
            {"pdf": b"%PDF raw"}, {"confidence": float("nan")},
            {"text": "x" * MAX_CONTEXT_BYTES},
        ):
            with self.subTest(keys=list(context)):
                with self.assertRaises(ValueError):
                    encode_context(context)

    def test_publication_readiness_is_exposed_without_credentials(self):
        with mock.patch.dict(os.environ, {"WIKIJS_URL": "", "WIKIJS_TOKEN": ""}):
            claim = self.api.claim("unconfigured")
            self.assertFalse(claim["publication_configured"])
            self.assertFalse(self.api.call("pv_research_contract", {}, claim["lease_token"])["publication_configured"])
            self.assertFalse(self.api.call("pv_research_result", {}, "")["publication_configured"])
            self.api.release(claim["lease_token"])
        with mock.patch.dict(os.environ, {"WIKIJS_URL": "https://wiki.example", "WIKIJS_TOKEN": "unit-only-credential-value-12345"}):
            claim = self.api.claim("configured")
            self.assertTrue(claim["publication_configured"])
            self.assertNotIn("unit-only-credential-value-12345", json.dumps(claim))


if __name__ == "__main__":
    unittest.main()
