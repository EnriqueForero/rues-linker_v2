"""Países sin clasificar: detenerse antes, o aislarlos y medirlo (v0.22.3).

Origen: la base ``snowflake_v2`` trae grafías de ``PAIS_ESTANDAR`` que el
catálogo no reconoce. Con 0.22.2 la corrida completaba el emparejamiento y
DESPUÉS fallaba la invariante «el país final está en el catálogo o marcado»,
sin decir qué grafías eran. Peor: medido sobre una muestra, la misma razón
social bajo tres grafías sin clasificar distintas se fusionaba en un solo
importador, porque las tres compartían ``PAIS_FINAL = "SIN CLASIFICAR"`` y el
veto categórico no las separaba.
"""

from __future__ import annotations

import pandas as pd
import pytest

from record_linkage.flujo import (
    ConfigImportadores,
    PaisesSinClasificar,
    cobertura_paises,
    deduplicar_importadores,
    exigir_cobertura_paises,
    preparar,
)

NOMBRE = "GLOBAL TRADING PARTNERS LLC"


def _cfg(**extra) -> ConfigImportadores:
    return ConfigImportadores(
        col_razon_social="RAZON_SOCIAL",
        col_pais="PAIS",
        cols_metricas=("FOB",),
        col_peso_economico="FOB",
        verboso=False,
        **extra,
    )


def _base_con_huecos() -> pd.DataFrame:
    """La misma empresa bajo tres grafías que el catálogo no conoce."""
    filas = [
        (NOMBRE, "NO DEFINIDO", 10.0),
        (NOMBRE, "SIN INFORMACION", 20.0),
        ("GLOBAL TRADING PARTNERS L.L.C.", "TERRITORIO X", 30.0),
        # Y una vez bajo un país real, para comprobar que tampoco se une a esos.
        (NOMBRE, "ESTADOS UNIDOS", 40.0),
        ("ACME TRADING LLC", "ESTADOS UNIDOS", 100.0),
        ("ACME TRADING L.L.C.", "Estados Unidos", 50.0),
        ("BETA LOGISTICS INC", "ALEMANIA", 70.0),
        ("BETA LOGISTICS INC", "NO DEFINIDO", 5.0),
    ]
    return pd.DataFrame(filas, columns=["RAZON_SOCIAL", "PAIS", "FOB"])


def _base_limpia() -> pd.DataFrame:
    return _base_con_huecos().query("PAIS not in ['NO DEFINIDO','SIN INFORMACION','TERRITORIO X']")


# ── Modo por defecto: detenerse ANTES, y decir qué falta ──────────────


def test_detener_falla_en_preparar_y_nombra_las_grafias():
    """El fallo debe ocurrir antes del emparejamiento y traer la lista."""
    with pytest.raises(PaisesSinClasificar) as exc:
        preparar(_base_con_huecos(), _cfg())
    mensaje = str(exc.value)
    for grafia in ("NO DEFINIDO", "SIN INFORMACION", "TERRITORIO X"):
        assert grafia in mensaje
    assert "GRAFIAS_ADICIONALES" in mensaje, "el remedio debe apuntar a la celda del catálogo"
    assert "aislar" in mensaje, "la escotilla debe estar en el mensaje"
    # La tabla viaja en la excepción: quien la capture puede mostrarla.
    assert list(exc.value.tabla.columns) == ["valor", "n_filas", "sugerencia", "similitud"]
    assert int(exc.value.tabla.n_filas.sum()) == 4


def test_detener_es_el_modo_por_defecto():
    assert ConfigImportadores().paises_sin_clasificar == "detener"


def test_config_rechaza_un_modo_desconocido():
    with pytest.raises(ValueError, match="paises_sin_clasificar"):
        _cfg(paises_sin_clasificar="ignorar")


# ── El preflight barato que el notebook usa como paso propio ──────────


def test_cobertura_paises_lista_todo_lo_que_falta_con_sugerencia():
    tabla = cobertura_paises(_base_con_huecos(), _cfg())
    assert list(tabla.columns) == ["valor", "n_filas", "sugerencia", "similitud"]
    assert set(tabla.valor) == {"NO DEFINIDO", "SIN INFORMACION", "TERRITORIO X"}
    assert tabla.loc[tabla.valor == "NO DEFINIDO", "n_filas"].item() == 2
    assert tabla.n_filas.is_monotonic_decreasing


def test_cobertura_paises_vacia_cuando_el_catalogo_cubre_todo():
    tabla = cobertura_paises(_base_limpia(), _cfg())
    assert tabla.empty and list(tabla.columns) == ["valor", "n_filas", "sugerencia", "similitud"]


def test_cobertura_paises_devuelve_todas_las_grafias_no_solo_25():
    """`sugerir_alias_pais` corta en 25 por defecto; el preflight no puede cortar."""
    filas = [(f"EMPRESA {i}", f"PAIS INVENTADO {i}", 1.0) for i in range(40)]
    tabla = cobertura_paises(pd.DataFrame(filas, columns=["RAZON_SOCIAL", "PAIS", "FOB"]), _cfg())
    assert len(tabla) == 40


def test_exigir_cobertura_es_la_misma_logica_que_preparar():
    cfg = _cfg()
    tabla = cobertura_paises(_base_con_huecos(), cfg)
    with pytest.raises(PaisesSinClasificar):
        exigir_cobertura_paises(tabla, cfg)
    exigir_cobertura_paises(tabla, _cfg(paises_sin_clasificar="aislar"))  # no lanza
    exigir_cobertura_paises(tabla.iloc[0:0], cfg)  # vacía: no lanza


# ── Modo aislar: la corrida sigue y NADA se mezcla ────────────────────


@pytest.fixture(scope="module")
def aislado():
    return deduplicar_importadores(_base_con_huecos(), _cfg(paises_sin_clasificar="aislar"))


def test_aislar_completa_la_corrida_con_todas_las_invariantes(aislado):
    assert aislado.todo_ok, aislado.invariantes.to_string(index=False)


def test_aislar_no_fusiona_la_misma_razon_social_entre_grafias_sin_clasificar(aislado):
    """El caso medido en 0.22.2: tres grafías → un solo ZZZ-000035. Ya no."""
    c = aislado.correlativa
    g = c[c.RAZON_SOCIAL.str.startswith("GLOBAL TRADING")]
    assert g.ID_IMPORTADOR.nunique() == 4, g[["PAIS", "PAIS_FINAL", "ID_IMPORTADOR"]]
    assert set(g.PAIS_FINAL) == {
        "SIN CLASIFICAR: NO DEFINIDO",
        "SIN CLASIFICAR: SIN INFORMACION",
        "SIN CLASIFICAR: TERRITORIO X",
        "ESTADOS UNIDOS",
    }


def test_aislar_no_une_una_grafia_sin_clasificar_con_un_pais_real(aislado):
    c = aislado.correlativa
    beta = c[c.RAZON_SOCIAL == "BETA LOGISTICS INC"]
    assert beta.ID_IMPORTADOR.nunique() == 2
    assert set(beta.PAIS_ISO3) == {"DEU", "ZZZ"}


def test_aislar_marca_el_metodo_y_el_iso(aislado):
    c = aislado.correlativa
    sc = c[c.PAIS_METODO == "sin_clasificar"]
    assert (sc.PAIS_ISO3 == "ZZZ").all()
    assert sc.PAIS_FINAL.str.startswith("SIN CLASIFICAR: ").all()


def test_la_invariante_nueva_existe_y_se_mide(aislado):
    inv = aislado.invariantes.set_index("invariante")["cumple"]
    assert "ningún grupo mezcla dos grafías de país" in inv.index
    assert bool(inv["ningún grupo mezcla dos grafías de país"])
    assert bool(inv["el país final está en el catálogo o marcado"])


def test_aislar_expone_las_sugerencias_para_ampliar_el_catalogo(aislado):
    assert set(aislado.sugerencias_pais.valor) == {"NO DEFINIDO", "SIN INFORMACION", "TERRITORIO X"}
    assert "SUGERENCIAS_PAIS" in aislado.extra


# ── Sin huecos, los dos modos son EL MISMO resultado ──────────────────


def test_sin_grafias_sin_clasificar_el_modo_no_cambia_nada():
    """Garantía de no regresión: la base de referencia tiene cobertura total."""
    a = deduplicar_importadores(_base_limpia(), _cfg())
    b = deduplicar_importadores(_base_limpia(), _cfg(paises_sin_clasificar="aislar"))
    assert a.todo_ok and b.todo_ok
    pd.testing.assert_frame_equal(a.correlativa, b.correlativa)
    pd.testing.assert_frame_equal(a.golden, b.golden)


def test_hay_once_invariantes():
    """Diez hasta F2.12; la undécima es la CONFIANZA del estándar (regla única)."""
    r = deduplicar_importadores(_base_limpia(), _cfg())
    assert len(r.invariantes) == 11, r.invariantes.invariante.tolist()


# ── El defecto latente que el modo aislar destapó ─────────────────────


def test_agrupar_no_pais_false_ya_no_multiplica_filas():
    """Antes de 0.22.3 la unión base↔representantes iba por (ISO3, nombre).

    Con ``agrupar_no_pais=False`` varias zonas francas comparten ISO3 = ZZF con
    ``PAIS_FINAL`` distinto, así que el merge multiplicaba filas. Nadie lo pisó
    porque el defecto es ``True``. La invariante «una fila por fila de entrada»
    lo detecta; esta prueba fija que quede cerrado.
    """
    filas = [
        ("GAMMA SAS", "ZONA FRANCA BOGOTA", 10.0),
        ("GAMMA SAS", "ZONA FRANCA CARTAGENA", 20.0),
        ("GAMMA S.A.S.", "ZONA FRANCA BOGOTA", 30.0),
        ("DELTA LTDA", "PANAMA", 5.0),
    ]
    df = pd.DataFrame(filas, columns=["RAZON_SOCIAL", "PAIS", "FOB"])
    r = deduplicar_importadores(df, _cfg(agrupar_no_pais=False))
    assert r.todo_ok, r.invariantes.to_string(index=False)
    assert len(r.correlativa) == len(df)
    gamma = r.correlativa[r.correlativa.RAZON_SOCIAL.str.startswith("GAMMA")]
    assert gamma.ID_IMPORTADOR.nunique() == 2, "una zona franca ≠ otra zona franca"


# ── paises_aislar: aceptar lo conocido sin apagar la guardia (v0.22.4) ──
#
# Primera corrida real sobre snowflake_v2: 19 grafías fuera del catálogo. 18
# eran países que faltaban (van al catálogo). La 19, `OTROS` (444 filas), no es
# un país. Declararla no puede significar "aislar todo": una grafía nueva que
# aparezca mañana tiene que seguir deteniendo la corrida.


def _base_otros() -> pd.DataFrame:
    filas = [
        ("GLOBAL TRADING PARTNERS LLC", "OTROS", 10.0),
        ("GLOBAL TRADING PARTNERS L.L.C.", "OTROS", 20.0),
        ("GLOBAL TRADING PARTNERS LLC", "ESTADOS UNIDOS", 40.0),
        ("ACME TRADING LLC", "ESTADOS UNIDOS", 100.0),
        ("BETA LOGISTICS INC", "ALEMANIA", 70.0),
    ]
    return pd.DataFrame(filas, columns=["RAZON_SOCIAL", "PAIS", "FOB"])


def test_lo_declarado_en_paises_aislar_no_detiene_y_queda_aislado():
    r = deduplicar_importadores(_base_otros(), _cfg(paises_aislar=("OTROS",)))
    assert r.todo_ok, r.invariantes.to_string(index=False)
    c = r.correlativa
    otros = c[c.PAIS == "OTROS"]
    assert (otros.PAIS_FINAL == "SIN CLASIFICAR: OTROS").all()
    assert (otros.PAIS_ISO3 == "ZZZ").all()
    # Dentro de OTROS sí se deduplica por nombre; con un país real, nunca.
    assert otros.ID_IMPORTADOR.nunique() == 1
    g = c[c.RAZON_SOCIAL.str.startswith("GLOBAL TRADING")]
    assert g.ID_IMPORTADOR.nunique() == 2


def test_una_grafia_no_declarada_sigue_deteniendo_aunque_haya_declaradas():
    df = pd.concat(
        [
            _base_otros(),
            pd.DataFrame(
                [("ZETA CO", "PAIS INVENTADO", 1.0)], columns=["RAZON_SOCIAL", "PAIS", "FOB"]
            ),
        ]
    )
    with pytest.raises(PaisesSinClasificar) as exc:
        preparar(df, _cfg(paises_aislar=("OTROS",)))
    assert list(exc.value.tabla.valor) == ["PAIS INVENTADO"], "solo lo NO declarado se reporta"
    assert "NO_SON_PAISES" in str(exc.value)


def test_paises_aislar_se_compara_normalizado():
    """`otros`, `Otros ` y `OTROS` son la misma declaración."""
    r = deduplicar_importadores(_base_otros(), _cfg(paises_aislar=("  otros ",)))
    assert r.todo_ok
    assert (
        r.correlativa.loc[r.correlativa.PAIS == "OTROS", "PAIS_FINAL"] == "SIN CLASIFICAR: OTROS"
    ).all()


def test_exigir_cobertura_ignora_lo_declarado():
    cfg = _cfg(paises_aislar=("OTROS",))
    tabla = cobertura_paises(_base_otros(), cfg)
    assert list(tabla.valor) == ["OTROS"], "la cobertura lo LISTA (para que se vea)"
    exigir_cobertura_paises(tabla, cfg)  # pero no detiene


def test_sin_declarar_otros_se_detiene():
    with pytest.raises(PaisesSinClasificar, match="OTROS"):
        preparar(_base_otros(), _cfg())
