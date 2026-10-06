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

Exportadores (F2.11):
    `to_excel` y `to_csv` ya no escriben nada por su cuenta: son ALIAS de la
    función libre del estándar `exporters.escritor.exportar_vistas` y avisan
    con `DeprecationWarning`. Los notebooks que los usaban como exportador
    genérico llaman a `exportar_vistas` directamente. La carga perezosa de
    esta clase se retira junto con `RecordLinkagePipeline`.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import pandas as pd

from ..exporters.escritor import exportar_vistas
from .errores import mensaje_accionable


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
    # Exportadores (F6.4 — v2.1.0): desde F2.11 son alias de ``exportar_vistas``
    # ──────────────────────────────────────────────────────────────────
    def _vistas(self, include: tuple[str, ...], metodo: str) -> dict[str, pd.DataFrame]:
        """Las claves de ``include`` que resuelven a un DataFrame NO vacío, en orden."""
        vistas: dict[str, pd.DataFrame] = {}
        for key in include:
            value = self.get(key)
            if isinstance(value, pd.DataFrame) and not value.empty:
                vistas[key] = value
        if not vistas:
            raise ValueError(
                f"{metodo}: ninguna de las claves {include} produjo un DataFrame "
                f"no vacío. Claves disponibles: {list(self.keys())}"
            )
        return vistas

    @staticmethod
    def _avisar(metodo: str) -> None:
        warnings.warn(
            f"PipelineResult.{metodo} es un alias y desaparece con RecordLinkagePipeline: "
            "use record_linkage.exportar_vistas(tablas, carpeta, nombre, ...).",
            DeprecationWarning,
            stacklevel=3,
        )

    def to_excel(
        self,
        path: str | Path,
        include: tuple[str, ...] = ("golden_records", "correlative_table"),
        engine: str = "openpyxl",
    ) -> Path:
        """Alias de ``exportar_vistas(..., libro=True)``: un libro con una hoja por clave.

        Cada clave de ``include`` que resuelva a un DataFrame no vacío es una
        hoja (nombre saneado a 31 caracteres y único); ``path`` debe terminar
        en ``.xlsx`` y el único motor es ``openpyxl`` (el del estándar). Una
        tabla que no cabe en Excel va completa a ``<tronco>__<clave>.csv.gz``
        al lado del libro, como en ``exportar_vistas``; nunca un recorte.

        Returns:
            Ruta absoluta del libro (o del primer archivo escrito si ninguna
            tabla cupo en Excel).

        Raises:
            ValueError: si ninguna clave produce un DataFrame no vacío, si
                ``path`` no es ``.xlsx`` o si ``engine`` no es ``openpyxl``.
        """
        self._avisar("to_excel")
        path = Path(path)
        if path.suffix.lower() != ".xlsx":
            raise ValueError(
                mensaje_accionable(
                    f"to_excel recibió {path.name!r}, que no termina en .xlsx.",
                    "exportar_vistas escribe <carpeta>/<nombre>.xlsx; otro sufijo "
                    "produciría un archivo que no es el pedido.",
                    "Pase una ruta .xlsx o llame a exportar_vistas(tablas, carpeta, nombre).",
                )
            )
        if engine != "openpyxl":
            raise ValueError(
                mensaje_accionable(
                    f"to_excel recibió engine={engine!r}.",
                    "El estándar escribe los libros pequeños con openpyxl; otro motor "
                    "no está probado con la neutralización de hoja de cálculo.",
                    "Quite el parámetro engine (o use exportar_vistas).",
                )
            )
        vistas = self._vistas(include, "to_excel")
        escritas = exportar_vistas(vistas, path.parent, path.stem, libro=True)
        libro = path.resolve()
        return libro if libro in {r.resolve() for r in escritas} else escritas[0].resolve()

    def to_csv(
        self,
        output_dir: str | Path,
        include: tuple[str, ...] = ("golden_records", "correlative_table"),
        index: bool = False,
        encoding: str = "utf-8",
    ) -> dict[str, Path]:
        """Alias de ``exportar_vistas(..., formato="csv")``: un ``<clave>.csv`` por clave.

        Con ``index=True`` el índice entra como primera columna (``reset_index``)
        y recibe la misma neutralización que el resto. Solo UTF-8: es lo que
        ``exportar_vistas`` escribe.

        Returns:
            Dict {clave: Path} con las rutas absolutas de los archivos creados.

        Raises:
            ValueError: si ninguna clave produce un DataFrame no vacío o si
                ``encoding`` no es UTF-8.
        """
        self._avisar("to_csv")
        if encoding.lower().replace("_", "-") not in ("utf-8", "utf8"):
            raise ValueError(
                mensaje_accionable(
                    f"to_csv recibió encoding={encoding!r}.",
                    "exportar_vistas escribe UTF-8, la codificación del estándar; "
                    "otra codificación produciría un archivo distinto del documentado.",
                    "Quite el parámetro encoding; si necesita otra codificación, "
                    "recodifique el archivo después.",
                )
            )
        vistas = self._vistas(include, "to_csv")
        if index:
            vistas = {k: v.reset_index() for k, v in vistas.items()}
        escritas = exportar_vistas(vistas, output_dir, formato="csv")
        return {k: r.resolve() for k, r in zip(vistas, escritas, strict=True)}
