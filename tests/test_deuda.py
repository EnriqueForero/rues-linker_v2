"""Pruebas del trinquete de deuda técnica (F0.8, ``scripts/deuda.py``).

Rápidas: la comparación trabaja con diccionarios sintéticos y la medición
corre ruff (sin mypy) sobre un paquete diminuto creado en ``tmp_path``. La
única prueba que lee el repositorio mide ``src/record_linkage`` contra la
referencia versionada; no se incluye a sí misma (vive en ``tests/``).
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from cargar_script import cargar_script

RAIZ = Path(__file__).resolve().parents[1]

deuda = cargar_script("deuda")


def _medicion(conteos: dict[str, int], ubicaciones: dict[str, list[str]]) -> dict:
    return {"conteos": conteos, "ubicaciones": ubicaciones}


# ---------------------------------------------------------------------------
# Comparación con diccionarios sintéticos
# ---------------------------------------------------------------------------


def test_comparar_sube_devuelve_1_y_nombra_la_ubicacion_nueva() -> None:
    referencia = _medicion(
        {"print": 2, "os_path": 1}, {"print": ["a.py:1", "a.py:2"], "os_path": ["b.py:9"]}
    )
    actual = _medicion(
        {"print": 3, "os_path": 1},
        {"print": ["a.py:1", "a.py:2", "c.py:7"], "os_path": ["b.py:9"]},
    )
    veredicto = deuda.comparar(actual, referencia, "ref.json")
    assert veredicto.codigo == 1
    assert "print" in veredicto.texto and "2 → 3" in veredicto.texto and "+1" in veredicto.texto
    assert "c.py:7" in veredicto.texto, "debe listar la ubicación nueva (diferencia de conjuntos)"
    assert "a.py:1" not in veredicto.texto, "las ubicaciones ya conocidas no son nuevas"
    assert "FALLA" in veredicto.texto


def test_comparar_igual_devuelve_0() -> None:
    ref = _medicion({"print": 2, "mypy": 5}, {"print": ["a.py:1", "a.py:2"], "mypy": ["z.py:3"]})
    veredicto = deuda.comparar(ref, ref)
    assert veredicto.codigo == 0
    assert "PASA" in veredicto.texto


def test_comparar_baja_devuelve_0_y_sugiere_actualizar_la_referencia() -> None:
    referencia = _medicion({"print": 2}, {"print": ["a.py:1", "a.py:2"]})
    actual = _medicion({"print": 1}, {"print": ["a.py:1"]})
    veredicto = deuda.comparar(actual, referencia, "docs/evidencia/deuda_f0.json")
    assert veredicto.codigo == 0
    assert "BAJA" in veredicto.texto and "2 → 1" in veredicto.texto
    assert "--escribir docs/evidencia/deuda_f0.json" in veredicto.texto


def test_comparar_mismo_total_con_ubicaciones_movidas_pasa() -> None:
    referencia = _medicion({"print": 1}, {"print": ["a.py:1"]})
    actual = _medicion({"print": 1}, {"print": ["a.py:40"]})
    veredicto = deuda.comparar(actual, referencia)
    assert veredicto.codigo == 0
    assert "movidas" in veredicto.texto


def test_comparar_basta_con_que_una_metrica_suba() -> None:
    referencia = _medicion({"print": 5, "os_path": 5}, {"print": [], "os_path": []})
    actual = _medicion({"print": 0, "os_path": 6}, {"print": [], "os_path": ["x.py:1"]})
    veredicto = deuda.comparar(actual, referencia)
    assert veredicto.codigo == 1
    assert "BAJA  print" in veredicto.texto and "SUBE  os_path" in veredicto.texto


def test_comparar_metrica_ausente_en_la_referencia_falla() -> None:
    """Una referencia incompleta no es un techo: hay que regenerarla."""
    referencia = _medicion({"print": 0}, {"print": []})
    actual = _medicion({"print": 0, "os_path": 0}, {"print": [], "os_path": []})
    veredicto = deuda.comparar(actual, referencia)
    assert veredicto.codigo == 1
    assert "FALTA os_path" in veredicto.texto


def test_comparar_metrica_no_medida_se_omite_sin_fallar() -> None:
    """``--sin-mypy`` compara lo que midió y avisa de lo que no."""
    referencia = _medicion({"print": 0, "mypy": 108}, {"print": [], "mypy": []})
    actual = _medicion({"print": 0}, {"print": []})
    veredicto = deuda.comparar(actual, referencia)
    assert veredicto.codigo == 0
    assert "OMITE mypy" in veredicto.texto


# ---------------------------------------------------------------------------
# Medición sobre un paquete diminuto (sin mypy)
# ---------------------------------------------------------------------------

MODULO_DIMINUTO = '''
"""Módulo de prueba: un except desnudo, un print y un os.path.join."""

import os


def saludar(nombre: str) -> str:
    try:
        ruta = os.path.join("carpeta", nombre)
    except:
        ruta = nombre
    print(ruta)
    return ruta
'''


def _crear_paquete(raiz: Path, codigo: str = MODULO_DIMINUTO) -> Path:
    paquete = raiz / "src" / "paquetito"
    paquete.mkdir(parents=True)
    (paquete / "__init__.py").write_text('"""Paquete diminuto."""\n', encoding="utf-8")
    (paquete / "modulo.py").write_text(textwrap.dedent(codigo).lstrip(), encoding="utf-8")
    return Path("src") / "paquetito"


def test_medir_paquete_diminuto_cuenta_1_1_1(tmp_path: Path) -> None:
    objetivo = _crear_paquete(tmp_path)
    medicion = deuda.medir(tmp_path, objetivo, sin_mypy=True)
    assert medicion.conteos == {
        "cc_ge_20": 0,
        "except_sin_relanzar": 1,
        "print": 1,
        "os_path": 1,
    }
    assert "mypy" not in medicion.conteos and "mypy" not in medicion.ubicaciones
    assert medicion.ubicaciones["except_sin_relanzar"] == ["src/paquetito/modulo.py:9"]
    assert medicion.ubicaciones["print"] == ["src/paquetito/modulo.py:11"]
    assert medicion.ubicaciones["os_path"] == ["src/paquetito/modulo.py:8"]


def test_medir_cuenta_print_aliasado_y_es_superconjunto_de_t201(tmp_path: Path) -> None:
    """La razón de medir ``print`` con ``ast``: ``safe_print as print`` esconde el 94 %.

    Reproduce el patrón real de la librería (``from ..utils.output import
    safe_print as print``). Con ruff 0.15.13, T201 resuelve el nombre al alias
    y calla ante ``print("hola")``; esa ceguera es una limitación de la
    herramienta, no una propiedad del script, así que no se asegura. Lo que sí
    se asegura es la propiedad que importa: la medición por ``ast`` es un
    superconjunto de lo que T201 reporta, hoy y aunque ruff cambie.
    """
    codigo = """
from .salida import safe_print as print


def f() -> None:
    print("hola")
"""
    objetivo = _crear_paquete(tmp_path, codigo)
    (tmp_path / objetivo / "salida.py").write_text(
        "import builtins\n\n\ndef safe_print(*args, **kwargs):\n    builtins.print(*args, **kwargs)\n",
        encoding="utf-8",
    )
    medicion = deuda.medir(tmp_path, objetivo, sin_mypy=True)
    # Cuenta la llamada aliasada y también la `builtins.print` de la implementación.
    assert medicion.ubicaciones["print"] == [
        "src/paquetito/modulo.py:5",
        "src/paquetito/salida.py:5",
    ]
    # Superconjunto de T201: nada que ruff vea se le escapa a la medición por ast.
    t201 = deuda.ubicaciones_ruff(tmp_path, objetivo, "T201")
    assert set(t201) <= set(medicion.ubicaciones["print"]), (t201, medicion.ubicaciones["print"])


def test_medir_un_noqa_no_esconde_deuda_al_trinquete(tmp_path: Path) -> None:
    """El trinquete mide la deuda real: ``# noqa`` silencia a ruff, no al techo.

    Un ``noqa`` deliberado es legítimo para la compuerta por archivo del CI
    (reglas estrictas en código tocado), pero si además sacara la ubicación de
    la referencia, bastaría anotar el código nuevo para que la deuda subiera
    sin que nadie lo viera.
    """
    codigo = (
        """
import os


def f(nombre: str) -> str:
    try:
        ruta = os.path.join("carpeta", nombre)  # noqa: PTH118
    except:  # noqa: E722
        ruta = nombre
    return ruta


def g() -> int:  # noqa: C901
"""
        + "\n".join(f"    if g == {i}:\n        return {i}" for i in range(21))
        + "\n    return -1\n"
    )
    objetivo = _crear_paquete(tmp_path, codigo)
    medicion = deuda.medir(tmp_path, objetivo, sin_mypy=True)
    assert medicion.conteos == {
        "cc_ge_20": 1,
        "except_sin_relanzar": 1,
        "print": 0,
        "os_path": 1,
    }


def test_medir_cc_ge_20_detecta_una_funcion_compleja(tmp_path: Path) -> None:
    ramas = "\n".join(f"    if x == {i}:\n        return {i}" for i in range(21))
    codigo = f"def compleja(x: int) -> int:\n{ramas}\n    return -1\n"
    objetivo = _crear_paquete(tmp_path, codigo)
    medicion = deuda.medir(tmp_path, objetivo, sin_mypy=True)
    assert medicion.conteos["cc_ge_20"] == 1
    assert medicion.ubicaciones["cc_ge_20"] == ["src/paquetito/modulo.py:1"]


def test_version_instalada_de_un_paquete_ausente_no_rompe() -> None:
    assert deuda.version_instalada("paquete-que-no-existe-xyz") == "no instalada"
    assert deuda.version_instalada("pytest") == pytest.__version__


def test_comparar_avisa_si_las_herramientas_cambiaron_de_version() -> None:
    """Un conteo de mypy que cambia por otra versión de stubs debe poder diagnosticarse."""
    referencia = {
        "conteos": {"mypy": 108},
        "ubicaciones": {"mypy": []},
        "herramientas": {"pandas-stubs": "3.0.5.260914"},
    }
    actual = {
        "conteos": {"mypy": 108},
        "ubicaciones": {"mypy": []},
        "herramientas": {"pandas-stubs": "3.1.0.000000"},
    }
    veredicto = deuda.comparar(actual, referencia)
    assert veredicto.codigo == 0
    assert "AVISO" in veredicto.texto and "pandas-stubs" in veredicto.texto
    assert "3.0.5.260914" in veredicto.texto and "3.1.0.000000" in veredicto.texto
    # Sin diferencia no hay aviso.
    assert "AVISO" not in deuda.comparar(referencia, referencia).texto
    # Una referencia que no registraba la herramienta no «cambió»: se muestra, sin aviso.
    sin_registro = {**referencia, "herramientas": {}}
    texto = deuda.comparar(actual, sin_registro).texto
    assert "sin registrar → 3.1.0.000000" in texto and "AVISO" not in texto


def test_medir_objetivo_inexistente_es_error_accionable(tmp_path: Path) -> None:
    with pytest.raises(deuda.ErrorDeMedicion, match="No existe el directorio"):
        deuda.medir(tmp_path, Path("src") / "nada", sin_mypy=True)


# ---------------------------------------------------------------------------
# CLI: --escribir y --referencia de punta a punta
# ---------------------------------------------------------------------------


def test_cli_escribir_y_referencia(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    objetivo = _crear_paquete(tmp_path)
    base = ["--raiz", str(tmp_path), "--objetivo", str(objetivo), "--sin-mypy"]
    assert deuda.main([*base, "--escribir", "ref.json"]) == 0
    referencia = json.loads((tmp_path / "ref.json").read_text(encoding="utf-8"))
    assert referencia["conteos"] == {
        "cc_ge_20": 0,
        "except_sin_relanzar": 1,
        "print": 1,
        "os_path": 1,
    }
    assert referencia["ubicaciones"]["print"] == ["src/paquetito/modulo.py:11"]
    assert set(referencia) >= {"version", "commit", "fecha", "objetivo", "herramientas"}
    assert referencia["herramientas"]["mypy"] == "no medido"
    # Las versiones que de verdad mueven el conteo de mypy quedan registradas.
    assert set(referencia["herramientas"]) >= {"ruff", "mypy", "pandas", "pandas-stubs"}
    assert all(v != "" for v in referencia["herramientas"].values())

    # Igual → 0, y el encabezado dice con qué versiones se midió cada lado.
    assert deuda.main([*base, "--referencia", "ref.json"]) == 0
    salida = capsys.readouterr().out
    assert "PASA" in salida
    assert "pandas-stubs" in salida and "Herramientas" in salida

    # Un print más → 1, con la ubicación nueva en el mensaje.
    modulo = tmp_path / objetivo / "modulo.py"
    modulo.write_text(
        modulo.read_text(encoding="utf-8") + '\n\ndef otro() -> None:\n    print("más")\n',
        encoding="utf-8",
    )
    assert deuda.main([*base, "--referencia", "ref.json"]) == 1
    salida = capsys.readouterr().out
    assert "SUBE  print: 1 → 2 (+1)" in salida
    assert "src/paquetito/modulo.py:16" in salida


def test_cli_no_escribe_la_referencia_si_la_comparacion_fallo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--referencia X --escribir X` con la deuda arriba no debe consolidarla."""
    objetivo = _crear_paquete(tmp_path)
    base = ["--raiz", str(tmp_path), "--objetivo", str(objetivo), "--sin-mypy"]
    assert deuda.main([*base, "--escribir", "ref.json"]) == 0
    antes = (tmp_path / "ref.json").read_bytes()
    modulo = tmp_path / objetivo / "modulo.py"
    modulo.write_text(
        modulo.read_text(encoding="utf-8") + '\n\ndef otro() -> None:\n    print("más")\n',
        encoding="utf-8",
    )
    assert deuda.main([*base, "--referencia", "ref.json", "--escribir", "ref.json"]) == 1
    assert (tmp_path / "ref.json").read_bytes() == antes
    assert "No se escribe ref.json" in capsys.readouterr().err


def test_cli_referencia_inexistente_es_error_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    objetivo = _crear_paquete(tmp_path)
    codigo = deuda.main(
        [
            "--raiz",
            str(tmp_path),
            "--objetivo",
            str(objetivo),
            "--sin-mypy",
            "--referencia",
            "no.json",
        ]
    )
    assert codigo == 2
    assert "--escribir" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# La referencia versionada es un techo que el árbol actual respeta
# ---------------------------------------------------------------------------


def test_referencia_versionada_cubre_el_arbol_actual_sin_mypy() -> None:
    """Las cuatro métricas rápidas del árbol no superan ``deuda_f0.json``.

    Es la misma comparación que hace el job ``deuda`` del CI, sin mypy (que
    tarda minutos). Si falla, o subió la deuda o alguien no regeneró la
    referencia al bajarla: el mensaje dice cuál.
    """
    referencia = deuda.leer_referencia(RAIZ / deuda.REFERENCIA_POR_DEFECTO)
    assert set(referencia["conteos"]) == set(deuda.METRICAS)
    medicion = deuda.medir(RAIZ, deuda.OBJETIVO_POR_DEFECTO, sin_mypy=True)
    veredicto = deuda.comparar(medicion.a_dict(), referencia)
    assert veredicto.codigo == 0, veredicto.texto
