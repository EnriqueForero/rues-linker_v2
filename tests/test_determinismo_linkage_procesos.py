"""`linkage()` no puede depender de la semilla de hash del proceso (F0.6).

`test_determinismo_contrato.py` prueba que dos corridas en el MISMO proceso
coinciden. Eso no ve lo que pasó en la 0.22.3: la semilla de hash de Python es
fija dentro de un proceso y distinta entre procesos, así que cualquier orden
que salga de un `set` de cadenas (sufijos apilados, tokens genéricos, llaves de
bloqueo) cambia de sesión a sesión y la misma base da particiones distintas
(CLAUDE.md §5). Aquí la fachada completa (L1…L5) corre en SUBPROCESOS con
`PYTHONHASHSEED` distinto y se exige la misma huella de la partición.

El conjunto es sintético, inline e inventado (ninguna empresa real), y está
diseñado para delatar órdenes derivados de un `set`: sufijos multi-token
apilados, paréntesis, tildes y mojibake, nombres que comparten tokens
genéricos y grupos sin NIT con 4-6 variantes. Patrón tomado de
`test_normalizador_determinista_v0223.py` (subprocesos con `sys.executable` y
el `PYTHONPATH` del entorno actual, para que funcione en worktrees y en CI).
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import textwrap
from collections.abc import Sequence
from pathlib import Path

import pytest

from record_linkage.matching.identificadores import digito_verificacion_dian

# ---------------------------------------------------------------------------
# Conjunto sintético (todo inventado; el orden de construcción es determinista
# y NO pasa por ningún `set`, para que el único no-determinismo posible sea el
# del motor).
# ---------------------------------------------------------------------------

#: Raíces inventadas de empresas CON NIT. Varias llevan tilde a propósito.
RAICES_CON_NIT: list[str] = [
    "ZARAGUAY",
    "TELMIRA ANDINA",
    "QUIBORÁN",
    "MOLTEX DEL CARIBE",
    "CORUMBÁ INDUSTRIAL",
    "ANDARIEGA",
    "VELTRÓN",
    "SIRAMBÚ",
    "OKANDO TEXTIL",
    "PIRAMBA",
    "TUNDAMA ENERGÍA",
    "LUMBRERAS DEL VALLE",
    "DISTRIBUIDORA NARQUÉ",
    "IMPORTADORA FOLVIA",
    "DISTRIBUIDORA TREMOLÍN",
    "IMPORTADORA SACARÍ",
    "COMERCIALIZADORA ULMEZ",
    "AGROPECUARIA BRISOTE",
    "LABORATORIOS QUINZÁN",
    "CONSTRUCTORA ARPEGIO DEL SUR",
    "TRANSPORTES MALVARÍN",
    "INVERSIONES COTOPÉ",
    "ALIMENTOS ZURBANO",
    "METALÚRGICA ORDIVEL",
]

#: Familias de sufijos multi-token apilados: cada grupo CON NIT usa una familia
#: y cada variante del grupo toma un sufijo distinto de ella.
FAMILIAS_SUFIJOS: list[list[str]] = [
    ["S.A.S. E.S.P.", "SAS ESP", "S A S E S P", "S.A.S E.S.P"],
    ["LTDA Y CIA S EN C", "LIMITADA Y CIA S. EN C.", "LTDA & CIA S EN C", "LTDA"],
    ["S.A. SUCURSAL COLOMBIA", "SOCIEDAD ANONIMA SUCURSAL COLOMBIA", "S.A.", "SA"],
    ["S EN C S", "S. EN C.S.", "SOCIEDAD EN COMANDITA SIMPLE", "S EN C"],
    ["S.A.S. (EN REORGANIZACIÓN)", "SAS EN REORGANIZACION", "S.A.S.", "SAS"],
    ["E.U.", "EMPRESA UNIPERSONAL", "E U", "EU"],
]

#: Grupos SIN NIT con 4-6 variantes (la especificación pide al menos 3).
GRUPOS_SIN_NIT_GRANDES: list[list[str]] = [
    [
        "DISTRIBUIDORA ZARCOTÁN S.A.S. E.S.P.",
        "DISTRIBUIDORA ZARCOTAN SAS ESP",
        "DISTRIBUIDORA ZARCOTÃ\x81N S.A.S.",
        "DISTRIBUIDORA ZARCOTÁN (ANTES ZARCOTÁN LTDA)",
        "DISTRIBUIDORA ZARCOTÁN S A S",
        "DISTRIBUIDORA ZARCOTÁN",
    ],
    [
        "IMPORTADORA BELQUIRÁN LTDA Y CIA S EN C",
        "IMPORTADORA BELQUIRAN LIMITADA Y CIA S. EN C.",
        "IMPORTADORA BELQUIRÁN LTDA",
        "IMPORTADORA BELQUIRÃ\x81N & CIA",
        "IMPORTADORA BELQUIRAN",
    ],
    [
        "COMERCIALIZADORA TUNDAVIA S.A. SUCURSAL COLOMBIA",
        "COMERCIALIZADORA TUNDAVIA SOCIEDAD ANONIMA SUCURSAL COLOMBIA",
        "COMERCIALIZADORA TUNDAVIA S.A.",
        "COMERCIALIZADORA TUNDAVIA SA",
        "COMERCIALIZADORA TUNDAVIA (SUCURSAL)",
    ],
    [
        "INVERSIONES MOLGARÉ S EN C S",
        "INVERSIONES MOLGARE S. EN C.S.",
        "INVERSIONES MOLGARÃ\x89 SOCIEDAD EN COMANDITA SIMPLE",
        "INVERSIONES MOLGARÉ",
    ],
    [
        "TRANSPORTES QUIRAMBO Y CIA LTDA",
        "TRANSPORTES QUIRAMBO & CIA. LTDA.",
        "TRANSPORTES QUIRAMBO Y COMPAÑÍA LIMITADA",
        "TRANSPORTES QUIRAMBO Y COMPAÃ\x91IA LIMITADA",
        "TRANSPORTES QUIRAMBO",
    ],
]

#: Raíces SIN NIT con 2-3 variantes cada una.
RAICES_SIN_NIT: list[str] = [
    "DISTRIBUIDORA ORIMBELA",
    "DISTRIBUIDORA CASTAÑUELA",
    "IMPORTADORA VELORÍN",
    "IMPORTADORA BRUMANTE",
    "COMERCIALIZADORA PALTÓN",
    "DROGUERÍA SALMIRA",
    "FERRETERÍA TORNAVIENTO",
    "PANIFICADORA GUALMÉS",
    "CALZADO ARRIQUÍN",
    "PLÁSTICOS DURMANTE",
    "TEXTILES ZOCARÁ",
    "LOGÍSTICA BERMÚN",
    "CONFECCIONES TALAVÍN",
    "MADERAS PIRQUELO",
    "AGROINDUSTRIAS CORVANTE",
    "EDITORIAL LUMBARDO",
    "SERVICIOS OLTEMAR",
    "MINERALES SARAPUNGO",
]

#: Nombres SIN NIT que comparten tokens genéricos y se distinguen por UNO solo:
#: empates de scoring que un orden inestable resolvería distinto en cada sesión.
TOPONIMOS_INVENTADOS: list[str] = [
    "NORTE",
    "SUR",
    "ORIENTE",
    "OCCIDENTE",
    "CENTRO",
    "LLANO",
    "LITORAL",
    "ALTIPLANO",
    "PIEDEMONTE",
    "PÁRAMO",
    "DESIERTO",
    "ARCHIPIÉLAGO",
]

MOJIBAKE: list[tuple[str, str]] = [
    ("Á", "Ã\x81"),
    ("É", "Ã\x89"),
    ("Í", "Ã\x8d"),
    ("Ó", "Ã\x93"),
    ("Ú", "Ã\x9a"),
    ("Ñ", "Ã\x91"),
]
SIN_TILDE: list[tuple[str, str]] = [
    ("Á", "A"),
    ("É", "E"),
    ("Í", "I"),
    ("Ó", "O"),
    ("Ú", "U"),
]


def _mojibake(texto: str) -> str:
    for bueno, roto in MOJIBAKE:
        texto = texto.replace(bueno, roto)
    return texto


def _sin_tilde(texto: str) -> str:
    for con, sin in SIN_TILDE:
        texto = texto.replace(con, sin)
    return texto


def _formatos_nit(base: str, dv: str) -> list[str]:
    """El mismo NIT como viaja en la práctica: con y sin DV, con puntos y guion."""
    con_puntos = f"{base[:3]}.{base[3:6]}.{base[6:]}"
    return [base, f"{base}{dv}", f"{con_puntos}-{dv}", f"{base}-{dv}", con_puntos]


def construir_conjunto() -> list[tuple[str, str]]:
    """Filas (NIT, RAZON_SOCIAL) del conjunto sintético, en orden determinista.

    El orden final se baraja con `random.Random(42)` (independiente de la
    semilla de hash) para intercalar los regímenes como en una base real.
    """
    filas: list[tuple[str, str]] = []

    # CON NIT: 24 grupos x 3-4 variantes; sufijos apilados, tildes, mojibake, paréntesis.
    for i, raiz in enumerate(RAICES_CON_NIT):
        base = str(900_100_000 + i * 7_919)
        formatos = _formatos_nit(base, digito_verificacion_dian(base))
        sufijos = FAMILIAS_SUFIJOS[i % len(FAMILIAS_SUFIJOS)]
        n_variantes = 3 + (i % 2)
        for j in range(n_variantes):
            nombre = f"{raiz} {sufijos[j]}"
            if j == 1:
                nombre = _sin_tilde(nombre)
            elif j == 2:
                nombre = _mojibake(nombre)
            elif j == 3:
                nombre = f"{raiz} {sufijos[j]} (ANTES {_sin_tilde(raiz)} LTDA)"
            filas.append((formatos[(i + j) % len(formatos)], nombre))

    # SIN NIT: grupos grandes (4-6 variantes).
    for variantes in GRUPOS_SIN_NIT_GRANDES:
        filas.extend(("", nombre) for nombre in variantes)

    # SIN NIT: 18 grupos x 2-3 variantes con familias de sufijos apiladas.
    for i, raiz in enumerate(RAICES_SIN_NIT):
        sufijos = FAMILIAS_SUFIJOS[(i + 2) % len(FAMILIAS_SUFIJOS)]
        n_variantes = 2 + (i % 2)
        for j in range(n_variantes):
            nombre = f"{raiz} {sufijos[j]}"
            if j == 1:
                nombre = _mojibake(nombre) if i % 3 == 0 else _sin_tilde(nombre)
            filas.append(("", nombre))

    # SIN NIT: tokens genéricos compartidos, un solo token distintivo.
    for toponimo in TOPONIMOS_INVENTADOS:
        filas.append(("", f"DISTRIBUIDORA DEL {toponimo} S.A.S."))
        filas.append(("", f"IMPORTADORA DEL {toponimo} LTDA"))

    # Cruce de regímenes: nombres de grupos CON NIT que llegan sin NIT.
    for raiz in RAICES_CON_NIT[::4]:
        filas.append(("", f"{_sin_tilde(raiz)} SAS"))

    random.Random(42).shuffle(filas)
    return filas


# ---------------------------------------------------------------------------
# Subproceso
# ---------------------------------------------------------------------------

MARCA_RESULTADO = "RESULTADO_F0_6="

#: Corre la fachada completa y deja en la ÚLTIMA línea de stdout la huella de
#: la correlativa (la bitácora del pipeline también escribe en stdout).
CODIGO_SUBPROCESO = f"""
import json, sys
import pandas as pd
from record_linkage.api import linkage
from record_linkage.evaluation.banco import huella_particion
filas = json.load(sys.stdin)
df = pd.DataFrame(filas, columns=["NIT", "RAZON_SOCIAL"], dtype=str)
res = linkage(sources={{"SINTETICA": df}}, work_dir=sys.argv[1], skip_reporting=True,
              trusted_sources=set(), col_ciudad=None)
c = res["correlative"].sort_values("ORIGINAL_INDEX", kind="stable")
indices = c["ORIGINAL_INDEX"].astype(int).tolist()
grupos = c["ID_GRUPO"].astype(str).tolist()
print({MARCA_RESULTADO!r} + json.dumps({{"huella": huella_particion(indices, grupos),
                                       "indices": indices, "grupos": grupos}}))
"""


def _en_subproceso(filas: Sequence[tuple[str, str]], semilla: int, work_dir: Path) -> dict:
    env = {**os.environ, "PYTHONHASHSEED": str(semilla)}
    r = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(CODIGO_SUBPROCESO), str(work_dir)],
        env=env,
        input=json.dumps(list(filas)),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, f"PYTHONHASHSEED={semilla} falló:\n{r.stderr[-2000:]}"
    lineas = [ln for ln in r.stdout.splitlines() if ln.startswith(MARCA_RESULTADO)]
    assert len(lineas) == 1, f"sin línea de resultado en stdout:\n{r.stdout[-1500:]}"
    return json.loads(lineas[0][len(MARCA_RESULTADO) :])


def _representantes(indices: Sequence[int], grupos: Sequence[str]) -> dict[int, int]:
    """Índice → mínimo índice de su grupo (la misma identidad que usa la huella)."""
    minimo: dict[str, int] = {}
    for i, g in zip(indices, grupos, strict=True):
        minimo[g] = min(minimo.get(g, i), i)
    return {i: minimo[g] for i, g in zip(indices, grupos, strict=True)}


def _filas_que_cambian(
    filas: Sequence[tuple[str, str]], a: dict, b: dict, maximo: int = 25
) -> list[str]:
    """Filas cuyo grupo (por representante) difiere entre las dos corridas."""
    ra = _representantes(a["indices"], a["grupos"])
    rb = _representantes(b["indices"], b["grupos"])
    cambios = []
    for i in sorted(set(ra) | set(rb)):
        if ra.get(i) != rb.get(i):
            nit, nombre = filas[i]
            cambios.append(
                f"  fila {i:3d} [{nit or 'SIN_NIT'}] {nombre!r}: {ra.get(i)} → {rb.get(i)}"
            )
    sobrantes = len(cambios) - maximo
    cambios = cambios[:maximo]
    if sobrantes > 0:
        cambios.append(f"  … y {sobrantes} filas más")
    return cambios


# ---------------------------------------------------------------------------
# Pruebas
# ---------------------------------------------------------------------------


def test_el_conjunto_sintetico_cumple_la_especificacion():
    """Guarda al propio instrumento: si el conjunto se degrada, la prueba es vacía."""
    filas = construir_conjunto()
    nombres = [n for _, n in filas]
    assert 150 <= len(filas) <= 250, len(filas)
    assert len(set(nombres)) == len(nombres), "hay razones sociales repetidas"
    con_nit = [nit for nit, _ in filas if nit]
    assert con_nit and len(con_nit) < len(filas), "faltan filas CON_NIT o SIN_NIT"
    assert any("." in nit and "-" in nit for nit in con_nit), "falta el formato 900.123.456-7"
    assert any(len(nit) == 9 for nit in con_nit) and any(len(nit) == 10 for nit in con_nit)
    assert sum(4 <= len(g) <= 6 for g in GRUPOS_SIN_NIT_GRANDES) >= 3
    assert any("Ã" in n for n in nombres), "falta mojibake"
    assert any("(" in n for n in nombres), "faltan paréntesis"
    assert any("S.A.S. E.S.P." in n for n in nombres) and any(
        "LTDA Y CIA S EN C" in n for n in nombres
    )


@pytest.mark.parametrize("semillas", [(1, 2), (0, 4242)])
def test_linkage_es_identico_entre_procesos_con_distinta_semilla(semillas, tmp_path):
    """Dos procesos con PYTHONHASHSEED distinto → la misma huella de partición."""
    filas = construir_conjunto()
    a, b = (_en_subproceso(filas, s, tmp_path / f"semilla_{s}") for s in semillas)

    # La igualdad de huellas sería vacía si la correlativa viniera incompleta o sin fusiones.
    for s, r in zip(semillas, (a, b), strict=True):
        assert sorted(r["indices"]) == list(range(len(filas))), (
            f"PYTHONHASHSEED={s}: la correlativa no trae exactamente una fila por registro"
        )
        assert len(set(r["grupos"])) < len(filas), f"PYTHONHASHSEED={s}: no fusionó nada"

    assert a["huella"] == b["huella"], (
        f"linkage() no es determinista entre procesos: PYTHONHASHSEED={semillas[0]} da "
        f"{a['huella'][:12]}… y PYTHONHASHSEED={semillas[1]} da {b['huella'][:12]}…\n"
        "La semilla de hash de Python es fija dentro de un proceso y distinta entre procesos, "
        "así que un orden derivado de un `set` de cadenas (sufijos, tokens genéricos, llaves de "
        "bloqueo) cambia entre sesiones; `test_es_determinista` no lo ve porque corre en UN "
        "proceso (CLAUDE.md §5). Busque el `set` y use una clave total "
        "(`sorted(..., key=(…, x))`). Filas que cambian de grupo (representante a → b):\n"
        + "\n".join(_filas_que_cambian(filas, a, b))
    )
