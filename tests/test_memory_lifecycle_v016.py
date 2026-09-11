"""Regresiones de propiedad y ciclo de vida para las mejoras de memoria 0.16."""

from __future__ import annotations

import logging
import threading

import numpy as np
import pandas as pd
import pytest

from record_linkage.golden.containment import (
    _concat_filtrado_por_columnas,
    consolidate_groups_by_nit_balanced,
)
from record_linkage.golden.generator import ConsumableDataFrame, GoldenRecordGeneratorV7
from record_linkage.pipeline.orchestrator import Orchestrator
from record_linkage.reporting.strategies import Phase
from record_linkage.utils.memory import RSSSampler


def test_consumable_dataframe_transfiere_exactamente_una_vez() -> None:
    frame = pd.DataFrame({"valor": [1, 2]})
    owner = ConsumableDataFrame(frame)

    assert owner.take() is frame
    assert owner.consumed
    with pytest.raises(RuntimeError, match="ya fue transferido"):
        owner.take()
    owner.release()


def test_generator_consume_la_entrada_antes_de_cargar_salidas(monkeypatch) -> None:
    frame = pd.DataFrame(
        {
            "ID_GRUPO": [0, 0, 2],
            "NIT": ["900", "900", "800"],
            "RAZON_SOCIAL": ["ACME", "ACME SAS", "BETA"],
            "SRC": ["A", "B", "A"],
            "ORIGINAL_INDEX": [10, 11, 12],
        }
    )
    owner = ConsumableDataFrame(frame)
    generator = GoldenRecordGeneratorV7(["A", "B"], {})
    golden = pd.DataFrame({"ID_GRUPO": [0, 2]})
    correl = pd.DataFrame(
        {
            "ORIGINAL_INDEX": [12, 10, 11],
            "ID_GRUPO": [2, 0, 0],
            "SRC": ["A", "A", "B"],
        }
    )

    monkeypatch.setattr(generator, "_save_dataframe_optimized", lambda _df, _path: None)
    monkeypatch.setattr(generator, "_process_vectorized_batches", lambda _a, _b: None)

    def load_results(_path):
        assert owner.consumed
        return golden, correl

    monkeypatch.setattr(generator, "_load_results_from_db", load_results)
    monkeypatch.setattr(generator, "_cleanup_resources", lambda: None)

    golden_out, correl_out = generator.generate(owner)

    assert golden_out is golden
    assert correl_out is correl
    assert owner.consumed


def test_invariantes_vectorizadas_detectan_asociacion_incorrecta() -> None:
    generator = GoldenRecordGeneratorV7(["A"], {})
    expected_index = pd.Series([0, 1, 2], dtype="int64")
    expected_groups = pd.Series([0, 0, 2], dtype="int64")
    golden = pd.DataFrame({"ID_GRUPO": [0, 2]})
    correl = pd.DataFrame({"ORIGINAL_INDEX": [2, 0, 1], "ID_GRUPO": [2, 0, 0], "SRC": ["A"] * 3})

    generator._validate_output_invariants(3, expected_index, expected_groups, golden, correl)
    corrupt = correl.assign(ID_GRUPO=[2, 2, 0])
    with pytest.raises(RuntimeError, match="asociación"):
        generator._validate_output_invariants(3, expected_index, expected_groups, golden, corrupt)


def _containment_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    golden = pd.DataFrame(
        {
            "ID_GRUPO": [10, 11, 12],
            "NIT_FINAL": ["8909002860", "8909002860", "800111222"],
            "RAZON_SOCIAL_FINAL": [
                "DEPARTAMENTO DE ANTIOQUIA",
                "GOBERNACION DE ANTIOQUIA",
                "ACME LTDA",
            ],
            "SCORE": [0.8, 0.7, 0.9],
        }
    )
    correl = pd.DataFrame(
        {
            "ID_GRUPO": [10, 11, 12],
            "NIT": ["8909002860", "8909002860", "800111222"],
            "RAZON_SOCIAL": [
                "DEPARTAMENTO DE ANTIOQUIA",
                "GOBERNACION DE ANTIOQUIA",
                "ACME LTDA",
            ],
            "NIT_FINAL": ["viejo", "viejo", "viejo"],
            "RAZON_SOCIAL_FINAL": ["viejo", "viejo", "viejo"],
        }
    )
    return golden, correl


def test_consolidacion_low_copy_preserva_api_y_permite_transferir_propiedad() -> None:
    golden, correl = _containment_frames()
    original = correl.copy(deep=True)

    gold_copy, corr_copy = consolidate_groups_by_nit_balanced(golden, correl, verbose=False)
    pd.testing.assert_frame_equal(correl, original)
    assert corr_copy is not correl
    assert len(gold_copy) == 2

    golden, correl = _containment_frames()
    gold_owned, corr_owned = consolidate_groups_by_nit_balanced(
        golden, correl, verbose=False, copiar_correlativa=False
    )
    assert corr_owned is correl
    assert len(gold_owned) == 2
    assert corr_owned["ID_GRUPO"].tolist() == [10, 10, 12]


def test_consolidacion_transferida_no_queda_parcial_si_falla_validacion(
    monkeypatch,
) -> None:
    import record_linkage.golden.containment as containment_module

    golden, correl = _containment_frames()
    original = correl.copy(deep=True)

    def fail_before_commit(*_args, **_kwargs):
        raise ValueError("fallo contractual")

    monkeypatch.setattr(containment_module, "_concat_filtrado_por_columnas", fail_before_commit)
    with pytest.raises(ValueError, match="fallo contractual"):
        containment_module.consolidate_groups_by_nit_balanced(
            golden,
            correl,
            verbose=False,
            copiar_correlativa=False,
        )

    pd.testing.assert_frame_equal(correl, original)


def test_concat_por_columnas_equivale_a_concat_tradicional() -> None:
    golden = pd.DataFrame(
        {
            "ID_GRUPO": pd.array([0, 1, 2], dtype="int64"),
            "NIT_FINAL": pd.array(["a", "b", "c"], dtype="string[pyarrow]"),
            "SOLO_ARRIBA": [0.1, 0.2, 0.3],
        }
    )
    new = pd.DataFrame(
        {
            "ID_GRUPO": pd.array([1], dtype="int64"),
            "NIT_FINAL": pd.array(["bb"], dtype="string[pyarrow]"),
            "SOLO_ABAJO": pd.array([7], dtype="int64"),
        }
    )
    affected = {1}
    expected = pd.concat([golden[~golden["ID_GRUPO"].isin(affected)], new], ignore_index=True)

    result = _concat_filtrado_por_columnas(golden, new, affected)

    pd.testing.assert_frame_equal(result, expected)


def test_liberacion_de_fuentes_no_retiene_bloques_originales() -> None:
    original = pd.DataFrame({"NIT": np.arange(10_000), "RAZON_SOCIAL": ["A"] * 10_000})
    original_numeric_block = next(
        block.values for block in original._mgr.blocks if block.values.dtype.kind in "iu"
    )
    orchestrator = object.__new__(Orchestrator)
    orchestrator.sources = {"RUES": original}
    orchestrator._owned_source_mapping = None
    orchestrator._sources_released_after_l1 = False

    orchestrator._release_source_frames()

    empty = orchestrator.sources["RUES"]
    empty_numeric_block = next(
        block.values for block in empty._mgr.blocks if block.values.dtype.kind in "iu"
    )
    assert list(orchestrator.sources) == ["RUES"]
    assert empty.empty
    assert empty.dtypes.equals(original.dtypes)
    assert empty_numeric_block.base is not original_numeric_block
    assert orchestrator._sources_released_after_l1


def test_ownership_de_fuentes_default_no_muta_y_opt_in_vacia_mapping() -> None:
    frame = pd.DataFrame({"NIT": [1], "RAZON_SOCIAL": ["ACME"]})

    external_default = {"RUES": frame}
    orchestrator_default = object.__new__(Orchestrator)
    orchestrator_default.sources = dict(external_default)
    orchestrator_default._owned_source_mapping = None
    orchestrator_default._sources_released_after_l1 = False
    orchestrator_default._release_source_frames()
    assert list(external_default) == ["RUES"]
    assert external_default["RUES"] is frame

    external_owned = {"RUES": frame}
    orchestrator_owned = object.__new__(Orchestrator)
    orchestrator_owned.sources = dict(external_owned)
    orchestrator_owned._owned_source_mapping = external_owned
    orchestrator_owned._sources_released_after_l1 = False
    orchestrator_owned._release_source_frames()
    assert external_owned == {}
    assert list(orchestrator_owned.sources) == ["RUES"]


def test_linkage_propaga_contrato_de_consumo_sin_cambiar_default(monkeypatch, tmp_path) -> None:
    import record_linkage.api as api_module
    import record_linkage.config.profiles as profiles_module
    import record_linkage.pipeline.orchestrator as orchestrator_module

    received_flags: list[bool] = []

    class FakeOrchestrator:
        def __init__(self, config, sources, work_dir, *, consume_sources=False):
            del work_dir
            self.config = config
            self.sources = sources
            self.consume_sources = consume_sources
            self.profile = {"skip_reporting": True}
            received_flags.append(consume_sources)

        def run(self, *, skip_reporting=None):
            del skip_reporting
            if self.consume_sources:
                self.sources.clear()
            return {"golden": pd.DataFrame(), "correlative": pd.DataFrame()}

    monkeypatch.setattr(orchestrator_module, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(
        profiles_module,
        "crear_config_orchestrator",
        lambda **_kwargs: {"profile": "test", "profiles": {"test": {}}},
    )

    default_sources = {"RUES": pd.DataFrame({"NIT": ["1"], "RAZON_SOCIAL": ["ACME"]})}
    api_module.linkage(
        default_sources,
        work_dir=str(tmp_path / "default"),
        skip_reporting=True,
    )
    assert list(default_sources) == ["RUES"]

    owned_sources = {"RUES": pd.DataFrame({"NIT": ["1"], "RAZON_SOCIAL": ["ACME"]})}
    api_module.linkage(
        owned_sources,
        work_dir=str(tmp_path / "owned"),
        skip_reporting=True,
        consume_sources=True,
    )
    assert owned_sources == {}
    assert received_flags == [False, True]


def test_linkage_real_consume_mapping_tras_l1(tmp_path) -> None:
    from record_linkage.api import linkage

    sources = {
        "A": pd.DataFrame(
            {
                "NIT": ["900111222", "800333444"],
                "RAZON_SOCIAL": ["ACME SAS", "BETA LTDA"],
            }
        ),
        "B": pd.DataFrame({"NIT": ["9001112221"], "RAZON_SOCIAL": ["ACME S.A.S."]}),
    }

    result = linkage(
        sources,
        work_dir=str(tmp_path),
        profile="prueba_rapida",
        skip_reporting=True,
        consume_sources=True,
    )

    assert sources == {}
    assert len(result["correlative"]) == 3
    assert not result["golden"].empty


def test_rss_sampler_real_no_deja_hilo_vivo() -> None:
    samples = iter([1, 5, 3, 2])
    peak_seen = threading.Event()

    def reader() -> int:
        value = next(samples, 2)
        if value == 5:
            peak_seen.set()
        return value * 1024**2

    sampler = RSSSampler(interval_seconds=0.001, reader=reader)
    sampler.start()
    assert peak_seen.wait(timeout=1.0)
    sampler.stop()
    sampler.join()

    assert sampler.peak_mib == 5
    assert not sampler.is_alive


def test_exec_phase_cierra_sampler_en_finally_si_la_fase_falla(monkeypatch) -> None:
    import record_linkage.pipeline.orchestrator as orchestrator_module

    events: list[str] = []

    class FakeSampler:
        peak_mib = 12.5

        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

        def join(self):
            events.append("join")

    class FakeState:
        manifest: dict = {}

        @staticmethod
        def compute_hash(_config, _phase, _data_sig, _prev_hash):
            return "a" * 64

        @staticmethod
        def is_valid(_phase, _ph_hash):
            return False

    orchestrator = object.__new__(Orchestrator)
    orchestrator.state = FakeState()
    orchestrator.config = {}
    orchestrator.data_sig = "data"
    orchestrator._force_rerun_phases = set()
    orchestrator._phase_peak_rss_mib = {}
    orchestrator._phase_times = {}
    orchestrator.log = logging.getLogger("test-rss-finally")
    monkeypatch.setattr(orchestrator_module, "RSSSampler", FakeSampler)

    def fail():
        raise LookupError("fallo deliberado")

    with pytest.raises(LookupError, match="fallo deliberado"):
        orchestrator._exec_phase(Phase.L1_PREP, fail, "")

    assert events == ["start", "stop", "join"]
    assert orchestrator._phase_peak_rss_mib[Phase.L1_PREP.value] == 12.5


def test_exec_phase_intenta_cerrar_sampler_si_start_falla(monkeypatch) -> None:
    import record_linkage.pipeline.orchestrator as orchestrator_module

    events: list[str] = []

    class FailingStartSampler:
        peak_mib = 0.0

        def start(self):
            events.append("start")
            raise RuntimeError("no pudo iniciar")

        def stop(self):
            events.append("stop")

        def join(self):
            events.append("join")

    class FakeState:
        manifest: dict = {}

        @staticmethod
        def compute_hash(_config, _phase, _data_sig, _prev_hash):
            return "b" * 64

        @staticmethod
        def is_valid(_phase, _ph_hash):
            return False

    orchestrator = object.__new__(Orchestrator)
    orchestrator.state = FakeState()
    orchestrator.config = {}
    orchestrator.data_sig = "data"
    orchestrator._force_rerun_phases = set()
    orchestrator._phase_peak_rss_mib = {}
    orchestrator._phase_times = {}
    orchestrator.log = logging.getLogger("test-rss-start-failure")
    monkeypatch.setattr(orchestrator_module, "RSSSampler", FailingStartSampler)

    with pytest.raises(RuntimeError, match="no pudo iniciar"):
        orchestrator._exec_phase(Phase.L1_PREP, lambda: None, "")

    assert events == ["start", "stop", "join"]
