> **Copia del plan de ejecución del 2026-10-05** (exportación del 2026-10-06), guardada aquí para que no dependa de ningún directorio temporal.
> El estado vivo está en [`REANUDAR.md`](REANUDAR.md). Donde este plan dice «de esta entrega» o «la entrega del 5-oct» se refiere a archivos
> (`formato_vpi.py`, `test_ida_y_vuelta.py`, el Excel VPI) que NO están en el repositorio ni en el Drive; ver `REANUDAR.md` §5.

<!-- Exportado del documento vivo «Plan de ejecución rues-linker v2 → producción (para Claude Code)»
     https://claude.ai/code/artifact/20f8f603-3aae-48e6-ae4d-95482bbcd80f · exportación 2026-10-06.
     Evidencia adjunta en la misma carpeta: bench_runner.py, bench_runner_estrellas.py, evaluar.py,
     evidencia_escala_y_estrellas.zip (corrida.json / calidad.json / estrellas.json / manifest.json / logs de cada corrida).
     Regla para Claude Code: ejecutar por fases, en orden, sin pasar a la siguiente sin cumplir la compuerta;
     ningún cambio en el motor sin banco antes y después; ningún dato licenciado ni nombre de empresa en el repositorio. -->

# Plan de ejecución rues-linker v2 → producción (para Claude Code)

Oct 5, 2026 · @Enrique

## Cómo usar este plan con Claude Code

Este plan deja rues-linker v2 listo para producción sin que nada empeore frente a v1. Cada tarea trae archivo, cambio, prueba y criterio de aceptación; Claude Code las ejecuta en orden dentro de cada fase y no pasa a la siguiente sin cumplir la compuerta. Repositorio de trabajo: [rues-linker\_v2](https://github.com/EnriqueForero/rues-linker_v2), rama `main` en `b45e335` más el arreglo de CI de `feature/vinculacion-0.23.0` (`ab67f86`), que es todo lo que esa rama agrega.

**Reglas de trabajo (no negociables):**

1. Ningún cambio sin prueba primero: se escribe la prueba que falla, luego el cambio, luego la prueba pasa. Lo que no tiene prueba no entra.
2. Cada tarea se cierra con `ruff check`, `ruff format --check`, `mypy` del anillo estricto y `pytest -m "not slow"` en verde, más la compuerta de su fase.
3. El banco y la conformidad corren antes y después de todo cambio que toque el motor (L1…L5). Un cambio de comportamiento se declara: perilla nueva con valor por defecto que conserva la huella, ADR y entrada en `CHANGELOG.md`.
4. Las bases licenciadas (Orbis, fDi) y los nombres de empresas de la realidad no entran al repositorio; en pruebas, empresas inventadas; en manifiestos, huellas SHA-256 y conteos, nunca nombres.
5. Una regla se escribe una vez. Si dos módulos la necesitan, los dos la importan del mismo sitio.
6. Nada se repara en silencio: si falta un entregable, la corrida falla con mensaje accionable; si se repara, queda en el manifiesto.
7. El código nuevo es español, con `@dataclass` para configuración, `pathlib`, sin `iterrows`/`apply` fila a fila, sin `print` en la librería, excepciones específicas.
8. Commits pequeños, un tema por PR, CI en verde en `main` antes de fusionar.

**Compuertas obligatorias (se definen en F0 y se repiten en cada fase):**

| Compuerta | Qué mide | Umbral |
| --- | --- | --- |
| Banco | F1, macro-F1, B³, FP sobre negativos y huella sobre `benchmark_institucional.csv.gz` | sin cambio de motor: huella idéntica (`1e365ba8…`); con cambio declarado: macro-F1 ≥ 0,892 y FP sobre negativos ≤ 287 |
| Conformidad | 43 casos frontera (`scripts/conformidad.py`), sin y con `--corroborar` | 0 casos firmes en FALLA |
| Contrato | esquema de correlativa, golden, enlaces, manifiesto y archivos esperados | prueba de esquema verde en todos los caminos |
| Escala | tiempo, RAM y candidatos a 139k y 463k sintéticos (y 1 M en F4) | sin retroceso > 10 % frente a la línea base de F0 |
| Determinismo | dos procesos con `PYTHONHASHSEED` distinto | misma huella |

**Decisiones ya tomadas por Enrique (no se vuelven a discutir):**

- Un solo estándar para todos los equipos; lo que no se puede romper es la correlativa original: entrada completa y salida con fuente, `NIT_FINAL`, `RAZON_SOCIAL_FINAL` y las variables adicionales. Es la tabla maestra para todos los cruces.
- Se conservan los nombres de columna de v1; se puede ofrecer un alias en español.
- La correlativa lleva todas las columnas de la fuente.
- Excel es entregable; si la tabla no cabe en Excel (1.048.576 filas), el entregable es el parquet, como en v1, y el Excel lo dice.
- Los reportes L6 se conservan todos y se mejoran; no se retiran.
- Las decisiones asistidas por IA las firma Enrique; se pueden entregar filas «(validar)» si van marcadas.
- Antes de retirar `RecordLinkagePipeline`, `deduplicate_unified` o `api.dedupe/link` se prueba y se investiga quién depende de ellos (hecho en este plan: ver F2).
- Los notebooks 09 y 10 se conservan como ejemplos reales y se vuelven genéricos; solo después se retiran.
- Escala objetivo: 4 millones de registros en máximo 11 horas de Colab.
- Unir dos empresas distintas duele más que dejar sin unir; las dos son graves.
- Enrique publica versiones y fusiona el CI; regla: ningún cambio sin banco y sin prueba de contrato.
- El formato VPI es el resultado de una consulta (vista), no el estándar.

## Hechos nuevos medidos hoy (cambian el plan)

Todo lo de esta sección se midió el 5 y 6 de octubre de 2026 sobre los repositorios clonados (`v1` 0.11.0 en `d1beb59`, `v2` 0.22.4 en `b45e335`), con el evaluador de v2 (`evaluation/banco.py`) para las dos versiones y los corredores `bench_runner.py` / `bench_runner_estrellas.py` / `evaluar.py` que se entregan con este plan. Lo que no se midió está marcado VACÍO en la última sección.

**1 · La cobertura por estrellas dentro de `linkage()` pasó de INFERENCIA a HECHO.** Prototipo fuera del repositorio (`bench_runner_estrellas.py`): parchea `aplicar_cannot_link_identificador` en L5 y aplica `engine.cobertura.cobertura_estrella` solo a los grupos sin identificador válido, con el comparador `matching.nombre_idf.SimilitudNombre` (IDF del corpus, genéricos declarados neutralizados) y `similitud_minima = 2·umbral − 1`. Costo: 4,4–4,6 s a 30k y 10,4–11,4 s a 139k (1.279 y 2.727 grupos sin identificador). Resultados con el mismo evaluador:

| Conjunto | Umbral | Precisión | Recall | F1 | macro-F1 | FP sobre negativos | Grupo mayor (registros / entidades) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| banco 30.486 | sin estrellas (v2 hoy) | 0,9385 | 0,8249 | 0,8780 | 0,8920 | 287 | — |
| banco 30.486 | 0,70 | 0,9468 | 0,8227 | 0,8804 | 0,8936 | 277 | — |
| banco 30.486 | 0,75 | 0,9506 | 0,8140 | 0,8770 | 0,8903 | 264 | — |
| banco 30.486 | 0,80 | 0,9557 | 0,7927 | 0,8666 | 0,8803 | 246 | — |
| banco 30.486 | 0,84 (el de `importadores`) | 0,9577 | 0,7645 | 0,8503 | 0,8647 | 180 | — |
| sintético 139.028 | sin estrellas (v2 hoy) | 0,3387 | 0,9647 | 0,5014 | — | 105 | 630 / 198 (12 grupos ≥ 50) |
| sintético 139.028 | 0,70 | 0,7940 | 0,9504 | 0,8652 | — | 94 | 100 / 52 |
| sintético 139.028 | 0,80 | 0,9848 | 0,9474 | 0,9657 | — | 34 | 13 / 2 (0 grupos ≥ 50) |
| sintético 139.028 | 0,84 | 0,9969 | 0,9401 | 0,9677 | — | 5 | 11 / 2 |
| sintético 139.028 | v1 0.11.0 | 0,0092 | 0,8840 | 0,0182 | — | 6.188 | (19,0 M de falsos positivos) |

Lectura: en el banco institucional el efecto es pequeño (de +0,002 a −0,028 de F1 según umbral: allí casi todos los grupos tienen NIT); en el sintético grande, donde el nombre es la única evidencia, el encadenamiento de v2 destruye la precisión (0,34) y las estrellas la recuperan (0,98 con 0,80). Por eso F2.1 fija 0,80 como valor por defecto propuesto y F2.2 obliga a medir el barrido por estrato (macro-F1) antes de fijarlo: 0,70 mejora los dos conjuntos pero deja grupos de 100 registros con 52 entidades; 0,84 maximiza la precisión y pierde 0,034 de recall en el banco. Enrique decidió que la fusión falsa pesa más (Q12): el empate se rompe hacia la precisión.

**2 · Escala.** Medido a 30k, 139k y 463k y verificado contra la corrida real de 355.681 importadores en Colab: los números y la causa raíz están en F4. En una frase: L3 pierde 3× de velocidad dentro de la misma corrida porque la consulta de paginación con `OR` hace que SQLite recorra la tabla desde el principio en cada lote (`SCAN`, verificado con `EXPLAIN QUERY PLAN`), y ese defecto solo vuelve inalcanzables las 11 h a 4 M. El arreglo es de una línea más dos pruebas (F4.1).

**3 · Conformidad (43 casos).** v2 sin `--corroborar` falla C09 y C21 (frontera: NIT distinto con correo y teléfono iguales); con `--corroborar` pasan los 43, y el JSON publicado `conformidad_dedup_base.json` corresponde a la corrida corroborada sin que el flag quede registrado. v1 con su API clásica falla MC01 y C23 porque ignora `extra_features`; el motor multicampo de v1 (`evaluar_esquema`) coincide par por par con el de v2. La conformidad ejercita `api.dedupe_esquema`, no `linkage()`: nada de lo añadido entre 0.12 y 0.22 en el camino de producción tiene veredicto por caso, y solo 37 de los 43 casos tienen veredicto explícito en `docs/CONFORMIDAD.md`. De ahí F0.3 (registrar el flag y congelar la línea base) y F2.13 (conformidad sobre `linkage()` y veredicto explícito para los 43 casos).

**4 · Degradaciones silenciosas, con causa raíz.** (a) `golden/containment.py:421-431` más `_concat_filtrado_por_columnas` (509-528) reconstruyen las filas golden fusionadas por NIT con `groupby(...).first()` sobre columnas que no existen en ese marco: métricas en NaN, 20 columnas de la correlativa pegadas al golden y `SRC` vacío en el 99,9 % de las filas golden. (b) `reporting/suite.py:140-154` hace `groupby("SRC").apply(sample)`; con pandas 3 la columna de agrupación desaparece del marco y la muestra queda vacía cuando el golden supera 30.000 filas, así que la tarjeta, el mapa de calor y los casos se omiten con un `WARNING` que nadie lee. (c) Veinte puntos de escritura (`orchestrator.py:1804-1806`, `strategies.py:231-237` y `420-453`, `reports.py` en seis sitios, `dashboard.py`, `visualizer.py`, `cruce.py:1552-1567`, `auto.py`, `api.py`, `columnas_finales.py:302-320`, `unified.py:247-254`, `smart.py:164-171`) atrapan la excepción y siguen. Todo esto se cierra en F1 (contrato + manifiesto `omitidos` + prueba que falla si falta una salida).

**5 · Dependencias de los caminos paralelos (para retirar sin romper).** `RecordLinkagePipeline` lo usa el `Orchestrator` solo como fábrica de componentes (`orchestrator.py:280-283`, `940-963`) más 3 scripts y 3 archivos de prueba → retirable tras migrar la fábrica (F2.9). `deduplicate_unified` lo usan 17 archivos de prueba y 8 scripts → se conserva y se le arregla el contrato. `deduplicate_dataframe`, `deduplicate_large_dataset_colab`, `OptimizationEngine`/`optuna_integration` y `GroundTruthGenerator` → retirables. `api.dedupe` y `api.link` se conservan. `PipelineResult.to_excel`/`to_csv` lo usan 6 notebooks → se extrae a una función del estándar.

**6 · Verificaciones en Google Drive y GitHub.** La rama `feature/vinculacion-0.23.0` es `main` (`b45e335`) más un solo commit `ab67f86` que arregla el CI: sigue en 0.22.4 y no contiene `flujo/vinculacion.py`; lo que la skill `record-linkage-empresas` describe como «desde 0.23.0 el motor vive en la librería» es el estado objetivo de F3, no el actual. En el Drive de Enrique no hay código de rues-linker posterior a 0.22.4 (`rues_linker_pruebas`, copia del 2026-09-11); en `dist/` existe `rues_linker-0.22.5-py3-none-any.whl` (2026-09-11) cuyo contenido no se verificó (VACÍO). El notebook VPI de Caro no está en ese Drive (VACÍO).

## El estándar de salida definitivo

Una corrida de cualquier flujo (deduplicar, cruzar bases, importadores, vincular) deja una sola carpeta con la misma forma, y todo Excel se genera desde ella. La correlativa es LA tabla: una fila por registro de entrada, con todas sus columnas originales y lo que el motor decidió. Es lo que Enrique describió como tabla maestra y lo que v1 ya daba.

```text
<carpeta_salida>/<AAAA-MM-DD_HHMM>_<nombre>/
├── correlativa.parquet        una fila por registro de entrada (LA tabla)
├── golden.parquet             una fila por entidad
├── enlaces.parquet            solo vinculación: entidad ancla → registro destino
├── entidades_ids.parquet      crosswalk ID_ENTIDAD ↔ ID_GRUPO de esta corrida (ver abajo)
├── revision.csv               pares por decidir (forma del archivo de decisiones)
├── diccionario.csv            tabla · columna · tipo · significado · origen
├── manifest.json              contrato, versión, huellas de insumos y artefactos, parámetros, conteos, invariantes, tiempos y RAM por fase, artefactos omitidos (con motivo)
├── excel/                     correlativa.xlsx, golden.xlsx, informe_cruce.xlsx (reportes L6 unificados)
├── figuras/                   dashboard, heatmap, distribución de confianza, comparación de fuentes
└── _trabajo/                  L1…L5 (checkpoints), borrable
```

**Correlativa** (`correlativa.parquet`): primero las columnas fijas del contrato, en este orden, y después TODAS las columnas de la fuente sin cambios. Las técnicas (`NOMBRE_LIMPIO`, `NIT_OK`, `PHONETIC_KEY1`, `NOMBRE_BLOQUEO`) salen del entregable y quedan en `_trabajo/`.

| Columna | Tipo | Qué es |
| --- | --- | --- |
| `ID_REGISTRO` | texto | identidad estable del registro: `<SRC>-<id nativo>` si la fuente trae un id único por fila; si no, `<SRC>-F<fila>` |
| `SRC` | texto | base de origen (nombre de v1) |
| `ORIGINAL_INDEX` | entero | fila en el archivo de origen (nombre de v1) |
| `ID_GRUPO` | entero | grupo de esta corrida (nombre de v1), determinista |
| `ID_ENTIDAD` | texto | identificador estable entre corridas (nuevo; ver regla) |
| `NIT_FINAL`, `RAZON_SOCIAL_FINAL` | texto | identidad adoptada (nombres de v1) |
| `NAME_SIMILARITY_SCORE` | decimal 0–1 | parecido del nombre con el adoptado (v1); se corrige para comparar normalizado contra normalizado (C38 del repo) |
| `NIT_DISTANCE` | entero | distancia del identificador al adoptado (v1) |
| `SCORE_PAR` | decimal 0–1 | puntaje del par que unió el registro a su grupo (nuevo) |
| `METODO_UNION` | texto | `identificador` · `nombre` · `nombre+contacto` · `decision_humana` · `decision_asistida (validar)` · `sin_pareja` (nuevo) |
| `CONFIANZA` | texto | ALTA · MEDIA · BAJA con una sola definición documentada en `diccionario.csv` (nombre de v1, regla unificada) |

**Golden** (`golden.parquet`): las 13 columnas de v1 (`ID_GRUPO`, `NIT_FINAL`, `RAZON_SOCIAL_FINAL`, `PRIMARY_SOURCE`, `SOURCES_LIST`, `SOURCES_COUNT`, `RECORD_COUNT`, `NAME_VARIATIONS`, `NIT_VARIATIONS`, `CONFIDENCE_SCORE`, `CONFIANZA`, `REQUIRES_REVIEW`, `CREATED_AT`) más `ID_ENTIDAD`, con conteos enteros y `REQUIRES_REVIEW` booleano; sin columnas de la correlativa pegadas (hoy hay hasta 27 columnas vacías por dos filas huérfanas) y sin filas sin métricas.

**Enlaces** (`enlaces.parquet`, solo vinculación): `ID_GRUPO`, `ID_ENTIDAD`, `FUENTE_DESTINO`, `ID_DESTINO`, `NOMBRE_DESTINO`, `NIVEL` (EMPRESA, GRUPO, RELACION\_PROPIEDAD), `BANDA` (A, B, C), `METODO_UNION`, `CONFIANZA`, `PUNTAJE`, `MOTIVO`, `ES_PRINCIPAL`. La `BASE` del formato VPI se deriva de aquí.

**Excel y parquet:** `correlativa.xlsx` y `golden.xlsx` se escriben completos hasta 1.048.576 filas; si no caben, no se escribe un recorte: se escribe un `correlativa_LEEME.xlsx` que dice cuántas filas tiene el parquet y cómo abrirlo (pandas, DuckDB, Power Query). Nunca más `_MUESTRA_100k`.

**Alias en español:** `diccionario.csv` trae la columna `alias_es` (`ID_GRUPO` → `ID_GRUPO`, `PRIMARY_SOURCE` → `FUENTE_PRINCIPAL`…) y `leer_resultado(ruta, alias="es")` los aplica al leer; los archivos conservan los nombres de v1.

**Identificador de entidad estable (`ID_ENTIDAD`).** Hoy `ID_GRUPO` es la posición del primer registro del grupo (`engine/clusterer.py:262`): cambia con cualquier fila nueva o reordenada. La práctica recomendada en resolución de entidades es separar el id interno del clúster (volátil) del id público (estable) y mantener la historia de fusiones y divisiones:

- Senzing advierte que su id de entidad «no es un identificador persistente globalmente único» y recomienda no dejar que esa volatilidad llegue al contrato externo: un id público propio que apunte al clúster interno, con redirecciones al fusionar ([análisis del problema de inestabilidad](https://www.bencode.io/posts/entity/)).
- Zingg mantiene un `ZINGG_ID` que sobrevive corridas incrementales: un registro nuevo hereda el id del clúster al que se une; al fusionar dos clústeres sobrevive el id del mayor y el otro se retira; al dividir, el id original sigue al subclúster con más registros y el otro recibe id nuevo; tras un cambio de modelo, cada clúster nuevo recibe el id del clúster anterior con el que comparte más registros ([Zingg, flujo incremental](https://www.zingg.ai/post/entity-resolution-at-scale-part-5-incremental-flow)).
- Google formaliza el criterio como «estabilidad semántica del id»: un clúster que representa el mismo concepto que uno histórico debe recibir el mismo id ([Evaluation of Cluster Id Assignment Schemes with ABCDE](https://arxiv.org/abs/2409.18254)).

Regla para rues-linker (INFERENCIA a partir de esas fuentes, ajustada a datos con y sin NIT):

1. `ID_GRUPO` sigue siendo el id de la corrida, pero determinista por contenido: el menor `ID_REGISTRO` del grupo en orden lexicográfico, no la posición.
2. `ID_ENTIDAD` para grupos con identificador válido: `NIT-<NIT_FINAL base sin DV>`; es natural, estable y legible.
3. `ID_ENTIDAD` para grupos sin identificador: surrogado `ENT-<ulid>` asignado por crosswalk. Con un `entidades_ids.parquet` de la corrida anterior (opcional), cada grupo nuevo hereda el `ID_ENTIDAD` del grupo anterior con el que comparte más `ID_REGISTRO` (desempate: el grupo anterior más grande, luego el id menor); si dos grupos anteriores se fusionan, sobrevive el del mayor y el otro queda en `entidades_ids.parquet` como `retirado_en → sobreviviente`; si uno se divide, conserva el id la parte con más registros compartidos; sin corrida anterior, todos son nuevos.
4. El crosswalk se guarda en cada corrida y se declara en el manifiesto (huella de la corrida anterior usada). Sin NIT ni corrida anterior no hay magia: el id es nuevo y el manifiesto lo dice.

**Reportes L6:** se conservan los 24 entregables de v1 y se corrigen sus defectos medidos (tiempos inventados, claves que no existen, muestras no rotuladas, 2 golden huérfanas). Se unifican en un libro `informe_cruce.xlsx` con hoja LEEME y fórmulas vivas, un solo dashboard y un solo heatmap; los archivos de v1 se siguen escribiendo con su nombre durante dos versiones menores (alias), con una hoja LEEME que remite al nuevo.

## Mapa de fases y compuertas

```text
F0 · Fundaciones (no toca el motor)
   └─ Compuerta F0 · CI verde · 4 líneas base · trinquete activo
F1 · Contrato de salida y degradaciones silenciosas (severidad ALTA: va primero)
   └─ Compuerta F1 · huella idéntica · conformidad 0 fallas · sin omitidos a 139k y 463k
F2 · Calidad y unificación del motor
   └─ Compuerta F2 · macro-F1 ≥ 0,880 · grupo sin NIT ≤ 50 a 139k · contrato en 5 caminos
F3 · Vinculación dentro de la librería (0.23.0)
   └─ Compuerta F3 · huella de ENLACES igual · tag instalable · banco intacto
F4 · Escala a 4 millones en 11 horas de Colab
   └─ Compuerta F4 · 1 M ≤ 1,5 h y ≤ 4 GiB · 4 M reales ≤ 11 h y ≤ 11 GB
F5 · Ingeniería y estándar python-data-library-dev
   └─ Compuerta F5 · deuda ≤ v1 en todas las métricas · mypy limpio · CI ≤ 10 min
F6 · Release 1.0
   └─ Compuerta final · banco, conformidad, contrato, escala y determinismo verdes en la rueda instalada
```

Las fases van en serie y cada una cierra con una compuerta medida (banco, conformidad, contrato, escala). F1 va primero porque concentra la severidad alta (las degradaciones silenciosas que hoy entregan resultados incompletos sin avisar); F4 va antes que F5 porque la escala a 4 M es una restricción de negocio y porque su defecto principal (la paginación de L3) ya está diagnosticado y es barato. F2 y F3 pueden solaparse en ramas distintas: F3 no toca L1…L5.

## F0 · Fundaciones (1 semana)

Objetivo: que todo cambio posterior tenga una red que lo detecte. Sin F0, F1 a F5 no se pueden medir.

| ID | Tarea | Archivos | Prueba / criterio de aceptación |
| --- | --- | --- | --- |
| F0.1 | Fusionar el arreglo de CI (`ab67f86`) en `main` y corregir `.github/workflows/ci.yml:206`, que pide `--expected-version 0.16.0`: debe leer la versión de `pyproject.toml` (ya existe `scripts/verificar_coherencia_version.py`) | `.github/workflows/ci.yml`, `release.yml` | CI, Release y Nightly en verde en `main`; captura del run en `docs/BITACORA.md` |
| F0.2 | Congelar la línea base del banco: correr `scripts/banco.py --datos data/benchmark/benchmark_institucional.csv.gz --etiqueta base_f0` y guardar `docs/evidencia/corrida_base_f0.json`; prueba `tests/test_banco_linea_base.py` que exige huella `1e365ba81c4df45e410dd09154cafef1…` mientras no haya ADR que la cambie | `docs/evidencia/`, `tests/` | F1 0,8780 · macro-F1 0,8920 · huella idéntica; la prueba está marcada `slow` y corre en Nightly |
| F0.3 | Congelar la conformidad: `scripts/conformidad.py` sin y con `--corroborar`; que el JSON de evidencia registre el flag (hoy `conformidad_dedup_base.json` es en realidad la corrida corroborada y `nota` va vacía) | `evaluation/conformidad.py`, `scripts/conformidad.py`, `docs/evidencia/` | 0 casos firmes en FALLA en ambas; el JSON dice `corroborar: true/false` |
| F0.4 | Foto del esquema actual de `linkage()`: prueba que fija nombres, orden y tipos de las 30 columnas de `correlative.parquet`, las 40 de `golden.parquet`, los 31 archivos de L1…L6 y las claves del `manifest.json`, sobre `tests/data_sintetica` (sin datos reales) | `tests/test_contrato_salida_v0.py` (nuevo) | pasa en `b45e335`; es la referencia que F1 cambiará a propósito, con la prueba actualizada en el mismo PR |
| F0.5 | Compuerta de escala: script `scripts/escala.py` que genera (semilla 42) 139.028 y 463.473 registros con `scripts/generar_ground_truth_grande.py`, corre `linkage()` con `skip_reporting=True` y escribe tiempos por fase, candidatos, RSS pico y disco en `docs/evidencia/escala_<etiqueta>.json`; `--comparar` falla si algún tiempo sube > 10 % | `scripts/escala.py` (nuevo), `evaluation/banco.py` (reutilizar `MuestreadorRecursos`) | línea base guardada: 139k → L2 268 s, L3 168 s, RSS 1,14 GiB, 12,7 M candidatos; 463k → valores de la sección de hechos |
| F0.6 | Prueba de determinismo entre procesos para `linkage()` (dos subprocesos con `PYTHONHASHSEED` distinto, misma huella), como la que ya existe para importadores | `tests/test_determinismo_contrato.py` | pasa en `b45e335` |
| F0.7 | Inventario de consumidores en un archivo versionado: `docs/CONSUMIDORES.md` con quién lee qué archivo (VPI, Caro, Snowflake, notebooks 01–11), a partir de las respuestas de Enrique; es la lista que decide los alias y su plazo | `docs/CONSUMIDORES.md` | revisado por Enrique |
| F0.8 | Trinquete de calidad en CI: `ruff` con `PTH`, `BLE001`, `E722`, `T201` y `C901` (máx. 15) activadas solo para archivos nuevos o tocados (`--diff` contra `main`), y un conteo de referencia (`docs/evidencia/deuda_f0.json`: 35 CC≥20, 125 except sin relanzar, 179 print, 89 os.path, 113 mypy) que la CI no deja subir | `pyproject.toml`, `ci.yml`, `scripts/deuda.py` (nuevo) | CI falla si cualquier conteo sube |

**Compuerta F0:** CI verde en `main`; cuatro líneas base guardadas (banco, conformidad, esquema, escala); trinquete activo.

## F1 · Contrato de salida y cero degradaciones silenciosas en linkage() (2 a 3 semanas)

Objetivo: que `linkage()` entregue el estándar completo y que ninguna falla quede solo en un log. Se corrige primero lo que afecta a la correlativa y al golden (CRÍTICO), luego lo que omite entregables (ALTO), luego lo que inventa cifras (MEDIO). Todas las referencias archivo:línea son de `b45e335`.

| ID | Tarea | Archivos | Prueba / criterio |
| --- | --- | --- | --- |
| F1.1 | **Golden huérfanas (CRÍTICO).** Tras la consolidación por NIT, los grupos fusionados se reconstruyen con `groupby("ID_GRUPO").first()` de la correlativa (`golden/containment.py:421-431`) y `_concat_filtrado_por_columnas` (:509-528) pega 20 columnas de la correlativa al golden: quedan filas sin `PRIMARY_SOURCE`, `SOURCES_COUNT`, `CONFIDENCE_SCORE`, `CONFIANZA`, `REQUIRES_REVIEW` y `SRC` NaN en el 99,9 % del golden. Recalcular las métricas de esos grupos con `_process_batch_vectorized` sobre su subconjunto y restringir al esquema del golden | `golden/containment.py`, `golden/generator.py` | prueba con un caso sintético de fusión por NIT: golden sin NaN en métricas, sin columnas de la correlativa; invariante nueva en `_validate_output_invariants` |
| F1.2 | **Consolidación por NIT que falla en silencio (CRÍTICO).** `orchestrator.py:1804-1806` captura la excepción y sigue sin fusionar, con L5 en DONE. Debe fallar con mensaje accionable; si se decide reparar, dejar `meta.consolidacion_omitida` en el manifiesto | `pipeline/orchestrator.py` | prueba que fuerza la excepción y comprueba el `raise` o la constancia en `manifest.json` |
| F1.3 | **Muestreo que vacía los reportes (ALTO).** `reporting/suite.py:140-154`: `groupby("SRC").apply(sample)` descarta `SRC` NaN y, en pandas 3, quita la columna de agrupación; con golden > 30.000 filas la muestra queda vacía y se omiten `tarjeta_calidad_datos.png`, `heatmap_interseccion_mejorado.png` y `casos_problematicos_detallado.xlsx` con solo un WARNING. Cambiar a `groupby("SRC", dropna=False, group_keys=False).sample(frac, random_state=42)` con piso de 1 fila por grupo; `_validate_data` (:113-127) falla si un insumo no vacío produce muestra vacía o pierde columnas | `reporting/suite.py` | prueba con golden de 60.000 filas y `SRC` NaN: los tres artefactos se escriben |
| F1.4 | **Estrategias que fallan → `[]` (ALTO).** `reporting/strategies.py:231-237` convierte cualquier excepción en lista vacía; `reports.py:308-312` escribe un xlsx con una celda «Error»; `dashboard.py:369-372` escribe un PNG con el error; `orchestrator.py:1936-1942` salta estrategias. Nuevo contrato: lista de artefactos obligatorios por fase; si falta uno, la corrida falla; los opcionales omitidos van a `manifest.json → L6.omitidos[{artefacto, motivo}]`; nunca un archivo con un error dentro | `reporting/strategies.py`, `reports.py`, `dashboard.py`, `pipeline/orchestrator.py`, `pipeline/state_manager.py` | prueba: estrategia que lanza → corrida falla (obligatorio) o manifiesto con `omitidos` (opcional); verificación por nombre exacto de `golden_records.parquet` y `.csv.gz` (`orchestrator.py:1922-1933` hoy solo mira el prefijo) |
| F1.5 | **Reportes sobre muestras sin rótulo (ALTO).** `reports.py:112-128,243-249` calcula `analisis_fuentes` y `estadisticas_grupos` sobre 100.000 filas (a 139k: RUES 21.973 vs 30.550 reales; total 99.997 vs 139.028). Los agregados son `groupby` baratos: calcularlos sobre la tabla completa; si alguna vez se muestrea, rotular `MUESTRA` en el archivo y en `metrics` | `reporting/reports.py` | prueba: totales del reporte = N de la correlativa |
| F1.6 | **Tiempos inventados (MEDIO).** `dashboard.py:813-846`, `reports.py:981-991`, `visualizer.py:1496-1510` buscan `load_validate`, `scoring_time`… y el orquestador entrega `phase_times` con `L1_prep`… (`strategies.py:580-585`). Un solo mapeo fase → etiqueta en `_build_metrics`; eliminar los porcentajes fijos | `pipeline/orchestrator.py`, `reporting/*` | prueba: los tiempos del dashboard y de `reporte_metricas_performance.xlsx` = `manifest.json`; `performance_timeline.png` se genera |
| F1.7 | **Claves ausentes en el resumen (MEDIO).** `reports.py:331-475`: «Candidatos 0», «Pares evaluados 0», «Memoria 0.0 GB». Alimentar desde `candidates.db`, `scored.db` y `peak_rss_mib` del manifiesto | `pipeline/orchestrator.py:_build_metrics` | prueba: resumen = conteos reales |
| F1.8 | **Columnas de arrastre (CRÍTICO en `flujo.cruce`).** `flujo/cruce.py:1552-1567,1958` omite columnas del usuario si cambian las longitudes y el manifiesto declara la unión planificada. Fallar, o persistir `adjuntadas`/`omitidas` reales | `flujo/cruce.py` | prueba con columna de arrastre que no cuadra |
| F1.9 | **`ResultadoLinkage` ampliado (el contrato).** En `api.py`: campos `correlativa`, `golden`, `enlaces` (None si no aplica), `revision`, `diccionario`, `manifiesto`, `metricas`; método `validar()` que exige el esquema de la sección «Estándar» (`golden/columnas_finales.py` se extiende de 4 a las columnas fijas); `linkage()`, `dedupe()` y `link()` lo devuelven (hoy `linkage()` devuelve un `dict`) | `api.py`, `golden/columnas_finales.py`, `contrato.py` (nuevo: esquemas como `pyarrow.Schema`) | `tests/test_contrato_salida.py`: esquema, tipos (enteros, booleanos), orden de columnas, `ID_REGISTRO` único, correlativa con N filas, golden sin NaN en métricas |
| F1.10 | **Escritor único.** `exporters/escritor.py` (nuevo): `escribir_resultado(res, carpeta) -> Manifiesto` escribe la carpeta del estándar (parquet + `diccionario.csv` + `manifest.json` + `excel/` + `figuras/`), de forma atómica (pendiente → rename) y es el único punto con `to_parquet/to_excel` fuera de `_trabajo/`. L6 escribe a través de él. Los nombres de v1 (`L6_reporting/tabla_correlativa.*`, `golden_records.*`) se siguen escribiendo como alias dos versiones menores, con `DeprecationWarning` y hoja LEEME | `exporters/escritor.py`, `pipeline/orchestrator.py`, `reporting/strategies.py` | prueba: carpeta con la forma exacta del estándar; `manifest.json` lista cada artefacto con SHA-256 y tamaño; segunda ejecución idéntica |
| F1.11 | **Excel completo o LEEME.** Sustituir `_MUESTRA_100k` (`strategies.py:379-395`) por: Excel completo hasta 1.048.576 filas (xlsxwriter `constant_memory`), y si no cabe, `correlativa_LEEME.xlsx` con filas, columnas y cómo abrir el parquet | `reporting/strategies.py`, `exporters/escritor.py` | prueba con 1.100.000 filas sintéticas: no se escribe recorte; LEEME presente |
| F1.12 | **Diccionario y manifiesto únicos.** `diccionario.csv` generado desde `contrato.py` (incluye `alias_es`); `config_auditoria_*.json/.txt` se funde en `manifest.json` (versión del paquete real en vez de `orchestrator_version: "8.5"`, huellas de insumos, prioridad de fuentes real: hoy busca `source_quality_weights` y el perfil usa `source_priorities`) | `exporters/escritor.py`, `reporting/strategies.py:644-774` | prueba: todo campo del manifiesto presente y coherente con la corrida |
| F1.13 | **`api.py` degradaciones restantes.** `:530-535` L6 fuera de `_exec_phase` (no entra al manifiesto); `:192-196` colapso exacto que no factoriza sin aviso; `:832-833` centinela `-1`; `deduplication/auto.py:124,138` devuelve un DataFrame donde se espera `stats` | `api.py`, `deduplication/auto.py` | pruebas unitarias por caso |
| F1.14 | **Tipos y nombres.** `SOURCES_COUNT`, `RECORD_COUNT` enteros; `REQUIRES_REVIEW` booleano; `NAME_SIMILARITY_SCORE` comparando normalizado contra normalizado (C38) con ADR y cambio de huella declarado | `golden/generator.py`, `golden/columnas_finales.py` | banco: cambio solo en esa columna; prueba de tipos |
| F1.15 | Notebooks 01–06 y 03 (producción) leen el resultado con `ResultadoLinkage`/`leer_resultado`, sin `PipelineResult` | `notebooks/0[1-6]*.ipynb` | ejecución con nbclient sobre `tests/data_sintetica` sin errores |

**Compuerta F1:** banco con huella idéntica salvo F1.14 (declarada); conformidad 0 fallas; `test_contrato_salida` verde en `linkage()`, `dedupe()`, `link()`; a 139k y 463k sintéticos no falta ningún artefacto y el manifiesto no tiene `omitidos`; prueba de escala sin retroceso.

## F2 · Calidad y unificación de caminos (2 a 3 semanas)

Objetivo: que `linkage()` deje de encadenar empresas sin identificador, que la identidad sea estable entre corridas, y que todos los caminos escriban el mismo estándar. Lo medido hoy sostiene cada tarea (sección de hechos).

| ID | Tarea | Archivos | Prueba / criterio |
| --- | --- | --- | --- |
| F2.1 | **Cobertura por estrellas en `linkage()`.** Aplicar `engine.cobertura.cobertura_estrella` en L5, justo después del cannot-link por identificador (`orchestrator.py:1760-1770`), SOLO a grupos sin identificador válido, con `matching.nombre_idf.SimilitudNombre` sobre el IDF de `NOMBRE_LIMPIO` y los genéricos declarados. Perilla de perfil `cobertura_sin_identificador: {activa: True, umbral: 0.80, regla_lider: "cobertura"}`. El prototipo ya existe (script `bench_runner_estrellas.py` de esta sesión, adjunto): hay que llevarlo a la librería como regla escrita una vez (`engine/cobertura.py` + un único punto de aplicación reutilizado por `flujo.importadores`) | `pipeline/orchestrator.py`, `engine/cobertura.py`, `config/profiles.py`, ADR-0011 | banco con 0,80: macro-F1 0,880 (−0,012) y FP sobre negativos 246 (−14 %); sintético 139k: precisión 0,34 → 0,98, grupo mayor 630 → 13. Compuerta: FP sobre negativos ≤ 287 y grupo mayor sin identificador ≤ 50 registros a 139k; recall sin NIT del banco ≥ 0,62 |
| F2.2 | **Umbral medido, no adivinado.** Correr el barrido 0,70–0,88 con `scripts/banco.py --ajuste cobertura_umbral=` y con `scripts/escala.py`; elegir el umbral con la regla de Enrique (unir mal duele más) y documentar la curva en `docs/evidencia/cobertura_umbral.json` | `docs/evidencia/`, ADR-0011 | tabla con precisión, recall por régimen y grupo mayor por umbral |
| F2.3 | **`ID_GRUPO` determinista por contenido.** Reemplazar la posición mínima (`engine/clusterer.py:262`) por el menor `ID_REGISTRO` lexicográfico del grupo; mantener enteros vía `factorize` estable | `engine/clusterer.py`, `golden/generator.py` | prueba: reordenar las filas de entrada no cambia la partición ni los ids; huella del banco igual (la huella no depende del valor del id) |
| F2.4 | **`ID_ENTIDAD` estable.** Módulo `identidad/estable.py` (nuevo): `NIT-<base>` para grupos con identificador válido; `ENT-<ulid>` para el resto con herencia por mayor solapamiento desde `entidades_ids.parquet` de la corrida anterior (regla de la sección Estándar); crosswalk escrito en cada corrida; parámetro `corrida_anterior=` en `linkage()`/`ConfigCruce` | `identidad/estable.py`, `api.py`, `exporters/escritor.py`, ADR-0012 | pruebas: (a) misma entrada → mismos ids; (b) una fila nueva que se une a un grupo → el grupo conserva su id; (c) fusión → sobrevive el mayor y el retirado queda en el crosswalk; (d) división → conserva el id la parte con más registros compartidos |
| F2.5 | **`flujo.cruce` escribe el estándar.** `ejecutar_cruce` devuelve `ResultadoLinkage` y usa `escribir_resultado`; sus invariantes se mueven a `ResultadoLinkage.validar()`; modo disco conserva generaciones pero con los nombres del estándar | `flujo/cruce.py`, `flujo/resultados_disco.py` | `test_contrato_salida` verde en `flujo.cruce` en modo dataframe y disco; `test_flujo_cruce.py` adaptado |
| F2.6 | **`flujo.importadores` escribe el estándar.** Sus tablas `CORRELATIVA/GOLDEN/PAISES/REVISION` pasan a `correlativa/golden/revision` con `ID_GRUPO` + `ID_ENTIDAD` (`ID_IMPORTADOR` e `ID_EMPRESA_GLOBAL` quedan como columnas adicionales documentadas) | `flujo/importadores.py` | contrato verde; huella de importadores igual (ADR-0009) |
| F2.7 | **`dedupe()` y `link()`** devuelven el `ResultadoLinkage` ampliado y escriben con el escritor único (`dedupe` conserva sus perfiles por régimen) | `api.py` | contrato verde; `test_api_fachada`, `test_ejemplos_quickstart` verdes |
| F2.8 | **Retiros con evidencia (medido hoy):** `deduplicate_dataframe` (0 usos fuera de su módulo) y `deduplicate_large_dataset_colab` (nadie lo importa; chunking sin enlace entre chunks) se retiran ya; `OptimizationEngine` y `optuna_integration` (deprecados desde v3.2.7) y `GroundTruthGenerator` (2 pruebas humo) se retiran separando antes `GroundTruthEvaluator`. Entran en `CHANGELOG` como *Removed* | `deduplication/dataframe.py`, `deduplication/colab.py`, `optimization/`, `evaluation/ground_truth.py` | suite verde sin ellos; conteo de SLOC baja |
| F2.9 | **`RecordLinkagePipeline`: retirable tras migrar.** El `Orchestrator` solo lo usa como fábrica de `DataHandler`, `TextProcessor(cleaning_mode)` y `NitProcessor` (`orchestrator.py:280-283, 940-963`); 3 scripts (`generar_pares_para_etiquetar`, `medir_con_ground_truth`, `replicar_v2_8_0`) y 3 archivos de prueba lo usan. Crear `pipeline/componentes.py` con esa fábrica, migrar scripts y pruebas, marcar la clase `DeprecationWarning` una versión y retirarla en la siguiente | `pipeline/componentes.py` (nuevo), `pipeline/orchestrator.py`, `scripts/`, `tests/` | huella del banco igual tras la migración |
| F2.10 | **`deduplicate_unified` se conserva** mientras `dedupe()` sea la ruta por régimen (17 archivos de prueba, 8 scripts); se corrige su contrato roto (`auto.py:124,138,191-192`: devuelve DataFrame donde se espera `stats`) y sus salidas (`correlativa_unificada.parquet`, `conexiones_no_triviales.parquet`) pasan por el escritor único como `correlativa.parquet` + `conexiones.parquet` | `deduplication/unified.py`, `deduplication/auto.py` | `test_deduplicate_unified` y canario/baseline verdes |
| F2.11 | **`PipelineResult.to_excel/to_csv`** se extrae a `exporters/_spreadsheet.py` como función libre (6 notebooks lo usan como exportador); la carga perezosa muere con `RecordLinkagePipeline` | `pipeline/result.py`, `exporters/_spreadsheet.py` | notebooks 01–07 y 09 ejecutan con nbclient |
| F2.12 | **Una sola regla de `CONFIANZA`** (ALTA/MEDIA/BAJA) documentada en `diccionario.csv` y aplicada por golden, importadores y vinculación | `golden/`, `flujo/`, `contrato.py` | prueba de vocabulario |
| F2.13 | Conformidad sobre el camino de producción. Hoy los 43 casos corren solo por api.dedupe\_esquema (multicampo): nada de lo añadido entre 0.12 y 0.22 a linkage() tiene veredicto por caso. Añadir una segunda pasada de scripts/conformidad.py que ejecute los mismos casos por linkage() (y por flujo.cruce cuando haya columnas de arrastre), con veredicto por caso en docs/CONFORMIDAD.md para los 43 (hoy 37 explícitos; C09 y C21 solo pasan con --corroborar y eso debe quedar escrito como regla, no como flag olvidado) | evaluation/conformidad.py, scripts/conformidad.py, docs/CONFORMIDAD.md, docs/evidencia/ | 43/43 con veredicto explícito por los dos caminos; el JSON de evidencia registra camino y flag; test\_conformidad no se salta (0 skipped) |

**Compuerta F2:** banco con el cambio declarado de F2.1/F2.3 (macro-F1 ≥ 0,880, FP sobre negativos ≤ 287); conformidad 0 fallas; contrato verde en los 5 caminos (`linkage`, `dedupe`, `link`, `flujo.cruce`, `flujo.importadores`); sintético 139k con grupo mayor sin identificador ≤ 50; pruebas de identidad estable verdes.

## F3 · Vinculación dentro de la librería (2 a 3 semanas)

Objetivo: que el motor del notebook 10 (`vinculador_empresas.py`, 2.984 líneas, 60 funciones y clases, versión 1.0.0 en una celda `%%writefile`) viva en la librería con pruebas, y que los notebooks solo configuren. Es lo que la skill `record-linkage-empresas` ya describe como 0.23.0 y no existe.

| ID | Tarea | Archivos | Prueba / criterio |
| --- | --- | --- | --- |
| F3.1 | Extraer el motor a `flujo/vinculacion.py` sin cambiar comportamiento: `Fuente`, `ConfigVinculacion`, `Normalizador`, `Comparador`, `leer_fuente`, `preparar_fuente`, `deduplicar_ancla/destino`, `generar_candidatos`, `clasificar`, `evidencia_extra`, `aplicar_decisiones`, `propagar_por_web`, `validar_contra_referencia`, `ejecutar` → `vincular_empresas`; la exportación de 16 hojas a `flujo/vinculacion_excel.py` → `exportar_vinculacion_excel`. Partir el archivo por responsabilidad: `vinculacion/config.py`, `normalizar.py`, `comparar.py`, `candidatos.py`, `clasificar.py`, `decisiones.py`, `tablas.py` (ninguno > 600 líneas) | `src/record_linkage/flujo/vinculacion/` (nuevo paquete) | paridad: con los insumos del piloto (fuera del repo, en Drive) la huella de `ENLACES` es `9e3950f469ae0523` (la de la entrega del 5-oct); con empresas inventadas, 33+ pruebas unitarias que reproducen cada regla de la tabla del motor (EMPRESA A/B, GRUPO B, candidato C, web, truncamiento, siglas, decisiones, `(verificar)`) |
| F3.2 | Reutilizar lo que la librería ya tiene en vez de la copia del notebook: `paises.canonizar_pais` (hoy el motor trae su catálogo), `processing.saneamiento`, `matching.genericos`, `matching.locales.registrar_locale_compuesto`, `engine.cobertura` | `flujo/vinculacion/*` | huella de `ENLACES` igual; regla escrita una vez (prueba que el paquete `vinculacion` no define catálogos propios) |
| F3.3 | Salida al estándar: `vincular_empresas` devuelve `ResultadoLinkage` con `correlativa` (una fila por registro de la ancla, con `ID_GRUPO`/`ID_ENTIDAD`), `golden` (una por entidad ancla), `enlaces` (entidad ancla → registro destino con NIVEL, BANDA, PUNTAJE, MOTIVO, METODO\_UNION, CONFIANZA), `revision` y manifiesto; el Excel técnico de 16 hojas y el formato VPI son vistas generadas desde la carpeta | `flujo/vinculacion/tablas.py`, `exporters/vistas/tecnico.py`, `exporters/vistas/vpi.py` (desde `formato_vpi.py` de esta entrega) | contrato verde; el Excel VPI generado desde la carpeta es idéntico al de la entrega del 5-oct (huella BASE `73a122ed2414e27d` con las mismas decisiones) |
| F3.4 | Decisiones: un solo formato de archivo de decisiones (`COLUMNAS_DECISION`) leído por `vinculacion`, por `importadores` y por el formato VPI (`leer_pares_revision`); `AUTOR` con marca `(verificar)` para asistidas; el Excel VPI y el técnico sirven como archivo de decisiones | `flujo/decisiones.py` (nuevo, común) | prueba de ida y vuelta (la de esta entrega, `test_ida_y_vuelta.py`, llevada al repositorio con datos inventados) |
| F3.5 | Notebooks 10 y 11 delgados: celda 1 instala el tag, celda 2 importa de `record_linkage.flujo`, celda 3 configura; sin `%%writefile`; prueba `test_vinculacion_no_reimplementa_el_motor` (ningún notebook define `def clasificar` ni `class Comparador`) | `notebooks/10_*.ipynb`, `notebooks/11_*.ipynb`, `tests/test_notebooks_v014.py` | nbclient sobre `tests/data_sintetica/vinculacion/` sin errores |
| F3.6 | Notebook 09 genérico: reescrito sobre `flujo.vinculacion` (hoy su motor está en celdas sobre `flujo.importadores`) como «ejemplo real: segmentación × Orbis × fDi»; conserva sus decisiones como CSV de ejemplo con empresas inventadas | `notebooks/09_*.ipynb` | nbclient sin errores; reproduce A01 con los insumos reales fuera del repo (huella documentada en BITACORA) |
| F3.7 | ADR-0010 (vinculación en la librería), `docs/VINCULACION.md` (reglas del motor, bandas, decisiones), skill `record-linkage-empresas` alineada con la API real; versión 0.23.0 y tag | `docs/`, `CHANGELOG.md`, `pyproject.toml`, skill | `pip install "rues-linker @ git+…@v0.23.0"` e `import record_linkage.flujo.vinculacion` funcionan en un venv limpio (prueba de instalación del CI) |

**Compuerta F3:** paridad con el notebook 10 (huella `ENLACES`), contrato verde en `vincular_empresas`, notebooks 09–11 sin motor propio, tag 0.23.0 instalable, banco y conformidad intactos (la vinculación no toca L1…L5).

## F4 · Escala a 4 millones en 11 horas de Colab

Objetivo: que `linkage()` procese 4 M de registros en ≤ 11 h de Colab Free (12,7 GB de RAM, 2 vCPU, disco por FUSE) sin perder calidad, con cada cuello de botella medido antes de tocarlo (regla «perfile antes de optimizar» de `CLAUDE.md`). La restricción es un hecho declarado por Enrique (bases de 4 M); la preocupación «v2 pierde velocidad» se resuelve con las mediciones de abajo, no con opiniones.

**HECHO (medido hoy, v2 0.22.4, perfil `produccion_estandar`, 2 vCPU del contenedor, sin reportes, `bench_runner.py`):** 30.486 registros → 56 s (L2 42,2 · L3 6,9), 968.673 candidatos (32 por registro), RSS pico 470 MiB. 139.028 → 460 s (L2 269,1 · L3 167,1), 12.739.949 candidatos (92 por registro), RSS 1.137 MiB. 463.473 → 1.490,5 s (L1 6,0 · L2 704,5 · L3 725,6 · L4 5,4 · L5 45,0), 38.646.798 candidatos (83 por registro), 650.992 pares puntuados (1,7 %), RSS 2.413 MiB, 661 MB en disco (`candidates.db` 496 MB). Base real de importadores (Drive, metadatos de la corrida 2026-09-11 en Colab, `flujo.importadores` partido por país): 355.681 filas, 52.801.652 candidatos (148 por registro), matching 767 s, total 920 s; la partición USA sola trajo 24,5 M de candidatos y 11,4 GiB de pico (`CLAUDE.md`, 0.22.4).

**HECHO (cuello de botella con causa raíz):** dentro de la corrida de 463k el puntaje L3 se degrada de 111.774 pares/s al 0 % a 57.671 al 42 %, 44.102 al 69 % y 37.407 al 100 % (3×), con memoria plana (1.861–1.888 MB) y tasa de válidos uniforme (1,7 %): no es fuga ni densidad. La consulta de paginación de `_score_pairs_from_db_streaming` (`engine/scorer.py:1900-1906`) usa `WHERE idx_0 > ? OR (idx_0 = ? AND idx_1 > ?)`; `EXPLAIN QUERY PLAN` sobre el `candidates.db` real (SQLite 3.45.1) responde `SCAN candidate_pairs`: el `OR` impide usar la clave primaria y cada lote vuelve a recorrer la tabla desde el principio. Lectura de un lote de 75.000 pares medida por posición: 28 ms al inicio, 337 ms al 25 %, 666 ms al 50 %, 996 ms al 75 %, 1.154 ms al 95 %. Con comparación de valor-fila `WHERE (idx_0, idx_1) > (?, ?)` el plan es `SEARCH candidate_pairs USING PRIMARY KEY` y el lote tarda 31–43 ms en cualquier posición; un solo cursor `ORDER BY idx_0, idx_1` con `fetchmany` recorre los 38,6 M de pares en 12,1 s. v1 paginaba con `OFFSET` (mismo costo cuadrático); v2 quiso arreglarlo (comentario en `scorer.py:1915` que promete O(lote)) y ninguna prueba verifica el plan de consulta. El camino no-streaming (`scorer.py:1694`) y `exporters/smart.py` siguen con `OFFSET`.

**INFERENCIA:** ≈ 300 s (≈ 40 %) de los 725,6 s de L3 a 463k son escaneo puro; sin el arreglo, a 4 M (330–600 M de candidatos según 83 o 148 por registro, 4.400–8.000 lotes) el escaneo solo costaría entre 6 y 20 h: el plazo de 11 h es inalcanzable por este defecto antes que por el puntaje. Con el arreglo, el puntaje queda en ≈ 92.000 pares/s por vCPU-par, es decir 1,0–1,8 h a 4 M. L2 escala con exponente 0,80 (medido 139k → 463k): ≈ 1,1 h a 4 M. La RAM escala con exponente 0,62: ≈ 9 GiB a 4 M, al borde de Colab; el pico de 2,4 GiB a 463k no está localizado por fase (L5 reporta 1,8 → 1,9 GB; L3 es plano en 1,87 GB; el pico está en L2 o en L4) y hay que medirlo antes de decidir (F4.2). Todas estas cifras son proyecciones a confirmar con 1 M sintético y con la base real.

| ID | Tarea | Archivos | Prueba / criterio |
| --- | --- | --- | --- |
| F4.1 | Paginación de L3 en O(lote): sustituir el `OR` por valor-fila `(idx_0, idx_1) > (?, ?)` o por un único cursor con `fetchmany(batch_size)`; retirar los `OFFSET` que quedan (`scorer.py:1694`, `exporters/smart.py:510,565,646`). Banco antes y después (huella idéntica: el cambio no toca la decisión) | `engine/scorer.py`, `exporters/smart.py`, `tests/test_scorer_streaming.py` (nuevo) | prueba que ejecuta `EXPLAIN QUERY PLAN` sobre la consulta real y exige `SEARCH … USING PRIMARY KEY` (nunca `SCAN`); prueba de tiempo plano sobre 2 M de pares sintéticos: lectura del último lote ≤ 2× la del primero; 463k: L3 ≤ 450 s (antes 725,6) y tasa al 90 % ≥ 0,9 × tasa al 10 %; huella del banco 30k igual a `1e365ba81c4df45e…` |
| F4.2 | RSS por fase en el banco: `MuestreadorRecursos` ya existe en `evaluation/banco.py`; emitir pico por fase (L1…L6) en `manifest.json` y en el JSON de evidencia; localizar el pico de 2,4 GiB (463k) y el de 11,4 GiB de la partición USA real | `evaluation/banco.py`, `pipeline/orchestrator.py` (manifiesto) | `manifest.json` con `rss_pico_mib` por fase; informe en BITACORA con la fase culpable a 139k y 463k |
| F4.3 | Vectorizar la escritura de pares válidos en L3: el bucle `for k in range(len(valid_scores))` (`scorer.py:1955-1966`) construye una tupla por par con `int()`/`float()`; sustituir por `executemany` sobre `zip` de arrays nativos (`.tolist()` por columna) o `np.rec`; comprobar que `_score_batch_at_threshold` no convierte columnas del `df` completo en cada lote (regla «no convertir dentro del bucle», `CLAUDE.md` §5) | `engine/scorer.py` | `cProfile` de L3 a 139k antes y después; pares/s estable ≥ 100.000 en 2 vCPU; huella igual |
| F4.4 | L2 a escala: generar 1 M sintético con scripts/escala.py de F0.5 (`scripts/generar_ground_truth_grande.py`, semilla 42), perfilar `_index_chunk`, `_process_band_candidates`, `_generate_bucket_pairs`; tope por bucket para nombres genéricos DECLARADO y con aviso (nunca silencioso: cuántos pares se descartaron y de qué tokens); medir candidatos por registro contra los 148 de la base real | `engine/lsh/disk_based.py`, `engine/lsh/politica_pares.py`, `scripts/banco.py` | L2 1 M ≤ 1 h en 2 vCPU; proyección a 4 M ≤ 3 h; huella del banco igual; el aviso de tope aparece en el manifiesto |
| F4.5 | Presupuesto de memoria explícito y fail-fast: `ingestion/dimensionado` estima RSS esperado a partir de n, candidatos por registro y permutaciones (calibrado con los puntos 30k/139k/463k/1 M) y se detiene antes de L2 si supera el 80 % de la RAM disponible, con mensaje accionable (partir por país o régimen de NIT, bajar `lsh_permutations`, usar `scoring_batch_size` menor) | `ingestion/dimensionado.py`, `config/profiles.py` | prueba unitaria del estimador (error ≤ 30 % en los cuatro puntos medidos); 463k con RSS ≤ 2,0 GiB tras F4.2/F4.3 |
| F4.6 | Reanudación probada: L2 ya guarda bandas y L3 hace commit cada 500k pares; prueba que mata el proceso a mitad de L3 (y otra a mitad de L2) y reanuda con la misma huella; documentar la partición por país o régimen de NIT como opción para > 2 M (no obligatoria) | `pipeline/orchestrator.py`, `tests/test_reanudacion.py` (nuevo), `docs/ESCALA.md` (nuevo) | dos pruebas de reanudación verdes; huella idéntica a la corrida sin corte |
| F4.7 | Salidas a 4 M: `correlativa.xlsx` es imposible (> 1.048.576 filas) → `correlativa_LEEME.xlsx` + parquet (decisión Q5); `golden.xlsx` solo si cabe; L6 sobre muestras estratificadas por grupo, no sobre las primeras 100k filas (hoy `_MUESTRA_100k` parte 19.950 grupos) | `exporters/`, `reporting/` | prueba con 1,2 M de filas sintéticas: LEEME escrito, parquet completo, ningún grupo partido en la muestra |
| F4.8 | Corrida real de 4 M en Colab (Enrique, base real, fuera del repositorio): medir con el banco (`MuestreadorRecursos`), guardar `metadatos_corrida.json` y la evidencia sin nombres de empresas; si falla por RAM, aplicar la partición de F4.6 y repetir | Drive del proyecto; `docs/evidencia/` (solo cifras) | ≤ 11 h de pared y pico ≤ 11 GB; misma huella de calidad del banco 30k (F1 0,8780 · macro-F1 0,8920 o mejor) |

**Compuerta F4:** 1 M sintético ≤ 1,5 h y RSS ≤ 4 GiB en 2 vCPU con huella del banco idéntica; estimador de memoria probado; corrida real de 4 M ≤ 11 h y pico ≤ 11 GB en Colab (F4.8 queda como VACÍO hasta que se corra: qué = corrida real · quién = Enrique con Claude Code · cuándo = al cerrar F4.1–F4.7 · cómo = notebook 05 con el perfil `produccion_estandar` y `MuestreadorRecursos`).

## F5 · Ingeniería y estándar de la organización (3 a 4 semanas, en paralelo con F3/F4 donde no choque)

Objetivo: que lo que hoy empeora frente a v1 (SRP, ISP, tamaño, complejidad, errores tragados) quede mejor que en v1, medido con los conteos de F0.8 y con el estándar `python-data-library-dev`.

| ID | Tarea | Archivos | Prueba / criterio |
| --- | --- | --- | --- |
| F5.1 | **SRP del orquestador.** Partir `pipeline/orchestrator.py` (1.997 líneas, CC 45 en `_phase5`) en `pipeline/fases/l1_preparar.py` … `l6_reportar.py` (cada una una función pura `ejecutar(entrada, config) -> salida`), `pipeline/estado.py` (checkpoints y manifiesto) y un `Orchestrator` de ≤ 300 líneas que solo encadena fases. Escrituras a disco solo en `estado.py` y en el escritor único | `pipeline/` | huella del banco idéntica; ningún módulo nuevo > 600 líneas; CC ≤ 15 en código nuevo |
| F5.2 | **SRP de `flujo/cruce.py`** (2.010 líneas, `ConfigCruce.__post_init__` CC 45): separar `config.py`, `preflight.py`, `smoke.py`, `ejecutar.py`, y mover exportación al escritor único | `flujo/cruce/` | contrato verde; `test_flujo_cruce.py` verde; ningún módulo > 600 líneas |
| F5.3 | **SRP de `engine/scorer.py`** (`VectorizedScorer` 1.922 líneas, una función CC 62 de 489 líneas) y `engine/lsh/disk_based.py` (2.115 líneas, 46 métodos): extraer puntaje por variable, puertas IDF y E/S SQLite a módulos propios | `engine/` | huella idéntica; `scripts/escala.py` sin retroceso |
| F5.4 | **ISP: configuración tipada.** Reemplazar los perfiles `dict` de 30–39 claves (`config/profiles.py`, 9 claves muertas iguales en v1) por dataclasses anidadas por etapa (`ConfigBloqueo`, `ConfigPuntaje`, `ConfigClustering`, `ConfigGolden`, `ConfigReportes`) con `__post_init__` que valida y rechaza claves desconocidas; `linkage()` recibe un `ConfigLinkage` en vez de 15 parámetros (los 15 se mantienen como atajo que construye el objeto); `ConfigImportadores` (41 campos) y `SourceSpec` (25) se agrupan igual | `config/`, `api.py`, `flujo/` | `crear_config_orchestrator` sigue funcionando (compatibilidad); prueba que una clave desconocida falla con mensaje; 0 claves muertas |
| F5.5 | **LSP: contratos cumplidos.** `cluster_entities` y `find_candidates` devuelven \`dict | str`y`set | str`contra sus`Protocol`  ( `engine/clusterer.py:46/141` ,  `engine/lsh/candidates.py:22`vs`disk\_based.py:406` );  `TrustedSourceLSHEngine`fuerza`cross\_source\_only=True`. Separar interfaces de memoria y de disco (`Protocol\` distintos), devolver un handle tipado |
| F5.6 | **DIP.** `RecordLinkageEngine` busca componentes en `globals()` («ejecute la celda 3.2», `engine/linkage.py:67-145`); el núcleo imprime y lee Excel (`classifier/data_loader.py:39`). Inyectar dependencias por constructor; el núcleo no lee ni escribe archivos ni imprime | `engine/linkage.py`, `classifier/` | prueba que `engine/` y `matching/` no importan `openpyxl` ni usan `print`/`globals()` |
| F5.7 | **Excepciones.** Las 125 capturas que no relanzan: cada una pasa a excepción específica con `raise … from`, o a reparación con constancia en el manifiesto; `golden/utils.py:38` (inventa 50 %/4 GB de memoria) y los 10 `except:` desnudos primero | todo `src/` | `ruff` `BLE001`, `E722`, `S110` bloqueantes; conteo de deuda = 0 |
| F5.8 | **Logging.** Quitar el alias `safe_print as print` (16 módulos, 168 llamadas); `logging.getLogger(__name__)` con `NullHandler` en la librería; la configuración de handlers solo en notebooks y scripts; un solo formato de línea | `utils/output.py`, `utils/logger.py`, 19 módulos | `ruff` `T201` bloqueante; 0 `print` en `src/` |
| F5.9 | **Vectorización y rutas.** Reescribir los 11 `iterrows` (`matching/inferencia.py:263`, `evaluation/ground_truth.py:165-212` O(n²), `reporting/visualizer.py:1219`…), los 5 `apply` fila a fila y los 89 `os.path` (33 en `linkage_pipeline.py`, que se retira) | `src/` | `ruff` `PD`/`PTH` bloqueantes; prueba de equivalencia antes/después por función |
| F5.10 | **Tipado.** 113 errores de mypy a 0 con trinquete; `strict_optional = true` por módulo; `linkage(sources: Any)` → \`Mapping\[str, DataFrame | Path\]\` | `pyproject.toml`, `src/` |
| F5.11 | **Suite rápida.** Separar unitarias (≤ 10 min con `pytest-xdist`) de punta a punta (`slow`, Nightly); eliminar los 11 módulos al 0 % de cobertura o probarlos; cobertura `fail_under` de 60 a 75 | `tests/`, `ci.yml` | CI rápido ≤ 10 min; cobertura ≥ 75 % |
| F5.12 | **Pruebas de contrato de L6** (hoy nivel 1 del estándar): columnas, filas > 0, totales = N, archivos esperados por tamaño, sin hojas vacías | `tests/test_reportes_contrato.py` | verde a 30k y 139k sintéticos |
| F5.13 | **Determinismo en reportes.** `np.random.choice` sin semilla (`visualizer.py:259`, `dashboard.py:214`, `ground_truth.py:253`) → `random_state` desde la configuración | `reporting/` | dos corridas → PNG idénticos |
| F5.14 | **Docstrings y nombres.** Habilitar `ruff` `D` gradual; 118 funciones públicas sin docstring; traducir al español los módulos que se toquen (regla 6 del repo) | `src/` | interrogate ≥ 95 % en API pública |
| F5.15 | **Estructura de paquete según el estándar:** `__all__` en los 10 subpaquetes que no lo tienen; `__init__` raíz con la API pública (≤ 20 nombres: `linkage`, `dedupe`, `link`, `vincular_empresas`, `ResultadoLinkage`, `Config*`, `leer_resultado`, `escribir_resultado`) y el resto bajo `record_linkage.flujo`/`.engine` | `src/record_linkage/__init__.py` | prueba de API pública (snapshot de `__all__`) |

**Compuerta F5:** conteos de deuda en cero o por debajo de v1 en todas las métricas de la tabla de F0.8; mypy limpio; CI rápido ≤ 10 min; huella del banco idéntica en cada PR de refactor.

## F6 · Release 1.0.0 y mantenimiento (1 semana)

| ID | Tarea | Archivos | Criterio |
| --- | --- | --- | --- |
| F6.1 | `docs/CONTRATO_SALIDA.md`: el estándar (carpeta, tablas, columnas, tipos, reglas de Excel, `ID_ENTIDAD`, alias) con ejemplos; `docs/MIGRACION_v1.md`: tabla nombre viejo → nuevo, plazo de los alias, `leer_resultado` para carpetas de v1 | `docs/` | revisado por Enrique |
| F6.2 | `README.md` con un solo «cómo empezar» por flujo (deduplicar, cruzar, importadores, vincular) de ≤ 15 líneas cada uno, apuntando al estándar | `README.md` | los 4 ejemplos corren en `tests/test_ejemplos_readme.py` |
| F6.3 | `CHANGELOG.md` en formato Keep a Changelog: *Added / Changed / Removed / Fixed* por versión, con las huellas del banco de cada una | `CHANGELOG.md` | `scripts/verificar_coherencia_version.py` ampliado al CHANGELOG |
| F6.4 | Skill `record-linkage-empresas` y `CLAUDE.md` alineados con la API real (sin referencias a módulos inexistentes); `docs/PLAN.md` cerrado con lo hecho | skill, `CLAUDE.md`, `docs/` | prueba que cada import citado en la skill existe |
| F6.5 | Notebooks 01–11 como ejemplos genéricos con datos inventados en `tests/data_sintetica/`; prueba nbclient de todos en Nightly | `notebooks/`, `tests/` | 0 errores |
| F6.6 | Proceso de publicación: `release.yml` construye la rueda, instala en venv limpio, corre `smoke_installed_release.py` con la versión de `pyproject.toml`, publica el tag; Enrique aprueba el PR de versión | `.github/workflows/release.yml` | tag 1.0.0 publicado con CI verde |
| F6.7 | Mantenimiento: `deps/bump-mensual` con el banco como compuerta; `constraints/runtime-validado.txt` actualizado por versión | `.github/workflows/` | documentado en `CLAUDE.md` |

**Compuerta F6:** 1.0.0 instalable desde el tag en un venv limpio; banco, conformidad, contrato, escala y determinismo verdes en la rueda instalada, no solo en el código fuente.

## Matriz de pruebas y compuertas

| Prueba | Qué protege | Dónde corre | Bloquea |
| --- | --- | --- | --- |
| `ruff check` + `ruff format --check` (con `PTH`, `BLE001`, `E722`, `T201`, `C901≤15` en código tocado) | estilo, errores tragados, prints, rutas, complejidad | cada commit (pre-commit) y CI | sí |
| `mypy` anillo estricto (todo `src/` desde F5.10) | contratos de tipos | CI | sí |
| `pytest -m "not slow"` (≤ 10 min) | unidades, contrato de salida, invariantes, determinismo en proceso | CI por PR | sí |
| `test_contrato_salida` en los 5 caminos | esquema, tipos, archivos, manifiesto | CI por PR | sí |
| `test_reportes_contrato` | reportes L6 completos y coherentes | CI por PR | sí |
| `scripts/conformidad.py` (sin y con `--corroborar`) | 43 casos frontera | CI por PR (segundos) | sí: 0 firmes en FALLA |
| `scripts/banco.py --comparar` | F1, macro-F1, B³, FP sobre negativos, huella, tiempo, RAM | Nightly y antes de fusionar cambios de L1…L5 | sí: según ADR vigente |
| `scripts/escala.py --comparar` (139k, 463k; 1 M en F4) | tiempo por fase, candidatos, RSS, disco | Nightly y antes de fusionar cambios de L2/L3 | sí: sin retroceso > 10 % |
| `test_determinismo_contrato` (subprocesos con `PYTHONHASHSEED` distinto) | misma huella entre procesos | CI por PR | sí |
| nbclient sobre notebooks 01–11 con datos inventados | notebooks que corren | Nightly | sí |
| Prueba de instalación de la rueda en venv limpio + `smoke_installed_release.py` | paquete completo e importable | Release | sí |
| `scripts/deuda.py` (CC≥20, except sin relanzar, print, os.path, mypy) | trinquete: nada sube | CI por PR | sí |
| Paridad con el piloto (huella `ENLACES` del notebook 10 y huella BASE del formato VPI) | vinculación no cambia sin ADR | manual, antes de cada release, con insumos fuera del repo | sí (documentada en BITACORA) |
| Prueba de 4 millones en Colab (F4.8) | 11 horas y 12 GB | una vez por release mayor | sí para 1.0.0 |

## Riesgos, vacíos, preguntas y confianza

**Riesgos (con su mitigación en el plan).**

1. **Pruebas que consagran el defecto.** Varias pruebas actuales pasan contra la salida equivocada (golden con NaN, muestras vacías, resumen en cero). Al corregir F1 van a fallar y la tentación será ajustar la prueba. Regla: una prueba solo cambia con una línea en el ADR que diga qué comportamiento se declaró erróneo y por qué (F1.1–F1.8 llevan cada una su prueba nueva).
2. **Sobreajuste del umbral de estrellas.** 0,80 sale de un conjunto sintético; `CLAUDE.md` documenta cómo `fuentes_mixtas` ganó en sintético y perdió en institucional. Mitigación: F2.2 barre por pliegues partidos por grupo y la compuerta se mide en el banco institucional con macro-F1, nunca solo en el sintético.
3. **RAM a 4 M.** Proyección ≈ 9 GiB con exponente 0,62 sobre dos puntos; la partición USA real llegó a 11,4 GiB con 24,5 M de candidatos. Mitigación: F4.2 localiza el pico, F4.5 falla rápido con presupuesto explícito, F4.6 deja la partición como salida documentada.
4. **Consumidores del formato v1.** Enrique cree que existen procesos atados a `tabla_correlativa`/`golden_records` (Q1). Mitigación: alias durante dos versiones menores con `DeprecationWarning` y LEEME (F1.10); inventario de consumidores antes de retirarlos (vacío V6).
5. **Dos pandas.** Colab trae pandas 2.2 y el desarrollo corre en 3.0; el defecto del muestreo (F1.3) es exactamente una diferencia entre ambos. Mitigación: matriz de CI con las dos versiones desde F0.
6. **Datos licenciados.** Orbis y fDi no entran al repositorio; la paridad de F3 se verifica fuera y se deja solo la huella. Riesgo: una prueba «real» que alguien quiera meter al repo. Regla ya escrita en la skill y en F3.1.
7. **Alcance.** 73 tareas en 7 fases. El riesgo no es técnico sino de disciplina: saltarse una compuerta para avanzar. Por eso cada fase tiene una sola compuerta medible y el trinquete de F0 bloquea el merge sin banco.

**Vacíos abiertos (qué · quién · cuándo · cómo).**

| ID | Vacío | Qué hay que hacer | Quién | Cuándo | Cómo |
| --- | --- | --- | --- | --- | --- |
| V1 | `rues_linker-0.22.5-py3-none-any.whl` en `dist/` del Drive (2026-09-11): contenido sin verificar; podría traer cambios no fusionados en `main` | diff de la rueda contra `b45e335` | Claude Code | F0.1, antes de tocar código | descomprimir la rueda y `diff -r` contra `src/record_linkage`; si hay diferencias, decidir con Enrique qué entra |
| V2 | Notebook y Excel VPI originales de Caro: no están en el Drive de Enrique | confirmar que el formato VPI replicado es el vigente | Enrique (pedirlo a Caro) | antes de F3.3 | compartir en la carpeta del proyecto; comparar hoja por hoja con la entrega del 5-oct |
| V3 | Corrida real de 4 M | medir tiempo, RSS y calidad en Colab | Enrique con Claude Code | al cerrar F4.1–F4.7 | F4.8 |
| V4 | Pico de RSS por fase (2,4 GiB a 463k sin localizar) | instrumentar y medir | Claude Code | F4.2 | `MuestreadorRecursos` por fase |
| V5 | Candidatos por registro en la base real de 4 M (83 en sintético, 148 en importadores reales) | medir L2 sobre una muestra real de 500k | Enrique (datos) + Claude Code | F4.4 | correr solo L2 con `skip` de L3–L6 y leer `candidates.db` |
| V6 | Procesos aguas abajo que leen el formato v1 | inventario de consumidores | Enrique con los equipos | antes de retirar los alias (dos versiones tras F1.10) | lista de notebooks, scripts y consultas que abren `tabla_correlativa.*` y `golden_records.*` |
| V7 | Verdad de campo sobre datos reales (el banco es institucional, el grande es sintético) | muestra etiquetada de 500–1.000 pares reales | Enrique y equipo | durante F2 | `scripts/generar_pares_para_etiquetar.py` + decisiones humanas; entra al banco como estrato real (sin nombres en el repo si son licenciados) |
| V8 | Esfuerzo por fase | estimación | Claude Code | al abrir cada fase | medir las dos primeras tareas de cada fase y extrapolar; las semanas de los títulos de fase son órdenes de magnitud sin medir, no compromisos con terceros |

**Preguntas para Enrique (decisiones que el plan no toma solo).**

1. Orden F3 ↔ F4: el plan pone F3 antes porque no toca L1…L5 y desbloquea el trabajo con el formato VPI; si la base de 4 M llega antes, F4 pasa adelante. ¿Cuál de las dos llega primero?
2. Retiro de `deduplicate_dataframe`, `deduplicate_large_dataset_colab`, `OptimizationEngine`/optuna y `GroundTruthGenerator`: el plan asume `DeprecationWarning` en 0.23 y retiro en 0.24 (dependencias ya investigadas, sección 5 de hechos). ¿Confirma?
3. Umbral de estrellas: 0,80 (precisión, pierde 0,011 de F1 en el banco) o 0,70 (mejora los dos conjuntos pero deja grupos de 100 registros). F2.2 trae los números por estrato; la decisión es suya.
4. Alias en español: solo en `diccionario.csv` y `leer_resultado(alias_es=True)`, nunca en los archivos (Q3 dijo «opcional»). ¿Basta así?

**Nivel de confianza.** Alta (≥ 0,9) en lo medido y reproducido: la causa raíz de la paginación de L3 (plan de consulta y tiempos por posición verificados sobre el `candidates.db` real; referencia sobre comparación de valor-fila en SQLite: https://www.sqlite.org/rowvalue.html, consultado 2026-10-06), las degradaciones silenciosas (código leído y efecto reproducido), los resultados de la cobertura por estrellas (mismo evaluador, mismas corridas) y el inventario de dependencias (búsqueda exhaustiva en el código). Media (0,6–0,75) en las proyecciones a 4 M (dos puntos de ajuste, datos reales con el doble de candidatos por registro), en la proyección de RAM y en el umbral 0,80 (conjunto sintético). Media-baja (0,5) en que 4 M quepan en 11 h sin partición: depende de RAM, disco FUSE y densidad de candidatos, por eso F4.8 es compuerta y no promesa. Baja en el contenido de la rueda 0.22.5 (no verificada). Confianza global del plan como hoja de ruta ejecutable: 0,75.
