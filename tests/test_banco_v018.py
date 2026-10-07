"""Banco de pruebas y comparador de corridas (v0.18.0).

El banco es el instrumento con el que se acepta o se rechaza toda mejora de
la librería. Un instrumento sin calibrar no sirve para nada: estas pruebas
verifican que las métricas dan lo que deben dar en casos donde la respuesta
se conoce a mano, que el reparto en pliegues no rompe grupos, y que el
veredicto de comparación es el esperado en cada dirección.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from record_linkage.evaluation.banco import (
    EspecificacionBanco,
    MuestreadorRecursos,
    _pares_de_grupos,
    _seleccionar_pliegue,
    bcubed,
    cargar_referencia,
    evaluar_calidad,
    huella_particion,
)
from record_linkage.evaluation.comparador import Umbrales, cargar_corrida, comparar

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "data" / "ground_truth" / "ground_truth_grande.csv"


# ── Métricas ──────────────────────────────────────────────────────────────


def test_bcubed_de_una_particion_perfecta_es_uno() -> None:
    etiquetas = np.array([1, 1, 2, 2, 3])
    assert bcubed(etiquetas, etiquetas) == (1.0, 1.0, 1.0)


def test_bcubed_castiga_unir_todo() -> None:
    precision, recall, _ = bcubed(np.array([1, 1, 2, 2]), np.array([9, 9, 9, 9]))
    assert recall == 1.0 and precision == 0.5


def test_bcubed_castiga_partir_todo() -> None:
    precision, recall, _ = bcubed(np.array([1, 1, 1, 1]), np.array([1, 2, 3, 4]))
    assert precision == 1.0 and recall == 0.25


def test_bcubed_exige_mismo_largo() -> None:
    with pytest.raises(ValueError, match="longitudes distintas"):
        bcubed(np.array([1, 2]), np.array([1]))


def test_bcubed_rechaza_entrada_vacia() -> None:
    with pytest.raises(ValueError, match="no hay registros"):
        bcubed(np.array([]), np.array([]))


def test_pares_de_grupos_emite_las_combinaciones_correctas() -> None:
    assert sorted(_pares_de_grupos(np.array([1, 1, 2, 1, 2]))) == [
        (0, 1),
        (0, 3),
        (1, 3),
        (2, 4),
    ]


def test_pares_de_grupos_ignora_los_singulares() -> None:
    assert _pares_de_grupos(np.array([1, 2, 3])) == set()


def test_evaluar_calidad_sobre_una_particion_perfecta() -> None:
    referencia = pd.DataFrame(
        {
            "ID_GROUP": ["a", "a", "b", "b"],
            "REGIMEN": ["CON_NIT"] * 4,
            "CASO": ["positivo_con_nit"] * 4,
        }
    )
    metricas = evaluar_calidad(referencia, np.array([10, 10, 20, 20]))
    assert (metricas.precision, metricas.recall, metricas.f1) == (1.0, 1.0, 1.0)
    assert metricas.fp == 0 and metricas.fn == 0


def test_evaluar_calidad_cuenta_los_falsos_positivos_sobre_negativos() -> None:
    referencia = pd.DataFrame(
        {
            "ID_GROUP": ["a", "b"],
            "REGIMEN": ["SIN_NIT", "SIN_NIT"],
            "CASO": ["positivo_sin_nit", "negativo_generico"],
        }
    )
    metricas = evaluar_calidad(referencia, np.array([1, 1]))
    assert metricas.fp == 1 and metricas.fp_que_tocan_negativo == 1


def test_evaluar_calidad_exige_las_columnas_de_estrato() -> None:
    with pytest.raises(ValueError, match="faltan columnas"):
        evaluar_calidad(pd.DataFrame({"ID_GROUP": ["a"]}), np.array([1]))


# ── Huella ────────────────────────────────────────────────────────────────


def test_la_huella_no_cambia_al_renombrar_los_grupos() -> None:
    """Los identificadores de grupo son arbitrarios; la partición no."""
    a = huella_particion([0, 1, 2, 3], ["x", "x", "y", "y"])
    b = huella_particion([0, 1, 2, 3], [77, 77, 99, 99])
    assert a == b


def test_la_huella_cambia_si_un_registro_cambia_de_grupo() -> None:
    a = huella_particion([0, 1, 2], ["x", "x", "y"])
    b = huella_particion([0, 1, 2], ["x", "y", "y"])
    assert a != b


# ── Pliegues ──────────────────────────────────────────────────────────────


def test_los_pliegues_no_parten_grupos() -> None:
    """Partir por fila rompería grupos verdaderos y falsearía el recall."""
    referencia = cargar_referencia(DATOS)
    vistos: dict[str, int] = {}
    for pliegue in range(3):
        parte = _seleccionar_pliegue(referencia, pliegue, 3)
        for grupo in parte["ID_GROUP"].unique():
            assert grupo not in vistos, f"el grupo {grupo} aparece en dos pliegues"
            vistos[grupo] = pliegue
    assert len(vistos) == referencia["ID_GROUP"].nunique()


def test_los_pliegues_son_estables_entre_ejecuciones() -> None:
    referencia = cargar_referencia(DATOS)
    primera = set(_seleccionar_pliegue(referencia, 1, 3)["ID_REGISTRO"])
    segunda = set(_seleccionar_pliegue(referencia, 1, 3)["ID_REGISTRO"])
    assert primera == segunda


def test_un_pliegue_vacio_falla_rapido() -> None:
    referencia = cargar_referencia(DATOS).head(1)
    with pytest.raises(ValueError, match="quedó vacío"):
        for pliegue in range(50):
            _seleccionar_pliegue(referencia, pliegue, 50)


# ── Conjunto de referencia ────────────────────────────────────────────────


def test_el_conjunto_de_referencia_existe_y_tiene_lo_necesario() -> None:
    referencia = cargar_referencia(DATOS)
    assert len(referencia) == 12_427
    assert referencia["ID_GROUP"].nunique() == 3_486
    assert set(referencia["REGIMEN"]) == {"CON_NIT", "SIN_NIT"}


def test_un_archivo_inexistente_falla_con_su_ruta(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="conjunto de referencia"):
        cargar_referencia(tmp_path / "no_existe.csv")


def test_un_archivo_sin_columnas_falla_nombrandolas(tmp_path: Path) -> None:
    ruta = tmp_path / "malo.csv"
    ruta.write_text("A,B\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no tiene"):
        cargar_referencia(ruta)


# ── Especificación ────────────────────────────────────────────────────────


def test_la_etiqueta_debe_servir_como_nombre_de_archivo() -> None:
    with pytest.raises(ValueError, match="alfanumérica"):
        EspecificacionBanco(etiqueta="con/barra", datos=DATOS)


def test_el_pliegue_debe_estar_en_rango() -> None:
    with pytest.raises(ValueError, match=r"pliegue debe estar en"):
        EspecificacionBanco(etiqueta="x", datos=DATOS, pliegue=5, pliegues=3)


# ── Muestreador de recursos ───────────────────────────────────────────────


def test_el_muestreador_registra_al_menos_una_lectura() -> None:
    with MuestreadorRecursos(intervalo=0.01) as muestreador:
        _ = [0] * 100_000
    assert muestreador.pico_mib >= muestreador.inicial_mib > 0
    assert muestreador.muestras >= 1


def test_el_muestreador_cierra_su_hilo_aunque_falle_el_bloque() -> None:
    muestreador = MuestreadorRecursos(intervalo=0.01)
    with pytest.raises(RuntimeError), muestreador:
        raise RuntimeError("falla a mitad")
    assert not muestreador._hilo.is_alive()


def test_el_intervalo_debe_ser_positivo() -> None:
    with pytest.raises(ValueError, match="intervalo debe ser > 0"):
        MuestreadorRecursos(intervalo=0)


# ── Comparador de corridas ────────────────────────────────────────────────


def _corrida(etiqueta: str, **kwargs) -> dict:
    calidad = {
        "precision": 0.9,
        "recall": 0.9,
        "f1": 0.9,
        "b3_f1": 0.9,
        "fp_que_tocan_negativo": 0,
    }
    recursos = {"segundos_total": 100.0, "rss_pico_mib": 1000.0}
    calidad.update({k: v for k, v in kwargs.items() if k in calidad})
    recursos.update({k: v for k, v in kwargs.items() if k in recursos})
    return {"etiqueta": etiqueta, "calidad": calidad, "recursos": recursos, "huella": "h"}


def test_una_corrida_identica_pasa() -> None:
    informe = comparar(_corrida("a"), _corrida("b"))
    assert informe.pasa and informe.huella_igual


def test_una_caida_de_f1_falla() -> None:
    informe = comparar(_corrida("a"), _corrida("b", f1=0.89))
    assert not informe.pasa
    assert any(v.metrica == "f1" and not v.pasa for v in informe.veredictos)


def test_una_mejora_de_calidad_pasa() -> None:
    assert comparar(_corrida("a"), _corrida("b", f1=0.95, precision=0.95)).pasa


def test_el_tiempo_tolera_ruido_pero_no_una_regresion_grande() -> None:
    assert comparar(_corrida("a"), _corrida("b", segundos_total=115.0)).pasa
    assert not comparar(_corrida("a"), _corrida("b", segundos_total=130.0)).pasa


def test_los_umbrales_se_pueden_relajar_explicitamente() -> None:
    laxo = Umbrales(caida_maxima_f1=0.02)
    assert comparar(_corrida("a"), _corrida("b", f1=0.885), laxo).pasa


def test_los_umbrales_rechazan_valores_absurdos() -> None:
    with pytest.raises(ValueError, match="no puede ser negativo"):
        Umbrales(caida_maxima_f1=-0.1)


def test_una_corrida_inexistente_nombra_las_disponibles(tmp_path: Path) -> None:
    (tmp_path / "corrida_existe.json").write_text(json.dumps(_corrida("existe")), encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="existe"):
        cargar_corrida(tmp_path, "no_esta")


def test_el_informe_dice_pasa_o_falla() -> None:
    assert "VEREDICTO: PASA" in comparar(_corrida("a"), _corrida("b")).resumen()
    assert "VEREDICTO: FALLA" in comparar(_corrida("a"), _corrida("b", f1=0.5)).resumen()


def test_comparar_corridas_de_conjuntos_distintos_es_un_error() -> None:
    """Dos corridas sobre CSV distintos no son comparables: hay que decirlo, no dar PASA.

    Antes `--comparar base_f0 f1_a` dio PASA con la huella «distinta» porque
    f1_a se corrió sin `--datos` (ground_truth_grande en vez del benchmark
    institucional): todas las métricas «mejoraban» contra otro conjunto.
    """
    base, nueva = _corrida("a"), _corrida("b")
    base["especificacion"] = {"datos": "data/benchmark/benchmark_institucional.csv.gz"}
    nueva["especificacion"] = {"datos": "data/ground_truth/ground_truth_grande.csv"}
    with pytest.raises(ValueError, match="conjuntos distintos") as exc:
        comparar(base, nueva)
    assert "benchmark_institucional" in str(exc.value) and "ground_truth_grande" in str(exc.value)
    assert "--datos" in str(exc.value)


def test_comparar_acepta_el_mismo_conjunto_o_corridas_sin_especificacion() -> None:
    base, nueva = _corrida("a"), _corrida("b")
    base["especificacion"] = nueva["especificacion"] = {"datos": "x.csv"}
    assert comparar(base, nueva).pasa
    base.pop("especificacion")
    assert comparar(base, nueva).pasa
