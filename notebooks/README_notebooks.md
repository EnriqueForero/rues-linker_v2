# Notebooks oficiales de `rues-linker`

> Vigente para **0.22.4**. Los números de versión entre paréntesis en cada
> sección dicen cuándo se introdujo o se cambió por última vez ese notebook.

Suite mínima para deduplicación y record linkage en **Google Colab Free**. Los
notebooks 01–05 conservan el patrón **Celda A / A1 / A2 / B** (la A1 fija
`RUTA_DATOS` y `RUTA_RESULTADOS`). El nuevo 06 es un
orquestador declarativo: entorno, parámetros, preflight, ejecución y QA lazy.

## ¿Cuál notebook usar?

- Tengo **UNA base** y quiero encontrar duplicados (NIT + nombre)
  -> `01_deduplicar_una_base.ipynb` (básico, formulario).
- Tengo **DOS bases** y quiero saber qué empresas están en ambas
  -> `02_cruzar_dos_bases.ipynb` (intermedio).
- Tengo **3 o más fuentes** y necesito el consolidado de producción
  (trusted, checkpoints, manifiesto, auditoría)
  -> `03_produccion_multifuente.ipynb` (avanzado).
- Quiero deduplicar con **varias variables** (email, teléfono, dirección...),
  activar la **corroboración de veto de NIT (F3)** o medir **P/R/F1**
  -> `04_multicampo_y_evaluacion.ipynb` (experto; trae demo autocontenida).
- Quiero **cualquier par de bases** con nombres, separadores y tipos distintos,
  declarados en un contrato, y que el flujo haga preflight, smoke test,
  invariantes, exportes y metadatos
  -> `05_general_cruce_configurable.ipynb` (**el general**; v0.16.0).
- Tengo una base **SIN identificador** —solo nombre y país— y quiero una
  correlativa con el nombre final de cada empresa
  -> `07_deduplicar_importadores_razon_social_pais.ipynb` (v0.22.4).
- Quiero **publicar el paquete a GitHub** con las compuertas de calidad del
  repositorio (ruff, coherencia de versión, banco, conformidad)
  -> `08_PUBLICAR_GITHUB.ipynb` (v0.22.4).
- Quiero la **plantilla profesional y universal**: lista de N `SourceSpec`,
  presupuesto DuckDB, salida Parquet lazy y QA N-fuente — con el caso
  RUES × Exportaciones DANE ya cargado
  -> `06_orquestador_configurable.ipynb` (**la recomendada**; v0.16.0,
  con staging DuckDB, límite temprano y spill local).

## Preparación en Colab Free

1. Suba sus archivos desde el panel **Archivos** de Colab a `/content/datos/`
   (el 06 también busca allí los nombres auditados) o defina las variables de ruta.
2. Ejecute la Celda A. Los notebooks 01-04 instalan el tag oficial con
   `pip install rues-linker @ git+https://github.com/EnriqueForero/rues-linker_v2@v0.16.0`.
   En el 06 configure una ruta exacta mediante `RUES_LINKER_WHEEL` o
   `RUES_LINKER_PROJECT_DIR` si el paquete correcto aún no está instalado.
3. Revise `RUTA_DATOS`/`RUTA_RESULTADOS` en la Celda A1 y edite únicamente los
   nombres de archivo y columnas de la Celda B.
4. Descargue `/content/resultados/` antes de cerrar el runtime: el disco local de
   Colab es efímero.

El notebook 05 monta Drive. El 06 es portable y solo lo monta si se cambia
conscientemente `MONTAR_DRIVE=True`; también funciona con archivos locales o subidos a
`/content/datos`. Ambos hacen el trabajo pesado en disco local y escriben los
resultados en el `workspace` indicado. Los notebooks 01–04 no montan Drive.

Su lógica no vive en las celdas sino en `record_linkage.flujo`, que se entrega
con pruebas; los notebooks tienen además contratos en
`tests/test_notebooks_v014.py`, `tests/test_notebook_orquestador_v016.py` y
`tests/test_notebooks_contrato.py` (F1.15: ninguno usa la API anterior al
contrato de salida y los siete 01–06 se ejecutan enteros con `nbclient` sobre
`tests/data_sintetica/`, prueba marcada `slow`)
(el primer nombre es histórico; el contrato actual es 0.16.0:
el código compila, los nombres importados
existen, la celda editable se mantiene corta y el trabajo nunca se configura
sobre Drive).

Para los notebooks 01-04: no se monta Drive automáticamente. Insumos, descompresión, checkpoints y demás
temporales permanecen en `/content`, evitando usar Drive como disco de trabajo.

## Contrato e ingesta

El motor trabaja con `NIT` y `RAZON_SOCIAL` (y opcionales `CIUDAD`, `EMAIL`,
`TELEFONO`, `DIRECCION`). Sus archivos no necesitan renombrarse: el notebook hace
el mapeo y fuerza el NIT como texto para conservar ceros a la izquierda.

Los notebooks `01`, `02` y `04` leen CSV como UTF-8 estricto: un byte inválido
produce un error en lugar de sustituirse silenciosamente. Para fuentes heterogéneas,
use el `03`: sus `SourceSpec` detectan UTF-8/CP1252 en modo estricto, proyectan solo
las columnas declaradas y admiten CSV, TXT, XLSX, Parquet, GZIP y ZIP. En un ZIP con
varios archivos tabulares se debe declarar `miembro_zip`; el `03` limita cada ZIP a
16 miembros, 1 GiB descomprimido y ratio 100x antes y durante la descompresión.

`load_source` lee por bloques pero, por contrato, reúne la fuente completa antes del
enlace. Para inspeccionar use `iter_source_chunks`; para el notebook 06 use el motor
`duckdb`, que proyecta, limita y colapsa en disco antes de entregar representantes al
enlace. Las fases LSH/scoring todavía usan memoria proporcional a esos representantes:
controle el perfil y valide el pico antes de una corrida nacional.

## Reglas de operación

- Corridas grandes con ZIP/CSV: use el `06` y su staging DuckDB. Para tres o
  más fuentes que ya caben proyectadas en memoria, el `03` conserva el flujo
  multifuente. Sus checkpoints locales viven en
  `/content/rues_linker_work/<etiqueta>/` y solo sobreviven mientras viva el runtime.
- Los `01`, `02` y `03` entregan la **carpeta del estándar de salida** (contrato
  1.0, escrita de forma atómica por `escribir_resultado`/`linkage(carpeta_salida=…)`):
  `correlativa.parquet`, `golden.parquet` (no en el `01`: `dedupe()` no lo produce
  en memoria), `excel/` completo o `*_LEEME.xlsx`, `diccionario.csv`, `revision.csv`
  y `manifest.json`; `leer_resultado(carpeta)` la lee de vuelta verificando huellas.
  Las vistas propias de cada notebook (DUPLICADOS, PARES_CRUZADOS, MATRIZ_FUENTES,
  RESUMEN…) van en `vistas/` dentro de esa carpeta y no forman parte del contrato.
- En el `03`, `skip_reporting=True` omite los reportes L6 (figuras y alias de v1),
  no la carpeta del estándar ni las vistas del notebook.
- En el `03`, `collapse_exact_duplicates=True` procesa una sola copia de filas
  exactamente iguales y restaura una fila por registro en la correlativa final.
- En el 06, resultados productivos: `golden.parquet`, `correlativa.parquet` y
  metadatos JSON. Excel se desactiva porque materializaría la salida grande.
- Seguridad de salida: los `.xlsx`/`.csv.gz` (del estándar y de las vistas) se escriben
  con las primitivas de `exporters.escritor`, que neutralizan texto que empieza por
  `=`, `+`, `-`, `@` antes de escribir. Los notebooks ya no usan `PipelineResult`
  (obsoleto desde el contrato de salida; hasta 0.22.x lo usaban para el Excel).
- Trazabilidad: `manifest.json` es el único manifiesto de la corrida (versión,
  huella y filas de cada fuente, parámetros pedidos y efectivos, conteos, métricas);
  el `manifiesto_corrida.json` que el `03` escribía a mano desaparece.
- El `04` (motor multicampo) exporta vistas CLUSTERS/DECISIONES, no la carpeta del
  estándar: `evaluar_esquema` no completa el contrato 1.0 todavía.

Estos notebooks reemplazan a: `fuente_unica`, `Cruce_universal_de_dos_bases`,
`rues_linker_universal`, `Nearshoring_tres_fuentes`, `produccion_4fuentes` y
`prueba_datos_reales_v0_7_5`.


## 07 · Deduplicar sin identificador (v0.22.4)

Para bases donde **no existe ningún NIT**: destinatarios de exportación,
padrones de compradores, listas de contrapartes. El empalme es por **razón
social y país**, con el país como bloqueo duro y canonizado contra un catálogo
ISO 3166 declarado — no por similitud de cadenas, porque `TURQUÍA`/`TÜRKIYE` no
se parecen y son el mismo país, mientras que `GUINEA`/`GUINEA-BISSAU` sí se
parecen y son distintos.

Entrega una **correlativa** —cada nombre y país originales con los finales
asignados— más la evidencia para juzgarla: precisión por banda sobre muestra
revisable, recall del bloqueo medido por fuerza bruta, diez invariantes que
detienen el notebook si fallan, y sensibilidad al umbral.

Son **16 celdas** numeradas 1–10 (la 10 es la referencia). La lógica vive en
`record_linkage.flujo.importadores`, con 76 pruebas propias; el notebook configura y
audita. Dos celdas opcionales amplían el catálogo de países y las listas de
términos a limpiar: son la palanca principal de calidad, porque el IDF
aprendido del corpus **no distingue una marca de un genérico** —medido,
`ZELECTA` (6,37) pesa menos que `IMPORTADORA` (5,96)— y por eso la lista es un
dato declarado.

**Carga la librería desde Drive, sin `pip install`** (v0.22.2). Es la segunda
estrategia admitida por el contrato de la celda de entorno: en vez de instalar
la rueda, pone `src/` al frente de `sys.path`. A cambio tiene que verificar dos
cosas que la instalación da gratis, y las verifica: **de dónde se importó
`record_linkage` de verdad** —si ya había otro en el kernel, aborta— y que el
árbol de Drive esté **completo**, importando los 9 submódulos uno por uno. Sin
eso, una sincronización de Drive a medias se manifestaría como un
`ModuleNotFoundError` a los siete minutos de cómputo. Las dependencias se leen
del `pyproject.toml` del propio paquete, no de una lista copiada en el
notebook: así no se desincronizan.

**Perfil de columnas y países fuera del catálogo** (v0.22.3). Los nombres de las
columnas viven en `PERFILES_ENTRADA` de la celda 3 — un perfil por fuente
(`snowflake_v2`, `destinatarios_dian`) y una variable `PERFIL` que elige. Y
`PAISES_SIN_CLASIFICAR`: la base nueva trajo grafías de `PAIS_ESTANDAR` que el
catálogo no conoce, y medido, con la etiqueta única que había, la misma razón
social bajo tres grafías sin clasificar se fusionaba en un solo importador. Ahora
la celda 6 comprueba la cobertura **sobre la base completa antes del smoke
test** y se detiene con la lista (`detener`, defecto). Lo que no es un país
(`OTROS`) se declara en `NO_SON_PAISES` (celda 4): se aísla con su propio
`PAIS_FINAL` y la corrida sigue, **sin** dejar de detenerse ante una grafía
nueva. La primera corrida real trajo 19: 18 eran países que faltaban en el
catálogo y ya están en la librería (0.22.4).

**Memoria** (v0.22.4). La primera corrida sobre la base `snowflake_v2` completa
(355.681 filas) murió por memoria en la partición 175 de 214. La partición USA
sola —54.669 nombres, 24,5 M de pares candidatos— costaba 11,4 GiB de pico:
el motor puntuaba todos los pares de una vez y la tabla de decisiones guardaba
los dos nombres en cada candidato. Ahora puntúa por lotes (`pares_por_lote`,
1 M) y `decisiones` conserva solo lo auditable, con los nombres bajo demanda
(`RESULTADO.decisiones_con_nombres()`). Mismo resultado —probado bit a bit—,
4,6 GiB de pico en USA y 4,8 GiB en la base completa (355.681 filas → 209.038
importadores, 17 min de pared). No hay
nada que configurar en el notebook.

Corrida de referencia: 211.949 registros → 100.008 importadores (−52,8 %; 99.897
hasta 0.22.3, antes de tratar `A LA ORDEN`/`TO ORDER` como sin nombre), las 10
invariantes en OK, idéntico entre procesos con distinta semilla de hash (0.22.3). Precisión ponderada 0,966 sobre 160 asignaciones revisadas a
mano; recall del bloqueo 1,000. Ver [`docs/ANALISIS_IMPORTADORES.md`](../docs/ANALISIS_IMPORTADORES.md)
y ADR-0009.

## 08 · Publicar a GitHub (v0.22.4)

Empaqueta y publica el paquete al repositorio **`rues-linker_v2`** con **las
compuertas de este repositorio**, no con las genéricas: ruff, coherencia de
versión en sus cuatro puntos, contratos de notebooks, **banco institucional**,
**conformidad** (43 casos frontera) y verificación de que existan la entrada del
CHANGELOG y de la bitácora. Aborta si algo bloqueante falla.

Orden de celdas: **A → A.0 → A.1 → B → C → D → E**.

Ocho adaptaciones respecto de la plantilla genérica, todas fijadas por
pruebas de contrato:

1. **Celda A.0 instala el paquete antes de medir.** Sin ella pytest falla con
   `ModuleNotFoundError` — el layout es `src/` y nada instalaba la librería;
   el banco y la conformidad sí pasaban porque hacen `sys.path.insert(src)`,
   así que el síntoma señala al lugar equivocado. Instala la **rueda**, no el
   árbol, y verifica nueve submódulos.
2. **No regenera `pyproject.toml`.** El generado pierde el marcador `slow`
   (7 tests dejarían de colectar), `[tool.ruff]` y `[tool.mypy]`.
3. **No reescribe `__version__`** en `__init__.py`: es un centinela.
4. **No genera LICENSE**: el proyecto es Apache-2.0 y la plantilla escribe MIT.
5. **Un salto de conformidad es un fallo, no un OK.** pytest devuelve 0 con
   saltos: el fixture del conjunto hace `pytest.skip` si no lo encuentra, y con
   el paquete instalado —lo que hace A.0— la ruta se resolvía contra
   `site-packages`, así que **8 casos se saltaban en silencio** y la compuerta
   reportaba verde sin medir. La compuerta 3bis inspecciona los saltos, no solo
   el código de salida.
6. **Limpia las salidas de los notebooks al copiar, no antes.** La versión
   anterior exigía por compuerta que los notebooks no tuvieran salidas — y el
   08 es el que corre la compuerta, así que Colab ya le había autoguardado las
   suyas: no podía pasar nunca. Ahora el build las limpia (Drive intacto) y la
   prueba comprueba el checkout de git, donde la propiedad sí es exigible.
7. **Las dependencias de desarrollo salen del pyproject, no de una lista.** La
   celda 3 traía una lista a mano sin `hypothesis` y la publicación real cayó
   al colectar la suite. A.0 instala y verifica las extras `dev` del pyproject —
   la misma lista que el CI— y `correr_tests_locales` usa los marcadores del
   `ci.yml`. La salida de un fallo ya no se recorta a 600 caracteres.
8. **Los conjuntos versionados no se quedan fuera del build.** La plantilla
   excluye `*.csv`/`*.parquet` —"solo va a git el código"—, y aquí eso se
   llevaba 16 archivos: los 7 CSV de conformidad, el ground truth y los 6
   fixtures de tests. Sin error: copiaba 404 de 420 y decía "Build listo".
   Ahora `DIRECTORIOS_VERSIONADOS` los declara por directorio y un build
   incompleto **aborta** en vez de avisar. El `.gitignore` generado además
   re-incluye el directorio antes que el archivo, pero eso es refuerzo: se
   comprobó que hoy no hacía falta.

Y una distinción que evita falsas alarmas: el banco separa **calidad**
(determinista, siempre bloquea) de **costo** (segundos y RSS, que dependen de
la carga del contenedor). Medido aquí: la misma versión 0.21.0 tardó 51,0 s por
la mañana y 65,1 s por la tarde en el mismo contenedor. El costo solo bloquea
si la línea base es de esta sesión.

> La **distribución** sigue llamándose `rues-linker` aunque el repositorio sea
> `rues-linker_v2`: renombrarla rompería `importlib.metadata.version(...)` en
> `__init__.py`, en `verificar_coherencia_version.py` y en todos los notebooks que
> verifican su versión.
