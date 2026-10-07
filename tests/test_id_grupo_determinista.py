"""``ID_GRUPO`` determinista por contenido (F2.3).

Antes, ``ID_GRUPO`` era la posición mínima del grupo (``engine/clusterer.py``):
cambiaba con cualquier fila nueva o reordenada. Ahora es la posición
lexicográfica del menor ``ID_REGISTRO`` del grupo (entero denso, estable
mediante ``factorize`` ordenado): reordenar la entrada no cambia la partición
ni los ids. La huella del banco no depende del valor del id, así que sigue
igual (``test_identificadores_registro.test_huella_no_depende_del_valor_del_id``
y la corrida del banco en el informe de F2.3).
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

import record_linkage as rl
from record_linkage.engine.clusterer import OptimizedClusterer
from record_linkage.resultado import ResultadoLinkage
from record_linkage.salida.completar import completar_correlativa
from record_linkage.salida.identificadores_registro import id_grupo_por_clave_minima

# ── L4: el clusterer con claves por registro ─────────────────────────────


def _perfil() -> dict[str, Any]:
    return {
        "clustering_batch_size": 50_000,
        "clustering_cache_size": 10_000,
        "use_strict_clusters": False,
        "max_nit_distance": 2,
    }


def _grafo(seed: int, n: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    a = rng.integers(0, n, n)
    b = rng.integers(0, n, n)
    m = a != b
    return pd.DataFrame({"idx_0": a[m].astype(np.int64), "idx_1": b[m].astype(np.int64)})


@pytest.mark.parametrize("camino", ["RUES_LINKER_FORCE_MEMORY", "RUES_LINKER_FORCE_DISK"])
def test_clusterer_sin_claves_conserva_el_nodo_minimo(camino: str, monkeypatch) -> None:
    """Sin claves (llamadores heredados), el representante sigue siendo el nodo mínimo."""
    monkeypatch.setenv(camino, "1")
    scored = pd.DataFrame({"idx_0": [0, 1, 2], "idx_1": [1, 2, 3]})
    mapping = OptimizedClusterer(_perfil()).cluster_entities(
        scored, pd.DataFrame({"NIT_OK": [None] * 5}), 5
    )
    assert mapping == {0: 0, 1: 0, 2: 0, 3: 0, 4: 4}


@pytest.mark.parametrize("camino", ["RUES_LINKER_FORCE_MEMORY", "RUES_LINKER_FORCE_DISK"])
def test_clusterer_con_claves_usa_el_menor_id_registro(camino: str, monkeypatch) -> None:
    monkeypatch.setenv(camino, "1")
    scored = pd.DataFrame({"idx_0": [0, 1, 2], "idx_1": [1, 2, 3]})  # {0,1,2,3} y {4}
    claves = np.array(["Z-9", "Z-8", "M-1", "Z-7", "A-0"], dtype=object)
    mapping = OptimizedClusterer(_perfil()).cluster_entities(
        scored, pd.DataFrame({"NIT_OK": [None] * 5}), 5, claves_registro=claves
    )
    # mínimos: {0,1,2,3} → "M-1"; {4} → "A-0". Orden: A-0 (0) < M-1 (1).
    assert mapping == {0: 1, 1: 1, 2: 1, 3: 1, 4: 0}


@pytest.mark.parametrize("seed", range(4))
def test_clusterer_permutar_los_nodos_no_cambia_el_id_por_clave(seed: int, monkeypatch) -> None:
    """Misma entrada en otro orden → misma partición y mismo ID_GRUPO por clave."""
    monkeypatch.setenv("RUES_LINKER_FORCE_MEMORY", "1")
    rng = np.random.default_rng(seed)
    n = 60
    scored = _grafo(seed, n)
    claves = np.array([f"SRC-{rng.integers(0, 10**6):06d}-{i}" for i in range(n)], dtype=object)

    base = OptimizedClusterer(_perfil()).cluster_entities(
        scored, pd.DataFrame({"NIT_OK": [None] * n}), n, claves_registro=claves
    )
    perm = rng.permutation(n)  # nueva posición → posición vieja
    inversa = np.empty(n, dtype=np.int64)
    inversa[perm] = np.arange(n)  # posición vieja → nueva
    scored_perm = pd.DataFrame(
        {"idx_0": inversa[scored["idx_0"]], "idx_1": inversa[scored["idx_1"]]}
    )
    otro = OptimizedClusterer(_perfil()).cluster_entities(
        scored_perm, pd.DataFrame({"NIT_OK": [None] * n}), n, claves_registro=claves[perm]
    )
    por_clave_base = {claves[i]: g for i, g in base.items()}
    por_clave_otro = {claves[perm][i]: g for i, g in otro.items()}
    assert por_clave_base == por_clave_otro


def test_clusterer_con_claves_de_otro_tamano_falla_rapido(monkeypatch) -> None:
    monkeypatch.setenv("RUES_LINKER_FORCE_MEMORY", "1")
    with pytest.raises(ValueError, match="claves_registro"):
        OptimizedClusterer(_perfil()).cluster_entities(
            pd.DataFrame({"idx_0": [0], "idx_1": [1]}),
            pd.DataFrame({"NIT_OK": [None] * 3}),
            3,
            claves_registro=np.array(["a", "b"], dtype=object),
        )


# ── completar: la correlativa y el golden salen recodificados juntos ─────


def _correl_motor() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "SRC": ["RUES", "RUES", "CRM", "CRM"],
            "ORIGINAL_INDEX": [0, 1, 2, 3],
            "CODIGO": ["r-b", "r-a", "c-z", "c-a"],
            "NIT": ["900111222", "900111222", "", ""],
            "RAZON_SOCIAL": ["ALFA SAS", "ALFA S.A.S.", "BETA LTDA", "GAMMA SA"],
            "ID_GRUPO": [5, 5, 9, 2],  # etiquetas del motor (posiciones, saltos)
            "NIT_FINAL": ["900111222", "900111222", "", ""],
            "RAZON_SOCIAL_FINAL": ["ALFA SAS", "ALFA SAS", "BETA LTDA", "GAMMA SA"],
            "NAME_SIMILARITY_SCORE": [1.0, 1.0, 1.0, 1.0],
            "NIT_DISTANCE": [0, 0, 0, 0],
        }
    )


def _golden_motor() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ID_GRUPO": [5, 9, 2, 77],  # 77: huérfana (no está en la correlativa)
            "NIT_FINAL": ["900111222", "", "", ""],
            "RAZON_SOCIAL_FINAL": ["ALFA SAS", "BETA LTDA", "GAMMA SA", "NADIE"],
            "PRIMARY_SOURCE": ["RUES", "CRM", "CRM", "CRM"],
            "SOURCES_LIST": ["RUES", "CRM", "CRM", "CRM"],
            "SOURCES_COUNT": [1, 1, 1, 1],
            "RECORD_COUNT": [2, 1, 1, 1],
            "NAME_VARIATIONS": [2, 1, 1, 1],
            "NIT_VARIATIONS": [1, 0, 0, 0],
            "CONFIDENCE_SCORE": [0.9, 0.5, 0.5, 0.5],
            "CONFIANZA": ["ALTA", "MEDIA", "MEDIA", "MEDIA"],
            "REQUIRES_REVIEW": [False, True, True, True],
            "CREATED_AT": ["x"] * 4,
        }
    )


def test_completar_recodifica_id_grupo_por_menor_id_registro(tmp_path: Path) -> None:
    fuentes = {"RUES": ["NIT", "RAZON_SOCIAL", "CODIGO"], "CRM": ["NIT", "RAZON_SOCIAL", "CODIGO"]}
    correl, golden, reporte = completar_correlativa(
        _correl_motor(), _golden_motor(), tmp_path, "CODIGO", fuentes
    )
    assert correl["ID_REGISTRO"].tolist() == ["RUES-r-b", "RUES-r-a", "CRM-c-z", "CRM-c-a"]
    # mínimos: grupo 5 → "RUES-r-a"; 9 → "CRM-c-z"; 2 → "CRM-c-a". Orden: CRM-c-a, CRM-c-z, RUES-r-a
    assert correl["ID_GRUPO"].tolist() == [2, 2, 1, 0]
    assert pd.api.types.is_integer_dtype(correl["ID_GRUPO"])
    assert reporte.id_grupo["recodificado"] is True
    assert reporte.id_grupo["regla"].startswith("entero")
    assert reporte.id_grupo["n_grupos"] == 3
    # El golden sigue a la correlativa con el MISMO mapa; la huérfana se retira.
    assert golden is not None
    assert pd.api.types.is_integer_dtype(golden["ID_GRUPO"])
    assert dict(zip(golden["RAZON_SOCIAL_FINAL"], golden["ID_GRUPO"], strict=True)) == {
        "GAMMA SA": 0,
        "BETA LTDA": 1,
        "ALFA SAS": 2,
    }
    assert reporte.golden["huerfanas_retiradas"] == 1
    # CONFIANZA de la correlativa es la del grupo, tras la recodificación.
    assert correl["CONFIANZA"].tolist() == ["ALTA", "ALTA", "MEDIA", "MEDIA"]


def test_completar_declara_identidad_cuando_el_motor_ya_venia_por_contenido(
    tmp_path: Path,
) -> None:
    correl = _correl_motor()
    correl["ID_GRUPO"] = [2, 2, 1, 0]  # ya es el rango del menor ID_REGISTRO
    fuentes = {"RUES": ["NIT", "RAZON_SOCIAL", "CODIGO"], "CRM": ["NIT", "RAZON_SOCIAL", "CODIGO"]}
    salida, _, reporte = completar_correlativa(correl, None, tmp_path, "CODIGO", fuentes)
    assert salida["ID_GRUPO"].tolist() == [2, 2, 1, 0]
    assert reporte.id_grupo["recodificado"] is False


# ── Extremo a extremo: linkage() con la entrada reordenada ───────────────

_SIN_NIT = [
    "PANADERIA LA ESPIGA DORADA",
    "TRANSPORTES RIO FRIO",
    "CONFECCIONES LUNA AZUL",
    "AGROPECUARIA EL ROBLE VIEJO",
    "LABORATORIO VIDA SANA",
    "CALZADO PASO FIRME",
]
_SOLOS = ["ASERRIO MONTE ALTO", "OPTICA MIRADA CLARA", "VIVERO FLOR DE LOTO", "HERRAJES EL YUNQUE"]


def _fuentes() -> dict[str, pd.DataFrame]:
    """Dos fuentes inventadas, ambas con ``CODIGO`` único por fila."""
    a: list[dict[str, str]] = []
    b: list[dict[str, str]] = []
    for i in range(8):
        nit = f"{800300000 + i}"
        base = f"FERRETERIA EL TORNILLO {i:02d}"
        a.append(
            {"NIT": nit, "RAZON_SOCIAL": f"{base} SAS", "CIUDAD": "BOGOTA", "CODIGO": f"A{i:03d}"}
        )
        a.append(
            {
                "NIT": nit,
                "RAZON_SOCIAL": f"{base} S.A.S.",
                "CIUDAD": "BOGOTA",
                "CODIGO": f"A{i:03d}X",
            }
        )
        b.append(
            {"NIT": nit, "RAZON_SOCIAL": f"{base} S A S", "CIUDAD": "BOGOTA", "CODIGO": f"B{i:03d}"}
        )
    for i, nombre in enumerate(_SIN_NIT):
        a.append(
            {"NIT": "", "RAZON_SOCIAL": f"{nombre} LTDA", "CIUDAD": "CALI", "CODIGO": f"A9{i:02d}"}
        )
        b.append(
            {
                "NIT": "",
                "RAZON_SOCIAL": f"{nombre} LIMITADA",
                "CIUDAD": "CALI",
                "CODIGO": f"B9{i:02d}",
            }
        )
    for i, nombre in enumerate(_SOLOS[:2]):
        a.append({"NIT": "", "RAZON_SOCIAL": nombre, "CIUDAD": "PASTO", "CODIGO": f"A8{i:02d}"})
    for i, nombre in enumerate(_SOLOS[2:]):
        b.append({"NIT": "", "RAZON_SOCIAL": nombre, "CIUDAD": "CUCUTA", "CODIGO": f"B8{i:02d}"})
    return {"CRM": pd.DataFrame(a, dtype=str), "ADUANAS": pd.DataFrame(b, dtype=str)}


def _barajar(fuentes: dict[str, pd.DataFrame], semilla: int) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(semilla)
    return {n: f.iloc[rng.permutation(len(f))].reset_index(drop=True) for n, f in fuentes.items()}


def _linkage(
    fuentes: dict[str, pd.DataFrame], dir_trabajo: Path, *, skip_reporting: bool = True
) -> ResultadoLinkage:
    with contextlib.redirect_stdout(io.StringIO()):
        return rl.linkage(
            fuentes,
            work_dir=str(dir_trabajo),
            skip_reporting=skip_reporting,
            col_ciudad="CIUDAD",
            col_id="CODIGO",
        )


def _particion(res: ResultadoLinkage) -> frozenset[frozenset[str]]:
    return frozenset(
        frozenset(g["ID_REGISTRO"]) for _, g in res.correlativa.groupby("ID_GRUPO", sort=False)
    )


def _ids(res: ResultadoLinkage) -> dict[str, int]:
    return dict(zip(res.correlativa["ID_REGISTRO"], res.correlativa["ID_GRUPO"], strict=True))


def test_reordenar_la_entrada_no_cambia_la_particion_ni_los_ids(tmp_path: Path) -> None:
    fuentes = _fuentes()
    base = _linkage(fuentes, tmp_path / "base")
    assert base.correlativa["ID_GRUPO"].nunique() > 1
    for semilla in (1, 2):
        otro = _linkage(_barajar(fuentes, semilla), tmp_path / f"barajado_{semilla}")
        assert _particion(otro) == _particion(base)
        assert _ids(otro) == _ids(base)
        assert otro.golden is not None and base.golden is not None
        assert set(otro.golden["ID_GRUPO"]) == set(base.golden["ID_GRUPO"])


def test_id_grupo_del_entregable_es_el_rango_del_menor_id_registro(tmp_path: Path) -> None:
    res = _linkage(_fuentes(), tmp_path / "una")
    correl = res.correlativa
    esperado = id_grupo_por_clave_minima(
        correl["ID_GRUPO"].to_numpy(), correl["ID_REGISTRO"].to_numpy(dtype=object)
    )
    assert correl["ID_GRUPO"].tolist() == esperado.tolist()
    # Denso desde 0 y el golden habla el mismo idioma.
    assert sorted(correl["ID_GRUPO"].unique().tolist()) == list(range(correl["ID_GRUPO"].nunique()))
    assert res.golden is not None
    assert set(res.golden["ID_GRUPO"]) == set(correl["ID_GRUPO"])
    # El motor (L4/L5 en _trabajo/) ya dejó el mismo id: completar no tuvo que recodificar.
    assert res.manifiesto["completar"]["id_grupo"]["recodificado"] is False


# ── L5 renumera (cannot-link, fusión por NIT): _trabajo/ y L6 llevan los ids finales ──


def _fuentes_con_puente() -> dict[str, pd.DataFrame]:
    """Un registro sin NIT une por nombre dos NIT válidos distintos: cannot-link separa en L5."""
    nombre = "COMERCIALIZADORA PRUEBA DEL NORTE S.A.S."
    a = pd.DataFrame(
        [
            {"NIT": "800300001", "RAZON_SOCIAL": nombre, "CIUDAD": "BOGOTA", "CODIGO": "A001"},
            {"NIT": "", "RAZON_SOCIAL": nombre, "CIUDAD": "BOGOTA", "CODIGO": "A002"},
            {
                "NIT": "",
                "RAZON_SOCIAL": "PANADERIA LA ESPIGA DORADA LTDA",
                "CIUDAD": "CALI",
                "CODIGO": "A003",
            },
            {"NIT": "", "RAZON_SOCIAL": "ASERRIO MONTE ALTO", "CIUDAD": "PASTO", "CODIGO": "A004"},
        ],
        dtype=str,
    )
    b = pd.DataFrame(
        [
            {"NIT": "800300002", "RAZON_SOCIAL": nombre, "CIUDAD": "BOGOTA", "CODIGO": "B001"},
            {
                "NIT": "",
                "RAZON_SOCIAL": "PANADERIA LA ESPIGA DORADA LIMITADA",
                "CIUDAD": "CALI",
                "CODIGO": "B002",
            },
            {
                "NIT": "",
                "RAZON_SOCIAL": "VIVERO FLOR DE LOTO",
                "CIUDAD": "CUCUTA",
                "CODIGO": "B003",
            },
        ],
        dtype=str,
    )
    return {"CRM": a, "ADUANAS": b}


def _ids_por_registro(marco: pd.DataFrame) -> dict[tuple[str, int], int]:
    return {
        (str(s), int(i)): int(g)
        for s, i, g in zip(marco["SRC"], marco["ORIGINAL_INDEX"], marco["ID_GRUPO"], strict=True)
    }


def test_trabajo_y_reportes_llevan_los_ids_del_entregable_cuando_l5_renumera(
    tmp_path: Path,
) -> None:
    """Revisión F2.3 (hallazgo 1): cannot-link renumera (``max+1``) y la fusión por NIT
    también; sin recodificar al cierre de L5, ``_trabajo/L5_golden/*.parquet`` y los
    reportes L6 llevaban otros ``ID_GRUPO`` que el entregable (en el banco, 30.112 de
    30.486). La misma regla se aplica al cierre de L5 y ``completar`` no cambia nada."""
    dir_trabajo = tmp_path / "puente"
    res = _linkage(_fuentes_con_puente(), dir_trabajo, skip_reporting=False)
    correl = res.correlativa
    # El cannot-link sí actuó: L4 tenía menos grupos que el entregable.
    l4 = pd.read_parquet(dir_trabajo / "L4_clustering" / "clusters.parquet")
    assert l4["ID_GRUPO"].nunique() < correl["ID_GRUPO"].nunique()
    grupo = dict(zip(correl["ID_REGISTRO"], correl["ID_GRUPO"], strict=True))
    assert len({grupo["CRM-A001"], grupo["CRM-A002"], grupo["ADUANAS-B001"]}) > 1

    # completar no tuvo que recodificar: L5 ya dejó los ids por contenido.
    assert res.manifiesto["completar"]["id_grupo"]["recodificado"] is False
    assert res.manifiesto["completar"]["id_grupo"]["valores_cambiados"] == 0

    # _trabajo/L5_golden: mismo id por registro y mismo conjunto en el golden.
    l5_correl = pd.read_parquet(dir_trabajo / "L5_golden" / "correlative.parquet")
    assert _ids_por_registro(l5_correl) == _ids_por_registro(correl)
    assert set(l5_correl["ID_GRUPO"]) == set(correl["ID_GRUPO"])
    l5_golden = pd.read_parquet(dir_trabajo / "L5_golden" / "golden.parquet")
    assert res.golden is not None
    assert set(l5_golden["ID_GRUPO"]) == set(res.golden["ID_GRUPO"])

    # Reportes L6: la correlativa exportada y los grupos que listan los reportes.
    l6 = dir_trabajo / "L6_reporting"
    assert _ids_por_registro(pd.read_parquet(l6 / "tabla_correlativa.parquet")) == (
        _ids_por_registro(correl)
    )
    grupos_entregable = set(correl["ID_GRUPO"].tolist())
    for nombre in ("reporte_casos_revision.xlsx", "reporte_estadisticas_grupos.xlsx"):
        for _, hoja in pd.read_excel(l6 / nombre, sheet_name=None).items():
            if "ID_GRUPO" in hoja.columns:
                assert set(hoja["ID_GRUPO"].dropna().astype(int)) <= grupos_entregable
