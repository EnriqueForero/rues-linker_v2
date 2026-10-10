"""record_linkage.config.profiles — Perfiles de configuración LSH.

Cada perfil define los parámetros completos del pipeline para un escenario:
parámetros LSH, scoring, pesos, batch sizes, fuentes trusted, etc.

Origen:
- PERFILES_BASE: notebook celda [87]
- config_produccion_it7: notebook celda [260] (CELDA 8.3 - IT-7 OPTIMIZADA)
- crear_config_orchestrator: notebook celda [87]

v3.2.4 — FASE 1 de auditoría:
- Nuevo perfil `produccion_calibrada` con parámetros validados contra
  ground_truth_grande.csv (F1=0.84, P=1.00, R=0.73).
- Lista DEAD_CONFIG_KEYS con parámetros que el código NO lee y que solo
  generan ilusión configuracional. El validador emite warnings cuando
  un config contiene estas claves.
- Bug NIT vacío + max_nit_distance: ver scorer.nit_empty_passes_filter.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..golden._priorities import obtener_prioridades_fuentes
from ..utils.output import safe_print as print

# ═════════════════════════════════════════════════════════════════════════
#  CLAVES DEAD CODE (validadas empíricamente en v3.2.4)
# ═════════════════════════════════════════════════════════════════════════
# Parámetros que aparecen en config_produccion_it7 y otros configs pero que
# el flujo Orchestrator.run() NO LEE. Mantenerlos en la configuración crea
# ilusión de calibración. Documentado en docs/AUDITORIA_FASE1.md.
#
# Verificado con:
#   grep -rn "\.get(.<param>.\|config\[.<param>.\]" src/ --include="*.py"
#
# Si quieres recuperar el comportamiento que estas claves prometen, hay que
# IMPLEMENTARLAS en el código (ver plan de Fase 2).
DEAD_CONFIG_KEYS: set[str] = {
    # Top-level
    "confidence_weights",  # 0 lecturas, solo aparece en perfiles
    "max_sources_per_group",  # ✅ v3.2.5 IMPLEMENTADO en clusterer
    #    Conservado aquí para legacy detection;
    #    si ves esto en config y NO usas v3.2.5+,
    #    sigue siendo dead.
    # v3.2.7: cross_source_validation movido a DEPRECATED_CONFIG_KEYS
    "validation_rules",  # 0 lecturas (la mayoría de sub-claves dead)
    "performance_settings",  # 0 lecturas
    "min_confidence_export",  # 0 lecturas
    "memory_monitor_interval",  # 0 lecturas
    "sqlite_cache_size",  # 0 lecturas
    "commit_interval",  # 0 lecturas
    "correlative_chunk_size",  # 0 lecturas reales (7 definiciones, 0 .get)
    "aggressive_gc",  # 0 lecturas (solo se define)
    # source_quality_weights:
    #   - tiene 2 lecturas pero SOLO usa las KEYS para orden de fuentes.
    #   - los VALORES (0.99, 0.90, etc.) son ignorados.
    #   - lo marcamos como WARN parcial.
    # v3.2.5: ahora también se usa para desempates en AdvancedValueSelector
    # cuando se proporciona explícitamente (sigue siendo PARTIAL).
}

# Claves que ANTES eran dead y AHORA están implementadas (en v3.2.5+ / v3.2.7).
# Mantenidas como referencia documental de qué cambió.
RESURRECTED_CONFIG_KEYS: set[str] = {
    "max_sources_per_group",  # ✅ v3.2.5 — clusterer.py split de mega-clusters
    "min_sources_for_golden",  # ✅ v3.2.7 — golden/generator.py filtro post-gen
    "nit_empty_passes_filter",  # ✅ v3.2.4 — scorer.py fix bug NIT vacío
}

# Claves deprecadas: el código no las lee y NO se planea implementar.
DEPRECATED_CONFIG_KEYS: set[str] = {
    "cross_source_validation",  # v3.2.7: usar `cross_source_only` en su lugar
}

# Claves con lectura parcial (no son dead pero su comportamiento es limitado)
PARTIAL_CONFIG_KEYS: set[str] = {
    "source_quality_weights",  # Solo se usa el orden de las keys, no los valores
    "export_settings",  # Ninguna clave se lee desde F1.11 (excel_max_rows retirada:
    # el Excel va completo o <alias>_LEEME.xlsx; L6 avisa si la ve)
}


PERFILES_BASE = {
    # ═══════════════════════════════════════════════════════════════════════════
    # PERFIL: PRUEBA RÁPIDA (para desarrollo y testing)
    # ═══════════════════════════════════════════════════════════════════════════
    "prueba_rapida": {
        "description": "Prueba rápida con parámetros conservadores",
        # Limpieza de texto
        "cleaning_mode": "BALANCEADO",
        # v0.13.0 (T1 dian-comercio): category en columnas de baja cardinalidad.
        # OPT-IN: el default False no cambia comportamiento.
        "use_categorical_dtypes": False,
        # v0.14.0 — salvaguardas de precisión. Apagarlas exige una razón:
        # medido sobre RUES x Exportaciones, sin ellas 226 grupos mezclaban
        # 2-3 NIT válidos distintos (empresas distintas fusionadas).
        "veto_nit_base_distinto": True,
        "cannot_link_identificador": True,
        # F2.1: cobertura por estrellas en grupos sin identificador válido. Se
        # declara apagada: solo `produccion_estandar` está medido (ADR-0011).
        # Para activarla: {"activa": True, "umbral": 0.80, "regla_lider": "cobertura"}.
        "cobertura_sin_identificador": {
            "activa": False,
            "umbral": 0.80,
            "regla_lider": "cobertura",
        },
        "tolerancia_digitacion_identificador": 0,
        "dv_es_mismo_identificador": True,
        "llaves_bloqueo": (),
        "tokens_bloqueo": (),
        "similitud_nombre_rescate_identificador": 0.90,
        # ── Ponderación IDF del nombre (v0.18.0) ─────────────────────────
        # 0.0 en ambas = comportamiento idéntico al de 0.17.4.
        # `..._sin_identificador` pondera los tokens por informatividad SOLO
        # en los pares donde ningún lado tiene identificador utilizable, que
        # es donde el nombre es toda la evidencia. Medido sobre
        # ground_truth_grande.csv, ahí vive el 100 % de los falsos positivos.
        # Piso de similitud de cadena compactada (v0.18.0). Recupera pares
        # que un error de espaciado rompe para el comparador por tokens.
        # 0.0 = desactivada (comportamiento idéntico al de 0.17.4).
        "similitud_compacta_min": 0.0,
        "idf_veto_min_sin_identificador": 0.0,
        "idf_weight_blend": 0.0,
        "idf_weight_blend_sin_identificador": 0.0,
        "liberar_fuentes_tras_l1": False,
        "remove_top_words": 0,
        # LSH - Locality Sensitive Hashing
        "lsh_permutations": 128,
        "lsh_threshold": 0.55,
        "lsh_ngram": 3,
        # Scoring
        "score_threshold": 0.45,
        "min_name_similarity": 0.30,
        "max_nit_distance": 3,
        # Pesos de similitud
        "weights": {"name": 0.5, "nit": 0.5, "phonetic": 0.0},
        # Recursos (conservador para Colab)
        "batch_size": 50_000,
        "lsh_batch_size": 30_000,
        "scoring_batch_size": 50_000,
        # Opciones
        "cross_source_only": False,  # ⚠️ DEPRECADO: usar trusted_unique_sources
        "trusted_unique_sources": [],  # ✅ Paso 1.6: fuentes que no se comparan internamente
        "use_disk_cache": True,
        "force_disk_results": True,
        "use_strict_clusters": True,  # ← NUEVO: Paso 1.1
    },
    # ═══════════════════════════════════════════════════════════════════════════
    # PERFIL: PRODUCCIÓN ESTÁNDAR (IT-7 - balance óptimo)
    # ═══════════════════════════════════════════════════════════════════════════
    "produccion_estandar": {
        "description": "Producción estándar - Balance calidad/tiempo (8-9 horas)",
        # Limpieza
        "cleaning_mode": "BALANCEADO",
        # v0.13.0 (T1 dian-comercio): category en columnas de baja cardinalidad.
        # OPT-IN: el default False no cambia comportamiento.
        "use_categorical_dtypes": False,
        # v0.14.0 — salvaguardas de precisión. Apagarlas exige una razón:
        # medido sobre RUES x Exportaciones, sin ellas 226 grupos mezclaban
        # 2-3 NIT válidos distintos (empresas distintas fusionadas).
        "veto_nit_base_distinto": True,
        "cannot_link_identificador": True,
        # ── F2.1 (ADR-0011): cobertura por estrellas en grupos sin identificador
        # válido. Cambio DECLARADO en este perfil: medido en el banco 30.486,
        # macro-F1 0,892 → 0,880 y FP sobre negativos 287 → 246; en el
        # sintético de 139k precisión 0,34 → 0,98 y grupo mayor 630 → 13.
        # `activa: False` reproduce la huella 1e365ba8 de 0.22.4. Similitud
        # mínima firmada = 2·umbral − 1 (engine.cobertura).
        "cobertura_sin_identificador": {
            "activa": True,
            "umbral": 0.80,
            "regla_lider": "cobertura",
        },
        "tolerancia_digitacion_identificador": 0,
        "dv_es_mismo_identificador": True,
        "llaves_bloqueo": (),
        "tokens_bloqueo": (),
        "similitud_nombre_rescate_identificador": 0.90,
        # ── Ponderación IDF del nombre (v0.18.0) ─────────────────────────
        # 0.0 en ambas = comportamiento idéntico al de 0.17.4.
        # `..._sin_identificador` pondera los tokens por informatividad SOLO
        # en los pares donde ningún lado tiene identificador utilizable, que
        # es donde el nombre es toda la evidencia. Medido sobre
        # ground_truth_grande.csv, ahí vive el 100 % de los falsos positivos.
        # Piso de similitud de cadena compactada (v0.18.0). Recupera pares
        # que un error de espaciado rompe para el comparador por tokens.
        # 0.0 = desactivada (comportamiento idéntico al de 0.17.4).
        "similitud_compacta_min": 0.0,
        "idf_veto_min_sin_identificador": 0.0,
        "idf_weight_blend": 0.0,
        "idf_weight_blend_sin_identificador": 0.0,
        "liberar_fuentes_tras_l1": False,
        "remove_top_words": 0,
        # LSH - Configuración IT-7 validada
        "lsh_permutations": 252,
        "lsh_threshold": 0.55,
        "lsh_ngram": 3,
        # Scoring
        "score_threshold": 0.45,
        "min_name_similarity": 0.30,
        "max_nit_distance": 3,
        # Pesos
        "weights": {"name": 0.5, "nit": 0.5, "phonetic": 0.0},
        # Recursos
        "batch_size": 100_000,
        "lsh_batch_size": 50_000,
        "lsh_chunk_size": 150_000,
        "scoring_batch_size": 75_000,
        "clustering_batch_size": 100_000,
        "golden_chunk_size": 100_000,
        # Memoria
        "max_memory_gb": 8.5,
        # v3.2.5 (FASE 2): eliminada clave `aggressive_gc` (dead code).
        # Opciones
        "cross_source_only": False,  # ⚠️ DEPRECADO: usar trusted_unique_sources
        "trusted_unique_sources": [],  # ✅ Paso 1.6
        "use_disk_cache": True,
        "force_disk_results": True,
        "use_strict_clusters": True,  # ← NUEVO: Paso 1.1
    },
    # ═══════════════════════════════════════════════════════════════════════════
    # PERFIL: FUENTES RUIDOSAS (v0.17.0 — identificador como evidencia graduada)
    # ═══════════════════════════════════════════════════════════════════════════
    # Para bases capturadas a mano (CRM, formularios, digitación) donde el
    # identificador trae errores de tecleo y el nombre es la evidencia fuerte.
    # Diferencias con produccion_estandar, cada una medida sobre
    # Ground_Truth_Robusto_V3 (7.368 filas, 2.000 grupos, ruido hasta EXTREME):
    #   - tolerancia_digitacion_identificador=2: dos bases a distancia OSA ≤ 2
    #     (digitación, dígito extra/faltante, transposición) con nombre
    #     cohesivo son variantes de captura, no entidades distintas.
    #   - lsh_threshold 0.45: recupera candidatos con más ruido de nombre.
    # El caso institucional RUES (identificador oficial confiable) debe seguir
    # usando produccion_estandar: allí el veto binario ES la semántica.
    "fuentes_ruidosas": {
        "description": "Fuentes con identificador sucio - evidencia graduada (v0.17.0)",
        "cleaning_mode": "BALANCEADO",
        "use_categorical_dtypes": False,
        "veto_nit_base_distinto": True,
        "cannot_link_identificador": True,
        # F2.1: cobertura por estrellas en grupos sin identificador válido. Se
        # declara apagada: solo `produccion_estandar` está medido (ADR-0011).
        # Para activarla: {"activa": True, "umbral": 0.80, "regla_lider": "cobertura"}.
        "cobertura_sin_identificador": {
            "activa": False,
            "umbral": 0.80,
            "regla_lider": "cobertura",
        },
        "tolerancia_digitacion_identificador": 2,
        "dv_es_mismo_identificador": True,
        "llaves_bloqueo": (),
        "tokens_bloqueo": (),
        "similitud_nombre_rescate_identificador": 0.82,
        # ── Ponderación IDF del nombre (v0.18.0) ─────────────────────────
        # 0.0 en ambas = comportamiento idéntico al de 0.17.4.
        # `..._sin_identificador` pondera los tokens por informatividad SOLO
        # en los pares donde ningún lado tiene identificador utilizable, que
        # es donde el nombre es toda la evidencia. Medido sobre
        # ground_truth_grande.csv, ahí vive el 100 % de los falsos positivos.
        # Piso de similitud de cadena compactada (v0.18.0). Recupera pares
        # que un error de espaciado rompe para el comparador por tokens.
        # 0.0 = desactivada (comportamiento idéntico al de 0.17.4).
        "similitud_compacta_min": 0.0,
        "idf_veto_min_sin_identificador": 0.0,
        "idf_weight_blend": 0.0,
        "idf_weight_blend_sin_identificador": 0.0,
        # v0.17.4 — el bloqueo por NIT se abre al mismo radio que el
        # scorer está dispuesto a aceptar: con tolerancia 2 y vecindad 1,
        # los pares a dos dígitos de distancia nunca llegaban a scorearse.
        "nit_blocking_radio": 2,
        # Perilla experimental medida NEUTRA en Ground_Truth_Robusto_V3
        # (F1 0,8361 → 0,8347): dejarla apagada salvo OCR de escáner real.
        "ocr_confusables_en_firma": False,
        "liberar_fuentes_tras_l1": False,
        "remove_top_words": 0,
        "lsh_permutations": 252,
        "lsh_threshold": 0.45,
        "lsh_ngram": 3,
        # 0.50 medido idéntico a 0.45 sobre el ground truth (mismos 15.889
        # pares válidos) y recorta la franja de score donde viven pares
        # nombre-mediano con identificador inválido cercano en datos reales.
        "score_threshold": 0.50,
        "min_name_similarity": 0.30,
        "max_nit_distance": 3,
        "weights": {"name": 0.5, "nit": 0.5, "phonetic": 0.0},
        "batch_size": 100_000,
        "lsh_batch_size": 50_000,
        "lsh_chunk_size": 150_000,
        "scoring_batch_size": 75_000,
        "clustering_batch_size": 100_000,
        "golden_chunk_size": 100_000,
        "max_memory_gb": 8.5,
        "cross_source_only": False,
        "trusted_unique_sources": [],
        "use_disk_cache": True,
        "force_disk_results": True,
        "use_strict_clusters": True,
    },
    "fuentes_mixtas": {
        "description": (
            "Fuentes donde SOLO ALGUNOS registros traen identificador "
            "(v0.18.0, calibrado y validado fuera de muestra)"
        ),
        # Para qué sirve
        # --------------
        # Es el caso más común fuera de un registro mercantil: un padrón de
        # damnificados, una matrícula escolar o un censo de pacientes donde
        # una parte de los registros trae documento y otra no. El régimen con
        # identificador ya se resuelve solo; todo el error se concentra en el
        # que no lo trae.
        #
        # Evidencia (data/ground_truth/ground_truth_grande.csv · 12.427
        # registros · 22.073 pares verdaderos · 81 % con identificador):
        #
        #   métrica            produccion_estandar   fuentes_mixtas
        #   precision                    0,9760          0,9866
        #   recall                       0,9560          0,9733
        #   F1                           0,9659          0,9799
        #   B³ F1                        0,9797          0,9868
        #   recall SIN identificador     0,8344          0,9165
        #   tiempo                       25,6 s          39,8 s
        #
        # Validación fuera de muestra (3 pliegues por grupo): ΔF1 medio
        # +0,0105 (min +0,0077, max +0,0133) contra +0,0140 en el conjunto
        # donde se calibró. La diferencia de 0,0035 dice que la ganancia no
        # es sobreajuste.
        #
        # Lo que cuesta: 55 % más de tiempo, porque el bloqueo más abierto
        # genera 1,4 M de candidatos en vez de 850 K.
        "cleaning_mode": "BALANCEADO",
        "use_categorical_dtypes": False,
        "veto_nit_base_distinto": True,
        "cannot_link_identificador": True,
        # F2.1: cobertura por estrellas en grupos sin identificador válido. Se
        # declara apagada: solo `produccion_estandar` está medido (ADR-0011).
        # Para activarla: {"activa": True, "umbral": 0.80, "regla_lider": "cobertura"}.
        "cobertura_sin_identificador": {
            "activa": False,
            "umbral": 0.80,
            "regla_lider": "cobertura",
        },
        "tolerancia_digitacion_identificador": 0,
        "dv_es_mismo_identificador": True,
        "llaves_bloqueo": (),
        "tokens_bloqueo": (),
        "similitud_nombre_rescate_identificador": 0.90,
        # Las tres perillas que hacen la diferencia, en orden de aporte:
        #   1. lsh_threshold 0,55 → 0,45. El diagnóstico mostró que el 72,5 %
        #      de los pares verdaderos perdidos NUNCA llegaban a ser
        #      candidatos: el cuello estaba en el bloqueo, no en el score.
        #   2. similitud_compacta_min 0,96. Recupera los pares que un espacio
        #      mal puesto rompe para el comparador por tokens
        #      ("CHOIMIN GLOBAL" vs "CHOIMING LOBAL").
        #   3. idf_weight_blend_sin_identificador 0,05. Compensa la precisión
        #      que cuesta abrir el bloqueo, ponderando cada token por lo que
        #      informa — y SOLO donde no hay identificador que decida.
        "lsh_threshold": 0.45,
        "similitud_compacta_min": 0.96,
        "idf_weight_blend_sin_identificador": 0.05,
        "idf_veto_min_sin_identificador": 0.0,
        "idf_weight_blend": 0.0,
        "ocr_confusables_en_firma": False,
        "liberar_fuentes_tras_l1": False,
        "remove_top_words": 0,
        "lsh_permutations": 252,
        "lsh_ngram": 3,
        "score_threshold": 0.45,
        "min_name_similarity": 0.30,
        "max_nit_distance": 3,
        "weights": {"name": 0.5, "nit": 0.5, "phonetic": 0.0},
        "batch_size": 100_000,
        "lsh_batch_size": 50_000,
        "lsh_chunk_size": 150_000,
        "scoring_batch_size": 75_000,
        "clustering_batch_size": 100_000,
        "golden_chunk_size": 100_000,
        "max_memory_gb": 8.5,
        "cross_source_only": False,
        "trusted_unique_sources": [],
        "use_disk_cache": True,
        "force_disk_results": True,
        "use_strict_clusters": True,
    },
    # ═══════════════════════════════════════════════════════════════════════════
    # PERFIL: PRODUCCIÓN EXHAUSTIVA (IT-9 - máxima detección)
    # ═══════════════════════════════════════════════════════════════════════════
    "produccion_exhaustiva": {
        "description": "Producción exhaustiva - Máxima detección (12-14 horas)",
        # Limpieza
        "cleaning_mode": "BALANCEADO",
        # v0.13.0 (T1 dian-comercio): category en columnas de baja cardinalidad.
        # OPT-IN: el default False no cambia comportamiento.
        "use_categorical_dtypes": False,
        # v0.14.0 — salvaguardas de precisión. Apagarlas exige una razón:
        # medido sobre RUES x Exportaciones, sin ellas 226 grupos mezclaban
        # 2-3 NIT válidos distintos (empresas distintas fusionadas).
        "veto_nit_base_distinto": True,
        "cannot_link_identificador": True,
        # F2.1: cobertura por estrellas en grupos sin identificador válido. Se
        # declara apagada: solo `produccion_estandar` está medido (ADR-0011).
        # Para activarla: {"activa": True, "umbral": 0.80, "regla_lider": "cobertura"}.
        "cobertura_sin_identificador": {
            "activa": False,
            "umbral": 0.80,
            "regla_lider": "cobertura",
        },
        "tolerancia_digitacion_identificador": 0,
        "dv_es_mismo_identificador": True,
        "llaves_bloqueo": (),
        "tokens_bloqueo": (),
        "similitud_nombre_rescate_identificador": 0.90,
        # ── Ponderación IDF del nombre (v0.18.0) ─────────────────────────
        # 0.0 en ambas = comportamiento idéntico al de 0.17.4.
        # `..._sin_identificador` pondera los tokens por informatividad SOLO
        # en los pares donde ningún lado tiene identificador utilizable, que
        # es donde el nombre es toda la evidencia. Medido sobre
        # ground_truth_grande.csv, ahí vive el 100 % de los falsos positivos.
        # Piso de similitud de cadena compactada (v0.18.0). Recupera pares
        # que un error de espaciado rompe para el comparador por tokens.
        # 0.0 = desactivada (comportamiento idéntico al de 0.17.4).
        "similitud_compacta_min": 0.0,
        "idf_veto_min_sin_identificador": 0.0,
        "idf_weight_blend": 0.0,
        "idf_weight_blend_sin_identificador": 0.0,
        "liberar_fuentes_tras_l1": False,
        "remove_top_words": 0,
        # LSH - Más permisivo
        "lsh_permutations": 252,
        "lsh_threshold": 0.58,  # Más bajo = más candidatos
        "lsh_ngram": 3,
        # Scoring - Más permisivo
        "score_threshold": 0.40,
        "min_name_similarity": 0.25,
        "max_nit_distance": 4,
        # Pesos
        "weights": {"name": 0.5, "nit": 0.5, "phonetic": 0.0},
        # Recursos
        "batch_size": 100_000,
        "lsh_batch_size": 50_000,
        "lsh_chunk_size": 150_000,
        "scoring_batch_size": 75_000,
        "clustering_batch_size": 100_000,
        "golden_chunk_size": 100_000,
        # Memoria
        "max_memory_gb": 8.5,
        # v3.2.5 (FASE 2): eliminada clave `aggressive_gc` (dead code).
        # Opciones
        "cross_source_only": False,  # ⚠️ DEPRECADO: usar trusted_unique_sources
        "trusted_unique_sources": [],  # ✅ Paso 1.6
        "use_disk_cache": True,
        "force_disk_results": True,
        "use_strict_clusters": True,  # ← NUEVO: Paso 1.1
    },
    # ═══════════════════════════════════════════════════════════════════════════
    # PERFIL: ALTA PRECISIÓN (calibrado v3.2.5 — basado en evidencia GT)
    # ═══════════════════════════════════════════════════════════════════════════
    "alta_precision": {
        "description": (
            "Alta precisión - Minimiza falsos positivos. v3.2.5: ajustado "
            "siguiendo evidencia de produccion_calibrada (F1=0.84 sobre GT)."
        ),
        # Limpieza
        "cleaning_mode": "AGRESIVO",
        # v0.13.0 (T1 dian-comercio): category en columnas de baja cardinalidad.
        # OPT-IN: el default False no cambia comportamiento.
        "use_categorical_dtypes": False,
        # v0.14.0 — salvaguardas de precisión. Apagarlas exige una razón:
        # medido sobre RUES x Exportaciones, sin ellas 226 grupos mezclaban
        # 2-3 NIT válidos distintos (empresas distintas fusionadas).
        "veto_nit_base_distinto": True,
        "cannot_link_identificador": True,
        # F2.1: cobertura por estrellas en grupos sin identificador válido. Se
        # declara apagada: solo `produccion_estandar` está medido (ADR-0011).
        # Para activarla: {"activa": True, "umbral": 0.80, "regla_lider": "cobertura"}.
        "cobertura_sin_identificador": {
            "activa": False,
            "umbral": 0.80,
            "regla_lider": "cobertura",
        },
        "tolerancia_digitacion_identificador": 0,
        "dv_es_mismo_identificador": True,
        "llaves_bloqueo": (),
        "tokens_bloqueo": (),
        "similitud_nombre_rescate_identificador": 0.90,
        # ── Ponderación IDF del nombre (v0.18.0) ─────────────────────────
        # 0.0 en ambas = comportamiento idéntico al de 0.17.4.
        # `..._sin_identificador` pondera los tokens por informatividad SOLO
        # en los pares donde ningún lado tiene identificador utilizable, que
        # es donde el nombre es toda la evidencia. Medido sobre
        # ground_truth_grande.csv, ahí vive el 100 % de los falsos positivos.
        # Piso de similitud de cadena compactada (v0.18.0). Recupera pares
        # que un error de espaciado rompe para el comparador por tokens.
        # 0.0 = desactivada (comportamiento idéntico al de 0.17.4).
        "similitud_compacta_min": 0.0,
        "idf_veto_min_sin_identificador": 0.0,
        "idf_weight_blend": 0.0,
        "idf_weight_blend_sin_identificador": 0.0,
        "liberar_fuentes_tras_l1": False,
        "remove_top_words": 5,
        # LSH - Restrictivo
        "lsh_permutations": 252,
        "lsh_threshold": 0.65,
        "lsh_ngram": 3,
        # Scoring - Estricto (v3.2.5: alineados con produccion_calibrada)
        "score_threshold": 0.60,  # ← v3.2.5: subido desde 0.55
        "min_name_similarity": 0.65,  # ← v3.2.5: subido desde 0.45
        "max_nit_distance": 0,  # ← v3.2.5: bajado desde 2 (NIT idéntico)
        "nit_empty_passes_filter": False,  # ← v3.2.5: fix bug NIT vacío
        # Pesos
        "weights": {
            "name": 0.50,
            "nit": 0.50,
            "phonetic": 0.0,
        },
        # Recursos
        "batch_size": 100_000,
        "lsh_batch_size": 50_000,
        # v3.2.5 (FASE 2): eliminada clave `aggressive_gc` (dead code).
        # Opciones
        "cross_source_only": False,
        "trusted_unique_sources": [],
        "use_disk_cache": True,
        "force_disk_results": True,
        "use_strict_clusters": True,
    },
    # ═══════════════════════════════════════════════════════════════════════════
    # PERFIL: DEDUPLICACIÓN SIMPLE (una sola fuente)
    # ═══════════════════════════════════════════════════════════════════════════
    "deduplicacion_simple": {
        "description": "Deduplicación de una sola fuente",
        # Limpieza
        "cleaning_mode": "BALANCEADO",
        # v0.13.0 (T1 dian-comercio): category en columnas de baja cardinalidad.
        # OPT-IN: el default False no cambia comportamiento.
        "use_categorical_dtypes": False,
        # v0.14.0 — salvaguardas de precisión. Apagarlas exige una razón:
        # medido sobre RUES x Exportaciones, sin ellas 226 grupos mezclaban
        # 2-3 NIT válidos distintos (empresas distintas fusionadas).
        "veto_nit_base_distinto": True,
        "cannot_link_identificador": True,
        # F2.1: cobertura por estrellas en grupos sin identificador válido. Se
        # declara apagada: solo `produccion_estandar` está medido (ADR-0011).
        # Para activarla: {"activa": True, "umbral": 0.80, "regla_lider": "cobertura"}.
        "cobertura_sin_identificador": {
            "activa": False,
            "umbral": 0.80,
            "regla_lider": "cobertura",
        },
        "tolerancia_digitacion_identificador": 0,
        "dv_es_mismo_identificador": True,
        "llaves_bloqueo": (),
        "tokens_bloqueo": (),
        "similitud_nombre_rescate_identificador": 0.90,
        # ── Ponderación IDF del nombre (v0.18.0) ─────────────────────────
        # 0.0 en ambas = comportamiento idéntico al de 0.17.4.
        # `..._sin_identificador` pondera los tokens por informatividad SOLO
        # en los pares donde ningún lado tiene identificador utilizable, que
        # es donde el nombre es toda la evidencia. Medido sobre
        # ground_truth_grande.csv, ahí vive el 100 % de los falsos positivos.
        # Piso de similitud de cadena compactada (v0.18.0). Recupera pares
        # que un error de espaciado rompe para el comparador por tokens.
        # 0.0 = desactivada (comportamiento idéntico al de 0.17.4).
        "similitud_compacta_min": 0.0,
        "idf_veto_min_sin_identificador": 0.0,
        "idf_weight_blend": 0.0,
        "idf_weight_blend_sin_identificador": 0.0,
        "liberar_fuentes_tras_l1": False,
        "remove_top_words": 0,
        # LSH
        "lsh_permutations": 128,
        "lsh_threshold": 0.50,
        "lsh_ngram": 3,
        # Scoring
        "score_threshold": 0.45,
        "min_name_similarity": 0.35,
        "max_nit_distance": 3,
        # Pesos
        "weights": {"name": 0.50, "nit": 0.50, "phonetic": 0.0},
        # Recursos (conservador)
        "batch_size": 50_000,
        "lsh_batch_size": 30_000,
        # Opciones
        "cross_source_only": False,  # ⚠️ DEPRECADO: usar trusted_unique_sources
        "trusted_unique_sources": [],  # ✅ Paso 1.6
        "use_disk_cache": True,
        "force_disk_results": True,
        "use_strict_clusters": True,  # ← NUEVO: Paso 1.1
    },
    # ═══════════════════════════════════════════════════════════════════════════
    #  PERFIL: PRODUCCIÓN CALIBRADA (v3.2.4 — FASE 1)
    # ═══════════════════════════════════════════════════════════════════════════
    #  Calibrado contra ground_truth_grande.csv (12,427 registros, 5 fuentes,
    #  3,486 grupos verdad). Métricas medidas (run determinista):
    #     F1        = 0.8424
    #     Precision = 1.0000
    #     Recall    = 0.7277
    #     TP=16,062  FP=0  FN=6,011
    #  Comparativa contra el config IT-7 default (score_threshold=0.40):
    #     IT-7:        F1=0.05  P=0.03  R=0.91  ← sobre-fusión catastrófica
    #     CALIBRADA:   F1=0.84  P=1.00  R=0.73  ← precision perfecta
    #  Cambios clave vs IT-7:
    #     - score_threshold:     0.40 → 0.60  (umbral final, el más decisivo)
    #     - min_name_similarity: 0.25 → 0.65  (filtro previo más estricto)
    #     - max_nit_distance:    2 → 0        (NITs deben ser idénticos)
    #     - nit_empty_passes_filter: False    (NIT vacío NO pasa filtro)
    #  Si necesitas recall mayor a costa de precision, usa score_threshold=0.50.
    # ═══════════════════════════════════════════════════════════════════════════
    "produccion_calibrada": {
        "description": (
            "Producción calibrada contra ground_truth_grande.csv (F1=0.84, "
            "P=1.00, R=0.73). v3.2.4 — Fase 1 de auditoría."
        ),
        # Limpieza
        "cleaning_mode": "AGRESIVO",
        # v0.13.0 (T1 dian-comercio): category en columnas de baja cardinalidad.
        # OPT-IN: el default False no cambia comportamiento.
        "use_categorical_dtypes": False,
        # v0.14.0 — salvaguardas de precisión. Apagarlas exige una razón:
        # medido sobre RUES x Exportaciones, sin ellas 226 grupos mezclaban
        # 2-3 NIT válidos distintos (empresas distintas fusionadas).
        "veto_nit_base_distinto": True,
        "cannot_link_identificador": True,
        # F2.1: cobertura por estrellas en grupos sin identificador válido. Se
        # declara apagada: solo `produccion_estandar` está medido (ADR-0011).
        # Para activarla: {"activa": True, "umbral": 0.80, "regla_lider": "cobertura"}.
        "cobertura_sin_identificador": {
            "activa": False,
            "umbral": 0.80,
            "regla_lider": "cobertura",
        },
        "tolerancia_digitacion_identificador": 0,
        "dv_es_mismo_identificador": True,
        "llaves_bloqueo": (),
        "tokens_bloqueo": (),
        "similitud_nombre_rescate_identificador": 0.90,
        # ── Ponderación IDF del nombre (v0.18.0) ─────────────────────────
        # 0.0 en ambas = comportamiento idéntico al de 0.17.4.
        # `..._sin_identificador` pondera los tokens por informatividad SOLO
        # en los pares donde ningún lado tiene identificador utilizable, que
        # es donde el nombre es toda la evidencia. Medido sobre
        # ground_truth_grande.csv, ahí vive el 100 % de los falsos positivos.
        # Piso de similitud de cadena compactada (v0.18.0). Recupera pares
        # que un error de espaciado rompe para el comparador por tokens.
        # 0.0 = desactivada (comportamiento idéntico al de 0.17.4).
        "similitud_compacta_min": 0.0,
        "idf_veto_min_sin_identificador": 0.0,
        "idf_weight_blend": 0.0,
        "idf_weight_blend_sin_identificador": 0.0,
        "liberar_fuentes_tras_l1": False,
        "remove_top_words": 35,
        # LSH
        "lsh_permutations": 252,
        "lsh_threshold": 0.58,
        "lsh_ngram": 2,
        # Scoring — CALIBRADOS
        "score_threshold": 0.60,  # ← clave: subido desde 0.40
        "min_name_similarity": 0.65,  # ← clave: subido desde 0.25
        "max_nit_distance": 0,  # ← clave: NIT debe ser idéntico
        "nit_empty_passes_filter": False,  # ← v3.2.4: NIT vacío no pasa
        # Pesos
        "weights": {"name": 0.50, "nit": 0.50, "phonetic": 0.00},
        # Trusted sources
        "trusted_unique_sources": ["RUES", "SUPERSOCIEDADES"],
        "cross_source_only": False,
        # v0.7.1 (Sprint 0.8.1, Tarea 1.3): skip_reporting configurable desde el perfil.
        # Si True, omite la fase L6_REPORTING (ahorra ~4 min en pipeline de 1.97M).
        # Útil para producción cuando los reportes no se consumen.
        # Override: pasar skip_reporting=True/False explícito a Orchestrator.run().
        "skip_reporting": False,
        # Memoria
        "force_disk_results": True,
        "use_disk_cache": True,
        "max_memory_gb": 11.0,
        # Batches
        "batch_size": 200_000,
        "lsh_batch_size": 75_000,
        "lsh_chunk_size": 200_000,
        "scoring_batch_size": 100_000,
        "clustering_batch_size": 200_000,
        "golden_chunk_size": 150_000,
        "sqlite_batch_size": 100_000,
        # Source quality (solo se usa el ORDEN — ver PARTIAL_CONFIG_KEYS)
        "source_quality_weights": {
            "RUES": 0.99,
            "SUPERSOCIEDADES": 0.90,
            "DIAN": 0.85,
            "EXPORTACIONES": 0.80,
            "IMPORTACIONES": 0.70,
            "CRM": 0.60,
        },
        "use_strict_clusters": True,
    },
}


# NOTA: en el notebook fuente (celda 260), `output_directory` y `lsh_storage_dir`
# referenciaban globales `WORKSPACE` y `NOMBRE_EJECUCION` definidas en runtime.
# En el paquete .py se exponen como placeholders ("$WORKSPACE", "$NOMBRE_EJECUCION")
# que el `Orchestrator` debe sustituir o el script orquestador debe sobrescribir
# antes de ejecutar. Esto preserva la estructura del config sin romper imports.
config_produccion_it7 = {
    # ═════════════════════════════════════════════════════════════════════
    # v0.5.0 (Sprint 0.5.0): config IT-7 LIMPIADA.
    # Eliminadas 11 claves verificadas como dead/deprecated en auditoría Fase 1-4:
    #   DEAD removidas:
    #     - confidence_weights, max_sources_per_group, min_sources_for_golden,
    #       aggressive_gc, memory_monitor_interval, sqlite_cache_size,
    #       commit_interval, correlative_chunk_size, validation_rules,
    #       performance_settings
    #   DEPRECATED removida:
    #     - cross_source_validation  (usar cross_source_only)
    # NOTA: max_sources_per_group y min_sources_for_golden YA están implementadas
    # en v0.3.1/v0.4.0 pero NO se incluyen aquí porque IT-7 original no las usaba
    # con valores significativos (4 y 1 respectivamente eran defaults inertes).
    # Si quieres activarlas: usa `produccion_calibrada` u override explícito.
    # Antes del cambio: 11 claves no leídas. Ahora: 0.
    # Tests F1 contra GT: idéntico antes/después (F1=0.05, sin cambios funcionales).
    # ═════════════════════════════════════════════════════════════════════
    "profile": "enterprise_scale_4_sources",
    "output_directory": "$WORKSPACE",  # ← sobrescribir antes de ejecutar
    "linkage_engine_class": "disk_based",
    "lsh_storage_dir": "lsh_$NOMBRE_EJECUCION",  # ← sobrescribir antes de ejecutar
    "cleaning_mode": "AGRESIVO",
    # v0.13.0 (T1 dian-comercio): category en columnas de baja cardinalidad.
    # OPT-IN: el default False no cambia comportamiento.
    "use_categorical_dtypes": False,
    # v0.14.0 — salvaguardas de precisión. Apagarlas exige una razón:
    # medido sobre RUES x Exportaciones, sin ellas 226 grupos mezclaban
    # 2-3 NIT válidos distintos (empresas distintas fusionadas).
    "veto_nit_base_distinto": True,
    "cannot_link_identificador": True,
    "liberar_fuentes_tras_l1": False,
    "profiles": {
        "enterprise_scale_4_sources": {
            "description": "IT-7 + Trusted Sources (RUES, SUPERSOCIEDADES)",
            # ─── PARÁMETROS LSH ──────────────────────────────────────────
            "lsh_permutations": 252,
            "lsh_threshold": 0.58,
            "lsh_ngram": 2,
            # ─── TRUSTED SOURCES (Paso 1.6 del notebook IT-7) ───────────
            # RUES y SUPERSOCIEDADES: fuentes maestras, únicas por definición.
            # NO se comparan internamente → elimina ~95% de candidatos.
            # SÍ se cruzan con CRM y EXPORTACIONES normalmente.
            "cross_source_only": False,
            "trusted_unique_sources": ["RUES", "SUPERSOCIEDADES"],
            # ─── MEMORIA Y VELOCIDAD ─────────────────────────────────────
            "force_disk_results": True,
            "memory_threshold_candidates": 1_000_000,
            # ─── TAMAÑOS DE BATCH ────────────────────────────────────────
            "batch_size": 200_000,
            "lsh_batch_size": 75_000,
            "lsh_chunk_size": 200_000,
            "scoring_batch_size": 100_000,
            "clustering_batch_size": 200_000,
            "golden_chunk_size": 150_000,
            # ─── SCORING ─────────────────────────────────────────────────
            "score_threshold": 0.40,
            "min_name_similarity": 0.25,
            "max_nit_distance": 2,
            # ─── SISTEMA ─────────────────────────────────────────────────
            "max_memory_gb": 11.0,
            "use_disk_cache": True,
            "sqlite_batch_size": 100_000,
            # ─── PESOS (Paso 1.4 del notebook IT-7: fonético=0) ─────────
            "weights": {"name": 0.50, "nit": 0.50, "phonetic": 0.00},
            "remove_top_words": 35,
            # ─── source_quality_weights ─────────────────────────────────
            # En v0.3.1+ los valores numéricos se usan para desempate.
            # En IT-7 original solo el orden de las claves importaba.
            "source_quality_weights": {
                "RUES": 0.99,
                "SUPERSOCIEDADES": 0.90,
                "EXPORTACIONES": 0.80,
                "CRM": 0.60,
            },
        }
    },
    "min_nit_length": 1,
    "max_nit_length": 20,
    "remove_test_data": False,
    "remove_invalid_nits": False,
    "export_settings": {
        "csv_compression": "gzip",
        "parquet_compression": "snappy",
        "include_diagnostics": True,
        "export_chunk_size": 50_000,
        "split_large_exports": False,
        "max_file_size_mb": 500,
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# REGISTRO ÚNICO DE PERFILES (F1.2, v0.9.0)
# ═══════════════════════════════════════════════════════════════════════════
# Antes vivían en pipeline/_internal.py (PROFILES y DEDUPLICATION_PROFILES),
# separados de este módulo de configuración: la doble contabilidad fue
# coprotagonista del diagnóstico 0.7.6. Desde v0.9.0 este módulo es la ÚNICA
# fuente de verdad; _internal reexporta los MISMOS objetos por
# retrocompatibilidad (código y scripts que los mutan siguen funcionando).
#
# Familias (no mezclar):
#   - PERFILES_MOTOR         → parámetros del motor LSH/scoring (engine/).
#   - PERFILES_DEDUPLICACION → perfiles de deduplicate_unified/auto.
#   - PERFILES_BASE (arriba) → plantillas de configuración del Orchestrator
#                              (otro nivel: configs completas, no parámetros).

PERFILES_MOTOR = {
    "standard": {
        "description": "Perfil estándar para datasets medianos (<500k registros)",
        "lsh_permutations": 128,
        "lsh_threshold": 0.75,
        "lsh_ngram": 3,
        "batch_size": 30_000,
        "score_threshold": 0.82,
        "max_nit_distance": 2,
        "min_name_similarity": 0.65,
        "remove_top_words": 20,
        "weights": {"name": 0.65, "nit": 0.35, "phonetic": 0.0},
        "confidence_weights": {
            "source_priority": 0.4,
            "nit_consistency": 0.3,
            "name_consistency": 0.2,
            "source_count": 0.1,
        },
        "golden_chunk_size": 50_000,
        "correlative_chunk_size": 50_000,
        "trusted_unique_sources": [],  # ✅ Paso 1.6
    },
    "large_dataset_fast": {
        "description": "Perfil optimizado para datasets grandes (>3M registros)",
        "lsh_permutations": 128,
        "lsh_threshold": 0.65,
        "lsh_ngram": 3,
        "batch_size": 50_000,
        "lsh_batch_size": 10_000,
        "score_threshold": 0.7,
        "max_nit_distance": 2,
        "min_name_similarity": 0.70,
        "remove_top_words": 25,
        "weights": {"name": 0.70, "nit": 0.30, "phonetic": 0.0},
        "confidence_weights": {
            "source_priority": 0.4,
            "nit_consistency": 0.3,
            "name_consistency": 0.2,
            "source_count": 0.1,
        },
        "golden_chunk_size": 200_000,
        "correlative_chunk_size": 100_000,
        "use_disk_cache": True,
        "aggressive_gc": True,
        "trusted_unique_sources": [],  # ✅ Paso 1.6
    },
    "large_scale": {
        "description": "Perfil optimizado para datasets grandes (>3M registros)",
        "lsh_permutations": 96,
        "lsh_threshold": 0.82,
        "lsh_ngram": 3,
        "batch_size": 50_000,
        "lsh_batch_size": 10_000,
        "score_threshold": 0.85,
        "max_nit_distance": 2,
        "min_name_similarity": 0.70,
        "remove_top_words": 25,
        "weights": {"name": 0.70, "nit": 0.30, "phonetic": 0.00},
        "confidence_weights": {
            "source_priority": 0.4,
            "nit_consistency": 0.3,
            "name_consistency": 0.2,
            "source_count": 0.1,
        },
        "golden_chunk_size": 100_000,
        "correlative_chunk_size": 100_000,
        "use_disk_cache": True,
        "aggressive_gc": True,
        "trusted_unique_sources": [],  # ✅ Paso 1.6
    },
    "high_precision": {
        "description": "Perfil para máxima precisión (menos falsos positivos)",
        "lsh_permutations": 128,
        "lsh_threshold": 0.85,
        "lsh_ngram": 4,
        "batch_size": 20_000,
        "score_threshold": 0.90,
        "max_nit_distance": 1,
        "min_name_similarity": 0.80,
        "remove_top_words": 30,
        "weights": {"name": 0.60, "nit": 0.40, "phonetic": 0.00},
        "confidence_weights": {
            "source_priority": 0.5,
            "nit_consistency": 0.3,
            "name_consistency": 0.15,
            "source_count": 0.05,
        },
        "golden_chunk_size": 30_000,
        "correlative_chunk_size": 30_000,
        "trusted_unique_sources": [],  # ✅ Paso 1.6
    },
    "high_recall": {
        "description": "Perfil para máximo recall (encontrar más matches)",
        "lsh_permutations": 128,
        "lsh_threshold": 0.65,
        "lsh_ngram": 2,
        "batch_size": 40_000,
        "score_threshold": 0.70,
        "max_nit_distance": 3,
        "min_name_similarity": 0.55,
        "remove_top_words": 15,
        "weights": {"name": 0.70, "nit": 0.30, "phonetic": 0.00},
        "confidence_weights": {
            "source_priority": 0.3,
            "nit_consistency": 0.3,
            "name_consistency": 0.3,
            "source_count": 0.1,
        },
        "golden_chunk_size": 50_000,
        "correlative_chunk_size": 50_000,
        "trusted_unique_sources": [],  # ✅ Paso 1.6
    },
    "memory_constrained": {
        "description": "Perfil para entornos con memoria limitada (Google Colab)",
        "lsh_permutations": 64,
        "lsh_threshold": 0.80,
        "lsh_ngram": 3,
        "batch_size": 10_000,
        "lsh_batch_size": 5_000,
        "score_threshold": 0.85,
        "max_nit_distance": 2,
        "min_name_similarity": 0.70,
        "remove_top_words": 20,
        "weights": {"name": 0.70, "nit": 0.30, "phonetic": 0.00},
        "confidence_weights": {
            "source_priority": 0.4,
            "nit_consistency": 0.3,
            "name_consistency": 0.2,
            "source_count": 0.1,
        },
        "golden_chunk_size": 20_000,
        "correlative_chunk_size": 20_000,
        "use_disk_cache": True,
        "aggressive_gc": True,
        "max_memory_gb": 8.0,
        "trusted_unique_sources": [],  # ✅ Paso 1.6
    },
}

PERFILES_DEDUPLICACION = {
    "deduplication_standard": {
        "description": "Deduplicación estándar para datasets medianos",
        "lsh_permutations": 128,
        # v2.2.0 — recalibrado contra ground truth (test_data_y_tabla_verdad,
        # 269 registros / 84 grupos). Ver tests/test_quality_golden.py y
        # MIGRATION_LOG §11. Resultados medidos del cambio:
        #   F1     0.406 → 0.635   (+56 %)
        #   recall 0.267 → 0.510   (casi 2×)
        #   prec.  0.848 → 0.839   (sin pérdida material)
        # Diagnóstico: el bloqueo LSH a 0.75 descartaba pares con typos antes
        # de compararlos (causa raíz del bajo recall); el NIT pesaba 0.45 pese
        # a ser poco fiable (dígitos errados); la fonética estaba apagada.
        "lsh_threshold": 0.30,  # antes 0.75 — bloqueo más permisivo
        "lsh_ngram": 3,
        "batch_size": 30_000,
        "score_threshold": 0.68,  # antes 0.85
        "max_nit_distance": 3,
        "min_name_similarity": 0.60,  # antes 0.65
        "remove_top_words": 20,
        # antes name:0.55 nit:0.45 phonetic:0.0
        "weights": {"name": 0.65, "nit": 0.20, "phonetic": 0.15},
        # v2.8.0 (P0-1): tratamiento privilegiado de NIT idéntico.
        # Ver MIGRATION_LOG §18 para diagnóstico completo.
        "nit_identical_overrides_name_filter": True,
        "nit_identical_score_boost": 0.05,
        # v2.10.0 (Fix #1): boost diferenciado para NIT con DV declarado.
        # Cuando AMBOS lados del par vienen con DV declarado en origen
        # (longitud original >= 10 dígitos o formato 'XXXXXXXXX-D'), la
        # evidencia de identidad es FUERTE — dos fuentes independientes
        # acordaron en el mismo NIT+DV que probablemente no se inventaron.
        # Boost 0.10 (vs 0.05 de computed) recupera FN de tipo P4 (token
        # disímil con NIT idéntico). Calibración contra dataset sintético;
        # recalibrar con producción real. Boost > 0.10 regresa golden 269.
        "nit_identical_score_boost_declared": 0.10,
        # v2.10.0 (Fix #2): penalización por nombre genérico (OPT-IN).
        # Penaliza name_sim cuando ambos nombres son cortos (≤3 tokens) y
        # están compuestos SOLO por tokens del set genérico (curado +
        # top-N del corpus). Resuelve el FP de "INVERSIONES SAS × 3" en
        # el dataset sintético robusto, PERO produce regresión en golden
        # 269 (F1 0.759→0.647) porque la lista curada de stop-words
        # empresariales incluye tokens (COMPAÑIA, COLOMBIA, EMPRESA, etc.)
        # que aparecen en nombres de empresas reales pequeñas.
        #
        # Default 0.0 = OFF. Activar SOLO tras calibrar con producción
        # real: si la muestra tiene 50k+ regs, los top-N del corpus
        # tomarán solo palabras genuinamente frecuentes y la regla será
        # más segura. Recomendado entonces:
        #     "generic_name_penalty": 0.5,
        #     "generic_name_top_n": 50,
        #     "generic_name_max_tokens": 3,
        #     "generic_name_min_sim": 0.85,
        "generic_name_penalty": 0.0,
        "generic_name_top_n": 30,
        "generic_name_max_tokens": 3,
        "generic_name_min_sim": 0.85,
        "use_strict_clusters": True,
        "cross_source_only": False,  # Clave para deduplicación
    },
    "deduplication_colab_3M": {
        "description": "Optimizado para RUES 3M+ registros en Colab",
        "lsh_permutations": 128,  # 96
        "lsh_threshold": 0.7,  # 0.82
        "lsh_ngram": 3,
        "batch_size": 15_000,
        "score_threshold": 0.88,
        "max_nit_distance": 2,
        "min_name_similarity": 0.70,
        "remove_top_words": 25,
        "weights": {"name": 0.70, "nit": 0.30, "phonetic": 0.0},
        "use_disk_cache": True,
        "aggressive_gc": True,
        "streaming_mode": True,
        "max_memory_gb": 10.5,
        "use_strict_clusters": True,
        "cross_source_only": False,
    },
    "deduplication_colab_1M": {
        "description": "Optimizado para DANE 1M registros en Colab",
        "lsh_permutations": 128,
        "lsh_threshold": 0.78,
        "lsh_ngram": 3,
        "batch_size": 25_000,
        "score_threshold": 0.85,
        "max_nit_distance": 3,
        "min_name_similarity": 0.65,
        "remove_top_words": 20,
        "weights": {"name": 0.65, "nit": 0.30, "phonetic": 0.05},
        "use_disk_cache": True,
        "aggressive_gc": False,
        "max_memory_gb": 10.0,
        "use_strict_clusters": True,
        "cross_source_only": False,
    },
    "deduplication_sin_nit_conservador": {
        # v2.11.0 — Perfil para fuentes SIN NIT (solo Razón Social [+ Ciudad]).
        # Caso real: importaciones (Corea del Sur), donde no hay NIT y el
        # único discriminante es el nombre + ciudad. En ese régimen el perfil
        # estándar SOBRE-FUSIONA: une empresas distintas que comparten un
        # token y la ciudad (p. ej. WORLD FLORA + DAEDONG GARDENING en SEOUL,
        # o tres compañías KOREA *POWER* CO LTD distintas).
        #
        # Calibración (barrido sobre 818 registros reales de Corea, v2.11.0):
        #   estándar (0.68/0.60/0.30) -> 324 grupos, grupo monstruo n=82,
        #     fusiona mal WORLD FLORA + DAEDONG.
        #   este perfil (0.80/0.75/0.50/ciudad 0.40) -> 450 grupos, max n=33
        #     (NENOVA, agrupación CORRECTA), separa WORLD FLORA de DAEDONG.
        # Prioriza PRECISIÓN sobre recall: preferimos NO fusionar dudosos.
        #
        # v0.7.4 — RECALIBRADO CONTRA GROUND TRUTH. Sobre los 2342 registros
        # SIN_NIT de ground_truth_grande.csv (604 grupos), barrido de
        # min_name_similarity con score_threshold acoplado:
        #   nsim=0.75 -> P=0.944 R=0.313 F1=0.470 (demasiado estricto: pierde typos)
        #   nsim=0.78 -> P=0.907 R=0.501 F1=0.645 ← ÓPTIMO operativo
        #   nsim=0.80 -> P=0.944 R=0.313 F1=0.470
        # El baseline previo (deduplicate_unified estándar) daba F1=0.217 por
        # sobre-fusión. Este perfil casi TRIPLICA el F1 (0.645) manteniendo
        # precision >0.90: cuando agrupa, acierta 9 de cada 10. El recall 0.50
        # refleja un límite de DATOS (typos OCR + romanización coreana
        # inconsistente), no de calibración: ningún umbral supera F1~0.65 sin
        # destruir precision. Ver docs/DEUDA_SIN_NIT.md para el análisis.
        "description": "Conservador para fuentes sin NIT (recalibrado vs GT v0.7.4: P=0.91 F1=0.65)",
        "lsh_permutations": 128,
        "lsh_threshold": 0.50,  # más estricto que 0.30 — menos candidatos ruidosos
        "lsh_ngram": 3,
        "batch_size": 30_000,
        "score_threshold": 0.78,  # v0.7.4: acoplado a min_name_similarity óptimo
        "max_nit_distance": 3,
        "min_name_similarity": 0.78,  # v0.7.4: óptimo F1 medido (era 0.75)
        "remove_top_words": 20,
        # Sin NIT, el peso del NIT (0.20) se reparte: nombre domina, ciudad
        # entra vía extra_features (signed) para penalizar ciudades distintas.
        "weights": {"name": 0.65, "nit": 0.20, "phonetic": 0.15},
        # El override por NIT idéntico no aplica sin NIT, pero lo dejamos OFF
        # explícitamente para que el perfil sea autoexplicativo.
        "nit_identical_overrides_name_filter": False,
        "nit_identical_score_boost": 0.0,
        "nit_identical_score_boost_declared": 0.0,
        "generic_name_penalty": 0.0,
        "generic_name_top_n": 30,
        "generic_name_max_tokens": 3,
        "generic_name_min_sim": 0.85,
        # v2.12.0 (Fix #3): re-scoring por IDF de tokens. CLAVE para este
        # régimen. Sin IDF, el token_set_ratio agrupa por tokens frecuentes
        # ("ELITE EXPORTS INTERNATIONAL INC Y/O X" vs "...Y/O Z" = 93%, o
        # "SECUI CORPORATION" vs "MULTIFLORA CORPORATION" = 79% por
        # "CORPORATION"). Con blend=0.5 esos pares caen por debajo del
        # umbral y las empresas distintas dejan de fusionarse, mientras las
        # variantes reales (NENOVA, WORLD FLORA, ARES3...) se mantienen.
        #
        # Medido sobre Corea (818 sin NIT): SECUI/MULTIFLORA/CK/KSCORP pasan
        # de 1 grupo común a 4 grupos distintos; ELITE...Y/O X se separan en
        # singletons; NENOVA (n=28) y WORLD FLORA (n=18) se preservan.
        #
        # ⚠️ Este valor DAÑA los datasets con NIT (golden 269: F1 0.759→0.538)
        # porque penaliza la señal de nombre que el NIT debería complementar.
        # Por eso vive SOLO en este perfil, no en deduplication_standard.
        "idf_weight_blend": 0.5,
        "use_strict_clusters": True,
        "cross_source_only": False,
    },
}

#: Vista unificada motor+deduplicación (== ALL_PROFILES histórico).
REGISTRO_PERFILES = {**PERFILES_MOTOR, **PERFILES_DEDUPLICACION}


def get_profile(nombre: str) -> dict[str, Any]:
    """Devuelve un perfil del registro único, fallando rápido si no existe.

    Args:
        nombre: clave en ``REGISTRO_PERFILES`` (motor o deduplicación).

    Returns:
        El dict del perfil (el objeto REAL del registro, no una copia:
        mutarlo afecta al registro, igual que siempre; use ``.copy()``
        si necesita una variante local).

    Raises:
        KeyError: con la lista completa de perfiles disponibles.
    """
    try:
        return REGISTRO_PERFILES[nombre]
    except KeyError:
        disponibles = ", ".join(sorted(REGISTRO_PERFILES))
        raise KeyError(
            f"Perfil '{nombre}' no existe. Disponibles: {disponibles}. "
            f"(Las plantillas del Orchestrator van aparte: "
            f"{', '.join(sorted(PERFILES_BASE))} vía crear_config_orchestrator)."
        ) from None


#: Rangos sanos por clave numérica conocida (validación fail-fast al importar).
#: Los límites DOCUMENTAN la realidad validada; ampliarlos exige acta.
_RANGOS_PERFIL: dict[str, tuple[float, float]] = {
    "lsh_threshold": (0.05, 0.95),
    "min_name_similarity": (0.0, 1.0),
    "lsh_permutations": (16, 1024),
    "lsh_ngram": (1, 6),
    "batch_size": (1_000, 5_000_000),
    "max_nit_distance": (0, 10),
    "generic_name_min_sim": (0.0, 1.0),
    "generic_name_penalty": (0.0, 1.0),
    "generic_name_top_n": (0, 500),
    "generic_name_max_tokens": (1, 10),
    "nit_identical_score_boost": (0.0, 0.5),
}


def _validar_registro() -> None:
    """Valida rangos de TODOS los perfiles al importar (fail-fast)."""
    for nombre, perfil in REGISTRO_PERFILES.items():
        for clave, (lo, hi) in _RANGOS_PERFIL.items():
            if clave in perfil:
                v = perfil[clave]
                if not isinstance(v, (int, float)) or not (lo <= v <= hi):
                    raise ValueError(
                        f"Perfil '{nombre}': {clave}={v!r} fuera de rango "
                        f"[{lo}, {hi}]. Corrija el perfil o amplíe "
                        f"_RANGOS_PERFIL con acta."
                    )


_validar_registro()


def validar_config(config: dict[str, Any], verbose: bool = True) -> dict[str, list[str]]:
    """Audita un config dict y reporta claves dead/partial/deprecated (v3.2.7).

    Inspecciona top-level y todos los `profiles[<name>]` buscando claves que
    aparecen en las listas negras `DEAD_CONFIG_KEYS`, `PARTIAL_CONFIG_KEYS`
    o `DEPRECATED_CONFIG_KEYS`. NO modifica el config — solo reporta.

    Args:
        config: dict de configuración completo del Orchestrator.
        verbose: si True, imprime warnings.

    Returns:
        Dict con cuatro listas:
          - 'dead'       : claves que el código NO LEE (alarmante)
          - 'partial'    : claves con uso parcial/limitado
          - 'deprecated' : claves DEPRECADAS (v3.2.7); usar alternativas
          - 'unknown'    : claves que no están registradas como válidas
    """
    encontradas_dead: list[str] = []
    encontradas_partial: list[str] = []
    encontradas_deprecated: list[str] = []

    def _recurse(d, path=""):
        if not isinstance(d, dict):
            return
        for k, v in d.items():
            full_path = f"{path}.{k}" if path else k
            if k in DEPRECATED_CONFIG_KEYS:
                encontradas_deprecated.append(full_path)
            elif k in DEAD_CONFIG_KEYS:
                encontradas_dead.append(full_path)
            if k in PARTIAL_CONFIG_KEYS:
                encontradas_partial.append(full_path)
            if isinstance(v, dict):
                _recurse(v, full_path)

    _recurse(config)

    if verbose:
        if encontradas_deprecated:
            print("⚠️  CONFIG DEPRECATED (v3.2.7): el config contiene claves DEPRECADAS:")
            for k in sorted(set(encontradas_deprecated)):
                print(f"     ⚠️  {k}  (deprecated; usar alternativa documentada)")
        if encontradas_dead:
            print(
                "⚠️  CONFIG WARNING (v3.2.4+): el config contiene claves que el código "
                "NO LEE. Mantenerlas crea ilusión configuracional. Considera eliminarlas:"
            )
            for k in sorted(set(encontradas_dead)):
                print(f"     🪦 {k}  (dead code, sin efecto)")
        if encontradas_partial:
            print("ℹ️  CONFIG INFO (v3.2.4+): el config contiene claves con uso PARCIAL:")
            for k in sorted(set(encontradas_partial)):
                print(f"     ⚠️  {k}  (uso limitado; ver docstring de cada clave)")

    return {
        "dead": sorted(set(encontradas_dead)),
        "partial": sorted(set(encontradas_partial)),
        "deprecated": sorted(set(encontradas_deprecated)),
        "unknown": [],
    }


#: Peso y tipo por defecto para extra_features declaradas como string simple.
#: `categorical_signed` premia coincidencia y PENALIZA discrepancia (neutral
#: ante nulos): el default seguro para variables de contacto/geo.
_EXTRA_FEATURE_DEFAULT_WEIGHT: float = 0.05
_EXTRA_FEATURE_DEFAULT_TYPE: str = "categorical_signed"

#: Alias legados aceptados en overrides → clave real del perfil.
_ALIAS_OVERRIDES: dict[str, str] = {
    "trusted_sources": "trusted_unique_sources",
}


def _normalizar_extra_features(extra_features: list[Any] | None) -> list[dict[str, Any]]:
    """Normaliza extra_features a la forma que consume el VectorizedScorer.

    Acepta strings (nombre de columna → dict con peso/tipo por defecto) o
    dicts ya completos ``{"column", "weight", "type"}``. Fail-fast ante
    cualquier otra cosa (v0.12.0: antes esto se descartaba en silencio).
    """
    if not extra_features:
        return []
    normalizadas: list[dict[str, Any]] = []
    for i, feat in enumerate(extra_features):
        if isinstance(feat, str):
            normalizadas.append(
                {
                    "column": feat,
                    "weight": _EXTRA_FEATURE_DEFAULT_WEIGHT,
                    "type": _EXTRA_FEATURE_DEFAULT_TYPE,
                }
            )
        elif isinstance(feat, dict):
            normalizadas.append(dict(feat))
        else:
            raise ValueError(
                f"Qué pasó: extra_features[{i}] es {type(feat).__name__}, no str ni dict. "
                f"Por qué importa: el scorer no sabría cómo comparar esa variable. "
                f"Qué hacer: pase el nombre de la columna ('TELEFONO') o un dict "
                f"{{'column': 'TELEFONO', 'weight': 0.05, 'type': 'categorical_signed'}}."
            )
    return normalizadas


def crear_config_orchestrator(
    perfil: str = "produccion_estandar",
    workspace: str | None = None,
    validate: bool = True,
    col_name: str | None = None,
    col_nit: str | None = None,
    col_ciudad: str | None = None,
    extra_features: list[Any] | None = None,
    **overrides,
) -> dict[str, Any]:
    """
    Crea configuración completa para el Orchestrator.

    Args:
        perfil: Nombre del perfil base (de PERFILES_BASE)
        workspace: Directorio de trabajo (se genera si no se proporciona)
        validate: Si True (default), audita el config resultante con
            validar_config() y emite warnings sobre dead code.
        col_name: Columna de razón social en las fuentes del usuario. Si
            difiere de "RAZON_SOCIAL", el Orchestrator la renombra al canónico
            en la ingesta (v0.12.0; hasta 0.11.x se ignoraba en silencio).
        col_nit: Ídem para el NIT (canónico "NIT").
        col_ciudad: Ídem para la ciudad (canónico "CIUDAD"); None si no aplica.
        extra_features: Variables adicionales para el scoring. Cada elemento es
            un string (nombre de columna; peso 0.05, tipo 'categorical_signed')
            o un dict ``{"column", "weight", "type"}``. Se inyectan al perfil
            activo (misma vía que usa deduplicate_unified).
        **overrides: Parámetros del perfil a sobrescribir. Una clave
            desconocida lanza ValueError con sugerencia (fail-fast, v0.12.0).

    Returns:
        Dict con configuración completa lista para Orchestrator. Incluye
        ``column_mapping`` ({canónico: columna_usuario}; vacío si no aplica).

    Raises:
        ValueError: perfil inexistente, override desconocido o
            extra_features malformadas.

    Example:
        config = crear_config_orchestrator(
            perfil='produccion_calibrada',
            lsh_threshold=0.50  # Override específico
        )
    """
    # Validar perfil
    if perfil not in PERFILES_BASE:
        perfiles_disponibles = ", ".join(PERFILES_BASE.keys())
        raise ValueError(
            f"❌ Perfil '{perfil}' no existe.\n   Perfiles disponibles: {perfiles_disponibles}"
        )

    # Obtener perfil base
    perfil_config = PERFILES_BASE[perfil].copy()

    # Retro-compat: los kwargs de mapeo también se aceptan dentro de overrides
    # (así llamaba api.linkage() hasta 0.11.x).
    col_name = overrides.pop("col_name", col_name)
    col_nit = overrides.pop("col_nit", col_nit)
    col_ciudad = overrides.pop("col_ciudad", col_ciudad)
    extra_features = overrides.pop("extra_features", extra_features)

    # Alias legados (p. ej. trusted_sources → trusted_unique_sources).
    for alias, real in _ALIAS_OVERRIDES.items():
        if alias in overrides and real not in overrides:
            overrides[real] = overrides.pop(alias)

    # Aplicar overrides con validación fail-fast (v0.12.0).
    # Hasta 0.11.x una clave desconocida se descartaba con un print y la
    # corrida seguía con los defaults — el modo de falla más caro: el usuario
    # cree que su parámetro rige y no rige. Ahora: error accionable.
    if overrides:
        claves_validas = set(perfil_config.keys())
        claves_invalidas = set(overrides.keys()) - claves_validas
        if claves_invalidas:
            import difflib

            partes = []
            for clave in sorted(claves_invalidas):
                cerca = difflib.get_close_matches(clave, sorted(claves_validas), n=1)
                partes.append(f"'{clave}'" + (f" (¿quiso decir '{cerca[0]}'?)" if cerca else ""))
            raise ValueError(
                f"Qué pasó: overrides desconocidos para el perfil '{perfil}': "
                f"{'; '.join(partes)}. "
                f"Por qué importa: hasta v0.11 esto se ignoraba en silencio y la corrida "
                f"seguía con los defaults — el parámetro que usted creía activo no regía. "
                f"Qué hacer: use una clave válida del perfil "
                f"({', '.join(sorted(claves_validas))}) o, para mapear columnas y "
                f"variables, col_name=/col_nit=/col_ciudad=/extra_features=."
            )
        for clave, valor in overrides.items():
            perfil_config[clave] = valor

    # extra_features: normalizar e inyectar al perfil activo. El
    # VectorizedScorer lee profile["extra_features"]. La validación contra
    # columnas reales ocurre en Orchestrator.__init__ (ahí ya hay fuentes).
    extra_norm = _normalizar_extra_features(extra_features)
    if extra_norm:
        perfil_config["extra_features"] = extra_norm

    # Mapeo de columnas {canónico: columna_del_usuario}; solo entradas que
    # difieren del canónico. Lo aplica el Orchestrator en la ingesta.
    column_mapping: dict[str, str] = {}
    if col_name and col_name != "RAZON_SOCIAL":
        column_mapping["RAZON_SOCIAL"] = col_name
    if col_nit and col_nit != "NIT":
        column_mapping["NIT"] = col_nit
    if col_ciudad and col_ciudad != "CIUDAD":
        column_mapping["CIUDAD"] = col_ciudad

    # Generar workspace si no se proporciona
    if workspace is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workspace = f"z_workspace_{perfil}_{timestamp}"

    # Construir configuración completa
    config = {
        "profile": perfil,
        "output_directory": workspace,
        "linkage_engine_class": "disk_based",
        "source_priorities": obtener_prioridades_fuentes(),
        "profiles": {perfil: perfil_config},
        "column_mapping": column_mapping,
    }

    # v3.2.4: validar config (audita dead code)
    if validate:
        validar_config(config, verbose=True)

    return config
