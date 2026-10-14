"""Declara en la línea base lo que un arreglo del motor cambió en las tablas de los 4 escenarios.

Uso (después de arreglar y de comprobar que el cambio es el que se quería):
    python scripts/oraculo_vinculacion/declarar_cambio.py "<id>" "<qué cambió y por qué>"

Corre el paquete sobre los insumos inventados, compara la huella de cada (escenario, tabla) con la vigente
(``CAMBIOS_DECLARADOS`` o, si no hay, la del motor original) y, donde difiere, la reemplaza y agrega ``[<id>] <motivo>``
al motivo en ``tests/lineas_base_vinculacion_cambios.json``. Si una tabla vuelve a la huella del original, su entrada
se retira. Imprime qué tablas cambiaron: si no es lo que el arreglo debía tocar, NO se declara: se corrige el arreglo.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from _rutas import REPO, usar_apoyo_de_pruebas, usar_biblioteca

usar_biblioteca()
usar_apoyo_de_pruebas()

import apoyo_vinculacion as av
from lineas_base_vinculacion import CAMBIOS_DECLARADOS, HUELLAS_MOTOR_ORIGINAL

from record_linkage.flujo import vinculacion as vinc

RUTA_JSON = REPO / "tests" / "lineas_base_vinculacion_cambios.json"


def huellas_de(res: vinc.Resultado) -> dict[str, str]:
    huellas = {nombre: av.huella_tabla(tabla, nombre) for nombre, tabla in res.tablas.items()}
    huellas["resumen"] = av.huella_tabla(res.resumen)
    huellas["por_pais"] = av.huella_tabla(res.por_pais)
    return huellas


def main(identificador: str, motivo: str) -> int:
    crudo = {"|".join(k): {"huella": h, "motivo": m} for k, (h, m) in CAMBIOS_DECLARADOS.items()}
    with tempfile.TemporaryDirectory() as tmp:
        ins = av.construir_insumos(Path(tmp))
        for escenario in av.ESCENARIOS:
            nuevas = huellas_de(
                vinc.ejecutar(av.configurar(vinc, ins, escenario=escenario), verboso=False)
            )
            for tabla, huella in sorted(nuevas.items()):
                clave = f"{escenario}|{tabla}"
                original = HUELLAS_MOTOR_ORIGINAL[escenario][tabla]
                vigente = crudo[clave]["huella"] if clave in crudo else original
                if huella == vigente:
                    continue
                if huella == original:
                    crudo.pop(clave, None)
                    print(f"  {clave}: vuelve a la huella del original (se retira la declaración)")
                    continue
                previo = crudo.get(clave, {}).get("motivo", "")
                crudo[clave] = {
                    "huella": huella,
                    "motivo": f"{previo}; [{identificador}] {motivo}".lstrip("; "),
                }
                print(f"  {clave}: {vigente} → {huella}")
    RUTA_JSON.write_text(
        json.dumps(crudo, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    print(f"declaradas {len(crudo)} entradas en {RUTA_JSON.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
