"""evaluation.multicriterio — medir qué hacen las variables extra del motor sobre una base con verdad.

Una prueba multicriterio es una corrida del motor que, además del NIT y la razón social, usa otras
columnas (TELEFONO, EMAIL, DIRECCION, MUNICIPIO, DEPARTAMENTO, CIIU, SITIO_WEB…) como evidencia:
``linkage(..., extra_features=[{"column", "weight", "type"}, ...])``. El puntaje del motor es
lineal (0,5·nombre + 0,5·nit + Σ peso·similitud, umbral 0,45) y los comparadores firmados devuelven
−1..+1: premian la coincidencia, castigan la discrepancia y valen 0 si falta alguno de los dos
lados. En los pares sin identificador el puntaje de NIT es cero, así que las variables extra son las
que pueden subirlos o bajarlos del umbral.

La respuesta tiene que salir de cifras, no de opinión. Este módulo corre el motor con una o varias
configuraciones de variables extra sobre las MISMAS fuentes y mide cada una:

- contra ``VERDAD_GRUPOS``: grupos predichos, precisión/recall/F1 por pares, FP y FN (con sus ids) y
  la tabla de casos acertados (``evaluation.plantilla_base_real``; una sola implementación);
- contra ``ETIQUETAS_PARES`` (``evaluation.verdad_real.medir_verdad_real`` con
  ``col_id=ID_REGISTRO_FUENTE``): precisión y recall con Wilson 95 % por régimen;
- contra la configuración base (sin extras): qué casos cambian de acertó/falló y qué pares
  concretos (ids) gana o pierde, cada uno marcado como verdadero o falso, y una tabla con los
  valores de las dos filas de cada par (:func:`tabla_pares_que_cambian`) para auditar a ojo.

Sin ``VERDAD_GRUPOS`` (una base real recién estructurada) se puede correr igual: no hay cifras de
calidad, pero sí la lista de pares que cambian de decisión al activar las extras, que es lo que una
persona revisa. Con una ``VERDAD_GRUPOS`` mínima (solo ``ID_REGISTRO_FUENTE`` e ``ID_GRUPO``) hay
precisión y recall por pares; la tabla por caso y el aporte por variable piden además la columna
``CASO`` y la hoja ``CASOS``. :func:`comparar_off_on` hace todo eso en una llamada (es lo que usa el
notebook 13) y :func:`describir_libro` dice, antes de correr, qué cifras va a haber y cuáles no.

Lo que hay que saber del motor para no medir mal (hallazgos de 2026-10-14, escritos y no escondidos):

- Con ``col_ciudad="MUNICIPIO"`` el motor renombra esa columna a ``CIUDAD`` ANTES de validar las
  variables extra: ``{"column": "MUNICIPIO"}`` lanza ``ValueError`` («la columna 'MUNICIPIO' no
  existe»). Las configuraciones se escriben con el nombre de la plantilla y
  :func:`a_columnas_del_motor` las traduce; la traducción queda en el JSON.
- La validación de columnas pasa, pero el scorer solo AVISA por log si una columna no llega a su
  DataFrame, y la medición apaga ese log. Por eso cada corrida comprueba directamente que las
  columnas extra están en ``L1_prep/data.parquet`` (lo que lee el scorer), lo escribe en el JSON
  (``columnas_extra_en_scorer``) y SE DETIENE si alguna no llegó: una comparación OFF/ON con una
  columna que el motor omitió no mediría nada.
- Las extras se suman DESPUÉS del filtro de nombre (``min_name_similarity``) y del filtro de NIT
  distinto: no rescatan un par que esos filtros descartan, y la cobertura por estrellas del régimen
  sin NIT tampoco las ve.
- El comparador no firmado ``categorical`` devuelve 0,5 si falta un lado: premia el dato ausente.
  Para variables con huecos, solo tipos firmados.
- La docstring de ``matching/puente_campos.py`` muestra ``extra_features=[("FECHA", "tipo_fecha",
  0.10)]``; una tupla la rechaza ``config/profiles.py::_normalizar_extra_features``. Use dicts.

Regla de decisión (la del proyecto: «unir mal duele más que dejar sin unir»): se recomienda la
configuración de mayor precisión por pares contra VERDAD_GRUPOS; a igual precisión, mayor recall;
a igual recall, menos variables (la más simple). Lo que esto NO prueba sobre la plantilla de
``data/ejemplos``: es sintética (correos idénticos en todos los positivos, ningún negativo comparte
teléfono o correo, SITIO_WEB casi vacío, CIIU escaso); la certificación sale de la base real y sus
etiquetas. Lectura completa: ``docs/PLANTILLA_BASE_REAL.md`` §5.1 y §8.

Hasta 2026-10-14 vivía en ``scripts/multicriterio_plantilla.py``; el script es hoy un envoltorio
delgado (argumentos, impresión y determinismo en subprocesos) sobre este paquete.

Nació el 2026-10-14 como un solo archivo de 1.180 líneas y el mismo día se dividió en este paquete
por la compuerta de ingeniería del plan (archivo ≤ 600 líneas; ``docs/PLAN_DETALLADO_2026-10-14.md``
§7), sin cambiar lógica ni cifras. Se importa desde aquí
(``from record_linkage.evaluation.multicriterio import comparar_off_on``) y todo nombre público está
en ``__all__``. Qué hay en cada módulo, en el orden en que se apoyan (ninguno importa al que lo
sigue):

- ``configuraciones``: :class:`Configuracion`, las doce ``CONFIGURACIONES`` por defecto,
  ``VARIABLES_A_MEDIR``, la traducción de columnas al nombre que ve el motor
  (:func:`a_columnas_del_motor`, :func:`ajustes_del_motor`), la validación fail-fast
  (:func:`validar_configuraciones`), :func:`leer_configuraciones` y :func:`descripcion_tipos`;
  también registra los comparadores ``tipo_*`` del puente de campos.
- ``datos``: dónde está la plantilla (:func:`localizar_plantilla`, con descarga desde ``main``),
  :func:`leer_etiquetas_de`, :func:`marco_registros`, :func:`caso_por_id` y :func:`pares_de_prueba`.
- ``medicion``: el aporte de cada variable (:func:`aporte_por_variable`, :func:`aporte_como_tabla`,
  :func:`tabla_aporte`), :class:`Medicion`, :func:`huella_particion`, la comprobación de que las
  columnas extra llegaron al scorer, :func:`medir_configuracion`, :func:`comparar_con_base`,
  :func:`medicion_como_dict` y :func:`elegir_recomendada`.
- ``informe``: las tablas (:func:`resumen`, :func:`tabla_resumen`, :func:`describir_libro`,
  :func:`tabla_pares_que_cambian`), :class:`ComparacionOffOn`, :func:`comparar_off_on` y
  :func:`exportar_comparacion`.
"""

from __future__ import annotations

from record_linkage.evaluation.multicriterio.configuraciones import (
    COLUMNAS_CANONICAS,
    CONFIGURACION_BASE,
    CONFIGURACIONES,
    CONTACTO,
    GEO,
    NOMBRE_BASE,
    VARIABLES_A_MEDIR,
    Configuracion,
    a_columnas_del_motor,
    ajustes_del_motor,
    descripcion_tipos,
    es_base,
    leer_configuraciones,
    validar_configuraciones,
    variable,
)
from record_linkage.evaluation.multicriterio.datos import (
    ARCHIVOS_PLANTILLA,
    URL_RAW_PLANTILLA,
    caso_por_id,
    leer_etiquetas_de,
    localizar_plantilla,
    marco_registros,
    pares_de_prueba,
)
from record_linkage.evaluation.multicriterio.informe import (
    COLUMNAS_AUDITORIA,
    LEYENDA_RESUMEN,
    ComparacionOffOn,
    comparar_off_on,
    describir_libro,
    exportar_comparacion,
    resumen,
    tabla_pares_que_cambian,
    tabla_resumen,
)
from record_linkage.evaluation.multicriterio.medicion import (
    Medicion,
    aporte_como_tabla,
    aporte_por_variable,
    columnas_que_ve_el_scorer,
    comparar_con_base,
    elegir_recomendada,
    exigir_columnas_en_scorer,
    huella_particion,
    medicion_como_dict,
    medir_configuracion,
    tabla_aporte,
)

__all__ = [
    "ARCHIVOS_PLANTILLA",
    "COLUMNAS_AUDITORIA",
    "COLUMNAS_CANONICAS",
    "CONFIGURACIONES",
    "CONFIGURACION_BASE",
    "CONTACTO",
    "GEO",
    "LEYENDA_RESUMEN",
    "NOMBRE_BASE",
    "URL_RAW_PLANTILLA",
    "VARIABLES_A_MEDIR",
    "ComparacionOffOn",
    "Configuracion",
    "Medicion",
    "a_columnas_del_motor",
    "ajustes_del_motor",
    "aporte_como_tabla",
    "aporte_por_variable",
    "caso_por_id",
    "columnas_que_ve_el_scorer",
    "comparar_con_base",
    "comparar_off_on",
    "describir_libro",
    "descripcion_tipos",
    "elegir_recomendada",
    "es_base",
    "exigir_columnas_en_scorer",
    "exportar_comparacion",
    "huella_particion",
    "leer_configuraciones",
    "leer_etiquetas_de",
    "localizar_plantilla",
    "marco_registros",
    "medicion_como_dict",
    "medir_configuracion",
    "pares_de_prueba",
    "resumen",
    "tabla_aporte",
    "tabla_pares_que_cambian",
    "tabla_resumen",
    "validar_configuraciones",
    "variable",
]
