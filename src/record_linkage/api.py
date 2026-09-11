"""API de alto nivel para record linkage multi-fuente.

Expone `linkage()`, el punto de entrada único y recomendado para producción.
Encapsula la construcción de configuración + Orchestrator en una sola llamada,
de modo que el usuario no necesite conocer la estructura interna del paquete.

Diseño:
    - Un solo camino: siempre vía Orchestrator (motor en disco).
    - Multi-fuente y multi-variable de fábrica.
    - `trusted_sources` para fuentes con identidad verificada (NIT confiable).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd


def _feature_columns(extra_features: Sequence[Any] | None) -> list[str]:
    columns: list[str] = []
    for feature in extra_features or ():
        if isinstance(feature, str):
            columns.append(feature)
        elif isinstance(feature, Mapping) and feature.get("column"):
            columns.append(str(feature["column"]))
    return columns


def _ordered_trusted_sources(
    source_order: Sequence[str], trusted_sources: set[str] | list[str] | None
) -> list[str]:
    """Valida y ordena fuentes confiables según el orden efectivo de entrada."""

    if not trusted_sources:
        return []
    trusted_requested = set(trusted_sources)
    unknown_trusted = trusted_requested.difference(source_order)
    if unknown_trusted:
        raise ValueError(
            "trusted_sources contiene fuentes no declaradas: "
            f"{sorted(unknown_trusted)!r}. Disponibles: {list(source_order)!r}."
        )
    return [source for source in source_order if source in trusted_requested]


def _prepare_sources(
    sources: Any,
    *,
    col_name: str,
    col_nit: str,
    col_ciudad: str | None,
    extra_features: Sequence[Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materializa DataFrames/archivos mediante contratos por fuente.

    La ruta histórica ``dict[str, DataFrame]`` queda intacta. Para archivos,
    ``SourceSpec`` permite mapeos distintos, selección de columnas, tipos,
    encoding, delimitador y miembro de archivo por fuente.
    """
    import pandas as pd

    from .ingestion import ColumnType, LoadedSource, SourceSpec, load_source

    if isinstance(sources, Mapping):
        entries = list(sources.items())
    elif isinstance(sources, Sequence) and not isinstance(sources, (str, bytes)):
        if not all(isinstance(item, SourceSpec) for item in sources):
            raise TypeError(
                "Cuando `sources` es una secuencia, todos sus elementos deben ser SourceSpec."
            )
        entries = [(item.name, item) for item in sources]
    else:
        raise TypeError(
            "`sources` debe ser un mapping nombre→DataFrame/Path/SourceSpec "
            "o una secuencia de SourceSpec."
        )

    if not entries:
        raise ValueError("Debe proporcionar al menos una fuente en `sources`.")
    names = [str(name) for name, _ in entries]
    if len(set(names)) != len(names):
        raise ValueError("Los nombres de fuente deben ser únicos.")

    prepared: dict[str, pd.DataFrame] = {}
    reports: dict[str, Any] = {}
    optional_global = {
        **({"CIUDAD": col_ciudad} if col_ciudad else {}),
        **{column: column for column in _feature_columns(extra_features)},
    }

    for raw_name, source in entries:
        name = str(raw_name)
        if isinstance(source, pd.DataFrame):
            frame = source
        else:
            if isinstance(source, LoadedSource):
                loaded = source
                if loaded.report.source_name != name:
                    raise ValueError(
                        f"La fuente '{name}' contiene un LoadedSource de "
                        f"'{loaded.report.source_name}'."
                    )
            else:
                if isinstance(source, SourceSpec):
                    spec = source
                    if spec.name != name:
                        raise ValueError(
                            f"La clave de fuente '{name}' no coincide con "
                            f"SourceSpec.name='{spec.name}'."
                        )
                elif isinstance(source, (str, Path)):
                    # Conveniencia retrocompatible para archivos homogéneos.
                    # Para encabezados distintos use un SourceSpec por fuente.
                    spec = SourceSpec(
                        name=name,
                        path=source,
                        column_mapping={"RAZON_SOCIAL": col_name},
                        optional_column_mapping={
                            "NIT": col_nit,
                            **optional_global,
                        },
                        column_types={"NIT": ColumnType.IDENTIFIER},
                    )
                else:
                    raise TypeError(
                        f"Fuente '{name}': se esperaba DataFrame, Path, "
                        f"SourceSpec o LoadedSource; se recibió {type(source).__name__}."
                    )
                loaded = load_source(spec)
            frame = loaded.data
            reports[name] = loaded.report

        # Una fuente sin identificador es válida: entra al régimen SIN_NIT.
        # Se crea sin mutar el DataFrame del llamador.
        if col_nit not in frame.columns and "NIT" not in frame.columns:
            frame = frame.assign(**{col_nit: pd.Series(pd.NA, index=frame.index, dtype="string")})
        prepared[name] = frame

    return prepared, reports


def _collapse_exact_sources(
    sources: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Colapsa filas completamente idénticas y conserva un plan de expansión.

    ``MultiIndex.from_frame`` sobre toda la entrada retiene una representación
    adicional de cada celda y resulta costoso para tablas anchas. Aquí pandas
    decide los duplicados con su tabla hash (que verifica igualdad), y una
    huella ``uint64`` sirve únicamente para reconstruir los códigos. Si dos
    filas *distintas* comparten huella, se vuelve al camino exacto con
    ``MultiIndex``; una colisión nunca puede fusionar registros silenciosamente.
    """
    import numpy as np
    import pandas as pd

    collapsed: dict[str, pd.DataFrame] = {}
    codes_by_source: dict[str, np.ndarray] = {}
    stats: dict[str, dict[str, int]] = {}
    for name, frame in sources.items():
        try:
            duplicated = frame.duplicated(keep="first").to_numpy(dtype=bool, copy=False)
            representatives = np.flatnonzero(~duplicated)
            compact = frame.iloc[representatives].reset_index(drop=True)

            if len(frame):
                row_hashes = pd.util.hash_pandas_object(frame, index=False).to_numpy(
                    dtype=np.uint64, copy=False
                )
                compact_hashes = row_hashes[representatives]
                if pd.Index(compact_hashes).has_duplicates:
                    # Hash collision between rows distinct according to
                    # DataFrame.duplicated(): use the exact, heavier fallback.
                    row_index = pd.MultiIndex.from_frame(frame)
                    codes, _ = pd.factorize(row_index, sort=False, use_na_sentinel=False)
                    first = ~pd.Series(codes).duplicated().to_numpy()
                    representatives = np.flatnonzero(first)
                    compact = frame.iloc[representatives].reset_index(drop=True)
                else:
                    codes = pd.Index(compact_hashes).get_indexer(row_hashes)
                    if (codes < 0).any():  # pragma: no cover - invariant pandas
                        raise RuntimeError("No se pudo reconstruir el colapso exacto.")
            else:
                codes = np.empty(0, dtype=np.int64)
        except (TypeError, ValueError):
            # Celdas con listas/dicts no son factorizables de forma exacta;
            # conservar todas las filas es la única decisión segura.
            codes = np.arange(len(frame), dtype=np.int64)
            compact = frame.reset_index(drop=True)
        if len(compact) and int(codes.max()) + 1 != len(compact):
            raise RuntimeError("Invariante interna de colapso exacto violada.")
        collapsed[name] = compact
        codes_by_source[name] = np.asarray(codes, dtype=np.int64)
        stats[name] = {
            "input_rows": len(frame),
            "processed_rows": len(compact),
            "collapsed_rows": len(frame) - len(compact),
        }
    return collapsed, {"codes": codes_by_source, "stats": stats}


def _expand_exact_correlative(
    correlative: Any,
    source_order: Sequence[str],
    collapse_plan: dict[str, Any],
) -> Any:
    """Restaura una fila correlativa por fila original tras el enlace compacto."""
    import numpy as np
    import pandas as pd

    take_by_source: list[np.ndarray] = []
    # Evita materializar SRC como object y seleccionar frames completos solo
    # para ordenar una columna; ambas copias son costosas a escala Colab.
    source_values = correlative["SRC"]
    original_index = correlative["ORIGINAL_INDEX"].to_numpy(copy=False)
    for name in source_order:
        codes = collapse_plan["codes"][name]
        positions = np.flatnonzero((source_values == name).to_numpy())
        if len(positions):
            order = np.argsort(original_index[positions], kind="stable")
            positions = positions[order]
        expected = collapse_plan["stats"][name]["processed_rows"]
        if len(positions) != expected:
            raise RuntimeError(
                f"No se puede expandir '{name}': correlativa={len(positions):,}, "
                f"compactadas={expected:,}."
            )
        take_by_source.append(positions[codes])

    if not take_by_source:
        return correlative.iloc[0:0].copy()
    # Un único ``take`` evita retener una copia expandida por fuente y otra
    # copia adicional durante ``concat``; a escala Colab el ahorro es material.
    take = np.concatenate(take_by_source)
    del take_by_source
    # ``take`` ya devuelve un frame nuevo. Reasignar su índice evita la copia
    # adicional de ``reset_index(drop=True).copy()``.
    expanded = correlative.take(take)
    del take
    expanded.index = pd.RangeIndex(len(expanded))
    expanded["ORIGINAL_INDEX"] = np.arange(len(expanded), dtype=np.int64)
    return expanded


def linkage(
    sources: Any,
    *,
    trusted_sources: set[str] | list[str] | None = None,
    col_name: str = "RAZON_SOCIAL",
    col_nit: str = "NIT",
    col_ciudad: str | None = "CIUDAD",
    extra_features: list[str] | None = None,
    work_dir: str | None = None,
    profile: str = "produccion_estandar",
    ajustes_perfil: dict[str, Any] | None = None,
    matching_profile: Any = None,
    return_matcher_audit: bool = False,
    matcher_max_pairs: int | None = 5_000_000,
    skip_reporting: bool | None = None,
    collapse_exact_duplicates: bool = False,
    consume_sources: bool = False,
) -> dict[str, Any]:
    """Ejecuta deduplicación + record linkage multi-fuente en una sola llamada.

    Esta es la API de alto nivel recomendada. Internamente usa el `Orchestrator`
    (motor en disco), que es el único camino que separa correctamente los
    regímenes CON_NIT y SIN_NIT cuando se mezclan fuentes.

    Args:
        sources: dict {nombre_fuente: DataFrame}. Una entrada por fuente. Cada
            DataFrame debe tener al menos la columna `col_name`.
        trusted_sources: nombres de fuentes confiables (NIT verificado). Estas
            fuentes no se deduplican internamente (se asume que ya están limpias)
            y tienen prioridad para el nombre canónico del golden record. Ej:
            {"RUES", "SUPERSOCIEDADES"}.
        col_name: nombre de la columna de razón social. Default "RAZON_SOCIAL".
            Si difiere, el pipeline la renombra al canónico en la ingesta
            (v0.12.0); las salidas usan siempre nombres canónicos.
        col_nit: nombre de la columna de NIT. Default "NIT". Si una fuente no
            tiene NIT, déjala vacía ("") en esa fuente.
        col_ciudad: nombre de la columna de ciudad, o None si no aplica.
        extra_features: variables adicionales para el scoring. Cada elemento
            es un string (nombre de columna; peso 0.05 y tipo
            'categorical_signed' por defecto) o un dict
            ``{"column": "TELEFONO", "weight": 0.10, "type":
            "categorical_signed"}``. Tipos válidos: los de
            ``deduplication.unified`` (exact_or_zero, categorical,
            token_set_ratio y sus variantes _signed). v0.12.0: estas
            variables SÍ llegan al scorer del Orchestrator; hasta 0.11.x se
            descartaban en silencio en esta ruta.
        work_dir: directorio de trabajo. Todos los archivos intermedios (SQLite,
            parquet) se escriben ahí. Si es None, se usa un temporal.
        ajustes_perfil: sobrescrituras puntuales del perfil, p. ej.
            ``{"lsh_permutations": 256, "score_threshold": 0.55,
            "cleaning_mode": "AGRESIVO"}``. Una clave desconocida falla de
            inmediato con una sugerencia del nombre correcto, en vez de
            ignorarse en silencio. Las claves disponibles son las del perfil
            (véase ``PERFILES_BASE``): umbrales LSH y de score, permutaciones,
            n-grama, distancia máxima de NIT, similitud mínima de nombre, modo
            de limpieza, pesos, tamaños de lote y memoria.
        profile: perfil de configuración. Ver `record_linkage.config.profiles`.
            Para fuentes sin NIT usar "deduplication_sin_nit_conservador".
        matching_profile: MatchingProfile (de
            ``record_linkage.matching``) o string para activar refinamiento
            multi-variable post-clustering. Opciones:
              - None (default): pipeline core sin refinamiento. Comportamiento
                idéntico al comportamiento sin matcher.
              - "colombia": aplica ``default_colombia_profile()``.
              - "international": aplica ``default_international_profile()``.
              - Instancia de MatchingProfile: usa ese profile exacto.
            El refinamiento SEPARA clusters predichos donde no se cumplen las
            reglas multi-variable. Sube precision sin afectar recall LSH.
            Útil sobre todo para mejorar SIN_NIT donde el scorer plano da FP.
        return_matcher_audit: si True y matching_profile no es None, incluye
            en el resultado las claves "matcher_stats" (métricas de la refinación)
            y "matcher_decisions" (DataFrame con scores por variable para cada
            par evaluado). Útil para auditoría/debugging. Default False.
        consume_sources: contrato opt-in de propiedad. Si True, ``sources``
            debe ser un ``dict`` mutable y se vacía tras completar L1. Permite
            liberar las últimas referencias del llamador en flujos de gran
            volumen. Default False: nunca muta el diccionario de entrada.

    Returns:
        dict con dos claves base:
            - "golden": DataFrame con un registro por entidad única (deduplicado).
            - "correlative": DataFrame que mapea cada registro original a su
              ID_GRUPO.
        Si ``return_matcher_audit=True`` y ``matching_profile`` está activo:
            - "matcher_stats": dict con métricas de la refinación.
            - "matcher_decisions": DataFrame con decisión por par evaluado.

    Ejemplo (con matcher):
        >>> from record_linkage import linkage
        >>> result = linkage(
        ...     sources={"RUES": df_rues, "DIAN": df_dian, "CRM": df_crm},
        ...     trusted_sources={"RUES"},
        ...     col_ciudad="CIUDAD",
        ...     extra_features=["TELEFONO", "EMAIL"],
        ...     matching_profile="colombia",  # NUEVO: refinamiento multi-variable
        ...     return_matcher_audit=True,
        ... )
        >>> print(result["matcher_stats"])  # cuántos clusters se separaron
        >>> result["correlative"].to_parquet("correlativa.parquet")

    Ejemplo (sin matcher):
        >>> result = linkage(sources={"RUES": df})  # mismo comportamiento que antes

    Nota sobre calidad medida (ground truth sintético):
        Las cifras del docstring previas (F1 ≈ 0.875) NO eran trazables.
        Para benchmarks reproducibles, ejecutar:
            scripts/benchmark_e2e_matcher.py
        ADVERTENCIA: todo está medido sobre GT sintético. No es sustituto
        de medición sobre datos reales etiquetados (RUES). Ver
        scripts/active_labeling.py para construir tu ground truth real.
    """
    import tempfile

    from .config.profiles import crear_config_orchestrator
    from .pipeline.orchestrator import Orchestrator

    if consume_sources and not isinstance(sources, dict):
        raise TypeError("consume_sources=True requiere sources como dict mutable")
    owned_sources = sources if consume_sources else None

    sources, ingestion_reports = _prepare_sources(
        sources,
        col_name=col_name,
        col_nit=col_nit,
        col_ciudad=col_ciudad,
        extra_features=extra_features,
    )

    source_order = list(sources)
    collapse_plan: dict[str, Any] | None = None
    if collapse_exact_duplicates:
        sources, collapse_plan = _collapse_exact_sources(sources)

    if work_dir is None:
        work_dir = tempfile.mkdtemp(prefix="rues_linker_")

    # Un ``set`` no tiene orden estable entre procesos. Ordenar por el orden
    # efectivo de las fuentes mantiene deterministas la configuración y las
    # huellas de checkpoint sin cambiar la semántica del parámetro.
    trusted = _ordered_trusted_sources(source_order, trusted_sources)

    # v0.12.0: col_name/col_nit/col_ciudad/extra_features son parámetros de
    # primera clase de crear_config_orchestrator (hasta 0.11.x viajaban como
    # overrides del perfil y se DESCARTABAN en silencio — hallazgo H1 de la
    # auditoría). El Orchestrator renombra las columnas del usuario a las
    # canónicas en la ingesta; las salidas usan nombres canónicos
    # (RAZON_SOCIAL, NIT, CIUDAD) y el mapeo queda en config["column_mapping"].
    config = crear_config_orchestrator(
        perfil=profile,
        workspace=work_dir,
        col_name=col_name,
        col_nit=col_nit,
        col_ciudad=col_ciudad,
        extra_features=extra_features,
        trusted_unique_sources=trusted,
        **(ajustes_perfil or {}),
    )
    if owned_sources is not None:
        # Orchestrator debe recibir exactamente el mapping cuya propiedad se
        # cedió; así puede vaciar las referencias del llamador después de L1.
        owned_sources.clear()
        owned_sources.update(sources)
        sources = owned_sources
    orchestrator = Orchestrator(
        config=config,
        sources=sources,
        work_dir=work_dir,
        consume_sources=consume_sources,
    )
    reporting_requested = (
        not bool(orchestrator.profile.get("skip_reporting", False))
        if skip_reporting is None
        else not skip_reporting
    )
    # Si habrá refinamiento, L6 debe ejecutarse DESPUÉS: de otro modo los
    # archivos de reporte describirían los clusters previos al matcher.
    needs_postprocessing = matching_profile is not None or collapse_plan is not None
    result = orchestrator.run(skip_reporting=True if needs_postprocessing else skip_reporting)
    if ingestion_reports:
        result["ingestion_reports"] = ingestion_reports

    # ── Refinamiento opt-in con MatcherPostProcessor ──────────
    if matching_profile is not None:
        from .matching import (
            MatcherPostProcessor,
            default_colombia_profile,
            default_international_profile,
        )
        from .matching.spec import MatchingProfile as MatchingProfileCls

        # Resolver string → instancia
        if isinstance(matching_profile, str):
            from .matching import (
                default_colombia_profile_conservative,
                default_colombia_profile_recall,
            )

            if matching_profile in ("colombia", "colombia_balanced"):
                profile_obj = default_colombia_profile()
            elif matching_profile == "colombia_conservative":
                profile_obj = default_colombia_profile_conservative()
            elif matching_profile == "colombia_recall":
                profile_obj = default_colombia_profile_recall()
            elif matching_profile == "international":
                profile_obj = default_international_profile()
            else:
                raise ValueError(
                    f"matching_profile string desconocido: '{matching_profile}'. "
                    "Opciones: 'colombia' (balanced, default), "
                    "'colombia_conservative' (max precision), "
                    "'colombia_recall' (max recall), 'international', "
                    "o instancia de MatchingProfile."
                )
        elif isinstance(matching_profile, MatchingProfileCls):
            profile_obj = matching_profile
        else:
            raise TypeError(
                f"matching_profile debe ser str o MatchingProfile, recibido "
                f"{type(matching_profile).__name__}"
            )

        # La correlativa ya contiene el esquema canónico y el orden exacto de
        # L1. Usarla como source evita concatenar de nuevo todas las fuentes
        # (pico de RAM) y evita desalineación cuando cada fuente usa nombres
        # de columna diferentes.
        import numpy as _np

        matcher_id = "__RL_MATCHER_ROW_ID__"
        combined_source = result["correlative"].copy()
        combined_source[matcher_id] = _np.arange(len(combined_source)).astype(str)

        # Aplicar post-procesador
        postproc = MatcherPostProcessor(
            profile_obj,
            verbose=False,
            max_pairs=matcher_max_pairs,
            allow_missing_optional=True,
        )
        refined_correlative = postproc.apply(
            combined_source,
            combined_source,
            id_col=matcher_id,
        )

        # Recalcular el golden con la misma selección canónica de L5. Tomar
        # simplemente la primera fila por grupo pierde consenso, prioridad de
        # fuente y métricas de calidad.
        from .golden.generator import GoldenRecordGeneratorV7

        priority = list(orchestrator.profile.get("source_quality_weights", {}).keys())
        if not priority:
            priority = list(orchestrator.sources.keys())
        refined_golden, refined_correlative = GoldenRecordGeneratorV7(priority, config).generate(
            refined_correlative
        )
        refined_golden = refined_golden.drop(columns=[matcher_id], errors="ignore")
        refined_correlative = refined_correlative.drop(columns=[matcher_id], errors="ignore")

        result["correlative"] = refined_correlative
        result["golden"] = refined_golden

        if return_matcher_audit:
            result["matcher_stats"] = postproc.last_stats
            result["matcher_decisions"] = postproc.decisions_log

    if collapse_plan is not None:
        result["correlative"] = _expand_exact_correlative(
            result["correlative"], source_order, collapse_plan
        )
        result["preprocessing"] = {"exact_duplicate_collapse": collapse_plan["stats"]}

        # Diferenciar registros únicos procesados de filas de entrada
        # restauradas, sin alterar la semántica histórica de RECORD_COUNT.
        input_counts = result["correlative"].groupby("ID_GRUPO", sort=False).size()
        result["golden"]["INPUT_ROW_COUNT"] = (
            result["golden"]["ID_GRUPO"].map(input_counts).fillna(0).astype("int64")
        )

    if needs_postprocessing and reporting_requested:
        # Los checkpoints L5 pertenecen al resultado compacto/anterior al
        # matcher. Exportar desde memoria evita reportes obsoletos.
        config["reporting_use_checkpoints"] = False
        report_files, _ = orchestrator._run_L6(result)
        result["report_files"] = report_files

    return result


# ═══════════════════════════════════════════════════════════════════════════
# FACHADA CANÓNICA (F1, v0.9.0): ResultadoLinkage · dedupe · link
# ═══════════════════════════════════════════════════════════════════════════

#: Semilla global del pipeline (determinismo contractual, ver F0.6).
_SEED_GLOBAL = 42


@dataclass
class ResultadoLinkage:
    """Resultado tipado de la fachada (F1.5): datos + métricas + trazabilidad.

    Attributes:
        correlativa: mapeo registro→ID_GRUPO (una fila por registro de entrada).
        golden: un registro canónico por entidad, o None si la ruta no lo
            produce en memoria (``dedupe`` los escribe por régimen en
            ``metricas['output_dir']``).
        metricas: conteos de la corrida y estadísticas del pipeline.
        manifiesto: trazabilidad total — función, timestamp UTC, seed,
            parámetros y su hash, huella de cada insumo, versiones del entorno.
    """

    correlativa: pd.DataFrame
    golden: pd.DataFrame | None
    metricas: dict[str, Any]
    manifiesto: dict[str, Any]

    def resumen(self) -> str:
        """Resumen humano de una línea (para logs y actas)."""
        m, man = self.metricas, self.manifiesto
        return (
            f"{man.get('funcion', '?')}: {m.get('n_registros', '?')} registros "
            f"→ {m.get('n_grupos', '?')} grupos únicos "
            f"(rues-linker {man.get('versiones', {}).get('rues-linker', '?')}, "
            f"hash_parametros {man.get('hash_parametros', '?')})"
        )


def _huella_dataset(df: pd.DataFrame) -> str:
    """Huella SHA-256 (16 hex) de esquema y contenido completo por chunks.

    Permite auditar que una corrida se hizo sobre ESE insumo exacto sin
    almacenar los datos (mismo patrón que la key del cache MinHash).
    """
    from .pipeline.fingerprints import fingerprint_dataframe

    return fingerprint_dataframe(df)[:16]


def _versiones_entorno() -> dict[str, str]:
    """Versiones de la librería y dependencias que alteran resultados."""
    from importlib.metadata import PackageNotFoundError, version

    out: dict[str, str] = {}
    for paq in ("rues-linker", "datasketch", "pandas", "numpy", "networkx", "rapidfuzz"):
        try:
            out[paq] = version(paq)
        except PackageNotFoundError:  # pragma: no cover - entorno incompleto
            out[paq] = "no-instalado"
    return out


def _manifiesto(
    funcion: str,
    parametros: dict[str, Any],
    entradas: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    """Manifiesto de corrida (F1.5): qué corrió, con qué parámetros, sobre qué."""
    from datetime import datetime, timezone

    hash_par = hashlib.sha256(
        json.dumps(parametros, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    return {
        "funcion": funcion,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": _SEED_GLOBAL,
        "parametros": parametros,
        "hash_parametros": hash_par,
        "entradas": {
            nombre: {
                "filas": len(d),
                "columnas": list(d.columns),
                "huella": _huella_dataset(d),
            }
            for nombre, d in entradas.items()
        },
        "versiones": _versiones_entorno(),
    }


def _preflight(df: Any, columnas: list[str], nombre_arg: str) -> None:
    """Validación fail-fast con mensajes accionables (F1.4).

    Formato de todo error: qué pasó / por qué importa / qué hacer.
    """
    import pandas as pd

    if not isinstance(df, pd.DataFrame):
        raise TypeError(
            f"Qué pasó: `{nombre_arg}` es {type(df).__name__}, no un DataFrame. "
            f"Por qué importa: el pipeline opera sobre pandas. "
            f"Qué hacer: conviértalo con pd.DataFrame(datos) o cargue con "
            f"pd.read_csv/read_parquet/read_excel."
        )
    if df.empty:
        raise ValueError(
            f"Qué pasó: `{nombre_arg}` está vacío (0 filas). "
            f"Por qué importa: no hay nada que enlazar y un resultado vacío "
            f"suele esconder un error de carga aguas arriba. "
            f"Qué hacer: verifique la ruta/filtros con los que cargó el insumo."
        )
    faltantes = [c for c in columnas if c not in df.columns]
    if faltantes:
        raise ValueError(
            f"Qué pasó: a `{nombre_arg}` le faltan las columnas {faltantes} "
            f"(tiene: {list(df.columns)}). "
            f"Por qué importa: son el insumo del matching. "
            f"Qué hacer: renombre (df.rename(columns={{...}})) o pase "
            f"col_nit=/col_name= con los nombres reales; si la fuente no tiene "
            f"NIT, cree la columna vacía: df['{faltantes[0]}'] = ''."
        )


def dedupe(
    df: pd.DataFrame,
    *,
    col_nit: str = "NIT",
    col_name: str = "RAZON_SOCIAL",
    mode: str = "AGRESIVO",
    profile_con_nit: str | None = None,
    profile_sin_nit: str | None = None,
    output_dir: str | None = None,
) -> ResultadoLinkage:
    """Deduplica UNA tabla por la ruta canónica de producción (F1.1).

    Envuelve `deduplicate_auto` (enrutamiento CON_NIT/SIN_NIT con perfiles
    validados; baseline v0_9_0 y canario de percolación la protegen) sin
    transformar los datos: la correlativa es bit a bit idéntica a la de la
    ruta directa (paridad verificada sobre el GT de 12.427, CHANGELOG 0.9.0).

    Args:
        df: tabla con al menos ``col_nit`` y ``col_name``.
        col_nit: columna de NIT (vacío/None → régimen SIN_NIT).
        col_name: columna de razón social.
        mode: modo de `deduplicate_unified` (default "AGRESIVO").
        profile_con_nit: perfil para el régimen CON_NIT; None = default
            validado de la ruta auto. Ver ``get_profile``/``REGISTRO_PERFILES``.
        profile_sin_nit: ídem para SIN_NIT.
        output_dir: carpeta de salida (reportes y golden por régimen). None →
            temporal; la ruta queda en ``metricas['output_dir']``.

    Returns:
        ResultadoLinkage. ``golden`` es None aquí: los golden y reportes por
        régimen quedan escritos en ``metricas['output_dir']``.

    Raises:
        TypeError | ValueError: preflight accionable (qué pasó / por qué
            importa / qué hacer).

    Ejemplo:
        >>> import pandas as pd, record_linkage as rl
        >>> df = pd.read_parquet("empresas.parquet")
        >>> res = rl.dedupe(df)
        >>> print(res.resumen())
        >>> res.correlativa.to_parquet("correlativa.parquet")
    """
    import tempfile

    from .deduplication.auto import deduplicate_auto

    _preflight(df, [col_nit, col_name], "df")
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="rues_linker_dedupe_")

    kwargs: dict[str, Any] = {}
    if profile_con_nit is not None:
        kwargs["profile_con_nit"] = profile_con_nit
    if profile_sin_nit is not None:
        kwargs["profile_sin_nit"] = profile_sin_nit

    corr, stats = deduplicate_auto(
        df_input=df,
        col_nit=col_nit,
        col_name=col_name,
        mode=mode,
        output_dir=str(output_dir),
        **kwargs,
    )
    metricas: dict[str, Any] = {
        "n_registros": len(corr),
        "n_grupos": int(corr["ID_GRUPO"].nunique()),
        "n_registros_con_nit": int((corr["REGIMEN_AUTO"] == "CON_NIT").sum()),
        "n_registros_sin_nit": int((corr["REGIMEN_AUTO"] == "SIN_NIT").sum()),
        "stats_pipeline": stats,
        "output_dir": str(output_dir),
    }
    manifiesto = _manifiesto(
        "dedupe",
        {
            "col_nit": col_nit,
            "col_name": col_name,
            "mode": mode,
            "profile_con_nit": profile_con_nit,
            "profile_sin_nit": profile_sin_nit,
        },
        {"df": df},
    )
    return ResultadoLinkage(correlativa=corr, golden=None, metricas=metricas, manifiesto=manifiesto)


def link(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    *,
    nombre_a: str = "A",
    nombre_b: str = "B",
    trusted: set[str] | list[str] | None = None,
    col_nit: str = "NIT",
    col_name: str = "RAZON_SOCIAL",
    col_ciudad: str | None = "CIUDAD",
    extra_features: list[str] | None = None,
    profile: str = "produccion_estandar",
    matching_profile: Any = None,
    work_dir: str | None = None,
    matcher_max_pairs: int | None = 5_000_000,
    skip_reporting: bool | None = None,
    collapse_exact_duplicates: bool = False,
) -> ResultadoLinkage:
    """Cruza DOS tablas (record linkage A↔B) sobre el Orchestrator (F1.1).

    Un match = ambos registros comparten ``ID_GRUPO`` en la correlativa (la
    columna ``SRC`` dice de qué tabla vino cada uno). Envuelve ``linkage()``
    con dos fuentes, añadiendo preflight, métricas de cruce y manifiesto.

    Args:
        df_a, df_b: tablas con ``col_nit`` y ``col_name`` (NIT puede ir vacío).
        nombre_a, nombre_b: etiquetas de fuente (aparecen en ``SRC``).
        trusted: fuentes con identidad verificada (p. ej. {"RUES"}).
        col_ciudad: columna de ciudad o None si no aplica.
        extra_features: columnas adicionales para el scoring.
        profile: plantilla del Orchestrator (``PERFILES_BASE``).
        matching_profile: refinamiento multi-variable opcional (ver linkage()).
        work_dir: carpeta de trabajo; None → temporal.

    Returns:
        ResultadoLinkage con ``golden`` multi-fuente y, en ``metricas``:
        ``n_grupos_cruzados`` (entidades presentes en AMBAS tablas) y
        ``n_pares_a_b`` (pares registro-a-registro implicados).

    Ejemplo:
        >>> res = rl.link(df_rues, df_aduanas, nombre_a="RUES",
        ...               nombre_b="ADUANAS", trusted={"RUES"})
        >>> cruz = res.correlativa.groupby("ID_GRUPO")["SRC"].nunique()
        >>> res.correlativa[res.correlativa["ID_GRUPO"].isin(cruz[cruz > 1].index)]
    """
    _preflight(df_a, [col_nit, col_name], "df_a")
    _preflight(df_b, [col_nit, col_name], "df_b")
    if nombre_a == nombre_b:
        raise ValueError(
            f"Qué pasó: nombre_a y nombre_b son iguales ('{nombre_a}'). "
            f"Por qué importa: la columna SRC no podría distinguir las fuentes "
            f"y las métricas de cruce serían falsas. "
            f"Qué hacer: use etiquetas distintas, p. ej. nombre_a='RUES', "
            f"nombre_b='ADUANAS'."
        )

    res = linkage(
        sources={nombre_a: df_a, nombre_b: df_b},
        trusted_sources=trusted,
        col_name=col_name,
        col_nit=col_nit,
        col_ciudad=col_ciudad,
        extra_features=extra_features,
        work_dir=work_dir,
        profile=profile,
        matching_profile=matching_profile,
        matcher_max_pairs=matcher_max_pairs,
        skip_reporting=skip_reporting,
        collapse_exact_duplicates=collapse_exact_duplicates,
    )
    corr = res["correlative"]

    conteos = (
        corr.groupby(["ID_GRUPO", "SRC"]).size().unstack(fill_value=0)
        if "SRC" in corr.columns
        else None
    )
    if conteos is not None and nombre_a in conteos and nombre_b in conteos:
        mask_cruz = (conteos[nombre_a] > 0) & (conteos[nombre_b] > 0)
        n_cruzados = int(mask_cruz.sum())
        n_pares = int((conteos.loc[mask_cruz, nombre_a] * conteos.loc[mask_cruz, nombre_b]).sum())
    else:  # pragma: no cover - defensivo ante cambios del Orchestrator
        n_cruzados, n_pares = -1, -1

    metricas: dict[str, Any] = {
        "n_registros": len(corr),
        "n_registros_a": len(df_a),
        "n_registros_b": len(df_b),
        "n_grupos": int(corr["ID_GRUPO"].nunique()),
        "n_grupos_cruzados": n_cruzados,
        "n_pares_a_b": n_pares,
        "report_files": res.get("report_files"),
    }
    manifiesto = _manifiesto(
        "link",
        {
            "nombre_a": nombre_a,
            "nombre_b": nombre_b,
            "trusted": sorted(trusted) if trusted else None,
            "col_nit": col_nit,
            "col_name": col_name,
            "col_ciudad": col_ciudad,
            "extra_features": extra_features,
            "profile": profile,
            "matching_profile": str(matching_profile) if matching_profile else None,
            "collapse_exact_duplicates": collapse_exact_duplicates,
        },
        {nombre_a: df_a, nombre_b: df_b},
    )
    return ResultadoLinkage(
        correlativa=corr,
        golden=res.get("golden"),
        metricas=metricas,
        manifiesto=manifiesto,
    )


# ═══════════════════════════════════════════════════════════════════════════
# MOTOR UNIFICADO (v0.12.0): dedupe_esquema — fachada del motor multicampo
# ═══════════════════════════════════════════════════════════════════════════


def _bloqueo_automatico(esquema: Any, n_filas: int) -> Any:
    """Deriva un bloqueo componible razonable DESDE el esquema (v0.12.0).

    Reglas (documentadas, no mágicas):
        - ``LlaveExacta`` por cada campo IDENTIFICADOR, TELEFONO o EMAIL
          (llaves de alta precisión; grupos degenerados se auto-omiten).
        - ``LSHTexto`` sobre el PRIMER campo de nombre (empresa o persona):
          recall difuso para variaciones de razón social. El umbral es
          CONSCIENTE DE LA ESCALA: 0.4 (recall-primero) hasta 50K filas y
          0.6 (curva S más selectiva) por encima — medido en la auditoría:
          a 200K filas el umbral 0.4 produce >45M candidatos y agota la RAM
          de Colab; 0.6 mantiene el recall de duplicados reales (J≈0.9 tras
          normalizar) con una fracción de los pares.
        - ``RejillaGeo`` por cada campo GEO (vecindad 3×3 de la celda).
        - ``VecindarioOrdenado`` por cada campo FECHA o NUMERICO.

    El usuario puede pasar su propio ``BloqueoComponible`` para anular esto.
    """
    from .matching.campos import TipoCampo
    from .matching.motor_bloqueo import (
        BloqueoComponible,
        LlaveExacta,
        LSHTexto,
        RejillaGeo,
        VecindarioOrdenado,
    )

    estrategias: list[Any] = []
    nombre_ya: bool = False
    for campo in esquema.campos:
        if campo.tipo in (TipoCampo.IDENTIFICADOR, TipoCampo.TELEFONO, TipoCampo.EMAIL):
            estrategias.append(LlaveExacta(campo.nombre))
        elif campo.tipo in (TipoCampo.NOMBRE_EMPRESA, TipoCampo.NOMBRE_PERSONA) and not nombre_ya:
            umbral_lsh = 0.4 if n_filas <= 50_000 else 0.6
            estrategias.append(LSHTexto(campo.nombre, umbral=umbral_lsh))
            nombre_ya = True
        elif campo.tipo is TipoCampo.GEO:
            estrategias.append(
                RejillaGeo(campo.nombre, celda_km=float(campo.params.get("radio_km", 1.0)))
            )
        elif campo.tipo in (TipoCampo.FECHA, TipoCampo.NUMERICO):
            estrategias.append(VecindarioOrdenado(campo.nombre))
    if not estrategias:
        raise ValueError(
            "Qué pasó: el esquema no tiene campos de identificador, nombre, "
            "contacto, geo, fecha ni numérico sobre los cuales bloquear. "
            "Por qué importa: sin bloqueo el motor sería O(n²). "
            "Qué hacer: añada un campo bloqueable al esquema o pase bloqueo= "
            "explícito (record_linkage.matching.motor_bloqueo)."
        )
    return BloqueoComponible(estrategias)


def dedupe_esquema(
    df: pd.DataFrame,
    esquema: Any = None,
    *,
    bloqueo: Any = None,
    respetar_vetos: bool = True,
    incluir_desglose: bool = False,
    max_candidatos: int = 10_000_000,
) -> ResultadoLinkage:
    """Deduplica UNA tabla con el MOTOR MULTICAMPO declarativo (v0.12.0).

    Esta es la fachada del motor unificado: usted declara QUÉ es cada columna
    (``EsquemaCampos``: tipo, peso, política de faltantes, veto) y el motor
    deriva normalización, comparación, bloqueo y decisión. Funciona con
    CUALQUIER nombre de columna y CUALQUIER combinación de tipos (nombre,
    identificador, teléfono, email, dirección, ciudad, geo, fecha, numérico,
    categórico) — la universalidad que ``dedupe()`` (ruta clásica calibrada
    RUES) no pretende cubrir. Las otras rutas siguen disponibles y estables.

    Args:
        df: tabla de entrada (cualquier esquema de columnas).
        esquema: ``EsquemaCampos``. None → preset ``esquema_rues()`` si las
            columnas NIT y RAZON_SOCIAL existen; si no, error accionable.
        bloqueo: ``BloqueoComponible`` propio, o None para derivarlo del
            esquema (ver ``_bloqueo_automatico``: llaves exactas por
            identificador/contacto + LSH por nombre + rejilla geo + vecindario
            para fecha/numérico).
        respetar_vetos: aplica restricciones cannot-link en el clustering
            (un par vetado jamás queda en el mismo grupo ni por puente
            transitivo). O((n+F)·α) desde v0.12.0 — apto para millones.
        incluir_desglose: si True, ``metricas`` incluye ``decisiones`` y
            ``desglose`` (score por campo y par) para auditoría fina.

    Returns:
        ResultadoLinkage:
            - ``correlativa``: ``df`` + columna ``ID_GRUPO`` (0-based).
            - ``golden``: primer registro de cada grupo (representante).
            - ``metricas``: n_registros, n_grupos, n_candidatos, n_fusiones,
              n_vetos, n_vetos_levantados (+ decisiones/desglose si se pide).
            - ``manifiesto``: trazabilidad total (esquema serializado, huella
              del insumo, versiones, seed).

    Raises:
        TypeError | ValueError: preflight accionable (df inválido, esquema
            ausente sin columnas RUES, columnas del esquema faltantes,
            presupuesto de candidatos excedido).

    Escala (medida en 2 vCPU, corpus realista):
        200K filas ≈ 60 s / 1.1 GB pico · 400K ≈ 186 s / 3.0 GB pico. Este
        camino opera EN MEMORIA: es la vía recomendada hasta ~500K filas.
        Para millones de registros use la ruta Orchestrator en disco
        (``linkage()``/``dedupe()``) o pase un ``bloqueo`` más selectivo; el
        presupuesto ``max_candidatos`` corta con error accionable antes de
        agotar la RAM (los corpus con nombres cuasi-homogéneos disparan
        cubetas LSH degeneradas: medido 667M pares sin las defensas v0.12.0).

    Ejemplo (columnas arbitrarias, sin renombrar nada):
        >>> import record_linkage as rl
        >>> from record_linkage import CampoSpec, EsquemaCampos, TipoCampo
        >>> esq = EsquemaCampos(
        ...     campos=[
        ...         CampoSpec("TAX_ID", TipoCampo.IDENTIFICADOR, peso=3.0),
        ...         CampoSpec("COMPANY", TipoCampo.NOMBRE_EMPRESA, peso=2.0),
        ...         CampoSpec("PHONE", TipoCampo.TELEFONO, peso=1.0),
        ...     ],
        ...     umbral_score=0.60, min_concordancias=1,
        ... )
        >>> res = rl.dedupe_esquema(df, esq)
        >>> res.correlativa[["TAX_ID", "COMPANY", "ID_GRUPO"]]
    """
    import numpy as np

    from .matching.campos import EsquemaCampos, esquema_rues
    from .matching.motor_multicampo import clusters_desde_decisiones, evaluar_esquema

    _preflight(df, [], "df")
    if esquema is None:
        if "NIT" in df.columns and "RAZON_SOCIAL" in df.columns:
            esquema = esquema_rues(col_ciudad="CIUDAD" if "CIUDAD" in df.columns else None)
        else:
            raise ValueError(
                "Qué pasó: no se pasó `esquema` y el DataFrame no tiene las "
                "columnas RUES por defecto (NIT, RAZON_SOCIAL). "
                "Por qué importa: el motor multicampo necesita saber QUÉ es "
                "cada columna para elegir normalizador y comparador. "
                "Qué hacer: declare un EsquemaCampos con sus columnas reales, "
                "p. ej. CampoSpec('TAX_ID', TipoCampo.IDENTIFICADOR), "
                "CampoSpec('COMPANY', TipoCampo.NOMBRE_EMPRESA)."
            )
    if not isinstance(esquema, EsquemaCampos):
        raise TypeError(
            f"Qué pasó: `esquema` es {type(esquema).__name__}, no EsquemaCampos. "
            f"Por qué importa: el motor deriva todo del esquema declarativo. "
            f"Qué hacer: construya record_linkage.EsquemaCampos(campos=[...])."
        )
    esquema.validar(df)

    df_pos = df.reset_index(drop=True)
    bloqueo_efectivo = bloqueo if bloqueo is not None else _bloqueo_automatico(esquema, len(df_pos))

    # Presupuesto de candidatos (v0.12.0): mejor un error accionable AHORA
    # que un OOM del kernel a mitad del scoring. 10M pares ≈ ~1 GB entre
    # decisiones y desglose; ajustable explícitamente por el usuario.
    resultado = evaluar_esquema(df_pos, esquema, bloqueo_efectivo, max_candidatos=max_candidatos)
    etiquetas = clusters_desde_decisiones(
        len(df_pos), resultado.decisiones, respetar_vetos=respetar_vetos
    )

    correlativa = df_pos.assign(ID_GRUPO=etiquetas)
    golden = correlativa.loc[~correlativa["ID_GRUPO"].duplicated(keep="first")].copy()

    dec = resultado.decisiones
    metricas: dict[str, Any] = {
        "n_registros": len(df_pos),
        "n_grupos": int(np.unique(etiquetas).size),
        "n_candidatos": int(resultado.n_candidatos),
        "n_fusiones": int(resultado.n_fusiones),
        "n_vetos": int(dec["veto"].sum()) if "veto" in dec.columns else 0,
        "n_vetos_levantados": (
            int(dec["veto_levantado"].sum()) if "veto_levantado" in dec.columns else 0
        ),
        "bloqueo": [e.nombre for e in bloqueo_efectivo.estrategias],
    }
    if incluir_desglose:
        metricas["decisiones"] = resultado.decisiones
        metricas["desglose"] = resultado.desglose

    manifiesto = _manifiesto(
        "dedupe_esquema",
        {
            "esquema": {
                "nombre": esquema.nombre,
                "umbral_score": esquema.umbral_score,
                "min_concordancias": esquema.min_concordancias,
                "corroboracion_activa": esquema.corroboracion.activa,
                "campos": [
                    {
                        "nombre": c.nombre,
                        "tipo": c.tipo.value,
                        "peso": c.peso,
                        "faltante": c.faltante.value,
                        "veta_discrepancia": bool(c.veta_discrepancia),
                        "comparador": getattr(c.comparador, "name", "?"),
                    }
                    for c in esquema.campos
                ],
            },
            "respetar_vetos": respetar_vetos,
            "bloqueo": [e.nombre for e in bloqueo_efectivo.estrategias],
        },
        {"df": df},
    )
    return ResultadoLinkage(
        correlativa=correlativa, golden=golden, metricas=metricas, manifiesto=manifiesto
    )
