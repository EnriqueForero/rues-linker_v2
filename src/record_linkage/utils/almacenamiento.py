"""utils.almacenamiento — política anti-FUSE para archivos de trabajo (v0.13.0).

Práctica transferida de `dian-comercio` (T2 de la auditoría 2026-08-26):
escribir SQLite/HDF5 intensivamente sobre un montaje FUSE de Google Drive es
lento y frágil — el modo de falla clásico en Colab es "transport endpoint is
not connected" a mitad de una fase larga, y los workarounds históricos de
este paquete (``os.sync()`` + ``time.sleep(2)`` tras construir el índice)
solo maquillaban el síntoma.

Regla:
    - Los archivos DE TRABAJO (escritura intensiva: firmas HDF5, índice LSH,
      candidatos SQLite) viven SIEMPRE en disco local de la VM.
    - El directorio final del usuario (posiblemente Drive) recibe COPIAS al
      completar cada fase — durabilidad ante reinicios de la VM — y de él se
      RECUPERAN los artefactos reutilizables al arrancar (reanudación).
    - Si el directorio final ya es local, no hay redirección ni copias: costo
      cero fuera de Colab.

El directorio local es DETERMINISTA por directorio final (hash de la ruta):
dos corridas sobre el mismo workspace comparten cache local dentro de la
misma sesión de VM, y workspaces distintos jamás colisionan.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

#: Prefijos de montajes FUSE de Google Drive en Colab. Mismo criterio que
#: `dian_comercio.analisis_duckdb._PREFIJOS_DRIVE` (validado en producción).
_PREFIJOS_FUSE: tuple[str, ...] = ("/content/drive", "/content/gdrive", "/gdrive")


def es_ruta_fuse(ruta: Path | str) -> bool:
    """True si ``ruta`` cae bajo un montaje FUSE de Google Drive (Colab).

    Se evalúan la ruta literal y su resolución (un Drive desconectado puede
    hacer fallar ``resolve()``; en ese caso se usa solo la literal).
    """
    candidatos = {str(ruta)}
    try:
        candidatos.add(str(Path(ruta).resolve()))
    except OSError:  # pragma: no cover - Drive desconectado
        pass
    return any(c.startswith(_PREFIJOS_FUSE) for c in candidatos)


def dir_trabajo_seguro(final_dir: Path | str, *, etiqueta: str = "rues_linker") -> Path:
    """Directorio de TRABAJO apto para escritura intensiva.

    Args:
        final_dir: directorio de destino elegido por el usuario (puede estar
            en Drive). Se crea si no existe.
        etiqueta: prefijo del directorio local (aislamiento por paquete).

    Returns:
        ``final_dir`` tal cual si NO está en FUSE (costo cero). Si está en
        FUSE: un directorio LOCAL determinista de la VM (``/content`` en
        Colab; el temp del SO en cualquier otro entorno), derivado del hash
        de la ruta final.
    """
    final_dir = Path(final_dir)
    final_dir.mkdir(parents=True, exist_ok=True)
    if not es_ruta_fuse(final_dir):
        return final_dir
    base = Path("/content") if Path("/content").is_dir() else Path(tempfile.gettempdir())
    huella = hashlib.sha256(str(final_dir).encode("utf-8")).hexdigest()[:12]
    local = base / f"{etiqueta}_local" / huella
    local.mkdir(parents=True, exist_ok=True)
    return local


def copiar_si_existe(origen: Path, destino: Path, logger=None, direccion: str = "") -> bool:
    """Copia ``origen`` → ``destino`` si origen existe y no son el mismo archivo.

    Devuelve True si copió. Los errores de FUSE se reportan y NO se propagan:
    la copia hacia Drive es durabilidad extra, jamás debe tumbar la corrida
    (el archivo de trabajo local sigue siendo la fuente de verdad).
    """
    origen, destino = Path(origen), Path(destino)
    if not origen.exists() or origen == destino:
        return False
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origen, destino)
        if logger is not None:
            mb = origen.stat().st_size / (1024 * 1024)
            logger.info(f"   🔄 {direccion or 'copia'}: {origen.name} ({mb:.1f} MB)")
        return True
    except OSError as exc:  # pragma: no cover - fallo de FUSE
        if logger is not None:
            logger.warning(
                f"   ⚠️ No se pudo copiar {origen.name} ({direccion}): {exc}. "
                f"La corrida continúa con el archivo local."
            )
        return False
