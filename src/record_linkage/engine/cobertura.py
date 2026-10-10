"""engine.cobertura — cobertura por estrellas sobre componentes conexas (v0.22.0).

``clusters_desde_decisiones`` agrupa por componentes conexas, que es
single-linkage: si ``a≈b`` y ``b≈c``, une ``a`` con ``c`` aunque no se
parezcan. Con identificador eso casi no importa —el veto corta el puente—,
pero deduplicando SOLO por nombre el encadenamiento es el modo de falla
dominante: medido sobre 211.949 destinatarios de exportación, produjo grupos
de 200 empresas distintas encadenadas por prefijos genéricos.

Este módulo reparte cada componente en estrellas y deja una garantía
verificable: **todo miembro queda a ≤ (1 − umbral) de SU líder**, que es
exactamente lo que una tabla correlativa afirma cuando dice "este nombre
original corresponde a este nombre final". Sin ella, la correlativa afirma
algo que el pipeline no comprobó nunca.

El líder se elige por cobertura (cubre a más miembros libres): es la
aproximación voraz estándar al *star cover*, determinista con los desempates
declarados. Referencia del problema: Wagstaff & Cardie (ICML 2000) para
clustering con restricciones a nivel de instancia.

F2.1 (ADR-0011) lleva la misma regla a ``linkage()``:
:func:`aplicar_cobertura_sin_identificador` la aplica en L5, después del
cannot-link por identificador, SOLO a los grupos en los que ninguna fila trae
identificador válido —donde el nombre es toda la evidencia— con el
comparador :class:`~record_linkage.matching.nombre_idf.SimilitudNombre`
construido sobre el IDF de ``NOMBRE_LIMPIO`` del corpus y los genéricos
declarados. La perilla de perfil es ``cobertura_sin_identificador`` y la
interpreta :class:`ConfigCoberturaSinIdentificador` en un solo sitio.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

import numpy as np
import pandas as pd

from ..matching.genericos import GENERICOS_ESTRUCTURALES, genericos
from ..matching.nombre_idf import comparador_desde_corpus
from ..pipeline.errores import mensaje_accionable
from .cannot_link import identificadores_validos

__all__ = [
    "ConfigCoberturaSinIdentificador",
    "ReporteCoberturaSinIdentificador",
    "ResultadoCobertura",
    "aplicar_cobertura_sin_identificador",
    "cobertura_estrella",
]

ReglaLider = Literal["cobertura", "masa"]


class ResultadoCobertura:
    """Salida de :func:`cobertura_estrella`.

    Attributes:
        etiquetas: etiqueta de grupo por fila, 0-based y estable.
        es_lider: máscara del representante de cada grupo.
        similitud_al_lider: similitud FIRMADA de cada fila con su líder.
        n_grupos_antes: grupos que entraron (componentes conexas).
        n_grupos_despues: grupos que salieron.
    """

    __slots__ = (
        "es_lider",
        "etiquetas",
        "n_grupos_antes",
        "n_grupos_despues",
        "similitud_al_lider",
    )

    def __init__(
        self,
        etiquetas: np.ndarray,
        es_lider: np.ndarray,
        similitud_al_lider: np.ndarray,
        n_grupos_antes: int,
    ) -> None:
        self.etiquetas = etiquetas
        self.es_lider = es_lider
        self.similitud_al_lider = similitud_al_lider
        self.n_grupos_antes = int(n_grupos_antes)
        self.n_grupos_despues = len(np.unique(etiquetas)) if len(etiquetas) else 0

    @property
    def n_cortes(self) -> int:
        """Cuántos grupos añadió el refinamiento (encadenamientos rotos)."""
        return self.n_grupos_despues - self.n_grupos_antes

    def __repr__(self) -> str:  # pragma: no cover - representación
        return (
            f"ResultadoCobertura(grupos {self.n_grupos_antes} → "
            f"{self.n_grupos_despues}, cortes {self.n_cortes})"
        )


def _matriz_similitud(comparador, nombres: np.ndarray) -> np.ndarray:
    """Matriz k×k de similitud firmada. k es pequeño: una componente conexa."""
    k = len(nombres)
    a, b = np.meshgrid(np.arange(k), np.arange(k), indexing="ij")
    s = comparador.compare(nombres[a.ravel()], nombres[b.ravel()]).reshape(k, k)
    np.fill_diagonal(s, 1.0)
    return s


def cobertura_estrella(
    valores: np.ndarray,
    etiquetas: np.ndarray,
    masa: np.ndarray,
    comparador,
    *,
    similitud_minima: float,
    regla_lider: ReglaLider = "cobertura",
) -> ResultadoCobertura:
    """Parte cada componente en estrellas y nombra un líder por estrella.

    Args:
        valores: el texto comparado (p. ej. el nombre normalizado), por fila.
        etiquetas: componente conexa de cada fila (salida del clustering).
        masa: criterio de desempate, mayor es mejor (p. ej. nº de filas de
            origen, con el valor económico como decimal).
        comparador: objeto con ``compare(izq, der) -> np.ndarray`` firmado.
        similitud_minima: umbral en escala FIRMADA [−1, 1]. Un miembro entra
            en la estrella solo si alcanza este valor contra el líder.
        regla_lider: ``"cobertura"`` elige al que cubre a más miembros libres
            (medoide operativo, maximiza los grupos que sobreviven enteros);
            ``"masa"`` elige simplemente al de mayor masa.

    Returns:
        ``ResultadoCobertura``.

    Raises:
        ValueError: si los arreglos no tienen el mismo largo o el umbral está
            fuera de rango.
    """
    n = len(etiquetas)
    if not (len(valores) == len(masa) == n):
        raise ValueError(
            f"Qué pasó: largos distintos (valores={len(valores)}, "
            f"etiquetas={n}, masa={len(masa)}). Por qué importa: las tres "
            f"describen las MISMAS filas. Qué hacer: alinéelas antes de llamar."
        )
    if not -1.0 <= similitud_minima <= 1.0:
        raise ValueError(
            f"similitud_minima={similitud_minima} fuera de [-1, 1]. Es la escala "
            f"FIRMADA del comparador: un umbral de 0.84 en escala 0-1 "
            f"equivale a {2 * 0.84 - 1:.2f} aquí."
        )
    if regla_lider not in ("cobertura", "masa"):
        raise ValueError("regla_lider debe ser 'cobertura' o 'masa'.")
    if n == 0:
        vacio = np.zeros(0)
        return ResultadoCobertura(vacio.astype(np.int64), vacio.astype(bool), vacio, 0)

    valores = np.asarray(valores)
    nuevas = np.full(n, -1, dtype=np.int64)
    es_lider = np.zeros(n, dtype=bool)
    similitud = np.ones(n, dtype=np.float64)
    # Orden de desempate global: masa descendente, luego valor ascendente.
    orden = np.lexsort((valores, -np.asarray(masa, dtype=np.float64)))
    rango = np.empty(n, dtype=np.int64)
    rango[orden] = np.arange(n)

    siguiente = 0
    for _, posiciones in pd.Series(np.arange(n)).groupby(etiquetas):
        pos = posiciones.to_numpy()
        if len(pos) == 1:
            nuevas[pos] = siguiente
            es_lider[pos] = True
            siguiente += 1
            continue
        matriz = _matriz_similitud(comparador, valores[pos])
        cerca = matriz >= similitud_minima
        libres = np.ones(len(pos), dtype=bool)
        while libres.any():
            if regla_lider == "cobertura":
                alcance = np.where(libres, (cerca & libres[None, :]).sum(axis=1), -1)
                elegido = int(np.lexsort((rango[pos], -alcance))[0])
            else:
                candidatos = np.flatnonzero(libres)
                elegido = int(candidatos[np.argmin(rango[pos][candidatos])])
            miembros = np.union1d(np.flatnonzero(libres & cerca[elegido]), [elegido])
            nuevas[pos[miembros]] = siguiente
            similitud[pos[miembros]] = matriz[elegido, miembros]
            es_lider[pos[elegido]] = True
            libres[miembros] = False
            siguiente += 1
    return ResultadoCobertura(nuevas, es_lider, similitud, n_grupos_antes=len(np.unique(etiquetas)))


# ═══════════════════════════════════════════════════════════════════════════
# F2.1 — cobertura por estrellas en linkage(): grupos sin identificador válido
# ═══════════════════════════════════════════════════════════════════════════

#: Clave del perfil donde vive la perilla.
CLAVE_PERFIL = "cobertura_sin_identificador"


@dataclass(frozen=True)
class ConfigCoberturaSinIdentificador:
    """Perilla ``cobertura_sin_identificador`` del perfil, interpretada una vez.

    Attributes:
        activa: aplicar la cobertura en L5. Ausente en el perfil = inactiva,
            que conserva la huella del banco (regla 4 del CLAUDE.md).
        umbral: similitud mínima nombre–líder en escala 0–1. Medido en el
            banco de 30.486 (ADR-0011): 0,70 → macro-F1 0,8935 / 277 FP sobre
            negativos / recall SIN_ID 0,6471; 0,75 → 0,8906 / 264 / 0,6297;
            0,80 → 0,8803 / 246 / 0,5836. El defecto es 0,80 porque a 139k es
            el que deja el grupo mayor sin identificador en 13 registros (0,70
            lo deja en 100) y porque unir dos empresas distintas duele más que
            dejar sin unir; queda declarado que NO cumple la cifra «recall sin
            NIT ≥ 0,62» de la tarea (0,75 sí la cumple en el banco, pero no
            está medido a 139k). F2.2 barre el umbral por estrato y decide.
        regla_lider: ``"cobertura"`` (cubre a más miembros) o ``"masa"``
            (el nombre más repetido), como en :func:`cobertura_estrella`.
    """

    activa: bool = True
    umbral: float = 0.80
    regla_lider: ReglaLider = "cobertura"

    def __post_init__(self) -> None:
        if not isinstance(self.activa, bool):
            raise TypeError(
                mensaje_accionable(
                    f"{CLAVE_PERFIL}.activa={self.activa!r} no es booleano.",
                    "la cobertura se aplica o no; un valor ambiguo no se interpreta.",
                    "use True o False.",
                )
            )
        if not isinstance(self.umbral, (int, float)) or not 0.0 < float(self.umbral) <= 1.0:
            raise ValueError(
                mensaje_accionable(
                    f"{CLAVE_PERFIL}.umbral={self.umbral!r} fuera de (0, 1].",
                    "es la similitud mínima nombre–líder en escala 0–1; fuera de ese "
                    "rango no corta nada o lo corta todo.",
                    "use un valor como 0.80 (medido en ADR-0011); 2·umbral − 1 es la "
                    "escala firmada que recibe cobertura_estrella.",
                )
            )
        if self.regla_lider not in ("cobertura", "masa"):
            raise ValueError(
                mensaje_accionable(
                    f"{CLAVE_PERFIL}.regla_lider={self.regla_lider!r} no existe.",
                    "decide quién es el nombre final del grupo.",
                    "use 'cobertura' (cubre a más miembros) o 'masa' (el más repetido).",
                )
            )
        object.__setattr__(self, "umbral", float(self.umbral))

    @property
    def similitud_minima(self) -> float:
        """``umbral`` en la escala FIRMADA [−1, 1] de :func:`cobertura_estrella`."""
        return 2.0 * self.umbral - 1.0

    @classmethod
    def desde_perfil(cls, valor: object) -> ConfigCoberturaSinIdentificador:
        """Interpreta lo que trae el perfil, fallando rápido ante lo desconocido.

        Args:
            valor: ``profile.get("cobertura_sin_identificador")``. Se admite
                ausente (``None`` → inactiva, paridad con 0.22.4), un booleano
                (``False`` apaga; ``True`` activa con los valores por defecto)
                o un diccionario con un subconjunto de las claves; las que
                faltan toman el valor por defecto.

        Raises:
            TypeError: si el valor no es de un tipo admitido.
            ValueError: ante una clave desconocida o un valor fuera de rango.
        """
        if valor is None:
            return cls(activa=False)
        if isinstance(valor, bool):
            return cls(activa=valor)
        if not isinstance(valor, Mapping):
            raise TypeError(
                mensaje_accionable(
                    f"{CLAVE_PERFIL}={valor!r} no es un diccionario ni un booleano.",
                    "una perilla mal escrita no se ignora en silencio.",
                    "use False, True o {'activa': bool, 'umbral': float, "
                    "'regla_lider': 'cobertura'|'masa'}.",
                )
            )
        conocidas = {f.name for f in fields(cls)}
        desconocidas = sorted(set(map(str, valor)) - conocidas)
        if desconocidas:
            raise ValueError(
                mensaje_accionable(
                    f"{CLAVE_PERFIL} trae claves desconocidas: {desconocidas}.",
                    "hasta v0.11 un parámetro desconocido se ignoraba y la corrida seguía "
                    "con los defaults — el parámetro que usted creía activo no regía.",
                    f"use solo {sorted(conocidas)}.",
                )
            )
        return cls(**{str(k): v for k, v in valor.items()})


@dataclass(frozen=True)
class ReporteCoberturaSinIdentificador:
    """Resumen auditable de lo que la cobertura hizo en L5; va al manifiesto.

    Attributes:
        umbral: el umbral aplicado (escala 0–1).
        grupos_sin_identificador: grupos en los que ninguna fila trae
            identificador válido (con 1 o más filas).
        grupos_examinados: de esos, los que tienen al menos dos nombres
            distintos (los únicos donde hay algo que cortar).
        grupos_partidos: grupos examinados que quedaron en más de una estrella.
        grupos_creados: etiquetas nuevas que salieron del corte.
        registros_reasignados: filas que cambiaron de ``ID_GRUPO``.
        grupo_mayor_antes: filas del grupo examinado más grande antes.
        grupo_mayor_despues: filas del grupo más grande que salió de ellos.
        nombres_distintos: tamaño del vocabulario con que se midió el IDF.
    """

    umbral: float
    grupos_sin_identificador: int = 0
    grupos_examinados: int = 0
    grupos_partidos: int = 0
    grupos_creados: int = 0
    registros_reasignados: int = 0
    grupo_mayor_antes: int = 0
    grupo_mayor_despues: int = 0
    nombres_distintos: int = 0

    @property
    def hubo_cambios(self) -> bool:
        """True si al menos un grupo se partió."""
        return self.grupos_partidos > 0

    def como_dict(self) -> dict[str, Any]:
        """Forma serializable para ``manifest.json``."""
        return asdict(self)

    def resumen(self) -> str:
        """Línea legible para el log del pipeline."""
        if self.grupos_examinados == 0:
            return (
                f"cobertura sin identificador (umbral {self.umbral:.2f}): sin grupos que "
                f"examinar ({self.grupos_sin_identificador:,} grupos sin identificador, "
                f"ninguno con dos nombres distintos)"
            )
        return (
            f"cobertura sin identificador (umbral {self.umbral:.2f}): "
            f"{self.grupos_examinados:,} grupos examinados, {self.grupos_partidos:,} partidos "
            f"en {self.grupos_creados:,} grupos nuevos ({self.registros_reasignados:,} "
            f"registros reasignados); grupo mayor {self.grupo_mayor_antes:,} → "
            f"{self.grupo_mayor_despues:,} filas"
        )


def aplicar_cobertura_sin_identificador(
    df: pd.DataFrame,
    *,
    config: ConfigCoberturaSinIdentificador,
    comparador: Any | None = None,
    columna_grupo: str = "ID_GRUPO",
    columna_nombre: str = "NOMBRE_LIMPIO",
    columna_id: str = "NIT_BASE",
    columna_valido: str | None = "NIT_VALID",
    canonicalizar_dv: bool = True,
) -> tuple[pd.DataFrame, ReporteCoberturaSinIdentificador]:
    """Reparte en estrellas los grupos sin identificador válido (F2.1).

    Único punto de aplicación de :func:`cobertura_estrella` sobre una
    correlativa clusterizada. Un grupo se examina solo si NINGUNA de sus filas
    trae identificador válido (misma definición que el cannot-link:
    :func:`~record_linkage.engine.cannot_link.identificadores_validos`) y
    tiene al menos dos nombres distintos. Los grupos con identificador válido
    no se tocan: allí el identificador ya decidió y el veto/cannot-link ya
    cortó los puentes.

    La comparación se hace entre nombres DISTINTOS del grupo (los repetidos
    pesan como ``masa``), así que el costo es O(k²) en nombres distintos, no
    en filas. Si no se inyecta ``comparador``, se construye con
    :func:`~record_linkage.matching.nombre_idf.comparador_desde_corpus` sobre
    los nombres distintos de TODA la tabla (el IDF mide informatividad en el
    corpus, no en el grupo) con ``matching.genericos.genericos()``
    neutralizados y ``GENERICOS_ESTRUCTURALES`` como ruido; la geografía NO
    es ruido aquí: en un padrón con identificador «X USA» y «X CANADA» son
    dos sociedades (ADR-0009).

    Etiquetas: la estrella con más filas conserva el ``ID_GRUPO`` original
    (desempate: la estrella que la cobertura creó primero, la de mayor
    alcance); las demás se numeran desde ``max(ID_GRUPO) + 1`` en orden de
    (grupo, estrella), de modo que la misma entrada produce las mismas
    etiquetas. Una fila con nombre vacío en un grupo examinado no alcanza a
    ningún líder (el comparador la puntúa 0) y queda en estrella propia: es
    el lado conservador —dejar sin unir— y queda contado en el reporte.

    Args:
        df: correlativa clusterizada. No se muta.
        config: la perilla interpretada; con ``activa=False`` no hace nada.
        comparador: objeto con ``compare(izq, der) -> np.ndarray`` firmado
            (pruebas); None = el del corpus.
        columna_grupo: etiqueta de grupo a refinar.
        columna_nombre: nombre normalizado que se compara.
        columna_id: identificador canónico.
        columna_valido: validez del identificador; None = todo no vacío.
        canonicalizar_dv: comparar identificadores en forma base.

    Returns:
        ``(df_refinado, reporte)``; sin cortes, una copia superficial.

    Raises:
        KeyError: si falta alguna columna requerida.
    """
    for requerida in (columna_grupo, columna_nombre, columna_id):
        if requerida not in df.columns:
            raise KeyError(
                mensaje_accionable(
                    f"aplicar_cobertura_sin_identificador requiere la columna "
                    f"'{requerida}' y no está (disponibles: {sorted(df.columns)[:12]}).",
                    "sin ella no se sabe qué comparar ni qué grupos tienen identificador.",
                    "llámela sobre la correlativa de L5 (salida de L1 + ID_GRUPO).",
                )
            )
    reporte = ReporteCoberturaSinIdentificador(umbral=config.umbral)
    if not config.activa or len(df) == 0:
        return df.copy(deep=False), reporte

    grupos = df[columna_grupo].to_numpy()
    _, valido = identificadores_validos(
        df, columna_id=columna_id, columna_valido=columna_valido, canonicalizar_dv=canonicalizar_dv
    )
    nombres = df[columna_nombre].astype("string").fillna("").str.strip().to_numpy(dtype=object)

    con_identificador = np.unique(grupos[valido])
    sin_identificador = ~np.isin(grupos, con_identificador)
    n_grupos_sin_id = len(np.unique(grupos[sin_identificador]))
    reporte = ReporteCoberturaSinIdentificador(
        umbral=config.umbral, grupos_sin_identificador=n_grupos_sin_id
    )
    if n_grupos_sin_id == 0:
        return df.copy(deep=False), reporte

    # Representantes: un nombre distinto por grupo, con cuántas filas pesa.
    posiciones = np.flatnonzero(sin_identificador)
    rep = (
        pd.DataFrame({"g": grupos[posiciones], "nombre": nombres[posiciones]})
        .groupby(["g", "nombre"], sort=True)
        .size()
        .rename("masa")
        .reset_index()
    )
    nombres_por_grupo = rep.groupby("g", sort=False)["nombre"].transform("size")
    rep = rep[nombres_por_grupo >= 2].reset_index(drop=True)
    if rep.empty:
        return df.copy(deep=False), reporte

    filas_por_grupo = rep.groupby("g", sort=False)["masa"].sum()
    reporte = ReporteCoberturaSinIdentificador(
        umbral=config.umbral,
        grupos_sin_identificador=n_grupos_sin_id,
        grupos_examinados=len(filas_por_grupo),
        grupo_mayor_antes=int(filas_por_grupo.max()),
    )

    if comparador is None:
        corpus = pd.Series(np.unique(nombres[nombres != ""]))
        comparador = comparador_desde_corpus(
            corpus,
            genericos_neutralizados=genericos(),
            genericos_estructurales=GENERICOS_ESTRUCTURALES,
        )
    cobertura = cobertura_estrella(
        rep["nombre"].to_numpy(),
        rep["g"].to_numpy(),
        rep["masa"].to_numpy(dtype=np.float64),
        comparador,
        similitud_minima=config.similitud_minima,
        regla_lider=config.regla_lider,
    )
    rep["estrella"] = cobertura.etiquetas

    # La estrella con más filas conserva la etiqueta; desempate: la creada
    # primero (etiqueta menor). Las demás se numeran desde max + 1.
    masa_estrella = (
        rep.groupby(["g", "estrella"], sort=True)["masa"]
        .sum()
        .reset_index()
        .sort_values(["g", "masa", "estrella"], ascending=[True, False, True], kind="stable")
    )
    conserva = ~masa_estrella.duplicated("g", keep="first")
    nuevas = masa_estrella[~conserva].sort_values(["g", "estrella"], kind="stable")
    if nuevas.empty:
        return df.copy(deep=False), reporte

    siguiente = int(pd.to_numeric(pd.Series(grupos), errors="coerce").max()) + 1
    masa_estrella["destino"] = masa_estrella["g"]
    masa_estrella.loc[nuevas.index, "destino"] = np.arange(
        siguiente, siguiente + len(nuevas), dtype=np.int64
    )
    rep = rep.merge(masa_estrella[["g", "estrella", "destino"]], on=["g", "estrella"], how="left")

    filas = pd.DataFrame(
        {"g": grupos[posiciones], "nombre": nombres[posiciones], "pos": posiciones}
    )
    filas = filas.merge(rep[["g", "nombre", "destino"]], on=["g", "nombre"], how="inner")
    cambian = filas[filas["destino"] != filas["g"]]

    nuevo = pd.Series(grupos, index=df.index).copy()
    nuevo.iloc[cambian["pos"].to_numpy()] = cambian["destino"].to_numpy()
    salida = df.copy(deep=False)
    salida[columna_grupo] = nuevo.to_numpy()

    partidos = int(nuevas["g"].nunique())
    mayor_despues = int(masa_estrella["masa"].max())
    reporte = ReporteCoberturaSinIdentificador(
        umbral=config.umbral,
        grupos_sin_identificador=n_grupos_sin_id,
        grupos_examinados=len(filas_por_grupo),
        grupos_partidos=partidos,
        grupos_creados=len(nuevas),
        registros_reasignados=len(cambian),
        grupo_mayor_antes=int(filas_por_grupo.max()),
        grupo_mayor_despues=mayor_despues,
        nombres_distintos=int(getattr(getattr(comparador, "pesos_idf", None), "documentos", 0)),
    )
    return salida, reporte
