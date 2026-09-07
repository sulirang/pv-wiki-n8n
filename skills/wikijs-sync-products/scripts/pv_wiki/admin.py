"""Completion-only administration for publication control and rerendering."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from .hermes_store import (
    HermesCompletionStore,
    _json_default,
    _safe_cli_error,
    rerender_completion,
)


def _print(value: Any, *, error: bool = False) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=_json_default,
        ),
        file=sys.stderr if error else sys.stdout,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pv-wiki-admin",
        description=(
            "Administer immutable Hermes completions without triggering research."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    rerender = commands.add_parser(
        "rerender",
        help="preview a completion-only rerender; --apply is explicit",
    )
    rerender.add_argument("--product-id", required=True)
    rerender.add_argument(
        "--apply",
        action="store_true",
        help="apply the rerender to an existing exact Wiki page",
    )

    for action in ("suppress", "resume"):
        control = commands.add_parser(action)
        control.add_argument("--product-id", required=True)
        control.add_argument("--reason", required=True)
        control.add_argument("--expected-fence")

    status = commands.add_parser("status")
    status.add_argument("--product-id", required=True)

    export = commands.add_parser("export", help="export a consistent research-state backup")
    export.add_argument("--output", required=True)
    reresearch = commands.add_parser("reresearch", help="back up and mark existing branded products once")
    reresearch.add_argument("--all-eligible", action="store_true", required=True)
    reresearch.add_argument("--backup", help="new backup file; defaults to the configured backup directory")

    args = parser.parse_args(argv)
    try:
        with HermesCompletionStore() as store:
            if args.command == "export":
                result = store.export_snapshot(args.output)
            elif args.command == "reresearch":
                result = store.mark_for_reresearch_all_eligible(backup_path=args.backup)
            elif args.command == "rerender":
                result = rerender_completion(
                    store,
                    args.product_id,
                    apply=args.apply,
                )
            elif args.command in {"suppress", "resume"}:
                fence = store.append_control_event(
                    args.product_id,
                    action=args.command,
                    reason=args.reason,
                    expected_fence=args.expected_fence,
                )
                result = {
                    "ok": True,
                    "product_id": args.product_id,
                    "action": args.command,
                    "fence": fence,
                }
            else:
                result = {
                    "ok": True,
                    **store.publication_status(args.product_id),
                }
    except Exception as exc:
        _print({"ok": False, "error": _safe_cli_error(exc)}, error=True)
        return 2
    _print(result)
    return 0


__all__ = ["main"]
