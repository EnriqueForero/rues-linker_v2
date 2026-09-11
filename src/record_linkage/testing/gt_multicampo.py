"""testing.gt_multicampo — generador de ground truth sintético v3 (F2.7).

Genera un dataset multicampo con identidad verdadera conocida
(``ID_ENTIDAD``) y perturbaciones realistas POR TIPO, para medir el motor
multicampo en slices por campo. Determinista: la semilla fija reproduce el
mismo dataset bit a bit (contrato de determinismo, F0.6).

Perturbaciones por tipo:
    - nombre: variante de sufijo legal, typo de un carácter, orden de tokens.
    - identificador (NIT): dígito de verificación distinto, o faltante.
    - teléfono: prefijo país, espacios/guiones, faltante.
    - email: mismo dominio con typo en local-part, o faltante.
    - dirección: abreviatura vial equivalente.
    - ciudad: con/sin tilde.
    - fecha: corrimiento de pocos días.
    - geo: jitter dentro del radio.
    - numérico: ruido relativo pequeño.

Incluye controles negativos (F2 amplía los del diagnóstico): entidades
DISTINTAS con nombre genérico/sectorial muy parecido que NO deben fusionarse.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import pandas as pd

_SUFIJOS = ["SAS", "S.A.S.", "S A S", "LTDA", "S.A.", "& CIA"]
_CIUDADES = [
    ("BOGOTA", "BOGOTÁ", 4.7110, -74.0721),
    ("MEDELLIN", "MEDELLÍN", 6.2442, -75.5812),
    ("CALI", "CALI", 3.4516, -76.5320),
    ("BARRANQUILLA", "BARRANQUILLA", 10.9685, -74.7813),
]
_RAICES = [
    "COMERCIALIZADORA ANDINA",
    "DISTRIBUCIONES DEL ORIENTE",
    "INVERSIONES PACIFICO",
    "SERVICIOS INTEGRALES",
    "TECNOLOGIA APLICADA",
    "GRUPO EMPRESARIAL DELTA",
    "MANUFACTURAS DEL NORTE",
    "LOGISTICA GLOBAL",
    "CONSULTORES ASOCIADOS",
    "PRODUCTOS NATURALES",
]
_ABREV = {"CALLE": "CL", "CARRERA": "CRA", "AVENIDA": "AV"}


@dataclass
class ConfigGT:
    """Configuración del generador de GT multicampo v3."""

    n_entidades: int = 120
    max_registros_por_entidad: int = 3
    prob_faltante: float = 0.25
    prob_typo: float = 0.4
    n_negativos_genericos: int = 15
    seed: int = 42
    campos: tuple[str, ...] = field(
        default=(
            "NIT",
            "RAZON_SOCIAL",
            "TELEFONO",
            "EMAIL",
            "DIRECCION",
            "CIUDAD",
            "FECHA_CONST",
            "VENTAS",
            "LAT",
            "LON",
        )
    )


def _typo(texto: str, rng: random.Random) -> str:
    if len(texto) < 4:
        return texto
    p = rng.randrange(1, len(texto) - 1)
    return texto[:p] + texto[p + 1 :]  # borra un carácter (typo común)


def _perturbar_nombre(raiz: str, rng: random.Random) -> str:
    nombre = f"{raiz} {rng.choice(_SUFIJOS)}"
    if rng.random() < 0.3:  # orden de tokens
        toks = raiz.split()
        if len(toks) > 1:
            rng.shuffle(toks)
            nombre = f"{' '.join(toks)} {rng.choice(_SUFIJOS)}"
    if rng.random() < 0.3:
        nombre = _typo(nombre, rng)
    return nombre


def generar_gt_multicampo(config: ConfigGT | None = None) -> pd.DataFrame:
    """Genera el DataFrame de GT multicampo v3 (determinista por ``seed``).

    Returns:
        DataFrame con las columnas de ``config.campos`` más ``ID_ENTIDAD``
        (identidad verdadera; ``-1`` marca los negativos, cada uno único).
    """
    cfg = config or ConfigGT()
    rng = random.Random(cfg.seed)
    filas: list[dict[str, object]] = []

    for ent in range(cfg.n_entidades):
        raiz = rng.choice(_RAICES) + f" {ent:03d}"
        nit_base = 900_000_000 + ent * 7
        dv = ent % 10
        tel_base = 601_0000000 + ent * 13
        dom = f"empresa{ent:03d}.com" if ent % 3 else ["gmail.com", "hotmail.com"][ent % 2]
        local = f"contacto{ent:03d}"
        ciudad_norm, ciudad_tilde, lat, lon = _CIUDADES[ent % len(_CIUDADES)]
        calle = rng.randrange(1, 150)
        num = rng.randrange(1, 99)
        anio = 2005 + (ent % 20)
        ventas = 1_000_000 * (ent + 1)

        n_reg = rng.randint(1, cfg.max_registros_por_entidad)
        for _ in range(n_reg):
            nit = f"{nit_base}{(dv + (1 if rng.random() < 0.2 else 0)) % 10}"
            if rng.random() < cfg.prob_faltante:
                nit = rng.choice(["", "0", "N/A"])
            tel = f"+57 {tel_base}" if rng.random() < 0.5 else str(tel_base)
            if rng.random() < cfg.prob_faltante:
                tel = ""
            mail = f"{_typo(local, rng) if rng.random() < 0.3 else local}@{dom}"
            if rng.random() < cfg.prob_faltante:
                mail = rng.choice(["", "sin correo"])
            via = "CALLE" if rng.random() < 0.5 else "CL"
            direccion = f"{via} {calle} # {num} - {rng.randrange(1, 99)}"
            ciudad = ciudad_tilde if rng.random() < 0.5 else ciudad_norm
            corr_dias = rng.randint(-3, 3)
            fecha = pd.Timestamp(year=anio, month=1 + ent % 12, day=1 + ent % 27) + pd.Timedelta(
                days=corr_dias
            )
            fecha_str = fecha.strftime("%d/%m/%Y" if rng.random() < 0.5 else "%Y-%m-%d")
            v = ventas * (1 + rng.uniform(-0.03, 0.03))
            jlat = lat + rng.uniform(-0.003, 0.003)
            jlon = lon + rng.uniform(-0.003, 0.003)
            filas.append(
                {
                    "NIT": nit,
                    "RAZON_SOCIAL": _perturbar_nombre(raiz, rng),
                    "TELEFONO": tel,
                    "EMAIL": mail,
                    "DIRECCION": direccion,
                    "CIUDAD": ciudad,
                    "FECHA_CONST": fecha_str,
                    "VENTAS": round(v, 2),
                    "LAT": round(jlat, 6),
                    "LON": round(jlon, 6),
                    "ID_ENTIDAD": ent,
                }
            )

    # Controles negativos: nombres genéricos casi iguales, entidades DISTINTAS.
    for k in range(cfg.n_negativos_genericos):
        base = rng.choice(["INVERSIONES", "COMERCIALIZADORA", "SERVICIOS", "GRUPO"])
        sector = rng.choice(["DEL VALLE", "ANDINA", "NACIONAL", "S A"])
        nit_base = 800_000_000 + k * 11
        filas.append(
            {
                "NIT": f"{nit_base}{k % 10}",
                "RAZON_SOCIAL": f"{base} {sector} {rng.choice(_SUFIJOS)}",
                "TELEFONO": str(602_0000000 + k),
                "EMAIL": f"info{k}@{['gmail.com', 'empresa.co'][k % 2]}",
                "DIRECCION": f"CRA {rng.randrange(1, 100)} # {k} - {k}",
                "CIUDAD": _CIUDADES[k % len(_CIUDADES)][0],
                "FECHA_CONST": f"2010-0{1 + k % 9}-15",
                "VENTAS": round(500_000 * (k + 1), 2),
                "LAT": round(_CIUDADES[k % len(_CIUDADES)][2] + 0.5, 6),
                "LON": round(_CIUDADES[k % len(_CIUDADES)][3] + 0.5, 6),
                "ID_ENTIDAD": -1,  # cada negativo es su propia entidad
            }
        )

    df = pd.DataFrame(filas)
    # Los negativos con ID -1 deben ser entidades distintas: reasignar únicos.
    mask_neg = df["ID_ENTIDAD"] == -1
    df.loc[mask_neg, "ID_ENTIDAD"] = range(cfg.n_entidades, cfg.n_entidades + int(mask_neg.sum()))
    return df.sample(frac=1.0, random_state=cfg.seed).reset_index(drop=True)
