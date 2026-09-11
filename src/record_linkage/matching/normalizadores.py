"""matching.normalizadores — normalización por tipo con diccionarios por locale.

Sustituye la inferencia de genéricos por frecuencia de corpus
(``remove_top_words``, que dejó al régimen SIN_NIT en el filo de percolación,
ver diagnóstico 0.7.6) por **diccionarios declarados por locale**: sufijos
legales y términos genéricos son datos versionados y auditables, no un
artefacto del dataset de turno.

Diseño (F2.3):
    - Toda función es vectorizada (``pd.Series`` → ``pd.Series``); sin bucles
      Python por fila.
    - Quitar genéricos es OPT-IN y con piso: nunca deja un nombre por debajo
      de ``min_tokens`` tokens (el sobre-borrado fue la causa mecánica de la
      percolación SIN_NIT).
    - Placeholders ("SIN DATO", "0", "N/A", …) se normalizan a faltante
      (cadena vacía) ANTES de comparar: la salvaguarda de F2.4 depende de
      esto (ningún override opera sobre faltantes).
    - Locales: ES completo (reusa los 152 sufijos legales validados del
      paquete), EN funcional, KR semilla declarada. Extensible por dict.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from ..processing._constants import LEGAL_SUFFIXES

if TYPE_CHECKING:  # pragma: no cover - solo tipado
    from .campos import CampoSpec

# ═══════════════════════════════════════════════════════════════════════════
# Placeholders → faltante
# ═══════════════════════════════════════════════════════════════════════════

#: Valores que representan "no hay dato" y deben tratarse como faltante.
#: La salvaguarda de F2.4 (ningún override sobre faltantes) parte de aquí.
PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "",
        "0",
        "00",
        "000",
        "N/A",
        "NA",
        "N.A.",
        "NONE",
        "NULL",
        "NAN",
        "SIN DATO",
        "SIN DATOS",
        "SIN INFORMACION",
        # ── Añadidos en 0.22.4, medidos sobre la vista snowflake_v2 de
        # importadores (355.681 filas): "NO DISPONIBLE" son 69 filas —una por
        # país— con el 21,7 % del FOB; sin esto se convertía en "el importador
        # más grande" de cada país. "A LA ORDEN" / "TO ORDER" / "TO THE ORDER"
        # son el consignatario genérico del conocimiento de embarque, no una
        # empresa (100 + 69 + 30 filas; también 204 en la base DIAN de
        # referencia). "CONFIDENCIAL" y "RESERVADO" son los marcadores de
        # destinatario reservado que el notebook 07 ya trataba como centinela.
        # Solo coincidencias EXACTAS: "TO ORDER OF ING BELGIUM" nombra a la
        # parte y se conserva.
        "NO DISPONIBLE",
        "A LA ORDEN",
        "TO ORDER",
        "TO THE ORDER",
        "CONFIDENCIAL",
        "RESERVADO",
        "SIN INFORMACIÓN",
        "NO REGISTRA",
        "NO APLICA",
        "NO REPORTA",
        "-",
        "--",
        ".",
        "S/D",
        "SD",
        "X",
        "XX",
        "XXX",
        "PENDIENTE",
        "DESCONOCIDO",
    }
)


def es_faltante(serie: pd.Series) -> np.ndarray:
    """Máscara booleana: True donde el valor es null o placeholder.

    Args:
        serie: valores crudos o normalizados.

    Returns:
        ``np.ndarray[bool]`` por posición.
    """
    s = serie.astype("string")
    limpio = s.str.strip().str.upper()
    return (s.isna() | limpio.isin(PLACEHOLDERS)).to_numpy()


# ═══════════════════════════════════════════════════════════════════════════
# Diccionarios por locale (DECLARADOS, no inferidos)
# ═══════════════════════════════════════════════════════════════════════════

_GENERICOS_ES: frozenset[str] = frozenset(
    {
        "INVERSIONES",
        "COMERCIALIZADORA",
        "DISTRIBUIDORA",
        "DISTRIBUCIONES",
        "IMPORTADORA",
        "EXPORTADORA",
        "REPRESENTACIONES",
        "SERVICIOS",
        "SOLUCIONES",
        "CONSULTORES",
        "CONSULTORIA",
        "ASESORIAS",
        "GRUPO",
        "INTERNACIONAL",
        "NACIONAL",
        "COLOMBIA",
        "COLOMBIANA",
        "ANDINA",
        "GLOBAL",
        "GENERAL",
        "INDUSTRIAS",
        "MANUFACTURAS",
        "PRODUCTOS",
        "SUMINISTROS",
        "LOGISTICA",
        "TRANSPORTES",
        "CONSTRUCCIONES",
        "PROYECTOS",
    }
)

_GENERICOS_EN: frozenset[str] = frozenset(
    {
        "INTERNATIONAL",
        "GLOBAL",
        "GENERAL",
        "GROUP",
        "HOLDINGS",
        "SERVICES",
        "SOLUTIONS",
        "CONSULTING",
        "TRADING",
        "INDUSTRIES",
        "PRODUCTS",
        "SUPPLY",
        "LOGISTICS",
        "TRANSPORT",
        "ENTERPRISES",
        "PARTNERS",
        "VENTURES",
        "WORLDWIDE",
    }
)

_SUFIJOS_EN: frozenset[str] = frozenset(
    {
        "INC",
        "INC.",
        "INCORPORATED",
        "LLC",
        "L.L.C.",
        "LLP",
        "LTD",
        "LTD.",
        "LIMITED",
        "CORP",
        "CORP.",
        "CORPORATION",
        "CO",
        "CO.",
        "COMPANY",
        "PLC",
        "GMBH",
        "S.A.",
        "SA",
    }
)

# Semilla declarada para coreano (razones sociales KR frecuentes en aduanas).
# Ampliable por acta; NO se infiere del corpus.
_SUFIJOS_KR: frozenset[str] = frozenset(
    {
        "주식회사",  # sociedad anónima (prefijo o sufijo)
        "(주)",
        "㈜",
        "유한회사",  # sociedad limitada
        "유한책임회사",
        "합자회사",
        "합명회사",
        "CO LTD",
        "CO., LTD.",
        "CO.,LTD.",
    }
)

_GENERICOS_KR: frozenset[str] = frozenset({"코리아", "인터내셔널", "글로벌", "그룹"})

#: Registro de locales: sufijos legales y genéricos DECLARADOS por idioma.
LOCALES: dict[str, dict[str, frozenset[str]]] = {
    "ES": {"sufijos": frozenset(LEGAL_SUFFIXES), "genericos": _GENERICOS_ES},
    "EN": {"sufijos": _SUFIJOS_EN, "genericos": _GENERICOS_EN},
    "KR": {"sufijos": _SUFIJOS_KR, "genericos": _GENERICOS_KR},
}

#: Abreviaturas viales ES → forma canónica (dirección colombiana).
_ABREV_DIRECCION_ES: dict[str, str] = {
    "CL": "CALLE",
    "CLL": "CALLE",
    "CALL": "CALLE",
    "CRA": "CARRERA",
    "CR": "CARRERA",
    "KR": "CARRERA",
    "KRA": "CARRERA",
    "AV": "AVENIDA",
    "AVDA": "AVENIDA",
    "AK": "AVENIDA CARRERA",
    "AC": "AVENIDA CALLE",
    "DG": "DIAGONAL",
    "DIAG": "DIAGONAL",
    "TV": "TRANSVERSAL",
    "TRANSV": "TRANSVERSAL",
    "APTO": "APARTAMENTO",
    "APT": "APARTAMENTO",
    "OF": "OFICINA",
    "OFC": "OFICINA",
    "ED": "EDIFICIO",
    "EDIF": "EDIFICIO",
    "BRR": "BARRIO",
    "PISO": "PISO",
}


def _ascii_upper(serie: pd.Series) -> pd.Series:
    """MAYÚSCULAS sin tildes (NFKD → ASCII), vectorizado, null→''."""
    s = serie.astype("string").fillna("")
    return (
        s.str.normalize("NFKD")
        .str.encode("ascii", errors="ignore")
        .str.decode("ascii")
        .str.upper()
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )


def _quitar_terminos(serie: pd.Series, terminos: frozenset[str], min_tokens: int) -> pd.Series:
    """Quita términos exactos (por token) respetando el piso ``min_tokens``.

    Si al quitar quedarían menos de ``min_tokens`` tokens, se conserva el
    valor SIN recorte adicional (piso anti-percolación, F2.3).
    """
    if not len(serie):
        return serie
    tokens = serie.str.split()
    filtrados = tokens.apply(lambda ts: [t for t in ts if t not in terminos])
    n_filtrados = filtrados.str.len()
    usar_filtrado = n_filtrados >= min_tokens
    resultado = filtrados.str.join(" ")
    return resultado.where(usar_filtrado, serie)


# ═══════════════════════════════════════════════════════════════════════════
# Normalizadores por tipo (todos: pd.Series → pd.Series de string, ''=faltante)
# ═══════════════════════════════════════════════════════════════════════════


def normalizar_nombre(
    serie: pd.Series,
    *,
    locale: str = "ES",
    quitar_sufijos: bool = True,
    quitar_genericos: bool = False,
    min_tokens: int = 2,
) -> pd.Series:
    """Normaliza razones sociales / nombres de persona.

    Args:
        serie: nombres crudos.
        locale: clave de ``LOCALES`` ("ES", "EN", "KR").
        quitar_sufijos: elimina sufijos legales declarados del locale.
        quitar_genericos: OPT-IN; elimina genéricos declarados del locale,
            nunca por debajo de ``min_tokens`` tokens.
        min_tokens: piso de tokens preservados (anti-percolación).

    Raises:
        KeyError: locale no declarado (lista los disponibles).
    """
    if locale not in LOCALES:
        raise KeyError(
            f"Locale '{locale}' no declarado. Disponibles: {sorted(LOCALES)}. "
            f"Amplíe LOCALES con acta si necesita otro."
        )
    s = (
        _ascii_upper(serie)
        if locale != "KR"
        else (
            serie.astype("string")
            .fillna("")
            .str.replace(r"\s+", " ", regex=True)
            .str.strip()
            .str.upper()
        )
    )
    s = (
        s.str.replace(r"[^\w\s()]", " ", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )
    if quitar_sufijos:
        sufijos_norm = (
            {_ascii_upper(pd.Series([suf])).iloc[0] for suf in LOCALES[locale]["sufijos"]}
            if locale != "KR"
            else set(LOCALES[locale]["sufijos"])
        )
        # Frases multi-token (p. ej. "SUCURSAL DE COLOMBIA"): quitar como frase
        # COMPLETA al final del nombre, nunca token a token (borrar "COLOMBIA"
        # suelto destruiría información real).
        #
        # Dos defectos que hubo aquí hasta 0.22.2, ambos medidos sobre
        # "AVIATECA SOCIEDAD ANONIMA SUCURSAL COLOMBIA":
        #  1. El orden salía de un `set`: la clave ordenaba solo por número de
        #     tokens y los empates quedaban en orden de iteración del set, que
        #     cambia con PYTHONHASHSEED. El resultado de un proceso era
        #     "AVIATECA" y el de otro "AVIATECA ANONIMA": la misma base daba
        #     99.897 o 99.898 importadores según la sesión.
        #  2. Una sola pasada por frase, en secuencia: quitar "SUCURSAL COLOMBIA"
        #     deja "SOCIEDAD ANONIMA" al final, pero esa frase ya se había
        #     probado. Los sufijos apilados nunca se reducían del todo.
        # Ahora: orden total (tokens desc, longitud desc, alfabético) y un solo
        # regex con grupo repetido, que elimina frases apiladas hasta el punto
        # fijo. Determinista por construcción, independiente del orden del set.
        frases = sorted(
            (x for x in sufijos_norm if " " in x), key=lambda x: (-len(x.split()), -len(x), x)
        )
        if frases:
            patron = r"(?:\s+(?:" + "|".join(re.escape(f) for f in frases) + r"))+$"
            s = s.str.replace(patron, "", regex=True)
        # Tokens de una sola palabra (SAS, LTDA, SA, INC…): por token, piso 1.
        tokens_sufijo = frozenset(x for x in sufijos_norm if " " not in x)
        s = _quitar_terminos(s, tokens_sufijo, min_tokens=1)
    if quitar_genericos:
        s = _quitar_terminos(s, LOCALES[locale]["genericos"], min_tokens=min_tokens)
    s = s.where(~pd.Series(es_faltante(s), index=s.index), "")
    return s


def normalizar_identificador(
    serie: pd.Series,
    *,
    modo: str = "digits",
    min_longitud: int = 6,
    max_longitud: int = 64,
) -> pd.Series:
    """Normaliza identificadores sin convertirlos accidentalmente en números.

    Args:
        serie: identificadores crudos. Los floats integrales seguros se
            convierten sin el sufijo ``.0``; un float fraccionario o mayor a
            ``2**53`` se invalida porque ya no permite garantizar exactitud.
        modo: ``"digits"``/``"digitos"`` elimina todo salvo dígitos;
            ``"alphanumeric"``/``"alfanumerico"`` conserva letras Unicode.
        min_longitud: longitud mínima válida.
        max_longitud: longitud máxima válida.

    Returns:
        Serie string; ``""`` representa faltante o inválido.
    """
    aliases = {"digitos": "digits", "alfanumerico": "alphanumeric"}
    modo_normalizado = aliases.get(str(modo).strip().casefold(), str(modo).strip().casefold())
    if modo_normalizado not in {"digits", "alphanumeric"}:
        raise ValueError("modo debe ser 'digits'/'digitos' o 'alphanumeric'/'alfanumerico'.")
    if min_longitud < 1 or max_longitud < min_longitud:
        raise ValueError("Se requiere 1 <= min_longitud <= max_longitud.")

    faltante_original = pd.Series(es_faltante(serie), index=serie.index)
    if pd.api.types.is_integer_dtype(serie.dtype):
        s = serie.astype("string")
    elif pd.api.types.is_float_dtype(serie.dtype):
        numeric_array = pd.to_numeric(serie, errors="coerce").to_numpy(dtype=float, na_value=np.nan)
        finite_array = np.isfinite(numeric_array)
        # Evitar ``where=`` sin ``out``: NumPy deja sin inicializar las
        # posiciones donde la condición es falsa. Las comparaciones con NaN
        # ya retornan False de forma determinista.
        integral_array = np.mod(numeric_array, 1) == 0
        exacto_array = np.abs(numeric_array) <= 2**53
        valido = pd.Series(
            finite_array & integral_array & exacto_array,
            index=serie.index,
        )
        numeric = pd.Series(numeric_array, index=serie.index)
        s = numeric.where(valido).astype("Int64").astype("string")
    else:
        s = serie.astype("string")

    s = s.fillna("").str.strip()
    # Corrige el artefacto textual habitual de Excel/pandas ("123.0"), pero
    # no interpreta "123.000" porque podría ser un separador de miles real.
    decimal_cero = s.str.extract(r"^\+?(\d+)[\.,]0$", expand=False)
    s = s.where(decimal_cero.isna(), decimal_cero)
    if modo_normalizado == "digits":
        s = s.str.replace(r"\D+", "", regex=True)
    else:
        s = s.str.normalize("NFKC").str.upper().str.replace(r"[\W_]+", "", regex=True)
    longitud_valida = s.str.len().between(min_longitud, max_longitud)
    no_es_cero = ~s.str.fullmatch(r"0+")
    return s.where(~faltante_original & longitud_valida & no_es_cero, "")


def normalizar_telefono(serie: pd.Series, *, locale: str = "ES") -> pd.Series:
    """Teléfono: dígitos, sin prefijo de país del locale; <7 dígitos → ''.

    Prefijos: ES/CO→57, EN/US→1, KR→82. El comparador ``PhoneLastDigits``
    añade robustez adicional por últimos N dígitos.
    """
    prefijo = {"ES": "57", "EN": "1", "KR": "82"}.get(locale, "")
    s = serie.astype("string").fillna("").str.replace(r"\D+", "", regex=True)
    if prefijo:
        con_prefijo = s.str.startswith(prefijo) & (s.str.len() >= 7 + len(prefijo))
        s = s.where(~con_prefijo, s.str.slice(len(prefijo)))
    return s.where(s.str.len() >= 7, "")


def normalizar_email(serie: pd.Series) -> pd.Series:
    """Email: minúsculas y trim; sin '@' o placeholder → faltante ('')."""
    s = serie.astype("string").fillna("").str.strip().str.lower()
    s = s.where(s.str.contains("@", regex=False), "")
    return s.where(~pd.Series(es_faltante(s), index=s.index), "")


def normalizar_direccion(serie: pd.Series, *, locale: str = "ES") -> pd.Series:
    """Dirección: ASCII/upper + abreviaturas viales canónicas del locale."""
    s = _ascii_upper(serie)
    s = s.str.replace(r"[#º°\.]", " ", regex=True)
    s = s.str.replace(r"\bNO\b", " ", regex=True)
    if locale == "ES":
        for abrev, canon in _ABREV_DIRECCION_ES.items():
            s = s.str.replace(rf"\b{abrev}\b", canon, regex=True)
    s = s.str.replace(r"\s+", " ", regex=True).str.strip()
    return s.where(~pd.Series(es_faltante(s), index=s.index), "")


def normalizar_ciudad(serie: pd.Series) -> pd.Series:
    """Ciudad: ASCII/upper/trim; placeholders → ''."""
    s = _ascii_upper(serie)
    return s.where(~pd.Series(es_faltante(s), index=s.index), "")


def normalizar_fecha(serie: pd.Series, *, locale: str = "ES") -> pd.Series:
    """Fecha → ISO 'YYYY-MM-DD' como string; inválidas → ''.

    ``dayfirst`` según locale (ES=True). El comparador trabaja en días.
    """
    dt = pd.to_datetime(serie, errors="coerce", dayfirst=(locale == "ES"))
    return dt.dt.strftime("%Y-%m-%d").fillna("")


def normalizar_numero(
    serie: pd.Series,
    *,
    separador_decimal: str | None = None,
    separador_miles: str | None = None,
) -> pd.Series:
    """Numérico localizado → string canónico de float; inválidos → ``""``.

    Sin parámetros conserva la convención histórica (coma decimal). Para
    fuentes ambiguas se deben declarar ambos separadores: por ejemplo
    ``separador_decimal=",", separador_miles="."`` para ``"1.234,56"``.
    """
    for nombre, separador in (
        ("separador_decimal", separador_decimal),
        ("separador_miles", separador_miles),
    ):
        if separador is not None and len(separador) != 1:
            raise ValueError(f"{nombre} debe tener exactamente un carácter.")
    if separador_decimal is not None and separador_decimal == separador_miles:
        raise ValueError("Los separadores decimal y de miles deben ser distintos.")

    s = serie.astype("string").str.strip()
    if separador_miles is not None:
        if separador_miles.isspace() or separador_miles in {"\u00a0", "\u202f"}:
            s = s.str.replace(r"[\s\u00a0\u202f]+", "", regex=True)
        else:
            s = s.str.replace(separador_miles, "", regex=False)
    if separador_decimal is not None:
        if separador_decimal != ".":
            s = s.str.replace(separador_decimal, ".", regex=False)
    elif separador_miles is None:
        # Compatibilidad con el comportamiento preexistente.
        s = s.str.replace(",", ".", regex=False)
    valores = pd.to_numeric(s, errors="coerce")
    valores = valores.where(np.isfinite(valores))
    return valores.astype("Float64").astype("string").fillna("")


def normalizar_geo(lat: pd.Series, lon: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Lat/Lon → floats válidos; fuera de rango o no numérico → NaN."""
    la = pd.to_numeric(lat, errors="coerce")
    lo = pd.to_numeric(lon, errors="coerce")
    ok = la.between(-90, 90) & lo.between(-180, 180)
    return la.where(ok), lo.where(ok)


def normalizar_categorico(serie: pd.Series) -> pd.Series:
    """Categórico: ASCII/upper/trim; placeholders → ''."""
    return normalizar_ciudad(serie)


def normalizar_jerarquico(serie: pd.Series) -> pd.Series:
    """Código jerárquico (CIIU/HS/DANE): alfanumérico upper; placeholders → ''."""
    s = serie.astype("string").fillna("").str.strip().str.upper()
    s = s.str.replace(r"[^A-Z0-9]", "", regex=True)
    return s.where(~pd.Series(es_faltante(s), index=s.index), "")


def normalizar_conjunto(serie: pd.Series, *, separador: str = ";") -> pd.Series:
    """Multi-valor: tokens upper/trim ORDENADOS y re-unidos (forma canónica).

    La forma canónica ordenada hace que "B;A" y "A; B" produzcan la misma
    celda — útil para llaves exactas de bloqueo además del Jaccard.
    """
    s = serie.astype("string").fillna("").str.upper()
    partes = s.str.split(separador)
    canon = partes.map(
        lambda xs: (
            separador.join(sorted({t.strip() for t in xs if t and t.strip()}))
            if isinstance(xs, list)
            else ""
        )
    ).astype("string")
    return canon.where(~pd.Series(es_faltante(canon), index=canon.index), "")


#: Booleanos reconocidos → canónico "V"/"F" (v0.13.0). Se eligen V/F — y NO
#: "1"/"0" — a propósito: "0" está en PLACEHOLDERS y en _INVALID_VALUES de los
#: comparadores, así que un FALSO canónico "0" se trataría como FALTANTE y dos
#: registros con EXPORTA=NO jamás concordarían (bug atrapado por el smoke test
#: de v0.13.0 antes de publicar).
_BOOLEANOS: dict[str, str] = {
    "1": "V",
    "SI": "V",
    "SÍ": "V",
    "S": "V",
    "TRUE": "V",
    "VERDADERO": "V",
    "YES": "V",
    "Y": "V",
    "0": "F",
    "NO": "F",
    "N": "F",
    "FALSE": "F",
    "FALSO": "F",
}


def normalizar_booleano(serie: pd.Series) -> pd.Series:
    """Booleano → 'V'/'F' canónico; no reconocido o placeholder → '' (faltante)."""
    s = serie.astype("string").fillna("").str.strip().str.upper()
    return s.map(lambda v: _BOOLEANOS.get(v, "")).astype("string")


def normalizar_campo(serie: pd.Series, campo: CampoSpec) -> pd.Series:
    """Aplica el normalizador del tipo declarado en ``campo`` (registro F2.3)."""
    from .campos import TipoCampo  # import local: evita ciclo campos↔normalizadores

    t = campo.tipo
    if t in (TipoCampo.NOMBRE_EMPRESA, TipoCampo.NOMBRE_PERSONA):
        return normalizar_nombre(
            serie,
            locale=campo.locale,
            quitar_sufijos=campo.params.get("quitar_sufijos", True),
            quitar_genericos=campo.params.get("quitar_genericos", False),
            min_tokens=campo.params.get("min_tokens", 2),
        )
    if t is TipoCampo.IDENTIFICADOR:
        return normalizar_identificador(
            serie,
            modo=campo.params.get("modo", "digits"),
            min_longitud=int(campo.params.get("min_longitud", 6)),
            max_longitud=int(campo.params.get("max_longitud", 64)),
        )
    if t is TipoCampo.TELEFONO:
        return normalizar_telefono(serie, locale=campo.locale)
    if t is TipoCampo.EMAIL:
        return normalizar_email(serie)
    if t is TipoCampo.DIRECCION:
        return normalizar_direccion(serie, locale=campo.locale)
    if t is TipoCampo.CIUDAD:
        return normalizar_ciudad(serie)
    if t is TipoCampo.FECHA:
        return normalizar_fecha(serie, locale=campo.locale)
    if t is TipoCampo.NUMERICO:
        return normalizar_numero(
            serie,
            separador_decimal=campo.params.get("separador_decimal"),
            separador_miles=campo.params.get("separador_miles"),
        )
    if t is TipoCampo.CATEGORICO:
        return normalizar_categorico(serie)
    if t is TipoCampo.JERARQUICO:
        return normalizar_jerarquico(serie)
    if t is TipoCampo.CONJUNTO:
        return normalizar_conjunto(serie, separador=str(campo.params.get("separador", ";")))
    if t is TipoCampo.BOOLEANO:
        return normalizar_booleano(serie)
    raise ValueError(f"Tipo sin normalizador de serie única: {t} (GEO usa normalizar_geo).")
