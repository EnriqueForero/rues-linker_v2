"""Excel de entrega de la vinculación de empresas.

``exportar_vinculacion_excel(res)`` escribe, a partir del ``Resultado`` de una corrida, el libro que se
entrega: LEEME, RESUMEN (con fórmulas vivas sobre EXPORTADORES), EXPORTADORES, ENLACES, REVISION (con
lista desplegable y columnas para llenar), DECISIONES, CONTACTOS, DETALLE_*, DEDUPLICACION, VALIDACION,
CALIDAD_DATOS, CONTROL_QA, PARAMETROS y METODOLOGIA.

El subpaquete se reparte así: ``estilos`` (cómo se ve), ``tablas`` (volcar un ``DataFrame`` en una
hoja), ``leeme`` (hoja LEEME y textos de METODOLOGIA), ``hojas`` (el contenido de cada hoja y su
orden) y ``exportador`` (ruta de salida y guardado).
"""

from __future__ import annotations

from record_linkage.flujo.vinculacion_excel.exportador import exportar_vinculacion_excel

#: Nombre que esta función tenía en el motor original; lo usan el arnés de paridad y los notebooks.
exportar_excel = exportar_vinculacion_excel

__all__ = ["exportar_excel", "exportar_vinculacion_excel"]
