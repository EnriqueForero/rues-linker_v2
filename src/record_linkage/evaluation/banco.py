"""record_linkage.evaluation.banco — Banco de pruebas reproducible (v0.18.0).

Contexto: Google Colab Free (~12 GB RAM) y contenedores de CI de 2 vCPU.
Fuente de datos: ``data/ground_truth/ground_truth_grande.csv`` (12.427
registros, 3.486 grupos, 5 fuentes, 22.073 pares verdaderos).

Por qué existe
--------------
Toda mejora de esta librería se justificó hasta ahora con una medición hecha
a mano, distinta cada vez. Eso permitió dos regresiones que llegaron a
producción: una de velocidad (0.17.0) y una de memoria (0.17.3), ambas
"verificadas" contra un banco que no era el mismo de la vez anterior.

Este módulo fija **un solo conjunto de datos, una sola invocación y un solo
formato de resultado**, de modo que dos corridas cualesquiera sean
comparables campo por campo. Mide en la misma pasada:

* **Calidad** — precision, recall y F1 por pares; B-cubed; recall
  estratificado por régimen (CON_NIT / SIN_NIT), por caso y por fuente.
* **Tiempo** — total y por fase del pipeline.
* **Memoria** — pico de RSS muestreado durante toda la corrida.
* **Recursos** — bytes escritos en disco y artefactos producidos.
* **Determinismo** — huella SHA-256 canónica de la partición resultante.

Dependencias no estándar: ``psutil`` (muestreo de RSS).

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-29  ·  Version: 0.18.0
"""

from __future__ import annotations

import hashlib
import json
import platform
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "Corrida",
    "EspecificacionBanco",
    "MetricasCalidad",
    "MetricasRecursos",
    "MuestreadorRecursos",
    "bcubed",
    "correr_banco",
    "evaluar_calidad",
    "huella_particion",
]

#: Semilla única de todo el banco. Cualquier proceso aleatorio la usa.
SEMILLA = 42

#: Columnas mínimas que debe traer el conjunto de referencia.
COLUMNAS_REQUERIDAS = frozenset(
    {"ID_REGISTRO", "ID_GROUP", "REGIMEN", "NIT", "RAZON_SOCIAL", "FUENTE", "CASO"}
)


@dataclass(frozen=True)
class EspecificacionBanco:
    """Qué se mide, con qué datos y con qué configuración.

    Cero números mágicos en la lógica: todo lo ajustable vive aquí y queda
    guardado en el JSON de la corrida, de modo que un resultado siempre se
    puede reproducir a partir de su propio archivo.

    Attributes:
        etiqueta: nombre corto de la corrida (aparece en el archivo de salida).
        datos: ruta al CSV de referencia.
        perfil: perfil de configuración de la librería.
        confiables: fuentes que no se deduplican internamente.
        variables_extra: evidencia adicional para el scorer, o None.
        ajustes_perfil: sobrescrituras puntuales del perfil.
        perfil_multicampo: refinamiento multi-variable posterior, o None.
        dir_trabajo: carpeta de artefactos intermedios.
        dir_evidencia: carpeta donde se deposita el JSON de la corrida.
        intervalo_muestreo: segundos entre lecturas de RSS.
        pliegue: si se indica, se evalúa solo el subconjunto de grupos cuyo
            identificador cae en ese pliegue. Sirve para calibrar en unos
            pliegues y verificar en otro: un umbral elegido sobre todos los
            datos siempre parece mejor de lo que es.
        pliegues: número total de pliegues.
        nota: texto libre que explica qué cambió en esta corrida.
    """

    etiqueta: str
    datos: Path
    perfil: str = "produccion_estandar"
    confiables: frozenset[str] = frozenset()
    variables_extra: tuple[Any, ...] | None = None
    ajustes_perfil: Mapping[str, Any] | None = None
    perfil_multicampo: str | None = None
    dir_trabajo: Path = Path("/tmp/banco_trabajo")
    dir_evidencia: Path = Path("docs/evidencia")
    intervalo_muestreo: float = 0.25
    pliegue: int | None = None
    pliegues: int = 3
    nota: str = ""

    def __post_init__(self) -> None:
        if not self.etiqueta or not self.etiqueta.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                f"etiqueta={self.etiqueta!r} debe ser alfanumérica (guiones y "
                f"guiones bajos permitidos): se usa como nombre de archivo."
            )
        if self.intervalo_muestreo <= 0:
            raise ValueError("intervalo_muestreo debe ser > 0.")
        if self.pliegues < 1:
            raise ValueError("pliegues debe ser >= 1.")
        if self.pliegue is not None and not 0 <= self.pliegue < self.pliegues:
            raise ValueError(f"pliegue debe estar en [0, {self.pliegues}).")
        object.__setattr__(self, "datos", Path(self.datos).expanduser())
        object.__setattr__(self, "dir_trabajo", Path(self.dir_trabajo).expanduser())
        object.__setattr__(self, "dir_evidencia", Path(self.dir_evidencia).expanduser())


@dataclass(frozen=True)
class MetricasCalidad:
    """Calidad del enlace, global y estratificada.

    Attributes:
        registros: filas evaluadas.
        grupos_verdad: grupos en el conjunto de referencia.
        grupos_predichos: grupos que produjo la librería.
        pares_verdaderos: pares que deben unirse.
        tp / fp / fn: aciertos, sobre-fusiones y fragmentaciones.
        precision / recall / f1: métricas por pares.
        b3_precision / b3_recall / b3_f1: métricas B-cubed (por registro).
        recall_por_regimen: recall dentro de CON_NIT y SIN_NIT.
        recall_por_caso: recall dentro de cada categoría del conjunto.
        fp_que_tocan_negativo: falsos positivos que involucran un registro
            diseñado como negativo. Son los errores más caros.
    """

    registros: int
    grupos_verdad: int
    grupos_predichos: int
    pares_verdaderos: int
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    b3_precision: float
    b3_recall: float
    b3_f1: float
    recall_por_regimen: dict[str, float] = field(default_factory=dict)
    recall_por_caso: dict[str, float] = field(default_factory=dict)
    fp_que_tocan_negativo: int = 0
    calidad_por_estrato: dict[str, dict[str, float]] = field(default_factory=dict)
    macro_f1: float = float("nan")


@dataclass(frozen=True)
class MetricasRecursos:
    """Costo de la corrida: tiempo, memoria y disco.

    Attributes:
        segundos_total: duración de la llamada completa.
        segundos_por_fase: duración de cada fase del pipeline.
        rss_por_fase: pico de memoria residente que el pipeline registró
            para cada fase.
        rss_pico_mib: máximo de memoria residente observado por el banco.
        rss_inicial_mib: memoria residente antes de empezar.
        muestras_rss: número de lecturas tomadas (para saber si el muestreo
            fue suficientemente denso).
        bytes_disco: tamaño total de los artefactos intermedios.
        candidatos: pares que produjo el bloqueo.
        pares_scoreados: pares que superaron el umbral de score.
    """

    segundos_total: float
    segundos_por_fase: dict[str, float] = field(default_factory=dict)
    rss_por_fase: dict[str, float] = field(default_factory=dict)
    rss_pico_mib: float = 0.0
    rss_inicial_mib: float = 0.0
    muestras_rss: int = 0
    bytes_disco: int = 0
    candidatos: int | None = None
    pares_scoreados: int | None = None

    @property
    def candidatos_por_valido(self) -> float | None:
        """Cuántos candidatos se descartaron por cada par aceptado."""
        if not self.candidatos or not self.pares_scoreados:
            return None
        return round(self.candidatos / self.pares_scoreados, 1)


@dataclass(frozen=True)
class Corrida:
    """Resultado completo y auto-contenido de una corrida del banco."""

    etiqueta: str
    version: str
    perfil: str
    marca_tiempo: str
    entorno: dict[str, Any]
    calidad: MetricasCalidad
    recursos: MetricasRecursos
    huella: str
    especificacion: dict[str, Any]
    nota: str = ""

    def a_dict(self) -> dict[str, Any]:
        """Serializa a un diccionario JSON-compatible."""
        crudo = asdict(self)
        crudo["recursos"]["candidatos_por_valido"] = self.recursos.candidatos_por_valido
        return crudo

    def guardar(self, directorio: Path) -> Path:
        """Escribe el JSON de evidencia y devuelve su ruta."""
        directorio = Path(directorio)
        directorio.mkdir(parents=True, exist_ok=True)
        destino = directorio / f"corrida_{self.etiqueta}.json"
        destino.write_text(
            json.dumps(self.a_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return destino

    def resumen(self) -> str:
        """Reporte legible de una sola pieza, apto para pegar en la bitácora."""
        c, r = self.calidad, self.recursos
        ancho = 62
        linea = "═" * ancho
        cpv = r.candidatos_por_valido
        return (
            f"{linea}\n"
            f"  BANCO · {self.etiqueta}  ·  rues-linker {self.version}  ·  {self.perfil}\n"
            f"{linea}\n"
            f"  CALIDAD        precision {c.precision:.4f}   recall {c.recall:.4f}   "
            f"F1 {c.f1:.4f}\n"
            f"                 B³ precision {c.b3_precision:.4f}   recall "
            f"{c.b3_recall:.4f}   F1 {c.b3_f1:.4f}\n"
            f"                 TP {c.tp:,}   FP {c.fp:,}   FN {c.fn:,}\n"
            f"                 grupos {c.grupos_predichos:,} vs {c.grupos_verdad:,} "
            f"verdaderos\n"
            f"                 FP que tocan un negativo: {c.fp_que_tocan_negativo}\n"
            + (
                "".join(
                    f"  ESTRATO        {n:<12s} F1 {m['f1']:.4f}  "
                    f"P {m['precision']:.4f}  R {m['recall']:.4f}  "
                    f"({int(m.get('pares', 0)):,} pares)\n"
                    for n, m in sorted(c.calidad_por_estrato.items())
                    if m["f1"] == m["f1"]
                )
                + f"                 macro-F1 {c.macro_f1:.4f}\n"
                if c.calidad_por_estrato
                else ""
            )
            + f"{'─' * ancho}\n"
            f"  RECALL         "
            + "   ".join(f"{k} {v:.4f}" for k, v in sorted(c.recall_por_regimen.items()))
            + "\n"
            f"{'─' * ancho}\n"
            f"  TIEMPO         {r.segundos_total:.1f}s total"
            + (
                "   "
                + "  ".join(f"{k.split('_')[0]} {v:.1f}s" for k, v in r.segundos_por_fase.items())
                if r.segundos_por_fase
                else ""
            )
            + "\n"
            f"  MEMORIA        pico {r.rss_pico_mib:,.0f} MiB "
            f"(inicial {r.rss_inicial_mib:,.0f} · {r.muestras_rss} muestras)\n"
            f"  DISCO          {r.bytes_disco / 1024**2:,.1f} MiB de artefactos\n"
            f"  BLOQUEO        {r.candidatos or 0:,} candidatos → "
            f"{r.pares_scoreados or 0:,} válidos" + (f"  ({cpv}:1)" if cpv else "") + "\n"
            f"  HUELLA         {self.huella[:32]}…\n"
            f"{linea}"
        )


class MuestreadorRecursos:
    """Muestrea el RSS del proceso en un hilo aparte mientras corre el bloque.

    Un pico de memoria que dura tres segundos no aparece si solo se mide al
    principio y al final. El hilo lee cada ``intervalo`` segundos y guarda el
    máximo; el cierre hace ``join`` pase lo que pase, incluida una excepción
    a mitad de corrida.
    """

    def __init__(self, intervalo: float = 0.25) -> None:
        if intervalo <= 0:
            raise ValueError("intervalo debe ser > 0.")
        self._intervalo = intervalo
        self._activo = threading.Event()
        self._hilo: threading.Thread | None = None
        self.pico_mib = 0.0
        self.inicial_mib = 0.0
        self.muestras = 0

    def _leer(self) -> float:
        import psutil

        return psutil.Process().memory_info().rss / 1024**2

    def _bucle(self) -> None:
        while self._activo.is_set():
            actual = self._leer()
            self.pico_mib = max(self.pico_mib, actual)
            self.muestras += 1
            time.sleep(self._intervalo)

    def __enter__(self) -> MuestreadorRecursos:
        self.inicial_mib = self._leer()
        self.pico_mib = self.inicial_mib
        self._activo.set()
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._activo.clear()
        if self._hilo is not None:
            self._hilo.join(timeout=self._intervalo * 8)
        self.pico_mib = max(self.pico_mib, self._leer())


def bcubed(verdad: np.ndarray, prediccion: np.ndarray) -> tuple[float, float, float]:
    """Precision, recall y F1 B-cubed, vectorizado por tabla de contingencia.

    B-cubed evalúa por REGISTRO en vez de por par, así que no premia a un
    sistema que acierta un grupo enorme y falla mil pequeños. Es la métrica
    que mejor refleja "¿mi correlativa sirve?".

    Args:
        verdad: etiqueta de grupo verdadera por registro.
        prediccion: etiqueta de grupo predicha por registro.

    Returns:
        Tupla (precision, recall, f1).

    Raises:
        ValueError: si los arreglos tienen longitudes distintas o están vacíos.
    """
    if len(verdad) != len(prediccion):
        raise ValueError(f"longitudes distintas: {len(verdad)} vs {len(prediccion)}")
    if len(verdad) == 0:
        raise ValueError("no hay registros que evaluar.")

    marco = pd.DataFrame({"v": verdad, "p": prediccion})
    inter = marco.groupby(["v", "p"], observed=True).size().rename("n").reset_index()
    tam_v = marco.groupby("v", observed=True).size().rename("nv")
    tam_p = marco.groupby("p", observed=True).size().rename("np_")
    inter = inter.merge(tam_v, on="v").merge(tam_p, on="p")
    n = len(marco)
    precision = float((inter["n"] ** 2 / inter["np_"]).sum() / n)
    recall = float((inter["n"] ** 2 / inter["nv"]).sum() / n)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return round(precision, 4), round(recall, 4), round(f1, 4)


def _pares_de_grupos(etiquetas: np.ndarray) -> set[tuple[int, int]]:
    """Pares (i, j) con i < j que comparten etiqueta, sin bucles por par.

    Se agrupa por etiqueta y se emiten las combinaciones dentro de cada grupo
    con ``np.triu_indices``; sobre 12.427 registros el conjunto verdadero son
    22.073 pares, así que materializarlo es barato y exacto.
    """
    pares: set[tuple[int, int]] = set()
    orden = np.argsort(etiquetas, kind="stable")
    ordenadas = np.asarray(etiquetas)[orden]
    cortes = np.flatnonzero(np.r_[True, ordenadas[1:] != ordenadas[:-1], True])
    for ini, fin in pairwise(cortes):
        miembros = np.sort(orden[ini:fin])
        if len(miembros) < 2:
            continue
        i, j = np.triu_indices(len(miembros), k=1)
        pares.update(zip(miembros[i].tolist(), miembros[j].tolist(), strict=True))
    return pares


def evaluar_calidad(referencia: pd.DataFrame, prediccion: np.ndarray) -> MetricasCalidad:
    """Compara la partición predicha contra el conjunto de referencia.

    Args:
        referencia: DataFrame con ID_GROUP, REGIMEN, CASO y FUENTE por fila,
            en el mismo orden que ``prediccion``.
        prediccion: etiqueta de grupo predicha por fila.

    Returns:
        MetricasCalidad con las métricas globales y estratificadas.

    Raises:
        ValueError: si faltan columnas o las longitudes no coinciden.
    """
    faltantes = {"ID_GROUP", "REGIMEN", "CASO"} - set(referencia.columns)
    if faltantes:
        raise ValueError(f"al conjunto de referencia le faltan columnas: {sorted(faltantes)}")
    if len(referencia) != len(prediccion):
        raise ValueError(f"longitudes distintas: {len(referencia)} vs {len(prediccion)}")

    verdad = referencia["ID_GROUP"].to_numpy()
    pares_v = _pares_de_grupos(verdad)
    pares_p = _pares_de_grupos(np.asarray(prediccion))
    tp = len(pares_v & pares_p)
    fp = len(pares_p - pares_v)
    fn = len(pares_v - pares_p)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    b3p, b3r, b3f = bcubed(verdad, np.asarray(prediccion))

    def _recall_en(mascara: np.ndarray) -> float:
        posiciones = set(np.flatnonzero(mascara).tolist())
        sub_v = {p for p in pares_v if p[0] in posiciones and p[1] in posiciones}
        if not sub_v:
            return float("nan")
        return round(len(sub_v & pares_p) / len(sub_v), 4)

    def _calidad_en(mascara: np.ndarray) -> dict[str, float]:
        """Precisión, recall y F1 restringidos a los pares internos del estrato."""
        posiciones = set(np.flatnonzero(mascara).tolist())

        def dentro(par: tuple[int, int]) -> bool:
            return par[0] in posiciones and par[1] in posiciones

        sub_v = {par for par in pares_v if dentro(par)}
        if not sub_v:
            return {"precision": float("nan"), "recall": float("nan"), "f1": float("nan")}
        sub_p = {par for par in pares_p if dentro(par)}
        aciertos = len(sub_v & sub_p)
        prec = aciertos / len(sub_p) if sub_p else 0.0
        rec = aciertos / len(sub_v)
        efe = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        return {
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(efe, 4),
            "pares": float(len(sub_v)),
        }

    if "ESTRATO" in referencia.columns:
        capa = referencia["ESTRATO"].to_numpy()
        por_estrato = {str(v): _calidad_en(capa == v) for v in pd.unique(capa)}
    else:
        por_estrato = {}
    efes = [m["f1"] for m in por_estrato.values() if m["f1"] == m["f1"]]
    macro = round(float(np.mean(efes)), 4) if efes else float("nan")

    regimen = referencia["REGIMEN"].to_numpy()
    caso = referencia["CASO"].to_numpy()
    negativos = set(np.flatnonzero(pd.Series(caso).str.startswith("negativo")).tolist())
    fp_negativos = sum(1 for a, b in (pares_p - pares_v) if a in negativos or b in negativos)

    return MetricasCalidad(
        registros=len(referencia),
        grupos_verdad=int(pd.Series(verdad).nunique()),
        grupos_predichos=int(pd.Series(prediccion).nunique()),
        pares_verdaderos=len(pares_v),
        tp=tp,
        fp=fp,
        fn=fn,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        b3_precision=b3p,
        b3_recall=b3r,
        b3_f1=b3f,
        recall_por_regimen={str(v): _recall_en(regimen == v) for v in pd.unique(regimen)},
        recall_por_caso={str(v): _recall_en(caso == v) for v in pd.unique(caso)},
        fp_que_tocan_negativo=fp_negativos,
        calidad_por_estrato=por_estrato,
        macro_f1=macro,
    )


def huella_particion(indices: Sequence[int], grupos: Sequence[Any]) -> str:
    """SHA-256 canónico de una partición: identidad reproducible del resultado.

    Se construye a partir de ``indice:mínimo índice del grupo``, ordenado por
    índice, de modo que renombrar los identificadores de grupo no cambia la
    huella pero mover un solo registro de grupo sí.
    """
    marco = pd.DataFrame({"i": list(indices), "g": list(grupos)})
    minimos = marco.groupby("g", observed=True)["i"].transform("min")
    marco = marco.assign(m=minimos).sort_values("i", kind="stable")
    digestor = hashlib.sha256()
    for i, m in zip(marco["i"].tolist(), marco["m"].tolist(), strict=True):
        digestor.update(f"{i}:{m}\n".encode())
    return digestor.hexdigest()


def _seleccionar_pliegue(referencia: pd.DataFrame, pliegue: int, pliegues: int) -> pd.DataFrame:
    """Deja solo los grupos que caen en el pliegue pedido.

    La partición es por GRUPO, no por fila: partir por fila rompería grupos
    verdaderos y haría imposible medir recall. El reparto usa un hash estable
    del identificador de grupo, así que el mismo pliegue contiene siempre los
    mismos grupos, en cualquier máquina y en cualquier orden de lectura.
    """
    claves = referencia["ID_GROUP"].astype(str)
    asignacion = claves.map(lambda g: int(hashlib.sha256(g.encode()).hexdigest(), 16) % pliegues)
    seleccion = referencia[asignacion.to_numpy() == pliegue].reset_index(drop=True)
    if seleccion.empty:
        raise ValueError(f"El pliegue {pliegue} de {pliegues} quedó vacío.")
    return seleccion


def _guardar_prediccion(
    espec: EspecificacionBanco, referencia: pd.DataFrame, prediccion: np.ndarray
) -> Path:
    """Deja la partición predicha junto a la verdad, para análisis de errores.

    Sin esto, estudiar un falso positivo obliga a volver a correr el pipeline
    o a confiar en un directorio de trabajo que la siguiente corrida pisa. El
    archivo es pequeño (una fila por registro) y hace reproducible cualquier
    análisis posterior sin repetir veinticinco segundos de cómputo.
    """
    destino = Path(espec.dir_evidencia) / f"prediccion_{espec.etiqueta}.parquet"
    destino.parent.mkdir(parents=True, exist_ok=True)
    columnas = ["POS", "ID_REGISTRO", "ID_GROUP", "REGIMEN", "CASO", "FUENTE", "RAZON_SOCIAL"]
    marco = referencia[[c for c in columnas if c in referencia.columns]].copy()
    marco["ID_GRUPO_PREDICHO"] = prediccion
    marco.to_parquet(destino, index=False)
    return destino


def _tamano_directorio(ruta: Path) -> int:
    """Bytes ocupados por todos los archivos bajo ``ruta``."""
    if not ruta.exists():
        return 0
    return sum(f.stat().st_size for f in ruta.rglob("*") if f.is_file())


def cargar_referencia(ruta: Path) -> pd.DataFrame:
    """Carga y valida el conjunto de referencia.

    Trust but verify: si al archivo le faltan columnas o llega vacío, esto
    falla en el primer segundo en vez de producir métricas sin sentido veinte
    minutos después.
    """
    ruta = Path(ruta)
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe el conjunto de referencia: {ruta}")
    marco = pd.read_csv(ruta, dtype=str, keep_default_na=False, na_values=[""])
    faltantes = COLUMNAS_REQUERIDAS - set(marco.columns)
    if faltantes:
        raise ValueError(
            f"{ruta.name} no tiene {sorted(faltantes)}. Columnas presentes: {sorted(marco.columns)}"
        )
    if marco.empty:
        raise ValueError(f"{ruta.name} está vacío.")
    return marco


@contextmanager
def _silenciar_pipeline() -> Iterator[None]:
    """Baja el ruido de logging del pipeline durante la medición."""
    import logging

    niveles = {}
    for nombre in ("Orchestrator", "RecordLinkagePipeline", "DataHandler", "root"):
        registrador = logging.getLogger(nombre)
        niveles[nombre] = registrador.level
        registrador.setLevel(logging.WARNING)
    try:
        yield
    finally:
        for nombre, nivel in niveles.items():
            logging.getLogger(nombre).setLevel(nivel)


def correr_banco(espec: EspecificacionBanco, *, silencioso: bool = True) -> Corrida:
    """Ejecuta el banco completo y devuelve la corrida medida.

    Args:
        espec: qué medir y con qué configuración.
        silencioso: si True, baja el nivel de logging del pipeline.

    Returns:
        Corrida con calidad, recursos, huella y entorno.
    """
    import record_linkage as rl
    from record_linkage.api import linkage

    referencia = cargar_referencia(espec.datos)
    if espec.pliegue is not None:
        referencia = _seleccionar_pliegue(referencia, espec.pliegue, espec.pliegues)
    fuentes = {
        nombre: parte.reset_index(drop=True)
        for nombre, parte in referencia.groupby("FUENTE", sort=True)
    }
    # Posición global de cada fila en el orden en que la librería consolida:
    # fuentes en orden alfabético, filas en su orden original dentro de cada una.
    desplazamiento, acumulado = {}, 0
    for nombre in sorted(fuentes):
        desplazamiento[nombre] = acumulado
        acumulado += len(fuentes[nombre])
    referencia = referencia.sort_values("FUENTE", kind="stable").reset_index(drop=True)
    referencia["POS"] = referencia.groupby("FUENTE").cumcount() + referencia["FUENTE"].map(
        desplazamiento
    )

    if espec.dir_trabajo.exists():
        import shutil

        shutil.rmtree(espec.dir_trabajo, ignore_errors=True)

    contexto = _silenciar_pipeline() if silencioso else _sin_contexto()
    t0 = time.perf_counter()
    with MuestreadorRecursos(espec.intervalo_muestreo) as muestreador, contexto:
        salida = linkage(
            fuentes,
            trusted_sources=set(espec.confiables),
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            col_ciudad=None,
            extra_features=list(espec.variables_extra) if espec.variables_extra else None,
            work_dir=str(espec.dir_trabajo),
            profile=espec.perfil,
            ajustes_perfil=dict(espec.ajustes_perfil) if espec.ajustes_perfil else None,
            matching_profile=espec.perfil_multicampo,
            skip_reporting=True,
        )
    segundos = time.perf_counter() - t0

    correlativa = salida["correlative"].sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    if len(correlativa) != len(referencia):
        raise RuntimeError(
            f"la correlativa trae {len(correlativa):,} filas y la referencia "
            f"{len(referencia):,}: no se pueden comparar."
        )
    prediccion = correlativa["ID_GRUPO"].to_numpy()[referencia["POS"].to_numpy()]

    calidad = evaluar_calidad(referencia, prediccion)
    manifiesto = leer_manifiesto(espec.dir_trabajo)
    recursos = MetricasRecursos(
        segundos_total=round(segundos, 2),
        segundos_por_fase=_fases_desde_manifiesto(manifiesto),
        rss_por_fase=_rss_por_fase(manifiesto),
        rss_pico_mib=round(muestreador.pico_mib, 1),
        rss_inicial_mib=round(muestreador.inicial_mib, 1),
        muestras_rss=muestreador.muestras,
        bytes_disco=_tamano_directorio(espec.dir_trabajo),
        candidatos=_contar_filas_sqlite(
            espec.dir_trabajo / "L2_lsh_candidates" / "candidates.db", "candidate_pairs"
        ),
        pares_scoreados=_contar_filas_sqlite(
            espec.dir_trabajo / "L3_scoring" / "scored.db", "scored_pairs"
        ),
    )
    _guardar_prediccion(espec, referencia, prediccion)
    corrida = Corrida(
        etiqueta=espec.etiqueta,
        version=rl.__version__,
        perfil=espec.perfil,
        marca_tiempo=time.strftime("%Y-%m-%dT%H:%M:%S"),
        entorno={
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "plataforma": platform.platform(),
        },
        calidad=calidad,
        recursos=recursos,
        huella=huella_particion(referencia["POS"].tolist(), prediccion.tolist()),
        especificacion={
            "datos": str(espec.datos),
            "perfil": espec.perfil,
            "confiables": sorted(espec.confiables),
            "variables_extra": list(espec.variables_extra) if espec.variables_extra else None,
            "ajustes_perfil": dict(espec.ajustes_perfil) if espec.ajustes_perfil else None,
            "perfil_multicampo": espec.perfil_multicampo,
            "semilla": SEMILLA,
        },
        nota=espec.nota,
    )
    return corrida


@contextmanager
def _sin_contexto() -> Iterator[None]:
    """Contexto neutro, para no ramificar en el llamador."""
    yield


def leer_manifiesto(dir_trabajo: Path) -> dict[str, Any]:
    """Lee el manifiesto que el pipeline deja en su directorio de trabajo.

    El pipeline ya registra ``duration`` y ``peak_rss_mib`` por fase; leerlo
    del artefacto en vez de instrumentar el llamador evita duplicar la
    medición y mantiene el banco desacoplado de la firma de la API.
    """
    ruta = Path(dir_trabajo) / "manifest.json"
    if not ruta.is_file():
        return {}
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _fases_desde_manifiesto(manifiesto: Mapping[str, Any]) -> dict[str, float]:
    """Segundos por fase, en el orden en que el pipeline las ejecutó."""
    salida: dict[str, float] = {}
    for nombre, bloque in manifiesto.items():
        if nombre.startswith("_") or not isinstance(bloque, dict):
            continue
        duracion = (bloque.get("meta") or {}).get("duration")
        if duracion is not None:
            salida[nombre] = round(float(duracion), 2)
    return salida


def _rss_por_fase(manifiesto: Mapping[str, Any]) -> dict[str, float]:
    """Pico de RSS por fase, según lo que el pipeline registró."""
    salida: dict[str, float] = {}
    for nombre, bloque in manifiesto.items():
        if nombre.startswith("_") or not isinstance(bloque, dict):
            continue
        pico = (bloque.get("meta") or {}).get("peak_rss_mib")
        if pico is not None:
            salida[nombre] = round(float(pico), 1)
    return salida


def _contar_filas_sqlite(ruta: Path, tabla: str) -> int | None:
    """Cuenta filas de una tabla SQLite sin cargarla. None si no se puede."""
    if not ruta.is_file():
        return None
    import sqlite3

    try:
        with sqlite3.connect(f"file:{ruta}?mode=ro", uri=True) as conexion:
            existe = conexion.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabla,)
            ).fetchone()
            if not existe:
                return None
            return int(conexion.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0])
    except sqlite3.Error:
        return None
