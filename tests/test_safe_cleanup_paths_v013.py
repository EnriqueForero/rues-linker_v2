"""Adversarial regressions for destructive temporary-path cleanup."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from record_linkage.deduplication.colab import ColabOptimizedManager


def _symlink_or_skip(link: Path, target: Path, *, is_directory: bool = False) -> None:
    """Create a symlink or skip when Windows lacks SeCreateSymbolicLinkPrivilege."""

    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows host does not grant symbolic-link privilege")
        raise


def test_colab_cleanup_preserves_caller_root_and_siblings(tmp_path: Path) -> None:
    if not shutil.rmtree.avoids_symlink_attacks:
        pytest.skip("This platform lacks symlink-safe recursive deletion")
    caller_root = tmp_path / "caller-owned"
    caller_root.mkdir()
    sibling = caller_root / "do-not-delete.txt"
    sibling.write_text("valuable", encoding="utf-8")

    manager = ColabOptimizedManager(temp_storage=caller_root)
    first_session = Path(manager.temp_storage)
    (first_session / "generated.bin").write_bytes(b"temporary")

    assert first_session.parent == caller_root.resolve()
    manager.cleanup_temp_files()

    second_session = Path(manager.temp_storage)
    assert sibling.read_text(encoding="utf-8") == "valuable"
    assert not first_session.exists()
    assert second_session.is_dir()
    assert second_session.parent == caller_root.resolve()
    assert second_session != first_session


def test_colab_cleanup_refuses_arbitrary_reassigned_path(tmp_path: Path) -> None:
    manager = ColabOptimizedManager(temp_storage=tmp_path / "temp-root")
    valuable = tmp_path / "valuable"
    valuable.mkdir()
    sentinel = valuable / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    manager.temp_storage = str(valuable)

    with pytest.raises(RuntimeError, match=r"propiedad|sesión hija"):
        manager.cleanup_temp_files()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_colab_cleanup_refuses_replaced_session_symlink(tmp_path: Path) -> None:
    manager = ColabOptimizedManager(temp_storage=tmp_path / "temp-root")
    session = Path(manager.temp_storage)
    preserved_session = session.with_name(f"{session.name}_original")
    session.rename(preserved_session)

    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    _symlink_or_skip(session, external, is_directory=True)

    with pytest.raises(RuntimeError, match="enlace simbólico"):
        manager.cleanup_temp_files()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_colab_cleanup_unlinks_child_symlink_without_following_it(tmp_path: Path) -> None:
    manager = ColabOptimizedManager(temp_storage=tmp_path / "temp-root")
    first_session = Path(manager.temp_storage)
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    _symlink_or_skip(first_session / "escape", external, is_directory=True)

    manager.cleanup_temp_files()

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not first_session.exists()


def test_colab_cleanup_requires_untampered_owner_marker(tmp_path: Path) -> None:
    manager = ColabOptimizedManager(temp_storage=tmp_path / "temp-root")
    session = Path(manager.temp_storage)
    marker = session / manager._OWNERSHIP_MARKER
    marker.write_text(json.dumps({"schema_version": 1, "owner_token": "attacker"}))
    sentinel = session / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(RuntimeError, match="no pertenece"):
        manager.cleanup_temp_files()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_colab_constructor_rejects_symlink_root(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    linked_root = tmp_path / "linked-root"
    _symlink_or_skip(linked_root, external, is_directory=True)

    with pytest.raises(ValueError, match="enlace simbólico"):
        ColabOptimizedManager(temp_storage=linked_root)


def test_colab_cleanup_refuses_replaced_root_directory(tmp_path: Path) -> None:
    root = tmp_path / "temp-root"
    manager = ColabOptimizedManager(temp_storage=root)
    moved_original = tmp_path / "moved-original-root"
    root.rename(moved_original)
    root.mkdir()
    sentinel = root / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(RuntimeError, match="raíz temporal cambió"):
        manager.cleanup_temp_files()
    assert sentinel.read_text(encoding="utf-8") == "keep"
