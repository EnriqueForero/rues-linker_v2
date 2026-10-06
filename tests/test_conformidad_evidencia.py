"""La evidencia de conformidad dice si se corroboró (F0.3).

Qué se protege
--------------
Hecho medido antes de esta tarea: `scripts/conformidad.py` sin `--corroborar`
reprueba C09 y C21 (frontera, deuda declarada) y con `--corroborar` aprueba los
43; pero el JSON de evidencia no registraba el flag, `nota` iba vacía y
`docs/evidencia/conformidad_dedup_base.json` era en realidad la corrida
corroborada. Una evidencia que no dice cómo se produjo no es evidencia.

1. `Informe` registra `corroborar` y `camino`, y los serializa.
2. El script los escribe en el JSON desde sus argumentos: sin `--corroborar`
   el archivo dice `false`, con él dice `true`, y en los dos la corrida PASA
   (0 casos firmes en FALLA). C09 y C21 reprueban solo sin corroborar.

Las pruebas que corren el script NO se saltan: el conjunto se ancla al
repositorio (como en `test_conformidad_v020.py`), no al cwd. Si el conjunto
no está, esto falla y se ve.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-10-06  ·  Version: 0.22.4
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cargar_script import cargar_script

from record_linkage.evaluation.conformidad import (
    CAMINO_POR_DEFECTO,
    ConjuntoConformidad,
    cargar_conjunto,
    evaluar_dedup,
    evaluar_linkage,
)

RAIZ_REPO = Path(__file__).resolve().parents[1]
DIRECTORIO_CONJUNTO = RAIZ_REPO / "data" / "conformidad"

# conformidad.py es un script (no parte del paquete): se carga por ruta.
script_conformidad = cargar_script("conformidad", nombre_modulo="script_conformidad")

#: Los dos casos que el catálogo marca TP_DIFICIL y que solo resuelve F3.
CASOS_SOLO_CON_CORROBORACION = {"C09", "C21"}


@pytest.fixture(scope="module")
def conjunto() -> ConjuntoConformidad:
    # Sin `pytest.skip`: un conjunto ausente es un fallo, no un entorno raro.
    return cargar_conjunto(DIRECTORIO_CONJUNTO)


# ── 1. La librería registra el flag ──────────────────────────────────────


def test_evaluar_dedup_registra_corroborar_y_camino(conjunto: ConjuntoConformidad) -> None:
    verdad = conjunto.dedup_registros["ID_GRUPO_ESPERADO"].to_numpy()
    informe = evaluar_dedup(conjunto, verdad, etiqueta="t", version="0", corroborar=True)
    datos = informe.a_dict()
    assert datos["corroborar"] is True
    assert datos["camino"] == CAMINO_POR_DEFECTO == "dedupe_esquema"
    assert informe.resumen().count("corroborar") >= 1, "el resumen legible también lo dice"


def test_evaluar_linkage_registra_corroborar_y_camino(conjunto: ConjuntoConformidad) -> None:
    verdaderos = {
        (str(f.REG_ID_A), str(f.REG_ID_B))
        for f in conjunto.linkage_verdad.itertuples(index=False)
        if str(f.MATCH_ESPERADO).strip().lower() == "true"
    }
    informe = evaluar_linkage(
        conjunto, verdaderos, etiqueta="t", version="0", corroborar=False, camino="otro_camino"
    )
    datos = informe.a_dict()
    assert datos["corroborar"] is False
    assert datos["camino"] == "otro_camino"


def test_el_flag_sobrevive_al_json(conjunto: ConjuntoConformidad, tmp_path: Path) -> None:
    verdad = conjunto.dedup_registros["ID_GRUPO_ESPERADO"].to_numpy()
    informe = evaluar_dedup(conjunto, verdad, etiqueta="t", version="0", corroborar=True)
    datos = json.loads(informe.guardar(tmp_path).read_text(encoding="utf-8"))
    assert datos["corroborar"] is True
    assert datos["camino"] == "dedupe_esquema"


# ── 2. El script escribe el flag desde sus argumentos ────────────────────


def _correr_script(evidencia: Path, *extra: str) -> dict[str, dict]:
    """Corre `main` del script y devuelve {escenario: json} de lo que escribió."""
    argumentos = ["--datos", str(DIRECTORIO_CONJUNTO), "--evidencia", str(evidencia), *extra]
    assert script_conformidad.main(argumentos) == 0, "ningún caso firme puede reprobar"
    archivos = sorted(evidencia.glob("conformidad_*.json"))
    assert len(archivos) == 2, [a.name for a in archivos]
    salida = {}
    for archivo in archivos:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        salida[datos["escenario"]] = datos
    return salida


def _reprobados(datos: dict) -> set[str]:
    return {c["codigo"] for c in datos["casos"] if not c["pasa"]}


def test_sin_corroborar_el_json_dice_false_y_pasa(tmp_path: Path) -> None:
    informes = _correr_script(tmp_path / "sin")
    for escenario in ("dedup", "linkage"):
        datos = informes[escenario]
        assert datos["corroborar"] is False, escenario
        assert datos["camino"] == "dedupe_esquema", escenario
        assert datos["pasa"] is True, escenario
        assert not {c["codigo"] for c in datos["casos"] if not c["pasa"] and not c["frontera"]}
    # El hecho medido: sin F3, C09 y C21 reprueban y son frontera (deuda, no regresión).
    assert _reprobados(informes["dedup"]) == CASOS_SOLO_CON_CORROBORACION
    assert all(
        c["frontera"]
        for c in informes["dedup"]["casos"]
        if c["codigo"] in CASOS_SOLO_CON_CORROBORACION
    )


def test_con_corroborar_el_json_dice_true_y_aprueba_los_43(tmp_path: Path) -> None:
    informes = _correr_script(tmp_path / "con", "--corroborar", "--etiqueta", "corroborado")
    for escenario in ("dedup", "linkage"):
        datos = informes[escenario]
        assert datos["corroborar"] is True, escenario
        assert datos["camino"] == "dedupe_esquema", escenario
        assert datos["pasa"] is True, escenario
        assert _reprobados(datos) == set(), escenario
    assert (tmp_path / "con" / "conformidad_dedup_corroborado.json").is_file()


# ── 3. La evidencia versionada dice la verdad ────────────────────────────


@pytest.mark.parametrize("escenario", ["dedup", "linkage"])
def test_la_evidencia_versionada_registra_el_flag(escenario: str) -> None:
    """`_base` es SIN corroborar y `_corroborado` es CON; los dos lo declaran."""
    evidencia = RAIZ_REPO / "docs" / "evidencia"
    base = json.loads((evidencia / f"conformidad_{escenario}_base.json").read_text("utf-8"))
    con = json.loads((evidencia / f"conformidad_{escenario}_corroborado.json").read_text("utf-8"))
    assert base["corroborar"] is False and base["pasa"] is True
    assert con["corroborar"] is True and con["pasa"] is True
    assert base["camino"] == con["camino"] == "dedupe_esquema"
    if escenario == "dedup":
        assert _reprobados(base) == CASOS_SOLO_CON_CORROBORACION
        assert _reprobados(con) == set()
