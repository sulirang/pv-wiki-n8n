from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "wikijs-sync-products" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from pv_wiki import datasheets, documents  # noqa: E402
from pv_wiki.evidence_receipt import (  # noqa: E402
    EvidenceReceiptError,
    verify_datasheet_receipt,
    verify_datasheet_view_receipt,
)
from tests.test_documents import (  # noqa: E402
    FakeConnection,
    FakeResponse,
    minimal_text_pdf,
    public_resolver,
    r5_fixed_width_layout,
)


KEY = b"datasheet-test-key-with-at-least-32-bytes"
MODEL = "R5-10K-T2-15"
PDF_URL = "https://maker.example/download?id=42"
CDN_URL = "https://assets.example/specification"
PAGE_URL = "https://maker.example/products/r5"


def downloaded(content: bytes, *, requested: str = PDF_URL, final: str = PDF_URL, content_type: str = "application/pdf") -> documents.PDFDownload:
    return documents.PDFDownload(
        requested_url=requested, final_url=final, content=content,
        sha256=hashlib.sha256(content).hexdigest(), content_type=content_type,
        redirect_chain=(requested,) if final == requested else (requested, final),
    )


def table_parse(*_args: object, target_models: list[str], **_kwargs: object) -> documents.PDFText:
    layout = r5_fixed_width_layout()
    rows = documents.extract_model_parameter_rows(((1, layout),), target_models)
    return documents.PDFText(
        text=f"[Page 1]\n{layout}", page_count=1, extracted_pages=1, truncated=False,
        parameter_rows=rows, page_manifest=({"page": 1, "text_chars": len(layout), "has_parameters": True},),
    )


class DatasheetServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name)
        self.pdf = minimal_text_pdf("R5-10K-T2-15 Technical specification " + "Rated power 10000 W " * 12)
        self.download = mock.Mock(return_value=downloaded(self.pdf, final=CDN_URL))
        self.html = mock.Mock(side_effect=AssertionError("unexpected HTML request"))
        self.service = datasheets.DatasheetService(
            self.cache, KEY, downloader=self.download, html_downloader=self.html,
        )

    def parsed_result(self, **kwargs: object) -> dict[str, object]:
        with mock.patch.object(documents, "parse_pdf_text", side_effect=table_parse):
            return self.service.read_pdf(PDF_URL, MODEL, **kwargs)

    def test_official_cdn_download_and_model_candidates_are_signed(self) -> None:
        html = b'<html><title>Maker R5</title><p>R5-10K-T2-15 official specification</p><a href="../download?id=42">Download</a></html>'
        self.html.side_effect = None
        self.html.return_value = downloaded(html, requested=PAGE_URL, final=PAGE_URL, content_type="text/html")
        result = self.parsed_result(official_page_url=PAGE_URL)
        self.assertEqual(result["document_id"], hashlib.sha256(self.pdf).hexdigest())
        self.assertEqual(result["url"], CDN_URL)
        self.assertEqual(result["redirect_chain"], [PDF_URL, CDN_URL])
        self.assertEqual(result["page_count"], 1)
        self.assertEqual(result["parse_status"], "parsed")
        self.assertEqual(result["suggested_review_pages"], [1])
        self.assertTrue(result["parameter_rows"])
        self.assertTrue(all(row["model"] == MODEL for row in result["parameter_rows"]))
        source = result["official_page_evidence"]
        self.assertEqual(source["link_status"], "verified")
        self.assertEqual(source["matched_link"], PDF_URL)
        self.assertEqual(source["content_sha256"], hashlib.sha256(html).hexdigest())
        self.assertIn(MODEL, source["excerpt"])
        payload = verify_datasheet_receipt(KEY, receipt=result["receipt"], content=result["text"])
        self.assertEqual(payload["parameter_rows"], result["parameter_rows"])
        self.download.assert_called_once_with(PDF_URL, max_bytes=datasheets.MAX_PDF_BYTES, timeout=30, allow_public_redirects=True)

    def test_dynamic_or_unavailable_official_page_keeps_pdf_with_unverified_link(self) -> None:
        for html_result in (
            downloaded(b'<p>Maker R5</p><script>downloadPdf()</script>', requested=PAGE_URL, content_type="text/html"),
            documents.PDFDownloadError("page unavailable"),
        ):
            with self.subTest(page=type(html_result).__name__):
                self.html.side_effect = html_result if isinstance(html_result, Exception) else None
                self.html.return_value = html_result
                result = self.parsed_result(official_page_url=PAGE_URL)
                self.assertEqual(result["official_page_evidence"]["link_status"], "unverified")
                self.assertIsNone(result["official_page_evidence"]["matched_link"])
                verify_datasheet_receipt(KEY, receipt=result["receipt"])

    def test_parse_cache_reuses_hash_and_model_but_never_another_model_or_version(self) -> None:
        with mock.patch.object(documents, "parse_pdf_text", side_effect=table_parse) as parser:
            first = self.service.read_pdf(PDF_URL, MODEL)
            self.service.read_pdf(PDF_URL, MODEL)
            self.assertEqual(parser.call_count, 1)
            other = self.service.read_pdf(PDF_URL, "R5-8K-T2-15")
            self.assertEqual(parser.call_count, 2)
            self.assertEqual(first["document_id"], other["document_id"])
            self.assertTrue(all(row["model"] == "R5-8K-T2-15" for row in other["parameter_rows"]))
            with mock.patch.object(datasheets, "PARSER_VERSION", "test-parser-version-2"):
                self.service.read_pdf(PDF_URL, MODEL)
            self.assertEqual(parser.call_count, 3)

    def test_modified_parse_cache_is_recomputed_instead_of_resigned(self) -> None:
        original = self.parsed_result()
        path = next(self.cache.glob("*.parse.json"))
        cached = json.loads(path.read_text(encoding="utf-8"))
        cached["payload"]["parameter_rows"][0]["value"] = "999999"
        path.write_text(json.dumps(cached), encoding="utf-8")
        with mock.patch.object(documents, "parse_pdf_text", side_effect=table_parse) as parser:
            result = self.service.read_pdf(PDF_URL, MODEL)
        self.assertEqual(parser.call_count, 1)
        self.assertEqual(result["parameter_rows"], original["parameter_rows"])

    def test_receipt_rejects_changes_to_artifact_model_rows_and_source(self) -> None:
        result = self.parsed_result()
        mutations = (
            lambda value: value.update(document_id="0" * 64),
            lambda value: value.update(target_model="R5-8K-T2-15"),
            lambda value: value["parameter_rows"][0].update(value="999999"),
            lambda value: value["parser_metadata"].update(parse_status=[]),
            lambda value: value["page_manifest"][0].update(page=1.0),
            lambda value: value.update(official_page_url=PAGE_URL),
            lambda value: value["redirect_chain"].__setitem__(0, CDN_URL),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                receipt = copy.deepcopy(result["receipt"])
                mutate(receipt)
                with self.assertRaises(EvidenceReceiptError):
                    verify_datasheet_receipt(KEY, receipt=receipt)
        with self.assertRaises(EvidenceReceiptError):
            verify_datasheet_receipt(KEY, receipt=result["receipt"], content="changed")
        with self.assertRaises(EvidenceReceiptError):
            verify_datasheet_receipt(b"different-key-with-at-least-32-bytes", receipt=result["receipt"])

    def test_document_id_and_cached_artifact_are_checked_before_rendering(self) -> None:
        result = self.parsed_result()
        with mock.patch.object(datasheets, "_run_renderer") as renderer:
            for invalid in ("../outside", "F" * 64, "a" * 63, None):
                with self.subTest(document_id=invalid), self.assertRaises(datasheets.DatasheetError):
                    self.service.view_pages(invalid, [1])
            (self.cache / f'{result["document_id"]}.pdf').write_bytes(b"changed")
            with self.assertRaisesRegex(datasheets.DatasheetError, "hash differs"):
                self.service.view_pages(result["document_id"], [1])
            renderer.assert_not_called()

    def test_page_and_crop_limits_are_checked_before_rendering(self) -> None:
        result = self.parsed_result()
        with mock.patch.object(datasheets, "_run_renderer") as renderer:
            for pages in ([], [0], [True], [1.5], [1, 1], [1, 2, 3, 4, 5], [101], "1"):
                with self.subTest(pages=pages), self.assertRaises(datasheets.DatasheetError):
                    self.service.view_pages(result["document_id"], pages)
            for region in ([0, 0, 1], [0, 0, 2, 1], [0.7, 0, 0.2, 1], [0, 0, float("nan"), 1], [False, 0, 1, 1]):
                with self.subTest(region=region), self.assertRaises(datasheets.DatasheetError):
                    self.service.view_pages(result["document_id"], [1], region)
            renderer.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "symlink fixture requires POSIX")
    def test_cache_symlinks_are_rejected(self) -> None:
        outside = self.cache / "outside"
        outside.write_bytes(b"outside remains unchanged")
        digest = hashlib.sha256(self.pdf).hexdigest()
        (self.cache / f"{digest}.pdf").symlink_to(outside)
        with self.assertRaisesRegex(datasheets.DatasheetError, "escapes"):
            self.service.read_pdf(PDF_URL, MODEL)
        self.assertEqual(outside.read_bytes(), b"outside remains unchanged")

    def test_cache_capacity_evicts_old_own_entries_only(self) -> None:
        old = self.cache / f'{"a" * 64}.pdf'
        old.write_bytes(b"x" * 999_999)
        unrelated = self.cache / "operator-notes.txt"
        unrelated.write_text("retain", encoding="utf-8")
        service = datasheets.DatasheetService(self.cache, KEY, downloader=self.download, max_cache_bytes=1_000_000)
        with mock.patch.object(documents, "parse_pdf_text", side_effect=table_parse):
            result = service.read_pdf(PDF_URL, MODEL)
        self.assertFalse(old.exists())
        self.assertTrue((self.cache / f'{result["document_id"]}.pdf').exists())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "retain")
        self.assertLessEqual(sum(path.stat().st_size for path in self.cache.iterdir() if path.suffix != ".txt"), 1_000_000)

    def test_real_embedded_text_parser_keeps_page_manifest(self) -> None:
        result = self.service.read_pdf(PDF_URL, MODEL)
        self.assertIn(MODEL, result["text"])
        self.assertEqual(result["page_manifest"][0]["page"], 1)
        self.assertGreater(result["page_manifest"][0]["text_chars"], 50)
        verify_datasheet_receipt(KEY, receipt=result["receipt"], content=result["text"])

    def test_image_only_pdf_can_be_read_and_visually_rendered_with_receipts(self) -> None:
        from PIL import Image, ImageDraw

        # A real scan-style page: all visible content is raster pixels; no PDF text layer.
        image = Image.new("RGB", (600, 800), "white")
        ImageDraw.Draw(image).text((30, 60), "R5-10K-T2-15   Rated AC power: 10000 W", fill="black")
        output = io.BytesIO()
        image.save(output, format="PDF", resolution=72)
        image.close()
        content = output.getvalue()
        self.download.return_value = downloaded(content)
        result = self.service.read_pdf(PDF_URL, MODEL)
        self.assertEqual(result["parse_status"], "needs_visual")
        self.assertEqual(result["parser_metadata"]["extracted_pages"], 0)
        self.assertEqual(result["text"], "")
        self.assertEqual(result["parameter_rows"], [])
        self.assertEqual(result["suggested_review_pages"], [1])
        verify_datasheet_receipt(KEY, receipt=result["receipt"], content="")

        full = self.service.view_pages(result["document_id"], [1])
        cropped = self.service.view_pages(result["document_id"], [1], [0, 0, 1, 0.25])
        for viewed in (full, cropped):
            pixels = base64.b64decode(viewed["images"][0]["data"], validate=True)
            self.assertTrue(pixels.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertLessEqual(len(pixels), datasheets.MAX_IMAGE_BYTES)
            manifest = viewed["manifest"][0]
            self.assertEqual(manifest["sha256"], hashlib.sha256(pixels).hexdigest())
            self.assertLessEqual(manifest["width"] * manifest["height"], datasheets.MAX_IMAGE_PIXELS)
            with Image.open(io.BytesIO(pixels)) as decoded:
                self.assertEqual(decoded.size, (manifest["width"], manifest["height"]))
                # The scan text survives rasterization; this is not an empty placeholder PNG.
                self.assertLess(decoded.convert("L").getextrema()[0], 100)
            signed = verify_datasheet_view_receipt(KEY, receipt=viewed["receipt"])
            self.assertEqual(signed["manifest"], viewed["manifest"])
        self.assertLess(cropped["manifest"][0]["height"], full["manifest"][0]["height"])
        tampered = copy.deepcopy(full["receipt"])
        tampered["manifest"][0]["region"] = [0, 0, 1, 0.25]
        with self.assertRaises(EvidenceReceiptError):
            verify_datasheet_view_receipt(KEY, receipt=tampered)
        with self.assertRaisesRegex(datasheets.DatasheetError, "outside"):
            self.service.view_pages(result["document_id"], [2])


class DatasheetDownloadTests(unittest.TestCase):
    def test_cross_host_https_cdn_redirect_is_pinned_and_fetched_without_pdf_suffix(self) -> None:
        content = minimal_text_pdf("example datasheet")
        responses = iter((
            FakeConnection(FakeResponse(b"", status=302, headers={"Location": CDN_URL})),
            FakeConnection(FakeResponse(content, headers={"Content-Type": "application/pdf"})),
        ))
        connections = []

        def factory(hostname: str, address: str, port: int, timeout: float) -> FakeConnection:
            connections.append((hostname, address))
            return next(responses)

        result = documents.download_pdf(
            PDF_URL, max_bytes=1_000_000, timeout=10, allow_public_redirects=True,
            resolver=public_resolver, connection_factory=factory,
        )
        self.assertEqual(result.final_url, CDN_URL)
        self.assertEqual(result.redirect_chain, (PDF_URL, CDN_URL))
        self.assertEqual(connections, [("maker.example", "93.184.216.34"), ("assets.example", "93.184.216.34")])

    def test_public_cdn_mode_still_blocks_private_redirects(self) -> None:
        first = FakeConnection(FakeResponse(b"", status=302, headers={"Location": CDN_URL}))

        def resolver(hostname: str, port: int, **kwargs: object) -> list[tuple[object, ...]]:
            if hostname == "assets.example":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
            return public_resolver(hostname, port, **kwargs)

        factory = mock.Mock(return_value=first)
        with self.assertRaises(documents.PDFDownloadError):
            documents.download_pdf(PDF_URL, max_bytes=1_000_000, timeout=10, allow_public_redirects=True, resolver=resolver, connection_factory=factory)
        self.assertEqual(factory.call_count, 1)

    def test_public_html_download_reuses_safety_checks_and_rejects_non_html(self) -> None:
        for content_type, succeeds in (("text/html; charset=utf-8", True), ("application/pdf", False)):
            response = FakeConnection(FakeResponse(b"<p>Maker R5</p>", headers={"Content-Type": content_type}))
            with self.subTest(content_type=content_type):
                kwargs = {"resolver": public_resolver, "connection_factory": lambda *_args: response}
                if succeeds:
                    result = documents.download_public_html(PAGE_URL, **kwargs)
                    self.assertEqual(result.content, b"<p>Maker R5</p>")
                else:
                    with self.assertRaises(documents.PDFDownloadError):
                        documents.download_public_html(PAGE_URL, **kwargs)


if __name__ == "__main__":
    unittest.main()
