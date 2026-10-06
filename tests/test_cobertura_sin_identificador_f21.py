"""Cobertura por estrellas en ``linkage()`` (tarea F2.1, ADR-0011).

``clusters_desde_decisiones`` une por componentes conexas (single-linkage):
si ``a≈b`` y ``b≈c``, ``a`` y ``c`` quedan juntos aunque no se parezcan. Con
identificador válido el cannot-link corta el puente; SIN identificador no hay
quien lo corte. ``engine.cobertura.aplicar_cobertura_sin_identificador`` es el
único punto donde L5 reparte en estrellas los grupos sin identificador válido,
con ``matching.nombre_idf.SimilitudNombre`` sobre el IDF del corpus.

Qué exigen estas pruebas (todas con empresas inventadas):

1. un grupo encadenado por nombre con DOS entidades distintas se parte;
2. un grupo con identificador válido NO se toca, se parezcan o no los nombres;
3. la perilla ``cobertura_sin_identificador`` del perfil se interpreta una
   sola vez (``ConfigCoberturaSinIdentificador.desde_perfil``) y falla rápido
   ante claves desconocidas; ``similitud_minima = 2·umbral − 1``;
4. ``produccion_estandar`` la trae activa con 0,80 (cambio declarado);
5. ``linkage()`` la aplica en L5 y deja constancia en el manifiesto.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from record_linkage import linkage
from record_linkage.config.profiles import PERFILES_BASE
from record_linkage.engine.cobertura import (
    ConfigCoberturaSinIdentificador,
    ReporteCoberturaSinIdentificador,
    aplicar_cobertura_sin_identificador,
)
from record_linkage.matching.nombre_idf import SimilitudNombre, comparador_desde_corpus


class ComparadorPrefijo:
    """Similitud artificial firmada: +1 si comparten los 4 primeros caracteres."""

    signed = True

    def compare(self, izq, der):
        return np.array(
            [1.0 if str(a)[:4] == str(b)[:4] else -1.0 for a, b in zip(izq, der, strict=True)],
            dtype=np.float64,
        )


#: Grupo 7: dos entidades inventadas (ANDARIEGA y BRUMOSA) encadenadas por un
#: puente sin identificador válido. Grupo 8: una sola entidad con NIT válido
#: cuyas variantes NO se parecen entre sí (nombre comercial vs razón social).
#: Grupo 9: un registro solo. Ninguna fila del grupo 7 tiene NIT válido.
CASO = pd.DataFrame(
    {
        "ID_GRUPO": [7, 7, 7, 7, 8, 8, 9],
        "NOMBRE_LIMPIO": [
            "ANDARIEGA COMERCIAL",
            "ANDARIEGA COMERCIAL DEL SUR",
            "BRUMOSA LOGISTICA",
            "BRUMOSA LOGISTICA ANDINA",
            "CALICANTO INGENIERIA",
            "ZZZZ OTRO NOMBRE",
            "DESTELLO SOLITARIO",
        ],
        "NIT_BASE": ["", "", "", "900111222", "800333444", "800333444", ""],
        "NIT_VALID": [0, 0, 0, 0, 1, 1, 0],
    }
)


def _config(**kw) -> ConfigCoberturaSinIdentificador:
    base = {"activa": True, "umbral": 0.80, "regla_lider": "cobertura"}
    base.update(kw)
    return ConfigCoberturaSinIdentificador(**base)


# ── 1 y 2: parte lo encadenado sin identificador, no toca lo identificado ──


def test_parte_el_grupo_encadenado_sin_identificador_valido() -> None:
    salida, reporte = aplicar_cobertura_sin_identificador(
        CASO, config=_config(), comparador=ComparadorPrefijo()
    )
    g = salida["ID_GRUPO"].tolist()
    assert g[0] == g[1], "las dos ANDARIEGA siguen juntas"
    assert g[2] == g[3], "las dos BRUMOSA siguen juntas"
    assert g[0] != g[2], "ANDARIEGA y BRUMOSA son dos entidades: se parten"
    assert isinstance(reporte, ReporteCoberturaSinIdentificador)
    assert reporte.hubo_cambios
    assert reporte.grupos_partidos == 1
    assert reporte.grupos_creados == 1
    assert reporte.registros_reasignados == 2


def test_no_toca_un_grupo_con_identificador_valido_aunque_los_nombres_no_se_parezcan() -> None:
    salida, reporte = aplicar_cobertura_sin_identificador(
        CASO, config=_config(), comparador=ComparadorPrefijo()
    )
    g = salida["ID_GRUPO"].tolist()
    assert g[4] == g[5] == 8, "el NIT válido manda: la cobertura no opina"
    assert g[6] == 9, "un grupo de un registro se queda como está"
    assert reporte.grupos_examinados == 1


def test_un_nit_invalido_no_cuenta_como_identificador() -> None:
    """Fila 3 trae NIT_BASE pero NIT_VALID=0: el grupo 7 sigue siendo «sin identificador»."""
    _, reporte = aplicar_cobertura_sin_identificador(
        CASO, config=_config(), comparador=ComparadorPrefijo()
    )
    assert reporte.grupos_examinados == 1


def test_la_etiqueta_original_la_conserva_la_estrella_mayor_y_las_nuevas_salen_del_maximo() -> None:
    df = CASO.copy()
    df.loc[2, "NOMBRE_LIMPIO"] = "ANDARIEGA COMERCIAL NORTE"  # 3 ANDARIEGA vs 1 BRUMOSA
    salida, _ = aplicar_cobertura_sin_identificador(
        df, config=_config(), comparador=ComparadorPrefijo()
    )
    g = salida["ID_GRUPO"].tolist()
    assert g[0] == g[1] == g[2] == 7
    assert g[3] == 10, "la estrella nueva se numera desde max(ID_GRUPO) + 1"


def test_conserva_cardinalidad_no_muta_y_es_determinista() -> None:
    original = CASO.copy(deep=True)
    primera, _ = aplicar_cobertura_sin_identificador(
        CASO, config=_config(), comparador=ComparadorPrefijo()
    )
    segunda, _ = aplicar_cobertura_sin_identificador(
        CASO, config=_config(), comparador=ComparadorPrefijo()
    )
    assert len(primera) == len(CASO)
    assert primera["ID_GRUPO"].tolist() == segunda["ID_GRUPO"].tolist()
    pd.testing.assert_frame_equal(CASO, original)


def test_sin_grupos_que_examinar_devuelve_los_mismos_grupos_y_un_reporte_vacio() -> None:
    df = CASO.iloc[4:].reset_index(drop=True)
    salida, reporte = aplicar_cobertura_sin_identificador(
        df, config=_config(), comparador=ComparadorPrefijo()
    )
    assert salida["ID_GRUPO"].tolist() == df["ID_GRUPO"].tolist()
    assert not reporte.hubo_cambios
    assert reporte.grupos_examinados == 0
    assert "sin grupos" in reporte.resumen()


def test_exige_las_columnas_con_mensaje_accionable() -> None:
    with pytest.raises(KeyError, match="NOMBRE_LIMPIO"):
        aplicar_cobertura_sin_identificador(
            CASO.drop(columns=["NOMBRE_LIMPIO"]), config=_config(), comparador=ComparadorPrefijo()
        )


def test_con_el_comparador_real_del_corpus_parte_dos_entidades_y_une_las_variantes() -> None:
    """Sin comparador inyectado, el comparador sale del IDF de NOMBRE_LIMPIO."""
    df = pd.DataFrame(
        {
            "ID_GRUPO": [1] * 4,
            "NOMBRE_LIMPIO": [
                "COMERCIALIZADORA ANDARIEGA",
                "COMERCIALIZADORA ANDARIEGA SAS",
                "COMERCIALIZADORA BRUMOSA",
                "COMERCIALIZADORA BRUMOSA SAS",
            ],
            "NIT_BASE": [""] * 4,
            "NIT_VALID": [0] * 4,
        }
    )
    salida, reporte = aplicar_cobertura_sin_identificador(df, config=_config())
    g = salida["ID_GRUPO"].tolist()
    assert g[0] == g[1] and g[2] == g[3] and g[0] != g[2]
    assert reporte.grupos_partidos == 1


# ── 3: la perilla se interpreta una sola vez ───────────────────────────────


def test_similitud_minima_es_dos_umbral_menos_uno() -> None:
    assert _config(umbral=0.80).similitud_minima == pytest.approx(0.60)
    assert _config(umbral=0.70).similitud_minima == pytest.approx(0.40)


def test_desde_perfil_acepta_ausente_booleano_y_diccionario() -> None:
    assert not ConfigCoberturaSinIdentificador.desde_perfil(None).activa
    assert not ConfigCoberturaSinIdentificador.desde_perfil(False).activa
    assert ConfigCoberturaSinIdentificador.desde_perfil(True).activa
    parcial = ConfigCoberturaSinIdentificador.desde_perfil({"umbral": 0.70})
    assert parcial.activa and parcial.umbral == 0.70 and parcial.regla_lider == "cobertura"
    completa = ConfigCoberturaSinIdentificador.desde_perfil(
        {"activa": False, "umbral": 0.9, "regla_lider": "masa"}
    )
    assert (completa.activa, completa.umbral, completa.regla_lider) == (False, 0.9, "masa")


@pytest.mark.parametrize(
    "valor",
    [
        {"umbrall": 0.8},
        {"activa": True, "regla_lider": "mediana"},
        {"umbral": 1.5},
        {"umbral": 0.0},
        "0.8",
    ],
)
def test_desde_perfil_falla_rapido_con_mensaje_accionable(valor) -> None:
    with pytest.raises((ValueError, TypeError)) as info:
        ConfigCoberturaSinIdentificador.desde_perfil(valor)
    mensaje = str(info.value)
    for seccion in ("Qué pasó", "Por qué importa", "Qué hacer"):
        assert seccion in mensaje, mensaje


# ── 4: el perfil declara el cambio ─────────────────────────────────────────


def test_produccion_estandar_trae_la_cobertura_activa_con_0_80() -> None:
    perilla = PERFILES_BASE["produccion_estandar"]["cobertura_sin_identificador"]
    assert perilla == {"activa": True, "umbral": 0.80, "regla_lider": "cobertura"}


def test_todos_los_perfiles_del_orquestador_declaran_la_perilla_y_se_interpreta() -> None:
    """Sin la clave, ``ajustes_perfil={"cobertura_sin_identificador": …}`` sería rechazado."""
    for nombre, perfil in PERFILES_BASE.items():
        assert "cobertura_sin_identificador" in perfil, nombre
        ConfigCoberturaSinIdentificador.desde_perfil(perfil["cobertura_sin_identificador"])


def test_comparador_desde_corpus_es_un_similitud_nombre_con_genericos_neutralizados() -> None:
    nombres = pd.Series(["COMERCIALIZADORA ANDARIEGA", "COMERCIALIZADORA BRUMOSA", "ZETA"])
    comparador = comparador_desde_corpus(
        nombres,
        genericos_neutralizados={"COMERCIALIZADORA"},
        genericos_estructurales={"COMERCIALIZADORA"},
    )
    assert isinstance(comparador, SimilitudNombre)
    assert getattr(comparador.pesos_idf, "_neutralizados", 0) == 1
    assert comparador.pesos_idf.documentos == 3


# ── 5: linkage() la aplica en L5 y deja constancia ─────────────────────────

_PRIMERAS = ("ANDARIEGA", "BRUMOSA", "CALICANTO", "DESTELLO", "ESPIRAL", "FUMAROLA")


def _fuente_encadenada() -> pd.DataFrame:
    """Empresas inventadas SIN identificador cuyos nombres se encadenan.

    ``ANDARIEGA BRUMOSA`` y ``CALICANTO DESTELLO`` no se parecen, pero el
    scorer une por contención a los tres puentes intermedios
    (``ANDARIEGA BRUMOSA CALICANTO`` → ``… DESTELLO`` → ``BRUMOSA CALICANTO
    DESTELLO``), así que el single-linkage deja a las dos entidades en un
    mismo grupo. Hay además entidades con NIT válido para que L2…L5 tengan
    trabajo normal y se verifique que no se tocan.
    """
    filas = [
        ("", "ANDARIEGA BRUMOSA SAS"),
        ("", "ANDARIEGA BRUMOSA S.A.S."),
        ("", "ANDARIEGA BRUMOSA CALICANTO SAS"),
        ("", "ANDARIEGA BRUMOSA CALICANTO DESTELLO SAS"),
        ("", "BRUMOSA CALICANTO DESTELLO SAS"),
        ("", "CALICANTO DESTELLO SAS"),
        ("", "CALICANTO DESTELLO S.A.S."),
    ]
    for i, giro in enumerate(_PRIMERAS[4:], start=4):
        nit = f"9{i:02d}{(i * 7919) % 100000:05d}"
        filas.append((nit, f"LOGISTICA {giro} SAS"))
        filas.append((nit, f"LOGISTICA {giro} S.A.S."))
    return pd.DataFrame(filas, columns=["NIT", "RAZON_SOCIAL"]).assign(CIUDAD="BOGOTA")


def _grupos_de(res, nombres: tuple[str, ...]) -> set[int]:
    corr = res.correlativa
    return set(corr.loc[corr["RAZON_SOCIAL"].isin(nombres), "ID_GRUPO"].tolist())


def _manifiesto(work_dir: Path) -> dict:
    return json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.slow
def test_linkage_parte_la_cadena_solo_con_la_perilla_activa(tmp_path) -> None:
    fuente = _fuente_encadenada()
    andariega = ("ANDARIEGA BRUMOSA SAS", "ANDARIEGA BRUMOSA S.A.S.")
    brumosa = ("CALICANTO DESTELLO SAS", "CALICANTO DESTELLO S.A.S.")

    apagada = linkage(
        sources={"CRM": fuente},
        work_dir=str(tmp_path / "apagada"),
        skip_reporting=True,
        ajustes_perfil={"cobertura_sin_identificador": {"activa": False}},
    )
    assert _grupos_de(apagada, andariega) == _grupos_de(apagada, brumosa), (
        "sin cobertura el puente encadena las dos entidades (precondición de la prueba)"
    )
    meta_apagada = _manifiesto(tmp_path / "apagada")["L5_golden"]["meta"]
    assert "cobertura_sin_identificador" not in meta_apagada

    encendida = linkage(
        sources={"CRM": fuente}, work_dir=str(tmp_path / "encendida"), skip_reporting=True
    )
    ga, gb = _grupos_de(encendida, andariega), _grupos_de(encendida, brumosa)
    assert len(ga) == 1 and len(gb) == 1, "cada entidad conserva sus variantes"
    assert ga != gb, "la cobertura parte la cadena"
    assert set(encendida.correlativa["ID_GRUPO"]) >= ga | gb
    meta = _manifiesto(tmp_path / "encendida")["L5_golden"]["meta"]
    reporte = meta["cobertura_sin_identificador"]
    assert reporte["umbral"] == 0.80
    assert reporte["grupos_partidos"] >= 1
    # Las entidades con NIT válido no se tocaron: 2 variantes → 1 grupo cada una.
    for giro in _PRIMERAS[4:]:
        assert (
            len(_grupos_de(encendida, (f"LOGISTICA {giro} SAS", f"LOGISTICA {giro} S.A.S."))) == 1
        )


# ── Paridad: con la perilla apagada el banco reproduce la huella de F0 ─────

#: Huella de la partición del banco institucional en 0.22.4 / F0 (ADR-0011,
#: `docs/evidencia/corrida_base_f0.json`). Se fija aquí, no en `BANCO_F0`,
#: porque esa constante seguirá al tronco cuando la cobertura quede activa por
#: defecto y ESTA prueba mide lo contrario: que apagarla devuelve 0.22.4 exacto.
HUELLA_SIN_COBERTURA = "1e365ba81c4df45e410dd09154cafef1d38e96d9fb2846998260c10ad69cbe31"
RAIZ = Path(__file__).resolve().parents[1]
DATOS_BANCO = RAIZ / "data" / "benchmark" / "benchmark_institucional.csv.gz"


@pytest.mark.slow
def test_con_la_perilla_apagada_el_banco_reproduce_la_huella_de_f0(tmp_path: Path) -> None:
    """`activa: False` conserva la partición de 0.22.4 exactamente (≈ 50 s).

    Es la mitad «paridad» del cambio declarado: la otra mitad (el valor por
    defecto activo) la mide `tests/test_banco_linea_base.py` contra la línea
    base que el coordinador mueve con el ADR. Mide en temporales, no publica.
    """
    from record_linkage.evaluation.banco import EspecificacionBanco, correr_banco

    assert DATOS_BANCO.is_file(), f"no existe el conjunto de referencia {DATOS_BANCO}"
    corrida = correr_banco(
        EspecificacionBanco(
            etiqueta="f21_paridad",
            datos=DATOS_BANCO,
            perfil="produccion_estandar",
            ajustes_perfil={"cobertura_sin_identificador": {"activa": False}},
            dir_trabajo=tmp_path / "trabajo",
            dir_evidencia=tmp_path / "evidencia",
        )
    )
    assert corrida.huella == HUELLA_SIN_COBERTURA, (
        f"con la cobertura apagada la huella debía ser {HUELLA_SIN_COBERTURA[:16]}… y fue "
        f"{corrida.huella[:16]}…: algo distinto de la perilla cambió la partición"
    )
    assert corrida.calidad.fp_que_tocan_negativo == 287
    assert corrida.calidad.macro_f1 == pytest.approx(0.8920, abs=5e-4)


# ── scripts/banco.py: --ajuste con clave anidada (para medir el barrido) ───


def test_banco_parsea_ajustes_anidados_con_punto() -> None:
    from cargar_script import cargar_script

    banco = cargar_script("banco", nombre_modulo="banco_script_f21")
    ajustes = banco._parsear_ajustes(
        ["cobertura_sin_identificador.umbral=0.70", "cobertura_sin_identificador.activa=true"]
    )
    assert ajustes == {"cobertura_sin_identificador": {"umbral": 0.70, "activa": True}}
    assert banco._parsear_ajustes(["cobertura_sin_identificador=false", "lsh_threshold=0.5"]) == {
        "cobertura_sin_identificador": False,
        "lsh_threshold": 0.5,
    }
    with pytest.raises(SystemExit):
        banco._parsear_ajustes(["a=1", "a.b=2"])
