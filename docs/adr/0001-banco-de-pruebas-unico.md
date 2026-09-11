# ADR-0001 · Un solo banco de pruebas para toda decisión

**Estado:** aceptado · **Fecha:** 2026-08-29 · **Versión:** 0.18.0

## Contexto

Entre 0.14.1 y 0.17.4 cada mejora se justificó con una medición hecha a mano:
un notebook aquí, un script ahí, un conjunto de datos distinto cada vez. Dos
regresiones llegaron a producción con la etiqueta de "verificadas":

- **0.17.0** — quitar términos organizacionales de la limpieza subió el ancho
  medio de nombre de 17,5 a 24,8 caracteres y triplicó los candidatos. Se
  validó contra un ground truth sintético sin medir a escala real.
- **0.17.3** — el bloqueo por NIT construía 19 cadenas de Python por registro.
  Sobre 4,37 M de filas eso son 83 millones de objetos y entre 6 y 10 GB. Se
  corrigió el mismo patrón en el bloqueo LSH y no se auditó el gemelo.

El factor común no es descuido: es que **no había con qué comparar**. Dos
mediciones sobre datos distintos no se restan.

## Decisión

Un único banco (`record_linkage.evaluation.banco` + `scripts/banco.py`) con:

1. Un conjunto de referencia fijo y versionado en el repositorio.
2. Una sola invocación, que mide calidad, tiempo, memoria y disco en la misma
   pasada.
3. Un formato de salida único (`corrida_<etiqueta>.json`) que incluye la
   especificación con la que se corrió y el entorno.
4. Un comparador que emite veredicto binario con umbrales declarados.
5. Una huella SHA-256 de la partición, para poder afirmar "esto no cambió
   nada" sin revisión manual.

Se prefirió `ground_truth_grande.csv` sobre `gt_robusto.parquet` porque trae
variables de contacto, casos negativos explícitos y los dos regímenes
mezclados.

## Consecuencias

**A favor**

- Toda afirmación de mejora es reproducible con un comando.
- La comparación es campo por campo, no de memoria.
- La evidencia queda versionada junto al código que la produjo.
- El comparador devuelve código de salida, así que sirve directo en CI.

**En contra**

- Una corrida tarda entre 25 y 40 segundos; iterar es más lento que confiar
  en la intuición.
- El conjunto de referencia es sintético en su origen. Mide bien lo que
  modela —variantes tipográficas, formatos de identificador, intermediarios—
  y no mide lo que no: por eso el criterio C14 del plan exige además una
  estimación de precisión sobre datos reales.

## Alternativas descartadas

- **Seguir con mediciones ad hoc.** Es el statu quo que produjo el problema.
- **Usar solo `gt_robusto.parquet`.** Sin variables de contacto no se puede
  medir si aportan, y sin casos negativos la precisión no distingue entre un
  sistema prudente y uno que no une nada.
- **Medir contra los datos reales del usuario.** No hay verdad de terreno
  sobre ellos; es justamente lo que el plan pide construir aparte.
