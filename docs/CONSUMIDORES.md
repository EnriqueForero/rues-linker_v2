# Inventario de consumidores de las salidas de rues-linker

> **Borrador para revisión de Enrique · 2026-10-06 · revisado contra v1 0.11.0
> (`c40dae5`).** Tarea F0.7 del plan de ejecución v2 → producción. Estado del
> repositorio medido: rama `claude/f0-fundaciones`, commit `33827cc`, versión
> `0.22.4`. La §3.8, las filas marcadas **(F1)** y las correcciones marcadas
> **(cruce v1)** se midieron después, sobre la rama `claude/f1-contrato`
> (commit `c7a6d84` más los cambios de F1 descritos en 3.8.iii, contrato de
> salida 1.0 ya aplicado: columnas técnicas fuera del entregable) y sobre la v1
> en `/home/user/rues-linker` (rama `claude/modest-davinci-4jh9os`, commit
> `c40dae5`, versión `0.11.0`, solo lectura). Los comandos de ese cruce están en
> el Anexo B.
>
> Este archivo es **la lista que decide qué nombres de archivo y de columna de
> v1 reciben alias en F1.10 y por cuánto tiempo**. Nada de lo que dice sale de
> memoria: cada fila lleva su evidencia (`archivo:línea` o `notebook · celda`)
> y cada cifra se midió con los comandos del anexo. Lo que el plan declara y el
> repositorio no muestra está marcado como tal; lo que nadie ha verificado está
> marcado **VACÍO** con qué · quién · cuándo · cómo.

## 1. Cómo se midió

- **Notebooks:** se leyó el JSON de los 11 archivos de `notebooks/` (numerados
  01–10; hay dos 06) y se buscaron, **solo en el `source` de las celdas**
  (no en las salidas ni en `metadata`), los patrones `tabla_correlativa`,
  `golden_records`, `correlative.parquet`, `golden.parquet`, `L6_reporting`,
  `PipelineResult`, `to_excel`, `to_csv`, `CORRELATIVA`, `GOLDEN`, `ENLACES`,
  `REVISION`, `leer_resultado`, `ejecutar_cruce`, `deduplicar_importadores`,
  `linkage(`, `dedupe(`, `link(`. El plan habla de «notebooks 01–11»: **no existe un
  notebook 11** en el repositorio.
- **Scripts, `src/`, `tests/`, `docs/`, `README.md`:** `grep -rn` con los mismos
  patrones, y lectura del contexto de cada coincidencia para separar un
  **lector real** (abre el archivo o usa la clave/columna) de una **mención**
  (docstring, comentario, nombre de variable, cadena de log). **El inventario
  se excluye a sí mismo de todos los conteos** (`--exclude=CONSUMIDORES.md`):
  este archivo nombra cada patrón que busca y, si contara, todo conteo que era
  0 dejaría de serlo por su sola existencia.
- **Skill `record-linkage-empresas`:** no está en el repositorio
  (`grep -rl vinculacion` sobre `src tests scripts notebooks docs README.md
  pyproject.toml`, excluido este inventario → 0 archivos). Se leyó la copia
  sincronizada fuera del repo
  (`~/.claude/skills/synced/…/record-linkage-empresas/SKILL.md`) y se trata
  como consumidor **declarado**, porque describe la API de 0.23.0 que F3 aún no
  ha construido.
- **Estados:** `verificado en repo` (hay código o prueba que lo lee) ·
  `declarado en el plan` (lo dice el plan y el repositorio no lo contradice ni lo
  confirma) · `VACÍO` (nadie lo ha comprobado; lleva qué · quién · cuándo · cómo).
- **Cruce contra v1 (§3.8, Anexo B):** sobre `/home/user/rues-linker` en
  `c40dae5` (0.11.0: `notebooks/` = 01, 02, 03, 04, 07; 27 scripts; sin
  `flujo/`, sin `salida/`), `grep -rnw --exclude-dir=__pycache__` sobre `scripts
  src tests docs README.md CHANGELOG.md .github` con los patrones de archivo de
  arriba más los de columna (`ID_GRUPO`, `NIT_FINAL`, `RAZON_SOCIAL_FINAL`,
  `CONFIANZA`, `REQUIRES_REVIEW`, `SRC`, `ORIGINAL_INDEX`, `NIT_BASE`,
  `NIT_VALID`, `NOMBRE_LIMPIO`, `NOMBRE_BLOQUEO`, `PHONETIC_KEY1`, `NIT_OK`,
  `RECORD_COUNT`, `REGIMEN_AUTO`, `matcher_stats`, `ID_REGISTRO`…), y el
  `source` de cada celda de los 5 notebooks volcado a texto numerado (`celda N
  Lx`). Se leyó el contexto de cada coincidencia para separar lector real de
  mención. No se ejecutó ningún notebook ni prueba de v1 y no se editó la v1.

## 2. Qué escribe hoy cada camino (los artefactos cuyo nombre está en juego)

| Camino | Artefacto que escribe | Nombres | Evidencia |
| --- | --- | --- | --- |
| `Orchestrator` L5 | parquet intermedios | `L5_golden/golden.parquet`, `L5_golden/correlative.parquet` | `pipeline/orchestrator.py:1714-1715` (escribe), `:1809-1810` (escribe), `:756-757` (vuelve a leerlos para reportar) |
| `Orchestrator` L6 (`DataExportStrategy`) | exportación contractual | `L6_reporting/golden_records.{parquet,csv.gz,xlsx}`, `L6_reporting/tabla_correlativa.{parquet,csv.gz,xlsx}`; si supera `excel_limit` = min(`export_settings.excel_max_rows`, `EXCEL_ROW_LIMIT` = 100.000) filas —nunca más de 100.000—, `<nombre>_MUESTRA_100k.xlsx` (el plan lo prohíbe: F1.11 lo sustituye por Excel completo o `correlativa_LEEME.xlsx`) | `reporting/strategies.py:69` (directorio), `:300-312` (nombres y patrones de checkpoint `golden.parquet` o `golden_records.parquet`, `correlative.parquet` o `correlativa.parquet`), `:415-453` (extensiones y muestra) |
| `Orchestrator` L6 (reportes) | 24 entregables de v1 | `dashboard_ejecutivo.png`, `dashboard_ejecutivo_mejorado.png`, `reporte_<nombre>.xlsx`, `config_auditoria.json` (alias de v1 desde F1.12: nombre estable con `vease: "manifest.json"`, opcional en `contrato_l6`; el `.txt` se retiró y el contenido vive en `manifest.json → parametros/tiempos_por_fase/metricas`), `visualizaciones/` | `reporting/{dashboard,visualizer,reports,suite,strategies}.py` (nombres construidos con `output_dir / f"…"`) |
| `api.linkage()` | dict en memoria | claves `"golden"`, `"correlative"` (+ `"matcher_stats"`, `"matcher_decisions"` con auditoría) | `api.py:330-337` |
| `api.dedupe()` / `api.link()` | `ResultadoLinkage` | atributos `correlativa`, `golden`, `metricas`, `manifiesto`, método `resumen()` | `api.py:549-576` |
| `flujo.cruce.ejecutar_cruce` | carpeta de corrida | `golden.parquet`, `correlativa.parquet`, `metadatos_corrida.json`, `golden.xlsx`/`correlativa.xlsx` (si `exportar_excel`); modo disco: `resultados.generations/<token>/`, `resultados.manifest.json` | `flujo/cruce.py:992-998`, `:1620`, `:1644`, `:1979-1986`; `flujo/resultados_disco.py:431-440` |
| `flujo.importadores.deduplicar_importadores` | `ResultadoImportadores` en memoria | tablas `CORRELATIVA`, `GOLDEN`, `PAISES`, `REVISION`, `MUESTRA_REVISION`, `METRICAS`, `INVARIANTES`, `PARAMETROS` | notebook 07 · celda 3 (`ResultadoImportadores(correlativa=tablas["CORRELATIVA"], golden=tablas["GOLDEN"], paises=…, revision=…)`); `docs/evidencia_importadores/` (7 CSV con esos nombres) |
| `PipelineResult.to_excel` / `.to_csv` | Excel multi-hoja o CSV por tabla | hojas/archivos con las claves del resultado: por defecto `golden_records`, `correlative_table`; con `extra=` las que pase el llamador | `pipeline/result.py:130`, `:231`, `:290` |
| `RecordLinkagePipeline.run` (heredado) | exportación propia | `tabla_correlativa.*`, `golden_records.*` vía `SmartExporter` | `pipeline/linkage_pipeline.py:867`, `:873`, `:1085-1086` |
| Notebook 10 (motor en celdas) | Excel de vinculación de 16 hojas | `VINCULACION_EXPORTADORES_ORBIS_FDI_<fecha>.xlsx`: `EXPORTADORES`, `ENLACES`, `REVISION`, `DECISIONES`, `CONTACTOS`, `DEDUPLICACION`, `VALIDACION`, `VALIDACION_DETALLE`, `COMPARACION_REVISIONES`, … ; CSV de decisiones con `COLUMNAS_DECISION` | notebook 10 · celda 4 L2193-2197, L2627, L829-864; celda 7 L139; celda 15 L5 |

## 3. Inventario de consumidores

Columnas: **consumidor · artefacto que lee (archivo o tabla) · columnas que
usa · evidencia · estado**. «Archivo» quiere decir que lo abre del disco por
nombre; «tabla» quiere decir que lo recibe en memoria y depende de la clave o
del atributo.

### 3.1 Notebooks (los 11 de `notebooks/`)

| Consumidor | Artefacto que lee | Columnas / claves que usa | Evidencia | Estado |
| --- | --- | --- | --- | --- |
| `01_deduplicar_una_base` | tabla: `dedupe()` → `ResultadoLinkage` | `resultado.correlativa` → `ID_GRUPO`, `RAZON_SOCIAL`; `resultado.metricas["n_registros", "n_grupos", "n_registros_con_nit", "n_registros_sin_nit"]`; `resultado.resumen()`. Exporta hojas `CORRELATIVA`, `DUPLICADOS`, `RESUMEN` a `deduplicacion.xlsx` con `PipelineResult.to_excel` | celda 2 L16, L113-114, L144-176 | verificado en repo |
| `02_cruzar_dos_bases` | tabla: `link()` → `ResultadoLinkage` | `r.correlativa` → `ID_GRUPO`, `NIT`, `RAZON_SOCIAL`, `SRC`; `r.golden`; `r.metricas["n_registros_a", "n_registros_b", "n_grupos", "n_grupos_cruzados", "n_pares_a_b"]`. Exporta `PARES_CRUZADOS`, `GOLDEN`, `CORRELATIVA`, `RESUMEN` a `cruce_<A>_<B>.xlsx` con `PipelineResult.to_excel` | celda 2 L17, L114-115, L163-167, L185-228 | verificado en repo |
| `03_produccion_multifuente` | tabla: `linkage()` → dict | `res["golden"]`, `res["correlative"]` → `ID_GRUPO`, `SRC`; `res["matcher_decisions"]`. Exporta `GOLDEN`, `CORRELATIVA`, `MATRIZ_FUENTES`, `RESUMEN`, `AUDITORIA_REFINADOR` a `consolidado_<etiqueta>.xlsx` con `PipelineResult.to_excel` | celda 2 L29, L126-127, L268-325, L349 | verificado en repo |
| `04_multicampo_y_evaluacion` | tabla: `evaluar_esquema()` (multicampo, no pasa por L6) | `res.decisiones`, `CLUSTER_ID`. Exporta `CLUSTERS`, `DECISIONES` a `multicampo.xlsx` con `PipelineResult.to_excel` (lo usa solo como exportador) | celda 2 L24, L121-122, L196-200; celda 6 L37-42 | verificado en repo |
| `05_general_cruce_configurable` | tabla: `ejecutar_cruce()` → `ResultadoCruce` | `resultado.correlativa` → `NIT`, `ID_GRUPO`, `SRC`; `resultado.cruce_por_fuente()`, `resultado.resumen()` | celda 2 L21; celda 3 L43; celda 4 L8-13 | verificado en repo |
| `06_ejemplo_rues_x_exportaciones` | archivo + tabla: `ejecutar_cruce()` deja `golden.parquet`, `correlativa.parquet`, `metadatos_corrida.json`; el notebook usa `resultado.rutas` | `resultado.correlativa`, `.metricas`, `.tiempos`, `.rutas`; métodos `cruce_por_fuente`, `conflictos_identificador`, `distribucion_grupos`, `entidades_multifuente`, `grupos_sospechosos`, `identidad_adoptada`, `identificadores_por_fuente`; `exportar_sin_pareja`, `pares_enlazados` → escribe `sin_pareja_en_<fuente>.parquet`, `entidades_cruzadas.parquet` | celda 4 L36; celda 13 L46; celda 15; celda 17 L5-26 | verificado en repo |
| `06_orquestador_configurable` | archivo + tabla: `ejecutar_cruce()` en modo disco | `resultado.golden`, `.correlativa`, `.metricas`, `.tiempos`, `.rutas` (incluida `rutas["generation_dir"]`, que copia a un snapshot persistente); métodos `cruce_por_fuente`, `entidades_ausentes_de`, `identidad_adoptada`, `matriz_presencia` | celda 8 L1; celda 10; celda 12 L35-48 | verificado en repo |
| `07_deduplicar_importadores_razon_social_pais` | tabla: `deduplicar_importadores()` → `ResultadoImportadores` | `correlativa` (`RAZON_SOCIAL_IMPORTADOR`, `PAIS_ESTANDAR`, `RAZON_SOCIAL_FINAL`, `PAIS_FINAL`), `golden` (`USD_FOB_TOTAL`, `NUM_REGISTROS`), `paises`, `revision`, `muestra` (`VEREDICTO_MANUAL`), `metricas`, `invariantes`. Escribe `<base>__<HOJA>.parquet` por tabla y `<base>.xlsx` con `PipelineResult.to_excel`; `CORRELATIVA` es obligatoria | celda 0 L113-120; celda 3 L25, L37, L489-496, L626-644; celda 11 L10; celda 15 L7-11, L31 | verificado en repo |
| `08_PUBLICAR_GITHUB` | ninguno: es el publicador. Lleva el texto del `README.md` (con los ejemplos `linkage(`, `ejecutar_cruce(`, `deduplicar_importadores(`) como residuo de estado de un widget, y la lista de notebooks que copia | — | celda 8 L181 (lista de notebooks); el texto del README está en `metadata.widgets` (estado residual de un `TextareaModel`, 31.147 caracteres, línea 4586 del JSON), **no** en el `source` ni en los `outputs` de ninguna celda (el notebook tiene 44 celdas y 0 con salidas) | verificado en repo (no consume salidas) |
| `09_vincular_segmentacion_orbis_fdi` | tabla: motor propio en celdas (reutiliza piezas de `flujo.importadores`); **no** llama a `deduplicar_importadores` (lo descarta explícitamente) | lee `revision_adjudicada_piloto.csv` con `COLUMNAS_REVISION = TIPO, FUENTE, CLAVE_A, NOMBRE_A, CLAVE_B, NOMBRE_B, DECISION, ORIGEN_REVISION, RAZON`; escribe hojas `RELACIONES_PROPIEDAD`, `EXCLUIDOS_REVISION`, `REVISION_PENDIENTE`, `DEDUP_PENDIENTE`, `PARAMETROS`, … a `<nombre>.xlsx` con `PipelineResult.to_excel`; columnas `N_ENLACES_ORBIS`, `N_ENLACES_FDI` | celda 0 L48; celda 4 L11; celda 6 L22, L416; celda 7 L14-16, L241, L364-368, L508; celda 21 L7 | verificado en repo |
| `10_vincular_exportadores_orbis_fdi` | archivo + tabla: motor propio en celda 4 (`vinculador_empresas.py`); lee 0..N archivos de decisiones (CSV con `COLUMNAS_DECISION`, o XLSX con hojas `DECISIONES`/`REVISION`) | produce `EXPORTADORES`, `ENLACES` (`NIVEL_FINAL`, `CONFIANZA`, `MOTIVO`, `ORIGEN`, huella sha256/16), `REVISION` (`ESTADO_REVISION`, `PRIORIDAD`, `AUTOR` con marca `(verificar)`), `DECISIONES`, `CONTACTOS`, `DEDUPLICACION`, `VALIDACION`, `VALIDACION_DETALLE`, `COMPARACION_REVISIONES`, `PARAMETROS`; el único `to_csv` es `DataFrame.to_csv` de la plantilla de decisiones, no `PipelineResult.to_csv` | celda 0 L7; celda 4 L829-864, L898, L1974-2058, L2193-2197, L2508, L2627; celda 7 L13-21, L139; celda 13 L7-8; celda 16 L3-7 | verificado en repo |

Conteos de esta sección (anexo A): `PipelineResult` importado en **6**
notebooks (01, 02, 03, 04, 07, 09); `.to_excel(` en esos mismos **6**;
`.to_csv(` en **1** (el 10, sobre un `DataFrame`); **0** notebooks abren
`tabla_correlativa`, `golden_records`, `L6_reporting` o `correlative.parquet`
por nombre; `golden.parquet` aparece en **1** (06, un comentario que describe
lo que `ejecutar_cruce` dejó); `ejecutar_cruce(` en **3** (05, 06, 06
orquestador); `deduplicar_importadores(` como llamada en **1** (07);
`linkage(` como llamada en **1** (03); `dedupe(` en **1** (01); `link(` en
**1** (02); `leer_resultado` en **0** (todavía no existe).

Pendiente que deja esta sección: **limpiar `metadata.widgets` del 08** (residuo
de 31 KB con el README completo) en la próxima tarea que toque notebooks. La
prueba `test_notebooks_v014` exige notebooks «sin salidas embebidas», pero mira
`outputs` y `execution_count` de las celdas, no `metadata`, y por eso no lo
detecta.

### 3.2 Scripts (`scripts/*.py`)

| Consumidor | Artefacto que lee | Columnas / claves que usa | Evidencia | Estado |
| --- | --- | --- | --- | --- |
| `benchmark_duckdb_flow.py`, `smoke_installed_release.py`, `verificar_rues_x_exportaciones.py` | tabla: `ejecutar_cruce()` (`ResultadoCruce` / `ResultadoCruceDisco`) | resultado en memoria y carpeta de corrida de `flujo.cruce` | `:290`, `:18,79`, `:33,104` | verificado en repo |
| `active_labeling.py`, `benchmark_e2e_matcher.py`, `verify_real_archives.py` | tabla: `linkage()` → dict | `"golden"`, `"correlative"` (+ auditoría del matcher en `benchmark_e2e_matcher`) | `:181`, `:112,138`, `:217` (`recalibrate_from_labels.py:135` solo imprime la llamada) | verificado en repo (medido en `33827cc`) |
| **(F1)** `benchmark_e2e_matcher.py`, `verify_real_archives.py` | tabla: `linkage()` → `ResultadoLinkage` (F1.9) | `.golden`, `.correlativa`, `.metricas["matcher_stats"]`, `.metricas["preprocessing"]`, `.metricas["ingestion_reports"]`, `.dir_trabajo`; ya no pasan por el shim `res["correlative"]` (`resultado.py:177-198`, `DeprecationWarning`) | `benchmark_e2e_matcher.py:154-156,182-186,206-207`; `verify_real_archives.py:239-243,265-267,286`; prueba `tests/test_scripts_tecnicas_contrato.py` con `-W error::DeprecationWarning` sobre los tres scripts | verificado en repo |
| **(F1)** `active_labeling.py` | tabla: `linkage(..., return_matcher_audit=True)` → `ResultadoLinkage` | `result.get("matcher_decisions")`, `result["matcher_stats"]` **por el shim** (funciona, avisa `DeprecationWarning`; las claves viejas desaparecen en 1.0) | `:181-200` | verificado en repo (pendiente de migrar a `.metricas[...]`) |
| **(F1)** `benchmark_e2e_matcher.py` | tabla: columna de la fuente arrastrada a la correlativa | **`ID_REGISTRO_FUENTE`**: el GT trae su propio `ID_REGISTRO`, que choca con la columna fija del contrato y el contrato conserva renombrada (`salida/completar.py:128`, `:316-334`; manifiesto `completar.renombres`). Hasta F1 el script cruzaba por `ID_REGISTRO` y con el contrato habría cruzado contra `<SRC>-F<fila>`: 0 pares en silencio | `:42`, `:69-88` (`pares_predichos`, falla claro si falta la columna), `:206-207`; prueba `tests/test_scripts_tecnicas_contrato.py:163-179` | verificado en repo |
| **(F1)** `verify_real_archives.py`, `verificar_rues_x_exportaciones.py` | **archivo** `<dir_trabajo>/L5_golden/correlative.parquet` (checkpoint de L5; `_trabajo/` del estándar) vía `salida.tecnicas.adjuntar_tecnicas` | `NIT_BASE` (ambos), `NIT_VALID` (el segundo), alineadas por `ORIGINAL_INDEX` o, si hubo colapso de duplicados exactos, por contenido `SRC`·`NIT`·`RAZON_SOCIAL` (ver 3.8.iii); el JSON de cada script registra origen y alineación | `verify_real_archives.py:37,141-145,239-243,265`; `verificar_rues_x_exportaciones.py:42-44,94-102,105-123,126-140`; `salida/tecnicas.py:68,72,229`; pruebas `tests/test_scripts_tecnicas_contrato.py:81-103,124-151` (extremo a extremo sobre ZIP inventados) | verificado en repo |
| `ejecutar_produccion.py`, `stress_test.py` (modo `orchestrator`) | tabla: `Orchestrator.run()` | resultado en memoria; escriben vía L6 | `:34,174,179`, `:47,59-60` | verificado en repo |
| `generar_pares_para_etiquetar.py`, `medir_con_ground_truth.py`, `replicar_v2_8_0.py` | tabla: `RecordLinkagePipeline(config, profile="deduplication_standard")` | `PipelineResult` y sus claves (`correlative_table`, `golden_records`) | `:51,71`, `:81,96`, `:44,105` | verificado en repo |
| `medir_baseline_v0_9_0.py`, `replicar_v2_5_0.py`, `replicar_v2_6_0.py`, `replicar_v2_7_0.py`, `replicar_v2_8_0.py`, `stress_test.py`, `verificar_determinismo.py` | tabla: `deduplicate_unified()` → `(correlativa, estadísticas)` | correlativa en memoria | `:50,232`, `:30,44`, `:33,61`, `:38,54`, `:41,181,190`, `:85,91`, `:29,64` | verificado en repo |
| Ningún script | archivo `tabla_correlativa.*`, `golden_records.*`, `L6_reporting/` | — | `grep -lE 'tabla_correlativa\|golden_records\|L6_reporting' scripts/*.py` → **0** | verificado en repo (sin consumidor) |

### 3.3 La propia librería leyendo por nombre (`src/`)

| Consumidor | Artefacto que lee | Columnas / claves que usa | Evidencia | Estado |
| --- | --- | --- | --- | --- |
| `Orchestrator._run_reporting` (compuerta contractual) | archivos `L6_reporting/golden_records*` y `tabla_correlativa*` | exige que la `DataExportStrategy` haya generado al menos un archivo con cada **prefijo**; si falta, `RuntimeError` | `pipeline/orchestrator.py:1922-1932` | verificado en repo |
| `DataExportStrategy` | archivos `L5_golden/golden.parquet`, `correlative.parquet` (o `golden_records.parquet`, `correlativa.parquet`) como checkpoint para exportar en streaming | todas las columnas | `reporting/strategies.py:300-312` | verificado en repo |
| `Orchestrator.generate_reports(results=None)` | archivos `L5_golden/golden.parquet`, `correlative.parquet` | todas | `pipeline/orchestrator.py:755-762` | verificado en repo |
| Reportes L6 (`reports.py`, `dashboard.py`, `visualizer.py`, `suite.py`) | tablas golden y correlativa en memoria (o ruta) | correlativa: `ID_GRUPO` (obligatoria), `SRC`; golden: `ID_GRUPO` (obligatoria), `CONFIDENCE_SCORE`, `PRIMARY_SOURCE`, `NAME_VARIATIONS`, `NIT_VARIATIONS` | `reports.py:100-108`, `:622-624`, `:694`; `dashboard.py:115-123`, `:515`, `:1314-1315`; `visualizer.py:105-113`, `:570-571`, `:1167`; `suite.py:117-125`, `:1437-1452` | verificado en repo |
| `SmartExporter.export_summary_report` | tabla golden en memoria | `CONFIDENCE_SCORE`, `SOURCES_COUNT`, `NIT_VARIATIONS`, `NAME_VARIATIONS` | `exporters/smart.py:475-495` | verificado en repo |
| `PipelineValidator._validate_output_files` (ruta heredada) | archivos `golden_records.*`, `tabla_correlativa.*`, `dashboard_ejecutivo.png` en `output_directory` | existencia con `.xlsx/.csv/.csv.zip/.parquet/.png` | `pipeline/validator.py:162-172` | verificado en repo |
| `RecordLinkagePipeline._phase6_export` (ruta heredada) | escribe y anuncia `tabla_correlativa.*`, `golden_records.*` | — | `pipeline/linkage_pipeline.py:867`, `:873`, `:1085-1086` | verificado en repo |
| `PipelineResult` | checkpoints parquet del pipeline heredado; claves `golden_records`, `correlative_table` | `to_excel`/`to_csv` escriben hojas y archivos con esos nombres | `pipeline/result.py:130`, `:231`, `:290` | verificado en repo |
| `flujo.cruce._verificar_invariantes` (ADR-0008) | tabla correlativa | `NIT_FINAL`, `RAZON_SOCIAL_FINAL` son entregables exigidos | `CLAUDE.md` §5 («Dar por garantizada una columna de salida…»), `docs/adr/0008-el-contrato-de-salida-de-la-correlativa.md` | verificado en repo |
| `evaluation/metrics.py`, `optimization/engine.py` | dict de resultados con claves `golden_records`, `correlative_table` | `CONFIDENCE_SCORE` | `metrics.py:122-138`; `engine.py:300-326`, `:374` | verificado en repo (`optimization/` es retirable en F2.8) |
| **(cruce v1)** `api.dedupe()` ← `deduplication/auto.py` | tabla correlativa de `deduplicate_auto` | **`REGIMEN_AUTO`** (`CON_NIT`/`SIN_NIT`): `auto.py` la escribe y `dedupe()` la lee para `metricas["n_registros_con_nit"/"n_registros_sin_nit"]`, que el notebook 01 imprime | v2 `api.py:889-890`, `auto.py:151,175,217-218`; v1 `api.py:403-404`, `auto.py:123,137,169-170` | verificado en repo (v1 y v2) |
| **(cruce v1)** reportes L6 `reporting/reports.py` | tabla golden | **`RECORD_COUNT`** (factor de tamaño del reporte de revisión y columna listada) | v2 `reports.py:873-874,918`; v1 `reports.py:724-725,768`; productor `golden/generator.py:621` (v1) | verificado en repo (v1 y v2) |
| **(cruce v1)** `deduplication/unified.py` (`deduplicate_unified`, motor de `dedupe()`) | tabla `result.get("correlative_table")` del `RecordLinkagePipeline` (marco **interno**, antes del contrato) | `RECORD_COUNT` si existe; **`NIT_OK` obligatoria**: `conexiones.sort_values(["ID_GRUPO","NIT_OK"])` para `conexiones_no_triviales.parquet` | v2 `unified.py:297-314,505-513`; v1 `unified.py:498-506` | verificado en repo; **no lo rompe F1.9**: ver 3.8.iii |
| **(cruce v1)** `pipeline/validator.py` (`PipelineValidator`, ruta heredada) | tablas en memoria (además de los archivos de la fila de arriba) | correlativa: `ID_GRUPO`, `SRC`; golden: `CONFIDENCE_SCORE`, `PRIMARY_SOURCE` | v2 `validator.py:86-92,105,120-132,146-156`; v1 `:84-104,119-131,145-155` | verificado en repo |
| **(cruce v1)** `deduplication/validator.py` (`DeduplicationValidator`) | dos correlativas en memoria | `ID_GRUPO`, `NIT_FINAL`, `RAZON_SOCIAL_FINAL` | v2 `:72-108`; v1 `:72-108`; **0 llamadores** en `src`, `tests`, `scripts` de los dos repositorios | verificado en repo (sin consumidor del validador) |
| **(cruce v1)** `evaluation/metrics.py` | correlativa unida al ground truth | `ID_GRUPO` + `NIT_FINAL_truth` | v2 `:327-333`; v1 `:327-333` | verificado en repo |

Nota: las ≈ 200 coincidencias restantes de `golden_records` en `src/` son
**nombres de variables y parámetros** (`golden_records_data`,
`self.golden_records`, `golden_records_time`), no lecturas de archivo; se
revisaron y se excluyeron de la tabla.

### 3.4 Pruebas (`tests/`)

| Consumidor | Artefacto que lee | Columnas / claves que usa | Evidencia | Estado |
| --- | --- | --- | --- | --- |
| `tests/integration/test_pipeline_result_v2_1.py` | `PipelineResult.to_excel` (hoja `golden_records`) y `.to_csv` (archivos `golden_records.csv`, `correlative_table.csv`) | existencia, conteo de filas | `:91-102`, `:113-121` | verificado en repo |
| `tests/integration/test_pipeline_result.py` | `PipelineResult.get("golden_records")`, `to_dict()` | claves | `:79`, `:138` | verificado en repo |
| `tests/test_flujo_cruce.py`, `tests/test_disk_results_v016.py`, `tests/test_notebook_orquestador_v016.py` | archivos `golden.parquet` de `flujo.cruce` y `resultados.generations/<token>/golden.parquet`; `metadatos_corrida.json` (3 pruebas); `resultados.generations` (3 pruebas) | existencia y contenido | `test_flujo_cruce.py:225,264`; `test_disk_results_v016.py:249`; `test_notebook_orquestador_v016.py:203` | verificado en repo |
| `tests/test_api_postprocessing.py` | directorio `Phase.L6_REPORTING` (lo redirige a `tmp_path / "reports"`) | — | `:211`, `:238`, `:261` | verificado en repo |
| `tests/test_disk_based_path.py` | `RecordLinkagePipeline(config, profile="deduplication_standard")` | `PipelineResult` | `:38`, `:62` | verificado en repo |
| 14 archivos de prueba que **importan o llaman** `deduplicate_unified` | `(correlativa, estadísticas)` | correlativa en memoria | anexo A (lista completa) | verificado en repo |
| `tests/test_colab_safe_cache.py:104` (monkeypatch del atributo), `tests/test_api_linkage.py:13` (exige que el nombre esté exportado en `record_linkage`) | el **nombre** `deduplicate_unified` | — | líneas citadas | verificado en repo |
| `tests/test_notebooks_v014.py` | el `source` de los notebooks 01–08 (celda de entorno, versión, sin `pip -e`, sin salidas embebidas en los que publica) | estructura de celdas | `:21-35`, `:188-215` | verificado en repo |
| **(cruce v1)** `tests/test_deduplicate_auto.py` | correlativa de `deduplicate_auto` | **`REGIMEN_AUTO`** ∈ {`CON_NIT`, `SIN_NIT`}; `ORIGINAL_INDEX` biyectiva con el índice de entrada; `ID_GRUPO` | v2 `:49,60`; v1 `:49,60,76,85-86,93-98,130-133` | verificado en repo |
| **(cruce v1)** `tests/test_matching_integration.py` | dict/`ResultadoLinkage` de `linkage()` | clave **`matcher_stats`** (`in`, `[]`; por el shim desde F1.9) | v2 y v1 `:58,70,74,200,222` | verificado en repo |
| **(cruce v1)** pruebas que ordenan la correlativa por **`ORIGINAL_INDEX`** y leen `ID_GRUPO` | correlativa en memoria | `ORIGINAL_INDEX`, `ID_GRUPO` | v1: **14 archivos** (`grep -rlw`, Anexo B): `test_determinismo_contrato.py:42-68`, `test_disk_based_path.py:78-111`, `test_api_fachada.py:53-54`, `test_quality_golden.py:81-88`, `test_sin_nit_recalibrado.py:56-90`, `test_canario_percolacion.py:56-62`, `test_quality_exhaustivo.py:60-62`, `test_quality_extra_features.py:96-97`, `test_extra_features_integration.py:50-51`, `test_calidad_ground_truth_grande.py:62-63`, `test_quality_sintetico_robusto.py:77-146`, `test_baseline_v0_9_0.py:101-119`, `test_deduplicate_auto.py`, `test_ejemplos_quickstart.py:39`; v2: **32 archivos** (incluidos los dos de F1 de esta fila y `test_salida_tecnicas.py`, que alinean por ella) | verificado en repo |
| Ninguna prueba | archivos `L6_reporting/golden_records*` o `tabla_correlativa*` leídos por nombre; `reporte_*.xlsx`; `config_auditoria.json` (alias de v1; solo lo leen las pruebas de F1.12 que verifican el alias, no un consumidor); `_MUESTRA_<n>k.xlsx` | — | `grep` → **0** en los cuatro casos (anexo A; F1.12 añadió pruebas del alias `config_auditoria.json`) | verificado en repo (sin consumidor) |

### 3.5 Documentación (`docs/*.md`, `README.md`)

| Consumidor | Artefacto que lee | Columnas / claves que usa | Evidencia | Estado |
| --- | --- | --- | --- | --- |
| `README.md` (ejemplo publicado para usuarios) | enseña a guardar `result["golden"]` y `result["correlative"]` como `golden_records.parquet` y `tabla_correlativa.parquet` | claves `golden`, `correlative` | `README.md:452-453` | verificado en repo: es el patrón que cualquier usuario externo pudo copiar |
| `docs/*.md`, `docs/adr/*.md` | ninguno salvo este inventario menciona `tabla_correlativa`, `golden_records` ni `L6_reporting` | — | `grep` → **0** (excluido `CONSUMIDORES.md`, anexo A) | verificado en repo |
| `notebooks/README_notebooks.md` | describe las exportaciones `GOLDEN/CORRELATIVA/MATRIZ/RESUMEN` de los notebooks 01–04 | hojas | `:92` | verificado en repo |
| `docs/evidencia_importadores/` | CSV `INVARIANTES`, `METRICAS`, `MUESTRA_REVISION`, `PAISES`, `PARAMETROS`, `RECALL_BLOQUEO`, `SENSIBILIDAD` (7 archivos: la forma de las tablas de `flujo.importadores`) | — | `ls docs/evidencia_importadores` | verificado en repo |

### 3.6 Consumidores externos declarados en el plan (y la skill)

| Consumidor | Artefacto que lee | Columnas que usa | Evidencia | Estado |
| --- | --- | --- | --- | --- |
| **Formato VPI de Caro** | vista derivada de `ENLACES` (del Excel de vinculación del notebook 10), entregada como Excel | `BASE` del VPI se deriva de `ENLACES` (plan, «El estándar de salida definitivo», tabla *Enlaces*); huella `BASE` de la entrega del 5-oct: `73a122ed2414e27d` (plan F3.3) | plan: «Hechos nuevos» 6 (*«El notebook VPI de Caro no está en ese Drive (VACÍO)»*), vacío V2, F3.3; repositorio: `grep -rlw VPI` → **0**, `grep -rlw Caro` → **0** | **VACÍO** · **qué:** confirmar que el formato VPI replicado en la entrega del 5-oct es el vigente (hoja por hoja, columnas y orden) · **quién:** Enrique se lo pide a Caro · **cuándo:** antes de F3.3 · **cómo:** Caro comparte el notebook y el Excel originales en la carpeta del proyecto; se comparan hoja por hoja con la entrega del 5-oct y se fija la huella `BASE` como prueba de paridad |
| **Snowflake** | el plan lo declara como consumidor de las salidas (F0.7: «quién lee qué archivo (VPI, Caro, Snowflake, notebooks 01–11)») | ninguna identificada | repositorio: Snowflake aparece **solo como insumo** — credenciales de lectura (`config/credentials.py`, `config/settings.py:33-34`, `config/__init__.py`), la vista `snowflake_v2` como fuente del notebook 07 (celda 5 L21-35) y `matching/normalizadores.py:56`; **0** archivos con carga/escritura hacia Snowflake (anexo A) | **VACÍO** · **qué:** saber si alguna tabla de Snowflake (o un proceso que la alimente) carga `tabla_correlativa.*`, `golden_records.*` o el Excel de importadores, y con qué columnas · **quién:** Enrique con el equipo de datos (dueños de `snowflake_v2`) · **cuándo:** antes de retirar los alias (dos versiones menores después de F1.10) · **cómo:** listar las cargas/`COPY INTO`/`PUT` y los notebooks que escriben a Snowflake; si existen, añadirlos a esta tabla con las columnas que leen |
| **Equipos aguas abajo (V6 del plan)** | «notebooks, scripts y consultas que abren `tabla_correlativa.*` y `golden_records.*`» fuera del repositorio | desconocidas | plan: riesgo 4 (Q1: Enrique cree que existen) y vacío V6; repositorio: **0** lectores por nombre fuera de la propia librería (3.2–3.4) | **VACÍO** · **qué:** inventario de los procesos externos · **quién:** Enrique con los equipos · **cuándo:** antes de retirar los alias · **cómo:** cada equipo responde con la lista de archivos que abre y las columnas que usa; se agregan aquí como filas con estado `verificado` |
| **Skill `record-linkage-empresas`** (fuera del repo) | API de 0.23.0: `record_linkage.flujo.vinculacion` (`vincular_empresas`, `exportar_vinculacion_excel`, `leer_decisiones`, `plantilla_decisiones`), Excel de 16 hojas (`LEEME`, `RESUMEN`, `EXPORTADORES`, `ENLACES`, `REVISION`, `DECISIONES`, `CONTACTOS`, `DETALLE_<fuente>`, `DEDUPLICACION`, `VALIDACION`, `CALIDAD_DATOS`, `CONTROL_QA`, `PARAMETROS`, `METODOLOGIA`), CSV de decisiones | columnas de `EXPORTADORES` (`RESULTADO`, `NIVEL_VINCULO`, `CONFIANZA`, `REQUIERE_REVISION`, `QUE_REVISAR`, bloque `<DESTINO>_*`), `ENLACES` (`MOTIVO`, evidencia, `ORIGEN`), `REVISION` (`DECISION`, `AUTOR`) | `SKILL.md:6`, `:12`, `:26-36`, `:84`, `:184-185`, `:236-242`; plan «Hechos nuevos» 6: esa API **no existe** en `33827cc` (`grep -rl vinculacion` → 0) | declarado en el plan (es el objetivo de F3, no el estado actual) |
| **`RecordLinkagePipeline`** | fábrica de componentes del `Orchestrator` + ruta heredada | ver 3.7 | ver 3.7 | verificado en repo |
| **`deduplicate_unified`** | ver 3.7 | ver 3.7 | ver 3.7 | verificado en repo |
| **`PipelineResult.to_excel` / `to_csv`** | ver 3.7 | ver 3.7 | ver 3.7 | verificado en repo |

### 3.7 Dependencias de los caminos paralelos (lo que el plan afirma, medido)

| Dependencia | Lo que dice el plan («Hechos nuevos» 5) | Lo medido en `33827cc` | Diferencia |
| --- | --- | --- | --- |
| `RecordLinkagePipeline` en el `Orchestrator` | «solo como fábrica de componentes (`orchestrator.py:280-283`, `940-963`)» | import en `:51`; instancia perezosa en `:280-282`; **5** usos de `self.pipeline.` en `:940` (`data_handler.load_sources`), `:947` (`data_handler.consolidate_sources`), `:952` (`text_processor.process_series`), `:957` (`text_processor.derivar_nombre_bloqueo`), `:963` (`nit_processor.process_series`) | coincide |
| `RecordLinkagePipeline` en scripts | 3 scripts | **3**: `generar_pares_para_etiquetar.py:71`, `medir_con_ground_truth.py:96`, `replicar_v2_8_0.py:105` | coincide |
| `RecordLinkagePipeline` en pruebas | 3 archivos de prueba | **3 lo mencionan**, pero solo **1 lo instancia**: `tests/test_disk_based_path.py:62`. `tests/test_reproduce_bugs.py:11,113` y `tests/integration/test_pipeline_result.py:26` lo nombran en docstrings | el plan cuenta menciones; la dependencia real es 1 prueba |
| `RecordLinkagePipeline` en otros módulos de `src/` | no los enumera | **`deduplication/unified.py:25,285` lo instancia y ejecuta `run()`** (es el motor de `deduplicate_unified`); `optimization/engine.py:283-286` lo instancia (retirable en F2.8); `evaluation/banco.py:564` solo silencia su logger; `optimization/engine.py:33` es un comentario; `optimization/parameters.py:148` y `pipeline/result.py:4,140` son docstrings (es todo lo que `grep -rn RecordLinkagePipeline src` devuelve fuera de `orchestrator.py` y `linkage_pipeline.py`) | **hallazgo:** `RecordLinkagePipeline` no es retirable mientras `deduplicate_unified` (que F2.10 conserva) lo use como motor. F2.9 debe migrar también `unified.py:285`, no solo la fábrica del `Orchestrator` |
| `deduplicate_unified` en pruebas | 17 archivos de prueba | **18** archivos lo mencionan; **14** lo importan o llaman; **2** más dependen del nombre (`test_colab_safe_cache.py:104` monkeypatch, `test_api_linkage.py:13` exportación pública); **2** solo en docstring (`integration/conftest.py:4`, `test_deduplicate_auto.py:4,116`) | 16 dependen de verdad (el plan dice 17; la diferencia es cómo se cuentan las menciones en docstrings) |
| `deduplicate_unified` en scripts | 8 scripts | **8** lo mencionan; **7** lo llaman; `medir_con_ground_truth.py:71` dice en su docstring que **no** lo usa | 7 dependen de verdad |
| `deduplicate_unified` en `src/` | no lo enumera | lo llaman `deduplication/auto.py:114,128,143,152` (y por él `api.dedupe()`, `api.py:709-721`), `deduplication/colab.py:527,618`, `deduplication/dataframe.py:107,199`, `deduplication/validator.py:39`; lo exporta `__init__.py:43,147` | `api.dedupe()` → `deduplicate_auto` → `deduplicate_unified` → `RecordLinkagePipeline`: los notebooks 01 y 02 dependen, por transitividad, de los dos |
| `PipelineResult.to_excel`/`to_csv` | 6 notebooks | **6** notebooks importan `PipelineResult` y llaman `.to_excel(`: 01, 02, 03, 04, 07, 09; **0** notebooks llaman `PipelineResult.to_csv` (el 10 usa `DataFrame.to_csv`); pruebas: `integration/test_pipeline_result_v2_1.py` (`to_excel` y `to_csv`), `integration/test_pipeline_result.py` | coincide; añade que ningún notebook usa `to_csv` |

### 3.8 Cruce contra v1 0.11.0 (`c40dae5`): lo que v1 añade o corrige

Hecho que condiciona todo lo que sigue: **la v1 no tiene `flujo/`, `salida/`,
`golden/metricas.py`, ni notebooks 05, 06, 08, 09 o 10**; sus consumidores de
salida son los notebooks 01–04 y 07, 27 scripts, la librería y las pruebas.
Nada de lo que v1 consume contradice la decisión (a); lo que sigue la completa.

#### (i) Consumidores que este inventario no tenía

| # | Nombre | Quién lo consume | En v2 (`c7a6d84`) | Dónde quedó registrado |
| --- | --- | --- | --- | --- |
| 1 | **`REGIMEN_AUTO`** (correlativa de `dedupe()`) | v1 `api.py:403-404` (métricas del notebook 01, celda 2 L167-168); `auto.py:123,137,169-170`; `tests/test_deduplicate_auto.py:49,60,76,85-86` | `api.py:889-890`; `auto.py:151,175,217-218`; `test_deduplicate_auto.py:49,60`; el contrato la deja como columna extra del motor y la explica en el diccionario (`contrato.py` `_SIGNIFICADOS_MOTOR_EXTRA`) | 3.3, 3.4, (a).12 |
| 2 | **`matcher_stats`** (clave del dict de `linkage()`) | v1 `benchmark_e2e_matcher.py:156-157,245`; `active_labeling.py:200`; `tests/test_matching_integration.py:58,70,74,200,222`; `api.py:201` | mismos scripts y prueba; desde F1.9 vive en `ResultadoLinkage.metricas["matcher_stats"]` y el shim la sirve con aviso (`resultado.py:57-65,177-198`) | 3.2, 3.4, (a).13 |
| 3 | **`RECORD_COUNT`** (golden; opcional en la correlativa) | v1 `reports.py:724-725,768`; `unified.py:498-503`; productor `golden/generator.py:621` | `reports.py:873-874,918`; `unified.py:505-510`; columna fija del golden en `contrato.py` (`COLUMNAS_METRICAS_GOLDEN`) | 3.3, (a).14 |
| 4 | **`ID_REGISTRO` de entrada** y, en general, las columnas de la fuente arrastradas a la correlativa | v1 `benchmark_e2e_matcher.py:176,184`: `result["correlative"][["ID_REGISTRO","ID_GRUPO"]]` (la columna viene del GT, no la crea la librería) | F1.9 escribe su propio `ID_REGISTRO` y conserva el de la fuente como **`ID_REGISTRO_FUENTE`** (`salida/completar.py:128,316-334`; manifiesto `completar.renombres`; `tests/test_contrato_salida.py:338-343`). El script quedó corregido en F1: `benchmark_e2e_matcher.py:42,69-88,206-207` | 3.2, (a).15 |
| 5 | **Nombres del Excel de importadores de v1** (lo que recibieron terceros) | notebook 07 v1: archivos `deduplicacion_importadores.xlsx`, `deduplicacion_importadores__<HOJA>.csv.gz`, `deduplicacion_importadores__CORRELATIVA.parquet` (celda 10 L11, L23-33, L40-53); **10 tablas**: las 8 de §2 más `SENSIBILIDAD` y `RECALL_BLOQUEO` (celda 7 L601-637, L749-751); columnas `PAIS_ISO3`, `PAIS_METODO`, `SIM_AL_FINAL`, `CAMBIO_NOMBRE`, `CAMBIO_PAIS`, `N_VARIANTES_NOMBRE`, `SIM_MINIMA`, `MOTIVO_REVISION`, `N_FILAS`, `BANDA`, `POBLACION_BANDA`; en 0.11.0 no existe `flujo.importadores`: el 07 lleva el motor en la celda 7 | `docs/evidencia_importadores/` de v2 tiene **las mismas 7 cabeceras** que el de v1 (incluye `RECALL_BLOQUEO.csv`, `SENSIBILIDAD.csv`); `flujo/importadores.py` produce las tablas de §2 | 3.5, (a).16 |

Consumidores no registrados de nombres que (a) **ya** conserva (completan la
evidencia; no cambian la decisión):

1. **`ORIGINAL_INDEX`**: (a).8 lo citaba solo por `flujo/cruce.py:521,:1283`. En
   v1 lo leen **7 scripts** (`medir_baseline_v0_9_0.py:73-94,240`;
   `medir_con_ground_truth.py:109,127,307`; `replicar_v2_5_0.py:53,68,72`;
   `replicar_v2_6_0.py:70,89,91,123,125,171`; `replicar_v2_7_0.py:64-65`;
   `replicar_v2_8_0.py:119-120,203-204`; `verificar_determinismo.py:4,73-74`) y
   **14 pruebas** (3.4); lo crean `processing/utilities.py:198` y
   `golden/generator.py:256-258`. En v2 lo leen además `scripts/
   verify_real_archives.py:118-126,249-253`, `scripts/benchmark_duckdb_flow.py:154`,
   `scripts/escala.py` y, desde F1, `salida/tecnicas.py` (alineación).
2. **`ID_GRUPO`, `SRC`, `CONFIDENCE_SCORE`, `PRIMARY_SOURCE`** en
   `pipeline/validator.py` (3.3 citaba al validador solo por los archivos).
3. **`ID_GRUPO`, `NIT_FINAL`, `RAZON_SOCIAL_FINAL`** en
   `deduplication/validator.py:72-108` (0 llamadores en ambos repositorios).
4. **`ID_GRUPO` + `NIT_FINAL_truth`** en `evaluation/metrics.py:327-333`.
5. **Notebooks 01–04 de v1** escriben con `pd.ExcelWriter` + `DataFrame.to_excel`
   propios (celda 2 L107-109), **no** con `PipelineResult.to_excel` (3.1 lo
   atribuye a los 01–04 de v2, donde `exportar_resultados` sí lo envuelve). Los
   nombres de archivo y hoja coinciden (`deduplicacion.xlsx`, `cruce_<A>_<B>.xlsx`,
   `consolidado_<etiqueta>.xlsx`, `multicampo.xlsx`), pero v1 añade el respaldo
   **`<base>__<HOJA>.csv.gz`** cuando una tabla supera 1 M de filas (celda 2
   L113-114) y el 03 escribe **`manifiesto_corrida.json`** (celda 2 L267):
   ninguno estaba registrado.

Archivos que la librería escribe y nadie lee (0 lectores en v1 y en v2; quedan
como residuos en carpetas del usuario):

| Archivo | Quién lo escribe | Observación |
| --- | --- | --- |
| `correlativa_unificada.parquet`, `conexiones_no_triviales.parquet` | `deduplication/unified.py:312-314` (v1 `:308-311`); los deja **cada** `dedupe()` en `output_dir` (notebook 01: `<carpeta_salida>/trabajo/`) | es donde quedan las técnicas de la ruta `dedupe()` (3.8.iii) |
| `checkpoint_01…05_*.parquet` | `pipeline/linkage_pipeline.py:252-263` (v1 `:242-253`), ruta heredada | muere con `RecordLinkagePipeline` (F2.9) |
| `experiments/<name>_<ts>/{golden.parquet, correlative.parquet, experiment.json}` | `pipeline/orchestrator.py:2252-2260` (v1 `:1686-1703`) | — |
| `casos_problematicos_detallado.xlsx`, `heatmap_interseccion_mejorado.png`, `tarjeta_calidad_datos.png`, 6 PNG del visualizer | `reporting/suite.py:306-324,438,763,1516`; `visualizer.py:374-404` (v1 `suite.py:361,688,1471`; `visualizer.py:384-414`) | parte de los «24 entregables» que §2 agrupa sin nombrar |
| `manifest.json` de `pipeline/state_manager.py:56` (v1 `:49`) | estado del pipeline en `work_dir` | **el nombre ya existe en v1 con ese significado**; F1.12 usa el mismo nombre para el manifiesto del entregable: dos archivos distintos con el mismo nombre en carpetas distintas |

#### (ii) Nombres que (a) conserva y ningún consumidor usa

| # | Nombre | Medición v1 | Medición v2 | Consecuencia |
| --- | --- | --- | --- | --- |
| 1 | **`CONFIANZA`** (golden) | **0 lectores**: solo el productor `golden/generator.py:175,646,841`; ni `reports.py`, ni `dashboard.py`, ni `visualizer.py`, ni `smart.py`, ni notebooks, ni scripts, ni pruebas la indexan (`grep -rnw CONFIANZA src tests scripts \| grep -v golden/generator.py` → 0) | fuera del contrato de F1 (`contrato.py`, `salida/completar.py:118`, `resultado.py`, `golden/metricas.py`) y de sus pruebas (`test_contrato_salida.py`, `test_golden_consolidacion_nit.py`, `test_cruce_columnas_arrastre.py`, `test_memory_lifecycle_v016.py`, `tests/contratos/esquema_salida_v0.json`) solo la nombra el docstring `api.py:846`; **0 notebooks, 0 scripts** | (a).8 decía «los leen los reportes L6, el SmartExporter, los notebooks 01–03 y 05»: **era falso para `CONFIANZA`**. Se conserva igual (decisión de Enrique: nombres de v1), pero su único consumidor es el contrato |
| 2 | **`USD_FOB_TOTAL`, `NUM_REGISTROS`** (golden de importadores) | no existen: el 07 de v1 suma `COLS_METRICAS` con el nombre de la columna de entrada (celda 2 L19: `COLS_METRICAS = ["NUM_REGISTROS_EXPORTACION", "VALOR_FOB_USD_TOTAL"]`; celda 7 L612) | tampoco son nombres de la librería: `flujo/importadores.py:661,910` hace `{c: (c, "sum") for c in cfg.cols_metricas}`; el 07 de v2 los trae como configuración del usuario; `docs/evidencia_importadores/PAISES.csv` de **ambos** repos trae `NUM_REGISTROS_EXPORTACION, VALOR_FOB_USD_TOTAL` | (a).7 corregido: lo que de verdad consumen los dos 07 es que el golden conserve los nombres de `cols_metricas` **tal cual** |
| 3 | Prefijos **`golden_records`/`tabla_correlativa`** | lectores: `pipeline/validator.py:165` (ruta heredada) y `README.md:147-148`; **la compuerta del `Orchestrator` que (a).1 cita no existe en v1** (`grep -c 'golden_records\|tabla_correlativa' orchestrator.py` → 0); 0 notebooks, 0 scripts, 0 pruebas | como lo describe (a).1 | v1 no aporta ningún lector externo adicional; el plazo de (a).1 sigue dependiendo de los VACÍOS de 3.6 |
| 4 | Patrones `golden_records.parquet`/`correlativa.parquet` como checkpoint | nadie los escribe tampoco en v1 (`strategies.py:299,305` es la única aparición) | ídem | ya marcados retirables |
| 5 | Nombres de v2 sin equivalente en v1: (a).6 (`flujo.cruce`), (a).7 como API, (a).9, `leer_resultado`, `ResultadoCruce` | no existen en 0.11.0 | — | v1 no añade ni quita nada |

Dato complementario: `REQUIRES_REVIEW`, `NAME_SIMILARITY_SCORE`, `NIT_DISTANCE`
y `SOURCES_LIST` no las lee nadie en v1 fuera de su productor
(`golden/generator.py:173,240,619,842,923`; `smart.py:135` limpia `SOURCES_LIST`
si existe); en v2 solo el contrato de F1 y sus pruebas. Se conservan por la
decisión «nombres de v1», no por un consumidor.

#### (iii) Columnas técnicas: quién las lee del entregable y qué hace F1 con ello

Qué prueba que las técnicas viajaban en el entregable de v1: `orchestrator.py:
756-777` las crea en L1 (`NOMBRE_LIMPIO`, `NIT_OK`, `NIT_BASE`, `NIT_VALID`,
`PHONETIC_KEY1`), ningún `drop(columns` las retira antes de L5 y
`strategies.py:464-470` / `smart.py:128-133` las limpian **si existen** al
escribir `tabla_correlativa.*`. Desde F1.9 `salida.completar` las retira del
entregable y el manifiesto dice dónde quedan (`manifiesto["columnas_tecnicas"]
["quedan_en"]` = `dir_trabajo`; el checkpoint es `L5_golden/correlative.parquet`).

| Columna | Lector | Tipo de lectura | Estado tras F1 |
| --- | --- | --- | --- |
| **`NIT_OK`** | `deduplication/unified.py:513` (v1 `:506`): `conexiones.sort_values(["ID_GRUPO","NIT_OK"])` | **dura** (`KeyError` si falta), pero **sobre su marco interno, antes del contrato**: `deduplicate_unified` toma `result.get("correlative_table")` del `RecordLinkagePipeline` (`unified.py:301`), cuya preparación añade `NIT_OK` (`:475`), ordena y exporta `conexiones_no_triviales.parquet` (`:308-314`) y devuelve; `api.dedupe()` aplica `completar_correlativa` **después** (`api.py:878,906`). Verificado leyendo el código y con `tests/test_contrato_salida.py` (fixture `res_dedupe`) | no lo rompe F1.9 |
| | `golden/generator.py:607,711` (v1); `strategies.py:464`, `smart.py:128` | blanda (si existe) | sin cambio |
| **`NIT_BASE`**, **`NIT_VALID`** | `scripts/verify_real_archives.py:133-136` y `scripts/verificar_rues_x_exportaciones.py:86-90` **en `c7a6d84`**: `AssertionError("La correlativa no contiene NIT_BASE canónico.")` y `correlativa["NIT_BASE"]`/`["NIT_VALID"]` sobre la correlativa **en memoria** de `linkage()`/`ejecutar_cruce()` | **dura y rota por F1.9** (la correlativa entregada ya no las trae) | **corregido en F1**: leen `<dir_trabajo>/L5_golden/correlative.parquet` con `salida.tecnicas.adjuntar_tecnicas` (`salida/tecnicas.py:229`; `ResultadoLinkage.dir_trabajo`; `ResultadoCruce.rutas["dir_trabajo"]`, `flujo/cruce.py:2276`); fail-fast `ColumnasTecnicasError` (`pipeline/errores.py:439`) si no hay `dir_trabajo`, parquet o columna. `verificar_rues_x_exportaciones.py` además recalcula los conflictos con la regla del contrato (`salida.completar.bases_del_motor` + `grupos_con_bases_distintas`) y exige que coincidan con el conteo publicado en el manifiesto |
| | v1 `scripts/generar_pares_para_etiquetar.py:108-109` (sobre `df_prep`, insumo preparado); `engine/lsh/prescreen.py:187-188` (marco de L2) | no sobre el entregable | sin cambio |
| **`NOMBRE_LIMPIO`** | v1 `generar_pares_para_etiquetar.py:113-114` (`df_prep`); `bench_lsh_indexing.py:198-199,219` (parquet externo, `sys.exit` si falta); `golden/generator.py:719`, `strategies.py:468`, `smart.py:133` (si existe) | ninguna dura sobre el entregable | sin cambio |
| **`PHONETIC_KEY1`** | `strategies.py:470` (si existe); `capturar_oraculo_p1_1.py`, `benchmark_p1_1.py` la **construyen** como entrada | ninguna dura | sin cambio |
| **`NOMBRE_BLOQUEO`** | **no existe en v1** (0 coincidencias en `src`, `tests`, `scripts`, `notebooks`, `docs`, `README`) | — | nace en v2 |
| Notebooks (v1 01–04, 07; v2 09) y pruebas de v1 | **0** lecturas de técnicas desde una salida del pipeline (las apariciones en pruebas son insumos construidos: `test_checkpoint_fingerprint.py`, `test_cache_version_key.py`, `test_disk_engine.py`, `test_sprint_0_8_0_prescreen.py`, `integration/test_nit_processor_parity.py:92`) | — | — |

Hecho medido al corregir los scripts (condiciona a cualquier consumidor de
`_trabajo/`): con `collapse_exact_duplicates=True` (`linkage`) o
`colapsar_duplicados_exactos=True` (`ConfigCruce`, valor por defecto) el
checkpoint de L5 es **compacto** y `api._expand_exact_correlative` renumera
`ORIGINAL_INDEX` de la correlativa entregada a la posición original
(`api.py:283`): la fila 3 entregada no es la fila 3 del checkpoint. Con 10
filas y 2 colapsadas, alinear por `ORIGINAL_INDEX` pegaba el `NIT_BASE` de
`BETA LTDA` a un registro sin NIT. `adjuntar_tecnicas` alinea por
`ORIGINAL_INDEX` solo si `SRC`·`NIT`·`RAZON_SOCIAL` coinciden fila a fila; si
no, por contenido (las técnicas son funciones del registro dentro de una
corrida), comprobando en el parquet que ese contenido las determina, y declara
cuál usó (`tests/test_salida_tecnicas.py:116-143,244-266`). La ruta `dedupe()`
no pasa por el Orchestrator: sus técnicas quedan en
`correlativa_unificada.parquet` por régimen (`con_nit/`, `sin_nit/` de
`metricas["output_dir"]`) y `adjuntar_tecnicas` no la cubre (el error lo dice).

#### (iv) El notebook 09

**No existe en v1**: no está en `notebooks/` de `c40dae5` (01, 02, 03, 04, 07), ni
en `main`, ni en `origin/deps/bump-mensual` (`git ls-tree`), ni en el historial de
ninguna rama (`git log --all --diff-filter=A --name-only | grep -iE
'09_|orbis|fdi|segmentacion|vincul'` → vacío). Las únicas coincidencias de
`orbis` en v1 son el nombre de empresa de un dato de prueba. El 09 vive en v2
(`notebooks/09_vincular_segmentacion_orbis_fdi.ipynb`, cabecera «motor
rues-linker 0.22.4») y 3.1 ya lo inventaría. De lo que F1 cambia, el 09 **solo**
usa `PipelineResult(work_dir=…, extra=…).to_excel(ruta, include=…)` (celda 7
L241; `PipelineResult` 2 menciones, `.to_excel(` 1), que F2.11 extrae a una
función libre ((a).5): **0** menciones de `tabla_correlativa`, `golden_records`,
`L5_golden`, `correlative.parquet`, `ID_GRUPO`, `ORIGINAL_INDEX`, `CONFIANZA`,
`REGIMEN_AUTO` ni de ninguna técnica; **0** llamadas a `ejecutar_cruce`,
`linkage`, `dedupe` o `deduplicar_importadores` (lo descarta explícitamente,
celda 6 L22, L416). Los renombres de F1.10/F1.11 y el traslado de las técnicas a
`_trabajo/` no alcanzan nada que el 09 lea o escriba.

## 4. Qué se puede retirar y qué no

**(a) Nombres de archivo y de columna de v1 que NO se pueden retirar sin romper
a alguien (hoy, con la evidencia de arriba):**

1. **Prefijos `golden_records` y `tabla_correlativa` en `L6_reporting/`** (con
   `.parquet`, `.csv.gz`, `.xlsx`). Los exige el propio `Orchestrator` como
   compuerta contractual (`orchestrator.py:1922-1932`), los busca
   `PipelineValidator` (`validator.py:166`), los documenta el `README.md:452-453`
   y son exactamente lo que Enrique cree que leen procesos externos (riesgo 4,
   V6). → **Alias obligatorio** en F1.10 durante dos versiones menores, con
   `DeprecationWarning` y hoja `LEEME`; retiro solo cuando V6 y la fila de
   Snowflake de 3.6 dejen de ser VACÍO.
2. **`L5_golden/golden.parquet` y `correlative.parquet`.** La propia librería
   los vuelve a leer (`orchestrator.py:755-762`, `strategies.py:304,310`). Pueden
   cambiar de sitio (el plan los lleva a `_trabajo/`), pero el cambio debe hacerse
   en los dos puntos de lectura en el mismo PR; no son contrato externo.
3. **Claves `"golden"` y `"correlative"` del dict de `linkage()`** y
   `"matcher_decisions"`: notebook 03, 3 scripts, `README.md`. Si F2.7 cambia el
   retorno a `ResultadoLinkage`, hace falta compatibilidad `__getitem__` o
   actualizar el 03, los 3 scripts y el README en el mismo PR. **(F1)** F1.9 lo
   hizo con el shim (`resultado.py:177-198`: `[]`, `get`, `in`, `keys()` con
   `DeprecationWarning`); `benchmark_e2e_matcher.py` y `verify_real_archives.py`
   ya usan los atributos (3.2); `active_labeling.py:181-200`, el notebook 03 y
   `README.md:452-453` siguen por el shim.
4. **`ResultadoLinkage.correlativa` / `.golden` / `.metricas` / `.resumen()`** y
   las claves de `metricas` (`n_registros`, `n_grupos`, `n_registros_con_nit`,
   `n_registros_sin_nit`, `n_registros_a`, `n_registros_b`,
   `n_grupos_cruzados`, `n_pares_a_b`): notebooks 01 y 02.
5. **Claves y hojas de `PipelineResult`** (`golden_records`,
   `correlative_table`) y sus métodos `to_excel`/`to_csv`: 6 notebooks y 2
   pruebas de integración. F2.11 extrae `to_excel` a una función libre; los
   notebooks siguen funcionando mientras la función acepte `extra=` con
   nombres de hoja arbitrarios (hoy todos la usan así).
6. **Archivos de `flujo.cruce`** (`golden.parquet`, `correlativa.parquet`,
   `metadatos_corrida.json`, `resultados.generations/<token>/`,
   `resultados.manifest.json`) y los atributos de `ResultadoCruce`
   (`correlativa`, `golden`, `rutas` —incluida `rutas["generation_dir"]`—,
   `metricas`, `tiempos`, `cruce_por_fuente`, `identidad_adoptada`,
   `matriz_presencia`, `entidades_ausentes_de`, `conflictos_identificador`,
   `distribucion_grupos`, `entidades_multifuente`, `grupos_sospechosos`,
   `identificadores_por_fuente`): notebooks 05, 06, 06 orquestador, 3 scripts y
   3 pruebas. F2.5 debe conservarlos como alias o adaptar notebooks y pruebas en
   el mismo PR.
7. **Tablas de `flujo.importadores`** (`CORRELATIVA`, `GOLDEN`, `PAISES`,
   `REVISION`, `MUESTRA_REVISION`, `METRICAS`, `INVARIANTES`, `PARAMETROS`) y
   sus columnas `RAZON_SOCIAL_FINAL`, `PAIS_FINAL`, `VEREDICTO_MANUAL`,
   `ID_IMPORTADOR` (`flujo/importadores.py:764`), `ID_EMPRESA_GLOBAL`, **más las
   columnas de `cfg.cols_metricas` con su nombre de entrada tal cual**
   (`flujo/importadores.py:661,910`; en la evidencia
   `NUM_REGISTROS_EXPORTACION`, `VALOR_FOB_USD_TOTAL`): notebook 07 y
   `docs/evidencia_importadores/`. F2.6 las renombra al estándar; el 07 cambia
   en el mismo PR. **(cruce v1)** Corrección: este punto listaba `USD_FOB_TOTAL`
   y `NUM_REGISTROS` como nombres de la librería; no lo son, son los nombres de
   entrada que el 07 de v2 pasa en `cols_metricas` (3.8.ii.2). Lo que no se
   puede cambiar es la regla «la suma conserva el nombre de la columna de
   entrada».
8. **Columnas de la correlativa** `ID_GRUPO`, `SRC`, `NIT`, `RAZON_SOCIAL`,
   `ORIGINAL_INDEX` (`flujo/cruce.py:521`, `:1283`; **(cruce v1)** además 7
   scripts y 14 pruebas de v1, 10 scripts y 32 pruebas de v2: 3.4 y 3.8.i),
   `NIT_FINAL`, `RAZON_SOCIAL_FINAL` y **del golden** `ID_GRUPO`,
   `CONFIDENCE_SCORE`, `PRIMARY_SOURCE`, `SOURCES_COUNT`, `NAME_VARIATIONS`,
   `NIT_VARIATIONS`: los leen los reportes L6, el `SmartExporter`, los notebooks
   01–03 y 05, `flujo.cruce` y las invariantes de la correlativa (ADR-0008).
   **`CONFIANZA`** (`golden/generator.py:228`, `:904`) se conserva por la misma
   decisión, pero **(cruce v1)** nadie la lee fuera del contrato de F1 (3.8.ii.1):
   este punto la atribuía a los reportes L6, el `SmartExporter` y los notebooks,
   y era falso. Coincide con la decisión de Enrique («se conservan los nombres de
   columna de v1»).
9. **Hojas del Excel de vinculación** (`EXPORTADORES`, `ENLACES`, `REVISION`,
   `DECISIONES`) y **`COLUMNAS_DECISION`**: notebook 10, la skill, el formato VPI
   (derivado de `ENLACES`) y la paridad de F3 (huella `ENLACES`
   `9e3950f469ae0523`). No se tocan sin ADR.
10. **El nombre público `deduplicate_unified`** y su retorno
    `(correlativa, estadísticas)`: 16 pruebas, 7 scripts, `api.dedupe()`.
    Coincide con F2.10 (se conserva).
11. **`RecordLinkagePipeline`**: hasta que F2.9 migre la fábrica del
    `Orchestrator` **y** `deduplication/unified.py:285`, 3 scripts y 1 prueba.
12. **(cruce v1)** **`REGIMEN_AUTO`** en la correlativa de `dedupe()`: la leen
    `api.dedupe()` para dos métricas del notebook 01 y `test_deduplicate_auto.py`
    (3.8.i.1). El contrato la trata como columna extra del motor, después de las
    de la fuente.
13. **(cruce v1)** **`matcher_stats`** (y `matcher_decisions`) como clave:
    2 scripts y 1 prueba (3.8.i.2). Desde F1.9 viven en `metricas` y el shim las
    sirve; se retiran con las demás claves del dict en 1.0.
14. **(cruce v1)** **`RECORD_COUNT`** en el golden: reportes L6 y
    `deduplicate_unified` (3.8.i.3). Es columna fija del contrato.
15. **(cruce v1)** **Las columnas de la fuente arrastradas a la correlativa con
    su nombre**, incluido un `ID_REGISTRO` propio que el contrato conserva como
    `ID_REGISTRO_FUENTE`: `benchmark_e2e_matcher.py` (3.8.i.4). Cualquier cambio
    del sufijo `_FUENTE` rompe ese script y el manifiesto `completar.renombres`.
16. **(cruce v1)** **Nombres del Excel de importadores que v1 ya entregó**
    (`deduplicacion_importadores.xlsx`/`__<HOJA>.csv.gz`/`__CORRELATIVA.parquet`,
    hojas `SENSIBILIDAD` y `RECALL_BLOQUEO`, columnas `PAIS_ISO3`,
    `PAIS_METODO`, `SIM_AL_FINAL`, `CAMBIO_NOMBRE`, `CAMBIO_PAIS`,
    `N_VARIANTES_NOMBRE`, `SIM_MINIMA`, `MOTIVO_REVISION`, `N_FILAS`, `BANDA`,
    `POBLACION_BANDA`): son lo que recibieron terceros con el 07 de v1 (3.8.i.5).
    F2.6 debe tratarlos como nombres de v1 (alias o LEEME), no como nombres nuevos.

**Nombres que SÍ se pueden retirar (ningún lector en el repositorio):**

- `<nombre>_MUESTRA_<n>k.xlsx` (0 lectores; el plan ya lo prohíbe:
  «Nunca más `_MUESTRA_100k`»).
- `config_auditoria_<ts>.json/.txt` (0 lectores; F1.12 lo fundió en
  `manifest.json → parametros/tiempos_por_fase/metricas`: queda
  `config_auditoria.json` como alias de v1 con `vease`, opcional en
  `contrato_l6`, y el `.txt` se retiró).
- `reporte_<nombre>.xlsx` sueltos (0 lectores; F1 los unifica en
  `informe_cruce.xlsx` conservando los 24 entregables).
- `correlative_table.csv` / `golden_records.csv` que escribe
  `PipelineResult.to_csv` (solo 1 prueba de integración; ningún notebook ni
  script).
- El nombre de directorio `L6_reporting` (0 notebooks, 0 scripts; solo
  `test_api_postprocessing.py` lo redirige vía el enum `Phase`). Puede pasar a
  la carpeta del estándar con un alias documentado en `docs/MIGRACION_v1.md`.
- Los patrones alternativos `golden_records.parquet` / `correlativa.parquet`
  como checkpoint de L5 (`strategies.py:304,310`): nadie los escribe
  (`grep -rn "golden_records\.parquet" src` → solo esa línea).
- La exportación propia de `RecordLinkagePipeline._phase6_export`
  (`linkage_pipeline.py:867,873`): muere con la clase (F2.9).
- `dashboard_ejecutivo.png` como **archivo exigido** por `PipelineValidator`
  (`validator.py:166`): el validador pertenece a la ruta heredada; el dashboard
  como entregable se conserva dentro de `figuras/`.

**(b) Las 4 preguntas abiertas para Enrique, tal como están en el plan:**

1. Orden F3 ↔ F4: el plan pone F3 antes porque no toca L1…L5 y desbloquea el
   trabajo con el formato VPI; si la base de 4 M llega antes, F4 pasa adelante.
   ¿Cuál de las dos llega primero?
2. Retiro de `deduplicate_dataframe`, `deduplicate_large_dataset_colab`,
   `OptimizationEngine`/optuna y `GroundTruthGenerator`: el plan asume
   `DeprecationWarning` en 0.23 y retiro en 0.24 (dependencias ya investigadas,
   sección 5 de hechos). ¿Confirma?
3. Umbral de estrellas: 0,80 (precisión, pierde 0,011 de F1 en el banco) o 0,70
   (mejora los dos conjuntos pero deja grupos de 100 registros). F2.2 trae los
   números por estrato; la decisión es suya.
4. Alias en español: solo en `diccionario.csv` y `leer_resultado(alias_es=True)`,
   nunca en los archivos (Q3 dijo «opcional»). ¿Basta así?

**Lo que este inventario necesita de Enrique además de esas cuatro** (son los
tres VACÍOS de 3.6, repetidos aquí para que no se pierdan): el notebook y el
Excel VPI de Caro (antes de F3.3); si Snowflake o algún proceso de carga lee
`tabla_correlativa.*` / `golden_records.*` (antes de retirar los alias); y la
lista de los equipos que abren esos archivos (V6). Hasta que lleguen, el plazo
de los alias de F1.10 **no se acorta**: dos versiones menores contadas desde
la versión que publique F1.10, y solo se retiran con esta tabla sin VACÍOS.

## 5. Anexo A · Comandos de medición y sus resultados

Todos corridos en `/home/user/wt/f0_7` el 2026-10-06 sobre `33827cc` y vueltos
a correr sobre la rama `claude/f0/t7` con este archivo ya en el árbol: cada
cifra es la que devuelve el comando que la acompaña. **El inventario se excluye
a sí mismo de todos los conteos** (`--exclude=CONSUMIDORES.md` en los que
recorren `docs`); sin esa exclusión, siete conteos cambiarían solo por existir
este archivo. Todo `grep -r` lleva además `--exclude-dir=__pycache__`: los
`.pyc` conservan las cadenas del código y, en un árbol donde ya corrió pytest,
`grep -rli snowflake src` pasaba de 4 a 7 por tres `.pyc` de `config/`. Los
conteos sobre notebooks que distinguen `source` de salidas se hicieron con un
lector del JSON (celda por celda); los `grep -l` sobre `*.ipynb` cuentan el
archivo completo y por eso el 08 (que lleva el README en `metadata.widgets`, no
en celdas) aparece en algunos y no en el inventario.

```text
ls notebooks/*.ipynb | wc -l                                                        → 11
grep -l 'from record_linkage.pipeline.result import PipelineResult' notebooks/*.ipynb | wc -l → 6  (01 02 03 04 07 09)
grep -l '\.to_excel(' notebooks/*.ipynb | wc -l                                     → 6  (01 02 03 04 07 09)
grep -l '\.to_csv(' notebooks/*.ipynb | wc -l                                       → 1  (10)
grep -lE 'tabla_correlativa|golden_records|L6_reporting|correlative\.parquet' notebooks/*.ipynb | wc -l → 1 (08: README en `metadata.widgets`)
grep -l 'golden\.parquet' notebooks/*.ipynb | wc -l                                 → 1  (06_ejemplo, comentario)
grep -l 'ejecutar_cruce(' notebooks/*.ipynb | wc -l                                 → 4  (05 06 06_orq + 08 por el README)
grep -l 'ejecutar_cruce(' scripts/*.py | wc -l                                      → 3
grep -l 'deduplicar_importadores(' notebooks/*.ipynb | wc -l                        → 2  (07 + 08 por el README)
grep -lE '=\s*linkage\(' scripts/*.py | wc -l                                       → 4  (3 llamadas + recalibrate_from_labels.py:135, que la imprime)
grep -lE '=\s*dedupe\(' notebooks/*.ipynb | wc -l                                   → 1  (01)
grep -lE '=\s*link\(' notebooks/*.ipynb | wc -l                                     → 1  (02)
grep -rl --exclude-dir=__pycache__ --exclude=CONSUMIDORES.md 'leer_resultado' src tests scripts notebooks docs README.md | wc -l → 0
grep -rl --exclude-dir=__pycache__ --exclude=CONSUMIDORES.md 'vinculacion' src tests scripts notebooks docs README.md pyproject.toml | wc -l → 0
grep -c 'RecordLinkagePipeline' src/record_linkage/pipeline/orchestrator.py         → 3   (:51 :274 :280)
grep -c 'self\.pipeline\.' src/record_linkage/pipeline/orchestrator.py              → 5   (:940 :947 :952 :957 :963)
grep -l 'RecordLinkagePipeline(' scripts/*.py | wc -l                               → 3
grep -rl --exclude-dir=__pycache__ 'RecordLinkagePipeline' tests | wc -l                                      → 3
grep -rl --exclude-dir=__pycache__ 'RecordLinkagePipeline(' tests | wc -l                                     → 1   (test_disk_based_path.py)
grep -rl --exclude-dir=__pycache__ 'RecordLinkagePipeline(' src | wc -l                                       → 3   (orchestrator.py, deduplication/unified.py, optimization/engine.py)
grep -rl --exclude-dir=__pycache__ 'deduplicate_unified' tests | wc -l                                        → 18
grep -rlE --exclude-dir=__pycache__ 'import.*deduplicate_unified|deduplicate_unified\(' tests | wc -l         → 14
grep -rlE --exclude-dir=__pycache__ '"deduplicate_unified"' tests | wc -l                                     → 2   (test_colab_safe_cache.py, test_api_linkage.py)
grep -l 'deduplicate_unified' scripts/*.py | wc -l                                  → 8
grep -lE 'import.*deduplicate_unified|deduplicate_unified\(|^\s+deduplicate_unified,' scripts/*.py | wc -l → 7
grep -rl --exclude-dir=__pycache__ 'deduplicate_unified(' src | wc -l                                         → 5   (auto, colab, dataframe, validator, unified)
grep -cE 'golden_records|tabla_correlativa' src/record_linkage/pipeline/orchestrator.py → 1 (:1926)
grep -cE 'name="(golden_records|tabla_correlativa)"' src/record_linkage/reporting/strategies.py → 2
grep -cE 'tabla_correlativa|golden_records' README.md                               → 2   (:452-453)
ls docs/*.md docs/adr/*.md | grep -v CONSUMIDORES | xargs cat | grep -cE 'tabla_correlativa|golden_records|L6_reporting' → 0
grep -lE 'tabla_correlativa|golden_records|L6_reporting' scripts/*.py | wc -l       → 0
grep -rl --exclude-dir=__pycache__ 'golden_records\.csv' tests | wc -l                                        → 1   (integration/test_pipeline_result_v2_1.py)
grep -rlE --exclude-dir=__pycache__ 'golden_records\*|tabla_correlativa\*' tests | wc -l                      → 0
grep -rl --exclude-dir=__pycache__ --exclude=CONSUMIDORES.md '_MUESTRA_' tests notebooks scripts docs README.md | wc -l → 1   (06_orq: LIMITE_MUESTRA_DEFAULT, falso positivo → 0 lectores)
grep -rl --exclude-dir=__pycache__ --exclude=CONSUMIDORES.md 'config_auditoria' tests notebooks scripts docs README.md | wc -l → 0
grep -rlE --exclude-dir=__pycache__ 'reporte_[a-z_]+\.xlsx' tests | wc -l                                     → 0
grep -rlE --exclude-dir=__pycache__ 'L6_reporting|L6_REPORTING' tests | wc -l                                 → 1   (test_api_postprocessing.py:211,238,261)
grep -l 'L6_reporting' notebooks/*.ipynb | wc -l                                    → 0
grep -rl --exclude-dir=__pycache__ 'metadatos_corrida' tests | wc -l                                          → 3
grep -rl --exclude-dir=__pycache__ 'resultados.generations' tests | wc -l                                     → 3
grep -rn --exclude-dir=__pycache__ 'golden_records\.parquet' src | wc -l                                      → 1   (strategies.py:304, patrón de lectura; nadie lo escribe)
grep -rli --exclude-dir=__pycache__ snowflake src | wc -l                                                     → 4
grep -rli --exclude-dir=__pycache__ snowflake notebooks | wc -l                                               → 3   (07, 08, README_notebooks)
grep -rliE --exclude-dir=__pycache__ --exclude=CONSUMIDORES.md '(subir|cargar|escribir|write|upload)[^\n]{0,40}snowflake' src notebooks scripts docs README.md | wc -l → 0
grep -rlw --exclude-dir=__pycache__ --exclude=CONSUMIDORES.md 'VPI' src tests scripts notebooks docs README.md | wc -l → 0
grep -rlw --exclude-dir=__pycache__ --exclude=CONSUMIDORES.md 'Caro' src tests scripts notebooks docs README.md | wc -l → 0
ls docs/evidencia_importadores | wc -l                                              → 7
```

Archivos de prueba que importan o llaman `deduplicate_unified` (14):
`tests/integration/test_deduplicate_unified.py`, `tests/test_baseline_v0_9_0.py`,
`tests/test_calidad_ground_truth_grande.py`, `tests/test_dedup_sin_nit.py`,
`tests/test_extra_features_integration.py`, `tests/test_idf_rescoring.py`,
`tests/test_production_hardening_additional.py`, `tests/test_quality_exhaustivo.py`,
`tests/test_quality_extra_features.py`, `tests/test_quality_golden.py`,
`tests/test_quality_sintetico_robusto.py`, `tests/test_regimen_warning.py`,
`tests/test_reproduce_bugs.py`, `tests/test_sin_nit_recalibrado.py`.

Scripts que lo llaman (7): `medir_baseline_v0_9_0.py`, `replicar_v2_5_0.py`,
`replicar_v2_6_0.py`, `replicar_v2_7_0.py`, `replicar_v2_8_0.py`,
`stress_test.py`, `verificar_determinismo.py`.

## 5b. Anexo B · Comandos del cruce contra v1 (`/home/user/rues-linker`, `c40dae5`)

Corridos el 2026-10-06 en solo lectura; `grep -r` siempre con
`--exclude-dir=__pycache__`.

```text
ls notebooks/*.ipynb | wc -l                                                                  → 5  (01 02 03 04 07)
git ls-tree --name-only origin/main notebooks/ ; … origin/deps/bump-mensual notebooks/        → 01 02 03 04 + README (ambas)
git log --all --diff-filter=A --name-only | grep -iE '09_|orbis|fdi|segmentacion|vincul'       → 0
grep -l 'PipelineResult' notebooks/*.ipynb | wc -l                                            → 1  (07)
grep -lE 'tabla_correlativa|golden_records|L6_reporting|L5_golden|correlative\.parquet|golden\.parquet' notebooks/*.ipynb | wc -l → 0
grep -lwE 'NIT_BASE|NIT_VALID|NOMBRE_LIMPIO|NOMBRE_BLOQUEO|PHONETIC_KEY1|NIT_OK' notebooks/*.ipynb | wc -l → 0
grep -lE 'tabla_correlativa|golden_records|L6_reporting' scripts/*.py | wc -l                → 0
grep -lw 'ORIGINAL_INDEX' scripts/*.py | wc -l                                               → 7
grep -rlw 'ORIGINAL_INDEX' tests | wc -l                                                     → 14
grep -rlw 'REGIMEN_AUTO' src tests | wc -l                                                   → 3  (api.py, deduplication/auto.py, test_deduplicate_auto.py)
grep -rlw 'matcher_stats' src tests scripts | wc -l                                          → 5  (api.py, matching/pipeline_integration.py, test_matching_integration.py, benchmark_e2e_matcher.py, active_labeling.py)
grep -c 'golden_records\|tabla_correlativa' src/record_linkage/pipeline/orchestrator.py      → 0  (sin compuerta en v1)
grep -rnw 'CONFIANZA' src tests scripts | grep -v golden/generator.py | wc -l                → 0
grep -rnwE 'REQUIRES_REVIEW|NAME_SIMILARITY_SCORE|NIT_DISTANCE' src tests scripts | grep -v golden/generator.py | wc -l → 0
grep -rnw 'RECORD_COUNT' src | grep -v golden/generator.py                                   → reports.py:724,725,768; unified.py:498,503
grep -rnw 'NOMBRE_BLOQUEO' src tests scripts notebooks docs README.md | wc -l                → 0
grep -rn 'DeduplicationValidator' src tests scripts | grep -v deduplication/validator.py | wc -l → 0
grep -rnE 'correlativa_unificada|conexiones_no_triviales' src tests scripts notebooks docs README.md → unified.py:304,308,310,319; dataframe.py:56 (0 lectores)
grep -rnE 'tabla_correlativa|golden_records|L6_reporting' .github/                           → 0
grep -cE 'tabla_correlativa|golden_records' CHANGELOG.md                                     → 0
for f in docs/evidencia_importadores/*.csv; do head -1 "$f"; done   (cabeceras iguales a las de rues-linker_v2/docs/evidencia_importadores/)
python3: source de las celdas del notebook 07 → deduplicacion_importadores 1 · RECALL_BLOQUEO 5 · SENSIBILIDAD 5 · PAIS_ISO3 18 · USD_FOB_TOTAL 0 · NUM_REGISTROS_EXPORTACION 1 · VALOR_FOB_USD_TOTAL 2 · flujo.importadores 0
```

Sobre v2 (`/home/user/wt/f1_consumidores`, `c7a6d84` más los cambios de F1):

```text
grep -lw 'ORIGINAL_INDEX' scripts/*.py | wc -l                                               → 10
grep -rlw 'ORIGINAL_INDEX' tests | wc -l                                                     → 32 (2 son de F1: test_salida_tecnicas.py, test_scripts_tecnicas_contrato.py)
grep -rlw 'REGIMEN_AUTO' src tests                                                           → api.py, salida/completar.py, contrato.py, deduplication/auto.py, test_deduplicate_auto.py
grep -rlw 'matcher_stats' src tests scripts                                                  → matching/pipeline_integration.py, resultado.py, api.py, test_matching_integration.py, benchmark_e2e_matcher.py, active_labeling.py
grep -rnw 'CONFIANZA' src tests scripts | grep -v 'contrato.py\|salida/\|resultado.py\|golden/' | cut -d: -f1 | sort -u → api.py (docstring :846), tests/contratos/esquema_salida_v0.json, test_contrato_salida.py, test_cruce_columnas_arrastre.py, test_golden_consolidacion_nit.py, test_memory_lifecycle_v016.py
grep -nwE 'USD_FOB_TOTAL|NUM_REGISTROS|cols_metricas' src/record_linkage/flujo/importadores.py → solo cols_metricas (:135,191,306-311,504-515,661,906,910)
python3: source de las celdas del notebook 09 → PipelineResult 2 · to_excel 1 · tabla_correlativa 0 · L5_golden 0 · NIT_BASE 0 · ORIGINAL_INDEX 0 · ID_GRUPO 0 · ejecutar_cruce 0 · linkage( 0
python3: source de las celdas del notebook 07 de v2 → USD_FOB_TOTAL 3 · NUM_REGISTROS 2 · cols_metricas 8 (configuración del usuario)
pytest tests/test_scripts_tecnicas_contrato.py -W error::DeprecationWarning:<los tres scripts> → 7 passed (ningún script pasa por el shim)
```

## 6. Cómo se mantiene este archivo

- Cada fila nueva entra con evidencia (`archivo:línea` o `notebook · celda`) y
  estado. Un consumidor externo pasa de `VACÍO` a `verificado` cuando su dueño
  entrega la lista de archivos y columnas que abre.
- Cuando F1.10 publique los alias, se anota aquí la versión de publicación y la
  versión prevista de retiro (dos menores después). Cuando F2.9 migre la fábrica
  y `unified.py:285`, se cierra la fila de `RecordLinkagePipeline`.
- Antes de retirar cualquier nombre de la lista (a), se vuelven a correr los
  comandos del anexo A **tal como están escritos** (con la exclusión de este
  archivo) y se actualizan los conteos; si un conteo que era 0 dejó de serlo,
  el retiro espera.
- El cruce con v1 (3.8, Anexo B) está cerrado sobre `c40dae5`; solo se repite si
  aparece una versión de v1 posterior a 0.11.0 con consumidores nuevos. Lo que
  F1 cambió en los scripts (3.8.iii) se verifica con
  `tests/test_scripts_tecnicas_contrato.py`; si un script nuevo lee una columna
  técnica, debe pasar por `salida.tecnicas.adjuntar_tecnicas`, no por la
  correlativa entregada.
