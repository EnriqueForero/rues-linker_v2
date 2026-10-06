"""record_linkage.contrato — El estándar de salida, declarado una sola vez (F1.9).

Qué es
------
La descripción declarativa de cada tabla que deja una corrida (correlativa,
golden, enlaces, revision): nombre, tipo ``pyarrow``, significado, origen y
alias en español. De aquí salen los ``pa.Schema`` con los que se escriben los
parquet, el ``diccionario.csv`` que acompaña a toda salida y la lista contra
la que ``ResultadoLinkage.validar()`` compara.

Decisiones (plan F1, no se discuten aquí)
-----------------------------------------
* Se conservan los nombres de columna de v1 (``SRC``, ``ORIGINAL_INDEX``,
  ``PRIMARY_SOURCE``…); el alias en español vive solo en ``alias_es`` y lo
  aplica ``leer_resultado(ruta, alias="es")``.
* La correlativa lleva primero las 12 columnas fijas, en ESE orden, y después
  TODAS las columnas de la fuente sin cambios. Las técnicas
  (``COLUMNAS_TECNICAS``) salen del entregable y quedan en ``_trabajo/``.
* El golden son las 13 columnas de v1 más ``ID_ENTIDAD``, con conteos enteros
  y ``REQUIRES_REVIEW`` booleano.
* ``METODO_UNION`` en F1 solo toma ``identificador``, ``nombre`` y
  ``sin_pareja``; los demás valores del vocabulario quedan reservados para
  F2/F3 (``nombre+contacto``, ``decision_humana``, ``decision_asistida
  (validar)``).
* ``golden/columnas_finales.py`` importa sus cuatro columnas DESDE aquí (una
  regla escrita una vez); nunca al revés, para no crear ciclos.
* ``entidades_ids`` (F1.10) es el crosswalk ``ID_ENTIDAD ↔ ID_GRUPO`` de la
  corrida que ``exporters/escritor.py`` deja en la carpeta; ``RETIRADO_EN``
  queda vacío hasta que F2.4 añada la herencia entre corridas.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-10-06  ·  Version: 0.23.0
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd
import pyarrow as pa

__all__ = [
    "COLUMNAS_CORRELATIVA",
    "COLUMNAS_DIAGNOSTICO",
    "COLUMNAS_DICCIONARIO",
    "COLUMNAS_ENLACES",
    "COLUMNAS_ENTIDADES_IDS",
    "COLUMNAS_FINALES",
    "COLUMNAS_GOLDEN",
    "COLUMNAS_IDENTIDAD",
    "COLUMNAS_METRICAS_GOLDEN",
    "COLUMNAS_REVISION",
    "COLUMNAS_TECNICAS",
    "METODOS_UNION",
    "METODOS_UNION_F1",
    "NIVELES_CONFIANZA",
    "TABLAS",
    "VERSION_CONTRATO",
    "ColumnaContrato",
    "diccionario",
    "esquema_correlativa",
    "esquema_enlaces",
    "esquema_entidades_ids",
    "esquema_golden",
    "esquema_revision",
    "familia_tipo",
    "revision_vacia",
]

#: Versión del contrato. Cambia solo cuando cambia una columna fija, su tipo
#: o su orden; se escribe en ``manifest.json`` y en el diccionario.
VERSION_CONTRATO = "1.0"

#: Columnas que acompañan a toda tabla del diccionario, en este orden.
COLUMNAS_DICCIONARIO: tuple[str, ...] = (
    "tabla",
    "columna",
    "tipo",
    "significado",
    "origen",
    "alias_es",
)

#: Vocabulario de CONFIANZA. Es el único sitio donde se nombran los niveles:
#: ``golden.metricas.confianza_de_grupo`` los toma de aquí y ``resultado.validar``
#: rechaza cualquier otro valor.
NIVELES_CONFIANZA: tuple[str, ...] = ("ALTA", "MEDIA", "BAJA")

#: Una sola definición de CONFIANZA, repetida en el diccionario de cada tabla
#: que la lleva (correlativa, golden, enlaces). Cita por su nombre la función
#: que produce los datos —golden/metricas.py::confianza_de_grupo, la única
#: copia de la regla desde F2.12— con sus tres umbrales exactos; los umbrales
#: de la función y este texto se comprueban juntos en
#: ``tests/test_contrato_salida.py``.
_DEFINICION_CONFIANZA = (
    "ALTA · MEDIA · BAJA (contrato.NIVELES_CONFIANZA), una sola definición: la regla "
    "record_linkage.golden.metricas.confianza_de_grupo, que produce los datos en los "
    "cinco caminos (linkage, link, dedupe, cruce e importadores; los enlaces de "
    "vinculación la citan igual). ALTA si el grupo tiene un solo identificador "
    "(NIT_VARIATIONS = 1) confirmado por dos o más fuentes (SOURCES_COUNT >= 2); si no, "
    "MEDIA si tiene a lo sumo dos identificadores (NIT_VARIATIONS <= 2) y es pequeño "
    "(RECORD_COUNT <= 5); BAJA en el resto. Un registro solo sin identificador queda en "
    "MEDIA; una base sin identificador y de una sola fuente (importadores) solo distingue "
    "por tamaño del grupo (MEDIA hasta 5 filas, BAJA después). F2.12 unifica la regla "
    "entre caminos."
)


@dataclass(frozen=True)
class ColumnaContrato:
    """Una columna del estándar: qué es, de dónde sale y cómo se llama en español.

    Attributes:
        nombre: nombre de v1 (el que llevan los archivos).
        tipo: tipo ``pyarrow`` con el que se escribe.
        significado: qué contiene, para el diccionario.
        origen: ``motor`` (lo decide el pipeline), ``fuente`` (viene del
            insumo) o ``revision`` (lo escribió una persona).
        alias_es: nombre en español que ``leer_resultado(alias="es")`` aplica.
    """

    nombre: str
    tipo: pa.DataType
    significado: str
    origen: str
    alias_es: str

    def campo(self) -> pa.Field:
        return pa.field(
            self.nombre,
            self.tipo,
            metadata={
                "significado": self.significado,
                "origen": self.origen,
                "alias_es": self.alias_es,
            },
        )


def _c(
    nombre: str, tipo: pa.DataType, significado: str, alias_es: str, origen: str = "motor"
) -> ColumnaContrato:
    return ColumnaContrato(nombre, tipo, significado, origen, alias_es)


# ─────────────────────────────────────────────────────────────────────────────
# Correlativa: LA tabla. Una fila por registro de entrada.
# ─────────────────────────────────────────────────────────────────────────────

CORRELATIVA: tuple[ColumnaContrato, ...] = (
    _c(
        "ID_REGISTRO",
        pa.string(),
        "Identidad estable del registro: <SRC>-<id nativo> si la fuente trae una columna "
        "única por fila (linkage(col_id=...)); si no, <SRC>-F<ORIGINAL_INDEX>.",
        "ID_REGISTRO",
    ),
    _c("SRC", pa.string(), "Base de origen del registro (nombre de la fuente).", "FUENTE"),
    _c(
        "ORIGINAL_INDEX",
        pa.int64(),
        "Posición del registro en la consolidación de entrada (v1).",
        "FILA_ORIGEN",
    ),
    _c("ID_GRUPO", pa.int64(), "Grupo de esta corrida al que pertenece el registro.", "ID_GRUPO"),
    _c(
        "ID_ENTIDAD",
        pa.string(),
        "Identificador estable entre corridas: NIT-<base sin DV> si el grupo adopta un "
        "identificador válido; si no, ENT-<16 hex de SHA-256 de los ID_REGISTRO del grupo "
        "ordenados> (determinista por contenido).",
        "ID_ENTIDAD",
    ),
    _c("NIT_FINAL", pa.string(), "Identificador que el grupo adopta.", "NIT_FINAL"),
    _c(
        "RAZON_SOCIAL_FINAL",
        pa.string(),
        "Razón social que el grupo adopta.",
        "RAZON_SOCIAL_FINAL",
    ),
    _c(
        "NAME_SIMILARITY_SCORE",
        pa.float64(),
        "Parecido (0–1) del nombre del registro con el adoptado por el grupo.",
        "SIMILITUD_NOMBRE",
    ),
    _c(
        "NIT_DISTANCE",
        pa.int64(),
        "Distancia de edición del identificador del registro al adoptado.",
        "DISTANCIA_NIT",
    ),
    _c(
        "SCORE_PAR",
        pa.float64(),
        "Mayor puntaje (0–1) de L3_scoring/scored.db entre los pares que conectan el "
        "registro con otro de su grupo; nulo si no hay par puntuado (unión por "
        "identificador o sin pareja).",
        "SCORE_PAR",
    ),
    _c(
        "METODO_UNION",
        pa.string(),
        "Cómo quedó el registro en su grupo: identificador · nombre · nombre+contacto · "
        "decision_humana · decision_asistida (validar) · sin_pareja. En F1 solo se "
        "producen identificador (otro miembro del grupo comparte su base válida de "
        "NIT_FINAL), nombre (el resto, incluido quien aportó NIT_FINAL sin pareja de "
        "base) y sin_pareja (grupo de un registro).",
        "METODO_UNION",
    ),
    _c(
        "CONFIANZA",
        pa.string(),
        f"Confianza del grupo (la del golden). {_DEFINICION_CONFIANZA}",
        "CONFIANZA",
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Golden: una fila por entidad.
# ─────────────────────────────────────────────────────────────────────────────

GOLDEN: tuple[ColumnaContrato, ...] = (
    _c("ID_GRUPO", pa.int64(), "Grupo de esta corrida.", "ID_GRUPO"),
    _c("NIT_FINAL", pa.string(), "Identificador que el grupo adopta.", "NIT_FINAL"),
    _c(
        "RAZON_SOCIAL_FINAL", pa.string(), "Razón social que el grupo adopta.", "RAZON_SOCIAL_FINAL"
    ),
    _c(
        "PRIMARY_SOURCE",
        pa.string(),
        "Fuente de la que se tomó la identidad adoptada.",
        "FUENTE_PRINCIPAL",
    ),
    _c(
        "SOURCES_LIST",
        pa.string(),
        "Fuentes presentes en el grupo, ordenadas y separadas por '|'.",
        "LISTA_FUENTES",
    ),
    _c("SOURCES_COUNT", pa.int64(), "Número de fuentes distintas en el grupo.", "N_FUENTES"),
    _c("RECORD_COUNT", pa.int64(), "Número de registros del grupo.", "N_REGISTROS"),
    _c(
        "NAME_VARIATIONS",
        pa.int64(),
        "Razones sociales distintas dentro del grupo.",
        "VARIACIONES_NOMBRE",
    ),
    _c(
        "NIT_VARIATIONS",
        pa.int64(),
        "Identificadores distintos dentro del grupo.",
        "VARIACIONES_NIT",
    ),
    _c(
        "CONFIDENCE_SCORE",
        pa.float64(),
        "Puntaje de confianza (0–1) del grupo.",
        "PUNTAJE_CONFIANZA",
    ),
    _c("CONFIANZA", pa.string(), _DEFINICION_CONFIANZA, "CONFIANZA"),
    _c(
        "REQUIRES_REVIEW",
        pa.bool_(),
        "True si el grupo merece revisión humana.",
        "REQUIERE_REVISION",
    ),
    _c(
        "CREATED_AT",
        pa.string(),
        "Marca de tiempo de creación del golden (texto AAAA-MM-DD HH:MM:SS).",
        "CREADO_EN",
    ),
    _c(
        "ID_ENTIDAD",
        pa.string(),
        "Identificador estable entre corridas (misma regla que en la correlativa).",
        "ID_ENTIDAD",
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Enlaces: solo vinculación (F3). Entidad ancla → registro destino.
# ─────────────────────────────────────────────────────────────────────────────

ENLACES: tuple[ColumnaContrato, ...] = (
    _c("ID_GRUPO", pa.int64(), "Grupo ancla de esta corrida.", "ID_GRUPO"),
    _c("ID_ENTIDAD", pa.string(), "Entidad ancla (estable entre corridas).", "ID_ENTIDAD"),
    _c("FUENTE_DESTINO", pa.string(), "Base destino del enlace.", "FUENTE_DESTINO"),
    _c("ID_DESTINO", pa.string(), "ID_REGISTRO del registro destino.", "ID_DESTINO"),
    _c(
        "NOMBRE_DESTINO",
        pa.string(),
        "Nombre del registro destino, tal como viene.",
        "NOMBRE_DESTINO",
    ),
    _c("NIVEL", pa.string(), "EMPRESA · GRUPO · RELACION_PROPIEDAD.", "NIVEL"),
    _c("BANDA", pa.string(), "A · B · C (calidad del enlace).", "BANDA"),
    _c("METODO_UNION", pa.string(), "Mismo vocabulario que en la correlativa.", "METODO_UNION"),
    _c("CONFIANZA", pa.string(), _DEFINICION_CONFIANZA, "CONFIANZA"),
    _c("PUNTAJE", pa.float64(), "Puntaje (0–1) del enlace.", "PUNTAJE"),
    _c("MOTIVO", pa.string(), "Evidencia que sostiene el enlace, legible.", "MOTIVO"),
    _c(
        "ES_PRINCIPAL",
        pa.bool_(),
        "True si es el enlace principal de la entidad en esa fuente.",
        "ES_PRINCIPAL",
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Revision: la forma del archivo de decisiones (compatible con notebooks 09/10,
# donde la columna de autoría se llama ORIGEN_REVISION).
# ─────────────────────────────────────────────────────────────────────────────

REVISION: tuple[ColumnaContrato, ...] = (
    _c("TIPO", pa.string(), "cruce · duplicado: qué clase de par se decide.", "TIPO", "revision"),
    _c(
        "FUENTE",
        pa.string(),
        "Fuente destino del par (en duplicados, la misma base).",
        "FUENTE",
        "revision",
    ),
    _c("CLAVE_A", pa.string(), "ID_REGISTRO o clave natural del lado A.", "CLAVE_A", "revision"),
    _c("NOMBRE_A", pa.string(), "Nombre del lado A, para leer sin cruzar.", "NOMBRE_A", "revision"),
    _c("CLAVE_B", pa.string(), "ID_REGISTRO o clave natural del lado B.", "CLAVE_B", "revision"),
    _c("NOMBRE_B", pa.string(), "Nombre del lado B.", "NOMBRE_B", "revision"),
    _c(
        "DECISION",
        pa.string(),
        "misma_empresa · mismo_grupo · relacion_propiedad · distinta (cruce); "
        "misma_entidad · mismo_grupo · distinta (duplicado).",
        "DECISION",
        "revision",
    ),
    _c(
        "AUTOR",
        pa.string(),
        "Quién decidió (persona o 'asistida'). Alias de ORIGEN_REVISION en los notebooks.",
        "AUTOR",
        "revision",
    ),
    _c("RAZON", pa.string(), "Por qué, en una frase.", "RAZON", "revision"),
)

# ─────────────────────────────────────────────────────────────────────────────
# Entidades_ids: crosswalk ID_ENTIDAD ↔ ID_GRUPO de la corrida (F1.10). La
# herencia entre corridas (RETIRADO_EN → sobreviviente) la añade F2.4.
# ─────────────────────────────────────────────────────────────────────────────

ENTIDADES_IDS: tuple[ColumnaContrato, ...] = (
    _c(
        "ID_ENTIDAD",
        pa.string(),
        "Entidad estable (misma regla que en la correlativa).",
        "ID_ENTIDAD",
    ),
    _c("ID_GRUPO", pa.int64(), "Grupo de esta corrida al que corresponde la entidad.", "ID_GRUPO"),
    _c("N_REGISTROS", pa.int64(), "Registros de la correlativa en el grupo.", "N_REGISTROS"),
    _c(
        "RETIRADO_EN",
        pa.string(),
        "ID_ENTIDAD sobreviviente si esta entidad se fusionó con otra en una corrida "
        "posterior; vacío en la corrida que la crea (F2.4 lo llena).",
        "RETIRADO_EN",
    ),
)

TABLAS: dict[str, tuple[ColumnaContrato, ...]] = {
    "correlativa": CORRELATIVA,
    "golden": GOLDEN,
    "enlaces": ENLACES,
    "revision": REVISION,
    "entidades_ids": ENTIDADES_IDS,
}

COLUMNAS_CORRELATIVA: tuple[str, ...] = tuple(c.nombre for c in CORRELATIVA)
COLUMNAS_GOLDEN: tuple[str, ...] = tuple(c.nombre for c in GOLDEN)
COLUMNAS_ENLACES: tuple[str, ...] = tuple(c.nombre for c in ENLACES)
COLUMNAS_REVISION: tuple[str, ...] = tuple(c.nombre for c in REVISION)
COLUMNAS_ENTIDADES_IDS: tuple[str, ...] = tuple(c.nombre for c in ENTIDADES_IDS)

#: Métricas del golden que nunca pueden quedar vacías.
COLUMNAS_METRICAS_GOLDEN: tuple[str, ...] = (
    "SOURCES_COUNT",
    "RECORD_COUNT",
    "NAME_VARIATIONS",
    "NIT_VARIATIONS",
    "CONFIDENCE_SCORE",
)

#: Identidad que el grupo adopta (antes en golden/columnas_finales.py).
COLUMNAS_IDENTIDAD: tuple[str, ...] = ("NIT_FINAL", "RAZON_SOCIAL_FINAL")
#: Trazabilidad de cada fila frente a la identidad adoptada.
COLUMNAS_DIAGNOSTICO: tuple[str, ...] = ("NAME_SIMILARITY_SCORE", "NIT_DISTANCE")
#: La entrega mínima de v0.21.0; hoy un subconjunto de la correlativa.
COLUMNAS_FINALES: tuple[str, ...] = COLUMNAS_IDENTIDAD + COLUMNAS_DIAGNOSTICO

#: Columnas técnicas de L1 que salen del entregable y quedan en ``_trabajo/``.
#: Las seis primeras las produce el Orchestrator; ``PHONETIC_KEY2`` y
#: ``DV_ORIGEN`` las produce la ruta ``deduplicate_unified`` (dedupe) y son
#: de la misma naturaleza.
COLUMNAS_TECNICAS: tuple[str, ...] = (
    "NOMBRE_LIMPIO",
    "NOMBRE_BLOQUEO",
    "NIT_OK",
    "NIT_BASE",
    "NIT_VALID",
    "PHONETIC_KEY1",
    "PHONETIC_KEY2",
    "DV_ORIGEN",
)

#: Vocabulario completo de METODO_UNION y el subconjunto que F1 produce.
METODOS_UNION: tuple[str, ...] = (
    "identificador",
    "nombre",
    "nombre+contacto",
    "decision_humana",
    "decision_asistida (validar)",
    "sin_pareja",
)
METODOS_UNION_F1: tuple[str, ...] = ("identificador", "nombre", "sin_pareja")


# ─────────────────────────────────────────────────────────────────────────────
# Esquemas pyarrow
# ─────────────────────────────────────────────────────────────────────────────


def _esquema(columnas: Sequence[ColumnaContrato]) -> pa.Schema:
    return pa.schema([c.campo() for c in columnas], metadata={"contrato": VERSION_CONTRATO})


def esquema_correlativa(columnas_fuente: Sequence[str] | Mapping[str, pa.DataType]) -> pa.Schema:
    """Esquema de la correlativa: las 12 fijas y después las de la fuente.

    Args:
        columnas_fuente: nombres de las columnas de la fuente, en el orden en
            que deben quedar. Una secuencia de nombres se tipa como texto; un
            mapping ``nombre → pa.DataType`` conserva el tipo real.

    Raises:
        ValueError: si una columna de la fuente choca con una fija. El
            renombre ``<col>_FUENTE`` es responsabilidad de
            ``salida.completar`` y debe ocurrir antes de pedir el esquema.
    """
    tipos: dict[str, pa.DataType]
    if isinstance(columnas_fuente, Mapping):
        tipos = dict(columnas_fuente)
    else:
        tipos = {str(nombre): pa.string() for nombre in columnas_fuente}
    choques = [n for n in tipos if n in COLUMNAS_CORRELATIVA]
    if choques:
        raise ValueError(
            f"Qué pasó: las columnas de la fuente {choques} chocan con columnas fijas del "
            f"contrato. Por qué importa: una columna no puede significar dos cosas. "
            f"Qué hacer: renómbrelas <col>_FUENTE (salida.completar lo hace solo)."
        )
    campos = [c.campo() for c in CORRELATIVA]
    campos += [
        pa.field(nombre, tipo, metadata={"origen": "fuente"}) for nombre, tipo in tipos.items()
    ]
    return pa.schema(campos, metadata={"contrato": VERSION_CONTRATO})


def esquema_golden() -> pa.Schema:
    return _esquema(GOLDEN)


def esquema_enlaces() -> pa.Schema:
    return _esquema(ENLACES)


def esquema_revision() -> pa.Schema:
    return _esquema(REVISION)


def esquema_entidades_ids() -> pa.Schema:
    return _esquema(ENTIDADES_IDS)


def revision_vacia() -> pd.DataFrame:
    """DataFrame vacío con la forma del archivo de decisiones."""
    return esquema_revision().empty_table().to_pandas()


# ─────────────────────────────────────────────────────────────────────────────
# Familias de tipo (para validar contra dtypes de pandas sin atarse a una
# versión: en pandas 3 el texto es ``str``, en 2.x ``object``).
# ─────────────────────────────────────────────────────────────────────────────


def familia_tipo(tipo: pa.DataType) -> str:
    """``texto`` · ``entero`` · ``decimal`` · ``booleano`` · ``otro``."""
    if pa.types.is_string(tipo) or pa.types.is_large_string(tipo):
        return "texto"
    if pa.types.is_integer(tipo):
        return "entero"
    if pa.types.is_floating(tipo):
        return "decimal"
    if pa.types.is_boolean(tipo):
        return "booleano"
    return "otro"


#: Nombres de representación que el diccionario reduce a su tipo lógico: pandas 3
#: (`str`), `string[pyarrow]` y pandas 2 (`object`) producen `large_string` o
#: `string` según la versión; el contrato solo conoce `string`.
_TIPOS_LOGICOS: dict[str, str] = {"large_string": "string", "large_binary": "binary"}


def _tipo_de_serie(serie: pd.Series) -> str:
    """Nombre pyarrow del tipo LÓGICO de una Serie, para el diccionario."""
    try:
        nombre = str(pa.Array.from_pandas(serie.head(1000)).type)
    except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError):
        return str(serie.dtype)
    return _TIPOS_LOGICOS.get(nombre, nombre)


# ─────────────────────────────────────────────────────────────────────────────
# Diccionario
# ─────────────────────────────────────────────────────────────────────────────

_SIGNIFICADOS_MOTOR_EXTRA: dict[str, str] = {
    "REGIMEN_AUTO": "Ruta por la que pasó el registro en dedupe(): CON_NIT o SIN_NIT.",
    # dedupe() no renombra las columnas del usuario: las copia a las canónicas
    # y la fuente conserva las suyas (col_nit/col_name quedan como columnas
    # de la fuente, con su nombre). linkage()/link() sí renombran.
    "NIT": "Copia canónica de la columna de identificador del usuario (col_nit, p. ej. "
    "IDENT) que hace dedupe(); la columna original sigue en la correlativa.",
    "RAZON_SOCIAL": "Copia canónica de la columna de nombre del usuario (col_name, p. ej. "
    "NOMBRE) que hace dedupe(); la columna original sigue en la correlativa.",
    "INPUT_ROW_COUNT": "Filas de entrada del grupo contando los duplicados exactos colapsados "
    "(collapse_exact_duplicates=True).",
}


#: Qué parámetro de ``linkage()`` produce cada renombre canónico del motor.
PARAMETRO_CANONICO: dict[str, str] = {
    "NIT": "col_nit",
    "RAZON_SOCIAL": "col_name",
    "CIUDAD": "col_ciudad",
}


def diccionario(
    tablas: Mapping[str, pd.DataFrame | None],
    *,
    columnas_fuente: Collection[str] = (),
    renombres: Mapping[str, str] | None = None,
    renombres_canonicos: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Diccionario de datos: tabla · columna · tipo · significado · origen · alias_es.

    Cubre las columnas del contrato de cada tabla presente y, además, toda
    columna real del DataFrame que no esté en el contrato: las de la fuente
    (``origen = fuente``) y las extra del motor (``origen = motor``), para
    que ninguna columna entregada quede sin explicar.

    Args:
        tablas: ``{"correlativa": df, "golden": df | None, ...}``. Una tabla
            ``None`` no se lista.
        columnas_fuente: nombres de las columnas que vienen del insumo (ya
            renombradas si hubo colisión).
        renombres: ``{nombre_en_fuente: nombre_en_salida}`` de las colisiones
            resueltas, para explicarlas.
        renombres_canonicos: ``{columna_del_usuario: columna_canónica}`` que
            el motor aplicó en la ingesta (``col_name="NOMBRE"`` →
            ``RAZON_SOCIAL``), para que el diccionario no diga «sin cambios»
            de una columna que cambió de nombre.
    """
    renombres = dict(renombres or {})
    invertido = {v: k for k, v in renombres.items()}
    canonico_invertido = {v: k for k, v in dict(renombres_canonicos or {}).items()}
    fuente = set(columnas_fuente)
    filas: list[dict[str, Any]] = []
    for tabla, columnas in TABLAS.items():
        df = tablas.get(tabla)
        if df is None:
            continue
        contrato_por_nombre = {c.nombre: c for c in columnas}
        for nombre in df.columns:
            col = contrato_por_nombre.get(str(nombre))
            if col is not None:
                filas.append(
                    {
                        "tabla": tabla,
                        "columna": col.nombre,
                        "tipo": str(col.tipo),
                        "significado": col.significado,
                        "origen": col.origen,
                        "alias_es": col.alias_es,
                    }
                )
                continue
            nombre_s = str(nombre)
            if nombre_s in invertido:
                significado = (
                    f"Columna '{invertido[nombre_s]}' de la fuente, renombrada por colisión "
                    f"con la columna fija del contrato del mismo nombre."
                )
                origen = "fuente"
            elif nombre_s in canonico_invertido:
                parametro = PARAMETRO_CANONICO.get(nombre_s, "columna canónica")
                significado = (
                    f"Columna '{canonico_invertido[nombre_s]}' de la fuente, renombrada a la "
                    f"canónica {nombre_s} por el motor ({parametro}='{canonico_invertido[nombre_s]}')."
                )
                origen = "fuente"
            elif nombre_s in fuente:
                significado = "Columna de la fuente, sin cambios."
                origen = "fuente"
            else:
                significado = _SIGNIFICADOS_MOTOR_EXTRA.get(
                    nombre_s, "Columna del motor fuera del contrato (ver _trabajo/)."
                )
                origen = "motor"
            filas.append(
                {
                    "tabla": tabla,
                    "columna": nombre_s,
                    "tipo": _tipo_de_serie(df[nombre]),
                    "significado": significado,
                    "origen": origen,
                    "alias_es": nombre_s,
                }
            )
        # Columnas del contrato que la tabla todavía no trae (p. ej. un golden
        # sin ID_ENTIDAD antes de completar) se listan igual: el diccionario
        # describe el contrato, no la foto.
        presentes = {str(n) for n in df.columns}
        for col in columnas:
            if col.nombre not in presentes:
                filas.append(
                    {
                        "tabla": tabla,
                        "columna": col.nombre,
                        "tipo": str(col.tipo),
                        "significado": col.significado,
                        "origen": col.origen,
                        "alias_es": col.alias_es,
                    }
                )
    return pd.DataFrame(filas, columns=list(COLUMNAS_DICCIONARIO))
