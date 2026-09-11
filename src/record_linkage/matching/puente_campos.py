"""record_linkage.matching.puente_campos — Un solo catálogo de comparación (v0.20.0).

El defecto que corrige
----------------------
Hasta 0.19.0 la librería tenía DOS catálogos de comparadores que no se
conocían entre sí:

* ``matching.campos`` + ``matching.comparators`` — 14 tipos declarativos
  (nombre de empresa, nombre de persona, identificador, teléfono, correo,
  dirección, ciudad, geo, fecha, numérico, categórico, jerárquico, conjunto,
  booleano), usados por el motor multicampo en memoria.
* ``matching.comparadores_extra`` — el registro que consume el scorer de
  producción (``engine.scorer``) a través de ``extra_features``, y que solo
  conocía once formas de comparar.

Consecuencia práctica: **el camino que procesa millones de registros en disco
no podía usar la mitad de lo que la librería ya sabía hacer.** Un usuario con
coordenadas, fechas o montos tenía que elegir entre escala y criterio. Eso no
es una funcionalidad que falta: es la misma capacidad implementada en un lado
y ausente en el otro, que es la definición de deuda técnica.

Cómo lo corrige
---------------
Este módulo NO reimplementa nada. Registra en el catálogo de producción los
comparadores canónicos de cada ``TipoCampo``, delegando en la misma instancia
que usa el motor multicampo. Una sola implementación por forma de comparar
(DRY), el scorer sigue sin saber cuántas hay (OCP) y ambos motores dependen
del protocolo ``Comparator``, no de clases concretas (DIP).

Los nombres registrados llevan el prefijo ``tipo_`` para no chocar con los
comparadores heredados, cuyo comportamiento está congelado por el oráculo de
paridad y no debe cambiar.

Uso desde el camino de producción::

    linkage(..., extra_features=[("FECHA_CONST", "tipo_fecha", 0.10)])

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-29  ·  Version: 0.20.0
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .campos import TipoCampo, comparador_por_defecto
from .comparadores_extra import registrar

__all__ = ["PREFIJO", "SEPARADOR_GEO", "TIPOS_PUENTEADOS", "descomponer_geo"]

#: Prefijo de los nombres registrados por este puente.
PREFIJO = "tipo_"

#: Separadores admitidos para empaquetar latitud y longitud en una columna.
SEPARADOR_GEO = ",;| "

#: Tipos que el puente expone al camino de producción. Se excluye
#: ``IDENTIFICADOR``: el scorer ya trata el identificador como evidencia de
#: primera clase, con su propio veto, boost y forma canónica; exponerlo además
#: como variable adicional invitaría a contarlo dos veces.
TIPOS_PUENTEADOS: tuple[TipoCampo, ...] = (
    TipoCampo.NOMBRE_EMPRESA,
    TipoCampo.NOMBRE_PERSONA,
    TipoCampo.TELEFONO,
    TipoCampo.EMAIL,
    TipoCampo.DIRECCION,
    TipoCampo.CIUDAD,
    TipoCampo.GEO,
    TipoCampo.FECHA,
    TipoCampo.NUMERICO,
    TipoCampo.CATEGORICO,
    TipoCampo.JERARQUICO,
    TipoCampo.CONJUNTO,
    TipoCampo.BOOLEANO,
)


def descomponer_geo(valores: np.ndarray) -> np.ndarray:
    """Convierte una columna ``"lat,lon"`` en el arreglo ``(n, 2)`` que exige el haversine.

    El motor multicampo recibe latitud y longitud en dos columnas declaradas.
    El camino de producción pasa una sola columna por variable adicional, así
    que aquí se admite el par empaquetado en un texto: ``"4.65,-74.05"``,
    ``"4.65;-74.05"`` o ``"4.65 -74.05"``. Lo que no se pueda leer como dos
    números queda NaN, que el comparador ya trata como faltante neutro.

    Args:
        valores: columna con el par de coordenadas por registro.

    Returns:
        ``np.ndarray`` de forma ``(n, 2)`` y tipo float64.
    """
    texto = pd.Series(pd.array(np.asarray(valores), dtype="string")).fillna("")
    partido = texto.str.strip().str.split(f"[{SEPARADOR_GEO}]+", regex=True, expand=True)
    salida = np.full((len(texto), 2), np.nan, dtype=np.float64)
    for columna in range(min(2, partido.shape[1])):
        salida[:, columna] = pd.to_numeric(partido[columna], errors="coerce").to_numpy(
            dtype="float64", na_value=np.nan
        )
    return salida


def _adaptar(tipo: TipoCampo):
    """Envuelve el comparador canónico del tipo en la firma del registro.

    El registro de producción trabaja con dos arreglos de una columna; el
    protocolo ``Comparator`` es el mismo salvo en GEO, que necesita ``(n, 2)``.
    Esa es toda la adaptación: no hay lógica de comparación aquí.
    """
    comparador = comparador_por_defecto(tipo, {})
    preparar = descomponer_geo if tipo is TipoCampo.GEO else (lambda v: np.asarray(v))

    def comparar(izquierda: np.ndarray, derecha: np.ndarray) -> np.ndarray:
        resultado = comparador.compare(preparar(izquierda), preparar(derecha))
        return np.asarray(resultado, dtype=np.float64)

    comparar.__name__ = f"_puente_{tipo.value}"
    return comparador, comparar


def _registrar_todos() -> tuple[str, ...]:
    """Registra un comparador por tipo. Idempotente: si ya está, no lo pisa."""
    from .comparadores_extra import obtener

    nombres = []
    for tipo in TIPOS_PUENTEADOS:
        nombre = f"{PREFIJO}{tipo.value}"
        if obtener(nombre) is not None:  # pragma: no cover - reimportación
            nombres.append(nombre)
            continue
        comparador, funcion = _adaptar(tipo)
        registrar(
            nombre,
            firmado=bool(getattr(comparador, "signed", True)),
            descripcion=(
                f"Comparador canónico del tipo de campo '{tipo.value}' "
                f"({getattr(comparador, 'name', tipo.value)}), compartido con el "
                f"motor multicampo."
            ),
        )(funcion)
        nombres.append(nombre)
    return tuple(nombres)


#: Nombres efectivamente registrados, en orden. Sirve para documentar y para
#: que una prueba pueda afirmar que el puente no dejó ningún tipo fuera.
NOMBRES_REGISTRADOS: tuple[str, ...] = _registrar_todos()
