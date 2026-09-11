"""Regression gates for repository-only supply-chain assets."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FULL_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
TRUSTED_ORACLE_SHA256 = "47cf50dfa515d6549cb49697107b831666dae763660e42b7d5eb7c525f7542d7"


def test_all_github_actions_are_pinned_to_full_commit_sha() -> None:
    workflow_dir = REPO_ROOT / ".github" / "workflows"
    uses_pattern = re.compile(r"^\s*-?\s*uses:\s*[^@\s]+@([^\s#]+)", re.MULTILINE)
    refs: list[tuple[str, str]] = []

    for workflow in sorted(workflow_dir.glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        refs.extend((workflow.name, ref) for ref in uses_pattern.findall(text))

    assert refs, "No se encontraron actions en los workflows"
    assert all(FULL_COMMIT_SHA.fullmatch(ref) for _, ref in refs), refs


def test_trusted_pickle_oracle_is_hash_pinned_and_source_only() -> None:
    """The legacy parity oracle is trusted test data, never a runtime asset."""

    oracle = REPO_ROOT / "tests" / "data" / "oraculo_scorer_p1_1.pkl"
    assert hashlib.sha256(oracle.read_bytes()).hexdigest() == TRUSTED_ORACLE_SHA256

    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "tests" in manifest and "recursive-exclude" in manifest
