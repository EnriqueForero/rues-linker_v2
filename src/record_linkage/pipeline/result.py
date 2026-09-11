"""record_linkage.pipeline.result — Contrato de retorno del pipeline.

Define `PipelineResult`, un objeto que encapsula el resultado de
`RecordLinkagePipeline.run()` y `Orchestrator.run()`. Resuelve el bug #4 de
v2.0.0, donde el pipeline borraba `results["correlative_table"]` antes del
return cuando `keep_intermediate_results=False` (default), dejando a los
callers (`deduplicate_unified`, `HyperparameterOptimizer`) sin acceso a la
tabla correlativa.

Diseño (v2.0.1):
    Los DataFrames pesados (golden, correlativa, df_linked) NO se mantienen
    en memoria por defecto. Se cargan bajo demanda desde los checkpoints
    parquet que el pipeline ya escribe a disco. Esto preserva la promesa
    de bajo uso de memoria de `keep_intermediate_results=False` mientras
    garantiza que cualquier caller pueda acceder al resultado final sin
    importar la configuración.

Compatibilidad hacia atrás:
    PipelineResult implementa __getitem__, get(), keys() y __contains__,
    por lo que `result["correlative_table"]` y `result.get("golden_records")`
    siguen funcionando como antes — pero ahora son lazy.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import pandas as pd

from ..exporters._spreadsheet import (
    prepare_spreadsheet_data,
    safe_sheet_name,
    validate_leaf_name,
)


@dataclass
class PipelineResult:
    """Resultado del pipeline de record linkage con carga lazy de DataFrames.

    Los DataFrames pesados se leen desde parquet la primera vez que se
    acceden y se cachean en memoria (cached_property). Si el caller no los
    pide, nunca se cargan — preservando el comportamiento de memoria de
    `keep_intermediate_results=False`.

    Attributes:
        work_dir: Directorio raíz del run (contiene checkpoints/).
        metrics: Diccionario con métricas escalares (no DataFrames):
            tiempos por fase, conteos, tasas de reducción, etc.
        extra: Resultados adicionales que el pipeline quiera adjuntar
            (linkage_diagnostics, load_report, validation_reports, ...).
            Pueden ser DataFrames o dicts; no son lazy.
        golden_path: Ruta al parquet del golden record. Opcional —
            si None, `golden_records` retorna None.
        correlative_path: Ruta al parquet de la tabla correlativa.
        linked_path: Ruta al parquet de df_linked (intermedio).

    Example:
        >>> result = pipeline.run(sources=...)
        >>> df = result.correlative_table  # se carga aquí desde parquet
        >>> df2 = result.correlative_table  # ya cacheada, no relee
        >>> result["correlative_table"]  # compat: acceso dict-like

    """

    work_dir: Path
    metrics: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    golden_path: Path | None = None
    correlative_path: Path | None = None
    linked_path: Path | None = None

    def __post_init__(self) -> None:
        """Normalizar rutas a Path."""
        self.work_dir = Path(self.work_dir)
        if self.golden_path is not None:
            self.golden_path = Path(self.golden_path)
        if self.correlative_path is not None:
            self.correlative_path = Path(self.correlative_path)
        if self.linked_path is not None:
            self.linked_path = Path(self.linked_path)

    # ──────────────────────────────────────────────────────────────────
    # Carga lazy de DataFrames
    # ──────────────────────────────────────────────────────────────────
    @cached_property
    def golden_records(self) -> pd.DataFrame | None:
        """Golden records (un registro por entidad única detectada).

        Returns:
            DataFrame leído del checkpoint parquet, o None si no se generó.
        """
        return self._read_parquet_or_none(self.golden_path)

    @cached_property
    def correlative_table(self) -> pd.DataFrame | None:
        """Tabla correlativa (mapeo registro original → grupo de entidad).

        Returns:
            DataFrame leído del checkpoint parquet, o None si no se generó.
        """
        return self._read_parquet_or_none(self.correlative_path)

    @cached_property
    def df_linked(self) -> pd.DataFrame | None:
        """DataFrame intermedio con ID_GRUPO asignado tras record linkage.

        Returns:
            DataFrame leído del checkpoint parquet, o None si no se generó.
        """
        return self._read_parquet_or_none(self.linked_path)

    @staticmethod
    def _read_parquet_or_none(path: Path | None) -> pd.DataFrame | None:
        """Leer parquet si existe; retornar None si la ruta es None o no existe."""
        if path is None or not path.exists():
            return None
        return pd.read_parquet(path)

    # ──────────────────────────────────────────────────────────────────
    # Compatibilidad dict-like (callers existentes en optimization/, etc.)
    # ──────────────────────────────────────────────────────────────────

    # Mapeo de claves "viejas" del dict de results a propiedades del objeto.
    _DICT_KEY_MAP = {
        "golden_records": "golden_records",
        "correlative_table": "correlative_table",
        "df_linked": "df_linked",
        "metrics": "metrics",
    }

    def __getitem__(self, key: str) -> Any:
        """Acceso dict-like: result["correlative_table"] equivale a result.correlative_table.

        Mantiene compatibilidad con el contrato anterior donde
        `RecordLinkagePipeline.run()` devolvía un dict.
        """
        if key in self._DICT_KEY_MAP:
            return getattr(self, self._DICT_KEY_MAP[key])
        if key in self.extra:
            return self.extra[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        """Get dict-like con default."""
        try:
            value = self[key]
            return default if value is None else value
        except KeyError:
            return default

    def __contains__(self, key: str) -> bool:
        """`"correlative_table" in result` funciona como con un dict."""
        if key in self._DICT_KEY_MAP:
            return getattr(self, self._DICT_KEY_MAP[key]) is not None
        return key in self.extra

    def keys(self) -> Iterator[str]:
        """Iterar claves disponibles (DataFrames cargables + extras).

        Una clave aparece solo si su valor no es None — esto evita yield
        de claves cuyo checkpoint parquet no existe.
        """
        for k in self._DICT_KEY_MAP:
            if k in self:
                yield k
        yield from self.extra.keys()

    def values(self) -> Iterator[Any]:
        """Iterar valores (carga lazy DataFrames cuando se accede)."""
        for k in self.keys():
            yield self[k]

    def items(self) -> Iterator[tuple[str, Any]]:
        """Iterar pares (clave, valor). Habilita `dict(result)` y
        `for k, v in result.items()` — patrón estándar de Mapping.
        """
        for k in self.keys():
            yield k, self[k]

    def __iter__(self) -> Iterator[str]:
        """Habilita `for k in result` (itera claves, como un dict)."""
        return self.keys()

    def __len__(self) -> int:
        """Número de claves disponibles."""
        return sum(1 for _ in self.keys())

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    def to_dict(self, materialize: bool = True) -> dict[str, Any]:
        """Convertir a dict plano.

        Args:
            materialize: Si True (default), carga los DataFrames desde
                disco para que el dict resultante sea autocontenido.
                Si False, deja las rutas en lugar de los DataFrames.
        """
        if materialize:
            return {
                "golden_records": self.golden_records,
                "correlative_table": self.correlative_table,
                "df_linked": self.df_linked,
                "metrics": self.metrics,
                **self.extra,
            }
        return {
            "golden_path": self.golden_path,
            "correlative_path": self.correlative_path,
            "linked_path": self.linked_path,
            "metrics": self.metrics,
            **self.extra,
        }

    # ──────────────────────────────────────────────────────────────────
    # Exportadores (F6.4 — v2.1.0)
    #
    # Métodos de conveniencia para que callers no tengan que materializar
    # los DataFrames manualmente. Internamente usan las cached_property,
    # por lo que respetan la carga lazy: solo se lee del disco lo que se
    # va a exportar.
    # ──────────────────────────────────────────────────────────────────
    def to_excel(
        self,
        path: str | Path,
        include: tuple[str, ...] = ("golden_records", "correlative_table"),
        engine: str = "openpyxl",
    ) -> Path:
        """Exportar resultados a un archivo Excel multi-hoja.

        Cada clave en `include` se escribe a una hoja con el mismo nombre
        (truncado a 31 caracteres por la restricción de Excel). Los valores
        None se omiten silenciosamente.

        Args:
            path: Ruta del .xlsx a crear.
            include: Tupla de claves del resultado a exportar.
                Default: golden_records y correlative_table.
                Acepta cualquier clave que `__getitem__` resuelva a un DataFrame.
            engine: Motor de pandas (`openpyxl` por defecto). Requiere
                `pip install openpyxl`.

        Returns:
            Path absoluto del archivo creado.

        Raises:
            ValueError: si ninguna de las claves resulta en un DataFrame.

        Example:
            >>> result = pipeline.run(sources=...)
            >>> result.to_excel("salida.xlsx")
            >>> # también con sheets personalizados:
            >>> result.to_excel("salida.xlsx", include=("golden_records",))
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Resolver pares (sheet_name, DataFrame) filtrando los None
        sheets: list[tuple[str, pd.DataFrame]] = []
        used_sheet_names: set[str] = set()
        for key in include:
            value = self.get(key)
            if isinstance(value, pd.DataFrame) and not value.empty:
                # Excel limita nombres a 31 chars, prohíbe varios caracteres y
                # compara nombres sin distinguir mayúsculas. El helper también
                # evita colisiones después de truncar/sanear.
                sheet_name = safe_sheet_name(str(key), used_sheet_names)
                sheets.append((sheet_name, value))

        if not sheets:
            raise ValueError(
                f"to_excel: ninguna de las claves {include} produjo un DataFrame "
                f"no vacío. Claves disponibles: {list(self.keys())}"
            )

        with pd.ExcelWriter(path, engine=engine) as writer:
            for sheet_name, df in sheets:
                prepare_spreadsheet_data(df).to_excel(writer, sheet_name=sheet_name, index=False)

        return path.resolve()

    def to_csv(
        self,
        output_dir: str | Path,
        include: tuple[str, ...] = ("golden_records", "correlative_table"),
        index: bool = False,
        encoding: str = "utf-8",
    ) -> dict[str, Path]:
        """Exportar resultados a un directorio con un .csv por DataFrame.

        Args:
            output_dir: Directorio destino (se crea si no existe).
            include: Claves del resultado a exportar.
            index: Si True incluye el índice del DataFrame en el CSV.
            encoding: Codificación de archivo (utf-8 por defecto).

        Returns:
            Dict {clave: Path} con las rutas absolutas de los archivos creados.

        Raises:
            ValueError: si ninguna de las claves resulta en un DataFrame.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        written: dict[str, Path] = {}
        output_root = output_dir.resolve()
        for key in include:
            safe_key = validate_leaf_name(key, "include")
            value = self.get(key)
            if isinstance(value, pd.DataFrame) and not value.empty:
                csv_path = (output_root / f"{safe_key}.csv").resolve()
                if csv_path.parent != output_root:
                    raise ValueError(f"include sale del directorio de exportación: {key!r}")
                prepare_spreadsheet_data(value, include_index=index).to_csv(
                    csv_path, index=index, encoding=encoding
                )
                written[key] = csv_path.resolve()

        if not written:
            raise ValueError(
                f"to_csv: ninguna de las claves {include} produjo un DataFrame "
                f"no vacío. Claves disponibles: {list(self.keys())}"
            )

        return written
