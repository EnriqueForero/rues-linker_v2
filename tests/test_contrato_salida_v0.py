"""Foto del esquema de salida de ``linkage()`` — contrato v0 (tarea F0.4).

Qué congela
-----------
Corre ``record_linkage.api.linkage()`` UNA sola vez (fixture de módulo) sobre
``tests/data_sintetica/dataset_sintetico_p2_extra_features.csv`` —una sola
fuente ``{"P2": df}`` leída con ``dtype=str`` y ``keep_default_na=False``,
``skip_reporting=False`` para que L6 escriba sus archivos, ``trusted_sources``
vacío y ``col_ciudad="CIUDAD"``— y compara contra el fixture
``tests/contratos/esquema_salida_v0.json``:

1. nombres, ORDEN y tipos de las columnas de ``L5_golden/correlative.parquet``
   y ``L5_golden/golden.parquet`` leídos con pandas. El tipo se registra con
   ``_nombre_tipo``: las columnas de texto quedan como ``str`` con
   independencia de la versión de pandas (en 3.x un parquet de texto se lee
   como ``str``; en 2.x, que es lo que instala el job de CI en Python 3.10
   porque pandas 3 exige Python ≥ 3.11, se lee como ``object``), y el resto
   como ``str(dtype)`` (``int64``, ``float64``…). Sin esa canonización la
   compuerta no podría pasar en 3.10 y enseñaría a desactivarla;
2. la lista ordenada de rutas relativas de TODOS los archivos escritos bajo
   ``work_dir`` (L1…L6 + ``manifest.json``), con multiplicidad: dos archivos
   que normalizan al mismo patrón cuentan como dos. Las partes variables se
   sustituyen por marcadores con las expresiones regulares de
   ``NORMALIZACIONES`` (marca de tiempo → ``<MARCA_TIEMPO>``, hash hexadecimal
   en minúsculas, mayúsculas o mezcla → ``<HASH>``, sufijo aleatorio de
   ``tempfile`` → ``<ALEATORIO>``): el fixture guarda el patrón, nunca el
   valor;
3. las claves de primer nivel de ``manifest.json``, las de ``_meta`` y, por
   fase (``L1_prep`` … ``L6_reporting``), las claves de la fase y de su
   ``meta``;
4. el número de filas de la correlativa (= filas del dataset) y del golden;
5. (adicional) las claves del ``dict`` que devuelve ``linkage()``: F1.9 lo
   reemplaza por ``ResultadoLinkage`` y conviene que ese cambio también
   quede declarado aquí;
6. (adicional) el ``_meta`` del fixture (dataset, filas de entrada,
   parámetros de la llamada y patrones de normalización) se compara con lo
   que el código produce hoy, para que no documente algo rancio si alguien
   cambia ``NORMALIZACIONES`` o ``PARAMETROS_LINKAGE`` sin regenerarlo.

Por qué existe
--------------
Es la referencia que la fase F1 cambiará A PROPÓSITO (contrato de salida,
F1.9 a F1.14). Si esta prueba falla hay dos salidas, y ninguna es silenciar
la prueba: o el cambio es intencional y el fixture se regenera y se revisa
EN EL MISMO PR, o es una regresión y se corrige el código.

Regenerar el fixture
--------------------
Con la variable de entorno ``ACTUALIZAR_ESQUEMA_V0=1`` la prueba reescribe
el fixture con la foto actual y luego compara (pasa trivialmente)::

    ACTUALIZAR_ESQUEMA_V0=1 PYTHONPATH=src pytest tests/test_contrato_salida_v0.py -q

Revise el ``git diff`` del JSON antes de hacer commit: cada línea que cambia
es una columna, un archivo o una clave que cambió de contrato.

Cifras de referencia frente a las del plan
------------------------------------------
El plan de ejecución (F0.4) cita 30 columnas de correlativa, 40 de golden y
31 archivos de L1…L6, medidos sobre el banco institucional de 18 columnas.
Sobre este dataset sintético de 5 columnas (NIT, RAZON_SOCIAL, CIUDAD,
TELEFONO, ID_GROUP) la foto es: 18 columnas de correlativa (5 de entrada +
SRC, ORIGINAL_INDEX, 6 derivadas de L1, ID_GRUPO y 4 de L5), 13 de golden y
29 archivos de L1…L6 más ``manifest.json`` (30 en total). Las diferencias
tienen explicación:

* columnas: la correlativa arrastra todas las columnas de entrada (18 en el
  banco, 5 aquí) y el golden de F1.1 pega columnas de la correlativa al
  consolidar por NIT, cosa que no ocurre en este dataset;
* archivos: faltan dos de los 31 del plan y en ambos casos es por diseño
  actual, no por azar: ``L6_reporting/visualizaciones/performance_timeline.png``
  no se genera nunca (el visualizador busca ``load_validate``… y el
  orquestador entrega ``L1_prep``…; es la tarea F1.6) y
  ``L6_reporting/casos_problematicos_detallado.xlsx`` solo se escribe cuando
  la suite detecta casos problemáticos (aquí no hay). Ambas ausencias quedan
  congeladas tal cual: cuando F1 las corrija, el fixture cambiará y el diff
  lo mostrará.

El dataset tiene 28 filas de datos (29 líneas contadas con el encabezado): la
prueba no fija «29» a mano sino que exige correlativa == filas leídas, y el
fixture guarda el valor medido.

Dependencias opcionales
-----------------------
Los PNG de L6 (``visualizaciones/*.png``, ``dashboard_ejecutivo*.png``,
``heatmap_interseccion_mejorado.png``, ``tarjeta_calidad_datos.png``) los
producen ``matplotlib`` y ``seaborn`` (extra ``[viz]``, incluido en ``[dev]``).
Si no están instalados, L6 los omite con un WARNING y la foto no coincide:
la prueba falla ANTES de correr el pipeline con un mensaje que dice qué
instalar, en vez de saltarse (una compuerta que se salta en silencio no mide).
No se usa ``plotly``.

Costo
-----
Medido en el entorno de desarrollo: ≈ 17 s en total, de los cuales ≈ 15 s
son L6 (figuras). Por eso corre en la suite rápida, sin marcador ``slow``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from record_linkage.api import linkage

RAIZ = Path(__file__).resolve().parent
RUTA_DATASET = RAIZ / "data_sintetica" / "dataset_sintetico_p2_extra_features.csv"
RUTA_FIXTURE = RAIZ / "contratos" / "esquema_salida_v0.json"
VARIABLE_ACTUALIZAR = "ACTUALIZAR_ESQUEMA_V0"
NOMBRE_FUENTE = "P2"

# Parámetros con los que se toma la foto. Se guardan en el fixture para que
# quien lo lea sepa con qué llamada se produjo.
PARAMETROS_LINKAGE: dict[str, Any] = {
    "fuentes": [NOMBRE_FUENTE],
    "trusted_sources": [],
    "col_ciudad": "CIUDAD",
    "skip_reporting": False,
    "lectura": {"dtype": "str", "keep_default_na": False},
}

# Partes variables de los nombres de archivo → marcador. Se aplican en orden;
# la marca de tiempo va primero para que sus dígitos no se confundan con un
# hash. El patrón de hash acepta hexadecimal en cualquier caja (un
# ``code_fingerprint`` en mayúsculas también es un valor, no un patrón) y
# exige al menos una letra a-f para no absorber contadores decimales
# (p. ej. ``chunk_00000001``).
NORMALIZACIONES: tuple[tuple[str, str], ...] = (
    (r"\d{8}_\d{6}", "<MARCA_TIEMPO>"),
    (r"\d{4}-\d{2}-\d{2}[T_ ]\d{2}[-:]\d{2}[-:]\d{2}", "<MARCA_TIEMPO>"),
    (r"(?<![0-9a-zA-Z])(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{8,}(?![0-9a-zA-Z])", "<HASH>"),
    (r"(?<![0-9a-zA-Z])tmp[a-z0-9_]{8}(?![0-9a-zA-Z])", "<ALEATORIO>"),
)

# Nombre canónico con el que la foto registra una columna de texto, sea cual
# sea la versión de pandas que la lea (3.x: ``str``; 2.x: ``object``).
TIPO_TEXTO = "str"

RECORDATORIO = (
    "El esquema de salida de linkage() cambió respecto al fixture "
    f"{RUTA_FIXTURE.relative_to(RAIZ.parent)}. El esquema solo cambia A PROPÓSITO "
    "(fase F1): si el cambio es intencional, regenere el fixture con "
    f"{VARIABLE_ACTUALIZAR}=1 pytest tests/{Path(__file__).name}, revise el diff del JSON y "
    "súbalo en el MISMO PR; si no lo es, corrija el código."
)

# Una columna en la foto: {"nombre": ..., "tipo": ...}. Se guarda como objeto
# y no como par posicional para que el diff del JSON se lea solo.
Columna = dict[str, str]
Columnas = list[Columna]


# ─────────────────────────────────────────────────────────────────────────────
# Normalización y toma de la foto
# ─────────────────────────────────────────────────────────────────────────────


def normalizar_ruta(ruta: str) -> str:
    """Sustituye las partes variables de una ruta relativa por marcadores."""
    for patron, marcador in NORMALIZACIONES:
        ruta = re.sub(patron, marcador, ruta)
    return ruta


def _nombre_tipo(dtype: Any) -> str:
    """Nombre del tipo de una columna, estable entre versiones de pandas.

    Una columna de texto leída de parquet es ``str`` en pandas 3 (``future.infer_string``)
    y ``object`` en pandas 2.x; también puede llegar como ``string``/``string[pyarrow]``
    si alguien activa ``string_storage``. Todas se registran como ``TIPO_TEXTO`` para
    que un cambio de versión no se lea como cambio de contrato. El resto (``int64``,
    ``float64``, ``bool``…) conserva ``str(dtype)``.
    """
    if pd.api.types.is_object_dtype(dtype) or isinstance(dtype, pd.StringDtype):
        return TIPO_TEXTO
    return str(dtype)


def _columnas(df: pd.DataFrame) -> Columnas:
    return [
        {"nombre": str(nombre), "tipo": _nombre_tipo(dtype)} for nombre, dtype in df.dtypes.items()
    ]


def _archivos_relativos(work_dir: Path) -> list[str]:
    rutas = (p.relative_to(work_dir).as_posix() for p in work_dir.rglob("*") if p.is_file())
    return sorted(normalizar_ruta(r) for r in rutas)


def _claves_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    fases: dict[str, dict[str, list[str]]] = {}
    for clave, valor in manifest.items():
        if clave.startswith("_") or not isinstance(valor, dict):
            continue
        meta = valor.get("meta")
        fases[clave] = {
            "claves": list(valor),
            "meta": list(meta) if isinstance(meta, dict) else [],
        }
    meta_global = manifest.get("_meta")
    return {
        "primer_nivel": list(manifest),
        "_meta": list(meta_global) if isinstance(meta_global, dict) else [],
        "fases": fases,
    }


def tomar_foto(work_dir: Path, resultado: dict[str, Any], filas_entrada: int) -> dict[str, Any]:
    """Construye la foto del esquema a partir de una corrida ya hecha en ``work_dir``."""
    correlativa = pd.read_parquet(work_dir / "L5_golden" / "correlative.parquet")
    golden = pd.read_parquet(work_dir / "L5_golden" / "golden.parquet")
    manifest = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))
    return {
        "_meta": {
            "proposito": (
                "Foto del esquema de salida de linkage() antes de F1 (tarea F0.4). "
                "Cambia solo a propósito, con el fixture regenerado en el mismo PR."
            ),
            "regenerar": f"{VARIABLE_ACTUALIZAR}=1 pytest tests/{Path(__file__).name}",
            "dataset": RUTA_DATASET.relative_to(RAIZ.parent).as_posix(),
            "filas_entrada": filas_entrada,
            "parametros_linkage": PARAMETROS_LINKAGE,
            "normalizaciones": [
                {"patron": patron, "marcador": marcador} for patron, marcador in NORMALIZACIONES
            ],
        },
        "correlativa": {"filas": len(correlativa), "columnas": _columnas(correlativa)},
        "golden": {"filas": len(golden), "columnas": _columnas(golden)},
        "archivos": _archivos_relativos(work_dir),
        "manifest": _claves_manifest(manifest),
        "resultado": {"claves": sorted(resultado)},
    }


def _normalizar_json(foto: dict[str, Any]) -> dict[str, Any]:
    """Pasa la foto por JSON para comparar tuplas con listas sin sorpresas."""
    return json.loads(json.dumps(foto, ensure_ascii=False))


# ─────────────────────────────────────────────────────────────────────────────
# Diferencias legibles
# ─────────────────────────────────────────────────────────────────────────────


def diferencias_columnas(esperadas: Columnas, actuales: Columnas, tabla: str) -> list[str]:
    """Dice qué columna apareció, desapareció, cambió de tipo o de orden."""
    esperado = {c["nombre"]: c["tipo"] for c in esperadas}
    actual = {c["nombre"]: c["tipo"] for c in actuales}
    mensajes: list[str] = []
    for nombre in (n for n in esperado if n not in actual):
        mensajes.append(f"{tabla}: desapareció la columna {nombre!r} (tipo {esperado[nombre]})")
    for nombre in (n for n in actual if n not in esperado):
        mensajes.append(f"{tabla}: apareció la columna {nombre!r} (tipo {actual[nombre]})")
    for nombre in (n for n in esperado if n in actual):
        if esperado[nombre] != actual[nombre]:
            mensajes.append(
                f"{tabla}: la columna {nombre!r} cambió de tipo: "
                f"{esperado[nombre]} → {actual[nombre]}"
            )
    orden_esperado = [n for n in esperado if n in actual]
    orden_actual = [n for n in actual if n in esperado]
    if orden_esperado != orden_actual:
        mensajes.append(
            f"{tabla}: cambió el ORDEN de las columnas comunes.\n"
            f"    esperado: {orden_esperado}\n"
            f"    actual:   {orden_actual}"
        )
    return mensajes


def diferencias_listas(esperada: list[str], actual: list[str], que: str) -> list[str]:
    """Dice qué elemento (archivo, clave) apareció, desapareció o cambió de multiplicidad.

    La multiplicidad importa: dos archivos que normalizan al mismo patrón (p. ej. dos
    ``config_auditoria_<MARCA_TIEMPO>.json`` en una corrida) son dos entradas, y que
    pasen de una a dos es un cambio de esquema aunque el conjunto no cambie.
    """
    conteo_esperado = Counter(esperada)
    conteo_actual = Counter(actual)
    mensajes = [f"{que}: desapareció {x!r}" for x in conteo_esperado if x not in conteo_actual]
    mensajes += [f"{que}: apareció {x!r}" for x in conteo_actual if x not in conteo_esperado]
    mensajes += [
        f"{que}: {x!r} aparece {conteo_actual[x]} veces (antes {conteo_esperado[x]})"
        for x in conteo_esperado
        if x in conteo_actual and conteo_actual[x] != conteo_esperado[x]
    ]
    return mensajes


def diferencias_manifest(esperado: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    mensajes = diferencias_listas(
        esperado["primer_nivel"], actual["primer_nivel"], "manifest.json (primer nivel)"
    )
    mensajes += diferencias_listas(esperado["_meta"], actual["_meta"], "manifest.json._meta")
    fases_esperadas = esperado["fases"]
    fases_actuales = actual["fases"]
    for fase in fases_esperadas:
        if fase not in fases_actuales:
            mensajes.append(f"manifest.json: desapareció la fase {fase!r}")
            continue
        mensajes += diferencias_listas(
            fases_esperadas[fase]["claves"], fases_actuales[fase]["claves"], f"manifest.json.{fase}"
        )
        mensajes += diferencias_listas(
            fases_esperadas[fase]["meta"],
            fases_actuales[fase]["meta"],
            f"manifest.json.{fase}.meta",
        )
    mensajes += [
        f"manifest.json: apareció la fase {fase!r}"
        for fase in fases_actuales
        if fase not in fases_esperadas
    ]
    return mensajes


def diferencias_filas(esperado: int, actual: int, tabla: str) -> list[str]:
    if esperado == actual:
        return []
    return [f"{tabla}: el número de filas cambió: {esperado} → {actual}"]


def diferencias_meta(esperado: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Dice qué campo de ``_meta`` del fixture ya no coincide con lo que produce el código."""
    mensajes = diferencias_listas(list(esperado), list(actual), "_meta del fixture")
    mensajes += [
        f"_meta.{clave} del fixture está rancio:\n"
        f"    fixture: {esperado[clave]!r}\n"
        f"    código:  {actual[clave]!r}"
        for clave in esperado
        if clave in actual and esperado[clave] != actual[clave]
    ]
    return mensajes


def _fallar_si_hay(mensajes: list[str]) -> None:
    if mensajes:
        detalle = "\n".join(f"  - {m}" for m in mensajes)
        pytest.fail(f"{RECORDATORIO}\n\nDiferencias:\n{detalle}", pytrace=False)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _exigir_dependencias_de_l6() -> None:
    faltan = [m for m in ("matplotlib", "seaborn") if importlib.util.find_spec(m) is None]
    if faltan:
        pytest.fail(
            f"Faltan {faltan} y L6 omitiría sus PNG en silencio, así que la foto no "
            "coincidiría por una causa ajena al contrato. Instale el extra: "
            'pip install -e ".[dev]" (o ".[viz]").',
            pytrace=False,
        )


@pytest.fixture(scope="module")
def foto_actual(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Corre linkage() UNA vez con L6 activo y devuelve la foto del esquema."""
    _exigir_dependencias_de_l6()
    df = pd.read_csv(RUTA_DATASET, dtype=str, keep_default_na=False)
    work_dir = tmp_path_factory.mktemp("esquema_v0")
    resultado = linkage(
        {NOMBRE_FUENTE: df},
        work_dir=str(work_dir),
        skip_reporting=False,
        trusted_sources=set(),
        col_ciudad="CIUDAD",
    )
    foto = _normalizar_json(tomar_foto(work_dir, resultado, filas_entrada=len(df)))
    if os.environ.get(VARIABLE_ACTUALIZAR) == "1":
        RUTA_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        RUTA_FIXTURE.write_text(
            json.dumps(foto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return foto


@pytest.fixture(scope="module")
def foto_esperada(foto_actual: dict[str, Any]) -> dict[str, Any]:
    """Lee el fixture. Depende de ``foto_actual`` para que, en modo regenerar,
    el archivo exista antes de leerse."""
    if not RUTA_FIXTURE.is_file():
        pytest.fail(
            f"No existe el fixture {RUTA_FIXTURE}. Sin él no hay contrato que comparar. "
            f"Genérelo con {VARIABLE_ACTUALIZAR}=1 pytest tests/{Path(__file__).name} y "
            "revise su contenido antes de hacer commit.",
            pytrace=False,
        )
    return json.loads(RUTA_FIXTURE.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# Pruebas del contrato
# ─────────────────────────────────────────────────────────────────────────────


def test_columnas_correlativa(foto_esperada: dict[str, Any], foto_actual: dict[str, Any]) -> None:
    _fallar_si_hay(
        diferencias_columnas(
            foto_esperada["correlativa"]["columnas"],
            foto_actual["correlativa"]["columnas"],
            "L5_golden/correlative.parquet",
        )
    )


def test_columnas_golden(foto_esperada: dict[str, Any], foto_actual: dict[str, Any]) -> None:
    _fallar_si_hay(
        diferencias_columnas(
            foto_esperada["golden"]["columnas"],
            foto_actual["golden"]["columnas"],
            "L5_golden/golden.parquet",
        )
    )


def test_archivos_escritos(foto_esperada: dict[str, Any], foto_actual: dict[str, Any]) -> None:
    _fallar_si_hay(
        diferencias_listas(
            foto_esperada["archivos"], foto_actual["archivos"], "archivo en work_dir"
        )
    )
    # Protege el fixture editado a mano (la foto actual sale ordenada por construcción).
    assert foto_esperada["archivos"] == sorted(foto_esperada["archivos"]), (
        "el fixture tiene los archivos desordenados: regenérelo, no lo edite a mano"
    )
    assert len(set(foto_esperada["archivos"])) == len(foto_esperada["archivos"]), (
        "el fixture tiene archivos repetidos: regenérelo, no lo edite a mano"
    )
    assert "manifest.json" in foto_actual["archivos"]


def test_claves_manifest(foto_esperada: dict[str, Any], foto_actual: dict[str, Any]) -> None:
    _fallar_si_hay(diferencias_manifest(foto_esperada["manifest"], foto_actual["manifest"]))


def test_conteo_filas(foto_esperada: dict[str, Any], foto_actual: dict[str, Any]) -> None:
    mensajes = diferencias_filas(
        foto_esperada["correlativa"]["filas"], foto_actual["correlativa"]["filas"], "correlativa"
    )
    mensajes += diferencias_filas(
        foto_esperada["golden"]["filas"], foto_actual["golden"]["filas"], "golden"
    )
    _fallar_si_hay(mensajes)
    # Propiedad, no foto: la correlativa tiene una fila por registro de entrada.
    assert foto_actual["correlativa"]["filas"] == foto_actual["_meta"]["filas_entrada"]
    assert 0 < foto_actual["golden"]["filas"] <= foto_actual["correlativa"]["filas"]


def test_claves_resultado(foto_esperada: dict[str, Any], foto_actual: dict[str, Any]) -> None:
    _fallar_si_hay(
        diferencias_listas(
            foto_esperada["resultado"]["claves"],
            foto_actual["resultado"]["claves"],
            "clave del dict que devuelve linkage()",
        )
    )


def test_meta_del_fixture_coincide_con_el_codigo(
    foto_esperada: dict[str, Any], foto_actual: dict[str, Any]
) -> None:
    """``_meta`` documenta cómo se tomó la foto; si el código cambia, el fixture también."""
    _fallar_si_hay(diferencias_meta(foto_esperada["_meta"], foto_actual["_meta"]))


def test_fixture_no_guarda_valores_variables(foto_esperada: dict[str, Any]) -> None:
    """El fixture guarda patrones: ninguna ruta conserva una marca de tiempo."""
    for ruta in foto_esperada["archivos"]:
        assert normalizar_ruta(ruta) == ruta, f"ruta sin normalizar en el fixture: {ruta!r}"
    assert any("<MARCA_TIEMPO>" in r for r in foto_esperada["archivos"]), (
        "config_auditoria_<MARCA_TIEMPO>.* debería estar en la foto; si L6 dejó de "
        "escribirla, actualice esta prueba junto con el fixture."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pruebas de los comparadores (no corren el pipeline)
# ─────────────────────────────────────────────────────────────────────────────


def test_normalizar_ruta_sustituye_partes_variables() -> None:
    assert (
        normalizar_ruta("L6_reporting/config_auditoria_20261006_041806.json")
        == "L6_reporting/config_auditoria_<MARCA_TIEMPO>.json"
    )
    assert normalizar_ruta("L3/lote_3fa9c2b1e0d4.db") == "L3/lote_<HASH>.db"
    # Un hash en mayúsculas o en caja mixta también es un valor, no un patrón.
    assert normalizar_ruta("L6_reporting/DEADBEEF01.png") == "L6_reporting/<HASH>.png"
    assert normalizar_ruta("L6_reporting/huella_DeadBeef01.png") == "L6_reporting/huella_<HASH>.png"
    assert normalizar_ruta("tmpab12cd34/x.parquet") == "<ALEATORIO>/x.parquet"
    # Un contador decimal no es un hash y una marca de tiempo no se toma por hash.
    assert normalizar_ruta("L2/chunk_00000001.parquet") == "L2/chunk_00000001.parquet"
    assert normalizar_ruta("L5_golden/golden.parquet") == "L5_golden/golden.parquet"


def test_nombre_tipo_canoniza_el_texto_entre_versiones_de_pandas() -> None:
    assert _nombre_tipo(np.dtype(object)) == "str"  # pandas 2.x
    assert _nombre_tipo(pd.StringDtype()) == "str"  # string[python]
    assert _nombre_tipo(pd.StringDtype("pyarrow")) == "str"  # string[pyarrow]
    assert _nombre_tipo(pd.StringDtype(na_value=np.nan)) == "str"  # pandas 3 "str"
    assert _nombre_tipo(np.dtype("int64")) == "int64"
    assert _nombre_tipo(np.dtype("float64")) == "float64"
    assert _nombre_tipo(np.dtype("bool")) == "bool"
    # Y lo mismo leyendo un DataFrame con y sin future.infer_string (pandas 3 vs 2.x).
    df = pd.DataFrame({"texto": ["a", "b"], "n": [1, 2]})
    with pd.option_context("future.infer_string", False):
        sin_inferencia = _columnas(df.astype({"texto": object}))
    assert sin_inferencia == _columnas(df) == _col([("texto", "str"), ("n", "int64")])


def _col(pares: list[tuple[str, str]]) -> Columnas:
    return [{"nombre": n, "tipo": t} for n, t in pares]


def test_diferencias_columnas_describe_cada_cambio() -> None:
    esperadas = _col([("A", "str"), ("B", "int64"), ("C", "float64")])
    actuales = _col([("C", "float64"), ("A", "str"), ("D", "bool"), ("B", "str")])
    mensajes = "\n".join(diferencias_columnas(esperadas, actuales, "tabla"))
    assert "apareció la columna 'D' (tipo bool)" in mensajes
    assert "la columna 'B' cambió de tipo: int64 → str" in mensajes
    assert "cambió el ORDEN" in mensajes
    assert "desapareció" not in mensajes
    assert diferencias_columnas(_col([("A", "str")]), [], "t") == [
        "t: desapareció la columna 'A' (tipo str)"
    ]
    assert diferencias_columnas(esperadas, list(esperadas), "t") == []


def test_diferencias_manifest_describe_fases_y_claves() -> None:
    esperado = {
        "primer_nivel": ["_meta", "L1_prep"],
        "_meta": ["schema_version"],
        "fases": {"L1_prep": {"claves": ["hash", "meta"], "meta": ["duration"]}},
    }
    actual = {
        "primer_nivel": ["_meta", "L1_prep", "L6_reporting"],
        "_meta": [],
        "fases": {
            "L1_prep": {"claves": ["hash", "meta", "omitidos"], "meta": []},
            "L6_reporting": {"claves": ["hash"], "meta": []},
        },
    }
    mensajes = diferencias_manifest(esperado, actual)
    assert "manifest.json (primer nivel): apareció 'L6_reporting'" in mensajes
    assert "manifest.json._meta: desapareció 'schema_version'" in mensajes
    assert "manifest.json.L1_prep: apareció 'omitidos'" in mensajes
    assert "manifest.json.L1_prep.meta: desapareció 'duration'" in mensajes
    assert "manifest.json: apareció la fase 'L6_reporting'" in mensajes
    assert diferencias_manifest(esperado, esperado) == []


def test_diferencias_listas_detecta_la_multiplicidad() -> None:
    patron = "L6_reporting/config_auditoria_<MARCA_TIEMPO>.json"
    assert diferencias_listas(["a", patron], ["a", patron, patron], "archivo") == [
        f"archivo: {patron!r} aparece 2 veces (antes 1)"
    ]
    assert diferencias_listas(["a", patron, patron], ["a", patron], "archivo") == [
        f"archivo: {patron!r} aparece 1 veces (antes 2)"
    ]
    assert diferencias_listas(["a", "b"], ["b", "c"], "clave") == [
        "clave: desapareció 'a'",
        "clave: apareció 'c'",
    ]
    assert diferencias_listas(["a", "b"], ["b", "a"], "clave") == []


def test_diferencias_meta_describe_el_campo_rancio() -> None:
    esperado = {"dataset": "x.csv", "filas_entrada": 28, "normalizaciones": [{"patron": "a"}]}
    actual = {"dataset": "x.csv", "filas_entrada": 28, "normalizaciones": [{"patron": "b"}]}
    mensajes = diferencias_meta(esperado, actual)
    assert len(mensajes) == 1
    assert mensajes[0].startswith("_meta.normalizaciones del fixture está rancio")
    assert "fixture: [{'patron': 'a'}]" in mensajes[0]
    assert "código:  [{'patron': 'b'}]" in mensajes[0]
    assert diferencias_meta(esperado, {**esperado, "extra": 1}) == [
        "_meta del fixture: apareció 'extra'"
    ]
    assert diferencias_meta(esperado, esperado) == []


def test_fallar_si_hay_recuerda_que_el_esquema_cambia_a_proposito() -> None:
    with pytest.raises(pytest.fail.Exception) as info:
        _fallar_si_hay(["tabla: apareció la columna 'X' (tipo str)"])
    texto = str(info.value)
    assert VARIABLE_ACTUALIZAR in texto
    assert "A PROPÓSITO" in texto
    assert "apareció la columna 'X'" in texto
    _fallar_si_hay([])  # sin diferencias no falla
