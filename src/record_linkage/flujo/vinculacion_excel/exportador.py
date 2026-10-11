"""Escritura del libro Excel de entrega de la vinculación: ruta de salida, hojas y guardado."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from record_linkage.flujo.vinculacion.config import ConfigVinculacion
from record_linkage.flujo.vinculacion.resultado import Resultado
from record_linkage.flujo.vinculacion_excel.estilos import Estilos
from record_linkage.flujo.vinculacion_excel.hojas import escribir_hojas

__all__ = ["exportar_vinculacion_excel"]

#: Zoom con el que se abre cada hoja.
_ZOOM = 90


def _ruta_de_salida(cfg: ConfigVinculacion, ruta: str | Path | None) -> Path:
    """Ruta del archivo, con sus carpetas creadas.

    Sin ``ruta`` se usa ``<carpeta_salida>/<prefijo_salida>[_AAAA-MM-DD_HHMM].xlsx``; el sello solo va
    si ``versionar_salida`` es verdadero.
    """
    if ruta is None:
        carpeta = Path(cfg.carpeta_salida)
        carpeta.mkdir(parents=True, exist_ok=True)
        # Hallazgo F3: el sello llega al minuto: dos exportaciones del mismo minuto escriben el mismo archivo.
        sello = datetime.now().strftime("_%Y-%m-%d_%H%M") if cfg.versionar_salida else ""
        ruta = carpeta / f"{cfg.prefijo_salida}{sello}.xlsx"
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    return ruta


def exportar_vinculacion_excel(res: Resultado, ruta: str | Path | None = None) -> Path:
    """Escribe el Excel de entrega con formato profesional y devuelve su ruta.

    Args:
        res: resultado de una corrida de vinculación (tablas, resumen, cobertura y calidad).
        ruta: archivo de salida. Si es ``None``, se escribe en ``res.cfg.carpeta_salida`` con el prefijo
            ``res.cfg.prefijo_salida`` y, si ``res.cfg.versionar_salida``, el sello ``_AAAA-MM-DD_HHMM``.
            Las carpetas que falten se crean.

    Returns:
        La ruta del archivo escrito.
    """
    ruta = _ruta_de_salida(res.cfg, ruta)
    libro = Workbook()
    libro.remove(libro.active)
    escribir_hojas(libro, Estilos(), res)
    for hoja in libro.worksheets:
        hoja.sheet_view.zoomScale = _ZOOM
    libro.calculation.fullCalcOnLoad = True  # Excel recalcula las fórmulas del RESUMEN al abrir
    libro.save(ruta)
    return ruta
