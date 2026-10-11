"""Corre el motor ORIGINAL sobre los insumos inventados, un escenario por vez, y guarda la referencia.

La referencia de cada escenario (``ref_<etiqueta>/<escenario>/``) trae las tablas (``tablas.pkl``) y el Excel
(``salida.xlsx``) que el motor original produce; ``hibrido.py`` y ``comparar.py`` las usan como verdad.

Uso:  python scripts/oraculo_vinculacion/correr_original.py <etiqueta>   (p. ej. py311_pandas3)
La etiqueta nombra el entorno (versión de Python y de pandas): se corre una vez por entorno.
"""

from __future__ import annotations

import pickle
import sys

from _rutas import (
    ORACULO_DIR,
    usar_apoyo_de_pruebas,
    usar_biblioteca,
    usar_motor_original,
)

usar_biblioteca()
usar_motor_original()
usar_apoyo_de_pruebas()

import apoyo_vinculacion as av
import pandas as pd
import vinculador_empresas as ve


def main(etiqueta: str) -> None:
    carpeta = ORACULO_DIR / f"insumos_inventados_{etiqueta}"
    ins = av.construir_insumos(carpeta)
    for escenario in av.ESCENARIOS:
        res = ve.ejecutar(av.configurar(ve, ins, escenario=escenario), verboso=False)
        ref = ORACULO_DIR / f"ref_{etiqueta}" / escenario
        ref.mkdir(parents=True, exist_ok=True)
        contenido = {
            "tablas": res.tablas,
            "resumen": res.resumen,
            "por_pais": res.por_pais,
            "avisos": res.avisos,
            "casos": ins.casos,
        }
        with (ref / "tablas.pkl").open("wb") as fh:
            pickle.dump(contenido, fh)
        ve.exportar_excel(res, ref / "salida.xlsx")
        print(
            f"{escenario:16s} pandas={pd.__version__} todo_ok={res.todo_ok} avisos={len(res.avisos)} "
            f"enlaces={len(res.tablas['ENLACES'])} revision={len(res.tablas['REVISION'])}"
        )


if __name__ == "__main__":
    main(sys.argv[1])
