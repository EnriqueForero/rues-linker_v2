"""Preparación de insumos: Drive → disco local, una sola vez (v0.14.0)."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

import pandas as pd

from ..ingestion import SourceSpec, load_source
from ..pipeline.errores import mensaje_accionable
from ..utils.almacenamiento import es_ruta_fuse

__all__ = [
    "cargar_fuente_con_cache",
    "escribir_cache",
    "filas_en_cache",
    "leer_cache",
    "preparar_insumo_local",
    "ruta_en_cache",
]


def preparar_insumo_local(
    origen: Path | str,
    destino_dir: Path | str,
    *,
    forzar: bool = False,
    logger=None,
) -> Path:
    """Copia un insumo de Drive al disco local de la VM si hace falta.

    Leer repetidamente desde un montaje FUSE es la causa más común de que una
    corrida larga en Colab muera a mitad de camino ("transport endpoint is not
    connected") y, aun sin fallar, multiplica el tiempo de I/O. La copia se
    hace UNA vez y se reutiliza mientras el tamaño coincida.

    Si el origen no está en un montaje FUSE, se devuelve tal cual: no se paga
    ninguna copia innecesaria.

    Args:
        origen: archivo de entrada (típicamente un .zip en Drive).
        destino_dir: carpeta local donde dejar la copia.
        forzar: si True, recopia aunque exista una copia del mismo tamaño.
        logger: logger opcional para trazar la decisión.

    Returns:
        Ruta que debe usarse para leer el insumo.

    Raises:
        FileNotFoundError: si el origen no existe.
    """
    origen = Path(origen).expanduser()
    if not origen.exists():
        raise FileNotFoundError(
            mensaje_accionable(
                f"no existe el insumo '{origen}'.",
                "sin él no hay nada que procesar.",
                "verifique la ruta (¿montó Drive?, ¿el nombre lleva tildes o espacios distintos?).",
            )
        )
    if not es_ruta_fuse(origen):
        return origen

    destino_dir = Path(destino_dir).expanduser()
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / origen.name
    tam_origen = origen.stat().st_size
    if not forzar and destino.exists() and destino.stat().st_size == tam_origen:
        if logger:
            logger.info(f"   ♻️  Insumo ya copiado: {destino.name}")
        return destino

    inicio = time.time()
    # Copia a un temporal hermano y publicación atómica: una sesión que muere
    # a mitad de copia no deja un archivo truncado que parezca válido.
    temporal = destino.with_name(destino.name + ".parcial")
    shutil.copyfile(origen, temporal)
    temporal.replace(destino)
    if logger:
        mb = tam_origen / 1024**2
        logger.info(
            f"   📥 {origen.name}: Drive → local ({mb:,.0f} MB en {time.time() - inicio:.1f}s)"
        )
    return destino


def _huella_de_contrato(spec: SourceSpec) -> str:
    """Huella de lo que define la PROYECCIÓN, sin depender del archivo.

    Entra el nombre del archivo y cada parámetro del contrato que cambia el
    DataFrame resultante. Deliberadamente NO entra el tamaño ni la fecha del
    archivo: eso se guarda aparte, de modo que la caché siga siendo
    localizable aunque el original ya no esté a mano (Drive sin montar, el
    archivo movido de sitio). El estado del archivo se compara al leer.
    """
    partes = [
        Path(spec.path).name,
        spec.name,
        str(spec.format),
        str(spec.compression),
        str(spec.encoding),
        repr(spec.delimiter),
        str(spec.header),
        str(spec.archive_member),
        str(spec.sheet_name),
        str(spec.keep_unmapped),
        repr(sorted(dict(spec.column_mapping).items())),
        repr(sorted(dict(spec.optional_column_mapping).items())),
        repr(tuple(spec.passthrough_columns)),
        repr(sorted((k, str(v)) for k, v in dict(spec.column_types).items())),
        repr(sorted((k, str(v)) for k, v in dict(spec.identifier_formats).items())),
        repr(sorted((k, str(v)) for k, v in dict(spec.numeric_formats).items())),
        repr(sorted((k, tuple(v)) for k, v in dict(spec.null_values).items())),
        repr(tuple(spec.global_null_values)),
        str(spec.invalid_values),
    ]
    # La huella no es una firma criptográfica, pero SHA-256 evita depender de
    # un algoritmo retirado por las compuertas modernas de seguridad. Se
    # conservan 16 hex para mantener nombres de archivo compactos.
    return hashlib.sha256("\x1f".join(partes).encode("utf-8")).hexdigest()[:16]


def _estado_del_archivo(spec: SourceSpec) -> dict[str, Any] | None:
    """Tamaño y fecha del archivo fuente, o None si no está accesible."""
    try:
        estado = Path(spec.path).expanduser().stat()
    except OSError:
        return None
    return {"bytes": estado.st_size, "mtime_ns": estado.st_mtime_ns}


def _normalizar_dtypes_texto(df: pd.DataFrame) -> pd.DataFrame:
    """Restituye strings Arrow al releer un Parquet de caché.

    ``load_source`` entrega texto como ``string[pyarrow]``. Pandas 2 puede
    releer esas columnas como ``object``, triplicando silenciosamente su uso
    de memoria. Las columnas object no convertibles se conservan intactas.
    """
    for columna in df.columns:
        if df[columna].dtype == object:
            try:
                df[columna] = df[columna].astype("string[pyarrow]")
            except (TypeError, ValueError):
                continue
    return df


def ruta_en_cache(spec: SourceSpec, dir_cache: Path | str) -> Path:
    """Ruta del Parquet que le corresponde a esta fuente con este contrato."""
    return Path(dir_cache).expanduser() / f"{spec.name}__{_huella_de_contrato(spec)}.parquet"


def filas_en_cache(spec: SourceSpec, dir_cache: Path | str | None) -> int | None:
    """Filas exactas de la caché de esta fuente, leyendo solo el pie del Parquet.

    Sirve para dimensionar el universo antes de decidir el motor sin pagar la
    lectura de ninguna columna: ``num_rows`` vive en los metadatos del archivo.
    Devuelve None si no hay caché vigente o si el pie no se puede leer; nunca
    lanza, porque dimensionar es una ayuda, no un resultado.
    """
    if dir_cache is None:
        return None
    try:
        import pyarrow.parquet as pq

        ruta = ruta_en_cache(spec, dir_cache)
        if not ruta.is_file():
            return None
        return int(pq.ParquetFile(ruta).metadata.num_rows)
    except Exception:
        return None


def leer_cache(
    spec: SourceSpec, dir_cache: Path | str | None, *, logger=None
) -> tuple[pd.DataFrame, dict[str, Any]] | None:
    """Devuelve la fuente ya proyectada si hay una caché vigente, o None.

    Una caché ilegible no aborta nada: se avisa y se vuelve a leer el original.
    """
    if dir_cache is None:
        return None
    ruta_parquet = ruta_en_cache(spec, dir_cache)
    ruta_reporte = ruta_parquet.with_suffix(".json")
    if not (ruta_parquet.is_file() and ruta_reporte.is_file()):
        return None
    try:
        reporte = json.loads(ruta_reporte.read_text(encoding="utf-8"))
    except Exception as exc:
        if logger:
            logger.warning(f"   ⚠️ Caché ilegible ({exc}); se vuelve a leer el original")
        return None

    estado_actual = _estado_del_archivo(spec)
    estado_guardado = reporte.get("_estado_archivo")
    if estado_actual is not None and estado_guardado != estado_actual:
        if logger:
            logger.info(f"   🔁 {spec.name}: el archivo cambió; la caché se descarta")
        return None
    if estado_actual is None and logger:
        logger.warning(
            f"   ⚠️ {spec.name}: no se ve el archivo original; se usa la caché "
            f"sin poder verificar que siga vigente"
        )

    try:
        datos = _normalizar_dtypes_texto(pd.read_parquet(ruta_parquet))
    except Exception as exc:
        if logger:
            logger.warning(f"   ⚠️ Caché ilegible ({exc}); se vuelve a leer el original")
        return None
    if logger:
        logger.info(
            f"   ♻️  {spec.name}: reutilizado de caché ({len(datos):,} filas · {ruta_parquet.name})"
        )
    return datos, reporte


def escribir_cache(
    spec: SourceSpec,
    dir_cache: Path | str | None,
    datos: pd.DataFrame,
    reporte: dict[str, Any],
    *,
    logger=None,
) -> Path | None:
    """Guarda la fuente proyectada para reutilizarla en la próxima corrida.

    Un fallo al escribir (sin permiso, sin espacio) nunca tumba la corrida: la
    caché es una optimización, no un resultado.
    """
    if dir_cache is None:
        return None
    ruta_parquet = ruta_en_cache(spec, dir_cache)
    contenido = dict(reporte)
    contenido["_estado_archivo"] = _estado_del_archivo(spec)
    try:
        ruta_parquet.parent.mkdir(parents=True, exist_ok=True)
        datos.to_parquet(ruta_parquet, index=False)
        ruta_parquet.with_suffix(".json").write_text(
            json.dumps(contenido, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
    except Exception as exc:
        if logger:
            logger.warning(f"   ⚠️ No se pudo escribir la caché ({exc}); la corrida sigue")
        return None
    if logger:
        logger.info(f"   💾 {spec.name}: caché escrita en {ruta_parquet.name}")
    return ruta_parquet


def cargar_fuente_con_cache(
    spec: SourceSpec,
    dir_cache: Path | str | None,
    *,
    spec_lectura: SourceSpec | None = None,
    forzar: bool = False,
    logger=None,
) -> tuple[pd.DataFrame, dict[str, Any], bool]:
    """Carga una fuente proyectada, reutilizando el Parquet si sigue vigente.

    Leer y proyectar el histórico del RUES cuesta minutos cada vez. Guardar el
    resultado ya proyectado convierte la segunda corrida —y todas las
    siguientes— en una lectura de Parquet de segundos, sin dejar de garantizar
    que un archivo o un contrato distintos invalidan la caché.

    Args:
        spec: contrato de la fuente. Define la LLAVE de la caché.
        dir_cache: carpeta de los Parquet reutilizables. ``None`` la desactiva.
        spec_lectura: contrato equivalente apuntando a una copia local, usado
            solo cuando hay que leer de verdad. La llave sigue siendo ``spec``,
            de modo que copiar el archivo no invalida lo ya cacheado.
        forzar: si True, ignora lo cacheado y vuelve a leer el original.
        logger: logger opcional para trazar qué camino se tomó.

    Returns:
        Tupla ``(datos, reporte, desde_cache)``.
    """
    if not forzar:
        encontrado = leer_cache(spec, dir_cache, logger=logger)
        if encontrado is not None:
            return encontrado[0], encontrado[1], True
    cargada = load_source(spec_lectura if spec_lectura is not None else spec)
    reporte = dict(vars(cargada.report))
    escribir_cache(spec, dir_cache, cargada.data, reporte, logger=logger)
    return cargada.data, reporte, False
