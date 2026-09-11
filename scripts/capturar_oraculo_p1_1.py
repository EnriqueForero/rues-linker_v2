"""Oráculo de paridad para P1-1: captura outputs del scorer ACTUAL.

Antes de vectorizar, se ejecuta el scorer sobre un set diverso de pares y
se guardan los outputs exactos. La versión vectorizada DEBE reproducir
estos outputs bit-a-bit (tolerancia 1e-9). Ver MIGRATION_LOG §19.

Casos cubiertos:
    - Pares con NIT idéntico (dist=0)
    - Pares con NIT a distancia 1, 2, 3
    - Pares con NIT vacío en uno o ambos lados
    - Pares con nombres idénticos
    - Pares con nombres totalmente disjuntos
    - Pares con typos / órdenes distintos
    - Pares con valores nulos / 'nan'
    - Pares cuyo primer token coincide (bonus *1.05)

Nota sobre portabilidad del pickle (v3.2.2+):
    Todos los DataFrames se normalizan a `dtype=object` (datos y nombres
    de columnas) antes de serializar. Pandas 3.x almacena nombres de
    columnas como `StringDtype('pyarrow', NaN)`, cuya firma de __init__
    NO es compatible con pandas 2.2.x, rompiendo `pickle.load` con
    `TypeError: StringDtype.__init__() takes from 1 to 2 positional
    arguments but 3 were given`. Forzar `object` evita esta dependencia
    de versión y hace el oráculo bidireccionalmente portable.
    El scorer coerciona internamente strings con
    `pd.array(..., dtype='string').to_numpy(na_value='')`, por lo que el
    dtype del DataFrame de entrada NO afecta el cómputo (paridad
    bit-a-bit verificada).
"""

from __future__ import annotations

import logging
import os
import pickle
import platform
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from record_linkage.engine.scorer import VectorizedScorer


def _to_portable_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza un DataFrame a dtypes serializables entre pandas 2.2.x y 3.x.

    Convierte:
        - Columnas con dtype 'string'/'string[pyarrow]'/StringDtype → object
        - El propio Index de columnas (df.columns) → object

    El resto de dtypes (int64, float64, bool) se preservan: son estables
    entre versiones. La función NO muta el DataFrame original.

    Justificación: pickle de pandas serializa la estructura completa,
    incluyendo el constructor de cada dtype. Si en versión A el dtype
    StringDtype acepta (storage, na_value) y en versión B solo (storage),
    el pickle generado en A no carga en B. dtype=object es estable desde
    pandas 1.x hasta 3.x inclusive.
    """
    out = df.copy()
    # 1. Columnas (data): toda columna string-like a object. Numéricos y bools
    #    se preservan (son estables entre versiones de pandas).
    for col in out.columns:
        dt = out[col].dtype
        if pd.api.types.is_string_dtype(dt) or str(dt).startswith("string"):
            out[col] = out[col].astype(object)
    # 2. Index de columnas (nombres): pandas 3.x lo guarda como StringDtype.
    out.columns = pd.Index(list(out.columns), dtype=object)
    # 3. Index de filas: si es RangeIndex se preserva (es estable). Si es
    #    object/string también lo dejamos object explícito.
    if not isinstance(out.index, pd.RangeIndex):
        out.index = pd.Index(list(out.index), dtype=object)
    return out


def construir_dataset_oraculo() -> tuple[pd.DataFrame, np.ndarray]:
    """Construye un DataFrame y una lista de pares que cubren casos borde."""
    rows = [
        # 0-1: idénticos exactos (nombre y NIT)
        {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZONOBEL PINTUCO", "PHONETIC_KEY1": "AKSNBL"},
        {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZONOBEL PINTUCO", "PHONETIC_KEY1": "AKSNBL"},
        # 2-3: NIT idéntico, nombre con typo
        {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZONOBEL PINTCO", "PHONETIC_KEY1": "AKSNBL"},
        {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZOMOBEL PINYUCO", "PHONETIC_KEY1": "AKSNBL"},
        # 4: NIT distancia 1, nombre similar
        {"NIT_OK": "8909001483", "NOMBRE_LIMPIO": "PINTUCO ORBIS", "PHONETIC_KEY1": "PNTKR"},
        # 5: NIT distancia 2, nombre totalmente distinto
        {
            "NIT_OK": "8909001488",
            "NOMBRE_LIMPIO": "COMPAÑIA GLOBAL DE PINTURAS",
            "PHONETIC_KEY1": "KMPN",
        },
        # 6: NIT vacío, nombre con primer token compartido
        {"NIT_OK": "", "NOMBRE_LIMPIO": "ECOPETROL SA", "PHONETIC_KEY1": "EKPTR"},
        # 7: NIT vacío, nombre con primer token compartido pero distinto resto
        {"NIT_OK": "", "NOMBRE_LIMPIO": "ECOPETROL LIMITADA", "PHONETIC_KEY1": "EKPTR"},
        # 8: nombres totalmente disjuntos, distintas longitudes
        {"NIT_OK": "1234567890", "NOMBRE_LIMPIO": "X", "PHONETIC_KEY1": "X"},
        {
            "NIT_OK": "0987654321",
            "NOMBRE_LIMPIO": "EMPRESA QUE NO TIENE NADA QUE VER CON LA OTRA",
            "PHONETIC_KEY1": "MPRS",
        },
        # 10-11: nombres con orden distinto
        {"NIT_OK": "8000000001", "NOMBRE_LIMPIO": "BOLIVAR CONSTRUCTORA", "PHONETIC_KEY1": "BLVR"},
        {"NIT_OK": "8000000001", "NOMBRE_LIMPIO": "CONSTRUCTORA BOLIVAR", "PHONETIC_KEY1": "KNSTR"},
        # 12-13: caso edge — 'nan' strings (vienen del cleaner cuando NaN)
        {"NIT_OK": "nan", "NOMBRE_LIMPIO": "nan", "PHONETIC_KEY1": ""},
        {"NIT_OK": "8888888888", "NOMBRE_LIMPIO": "EMPRESA REAL SAS", "PHONETIC_KEY1": "MPRRS"},
        # 14-15: nombre con score intermedio (refinement triggered) y primer token diferente
        {"NIT_OK": "7000000001", "NOMBRE_LIMPIO": "GRUPO ARGOS", "PHONETIC_KEY1": "GRPRG"},
        {"NIT_OK": "7000000001", "NOMBRE_LIMPIO": "ARGOS CEMENTOS CIA", "PHONETIC_KEY1": "RGSKM"},
    ]
    # IMPORTANTE: dtype=object explícito para portabilidad del pickle.
    # Sin esta línea, pandas 3.x infiere StringDtype('pyarrow', NaN) cuyo
    # constructor acepta 2 args, mientras que pandas 2.2.x espera 1. Ver
    # docstring del módulo y _to_portable_dtypes().
    df = _to_portable_dtypes(pd.DataFrame(rows))
    # Generar TODOS los pares (idx_0 < idx_1) — cobertura exhaustiva del set
    n = len(df)
    pairs = np.array([(i, j) for i in range(n) for j in range(i + 1, n)])
    return df, pairs


def capturar_oraculo(out_path: Path) -> None:
    """Corre el scorer actual sobre el set oráculo y guarda los outputs."""
    df, pairs = construir_dataset_oraculo()
    # Tres perfiles para cubrir las ramas:
    profiles = {
        "default_off": {
            "score_threshold": 0.0,  # 0 para capturar TODOS los pares (sin filtro final)
            "max_nit_distance": 99,
            "min_name_similarity": 0.0,
            "weights": {"name": 0.65, "nit": 0.20, "phonetic": 0.15},
            "scoring_batch_size": 50_000,
        },
        "with_override_and_boost": {
            "score_threshold": 0.0,
            "max_nit_distance": 99,
            "min_name_similarity": 0.0,
            "weights": {"name": 0.65, "nit": 0.20, "phonetic": 0.15},
            "scoring_batch_size": 50_000,
            "nit_identical_overrides_name_filter": True,
            "nit_identical_score_boost": 0.05,
        },
        "with_extra_features": {
            "score_threshold": 0.0,
            "max_nit_distance": 99,
            "min_name_similarity": 0.0,
            "weights": {"name": 0.65, "nit": 0.20, "phonetic": 0.15},
            "scoring_batch_size": 50_000,
            "extra_features": [
                {"column": "PHONETIC_KEY1", "weight": 0.10, "type": "categorical_signed"},
            ],
        },
    }
    capturas: dict[str, pd.DataFrame] = {}
    logging.disable(logging.CRITICAL)
    try:
        for name, prof in profiles.items():
            with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
                sc = VectorizedScorer(prof)
                res = sc._score_batch_vectorized(pairs.copy(), df.copy())
            # Normalizar dtypes a portable: floats/ints preservados, columns→object.
            capturas[name] = _to_portable_dtypes(
                res.sort_values(["idx_0", "idx_1"]).reset_index(drop=True)
            )
    finally:
        logging.disable(logging.NOTSET)

    payload = {
        "df": df,
        "pairs": pairs,
        "profiles": profiles,
        "capturas": capturas,
        # Metadata diagnóstica (no leída por el test; solo para debug si
        # algún día rompe la compatibilidad). NO incluir información que
        # cambie entre máquinas (rutas, usuarios, etc.).
        "_meta": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "pandas_version": pd.__version__,
            "numpy_version": np.__version__,
            "python_version": platform.python_version(),
            "oraculo_format_version": "2",  # v2 = dtypes portables (object)
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Protocol 4 (default en Py3.8+) es compatible con todas las versiones
    # de Python soportadas (requires-python = ">=3.10"). Evitar HIGHEST_PROTOCOL
    # porque sube con cada release de Python y reduce portabilidad cross-env.
    with out_path.open("wb") as f:
        pickle.dump(payload, f, protocol=4)
    print(f"  ✅ Oráculo guardado: {out_path}")
    print(f"     Pandas: {pd.__version__} | Python: {platform.python_version()}")
    for name, cap in capturas.items():
        print(f"     {name}: {len(cap)} pares scoreados")


if __name__ == "__main__":
    # Permitir invocación desde cualquier cwd (CI, Drive, local). Resolver
    # ruta relativa al repo (3 niveles arriba: scripts/ → repo/).
    repo_root = Path(__file__).resolve().parent.parent
    out = repo_root / "tests" / "data" / "oraculo_scorer_p1_1.pkl"
    print(f"  Repo root: {repo_root}")
    capturar_oraculo(out)
    print("  Para validar paridad: python scripts/validar_paridad_p1_1.py")
    sys.exit(0)
