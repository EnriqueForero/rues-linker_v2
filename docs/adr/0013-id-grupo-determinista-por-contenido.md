# ADR-0013 — `ID_GRUPO` es determinista por contenido: el menor `ID_REGISTRO` del grupo, no la posición

- **Estado:** aceptada
- **Fecha:** 2026-10-06
- **Versión:** F2.3 (rues-linker v2, sobre 0.22.4 + F1)

## Contexto

Hasta F1, `ID_GRUPO` era la **posición mínima** del grupo en el consolidado
de L1 (`engine/clusterer.py`, «representante = nodo mínimo de cada
componente»). Es un id correcto para una corrida, pero volátil: una fila
nueva al principio de una fuente, o la misma entrada en otro orden, cambia
el id de todos los grupos aunque la partición sea idéntica. El estándar de
salida (ESTANDAR_SALIDA.md) separa el id interno del clúster (`ID_GRUPO`,
de la corrida) del id público estable (`ID_ENTIDAD`, F1.9/F2.4), y pide que
el primero sea «determinista por contenido: el menor `ID_REGISTRO` del grupo
en orden lexicográfico, no la posición».

Además, la regla de `ID_REGISTRO` (`<SRC>-<id nativo>` si `col_id` es única
por fila en la fuente; si no, `<SRC>-F<ORIGINAL_INDEX>`) vivía dentro de
`salida/completar.py` (después de L5), y el clusterer (L4) la necesita para
etiquetar. Regla 3 del CLAUDE.md: una regla se escribe una vez.

## Decisión

1. **Una regla de identidad, un módulo:** `salida/identificadores_registro.py`
   contiene `asignar_id_registro` (la clave por registro, movida tal cual desde
   `completar`), `nombre_efectivo` (cómo se llama en el motor la columna que
   nombró el usuario) e `id_grupo_por_clave_minima` (el entero de grupo).
   L4 (`pipeline/orchestrator._run_L4` → `OptimizedClusterer.cluster_entities(
   …, claves_registro=)`) y `salida/completar.py` la llaman; ninguno la copia.
   `api.linkage` deja `col_id` en `config["col_id"]` para que L4 lo vea.

2. **El entero:** cada grupo se representa por su menor `ID_REGISTRO` en orden
   lexicográfico; `ID_GRUPO` es la posición de esa clave entre las claves
   representantes de todos los grupos, ordenadas (denso desde 0). Es un
   `factorize` estable: misma entrada en cualquier orden → misma partición y
   mismos ids. El contrato no cambia (`ID_GRUPO` sigue siendo `int64`;
   versión 1.0).

3. **Dónde se aplica:** en L4 (el motor etiqueta así desde `clusters.parquet`)
   **y** en `completar_correlativa`, con la misma función, porque L5 renumera
   al separar (`cannot_link`: `max+1` por orden de aparición) y al fusionar por
   NIT, y `dedupe` llega con etiquetas de texto (`C<n>`/`S<n>`). Si el motor ya
   venía por contenido, `completar` no cambia nada y el reporte lo declara
   (`id_grupo.recodificado: False`); el golden se recodifica con el mismo mapa.
   Sin claves (`claves_registro=None`, llamadores heredados del clusterer) el
   representante sigue siendo el nodo mínimo: la partición es la misma en
   ambos casos.

4. **Orden lexicográfico, no numérico.** `ID_REGISTRO` es texto; el único
   orden total válido para cualquier fuente es el de cadenas (`RUES-F10` <
   `RUES-F2`). Es irrelevante para la estabilidad —lo que importa es que sea el
   mismo en toda corrida— y evita inventar un parseo por fuente.

5. **Una `col_id` inexistente falla en L4** (mensaje accionable), no al final
   de la corrida como antes: el mismo `ValueError` de `asignar_id_registro`.

## Medición

- **Banco** (`benchmark_institucional.csv.gz`, 30.486 filas), `base_f0` → `f2_3`
  (`scripts/banco.py --comparar base_f0 f2_3`: **PASA**):
  precisión 0,9385 → 0,9385 · recall 0,8249 → 0,8249 · F1 0,8780 → 0,8780 ·
  B³-F1 0,9534 → 0,9534 · macro-F1 0,8920 · FP sobre negativos 287 → 287 ·
  grupos predichos 13.069 → 13.069 · **huella 1e365ba81c4df45e… → idéntica**
  (`huella_particion` se construye con el índice mínimo de cada grupo, no con
  el valor del id). Tiempo 54,9 s → 49,3 s (L4 0,4 s), RSS pico 543 → 502 MiB
  (ruido de máquina, no efecto del cambio).
- **Costo de la regla, medido aislado** a 2.000.000 de filas (633.355 grupos,
  tres fuentes, `col_id` único): `asignar_id_registro` 2,9 s;
  `id_grupo_por_clave_minima` 0,98 s con claves `string[pyarrow]` (sin copia) y
  1,5 s desde `object`. La primera versión (`groupby().transform("min")` sobre
  cadenas `object`) tardaba 38 s a 2M por comparar cadenas en Python; se
  reemplazó por `pyarrow.compute.sort_indices` (orden de bytes UTF-8 = orden
  por puntos de código de Python) + `np.minimum.at` sobre el rango entero.
- **Determinismo entre procesos:** `linkage()` sobre la misma entrada barajada
  con tres semillas distintas, en tres subprocesos con `PYTHONHASHSEED`
  distinto, da el mismo mapa `ID_REGISTRO → ID_GRUPO` (18 grupos, hash
  idéntico): el orden sale de `sort_indices`, nunca de un `set`.

## Consecuencias

- Reordenar la entrada no cambia la partición ni los ids
  (`tests/test_id_grupo_determinista.py`, extremo a extremo con `linkage()`).
- `dedupe` cambia el valor de `ID_GRUPO` (antes: entero por primera aparición
  en `ORIGINAL_INDEX`; ahora: la misma regla que los demás caminos). La huella
  de conformidad no depende del valor.
- Los `ID_GRUPO` de `_trabajo/L4_clustering/clusters.parquet` y
  `L5_golden/correlative.parquet` ya son por contenido; con
  `collapse_exact_duplicates` la expansión reasigna `ORIGINAL_INDEX` y
  `completar` vuelve a aplicar la regla sobre el `ID_REGISTRO` final.
- Pendiente (F2.4): la herencia entre corridas vive en `ID_ENTIDAD`, no aquí.
