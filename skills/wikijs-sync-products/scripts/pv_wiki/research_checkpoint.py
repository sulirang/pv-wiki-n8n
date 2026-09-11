"""Bounded, untrusted n8n progress stored beside accepted research.

These records are recovery inputs, never completion records or publication
authority. Research, page selection and model calls remain in n8n.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from .catalogue_snapshot import CATALOGUE_TABLE
from .datasheet_decision import eligible_brand
from .hermes_store import COMPLETION_TABLE, RERESEARCH_TABLE, ResearchProduct


CHECKPOINT_TABLE = "product_research_checkpoints"
MAX_CONTEXT_BYTES = 4 * 1024 * 1024
STAGES = frozenset({"pdf_ready", "draft", "reviewed"})
_FORBIDDEN_KEYS = frozenset({
    "apikey", "authorization", "password", "passwd", "cookie", "setcookie",
    "leasetoken", "accesstoken", "refreshtoken", "clientsecret", "privatekey",
    "credential", "credentials", "secrets", "binary", "imagedata", "imagebase64",
    "openaiapikey", "exaapikey", "authheaders",
    "token", "secret", "base64", "evidencehmackey", "encryptionkey",
})
_CREATE = f"""CREATE TABLE IF NOT EXISTS {CHECKPOINT_TABLE} (
    product_id TEXT PRIMARY KEY,
    source_hash TEXT NOT NULL,
    revision TEXT NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN ('pdf_ready', 'draft', 'reviewed')),
    context_json TEXT NOT NULL,
    context_bytes INTEGER NOT NULL CHECK (context_bytes > 0 AND context_bytes <= {MAX_CONTEXT_BYTES}),
    updated_at TEXT NOT NULL
)"""


def encode_context(context: Any) -> str:
    if not isinstance(context, dict):
        raise ValueError("checkpoint context must be a JSON object")
    visited = 0

    def inspect(value: Any, depth: int = 0) -> None:
        nonlocal visited
        visited += 1
        if depth > 32 or visited > 200000:
            raise ValueError("checkpoint context is too complex")
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError("checkpoint object keys must be strings")
                normalized = "".join(character for character in key.casefold() if character.isalnum())
                if normalized in _FORBIDDEN_KEYS or normalized.endswith("apikey"):
                    raise ValueError("checkpoint must not contain credentials, lease tokens or binary images")
                inspect(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                inspect(item, depth + 1)
        elif isinstance(value, str):
            if ("data:image/" in value.casefold()
                    or value.startswith(("-----BEGIN PRIVATE KEY", "-----BEGIN RSA PRIVATE KEY"))
                    or (len(value) > 256 and value.startswith(("iVBORw0KGgo", "/9j/", "R0lGOD")))):
                raise ValueError("checkpoint must not contain credentials or binary images")
        elif value is None or isinstance(value, (bool, int)):
            return
        elif isinstance(value, float) and math.isfinite(value):
            return
        else:
            raise ValueError("checkpoint context must contain only finite JSON values")

    inspect(context)
    serialized = json.dumps(context, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise ValueError("checkpoint context exceeds 4 MiB")
    return serialized


class ResearchCheckpointStore:
    """Reuse the configured completion store's SQLite/PostgreSQL transaction API."""

    def __init__(self, completion_store: Any):
        self._state = completion_store._state
        with self._state._write_transaction() as connection:
            connection.execute(_CREATE)
            connection.execute(f"CREATE INDEX IF NOT EXISTS product_research_checkpoints_updated_idx ON {CHECKPOINT_TABLE}(updated_at, product_id)")

    @staticmethod
    def _current(connection: Any, product_id: str) -> ResearchProduct | None:
        row = connection.execute(f"""
            SELECT p.product_id, p.source_hash, p.payload_json,
                   refresh.revision AS refresh_revision, completed.product_id AS completed_id
            FROM {CATALOGUE_TABLE} AS p
            LEFT JOIN {RERESEARCH_TABLE} AS refresh
              ON refresh.product_id = p.product_id AND refresh.status = 'pending'
            LEFT JOIN {COMPLETION_TABLE} AS completed ON completed.product_id = p.product_id
            WHERE p.product_id = ?
        """, (product_id,)).fetchone()
        if row is None or (row["completed_id"] is not None and row["refresh_revision"] is None):
            return None
        payload = json.loads(row["payload_json"])
        if not eligible_brand(payload):
            return None
        return ResearchProduct(row["product_id"], row["source_hash"], payload,
                               row["refresh_revision"] or f"new:{row['source_hash']}")

    @staticmethod
    def _payload(row: Any, *, include_context: bool) -> dict[str, Any]:
        result = {key: row[key] for key in (
            "product_id", "source_hash", "revision", "stage", "updated_at", "context_bytes",
        )}
        result["requires_revalidation"] = True
        if include_context:
            result["context"] = json.loads(row["context_json"])
        return result

    def save(self, *, product_id: str, source_hash: str, revision: str,
             stage: Any, context: Any) -> dict[str, Any]:
        if not isinstance(stage, str) or stage not in STAGES:
            raise ValueError("checkpoint stage must be pdf_ready, draft or reviewed")
        serialized = encode_context(context)
        now = datetime.now(timezone.utc).isoformat()
        with self._state._write_transaction() as connection:
            current = self._current(connection, product_id)
            if current is None or current.source_hash != source_hash or current.revision != revision:
                raise ValueError("checkpoint source or revision changed; claim fresh research")
            connection.execute(f"""INSERT INTO {CHECKPOINT_TABLE}
                (product_id, source_hash, revision, stage, context_json, context_bytes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (product_id) DO UPDATE SET source_hash = excluded.source_hash,
                    revision = excluded.revision, stage = excluded.stage,
                    context_json = excluded.context_json, context_bytes = excluded.context_bytes,
                    updated_at = excluded.updated_at
            """, (product_id, source_hash, revision, stage, serialized,
                    len(serialized.encode("utf-8")), now))
            row = connection.execute(f"SELECT * FROM {CHECKPOINT_TABLE} WHERE product_id = ?", (product_id,)).fetchone()
        return self._payload(row, include_context=False)

    def resumable(self) -> tuple[ResearchProduct, dict[str, Any]] | None:
        with self._state._connection() as connection:
            # Do not materialize every multi-megabyte draft while selecting
            # one product. Fetch its context only after its revision matches.
            rows = connection.execute(f"SELECT product_id, source_hash, revision FROM {CHECKPOINT_TABLE} ORDER BY updated_at, product_id").fetchall()
            for row in rows:
                product = self._current(connection, row["product_id"])
                if product is not None and product.source_hash == row["source_hash"] and product.revision == row["revision"]:
                    checkpoint = connection.execute(f"SELECT * FROM {CHECKPOINT_TABLE} WHERE product_id = ?",
                                                    (product.product_id,)).fetchone()
                    if checkpoint is not None and checkpoint["source_hash"] == product.source_hash and checkpoint["revision"] == product.revision:
                        return product, self._payload(checkpoint, include_context=True)
        return None

    def summary(self, product_id: str | None = None) -> dict[str, Any] | None:
        with self._state._connection() as connection:
            if product_id is None:
                row = connection.execute(f"SELECT * FROM {CHECKPOINT_TABLE} ORDER BY updated_at DESC, product_id LIMIT 1").fetchone()
            else:
                row = connection.execute(f"SELECT * FROM {CHECKPOINT_TABLE} WHERE product_id = ?", (product_id,)).fetchone()
            if row is None:
                return None
            current = self._current(connection, row["product_id"])
            summary = self._payload(row, include_context=False)
            summary["resumable"] = bool(current is not None and current.source_hash == row["source_hash"] and current.revision == row["revision"])
            return summary

    def clear(self, *, product_id: str, source_hash: str, revision: str | None) -> None:
        with self._state._write_transaction() as connection:
            connection.execute(f"DELETE FROM {CHECKPOINT_TABLE} WHERE product_id = ? AND source_hash = ? AND revision = ?",
                               (product_id, source_hash, revision))

    def latest_completed_id(self) -> str | None:
        with self._state._connection() as connection:
            row = connection.execute(f"SELECT product_id FROM {COMPLETION_TABLE} ORDER BY completed_at DESC, product_id LIMIT 1").fetchone()
        return str(row["product_id"]) if row is not None else None
