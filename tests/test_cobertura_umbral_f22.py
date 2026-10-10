"""F2.2 · Umbral medido, no adivinado (barrido de ``cobertura_sin_identificador.umbral``).

Tres piezas, cada una con su prueba:

1. El banco mide lo que la compuerta exige. ``evaluar_calidad`` reporta el
   grupo predicho mayor entre los que NO tienen identificador (todas sus filas
   con régimen ``SIN_*``): filas, entidades verdaderas que mezcla y cuántos de
   esos grupos llegan a 50 filas. Antes se calculaba a mano sobre el parquet.
2. Los dos instrumentos aceptan la perilla. ``parsear_ajustes`` vive en la
   librería (una regla, un sitio) y ``scripts/escala.py --ajuste`` la pasa al
   banco igual que ``scripts/banco.py --ajuste``; ``scripts/banco.py
   --evidencia`` deja la corrida donde se le pide y no en ``docs/evidencia/``.
3. La decisión es una función, no un párrafo. ``elegir_umbral`` aplica la
   compuerta de F2 y la regla de Enrique (unir mal duele más que dejar sin
   unir; desempate hacia la precisión) a la tabla medida, y
   ``docs/evidencia/cobertura_umbral.json`` —la tabla de decisión que cita el
   ADR-0011— debe reproducir con esa función el umbral que trae el perfil.
4. El script compone la tabla. ``scripts/cobertura_umbral.py`` lee las
   corridas de los dos instrumentos por su etiqueta (``u<umbral>``), verifica
   que cada una se midió con el umbral que se le atribuye, escribe la tabla
   donde se le pide y resume la decisión.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from cargar_script import cargar_script

from record_linkage.config.profiles import PERFILES_BASE
from record_linkage.engine.cobertura import ConfigCoberturaSinIdentificador
from record_linkage.evaluation import cobertura_umbral as cu
from record_linkage.evaluation.banco import (
    FILAS_GRUPO_GRANDE,
    Corrida,
    MetricasCalidad,
    MetricasRecursos,
    evaluar_calidad,
    parsear_ajustes,
)

RAIZ = Path(__file__).resolve().parents[1]
EVIDENCIA = RAIZ / "docs" / "evidencia" / "cobertura_umbral.json"


# ── 1. El banco mide el grupo mayor sin identificador ─────────────────────


def _referencia(regimen: list[str], verdad: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ID_GROUP": verdad,
            "REGIMEN": regimen,
            "CASO": ["positivo"] * len(verdad),
        }
    )


def test_el_grupo_mayor_sin_identificador_cuenta_filas_y_entidades() -> None:
    """Un grupo predicho de 3 filas SIN_ID que mezcla 2 entidades es el mayor;
    el grupo con una fila CON_ID no cuenta aunque sea más grande."""
    referencia = _referencia(
        regimen=["SIN_ID", "SIN_ID", "SIN_ID", "CON_ID", "SIN_ID", "SIN_ID", "SIN_ID"],
        verdad=["a", "a", "b", "c", "c", "c", "c"],
    )
    metricas = evaluar_calidad(referencia, np.array([1, 1, 1, 2, 2, 2, 2]))
    assert metricas.grupo_mayor_sin_identificador == {
        "filas": 3,
        "entidades": 2,
        "grupos": 1,
        "grupos_grandes": 0,
    }


def test_el_grupo_mayor_sin_identificador_vale_cero_si_no_hay_grupos_sin_id() -> None:
    referencia = _referencia(regimen=["CON_NIT", "CON_NIT"], verdad=["a", "a"])
    metricas = evaluar_calidad(referencia, np.array([1, 1]))
    assert metricas.grupo_mayor_sin_identificador == {
        "filas": 0,
        "entidades": 0,
        "grupos": 0,
        "grupos_grandes": 0,
    }


def test_los_grupos_grandes_se_cuentan_desde_el_umbral_de_la_compuerta() -> None:
    """La compuerta de F2 dice «grupo mayor sin identificador ≤ 50»; el conteo
    de grupos grandes usa esa misma cifra, declarada una vez."""
    assert FILAS_GRUPO_GRANDE == 50
    n = FILAS_GRUPO_GRANDE
    referencia = _referencia(regimen=["SIN_NIT"] * (2 * n), verdad=[str(i) for i in range(2 * n)])
    prediccion = np.array([1] * n + [2] * (n - 1) + [3])
    metricas = evaluar_calidad(referencia, prediccion)
    assert metricas.grupo_mayor_sin_identificador["filas"] == n
    assert metricas.grupo_mayor_sin_identificador["entidades"] == n
    assert metricas.grupo_mayor_sin_identificador["grupos_grandes"] == 1
    assert metricas.grupo_mayor_sin_identificador["grupos"] == 3


def test_una_corrida_vieja_sin_el_campo_se_sigue_cargando() -> None:
    """El campo es aditivo: ``corrida_base_f0.json`` no lo trae y debe seguir leyéndose."""
    crudo = json.loads((RAIZ / "docs" / "evidencia" / "corrida_base_f0.json").read_text("utf-8"))
    assert "grupo_mayor_sin_identificador" not in crudo["calidad"]
    metricas = MetricasCalidad(**crudo["calidad"])
    assert metricas.grupo_mayor_sin_identificador == {}


# ── 2. Los dos instrumentos aceptan la perilla ────────────────────────────


def test_parsear_ajustes_vive_en_la_libreria_y_anida_las_claves_con_punto() -> None:
    ajustes = parsear_ajustes(
        ["cobertura_sin_identificador.umbral=0.75", "cobertura_sin_identificador.activa=true"]
    )
    assert ajustes == {"cobertura_sin_identificador": {"umbral": 0.75, "activa": True}}
    assert parsear_ajustes(["lsh_threshold=0.4", "nota=x", "n=3"]) == {
        "lsh_threshold": 0.4,
        "nota": "x",
        "n": 3,
    }
    assert parsear_ajustes([]) == {}


def test_parsear_ajustes_falla_rapido_ante_un_par_sin_igual_o_una_clave_mixta() -> None:
    with pytest.raises(ValueError, match="CLAVE=VALOR"):
        parsear_ajustes(["umbral"])
    with pytest.raises(ValueError, match="escalar y diccionario"):
        parsear_ajustes(["a=1", "a.b=2"])


def test_el_script_del_banco_reutiliza_el_parseador_de_la_libreria() -> None:
    banco_script = cargar_script("banco")
    assert banco_script.parsear_ajustes is parsear_ajustes
    assert not hasattr(banco_script, "_parsear_ajustes")


def _corrida_falsa(etiqueta: str) -> Corrida:
    return Corrida(
        etiqueta=etiqueta,
        version="0.0.0-prueba",
        perfil="produccion_estandar",
        marca_tiempo="2026-01-01T00:00:00",
        entorno={"python": "3.x", "pandas": "3.x", "numpy": "2.x", "plataforma": "prueba"},
        calidad=MetricasCalidad(
            registros=2,
            grupos_verdad=1,
            grupos_predichos=1,
            pares_verdaderos=1,
            tp=1,
            fp=0,
            fn=0,
            precision=1.0,
            recall=1.0,
            f1=1.0,
            b3_precision=1.0,
            b3_recall=1.0,
            b3_f1=1.0,
            macro_f1=0.9,
        ),
        recursos=MetricasRecursos(segundos_total=0.1, candidatos=1, pares_scoreados=1),
        huella="prueba",
        especificacion={"ajustes_perfil": None},
    )


def test_escala_pasa_los_ajustes_al_banco_y_los_deja_en_el_json(
    tmp_path: Path, monkeypatch
) -> None:
    escala = cargar_script("escala")
    params = escala.ParametrosConjunto(empresas_extra=1, importadores_extra=1, filas_esperadas=1)
    dir_datos = tmp_path / "datos"
    dir_datos.mkdir()
    (dir_datos / params.nombre_archivo).write_text(
        "ID_REGISTRO,ID_GROUP,REGIMEN,NIT,RAZON_SOCIAL,FUENTE,CASO\n"
        "1,g1,SIN_NIT,,EMPRESA FICTICIA SAS,A,positivo_sin_nit\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(escala, "TAMANOS", {"mini": params})
    vistos: dict[str, Any] = {}

    def _banco_falso(espec: Any, *, silencioso: bool = True) -> Corrida:
        vistos["espec"] = espec
        corrida = _corrida_falsa(espec.etiqueta)
        return Corrida(
            **{
                **corrida.__dict__,
                "especificacion": {"ajustes_perfil": dict(espec.ajustes_perfil or {}) or None},
            }
        )

    monkeypatch.setattr(escala, "correr_banco", _banco_falso)
    ajustes = {"cobertura_sin_identificador": {"umbral": 0.75}}
    opciones = escala.OpcionesCorrida(
        etiqueta="u075",
        dir_datos=dir_datos,
        dir_evidencia=tmp_path / "evidencia",
        ajustes_perfil=ajustes,
    )
    escala.correr_escala(("mini",), opciones)

    assert vistos["espec"].ajustes_perfil == ajustes
    documento = escala.cargar_evidencia(tmp_path / "evidencia", "u075")
    assert documento["ajustes_perfil"] == ajustes
    assert documento["tamanos"]["mini"]["especificacion"]["ajustes_perfil"] == ajustes


def test_escala_cli_acepta_ajuste_con_clave_anidada() -> None:
    escala = cargar_script("escala")
    args = escala.construir_parser().parse_args(
        ["--etiqueta", "x", "--ajuste", "cobertura_sin_identificador.umbral=0.84"]
    )
    assert parsear_ajustes(args.ajuste) == {"cobertura_sin_identificador": {"umbral": 0.84}}


def test_el_banco_cli_deja_la_corrida_en_la_carpeta_de_evidencia_pedida(
    tmp_path: Path, monkeypatch
) -> None:
    """``docs/evidencia/`` es del coordinador: un barrido se corre con ``--evidencia``."""
    banco_script = cargar_script("banco")
    vistos: dict[str, Any] = {}

    def _banco_falso(espec: Any, **_k: Any) -> Corrida:
        vistos["espec"] = espec
        return _corrida_falsa(espec.etiqueta)

    monkeypatch.setattr(banco_script, "correr_banco", _banco_falso)
    evidencia = tmp_path / "barrido"
    codigo = banco_script.main(
        [
            "--etiqueta",
            "u070",
            "--datos",
            str(tmp_path / "datos.csv"),
            "--evidencia",
            str(evidencia),
            "--ajuste",
            "cobertura_sin_identificador.umbral=0.70",
        ]
    )
    assert codigo == 0
    assert (evidencia / "corrida_u070.json").is_file()
    assert vistos["espec"].dir_evidencia == evidencia
    assert vistos["espec"].ajustes_perfil == {"cobertura_sin_identificador": {"umbral": 0.70}}


def test_el_banco_cli_compara_en_la_carpeta_de_evidencia_pedida(tmp_path: Path) -> None:
    banco_script = cargar_script("banco")
    for etiqueta in ("a", "b"):
        _corrida_falsa(etiqueta).guardar(tmp_path)
    assert banco_script.main(["--comparar", "a", "b", "--evidencia", str(tmp_path)]) == 0


# ── 3. La decisión es una función ─────────────────────────────────────────


def _punto(
    umbral: float,
    *,
    macro_f1: float = 0.89,
    precision: float = 0.95,
    recall: float = 0.80,
    fp_negativos: int = 250,
    grupo_mayor: int | None = 13,
    precision_escala: float | None = 0.98,
    fp_negativos_escala: int | None = 34,
) -> cu.PuntoUmbral:
    return cu.PuntoUmbral(
        umbral=umbral,
        banco=cu.MedidaBanco(
            precision=precision,
            recall=recall,
            f1=2 * precision * recall / (precision + recall),
            macro_f1=macro_f1,
            fp_que_tocan_negativo=fp_negativos,
            recall_por_regimen={"CON_ID": 0.95, "SIN_ID": recall},
            calidad_por_estrato={},
            huella="h" + str(umbral),
        ),
        escala=(
            None
            if grupo_mayor is None
            else cu.MedidaEscala(
                filas=139_028,
                precision=precision_escala or 0.0,
                recall=0.94,
                f1=0.95,
                fp_que_tocan_negativo=fp_negativos_escala or 0,
                recall_por_regimen={"CON_NIT": 0.98, "SIN_NIT": 0.7},
                grupo_mayor_sin_identificador={
                    "filas": grupo_mayor,
                    "entidades": 2,
                    "grupos": 100,
                    "grupos_grandes": int(grupo_mayor >= 50),
                },
                huella="e" + str(umbral),
            )
        ),
    )


def test_la_compuerta_de_f2_es_la_del_plan() -> None:
    compuerta = cu.CompuertaF2()
    assert compuerta.macro_f1_minimo == 0.880
    assert compuerta.fp_negativos_maximo == 287
    assert compuerta.grupo_mayor_maximo == 50


def test_la_compuerta_descarta_y_dice_por_que() -> None:
    puntos = [
        _punto(0.70, macro_f1=0.8935, fp_negativos=277, grupo_mayor=100),
        _punto(0.75, macro_f1=0.8906, fp_negativos=264, grupo_mayor=33),
        _punto(0.80, macro_f1=0.8803, fp_negativos=246, grupo_mayor=13),
        _punto(0.84, macro_f1=0.8647, fp_negativos=180, grupo_mayor=11),
        _punto(0.90, macro_f1=0.8900, fp_negativos=300, grupo_mayor=10),
    ]
    decision = cu.elegir_umbral(puntos)
    motivos = {d.umbral: d.motivo for d in decision.descartados}
    assert set(motivos) == {0.70, 0.84, 0.90}
    assert "grupo mayor" in motivos[0.70] and "100" in motivos[0.70]
    assert "macro-F1" in motivos[0.84]
    assert "FP" in motivos[0.90]
    assert [p.umbral for p in decision.candidatos] == [0.75, 0.80]


def test_entre_los_que_pasan_gana_el_que_menos_une_mal() -> None:
    """Regla de Enrique: 0,75 tiene mejor macro-F1 pero más FP sobre negativos;
    el elegido es 0,80. La alternativa «mayor macro-F1» queda registrada."""
    puntos = [
        _punto(0.75, macro_f1=0.8906, fp_negativos=264, precision=0.9513),
        _punto(0.80, macro_f1=0.8803, fp_negativos=246, precision=0.9557),
    ]
    decision = cu.elegir_umbral(puntos)
    assert decision.umbral == 0.80
    assert decision.alternativa_mayor_macro_f1 == 0.75
    assert "FP" in decision.motivo


def test_a_igual_fp_sobre_negativos_desempata_la_precision() -> None:
    puntos = [
        _punto(0.75, macro_f1=0.89, fp_negativos=250, precision=0.95, precision_escala=0.92),
        _punto(0.80, macro_f1=0.88, fp_negativos=250, precision=0.95, precision_escala=0.98),
        _punto(0.78, macro_f1=0.88, fp_negativos=250, precision=0.96, precision_escala=0.90),
    ]
    decision = cu.elegir_umbral(puntos)
    assert decision.umbral == 0.78
    assert cu.elegir_umbral(puntos[:2]).umbral == 0.80


def test_sin_escala_la_compuerta_del_grupo_mayor_no_se_evalua_pero_se_declara() -> None:
    puntos = [_punto(0.70, grupo_mayor=None), _punto(0.80, grupo_mayor=None)]
    decision = cu.elegir_umbral(puntos)
    assert decision.umbral == 0.80
    assert "sin medición a escala" in decision.motivo


def test_si_nada_pasa_la_compuerta_se_falla_con_mensaje_accionable() -> None:
    with pytest.raises(cu.SinUmbralAdmisibleError, match="Qué hacer"):
        cu.elegir_umbral([_punto(0.80, macro_f1=0.5)])
    with pytest.raises(ValueError, match=r"(?s)repetido.*Qué hacer"):
        cu.elegir_umbral([_punto(0.80), _punto(0.80)])
    with pytest.raises(ValueError, match=r"(?s)al menos un punto.*Qué hacer"):
        cu.elegir_umbral([])


def _escala_falsa(directorio: Path, etiqueta: str, umbral: float) -> Path:
    """Deja en ``directorio`` un ``escala_<etiqueta>.json`` mínimo (solo 139k)."""
    escala_doc = {
        "etiqueta": etiqueta,
        "ajustes_perfil": {"cobertura_sin_identificador": {"umbral": umbral}},
        "tamanos": {
            "139k": {
                "filas": 139_028,
                "calidad": {
                    "precision": 0.98,
                    "recall": 0.94,
                    "f1": 0.96,
                    "fp_que_tocan_negativo": 34,
                    "recall_por_regimen": {"CON_NIT": 0.98, "SIN_NIT": 0.71},
                    "grupo_mayor_sin_identificador": {
                        "filas": 13,
                        "entidades": 2,
                        "grupos": 1,
                        "grupos_grandes": 0,
                    },
                },
                "huella": "e",
                "recursos": {"segundos_total": 400.0},
            }
        },
    }
    destino = directorio / f"escala_{etiqueta}.json"
    destino.write_text(json.dumps(escala_doc), encoding="utf-8")
    return destino


def test_la_tabla_se_arma_desde_los_json_de_los_dos_instrumentos(tmp_path: Path) -> None:
    corrida = _corrida_falsa("u080")
    corrida.guardar(tmp_path)
    _escala_falsa(tmp_path, "u080", 0.8)
    puntos = cu.puntos_desde_evidencia(tmp_path, {0.80: "u080"}, tamano="139k")
    assert len(puntos) == 1 and puntos[0].umbral == 0.80
    assert puntos[0].banco.huella == "prueba"
    assert puntos[0].escala is not None
    assert puntos[0].escala.grupo_mayor_sin_identificador["filas"] == 13
    tabla = cu.armar_tabla(puntos, nota="prueba")
    assert tabla["decision"]["umbral"] == 0.80
    assert tabla["compuerta"] == {
        "macro_f1_minimo": 0.88,
        "fp_negativos_maximo": 287,
        "grupo_mayor_maximo": 50,
    }
    assert [fila["umbral"] for fila in tabla["umbrales"]] == [0.80]
    destino = cu.escribir_tabla(tabla, tmp_path / "cobertura_umbral.json")
    assert json.loads(destino.read_text("utf-8")) == tabla


def test_sin_corrida_a_escala_el_punto_lo_declara(tmp_path: Path) -> None:
    _corrida_falsa("u084").guardar(tmp_path)
    puntos = cu.puntos_desde_evidencia(tmp_path, {0.84: "u084"}, tamano="139k")
    assert puntos[0].escala is None
    with pytest.raises(FileNotFoundError, match="u099"):
        cu.puntos_desde_evidencia(tmp_path, {0.99: "u099"}, tamano="139k")


# ── 4. El script compone la tabla ─────────────────────────────────────────


def _corrida_falsa_con_umbral(etiqueta: str, umbral: float | None) -> Corrida:
    """Una corrida del banco que declara con qué umbral se midió (o ninguno)."""
    base = _corrida_falsa(etiqueta)
    ajustes = None if umbral is None else {"cobertura_sin_identificador": {"umbral": umbral}}
    return Corrida(**{**base.__dict__, "especificacion": {"ajustes_perfil": ajustes}})


def test_la_etiqueta_del_script_es_la_convencion_con_que_se_corren_banco_y_escala() -> None:
    """``u<umbral a dos decimales sin punto>``: si cambia, el script lee corridas
    equivocadas o no las encuentra."""
    script = cargar_script("cobertura_umbral")
    assert script.etiqueta_de(0.70) == "u070"
    assert script.etiqueta_de(0.8) == "u080"
    assert script.etiqueta_de(0.84) == "u084"


def test_las_etiquetas_propias_se_parsean_y_fallan_rapido_si_vienen_mal() -> None:
    script = cargar_script("cobertura_umbral")
    assert script._parsear_etiquetas(["0.75=mio"]) == {0.75: "mio"}
    assert script._parsear_etiquetas([]) == {}
    with pytest.raises(SystemExit, match="UMBRAL=ETIQUETA"):
        script._parsear_etiquetas(["x"])
    with pytest.raises(SystemExit, match="UMBRAL=ETIQUETA"):
        script._parsear_etiquetas(["abc=mio"])


def test_el_script_compone_la_tabla_desde_las_corridas_del_barrido(tmp_path: Path) -> None:
    """Dos corridas del banco (u075 y u080, una con escala), una apagada como
    referencia: el script escribe la tabla donde se le pide y elige 0,80."""
    script = cargar_script("cobertura_umbral")
    _corrida_falsa_con_umbral("u075", 0.75).guardar(tmp_path)
    _corrida_falsa_con_umbral("u080", 0.80).guardar(tmp_path)
    _corrida_falsa_con_umbral("apagada", None).guardar(tmp_path)
    _escala_falsa(tmp_path, "u080", 0.80)
    salida = tmp_path / "t.json"

    codigo = script.main(
        [
            "--evidencia",
            str(tmp_path),
            "--umbrales",
            "0.75",
            "0.80",
            "--sin-estrellas",
            "apagada",
            "--salida",
            str(salida),
        ]
    )

    assert codigo == 0
    assert salida.is_file()
    tabla = json.loads(salida.read_text(encoding="utf-8"))
    assert tabla["decision"]["umbral"] == 0.80
    assert tabla["sin_estrellas"]["etiqueta"] == "apagada"
    assert [fila["umbral"] for fila in tabla["umbrales"]] == [0.75, 0.80]
    assert tabla["umbrales"][0]["escala"] is None
    assert tabla["umbrales"][1]["escala"]["grupo_mayor_sin_identificador"]["filas"] == 13


def test_el_script_falla_si_la_corrida_no_se_midio_con_el_umbral_que_se_le_atribuye(
    tmp_path: Path,
) -> None:
    script = cargar_script("cobertura_umbral")
    _corrida_falsa_con_umbral("mio", 0.75).guardar(tmp_path)
    with pytest.raises(ValueError, match=r"se midió con umbral 0\.75"):
        script.main(["--evidencia", str(tmp_path), "--umbrales", "0.80", "--etiquetas", "0.80=mio"])


def test_el_resumen_del_script_dice_el_elegido_con_y_sin_escala() -> None:
    script = cargar_script("cobertura_umbral")
    con_escala = cu.armar_tabla([_punto(0.75), _punto(0.80)])
    sin_escala = cu.armar_tabla([_punto(0.75, grupo_mayor=None), _punto(0.80, grupo_mayor=None)])
    for tabla in (con_escala, sin_escala):
        texto = script.resumen(json.loads(json.dumps(tabla)))
        assert "ELEGIDO 0.80" in texto
        filas = [linea for linea in texto.splitlines() if linea.strip()[:4] in {"0.75", "0.80"}]
        assert len(filas) == 2
        # Sin escala, las columnas de 139k se marcan con «—»; con escala traen cifras.
        assert all(("—" in fila) == (tabla["umbrales"][0]["escala"] is None) for fila in filas)


# ── La tabla de decisión del repositorio ──────────────────────────────────


@pytest.fixture(scope="module")
def tabla() -> dict[str, Any]:
    assert EVIDENCIA.is_file(), (
        f"no existe {EVIDENCIA.relative_to(RAIZ)}: es la tabla de decisión del umbral "
        f"(F2.2, ADR-0011); se produce con scripts/cobertura_umbral.py"
    )
    return json.loads(EVIDENCIA.read_text(encoding="utf-8"))


def test_la_tabla_de_decision_cubre_el_barrido_del_plan(tabla: dict[str, Any]) -> None:
    umbrales = [fila["umbral"] for fila in tabla["umbrales"]]
    assert umbrales == sorted(umbrales)
    assert {0.70, 0.75, 0.80, 0.84} <= set(umbrales)
    for fila in tabla["umbrales"]:
        banco = fila["banco"]
        assert {"precision", "recall", "macro_f1", "fp_que_tocan_negativo"} <= set(banco)
        assert "SIN_ID" in banco["recall_por_regimen"]
        assert set(banco["calidad_por_estrato"]) >= {"CONTACTO", "RUIDO"}
        assert fila["escala"] is not None, f"falta la medición a 139k del umbral {fila['umbral']}"
        assert fila["escala"]["filas"] == 139_028
        assert "filas" in fila["escala"]["grupo_mayor_sin_identificador"]


def test_el_umbral_elegido_es_el_del_perfil_y_pasa_la_compuerta(tabla: dict[str, Any]) -> None:
    perfil = ConfigCoberturaSinIdentificador.desde_perfil(
        PERFILES_BASE["produccion_estandar"]["cobertura_sin_identificador"]
    )
    assert perfil.activa
    assert tabla["decision"]["umbral"] == perfil.umbral
    elegido = next(f for f in tabla["umbrales"] if f["umbral"] == perfil.umbral)
    compuerta = cu.CompuertaF2(**tabla["compuerta"])
    assert elegido["banco"]["macro_f1"] >= compuerta.macro_f1_minimo
    assert elegido["banco"]["fp_que_tocan_negativo"] <= compuerta.fp_negativos_maximo
    assert (
        elegido["escala"]["grupo_mayor_sin_identificador"]["filas"] <= compuerta.grupo_mayor_maximo
    )


def test_la_regla_reproduce_la_decision_registrada(tabla: dict[str, Any]) -> None:
    """La tabla no es un párrafo: `elegir_umbral` sobre sus filas da lo que dice."""
    puntos = cu.puntos_desde_tabla(tabla)
    decision = cu.elegir_umbral(puntos, cu.CompuertaF2(**tabla["compuerta"]))
    assert decision.umbral == tabla["decision"]["umbral"]
    assert decision.alternativa_mayor_macro_f1 == tabla["decision"]["alternativa_mayor_macro_f1"]
    assert sorted(d.umbral for d in decision.descartados) == sorted(
        d["umbral"] for d in tabla["decision"]["descartados"]
    )
