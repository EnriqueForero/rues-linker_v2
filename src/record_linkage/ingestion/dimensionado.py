"""Dimensionado barato de una fuente: cuántas filas trae, sin leerla (v0.17.3).

Motivación operativa
--------------------
La decisión "¿motor en RAM o motor en disco?" hay que tomarla ANTES de leer
nada: tomada mal, la sesión de Colab muere por OOM a los cuarenta minutos, con
todo el trabajo perdido. Hasta 0.17.2 esa decisión solo podía apoyarse en la
caché de una corrida previa —es decir, justo en la información que NO existe la
primera vez, que es la vez peligrosa—.

Este módulo estima el número de filas leyendo kilobytes, no gigabytes:

* Parquet        → ``num_rows`` del pie del archivo. Exacto y gratis.
* ZIP de texto   → tamaño descomprimido declarado en el directorio central del
                   ZIP (gratis, sin descomprimir) ÷ bytes por línea medidos
                   sobre una muestra de la cabecera.
* GZIP de texto  → tamaño descomprimido derivado del trailer ISIZE,
                   desambiguado con la razón de compresión medida.
* Texto plano    → tamaño en disco ÷ bytes por línea de dos ventanas (una tras
                   la cabecera y otra en el medio), lo que corrige el sesgo de
                   que las primeras filas suelan ser más cortas.

Sesgo deliberado
----------------
Se cuentan LÍNEAS FÍSICAS. Con saltos de línea dentro de campos entrecomillados
eso sobreestima las filas lógicas. Para una decisión de memoria sobreestimar es
el lado seguro: empuja al motor de disco, que es el que no se cae.

Ninguna estimación aborta nada. Si el formato no permite estimar barato, se
devuelve ``filas=None`` y quien decide debe elegir el lado conservador.
"""

from __future__ import annotations

import gzip
import os
import zipfile
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .readers import (
    _detect_compression,
    _detect_input_format,
    _select_zip_member,
    _validate_zip_infos,
)
from .specs import Compression, InputFormat, SourceSpec

__all__ = [
    "EstimacionFilas",
    "ResumenUniverso",
    "estimar_filas",
    "resumir_universo",
]

#: Diseño del muestreo: k ventanas equiespaciadas (muestreo sistemático
#: estratificado). Medir solo la cabecera del archivo sesga el ancho de línea
#: —las primeras filas suelen ser más cortas—; contra la base real de
#: exportaciones ese sesgo daba +21 % de error, y el muestreo estratificado lo
#: bajó a ±0,2 % por ~1 s de CPU. Dieciséis ventanas de 128 KiB = 2 MiB leídos:
#: a igual volumen leído, más estratos reducen el sesgo de curvatura (Jensen)
#: cuando el ancho de fila varía mucho a lo largo del archivo.
K_VENTANAS = 16
VENTANA_BYTES = 128 * 1024
MUESTRA_BYTES = K_VENTANAS * VENTANA_BYTES

#: Tope de bytes descomprimidos que se recorren para sondear un miembro de ZIP
#: o un GZIP: no son seekables, así que sondear al 94 % del archivo cuesta
#: descomprimir el 94 %. Por encima del tope se sondea solo el tramo inicial y
#: se avisa: una fuente de ese tamaño va a disco de todas formas, así que la
#: precisión ahí ya no cambia la decisión.
PRESUPUESTO_SONDEO_BYTES = 2 * 1024**3

#: Factor con el que se corrige una estimación NO exacta antes de compararla
#: contra un umbral de memoria. Medido contra las fuentes reales de ProColombia
#: el error se mantuvo bajo 2 %; 1,25 deja margen amplio y mantiene la decisión
#: del lado que no revienta la sesión.
MARGEN_INCERTIDUMBRE = 1.25


@dataclass(frozen=True)
class EstimacionFilas:
    """Cuántas filas se espera que traiga una fuente y con qué respaldo.

    Attributes:
        fuente: nombre lógico de la fuente (``SourceSpec.name``).
        filas: filas estimadas, o None si el formato no permite estimar barato.
        exacta: True solo cuando el número sale de metadatos (Parquet), no de
            un muestreo.
        metodo: etiqueta corta del camino usado, para poder auditar la cifra.
        bytes_datos: tamaño descomprimido de los datos, cuando se conoce.
        detalle: explicación legible, apta para imprimir en un notebook.
    """

    fuente: str
    filas: int | None
    exacta: bool
    metodo: str
    bytes_datos: int | None = None
    detalle: str = ""

    @property
    def filas_con_margen(self) -> int | None:
        """Filas a usar en una decisión de memoria (conservadora si es estimada)."""
        if self.filas is None:
            return None
        if self.exacta:
            return self.filas
        return int(self.filas * MARGEN_INCERTIDUMBRE)


@dataclass(frozen=True)
class ResumenUniverso:
    """Tamaño total del universo a procesar, agregando todas las fuentes."""

    por_fuente: tuple[EstimacionFilas, ...]

    @property
    def completo(self) -> bool:
        """True si TODAS las fuentes pudieron dimensionarse."""
        return bool(self.por_fuente) and all(e.filas is not None for e in self.por_fuente)

    @property
    def exacto(self) -> bool:
        """True si todas las cifras salen de metadatos, ninguna de muestreo."""
        return self.completo and all(e.exacta for e in self.por_fuente)

    @property
    def filas(self) -> int | None:
        """Filas totales, o None si alguna fuente no pudo dimensionarse."""
        if not self.completo:
            return None
        return sum(int(e.filas or 0) for e in self.por_fuente)

    @property
    def filas_con_margen(self) -> int | None:
        """Filas totales con margen de incertidumbre, para decidir el motor."""
        if not self.completo:
            return None
        return sum(int(e.filas_con_margen or 0) for e in self.por_fuente)


def _razon_compresion_gzip(ruta: Path, objetivo: int) -> float:
    """Bytes descomprimidos por byte comprimido, medida sin sesgo de búfer.

    Se alimenta ``zlib`` a mano en vez de usar :class:`gzip.GzipFile` porque
    este último lee por adelantado: al pedirle N bytes descomprimidos ya
    consumió del archivo bastante más de lo que necesitaba, y esa lectura
    anticipada inflaba el denominador —hasta 6x en archivos muy comprimibles—.
    Alimentando el descompresor por trozos, lo consumido es exacto.
    """
    descompresor = zlib.decompressobj(zlib.MAX_WBITS | 16)
    crudos = 0
    salida = 0
    with ruta.open("rb") as fh:
        while salida < objetivo:
            trozo = fh.read(64 * 1024)
            if not trozo:
                break
            crudos += len(trozo)
            salida += len(descompresor.decompress(trozo, objetivo - salida))
            if descompresor.eof:
                crudos -= len(descompresor.unused_data)
                break
            crudos -= len(descompresor.unconsumed_tail)
            if descompresor.unconsumed_tail:
                # Lo no consumido se vuelve a presentar en la próxima vuelta.
                fh.seek(-len(descompresor.unconsumed_tail), os.SEEK_CUR)
    return salida / max(1, crudos)


def _fracciones(k: int = K_VENTANAS) -> tuple[float, ...]:
    """Centros de k estratos iguales: 0,5/k, 1,5/k, ... (muestreo sistemático)."""
    return tuple((i + 0.5) / k for i in range(k))


def _ancho_de_linea(ventanas: Sequence[bytes]) -> float | None:
    """Ancho medio de línea agrupando ventanas, o None si no hay líneas.

    De cada ventana se descartan la primera y la última línea por estar
    cortadas: sin ese recorte el ancho medio queda sesgado hacia abajo.
    """
    bytes_completos = 0
    lineas = 0
    for ventana in ventanas:
        inicio = ventana.find(b"\n") + 1
        cuerpo = ventana[inicio:]
        fin = cuerpo.rfind(b"\n")
        if fin <= 0:
            continue
        completas = cuerpo[: fin + 1]
        n = completas.count(b"\n")
        if n:
            bytes_completos += len(completas)
            lineas += n
    if lineas < 1:
        return None
    return bytes_completos / lineas


def _sondear_secuencial(flujo, total: int, *, presupuesto: int) -> tuple[list[bytes], bool]:
    """Ventanas estratificadas sobre un flujo NO seekable barato hacia atrás.

    Devuelve (ventanas, completo). ``completo`` es False cuando el presupuesto
    obligó a concentrar el sondeo en el tramo inicial.
    """
    alcance = min(total, presupuesto)
    parcial = alcance < total
    ventanas: list[bytes] = []
    for fraccion in _fracciones():
        destino = int(alcance * fraccion)
        flujo.seek(destino)
        datos = flujo.read(VENTANA_BYTES)
        if not datos:
            break
        ventanas.append(datos)
    return ventanas, not parcial


def _sondear_seekable(fh, total: int) -> list[bytes]:
    """Ventanas estratificadas sobre un archivo local (seek gratis)."""
    ventanas: list[bytes] = []
    for fraccion in _fracciones():
        fh.seek(int(total * fraccion))
        datos = fh.read(VENTANA_BYTES)
        if not datos:
            break
        ventanas.append(datos)
    return ventanas


def _filas_desde_ventanas(
    total_bytes: int, ventanas: Sequence[bytes], *, lineas_cabecera: int
) -> int | None:
    """Filas = bytes totales ÷ ancho medio, descontando el encabezado."""
    ancho = _ancho_de_linea(ventanas)
    if ancho is None or ancho <= 0:
        return None
    return max(0, round(total_bytes / ancho) - max(0, lineas_cabecera))


def _contar_lineas(datos: bytes, *, lineas_cabecera: int) -> int:
    """Filas de un contenido leído ENTERO: conteo, no estimación."""
    n = datos.count(b"\n")
    if datos and not datos.endswith(b"\n"):
        n += 1
    return max(0, n - max(0, lineas_cabecera))


def _estimar_parquet(ruta: Path, nombre: str) -> EstimacionFilas:
    try:
        import pyarrow.parquet as pq

        filas = int(pq.ParquetFile(ruta).metadata.num_rows)
    except Exception as exc:  # pragma: no cover - depende del archivo
        return EstimacionFilas(
            fuente=nombre,
            filas=None,
            exacta=False,
            metodo="parquet_ilegible",
            detalle=f"No se pudo leer el pie del Parquet ({exc}).",
        )
    return EstimacionFilas(
        fuente=nombre,
        filas=filas,
        exacta=True,
        metodo="parquet_metadata",
        bytes_datos=ruta.stat().st_size,
        detalle=f"{filas:,} filas leídas del pie del Parquet (exacto, sin cargar datos).",
    )


def _estimar_zip(spec: SourceSpec, ruta: Path, lineas_cabecera: int) -> EstimacionFilas:
    with zipfile.ZipFile(ruta) as archivo:
        infos = _validate_zip_infos(archivo.infolist(), spec)
        miembro = _select_zip_member(infos, spec)
        formato = _detect_input_format(ruta, miembro.filename, InputFormat(spec.format))
        if formato not in {InputFormat.TXT, InputFormat.CSV}:
            return EstimacionFilas(
                fuente=spec.name,
                filas=None,
                exacta=False,
                metodo="zip_no_textual",
                bytes_datos=miembro.file_size,
                detalle=(
                    f"El miembro {miembro.filename!r} es {formato.value}: dentro de un ZIP "
                    "no se puede dimensionar sin descomprimirlo entero."
                ),
            )
        total = miembro.file_size
        if total <= MUESTRA_BYTES:
            with archivo.open(miembro) as flujo:
                contenido = flujo.read(MUESTRA_BYTES + 1)
            filas = _contar_lineas(contenido, lineas_cabecera=lineas_cabecera)
            return EstimacionFilas(
                fuente=spec.name,
                filas=filas,
                exacta=not spec.newlines_in_values,
                metodo="zip_conteo_completo",
                bytes_datos=total,
                detalle=(
                    f"{filas:,} líneas contadas sobre el miembro completo "
                    f"({total / 1024**2:.1f} MiB descomprimidos)."
                ),
            )
        with archivo.open(miembro) as flujo:
            ventanas, completo = _sondear_secuencial(
                flujo, total, presupuesto=PRESUPUESTO_SONDEO_BYTES
            )
    filas = _filas_desde_ventanas(total, ventanas, lineas_cabecera=lineas_cabecera)
    if filas is None:
        return EstimacionFilas(
            fuente=spec.name,
            filas=None,
            exacta=False,
            metodo="zip_sin_lineas",
            bytes_datos=total,
            detalle="Las ventanas no contienen saltos de línea; no se puede medir el ancho de fila.",
        )
    alcance = "todo el miembro" if completo else "solo el tramo inicial (presupuesto de sondeo)"
    return EstimacionFilas(
        fuente=spec.name,
        filas=filas,
        exacta=False,
        metodo="zip_ancho_estratificado" if completo else "zip_ancho_parcial",
        bytes_datos=total,
        detalle=(
            f"~{filas:,} filas: {total / 1024**2:,.0f} MiB descomprimidos (declarados en el "
            f"directorio del ZIP) ÷ ancho medio de {len(ventanas)} ventanas sobre {alcance}."
        ),
    )


def _desenrollar_isize(isize: int, estimado: float, comprimido: int) -> tuple[int, bool]:
    """Recupera el tamaño real a partir del trailer (módulo 2^32) y una pista.

    Args:
        isize: valor del trailer, es decir tamaño real módulo 2^32.
        estimado: tamaño aproximado, solo para elegir cuántas vueltas dio el
            contador. Basta con acertar dentro de ±2 GiB.
        comprimido: tamaño del archivo en disco, para validar el resultado.

    Returns:
        (tamaño, viene_del_trailer). DEFLATE no comprime más de 1032:1 ni
        expande de forma apreciable: un resultado fuera de esa banda solo puede
        venir de un gzip multi-miembro —donde el trailer describe el último
        miembro, no el archivo—, y entonces manda el estimado.
    """
    vueltas = max(0, round((estimado - isize) / 2**32))
    por_trailer = isize + vueltas * 2**32
    if comprimido * 0.5 <= por_trailer <= comprimido * 1100:
        return por_trailer, True
    return int(estimado), False


def _tamano_descomprimido_gzip(ruta: Path) -> tuple[int, bool, str]:
    """(bytes descomprimidos, es_exacto, método).

    El trailer de gzip guarda el tamaño descomprimido **módulo 2^32**, así que
    por encima de 4 GiB hay que saber cuántas vueltas dio el contador. Para eso
    —y solo para eso— se mide la razón de compresión: elegir bien la vuelta
    necesita una precisión de ±2 GiB, que cualquier medición razonable alcanza
    con holgura. El número final sigue saliendo del trailer, que es exacto.
    """
    comprimido = ruta.stat().st_size
    with gzip.open(ruta, "rb") as flujo:
        muestra = flujo.read(MUESTRA_BYTES + 1)
    if len(muestra) <= MUESTRA_BYTES:
        return len(muestra), True, "gzip_completo"

    razon = _razon_compresion_gzip(ruta, MUESTRA_BYTES)
    estimado = comprimido * razon
    with ruta.open("rb") as fh:
        fh.seek(-4, os.SEEK_END)
        isize = int.from_bytes(fh.read(4), "little")
    total, del_trailer = _desenrollar_isize(isize, estimado, comprimido)
    return total, del_trailer, "gzip_isize" if del_trailer else "gzip_razon"


def _estimar_gzip(spec: SourceSpec, ruta: Path, lineas_cabecera: int) -> EstimacionFilas:
    total, tamano_exacto, metodo = _tamano_descomprimido_gzip(ruta)
    if total <= MUESTRA_BYTES:
        with gzip.open(ruta, "rb") as flujo:
            contenido = flujo.read(MUESTRA_BYTES + 1)
        filas = _contar_lineas(contenido, lineas_cabecera=lineas_cabecera)
        return EstimacionFilas(
            fuente=spec.name,
            filas=filas,
            exacta=not spec.newlines_in_values,
            metodo="gzip_conteo_completo",
            bytes_datos=total,
            detalle=f"{filas:,} líneas contadas sobre el archivo completo.",
        )
    with gzip.open(ruta, "rb") as flujo:
        ventanas, completo = _sondear_secuencial(flujo, total, presupuesto=PRESUPUESTO_SONDEO_BYTES)
    filas = _filas_desde_ventanas(total, ventanas, lineas_cabecera=lineas_cabecera)
    if filas is None:
        return EstimacionFilas(
            fuente=spec.name,
            filas=None,
            exacta=False,
            metodo="gzip_sin_lineas",
            bytes_datos=total,
            detalle="Las ventanas no contienen saltos de línea; no se puede medir el ancho de fila.",
        )
    sufijo = "" if (completo and tamano_exacto) else " (sondeo parcial o tamaño estimado)"
    return EstimacionFilas(
        fuente=spec.name,
        filas=filas,
        exacta=False,
        metodo=metodo,
        bytes_datos=total,
        detalle=(
            f"~{filas:,} filas: {total / 1024**2:,.0f} MiB descomprimidos ({metodo}) "
            f"÷ ancho medio de {len(ventanas)} ventanas{sufijo}."
        ),
    )


def _estimar_texto_plano(spec: SourceSpec, ruta: Path, lineas_cabecera: int) -> EstimacionFilas:
    total = ruta.stat().st_size
    if total <= MUESTRA_BYTES:
        contenido = ruta.read_bytes()
        filas = _contar_lineas(contenido, lineas_cabecera=lineas_cabecera)
        return EstimacionFilas(
            fuente=spec.name,
            filas=filas,
            exacta=not spec.newlines_in_values,
            metodo="texto_conteo_completo",
            bytes_datos=total,
            detalle=f"{filas:,} líneas contadas sobre el archivo completo ({total:,} bytes).",
        )
    with ruta.open("rb") as fh:
        ventanas = _sondear_seekable(fh, total)
    filas = _filas_desde_ventanas(total, ventanas, lineas_cabecera=lineas_cabecera)
    if filas is None:
        return EstimacionFilas(
            fuente=spec.name,
            filas=None,
            exacta=False,
            metodo="texto_sin_lineas",
            bytes_datos=total,
            detalle="Las ventanas no contienen saltos de línea; no se puede medir el ancho de fila.",
        )
    return EstimacionFilas(
        fuente=spec.name,
        filas=filas,
        exacta=False,
        metodo="texto_ancho_estratificado",
        bytes_datos=total,
        detalle=(
            f"~{filas:,} filas: {total / 1024**2:,.0f} MiB ÷ ancho medio de "
            f"{len(ventanas)} ventanas repartidas por todo el archivo."
        ),
    )


def estimar_filas(spec: SourceSpec, *, lineas_cabecera: int | None = None) -> EstimacionFilas:
    """Estima cuántas filas trae la fuente leyendo kilobytes, no gigabytes.

    Nunca lanza por el contenido del archivo: un formato que no se puede
    dimensionar barato devuelve ``filas=None`` con el motivo en ``detalle``.

    Args:
        spec: contrato de la fuente. Se respetan ``format``, ``compression``
            y ``archive_member`` tal como los aplicaría el lector real.
        lineas_cabecera: filas de encabezado a descontar. Por defecto
            ``spec.header + 1`` (la fila de nombres de columna).

    Returns:
        EstimacionFilas con la cifra, si es exacta y por qué camino se obtuvo.
    """
    ruta = Path(spec.path).expanduser()
    cabecera = spec.header + 1 if lineas_cabecera is None else lineas_cabecera
    if not ruta.is_file():
        return EstimacionFilas(
            fuente=spec.name,
            filas=None,
            exacta=False,
            metodo="inexistente",
            detalle=f"No existe el archivo {ruta}.",
        )
    try:
        compresion = _detect_compression(ruta, Compression(spec.compression))
        if compresion is Compression.ZIP:
            return _estimar_zip(spec, ruta, cabecera)
        if compresion is Compression.GZIP:
            # El formato se infiere del nombre SIN el sufijo de compresión: leer
            # los bytes mágicos de un .gz solo diría "esto está comprimido".
            interno = InputFormat(spec.format)
            if interno is InputFormat.AUTO:
                sufijo = Path(ruta.stem).suffix.lower()
                interno = InputFormat.PARQUET if sufijo in {".parquet", ".pq"} else InputFormat.TXT
            if interno is not InputFormat.TXT and interno is not InputFormat.CSV:
                return EstimacionFilas(
                    fuente=spec.name,
                    filas=None,
                    exacta=False,
                    metodo="gzip_no_textual",
                    detalle=(
                        f"Un {interno.value} comprimido con gzip no se puede dimensionar "
                        "sin descomprimirlo entero."
                    ),
                )
            return _estimar_gzip(spec, ruta, cabecera)
        formato = _detect_input_format(ruta, ruta.name, InputFormat(spec.format))
        if formato is InputFormat.PARQUET:
            return _estimar_parquet(ruta, spec.name)
        if formato is InputFormat.XLSX:
            return EstimacionFilas(
                fuente=spec.name,
                filas=None,
                exacta=False,
                metodo="xlsx_no_dimensionable",
                bytes_datos=ruta.stat().st_size,
                detalle=(
                    "Un XLSX no declara su número de filas sin abrir la hoja; "
                    "para decidir el motor, conviértalo a CSV o Parquet."
                ),
            )
        return _estimar_texto_plano(spec, ruta, cabecera)
    except Exception as exc:  # dimensionar jamás debe tumbar la corrida
        return EstimacionFilas(
            fuente=spec.name,
            filas=None,
            exacta=False,
            metodo="error",
            detalle=f"No se pudo dimensionar ({type(exc).__name__}: {exc}).",
        )


def resumir_universo(
    specs: Sequence[SourceSpec],
    *,
    exactas: dict[str, int] | None = None,
) -> ResumenUniverso:
    """Dimensiona todas las fuentes y agrega el total.

    Args:
        specs: fuentes del cruce.
        exactas: conteos ya conocidos por nombre de fuente (por ejemplo, los
            que salen de una caché Parquet de una corrida previa). Tienen
            prioridad sobre cualquier estimación.

    Returns:
        ResumenUniverso con el detalle por fuente y el total agregado.
    """
    conocidas = dict(exactas or {})
    estimaciones: list[EstimacionFilas] = []
    for spec in specs:
        if spec.name in conocidas:
            filas = int(conocidas[spec.name])
            estimaciones.append(
                EstimacionFilas(
                    fuente=spec.name,
                    filas=filas,
                    exacta=True,
                    metodo="cache",
                    detalle=f"{filas:,} filas conocidas de una corrida previa.",
                )
            )
            continue
        estimaciones.append(estimar_filas(spec))
    return ResumenUniverso(por_fuente=tuple(estimaciones))
