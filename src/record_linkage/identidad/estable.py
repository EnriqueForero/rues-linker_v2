"""record_linkage.identidad.estable — ``ID_ENTIDAD`` estable entre corridas (F2.4, ADR-0012).

Qué hace
--------
Asigna el identificador público de cada grupo de la correlativa y mantiene el
crosswalk ``entidades_ids.parquet`` que lo hace estable de una corrida a la
siguiente (regla del estándar, sección «Identificador de entidad estable»):

* ``NIT-<base canónica>`` para los grupos que adoptan un identificador válido:
  es natural, legible y estable por sí mismo. No se hereda ni se retira.
* ``ENT-…`` para el resto. Sin corrida anterior es el id por contenido de F1:
  ``ENT-<16 hex de SHA-256 de los ID_REGISTRO del grupo ordenados>`` (misma
  entrada → mismos ids, sin estado). Con ``corrida_anterior=`` cada grupo
  hereda el ``ENT-…`` de la entidad anterior con la que comparte más
  ``ID_REGISTRO``; el que no hereda recibe el id por contenido, que es nuevo.
* El crosswalk de la corrida anterior trae, por entidad, sus ``ID_REGISTRO``
  (columna ``REGISTROS``, ordenados y separados por ``|``): es lo único que
  hace falta para medir el solapamiento, así que un solo archivo basta para
  heredar; no se vuelve a leer la correlativa anterior.

La regla de herencia, en el orden en que se aplica (todo vectorizado con
``merge``/``groupby``; ningún bucle por fila ni por par):

1. **Destino** de cada entidad ``ENT-…`` activa de la corrida anterior: el
   grupo de esta corrida con el que comparte más ``ID_REGISTRO`` (empate: un
   grupo que PUEDE heredar —sin identificador— antes que uno ``NIT-…``, que
   nunca hereda y solo retiraría el id; luego el grupo actual con más
   registros, luego el de menor ``ID_REGISTRO``).
2. **Herencia**: un grupo sin identificador hereda, entre las entidades
   anteriores cuyo destino es él, la que más registros comparte (empate: la
   entidad anterior más grande, luego el id menor). Así una **fusión** deja
   vivo el id de la anterior mayor, y en una **división** conserva el id la
   parte con más registros compartidos; la otra parte recibe id nuevo.
3. **Retiro**: una entidad anterior cuyo destino terminó con otro id (porque
   perdió la herencia frente a otra mayor, o porque el grupo adoptó un
   identificador y ahora es ``NIT-…``) queda en el crosswalk con
   ``RETIRADO_EN = <id sobreviviente>``, ``N_REGISTROS = 0`` y el ``ID_GRUPO``
   del sobreviviente. Un ``NIT-…`` nunca se retira: si el identificador
   desaparece de la fuente, el grupo recibe un ``ENT-…`` y el reporte cuenta
   el ``NIT-…`` ausente; si vuelve, vuelve el mismo ``NIT-…``.
4. **Arrastre**: los retiros de corridas anteriores se copian al crosswalk
   nuevo y las cadenas se comprimen (``X → Y`` y luego ``Y → Z`` queda
   ``X → Z``): un consumidor resuelve cualquier id que se haya publicado con
   un solo salto. Una entidad anterior sin ningún registro en esta corrida no
   tiene sobreviviente: desaparece del crosswalk y el reporte la cuenta
   (``desaparecidas``); los retiros que apuntaban a ella (o a un ``NIT-…``
   ausente en esta corrida) se dejan de arrastrar y se cuentan en
   ``arrastres_sin_sobreviviente``, de modo que todo ``RETIRADO_EN`` del
   crosswalk publicado es un ``ID_ENTIDAD`` activo del mismo crosswalk.
5. **Un id retirado no resucita mientras siga en el crosswalk.** El id por
   contenido de un grupo nuevo se compara con todos los ids del crosswalk
   anterior (activos y retirados) y con los de esta corrida; si choca (el
   mismo conjunto de registros volvió a formar grupo después de haberse
   fusionado), se rehace con un sufijo determinista (``<clave>#1``, ``#2``…)
   y el reporte lo cuenta. Sin esto, un id que el crosswalk redirige a otro
   volvería a aparecer activo. La garantía alcanza hasta donde llega la
   memoria del crosswalk: un id que salió de él (``desaparecidas`` o
   ``arrastres_sin_sobreviviente`` del paso 4) ya no redirige a nada, y si en
   una corrida posterior vuelve exactamente su contenido, vuelve su hash. Es
   la consecuencia de no arrastrar retiros sin sobreviviente (paso 4); la
   alternativa —conservarlos como lápidas— arrastraría filas para siempre.

Antes de L1, :func:`exigir_fuentes_sin_separador` rechaza (desde
``linkage()``/``dedupe()``/``link()``) un nombre de fuente o un valor de
``col_id`` que contenga ``|``: ``ID_REGISTRO = <SRC>-<id nativo>`` no cabría
en ``REGISTROS`` y la corrida entera se perdería al cierre.

Lo que decide el plan y por qué no es ULID: el plan pedía ``ENT-<ulid>``; la
nota del coordinador conserva el id por contenido para que una corrida sin
estado siga siendo determinista (dos corridas de la misma entrada dan los
mismos ids sin necesidad de un crosswalk). Un ULID aportaría orden temporal
pero rompería esa propiedad y añadiría una dependencia; con el crosswalk, la
estabilidad entre corridas ya no depende de cómo se genere el id nuevo.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-10-06  ·  Version: 0.23.0
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .. import contrato
from ..pipeline.errores import CorridaAnteriorError, IdRegistroInvalidoError, mensaje_accionable

__all__ = [
    "COLUMNA_REGISTROS",
    "NOMBRE_CROSSWALK",
    "PREFIJO_ENT",
    "PREFIJO_NIT",
    "SEPARADOR_REGISTROS",
    "CorridaAnterior",
    "ResultadoIdentidad",
    "asignar_id_entidad",
    "cargar_corrida_anterior",
    "crosswalk_de_corrida",
    "exigir_fuentes_sin_separador",
    "localizar_crosswalk",
]

#: Nombre del crosswalk dentro de la carpeta del estándar.
NOMBRE_CROSSWALK = "entidades_ids.parquet"
#: Columna del crosswalk con los ``ID_REGISTRO`` de la entidad (ordenados).
COLUMNA_REGISTROS = "REGISTROS"
#: Separador de ``REGISTROS``. ``ID_REGISTRO`` no puede contenerlo (el id
#: nativo de ``col_id`` y el nombre de la fuente los pone el usuario):
#: :func:`exigir_fuentes_sin_separador` lo rechaza ANTES de L1 y
#: :func:`_claves_por_grupo` lo vuelve a exigir antes de escribir un crosswalk
#: que la corrida siguiente partiría mal. Las dos veces con
#: ``IdRegistroInvalidoError``: es un dato de entrada, no un defecto del motor.
SEPARADOR_REGISTROS = "|"
PREFIJO_NIT = "NIT-"
PREFIJO_ENT = "ENT-"

#: Hex del SHA-256 que entra al ``ENT-…`` (igual que en F1).
_LARGO_HASH = 16
_BYTES_POR_LECTURA = 1 << 20
#: Cuántos valores cita un mensaje de error (el conteo va aparte).
_EJEMPLOS_EN_ERROR = 3

#: Columnas mínimas del crosswalk anterior para poder heredar.
_COLUMNAS_NECESARIAS: tuple[str, ...] = (
    "ID_ENTIDAD",
    "N_REGISTROS",
    "RETIRADO_EN",
    COLUMNA_REGISTROS,
)


@dataclass(frozen=True)
class CorridaAnterior:
    """El crosswalk ``entidades_ids.parquet`` de la corrida previa, ya validado.

    Attributes:
        ruta: el archivo leído (no la carpeta), tal como irá al manifiesto.
        huella_sha256: SHA-256 del archivo: la huella que el manifiesto
            declara como «corrida anterior usada».
        crosswalk: la tabla con ``ID_ENTIDAD``, ``ID_GRUPO``, ``N_REGISTROS``,
            ``RETIRADO_EN`` y ``REGISTROS`` en tipos de pandas (texto en
            ``string``; ``RETIRADO_EN`` y ``REGISTROS`` nulos donde no aplican).
    """

    ruta: Path
    huella_sha256: str
    crosswalk: pd.DataFrame = field(repr=False)


@dataclass
class ResultadoIdentidad:
    """Lo que :func:`asignar_id_entidad` devuelve.

    Attributes:
        id_entidad: ``ID_ENTIDAD`` por fila de la correlativa (mismo índice).
        entidades_ids: el crosswalk de ESTA corrida con la forma de
            ``contrato.ENTIDADES_IDS``: una fila por grupo (activas) más las
            entidades retiradas (``RETIRADO_EN`` no nulo, ``N_REGISTROS`` 0).
        reporte: lo que va al manifiesto (``completar.id_entidad.herencia``):
            ruta y huella de la corrida anterior, cuántas entidades se
            heredaron, cuántas son nuevas, retiradas, pasadas a ``NIT-…``,
            desaparecidas, retiros anteriores que se dejaron de arrastrar por
            no tener sobreviviente, ``NIT-…`` anteriores ausentes y colisiones
            resueltas;
            sin corrida anterior, el motivo.
    """

    id_entidad: pd.Series
    entidades_ids: pd.DataFrame
    reporte: dict[str, Any]


# ─────────────────────────────────────────────────────────────────────────────
# Carga de la corrida anterior
# ─────────────────────────────────────────────────────────────────────────────


def _que_hacer_sin_corrida() -> str:
    return (
        f"pase la carpeta del estándar de la corrida anterior (la que contiene "
        f"{NOMBRE_CROSSWALK}) o ese archivo; si no hay corrida anterior, omita "
        "corrida_anterior=: los ENT-… serán nuevos y deterministas por contenido."
    )


def localizar_crosswalk(ruta: Path | str) -> Path:
    """Resuelve ``corrida_anterior`` (carpeta del estándar o archivo) al parquet.

    Falla con :class:`CorridaAnteriorError` si la ruta no existe o la carpeta
    no contiene ``entidades_ids.parquet``. Es lo que ``ConfigCruce`` y
    ``linkage()`` llaman ANTES de L1 para no descubrirlo tras la corrida.
    """
    ruta = Path(ruta)
    if ruta.is_dir():
        candidato = ruta / NOMBRE_CROSSWALK
    elif ruta.is_file():
        candidato = ruta
    else:
        raise CorridaAnteriorError(
            mensaje_accionable(
                f"corrida_anterior={ruta} no existe.",
                "sin el crosswalk de la corrida anterior no hay de dónde heredar ID_ENTIDAD, "
                "y descubrirlo después de L1…L5 costaría la corrida entera.",
                _que_hacer_sin_corrida(),
            )
        )
    if not candidato.is_file():
        raise CorridaAnteriorError(
            mensaje_accionable(
                f"corrida_anterior={ruta} no contiene {NOMBRE_CROSSWALK}.",
                "ese archivo es el crosswalk ID_ENTIDAD ↔ registros de la corrida anterior; "
                "sin él no se puede medir el solapamiento entre grupos.",
                _que_hacer_sin_corrida(),
            )
        )
    return candidato


def _sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for trozo in iter(lambda: f.read(_BYTES_POR_LECTURA), b""):
            h.update(trozo)
    return h.hexdigest()


def _miembros(entidades: pd.DataFrame) -> pd.DataFrame:
    """Una fila por (``PREVIA``, ``ID_REGISTRO``): ``REGISTROS`` partido por ``|``.

    Es la única lectura de ``REGISTROS``: la usan la validación de la carga
    (ningún ``ID_REGISTRO`` en dos entidades activas) y la herencia.
    """
    miembros = (
        entidades.set_index("ID_ENTIDAD")[COLUMNA_REGISTROS]
        .dropna()
        .str.split(SEPARADOR_REGISTROS, regex=False)
        .explode()
        .rename("ID_REGISTRO")
        .reset_index()
        .rename(columns={"ID_ENTIDAD": "PREVIA"})
    )
    miembros["ID_REGISTRO"] = miembros["ID_REGISTRO"].astype(str)
    return miembros


def _exigir_registros_en_una_sola_entidad(crosswalk: pd.DataFrame, archivo: Path) -> None:
    """Falla si un ``ID_REGISTRO`` aparece en más de una entidad ACTIVA del crosswalk.

    ``linkage()`` nunca lo escribe así (un registro está en un solo grupo);
    pasa con un crosswalk editado a mano. Si se aceptara, ``_heredar``
    contaría ese registro dos veces en el solapamiento y podría cambiar qué
    entidad sobrevive, sin aviso.
    """
    registros = _miembros(crosswalk[crosswalk["RETIRADO_EN"].isna()])["ID_REGISTRO"]
    repetidos = registros[registros.duplicated(keep=False)].unique()
    if len(repetidos) == 0:
        return
    n = len(repetidos)
    ejemplos = ", ".join(repr(v) for v in repetidos[:_EJEMPLOS_EN_ERROR])
    raise CorridaAnteriorError(
        mensaje_accionable(
            f"{archivo}: {n} ID_REGISTRO aparecen en más de una entidad activa "
            f"(p. ej. {ejemplos}).",
            "la herencia mide cuántos ID_REGISTRO comparte cada grupo con cada entidad "
            "anterior; un registro en dos entidades se contaría doble y podría cambiar "
            "qué ID_ENTIDAD sobrevive.",
            "use el entidades_ids.parquet tal como lo escribió linkage(carpeta_salida=…); "
            "si lo editó, deje cada ID_REGISTRO en una sola entidad activa "
            f"(columna {COLUMNA_REGISTROS}).",
        )
    )


def cargar_corrida_anterior(ruta: Path | str) -> CorridaAnterior:
    """Lee y valida el crosswalk de la corrida anterior (carpeta o archivo).

    Exige ``ID_ENTIDAD`` único y no nulo, la columna ``REGISTROS`` (un
    crosswalk escrito antes de F2.4 no la trae y no sirve para heredar) y que
    ningún ``ID_REGISTRO`` esté en dos entidades activas (crosswalk editado a
    mano); el error lo dice y dice qué hacer. Es lo que ``ConfigCruce`` y
    ``linkage()`` llaman ANTES de L1.
    """
    archivo = localizar_crosswalk(ruta)
    columnas = set(pq.read_schema(archivo).names)
    faltan = [c for c in _COLUMNAS_NECESARIAS if c not in columnas]
    if faltan:
        raise CorridaAnteriorError(
            mensaje_accionable(
                f"{archivo} no trae las columnas {faltan}.",
                f"un crosswalk sin {COLUMNA_REGISTROS} no dice qué ID_REGISTRO tenía cada "
                "entidad (lo escribió una versión anterior a F2.4) y sin eso no hay "
                "solapamiento que medir.",
                "use la carpeta de una corrida escrita con esta versión o corra sin "
                "corrida_anterior= (los ENT-… serán nuevos, deterministas por contenido).",
            )
        )
    df = pd.read_parquet(archivo)
    crosswalk = pd.DataFrame(
        {
            "ID_ENTIDAD": df["ID_ENTIDAD"].astype("string"),
            "ID_GRUPO": (
                df["ID_GRUPO"].astype("Int64")
                if "ID_GRUPO" in df.columns
                else pd.Series(pd.NA, index=df.index, dtype="Int64")
            ),
            "N_REGISTROS": df["N_REGISTROS"].fillna(0).astype("int64"),
            "RETIRADO_EN": df["RETIRADO_EN"].astype("string"),
            COLUMNA_REGISTROS: df[COLUMNA_REGISTROS].astype("string"),
        }
    )
    ids = crosswalk["ID_ENTIDAD"]
    if ids.isna().any() or not ids.is_unique:
        raise CorridaAnteriorError(
            mensaje_accionable(
                f"{archivo} tiene ID_ENTIDAD nulos o repetidos.",
                "el crosswalk debe tener una fila por entidad; con repetidos la herencia "
                "sería ambigua.",
                "use el entidades_ids.parquet tal como lo escribió linkage(carpeta_salida=…); "
                "si lo editó a mano, deje una fila por ID_ENTIDAD.",
            )
        )
    _exigir_registros_en_una_sola_entidad(crosswalk, archivo)
    return CorridaAnterior(
        ruta=archivo, huella_sha256=_sha256_archivo(archivo), crosswalk=crosswalk
    )


# ─────────────────────────────────────────────────────────────────────────────
# Asignación
# ─────────────────────────────────────────────────────────────────────────────


def _sha16(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:_LARGO_HASH]


def _con_separador(valores: pd.Series) -> pd.Series:
    """Máscara booleana: qué valores (como texto) contienen ``SEPARADOR_REGISTROS``."""
    texto = valores.astype("string")
    return texto.str.contains(SEPARADOR_REGISTROS, regex=False).fillna(False).astype(bool)


def _error_separador(que_paso: str) -> IdRegistroInvalidoError:
    """El único texto del rechazo de ``|``: antes de L1 y en la guardia del módulo."""
    return IdRegistroInvalidoError(
        mensaje_accionable(
            que_paso,
            f"ID_REGISTRO = <SRC>-<id nativo> y el crosswalk entidades_ids.parquet une los "
            f"ID_REGISTRO de cada entidad con {SEPARADOR_REGISTROS!r} ({COLUMNA_REGISTROS}); "
            "la herencia de la corrida siguiente los partiría y esos registros nunca "
            "contarían como compartidos.",
            f"pase en col_id= otra columna única por fila sin {SEPARADOR_REGISTROS!r}, "
            "limpie esa columna en la fuente, o use col_id=None (ID_REGISTRO = "
            f"<SRC>-F<ORIGINAL_INDEX>); un nombre de fuente tampoco puede llevar "
            f"{SEPARADOR_REGISTROS!r}.",
        )
    )


def _ejemplos(valores: pd.Series) -> str:
    return ", ".join(repr(v) for v in valores.astype("string").head(_EJEMPLOS_EN_ERROR))


def exigir_fuentes_sin_separador(fuentes: Mapping[str, pd.DataFrame], col_id: str | None) -> None:
    """Falla ANTES de L1 si un nombre de fuente o un valor de ``col_id`` contiene ``|``.

    Lo llaman ``linkage()`` (y ``link()`` a través de él) y ``dedupe()`` con
    las fuentes ya materializadas, antes de construir el motor: es un dato de
    entrada y la corrida entera no debe perderse al cierre por él. Solo se mira
    ``col_id`` en las fuentes que traen esa columna (donde no está,
    ``salida.completar`` usa ``<SRC>-F<fila>`` y lo dice en el manifiesto).
    Vectorizado por fuente.
    """
    con_sep = [nombre for nombre in fuentes if SEPARADOR_REGISTROS in str(nombre)]
    if con_sep:
        lista = ", ".join(repr(n) for n in con_sep)
        raise _error_separador(
            f"el nombre de fuente {lista} contiene {SEPARADOR_REGISTROS!r} "
            f"(sources= / nombre_a= / nombre_b=)."
        )
    if col_id is None:
        return
    for nombre, df in fuentes.items():
        if col_id not in df.columns:
            continue
        mascara = _con_separador(df[col_id])
        n = int(mascara.sum())
        if n:
            raise _error_separador(
                f"col_id='{col_id}' en la fuente '{nombre}' tiene {n} valor(es) con "
                f"{SEPARADOR_REGISTROS!r} (p. ej. {_ejemplos(df.loc[mascara, col_id])})."
            )


def _exigir_sin_separador(id_registro: pd.Series) -> None:
    """Guardia del módulo: ningún ``ID_REGISTRO`` contiene ``|``.

    ``linkage()`` ya lo rechazó antes de L1 (:func:`exigir_fuentes_sin_separador`);
    aquí se repite para quien llame al módulo con una correlativa propia.
    """
    con_sep = _con_separador(id_registro)
    n = int(con_sep.sum())
    if n:
        raise _error_separador(
            f"{n} ID_REGISTRO contienen {SEPARADOR_REGISTROS!r} "
            f"(p. ej. {_ejemplos(id_registro[con_sep])})."
        )


def _claves_por_grupo(grupos: pd.DataFrame) -> pd.Series:
    """``ID_REGISTRO`` ordenados y unidos por ``|``, por ``ID_GRUPO`` (índice ordenado).

    Vectorizado con Arrow (``binary_join`` sobre una lista por grupo):
    ``groupby().agg("|".join)`` recorta un sub-DataFrame por grupo en Python y
    medido costaba 3,0 s a 139k filas / 50k grupos; esto, 0,1 s. Antes de unir
    exige que ningún ``ID_REGISTRO`` contenga el separador.
    """
    if grupos.empty:
        return pd.Series(dtype="string", index=pd.Index([], name="ID_GRUPO"))
    _exigir_sin_separador(grupos["ID_REGISTRO"])
    orden = grupos.sort_values(["ID_GRUPO", "ID_REGISTRO"], kind="stable")
    ids = orden["ID_GRUPO"].to_numpy()
    limites = np.flatnonzero(ids[1:] != ids[:-1]) + 1
    desplazamientos = np.concatenate(([0], limites, [len(ids)])).astype(np.int64)
    valores = pa.array(orden["ID_REGISTRO"].astype(str).to_numpy(dtype=object), type=pa.string())
    listas = pa.LargeListArray.from_arrays(pa.array(desplazamientos, type=pa.int64()), valores)
    unidas = pc.binary_join(listas, SEPARADOR_REGISTROS)
    return pd.Series(
        unidas.to_numpy(zero_copy_only=False),
        index=pd.Index(ids[desplazamientos[:-1]], name="ID_GRUPO"),
        dtype="string",
    )


def crosswalk_de_corrida(
    correl: pd.DataFrame,
    retirados: pd.DataFrame | None = None,
    claves: pd.Series | None = None,
) -> pd.DataFrame:
    """Crosswalk de esta corrida con la forma de ``contrato.ENTIDADES_IDS``.

    Args:
        correl: con ``ID_GRUPO``, ``ID_REGISTRO`` e ``ID_ENTIDAD``.
        retirados: entidades retiradas (``ID_ENTIDAD``, ``RETIRADO_EN``,
            ``ID_GRUPO`` del sobreviviente o nulo), o None.
        claves: ``REGISTROS`` por ``ID_GRUPO`` si ya se calcularon
            (:func:`asignar_id_entidad` las reutiliza); si no, se calculan.

    Una fila por grupo (ordenadas por ``ID_GRUPO``) con sus ``REGISTROS``;
    después las retiradas, ordenadas por ``ID_ENTIDAD``, con ``N_REGISTROS``
    0 y ``REGISTROS`` nulo.
    """
    if claves is None:
        claves = _claves_por_grupo(correl[["ID_GRUPO", "ID_REGISTRO"]])
    grupos = (
        correl[["ID_GRUPO", "ID_ENTIDAD"]]
        .groupby("ID_GRUPO", sort=True)
        .agg(ID_ENTIDAD=("ID_ENTIDAD", "first"), N_REGISTROS=("ID_ENTIDAD", "size"))
        .reset_index()
    )
    activos = pd.DataFrame(
        {
            "ID_ENTIDAD": grupos["ID_ENTIDAD"].astype("string"),
            "ID_GRUPO": grupos["ID_GRUPO"].astype("Int64"),
            "N_REGISTROS": grupos["N_REGISTROS"].astype("int64"),
            "RETIRADO_EN": pd.Series(pd.NA, index=grupos.index, dtype="string"),
            COLUMNA_REGISTROS: grupos["ID_GRUPO"].map(claves).astype("string"),
        }
    )
    if retirados is None or retirados.empty:
        return activos[list(contrato.COLUMNAS_ENTIDADES_IDS)]
    r = retirados.sort_values("ID_ENTIDAD", kind="stable").reset_index(drop=True)
    fuera = pd.DataFrame(
        {
            "ID_ENTIDAD": r["ID_ENTIDAD"].astype("string"),
            "ID_GRUPO": r["ID_GRUPO"].astype("Int64"),
            "N_REGISTROS": pd.Series(0, index=r.index, dtype="int64"),
            "RETIRADO_EN": r["RETIRADO_EN"].astype("string"),
            COLUMNA_REGISTROS: pd.Series(pd.NA, index=r.index, dtype="string"),
        }
    )
    todo = pd.concat([activos, fuera], ignore_index=True)
    return todo[list(contrato.COLUMNAS_ENTIDADES_IDS)]


def _regla() -> str:
    return (
        "NIT-<base sin DV> si el grupo adopta un identificador válido; si no, hereda el "
        "ENT-… de la entidad anterior con más ID_REGISTRO compartidos (fusión: sobrevive "
        "la mayor; división: la parte con más compartidos); sin herencia, "
        "ENT-<16 hex de SHA-256 de los ID_REGISTRO del grupo ordenados>"
    )


def asignar_id_entidad(
    correl: pd.DataFrame,
    base_grupo: pd.Series,
    grupo_valido: np.ndarray,
    corrida_anterior: CorridaAnterior | None = None,
) -> ResultadoIdentidad:
    """``ID_ENTIDAD`` por fila y crosswalk de la corrida, con herencia si hay anterior.

    Args:
        correl: con ``ID_GRUPO`` (final de la corrida) e ``ID_REGISTRO`` (texto, único).
        base_grupo: base canónica del identificador del grupo, por fila
            (nula donde el grupo no adopta identificador válido).
        grupo_valido: por fila, True si el grupo lleva ``NIT-<base>``.
        corrida_anterior: crosswalk previo ya cargado, o None.
    """
    valido = np.asarray(grupo_valido, dtype=bool)
    con_nit = pd.Series(PREFIJO_NIT, index=correl.index, dtype="string") + base_grupo.astype(
        "string"
    )
    claves_todas = _claves_por_grupo(correl[["ID_GRUPO", "ID_REGISTRO"]])
    grupos_sin = pd.Index(correl.loc[~valido, "ID_GRUPO"].unique()).sort_values()
    claves = claves_todas.loc[grupos_sin]
    contenido = (PREFIJO_ENT + claves.map(_sha16)).astype("string")

    retirados: pd.DataFrame | None = None
    if corrida_anterior is None:
        ent_por_grupo = contenido
        reporte: dict[str, Any] = {
            "corrida_anterior": None,
            "motivo": "sin corrida anterior: todos los ENT-… son nuevos, deterministas por "
            "contenido (SHA-256 de los ID_REGISTRO del grupo ordenados).",
            "regla": _regla(),
            "heredadas": 0,
            "nuevas": len(contenido),
            "retiradas": 0,
        }
    else:
        nit_por_grupo = (
            con_nit[valido].groupby(correl.loc[valido, "ID_GRUPO"], sort=True).first()
            if valido.any()
            else pd.Series(dtype="string", index=pd.Index([], name="ID_GRUPO"))
        )
        ent_por_grupo, retirados, reporte = _heredar(
            correl[["ID_GRUPO", "ID_REGISTRO"]], claves, contenido, nit_por_grupo, corrida_anterior
        )
    surrogado = correl["ID_GRUPO"].map(ent_por_grupo).astype("string")
    id_entidad = con_nit.where(valido, surrogado)
    con_ids = correl[["ID_GRUPO", "ID_REGISTRO"]].assign(ID_ENTIDAD=id_entidad)
    return ResultadoIdentidad(
        id_entidad=id_entidad,
        entidades_ids=crosswalk_de_corrida(con_ids, retirados, claves=claves_todas),
        reporte=reporte,
    )


def _heredar(
    actual: pd.DataFrame,
    claves: pd.Series,
    contenido: pd.Series,
    nit_por_grupo: pd.Series,
    anterior: CorridaAnterior,
) -> tuple[pd.Series, pd.DataFrame, dict[str, Any]]:
    """Herencia, retiros y arrastre (ver docstring del módulo, pasos 1–5).

    Returns:
        ``(ent_por_grupo, retirados, reporte)``: id ``ENT-…`` por grupo sin
        identificador (índice ``ID_GRUPO``), las entidades retiradas
        (``ID_ENTIDAD``, ``RETIRADO_EN``, ``ID_GRUPO``) y el reporte.
    """
    previo = anterior.crosswalk
    activas = previo[previo["RETIRADO_EN"].isna()]
    es_ent = activas["ID_ENTIDAD"].str.startswith(PREFIJO_ENT).fillna(False).to_numpy(dtype=bool)
    ent_prev = activas[es_ent]
    nit_prev = activas[~es_ent]
    retirados_prev = previo[previo["RETIRADO_EN"].notna()]

    # 1. Solapamiento entidad anterior × grupo actual, por ID_REGISTRO
    # (cargar_corrida_anterior ya garantizó un registro por entidad activa).
    cruce = actual.merge(_miembros(ent_prev), on="ID_REGISTRO", how="inner")
    solape = cruce.groupby(["ID_GRUPO", "PREVIA"], sort=False).size().rename("n").reset_index()
    tam_prev = ent_prev.set_index("ID_ENTIDAD")["N_REGISTROS"]
    tam_actual = actual.groupby("ID_GRUPO", sort=False).size()
    # El menor ID_REGISTRO por grupo sale del orden, no de ``agg("min")``: con
    # texto Arrow, ``min`` reduce grupo a grupo en Python (medido: 5 s a 139k).
    min_actual = (
        actual.sort_values(["ID_GRUPO", "ID_REGISTRO"], kind="stable")
        .drop_duplicates("ID_GRUPO")
        .set_index("ID_GRUPO")["ID_REGISTRO"]
    )
    solape["n_prev"] = solape["PREVIA"].map(tam_prev).fillna(0).astype("int64")
    solape["n_actual"] = solape["ID_GRUPO"].map(tam_actual).astype("int64")
    solape["min_reg"] = solape["ID_GRUPO"].map(min_actual).astype(str)
    solape["puede_heredar"] = solape["ID_GRUPO"].isin(contenido.index)

    # Destino de cada entidad anterior: el grupo con más compartidos (empate:
    # un grupo que puede heredar antes que un NIT-…, que solo retiraría el id;
    # luego el grupo mayor, luego el de menor ID_REGISTRO).
    destino = solape.sort_values(
        ["PREVIA", "n", "puede_heredar", "n_actual", "min_reg"],
        ascending=[True, False, False, False, True],
        kind="stable",
    ).drop_duplicates("PREVIA")

    # 2. Herencia: por grupo sin identificador, la entidad anterior con más
    # compartidos entre las que tienen su destino en él (empate: la anterior
    # mayor, luego el id menor).
    elegibles = destino[destino["ID_GRUPO"].isin(contenido.index)]
    heredado = (
        elegibles.sort_values(
            ["ID_GRUPO", "n", "n_prev", "PREVIA"],
            ascending=[True, False, False, True],
            kind="stable",
        )
        .drop_duplicates("ID_GRUPO")
        .set_index("ID_GRUPO")["PREVIA"]
        .astype("string")
    )

    # 5. Ids nuevos por contenido que no choquen con ningún id ya publicado.
    ocupados: set[str] = set(previo["ID_ENTIDAD"].dropna()) | set(heredado) | set(nit_por_grupo)
    nuevos = contenido.drop(index=heredado.index)
    colisiones = 0
    choque = nuevos.isin(ocupados)
    if bool(choque.any()):
        # Solo los grupos que chocan (en la práctica, cero o uno): un
        # conjunto de registros que volvió a formar grupo tras fusionarse.
        for grupo in nuevos.index[choque.to_numpy(dtype=bool)]:
            k = 1
            candidato = PREFIJO_ENT + _sha16(f"{claves.loc[grupo]}#{k}")
            while candidato in ocupados:
                k += 1
                candidato = PREFIJO_ENT + _sha16(f"{claves.loc[grupo]}#{k}")
            nuevos.loc[grupo] = candidato
            ocupados.add(candidato)
            colisiones += 1
    ent_por_grupo = pd.concat([heredado, nuevos.astype("string")]).sort_index()
    final_por_grupo = pd.concat([nit_por_grupo.astype("string"), ent_por_grupo]).sort_index()

    # 3. Retiros: entidad anterior cuyo destino quedó con otro id.
    final_destino = destino["ID_GRUPO"].map(final_por_grupo).astype("string")
    se_retira = (
        (final_destino != destino["PREVIA"].astype("string")).fillna(True).to_numpy(dtype=bool)
    )
    nuevos_retiros = pd.DataFrame(
        {
            "ID_ENTIDAD": destino.loc[se_retira, "PREVIA"].astype("string").to_numpy(),
            "RETIRADO_EN": final_destino[se_retira].to_numpy(),
            "ID_GRUPO": destino.loc[se_retira, "ID_GRUPO"].to_numpy(),
        }
    )
    nuevos_retiros["ID_GRUPO"] = nuevos_retiros["ID_GRUPO"].astype("Int64")

    # 4. Arrastre con compresión de cadenas: X → Y, Y → Z  ⇒  X → Z. Solo se
    # arrastra lo que apunta a un id activo de ESTA corrida: si el
    # sobreviviente desapareció (o era un NIT-… ausente), el retiro se queda
    # sin destino y se cuenta; así cualquier RETIRADO_EN publicado existe
    # como ID_ENTIDAD activo en el mismo crosswalk.
    redireccion = nuevos_retiros.set_index("ID_ENTIDAD")["RETIRADO_EN"]
    hacia = retirados_prev["RETIRADO_EN"].astype("string")
    hacia = hacia.map(redireccion).fillna(hacia).astype("string")
    grupo_de = pd.Series(final_por_grupo.index, index=final_por_grupo.to_numpy(dtype=object))
    con_sobreviviente = hacia.isin(grupo_de.index).to_numpy(dtype=bool)
    arrastrados = pd.DataFrame(
        {
            "ID_ENTIDAD": retirados_prev.loc[con_sobreviviente, "ID_ENTIDAD"]
            .astype("string")
            .to_numpy(),
            "RETIRADO_EN": hacia[con_sobreviviente].to_numpy(),
            "ID_GRUPO": pd.array(hacia[con_sobreviviente].map(grupo_de).to_numpy(), dtype="Int64"),
        }
    )
    arrastres_sin_sobreviviente = int((~con_sobreviviente).sum())
    retirados = pd.concat([nuevos_retiros, arrastrados], ignore_index=True)

    desaparecidas = int((~ent_prev["ID_ENTIDAD"].isin(destino["PREVIA"])).sum())
    nit_ausentes = int((~nit_prev["ID_ENTIDAD"].isin(set(nit_por_grupo))).sum())
    ent_a_nit = int(nuevos_retiros["RETIRADO_EN"].str.startswith(PREFIJO_NIT).fillna(False).sum())
    reporte: dict[str, Any] = {
        "corrida_anterior": str(anterior.ruta),
        "huella_sha256": anterior.huella_sha256,
        "regla": _regla(),
        "anteriores": {
            "ent_activas": len(ent_prev),
            "nit_activas": len(nit_prev),
            "retiradas": len(retirados_prev),
        },
        "heredadas": len(heredado),
        "nuevas": len(nuevos),
        "retiradas": len(nuevos_retiros),
        "ent_a_nit": ent_a_nit,
        "desaparecidas": desaparecidas,
        "arrastres_sin_sobreviviente": arrastres_sin_sobreviviente,
        "nit_anteriores_ausentes": nit_ausentes,
        "colisiones_resueltas": colisiones,
    }
    return ent_por_grupo, retirados, reporte
