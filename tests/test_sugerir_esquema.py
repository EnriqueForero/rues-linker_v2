"""Tests de ``sugerir_esquema`` (v0.13.0, N3): inferencia asistida de esquemas.

Contrato: propone tipos correctos por nombre+contenido+dtype, omite con
motivo lo ambiguo (User Control > Automation), y el esquema resultante corre
end-to-end en ``dedupe_esquema``.
"""

from __future__ import annotations

import pandas as pd
import pytest

import record_linkage as rl
from record_linkage import TipoCampo
from record_linkage.matching.inferencia import sugerir_esquema_detallado


def _df_completo() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ID": range(6),
            "TAX_ID": ["900111222", "900111223", "900111224", "900111225", "", "900111227"],
            "COMPANY_NAME": [
                "ACME SAS",
                "BETA LTDA",
                "GAMA SA",
                "DELTA SAS",
                "ACME S.A.S.",
                "ZETA LTDA",
            ],
            "PHONE": ["3001234567"] * 6,
            "EMAIL_CONTACTO": ["a@x.com", "b@y.com", "", "c@z.com", "a@x.com", ""],
            "DIRECCION": ["CRA 7 # 12-34"] * 6,
            "CIUDAD": ["BOGOTA", "CALI", "BOGOTA", "CALI", "BOGOTA", "CALI"],
            "LATITUD": [4.6, 3.4, 4.6, 3.4, 4.6, 3.4],
            "LONGITUD": [-74.1, -76.5, -74.1, -76.5, -74.1, -76.5],
            "FECHA_MATRICULA": ["2020-01-15"] * 6,
            "CIIU": ["6202", "1101", "6202", "2202", "6202", "1101"],
            "CANALES": ["WEB;TIENDA", "WEB", "TV;RADIO", "WEB", "WEB;TIENDA", "TV"],
            "EXPORTA": ["SI", "NO", "SI", "NO", "SI", "NO"],
            "EMPLEADOS": [10.5, 200.1, 30.2, 41.3, 10.9, 77.7],
            "NOTAS": [f"texto libre cualquiera {i}" for i in range(6)],
        }
    )


def test_infiere_los_14_casos():
    esquema, motivos = sugerir_esquema_detallado(_df_completo())
    tipos = {c.nombre: c.tipo for c in esquema.campos}
    assert tipos["TAX_ID"] is TipoCampo.IDENTIFICADOR
    assert tipos["COMPANY_NAME"] is TipoCampo.NOMBRE_EMPRESA
    assert tipos["PHONE"] is TipoCampo.TELEFONO
    assert tipos["EMAIL_CONTACTO"] is TipoCampo.EMAIL
    assert tipos["DIRECCION"] is TipoCampo.DIRECCION
    assert tipos["CIUDAD"] is TipoCampo.CIUDAD
    assert tipos["LATITUD"] is TipoCampo.GEO
    assert tipos["FECHA_MATRICULA"] is TipoCampo.FECHA
    assert tipos["CIIU"] is TipoCampo.JERARQUICO
    assert tipos["CANALES"] is TipoCampo.CONJUNTO
    assert tipos["EXPORTA"] is TipoCampo.BOOLEANO
    assert tipos["EMPLEADOS"] is TipoCampo.NUMERICO
    # GEO consumió LONGITUD como pareja: no aparece como campo aparte.
    assert "LONGITUD" not in tipos
    geo = next(c for c in esquema.campos if c.tipo is TipoCampo.GEO)
    assert geo.columna_lon == "LONGITUD"
    # Omisiones con motivo explícito.
    omitidas = motivos.loc[motivos["decision"] == "omitida", "columna"].tolist()
    assert "ID" in omitidas and "NOTAS" in omitidas


def test_id_de_fila_se_omite_pero_nit_no():
    df = pd.DataFrame(
        {"ID": range(5), "NIT": ["900111222", "900111223", "900111224", "900111225", "900111226"]}
    )
    esquema, motivos = sugerir_esquema_detallado(df)
    assert [c.nombre for c in esquema.campos] == ["NIT"]
    assert "identificador DE FILA" in motivos.set_index("columna").loc["ID", "motivo"]


def test_digitos_largos_sin_nombre_id_se_omiten():
    df = pd.DataFrame(
        {
            "CODIGO_RARO": ["900111222", "900111223", "900111224"],
            "RAZON_SOCIAL": ["A B", "C D", "E F"],
        }
    )
    esquema, motivos = sugerir_esquema_detallado(df)
    assert "CODIGO_RARO" not in {c.nombre for c in esquema.campos}
    assert "ambiguo" in motivos.set_index("columna").loc["CODIGO_RARO", "motivo"]


def test_decimales_y_enteros_cortos_son_numericos():
    df = pd.DataFrame(
        {
            "EMPLEADOS": [10.5, 200.1, 30.2],
            "EDAD": [34, 51, 29],
            "RAZON_SOCIAL": ["A B", "C D", "E F"],
        }
    )
    esquema, _ = sugerir_esquema_detallado(df)
    tipos = {c.nombre: c.tipo for c in esquema.campos}
    assert tipos["EMPLEADOS"] is TipoCampo.NUMERICO
    assert tipos["EDAD"] is TipoCampo.NUMERICO


def test_sin_evidencia_lanza_accionable():
    df = pd.DataFrame({"X": [f"texto libre {i}" for i in range(30)]})
    with pytest.raises(ValueError, match="EsquemaCampos"):
        sugerir_esquema_detallado(df)


def test_end_to_end_con_esquema_sugerido():
    df = _df_completo()
    esquema = rl.sugerir_esquema(df, verbose=False)
    res = rl.dedupe_esquema(df, esquema)
    assert res.metricas["n_registros"] == len(df)
    assert res.manifiesto["parametros"]["esquema"]["nombre"] == "sugerido"
