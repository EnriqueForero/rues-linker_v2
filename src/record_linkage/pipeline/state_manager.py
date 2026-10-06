"""
pipeline.state_manager — record_linkage_pipeline

Componentes:
    - class StateManager  (origen: notebook celda [192])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from ..reporting.strategies import Phase
from ._phase_constants import PHASES_ORDER
from .fingerprints import fingerprint_config, fingerprint_file, package_code_fingerprint

MANIFEST_SCHEMA_VERSION = 2
HASH_ALGORITHM = "sha256"


class StateManager:
    """
    Gestiona el estado persistente del pipeline mediante manifest.json.

    Responsabilidades:
    - Calcular hashes de configuración por fase para detectar cambios
    - Validar si una fase puede reutilizarse (checkpointing)
    - Persistir estado tras cada fase completada
    - Invalidar fases cuando cambia la configuración

    El manifest guarda:
    - Hash de configuración de cada fase
    - Estado (DONE/PENDING)
    - Timestamp de completación
    - Lista de archivos generados
    - Metadatos adicionales (duración, etc.)
    """

    def __init__(self, work_dir: Path):
        """
        Inicializa el gestor de estado.

        Args:
            work_dir: Directorio de trabajo donde se guarda manifest.json
        """
        self.work_dir = work_dir
        self.manifest_file = work_dir / "manifest.json"
        self.manifest = self._load()

    @staticmethod
    def _manifest_meta() -> dict:
        """Metadatos que vuelven explícito el contrato del checkpoint."""

        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "hash_algorithm": HASH_ALGORITHM,
            "code_fingerprint": package_code_fingerprint(),
        }

    def _load(self) -> dict:
        """Carga manifest existente o retorna diccionario vacío."""
        if self.manifest_file.exists():
            try:
                with open(self.manifest_file, encoding="utf-8") as f:
                    manifest = json.load(f)
                current_meta = self._manifest_meta()
                stored_meta = manifest.get("_meta", {}) if isinstance(manifest, dict) else {}
                if stored_meta != current_meta:
                    # Checkpoints anteriores a v2, o producidos por otro código,
                    # carecen del contrato de integridad actual. Se reconstruyen
                    # una sola vez; reutilizarlos sería asumir compatibilidad.
                    return {"_meta": current_meta}
                return manifest
            except (OSError, json.JSONDecodeError):
                # Manifest corrupto, empezar de cero
                return {"_meta": self._manifest_meta()}
        return {"_meta": self._manifest_meta()}

    def save(self) -> None:
        """Persiste el manifest atómicamente en el mismo filesystem."""

        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.manifest["_meta"] = self._manifest_meta()
        fd, temp_name = tempfile.mkstemp(
            dir=self.work_dir,
            prefix=f".{self.manifest_file.name}.",
            suffix=".tmp",
        )
        temp_file = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.manifest, stream, indent=2, default=str, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_file, self.manifest_file)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temp_file.unlink()

    def compute_hash(self, config: dict, phase: Phase, data_sig: str, prev_hash: str) -> str:
        """
        Calcula hash único para una fase basado en:
        - Configuración efectiva completa
        - Hash de la fase anterior (encadenamiento)
        - Firma de los datos de entrada
        - Huella del código y dependencias relevantes

        Esto permite detectar cuándo una fase necesita re-ejecutarse.

        Args:
            config: Configuración completa del pipeline
            phase: Fase para la cual calcular el hash
            data_sig: Firma de los datos de entrada
            prev_hash: Hash de la fase anterior (para encadenamiento)

        Returns:
            Hash SHA-256 hexadecimal
        """
        payload = {
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "phase": phase.value,
            "config_fingerprint": fingerprint_config(config),
            "previous_phase_hash": prev_hash,
            "data_fingerprint": data_sig,
            "code_fingerprint": package_code_fingerprint(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def get_prev_hash(self, phase: Phase) -> str:
        """
        Obtiene hash de la fase anterior para encadenamiento.

        Args:
            phase: Fase actual

        Returns:
            Hash de la fase anterior, o cadena vacía si es L1_PREP
        """
        idx = PHASES_ORDER.index(phase)
        if idx == 0:
            return ""
        prev_phase = PHASES_ORDER[idx - 1]
        return self.manifest.get(prev_phase.value, {}).get("hash", "")

    def is_valid(self, phase: Phase, expected_hash: str) -> bool:
        """
        Verifica si una fase puede reutilizarse.

        Una fase es válida si:
        1. Existe en el manifest
        2. Su hash coincide con el esperado
        3. Su estado es DONE
        4. Todos los archivos generados existen en disco

        Args:
            phase: Fase a verificar
            expected_hash: Hash calculado con la configuración actual

        Returns:
            True si la fase puede reutilizarse
        """
        rec = self.manifest.get(phase.value)

        if not rec:
            return False

        if rec.get("hash") != expected_hash:
            return False

        if rec.get("status") != "DONE":
            return False

        artifacts = rec.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            return False

        # Tamaño + huella completa detectan truncamiento, reemplazo y corrupción,
        # incluso si Drive conserva o reordena mtimes. El hashing es streaming.
        for artifact in artifacts:
            try:
                path = Path(artifact["path"])
                if not path.is_file() or path.stat().st_size != artifact["size_bytes"]:
                    return False
                if fingerprint_file(path) != artifact["fingerprint"]:
                    return False
            except (KeyError, OSError, TypeError, ValueError):
                return False
        return True

    def mark_done(
        self, phase: Phase, ph_hash: str, files: list[Path], meta: dict | None = None
    ) -> None:
        """
        Marca una fase como completada exitosamente.

        Args:
            phase: Fase completada
            ph_hash: Hash de configuración de la fase
            files: Lista de archivos generados
            meta: Metadatos adicionales (ej: duración)
        """
        artifact_records = []
        resolved_files = []
        for file_path in files:
            path = Path(file_path).resolve()
            before = path.stat()
            if not path.is_file():
                raise ValueError(f"El artefacto de {phase.value} no es un archivo: {path}")
            fingerprint = fingerprint_file(path)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(
                    f"El artefacto cambió mientras se registraba el checkpoint: {path}"
                )
            resolved_files.append(str(path))
            artifact_records.append(
                {
                    "path": str(path),
                    "size_bytes": after.st_size,
                    "fingerprint": fingerprint,
                }
            )

        self.manifest[phase.value] = {
            "hash": ph_hash,
            "status": "DONE",
            "timestamp": datetime.now().isoformat(),
            # `files` se conserva para compatibilidad con _load_phase_result.
            "files": resolved_files,
            "artifacts": artifact_records,
            "meta": meta or {},
        }
        self.save()

    def anotar_meta(self, phase: Phase, meta: dict) -> None:
        """Añade metadatos a una fase SIN tocar hash, estado ni artefactos.

        Para los caminos que generan L6 fuera de ``_exec_phase``
        (``Orchestrator.export_reports``, reportes tras postprocesar): no son
        un checkpoint, pero el manifiesto debe decir igual qué artefactos se
        omitieron y por qué (F1.4). Si la fase no tiene entrada se crea una
        sin ``hash`` ni ``status`` DONE, que ``is_valid`` rechaza.
        """
        rec = self.manifest.setdefault(phase.value, {"status": "SIN_CHECKPOINT"})
        rec.setdefault("meta", {}).update(meta)
        rec["timestamp"] = datetime.now().isoformat()
        self.save()

    def invalidate_from(self, phase: Phase) -> None:
        """
        Invalida una fase y todas las posteriores.

        Usado cuando se quiere forzar re-ejecución desde un punto específico.

        Args:
            phase: Fase desde la cual invalidar (inclusive)
        """
        idx = PHASES_ORDER.index(phase)
        for p in PHASES_ORDER[idx:]:
            if p.value in self.manifest:
                del self.manifest[p.value]
        self.save()
