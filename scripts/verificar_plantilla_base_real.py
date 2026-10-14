#!/usr/bin/env python
"""verificar_plantilla_base_real.py — Corre el motor sobre la plantilla (o sobre una base real con
su misma estructura), dice caso por caso qué hizo y muestrea los pares que se van a etiquetar.

Lee las hojas ``FUENTE_*`` (del Excel, o de los CSV ``plantilla_fuente_*.csv`` de una carpeta),
corre ``record_linkage.linkage`` con ``col_nit="NIT"``, ``col_name="RAZON_SOCIAL"`` y
``col_ciudad="MUNICIPIO"`` y, según lo que se pida:

- si el libro trae ``VERDAD_GRUPOS`` y ``CASOS``, compara la partición predicha con la verdadera
  según lo que la hoja CASOS dice que debe hacer el motor y calcula precisión y recall por
  pares; sin esas hojas (una base real recién estructurada) solo corre el motor;
- ``--correlativa`` guarda la correlativa en parquet, para ``scripts/medir_verdad_real.py
  --col-id ID_REGISTRO_FUENTE``;
- ``--pares-a-etiquetar`` muestrea los pares para los anotadores, repartidos por régimen (los
  dos con identificador / ninguno / mixto) y por banda de puntaje, en dos archivos: el de los
  anotadores (los dos registros y nada del motor) y ``<nombre>_bandas.csv`` (de qué banda salió
  cada par), que se pega al archivo final;
- ``--banco`` escribe el CSV plano de ``scripts/banco.py`` (``construir_banco``: VERDAD_GRUPOS
  más las hojas FUENTE_*).

Las hojas de fuente se reconocen por el prefijo ``FUENTE_`` (FUENTE_CRM, FUENTE_DIAN…): renombrar,
agregar o quitar fuentes no exige tocar el script. Las bandas salen de lo que el motor hizo de
verdad (``L3_scoring/scored.db`` y ``L2_lsh_candidates/candidates.db`` del directorio de trabajo):

- ``unido_holgado``: el motor unió el par con puntaje ≥ umbral + MARGEN_BANDA (o por identificador);
- ``unido_borde``: lo unió con puntaje entre el umbral y umbral + MARGEN_BANDA;
- ``no_unido_borde``: lo consideró candidato y no lo unió (puntaje por debajo del umbral, o por
  encima pero vetado por identificador distinto o por la cobertura);
- ``no_unido_lejos``: nunca fue candidato (par de control al azar).

La lectura de las hojas, ``correr_motor`` y la evaluación por casos y por pares viven en
``record_linkage.evaluation.plantilla_base_real`` (desde 2026-10-14; antes aquí): este script
las importa y añade el muestreo de pares y el CSV del banco.

Qué debe hacer el motor según CASOS: ``unir`` = todos los registros de cada grupo verdadero del
caso quedan en un mismo grupo predicho; ``no unir`` = ningún par de registros de grupos verdaderos
distintos del caso queda en el mismo grupo predicho; ``revisar`` = el motor no une solo; el caso
``singleton`` acierta si cada registro queda solo. Los fallos no se ocultan: la plantilla también
sirve para mostrar lo que el motor no sabe hacer hoy.

Uso::

    python scripts/verificar_plantilla_base_real.py --datos data/ejemplos \\
        --dir-trabajo /tmp/plantilla_trabajo --salida docs/evidencia/plantilla_base_real_casos.json \\
        --correlativa /tmp/plantilla_trabajo/correlativa.parquet
    python scripts/verificar_plantilla_base_real.py --datos mi_base.xlsx --dir-trabajo salida/trabajo \\
        --correlativa salida/correlativa.parquet --pares-a-etiquetar salida/pares_a_etiquetar.csv \\
        --n-pares 500 --banco salida/banco.csv --estrato MI_LOTE
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from generar_plantilla_base_real import (
    BANDAS,
    COLUMNAS_BANDAS,
    COLUMNAS_PARES_A_ETIQUETAR,
    MARGEN_BANDA,
    construir_banco,
)

# La lectura de las hojas, el motor y la medición por casos y pares viven en el paquete (una
# sola implementación, compartida con el notebook 13 y scripts/multicriterio_plantilla.py).
from record_linkage.evaluation.plantilla_base_real import (
    COL_ID,
    correr_motor,
    evaluar_casos,
    leer_plantilla,
    metricas_pares,
    tabla_markdown,
)
from record_linkage.evaluation.verdad_real import regimen_de_los_pares
from record_linkage.salida.completar import RUTA_SCORED_DB

#: Los candidatos de L2 (la misma ruta que lee ``reporting/time_estimator.py``).
RUTA_CANDIDATOS_DB = Path("L2_lsh_candidates") / "candidates.db"
REGIMENES: tuple[str, ...] = ("CON_ID", "SIN_ID", "MIXTO")


# ---------------------------------------------------------------------------
# Muestreo de pares para etiquetar
# ---------------------------------------------------------------------------


def _leer_pares_sqlite(ruta: Path, consulta: str) -> pd.DataFrame:
    if not ruta.is_file():
        raise FileNotFoundError(
            f"No existe {ruta}: el muestreo necesita el directorio de trabajo de la MISMA corrida "
            "del motor (--dir-trabajo); corra el motor y muestree en la misma llamada."
        )
    con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(consulta, con)
    finally:
        con.close()


def _umbral_del_motor(ruta_scored: Path) -> float:
    meta = _leer_pares_sqlite(ruta_scored, "SELECT key, value FROM metadata")
    valores = dict(zip(meta["key"], meta["value"], strict=True))
    if "threshold" not in valores:
        raise RuntimeError(f"{ruta_scored} no registra el umbral (metadata.threshold)")
    return float(valores["threshold"])


def _clave_ordenada(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return np.minimum(a, b), np.maximum(a, b)


def _pares_de_control(
    rng: np.random.Generator,
    n: int,
    lado_a: np.ndarray,
    lado_b: np.ndarray,
    grupo_por_pos: np.ndarray,
    ya_vistos: set[tuple[int, int]],
) -> pd.DataFrame:
    """``n`` pares al azar (uno de ``lado_a``, otro de ``lado_b``) de grupos distintos que nunca
    fueron candidatos. Se tiran lotes vectorizados y se filtran: no hay bucle por par."""
    elegidos: dict[tuple[int, int], None] = {}
    for _ in range(20):
        if len(elegidos) >= n or len(lado_a) == 0 or len(lado_b) == 0:
            break
        a = rng.choice(lado_a, size=max(4 * n, 64))
        b = rng.choice(lado_b, size=a.size)
        lo, hi = _clave_ordenada(a, b)
        validos = (lo != hi) & (grupo_por_pos[lo] != grupo_por_pos[hi])
        for par in zip(lo[validos].tolist(), hi[validos].tolist(), strict=True):
            if par not in ya_vistos and par not in elegidos:
                elegidos[par] = None
                if len(elegidos) >= n:
                    break
    return pd.DataFrame(list(elegidos), columns=["POS_1", "POS_2"])


def muestrear_pares(
    correlativa: pd.DataFrame, dir_trabajo: Path, n_pares: int, semilla: int = 42
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pares para etiquetar, repartidos por régimen y banda de puntaje, desde lo que el motor hizo.

    Devuelve ``(para_anotar, bandas)``: el primero con las columnas de
    ``COLUMNAS_PARES_A_ETIQUETAR`` (los dos registros y las columnas vacías IS_MATCH, ANOTADOR y
    COMENTARIO; nada del motor, en orden barajado) y el segundo con ``COLUMNAS_BANDAS``. Los ids son
    ``ID_REGISTRO_FUENTE``, los mismos que mide ``scripts/medir_verdad_real.py --col-id``.

    Las cuotas se reparten por turnos entre las 12 celdas (3 regímenes × 4 bandas) hasta llegar a
    ``n_pares`` o agotar las celdas; una celda vacía (por ejemplo SIN_ID × unido_holgado en una base
    pequeña) cede su cuota a las demás.
    """
    if n_pares < 1:
        raise ValueError("n_pares debe ser ≥ 1")
    for columna in ("ORIGINAL_INDEX", "ID_GRUPO", COL_ID):
        if columna not in correlativa.columns:
            raise ValueError(f"la correlativa no trae {columna}")
    pos = correlativa["ORIGINAL_INDEX"].to_numpy(dtype=np.int64)
    if len(np.unique(pos)) != len(pos) or pos.min() < 0:
        raise RuntimeError("ORIGINAL_INDEX no es único: la correlativa no es la del motor")
    n_registros = int(pos.max()) + 1
    grupo_por_pos = np.full(n_registros, -1, dtype=np.int64)
    grupo_por_pos[pos] = pd.factorize(correlativa["ID_GRUPO"].astype(str))[0]
    id_por_pos = np.full(n_registros, "", dtype=object)
    id_por_pos[pos] = correlativa[COL_ID].astype(str).to_numpy()
    metodo = correlativa["METODO_UNION"] if "METODO_UNION" in correlativa.columns else None
    por_identificador = np.zeros(n_registros, dtype=bool)
    if metodo is not None:
        por_identificador[pos] = (metodo.astype(str) == "identificador").to_numpy()

    ruta_scored = dir_trabajo / RUTA_SCORED_DB
    umbral = _umbral_del_motor(ruta_scored)
    puntuados = _leer_pares_sqlite(ruta_scored, "SELECT idx_0, idx_1, score FROM scored_pairs")
    candidatos = _leer_pares_sqlite(
        dir_trabajo / RUTA_CANDIDATOS_DB, "SELECT idx_0, idx_1 FROM candidate_pairs"
    )
    lo, hi = _clave_ordenada(
        puntuados["idx_0"].to_numpy(dtype=np.int64), puntuados["idx_1"].to_numpy(dtype=np.int64)
    )
    puntajes = (
        pd.DataFrame({"POS_1": lo, "POS_2": hi, "PUNTAJE": puntuados["score"].to_numpy()})
        .groupby(["POS_1", "POS_2"], as_index=False)["PUNTAJE"]
        .max()
    )
    lo, hi = _clave_ordenada(
        candidatos["idx_0"].to_numpy(dtype=np.int64), candidatos["idx_1"].to_numpy(dtype=np.int64)
    )
    candidatos = pd.DataFrame({"POS_1": lo, "POS_2": hi}).drop_duplicates()
    candidatos = candidatos[(candidatos["POS_1"] != candidatos["POS_2"])]
    candidatos = candidatos[
        (candidatos["POS_1"] < n_registros) & (candidatos["POS_2"] < n_registros)
    ]

    # Unidos: todos los pares de un mismo grupo predicho (los une el motor, directa o transitivamente).
    marco = pd.DataFrame({"POS": pos, "GRUPO": grupo_por_pos[pos]})
    unidos = marco.merge(marco, on="GRUPO", suffixes=("_1", "_2"))
    unidos = unidos[unidos["POS_1"] < unidos["POS_2"]][["POS_1", "POS_2"]]
    unidos = unidos.merge(puntajes, on=["POS_1", "POS_2"], how="left")
    ambos_id = por_identificador[unidos["POS_1"]] & por_identificador[unidos["POS_2"]]
    holgado = (unidos["PUNTAJE"] >= umbral + MARGEN_BANDA) | (unidos["PUNTAJE"].isna() & ambos_id)
    unidos["BANDA_PUNTAJE"] = np.where(holgado, BANDAS[0], BANDAS[1])
    unidos["UNIDO_POR_EL_MOTOR"] = 1

    # Candidatos no unidos: el motor los consideró y los dejó separados.
    distintos = grupo_por_pos[candidatos["POS_1"]] != grupo_por_pos[candidatos["POS_2"]]
    no_unidos = candidatos[distintos].merge(puntajes, on=["POS_1", "POS_2"], how="left")
    no_unidos["BANDA_PUNTAJE"] = BANDAS[2]
    no_unidos["UNIDO_POR_EL_MOTOR"] = 0

    vistos = set(zip(candidatos["POS_1"].tolist(), candidatos["POS_2"].tolist(), strict=True))
    vistos |= set(zip(unidos["POS_1"].tolist(), unidos["POS_2"].tolist(), strict=True))
    identificador = pd.Series(
        correlativa["NIT"].to_numpy() if "NIT" in correlativa.columns else "",
        index=correlativa[COL_ID].astype(str),
    )
    # Pares de control por régimen: con id con con id, sin id con sin id, y mixtos.
    tiene_id = (
        pd.Series(identificador.to_numpy(), index=pos).fillna("").astype(str).str.strip().ne("")
    )
    con_id = tiene_id[tiene_id].index.to_numpy(dtype=np.int64)
    sin_id = tiene_id[~tiene_id].index.to_numpy(dtype=np.int64)
    rng = np.random.default_rng(semilla)
    cuota_lejos = max(1, -(-n_pares // (len(BANDAS) * len(REGIMENES))))
    lejos = pd.concat(
        [
            _pares_de_control(rng, cuota_lejos, lado_a, lado_b, grupo_por_pos, vistos)
            for lado_a, lado_b in ((con_id, con_id), (sin_id, sin_id), (con_id, sin_id))
        ],
        ignore_index=True,
    ).drop_duplicates()
    lejos["PUNTAJE"] = np.nan
    lejos["BANDA_PUNTAJE"] = BANDAS[3]
    lejos["UNIDO_POR_EL_MOTOR"] = 0

    todos = pd.concat([unidos, no_unidos, lejos], ignore_index=True)
    todos["ID_1"] = id_por_pos[todos["POS_1"]]
    todos["ID_2"] = id_por_pos[todos["POS_2"]]
    todos["REGIMEN"] = regimen_de_los_pares(todos, identificador).to_numpy()

    # Cuotas por turnos entre las 12 celdas, cada una barajada con la semilla.
    todos = todos.sample(frac=1.0, random_state=int(rng.integers(0, 2**31 - 1))).reset_index(
        drop=True
    )
    celdas = {
        clave: sub.index.to_list() for clave, sub in todos.groupby(["REGIMEN", "BANDA_PUNTAJE"])
    }
    orden = [(r, b) for b in BANDAS for r in REGIMENES if (r, b) in celdas]
    elegidos: list[int] = []
    while len(elegidos) < n_pares and any(celdas[c] for c in orden):
        for celda in orden:
            if celdas[celda] and len(elegidos) < n_pares:
                elegidos.append(celdas[celda].pop())
    muestra = todos.loc[elegidos].reset_index(drop=True)

    datos = correlativa.set_index(correlativa[COL_ID].astype(str))
    for columna, origen in (
        ("RAZON_SOCIAL", "RAZON_SOCIAL"),
        ("NIT", "NIT"),
        ("MUNICIPIO", "CIUDAD"),
    ):
        serie = datos[origen].fillna("").astype(str) if origen in datos.columns else None
        for lado in ("1", "2"):
            muestra[f"{columna}_{lado}"] = (
                muestra[f"ID_{lado}"].map(serie).to_numpy() if serie is not None else ""
            )
    for columna in ("IS_MATCH", "ANOTADOR", "COMENTARIO"):
        muestra[columna] = ""
    muestra["PUNTAJE"] = muestra["PUNTAJE"].round(4)
    para_anotar = muestra[list(COLUMNAS_PARES_A_ETIQUETAR)].copy()
    bandas = muestra[list(COLUMNAS_BANDAS)].copy()
    return para_anotar, bandas


def _ruta_bandas(ruta: Path) -> Path:
    return ruta.with_name(f"{ruta.stem}_bandas{ruta.suffix or '.csv'}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--datos", type=Path, default=RAIZ / "data" / "ejemplos", help="carpeta de CSV o el .xlsx"
    )
    p.add_argument("--dir-trabajo", type=Path, default=Path("/tmp/plantilla_trabajo"))
    p.add_argument("--salida", type=Path, help="JSON con la tabla por caso y las métricas")
    p.add_argument(
        "--correlativa", type=Path, help="parquet de la correlativa (para medir_verdad_real.py)"
    )
    p.add_argument(
        "--pares-a-etiquetar",
        type=Path,
        help="CSV para los anotadores; las bandas van aparte en <nombre>_bandas.csv",
    )
    p.add_argument("--n-pares", type=int, default=500, help="cuántos pares muestrear")
    p.add_argument("--semilla", type=int, default=42, help="semilla del muestreo")
    p.add_argument(
        "--banco", type=Path, help="CSV plano para scripts/banco.py (exige VERDAD_GRUPOS)"
    )
    p.add_argument("--estrato", default="PLANTILLA", help="valor de ESTRATO en el CSV del banco")
    args = p.parse_args(argv)

    libro = leer_plantilla(args.datos)
    print("Fuentes: " + ", ".join(f"{f} ({len(df)})" for f, df in libro.fuentes.items()))
    correlativa = correr_motor(libro.fuentes, args.dir_trabajo)
    n_grupos = int(correlativa["ID_GRUPO"].nunique())
    tabla = metricas = None
    if libro.verdad is not None and libro.casos is not None:
        tabla = evaluar_casos(correlativa, libro.verdad, libro.casos)
        metricas = metricas_pares(correlativa, libro.verdad)
        print(
            f"Registros: {len(correlativa)} · grupos predichos: {n_grupos} · grupos verdaderos: "
            f"{libro.verdad['ID_GRUPO'].nunique()}"
        )
        print("Pares contra VERDAD_GRUPOS: " + ", ".join(f"{k}={v}" for k, v in metricas.items()))
        print(tabla_markdown(tabla))
        print(f"Casos acertados: {int(tabla['ACERTO'].sum())}/{len(tabla)}")
    else:
        print(
            f"Registros: {len(correlativa)} · grupos predichos: {n_grupos} · sin VERDAD_GRUPOS y "
            "CASOS no hay tabla por caso (modo base real: solo se corrió el motor)"
        )
    if args.correlativa:
        args.correlativa.parent.mkdir(parents=True, exist_ok=True)
        correlativa.to_parquet(args.correlativa, index=False)
        print(f"Correlativa guardada en {args.correlativa}")
    if args.pares_a_etiquetar:
        para_anotar, bandas = muestrear_pares(
            correlativa, args.dir_trabajo, args.n_pares, args.semilla
        )
        args.pares_a_etiquetar.parent.mkdir(parents=True, exist_ok=True)
        para_anotar.to_csv(args.pares_a_etiquetar, index=False, encoding="utf-8")
        ruta_bandas = _ruta_bandas(args.pares_a_etiquetar)
        bandas.to_csv(ruta_bandas, index=False, encoding="utf-8")
        reparto = bandas.groupby(["BANDA_PUNTAJE", "REGIMEN"]).size()
        print(
            f"Pares para etiquetar: {len(para_anotar)} en {args.pares_a_etiquetar} (para los "
            f"anotadores) y {ruta_bandas} (bandas; se pega al archivo final). Reparto: "
            + ", ".join(f"{b}/{r}={n}" for (b, r), n in reparto.items())
        )
    if args.banco:
        if libro.verdad is None:
            raise SystemExit(
                "--banco necesita la hoja VERDAD_GRUPOS (ID_REGISTRO_FUENTE, FUENTE, ID_GRUPO)"
            )
        banco = construir_banco(libro.fuentes, libro.verdad, estrato=args.estrato)
        args.banco.parent.mkdir(parents=True, exist_ok=True)
        banco.to_csv(args.banco, index=False, encoding="utf-8", lineterminator="\n")
        print(f"CSV del banco guardado en {args.banco} ({len(banco)} filas)")
    if args.salida:
        import record_linkage

        salida: dict[str, object] = {
            "version": record_linkage.__version__,
            "datos": str(args.datos),
            "registros": len(correlativa),
            "grupos_predichos": n_grupos,
        }
        if tabla is not None and metricas is not None and libro.verdad is not None:
            salida.update(
                {
                    "grupos_verdaderos": int(libro.verdad["ID_GRUPO"].nunique()),
                    "pares": metricas,
                    "casos_acertados": int(tabla["ACERTO"].sum()),
                    "casos_total": len(tabla),
                    "por_caso": tabla.to_dict(orient="records"),
                }
            )
        args.salida.parent.mkdir(parents=True, exist_ok=True)
        args.salida.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Resultado guardado en {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
