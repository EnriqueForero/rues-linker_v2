"""Tests v0.20.0 — Suite de conformidad, puente de tipos y bloqueo por llaves.

Qué se protege
--------------
1. La suite de conformidad detecta un conjunto que se contradice a sí mismo:
   si la hoja de pares y la columna de grupo no dicen lo mismo, ninguna cifra
   que salga de ahí significa nada y hay que enterarse al cargar.
2. Un caso reprobado NO se promedia con los demás: reprueba la corrida.
3. Los casos que el catálogo marca frontera son deuda declarada y no
   reprueban — pero se cuentan aparte, para que no se olviden.
4. El puente registra TODOS los tipos de campo en el catálogo de producción,
   sin reimplementar ninguno.
5. Las llaves de bloqueo nunca agrupan por el centinela de "sin llave", que
   es como un bloqueo pasa de ayudar a producir un bloque de millones.
6. Los comparadores sobrevin a una columna con ausentes (pandas 3.0).

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-29  ·  Version: 0.20.0
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.lsh.llaves_extra import (
    CANONICALIZADORES,
    LlaveBloqueo,
    LlaveTokens,
    canonicalizar_llave,
    iter_pares_por_llaves,
    iter_pares_por_tokens,
)
from record_linkage.evaluation import conformidad as conformidad_mod
from record_linkage.evaluation.conformidad import (
    VARIABLE_ENTORNO,
    ConjuntoConformidad,
    cargar_conjunto,
    evaluar_dedup,
    evaluar_linkage,
    localizar_conjunto,
)
from record_linkage.matching.comparadores_extra import obtener, tipos_disponibles
from record_linkage.matching.puente_campos import (
    NOMBRES_REGISTRADOS,
    PREFIJO,
    TIPOS_PUENTEADOS,
    descomponer_geo,
)

# ── El conjunto versionado ────────────────────────────────────────────────


#: El conjunto se ancla al archivo de prueba, no al directorio de trabajo ni a
#: la ubicación del paquete instalado. Las pruebas viven en el repositorio y
#: siempre saben dónde está; depender del cwd hace que el resultado cambie
#: según desde dónde se lance pytest, que es como 8 casos se saltaron en
#: silencio durante dos versiones.
RAIZ_REPO = Path(__file__).resolve().parents[1]
DIRECTORIO_CONJUNTO = RAIZ_REPO / "data" / "conformidad"


@pytest.fixture(scope="module")
def conjunto() -> ConjuntoConformidad:
    try:
        return cargar_conjunto(DIRECTORIO_CONJUNTO)
    except FileNotFoundError:  # pragma: no cover
        pytest.skip("data/conformidad/ no está presente en esta copia")


def test_el_conjunto_versionado_es_coherente(conjunto: ConjuntoConformidad) -> None:
    """Si esto falla, el conjunto se contradice y no se puede medir con él."""
    assert len(conjunto.dedup_registros) == 166
    assert len(conjunto.dedup_pares) == 140
    assert len(conjunto.catalogo) == 43


def test_carga_rechaza_un_conjunto_que_se_contradice(
    conjunto: ConjuntoConformidad, tmp_path
) -> None:
    pares = conjunto.dedup_pares.copy()
    pares.loc[0, "MATCH_ESPERADO"] = "False" if pares.loc[0, "MATCH_ESPERADO"] == "True" else "True"
    for nombre, marco in (
        ("dedup_registros", conjunto.dedup_registros),
        ("dedup_pares", pares),
        ("linkage_base_a", conjunto.linkage_a),
        ("linkage_base_b", conjunto.linkage_b),
        ("linkage_ground_truth", conjunto.linkage_verdad),
        ("catalogo_casos", conjunto.catalogo),
    ):
        marco.to_csv(tmp_path / f"{nombre}.csv", index=False)
    with pytest.raises(ValueError, match="se contradice"):
        cargar_conjunto(tmp_path)


def test_falta_una_hoja_lo_dice_con_qué_hacer(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match=r"construir|conformidad"):
        cargar_conjunto(tmp_path)


# ── El veredicto por caso ─────────────────────────────────────────────────


def _informe(conjunto: ConjuntoConformidad, grupos):
    return evaluar_dedup(conjunto, np.asarray(grupos), etiqueta="t", version="0")


def test_una_particion_perfecta_aprueba_todo(conjunto: ConjuntoConformidad) -> None:
    verdad = conjunto.dedup_registros["ID_GRUPO_ESPERADO"].to_numpy()
    informe = _informe(conjunto, verdad)
    assert informe.f1 == pytest.approx(1.0)
    assert informe.pasa
    assert all(c.pasa for c in informe.casos)


def test_un_caso_roto_reprueba_la_corrida(conjunto: ConjuntoConformidad) -> None:
    """Un solo caso mal no se diluye: hunde el veredicto."""
    verdad = conjunto.dedup_registros["ID_GRUPO_ESPERADO"].to_numpy().copy()
    # Partir el primer grupo con más de un miembro rompe su caso.
    serie = pd.Series(verdad)
    grande = serie.value_counts().idxmax()
    posiciones = np.flatnonzero(serie.to_numpy() == grande)
    verdad[posiciones[0]] = "GRUPO_INVENTADO"
    informe = _informe(conjunto, verdad)
    assert not informe.pasa
    assert any(not c.pasa for c in informe.firmes)


def test_los_casos_frontera_no_reprueban_pero_se_cuentan(
    conjunto: ConjuntoConformidad,
) -> None:
    verdad = conjunto.dedup_registros["ID_GRUPO_ESPERADO"].to_numpy().copy()
    # Separar los pares de C09 y C21, que el catálogo marca TP_DIFICIL.
    frontera = conjunto.dedup_pares[conjunto.dedup_pares["CASO_TIPO"].isin(["C09", "C21"])]
    posicion = {str(r): i for i, r in enumerate(conjunto.dedup_registros["REG_ID"])}
    for fila in frontera.itertuples(index=False):
        verdad[posicion[str(fila.REG_ID_B)]] = f"SEPARADO_{fila.REG_ID_B}"
    informe = _informe(conjunto, verdad)
    reprobados = {c.codigo for c in informe.casos if not c.pasa}
    assert reprobados == {"C09", "C21"}
    assert all(c.frontera for c in informe.casos if not c.pasa)
    assert informe.pasa, "los casos frontera son deuda declarada, no regresión"


def test_longitud_incoherente_es_error(conjunto: ConjuntoConformidad) -> None:
    with pytest.raises(ValueError, match="etiquetas"):
        evaluar_dedup(conjunto, np.array([0, 1]), etiqueta="t", version="0")


def test_linkage_castiga_los_cruces_inventados(conjunto: ConjuntoConformidad) -> None:
    verdaderos = {
        (str(f.REG_ID_A), str(f.REG_ID_B))
        for f in conjunto.linkage_verdad.itertuples(index=False)
        if str(f.MATCH_ESPERADO).strip().lower() == "true"
    }
    limpio = evaluar_linkage(conjunto, verdaderos, etiqueta="t", version="0")
    assert limpio.f1 == pytest.approx(1.0)
    sucio = evaluar_linkage(conjunto, verdaderos | {("1", "20")}, etiqueta="t", version="0")
    assert sucio.precision < 1.0
    assert not sucio.pasa


def test_el_informe_se_serializa_completo(conjunto: ConjuntoConformidad, tmp_path) -> None:
    import json

    informe = _informe(conjunto, conjunto.dedup_registros["ID_GRUPO_ESPERADO"].to_numpy())
    destino = informe.guardar(tmp_path)
    datos = json.loads(destino.read_text(encoding="utf-8"))
    assert datos["pasa"] is True
    assert len(datos["casos"]) == len({c.codigo for c in informe.casos})


# ── El puente de tipos ────────────────────────────────────────────────────


def test_el_puente_registra_todos_los_tipos() -> None:
    disponibles = set(tipos_disponibles())
    for tipo in TIPOS_PUENTEADOS:
        assert f"{PREFIJO}{tipo.value}" in disponibles, f"falta {tipo.value}"
    assert len(NOMBRES_REGISTRADOS) == len(TIPOS_PUENTEADOS)


def test_el_puente_no_pisa_los_comparadores_heredados() -> None:
    """La paridad de los heredados está congelada; el puente no los toca."""
    for heredado in ("exact_or_zero", "categorical", "token_set_ratio_signed"):
        assert not heredado.startswith(PREFIJO)
        assert obtener(heredado) is not None


@pytest.mark.parametrize("tipo", [t.value for t in TIPOS_PUENTEADOS])
def test_cada_tipo_puenteado_es_simetrico_y_acotado(tipo: str) -> None:
    izquierda = np.array(
        ["4.6,-74.0", "2024-01-01", "1000", "BOGOTA", "A|B", "", None], dtype=object
    )
    derecha = np.array(
        ["4.6,-74.0", "2024-02-01", "1100", "BOGOTA D.C.", "B", None, ""], dtype=object
    )
    comparador = obtener(f"{PREFIJO}{tipo}")
    ida, vuelta = comparador.funcion(izquierda, derecha), comparador.funcion(derecha, izquierda)
    np.testing.assert_allclose(ida, vuelta, atol=1e-12)
    minimo = -1.0 if comparador.firmado else 0.0
    assert ida.min() >= minimo - 1e-12
    assert ida.max() <= 1.0 + 1e-12


def test_geo_reconoce_los_tres_separadores() -> None:
    esperado = np.array([[4.65, -74.05]])
    for texto in ("4.65,-74.05", "4.65;-74.05", "4.65 -74.05"):
        np.testing.assert_allclose(descomponer_geo(np.array([texto])), esperado)


def test_geo_sin_dos_numeros_es_faltante() -> None:
    salida = descomponer_geo(np.array(["4.65", "", "hola", None], dtype=object))
    assert np.isnan(salida).any(axis=1).all()


def test_geo_mide_distancia_de_verdad() -> None:
    cerca = obtener("tipo_geo").funcion(np.array(["4.65,-74.05"]), np.array(["4.6505,-74.0505"]))
    lejos = obtener("tipo_geo").funcion(np.array(["4.65,-74.05"]), np.array(["10.9,-74.7"]))
    assert cerca[0] > 0.9
    assert lejos[0] == pytest.approx(0.0)


def test_direccion_reconoce_placa_con_guion() -> None:
    """El defecto que motivó la corrección: '71-21' contra '71 21'."""
    f = obtener("tipo_direccion").funcion
    assert f(np.array(["CRA 7 # 71-21"]), np.array(["CARRERA 7 NO 71 21"]))[0] == pytest.approx(1.0)


def test_los_comparadores_toleran_ausentes_de_pandas_3() -> None:
    """pandas 3.0 dejó los ausentes como float; un `.map` posterior reventaba."""
    izquierda = np.array(["BOGOTA", None, np.nan], dtype=object)
    derecha = np.array([None, "BOGOTA", "CALI"], dtype=object)
    for tipo in ("tipo_ciudad", "tipo_direccion", "tipo_nombre_empresa"):
        salida = obtener(tipo).funcion(izquierda, derecha)
        assert np.isfinite(salida).all()


# ── Bloqueo por llaves declaradas ─────────────────────────────────────────


@pytest.fixture
def tabla() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "TELEFONO": ["+57 312 189 7799", "3121897799", "312-189-7799", "6011234567", ""],
            "EMAIL": ["A@X.COM", "a@x.com", "otro@y.com", "", "n/a"],
            "GEO": ["4.65,-74.05", "4.6501,-74.0502", "10.9,-74.7", "", ""],
            "NOMBRE": ["ACME TORRES SAS", "ACME TORRES SA", "OTRA COSA LTDA", "X", "Y"],
        }
    )


def test_el_centinela_sin_llave_no_forma_bloque(tabla: pd.DataFrame) -> None:
    """Sin esto, todos los registros sin teléfono quedarían en un bloque."""
    pares = list(iter_pares_por_llaves(tabla, llaves=(LlaveBloqueo("EMAIL", "email"),)))
    emitidos = np.vstack(pares).tolist() if pares else []
    assert [3, 4] not in emitidos, "dos registros sin correo no comparten nada"


def test_las_llaves_agrupan_lo_equivalente(tabla: pd.DataFrame) -> None:
    pares = list(iter_pares_por_llaves(tabla, llaves=(LlaveBloqueo("TELEFONO", "telefono"),)))
    emitidos = np.vstack(pares).tolist()
    for esperado in ([0, 1], [0, 2], [1, 2]):
        assert esperado in emitidos


def test_la_rejilla_desplazada_rescata_el_borde(tabla: pd.DataFrame) -> None:
    """Dos puntos a 25 m pueden caer a lados distintos de un borde de celda."""
    base = canonicalizar_llave(tabla, LlaveBloqueo("GEO", "geo", longitud_minima=3))
    desplazada = canonicalizar_llave(
        tabla, LlaveBloqueo("GEO", "geo_desplazada", longitud_minima=3)
    )
    assert base[0] != base[1], "el caso de prueba debe caer en el borde"
    assert desplazada[0] == desplazada[1]


def test_un_canonicalizador_inexistente_falla_al_declarar() -> None:
    with pytest.raises(ValueError, match="no existe"):
        LlaveBloqueo("X", "inventado")


def test_columna_ausente_falla_diciendo_cuáles_hay(tabla: pd.DataFrame) -> None:
    with pytest.raises(KeyError, match=r"disponibles|Disponibles"):
        canonicalizar_llave(tabla, LlaveBloqueo("NO_EXISTE"))


def test_desde_texto_acepta_la_forma_de_la_linea_de_ordenes() -> None:
    llave = LlaveBloqueo.desde_texto("TELEFONO:telefono:50")
    assert (llave.columna, llave.canonicalizador, llave.max_bloque) == ("TELEFONO", "telefono", 50)
    assert LlaveBloqueo.desde_texto("EMAIL").canonicalizador == "texto"


def test_todos_los_canonicalizadores_devuelven_texto(tabla: pd.DataFrame) -> None:
    for nombre, funcion in CANONICALIZADORES.items():
        columna = "GEO" if "geo" in nombre else "TELEFONO"
        salida = funcion(tabla[columna])
        assert len(salida) == len(tabla), nombre
        assert all(isinstance(x, str) or pd.isna(x) for x in salida), nombre


def test_el_bloqueo_por_tokens_une_lo_que_el_trigrama_pierde() -> None:
    """El caso medido: la corrupción golpea al token más raro, no a todos."""
    tabla = pd.DataFrame(
        {"N": ["ORGANIZACION RUIZ TORRES ARGOS", "ORGANIZACION RUZ TORRES RAGOS", "PANADERIA X"]}
    )
    pares = list(iter_pares_por_tokens(tabla, llave=LlaveTokens("N")))
    assert [0, 1] in np.vstack(pares).tolist()


def test_el_bloqueo_por_tokens_ignora_los_frecuentes() -> None:
    """Un token que aparece en todos no identifica: no debe agrupar."""
    tabla = pd.DataFrame({"N": [f"EMPRESA COLOMBIA {i}" for i in range(20)]})
    pares = list(iter_pares_por_tokens(tabla, llave=LlaveTokens("N", frecuencia_maxima=5)))
    assert not pares, "EMPRESA y COLOMBIA aparecen 20 veces; no deben formar bloque"


def test_llave_de_tokens_valida_sus_parametros() -> None:
    with pytest.raises(ValueError, match="frecuencia_maxima"):
        LlaveTokens("N", frecuencia_maxima=1)
    with pytest.raises(ValueError, match="max_bloque"):
        LlaveTokens("N", max_bloque=1)


# ── Localización del conjunto (v0.22.1) ───────────────────────────────────
#
# Por qué estas pruebas existen. `cargar_conjunto()` resolvía el directorio con
# `Path(__file__).parents[3]`, o sea relativo al MÓDULO. Con layout `src/` y el
# paquete importado desde el árbol de fuentes eso da la raíz del repositorio y
# funciona. Con la rueda instalada da el directorio de instalación de Python, el
# conjunto no aparece, `cargar_conjunto()` lanza `FileNotFoundError` y el fixture
# de arriba **se salta los 8 casos en silencio**: la compuerta de publicación
# reporta verde sin haber medido nada. Se detectó al instalar el paquete antes
# de las compuertas en el notebook de publicación.


def test_el_conjunto_se_localiza_desde_la_raiz_del_repositorio() -> None:
    """Con cwd en la raíz, el conjunto aparece aunque el paquete esté instalado."""
    assert (DIRECTORIO_CONJUNTO / "dedup_registros.csv").is_file()
    assert localizar_conjunto(RAIZ_REPO) == DIRECTORIO_CONJUNTO


def test_el_conjunto_se_localiza_desde_un_subdirectorio() -> None:
    """La búsqueda sube: correr pytest desde `tests/` no debe cambiar el resultado."""
    assert localizar_conjunto(RAIZ_REPO / "tests") == DIRECTORIO_CONJUNTO


def test_la_variable_de_entorno_gana_sobre_la_heuristica(monkeypatch, tmp_path) -> None:
    """Escotilla explícita: si el operador la define, manda, aunque no exista."""
    monkeypatch.setenv(VARIABLE_ENTORNO, str(tmp_path / "otro_sitio"))
    assert localizar_conjunto() == tmp_path / "otro_sitio"


def test_sin_conjunto_devuelve_la_ruta_por_defecto_para_que_el_error_la_nombre(
    monkeypatch, tmp_path
) -> None:
    """Cuando no hay nada, se devuelve algo nombrable: un error mudo no se depura."""
    monkeypatch.delenv(VARIABLE_ENTORNO, raising=False)
    monkeypatch.setattr(conformidad_mod, "DIRECTORIO_POR_DEFECTO", tmp_path / "inexistente")
    assert localizar_conjunto(tmp_path) == tmp_path / "inexistente"


def test_cargar_conjunto_sin_argumento_carga_desde_la_raiz(monkeypatch) -> None:
    """El caso que rompía: `cargar_conjunto()` a secas tiene que cargar, no saltar.

    Falla, no se salta: si el conjunto dejara de ser alcanzable con el paquete
    instalado, esto tiene que verse. Un `pytest.skip` aquí reconstruiría el
    defecto que la 0.22.1 corrige.
    """
    monkeypatch.chdir(RAIZ_REPO)
    assert len(cargar_conjunto().dedup_registros) == 166


def test_cargar_conjunto_desde_un_cwd_ajeno_falla_con_una_ruta(monkeypatch, tmp_path) -> None:
    """Fuera del repositorio no hay magia: se falla nombrando la ruta buscada."""
    monkeypatch.delenv(VARIABLE_ENTORNO, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(conformidad_mod, "DIRECTORIO_POR_DEFECTO", tmp_path / "inexistente")
    with pytest.raises(FileNotFoundError, match="inexistente"):
        cargar_conjunto()


def test_el_directorio_de_conformidad_no_se_reconoce_vacio(tmp_path, monkeypatch) -> None:
    """Un `data/conformidad/` vacío no cuenta: pasaría la comprobación y fallaría lejos."""
    monkeypatch.delenv(VARIABLE_ENTORNO, raising=False)
    monkeypatch.setattr(conformidad_mod, "DIRECTORIO_POR_DEFECTO", tmp_path / "inexistente")
    (tmp_path / "data" / "conformidad").mkdir(parents=True)
    assert localizar_conjunto(tmp_path) == tmp_path / "inexistente"
