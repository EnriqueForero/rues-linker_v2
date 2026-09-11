"""testing.datos_sinteticos — corpus sintético realista con verdad conocida.

v0.13.0 (N6). Generador DETERMINISTA de razones sociales colombianas con los
modos de error que producen duplicados reales:

    - typos carácter a carácter (swap, borrado, reemplazo, inserción),
    - variantes FONÉTICAS del español (B/V, S/Z, C(e,i)/S, LL/Y, H muda),
    - sufijos legales alternos (S A S ↔ SAS ↔ S.A.S. ↔ LTDA),
    - reordenamiento de tokens,
    - NIT faltante (régimen SIN_NIT), teléfono/email como corroboración.

Cada fila lleva ``ID_ENTIDAD`` (la verdad): dos filas con el mismo valor son
la misma empresa real. Sirve para medir precision/recall/F1 de cualquier
ruta del paquete sin depender de datos reales — con la advertencia de
siempre (H8): un sintético calibra mecánica, no reemplaza GT real.

Uso::

    from record_linkage.testing.datos_sinteticos import generar_corpus, metricas_pairwise
    df = generar_corpus(n_entidades=1000, seed=42)
    res = rl.dedupe_esquema(df, esquema)
    m = metricas_pairwise(res.correlativa["ID_GRUPO"], df["ID_ENTIDAD"])
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

_PALABRAS = [
    "COMERCIALIZADORA",
    "DISTRIBUIDORA",
    "INVERSIONES",
    "TEXTILES",
    "ALIMENTOS",
    "CAFETALES",
    "MINERALES",
    "PLASTICOS",
    "FERRETERIA",
    "QUIMICOS",
    "FARMACEUTICA",
    "CONSTRUCTORA",
    "EDITORIAL",
    "METALICAS",
    "ELECTRICOS",
    "TRANSPORTES",
    "LOGISTICA",
    "AGROPECUARIA",
    "PESQUERA",
    "MADERAS",
    "CALZADO",
    "CONFECCIONES",
    "DULCES",
    "LACTEOS",
    "CARNICOS",
    "HARINAS",
    "EMPAQUES",
    "VIDRIOS",
    "ANDINA",
    "PACIFICO",
    "CARIBE",
    "BOLIVAR",
    "SANTANDER",
    "ANTIOQUIA",
    "CUNDINAMARCA",
    "BOYACA",
    "VALLE",
    "CAUCA",
    "NARINO",
    "TOLIMA",
    "HUILA",
    "QUINDIO",
    "CALDAS",
    "RISARALDA",
    "ATLANTICO",
    "MAGDALENA",
    "RODRIGUEZ",
    "GONZALEZ",
    "VASQUEZ",
    "GUTIERREZ",
    "HERNANDEZ",
    "JIMENEZ",
    "VILLAMIZAR",
    "ZAPATA",
    "DORADO",
    "REAL",
    "IMPERIAL",
    "MODERNO",
    "CLASICO",
    "GLOBAL",
    "EXPRESS",
    "UNIDO",
    "CENTRAL",
    "MAYOR",
]

_SUFIJOS = ["S A S", "SAS", "S.A.S.", "LTDA", "LIMITADA", "SA", "S.A.", "Y CIA", "E U"]

_CIUDADES = [
    "BOGOTA",
    "MEDELLIN",
    "CALI",
    "BARRANQUILLA",
    "CARTAGENA",
    "BUCARAMANGA",
    "PEREIRA",
    "MANIZALES",
    "CUCUTA",
    "IBAGUE",
    "SANTA",
    "MARTA",
    "VILLAVICENCIO",
    "PASTO",
    "MONTERIA",
    "NEIVA",
]

#: Sustituciones fonéticas del español (par confundible, en ambos sentidos).
_FONETICAS = [("V", "B"), ("Z", "S"), ("CI", "SI"), ("CE", "SE"), ("LL", "Y"), ("H", "")]


def _typo(rng: np.random.Generator, texto: str) -> str:
    """Un typo realista en posición alfabética aleatoria."""
    pos_validas = [i for i, c in enumerate(texto) if c.isalpha()]
    if not pos_validas:
        return texto
    i = int(rng.choice(pos_validas))
    op = int(rng.integers(0, 4))
    letras = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if op == 0 and i + 1 < len(texto) and texto[i + 1].isalpha():  # swap
        return texto[:i] + texto[i + 1] + texto[i] + texto[i + 2 :]
    if op == 1 and len(texto) > 4:  # borrado
        return texto[:i] + texto[i + 1 :]
    if op == 2:  # reemplazo
        return texto[:i] + letras[int(rng.integers(0, 26))] + texto[i + 1 :]
    return texto[:i] + letras[int(rng.integers(0, 26))] + texto[i:]  # inserción


def _variante_fonetica(rng: np.random.Generator, texto: str) -> str:
    """Aplica UNA confusión ortográfica típica del español, si aplica."""
    orden = rng.permutation(len(_FONETICAS))
    for k in orden:
        a, b = _FONETICAS[int(k)]
        if a in texto:
            return texto.replace(a, b, 1)
        if b and b in texto:
            return texto.replace(b, a, 1)
    return texto


def generar_corpus(
    n_entidades: int = 1_000,
    *,
    max_duplicados: int = 3,
    p_typo: float = 0.30,
    p_fonetico: float = 0.25,
    p_reorden: float = 0.15,
    p_sin_nit: float = 0.20,
    p_sufijo_distinto: float = 0.60,
    seed: int = 42,
) -> pd.DataFrame:
    """Genera un corpus con duplicados perturbados y verdad conocida.

    Args:
        n_entidades: empresas reales distintas.
        max_duplicados: registros EXTRA por entidad (uniforme en [0, max]).
        p_typo / p_fonetico / p_reorden: probabilidad de cada perturbación
            del nombre en un duplicado (independientes, componibles).
        p_sin_nit: probabilidad de que un duplicado pierda el NIT ('').
        p_sufijo_distinto: probabilidad de cambiar el sufijo legal.
        seed: determinismo total (mismo seed → mismo corpus, bit a bit).

    Returns:
        DataFrame con columnas RAZON_SOCIAL, NIT, CIUDAD, TELEFONO, EMAIL e
        ID_ENTIDAD (la verdad), en orden aleatorio reproducible.
    """
    rng = np.random.default_rng(seed)
    filas: list[tuple[str, str, str, str, str, int]] = []
    for ent in range(n_entidades):
        n_tokens = int(rng.integers(2, 4))
        base = " ".join(rng.choice(_PALABRAS, size=n_tokens, replace=False))
        sufijo = _SUFIJOS[int(rng.integers(0, len(_SUFIJOS)))]
        nit = str(800_000_000 + ent * 7 + int(rng.integers(0, 7)))
        ciudad = _CIUDADES[int(rng.integers(0, len(_CIUDADES)))]
        telefono = f"3{int(rng.integers(10**8, 10**9))}"
        email = f"contacto@{base.split()[0].lower()}{ent}.com"
        filas.append((f"{base} {sufijo}", nit, ciudad, telefono, email, ent))

        for _dup in range(int(rng.integers(0, max_duplicados + 1))):
            nombre = base
            if rng.random() < p_reorden and n_tokens >= 2:
                toks = nombre.split()
                orden = rng.permutation(len(toks))
                nombre = " ".join(toks[int(i)] for i in orden)
            if rng.random() < p_fonetico:
                nombre = _variante_fonetica(rng, nombre)
            if rng.random() < p_typo:
                nombre = _typo(rng, nombre)
            suf = (
                _SUFIJOS[int(rng.integers(0, len(_SUFIJOS)))]
                if rng.random() < p_sufijo_distinto
                else sufijo
            )
            nit_dup = "" if rng.random() < p_sin_nit else nit
            filas.append((f"{nombre} {suf}", nit_dup, ciudad, telefono, email, ent))

    df = pd.DataFrame(
        filas, columns=["RAZON_SOCIAL", "NIT", "CIUDAD", "TELEFONO", "EMAIL", "ID_ENTIDAD"]
    )
    return df.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def metricas_pairwise(pred, verdad) -> dict[str, float]:
    """Precision/recall/F1 PAIRWISE entre una partición predicha y la verdad.

    Definición estándar de record linkage: un "par verdadero" es todo par de
    filas con el mismo valor de ``verdad``; un "par predicho", todo par con
    la misma etiqueta en ``pred``.
    """
    pred = np.asarray(pred)
    verdad = np.asarray(verdad)
    if len(pred) != len(verdad):
        raise ValueError(f"longitudes distintas: {len(pred)} vs {len(verdad)}")

    def _pares(etiquetas: np.ndarray) -> set[tuple[int, int]]:
        grupos: dict = {}
        for idx, g in enumerate(etiquetas):
            grupos.setdefault(g, []).append(idx)
        return {p for m in grupos.values() for p in combinations(m, 2)}

    p_pred, p_true = _pares(pred), _pares(verdad)
    tp = len(p_pred & p_true)
    precision = tp / len(p_pred) if p_pred else 1.0
    recall = tp / len(p_true) if p_true else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    # Sin redondeo: una función de métricas devuelve precisión completa;
    # el redondeo es responsabilidad de la capa de presentación.
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "pares_predichos": len(p_pred),
        "pares_verdaderos": len(p_true),
        "tp": tp,
    }
