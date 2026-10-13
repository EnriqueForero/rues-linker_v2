"""Normalización del motor de vinculación: nombres, países y contacto de cada fuente.

``Normalizador`` configura UNA vez la cadena de la librería (sanear → ASCII/mayúsculas → reunir iniciales
→ retirar formas legales) y el catálogo de países. ``preparar_fuente`` la aplica a una fuente ya leída y
devuelve las columnas estándar con las que trabaja el resto del motor. El dominio web, el dominio del
correo, los dígitos del teléfono y el identificador comparable se extraen con funciones sueltas.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import ClassVar

import numpy as np
import pandas as pd

from record_linkage.flujo.vinculacion.config import ConfigVinculacion, Fuente
from record_linkage.flujo.vinculacion.constantes import CONECTORES, LEGALES_BASE
from record_linkage.matching.genericos import (
    GENERICOS_ESTRUCTURALES,
    GENERICOS_GEOGRAFIA,
    GENERICOS_SECTOR,
    SUFIJOS_INTERNACIONALES,
)
from record_linkage.matching.normalizadores import LOCALES, normalizar_nombre
from record_linkage.paises import CATALOGO_PAISES, canonizar_pais, sugerir_alias_pais
from record_linkage.processing.saneamiento import (
    PRELIMPIEZA_COMERCIO_EXTERIOR,
    ascii_mayusculas,
    sanear_texto,
    unir_iniciales,
)

__all__ = [
    "Normalizador",
    "digitos_telefono",
    "dominio_email",
    "dominio_registrable",
    "dominio_web",
    "id_comparable",
    "preparar_fuente",
    "serie_numerica",
]

#: Segundos niveles que, junto a un dominio de país de 2 letras, forman el sufijo público (``co.uk``, ``gob.es``).
_SEGUNDOS_NIVELES = frozenset(
    {
        "co",
        "com",
        "org",
        "net",
        "gov",
        "gob",
        "gub",
        "mil",
        "edu",
        "ac",
        "ed",
        "or",
        "ne",
        "go",
        "nom",
        "sch",
    }
)

#: Una dirección IPv4: no es un dominio y no identifica a una empresa.
_IPV4 = re.compile(r"\d{1,3}(\.\d{1,3}){3}")

#: Lo que una celda de sitio web trae cuando no hay dirección.
_WEB_AUSENTE = frozenset({"nan", "none", "n.a.", "-"})

#: Formas legales de 2 letras que también son palabras corrientes: cuentan como palabra legal
#: (``Normalizador.legales``) pero no se registran como sufijo en la librería.
_LEGALES_NO_SUFIJO = frozenset({"CO", "SE", "AS", "AB", "SC", "SP", "RL"})


def _es_ausente(x: object) -> bool:
    """True si ``x`` es la celda vacía que deja pandas: ``None``, ``pd.NA``, ``pd.NaT`` o un ``NaN`` (cualquier ancho).

    F3.1b (g1-O1): el original solo reconocía ``None`` y el ``float`` NaN, así que ``pd.NA`` (texto anulable, p. ej. de
    un Parquet) terminaba como el texto «NA» y dos empresas sin NIF «compartían identificador».
    """
    if x is None or x is pd.NA or x is pd.NaT:
        return True
    return isinstance(x, (float, np.floating)) and math.isnan(x)


def _texto_de_celda(x: object) -> str:
    """``str(x)``, pero un número entero guardado como ``float`` (``900123456.0``) se escribe sin el «.0».

    F3.1b (g1-O2): un NIT o un teléfono que viene de una columna numérica con ausentes (Parquet ``float64``, ``Int64``)
    es ``float``; ``str`` le agregaba un dígito al unirse con el texto de la otra base.
    """
    if isinstance(x, (float, np.floating)) and math.isfinite(x) and float(x).is_integer():
        return str(int(x))
    return str(x)


def dominio_registrable(dominio: str) -> str:
    """El dominio por el que se identifica a un sitio: ``tienda.zelvora.co.uk`` → ``zelvora.co.uk``.

    Conserva los dos últimos rótulos (``www.kramento.es`` → ``kramento.es``) o tres si el segundo nivel es
    público (``co``, ``com``, ``org``, ``net``, ``gov``, ``gob``, ``gub``, ``mil``, ``edu``, ``ac``, ``ed``, ``or``,
    ``ne``, ``go``, ``nom``, ``sch``) y el dominio de país tiene 2 letras: ``tienda.zelvora.gob.es`` →
    ``zelvora.gob.es``, no ``gob.es``.
    """
    partes = [p for p in dominio.split(".") if p]
    if len(partes) <= 2:
        return ".".join(partes)
    if partes[-2] in _SEGUNDOS_NIVELES and len(partes[-1]) == 2:
        return ".".join(partes[-3:])
    return ".".join(partes[-2:])


def serie_numerica(s: pd.Series) -> pd.Series:
    """``s`` como números: ``'n.a.'``, ``'n.s.'``, ``'-'`` y todo lo que no sea un número quedan en ``NaN``."""
    # Hallazgo F3 (menor): el replace es redundante, errors="coerce" ya deja en NaN esos tres textos.
    return pd.to_numeric(
        pd.Series(s, dtype="object").replace({"n.a.": None, "n.s.": None, "-": None}),
        errors="coerce",
    )


def dominio_web(x: object) -> str:
    """Dominio registrable de una dirección web (sin esquema, ``www``, puerto, ruta ni consulta).

    Con varias direcciones en la celda toma la primera (se ignoran los separadores del comienzo). Vacío si no hay
    un dominio con punto o si es una dirección IP.
    """
    if _es_ausente(x):
        return ""
    s = str(x).strip().lower()
    if not s or s in _WEB_AUSENTE:
        return ""
    s = re.split(r"[\s;,|]+", s.strip(" \t;,|"))[0]
    s = re.sub(r"^[a-z]+://", "", s)
    s = (
        s.split("/")[0].split("?")[0].rsplit("@", 1)[-1].split(":")[0]
    )  # sin ruta, consulta, usuario ni puerto
    s = re.sub(r"^www\d?\.", "", s)
    if "." not in s or _IPV4.fullmatch(s):
        return ""
    return dominio_registrable(s)


def dominio_email(x: object) -> str:
    """Dominio registrable del primer correo que haya en ``x``; vacío si no hay ninguno."""
    if _es_ausente(x):
        return ""
    m = re.search(r"@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})", str(x))
    return dominio_registrable(m.group(1).lower()) if m else ""


def digitos_telefono(x: object) -> str:
    """Los últimos 8 dígitos del teléfono (sin prefijo de país ni signos); vacío si tiene menos de 8."""
    if _es_ausente(x):
        return ""
    d = re.sub(r"\D", "", _texto_de_celda(x))
    return d[-8:] if len(d) >= 8 else ""


def id_comparable(x: object) -> str:
    """Identificador sin signos y en mayúsculas (``'B-1234 5678'`` → ``'B12345678'``) para compararlo entre bases."""
    if _es_ausente(x):
        return ""
    return re.sub(r"[^A-Z0-9]", "", _texto_de_celda(x).upper())


class Normalizador:
    """Normalización de nombres y países con la librería, configurada una sola vez.

    Atributos que usa el resto del motor: ``cfg``, ``genericos`` (palabras que nunca identifican),
    ``legales`` (palabras de forma legal), ``catalogo`` (países con los alias declarados) y
    ``patron_inter`` (regex de agentes de carga, o ``None``).
    """

    LOCALE = "VINCULADOR_EMPRESAS"

    _PAREN: ClassVar[re.Pattern[str]] = re.compile(r"\(([^()]*)\)")
    _PAREN_ABIERTO: ClassVar[re.Pattern[str]] = re.compile(r"\([^()]*$")
    _APOSTROFE_S: ClassVar[re.Pattern[str]] = re.compile(r"(\w)\s?['`´’]\s?(S)\b", re.I)
    _APOSTROFE: ClassVar[re.Pattern[str]] = re.compile(r"['`´’]")

    def __init__(self, cfg: ConfigVinculacion) -> None:
        self.cfg = cfg
        sufijos = self._sufijos_legales(cfg)
        self.genericos = frozenset(
            set(GENERICOS_ESTRUCTURALES)
            | set(GENERICOS_SECTOR)
            | set(GENERICOS_GEOGRAFIA)
            | set(cfg.genericos_extra)
        )
        self._registrar_locale(sufijos, self.genericos)
        self.legales = self._palabras_legales(sufijos, cfg)
        self.catalogo = self._catalogo(cfg.alias_paises)
        self.patron_inter = (
            re.compile("|".join(cfg.patrones_intermediario), re.I)
            if cfg.patrones_intermediario
            else None
        )

    @staticmethod
    def _sufijos_legales(cfg: ConfigVinculacion) -> set[str]:
        """Sufijos legales que se retiran del final del nombre.

        Reúne los de la librería (ES y EN y los internacionales), las formas declaradas en la
        configuración y las palabras sueltas de ``LEGALES_BASE`` (salvo ``_LEGALES_NO_SUFIJO``).
        """
        sufijos: set[str] = set()
        for base in ("ES", "EN"):
            sufijos |= set(LOCALES[base]["sufijos"])
        sufijos |= set(SUFIJOS_INTERNACIONALES) | set(cfg.formas_legales_extra)
        sufijos |= {
            t for t in LEGALES_BASE if " " not in t and len(t) >= 2 and t not in _LEGALES_NO_SUFIJO
        }
        return sufijos

    def _registrar_locale(self, sufijos: set[str], genericos: frozenset[str]) -> None:
        """Declara en la librería el locale de este motor: sus sufijos legales y sus genéricos.

        ``normalizar()`` lo pide por nombre a ``normalizar_nombre`` (``locale=self.LOCALE``).
        """
        # Hallazgo F3: estado global de la librería; F3.2 lo sustituye por matching.locales.registrar_locale_compuesto
        LOCALES[self.LOCALE] = {"sufijos": frozenset(sufijos), "genericos": genericos}

    @staticmethod
    def _palabras_legales(sufijos: set[str], cfg: ConfigVinculacion) -> frozenset[str]:
        """Palabras legales sueltas, sin signos y de 2 o más caracteres.

        Reúne ``LEGALES_BASE``, los sufijos de una sola palabra y las palabras de las formas declaradas.
        De las FRASES de la librería NO se toman sus palabras sueltas: «COMERCIALIZADORA INTERNACIONAL»
        es una forma legal colombiana, pero «INTERNACIONAL» suelta es parte del nombre (medido: sin esta
        regla ZELVORA INTERNACIONAL quedaba igual a ZELVORA S.A.).
        """
        # Hallazgo F3: la frase «DE CV» de LEGALES_BASE (con espacio) entra entera y se limpia a 'DECV',
        # una palabra que nunca aparece; 'CV' ya está aparte.
        legales = set(LEGALES_BASE) | {t for t in sufijos if " " not in t}
        for s in cfg.formas_legales_extra:
            legales |= set(re.sub(r"[^A-Z0-9 ]", " ", s.upper()).split())
        return frozenset(
            re.sub(r"[^A-Z0-9]", "", t) for t in legales if len(re.sub(r"[^A-Z0-9]", "", t)) >= 2
        )

    @staticmethod
    def _catalogo(
        alias: Mapping[str, Iterable[str]],
    ) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        """El catálogo de países de la librería con los alias declarados (ISO3 → grafías) agregados.

        Raises:
            KeyError: algún ISO3 de ``alias`` no existe en el catálogo.
        """
        iso_validos = {iso for iso, _, _ in CATALOGO_PAISES}
        malos = sorted(set(alias) - iso_validos)
        if malos:
            raise KeyError(f"ALIAS_PAISES usa ISO3 inexistentes en el catálogo: {malos}")
        return tuple(
            (iso, nombre, tuple(graf) + tuple(str(a).strip().upper() for a in alias.get(iso, ())))
            for iso, nombre, graf in CATALOGO_PAISES
        )

    # ── nombres ──────────────────────────────────────────────────────────────
    def separar_alias(self, nombre: object) -> tuple[str, str]:
        """``'Zelvora Foods (Dulcevent)'`` → ``('Zelvora Foods', 'Dulcevent')``.

        Un paréntesis sin cerrar se descarta; ``&AMP;`` y ``&amp;`` pasan a ``&``.
        """
        s = "" if _es_ausente(nombre) else str(nombre)
        s = s.replace("&AMP;", "&").replace("&amp;", "&")
        if "(" not in s:
            return s, ""
        alias = " ".join(m.strip() for m in self._PAREN.findall(s) if m.strip())
        principal = self._PAREN_ABIERTO.sub(" ", self._PAREN.sub(" ", s))
        return re.sub(r"\s+", " ", principal).strip(), alias

    def normalizar(self, serie: pd.Series) -> pd.Series:
        """Cadena de la librería: sanear → ASCII/mayúsculas → iniciales → retirar formas legales."""
        s = pd.Series(serie, dtype="object").fillna("").astype(str)
        s = s.str.replace(self._APOSTROFE_S, r"\1\2", regex=True).str.replace(
            self._APOSTROFE, " ", regex=True
        )
        limpio = unir_iniciales(
            ascii_mayusculas(
                sanear_texto(s.astype("string"), prelimpieza=PRELIMPIEZA_COMERCIO_EXTERIOR)
            )
        )
        out = normalizar_nombre(
            limpio, locale=self.LOCALE, quitar_sufijos=True, quitar_genericos=False
        )
        return (
            out.astype(object)
            .fillna("")
            .astype(str)
            .str.replace(r"[()]", " ", regex=True)
            .str.replace(r"\s+", " ", regex=True)
            .str.strip()
        )

    def utiles(self, nombre: str) -> list[str]:
        """Las palabras con contenido: más de una letra y ni forma legal, ni conector, ni número."""
        return [
            t
            for t in nombre.split()
            if len(t) > 1 and t not in self.legales and t not in CONECTORES and not t.isdigit()
        ]

    def distintivas(self, nombre: str) -> list[str]:
        """Las palabras útiles que además no son genéricas y tienen 3 o más letras: las que identifican."""
        return [t for t in self.utiles(nombre) if t not in self.genericos and len(t) >= 3]

    def marcas(self, nombre: str, truncado: bool = False) -> tuple[str, ...]:
        """Iniciales sueltas y números: ``'J ORBELIA'`` ≠ ``'K ORBELIA'``; ``'ZELVORA 2'`` ≠ ``'ZELVORA 3'``.

        Con ``truncado`` se descarta una inicial final: el sistema de origen pudo cortar la última palabra.
        """
        # Hallazgo F3: los conectores de una letra (A, D, E, I, L, V, Y) se descartan ANTES de buscar
        # iniciales, así que esas letras nunca son marca: 'A ORBELIA' y 'ORBELIA' tienen las mismas marcas.
        t = [x for x in nombre.split() if x not in self.legales and x not in CONECTORES]
        if truncado and t and len(t[-1]) == 1:
            t = t[:-1]
        return tuple(sorted(x for x in t if (len(x) == 1 and x.isalpha()) or x.isdigit()))

    def compacto(self, nombre: str) -> str:
        """Las palabras útiles pegadas: ``'KR FOODS'`` y ``'KRFOODS'`` dan ambas ``'KRFOODS'``."""
        return "".join(self.utiles(nombre))

    # ── países ───────────────────────────────────────────────────────────────
    def paises(self, serie: pd.Series, fuente: str, columna: str) -> pd.DataFrame:
        """ISO3 + nombre canónico. Detiene la corrida si hay grafías fuera del catálogo.

        Las celdas vacías dan ``''`` en las dos columnas (``PAIS_ISO3``, ``PAIS_FINAL``).

        Raises:
            ValueError: hay grafías que el catálogo no reconoce (el mensaje las lista con una sugerencia).
        """
        s = pd.Series(serie, dtype="object").fillna("").astype(str).str.strip()
        vacio = s == ""
        # Solo se clasifican las celdas con algo escrito: así ningún marcador de «celda vacía» llega a las
        # sugerencias ni a los ejemplos del mensaje como si lo hubiera escrito el usuario.
        res = canonizar_pais(s[~vacio].astype("string"), catalogo=self.catalogo)
        if (res.tabla["PAIS_METODO"] == "sin_clasificar").any():
            sug = sugerir_alias_pais(res.sin_clasificar, catalogo=self.catalogo, maximo=50)
            ejemplos = ", ".join(f"'ISO3': ({str(v).upper()!r},)" for v in sug["valor"].head(5))
            # 'NA' es Namibia, pero en muchas bases quiere decir «no disponible»: el catálogo no puede saberlo (A2 de F3.1b).
            nota_na = (
                " Si 'NA' significa «sin dato» y no Namibia, déjela vacía en la base."
                if sug["valor"].astype(str).str.upper().eq("NA").any()
                else ""
            )
            raise ValueError(
                f"Qué pasó: {fuente}.{columna} trae grafías de país que el catálogo no reconoce:\n"
                f"{sug.to_string(index=False)}\n"
                f"Por qué importa: el país es evidencia del cruce; sin catalogar, 'France' y 'Francia' no coinciden.\n"
                f"Qué hacer: agréguelas en ALIAS_PAISES con su ISO3, p. ej. {ejemplos}. La sugerencia es una ayuda, no una regla.{nota_na}"
            )
        tabla = res.tabla[["PAIS_ISO3", "PAIS_FINAL"]].copy()
        tabla.index = s.index[~vacio]
        return tabla.reindex(s.index, fill_value="").reset_index(drop=True)


# ── preparar_fuente: un bloque por familia de columnas ───────────────────────
def _texto_crudo(serie: pd.Series) -> pd.Series:
    """``serie`` como texto, con ``''`` en lugar de los ausentes."""
    return serie.astype(object).where(serie.notna(), "").astype(str)


def _columna_o_vacia(df: pd.DataFrame, columna: str | None) -> np.ndarray:
    """Los valores de ``columna``, o un arreglo de ``None`` si la fuente no la declara."""
    return df[columna].to_numpy() if columna else np.full(len(df), None, dtype=object)


def _agregar_nombre_y_alias(out: pd.DataFrame, crudo: pd.Series, nz: Normalizador) -> None:
    """``NOMBRE_ORIGINAL``, ``NOMBRE_NORM`` y ``ALIAS_NORM`` (el alias solo si aporta una palabra distintiva)."""
    out["NOMBRE_ORIGINAL"] = crudo.to_numpy()
    partes = crudo.map(nz.separar_alias)
    out["NOMBRE_NORM"] = nz.normalizar(partes.str[0]).to_numpy()
    alias = nz.normalizar(partes.str[1]).to_numpy()
    util = np.array(
        [bool(nz.distintivas(a)) for a in alias], dtype=bool
    )  # dtype: con 0 filas sería float64
    out["ALIAS_NORM"] = np.where(util & (alias != out["NOMBRE_NORM"].to_numpy()), alias, "")
    # Sin nombre principal utilizable pero con alias: el alias pasa a principal.
    # Hallazgo F3: «utilizable» se mide con NOMBRE_NORM == "", no con nz.utiles(): un nombre que queda
    # solo en una forma legal («SL») no cede el lugar al alias aunque SIN_NOMBRE_UTIL sea True.
    vacio = (out["NOMBRE_NORM"] == "") & (out["ALIAS_NORM"] != "")
    out.loc[vacio, "NOMBRE_NORM"] = out.loc[vacio, "ALIAS_NORM"]
    out.loc[vacio, "ALIAS_NORM"] = ""


def _agregar_grupo(out: pd.DataFrame, df: pd.DataFrame, f: Fuente, nz: Normalizador) -> None:
    """``GRUPO_ORIGINAL`` y ``GRUPO_NORM`` (vacío si el grupo normaliza igual que el propio nombre)."""
    if f.col_grupo:
        g = _texto_crudo(df[f.col_grupo]).str.strip()
        out["GRUPO_ORIGINAL"] = g.to_numpy()
        gp = g.map(nz.separar_alias)
        gn = nz.normalizar(gp.str[0]).to_numpy()
        out["GRUPO_NORM"] = np.where(gn == out["NOMBRE_NORM"].to_numpy(), "", gn)
    else:
        out["GRUPO_ORIGINAL"], out["GRUPO_NORM"] = "", ""


def _agregar_paises(out: pd.DataFrame, df: pd.DataFrame, f: Fuente, nz: Normalizador) -> None:
    """País principal (``PAIS_ORIGINAL``, ``PAIS_ISO3``, ``PAIS_CANONICO``) y ``PAISES_EVIDENCIA``.

    ``PAISES_EVIDENCIA`` junta el principal y los de ``cols_pais_extra`` (ISO3 ordenados, unidos por
    ``|``): cualquiera de ellos cuenta como «mismo país».
    """
    if f.col_pais:
        p = nz.paises(df[f.col_pais], f.clave, f.col_pais)
        out["PAIS_ORIGINAL"] = _texto_crudo(df[f.col_pais]).to_numpy()
        out["PAIS_ISO3"], out["PAIS_CANONICO"] = (
            p["PAIS_ISO3"].to_numpy(),
            p["PAIS_FINAL"].to_numpy(),
        )
    else:
        out["PAIS_ORIGINAL"], out["PAIS_ISO3"], out["PAIS_CANONICO"] = "", "", ""
    isos = [{x} - {""} for x in out["PAIS_ISO3"]]
    for c in f.cols_pais_extra:
        p = nz.paises(df[c], f.clave, c)
        for k, x in enumerate(p["PAIS_ISO3"]):
            if x:
                isos[k].add(x)
    out["PAISES_EVIDENCIA"] = ["|".join(sorted(s)) for s in isos]


def _agregar_contacto(out: pd.DataFrame, df: pd.DataFrame, f: Fuente) -> None:
    """Contacto y variables adicionales; lo que la fuente no declara queda vacío (``-1.0`` en ``TAMANO``)."""
    web = _columna_o_vacia(df, f.col_web)
    email = _columna_o_vacia(df, f.col_email)
    telefono = _columna_o_vacia(df, f.col_telefono)
    out["WEB"] = pd.Series(web, dtype=object).fillna("").astype(str).to_numpy()
    out["DOMINIO_WEB"] = [dominio_web(x) for x in web]
    out["EMAIL"] = pd.Series(email, dtype=object).fillna("").astype(str).to_numpy()
    out["DOMINIO_EMAIL"] = [dominio_email(x) for x in email]
    out["TELEFONO"] = (
        pd.Series(telefono, dtype=object)
        .fillna("")
        .astype(str)
        .str.lstrip("'")
        .str.strip()
        .to_numpy()
    )
    out["TEL_DIGITOS"] = [digitos_telefono(x) for x in telefono]
    out["CIUDAD"] = (
        pd.Series(_columna_o_vacia(df, f.col_ciudad), dtype=object)
        .fillna("")
        .astype(str)
        .str.strip()
        .to_numpy()
    )
    out["CIUDAD_NORM"] = (
        ascii_mayusculas(pd.Series(out["CIUDAD"], dtype="string")).astype(object).to_numpy()
    )
    out["DIRECCION"] = (
        pd.Series(_columna_o_vacia(df, f.col_direccion), dtype=object)
        .fillna("")
        .astype(str)
        .to_numpy()
    )
    out["ID_COMPARABLE"] = [id_comparable(x) for x in _columna_o_vacia(df, f.col_id_comparable)]
    out["TAMANO"] = (
        serie_numerica(pd.Series(_columna_o_vacia(df, f.col_tamano), dtype=object))
        .fillna(-1)
        .to_numpy()
        if f.col_tamano
        else -1.0
    )


def _anular_dominios_genericos(out: pd.DataFrame, nz: Normalizador) -> None:
    """Deja en ``''`` los dominios (web y correo) que la configuración declara genéricos."""
    gen = nz.cfg.dominios_genericos
    for c in ("DOMINIO_WEB", "DOMINIO_EMAIL"):
        out[c] = np.where(pd.Series(out[c]).isin(gen), "", out[c])


def _agregar_banderas(out: pd.DataFrame, crudo: pd.Series, f: Fuente, nz: Normalizador) -> None:
    """``TRUNCADO``, ``INTERMEDIARIO`` y ``SIN_NOMBRE_UTIL``."""
    out["TRUNCADO"] = (
        (crudo.str.len() >= f.largo_truncamiento).to_numpy() if f.largo_truncamiento else False
    )
    out["INTERMEDIARIO"] = (
        crudo.str.contains(nz.patron_inter, na=False).to_numpy()
        if (f.detectar_intermediarios and nz.patron_inter is not None)
        else False
    )
    out["SIN_NOMBRE_UTIL"] = [not nz.utiles(n) for n in out["NOMBRE_NORM"]]


def preparar_fuente(df: pd.DataFrame, f: Fuente, nz: Normalizador) -> pd.DataFrame:
    """Columnas estándar del motor para una fuente ya leída por ``leer_fuente``.

    El orden de las columnas es parte del contrato (las demás etapas y el Excel las leen):
    ``FUENTE``, ``ID_REGISTRO``, ``FILA_EXCEL`` · ``NOMBRE_ORIGINAL``, ``NOMBRE_NORM``, ``ALIAS_NORM`` ·
    ``GRUPO_ORIGINAL``, ``GRUPO_NORM`` · ``PAIS_ORIGINAL``, ``PAIS_ISO3``, ``PAIS_CANONICO``,
    ``PAISES_EVIDENCIA`` · ``WEB``, ``DOMINIO_WEB``, ``EMAIL``, ``DOMINIO_EMAIL``, ``TELEFONO``,
    ``TEL_DIGITOS``, ``CIUDAD``, ``CIUDAD_NORM``, ``DIRECCION``, ``ID_COMPARABLE``, ``TAMANO`` ·
    ``TRUNCADO``, ``INTERMEDIARIO``, ``SIN_NOMBRE_UTIL``.

    Raises:
        ValueError: una columna de país trae grafías que el catálogo no reconoce.
    """
    out = pd.DataFrame(
        {
            "FUENTE": f.clave,
            "ID_REGISTRO": df["ID_REGISTRO"].to_numpy(),
            "FILA_EXCEL": df["FILA_EXCEL"].to_numpy(),
        }
    )
    crudo = _texto_crudo(df[f.col_nombre]).str.strip()
    _agregar_nombre_y_alias(out, crudo, nz)
    _agregar_grupo(out, df, f, nz)
    _agregar_paises(out, df, f, nz)
    _agregar_contacto(out, df, f)
    _anular_dominios_genericos(out, nz)
    _agregar_banderas(out, crudo, f, nz)
    return out
