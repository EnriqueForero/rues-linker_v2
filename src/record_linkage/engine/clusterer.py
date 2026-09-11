"""
engine.clusterer — record_linkage_pipeline

Componentes:
    - class EntityClusterer  (origen: notebook celda [118])
    - class OptimizedClusterer  (origen: notebook celda [122])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import gc
import os
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import (
    Any,
    Protocol,
    runtime_checkable,
)

import networkx as nx
import numpy as np
import pandas as pd
from rapidfuzz.distance import Levenshtein
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components as scipy_cc
from tqdm import tqdm

from ..deduplication.strict import build_strict_clusters
from ..utils.logger import CustomLogger
from ..utils.performance import (
    track_performance,
)


@runtime_checkable
class EntityClusterer(Protocol):
    """Protocolo para agrupar entidades."""

    def cluster_entities(self, scored_pairs: pd.DataFrame, n_entities: int) -> dict[int, int]: ...


class _ArrayUnionFind:
    """Union-Find compacto: O(n) memoria y sin objetos de grafo por arista."""

    def __init__(self, n_entities: int):
        self.parent = np.arange(n_entities, dtype=np.int64)
        self.size = np.ones(n_entities, dtype=np.int64)
        self.minimum = np.arange(n_entities, dtype=np.int64)
        self.unions = 0

    def find(self, node: int) -> int:
        parent = self.parent
        root = node
        while parent[root] != root:
            root = int(parent[root])
        while parent[node] != node:
            next_node = int(parent[node])
            parent[node] = root
            node = next_node
        return root

    def union(self, left: int, right: int) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        if self.size[root_left] < self.size[root_right]:
            root_left, root_right = root_right, root_left
        self.parent[root_right] = root_left
        self.size[root_left] += self.size[root_right]
        self.minimum[root_left] = min(self.minimum[root_left], self.minimum[root_right])
        self.unions += 1

    def mapping(self) -> dict[int, int]:
        result: dict[int, int] = {}
        for node in range(len(self.parent)):
            root = self.find(node)
            result[node] = int(self.minimum[root])
        return result


class OptimizedClusterer:
    """
    Clusterer MEJORADO que implementa un enfoque híbrido y escalable:
    1. Usa una técnica de ordenamiento eficiente para conectar NITs idénticos.
    2. Procesa los pares de LSH para conectar clusters adicionales.
    3. Toda la estructura de clusters (Union-Find) se gestiona en disco (SQLite)
       para manejar millones de registros sin agotar la memoria.
    4. Incluye sistema de caché LRU y estadísticas detalladas para monitoreo.
    """

    def __init__(self, profile: dict[str, Any], config: dict[str, Any] | None = None):
        self.profile = profile
        self.config = config or {}
        self.logger = CustomLogger("OptimizedClusterer")

        # Parámetros clave del perfil
        self.batch_size = profile.get("clustering_batch_size", 100_000)
        self.use_strict_clustering = profile.get("use_strict_clusters", False)
        self.max_nit_distance = profile.get("max_nit_distance", 2)

        # v3.2.5 (FASE 2): límite opcional de fuentes por cluster.
        # Si está definido y un cluster supera el límite, se divide
        # post-clustering separando registros por (SRC, NIT). Default None
        # (sin límite) preserva comportamiento <= v3.2.4.
        # Recomendación: usar valores >= número total de fuentes (ej. 4 para
        # 4 fuentes) salvo que se quiera forzar separación.
        self.max_sources_per_group: int | None = profile.get("max_sources_per_group")

        # Configuración de caché LRU
        self.cache_size = profile.get("clustering_cache_size", 50_000)

        # Paths y conexiones de BD
        self._db_path = None
        self._db_conn = None

        # Estadísticas detalladas
        self.stats = {
            "nodes_processed": 0,
            "edges_processed": 0,
            "unions_made": 0,
            "unions_by_nit": 0,
            "unions_by_lsh": 0,
            "clusters_found": 0,
            "largest_cluster_size": 0,
            "processing_time_seconds": 0,
            "cache_hit_rate": 0.0,
        }

        # Timer para medición de tiempo
        self._start_time = None

    @track_performance("Clustering completo de entidades")
    def cluster_entities(
        self,
        scored_pairs: pd.DataFrame | str,
        df_full: pd.DataFrame,  # Añadimos df_full como parámetro requerido
        n_entities: int | None = None,
    ) -> dict[int, int] | str:
        """
        Versión MEJORADA que integra el clustering estricto para mayor precisión.

        FASE 2 - Paso 2.4: Para clustering estándar, usa scipy.sparse en lugar
        de NetworkX. Esto reduce RAM de ~3GB a ~300MB y ahorra 10-20 min.
        El clustering estricto sigue usando NetworkX porque build_strict_clusters
        requiere un grafo nx.Graph como entrada.
        """
        if n_entities is None:
            n_entities = len(df_full)

        self.logger.info(f"Iniciando clustering para {n_entities:,} entidades.")

        # La política disk/streaming existía desde versiones anteriores, pero
        # nunca se consultaba: las pruebas que la monkeypatcheaban ejecutaban
        # dos veces el mismo camino en memoria. Activarla aquí evita cargar
        # todos los scores, una matriz CSR y/o un grafo NetworkX a la vez.
        if self._should_use_disk_processing(scored_pairs, n_entities):
            return self._cluster_streaming(scored_pairs, df_full, n_entities)

        # --- PASO 1: Cargar pares desde DataFrame o DB ---
        self.logger.info("Cargando pares de conexiones...")

        if isinstance(scored_pairs, str) and scored_pairs.endswith(".db"):
            conn = sqlite3.connect(scored_pairs)
            edge_chunks = pd.read_sql_query(
                "SELECT idx_0, idx_1 FROM scored_pairs", conn, chunksize=100_000
            )
            all_edges = []
            for chunk in edge_chunks:
                all_edges.append(chunk.values)
            conn.close()
            edges = np.vstack(all_edges) if all_edges else np.empty((0, 2), dtype=np.int64)
        elif isinstance(scored_pairs, pd.DataFrame):
            edges = scored_pairs[["idx_0", "idx_1"]].values
        else:
            raise ValueError("scored_pairs textual debe apuntar a una base SQLite .db")

        self.logger.info(f"   Aristas cargadas: {len(edges):,}")

        # --- PASO 2: Elegir método según configuración ---
        if self.use_strict_clustering:
            # Clustering estricto: necesita grafo NetworkX para build_strict_clusters
            self.logger.info("Aplicando clustering estricto (requiere NetworkX)...")
            G = nx.Graph()
            G.add_nodes_from(range(n_entities))
            G.add_edges_from(edges)
            final_clusters = build_strict_clusters(G, df_full, self.max_nit_distance)
            del G
            gc.collect()
        else:
            # ═══════════════════════════════════════════════════════════════
            # FASE 2 - Paso 2.4: Clustering estándar con scipy.sparse
            # En lugar de crear un grafo NetworkX (objetos Python por nodo/arista),
            # usamos una matriz dispersa CSR que opera con arrays de C.
            # RAM: de ~3GB a ~300MB para 2M nodos.
            # Tiempo: ahorro de 10-20 minutos.
            # ═══════════════════════════════════════════════════════════════
            self.logger.info("Usando clustering estándar con scipy.sparse (optimizado)...")

            if len(edges) > 0:
                rows = edges[:, 0]
                cols = edges[:, 1]
                data = np.ones(len(rows), dtype=np.int8)
                # Crear matriz simétrica (grafo no dirigido)
                graph = csr_matrix((data, (rows, cols)), shape=(n_entities, n_entities))
                graph = graph + graph.T  # Hacer simétrica
                _n_components, labels = scipy_cc(graph, directed=False)
                del graph
                gc.collect()
            else:
                # Sin aristas: cada nodo es su propio cluster
                labels = np.arange(n_entities)

            # `labels` (array de scipy_cc) se conserva tal cual. El mapeo final se
            # construye de forma VECTORIZADA más abajo (sin bucle Python sobre n
            # nodos). La lista de sets solo se materializa si el split de
            # mega-clusters la necesita.

        # v3.2.5 (FASE 2): split de mega-clusters si max_sources_per_group definido.
        # Política: si un cluster tiene más fuentes únicas que el límite, se divide
        # en sub-clusters por (SRC, NIT), evitando clusters patológicos unidos por
        # cadenas transitivas (A~B, B~C, ...) sin similitud directa.
        split_activo = self.max_sources_per_group is not None and "SRC" in df_full.columns

        if not self.use_strict_clustering and not split_activo:
            # Camino ESTÁNDAR sin split (el de 2M registros): mapeo 100% vectorizado,
            # sin bucle Python sobre n nodos. Representante = nodo mínimo de cada
            # componente, idéntico a `min(cluster_nodes)` de la versión previa.
            nodos = np.arange(n_entities)
            if len(labels):
                rep = pd.Series(nodos).groupby(labels, sort=False).transform("min").to_numpy()
                cluster_mapping = dict(zip(nodos.tolist(), rep.tolist(), strict=True))
                n_clusters = int(np.unique(labels).size)
            else:
                cluster_mapping = {i: i for i in range(n_entities)}
                n_clusters = n_entities
        else:
            # Camino ESTRICTO, o estándar CON split: requiere la lista de clusters
            # como sets (el split la modifica, así que el mapeo se recalcula desde
            # el resultado del split).
            if not self.use_strict_clustering:
                from collections import defaultdict

                cluster_dict: dict[int, set[int]] = defaultdict(set)
                for node_id, cluster_label in enumerate(labels):
                    cluster_dict[int(cluster_label)].add(node_id)
                final_clusters = list(cluster_dict.values())
            if split_activo:
                final_clusters = self._split_mega_clusters(
                    final_clusters, df_full, self.max_sources_per_group
                )
            self.logger.info("Creando mapeo final de ID_GRUPO...")
            cluster_mapping = {}
            for cluster_nodes in tqdm(final_clusters, desc="Mapeando clusters"):
                cluster_id = min(cluster_nodes)
                for node in cluster_nodes:
                    cluster_mapping[node] = cluster_id
            n_clusters = len(final_clusters)

        self.logger.info(f"Clustering completado. {n_clusters:,} clusters finales encontrados.")
        return cluster_mapping

    def _split_mega_clusters(
        self,
        clusters: list,
        df_full: pd.DataFrame,
        max_sources: int,
    ) -> list:
        """v3.2.5 (FASE 2): divide clusters con más fuentes que el límite.

        Algoritmo:
          1. Para cada cluster, contar fuentes únicas (SRC).
          2. Si <= max_sources, mantener intacto.
          3. Si > max_sources, dividir:
             - Agrupar por (SRC, NIT) — registros con mismo NIT en misma
               fuente quedan juntos (son duplicados internos legítimos).
             - Cada (SRC, NIT) único se convierte en sub-cluster aparte.
             - Esto preserva la deduplicación intra-fuente pero rompe la
               unión transitiva sospechosa entre múltiples fuentes.

        Trade-off documentado: este split es CONSERVADOR. Puede separar
        registros que SON la misma empresa si fueron unidos por cadena.
        Para casos donde la fusión inter-fuente es prioritaria, dejar
        `max_sources_per_group=None`.

        Args:
            clusters: lista de sets/iterables, cada uno con los node_ids
                del cluster (formato salida de scipy_cc o build_strict_clusters).
            df_full: DataFrame original con columnas 'SRC' y 'NIT_OK' o 'NIT'.
            max_sources: máximo de fuentes únicas permitidas por cluster.

        Returns:
            Lista de clusters (mismo formato de entrada) con mega-clusters
            divididos. Los demás permanecen intactos.
        """
        if max_sources <= 0:
            self.logger.warning(f"max_sources_per_group={max_sources} <= 0; saltando split.")
            return clusters

        # Detectar columna NIT disponible (NIT_OK preferido, si existe)
        nit_col = "NIT_OK" if "NIT_OK" in df_full.columns else "NIT"
        if nit_col not in df_full.columns:
            self.logger.warning("Columna NIT no encontrada en df_full; saltando split.")
            return clusters

        # Pre-extraer arrays para acceso rápido por índice
        src_arr = df_full["SRC"].to_numpy()
        nit_arr = df_full[nit_col].fillna("").astype(str).to_numpy()

        split_count = 0
        original_count = len(clusters)
        new_clusters = []

        for cluster_nodes in clusters:
            nodes_list = list(cluster_nodes)
            # Contar fuentes únicas en este cluster
            srcs_in_cluster = {src_arr[n] for n in nodes_list}
            if len(srcs_in_cluster) <= max_sources:
                new_clusters.append(cluster_nodes)
                continue

            # Mega-cluster detectado: dividir por (SRC, NIT)
            split_count += 1
            sub_clusters: dict[tuple, list] = {}
            for n in nodes_list:
                key = (src_arr[n], nit_arr[n])
                sub_clusters.setdefault(key, []).append(n)

            for sub_nodes in sub_clusters.values():
                # Mantener tipo set para compatibilidad con downstream
                new_clusters.append(set(sub_nodes) if isinstance(cluster_nodes, set) else sub_nodes)

        if split_count > 0:
            self.logger.info(
                f"🔪 max_sources_per_group={max_sources}: divididos {split_count:,} "
                f"mega-clusters ({original_count:,} → {len(new_clusters):,} clusters totales)"
            )
        else:
            self.logger.info(
                f"✅ max_sources_per_group={max_sources}: ningún cluster excede el límite"
            )

        return new_clusters

    def _split_streaming_mapping(
        self,
        mapping: dict[int, int],
        df_full: pd.DataFrame,
        n_entities: int,
    ) -> dict[int, int]:
        """Aplica ``max_sources_per_group`` sin reconstruir todos los clusters.

        La ruta histórica convertía cada componente en un ``set``. En el camino
        streaming eso anularía buena parte del ahorro de memoria. Aquí se detectan
        de forma vectorizada únicamente los componentes que exceden el límite y
        solo se crean claves ``(componente, SRC, NIT)`` para esos casos.
        """

        max_sources = self.max_sources_per_group
        if max_sources is None or "SRC" not in df_full.columns:
            return mapping
        if max_sources <= 0:
            self.logger.warning(f"max_sources_per_group={max_sources} <= 0; saltando split.")
            return mapping
        nit_col = "NIT_OK" if "NIT_OK" in df_full.columns else "NIT"
        if nit_col not in df_full.columns:
            self.logger.warning("Columna NIT no encontrada en df_full; saltando split.")
            return mapping
        if len(df_full) < n_entities:
            raise ValueError(f"df_full tiene {len(df_full):,} filas para {n_entities:,} entidades.")

        roots = np.fromiter(
            (mapping[node] for node in range(n_entities)),
            dtype=np.int64,
            count=n_entities,
        )
        sources = df_full["SRC"].iloc[:n_entities].to_numpy(copy=False)
        root_source = pd.DataFrame({"root": roots, "source": sources}).drop_duplicates()
        source_counts = root_source.groupby("root", sort=False).size()
        oversized = source_counts.index[source_counts > max_sources].to_numpy(dtype=np.int64)
        if not len(oversized):
            self.logger.info(
                f"max_sources_per_group={max_sources}: ningún cluster excede el límite"
            )
            return mapping

        mask = np.isin(roots, oversized)
        nodes = np.flatnonzero(mask)
        nits = df_full[nit_col].iloc[:n_entities].fillna("").astype(str).to_numpy(copy=False)
        representatives: dict[tuple[int, Any, str], int] = {}
        for node in nodes:
            key = (int(roots[node]), sources[node], nits[node])
            representatives.setdefault(key, int(node))
        for node in nodes:
            key = (int(roots[node]), sources[node], nits[node])
            mapping[int(node)] = representatives[key]

        self.logger.info(
            f"max_sources_per_group={max_sources}: divididos {len(oversized):,} "
            f"mega-clusters en la ruta streaming"
        )
        return mapping

    def _should_use_disk_processing(
        self, scored_pairs: pd.DataFrame | str, n_entities: int
    ) -> bool:
        """Determina si usar procesamiento en disco (SQLite) o en memoria.

        Política "todo en disco": el path en disco es el camino por
        defecto en producción, para tener un solo conjunto de comportamientos y
        simplificar el mantenimiento. Excepciones:

        - `RUES_LINKER_FORCE_MEMORY=1`: fuerza memoria (usado por la suite de
          tests para evitar el overhead de SQLite en datasets de juguete).
        - `RUES_LINKER_FORCE_DISK=1`: fuerza disco (para ejercitar el path
          SQLite incluso en datasets pequeños).
        - Datasets muy pequeños (< 5.000 pares y < 5.000 entidades) usan
          memoria por eficiencia, salvo que se fuerce disco. Esto evita pagar
          el costo de SQLite en cargas triviales sin afectar producción.
        """
        import os

        if os.getenv("RUES_LINKER_FORCE_MEMORY") == "1":
            return False
        if os.getenv("RUES_LINKER_FORCE_DISK") == "1":
            return True

        # Si ya es una BD en disco, usar procesamiento en disco.
        if isinstance(scored_pairs, str) and scored_pairs.endswith(".db"):
            return True

        if isinstance(scored_pairs, pd.DataFrame):
            # Cargas triviales: memoria por eficiencia. Todo lo demás: disco.
            es_trivial = len(scored_pairs) < 5_000 and n_entities < 5_000
            return not es_trivial

        return True  # Por defecto, disco (seguridad y un solo path).

    def _iter_edge_batches(self, scored_pairs: pd.DataFrame | str) -> Iterator[np.ndarray]:
        """Entrega aristas en lotes sin materializar la tabla SQLite completa."""

        if isinstance(scored_pairs, pd.DataFrame):
            missing = {"idx_0", "idx_1"} - set(scored_pairs.columns)
            if missing:
                raise ValueError(f"Faltan columnas de aristas: {sorted(missing)}")
            for start in range(0, len(scored_pairs), self.batch_size):
                batch = scored_pairs.iloc[start : start + self.batch_size][
                    ["idx_0", "idx_1"]
                ].to_numpy(dtype=np.int64, copy=False)
                if len(batch):
                    yield batch
            return

        db_path = Path(scored_pairs).expanduser().resolve()
        if not db_path.is_file():
            raise FileNotFoundError(f"No existe la BD de scores: {db_path}")
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cursor = connection.execute(
                "SELECT idx_0, idx_1 FROM scored_pairs ORDER BY idx_0, idx_1"
            )
            while True:
                rows = cursor.fetchmany(self.batch_size)
                if not rows:
                    break
                yield np.asarray(rows, dtype=np.int64)
        finally:
            connection.close()

    @staticmethod
    def _validate_edge_batch(batch: np.ndarray, n_entities: int) -> None:
        if batch.ndim != 2 or batch.shape[1] != 2:
            raise ValueError(f"Lote de aristas inválido: shape={batch.shape}")
        if len(batch) and (batch.min() < 0 or batch.max() >= n_entities):
            raise ValueError(
                "Una arista referencia una entidad fuera de rango "
                f"[0, {n_entities}): min={int(batch.min())}, max={int(batch.max())}."
            )

    def _union_batches(
        self,
        scored_pairs: pd.DataFrame | str,
        union_find: _ArrayUnionFind,
        n_entities: int,
        *,
        initial_components: _ArrayUnionFind | None = None,
        nit_values: np.ndarray | None = None,
    ) -> int:
        """Une lotes; opcionalmente reproduce el filtro del clustering estricto."""

        processed = 0
        for batch in self._iter_edge_batches(scored_pairs):
            self._validate_edge_batch(batch, n_entities)
            processed += len(batch)
            for left_raw, right_raw in batch:
                left, right = int(left_raw), int(right_raw)
                if initial_components is not None:
                    initial_root = initial_components.find(left)
                    # La implementación histórica aceptaba sin verificar los
                    # componentes de uno o dos nodos. Solo los componentes
                    # mayores reconstruían sus aristas por distancia de NIT.
                    if initial_components.size[initial_root] > 2 and nit_values is not None:
                        nit_left = nit_values[left]
                        nit_right = nit_values[right]
                        if (
                            nit_left
                            and nit_right
                            and Levenshtein.distance(str(nit_left), str(nit_right))
                            > self.max_nit_distance
                        ):
                            continue
                union_find.union(left, right)
        return processed

    def _cluster_streaming(
        self,
        scored_pairs: pd.DataFrame | str,
        df_full: pd.DataFrame,
        n_entities: int,
    ) -> dict[int, int]:
        """Componentes conexos con memoria O(n), leyendo aristas por lotes.

        En modo estricto hace dos recorridos: el primero obtiene los
        componentes iniciales y el segundo aplica exactamente la regla
        histórica de distancia de NIT dentro de componentes complejos. El
        costo es I/O secuencial adicional, a cambio de no crear NetworkX, CSR
        ni un ``vstack`` de todas las aristas.
        """

        started = time.time()
        self.logger.info("Usando Union-Find streaming (sin grafo materializado)...")
        if n_entities < 0:
            raise ValueError("n_entities debe ser >= 0")

        if self.use_strict_clustering:
            initial = _ArrayUnionFind(n_entities)
            edges_processed = self._union_batches(scored_pairs, initial, n_entities)
            final = _ArrayUnionFind(n_entities)
            nit_values: np.ndarray | None = None
            if "NIT_OK" in df_full.columns:
                nit_values = df_full["NIT_OK"].fillna("").astype(str).to_numpy(copy=False)
            self._union_batches(
                scored_pairs,
                final,
                n_entities,
                initial_components=initial,
                nit_values=nit_values,
            )
            union_find = final
        else:
            union_find = _ArrayUnionFind(n_entities)
            edges_processed = self._union_batches(scored_pairs, union_find, n_entities)

        mapping = union_find.mapping()
        mapping = self._split_streaming_mapping(mapping, df_full, n_entities)
        final_labels = np.fromiter(
            (mapping[node] for node in range(n_entities)),
            dtype=np.int64,
            count=n_entities,
        )
        _labels, final_sizes = np.unique(final_labels, return_counts=True)
        self.stats.update(
            {
                "nodes_processed": n_entities,
                "edges_processed": edges_processed,
                "unions_made": union_find.unions,
                "unions_by_lsh": union_find.unions,
                "clusters_found": len(final_sizes),
                "largest_cluster_size": int(final_sizes.max()) if len(final_sizes) else 0,
                "processing_time_seconds": time.time() - started,
            }
        )
        self.logger.info(
            "Clustering streaming completado: "
            f"{self.stats['clusters_found']:,} clusters; "
            f"{edges_processed:,} aristas recorridas"
        )
        return mapping

    def _cluster_in_memory(
        self, scored_pairs: pd.DataFrame, df_full: pd.DataFrame | None, n_entities: int
    ) -> dict[int, int]:
        """
        Clustering en memoria por componentes conexos.

        v2.2.0: reescrito de Union-Find con ``iterrows`` (bucle Python sobre
        cada par scored — letal en millones de pares) a construcción de grafo
        disperso + ``scipy.sparse.csgraph.connected_components``, que resuelve
        los componentes en C. La semántica es idéntica: dos entidades quedan en
        el mismo cluster sii están conectadas por una arista de NIT idéntico o
        por un par scored. Ver MIGRATION_LOG §12.

        El antiguo Union-Find producía exactamente los mismos componentes
        conexos; la equivalencia se valida en tests/test_clusterer_vectorizado.py.
        """
        self.logger.info("Ejecutando clustering en memoria (vectorizado)...")

        filas: list[np.ndarray] = []
        cols: list[np.ndarray] = []

        # Pase 1: aristas por NIT idéntico (vectorizado, sin bucle por par).
        # Para cada grupo de NIT con k>1 miembros, conectamos en estrella al
        # primer miembro (k-1 aristas) — basta para que el componente conexo
        # los una a todos, igual que el Union-Find original.
        if (
            df_full is not None
            and isinstance(df_full, pd.DataFrame)
            and "NIT_OK" in df_full.columns
        ):
            self.logger.info("Conectando NITs idénticos...")
            con_nit = df_full[df_full["NIT_OK"].notna()]
            # group indices posicionales (0..n_entities-1), no labels del índice
            pos = np.arange(len(df_full))
            con_nit_pos = pos[df_full["NIT_OK"].notna().to_numpy()]
            codes, _uniques = pd.factorize(con_nit["NIT_OK"].to_numpy())
            orden = np.argsort(codes, kind="stable")
            codes_ord = codes[orden]
            pos_ord = con_nit_pos[orden]
            # límites de cada grupo de NIT en el array ordenado
            limites = np.flatnonzero(np.diff(codes_ord)) + 1
            grupos = np.split(pos_ord, limites)
            n_nit_edges = 0
            for miembros in grupos:
                if miembros.size > 1:
                    raiz = miembros[0]
                    filas.append(np.full(miembros.size - 1, raiz))
                    cols.append(miembros[1:])
                    n_nit_edges += miembros.size - 1
            self.stats["unions_by_nit"] += n_nit_edges

        # Pase 2: aristas por pares scored (vectorizado — el hot-path real).
        if scored_pairs is not None and len(scored_pairs) > 0:
            self.logger.info(f"Procesando {len(scored_pairs):,} pares scored...")
            i0 = scored_pairs["idx_0"].to_numpy(dtype=np.int64)
            i1 = scored_pairs["idx_1"].to_numpy(dtype=np.int64)
            filas.append(i0)
            cols.append(i1)
            self.stats["unions_by_lsh"] += len(scored_pairs)

        if filas:
            row = np.concatenate(filas)
            col = np.concatenate(cols)
            data = np.ones(row.shape[0], dtype=np.int8)
            grafo = csr_matrix((data, (row, col)), shape=(n_entities, n_entities))
            n_comp, labels = scipy_cc(grafo, directed=False, return_labels=True)
        else:
            # Sin aristas: cada entidad es su propio cluster.
            labels = np.arange(n_entities)
            n_comp = n_entities

        # Mapeo final entidad -> id de cluster (label del componente conexo).
        cluster_mapping = {i: int(labels[i]) for i in range(n_entities)}

        unique_clusters = int(n_comp)
        self.stats["clusters_found"] = unique_clusters
        self.stats["unions_made"] = len(np.concatenate(filas)) if filas else 0
        self.stats["nodes_processed"] = n_entities
        self.stats["edges_processed"] = len(scored_pairs) if scored_pairs is not None else 0
        self.stats["processing_time_seconds"] = time.time() - self._start_time

        self.logger.info(f"Clustering en memoria completado: {unique_clusters:,} clusters únicos")

        return cluster_mapping

    def get_stats(self) -> dict[str, Any]:
        """
        Obtener estadísticas detalladas del proceso de clustering.

        Returns:
            Dict con todas las estadísticas recopiladas.
        """
        return self.stats.copy()

    def update_config(self, new_config: dict[str, Any]):
        """
        Actualizar configuración del clusterer.

        Args:
            new_config: Diccionario con nuevos parámetros de configuración.
        """
        # Actualizar profile
        self.profile.update(new_config)

        # Actualizar parámetros específicos si están presentes
        if "clustering_batch_size" in new_config:
            self.batch_size = new_config["clustering_batch_size"]
        if "clustering_cache_size" in new_config:
            self.cache_size = new_config["clustering_cache_size"]
        if "use_strict_clusters" in new_config:
            self.use_strict_clustering = new_config["use_strict_clusters"]

        self.logger.debug(f"Configuración actualizada: {new_config}")

    def cleanup(self):
        """
        Limpiar recursos y archivos temporales.
        """
        # Cerrar conexión si está abierta
        self._close_db()

        # Intentar eliminar archivo de BD si existe
        if self._db_path and os.path.exists(self._db_path):
            try:
                os.remove(self._db_path)
                self.logger.debug(f"Archivo de clusters eliminado: {self._db_path}")
            except Exception as e:
                self.logger.warning(f"No se pudo eliminar archivo de clusters: {e}")

        # Resetear estadísticas
        self.stats = {key: 0 for key in self.stats}

        # Forzar recolección de basura
        gc.collect()

        self.logger.info("Limpieza de recursos completada")

    def _close_db(self):
        """Cerrar conexión a BD de manera segura."""
        if self._db_conn:
            try:
                self._db_conn.close()
            except Exception as e:
                self.logger.warning(f"Error al cerrar conexión BD: {e}")
            finally:
                self._db_conn = None
