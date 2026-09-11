"""Regresiones de integridad para checkpoints y huellas del Orchestrator."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.config.profiles import crear_config_orchestrator
from record_linkage.pipeline._phase_constants import PHASES_ORDER
from record_linkage.pipeline.fingerprints import (
    fingerprint_config,
    fingerprint_dataframe,
    fingerprint_sources,
)
from record_linkage.pipeline.orchestrator import Orchestrator
from record_linkage.pipeline.state_manager import MANIFEST_SCHEMA_VERSION, StateManager
from record_linkage.reporting.strategies import Phase


def _sources(prefix: str) -> dict[str, pd.DataFrame]:
    return {
        "RUES": pd.DataFrame(
            {
                "NIT": ["900111111", "900222222"],
                "RAZON_SOCIAL": [f"{prefix} UNO", f"{prefix} DOS"],
            }
        )
    }


def _config() -> dict:
    return {
        "profile": "test",
        "profiles": {
            "test": {
                "lsh_permutations": 128,
                "lsh_ngram": 3,
                "lsh_threshold": 0.55,
                "cross_source_only": False,
                "score_threshold": 0.45,
                "min_name_similarity": 0.30,
                "max_nit_distance": 3,
                "weights": {"name": 0.5, "nit": 0.5},
                "source_quality_weights": {"RUES": 1.0},
            }
        },
        "column_mapping": {},
    }


def test_fingerprint_sources_detects_same_shape_different_content() -> None:
    assert fingerprint_sources(_sources("ALFA")) != fingerprint_sources(_sources("GAMMA"))


def test_fingerprint_sources_hashes_every_row_not_a_sample() -> None:
    original = pd.DataFrame({"RAZON_SOCIAL": [f"EMPRESA {i}" for i in range(2_001)]})
    changed = original.copy()
    changed.loc[1, "RAZON_SOCIAL"] = "CAMBIO EN FILA NO MUESTREADA POR EL HASH ANTIGUO"

    assert fingerprint_sources({"X": original}) != fingerprint_sources({"X": changed})


def test_fingerprint_sources_hashes_complete_values() -> None:
    common_prefix = "A" * 50
    left = pd.DataFrame({"RAZON_SOCIAL": [common_prefix + "IZQUIERDA"]})
    right = pd.DataFrame({"RAZON_SOCIAL": [common_prefix + "DERECHA"]})

    assert fingerprint_sources({"X": left}) != fingerprint_sources({"X": right})


def test_fingerprint_sources_includes_schema_dtype_and_source_order() -> None:
    ints = pd.DataFrame({"VALOR": pd.Series([1, 2], dtype="int64")})
    strings = pd.DataFrame({"VALOR": pd.Series(["1", "2"], dtype="string")})
    assert fingerprint_sources({"X": ints}) != fingerprint_sources({"X": strings})

    a = pd.DataFrame({"VALOR": [1]})
    b = pd.DataFrame({"VALOR": [2]})
    assert fingerprint_sources({"A": a, "B": b}) != fingerprint_sources({"B": b, "A": a})


def test_fingerprint_dataframe_includes_categorical_metadata() -> None:
    left = pd.DataFrame(
        {"VALOR": pd.Series(pd.Categorical(["A"], categories=["A", "B"], ordered=False))}
    )
    right = pd.DataFrame(
        {"VALOR": pd.Series(pd.Categorical(["A"], categories=["A", "C"], ordered=False))}
    )
    assert fingerprint_dataframe(left) != fingerprint_dataframe(right)


def test_fingerprint_dataframe_supports_unhashable_cells_deterministically() -> None:
    left = pd.DataFrame({"META": [{"tags": ["A", "B"]}]})
    right = pd.DataFrame({"META": [{"tags": ["A", "C"]}]})
    assert fingerprint_dataframe(left) == fingerprint_dataframe(left.copy(deep=True))
    assert fingerprint_dataframe(left) != fingerprint_dataframe(right)


def test_fingerprint_config_is_deterministic_for_sets() -> None:
    left = {"trusted_unique_sources": {"RUES", "DIAN"}}
    right = {"trusted_unique_sources": {"DIAN", "RUES"}}
    assert fingerprint_config(left) == fingerprint_config(right)


@pytest.mark.parametrize(
    ("phase", "key", "before", "after"),
    [
        (Phase.L2_LSH_CANDIDATES, "trusted_unique_sources", [], ["RUES"]),
        (Phase.L2_LSH_CANDIDATES, "max_bucket_size", 500, 10),
        (
            Phase.L3_SCORING,
            "extra_features",
            [],
            [{"column": "CIUDAD", "weight": 0.5, "type": "categorical_signed"}],
        ),
        (Phase.L3_SCORING, "nit_empty_passes_filter", True, False),
        (Phase.L4_CLUSTERING, "use_strict_clusters", False, True),
        (Phase.L4_CLUSTERING, "max_sources_per_group", None, 2),
        (Phase.L5_GOLDEN, "min_sources_for_golden", 1, 2),
    ],
)
def test_phase_hash_changes_for_every_effective_config_parameter(
    tmp_path: Path,
    phase: Phase,
    key: str,
    before: object,
    after: object,
) -> None:
    state = StateManager(tmp_path)
    config_before = _config()
    config_after = copy.deepcopy(config_before)
    config_before["profiles"]["test"][key] = before
    config_after["profiles"]["test"][key] = after

    hash_before = state.compute_hash(config_before, phase, "data", "upstream")
    hash_after = state.compute_hash(config_after, phase, "data", "upstream")
    assert hash_before != hash_after


def test_orchestrator_data_signature_changes_with_same_cardinality(tmp_path: Path) -> None:
    config = _config()
    first = Orchestrator(config, _sources("ALFA"), str(tmp_path / "first"))
    second = Orchestrator(config, _sources("GAMMA"), str(tmp_path / "second"))

    assert first.data_sig != second.data_sig


def test_same_orchestrator_instance_rehashes_mutated_sources(tmp_path: Path) -> None:
    workspace = tmp_path / "mutated-source"
    source = pd.DataFrame(
        {
            "NIT": ["900111111", "900222222"],
            "RAZON_SOCIAL": ["ALFA SAS", "BETA SAS"],
            "MARK": ["A0", "A1"],
        }
    )
    config = crear_config_orchestrator(perfil="prueba_rapida", workspace=str(workspace))
    orchestrator = Orchestrator(config, {"SRC1": source}, str(workspace))

    first = orchestrator.run(skip_reporting=True)
    assert len(first["correlative"]) == 2

    source.loc[0, "MARK"] = "B0"
    source.loc[2] = ["900333333", "GAMMA SAS", "B2"]
    second = orchestrator.run(skip_reporting=True)

    assert len(second["correlative"]) == 3
    assert set(second["correlative"]["MARK"]) == {"B0", "A1", "B2"}


def test_manifest_legacy_is_invalidated_once(tmp_path: Path) -> None:
    legacy = {
        Phase.L1_PREP.value: {
            "hash": "0123456789ab",
            "status": "DONE",
            "files": [str(tmp_path / "old.parquet")],
        }
    }
    (tmp_path / "manifest.json").write_text(json.dumps(legacy), encoding="utf-8")

    state = StateManager(tmp_path)
    assert Phase.L1_PREP.value not in state.manifest
    assert state.manifest["_meta"]["schema_version"] == MANIFEST_SCHEMA_VERSION


@pytest.mark.parametrize(
    ("replacement",),
    [(b"mas-largo",), (b"cambiadx",)],
    ids=["different-size", "same-size"],
)
def test_checkpoint_rejects_replaced_artifact(tmp_path: Path, replacement: bytes) -> None:
    state = StateManager(tmp_path)
    artifact = tmp_path / "phase.bin"
    artifact.write_bytes(b"original")
    phase_hash = state.compute_hash(_config(), Phase.L1_PREP, "data", "")
    state.mark_done(Phase.L1_PREP, phase_hash, [artifact])
    assert state.is_valid(Phase.L1_PREP, phase_hash)

    artifact.write_bytes(replacement)
    assert not state.is_valid(Phase.L1_PREP, phase_hash)


def test_manifest_save_is_valid_json_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    state = StateManager(tmp_path)
    artifact = tmp_path / "phase.bin"
    artifact.write_bytes(b"contenido")
    phase_hash = state.compute_hash(_config(), Phase.L1_PREP, "data", "")
    state.mark_done(Phase.L1_PREP, phase_hash, [artifact])

    persisted = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert persisted[Phase.L1_PREP.value]["hash"] == phase_hash
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []


def test_estimate_uses_current_expected_hash_chain_and_honors_from_phase(
    tmp_path: Path,
) -> None:
    config = _config()
    data_sig = fingerprint_sources(_sources("ALFA"))
    state = StateManager(tmp_path)

    previous = ""
    for phase in PHASES_ORDER:
        artifact = tmp_path / f"{phase.value}.bin"
        artifact.write_bytes(phase.value.encode())
        phase_hash = state.compute_hash(config, phase, data_sig, previous)
        state.mark_done(phase, phase_hash, [artifact])
        previous = phase_hash

    orchestrator = object.__new__(Orchestrator)
    orchestrator.config = config
    orchestrator.data_sig = data_sig
    orchestrator.state = state

    assert orchestrator.estimate()["pending"] == []
    estimate = orchestrator.estimate(from_phase=Phase.L3_SCORING)
    assert estimate["saved"] == [Phase.L1_PREP.value, Phase.L2_LSH_CANDIDATES.value]
    assert estimate["pending"] == [phase.value for phase in PHASES_ORDER[2:]]


def test_estimate_does_not_reuse_manifest_after_omitted_parameter_changes(
    tmp_path: Path,
) -> None:
    old_config = _config()
    data_sig = fingerprint_sources(_sources("ALFA"))
    state = StateManager(tmp_path)

    previous = ""
    for phase in PHASES_ORDER:
        artifact = tmp_path / f"{phase.value}.bin"
        artifact.write_bytes(phase.value.encode())
        phase_hash = state.compute_hash(old_config, phase, data_sig, previous)
        state.mark_done(phase, phase_hash, [artifact])
        previous = phase_hash

    new_config = copy.deepcopy(old_config)
    new_config["profiles"]["test"]["extra_features"] = [
        {"column": "CIUDAD", "weight": 0.5, "type": "categorical_signed"}
    ]
    orchestrator = object.__new__(Orchestrator)
    orchestrator.config = new_config
    orchestrator.data_sig = data_sig
    orchestrator.state = state

    assert orchestrator.estimate()["saved"] == []
    assert orchestrator.estimate()["pending"] == [phase.value for phase in PHASES_ORDER]
