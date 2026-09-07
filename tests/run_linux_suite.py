"""Run both test groups from an isolated copy of a Windows checkout in Linux.

Example (WSL): docker run --rm -v "$PWD:/source:ro" -v "$PWD/../test-results:/results"
  python:3.13-slim-bookworm python /source/tests/run_linux_suite.py /results/python313
The checkout is never modified. CRLF is normalized only in the temporary copy.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    source = Path(__file__).resolve().parents[1]
    logs = Path(sys.argv[1]).resolve()
    logs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pvwiki-tests-") as temporary:
        checkout = Path(temporary) / "repo"
        shutil.copytree(source, checkout, ignore=shutil.ignore_patterns(
            ".git", ".venv", ".codex", ".test-deps", "__pycache__", "*.egg-info", ".pytest_cache"))
        for path in checkout.rglob("*"):
            if not path.is_file():
                continue
            data = path.read_bytes()
            if path.suffix in {".py", ".md", ".toml", ".yaml", ".yml", ".sh", ".json", ".js"} or data.startswith(b"#!"):
                path.write_bytes(data.replace(b"\r\n", b"\n"))
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
        commands = {
            "install": [sys.executable, "-m", "pip", "install", ".[dev]"],
            "runtime": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            "skills": [sys.executable, "-m", "unittest", "discover", "-s", "skills/wikijs-sync-products/tests", "-v"],
        }
        result = {"python": sys.version, "groups": {}}
        for name, command in commands.items():
            with (logs / f"{name}.log").open("w", encoding="utf-8") as output:
                code = subprocess.call(command, cwd=checkout, env=environment, stdout=output, stderr=subprocess.STDOUT)
            result["groups"][name] = code
            print(f"{name}: exit {code}", flush=True)
            if name == "install" and code:
                break
        (logs / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result), flush=True)
        return int(any(result["groups"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
