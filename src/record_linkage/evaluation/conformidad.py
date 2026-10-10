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

Dos formas de veredicto, 43/43 (F2.13)
--------------------------------------
Las hojas de pares nombran 37 de los 43 casos. Los otros seis —C20, C24 y C25
en deduplicación; solo_A, solo_B y homonimo_cross en el cruce— no tienen ningún
par declarado porque su acierto consiste en **no** unirse con nadie, y una
hoja de pares no puede enumerar «con nadie». Hasta F2.13 quedaban sin fila:
verde sin haber medido. Ahora se juzgan **por registro** contra la columna de
verdad (``ID_GRUPO_ESPERADO`` / ``ENTIDAD_ID_ESPERADO``): el registro acierta
si el grupo que le tocó tiene exactamente los miembros que la verdad dice.
Cada ``ResultadoCaso`` declara de cuál de las dos reglas salió (``origen``),
y los 37 con pares se siguen evaluando exactamente igual que antes.

Caminos del motor
-----------------
La partición que se evalúa puede venir de tres caminos (``CAMINOS``):
``dedupe_esquema`` (motor multicampo, el histórico), ``linkage`` (la fachada
de producción ``api.linkage``) y ``flujo.cruce`` (``ejecutar_cruce`` sobre
archivos, con columnas de arrastre). El camino va en la evidencia y en el
nombre del archivo; cada camino tiene su propia línea base y un caso que da
distinto entre caminos se registra tal cual, con su explicación.

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
    "CAMINOS",
    "CAMINO_POR_DEFECTO",
    "DIRECTORIO_POR_DEFECTO",
    "ORIGEN_PARES_DECLARADOS",
    "ORIGEN_VERDAD_POR_REGISTRO",
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

#: Caminos del motor con los que `scripts/conformidad.py` produce la partición
#: evaluada. Se registran por nombre en la evidencia (`Informe.camino`) y en
#: el nombre del archivo, porque cada camino tiene su propia línea base:
#:
#: * ``dedupe_esquema`` — `api.dedupe_esquema`, motor multicampo (histórico).
#: * ``linkage``        — `api.linkage`, la fachada de producción (Orchestrator).
#: * ``flujo.cruce``    — `flujo.cruce.ejecutar_cruce` sobre archivos, con
#:   columnas de arrastre; envuelve a `linkage()`.
CAMINOS = ("dedupe_esquema", "linkage", "flujo.cruce")

#: El camino histórico: el único hasta F2.13 y el que conserva los nombres de
#: archivo de la evidencia de F0 (`conformidad_<escenario>_<etiqueta>.json`).
CAMINO_POR_DEFECTO = CAMINOS[0]

#: De dónde salió el veredicto de un caso (`ResultadoCaso.origen`).
#: `pares_declarados`: de la hoja de pares del libro, como siempre.
#: `verdad_por_registro`: el caso no tiene pares declarados y se juzga por
#: registro contra la columna de verdad (F2.13; ver el docstring del módulo).
ORIGEN_PARES_DECLARADOS = "pares_declarados"
ORIGEN_VERDAD_POR_REGISTRO = "verdad_por_registro"


def _validar_camino(camino: str) -> str:
    if camino not in CAMINOS:
        raise ValueError(
            f"camino={camino!r} no es un camino del motor conocido. "
            "La evidencia registra con qué camino se midió y un nombre libre la "
            f"haría incomparable. Use uno de {list(CAMINOS)}."
        )
    return camino


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
        detalle: los pares (o registros) fallados, para poder mirarlos.
        origen: `ORIGEN_PARES_DECLARADOS` si `pares` cuenta pares de la hoja;
            `ORIGEN_VERDAD_POR_REGISTRO` si el caso no tiene pares declarados
            y `pares` cuenta registros juzgados contra la columna de verdad.
    """

    codigo: str
    nombre: str
    tipo_esperado: str
    pares: int
    aciertos: int
    pasa: bool
    frontera: bool
    detalle: tuple[str, ...] = ()
    origen: str = ORIGEN_PARES_DECLARADOS

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
        camino: camino del motor que produjo la partición; uno de `CAMINOS`.
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

    @property
    def por_registro(self) -> tuple[ResultadoCaso, ...]:
        """Casos juzgados por registro porque no tienen pares declarados."""
        return tuple(c for c in self.casos if c.origen == ORIGEN_VERDAD_POR_REGISTRO)

    def nombre_archivo(self) -> str:
        """`conformidad_<escenario>[_<camino>]_<etiqueta>.json`.

        El camino por defecto no aparece en el nombre: así la evidencia de F0
        (`conformidad_dedup_base.json`…) conserva su nombre y sigue siendo la
        misma serie. Los otros caminos lo llevan, con el punto cambiado por
        guion bajo (`flujo.cruce` → `flujo_cruce`), para que dos caminos nunca
        se pisen el archivo.
        """
        camino = "" if self.camino == CAMINO_POR_DEFECTO else self.camino.replace(".", "_") + "_"
        return f"conformidad_{self.escenario}_{camino}{self.etiqueta}.json"

    def guardar(self, directorio: Path) -> Path:
        directorio.mkdir(parents=True, exist_ok=True)
        destino = directorio / self.nombre_archivo()
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
        por_registro = self.por_registro
        partes = [
            linea,
            f"  CONFORMIDAD · {self.escenario} · {self.etiqueta} · rues-linker {self.version}",
            linea,
            f"  CAMINO         {self.camino} · corroborar: {'sí' if self.corroborar else 'no'}",
            f"  PARES          precision {self.precision:.4f}   recall {self.recall:.4f}"
            f"   F1 {self.f1:.4f}",
            f"  CASOS FIRMES   {aprobados}/{len(firmes)} aprobados",
            f"  VEREDICTOS     {len(self.casos)} casos: {len(self.casos) - len(por_registro)} por "
            f"{ORIGEN_PARES_DECLARADOS} · {len(por_registro)} por {ORIGEN_VERDAD_POR_REGISTRO}"
            " (marcados ·reg)",
            "─" * ancho,
        ]
        for caso in sorted(self.casos, key=lambda c: (c.pasa, c.frontera, c.codigo)):
            marca = "✅" if caso.pasa else ("🟡" if caso.frontera else "❌")
            unidad = "·reg" if caso.origen == ORIGEN_VERDAD_POR_REGISTRO else "    "
            partes.append(
                f"  {marca} {caso.codigo:<18s} {caso.aciertos:>3d}/{caso.pares:<3d}{unidad}"
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


def _miembros_por_grupo(ids: np.ndarray, grupos: np.ndarray) -> dict[Any, frozenset[str]]:
    """{grupo: conjunto de REG_ID que lo integran}."""
    serie = pd.Series(ids, index=pd.Index(grupos)).astype(str)
    return {grupo: frozenset(bloque) for grupo, bloque in serie.groupby(level=0)}


def _caso_por_registro(
    codigo: str,
    catalogo: Mapping[str, tuple[str, str]],
    aciertos: Sequence[bool],
    fallados: Sequence[str],
) -> ResultadoCaso:
    nombre, tipo = catalogo.get(codigo, (codigo, "?"))
    return ResultadoCaso(
        codigo=codigo,
        nombre=nombre,
        tipo_esperado=tipo,
        pares=len(aciertos),
        aciertos=int(sum(aciertos)),
        pasa=all(aciertos),
        frontera=tipo in TIPOS_FRONTERA,
        detalle=tuple(fallados[:6]),
        origen=ORIGEN_VERDAD_POR_REGISTRO,
    )


def _veredictos_por_registro_dedup(
    conjunto: ConjuntoConformidad,
    predicho: np.ndarray,
    codigos_con_pares: set[str],
    catalogo: Mapping[str, tuple[str, str]],
) -> list[ResultadoCaso]:
    """Un veredicto por registro para los casos que no tienen pares declarados.

    C20 (datos faltantes), C24 (stopwords) y C25 (singletons) aciertan cuando
    **no** se unen a nadie, y eso una hoja de pares no lo puede enumerar. Se
    juzga cada registro del caso por su grupo completo: acierta si el grupo
    que le tocó tiene exactamente los miembros que ``ID_GRUPO_ESPERADO`` dice.
    Así, un singleton que absorbe a un registro ajeno reprueba, igual que uno
    que se queda fuera de su grupo.
    """
    registros = conjunto.dedup_registros
    casos = registros["CASO_TIPO"].astype(str).to_numpy()
    sin_pares = sorted(set(casos) - codigos_con_pares)
    if not sin_pares:
        return []
    ids = registros["REG_ID"].astype(str).to_numpy()
    esperado = registros["ID_GRUPO_ESPERADO"].astype(str).to_numpy()
    miembros_esperados = _miembros_por_grupo(ids, esperado)
    miembros_predichos = _miembros_por_grupo(ids, predicho)
    salida = []
    for codigo in sin_pares:
        aciertos, fallados = [], []
        for i in np.flatnonzero(casos == codigo):
            debe, tiene = miembros_esperados[esperado[i]], miembros_predichos[predicho[i]]
            aciertos.append(debe == tiene)
            if debe != tiene:
                sobran, faltan = sorted(tiene - debe), sorted(debe - tiene)
                fallados.append(
                    f"{ids[i]}: "
                    + (f"unido a {sobran}" if sobran else "")
                    + (" · " if sobran and faltan else "")
                    + (f"separado de {faltan}" if faltan else "")
                )
        salida.append(_caso_por_registro(codigo, catalogo, aciertos, fallados))
    return salida


def _cruces_esperados(
    lado: pd.DataFrame, col_id: str, otro: pd.DataFrame, col_otro: str
) -> dict[str, frozenset[str]]:
    """{REG_ID: ids del otro lado con su misma entidad}; entidad ≤ 0 = ninguno.

    La entidad se compara con ``strip`` en los dos lados: un ``' 3'`` que
    trajera una reimportación del libro buscaría ``'3'`` en un diccionario
    con llave ``' 3'`` y el caso reprobaría por una razón falsa.
    """
    por_entidad = (
        otro.assign(_id=otro[col_otro].astype(str))
        .groupby(otro["ENTIDAD_ID_ESPERADO"].astype(str).str.strip())["_id"]
        .agg(frozenset)
        .to_dict()
    )
    ids = lado[col_id].astype(str).to_numpy()
    entidades = lado["ENTIDAD_ID_ESPERADO"].astype(str).str.strip().to_numpy()
    positivas = pd.to_numeric(pd.Series(entidades), errors="coerce").fillna(-1).to_numpy() > 0
    return {
        reg: (por_entidad.get(entidad, frozenset()) if positiva else frozenset())
        for reg, entidad, positiva in zip(ids, entidades, positivas, strict=True)
    }


def _veredictos_por_registro_linkage(
    conjunto: ConjuntoConformidad,
    predichos: set[tuple[str, str]],
    codigos_con_pares: set[str],
    catalogo: Mapping[str, tuple[str, str]],
) -> tuple[list[ResultadoCaso], set[str]]:
    """Veredicto por registro para los retos sin pares declarados (A y B).

    solo_A, solo_B y homonimo_cross aciertan cuando el registro **no cruza con
    nadie**. Cada registro del reto, de la base que sea, acierta si el conjunto
    de registros del otro lado con que la librería lo cruzó es exactamente el
    que ``ENTIDAD_ID_ESPERADO`` manda (vacío si la entidad es negativa).

    Returns:
        Los casos y el conjunto de códigos juzgados así (para que un cruce
        inventado que ya cuenta aquí no se impute además como par).
    """
    a, b = conjunto.linkage_a, conjunto.linkage_b
    retos = set(a["RETO"].astype(str)) | set(b["RETO"].astype(str))
    sin_pares = sorted(retos - codigos_con_pares)
    if not sin_pares:
        return [], set()
    esperado_a = _cruces_esperados(a, "REG_ID_A", b, "REG_ID_B")
    esperado_b = _cruces_esperados(b, "REG_ID_B", a, "REG_ID_A")
    obtenido_a: dict[str, set[str]] = {}
    obtenido_b: dict[str, set[str]] = {}
    for x, y in predichos:
        obtenido_a.setdefault(x, set()).add(y)
        obtenido_b.setdefault(y, set()).add(x)
    lados = (
        ("A", a, "REG_ID_A", esperado_a, obtenido_a, "B"),
        ("B", b, "REG_ID_B", esperado_b, obtenido_b, "A"),
    )
    salida = []
    for codigo in sin_pares:
        aciertos, fallados = [], []
        for marca, lado, col_id, esperado, obtenido, otra in lados:
            bloque = lado.loc[lado["RETO"].astype(str) == codigo, col_id].astype(str)
            for reg in bloque:
                debe, tiene = esperado[reg], frozenset(obtenido.get(reg, ()))
                aciertos.append(debe == tiene)
                if debe != tiene:
                    fallados.append(
                        f"{marca}{reg}: cruza con {otra}{sorted(tiene)}, debía {otra}{sorted(debe)}"
                    )
        salida.append(_caso_por_registro(codigo, catalogo, aciertos, fallados))
    return salida, set(sin_pares)


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
    _validar_camino(camino)
    registros = conjunto.dedup_registros
    if len(grupos_predichos) != len(registros):
        raise ValueError(
            f"grupos_predichos tiene {len(grupos_predichos)} etiquetas y el "
            f"conjunto {len(registros)} registros."
        )
    posicion = {str(r): i for i, r in enumerate(registros["REG_ID"])}
    predicho = np.asarray(grupos_predichos)
    if pd.isna(predicho).any():
        raise ValueError(
            "grupos_predichos trae registros sin grupo (NaN/None). Un registro sin "
            "grupo no se puede juzgar; alinee la partición por REG_ID antes de evaluar."
        )

    esperados, obtenidos, codigos, etiquetas = [], [], [], []
    for fila in conjunto.dedup_pares.itertuples(index=False):
        ia, ib = posicion[str(fila.REG_ID_A)], posicion[str(fila.REG_ID_B)]
        esperados.append(str(fila.MATCH_ESPERADO).strip().lower() == "true")
        obtenidos.append(bool(predicho[ia] == predicho[ib]))
        codigos.append(str(fila.CASO_TIPO))
        etiquetas.append(f"({fila.REG_ID_A},{fila.REG_ID_B})")

    precision, recall, f1 = _metricas_de_pares(esperados, obtenidos)
    catalogo = _catalogo_por_codigo(conjunto.catalogo)
    casos = [
        *_agrupar_por_caso(codigos, esperados, obtenidos, etiquetas, catalogo),
        *_veredictos_por_registro_dedup(conjunto, predicho, set(codigos), catalogo),
    ]
    return Informe(
        etiqueta=etiqueta,
        version=version,
        escenario="dedup",
        casos=tuple(sorted(casos, key=lambda c: c.codigo)),
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
    _validar_camino(camino)
    verdad = conjunto.linkage_verdad
    predichos = {(str(a), str(b)) for a, b in pares_predichos}
    catalogo = _catalogo_por_codigo(conjunto.catalogo)

    esperados, obtenidos, codigos, etiquetas = [], [], [], []
    for fila in verdad.itertuples(index=False):
        clave = (str(fila.REG_ID_A), str(fila.REG_ID_B))
        esperados.append(str(fila.MATCH_ESPERADO).strip().lower() == "true")
        obtenidos.append(clave in predichos)
        codigos.append(str(fila.RETO))
        etiquetas.append(f"A{fila.REG_ID_A}↔B{fila.REG_ID_B}")
    por_registro, codigos_por_registro = _veredictos_por_registro_linkage(
        conjunto, predichos, set(codigos), catalogo
    )
    n_declarados = len(esperados)

    # Los cruces que la librería inventó y el conjunto no declara: cada uno es
    # un falso positivo para la precisión y se imputa al caso del registro de
    # A, que es donde el catálogo describe el reto — salvo que ese reto ya se
    # juzgue por registro (solo_A, homonimo_cross): ahí el cruce ya cuenta como
    # registro fallado y no se cuenta dos veces.
    reto_a = dict(zip(conjunto.linkage_a["REG_ID_A"], conjunto.linkage_a["RETO"], strict=True))
    declarados = {(str(f.REG_ID_A), str(f.REG_ID_B)) for f in verdad.itertuples(index=False)}
    en_caso = [True] * n_declarados
    for a, b in sorted(predichos - declarados):
        reto = str(reto_a.get(a, "desconocido"))
        esperados.append(False)
        obtenidos.append(True)
        codigos.append(reto)
        etiquetas.append(f"A{a}↔B{b} (no declarado)")
        en_caso.append(reto not in codigos_por_registro)

    precision, recall, f1 = _metricas_de_pares(esperados, obtenidos)
    filtro = np.asarray(en_caso)
    casos = [
        *_agrupar_por_caso(
            list(np.asarray(codigos, dtype=object)[filtro]),
            list(np.asarray(esperados)[filtro]),
            list(np.asarray(obtenidos)[filtro]),
            list(np.asarray(etiquetas, dtype=object)[filtro]),
            catalogo,
        ),
        *por_registro,
    ]
    return Informe(
        etiqueta=etiqueta,
        version=version,
        escenario="linkage",
        casos=tuple(sorted(casos, key=lambda c: c.codigo)),
        precision=precision,
        recall=recall,
        f1=f1,
        nota=nota,
        corroborar=corroborar,
        camino=camino,
        extra={"base_a": len(conjunto.linkage_a), "base_b": len(conjunto.linkage_b)},
    )
