"""record_linkage.matching.identificadores — Dígito de verificación (v0.19.0).

Por qué existe
--------------
Un identificador oficial casi nunca viaja en una sola forma. En Colombia el
NIT circula como ``890903436`` y como ``8909034362``: el mismo número, con y
sin dígito de verificación. Hasta 0.18.0 la librería trataba esas dos formas
como dos identificadores distintos y **vetaba** el par, así que dos registros
del mismo ente quedaban separados justo cuando el dato más fuerte que tenían
decía que eran el mismo.

Cuánto pesa el problema
-----------------------
Medido sobre ``benchmark_institucional.csv``: de los 5.312 pares en que dos
registros del mismo grupo traen identificadores textualmente distintos, en
5.269 (99,2 %) uno es el otro más un dígito al final, y en 5.002 (94,9 % de
esos) ese dígito es exactamente el de verificación calculado por módulo 11.
No es ruido: es la forma normal en que dos fuentes escriben el mismo número.

Cuánto riesgo tiene corregirlo
------------------------------
Sobre 145.082 identificadores distintos de CRM, RUES y Superintendencia de
Sociedades, colapsar ``X`` con ``X+DV`` produce 35.329 claves compartidas y
**todas** agrupan exactamente dos formas del mismo número: cero casos de tres
o más, cero agrupaciones que no sean prefijo común. El riesgo de unir dos
entes ajenos con esta regla es, en ese corpus, nulo.

Alcance
-------
El algoritmo módulo 11 con los pesos de la DIAN es colombiano, pero la
*estructura* —identificador base más dígito de control— es universal: CNPJ en
Brasil, RUT en Chile, CUIT en Argentina, IVA europeo. La verificación se
expone como función aparte, de modo que añadir otro país es añadir otra
función, no tocar el scorer (ver ADR-0004).

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-29  ·  Version: 0.19.0
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "LONGITUD_MINIMA_BASE",
    "PESOS_DIAN",
    "base_canonica",
    "bases_canonicas",
    "bases_validas",
    "digito_verificacion_dian",
    "es_extension_por_digito_verificacion",
    "formas_canonicas",
]

#: Pesos primos del algoritmo módulo 11 que publica la DIAN, de derecha a
#: izquierda sobre la base del NIT. Fuente: Resolución DIAN sobre estructura
#: del NIT; el mismo cuadro aparece en el formulario RUT.
PESOS_DIAN: tuple[int, ...] = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)

#: Longitud mínima de la base para siquiera intentar validar un dígito. Por
#: debajo de 7 dígitos la coincidencia del módulo 11 es casual con demasiada
#: frecuencia (1 de cada 11) y el número no tiene forma de identificador.
LONGITUD_MINIMA_BASE = 7


def digito_verificacion_dian(base: str) -> str:
    """Dígito de verificación de un identificador, por módulo 11.

    Args:
        base: identificador sin dígito de verificación, solo dígitos.

    Returns:
        El dígito como cadena de un carácter, o "" si la base no sirve
        (vacía, con no-dígitos, o más larga que los pesos disponibles).

    Examples:
        >>> digito_verificacion_dian("890903436")
        '2'
        >>> digito_verificacion_dian("10282948")
        '2'
    """
    if not base or not base.isdigit() or len(base) > len(PESOS_DIAN):
        return ""
    total = sum(int(c) * PESOS_DIAN[i] for i, c in enumerate(reversed(base)))
    residuo = total % 11
    return str(residuo) if residuo < 2 else str(11 - residuo)


def _solo_digitos(valores: np.ndarray | pd.Series) -> np.ndarray:
    serie = pd.Series(pd.array(np.asarray(valores), dtype="string")).fillna("")
    return serie.str.replace(r"\D", "", regex=True).str.lstrip("0").to_numpy(dtype=object)


def es_extension_por_digito_verificacion(izquierda: np.ndarray, derecha: np.ndarray) -> np.ndarray:
    """¿Son el mismo identificador escrito con y sin dígito de verificación?

    True cuando uno de los dos, en dígitos, es exactamente el otro más un
    carácter al final Y ese carácter es el dígito de verificación calculado
    sobre el más corto. La verificación importa: sin ella, ``12345678`` y
    ``123456789`` pasarían por lo mismo aunque sean números ajenos.

    Args:
        izquierda: identificadores del lado izquierdo del par.
        derecha: identificadores del lado derecho, misma longitud.

    Returns:
        Máscara booleana por par.

    Raises:
        ValueError: si los dos arreglos tienen longitudes distintas.
    """
    a, b = _solo_digitos(izquierda), _solo_digitos(derecha)
    if len(a) != len(b):
        raise ValueError(f"longitudes distintas: {len(a)} vs {len(b)}")
    salida = np.zeros(len(a), dtype=bool)
    memoria: dict[str, str] = {}
    for posicion, (x, y) in enumerate(zip(a, b, strict=True)):
        largo, corto = (x, y) if len(x) > len(y) else (y, x)
        if len(largo) != len(corto) + 1 or not largo.startswith(corto):
            continue
        if len(corto) < LONGITUD_MINIMA_BASE:
            continue
        esperado = memoria.get(corto)
        if esperado is None:
            esperado = digito_verificacion_dian(corto)
            memoria[corto] = esperado
        salida[posicion] = bool(esperado) and largo[-1] == esperado
    return salida


def formas_canonicas(valor: object) -> frozenset[str]:
    """Todas las escrituras plausibles de un identificador.

    Un valor de nueve dígitos puede ser un NIT de empresa tal cual, o una
    cédula de ocho más su dígito de verificación. No hay forma de saberlo por
    el número solo, así que no se elige: se devuelven las dos y que decida
    quien tenga más contexto.
    """
    digitos = str(valor or "")
    digitos = "".join(c for c in digitos if c.isdigit()).lstrip("0")
    if not digitos:
        return frozenset()
    formas = {digitos}
    base = digitos[:-1]
    if len(base) >= LONGITUD_MINIMA_BASE and digitos[-1] == digito_verificacion_dian(base):
        formas.add(base)
    return frozenset(formas)


#: Cuántos dígitos de control se aceptan encima de la base. Dos, porque la
#: cadena real llega a serlo: la fuente escribe ``NIT+DV`` y el preprocesador
#: le calcula y añade otro DV, de modo que ``10282948`` viaja como
#: ``1028294825``. Medido sobre 145.082 identificadores reales, quitar hasta
#: dos colapsa 35.045 claves y las 5 que agrupan más de dos formas son
#: cadenas legítimas del mismo número; ninguna agrupación deja de ser prefijo
#: común. Con tres el riesgo empieza a crecer sin ganar casos.
PASOS_MAXIMOS_DV = 2


def base_canonica(valor: object) -> str:
    """Identificador reducido a su base, sin dígitos de control validados.

    Quita un dígito final solo si es exactamente el de verificación de lo que
    queda, y como mucho ``PASOS_MAXIMOS_DV`` veces. Un dígito que no valida se
    queda: la función nunca adivina.

    Examples:
        >>> base_canonica("1028294825")
        '10282948'
        >>> base_canonica("890903436")
        '890903436'
    """
    digitos = "".join(c for c in str(valor or "") if c.isdigit()).lstrip("0")
    for _ in range(PASOS_MAXIMOS_DV):
        if len(digitos) <= LONGITUD_MINIMA_BASE:
            break
        if digitos[-1] != digito_verificacion_dian(digitos[:-1]):
            break
        digitos = digitos[:-1]
    return digitos


def bases_canonicas(valores: np.ndarray) -> np.ndarray:
    """`base_canonica` sobre un arreglo, resolviendo cada valor único una vez.

    En un lote de pares los mismos identificadores se repiten muchas veces;
    calcular el módulo 11 por par sería trabajo tirado. Resolver por valor
    único mantiene el costo proporcional a la cardinalidad, no al número de
    pares.

    Costo medido sobre 5 M de identificadores (4,2 M únicos): 35 s sobre el
    arreglo completo y 4,7 s por cada millón de pares scoreados — unos 80 s
    adicionales en una corrida de 20 M de pares que dura ~41 min.

    Optimización probada y DESCARTADA: calcular el módulo 11 con una
    multiplicación de matrices en vez del bucle por valor único. Suena obvio y
    no sirve — el costo no está en la aritmética sino en las operaciones de
    cadena de pandas sobre 4,2 M de valores únicos. Medido: 39,5 s contra
    35,3 s en el arreglo completo, y solo un 15 % mejor por lote, a cambio de
    35 líneas más de código. Se conserva la versión simple. Queda anotado para
    que nadie vuelva a intentarlo sin medir antes.
    """
    serie = pd.Series(pd.array(np.asarray(valores), dtype="string")).fillna("")
    unicos = serie.drop_duplicates()
    mapa = {valor: base_canonica(valor) for valor in unicos}
    return serie.map(mapa).to_numpy(dtype=object)


def bases_validas(valores: np.ndarray) -> np.ndarray:
    """``bases_canonicas`` dejando vacía toda base que no sirve para agrupar.

    Una base más corta que ``LONGITUD_MINIMA_BASE`` (o vacía) no identifica a
    nadie. Es la reducción que ``salida/completar.py`` aplica a ``NIT_FINAL``
    y a ``NIT_OK`` del motor (``ID_ENTIDAD``, ``METODO_UNION`` y el conteo de
    conflictos que el QA del flujo lee del manifiesto): una sola regla,
    escrita una sola vez. Sobre un ``NIT`` crudo (flotante ``900111222.0``,
    prefijos) NO reproduce la limpieza de NitProcessor: por eso el contrato
    parte de ``NIT_OK`` y no del valor de la fuente.

    Returns:
        Arreglo de ``object`` con la base canónica, o ``""`` si no es válida.
    """
    bases = bases_canonicas(valores)
    longitudes = pd.Series(bases, dtype="string").str.len().fillna(0).to_numpy()
    return np.where(longitudes >= LONGITUD_MINIMA_BASE, bases, "")
