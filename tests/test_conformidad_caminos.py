"""F2.13 — Conformidad sobre el camino de producción, 43/43 con veredicto por camino.

Qué se protege
--------------
Hasta F2.13 los 43 casos corrían solo por ``api.dedupe_esquema`` (multicampo) y
seis de ellos —C20, C24, C25, solo_A, solo_B, homonimo_cross— no tenían
veredicto: las hojas de pares no los nombran, así que la evaluación por pares
los dejaba sin fila. Aquí se exige:

1. **43/43 con veredicto explícito.** Los casos sin pares declarados se
   evalúan por registro contra la columna de verdad (``ID_GRUPO_ESPERADO`` /
   ``ENTIDAD_ID_ESPERADO``) y el resultado dice de dónde salió
   (``origen``). Un singleton que se fusiona reprueba, igual que un par.
2. **Camino registrado en la evidencia y en el nombre del archivo.** El JSON
   del camino por defecto conserva su nombre (compatibilidad con la evidencia
   de F0); los demás llevan el camino en el nombre.
3. **El script corre los mismos casos por ``linkage()`` y por
   ``flujo.cruce``** (``--camino``), y rechaza ``--corroborar`` en esos
   caminos: la corroboración por contacto (F3) vive solo en el motor
   multicampo, y una evidencia que dijera ``corroborar: true`` sobre un motor
   que no la tiene mentiría.
4. **La evidencia versionada de los caminos nuevos dice la verdad** y
   ``docs/CONFORMIDAD.md`` nombra los 43 casos.

Nada se salta: el conjunto se ancla al repositorio y su ausencia es un fallo.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-10-06  ·  Version: 0.22.4
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from cargar_script import cargar_script

from record_linkage.evaluation.conformidad import (
    CAMINO_POR_DEFECTO,
    CAMINOS,
    ORIGEN_PARES_DECLARADOS,
    ORIGEN_VERDAD_POR_REGISTRO,
    ConjuntoConformidad,
    cargar_conjunto,
    evaluar_dedup,
    evaluar_linkage,
)

RAIZ_REPO = Path(__file__).resolve().parents[1]
DIRECTORIO_CONJUNTO = RAIZ_REPO / "data" / "conformidad"
EVIDENCIA = RAIZ_REPO / "docs" / "evidencia"

script_conformidad = cargar_script("conformidad", nombre_modulo="script_conformidad_f213")

#: Los seis casos que hasta F2.13 no tenían veredicto: ninguna hoja de pares
#: los nombra, así que solo la columna de verdad puede juzgarlos.
CASOS_SIN_PARES_DEDUP = {"C20", "C24", "C25"}
CASOS_SIN_PARES_LINKAGE = {"solo_A", "solo_B", "homonimo_cross"}


@pytest.fixture(scope="module")
def conjunto() -> ConjuntoConformidad:
    return cargar_conjunto(DIRECTORIO_CONJUNTO)


def _verdad_dedup(conjunto: ConjuntoConformidad):
    return conjunto.dedup_registros["ID_GRUPO_ESPERADO"].to_numpy().copy()


def _verdad_linkage(conjunto: ConjuntoConformidad) -> set[tuple[str, str]]:
    return {
        (str(f.REG_ID_A), str(f.REG_ID_B))
        for f in conjunto.linkage_verdad.itertuples(index=False)
        if str(f.MATCH_ESPERADO).strip().lower() == "true"
    }


def _posicion(conjunto: ConjuntoConformidad) -> dict[str, int]:
    return {str(r): i for i, r in enumerate(conjunto.dedup_registros["REG_ID"])}


# ── 1. 43/43 con veredicto explícito ─────────────────────────────────────


def test_los_43_codigos_del_catalogo_tienen_veredicto(conjunto: ConjuntoConformidad) -> None:
    dedup = evaluar_dedup(conjunto, _verdad_dedup(conjunto), etiqueta="t", version="0")
    linkage = evaluar_linkage(conjunto, _verdad_linkage(conjunto), etiqueta="t", version="0")
    codigos = {c.codigo for c in dedup.casos} | {c.codigo for c in linkage.casos}
    assert codigos == set(conjunto.catalogo["CODIGO"]), "faltan casos del catálogo"
    assert len(codigos) == 43
    assert dedup.pasa and linkage.pasa
    assert all(c.pasa for c in dedup.casos + linkage.casos)


def test_los_casos_sin_pares_declaran_su_origen(conjunto: ConjuntoConformidad) -> None:
    dedup = evaluar_dedup(conjunto, _verdad_dedup(conjunto), etiqueta="t", version="0")
    origen = {c.codigo: c.origen for c in dedup.casos}
    assert {k for k, v in origen.items() if v == ORIGEN_VERDAD_POR_REGISTRO} == (
        CASOS_SIN_PARES_DEDUP
    )
    assert origen["C01"] == ORIGEN_PARES_DECLARADOS
    # C25 son 8 singletons: un veredicto por registro.
    c25 = next(c for c in dedup.casos if c.codigo == "C25")
    assert (c25.pares, c25.aciertos, c25.frontera) == (8, 8, False)

    linkage = evaluar_linkage(conjunto, _verdad_linkage(conjunto), etiqueta="t", version="0")
    origen = {c.codigo: c.origen for c in linkage.casos}
    assert {k for k, v in origen.items() if v == ORIGEN_VERDAD_POR_REGISTRO} == (
        CASOS_SIN_PARES_LINKAGE
    )


def test_un_singleton_fusionado_reprueba_su_caso(conjunto: ConjuntoConformidad) -> None:
    """C25 es TN: 'no fusiona'. Fusionarlo con cualquiera es un fallo firme."""
    verdad = _verdad_dedup(conjunto)
    posicion = _posicion(conjunto)
    registros = conjunto.dedup_registros
    singleton = str(registros.loc[registros["CASO_TIPO"] == "C25", "REG_ID"].iloc[0])
    otro = str(registros.loc[registros["CASO_TIPO"] == "C01", "REG_ID"].iloc[0])
    verdad[posicion[singleton]] = verdad[posicion[otro]]
    informe = evaluar_dedup(conjunto, verdad, etiqueta="t", version="0")
    c25 = next(c for c in informe.casos if c.codigo == "C25")
    assert not c25.pasa and not c25.frontera
    assert c25.aciertos == 7 and c25.pares == 8
    assert any(singleton in d for d in c25.detalle), c25.detalle
    assert not informe.pasa


def test_un_registro_con_datos_faltantes_que_se_fusiona_reprueba_c20(
    conjunto: ConjuntoConformidad,
) -> None:
    verdad = _verdad_dedup(conjunto)
    posicion = _posicion(conjunto)
    registros = conjunto.dedup_registros
    vacio = str(registros.loc[registros["CASO_TIPO"] == "C20", "REG_ID"].iloc[0])
    gen = str(registros.loc[registros["CASO_TIPO"] == "GEN", "REG_ID"].iloc[0])
    verdad[posicion[vacio]] = verdad[posicion[gen]]
    informe = evaluar_dedup(conjunto, verdad, etiqueta="t", version="0")
    reprobados = {c.codigo for c in informe.casos if not c.pasa}
    assert "C20" in reprobados


def test_un_homonimo_que_cruza_reprueba_sin_contarse_dos_veces(
    conjunto: ConjuntoConformidad,
) -> None:
    """A20↔B21 (homónimo cross-base) es un cruce inventado. Hasta F2.13 solo
    se veía si ocurría (imputado como FP no declarado); ahora el caso tiene
    veredicto siempre y el cruce no se cuenta dos veces."""
    limpio = evaluar_linkage(conjunto, _verdad_linkage(conjunto), etiqueta="t", version="0")
    homonimo = next(c for c in limpio.casos if c.codigo == "homonimo_cross")
    assert homonimo.pasa and homonimo.pares == 2, "A20 y B21: un veredicto por registro"

    sucio = evaluar_linkage(
        conjunto, _verdad_linkage(conjunto) | {("20", "21")}, etiqueta="t", version="0"
    )
    homonimo = next(c for c in sucio.casos if c.codigo == "homonimo_cross")
    assert not homonimo.pasa and homonimo.pares == 2 and homonimo.aciertos == 0
    assert not sucio.pasa
    assert sucio.precision < 1.0, "el cruce inventado sí baja la precisión"


def test_un_solo_a_que_cruza_reprueba(conjunto: ConjuntoConformidad) -> None:
    sucio = evaluar_linkage(
        conjunto, _verdad_linkage(conjunto) | {("16", "16")}, etiqueta="t", version="0"
    )
    reprobados = {c.codigo for c in sucio.casos if not c.pasa}
    assert reprobados == {"solo_A", "solo_B"}, "A16 (solo_A) y B16 (solo_B) fallan los dos"


def test_el_resumen_y_el_json_llevan_el_origen(
    conjunto: ConjuntoConformidad, tmp_path: Path
) -> None:
    informe = evaluar_dedup(conjunto, _verdad_dedup(conjunto), etiqueta="t", version="0")
    datos = json.loads(informe.guardar(tmp_path).read_text(encoding="utf-8"))
    assert {c["origen"] for c in datos["casos"]} == {
        ORIGEN_PARES_DECLARADOS,
        ORIGEN_VERDAD_POR_REGISTRO,
    }
    assert ORIGEN_VERDAD_POR_REGISTRO in informe.resumen()


# ── 2. El camino va en el nombre del archivo ─────────────────────────────


def test_el_camino_por_defecto_conserva_el_nombre_del_archivo(
    conjunto: ConjuntoConformidad, tmp_path: Path
) -> None:
    informe = evaluar_dedup(conjunto, _verdad_dedup(conjunto), etiqueta="base", version="0")
    assert informe.guardar(tmp_path).name == "conformidad_dedup_base.json"


@pytest.mark.parametrize(
    ("camino", "nombre"),
    [
        ("linkage", "conformidad_dedup_linkage_base.json"),
        ("flujo.cruce", "conformidad_dedup_flujo_cruce_base.json"),
    ],
)
def test_otro_camino_va_en_el_nombre(
    conjunto: ConjuntoConformidad, tmp_path: Path, camino: str, nombre: str
) -> None:
    informe = evaluar_dedup(
        conjunto, _verdad_dedup(conjunto), etiqueta="base", version="0", camino=camino
    )
    assert informe.guardar(tmp_path).name == nombre
    assert json.loads((tmp_path / nombre).read_text(encoding="utf-8"))["camino"] == camino


def test_un_camino_desconocido_se_rechaza(conjunto: ConjuntoConformidad) -> None:
    with pytest.raises(ValueError, match="camino"):
        evaluar_dedup(conjunto, _verdad_dedup(conjunto), etiqueta="t", version="0", camino="x")


def test_los_caminos_declarados_son_los_tres() -> None:
    assert CAMINOS == ("dedupe_esquema", "linkage", "flujo.cruce")
    assert CAMINOS[0] == CAMINO_POR_DEFECTO


# ── 3. El script corre por los caminos de producción ─────────────────────


def _correr(evidencia: Path, *extra: str) -> tuple[int, dict[str, dict]]:
    argumentos = ["--datos", str(DIRECTORIO_CONJUNTO), "--evidencia", str(evidencia), *extra]
    codigo = script_conformidad.main(argumentos)
    salida = {}
    for archivo in sorted(evidencia.glob("conformidad_*.json")):
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        salida[archivo.name] = datos
    return codigo, salida


def _firmes_reprobados(datos: dict) -> set[str]:
    return {c["codigo"] for c in datos["casos"] if not c["pasa"] and not c["frontera"]}


@pytest.mark.parametrize("camino", ["linkage", "flujo.cruce"])
def test_el_script_corre_los_43_por_el_camino_de_produccion(tmp_path: Path, camino: str) -> None:
    """Veredicto explícito para los 43 por el camino pedido; el JSON lo registra.

    El veredicto se registra tal cual (nota del coordinador): esta prueba exige
    que los 43 tengan fila y que el camino quede escrito, no que todos pasen.
    """
    _, informes = _correr(tmp_path, "--camino", camino, "--etiqueta", "prueba")
    sufijo = camino.replace(".", "_")
    nombres = {
        f"conformidad_dedup_{sufijo}_prueba.json",
        f"conformidad_linkage_{sufijo}_prueba.json",
    }
    assert set(informes) == nombres, sorted(informes)
    codigos: set[str] = set()
    for datos in informes.values():
        assert datos["camino"] == camino
        assert datos["corroborar"] is False
        assert datos["extra"]["fuentes"], "la evidencia dice con qué fuentes se corrió"
        codigos |= {c["codigo"] for c in datos["casos"]}
    assert len(codigos) == 43


def test_corroborar_no_existe_fuera_del_motor_multicampo(tmp_path: Path, capsys) -> None:
    """``--corroborar`` con ``--camino linkage`` se rechaza con mensaje accionable."""
    with pytest.raises(SystemExit) as salida:
        _correr(tmp_path, "--camino", "linkage", "--corroborar")
    assert salida.value.code == 2
    mensaje = capsys.readouterr().err
    assert "corroborar" in mensaje and "dedupe_esquema" in mensaje
    assert not list(tmp_path.glob("*.json")), "no se escribe evidencia a medias"


def test_el_camino_por_defecto_no_cambia(tmp_path: Path) -> None:
    """La línea base de ``dedupe_esquema`` sigue: 0 firmes en FALLA, C09/C21 frontera."""
    codigo, informes = _correr(tmp_path)
    assert codigo == 0
    assert set(informes) == {"conformidad_dedup_base.json", "conformidad_linkage_base.json"}
    for datos in informes.values():
        assert datos["camino"] == CAMINO_POR_DEFECTO
        assert _firmes_reprobados(datos) == set()
    assert {c["codigo"] for c in informes["conformidad_dedup_base.json"]["casos"]} >= (
        CASOS_SIN_PARES_DEDUP
    )


# ── 4. La evidencia versionada y la documentación ────────────────────────


@pytest.mark.parametrize("camino", ["linkage", "flujo_cruce"])
@pytest.mark.parametrize("escenario", ["dedup", "linkage"])
def test_la_evidencia_versionada_del_camino_dice_la_verdad(camino: str, escenario: str) -> None:
    ruta = EVIDENCIA / f"conformidad_{escenario}_{camino}_base.json"
    assert ruta.is_file(), f"falta la evidencia del camino: {ruta.name}"
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert datos["camino"] == camino.replace("flujo_cruce", "flujo.cruce")
    assert datos["corroborar"] is False
    assert all("origen" in c for c in datos["casos"])


def test_la_evidencia_versionada_cubre_los_43_por_cada_camino() -> None:
    for camino in ("", "linkage_", "flujo_cruce_"):
        codigos: set[str] = set()
        for escenario in ("dedup", "linkage"):
            ruta = EVIDENCIA / f"conformidad_{escenario}_{camino}base.json"
            codigos |= {c["codigo"] for c in json.loads(ruta.read_text("utf-8"))["casos"]}
        assert len(codigos) == 43, (camino, len(codigos))


def test_la_documentacion_nombra_los_43_casos_y_los_tres_caminos() -> None:
    texto = (RAIZ_REPO / "docs" / "CONFORMIDAD.md").read_text(encoding="utf-8")
    catalogo = cargar_conjunto(DIRECTORIO_CONJUNTO).catalogo
    faltan = [
        codigo
        for codigo in catalogo["CODIGO"]
        if not re.search(rf"`{re.escape(str(codigo))}`", texto)
    ]
    assert not faltan, f"docs/CONFORMIDAD.md no tiene veredicto para: {faltan}"
    for camino in CAMINOS:
        assert camino in texto
