"""Compuerta de escala (scripts/escala.py) — pruebas rápidas (F0.5).

La compuerta decide si un cambio puede entrar según el tiempo, la memoria y
los candidatos a 139k y 463k registros. Un instrumento sin calibrar no sirve:
aquí se verifica (1) que la comparación pura falla exactamente cuando debe y
nombra la fase culpable, (2) que la tabla de tamaños reproduce las cifras
medidas, y (3) que la generación cachea por parámetros y reutiliza el archivo.

Ninguna prueba corre ``linkage()``: el conjunto diminuto (900/180) se genera en
``tmp_path`` con el generador real, en menos de dos segundos.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# escala.py es un script (no parte del paquete): se carga por ruta.
_RUTA = Path(__file__).resolve().parent.parent / "scripts" / "escala.py"
_spec = importlib.util.spec_from_file_location("escala", _RUTA)
assert _spec is not None and _spec.loader is not None
escala = importlib.util.module_from_spec(_spec)
# Registrarlo en sys.modules: los @dataclass con `from __future__ import
# annotations` resuelven anotaciones buscando el módulo por nombre.
sys.modules["escala"] = escala
_spec.loader.exec_module(escala)


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


def test_corridas_identicas_pasan() -> None:
    informe = escala.comparar_escala(
        _evidencia("a", k139=_corrida()), _evidencia("b", k139=_corrida())
    )
    assert informe.pasa
    assert "VEREDICTO: PASA" in informe.resumen()


def test_una_fase_que_sube_once_por_ciento_falla_y_la_nombra() -> None:
    nueva = _corrida(fases={"L2_lsh_candidates": 250.0 * 1.11, "L3_scoring": 150.0})
    informe = escala.comparar_escala(_evidencia("a", k139=_corrida()), _evidencia("b", k139=nueva))
    assert not informe.pasa
    culpables = [v.metrica for c in informe.comparaciones for v in c.veredictos if not v.pasa]
    assert culpables == ["L2_lsh_candidates"]
    assert "L2_lsh_candidates" in informe.resumen()
    assert "VEREDICTO: FALLA" in informe.resumen()


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


def test_los_tamanos_reproducen_las_cifras_medidas() -> None:
    """139.028 y 463.473 filas son las de la línea base del plan (semilla 42)."""
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
