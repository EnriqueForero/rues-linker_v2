"""
processing.text — record_linkage_pipeline

Componentes:
    - class TextProcessor  (origen: notebook celda [113])
    - class EnhancedTextProcessor  (origen: notebook celda [148])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from functools import lru_cache
from typing import Any

import pandas as pd

from ..pipeline._internal import DEDUP_CLEANING_MODES
from ..utils.logger import CustomLogger
from ._constants import (
    ADMINISTRATIVE_NOISE,
    CLEANING_MODES,
    STOPWORDS_BASIC,
    VOCABULARIO_SOLO_BLOQUEO,
)

#: v0.17.0 — razones sociales que son estado administrativo, no identidad.
_NOMBRES_PURAMENTE_ADMINISTRATIVOS = frozenset(
    {"PERSONA NATURAL", "PERSONA", "NATURAL", "SIN RAZON SOCIAL", "NO DEFINIDO"}
    | {ruido.strip().upper() for ruido in ADMINISTRATIVE_NOISE}
)


class TextProcessor:
    """
    Procesador de texto optimizado con limpieza agresiva para nombres empresariales.

    Características:
    - Caché LRU para mejor performance
    - Procesamiento vectorizado por chunks
    - Modo AGRESIVO con limpieza especializada:
        * Remoción de códigos registrales (UAP, ALTEX, NIT, RUT, etc.)
        * Eliminación de prefijos/sufijos legales por posición
        * Manejo de problemas de encoding
        * Conversión de puntuación a espacios
        * Limpieza de ruido administrativo
        * Estandarización de símbolos (& -> Y)
        * Validación de nombres no vacíos
    """

    # ═══════════════════════════════════════════════════════════════════════
    # FASE 2 - Paso 2.6: Tabla de traducción de acentos a nivel de CLASE
    # Se crea UNA SOLA VEZ al cargar la clase, no en cada llamada a
    # _strip_accents_impl(). Esto elimina miles de recreaciones redundantes.
    # ═══════════════════════════════════════════════════════════════════════
    _ACCENT_TABLE = str.maketrans(
        "ÁÉÍÓÚÀÈÌÒÙÄËÏÖÜÂÊÎÔÛÑÇáéíóúàèìòùäëïöüâêîôûñç",
        "AEIOUAEIOUAEIOUAEIOUNCAEIOUAEIOUAEIOUAEIOUNC",
    )

    def __init__(self, cleaning_mode: str = "BALANCEADO", cache_size: int = 100_000):
        """
        Inicializar TextProcessor.

        Args:
            cleaning_mode: Modo de limpieza. Default ``'BALANCEADO'``. Opciones:

                - ``'CONSERVADOR'``: limpieza mínima. Elimina TODOS los caracteres
                  no-alfanuméricos vía ``non_alpha_regex`` (incluidas comillas).
                  Usa el set de stopwords más pequeño. Apropiado si el dataset
                  trae nombres muy limpios y no quieres tocar nada agresivamente.
                  **Conocido**: rompe apóstrofes legítimos (``O'CONNOR`` →
                  ``CONNOR``); es comportamiento heredado del notebook fuente.

                - ``'BALANCEADO'`` *(default)*: igual que CONSERVADOR en el
                  tratamiento de caracteres no-alfanuméricos, pero con un set
                  de stopwords más amplio (``CLEANING_MODES['BALANCEADO']``).
                  Recomendado para la mayoría de los casos.
                  **Conocido**: mismo issue de apóstrofes que CONSERVADOR.

                - ``'AGRESIVO'``: limpieza especializada para nombres empresariales
                  colombianos. Aplica, en este orden:

                    0. ``_strip_quote_artifacts`` *(v0.7.1, Sprint 0.8.1)*:
                       remueve comillas duplicadas (``''``, ``""``) y comillas
                       en bordes, preservando apóstrofes legítimos interiores.
                    1. Corrección de mojibake (UTF-8 mal decodificado).
                    2. Remoción de ruido administrativo ("EN LIQUIDACIÓN", etc.).
                    3. Remoción de códigos registrales (NIT, UAP, ALTEX, ...).
                    4. Puntuación → espacio (excepto comillas, ya tratadas).
                    5. Estandarización de símbolos (``&`` → ``Y``) e iniciales.
                    6. Remoción de prefijos legales (``C.I.``, ``S.A. de C.V.``).
                    7. Remoción de sufijos legales (``S.A.S``, ``LTDA``, ...).
                    8. Truncamiento en ``/`` o ``(``.

                  Es el modo usado por ``produccion_calibrada`` (F1=0.84 sobre GT).

            cache_size: Tamaño máximo del caché LRU para ``_clean_name_impl``.
                Default 100_000. Subir si tienes >1M registros con muchos
                nombres repetidos; bajar si la RAM es crítica.

        Raises:
            (ninguno) — si ``cleaning_mode`` no es válido, cae a ``BALANCEADO``
            silenciosamente.
        """
        self.cleaning_mode = cleaning_mode
        self.stopwords = CLEANING_MODES.get(cleaning_mode, CLEANING_MODES["BALANCEADO"])
        self.cache_size = cache_size

        # Configurar logger básico si CustomLogger no existe
        try:
            self.logger = CustomLogger("TextProcessor")
        except:
            self.logger = logging.getLogger("TextProcessor")
            if not self.logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
                self.logger.addHandler(handler)
                self.logger.setLevel(logging.INFO)

        # ============================================================
        # EXPRESIONES REGULARES COMPILADAS (mejor performance)
        # ============================================================

        # Básicas
        self.non_alpha_regex = re.compile(r"[\W_]+")
        self.multiple_spaces_regex = re.compile(r"\s+")

        # Para modo AGRESIVO: conversión de puntuación a espacios
        self.punctuation_to_space_regex = re.compile(r"[.,\-_/\\:()\[\]{}]")

        # Ruido administrativo (Eliminar de cualquier parte)
        self.admin_noise_regex = re.compile(
            r"\b(" + "|".join(map(re.escape, ADMINISTRATIVE_NOISE)) + r")\b", re.IGNORECASE
        )

        # ============================================================
        # PREFIJOS LEGALES (INICIO de cadena)
        # ============================================================
        self.legal_prefixes_patterns = [
            re.compile(r"^S\s*P\s*A\s+", re.IGNORECASE),  # S.P.A.
            re.compile(r"^S\s*A\s*S\s+", re.IGNORECASE),  # S.A.S.
            re.compile(r"^S\s*A\s*U\s+", re.IGNORECASE),  # S.A.U.
            re.compile(r"^S\s*A\s+DE\s+C\s*V\s+", re.IGNORECASE),  # S.A. de C.V.
            # Comercio Internacional (muy común al inicio en Colombia)
            re.compile(
                r"^(?:SOCIEDAD|EMPRESA)?\s*(?:DE)?\s*COMERCIALI[ZS]ACI[OÓ]N\s+INTERNACIONAL\s+",
                re.IGNORECASE,
            ),
            re.compile(r"^C\.?\s*I\.?\s+", re.IGNORECASE),  # C.I. / CI
            re.compile(r"^S\.?\s*C\.?\s*I\.?\s+", re.IGNORECASE),  # S.C.I.
            # Agencias de Aduanas
            re.compile(r"^SOCIEDAD\s+DE\s+INTERMEDIACI[OÓ]N\s+ADUANERA\s+", re.IGNORECASE),
            re.compile(r"^AGENCIA\s+DE\s+ADUANAS?\s+", re.IGNORECASE),
            re.compile(r"^AGENCIA\s+DE\s+ADUANAS?\s+NIVEL\s+\d+\s+", re.IGNORECASE),
            # Otros prefijos genéricos comunes
            re.compile(r"^SUCURSAL\s+(?:DE)?\s+", re.IGNORECASE),
            re.compile(r"^INVERSIONES\s+", re.IGNORECASE),
            re.compile(r"^DISTRIBUIDORA\s+", re.IGNORECASE),
            re.compile(r"^COMERCIALIZADORA\s+", re.IGNORECASE),
            re.compile(r"^INDUSTRIAS?\s+", re.IGNORECASE),
            re.compile(r"^GRUPO\s+(?:EMPRESARIAL)?\s+", re.IGNORECASE),
        ]

        # ============================================================
        # SUFIJOS LEGALES (FINAL de cadena) - Ordenados por longitud
        # ============================================================
        self.legal_suffixes_patterns = [
            # SAS y variaciones (más largo primero)
            re.compile(r"\s+S\.?\s*A\.?\s*S\.?\s+BIC$", re.IGNORECASE),
            re.compile(r"\s+S\.?\s*A\.?\s*S\.?$", re.IGNORECASE),
            re.compile(r"\s+S\.?\s*A\.?\s*U\.?$", re.IGNORECASE),
            re.compile(r"\s+S\.?\s*A\.?$", re.IGNORECASE),
            # Limitadas
            re.compile(r"\s+L\.?T\.?D\.?A\.?$", re.IGNORECASE),
            re.compile(r"\s+LIMITADA$", re.IGNORECASE),
            re.compile(r"\s+LTD\.?$", re.IGNORECASE),
            # Unipersonales
            re.compile(r"\s+E\.?\s*U\.?$", re.IGNORECASE),
            # Comanditarias
            re.compile(r"\s+S\.?\s*EN\s*C\.?\s*A\.?$", re.IGNORECASE),  # S. en C. A.
            re.compile(r"\s+S\.?\s*EN\s*C\.?$", re.IGNORECASE),  # S. en C.
            re.compile(r"\s+S\.?\s*C\.?\s*A\.?$", re.IGNORECASE),  # S.C.A.
            re.compile(r"\s+S\.?\s*C\.?\s*S\.?$", re.IGNORECASE),  # S.C.S.
            # Entidades especiales
            re.compile(r"\s+E\.?\s*S\.?\s*P\.?$", re.IGNORECASE),  # E.S.P.
            re.compile(r"\s+B\.?\s*I\.?\s*C\.?$", re.IGNORECASE),  # B.I.C.
            # Comercio Internacional
            re.compile(r"\s+C\.?\s*I\.?$", re.IGNORECASE),  # C.I.
            re.compile(r"\s+S\.?\s*I\.?\s*A\.?$", re.IGNORECASE),  # S.I.A.
            # Otros
            re.compile(r"\s+ZOMAC$", re.IGNORECASE),
            re.compile(r"\s+SUCURSAL\s+(?:DE\s+)?COLOMBIA$", re.IGNORECASE),
            re.compile(r"\s+Y\s+C(?:I|Í)A\.?$", re.IGNORECASE),
            re.compile(r"\s+&\s+C(?:I|Í)A\.?$", re.IGNORECASE),
            re.compile(r"\s+INC\.?$", re.IGNORECASE),
            re.compile(r"\s+CORP\.?$", re.IGNORECASE),
            re.compile(r"\s+LLC\.?$", re.IGNORECASE),
        ]

        # ============================================================
        # CÓDIGOS REGISTRALES Y RUIDO (Patrones actualizados)
        # ============================================================
        self.code_patterns = [
            # Códigos específicos de Colombia
            re.compile(r"\s+ALTEX\s+(?:COD|CÓDIGO|NO|NRO)?\.?\s*:?\s*\d+", re.IGNORECASE),
            re.compile(r"\s+UAP\s+(?:COD|CÓDIGO|NO|NRO)?\.?\s*:?\s*\d+", re.IGNORECASE),
            re.compile(r"\s+COD\.?\s+UAP\.?\s*:?\s*\d+", re.IGNORECASE),
            re.compile(r"\s+COD\.?\s+ALTEX\.?\s*:?\s*\d+", re.IGNORECASE),
            re.compile(r"\s+CODIGO\s*:?\s*\d+", re.IGNORECASE),
            re.compile(r"\s+NIVEL\s+\d+", re.IGNORECASE),
            # Códigos generales
            re.compile(r"\s+(?:NIT|RUT|RUC|RFC|CUIT)\.?\s*:?\s*[\d\.-]+", re.IGNORECASE),
            re.compile(r"\s+N\.?\s*I\.?\s*T\.?\s*:?\s*[\d\.-]+", re.IGNORECASE),
            # Estados legales
            re.compile(r"\s+EN\s+LIQUIDACION.*$", re.IGNORECASE),
            re.compile(r"\s+EN\s+REORGANIZACION.*$", re.IGNORECASE),
            re.compile(r"\s+EN\s+REESTRUCTURACION.*$", re.IGNORECASE),
            re.compile(r"\s+EN\s+CONCORDATO.*$", re.IGNORECASE),
            re.compile(r"\s+EN\s+TOMA\s+DE\s+POSESION.*$", re.IGNORECASE),
            re.compile(r"\s+EN\s+ACUERDO\s+DE\s+REESTRUCTURACION.*$", re.IGNORECASE),
            # Números sueltos al final (probables códigos internos)
            re.compile(r"\s+\d{3,}$"),
        ]

        # Tabla de traducción para acentos - FASE 2 Paso 2.6: ahora usa la variable de clase
        self.accent_translation = TextProcessor._ACCENT_TABLE

        # Inicializar caché
        self._init_cache()

    def _init_cache(self):
        """Inicializar métodos con caché LRU."""
        self.strip_accents = lru_cache(maxsize=self.cache_size)(self._strip_accents_impl)
        self.clean_name = lru_cache(maxsize=self.cache_size)(self._clean_name_impl)

    @staticmethod
    def _strip_accents_impl(text: str) -> str:
        """
        Implementación de remoción de acentos (para caché).
        FASE 2 - Paso 2.6: Usa tabla de traducción a nivel de CLASE
        (TextProcessor._ACCENT_TABLE) en lugar de crearla en cada llamada.
        """
        if not text:
            return ""

        return text.translate(TextProcessor._ACCENT_TABLE)

    def _fix_encoding_issues(self, text: str) -> str:
        """
        Corregir problemas comunes de encoding en nombres empresariales.

        Args:
            text: Texto con posibles problemas de encoding

        Returns:
            Texto corregido
        """
        # Diccionario de reemplazos comunes de mojibake (UTF-8 mal decodificado
        # como Latin-1). v2.2.0: las claves se definen por sus bytes exactos para
        # evitar colisiones invisibles. En v2.1.0 las claves 'Ã\x8d' (Í) y 'Ã\x81'
        # (Á) habían quedado escritas ambas como 'Ã', colapsando a una sola clave
        # y descartando silenciosamente el mapeo de Í. Bug F601 real (no cosmético).
        replacements = {
            "\xc3\x91": "Ñ",
            "\xc3\x93": "Ó",
            "\xc3\x89": "É",
            "\xc3\x8d": "Í",
            "\xc3\x9a": "Ú",
            "\xc3\x81": "Á",
            "\xc3\xb1": "ñ",
            "\xc3\xb3": "ó",
            "\xc3\xa9": "é",
            "\xc3\xad": "í",
            "\xc3\xba": "ú",
            "\xc3\xa1": "á",
        }

        for bad, good in replacements.items():
            text = text.replace(bad, good)

        return text

    def _clean_initials_and_symbols(self, text: str) -> str:
        """
        Limpia espacios entre iniciales (J. E. -> JE) y estandariza símbolos (& -> Y).

        Args:
            text: Texto a limpiar

        Returns:
            Texto con iniciales y símbolos estandarizados
        """
        # Reemplazar & por Y (importante para "H&M" vs "H Y M")
        text = text.replace("&", " Y ")

        # Eliminar puntos de abreviaturas (S.A.S -> SAS, J.M. -> JM)
        # Solo eliminamos puntos que están entre letras mayúsculas
        text = re.sub(r"\.(?=[A-Z])", "", text)

        return text

    def _remove_patterns(self, text: str, patterns: list[re.Pattern]) -> str:
        """
        Remover múltiples patrones regex de un texto.

        Args:
            text: Texto a limpiar
            patterns: Lista de patrones compilados

        Returns:
            Texto sin los patrones
        """
        for pattern in patterns:
            text = pattern.sub("", text)
        return text.strip()

    def _strip_quote_artifacts(self, text: str) -> str:
        """Elimina comillas literales mal escapadas que vienen del CSV (no del lenguaje).

        Este método ataca el patrón ``''DISENITOS S S ''`` (comillas dobles
        DENTRO del campo, artefacto típico de CSVs mal escapados — por ejemplo
        cuando RUES exporta y dobla comillas para escapar al lado de delimitadores).

        DECISIONES DE DISEÑO (v0.7.1, Sprint 0.8.1, Tarea 1.2):
          • Solo se llama desde modo AGRESIVO. En CONSERVADOR/BALANCEADO el
            ``non_alpha_regex`` ya elimina TODAS las comillas (junto con todo lo
            no-alfanumérico). En AGRESIVO la limpieza es selectiva y por eso
            las comillas literales sobreviven; ahí sí hace falta este sanitizador.
          • Elimina secuencias de DOS o más comillas idénticas (``''`` o ``""``)
            que son inequívocamente artefacto de escape de CSV. Las reemplaza
            por espacio (no por nada) para que ``ACME''CORP`` se vuelva
            ``ACME CORP`` y no ``ACMECORP``.
          • Elimina comillas SIMPLES o DOBLES en los extremos del string
            (envoltura completa del campo).
          • NO toca comillas simples interiores aisladas — preserva apóstrofes
            legítimos como ``O'CONNOR`` o ``DON'T``.
          • Idempotente: aplicar dos veces da el mismo resultado.

        Args:
            text: Texto crudo (puede tener artefactos de comillas).

        Returns:
            Texto con artefactos de comillas removidos.
        """
        if not text:
            return text

        # 1) Comillas duplicadas → espacio (NO cadena vacía, para no pegar palabras).
        #    Aplica a ''   ""   y combinaciones consecutivas (3+ comillas).
        text = re.sub(r"'{2,}", " ", text)
        text = re.sub(r'"{2,}', " ", text)

        # 2) Comillas en los extremos del string (envoltura del campo).
        #    Solo si la comilla está en posición de borde — esto preserva
        #    apóstrofes legítimos como O'CONNOR (no están en el borde).
        text = text.strip()
        while text and text[0] in "'\"":
            text = text[1:].lstrip()
        while text and text[-1] in "'\"":
            text = text[:-1].rstrip()

        # 3) Normalizar espacios múltiples generados por el paso 1.
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _aggressive_clean(self, text: str) -> str:
        """
        Limpieza agresiva especializada para nombres empresariales colombianos.

        Incluye:
        - Corrección de encoding
        - Eliminación de ruido administrativo
        - Remoción de códigos registrales
        - Limpieza de prefijos/sufijos legales
        - Estandarización de símbolos

        Args:
            text: Texto a limpiar

        Returns:
            Texto limpiado agresivamente
        """
        # 0. v0.7.1: Sanear artefactos de comillas literales del CSV
        #    (caso ''DISENITOS S S '' detectado en el first-run, ver tests
        #    tests/test_sprint_0_8_1.py::test_caso_real_first_run_disenitos).
        text = self._strip_quote_artifacts(text)

        # 1. Corregir problemas de encoding
        text = self._fix_encoding_issues(text)

        # 2. Eliminar ruido administrativo (EN LIQUIDACIÓN, PERSONA NATURAL, etc.)
        text = self.admin_noise_regex.sub(" ", text)

        # 3. Eliminar códigos registrales (NIT, UAP, ALTEX, etc.)
        text = self._remove_patterns(text, self.code_patterns)

        # 4. Convertir puntuación a espacios (pero mantener puntos de siglas)
        text = self.punctuation_to_space_regex.sub(" ", text)

        # 5. Estandarizar símbolos (&) y limpiar iniciales
        text = self._clean_initials_and_symbols(text)

        # 6. Eliminar prefijos legales/comerciales (INICIO)
        text = self._remove_patterns(text, self.legal_prefixes_patterns)

        # 7. Eliminar sufijos legales (FINAL)
        # Se ejecuta dos veces por si hay anidación (ej: "SAS BIC")
        text = self._remove_patterns(text, self.legal_suffixes_patterns)
        text = self._remove_patterns(text, self.legal_suffixes_patterns)

        # 8. Eliminar información después de slash o paréntesis
        # Ej: "AGENCIA DE ADUANAS / NIVEL 1" -> "AGENCIA DE ADUANAS"
        text = re.sub(r"\s*[/(].*$", "", text)

        return text.strip()

    def _clean_name_impl(self, raw: str) -> str:
        """
        Implementación de limpieza de nombre (para caché).

        Args:
            raw: Nombre crudo a limpiar

        Returns:
            Nombre limpio según el modo configurado
        """
        # Validación inicial
        if not isinstance(raw, str) or not raw.strip():
            return ""

        # Guardar original para fallback
        original = raw.strip()

        try:
            # Eliminar acentos y convertir a mayúsculas
            text = self.strip_accents(raw.upper())

            # ============================================================
            # MODO AGRESIVO: Limpieza especializada para empresas
            # ============================================================
            if self.cleaning_mode == "AGRESIVO":
                text = self._aggressive_clean(text)
            else:
                # Modo estándar: solo reemplazar no-alfanuméricos
                text = self.non_alpha_regex.sub(" ", text)

            # Normalizar espacios
            text = self.multiple_spaces_regex.sub(" ", text).strip()

            # ============================================================
            # FILTRADO DE STOPWORDS (después de limpieza agresiva)
            # ============================================================
            words = text.split()

            if len(words) > 2:  # Solo filtrar si hay más de 2 palabras
                filtered_words = [w for w in words if w and w not in self.stopwords]

                # v0.17.0 — la salvaguarda mide CONTENIDO, no conteo. El guard
                # anterior (`len < 2`) dejaba pasar residuos como "S S": en el
                # RUES/exportaciones reales, 195 nombres compactos quedaban
                # reducidos a iniciales societarias ("BODEGA DE MODA S.A.S."
                # → "S S") porque la lista de 511 stopwords traga palabras
                # distintivas; esas firmas degeneradas unían empresas ajenas.
                # Regla: el filtrado solo vale si conserva al menos un token
                # distintivo (≥3 caracteres); si no, se degrada a
                # STOPWORDS_BASIC, y en último término se conserva el texto
                # sin filtrar.
                def _tiene_contenido(tokens: list[str]) -> bool:
                    return any(len(t) >= 3 for t in tokens)

                if not _tiene_contenido(filtered_words) and len(words) > 1:
                    filtered_words = [w for w in words if w and w not in STOPWORDS_BASIC]

                if filtered_words and _tiene_contenido(filtered_words):
                    text = " ".join(filtered_words)
                # Si ni el filtro básico conservó contenido, `text` queda con
                # todas sus palabras: mejor un nombre con ruido societario que
                # una firma degenerada que matchea con cualquier cosa.

            # ── v0.17.0: un nombre puramente administrativo no es un nombre.
            # "PERSONA NATURAL" o "EN LIQUIDACION" como razón social completa
            # producían matching de texto entre personas/empresas ajenas (5
            # grupos mixtos medidos en RUES x Exportaciones con NIT vecino).
            # Vacío = sin evidencia de nombre: el par solo puede unir por
            # identificador idéntico, que es la única evidencia real aquí.
            if text.strip() in _NOMBRES_PURAMENTE_ADMINISTRATIVOS:
                return ""

            # ============================================================
            # VALIDACIÓN: El nombre NO puede quedar vacío
            # ============================================================
            if not text or len(text.strip()) == 0:
                # Fallback: retornar versión básica del original
                fallback = self.strip_accents(original.upper())
                fallback = self.non_alpha_regex.sub(" ", fallback)
                fallback = self.multiple_spaces_regex.sub(" ", fallback).strip()

                if fallback:
                    return fallback
                else:
                    # Último recurso: retornar original en mayúsculas
                    return original.upper()

            return text

        except Exception as e:
            self.logger.debug(f"Error limpiando nombre '{raw[:30]}...': {e}")
            # En caso de error, retornar original procesado mínimamente
            return str(raw).upper().strip()

    def derivar_nombre_bloqueo(self, limpios: pd.Series) -> pd.Series:
        """Deriva la firma de bloqueo podando el vocabulario genérico.

        Se calcula SOBRE ``NOMBRE_LIMPIO`` ya normalizado, no re-limpiando el
        crudo: es un filtro de tokens, ~10x más barato que una segunda pasada
        completa sobre millones de filas.

        Salvaguarda de contenido (la misma de ``clean_name``): si podar deja
        el nombre sin ningún token de 3+ caracteres, se conserva el nombre
        limpio íntegro. Un nombre degenerado en la firma es peor que un
        nombre genérico — colisiona con todo.

        Args:
            limpios: Serie con ``NOMBRE_LIMPIO`` (ya en mayúsculas, sin
                acentos ni puntuación).

        Returns:
            Serie del mismo largo e índice con la firma de bloqueo.

        Example:
            >>> tp = TextProcessor(cleaning_mode="BALANCEADO")
            >>> s = pd.Series(["EMPAQUES CAUCA", "MIL DROGAS"])
            >>> tp.derivar_nombre_bloqueo(s).tolist()
            ['CAUCA', 'MIL DROGAS']
        """
        vocabulario = VOCABULARIO_SOLO_BLOQUEO

        def _podar(texto: str) -> str:
            if not texto:
                return texto
            tokens = texto.split()
            podados = [t for t in tokens if t not in vocabulario]
            if not any(len(t) >= 3 for t in podados):
                return texto
            return " ".join(podados)

        base = limpios.astype("string").fillna("")
        # El vocabulario tiene 315 entradas y los nombres se repiten mucho
        # (razones sociales comparten prefijos): cachear por valor único
        # convierte millones de podas en decenas de miles.
        unicos = base.drop_duplicates()
        mapa = dict(zip(unicos, (_podar(v) for v in unicos), strict=True))
        return base.map(mapa).astype("string")

    def process_series(self, series: pd.Series, column_name: str = "text") -> pd.Series:
        """
        Procesar una serie completa de forma vectorizada por chunks.

        Args:
            series: Serie de pandas con texto
            column_name: Nombre de la columna para logging

        Returns:
            Serie procesada
        """
        self.logger.info(
            f"Procesando {len(series):,} textos de columna '{column_name}' en modo {self.cleaning_mode}"
        )

        # Convertir a string y eliminar nulos
        series = series.fillna("").astype(str)

        # Limpieza sobre valores ÚNICOS y mapeo (no fila a fila con .apply).
        # En record linkage hay alta duplicación de nombres entre fuentes; limpiar
        # cada valor único UNA sola vez evita (a) el overhead de dispatch de .apply
        # sobre n filas y (b) el thrash del LRU cuando |únicos| > cache_size, que
        # reprocesa nombres ya limpiados. Output bit-idéntico: misma _clean_name_impl
        # (vía self.clean_name). Cada único se computa exactamente una vez.
        valores_unicos = series.unique()
        mapa_limpieza = {valor: self.clean_name(valor) for valor in valores_unicos}
        result = series.map(mapa_limpieza)

        # Columna de texto en backend PyArrow: ~30-50% menos RAM que 'object',
        # crítico en Colab Free a escala 2M. Centralizado aquí porque TODO el texto
        # limpio del pipeline pasa por este único punto (DRY). Si pyarrow no está
        # disponible, se degrada silenciosamente a 'object'.
        try:
            result = result.astype("string[pyarrow]")
        except (ImportError, TypeError, ValueError):  # pragma: no cover - fallback sin pyarrow
            pass

        # Estadísticas de limpieza
        empty_count = (result == "").sum()
        if empty_count > 0:
            self.logger.warning(
                f"Advertencia: {empty_count:,} textos quedaron vacíos después de limpieza"
            )

        # Info de caché
        if hasattr(self.clean_name, "cache_info"):
            cache_info = self.clean_name.cache_info()
            hit_rate = (
                cache_info.hits / (cache_info.hits + cache_info.misses)
                if cache_info.hits + cache_info.misses > 0
                else 0
            )
            self.logger.info(
                f"Cache hit rate: {hit_rate:.2%} (hits: {cache_info.hits:,}, misses: {cache_info.misses:,})"
            )

        return result

    def extract_words_frequency(self, series: pd.Series, top_n: int = 100) -> pd.DataFrame:
        """
        Extraer frecuencia de palabras de una serie de texto.

        Args:
            series: Serie con texto limpio
            top_n: Número de palabras más frecuentes a retornar

        Returns:
            DataFrame con palabras y frecuencias
        """
        # Combinar todos los textos
        all_text = " ".join(series.dropna().astype(str))

        # Contar palabras
        word_counts = Counter(all_text.split())

        # Crear DataFrame con las más comunes
        most_common = word_counts.most_common(top_n)

        return pd.DataFrame(most_common, columns=["palabra", "frecuencia"])

    def remove_common_words(self, series: pd.Series, threshold: int = 25) -> pd.Series:
        """
        Remover las palabras más comunes de una serie.

        Args:
            series: Serie con texto limpio
            threshold: Número de palabras comunes a remover

        Returns:
            Serie con palabras comunes removidas
        """
        # Obtener palabras más comunes
        word_freq = self.extract_words_frequency(series, top_n=threshold)
        common_words = set(word_freq["palabra"].values)

        self.logger.info(f"Removiendo {len(common_words)} palabras comunes")

        # Remover palabras comunes
        def remove_words(text):
            if not text:
                return text
            words = text.split()
            filtered = [w for w in words if w not in common_words]
            return " ".join(filtered) if filtered else text

        return series.apply(remove_words)

    def clear_cache(self):
        """Limpiar caché para liberar memoria."""
        if hasattr(self.strip_accents, "cache_clear"):
            self.strip_accents.cache_clear()
        if hasattr(self.clean_name, "cache_clear"):
            self.clean_name.cache_clear()
        self.logger.info("Cache limpiado")

    def get_stats(self) -> dict[str, Any]:
        """
        Obtener estadísticas del procesador.

        Returns:
            Diccionario con estadísticas de uso y caché
        """
        stats = {
            "cleaning_mode": self.cleaning_mode,
            "stopwords_count": len(self.stopwords),
            "legal_prefixes": len(self.legal_prefixes_patterns)
            if self.cleaning_mode == "AGRESIVO"
            else 0,
            "legal_suffixes": len(self.legal_suffixes_patterns)
            if self.cleaning_mode == "AGRESIVO"
            else 0,
            "code_patterns": len(self.code_patterns) if self.cleaning_mode == "AGRESIVO" else 0,
            "administrative_noise_terms": len(ADMINISTRATIVE_NOISE),
        }

        # Agregar info de caché si está disponible
        for method_name in ["strip_accents", "clean_name"]:
            method = getattr(self, method_name, None)
            if method and hasattr(method, "cache_info"):
                cache_info = method.cache_info()
                stats[f"{method_name}_cache"] = {
                    "hits": cache_info.hits,
                    "misses": cache_info.misses,
                    "size": cache_info.currsize,
                    "hit_rate": cache_info.hits / (cache_info.hits + cache_info.misses)
                    if cache_info.hits + cache_info.misses > 0
                    else 0,
                }

        return stats


class EnhancedTextProcessor(TextProcessor):
    """
    TextProcessor mejorado que integra el sistema de stopwords
    estratificado del código de deduplicación original.

    Esta clase extiende TextProcessor del framework manteniendo
    100% de compatibilidad con la lógica original.
    """

    def __init__(self, cleaning_mode: str = "BALANCEADO", cache_size: int = 100_000):
        # Inicializar clase padre
        super().__init__(cleaning_mode, cache_size)

        # Migración directa de stopwords estratificadas
        self.stopwords_levels = DEDUP_CLEANING_MODES
        self.active_stopwords = self.stopwords_levels.get(
            cleaning_mode, self.stopwords_levels["BALANCEADO"]
        )

        # Actualizar stopwords de la clase padre
        self.stopwords = self.active_stopwords

        self.logger.info(
            f"EnhancedTextProcessor inicializado con {len(self.active_stopwords)} stopwords"
        )

    @lru_cache(maxsize=100_000)
    def enhanced_clean_name(self, raw_name: str) -> str:
        """
        Integra la lógica exacta de clean_name() del código original
        manteniendo la compatibilidad total.

        Esta es una migración línea por línea del método original.
        """
        # Implementación exacta migrada del código original
        if not isinstance(raw_name, str) or pd.isna(raw_name) or not raw_name.strip():
            return ""

        # Usar strip_accents mejorado
        text = self.strip_accents(raw_name.upper())
        text = self.non_alpha_regex.sub(" ", text).strip()

        # Filtrar stopwords exactamente como el original
        words = [word for word in text.split() if word and word not in self.active_stopwords]

        return " ".join(words) if words else text

    def process_for_deduplication(self, series: pd.Series) -> pd.Series:
        """
        Procesar serie específicamente para deduplicación.
        Usa enhanced_clean_name para mantener compatibilidad.
        """
        self.logger.info(f"Procesando {len(series):,} nombres para deduplicación")

        # Usar el método enhanced para compatibilidad exacta
        return series.apply(self.enhanced_clean_name)
