"""
reporting.reports — record_linkage_pipeline

Componentes:
    - class ReportGenerator  (origen: notebook celda [136])

NOTA: el módulo nació como migración literal del notebook (celda [136]);
la lógica de negocio ya NO es la del notebook. F1.5 (v2) cambió el alcance:
los agregados (``analisis_fuentes``, ``estadisticas_grupos`` y los totales
del ``resumen_ejecutivo``) se calculan sobre la tabla COMPLETA. Hasta aquí el
generador cargaba una muestra de ``report_sample_size`` (100.000) filas y a
139k registros el reporte decía «total 99.997» sin rotularlo. Lo único que
sigue recortado —los casos de revisión ilustrativos— lo declara con la
columna ``ALCANCE`` y en ``metrics["muestras"]``; y si quien llamó recortó
las tablas (orquestador bajo RAM crítica) y lo declaró en
``metrics["muestras"]``, ningún reporte presenta esa vista como total.
"""

from __future__ import annotations

import gc
import os
from functools import cached_property
from typing import Any

import pandas as pd

from ..utils.logger import CustomLogger
from ._sqlite import (
    open_readonly_sqlite,
    quote_existing_table,
    quote_sqlite_identifier,
    validate_row_limit,
)

# Columnas de la correlativa que los reportes consumen. Cuando la tabla llega
# por archivo se leen SOLO estas: los agregados son groupby sobre SRC/ID_GRUPO
# y cargar las demás columnas (todas las de la fuente) sería una copia inútil.
COLUMNAS_CORRELATIVA_REPORTES: tuple[str, ...] = ("ID_GRUPO", "SRC")

# Perillas heredadas que ya no recortan nada: se avisa, no se ignora en silencio.
PERILLAS_MUESTRA_RETIRADAS: tuple[str, ...] = ("report_sample_size", "report_chunk_size")

# Tope de casos de revisión ilustrativos (el único reporte que se recorta).
MAX_CASOS_REVISION_POR_DEFECTO = 1000

# Valor de ``ALCANCE`` cuando la cifra cubre toda la tabla (o viene de una
# métrica calculada sobre ella). Lo demás lleva ``MUESTRA (n de N)``.
ALCANCE_COMPLETO = "COMPLETO"

# Métricas del resumen cuyo valor real, si falta la métrica y la tabla llegó
# recortada, es el N declarado de esa tabla en ``metrics["muestras"]``.
TOTAL_DECLARADO_POR_METRICA: dict[str, str] = {
    "total_records": "correlativa",
    "unique_groups": "golden",
}
from ._fases import ETIQUETAS_FASE, formatear_segundos, tiempos_por_fase


class ReportGenerator:
    """
    Generador de reportes V6.0 - Versión final con todas las mejoras.

    Características principales:
    - Manejo inteligente de memoria para datasets grandes
    - Soporte completo para DataFrames en memoria y archivos
    - Generación robusta de reportes con manejo granular de errores
    - Optimización para Google Colab y entornos con memoria limitada
    - Logging detallado para debugging y monitoreo

    Alcance de cada reporte (F1.5):
    - ``resumen_ejecutivo``, ``analisis_fuentes``, ``estadisticas_grupos`` y
      ``metricas_calidad``: sobre la tabla completa (correlativa y golden).
      Un DataFrame recibido se usa por referencia, sin copiar; un archivo se
      lee solo en las columnas necesarias.
    - ``casos_revision``: la prioridad se calcula sobre TODO el golden y el
      reporte lleva los ``max_casos_revision`` primeros. Si recorta, cada fila
      dice ``ALCANCE = MUESTRA (n de N)`` y ``metrics["muestras"]["casos_revision"]``
      registra ``{"n": n, "N": N}``; si no, ``ALCANCE = COMPLETO (N de N)``.
    - Si quien llamó declaró en ``metrics["muestras"]["correlativa"|"golden"]``
      que la tabla llegó recortada (``{"n": n, "N": N}``), ningún reporte dice
      COMPLETO: los agregados llevan ``ALCANCE = MUESTRA (n de N)``, el resumen
      toma los totales y la calidad de las métricas (calculadas antes del
      recorte) o del N declarado, y rotula fila a fila lo que solo pudo salir
      de la vista; ``casos_revision`` añade ``golden_vista`` a su registro.
    - ``metrics["muestras"]`` solo existe si algo se recortó: ausente
      significa «todo completo».
    """

    def __init__(
        self,
        correlative_data: pd.DataFrame | str,
        golden_records_data: pd.DataFrame | str,
        metrics: dict[str, Any],
        config: dict[str, Any] | None = None,
    ):
        """
        Inicializa el generador con datos explícitos y configuración robusta.

        Args:
            correlative_data: DataFrame o ruta a archivo con tabla correlativa
            golden_records_data: DataFrame o ruta a archivo con golden records
            metrics: Diccionario con métricas del proceso. Se conserva la
                referencia: ``generate_all_reports`` escribe en él la clave
                ``muestras`` con lo que se recortó.
            config: Configuración opcional del sistema. ``report_max_casos_revision``
                acota el reporte de casos de revisión (1000 por defecto).
        """
        # La referencia se conserva (no `metrics or {}`): un dict vacío del
        # llamador es donde se registra `muestras`.
        self.metrics = metrics if metrics is not None else {}
        self.config = config or {}
        self.logger = CustomLogger("ReportGenerator")

        self.max_casos_revision = self._validar_tope_casos_revision(
            self.config.get("report_max_casos_revision", MAX_CASOS_REVISION_POR_DEFECTO)
        )
        for perilla in PERILLAS_MUESTRA_RETIRADAS:
            if perilla in self.config:
                self.logger.warning(
                    f"'{perilla}' ya no aplica: los reportes se calculan sobre la tabla "
                    "completa (F1.5). Por qué importa: una muestra sin rótulo daba totales "
                    "falsos. Qué hacer: retire la perilla de la configuración."
                )

        # Logging de diagnóstico mejorado
        self.logger.info("=" * 60)
        self.logger.info("Inicializando ReportGenerator V6.0 - VERSIÓN FINAL")
        self.logger.info(f"Tipo correlative_data: {type(correlative_data)}")
        self.logger.info(f"Tipo golden_records_data: {type(golden_records_data)}")
        self.logger.info(f"Métricas recibidas: {list(self.metrics.keys())}")
        self.logger.info(f"Tope de casos de revisión: {self.max_casos_revision:,}")
        self.logger.info("=" * 60)

        # Guardar referencias originales
        self.correlative_data_ref = correlative_data
        self.golden_records_data_ref = golden_records_data

        # Cargar las tablas completas (la correlativa, solo en sus columnas útiles)
        self.correlativa = self._cargar_tabla(
            correlative_data, "correlative_table", columnas=COLUMNAS_CORRELATIVA_REPORTES
        )
        self.golden_records = self._cargar_tabla(golden_records_data, "golden_records")

        # Validar datos cargados
        self._validate_loaded_data()

        self.logger.info(f"Datos listos - Correlativa: {len(self.correlativa):,} registros")
        self.logger.info(f"Datos listos - Golden: {len(self.golden_records):,} registros")

    @staticmethod
    def _validar_tope_casos_revision(valor: object) -> int:
        """Exige un entero ≥ 1 para ``report_max_casos_revision``; si no, falla rápido.

        Reutiliza ``validate_row_limit`` (rechaza no enteros y negativos) y añade
        el cero: ``head(0)`` dejaría el reporte vacío y ``head(-5)`` lo rotularía
        como «MUESTRA (N-5 de N)», un rótulo absurdo registrado como muestra.
        """
        try:
            tope = validate_row_limit(valor, label="report_max_casos_revision")
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"Qué pasó: report_max_casos_revision={valor!r} no es un entero ≥ 1 ({e}). "
                "Por qué importa: el tope recorta los casos de revisión con head(); un "
                "valor inválido produce un reporte vacío o un rótulo MUESTRA absurdo. "
                "Qué hacer: configure un entero positivo (1000 por defecto)."
            ) from e
        if tope < 1:
            raise ValueError(
                f"Qué pasó: report_max_casos_revision={tope} es cero. Por qué importa: el "
                "reporte de casos de revisión quedaría vacío sin aviso. Qué hacer: "
                "configure un entero positivo (1000 por defecto)."
            )
        return tope

    def _validate_loaded_data(self):
        """Valida que los datos cargados tengan las columnas mínimas necesarias."""
        # Validar tabla correlativa
        if not self.correlativa.empty:
            missing = set(COLUMNAS_CORRELATIVA_REPORTES) - set(self.correlativa.columns)
            if missing:
                self.logger.warning(f"Columnas faltantes en correlativa: {missing}")

        # Validar golden records
        if not self.golden_records.empty:
            required_cols = ["ID_GRUPO", "CONFIDENCE_SCORE"]
            missing = set(required_cols) - set(self.golden_records.columns)
            if missing:
                self.logger.warning(f"Columnas faltantes en golden records: {missing}")

    def _cargar_tabla(
        self,
        data_ref: pd.DataFrame | str,
        table_name: str,
        columnas: tuple[str, ...] | None = None,
    ) -> pd.DataFrame:
        """Carga una tabla COMPLETA desde memoria o archivo.

        Un DataFrame se devuelve por referencia (no se copia ni se muestrea).
        Un archivo se lee entero; si ``columnas`` viene, solo esas columnas (las
        que existan). Si falla la carga se registra el error y se devuelve un
        DataFrame vacío con la estructura mínima, como hacía la versión heredada.
        """
        try:
            if isinstance(data_ref, pd.DataFrame):
                self.logger.debug(f"Usando DataFrame en memoria para '{table_name}'")
                return data_ref

            if isinstance(data_ref, str) and os.path.exists(data_ref):
                self.logger.info(
                    f"Cargando '{table_name}' desde archivo: {os.path.basename(data_ref)}"
                )
                if data_ref.endswith(".db"):
                    return self._load_from_sqlite(data_ref, table_name, columnas)
                return self._leer_archivo_plano(data_ref, table_name, columnas)

        except Exception as e:
            self.logger.error(f"Error cargando '{table_name}': {e!s}")
            self.logger.debug("Stack trace:", exc_info=True)

        # Retornar DataFrame vacío con estructura mínima si falla
        self.logger.warning(f"No se pudo cargar '{table_name}', usando DataFrame vacío")
        return self._create_empty_dataframe(table_name)

    @staticmethod
    def _leer_archivo_plano(
        ruta: str, table_name: str, columnas: tuple[str, ...] | None
    ) -> pd.DataFrame:
        """Lee parquet/CSV/Excel/HDF5 completo; parquet y CSV solo en ``columnas``."""
        if ruta.endswith(".parquet"):
            if columnas is None:
                return pd.read_parquet(ruta)
            import pyarrow.parquet as pq

            presentes = [c for c in columnas if c in pq.read_schema(ruta).names]
            return pd.read_parquet(ruta, columns=presentes or None)

        if ruta.endswith((".csv", ".csv.gz", ".csv.zip")):
            if columnas is None:
                return pd.read_csv(ruta)
            quiero = set(columnas)
            return pd.read_csv(ruta, usecols=lambda c: c in quiero)

        if ruta.endswith((".xlsx", ".xls")):
            return pd.read_excel(ruta)

        if ruta.endswith(".h5"):
            return pd.read_hdf(ruta, key=table_name)

        raise ValueError(
            f"Formato no reconocido para '{table_name}': {os.path.basename(ruta)}. "
            "Se aceptan .db, .parquet, .csv(.gz|.zip), .xlsx/.xls y .h5."
        )

    def _load_from_sqlite(
        self, db_path: str, table_name: str, columnas: tuple[str, ...] | None = None
    ) -> pd.DataFrame:
        """Carga una tabla SQLite completa (solo ``columnas``, si se indican y existen)."""
        try:
            with open_readonly_sqlite(db_path) as conn:
                quoted_table = quote_existing_table(conn, table_name)
                seleccion = "*"
                if columnas is not None:
                    existentes = {
                        str(fila[1]) for fila in conn.execute(f"PRAGMA table_info({quoted_table})")
                    }
                    presentes = [c for c in columnas if c in existentes]
                    if presentes:
                        seleccion = ", ".join(quote_sqlite_identifier(c) for c in presentes)
                return pd.read_sql_query(f"SELECT {seleccion} FROM {quoted_table}", conn)

        except Exception as e:
            self.logger.error(f"Error en consulta SQLite: {e!s}")
            return self._create_empty_dataframe(table_name)

    def _create_empty_dataframe(self, table_name: str) -> pd.DataFrame:
        """Crea DataFrame vacío con estructura esperada."""
        if table_name == "correlative_table":
            return pd.DataFrame(columns=["ID_GRUPO", "SRC", "NIT", "RAZON_SOCIAL"])
        elif table_name == "golden_records":
            return pd.DataFrame(
                columns=["ID_GRUPO", "CONFIDENCE_SCORE", "NIT_FINAL", "RAZON_SOCIAL_FINAL"]
            )
        else:
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Agregados sobre la correlativa completa (se calculan una vez)
    # ------------------------------------------------------------------

    @cached_property
    def _tamanos_grupo(self) -> pd.Series:
        """Registros por ``ID_GRUPO`` sobre toda la correlativa (vacío si no hay columna)."""
        if self.correlativa.empty or "ID_GRUPO" not in self.correlativa.columns:
            return pd.Series(dtype="int64")
        return self.correlativa.groupby("ID_GRUPO").size()

    @cached_property
    def _por_fuente(self) -> pd.DataFrame:
        """Registros y grupos distintos por ``SRC`` sobre toda la correlativa."""
        if self.correlativa.empty or not set(COLUMNAS_CORRELATIVA_REPORTES) <= set(
            self.correlativa.columns
        ):
            return pd.DataFrame(columns=["SRC", "Total_Registros", "Grupos_Unicos"])
        return (
            self.correlativa.groupby("SRC")
            .agg(Total_Registros=("ID_GRUPO", "size"), Grupos_Unicos=("ID_GRUPO", "nunique"))
            .reset_index()
        )

    @cached_property
    def _grupos_multifuente(self) -> int | None:
        """Grupos con más de una fuente, o ``None`` si la correlativa no lo permite."""
        if self.correlativa.empty or not set(COLUMNAS_CORRELATIVA_REPORTES) <= set(
            self.correlativa.columns
        ):
            return None
        return int((self.correlativa.groupby("ID_GRUPO")["SRC"].nunique() > 1).sum())

    def _muestra_declarada(self, tabla: str) -> dict[str, int] | None:
        """``{"n", "N"}`` si quien llamó declaró en ``metrics["muestras"]`` que la
        tabla (``"correlativa"`` o ``"golden"``) llegó recortada; si no, ``None``."""
        muestras = self.metrics.get("muestras")
        declarado = muestras.get(tabla) if isinstance(muestras, dict) else None
        if not isinstance(declarado, dict) or "n" not in declarado or "N" not in declarado:
            return None
        return {"n": int(declarado["n"]), "N": int(declarado["N"])}

    @staticmethod
    def _rotulo_muestra(muestra: dict[str, int]) -> str:
        return f"MUESTRA ({muestra['n']} de {muestra['N']})"

    def _alcance_declarado(self, tabla: str) -> str | None:
        """Rótulo ``MUESTRA (n de N)`` de la tabla declarada recortada, o ``None``."""
        declarado = self._muestra_declarada(tabla)
        return None if declarado is None else self._rotulo_muestra(declarado)

    def _rotular_alcance(self, reporte: pd.DataFrame, tabla: str) -> pd.DataFrame:
        """Añade ``ALCANCE`` al reporte solo si la tabla que lo alimenta llegó recortada."""
        alcance = self._alcance_declarado(tabla)
        if alcance is not None:
            reporte["ALCANCE"] = alcance
        return reporte

    def _cifra_del_resumen(
        self, nombre_metrica: str, valor_tabla: int | float | None, tabla: str
    ) -> tuple[int | float, str]:
        """Cifra del resumen y su alcance: ``(valor, "COMPLETO" | "MUESTRA (n de N)")``.

        - Tabla completa: se prefiere el conteo de la tabla y se avisa si la
          métrica heredada discrepa (pudo salir de una muestra).
        - Tabla declarada recortada en ``metrics["muestras"][tabla]``: la tabla es
          una vista. Se usa la métrica (calculada antes del recorte); si falta y
          la cifra es el total de una tabla (``TOTAL_DECLARADO_POR_METRICA``), el
          N declarado; y si tampoco, el valor de la vista ROTULADO como muestra.
        """
        valor_metrica = self.metrics.get(nombre_metrica)
        declarado = self._muestra_declarada(tabla)
        if declarado is not None:
            if valor_metrica is not None:
                return valor_metrica, ALCANCE_COMPLETO
            tabla_total = TOTAL_DECLARADO_POR_METRICA.get(nombre_metrica)
            total = self._muestra_declarada(tabla_total) if tabla_total else None
            if total is not None:
                return total["N"], ALCANCE_COMPLETO
            return valor_tabla or 0, self._rotulo_muestra(declarado)
        if valor_tabla is None:
            return valor_metrica or 0, ALCANCE_COMPLETO
        if valor_metrica is not None and valor_metrica != valor_tabla:
            self.logger.warning(
                f"metrics['{nombre_metrica}']={valor_metrica:,} difiere de la tabla "
                f"completa ({valor_tabla:,}); el resumen usa la tabla. Por qué importa: "
                "la métrica heredada pudo salir de una muestra. Qué hacer: revise quién "
                "la calcula."
            )
        return valor_tabla, ALCANCE_COMPLETO

    def generate_all_reports(self) -> dict[str, pd.DataFrame]:
        """
        Genera todos los reportes disponibles con manejo robusto de errores.
        Versión mejorada con más reportes y mejor gestión de memoria.
        """
        self.logger.info("Iniciando generación de reportes...")

        # Registro de lo que se recorta: ausente significa que todo es completo.
        # Se conserva lo que declaró quien llamó (p. ej. el orquestador bajo
        # presión de RAM), se regenera solo la entrada propia y la clave se crea
        # únicamente cuando hay algo que registrar.
        muestras = self.metrics.get("muestras")
        if isinstance(muestras, dict):
            muestras.pop("casos_revision", None)

        # Definir todos los reportes disponibles (orden optimizado)
        report_generators = [
            ("resumen_ejecutivo", self._generate_executive_summary, True),  # Siempre generar
            ("metricas_calidad", self._generate_quality_metrics, True),  # Siempre generar
            ("analisis_fuentes", self._generate_source_analysis, False),  # Solo si hay datos
            ("casos_revision", self._generate_review_cases, False),  # Solo si hay datos
            ("estadisticas_grupos", self._generate_group_statistics, False),  # Solo si hay datos
            ("metricas_performance", self._generate_performance_metrics, True),  # Siempre generar
        ]

        reports = {}
        successful = 0
        failed = 0
        skipped = 0

        # Generar cada reporte
        for report_name, generator_func, always_generate in report_generators:
            try:
                # Verificar si debe generarse
                if not always_generate and self._should_skip_report(report_name):
                    self.logger.info(f"⏭️  {report_name}: Omitido (datos insuficientes)")
                    skipped += 1
                    continue

                self.logger.info(f"Generando reporte: {report_name}...")

                # Generar reporte
                report_df = generator_func()

                # Validar resultado
                if report_df is not None and not report_df.empty:
                    reports[report_name] = report_df
                    successful += 1
                    self.logger.info(f"✓ {report_name}: {len(report_df)} filas generadas")
                else:
                    # El generador ya dijo en el log por qué no hay datos (p. ej.
                    # «sin tiempos por fase»); no se escribe ningún archivo.
                    self.logger.info(f"⏭️  {report_name}: Omitido (sin datos)")
                    skipped += 1

            except Exception as e:
                failed += 1
                self.logger.error(f"✗ {report_name}: Error - {e!s}", exc_info=True)
                # Crear reporte de error
                reports[report_name] = pd.DataFrame({"Error": [f"Error generando reporte: {e!s}"]})

        # Liberar memoria después de generar reportes
        gc.collect()

        self.logger.info(
            f"Generación completada: {successful} exitosos, {failed} fallidos, {skipped} omitidos"
        )

        return reports

    def _should_skip_report(self, report_name: str) -> bool:
        """Determina si un reporte debe omitirse por falta de datos."""
        if report_name in ["analisis_fuentes", "estadisticas_grupos"]:
            return self.correlativa.empty
        elif report_name in ["metricas_calidad", "casos_revision"]:
            return self.golden_records.empty
        return False

    def _generate_executive_summary(self) -> pd.DataFrame:
        """
        Genera resumen ejecutivo con métricas principales.

        F1.5: cada fila sabe de dónde sale su cifra. Si alguna tabla llegó
        recortada y declarada, el reporte lleva la columna ``ALCANCE`` con
        ``COMPLETO`` (tabla completa, métrica o N declarado) o ``MUESTRA (n de N)``
        (la cifra solo pudo salir de la vista). Sin recorte no hay columna.
        """
        try:
            summary_data: list[dict[str, str]] = []
            hay_recorte = any(
                self._muestra_declarada(tabla) is not None for tabla in ("correlativa", "golden")
            )

            def fila(
                categoria: str, metrica: str, valor: str, descripcion: str, alcance: str
            ) -> dict[str, str]:
                return {
                    "Categoría": categoria,
                    "Métrica": metrica,
                    "Valor": valor,
                    "Descripción": descripcion,
                    "ALCANCE": alcance,
                }

            # --- MÉTRICAS DE VOLUMEN (sobre la correlativa completa; F1.5) ---
            tamanos = self._tamanos_grupo
            hay_tabla = not tamanos.empty
            total_records, alc_total = self._cifra_del_resumen(
                "total_records", int(tamanos.sum()) if hay_tabla else None, "correlativa"
            )
            unique_groups, alc_grupos = self._cifra_del_resumen(
                "unique_groups", len(tamanos) if hay_tabla else None, "correlativa"
            )
            multi_source_groups, alc_multi = self._cifra_del_resumen(
                "multi_source_groups", self._grupos_multifuente, "correlativa"
            )
            fuentes_tabla = None
            if not self.correlativa.empty and "SRC" in self.correlativa.columns:
                fuentes_tabla = int(self.correlativa["SRC"].nunique())
            num_sources, alc_fuentes = self._cifra_del_resumen(
                "sources_count", fuentes_tabla, "correlativa"
            )

            summary_data.extend(
                [
                    fila(
                        "VOLUMEN",
                        "Total Registros Procesados",
                        f"{int(total_records):,}",
                        "Número total de registros de entrada",
                        alc_total,
                    ),
                    fila(
                        "VOLUMEN",
                        "Entidades Únicas Identificadas",
                        f"{int(unique_groups):,}",
                        "Grupos únicos después del linkage",
                        alc_grupos,
                    ),
                    fila(
                        "VOLUMEN",
                        "Fuentes de Datos",
                        str(int(num_sources)) if num_sources > 0 else "N/A",
                        "Número de fuentes procesadas",
                        alc_fuentes,
                    ),
                    fila(
                        "VOLUMEN",
                        "Entidades Multi-fuente",
                        f"{int(multi_source_groups):,}",
                        "Grupos con registros de múltiples fuentes",
                        alc_multi,
                    ),
                ]
            )

            # --- MÉTRICAS DE EFICIENCIA ---
            linkage_rate = self.metrics.get("linkage_rate", 0)
            reduction_rate = self.metrics.get("reduction_rate", 0)

            # Las métricas de eficiencia vienen del proceso (L2/L3), no de las tablas.
            summary_data.extend(
                [
                    fila(
                        "EFICIENCIA",
                        "Tasa de Linkage",
                        f"{linkage_rate:.2%}",
                        "Porcentaje de registros vinculados",
                        ALCANCE_COMPLETO,
                    ),
                    fila(
                        "EFICIENCIA",
                        "Tasa de Reducción",
                        f"{reduction_rate:.2%}",
                        "Reducción lograda por deduplicación",
                        ALCANCE_COMPLETO,
                    ),
                    fila(
                        "EFICIENCIA",
                        "Candidatos Encontrados",
                        f"{self.metrics.get('candidates_found', 0):,}",
                        "Pares candidatos identificados por LSH",
                        ALCANCE_COMPLETO,
                    ),
                    fila(
                        "EFICIENCIA",
                        "Pares Evaluados",
                        f"{self.metrics.get('pairs_scored', 0):,}",
                        "Pares que pasaron scoring detallado",
                        ALCANCE_COMPLETO,
                    ),
                ]
            )

            # --- MÉTRICAS DE CALIDAD (sobre todo el golden; si llegó recortado,
            # de las métricas calculadas antes del recorte o rotuladas) ---
            if not self.golden_records.empty and "CONFIDENCE_SCORE" in self.golden_records.columns:
                scores = self.golden_records["CONFIDENCE_SCORE"].dropna()
                if len(scores) > 0:
                    promedio, alc_prom = self._cifra_del_resumen(
                        "avg_confidence", float(scores.mean()), "golden"
                    )
                    alta, alc_alta = self._cifra_del_resumen(
                        "high_confidence_count", int((scores > 0.9).sum()), "golden"
                    )
                    baja, alc_baja = self._cifra_del_resumen(
                        "low_confidence_count", int((scores < 0.75).sum()), "golden"
                    )
                    summary_data.extend(
                        [
                            fila(
                                "CALIDAD",
                                "Confidence Promedio",
                                f"{promedio:.3f}",
                                "Score promedio de confianza",
                                alc_prom,
                            ),
                            fila(
                                "CALIDAD",
                                "Registros Alta Confianza (>0.9)",
                                f"{int(alta):,}",
                                "Grupos con confidence superior a 0.9",
                                alc_alta,
                            ),
                            fila(
                                "CALIDAD",
                                "Casos para Revisión (<0.75)",
                                f"{int(baja):,}",
                                "Grupos que requieren revisión manual",
                                alc_baja,
                            ),
                        ]
                    )

            # --- MÉTRICAS DE RENDIMIENTO ---
            exec_time = self.metrics.get("execution_time", 0)
            if exec_time > 0:
                throughput = total_records / exec_time if total_records > 0 else 0

                # Formato de tiempo adaptativo
                if exec_time < 60:
                    time_str = f"{exec_time:.1f} segundos"
                elif exec_time < 3600:
                    time_str = f"{exec_time / 60:.1f} minutos"
                else:
                    time_str = f"{exec_time / 3600:.1f} horas"

                summary_data.extend(
                    [
                        fila(
                            "RENDIMIENTO",
                            "Tiempo Total",
                            time_str,
                            "Duración total del proceso",
                            ALCANCE_COMPLETO,
                        ),
                        fila(
                            "RENDIMIENTO",
                            "Throughput",
                            f"{throughput:.0f} reg/s",
                            "Velocidad de procesamiento",
                            alc_total,
                        ),
                        fila(
                            "RENDIMIENTO",
                            "Memoria Máxima",
                            f"{self.metrics.get('max_memory_gb', 0):.1f} GB",
                            "Uso máximo de memoria",
                            ALCANCE_COMPLETO,
                        ),
                    ]
                )

            resumen = pd.DataFrame(summary_data)
            if not hay_recorte:
                resumen = resumen.drop(columns="ALCANCE")
            return resumen

        except Exception as e:
            self.logger.error(f"Error en resumen ejecutivo: {e!s}")
            return pd.DataFrame(
                {
                    "Categoría": ["ERROR"],
                    "Métrica": ["Error generando resumen"],
                    "Valor": [str(e)],
                    "Descripción": ["Revisar logs para más detalles"],
                }
            )

    def _generate_quality_metrics(self) -> pd.DataFrame:
        """
        Genera reporte detallado de métricas de calidad.
        Incluye análisis de distribución de confidence scores.
        """
        try:
            if self.golden_records.empty or "CONFIDENCE_SCORE" not in self.golden_records.columns:
                return pd.DataFrame({"Mensaje": ["No hay datos de confidence score disponibles"]})

            scores = self.golden_records["CONFIDENCE_SCORE"].dropna()
            if scores.empty:
                return pd.DataFrame({"Mensaje": ["Todos los confidence scores son nulos"]})

            # Definir rangos de confianza mejorados
            bins = [0, 0.5, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0]
            labels = [
                "Muy Baja (<0.5)",
                "Baja (0.5-0.7)",
                "Media (0.7-0.75)",
                "Aceptable (0.75-0.8)",
                "Buena (0.8-0.85)",
                "Alta (0.85-0.9)",
                "Muy Alta (0.9-0.95)",
                "Excelente (0.95-1.0)",
            ]

            # Categorizar scores
            categories = pd.cut(scores, bins=bins, labels=labels, include_lowest=True)
            distribution = categories.value_counts().sort_index()

            # Construir reporte detallado
            quality_data = []
            total = len(scores)
            cumulative = 0

            for label in labels:
                count = distribution.get(label, 0)
                cumulative += count
                pct = (count / total * 100) if total > 0 else 0
                cum_pct = (cumulative / total * 100) if total > 0 else 0

                # Determinar acción recomendada según el nivel
                if "Muy Baja" in label or "Baja" in label:
                    accion = "Revisión urgente"
                elif "Media" in label:
                    accion = "Revisión recomendada"
                elif "Aceptable" in label:
                    accion = "Revisión opcional"
                else:
                    accion = "No requiere revisión"

                quality_data.append(
                    {
                        "Nivel de Confianza": label,
                        "Cantidad": count,
                        "Porcentaje": f"{pct:.1f}%",
                        "Acumulado": f"{cum_pct:.1f}%",
                        "Acción Recomendada": accion,
                    }
                )

            # Agregar estadísticas adicionales al final
            quality_data.extend(
                [
                    {
                        "Nivel de Confianza": "--- ESTADÍSTICAS ---",
                        "Cantidad": "",
                        "Porcentaje": "",
                        "Acumulado": "",
                        "Acción Recomendada": "",
                    },
                    {
                        "Nivel de Confianza": "Media",
                        "Cantidad": f"{scores.mean():.3f}",
                        "Porcentaje": "",
                        "Acumulado": "",
                        "Acción Recomendada": "",
                    },
                    {
                        "Nivel de Confianza": "Mediana",
                        "Cantidad": f"{scores.median():.3f}",
                        "Porcentaje": "",
                        "Acumulado": "",
                        "Acción Recomendada": "",
                    },
                    {
                        "Nivel de Confianza": "Desviación Estándar",
                        "Cantidad": f"{scores.std():.3f}",
                        "Porcentaje": "",
                        "Acumulado": "",
                        "Acción Recomendada": "",
                    },
                ]
            )

            return self._rotular_alcance(pd.DataFrame(quality_data), "golden")

        except Exception as e:
            self.logger.error(f"Error en métricas de calidad: {e!s}")
            return pd.DataFrame({"Error": [f"Error generando métricas: {e!s}"]})

    def _generate_source_analysis(self) -> pd.DataFrame:
        """
        Analiza contribución y calidad por fuente de datos.

        F1.5: groupby sobre la correlativa COMPLETA (``_por_fuente``), venga de
        memoria, archivo o SQLite; antes se calculaba sobre una muestra.
        """
        try:
            source_stats = self._por_fuente
            if source_stats.empty:
                return pd.DataFrame({"Mensaje": ["No hay datos de fuentes disponibles"]})
            source_stats = source_stats.copy()

            # Calcular métricas adicionales
            source_stats["Registros_por_Grupo"] = (
                source_stats["Total_Registros"] / source_stats["Grupos_Unicos"]
            ).round(2)

            # Calcular porcentaje del total
            total_registros = source_stats["Total_Registros"].sum()
            source_stats["Porcentaje_Total"] = (
                source_stats["Total_Registros"] / total_registros * 100
            ).round(1).astype(str) + "%"

            # Si tenemos golden records, agregar confidence promedio por fuente
            if not self.golden_records.empty and "PRIMARY_SOURCE" in self.golden_records.columns:
                confidence_by_source = (
                    self.golden_records.groupby("PRIMARY_SOURCE")["CONFIDENCE_SCORE"]
                    .mean()
                    .round(3)
                )

                source_stats = source_stats.merge(
                    confidence_by_source.rename("Confidence_Promedio"),
                    left_on="SRC",
                    right_index=True,
                    how="left",
                )

            # Ordenar por total de registros
            source_stats = source_stats.sort_values("Total_Registros", ascending=False)

            # Renombrar columnas para presentación
            source_stats = source_stats.rename(columns={"SRC": "Fuente"})

            return self._rotular_alcance(source_stats, "correlativa")

        except Exception as e:
            self.logger.error(f"Error en análisis de fuentes: {e!s}")
            return pd.DataFrame({"Error": [f"Error analizando fuentes: {e!s}"]})

    def _generate_review_cases(self) -> pd.DataFrame:
        """
        Genera lista priorizada de casos para revisión manual.
        Incluye múltiples criterios de priorización.
        """
        try:
            if self.golden_records.empty:
                return pd.DataFrame({"Mensaje": ["No hay golden records para analizar"]})

            # Los factores se calculan sobre TODO el golden, sin copiarlo: solo
            # las filas que pasan el filtro se materializan (F1.5).
            golden = self.golden_records
            factores = pd.DataFrame(index=golden.index)

            # Factor 1: Baja confianza (peso 40%)
            if "CONFIDENCE_SCORE" in golden.columns:
                factores["LOW_CONFIDENCE_FACTOR"] = (1 - golden["CONFIDENCE_SCORE"].fillna(1)) * 40

            # Factor 2: Muchas variaciones de nombres (peso 25%)
            if "NAME_VARIATIONS" in golden.columns:
                factores["NAME_VAR_FACTOR"] = (golden["NAME_VARIATIONS"].fillna(0) / 10).clip(
                    0, 1
                ) * 25

            # Factor 3: Variaciones de NIT (peso 20%)
            if "NIT_VARIATIONS" in golden.columns:
                factores["NIT_VAR_FACTOR"] = (golden["NIT_VARIATIONS"].fillna(0) / 5).clip(
                    0, 1
                ) * 20

            # Factor 4: Tamaño del grupo (peso 15%)
            if "RECORD_COUNT" in golden.columns:
                factores["SIZE_FACTOR"] = (golden["RECORD_COUNT"].fillna(0) / 50).clip(0, 1) * 15

            # Calcular score de prioridad compuesto
            factores["REVIEW_PRIORITY"] = factores.sum(axis=1) if len(factores.columns) else 0.0

            # Filtrar casos que requieren revisión (score > 20)
            requiere = factores["REVIEW_PRIORITY"] > 20
            review_cases = golden.loc[requiere].join(factores.loc[requiere])

            if review_cases.empty:
                return pd.DataFrame(
                    {
                        "Mensaje": ["✅ No hay casos que requieran revisión urgente"],
                        "Detalle": ["Todos los grupos tienen alta confianza"],
                    }
                )

            # Clasificar severidad
            review_cases["SEVERIDAD"] = pd.cut(
                review_cases["REVIEW_PRIORITY"],
                bins=[0, 30, 50, 70, 100],
                labels=["BAJA", "MEDIA", "ALTA", "CRÍTICA"],
            )

            # Ordenar por prioridad descendente
            review_cases = review_cases.sort_values("REVIEW_PRIORITY", ascending=False)

            # Seleccionar columnas relevantes para el reporte
            columns_to_include = []

            # Columnas básicas
            basic_cols = [
                "ID_GRUPO",
                "NIT_FINAL",
                "RAZON_SOCIAL_FINAL",
                "CONFIDENCE_SCORE",
                "SEVERIDAD",
                "REVIEW_PRIORITY",
            ]
            columns_to_include.extend([col for col in basic_cols if col in review_cases.columns])

            # Columnas de detalle
            detail_cols = [
                "SOURCES_COUNT",
                "RECORD_COUNT",
                "NAME_VARIATIONS",
                "NIT_VARIATIONS",
                "PRIMARY_SOURCE",
            ]
            columns_to_include.extend([col for col in detail_cols if col in review_cases.columns])

            # Recortar a los primeros `max_casos_revision` para no sobrecargar el
            # Excel; es el ÚNICO reporte que no lleva toda la tabla y lo declara.
            n_total = len(review_cases)
            final_report = review_cases.head(self.max_casos_revision)
            n_mostrados = len(final_report)

            # Agregar columna de razón principal (vectorizado sobre los factores)
            razon_principal = self._razones_principales(final_report)
            final_report = final_report[columns_to_include].copy()
            final_report["RAZON_PRINCIPAL"] = razon_principal

            # Si el golden mismo es una vista declarada por quien llamó, el
            # total de casos también lo es: nunca se rotula COMPLETO.
            golden_vista = self._muestra_declarada("golden")
            if n_mostrados < n_total or golden_vista is not None:
                rotulo = f"MUESTRA ({n_mostrados} de {n_total}"
                registro: dict[str, Any] = {"n": n_mostrados, "N": n_total}
                if golden_vista is not None:
                    rotulo += (
                        f", sobre una vista de {golden_vista['n']} de {golden_vista['N']} "
                        "del golden"
                    )
                    registro["golden_vista"] = golden_vista
                final_report["ALCANCE"] = rotulo + ")"
                self.metrics.setdefault("muestras", {})["casos_revision"] = registro
                self.logger.info(
                    f"casos_revision: {n_mostrados:,} de {n_total:,} casos (los de mayor "
                    f"prioridad); ALCANCE = {final_report['ALCANCE'].iloc[0]}"
                )
            else:
                final_report["ALCANCE"] = f"COMPLETO ({n_total} de {n_total})"

            return final_report

        except Exception as e:
            self.logger.error(f"Error en casos de revisión: {e!s}")
            return pd.DataFrame({"Error": [f"Error generando casos de revisión: {e!s}"]})

    @staticmethod
    def _razones_principales(casos: pd.DataFrame) -> pd.Series:
        """Razón principal de revisión por caso, vectorizada sobre los factores."""
        umbrales = (
            ("LOW_CONFIDENCE_FACTOR", 15, "Baja confianza"),
            ("NAME_VAR_FACTOR", 10, "Múltiples nombres"),
            ("NIT_VAR_FACTOR", 8, "Múltiples NITs"),
            ("SIZE_FACTOR", 7, "Grupo grande"),
        )
        razones = pd.Series("", index=casos.index, dtype="object")
        for columna, umbral, etiqueta in umbrales:
            if columna not in casos.columns:
                continue
            aplica = casos[columna] > umbral
            separador = razones.where(razones == "", " | ")
            razones = razones.where(~aplica, razones + separador.where(aplica, "") + etiqueta)
        return razones.where(razones != "", "Revisar detalles")

    def _generate_group_statistics(self) -> pd.DataFrame:
        """
        Genera estadísticas detalladas sobre la distribución de grupos.

        F1.5: tamaños de grupo sobre la correlativa COMPLETA (``_tamanos_grupo``);
        antes se calculaban sobre una muestra.
        """
        try:
            group_sizes = self._tamanos_grupo
            if group_sizes.empty:
                return pd.DataFrame({"Mensaje": ["No hay datos de grupos disponibles"]})

            # Categorizar tamaños con rangos más detallados
            bins = [0, 1, 2, 3, 5, 10, 20, 50, 100, float("inf")]
            labels = [
                "1 (Singleton)",
                "2 (Par)",
                "3",
                "4-5",
                "6-10",
                "11-20",
                "21-50",
                "51-100",
                ">100",
            ]

            # 1. Guardar las categorías de cada grupo
            size_categories = pd.cut(group_sizes, bins=bins, labels=labels)

            # 2. Contar cuántos hay en cada categoría
            size_dist = size_categories.value_counts().sort_index()

            # Construir DataFrame de estadísticas
            stats_data = []
            total_groups = size_dist.sum()
            total_records = group_sizes.sum()
            cumulative_groups = 0
            cumulative_records = 0

            for label, count in size_dist.items():
                if count == 0:
                    continue

                # Calcular registros en esta categoría
                if label == "1 (Singleton)":
                    records_in_category = count * 1
                elif label == "2 (Par)":
                    records_in_category = count * 2
                else:
                    # ✅ CORRECCIÓN: Usar la variable `size_categories` para filtrar correctamente
                    range_groups = group_sizes[size_categories == label]
                    records_in_category = range_groups.sum() if len(range_groups) > 0 else 0

                cumulative_groups += count
                cumulative_records += records_in_category

                pct_groups = (count / total_groups * 100) if total_groups > 0 else 0
                pct_records = (
                    (records_in_category / total_records * 100) if total_records > 0 else 0
                )
                cum_pct_groups = (cumulative_groups / total_groups * 100) if total_groups > 0 else 0

                stats_data.append(
                    {
                        "Tamaño": label,
                        "Cantidad_Grupos": count,
                        "Porcentaje_Grupos": f"{pct_groups:.1f}%",
                        "Registros_Estimados": records_in_category,
                        "Porcentaje_Registros": f"{pct_records:.1f}%",
                        "Acumulado_Grupos": f"{cum_pct_groups:.1f}%",
                    }
                )

            # Agregar resumen al final
            stats_data.append(
                {
                    "Tamaño": "--- TOTALES ---",
                    "Cantidad_Grupos": total_groups,
                    "Porcentaje_Grupos": "100.0%",
                    "Registros_Estimados": total_records,
                    "Porcentaje_Registros": "100.0%",
                    "Acumulado_Grupos": "100.0%",
                }
            )

            return self._rotular_alcance(pd.DataFrame(stats_data), "correlativa")

        except Exception as e:
            self.logger.error(f"Error en estadísticas de grupos: {e!s}")
            return pd.DataFrame({"Error": [f"Error generando estadísticas: {e!s}"]})

    def _generate_performance_metrics(self) -> pd.DataFrame:
        """Alias histórico de :meth:`reporte_metricas_performance`."""
        return self.reporte_metricas_performance()

    def reporte_metricas_performance(self) -> pd.DataFrame:
        """
        Reporte ``reporte_metricas_performance.xlsx``: segundos por fase.

        F1.6: los tiempos son EXACTAMENTE ``metrics["phase_times"]`` (los del
        ``manifest.json``), una fila por fase medida más una fila ``TOTAL``
        con la suma. Columnas: ``Fase`` (etiqueta humana), ``Clave`` (clave
        de la fase en el manifiesto), ``Segundos`` (crudo, comparable),
        ``Tiempo`` (legible), ``Porcentaje`` y ``Velocidad``.

        Si no hay tiempos por fase devuelve un DataFrame vacío y
        ``generate_all_reports`` omite el archivo: antes se escribía un Excel
        con filas que buscaban claves inexistentes.
        """
        tiempos = tiempos_por_fase(self.metrics)
        if not tiempos:
            self.logger.warning(
                "reporte_metricas_performance omitido: metrics['phase_times'] no trae "
                "tiempos por fase (el orquestador los cronometra en _exec_phase)."
            )
            return pd.DataFrame()

        total_time = sum(tiempos.values())
        perf_data = [
            {
                "Fase": ETIQUETAS_FASE[clave],
                "Clave": clave,
                "Segundos": segundos,
                "Tiempo": formatear_segundos(segundos),
                "Porcentaje": f"{(segundos / total_time * 100) if total_time > 0 else 0:.1f}%",
                "Velocidad": self._calculate_phase_speed(clave, segundos),
            }
            for clave, segundos in tiempos.items()
        ]
        total_records = self.metrics.get("total_records", 0)
        perf_data.append(
            {
                "Fase": "TOTAL (fases medidas)",
                "Clave": "TOTAL",
                "Segundos": total_time,
                "Tiempo": formatear_segundos(total_time),
                "Porcentaje": "100.0%",
                "Velocidad": f"{total_records / total_time:.0f} reg/seg"
                if total_time > 0
                else "N/A",
            }
        )
        return pd.DataFrame(perf_data)

    def _calculate_phase_speed(self, phase: str, time_val: float) -> str:
        """Velocidad por fase; ``phase`` es la clave del manifiesto (``L2_lsh_candidates``…).

        Si la métrica de conteo de esa fase no viene en ``metrics`` (hoy
        ``_build_metrics`` no entrega ``candidates_found`` ni ``pairs_scored``)
        se responde ``N/A`` en vez de «0 por segundo».
        """
        if time_val <= 0:
            return "N/A"

        conteo_por_fase = {
            "L2_lsh_candidates": ("candidates_found", "candidatos/seg"),
            "L3_scoring": ("pairs_scored", "pares/seg"),
            "L5_golden": ("unique_groups", "grupos/seg"),
        }
        clave_conteo, unidad = conteo_por_fase.get(phase, ("total_records", "reg/seg"))
        items = self.metrics.get(clave_conteo)
        if items is None:
            return "N/A"
        return f"{items / time_val:.0f} {unidad}"
