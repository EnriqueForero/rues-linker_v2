"""Línea base del motor de vinculación ORIGINAL sobre los insumos inventados (F3.0).

Origen: el motor vinculador_empresas 1.0.0 que vivía en la celda %%writefile del notebook 10
(commit bab03c2; SHA-256 de su fuente b4484359b8e096b574327f13eb8b972b4f49cc5455d93a651bb7c440fcd13959), corrido sobre
tests/apoyo_vinculacion.py (semilla 42) en cada escenario de ESCENARIOS.

Cada valor es apoyo_vinculacion.huella_tabla de una tabla de salida: columnas, orden de filas y valores,
sin depender de la versión de pandas. Medidas el 2026-10-10 con pandas 2.3.3 (Python 3.10) y pandas 3.0.6
(Python 3.11): son IGUALES en los dos. Con la librería del tag v0.22.4 y con la de F2 el motor original
también da las mismas tablas.

Un cambio de comportamiento de la vinculación cambia estas constantes: se actualizan en el mismo commit que
el cambio, con una línea en el ADR que diga qué se declaró distinto y por qué. Nunca para que la prueba pase.
"""

from __future__ import annotations

#: escenario → tabla → huella
HUELLAS_MOTOR_ORIGINAL: dict[str, dict[str, str]] = {
    "automatico_puro": {
        "CALIDAD_DATOS": "e5f96c99d9916ff3",
        "COMPARACION_REVISIONES": "6b943346b0cba32f",
        "CONTACTOS": "e461fa764182bf51",
        "CONTROL_QA": "cc3dbe2393dec99c",
        "DECISIONES": "fe0cfff0a32f8af8",
        "DEDUPLICACION": "a7c89ef913301ee1",
        "DETALLE_EXPORTADORES": "31185175b3c82f2e",
        "DETALLE_FDI": "d30409180033165e",
        "DETALLE_ORBIS": "274ae4180e2d4824",
        "ENLACES": "da93fa69f7ee7bef",
        "EXPORTADORES": "fb8cda0a68dc0f2b",
        "PARAMETROS": "e568380935e7246e",
        "REVISION": "0ef177221da5e522",
        "VALIDACION": "e3b0c44298fc1c14",
        "VALIDACION_DETALLE": "e3b0c44298fc1c14",
        "por_pais": "63a1768eda95a906",
        "resumen": "276814a4521a5a92",
    },
    "base": {
        "CALIDAD_DATOS": "acad5d51e6197729",
        "COMPARACION_REVISIONES": "9af061aa3114d889",
        "CONTACTOS": "b297a2b9f44b8995",
        "CONTROL_QA": "0e4771160cfc52a5",
        "DECISIONES": "94548809f8f11ea2",
        "DEDUPLICACION": "b1f01c0366742334",
        "DETALLE_EXPORTADORES": "627e8328feafebb4",
        "DETALLE_FDI": "2c0ce2336f3e2539",
        "DETALLE_ORBIS": "27c11ba4d449e132",
        "ENLACES": "55694d1fc1966a14",
        "EXPORTADORES": "61642a55aa344227",
        "PARAMETROS": "f102f37f17a7cd69",
        "REVISION": "b631c4cd2e833ae0",
        "VALIDACION": "f39edadbd561da18",
        "VALIDACION_DETALLE": "c6520ed635e8ed35",
        "por_pais": "fadb627f7285980b",
        "resumen": "ac7e9b4ba2951e30",
    },
    "errata_doble": {
        "CALIDAD_DATOS": "7786614e10a94738",
        "COMPARACION_REVISIONES": "9af061aa3114d889",
        "CONTACTOS": "b208f6b901e970e6",
        "CONTROL_QA": "0e4771160cfc52a5",
        "DECISIONES": "94548809f8f11ea2",
        "DEDUPLICACION": "9f52e5f032688ff6",
        "DETALLE_EXPORTADORES": "b5d383ccd3e22de1",
        "DETALLE_FDI": "cb9d2a48f86d317c",
        "DETALLE_ORBIS": "05a339267eae7a6a",
        "ENLACES": "f7a923f77654ba99",
        "EXPORTADORES": "706639796682c814",
        "PARAMETROS": "d1db222c8ff61b3a",
        "REVISION": "711b10eb2d11f135",
        "VALIDACION": "15d294471b62d140",
        "VALIDACION_DETALLE": "e3465f1ea346e151",
        "por_pais": "06efb45a10099fa9",
        "resumen": "2c234750ca3dd293",
    },
    "relajado": {
        "CALIDAD_DATOS": "18fcc95562afbc7e",
        "COMPARACION_REVISIONES": "9af061aa3114d889",
        "CONTACTOS": "deb56c6b67c4803c",
        "CONTROL_QA": "0e4771160cfc52a5",
        "DECISIONES": "94548809f8f11ea2",
        "DEDUPLICACION": "34d643f270e0658e",
        "DETALLE_EXPORTADORES": "62a563877c6a3194",
        "DETALLE_FDI": "bcbe91ff259bc15b",
        "DETALLE_ORBIS": "55b0eae3fcbae782",
        "ENLACES": "241131b9ffb65fd2",
        "EXPORTADORES": "d6c4a4366a661046",
        "PARAMETROS": "7f72b2ccb9680c48",
        "REVISION": "e2739a6a63b1edf2",
        "VALIDACION": "fa2ec52201642210",
        "VALIDACION_DETALLE": "f53ac3afedd60667",
        "por_pais": "6ab033974d8a0d82",
        "resumen": "6507dc6aa65810e0",
    },
}
