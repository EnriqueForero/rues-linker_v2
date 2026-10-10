"""Orquestación reproducible de un cruce multi-fuente (v0.16.0).

Contexto: Google Colab Free (~12 GB RAM, sesión ~12 h). El flujo sigue el
orden que evita perder horas de cómputo:

    preflight → insumos a disco local → carga proyectada → smoke test →
    corrida completa con checkpoints → invariantes → carpeta del estándar

Nada de esto es específico del RUES: las fuentes se declaran con
``SourceSpec``, así que los mismos pasos sirven para cualquier par de
archivos con nombres, formatos y tipos distintos.

F2.5 — el cruce escribe el estándar
-----------------------------------
``ejecutar_cruce`` devuelve un ``ResultadoLinkage`` (``ResultadoCruce`` lo
extiende con el QA del cruce, ``reportes_carga`` y ``rutas``) y escribe UNA
carpeta ``<workspace>/<AAAA-MM-DD_HHMM>_<nombre>/`` con
``exporters.escritor.escribir_resultado``; ya no hay ``golden.parquet`` suelto
ni ``metadatos_corrida.json``: lo que ese JSON traía (la ``ConfigCruce``, los
reportes de ingesta, los tiempos por fase del flujo) vive en
``manifest.json`` → ``corrida.cruce``. Las invariantes del cruce son
``ResultadoLinkage.validar()`` (``_validar_cruce`` la llama y añade el cruce
con las filas que el flujo contó al cargar). En modo disco se conservan las
generaciones (``resultados.generations/<token>/`` + ``resultados.manifest.json``
+ retención), pero cada generación tiene la forma del estándar
(``resultados_disco.escribir_estandar_de_expansion``) y ``leer_resultado`` la
lee; ``ResultadoCruceDisco.leer()`` es ese atajo.
"""

from __future__ import annotations

import math
import shutil
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
from numbers import Integral
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
import pandas as pd

from .. import contrato
from ..api import linkage
from ..exporters.escritor import (
    NOMBRE_MANIFEST,
    Manifiesto,
    carpeta_pendiente,
    escribir_resultado,
    leer_resultado,
)
from ..golden.columnas_finales import (
    COLUMNAS_FINALES,
    faltantes,
)
from ..ingestion import (
    DuckDBCompactionResult,
    DuckDBIngestionSettings,
    SourceSpec,
    compact_source_to_parquet,
    load_source,
    resumir_universo,
)
from ..matching import MatchingProfile
from ..pipeline.errores import ColumnasArrastreError, ContratoSalidaError, mensaje_accionable
from ..resultado import ResultadoLinkage
from ..utils.almacenamiento import es_ruta_fuse
from ..utils.logger import CustomLogger
from .insumos import (
    _normalizar_dtypes_texto,
    escribir_cache,
    leer_cache,
    preparar_insumo_local,
    ruta_en_cache,
)
from .reportes import reportar_composicion, reportar_cruce_por_fuente
from .resultados_disco import (
    ExpansionDisco,
    PublicacionResultadosDisco,
    TablaParquet,
    _configure_connection,
    _quote_literal,
    escribir_estandar_de_expansion,
    expandir_en_disco,
    publicar_resultados_duckdb,
)

__all__ = [
    "ConfigCruce",
    "ControlCalidad",
    "ResultadoCruce",
    "ResultadoCruceDisco",
    "ejecutar_cruce",
    "exportar_sin_pareja",
    "pares_enlazados",
    "resolver_motor",
]


@dataclass
class ConfigCruce:
    """Parámetros de una corrida de cruce, validados al construirse.

    Cero números mágicos en la lógica: todo lo ajustable vive aquí y se
    guarda en el JSON de metadatos junto al resultado.

    Attributes:
        fuentes: contratos de las fuentes a cruzar (una o varias).
        workspace: carpeta de resultados. Puede estar en Drive. La corrida
            deja ``<workspace>/<AAAA-MM-DD_HHMM>_<nombre>/`` con la forma del
            estándar (modo dataframe) o ``<workspace>/resultados.generations/``
            (modo disco).
        nombre: cierra el nombre de la carpeta del estándar; un nombre simple,
            sin separadores. Dos corridas con el mismo nombre en el mismo
            minuto chocan: el escritor nunca sobrescribe una publicada.
        confiables: fuentes con NIT verificado; no se deduplican internamente
            y tienen prioridad para el nombre canónico.
        dir_trabajo: carpeta de trabajo (checkpoints). Debe ser disco local;
            si es None se deriva del workspace evitando Drive.
        perfil: perfil de configuración del pipeline.
        ajustes_perfil: sobrescrituras puntuales del perfil —permutaciones LSH,
            umbrales, modo de limpieza, pesos—. Una clave desconocida falla de
            inmediato con sugerencia, nunca se ignora en silencio.
        perfil_multicampo: refinamiento multi-variable posterior al clustering
            (``"colombia"``, ``"international"`` o un ``MatchingProfile``).
            Separa clusters donde las reglas multi-campo no se cumplen: sube
            precisión sin tocar el recall del bloqueo.
        col_nit / col_nombre / col_ciudad: nombres canónicos post-mapeo.
        variables_extra: columnas adicionales para el scoring.
        filas_smoke: tamaño del ensayo previo. 0 lo desactiva.
        exportar_excel: si además del Parquet se escribe ``excel/`` (completo
            hasta ``LIMITE_FILAS_EXCEL`` filas o un ``<tabla>_LEEME.xlsx``;
            nunca un recorte: regla del escritor, F1.11).
        reusar_checkpoints: si False, la corrida ignora el estado previo.
        dir_procesados: en ``motor_ingesta="pandas"``, carpeta donde se
            reutilizan las fuentes ya proyectadas
            (Parquet + reporte JSON). Leer y proyectar el histórico del RUES
            cuesta minutos; con esto la segunda corrida son segundos. La caché
            se invalida sola si cambia el archivo o el contrato. ``None`` la
            desactiva.
        forzar_relectura: en ``motor_ingesta="pandas"``, ignora la caché de
            fuentes y vuelve a leer el original.
        limite_filas: ``{nombre_fuente: n}`` para recortar una fuente a sus
            primeras ``n`` filas. Sirve para validar el montaje completo en
            minutos antes de comprometerse con el universo entero; el recorte
            queda registrado en los metadatos para que nadie confunda un
            ensayo con una corrida de producción.
        colapsar_duplicados_exactos: procesa un representante por combinación
            idéntica de campos y expande al final a una fila por registro
            original. Decisivo en bases transaccionales (una base de
            exportaciones de 895.102 filas tiene ~19.400 exportadores
            distintos): reduce el trabajo ~12x sin perder ninguna fila.
        motor_ingesta: ``"pandas"`` conserva la ruta histórica;
            ``"duckdb"`` proyecta, normaliza, limita y colapsa en disco antes
            de materializar únicamente los representantes que entran al
            matcher. La expansión a las filas originales se hace al final.
            ``"auto"`` (v0.17.3) dimensiona las fuentes leyendo kilobytes y
            elige: por encima de ``umbral_filas_disco`` va a DuckDB, y si el
            tamaño no se puede establecer también, porque correr más lento es
            recuperable y quedarse sin RAM a los cuarenta minutos no lo es.
        umbral_filas_disco: filas del universo por encima de las cuales
            ``motor_ingesta="auto"`` escoge DuckDB. El valor por defecto sale
            de la medición en Colab Free: 5,26 M filas hacen pico de 7,97 GB
            por el camino pandas sobre un techo de ~12,7 GB compartido con el
            runtime, así que 1,5 M deja margen amplio.
        duckdb_settings: presupuesto explícito de RAM, hilos y temporales para
            el motor DuckDB. Si es ``None`` se usan límites conservadores.
        modo_resultado: ``"dataframe"`` mantiene compatibilidad y materializa
            el resultado final; ``"disco"`` devuelve referencias Parquet y
            ejecuta expansión, payload e invariantes con DuckDB. ``"auto"``
            sigue al motor resuelto: disco con DuckDB, DataFrame con pandas.
        preservar_payload: conserva fuera del matcher las columnas no usadas
            para comparar y las re-adjunta por fila física al publicar.
    """

    fuentes: list[SourceSpec]
    workspace: Path | str
    confiables: frozenset[str] | set[str] | list[str] = field(default_factory=frozenset)
    dir_trabajo: Path | str | None = None
    perfil: str = "produccion_estandar"
    col_nit: str = "NIT"
    col_nombre: str = "RAZON_SOCIAL"
    col_ciudad: str | None = None
    variables_extra: list[Any] | None = None
    filas_smoke: int = 4_000
    exportar_excel: bool = True
    reusar_checkpoints: bool = True
    colapsar_duplicados_exactos: bool = True
    dir_procesados: Path | str | None = None
    forzar_relectura: bool = False
    limite_filas: dict[str, int] | None = None
    ajustes_perfil: dict[str, Any] | None = None
    perfil_multicampo: Any = None
    motor_ingesta: str = "pandas"
    umbral_filas_disco: int = 1_500_000
    duckdb_settings: DuckDBIngestionSettings | None = None
    modo_resultado: str = "dataframe"
    preservar_payload: bool = True
    #: v0.17.0 (restituido de 0.14.1) — saca del motor las columnas mapeadas
    #: que NO participan en la decisión (ciudad, teléfono, correo… mientras no
    #: estén en ``variables_extra``) y las re-adjunta a la correlativa al
    #: final por ``ORIGINAL_INDEX``. Sin esto, esas columnas degradan el
    #: colapso de duplicados exactos: medido sobre RUES × Exportaciones, sólo
    #: por mapear DEPARTAMENTO los representantes pasaron de 19.407 a 32.745.
    #: Solo aplica al motor de ingesta pandas (DuckDB usa ``preservar_payload``).
    separar_columnas_extra: bool = True
    #: F2.5 — nombre de la carpeta del estándar (``<marca>_<nombre>``).
    nombre: str = "cruce"

    def __post_init__(self) -> None:
        if not self.fuentes:
            raise ValueError(
                mensaje_accionable(
                    "ConfigCruce.fuentes está vacío.",
                    "no hay nada que cruzar.",
                    "declare al menos un SourceSpec.",
                )
            )
        nombres = [f.name for f in self.fuentes]
        if len(set(nombres)) != len(nombres):
            raise ValueError(f"Nombres de fuente duplicados: {nombres}.")
        if self.filas_smoke < 0:
            raise ValueError("ConfigCruce.filas_smoke no puede ser negativo.")
        motor_ingesta = str(self.motor_ingesta).strip().lower()
        if motor_ingesta not in {"pandas", "duckdb", "auto"}:
            raise ValueError("motor_ingesta debe ser 'pandas', 'duckdb' o 'auto'.")
        if isinstance(self.umbral_filas_disco, bool) or not isinstance(
            self.umbral_filas_disco, Integral
        ):
            raise TypeError("umbral_filas_disco debe ser un entero >= 1.")
        if self.umbral_filas_disco < 1:
            raise ValueError("umbral_filas_disco debe ser >= 1.")
        self.umbral_filas_disco = int(self.umbral_filas_disco)
        if motor_ingesta == "duckdb" and self.forzar_relectura:
            raise ValueError(
                "motor_ingesta='duckdb' no admite forzar_relectura=True: "
                "DuckDB reconstruye su staging desde la fuente en cada llamada. "
                "Use forzar_relectura=False o motor_ingesta='pandas'."
            )
        if motor_ingesta == "duckdb" and self.dir_procesados is not None:
            raise ValueError(
                "motor_ingesta='duckdb' no usa dir_procesados: esa caché sólo "
                "pertenece al motor pandas. Quite dir_procesados; los artefactos "
                "DuckDB se gestionan dentro de dir_trabajo."
            )
        if motor_ingesta == "duckdb" and not self.colapsar_duplicados_exactos:
            raise ValueError(
                "motor_ingesta='duckdb' requiere colapsar_duplicados_exactos=True "
                "para conservar el contrato de expansión."
            )
        modo_resultado = str(self.modo_resultado).strip().lower()
        if modo_resultado not in {"dataframe", "disco", "auto"}:
            raise ValueError("modo_resultado debe ser 'dataframe', 'disco' o 'auto'.")
        if modo_resultado == "disco" and motor_ingesta == "pandas":
            raise ValueError("modo_resultado='disco' requiere motor_ingesta='duckdb'.")
        if modo_resultado == "auto" and motor_ingesta == "pandas":
            modo_resultado = "dataframe"
        if modo_resultado == "auto" and motor_ingesta == "duckdb":
            modo_resultado = "dataframe" if self.exportar_excel else "disco"
        if modo_resultado == "disco" and self.exportar_excel:
            raise ValueError(
                "modo_resultado='disco' requiere exportar_excel=False; Excel "
                "materializaría la salida y contradice el presupuesto de memoria."
            )
        if not isinstance(self.preservar_payload, bool):
            raise TypeError("preservar_payload debe ser bool.")
        if self.perfil_multicampo is not None and not isinstance(
            self.perfil_multicampo, (str, MatchingProfile)
        ):
            raise TypeError(
                "perfil_multicampo debe ser None, un nombre de perfil o "
                "MatchingProfile; no se aceptan objetos opacos."
            )
        if self.duckdb_settings is not None and not isinstance(
            self.duckdb_settings, DuckDBIngestionSettings
        ):
            raise TypeError("duckdb_settings debe ser DuckDBIngestionSettings o None.")
        self.motor_ingesta = motor_ingesta
        self.modo_resultado = modo_resultado
        if motor_ingesta == "duckdb" and self.duckdb_settings is None:
            self.duckdb_settings = DuckDBIngestionSettings()
        limites_normalizados: dict[str, int] = {}
        for fuente, limite in (self.limite_filas or {}).items():
            if isinstance(limite, bool) or not isinstance(limite, Integral):
                raise TypeError(
                    f"limite_filas['{fuente}'] debe ser un entero >= 1, no {type(limite).__name__}."
                )
            if limite < 1:
                raise ValueError(f"limite_filas['{fuente}'] debe ser >= 1, no {limite}.")
            limites_normalizados[fuente] = int(limite)
        self.limite_filas = limites_normalizados or None
        self.workspace = Path(self.workspace).expanduser()
        # Falla ahora si el nombre no sirve, no después de L1…L5 (misma regla
        # que linkage(carpeta_salida=, nombre=)).
        carpeta_pendiente(self.workspace, self.nombre)
        self.confiables = frozenset(self.confiables)
        sobrantes = set(self.limite_filas or {}) - set(nombres)
        if sobrantes:
            raise ValueError(
                f"limite_filas nombra fuentes inexistentes {sorted(sobrantes)}. "
                f"Use exactamente: {sorted(nombres)}."
            )
        desconocidas = self.confiables - set(nombres)
        if desconocidas:
            raise ValueError(
                mensaje_accionable(
                    f"'confiables' nombra fuentes inexistentes {sorted(desconocidas)}.",
                    "la política de fuentes confiables se aplicaría a un nombre que no "
                    "existe y ninguna fuente real la recibiría.",
                    f"use exactamente los nombres declarados: {sorted(nombres)}.",
                )
            )
        if self.dir_trabajo is None:
            # Nunca usar Drive como disco de trabajo: los checkpoints SQLite
            # sobre FUSE son justamente lo que rompe las corridas largas.
            base = Path("/content") if Path("/content").is_dir() else Path.home()
            self.dir_trabajo = (
                base / "rues_linker_trabajo"
                if es_ruta_fuse(self.workspace)
                else self.workspace / "_trabajo"
            )
        self.dir_trabajo = Path(self.dir_trabajo).expanduser()
        if self.dir_procesados is not None:
            self.dir_procesados = Path(self.dir_procesados).expanduser()
        _validar_rutas_limpieza(self)
        _validar_parametros_json(self)

    @property
    def ruta_workspace(self) -> Path:
        """Workspace ya normalizado a ``Path`` (el campo acepta ``str``)."""
        return Path(self.workspace)

    @property
    def ruta_procesados(self) -> Path | None:
        """Carpeta de caché ya normalizada a ``Path`` (o None si está apagada)."""
        return None if self.dir_procesados is None else Path(self.dir_procesados)

    @property
    def ruta_trabajo(self) -> Path:
        """Directorio de trabajo ya normalizado a ``Path``."""
        assert self.dir_trabajo is not None  # garantizado por __post_init__
        return Path(self.dir_trabajo)


# ═══════════════════════════════════════════════════════════════════════════
# Control de calidad común a los dos modos de resultado (v0.17.1)
# ═══════════════════════════════════════════════════════════════════════════
# Antes, el consumidor tenía que discriminar el modo: `ResultadoCruce` traía
# DataFrames y `ResultadoCruceDisco` sólo referencias Parquet, así que un
# notebook que analizaba la salida no era sustituible entre modos (violación
# de Liskov señalada en la auditoría 0.16.0). Este protocolo define la
# superficie de QA que AMBOS cumplen, con dos implementaciones —pandas y
# DuckDB— que devuelven exactamente la misma forma. Ninguna materializa la
# correlativa completa: la de disco agrega en SQL.


@runtime_checkable
class ControlCalidad(Protocol):
    """Preguntas de calidad que todo resultado de cruce sabe responder."""

    def conflictos_identificador(self) -> int:
        """Grupos que mezclan dos identificadores válidos distintos (debe ser 0)."""

    def distribucion_grupos(self) -> dict[str, int]:
        """Tamaño de grupo y variedad de nombres, en conteos agregados."""

    def identidad_adoptada(self, limite: int = 25) -> pd.DataFrame:
        """Qué identidad (NIT y razón social) quedó para cada grupo."""

    def grupos_sospechosos(self, *, minimo_nombres: int = 6, limite: int = 20) -> pd.DataFrame:
        """Filas de los grupos con más nombres distintos, para revisión humana."""

    def identificadores_por_fuente(self) -> dict[str, Any]:
        """Diagnóstico del identificador declarado, por fuente."""

    def entidades_multifuente(self) -> int:
        """Entidades presentes en más de una fuente."""


#: Presupuesto de las consultas de QA sobre Parquet. Deliberadamente pequeño:
#: son agregaciones por grupo, no materializaciones, y así el QA nunca se
#: convierte en el pico de memoria de la corrida.
_AJUSTES_QA = DuckDBIngestionSettings(memory_limit="512MB", threads=2)


def _conflictos_del_contrato(metricas: Mapping[str, Any]) -> int:
    """Grupos que mezclan dos bases de identificador válidas distintas.

    Desde F1.9 las columnas técnicas (``NIT_BASE``, ``NIT_VALID``…) ya no
    viajan en el entregable —quedan en ``_trabajo/``—, así que el conteo NO se
    recalcula aquí desde ``NIT``: lo calcula una sola vez
    ``salida.completar`` con las técnicas del motor (``NIT_BASE`` donde
    ``NIT_VALID``, la misma regla que decide ``METODO_UNION``), lo publica en
    el manifiesto del contrato y :func:`ejecutar_cruce` lo copia a
    ``metricas["conflictos_identificador"]``. Recalcularlo desde ``NIT``
    daba un falso conflicto con un identificador flotante (``900111222.0``).
    """
    conflictos = metricas.get("conflictos_identificador")
    if conflictos is None:
        raise ContratoSalidaError(
            [
                mensaje_accionable(
                    "el resultado no trae metricas['conflictos_identificador'].",
                    "el QA de identificador se calcula con las técnicas del motor al "
                    "completar el contrato, no desde la correlativa publicada.",
                    "construya el resultado con ejecutar_cruce(); si lo arma a mano, "
                    "copie el conteo de manifiesto['completar']['identificador'].",
                )
            ]
        )
    return int(conflictos)


def _qa_desde_dataframe(correlativa: pd.DataFrame, conflictos: int) -> dict[str, Any]:
    """Métricas de QA sobre una correlativa materializada."""
    from .diagnostico import diagnosticar_identificadores

    tam = correlativa.groupby("ID_GRUPO").size()
    variedad = correlativa.groupby("ID_GRUPO")["RAZON_SOCIAL"].nunique()
    return {
        "conflictos": conflictos,
        "tam": tam,
        "variedad": variedad,
        "por_fuente": {
            str(src): diagnosticar_identificadores(
                correlativa.loc[correlativa["SRC"] == src, "NIT"]
            )
            for src in correlativa["SRC"].dropna().unique()
        },
    }


def _qa_desde_parquet(ruta: Path, settings: Any, conflictos: int) -> dict[str, Any]:
    """Mismas métricas, resueltas en DuckDB sin traer la correlativa a RAM.

    El conteo de conflictos llega calculado (ver ``_conflictos_del_contrato``):
    no se materializa ninguna columna para recalcularlo.
    """
    import duckdb

    con = duckdb.connect()
    try:
        _configure_connection(con, settings)
        origen = f"read_parquet({_quote_literal(str(ruta))})"
        tam = (
            con.execute(f"SELECT ID_GRUPO, COUNT(*) n FROM {origen} GROUP BY ID_GRUPO")
            .df()
            .set_index("ID_GRUPO")["n"]
        )
        variedad = (
            con.execute(
                f"SELECT ID_GRUPO, COUNT(DISTINCT RAZON_SOCIAL) n FROM {origen} GROUP BY ID_GRUPO"
            )
            .df()
            .set_index("ID_GRUPO")["n"]
        )
        fuentes = [f[0] for f in con.execute(f"SELECT DISTINCT SRC FROM {origen}").fetchall()]
        por_fuente = {}
        for src in fuentes:
            from .diagnostico import diagnosticar_identificadores

            nits = con.execute(
                f"SELECT NIT FROM {origen} WHERE SRC = {_quote_literal(str(src))}"
            ).df()["NIT"]
            por_fuente[str(src)] = diagnosticar_identificadores(nits)
        return {
            "conflictos": int(conflictos),
            "tam": tam,
            "variedad": variedad,
            "por_fuente": por_fuente,
        }
    finally:
        con.close()


def _formato_distribucion(tam: pd.Series, variedad: pd.Series) -> dict[str, int]:
    """Conteos agregados; idénticos vengan de pandas o de DuckDB."""
    return {
        "grupos_1_fila": int((tam == 1).sum()),
        "grupos_2a5_filas": int(((tam >= 2) & (tam <= 5)).sum()),
        "grupos_mas_5_filas": int((tam > 5).sum()),
        "max_filas_por_grupo": int(tam.max()) if len(tam) else 0,
        "grupos_1_nombre": int((variedad == 1).sum()),
        "grupos_2a3_nombres": int(((variedad >= 2) & (variedad <= 3)).sum()),
        "grupos_mas_3_nombres": int((variedad > 3).sum()),
        "max_nombres_por_grupo": int(variedad.max()) if len(variedad) else 0,
    }


def pares_enlazados(resultado: Any, *, limite: int = 15) -> pd.DataFrame:
    """Entidades presentes en más de una fuente, una fila por entidad.

    Pone el NIT y la razón social de CADA fuente en columnas contiguas, que es
    la vista que permite auditar un match de un vistazo. Funciona igual con un
    resultado materializado o de disco; en disco la consulta se resuelve en
    DuckDB y solo baja ``limite`` filas.

    Args:
        resultado: ``ResultadoCruce`` o ``ResultadoCruceDisco``.
        limite: máximo de entidades a devolver.

    Returns:
        DataFrame indexado por ``ID_GRUPO`` con columnas ``<FUENTE>_NIT`` y
        ``<FUENTE>_RAZON_SOCIAL``.
    """
    if isinstance(resultado, ResultadoCruceDisco):
        import duckdb

        origen = f"read_parquet({_quote_literal(str(resultado.correlativa.path))})"
        con = duckdb.connect()
        try:
            _configure_connection(con, _AJUSTES_QA)
            base = con.execute(
                f"""
                WITH m AS (
                    SELECT ID_GRUPO FROM {origen}
                    GROUP BY ID_GRUPO HAVING COUNT(DISTINCT SRC) > 1
                    ORDER BY ID_GRUPO LIMIT {int(limite)}
                )
                SELECT DISTINCT c.ID_GRUPO, c.SRC, c.NIT, c.RAZON_SOCIAL
                FROM {origen} c JOIN m USING (ID_GRUPO)
                """
            ).df()
        finally:
            con.close()
    else:
        corr = resultado.correlativa
        multi = corr.groupby("ID_GRUPO")["SRC"].transform("nunique") > 1
        grupos = corr.loc[multi, "ID_GRUPO"].drop_duplicates().nsmallest(limite)
        base = corr[corr["ID_GRUPO"].isin(grupos)][["ID_GRUPO", "SRC", "NIT", "RAZON_SOCIAL"]]
    if base.empty:
        return base
    tabla = (
        base.sort_values(["ID_GRUPO", "SRC"])
        .drop_duplicates(["ID_GRUPO", "SRC"])
        .pivot_table(
            index="ID_GRUPO", columns="SRC", values=["NIT", "RAZON_SOCIAL"], aggfunc="first"
        )
    )
    tabla.columns = [f"{fuente}_{campo}" for campo, fuente in tabla.columns]
    return tabla.head(limite)


def exportar_sin_pareja(
    resultado: Any, *, fuente_principal: str, destino: Path | str
) -> tuple[Path, int]:
    """Escribe las entidades de otras fuentes que NO cruzaron con la principal.

    Es la lista de trabajo del negocio: exportadores que el RUES no reconoce,
    clientes que el padrón no tiene. Se escribe SIEMPRE como Parquet y, en
    modo disco, la selección ocurre dentro de DuckDB: la salida puede tener
    millones de filas sin que ninguna pase por la memoria del proceso.

    Args:
        resultado: ``ResultadoCruce`` o ``ResultadoCruceDisco``.
        fuente_principal: nombre de la fuente contra la que se busca pareja.
        destino: ruta del Parquet a escribir.

    Returns:
        Tupla ``(ruta escrita, número de entidades sin pareja)``.
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(resultado, ResultadoCruceDisco):
        import duckdb

        origen = f"read_parquet({_quote_literal(str(resultado.correlativa.path))})"
        con = duckdb.connect()
        try:
            _configure_connection(con, _AJUSTES_QA)
            con.execute(
                f"""
                COPY (
                    WITH cruzadas AS (
                        SELECT DISTINCT ID_GRUPO FROM {origen}
                        WHERE SRC = {_quote_literal(fuente_principal)}
                    )
                    SELECT * FROM (
                        SELECT *, row_number() OVER (PARTITION BY NIT ORDER BY ORIGINAL_INDEX) rn
                        FROM {origen}
                        WHERE SRC <> {_quote_literal(fuente_principal)}
                          AND ID_GRUPO NOT IN (SELECT ID_GRUPO FROM cruzadas)
                    ) WHERE rn = 1
                ) TO {_quote_literal(str(destino))} (FORMAT PARQUET)
                """
            )
            n = con.execute(
                f"SELECT COUNT(*) FROM read_parquet({_quote_literal(str(destino))})"
            ).fetchone()[0]
        finally:
            con.close()
        return destino, int(n)

    corr = resultado.correlativa
    grupos_principal = set(corr.loc[corr["SRC"] == fuente_principal, "ID_GRUPO"].unique())
    otras = corr[corr["SRC"] != fuente_principal]
    sin_pareja = otras[~otras["ID_GRUPO"].isin(grupos_principal)].drop_duplicates("NIT")
    sin_pareja.to_parquet(destino, index=False)
    return destino, len(sin_pareja)


def _tabla_tiempos(metricas: dict[str, Any], titulo_base: str = "TIEMPO") -> str:
    """Formatea el desglose por fase, con la columna de pico de RSS si existe.

    Una sola implementación para la salida materializada y la de disco: antes
    divergían en formato y solo una traía barras. El pico de RSS por fase es
    lo que convierte "se quedó sin RAM" en un diagnóstico (v0.17.0); si el
    entorno no tenía ``psutil``, la columna simplemente no aparece.

    Args:
        metricas: dict de la corrida; usa ``segundos_por_fase``,
            ``segundos_total`` y opcionalmente ``pico_rss_mib_por_fase``.
        titulo_base: prefijo del encabezado.

    Returns:
        Bloque de texto listo para ``print``.
    """
    fases = metricas.get("segundos_por_fase", {})
    picos = metricas.get("pico_rss_mib_por_fase", {}) or {}
    total = float(metricas.get("segundos_total", 0.0))
    ancho = 70 if picos else 62
    titulo = f"⏱️  {titulo_base} Y MEMORIA POR FASE" if picos else f"⏱️  {titulo_base} POR FASE"
    lineas = ["=" * ancho, titulo, "=" * ancho]
    for nombre, segundos in sorted(fases.items(), key=lambda kv: -kv[1]):
        proporcion = segundos / total if total else 0.0
        pico = picos.get(nombre)
        columna = f"  pico {pico:>8,.0f} MiB" if pico else (" " * 19 if picos else "")
        lineas.append(
            f"  {nombre:<22} {segundos:>8.1f}s  {proporcion:>6.1%}{columna}  "
            + "█" * int(proporcion * 20)
        )
    lineas.append("-" * ancho)
    pico_total = max(picos.values(), default=0.0)
    cierre = f"        pico {pico_total:>8,.0f} MiB" if picos else ""
    lineas.append(f"  {'TOTAL':<22} {total:>8.1f}s{cierre}")
    lineas.append("=" * ancho)
    return "\n".join(lineas)


@dataclass
class ResultadoCruce(ResultadoLinkage):
    """Salida de :func:`ejecutar_cruce` en modo dataframe: el ``ResultadoLinkage``
    del contrato (F2.5) más lo que el cruce sabe de sí mismo.

    Hereda ``correlativa``, ``golden``, ``metricas``, ``manifiesto``,
    ``revision``, ``diccionario``, ``dir_trabajo`` y ``validar()``; el QA del
    cruce (``conflictos_identificador``, ``distribucion_grupos``…) es el mismo
    que en modo disco (:class:`ControlCalidad`).

    Attributes:
        reportes_carga: reporte de ingesta por fuente.
        rutas: artefactos de la carpeta del estándar por clave
            (``correlativa_parquet``, ``golden_parquet``, ``entidades_ids_parquet``,
            ``revision_csv``, ``diccionario_csv``, ``manifest``, ``carpeta``,
            ``<tabla>_excel`` si hay Excel, ``figura_<nombre>``), más
            ``rutas["dir_trabajo"]`` (carpeta L1…L5 de la corrida, donde quedan
            las columnas técnicas que el contrato retira).
        carpeta: la carpeta del estándar escrita (``<marca>_<nombre>``).
        metricas: las de ``linkage()`` (``n_registros``…) más las del cruce
            (``filas_entrada``, ``entidades``, ``conflictos_identificador``,
            ``segundos_por_fase``, ``pico_rss_mib_por_fase``, ``por_fuente``…).
    """

    reportes_carga: dict[str, Any] = field(default_factory=dict)
    rutas: dict[str, Path] = field(default_factory=dict)
    carpeta: Path | None = None
    _qa_cache: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    def cruce_por_fuente(self) -> pd.DataFrame:
        """Tabla de cuántos registros de cada fuente quedaron enlazados."""
        return reportar_cruce_por_fuente(self.correlativa)

    def resumen(self) -> str:
        """Reporte de composición listo para imprimir."""
        return reportar_composicion(int(self.metricas["filas_entrada"]), self.correlativa)

    def _qa(self) -> dict[str, Any]:
        """Métricas de QA cacheadas por resultado."""
        if self._qa_cache is None:
            self._qa_cache = _qa_desde_dataframe(
                self.correlativa, _conflictos_del_contrato(self.metricas)
            )
        return self._qa_cache

    def conflictos_identificador(self) -> int:
        """Grupos que mezclan dos identificadores válidos distintos."""
        return int(self._qa()["conflictos"])

    def distribucion_grupos(self) -> dict[str, int]:
        """Tamaño de grupo y variedad de nombres, en conteos agregados."""
        qa = self._qa()
        return _formato_distribucion(qa["tam"], qa["variedad"])

    def identidad_adoptada(self, limite: int = 25) -> pd.DataFrame:
        """Qué identidad quedó para cada grupo, al lado de lo que traía la fila.

        Es el entregable del cruce y por eso se expone como método: quien lo
        consume no debería tener que saber cómo se llaman las columnas ni de
        dónde salen. Falla si no están, en vez de devolver una vista
        incompleta que parece correcta.

        Args:
            limite: filas de muestra a devolver.

        Raises:
            RuntimeError: si la correlativa no trae las columnas del contrato.
        """
        ausentes = faltantes(self.correlativa)
        if ausentes:
            raise RuntimeError(
                f"La correlativa no trae {list(ausentes)}: el resultado no dice "
                f"qué identidad adoptó cada grupo. Revise la fase L5."
            )
        columnas = [
            c
            for c in ("ID_GRUPO", "SRC", "NIT", "RAZON_SOCIAL", *COLUMNAS_FINALES)
            if c in self.correlativa.columns
        ]
        return self.correlativa[columnas].head(int(limite))

    def grupos_sospechosos(self, *, minimo_nombres: int = 6, limite: int = 20) -> pd.DataFrame:
        """Filas de los grupos con más nombres distintos, para revisión humana."""
        variedad = self._qa()["variedad"]
        sospechosos = variedad[variedad >= minimo_nombres]
        if not len(sospechosos):
            return self.correlativa.iloc[0:0][["ID_GRUPO", "SRC", "NIT", "RAZON_SOCIAL"]]
        mayores = sospechosos.nlargest(3).index
        return (
            self.correlativa[self.correlativa["ID_GRUPO"].isin(mayores)]
            .drop_duplicates(["ID_GRUPO", "RAZON_SOCIAL"])
            .sort_values("ID_GRUPO")[["ID_GRUPO", "SRC", "NIT", "RAZON_SOCIAL"]]
            .head(limite)
        )

    def identificadores_por_fuente(self) -> dict[str, Any]:
        """Diagnóstico del identificador declarado, por fuente."""
        return dict(self._qa()["por_fuente"])

    def entidades_multifuente(self) -> int:
        """Entidades presentes en más de una fuente."""
        corr = self.correlativa
        return int((corr.groupby("ID_GRUPO")["SRC"].nunique() > 1).sum())

    def tiempos(self) -> str:
        """Desglose de tiempo y pico de RSS por fase, de mayor a menor costo."""
        return _tabla_tiempos(self.metricas)


@dataclass
class ResultadoCruceDisco:
    """Salida productiva: tablas Parquet consultables sin materializarlas.

    No es un ``ResultadoLinkage`` (ese objeto lleva las tablas en pandas y en
    modo disco nada se materializa), pero ``carpeta`` —la generación
    publicada— tiene la forma del estándar (F2.5) y :meth:`leer` la devuelve
    como ``ResultadoLinkage`` cuando el usuario decide pagar la memoria.

    Attributes:
        carpeta: la generación publicada (``resultados.generations/<token>/``),
            una carpeta del estándar: ``correlativa.parquet``, ``golden.parquet``,
            ``entidades_ids.parquet``, ``revision.csv``, ``diccionario.csv``,
            ``manifest.json``.
        rutas: los mismos artefactos por clave que en modo dataframe, más
            ``generation_dir`` (= ``carpeta``), ``manifest_generaciones``
            (``resultados.manifest.json``, el puntero a la generación vigente)
            y ``dir_trabajo``.
    """

    golden: TablaParquet
    correlativa: TablaParquet
    reportes_carga: dict[str, Any]
    metricas: dict[str, Any]
    rutas: dict[str, Path]
    carpeta: Path
    _publicacion: PublicacionResultadosDisco = field(repr=False)
    _qa_cache: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    def leer(self, alias: str | None = None) -> ResultadoLinkage:
        """Materializa la generación como ``ResultadoLinkage`` (``leer_resultado``).

        Verifica las huellas del manifiesto y carga la correlativa completa:
        es la decisión explícita de traerla a memoria. ``dir_trabajo`` vuelve
        a señalar L1…L5 de esta corrida (está fuera del workspace y
        ``leer_resultado`` solo resuelve un ``_trabajo/`` interno).
        """
        res = leer_resultado(self.carpeta, alias)
        dir_trabajo = self.rutas.get("dir_trabajo")
        if res.dir_trabajo is None and dir_trabajo is not None and Path(dir_trabajo).is_dir():
            res.dir_trabajo = Path(dir_trabajo)
            res.manifiesto["dir_trabajo"] = str(res.dir_trabajo)
        return res

    def cruce_por_fuente(self) -> pd.DataFrame:
        """Cuenta filas y entidades enlazadas mediante una consulta DuckDB."""

        import duckdb

        with duckdb.connect(":memory:") as connection:
            return connection.execute(
                """
                WITH base AS (
                    SELECT SRC, ID_GRUPO,
                           count(DISTINCT SRC) OVER (PARTITION BY ID_GRUPO) AS n_fuentes
                    FROM read_parquet(?)
                )
                SELECT SRC AS FUENTE, count(*)::BIGINT AS TOTAL_REGISTROS,
                       count(*) FILTER (WHERE n_fuentes > 1)::BIGINT AS REGISTROS_ENLAZADOS
                FROM base GROUP BY SRC ORDER BY SRC
                """,
                [str(self.correlativa.path)],
            ).fetch_df()

    def resumen(self) -> str:
        """Resumen acotado calculado a partir de métricas ya verificadas."""

        return (
            f"{self.metricas['filas_entrada']:,} filas de entrada → "
            f"{self.metricas['entidades']:,} entidades; resultados en Parquet."
        )

    def _qa(self) -> dict[str, Any]:
        """Métricas de QA resueltas en DuckDB, cacheadas por resultado."""
        if self._qa_cache is None:
            self._qa_cache = _qa_desde_parquet(
                Path(self.correlativa.path),
                _AJUSTES_QA,
                _conflictos_del_contrato(self.metricas),
            )
        return self._qa_cache

    def conflictos_identificador(self) -> int:
        """Grupos que mezclan dos identificadores válidos distintos."""
        return int(self._qa()["conflictos"])

    def distribucion_grupos(self) -> dict[str, int]:
        """Tamaño de grupo y variedad de nombres, en conteos agregados."""
        qa = self._qa()
        return _formato_distribucion(qa["tam"], qa["variedad"])

    def identidad_adoptada(self, limite: int = 25) -> pd.DataFrame:
        """Qué identidad quedó para cada grupo, sin materializar la tabla.

        Misma pregunta y misma respuesta que en el camino en memoria; aquí se
        resuelve dentro de DuckDB para que funcione igual con millones de filas.

        Args:
            limite: filas de muestra a devolver.

        Raises:
            RuntimeError: si la correlativa publicada no trae el contrato.
        """
        import duckdb

        ausentes = [c for c in COLUMNAS_FINALES if c not in self.correlativa.columns]
        if ausentes:
            raise RuntimeError(
                f"La correlativa publicada no trae {ausentes}: el resultado no "
                f"dice qué identidad adoptó cada grupo. Revise la fase L5."
            )
        columnas = ", ".join(
            f'"{c}"'
            for c in ("ID_GRUPO", "SRC", "NIT", "RAZON_SOCIAL", *COLUMNAS_FINALES)
            if c in self.correlativa.columns
        )
        origen = f"read_parquet({_quote_literal(str(self.correlativa.path))})"
        con = duckdb.connect()
        try:
            return con.execute(
                f"SELECT {columnas} FROM {origen} ORDER BY ID_GRUPO LIMIT {int(limite)}"
            ).df()
        finally:
            con.close()

    def grupos_sospechosos(self, *, minimo_nombres: int = 6, limite: int = 20) -> pd.DataFrame:
        """Filas de los grupos con más nombres distintos, sin materializar todo."""
        import duckdb

        variedad = self._qa()["variedad"]
        sospechosos = variedad[variedad >= minimo_nombres]
        origen = f"read_parquet({_quote_literal(str(self.correlativa.path))})"
        columnas = "ID_GRUPO, SRC, NIT, RAZON_SOCIAL"
        con = duckdb.connect()
        try:
            _configure_connection(con, _AJUSTES_QA)
            if not len(sospechosos):
                return con.execute(f"SELECT {columnas} FROM {origen} LIMIT 0").df()
            ids = ", ".join(str(int(g)) for g in sospechosos.nlargest(3).index)
            return con.execute(
                f"SELECT DISTINCT {columnas} FROM {origen} "
                f"WHERE ID_GRUPO IN ({ids}) ORDER BY ID_GRUPO LIMIT {int(limite)}"
            ).df()
        finally:
            con.close()

    def identificadores_por_fuente(self) -> dict[str, Any]:
        """Diagnóstico del identificador declarado, por fuente."""
        return dict(self._qa()["por_fuente"])

    def entidades_multifuente(self) -> int:
        """Entidades presentes en más de una fuente, contadas en SQL."""
        import duckdb

        origen = f"read_parquet({_quote_literal(str(self.correlativa.path))})"
        con = duckdb.connect()
        try:
            _configure_connection(con, _AJUSTES_QA)
            return int(
                con.execute(
                    f"SELECT COUNT(*) FROM (SELECT ID_GRUPO FROM {origen} "
                    f"GROUP BY ID_GRUPO HAVING COUNT(DISTINCT SRC) > 1)"
                ).fetchone()[0]
            )
        finally:
            con.close()

    def tiempos(self) -> str:
        """Desglose de tiempos con el mismo formato de la salida materializada."""
        return _tabla_tiempos(self.metricas)

    def matriz_presencia(self) -> pd.DataFrame:
        """Matriz N-fuente de entidades compartidas, sin cargar la correlativa."""

        return self._publicacion.matriz_presencia()

    def entidades_ausentes_de(self, source: str, *, limit: int = 100) -> pd.DataFrame:
        """Entidades ausentes de una fuente, consultadas directamente en disco."""

        return self._publicacion.entidades_ausentes_de(source, limit=limit)


# ═══════════════════════════════════════════════════════════════════════════
# F2.5 — el resultado del cruce es el del contrato y se escribe con el escritor
# ═══════════════════════════════════════════════════════════════════════════


def _validar_cruce(res: ResultadoLinkage, *, filas_entrada: int) -> None:
    """Las invariantes del cruce: ``ResultadoLinkage.validar()`` más el cruce de filas.

    Hasta F2.5 ``_verificar_invariantes`` repetía por su cuenta lo que el
    contrato ya exige (una fila por registro de entrada, ``ID_GRUPO`` no nulo,
    golden ↔ correlativa, ``NIT_FINAL``/``RAZON_SOCIAL_FINAL`` presentes,
    ADR-0008). Ahora la regla vive una vez en ``validar()``; aquí solo se
    añade lo que el contrato no puede saber: cuántas filas contó ESTE flujo al
    cargar las fuentes, que debe coincidir con la correlativa aunque el
    manifiesto estuviera mal armado.

    Raises:
        ContratoSalidaError: con todos los fallos, los del contrato y el propio.
    """
    fallos = list(res.validar().fallos)
    if len(res.correlativa) != filas_entrada:
        fallos.append(
            mensaje_accionable(
                f"la correlativa trae {len(res.correlativa):,} filas y entraron {filas_entrada:,}.",
                "el cruce debe conservar TODAS las filas de entrada.",
                "no use este resultado; reporte el caso.",
            )
        )
    if fallos:
        raise ContratoSalidaError(fallos)


def _insumos_reales(
    entradas: Mapping[str, Any], compactaciones: Mapping[str, DuckDBCompactionResult]
) -> dict[str, Any]:
    """``entradas`` del manifiesto con las filas REALES de cada fuente (motor DuckDB).

    ``linkage()`` hashea y cuenta lo que recibe: los representantes que DuckDB
    dejó tras colapsar duplicados exactos. La correlativa publicada tiene una
    fila por registro original, así que el manifiesto declara esas filas y
    conserva, declarado, sobre qué se calculó la huella.
    """
    insumos: dict[str, Any] = {}
    for nombre, entrada in entradas.items():
        copia = dict(entrada) if isinstance(entrada, Mapping) else {"valor": entrada}
        compactacion = compactaciones.get(nombre)
        if compactacion is not None:
            copia["filas"] = int(compactacion.input_rows)
            copia["representantes"] = int(compactacion.compact_rows)
            copia["huella_de"] = (
                "representantes (DuckDB colapsó duplicados exactos antes del motor)"
            )
        insumos[nombre] = copia
    return insumos


def _resultado_cruce(
    salida: ResultadoLinkage,
    correlativa: pd.DataFrame,
    golden: pd.DataFrame,
    reporte_arrastre: ReporteColumnasArrastre,
    *,
    reportes_carga: dict[str, Any],
    insumos: Mapping[str, Any] | None = None,
) -> ResultadoCruce:
    """El ``ResultadoCruce`` a partir de lo que devolvió ``linkage()`` y la correlativa entregada.

    La correlativa puede traer más columnas que la que vio el motor (las de
    arrastre re-adjuntadas o el payload de DuckDB): el diccionario se
    recalcula con ellas como columnas de la fuente, y ``metricas``/``entradas``
    describen la tabla entregada, no la compacta.
    """
    manifiesto = dict(salida.manifiesto)
    manifiesto["funcion"] = "ejecutar_cruce"
    if insumos is not None:
        manifiesto["entradas"] = dict(insumos)
    metricas = dict(salida.metricas)
    metricas["n_registros"] = len(correlativa)
    metricas["n_grupos"] = int(correlativa["ID_GRUPO"].nunique()) if len(correlativa) else 0
    completar = manifiesto.get("completar") or {}
    diccionario = contrato.diccionario(
        {
            "correlativa": correlativa,
            "golden": golden,
            "enlaces": None,
            "revision": salida.revision,
        },
        columnas_fuente=[*completar.get("columnas_fuente", ()), *reporte_arrastre.adjuntadas],
        renombres=completar.get("renombres"),
        renombres_canonicos=completar.get("renombres_canonicos"),
    )
    return ResultadoCruce(
        correlativa=correlativa,
        golden=golden,
        metricas=metricas,
        manifiesto=manifiesto,
        enlaces=None,
        revision=salida.revision,
        diccionario=diccionario,
        dir_trabajo=salida.dir_trabajo,
        reportes_carga=reportes_carga,
    )


def _clave_de_artefacto(ruta_relativa: str) -> str:
    """``correlativa.parquet`` → ``correlativa_parquet``; ``excel/golden.xlsx`` →
    ``golden_excel``; ``excel/correlativa_LEEME.xlsx`` → ``correlativa_leeme_excel``;
    ``figuras/dashboard.png`` → ``figura_dashboard``."""
    ruta = Path(ruta_relativa)
    if ruta.parent.name == "excel":
        return f"{ruta.stem.lower()}_excel"
    if ruta.parent.name == "figuras":
        return f"figura_{ruta.stem.lower()}"
    return f"{ruta.stem}_{ruta.suffix.lstrip('.')}"


def _rutas_de(
    manifiesto: Manifiesto, dir_trabajo: Path, *, con_carpeta: bool = True
) -> dict[str, Path]:
    """``rutas`` del resultado a partir de los artefactos que el manifiesto lista.

    ``con_carpeta=False`` en modo disco: ahí la carpeta es ``generation_dir``
    (el único directorio que los consumidores esperan en ``rutas`` además de
    ``dir_trabajo``; el notebook 06 orquestador copia el resto como archivos).
    """
    rutas: dict[str, Path] = {"manifest": manifiesto.carpeta / NOMBRE_MANIFEST}
    if con_carpeta:
        rutas["carpeta"] = manifiesto.carpeta
    for artefacto in manifiesto.artefactos:
        relativa = str(artefacto["ruta"])
        rutas[_clave_de_artefacto(relativa)] = manifiesto.carpeta / relativa
    rutas["dir_trabajo"] = dir_trabajo
    return rutas


def _metricas_cruce(
    cronometro: _Cronometro,
    *,
    filas_entrada: int,
    filas_correlativa: int,
    entidades: int,
    conflictos_identificador: int,
    segundos_linkage: float,
    por_fuente: Mapping[str, int],
) -> dict[str, Any]:
    return {
        "filas_entrada": filas_entrada,
        "filas_correlativa": filas_correlativa,
        "entidades": entidades,
        "conflictos_identificador": int(conflictos_identificador),
        "segundos_linkage": round(segundos_linkage, 2),
        "segundos_total": round(cronometro.total, 2),
        "segundos_por_fase": {k: round(v, 2) for k, v in cronometro.fases.items()},
        "pico_rss_mib_por_fase": {k: round(v, 1) for k, v in cronometro.rss_mib.items()},
        "por_fuente": dict(por_fuente),
    }


def _cerrar_tiempos(metricas: dict[str, Any], cronometro: _Cronometro) -> None:
    """Actualiza el total y las fases tras la última fase medida (la escritura).

    El manifiesto se escribe DENTRO de la fase ``exportes`` y no puede
    contener el tiempo de escribirse a sí mismo: ``manifest.json`` →
    ``corrida.cruce.metricas.segundos_total`` llega hasta el inicio de esa fase;
    el objeto devuelto sí trae el total completo.
    """
    metricas["segundos_total"] = round(cronometro.total, 2)
    metricas["segundos_por_fase"] = {k: round(v, 2) for k, v in cronometro.fases.items()}
    metricas["pico_rss_mib_por_fase"] = {k: round(v, 1) for k, v in cronometro.rss_mib.items()}


def _bloque_cruce(
    config: ConfigCruce,
    reporte_arrastre: ReporteColumnasArrastre,
    metricas: Mapping[str, Any],
    reportes_carga: Mapping[str, Any],
) -> dict[str, Any]:
    """``manifest.json`` → ``corrida.cruce``: lo que ``metadatos_corrida.json`` traía hasta F2.5."""
    return _normalizar_json(
        {
            "parametros": {
                "nombre": config.nombre,
                "perfil": config.perfil,
                "confiables": sorted(config.confiables),
                "col_nit": config.col_nit,
                "col_nombre": config.col_nombre,
                "col_ciudad": config.col_ciudad,
                "variables_extra": config.variables_extra,
                "filas_smoke": config.filas_smoke,
                "exportar_excel": config.exportar_excel,
                "reusar_checkpoints": config.reusar_checkpoints,
                "forzar_relectura": config.forzar_relectura,
                "dir_trabajo": str(config.ruta_trabajo),
                "colapsar_duplicados_exactos": config.colapsar_duplicados_exactos,
                "limite_filas": config.limite_filas,
                "columnas_arrastre": reporte_arrastre.como_manifiesto(),
                "ajustes_perfil": config.ajustes_perfil,
                "perfil_multicampo": _serializar_perfil_multicampo(config.perfil_multicampo),
                "motor_ingesta": config.motor_ingesta,
                "modo_resultado": config.modo_resultado,
                "preservar_payload": config.preservar_payload,
                "separar_columnas_extra": config.separar_columnas_extra,
                "duckdb_settings": (
                    asdict(config.duckdb_settings) if config.duckdb_settings is not None else None
                ),
                "fuentes": [
                    {"nombre": f.name, "ruta": str(f.path), "mapeo": dict(f.column_mapping)}
                    for f in config.fuentes
                ],
                "source_specs": [_serializar_source_spec(f) for f in config.fuentes],
            },
            "metricas": dict(metricas),
            "ingesta": dict(reportes_carga),
        }
    )


class _Cronometro:
    """Mide tiempo y pico de RSS por fase, y sabe presentarlos.

    Un total no dice dónde se fue el tiempo ni dónde se disparó la memoria.
    Tras el incidente de OOM en Colab (2026-08-27) el desglose de RSS por fase
    dejó de ser un lujo: es lo que convierte "consumió toda la RAM" en un
    diagnóstico accionable, porque señala la fase exacta del pico.

    v0.17.0 — el muestreo se restituye con ciclo de vida explícito: el hilo se
    detiene con ``cerrar()`` (o al salir del contexto) y se hace ``join``, así
    que no queda un daemon vivo tras la corrida. Sin ``psutil`` degrada a
    solo-tiempos sin fallar.

    Example:
        >>> with _Cronometro() as crono:
        ...     with crono.fase("carga"):
        ...         pass
        >>> "carga" in crono.fases
        True
    """

    #: Periodo de muestreo. 0,2 s da resolución suficiente para fases de
    #: segundos sin costar CPU medible (una lectura de /proc por tick).
    _PERIODO_MUESTREO_S = 0.2

    def __init__(self) -> None:
        self.fases: dict[str, float] = {}
        self.rss_mib: dict[str, float] = {}
        self._inicio_global = time.time()
        self._pico_ventana = 0.0
        self._detener: Any = None
        self._hilo: Any = None
        self._proceso: Any = None
        try:
            import threading

            import psutil

            proceso = psutil.Process()
            self._proceso = proceso
            self._detener = threading.Event()

            def _muestrear() -> None:
                while not self._detener.is_set():
                    try:
                        rss = proceso.memory_info().rss / 1024**2
                    except Exception:  # proceso terminando: dejar de muestrear
                        return
                    if rss > self._pico_ventana:
                        self._pico_ventana = rss
                    self._detener.wait(self._PERIODO_MUESTREO_S)

            self._hilo = threading.Thread(target=_muestrear, name="rues-linker-rss", daemon=True)
            self._hilo.start()
        except Exception:  # pragma: no cover - entorno sin psutil
            self._detener = None
            self._hilo = None
            self._proceso = None

    def _rss_ahora(self) -> float:
        """RSS instantáneo en MiB; 0.0 si no hay psutil o el proceso terminó."""
        if self._proceso is None:
            return 0.0
        try:
            return float(self._proceso.memory_info().rss) / 1024**2
        except Exception:  # pragma: no cover - proceso terminando
            return 0.0

    def cerrar(self) -> None:
        """Detiene el muestreo y espera al hilo. Idempotente."""
        if self._detener is not None:
            self._detener.set()
        if self._hilo is not None:
            self._hilo.join(timeout=2.0)
            self._hilo = None

    def __enter__(self) -> _Cronometro:
        """Permite usar el cronómetro como contexto y garantizar el cierre."""
        return self

    def __exit__(self, *_excepcion: object) -> None:
        """Cierra el muestreo pase lo que pase dentro del bloque."""
        self.cerrar()

    @contextmanager
    def fase(self, nombre: str):
        """Contexto que acumula tiempo y pico de RSS de una fase.

        El pico combina el muestreo periódico con lecturas síncronas en los
        bordes: una fase más corta que el período de muestreo (preflight,
        invariantes) igual queda con un valor, en vez de una celda vacía que
        se lee como "no se midió".
        """
        inicio = time.time()
        self._pico_ventana = self._rss_ahora()
        try:
            yield
        finally:
            self.fases[nombre] = self.fases.get(nombre, 0.0) + (time.time() - inicio)
            if self._proceso is not None:
                pico = max(self._pico_ventana, self._rss_ahora())
                self.rss_mib[nombre] = max(self.rss_mib.get(nombre, 0.0), pico)

    @property
    def total(self) -> float:
        """Segundos transcurridos desde que se creó el cronómetro."""
        return time.time() - self._inicio_global

    def tabla(self) -> str:
        """Desglose legible, ordenado de mayor a menor costo."""
        total = self.total
        hay_rss = bool(self.rss_mib)
        titulo = "⏱️  TIEMPO Y MEMORIA POR FASE" if hay_rss else "⏱️  TIEMPO POR FASE"
        lineas = ["=" * 70, titulo, "=" * 70]
        for nombre, segundos in sorted(self.fases.items(), key=lambda kv: -kv[1]):
            proporcion = segundos / total if total else 0.0
            barra = "█" * int(proporcion * 20)
            pico = self.rss_mib.get(nombre)
            columna_pico = f"  pico {pico:>8,.0f} MiB" if pico else " " * 19
            lineas.append(
                f"  {nombre:<22} {segundos:>8.1f}s  {proporcion:>6.1%}{columna_pico}  {barra}"
            )
        lineas.append("-" * 70)
        pico_total = max(self.rss_mib.values(), default=0.0)
        cierre = f"  pico {pico_total:>8,.0f} MiB" if hay_rss else ""
        lineas.append(f"  {'TOTAL':<22} {total:>8.1f}s        {cierre}")
        lineas.append("=" * 70)
        return "\n".join(lineas)


def _resolver_ruta_segura(ruta: Path | str, *, etiqueta: str) -> Path:
    """Resuelve una ruta para comparaciones destructivas o falla cerrado."""

    try:
        return Path(ruta).expanduser().resolve(strict=False)
    except OSError as exc:
        raise ValueError(f"No se pudo resolver la ruta protegida '{etiqueta}': {ruta}") from exc


def _rutas_checkpoints(config: ConfigCruce) -> tuple[Path, Path]:
    """Directorios exactos que pueden eliminarse al descartar checkpoints."""

    return config.ruta_trabajo / "_smoke", config.ruta_trabajo / "corrida"


def _rutas_publicacion_reservadas(
    config: ConfigCruce,
) -> tuple[tuple[tuple[str, Path], ...], tuple[tuple[str, Path], ...]]:
    """Devuelve archivos y directorios que pertenecen al publicador.

    Los archivos sólo colisionan por igualdad exacta. Un directorio reservado
    tampoco puede contener una fuente ni quedar contenido por una fuente que
    sea directorio, porque el publicador administra todo ese subárbol.
    """

    workspace = config.ruta_workspace
    if config.modo_resultado == "disco":
        return (
            (
                ("manifiesto de resultados", workspace / "resultados.manifest.json"),
                ("lock del manifiesto", workspace / ".resultados.manifest.json.lock"),
            ),
            (("generaciones de resultados", workspace / "resultados.generations"),),
        )

    # F2.5: el escritor del estándar limpia y renombra la carpeta pendiente;
    # la definitiva lleva marca de tiempo y, si existiera, el escritor falla
    # sin tocarla, así que no hay nada más que reservar.
    return (), (("carpeta pendiente del estándar", carpeta_pendiente(workspace, config.nombre)),)


def _es_control_pendiente_disco(ruta: Path, workspace: Path) -> bool:
    """Reconoce los nombres dinámicos reservados por el commit generacional."""

    if ruta.parent != workspace:
        return False
    nombre = ruta.name
    return (nombre.startswith(".resultados.") and nombre.endswith(".generation.pending")) or (
        nombre.startswith(".resultados.manifest.json.") and nombre.endswith(".pending")
    )


def _validar_colisiones_publicacion(config: ConfigCruce) -> None:
    """Falla antes de leer si una fuente ocupa una salida o control reservado."""

    archivos, directorios = _rutas_publicacion_reservadas(config)
    workspace = _resolver_ruta_segura(config.ruta_workspace, etiqueta="workspace")
    for spec in config.fuentes:
        fuente = _resolver_ruta_segura(spec.path, etiqueta=f"fuente[{spec.name}]")
        for etiqueta, destino_original in archivos:
            destino = _resolver_ruta_segura(destino_original, etiqueta=etiqueta)
            if fuente == destino:
                raise ValueError(
                    f"La fuente '{spec.name}' colisiona con {etiqueta} '{destino}'. "
                    "Mueva la fuente fuera del workspace o elija otro workspace; "
                    "el flujo escribiría en esa ruta."
                )
        for etiqueta, destino_original in directorios:
            destino = _resolver_ruta_segura(destino_original, etiqueta=etiqueta)
            if (
                fuente == destino
                or fuente.is_relative_to(destino)
                or destino.is_relative_to(fuente)
            ):
                raise ValueError(
                    f"La fuente '{spec.name}' se solapa con {etiqueta} '{destino}'. "
                    "Ese subárbol es propiedad del publicador; mueva la fuente o "
                    "elija otro workspace."
                )
        if config.modo_resultado == "disco" and _es_control_pendiente_disco(fuente, workspace):
            raise ValueError(
                f"La fuente '{spec.name}' usa el nombre reservado de commit "
                f"'{fuente.name}' dentro del workspace. Mueva la fuente o elija "
                "otro workspace."
            )


def _validar_rutas_limpieza(config: ConfigCruce) -> None:
    """Impide que una limpieza de checkpoints alcance datos o resultados."""

    if config.reusar_checkpoints:
        return

    protegidas: list[tuple[str, Path | str]] = [("workspace", config.ruta_workspace)]
    protegidas.extend((f"fuente[{spec.name}]", spec.path) for spec in config.fuentes)
    if config.ruta_procesados is not None:
        protegidas.append(("dir_procesados", config.ruta_procesados))
    if config.duckdb_settings is not None and config.duckdb_settings.temp_directory is not None:
        protegidas.append(("duckdb_settings.temp_directory", config.duckdb_settings.temp_directory))

    for objetivo_original in _rutas_checkpoints(config):
        objetivo = _resolver_ruta_segura(objetivo_original, etiqueta="checkpoint")
        for etiqueta, protegida_original in protegidas:
            protegida = _resolver_ruta_segura(protegida_original, etiqueta=etiqueta)
            if etiqueta == "workspace":
                # Los checkpoints bajo el workspace son el valor por defecto y
                # borrar ese hijo exacto no alcanza los resultados hermanos.
                # Sí es destructivo que el objetivo sea el workspace o uno de
                # sus ancestros, pues entonces rmtree lo contendría completo.
                se_solapan = objetivo == protegida or protegida.is_relative_to(objetivo)
            else:
                se_solapan = (
                    objetivo == protegida
                    or objetivo.is_relative_to(protegida)
                    or protegida.is_relative_to(objetivo)
                )
            if se_solapan:
                raise ValueError(
                    "Limpieza de checkpoints insegura: "
                    f"'{objetivo}' se solapa con {etiqueta} '{protegida}'. "
                    "Separe dir_trabajo de resultados, fuentes, caché y spill de DuckDB."
                )


def _limpiar_checkpoints(config: ConfigCruce) -> None:
    """Elimina únicamente los dos directorios previamente validados."""

    _validar_rutas_limpieza(config)
    for ruta in _rutas_checkpoints(config):
        if ruta.exists():
            shutil.rmtree(ruta)


def _preflight(config: ConfigCruce, log: Any) -> None:
    """Valida rutas y permisos ANTES de cualquier operación costosa."""
    _validar_rutas_limpieza(config)
    _validar_colisiones_publicacion(config)
    for spec in config.fuentes:
        ruta = Path(spec.path).expanduser()
        if ruta.exists():
            continue
        # Con una caché vigente el original ya no hace falta: se puede correr
        # sin Drive montado, o después de mover el archivo de sitio.
        if (
            config.motor_ingesta == "pandas"
            and not config.forzar_relectura
            and config.ruta_procesados is not None
        ):
            cacheado = ruta_en_cache(spec, config.ruta_procesados)
            if cacheado.is_file() and cacheado.with_suffix(".json").is_file():
                log.info(f"   ♻️  {spec.name}: sin el original, pero hay caché vigente")
                continue
        detalle_motor = (
            " El motor DuckDB necesita el archivo de texto/ZIP original; "
            "la caché Parquet histórica no implementa este contrato de expansión."
            if config.motor_ingesta == "duckdb"
            else ""
        )
        raise FileNotFoundError(
            mensaje_accionable(
                f"la fuente '{spec.name}' apunta a '{ruta}', que no existe, y no hay "
                "caché reutilizable.",
                "el preflight corta aquí para no fallar dentro de una corrida de horas.",
                f"revise la ruta y, en Colab, que Drive esté montado.{detalle_motor}",
            )
        )
    config.ruta_workspace.mkdir(parents=True, exist_ok=True)
    config.ruta_trabajo.mkdir(parents=True, exist_ok=True)
    testigo = config.ruta_workspace / ".rues_linker_escritura"
    try:
        testigo.write_text("ok", encoding="utf-8")
        testigo.unlink()
    except OSError as exc:
        raise PermissionError(
            mensaje_accionable(
                f"no se puede escribir en el workspace '{config.ruta_workspace}' ({exc}).",
                "sin un workspace escribible la corrida no puede guardar resultados ni "
                "checkpoints y moriría a mitad de camino.",
                "elija otra carpeta o verifique el montaje de Drive.",
            )
        ) from exc
    log.info(f"   ✅ Preflight: {len(config.fuentes)} fuentes, workspace escribible")


def _columnas_matcher(config: ConfigCruce) -> tuple[str, ...]:
    """Columnas que realmente consume el matcher, en orden estable."""

    columnas: list[str] = [config.col_nit, config.col_nombre]
    if config.col_ciudad:
        columnas.append(config.col_ciudad)
    for feature in config.variables_extra or ():
        if isinstance(feature, str):
            columnas.append(feature)
        elif isinstance(feature, Mapping) and feature.get("column"):
            columnas.append(str(feature["column"]))
    return tuple(dict.fromkeys(columnas))


def _cargar(
    config: ConfigCruce, log: Any
) -> tuple[
    dict[str, tuple[pd.DataFrame, dict[str, Any]]],
    dict[str, DuckDBCompactionResult],
]:
    """Carga las fuentes proyectadas, reutilizando la caché cuando está vigente.

    El orden importa: primero se consulta la caché (si acierta no se toca el
    original ni se copia nada desde Drive) y solo si falla se paga la copia a
    disco local y la lectura completa. La llave de la caché se calcula siempre
    sobre la ruta ORIGINAL, así copiar el archivo no invalida lo ya guardado.
    """
    cargadas: dict[str, tuple[pd.DataFrame, dict[str, Any]]] = {}
    compactaciones: dict[str, DuckDBCompactionResult] = {}
    dir_insumos = config.ruta_trabajo / "insumos"
    for spec in config.fuentes:
        inicio = time.time()
        if config.motor_ingesta == "duckdb":
            row_limit = (config.limite_filas or {}).get(spec.name)
            ruta_local = preparar_insumo_local(spec.path, dir_insumos, logger=log)
            lectura = spec if ruta_local == Path(spec.path) else _con_ruta(spec, ruta_local)
            resultado = compact_source_to_parquet(
                lectura,
                config.ruta_trabajo / "ingesta_duckdb",
                settings=config.duckdb_settings,
                output_prefix=spec.name,
                overwrite=True,
                row_limit=row_limit,
                matcher_columns=_columnas_matcher(config),
                preserve_payload=config.preservar_payload,
            )
            datos = pd.read_parquet(resultado.compact_path)
            reporte = {
                "source_name": resultado.source_name,
                "source_path": str(resultado.source_path),
                "engine": "duckdb",
                "rows": resultado.input_rows,
                "processed_rows": resultado.compact_rows,
                "collapsed_rows": resultado.collapsed_rows,
                "columns": resultado.columns,
                "resolved_mapping": dict(resultado.resolved_mapping),
                "invalid_values": dict(resultado.invalid_values),
                "encoding": resultado.source_encoding,
                "delimiter": resultado.delimiter,
                "compression": resultado.compression.value,
                "archive_member": resultado.archive_member,
                "transcoded_to_utf8": resultado.transcoded_to_utf8,
                "warnings": resultado.warnings,
                "report_scope": "limited_prefix" if row_limit is not None else "full_source",
                "row_limit": row_limit,
                "compact_path": str(resultado.compact_path),
                "expansion_map_path": str(resultado.expansion_map_path),
                "payload_path": (
                    str(resultado.payload_path) if resultado.payload_path is not None else None
                ),
                "payload_columns": list(resultado.payload_columns),
                "missing_optional": list(resultado.missing_optional),
            }
            cargadas[spec.name] = (datos, reporte)
            compactaciones[spec.name] = resultado
            log.info(
                f"   ✅ {spec.name}: {resultado.input_rows:,} filas → "
                f"{resultado.compact_rows:,} representantes DuckDB "
                f"en {time.time() - inicio:.1f}s"
                + (
                    f" · valores inválidos: {dict(resultado.invalid_values)}"
                    if resultado.invalid_values
                    else ""
                )
            )
            continue

        desde_cache = False
        if not config.forzar_relectura:
            encontrado = leer_cache(spec, config.ruta_procesados, logger=log)
            if encontrado is not None and "missing_optional" not in encontrado[1]:
                # F1.8 — una caché anterior no dice qué columnas opcionales
                # faltaron; reutilizarla dejaría ``omitidas = []`` sin saberlo.
                log.info(
                    f"   🔁 {spec.name}: la caché es anterior a F1.8 (no declara las "
                    f"columnas opcionales ausentes); se vuelve a leer el original"
                )
                encontrado = None
            if encontrado is not None:
                datos, reporte = encontrado
                desde_cache = True
        if not desde_cache:
            ruta_local = preparar_insumo_local(spec.path, dir_insumos, logger=log)
            lectura = spec if ruta_local == Path(spec.path) else _con_ruta(spec, ruta_local)
            cargada = load_source(lectura)
            datos = cargada.data
            reporte = _normalizar_reporte_carga(vars(cargada.report))
            escribir_cache(spec, config.ruta_procesados, datos, reporte, logger=log)
        else:
            reporte = _normalizar_reporte_carga(reporte)
        # La ruta pandas valida el archivo completo y recorta después de
        # cargar. Declarar el alcance evita comparar sus invalid_values con
        # los de un prefijo DuckDB como si midieran el mismo universo.
        reporte["report_scope"] = "full_source"
        reporte["row_limit"] = (config.limite_filas or {}).get(spec.name)
        cargadas[spec.name] = (datos, reporte)
        invalidos = reporte.get("invalid_values") or {}
        log.info(
            f"   ✅ {spec.name}: {len(datos):,} filas × {len(datos.columns)} columnas "
            f"en {time.time() - inicio:.1f}s"
            + (" · reutilizado" if desde_cache else "")
            + (f" · valores inválidos: {invalidos}" if invalidos else "")
        )
    return cargadas, compactaciones


def _con_ruta(spec: SourceSpec, ruta: Path) -> SourceSpec:
    """Devuelve una copia del contrato apuntando a otra ruta (spec es inmutable)."""
    from dataclasses import replace

    return replace(spec, path=ruta)


def _normalizar_reporte_carga(reporte: Mapping[str, Any]) -> dict[str, Any]:
    """Convierte vistas inmutables a estructuras JSON, sin stringify silencioso."""

    normalizado = dict(reporte)
    for key in ("resolved_mapping", "invalid_values"):
        value = normalizado.get(key)
        if isinstance(value, Mapping):
            normalizado[key] = dict(value)
    if "missing_optional" in normalizado:
        normalizado["missing_optional"] = list(normalizado["missing_optional"])
    return normalizado


def _serializar_source_spec(spec: SourceSpec) -> dict[str, Any]:
    """Representa el contrato de entrada con valores JSON reproducibles."""

    def valor_enum(value: Any) -> str:
        return str(getattr(value, "value", value))

    def valor_dataclass(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else asdict(value)

    return {
        "name": spec.name,
        "path": str(spec.path),
        "column_mapping": dict(spec.column_mapping),
        "optional_column_mapping": dict(spec.optional_column_mapping),
        "format": valor_enum(spec.format),
        "compression": valor_enum(spec.compression),
        "encoding": spec.encoding,
        "delimiter": spec.delimiter,
        "text_engine": spec.text_engine,
        "newlines_in_values": spec.newlines_in_values,
        "text_block_size_bytes": spec.text_block_size_bytes,
        "archive_member": spec.archive_member,
        "sheet_name": spec.sheet_name,
        "header": spec.header,
        "passthrough_columns": list(spec.passthrough_columns),
        "keep_unmapped": spec.keep_unmapped,
        "column_types": {column: valor_enum(value) for column, value in spec.column_types.items()},
        "identifier_formats": {
            column: valor_dataclass(value) for column, value in spec.identifier_formats.items()
        },
        "numeric_formats": {
            column: valor_dataclass(value) for column, value in spec.numeric_formats.items()
        },
        "null_values": {column: list(values) for column, values in spec.null_values.items()},
        "global_null_values": list(spec.global_null_values),
        "chunksize": spec.chunksize,
        "invalid_values": valor_enum(spec.invalid_values),
        "safety_limits": asdict(spec.safety_limits),
        "temp_dir": None if spec.temp_dir is None else str(spec.temp_dir),
    }


def _normalizar_json(value: Any, *, ruta: str = "$") -> Any:
    """Convierte tipos conocidos a JSON estricto y rechaza objetos opacos."""

    if isinstance(value, MatchingProfile):
        return _normalizar_json(value.to_dict(), ruta=ruta)
    if isinstance(value, Enum):
        return _normalizar_json(value.value, ruta=ruta)
    if isinstance(value, np.generic):
        return _normalizar_json(value.item(), ruta=ruta)
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"Metadato no finito en {ruta}: {value!r}.")
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _normalizar_json(getattr(value, item.name), ruta=f"{ruta}.{item.name}")
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        resultado: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if isinstance(raw_key, Enum):
                raw_key = raw_key.value
            if isinstance(raw_key, Path):
                raw_key = str(raw_key)
            if not isinstance(raw_key, str):
                raise TypeError(
                    f"Clave de metadatos no serializable en {ruta}: "
                    f"{type(raw_key).__name__}; use claves str."
                )
            if raw_key in resultado:
                raise TypeError(f"Clave de metadatos duplicada en {ruta}: {raw_key!r}.")
            resultado[raw_key] = _normalizar_json(raw_value, ruta=f"{ruta}.{raw_key}")
        return resultado
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalizar_json(item, ruta=f"{ruta}[{index}]") for index, item in enumerate(value)]
    raise TypeError(
        f"Metadato no serializable en {ruta}: {type(value).__name__}. "
        "Use valores JSON, Path, Enum, dataclass, MatchingProfile o escalares NumPy."
    )


def _serializar_perfil_multicampo(value: str | MatchingProfile | None) -> Any:
    """Conserva el perfil multi-campo en forma reproducible y reejecutable."""

    if value is None or isinstance(value, str):
        return value
    return value.to_dict()


def _validar_parametros_json(config: ConfigCruce) -> None:
    """Falla al construir la configuración, no después de una corrida larga."""

    _normalizar_json(
        {
            "variables_extra": config.variables_extra,
            "ajustes_perfil": config.ajustes_perfil,
            "perfil_multicampo": _serializar_perfil_multicampo(config.perfil_multicampo),
            "duckdb_settings": config.duckdb_settings,
            "source_specs": [_serializar_source_spec(spec) for spec in config.fuentes],
        },
        ruta="$.parametros",
    )


def _columnas_de_motor(config: ConfigCruce) -> set[str]:
    """Columnas canónicas que el motor SÍ usa para decidir."""
    columnas = {"NIT", "RAZON_SOCIAL"}
    for extra in config.variables_extra or []:
        columnas.add(extra["column"] if isinstance(extra, dict) else str(extra))
    if config.col_ciudad:
        columnas.add(str(config.col_ciudad))
    return columnas


def _separar_columnas_extra(
    marcos: dict[str, pd.DataFrame], config: ConfigCruce, log: Any
) -> tuple[dict[str, Path], list[str], dict[str, int]]:
    """Saca del motor las columnas que no participan en la decisión.

    Se derraman a Parquet en el directorio de trabajo (ya recortadas por
    ``limite_filas``, para que la alineación posicional sea exacta) y los
    frames de ``marcos`` quedan reducidos a las columnas de motor.

    Con ``separar_columnas_extra=False`` no se derrama ni se reduce nada (las
    columnas viajan por el motor), pero la unión se calcula igual: es lo que
    el manifiesto debe encontrar en la correlativa entregada (F1.8).

    Returns:
        Tupla ``(rutas_spill, union_de_columnas, longitudes_por_fuente)``;
        ``rutas_spill`` queda vacío cuando no se separa.
    """
    rutas: dict[str, Path] = {}
    union: list[str] = []
    longitudes: dict[str, int] = {}
    motor = _columnas_de_motor(config)
    dir_spill = config.ruta_trabajo / "columnas_extra"
    for nombre, df in marcos.items():
        longitudes[nombre] = len(df)
        extras = [c for c in df.columns if c not in motor]
        if not extras:
            continue
        if not config.separar_columnas_extra:
            for columna in extras:
                if columna not in union:
                    union.append(columna)
            continue
        dir_spill.mkdir(parents=True, exist_ok=True)
        ruta = dir_spill / f"{nombre}.parquet"
        df[extras].to_parquet(ruta, index=False)
        rutas[nombre] = ruta
        for columna in extras:
            if columna not in union:
                union.append(columna)
        marcos[nombre] = df[[c for c in df.columns if c in motor]]
        log.info(
            f"   📦 {nombre}: fuera del motor {extras} (se re-adjuntan a la correlativa al final)"
        )
    return rutas, union, longitudes


#: Motivo (global) de una columna pedida cuyo nombre lo produce el motor. Es
#: neutral respecto del camino: con separación la de la fuente queda fuera y
#: no se adjunta; sin separación viajó por el motor y este la pisó. En ambos
#: prevalece la del motor y la de la fuente no se entrega.
MOTIVO_CHOQUE_CON_MOTOR = (
    "choca con una columna que produce el motor; prevalece la del motor y la de la "
    "fuente no se entrega"
)

#: Motivo (DuckDB) de una columna no-matcher que la fuente sí produjo y no se
#: publicó: ``ConfigCruce(preservar_payload=False)`` la dejó fuera del entregable.
MOTIVO_SIN_PAYLOAD = "fuera del entregable por preservar_payload=False"

#: Prefijo del motivo por fuente que calcula el lector (``missing_optional``);
#: :func:`motivo_fuente_sin_columna` lo completa con la columna de origen.
PREFIJO_MOTIVO_FUENTE_SIN_COLUMNA = "la fuente no tiene la columna"


def motivo_fuente_sin_columna(origen: str) -> str:
    """Motivo documentado 1: la fuente no trae la columna de origen pedida."""
    return f"{PREFIJO_MOTIVO_FUENTE_SIN_COLUMNA} {origen!r} pedida en optional_column_mapping"


#: Columnas que el motor ESCRIBE en la correlativa, además de sus entradas
#: (:func:`_columnas_de_motor`). Con ``separar_columnas_extra=False`` las
#: columnas de arrastre viajan por el motor y una con uno de estos nombres
#: queda PISADA: es la única forma de declarar ese choque en vez de afirmar
#: que se adjuntó (revisión r3 de F1.8). Desde F1.9 son EXACTAMENTE las
#: columnas fijas del contrato de salida (``contrato.COLUMNAS_CORRELATIVA``:
#: las técnicas ya no están en el entregable, quedan en ``_trabajo/``); la
#: regla se escribe una vez, en ``contrato.py``. La prueba
#: ``test_las_columnas_que_produce_el_motor_son_exactamente_las_declaradas``
#: ata esta constante a lo observado.
COLUMNAS_QUE_PRODUCE_EL_MOTOR: frozenset[str] = frozenset(contrato.COLUMNAS_CORRELATIVA)


@dataclass(frozen=True)
class ColumnaOmitida:
    """Una columna de arrastre pedida que NO está en la correlativa entregada.

    Attributes:
        columna: nombre canónico pedido.
        motivo: uno de los motivos documentados en
            :class:`ReporteColumnasArrastre`.
        fuente: fuente donde se pidió, cuando el motivo es por fuente; ``None``
            si el motivo es global (choque con el motor).
    """

    columna: str
    motivo: str
    fuente: str | None = None

    def como_manifiesto(self) -> dict[str, str | None]:
        """Forma serializable para el manifiesto (``corrida.cruce``)."""
        return {"columna": self.columna, "fuente": self.fuente, "motivo": self.motivo}


@dataclass(frozen=True)
class ReporteColumnasArrastre:
    """Lo que REALMENTE pasó con las columnas de arrastre (F1.8).

    Va al manifiesto como ``parametros.columnas_arrastre``. Hasta F1.8 el
    manifiesto declaraba ``columnas_re_adjuntadas = unión planificada``,
    que era verdad solo si el re-adjunte había ocurrido; ahora se deriva de
    la correlativa ENTREGADA en los tres caminos (pandas con y sin separación,
    DuckDB), con :func:`_reporte_arrastre_observado`.

    Es un valor: se construye una sola vez, al final, y no se muta.

    Attributes:
        adjuntadas: columnas de arrastre que están en la correlativa entregada.
        omitidas: columnas pedidas que NO están. Los motivos posibles son
            EXACTAMENTE tres, y son constantes del módulo:

            1. por fuente, :func:`motivo_fuente_sin_columna`: lo calcula el
               lector (``missing_optional`` del informe de ingesta) y aquí
               solo se declara, con ``fuente``. Puede convivir con la misma
               columna en ``adjuntadas`` si otra fuente sí la trae: para la
               fuente que no la tiene, la petición no se cumplió y se dice;
            2. global, :data:`MOTIVO_CHOQUE_CON_MOTOR`: la fuente SÍ la trae,
               pero el motor produce una columna con ese nombre y prevalece
               la del motor (F1 la renombrará ``<col>_FUENTE`` cuando entre
               el contrato de salida; hasta entonces queda declarada aquí).
               Con separación la de la fuente queda fuera; con
               ``separar_columnas_extra=False`` viajó por el motor y este la
               pisó: en ambos casos se declara choque, nunca «adjuntada»;
            3. por fuente, :data:`MOTIVO_SIN_PAYLOAD` (solo DuckDB): la fuente
               la produjo y ``preservar_payload=False`` la dejó fuera.

            Una columna que la fuente aportó fuera del motor y no llegó a la
            correlativa NO es una omisión: es :class:`ColumnasArrastreError`.
    """

    adjuntadas: tuple[str, ...] = ()
    omitidas: tuple[ColumnaOmitida, ...] = ()

    def como_manifiesto(self) -> dict[str, Any]:
        """Forma serializable para el manifiesto (``corrida.cruce``)."""
        return {
            "adjuntadas": list(self.adjuntadas),
            "omitidas": [omitida.como_manifiesto() for omitida in self.omitidas],
        }


def _omitidas_por_ingesta(
    config: ConfigCruce, reportes_carga: Mapping[str, Mapping[str, Any]]
) -> tuple[ColumnaOmitida, ...]:
    """Columnas opcionales pedidas que cada fuente NO tiene, según el lector.

    No recalcula la regla: lee ``missing_optional`` del informe de ingesta
    (``SourceLoadReport`` / ``DuckDBCompactionResult``), que es donde el lector
    la decidió. Se excluyen las columnas que el motor usa para decidir: esas no
    son de arrastre.
    """
    motor = _columnas_de_motor(config)
    omitidas: list[ColumnaOmitida] = []
    for spec in config.fuentes:
        for canonica in reportes_carga[spec.name]["missing_optional"]:
            if canonica in motor:
                continue
            origen = spec.optional_column_mapping[canonica]
            omitidas.append(
                ColumnaOmitida(
                    columna=str(canonica),
                    motivo=motivo_fuente_sin_columna(str(origen)),
                    fuente=spec.name,
                )
            )
    return tuple(omitidas)


def _omitidas_por_payload_no_publicado(
    orden_fuentes: Sequence[str], compactaciones: Mapping[str, DuckDBCompactionResult]
) -> tuple[ColumnaOmitida, ...]:
    """Columnas no-matcher que DuckDB produjo y ``preservar_payload=False`` dejó
    fuera de la entrega: ``discarded_payload_columns`` del compactador, que es
    quien decidió no publicarlas."""
    return tuple(
        ColumnaOmitida(columna=columna, motivo=MOTIVO_SIN_PAYLOAD, fuente=nombre)
        for nombre in orden_fuentes
        for columna in compactaciones[nombre].discarded_payload_columns
    )


def _reporte_arrastre_observado(
    columnas_finales: Iterable[str],
    esperadas: Sequence[str],
    columnas_motor: Iterable[str],
    omitidas_ingesta: Sequence[ColumnaOmitida],
    *,
    que_hacer: str = ColumnasArrastreError.QUE_HACER_ENTREGA_INCOMPLETA,
) -> ReporteColumnasArrastre:
    """Deriva el reporte de la correlativa ENTREGADA; es la misma regla en los
    tres caminos.

    Args:
        columnas_finales: columnas de la correlativa que se entrega.
        esperadas: columnas de arrastre que las fuentes aportaron fuera del
            motor (unión de parquets derramados, de ``payload_columns`` DuckDB
            o de las columnas no-motor que viajaron por el pipeline).
        columnas_motor: columnas que el motor produjo ANTES del re-adjunte;
            una esperada con ese nombre es un choque y no se adjunta.
        omitidas_ingesta: lo que el lector (o el compactador) declaró ausente
            por fuente.
        que_hacer: remedio del error; por defecto el de la entrega
            (:attr:`ColumnasArrastreError.QUE_HACER_ENTREGA_INCOMPLETA`). El
            camino con parquets derramados pasa el de la alineación
            (:attr:`ColumnasArrastreError.QUE_HACER_POR_DEFECTO`).

    Raises:
        ColumnasArrastreError: si una esperada que no choca con el motor no
            está en la correlativa: eso no es una omisión declarable, es una
            entrega incompleta.
    """
    finales = set(columnas_finales)
    motor = set(columnas_motor)
    adjuntadas = tuple(c for c in esperadas if c not in motor and c in finales)
    perdidas = [c for c in esperadas if c not in motor and c not in finales]
    if perdidas:
        raise ColumnasArrastreError(
            f"la correlativa entregada no trae {perdidas} aunque las fuentes las "
            "aportaron fuera del motor",
            que_hacer=que_hacer,
        )
    choques = tuple(
        ColumnaOmitida(columna=c, motivo=MOTIVO_CHOQUE_CON_MOTOR) for c in esperadas if c in motor
    )
    return ReporteColumnasArrastre(
        adjuntadas=adjuntadas, omitidas=tuple(omitidas_ingesta) + choques
    )


def _adjuntar_columnas_extra(
    correlativa: pd.DataFrame,
    rutas: dict[str, Path],
    columnas: list[str],
    longitudes: dict[str, int],
    orden_fuentes: list[str],
    log: Any,
    *,
    omitidas_ingesta: Sequence[ColumnaOmitida] = (),
) -> tuple[pd.DataFrame, ReporteColumnasArrastre]:
    """Re-adjunta a la correlativa las columnas derramadas, por posición.

    El contrato (verificado con datos reales, colapso incluido) es que en la
    correlativa expandida ``ORIGINAL_INDEX = offset_de_la_fuente + fila
    original``. Eso permite un ``take`` posicional exacto, sin merges.

    F1.8: nunca devuelve la correlativa intacta en silencio. Si una parte
    derramada no tiene las filas de su fuente, si la unión no tiene las de la
    correlativa o si falta ``ORIGINAL_INDEX``, levanta
    :class:`~record_linkage.pipeline.errores.ColumnasArrastreError`. Un
    choque de nombre con una columna que ya produjo el motor no pisa la del
    motor: queda en ``omitidas`` con :data:`MOTIVO_CHOQUE_CON_MOTOR`.

    Args:
        omitidas_ingesta: lo que el lector declaró ausente por fuente
            (:func:`_omitidas_por_ingesta`); viaja al reporte tal cual.

    Returns:
        ``(correlativa, reporte)``; el reporte se deriva de la correlativa
        resultante con :func:`_reporte_arrastre_observado`.
    """
    columnas_motor = list(correlativa.columns)
    if rutas:
        if "ORIGINAL_INDEX" not in correlativa.columns:
            raise ColumnasArrastreError(
                "la correlativa no trae ORIGINAL_INDEX y hay columnas de arrastre "
                f"derramadas para {sorted(rutas)}; sin esa columna no hay forma de "
                "alinearlas por posición"
            )
        partes: list[pd.DataFrame] = []
        for nombre in orden_fuentes:
            n = longitudes.get(nombre, 0)
            if nombre in rutas:
                parte = _normalizar_dtypes_texto(pd.read_parquet(rutas[nombre]))
                if len(parte) != n:
                    raise ColumnasArrastreError(
                        f"las columnas de arrastre de {nombre} ({rutas[nombre]}) traen "
                        f"{len(parte):,} filas y la fuente entró con {n:,}; la "
                        "alineación posicional no cuadra",
                        fuente=nombre,
                        esperadas=n,
                        observadas=len(parte),
                    )
            else:
                parte = pd.DataFrame(index=pd.RangeIndex(n))
            partes.append(parte)
        extras = pd.concat(partes, ignore_index=True)
        if len(extras) != len(correlativa):
            raise ColumnasArrastreError(
                f"la unión de columnas de arrastre trae {len(extras):,} filas y la "
                f"correlativa {len(correlativa):,}; la alineación posicional no cuadra",
                esperadas=len(correlativa),
                observadas=len(extras),
            )
        posiciones = pd.to_numeric(correlativa["ORIGINAL_INDEX"], errors="raise").to_numpy(
            dtype="int64"
        )
        for columna in columnas:
            if columna in extras.columns and columna not in correlativa.columns:
                correlativa[columna] = extras[columna].array.take(posiciones)
    reporte = _reporte_arrastre_observado(
        correlativa.columns,
        columnas,
        columnas_motor,
        omitidas_ingesta,
        que_hacer=ColumnasArrastreError.QUE_HACER_POR_DEFECTO,
    )
    _registrar_reporte_arrastre(reporte, log)
    return correlativa, reporte


def _registrar_reporte_arrastre(reporte: ReporteColumnasArrastre, log: Any) -> None:
    """Deja en el registro lo mismo que va al manifiesto, en los tres caminos."""
    if reporte.adjuntadas:
        log.info(f"   📎 Re-adjuntadas a la correlativa: {list(reporte.adjuntadas)}")
    for omitida in reporte.omitidas:
        donde = f" en {omitida.fuente}" if omitida.fuente else ""
        log.warning(
            f"   ⚠️ Columna de arrastre omitida {omitida.columna!r}{donde}: {omitida.motivo}"
        )


def resolver_motor(config: ConfigCruce, log: Any = None) -> ConfigCruce:
    """Fija ``motor_ingesta``/``modo_resultado`` cuando vienen en ``"auto"``.

    El problema que resuelve es de orden, no de rendimiento: la elección entre
    el camino en RAM y el camino en disco hay que hacerla ANTES de leer nada,
    y hasta 0.17.2 la única evidencia disponible era la caché de una corrida
    previa —justo lo que no existe en la primera corrida, que es la que
    revienta la sesión—. Aquí el universo se dimensiona leyendo kilobytes
    (metadatos de Parquet, tamaño descomprimido declarado en el ZIP, muestreo
    estratificado del ancho de línea) y la decisión se toma con esa cifra.

    Tres reglas, en este orden:

    1. Si el tamaño **no** se puede establecer, gana DuckDB. Correr más lento
       es recuperable; quedarse sin RAM a los cuarenta minutos, con el trabajo
       perdido, no lo es.
    2. Una cifra estimada se compara **inflada** por
       :data:`~record_linkage.ingestion.dimensionado.MARGEN_INCERTIDUMBRE`,
       para que el error del muestreo nunca empuje hacia el motor que se cae.
    3. Las opciones que solo existen en el camino pandas (``dir_procesados``,
       ``forzar_relectura``) se desactivan con aviso si la resolución va a
       DuckDB: son cachés, no semántica. En cambio
       ``colapsar_duplicados_exactos=False`` sí cambia el contrato de salida,
       así que en ese caso se respeta la decisión del usuario y se conserva
       pandas, avisando del riesgo.

    Args:
        config: configuración a resolver. Se modifica en el sitio y se
            devuelve, para poder encadenar.
        log: logger opcional; sin él la decisión no se narra.

    Returns:
        El mismo ``config``, con motor y modo ya concretos.
    """
    if config.motor_ingesta != "auto":
        return config

    universo = resumir_universo(config.fuentes)
    decision = universo.filas_con_margen
    if config.modo_resultado == "disco":
        motivo = "modo_resultado='disco' exige el motor de disco"
        elegido = "duckdb"
    elif decision is None:
        sin_medir = [e.fuente for e in universo.por_fuente if e.filas is None]
        motivo = f"no se pudo dimensionar {sin_medir}; se toma el lado seguro"
        elegido = "duckdb"
    elif decision > config.umbral_filas_disco:
        exactitud = "" if universo.exacto else " (con margen de estimación)"
        motivo = f"{decision:,} filas{exactitud} superan el umbral de {config.umbral_filas_disco:,}"
        elegido = "duckdb"
    else:
        motivo = f"{decision:,} filas caben en memoria"
        elegido = "pandas"

    if elegido == "duckdb" and not config.colapsar_duplicados_exactos:
        elegido = "pandas"
        motivo = (
            "colapsar_duplicados_exactos=False no es compatible con DuckDB; se "
            "conserva pandas PESE al tamaño — vigile la RAM o active el colapso"
        )
        if log:
            log.warning(f"   ⚠️ Motor auto → pandas: {motivo}.")
    elif log:
        log.info(f"   ⚙️ Motor auto → {elegido}: {motivo}.")
    for estimacion in universo.por_fuente:
        if log:
            log.info(f"      · {estimacion.fuente}: {estimacion.detalle}")

    config.motor_ingesta = elegido
    if elegido == "duckdb":
        if config.dir_procesados is not None:
            if log:
                log.info("      (dir_procesados no aplica a DuckDB; se ignora)")
            config.dir_procesados = None
        if config.forzar_relectura:
            if log:
                log.info("      (forzar_relectura no aplica a DuckDB; se ignora)")
            config.forzar_relectura = False
        if config.duckdb_settings is None:
            config.duckdb_settings = DuckDBIngestionSettings()
    if config.modo_resultado == "auto":
        config.modo_resultado = (
            "disco" if (elegido == "duckdb" and not config.exportar_excel) else "dataframe"
        )
    return config


def ejecutar_cruce(config: ConfigCruce) -> ResultadoCruce | ResultadoCruceDisco:
    """Ejecuta el cruce completo con validación, smoke test y la carpeta del estándar.

    Args:
        config: parámetros de la corrida, ya validados.

    Returns:
        En modo dataframe, :class:`ResultadoCruce` (un ``ResultadoLinkage``
        con golden, correlativa, métricas, manifiesto y ``rutas`` de la carpeta
        del estándar escrita). En modo disco, :class:`ResultadoCruceDisco`
        (referencias Parquet a la generación publicada, que tiene la forma del
        estándar; ``.leer()`` la materializa).

    Raises:
        FileNotFoundError: si alguna fuente no existe (preflight).
        PermissionError: si el workspace no es escribible (preflight).
        ContratoSalidaError: si el resultado incumple el contrato de salida o
            no conserva las filas de entrada (``_validar_cruce``).
        EscrituraSalidaError: si la carpeta del estándar no se puede publicar
            (p. ej. ya existe una con el mismo nombre en el mismo minuto).
        ColumnasArrastreError: si las columnas de arrastre no se pudieron
            re-adjuntar a la correlativa (F1.8: nunca se omiten en silencio).
    """
    log = CustomLogger("flujo.cruce")
    cronometro = _Cronometro()
    try:
        return _ejecutar_cruce_medido(config, log, cronometro)
    finally:
        # v0.17.0: el hilo de muestreo se detiene y se hace join pase lo que
        # pase — incluida una excepción a mitad de corrida.
        cronometro.cerrar()


def _golden_de(salida: ResultadoLinkage) -> pd.DataFrame:
    """El golden que ``linkage()`` publicó; sin él el cruce no tiene entregable."""
    if salida.golden is None:
        raise ContratoSalidaError(
            [
                mensaje_accionable(
                    "linkage() devolvió un resultado sin golden.",
                    "el flujo de cruce publica golden y correlativa.",
                    "revise la ruta de linkage/collapse_exact_duplicates usada por ejecutar_cruce.",
                )
            ]
        )
    return salida.golden


def _conflictos_identificador_de(salida: ResultadoLinkage) -> int:
    """QA de identificador (grupos que mezclan dos bases válidas) que dejó ``salida.completar``.

    Lo calculó ``completar`` con las técnicas del motor; de aquí sale a
    ``metricas``. No se recalcula desde la correlativa publicada, que ya no
    trae columnas técnicas.
    """
    conflictos = (
        salida.manifiesto.get("completar", {})
        .get("identificador", {})
        .get("grupos_con_bases_distintas")
    )
    if conflictos is None:
        raise ContratoSalidaError(
            [
                mensaje_accionable(
                    "linkage() devolvió un manifiesto sin completar.identificador."
                    "grupos_con_bases_distintas.",
                    "el QA de identificador del cruce se toma de ahí, no se recalcula "
                    "desde la correlativa publicada (sin columnas técnicas).",
                    "revise que linkage() complete el contrato con salida.completar.",
                )
            ]
        )
    return int(conflictos)


def _ejecutar_cruce_medido(
    config: ConfigCruce, log: Any, cronometro: _Cronometro
) -> ResultadoCruce | ResultadoCruceDisco:
    """Cuerpo instrumentado de :func:`ejecutar_cruce`.

    Separado para que el cierre del muestreo de RSS viva en un ``finally``
    sin anidar todo el flujo en un bloque gigante.
    """
    log.info("═" * 62)
    log.info(f"🚀 Cruce de {len(config.fuentes)} fuentes → {config.ruta_workspace}")

    with cronometro.fase("preflight"):
        _preflight(config, log)
        resolver_motor(config, log)
    if not config.reusar_checkpoints:
        with cronometro.fase("limpieza de checkpoints"):
            _limpiar_checkpoints(config)
    with cronometro.fase("carga de fuentes"):
        cargadas, compactaciones = _cargar(config, log)
    reportes_carga = {nombre: reporte for nombre, (_, reporte) in cargadas.items()}
    marcos = {nombre: datos for nombre, (datos, _) in cargadas.items()}
    # Los reportes no necesitan retener DataFrames. A partir de aquí ``marcos``
    # es el único dueño del conjunto compacto y puede transferirlo a linkage.
    del cargadas
    if config.motor_ingesta == "pandas":
        for nombre, limite in (config.limite_filas or {}).items():
            if len(marcos[nombre]) > limite:
                log.warning(
                    f"   ✂️  {nombre}: recortada a {limite:,} de {len(marcos[nombre]):,} filas "
                    f"(ENSAYO, no es una corrida de producción)"
                )
                marcos[nombre] = marcos[nombre].head(limite).reset_index(drop=True)
        filas_entrada = sum(len(df) for df in marcos.values())
    else:
        filas_entrada = sum(item.input_rows for item in compactaciones.values())
    por_fuente_entrada = {
        nombre: (compactaciones[nombre].input_rows if nombre in compactaciones else len(datos))
        for nombre, datos in marcos.items()
    }
    source_order = [spec.name for spec in config.fuentes]

    # v0.17.0 — las columnas que no puntúan salen del motor antes del cruce.
    # Solo el camino pandas: con DuckDB la proyección matcher/payload ya
    # ocurre en el staging y ``preservar_payload`` hace el re-adjunte.
    rutas_extra: dict[str, Path] = {}
    columnas_extra: list[str] = []
    # F1.8 — el manifiesto declara lo que REALMENTE está en la correlativa
    # entregada, en los tres caminos; las ausencias por fuente las dijo el lector.
    omitidas_ingesta = _omitidas_por_ingesta(config, reportes_carga)
    reporte_arrastre: ReporteColumnasArrastre
    longitudes_fuente: dict[str, int] = {n: len(df) for n, df in marcos.items()}
    if not compactaciones:
        with cronometro.fase("separar columnas"):
            rutas_extra, columnas_extra, longitudes_fuente = _separar_columnas_extra(
                marcos, config, log
            )

    dir_smoke = config.ruta_trabajo / "_smoke"
    dir_corrida = config.ruta_trabajo / "corrida"
    if config.filas_smoke:
        muestras = {nombre: df.head(config.filas_smoke) for nombre, df in marcos.items() if len(df)}
        inicio = time.time()
        with cronometro.fase("smoke test"):
            linkage(
                muestras,
                trusted_sources=set(config.confiables),
                col_nit=config.col_nit,
                col_name=config.col_nombre,
                col_ciudad=config.col_ciudad,
                extra_features=config.variables_extra,
                work_dir=str(dir_smoke),
                profile=config.perfil,
                ajustes_perfil=config.ajustes_perfil,
                matching_profile=config.perfil_multicampo,
                skip_reporting=True,
                collapse_exact_duplicates=(
                    config.colapsar_duplicados_exactos and not compactaciones
                ),
            )
        log.info(f"   🔬 Smoke test OK ({time.time() - inicio:.1f}s) — se procede al total")

    inicio_link = time.time()
    with cronometro.fase("cruce"):
        salida = linkage(
            marcos,
            trusted_sources=set(config.confiables),
            col_nit=config.col_nit,
            col_name=config.col_nombre,
            col_ciudad=config.col_ciudad,
            extra_features=config.variables_extra,
            work_dir=str(dir_corrida),
            profile=config.perfil,
            ajustes_perfil=config.ajustes_perfil,
            matching_profile=config.perfil_multicampo,
            skip_reporting=True,
            collapse_exact_duplicates=(config.colapsar_duplicados_exactos and not compactaciones),
            consume_sources=True,
        )
    segundos_link = time.time() - inicio_link
    # F1.9: linkage() devuelve ResultadoLinkage (contrato 1.0); las claves del
    # dict viejo siguen funcionando pero avisan, y la librería no se avisa a
    # sí misma.
    golden_compacta = _golden_de(salida)
    correlativa_compacta = salida.correlativa
    conflictos_identificador = _conflictos_identificador_de(salida)

    def metricas_de(filas_correlativa: int, entidades: int) -> dict[str, Any]:
        return _metricas_cruce(
            cronometro,
            filas_entrada=filas_entrada,
            filas_correlativa=filas_correlativa,
            entidades=entidades,
            conflictos_identificador=conflictos_identificador,
            segundos_linkage=segundos_link,
            por_fuente=por_fuente_entrada,
        )

    if compactaciones:
        # La expansión y el re-adjunte de columnas de arrastre ocurren dentro
        # de DuckDB (``preservar_payload``). F1.8 — ``expandir_en_disco``
        # adjunta el payload por nombre y descarta el que choca con una columna
        # del motor: el manifiesto se deriva de la correlativa publicada.
        payload_esperado = list(
            dict.fromkeys(
                c for nombre in source_order for c in compactaciones[nombre].payload_columns
            )
        )
        omitidas_payload = omitidas_ingesta + _omitidas_por_payload_no_publicado(
            source_order, compactaciones
        )
        insumos = _insumos_reales(salida.manifiesto.get("entradas") or {}, compactaciones)

        def arrastre_de(expansion: ExpansionDisco) -> ReporteColumnasArrastre:
            reporte = _reporte_arrastre_observado(
                expansion.correlativa.columns,
                payload_esperado,
                correlativa_compacta.columns,
                omitidas_payload,
            )
            _registrar_reporte_arrastre(reporte, log)
            return reporte

        if config.modo_resultado == "disco":
            return _publicar_en_disco(
                config,
                salida,
                golden_compacta,
                compactaciones=compactaciones,
                source_order=source_order,
                insumos=insumos,
                arrastre_de=arrastre_de,
                metricas_de=metricas_de,
                reportes_carga=reportes_carga,
                dir_corrida=dir_corrida,
                cronometro=cronometro,
                log=log,
            )

        # Motor DuckDB en modo dataframe: la expansión queda en _trabajo/
        # (borrable) y la carpeta del estándar la escribe el escritor único,
        # igual que por el camino pandas.
        dir_expansion = dir_corrida / "expansion"
        dir_expansion.mkdir(parents=True, exist_ok=True)
        with cronometro.fase("expansión"):
            expansion = expandir_en_disco(
                golden_compacta,
                correlativa_compacta,
                source_order=source_order,
                compactaciones=compactaciones,
                carpeta=dir_expansion,
                settings=config.duckdb_settings,
            )
        reporte_arrastre = arrastre_de(expansion)
        res = _resultado_cruce(
            salida,
            expansion.correlativa.to_pandas(),
            expansion.golden.to_pandas(),
            reporte_arrastre,
            reportes_carga=reportes_carga,
            insumos=insumos,
        )
    else:
        correlativa = correlativa_compacta
        if config.separar_columnas_extra:
            with cronometro.fase("re-adjuntar columnas"):
                correlativa, reporte_arrastre = _adjuntar_columnas_extra(
                    correlativa,
                    rutas_extra,
                    columnas_extra,
                    longitudes_fuente,
                    source_order,
                    log,
                    omitidas_ingesta=omitidas_ingesta,
                )
        else:
            # Las columnas viajaron por el motor y una con el nombre de una que
            # el motor escribe quedó PISADA: la correlativa la trae, pero con
            # los valores del motor. Se declara choque, no «adjuntada».
            reporte_arrastre = _reporte_arrastre_observado(
                correlativa.columns,
                columnas_extra,
                COLUMNAS_QUE_PRODUCE_EL_MOTOR,
                omitidas_ingesta,
            )
            _registrar_reporte_arrastre(reporte_arrastre, log)
        res = _resultado_cruce(
            salida, correlativa, golden_compacta, reporte_arrastre, reportes_carga=reportes_carga
        )

    # Invariantes: el contrato (validar) más el cruce de filas del flujo, ANTES
    # de escribir nada.
    with cronometro.fase("invariantes"):
        _validar_cruce(res, filas_entrada=filas_entrada)
    metricas = metricas_de(len(res.correlativa), int(res.correlativa["ID_GRUPO"].nunique()))
    res.metricas.update(metricas)
    res.manifiesto["cruce"] = _bloque_cruce(config, reporte_arrastre, metricas, reportes_carga)
    with cronometro.fase("exportes"):
        manifiesto = escribir_resultado(
            res, config.ruta_workspace, config.nombre, excel=config.exportar_excel
        )
    # F1: las columnas técnicas (NIT_BASE, NIT_VALID…) salieron del entregable
    # y quedan en el checkpoint de L5 de esta carpeta; el resultado la señala
    # para que `salida.tecnicas.adjuntar_tecnicas` las recupere sin adivinar.
    res.carpeta = manifiesto.carpeta
    res.rutas = _rutas_de(manifiesto, dir_corrida)
    _cerrar_tiempos(metricas, cronometro)
    res.metricas.update(metricas)
    res.manifiesto["cruce"]["metricas"] = _normalizar_json(metricas)
    log.info(f"🏁 Listo en {metricas['segundos_total']:.1f}s · {metricas['entidades']:,} entidades")
    log.info(f"   📁 {manifiesto.carpeta}")
    log.info("═" * 62)
    return res


def _publicar_en_disco(
    config: ConfigCruce,
    salida: ResultadoLinkage,
    golden_compacta: pd.DataFrame,
    *,
    compactaciones: dict[str, DuckDBCompactionResult],
    source_order: list[str],
    insumos: Mapping[str, Any],
    arrastre_de: Callable[[ExpansionDisco], ReporteColumnasArrastre],
    metricas_de: Callable[[int, int], dict[str, Any]],
    reportes_carga: dict[str, Any],
    dir_corrida: Path,
    cronometro: _Cronometro,
    log: Any,
) -> ResultadoCruceDisco:
    """Modo disco: una generación nueva con la forma del estándar, sin materializar.

    ``publicar_resultados_duckdb`` expande y verifica en SQL; ``completar``
    escribe el resto del estándar dentro de la generación pendiente, con el
    manifiesto del cruce ya en ``salida.manifiesto['cruce']``, y solo entonces
    se publica (``rename`` + ``resultados.manifest.json``).
    """
    capturado: dict[str, Any] = {}

    def completar(_pendiente: Path, definitiva: Path, expansion: ExpansionDisco) -> dict[str, str]:
        reporte_arrastre = arrastre_de(expansion)
        metricas = metricas_de(expansion.correlativa.rows, expansion.n_grupos)
        salida.manifiesto["funcion"] = "ejecutar_cruce"
        salida.manifiesto["cruce"] = _bloque_cruce(
            config, reporte_arrastre, metricas, reportes_carga
        )
        manifiesto = escribir_estandar_de_expansion(
            salida,
            expansion,
            nombre=config.nombre,
            definitiva=definitiva,
            insumos=insumos,
            columnas_fuente=reporte_arrastre.adjuntadas,
        )
        capturado.update(manifiesto=manifiesto, metricas=metricas)
        return {"manifest": NOMBRE_MANIFEST}

    with cronometro.fase("exportes"):
        publicacion = publicar_resultados_duckdb(
            golden_compacta,
            salida.correlativa,
            source_order=source_order,
            compactaciones=compactaciones,
            output_directory=config.ruta_workspace,
            settings=config.duckdb_settings,
            completar=completar,
        )
    manifiesto: Manifiesto = capturado["manifiesto"]
    metricas: dict[str, Any] = capturado["metricas"]
    rutas = _rutas_de(manifiesto, dir_corrida, con_carpeta=False)
    rutas["generation_dir"] = publicacion.generation_dir
    rutas["manifest_generaciones"] = publicacion.manifest_path
    _cerrar_tiempos(metricas, cronometro)
    salida.manifiesto["cruce"]["metricas"] = _normalizar_json(metricas)
    log.info(f"🏁 Listo en {metricas['segundos_total']:.1f}s · {metricas['entidades']:,} entidades")
    log.info(f"   📁 {publicacion.generation_dir}")
    log.info("═" * 62)
    return ResultadoCruceDisco(
        golden=publicacion.golden,
        correlativa=publicacion.correlativa,
        reportes_carga=reportes_carga,
        metricas=metricas,
        rutas=rutas,
        carpeta=publicacion.generation_dir,
        _publicacion=publicacion,
    )
