"""Arnés híbrido: el motor ORIGINAL con funciones o clases sustituidas por las del paquete nuevo.

Uso:  python scripts/oraculo_vinculacion/hibrido.py <etiqueta> <modulo:nombre[=nombre_original]> ... [-- escenario ...]

Ejemplo (valida solo ``Normalizador`` y ``clasificar`` del paquete nuevo contra la referencia):
  python scripts/oraculo_vinculacion/hibrido.py py311_pandas3 \\
      record_linkage.flujo.vinculacion.normalizar:Normalizador \\
      record_linkage.flujo.vinculacion.clasificar:clasificar -- base relajado

Cada sustitución reemplaza el nombre GLOBAL del módulo original; el resto del motor original llama a la pieza
nueva por duck typing. Compara las 15 tablas, el resumen, el desglose por país, los avisos y el Excel (valores y
estilo) contra la referencia de ``correr_original.py``. Sale con 1 si algo difiere.
"""

from __future__ import annotations

import importlib
import pickle
import sys
import tempfile
from pathlib import Path

from _rutas import ORACULO_DIR, usar_apoyo_de_pruebas, usar_biblioteca, usar_motor_original

usar_biblioteca()
usar_motor_original()
usar_apoyo_de_pruebas()

import apoyo_vinculacion as av
import comparar
import vinculador_empresas as ve


def sustituir(especificaciones: list[str]) -> None:
    """Aplica ``modulo:nuevo[=original]`` sobre el módulo del motor original."""
    for tok in especificaciones:
        mod, _, nombre = tok.partition(":")
        nuevo, _, original = nombre.partition("=")
        original = original or nuevo
        if not hasattr(ve, original):
            raise SystemExit(f"el motor original no tiene {original!r}")
        setattr(ve, original, getattr(importlib.import_module(mod), nuevo))
        print(f"  sustituido: {original} <- {mod.rsplit('.', 1)[-1]}.{nuevo}")


def diferencias(etiqueta: str, ins: av.InsumosVinculacion, escenario: str) -> list[str]:
    base = ORACULO_DIR / f"ref_{etiqueta}" / escenario
    with (base / "tablas.pkl").open("rb") as fh:
        ref = pickle.load(fh)
    res = ve.ejecutar(av.configurar(ve, ins, escenario=escenario), verboso=False)
    dif = comparar.comparar_resultado(ref, res.tablas, res.resumen, res.por_pais, res.avisos)
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "salida.xlsx"
        ve.exportar_excel(res, ruta)
        dif += ["EXCEL: " + d for d in comparar.comparar_excel(base / "salida.xlsx", ruta)]
    return dif


def main(argumentos: list[str]) -> int:
    etiqueta, resto = argumentos[0], argumentos[1:]
    escenarios = list(av.ESCENARIOS)
    if "--" in resto:
        i = resto.index("--")
        escenarios, resto = resto[i + 1 :], resto[:i]
    sustituir(resto)
    carpeta = ORACULO_DIR / f"insumos_inventados_{etiqueta}"
    ins = av.InsumosVinculacion(
        carpeta, carpeta / "ancla.xlsx", carpeta / "descargas.xlsx", carpeta / "decisiones_1.csv",
        carpeta / "decisiones_2.csv",
    )  # fmt: skip
    if not ins.archivo_ancla.exists():
        raise SystemExit("falta correr correr_original.py para esta etiqueta")
    hubo = False
    for escenario in escenarios:
        dif = diferencias(etiqueta, ins, escenario)
        print(f"HÍBRIDO [{escenario}]:", "IDÉNTICO" if not dif else f"{len(dif)} DIFERENCIAS")
        for d in dif[:15]:
            print("   -", d)
        hubo = hubo or bool(dif)
    return 1 if hubo else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
