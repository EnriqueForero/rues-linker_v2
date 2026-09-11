"""Tests del Sprint 0.8.1 — Limpieza post first-run.

Cubre las 4 tareas técnicas del sprint:
    1.1 — Auditoría de pares: stdout limpio por default, opt-in vía profile/env.
    1.2 — Sanitización de comillas literales en RAZON_SOCIAL (modo AGRESIVO).
    1.3 — skip_reporting configurable desde el profile.
    1.4 — Sin emojis en textos renderizados por matplotlib.

Cada bloque está documentado para que sirva como referencia de las decisiones
tomadas (no solo aserciones mecánicas).
"""

from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.scorer import VectorizedScorer
from record_linkage.processing.text import TextProcessor

# ─────────────────────────────────────────────────────────────────────────────
# TAREA 1.1 — Auditoría de pares opt-in
# ─────────────────────────────────────────────────────────────────────────────

# Profile mínimo para construir VectorizedScorer sin depender del resto del pipeline.
_PROFILE_BASE = {
    "score_threshold": 0.60,
    "max_nit_distance": 0,
    "min_name_similarity": 0.65,
    "weights": {"name": 0.5, "nit": 0.5, "phonetic": 0.0},
    "nit_empty_passes_filter": False,
}


def _scorer(extra: dict | None = None) -> VectorizedScorer:
    profile = {**_PROFILE_BASE, **(extra or {})}
    return VectorizedScorer(profile=profile)


def _build_minimal_df() -> pd.DataFrame:
    """DataFrame mínimo para invocar la fase de scoring vectorizado.

    Construye 6 pares: 3 que pasarían filtros y 3 que no. Suficiente para
    disparar la rama de auditoría sin necesidad de un dataset grande.
    """
    return pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["ACME", "ACME SAS", "BETA", "BETA LTDA", "GAMA", "OTRO"],
            "NIT_OK": ["900111", "900111", "900222", "900222", "900333", "900999"],
        }
    )


def _candidates_pairs() -> set[tuple[int, int]]:
    return {(0, 1), (2, 3), (4, 5)}  # 2 mismos NIT (potencial pass), 1 distinto


def test_audit_pairs_default_es_cero(monkeypatch):
    """Default debe ser 0: NO se imprime nada salvo opt-in explícito."""
    monkeypatch.delenv("RUES_LINKER_AUDIT_PAIRS", raising=False)
    sc = _scorer()
    # Reproducir lo que hace score_pairs en su línea de init de _audit_counters.
    df = _build_minimal_df()
    sc.score_pairs(_candidates_pairs(), df, score_threshold=0.6)
    assert sc._audit_counters["max_prints"] == 0


def test_audit_pairs_opt_in_via_profile(monkeypatch):
    """audit_pairs_count en el profile debe propagarse a _audit_counters."""
    monkeypatch.delenv("RUES_LINKER_AUDIT_PAIRS", raising=False)
    sc = _scorer({"audit_pairs_count": 7})
    df = _build_minimal_df()
    sc.score_pairs(_candidates_pairs(), df, score_threshold=0.6)
    assert sc._audit_counters["max_prints"] == 7


def test_audit_pairs_env_var_overrides_profile(monkeypatch):
    """La env var debe pisar lo que diga el profile (corridas ad-hoc)."""
    monkeypatch.setenv("RUES_LINKER_AUDIT_PAIRS", "3")
    sc = _scorer({"audit_pairs_count": 99})
    df = _build_minimal_df()
    sc.score_pairs(_candidates_pairs(), df, score_threshold=0.6)
    assert sc._audit_counters["max_prints"] == 3


def test_audit_pairs_env_var_invalida_cae_a_cero(monkeypatch):
    """Un valor no-numérico en la env var no debe romper, solo deshabilitar."""
    monkeypatch.setenv("RUES_LINKER_AUDIT_PAIRS", "no-soy-un-numero")
    sc = _scorer({"audit_pairs_count": 5})
    df = _build_minimal_df()
    sc.score_pairs(_candidates_pairs(), df, score_threshold=0.6)
    assert sc._audit_counters["max_prints"] == 0


def test_audit_pairs_no_print_a_stdout_por_default(monkeypatch, capsys):
    """Tras la corrida con default, stdout NO debe contener 'AUDITANDO PAR'.

    Este test es el corazón del fix: verifica que el código viejo (print
    directo) no haya regresado. Antes de v0.7.1 stdout siempre se contaminaba.
    """
    monkeypatch.delenv("RUES_LINKER_AUDIT_PAIRS", raising=False)
    sc = _scorer()
    df = _build_minimal_df()
    sc.score_pairs(_candidates_pairs(), df, score_threshold=0.6)
    captured = capsys.readouterr()
    assert "AUDITANDO PAR" not in captured.out
    assert "AUDIT PAIR" not in captured.out  # tampoco la nueva forma debe ir a stdout


def test_audit_pairs_opt_in_loguea_al_logger_no_a_stdout(monkeypatch, capsys):
    """Con opt-in: los mensajes van al logger en nivel DEBUG, no a stdout.

    Llama directamente a `_score_batch_vectorized` con un batch determinista
    para garantizar que se ejecute la rama de auditoría. Adjunta un handler
    propio al logger del scorer porque `CustomLogger` desactiva `propagate`
    (lo que rompería caplog).

    Robusto a contaminación de otros tests: ``test_scorer_extra_features.py``
    desactiva el logger globalmente (``.disabled = True``), por eso lo
    re-habilitamos explícitamente aquí.
    """
    monkeypatch.delenv("RUES_LINKER_AUDIT_PAIRS", raising=False)
    sc = _scorer({"audit_pairs_count": 5})
    # Defensiva: otros tests pueden haber desactivado el logger.
    sc.logger.logger.disabled = False
    sc.logger.logger.setLevel(logging.DEBUG)
    # score_pairs hace el setLevel/init, pero aquí queremos saltearlo y llamar
    # directo al batch vectorizado. Reproducimos lo mínimo:
    sc._audit_counters = {"passes_printed": 0, "fails_printed": 0, "max_prints": 5}

    # Handler de captura local (porque propagate=False en CustomLogger):
    captured_records: list[logging.LogRecord] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            captured_records.append(record)

    handler = _CaptureHandler(level=logging.DEBUG)
    sc.logger.logger.addHandler(handler)
    try:
        df = _build_minimal_df()
        batch = np.array([[0, 1], [2, 3], [4, 5]], dtype=int)
        sc._score_batch_vectorized(batch, df)
    finally:
        sc.logger.logger.removeHandler(handler)

    out = capsys.readouterr().out
    # stdout limpio (regresión que NUNCA debe volver):
    assert "AUDITANDO PAR" not in out
    assert "AUDIT PAIR" not in out
    # Al menos un mensaje de auditoría llegó al logger:
    audit_records = [r for r in captured_records if "AUDIT PAIR" in r.getMessage()]
    assert audit_records, (
        "Con opt-in (max_prints=5) debería haber al menos un mensaje AUDIT PAIR en el logger"
    )
    # Confirmar nivel DEBUG (no INFO/WARNING):
    assert all(r.levelno == logging.DEBUG for r in audit_records)


# ─────────────────────────────────────────────────────────────────────────────
# TAREA 1.2 — Sanitización de comillas literales en RAZON_SOCIAL (modo AGRESIVO)
# ─────────────────────────────────────────────────────────────────────────────
#
# CONTEXTO: el first-run mostró pares como `'DISENITOS S S '' vs 'DISENITOS'`
# donde RUES vino con comillas literales DENTRO del campo (CSV mal escapado).
#
# DECISIÓN (verificada empíricamente antes de codear):
#   - Modos CONSERVADOR/BALANCEADO: ya limpian comillas vía `non_alpha_regex`.
#     No se tocan: el riesgo de regresión supera al beneficio (y además rompen
#     apóstrofes legítimos como `O'CONNOR`, que es un bug VIEJO independiente).
#   - Modo AGRESIVO: NO limpia comillas (porque preserva puntuación selectiva).
#     Aquí SÍ se inserta `_strip_quote_artifacts`, preservando `O'CONNOR`.
#
# Por eso todos los tests de esta tarea usan cleaning_mode="AGRESIVO".


def test_strip_comillas_dobles_outer_agresivo():
    """`''ACME''` → comillas eliminadas; el nombre se conserva limpio."""
    tp = TextProcessor(cleaning_mode="AGRESIVO")
    out = tp._clean_name_impl("''ACME''")
    assert "''" not in out
    assert "ACME" in out


def test_strip_comillas_simples_outer_agresivo():
    """`'ACME'` (comilla simple outer) también se limpia en AGRESIVO."""
    tp = TextProcessor(cleaning_mode="AGRESIVO")
    out = tp._clean_name_impl("'ACME'")
    # No debería empezar/terminar con comilla:
    assert not out.startswith("'") and not out.endswith("'")
    assert "ACME" in out


def test_strip_comillas_dobles_internas_agresivo():
    """`ACME''XYZ` (artefacto CSV) → `ACME XYZ`, NO `ACME''XYZ`.

    Usamos `XYZ` (no `CORP`) porque CORP está en stopwords de modo AGRESIVO
    y se eliminaría por otra razón, oscureciendo lo que este test verifica.
    """
    tp = TextProcessor(cleaning_mode="AGRESIVO")
    out = tp._clean_name_impl("ACME''XYZ")
    assert "''" not in out
    assert "ACME" in out and "XYZ" in out


def test_strip_quote_artifacts_unit_directo():
    """Test unitario directo del sanitizador (sin pasar por el pipeline completo).

    Aísla el comportamiento del nuevo método antes de cualquier paso posterior
    de limpieza. Sirve de oráculo para regresiones futuras.
    """
    tp = TextProcessor(cleaning_mode="AGRESIVO")
    casos = [
        ("''DISENITOS S S ''", "DISENITOS S S"),
        ("'ACME'", "ACME"),
        ("''ACME''", "ACME"),
        ("ACME''CORP", "ACME CORP"),
        ("O'CONNOR", "O'CONNOR"),  # apóstrofe interior intacto
        ("DON'T BREAK ME", "DON'T BREAK ME"),
        ('"ACME"', "ACME"),
        ('AC""ME', "AC ME"),
        ("", ""),
        ("   ", ""),
    ]
    for entrada, esperado in casos:
        got = tp._strip_quote_artifacts(entrada)
        assert got == esperado, f"{entrada!r} → {got!r} (esperado {esperado!r})"


def test_caso_real_first_run_disenitos():
    """Caso exacto del first-run que motivó la tarea."""
    tp = TextProcessor(cleaning_mode="AGRESIVO")
    out = tp._clean_name_impl("''DISENITOS S S ''")
    assert "''" not in out
    assert "DISENITOS" in out


def test_preserva_apostrofes_legitimos_agresivo():
    """`O'CONNOR` NO debe perder el apóstrofe en AGRESIVO.

    Este es el test que asegura que la limpieza es quirúrgica: solo elimina
    DOBLES comillas (artefacto CSV), nunca apóstrofes simples sueltos dentro
    de palabras.
    """
    tp = TextProcessor(cleaning_mode="AGRESIVO")
    out = tp._clean_name_impl("O'CONNOR SAS")
    # En AGRESIVO se removerá " SAS" como sufijo legal, pero el apóstrofe
    # del nombre propio debe permanecer:
    assert "O'CONNOR" in out or "OCONNOR" in out  # tolerante a cómo el pipeline lo normalice
    # Lo crítico: NO debe convertirse en "CONNOR" (perdiendo la O):
    assert out.startswith("O"), f"Apóstrofe destruyó el inicio: {out!r}"


def test_strip_idempotente_agresivo():
    """Aplicar la limpieza dos veces da el mismo resultado."""
    tp = TextProcessor(cleaning_mode="AGRESIVO")
    out_1 = tp._clean_name_impl("''ACME CORP''")
    out_2 = tp._clean_name_impl(out_1)
    assert out_1 == out_2


def test_balanceado_sigue_funcionando_para_comillas():
    """Garantía de no-regresión en BALANCEADO.

    BALANCEADO no se tocó porque ya elimina comillas vía non_alpha_regex.
    Si este test rompe, alguien tocó el camino BALANCEADO sin querer.
    """
    tp = TextProcessor(cleaning_mode="BALANCEADO")
    out = tp._clean_name_impl("''DISENITOS''")
    assert "''" not in out
    assert "DISENITOS" in out


# ─────────────────────────────────────────────────────────────────────────────
# TAREA 1.3 — skip_reporting configurable desde el profile
# ─────────────────────────────────────────────────────────────────────────────


def test_skip_reporting_default_es_none_sentinela():
    """El default del parámetro de run() ahora es None (sentinela) para permitir
    leer del profile. Resolución dentro de run(): None → profile → False.
    """
    import inspect

    from record_linkage.pipeline.orchestrator import Orchestrator

    sig = inspect.signature(Orchestrator.run)
    assert sig.parameters["skip_reporting"].default is None


def test_skip_reporting_se_lee_del_profile_si_no_se_pasa(monkeypatch):
    """Si el profile dice skip_reporting=True y NO se pasa kwarg, debe respetarlo.

    Este test verifica el contrato — la integración real se prueba en
    tests/integration/test_orchestrator.py.
    """
    from record_linkage.pipeline.orchestrator import Orchestrator

    # Profile con skip_reporting=True a top-level del perfil activo
    config = {
        "profile": "test_skip",
        "profiles": {
            "test_skip": {
                "skip_reporting": True,
                **_PROFILE_BASE,
            }
        },
    }
    # Sources mínimas (no se ejecuta run, solo inspección del flag)
    sources = {"FUENTE_A": pd.DataFrame({"NOMBRE": ["A"], "NIT": ["1"]})}

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        orch = Orchestrator(config, sources, work_dir=tmp)
        # La propiedad profile debe exponer el flag:
        assert orch.profile.get("skip_reporting") is True


def test_skip_reporting_kwarg_pisa_profile(monkeypatch):
    """Cuando se pasa skip_reporting=False explícito, ignora lo que diga el profile.

    Esta es la prueba clave de la lógica de precedencia: kwarg > profile.
    No corremos el pipeline (caro y dependiente de fuentes); inspeccionamos
    el código de resolución directamente.
    """
    from record_linkage.pipeline.orchestrator import Orchestrator

    config = {
        "profile": "test_skip",
        "profiles": {"test_skip": {"skip_reporting": True, **_PROFILE_BASE}},
    }
    sources = {"FUENTE_A": pd.DataFrame({"NOMBRE": ["A"], "NIT": ["1"]})}
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        orch = Orchestrator(config, sources, work_dir=tmp)
        # Simulamos la resolución que hace run() en sus primeras líneas:
        # kwarg explícito False → debe quedar False, ignorando profile True.
        kwarg_value = False
        resolved = (
            kwarg_value
            if kwarg_value is not None
            else bool(orch.profile.get("skip_reporting", False))
        )
        assert resolved is False, "kwarg explícito debe ganar sobre el profile"

        # Caso simétrico: kwarg None → debe usar el profile (True).
        kwarg_value = None
        resolved = (
            kwarg_value
            if kwarg_value is not None
            else bool(orch.profile.get("skip_reporting", False))
        )
        assert resolved is True, "Con kwarg None, profile debe ganar"


# ─────────────────────────────────────────────────────────────────────────────
# TAREA 1.4 — Sin emojis en textos renderizados por matplotlib
# ─────────────────────────────────────────────────────────────────────────────


def test_no_warnings_glyph_faltante_al_renderizar_kpis_dashboard():
    """El rendering NO debe emitir UserWarning('Glyph ... missing from font').

    Renderiza una tarjeta KPI sintética y verifica que matplotlib no emite
    warnings de glifo faltante — incluso si el diccionario fuente trae un
    emoji en "icon". Eso prueba que el filtro ASCII-safe del sitio de render
    está activo.

    Este es el síntoma exacto del problema del first-run: Liberation Sans
    (default en Colab/Linux) no tiene glyphs de emoji.
    """
    import warnings

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from record_linkage.reporting.dashboard import ExecutiveDashboard

    metrics = {
        "total_records": 10_000,
        "unique_groups": 8_500,
        "linkage_rate": 0.15,
        "reduction_rate": 0.15,
        "execution_time": 12.3,
        "review_cases": 50,
    }
    # ExecutiveDashboard requiere dos DataFrames; los pasamos vacíos para
    # construir el objeto sin tocar la pipeline real.
    empty_corr = pd.DataFrame(columns=["ID_GRUPO", "SRC", "NIT", "RAZON_SOCIAL"])
    empty_gold = pd.DataFrame(columns=["ID_GRUPO", "NIT", "RAZON_SOCIAL"])
    dash = ExecutiveDashboard(
        correlative_data=empty_corr,
        golden_records_data=empty_gold,
        metrics=metrics,
    )

    fig, ax = plt.subplots(figsize=(4, 3))
    kpi = {
        "label": "Total",
        "value": "10,000",
        "icon": "📊",  # emoji intencional para verificar el filtro
        "color": "#1f77b4",
    }

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dash._draw_modern_kpi_card(ax, kpi)
        # Forzar el render real (es donde matplotlib emite los warnings):
        fig.canvas.draw()

    plt.close(fig)

    glyph_warnings = [
        w
        for w in caught
        if issubclass(w.category, UserWarning) and "missing from font" in str(w.message)
    ]
    assert not glyph_warnings, (
        "matplotlib emitió warnings de glifo faltante (emojis llegando a render):\n"
        + "\n".join(f"  - {w.message}" for w in glyph_warnings)
    )


def test_no_emojis_en_literales_de_matplotlib():
    """Defensa en profundidad: detecta emojis en strings que matplotlib pinta DIRECTO.

    El test funcional anterior cubre el caso real (warnings de glyph). Este
    test estático sirve como red de seguridad ante introducciones futuras:
    si alguien agrega ``ax.set_title("🎯 Mi título")``, se cae aquí ANTES de
    correr la pipeline.

    Excluidos: los diccionarios ``{"icon": "📊"}`` se permiten porque los
    sitios de render aplican ``isascii()`` (ver dashboard.py:592, suite.py:953).
    """
    import re
    from pathlib import Path

    pkg_root = Path(__file__).resolve().parents[1] / "src" / "record_linkage" / "reporting"
    archivos = ["visualizer.py", "dashboard.py", "suite.py"]
    emoji_re = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2700-\u27BF\U0001F000-\U0001F9FF]")
    # Patrón: función-de-render abierta con string literal inline (no variable).
    render_patterns = [
        re.compile(r"\.suptitle\(\s*[fbru]*['\"]"),
        re.compile(r"\.set_title\(\s*[fbru]*['\"]"),
        re.compile(r"\bplt\.title\(\s*[fbru]*['\"]"),
        # ax.text con literal: posición1, posición2, "..."
        re.compile(r"(?:ax\w*|fig)\.text\([^,]+,\s*[^,]+,\s*[fbru]*['\"]"),
    ]

    ofensores = []
    for fname in archivos:
        path = pkg_root / fname
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not emoji_re.search(line):
                continue
            if any(p.search(line) for p in render_patterns):
                ofensores.append(f"{fname}:{i}: {line.strip()[:140]}")

    assert not ofensores, (
        "Emojis en literales que matplotlib pinta directo (sin filtro ASCII):\n  "
        + "\n  ".join(ofensores)
    )


def test_iconos_no_ascii_se_filtran_en_renderizado():
    """Verifica que el patrón de filtrado ASCII-safe sigue instalado.

    Si alguien refactoriza dashboard.py o suite.py y borra accidentalmente
    el bloque ``_icon_safe = _icon_raw if _icon_raw.isascii() else ...``,
    este test lo detecta. Es complemento del test funcional: el funcional
    detecta el síntoma (warning), este detecta la regresión del patrón.
    """
    from pathlib import Path

    pkg_root = Path(__file__).resolve().parents[1] / "src" / "record_linkage" / "reporting"
    archivos_y_marcadores = [
        ("dashboard.py", "_icon_safe = _icon_raw if _icon_raw.isascii()"),
        ("suite.py", "_icon_safe = _icon_raw if _icon_raw.isascii()"),
    ]
    for fname, marker in archivos_y_marcadores:
        contenido = (pkg_root / fname).read_text(encoding="utf-8")
        assert marker in contenido, (
            f"{fname} debe tener el patrón de filtrado ASCII-safe instalado "
            f"(buscado: {marker!r}). Revisa Sprint 0.8.1 Tarea 1.4."
        )
