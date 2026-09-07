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
        self.api = n8n_api.DatasheetAPI(
            store_factory=lambda: self.store, publisher=self.publisher,
            evidence_hmac_key=b"test-key-000000000000000000000000000", run_lock=self.lock,
            clock=lambda: self.now, ttl_seconds=60,
        )

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

    def test_tools_renew_and_cannot_change_selected_product_or_revision(self):
        token = self.api.claim("run-1")["lease_token"]
        self.now = 55
        self.api.call("pv_research_contract", {}, token)
        self.now = 80
        self.assertTrue(self.api.call("pv_next_product", {}, token)["found"])
        for body in (
            {"product_id": "p-2", "source_hash": "source-1", "decision": {}},
            {"product_id": "p-1", "source_hash": "source-2", "decision": {}},
            {"product_id": "p-1", "source_hash": "source-1", "decision": {"expected_revision": "other"}},
        ):
            with self.assertRaises(n8n_api.SessionError):
                self.api.call("pv_save_research", body, token)

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
        with self.assertRaises(n8n_api.CompletionValidationError):
            self.api.call("pv_validate_research", args, token)
        args["decision"] = {}
        with mock.patch.object(n8n_api, "validate_research_decision", side_effect=TypeError("invalid status")):
            self.assertFalse(self.api.call("pv_validate_research", args, token)["valid"])
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

    def test_pdf_failure_is_not_a_request_to_retry_research(self):
        self.api.call.side_effect = ValueError("PDF parser failed")
        status, result = self.request("/datasheet/tools/read_datasheet_pdf")
        self.assertEqual(502, status)
        self.assertEqual("tool_failed", result["code"])


if __name__ == "__main__":
    unittest.main()
