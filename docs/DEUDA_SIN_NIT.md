# Deuda del perfil sin identificador (`deduplication_sin_nit_conservador`)

*Escrito el 2026-10-13 (Fase 0 de la ruta). La prueba `tests/test_sin_nit_recalibrado.py` citaba este documento desde v0.7.4
y el documento no existía.*

## Qué es

`dedupe()` → `deduplicate_auto` separa los registros por régimen: con identificador (NIT) y sin él. Los que no traen NIT se
deduplican con `deduplicate_unified` y el perfil `deduplication_sin_nit_conservador` (`config/profiles.py`), calibrado en
v0.7.4 contra los 2.342 registros SIN_NIT de `tests/data/ground_truth_grande.csv` (sintético): P = 0,907 · R = 0,501 · F1 = 0,645.
El perfil prioriza precisión: prefiere no fusionar un par dudoso.

## Qué se midió el 2026-10-13

| | v0.7.4 (calibración) | hoy (0.22.4 + F0–F3.1b) |
|---|---|---|
| Precisión | 0,907 | **0,864** |
| Recall | 0,501 | **0,547** |
| F1 | 0,645 | **0,670** |
| TP / FP / FN | — | 2.548 / 400 / 2.113 |

Es un **desplazamiento del balance**, no una caída: el F1 subió 0,025 y el recall 0,046; la precisión bajó 0,043. La cifra es
idéntica con cuatro combinaciones de versiones de dependencias (rapidfuzz 3.14.5/3.14.6, pandas 3.0.5/3.0.6, scikit-learn
1.9.0/1.9.1, scipy 1.17/1.18) y en las 20 corridas del Nightly desde el 24-sep: **es el código, no el entorno**.

## Por qué no se corrigió a la raíz

- El cambio ocurrió entre v0.7.4 y v0.22.4, antes de que existiera este repositorio (la historia de v1 no está aquí); lo más
  probable es la normalización de la similitud de nombre (ADR-0009, ADR-0010) o el catálogo de genéricos (0.22.0).
- `deduplicate_unified` es un camino **heredado**: la ruta (`docs/DIAGNOSTICO_Y_RUTA_2026-10-13.md`, Fase 3 y Fase 6) lo
  sustituye por el puntaje probabilístico único y lo retira en 1.0. Invertir en bisecarlo hoy es invertir en código que se va.
- El CI diario no corre esta prueba (`-m "not slow"`), así que llevaba un mes en rojo sin consecuencias: la decisión honesta es
  dejar la cota donde el motor **está** y vigilar que no baje de ahí, no fingir una cota que no se cumple.

## Decisión (ADR-0014)

Las cotas de la prueba pasan a lo medido con margen: **P ≥ 0,85 · R ≥ 0,50 · F1 ≥ 0,64**. Cualquier cambio que las mueva se
declara. Cuando la Fase 3 entregue el puntaje calibrado, este perfil se mide contra la verdad real y se retira o se reemplaza.

## Lo que esta deuda cuesta al usuario

En una base sin NIT, de cada 100 pares que el motor une, ≈ 14 son empresas distintas, y deja sin unir ≈ 45 de cada 100 pares
verdaderos. Es inaceptable para una decisión automática: por eso la salida marca esos grupos como «sin identificador» y la
regla de la casa es revisarlos.
