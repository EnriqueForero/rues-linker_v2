"""Pruebas del trinquete por archivo y regla (``scripts/reglas_estrictas.py``).

Cada prueba construye un repositorio git inventado en ``tmp_path`` con un
archivo legado que ya trae un ``except Exception`` (BLE001) y un
``os.path.join`` (PTH118) en la base, y hace un commit en una rama con el
cambio que se quiere juzgar. Ninguna lee el repositorio real: el veredicto
sobre el PR de F1 se verifica a mano (``--base claude/f0-fundaciones``).

La configuración global de git se anula (``GIT_CONFIG_GLOBAL``) para que ni
la firma de commits ni los hooks de la máquina influyan.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest
from cargar_script import cargar_script

reglas_estrictas = cargar_script("reglas_estrictas")

LEGADO_BASE = """
import os


def ruta(nombre: str) -> str:
    try:
        return os.path.join("carpeta", nombre)
    except Exception:
        return nombre
"""

LEGADO = "src/paquetito/legado.py"


def _git(repo: Path, *args: str) -> str:
    salida = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Prueba",
            "-c",
            "user.email=prueba@ejemplo.test",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return salida.stdout.strip()


def _escribir(repo: Path, ruta: str, codigo: str) -> None:
    destino = repo / ruta
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(textwrap.dedent(codigo).lstrip(), encoding="utf-8")


def _commit(repo: Path, mensaje: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", mensaje)


@pytest.fixture
def repositorio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Rama ``base`` con el legado; HEAD en la rama ``cambio``, aún sin commits."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "base")
    _escribir(repo, LEGADO, LEGADO_BASE)
    _escribir(repo, "src/paquetito/__init__.py", "")
    _commit(repo, "base: legado con deuda")
    _git(repo, "checkout", "-q", "-b", "cambio")
    return repo


def _juzgar(repo: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, str]:
    codigo = reglas_estrictas.main(["--raiz", str(repo), "--base", "base", *extra])
    return codigo, capsys.readouterr().out


# ---------------------------------------------------------------------------
# Los cinco casos del diseño
# ---------------------------------------------------------------------------


def test_a_linea_anadida_sin_violaciones_nuevas_pasa(
    repositorio: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _escribir(repositorio, LEGADO, LEGADO_BASE + "\n\ndef limpia() -> int:\n    return 1\n")
    _commit(repositorio, "añade una función limpia")
    codigo, salida = _juzgar(repositorio, capsys)
    assert codigo == 0, salida
    assert "PASA" in salida
    # La deuda heredada se muestra tal cual, por regla, sin subir.
    assert "BLE001 1→1" in salida and "PTH118 1→1" in salida
    assert LEGADO in salida


def test_b_violacion_nueva_falla_nombrando_archivo_linea_y_regla(
    repositorio: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _escribir(
        repositorio,
        LEGADO,
        LEGADO_BASE
        + "\n\ndef otra(nombre: str) -> str:\n"
        + "    try:\n"
        + "        return nombre.upper()\n"
        + "    except Exception:\n"
        + "        return nombre\n",
    )
    _commit(repositorio, "añade un except Exception")
    codigo, salida = _juzgar(repositorio, capsys)
    assert codigo == 1, salida
    assert "FALLA" in salida
    assert "BLE001 1→2 (+1)" in salida
    # La nueva (línea 14) se nombra; la heredada (línea 7) no es nueva.
    assert f"{LEGADO}:14:" in salida and "BLE001" in salida
    assert f"{LEGADO}:7:" not in salida
    # La otra regla no sube y no se marca.
    assert "PTH118 1→1" in salida
    # Qué hacer, no solo qué pasó.
    assert "Qué hacer" in salida


def test_c_archivo_nuevo_con_print_falla_en_src_y_pasa_en_scripts(
    repositorio: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _escribir(repositorio, "src/paquetito/nuevo.py", 'def f() -> None:\n    print("x")\n')
    _commit(repositorio, "módulo nuevo con print")
    codigo, salida = _juzgar(repositorio, capsys)
    assert codigo == 1, salida
    assert "src/paquetito/nuevo.py" in salida and "T201 0→1 (+1)" in salida
    assert "src/paquetito/nuevo.py:2:" in salida

    # El mismo contenido bajo scripts/ es legítimo: T201 no aplica ahí.
    _git(repositorio, "checkout", "-q", "base")
    _git(repositorio, "checkout", "-q", "-b", "cambio_scripts")
    _escribir(repositorio, "scripts/nuevo.py", 'def f() -> None:\n    print("x")\n')
    _commit(repositorio, "script nuevo con print")
    codigo, salida = _juzgar(repositorio, capsys)
    assert codigo == 0, salida
    assert "scripts/nuevo.py" in salida and "limpio (nuevo)" in salida
    assert "T201" not in salida


def test_d_violacion_que_solo_cambia_de_linea_pasa(
    repositorio: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _escribir(
        repositorio, LEGADO, '"""Docstring nuevo que desplaza todo dos líneas."""\n\n' + LEGADO_BASE
    )
    _commit(repositorio, "docstring")
    codigo, salida = _juzgar(repositorio, capsys)
    assert codigo == 0, salida
    assert "BLE001 1→1" in salida and "PTH118 1→1" in salida
    assert "igual" in salida


def test_e_archivo_que_reduce_violaciones_pasa_y_lo_dice(
    repositorio: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _escribir(repositorio, LEGADO, LEGADO_BASE.replace("except Exception:", "except OSError:"))
    _commit(repositorio, "excepción específica")
    codigo, salida = _juzgar(repositorio, capsys)
    assert codigo == 0, salida
    assert "BLE001 1→0 (-1)" in salida
    assert "baja" in salida
    assert "PASA" in salida and "reduce" in salida


# ---------------------------------------------------------------------------
# Propiedades del trinquete que los cinco casos no cubren
# ---------------------------------------------------------------------------


def test_cuenta_por_regla_no_por_total(
    repositorio: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Quitar un BLE001 no compra un PTH110: cada (archivo, regla) es su propio techo."""
    codigo_head = LEGADO_BASE.replace("except Exception:", "except OSError:").replace(
        '        return os.path.join("carpeta", nombre)\n',
        '        os.path.exists(nombre)\n        return os.path.join("carpeta", nombre)\n',
    )
    _escribir(repositorio, LEGADO, codigo_head)
    _commit(repositorio, "cambia una deuda por otra")
    codigo, salida = _juzgar(repositorio, capsys)
    assert codigo == 1, salida
    assert "BLE001 1→0 (-1)" in salida
    assert "PTH110 0→1 (+1)" in salida and f"{LEGADO}:6:" in salida
    assert "PTH118 1→1" in salida


def test_funcion_heredada_que_solo_cambia_de_complejidad_no_es_nueva(
    repositorio: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """C901 identifica a la función, no el número «(N > 15)» del mensaje.

    Si la compleja heredada pasa de 17 a 18 y además aparece una compleja
    nueva, la ubicación nueva es solo la segunda.
    """

    def compleja(nombre: str, ramas: int) -> str:
        cuerpo = "\n".join(f"    if x == {i}:\n        return {i}" for i in range(ramas))
        return f"def {nombre}(x: int) -> int:\n{cuerpo}\n    return -1\n"

    _git(repositorio, "checkout", "-q", "base")
    _escribir(repositorio, "src/paquetito/complejo.py", compleja("vieja", 17))
    _commit(repositorio, "base con una función compleja")
    _git(repositorio, "checkout", "-q", "-b", "mas_compleja")
    _escribir(
        repositorio,
        "src/paquetito/complejo.py",
        compleja("vieja", 18) + "\n\n" + compleja("nueva", 17),
    )
    _commit(repositorio, "la vieja crece y llega otra")
    codigo, salida = _juzgar(repositorio, capsys)
    assert codigo == 1, salida
    assert "C901 1→2 (+1)" in salida
    assert "`nueva` is too complex" in salida
    assert "`vieja` is too complex" not in salida


def test_archivo_renombrado_se_compara_con_su_ruta_de_origen(
    repositorio: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Traducir el nombre de un módulo legado no lo convierte en «nuevo»."""
    _git(repositorio, "mv", LEGADO, "src/paquetito/heredado.py")
    _commit(repositorio, "renombra sin tocar el contenido")
    codigo, salida = _juzgar(repositorio, capsys)
    assert codigo == 0, salida
    assert "src/paquetito/heredado.py" in salida and "BLE001 1→1" in salida


def test_head_puede_ser_otra_referencia(
    repositorio: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _escribir(repositorio, "src/paquetito/nuevo.py", 'def f() -> None:\n    print("x")\n')
    _commit(repositorio, "módulo nuevo con print")
    _git(repositorio, "checkout", "-q", "base")
    # Desde la base, HEAD no toca nada; la rama sí.
    assert _juzgar(repositorio, capsys)[0] == 0
    codigo, salida = _juzgar(repositorio, capsys, "--head", "cambio")
    assert codigo == 1, salida
    assert "T201 0→1 (+1)" in salida


def test_sin_archivos_tocados_pasa_y_lo_dice(
    repositorio: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _escribir(repositorio, "docs/nota.md", "nada de python\n")
    _commit(repositorio, "solo docs")
    codigo, salida = _juzgar(repositorio, capsys)
    assert codigo == 0
    assert "Sin archivos" in salida and "PASA" in salida


def test_base_inexistente_es_error_2_y_dice_que_hacer(
    repositorio: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    codigo = reglas_estrictas.main(["--raiz", str(repositorio), "--base", "origin/no_existe"])
    assert codigo == 2
    err = capsys.readouterr().err
    assert "origin/no_existe" in err and "git fetch" in err


def test_archivo_que_no_se_puede_analizar_es_error_2(
    repositorio: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un archivo con error de sintaxis no se puede medir: no es «cero violaciones»."""
    _escribir(repositorio, "src/paquetito/roto.py", "def f(:\n    pass\n")
    _commit(repositorio, "archivo roto")
    codigo = reglas_estrictas.main(["--raiz", str(repositorio), "--base", "base"])
    assert codigo == 2
    assert "src/paquetito/roto.py" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# La configuración es un solo sitio y dice lo mismo que el diseño
# ---------------------------------------------------------------------------


def test_configuracion_por_defecto_es_la_del_diseno() -> None:
    reglas = reglas_estrictas.ReglasEstrictas()
    assert reglas.select == ("PTH", "BLE001", "E722", "T201", "C901")
    assert reglas.complejidad_maxima == 15
    assert reglas.directorios == ("src", "scripts")
    assert reglas.excluidas("scripts/x.py") == frozenset({"T201"})
    assert reglas.excluidas("src/record_linkage/x.py") == frozenset()
    assert reglas.pathspecs == [":(glob)src/**/*.py", ":(glob)scripts/**/*.py"]
