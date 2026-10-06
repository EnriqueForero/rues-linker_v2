# Inventario de consumidores de las salidas de rues-linker

> **Borrador para revisión de Enrique · 2026-10-06.** Tarea F0.7 del plan de
> ejecución v2 → producción. Estado del repositorio medido: rama
> `claude/f0-fundaciones`, commit `33827cc`, versión `0.22.4`.
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

## 2. Qué escribe hoy cada camino (los artefactos cuyo nombre está en juego)

| Camino | Artefacto que escribe | Nombres | Evidencia |
| --- | --- | --- | --- |
| `Orchestrator` L5 | parquet intermedios | `L5_golden/golden.parquet`, `L5_golden/correlative.parquet` | `pipeline/orchestrator.py:1714-1715` (escribe), `:1809-1810` (escribe), `:756-757` (vuelve a leerlos para reportar) |
| `Orchestrator` L6 (`DataExportStrategy`) | exportación contractual | `L6_reporting/golden_records.{parquet,csv.gz,xlsx}`, `L6_reporting/tabla_correlativa.{parquet,csv.gz,xlsx}`; si supera 1.048.576 filas, `<nombre>_MUESTRA_<n>k.xlsx` | `reporting/strategies.py:69` (directorio), `:300-312` (nombres y patrones de checkpoint `golden.parquet` o `golden_records.parquet`, `correlative.parquet` o `correlativa.parquet`), `:415-453` (extensiones y muestra) |
| `Orchestrator` L6 (reportes) | 24 entregables de v1 | `dashboard_ejecutivo.png`, `dashboard_ejecutivo_mejorado.png`, `reporte_<nombre>.xlsx`, `config_auditoria_<ts>.json/.txt`, `visualizaciones/` | `reporting/{dashboard,visualizer,reports,suite,strategies}.py` (nombres construidos con `output_dir / f"…"`) |
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
| `active_labeling.py`, `benchmark_e2e_matcher.py`, `verify_real_archives.py` | tabla: `linkage()` → dict | `"golden"`, `"correlative"` (+ auditoría del matcher en `benchmark_e2e_matcher`) | `:181`, `:112,138`, `:217` (`recalibrate_from_labels.py:135` solo imprime la llamada) | verificado en repo |
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
| Ninguna prueba | archivos `L6_reporting/golden_records*` o `tabla_correlativa*` leídos por nombre; `reporte_*.xlsx`; `config_auditoria_*`; `_MUESTRA_<n>k.xlsx` | — | `grep` → **0** en los cuatro casos (anexo A) | verificado en repo (sin consumidor) |

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
   actualizar el 03, los 3 scripts y el README en el mismo PR.
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
   sus columnas `RAZON_SOCIAL_FINAL`, `PAIS_FINAL`, `USD_FOB_TOTAL`,
   `NUM_REGISTROS`, `VEREDICTO_MANUAL`, `ID_IMPORTADOR` (`flujo/importadores.py:764`),
   `ID_EMPRESA_GLOBAL`: notebook 07 y `docs/evidencia_importadores/`. F2.6 las renombra al estándar;
   el 07 cambia en el mismo PR.
8. **Columnas de la correlativa** `ID_GRUPO`, `SRC`, `NIT`, `RAZON_SOCIAL`,
   `ORIGINAL_INDEX` (`flujo/cruce.py:521`, `:1283`), `NIT_FINAL`,
   `RAZON_SOCIAL_FINAL` y **del golden** `ID_GRUPO`, `CONFIDENCE_SCORE`,
   `CONFIANZA` (`golden/generator.py:228`, `:904`), `PRIMARY_SOURCE`,
   `SOURCES_COUNT`, `NAME_VARIATIONS`, `NIT_VARIATIONS`: los leen los reportes
   L6, el `SmartExporter`, los notebooks 01–03 y 05, `flujo.cruce` y las
   invariantes de la correlativa (ADR-0008). Coincide con la decisión de Enrique («se conservan los nombres de
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

**Nombres que SÍ se pueden retirar (ningún lector en el repositorio):**

- `<nombre>_MUESTRA_<n>k.xlsx` (0 lectores; el plan ya lo prohíbe:
  «Nunca más `_MUESTRA_100k`»).
- `config_auditoria_<ts>.json/.txt` (0 lectores; F1.12 lo funde en
  `manifest.json`).
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
