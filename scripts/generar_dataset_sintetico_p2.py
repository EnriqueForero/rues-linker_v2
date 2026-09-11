"""Genera dataset sintético V2 (P2 Camino #1) con CIUDAD y TELEFONO.

Este dataset extiende el sintético v1 con variables adicionales que deberían
permitir distinguir los casos negativos diseñados (NIT adyacente con tokens
compartidos) que en v2.4 a v2.6 eran indistinguibles.

Estructura:
    - Pares verdaderos: NITs idénticos o vecinos, MISMA ciudad/teléfono.
    - Casos negativos diseñados: NITs adyacentes, DIFERENTE ciudad/teléfono.
    - Singletons: sin par.

El dataset es DETERMINISTA (semilla fija) y se puede regenerar.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def generate() -> pd.DataFrame:
    """Genera el DataFrame sintético V2 con variables adicionales."""
    grupos = [
        # ─── Grupo 1: EY / Ernst & Young — MISMA CIUDAD/TELEFONO ───
        {
            "ID_GROUP": 1,
            "variantes": [
                ("900111222", "EY COLOMBIA SAS", "BOGOTA", "6011234567"),
                ("900111222", "ERNST AND YOUNG EN LIQUIDACION", "BOGOTA", "6011234567"),
                ("900111222", "ERNST & YOUNG", "BOGOTA", "6011234567"),
                ("900111223", "EY", "BOGOTA", "6011234567"),  # NIT vecino, misma ciudad
            ],
        },
        # ─── Grupo 2: Accenture / Andersen Consulting ───
        {
            "ID_GROUP": 2,
            "variantes": [
                ("900222333", "ACCENTURE SL", "MEDELLIN", "6044567890"),
                ("900222333", "DISTRIBUIDORA ANDERSEN CONSULTING", "MEDELLIN", "6044567890"),
                ("900222333", "ACCENTURE COLOMBIA", "MEDELLIN", "6044567890"),
                ("900222334", "ANDERSEN CONSULTING", "MEDELLIN", ""),  # NIT vecino, tel vacío
            ],
        },
        # ─── Grupo 3: PwC ───
        {
            "ID_GROUP": 3,
            "variantes": [
                ("800333444", "pwc", "BOGOTA", "6017654321"),
                ("800333444", "PWC CORP.", "BOGOTA", "6017654321"),
                ("800333444", "PRICEWATERHOUSECOOPERS", "BOGOTA", "6017654321"),
                ("800333445", "PWC AUDITORES", "BOGOTA", "6017654321"),
            ],
        },
        # ─── Grupo 4: Galletas Noel ───
        {
            "ID_GROUP": 4,
            "variantes": [
                ("890444555", "NOEL", "MEDELLIN", "6041112233"),
                ("890444555", "GALLETAS NOEL", "MEDELLIN", "6041112233"),
                ("890444555", "PRODUCTOS NOEL S.A.", "MEDELLIN", ""),
            ],
        },
        # ─── Grupo 5: KPMG con typos ───
        {
            "ID_GROUP": 5,
            "variantes": [
                ("830555666", "KPMG CIA", "BOGOTA", "6019998877"),
                ("830555666", "KMPG AUDITORS", "BOGOTA", "6019998877"),  # typo
                ("830555667", "KPMG", "BOGOTA", "6019998877"),
            ],
        },
        # ─── Singletons ───
        {
            "ID_GROUP": 6,
            "variantes": [("901000001", "EMPRESA UNICA SAS", "CALI", "6022223344")],
        },
        {
            "ID_GROUP": 7,
            "variantes": [("901000002", "OTRA EMPRESA UNICA SAS", "CALI", "6023334455")],
        },
        # ─── CASOS NEGATIVOS — NIT adyacente, MISMA tendencia de nombre,
        # pero CIUDADES Y TELÉFONOS DISTINTOS — la nueva variable los separa.
        {
            "ID_GROUP": 8,
            "variantes": [
                ("800999001", "TRANSPORTES DEL SUR", "BOGOTA", "6014001234"),
                ("800999001", "TRANSPORTES SUR", "BOGOTA", "6014001234"),
            ],
        },
        {
            "ID_GROUP": 9,
            "variantes": [
                # NIT adyacente al grupo 8 — el sistema v2.6.0 los unió porque
                # no podía distinguir. Ahora CIUDAD y TELEFONO son distintos
                # → el scorer puede separarlos.
                ("800999002", "INDUSTRIAS DEL NORTE", "MEDELLIN", "6048009876"),
                ("800999002", "NORTE INDUSTRIAL", "MEDELLIN", "6048009876"),
            ],
        },
        # ─── Otro caso negativo crítico del exhaustivo: empresas
        # "ORGANIZACION X" con NITs cercanos pero distintas. ───
        {
            "ID_GROUP": 10,
            "variantes": [
                ("860002536", "ORGANIZACION CORONA LIMITADA", "BOGOTA", "6013331111"),
                ("860002536", "ORGANIZACION CORONA", "BOGOTA", "6013331111"),
            ],
        },
        {
            "ID_GROUP": 11,
            "variantes": [
                (
                    "860007336",
                    "ORGANIZACION CARVAJAL",
                    "CALI",
                    "6027772222",
                ),  # ciudad/tel distintos
                ("860007336", "CARVAJAL SAS", "CALI", "6027772222"),
            ],
        },
    ]

    rows = []
    for g in grupos:
        for nit, razon, ciudad, telefono in g["variantes"]:
            rows.append(
                {
                    "NIT": nit,
                    "RAZON_SOCIAL": razon,
                    "CIUDAD": ciudad,
                    "TELEFONO": telefono,
                    "ID_GROUP": g["ID_GROUP"],
                }
            )

    return pd.DataFrame(rows)


def main() -> None:
    """Genera el dataset y lo escribe a tests/data_sintetica/."""
    df = generate()
    out_dir = Path(__file__).resolve().parent.parent / "tests" / "data_sintetica"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "dataset_sintetico_p2_extra_features.csv"
    df.to_csv(out, index=False)
    print(f"Dataset generado: {len(df)} registros, {df['ID_GROUP'].nunique()} grupos")
    print(f"Guardado en: {out}")
    print(df.to_string())


if __name__ == "__main__":
    main()
