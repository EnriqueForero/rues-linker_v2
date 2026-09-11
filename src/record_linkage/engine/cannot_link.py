"""Restricción *cannot-link* por identificador válido (v0.14.0).

Contexto: Google Colab Free (~12 GB RAM). Se aplica sobre la correlativa ya
clusterizada, así que su costo es O(n) y no reserva estructuras por par.

Por qué existe: el veto de pares del scorer impide fusionar DOS registros con
NIT base válido distinto, pero no impide que un tercer registro **sin**
identificador los una por transitividad (grafo de componentes conexos). Caso
real medido en RUES x Exportaciones DANE::

    ARTESANIAS M & M E U            NIT 900393694 (válido)
    CONSTRUCTORA Y SUMINISTROS M&M  sin NIT           <- puente
    COMERCIAL COLOMBIA M&M SAS      NIT 901491955 (válido)

Los tres caían en un mismo grupo. Dos identificadores oficiales distintos son
evidencia dura de que son entidades distintas: la restricción parte el grupo.

Política con los "puentes" (registros sin identificador válido dentro de un
grupo en conflicto): quedan en un grupo propio. Es la opción conservadora —
sus datos afirman dos identidades incompatibles, así que el sistema no elige
una; el registro queda visible para revisión humana en vez de contaminar una
entidad real. La alternativa (adjudicarlo por similitud de nombre) se descartó
por no ser auditable: adivina precisamente donde la evidencia se contradice.

Referencia del enfoque: Wagstaff & Cardie (2000), *Clustering with
Instance-level Constraints* — https://dl.acm.org/doi/10.5555/645529.658275
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = ["ReporteCannotLink", "aplicar_cannot_link_identificador"]


@dataclass(frozen=True)
class ReporteCannotLink:
    """Resumen auditable de la separación aplicada.

    Attributes:
        grupos_en_conflicto: grupos que contenían >1 identificador válido.
        grupos_creados: grupos nuevos generados al separar.
        registros_reasignados: filas que cambiaron de ``ID_GRUPO``.
        puentes_aislados: filas sin identificador válido que quedaron solas.
    """

    grupos_en_conflicto: int = 0
    grupos_creados: int = 0
    registros_reasignados: int = 0
    puentes_aislados: int = 0
    #: v0.17.0 — conflictos perdonados por tolerancia a digitación: pares de
    #: identificadores del mismo grupo tratados como variantes de captura
    #: (distancia OSA ≤ tolerancia y nombres representativos cohesivos).
    identificadores_fusionados_por_tolerancia: int = 0

    @property
    def hubo_cambios(self) -> bool:
        """True si la restricción modificó al menos un grupo."""
        return self.grupos_en_conflicto > 0

    def resumen(self) -> str:
        """Línea legible para el log del pipeline."""
        if not self.hubo_cambios:
            return "cannot-link: sin conflictos de identificador"
        return (
            f"cannot-link: {self.grupos_en_conflicto:,} grupos con identificador "
            f"válido en conflicto separados en {self.grupos_creados:,} grupos "
            f"nuevos ({self.registros_reasignados:,} registros reasignados, "
            f"{self.puentes_aislados:,} puentes aislados)"
        )


def _mascara_valida(serie: pd.Series) -> np.ndarray:
    """Coerción robusta de una columna de validez a booleano.

    ``NIT_VALID`` viaja como bool, entero o texto según la fase y el motor de
    persistencia. Cualquier valor no reconocido cuenta como NO válido: ante la
    duda no se separa, que es el lado conservador.
    """
    if serie.dtype == bool:
        return serie.to_numpy(dtype=bool)
    if pd.api.types.is_numeric_dtype(serie):
        return serie.fillna(0).astype(float).to_numpy() == 1.0
    texto = serie.astype("string").fillna("").str.strip().str.lower()
    return texto.isin(["1", "true", "t", "si", "sí", "yes", "y"]).to_numpy(dtype=bool)


def aplicar_cannot_link_identificador(
    df: pd.DataFrame,
    *,
    columna_grupo: str = "ID_GRUPO",
    columna_id: str = "NIT_BASE",
    columna_valido: str | None = "NIT_VALID",
    tolerancia_digitacion: int = 0,
    similitud_nombre_rescate: float = 0.90,
    columna_nombre: str = "NOMBRE_LIMPIO",
    max_identificadores_por_grupo: int = 50,
    canonicalizar_dv: bool = True,
) -> tuple[pd.DataFrame, ReporteCannotLink]:
    """Separa los grupos que mezclan dos identificadores válidos distintos.

    La operación es vectorizada y determinista: los grupos nuevos se numeran a
    partir de ``max(ID_GRUPO) + 1`` siguiendo el orden de aparición, de modo
    que dos corridas con la misma entrada producen exactamente las mismas
    etiquetas.

    Args:
        df: correlativa ya clusterizada. No se muta.
        columna_grupo: columna con la etiqueta de grupo a corregir.
        columna_id: identificador canónico (base sin dígito de verificación).
        columna_valido: columna booleana de validez del identificador. Si es
            None, se considera válido todo identificador no vacío.

    Returns:
        Tupla ``(df_corregido, reporte)``. Si no hay conflictos, ``df_corregido``
        es una copia superficial con los mismos valores.

        tolerancia_digitacion: v0.17.0 — con 0 (default) todo par de
            identificadores válidos distintos del mismo grupo es conflicto
            (semántica v0.14.0, bit a bit). Con d > 0, dos identificadores del
            grupo cuya distancia OSA sea ≤ d Y cuyos nombres representativos
            alcancen ``similitud_nombre_rescate`` se tratan como variantes de
            captura del MISMO identificador (una clase); el conflicto se
            evalúa entre clases. Un grupo cuyas variantes colapsan a una sola
            clase deja de estar en conflicto y se preserva entero.
        similitud_nombre_rescate: cohesión mínima de nombre (Indel
            normalizada, la misma familia de métricas del scorer) entre los
            representantes de dos identificadores para fusionarlos en una
            clase. Ignorada con ``tolerancia_digitacion=0``.
        columna_nombre: columna de nombre limpio usada para la cohesión. Si
            no existe en ``df`` y ``tolerancia_digitacion`` > 0, no hay
            rescate (se aplica la semántica estricta) — fallar cerrado.
        canonicalizar_dv: v0.19.0 — compara los identificadores en su forma
            base, quitando dígitos de verificación que validan por módulo 11.
            Con False se recupera la semántica literal de v0.18.0.
        max_identificadores_por_grupo: guarda de costo O(k²): un grupo en
            conflicto con más identificadores únicos que esto no intenta
            rescate y se separa con la semántica estricta.

    Raises:
        KeyError: si falta ``columna_grupo`` o ``columna_id``.
        ValueError: si ``tolerancia_digitacion`` está fuera de [0, 3].
    """
    if not 0 <= int(tolerancia_digitacion) <= 3:
        raise ValueError(
            f"tolerancia_digitacion debe estar en [0, 3]; llegó {tolerancia_digitacion}."
        )
    for requerida in (columna_grupo, columna_id):
        if requerida not in df.columns:
            raise KeyError(
                f"aplicar_cannot_link_identificador requiere la columna '{requerida}'. "
                f"Columnas disponibles: {sorted(df.columns)[:12]}"
            )

    ident = df[columna_id].astype("string").fillna("").str.strip()
    # v0.19.0 — El mismo identificador escrito con y sin dígito de
    # verificación NO son dos identificadores, y partir un grupo por esa
    # diferencia deshace justo lo que el scorer acababa de unir. Se compara en
    # forma canónica; la reducción solo quita dígitos que VALIDAN por módulo 11
    # (ver `matching.identificadores`), así que nunca junta números ajenos.
    if canonicalizar_dv:
        from ..matching.identificadores import bases_canonicas

        ident = pd.Series(bases_canonicas(ident.to_numpy()), index=df.index).astype("string")
    # Una sola conversión a numpy. Con Arrow detrás NO es barata, y más abajo
    # se indexa una vez por grupo en conflicto: convertir ahí dentro costaba
    # el 53 % del tiempo de la función (medido con cProfile).
    identificadores = np.asarray(ident.to_numpy(), dtype=object)
    valido = ident.ne("").to_numpy()
    if columna_valido is not None and columna_valido in df.columns:
        valido &= _mascara_valida(df[columna_valido])

    grupos = df[columna_grupo].to_numpy()
    # Identificadores distintos por grupo, contados solo sobre filas válidas.
    conteo = (
        pd.DataFrame({"g": grupos[valido], "i": identificadores[valido]})
        .drop_duplicates()
        .groupby("g")
        .size()
    )
    en_conflicto = set(conteo[conteo > 1].index)
    if not en_conflicto:
        return df.copy(deep=False), ReporteCannotLink()

    nuevo = pd.Series(grupos, index=df.index).copy()
    siguiente = int(pd.to_numeric(nuevo, errors="coerce").max()) + 1
    reasignados = puentes = creados = fusionados = 0
    grupos_realmente_en_conflicto = 0

    d = int(tolerancia_digitacion)
    nombres = (
        df[columna_nombre].astype("string").fillna("").str.strip().to_numpy()
        if d > 0 and columna_nombre in df.columns
        else None
    )

    # v0.20.0 — Todo lo que no depende del grupo sale del bucle.
    #
    # Medido con cProfile sobre 200 K filas y un 2 % de grupos en conflicto:
    # el 53 % del tiempo se iba en `ArrowStringArray.to_numpy`, llamado 5.316
    # veces. Era `ident.to_numpy()` DENTRO del bucle: cada iteración convertía
    # la columna ENTERA para quedarse con las tres filas de su grupo. Sobre
    # 1 M de filas y 8.891 grupos en conflicto son 8.891 conversiones de un
    # millón de valores, y la función tardaba **437 s** — a 5 M, más de media
    # hora, y la sesión de Colab se acaba antes de terminar.
    #
    # La conversión se hace una vez, y las filas de cada grupo se localizan
    # ordenando una vez: `searchsorted` da el tramo contiguo en O(log n) en
    # vez de recorrer el arreglo por grupo.
    #
    # Medido, con resultado IDÉNTICO (mismo número de grupos, mismas
    # etiquetas):
    #
    #     1 M de filas, 2 % en conflicto:  437,2 s → 10,2 s   (43×)
    #     5 M de filas, 2 % en conflicto:  no terminaba → 56,2 s
    #     RSS a 5 M:                       555 → 618 MiB (+63)
    orden = np.argsort(grupos, kind="stable")
    grupos_ordenados = grupos[orden]
    conflictivos_ordenados = np.array(sorted(en_conflicto), dtype=grupos.dtype)
    inicios = np.searchsorted(grupos_ordenados, conflictivos_ordenados, side="left")
    finales = np.searchsorted(grupos_ordenados, conflictivos_ordenados, side="right")

    for grupo, desde, hasta in zip(conflictivos_ordenados, inicios, finales, strict=True):
        filas = np.sort(orden[desde:hasta])
        ids_grupo = identificadores[filas]
        validos_grupo = valido[filas]

        # v0.17.0 — clase por identificador. Con d=0 cada identificador es su
        # propia clase y el flujo es bit a bit el de v0.14.0.
        clase_de_id = {i: i for i in dict.fromkeys(ids_grupo[validos_grupo])}
        if nombres is not None and 1 < len(clase_de_id) <= max_identificadores_por_grupo:
            clase_de_id, n_fusiones = _fusionar_variantes_de_captura(
                clase_de_id,
                filas,
                ids_grupo,
                validos_grupo,
                nombres,
                tolerancia=d,
                umbral_nombre=float(similitud_nombre_rescate),
            )
            fusionados += n_fusiones

        clases_grupo = set(clase_de_id.values())
        if len(clases_grupo) <= 1:
            # Todas las variantes colapsaron a una clase: no hay conflicto.
            continue
        grupos_realmente_en_conflicto += 1

        # La clase con más filas conserva la etiqueta original: así se
        # minimiza el churn de IDs entre corridas equivalentes. El desempate
        # es alfabético para que no dependa del orden de las filas.
        clases_filas = pd.Series([clase_de_id[i] for i in ids_grupo[validos_grupo]]).value_counts()
        principal = sorted(clases_filas[clases_filas == clases_filas.max()].index)[0]

        etiqueta_por_clase: dict[str, int] = {principal: int(grupo)}
        for fila, identificador, es_valido in zip(filas, ids_grupo, validos_grupo, strict=True):
            if es_valido:
                clase = clase_de_id[identificador]
                if clase not in etiqueta_por_clase:
                    etiqueta_por_clase[clase] = siguiente
                    siguiente += 1
                    creados += 1
                destino = etiqueta_por_clase[clase]
            else:
                # Puente sin identificador: grupo propio, nunca adjudicado.
                destino = siguiente
                siguiente += 1
                creados += 1
                puentes += 1
            if destino != grupo:
                nuevo.iat[int(fila)] = destino
                reasignados += 1

    salida = df.copy(deep=False)
    salida[columna_grupo] = nuevo.to_numpy()
    return salida, ReporteCannotLink(
        grupos_en_conflicto=grupos_realmente_en_conflicto,
        grupos_creados=creados,
        registros_reasignados=reasignados,
        puentes_aislados=puentes,
        identificadores_fusionados_por_tolerancia=fusionados,
    )


#: Léxico corporativo sin poder discriminante: tipos societarios, giros
#: comerciales genéricos y conectores. Un token FUERA de esta lista es la
#: "marca" que distingue una empresa de otra con el mismo giro.
_LEXICO_CORPORATIVO = frozenset(
    [
        "SA",
        "SAS",
        "LTDA",
        "LIMITADA",
        "EU",
        "CIA",
        "CO",
        "COMPANY",
        "COMPANIA",
        "CORP",
        "CORPORACION",
        "INC",
        "S",
        "A",
        "C",
        "EN",
        "E",
        "U",
        "Y",
        "DE",
        "DEL",
        "LA",
        "EL",
        "LAS",
        "LOS",
        "AND",
        "DISTRIBUIDORA",
        "COMERCIALIZADORA",
        "IMPORTADORA",
        "EXPORTADORA",
        "INVERSIONES",
        "INDUSTRIAS",
        "INDUSTRIA",
        "MANUFACTURAS",
        "SERVICIOS",
        "SOLUCIONES",
        "GRUPO",
        "GROUP",
        "INTERNACIONAL",
        "NACIONAL",
        "GLOBAL",
        "COLOMBIA",
        "COLOMBIANA",
        "ANDINA",
        "BOGOTA",
        "MEDELLIN",
        "CALI",
        "HOLDING",
        "SUCURSAL",
        "AGENCIA",
        "HERMANOS",
        "HNOS",
        "FAMILIA",
    ]
)


def _tokens_distintivos(nombre: str) -> frozenset[str]:
    """Tokens alfanuméricos del nombre que no son léxico corporativo."""
    tokens = "".join(c if c.isalnum() else " " for c in nombre.upper()).split()
    return frozenset(t for t in tokens if t not in _LEXICO_CORPORATIVO and len(t) > 1)


def _marcas_compatibles(nombre_a: str, nombre_b: str) -> bool:
    """True si los tokens distintivos de ambos nombres no se contradicen.

    Compatibles cuando algún lado no tiene tokens distintivos (la evidencia es
    ausencia), cuando comparten al menos uno, o cuando algún par de tokens
    difiere en ≤1 edición (typo/OCR: "ANDINA"/"ANDIMA"). Incompatibles cuando
    ambos tienen marca y ninguna coincide ni por aproximación — el caso
    "…COMERCIALIZADORA ML LTDA" vs "…COMERCIALIZADORA TITANS LIMITADA".
    """
    from rapidfuzz.distance import OSA as _osa

    marca_a = _tokens_distintivos(nombre_a)
    marca_b = _tokens_distintivos(nombre_b)
    if not marca_a or not marca_b:
        return True
    if marca_a & marca_b:
        return True
    return any(_osa.distance(ta, tb, score_cutoff=1) <= 1 for ta in marca_a for tb in marca_b)


def _fusionar_variantes_de_captura(
    clase_de_id: dict[str, str],
    filas: np.ndarray,
    ids_grupo: np.ndarray,
    validos_grupo: np.ndarray,
    nombres: np.ndarray,
    *,
    tolerancia: int,
    umbral_nombre: float,
) -> tuple[dict[str, str], int]:
    """Une identificadores del grupo que son variantes de captura entre sí.

    Dos identificadores se fusionan en una clase si su distancia OSA es ≤
    ``tolerancia`` y los nombres representativos (primer nombre no vacío de
    las filas de cada identificador, en orden de fila — determinista) alcanzan
    ``umbral_nombre`` de similitud Indel normalizada. La unión es transitiva
    (union-find sobre los k identificadores únicos del grupo, k acotado por el
    caller); la raíz de cada clase es el identificador alfabéticamente menor,
    para que el resultado no dependa del orden de exploración.

    Returns:
        Tupla ``(clase_de_id actualizado, número de fusiones aplicadas)``.
    """
    from rapidfuzz.distance import OSA as rf_osa, Indel as rf_indel

    unicos = sorted(clase_de_id)
    # Hasta 3 nombres distintos por identificador (orden de fila — determinista):
    # con ruido severo el primer nombre puede ser la peor variante; comparar el
    # mejor par de nombres evita partir por un solo registro ilegible.
    representante: dict[str, list[str]] = {}
    for fila, identificador, es_valido in zip(filas, ids_grupo, validos_grupo, strict=True):
        if not es_valido or not nombres[fila]:
            continue
        lista = representante.setdefault(identificador, [])
        nombre = str(nombres[fila])
        if nombre not in lista and len(lista) < 3:
            lista.append(nombre)

    padre = {i: i for i in unicos}

    def raiz(x: str) -> str:
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    fusiones = 0
    for a_pos in range(len(unicos)):
        for b_pos in range(a_pos + 1, len(unicos)):
            a, b = unicos[a_pos], unicos[b_pos]
            if rf_osa.distance(a, b, score_cutoff=tolerancia) > tolerancia:
                continue
            noms_a, noms_b = representante.get(a, []), representante.get(b, [])
            if not noms_a or not noms_b:
                continue
            mejor, par_mejor = 0.0, ("", "")
            for na in noms_a:
                for nb in noms_b:
                    sim = rf_indel.normalized_similarity(na, nb)
                    if sim > mejor:
                        mejor, par_mejor = sim, (na, nb)
            if mejor < umbral_nombre:
                continue
            # Guardia de marca (v0.17.0): con prefijos genéricos largos
            # ("DISTRIBUIDORA Y COMERCIALIZADORA …") la similitud global se
            # infla aunque el token distintivo difiera (ML vs TITANS — caso
            # real observado en RUES). La misma validación de marca de la
            # consolidación por NIT decide: marcas presentes y distintas → no
            # es variante de captura.
            if not _marcas_compatibles(*par_mejor):
                continue
            ra, rb = raiz(a), raiz(b)
            if ra != rb:
                # Raíz alfabéticamente menor para determinismo.
                menor, mayor = sorted((ra, rb))
                padre[mayor] = menor
                fusiones += 1

    return {i: raiz(i) for i in unicos}, fusiones
