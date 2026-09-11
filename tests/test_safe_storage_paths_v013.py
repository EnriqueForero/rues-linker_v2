"""Adversarial path and overwrite tests added for the v0.13 production gate."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

import record_linkage.engine.scorer as scorer_module
from record_linkage.engine.scorer import VectorizedScorer
from record_linkage.pipeline.storage import HybridStorageManager


def _symlink_or_skip(link: Path, target: Path, *, is_directory: bool = False) -> None:
    """Create a symlink or skip when Windows lacks SeCreateSymbolicLinkPrivilege."""

    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows host does not grant symbolic-link privilege")
        raise


def _storage(tmp_path: Path) -> HybridStorageManager:
    return HybridStorageManager(
        drive_workspace=tmp_path / "drive",
        local_base=str(tmp_path / "local"),
    )


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../escape.db",
        "nested/../../escape.db",
        r"nested\..\..\escape.db",
        "/tmp/escape.db",
        r"C:\escape.db",
        r"\rooted.db",
        r"\\server\share\escape.db",
        "",
        ".",
    ],
)
def test_storage_rejects_traversal_absolute_and_empty_paths(
    tmp_path: Path, unsafe_name: str
) -> None:
    storage = _storage(tmp_path)

    with pytest.raises(ValueError):
        storage.local_path(unsafe_name)
    with pytest.raises(ValueError):
        storage.pull_from_drive(unsafe_name)
    with pytest.raises(ValueError):
        storage.push_to_drive(unsafe_name, retries=1)


def test_storage_allows_safe_nested_paths_and_publishes_atomically(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    local = storage.local_path("nested/results.db")
    local.parent.mkdir(parents=True)
    local.write_bytes(b"local-v1")

    drive = storage.push_to_drive("nested/results.db")
    assert drive.read_bytes() == b"local-v1"

    # A hard link retains the old inode only when pull replaces atomically;
    # an in-place truncate/copy would mutate the hard link too.
    backup = tmp_path / "old-local-inode.db"
    os.link(local, backup)
    drive.write_bytes(b"drive-v2")
    returned = storage.pull_from_drive("nested/results.db")

    assert returned == local
    assert local.read_bytes() == b"drive-v2"
    assert backup.read_bytes() == b"local-v1"
    assert not list(local.parent.glob(".results.db.*.tmp"))


def test_storage_rejects_symlink_component_and_leaf(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink_or_skip(storage.local_dir / "linked", outside, is_directory=True)

    with pytest.raises(ValueError, match="simbólico"):
        storage.local_path("linked/victim.db")

    source = storage.local_path("source.db")
    source.write_bytes(b"new")
    protected = tmp_path / "protected.db"
    protected.write_bytes(b"do-not-touch")
    storage.drive_dir.mkdir(parents=True)
    _symlink_or_skip(storage.drive_dir / "source.db", protected)

    with pytest.raises(ValueError, match="simbólico"):
        storage.push_to_drive("source.db", retries=1)
    assert protected.read_bytes() == b"do-not-touch"


def test_cleanup_refuses_broad_or_replaced_symlink_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="demasiado amplio"):
        HybridStorageManager(tmp_path / "drive", local_base="/")

    storage = _storage(tmp_path)
    original_local = tmp_path / "original-local"
    storage.local_dir.rename(original_local)
    outside = tmp_path / "outside-cleanup"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("preserve", encoding="utf-8")
    _symlink_or_skip(storage.local_dir, outside, is_directory=True)

    storage.cleanup_local()

    assert victim.read_text(encoding="utf-8") == "preserve"
    assert storage.local_dir.is_symlink()


def test_cleanup_requires_ownership_marker_for_preexisting_nonempty_root(
    tmp_path: Path,
) -> None:
    local = tmp_path / "preexisting-local"
    local.mkdir()
    sentinel = local / "user-owned.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    storage = HybridStorageManager(tmp_path / "drive", local_base=str(local))

    storage.cleanup_local()

    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert not (local / HybridStorageManager._OWNERSHIP_MARKER).exists()


def test_cleanup_removes_root_created_and_marked_by_manager(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    marker = storage.local_dir / HybridStorageManager._OWNERSHIP_MARKER
    assert marker.is_file() and not marker.is_symlink()
    storage.local_path("temporary.db").write_bytes(b"temporary")

    storage.cleanup_local()

    assert not storage.local_dir.exists()


def _candidate_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE candidate_pairs "
            "(idx_0 INTEGER NOT NULL, idx_1 INTEGER NOT NULL, "
            "PRIMARY KEY (idx_0, idx_1)) WITHOUT ROWID"
        )
        connection.execute("INSERT INTO candidate_pairs VALUES (0, 1)")


def _scorer() -> VectorizedScorer:
    return VectorizedScorer(
        {
            "score_threshold": 0.8,
            "max_nit_distance": 1,
            "min_name_similarity": 0.5,
            "scoring_batch_size": 10,
        }
    )


def _empty_scores(
    _batch: object,
    _frame: pd.DataFrame,
    score_threshold: float | None = None,
) -> pd.DataFrame:
    return pd.DataFrame(columns=["idx_0", "idx_1", "score", "name_sim", "nit_dist"])


def test_scorer_atomic_overwrite_replaces_inode_only_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = tmp_path / "candidates.db"
    _candidate_database(candidates)
    target = tmp_path / "scored.db"
    target.write_bytes(b"old-result")
    old_inode = tmp_path / "old-result-hardlink.db"
    os.link(target, old_inode)
    scorer = _scorer()
    monkeypatch.setattr(scorer, "_score_batch_vectorized", _empty_scores)

    returned = scorer._score_pairs_from_db_streaming(
        str(candidates), pd.DataFrame(), 0.8, str(target)
    )

    assert returned == str(target)
    assert old_inode.read_bytes() == b"old-result"
    with sqlite3.connect(target) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("SELECT COUNT(*) FROM scored_pairs").fetchone() == (0,)
        assert connection.execute(
            "SELECT value FROM metadata WHERE key = 'total_candidates'"
        ).fetchone() == ("1",)
    assert not list(tmp_path.glob(".scored.db.*.tmp*"))


def test_scorer_failure_preserves_previous_output_and_cleans_private_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = tmp_path / "candidates.db"
    _candidate_database(candidates)
    target = tmp_path / "scored.db"
    target.write_bytes(b"known-good")
    scorer = _scorer()

    def fail_scoring(
        _batch: object,
        _frame: pd.DataFrame,
        score_threshold: float | None = None,
    ) -> pd.DataFrame:
        raise RuntimeError("injected scoring failure")

    monkeypatch.setattr(scorer, "_score_batch_vectorized", fail_scoring)

    with pytest.raises(RuntimeError, match="injected"):
        scorer._score_pairs_from_db_streaming(str(candidates), pd.DataFrame(), 0.8, str(target))

    assert target.read_bytes() == b"known-good"
    assert not list(tmp_path.glob(".scored.db.*.tmp*"))


def test_scorer_integrity_gate_failure_preserves_previous_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = tmp_path / "candidates.db"
    _candidate_database(candidates)
    target = tmp_path / "scored.db"
    target.write_bytes(b"last-verified-output")
    scorer = _scorer()
    monkeypatch.setattr(scorer, "_score_batch_vectorized", _empty_scores)

    def fail_integrity(_cursor: sqlite3.Cursor) -> None:
        raise sqlite3.DatabaseError("injected integrity failure")

    monkeypatch.setattr(scorer_module, "_validate_sqlite_integrity", fail_integrity)

    with pytest.raises(sqlite3.DatabaseError, match="integrity"):
        scorer._score_pairs_from_db_streaming(str(candidates), pd.DataFrame(), 0.8, str(target))

    assert target.read_bytes() == b"last-verified-output"
    assert not list(tmp_path.glob(".scored.db.*.tmp*"))


@pytest.mark.parametrize("hardlink_alias", [False, True])
def test_scorer_rejects_candidate_output_alias_and_preserves_candidates(
    tmp_path: Path, hardlink_alias: bool
) -> None:
    candidates = tmp_path / "candidates.db"
    _candidate_database(candidates)
    before = candidates.read_bytes()
    output = candidates
    if hardlink_alias:
        output = tmp_path / "candidate-alias.db"
        os.link(candidates, output)

    with pytest.raises(ValueError, match="distinto"):
        _scorer()._score_pairs_from_db_streaming(str(candidates), pd.DataFrame(), 0.8, str(output))

    assert candidates.read_bytes() == before
    with sqlite3.connect(candidates) as connection:
        assert connection.execute("SELECT COUNT(*) FROM candidate_pairs").fetchone() == (1,)


@pytest.mark.parametrize("link_parent", [False, True])
def test_scorer_rejects_symlink_output_without_touching_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link_parent: bool
) -> None:
    candidates = tmp_path / "candidates.db"
    _candidate_database(candidates)
    protected = tmp_path / "protected.db"
    protected.write_bytes(b"protected")
    scorer = _scorer()
    monkeypatch.setattr(scorer, "_score_batch_vectorized", _empty_scores)

    if link_parent:
        actual_parent = tmp_path / "actual"
        actual_parent.mkdir()
        linked_parent = tmp_path / "linked-parent"
        _symlink_or_skip(linked_parent, actual_parent, is_directory=True)
        output = linked_parent / "scored.db"
    else:
        output = tmp_path / "scored.db"
        _symlink_or_skip(output, protected)

    with pytest.raises(ValueError, match="simbólico"):
        scorer._score_pairs_from_db_streaming(str(candidates), pd.DataFrame(), 0.8, str(output))

    assert protected.read_bytes() == b"protected"
