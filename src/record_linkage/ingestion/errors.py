"""Errores públicos de la capa de ingestión.

Las excepciones son deliberadamente específicas: en producción no conviene
confundir un archivo peligroso con un esquema incompleto o con bytes que no se
pueden decodificar sin pérdida.
"""

from __future__ import annotations


class IngestionError(ValueError):
    """Error base para entradas inválidas o no soportadas."""


class ArchiveSafetyError(IngestionError):
    """El archivo comprimido viola un límite o una regla de seguridad."""


class EncodingDetectionError(IngestionError, UnicodeError):
    """No fue posible identificar una codificación de texto sin pérdida."""


class SchemaError(IngestionError):
    """Las columnas observadas no satisfacen el contrato de la fuente."""
