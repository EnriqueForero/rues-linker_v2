"""
engine.similarity — record_linkage_pipeline

Componentes:
    - class BasicSimilarityCalculator  (origen: notebook celda [121])
    - class SimilarityCalculator  (origen: notebook celda [121])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from functools import lru_cache

from rapidfuzz import fuzz


class BasicSimilarityCalculator:
    """Calculador de similitud básico como fallback."""

    def name_similarity(self, name1: str, name2: str) -> float:
        """Similitud básica entre nombres."""
        if not name1 or not name2:
            return 0.0
        if name1 == name2:
            return 1.0

        return fuzz.token_set_ratio(name1, name2) / 100.0

    def phonetic_keys(self, text: str) -> tuple[str, str]:
        """Generar claves fonéticas básicas."""
        if not text:
            return ("", "")

        # Consonantes
        consonants = "".join(c for c in text if c.isalpha() and c not in "AEIOU")[:10]
        # Primeras letras
        first_letters = "".join(w[0] for w in text.split() if w)[:6]

        return (consonants, first_letters)


class SimilarityCalculator:
    """
    Calculador de similitud de nivel de producción que utiliza un enfoque híbrido,
    resistente a errores de tipeo, desorden de palabras y sufijos societarios.

    PASO 1.2: Se eliminó la re-limpieza con CLEANUP_REGEX.
    Los nombres que llegan aquí ya fueron limpiados por TextProcessor en L1
    (columna NOMBRE_LIMPIO). Re-limpiar con reglas distintas causaba
    inconsistencia entre lo indexado en MinHash y lo comparado aquí.
    """

    def __init__(self):
        # Paso 1.2: CLEANUP_REGEX eliminado.
        # La limpieza de sufijos societarios e INTERNACIONAL/COMERCIALIZADORA/GRUPO/HOLDING
        # ya la hace TextProcessor en L1. No se necesita una segunda limpieza aquí.
        #
        # v0.12.0: el caché LRU pasa a ser POR INSTANCIA. El decorador a nivel
        # de clase (patrón B019) anclaba cada instancia (y sus hasta 250K
        # pares de strings) a un caché global que jamás se liberaba con el
        # objeto. Mismo comportamiento y misma interfaz (cache_info/
        # cache_clear disponibles), pero la memoria muere con la instancia.
        self.name_similarity = lru_cache(maxsize=250_000)(self._name_similarity_impl)

    def _name_similarity_impl(self, nombre1: str, nombre2: str) -> float:
        """
        Calcula una similitud híbrida y robusta.
        Recibe NOMBRE_LIMPIO (ya procesado por TextProcessor).
        """
        if not all(isinstance(n, str) and n for n in [nombre1, nombre2]):
            return 0.0
        if nombre1 == nombre2:
            return 1.0

        # Paso 1.2: Usar nombres directamente (ya limpios por TextProcessor)
        core_name1 = nombre1
        core_name2 = nombre2

        # 2. Score de Tokens (maneja desorden de palabras)
        token_score = fuzz.token_set_ratio(core_name1, core_name2) / 100.0

        # 3. Score de Secuencia (maneja errores de tipeo)
        sequence_score = fuzz.WRatio(core_name1, core_name2) / 100.0

        # 4. Ponderación Inteligente
        if token_score > sequence_score:
            final_score = 0.7 * token_score + 0.3 * sequence_score
        else:
            final_score = 0.3 * token_score + 0.7 * sequence_score

        return final_score

    def phonetic_keys(self, text: str) -> tuple[str, str]:
        if not text:
            return ("", "")
        consonants = "".join(c for c in text if c.isalpha() and c not in "AEIOU")[:10]
        first_letters = "".join(w[0] for w in text.split() if w)[:6]
        return (consonants, first_letters)
