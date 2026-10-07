"""F2.14 — ``GroundTruthGenerator`` restaurado sin ``optimization/``.

Enrique (2026-10-07): «Sí es necesario el GroundTruth, entonces también
conservarlo. No porque no se use seguido se debe eliminar.» F2.8 lo había
retirado junto con ``optimization/``; vuelve a ``evaluation/ground_truth.py``
sin esa dependencia, vectorizado (sin ``iterrows``) y reproducible por semilla.

Las empresas son inventadas (regla del preámbulo: ningún nombre real).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from record_linkage.evaluation import GroundTruthEvaluator as EvaluadorDesdePaquete
from record_linkage.evaluation.ground_truth import (
    CLASES,
    COLUMNAS_SALIDA,
    ConfigGroundTruth,
    GroundTruthEvaluator,
    GroundTruthGenerator,
)

# ── Base inventada ─────────────────────────────────────────────────────────
# Cuatro tipos de evidencia, para que cada clase del muestreo tenga de dónde
# salir: grupos con el mismo NIT (fáciles), nombres casi iguales con NIT
# distinto (difíciles), nombres parecidos a medias (ambiguos) y nombres que no
# se parecen en nada desde dos fuentes (negativos).
_MISMO_NIT = [
    ("900100001", "COMERCIALIZADORA ANDINA DEL SUR SAS", "COMERCIALIZADORA ANDINA DEL SUR S A S"),
    ("900100002", "INVERSIONES PRADERA VERDE LTDA", "INVERSIONES PRADERA VERDE LIMITADA"),
    ("900100003", "TEXTILES DEL ALTIPLANO SA", "TEXTILES DEL ALTIPLANO S.A."),
    ("900100004", "AGROINDUSTRIAS RIO CLARO SAS", "AGROINDUSTRIAS RIO CLARO"),
    ("900100005", "LOGISTICA PUERTO AZUL SAS", "LOGISTICA PUERTO AZUL"),
    ("900100006", "CAFETALERA MONTE ALTO SA", "CAFETALERA MONTE ALTO"),
]
_NIT_DISTINTO_NOMBRE_CASI_IGUAL = [
    (
        "800200001",
        "800200002",
        "DISTRIBUIDORA LA ESTRELLA LTDA",
        "DISTRIBUIDORA LA ESTRELLA LIMITADA",
    ),
    ("800200003", "800200004", "PANIFICADORA EL TRIGAL SAS", "PANIFICADORA EL TRIGAL S A S"),
    ("800200005", "800200006", "CONSTRUCCIONES VALLE SOL SAS", "CONSTRUCCIONES VALLE SOL"),
    ("800200007", "800200008", "FLORES DEL AMANECER SAS", "FLORES DEL AMANECER"),
    ("800200009", "800200010", "METALMECANICA NORTE SAS", "METALMECANICA NORTE S A S"),
]
# Medidos con token_set_ratio: 65,5 a 69,1 (banda ambigua [60, 85)). Cambiar una
# palabra basta para salir de la banda: ``FERRETERIA EL MARTILLO`` vs
# ``FERRETERIA EL TORNILLO`` da 88,5 porque el comparador perdona un token.
_AMBIGUOS = [
    ("700300001", "700300002", "FERRETERIA EL MARTILLO SAS", "FERRETERIA LA TUERCA DEL NORTE"),
    ("700300003", "700300004", "TRANSPORTES RAPIDO DEL CARIBE", "TRANSPORTES UNIDOS DE OCCIDENTE"),
    ("700300005", "700300006", "LACTEOS LA VAQUITA FELIZ", "LA VAQUITA DISTRIBUCIONES"),
    ("700300007", "700300008", "CALZADO PASO FIRME LTDA", "CALZADO Y CUEROS DEL SUR LTDA"),
    ("700300009", "700300010", "MUEBLES ROBLE DORADO SAS", "MUEBLES Y COLCHONES CENTRAL SAS"),
]
_SUELTOS = [
    ("600400001", "ZAPATERIA QUINTA AVENIDA"),
    ("600400002", "HELADOS POLAR NORTE"),
    ("600400003", "VIDRIOS TEMPLADOS OCCIDENTE"),
    ("600400004", "JARDINERIA BOSQUE ENCANTADO"),
    ("600400005", "RELOJERIA TIEMPO EXACTO"),
    ("600400006", "PESCADERIA MAR ADENTRO"),
    ("600400007", "TAPICERIA SOFA REAL"),
    ("600400008", "OPTICA VISION CLARA"),
]


def _base_inventada() -> pd.DataFrame:
    filas: list[dict[str, str | None]] = []
    for nit, a, b in _MISMO_NIT:
        filas.append({"NIT": nit, "RAZON_SOCIAL": a, "SRC": "RUES"})
        filas.append({"NIT": nit, "RAZON_SOCIAL": b, "SRC": "CRM"})
    for nit_a, nit_b, a, b in _NIT_DISTINTO_NOMBRE_CASI_IGUAL + _AMBIGUOS:
        filas.append({"NIT": nit_a, "RAZON_SOCIAL": a, "SRC": "RUES"})
        filas.append({"NIT": nit_b, "RAZON_SOCIAL": b, "SRC": "CRM"})
    for i, (nit, nombre) in enumerate(_SUELTOS):
        filas.append({"NIT": nit, "RAZON_SOCIAL": nombre, "SRC": "RUES" if i % 2 else "CRM"})
    # Un registro sin identificador: no puede entrar a los positivos fáciles.
    filas.append({"NIT": None, "RAZON_SOCIAL": "SIN NIT CONOCIDO SAS", "SRC": "CRM"})
    marco = pd.DataFrame(filas)
    marco["NIT_OK"] = marco["NIT"]
    marco["NOMBRE_LIMPIO"] = marco["RAZON_SOCIAL"]
    return marco


@pytest.fixture(scope="module")
def base() -> pd.DataFrame:
    return _base_inventada()


# ── Criterio de aceptación de la especificación ───────────────────────────


def test_import_conjunto_desde_ground_truth() -> None:
    """``from record_linkage.evaluation.ground_truth import GroundTruthGenerator,
    GroundTruthEvaluator`` funciona y el evaluador es el ÚNICO de la librería."""
    from record_linkage.evaluation.evaluador_verdad import (
        GroundTruthEvaluator as Implementacion,
    )

    assert GroundTruthEvaluator is Implementacion
    assert EvaluadorDesdePaquete is Implementacion
    assert GroundTruthGenerator is not None


def test_muestra_estratificada_con_las_cuatro_clases(base: pd.DataFrame) -> None:
    generador = GroundTruthGenerator()
    muestra = generador.create_intelligent_sample(base, n_samples=20)

    assert list(muestra.columns) == list(COLUMNAS_SALIDA)
    assert set(muestra["CASE_TYPE"]) == set(CLASES), muestra["CASE_TYPE"].value_counts()
    assert muestra["IS_MATCH"].isna().all(), "IS_MATCH se deja vacío para la revisión humana"
    # Ningún par se repite ni compara un registro consigo mismo.
    assert (muestra["ID_1"] != muestra["ID_2"]).all()
    llaves = muestra[["ID_1", "ID_2"]].apply(frozenset, axis=1)
    assert llaves.is_unique


def test_cada_clase_cumple_su_definicion(base: pd.DataFrame) -> None:
    config = ConfigGroundTruth()
    muestra = GroundTruthGenerator(config).create_intelligent_sample(base, n_samples=20)
    por_clase = {clase: grupo for clase, grupo in muestra.groupby("CASE_TYPE")}

    faciles = por_clase["easy_positive"]
    assert (faciles["NIT_1"] == faciles["NIT_2"]).all()
    assert (faciles["NIT_SIMILARITY"] == 1.0).all()

    dificiles = por_clase["difficult_positive"]
    assert (dificiles["NAME_SIMILARITY"] >= config.umbral_dificil).all()
    assert (dificiles["NIT_1"] != dificiles["NIT_2"]).all()

    ambiguos = por_clase["ambiguous"]
    assert (ambiguos["NAME_SIMILARITY"] >= config.umbral_ambiguo).all()
    assert (ambiguos["NAME_SIMILARITY"] < config.umbral_dificil).all()

    negativos = por_clase["easy_negative"]
    assert (negativos["NAME_SIMILARITY"] < config.umbral_ambiguo).all()
    assert (negativos["NIT_1"] != negativos["NIT_2"]).all()


def test_negativos_claros_usan_el_nit_canonico() -> None:
    """Hallazgo de revisión (ronda 1): los negativos verificaban el NIT crudo.

    Con NIT crudo distinto (DV, guion) pero el mismo ``NIT_OK`` el par es la
    misma empresa según el identificador canónico y NO puede ser un negativo
    claro. Nombres sin parecido para que la banda de similitud no lo filtre.
    """
    filas = [
        ("900100001-5", "900100001", "ZAPATERIA QUINTA AVENIDA", "RUES"),
        ("900100001", "900100001", "HELADOS POLAR NORTE", "CRM"),
        ("900100002-3", "900100002", "VIDRIOS TEMPLADOS OCCIDENTE", "RUES"),
        ("900100002", "900100002", "JARDINERIA BOSQUE ENCANTADO", "CRM"),
        ("900100003-1", "900100003", "RELOJERIA TIEMPO EXACTO", "RUES"),
        ("900100003", "900100003", "PESCADERIA MAR ADENTRO", "CRM"),
    ]
    marco = pd.DataFrame(filas, columns=["NIT", "NIT_OK", "RAZON_SOCIAL", "SRC"])
    marco["NOMBRE_LIMPIO"] = marco["RAZON_SOCIAL"]
    config = ConfigGroundTruth(
        proporciones={
            "easy_positive": 0.0,
            "difficult_positive": 0.0,
            "ambiguous": 0.0,
            "easy_negative": 1.0,
        }
    )
    muestra = GroundTruthGenerator(config).create_intelligent_sample(marco, n_samples=6)
    negativos = muestra.loc[muestra["CASE_TYPE"] == "easy_negative"]
    assert not negativos.empty, "hay pares con NIT canónico distinto de sobra"
    assert (negativos["NIT_SIMILARITY"] < 1.0).all(), negativos[
        ["NIT_1", "NIT_2", "NIT_SIMILARITY"]
    ]


def test_reproducible_por_semilla(base: pd.DataFrame) -> None:
    a = GroundTruthGenerator(ConfigGroundTruth(semilla=7)).create_intelligent_sample(base, 20)
    b = GroundTruthGenerator(ConfigGroundTruth(semilla=7)).create_intelligent_sample(base, 20)
    c = GroundTruthGenerator(ConfigGroundTruth(semilla=8)).create_intelligent_sample(base, 20)
    pd.testing.assert_frame_equal(a, b)
    assert not a[["ID_1", "ID_2"]].equals(c[["ID_1", "ID_2"]])


def test_proporciones_respetadas(base: pd.DataFrame) -> None:
    """Con oferta de sobra, cada clase recibe ``int(n · proporción)`` pares."""
    config = ConfigGroundTruth(
        proporciones={
            "easy_positive": 0.25,
            "difficult_positive": 0.25,
            "ambiguous": 0.25,
            "easy_negative": 0.25,
        }
    )
    muestra = GroundTruthGenerator(config).create_intelligent_sample(base, n_samples=12)
    assert muestra["CASE_TYPE"].value_counts().to_dict() == {clase: 3 for clase in CLASES}


def test_sin_identificador_ni_fuente_no_rompe() -> None:
    """Sin NIT_OK no hay positivos fáciles; sin SRC los negativos salen al azar."""
    marco = pd.DataFrame(
        {
            "RAZON_SOCIAL": ["ALFA BETA GAMMA", "ALFA BETA GAMMA SAS", "OMEGA DELTA", "PI RHO"],
            "NOMBRE_LIMPIO": ["ALFA BETA GAMMA", "ALFA BETA GAMMA SAS", "OMEGA DELTA", "PI RHO"],
        }
    )
    muestra = GroundTruthGenerator().create_intelligent_sample(marco, n_samples=8)
    assert "easy_positive" not in set(muestra["CASE_TYPE"])
    assert "difficult_positive" in set(muestra["CASE_TYPE"])
    assert "easy_negative" in set(muestra["CASE_TYPE"])


def test_config_invalida_falla_rapido() -> None:
    with pytest.raises(ValueError, match="proporciones"):
        ConfigGroundTruth(proporciones={"easy_positive": 1.0})
    with pytest.raises(ValueError, match="umbral"):
        ConfigGroundTruth(umbral_ambiguo=0.9, umbral_dificil=0.8)
    with pytest.raises(ValueError, match="n_samples"):
        GroundTruthGenerator().create_intelligent_sample(_base_inventada(), n_samples=0)


def test_guardar_csv_y_xlsx(base: pd.DataFrame, tmp_path: Path) -> None:
    generador = GroundTruthGenerator()
    csv = tmp_path / "verdad.csv"
    xlsx = tmp_path / "verdad.xlsx"
    muestra = generador.create_intelligent_sample(base, n_samples=12, output_path=csv)
    generador.guardar(muestra, xlsx)

    leido = pd.read_csv(csv)
    assert list(leido.columns) == list(COLUMNAS_SALIDA)
    assert len(leido) == len(muestra)
    assert list(pd.read_excel(xlsx).columns) == list(COLUMNAS_SALIDA)


def test_salida_la_consume_quality_evaluator(base: pd.DataFrame, tmp_path: Path) -> None:
    """El archivo revisado (IS_MATCH lleno) es lo que ``QualityEvaluator`` carga."""
    from record_linkage.evaluation.quality import QualityEvaluator

    muestra = GroundTruthGenerator().create_intelligent_sample(base, n_samples=12)
    muestra["IS_MATCH"] = (muestra["CASE_TYPE"] != "easy_negative").astype(int)
    ruta = tmp_path / "revisado.csv"
    GroundTruthGenerator().guardar(muestra, ruta)

    evaluador = QualityEvaluator(str(ruta))
    assert len(evaluador.true_matches) + len(evaluador.true_non_matches) == len(muestra)
