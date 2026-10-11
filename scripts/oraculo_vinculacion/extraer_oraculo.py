"""Extrae el motor ORIGINAL (celda ``%%writefile`` del notebook 10) del historial de git.

El notebook 10 se reescribe en F3.5 para importar el motor de la librería; el original vive en el commit
``COMMIT_ORIGEN``. Se escribe en ``ORACULO_DIR/oracle/vinculador_empresas.py`` (fuera de git: trae marcas
reales de empresas, que el repositorio no puede contener) y se verifica su SHA-256.

Uso:  python scripts/oraculo_vinculacion/extraer_oraculo.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess

from _rutas import CARPETA_MOTOR_ORIGINAL, COMMIT_ORIGEN, REPO, SHA256_MOTOR

RUTA_NOTEBOOK = "notebooks/10_vincular_exportadores_orbis_fdi.ipynb"
CELDA_DEL_MOTOR = 4


def main() -> None:
    crudo = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{COMMIT_ORIGEN}:{RUTA_NOTEBOOK}"],
        capture_output=True,
        check=True,
    ).stdout
    celda = "".join(json.loads(crudo.decode("utf-8"))["cells"][CELDA_DEL_MOTOR]["source"])
    primera, _, motor = celda.partition("\n")
    if not primera.startswith("%%writefile vinculador_empresas.py"):
        raise SystemExit(
            f"La celda {CELDA_DEL_MOTOR} de {COMMIT_ORIGEN} no es el motor: {primera!r}"
        )
    huella = hashlib.sha256(motor.encode("utf-8")).hexdigest()
    if huella != SHA256_MOTOR:
        raise SystemExit(f"El motor extraído no es el esperado: {huella} (esperado {SHA256_MOTOR})")
    destino = CARPETA_MOTOR_ORIGINAL / "vinculador_empresas.py"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(motor.encode("utf-8"))
    print(f"motor original: {destino} ({len(motor)} caracteres, sha256 {huella[:16]}…)")


if __name__ == "__main__":
    main()
