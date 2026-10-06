"""F1.8 — Las columnas de arrastre nunca se omiten en silencio en ``flujo.cruce``.

Hasta F1.8, si el parquet derramado de una fuente no cuadraba con la
correlativa, ``_adjuntar_columnas_extra`` escribía un WARNING, devolvía la
correlativa intacta y el manifiesto declaraba la unión PLANIFICADA
(``columnas_re_adjuntadas``) como si hubiera ocurrido. Aquí se exige:

* fallo ruidoso (``ColumnasArrastreError``, con qué pasó / por qué importa /
  qué hacer) cuando la alineación posicional no cuadra;
* manifiesto con la lista REAL: ``columnas_arrastre = {adjuntadas, omitidas}``,
  donde ``omitidas`` solo trae lo que el usuario pidió y ninguna fuente tiene,
  con el motivo.

Empresas inventadas; ningún dato real entra al repositorio.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from record_linkage import ColumnType, SourceSpec
from record_linkage.flujo import ConfigCruce, cruce as modulo, ejecutar_cruce
from record_linkage.pipeline.errores import ColumnasArrastreError


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


def _config(fuentes: list[SourceSpec], tmp_path: Path, **extra: object) -> ConfigCruce:
    base: dict[str, object] = {
        "fuentes": fuentes,
        "workspace": tmp_path / "salida",
        "confiables": {"PADRON"},
        "dir_trabajo": tmp_path / "trabajo",
        "filas_smoke": 0,
        "exportar_excel": False,
    }
    base.update(extra)
    return ConfigCruce(**base)  # type: ignore[arg-type]


def _manifiesto(resultado) -> dict:
    return json.loads(resultado.rutas["metadatos"].read_text(encoding="utf-8"))


class _LogNulo:
    """Logger inerte para ejercitar helpers sin ruido en la salida."""

    def info(self, *_a, **_k): ...

    def warning(self, *_a, **_k): ...


# ── Camino feliz: el manifiesto dice lo que REALMENTE se adjuntó ──────


def test_manifiesto_declara_las_columnas_realmente_adjuntadas(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path
) -> None:
    resultado = ejecutar_cruce(_config(fuentes_con_arrastre, tmp_path))
    corr = resultado.correlativa
    assert {"TELEFONO", "EMAIL", "DEPARTAMENTO"} <= set(corr.columns)

    parametros = _manifiesto(resultado)["parametros"]
    assert "columnas_re_adjuntadas" not in parametros, "declaraba la unión planificada, no la real"
    arrastre = parametros["columnas_arrastre"]
    assert set(arrastre["adjuntadas"]) == {"TELEFONO", "EMAIL", "DEPARTAMENTO"}
    assert arrastre["omitidas"] == []
    # La lista del manifiesto es la que de verdad está en la correlativa.
    assert set(arrastre["adjuntadas"]) <= set(corr.columns)


def test_columna_pedida_que_ninguna_fuente_tiene_queda_en_omitidas_con_motivo(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path
) -> None:
    """Pedir una columna opcional inexistente no tumba la corrida, pero se DICE."""
    padron, clientes = fuentes_con_arrastre
    padron_con_sucursal = SourceSpec(
        name=padron.name,
        path=padron.path,
        column_mapping=dict(padron.column_mapping),
        optional_column_mapping={**padron.optional_column_mapping, "SUCURSAL": "sucursal"},
        delimiter=padron.delimiter,
        column_types=dict(padron.column_types),
    )
    resultado = ejecutar_cruce(_config([padron_con_sucursal, clientes], tmp_path))
    assert "SUCURSAL" not in resultado.correlativa.columns

    arrastre = _manifiesto(resultado)["parametros"]["columnas_arrastre"]
    assert set(arrastre["adjuntadas"]) == {"TELEFONO", "EMAIL", "DEPARTAMENTO"}
    assert len(arrastre["omitidas"]) == 1
    omitida = arrastre["omitidas"][0]
    assert omitida["columna"] == "SUCURSAL"
    assert "PADRON" in omitida["motivo"], "el motivo debe decir en qué fuente faltó"


# ── Alineación que no cuadra: la corrida FALLA ────────────────────────


def test_parquet_de_arrastre_con_una_fila_de_menos_tumba_la_corrida(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Se simula el desalineamiento recortando el parquet derramado de PADRON."""
    separar_original = modulo._separar_columnas_extra

    def separar_y_recortar(marcos, config, log):
        rutas, union, longitudes = separar_original(marcos, config, log)
        ruta = rutas["PADRON"]
        pd.read_parquet(ruta).iloc[:-1].to_parquet(ruta, index=False)
        return rutas, union, longitudes

    monkeypatch.setattr(modulo, "_separar_columnas_extra", separar_y_recortar)

    with pytest.raises(ColumnasArrastreError) as info:
        ejecutar_cruce(_config(fuentes_con_arrastre, tmp_path))

    texto = str(info.value)
    assert "Qué pasó" in texto and "Por qué importa" in texto and "Qué hacer" in texto
    assert "PADRON" in texto
    assert "2" in texto and "3" in texto, "debe citar filas observadas y esperadas"
    assert info.value.fuente == "PADRON"
    assert (info.value.observadas, info.value.esperadas) == (2, 3)
    # Y no hay manifiesto que diga que la unión ocurrió.
    assert not (tmp_path / "salida" / "metadatos_corrida.json").exists()


def test_total_de_extras_distinto_de_la_correlativa_falla(tmp_path: Path) -> None:
    """Cada parte cuadra con su fuente, pero la suma no cuadra con la correlativa."""
    ruta = tmp_path / "A.parquet"
    pd.DataFrame({"TELEFONO": ["1", "2", "3"]}).to_parquet(ruta, index=False)
    correlativa = pd.DataFrame({"ORIGINAL_INDEX": [0, 1], "ID_GRUPO": [1, 2]})
    with pytest.raises(ColumnasArrastreError) as info:
        modulo._adjuntar_columnas_extra(
            correlativa, {"A": ruta}, ["TELEFONO"], {"A": 3}, ["A"], _LogNulo()
        )
    assert info.value.fuente is None
    assert (info.value.observadas, info.value.esperadas) == (3, 2)
    assert "Qué hacer" in str(info.value)


def test_sin_original_index_no_se_puede_alinear_y_falla(tmp_path: Path) -> None:
    ruta = tmp_path / "A.parquet"
    pd.DataFrame({"TELEFONO": ["1"]}).to_parquet(ruta, index=False)
    correlativa = pd.DataFrame({"ID_GRUPO": [1]})
    with pytest.raises(ColumnasArrastreError, match="ORIGINAL_INDEX"):
        modulo._adjuntar_columnas_extra(
            correlativa, {"A": ruta}, ["TELEFONO"], {"A": 1}, ["A"], _LogNulo()
        )


# ── La función devuelve (correlativa, reporte) con la lista real ──────


def test_adjuntar_devuelve_reporte_con_adjuntadas_reales(tmp_path: Path) -> None:
    ruta = tmp_path / "A.parquet"
    pd.DataFrame({"TELEFONO": ["1", "2"], "EMAIL": ["a", "b"]}).to_parquet(ruta, index=False)
    correlativa = pd.DataFrame({"ORIGINAL_INDEX": [1, 0], "ID_GRUPO": [1, 2]})
    salida, reporte = modulo._adjuntar_columnas_extra(
        correlativa, {"A": ruta}, ["TELEFONO", "EMAIL"], {"A": 2}, ["A"], _LogNulo()
    )
    assert reporte.adjuntadas == ["TELEFONO", "EMAIL"]
    assert reporte.omitidas == []
    assert list(salida["TELEFONO"]) == ["2", "1"], "take posicional por ORIGINAL_INDEX"
    assert reporte.como_manifiesto() == {
        "adjuntadas": ["TELEFONO", "EMAIL"],
        "omitidas": [],
    }


def test_choque_con_columna_del_motor_queda_declarado_no_callado(tmp_path: Path) -> None:
    """Si el motor ya produjo una columna con ese nombre, la de la fuente no se
    pisa — pero la omisión queda en el reporte con su motivo, no en un WARNING
    que nadie lee."""
    ruta = tmp_path / "A.parquet"
    pd.DataFrame({"TELEFONO": ["1"], "CONFIANZA": ["x"]}).to_parquet(ruta, index=False)
    correlativa = pd.DataFrame({"ORIGINAL_INDEX": [0], "ID_GRUPO": [1], "CONFIANZA": [0.9]})
    salida, reporte = modulo._adjuntar_columnas_extra(
        correlativa, {"A": ruta}, ["TELEFONO", "CONFIANZA"], {"A": 1}, ["A"], _LogNulo()
    )
    assert reporte.adjuntadas == ["TELEFONO"]
    assert [o["columna"] for o in reporte.omitidas] == ["CONFIANZA"]
    assert "motor" in reporte.omitidas[0]["motivo"]
    assert salida.loc[0, "CONFIANZA"] == 0.9, "la columna del motor no se pisa"


def test_columnas_solicitadas_que_no_llegaron_quedan_en_omitidas(tmp_path: Path) -> None:
    ruta = tmp_path / "A.parquet"
    pd.DataFrame({"TELEFONO": ["1"]}).to_parquet(ruta, index=False)
    correlativa = pd.DataFrame({"ORIGINAL_INDEX": [0], "ID_GRUPO": [1]})
    _salida, reporte = modulo._adjuntar_columnas_extra(
        correlativa,
        {"A": ruta},
        ["TELEFONO"],
        {"A": 1},
        ["A"],
        _LogNulo(),
        solicitadas={"SUCURSAL": ["A"]},
    )
    assert reporte.adjuntadas == ["TELEFONO"]
    assert reporte.omitidas == [
        {
            "columna": "SUCURSAL",
            "motivo": "ninguna fuente la trae (pedida en optional_column_mapping de: A)",
        }
    ]


def test_sin_parquets_derramados_el_reporte_sigue_siendo_real(tmp_path: Path) -> None:
    correlativa = pd.DataFrame({"ORIGINAL_INDEX": [0], "ID_GRUPO": [1]})
    salida, reporte = modulo._adjuntar_columnas_extra(
        correlativa, {}, [], {"A": 1}, ["A"], _LogNulo(), solicitadas={"SUCURSAL": ["A"]}
    )
    assert salida is correlativa
    assert reporte.adjuntadas == []
    assert [o["columna"] for o in reporte.omitidas] == ["SUCURSAL"]
