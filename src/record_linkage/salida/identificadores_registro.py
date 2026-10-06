"""record_linkage.salida.identificadores_registro — Las dos reglas de identidad, escritas una vez (F2.3).

Qué hay aquí
------------
* ``asignar_id_registro``: la clave por registro del estándar (``ID_REGISTRO``):
  ``<SRC>-<id nativo>`` si ``col_id`` nombra una columna única y no vacía por
  fila EN ESA FUENTE; si no, ``<SRC>-F<ORIGINAL_INDEX>``. La regla aplicada por
  fuente, y el motivo cuando se cae a la fila, van en la información que
  devuelve (y de ahí al manifiesto): nada se decide en silencio.
* ``id_grupo_por_clave_minima``: el entero de grupo determinista por
  contenido: cada grupo se identifica por el MENOR ``ID_REGISTRO`` que
  contiene (orden lexicográfico) y ``ID_GRUPO`` es la posición de esa clave
  entre las claves mínimas de todos los grupos, ordenadas (``factorize``
  con ``sort=True``: entero denso desde 0, estable ante cualquier reorden de
  las filas y ante cualquier relabel previo). Misma entrada → mismos ids.
* ``nombre_efectivo``: cómo se llama en el motor la columna que el usuario
  nombró (renombres canónicos de la ingesta: ``col_name`` → ``RAZON_SOCIAL``…).

Quién las usa
-------------
L4 (``engine/clusterer.py`` vía ``pipeline/orchestrator.py``) y
``salida/completar.py``. Antes de F2.3 ``ID_GRUPO`` era la posición mínima
del grupo y cambiaba con cualquier fila nueva o reordenada; la huella del
banco (``evaluation.banco.huella_particion``) no depende del valor del id,
así que el cambio no mueve la huella (medido: 1e365ba8 antes y después).

Por qué el orden es lexicográfico y no numérico
-----------------------------------------------
``ID_REGISTRO`` es texto (``CRM-A012``, ``RUES-F10``): el único orden total
que existe para cualquier fuente es el de cadenas. Eso implica que
``RUES-F10`` < ``RUES-F2``; es irrelevante para la estabilidad (lo que
importa es que el orden sea el mismo en toda corrida) y así lo declara el
estándar (ESTANDAR_SALIDA.md: «el menor ID_REGISTRO del grupo en orden
lexicográfico»).

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-10-06  ·  Version: 0.23.0
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from ..pipeline.errores import mensaje_accionable

__all__ = ["asignar_id_registro", "id_grupo_por_clave_minima", "nombre_efectivo"]


def nombre_efectivo(col: str, presentes: Collection[str], canonicos: Mapping[str, str]) -> str:
    """Nombre con el que el motor conoce la columna ``col`` del usuario.

    Si el usuario nombró ``col_name="NOMBRE"`` el motor la renombró a
    ``RAZON_SOCIAL`` en la ingesta: ``col`` ya no está y la canónica sí. En
    cualquier otro caso se devuelve ``col`` tal cual.
    """
    if col in presentes:
        return col
    canonico = canonicos.get(col)
    if canonico is not None and canonico in presentes:
        return canonico
    return col


def _texto(serie: pd.Series) -> pd.Series:
    """Texto sin espacios de borde; nulo se queda nulo (pandas 3 no convierte NaN a 'nan')."""
    return serie.astype("string").str.strip()


def asignar_id_registro(
    marco: pd.DataFrame, col_id: str | None
) -> tuple[pd.Series, dict[str, dict[str, str]]]:
    """``ID_REGISTRO`` por fila y la regla aplicada por fuente.

    Args:
        marco: tabla con ``SRC`` y ``ORIGINAL_INDEX`` (y ``col_id`` si se da).
        col_id: columna única por fila en la fuente, con el nombre que tiene
            en ``marco`` (resuélvalo antes con ``nombre_efectivo`` si el motor
            la renombró), o None para usar siempre ``<SRC>-F<ORIGINAL_INDEX>``.

    Returns:
        ``(id_registro, info)``: la serie de texto alineada con ``marco`` y,
        por fuente, ``{"regla": "col_id"|"fila", ...}`` con el motivo cuando
        una fuente cae a la fila (vacíos o repetidos en ``col_id``).

    Raises:
        ValueError: ``col_id`` no es columna de ``marco`` (mensaje accionable).
    """
    src = marco["SRC"].astype("string")
    fila = src + "-F" + marco["ORIGINAL_INDEX"].astype("int64").astype("string")
    fuentes = [str(f) for f in src.dropna().unique()]
    if col_id is None:
        return fila, {f: {"regla": "fila", "motivo": "sin col_id"} for f in fuentes}
    if col_id not in marco.columns:
        raise ValueError(
            mensaje_accionable(
                f"col_id='{col_id}' no es una columna de ninguna fuente "
                f"(hay: {[c for c in marco.columns][:30]}).",
                "ID_REGISTRO debía construirse con el id nativo y no hay de dónde.",
                "pase col_id con el nombre real de la columna, o col_id=None para usar "
                "<SRC>-F<fila>.",
            )
        )
    valores = _texto(marco[col_id])
    vacio = valores.isna() | (valores == "")
    repetido = marco.duplicated(subset=["SRC", col_id], keep=False) & ~vacio
    n_vacios = vacio.groupby(src, sort=False).sum()
    n_repetidos = repetido.groupby(src, sort=False).sum()
    usable = (n_vacios == 0) & (n_repetidos == 0)
    mascara = src.map(usable).fillna(False).astype(bool).to_numpy()
    id_registro = (src + "-" + valores.fillna("")).where(mascara, fila)
    info: dict[str, dict[str, str]] = {}
    for f in fuentes:
        if bool(usable.get(f, False)):
            info[f] = {"regla": "col_id", "columna": col_id}
            continue
        motivos: list[str] = []
        if int(n_vacios.get(f, 0)):
            motivos.append(f"{int(n_vacios[f])} vacío(s)/ausente(s)")
        if int(n_repetidos.get(f, 0)):
            motivos.append(f"no es único por fila ({int(n_repetidos[f])} repetido(s))")
        info[f] = {
            "regla": "fila",
            "columna": col_id,
            "motivo": f"col_id='{col_id}' en la fuente '{f}': " + "; ".join(motivos),
        }
    return id_registro, info


def _claves_arrow(claves: np.ndarray | pd.Series) -> pa.Array:
    """Las claves como ``pyarrow`` (sin copiar si ya vienen en ``string[pyarrow]``)."""
    if isinstance(claves, pd.Series):
        arr = pa.Array.from_pandas(claves)
    else:
        arr = pa.array(np.asarray(claves, dtype=object), type=pa.large_string())
    if not pa.types.is_string(arr.type) and not pa.types.is_large_string(arr.type):
        arr = arr.cast(pa.large_string())
    return arr


def id_grupo_por_clave_minima(
    grupos: np.ndarray | pd.Series, claves: np.ndarray | pd.Series
) -> np.ndarray:
    """``ID_GRUPO`` entero determinista por contenido.

    Cada grupo (cualquier etiqueta: posición del motor, texto ``C<n>``…) se
    representa por su menor clave (``ID_REGISTRO``) en orden lexicográfico;
    el entero es la posición de esa clave entre las claves representantes
    ordenadas (denso desde 0).

    Vectorizado y medido a 2M filas (≈ 0,8 s): el orden de las cadenas lo
    da ``pyarrow.compute.sort_indices`` (en C, orden de bytes UTF-8, que
    coincide con el orden por puntos de código de Python); el mínimo por
    grupo se toma sobre ese rango entero con ``np.minimum.at`` (el rango
    conserva el orden, así que el mínimo del rango es el rango de la clave
    mínima); el id final es la posición del mínimo de cada grupo en orden.
    Un ``groupby().transform("min")`` sobre cadenas ``object`` hacía lo mismo
    en 38 s a 2M (comparaciones de Python): por eso no se usa.

    Args:
        grupos: etiqueta de grupo por fila.
        claves: ``ID_REGISTRO`` por fila (texto, sin nulos; único por fila).
            Una ``Series`` ``string[pyarrow]`` (lo que devuelve
            ``asignar_id_registro``) entra sin copia.

    Returns:
        ``np.ndarray`` de ``int64`` alineado con la entrada.

    Raises:
        ValueError: longitudes distintas o claves nulas.
    """
    etiquetas = np.asarray(grupos)
    if len(etiquetas) != len(claves):
        raise ValueError(
            mensaje_accionable(
                f"grupos tiene {len(etiquetas):,} filas y claves {len(claves):,}.",
                "el id de grupo se deriva de la clave de cada fila; sin alineación no hay regla.",
                "pase una clave (ID_REGISTRO) por fila, en el mismo orden que los grupos.",
            )
        )
    n = len(etiquetas)
    if not n:
        return np.empty(0, dtype=np.int64)
    textos = _claves_arrow(claves)
    if textos.null_count:
        raise ValueError(
            mensaje_accionable(
                f"{textos.null_count:,} clave(s) nula(s) en claves.",
                "ID_REGISTRO identifica cada fila; sin clave no hay orden ni id de grupo.",
                "construya las claves con asignar_id_registro (SRC y ORIGINAL_INDEX sin nulos).",
            )
        )
    orden = pc.sort_indices(textos).to_numpy()
    rango = np.empty(n, dtype=np.int64)
    rango[orden] = np.arange(n, dtype=np.int64)
    codigos_grupo, unicos = pd.factorize(etiquetas, sort=False)
    minimo_por_grupo = np.full(len(unicos), n, dtype=np.int64)
    np.minimum.at(minimo_por_grupo, codigos_grupo, rango)
    id_por_grupo = np.empty(len(unicos), dtype=np.int64)
    id_por_grupo[np.argsort(minimo_por_grupo, kind="stable")] = np.arange(
        len(unicos), dtype=np.int64
    )
    return id_por_grupo[codigos_grupo]
