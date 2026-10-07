"""flujo.importadores — deduplicar una base SIN identificador (v0.22.0).

Resuelve entidades cuando la única evidencia es el **nombre** y una variable
de bloqueo categórica —típicamente el **país**—: destinatarios de exportación,
padrones de compradores, listas de contrapartes. Es el régimen SIN_NIT llevado
a su extremo: no hay identificador que vetar ni que corroborar.

Diseño en una frase: **el país es bloqueo duro y el nombre decide**. Se
particiona por país canónico (dos registros de países distintos jamás se
comparan, ni siquiera se vuelven candidatos), y dentro de cada partición el
motor multicampo decide con ``matching.nombre_idf.SimilitudNombre``. Al final,
``engine.cobertura`` reparte cada componente conexa en estrellas para que la
correlativa pueda afirmar algo verificable sobre cada asignación.

Uso típico::

    from record_linkage.flujo import ConfigImportadores, deduplicar_importadores

    cfg = ConfigImportadores(col_razon_social="RAZON_SOCIAL", col_pais="PAIS")
    resultado = deduplicar_importadores(df, cfg, carpeta_salida="salidas")
    print(resultado.metricas.to_string(index=False))
    resultado.carpeta_salida  # <salidas>/<AAAA-MM-DD_HHMM>_importadores/

Todos los valores por defecto salen de una corrida medida sobre 211.949
destinatarios de exportación colombianos; ver ``docs/ANALISIS_IMPORTADORES.md``.

El estándar de salida (F2.6)
----------------------------
Desde F2.6 las tablas tienen la forma del contrato 1.0 (``contrato.py``) y la
carpeta se escribe con ``exporters.escritor.escribir_resultado`` (el único
escritor del estándar), igual que ``linkage()``:

* ``correlativa``: las 12 columnas fijas primero (``ID_REGISTRO``, ``SRC``,
  ``ORIGINAL_INDEX``, ``ID_GRUPO``, ``ID_ENTIDAD``, ``NIT_FINAL``,
  ``RAZON_SOCIAL_FINAL``, ``NAME_SIMILARITY_SCORE``, ``NIT_DISTANCE``,
  ``SCORE_PAR``, ``METODO_UNION``, ``CONFIANZA``), después TODAS las columnas
  de la fuente sin cambios y al final las propias de este flujo
  (``COLUMNAS_PROPIAS``: ``ID_IMPORTADOR``, ``ID_EMPRESA_GLOBAL``, ``PAIS_*``,
  ``SIM_AL_FINAL``…), que se conservan como columnas adicionales documentadas
  en ``diccionario.csv``. La partición (``ID_IMPORTADOR``) no cambia: el
  estándar se añade encima (ADR-0009 sigue igual).
* ``ID_GRUPO``: entero por orden de primera aparición en ``ORIGINAL_INDEX``
  (la misma regla que ``salida.completar`` aplica a las etiquetas de texto de
  ``dedupe``); uno por ``ID_IMPORTADOR``. ``ID_ENTIDAD``: la base no trae
  identificador, así que SIEMPRE es ``ENT-<16 hex de SHA-256 de los
  ID_REGISTRO del grupo ordenados>`` (``salida.completar._id_entidad``,
  determinista por contenido). ``NIT_FINAL`` y ``NIT_DISTANCE`` quedan nulos.
* ``METODO_UNION``: ``sin_pareja`` en grupos de un registro; ``nombre`` en el
  resto (el nombre es toda la evidencia). ``SCORE_PAR``: la similitud 0–1 del
  nombre normalizado del registro con el del líder de su estrella (la misma
  que ``SIM_AL_FINAL``); para el líder, la mejor arista que lo une a un
  miembro; ``1.0`` cuando otro registro del grupo comparte su nombre
  normalizado (unión por igualdad exacta); nulo en ``sin_pareja``.
* ``golden``: las 13 columnas de v1 más ``ID_ENTIDAD`` con la regla ÚNICA de
  ``golden.metricas`` (``metricas_de_grupo`` + ``metricas_de_calidad``; con
  una sola fuente y sin identificador, ``CONFIANZA`` es MEDIA hasta 5
  registros y BAJA después; F2.12 unifica la regla), después las propias
  (``ID_IMPORTADOR``, ``N_FILAS_ORIGEN``, ``N_VARIANTES_NOMBRE``,
  ``SIM_MINIMA`` y las sumas de ``cols_metricas`` con su nombre de entrada).
* ``revision``: la forma del archivo de decisiones (``contrato.REVISION``):
  un par ``(líder, miembro)`` por arista de estrella de cada grupo marcado
  (``grupo_grande`` o ``cohesion_baja``), con ``CLAVE_*`` = ``ID_REGISTRO``
  del primer registro de cada representante y ``RAZON`` con el motivo y la
  similitud. La tabla de grupos de v0.22 (con ``MOTIVO_REVISION``, incluidos
  los ``sin_nombre_utilizable``, que no forman par) sigue en
  ``revision_grupos``. ``paises`` sigue en memoria (es un agrupado de la
  correlativa; no es un artefacto del estándar).
* Una columna de la fuente que choca con una columna del contrato, de las
  propias o de las de trabajo del merge (``_COLUMNAS_INTERNAS``) se conserva
  renombrada ``<col>_FUENTE`` (``renombres_por_colision``; el sufijo es el de
  ``salida.completar``)
  y el renombre va al manifiesto y al diccionario; una columna DECLARADA en
  la configuración que choca detiene la corrida con mensaje accionable.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from .. import contrato
from ..config.auditoria import ParametrosMotor
from ..engine.cobertura import cobertura_estrella
from ..golden.metricas import metricas_de_calidad, metricas_de_grupo
from ..matching.campos import CampoSpec, EsquemaCampos, PoliticaFaltante, TipoCampo
from ..matching.genericos import (
    GENERICOS_ESTRUCTURALES,
    GENERICOS_GEOGRAFIA,
    GENERICOS_SECTOR,
    SUFIJOS_INTERNACIONALES,
)
from ..matching.idf import construir_idf
from ..matching.motor_bloqueo import BloqueoComponible, LSHTexto
from ..matching.motor_multicampo import clusters_desde_decisiones, evaluar_esquema
from ..matching.nombre_idf import SimilitudNombre, neutralizar_genericos
from ..matching.normalizadores import LOCALES, normalizar_nombre
from ..paises import (
    CATALOGO_PAISES,
    PATRONES_NO_PAIS,
    canonizar_pais,
    sugerir_alias_pais,
)
from ..pipeline.errores import ContratoSalidaError, mensaje_accionable
from ..processing.saneamiento import (
    PRELIMPIEZA_COMERCIO_EXTERIOR,
    ascii_mayusculas,
    sanear_texto,
    unir_iniciales,
)
from ..resultado import ResultadoLinkage
from ..salida.completar import (
    _SUFIJO_COLISION,
    _asignar_id_registro,
    _id_entidad,
    _recodificar_id_grupo,
)

if TYPE_CHECKING:  # pragma: no cover - solo para tipar sin importar el escritor
    from ..exporters.escritor import Manifiesto

__all__ = [
    "COLUMNAS_PROPIAS",
    "ConfigImportadores",
    "ResultadoImportadores",
    "construir_entregables",
    "deduplicar_importadores",
    "diccionario_importadores",
    "ejecutar",
    "escribir_importadores",
    "metricas",
    "muestra_para_revision",
    "preparar",
    "recall_del_bloqueo",
    "registrar_locale",
    "renombres_por_colision",
    "sensibilidad_umbral",
    "verificar_invariantes",
]

#: Caracteres esperables en una razón social. Lo que queda fuera delata
#: mojibake o entidades HTML mal decodificadas y penaliza esa grafía al elegir
#: el nombre final.
_ESPERADOS = r"[^A-Za-z0-9ÁÉÍÓÚÜÑáéíóúüñ \.,&()\-/'\"]"

#: Columnas que ``preparar`` añade a la base (país canónico y nombre normalizado).
_COLUMNAS_PREPARACION: tuple[str, ...] = (
    "PAIS_ISO3",
    "PAIS_FINAL",
    "PAIS_METODO",
    "PAIS_NORMALIZADO",
    "NOMBRE_NORM",
)

#: Columnas propias de este flujo que van en la correlativa DESPUÉS de las
#: fijas del contrato y de las de la fuente, y en el golden después de las
#: fijas. Se conservan como adicionales documentadas (``diccionario.csv``).
COLUMNAS_PROPIAS: tuple[str, ...] = (
    "ID_IMPORTADOR",
    "ID_EMPRESA_GLOBAL",
    "PAIS_FINAL",
    "PAIS_ISO3",
    "PAIS_METODO",
    "PAIS_NORMALIZADO",
    "SIM_AL_FINAL",
    "CAMBIO_NOMBRE",
    "CAMBIO_PAIS",
    "NOMBRE_NORM",
    "N_FILAS_ORIGEN",
    "N_VARIANTES_NOMBRE",
    "SIM_MINIMA",
)

#: Columnas de trabajo que ``construir_entregables`` trae de ``representantes``
#: al merge con la base y que NO salen en la correlativa: si la fuente trae una
#: con ese nombre, el merge la sufijaría ``_x``/``_y`` y el flujo seguiría con
#: la columna equivocada. Se reservan igual que las del contrato y las propias
#: (``renombres_por_colision``). El sufijo ``_FUENTE`` es el de
#: ``salida.completar._SUFIJO_COLISION`` (una sola convención).
_COLUMNAS_INTERNAS: tuple[str, ...] = ("ES_LIDER", "NOMBRE_NORM_FINAL")

#: Qué significa cada columna propia, para ``diccionario.csv``.
_SIGNIFICADOS_PROPIOS: dict[str, str] = {
    "ID_IMPORTADOR": "Importador por país (v0.22): <ISO3>-<grupo local> o SINNOMBRE-<n> para "
    "nombres sin contenido utilizable. Uno por ID_GRUPO; el país es bloqueo duro.",
    "ID_EMPRESA_GLOBAL": "Identificador que IGNORA el país: G<n> une solo coincidencias EXACTAS "
    "del nombre normalizado final entre importadores; sin emparejamiento difuso entre países.",
    "PAIS_FINAL": "País canónico del catálogo (paises.canonizar_pais); SIN CLASIFICAR: <grafía> "
    "si se aisló; zona franca marcada aparte.",
    "PAIS_ISO3": "Código ISO 3166-1 alfa-3 del país final (ZZF zona franca, ZZZ sin clasificar).",
    "PAIS_METODO": "Cómo se canonizó el país: catalogo_exacto · catalogo_laxo · zona_franca · "
    "sin_clasificar.",
    "PAIS_NORMALIZADO": "Grafía del país saneada (sin tildes, mayúsculas) con la que se buscó en "
    "el catálogo.",
    "SIM_AL_FINAL": "Similitud 0–1 del nombre normalizado del registro con el del líder de su "
    "estrella (igual a NAME_SIMILARITY_SCORE; nombre de v0.22 que lee el notebook 07).",
    "CAMBIO_NOMBRE": "True si RAZON_SOCIAL_FINAL difiere de la grafía original.",
    "CAMBIO_PAIS": "True si PAIS_FINAL difiere de la grafía original del país.",
    "NOMBRE_NORM": "Nombre normalizado (sin sufijos legales, saneado) que vio el comparador.",
    "N_FILAS_ORIGEN": "Filas de entrada del grupo (igual a RECORD_COUNT; nombre de v0.22).",
    "N_VARIANTES_NOMBRE": "Grafías originales distintas en el grupo (igual a NAME_VARIATIONS; "
    "nombre de v0.22).",
    "SIM_MINIMA": "Menor SIM_AL_FINAL del grupo: la arista más débil de la estrella.",
}

#: Matices de este flujo sobre columnas fijas del contrato (se AÑADEN al
#: significado del contrato, no lo sustituyen).
_MATICES_CONTRATO: dict[str, str] = {
    "ID_GRUPO": "En importadores: entero por orden de primera aparición en ORIGINAL_INDEX, "
    "uno por ID_IMPORTADOR.",
    "ID_ENTIDAD": "En importadores la base no trae identificador: siempre ENT-….",
    "NIT_FINAL": "En importadores siempre vacío: la base no trae identificador.",
    "NIT_DISTANCE": "En importadores siempre vacío: la base no trae identificador.",
    "SCORE_PAR": "En importadores: similitud 0–1 del nombre con el líder de la estrella "
    "(SIM_AL_FINAL); para el líder, la mejor arista que lo une a un miembro; 1.0 si otro "
    "registro del grupo comparte su nombre normalizado; nulo en sin_pareja.",
    "METODO_UNION": "En importadores solo nombre (el nombre es toda la evidencia) y sin_pareja.",
}


#: Modos admitidos para ``ConfigImportadores.paises_sin_clasificar``.
MODOS_SIN_CLASIFICAR: frozenset[str] = frozenset({"detener", "aislar"})


class PaisesSinClasificar(ValueError):
    """Hay grafías de país que el catálogo no reconoce y el modo es ``detener``.

    Se lanza en ``preparar()``, antes de cualquier emparejamiento, y también
    desde ``exigir_cobertura_paises()`` si el notebook quiere comprobarlo como
    paso propio. Lleva la tabla en ``.tabla`` para que quien la capture pueda
    mostrarla o exportarla sin volver a calcularla.

    Attributes:
        tabla: columnas ``valor``, ``n_filas``, ``sugerencia``, ``similitud``.
    """

    def __init__(self, tabla: pd.DataFrame, cfg: ConfigImportadores) -> None:
        self.tabla = tabla
        filas = int(tabla["n_filas"].sum())
        muestra = tabla.head(15)
        lineas = "\n".join(
            f"  {r.valor!r:<40} {r.n_filas:>8,} filas   ¿{r.sugerencia}? ({r.similitud:.2f})"
            for r in muestra.itertuples(index=False)
        )
        resto = "" if len(tabla) <= 15 else f"\n  … y {len(tabla) - 15} grafías más"
        super().__init__(
            f"Qué pasó: {len(tabla)} grafía(s) de '{cfg.col_pais}' no están en el "
            f"catálogo ({filas:,} filas):\n{lineas}{resto}\n"
            "Por qué importa: el país es el bloqueo duro. Sin catalogar, todas esas "
            "filas caerían en la misma partición y la misma razón social bajo dos "
            "grafías distintas se fusionaría en un solo importador (medido).\n"
            "Qué hacer, en este orden:\n"
            "  1. Si ES un país: añada la grafía como alias en catalogo_paises "
            "(notebook: GRAFIAS_ADICIONALES, celda 4). La sugerencia es una AYUDA, "
            "no una regla: GUINEA y GUINEA-BISSAU se parecen y son países distintos.\n"
            "  2. Si NO es un país (zona franca, depósito): declare un patrón en "
            "patrones_no_pais.\n"
            "  3. Si NO es un país y no tiene sentido catalogarla (OTROS, NO DEFINIDO, "
            "VARIOS): declárela en paises_aislar (notebook: NO_SON_PAISES, celda 4). "
            "Se aísla con su propio PAIS_FINAL y la corrida sigue; cualquier otra "
            "grafía nueva seguirá deteniéndose aquí."
        )


@dataclass
class ConfigImportadores:
    """Parámetros del flujo. Los defectos están medidos, no supuestos.

    Args:
        col_razon_social: columna del nombre en el DataFrame de entrada.
        col_pais: columna del país.
        cols_metricas: columnas numéricas que se suman por grupo.
        col_peso_economico: cuál de ellas manda al desempatar. "" = ninguna.
        nombre_fuente: nombre de la base en el estándar (``SRC`` de la
            correlativa y prefijo de ``ID_REGISTRO``; F2.6).
        col_id: columna de la fuente única por fila para ``ID_REGISTRO`` =
            ``<SRC>-<id nativo>``; "" = ninguna (``<SRC>-F<fila>``). Si trae
            vacíos o repetidos se cae a la fila y el manifiesto lo dice.
        umbral_nombre: **la perilla principal**, en escala 0–1. Medido sobre
            160 asignaciones revisadas a mano: 0,84 → precisión ponderada
            0,966; 0,88 → más precisión pero pierde ~7.000 empalmes correctos;
            0,80 → el tramo añadido cae a ~0,75 de precisión.
        alfa_idf: cuánto pesa la evidencia de TOKENS frente a la de CADENA.
        prefix_weight: peso del prefijo en Jaro-Winkler.
        max_diferencia_informativa: puerta 1 de la contención (ver
            :class:`~record_linkage.matching.nombre_idf.SimilitudNombre`).
        min_informativos_compartidos: puerta 2.
        fraccion_max_distintivo: puerta 3, como fracción del corpus (0,01 =
            1 % de los nombres). Relativa y no absoluta porque el IDF lo es.
        longitud_minima_token: mínimo para que un token sea informativo.
        ignorar_numericos: excluir tokens de solo dígitos (guías, códigos).
        peso_token_generico: peso al que se baja un genérico declarado.
        geografia_es_ruido: con ``True``, "ECOLAB" y "ECOLAB CHILE" se unen —
            y también "BARRY CALLEBAUT USA" con "BARRY CALLEBAUT CANADA", que
            son dos sociedades. Es el modo de error dominante de la banda
            0,92–0,96 (4 de 40 revisadas). Póngalo en ``False`` si necesita
            resolver a nivel de persona jurídica y no de grupo comercial.
        genericos_extra: términos propios de su dominio.
        sufijos_extra: formas legales adicionales a borrar del nombre.
        prelimpieza: pares (regex, sustituto) previos a todo.
        catalogo_paises: catálogo de países; amplíelo concatenando sus grafías.
        patrones_no_pais: expresiones de valores que no son un país (zonas
            francas). Vacío = desactivado.
        agrupar_no_pais: reunir esos valores bajo una etiqueta única.
        unir_iniciales_sueltas: "O.M.G" → "OMG".
        quitar_genericos_del_nombre: borrar genéricos del texto. Déjelo en
            ``False``: ponderar es estrictamente mejor que borrar.
        locale: clave con la que se registra el diccionario compuesto en
            ``matching.normalizadores.LOCALES``.
        lsh_permutaciones, lsh_umbral, lsh_ngram, lsh_max_grupo: bloqueo.
            Medido por fuerza bruta sobre particiones completas — recall del
            bloqueo (PC): 128@0,25 → 0,99–1,00; 64@0,30 → 0,97–0,99;
            64@0,35 → **0,67–0,81**, es decir pierde uno de cada cuatro pares
            verdaderos sin ningún síntoma visible.
        max_candidatos: presupuesto fail-fast de pares.
        pares_por_lote: cuántos candidatos se puntúan de una vez (0.22.4).
            Acota la memoria transitoria sin cambiar el resultado.
        peso_nombre, peso_pais: pesos del esquema multicampo.
        refinar_cohesion: aplicar la cobertura por estrellas.
        regla_nombre_final: ``"cobertura"`` o ``"masa"``.
        paises_auditoria_bloqueo: ISO3 sobre los que medir el recall del
            bloqueo por fuerza bruta. Cuidado: el costo es O(k²).
        sim_minima_auditoria: piso de similitud de nombre para conservar un
            par en ``decisiones`` (0.22.4); fusionados y vetados se conservan
            siempre. ``sensibilidad_umbral`` no acepta umbrales por debajo.
        muestra_revision_por_banda: tamaño de la muestra estratificada.
        semilla: reproducibilidad del muestreo.
    """

    # ── Contrato de entrada ─────────────────────────────────────────────
    col_razon_social: str = "RAZON_SOCIAL"
    col_pais: str = "PAIS"
    cols_metricas: tuple[str, ...] = ()
    col_peso_economico: str = ""
    #: ``SRC`` del estándar (F2.6). Una sola fuente por corrida.
    nombre_fuente: str = "IMPORTADORES"
    #: Columna de la fuente única por fila para ``ID_REGISTRO``; "" = por fila.
    col_id: str = ""

    # ── Decisión ────────────────────────────────────────────────────────
    umbral_nombre: float = 0.84
    alfa_idf: float = 0.25
    prefix_weight: float = 0.10

    # ── Contención ──────────────────────────────────────────────────────
    max_diferencia_informativa: int = 0
    min_informativos_compartidos: int = 1
    fraccion_max_distintivo: float = 0.01
    longitud_minima_token: int = 3
    ignorar_numericos: bool = True
    peso_token_generico: float = 1.0

    # ── Diccionarios ────────────────────────────────────────────────────
    geografia_es_ruido: bool = True
    genericos_extra: frozenset[str] = frozenset()
    sufijos_extra: frozenset[str] = SUFIJOS_INTERNACIONALES
    prelimpieza: tuple[tuple[str, str], ...] = PRELIMPIEZA_COMERCIO_EXTERIOR
    unir_iniciales_sueltas: bool = True
    quitar_genericos_del_nombre: bool = False
    locale: str = "INTL_IMPORTADORES"
    locales_base: tuple[str, ...] = ("ES", "EN")
    catalogo_paises: tuple[tuple[str, str, tuple[str, ...]], ...] = CATALOGO_PAISES
    patrones_no_pais: tuple[str, ...] = PATRONES_NO_PAIS
    agrupar_no_pais: bool = True
    #: Qué hacer con las grafías de país que el catálogo no reconoce.
    #:
    #: ``"detener"`` (defecto): ``preparar()`` se detiene ANTES del
    #: emparejamiento con la lista de grafías, cuántas filas trae cada una y el
    #: país del catálogo más parecido — que es exactamente lo que hace falta
    #: para ampliar ``catalogo_paises`` o declarar un patrón en
    #: ``patrones_no_pais``. Cuesta un mapeo vectorizado; la alternativa es
    #: enterarse minutos después por una invariante.
    #:
    #: ``"aislar"``: cada grafía sin clasificar conserva su propio
    #: ``PAIS_FINAL`` (``SIN CLASIFICAR: NO DEFINIDO``), así que dos grafías
    #: distintas nunca se fusionan entre sí ni con un país real, y la corrida
    #: sigue. Úselo cuando la grafía no es un país y no tiene sentido
    #: catalogarla (``NO DEFINIDO``, ``VARIOS``). Medido en 0.22.2: con la
    #: etiqueta única que había, la misma razón social bajo tres grafías sin
    #: clasificar se fusionaba en un solo ``ZZZ-000035``.
    paises_sin_clasificar: str = "detener"
    #: Grafías declaradas como "NO es un país y no tiene sentido catalogarlo"
    #: (``OTROS``, ``NO DEFINIDO``, ``VARIOS``). Se aíslan —cada una con su
    #: propio ``PAIS_FINAL``— aunque el modo sea ``"detener"``; cualquier OTRA
    #: grafía fuera del catálogo sigue deteniendo la corrida. Es la forma de
    #: aceptar lo conocido sin apagar la guardia para lo desconocido. Se
    #: comparan normalizadas (sin tildes, mayúsculas, espacios colapsados).
    paises_aislar: tuple[str, ...] = ()

    # ── Bloqueo ─────────────────────────────────────────────────────────
    lsh_permutaciones: int = 128
    lsh_umbral: float = 0.25
    lsh_ngram: int = 3
    lsh_max_grupo: int = 500
    max_candidatos: int = 60_000_000
    #: Pares candidatos que se puntúan de una vez dentro de una partición.
    #: El motor puntúa cada par por separado, así que el resultado es idéntico
    #: con cualquier tamaño de lote; lo que cambia es la memoria transitoria,
    #: que pasa de O(candidatos de la partición) a O(lote). Medido en 0.22.4
    #: sobre la partición USA de la base real (54.669 nombres, 24,5 M
    #: candidatos): de una sola vez, pico de 11,4 GiB y la corrida completa
    #: moría por OOM en un contenedor de 15 GiB; por lotes de 1 M, el pico
    #: queda acotado por el lote.
    pares_por_lote: int = 1_000_000

    # ── Esquema ─────────────────────────────────────────────────────────
    peso_nombre: float = 2.0
    peso_pais: float = 1.0

    # ── Postproceso ─────────────────────────────────────────────────────
    refinar_cohesion: bool = True
    regla_nombre_final: str = "cobertura"

    # ── Control de calidad ──────────────────────────────────────────────
    paises_auditoria_bloqueo: tuple[str, ...] = ()
    #: Piso de similitud de nombre (escala 0–1) para CONSERVAR un par candidato
    #: en ``decisiones``. Los pares fusionados y los vetados se conservan
    #: siempre; del resto, solo los que llegan al piso. Por debajo hay solo
    #: pares que el bloqueo propuso y el comparador descartó de lejos: en la
    #: base real son la inmensa mayoría de los ~50 M candidatos y a ~110 B por
    #: par eran ~5 GiB de tabla que nadie iba a mirar. ``sensibilidad_umbral``
    #: rechaza umbrales por debajo del piso porque ya no podría contarlos.
    #: Debe ser ≤ ``umbral_nombre``.
    sim_minima_auditoria: float = 0.70
    muestra_revision_por_banda: int = 40
    semilla: int = 42
    verboso: bool = True

    def __post_init__(self) -> None:
        if not 0.0 < self.umbral_nombre < 1.0:
            raise ValueError(f"umbral_nombre={self.umbral_nombre} fuera de (0, 1).")
        if not 0.0 <= self.alfa_idf <= 1.0:
            raise ValueError(f"alfa_idf={self.alfa_idf} fuera de [0, 1].")
        if self.regla_nombre_final not in {"cobertura", "masa"}:
            raise ValueError("regla_nombre_final debe ser 'cobertura' o 'masa'.")
        if self.paises_sin_clasificar not in MODOS_SIN_CLASIFICAR:
            raise ValueError(
                f"paises_sin_clasificar={self.paises_sin_clasificar!r} no es uno de "
                f"{sorted(MODOS_SIN_CLASIFICAR)}."
            )
        if self.peso_nombre <= 0 or self.peso_pais <= 0:
            raise ValueError("peso_nombre y peso_pais deben ser > 0.")
        if self.pares_por_lote < 1:
            raise ValueError(f"pares_por_lote={self.pares_por_lote} debe ser >= 1.")
        if not 0.0 <= self.sim_minima_auditoria <= self.umbral_nombre:
            raise ValueError(
                f"sim_minima_auditoria={self.sim_minima_auditoria} debe estar en "
                f"[0, umbral_nombre={self.umbral_nombre}]: por debajo del umbral se conservan "
                f"los pares que sirven para auditar la decisión; por encima no habría nada "
                f"que auditar."
            )
        if self.col_peso_economico and self.col_peso_economico not in self.cols_metricas:
            raise ValueError(
                f"Qué pasó: col_peso_economico='{self.col_peso_economico}' no está en "
                f"cols_metricas={list(self.cols_metricas)}. Por qué importa: se usa "
                f"para desempatar el nombre final y debe venir agregada. "
                f"Qué hacer: añádala a cols_metricas o déjela en ''."
            )
        if not str(self.nombre_fuente).strip() or "-" in self.nombre_fuente:
            raise ValueError(
                mensaje_accionable(
                    f"nombre_fuente={self.nombre_fuente!r} no sirve como SRC.",
                    "es el prefijo de ID_REGISTRO (<SRC>-<id>) y el guion lo haría ambiguo.",
                    "use un nombre no vacío sin guiones (p. ej. 'IMPORTADORES', 'DIAN').",
                )
            )

    @property
    def columnas_declaradas(self) -> tuple[str, ...]:
        """Las columnas de la fuente que la configuración nombra."""
        extra = (self.col_id,) if self.col_id else ()
        return (self.col_razon_social, self.col_pais, *self.cols_metricas, *extra)

    @property
    def sim_minima(self) -> float:
        """``umbral_nombre`` en la escala firmada [−1, 1] del motor."""
        return 2.0 * self.umbral_nombre - 1.0

    @property
    def umbral_score(self) -> float:
        """Umbral del score combinado, calculado para EQUIVALER al de nombre.

        Dentro de una partición el país siempre concuerda (similitud 1,0), así
        que fijar aquí el valor exacto hace que ``score ≥ umbral_score`` y
        ``similitud_nombre ≥ umbral_nombre`` sean la MISMA condición. Sin este
        cálculo el umbral de score sería un número decorativo que nadie podría
        interpretar.
        """
        return (self.peso_nombre * self.sim_minima + self.peso_pais) / (
            self.peso_nombre + self.peso_pais
        )

    @property
    def genericos_todos(self) -> frozenset[str]:
        """Términos cuyo IDF se neutraliza (ponderación)."""
        base = set(GENERICOS_ESTRUCTURALES) | set(GENERICOS_SECTOR) | set(GENERICOS_GEOGRAFIA)
        return frozenset(base | {str(t).strip().upper() for t in self.genericos_extra})

    @property
    def genericos_estructurales(self) -> frozenset[str]:
        """Términos que además cuentan como RUIDO al comparar dos nombres."""
        base = set(GENERICOS_ESTRUCTURALES)
        if self.geografia_es_ruido:
            base |= set(GENERICOS_GEOGRAFIA)
        return frozenset(base | {str(t).strip().upper() for t in self.genericos_extra})


@dataclass
class ResultadoImportadores:
    """Salida completa del flujo.

    Attributes:
        correlativa: una fila por fila de entrada, con la forma del contrato
            1.0 (las 12 fijas, la fuente completa y las propias del flujo).
            **Es el entregable.**
        golden: una fila por importador consolidado (13 de v1 + ``ID_ENTIDAD``
            y las propias).
        paises: correlativa de la canonización del país (en memoria; no es un
            artefacto del estándar).
        revision: pares por decidir con la forma del archivo de decisiones
            (``contrato.REVISION``): una arista (líder, miembro) por estrella
            de los grupos marcados.
        revision_grupos: la tabla de grupos de v0.22 (golden del grupo +
            ``MOTIVO_REVISION``), incluidos los ``sin_nombre_utilizable``.
        diccionario: tabla · columna · tipo · significado · origen · alias_es
            de las tablas del estándar, con las columnas propias explicadas.
        manifiesto: trazabilidad de la corrida (función, parámetros y su hash,
            huella del insumo, versiones, ``configuracion`` efectiva,
            reporte ``completar``); lo que ``escribir_resultado`` publica.
        carpeta_salida: la carpeta del estándar si se escribió.
        muestra: muestra estratificada lista para etiquetar a mano.
        metricas: indicadores de la corrida.
        invariantes: lo que siempre debe cumplirse, con su veredicto.
        decisiones: par a par, con score y veredicto (auditoría). Desde
            0.22.4 ``i`` y ``j`` son posiciones GLOBALES en ``representantes``
            y solo se conservan los pares fusionados, los vetados y los que
            alcanzan ``cfg.sim_minima_auditoria``; los nombres se resuelven con
            :meth:`decisiones_con_nombres` en vez de viajar por par.
        comparador: el comparador usado (para re-puntuar un par a mano).
        preparada: la tabla tras saneamiento y normalización.
        segundos, n_candidatos, n_cortes_cohesion: costos de la corrida.
    """

    correlativa: pd.DataFrame
    golden: pd.DataFrame
    paises: pd.DataFrame
    revision: pd.DataFrame
    muestra: pd.DataFrame
    metricas: pd.DataFrame
    invariantes: pd.DataFrame
    decisiones: pd.DataFrame
    comparador: SimilitudNombre
    preparada: pd.DataFrame
    representantes: pd.DataFrame
    sugerencias_pais: pd.DataFrame
    segundos: float = 0.0
    n_candidatos: int = 0
    n_cortes_cohesion: int = 0
    extra: dict = field(default_factory=dict)
    revision_grupos: pd.DataFrame = field(default_factory=pd.DataFrame)
    diccionario: pd.DataFrame = field(default_factory=pd.DataFrame)
    manifiesto: dict[str, Any] = field(default_factory=dict)
    carpeta_salida: Path | None = None

    @property
    def todo_ok(self) -> bool:
        """True si todas las invariantes pasan."""
        return bool(self.invariantes["cumple"].all())

    def tablas(self) -> dict[str, pd.DataFrame]:
        """Las tablas exportables, en orden de utilidad."""
        return {
            "CORRELATIVA": self.correlativa,
            "GOLDEN": self.golden,
            "PAISES": self.paises,
            "REVISION": self.revision,
            "REVISION_GRUPOS": self.revision_grupos,
            "MUESTRA_REVISION": self.muestra,
            "METRICAS": self.metricas,
            "INVARIANTES": self.invariantes,
            **{k: v for k, v in self.extra.items()},
        }

    def a_resultado_linkage(self) -> ResultadoLinkage:
        """El ``ResultadoLinkage`` del contrato 1.0 que ``escribir_resultado`` publica.

        El diccionario se construye aquí si no vino (``diccionario_importadores``);
        el manifiesto NO: lo arma ``deduplicar_importadores`` con la huella del
        insumo y sin él ``validar()`` no puede comprobar «una fila por registro
        de entrada» ni el escritor reproducir la corrida.

        Raises:
            ContratoSalidaError: si el resultado no trae manifiesto.
        """
        if not self.manifiesto:
            raise ContratoSalidaError(
                [
                    "el resultado no trae manifiesto (funcion, parametros, entradas…): "
                    "constrúyalo con deduplicar_importadores(), que lo arma con la huella "
                    "del insumo, o asigne resultado.manifiesto antes de escribir."
                ]
            )
        diccionario = self.diccionario
        if diccionario is None or diccionario.empty:
            diccionario = diccionario_importadores(
                self.correlativa,
                self.golden,
                self.revision,
                renombres=(self.manifiesto.get("completar") or {}).get("renombres"),
            )
        metricas_escalares: dict[str, Any] = {
            "n_registros": len(self.correlativa),
            "n_grupos": int(self.correlativa["ID_GRUPO"].nunique()),
            "n_fuentes": 1,
            "n_importadores": int(self.correlativa["ID_IMPORTADOR"].nunique()),
            "n_empresas_globales": int(self.correlativa["ID_EMPRESA_GLOBAL"].nunique()),
            "n_candidatos": int(self.n_candidatos),
            "n_cortes_cohesion": int(self.n_cortes_cohesion),
            "segundos_emparejamiento": float(self.segundos),
            "invariantes_ok": self.todo_ok,
        }
        return ResultadoLinkage(
            correlativa=self.correlativa,
            golden=self.golden,
            metricas=metricas_escalares,
            manifiesto=dict(self.manifiesto),
            enlaces=None,
            revision=self.revision,
            diccionario=diccionario,
            dir_trabajo=None,
        )

    def resumen(self) -> str:
        """Resumen de una línea por indicador, para imprimir."""
        return self.metricas.to_string(index=False)

    def decisiones_con_nombres(self) -> pd.DataFrame:
        """``decisiones`` con ``NOMBRE_A`` y ``NOMBRE_B`` resueltos desde ``representantes``.

        Hasta 0.22.3 los dos nombres viajaban en cada fila de ``decisiones``;
        con ~50 M candidatos en la base real eso era la mitad de una tabla
        de 5 GiB. Ahora se resuelven aquí, solo cuando alguien los pide.
        """
        return decisiones_con_nombres(self.decisiones, self.representantes)


def registrar_locale(cfg: ConfigImportadores) -> str:
    """Registra en ``LOCALES`` el diccionario compuesto del flujo.

    ``matching.normalizadores.LOCALES`` está declarado como extensible; esta
    función es el punto único donde el flujo lo extiende, para que el notebook
    no tenga que tocar un registro global por su cuenta. Es idempotente.
    """
    sufijos: set[str] = set()
    for base in cfg.locales_base:
        if base not in LOCALES:
            raise KeyError(
                f"Qué pasó: el locale base '{base}' no está declarado. "
                f"Disponibles: {sorted(LOCALES)}. Qué hacer: corrija "
                f"ConfigImportadores.locales_base."
            )
        sufijos |= set(LOCALES[base]["sufijos"])
    sufijos |= {str(s).strip().upper() for s in cfg.sufijos_extra}
    LOCALES[cfg.locale] = {
        "sufijos": frozenset(sufijos),
        "genericos": cfg.genericos_todos,
    }
    return cfg.locale


def grafias_aisladas(cfg: ConfigImportadores) -> frozenset[str]:
    """``cfg.paises_aislar`` en la misma forma normalizada que ``PAIS_NORMALIZADO``."""
    if not cfg.paises_aislar:
        return frozenset()
    serie = pd.Series([str(x) for x in cfg.paises_aislar], dtype="string")
    return frozenset(ascii_mayusculas(sanear_texto(serie)).tolist())


def cobertura_paises(df: pd.DataFrame, cfg: ConfigImportadores) -> pd.DataFrame:
    """Grafías de país que el catálogo NO reconoce, con filas y sugerencia.

    Es el preflight barato que evita pagar el emparejamiento para descubrir un
    hueco del catálogo: un mapeo vectorizado sobre la columna de país. Vacía
    cuando la cobertura es total.

    Returns:
        Columnas ``valor``, ``n_filas``, ``sugerencia``, ``similitud``, en
        orden descendente de filas. **Todas** las grafías, no solo las 25 que
        ``sugerir_alias_pais`` devuelve por defecto.
    """
    if cfg.col_pais not in df.columns:
        raise KeyError(
            f"Qué pasó: no existe la columna de país '{cfg.col_pais}'.\n"
            f"Qué hacer: corrija col_pais. El DataFrame tiene: {list(df.columns)}"
        )
    paises = canonizar_pais(
        df[cfg.col_pais],
        catalogo=cfg.catalogo_paises,
        patrones_no_pais=cfg.patrones_no_pais,
        agrupar_no_pais=cfg.agrupar_no_pais,
    )
    return sugerir_alias_pais(
        paises.sin_clasificar, catalogo=cfg.catalogo_paises, maximo=len(paises.sin_clasificar)
    )


def exigir_cobertura_paises(tabla: pd.DataFrame, cfg: ConfigImportadores) -> None:
    """Aplica ``cfg.paises_sin_clasificar`` a una tabla de :func:`cobertura_paises`.

    Las grafías declaradas en ``cfg.paises_aislar`` no cuentan: están aceptadas
    a propósito. Solo lo NO declarado detiene.

    Raises:
        PaisesSinClasificar: si queda alguna grafía no declarada y el modo es
            ``detener``. La tabla de la excepción trae solo las no declaradas.
    """
    if cfg.paises_sin_clasificar != "detener" or not len(tabla):
        return
    pendientes = tabla[~tabla["valor"].isin(grafias_aisladas(cfg))]
    if len(pendientes):
        raise PaisesSinClasificar(pendientes.reset_index(drop=True), cfg)


def renombres_por_colision(df: pd.DataFrame, cfg: ConfigImportadores) -> dict[str, str]:
    """``{columna_de_la_fuente: <columna>_FUENTE}`` para lo que choca con el estándar.

    Una columna de la fuente que se llama como una columna fija del contrato
    (``ID_GRUPO``, ``SRC``…), como una propia del flujo (``PAIS_FINAL``,
    ``ID_IMPORTADOR``…) o como una de trabajo del merge con los representantes
    (``_COLUMNAS_INTERNAS``: ``ES_LIDER``, ``NOMBRE_NORM_FINAL``) se conserva
    renombrada: la correlativa lleva TODAS las columnas de la fuente y una
    columna no puede significar dos cosas. Es una función pura de
    ``df.columns``: ``preparar`` la aplica y ``deduplicar_importadores`` la
    anota en el manifiesto y el diccionario.

    Raises:
        ContratoSalidaError: si una columna DECLARADA en la configuración
            (``col_razon_social``, ``col_pais``, ``cols_metricas``, ``col_id``)
            choca —renombrarla dejaría la configuración apuntando a otra
            cosa—, o si ya existe ``<col>_FUENTE`` y no hay nombre libre.
    """
    reservadas = (
        set(contrato.COLUMNAS_CORRELATIVA) | set(COLUMNAS_PROPIAS) | set(_COLUMNAS_INTERNAS)
    )
    chocan = [str(c) for c in df.columns if str(c) in reservadas]
    if not chocan:
        return {}
    declaradas = [c for c in cfg.columnas_declaradas if c in chocan]
    if declaradas:
        raise ContratoSalidaError(
            [
                mensaje_accionable(
                    f"las columnas declaradas {declaradas} se llaman como columnas que el "
                    f"estándar escribe.",
                    "la correlativa las produciría con otro contenido y la configuración "
                    "dejaría de apuntar a la columna de la fuente.",
                    f"renombre esas columnas en la fuente (p. ej. '{declaradas[0]}' → "
                    f"'{declaradas[0]}{_SUFIJO_COLISION}') y actualice ConfigImportadores.",
                )
            ]
        )
    presentes = {str(c) for c in df.columns}
    sin_sitio = [c for c in chocan if f"{c}{_SUFIJO_COLISION}" in presentes]
    if sin_sitio:
        raise ContratoSalidaError(
            [
                mensaje_accionable(
                    f"la fuente trae {sin_sitio} (chocan con el estándar) y también "
                    f"{[f'{c}{_SUFIJO_COLISION}' for c in sin_sitio]}.",
                    "no hay nombre libre para conservar la columna de la fuente.",
                    "renombre una de las dos en la fuente antes de correr.",
                )
            ]
        )
    return {c: f"{c}{_SUFIJO_COLISION}" for c in chocan}


def preparar(df: pd.DataFrame, cfg: ConfigImportadores) -> pd.DataFrame:
    """Saneamiento, país canónico y nombre normalizado. Deja constancia.

    Las columnas de la fuente que chocan con el estándar llegan ya renombradas
    ``<col>_FUENTE`` (:func:`renombres_por_colision`).

    Raises:
        KeyError: si falta alguna columna declarada, con la lista disponible.
        ContratoSalidaError: si una columna declarada choca con el estándar.
    """
    faltan = [c for c in cfg.columnas_declaradas if c not in df.columns]
    if faltan:
        raise KeyError(
            f"Qué pasó: al DataFrame le faltan las columnas {faltan}.\n"
            f"Por qué importa: son las que declara ConfigImportadores.\n"
            f"Qué hacer: corrija col_razon_social / col_pais / cols_metricas / col_id. "
            f"El DataFrame tiene: {list(df.columns)}"
        )
    registrar_locale(cfg)
    renombres = renombres_por_colision(df, cfg)
    out = df.rename(columns=renombres) if renombres else df.copy()
    for c in cfg.cols_metricas:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0)

    paises = canonizar_pais(
        out[cfg.col_pais],
        catalogo=cfg.catalogo_paises,
        patrones_no_pais=cfg.patrones_no_pais,
        agrupar_no_pais=cfg.agrupar_no_pais,
        # Cada grafía sin clasificar conserva su propio PAIS_FINAL SIEMPRE:
        # en modo "detener" solo sobreviven las declaradas en paises_aislar
        # (las demás detienen abajo), y aisladas es como deben quedar.
        agrupar_sin_clasificar=False,
    )
    # Fallar aquí y no en una invariante: el emparejamiento cuesta minutos y
    # este hueco se ve en milisegundos. Se lanza con la tabla de lo NO
    # declarado; lo declarado en paises_aislar sigue, aislado.
    if len(paises.sin_clasificar):
        exigir_cobertura_paises(
            sugerir_alias_pais(
                paises.sin_clasificar,
                catalogo=cfg.catalogo_paises,
                maximo=len(paises.sin_clasificar),
            ),
            cfg,
        )
    out = pd.concat([out, paises.tabla], axis=1)

    crudo = out[cfg.col_razon_social].astype("string").fillna("")
    limpio = unir_iniciales(
        ascii_mayusculas(sanear_texto(crudo, prelimpieza=cfg.prelimpieza)),
        activo=cfg.unir_iniciales_sueltas,
    )
    out["NOMBRE_NORM"] = normalizar_nombre(
        limpio,
        locale=cfg.locale,
        quitar_sufijos=True,
        quitar_genericos=cfg.quitar_genericos_del_nombre,
    )
    if cfg.verboso:
        n_sin_nombre = int((out["NOMBRE_NORM"] == "").sum())
        n_sin_pais = int((out["PAIS_METODO"] == "sin_clasificar").sum())
        print(f"filas leídas            : {len(out):,}")
        print(
            f"países: {paises.n_grafias:,} grafías → {paises.n_canonicos:,} canónicos "
            f"| sin clasificar: {n_sin_pais:,} filas ({1 - paises.cobertura:.2%})"
        )
        print(
            f"razones sociales: {crudo.nunique():,} grafías → "
            f"{out['NOMBRE_NORM'].nunique():,} normalizadas "
            f"| sin nombre utilizable: {n_sin_nombre:,} filas"
        )
        if n_sin_pais:
            print(
                "\n  ATENCIÓN: hay países sin clasificar (modo 'aislar': cada grafía "
                "va aparte y no se fusiona con nada). Revise las sugerencias y añada "
                "los alias que falten a CATALOGO_PAISES antes de usar el resultado "
                "por mercado."
            )
    return out


def _esquema(cfg: ConfigImportadores, comparador: SimilitudNombre) -> EsquemaCampos:
    return EsquemaCampos(
        campos=[
            CampoSpec(
                "NOMBRE_NORM",
                TipoCampo.NOMBRE_EMPRESA,
                peso=cfg.peso_nombre,
                comparador=comparador,
                umbral_concordancia=cfg.sim_minima,
                params={"quitar_sufijos": False},
                locale="EN",
            ),
            CampoSpec(
                "PAIS_FINAL",
                TipoCampo.CATEGORICO,
                peso=cfg.peso_pais,
                veta_discrepancia=True,
                faltante=PoliticaFaltante.BLOQUEAR,
            ),
        ],
        umbral_score=cfg.umbral_score,
        # Nombre Y país: es la definición literal de "empalme por las dos
        # variables". Con el país constante dentro de la partición, exigir dos
        # concordancias equivale a exigir la del nombre.
        min_concordancias=2,
        nombre="importadores_razon_social_pais",
    )


def similitud_nombre_desde_score(score, cfg: ConfigImportadores) -> np.ndarray:
    """Invierte el score combinado a la similitud de nombre en escala 0–1.

    Dentro de una partición el país concuerda con similitud 1,0, así que el
    score es una función afín de la similitud de nombre y se puede deshacer.
    Es la misma inversión que usa :func:`sensibilidad_umbral`; vive aquí para
    que la poda de ``decisiones`` y la sensibilidad usen exactamente la misma
    aritmética en coma flotante.
    """
    firmada = (
        np.asarray(score, dtype=np.float64) * (cfg.peso_nombre + cfg.peso_pais) - cfg.peso_pais
    ) / cfg.peso_nombre
    return (firmada + 1.0) / 2.0


def _podar_decisiones(
    dec: pd.DataFrame, iso: str, desplazamiento: int, cfg: ConfigImportadores
) -> pd.DataFrame:
    """Conserva fusionados, vetados y pares ≥ piso; índices a posiciones globales."""
    sim01 = similitud_nombre_desde_score(dec["score"].to_numpy(), cfg)
    conservar = (
        dec["fusion"].to_numpy() | dec["veto"].to_numpy() | (sim01 >= cfg.sim_minima_auditoria)
    )
    out = dec.loc[conservar].reset_index(drop=True)
    out["i"] = out["i"].to_numpy() + desplazamiento
    out["j"] = out["j"].to_numpy() + desplazamiento
    out.insert(0, "PAIS_ISO3", iso)
    return out


def decisiones_con_nombres(decisiones: pd.DataFrame, representantes: pd.DataFrame) -> pd.DataFrame:
    """Añade ``NOMBRE_A``/``NOMBRE_B`` a ``decisiones`` desde ``representantes``.

    ``i`` y ``j`` son posiciones en ``representantes`` (el orden en que
    :func:`ejecutar` concatenó las particiones), así que la resolución es un
    indexado directo, sin merge.
    """
    if decisiones.empty:
        return decisiones.assign(NOMBRE_A=pd.Series(dtype=object), NOMBRE_B=pd.Series(dtype=object))
    nombres = representantes["NOMBRE_NORM"].to_numpy()
    return decisiones.assign(
        NOMBRE_A=nombres[decisiones["i"].to_numpy()],
        NOMBRE_B=nombres[decisiones["j"].to_numpy()],
    )


def ejecutar(prep: pd.DataFrame, cfg: ConfigImportadores) -> dict:
    """Bloqueo + scoring + clustering + cobertura, particionando por país.

    Memoria (0.22.4): cada partición se puntúa en lotes de
    ``cfg.pares_por_lote`` pares y de sus decisiones solo se conservan las que
    sirven para auditar (ver ``cfg.sim_minima_auditoria``), con ``i``/``j``
    como posiciones globales en ``representantes``. Antes se guardaban los
    ~50 M candidatos de la base real con los dos nombres en cada fila y la
    partición más grande se puntuaba de una vez: OOM en 15 GiB.
    """
    agregados = {c: (c, "sum") for c in cfg.cols_metricas}
    rep = (
        prep.groupby(["PAIS_ISO3", "PAIS_FINAL", "NOMBRE_NORM"], sort=False)
        .agg(N_FILAS=("NOMBRE_NORM", "size"), **agregados)
        .reset_index()
    )
    rep = rep[rep["NOMBRE_NORM"] != ""].reset_index(drop=True)

    vocabulario = pd.Series(sorted(rep["NOMBRE_NORM"].unique()))
    pesos = construir_idf(vocabulario.reset_index(drop=True))
    pesos = neutralizar_genericos(pesos, cfg.genericos_todos, peso=cfg.peso_token_generico)
    comparador = SimilitudNombre(
        pesos,
        vocabulario.to_numpy(),
        genericos_estructurales=cfg.genericos_estructurales,
        alfa=cfg.alfa_idf,
        prefix_weight=cfg.prefix_weight,
        max_diferencia_informativa=cfg.max_diferencia_informativa,
        min_informativos_compartidos=cfg.min_informativos_compartidos,
        fraccion_max_distintivo=cfg.fraccion_max_distintivo,
        longitud_minima_token=cfg.longitud_minima_token,
        ignorar_numericos=cfg.ignorar_numericos,
    )
    if cfg.verboso:
        print(f"representantes únicos (país, nombre): {len(rep):,}")
        print(
            f"vocabulario: {len(pesos.vocabulario):,} tokens · "
            f"{getattr(pesos, '_neutralizados', 0)} genéricos neutralizados · "
            f"{int(comparador.informativos.sum()):,} informativos"
        )

    esquema = _esquema(cfg, comparador)
    t0 = time.time()
    partes: list[pd.DataFrame] = []
    decisiones: list[pd.DataFrame] = []
    n_candidatos = 0
    n_cortes = 0
    desplazamiento = 0  # posición global del primer representante de la partición
    particiones = list(rep.groupby("PAIS_ISO3", sort=True))
    for k, (iso, sub) in enumerate(particiones, 1):
        sub = sub.reset_index(drop=True)
        if len(sub) < 2:
            sub["GRUPO_LOCAL"] = 0
            sub["ES_LIDER"] = True
            sub["SIM_LIDER"] = 1.0
            partes.append(sub)
            desplazamiento += len(sub)
            continue
        bloqueo = BloqueoComponible(
            [
                LSHTexto(
                    "NOMBRE_NORM",
                    umbral=cfg.lsh_umbral,
                    permutaciones=cfg.lsh_permutaciones,
                    ngram=cfg.lsh_ngram,
                    max_grupo=cfg.lsh_max_grupo,
                    overflow="connect",
                )
            ]
        )
        res = evaluar_esquema(
            sub,
            esquema,
            bloqueo,
            max_candidatos=cfg.max_candidatos,
            pares_por_lote=cfg.pares_por_lote,
            incluir_desglose=False,
        )
        etiquetas = clusters_desde_decisiones(len(sub), res.decisiones, respetar_vetos=True)
        masa = sub["N_FILAS"].to_numpy().astype(np.float64) * 1e9
        if cfg.col_peso_economico and cfg.col_peso_economico in sub.columns:
            masa = masa + sub[cfg.col_peso_economico].to_numpy()
        if cfg.refinar_cohesion:
            cobertura = cobertura_estrella(
                sub["NOMBRE_NORM"].to_numpy(),
                etiquetas,
                masa,
                comparador,
                similitud_minima=cfg.sim_minima,
                regla_lider=cfg.regla_nombre_final,
            )
            etiquetas = cobertura.etiquetas
            es_lider = cobertura.es_lider
            sim_lider = cobertura.similitud_al_lider
            n_cortes += cobertura.n_cortes
        else:
            es_lider = np.zeros(len(sub), dtype=bool)
            sim_lider = np.ones(len(sub))
        sub["GRUPO_LOCAL"], sub["ES_LIDER"], sub["SIM_LIDER"] = etiquetas, es_lider, sim_lider
        partes.append(sub)
        n_candidatos += res.n_candidatos
        if len(res.decisiones):
            decisiones.append(_podar_decisiones(res.decisiones, iso, desplazamiento, cfg))
        del res  # la tabla completa de la partición no sobrevive a la partición
        desplazamiento += len(sub)
        if cfg.verboso and (k % 25 == 0 or k == len(particiones)):
            print(
                f"  {k:>3}/{len(particiones)} particiones · {n_candidatos:,} candidatos "
                f"· {time.time() - t0:.0f}s",
                end="\r",
            )

    salida = pd.concat(partes, ignore_index=True)
    salida["ID_IMPORTADOR"] = (
        salida["PAIS_ISO3"] + "-" + salida["GRUPO_LOCAL"].astype(str).str.zfill(6)
    )
    dec = pd.concat(decisiones, ignore_index=True) if decisiones else pd.DataFrame()
    segundos = time.time() - t0
    if cfg.verboso:
        print(
            f"\ntiempo {segundos:.0f}s · candidatos {n_candidatos:,} · "
            f"fusiones {int(dec['fusion'].sum()) if len(dec) else 0:,} · "
            f"cortes por cohesión {n_cortes:,}"
        )
        print(
            f"{len(salida):,} representantes → {salida['ID_IMPORTADOR'].nunique():,} importadores"
        )
    return {
        "representantes": salida,
        "decisiones": dec,
        "comparador": comparador,
        "pesos_idf": pesos,
        "n_candidatos": n_candidatos,
        "n_cortes": n_cortes,
        "segundos": segundos,
    }


def construir_entregables(
    prep: pd.DataFrame, corrida: dict, cfg: ConfigImportadores
) -> dict[str, pd.DataFrame]:
    """CORRELATIVA, GOLDEN, PAISES, REVISION y REVISION_GRUPOS a partir de la corrida.

    Deja en ``corrida["regla_id_registro"]`` el reporte de
    ``salida.completar._asignar_id_registro`` (``{fuente: {regla, columna,
    motivo}}``): el manifiesto lo publica tal cual, sin recomputar la regla.
    """
    rep = corrida["representantes"]
    lideres = rep.loc[rep.ES_LIDER, ["ID_IMPORTADOR", "NOMBRE_NORM"]].rename(
        columns={"NOMBRE_NORM": "NOMBRE_NORM_FINAL"}
    )
    rep = rep.merge(lideres, on="ID_IMPORTADOR", how="left")

    # Nombre final legible = la grafía ORIGINAL más frecuente del nombre líder.
    peso = cfg.col_peso_economico if cfg.col_peso_economico in prep.columns else None
    agregado = {"N": (cfg.col_razon_social, "size")}
    if peso:
        agregado["PESO"] = (peso, "sum")
    grafias = (
        prep.groupby(["PAIS_ISO3", "PAIS_FINAL", "NOMBRE_NORM", cfg.col_razon_social], sort=False)
        .agg(**agregado)
        .reset_index()
    )
    if not peso:
        grafias["PESO"] = 0.0
    grafias["LARGO"] = grafias[cfg.col_razon_social].astype(str).str.len()
    # Penalización de rareza: caracteres que delatan mojibake o entidades HTML
    # mal decodificadas ("¿!FEST Coffee Mission¿ LLC"). Va ANTES del peso
    # económico: entre dos grafías igual de frecuentes, una cadena corrupta
    # nunca es la etiqueta correcta, por mucho valor que tenga detrás. Sin este
    # desempate, la regla de "la grafía más larga" escoge justo la corrupta,
    # porque los caracteres de mojibake suman longitud.
    grafias["RAREZA"] = grafias[cfg.col_razon_social].astype(str).str.count(_ESPERADOS)
    grafias = grafias.sort_values(
        [
            "PAIS_ISO3",
            "PAIS_FINAL",
            "NOMBRE_NORM",
            "N",
            "RAREZA",
            "PESO",
            "LARGO",
            cfg.col_razon_social,
        ],
        ascending=[True, True, True, False, True, False, False, True],
        kind="stable",
    )
    modal = grafias.drop_duplicates(["PAIS_ISO3", "PAIS_FINAL", "NOMBRE_NORM"])[
        ["PAIS_ISO3", "PAIS_FINAL", "NOMBRE_NORM", cfg.col_razon_social]
    ].rename(
        columns={
            "NOMBRE_NORM": "NOMBRE_NORM_FINAL",
            cfg.col_razon_social: "RAZON_SOCIAL_FINAL",
        }
    )
    rep = rep.merge(modal, on=["PAIS_ISO3", "PAIS_FINAL", "NOMBRE_NORM_FINAL"], how="left")
    rep["SIM_AL_FINAL"] = ((rep["SIM_LIDER"] + 1) / 2).round(4)

    # La llave es la identidad COMPLETA del representante. Unir solo por
    # (ISO3, NOMBRE_NORM) multiplicaba filas cuando varios PAIS_FINAL comparten
    # ISO3: ocurre con `paises_sin_clasificar="aislar"` (todos ZZZ) y ocurría ya,
    # latente, con `agrupar_no_pais=False` (todos ZZF). Detectado por la
    # invariante "una fila por fila de entrada".
    # SRC y ORIGINAL_INDEX (F2.6) van en la base ANTES del merge: si el merge
    # multiplicara filas, ORIGINAL_INDEX se repetiría y la invariante lo vería;
    # un arange después del merge lo taparía.
    columnas_fuente = [c for c in prep.columns if c not in _COLUMNAS_PREPARACION]
    corr = prep.assign(SRC=cfg.nombre_fuente, ORIGINAL_INDEX=np.arange(len(prep))).merge(
        rep[
            [
                "PAIS_ISO3",
                "PAIS_FINAL",
                "NOMBRE_NORM",
                "ID_IMPORTADOR",
                "NOMBRE_NORM_FINAL",
                "RAZON_SOCIAL_FINAL",
                "SIM_AL_FINAL",
                "ES_LIDER",
            ]
        ],
        on=["PAIS_ISO3", "PAIS_FINAL", "NOMBRE_NORM"],
        how="left",
    )

    # Nombres que quedan vacíos tras normalizar ("0", "N/A", "-"): cada uno a su
    # propio grupo. NUNCA se fusionan entre sí — no hay evidencia de que lo
    # sean, y unirlos inventaría una empresa gigante que no existe. En la base
    # de referencia son 58 filas y el 20,5 % del valor FOB.
    sin_nombre = corr["ID_IMPORTADOR"].isna()
    if sin_nombre.any():
        secuencia = pd.Series(np.arange(int(sin_nombre.sum())), index=corr.index[sin_nombre])
        corr.loc[sin_nombre, "ID_IMPORTADOR"] = "SINNOMBRE-" + secuencia.astype(str).str.zfill(6)
        corr.loc[sin_nombre, "RAZON_SOCIAL_FINAL"] = corr.loc[sin_nombre, cfg.col_razon_social]
        corr.loc[sin_nombre, "SIM_AL_FINAL"] = 1.0
        corr.loc[sin_nombre, "ES_LIDER"] = True

    # Identificador que IGNORA el país: la misma empresa en varios destinos.
    # Solo une coincidencias EXACTAS del nombre normalizado final; no hace
    # emparejamiento difuso entre países, y así está documentado.
    llave = rep[["ID_IMPORTADOR", "NOMBRE_NORM_FINAL"]].drop_duplicates()
    global_id = "G" + pd.Series(
        llave.groupby("NOMBRE_NORM_FINAL", sort=True).ngroup().to_numpy()
    ).astype(str).str.zfill(6)
    corr["ID_EMPRESA_GLOBAL"] = (
        corr["ID_IMPORTADOR"]
        .map(dict(zip(llave["ID_IMPORTADOR"], global_id, strict=True)))
        .fillna(corr["ID_IMPORTADOR"])
    )
    corr["CAMBIO_NOMBRE"] = corr[cfg.col_razon_social] != corr["RAZON_SOCIAL_FINAL"]
    corr["CAMBIO_PAIS"] = corr[cfg.col_pais] != corr["PAIS_FINAL"]
    corr["ES_LIDER"] = corr["ES_LIDER"].astype(bool)

    # ── Columnas fijas del contrato 1.0 (F2.6) ──────────────────────────
    # ID_GRUPO: entero por primera aparición en ORIGINAL_INDEX, UNA regla con
    # salida.completar (la misma que recodifica C<n>/S<n> de dedupe).
    corr["ID_GRUPO"] = corr["ID_IMPORTADOR"].astype("string")
    _recodificar_id_grupo(corr)
    id_registro, regla_id = _asignar_id_registro(corr, cfg.col_id or None)
    corrida["regla_id_registro"] = regla_id
    corr["ID_REGISTRO"] = id_registro.astype(str)
    # Defensa: _asignar_id_registro cae a <SRC>-F<ORIGINAL_INDEX> cuando col_id
    # no es único, así que hoy solo un merge que multiplique filas llegaría aquí.
    repetidos = int(corr["ID_REGISTRO"].duplicated().sum())
    if repetidos:
        raise ContratoSalidaError(
            [
                mensaje_accionable(
                    f"ID_REGISTRO no es único ({repetidos} repetido(s); "
                    f"col_id={cfg.col_id!r}; regla: {regla_id}).",
                    "cada fila de entrada debe tener un ID_REGISTRO propio o la "
                    "correlativa no es trazable.",
                    "revise que el merge con los representantes no multiplique filas "
                    "(invariante «una fila por fila de entrada»); reporte el manifiesto.",
                )
            ]
        )
    # ID_ENTIDAD: ningún grupo adopta identificador (la base no lo trae): todos ENT-….
    sin_identificador = pd.Series(pd.NA, index=corr.index, dtype="string")
    corr["ID_ENTIDAD"] = _id_entidad(
        corr, sin_identificador, np.zeros(len(corr), dtype=bool)
    ).astype(str)
    corr["NIT_FINAL"] = sin_identificador
    corr["NIT_DISTANCE"] = pd.array([pd.NA] * len(corr), dtype="Int64")
    corr["NAME_SIMILARITY_SCORE"] = corr["SIM_AL_FINAL"].astype("float64")
    tamano = corr.groupby("ID_GRUPO", sort=False)["ID_GRUPO"].transform("size").to_numpy()
    corr["METODO_UNION"] = np.where(tamano == 1, "sin_pareja", "nombre")
    corr["SCORE_PAR"] = _score_par(corr, tamano)

    golden = _golden_estandar(corr, cfg)
    confianza = golden.set_index("ID_GRUPO")["CONFIANZA"]
    corr["CONFIANZA"] = corr["ID_GRUPO"].map(confianza).astype("string")

    fijas = list(contrato.COLUMNAS_CORRELATIVA)
    propias = [c for c in COLUMNAS_PROPIAS if c in corr.columns]
    correlativa = corr[fijas + columnas_fuente + propias].copy()

    agg_metricas = {c: (c, "sum") for c in cfg.cols_metricas}
    paises = (
        correlativa.groupby([cfg.col_pais, "PAIS_FINAL", "PAIS_ISO3", "PAIS_METODO"])
        .agg(N_FILAS=("PAIS_FINAL", "size"), **agg_metricas)
        .reset_index()
        .sort_values(["PAIS_FINAL", "N_FILAS"], ascending=[True, False])
    )

    motivo = pd.Series("", index=golden.index)
    motivo += np.where(golden["N_VARIANTES_NOMBRE"] >= 15, "grupo_grande;", "")
    motivo += np.where(golden["SIM_MINIMA"] < cfg.umbral_nombre + 0.03, "cohesion_baja;", "")
    motivo += np.where(
        golden["ID_IMPORTADOR"].str.startswith("SINNOMBRE"), "sin_nombre_utilizable;", ""
    )
    revision_grupos = golden.assign(MOTIVO_REVISION=motivo)
    revision_grupos = revision_grupos[revision_grupos.MOTIVO_REVISION != ""].reset_index(drop=True)
    revision = _revision_pares(corr, revision_grupos, cfg)

    return {
        "CORRELATIVA": correlativa,
        "GOLDEN": golden,
        "PAISES": paises,
        "REVISION": revision,
        "REVISION_GRUPOS": revision_grupos,
    }


def _score_par(corr: pd.DataFrame, tamano: np.ndarray) -> pd.Series:
    """``SCORE_PAR`` del contrato con la evidencia de este flujo (ver docstring del módulo).

    Mayor similitud 0–1 entre el registro y otro de su grupo según la
    estrella: un miembro, su arista al líder (``SIM_AL_FINAL``); el líder, la
    mejor arista a un miembro; ``1.0`` si otro registro del grupo comparte su
    nombre normalizado (igualdad exacta tras normalizar); nulo en
    ``sin_pareja``. Vectorizado por grupo, sin bucles por fila.
    """
    sim = corr["SIM_AL_FINAL"].astype("float64")
    lider = corr["ES_LIDER"].to_numpy(dtype=bool)
    mismo_nombre = (
        corr.groupby(["ID_GRUPO", "NOMBRE_NORM"], sort=False)["ID_GRUPO"].transform("size") > 1
    ).to_numpy()
    mejor_miembro = sim.where(~lider).groupby(corr["ID_GRUPO"], sort=False).transform("max")
    score = pd.Series(np.where(lider, mejor_miembro.to_numpy(), sim.to_numpy()), index=corr.index)
    score = score.mask(mismo_nombre, 1.0)
    return score.mask(tamano == 1, np.nan).astype("float64")


def _golden_estandar(corr: pd.DataFrame, cfg: ConfigImportadores) -> pd.DataFrame:
    """Golden del contrato (13 de v1 + ``ID_ENTIDAD``) más las columnas propias.

    Las métricas salen de la regla ÚNICA de ``golden.metricas``
    (``metricas_de_grupo`` sobre ``ID_GRUPO``/``SRC``/``RAZON_SOCIAL``/``NIT``
    y ``metricas_de_calidad``), no de una copia: con una sola fuente y sin
    identificador, ``NIT_VARIATIONS`` es 0, ``CONFIDENCE_SCORE`` 0,85 y
    ``CONFIANZA`` MEDIA hasta 5 registros / BAJA después.
    """
    frame = pd.DataFrame(
        {
            "ID_GRUPO": corr["ID_GRUPO"].to_numpy(),
            "SRC": corr["SRC"].to_numpy(),
            "RAZON_SOCIAL": corr[cfg.col_razon_social].to_numpy(),
            "NIT": pd.Series(pd.NA, index=corr.index, dtype="string").to_numpy(),
        }
    )
    grupo = metricas_de_grupo(frame, [cfg.nombre_fuente])
    primeros = corr.drop_duplicates("ID_GRUPO").set_index("ID_GRUPO")
    golden = pd.DataFrame(index=grupo.index)
    golden["NIT_FINAL"] = pd.Series(pd.NA, index=grupo.index, dtype="string")
    golden["RAZON_SOCIAL_FINAL"] = primeros["RAZON_SOCIAL_FINAL"].reindex(grupo.index)
    for col in ("PRIMARY_SOURCE", "SOURCES_LIST"):
        golden[col] = grupo[col]
    for col in ("SOURCES_COUNT", "RECORD_COUNT", "NAME_VARIATIONS", "NIT_VARIATIONS"):
        golden[col] = grupo[col].astype("int64")
    calidad = metricas_de_calidad(golden)
    golden["CONFIDENCE_SCORE"] = calidad["CONFIDENCE_SCORE"].astype("float64")
    golden["CONFIANZA"] = grupo["CONFIANZA"]
    golden["REQUIRES_REVIEW"] = calidad["REQUIRES_REVIEW"].to_numpy() != 0
    golden["CREATED_AT"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    golden["ID_ENTIDAD"] = primeros["ID_ENTIDAD"].reindex(grupo.index)
    golden = golden.reset_index()[list(contrato.COLUMNAS_GOLDEN)]

    # Las propias de v0.22: por importador (= por ID_GRUPO) y las sumas de
    # cols_metricas con su nombre de entrada tal cual (lo consumen el 07 y la
    # evidencia).
    propias = (
        corr.groupby("ID_GRUPO", sort=False)
        .agg(
            ID_IMPORTADOR=("ID_IMPORTADOR", "first"),
            ID_EMPRESA_GLOBAL=("ID_EMPRESA_GLOBAL", "first"),
            PAIS_FINAL=("PAIS_FINAL", "first"),
            PAIS_ISO3=("PAIS_ISO3", "first"),
            N_FILAS_ORIGEN=(cfg.col_razon_social, "size"),
            N_VARIANTES_NOMBRE=(cfg.col_razon_social, "nunique"),
            SIM_MINIMA=("SIM_AL_FINAL", "min"),
            **{c: (c, "sum") for c in cfg.cols_metricas},
        )
        .reset_index()
    )
    golden = golden.merge(propias, on="ID_GRUPO", how="left")
    orden = cfg.col_peso_economico if cfg.col_peso_economico in golden.columns else "N_FILAS_ORIGEN"
    return golden.sort_values(orden, ascending=False, kind="stable").reset_index(drop=True)


def _revision_pares(
    corr: pd.DataFrame, revision_grupos: pd.DataFrame, cfg: ConfigImportadores
) -> pd.DataFrame:
    """``revision`` con la forma del archivo de decisiones (``contrato.REVISION``).

    Un par ``(líder, miembro)`` por arista de estrella de cada grupo marcado
    por ``grupo_grande`` o ``cohesion_baja``: es exactamente lo que la
    correlativa afirma de cada asignación (ADR-0009 §4) y lo que una persona
    puede confirmar o negar. ``CLAVE_*`` es el ``ID_REGISTRO`` del primer
    registro (menor ``ORIGINAL_INDEX``) de cada representante (país, nombre
    normalizado) y ``NOMBRE_*`` la grafía original de ese mismo registro.
    Los ``sin_nombre_utilizable`` no forman par (son grupos de un registro):
    quedan en ``revision_grupos`` y en la correlativa (``SINNOMBRE-``).
    """
    marcados = revision_grupos.loc[
        revision_grupos["MOTIVO_REVISION"].str.contains("grupo_grande|cohesion_baja"),
        ["ID_GRUPO", "MOTIVO_REVISION"],
    ]
    if marcados.empty:
        return contrato.revision_vacia()
    sub = corr.loc[
        corr["ID_GRUPO"].isin(marcados["ID_GRUPO"]),
        [
            "ID_GRUPO",
            "ORIGINAL_INDEX",
            "NOMBRE_NORM",
            "ES_LIDER",
            "ID_REGISTRO",
            cfg.col_razon_social,
            "SIM_AL_FINAL",
        ],
    ].sort_values(["ID_GRUPO", "ORIGINAL_INDEX"], kind="stable")
    representantes = sub.drop_duplicates(["ID_GRUPO", "NOMBRE_NORM"], keep="first")
    lideres = representantes.loc[
        representantes["ES_LIDER"], ["ID_GRUPO", "ID_REGISTRO", cfg.col_razon_social]
    ].rename(columns={"ID_REGISTRO": "CLAVE_A", cfg.col_razon_social: "NOMBRE_A"})
    miembros = representantes.loc[
        ~representantes["ES_LIDER"],
        ["ID_GRUPO", "ID_REGISTRO", cfg.col_razon_social, "SIM_AL_FINAL"],
    ].rename(columns={"ID_REGISTRO": "CLAVE_B", cfg.col_razon_social: "NOMBRE_B"})
    pares = miembros.merge(lideres, on="ID_GRUPO", how="inner").merge(marcados, on="ID_GRUPO")
    if pares.empty:
        return contrato.revision_vacia()
    razon = (
        pares["MOTIVO_REVISION"].astype("string").str.rstrip(";").str.replace(";", ", ")
        + " · similitud al líder "
        + pares["SIM_AL_FINAL"].map("{:.4f}".format).astype("string")
    )
    revision = pd.DataFrame(
        {
            "TIPO": "duplicado",
            "FUENTE": cfg.nombre_fuente,
            "CLAVE_A": pares["CLAVE_A"].astype("string"),
            "NOMBRE_A": pares["NOMBRE_A"].astype("string"),
            "CLAVE_B": pares["CLAVE_B"].astype("string"),
            "NOMBRE_B": pares["NOMBRE_B"].astype("string"),
            "DECISION": pd.Series(pd.NA, index=pares.index, dtype="string"),
            "AUTOR": pd.Series(pd.NA, index=pares.index, dtype="string"),
            "RAZON": razon,
        }
    )
    for col in ("TIPO", "FUENTE"):
        revision[col] = revision[col].astype("string")
    return revision[list(contrato.COLUMNAS_REVISION)].reset_index(drop=True)


def metricas(
    prep: pd.DataFrame, tablas: dict, corrida: dict, cfg: ConfigImportadores
) -> pd.DataFrame:
    """Indicadores de la corrida. Ninguno requiere verdad de campo."""
    correlativa = tablas["CORRELATIVA"]
    golden = tablas["GOLDEN"]
    peso = cfg.col_peso_economico if cfg.col_peso_economico in correlativa.columns else None
    tam = golden["N_VARIANTES_NOMBRE"]
    filas: list[tuple[str, object]] = [
        ("filas de entrada", len(correlativa)),
        (
            "pares (razón social, país) únicos",
            len(prep.groupby([cfg.col_razon_social, cfg.col_pais])),
        ),
        ("representantes (país, nombre normalizado)", len(corrida["representantes"])),
        ("importadores finales", golden["ID_IMPORTADOR"].nunique()),
        ("empresas finales ignorando país", correlativa["ID_EMPRESA_GLOBAL"].nunique()),
        (
            "reducción de filas",
            f"{1 - golden['ID_IMPORTADOR'].nunique() / max(len(correlativa), 1):.1%}",
        ),
        (
            "filas con nombre reasignado",
            f"{int(correlativa.CAMBIO_NOMBRE.sum()):,} ({correlativa.CAMBIO_NOMBRE.mean():.1%})",
        ),
        (
            "filas con país reasignado",
            f"{int(correlativa.CAMBIO_PAIS.sum()):,} ({correlativa.CAMBIO_PAIS.mean():.1%})",
        ),
        (
            "grafías de país → canónicos",
            f"{prep[cfg.col_pais].nunique():,} → {correlativa.PAIS_FINAL.nunique():,}",
        ),
        ("grupos con 1 sola variante", f"{int((tam == 1).sum()):,} ({(tam == 1).mean():.1%})"),
        ("grupos con 2–5 variantes", int(tam.between(2, 5).sum())),
        ("grupos con 6–14 variantes", int(tam.between(6, 14).sum())),
        ("grupos con 15+ variantes", int((tam >= 15).sum())),
        ("variantes en el grupo más grande", int(tam.max())),
        ("cohesión mínima del peor grupo", round(float(golden["SIM_MINIMA"].min()), 4)),
        (
            "cohesión media de lo reasignado",
            round(float(correlativa.loc[correlativa.CAMBIO_NOMBRE, "SIM_AL_FINAL"].mean()), 4)
            if correlativa.CAMBIO_NOMBRE.any()
            else 1.0,
        ),
        ("pares candidatos evaluados", corrida["n_candidatos"]),
        ("cortes por cohesión", corrida["n_cortes"]),
        ("segundos de ejecución", round(corrida["segundos"])),
        ("grupos marcados para revisión", len(tablas["REVISION_GRUPOS"])),
        ("pares (líder, miembro) en revision.csv", len(tablas["REVISION"])),
    ]
    if peso:
        total = float(correlativa[peso].sum())
        conservado = float(golden[peso].sum())
        filas += [
            (f"{peso} total", f"{total:,.0f}"),
            (f"{peso} conservado en GOLDEN", f"{conservado:,.0f}"),
            ("diferencia relativa", f"{abs(total - conservado) / max(abs(total), 1.0):.2e}"),
            (
                "concentración: top 10 importadores",
                f"{golden[peso].head(10).sum() / total:.2%}" if total else "n/a",
            ),
            (
                "concentración: top 100 importadores",
                f"{golden[peso].head(100).sum() / total:.2%}" if total else "n/a",
            ),
            (
                f"{peso} en grupos de revisión",
                f"{tablas['REVISION_GRUPOS'][peso].sum() / total:.1%}"
                if len(tablas["REVISION_GRUPOS"]) and total
                else "0.0%",
            ),
        ]
    # ``valor`` mezcla enteros, flotantes y textos formateados ("41.2 %"): como
    # columna tipada no tiene sentido y Parquet la rechaza ("Could not convert
    # '41.2%' with type str"), que es lo que pasó al exportar la corrida real
    # (0.22.4). Es una tabla para leer, no para calcular: todo va como texto.
    tabla = pd.DataFrame(filas, columns=["indicador", "valor"])
    tabla["valor"] = tabla["valor"].map(str)
    return tabla


def _metodos_admitidos(cfg: ConfigImportadores, prep: pd.DataFrame) -> list[str]:
    """Métodos de canonización que la invariante del país acepta.

    ``sin_clasificar`` es admisible en modo ``aislar`` (cada grafía conserva su
    ``PAIS_FINAL`` y la invariante siguiente comprueba que no se mezclen) o, en
    modo ``detener``, solo si TODAS las grafías sin clasificar presentes están
    declaradas en ``paises_aislar``. Ver una no declarada aquí significaría que
    alguien saltó ``preparar`` — y debe fallar.
    """
    base = ["catalogo_exacto", "catalogo_laxo", "zona_franca"]
    if cfg.paises_sin_clasificar == "aislar":
        return [*base, "sin_clasificar"]
    presentes = set(prep.loc[prep["PAIS_METODO"] == "sin_clasificar", "PAIS_NORMALIZADO"])
    return [*base, "sin_clasificar"] if presentes and presentes <= grafias_aisladas(cfg) else base


def verificar_invariantes(
    prep: pd.DataFrame, tablas: dict, cfg: ConfigImportadores
) -> pd.DataFrame:
    """Lo que SIEMPRE debe cumplirse. Si algo falla, el resultado no sirve.

    No son pruebas decorativas: cada una corresponde a un error que este tipo
    de pipeline comete en silencio —perder filas, duplicarlas, mover un
    registro de país, dejar un nombre final vacío, o afirmar una asignación por
    debajo del umbral declarado—. Es la lección de ADR-0008 aplicada aquí: si
    algo es entregable, va en las invariantes.
    """
    correlativa = tablas["CORRELATIVA"]
    golden = tablas["GOLDEN"]
    peso = cfg.col_peso_economico if cfg.col_peso_economico in correlativa.columns else None
    pruebas: list[tuple[str, bool]] = [
        ("la correlativa tiene una fila por fila de entrada", len(correlativa) == len(prep)),
        ("ningún importador sin identificador", not correlativa["ID_IMPORTADOR"].isna().any()),
        (
            "ningún nombre final vacío",
            not (
                correlativa["RAZON_SOCIAL_FINAL"].isna()
                | (correlativa["RAZON_SOCIAL_FINAL"].astype(str).str.strip() == "")
            ).any(),
        ),
        (
            "ningún grupo cruza dos países",
            int(correlativa.groupby("ID_IMPORTADOR")["PAIS_ISO3"].nunique().max()) == 1,
        ),
        (
            "toda asignación cumple el umbral declarado",
            bool((correlativa["SIM_AL_FINAL"] >= cfg.umbral_nombre - 1e-9).all()),
        ),
        (
            "cada grupo tiene exactamente un nombre final",
            int(correlativa.groupby("ID_IMPORTADOR")["RAZON_SOCIAL_FINAL"].nunique().max()) == 1,
        ),
        (
            "GOLDEN cubre todos los importadores",
            golden["ID_IMPORTADOR"].nunique() == correlativa["ID_IMPORTADOR"].nunique(),
        ),
        (
            "el país final está en el catálogo o marcado",
            bool(correlativa["PAIS_METODO"].isin(_metodos_admitidos(cfg, prep)).all()),
        ),
        # Más fuerte que "ningún grupo cruza dos países" (que mira el ISO3):
        # aquí se exige que PAIS_FINAL sea constante dentro del grupo. Es lo que
        # garantiza que, en modo 'aislar', dos grafías sin clasificar distintas
        # —ambas ISO3 = ZZZ— no compartan importador. Se mide, no se supone.
        (
            "ningún grupo mezcla dos grafías de país",
            int(correlativa.groupby("ID_IMPORTADOR")["PAIS_FINAL"].nunique().max()) == 1,
        ),
        # F2.6: el estándar se mide, no se supone. La misma comprobación que
        # escribir_resultado hace en estricto, pero aquí queda en la tabla.
        (
            "cumple el contrato de salida 1.0",
            ResultadoLinkage(correlativa=correlativa, golden=golden, revision=tablas["REVISION"])
            .validar()
            .ok,
        ),
    ]
    if peso:
        # Tolerancia RELATIVA, no absoluta: la suma de cientos de miles de
        # float64 depende del orden de acumulación, y agrupar cambia ese orden.
        # Lo que esta invariante debe detectar es una fila perdida o duplicada
        # —cualquiera movería el total muchos órdenes por encima de 1e-9
        # relativo—, no el último bit de un punto flotante.
        total = float(correlativa[peso].sum())
        conservado = float(golden[peso].sum())
        relativo = abs(total - conservado) / max(abs(total), 1.0)
        pruebas.append((f"{peso} se conserva (dif. relativa {relativo:.2e})", relativo <= 1e-9))
    out = pd.DataFrame(pruebas, columns=["invariante", "cumple"])
    out["estado"] = np.where(out["cumple"], "OK", "FALLA")
    return out


def muestra_para_revision(tablas: dict, cfg: ConfigImportadores) -> pd.DataFrame:
    """Muestra estratificada por banda de similitud, para etiquetar a mano.

    Es el único camino honesto a una cifra de precisión: sin verdad de campo,
    una métrica interna dice si el resultado es CONSISTENTE, no si es CORRECTO.
    """
    correlativa = tablas["CORRELATIVA"]
    # La identidad de una asignación es (grafía, país FINAL): con ISO3 solo, dos
    # grafías sin clasificar aisladas (ambas ZZZ) contarían como una.
    asignadas = correlativa[correlativa["CAMBIO_NOMBRE"]].drop_duplicates(
        [cfg.col_razon_social, "PAIS_ISO3", "PAIS_FINAL"]
    )
    bandas = [(cfg.umbral_nombre, 0.88), (0.88, 0.92), (0.92, 0.96), (0.96, 1.001)]
    piezas = []
    for lo, hi in bandas:
        if hi <= lo:
            continue
        s = asignadas[(lo <= asignadas.SIM_AL_FINAL) & (hi > asignadas.SIM_AL_FINAL)]
        if s.empty:
            continue
        take = s.sample(min(cfg.muestra_revision_por_banda, len(s)), random_state=cfg.semilla)
        piezas.append(take.assign(BANDA=f"[{lo:.2f}, {hi:.2f})", POBLACION_BANDA=len(s)))
    if not piezas:
        return pd.DataFrame(
            columns=[
                "BANDA",
                "POBLACION_BANDA",
                "PAIS_FINAL",
                "SIM_AL_FINAL",
                cfg.col_razon_social,
                "RAZON_SOCIAL_FINAL",
                "VEREDICTO_MANUAL",
            ]
        )
    muestra = pd.concat(piezas, ignore_index=True)
    muestra["VEREDICTO_MANUAL"] = ""  # escriba: OK / ERROR / DUDOSO
    return muestra[
        [
            "BANDA",
            "POBLACION_BANDA",
            "PAIS_FINAL",
            "SIM_AL_FINAL",
            cfg.col_razon_social,
            "RAZON_SOCIAL_FINAL",
            "VEREDICTO_MANUAL",
        ]
    ]


def recall_del_bloqueo(corrida: dict, cfg: ConfigImportadores) -> pd.DataFrame:
    """PC (pair completeness) EXACTO por fuerza bruta en las particiones dadas.

    El bloqueo decide qué pares se llegan a comparar; los que no pasan por él
    son invisibles para el resto del pipeline y no aparecen en ninguna métrica
    posterior. Un bloqueo malo produce un resultado que *parece* impecable.
    Medido aquí: con 64 permutaciones y umbral 0,35 se pierde entre el 19 % y
    el 33 % de los pares verdaderos, sin ningún síntoma.

    Costo O(k²) por partición: elija particiones medianas.
    """
    columnas = [
        "pais",
        "nombres",
        "pares_totales",
        "pares_verdaderos",
        "candidatos",
        "PC_recall_bloqueo",
        "RR_reduccion",
    ]
    if not cfg.paises_auditoria_bloqueo:
        return pd.DataFrame(columns=columnas)
    rep = corrida["representantes"]
    comparador = corrida["comparador"]
    filas = []
    for iso in cfg.paises_auditoria_bloqueo:
        sub = rep[iso == rep.PAIS_ISO3].reset_index(drop=True)
        k = len(sub)
        if k < 2:
            continue
        nombres = sub["NOMBRE_NORM"].to_numpy()
        verdad: set[tuple[int, int]] = set()
        paso = 400
        for a in range(0, k, paso):
            ia = np.arange(a, min(a + paso, k))
            for b in range(a, k, paso):
                jb = np.arange(b, min(b + paso, k))
                fil, col = np.meshgrid(ia, jb, indexing="ij")
                m = fil < col
                if not m.any():
                    continue
                fil, col = fil[m], col[m]
                ok = comparador.compare(nombres[fil], nombres[col]) >= cfg.sim_minima
                verdad.update(zip(fil[ok].tolist(), col[ok].tolist(), strict=True))
        bloqueo = BloqueoComponible(
            [
                LSHTexto(
                    "NOMBRE_NORM",
                    umbral=cfg.lsh_umbral,
                    permutaciones=cfg.lsh_permutaciones,
                    ngram=cfg.lsh_ngram,
                    max_grupo=cfg.lsh_max_grupo,
                    overflow="connect",
                )
            ]
        )
        union, _ = bloqueo.pares({"NOMBRE_NORM": nombres})
        candidatos = set(map(tuple, union.tolist()))
        totales = k * (k - 1) // 2
        filas.append(
            {
                "pais": iso,
                "nombres": k,
                "pares_totales": totales,
                "pares_verdaderos": len(verdad),
                "candidatos": len(candidatos),
                "PC_recall_bloqueo": round(len(verdad & candidatos) / len(verdad), 4)
                if verdad
                else np.nan,
                "RR_reduccion": round(1 - len(candidatos) / totales, 6) if totales else np.nan,
            }
        )
    return pd.DataFrame(filas, columns=columnas)


def sensibilidad_umbral(
    corrida: dict,
    cfg: ConfigImportadores,
    umbrales: Iterable[float] = (0.80, 0.82, 0.84, 0.86, 0.88, 0.90, 0.92),
) -> pd.DataFrame:
    """Cuántas fusiones añade o quita mover el umbral, sin volver a correr todo.

    Usa las decisiones ya calculadas: el número es exacto para el paso de
    scoring. No recalcula la cobertura por estrellas, que solo puede PARTIR
    grupos, nunca unirlos — así que es una cota superior de fusiones.
    """
    umbrales = tuple(float(u) for u in umbrales)
    bajos = sorted(u for u in umbrales if u < cfg.sim_minima_auditoria)
    if bajos:
        raise ValueError(
            f"Qué pasó: se pidió la sensibilidad en umbrales {bajos}, por debajo de "
            f"sim_minima_auditoria={cfg.sim_minima_auditoria}. Por qué importa: `decisiones` "
            f"solo conserva los pares con similitud ≥ ese piso (más los fusionados y los "
            f"vetados), así que el conteo en esos umbrales sería falso. Qué hacer: pida "
            f"umbrales ≥ {cfg.sim_minima_auditoria} o baje sim_minima_auditoria en la "
            f"configuración y vuelva a ejecutar."
        )
    dec = corrida["decisiones"]
    if dec.empty:
        return pd.DataFrame(columns=["umbral_nombre", "pares_fusionados", "vs_defecto"])
    sim01 = similitud_nombre_desde_score(dec["score"].to_numpy(), cfg)
    base = int((sim01 >= cfg.umbral_nombre).sum())
    filas = [
        {
            "umbral_nombre": u,
            "pares_fusionados": int((sim01 >= u).sum()),
            "vs_defecto": int((sim01 >= u).sum()) - base,
        }
        for u in umbrales
    ]
    return pd.DataFrame(filas)


def diccionario_importadores(
    correlativa: pd.DataFrame,
    golden: pd.DataFrame | None,
    revision: pd.DataFrame,
    *,
    renombres: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """``diccionario.csv`` del estándar con las columnas propias de este flujo explicadas.

    Parte de ``contrato.diccionario`` (las fijas con su significado único y las
    de la fuente como «sin cambios») y encima explica las ``COLUMNAS_PROPIAS``
    (``_SIGNIFICADOS_PROPIOS``), añade el matiz de este flujo a las fijas que
    lo tienen (``_MATICES_CONTRATO``) y describe las sumas de ``cols_metricas``
    del golden, que llevan el nombre de la columna de entrada y NO son «la
    columna de la fuente sin cambios». Ninguna columna entregada queda como
    «fuera del contrato».
    """
    fijas = set(contrato.COLUMNAS_CORRELATIVA)
    columnas_fuente = [
        c for c in correlativa.columns if c not in fijas and c not in COLUMNAS_PROPIAS
    ]
    base = contrato.diccionario(
        {"correlativa": correlativa, "golden": golden, "enlaces": None, "revision": revision},
        columnas_fuente=columnas_fuente,
        renombres=renombres,
    )
    es_tabla = base["tabla"].isin(["correlativa", "golden"])
    propia = base["columna"].map(_SIGNIFICADOS_PROPIOS)
    con_propia = es_tabla & propia.notna()
    base.loc[con_propia, "significado"] = propia[con_propia]
    base.loc[con_propia, "origen"] = "motor"
    matiz = base["columna"].map(_MATICES_CONTRATO)
    con_matiz = es_tabla & matiz.notna()
    base.loc[con_matiz, "significado"] = base.loc[con_matiz, "significado"] + " " + matiz[con_matiz]
    if golden is not None:
        sumas = (base["tabla"] == "golden") & base["columna"].isin(columnas_fuente)
        base.loc[sumas, "significado"] = (
            "Suma por grupo de la columna '"
            + base.loc[sumas, "columna"]
            + "' de la fuente (cols_metricas), con su nombre de entrada."
        )
        base.loc[sumas, "origen"] = "motor"
    return base


def _parametros_llamada(cfg: ConfigImportadores) -> dict[str, Any]:
    """La configuración como JSON reproducible (frozensets ordenados, tuplas → listas)."""

    def _json(valor: Any) -> Any:
        if isinstance(valor, frozenset | set):
            return sorted(str(v) for v in valor)
        if isinstance(valor, tuple | list):
            return [_json(v) for v in valor]
        return valor

    return {f.name: _json(getattr(cfg, f.name)) for f in fields(cfg)}


def _configuracion_motor(cfg: ConfigImportadores) -> dict[str, Any]:
    """Los parámetros efectivos en la forma del manifiesto (``config.auditoria``)."""
    return ParametrosMotor(
        perfil="importadores",
        lsh={
            "lsh_permutations": cfg.lsh_permutaciones,
            "lsh_threshold": cfg.lsh_umbral,
            "lsh_ngram": cfg.lsh_ngram,
            "cross_source_only": False,
        },
        scoring={
            "score_threshold": cfg.umbral_score,
            "min_name_similarity": cfg.umbral_nombre,
            "max_nit_distance": None,
        },
        pesos={"NOMBRE_NORM": cfg.peso_nombre, "PAIS_FINAL": cfg.peso_pais},
        prioridad_fuentes=(cfg.nombre_fuente,),
    ).a_dict()


def _manifiesto_importadores(
    df: pd.DataFrame, cfg: ConfigImportadores, tablas: dict[str, pd.DataFrame], corrida: dict
) -> dict[str, Any]:
    """Manifiesto de la corrida con la misma regla que ``linkage()`` (``api._manifiesto``)."""
    from ..api import _manifiesto

    correl = tablas["CORRELATIVA"]
    manifiesto = _manifiesto(
        "deduplicar_importadores", _parametros_llamada(cfg), {cfg.nombre_fuente: df}
    )
    manifiesto["contrato"] = {"version": contrato.VERSION_CONTRATO}
    manifiesto["configuracion"] = _configuracion_motor(cfg)
    manifiesto["dir_trabajo"] = None
    manifiesto["completar"] = {
        "renombres": renombres_por_colision(df, cfg),
        "columnas_fuente": [
            c
            for c in correl.columns
            if c not in contrato.COLUMNAS_CORRELATIVA and c not in COLUMNAS_PROPIAS
        ],
        # La regla que _asignar_id_registro aplicó de verdad (construir_entregables
        # la deja en corrida): no se recompone a partir de los valores.
        "id_registro": corrida["regla_id_registro"],
        "id_grupo": {
            "regla": "entero por orden de primera aparición en ORIGINAL_INDEX, uno por "
            "ID_IMPORTADOR"
        },
        "id_entidad": {
            "regla": "ENT-<16 hex de SHA-256 de los ID_REGISTRO del grupo ordenados> para "
            "todos los grupos: la base no trae identificador"
        },
        "score_par": {
            "origen": "similitud de nombre al líder de la estrella (SIM_AL_FINAL); 1.0 por "
            "igualdad exacta del nombre normalizado; nulo en sin_pareja"
        },
        "confianza": {"origen": "golden.CONFIANZA (la del grupo; golden.metricas)"},
        "columnas_propias": [c for c in COLUMNAS_PROPIAS if c in correl.columns],
    }
    manifiesto["flujo"] = {
        "n_candidatos": int(corrida["n_candidatos"]),
        "n_cortes_cohesion": int(corrida["n_cortes"]),
        "segundos_emparejamiento": round(float(corrida["segundos"]), 3),
        "n_importadores": int(correl["ID_IMPORTADOR"].nunique()),
        "n_empresas_globales": int(correl["ID_EMPRESA_GLOBAL"].nunique()),
        "grupos_en_revision": len(tablas["REVISION_GRUPOS"]),
    }
    return manifiesto


def escribir_importadores(
    resultado: ResultadoImportadores,
    carpeta_salida: Path | str,
    nombre: str = "importadores",
    *,
    marca_tiempo: datetime | None = None,
    excel: bool = True,
) -> Manifiesto:
    """Escribe la carpeta del estándar de una corrida de importadores.

    Es ``exporters.escritor.escribir_resultado`` sobre
    :meth:`ResultadoImportadores.a_resultado_linkage` (el único escritor del
    estándar; atómico; valida el contrato en estricto antes de tocar el
    disco). Deja la carpeta en ``resultado.carpeta_salida`` y devuelve el
    manifiesto. ``paises``, ``muestra``, ``metricas`` e ``invariantes`` siguen
    en memoria (``resultado.tablas()``): no son artefactos del estándar.
    """
    from ..exporters.escritor import escribir_resultado

    manifiesto = escribir_resultado(
        resultado.a_resultado_linkage(),
        Path(carpeta_salida),
        nombre,
        marca_tiempo=marca_tiempo,
        excel=excel,
    )
    resultado.carpeta_salida = manifiesto.carpeta
    return manifiesto


def deduplicar_importadores(
    df: pd.DataFrame,
    cfg: ConfigImportadores | None = None,
    *,
    carpeta_salida: Path | str | None = None,
    nombre: str = "importadores",
    excel: bool = True,
) -> ResultadoImportadores:
    """Fachada: de un DataFrame crudo a la correlativa y su control de calidad.

    Args:
        df: base con al menos las columnas de razón social y país.
        cfg: parámetros; ``None`` usa los defectos medidos.
        carpeta_salida: si se da, escribe la carpeta del estándar
            ``<carpeta_salida>/<AAAA-MM-DD_HHMM>_<nombre>/`` con
            :func:`escribir_importadores` y la deja en ``.carpeta_salida``.
        nombre: nombre simple que cierra el de la carpeta.
        excel: escribir ``excel/`` (completo o LEEME; nunca recorte).

    Returns:
        ``ResultadoImportadores``. Revise ``.todo_ok`` ANTES de usar nada:
        si una invariante falla, el resultado no es utilizable.
    """
    cfg = cfg or ConfigImportadores()
    prep = preparar(df, cfg)
    sugerencias = sugerir_alias_pais(
        prep.loc[prep.PAIS_METODO == "sin_clasificar", "PAIS_NORMALIZADO"],
        catalogo=cfg.catalogo_paises,
    )
    corrida = ejecutar(prep, cfg)
    tablas = construir_entregables(prep, corrida, cfg)
    muestra = muestra_para_revision(tablas, cfg)
    extra = {
        "SENSIBILIDAD": sensibilidad_umbral(corrida, cfg),
        "RECALL_BLOQUEO": recall_del_bloqueo(corrida, cfg),
        "PARAMETROS": pd.DataFrame(
            [(k, str(v)) for k, v in sorted(vars(cfg).items())],
            columns=["parametro", "valor"],
        ),
    }
    if len(sugerencias):
        extra["SUGERENCIAS_PAIS"] = sugerencias
    manifiesto = _manifiesto_importadores(df, cfg, tablas, corrida)
    resultado = ResultadoImportadores(
        correlativa=tablas["CORRELATIVA"],
        golden=tablas["GOLDEN"],
        paises=tablas["PAISES"],
        revision=tablas["REVISION"],
        muestra=muestra,
        metricas=metricas(prep, tablas, corrida, cfg),
        invariantes=verificar_invariantes(prep, tablas, cfg),
        decisiones=corrida["decisiones"],
        comparador=corrida["comparador"],
        preparada=prep,
        representantes=corrida["representantes"],
        sugerencias_pais=sugerencias,
        segundos=corrida["segundos"],
        n_candidatos=corrida["n_candidatos"],
        n_cortes_cohesion=corrida["n_cortes"],
        extra=extra,
        revision_grupos=tablas["REVISION_GRUPOS"],
        diccionario=diccionario_importadores(
            tablas["CORRELATIVA"],
            tablas["GOLDEN"],
            tablas["REVISION"],
            renombres=manifiesto["completar"]["renombres"],
        ),
        manifiesto=manifiesto,
    )
    if carpeta_salida is not None:
        escribir_importadores(resultado, carpeta_salida, nombre, excel=excel)
    return resultado
