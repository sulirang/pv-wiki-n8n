"""Document-centred schema-four decisions, independent of legacy fact gates.

Receipts establish which PDF and pages were actually fetched. Manufacturer
discovery is an explicit research judgement, not a maintained brand registry.
Only parameters attributed to the selected model enter its published page.
"""

from __future__ import annotations

import copy
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from . import evidence_receipt as receipts
from .config import trusted_source_domains_for_product
from .decision import model_matches_catalogue_identity
from .parameter_analysis import (
    ANALYSIS_SECTION_TITLES,
    apply_parameter_translations,
    validate_parameter_enrichment,
)
from .render import validate_public_http_url


class DatasheetDecisionError(ValueError):
    """A document decision cannot safely be attributed to this product."""


def eligible_brand(product: Mapping[str, Any]) -> bool:
    value = product.get("brand_code")
    return isinstance(value, str) and bool(value.strip())


def _text(value: Any, name: str, *, required: bool = False, maximum: int = 1000) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise DatasheetDecisionError(f"{name} must be text")
    value = value.strip()
    if len(value) > maximum or required and not value:
        raise DatasheetDecisionError(f"{name} is empty or too long")
    return value


def _model_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _array(value: Any, name: str, *, maximum: int = 500) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise DatasheetDecisionError(f"{name} must be an array of at most {maximum} items")
    return value


def _object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DatasheetDecisionError(f"{name} must be an object")
    return value


def _value(value: Any, name: str) -> str | int | float:
    if isinstance(value, bool) or value is None:
        raise DatasheetDecisionError(f"{name} must be an explicit value")
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise DatasheetDecisionError(f"{name} must be finite")
        return value
    return _text(value, name, required=True)


def _url(value: Any, name: str) -> str:
    try:
        return validate_public_http_url(_text(value, name, required=True, maximum=4000))
    except ValueError as exc:
        raise DatasheetDecisionError(f"{name} must be a public HTTP(S) URL") from exc


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise DatasheetDecisionError(f"{name} must be a SHA-256 digest")
    return value


def _notes(value: Any, name: str) -> str | list[str]:
    if isinstance(value, list):
        return [_text(item, name, maximum=4000) for item in _array(value, name, maximum=100)]
    return _text(value, name, maximum=4000)


def _document_evidence(
    evidence_documents: Any, evidence_hmac_key: bytes,
) -> list[dict[str, Any]]:
    documents = _array(evidence_documents, "evidence_documents", maximum=20)
    verified: list[dict[str, Any]] = []
    for index, raw in enumerate(documents):
        item = _object(raw, f"evidence_documents[{index}]")
        receipt = item.get("receipt")
        if not isinstance(receipt, Mapping) or receipt.get("version") != "pvwiki-datasheet-v1":
            continue
        try:
            payload = receipts.verify_datasheet_receipt(
                evidence_hmac_key, receipt=receipt, content=item.get("content"),
            )
        except (ValueError, TypeError, receipts.EvidenceReceiptError) as exc:
            raise DatasheetDecisionError("datasheet receipt is invalid") from exc
        if item.get("url") is not None and _url(item["url"], "evidence URL") != payload["url"]:
            raise DatasheetDecisionError("evidence URL does not match its signed PDF")
        verified.append(dict(payload))
    if not verified:
        raise DatasheetDecisionError("schema 4 requires a signed datasheet document")
    return verified


def _visual_review(value: Any, document_id: str, page_count: int, key: bytes) -> dict[str, Any]:
    raw = {} if value is None else _object(value, "visual_review")
    status = raw.get("status", "text_only")
    if status == "not_available":
        status = "unavailable"
    if status not in {"reviewed", "unavailable", "failed", "text_only", "partial", "not_needed"}:
        raise DatasheetDecisionError("visual_review.status is invalid")
    view_receipts = raw.get("receipts", [])
    if raw.get("receipt") is not None:
        view_receipts = [*view_receipts, raw["receipt"]] if isinstance(view_receipts, list) else view_receipts
    pages: set[int] = set()
    verified_receipts: list[dict[str, Any]] = []
    for receipt in _array(view_receipts, "visual_review.receipts", maximum=500):
        try:
            verified = receipts.verify_datasheet_view_receipt(key, receipt=receipt)
        except (ValueError, TypeError, receipts.EvidenceReceiptError) as exc:
            raise DatasheetDecisionError("visual page receipt is invalid") from exc
        if verified.get("document_id") != document_id:
            raise DatasheetDecisionError("visual review belongs to a different PDF")
        for image in verified.get("manifest", []):
            page = image.get("page")
            if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= page_count:
                raise DatasheetDecisionError("visual review page is outside the PDF")
            pages.add(page)
        verified_receipts.append(dict(receipt))
    # A receipt proves delivery of page images, not the model's judgement.
    # Do not label unviewed pages as visually checked on the strength of prose.
    if status == "reviewed" and not pages:
        status = "failed"
    return {"status": status, "pages": sorted(pages), "receipts": verified_receipts}


def _parameter_rows(
    value: Any, *, model: str, document: Mapping[str, Any], visual: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    document_id = document["document_id"]
    page_count = document["parser_metadata"]["page_count"]
    reviewed_pages = set(visual["pages"])
    for index, raw in enumerate(_array(value, "parameters")):
        item = _object(raw, f"parameters[{index}]")
        status = item.get("status", "confirmed")
        if status in {"uncertain", "unresolved", "omitted"}:
            omissions.append({
                "source_label": str(item.get("source_label", ""))[:200],
                "reason": str(item.get("reason", "parameter attribution is uncertain"))[:1000],
            })
            continue
        if status != "confirmed":
            raise DatasheetDecisionError("parameter status must be confirmed or uncertain")
        prefix = f"parameters[{index}]"
        page = item.get("page")
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= page_count:
            raise DatasheetDecisionError(f"{prefix}.page must locate a page in the PDF")
        row_document_id = item.get("document_sha256", item.get("document_id", document_id))
        if row_document_id != document_id:
            raise DatasheetDecisionError(f"{prefix} belongs to another PDF")
        row_model = _text(item.get("model", model), f"{prefix}.model", required=True, maximum=300)
        scope = item.get("scope", "shared" if item.get("value_state") == "merged_shared" else "model")
        if scope not in {"model", "shared"}:
            raise DatasheetDecisionError(f"{prefix}.scope must be model or shared")
        if scope == "shared" and "applies_to_models" not in item:
            raise DatasheetDecisionError(f"{prefix}.shared scope requires explicit applies_to_models")
        applies = item.get("applies_to_models", [row_model])
        applies = [_text(entry, f"{prefix}.applies_to_models", required=True, maximum=300)
                   for entry in _array(applies, f"{prefix}.applies_to_models", maximum=100)]
        if _model_key(row_model) != _model_key(model) or _model_key(model) not in {_model_key(entry) for entry in applies}:
            raise DatasheetDecisionError(f"{prefix} does not belong to the selected model")
        source_label = _text(item.get("source_label", item.get("name")), f"{prefix}.source_label", required=True, maximum=200)
        source_kind = item.get("source_kind", "visual" if item.get("correction") else "parser")
        if source_kind not in {"parser", "visual"}:
            raise DatasheetDecisionError(f"{prefix}.source_kind must be parser or visual")
        if source_kind == "visual" and page not in reviewed_pages:
            raise DatasheetDecisionError("visually recovered parameters require a receipt for their PDF page")
        if source_kind == "parser" and document["parser_metadata"]["extracted_pages"] == 0:
            raise DatasheetDecisionError("an image-only PDF requires visual evidence for recovered parameters")
        if item.get("correction") is not None:
            source_kind = "visual"
        row: dict[str, Any] = {
            "parameter_id": f"p{len(rows) + 1:03d}",
            "source_parameter_id": _text(item.get("parameter_id"), f"{prefix}.parameter_id", maximum=100),
            "source_label": source_label,
            "value": _value(item.get("value"), f"{prefix}.value"),
            "unit": _text(item.get("unit"), f"{prefix}.unit", maximum=80),
            "section": _text(item.get("section"), f"{prefix}.section", maximum=100),
            "subsection": _text(item.get("subsection"), f"{prefix}.subsection", maximum=100),
            "model": model, "scope": scope, "applies_to_models": applies,
            "page": page, "document_sha256": document_id,
            "source_url": document["url"], "status": "confirmed",
            "source_kind": source_kind,
            "conditions": _notes(item.get("conditions"), f"{prefix}.conditions"),
            "footnotes": _notes(item.get("footnotes"), f"{prefix}.footnotes"),
            "table_title": _text(item.get("table_title"), f"{prefix}.table_title", maximum=300),
            "column_header": _text(item.get("column_header", item.get("model_quote")), f"{prefix}.column_header"),
        }
        correction = item.get("correction")
        if correction is not None:
            if page not in reviewed_pages:
                raise DatasheetDecisionError("visual corrections require a receipt for their PDF page")
            correction = _object(correction, f"{prefix}.correction")
            before = _object(correction.get("before"), "correction.before")
            after = _object(correction.get("after"), "correction.after")
            after_value = _value(after.get("value"), "correction.after.value")
            after_unit = _text(after.get("unit", row["unit"]), "correction.after.unit", maximum=80)
            if str(after_value) != str(row["value"]) or after_unit != row["unit"]:
                raise DatasheetDecisionError("correction.after must equal the final parameter")
            row["correction"] = {
                "before": {"value": None if before.get("value") is None else _value(before.get("value"), "correction.before.value"), "unit": _text(before.get("unit"), "correction.before.unit", maximum=80)},
                "after": {"value": after_value, "unit": after_unit},
                "reason": _text(correction.get("reason"), "correction.reason", required=True),
            }
        rows.append(row)
    if not rows:
        raise DatasheetDecisionError("publish requires at least one confirmed target-model parameter")
    required_pages = {row["page"] for row in rows} | {
        item["page"] for item in document.get("page_manifest", [])
        if item.get("has_parameters") or item.get("text_chars") == 0
    }
    if visual["status"] == "reviewed" and not required_pages <= reviewed_pages:
        visual["status"] = "partial"
    return rows, omissions


def analysis_input(parameters: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{"parameter_id": row["parameter_id"], "name": row["source_label"],
             "section": row.get("section", ""), "subsection": row.get("subsection", ""),
             "value": row["value"], "unit": row.get("unit", "")}
            for row in parameters]


def normalize_optional_analysis(
    raw: Any, parameters: Sequence[Mapping[str, Any]],
    *, include_product_analysis: bool = True,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Accept partial model translations and analysis without numeric gates.

    Invalid optional items are recorded individually; they never discard
    confirmed datasheet parameters or otherwise valid analysis paragraphs.
    """
    if raw is None:
        return None, []
    rejected: list[dict[str, str]] = []
    parameters_by_id = {row["parameter_id"]: row for row in parameters}
    allowed_ids = set(parameters_by_id)
    def reject(path: str, exc: BaseException) -> None:
        rejected.append({"item": path, "reason": str(exc)[:300]})
    try:
        raw = _object(raw, "analysis")
    except DatasheetDecisionError as exc:
        return None, [{"item": "analysis", "reason": str(exc)}]
    result: dict[str, Any] = {"translations": [], "sections": [], "overall_limitations_zh": []}
    try:
        translations = _array(raw.get("translations", []), "analysis.translations", maximum=500)
    except DatasheetDecisionError as exc:
        translations = []
        reject("translations", exc)
    translated: set[str] = set()
    for index, value in enumerate(translations):
        path = f"translations[{index}]"
        try:
            item = _object(value, path)
            parameter_id = item.get("parameter_id")
            if not isinstance(parameter_id, str) or parameter_id not in allowed_ids or parameter_id in translated:
                raise DatasheetDecisionError("translation must name one known, nonduplicate parameter ID")
        except DatasheetDecisionError as exc:
            reject(path, exc)
            continue
        normalized: dict[str, Any] = {"parameter_id": parameter_id}
        # Display wording is separate from the immutable source parameters. A
        # malformed optional field must not remove its sibling translations.
        for field, maximum in (("name_zh", 500), ("section_zh", 300),
                               ("subsection_zh", 300), ("value_zh", 4000),
                               ("conditions_zh", 4000)):
            if field not in item:
                continue
            try:
                text = _text(item[field], f"{path}.{field}", maximum=maximum)
                if text:
                    normalized[field] = text
            except DatasheetDecisionError as exc:
                reject(f"{path}.{field}", exc)
        if "footnotes_zh" in item:
            notes_path = f"{path}.footnotes_zh"
            source_notes = parameters_by_id[parameter_id].get("footnotes", [])
            if isinstance(source_notes, str):
                source_notes = [source_notes] if source_notes.strip() else []
            try:
                notes = _array(item["footnotes_zh"], notes_path, maximum=100)
                if len(notes) != len(source_notes):
                    raise DatasheetDecisionError(f"{notes_path} must match source footnote positions")
            except DatasheetDecisionError as exc:
                reject(notes_path, exc)
            else:
                translated_notes: list[str] = []
                for number, note in enumerate(notes):
                    try:
                        translated_notes.append(_text(note, f"{notes_path}[{number}]", maximum=4000))
                    except DatasheetDecisionError as exc:
                        translated_notes.append("")
                        reject(f"{notes_path}[{number}]", exc)
                normalized["footnotes_zh"] = translated_notes
        if len(normalized) > 1:
            result["translations"].append(normalized)
        translated.add(parameter_id)
    if not include_product_analysis:
        return result if result["translations"] else None, rejected
    try:
        sections = _array(raw.get("sections", []), "analysis.sections", maximum=30)
    except DatasheetDecisionError as exc:
        sections = []
        reject("sections", exc)
    seen_sections: set[str] = set()
    for index, value in enumerate(sections):
        path = f"sections[{index}]"
        try:
            section = _object(value, path)
            code = section.get("section_code")
            if not isinstance(code, str) or code not in ANALYSIS_SECTION_TITLES or code in seen_sections:
                raise DatasheetDecisionError("analysis section must use a unique supported section_code")
            paragraphs = _array(section.get("paragraphs", []), f"{path}.paragraphs", maximum=100)
        except DatasheetDecisionError as exc:
            reject(path, exc)
            continue
        accepted = []
        for number, value in enumerate(paragraphs):
            paragraph_path = f"{path}.paragraphs[{number}]"
            try:
                item = _object(value, paragraph_path)
                ids = _array(item.get("basis_parameter_ids", []), "basis_parameter_ids", maximum=500)
                if not ids or any(not isinstance(parameter_id, str) or parameter_id not in allowed_ids for parameter_id in ids):
                    raise DatasheetDecisionError("analysis basis must reference final published parameters")
                accepted.append({
                    "analysis_zh": _text(item.get("analysis_zh"), "analysis_zh", required=True, maximum=4000),
                    "basis_parameter_ids": list(dict.fromkeys(ids)),
                    "conditions_zh": [_text(note, "conditions_zh", maximum=2000)
                                      for note in _array(item.get("conditions_zh", []), "conditions_zh", maximum=100)],
                    "limitations_zh": [_text(note, "limitations_zh", maximum=2000)
                                       for note in _array(item.get("limitations_zh", []), "limitations_zh", maximum=100)],
                })
            except DatasheetDecisionError as exc:
                reject(paragraph_path, exc)
        if accepted:
            result["sections"].append({"section_code": code, "paragraphs": accepted})
            seen_sections.add(code)
    try:
        limits = _array(raw.get("overall_limitations_zh", []), "overall_limitations_zh", maximum=100)
    except DatasheetDecisionError as exc:
        limits = []
        reject("overall_limitations_zh", exc)
    for index, item in enumerate(limits):
        try:
            result["overall_limitations_zh"].append(_text(item, "overall_limitations_zh", required=True, maximum=2000))
        except DatasheetDecisionError as exc:
            reject(f"overall_limitations_zh[{index}]", exc)
    return result if any(result.values()) else None, rejected


def validate_datasheet_decision(
    decision: Any, *, product_id: str, product: Mapping[str, Any],
    evidence_documents: Any, evidence_hmac_key: bytes,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    decision = _object(decision, "decision")
    if decision.get("schema_version") != "4" or decision.get("outcome") != "publish":
        raise DatasheetDecisionError("schema 4 stores confirmed datasheet publications only")
    if decision.get("product_id") != product_id:
        raise DatasheetDecisionError("decision.product_id must match the selected product")
    if not eligible_brand(product):
        raise DatasheetDecisionError("products with an empty brand_code are not researched")
    model = _text(decision.get("model"), "model", required=True, maximum=300)
    if not model_matches_catalogue_identity(model, product_id=product_id,
                                           product_name=str(product.get("product_name") or "")):
        raise DatasheetDecisionError("datasheet model does not match the catalogue product")
    source = _object(decision.get("official_source"), "official_source")
    source_url = _url(source.get("url"), "official_source.url")
    document_id = _digest(source.get("document_sha256", source.get("document_id", source.get("artifact_sha256"))), "official_source.document_sha256")
    documents = _document_evidence(evidence_documents, evidence_hmac_key)
    matching = [doc for doc in documents if doc.get("document_id") == document_id and doc.get("url") == source_url]
    if not matching:
        raise DatasheetDecisionError("official_source must match a fetched signed PDF")
    document = matching[0]
    if _model_key(str(document.get("target_model", ""))) != _model_key(model):
        raise DatasheetDecisionError("PDF was fetched for a different model")
    official_page = source.get("official_page_url") or document.get("official_page_url")
    if official_page:
        official_page = _url(official_page, "official_source.official_page_url")
        if official_page != document.get("official_page_url"):
            raise DatasheetDecisionError("official page does not match the signed download context")
        page_host = urlsplit(official_page).hostname
        pdf_host = urlsplit(source_url).hostname
        if page_host != pdf_host:
            link_evidence = document.get("official_page_evidence") or {}
            # A signed PDF served directly by a configured manufacturer host
            # does not need a second official page to link to it. Resolve only
            # from the catalogue brand, never the model's manufacturer claim,
            # and compare the final PDF host exactly (not a redirect origin).
            official_hosts = trusted_source_domains_for_product(
                product.get("brand_code"), None,
            )
            if (pdf_host not in official_hosts
                    and link_evidence.get("link_status") != "verified"):
                raise DatasheetDecisionError("cross-host PDF requires a verified official-page download link")
    visual = _visual_review(decision.get("visual_review"), document_id,
                            document["parser_metadata"]["page_count"], evidence_hmac_key)
    rows, omissions = _parameter_rows(decision.get("parameters", []), model=model,
                                     document=document, visual=visual)
    normalized: dict[str, Any] = {
        "schema_version": "4", "product_id": product_id, "outcome": "publish",
        "manufacturer": _text(decision.get("manufacturer") or product["brand_code"], "manufacturer", required=True, maximum=200),
        "model": model,
        "official_source": {"url": source_url, "document_sha256": document_id,
                            "official_page_url": official_page,
                            "verification_reason": _text(source.get("verification_reason"), "verification_reason", required=True),
                            "official_page_evidence": document.get("official_page_evidence")},
        "parameters": rows, "visual_review": visual,
        "omissions": [*omissions, *_array(decision.get("omissions", []), "omissions")],
        "datasheets": [{"url": source_url, "title": str(source.get("title") or "Manufacturer datasheet")[:300],
                        "source_type": "manufacturer", "is_primary": True}],
        "sources": [], "facts": [], "conflicts": [], "derived_insights": [],
        "review_summary": "", "review_evidence_urls": [], "conclusion_evidence": [],
        "parameter_enrichment": None, "analysis_status": "unavailable",
    }
    for field in ("product_category_code", "product_category", "product_type", "product_description_zh",
                  "manufacturer_zh", "summary", "decision_notes"):
        normalized[field] = _text(decision.get(field), field, maximum=1000)
    for field, maximum in (("datasheet_language", 80), ("language_selection_reason", 1000)):
        if field in source:
            try:
                language_metadata = _text(source[field], f"official_source.{field}", maximum=maximum)
                if language_metadata:
                    normalized["official_source"][field] = language_metadata
            except DatasheetDecisionError as exc:
                normalized["omissions"].append({"source_label": f"official_source.{field}",
                                               "reason": str(exc)[:300]})
    enrichment = decision.get("parameter_enrichment")
    if enrichment is not None:
        try:
            normalized["parameter_enrichment"] = validate_parameter_enrichment(enrichment, analysis_input(rows))
            normalized["analysis_status"] = "complete"
        except (ValueError, TypeError, KeyError) as exc:
            normalized["analysis_error"] = str(exc)[:300]
    normalized["analysis"], normalized["analysis_omissions"] = normalize_optional_analysis(decision.get("analysis"), rows)
    if normalized["analysis"] is not None:
        normalized["analysis_status"] = "partial" if normalized["analysis_omissions"] else "complete"
    # Hash the final parameters, including visual changes, not a parser array.
    import hashlib
    import json
    normalized["parameter_set_sha256"] = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    evidence = [{key: copy.deepcopy(value) for key, value in document.items()
                 if key not in {"content", "text", "signature"}}]
    return normalized, evidence


def render_datasheet_decision(decision: Mapping[str, Any]) -> dict[str, Any]:
    rendered = dict(decision)
    rendered["_render_schema"] = 4
    # Product comparisons belong to a later, separate workflow. Keep historical
    # analysis in storage, but render only its parameter translations here.
    rendered.pop("professional_analysis", None)
    if "\ufffd" in str(rendered.get("product_description_zh", "")):
        rendered["product_description_zh"] = " ".join(
            str(decision.get(field) or "").strip() for field in ("manufacturer", "model")
            if decision.get(field)
        )
    parameters = analysis_input(decision.get("parameters", []))
    displayed = [dict(row) for row in parameters]
    translated = False
    translation_omissions = [copy.deepcopy(item) for item in (decision.get("analysis_omissions") or [])
                            if isinstance(item, Mapping)
                            and (str(item.get("item", "")).startswith("translations")
                                 or item.get("item") == "analysis")]
    enrichment = decision.get("parameter_enrichment")
    if enrichment is not None:
        try:
            displayed = apply_parameter_translations(parameters, enrichment)
            translated = any(row.get("name_zh") for row in displayed)
        except (ValueError, TypeError, KeyError) as exc:
            translation_omissions.append({"item": "parameter_enrichment.translations", "reason": str(exc)[:300]})
    optional, rejected = normalize_optional_analysis(
        decision.get("analysis"), decision.get("parameters", []), include_product_analysis=False,
    )
    translation_omissions.extend(item for item in rejected if item not in translation_omissions)
    if optional is not None:
        by_id = {item["parameter_id"]: item for item in optional["translations"]}
        for row in displayed:
            translation = by_id.get(row["parameter_id"])
            if translation:
                row.update({key: value for key, value in translation.items() if key != "parameter_id"})
        translated = translated or bool(by_id)
    for display, original in zip(displayed, decision.get("parameters", [])):
        for field in ("model", "scope", "applies_to_models", "page", "source_url", "document_sha256",
                      "table_title", "column_header", "correction", "conditions", "footnotes", "source_kind"):
            if field in original:
                display[field] = copy.deepcopy(original[field])
    rendered["datasheet_parameters"] = displayed
    rendered["translation_status"] = ("partial" if translation_omissions else "complete") if translated else "unavailable"
    rendered["translation_omissions"] = translation_omissions
    return rendered
