from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/wikijs-sync-products/scripts"))

from pv_wiki import n8n_api, server
from pv_wiki.datasheet_decision import normalize_optional_analysis


class FakeStore:
    def __init__(self):
        self.pending = None
        self.product = SimpleNamespace(
            product_id="p-1", source_hash="source-1", revision="revision-1",
            payload={"product_id": "p-1", "brand_code": "ACME", "product_name": "MODEL-1"},
        )
        self.saved = []
        self.selected_after = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def pending_publication(self):
        return self.pending

    def next_product(self, after_product_id=None):
        self.selected_after.append(after_product_id)
        return self.product

    def catalogue_status(self):
        return SimpleNamespace(initialized=True)

    def counts(self):
        return {"eligible_unresearched": 1}

    def save_completion(self, **arguments):
        self.saved.append(arguments)
        return SimpleNamespace(created=True, completion={"product_id": arguments["product_id"]})


class AgentSessionTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.now = 0.0
        self.lock = threading.Lock()
        self.publisher = mock.Mock(return_value={"ok": True, "published": True})
        self.checkpoints = mock.Mock()
        self.checkpoints.resumable.return_value = None
        self.api = n8n_api.DatasheetAPI(
            store_factory=lambda: self.store, publisher=self.publisher,
            evidence_hmac_key=b"test-key-000000000000000000000000000", run_lock=self.lock,
            clock=lambda: self.now, ttl_seconds=60,
            checkpoint_factory=lambda store: self.checkpoints,
        )

    def test_contract_bilingual_example_uses_original_note_positions(self):
        contract = n8n_api.research_contract()
        analysis, rejected = normalize_optional_analysis(contract["analysis_example"],
                                                         [contract["parameter_example"]])
        self.assertEqual([], rejected)
        translated = analysis["translations"][0]
        self.assertEqual("在标称电网电压下", translated["conditions_zh"])
        self.assertEqual(["数值对应 25 °C 条件。"], translated["footnotes_zh"])
        self.assertEqual("en", contract["decision_example"]["official_source"]["datasheet_language"])
        self.assertFalse(contract["product_analysis_enabled"])
        self.assertEqual([], contract["analysis_section_codes"])
        self.assertEqual({"translations"}, set(contract["analysis_example"]))

    def test_claim_serializes_n8n_and_legacy_and_reuses_same_execution(self):
        first = self.api.claim("run-1")
        self.assertTrue(first["acquired"])
        self.assertEqual("research", first["work"]["kind"])
        self.assertEqual("revision-1", first["work"]["product"]["revision"])
        self.assertTrue(self.lock.locked())
        self.assertEqual(first, self.api.claim("run-1"))
        self.assertFalse(self.api.claim("run-2")["acquired"])
        self.assertEqual(1, len(self.store.selected_after))
        self.api.release(first["lease_token"])
        self.assertFalse(self.lock.locked())

    def test_pending_publication_wins_without_selecting_research(self):
        self.store.pending = {"product_id": "pending-1", "publishable": True}
        claim = self.api.claim("run-1")
        self.assertEqual("publication", claim["work"]["kind"])
        self.assertEqual([], self.store.selected_after)
        self.api.call("pv_publish_result", {"product_id": "pending-1"}, claim["lease_token"])
        self.publisher.assert_called_once_with(self.store, "pending-1")

    def test_no_work_releases_lock(self):
        self.store.product = None
        self.assertEqual("no_work", self.api.claim("run-1")["reason"])
        self.assertFalse(self.lock.locked())

    def test_checkpoint_is_prioritized_and_bound_to_claim_without_saving(self):
        resumed = SimpleNamespace(product_id="p-2", source_hash="source-2", revision="revision-2",
                                  payload={"product_id": "p-2", "brand_code": "ACME", "product_name": "MODEL-2"})
        context = {"pdf": {"document_id": "a" * 64}, "decision": {"parameters": []}}
        self.checkpoints.resumable.return_value = (resumed, {"stage": "draft", "context": context})
        claim = self.api.claim("resume")
        self.assertEqual("p-2", claim["work"]["product"]["product_id"])
        self.assertEqual(context, claim["work"]["checkpoint"]["context"])
        self.assertEqual([], self.store.selected_after)
        self.checkpoints.save.return_value = {"stage": "reviewed"}
        result = self.api.call("pv_checkpoint_research", {
            "product_id": "p-2", "source_hash": "source-2", "revision": "agent-chosen",
            "stage": "reviewed", "context": context,
        }, claim["lease_token"])
        self.assertTrue(result["ok"])
        self.checkpoints.save.assert_called_once_with(product_id="p-2", source_hash="source-2",
            revision="revision-2", stage="reviewed", context=context)
        self.assertEqual([], self.store.saved)

    def test_checkpoint_cannot_change_product_or_skip_session(self):
        with self.assertRaises(n8n_api.SessionError):
            self.api.call("pv_checkpoint_research", {}, "")
        claim = self.api.claim("wrong-checkpoint")
        with self.assertRaises(n8n_api.SessionError):
            self.api.call("pv_checkpoint_research", {"product_id": "p-2", "source_hash": "source-1",
                "stage": "reviewed", "context": {}}, claim["lease_token"])
        self.checkpoints.save.assert_not_called()

    def test_abandoned_session_can_expire_for_legacy_dispatch(self):
        self.api.claim("run-1")
        self.now = 59
        self.api.expire_idle_session()
        self.assertTrue(self.lock.locked())
        self.now = 61
        self.api.expire_idle_session()
        self.assertFalse(self.lock.locked())
        self.assertTrue(self.lock.acquire(blocking=False))
        self.lock.release()

    def test_failed_claim_releases_lock(self):
        with mock.patch.object(self.store, "pending_publication", side_effect=RuntimeError("offline")):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                self.api.claim("run-1")
        self.assertFalse(self.lock.locked())
        self.assertTrue(self.api.claim("run-2")["acquired"])

    def test_stale_execution_cannot_save_or_release_successor(self):
        old = self.api.claim("run-1")["lease_token"]
        self.now = 61
        new = self.api.claim("run-2")["lease_token"]
        self.assertNotEqual(old, new)
        with self.assertRaises(n8n_api.SessionError):
            self.api.call("pv_save_research", {}, old)
        with self.assertRaises(n8n_api.SessionError):
            self.api.release(old)
        self.assertEqual([], self.store.saved)
        self.assertTrue(self.lock.locked())

    def test_tools_renew_and_cannot_change_selected_product(self):
        token = self.api.claim("run-1")["lease_token"]
        self.now = 55
        self.api.call("pv_research_contract", {}, token)
        self.now = 80
        self.assertTrue(self.api.call("pv_next_product", {}, token)["found"])
        self.api.release(token)
        for index, (body, message) in enumerate((
            ({"product_id": "p-2", "source_hash": "source-1", "decision": {}}, "product_id must match"),
            ({"product_id": "p-1", "source_hash": "source-2", "decision": {}}, "source_hash must match"),
        )):
            with self.subTest(message=message):
                token = self.api.claim(f"wrong-selection-{index}")["lease_token"]
                with self.assertRaisesRegex(n8n_api.SessionError, message):
                    self.api.call("pv_validate_research", body, token)
                with self.assertRaisesRegex(n8n_api.SessionError, "research ended after a tool failure"):
                    self.api.call("pv_next_product", {}, token)
                self.assertEqual([], self.store.saved)
                self.api.release(token)

    def test_wrong_candidate_revision_is_repairable_but_cannot_save(self):
        for index, revision in enumerate(("source-1", "other")):
            with self.subTest(revision=revision):
                token = self.api.claim(f"wrong-revision-{index}")["lease_token"]
                args = {
                    "product_id": "p-1", "source_hash": "source-1",
                    "decision": {"expected_revision": revision}, "evidence_documents": [],
                }
                with mock.patch.object(n8n_api, "validate_research_decision") as validate:
                    result = self.api.call("pv_validate_research", args, token)
                    validate.assert_not_called()
                self.assertEqual({"ok": True, "valid": False, "errors": [
                    "expected_revision must match the claimed revision",
                ]}, result)
                saved_before = len(self.store.saved)
                with self.assertRaisesRegex(n8n_api.CompletionValidationError, "expected_revision must match"):
                    self.api.call("pv_save_research", args, token)
                self.assertEqual(saved_before, len(self.store.saved))
                args["decision"]["expected_revision"] = "revision-1"
                with mock.patch.object(n8n_api, "validate_research_decision", return_value=({"parameters": [1]}, [])):
                    self.assertTrue(self.api.call("pv_validate_research", args, token)["valid"])
                self.api.call("pv_save_research", args, token)
                self.assertEqual(saved_before + 1, len(self.store.saved))
                self.assertEqual("revision-1", self.store.saved[-1]["decision"]["expected_revision"])
                self.api.release(token)

    def test_validation_is_read_only_and_returns_repair_feedback(self):
        token = self.api.claim("run-1")["lease_token"]
        args = {"product_id": "p-1", "source_hash": "source-1", "decision": {}, "evidence_documents": []}
        with mock.patch.object(n8n_api, "validate_research_decision", side_effect=n8n_api.CompletionValidationError("wrong model column")):
            result = self.api.call("pv_validate_research", args, token)
        self.assertTrue(result["ok"])
        self.assertFalse(result["valid"])
        self.assertEqual(["wrong model column"], result["errors"])
        self.assertEqual([], self.store.saved)
        self.assertEqual("revision-1", args["decision"]["expected_revision"])

    def test_validation_then_save_uses_the_same_selected_product(self):
        token = self.api.claim("run-1")["lease_token"]
        args = {"product_id": "p-1", "source_hash": "source-1", "decision": {}, "evidence_documents": []}
        with mock.patch.object(n8n_api, "validate_research_decision", return_value=({"parameters": [1]}, [])):
            self.assertTrue(self.api.call("pv_validate_research", args, token)["valid"])
        self.api.call("pv_save_research", args, token)
        self.assertEqual(1, len(self.store.saved))
        self.assertEqual("p-1", self.store.saved[0]["product_id"])

    def test_malformed_candidate_remains_repairable_with_same_token(self):
        token = self.api.claim("run-1")["lease_token"]
        args = {"product_id": "p-1", "source_hash": "source-1", "decision": [], "evidence_documents": []}
        self.assertEqual({"ok": True, "valid": False, "errors": ["decision must be an object"]},
                         self.api.call("pv_validate_research", args, token))
        with self.assertRaises(n8n_api.CompletionValidationError):
            self.api.call("pv_save_research", args, token)
        args["decision"] = {}
        for error in (TypeError("invalid status"), ValueError("invalid parameter shape")):
            with mock.patch.object(n8n_api, "validate_research_decision", side_effect=error):
                self.assertEqual({"ok": True, "valid": False, "errors": [str(error)]},
                                 self.api.call("pv_validate_research", args, token))
        with mock.patch.object(n8n_api, "validate_research_decision", return_value=({"parameters": [1]}, [])):
            self.assertTrue(self.api.call("pv_validate_research", args, token)["valid"])

    def test_page_images_remain_binary_payload_not_a_textual_model_call(self):
        self.api._datasheets = mock.Mock()
        self.api._datasheets.view_pages.return_value = {
            "document_id": "a" * 64, "images": [{"page": 1, "mime_type": "image/png", "data": "aW1hZ2U="}],
            "receipt": {"signature": "test"},
        }
        token = self.api.claim("run-1")["lease_token"]
        payload = self.api.call("view_datasheet_pages", {"document_id": "a" * 64, "pages": [1]}, token)
        self.assertEqual("aW1hZ2U=", payload["images"][0]["data"])
        self.api._datasheets.view_pages.assert_called_once_with(document_id="a" * 64, pages=[1])

    def test_renderer_unavailable_keeps_lease_for_text_review_checkpoint_and_save(self):
        from pv_wiki.datasheets import DatasheetError
        for index, message in enumerate((
            "PDF rendering exceeded its timeout",
            "PDF renderer exited without a result",
            "PDF rendering failed (ModuleNotFoundError: No module named 'pypdfium2')",
            "PDF rendering failed (PdfiumError: Failed to render page)",
        )):
            with self.subTest(message=message):
                self.api._datasheets = mock.Mock()
                self.api._datasheets.view_pages.side_effect = DatasheetError(message)
                token = self.api.claim(f"render-fallback-{index}")["lease_token"]
                payload = self.api.call("view_datasheet_pages", {"document_id": "a" * 64, "pages": [1]}, token)
                self.assertFalse(payload["ok"])
                self.assertEqual("vision_unavailable", payload["code"])
                self.assertEqual([], payload["images"])
                checkpoint = self.api.call("pv_checkpoint_research", {"product_id": "p-1", "source_hash": "source-1",
                    "stage": "draft", "context": {"visual_review": "failed", "text": "verified text"}}, token)
                self.assertTrue(checkpoint["ok"])
                args = {"product_id": "p-1", "source_hash": "source-1", "decision": {}, "evidence_documents": []}
                with mock.patch.object(n8n_api, "validate_research_decision", return_value=({"parameters": [1]}, [])):
                    self.assertTrue(self.api.call("pv_validate_research", args, token)["valid"])
                self.assertTrue(self.api.call("pv_save_research", args, token)["ok"])
                self.api.release(token)

    def test_view_tampering_invalid_requests_and_credentials_still_end_session(self):
        from pv_wiki.datasheets import DatasheetError
        from pv_wiki.evidence_receipt import EvidenceReceiptError
        for index, error in enumerate((
            DatasheetError("cached PDF hash differs from document_id; fetch it again"),
            DatasheetError("datasheet cache entry is not a bounded regular file"),
            DatasheetError("datasheet cache entry cannot be read"),
            DatasheetError("document_id must be the 64-character PDF SHA256"),
            DatasheetError("pages must contain one to four unique 1-based page numbers"),
            DatasheetError("PDF rendering failed (DatasheetError: requested page is outside the PDF)"),
            EvidenceReceiptError("evidence receipt is invalid"),
            PermissionError("renderer or cache permission denied"),
        )):
            with self.subTest(error=str(error)):
                self.api._datasheets = mock.Mock()
                self.api._datasheets.view_pages.side_effect = error
                token = self.api.claim(f"invalid-view-{index}")["lease_token"]
                with self.assertRaises(type(error)):
                    self.api.call("view_datasheet_pages", {"document_id": "a" * 64, "pages": [1]}, token)
                with self.assertRaisesRegex(n8n_api.SessionError, "research ended after a tool failure"):
                    self.api.call("pv_checkpoint_research", {}, token)
                self.api.release(token)

    def test_publication_claim_cannot_use_page_renderer(self):
        self.store.pending = {"product_id": "pending-1", "publishable": True}
        self.api._datasheets = mock.Mock()
        token = self.api.claim("publication-view")["lease_token"]
        with self.assertRaisesRegex(n8n_api.SessionError, "claimed research product"):
            self.api.call("view_datasheet_pages", {"document_id": "a" * 64, "pages": [1]}, token)
        self.api._datasheets.view_pages.assert_not_called()

    def test_provider_failure_ends_execution_before_any_paid_replay(self):
        self.api._exa = mock.Mock()
        self.api._exa.post.side_effect = RuntimeError("provider timeout")
        token = self.api.claim("run-1")["lease_token"]
        with self.assertRaisesRegex(RuntimeError, "provider timeout"):
            self.api.call("web_search_exa", {"query": "MODEL-1"}, token)
        with self.assertRaises(n8n_api.SessionError):
            self.api.call("web_search_exa", {"query": "MODEL-1 again"}, token)
        self.assertEqual(1, self.api._exa.post.call_count)
        self.assertEqual("execution_failed", self.api.claim("run-1")["reason"])
        self.assertTrue(self.api.release(token)["released"])
        self.assertTrue(self.api.claim("run-2")["acquired"])

    def test_fetch_accepts_single_url_without_mutating_input_and_canonical_urls_win(self):
        url = "https://support.huawei.com/enterprise/en/fusionsolar/sun2000l-pid-22027611"
        self.api._exa = mock.Mock()
        self.api._exa.post.return_value = {"results": [{"url": url, "text": "Official product downloads"}]}
        token = self.api.claim("single-url-fetch")["lease_token"]
        arguments = {"url": url, "maxCharacters": 6000}
        result = self.api.call("web_fetch_exa", arguments, token)
        self.assertTrue(result["ok"])
        self.assertEqual(url, result["results"][0]["url"])
        self.api._exa.post.assert_called_once_with("/contents", {"urls": [url], "text": {"maxCharacters": 6000}})
        self.assertEqual({"url": url, "maxCharacters": 6000}, arguments)
        self.api.call("web_fetch_exa", {"urls": [url], "url": "ignored-invalid-alias"}, token)
        self.assertEqual([url], self.api._exa.post.call_args.args[1]["urls"])

    def test_search_argument_errors_are_repairable_before_any_external_request(self):
        self.api._exa = mock.Mock()
        self.api._exa.post.return_value = {"results": []}
        token = self.api.claim("fix-search-arguments")["lease_token"]
        for tool, arguments in (
            ("web_search_exa", {"query": " "}),
            ("web_search_exa", {"query": "MODEL-1", "numResults": 101}),
            ("web_search_advanced_exa", {"query": "MODEL-1", "includeDomains": "maker.example"}),
            ("web_fetch_exa", {"urls": [], "url": "https://maker.example/product"}),
            ("web_fetch_exa", {"url": 17}),
            ("web_fetch_exa", {"url": ""}),
            ("web_fetch_exa", {"urls": ["file:///private/file"]}),
            ("web_fetch_exa", {"urls": ["https://maker.example/product"], "maxCharacters": 0}),
        ):
            with self.subTest(tool=tool, arguments=arguments):
                result = self.api.call(tool, arguments, token)
                self.assertFalse(result["ok"])
                self.assertEqual("invalid_tool_arguments", result["code"])
                self.assertEqual(tool, result["tool"])
                self.assertTrue(result["error"])
                self.assertTrue(result["expected_arguments"])
                self.api._exa.post.assert_not_called()
                self.assertTrue(self.api.call("pv_next_product", {}, token)["found"])
        self.assertTrue(self.api.call("web_search_exa", {"query": "MODEL-1"}, token)["ok"])
        self.api._exa.post.assert_called_once()

    def test_provider_value_error_is_not_misclassified_as_invalid_tool_arguments(self):
        for index, (tool, arguments) in enumerate((
            ("web_search_exa", {"query": "MODEL-1"}),
            ("web_fetch_exa", {"url": "https://maker.example/product"}),
        )):
            with self.subTest(tool=tool):
                self.api._exa = mock.Mock()
                self.api._exa.post.side_effect = ValueError("provider response decoding failed")
                token = self.api.claim(f"provider-value-error-{index}")["lease_token"]
                with self.assertRaisesRegex(ValueError, "provider response decoding failed"):
                    self.api.call(tool, arguments, token)
                with self.assertRaisesRegex(n8n_api.SessionError, "research ended after a tool failure"):
                    self.api.call(tool, arguments, token)
                self.assertEqual(1, self.api._exa.post.call_count)
                self.api.release(token)

    def test_invalid_fetch_response_still_ends_session_after_request(self):
        self.api._exa = mock.Mock()
        self.api._exa.post.return_value = {"results": [{"url": "not-a-url", "text": "Response text"}]}
        token = self.api.claim("malformed-fetch-response")["lease_token"]
        with self.assertRaises(ValueError):
            self.api.call("web_fetch_exa", {"url": "https://maker.example/product"}, token)
        with self.assertRaisesRegex(n8n_api.SessionError, "research ended after a tool failure"):
            self.api.call("pv_next_product", {}, token)
        self.assertEqual(1, self.api._exa.post.call_count)

    def test_pdf_value_error_ends_execution_instead_of_repairing_candidate(self):
        self.api._datasheets = mock.Mock()
        self.api._datasheets.read_pdf.side_effect = ValueError("PDF parser failed")
        token = self.api.claim("run-1")["lease_token"]
        args = {"url": "https://maker.example/datasheet", "target_model": "MODEL-1"}
        with self.assertRaisesRegex(ValueError, "PDF parser failed"):
            self.api.call("read_datasheet_pdf", args, token)
        for name, body in (
            ("read_datasheet_pdf", args),
            ("pv_validate_research", {"product_id": "p-1", "source_hash": "source-1", "decision": {}}),
        ):
            with self.assertRaisesRegex(n8n_api.SessionError, "research ended after a tool failure"):
                self.api.call(name, body, token)
        self.assertEqual(1, self.api._datasheets.read_pdf.call_count)
        self.assertEqual([], self.store.saved)
        self.assertTrue(self.api.release(token)["released"])


class AgentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.api = mock.Mock()
        self.token = "n8n-test-worker-" + "x" * 32
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.httpd = server.build_server(
            server.WorkerSettings(token=self.token, port=self.port),
            operations={"/run-one": lambda: {"ok": True}}, datasheet_api=self.api,
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(2)

    def request(self, path, payload=None, authorized=True, headers=None):
        request_headers = dict(headers or {})
        if authorized:
            request_headers["Authorization"] = "Bearer " + self.token
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", path, body=json.dumps(payload or {}), headers=request_headers)
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def test_claim_requires_auth_and_tool_forwards_execution_token(self):
        code, _ = self.request("/datasheet/claim", {"execution_id": "1"}, authorized=False)
        self.assertEqual(401, code)
        self.api.claim.assert_not_called()
        self.api.claim.return_value = {"ok": True, "acquired": False}
        self.assertEqual(200, self.request("/datasheet/claim", {"execution_id": "1"})[0])
        self.api.call.return_value = {"ok": True, "valid": False, "errors": ["missing page"]}
        status, result = self.request("/datasheet/tools/pv_validate_research", {"product_id": "p"},
                                      headers={"X-PV-Wiki-Lease": "opaque-token"})
        self.assertEqual(200, status)
        self.assertFalse(result["valid"])
        self.api.call.assert_called_once_with("pv_validate_research", {"product_id": "p"}, "opaque-token")

    def test_save_validation_is_routable_but_expired_ownership_is_rejected(self):
        self.api.call.side_effect = n8n_api.CompletionValidationError("wrong column")
        status, result = self.request("/datasheet/tools/pv_save_research")
        self.assertEqual(200, status)
        self.assertEqual("validation_failed", result["code"])
        self.api.call.side_effect = n8n_api.SessionError("expired")
        self.assertEqual(409, self.request("/datasheet/tools/pv_save_research")[0])

    def test_unknown_tools_do_not_dispatch(self):
        self.assertEqual(404, self.request("/datasheet/tools/execute_shell")[0])
        self.api.call.assert_not_called()

    def test_result_is_authorized_even_without_execution_lease(self):
        self.assertEqual(401, self.request("/datasheet/tools/pv_research_result", authorized=False)[0])
        self.api.call.assert_not_called()
        self.api.call.return_value = {"ok": True, "completed": False, "markdown": None}
        status, result = self.request("/datasheet/tools/pv_research_result", {"product_id": "p-1"})
        self.assertEqual(200, status)
        self.assertFalse(result["completed"])
        self.api.call.assert_called_once_with("pv_research_result", {"product_id": "p-1"}, "")

    def test_pdf_failure_is_not_a_request_to_retry_research(self):
        self.api.call.side_effect = ValueError("PDF parser failed")
        status, result = self.request("/datasheet/tools/read_datasheet_pdf")
        self.assertEqual(502, status)
        self.assertEqual("tool_failed", result["code"])

    def test_invalid_fetch_arguments_return_http_200_and_same_lease_can_be_corrected(self):
        exa = mock.Mock()
        exa.post.return_value = {"results": [{"url": "https://maker.example/product", "text": "Official product"}]}
        checkpoints = mock.Mock()
        checkpoints.resumable.return_value = None
        actual = n8n_api.DatasheetAPI(store_factory=FakeStore, exa_client=exa,
            evidence_hmac_key=b"test-key-000000000000000000000000000", checkpoint_factory=lambda _: checkpoints)
        lease = actual.claim("http-correct-arguments")["lease_token"]
        self.api.call.side_effect = actual.call
        headers = {"X-PV-Wiki-Lease": lease}
        status, result = self.request("/datasheet/tools/web_fetch_exa", {"urls": []}, headers=headers)
        self.assertEqual(200, status)
        self.assertEqual("invalid_tool_arguments", result["code"])
        self.assertIn("urls", result["expected_arguments"])
        exa.post.assert_not_called()
        status, result = self.request("/datasheet/tools/web_fetch_exa", {"url": "https://maker.example/product"}, headers=headers)
        self.assertEqual(200, status)
        self.assertTrue(result["ok"])
        exa.post.assert_called_once()
        self.assertTrue(actual.release(lease)["released"])


if __name__ == "__main__":
    unittest.main()
