"""record_linkage.matching.comparadores_extra — Registro de comparadores (v0.18.0).

Contexto: Google Colab Free (~12 GB RAM) y bases de 2–5 M de registros.

Por qué existe
--------------
Hasta 0.17.4 la comparación de una variable adicional vivía en una cadena de
``if ftype == ...`` dentro del scorer. Añadir una forma de comparar obligaba a
editar el scorer, que es justo lo que el principio abierto/cerrado busca
evitar: el módulo más crítico del sistema se modificaba por cada variable
nueva, y cada modificación arriesgaba la paridad de todo lo demás.

Aquí los comparadores son objetos registrados por nombre. El scorer pide uno
al registro y lo aplica; no sabe cuántos hay ni cómo funcionan. Añadir una
forma de comparar es añadir una función con un decorador.

Qué resolvió en la práctica
---------------------------
Medido sobre ``ground_truth_grande.csv``: declarar TELEFONO y EMAIL como
evidencia adicional con el comparador exacto NO cambiaba ni un par, porque
dentro de un mismo grupo el teléfono aparece como ``312 1897799``,
``312-189-7799`` y ``+573121897799``, y el correo como ``SERVICIOSDEL@…`` y
``serviciosdel.co@…``. Un comparador exacto los declara distintos y *penaliza*
a los pares verdaderos. Los comparadores de este módulo canonicalizan antes de
comparar, que es lo que un humano hace sin pensarlo.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-29  ·  Version: 0.18.0
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

__all__ = [
    "NULOS_EXTENDIDO",
    "NULOS_LEGADO",
    "Comparador",
    "ComparadorExtra",
    "canonicalizar_documento",
    "canonicalizar_email",
    "canonicalizar_telefono",
    "obtener",
    "registrar",
    "tipos_disponibles",
]

#: Centinelas de nulo que reconocen los comparadores heredados. NO se amplía:
#: cualquier valor extra cambiaría el resultado de corridas ya calibradas, y
#: esa decisión merece su propia versión y su propia medición (ver ADR-0002).
NULOS_LEGADO = frozenset({"", "NAN", "NONE", "NULL", "<NA>"})

#: Centinelas que reconocen los comparadores nuevos (v0.18.0). Incluye las
#: formas que las fuentes reales usan para decir "no hay dato" y que el
#: conjunto heredado trataba como un valor legítimo.
NULOS_EXTENDIDO = NULOS_LEGADO | frozenset({"N/A", "NA", "-", "--", "SIN DATO", "NO DEFINIDO"})

#: Dígitos finales que se comparan en un teléfono. Siete es el largo del
#: número de abonado en la mayoría de planes de numeración del mundo: compara
#: la línea sin depender del prefijo internacional ni del indicativo, que es
#: exactamente donde difieren los registros de una misma entidad.
DIGITOS_TELEFONO = 7

#: Longitud mínima para que un teléfono se considere informativo.
MINIMO_DIGITOS_TELEFONO = 7


@runtime_checkable
class ComparadorExtra(Protocol):
    """Contrato de un comparador de variable adicional.

    Un comparador recibe dos arreglos alineados —lado izquierdo y derecho de
    cada par candidato— y devuelve una similitud por par. El scorer multiplica
    esa similitud por el peso declarado y la suma al score.
    """

    def __call__(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:  # pragma: no cover
        """Similitud por par, del mismo largo que las entradas."""
        ...


@dataclass(frozen=True)
class Comparador:
    """Comparador registrado, con los metadatos que lo hacen auditable.

    Attributes:
        nombre: identificador usado en ``extra_features[...]["type"]``.
        funcion: implementación vectorizada.
        firmado: True si el rango es [-1, 1] —es decir, si PENALIZA la
            discrepancia además de premiar la coincidencia—. Solo los
            firmados pueden separar casos negativos.
        descripcion: para qué sirve, en una frase.
    """

    nombre: str
    funcion: Callable[[np.ndarray, np.ndarray], np.ndarray]
    firmado: bool
    descripcion: str

    def __call__(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return self.funcion(a, b)


_REGISTRO: dict[str, Comparador] = {}


def registrar(
    nombre: str, *, firmado: bool, descripcion: str
) -> Callable[[Callable[[np.ndarray, np.ndarray], np.ndarray]], Comparador]:
    """Decorador que inscribe un comparador en el registro.

    Args:
        nombre: identificador único.
        firmado: si el rango es [-1, 1].
        descripcion: para qué sirve.

    Returns:
        Decorador que devuelve el ``Comparador`` registrado.

    Raises:
        ValueError: si el nombre ya está tomado (un registro silenciosamente
            sobrescrito es una fuente de resultados irreproducibles).
    """

    def decorador(funcion: Callable[[np.ndarray, np.ndarray], np.ndarray]) -> Comparador:
        if nombre in _REGISTRO:
            raise ValueError(f"El comparador {nombre!r} ya está registrado.")
        comparador = Comparador(
            nombre=nombre, funcion=funcion, firmado=firmado, descripcion=descripcion
        )
        _REGISTRO[nombre] = comparador
        return comparador

    return decorador


def obtener(nombre: str) -> Comparador | None:
    """Devuelve el comparador registrado con ese nombre, o None."""
    return _REGISTRO.get(nombre)


def tipos_disponibles() -> tuple[str, ...]:
    """Nombres de todos los comparadores registrados, en orden alfabético."""
    return tuple(sorted(_REGISTRO))


# ── Utilidades compartidas ────────────────────────────────────────────────


def _texto(valores: np.ndarray) -> pd.Series:
    """Serie de strings sin nulos flotantes.

    pandas 2.1+ dejó de convertir ``NaN`` a la cadena ``'nan'`` al hacer
    ``astype(str)``. Materializar el nulo como cadena vacía ANTES de la
    conversión hace que la política "nulo → 0.0" se aplique igual en 2.x y 3.x;
    sin esto, en pandas 3 los nulos se contaban como valores distintos y
    penalizaban pares verdaderos.
    """
    serie = pd.Series(valores)
    return serie.where(serie.notna(), "").astype(str)


def _validos(serie: pd.Series, nulos: frozenset[str] = NULOS_LEGADO) -> np.ndarray:
    """Máscara de valores informativos (no vacíos ni centinelas de nulo)."""
    return ~serie.str.strip().str.upper().isin(nulos).to_numpy()


def _firmar(validos_ambos: np.ndarray, iguales: np.ndarray) -> np.ndarray:
    """+1 donde coinciden, −1 donde discrepan, 0 donde falta información."""
    salida = np.zeros(len(validos_ambos), dtype=np.float64)
    salida[validos_ambos & iguales] = 1.0
    salida[validos_ambos & ~iguales] = -1.0
    return salida


def canonicalizar_telefono(valores: np.ndarray, digitos: int = DIGITOS_TELEFONO) -> pd.Series:
    """Reduce un teléfono a sus últimos ``digitos`` dígitos significativos.

    ``312 1897799``, ``312-189-7799`` y ``+573121897799`` son el mismo número
    escrito por tres sistemas distintos. Quedarse con la cola elimina el
    prefijo internacional y el indicativo, que es donde difieren.

    Args:
        valores: arreglo de teléfonos tal como vienen de la fuente.
        digitos: cuántos dígitos finales conservar.

    Returns:
        Serie con la cola de dígitos, o cadena vacía si el número no tiene
        suficientes dígitos para ser informativo.
    """
    texto = _texto(valores)
    informativo = _validos(texto, NULOS_EXTENDIDO)
    solo_digitos = texto.str.replace(r"\D", "", regex=True)
    suficiente = (solo_digitos.str.len() >= MINIMO_DIGITOS_TELEFONO).to_numpy() & informativo
    return solo_digitos.str[-digitos:].where(pd.Series(suficiente, index=texto.index), "")


def canonicalizar_email(valores: np.ndarray) -> tuple[pd.Series, pd.Series]:
    """Separa un correo en (parte local, dominio), ambos normalizados.

    El dominio es la parte con significado institucional: dos correos de
    dominios distintos casi nunca son la misma entidad, mientras que
    ``serviciosdel@`` y ``serviciosdel.co@`` del mismo dominio suelen serlo.
    """
    crudo = _texto(valores)
    texto = crudo.str.strip().str.lower()
    informativo = pd.Series(_validos(crudo, NULOS_EXTENDIDO), index=crudo.index)
    con_arroba = texto.str.contains("@", regex=False) & informativo
    local = texto.str.split("@").str[0].where(con_arroba, "")
    dominio = texto.str.split("@").str[-1].where(con_arroba, "")
    return local, dominio


def canonicalizar_documento(valores: np.ndarray) -> pd.Series:
    """Deja un documento en dígitos, sin ceros a la izquierda ni separadores."""
    texto = _texto(valores)
    informativo = pd.Series(_validos(texto, NULOS_EXTENDIDO), index=texto.index)
    solo_digitos = texto.str.replace(r"\D", "", regex=True).str.lstrip("0")
    return solo_digitos.where(informativo, "")


# ── Comparadores heredados (paridad exacta con 0.17.4) ────────────────────


@registrar("exact_or_zero", firmado=False, descripcion="1.0 si coinciden exactamente; 0.0 si no.")
def _exact_or_zero(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    sa, sb = _texto(a).str.strip(), _texto(b).str.strip()
    return (_validos(sa) & _validos(sb) & (sa.to_numpy() == sb.to_numpy())).astype(np.float64)


@registrar(
    "categorical",
    firmado=False,
    descripcion="1.0 si coinciden, 0.5 si falta uno, 0.0 si discrepan.",
)
def _categorical(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    sa, sb = _texto(a).str.strip().str.upper(), _texto(b).str.strip().str.upper()
    va, vb = _validos(sa), _validos(sb)
    salida = np.zeros(len(sa), dtype=np.float64)
    salida[va & vb & (sa.to_numpy() == sb.to_numpy())] = 1.0
    salida[va ^ vb] = 0.5
    return salida


@registrar(
    "categorical_signed",
    firmado=True,
    descripcion="+1 coinciden · −1 discrepan · 0 si falta alguno (recomendado para CIUDAD).",
)
def _categorical_signed(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    sa, sb = _texto(a).str.strip().str.upper(), _texto(b).str.strip().str.upper()
    return _firmar(_validos(sa) & _validos(sb), sa.to_numpy() == sb.to_numpy())


@registrar(
    "exact_signed",
    firmado=True,
    descripcion="Como categorical_signed pero sensible a mayúsculas.",
)
def _exact_signed(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    sa, sb = _texto(a).str.strip(), _texto(b).str.strip()
    return _firmar(_validos(sa) & _validos(sb), sa.to_numpy() == sb.to_numpy())


def _token_set(a: np.ndarray, b: np.ndarray, *, firmado: bool) -> np.ndarray:
    try:
        from rapidfuzz import fuzz, process as rf_process
    except ImportError:  # pragma: no cover - rapidfuzz es dependencia declarada
        return np.zeros(len(a), dtype=np.float64)
    sa, sb = _texto(a).str.strip(), _texto(b).str.strip()
    ambos = _validos(sa) & _validos(sb)
    puntajes = (
        rf_process.cpdist(sa.to_list(), sb.to_list(), scorer=fuzz.token_set_ratio, dtype=np.float64)
        / 100.0
    )
    if firmado:
        return np.where(ambos, 2.0 * puntajes - 1.0, 0.0)
    return np.where(ambos, puntajes, 0.0)


@registrar("token_set_ratio", firmado=False, descripcion="Similitud difusa de tokens, 0..1.")
def _token_set_ratio(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return _token_set(a, b, firmado=False)


@registrar("token_set_ratio_signed", firmado=True, descripcion="Similitud difusa firmada, −1..+1.")
def _token_set_ratio_signed(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return _token_set(a, b, firmado=True)


# ── Comparadores nuevos (v0.18.0) ─────────────────────────────────────────


@registrar(
    "telefono_signed",
    firmado=True,
    descripcion="Compara los últimos 7 dígitos: ignora prefijo país, indicativo y separadores.",
)
def _telefono_signed(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ca, cb = canonicalizar_telefono(a), canonicalizar_telefono(b)
    ambos = (ca.str.len() > 0).to_numpy() & (cb.str.len() > 0).to_numpy()
    return _firmar(ambos, ca.to_numpy() == cb.to_numpy())


@registrar(
    "email_signed",
    firmado=True,
    descripcion="Dominio distinto → −1; mismo dominio → similitud graduada de la parte local.",
)
def _email_signed(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    la, da = canonicalizar_email(a)
    lb, db = canonicalizar_email(b)
    ambos = (da.str.len() > 0).to_numpy() & (db.str.len() > 0).to_numpy()
    mismo_dominio = da.to_numpy() == db.to_numpy()

    salida = np.zeros(len(la), dtype=np.float64)
    salida[ambos & ~mismo_dominio] = -1.0
    candidatos = ambos & mismo_dominio
    if not candidatos.any():
        return salida
    iguales = candidatos & (la.to_numpy() == lb.to_numpy())
    salida[iguales] = 1.0
    graduar = candidatos & ~iguales
    if graduar.any():
        try:
            from rapidfuzz import distance, process as rf_process
        except ImportError:  # pragma: no cover
            salida[graduar] = -1.0
            return salida
        posiciones = np.flatnonzero(graduar)
        similitud = rf_process.cpdist(
            la.to_numpy()[posiciones].tolist(),
            lb.to_numpy()[posiciones].tolist(),
            scorer=distance.Indel.normalized_similarity,
            dtype=np.float64,
        )
        salida[posiciones] = 2.0 * similitud - 1.0
    return salida


@registrar(
    "documento_signed",
    firmado=True,
    descripcion="Compara identificadores en dígitos, sin separadores ni ceros a la izquierda.",
)
def _documento_signed(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ca, cb = canonicalizar_documento(a), canonicalizar_documento(b)
    ambos = (ca.str.len() > 0).to_numpy() & (cb.str.len() > 0).to_numpy()
    return _firmar(ambos, ca.to_numpy() == cb.to_numpy())


# ── Comparadores multicriterio (v0.19.0) ──────────────────────────────────
#
# Los dos que siguen existen porque medir con el comparador equivocado hace
# parecer inútil a una variable que sí sirve. Las cifras están medidas sobre
# `benchmark_institucional.csv` y se reproducen con
# `scripts/diagnostico_variables.py`.

#: Separadores con los que las fuentes empaquetan varios valores en una celda.
SEPARADORES_CONJUNTO = "|;"

#: Palabras que no aportan identidad a un topónimo y estorban al comparar.
#: Son artículos y preposiciones, no nombres de lugar, así que la lista vale
#: para cualquier español y no ata la librería a un país.
VACIAS_TOPONIMO = frozenset({"DE", "DEL", "LA", "EL", "LOS", "LAS", "Y", "D", "C", "DC"})


def _conjuntos(valores: np.ndarray) -> list[frozenset[str]]:
    """Parte cada celda en el conjunto de valores que empaqueta."""
    import re as _re

    patron = _re.compile(f"[{_re.escape(SEPARADORES_CONJUNTO)}]")
    salida: list[frozenset[str]] = []
    for bruto in _texto(valores):
        piezas = {" ".join(p.upper().split()) for p in patron.split(bruto) if p and p.strip()}
        salida.append(frozenset(p for p in piezas if p and p not in NULOS_EXTENDIDO))
    return salida


@registrar(
    "conjunto_signed",
    firmado=True,
    descripcion=(
        "Varios valores por celda (separados por | o ;): coeficiente de "
        "solapamiento |A∩B|/min(|A|,|B|), firmado. Para CIIU y listas."
    ),
)
def _conjunto_signed(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Compara conjuntos por SOLAPAMIENTO, no por Jaccard.

    Por qué no Jaccard. El RUES publica 2,52 actividades por empresa y la
    Superintendencia de Sociedades publica 1: la principal. Cuando el conjunto
    chico está contenido en el grande, Jaccard da 1/2,52 ≈ 0,40 y castiga a un
    par que es correcto; el solapamiento da 1,0.

    Medido sobre pares del mismo ente con CIIU en las dos fuentes
    (n = 297): solapamiento = 1 en el 99,33 % de los casos, Jaccard = 1 en el
    19,53 %. Sobre negativos de nombre confundible (n = 986) el solapamiento
    llega a 1 solo en el 20,69 %, y sobre pares al azar en el 1,29 %. Es decir:
    el solapamiento separa, Jaccard borra la señal.
    """
    ca, cb = _conjuntos(a), _conjuntos(b)
    salida = np.zeros(len(ca), dtype=np.float64)
    for posicion, (x, y) in enumerate(zip(ca, cb, strict=True)):
        if not x or not y:
            continue
        salida[posicion] = 2.0 * (len(x & y) / min(len(x), len(y))) - 1.0
    return salida


@registrar(
    "categoria_tolerante_signed",
    firmado=True,
    descripcion=(
        "Categórico que acepta que un nombre elabore al otro: 'CALI' ≡ "
        "'SANTIAGO DE CALI'. Para DEPARTAMENTO, MUNICIPIO y similares."
    ),
)
def _categoria_tolerante_signed(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Igualdad por CONTENCIÓN de tokens, no por cadena idéntica.

    Dos fuentes que nombran el mismo lugar rara vez lo escriben igual: una
    dice 'Bogotá' y la otra 'Bogotá D.C.'; una dice 'Cali' y la otra
    'Santiago de Cali'. La regla es genérica —un conjunto de tokens contiene
    al otro— y no depende de ninguna tabla de sinónimos de ningún país.

    Medido sobre `benchmark_institucional.csv`, separación entre pares
    verdaderos y negativos de nombre confundible:

        DEPARTAMENTO   exacto  60,88 % vs 20,55 %  →  40,3 pp
                       contención 90,87 % vs 20,95 %  →  69,9 pp
        MUNICIPIO      exacto  44,62 % vs 14,57 %  →  30,1 pp
                       contención 82,24 % vs 14,57 %  →  67,7 pp

    Con el comparador exacto la geografía parece una variable floja. No lo
    es: lo flojo era la forma de compararla.
    """
    import unicodedata as _ud

    def tokens(serie: pd.Series) -> list[frozenset[str]]:
        salida: list[frozenset[str]] = []
        for bruto in serie:
            plano = _ud.normalize("NFKD", str(bruto).upper())
            plano = "".join(c for c in plano if not _ud.combining(c))
            piezas = {
                p
                for p in "".join(c if c.isalnum() else " " for c in plano).split()
                if p not in VACIAS_TOPONIMO
            }
            salida.append(frozenset(piezas))
        return salida

    sa, sb = _texto(a).str.strip(), _texto(b).str.strip()
    ambos = np.asarray(_validos(sa) & _validos(sb))
    ta, tb = tokens(sa), tokens(sb)
    igual = np.fromiter(
        ((bool(x) and bool(y)) and (x <= y or y <= x) for x, y in zip(ta, tb, strict=True)),
        dtype=bool,
        count=len(ta),
    )
    return _firmar(ambos, igual)
