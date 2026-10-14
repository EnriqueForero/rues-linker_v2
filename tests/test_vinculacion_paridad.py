"""Paridad del motor de vinculación extraído: mismas tablas que el motor original (F3.1).

Las huellas de ``tests/lineas_base_vinculacion.py`` salen del motor ORIGINAL (la celda ``%%writefile`` del
notebook 10, commit bab03c2) corrido sobre los insumos inventados de ``tests/apoyo_vinculacion.py``. Esta
prueba corre el paquete nuevo sobre los mismos insumos y exige, por escenario, las mismas huellas de las 15
tablas del Excel más ``resumen`` y ``por_pais``, y los mismos avisos.

Si alguna falla: el paquete cambió de comportamiento. No se actualiza la constante para que pase; se declara el
cambio (ADR + una línea en la línea base) o se corrige el paquete.
"""

from __future__ import annotations

import apoyo_vinculacion as av
import pytest
from lineas_base_vinculacion import (
    AVISOS_MOTOR_ORIGINAL,
    CAMBIOS_DECLARADOS,
    CONTROLES_NO_OK_VIGENTES,
    HUELLAS_MOTOR_ORIGINAL,
)

from record_linkage.flujo import vinculacion as vinc

TABLAS = sorted(HUELLAS_MOTOR_ORIGINAL["base"])


@pytest.fixture(scope="module")
def insumos(tmp_path_factory):
    return av.construir_insumos(tmp_path_factory.mktemp("insumos_vinculacion"))


@pytest.fixture(scope="module", params=sorted(av.ESCENARIOS))
def corrida(request, insumos):
    cfg = av.configurar(vinc, insumos, escenario=request.param)
    return request.param, vinc.ejecutar(cfg, verboso=False)


def _huellas(res) -> dict[str, str]:
    huellas = {nombre: av.huella_tabla(tabla, nombre) for nombre, tabla in res.tablas.items()}
    huellas["resumen"] = av.huella_tabla(res.resumen)
    huellas["por_pais"] = av.huella_tabla(res.por_pais)
    return huellas


def test_las_lineas_base_cubren_todos_los_escenarios():
    assert set(HUELLAS_MOTOR_ORIGINAL) == set(av.ESCENARIOS) == set(AVISOS_MOTOR_ORIGINAL)
    assert all(sorted(h) == TABLAS for h in HUELLAS_MOTOR_ORIGINAL.values())


def test_la_corrida_entrega_las_mismas_tablas_que_el_original(corrida):
    escenario, res = corrida
    assert sorted(_huellas(res)) == TABLAS, escenario


@pytest.mark.parametrize("tabla", TABLAS)
def test_cada_tabla_tiene_la_huella_del_original(corrida, tabla):
    escenario, res = corrida
    obtenida = _huellas(res)[tabla]
    declarado = CAMBIOS_DECLARADOS.get((escenario, tabla))
    esperada = declarado[0] if declarado else HUELLAS_MOTOR_ORIGINAL[escenario][tabla]
    assert obtenida == esperada, (
        f"{escenario}/{tabla}: la tabla del paquete difiere de la del motor original"
        + (" (y de su cambio declarado)" if declarado else "")
    )


def test_los_cambios_declarados_son_reales_y_dicen_su_motivo():
    for (escenario, tabla), (huella, motivo) in CAMBIOS_DECLARADOS.items():
        assert huella != HUELLAS_MOTOR_ORIGINAL[escenario][tabla], (escenario, tabla)
        assert len(motivo) > 40, (escenario, tabla)


def test_los_avisos_son_los_del_original(corrida):
    escenario, res = corrida
    assert tuple(str(a) for a in res.avisos) == AVISOS_MOTOR_ORIGINAL[escenario]


def test_los_controles_que_no_salen_en_ok_son_los_vigentes(corrida):
    # El original dejaba una FALLA conocida (EMPRESA/A por identificador comparable con palabras sin alinear, hallazgo
    # C1/M-a): F3.1b la corrigió y está declarada en CAMBIOS_DECLARADOS. Queda solo la advertencia de recall.
    escenario, res = corrida
    no_ok = res.invariantes[res.invariantes["ESTADO"] != "OK"]
    assert (
        dict(zip(no_ok["CONTROL"], no_ok["ESTADO"], strict=True))
        == CONTROLES_NO_OK_VIGENTES[escenario]
    )
    assert res.todo_ok is True


def test_dos_corridas_seguidas_dan_las_mismas_tablas(insumos):
    cfg = av.configurar(vinc, insumos, escenario="base")
    primera, segunda = vinc.ejecutar(cfg, verboso=False), vinc.ejecutar(cfg, verboso=False)
    assert _huellas(primera) == _huellas(segunda)


def test_la_corrida_silenciosa_no_escribe_nada_a_la_salida(insumos, capsys):
    cfg = av.configurar(vinc, insumos, escenario="automatico_puro")
    vinc.ejecutar(cfg, verboso=False)
    salida = capsys.readouterr()
    assert salida.out == "" and salida.err == ""


def test_el_registro_recibe_los_mensajes_de_avance(insumos):
    mensajes: list[str] = []
    cfg = av.configurar(vinc, insumos, escenario="automatico_puro")
    vinc.ejecutar(cfg, verboso=True, registro=mensajes.append)
    assert mensajes and any("filas" in m for m in mensajes)
