"""Compuerta de escala (scripts/escala.py) — pruebas rápidas (F0.5).

La compuerta decide si un cambio puede entrar según el tiempo, la memoria y
los candidatos a 139k y 463k registros. Un instrumento sin calibrar no sirve:
aquí se verifica (1) que la comparación pura falla exactamente cuando debe,
nombra la fase culpable y decide por la línea global de la compuerta (incluida
una fase corta, con y sin ``--holgura-segundos``), (2) que la tabla de tamaños
está fijada a la línea base medida, (3) que la generación cachea por parámetros
y reutiliza el archivo, y (4) que una corrida deja en ``--evidencia`` solo el
JSON y toma el entorno del banco sin recalcularlo.

Ninguna prueba corre ``linkage()``: el conjunto diminuto (900/180) se genera en
``tmp_path`` con el generador real, en menos de dos segundos; las corridas se
prueban con ``correr_banco`` sustituido por una ``Corrida`` falsa.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from cargar_script import cargar_script

# escala.py es un script (no parte del paquete): se carga por ruta.
escala = cargar_script("escala")


# ── Utilidades ────────────────────────────────────────────────────────────


def _corrida(
    *,
    total: float = 400.0,
    fases: dict[str, float] | None = None,
    rss: float = 1000.0,
    candidatos: int | None = 10_000_000,
    f1: float = 0.5,
) -> dict[str, Any]:
    """Entrada mínima de un tamaño dentro de ``escala_<etiqueta>.json``."""
    fases = {"L2_lsh_candidates": 250.0, "L3_scoring": 150.0} if fases is None else fases
    return {
        "filas": 139_028,
        "recursos": {
            "segundos_total": total,
            "segundos_por_fase": fases,
            "rss_pico_mib": rss,
            "candidatos": candidatos,
        },
        "calidad": {"f1": f1, "precision": 0.4, "recall": 0.9},
        "huella": "abc",
    }


def _evidencia(etiqueta: str, **tamanos: dict[str, Any]) -> dict[str, Any]:
    return {"etiqueta": etiqueta, "tamanos": tamanos}


# ── Comparación pura ──────────────────────────────────────────────────────


def _culpables(informe: Any) -> list[str]:
    """Métricas que no pasaron, en el orden de la tabla."""
    return [v.metrica for c in informe.comparaciones for v in c.veredictos if not v.pasa]


def _veredicto_global(informe: Any) -> str:
    """Última línea del informe: la de la compuerta, no la de cada tamaño."""
    texto = informe.resumen().rstrip()
    ultima = texto.splitlines()[-1]
    assert ultima.lstrip().startswith("VEREDICTO ESCALA"), ultima
    return ultima


def test_corridas_identicas_pasan() -> None:
    informe = escala.comparar_escala(
        _evidencia("a", k139=_corrida()), _evidencia("b", k139=_corrida())
    )
    assert informe.pasa
    assert _veredicto_global(informe).endswith("PASA")


def test_una_fase_que_sube_once_por_ciento_falla_y_la_nombra() -> None:
    nueva = _corrida(fases={"L2_lsh_candidates": 250.0 * 1.11, "L3_scoring": 150.0})
    informe = escala.comparar_escala(_evidencia("a", k139=_corrida()), _evidencia("b", k139=nueva))
    assert not informe.pasa
    assert _culpables(informe) == ["L2_lsh_candidates"]
    assert "L2_lsh_candidates" in informe.resumen()
    assert _veredicto_global(informe).endswith("FALLA")


def test_con_dos_tamanos_basta_que_uno_falle() -> None:
    """La línea de cada tamaño puede decir PASA; la de la compuerta manda."""
    base = _evidencia("a", k139=_corrida(), k463=_corrida())
    nueva = _evidencia(
        "b",
        k139=_corrida(),
        k463=_corrida(fases={"L2_lsh_candidates": 250.0 * 1.11, "L3_scoring": 150.0}),
    )
    informe = escala.comparar_escala(base, nueva)
    assert informe.pasa is False
    assert [c.pasa for c in informe.comparaciones] == [True, False]
    texto = informe.resumen()
    assert "VEREDICTO: PASA" in texto and "VEREDICTO: FALLA" in texto
    assert _veredicto_global(informe).endswith("FALLA")


# ── Fases cortas y holgura absoluta ───────────────────────────────────────

_FASES_CORTAS = {"L2_lsh_candidates": 250.0, "L3_scoring": 150.0, "L4_clustering": 2.4}


def test_una_fase_corta_que_sube_mas_de_diez_por_ciento_falla_por_defecto() -> None:
    """La especificación no distingue fases cortas: 2,4 → 2,9 s (+21 %) falla."""
    nueva = _corrida(fases={**_FASES_CORTAS, "L4_clustering": 2.9})
    informe = escala.comparar_escala(
        _evidencia("a", k139=_corrida(fases=_FASES_CORTAS)), _evidencia("b", k139=nueva)
    )
    assert escala.HOLGURA_SEGUNDOS == 0.0
    assert not informe.pasa
    assert _culpables(informe) == ["L4_clustering"]


def test_una_fase_corta_dentro_de_la_holgura_explicita_pasa() -> None:
    nueva = _corrida(fases={**_FASES_CORTAS, "L4_clustering": 2.9})
    informe = escala.comparar_escala(
        _evidencia("a", k139=_corrida(fases=_FASES_CORTAS)),
        _evidencia("b", k139=nueva),
        holgura_segundos=2.0,
    )
    assert informe.pasa


def test_una_fase_corta_fuera_de_la_holgura_explicita_falla_y_la_nombra() -> None:
    nueva = _corrida(fases={**_FASES_CORTAS, "L4_clustering": 4.5})
    informe = escala.comparar_escala(
        _evidencia("a", k139=_corrida(fases=_FASES_CORTAS)),
        _evidencia("b", k139=nueva),
        holgura_segundos=2.0,
    )
    assert not informe.pasa
    assert _culpables(informe) == ["L4_clustering"]
    assert "L4_clustering" in informe.resumen()


def test_la_holgura_no_afloja_una_fase_larga() -> None:
    """Con 2 s de holgura, L2 (250 s) sigue regido por el 10 %: +11 % falla."""
    nueva = _corrida(fases={**_FASES_CORTAS, "L2_lsh_candidates": 250.0 * 1.11})
    informe = escala.comparar_escala(
        _evidencia("a", k139=_corrida(fases=_FASES_CORTAS)),
        _evidencia("b", k139=nueva),
        holgura_segundos=2.0,
    )
    assert _culpables(informe) == ["L2_lsh_candidates"]


def test_una_fase_que_sube_nueve_por_ciento_pasa() -> None:
    nueva = _corrida(fases={"L2_lsh_candidates": 250.0 * 1.09, "L3_scoring": 150.0 * 1.09})
    assert escala.comparar_escala(
        _evidencia("a", k139=_corrida()), _evidencia("b", k139=nueva)
    ).pasa


def test_el_tiempo_total_que_sube_once_por_ciento_falla() -> None:
    informe = escala.comparar_escala(
        _evidencia("a", k139=_corrida()), _evidencia("b", k139=_corrida(total=400.0 * 1.11))
    )
    assert not informe.pasa
    assert any(
        v.metrica == "segundos_total" and not v.pasa
        for c in informe.comparaciones
        for v in c.veredictos
    )


def test_el_rss_pico_que_sube_once_por_ciento_falla_y_nueve_pasa() -> None:
    base = _evidencia("a", k139=_corrida())
    assert not escala.comparar_escala(base, _evidencia("b", k139=_corrida(rss=1110.0))).pasa
    assert escala.comparar_escala(base, _evidencia("b", k139=_corrida(rss=1090.0))).pasa


@pytest.mark.parametrize(
    ("factor", "esperado"), [(1.11, False), (0.89, False), (1.09, True), (0.91, True)]
)
def test_los_candidatos_se_vigilan_en_ambas_direcciones(factor: float, esperado: bool) -> None:
    """Menos candidatos también es un cambio: puede ser recall perdido."""
    base = _evidencia("a", k139=_corrida())
    nueva = _evidencia("b", k139=_corrida(candidatos=int(10_000_000 * factor)))
    assert escala.comparar_escala(base, nueva).pasa is esperado


def test_bajar_el_tiempo_siempre_pasa() -> None:
    nueva = _corrida(total=200.0, fases={"L2_lsh_candidates": 100.0, "L3_scoring": 50.0}, rss=500.0)
    assert escala.comparar_escala(
        _evidencia("a", k139=_corrida()), _evidencia("b", k139=nueva)
    ).pasa


def test_una_fase_ausente_en_la_nueva_falla() -> None:
    nueva = _corrida(fases={"L2_lsh_candidates": 250.0})
    informe = escala.comparar_escala(_evidencia("a", k139=_corrida()), _evidencia("b", k139=nueva))
    assert not informe.pasa
    assert "L3_scoring" in informe.resumen()


def test_sin_dato_de_candidatos_falla_en_vez_de_pasar_en_silencio() -> None:
    nueva = _corrida(candidatos=None)
    informe = escala.comparar_escala(_evidencia("a", k139=_corrida()), _evidencia("b", k139=nueva))
    assert not informe.pasa
    assert "sin dato" in informe.resumen()


def test_un_tamano_sin_contraparte_falla_con_mensaje() -> None:
    base = _evidencia("a", k139=_corrida(), k463=_corrida())
    with pytest.raises(ValueError, match="463"):
        escala.comparar_escala(base, _evidencia("b", k139=_corrida()))


def test_la_tolerancia_es_configurable() -> None:
    base = _evidencia("a", k139=_corrida())
    nueva = _evidencia("b", k139=_corrida(total=400.0 * 1.15))
    assert not escala.comparar_escala(base, nueva).pasa
    assert escala.comparar_escala(base, nueva, tolerancia=0.20).pasa


# ── Tabla de tamaños y parámetros ─────────────────────────────────────────


def test_la_tabla_de_tamanos_esta_fijada_a_la_linea_base() -> None:
    """Pin documental, no conducta: fija la tabla a la línea base medida.

    139.028 y 463.473 filas (semilla 42) son las del plan y de
    ``escala_base_f0.json``. Si esta prueba falla es porque alguien cambió
    ``TAMANOS``: entonces la línea base deja de ser comparable y hay que volver
    a medirla. La verificación real (conteo del CSV contra ``filas_esperadas``)
    la cubre ``test_un_conteo_distinto_del_esperado_falla_con_mensaje_accionable``.
    """
    t139, t463 = escala.TAMANOS["139k"], escala.TAMANOS["463k"]
    assert (t139.empresas_extra, t139.importadores_extra, t139.filas_esperadas) == (
        48_000,
        9_600,
        139_028,
    )
    assert (t463.empresas_extra, t463.importadores_extra, t463.filas_esperadas) == (
        175_000,
        35_000,
        463_473,
    )
    assert t139.semilla == t463.semilla == 42


def test_el_nombre_del_archivo_lleva_los_parametros() -> None:
    nombre = escala.TAMANOS["139k"].nombre_archivo
    assert "48000" in nombre and "9600" in nombre and "42" in nombre
    assert nombre.endswith(".csv")
    assert escala.TAMANOS["139k"].nombre_archivo != escala.TAMANOS["463k"].nombre_archivo


def test_parsear_tamanos_acepta_la_lista_y_rechaza_desconocidos() -> None:
    assert escala.parsear_tamanos("139k,463k") == ("139k", "463k")
    assert escala.parsear_tamanos(" 463k ") == ("463k",)
    with pytest.raises(ValueError, match="139k"):
        escala.parsear_tamanos("1m")


# ── Generación y caché ────────────────────────────────────────────────────

#: Parámetros por defecto del generador. Con el generador actual (semilla 42)
#: producen 2.939 filas; la cifra de 12.427 del conjunto de referencia del
#: banco viene de una versión anterior del generador y NO se reproduce hoy.
_DIMINUTO = escala.ParametrosConjunto(empresas_extra=900, importadores_extra=180, semilla=42)
_FILAS_DIMINUTO = 2_939


def test_generar_crea_el_archivo_con_los_parametros_en_el_nombre(tmp_path: Path) -> None:
    ruta, generado = escala.resolver_conjunto(_DIMINUTO, tmp_path)
    assert generado is True
    assert ruta.parent == tmp_path
    assert ruta.name == _DIMINUTO.nombre_archivo
    assert escala.contar_filas_csv(ruta) == _FILAS_DIMINUTO


def test_un_conjunto_cacheado_se_reutiliza_sin_regenerar(tmp_path: Path, monkeypatch) -> None:
    ruta, _ = escala.resolver_conjunto(_DIMINUTO, tmp_path)
    marca = ruta.stat().st_mtime_ns

    def _no_debe_llamarse(*_a: Any, **_k: Any) -> None:
        raise AssertionError("el generador se invocó aunque el archivo ya existía")

    monkeypatch.setattr(subprocess, "run", _no_debe_llamarse)
    ruta2, generado = escala.resolver_conjunto(_DIMINUTO, tmp_path)
    assert generado is False
    assert ruta2 == ruta and ruta.stat().st_mtime_ns == marca


def test_un_archivo_parcial_no_se_reutiliza(tmp_path: Path) -> None:
    """La escritura es atómica: un ``.parcial`` de una corrida interrumpida no cuenta."""
    parcial = tmp_path / (_DIMINUTO.nombre_archivo + ".parcial")
    parcial.write_text("basura", encoding="utf-8")
    ruta, generado = escala.resolver_conjunto(_DIMINUTO, tmp_path)
    assert generado is True
    assert not parcial.exists()
    assert escala.contar_filas_csv(ruta) == _FILAS_DIMINUTO


def test_un_conteo_distinto_del_esperado_falla_con_mensaje_accionable(tmp_path: Path) -> None:
    params = escala.ParametrosConjunto(
        empresas_extra=900, importadores_extra=180, semilla=42, filas_esperadas=12_427
    )
    with pytest.raises(escala.ConjuntoInesperadoError, match=r"2.939.*12.427"):
        escala.resolver_conjunto(params, tmp_path)


def test_el_generador_falla_rapido_si_no_existe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(escala, "GENERADOR", tmp_path / "no_existe.py")
    with pytest.raises(FileNotFoundError, match=r"no_existe\.py"):
        escala.resolver_conjunto(_DIMINUTO, tmp_path)


# ── Corrida: qué deja y dónde ─────────────────────────────────────────────


def _corrida_banco_falsa(etiqueta: str) -> Any:
    """Una ``Corrida`` mínima pero real, con el ``entorno`` que arma el banco."""
    from record_linkage.evaluation.banco import Corrida, MetricasCalidad, MetricasRecursos

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
        ),
        recursos=MetricasRecursos(segundos_total=0.1, candidatos=1, pares_scoreados=1),
        huella="prueba",
        especificacion={},
    )


@pytest.fixture
def conjunto_mini(tmp_path: Path, monkeypatch) -> Path:
    """Un tamaño ``mini`` ya cacheado (2 filas): no se invoca el generador."""
    params = escala.ParametrosConjunto(empresas_extra=1, importadores_extra=1, filas_esperadas=2)
    dir_datos = tmp_path / "datos"
    dir_datos.mkdir()
    (dir_datos / params.nombre_archivo).write_text(
        "ID_REGISTRO,ID_GROUP,REGIMEN,NIT,RAZON_SOCIAL,FUENTE,CASO\n"
        "1,g1,CON_NIT,1,EMPRESA FICTICIA SAS,A,positivo_con_nit\n"
        "2,g1,CON_NIT,1,EMPRESA FICTICIA S.A.S.,B,positivo_con_nit\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(escala, "TAMANOS", {"mini": params})
    return dir_datos


def test_la_corrida_solo_deja_el_json_en_evidencia(
    tmp_path: Path, monkeypatch, conjunto_mini: Path
) -> None:
    """``correr_banco`` escribe ``prediccion_<etiqueta>.parquet`` en su
    ``dir_evidencia``; ese binario debe caer en el work_dir temporal (que se
    borra), no en la carpeta de evidencia del repositorio."""
    evidencia = tmp_path / "evidencia"
    trabajo = tmp_path / "trabajo"
    trabajo.mkdir()
    vistos: dict[str, Any] = {}

    def _banco_falso(espec: Any, *, silencioso: bool = True) -> Any:
        vistos["espec"] = espec
        destino = Path(espec.dir_evidencia) / f"prediccion_{espec.etiqueta}.parquet"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"parquet falso")
        return _corrida_banco_falsa(espec.etiqueta)

    monkeypatch.setattr(escala, "correr_banco", _banco_falso)
    opciones = escala.OpcionesCorrida(
        etiqueta="x", dir_datos=conjunto_mini, dir_evidencia=evidencia, dir_trabajo=trabajo
    )
    destino = escala.correr_escala(("mini",), opciones)

    assert destino == evidencia / "escala_x.json"
    assert sorted(p.name for p in evidencia.iterdir()) == ["escala_x.json"]
    espec = vistos["espec"]
    assert espec.etiqueta == "x_mini"
    assert Path(espec.dir_evidencia).is_relative_to(trabajo)
    assert Path(espec.dir_evidencia) != evidencia
    # El work_dir temporal (y el parquet con él) se borró al terminar.
    assert list(trabajo.iterdir()) == []


def test_el_entorno_por_tamano_es_el_del_banco_mas_la_maquina(
    tmp_path: Path, monkeypatch, conjunto_mini: Path
) -> None:
    """No se recalcula lo que ``Corrida.entorno`` ya trae; solo se añade lo que falta."""
    monkeypatch.setattr(
        escala, "correr_banco", lambda espec, **_k: _corrida_banco_falsa(espec.etiqueta)
    )
    opciones = escala.OpcionesCorrida(
        etiqueta="x", dir_datos=conjunto_mini, dir_evidencia=tmp_path / "evidencia"
    )
    escala.correr_escala(("mini",), opciones)
    documento = escala.cargar_evidencia(tmp_path / "evidencia", "x")
    entorno = documento["tamanos"]["mini"]["entorno"]
    assert entorno["plataforma"] == "prueba" and entorno["python"] == "3.x"
    assert entorno["version"] == "0.0.0-prueba"
    assert entorno["vcpu"] >= 1 and entorno["memoria_total_mib"] > 0
    assert set(documento["entorno"]) == {"vcpu", "memoria_total_mib"}
    assert documento["version"] == escala.rl.__version__


def test_dir_trabajo_inexistente_falla_rapido_con_mensaje_accionable(tmp_path: Path) -> None:
    """Antes se generaba el conjunto y recién después moría ``tempfile.mkdtemp``."""
    with pytest.raises(FileNotFoundError, match=r"--dir-trabajo .*no_existe.*no existe"):
        escala.OpcionesCorrida(etiqueta="x", dir_trabajo=tmp_path / "no_existe")


# ── JSON de evidencia ─────────────────────────────────────────────────────


def test_el_json_no_lleva_nan(tmp_path: Path) -> None:
    """``macro_f1`` es NaN en los conjuntos sin ESTRATO; el JSON debe ser estricto."""
    destino = escala.guardar_evidencia(
        {"etiqueta": "x", "tamanos": {"139k": {"calidad": {"macro_f1": float("nan")}}}},
        tmp_path,
        "x",
    )
    assert destino == tmp_path / "escala_x.json"
    texto = destino.read_text(encoding="utf-8")
    assert "NaN" not in texto
    assert escala.cargar_evidencia(tmp_path, "x")["tamanos"]["139k"]["calidad"]["macro_f1"] is None


def test_cargar_evidencia_inexistente_nombra_las_disponibles(tmp_path: Path) -> None:
    escala.guardar_evidencia({"etiqueta": "hay", "tamanos": {}}, tmp_path, "hay")
    with pytest.raises(FileNotFoundError, match="hay"):
        escala.cargar_evidencia(tmp_path, "no_esta")


def test_comparar_sin_tamanos_no_pasa_en_vacio() -> None:
    """Un JSON sin tamaños medidos no puede dar PASA: la compuerta exige medir."""
    vacia = {"etiqueta": "x", "tamanos": {}}
    with pytest.raises(ValueError, match="No hay tamaños que comparar"):
        escala.comparar_escala(vacia, vacia)
