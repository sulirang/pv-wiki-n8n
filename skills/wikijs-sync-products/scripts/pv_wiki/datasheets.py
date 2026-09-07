"""Official-datasheet snapshots, isolated PDF rendering and signed provenance.

This service performs no LLM calls. The supplied official page is checked for
a real download link where possible; that link alone does not prove that the
website is the product's manufacturer. Callers retain the identity decision.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import math
import multiprocessing
import os
import re
import stat
import tempfile
import threading
from contextlib import closing
from dataclasses import asdict
from html.parser import HTMLParser
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

from . import documents
from .evidence_receipt import (
    EvidenceReceiptError,
    issue_datasheet_receipt,
    issue_datasheet_view_receipt,
    normalize_datasheet_region,
    normalize_evidence_url,
    resolve_evidence_hmac_key,
    verify_datasheet_receipt,
)

PARSER_VERSION = "pv-datasheet-parser-2026-09-07.1"
CACHE_DIR_ENV = "PV_WIKI_DATASHEET_CACHE_DIR"
CACHE_MAX_BYTES_ENV = "PV_WIKI_DATASHEET_CACHE_MAX_BYTES"
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_TEXT_CHARS = 100_000
MAX_VIEW_PAGES = 4
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 4_000_000
MAX_IMAGE_DIMENSION = 2400
_CACHE_JSON_MAX_BYTES = 8 * 1024 * 1024
_CACHE_NAMES = re.compile(r"[0-9a-f]{64}\.(?:pdf|parse\.json)\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_CACHE_LOCK = threading.RLock()


class DatasheetError(ValueError):
    """A datasheet operation failed without changing a published product."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _public_https(value: Any) -> str:
    try:
        url = normalize_evidence_url(value)
    except ValueError as exc:
        raise DatasheetError("datasheet URLs must be public HTTPS URLs") from exc
    if not url.startswith("https://"):
        raise DatasheetError("datasheet URLs must use HTTPS")
    return url


def _document_id(value: Any) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise DatasheetError("document_id must be the 64-character PDF SHA256")
    return value


class _PageLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.text: list[str] = []
        self.base: str | None = None
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"script", "style"}:
            self._hidden += 1
        if tag == "base" and self.base is None and values.get("href"):
            self.base = values["href"]
        if tag in {"a", "area", "link"} and values.get("href"):
            self.links.append(str(values["href"]))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._hidden = max(0, self._hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self._hidden and data.strip():
            self.text.append(data.strip())


def _official_page_evidence(
    official_url: str | None, requested_pdf: str, final_pdf: str,
    target_model: str, downloader: Callable[..., documents.PDFDownload],
) -> dict[str, Any] | None:
    if official_url is None:
        return None
    evidence: dict[str, Any] = {
        "requested_url": official_url, "final_url": official_url,
        "redirect_chain": [official_url], "content_sha256": None,
        "link_status": "unverified", "matched_link": None, "excerpt": "",
    }
    try:
        page = downloader(official_url, max_bytes=2_000_000, timeout=30)
        parser = _PageLinks()
        parser.feed(page.content.decode("utf-8", errors="replace"))
        base_url = urljoin(page.final_url, parser.base or page.final_url)
        links = set()
        for link in parser.links:
            try:
                links.add(_public_https(urljoin(base_url, link)))
            except DatasheetError:
                continue
        text = " ".join(" ".join(parser.text).replace("\x00", "").split())
        location = text.casefold().find(target_model.casefold())
        excerpt = text[:4000] if location < 0 else text[max(0, location - 500):location + 3500]
        matched = next((url for url in (requested_pdf, final_pdf) if url in links), None)
        evidence.update({
            "final_url": page.final_url,
            "redirect_chain": list(page.redirect_chain or (official_url,)),
            "content_sha256": hashlib.sha256(page.content).hexdigest(),
            "link_status": "verified" if matched else "unverified",
            "matched_link": matched, "excerpt": excerpt,
        })
    except (documents.PDFDocumentError, ValueError, OSError):
        # JavaScript-only download pages and unavailable source pages do not
        # erase the PDF. The caller can supply independent page/link evidence.
        pass
    return evidence


def _limit_child(timeout: float) -> None:
    if os.name == "posix":
        import resource
        memory = 768 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        cpu = max(1, math.ceil(timeout))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))


def _render_child(sender: Any, content: bytes, pages: list[int], region: list[float] | None, timeout: float) -> None:
    try:
        _limit_child(timeout)
        import pypdfium2 as pdfium
        from PIL import Image

        images = []
        with pdfium.PdfDocument(content) as pdf:
            page_count = len(pdf)
            if not 1 <= page_count <= MAX_PDF_PAGES:
                raise DatasheetError("PDF exceeds the configured page limit")
            for number in pages:
                if number > page_count:
                    raise DatasheetError("requested page is outside the PDF")
                with closing(pdf[number - 1]) as page:
                    width, height = page.get_size()
                    if not all(math.isfinite(value) and 0 < value <= 14_400 for value in (width, height)):
                        raise DatasheetError("PDF page dimensions exceed the render limit")
                    left, top, right, bottom = region or [0.0, 0.0, 1.0, 1.0]
                    crop_width, crop_height = width * (right - left), height * (bottom - top)
                    scale = min(2.5, MAX_IMAGE_DIMENSION / max(crop_width, crop_height), math.sqrt(MAX_IMAGE_PIXELS / (crop_width * crop_height)))
                    crop = (left * width, (1 - bottom) * height, (1 - right) * width, top * height)
                    with closing(page.render(scale=scale, crop=crop)) as bitmap:
                        pil_image = bitmap.to_pil().convert("RGB")
                        try:
                            for _attempt in range(6):
                                output = io.BytesIO()
                                pil_image.save(output, format="PNG", optimize=False)
                                png = output.getvalue()
                                if len(png) <= MAX_IMAGE_BYTES:
                                    break
                                resized = pil_image.resize(
                                    (max(1, pil_image.width * 3 // 4), max(1, pil_image.height * 3 // 4)),
                                    Image.Resampling.LANCZOS,
                                )
                                pil_image.close()
                                pil_image = resized
                            else:
                                raise DatasheetError("rendered page exceeds the image byte limit")
                            images.append({
                                "page": number, "mime_type": "image/png",
                                "data": base64.b64encode(png).decode("ascii"),
                                "width": pil_image.width, "height": pil_image.height,
                                "region": region, "sha256": hashlib.sha256(png).hexdigest(),
                            })
                        finally:
                            pil_image.close()
        sender.send({"ok": True, "page_count": page_count, "images": images})
    except BaseException as exc:
        sender.send({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
    finally:
        sender.close()


def _run_renderer(content: bytes, pages: list[int], region: list[float] | None, *, timeout: float = 30) -> dict[str, Any]:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_render_child, args=(sender, content, pages, region, timeout), daemon=True)
    try:
        process.start()
        sender.close()
        if not receiver.poll(timeout):
            raise DatasheetError("PDF rendering exceeded its timeout")
        try:
            result = receiver.recv()
        except EOFError as exc:
            raise DatasheetError("PDF renderer exited without a result") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            reason = result.get("error", "unknown error") if isinstance(result, dict) else "invalid result"
            raise DatasheetError(f"PDF rendering failed ({reason})")
        return result
    finally:
        sender.close()
        receiver.close()
        if process.pid is not None:
            process.join(timeout=0.2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            process.close()


class DatasheetService:
    """Fetch and view immutable PDF bytes; HTTP/MCP adapters supply transport."""

    def __init__(
        self, cache_dir: str | Path | None = None, evidence_hmac_key: bytes | None = None,
        *, downloader: Callable[..., documents.PDFDownload] | None = None,
        html_downloader: Callable[..., documents.PDFDownload] | None = None,
        max_cache_bytes: int | None = None,
    ) -> None:
        self.key = resolve_evidence_hmac_key(evidence_hmac_key)
        configured = cache_dir or os.getenv(CACHE_DIR_ENV) or Path(tempfile.gettempdir()) / "pv-wiki-datasheets"
        directory = Path(configured).expanduser().absolute()
        if directory.is_symlink():
            raise DatasheetError("datasheet cache directory must not be a symlink")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.cache_dir = directory.resolve()
        self.max_cache_bytes = int(max_cache_bytes if max_cache_bytes is not None else os.getenv(CACHE_MAX_BYTES_ENV, str(512 * 1024 * 1024)))
        if self.max_cache_bytes < 1_000_000:
            raise DatasheetError("datasheet cache capacity must be at least 1000000 bytes")
        self.downloader = downloader or documents.download_pdf
        self.html_downloader = html_downloader or documents.download_public_html

    def _path(self, name: str) -> Path:
        if _CACHE_NAMES.fullmatch(name) is None:
            raise DatasheetError("invalid datasheet cache name")
        path = self.cache_dir / name
        if path.is_symlink() or path.resolve().parent != self.cache_dir:
            raise DatasheetError("datasheet cache entry escapes its directory")
        return path

    def _read(self, name: str, maximum: int) -> bytes:
        path = self._path(name)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise DatasheetError("document is not cached; call read_pdf first") from exc
        except OSError as exc:
            raise DatasheetError("datasheet cache entry cannot be read") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise DatasheetError("datasheet cache entry is not a bounded regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(maximum + 1)
            if len(data) > maximum:
                raise DatasheetError("datasheet cache entry exceeds its byte limit")
        finally:
            os.close(descriptor)
        os.utime(path, None, follow_symlinks=False)
        return data

    def _write(self, name: str, content: bytes, *, protect: set[str]) -> None:
        if len(content) > self.max_cache_bytes:
            raise DatasheetError("datasheet exceeds the configured cache capacity")
        with _CACHE_LOCK:
            path = self._path(name)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.cache_dir, prefix=".write-", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            entries = []
            for candidate in self.cache_dir.iterdir():
                if _CACHE_NAMES.fullmatch(candidate.name) and not candidate.is_symlink() and candidate.is_file():
                    info = candidate.stat()
                    entries.append((info.st_mtime_ns, candidate, info.st_size))
            size = sum(entry[2] for entry in entries)
            for _mtime, candidate, byte_count in sorted(entries):
                if size <= self.max_cache_bytes:
                    break
                if candidate.name not in protect | {name}:
                    self._path(candidate.name).unlink(missing_ok=True)
                    size -= byte_count
            if size > self.max_cache_bytes:
                path.unlink(missing_ok=True)
                raise DatasheetError("datasheet and parse snapshot exceed the cache capacity")

    def _cache_signature(self, name: str, payload: Any) -> str:
        return hmac.new(self.key, b"pv-wiki/datasheet-cache-v1\x00" + name.encode("ascii") + b"\x00" + _canonical(payload), hashlib.sha256).hexdigest()

    def _parsed(self, document_id: str, content: bytes, target_model: str) -> dict[str, Any]:
        cache_key = hashlib.sha256(_canonical({
            "document_id": document_id, "parser": PARSER_VERSION,
            "layout_contract": documents.PDF_EXTRACTION_CONTRACT_VERSION,
            "pypdf": package_version("pypdf"), "target_model": target_model,
            "max_pages": MAX_PDF_PAGES, "max_chars": MAX_TEXT_CHARS,
        })).hexdigest()
        name = f"{cache_key}.parse.json"
        if self._path(name).exists():
            try:
                cached = json.loads(self._read(name, _CACHE_JSON_MAX_BYTES))
                if not hmac.compare_digest(cached["signature"], self._cache_signature(name, cached["payload"])):
                    raise DatasheetError("parse cache signature is invalid")
                return cached["payload"]
            except (DatasheetError, ValueError, TypeError, KeyError):
                # A damaged/old-key cache is never re-signed as evidence.
                # Recompute from the verified PDF bytes instead.
                pass
        try:
            parsed = documents.parse_pdf_text(
                content, max_pages=MAX_PDF_PAGES, max_chars=MAX_TEXT_CHARS,
                timeout=30, target_models=[target_model], allow_empty_text=True,
            )
            rows = [{"parameter_id": f"p{index:03d}", **asdict(row)} for index, row in enumerate(parsed.parameter_rows, 1)]
            manifest = [dict(item) for item in parsed.page_manifest]
            if not manifest:
                manifest = [{"page": page, "text_chars": 0, "has_parameters": any(row["page"] == page for row in rows)} for page in range(1, parsed.page_count + 1)]
            incomplete = parsed.truncated or any(item["text_chars"] == 0 for item in manifest)
            status = "needs_visual" if parsed.extracted_pages == 0 or not rows else "partial" if incomplete else "parsed"
            payload = {
                "text": parsed.text, "parameter_rows": rows, "page_manifest": manifest,
                "parser_metadata": {
                    "contract_version": PARSER_VERSION, "page_count": parsed.page_count,
                    "extracted_pages": parsed.extracted_pages, "truncated": parsed.truncated,
                    "parse_status": status,
                },
            }
        except documents.PDFParseError:
            # PDFium may open a valid PDF whose text parser cannot. Keep the
            # actual artifact renderable without inventing any parsed rows.
            inspected = _run_renderer(content, [], None)
            count = inspected["page_count"]
            payload = {
                "text": "", "parameter_rows": [],
                "page_manifest": [{"page": page, "text_chars": 0, "has_parameters": False} for page in range(1, count + 1)],
                "parser_metadata": {"contract_version": PARSER_VERSION, "page_count": count, "extracted_pages": 0, "truncated": False, "parse_status": "needs_visual"},
            }
        cached_bytes = _canonical({"payload": payload, "signature": self._cache_signature(name, payload)})
        if len(cached_bytes) > _CACHE_JSON_MAX_BYTES:
            raise DatasheetError("parsed datasheet exceeds its snapshot limit")
        self._write(name, cached_bytes, protect={f"{document_id}.pdf"})
        return payload

    def read_pdf(self, url: str, target_model: str, official_page_url: str | None = None) -> dict[str, Any]:
        requested = _public_https(url)
        official_url = None if official_page_url is None else _public_https(official_page_url)
        if not isinstance(target_model, str) or not target_model.strip() or len(target_model) > 200 or "\x00" in target_model:
            raise DatasheetError("target_model must contain 1 to 200 characters")
        target_model = " ".join(target_model.split())
        downloaded = self.downloader(requested, max_bytes=MAX_PDF_BYTES, timeout=30, allow_public_redirects=True)
        content = downloaded.content
        if not isinstance(content, bytes) or len(content) > MAX_PDF_BYTES or documents.PDF_MAGIC not in content[:1024]:
            raise DatasheetError("downloaded PDF has invalid bytes or exceeds its limit")
        document_id = hashlib.sha256(content).hexdigest()
        if downloaded.sha256 != document_id:
            raise DatasheetError("downloaded PDF hash differs from its bytes")
        self._write(f"{document_id}.pdf", content, protect=set())
        parsed = self._parsed(document_id, content, target_model)
        source = _official_page_evidence(official_url, requested, downloaded.final_url, target_model, self.html_downloader)
        receipt = issue_datasheet_receipt(
            self.key, content=parsed["text"], document_id=document_id,
            artifact_sha256=document_id, requested_url=requested, url=downloaded.final_url,
            official_page_url=official_url, official_page_evidence=source,
            redirect_chain=list(downloaded.redirect_chain or (requested,)),
            target_model=target_model, parameter_rows=parsed["parameter_rows"],
            parser_metadata=parsed["parser_metadata"], page_manifest=parsed["page_manifest"],
        )
        verified = verify_datasheet_receipt(self.key, receipt=receipt, content=parsed["text"])
        # Return normalized rows exactly as signed, so callers can copy them.
        review_pages = [item["page"] for item in verified["page_manifest"] if item["has_parameters"] or item["text_chars"] == 0]
        if not review_pages:
            review_pages = list(range(1, verified["parser_metadata"]["page_count"] + 1))
        return {
            **{key: value for key, value in verified.items() if key != "version"},
            "page_count": verified["parser_metadata"]["page_count"],
            "parse_status": verified["parser_metadata"]["parse_status"],
            "text": parsed["text"], "content": parsed["text"],
            "suggested_review_pages": review_pages, "receipt": receipt,
        }

    def view_pages(self, document_id: str, pages: list[int], region: list[float] | None = None) -> dict[str, Any]:
        document_id = _document_id(document_id)
        if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_VIEW_PAGES or any(
            isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= MAX_PDF_PAGES for page in pages
        ) or len(set(pages)) != len(pages):
            raise DatasheetError("pages must contain one to four unique 1-based page numbers")
        try:
            normalized_region = normalize_datasheet_region(region)
        except EvidenceReceiptError as exc:
            raise DatasheetError(str(exc)) from exc
        content = self._read(f"{document_id}.pdf", MAX_PDF_BYTES)
        if hashlib.sha256(content).hexdigest() != document_id:
            raise DatasheetError("cached PDF hash differs from document_id; fetch it again")
        rendered = _run_renderer(content, pages, normalized_region)
        images = rendered["images"]
        manifest = [{key: value for key, value in image.items() if key != "data"} for image in images]
        receipt = issue_datasheet_view_receipt(self.key, document_id=document_id, manifest=manifest)
        return {"document_id": document_id, "page_count": rendered["page_count"], "images": images, "manifest": manifest, "receipt": receipt}


__all__ = ["DatasheetService", "DatasheetError", "PARSER_VERSION", "CACHE_DIR_ENV", "CACHE_MAX_BYTES_ENV", "MAX_VIEW_PAGES"]
