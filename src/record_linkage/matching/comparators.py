"""matching.comparators — comparadores vectorizados por tipo de variable.

Cada clase implementa el protocolo ``Comparator`` (ver ``spec.py``).
Todos los comparadores aceptan dos arrays numpy (lado izquierdo/derecho
de cada par candidato) y retornan un array de similitud por par.

**Garantía de vectorización**: ninguna implementación contiene bucles
Python sobre pares. Todas usan numpy vectorizado, rapidfuzz.process.cpdist
(C++), o pandas string accessors. Verificable con ``pytest -k
test_comparators_no_python_loops``.

Catálogo:

Identificadores
    - ``ExactWithDV``: NIT con tolerancia al dígito de verificación.
    - ``ExactSigned``: comparación exacta firmada (case-sensitive).
    - ``CategoricalSigned``: igual pero case-insensitive.

Nombres
    - ``JaroWinklerSigned``: óptimo para razones sociales (mejor que
      token_set_ratio para detectar diferencias de prefijo).
    - ``TokenSetSigned``: para nombres con orden variable.
    - ``TokenSortSigned``: penaliza orden distinto, premia mismos tokens.

Contacto
    - ``PhoneLastDigits``: últimos N dígitos (resuelve prefijo país).
    - ``EmailDomainLocal``: dominio exacto + similitud local-part.

Geo y dirección
    - ``CityNormalizedEqual``: ciudad exacta tras normalización ASCII/upper.
    - ``GeoHaversine``: distancia haversine con radio (arrays (n,2) lat/lon).

Fecha y numérico (F2.2)
    - ``FechaDelta``: delta en días con tolerancia, firmado.
    - ``NumericoRelativo``: diferencia relativa con tolerancia.
    - ``AddressTokenSet``: tokens normalizados con stop-words de direcciones.

Combinador y helpers
    - ``ExactOrZero``: 1 si iguales no-nulos, 0 si no. Para presencia/ausencia.
"""

from __future__ import annotations

import re
import unicodedata

import numpy as np
import pandas as pd

try:
    from rapidfuzz import fuzz, process as rf_process
    from rapidfuzz.distance import JaroWinkler
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "rapidfuzz es requerido para record_linkage.matching.comparators. "
        "Instalar con `pip install rapidfuzz`."
    ) from exc


# =============================================================================
# Helpers internos (no exportados)
# =============================================================================

_INVALID_VALUES = frozenset(["", "NAN", "NONE", "NULL", "NA", "<NA>", "NAT", "INVALID", "0", "00"])

#: Hilos para rapidfuzz.process.cpdist. -1 = todos los núcleos (v0.12.0):
#: rapidfuzz libera el GIL en C++, así que en Colab Free (2 vCPU) esto
#: aprovecha ambos núcleos sin cambiar un solo valor de similitud.
_CPDIST_WORKERS = -1


def _to_clean_str_array(arr: np.ndarray, *, upper: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Convierte un array a strings limpios + máscara de validez.

    Vectorizado vía pandas. Retorna ``(values, valid_mask)``.

    Args:
        arr: Array de valores (puede contener NaN, None, strings).
        upper: Si True, normaliza a mayúsculas. Default True.

    Returns:
        Tupla ``(strings_limpios, mascara_validez)``. Los inválidos quedan
        como string vacío en ``strings_limpios``.
    """
    s = _a_texto(arr).str.strip()
    if upper:
        s = s.str.upper()
    valid = ~s.isin(_INVALID_VALUES) & s.notna()
    return s.to_numpy(), valid.to_numpy()


def _a_texto(arr: np.ndarray) -> pd.Series:
    """Serie de cadenas REALES, sin ausentes disfrazados de float.

    ``pd.Series(arr).astype(str)`` dejó de ser seguro en pandas 3.0: los
    ausentes ya no se convierten en la cadena 'nan', se quedan como ``NaN``
    flotante. Cualquier ``.map`` posterior recibe entonces un float y revienta
    con ``normalize() argument 2 must be str, not float`` — sobre datos
    reales, en cuanto una columna de texto tenga una celda vacía. El defecto
    lo destapó la prueba de propiedades al alcanzar los comparadores nuevos a
    través del puente de tipos.

    Args:
        arr: valores tal como llegan de la columna.

    Returns:
        Serie de dtype object donde todo elemento es ``str``; los ausentes son
        cadena vacía, que es lo que los comparadores ya tratan como faltante.
    """
    serie = pd.Series(arr)
    return serie.astype("string").fillna("").astype(object)


def _normalize_ascii_upper(arr: np.ndarray) -> np.ndarray:
    """Normaliza un array a ASCII mayúsculas (sin acentos), vectorizado.

    Usa unicodedata.normalize NFKD + filter Mn. No es bucle Python "real"
    en el sentido de hot-loop sobre pares — es un solo pase por valor
    único que pandas optimiza.
    """
    s = _a_texto(arr)

    def _strip(x: str) -> str:
        return (
            "".join(c for c in unicodedata.normalize("NFKD", x) if not unicodedata.combining(c))
            .upper()
            .strip()
        )

    return s.map(_strip).to_numpy()


# =============================================================================
# Comparadores: Identificadores
# =============================================================================


def _valid_mask_clean_str(left: np.ndarray, right: np.ndarray, *, upper: bool = True) -> np.ndarray:
    """Máscara de validez compartida: ambos lados no nulos ni placeholder (v0.12.0)."""
    _, vl = _to_clean_str_array(left, upper=upper)
    _, vr = _to_clean_str_array(right, upper=upper)
    return vl & vr


class ExactWithDV:
    """NIT con tolerancia al dígito de verificación.

    Compara como iguales si:
    - Los NITs son idénticos tras normalización, O
    - Los primeros 9 dígitos coinciden (ignora DV cuando difiere uno solo).

    Vectorizado completo. Rango ``[-1, +1]`` (firmado).

    Comportamiento::

        ExactWithDV().compare(["900123456-1"], ["900.123.456-7"])  → [1.0]
        ExactWithDV().compare(["900123456-1"], ["900123457-1"])    → [-1.0]
        ExactWithDV().compare(["900123456-1"], [None])             → [0.0]
    """

    name = "exact_with_dv"
    signed = True

    _NON_DIGIT = re.compile(r"\D+")

    def _strip_to_digits(self, arr: np.ndarray) -> np.ndarray:
        s = _a_texto(arr).str.strip()
        # Eliminar todo no-dígito, vectorizado vía pandas.str.replace (regex en C).
        return s.str.replace(self._NON_DIGIT, "", regex=True).to_numpy()

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde AMBOS lados traen identificador comparable (v0.12.0)."""
        dl = self._strip_to_digits(left)
        dr = self._strip_to_digits(right)
        vl = (pd.Series(dl).str.len() >= 8).to_numpy()
        vr = (pd.Series(dr).str.len() >= 8).to_numpy()
        return vl & vr

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        dl = self._strip_to_digits(left)
        dr = self._strip_to_digits(right)
        valid_l = (pd.Series(dl).str.len() >= 8).to_numpy()
        valid_r = (pd.Series(dr).str.len() >= 8).to_numpy()
        both_valid = valid_l & valid_r

        out = np.zeros(n, dtype=np.float64)
        if not both_valid.any():
            return out

        # Match exacto
        exact = both_valid & (dl == dr)
        out[exact] = 1.0

        # Match por primeros 9 dígitos (NIT base, ignora DV)
        # Solo aplica si len >= 9 en ambos
        base_l = pd.Series(dl).str.slice(0, 9).to_numpy()
        base_r = pd.Series(dr).str.slice(0, 9).to_numpy()
        # Si las bases coinciden y los completos no, es match (DV diferente).
        base_eq = (
            (base_l == base_r)
            & (pd.Series(dl).str.len() >= 9).to_numpy()
            & (pd.Series(dr).str.len() >= 9).to_numpy()
        )
        out[both_valid & base_eq & ~exact] = 1.0

        # Discrepancia
        out[both_valid & ~exact & ~base_eq] = -1.0
        return out


class ExactSigned:
    """Comparación exacta firmada (case-sensitive, sin normalización)."""

    name = "exact_signed"
    signed = True

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos lados son valores reales (v0.12.0)."""
        return _valid_mask_clean_str(left, right, upper=False)

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        sl, vl = _to_clean_str_array(left, upper=False)
        sr, vr = _to_clean_str_array(right, upper=False)
        both = vl & vr
        eq = sl == sr
        out = np.zeros(n, dtype=np.float64)
        out[both & eq] = 1.0
        out[both & ~eq] = -1.0
        return out


class CategoricalSigned:
    """Comparación case-insensitive firmada. Apta para CIUDAD, PAIS."""

    name = "categorical_signed"
    signed = True

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos lados son valores reales (v0.12.0)."""
        return _valid_mask_clean_str(left, right)

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        sl, vl = _to_clean_str_array(left, upper=True)
        sr, vr = _to_clean_str_array(right, upper=True)
        both = vl & vr
        eq = sl == sr
        out = np.zeros(n, dtype=np.float64)
        out[both & eq] = 1.0
        out[both & ~eq] = -1.0
        return out


class ExactOrZero:
    """1 si iguales no-nulos, 0 si no. No firmado. Apta para flags binarios."""

    name = "exact_or_zero"
    signed = False

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos lados son valores reales (v0.12.0)."""
        return _valid_mask_clean_str(left, right)

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        sl, vl = _to_clean_str_array(left, upper=True)
        sr, vr = _to_clean_str_array(right, upper=True)
        return ((vl & vr) & (sl == sr)).astype(np.float64)


# =============================================================================
# Comparadores: Nombres
# =============================================================================


class JaroWinklerSigned:
    """Similitud Jaro-Winkler firmada. ÓPTIMA para razones sociales.

    Jaro-Winkler premia coincidencias al INICIO del string, lo que es
    exactamente lo que queremos en razones sociales (``CONSTRUCTORA
    BOLIVAR S.A.`` vs ``CONSTRUCTORA BOLIVAR``). Detecta mejor que
    ``token_set_ratio`` casos como ``KANGNAM PRIMEINC`` vs ``KANGNAM
    TEXTILE`` (donde token_set_ratio da ~0.71 y JW da ~0.58).

    Vectorizado vía ``rapidfuzz.process.cpdist`` (C++).

    Args:
        prefix_weight: Peso del prefijo común. Default 0.1 (estándar JW).
            Subir a 0.25 si los prefijos identifican bien (corporate names).
    """

    name = "jaro_winkler_signed"
    signed = True

    def __init__(self, prefix_weight: float = 0.1) -> None:
        if not 0.0 <= prefix_weight <= 0.25:
            raise ValueError(f"prefix_weight ∈ [0, 0.25], recibido {prefix_weight}")
        self.prefix_weight = prefix_weight

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos lados son valores reales (v0.12.0)."""
        return _valid_mask_clean_str(left, right)

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        sl, vl = _to_clean_str_array(left, upper=True)
        sr, vr = _to_clean_str_array(right, upper=True)
        both = vl & vr

        # Vectorizado con cpdist por par (no full matrix)
        # cpdist con arrays de igual tamaño hace pair-wise (no cross)
        # v0.12.0: prefix_weight por fin se PASA al scorer (hasta 0.11.x se
        # almacenaba en __init__ y jamás llegaba a cpdist: parámetro muerto).
        scores = rf_process.cpdist(
            sl.tolist(),
            sr.tolist(),
            scorer=JaroWinkler.normalized_similarity,
            dtype=np.float64,
            workers=_CPDIST_WORKERS,
            scorer_kwargs={"prefix_weight": self.prefix_weight},
        )
        # cpdist normalized_similarity ya retorna en [0, 1]
        # Mapear a [-1, +1]
        signed_scores = 2.0 * scores - 1.0
        # Donde no hay validez, retornar 0 (sin información, no penalizar)
        return np.where(both, signed_scores, 0.0)


class TokenSetSigned:
    """Token-set ratio firmado. Apto para nombres con orden de tokens variable."""

    name = "token_set_signed"
    signed = True

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos lados son valores reales (v0.12.0)."""
        return _valid_mask_clean_str(left, right)

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        sl, vl = _to_clean_str_array(left, upper=True)
        sr, vr = _to_clean_str_array(right, upper=True)
        both = vl & vr
        scores = (
            rf_process.cpdist(
                sl.tolist(),
                sr.tolist(),
                scorer=fuzz.token_set_ratio,
                dtype=np.float64,
                workers=_CPDIST_WORKERS,
            )
            / 100.0
        )
        return np.where(both, 2.0 * scores - 1.0, 0.0)


class TokenSortSigned:
    """Token-sort: penaliza ordens distintos, premia mismos tokens."""

    name = "token_sort_signed"
    signed = True

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos lados son valores reales (v0.12.0)."""
        return _valid_mask_clean_str(left, right)

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        sl, vl = _to_clean_str_array(left, upper=True)
        sr, vr = _to_clean_str_array(right, upper=True)
        both = vl & vr
        scores = (
            rf_process.cpdist(
                sl.tolist(),
                sr.tolist(),
                scorer=fuzz.token_sort_ratio,
                dtype=np.float64,
                workers=_CPDIST_WORKERS,
            )
            / 100.0
        )
        return np.where(both, 2.0 * scores - 1.0, 0.0)


# =============================================================================
# Comparadores: Contacto
# =============================================================================


class PhoneLastDigits:
    """Compara últimos N dígitos de teléfono. Resuelve prefijos de país.

    Vectorizado. Rango [-1, +1].

    Args:
        n: Cantidad de dígitos finales a comparar. Default 7 (Colombia
           fijo) — usa 8 para móviles internacionales con código país.

    Comportamiento::

        PhoneLastDigits(7).compare(["+57 301 234 5678"], ["3012345678"])  → [1.0]
        PhoneLastDigits(7).compare(["3012345678"], ["3019876543"])         → [-1.0]
    """

    name = "phone_last_digits"
    signed = True

    _NON_DIGIT = re.compile(r"\D+")

    def __init__(self, n: int = 7) -> None:
        if n < 4 or n > 15:
            raise ValueError(f"n ∈ [4, 15], recibido {n}")
        self.n = n

    def _last_digits(self, arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        s = _a_texto(arr).str.strip()
        digits = s.str.replace(self._NON_DIGIT, "", regex=True)
        valid = (digits.str.len() >= self.n).to_numpy()
        last_n = digits.str.slice(-self.n).to_numpy()
        return last_n, valid

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos lados tienen >= n dígitos (v0.12.0)."""
        _, vl = self._last_digits(left)
        _, vr = self._last_digits(right)
        return vl & vr

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        ll, vl = self._last_digits(left)
        lr, vr = self._last_digits(right)
        both = vl & vr
        eq = ll == lr
        out = np.zeros(n, dtype=np.float64)
        out[both & eq] = 1.0
        out[both & ~eq] = -1.0
        return out


class EmailDomainLocal:
    """Compara emails: dominio (peso fuerte) + local-part (peso suave).

    Vectorizado. Rango [-1, +1].

    Comportamiento::

        EmailDomainLocal().compare(
            ["jp@empresa.com"], ["juan.perez@empresa.com"]
        )  → [0.65]  # mismo dominio + local similar

        EmailDomainLocal().compare(
            ["jp@empresa.com"], ["jp@otra.com"]
        )  → [-0.30]  # dominio distinto pero local idéntico (sospechoso)

        EmailDomainLocal().compare(
            ["jp@empresa.com"], ["maria@otra.com"]
        )  → [-1.0]  # nada en común

    Args:
        domain_weight: Peso del dominio en el score. Default 0.7.
        free_email_domains: Dominios de email genéricos que NO deben dar
            evidencia fuerte (gmail, yahoo, hotmail). Default incluye los
            más comunes. Cuando ambos emails están en estos dominios, el
            match de dominio NO suma evidencia.
    """

    name = "email_domain_local"
    signed = True

    _DEFAULT_FREE = frozenset(
        [
            "GMAIL.COM",
            "HOTMAIL.COM",
            "YAHOO.COM",
            "OUTLOOK.COM",
            "LIVE.COM",
            "MSN.COM",
            "ICLOUD.COM",
            "AOL.COM",
            "PROTONMAIL.COM",
        ]
    )

    def __init__(
        self,
        domain_weight: float = 0.7,
        free_email_domains: frozenset[str] | None = None,
    ) -> None:
        if not 0.0 <= domain_weight <= 1.0:
            raise ValueError(f"domain_weight ∈ [0, 1], recibido {domain_weight}")
        self.domain_weight = domain_weight
        self.local_weight = 1.0 - domain_weight
        self.free_email_domains = (
            frozenset(d.upper() for d in free_email_domains)
            if free_email_domains is not None
            else self._DEFAULT_FREE
        )

    def _split(self, arr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        s = _a_texto(arr).str.strip().str.upper()
        # Validación: tiene exactamente un '@' y al menos un punto en dominio
        parts = s.str.split("@", n=1, expand=True)
        if parts.shape[1] < 2:
            parts[1] = ""
        local = parts[0].fillna("")
        domain = parts[1].fillna("")
        valid = (local.str.len() > 0) & domain.str.contains(".", regex=False, na=False)
        return local.to_numpy(), domain.to_numpy(), valid.to_numpy()

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos lados parecen emails (local@dominio.tld) (v0.12.0)."""
        _, _, vl = self._split(left)
        _, _, vr = self._split(right)
        return vl & vr

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        ll, dl, vl = self._split(left)
        lr, dr, vr = self._split(right)
        both = vl & vr

        out = np.zeros(n, dtype=np.float64)
        if not both.any():
            return out

        # Componente dominio (firmado)
        domain_eq = dl == dr
        is_free_l = pd.Series(dl).isin(self.free_email_domains).to_numpy()
        is_free_r = pd.Series(dr).isin(self.free_email_domains).to_numpy()
        both_free = is_free_l & is_free_r

        # Dominio iguales NO-free → fuerte +
        # Dominio iguales free → neutral (gmail==gmail no es evidencia)
        # Dominio distintos → -
        domain_component = np.zeros(n, dtype=np.float64)
        domain_component[both & domain_eq & ~both_free] = 1.0
        domain_component[both & domain_eq & both_free] = 0.2  # leve, no fuerte
        domain_component[both & ~domain_eq] = -1.0

        # Componente local (firmado, similitud JW del local-part)
        # Solo calculamos donde both es True
        if both.any():
            idx = np.where(both)[0]
            local_scores_raw = rf_process.cpdist(
                ll[idx].tolist(),
                lr[idx].tolist(),
                scorer=JaroWinkler.normalized_similarity,
                dtype=np.float64,
                workers=_CPDIST_WORKERS,
            )
            local_component = np.zeros(n, dtype=np.float64)
            local_component[idx] = 2.0 * local_scores_raw - 1.0
        else:
            local_component = np.zeros(n, dtype=np.float64)

        out = self.domain_weight * domain_component + self.local_weight * local_component
        # Donde no both, ya está en 0
        out[~both] = 0.0
        return np.clip(out, -1.0, 1.0)


# =============================================================================
# Comparadores: Geo y dirección
# =============================================================================


class CityNormalizedEqual:
    """Ciudad: igualdad exacta tras normalización ASCII/upper/trim.

    NO usa similitud fuzzy: ``BOGOTÁ`` y ``BOGOTÁ D.C.`` no son la misma
    ciudad para efectos de geo-blocking conservador. Si quieres tolerar
    estos casos, normalizar primero el DataFrame ANTES de pasar al matcher.
    """

    name = "city_normalized_equal"
    signed = True

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos lados tienen ciudad no vacía (v0.12.0)."""
        vl = (pd.Series(_normalize_ascii_upper(left)).str.len() > 0).to_numpy()
        vr = (pd.Series(_normalize_ascii_upper(right)).str.len() > 0).to_numpy()
        return vl & vr

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        nl = _normalize_ascii_upper(left)
        nr = _normalize_ascii_upper(right)
        vl = (pd.Series(nl).str.len() > 0).to_numpy()
        vr = (pd.Series(nr).str.len() > 0).to_numpy()
        both = vl & vr
        eq = nl == nr
        out = np.zeros(n, dtype=np.float64)
        out[both & eq] = 1.0
        out[both & ~eq] = -1.0
        return out


class AddressTokenSet:
    """Dirección por tokens normalizados, ignorando stop-words.

    Las direcciones tienen mucha variabilidad sintáctica (``CRA``, ``CARRERA``,
    ``CR.``, ``KR``). Este comparador:

    1. Normaliza ASCII/upper.
    2. Reemplaza abreviaturas comunes (``CRA → CARRERA``, ``CLL → CALLE``,
       ``KR → CARRERA``, ``AV → AVENIDA``).
    3. Compara con ``token_set_ratio`` post-normalización.

    Rango [-1, +1].
    """

    name = "address_token_set"
    signed = True

    _ABBREV: dict[str, str] = {
        " CRA ": " CARRERA ",
        " CR ": " CARRERA ",
        " KR ": " CARRERA ",
        " CLL ": " CALLE ",
        " CL ": " CALLE ",
        " AV ": " AVENIDA ",
        " AVE ": " AVENIDA ",
        " DG ": " DIAGONAL ",
        " TV ": " TRANSVERSAL ",
        " TRANS ": " TRANSVERSAL ",
        " AC ": " AVENIDA CALLE ",
        " AK ": " AVENIDA CARRERA ",
        " NO ": " ",
        " NRO ": " ",
        " N° ": " ",
        " # ": " ",
    }
    # Compilamos un único regex grande para single-pass
    _SUB_RE: re.Pattern[str] | None = None

    @classmethod
    def _compile_re(cls) -> re.Pattern[str]:
        if cls._SUB_RE is None:
            pattern = "|".join(re.escape(k) for k in cls._ABBREV)
            cls._SUB_RE = re.compile(pattern)
        return cls._SUB_RE

    def _normalize(self, arr: np.ndarray) -> np.ndarray:
        s = _normalize_ascii_upper(arr)
        ss = _a_texto(s)
        # v0.20.0 — La puntuación ENTRE DOS DÍGITOS separa números, no los
        # une. Medido: 'CRA 7 # 71-21' contra 'CARRERA 7 NO 71 21' daba 0,600
        # porque '71-21' quedaba como UN token y '71' '21' como dos; con la
        # separación da 1,000. La placa se escribe indistintamente '71-21',
        # '71/21' o '71.21' y las tres son la misma dirección.
        #
        # Solo entre dígitos, a propósito. Borrar toda la puntuación también
        # eliminaría los guiones sueltos de 'CALLE 11 # 2 - 11', que hoy
        # cuentan como un token compartido entre dos direcciones iguales;
        # medido sobre el GT sintético v3, hacerlo baja el recall de 0,8940 a
        # 0,8808 sin arreglar ningún caso. Se corrige el defecto y nada más.
        ss = ss.str.replace(r"(?<=\d)[-/.](?=\d)", " ", regex=True)
        # Padding para que la regex matchee al inicio/fin
        padded = " " + ss + " "
        regex = self._compile_re()
        replaced = padded.map(lambda x: regex.sub(lambda m: self._ABBREV[m.group(0)], x))
        # Colapsar espacios
        collapsed = replaced.str.replace(r"\s+", " ", regex=True).str.strip()
        return collapsed.to_numpy()

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambas direcciones normalizadas tienen >= 5 chars (v0.12.0)."""
        vl = (pd.Series(self._normalize(left)).str.len() >= 5).to_numpy()
        vr = (pd.Series(self._normalize(right)).str.len() >= 5).to_numpy()
        return vl & vr

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        nl = self._normalize(left)
        nr = self._normalize(right)
        vl = (pd.Series(nl).str.len() >= 5).to_numpy()
        vr = (pd.Series(nr).str.len() >= 5).to_numpy()
        both = vl & vr
        scores = (
            rf_process.cpdist(
                nl.tolist(),
                nr.tolist(),
                scorer=fuzz.token_set_ratio,
                dtype=np.float64,
                workers=_CPDIST_WORKERS,
            )
            / 100.0
        )
        return np.where(both, 2.0 * scores - 1.0, 0.0)


class FechaDelta:
    """Fecha con tolerancia en días (F2.2). Rango ``[-1, +1]`` (firmado).

    Espera fechas normalizadas a ISO 'YYYY-MM-DD' ('' = faltante, ver
    ``normalizadores.normalizar_fecha``). Similitud lineal::

        delta = |a - b| en días
        sim   = clip(1 - delta/tolerancia, -1, +1)

    → delta 0 → +1.0 · delta = tolerancia → 0.0 · delta ≥ 2·tolerancia → −1.0
    Faltante o inválida en cualquier lado → 0.0 (neutro; salvaguarda F2.4).
    """

    signed = True

    def __init__(self, dias_tolerancia: int = 30) -> None:
        if dias_tolerancia < 1:
            raise ValueError(f"dias_tolerancia={dias_tolerancia} debe ser >= 1.")
        self.dias_tolerancia = int(dias_tolerancia)
        self.name = f"fecha_delta_{self.dias_tolerancia}d"

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambas fechas parsean (v0.12.0)."""
        dl = pd.to_datetime(pd.Series(left), errors="coerce")
        dr = pd.to_datetime(pd.Series(right), errors="coerce")
        return (dl.notna() & dr.notna()).to_numpy()

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        dl = pd.to_datetime(pd.Series(left), errors="coerce")
        dr = pd.to_datetime(pd.Series(right), errors="coerce")
        ok = (dl.notna() & dr.notna()).to_numpy()
        out = np.zeros(n, dtype=np.float64)
        if not ok.any():
            return out
        delta = (dl - dr).dt.days.abs().to_numpy(dtype="float64")
        sim = np.clip(1.0 - delta / float(self.dias_tolerancia), -1.0, 1.0)
        out[ok] = sim[ok]
        return out


class GeoHaversine:
    """Geolocalización por distancia haversine con radio (F2.2). Rango ``[0, 1]``.

    CONTRATO DE FORMA: ``left`` y ``right`` son arrays float de forma
    ``(n, 2)`` con columnas ``[lat, lon]`` (ver ``normalizadores.
    normalizar_geo``). NaN en cualquier coordenada → 0.0 (faltante neutro).

    Similitud lineal: ``sim = clip(1 - dist_km/radio_km, 0, 1)`` — a 0 km es
    1.0 y a partir del radio es 0.0. No firmado a propósito: estar lejos no
    debe vetar por sí solo (dos sedes de la misma empresa pueden distar km).
    """

    signed = False

    _R_TIERRA_KM = 6371.0088

    def __init__(self, radio_km: float = 1.0) -> None:
        if radio_km <= 0:
            raise ValueError(f"radio_km={radio_km} debe ser > 0.")
        self.radio_km = float(radio_km)
        self.name = f"geo_haversine_{self.radio_km:g}km"

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambas coordenadas (lat, lon) son numéricas (v0.12.0)."""
        la = np.asarray(left, dtype=np.float64)
        ra = np.asarray(right, dtype=np.float64)
        return ~np.isnan(la).any(axis=1) & ~np.isnan(ra).any(axis=1)

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        la = np.asarray(left, dtype=np.float64)
        ra = np.asarray(right, dtype=np.float64)
        if la.ndim != 2 or la.shape[1] != 2 or ra.shape != la.shape:
            raise ValueError(
                f"GeoHaversine espera arrays (n, 2) [lat, lon]; recibió {la.shape} y {ra.shape}."
            )
        n = la.shape[0]
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        ok = ~np.isnan(la).any(axis=1) & ~np.isnan(ra).any(axis=1)
        out = np.zeros(n, dtype=np.float64)
        if not ok.any():
            return out
        lat1, lon1 = np.radians(la[:, 0]), np.radians(la[:, 1])
        lat2, lon2 = np.radians(ra[:, 0]), np.radians(ra[:, 1])
        dlat, dlon = lat2 - lat1, lon2 - lon1
        h = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
        dist = 2.0 * self._R_TIERRA_KM * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0)))
        sim = np.clip(1.0 - dist / self.radio_km, 0.0, 1.0)
        out[ok] = sim[ok]
        return out


def _numeros_finitos(valores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Convierte a float y marca solo los valores REALES y finitos.

    ``inf``, ``-inf``, ``nan`` y los desbordes de float64 (``"1e400"``) no son
    magnitudes comparables: restarlos produce ``nan`` y ese ``nan`` se propaga
    en silencio al score combinado hasta invalidar la decisión del par. Aquí se
    tratan como FALTANTE, que es lo único defendible.

    Defecto encontrado por property-based testing (v0.14.0) con el ejemplo
    ``"INF" vs "INF"``: afectaba a NumericoRelativo desde su introducción.

    Args:
        valores: arreglo con los valores tal como llegan del campo.

    Returns:
        Tupla ``(valores_float, mascara_valida)``.
    """
    serie = pd.to_numeric(pd.Series(valores), errors="coerce")
    arreglo = serie.to_numpy(dtype="float64", na_value=np.nan)
    return arreglo, np.isfinite(arreglo)


class NumericoRelativo:
    """Numérico por diferencia relativa con tolerancia (F2.2). Rango ``[0, 1]``.

    ``rel = |a − b| / max(|a|, |b|)``; ``sim = clip(1 − rel/tolerancia, 0, 1)``.
    Ambos exactamente cero → 1.0. No numérico o faltante → 0.0 (neutro).
    Acepta strings canónicos de ``normalizadores.normalizar_numero`` o floats.
    """

    signed = False

    def __init__(self, tolerancia: float = 0.10) -> None:
        if not (0.0 < tolerancia <= 10.0):
            raise ValueError(f"tolerancia={tolerancia} fuera de (0, 10].")
        self.tolerancia = float(tolerancia)
        self.name = f"numerico_rel_{self.tolerancia:g}"

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos lados son numéricos (v0.12.0). Un score de 0.0 en
        este comparador significa "muy distinto", NO "faltante": sin esta
        máscara, el combinador lo excluía de la masa efectiva e inflaba el
        score normalizado (hallazgo C5 de la auditoría)."""
        _, ok_a = _numeros_finitos(left)
        _, ok_b = _numeros_finitos(right)
        return ok_a & ok_b

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        av, ok_a = _numeros_finitos(left)
        bv, ok_b = _numeros_finitos(right)
        ok = ok_a & ok_b
        # Los no finitos ya quedaron fuera de `ok`; se neutralizan sus valores
        # para que ninguna operación vectorizada genere nan aguas abajo.
        av = np.where(ok, av, 0.0)
        bv = np.where(ok, bv, 0.0)
        out = np.zeros(n, dtype=np.float64)
        if not ok.any():
            return out
        denom = np.maximum(np.abs(av), np.abs(bv))
        ambos_cero = ok & (denom == 0.0)
        con_denom = ok & (denom > 0.0)
        rel = np.zeros(n, dtype=np.float64)
        rel[con_denom] = np.abs(av[con_denom] - bv[con_denom]) / denom[con_denom]
        sim = np.clip(1.0 - rel / self.tolerancia, 0.0, 1.0)
        out[con_denom] = sim[con_denom]
        out[ambos_cero] = 1.0
        return out


# Comparadores v0.13.0: edición, fonética ES, jerárquico, conjunto, absoluto
# =============================================================================


class LevenshteinSigned:
    """Distancia de edición normalizada, firmada. Para typos carácter a carácter.

    Complementa a Jaro-Winkler: JW premia prefijos comunes (razones sociales);
    Levenshtein mide ediciones en CUALQUIER posición — mejor para códigos,
    referencias y nombres cortos con un dígito o letra cambiada.

    Vectorizado vía ``rapidfuzz.process.cpdist`` (C++). Rango ``[-1, +1]``.
    """

    name = "levenshtein_signed"
    signed = True

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos lados son valores reales (v0.13.0)."""
        return _valid_mask_clean_str(left, right)

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        from rapidfuzz.distance import Levenshtein

        sl, vl = _to_clean_str_array(left, upper=True)
        sr, vr = _to_clean_str_array(right, upper=True)
        both = vl & vr
        scores = rf_process.cpdist(
            sl.tolist(),
            sr.tolist(),
            scorer=Levenshtein.normalized_similarity,
            dtype=np.float64,
            workers=_CPDIST_WORKERS,
        )
        return np.where(both, 2.0 * scores - 1.0, 0.0)


#: Reglas de la clave fonética española (aplicadas EN ORDEN, sobre ASCII/upper).
#: Colapsan las confusiones ortográficas típicas del español: B/V, C/S/Z ante
#: e-i, QU/K/C fuerte, G/J ante e-i, LL/Y, H muda, X/S inicial, W→GU.
_FONETICA_ES_REGLAS: list[tuple[str, str]] = [
    (r"H", ""),  # H muda (tras ASCII/upper; CH ya quedó como C+H → "C")
    (r"V", "B"),  # B/V indistinguibles
    (r"Z", "S"),  # seseo
    (r"C(?=[EI])", "S"),  # CE/CI → SE/SI
    (r"QU", "K"),  # QUE/QUI → KE/KI
    (r"C", "K"),  # C fuerte restante → K
    (r"G(?=[EI])", "J"),  # GE/GI → JE/JI
    (r"GU(?=[EI])", "G"),  # GUE/GUI → GE/GI (tras la regla anterior no aplica: orden)
    (r"LL", "Y"),  # yeísmo
    (r"W", "GU"),  # préstamos
    (r"X", "KS"),  # aproximación estable
    (r"(.)\1+", r"\1"),  # colapsar letras repetidas
]


_FONETICA_ES_COMPILADAS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(patron), reemplazo) for patron, reemplazo in _FONETICA_ES_REGLAS
]


def _aplicar_reglas_foneticas(texto: str) -> str:
    """Aplica las reglas fonéticas ES a UN valor (llamada por único, no por fila)."""
    for rx, reemplazo in _FONETICA_ES_COMPILADAS:
        texto = rx.sub(reemplazo, texto)
    return " ".join(texto.split())


def clave_fonetica_es(serie: pd.Series) -> pd.Series:
    """Clave fonética ESPAÑOLA vectorizada (v0.13.0).

    Sustituye la pseudo-fonética histórica del paquete ("consonantes +
    iniciales", ``engine.similarity.phonetic_keys``) por reglas reales de
    confusión ortográfica del español. No es Metaphone completo: es un
    colapso determinista y auditable de las equivalencias que SÍ producen
    duplicados en razones sociales colombianas (B/V, S/Z/C, G/J, LL/Y, H).

    Vectorizado: las reglas se aplican con ``str.replace`` (regex en C) sobre
    la serie completa; sin bucles Python por fila.
    """
    s = serie.astype("string").fillna("")
    s = (
        s.str.normalize("NFKD")
        .str.encode("ascii", errors="ignore")
        .str.decode("ascii")
        .str.upper()
        .str.replace(r"[^A-Z ]", "", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )
    # Las reglas usan lookahead y backreference, que el backend Arrow (RE2)
    # de pandas NO soporta. Se aplican con el motor `re` de Python sobre los
    # VALORES ÚNICOS (mismo patrón de TextProcessor: en corpus de razones
    # sociales la repetición es alta y cada único se procesa una sola vez).
    unicos = s.unique()
    mapa = {valor: _aplicar_reglas_foneticas(valor) for valor in unicos}
    return s.map(mapa).astype("string")


class FoneticoEspanolSigned:
    """Igualdad de clave fonética española por token-set, firmada.

    Dos nombres concuerdan si sus claves fonéticas (ver
    ``clave_fonetica_es``) coinciden como CONJUNTO de tokens — robusto a
    orden de palabras y a las confusiones B/V, S/Z/C, G/J, LL/Y, H.

    "BAZQUEZ E HIJOS" ≈ "VASQUES E HIJOS". Rango ``[-1, +1]``.

    CUÁNDO USARLO (medido, v0.13.0): como campo PARALELO al nombre con peso
    propio REDUCE la precisión (corpus sintético SIN_NIT: F1 0.987→0.927;
    incluso con 70% de errores ortográficos: 0.990→0.960), porque Jaro-Winkler
    ya absorbe la mayoría de variantes fonéticas y el campo extra solo suma
    score a falsos positivos cercanos. Úselo (a) como comparador PRINCIPAL
    del nombre en datos con ortografía muy degradada, o (b) su clave
    (``clave_fonetica_es``) como columna para ``LlaveExacta`` de bloqueo.
    Reproducir: scripts/medir_calidad_sintetica.py.
    """

    name = "fonetico_es_signed"
    signed = True

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True solo si AMBOS lados producen clave fonética no vacía (v0.13.0).

        Un valor sin letras ("123", "-") no tiene información fonética: es
        FALTANTE para este comparador, no discrepancia. Caso atrapado por
        property-based testing antes de publicar (identidad sobre "1" daba
        −1.0 con la validez ingenua).
        """
        base = _valid_mask_clean_str(left, right)
        kl = clave_fonetica_es(pd.Series(left))
        kr = clave_fonetica_es(pd.Series(right))
        con_clave = (kl.str.len() > 0).to_numpy() & (kr.str.len() > 0).to_numpy()
        return base & con_clave

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        both = self.valid_mask(left, right)
        kl = clave_fonetica_es(pd.Series(left))
        kr = clave_fonetica_es(pd.Series(right))
        # Token-set: mismo conjunto fonético sin importar el orden.
        tl = kl.str.split().map(frozenset)
        tr = kr.str.split().map(frozenset)
        eq = (tl == tr).to_numpy()
        out = np.zeros(n, dtype=np.float64)
        out[both & eq] = 1.0
        out[both & ~eq] = -1.0
        return out


class JerarquicoPrefijo:
    """Códigos jerárquicos (CIIU, HS/subpartida, geografía DANE) por niveles.

    La similitud es la fracción de NIVELES compartidos desde la raíz:
    con ``niveles=(2, 4, 6)``, dos códigos que comparten los primeros 4
    dígitos pero no 6 dan 2/3. Coincidencia total → 1.0; ni el primer
    nivel → -1.0 (firmado: ramas distintas son evidencia en contra).

    Args:
        niveles: cortes jerárquicos en Nº de caracteres, crecientes.
            Default (2, 4, 6): capítulo/partida/subpartida estilo HS-CIIU.
    """

    signed = True

    def __init__(self, niveles: tuple[int, ...] = (2, 4, 6)) -> None:
        if not niveles or list(niveles) != sorted(set(int(x) for x in niveles)):
            raise ValueError(f"niveles={niveles!r} debe ser una tupla creciente no vacía.")
        self.niveles = tuple(int(x) for x in niveles)
        self.name = f"jerarquico_prefijo_{'_'.join(map(str, self.niveles))}"

    def _preparar(self, arr: np.ndarray) -> tuple[pd.Series, np.ndarray]:
        s = pd.Series(arr).astype("string").fillna("").str.strip().str.upper()
        s = s.str.replace(r"[^A-Z0-9]", "", regex=True)
        valida = (s.str.len() >= self.niveles[0]).to_numpy()
        return s, valida

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos códigos alcanzan el primer nivel (v0.13.0)."""
        _, vl = self._preparar(left)
        _, vr = self._preparar(right)
        return vl & vr

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        sl, vl = self._preparar(left)
        sr, vr = self._preparar(right)
        both = vl & vr
        len_l = sl.str.len().to_numpy()
        len_r = sr.str.len().to_numpy()
        coincidencias = np.zeros(n, dtype=np.float64)
        cubiertos = np.zeros(n, dtype=np.float64)
        for nivel in self.niveles:
            pl = sl.str.slice(0, nivel)
            pr = sr.str.slice(0, nivel)
            ok = ((pl == pr) & (len_l >= nivel) & (len_r >= nivel)).to_numpy()
            coincidencias += ok.astype(np.float64)
            # Denominador: niveles que el código MÁS PROFUNDO del par cubre.
            # Así la identidad de códigos cortos da 1.0 (propiedad atrapada
            # por hypothesis: "00" vs "00" daba 1/3 con denominador fijo), y
            # un código más específico vs su padre da fracción < 1 (honesto).
            cubiertos += (np.maximum(len_l, len_r) >= nivel).astype(np.float64)
        cubiertos = np.maximum(cubiertos, 1.0)
        frac = coincidencias / cubiertos
        out = np.zeros(n, dtype=np.float64)
        out[both] = np.where(frac[both] > 0.0, frac[both], -1.0)
        return out


class ConjuntoJaccard:
    """Campos multi-valor ("A;B;C") por similitud de Jaccard, no firmado.

    Para listas de actividades, marcas, países de operación, etc. Separador
    configurable. Vectorizado con matrices dispersas (scipy): factoriza los
    tokens de ambos lados y calcula |A∩B| / |A∪B| por par sin bucles Python
    sobre pares. Rango ``[0, 1]`` (compartir poco no es evidencia en contra).

    Args:
        separador: separador de valores dentro de la celda. Default ";".
    """

    signed = False

    def __init__(self, separador: str = ";") -> None:
        if not separador:
            raise ValueError("separador no puede ser vacío.")
        self.separador = separador
        self.name = f"conjunto_jaccard[{separador!r}]"

    def _tokens(self, arr: np.ndarray) -> pd.Series:
        s = pd.Series(arr).astype("string").fillna("").str.upper()
        return s.str.split(self.separador).map(
            lambda xs: (
                frozenset(t.strip() for t in xs if t and t.strip())
                if isinstance(xs, list)
                else frozenset()
            )
        )

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos lados tienen al menos un token (v0.13.0)."""
        tl = self._tokens(left).map(len).to_numpy() > 0
        tr = self._tokens(right).map(len).to_numpy() > 0
        return tl & tr

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        from scipy.sparse import csr_matrix

        tl = self._tokens(left)
        tr = self._tokens(right)

        # Vocabulario conjunto → matrices dispersas (n, |V|) de pertenencia.
        vocab: dict[str, int] = {}

        def _fila(tokens: frozenset) -> list[int]:
            cols = []
            for t in tokens:
                j = vocab.setdefault(t, len(vocab))
                cols.append(j)
            return cols

        cols_l = tl.map(_fila)
        cols_r = tr.map(_fila)

        def _csr(cols_series: pd.Series) -> csr_matrix:
            lens = cols_series.map(len).to_numpy()
            indptr = np.concatenate(([0], np.cumsum(lens)))
            indices = np.fromiter(
                (j for cols in cols_series for j in cols), dtype=np.int64, count=int(lens.sum())
            )
            data = np.ones(len(indices), dtype=np.int8)
            return csr_matrix((data, indices, indptr), shape=(n, max(1, len(vocab))))

        ml, mr = _csr(cols_l), _csr(cols_r)
        inter = np.asarray(ml.multiply(mr).sum(axis=1)).ravel().astype(np.float64)
        tam_l = np.asarray(ml.sum(axis=1)).ravel().astype(np.float64)
        tam_r = np.asarray(mr.sum(axis=1)).ravel().astype(np.float64)
        union = tam_l + tam_r - inter
        out = np.zeros(n, dtype=np.float64)
        con_union = union > 0
        out[con_union] = inter[con_union] / union[con_union]
        return out


class NumericoAbsoluto:
    """Numérico por diferencia ABSOLUTA con tolerancia (v0.13.0). Rango [0, 1].

    Complementa a ``NumericoRelativo``: la tolerancia relativa distorsiona
    cerca de cero (|10-11|/11 ≈ 9% pero |1000010-1000011| ≈ 0.0001%). Para
    magnitudes con unidad fija (empleados, año, área), la diferencia absoluta
    con tolerancia es el contrato correcto:
    ``sim = clip(1 − |a − b| / tolerancia, 0, 1)``.
    """

    signed = False

    def __init__(self, tolerancia: float = 1.0) -> None:
        if not (tolerancia > 0):
            raise ValueError(f"tolerancia={tolerancia} debe ser > 0.")
        self.tolerancia = float(tolerancia)
        self.name = f"numerico_abs_{self.tolerancia:g}"

    def valid_mask(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        """True donde ambos lados son numéricos (v0.13.0)."""
        _, ok_a = _numeros_finitos(left)
        _, ok_b = _numeros_finitos(right)
        return ok_a & ok_b

    def compare(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        n = len(left)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        av, ok_a = _numeros_finitos(left)
        bv, ok_b = _numeros_finitos(right)
        ok = ok_a & ok_b
        # Los no finitos ya quedaron fuera de `ok`; se neutralizan sus valores
        # para que ninguna operación vectorizada genere nan aguas abajo.
        av = np.where(ok, av, 0.0)
        bv = np.where(ok, bv, 0.0)
        out = np.zeros(n, dtype=np.float64)
        if not ok.any():
            return out
        sim = np.clip(1.0 - np.abs(av - bv) / self.tolerancia, 0.0, 1.0)
        out[ok] = sim[ok]
        return out
