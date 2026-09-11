"""
pipeline.storage — record_linkage_pipeline

Componentes:
    - class HybridStorageManager  (origen: notebook celda [192])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from pathlib import Path, PureWindowsPath

from ._internal import _get_logger


class HybridStorageManager:
    """
    Procesa en disco local rápido, sincroniza a Drive para persistencia.

    Flujo por fase:
    1. Al inicio: pull_from_drive() → copia inputs de Drive → disco local NVMe
    2. Durante:   Todo procesamiento SQLite ocurre en disco local
    3. Al final:  push_to_drive() → copia resultado a Drive (checkpoint seguro)
    4. Reinicio:  Busca checkpoint en Drive, descarga a local, continúa

    Uso:
        storage = HybridStorageManager(Path("/content/drive/MyDrive/workspace"))
        local = storage.pull_from_drive("candidates.db")
        # ... procesar en local ...
        storage.push_to_drive("candidates.db")
    """

    _OWNERSHIP_MARKER = ".rues-linker-storage-owner"
    _OWNERSHIP_PREFIX = "rues-linker-hybrid-storage-v1:"

    def __init__(self, drive_workspace: Path, local_base: str = "/content/temp_work"):
        """
        Args:
            drive_workspace: Directorio en Google Drive (persistente)
            local_base: Directorio local para procesamiento rápido
        """
        raw_drive_dir = Path(drive_workspace).expanduser()
        raw_local_dir = Path(local_base).expanduser()
        if raw_drive_dir.is_symlink():
            raise ValueError("drive_workspace no puede ser un enlace simbólico")
        if raw_local_dir.is_symlink():
            raise ValueError("local_base no puede ser un enlace simbólico")

        # Canonicalizar una sola vez impide que un componente simbólico del
        # prefijo cambie la frontera administrada entre operaciones.
        self.drive_dir = raw_drive_dir.resolve(strict=False)
        self.local_dir = raw_local_dir.resolve(strict=False)
        self._validate_cleanup_root(self.local_dir)
        if self.drive_dir == self.local_dir or self.drive_dir.is_relative_to(self.local_dir):
            raise ValueError(
                "drive_workspace debe estar fuera de local_base; cleanup_local podría borrarlo"
            )
        local_existed = self.local_dir.exists()
        local_had_entries = local_existed and any(self.local_dir.iterdir())
        self.local_dir.mkdir(parents=True, exist_ok=True)
        if self.local_dir.is_symlink():
            raise ValueError("local_base no puede ser un enlace simbólico")
        self._logger = _get_logger("HybridStorage")
        self._ownership_token = self._claim_local_root(
            allow_new_marker=not local_existed or not local_had_entries
        )
        local_stat = self.local_dir.stat()
        self._local_root_identity = (local_stat.st_dev, local_stat.st_ino)
        self._ownership_marker_identity: tuple[int, int] | None = None
        if self._ownership_token is not None:
            marker_stat = (self.local_dir / self._OWNERSHIP_MARKER).lstat()
            self._ownership_marker_identity = (marker_stat.st_dev, marker_stat.st_ino)

    def _claim_local_root(self, *, allow_new_marker: bool) -> str | None:
        """Claim only a new/empty root; never adopt arbitrary pre-existing data."""

        marker = self.local_dir / self._OWNERSHIP_MARKER
        if marker.is_symlink():
            raise ValueError(f"El marcador de propiedad no puede ser un symlink: {marker}")
        if marker.exists():
            if not marker.is_file() or marker.stat().st_size > 256:
                return None
            token = marker.read_text(encoding="ascii").strip()
            if (
                token.startswith(self._OWNERSHIP_PREFIX)
                and len(token) == len(self._OWNERSHIP_PREFIX) + 64
            ):
                return token
            return None
        if not allow_new_marker:
            return None

        token = f"{self._OWNERSHIP_PREFIX}{secrets.token_hex(32)}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(marker, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="ascii", closefd=False) as stream:
                stream.write(f"{token}\n")
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(fd)
        self._fsync_directory(self.local_dir)
        return token

    @staticmethod
    def _validate_cleanup_root(root: Path) -> None:
        """Reject filesystem-wide or working-tree cleanup targets."""

        resolved = root.resolve(strict=False)
        anchor = Path(resolved.anchor)
        current_working_dir = Path.cwd().resolve()
        if resolved == anchor or len(resolved.parts) < 3:
            raise ValueError(f"local_base es demasiado amplio para borrado seguro: {root}")
        if current_working_dir == resolved or current_working_dir.is_relative_to(resolved):
            raise ValueError(
                f"local_base no puede contener el directorio de trabajo actual: {root}"
            )

    @staticmethod
    def _relative_path(filename: str | os.PathLike[str]) -> Path:
        """Validate a portable relative path while allowing safe subdirectories."""

        raw = os.fspath(filename)
        if not isinstance(raw, str):
            raise TypeError("filename debe ser una ruta de texto")
        if not raw or "\x00" in raw:
            raise ValueError("filename no puede estar vacío ni contener NUL")

        relative = Path(raw)
        windows_path = PureWindowsPath(raw)
        if (
            relative.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or windows_path.root
        ):
            raise ValueError(f"filename debe ser relativo al workspace administrado: {raw!r}")
        if relative == Path(".") or any(
            part == ".." for part in (*relative.parts, *windows_path.parts)
        ):
            raise ValueError(f"filename contiene traversal no permitido: {raw!r}")
        return relative

    @staticmethod
    def _reject_symlink_components(root: Path, relative: Path) -> None:
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"La ruta administrada contiene un enlace simbólico: {current}")

    def _managed_path(
        self,
        root: Path,
        filename: str | os.PathLike[str],
        *,
        create_parent: bool = False,
    ) -> Path:
        relative = self._relative_path(filename)
        self._reject_symlink_components(root, relative)
        target = root / relative
        if not target.resolve(strict=False).is_relative_to(root):
            raise ValueError(f"filename sale del workspace administrado: {filename!r}")
        if create_parent:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Revalidar después de mkdir limita cambios de ruta entre el
            # chequeo inicial y la operación de E/S.
            self._reject_symlink_components(root, relative)
        return target

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        """Best-effort durability barrier; unsupported platforms may ignore it."""

        try:
            fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    @classmethod
    def _atomic_copy(cls, src: Path, dst: Path) -> None:
        """Copy to a sibling temporary file and publish with atomic replace."""

        if src.is_symlink() or not src.is_file():
            raise ValueError(f"El origen debe ser un archivo regular, no un symlink: {src}")
        if dst.is_symlink():
            raise ValueError(f"El destino no puede ser un enlace simbólico: {dst}")

        fd, temp_name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".tmp", dir=str(dst.parent))
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            shutil.copy2(src, temp_path)
            # En Windows ``os.fsync`` delega en ``_commit`` y requiere un
            # descriptor escribible, igual que la publicación SQLite del
            # scorer. Un descriptor ``rb`` abortaba todo push/pull atómico.
            with temp_path.open("rb+") as handle:
                os.fsync(handle.fileno())
            if dst.is_symlink():
                raise ValueError(f"El destino no puede ser un enlace simbólico: {dst}")
            os.replace(temp_path, dst)
            cls._fsync_directory(dst.parent)
        finally:
            temp_path.unlink(missing_ok=True)

    def local_path(self, filename: str | os.PathLike[str]) -> Path:
        """Retorna ruta local para un archivo."""
        return self._managed_path(self.local_dir, filename)

    def pull_from_drive(self, filename: str | os.PathLike[str]) -> Path:
        """
        Copia archivo de Drive a disco local. Retorna ruta local.
        Si el archivo no existe en Drive, retorna la ruta local (vacía).
        """
        src = self._managed_path(self.drive_dir, filename)
        dst = self._managed_path(self.local_dir, filename, create_parent=True)

        if src.exists():
            self._atomic_copy(src, dst)
            size_mb = src.stat().st_size / (1024 * 1024)
            self._logger.info(f"   📥 Pull: {filename} ({size_mb:.1f} MB) Drive → Local")
        else:
            self._logger.debug(f"   ℹ️ {filename} no existe en Drive (se creará localmente)")

        return dst

    def push_to_drive(self, filename: str | os.PathLike[str], retries: int = 3) -> Path:
        """
        Copia archivo de disco local a Drive. Retorna ruta en Drive.
        Incluye retry con backoff exponencial para robustez.
        """
        if retries < 1:
            raise ValueError("retries debe ser al menos 1")
        src = self._managed_path(self.local_dir, filename)
        dst = self._managed_path(self.drive_dir, filename, create_parent=True)

        for attempt in range(retries):
            try:
                self._atomic_copy(src, dst)
                size_mb = src.stat().st_size / (1024 * 1024)
                self._logger.info(f"   📤 Push: {filename} ({size_mb:.1f} MB) Local → Drive")
                return dst
            except Exception as e:
                wait = 2**attempt
                self._logger.warning(
                    f"   ⚠️ Push fallido (intento {attempt + 1}/{retries}): {e}. Esperando {wait}s..."
                )
                if attempt + 1 < retries:
                    import time

                    time.sleep(wait)

        raise RuntimeError(f"No se pudo copiar {filename} a Drive después de {retries} intentos")

    def cleanup_local(self):
        """Limpia disco local al terminar todo el pipeline."""
        try:
            self._validate_cleanup_root(self.local_dir)
            if self.local_dir.is_symlink():
                raise ValueError("cleanup_local rechazó un local_base simbólico")
            if not self.local_dir.exists():
                return
            if self.local_dir.resolve(strict=True) != self.local_dir:
                raise ValueError("cleanup_local detectó un cambio de frontera administrada")
            marker = self.local_dir / self._OWNERSHIP_MARKER
            local_stat = self.local_dir.stat()
            marker_stat = marker.lstat() if marker.exists() else None
            if (
                self._ownership_token is None
                or (local_stat.st_dev, local_stat.st_ino) != self._local_root_identity
                or marker_stat is None
                or (marker_stat.st_dev, marker_stat.st_ino) != self._ownership_marker_identity
                or marker.is_symlink()
                or not marker.is_file()
                or marker_stat.st_size > 256
                or marker.read_text(encoding="ascii").strip() != self._ownership_token
            ):
                raise ValueError(
                    "cleanup_local rechazó un directorio sin marcador de propiedad válido"
                )
            shutil.rmtree(self.local_dir)
            self._logger.info(f"   🧹 Disco local limpiado: {self.local_dir}")
        except Exception as e:
            self._logger.warning(f"   ⚠️ Error limpiando disco local: {e}")

    def is_local_available(self) -> bool:
        """Verifica si el disco local está disponible (Colab con NVMe)."""
        try:
            return self.local_dir.exists() or self.local_dir.parent.exists()
        except OSError:
            return False
