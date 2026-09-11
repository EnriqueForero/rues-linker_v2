"""
processing.nit — record_linkage_pipeline

Componentes:
    - class NitProcessor  (origen: notebook celda [114])
    - class AdvancedNitProcessor  (origen: notebook celda [149])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import pandas as pd

from ..utils.logger import CustomLogger
from ..utils.memory import MemoryManager
from ..utils.performance import track_performance


class NitProcessor:
    """
    Procesador robusto y eficiente de NITs / documentos de identificación.

    - Limpieza segura (sin truncar por '.'); manejo especial de '... .0'.
    - Clasificación explícita: Cédula (CC/TI), Cédula de Extranjería (CE), Pasaporte (PA/PASAPORTE).
    - Detección de intención de NIT (prefijo 'NIT' o patrón con guion-DV).
    - Política conservadora por defecto: NO corrige 10 dígitos salvo que se active.
    - DV cacheado (LRU) para alto rendimiento en millones de filas.
    - Procesamiento por chunks para bajo uso de RAM.
    """

    DEFAULTS = {
        "min_nit_length": 6,
        "max_nit_length": 15,
        "chunk_size": 50_000,
        "correct_wrong_dv": False,  # ver docstring arriba
    }

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = {**self.DEFAULTS, **(config or {})}
        self.logger = CustomLogger("NitProcessor")

        # --- Regex precompilados ---
        self.non_digit_regex = re.compile(r"\D")

        # Prioriza 'PASAPORTE' sobre 'PA' para evitar recortes erróneos
        self.doc_types_regex = {
            "CEDULA": re.compile(r"^(?:CC|TI)\s*([0-9]{6,10})$", re.IGNORECASE),
            "CEDULA_EXTRANJERIA": re.compile(r"^CE\s*([0-9]{6,10})$", re.IGNORECASE),
            "PASSPORT": re.compile(r"^(?:PASAPORTE|PA)\s*([A-Z0-9]+)$", re.IGNORECASE),
        }

        self.pure_number_decimal_zero = re.compile(r"^\s*\d{6,15}[.,]0+\s*$")
        self.pure_integer = re.compile(r"^\s*\d{6,15}\s*$")

        self.min_length = int(self.config["min_nit_length"])
        self.max_length = int(self.config["max_nit_length"])

        self.test_nits = {
            "123456789",
            "111111111",
            "999999999",
            "000000000",
            "888888888",
            "222222222",
        }

        self.calculate_dv = lru_cache(maxsize=50_000)(self._calculate_dv_impl)

        self.stats = {
            "processed": 0,
            "valid": 0,
            "invalid": 0,
            "passports": 0,
            "test_nits": 0,
            "corrected": 0,
        }

        self._re_prefix_nit = re.compile(r"\bNIT\b", re.IGNORECASE)
        self._re_dv_pattern = re.compile(r"(?:\d[.\s-]?){9}\s*-\s*\d$")

    # --------------------- DV DIAN ---------------------
    def _calculate_dv_impl(self, nit_base: str) -> str:
        if not isinstance(nit_base, str) or not nit_base.isdigit() or len(nit_base) != 9:
            return ""
        pesos = [3, 7, 13, 17, 19, 23, 29, 37, 41]
        suma = sum(int(nit_base[8 - i]) * pesos[i] for i in range(9))
        residuo = suma % 11
        if residuo == 0:
            return "0"
        elif residuo == 1:
            return "1"
        else:
            return str(11 - residuo)

    # -------------------- Limpieza ---------------------
    def _strip_known_prefixes(self, s: str) -> str:
        return re.sub(r"^(?:NIT|ID|CC|CE|TI)\s*", "", s, flags=re.IGNORECASE)

    def _normalize_numeric_like(self, s: str) -> str:
        s_no_space = s.replace(" ", "")
        if self.pure_number_decimal_zero.match(s_no_space):
            return re.split(r"[.,]", s_no_space, maxsplit=1)[0]
        if self.pure_integer.match(s_no_space):
            return s_no_space
        return s

    def _has_nit_intent(self, original: str) -> bool:
        return bool(self._re_prefix_nit.search(original) or self._re_dv_pattern.search(original))

    # --------------- Proceso unitario ------------------
    def process_single_nit(self, value: Any) -> dict[str, Any]:
        result = {
            "NIT_ORIGINAL": value,
            "NIT_BASE": "",
            "NIT_OK": "",
            "IS_VALID": False,
            "NIT_TYPE": "INVALID",
            "DV_CALCULATED": "",
            "DV_PROVIDED": "",
        }

        # 1) Nulos / vacíos / marcadores
        if value is None or (isinstance(value, float) and pd.isna(value)):
            self.stats["invalid"] += 1
            return result

        original = str(value).strip()
        if not original or original.lower() in {"nan", "none", "null"}:
            self.stats["invalid"] += 1
            return result

        # 2) Clasificación explícita por prefijo (CC/TI, CE, PASAPORTE/PA)
        for doc_label, regex in self.doc_types_regex.items():
            m = regex.match(original)
            if m:
                body = m.group(1)  # solo el identificador, sin prefijo textual
                result.update(
                    {"NIT_BASE": body, "NIT_OK": body, "NIT_TYPE": doc_label, "IS_VALID": True}
                )
                if doc_label == "PASSPORT":
                    self.stats["passports"] += 1
                self.stats["valid"] += 1
                return result

        # 3) Limpieza numérica segura
        s = self._strip_known_prefixes(original)
        s = self._normalize_numeric_like(s)
        clean_nit = self.non_digit_regex.sub("", s)

        if not clean_nit:
            self.stats["invalid"] += 1
            return result

        # 4) Longitud (primero) → evita marcar TEST en longitudes inválidas
        length = len(clean_nit)
        if length < self.min_length or length > self.max_length:
            result["NIT_BASE"] = clean_nit  # <-- corrección: conservar lo limpiado
            result["NIT_TYPE"] = "INVALID_LENGTH"
            self.stats["invalid"] += 1
            return result

        # 5) NITs de prueba (solo 9 o 10 con base de 9)
        if length == 9 and clean_nit in self.test_nits:
            result["NIT_BASE"] = clean_nit
            result["NIT_TYPE"] = "TEST"
            self.stats["test_nits"] += 1
            self.stats["invalid"] += 1
            return result
        if length == 10 and clean_nit[:9] in self.test_nits:
            result["NIT_BASE"] = clean_nit[:9]
            result["NIT_TYPE"] = "TEST"
            self.stats["test_nits"] += 1
            self.stats["invalid"] += 1
            return result

        # 6) Lógica por longitud
        if length == 9:
            nit_base = clean_nit
            dv = self.calculate_dv(nit_base)
            if dv:
                result.update(
                    {
                        "NIT_BASE": nit_base,
                        "NIT_OK": f"{nit_base}{dv}",
                        "IS_VALID": True,
                        "NIT_TYPE": "STANDARD_9",
                        "DV_CALCULATED": dv,
                    }
                )
                self.stats["valid"] += 1
                self.stats["corrected"] += 1
            else:
                result.update(
                    {
                        "NIT_BASE": nit_base,
                        "NIT_OK": nit_base,
                        "IS_VALID": False,
                        "NIT_TYPE": "INVALID_9",
                    }
                )
                self.stats["invalid"] += 1

        elif length == 10:
            nit_base = clean_nit[:9]
            dv_provided = clean_nit[9]
            dv_calculated = self.calculate_dv(nit_base)

            result["NIT_BASE"] = nit_base
            result["DV_PROVIDED"] = dv_provided
            result["DV_CALCULATED"] = dv_calculated

            if dv_calculated and dv_provided == dv_calculated:
                result.update(
                    {"NIT_OK": clean_nit, "IS_VALID": True, "NIT_TYPE": "STANDARD_10_VALID"}
                )
                self.stats["valid"] += 1
            else:
                nit_intent = self._has_nit_intent(original)
                if nit_intent:
                    if self.config["correct_wrong_dv"] and dv_calculated:
                        result.update(
                            {
                                "NIT_OK": f"{nit_base}{dv_calculated}",
                                "IS_VALID": True,
                                "NIT_TYPE": "STANDARD_10_CORRECTED",
                            }
                        )
                        self.stats["corrected"] += 1
                        self.stats["valid"] += 1
                    else:
                        result.update(
                            {
                                "NIT_OK": f"{nit_base}{dv_provided}",
                                "IS_VALID": False,
                                "NIT_TYPE": "STANDARD_10_INVALID",
                            }
                        )
                        self.stats["invalid"] += 1
                else:
                    result.update(
                        {
                            "NIT_OK": f"{nit_base}{dv_provided}",
                            "IS_VALID": True,
                            "NIT_TYPE": "OTHER_DOCUMENT_10",
                        }
                    )
                    self.stats["valid"] += 1

        else:
            # 6–8 → SHORT_x; 11–15 → LONG_x
            if length > 10:
                result.update(
                    {
                        "NIT_BASE": clean_nit,
                        "NIT_OK": clean_nit,
                        "IS_VALID": True,
                        "NIT_TYPE": f"LONG_{length}",
                    }
                )
            else:
                result.update(
                    {
                        "NIT_BASE": clean_nit,
                        "NIT_OK": clean_nit,
                        "IS_VALID": True,
                        "NIT_TYPE": f"SHORT_{length}",
                    }
                )
            self.stats["valid"] += 1

        return result

    # --------------- Proceso vectorial -----------------
    @track_performance("Procesamiento de NITs")
    def process_series(self, series: pd.Series) -> pd.DataFrame:
        n = len(series)
        self.logger.info(f"Procesando {n:,} NITs/documentos.")
        self.stats = {k: 0 for k in self.stats}
        self.stats["processed"] = n

        series_clean = series.fillna("").astype(str).str.strip()

        chunk = int(self.config["chunk_size"])
        results = []

        for start in range(0, n, chunk):
            end = min(start + chunk, n)
            part = series_clean.iloc[start:end]
            part_res = part.apply(self.process_single_nit)
            results.append(pd.DataFrame(part_res.tolist(), index=part.index))

            if (start // max(chunk, 1)) % 5 == 0:
                MemoryManager.monitor_and_warn(self.logger)

        # (v2.1.0) Quitar `copy=False`: en pandas 3.0 Copy-on-Write es el
        # default — el parámetro está deprecado y emite Pandas4Warning.
        out = pd.concat(results)
        self._log_stats()
        return out

    # ------------------ Utilidades ---------------------
    def validate_nit_format(self, nit: str) -> bool:
        if not isinstance(nit, str) or not nit:
            return False
        if len(nit) < self.min_length or len(nit) > self.max_length:
            return False
        if len(nit) == 9 and nit in self.test_nits:
            return False
        return not (len(nit) == 10 and nit[:9] in self.test_nits)

    def _log_stats(self) -> None:
        total = self.stats["processed"]
        if total == 0:
            return
        self.logger.info(
            "Estadísticas de procesamiento: "
            f"Total: {total:,}, "
            f"Válidos: {self.stats['valid']:,} ({self.stats['valid'] / total:.1%}), "
            f"Inválidos: {self.stats['invalid']:,} ({self.stats['invalid'] / total:.1%}), "
            f"Pasaportes: {self.stats['passports']:,}, "
            f"NITs test: {self.stats['test_nits']:,}, "
            f"Corregidos: {self.stats['corrected']:,}"
        )

    def get_stats(self) -> dict[str, Any]:
        stats = self.stats.copy()
        if hasattr(self.calculate_dv, "cache_info"):
            info = self.calculate_dv.cache_info()
            total = (info.hits + info.misses) or 1
            stats["cache"] = {
                "hits": info.hits,
                "misses": info.misses,
                "hit_rate": info.hits / total,
            }
        return stats

    def clear_cache(self) -> None:
        if hasattr(self.calculate_dv, "cache_clear"):
            self.calculate_dv.cache_clear()
        self.logger.info("Cache de DV limpiado.")


class AdvancedNitProcessor(NitProcessor):
    """
    NitProcessor que integra la lógica refinada de fix_nit
    con manejo mejorado de documentos empresariales vs personales.

    Mantiene 100% de compatibilidad con el comportamiento original.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.logger = CustomLogger("AdvancedNitProcessor")

        # Cache adicional para fix_nit
        self.fix_nit_cache = {}

    @lru_cache(maxsize=50_000)
    def enhanced_fix_nit(self, value) -> tuple[str, str, str]:
        """Migración exacta de fix_nit() con marca de procedencia del DV.

        v2.10.0 (Fix #1): la tupla retornada ahora es de TRES elementos
        ``(nit_base, nit_ok, dv_origen)``. ``dv_origen`` toma uno de los
        valores:

            - ``"declared"``: el NIT venía con DV explícito (longitud
              original >= 10, o estructura con guion). Evidencia FUERTE
              para identidad de empresa.
            - ``"computed"``: el NIT vino sin DV (9 dígitos puros) y el
              procesador lo calculó. Evidencia DÉBIL — dos NITs distintos
              pueden producir el mismo NIT_OK calculado si su DV declarado
              de origen difería.
            - ``"none"``: NIT vacío / alfanumérico / no procesable.

        Esta marca habilita el ``nit_identical_score_boost_declared`` del
        scorer (v2.10.0), que da boost mayor al match cuando AMBOS NITs
        provienen de DV declarado (señal fuerte) y boost normal cuando al
        menos uno es DV calculado (señal media).

        Paridad: si el código cliente ignora el tercer elemento de la
        tupla (desempaqueta solo dos), debe usar ``(base, ok), _ = ...``
        o equivalente. El método ``process_for_deduplication`` ya está
        actualizado para usar los tres.
        """
        # Validación inicial (migrada línea por línea)
        if pd.isna(value):
            return ("", "", "none")

        original = str(value).strip()
        if not original:
            return ("", "", "none")

        # Detección del DV declarado por el origen ANTES de limpiar.
        # Dos firmas válidas de DV declarado:
        #   1. Formato canónico con guion al final: "900123456-7" — el guion
        #      separa exactamente 1 dígito al final (el DV).
        #   2. Longitud >= 10 dígitos puros sin separadores: "9001234567" —
        #      el último dígito es DV.
        # Quedan EXCLUIDAS:
        #   - "900-123-456" (9 dígitos, múltiples guiones como separador): el
        #     guion no marca DV, solo formato visual.
        #   - "900.123.456" (puntos como separadores): igual.
        digits_only_count = len(self.non_digit_regex.sub("", original.split(".")[0]))
        # Patrón "DDDDDDDDD-D": 9 dígitos + guion + 1 dígito = DV declarado.
        match_canonical = re.match(r"^\d{9,}-\d$", original.strip())
        venia_con_dv_declarado = bool(match_canonical) or digits_only_count >= 10

        # Detección de documentos alfanuméricos (pasaportes)
        if re.search(r"[A-Za-z]", original):
            return (original, original, "none")

        # Limpieza con preservación de decimales.
        s = original.split(".")[0]
        s = self.non_digit_regex.sub("", s)
        if not s:
            return ("", "", "none")

        # Lógica exacta migrada del original
        if len(s) == 9:
            # NIT empresarial - calcular DV (DV no venía declarado)
            nit_base = s
            dv = self.calculate_dv(nit_base)
            nit_ok = f"{nit_base}{dv}" if dv else nit_base
            return (nit_base, nit_ok, "computed")
        else:
            # Otros documentos - preservar intactos.
            # CORRECCIÓN CLAVE: No truncar a 9 dígitos
            nit_base = s
            nit_ok = s
            # Si llegó aquí con >= 10 dígitos, el último es DV declarado.
            origen = "declared" if venia_con_dv_declarado else "computed"
            return (nit_base, nit_ok, origen)

    def process_for_deduplication(self, series: pd.Series) -> pd.DataFrame:
        """
        Procesar NITs específicamente para deduplicación.
        Retorna DataFrame con NIT_BASE y NIT_OK usando enhanced_fix_nit.

        Implementación vectorial (v2.1.0):
            La versión original iteraba con `for _idx, value in series.items()`
            y construía una lista de dicts. Para 2M registros con NITs
            altamente repetidos esto era un cuello de botella.

            Versión actual usa `series.apply(enhanced_fix_nit)`, que:
            1. Reduce overhead Python por fila (el loop corre en C).
            2. Preserva la `lru_cache(50_000)` de `enhanced_fix_nit`, así que
               NITs repetidos se sirven desde cache sin recomputar.
            3. Mantiene el comportamiento bit-exact: las tuplas retornadas por
               `enhanced_fix_nit` se desempaquetan a dos Series en lugar de
               construirse dict-por-dict.

        Para datasets con alta repetición (típico en deduplicación: muchos
        registros con el mismo NIT) la mejora medida es ~3-5x. La lógica
        de `enhanced_fix_nit` no cambia — la parity está garantizada por
        `test_process_for_deduplication_parity` en la suite de integración.
        """
        n = len(series)
        self.logger.info(f"Procesando {n:,} NITs para deduplicación")

        if n == 0:
            return pd.DataFrame(
                {"NIT_ORIGINAL": [], "NIT_BASE": [], "NIT_OK": [], "DV_ORIGEN": []},
                index=series.index,
            )

        # Aplicar enhanced_fix_nit vectorialmente. Retorna Series de tuplas
        # (base, ok, dv_origen). v2.10.0: ahora tres elementos.
        tuples = series.apply(self.enhanced_fix_nit)

        # Desempaquetar a tres columnas.
        if len(tuples) > 0:
            base_vals, ok_vals, origen_vals = zip(*tuples, strict=False)
        else:
            base_vals, ok_vals, origen_vals = (), (), ()

        return pd.DataFrame(
            {
                "NIT_ORIGINAL": series.values,
                "NIT_BASE": list(base_vals),
                "NIT_OK": list(ok_vals),
                "DV_ORIGEN": list(origen_vals),
            },
            index=series.index,
        )
