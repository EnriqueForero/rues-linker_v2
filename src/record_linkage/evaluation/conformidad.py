"""record_linkage.evaluation.conformidad — Suite de conformidad por caso (v0.20.0).

Por qué existe, además del banco
--------------------------------
El banco (`evaluation.banco`) responde **cuánto**: F1, recall y precisión
sobre 46.374 pares de variación real. Es lo que dice si un cambio mejoró.

No responde **qué**. Con 46.374 pares, un comportamiento roto que afecta a
cuatro casos —geo idéntica en entidades distintas, teléfono de call center
compartido, grupo con dos NIT legítimos— se diluye hasta ser invisible: mueve
el F1 en la cuarta cifra decimal y nadie lo ve nunca.

Esta suite responde qué. Cada uno de los 43 casos del catálogo se evalúa por
separado y **aprueba o reprueba**, sin promediar con nada. Un caso reprobado
es una capacidad que la librería no tiene, dígalo el F1 lo que diga.

Los dos instrumentos son complementarios y ninguno reemplaza al otro:

    banco         ¿mejoró?          estadístico   30.486 registros
    conformidad   ¿sabe hacerlo?    por caso         207 registros

Contrato del conjunto
---------------------
`data/conformidad/` — extraído de `Ground_Truth_Multicampo_v1.xlsx` y
versionado. La regla del archivo original se respeta: **no se regenera sin
acta**, porque si cambia, las métricas históricas dejan de ser comparables.

* `dedup_registros.csv`  166 filas · `ID_GRUPO_ESPERADO` es la verdad.
* `dedup_pares.csv`      140 pares esperados, con su `CASO_TIPO`.
* `linkage_base_a/b.csv` 20 y 21 filas · `ENTIDAD_ID_ESPERADO` cruza A con B.
* `linkage_ground_truth.csv` 15 cruces verdaderos.
* `catalogo_casos.csv`   la taxonomía de los 43 casos.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-29  ·  Version: 0.20.0
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "CAMINO_POR_DEFECTO",
    "DIRECTORIO_POR_DEFECTO",
    "VARIABLE_ENTORNO",
    "ConjuntoConformidad",
    "Informe",
    "ResultadoCaso",
    "cargar_conjunto",
    "evaluar_dedup",
    "evaluar_linkage",
    "localizar_conjunto",
]

#: Dónde vive el conjunto versionado cuando se trabaja sobre el checkout.
#: Ojo: esto sale de la ubicación del MÓDULO, no del repositorio. Con layout
#: `src/` y el paquete importado desde el árbol de fuentes coinciden; con el
#: paquete instalado (rueda en site-packages) no coinciden en absoluto. Por eso
#: nadie debe resolver la ruta con esta constante a secas: use
#: `localizar_conjunto()`.
DIRECTORIO_POR_DEFECTO = Path(__file__).resolve().parents[3] / "data" / "conformidad"

#: Escotilla explícita: si está definida, gana sobre cualquier heurística.
VARIABLE_ENTORNO = "RUES_LINKER_CONFORMIDAD"

#: Archivo que se usa para reconocer un directorio de conformidad de verdad.
#: Se comprueba un archivo y no el directorio porque un `data/conformidad/`
#: vacío —creado por un `mkdir -p` distraído— es peor que uno ausente: pasa la
#: comprobación y falla después, lejos de la causa.
_ANCLA = "dedup_registros.csv"


def _es_conjunto(directorio: Path) -> bool:
    return (directorio / _ANCLA).is_file()


def localizar_conjunto(inicio: Path | str | None = None) -> Path:
    """Devuelve el directorio del conjunto de conformidad.

    Por qué no basta una constante
    ------------------------------
    El conjunto vive en el repositorio (`data/conformidad/`), no dentro del
    paquete. Cuando `record_linkage` se importa desde `src/` las dos cosas
    están a tres niveles de distancia y una constante alcanza. Cuando se
    importa la rueda instalada, `parents[3]` es el directorio de la instalación
    de Python y el conjunto queda inalcanzable: `cargar_conjunto()` lanza
    `FileNotFoundError` y la suite de conformidad **se salta en silencio**, que
    es la peor forma de fallar — la compuerta reporta verde sin haber medido.

    Orden de resolución, de más explícito a más adivinado:

    1. `RUES_LINKER_CONFORMIDAD`, tal cual, sin comprobarla. Si está definida
       y es incorrecta el error debe verse, no corregirse por detrás.
    2. `DIRECTORIO_POR_DEFECTO`, si contiene el conjunto (caso checkout).
    3. Búsqueda hacia arriba desde `inicio` (por omisión el directorio de
       trabajo) buscando `data/conformidad/`. Cubre el caso instalado: pytest
       y los scripts se corren desde la raíz del repositorio.
    4. Si nada aparece, `DIRECTORIO_POR_DEFECTO`, para que el mensaje de error
       de `_leer` siga nombrando una ruta y el que lea sepa qué falta.

    Args:
        inicio: desde dónde buscar hacia arriba. Por omisión, `Path.cwd()`.

    Returns:
        El directorio candidato. No se garantiza que exista — quien lee valida.
    """
    crudo = os.environ.get(VARIABLE_ENTORNO, "").strip()
    if crudo:
        return Path(crudo).expanduser()

    if _es_conjunto(DIRECTORIO_POR_DEFECTO):
        return DIRECTORIO_POR_DEFECTO

    base = Path(inicio).resolve() if inicio is not None else Path.cwd().resolve()
    for candidato in (base, *base.parents):
        posible = candidato / "data" / "conformidad"
        if _es_conjunto(posible):
            return posible

    return DIRECTORIO_POR_DEFECTO


#: Casos que el catálogo marca como frontera conocida. Se reportan aparte:
#: reprobarlos no es una regresión, es la deuda que ya estaba declarada. Se
#: separan para que un caso realmente roto no se pueda esconder entre ellos.
TIPOS_FRONTERA = frozenset({"TP_DIFICIL"})

#: Casos cuyo acierto consiste en NO fusionar. Se evalúan al revés y por eso
#: se identifican explícitamente en vez de inferirse.
TIPOS_NEGATIVOS = frozenset({"FP_trap", "TN", "TRUSTED"})

#: Camino del motor con el que se produce la partición evaluada. Hoy el único
#: es `api.dedupe_esquema` (`scripts/conformidad.py` lo usa para los dos
#: escenarios); se registra por nombre para que, cuando exista otro, la
#: evidencia diga con cuál se midió.
CAMINO_POR_DEFECTO = "dedupe_esquema"


@dataclass(frozen=True)
class ResultadoCaso:
    """Veredicto de un caso del catálogo.

    Attributes:
        codigo: código del catálogo (C01, MC04, homonimo_cross…).
        nombre: nombre legible del caso.
        tipo_esperado: TP, FP_trap, TN, TP_DIFICIL, CHAIN, TRUSTED…
        pares: pares del conjunto que pertenecen a este caso.
        aciertos: pares resueltos como el catálogo manda.
        pasa: True si todos los pares del caso se resolvieron bien.
        frontera: el catálogo lo declara frontera conocida.
        detalle: los pares fallados, para poder mirarlos.
    """

    codigo: str
    nombre: str
    tipo_esperado: str
    pares: int
    aciertos: int
    pasa: bool
    frontera: bool
    detalle: tuple[str, ...] = ()

    @property
    def tasa(self) -> float:
        return self.aciertos / self.pares if self.pares else float("nan")


@dataclass(frozen=True)
class Informe:
    """Resultado completo de una corrida de conformidad.

    Attributes:
        corroborar: si la partición se produjo con la corroboración por
            contacto (F3, `--corroborar`) activa. Es lo que quien evaluó
            DECLARA haber activado — `evaluar_dedup`/`evaluar_linkage` reciben
            una partición ya hecha y no pueden comprobarlo—, y va en la
            evidencia porque C09 y C21 solo pasan con ella: un JSON sin este
            campo no dice qué midió (F0.3).
        camino: camino del motor que produjo la partición (`CAMINO_POR_DEFECTO`).
    """

    etiqueta: str
    version: str
    escenario: str
    casos: tuple[ResultadoCaso, ...]
    precision: float
    recall: float
    f1: float
    nota: str = ""
    corroborar: bool = False
    camino: str = CAMINO_POR_DEFECTO
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def firmes(self) -> tuple[ResultadoCaso, ...]:
        """Casos que NO son frontera declarada: aquí no se admite reprobar."""
        return tuple(c for c in self.casos if not c.frontera)

    @property
    def pasa(self) -> bool:
        return all(c.pasa for c in self.firmes)

    def a_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["casos"] = [asdict(c) for c in self.casos]
        d["pasa"] = self.pasa
        return d

    def guardar(self, directorio: Path) -> Path:
        directorio.mkdir(parents=True, exist_ok=True)
        destino = directorio / f"conformidad_{self.escenario}_{self.etiqueta}.json"
        destino.write_text(
            json.dumps(self.a_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return destino

    def resumen(self) -> str:
        """Reporte legible: primero el veredicto, después el detalle."""
        ancho = 78
        linea = "═" * ancho
        firmes = self.firmes
        aprobados = sum(1 for c in firmes if c.pasa)
        frontera = tuple(c for c in self.casos if c.frontera)
        partes = [
            linea,
            f"  CONFORMIDAD · {self.escenario} · {self.etiqueta} · rues-linker {self.version}",
            linea,
            f"  CAMINO         {self.camino} · corroborar: {'sí' if self.corroborar else 'no'}",
            f"  PARES          precision {self.precision:.4f}   recall {self.recall:.4f}"
            f"   F1 {self.f1:.4f}",
            f"  CASOS FIRMES   {aprobados}/{len(firmes)} aprobados",
            "─" * ancho,
        ]
        for caso in sorted(self.casos, key=lambda c: (c.pasa, c.frontera, c.codigo)):
            marca = "✅" if caso.pasa else ("🟡" if caso.frontera else "❌")
            partes.append(
                f"  {marca} {caso.codigo:<18s} {caso.aciertos:>3d}/{caso.pares:<3d}"
                f"  {caso.tipo_esperado:<11s} {caso.nombre[:34]}"
            )
        if frontera:
            partes.append("─" * ancho)
            partes.append(
                f"  🟡 {len(frontera)} caso(s) marcados frontera en el catálogo: "
                "reprobarlos es deuda declarada, no regresión."
            )
        partes.append("─" * ancho)
        partes.append(f"  VEREDICTO: {'PASA' if self.pasa else 'FALLA'}")
        partes.append(linea)
        return "\n".join(partes)


@dataclass(frozen=True)
class ConjuntoConformidad:
    """El conjunto de conformidad ya cargado y validado."""

    dedup_registros: pd.DataFrame
    dedup_pares: pd.DataFrame
    linkage_a: pd.DataFrame
    linkage_b: pd.DataFrame
    linkage_verdad: pd.DataFrame
    catalogo: pd.DataFrame


def _leer(directorio: Path, nombre: str) -> pd.DataFrame:
    ruta = directorio / f"{nombre}.csv"
    if not ruta.is_file():
        raise FileNotFoundError(
            f"Falta '{ruta}'. El conjunto de conformidad se extrae de "
            f"Ground_Truth_Multicampo_v1.xlsx con scripts/conformidad.py --importar."
        )
    return pd.read_csv(ruta, dtype=str, keep_default_na=False)


def cargar_conjunto(directorio: Path | str | None = None) -> ConjuntoConformidad:
    """Carga y valida el conjunto de conformidad.

    Raises:
        FileNotFoundError: si falta alguna hoja.
        ValueError: si la verdad de deduplicación no es consistente con los
            pares declarados — un conjunto que se contradice mide su propio
            defecto, no el de la librería.
    """
    directorio = localizar_conjunto() if directorio is None else Path(directorio)
    conjunto = ConjuntoConformidad(
        dedup_registros=_leer(directorio, "dedup_registros"),
        dedup_pares=_leer(directorio, "dedup_pares"),
        linkage_a=_leer(directorio, "linkage_base_a"),
        linkage_b=_leer(directorio, "linkage_base_b"),
        linkage_verdad=_leer(directorio, "linkage_ground_truth"),
        catalogo=_leer(directorio, "catalogo_casos"),
    )
    _validar_coherencia(conjunto)
    return conjunto


def _validar_coherencia(conjunto: ConjuntoConformidad) -> None:
    """La hoja de pares tiene que decir lo mismo que la columna de grupo.

    Se comprueba en la carga y no en un test, porque un conjunto incoherente
    invalida todas las cifras que salgan de él y conviene enterarse antes de
    publicarlas, no después.
    """
    grupo = dict(
        zip(
            conjunto.dedup_registros["REG_ID"],
            conjunto.dedup_registros["ID_GRUPO_ESPERADO"],
            strict=True,
        )
    )
    contradicciones = []
    for fila in conjunto.dedup_pares.itertuples(index=False):
        a, b = grupo.get(fila.REG_ID_A), grupo.get(fila.REG_ID_B)
        if a is None or b is None:
            contradicciones.append(f"par {fila.PAR_ID}: REG_ID inexistente")
            continue
        declarado = str(fila.MATCH_ESPERADO).strip().lower() == "true"
        if (a == b) != declarado:
            contradicciones.append(
                f"par {fila.PAR_ID} ({fila.REG_ID_A},{fila.REG_ID_B}): la hoja dice "
                f"{declarado} y los grupos dicen {a == b}"
            )
    if contradicciones:
        raise ValueError(
            "El conjunto de conformidad se contradice a sí mismo en "
            f"{len(contradicciones)} par(es); ninguna métrica que salga de él "
            f"significaría nada. Primeros: {contradicciones[:5]}"
        )


def _catalogo_por_codigo(catalogo: pd.DataFrame) -> dict[str, tuple[str, str]]:
    return {
        str(f.CODIGO): (str(f.NOMBRE), str(f.TIPO_ESPERADO))
        for f in catalogo.itertuples(index=False)
    }


def _metricas_de_pares(
    esperados: Sequence[bool], obtenidos: Sequence[bool]
) -> tuple[float, float, float]:
    e = np.asarray(esperados, dtype=bool)
    o = np.asarray(obtenidos, dtype=bool)
    tp = int((e & o).sum())
    fp = int((~e & o).sum())
    fn = int((e & ~o).sum())
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def _agrupar_por_caso(
    codigos: Sequence[str],
    esperados: Sequence[bool],
    obtenidos: Sequence[bool],
    etiquetas: Sequence[str],
    catalogo: Mapping[str, tuple[str, str]],
) -> tuple[ResultadoCaso, ...]:
    """Un veredicto por código de caso, sin promediar entre casos distintos."""
    marco = pd.DataFrame(
        {"codigo": list(codigos), "e": list(esperados), "o": list(obtenidos), "et": list(etiquetas)}
    )
    salida = []
    for codigo, bloque in marco.groupby("codigo", sort=True):
        nombre, tipo = catalogo.get(str(codigo), (str(codigo), "?"))
        acierta = bloque["e"].to_numpy() == bloque["o"].to_numpy()
        fallados = bloque.loc[~acierta, "et"].tolist()
        salida.append(
            ResultadoCaso(
                codigo=str(codigo),
                nombre=nombre,
                tipo_esperado=tipo,
                pares=len(bloque),
                aciertos=int(acierta.sum()),
                pasa=bool(acierta.all()),
                frontera=tipo in TIPOS_FRONTERA,
                detalle=tuple(fallados[:6]),
            )
        )
    return tuple(salida)


def evaluar_dedup(
    conjunto: ConjuntoConformidad,
    grupos_predichos: np.ndarray,
    *,
    etiqueta: str,
    version: str,
    nota: str = "",
    corroborar: bool = False,
    camino: str = CAMINO_POR_DEFECTO,
) -> Informe:
    """Evalúa la deduplicación caso por caso.

    Args:
        conjunto: el conjunto cargado.
        grupos_predichos: etiqueta de grupo por fila, en el orden de
            ``conjunto.dedup_registros``.
        etiqueta: nombre corto de la corrida.
        version: versión de la librería que produjo el resultado.
        nota: qué se estaba probando.
        corroborar: si ``grupos_predichos`` se produjo con F3 activo. Se
            registra tal cual en el informe; quien llama es responsable de
            decir la verdad.
        camino: camino del motor que produjo la partición.

    Raises:
        ValueError: si la longitud no coincide con el conjunto.
    """
    registros = conjunto.dedup_registros
    if len(grupos_predichos) != len(registros):
        raise ValueError(
            f"grupos_predichos tiene {len(grupos_predichos)} etiquetas y el "
            f"conjunto {len(registros)} registros."
        )
    posicion = {str(r): i for i, r in enumerate(registros["REG_ID"])}
    predicho = np.asarray(grupos_predichos)

    esperados, obtenidos, codigos, etiquetas = [], [], [], []
    for fila in conjunto.dedup_pares.itertuples(index=False):
        ia, ib = posicion[str(fila.REG_ID_A)], posicion[str(fila.REG_ID_B)]
        esperados.append(str(fila.MATCH_ESPERADO).strip().lower() == "true")
        obtenidos.append(bool(predicho[ia] == predicho[ib]))
        codigos.append(str(fila.CASO_TIPO))
        etiquetas.append(f"({fila.REG_ID_A},{fila.REG_ID_B})")

    precision, recall, f1 = _metricas_de_pares(esperados, obtenidos)
    return Informe(
        etiqueta=etiqueta,
        version=version,
        escenario="dedup",
        casos=_agrupar_por_caso(
            codigos, esperados, obtenidos, etiquetas, _catalogo_por_codigo(conjunto.catalogo)
        ),
        precision=precision,
        recall=recall,
        f1=f1,
        nota=nota,
        corroborar=corroborar,
        camino=camino,
        extra={"registros": len(registros), "pares": len(esperados)},
    )


def evaluar_linkage(
    conjunto: ConjuntoConformidad,
    pares_predichos: set[tuple[str, str]],
    *,
    etiqueta: str,
    version: str,
    nota: str = "",
    corroborar: bool = False,
    camino: str = CAMINO_POR_DEFECTO,
) -> Informe:
    """Evalúa el cruce A↔B caso por caso.

    Args:
        pares_predichos: conjunto de ``(REG_ID_A, REG_ID_B)`` que la librería
            declaró la misma entidad. Los identificadores son cadenas, como en
            el conjunto.
        corroborar: si ``pares_predichos`` se produjo con F3 activo; se
            registra en el informe, no se comprueba.
        camino: camino del motor que produjo los pares.
    """
    verdad = conjunto.linkage_verdad
    predichos = {(str(a), str(b)) for a, b in pares_predichos}

    esperados, obtenidos, codigos, etiquetas = [], [], [], []
    for fila in verdad.itertuples(index=False):
        clave = (str(fila.REG_ID_A), str(fila.REG_ID_B))
        esperados.append(str(fila.MATCH_ESPERADO).strip().lower() == "true")
        obtenidos.append(clave in predichos)
        codigos.append(str(fila.RETO))
        etiquetas.append(f"A{fila.REG_ID_A}↔B{fila.REG_ID_B}")

    # Los cruces que la librería inventó y el conjunto no declara: cada uno es
    # un falso positivo y se imputa al caso del registro de A, que es donde el
    # catálogo describe el reto.
    reto_a = dict(zip(conjunto.linkage_a["REG_ID_A"], conjunto.linkage_a["RETO"], strict=True))
    declarados = {(str(f.REG_ID_A), str(f.REG_ID_B)) for f in verdad.itertuples(index=False)}
    for a, b in sorted(predichos - declarados):
        esperados.append(False)
        obtenidos.append(True)
        codigos.append(str(reto_a.get(a, "desconocido")))
        etiquetas.append(f"A{a}↔B{b} (no declarado)")

    precision, recall, f1 = _metricas_de_pares(esperados, obtenidos)
    return Informe(
        etiqueta=etiqueta,
        version=version,
        escenario="linkage",
        casos=_agrupar_por_caso(
            codigos, esperados, obtenidos, etiquetas, _catalogo_por_codigo(conjunto.catalogo)
        ),
        precision=precision,
        recall=recall,
        f1=f1,
        nota=nota,
        corroborar=corroborar,
        camino=camino,
        extra={"base_a": len(conjunto.linkage_a), "base_b": len(conjunto.linkage_b)},
    )
