"""La consolidación por NIT de L5 no puede fallar en silencio (tarea F1.2).

Hasta esta tarea ``Orchestrator._run_L5`` envolvía
``consolidate_groups_by_nit_balanced`` en ``except Exception`` que escribía una
advertencia («Consolidación falló, usando resultados directos»), seguía con el
golden y la correlativa SIN consolidar, marcaba ``L5_golden`` como ``DONE`` en
``manifest.json`` y dejaba que L6 publicara una entrega degradada. Regla 6 del
plan: nada se repara en silencio; si un entregable falta, la corrida falla con
un mensaje accionable.

Qué exige esta prueba
---------------------
Con ``monkeypatch`` se hace que ``consolidate_groups_by_nit_balanced`` —el
nombre que importa ``pipeline.orchestrator``— lance una excepción, y se corre
``linkage()`` sobre 30 filas de empresas inventadas. Entonces:

1. ``linkage()`` levanta ``ConsolidacionNitError`` (``pipeline.errores``), con
   la excepción original encadenada en ``__cause__`` (``raise ... from e``);
2. el mensaje lleva las tres secciones del formato de la casa: «Qué pasó»,
   «Por qué importa» y «Qué hacer»;
3. ``manifest.json`` del ``work_dir`` NO registra ``L5_golden`` con estado
   ``DONE`` (la fase no se persiste como completada) y L5 no deja parquets.

Datos: solo empresas inventadas; ningún nombre real entra al repositorio.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from record_linkage import linkage
from record_linkage.pipeline import orchestrator as modulo_orquestador
from record_linkage.pipeline.errores import ConsolidacionNitError, ErrorPipeline

_GIROS = (
    "ANDARIEGA",
    "BRUMOSA",
    "CALICANTO",
    "DESTELLO",
    "ESPIRAL",
    "FUMAROLA",
    "GARABATO",
    "HOJARASCA",
    "ICTERIA",
    "JARIPEO",
)
_RUBROS = ("COMERCIALIZADORA", "LOGISTICA", "INGENIERIA")
_CIUDADES = ("BOGOTA", "MEDELLIN", "CALI")


def _fuente_sintetica(filas: int = 30) -> pd.DataFrame:
    """30 registros inventados: 10 empresas × 3 variantes de escritura.

    Cada empresa comparte NIT entre sus variantes para que L2…L5 tengan
    trabajo real (grupos de tamaño 3) y la consolidación por NIT sea un paso
    que de verdad se ejecuta.
    """
    nits: list[str] = []
    nombres: list[str] = []
    ciudades: list[str] = []
    for i in range(filas):
        empresa = i % len(_GIROS)
        variante = i // len(_GIROS)
        giro = _GIROS[empresa]
        rubro = _RUBROS[empresa % len(_RUBROS)]
        sufijo = ("SAS", "S.A.S.", "S A S")[variante % 3]
        nits.append(f"9{empresa:02d}{(empresa * 7919) % 100000:05d}4")
        nombres.append(f"{rubro} {giro} {sufijo}")
        ciudades.append(_CIUDADES[empresa % len(_CIUDADES)])
    return pd.DataFrame({"NIT": nits, "RAZON_SOCIAL": nombres, "CIUDAD": ciudades})


def _leer_manifiesto(work_dir: Path) -> dict:
    archivo = work_dir / "manifest.json"
    assert archivo.exists(), "L1…L4 deben haber corrido y persistido manifest.json"
    with archivo.open(encoding="utf-8") as flujo:
        return json.load(flujo)


def test_la_fuente_sintetica_tiene_30_filas_y_10_empresas():
    df = _fuente_sintetica()
    assert len(df) == 30
    assert df["NIT"].nunique() == 10
    assert df["RAZON_SOCIAL"].nunique() == 30


def test_consolidacion_por_nit_rota_hace_fallar_linkage(monkeypatch, tmp_path):
    """Si la consolidación revienta, linkage() falla con ConsolidacionNitError."""

    def _consolidacion_rota(*_args, **_kwargs):
        raise RuntimeError("fallo simulado de la consolidación por NIT")

    monkeypatch.setattr(
        modulo_orquestador, "consolidate_groups_by_nit_balanced", _consolidacion_rota
    )
    work_dir = tmp_path / "corrida"

    with pytest.raises(ConsolidacionNitError) as info:
        linkage(
            sources={"FUENTE_A": _fuente_sintetica()},
            work_dir=str(work_dir),
            skip_reporting=True,
        )

    error = info.value
    # Encadenamiento explícito (`raise ... from e`): la causa original no se pierde.
    assert isinstance(error.__cause__, RuntimeError)
    assert "fallo simulado" in str(error.__cause__)
    assert isinstance(error, ErrorPipeline)

    mensaje = str(error)
    for seccion in ("Qué pasó", "Por qué importa", "Qué hacer"):
        assert seccion in mensaje, f"Falta la sección «{seccion}» en: {mensaje}"
    # La causa se cita en el mensaje para que el usuario no tenga que ir al traceback.
    assert "fallo simulado" in mensaje

    # El manifiesto no debe registrar L5 como completada.
    manifiesto = _leer_manifiesto(work_dir)
    assert manifiesto.get("L5_golden", {}).get("status") != "DONE"
    assert manifiesto.get("L6_reporting", {}).get("status") != "DONE"
    # Y L4 sí (la corrida llegó hasta L5): descarta un fallo anterior disfrazado.
    assert manifiesto["L4_clustering"]["status"] == "DONE"

    # L5 no deja entregables a medias que una corrida posterior pueda reutilizar.
    dir_l5 = work_dir / "L5_golden"
    assert not (dir_l5 / "golden.parquet").exists()
    assert not (dir_l5 / "correlative.parquet").exists()


def test_consolidacion_sana_sigue_entregando(tmp_path):
    """Sin sabotaje, el mismo conjunto llega a L5 y L5_golden queda DONE."""
    work_dir = tmp_path / "corrida_sana"
    res = linkage(
        sources={"FUENTE_A": _fuente_sintetica()},
        work_dir=str(work_dir),
        skip_reporting=True,
    )
    assert len(res["correlative"]) == 30
    assert len(res["golden"]) <= 30
    assert _leer_manifiesto(work_dir)["L5_golden"]["status"] == "DONE"


def test_consolidacion_nit_error_es_especifica_y_encadena_causa():
    """La excepción es específica (no un RuntimeError genérico) y formatea las 3 secciones."""
    causa = KeyError("NIT_FINAL")
    error = ConsolidacionNitError.desde_causa(causa, n_golden=12, n_correlativa=30)
    assert isinstance(error, ErrorPipeline)
    assert isinstance(error, RuntimeError)
    mensaje = str(error)
    assert "Qué pasó" in mensaje
    assert "Por qué importa" in mensaje
    assert "Qué hacer" in mensaje
    assert "KeyError" in mensaje
    assert "12" in mensaje and "30" in mensaje
