"""
deduplication.strict — record_linkage_pipeline

Componentes:
    - function build_strict_clusters  (origen: notebook celda [152])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import logging

import networkx as nx
import pandas as pd
from rapidfuzz.distance import Levenshtein
from tqdm import tqdm


def build_strict_clusters(
    G: nx.Graph, df: pd.DataFrame, max_nit_distance_link: int
) -> list[set[int]]:
    """
    Construye clusters respetando estrictamente la distancia Levenshtein
    (Versión optimizada con filtrado previo).
    """
    logger = logging.getLogger("StrictClustering")
    logger.info("Iniciando clustering estricto optimizado...")

    # 1. Obtener componentes conectados (sin cambios)
    connected_comps = list(nx.connected_components(G))
    logger.info(f"Se encontraron {len(connected_comps):,} componentes conectados iniciales.")

    # 2. OPTIMIZACIÓN: Separar componentes triviales de los complejos
    trivial_clusters = [c for c in connected_comps if len(c) <= 2]
    components_to_check = [c for c in connected_comps if len(c) > 2]

    logger.info(f"Componentes triviales (≤2 nodos): {len(trivial_clusters):,}")
    logger.info(f"Componentes a verificar (>2 nodos): {len(components_to_check):,}")

    strict_clusters = trivial_clusters  # Iniciar la lista final con los triviales

    # 3. Iterar SOLAMENTE sobre los componentes complejos
    for comp in tqdm(components_to_check, desc="Verificando clusters complejos"):
        comp_list = list(comp)
        strict_G = nx.Graph()
        strict_G.add_nodes_from(comp_list)

        # Reconstruir aristas validando la distancia
        for i, node1 in enumerate(comp_list):
            # Optimización: Obtener el NIT una sola vez por nodo
            nit1 = str(df.at[node1, "NIT_OK"]) if df.at[node1, "NIT_OK"] else ""

            for node2 in comp_list[i + 1 :]:
                if G.has_edge(node1, node2):
                    nit2 = str(df.at[node2, "NIT_OK"]) if df.at[node2, "NIT_OK"] else ""

                    if nit1 and nit2:
                        dist = Levenshtein.distance(nit1, nit2)
                        if dist <= max_nit_distance_link:
                            strict_G.add_edge(node1, node2)
                    else:
                        # Si uno o ambos NITs son vacíos, se mantiene la conexión
                        strict_G.add_edge(node1, node2)

        # Extraer las nuevas componentes del grafo "estricto" y añadirlas
        strict_comps = list(nx.connected_components(strict_G))
        strict_clusters.extend(strict_comps)

    logger.info(
        f"Clustering estricto finalizado. Total de clusters finales: {len(strict_clusters):,}"
    )
    return strict_clusters
