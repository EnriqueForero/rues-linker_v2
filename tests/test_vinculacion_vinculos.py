"""Vínculos por empresa ancla del motor de vinculación (``flujo.vinculacion.vinculos``).

Cada valor esperado salió de correr la función ORIGINAL del motor (la celda ``%%writefile`` del notebook 10) con las
mismas tablas: la extracción no cambia el comportamiento. Junto a cada caso va la regla que ejerce. Las empresas son
inventadas y las tablas se arman a mano (``apoyo_pares_vinculacion``) con las columnas que lee cada función.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest
from apoyo_pares_vinculacion import (
    FilaDecision,
    Grafia,
    ParAuto,
    Registro,
    destino_de,
    graf_de,
    pares_auto_de,
    tabla_decisiones,
)

from record_linkage.flujo.vinculacion.vinculos import (
    agregar_por_entidad,
    aplicar_decisiones,
    fusionar_por_empresa_comun,
    mapear_decisiones,
)

ZELVORA_SA = "España|Zelvora Alimentos SA"
ZELVORA_MAYUS = "España|ZELVORA ALIMENTOS"
KRAMENTO = "España|Kramento Foods SL"

#: Seis grafías de la ancla; ``ENT_DE_G`` las agrupa en cinco empresas (las dos primeras son la empresa 0).
GRAF = graf_de(
    [
        Grafia("España", "Zelvora Alimentos SA", "ZELVORA ALIMENTOS"),
        Grafia("España", "ZELVORA ALIMENTOS", "ZELVORA ALIMENTOS"),
        Grafia("España", "Kramento Foods SL", "KRAMENTO FOODS"),
        Grafia("Alemania", "Orbelia Conservas", "ORBELIA CONSERVAS", iso="DEU"),
        Grafia("España", "Orbelia Conservas SA", "ORBELIA CONSERVAS"),
        Grafia("España", "Quelmar", "QUELMAR"),
    ]
)
ENT_DE_G = np.array([0, 0, 1, 2, 3, 4])

PREP = {
    "ORBIS": destino_de(
        [
            Registro("ESB0000001", "Zelvora Alimentos SA", "ZELVORA ALIMENTOS"),
            Registro("ESB0000002", "Kramento Foods SL", "KRAMENTO FOODS"),
            Registro("ESB0000003", "Orbelia Conservas", "ORBELIA CONSERVAS"),
            Registro("ESB0000004", "Quelmar  Lácteos", "QUELMAR LACTEOS"),
        ]
    ),
    "FDI": destino_de(
        [
            Registro("FDI-F0002", "Zelvora Foods", "ZELVORA FOODS"),
            Registro("FDI-F0003", "Kramento", "KRAMENTO"),
        ],
        "FDI",
    ),
}
DESTINOS = ["ORBIS", "FDI"]

COLUMNAS_MAPA = [
    "ENT",
    "FUENTE_DESTINO",
    "pos_b",
    "DECISION",
    "AUTOR",
    "FECHA",
    "RAZON",
    "ARCHIVO",
    "ORDEN",
    "CLAVE_A_DEC",
]


def _filas(tabla: pd.DataFrame, columnas: list[str]) -> list[tuple[Any, ...]]:
    """Las filas de ``tabla`` en esas columnas, como tuplas de valores de Python."""
    return [tuple(f) for f in tabla[columnas].astype(object).itertuples(index=False)]


# ── agregar_por_entidad ───────────────────────────────────────────────────────

COLUMNAS_AGREGADAS = ["g", "pos_b", "FUENTE_DESTINO", "NIVEL_AUTO", "BANDA_AUTO", "MOTIVO", "ENT"]


def test_agregar_deja_el_mejor_par_por_empresa_fuente_y_registro():
    """Dos grafías de la misma empresa sobre el mismo registro: gana la de mejor nivel y banda, no la de mayor score."""
    pares = pares_auto_de(
        [
            ParAuto(0, 0, "EMPRESA", "B", 0.90, motivo="g0"),
            ParAuto(
                1, 0, "EMPRESA", "A", 0.80, motivo="g1"
            ),  # banda A: gana aunque su score es menor
        ]
    )
    r = agregar_por_entidad(pares, ENT_DE_G, GRAF)
    assert _filas(r, [*COLUMNAS_AGREGADAS, "GRAFIA_ANCLA"]) == [
        (1, 0, "ORBIS", "EMPRESA", "A", "g1", 0, "ZELVORA ALIMENTOS")
    ]


def test_agregar_a_igual_rango_gana_el_mayor_score():
    """Con el mismo nivel y banda, manda el ``SCORE_RUES_LINKER`` más alto."""
    pares = pares_auto_de([ParAuto(0, 1, "GRUPO", "B", 0.70), ParAuto(1, 1, "GRUPO", "B", 0.85)])
    r = agregar_por_entidad(pares, ENT_DE_G, GRAF)
    assert _filas(r, ["g", "SCORE_RUES_LINKER", "GRAFIA_ANCLA"]) == [(1, 0.85, "ZELVORA ALIMENTOS")]


def test_agregar_un_empate_total_lo_gana_el_primero_porque_el_orden_es_estable():
    """Mismo rango y mismo score: se queda el par que venía primero (``kind="stable"``)."""
    pares = pares_auto_de(
        [
            ParAuto(0, 2, "REVISAR", "C", 0.60, motivo="primero"),
            ParAuto(1, 2, "REVISAR", "C", 0.60, motivo="segundo"),
        ]
    )
    r = agregar_por_entidad(pares, ENT_DE_G, GRAF)
    assert r["MOTIVO"].tolist() == ["primero"]
    assert r["g"].tolist() == [0]


def test_agregar_cada_fuente_destino_es_una_clave_distinta():
    """El mismo registro de dos fuentes distintas son dos pares; salen ordenados por empresa, fuente y registro."""
    pares = pares_auto_de(
        [
            ParAuto(0, 0, "EMPRESA", "A", 0.50, fuente="ORBIS"),
            ParAuto(0, 0, "EMPRESA", "A", 0.50, fuente="FDI"),
        ]
    )
    r = agregar_por_entidad(pares, ENT_DE_G, GRAF)
    assert r["FUENTE_DESTINO"].tolist() == ["FDI", "ORBIS"]


def test_agregar_una_empresa_distinta_es_otra_clave_aunque_el_registro_sea_el_mismo():
    """Las grafías 0 y 2 son empresas distintas: el mismo registro queda una vez por empresa."""
    pares = pares_auto_de([ParAuto(2, 0, "REVISAR", "C"), ParAuto(0, 0, "EMPRESA", "A")])
    r = agregar_por_entidad(pares, ENT_DE_G, GRAF)
    assert _filas(r, ["ENT", "pos_b", "NIVEL_AUTO"]) == [(0, 0, "EMPRESA"), (1, 0, "REVISAR")]


def test_agregar_un_nivel_vacio_pierde_contra_cualquier_nivel():
    """Un par sin nivel tiene el peor rango (9), aunque su puntaje sea mayor."""
    pares = pares_auto_de([ParAuto(2, 3, "", "", 0.99), ParAuto(2, 3, "REVISAR", "C", 0.10)])
    r = agregar_por_entidad(pares, ENT_DE_G, GRAF)
    assert _filas(r, ["NIVEL_AUTO", "BANDA_AUTO", "SCORE_RUES_LINKER"]) == [("REVISAR", "C", 0.10)]


def test_agregar_un_nivel_o_una_banda_desconocidos_pierden_contra_cualquier_nivel():
    """Un nivel o una banda fuera de la tabla de órdenes (nulo tras el mapeo) rankean peor que todos, aunque su score sea mayor."""
    pares = pares_auto_de([ParAuto(2, 3, "OTRO", "Z", 0.99), ParAuto(2, 3, "REVISAR", "C", 0.10)])
    r = agregar_por_entidad(pares, ENT_DE_G, GRAF)
    assert _filas(r, ["NIVEL_AUTO", "BANDA_AUTO", "SCORE_RUES_LINKER"]) == [("REVISAR", "C", 0.10)]
    pares = pares_auto_de(
        [ParAuto(2, 3, "REVISAR", "Z", 0.99), ParAuto(2, 3, "REVISAR", "C", 0.10)]
    )
    r = agregar_por_entidad(pares, ENT_DE_G, GRAF)
    assert _filas(r, ["NIVEL_AUTO", "BANDA_AUTO", "SCORE_RUES_LINKER"]) == [("REVISAR", "C", 0.10)]


def test_agregar_agrega_ent_y_grafia_ancla_y_deja_un_indice_limpio():
    """Las columnas nuevas son ``ENT`` y ``GRAFIA_ANCLA`` (al final) y el índice vuelve a empezar en 0."""
    pares = pares_auto_de([ParAuto(2, 3, "REVISAR", "C"), ParAuto(0, 0, "EMPRESA", "A")])
    r = agregar_por_entidad(pares, ENT_DE_G, GRAF)
    assert list(r.columns) == [*pares.columns, "ENT", "GRAFIA_ANCLA"]
    assert r.index.tolist() == [0, 1]
    assert r["GRAFIA_ANCLA"].tolist() == ["Zelvora Alimentos SA", "Kramento Foods SL"]


def test_agregar_sin_pares_devuelve_la_tabla_vacia_solo_con_ent():
    """Sin pares no hay grafías que nombrar: la tabla vacía conserva sus columnas y agrega ``ENT`` (entera)."""
    pares = pares_auto_de([ParAuto(0, 0, "EMPRESA", "A")]).iloc[0:0]
    r = agregar_por_entidad(pares, ENT_DE_G, GRAF)
    assert r.empty
    assert list(r.columns) == [*pares.columns, "ENT"]
    assert np.issubdtype(r["ENT"].dtype, np.integer)


# ── mapear_decisiones ─────────────────────────────────────────────────────────


def _mapear(filas: list[FilaDecision]) -> tuple[pd.DataFrame, list[str]]:
    return mapear_decisiones(tabla_decisiones(filas), GRAF, ENT_DE_G, PREP, DESTINOS)


def test_mapear_traduce_la_decision_a_empresa_fuente_y_posicion_del_registro():
    """``CLAVE_A`` pasa a la empresa de esa grafía y ``CLAVE_B`` a la posición del registro en su fuente."""
    m, avisos = _mapear(
        [
            FilaDecision(
                "VINCULO", "ORBIS", ZELVORA_MAYUS, "ESB0000003", "EMPRESA", "Orbelia Conservas"
            )
        ]
    )
    assert avisos == []
    assert list(m.columns) == COLUMNAS_MAPA
    assert _filas(m, COLUMNAS_MAPA) == [
        (
            0,
            "ORBIS",
            2,
            "EMPRESA",
            "Revisora Uno",
            "2026-10-01",
            "",
            "decisiones_1.csv",
            0,
            ZELVORA_MAYUS,
        )
    ]


def test_mapear_sin_decisiones_devuelve_el_mapa_vacio_con_sus_columnas():
    """Una tabla de decisiones vacía da un mapa vacío con las 10 columnas y ningún aviso."""
    m, avisos = _mapear([])
    assert m.empty
    assert list(m.columns) == COLUMNAS_MAPA
    assert avisos == []


def test_mapear_el_orden_de_archivo_sale_entero_aunque_la_tabla_lo_traiga_decimal():
    """``ORDEN`` pasa por ``int``: el mapa lo guarda entero aunque la tabla de decisiones lo trajera decimal."""
    dec = tabla_decisiones(
        [
            FilaDecision(
                "VINCULO", "ORBIS", ZELVORA_MAYUS, "ESB0000003", "EMPRESA", "Orbelia Conservas"
            )
        ]
    )
    dec["ORDEN"] = dec["ORDEN"].astype(float)
    m, avisos = mapear_decisiones(dec, GRAF, ENT_DE_G, PREP, DESTINOS)
    assert avisos == []
    assert np.issubdtype(m["ORDEN"].dtype, np.integer)


def test_mapear_ignora_las_decisiones_de_duplicado():
    """Solo las filas ``TIPO=VINCULO`` se traducen aquí; las de ``DUPLICADO`` son de la deduplicación."""
    m, avisos = _mapear(
        [FilaDecision("DUPLICADO", "EXPORTADORES", ZELVORA_SA, ZELVORA_MAYUS, "DISTINTA")]
    )
    assert m.empty
    assert list(m.columns) == COLUMNAS_MAPA
    assert avisos == []


def test_mapear_omite_con_aviso_la_fuente_la_grafia_y_el_registro_que_no_existen():
    """Fuente desconocida, grafía de la ancla desconocida y registro destino desconocido: cada uno da su aviso."""
    m, avisos = _mapear(
        [
            FilaDecision("VINCULO", "NINGUNA", ZELVORA_SA, "ESB0000001", "EMPRESA"),
            FilaDecision("VINCULO", "ORBIS", "España|NO EXISTE SA", "ESB0000001", "EMPRESA"),
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB9999999", "EMPRESA"),
            FilaDecision("VINCULO", "FDI", KRAMENTO, "FDI-F0003", "GRUPO"),
        ]
    )
    assert avisos == [
        "decisión con FUENTE desconocida 'NINGUNA': España|Zelvora Alimentos SA ↔ ESB0000001",
        "ORBIS: la grafía del exportador 'España|NO EXISTE SA' no existe en esta base (se omite)",
        "ORBIS: el registro 'ESB9999999' no existe en esta base (se omite)",
    ]
    assert _filas(m, ["ENT", "FUENTE_DESTINO", "pos_b", "DECISION"]) == [(1, "FDI", 1, "GRUPO")]


def test_mapear_un_nombre_distinto_en_el_registro_detiene_la_corrida():
    """Si ``NOMBRE_B`` no es el del registro actual, la clave apunta a OTRA empresa: ValueError con qué/por qué/qué hacer."""
    with pytest.raises(ValueError, match=r"Qué pasó: 1 decisiones apuntan") as e:
        _mapear(
            [
                FilaDecision(
                    "VINCULO", "ORBIS", ZELVORA_SA, "ESB0000001", "EMPRESA", "Otra Empresa SA"
                )
            ]
        )
    assert str(e.value) == (
        "Qué pasó: 1 decisiones apuntan a un registro destino con OTRO nombre (primeras):\n"
        "  ORBIS · ESB0000001: la decisión dice 'otra empresa sa', el archivo dice 'zelvora alimentos sa'\n"
        "Por qué importa: sin identificador nativo, la clave es la fila del Excel; "
        "si la hoja cambió de orden, la decisión aplicaría a otra empresa.\n"
        "Qué hacer: use decisiones hechas sobre ESTOS archivos o corrija CLAVE_B."
    )


def test_mapear_el_error_muestra_solo_las_primeras_diez_pero_cuenta_todas():
    """Con 12 nombres equivocados el mensaje dice 12 y lista 10."""
    filas = [
        FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB0000001", "EMPRESA", f"Falso {k}")
        for k in range(12)
    ]
    with pytest.raises(ValueError, match=r"Qué pasó: 12 decisiones apuntan") as e:
        _mapear(filas)
    detalle = [x for x in str(e.value).split("\n") if x.startswith("  ORBIS")]
    assert len(detalle) == 10
    assert detalle[0].endswith("la decisión dice 'falso 0', el archivo dice 'zelvora alimentos sa'")
    assert detalle[-1].endswith(
        "la decisión dice 'falso 9', el archivo dice 'zelvora alimentos sa'"
    )


def test_mapear_el_nombre_en_blanco_no_se_verifica_y_el_resto_se_compara_sin_mayusculas_ni_espacios():
    """``NOMBRE_B`` vacío pasa; con valor se compara tras quitar espacios de más y plegar mayúsculas."""
    m, avisos = _mapear(
        [
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB0000001", "EMPRESA", ""),
            FilaDecision(
                "VINCULO", "ORBIS", KRAMENTO, "ESB0000002", "EMPRESA", "  KRAMENTO   FOODS sl "
            ),
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB0000004", "GRUPO", "quelmar lácteos"),
        ]
    )
    assert avisos == []
    assert _filas(m, ["ENT", "pos_b", "DECISION"]) == [
        (0, 0, "EMPRESA"),
        (1, 1, "EMPRESA"),
        (0, 3, "GRUPO"),
    ]


def test_mapear_entre_archivos_distintos_gana_la_ultima_decision_y_se_avisa_del_conflicto():
    """El orden de los archivos manda: la decisión de ``ORDEN`` mayor se aplica y el aviso cuenta el historial."""
    m, avisos = _mapear(
        [
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB0000001", "EMPRESA", archivo="d1.csv", orden=0, autor="Ana"),
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB0000001", "NO_VINCULAR", archivo="d2.csv", orden=1, autor="Beto"),
        ]
    )  # fmt: skip
    assert _filas(m, ["ENT", "pos_b", "DECISION", "AUTOR", "ARCHIVO", "ORDEN"]) == [
        (0, 0, "NO_VINCULAR", "Beto", "d2.csv", 1)
    ]
    assert avisos == [
        "ORBIS: «Zelvora Alimentos SA» ↔ ESB0000001 «Zelvora Alimentos SA» tiene decisiones distintas "
        "(d1.csv: EMPRESA → d2.csv: NO_VINCULAR); se aplica la última"
    ]


def test_mapear_la_ultima_se_decide_por_orden_de_archivo_aunque_las_filas_vengan_desordenadas():
    """``ORDEN`` ordena antes que la posición de la fila: la fila del archivo 1 va antes aunque se lea después."""
    m, avisos = _mapear(
        [
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB0000001", "NO_VINCULAR", archivo="d2.csv", orden=1),
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB0000001", "EMPRESA", archivo="d1.csv", orden=0),
        ]
    )  # fmt: skip
    assert m["DECISION"].tolist() == ["NO_VINCULAR"]
    assert "(d1.csv: EMPRESA → d2.csv: NO_VINCULAR)" in avisos[0]


def test_mapear_dos_grafias_de_la_misma_empresa_sobre_el_mismo_registro_son_un_conflicto():
    """El conflicto se mide por (empresa, fuente, registro): mandan la última fila y su ``CLAVE_A``."""
    m, avisos = _mapear(
        [
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB0000001", "EMPRESA", archivo="d1.csv"),
            FilaDecision(
                "VINCULO", "ORBIS", ZELVORA_MAYUS, "ESB0000001", "GRUPO", archivo="d1.csv"
            ),
        ]
    )
    assert _filas(m, ["ENT", "DECISION", "CLAVE_A_DEC"]) == [(0, "GRUPO", ZELVORA_MAYUS)]
    assert avisos == [
        "ORBIS: «ZELVORA ALIMENTOS» ↔ ESB0000001 «Zelvora Alimentos SA» tiene decisiones distintas "
        "(d1.csv: EMPRESA → d1.csv: GRUPO); se aplica la última"
    ]


def test_mapear_repetir_la_misma_decision_no_es_un_conflicto_y_gana_la_ultima_fila():
    """Dos archivos que coinciden dejan una sola fila (la última) y ningún aviso."""
    m, avisos = _mapear(
        [
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB0000001", "EMPRESA", archivo="d1.csv", orden=0, autor="Ana"),
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB0000001", "EMPRESA", archivo="d2.csv", orden=1, autor="Beto"),
        ]
    )  # fmt: skip
    assert avisos == []
    assert _filas(m, ["AUTOR", "ARCHIVO"]) == [("Beto", "d2.csv")]


def test_mapear_el_historial_del_aviso_lista_todas_las_decisiones_en_orden():
    """Con tres archivos el aviso encadena las tres con ``→`` y firma con la clave de la última."""
    _, avisos = _mapear(
        [
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB0000001", "EMPRESA", archivo="d1.csv", orden=0),
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB0000001", "GRUPO", archivo="d2.csv", orden=1),
            FilaDecision("VINCULO", "ORBIS", ZELVORA_MAYUS, "ESB0000001", "NO_VINCULAR", archivo="d3.csv", orden=2),
        ]
    )  # fmt: skip
    assert avisos == [
        "ORBIS: «ZELVORA ALIMENTOS» ↔ ESB0000001 «Zelvora Alimentos SA» tiene decisiones distintas "
        "(d1.csv: EMPRESA → d2.csv: GRUPO → d3.csv: NO_VINCULAR); se aplica la última"
    ]


def test_mapear_los_avisos_de_omision_van_antes_que_los_de_conflicto():
    """Primero las decisiones omitidas, en el orden de las filas; después los conflictos."""
    _, avisos = _mapear(
        [
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB0000001", "EMPRESA", archivo="d1.csv", orden=0),
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB0000001", "GRUPO", archivo="d2.csv", orden=1),
            FilaDecision("VINCULO", "ORBIS", ZELVORA_SA, "ESB9999999", "EMPRESA", archivo="d2.csv", orden=1),
        ]
    )  # fmt: skip
    assert [a.split(" tiene decisiones")[0][:12] for a in avisos] == [
        "ORBIS: el re",
        "ORBIS: «Zelv",
    ]


# ── aplicar_decisiones ────────────────────────────────────────────────────────

REVISORA = "Revisora Uno"
ASISTIDA = "(verificar) panel"


def _pares_L(filas: list[tuple[int, str, int, str, str]]) -> pd.DataFrame:
    """Pares por empresa: ``(ENT, fuente, pos_b, NIVEL_AUTO, BANDA_AUTO)``."""
    return pd.DataFrame(
        {
            "ENT": [f[0] for f in filas],
            "FUENTE_DESTINO": [f[1] for f in filas],
            "pos_b": [f[2] for f in filas],
            "NIVEL_AUTO": [f[3] for f in filas],
            "BANDA_AUTO": [f[4] for f in filas],
            "SCORE_RUES_LINKER": [0.9] * len(filas),
        }
    )


def _mapa(filas: list[tuple[int, str, int, str, str]]) -> pd.DataFrame:
    """Mapa de decisiones: ``(ENT, fuente, pos_b, DECISION, AUTOR)``."""
    return pd.DataFrame(
        {
            "ENT": [f[0] for f in filas],
            "FUENTE_DESTINO": [f[1] for f in filas],
            "pos_b": [f[2] for f in filas],
            "DECISION": [f[3] for f in filas],
            "AUTOR": [f[4] for f in filas],
            "FECHA": ["2026-10-01"] * len(filas),
            "RAZON": [""] * len(filas),
            "ARCHIVO": ["d1.csv"] * len(filas),
            "ORDEN": [0] * len(filas),
            "CLAVE_A_DEC": [ZELVORA_SA] * len(filas),
        }
    )


COLUMNAS_FINALES = [
    "DECISION_VALIDADA",
    "NIVEL_FINAL",
    "ESTADO",
    "CONFIANZA",
    "ORIGEN",
]

PARES_L = _pares_L(
    [
        (0, "ORBIS", 0, "EMPRESA", "A"),
        (0, "ORBIS", 1, "GRUPO", "B"),
        (0, "ORBIS", 2, "REVISAR", "C"),
        (1, "ORBIS", 0, "EMPRESA", "B"),
        (1, "ORBIS", 1, "EMPRESA", "B"),
        (1, "ORBIS", 2, "EMPRESA", "B"),
        (2, "ORBIS", 0, "REVISAR", "C"),
        (2, "ORBIS", 1, "REVISAR", "C"),
        (2, "ORBIS", 2, "REVISAR", "C"),
        (3, "ORBIS", 0, "EMPRESA", "A"),
        (3, "ORBIS", 1, "EMPRESA", "B"),
        (3, "ORBIS", 2, "EMPRESA", "A"),
        (5, "ORBIS", 0, "RELACION_MATRIZ", "C"),
        (6, "ORBIS", 0, "GRUPO", "C"),
        (6, "ORBIS", 1, "GRUPO", "C"),
    ]
)
MAPA = _mapa(
    [
        (1, "ORBIS", 0, "EMPRESA", REVISORA),
        (1, "ORBIS", 1, "GRUPO", REVISORA),
        (1, "ORBIS", 2, "NO_VINCULAR", REVISORA),
        (2, "ORBIS", 0, "EMPRESA", REVISORA),
        (2, "ORBIS", 1, "RELACION_PROPIEDAD", REVISORA),
        (2, "ORBIS", 2, "NO_VINCULAR", REVISORA),
        (3, "ORBIS", 0, "EMPRESA", ASISTIDA),
        (3, "ORBIS", 1, "EMPRESA", ASISTIDA),
        (3, "ORBIS", 2, "GRUPO", ASISTIDA),
        (4, "ORBIS", 5, "EMPRESA", REVISORA),
        (5, "ORBIS", 0, "RELACION_PROPIEDAD", ASISTIDA),
        (6, "ORBIS", 1, "EMPRESA", REVISORA),
    ]
)

#: (ENT, pos_b) → (DECISION_VALIDADA, NIVEL_FINAL, ESTADO, CONFIANZA, ORIGEN). Un caso por combinación.
ESPERADO_APLICAR = {
    # Sin decisión: lo automático.
    (0, 0): ("", "EMPRESA", "VINCULADO (automático A)", "ALTA", "automático"),
    (0, 1): ("", "GRUPO", "VINCULADO (automático B · revisar)", "MEDIA", "automático"),
    (0, 2): ("", "", "CANDIDATO (pendiente de revisión)", "", "candidato"),
    # Decisión validada por una persona sobre un par automático B.
    (1, 0): ("SÍ", "EMPRESA", "VINCULADO (revisión)", "ALTA", "automático, confirmado por revisión"),
    (1, 1): ("SÍ", "GRUPO", "VINCULADO (revisión)", "ALTA", "automático, nivel corregido por revisión"),
    (1, 2): ("SÍ", "", "DESCARTADO (revisión)", "", "automático, descartado por revisión"),
    # Decisión validada sobre un candidato C.
    (2, 0): ("SÍ", "EMPRESA", "VINCULADO (revisión)", "ALTA", "agregado por revisión"),
    (2, 1): ("SÍ", "RELACION_PROPIEDAD", "RELACIÓN DE PROPIEDAD (revisión)", "", "agregado por revisión"),
    # Hallazgo F3: rechazar un candidato que el motor no vinculó también se rotula «agregado por revisión».
    (2, 2): ("SÍ", "", "DESCARTADO (revisión)", "", "agregado por revisión"),
    # Revisión ASISTIDA: confirma un automático A (queda A y ALTA), o cambia/agrega (queda MEDIA y «validar»).
    (3, 0): ("NO (revisión asistida)", "EMPRESA", "VINCULADO (automático A)", "ALTA", "automático, confirmado por revisión"),
    (3, 1): ("NO (revisión asistida)", "EMPRESA", "VINCULADO (revisión asistida · validar)", "MEDIA", "automático, confirmado por revisión"),
    (3, 2): ("NO (revisión asistida)", "GRUPO", "VINCULADO (revisión asistida · validar)", "MEDIA", "automático, nivel corregido por revisión"),
    # Una decisión sobre un par que el motor no propuso agrega la fila (unión externa).
    (4, 5): ("SÍ", "EMPRESA", "VINCULADO (revisión)", "ALTA", "agregado por revisión"),
    (5, 0): ("NO (revisión asistida)", "RELACION_PROPIEDAD", "RELACIÓN DE PROPIEDAD (revisión)", "", "agregado por revisión"),
    # Un GRUPO de banda C (otro país) no es un vínculo automático: es un candidato, con o sin decisión.
    (6, 0): ("", "", "CANDIDATO (pendiente de revisión)", "", "candidato"),
    (6, 1): ("SÍ", "EMPRESA", "VINCULADO (revisión)", "ALTA", "agregado por revisión"),
}  # fmt: skip


def test_aplicar_cada_estado_confianza_y_origen():
    """Una fila por combinación de (automático A/B/C o sin par) × (sin decisión, validada, asistida) × decisión."""
    x = aplicar_decisiones(PARES_L, MAPA)
    obtenido = {
        (r.ENT, r.pos_b): (r.DECISION_VALIDADA, r.NIVEL_FINAL, r.ESTADO, r.CONFIANZA, r.ORIGEN)
        for r in x.itertuples(index=False)
    }
    assert obtenido == ESPERADO_APLICAR


def test_aplicar_agrega_las_columnas_de_decision_y_las_finales_y_quita_el_indicador_de_union():
    """Salen las columnas de ``L``, las del mapa y las cinco finales; no queda ``_merge``."""
    x = aplicar_decisiones(PARES_L, MAPA)
    assert set(x.columns) == set(PARES_L.columns) | set(MAPA.columns) | set(COLUMNAS_FINALES)
    assert "_merge" not in x.columns
    assert len(x) == len(ESPERADO_APLICAR)


def test_aplicar_sin_decisiones_deja_lo_automatico():
    """Con un mapa vacío solo cuentan la banda y el nivel automáticos."""
    x = aplicar_decisiones(PARES_L, MAPA.iloc[0:0])
    assert x["ESTADO"].tolist() == (
        [
            "VINCULADO (automático A)",
            "VINCULADO (automático B · revisar)",
            "CANDIDATO (pendiente de revisión)",
        ]
        + ["VINCULADO (automático B · revisar)"] * 3
        + ["CANDIDATO (pendiente de revisión)"] * 3
        + [
            "VINCULADO (automático A)",
            "VINCULADO (automático B · revisar)",
            "VINCULADO (automático A)",
        ]
        + ["CANDIDATO (pendiente de revisión)"] * 3
    )
    assert set(x["DECISION_VALIDADA"]) == {""}
    assert x["CONFIANZA"].tolist() == [
        "ALTA",
        "MEDIA",
        "",
        "MEDIA",
        "MEDIA",
        "MEDIA",
        "",
        "",
        "",
        "ALTA",
        "MEDIA",
        "ALTA",
        "",
        "",
        "",
    ]


def test_aplicar_una_marca_vacia_desactiva_la_revision_asistida():
    """Con ``marca=""`` ninguna decisión es asistida: todas quedan validadas aunque su autor diga «(verificar)»."""
    x = aplicar_decisiones(PARES_L, MAPA, "")
    assert set(x.loc[x["DECISION"] != "", "DECISION_VALIDADA"]) == {"SÍ"}
    fila = x[(x["ENT"] == 3) & (x["pos_b"] == 1)].iloc[0]
    assert (fila["ESTADO"], fila["CONFIANZA"]) == ("VINCULADO (revisión)", "ALTA")


def test_aplicar_la_marca_se_busca_como_texto_literal_dentro_del_autor():
    """La marca no es una expresión regular: sus paréntesis y signos se escapan."""
    l_ = _pares_L([(0, "ORBIS", 0, "EMPRESA", "B")])
    x = aplicar_decisiones(l_, _mapa([(0, "ORBIS", 0, "EMPRESA", "Ana (v+)")]), "(v+)")
    assert _filas(x, ["DECISION_VALIDADA", "ESTADO"]) == [
        ("NO (revisión asistida)", "VINCULADO (revisión asistida · validar)")
    ]
    y = aplicar_decisiones(l_, _mapa([(0, "ORBIS", 0, "EMPRESA", "Ana v")]), "(v+)")
    assert _filas(y, ["DECISION_VALIDADA", "ESTADO"]) == [("SÍ", "VINCULADO (revisión)")]


def test_aplicar_es_idempotente_en_valores():
    """Volver a aplicar sobre el resultado da los mismos valores: parte de lo automático y descarta las columnas de decisión.

    Hallazgo F3: el orden de las columnas no se conserva (``NIVEL_FINAL``, ``ESTADO``, ``CONFIANZA`` y ``ORIGEN`` no
    están en la lista que se descarta), así que se compara con las columnas alineadas.
    """
    x = aplicar_decisiones(PARES_L, MAPA)
    y = aplicar_decisiones(x, MAPA)
    assert set(y.columns) == set(x.columns)
    assert y[list(x.columns)].equals(x)


def test_aplicar_no_modifica_la_tabla_de_entrada():
    """``L`` y el mapa de entrada quedan intactos (se trabaja sobre copias)."""
    antes = PARES_L.copy()
    aplicar_decisiones(PARES_L, MAPA)
    assert PARES_L.equals(antes)
    assert list(PARES_L.columns) == list(antes.columns)


# ── fusionar_por_empresa_comun ────────────────────────────────────────────────

GRAF_FUSION = graf_de(
    [
        Grafia("España", "Zelvora Alimentos SA", "ZELVORA ALIMENTOS"),  # 0
        Grafia("España", "ZELVORA ALIMENTOS", "ZELVORA ALIMENTOS"),  # 1
        Grafia("España", "Zelvora SA", "ZELVORA"),  # 2
        Grafia("Alemania", "Zelvora GmbH", "ZELVORA", iso="DEU"),  # 3
        Grafia("España", "Kramento Foods SL", "KRAMENTO FOODS"),  # 4
        Grafia("España", "Quelmar", "QUELMAR"),  # 5
    ]
)
UNA_POR_GRAFIA = np.arange(6)


def _vinculos(filas: list[tuple[int, str, int, str, str]]) -> pd.DataFrame:
    """Pares ya decididos: ``(ENT, fuente, pos_b, NIVEL_FINAL, CONFIANZA)``."""
    return pd.DataFrame(
        {
            "ENT": [f[0] for f in filas],
            "FUENTE_DESTINO": [f[1] for f in filas],
            "pos_b": [f[2] for f in filas],
            "NIVEL_FINAL": [f[3] for f in filas],
            "CONFIANZA": [f[4] for f in filas],
        }
    )


def _con_vetos(vetos: set[tuple[int, int]]) -> pd.DataFrame:
    graf = GRAF_FUSION.copy()
    graf.attrs["vetos"] = vetos
    return graf


def _fusionar(filas, ent_de_g=UNA_POR_GRAFIA, graf=GRAF_FUSION):
    nuevo, uniones = fusionar_por_empresa_comun(_vinculos(filas), ent_de_g, graf, PREP)
    return nuevo.tolist(), uniones


def test_fusionar_dos_empresas_con_el_mismo_registro_empresa_alta_y_el_mismo_pais_se_unen():
    """Ambas son EMPRESA de confianza ALTA con ESB0000001 y del mismo país: pasan a ser una."""
    nuevo, uniones = _fusionar(
        [(0, "ORBIS", 0, "EMPRESA", "ALTA"), (1, "ORBIS", 0, "EMPRESA", "ALTA")]
    )
    assert nuevo == [0, 0, 1, 2, 3, 4]
    assert uniones == [(0, 1, "misma empresa que ORBIS ESB0000001")]


def test_fusionar_no_une_empresas_de_paises_distintos():
    """Mismo registro, pero una es de España y la otra de Alemania: siguen separadas."""
    nuevo, uniones = _fusionar(
        [(0, "ORBIS", 0, "EMPRESA", "ALTA"), (3, "ORBIS", 0, "EMPRESA", "ALTA")]
    )
    assert nuevo == [0, 1, 2, 3, 4, 5]
    assert uniones == []


@pytest.mark.parametrize(
    ("nivel", "confianza"),
    [
        pytest.param("EMPRESA", "MEDIA", id="confianza_media"),
        pytest.param("EMPRESA", "", id="sin_confianza"),
        pytest.param("GRUPO", "ALTA", id="nivel_grupo"),
        pytest.param("", "ALTA", id="sin_nivel"),
    ],
)
def test_fusionar_solo_cuentan_los_vinculos_empresa_de_confianza_alta(nivel, confianza):
    """Un vínculo GRUPO, o EMPRESA con confianza que no es ALTA, no prueba que sean la misma empresa."""
    nuevo, uniones = _fusionar(
        [(0, "ORBIS", 0, "EMPRESA", "ALTA"), (1, "ORBIS", 0, nivel, confianza)]
    )
    assert nuevo == [0, 1, 2, 3, 4, 5]
    assert uniones == []


def test_fusionar_un_veto_entre_las_grafias_impide_la_union():
    """Una decisión DISTINTA entre las dos grafías (``attrs["vetos"]``) las mantiene separadas."""
    nuevo, uniones = _fusionar(
        [(0, "ORBIS", 0, "EMPRESA", "ALTA"), (1, "ORBIS", 0, "EMPRESA", "ALTA")],
        graf=_con_vetos({(0, 1)}),
    )
    assert nuevo == [0, 1, 2, 3, 4, 5]
    assert uniones == []


def test_fusionar_tres_empresas_al_mismo_registro_se_unen_a_la_primera():
    """Las uniones parten siempre de la empresa menor del grupo: (0,1) y (0,2)."""
    nuevo, uniones = _fusionar(
        [
            (0, "ORBIS", 0, "EMPRESA", "ALTA"),
            (1, "ORBIS", 0, "EMPRESA", "ALTA"),
            (2, "ORBIS", 0, "EMPRESA", "ALTA"),
        ]
    )
    assert nuevo == [0, 0, 0, 1, 2, 3]
    assert uniones == [
        (0, 1, "misma empresa que ORBIS ESB0000001"),
        (0, 2, "misma empresa que ORBIS ESB0000001"),
    ]


def test_fusionar_un_veto_entre_dos_de_tres_deja_fuera_a_la_que_lo_sufre():
    """Con el veto (1, 2) la empresa 2 no puede unirse al grupo que ya contiene a la grafía 1."""
    nuevo, uniones = _fusionar(
        [
            (0, "ORBIS", 0, "EMPRESA", "ALTA"),
            (1, "ORBIS", 0, "EMPRESA", "ALTA"),
            (2, "ORBIS", 0, "EMPRESA", "ALTA"),
        ],
        graf=_con_vetos({(1, 2)}),
    )
    assert nuevo == [0, 0, 1, 2, 3, 4]
    assert uniones == [(0, 1, "misma empresa que ORBIS ESB0000001")]


def test_fusionar_la_union_es_transitiva_a_traves_de_dos_registros():
    """La empresa 1 comparte un registro con la 0 y otro con la 2: las tres terminan juntas."""
    nuevo, uniones = _fusionar(
        [
            (0, "ORBIS", 0, "EMPRESA", "ALTA"),
            (1, "ORBIS", 0, "EMPRESA", "ALTA"),
            (1, "FDI", 0, "EMPRESA", "ALTA"),
            (2, "FDI", 0, "EMPRESA", "ALTA"),
        ]
    )
    assert nuevo == [0, 0, 0, 1, 2, 3]
    assert uniones == [  # los registros se recorren por (fuente, posición): FDI antes que ORBIS
        (1, 2, "misma empresa que FDI FDI-F0002"),
        (0, 1, "misma empresa que ORBIS ESB0000001"),
    ]


def test_fusionar_empresas_con_varias_grafias_llevan_todas_sus_grafias():
    """Con la empresa 0 = grafías 0 y 1, unirla a la 1 (grafía 2) deja tres grafías en la empresa 0."""
    ent = np.array([0, 0, 1, 2, 3, 4])
    nuevo, uniones = _fusionar(
        [(0, "ORBIS", 0, "EMPRESA", "ALTA"), (1, "ORBIS", 0, "EMPRESA", "ALTA")], ent_de_g=ent
    )
    assert nuevo == [0, 0, 0, 1, 2, 3]
    assert uniones == [(0, 1, "misma empresa que ORBIS ESB0000001")]


def test_fusionar_el_veto_entre_grafias_de_empresas_con_varias_grafias_tambien_cuenta():
    """El veto (1, 2) enfrenta a una grafía de la empresa 0 con la de la empresa 1: no se funden."""
    ent = np.array([0, 0, 1, 2, 3, 4])
    nuevo, uniones = _fusionar(
        [(0, "ORBIS", 0, "EMPRESA", "ALTA"), (1, "ORBIS", 0, "EMPRESA", "ALTA")],
        ent_de_g=ent,
        graf=_con_vetos({(1, 2)}),
    )
    assert nuevo == [0, 0, 1, 2, 3, 4]
    assert uniones == []


def test_fusionar_las_empresas_de_un_registro_se_unen_en_orden_numerico():
    """Con empresas 1 y 16 sobre el mismo registro se une la 16 a la 1, no al revés (el orden no sale de un conjunto)."""
    graf = graf_de([Grafia("España", f"Empresa {k}", f"EMPRESA{k}") for k in range(17)])
    nuevo, uniones = fusionar_por_empresa_comun(
        _vinculos([(16, "ORBIS", 0, "EMPRESA", "ALTA"), (1, "ORBIS", 0, "EMPRESA", "ALTA")]),
        np.arange(17),
        graf,
        PREP,
    )
    assert uniones == [(1, 16, "misma empresa que ORBIS ESB0000001")]
    assert nuevo.tolist() == [0, 1, *range(2, 16), 1]


def test_fusionar_el_veto_vale_sin_importar_cual_empresa_tiene_la_grafia_de_menor_indice():
    """Las grafías 0 y 1 pertenecen a las empresas 1 y 0 (al revés): el veto (0, 1) debe verse en cualquier orden."""
    nuevo, uniones = _fusionar(
        [(0, "ORBIS", 0, "EMPRESA", "ALTA"), (1, "ORBIS", 0, "EMPRESA", "ALTA")],
        ent_de_g=np.array([1, 0, 2, 3, 4, 5]),
        graf=_con_vetos({(0, 1)}),
    )
    assert nuevo == [0, 1, 2, 3, 4, 5]
    assert uniones == []


def test_fusionar_el_veto_de_la_grafia_de_la_primera_empresa_tambien_protege_al_grupo_ya_unido():
    """Las empresas 0, 1 y 2 comparten registro: 1 se une a 0, y 2 no puede unirse porque la grafía 0 está vetada con la 2."""
    nuevo, uniones = _fusionar(
        [
            (0, "ORBIS", 0, "EMPRESA", "ALTA"),
            (1, "ORBIS", 0, "EMPRESA", "ALTA"),
            (2, "ORBIS", 0, "EMPRESA", "ALTA"),
        ],
        graf=_con_vetos({(0, 2)}),
    )
    assert nuevo == [0, 0, 1, 2, 3, 4]
    assert uniones == [(0, 1, "misma empresa que ORBIS ESB0000001")]


def test_fusionar_el_veto_se_mide_contra_el_grupo_ya_unido_de_la_otra_empresa():
    """1 y 2 ya son una (comparten FDI); al llegar la 0 por ORBIS con la 2, el veto (0, 1) la frena por la grafía 1."""
    nuevo, uniones = _fusionar(
        [
            (1, "FDI", 0, "EMPRESA", "ALTA"),
            (2, "FDI", 0, "EMPRESA", "ALTA"),
            (0, "ORBIS", 0, "EMPRESA", "ALTA"),
            (2, "ORBIS", 0, "EMPRESA", "ALTA"),
        ],
        graf=_con_vetos({(0, 1)}),
    )
    assert nuevo == [0, 1, 1, 2, 3, 4]
    assert uniones == [(1, 2, "misma empresa que FDI FDI-F0002")]


def test_fusionar_las_empresas_se_renumeran_por_orden_de_primera_aparicion():
    """Al unir la empresa 4 con la 1 el resultado vuelve a numerarse 0..k-1 según la primera grafía de cada una."""
    nuevo, _ = _fusionar([(1, "ORBIS", 0, "EMPRESA", "ALTA"), (4, "ORBIS", 0, "EMPRESA", "ALTA")])
    assert nuevo == [0, 1, 2, 3, 1, 4]


def test_fusionar_registros_distintos_no_unen_a_empresas_distintas():
    """Cada empresa con su propio registro: no hay nada en común."""
    nuevo, uniones = _fusionar(
        [(0, "ORBIS", 0, "EMPRESA", "ALTA"), (1, "ORBIS", 1, "EMPRESA", "ALTA")]
    )
    assert nuevo == [0, 1, 2, 3, 4, 5]
    assert uniones == []


def test_fusionar_sin_vinculos_o_sin_grafias_no_falla():
    """Sin vínculos cada grafía es su empresa; sin grafías el resultado es un arreglo entero vacío."""
    assert _fusionar([]) == ([0, 1, 2, 3, 4, 5], [])
    nuevo, uniones = fusionar_por_empresa_comun(
        _vinculos([]), np.array([], dtype=int), graf_de([]), PREP
    )
    assert len(nuevo) == 0
    assert np.issubdtype(nuevo.dtype, np.integer)
    assert uniones == []


def test_fusionar_sin_vetos_declarados_se_asume_ninguno():
    """Si ``graf`` no trae ``attrs["vetos"]`` (no hubo decisiones DISTINTA) se une sin restricción."""
    assert "vetos" not in GRAF_FUSION.attrs
    nuevo, _ = _fusionar([(0, "ORBIS", 0, "EMPRESA", "ALTA"), (2, "ORBIS", 0, "EMPRESA", "ALTA")])
    assert nuevo == [0, 1, 0, 2, 3, 4]
