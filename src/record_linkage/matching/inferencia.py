"""matching.inferencia — sugerencia asistida de EsquemaCampos (v0.13.0).

``sugerir_esquema(df)`` propone un esquema declarativo a partir de los
NOMBRES de columna, el CONTENIDO (muestreado) y el dtype — y se lo muestra
al usuario para que confirme o edite. Principio de la casa: *User Control >
Automation* — esto NUNCA corre solo dentro del motor; es un asistente que
produce un ``EsquemaCampos`` editable y una tabla de motivos auditable.

Orden de evidencia por columna:
    1. Patrones de NOMBRE (NIT/TAX → identificador; RAZON/COMPANY → nombre…)
    2. Verificaciones de CONTENIDO sobre una muestra (dígitos, '@', parseo de
       fechas, vocabulario booleano, separadores de conjunto, rangos lat/lon)
    3. dtype y cardinalidad (numérico, categórico)

Columnas que no alcanzan evidencia suficiente se OMITEN con motivo explícito
(mejor un esquema corto y correcto que uno largo y ruidoso).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from ..utils.output import safe_print as print
from .campos import CampoSpec, EsquemaCampos, TipoCampo
from .normalizadores import _BOOLEANOS

#: Filas de muestra por columna para las verificaciones de contenido.
_MUESTRA = 2_000

#: Peso por defecto por tipo sugerido (editable por el usuario en el esquema).
_PESOS: dict[TipoCampo, float] = {
    TipoCampo.IDENTIFICADOR: 3.0,
    TipoCampo.NOMBRE_EMPRESA: 2.0,
    TipoCampo.NOMBRE_PERSONA: 2.0,
    TipoCampo.TELEFONO: 1.5,
    TipoCampo.EMAIL: 1.5,
    TipoCampo.DIRECCION: 1.0,
    TipoCampo.JERARQUICO: 1.0,
    TipoCampo.GEO: 0.75,
    TipoCampo.FECHA: 0.75,
    TipoCampo.NUMERICO: 0.75,
    TipoCampo.CIUDAD: 0.5,
    TipoCampo.CATEGORICO: 0.5,
    TipoCampo.CONJUNTO: 0.5,
    TipoCampo.BOOLEANO: 0.5,
}

_RX = {
    "identificador": re.compile(r"NIT|TAX|RUT\b|RUC|CUIT|CEDULA|DOCUMENTO|DOC_?ID", re.I),
    "nombre_empresa": re.compile(
        r"RAZON|COMPANY|EMPRESA|BUSINESS|SUPPLIER|PROVEEDOR|CLIENTE", re.I
    ),
    "nombre_generico": re.compile(r"NOMBRE|NAME", re.I),
    "telefono": re.compile(r"TEL|PHONE|CEL|MOVIL|MOBILE", re.I),
    "email": re.compile(r"MAIL|CORREO", re.I),
    "direccion": re.compile(r"^DIR|ADDRESS|DOMICILIO", re.I),
    "ciudad": re.compile(r"CIUDAD|CITY|MUNICIPIO", re.I),
    "lat": re.compile(r"^LAT(ITUD)?$|_LAT$", re.I),
    "lon": re.compile(r"^LON(GITUD)?$|^LNG$|_LON$|_LNG$", re.I),
    "fecha": re.compile(r"FECHA|DATE|^FEC_", re.I),
    "jerarquico": re.compile(r"CIIU|^HS\b|SUBPARTIDA|PARTIDA|ARANCEL", re.I),
    "fila_id": re.compile(r"^(ID|INDEX|IDX|ROW_?ID|CONSECUTIVO)$", re.I),
}


@dataclass
class _Diagnostico:
    """Evidencia de contenido de una columna (sobre muestra no nula)."""

    n_no_nulos: int
    frac_digitos: float  # valores compuestos solo de dígitos (tras limpiar)
    len_digitos_mediana: float
    frac_arroba: float
    frac_fecha: float
    frac_numerica: float
    frac_decimal: float  # valores con parte decimal real ("10.5", "3,2")
    frac_booleana: float
    frac_separador: float
    cardinalidad: int
    frac_unicos: float
    tiene_espacios: float


def _diagnosticar(serie: pd.Series) -> _Diagnostico:
    s = serie.dropna().astype(str).str.strip()
    s = s[s != ""]
    if len(s) > _MUESTRA:
        s = s.sample(_MUESTRA, random_state=42)
    n = len(s)
    if n == 0:
        return _Diagnostico(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    digitos = s.str.replace(r"[\s\.\-]", "", regex=True)
    solo_digitos = digitos.str.fullmatch(r"\d+").fillna(False)
    with pd.option_context("mode.chained_assignment", None):
        frac_fecha = float(pd.to_datetime(s, errors="coerce", format="mixed").notna().mean())
    upper = s.str.upper()
    return _Diagnostico(
        n_no_nulos=n,
        frac_digitos=float(solo_digitos.mean()),
        len_digitos_mediana=float(digitos[solo_digitos].str.len().median() or 0.0),
        frac_arroba=float(s.str.contains("@", regex=False).mean()),
        frac_fecha=frac_fecha,
        frac_numerica=float(
            pd.to_numeric(s.str.replace(",", ".", regex=False), errors="coerce").notna().mean()
        ),
        frac_decimal=float(s.str.fullmatch(r"-?\d+[\.,]\d+").fillna(False).mean()),
        frac_booleana=float(upper.isin(set(_BOOLEANOS)).mean()),
        frac_separador=float(
            (s.str.contains(";", regex=False) | s.str.contains("|", regex=False)).mean()
        ),
        cardinalidad=int(s.nunique()),
        frac_unicos=float(s.nunique() / n),
        tiene_espacios=float(s.str.contains(" ", regex=False).mean()),
    )


def sugerir_esquema_detallado(
    df: pd.DataFrame,
    *,
    umbral_score: float = 0.60,
    min_concordancias: int = 1,
) -> tuple[EsquemaCampos, pd.DataFrame]:
    """Sugiere un ``EsquemaCampos`` y devuelve además la tabla de motivos.

    Returns:
        (esquema, motivos): ``motivos`` tiene columnas
        ``[columna, decision, tipo, motivo]`` — ``decision`` ∈
        {"incluida", "omitida"} — para revisión humana y auditoría.

    Raises:
        ValueError: si ninguna columna alcanza evidencia para el esquema.
    """
    campos: list[CampoSpec] = []
    filas: list[dict[str, str]] = []
    usadas_lon: set[str] = set()

    columnas = list(df.columns)

    def registrar(col: str, tipo: TipoCampo, motivo: str, **kwargs) -> None:
        campos.append(CampoSpec(col, tipo, peso=_PESOS[tipo], **kwargs))
        filas.append({"columna": col, "decision": "incluida", "tipo": tipo.value, "motivo": motivo})

    def omitir(col: str, motivo: str) -> None:
        filas.append({"columna": col, "decision": "omitida", "tipo": "-", "motivo": motivo})

    for col in columnas:
        if col in usadas_lon:
            continue
        d = _diagnosticar(df[col])
        nombre = str(col)

        if d.n_no_nulos == 0:
            omitir(col, "columna vacía")
            continue
        if _RX["fila_id"].fullmatch(nombre) and d.frac_unicos > 0.99:
            omitir(col, "identificador DE FILA (único por registro), no de entidad")
            continue

        # GEO: emparejar lat con su lon ANTES de que caiga como numérica.
        if _RX["lat"].search(nombre):
            pareja = next(
                (c for c in columnas if c not in usadas_lon and _RX["lon"].search(str(c))),
                None,
            )
            if pareja is not None:
                usadas_lon.add(pareja)
                registrar(
                    col, TipoCampo.GEO, f"lat/lon emparejada con '{pareja}'", columna_lon=pareja
                )
                continue

        if (
            _RX["identificador"].search(nombre)
            and d.frac_digitos >= 0.60
            and d.len_digitos_mediana >= 6
        ):
            registrar(
                col, TipoCampo.IDENTIFICADOR, "nombre tipo NIT/TAX y contenido de dígitos largos"
            )
        elif _RX["jerarquico"].search(nombre):
            registrar(col, TipoCampo.JERARQUICO, "nombre tipo CIIU/HS/subpartida")
        elif _RX["telefono"].search(nombre) and d.frac_digitos >= 0.50:
            registrar(col, TipoCampo.TELEFONO, "nombre tipo teléfono y contenido numérico")
        elif _RX["email"].search(nombre) or d.frac_arroba >= 0.50:
            registrar(col, TipoCampo.EMAIL, "nombre tipo correo o '@' en la mayoría de valores")
        elif _RX["direccion"].search(nombre):
            registrar(col, TipoCampo.DIRECCION, "nombre tipo dirección")
        elif _RX["ciudad"].search(nombre):
            registrar(col, TipoCampo.CIUDAD, "nombre tipo ciudad/municipio")
        elif _RX["nombre_empresa"].search(nombre) or (
            _RX["nombre_generico"].search(nombre) and d.tiene_espacios >= 0.30
        ):
            registrar(
                col, TipoCampo.NOMBRE_EMPRESA, "nombre tipo razón social y texto multi-palabra"
            )
        elif d.frac_booleana >= 0.95 and d.cardinalidad <= 4:
            registrar(col, TipoCampo.BOOLEANO, "vocabulario sí/no/true/false")
        elif _RX["fecha"].search(nombre) or d.frac_fecha >= 0.80:
            registrar(col, TipoCampo.FECHA, "nombre tipo fecha o >80% parsea como fecha")
        elif d.frac_separador >= 0.30:
            registrar(col, TipoCampo.CONJUNTO, "múltiples valores por celda (';' o '|')")
        elif d.frac_numerica >= 0.90 and (d.frac_decimal >= 0.30 or d.len_digitos_mediana <= 5):
            # Decimales reales, o enteros CORTOS (edad, empleados, año):
            # magnitudes continuas. Los dígitos LARGOS sin nombre de
            # identificador siguen siendo ambiguos y se omiten abajo.
            registrar(col, TipoCampo.NUMERICO, "contenido numérico continuo")
        elif d.frac_numerica >= 0.90 and d.len_digitos_mediana >= 6:
            omitir(
                col,
                "dígitos largos sin nombre de identificador: ambiguo (¿ID? ¿código?) — declárela a mano",
            )
        elif d.frac_unicos <= 0.05 and d.cardinalidad <= 100:
            registrar(col, TipoCampo.CATEGORICO, f"baja cardinalidad ({d.cardinalidad} valores)")
        else:
            omitir(col, "sin evidencia suficiente (texto libre o patrón no reconocido)")

    if not campos:
        raise ValueError(
            "Qué pasó: ninguna columna alcanzó evidencia para sugerir un esquema. "
            "Por qué importa: el motor necesita al menos un campo declarado. "
            "Qué hacer: construya el EsquemaCampos a mano con CampoSpec(...) "
            "o renombre las columnas a algo reconocible (NIT, RAZON_SOCIAL, ...)."
        )

    esquema = EsquemaCampos(
        campos=campos,
        umbral_score=umbral_score,
        min_concordancias=min_concordancias,
        nombre="sugerido",
    )
    motivos = pd.DataFrame(filas, columns=["columna", "decision", "tipo", "motivo"])
    return esquema, motivos


def sugerir_esquema(
    df: pd.DataFrame,
    *,
    umbral_score: float = 0.60,
    min_concordancias: int = 1,
    verbose: bool = True,
) -> EsquemaCampos:
    """Propone un ``EsquemaCampos`` para ``df`` y lo imprime para confirmación.

    El esquema devuelto es un PUNTO DE PARTIDA editable: ajuste pesos, políticas
    de faltantes o tipos antes de pasarlo a ``rl.dedupe_esquema``. La tabla
    impresa incluye el MOTIVO de cada decisión (y de cada omisión).

    Ejemplo:
        >>> import record_linkage as rl
        >>> esq = rl.sugerir_esquema(df)          # revise la tabla impresa
        >>> res = rl.dedupe_esquema(df, esq)
    """
    esquema, motivos = sugerir_esquema_detallado(
        df, umbral_score=umbral_score, min_concordancias=min_concordancias
    )
    if verbose:
        print("═" * 72)
        print("📋 ESQUEMA SUGERIDO (revíselo antes de usar — usted decide)")
        print("═" * 72)
        for _, fila in motivos.iterrows():
            marca = "✅" if fila["decision"] == "incluida" else "⏭️ "
            print(f"  {marca} {fila['columna']:<22} {fila['tipo']:<16} {fila['motivo']}")
        print("─" * 72)
        print(
            f"  {len(esquema.campos)} campos · umbral_score={esquema.umbral_score} · "
            f"min_concordancias={esquema.min_concordancias} · edite pesos/políticas a gusto"
        )
        print("═" * 72)
    return esquema
