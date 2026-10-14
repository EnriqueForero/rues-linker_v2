"""Decisiones de revisión (entrada humana): lectura de CSV/XLSX, normalización, validación y plantilla.

Una decisión es una fila con ``TIPO`` (``VINCULO`` o ``DUPLICADO``), la ``FUENTE`` a la que aplica, el
par de claves ``CLAVE_A`` / ``CLAVE_B`` y la ``DECISION`` tomada. ``leer_decisiones`` junta 0..N archivos en
una sola tabla con las columnas ``COLUMNAS_DECISION`` más ``ARCHIVO`` y ``ORDEN``. El orden de los archivos
importa: el último manda.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

__all__ = [
    "COLUMNAS_DECISION",
    "DECISIONES_DUPLICADO",
    "DECISIONES_VINCULO",
    "SINONIMOS_DECISION",
    "clave_ancla",
    "clave_texto",
    "leer_decisiones",
    "plantilla_decisiones",
]

logger = logging.getLogger(__name__)

COLUMNAS_DECISION = [
    "TIPO",
    "FUENTE",
    "CLAVE_A",
    "NOMBRE_A",
    "CLAVE_B",
    "NOMBRE_B",
    "DECISION",
    "AUTOR",
    "FECHA",
    "RAZON",
]
DECISIONES_VINCULO = {"EMPRESA", "GRUPO", "NO_VINCULAR", "RELACION_PROPIEDAD"}
DECISIONES_DUPLICADO = {"MISMA_ENTIDAD", "DISTINTA"}
SINONIMOS_DECISION = {
    "MISMA_EMPRESA": "EMPRESA",
    "MISMO_GRUPO": "GRUPO",
    "DISTINTA_EMPRESA": "NO_VINCULAR",
    "NO": "NO_VINCULAR",
    "NO_ES": "NO_VINCULAR",
    "RECHAZAR": "NO_VINCULAR",
    "SI": "EMPRESA",
    "PROPIEDAD": "RELACION_PROPIEDAD",
    "MISMA": "MISMA_ENTIDAD",
    "DUPLICADO": "MISMA_ENTIDAD",
}

#: Columnas sin las cuales una decisión no se puede aplicar (el resto de ``COLUMNAS_DECISION`` es opcional).
_COLUMNAS_OBLIGATORIAS = ("TIPO", "FUENTE", "CLAVE_A", "CLAVE_B", "DECISION")
_HOJAS_DECISIONES = ("DECISIONES", "REVISION")


_ESPACIOS = re.compile(r"\s+")


def _sin_espacios_de_mas(texto: str) -> str:
    return _ESPACIOS.sub(" ", texto.strip())


def clave_texto(x: Iterable[object]) -> pd.Series:
    """Clave natural sin espacios de más: sobrevive a un CSV abierto y guardado en Excel.

    F3.1b (g3-O5): se normaliza con ``re`` de Python, igual en pandas 2 y 3. Con pandas 3 el ``\\s`` de ``Series.str``
    lo evalúa Arrow/RE2, que no reconoce el espacio duro (NBSP), U+2007, U+202F, U+3000, ``\\x0b`` ni ``\\x85``: la
    misma empresa daba otra grafía y un archivo de decisiones hecho en pandas 2 no casaba en pandas 3.
    """
    return pd.Series(x, dtype="object").fillna("").astype(str).map(_sin_espacios_de_mas).astype(str)


def clave_ancla(pais: Iterable[object], nombre: Iterable[object]) -> pd.Series:
    """Clave de una grafía de la ancla: ``país|nombre``, cada parte con los espacios normalizados."""
    return clave_texto(pais).str.cat(clave_texto(nombre), sep="|")


def _leer_xlsx(p: Path) -> pd.DataFrame:
    """Excel de resultados: DECISIONES (historial aplicado) + REVISION (lo que se llenó después).

    Junta las filas con DECISION de las hojas que existan, en ese orden, y si una misma
    decisión sobre el mismo par aparece en las dos se queda con la última.
    """
    hojas = pd.ExcelFile(p).sheet_names
    leidas = [h for h in _HOJAS_DECISIONES if h in hojas]
    if not leidas:
        raise ValueError(f"{p.name}: no tiene hoja DECISIONES ni REVISION.")
    partes = []
    for h in leidas:
        x = pd.read_excel(p, sheet_name=h, dtype=str).fillna("")
        x.columns = [c.strip().upper() for c in x.columns]
        if "DECISION" in x.columns:
            partes.append(x[x["DECISION"].str.strip() != ""])
    r = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLUMNAS_DECISION)
    clave = [c for c in _COLUMNAS_OBLIGATORIAS if c in r.columns]
    return r.drop_duplicates(clave, keep="last")


def _leer_csv(p: Path) -> pd.DataFrame:
    """CSV de decisiones: separador ``;`` o ``,`` según la primera línea; UTF-8 y, si falla, cp1252."""
    texto = p.read_bytes()[:4096].decode("utf-8", errors="ignore")
    # Hallazgo F3: un archivo vacío (0 bytes) revienta aquí con IndexError, sin mensaje accionable.
    sep = ";" if texto.splitlines()[0].count(";") > texto.splitlines()[0].count(",") else ","
    try:
        return pd.read_csv(p, dtype=str, keep_default_na=False, sep=sep, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(p, dtype=str, keep_default_na=False, sep=sep, encoding="cp1252")


def _normalizar_columnas(r: pd.DataFrame, p: Path) -> pd.DataFrame:
    """Deja las columnas de ``COLUMNAS_DECISION`` (con los encabezados en mayúsculas y ``DECISION_HUMANA`` como
    ``DECISION``) y solo las filas que traen una decisión; falla si falta alguna columna obligatoria."""
    r.columns = [c.strip().upper() for c in r.columns]
    if "DECISION" not in r.columns and "DECISION_HUMANA" in r.columns:
        r = r.rename(columns={"DECISION_HUMANA": "DECISION"})
    faltan = [c for c in _COLUMNAS_OBLIGATORIAS if c not in r.columns]
    if faltan:
        raise KeyError(
            f"Qué pasó: {p.name} no trae las columnas {faltan}.\nQué hacer: use la plantilla "
            f"(columnas {COLUMNAS_DECISION}) o la hoja REVISION del Excel de resultados."
        )
    for c in COLUMNAS_DECISION:
        if c not in r.columns:
            r[c] = ""
    r = r[COLUMNAS_DECISION].apply(lambda c: c.astype(str).str.strip())
    return r[r["DECISION"] != ""].copy()


def _normalizar_valores(r: pd.DataFrame, p: Path, orden: int) -> pd.DataFrame:
    """Valores canónicos: TIPO y DECISION en mayúsculas (DECISION con sinónimos), claves sin espacios de más,
    y las columnas ``ARCHIVO`` y ``ORDEN``."""
    r["TIPO"] = r["TIPO"].str.upper().replace({"CRUCE": "VINCULO", "VÍNCULO": "VINCULO"})
    r["DECISION"] = r["DECISION"].str.upper().str.replace(" ", "_").replace(SINONIMOS_DECISION)
    r["FUENTE"] = r["FUENTE"].str.upper()
    r["CLAVE_A"], r["CLAVE_B"] = (
        clave_texto(r["CLAVE_A"]).to_numpy(),
        clave_texto(r["CLAVE_B"]).to_numpy(),
    )
    r["ARCHIVO"] = p.name
    r["ORDEN"] = orden
    return r


def _validar(r: pd.DataFrame, p: Path) -> None:
    """Falla si alguna fila tiene un TIPO o una DECISION fuera del vocabulario."""
    malas = r[
        ((r.TIPO == "VINCULO") & ~r.DECISION.isin(DECISIONES_VINCULO))
        | ((r.TIPO == "DUPLICADO") & ~r.DECISION.isin(DECISIONES_DUPLICADO))
        | ~r.TIPO.isin({"VINCULO", "DUPLICADO"})
    ]
    if len(malas):
        raise ValueError(
            f"Qué pasó: {len(malas)} filas de {p.name} con TIPO o DECISION no válidos.\n"
            f"Qué hacer: TIPO ∈ {{VINCULO, DUPLICADO}}; DECISION ∈ {sorted(DECISIONES_VINCULO)} "
            f"o {sorted(DECISIONES_DUPLICADO)}.\n{malas.head().to_string()}"
        )


def leer_decisiones(rutas: Iterable[str | Path] | None) -> pd.DataFrame:
    """Lee 0..N archivos de decisiones (CSV o XLSX con hoja REVISION/DECISIONES). El orden importa: el último manda.

    Devuelve las columnas de ``COLUMNAS_DECISION`` más ``ARCHIVO`` (nombre del archivo) y ``ORDEN``
    (posición del archivo en ``rutas``, contando los que no existen). Solo quedan las filas con
    ``DECISION``. Un archivo que no existe se omite con una advertencia en el registro.

    Raises:
        KeyError: si al archivo le falta alguna de TIPO, FUENTE, CLAVE_A, CLAVE_B o DECISION.
        ValueError: si un XLSX no tiene hoja DECISIONES ni REVISION, o si hay filas con TIPO o
            DECISION no válidos (el mensaje muestra las primeras).
    """
    piezas = []
    for orden, ruta in enumerate(rutas or ()):
        p = Path(ruta)
        if not p.exists():
            logger.warning("archivo de decisiones no encontrado, se omite: %s", p)
            continue
        r = _leer_xlsx(p) if p.suffix.lower() in (".xlsx", ".xlsm") else _leer_csv(p)
        r = _normalizar_columnas(r, p)
        r = _normalizar_valores(r, p, orden)
        _validar(r, p)
        piezas.append(r)
    if not piezas:
        return pd.DataFrame(columns=[*COLUMNAS_DECISION, "ARCHIVO", "ORDEN"])
    return pd.concat(piezas, ignore_index=True)


def plantilla_decisiones(ruta: str | Path) -> Path:
    """Escribe un CSV con las columnas esperadas y dos filas de EJEMPLO (empresas inventadas).

    Una es de ``VINCULO`` (ancla ↔ destino) y la otra de ``DUPLICADO`` (dos grafías de la ancla).
    """
    # Hallazgo F3: el docstring original decía «CSV vacío ... y dos filas de ejemplo comentadas», pero
    # las dos filas se escriben como decisiones válidas (no están comentadas ni vacías): si quien llena
    # la plantilla no las borra, entran como decisiones. Aquí solo se cambian los nombres reales.
    ej = pd.DataFrame(
        [
            [
                "VINCULO",
                "ORBIS",
                "España|GALLETAS ZELVORA, S.A.,",
                "GALLETAS ZELVORA, S.A.,",
                "ESB9990001",
                "GALLETAS ZELVORA, SA",
                "EMPRESA",
                "iniciales",
                "2026-09-29",
                "Mismo nombre y web zelvora.es",
            ],
            [
                "DUPLICADO",
                "EXPORTADORES",
                "España|PLVK SLU",
                "PLVK SLU",
                "España|PELUVIKA SLU",
                "PELUVIKA SLU",
                "MISMA_ENTIDAD",
                "iniciales",
                "2026-09-29",
                "Abreviatura de aduana de PELUVIKA",
            ],
        ],
        columns=COLUMNAS_DECISION,
    )
    ruta = Path(ruta)
    ej.to_csv(ruta, index=False, encoding="utf-8-sig")
    return ruta
