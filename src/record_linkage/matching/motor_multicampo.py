"""matching.motor_multicampo — score ponderado declarativo (F2.6).

Ata esquema + normalizadores + comparadores + bloqueo + política de faltantes
en una decisión de fusión por par candidato. El resultado es un DataFrame de
decisiones con score, veredicto y desglose por campo (auditable).

Salvaguardas del diagnóstico 0.7.6, codificadas como invariantes:
    - F2.4: los faltantes ('' tras normalizar; NaN en GEO) dan similitud 0.0
      (neutro) y NUNCA satisfacen un override. Un override exige score de
      campo ≈ concordancia (típicamente ≥ 0.99).
    - Veto bidireccional: una discrepancia fuerte (score ≤ veto_threshold) en
      un campo con ``veta_discrepancia=True`` prohíbe la fusión aunque el
      resto concuerde (NITs distintos ⇒ entidades distintas).
    - Renormalización por par: con ``PoliticaFaltante.IGNORAR`` el peso de un
      campo ausente se retira y los pesos restantes se renormalizan, de modo
      que el score sigue en [0, 1] y comparable entre pares.

El score combinado se calcula SOLO sobre los pares candidatos que entrega el
bloqueo componible (F2.5): el motor es O(candidatos), no O(n²).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from .campos import EsquemaCampos, PoliticaFaltante, TipoCampo
from .normalizadores import es_faltante, normalizar_campo, normalizar_geo

if TYPE_CHECKING:  # pragma: no cover
    from .campos import CampoSpec

#: Umbral de veto por defecto para campos firmados (mismatch fuerte).
_VETO_FIRMADO = -0.5


@dataclass
class ResultadoMulticampo:
    """Salida del motor multicampo (F2.6)."""

    decisiones: pd.DataFrame  # par (i, j), score, veredicto, concordancias
    desglose: pd.DataFrame  # score por campo y par (auditoría)
    metricas_bloqueo: dict[str, dict[str, float]]
    n_candidatos: int
    n_fusiones: int


def _valores_normalizados(df: pd.DataFrame, esquema: EsquemaCampos) -> dict[str, np.ndarray]:
    """Normaliza cada campo del esquema a array ('' o, en GEO, (n,2) float)."""
    out: dict[str, np.ndarray] = {}
    for campo in esquema.campos:
        if campo.tipo is TipoCampo.GEO:
            la, lo = normalizar_geo(df[campo.nombre], df[str(campo.columna_lon)])
            out[campo.nombre] = np.column_stack(
                [la.to_numpy(dtype="float64"), lo.to_numpy(dtype="float64")]
            )
        else:
            out[campo.nombre] = normalizar_campo(df[campo.nombre], campo).to_numpy()
    return out


def _faltante_lado(valores: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Máscara de faltante para las posiciones ``idx`` de un campo."""
    if valores.ndim == 2:  # GEO
        return np.isnan(valores[idx]).any(axis=1)
    return es_faltante(pd.Series(valores[idx]))


def _scores_campo(
    campo: CampoSpec, valores: np.ndarray, i: np.ndarray, j: np.ndarray
) -> np.ndarray:
    """Similitud por par para un campo, ya con faltantes puestos a 0.0.

    El fancy-indexing funciona igual para series (n,) y para GEO (n, 2):
    ``valores[i]`` entrega las filas del par. (v0.12.0: se eliminó un
    if/else con ramas idénticas que sugería lo contrario.)
    """
    sc = campo.comparador.compare(valores[i], valores[j])  # type: ignore[union-attr]
    falta = _faltante_lado(valores, i) | _faltante_lado(valores, j)
    sc = np.asarray(sc, dtype=np.float64)
    sc[falta] = 0.0
    return sc


def evaluar_esquema(
    df: pd.DataFrame,
    esquema: EsquemaCampos,
    bloqueo,
    *,
    max_candidatos: int | None = None,
    pares_por_lote: int | None = None,
    incluir_desglose: bool = True,
) -> ResultadoMulticampo:
    """Evalúa fusiones sobre ``df`` según ``esquema`` y un bloqueo componible.

    Args:
        df: tabla de entrada (columnas del esquema presentes).
        esquema: ``EsquemaCampos`` declarativo.
        bloqueo: instancia de ``motor_bloqueo.BloqueoComponible``.
        max_candidatos: presupuesto de pares candidatos (v0.12.0). Si la
            unión del bloqueo lo excede, se lanza un error ACCIONABLE en vez
            de dejar que el scoring muera por OOM (medido en la auditoría:
            cubetas LSH degeneradas → 667M pares → kernel muerto sin mensaje).
            None = sin límite (comportamiento previo).
        pares_por_lote: (v0.22.4) puntuar la unión de candidatos en lotes de
            este tamaño. El score de un par no depende de ningún otro par
            (comparadores por par, política de faltantes por par, veto y
            corroboración por par), así que el resultado es BIT-IDÉNTICO al
            de puntuar todo de una vez; lo que cambia es la memoria
            transitoria, que pasa de O(candidatos) a O(lote). Medido en la
            partición USA de la base real: 24,5 M candidatos de una vez →
            pico de 11,4 GiB; con lotes de 1 M → acotado por el lote.
            None = un solo lote (comportamiento previo).
        incluir_desglose: (v0.22.4) con ``False`` no se materializa el
            DataFrame ``desglose`` (score por campo y par), que duplica la
            memoria de ``decisiones``. Las decisiones no cambian.

    Returns:
        ``ResultadoMulticampo`` con decisiones, desglose y métricas de bloqueo.

    Raises:
        ValueError: presupuesto de candidatos excedido (con qué hacer), o
            ``pares_por_lote`` < 1.
    """
    if pares_por_lote is not None and pares_por_lote < 1:
        raise ValueError(f"pares_por_lote={pares_por_lote} debe ser >= 1 o None.")
    esquema.validar(df)
    valores = _valores_normalizados(df, esquema)
    # El presupuesto viaja hasta cada estrategia: se comprueba ANTES de
    # construir arrays O(k²), no después de materializar la unión.
    import inspect

    acepta_limite = "max_pares" in inspect.signature(bloqueo.pares).parameters
    union, _por_estrategia = (
        bloqueo.pares(valores, max_pares=max_candidatos)
        if acepta_limite
        else bloqueo.pares(valores)
    )
    if max_candidatos is not None and len(union) > max_candidatos:
        from .motor_bloqueo import CandidateBudgetExceeded

        raise CandidateBudgetExceeded(len(union), max_candidatos, "bloqueo personalizado")

    if len(union) == 0:
        vacio = pd.DataFrame(columns=["i", "j", "score", "concordancias", "fusion"])
        return ResultadoMulticampo(vacio, pd.DataFrame(), {}, 0, 0)

    i, j = union[:, 0], union[:, 1]
    m = len(union)
    del union

    if pares_por_lote is None or m <= pares_por_lote:
        lotes = [_evaluar_pares(valores, esquema, i, j)]
    else:
        lotes = [
            _evaluar_pares(valores, esquema, i[a : a + pares_por_lote], j[a : a + pares_por_lote])
            for a in range(0, m, pares_por_lote)
        ]
    r = lotes[0] if len(lotes) == 1 else _concatenar_lotes(lotes)
    del lotes

    decisiones = pd.DataFrame(
        {
            "i": i,
            "j": j,
            "score": np.round(r.score, 6),
            "concordancias": r.concordancias,
            "veto": r.veto,
            "veto_levantado": r.veto_levantado,
            "bloqueo_faltante": r.bloqueo_faltante,
            "fusion": r.fusion,
        }
    )
    if incluir_desglose:
        desglose_df = pd.DataFrame(
            {"i": i, "j": j, **{f"sim_{k}": np.round(v, 6) for k, v in r.desglose.items()}}
        )
    else:
        desglose_df = pd.DataFrame()
    metricas: dict[str, dict[str, float]] = {}  # PC/RR es opcional y cara; se pide aparte con GT
    return ResultadoMulticampo(
        decisiones=decisiones,
        desglose=desglose_df,
        metricas_bloqueo=metricas,
        n_candidatos=int(m),
        n_fusiones=int(r.fusion.sum()),
    )


@dataclass
class _PuntuacionPares:
    """Arrays por par que produce :func:`_evaluar_pares` (uso interno)."""

    score: np.ndarray
    concordancias: np.ndarray
    veto: np.ndarray
    veto_levantado: np.ndarray
    bloqueo_faltante: np.ndarray
    fusion: np.ndarray
    desglose: dict[str, np.ndarray]


def _concatenar_lotes(lotes: list[_PuntuacionPares]) -> _PuntuacionPares:
    campos = list(lotes[0].desglose)
    return _PuntuacionPares(
        score=np.concatenate([lote.score for lote in lotes]),
        concordancias=np.concatenate([lote.concordancias for lote in lotes]),
        veto=np.concatenate([lote.veto for lote in lotes]),
        veto_levantado=np.concatenate([lote.veto_levantado for lote in lotes]),
        bloqueo_faltante=np.concatenate([lote.bloqueo_faltante for lote in lotes]),
        fusion=np.concatenate([lote.fusion for lote in lotes]),
        desglose={c: np.concatenate([lote.desglose[c] for lote in lotes]) for c in campos},
    )


def _evaluar_pares(
    valores: dict[str, np.ndarray],
    esquema: EsquemaCampos,
    i: np.ndarray,
    j: np.ndarray,
) -> _PuntuacionPares:
    """Puntúa los pares ``(i[k], j[k])``: score, concordancias, veto y fusión.

    Es la aritmética por par de ``evaluar_esquema`` tal como existía hasta
    0.22.3, extraída sin cambiar una operación para poder llamarla por lotes.
    Todo aquí es elemento a elemento: ningún valor de un par entra en el de
    otro, que es lo que hace válido partir la unión en lotes.
    """
    m = len(i)
    pesos = np.array([c.peso for c in esquema.campos], dtype=np.float64)
    suma_ponderada = np.zeros(m, dtype=np.float64)
    peso_activo = np.zeros(m, dtype=np.float64)
    concordancias = np.zeros(m, dtype=np.int64)
    veto = np.zeros(m, dtype=bool)
    bloqueo_faltante = np.zeros(m, dtype=bool)
    desglose: dict[str, np.ndarray] = {}
    # F3: rastrear cuánto aportó CADA identificador vetante al denominador del
    # score, para poder neutralizarlo si su veto se levanta por corroboración.
    peso_id_por_par = np.zeros(m, dtype=np.float64)
    contrib_id_por_par = np.zeros(m, dtype=np.float64)

    for k, campo in enumerate(esquema.campos):
        sc = _scores_campo(campo, valores[campo.nombre], i, j)
        desglose[campo.nombre] = sc
        falta = _faltante_lado(valores[campo.nombre], i) | _faltante_lado(valores[campo.nombre], j)

        # Contribución al score según política de faltantes (F2.4).
        contrib = np.clip(sc, 0.0, 1.0)  # score negativo no "premia"
        if campo.faltante is PoliticaFaltante.IGNORAR:
            activo = ~falta
            aporte_num = np.where(activo, contrib * pesos[k], 0.0)
            aporte_den = np.where(activo, pesos[k], 0.0)
        elif campo.faltante is PoliticaFaltante.PENALIZAR:
            aporte_num = contrib * pesos[k]  # faltante aporta 0 pero pesa
            aporte_den = np.full(m, pesos[k], dtype=np.float64)
        else:  # BLOQUEAR
            aporte_num = np.where(~falta, contrib * pesos[k], 0.0)
            aporte_den = np.full(m, pesos[k], dtype=np.float64)
            bloqueo_faltante |= falta

        suma_ponderada += aporte_num
        peso_activo += aporte_den

        # Concordancia (γ) SOLO si no falta y supera el umbral del campo.
        concordancias += ((~falta) & (sc >= campo.umbral_concordancia)).astype(np.int64)

        # Veto por discrepancia fuerte (nunca sobre faltantes: F2.4).
        if campo.veta_discrepancia:
            thr = _VETO_FIRMADO if campo.comparador.signed else 0.0  # type: ignore[union-attr]
            discrepa = (~falta) & (sc <= thr)
            veto |= discrepa
            # Acumular el aporte del identificador SOLO en los pares que veta,
            # para retirarlo del score si luego se levanta el veto (F3).
            peso_id_por_par += np.where(discrepa, aporte_den, 0.0)
            contrib_id_por_par += np.where(discrepa, aporte_num, 0.0)

    # ── F3: corroboración — levantar el veto ante evidencia independiente ──
    # Un par con NITs distintos (veto=True) se re-habilita SOLO si campos de
    # alta entropía (email/teléfono) son idénticos y el nombre es muy similar.
    # Nunca opera sobre faltantes: el comparador ya devuelve 0.0 ante ellos,
    # así que sim ≥ umbral_campo (≈0.99) exige un valor real y casi idéntico.
    corr = esquema.corroboracion
    veto_levantado = np.zeros(m, dtype=bool)
    if corr.activa and bool(veto.any()):
        corroborantes = np.zeros(m, dtype=np.int64)
        for nombre_campo in corr.campos_corroborantes:
            sim = desglose[nombre_campo]
            corroborantes += (sim >= corr.umbral_campo).astype(np.int64)

        # Similitud de nombre de empresa en paralelo (evita reunir homónimos).
        nombre_ok = np.ones(m, dtype=bool)
        for campo in esquema.campos:
            if campo.tipo is TipoCampo.NOMBRE_EMPRESA:
                nombre_ok &= desglose[campo.nombre] >= corr.umbral_nombre_empresa

        veto_levantado = veto & (corroborantes >= corr.min_corroborantes) & nombre_ok
        veto = veto & ~veto_levantado

        # Al levantar el veto, el identificador discrepante deja de penalizar el
        # score: se retira su aporte del numerador y del denominador (queda como
        # "no computado", igual que un faltante bajo política IGNORAR).
        suma_ponderada -= np.where(veto_levantado, contrib_id_por_par, 0.0)
        peso_activo -= np.where(veto_levantado, peso_id_por_par, 0.0)

    score = np.divide(
        suma_ponderada,
        peso_activo,
        out=np.zeros_like(suma_ponderada),
        where=peso_activo > 0,
    )
    fusion = (
        (score >= esquema.umbral_score)
        & (concordancias >= esquema.min_concordancias)
        & (~veto)
        & (~bloqueo_faltante)
    )
    return _PuntuacionPares(
        score=score,
        concordancias=concordancias,
        veto=veto,
        veto_levantado=veto_levantado,
        bloqueo_faltante=bloqueo_faltante,
        fusion=fusion,
        desglose=desglose,
    )


def clusters_desde_decisiones(
    n_filas: int,
    decisiones: pd.DataFrame,
    *,
    respetar_vetos: bool = True,
) -> np.ndarray:
    """Componentes conexas de las fusiones, con restricciones de veto (F3.5 anticipado).

    Union-Find sobre los pares con ``fusion=True``, pero — si
    ``respetar_vetos`` — se PROHÍBE unir dos nodos entre los que exista un par
    explícitamente vetado (cannot-link). Esto corta los puentes transitivos
    ``a↔c↔b`` cuando ``a`` y ``b`` son incompatibles (p. ej. NITs distintos),
    el modo de falla que un clustering ingenuo propaga.

    Las fusiones se procesan por score descendente (las uniones más fuertes
    primero); una fusión que crearía una violación cannot-link se descarta.
    Filas sin fusión quedan como singleton. Etiquetas 0-based estables.

    Args:
        n_filas: número de registros (define el rango de índices).
        decisiones: DataFrame con columnas i, j, fusion, veto y score.
        respetar_vetos: si True, aplica las restricciones cannot-link.

    Returns:
        ``np.ndarray[int]`` con la etiqueta de grupo por fila.
    """
    padre = np.arange(n_filas)
    rango = np.zeros(n_filas, dtype=np.int64)

    def find(x: int) -> int:
        raiz = x
        while padre[raiz] != raiz:
            raiz = padre[raiz]
        while padre[x] != raiz:
            padre[x], x = raiz, padre[x]
        return raiz

    # ── Cannot-link en O((n+F)·α) — v0.12.0, cierre del hallazgo H3 ────────
    # Hasta 0.11.x, `hay_conflicto` reconstruía la pertenencia de TODOS los
    # nodos (dos pases de find() sobre n) por CADA unión candidata: costo
    # ~cuadrático, medido 193 s con n=30.000 y 1% de vetos (E10 de la
    # auditoría; a 2M filas: semanas). Ahora cada raíz mantiene el conjunto
    # de raíces "enemigas" (componentes con las que algún miembro tiene un
    # veto). La consulta de conflicto es O(1) esperado y, al unir, el
    # conjunto absorbido se re-apunta una sola vez (small-to-large implícito
    # por union-by-rank ⇒ O(C·log n) total, C = nº de pares vetados).
    # Es el esquema estándar de clustering con restricciones instance-level
    # (Wagstaff & Cardie, ICML 2000). La PARTICIÓN y las ETIQUETAS son
    # bit-idénticas a las de 0.11.x: mismo orden de proceso, mismo
    # union-by-rank, misma condición de descarte (verificado por
    # tests/test_clusters_cannot_link.py contra la implementación de
    # referencia).
    enemigos: dict[int, set[int]] = {}
    if respetar_vetos and "veto" in decisiones.columns:
        for a, b in decisiones.loc[decisiones["veto"], ["i", "j"]].to_numpy():
            ia, ib = int(a), int(b)
            if ia == ib:
                continue
            enemigos.setdefault(ia, set()).add(ib)
            enemigos.setdefault(ib, set()).add(ia)

    fusiones = decisiones.loc[decisiones["fusion"], ["i", "j", "score"]]
    fusiones = fusiones.sort_values("score", ascending=False)
    for a, b, _ in fusiones.to_numpy():
        ra, rb = find(int(a)), find(int(b))
        if ra == rb:
            continue
        if rb in enemigos.get(ra, ()):  # unir rompería un cannot-link
            continue
        # union by rank (idéntico a 0.11.x: preserva raíces y etiquetas)
        if rango[ra] < rango[rb]:
            ra, rb = rb, ra
        padre[rb] = ra
        if rango[ra] == rango[rb]:
            rango[ra] += 1
        # La raíz rb dejó de existir: sus enemigos pasan a ra y las
        # referencias hacia rb se re-apuntan hacia ra.
        set_rb = enemigos.pop(rb, None)
        if set_rb:
            set_ra = enemigos.setdefault(ra, set())
            for e in set_rb:
                ref = enemigos.get(e)
                if ref is not None:
                    ref.discard(rb)
                    ref.add(ra)
            set_ra |= set_rb
            set_ra.discard(ra)  # defensivo: una raíz nunca es su propia enemiga

    raices = np.array([find(k) for k in range(n_filas)])
    _, etiquetas = np.unique(raices, return_inverse=True)
    return etiquetas
