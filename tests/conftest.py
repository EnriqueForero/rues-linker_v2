"""Fixtures compartidos por las pruebas de ``flujo.cruce``.

Empresas inventadas; ningún dato real entra al repositorio.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from record_linkage import ColumnType, SourceSpec


@pytest.fixture
def fuentes_con_arrastre(tmp_path: Path) -> list[SourceSpec]:
    """Dos fuentes con columnas mapeadas que NO participan en la decisión."""
    a = tmp_path / "padron.csv"
    a.write_text(
        "IDENT,NOMBRE_EMPRESA,TEL,CORREO\n"
        "900111222,ACME COLOMBIA SAS,3001112233,acme@x.co\n"
        "800333444,BETA LTDA,3009998877,beta@x.co\n"
        "900555666,GAMA S.A.,3005554433,gama@x.co\n",
        encoding="utf-8",
    )
    b = tmp_path / "clientes.txt"
    b.write_text(
        "nit_cliente\trazon\tdepto\n"
        "9001112221\tACME COLOMBIA S.A.S.\tANTIOQUIA\n"
        "700999888\tDELTA EU\tBOGOTA\n",
        encoding="utf-8",
    )
    return [
        SourceSpec(
            name="PADRON",
            path=a,
            column_mapping={"NIT": "IDENT", "RAZON_SOCIAL": "NOMBRE_EMPRESA"},
            optional_column_mapping={"TELEFONO": "TEL", "EMAIL": "CORREO"},
            delimiter=",",
            column_types={"NIT": ColumnType.IDENTIFIER},
        ),
        SourceSpec(
            name="CLIENTES",
            path=b,
            column_mapping={"NIT": "nit_cliente", "RAZON_SOCIAL": "razon"},
            optional_column_mapping={"DEPARTAMENTO": "depto"},
            delimiter="\t",
            column_types={"NIT": ColumnType.IDENTIFIER},
        ),
    ]
