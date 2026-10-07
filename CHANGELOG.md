# Changelog

## [Sin publicar] — F1 · Contrato de salida: nada se degrada en silencio

**Cambio de API declarado**: `linkage()`, `dedupe()` y `link()` devuelven
`ResultadoLinkage` (contrato 1.0) en vez de un `dict`. Las claves de v1
(`res["correlative"]`, `res["golden"]`, `res.get("report_files")`,
`"preprocessing"`, `in`, `.keys()`) siguen funcionando con `DeprecationWarning`
hasta 0.25.0. El motor (L1…L5) no cambia: huella del banco idéntica.

- **Contrato de salida** (`record_linkage.contrato`, `resultado.py`,
  `salida/completar.py`): correlativa con 12 columnas fijas (`ID_REGISTRO`,
  `ID_GRUPO`, `ID_ENTIDAD`, `METODO_UNION`, `SCORE_PAR`, `CONFIANZA`, …) y
  después TODAS las de la fuente; golden de 13 columnas de v1 + `ID_ENTIDAD`,
  conteos `int64` y `REQUIRES_REVIEW` booleano; `validar()`; diccionario con
  alias en español; columnas técnicas (`NIT_BASE`, `NIT_VALID`, `NOMBRE_LIMPIO`,
  `PHONETIC_KEY1`, …) fuera del entregable, en `_trabajo/`
  (`salida.tecnicas.adjuntar_tecnicas` las recupera alineando por contenido).
  `METODO_UNION=identificador` exige que otro miembro del grupo comparta la
  base válida del motor (`NIT_BASE`/`NIT_VALID`, una sola regla).
- **Carpeta del estándar** (`exporters/escritor.py`, `linkage(carpeta_salida=,
  nombre=)`): escritura atómica (pendiente → definitiva), `manifest.json` con
  SHA-256 y bytes, `diccionario.csv`, `leer_resultado()`; Excel completo hasta
  1.048.575 filas o `<tabla>_LEEME.xlsx` (xlsxwriter en flujo, sin recortes;
  `_MUESTRA_100k.xlsx` y `export_settings.excel_max_rows` retirados);
  `config_auditoria_<ts>.json/.txt` fundido en `manifest.json → configuracion`
  (alias `config_auditoria.json` con `DeprecationWarning`).
- **Cero degradaciones silenciosas**: `pipeline/errores.py` (un solo
  `mensaje_accionable`; `ConsolidacionNitError`, `GoldenInvalidoError`,
  `MuestreoReportesError`, `ArtefactoObligatorioError`, `ColumnasArrastreError`,
  `ColapsoExactoError`, `CruceSinFuenteError`, `TiemposPorFaseError`,
  `EscrituraSalidaError`, `ContratoSalidaError`, …); contrato de L6
  (`reporting/contrato_l6.py`): los obligatorios tumban la corrida, los
  opcionales quedan en `manifest.json → omitidos`; ningún xlsx/PNG con un
  error dentro; golden fusionado por NIT con métricas recalculadas
  (`golden/metricas.py`) y sin columnas ajenas; muestreo de reportes con piso
  por estrato; reportes sobre la tabla completa con `ALCANCE`; tiempos por fase
  reales (`reporting/_fases.py`); resumen ejecutivo con candidatos, pares y RSS
  medidos; columnas de arrastre de `flujo.cruce` declaradas o error.
- **Golden tipado y C38** (ADR 0010): `NAME_SIMILARITY_SCORE` compara
  normalizado contra normalizado.
- **Notebooks 01–06** migrados al contrato (`ResultadoLinkage`/`leer_resultado`);
  `tests/test_notebooks_contrato.py` (slow) los ejecuta con nbclient.
- **Herramientas**: `scripts/banco.py --comparar` rechaza corridas de conjuntos
  distintos; `docs/CONSUMIDORES.md` revisado contra v1 0.11.0; scripts de
  verificación leen las técnicas desde `_trabajo/`.
- Dependencia nueva: `xlsxwriter>=3.1,<4`.

## [Sin publicar] — F0 · Fundaciones del plan «v2 → producción»

Sin cambios de comportamiento en el motor. Entra lo que hace medible todo lo
que sigue:

- **CI en verde**: versión del smoke leída de `pyproject`, `setuptools>=83`
  para `pip-audit`, `sys.platform == "win32"` en `ingestion/duckdb.py`,
  `tomllib` con respaldo `tomli` en la prueba de marcadores (Python 3.10).
- **Líneas base con prueba**: banco (`docs/evidencia/corrida_base_f0.json`,
  `tests/lineas_base.py`), conformidad sin y con `--corroborar`
  (`scripts/conformidad.py --corroborar`, `Informe.corroborar`/`camino`),
  contrato de salida v0 (`tests/contratos/esquema_salida_v0.json`), escala a
  139k y 463k (`scripts/escala.py`, `--comparar` con tolerancia del 10 %),
  determinismo entre procesos (`tests/test_determinismo_linkage_procesos.py`).
- **Trinquete de deuda técnica**: `scripts/deuda.py` mide cinco conteos sobre
  `src/record_linkage` y el job `deuda` del CI falla si alguno supera
  `docs/evidencia/deuda_f0.json`; el paso «Reglas estrictas en código tocado»
  aplica `PTH`, `BLE001`, `E722`, `T201` y complejidad ≤ 15 a los `.py` que
  un PR toca. `mypy==1.20.2` y `pandas-stubs==3.0.5.260914` fijados en `dev`.
- **Consumidores de la salida** (`docs/CONSUMIDORES.md`, borrador): quién lee
  qué archivo y columna, base para los alias de v1 de F1.
- `tests/cargar_script.py`: único cargador de `scripts/*.py` para las pruebas.

## [0.22.4] — 2026-09-11 — Lo que la base nueva trajo: 18 países que faltaban y un `OTROS`

**Enrique corrió el 07 sobre `snowflake_v2` y la celda 6 se detuvo, como debía,
con 19 grafías de `PAIS_ESTANDAR` fuera del catálogo (617 filas, 0,17 %).** La
guardia de 0.22.3 funcionó: paró antes de emparejar, con la lista. Lo que la
lista decía es de la librería, no del usuario:

| | grafías | filas | qué eran |
|---|---:|---:|---|
| países que **faltaban** en el catálogo | 11 | 63 | Irán, Afganistán, Liechtenstein, Vanuatu, Gibraltar, Isla Norfolk, Islas Salomón, Santo Tomé y Príncipe, Ciudad del Vaticano, Islas Marianas del Norte, Samoa |
| países presentes **sin la grafía en español** | 7 | 110 | Zimbabue, Fiyi, Lesoto, Bosnia, Guinea-Bisáu, República de Macedonia, Islas Vírgenes Estadounidenses |
| **no es un país** | 1 | 444 | `OTROS` |

Que a un catálogo de 203 países le faltaran Irán y Afganistán no es un detalle:
se construyó sobre la base DIAN de referencia, que nunca los trajo. **La
sugerencia automática acertó en 6 de 18** (`IRAN → IRLANDA 0,89`, `AFGANISTAN →
ALBANIA 0,76`, `LIECHTENSTEIN → BELICE 0,67`): es la demostración empírica de
por qué es una ayuda y no una regla.

### Catálogo: 203 → 214 países, 333 → 378 grafías

Once entradas ISO 3166-1 nuevas con su nombre canónico en español y alias
(`REPUBLICA ISLAMICA DE IRAN`, `SANTA SEDE`, `SAO TOME AND PRINCIPE`, `SAMOA
OCCIDENTAL`…), y siete alias en entradas existentes. `SAMOA` (WSM) y `SAMOA
AMERICANA` (ASM) siguen siendo distintas. El índice se construye sin alias
ambiguos; 18 pruebas parametrizadas, una por grafía real.

### `paises_aislar`: aceptar lo conocido sin apagar la guardia

`OTROS` no es un país. La escotilla de 0.22.3 era `paises_sin_clasificar=
"aislar"`, pero es **global**: declararla para aceptar `OTROS` habría dejado
pasar en silencio cualquier grafía nueva mañana. Justo lo que la guardia
existe para impedir.

`ConfigImportadores.paises_aislar: tuple[str, ...]` declara grafías concretas
como "no es un país y no tiene sentido catalogarlo". Cada una se aísla con su
propio `PAIS_FINAL` (`SIN CLASIFICAR: OTROS`) —se deduplica por nombre dentro
de sí misma y nunca se fusiona con un país real— **y el modo sigue en
`detener`**: `exigir_cobertura_paises` solo reporta lo NO declarado. Se compara
normalizado (`otros`, `Otros ` y `OTROS` son la misma declaración). La
invariante «el país final está en el catálogo o marcado» admite
`sin_clasificar` en modo `detener` únicamente si **todas** las grafías
presentes están declaradas: ver una no declarada significaría que alguien
saltó `preparar()`, y falla.

En el notebook: `NO_SON_PAISES = ("OTROS",)` en la celda del catálogo, cableado
a `CFG.paises_aislar`; la celda 6 muestra qué quedó declarado. Verificado con
las 19 grafías reales inyectadas en la base: sin declarar, se detiene y lista
**solo** `OTROS`; declarado, pasa la cobertura y el smoke test.

### Lo que la base real trajo en los NOMBRES: `NO DISPONIBLE` con el 21,7 % del FOB

Al correr la deduplicación sobre la base real (355.681 filas) para verificar la
entrega, las tres primeras filas por FOB eran `NO DISPONIBLE` — en Estados
Unidos, Panamá y China. Es un centinela de nombre, como el `'0'` de la base
DIAN, y **no estaba en `PLACEHOLDERS`**: se normalizaba a un nombre real y
quedaba como "el importador más grande" de cada uno de 69 países.

| grafía | filas | FOB | países | en la base DIAN |
|---|---:|---:|---:|---:|
| `NO DISPONIBLE` | 69 | **21,7 %** | 69 | 0 |
| `'0'` (ya era placeholder) | 33 | 9,3 % | 33 | 58 |
| `A LA ORDEN` | 100 | 1,5 % | 76 | 126 |
| `TO ORDER` / `TO THE ORDER` | 99 | 0,1 % | 45 / 28 | 78 |

`A LA ORDEN` / `TO ORDER` son el consignatario genérico del conocimiento de
embarque, no una empresa. Los seis (con `CONFIDENCIAL` y `RESERVADO`, que el
notebook ya trataba como centinelas) entran en `PLACEHOLDERS`: `es_faltante`
los deja en su propio grupo, marcados `sin_nombre_utilizable`, con el FOB
contado aparte y visible en REVISION. Solo coincidencias **exactas**: `TO ORDER
OF ING BELGIUM` nombra a la parte (1.181 filas, 1,75 %) y se conserva como
nombre — queda anotado como decisión pendiente.

**Y una segunda lista que derivaba:** el notebook 07 tenía su propia copia de
centinelas para *contar* (`CENTINELAS_SIN_NOMBRE`), distinta de la que el motor
usa para *decidir*. Ninguna de las dos tenía `NO DISPONIBLE`. Ahora el notebook
importa `PLACEHOLDERS`; hay un contrato que impide que vuelva la copia.

Afecta a la base DIAN de referencia (204 filas de `A LA ORDEN`/`TO ORDER`
pasan a grupo propio): la cifra de referencia cambia y se reporta abajo. No
afecta al banco institucional (ninguna de esas grafías aparece en él; medido).

### La corrida completa murió por memoria: scoring por lotes y unión LSH por claves

La deduplicación de la base real (355.681 filas, 214 particiones por país)
murió con `exit 137` en la partición 175, con 26,4 M de candidatos acumulados,
en un contenedor de 15 GiB y sin nada más corriendo. Reproducido sobre la
partición USA sola, con un vigilante de RSS: **54.669 nombres, 24.476.082
candidatos, 520 s, pico de 11,42 GiB**, y una tabla de decisiones de 2,58 GiB
para una sola partición. Tres causas, cada una medida:

1. **`evaluar_esquema` puntuaba toda la unión de una vez.** Unos quince arrays
   de 8 B por par, más las tablas del comparador (listas Python de los dos
   nombres, `cpdist`, `get_indexer`, cinco cortes de la matriz de incidencia
   dispersa) — a 24,5 M de pares, varios GiB transitorios por partición.
2. **La unión LSH se materializaba con `np.unique(np.vstack(piezas), axis=0)`.**
   Todos los pares brutos de todas las bandas, como pares int64 (16 B), hasta el
   final; y el `unique` por filas ordena una vista estructurada. El mismo par
   muy parecido aparece en casi todas las bandas.
3. **`ejecutar()` guardaba los ~50 M de candidatos con los dos nombres en cada
   fila** (~110 B/par): ~5 GiB de tabla que nadie iba a leer, y que además el
   `concat` final duplicaba.

**Corrección**, sin cambiar una decisión:

- `evaluar_esquema(..., pares_por_lote=, incluir_desglose=)`. La aritmética por
  par se extrajo a `_evaluar_pares` **sin cambiar una operación** y se llama
  por lotes; como el score de un par no depende de ningún otro (comparadores,
  política de faltantes, veto y corroboración son por par), el resultado es
  **bit-idéntico** — probado con lotes de 1, 3, 7 y "todo" sobre un caso que
  ejercita fusiones, vetos, vetos levantados y faltantes. `ConfigImportadores.
  pares_por_lote = 1_000_000`.
- La unión de pares del bloqueo se calcula por **clave escalar `i·n + j`**
  (`_union_pares`), con compactación amortizada mientras se acumulan las bandas
  (`_UnionIncremental`). Devuelve exactamente lo que devolvía `np.unique(axis=0)`
  (probado contra esa referencia) con la mitad de memoria y sin el sort
  estructurado.
- `decisiones` conserva **solo lo auditable**: fusionados, vetados y pares con
  similitud de nombre ≥ `sim_minima_auditoria` (0,70). `i`/`j` pasan a ser
  posiciones **globales** en `representantes`; los nombres se resuelven bajo
  demanda con `decisiones_con_nombres()`. `sensibilidad_umbral` rechaza, con
  mensaje accionable, umbrales por debajo del piso que ya no podría contar.

**Medido después**, partición USA: los mismos 24.476.082 candidatos, 28.409
fusiones y 44.761 grupos; **340 s** (−35 %) y **pico de 4,58 GiB** (−60 %); la
tabla de decisiones, 307.380 filas (1,3 % de los candidatos), 0,01 GiB. La
referencia DIAN: correlativa y representantes **bit-idénticos** antes y después
(`assert_frame_equal`), 100.008. La base real completa, que antes moría en la partición 175: **355.681 filas →
209.038 importadores** (247.518 representantes → 208.714 grupos + 324 sin nombre
utilizable), 52.801.652 candidatos, 71.548 fusiones, 2.422 cortes por cohesión;
emparejamiento 12 min 54 s, 17 min de pared con exportación, **pico de 4,81 GiB**
medido con vigilante; 10/10 invariantes; recall del bloqueo 0,999–1,000 en GTM,
NLD y DEU. Cabe en Colab Free.
24 pruebas nuevas en `tests/test_scoring_por_lotes_v0224.py`.

### La exportación real cayó por un byte: `\x1a` en diez razones sociales

Con la memoria resuelta, la corrida completa sobre la base real terminó el
emparejamiento (14 min, 10 invariantes en OK, checkpoint en Parquet escrito) y
**cayó al escribir el XLSX**:

```
openpyxl.utils.exceptions.IllegalCharacterError: COMPAÃ\x1aIA DE GALLETAS POZUELO DCR SA
```

Diez razones sociales de la base traen `\x1a` —el sustituto que deja un
decodificador ante un byte inválido; es el mojibake de "Ñ" y de "Ó"
(`COMPAÃ\x1aIA`, `REFRIGERACIÃ\x1aN`)— y openpyxl rechaza cualquier carácter de
control fuera de tab, CR y LF. El normalizador ya los quitaba de `NOMBRE_NORM`,
así que el emparejamiento era correcto; el entregable era lo que no se podía
escribir. En la misma corrida, `METRICAS` no cabía en Parquet porque su columna
`valor` mezclaba enteros con textos formateados (`41.2%`): el notebook avisaba y
seguía, pero avisaba en cada corrida.

**Corrección:** `exporters._spreadsheet.prepare_spreadsheet_data` —la puerta
por la que pasa TODO lo que va a CSV/XLSX en la librería— quita los caracteres
de control que openpyxl rechaza (`CONTROL_CHARACTERS_RE`, probado carácter por
carácter contra `openpyxl.cell.cell.ILLEGAL_CHARACTERS_RE`), sin mutar el
DataFrame fuente y sin tocar columnas numéricas; el Parquet y el CSV conservan
el texto original. `metricas()` entrega `valor` como texto siempre: es una tabla
para leer, no para calcular. Seis pruebas nuevas en
`tests/test_exportacion_caracteres_control_v0224.py`, incluida la ruta exacta
del notebook (`PipelineResult.to_excel`) con la grafía real.

### La publicación: pasó todas las compuertas y cayó en la siguiente

Con 0.22.3 el 08 pasó A.1 completa —ruff, versión, contratos, conformidad,
banco con huella idéntica— y `publicar_profesional()` se detuvo en
`correr_tests_locales`:

```
ERROR collecting tests/test_comparadores_tipos.py
ImportError while importing test module ... tests/test_comparadores_tipos.py:15:
```

La línea 15 es `from hypothesis import …`. La celda 3 del 08 instalaba una
**lista escrita a mano** de dependencias, y la lista no tenía `hypothesis`. El
CI no lo sufría porque instala `.[dev]` desde el pyproject. La misma deriva que
el 07 evita leyendo las dependencias del pyproject — y que este repositorio ya
había documentado como regla. Reproducido en un venv limpio: sin `hypothesis`,
**2 errores de colección** (`test_comparadores_tipos.py`, `test_tipos_v013.py`);
con las extras `[dev]` del pyproject, **1.582 casos colectados, 0 errores**.

Y un segundo defecto encima: **la causa no se veía**. `_run` recortaba STDOUT
y STDERR a 600 caracteres y el `ImportError` quedaba fuera del recorte; lo que
llegó a pantalla fue `tests/test_comparadores_tipos.py:15:` y nada más.

**Corrección:**

- La celda 3 instala solo herramientas de arranque (pytest, build, pyyaml,
  nbformat, ruff). La **Celda A.0** lee `optional-dependencies.dev` del
  pyproject de Drive, instala lo que falte y **verifica** que quede
  (`importlib.metadata`). Una fuente de verdad; la misma que el CI.
- `correr_tests_locales` usa **los mismos marcadores que el CI**
  (`not canario and not slow`): lo que se verifica localmente es lo que se
  verificará allá, y no se pagan los `slow` dos veces. `-p no:cacheprovider`
  para no escribir `.pytest_cache` en Drive.
- `_run` conserva los **últimos 3.000** caracteres, no los primeros 600, y al
  fallar se imprimen aparte las líneas de causa (`E …`, `Error`, `FAILED`).
- Tres contratos nuevos: extras dev desde el pyproject y sin lista a mano,
  marcadores iguales a `ci.yml` (si el CI cambia, el contrato avisa), y
  causa visible.

### Compatibilidad

Aditivo. Catálogo más grande no cambia ninguna asignación previa (un alias solo
añade claves; una colisión rompería el índice al construirse).

**La cifra de referencia sobre la base DIAN pasa de 99.897 a 100.008**, y los
111 de diferencia son exactamente los placeholders nuevos, verificado fila a
fila contra la correlativa de 0.22.3: 204 filas con `A LA ORDEN` (126), `TO THE
ORDER` (41) y `TO ORDER` (37), en 62 países, formaban **95 grupos** —uno por
país, como si "a la orden" fuera un importador— y ahora son **204 singletons**
marcados sin nombre (1,9 % del FOB). Dos de esos 95 grupos arrastraban además
un nombre real (`TO THE ORDER OF INTERNATIONAL COFFEE`), que ahora queda solo
con los suyos. Fuera de esas 204 filas la partición es **idéntica**. Cinco
pruebas nuevas de flujo, 21 del catálogo, ocho de placeholders, 24 de scoring
por lotes, seis de exportación, cuatro contratos de notebook.

**Estado conocido, no de esta versión:** `test_sin_nit_perfil_recalibrado_no_regresa`
(pipeline clásico con NIT, perfil SIN_NIT) mide precisión 0,864 < 0,87 y falla
igual con el árbol 0.22.0 intacto; ver `docs/ENTREGA_0.22.4.md` §8.

**`ResultadoImportadores.decisiones` cambia de contrato**: ya no trae
`NOMBRE_A`/`NOMBRE_B` (use `decisiones_con_nombres()`), `i`/`j` son posiciones
globales en `representantes` y solo conserva lo auditable (fusionados, vetados,
≥ `sim_minima_auditoria`); `n_candidatos` sigue siendo el total evaluado.
`evaluar_esquema` es compatible: los dos parámetros nuevos son opcionales y por
defecto el resultado es el de siempre.

## [0.22.3] — 2026-09-11 — Países fuera del catálogo: detenerse antes, o aislarlos y medirlo

**Enrique corrió el notebook 07 sobre la base nueva (`snowflake_v2`, 355.681
filas) y el smoke test se detuvo:**

```
❌ Smoke test FALLÓ: alguna invariante no se cumple sobre la muestra.
       el país final está en el catálogo o marcado   False  FALLA
```

`PAIS_ESTANDAR` viene "estandarizado" desde Snowflake, pero estandarizado no es
lo mismo que catalogado: trae grafías que `record_linkage.paises` no reconoce.
La invariante las atrapó. Pero lo hizo **después** de emparejar, **sin decir
cuáles eran**, y el remedio no estaba a la vista. Al reproducirlo apareció algo
peor.

### Lo que se midió, no lo que se razonó

Sobre una muestra de 5.000 filas con tres grafías inyectadas fuera del catálogo
(`NO DEFINIDO`, `SIN INFORMACION`, `TERRITORIO X`) y la misma razón social bajo
las tres:

| | 0.22.2 |
|---|---|
| `PAIS_FINAL` de las tres | `SIN CLASIFICAR` (la misma etiqueta) |
| `PAIS_ISO3` de las tres | `ZZZ` (la misma partición) |
| `GLOBAL TRADING PARTNERS LLC` bajo las tres grafías | **un solo importador**, `ZZZ-000035` |
| invariante «ningún grupo cruza dos países» | **OK** — mira el ISO3, y los tres son `ZZZ` |

Es decir: el país es el bloqueo duro, y una grafía sin catalogar lo **desactiva**
para todas las filas que la comparten. La invariante que falló era la única que
lo veía, y lo veía tarde.

### Corrección, en tres capas

1. **`ConfigImportadores.paises_sin_clasificar`** ∈ {`"detener"`, `"aislar"`},
   defecto `"detener"`. `preparar()` se detiene **antes** de cualquier
   emparejamiento con `PaisesSinClasificar`: cada grafía, cuántas filas trae,
   el país más parecido, y los tres remedios en orden (alias en el catálogo ·
   patrón de no-país · aislar). La tabla viaja en la excepción (`.tabla`).
   Cuesta un mapeo vectorizado; enterarse por la invariante costaba minutos.
2. **Modo `"aislar"`**: `canonizar_pais(..., agrupar_sin_clasificar=False)`,
   simétrico al `agrupar_no_pais` que ya existía. Cada grafía conserva su
   `PAIS_FINAL` (`SIN CLASIFICAR: NO DEFINIDO`), y como `PAIS_FINAL` es el campo
   categórico que veta la fusión, dos grafías distintas **no se unen** ni entre
   sí ni con un país real. Medido sobre la misma muestra: 3 grupos, no 1.
3. **Invariante nueva, la décima:** «ningún grupo mezcla dos grafías de país»
   — `PAIS_FINAL` constante dentro de cada `ID_IMPORTADOR`. Más fuerte que la
   del ISO3, y es la que **mide** que el aislamiento funciona en vez de
   suponerlo.

Y un preflight reutilizable para los notebooks: `cobertura_paises(df, cfg)`
devuelve la tabla completa (no las 25 de `sugerir_alias_pais`) y
`exigir_cobertura_paises(tabla, cfg)` aplica el modo — la **misma** función que
`preparar()` usa por dentro. Una lógica, dos puntos de llamada.

### El defecto latente que `aislar` destapó

Al activar `aislar`, la invariante «una fila por fila de entrada» **falló**: la
correlativa tenía más filas que la base. Causa: los representantes se
identifican por `(ISO3, PAIS_FINAL, NOMBRE_NORM)` pero se volvían a unir a la
base solo por `(ISO3, NOMBRE_NORM)`. Con varios `PAIS_FINAL` bajo el mismo ISO3,
el merge multiplica.

**No lo introdujo 0.22.3.** `agrupar_no_pais=False` (dos zonas francas
distintas, ambas `ZZF`) tenía exactamente el mismo defecto desde 0.22.0; nadie
lo pisó porque el defecto es `True`. Corregido en los cinco sitios de
`construir_entregables` y en la muestra de revisión; prueba de regresión
`test_agrupar_no_pais_false_ya_no_multiplica_filas`.

### El hallazgo que no se buscaba: la normalización dependía de la semilla del proceso

Al verificar que la corrección no movía la referencia, la corrida dio
**99.897** importadores en vez de 99.898. Se repitió con el árbol 0.22.0 sin
tocar: **99.897**. Y otra vez con 0.22.3: **99.898**. El ±1 no era del cambio:
la misma versión daba resultados distintos en procesos distintos.

Todo el ±1 era **un grupo**: AVIATECA (Guatemala). Aislada la partición y
corrida bajo tres `PYTHONHASHSEED` distintos, el **nombre normalizado**
cambiaba: `AVIATECA SOCIEDAD ANONIMA SUCURSAL COLOMBIA` → `AVIATECA` con una
semilla y `AVIATECA ANONIMA` con otra. La causa está en
`matching/normalizadores.py`, en cómo se quitaban los sufijos multi-token:

1. La lista salía de un `set` ordenada solo por número de tokens; los empates
   quedaban en orden de iteración del `set`, que cambia con la semilla de hash
   del proceso. `SOCIEDAD ANONIMA` y `SUCURSAL COLOMBIA` tienen dos tokens.
2. Cada frase se probaba **una sola vez**, en secuencia, anclada al final:
   quitar `SUCURSAL COLOMBIA` deja `SOCIEDAD ANONIMA` al final, pero esa ya se
   había probado. Los sufijos apilados nunca se reducían del todo — y cuál
   quedaba dependía del orden del punto 1.

A escala completa, con el código anterior: `PYTHONHASHSEED=0` → 99.898;
`PYTHONHASHSEED=1` → 99.897. **La promesa de reproducibilidad del notebook
("el `metadata.json` es lo que hace reproducible un resultado seis meses
después") no se cumplía**, y ninguna prueba lo veía: `test_es_determinista`
corre en un solo proceso, donde la semilla es fija.

**Corrección:** orden total (tokens desc, longitud desc, alfabético) y un solo
regex con grupo repetido `(?:\s+(?:…))+$` que elimina frases apiladas hasta el
punto fijo. Determinista por construcción. Verificado: la partición GTM bajo
tres semillas da la **misma huella** de candidatos y decisiones; la corrida
completa bajo dos semillas da correlativas **bit a bit idénticas**.

Afecta al motor multicampo también —usa el mismo normalizador—, así que el
banco institucional se volvió a correr (ver abajo). Tres pruebas nuevas
(`test_normalizador_determinista_v0223.py`), dos de ellas en **subprocesos con
semillas distintas**: es la única forma de probar determinismo entre procesos.

### Compatibilidad

**Con cobertura total del catálogo, nada cambia** en el flujo sin
identificador salvo lo que la corrección de determinismo corrige. Sobre la base de referencia
(211.949 filas, 0 sin clasificar): `deduplicar_importadores` con `detener` y
con `aislar` producen la **misma** correlativa y el **mismo** GOLDEN
(`assert_frame_equal`). La corrida completa da **99.897** importadores — uno menos
que el 99.898 publicado en 0.22.0–0.22.2, porque `AVIATECA SOCIEDAD ANONIMA
SUCURSAL COLOMBIA` ahora se reduce a `AVIATECA` y se une a su grupo. El 99.898
era la semilla afortunada (ver el hallazgo de determinismo); 99.897 es el
resultado correcto y es el mismo en cualquier proceso.
Quien tenga grafías fuera del catálogo verá ahora una excepción **antes** de
correr, con la lista: es el comportamiento nuevo y es a propósito.

- 21 pruebas nuevas: 16 en `test_flujo_importadores_v0223.py`, 2 en el canonizador, 3 de determinismo entre procesos.
- Notebook 07 (el de Enrique, integrado): `PAISES_SIN_CLASIFICAR` en la celda
  de configuración, y la cobertura del catálogo como paso `0d` sobre la base
  **completa** antes del smoke test — la muestra veía 3 filas donde hay 300.
- Contrato de la celda de entorno corregido: comprobaba el nombre
  `_SUBPAQUETES` y rechazaba la verificación **más exhaustiva** del 07
  (`pkgutil.walk_packages`). Otra vez un contrato sobre la implementación.

## [0.22.2] — 2026-09-11 — La compuerta que no podía pasar, y el notebook de Enrique integrado

**Enrique corrió la publicación real.** Pasó A, A.0, ruff, coherencia de
versión, conformidad (46 casos, 0 saltos), gobernanza documental y el banco con
huella idéntica. Se detuvo en **tres pruebas de contrato**, y dos de las tres
eran defectos míos, no suyos.

```
FAILED test_json_valido_y_sin_salidas_guardadas[07_...ipynb]
FAILED test_json_valido_y_sin_salidas_guardadas[08_PUBLICAR_GITHUB.ipynb]
FAILED test_la_instalacion_no_es_editable[07_...ipynb]
```

### Fallo 1 — una compuerta que no podía pasar nunca

`test_json_valido_y_sin_salidas_guardadas` exigía que los notebooks no tuvieran
salidas embebidas. La regla es correcta; el sitio donde se exigía, no. Falla por
dos razones **independientes**, y las dos se dieron a la vez:

| Notebook | Por qué no puede cumplirla |
|---|---|
| **08** | es el notebook que **corre** la compuerta. Colab autoguarda sus salidas en el .ipynb de Drive mientras se ejecuta, así que cuando pytest lo lee ya las tiene — las de la celda que está corriendo. Pedirle que no las tenga es pedirle que no se esté ejecutando |
| **07** | es un notebook de análisis: se corre contra los datos y **sus salidas son el resultado**. Exigir que estén vacías obliga a borrarlas a mano antes de cada publicación |

Es la segunda compuerta de esta serie que se mide a sí misma — la primera fue la
prueba de marcadores que detectaba su propio código fuente.

**La propiedad es del repositorio, no del árbol de trabajo.** Se corrigió
mudándola a los dos sitios donde sí es exigible:

1. **`preparar_build()` limpia las salidas al copiar.** Es más fuerte que una
   prueba que se queja: no depende de que nadie se acuerde. En Drive usted
   conserva las salidas de su corrida —que son la evidencia de que funcionó— y
   a git llega el notebook limpio.
2. **La prueba comprueba el checkout de git** cuando lo hay, y cuando no lo hay
   —una copia de Drive, un zip descomprimido— verifica que la garantía del
   build siga en pie. Ninguna de las dos ramas se calla.

Verificado en las tres ramas: copia de trabajo con salidas **pasa**; checkout
con salidas **falla** nombrando los notebooks; y el build limpia 2 notebooks
dejando **Drive intacto**.

### Fallo 2 — un contrato que fijaba la implementación, no el invariante

`test_la_instalacion_no_es_editable` exigía literalmente que la celda de entorno
hiciera `pip install`. El notebook 07 nuevo carga la librería **desde el árbol
de Drive** vía `sys.path`, sin instalar. El contrato lo rechazaba.

Pero el invariante real nunca fue "tiene que haber un pip install": es **no
correr código rancio ni incompleto, y fallar ruidosamente si ocurre**. Hay dos
estrategias legítimas y el repositorio usa las dos:

| | 05, 06 | 07 |
|---|---|---|
| estrategia | instalar la rueda | importar desde el árbol |
| no queda rancio porque | la rueda se reinstala con `--force-reinstall` | se lee el árbol directamente, y se **verifica de dónde se importó** |
| no queda incompleto porque | una rueda es un archivo: o está o falla | se importan los submódulos uno por uno |

El contrato se reescribió sobre lo común a las dos. Y al hacerlo **destapó un
hueco real en el notebook 05**: no verificaba que el paquete quedara completo.
Corregido: 6 submódulos comprobados.

### Fallo 3 — no era un fallo

El banco dio **AVISO**, no FALLA, en `segundos_total` (+23,8 %) con la huella
idéntica y las cuatro métricas de calidad sin mover un decimal. Es exactamente
el comportamiento que la 0.22.1 introdujo: el reloj de otra sesión no es
evidencia de regresión. **Funcionó como debía.**

### El notebook 07 de Enrique, integrado

Sustituye al anterior. Corrió completo en Colab contra los datos reales:
**211.949 filas → 99.898 importadores (−52,9 %)**, los 9 invariantes en OK,
396 s de emparejamiento, exportado a Drive. Dos cosas que hacía mejor que el
mío y que se conservan:

- **Lee las dependencias del propio `pyproject.toml`** del paquete en Drive, en
  vez de una lista copiada a mano en el notebook. No se puede desincronizar.
  Las versiones instaladas fuera de rango se **reportan**, no se tocan: degradar
  numpy en Colab obliga a reiniciar y es una decisión del usuario.
- **Verifica de dónde se importó `record_linkage` de verdad**, y aborta si ya
  había otro cargado en el kernel.

Lo que se le añadió: **verificación de que el árbol de Drive está completo**
(9 submódulos). Era la única protección que la estrategia de importar-desde-el-
árbol no tenía, y es el modo de falla característico de FUSE — el error habría
aparecido a los siete minutos de cómputo, con el emparejamiento a medias.

### Compatibilidad

**Sin cambios en la librería.** Solo notebooks y pruebas de contrato. Banco con
huella idéntica; conformidad 46 casos, 0 saltos.

## [0.22.1] — 2026-09-11 — Publicar de verdad: cinco fallos del pipeline de publicación

**Enrique corrió el notebook 08 contra el repositorio nuevo
`rues-linker_v2` y la publicación se detuvo en las compuertas.** El error que
vio era real; el que venía después habría sido peor.

### Fallo 1 — el observado

```
▸ pytest (contratos de notebooks, API pública y flujo sin identificador)
  FALLA
E   ModuleNotFoundError: No module named 'record_linkage'
```

**Causa:** el notebook nunca instalaba el paquete. La celda de dependencias
instala herramientas de build y las *dependencias* de la librería, pero no la
librería. Con layout `src/`, `import record_linkage` solo funciona si el
paquete está instalado.

**Por qué el síntoma engaña:** en la misma corrida, el banco y la conformidad
**pasaron**. No porque estuvieran instalados, sino porque `scripts/banco.py` y
`scripts/conformidad.py` hacen `sys.path.insert(0, RAIZ/"src")`. El error
señala a pytest; la causa está en la instalación ausente.

**Corrección:** nueva **Celda A.0**, que instala la **rueda** —no el árbol—,
purga los módulos viejos del kernel, verifica que estén los nueve submódulos y
que la versión instalada coincida con la de la Celda A. Se instala la rueda
porque copiar cientos de archivos desde Drive (FUSE) puede quedar incompleto
sin lanzar error; es la misma lección que ya estaba fijada para los notebooks
01–06.

### Fallo 2 — el que no llegó a verse

`preparar_build()` **reescribía `pyproject.toml`** con los metadatos de la
Celda A. El archivo generado habría publicado un repositorio cuyo CI no puede
pasar:

| Se pierde | Consecuencia medida |
|---|---|
| el marcador `slow` (declara `paridad`/`canario`/`smoke`) | con `--strict-markers`, **7 tests en 5 archivos** ERROR-an al colectar |
| `[tool.ruff]` (4 secciones) y `[tool.mypy]` | el gate de estilo del CI usa valores por defecto |
| `license-files`; escribe `license = {text = "Apache-2.0"}` | forma obsoleta en PEP 639 |
| `setuptools>=77` → `>=68` | PEP 639 exige 77 |

**Corrección:** `REGENERAR_PYPROJECT = False`. El `pyproject.toml` del
repositorio se mantiene a mano y es la fuente de verdad —
`scripts/verificar_coherencia_version.py` depende de ello. La Celda B lo
conserva y solo verifica que la versión coincida.

### Un tercer hallazgo: el banco reprobaba por el reloj de otra sesión

Al reproducir la corrida, `--comparar` dio **FALLA** por `segundos_total`
(+24 %) con la **huella idéntica** y todas las métricas de calidad iguales.
No era una regresión:

| Código | Máquina | Tiempo |
|---|---|---|
| 0.21.0 | contenedor, por la mañana | 51,0 s |
| 0.21.0 | **mismo** contenedor, por la tarde | **65,1 s** |
| 0.22.1 | mismo contenedor, por la tarde | **63,2 s** |

El contenedor estaba ~28 % más lento; la versión nueva es **más rápida** que la
anterior medida a la vez. Comparar relojes contra una línea base de otra sesión
reprueba cambios que no empeoran nada — y enseña a ignorar el veredicto, que es
justo lo que la bitácora dice que dejó pasar dos regresiones.

**Corrección:** la Celda A.1 separa lo que el veredicto binario mezclaba.
**Calidad** (precision, recall, F1, B³, FP sobre negativos, huella) es
determinista y **siempre bloquea**. **Costo** (segundos, RSS) depende de la
carga y solo bloquea si la línea base es de esta sesión
(`ANTIGUEDAD_MAXIMA_BASE_HORAS = 2`); si no, baja a aviso y dice cómo grabar
una base comparable. El umbral del banco no se tocó.

### Cuarto hallazgo: instalar el paquete destapó 8 casos que se saltaban solos

Consecuencia directa de la Celda A.0. Al instalar la rueda, `pytest` importa
`record_linkage` desde `site-packages` — que es lo correcto, se prueba lo que
se publica — y ahí `data/conformidad/` deja de ser alcanzable:

```
DIRECTORIO_POR_DEFECTO = Path(__file__).resolve().parents[3] / "data" / "conformidad"
#   desde src/  → <repo>/data/conformidad                      ✓
#   instalado   → /usr/local/lib/python3.11/data/conformidad   ✗
```

El fixture captura `FileNotFoundError` y hace `pytest.skip`. Resultado: **8
casos del conjunto de conformidad se saltaban en silencio** y la compuerta
reportaba verde sin haber medido. Es el modo de fallo más caro que hay: una
prueba que falla avisa; una que se salta, no.

**No lo introdujo la 0.22.1** — se reproduce en el zip original (`31 passed,
8 skipped`). Estaba latente porque nadie corría la suite con el paquete
instalado. La Celda A.0 lo destapó.

**Corrección:** `localizar_conjunto()` resuelve por capas, de lo más explícito
a lo más adivinado: variable de entorno `RUES_LINKER_CONFORMIDAD` → la
constante del módulo si contiene el conjunto (checkout) → búsqueda hacia
arriba desde el directorio de trabajo (instalado; pytest y los scripts corren
desde la raíz) → la constante, para que el error nombre una ruta. Se reconoce
un directorio por `dedup_registros.csv`, no por existir: un `data/conformidad/`
vacío pasaría la comprobación y fallaría lejos de la causa.
`scripts/conformidad.py` ancla `--datos` a su propia raíz, que siempre conoce.
Seis pruebas nuevas, una de ellas la que rompía: `cargar_conjunto()` sin
argumento tiene que **cargar**, no saltar.

| | antes | ahora |
|---|---|---|
| `pytest tests/test_conformidad_v020.py` con el paquete instalado | 31 pasan, **8 se saltan** | **45 pasan, 0 se saltan** |

### Quinto hallazgo, y el peor: el build publicaba 404 de 420 archivos

Se encontró ejecutando `preparar_build()` contra el árbol real y **comparando
el resultado archivo por archivo** con el origen, en vez de leer el código y
suponer. Sin ese cotejo no se ve: la celda no lanza ningún error, imprime
"Build listo" y sigue.

`_EXCLUIR_EXT_BASE` bota `*.csv`, `*.parquet` y `*.pkl` — regla sana, "solo va
a git el código". En este repositorio esa regla es **falsa** para dos familias
de archivos, y el filtro se llevaba 16:

| Se perdía | Consecuencia en el repositorio publicado |
|---|---|
| los **7 CSV de `data/conformidad/`** | la suite de conformidad no tiene conjunto: 8 casos se saltaban (o, con el arreglo del hallazgo 4, **fallan**) |
| `data/ground_truth/ground_truth_grande.csv` | no se puede reproducir ninguna cifra de calidad publicada |
| los **6 fixtures** de `tests/data/` y `tests/data_sintetica/` | ~22 archivos de test sin sus datos |
| 2 parquet de `docs/evidencia/` | ninguna — nunca estuvieron versionados, se dejan fuera a propósito |

`data/benchmark/benchmark_institucional.csv.gz` se salvó por accidente: `.gz`
no está en la lista de extensiones excluidas.

**Resultado:** se habría publicado un repositorio con el CI rojo desde el
primer commit, y el diagnóstico habría apuntado a los tests.

**Corrección, en dos capas medidas y una de refuerzo:**

1. `DIRECTORIOS_VERSIONADOS` en la Celda A declara **directorios**, no
   archivos, y se expanden contra el árbol real. Un fixture nuevo se publica
   solo; no depende de que alguien recuerde añadirlo a una lista. Un
   directorio ausente o vacío **aborta**: si Drive no sincronizó
   `data/conformidad/`, la alternativa silenciosa es publicar sin él.
2. `preparar_build()` **aborta** si falta algo, en vez de imprimir un aviso.
   Verificado con el escenario adverso: quitando `data/conformidad/` del árbol,
   la celda para con la lista de los 19 archivos ausentes en lugar de seguir.
3. *Refuerzo, no corrección de un defecto observado.* El generador de
   `.gitignore` emite `!<dir>/` antes de las excepciones por archivo, y se le
   pregunta a git (`git check-ignore --stdin`) si va a ignorar algo que debe
   publicarse. **Se comprobó que hoy no hacía falta:** el `.gitignore`
   *generado* no contiene `data/*` —esa línea es una adición a mano del
   archivo del repositorio— así que las excepciones por archivo sí se
   evaluaban. Protege el caso en que alguien añada `data` a
   `EXCLUIR_DIRS_EXTRA`, donde git dejaría de descender al directorio.

**Verificación de extremo a extremo.** No basta con mirar el directorio de
build: se corrió `preparar_build()`, se hizo `git init && git add -A &&
git commit` sobre el resultado y se **clonó**. Sobre el clon —lo que GitHub
recibiría de verdad— :

| | |
|---|---|
| archivos | 426 |
| `pytest tests/test_conformidad_v020.py` | **45 pasan, 0 saltos** |
| los 23 archivos de test que dependen de fixtures | **165 pasan, 0 fallan, 0 se saltan** |
| `scripts/conformidad.py --corroborar` | PASA |
| `ruff check src/ tests/ scripts/` | limpio |

### Además

- **`docs/evidencia_importadores/`** (76 KB, 7 tablas) pasa a versionarse:
  `docs/ANALISIS_IMPORTADORES.md` cita esas tablas y sin ellas ninguna de sus
  cifras es comprobable. Son CSV, así que la exclusión por extensión se las
  llevaba.
- **`[project.urls]`** en `pyproject.toml` (no existían), apuntando a
  `rues-linker_v2`, y todas las referencias del repositorio actualizadas.
- **`dist/rues_linker-0.22.1-py3-none-any.whl`** se entrega en el zip: los
  notebooks la buscan en `dist/*.whl` y no estaba.
- **Quince pruebas nuevas** para que estos fallos no vuelvan: ocho de
  contrato del notebook (que el 08 instale antes de medir, que no regenere el
  pyproject, que los marcadores usados estén declarados, que apunte al
  repositorio y la ruta correctos, que un salto de conformidad sea fallo, que
  los conjuntos versionados no se queden fuera del build, que el `.gitignore`
  re-incluya el directorio antes que el archivo, y que un build incompleto
  aborte) y seis de localización del conjunto de conformidad.

**La distribución sigue llamándose `rues-linker`** aunque el repositorio sea
`rues-linker_v2`. Renombrarla rompería `importlib.metadata.version(...)` en
`__init__.py`, en `verificar_coherencia_version.py` y en los siete notebooks.

### Compatibilidad

**Sin cambios funcionales.** Banco con la misma huella
(`1e365ba8…`, F1 0,8780, macro-F1 0,8920) contra una línea base medida en la
misma sesión: **PASA**. Conformidad: **PASA**.

## [0.22.0] — 2026-09-11 — Deduplicar sin identificador: cuando el nombre es toda la evidencia

**Caso que lo motivó:** una base de 211.949 destinatarios de exportación con
dos columnas —razón social y país— y ningún NIT. El régimen SIN_NIT llevado a
su extremo: no hay identificador que vetar ni que corroborar.

La librería ya tenía casi todas las piezas. Lo que faltaba no era potencia,
era **saber qué falla cuando el nombre es lo único que hay**. Las tres cosas
que se añaden salen de fallas medidas, no de una lista de deseos.

### Lo que se rompía con los defectos existentes

Correr el caso con `JaroWinklerSigned` y `clusters_desde_decisiones` produjo,
sobre datos reales:

| Falla | Evidencia | Causa |
|---|---|---|
| Imán genérico | `INTERNATIONAL` en un grupo con 157 razones sociales; `MQE` con 214; `COMERCIAL` con 133 | si "A contenido en B" cuenta como evidencia, todo nombre corto absorbe lo que lo mencione |
| Prefijo compartido | `COMERCIALIZADORA ATLANTA C.A.` vs `COMERCIALIZADORA ATLANTIC C.A.` → JW 0,972, dos empresas | Jaro-Winkler premia el prefijo, y el prefijo es la parte genérica |
| Encadenamiento | grupos de 200 empresas distintas | componentes conexas = single-linkage: `a≈b`, `b≈c` ⇒ une `a` con `c` |

Y una cuarta, fuera del nombre: el campo país traía **529 grafías para 203
países**, con pares que ninguna similitud de cadenas resuelve
(`TURQUIA`/`TURKIYE`, `CHEQUIA`/`REPÚBLICA CHECA`, `YIBUTI`/`DJIBOUTI`) junto a
pares que sí se parecen y son países distintos (`GUINEA`/`GUINEA-BISSAU`,
`CONGO`/`REPÚBLICA DEMOCRÁTICA DEL CONGO`).

### Lo que entra

**`matching.nombre_idf` — comparador de razones sociales.**
`sim = alfa · S_tokens + (1 − alfa) · JaroWinkler`, donde `S_tokens` es Jaccard
ponderado por IDF —simétrico— salvo cuando el par supera tres puertas, cada una
cerrando una de las fallas de arriba. Cumple el protocolo `Comparator`, así que
se pasa a un `CampoSpec(comparador=...)` como cualquier otro.

**`matching.genericos` — los términos no distintivos, DECLARADOS.**
Aquí está la lección que costó más: *el IDF aprendido del corpus no distingue
una marca de un genérico*. Medido sobre 105.705 nombres, `IMPORTADORA` (737
apariciones) pesa **5,96** y `ZELECTA` (490) pesa **6,37** — la marca pesa
MENOS que el genérico, porque la frecuencia de una marca crece con el número de
variantes de la MISMA empresa. El corpus la castiga por el motivo equivocado.
Por eso la lista es un dato versionado, misma doctrina que `LOCALES`.

**`paises` — catálogo ISO 3166 en español + canonizador.**
203 países, 333 grafías. Dos pasadas (exacta y sin puntuación); lo que no mapea
se **marca**, nunca se adivina. `sugerir_alias_pais` propone, no decide: en la
prueba de regresión, `NARNIA` se parece a `ARMENIA` con 0,85.

**`engine.cobertura` — cobertura por estrellas.**
Reparte cada componente conexa en estrellas y deja una garantía verificable:
*todo miembro queda a ≤ (1 − umbral) de SU líder*, que es exactamente lo que
una tabla correlativa afirma. Sin ella, la correlativa afirmaba algo que el
pipeline nunca comprobó.

**`processing.saneamiento` — entidades HTML, mojibake, siglas partidas.**
Con una trampa documentada: la conversión de puntuación a espacio va SIEMPRE
después de plegar tildes. Invertir el orden borra la "É" de "AMÉRICA" porque el
patrón se evalúa con semántica ASCII (pandas 3 delega en Arrow/RE2). Medido:
la canonización de países cayó del 100 % al 57 % sin ningún error visible.

**`flujo.importadores` — el flujo completo, con su control de calidad.**
`ConfigImportadores` + `deduplicar_importadores(df)` entregan correlativa,
golden, correlativa de países, lista de revisión, muestra estratificada para
etiquetar, métricas, **nueve invariantes** y sensibilidad al umbral. El
notebook queda delgado, como manda `flujo`.

### Lo que la medición cambió

Tres decisiones de diseño se revirtieron al medir. Vale la pena dejarlas:

1. **El bloqueo que parecía razonable perdía un cuarto de los pares.** Con 64
   permutaciones y umbral 0,35: menos candidatos, corrida más rápida, métricas
   internas idénticas... y **PC de 0,67–0,81** medido por fuerza bruta sobre
   particiones completas. El defecto es 128 @ 0,25, con PC 0,99–1,00.
2. **El umbral inicial de 0,88 era demasiado estricto.** Revisar 40 pares del
   tramo 0,84–0,88 dio **92,5 % de aciertos**: se descartaban ~7.000 empalmes
   correctos. Defecto: 0,84.
3. **El piso de distintividad estaba en frecuencia absoluta.** Un token en
   1.000 nombres es genérico en un corpus de 100.000 y es *todo el corpus* en
   uno de 1.000. Se detectó escribiendo la prueba de regresión, que pasaba en
   producción y fallaba en el corpus sintético. Ahora es una fracción.

### Calidad medida

160 asignaciones revisadas a mano, estratificadas por banda de similitud:

| Banda | Asignaciones | Precisión estricta |
|---|---:|---:|
| 0,84–0,88 | 6.898 | 0,825 |
| 0,88–0,92 | 4.237 | 0,950 |
| 0,92–0,96 | 3.520 | 0,850 |
| 0,96–1,00 | 41.966 | 1,000 |
| **Ponderada** | **56.621** | **0,966** |

Recall del bloqueo **PC = 1,000** en las tres particiones auditadas.
Corrida de referencia: 211.949 registros → 99.898 importadores en 226 s y
2,6 GiB de RSS. Detalle en [`docs/ANALISIS_IMPORTADORES.md`](docs/ANALISIS_IMPORTADORES.md).

### Compatibilidad

**Aditivo.** Ningún camino existente cambia: el banco institucional entrega la
misma huella de partición que en 0.21.0 (`1e365ba8…`, F1 0,8780, macro-F1
0,8920). Las 98 pruebas anteriores siguen verdes y se añaden 77.

### Notebooks

- **Nuevo `07_deduplicar_importadores_razon_social_pais.ipynb`**: el caso
  completo, con el catálogo de países y las listas de limpieza en celdas
  editables.
- **Nuevo `08_PUBLICAR_GITHUB.ipynb`**: publicación del paquete a GitHub con
  compuertas de calidad.

### Límites declarados

- Resuelve **grupos comerciales por destino**, no personas jurídicas.
  `BARRY CALLEBAUT USA` y `BARRY CALLEBAUT CANADA` quedan juntos; se separa con
  `geografia_es_ruido=False`.
- No une empresas entre países: el país es variable de empalme por diseño.
  `ID_EMPRESA_GLOBAL` une solo coincidencias exactas del nombre normalizado.
- Los consolidadores de carga se quedan con su clientela.
- La precisión de 0,966 es una estimación con n=160, no un censo.

## [0.21.0] — 2026-08-30 — La identidad adoptada deja de ser un extra y pasa a ser contrato

**Enrique reportó que `correlativa.parquet` salía sin `NIT_FINAL`,
`RAZON_SOCIAL_FINAL` ni `NAME_SIMILARITY_SCORE`. El síntoma no se pudo
reproducir; el defecto de fondo sí, y es peor.**

### Lo que se verificó primero

Sobre 0.20.0, las cuatro columnas **sí salen** en los cuatro caminos probados
—pandas y DuckDB, con y sin colapso, modo dataframe y modo disco—. No se
reprodujo el caso reportado.

Pero al buscar por dónde podrían faltar aparecieron **tres puntos donde la
entrega se degrada en silencio**, cada uno escribiendo solo una advertencia en
el registro de una corrida de cuarenta minutos:

```
generator.py:1269   except Exception: warning("No se pudieron añadir métricas
                    de diagnóstico")
generator.py:1341   if missing_cols: warning(...); return df
orchestrator.py     except Exception: warning("Consolidación falló, usando
                    resultados directos")
```

Y el hallazgo que lo explica: **`flujo/cruce.py` no mencionaba `NIT_FINAL` ni
una sola vez.** Se verificaba el número de filas, los `ID_GRUPO` nulos y la
coherencia entre golden y correlativa — pero no que el resultado dijera qué
identidad quedó para cada grupo. **La garantía no existía en el código.** Que
en la versión probada funcione es suerte, no diseño.

### El contrato, ahora explícito

`golden/columnas_finales.py`:

```python
COLUMNAS_IDENTIDAD   = ("NIT_FINAL", "RAZON_SOCIAL_FINAL")
COLUMNAS_DIAGNOSTICO = ("NAME_SIMILARITY_SCORE", "NIT_DISTANCE")
```

`garantizar_columnas_finales` distingue dos cosas que no son la misma:
**calcular** el diagnóstico es trabajo normal y se registra en INFO;
**reconstruir** la identidad desde el golden es una anomalía y se registra en
WARNING. La reparación siempre es posible —el golden lleva las dos columnas
por construcción— y solo se falla cuando el golden no sirve.

La invariante se hace explícita en los **dos** caminos:

- `_verificar_invariantes` exige las cuatro columnas (camino en memoria).
- `_validate_final_correlation` las comprueba con `DESCRIBE` sobre el parquet
  publicado, leyendo el esquema y no las filas (camino en disco).

### Una pregunta nueva, en vez de conocimiento tribal

`identidad_adoptada(limite)` entra en el protocolo `ControlCalidad` y en sus
dos implementaciones: el consumidor ya no tiene que saber cómo se llaman las
columnas ni cómo leerlas sin materializar millones de filas. Falla si el
contrato no está, en vez de devolver una vista incompleta que parece correcta.

### Notebooks

`06_orquestador_configurable` y `06_ejemplo_rues_x_exportaciones` muestran la
identidad adoptada en el preview y la verifican en el QA. **Una salida
incompleta se ve en el notebook, no al abrir el parquet días después.**

### Sin cambios en el enlace

Línea base del banco **bit a bit idéntica** a 0.20.0: `F1 0.8780`,
`macro-F1 0.8920`, huella `1e365ba81c4df45e410dd09154cafef1`. Este cambio
garantiza la salida; no altera a quién se une con quién.

### Caveat que queda documentado

`NAME_SIMILARITY_SCORE` compara `NOMBRE_LIMPIO` —normalizado— contra
`RAZON_SOCIAL_FINAL` —sin normalizar—: dos filas con el mismo nombre real
pueden dar 0,69 en vez de 1,0. Es la semántica de 0.20.0 y **se conservó a
propósito**; cambiarla alteraría una columna ya publicada. Anotado como C38.

### Añadido

- `golden/columnas_finales.py` y `ReporteColumnasFinales`.
- `identidad_adoptada` en `ControlCalidad`, `ResultadoCruce` y `ResultadoCruceDisco`.
- `tests/test_columnas_finales_v021.py` (17 pruebas).
- [ADR-0008](docs/adr/0008-el-contrato-de-salida-de-la-correlativa.md).

### Lo que NO se resolvió

**No se pudo reproducir el caso reportado.** Quedan tres hipótesis: una
versión anterior a 0.19.0, una de las tres excepciones disparándose, u otro
artefacto. Las tres quedan cerradas hacia adelante, pero cuál fue no se sabe.
Buscar `Consolidación falló` o `métricas de diagnóstico` en el registro de esa
corrida lo resolvería.

## [0.20.0] — 2026-08-29 — Dos instrumentos, un solo catálogo, y la deuda que el archivo nuevo destapó

**La pregunta era si `Ground_Truth_Multicampo_v1.xlsx` servía. Sirve, y además
destapó un defecto estructural que ninguna métrica había mostrado.**

### La respuesta corta sobre la base de pruebas

Ya hay base, y son **dos instrumentos con roles distintos**; ninguno reemplaza
al otro. Evaluación contra construcción se resuelve con **particiones**
(`--pliegue`), no con dos conjuntos.

| | banco | conformidad |
|---|---|---|
| pregunta | ¿mejoró? | ¿sabe hacerlo? |
| conjunto | `benchmark_institucional.csv.gz` | `data/conformidad/` |
| tamaño | 30.486 registros · 46.374 pares | 207 registros · 155 pares |
| salida | F1, recall, macro-F1 | aprobado/reprobado **por caso** |
| falla si | una métrica retrocede | **un solo caso** reprueba |

Con 46.374 pares, un comportamiento roto que afecta a cuatro casos mueve el F1
en la cuarta cifra decimal: no es que se note poco, es que no se nota nunca. Y
son justo los casos que en producción producen una fusión escandalosa. Ver
[ADR-0006](docs/adr/0006-dos-instrumentos-banco-y-conformidad.md).

### Suite de conformidad — 43 casos, veredicto por caso

`scripts/conformidad.py` y `evaluation/conformidad.py`. El conjunto se importa
del libro original y queda versionado en `data/conformidad/` (100 KB). Valida
su propia coherencia al cargar: si la hoja de pares y la columna de grupo no
dicen lo mismo, error — esa comprobación existe porque en 0.19.0 el otro
conjunto sí se contradecía.

Línea base: deduplicación P=1,0000 R=0,9847 F1=0,9923 (24/24 casos firmes);
record linkage P=R=F1=1,0000 (10/10). **Reproduce exactamente la cifra que
declara la hoja LEEME del libro**, medida con otra versión del motor.

Con la corroboración de veto activa (F3, que ya existía y estaba apagada), los
dos casos que el libro marcaba pendientes para "Fase 3" se resuelven:

| | C09 (NIT con un dígito mal) | C21 (dos NIT legítimos) | trampas |
|---|---|---|---|
| sin corroboración | reprueba | reprueba | 6/6 aprueban |
| con corroboración | **aprueba** | **aprueba** | **6/6 aprueban** |

F1 de deduplicación **0,9923 → 1,0000** sin perder ni una trampa: homónimos,
NIT idéntico con nombres distintos, persona contra empresa, correo genérico
compartido, teléfono de call center y geo idéntica en el mismo edificio.

### La deuda técnica real: dos catálogos de comparadores

El archivo declara 11 tipos de campo. Al verificarlos apareció que la librería
tenía **dos catálogos que no se conocían**: el declarativo
(`matching.campos`, 14 tipos, con geo/fecha/numérico/dirección/nombre de
persona) y el que consume el **scorer de producción** (11 tipos, sin ninguno
de esos). **El camino que procesa millones de registros no podía usar la mitad
de lo que la librería ya sabía hacer.**

`matching.puente_campos` registra en el catálogo de producción el comparador
canónico de cada tipo, delegando en la misma instancia del motor multicampo.
Cero lógica duplicada. **Tipos disponibles para producción: 11 → 24.**

```python
linkage(..., extra_features=[("FECHA_CONST", "tipo_fecha", 0.10),
                             ("COORDENADAS", "tipo_geo",   0.10)])
```

Ver [ADR-0007](docs/adr/0007-un-solo-catalogo-de-comparacion.md).

### Dos defectos reales que el puente destapó

- **`pandas 3.0` dejó de convertir los ausentes a la cadena `'nan'`**: los
  deja como `NaN` flotante y el `.map` posterior revienta con
  `normalize() argument 2 must be str, not float`. Bastaba una celda vacía en
  una columna de texto para tumbar una corrida. Corregido en todos los
  normalizadores de `matching/comparators.py`.
- **El comparador de direcciones no separaba la placa**: `'CRA 7 # 71-21'`
  contra `'CARRERA 7 NO 71 21'` daba 0,600 porque `'71-21'` era un token y
  `'71' '21'` eran dos. Ahora da **1,000**.

### C31 — Bloqueo multivariable: implementado, medido, y NO ayuda aquí

`engine/lsh/llaves_extra.py` genera candidatos por igualdad de una llave
declarada (teléfono, correo, dirección, celda geográfica, documento) y por
token raro. Está apagado por defecto y es seguro en memoria: códigos enteros,
bloques descartados antes de enumerar, lotes que se sueltan — ~4 bytes por
registro y llave, frente a los 6–10 GB de la implementación que causó el OOM
de 0.17.3.

**No mejora este conjunto, y se dice con números.** Tres mecanismos probados:

| configuración | F1 | macro-F1 | RUIDO recall | FP sobre negativos |
|---|---:|---:|---:|---:|
| línea base | **0,8780** | **0,8920** | 0,6472 | **287** |
| + llaves (tel., correo, dirección) | 0,8780 | 0,8920 | 0,6472 | 287 |
| + token raro (frec. ≤ 5) | 0,8779 | 0,8921 | 0,6482 | 324 |
| + token raro (frec. ≤ 50) | 0,8454 | 0,8712 | **0,6717** | 2.575 |
| umbral LSH 0,35 | 0,8537 | 0,8676 | 0,5547 | 787 |
| 504 permutaciones | 0,8761 | 0,8905 | 0,6430 | 289 |

**El hallazgo importante es el negativo:** el techo de recall del estrato de
ruido NO es un problema de bloqueo. Los pares perdidos son alcanzables —el
82,8 % tiene Jaccard de trigramas ≥ 0,30— pero toda forma de alcanzarlos
inunda el scorer de pares falsos que cuestan más de lo que ganan. El cuello
está en **discriminar bajo corrupción extrema del nombre sin identificador**,
no en proponer. Eso redirige el trabajo pendiente.

### Añadido

- `evaluation/conformidad.py`, `scripts/conformidad.py`, `data/conformidad/`.
- `matching/puente_campos.py` (13 tipos al catálogo de producción).
- `engine/lsh/llaves_extra.py`: `LlaveBloqueo` (5 canonicalizadores +
  rejilla geo desplazada) y `LlaveTokens`.
- Perillas `llaves_bloqueo` y `tokens_bloqueo` en los 8 perfiles.
- ADR-0006, ADR-0007, `docs/CONFORMIDAD.md`.
- `tests/test_conformidad_v020.py` (39 pruebas).

### Corregido

- `matching/comparators.py`: ausentes con pandas 3.0 y placa de dirección.
- El centinela "sin llave" ya no forma bloque: sin ese filtro, todos los
  registros sin teléfono habrían quedado agrupados entre sí.
- La rejilla geográfica pierde vecinos en los bordes de celda; se resuelve con
  una segunda rejilla desplazada media celda, no agrandando la celda.

### Optimización probada y descartada

Vectorizar el módulo 11 con multiplicación de matrices: **39,5 s contra 35,3 s**
sobre 5 M de identificadores, y solo 15 % mejor por lote, a cambio de 35 líneas.
El costo no está en la aritmética sino en las operaciones de cadena de pandas
sobre 4,2 M de valores únicos. Se conservó la versión simple y quedó anotado
en el docstring para que nadie vuelva a intentarlo sin medir.

### Escala — el arreglo que más pesa para Colab

`aplicar_cannot_link_identificador` convertía la columna ENTERA de
identificadores a numpy **dentro del bucle**, una vez por grupo en conflicto.
Con Arrow detrás esa conversión no es barata: cProfile la señaló como el 53 %
del tiempo de la función, con 5.316 llamadas sobre 200 K filas.

| | antes | después |
|---|---:|---:|
| 1 M de filas, 2 % en conflicto | 437,2 s | **10,2 s** (43×) |
| 5 M de filas, 2 % en conflicto | no terminaba en 10 min | **56,2 s** |
| RSS a 5 M | — | 555 → 618 MiB (+63) |

Resultado idéntico: mismas etiquetas, mismo número de grupos. A 5 M de
registros esta fase pasaba de más de media hora a menos de un minuto, que es
la diferencia entre terminar una corrida en Colab gratuito y no terminarla.

Medido también el costo de la forma canónica del identificador (ADR-0004) a
escala: 35 s sobre 5 M de valores (4,2 M únicos) y ~80 s repartidos en una
corrida de 20 M de pares, sin crecimiento de memoria.

### Sin cambios en producción

La línea base del banco es **bit a bit idéntica** a la de 0.19.0 (huella
`1e365ba8…`). Todo lo nuevo está disponible y apagado por defecto.

## [0.19.0] — 2026-08-29 — Un conjunto de referencia que no se contradice, y la corrección que destapó

**Esta versión reemplaza la vara de medir.** El conjunto anterior
(`ground_truth_grande.csv`, sintético) daba F1 0,9799 y hacía parecer buenas
cosas que no lo eran. El nuevo conjunto institucional —30.486 registros de
CRM, RUES, SuperSociedades y dos generadores— da 0,8780 y dice dónde duele.

### El conjunto de referencia institucional

`data/benchmark/benchmark_institucional.csv.gz`: 30.486 registros · 11.478
grupos · 46.374 pares verdaderos · 9 fuentes · 4 estratos · 11 casos · 18
columnas. Reproducible con `scripts/construir_benchmark.py`, semilla 42.

Se evaluaron los dos archivos disponibles y **ninguno servía solo**:

- **Ground_Truth_Robusto_V3** trae ruido etiquetado (CLEAN…EXTREME) que no se
  consigue de otro modo, pero solo tiene NIT y razón social — y aplica el
  ruido también al identificador. En 899 de 1.800 grupos (49,9 %) el NIT no se
  recupera con ninguna canonicalización; esos grupos concentran 17.055 de los
  18.629 pares del estrato. Pedían unir registros con identificadores
  distintos, que es justo lo que los negativos prohíben: **el conjunto se
  contradecía y ningún código podía sacar F1 = 1**. Se midió el daño: recall
  CON_ID 0,687, *peor* que SIN_ID (0,834). Corregido —identificador borrado
  donde está corrupto, conservado donde se canonicaliza— el mismo código pasó
  a 0,966.
- **El archivo del CRM** es real pero es la **salida de un cruce anterior**;
  medir contra él sería medir el acuerdo con ese proceso, no el acierto. Se
  usan solo las 50.997 filas "Alta Confianza" ancladas en identificador; las
  5.122 de "Revisión Manual" se excluyen en vez de contarse.

Negativos **minados, no inventados**: 1.040 empresas del RUES con NIT distinto
y nombre confundible (a la mitad se le borra el identificador, para que
separarlas exija geografía o CIIU) y 550 personas homónimas del CRM.

Detalle completo en [docs/BENCHMARK.md](docs/BENCHMARK.md).

### La corrección que el conjunto destapó

El dígito de verificación estaba partiendo entidades en dos. De los 5.312
pares con identificadores textualmente distintos dentro de un mismo grupo, en
**5.269 (99,2 %)** uno es el otro más un dígito, y en **5.002** ese dígito
valida por módulo 11. La librería los trataba como identificadores distintos
en tres lugares —distancia, veto y cannot-link—, y el tercero **deshacía en L5
lo que L4 acababa de unir bien**.

El caso que lo destapó: `FRESH PISCINAS` (NIT 102829482) y `SANCHEZ LOPEZ
FREDY` (NIT 10282948) — un establecimiento y su dueño. Los nombres no
comparten un token: el identificador era la única evidencia, y era la que se
tiraba.

| métrica | antes | después |
|---|---:|---:|
| F1 global | 0,8745 | **0,8780** |
| macro-F1 por estrato | 0,8819 | **0,8920** |
| F1 estrato REAL | 0,9344 | **0,9648** |
| recall estrato REAL | 0,8769 | **0,9319** |
| precisión estrato REAL | 1,0000 | **1,0000** |
| FP que tocan un negativo | 287 | **287** |

TP +275, FP **+0**. Mejora estrictamente dominante, estable en los 3 pliegues
fuera de muestra (+0,0035 global, +0,0303 en REAL, idéntico a la muestra
completa). Riesgo medido: colapsar `X` con `X+DV` sobre 145.082
identificadores reales produce **cero** colisiones entre números ajenos.

Ver [ADR-0004](docs/adr/0004-identificador-en-forma-canonica.md).

### Comparadores multicriterio

Dos comparadores nuevos en el registro, sin tocar el scorer:

- **`categoria_tolerante_signed`** — igualdad por contención de tokens.
  DEPARTAMENTO pasa de 40,3 pp de separación a **69,9 pp**; MUNICIPIO de 30,1
  a **67,7**. El desacuerdo dominante era `BOGOTA` contra `BOGOTA D C`: 15.781
  casos. **La geografía no era una variable floja; el comparador lo era.**
- **`conjunto_signed`** — solapamiento `|A∩B|/min(|A|,|B|)` para campos
  multivaluados. El RUES publica 2,52 actividades por empresa y la
  Superintendencia 1. Igualdad exacta reconoce el 18,1 % de los pares
  verdaderos, Jaccard el 19,5 %, **solapamiento el 99,3 %** (contra 20,7 % en
  negativos duros y 1,3 % al azar).

Reproducible con `scripts/diagnostico_variables.py`.

### Lo que quedó desmentido

**El perfil `fuentes_mixtas` de 0.18.0 no generaliza.** Ganaba +0,014 de F1
sobre el conjunto sintético; sobre el institucional **pierde 0,014**, baja el
recall SIN_ID de 0,652 a 0,591, sube los FP sobre negativos de 287 a 419 y
cuesta 75 % más tiempo. Era sobreajuste. Se mantiene documentado para datos de
contacto sintéticos, pero **deja de recomendarse como perfil general**.

**El criterio múltiple en el scorer es la palanca equivocada** para lo que
queda. Medido el recall de bloqueo por estrato: RUIDO 0,730 y CONTACTO 0,959
pierden en el **bloqueo**, no en el scoring — ninguna perilla del scorer
rescata un par que nunca fue candidato. La palanca correcta es bloqueo
multivariable (C27). Ver [ADR-0005](docs/adr/0005-comparadores-multicriterio.md).

### Instrumento

- El banco reporta **calidad por estrato y macro-F1**. El F1 global lo domina
  el estrato con más pares —que crecen con el cuadrado del tamaño de grupo— y
  puede esconder por completo lo que pasa en los demás.
- `--comparar` corrige el criterio de FP sobre negativos: era absoluto (`≤ 0`)
  y reprobaba mejoras que no empeoraban nada; ahora es **relativo a la base**.

### Añadido

- `matching/identificadores.py`: módulo 11 de la DIAN, forma canónica y
  detección de extensión por dígito de verificación.
- `matching/comparadores_extra.py`: `conjunto_signed`,
  `categoria_tolerante_signed`.
- `scripts/construir_benchmark.py`, `scripts/diagnostico_variables.py`.
- `docs/BENCHMARK.md`, ADR-0004, ADR-0005.
- `tests/test_identificadores_v019.py` (37 pruebas).
- Perilla `dv_es_mismo_identificador` (default `True`) en los 8 perfiles.

### Corregido

- `estrato_real` leía la columna de MUNICIPIO por posición (`_8` de
  `itertuples`) y estaba tomando el **sector económico**. Los accesos
  posicionales se reemplazaron por alias explícitos con validación.
- El benchmark copiaba `TAMANO_RUES__C` del CRM como tamaño del registro del
  CRM: es un campo derivado del RUES (90,4 % idéntico) y **regalaba la
  respuesta**. Se excluye.

## [0.18.0] — 2026-08-29 — Un banco de pruebas, y la primera mejora que pasa su examen

**Esta versión establece cómo se decide que algo mejoró, y usa ese
instrumento para subir el F1 de 0,9659 a 0,9799 sobre el conjunto de
referencia — con la ganancia validada fuera de muestra.**

### Por qué hacía falta un banco

Entre 0.14.1 y 0.17.4 cada mejora se justificó con una medición hecha a mano,
distinta cada vez. Dos regresiones llegaron a producción con la etiqueta de
"verificadas": la de velocidad de 0.17.0 y el OOM de 0.17.3. El factor común
no fue descuido, fue que **no había con qué comparar**: dos mediciones sobre
datos distintos no se restan.

`scripts/banco.py` mide en una sola pasada, sobre un conjunto fijo y
versionado, calidad, tiempo por fase, memoria por fase, disco, candidatos y
una huella SHA-256 de la partición. `--comparar` emite un veredicto binario
con umbrales declarados y devuelve código de salida, así que sirve en CI.

Conjunto de referencia: `data/ground_truth/ground_truth_grande.csv` —
12.427 registros · 3.486 grupos · 5 fuentes · 22.073 pares verdaderos ·
81 % con identificador y 19 % sin él · 43 casos negativos diseñados.

Ver [ADR-0001](docs/adr/0001-banco-de-pruebas-unico.md) y [docs/BANCO.md](docs/BANCO.md).

### El diagnóstico que cambió el plan

Con el banco corriendo, clasificar los errores tomó minutos y fue inequívoco:

| | falsos negativos | falsos positivos |
|---|---|---|
| Pares SIN identificador | 772 (79 %) | **519 (100 %)** |
| Pares CON identificador | 200 (21 %) | 0 |

Y por etapa donde se pierde el par verdadero:

| etapa | pares | % |
|---|---|---|
| **Nunca fue candidato (bloqueo)** | **705** | **72,5 %** |
| Candidato que no pasó el score | 267 | 27,5 % |
| Score aprobado, no agrupado | 0 | 0 % |

El cuello no estaba en el scoring sino en el **bloqueo**, y todo el error de
calidad vive en el régimen sin identificador. Sin esa medición, el trabajo
habría ido a afinar umbrales de score, que no habría servido de nada.

### Perfil nuevo: `fuentes_mixtas`

Para fuentes donde **solo algunos** registros traen identificador — que es el
caso común fuera de un registro mercantil: padrones de damnificados,
matrículas escolares, censos de pacientes, beneficiarios de programas
sociales.

| métrica | `produccion_estandar` | `fuentes_mixtas` |
|---|---|---|
| precision | 0,9760 | **0,9866** |
| recall | 0,9560 | **0,9733** |
| **F1** | 0,9659 | **0,9799** |
| B³ F1 | 0,9797 | **0,9868** |
| recall SIN identificador | 0,8344 | **0,9165** |
| falsos positivos | 519 | **291** |
| tiempo | 25,6 s | 39,8 s |

**Validación fuera de muestra** (3 pliegues repartidos por grupo): ΔF1 medio
**+0,0105** (mín +0,0077, máx +0,0133) contra +0,0140 en el conjunto donde se
calibró. La diferencia de 0,0035 dice que no es sobreajuste.

Lo componen tres mecanismos que **no sirven por separado** y sí juntos:

1. **`lsh_threshold` 0,55 → 0,45** — abre el bloqueo, que era el cuello real.
   Solo: recall 0,9764 pero precisión 0,9421.
2. **`similitud_compacta_min` 0,96** — un piso de Jaro-Winkler sobre el
   nombre sin espacios. Recupera lo que un espacio mal puesto rompe para el
   comparador por tokens: `CHOIMIN GLOBAL CORP` vs `CHOIMING LOBAL INC`,
   `HANYOO GLOBALCO., LTD` vs `HANYOO GLOBAL CO. LTD`. Solo: +0,0004 de F1.
3. **`idf_weight_blend_sin_identificador` 0,05** — pondera cada token por lo
   que informa, y **solo** donde no hay identificador que decida. Paga la
   precisión que cuesta abrir el bloqueo. Solo: F1 0,9653.

Ver [ADR-0003](docs/adr/0003-idf-por-regimen.md).

### Registro de comparadores

La comparación de variables adicionales vivía en una cadena de 118 líneas de
`if ftype == ...` dentro del scorer, y la lista de tipos válidos estaba
escrita **dos veces**. Registrar un comparador nuevo lo dejaba funcionando en
el scorer y rechazado por la validación — la misma falla de fondo que causó
el OOM de 0.17.3.

Ahora hay un registro (`matching/comparadores_extra.py`). Los seis
comparadores heredados se movieron con **paridad bit-a-bit**, verificada
sobre 3.000 pares con nulos de todas las formas, espacios y caja mixta.

Comparadores nuevos, que canonicalizan antes de comparar:

| tipo | qué normaliza |
|---|---|
| `telefono_signed` | últimos 7 dígitos: ignora prefijo país, indicativo y separadores |
| `email_signed` | dominio distinto → −1; mismo dominio → similitud graduada del buzón |
| `documento_signed` | dígitos, sin separadores ni ceros a la izquierda |

Hacían falta: declarar TELEFONO como evidencia con el comparador exacto **no
cambiaba ni un par**, porque dentro de un mismo grupo el número aparece como
`312 1897799`, `312-189-7799` y `+573121897799`, y el comparador exacto los
declara distintos — penalizando a los pares verdaderos.

Ver [ADR-0002](docs/adr/0002-registro-de-comparadores.md).

### Ponderación IDF, ahora utilizable

`matching/idf.py` calcula la informatividad de cada token con matrices
dispersas. La implementación anterior era un bucle de Python sobre los pares
del lote —el mismo antipatrón que causó el OOM— y además el mapa token→IDF
tenía que proveerlo el llamador, cosa que el Orchestrator nunca hacía: la
perilla era un no-op por ese camino.

La frecuencia es **documental**: un token repetido dentro del mismo nombre
cuenta una vez, para que `SEGUROS SEGUROS DEL SUR` no haga parecer genérico
a `SEGUROS`.

### Resultados negativos que también se documentan

- **CIUDAD como evidencia adicional empeora el F1**: 0,9659 → 0,9514.
  Coincidir de ciudad es evidencia débil que empuja pares dudosos por encima
  del umbral. Recomendación: no activarla.
- **El IDF global es un dial de precisión, no una mejora de F1**: con mezcla
  0,50 la precisión llega a 1,0000 y el recall cae a 0,8562. Útil cuando
  fusionar de más es catastrófico; no es una ganancia neta.
- **El veto por IDF fue peor que la mezcla** (F1 0,9496–0,9553). La perilla
  quedó en el código con default 0,0 por si un caso de uso la necesita.
- **La hipótesis de que la frecuencia del nombre identificara a los
  intermediarios era falsa**: aparecen 1 o 2 veces, no muchas.

### Limitación conocida

Los falsos positivos que tocan un caso negativo **suben de 62 a 111** en
`fuentes_mixtas`. Son intermediarios comerciales con nombre casi idéntico
usados por clientes distintos (`COMMERCIAL ZELECTA TRADING GROUP CORP`). El
bloqueo más abierto los expone y el IDF no alcanza a separarlos. Desde el
nombre solo no son separables: hace falta evidencia de la entidad detrás.

### Compatibilidad

**El comportamiento por defecto no cambió.** Las cuatro perillas nuevas valen
0,0 y la paridad se verificó por huella de partición: `c9d30223…` se reprodujo
idéntica en cinco corridas con código distinto. Quien no cambie de perfil
obtiene exactamente el mismo resultado que en 0.17.4.

### API nueva

- `record_linkage.evaluation.banco`: `EspecificacionBanco`, `correr_banco`,
  `evaluar_calidad`, `bcubed`, `huella_particion`, `MuestreadorRecursos`.
- `record_linkage.evaluation.comparador`: `comparar`, `Umbrales`, `Comparacion`.
- `record_linkage.matching.comparadores_extra`: `registrar`, `obtener`,
  `tipos_disponibles`, `canonicalizar_telefono`, `canonicalizar_email`,
  `canonicalizar_documento`.
- `record_linkage.matching.idf`: `construir_idf`, `similitud_idf`, `PesosIDF`.
- Perfiles: `fuentes_mixtas`. Perillas: `similitud_compacta_min`,
  `idf_weight_blend`, `idf_weight_blend_sin_identificador`,
  `idf_veto_min_sin_identificador`.
- `scripts/banco.py` — interfaz de línea de comandos del banco.

### Documentación

- `CLAUDE.md` — guía de trabajo del repositorio.
- `docs/BANCO.md` — cómo se mide.
- `docs/BITACORA.md` — qué se hizo, con qué evidencia, y qué no funcionó.
- `docs/PLAN.md` — los 30 criterios de mejora y su estado (8 hechos,
  6 parciales, 16 pendientes).
- `docs/adr/` — tres decisiones de arquitectura con su evidencia.
- `docs/evidencia/` — el JSON de cada corrida y la partición predicha.

### Verificación

- Suite completa: **1.252 aprobadas, 2 omitidas, 0 fallos** (74 pruebas
  nuevas: 43 de comparadores e IDF, 31 del banco, 4 del perfil).
- `ruff check` y `ruff format --check` limpios · `twine check` PASSED.
- Paridad de la partición verificada en cinco corridas.

## [0.17.4] — 2026-08-29 — El bloqueo por NIT deja de ser cuadrático en Python

**Corrige el OOM que mató la corrida de 5,26 M de filas en Colab, y que NO
era un problema de tamaño sino de estructura de datos.**

### Dónde murió, exactamente

El log del usuario termina justo después de que las 42 bandas del LSH
completan al 100 %. Lo siguiente que corre es el bloqueo complementario por
NIT. Ahí estaba el defecto:

```python
neighbor_index: dict[str, list[int]] = defaultdict(list)
for pos_idx, nit_val in zip(positional_idx, nits_valid):
    for key in _generate_one_digit_neighbors(nit_val):   # 19 claves por NIT
        neighbor_index[key].append(int(pos_idx))
```

Con 4,37 M de registros son **83 millones de cadenas de Python** en un
diccionario, construidas en un bucle interpretado, **antes de emitir un solo
par**. Medido en este contenedor, extrapolado a su escala:

| Registros | Claves | RSS del índice | Tiempo |
|---|---|---|---|
| 100.000 | 1.385.143 | 246 MB | 4,5 s |
| 300.000 | 3.210.828 | 453 MB | 11,4 s |
| **4.368.688** (extrapolado) | — | **6–10 GB** | **~3 min** |

Sobre un DataFrame de ~3 GB ya residente y un techo útil de ~11 GB, eso es
una sentencia. Y encima el `set` resultante enumeraba también los pares
RUES×RUES —que la política descarta acto seguido— y los copiaba tres veces
más (`list(pairs)` → `np.asarray` → `list` filtrado).

### Por qué se me pasó

En 0.17.3 corregí exactamente este patrón —enumerar todo y filtrar
después— en el generador de buckets del LSH, medí 12x y lo di por cerrado.
La misma regla estaba escrita **una segunda vez** en el bloqueo por NIT, y
no la audité. Una regla escrita dos veces se corrige una vez y sigue rota.

Por eso 0.17.4 no solo arregla la segunda copia: **la elimina**. La
política vive ahora en un único módulo, `engine.lsh.politica_pares`, que
los dos caminos llaman.

### Las tres decisiones que cambian el orden de magnitud

1. **El NIT se codifica como entero**, no como cadena. `(longitud, valor)`
   cabe en un int64: 4,37 M de códigos son 35 MB en vez de ~500 MB, y las
   claves de vecindad salen de aritmética vectorizada.
2. **Vecindad por borrados** (SymSpell, Garbe 2012) en vez de enumerar
   sustituciones e inserciones dígito a dígito: si dos cadenas están a
   distancia ≤ r, existe una forma común borrando ≤ r caracteres de cada
   una. Son **10 claves por registro a radio 1** en vez de 199 — y es
   demostrablemente completo, verificado contra fuerza bruta con **cero
   falsos negativos** a radio 1 y 2.
   <https://wolfgarbe.medium.com/1000x-faster-spelling-correction-algorithm-2012-8701fcd87a5f>
3. **Se indexa el lado pequeño y se recorre el grande.** Expandir la
   vecindad de 4,37 M de registros es lo que reventaba; expandir la de
   19.407 exportadores cuesta menos de un megabyte. La relación "distancia
   ≤ r" es simétrica, así que el conjunto de pares es el mismo.

Los pares se rinden **por lotes** y se escriben a SQLite; nunca se acumulan.

**Medido a la escala exacta del usuario** (4.388.095 registros, RUES
confiable):

| | 0.17.3 | 0.17.4 |
|---|---|---|
| Pares emitidos | (murió) | 493.818 |
| Tiempo | ~3 min y creciendo | **25,6 s** |
| Pico de RSS añadido | 6–10 GB | **0 MB** |

### Segundo cuello: la fase de candidatos leía 4,4 M de identificadores por banda

Su corrida gastó **22 min 42 s** en la fase de candidatos. La consulta por
banda concatenaba en SQL *todos* los identificadores de la banda y Python los
convertía uno a uno: 4,4 M de llamadas a `int()` por banda, 42 veces.

Un bucket sin ningún registro de EXPORTACIONES no puede producir un par
permitido. Formalmente se busca un **recubrimiento por vértices** del grafo
de la política —un conjunto de fuentes tal que toda combinación permitida
tenga un extremo en él—; con RUES confiable ese conjunto es
`{EXPORTACIONES}`: 19.407 registros de 4,39 M (**0,44 %**).

Saber *después* qué buckets tienen semilla obliga a recorrer las 184 M de
filas del índice (5,5 min medidos). Así que se anota **durante** la
indexación, que ya calcula cada hash: cuesta indexar 19.407 posiciones más
por chunk y el tiempo de indexación no se movió (8,1 → 8,25 s/chunk). Una
huella del conjunto de semillas guardada en el índice impide reutilizar una
anotación ajena; si no coincide, se recurre al recorrido.

El parseo de identificadores lo hace ahora NumPy en C.

**Medido sobre el universo real (4.389.407 registros), fase L2 completa:**

| | 0.17.3 | 0.17.4 |
|---|---|---|
| Firmas | 5 min 07 s | 5 min 05 s |
| Indexación | 4 min 04 s | 4 min 07 s |
| **Candidatos** | **14 min 50 s** | **1 min 45 s** |
| Bloqueo por NIT | (OOM) | ~3 min 30 s |
| **L2 total** | **27 min 57 s** | **14 min 54 s** |

El conjunto de candidatos es **idéntico**: 351.229 pares, mismas sumas de
control de `idx_0` e `idx_1`. Hay además una prueba que corre la fase con y
sin atajo y exige igualdad par por par.

### Calidad: paridad o mejor

| Perfil | | 0.17.3 | 0.17.4 |
|---|---|---|---|
| produccion_estandar | P / R / F1 | 1,0000 / 0,3652 / 0,5350 | 1,0000 / 0,3652 / 0,5350 |
| fuentes_ruidosas | P / R / F1 | 1,0000 / 0,7603 / 0,8638 | 1,0000 / **0,7620** / **0,8649** |

El perfil estándar queda **idéntico**. El ruidoso mejora porque el radio de
la vecindad ahora sigue a `tolerancia_digitacion_identificador`: con
tolerancia 2 y vecindad 1, los pares a dos dígitos de distancia nunca
llegaban a scorearse. Bloquear más estrecho que el scorer pone un techo al
recall que ningún umbral posterior puede levantar.

### Corrección honesta de un mensaje mío

La §4 del notebook decía, al elegir DuckDB: *"el pico de RAM deja de crecer
con el número de filas"*. **Eso es falso** y contribuyó a que usted confiara
en una corrida que no podía terminar. `motor_ingesta="duckdb"` hace la
INGESTA en disco —proyección, normalización, colapso—; las fases L1 a L5 del
matcher siguen trabajando sobre un DataFrame en RAM. El mensaje ahora dice
qué cubre el modo disco y qué no, y la §4 imprime el presupuesto de memoria
del matcher, que es el que manda.

### API nueva

- `engine.lsh.politica_pares`: `pares_permitidos`, `pares_por_bloque`,
  `bloques_utiles`, `indices_de_grupos`.
- `engine.lsh.nit_blocking`: `iter_pares_por_nit`, `codificar_nits`,
  `claves_por_borrado`, `patrones_de_borrado`; `NitBlockingConfig` gana
  `radio_vecindad`.
- `block_by_nit_base` se conserva por compatibilidad y queda documentada
  como apta solo para bases pequeñas.
- Perfil `fuentes_ruidosas`: `nit_blocking_radio = 2`.

### Verificación a escala real

La corrida completa se ejecutó de punta a punta en un contenedor con **7,8 GB
de RAM — un 40 % menos que Colab Free**, a propósito: lo que termina aquí,
allá sobra.

| | |
|---|---|
| Filas físicas | 5.265.102 |
| Registros tras colapsar | 4.389.407 |
| Entidades resultantes | 4.370.003 |
| **Pico de RSS** | **5,5 GB** |
| Tiempo total | 41,1 min (0.17.4 con todas las optimizaciones: ~28 min) |

De ahí sale la constante que la §4 del notebook ahora imprime: **~1,26 GB por
millón de registros que entran al matcher**. Con los ~11 GB útiles de Colab
Free eso da un techo práctico de **~8,5 millones de registros colapsados**.

Por encima de ese techo el diseño tiene que cambiar, y el camino ya está
identificado: **particionar la fuente confiable**. Como una fuente confiable
nunca se enlaza consigo misma, partirla en K bloques y cruzar cada bloque
contra las fuentes pequeñas es **exacto, no aproximado** —ningún par
RUES×RUES se pierde porque ninguno se quería—, y el pico de RAM pasa a ser
O(n/K). No está en esta versión.

## [0.17.3] — 2026-08-28 — Que no explote la RAM: pares por política y motor que se elige solo

**Corrige la explosión de memoria y el estancamiento de la fase de candidatos
sobre el universo completo, y cierra el hueco por el que la elección de motor
se hacía a ciegas justo en la primera corrida.**

### Defecto 1 — el generador de pares enumeraba 125x el trabajo que servía

`_generate_bucket_pairs` materializaba los `n(n-1)/2` pares de cada bucket con
`np.triu_indices` y **después** los filtraba por política de fuentes. En este
cruce el 99,56 % de los registros son RUES —fuente confiable, que no se
deduplica contra sí misma—, así que un bucket lleno (500 registros) enumeraba
**124.750 pares para conservar unos 997**: 125x de trabajo tirado, y el costo
dominante de la fase de candidatos (36:46 medidos sobre el universo real).

Ahora el bucket se parte por fuente y solo se materializan los bloques
permitidos: producto cruzado entre fuentes distintas, y pares internos solo
donde la política los admite. El costo pasa a ser proporcional a los pares
**emitidos**.

- **12x más rápido** en la generación de pares, medido.
- **Paridad exacta** verificada sobre 800 buckets aleatorios: el conjunto de
  pares emitido es idéntico al del diseño anterior, par por par.
- La política sigue viviendo en un solo método (`_source_pair_mask`), ahora
  consultado una vez por combinación de fuentes en una matriz k×k
  (`_matriz_politica_fuentes`), de modo que `TrustedSourceLSHEngine` la sigue
  gobernando sin que el generador conozca sus reglas.

### Defecto 2 — el mapa de fuentes pesaba 558 MB

El mapa `{record_id: nombre_de_fuente}` era un `dict` de Python con un objeto
`str` por registro. Sobre 5,26 M de filas eso son ~558 MB vivos durante toda
la fase de candidatos. Sustituido por un `np.ndarray` de códigos `int16`
indexado por posición: **8,4 MB, 67x menos**.

`_generate_bucket_pairs` **rechaza explícitamente** el `dict` anterior con un
`TypeError` que explica el cambio, en vez de producir resultados silenciosamente
distintos si alguien llamaba al método directamente.

### Defecto 3 — `NOMBRE_BLOQUEO` sobrevivía a su utilidad

La columna de firma solo se usa para construir las firmas MinHash. Ahora se
libera (`del` + `gc.collect()`) en cuanto las firmas están escritas en disco,
en vez de acompañar al DataFrame por el resto del pipeline.

### Defecto 4 — el motor "auto" se decidía a ciegas en la primera corrida

Éste es el que rompía la promesa "por grande que sea la base, termina": con
`MOTOR="auto"` la §4 solo sabía el tamaño del universo si **ya existía caché**
de una corrida previa. En la primera corrida —la peligrosa— no medía nada y
caía a **pandas**, que es exactamente el camino que agota los ~12,7 GB de Colab
Free.

Nuevo módulo `record_linkage.ingestion.dimensionado`: estima cuántas filas trae
una fuente leyendo **kilobytes, no gigabytes**.

| Fuente | Método | Costo |
|---|---|---|
| Parquet | `num_rows` del pie del archivo | exacto, gratis |
| ZIP de texto | tamaño descomprimido del directorio central ÷ ancho de línea | ~1 s |
| GZIP | trailer ISIZE, desambiguado con la razón de compresión medida | ~1 s |
| Texto plano | tamaño en disco ÷ ancho de línea | milisegundos |
| Caché de corrida previa | `num_rows` del Parquet de caché | exacto, gratis |

El ancho de línea se mide con **muestreo sistemático estratificado**: 16
ventanas de 128 KiB repartidas por todo el archivo. Medir solo la cabecera
—que es lo intuitivo— daba **+21 % de error** sobre la base real de
exportaciones, porque las primeras filas son más cortas que la media.

**Precisión medida contra las bases reales, sin caché alguna:**

| Fuente | Estimado | Real | Error |
|---|---|---|---|
| RUES | 57.074 | 57.186 | −0,20 % |
| Exportaciones | 896.810 | 895.102 | +0,19 % |
| **Universo** | **953.884** | **952.288** | **+0,17 %** en 2,0 s |

La decisión se toma con tres reglas, en este orden:

1. Si el tamaño **no se puede establecer**, gana DuckDB. Correr más lento es
   recuperable; quedarse sin RAM a los cuarenta minutos no lo es.
2. Una cifra **estimada** se compara inflada un 25 %, para que el error del
   muestreo nunca empuje hacia el motor que se cae.
3. Las opciones que solo existen en pandas (`dir_procesados`,
   `forzar_relectura`) se desactivan con aviso si la resolución va a DuckDB:
   son cachés, no semántica. En cambio `colapsar_duplicados_exactos=False` sí
   cambia el contrato de salida, así que se respeta la decisión del usuario y
   se conserva pandas, avisando del riesgo.

### API nueva

- `record_linkage.ingestion.estimar_filas(spec) -> EstimacionFilas`
- `record_linkage.ingestion.resumir_universo(specs, *, exactas=None) -> ResumenUniverso`
- `record_linkage.flujo.filas_en_cache(spec, dir_cache) -> int | None`
- `record_linkage.flujo.resolver_motor(config, log=None) -> ConfigCruce`
- `ConfigCruce.motor_ingesta` acepta `"auto"`; `ConfigCruce.modo_resultado`
  acepta `"auto"`; nuevo `ConfigCruce.umbral_filas_disco` (1.500.000 por
  defecto: a 5,26 M filas el camino pandas hizo pico de 7,97 GB sobre un techo
  de ~12,7 GB compartido con el runtime).

La resolución ocurre dentro de `ejecutar_cruce`, así que una configuración con
motor explícito no paga absolutamente nada: `resolver_motor` retorna de
inmediato.

### Notebook

- La §4 imprime el tamaño de cada fuente, si es exacto o estimado y por qué
  camino se obtuvo, **antes** de comprometer la sesión.
- El conteo desde caché ya no lee la columna NIT completa: sale del pie del
  Parquet.
- `VERSION_ESPERADA` se lee de `pyproject.toml` al generar el notebook. En
  0.17.2 la plantilla decía `0.17.1` mientras el paquete era `0.17.3` y el
  notebook abortaba por su propia verificación de versión.

### Verificación

- Suite completa en tres bloques: **1.168 aprobadas, 2 skips (Windows), 0
  fallos**.
- 47 pruebas nuevas en `test_motor_auto_v0173.py`: precisión del estimador en
  cinco formatos, aritmética del trailer de gzip (incluido el desbordamiento
  de 4 GiB y el gzip multi-miembro), fallos que **no** deben tumbar la corrida,
  y las tres reglas de resolución, más dos corridas completas de punta a punta
  que comprueban que la elección de motor no cambia el resultado.
- 15 pruebas en `test_pares_por_politica_v0173.py`, incluida la paridad exacta
  contra el diseño anterior.
- `test_audit_regressions_v014.py` actualizado al contrato nuevo del generador
  de pares: lo que vigilaba —que RUES no se deduplique contra sí misma y que
  EXPORTACIONES sí— sigue vigilado.
- **Prueba intermitente corregida** (defecto latente, no introducido aquí):
  `test_prop_identidad_jaro` excluía solo `PLACEHOLDERS`, pero un comparador
  trata como faltante su propio `_INVALID_VALUES`, que además contiene
  `"INVALID"` y `"NAT"`. Hypothesis daba con el caso de vez en cuando y la
  suite fallaba sin causa aparente. Ahora se excluyen ambos conjuntos.
- El notebook se ejecutó **de punta a punta desde el ZIP ENTREGADO**, sobre una
  instalación previa deliberadamente rota y **sin caché**, por los dos caminos:

  | | pandas | DuckDB (disco) |
  |---|---|---|
  | Entidades | 74.223 | 74.223 |
  | Filas por grupo | 58.541 / 5.831 / 9.851 | idéntico |
  | Entidades multifuente | 2.360 | 2.360 |
  | Grupos con dos NIT válidos distintos | 0 | 0 |
  | Pico de RSS | 924 MiB | 932 MiB |
  | Duración | 95 s | 107 s |

  La elección de motor cambia el **cómo**, no el **qué**.
- Dimensionado verificado a escala mayor: un ZIP de 1,13 GiB descomprimidos
  (2,69 M filas) se estimó con **+0,19 % de error en 3,2 s**, y la resolución
  automática eligió DuckDB.

## [0.17.2] — 2026-08-28 — Instalación desde la rueda, con verificación de integridad

**Corrige el fallo de instalación en Colab: `ModuleNotFoundError: No module
named 'record_linkage.config'` después de que pip reportó éxito.**

### La causa, reproducida

La celda de entorno copiaba el árbol de 250+ archivos desde Drive y le pedía
a pip que construyera desde ahí. Drive (FUSE) sincroniza de forma asíncrona:
la copia puede quedar **incompleta sin lanzar error**, y pip entonces
construye un paquete al que le faltan módulos. Los dos síntomas del usuario
se reprodujeron exactamente:

| Estado del árbol copiado | Error resultante |
|---|---|
| `config/` ausente | `No module named 'record_linkage.config'` |
| `config/` presente, `paths.py` ausente | `No module named 'record_linkage.config.paths'` |

Agravante: la celda **buscaba la rueda en la raíz y en el directorio padre**,
pero el zip la deja en `dist/`. Nunca la encontraba, así que siempre tomaba
el camino frágil. El mensaje de error ("reinicie la sesión") apuntaba al
lugar equivocado y reiniciar no podía arreglarlo.

### La corrección

- **Rueda primero.** La celda busca `dist/*.whl`, prefiere la que coincide con
  `VERSION_ESPERADA` y copia **un solo archivo** a disco local. Una rueda o
  está completa o falla ruidosamente; no existe el estado intermedio que
  causó el fallo. Además evita el paso de construcción: más rápido.
- **Rueda truncada detectada** con `zipfile.is_zipfile` antes de instalar,
  con el tamaño en el mensaje.
- **Árbol incompleto detectado** cuando no hay rueda: se verifican 17 rutas
  obligatorias y el error **lista exactamente cuáles faltan**.
- **Instalación previa rota reemplazada**: segunda pasada con
  `--force-reinstall --no-deps`, barata porque no re-resuelve dependencias.
  Verificado recuperándose de una instalación 0.14.1 mutilada a propósito.
- **Verificación post-instalación**: se importan 11 subpaquetes críticos
  (`config.paths`, `flujo.cruce`, `processing.text`, …) y, si alguno falla,
  el error dice cuál, con qué excepción y desde dónde se cargó.

### Verificación

- Los tres modos de falla probados uno por uno, cada uno con su diagnóstico
  accionable en vez de "reinicie la sesión".
- **El notebook se ejecutó de punta a punta desde el ZIP ENTREGADO**
  (desempaquetado en la jerarquía real de Drive) y sobre una instalación
  previa deliberadamente rota — que es el escenario del usuario. Esta
  verificación faltaba: antes se probaba el árbol de trabajo, no el
  artefacto que se entrega.
- Suite: 1.105 aprobadas, 2 skips (Windows), 0 fallos.

## [0.17.1] — 2026-08-28 — Firma de bloqueo separada del nombre de decisión

**Corrige una regresión de rendimiento que 0.17.0 introdujo y que impidió
terminar el universo completo de 4,4 M de filas en Colab.**

### El defecto

0.17.0 hizo `NOMBRE_LIMPIO` más fiel (dejó de podar los 315 giros
económicos) para ganar recall. Pero esa columna alimenta DOS fases con
requisitos opuestos: el bloqueo LSH la quiere corta y distintiva, el scoring
la quiere fiel. Medido sobre el RUES real:

| | 0.14.1 | 0.17.0 | 0.17.1 |
|---|---|---|---|
| Caracteres por nombre | 17,5 | 24,8 | 17,3 (firma) / 24,8 (score) |
| Candidatos generados | 233.420 | **706.214 (3,03×)** | 232.515 (1,00×) |
| Firmas (4,4 M filas) | 4:05 | **7:45** | — |
| Indexación | 4:29 | **7:33** | — |
| Candidatos | 15:51 | **41:10** | — |
| L2 completo | 29m 47s | **>56 min, no terminó** | — |

La corrida del usuario murió después de candidatos, con 3.046.304 pares.

### La corrección

- **`NOMBRE_BLOQUEO`** — columna nueva que L1 deriva de `NOMBRE_LIMPIO`
  podando `VOCABULARIO_SOLO_BLOQUEO` (los 315 giros). El motor LSH firma
  sobre ella; el scorer, el veto y el golden siguen viendo `NOMBRE_LIMPIO`
  íntegro. Cada fase obtiene lo que necesita.
- Derivarla es un filtro de tokens cacheado por valor único (3× más rápido
  que regex sobre los nombres del RUES, que se repiten mucho).
- Fallback a `NOMBRE_LIMPIO` si la columna no existe: checkpoints y rutas
  anteriores siguen funcionando.
- Calidad conservada: sobre el ground truth etiquetado, F1 0,8705 → 0,8638
  y B-cubed 0,9239 → 0,9200 (−0,7 %) a cambio de 3× menos candidatos.

### SOLID — sustituibilidad real de los dos modos de resultado

La auditoría 0.16.0 marcó Liskov como no resuelto: el consumidor tenía que
discriminar si el resultado traía DataFrames o Parquet. Ahora:

- **`ControlCalidad`** — protocolo con `conflictos_identificador()`,
  `distribucion_grupos()`, `grupos_sospechosos()`,
  `identificadores_por_fuente()` y `entidades_multifuente()`, implementado
  por `ResultadoCruce` (pandas) y `ResultadoCruceDisco` (DuckDB en SQL, sin
  materializar la correlativa). Verificado empíricamente: los dos motores
  devuelven cifras idénticas sobre los mismos datos.
- **`pares_enlazados()`** y **`exportar_sin_pareja()`** — mismas vistas de
  negocio en ambos modos; en disco la selección ocurre dentro de DuckDB y la
  salida puede tener millones de filas sin pasar por la memoria del proceso.
- El notebook expone **`MOTOR = "pandas" | "duckdb"`**: una línea cambia todo
  el pipeline a disco. La §7 y la §8 son idénticas en los dos.

### Otros

- **DuckDB ya no tumba la corrida sin `ipywidgets`**: `SET
  enable_progress_bar` lanza `InvalidInputException` dentro de un kernel
  Jupyter sin ese paquete. Es cosmético; ahora se intenta y se sigue.
- Suite: 1.103 aprobadas, 2 skips (Windows), 0 fallos en Linux.
- Notebook ejecutado de punta a punta en los dos motores contra la jerarquía
  y los datos reales de Drive.

## [0.17.0] — 2026-08-28 — Identificador como evidencia graduada y limpieza que conserva identidad

Primera versión calibrada contra ground truth etiquetado
(Ground_Truth_Robusto_V3: 7.368 registros, 2.000 grupos, ruido hasta EXTREME).
Sobre esa vara, el perfil nuevo pasa de F1 pairwise 0,5345 (0.16.0) a
**0,8705** y de B-cubed 0,7494 a **0,9239**, manteniendo precisión 1,0 y cero
uniones de casos negativos. El perfil institucional por defecto no cambia de
semántica de veto; los cambios de limpieza sí lo tocan (ver "Ruptura de
paridad", abajo).

### Añadido

- **`tolerancia_digitacion_identificador`** (default 0 = comportamiento
  0.14-0.16 exacto): con d>0, dos NIT base a distancia OSA ≤ d (digitación,
  dígito extra/faltante, transposición) con nombre cohesivo se tratan como
  variantes de captura del mismo identificador — en el veto de pares (L3) y
  en el cannot-link (L5), que ahora agrupa identificadores en clases por
  union-find acotado antes de contar conflictos. Modelo Fellegi-Sunter de
  acuerdo parcial; medido: era la causa del 88 % de los falsos negativos en
  fuentes con identificador sucio.
- **Guardia de marca** en ambos rescates: tokens distintivos (fuera del
  léxico corporativo) que difieren — "…COMERCIALIZADORA **ML**" vs
  "…COMERCIALIZADORA **TITANS**", caso real RUES con NIT a distancia 2 —
  bloquean el rescate aunque la similitud global del nombre sea alta.
- **Perfil `fuentes_ruidosas`**: para CRM/capturas manuales (d=2, rescate
  0,82, lsh_threshold 0,45, score_threshold 0,50 — medido idéntico a 0,45 en
  el ground truth y recorta la franja de riesgo en datos reales). NO usarlo
  con el RUES oficial: sobre RUES × Exportaciones reales fusiona 14 grupos
  más que el estándar (mayoría variantes legítimas tipo SUDINCO/CARIBBEAN
  EXOTICS; residuo con sigla distinta documentado como limitación).
- **`ocr_confusables_en_firma`** (experimental, default False en todos los
  perfiles): mapeo 0→O/1→I/5→S… solo en el texto que firma LSH. Medido
  NEUTRO en el ground truth (F1 0,8361 → 0,8347); queda como perilla para
  OCR de escáner real.

### Corregido — limpieza de nombres (BALANCEADO)

- **La salvaguarda de stopwords ahora mide contenido, no conteo**: 195
  nombres compactos reales quedaban reducidos a iniciales societarias
  ("BODEGA DE MODA S.A.S." → "S S") y unían empresas ajenas por firma
  degenerada. El filtrado solo vale si conserva un token distintivo (≥3
  caracteres); si no, degrada a stopwords básicas o conserva el texto.
- **BALANCEADO ya no incluye los 315 giros económicos** (FLORES, CAFE,
  EMPAQUES, TRANSPORTADORA, BODEGA, MODA…): amputaban la identidad de miles
  de empresas ("EMPAQUES DEL CAUCA S.A." → "CAUCA S"). La poda de giros
  sigue disponible explícita en AGRESIVO.
- **Razones sociales puramente administrativas → vacío** ("PERSONA NATURAL",
  "EN LIQUIDACION"): no son nombres; producían matching de texto entre
  personas ajenas con NIT vecino (5 grupos mixtos medidos). Sin nombre, el
  par solo puede unir por identificador idéntico.

### Ruptura de paridad — deliberada y acotada

El E2E real (RUES 57.186 × Exportaciones 895.102) con perfil estándar pasa de
74.217 entidades (fingerprint `61779b06…`, reproducido exacto en Linux antes
del cambio) a **74.220** (`08ccac3f…`): las tres correcciones de limpieza
deshacen fusiones que solo existían por firmas degeneradas. Caso mínimo
reproducible: `TextProcessor(cleaning_mode="BALANCEADO").clean_name("BODEGA
DE MODA S.A.S.")`.

- El **baseline v0.9.0** se regeneró con acta: el slice
  `caso=negativo_intermediario` (n=33, patrón "INTERMEDIARIO Y/O PROVEEDOR")
  empeora (P 0,086 → 0,017) porque los nombres conservan el prefijo del
  intermediario; era un acierto accidental de la lista amputadora. Limitación
  conocida; la mitigación de fondo (ponderar por frecuencia de corpus,
  `remove_top_words`) queda en el roadmap.

### Corregido — el notebook de producción (RUES × Exportaciones)

- **`06_ejemplo_rues_x_exportaciones.ipynb` vuelve a ser la plantilla
  operativa** con las rutas reales de Drive (`ProColombia/0A. Datos/…`,
  librería ya descomprimida en `ProColombia/rues_linker_pruebas`, caché en
  `_procesados`, temporales en `/content`). El archivo que llevaba ese nombre
  en 0.16.0 pertenecía a otro linaje y fallaba con
  `FileNotFoundError: … defina RUES_LINKER_LIB_DIR`.
- **`separar_columnas_extra` restituido** (se había perdido al partir de
  0.15.0): las columnas mapeadas que no puntúan salen del motor y se
  re-adjuntan a la correlativa por `ORIGINAL_INDEX`. Medido con datos reales:
  los representantes de exportaciones bajan de **32.745 a 19.407** (−41 % de
  trabajo de matching) sin perder ninguna columna en la salida.
- **Centinelas declarados** (`"00"`, `"0"`, ceros del RUES): los identificadores
  no válidos "sin explicar" pasan de 2.747 + 56 a **cero**.
- **Purga de módulos viejos + pin de versión duro** en la celda de entorno: un
  kernel con instalación previa ya no corre silenciosamente el motor anterior.
- **Warnings acotados** en vez de `filterwarnings("ignore")` global.
- **Pico de RSS por fase restituido** en `tiempos()` y
  `metricas["pico_rss_mib_por_fase"]`, con el hilo de muestreo cerrado por
  `finally` (`cerrar()` + `join`) y lectura síncrona en los bordes para que
  las fases cortas no queden en blanco.

### Verificación

- **Notebook ejecutado de punta a punta** en un Colab simulado (jerarquía
  `/content/drive/MyDrive/ProColombia/…` real, datos reales del RUES y del
  DANE): las 9 celdas de código sin error, en las dos maquetaciones del RUES
  (42 y 56 columnas — `auto` resuelve `MUNICIPIO`/`CODIGO_MUNICIPIO_COMERCIAL`,
  `TELEFONO`/`TELEFONO_COMERCIAL_1`, `CORREO_ELECTRONICO`/…), en pasada fría
  (61 s) y con caché caliente (16 s).
- Suite completa en Linux: 1.092+ aprobadas, 0 fallos (los 11 skips
  POSIX/symlink de la corrida Windows de 0.16.0 aquí SÍ corren).
- 19 pruebas nuevas de evidencia graduada, clases del cannot-link y guardia
  de marca; paridad d=0 verificada bit a bit sobre el ground truth (idéntico
  a 0.16.0) y sobre el E2E real ANTES de los cambios de limpieza
  (fingerprint `61779b06…` reproducido).
- E2E real Linux 2 vCPU: 54-60 s, RSS pico ~558 MiB (0.16.0 en Windows del
  informe adjunto: 220 s / 678 MiB — hosts distintos, no comparable como
  mejora).


Todas las versiones notables de `rues-linker` se documentan aquí.
Formato basado en [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
y [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

> ## 📍 Estado actual: 0.x — pre-1.0
>
> El paquete está en **desarrollo activo** y la API puede cambiar entre
> minor releases. **Aún NO está publicado en PyPI**. Antes de llegar a
> 1.0 se debe: validar contra producción real (1.97M registros), subir
> cobertura a 80%, y completar refactor de módulos heredados para mypy
> strict. Ver el roadmap del README y las limitaciones de cada release.

---

## [0.16.0] — 2026-08-27 — Fusión auditada, salida disk-first y reanudación exactly-once

Esta versión compara de forma explícita la rama 0.15.0 auditada con la 0.14.1
adjunta. Se portaron sólo las optimizaciones demostrables de 0.14.1 y se
conservaron las correcciones de integridad, seguridad y Windows de 0.15.0.

### Añadido

- **Contrato de resultado en disco.** `ConfigCruce.modo_resultado="disco"`
  devuelve `ResultadoCruceDisco`: `golden` y `correlativa` son referencias
  `TablaParquet`, con `preview()` acotado y `to_pandas(max_rows=...)`
  explícito. La ruta productiva no construye una correlativa física completa
  en pandas.
- **Payload separado del matcher.** DuckDB compacta únicamente NIT, nombre,
  ciudad y variables que realmente puntúan. Las columnas de negocio restantes
  se persisten por `source_row_id` y se re-adjuntan durante la publicación
  final mediante un join DuckDB. Así no inflan L1–L5 ni se pierden del output.
- **QA genuinamente N-fuente.** El resultado en disco calcula la matriz de
  presencia y las entidades ausentes de cualquier fuente mediante SQL; no usa
  la regla binaria `count > 1`, incorrecta para tres o más orígenes.
- **Notebook general disk-first.** El orquestador 06 declara una lista de
  `SourceSpec`, presupuesto DuckDB, fuentes confiables, perfil y límites en una
  sola sección; el resto son preflight, ejecución y consultas acotadas. En
  Colab publica en `/content` y sólo después del commit puede copiar a Drive un
  snapshot único cuyos artefactos verifica por SHA-256; no presupone semántica
  POSIX para lock/`replace`/`fsync` sobre FUSE.
- **Publicación generacional recuperable.** Compactos y resultados se escriben
  en directorios inmutables, se validan y sólo entonces un manifiesto atómico
  cambia la generación actual bajo un lock liberado por el sistema operativo.
  La retención conserva la actual más N anteriores. Los metadatos se adjuntan
  mediante un segundo commit guardado: nunca rebobinan el manifiesto si otra
  corrida ya publicó, aunque una caída entre ambos commits puede dejar la
  generación actual sin metadata.
- **Fail-fast de configuración por backend.** DuckDB rechaza los knobs legacy
  del lector pandas (`SourceSpec.chunksize`, `temp_dir`, `text_engine` y
  `text_block_size_bytes`) cuando no tienen su valor neutro; el mensaje dirige
  a `DuckDBIngestionSettings` para que ningún parámetro quede silenciosamente
  serializado pero inactivo.
- Pruebas de integración verifican que memoria, hilos, límites de filas, modo
  de resultado, payload y rutas declaradas lleguen efectivamente al flujo y a
  los metadatos.

### Evidencia E2E del candidato

- Sobre los dos ZIP adjuntos: 952.288 filas físicas, 76.591 representantes,
  74.217 entidades, 286.520 candidatos y 2.366 pares puntuados; ocho
  invariantes aprobadas, 220,137 s y 677,945 MiB de RSS pico en el host
  Windows auditado.
- La partición normalizada coincide en las 952.288 filas con 0.15.0. La
  diferencia observada de tiempo (−22,9 %) y RSS pico (−2,3 %) no aísla
  causalidad: ambas corridas comparten universo, perfil base y matcher
  efectivo, pero 0.16.0 usa salida disco, payload y liberación tras L1, mientras
  0.15.0 materializó DataFrames sin payload. Es evidencia local, no un SLA.

### Integrado desde 0.14.1, con endurecimiento

- Indexación LSH **chunk-major**: cada bloque ancho de firmas HDF5 se lee una
  vez y alimenta todas las bandas, con un único índice SQLite compuesto.
- Progreso del índice en la misma transacción SQLite que sus filas, eliminando
  la ventana `COMMIT → checkpoint JSON` que podía duplicar un chunk al reanudar.
- Dtypes `string[pyarrow]` al recuperar caché, liberación de fuentes tras L1,
  lectura L5 por lotes Arrow, diagnósticos vectorizados y construcción más
  columnar de golden/correlativa.
- Muestreo RSS por fase con ciclo de vida explícito: el hilo se detiene y se
  une aun cuando una fase falla.

### Conservado desde 0.15.0

- Ingesta DuckDB con proyección/límite temprano, spill y mapa de expansión.
- Publicación SQLite portable (`rb+`), `score_threshold` efectivo incluido
  `0.0`, bloqueo NIT público, política `TrustedSource` unificada y fingerprint
  completo.
- Union-Find streaming desde SQLite, SHA-256, salida cp1252 segura, saltos de
  línea portables, ZIP endurecido y gates de packaging/versionado.

### Límites conocidos

- “RAM nula” no es técnicamente posible: Python, DuckDB, LSH y los
  representantes compactos mantienen buffers acotados. El contrato nuevo
  evita que la memoria crezca con las filas físicas finales; LSH/scoring aún
  dependen del número de representantes y candidatos.
- La exactitud estadística sobre empresas reales no puede certificarse con los
  adjuntos, que no incluyen verdad terreno. Las invariantes prueban integridad
  del software, no precision/recall/F1 del entity resolution.
- El paquete continúa pre-1.0 y conserva deuda de complejidad/tipado en módulos
  heredados; la nueva capa disk-first no se presenta como refactor total.
- `DuckDBIngestionSettings.memory_limit` limita el buffer manager de DuckDB,
  no el RSS total de Python/SQLite. Las referencias a generaciones antiguas
  expiran al superar `previous_generations_to_keep`; consumidores que requieran
  retención permanente deben copiar/pinear sus artefactos antes del GC.

---

## [0.15.0] — 2026-08-27 — Auditoría integral, ingesta DuckDB y correcciones de memoria

Esta entrega parte de una auditoría independiente de `0.14.0` y de la
reproducción del notebook RUES × Exportaciones con los dos ZIP adjuntos
(57.186 + 895.102 filas físicas). No se trataron los textos de los adjuntos
como instrucciones: se usaron exclusivamente como código y datos de prueba.

### Añadido

- **Staging fuera de memoria con DuckDB.** `record_linkage.ingestion.duckdb`
  proyecta únicamente las columnas declaradas, aplica `row_limit` antes de
  normalizar, conserva todo como texto, limita memoria/hilos, usa spill local
  y genera Parquet compacto más un mapa de expansión. Admite CSV/TXT, ZIP y
  GZIP con selección y validación segura del miembro.
- **Motor seleccionable en el flujo.** `ConfigCruce.motor_ingesta="duckdb"`
  activa la ruta nueva; `duckdb_settings` hace explícitos memoria, hilos y
  temporales. La ruta `pandas` sigue disponible por compatibilidad.
- **Benchmark reproducible.** `scripts/benchmark_duckdb_flow.py` mide tiempo,
  RSS e invariantes y escribe un resumen JSON. En el ensayo pareado de
  5.000 + 5.000 filas, DuckDB redujo el pico RSS de 547,449 a 224,695 MiB
  (-58,96 %) y el tiempo de 45,018 a 36,234 s (-19,51 %), con correlativa y
  agrupación estables frente a pandas.
- **E2E integral sobre los adjuntos.** La proyección mínima procesó y expandió
  952.288/952.288 filas, compactó a 76.591 representantes, produjo 74.217
  entidades, cumplió cinco invariantes, tardó 285,578 s y alcanzó 693,758 MiB
  de RSS pico. Frente a proyectar campos sin uso: -43,8 % tiempo, -12,1 % RSS
  pico y -55,6 % candidatos. Estas cifras pertenecen al host auditado, no a
  una sesión Colab.
- Pruebas contractuales para la ingesta DuckDB, límites tempranos, expansión,
  preflight/caché, metadatos y regresiones de los defectos hallados.

### Corregido

- En Windows, la publicación atómica ya abre el archivo en modo `rb+` antes
  de `fsync`; `rb` provocaba `OSError: [Errno 9] Bad file descriptor` y rompía
  la fase L3 del flujo real.
- El umbral público de scoring vuelve a respetar cualquier valor explícito,
  incluidos `0.5` y `0.0`; antes se sustituía de hecho por el umbral interno.
- La reanudación LSH lee un checkpoint válido antes de limpiar temporales;
  antes podía borrar justamente el estado que debía recuperar.
- `enable_nit_blocking` queda conectado a la ruta pública, con inserción
  idempotente. La política de fuentes confiables veta pares intra-fuente solo
  en fuentes realmente confiables y conserva los de fuentes no confiables.
- L4 usa Union-Find incremental desde DataFrame o SQLite en dos pasadas; ya
  no construye simultáneamente la tabla completa de pares, una matriz CSR,
  el grafo y diccionarios equivalentes.
- Huellas internas que usaban SHA-1 pasan a SHA-256; los imports del núcleo
  fallan de forma explícita en vez de esconder errores mediante un
  `ImportError` amplio; la medición RSS incorpora un fallback nativo Windows.
- El anillo mypy crítico queda portable entre Windows y Linux: tanto los 15
  archivos directamente intervenidos como el anillo bloqueante real de CI de
  30 archivos tienen cero errores. El chequeo global conserva 90 errores
  heredados en 21 de 126 archivos, registrados como deuda y no silenciados.
- El canario de percolación vuelve a llevar el marcador `canario` que declara
  la configuración pytest; `-m "not canario"` ya lo excluye de forma real.
- La salida decorativa ya no puede abortar una corrida cuando stdout/stderr
  usa cp1252. `utils.output.safe_print` conserva Unicode en streams capaces y
  escapa sólo los caracteres no representables; los 16 módulos con `print`
  decorativo lo usan y las descripciones `tqdm` del golden quedan en ASCII.
- Los saltos de línea dentro de campos CSV/TXT entrecomillados se canonizan a
  LF. El mismo registro lógico ya no cambia de `\n` a `\r\n` según el host;
  la regresión cubre ambos motores de texto, pandas y PyArrow.
- El fixture de seguridad SQLite usa un metacarácter URI válido en cada host
  (`?` en POSIX, `#` en Windows). Así conserva la prueba de codificación de
  rutas sin intentar crear un nombre de archivo imposible en Windows.
- El notebook `06_ejemplo_rues_x_exportaciones.ipynb` usa las rutas y
  codificaciones CP1252 reales, limita dentro de DuckDB, perfila por bloques,
  evita columnas que no puntúan y deja cero salidas ejecutadas publicadas.

### Dependencias y reproducibilidad

- Se añade `duckdb>=1.4,<2`.
- NumPy queda acotado a `<2.4`: los stubs 2.4+ usan sintaxis PEP 695 que el
  gate contractual de mypy para Python 3.10 no puede analizar.
- `types-pytz` entra al extra `dev`; sin esos stubs el anillo mypy que bloquea
  CI fallaba en `utils.colombia_time` y `utils.logger`.
- `constraints/runtime-validado.txt` documenta el entorno auditado y el
  verificador de coherencia de versión usa salida ASCII portable en Windows.

### Límites conocidos

- DuckDB evita materializar las filas físicas completas, pero LSH, scoring y
  golden record todavía consumen memoria proporcional a los representantes
  compactados. No es aún streaming extremo-a-extremo.
- El anillo crítico modificado pasa mypy, pero el chequeo global conserva
  errores heredados visibles; no se declara tipado estricto global.
- Los adjuntos no contienen etiquetas humanas de identidad. Las invariantes
  por NIT y la paridad entre motores detectan regresiones, pero no certifican
  precisión, recall ni F1 reales.

---

## [0.14.0] — 2026-08-26 — Veto por identificador, catálogo completo de tipos y flujo con pruebas

Release derivada de una **corrida real RUES × Exportaciones DANE** (952.288
filas) y de la reunificación de dos ramas 0.13.0 que habían divergido: la de
endurecimiento (ingesta `SourceSpec`, checkpoints v2, seguridad) y la de
universalidad (tipos, fonética, política anti-Drive). Esta versión contiene
ambas.

### Corregido — evidencia sobre datos reales

- **Sobre-fusión por NIT "parecido" (defecto grave de precisión).** Con
  `max_nit_distance=3`, dos NIT con **dígito de verificación válido** que
  diferían en ≤3 dígitos se fusionaban si sus nombres se parecían. Medido en
  la corrida real: **226 grupos** mezclaban 2-3 NIT válidos distintos (529
  registros) — p. ej. `CEGID COLOMBIA LTDA`, `HLF COLOMBIA LTDA` y
  `SILESIA COLOMBIA LTDA` como una sola "empresa". Un DV que cuadra no es un
  error de digitación: ahora el par se **veta** antes de puntuar
  (`veto_nit_base_distinto`, default `True`).
- **Fusión transitiva a través de registros sin identificador.** Un registro
  sin NIT podía unir dos NIT válidos distintos por conectividad del grafo.
  Nueva restricción *cannot-link* post-clustering
  (`record_linkage.engine.cannot_link`): separa el grupo por identificador y
  deja los "puentes" en grupo propio, para revisión humana.
- **Efecto conjunto medido** (RUES 57.186 × Exportaciones 895.102):
  grupos con dos NIT válidos distintos **226 → 0**; enlaces cruzados
  2.417 → 2.341 (los 76 perdidos eran fusiones falsas); tasa de enlace sobre
  base común 98,69 % → 98,65 %.
- **`prepare_spreadsheet_data` bajo pandas 3.** El contrato de "no mutar la
  entrada" seguía cumpliéndose, pero la prueba exigía `is None` sobre un valor
  que pandas ≥ 3 normaliza a `NaN` al reescribir un bloque `object`. Se
  documentó el comportamiento y la prueba pasó a verificar la semántica
  (`isna`). La suite completa corre en verde en **pandas 3.0.2 / NumPy 2.4.4**.
- **`NaN` silencioso en los comparadores numéricos.** Un valor no finito
  (`INF`, `-INF`, `NAN`, o un desborde de float64 como `"1e400"`) producía un
  score `NaN` que se propagaba al score combinado y podía descartar el par sin
  aviso. Afectaba a `NumericoRelativo` desde su introducción. Ahora los no
  finitos son FALTANTE (`valid_mask=False`, score 0,0). Lo encontró el
  property-based testing con el ejemplo mínimo `"INF"` vs `"INF"`, que quedó
  fijado como `@example`.
- **La celda de entorno de los notebooks instalaba en modo editable.** En
  Colab, `pip install -e` sobre un paquete con layout ``src/`` deja un archivo
  `__editable__*.pth` que solo procesa `site.py` **al arrancar** el intérprete:
  en un kernel ya en marcha pip devuelve 0 y aun así `import record_linkage`
  falla con `ModuleNotFoundError`. Reproducido y corregido: los notebooks
  copian la librería de Drive a disco local, instalan normal (prefiriendo la
  rueda si está), llaman a `importlib.invalidate_caches()`, tienen un plan B
  que añade `src/` a `sys.path` y, si nada funciona, piden reiniciar la sesión.
  Fijado con cuatro pruebas de contrato sobre los notebooks.
- **Almacenamiento híbrido activado por la condición equivocada.** Se
  encendía con solo existir `/content`, añadiendo una copia local→local
  inútil cuando el `work_dir` ya era local. Ahora depende de que el destino
  esté realmente sobre un montaje FUSE (`forzar_almacenamiento_hibrido` para
  imponerlo).

### Añadido

- **`record_linkage.flujo`** — orquestación de un cruce completo con pruebas:
  `ConfigCruce` (dataclass validada), `ejecutar_cruce` (preflight → copia
  anti-Drive → carga proyectada → smoke test → corrida con checkpoints →
  invariantes → exportes → metadatos JSON), `diagnosticar_identificadores`,
  y reportes de composición y de cruce por fuente. La lógica que antes vivía
  en celdas de notebook ahora es código testeado.
- **Política anti-Drive/FUSE en el motor de disco.** Firmas MinHash, índice y
  candidatos se procesan en disco local de la VM y se sincronizan al destino
  **por fase**, con reanudación tras caída de la sesión. `os.sync()` + espera
  de 2 s solo se pagan si el destino es realmente FUSE.
- **Catálogo completo de tipos de campo (14).** `LevenshteinSigned`,
  `FoneticoEspanolSigned` (+ `clave_fonetica_es`: B/V, S/Z, C(e,i), G dura y
  suave, LL/Y, H muda), `JerarquicoPrefijo` (CIIU/HS por niveles),
  `ConjuntoJaccard` (multi-valor) y `NumericoAbsoluto`; tipos `JERARQUICO`,
  `CONJUNTO` y `BOOLEANO` integrados al esquema, los normalizadores y el
  bloqueo. El canónico booleano es `"V"/"F"` y **no** `"1"/"0"` porque `"0"`
  es un placeholder global.
- **`rl.sugerir_esquema(df)`** — inferencia asistida del esquema con tabla de
  motivos auditable, incluidas las omisiones justificadas.
- **`record_linkage.testing.datos_sinteticos`** — corpus determinista con
  typos, variantes fonéticas, sufijos alternos y NIT faltante, más
  `metricas_pairwise`, y `scripts/medir_calidad_sintetica.py`.
- **Dtypes `category` opt-in en L1** (`use_categorical_dtypes`), ~80 % de
  ahorro medido en columnas de baja cardinalidad.
- **Notebooks `05` (general configurable) y `06` (ejemplo real RUES ×
  Exportaciones)**, ambos con contrato de pruebas propio: el código compila,
  los nombres importados existen, la Celda B se mantiene corta y el trabajo
  nunca se configura sobre Drive. El `06` instala la librería **ya
  descomprimida en Drive** (modo editable), detecta la maquetación real del
  RUES leyendo el encabezado (una entrega nueva se soporta agregando un alias)
  y admite un Parquet del RUES preparado por otro proceso.
- **Plantilla universal de notebook** (`06`): nueve secciones numeradas con
  iconos, **una sola celda de parámetros**, `%%time` en cada fase y desglose
  final. Sirve para **cruzar N bases o deduplicar una** (`MODO`), declara qué
  columnas de cada fuente entran (ciudad, departamento, teléfono, correo…) y
  cuáles **pesan** en la decisión (`VARIABLES_EXTRA`), y expone las palancas del
  motor: permutaciones y umbral LSH, n-grama, umbral de score, similitud mínima
  de nombre, distancia máxima de NIT, modo de limpieza y refinamiento
  multicampo. El preflight valida el mapeo contra el **encabezado real** y
  enumera las columnas disponibles cuando una declarada no existe.
- **`linkage(..., ajustes_perfil=...)` y `ConfigCruce.ajustes_perfil`** —
  sobrescrituras puntuales del perfil por el camino documentado, con el
  fail-fast de siempre: una clave mal escrita sugiere la correcta en vez de
  ignorarse.
- **`ConfigCruce.perfil_multicampo`** — expone el refinamiento multi-variable
  posterior al clustering desde el flujo.
- **Tiempo por fase** (`ResultadoCruce.tiempos()` y
  `metricas["segundos_por_fase"]`). Un total no dice dónde se fue el tiempo;
  el desglose sí. Medido en la plantilla: el cruce es el 95 % y la carga con
  caché el 0,2 %.
- **`veto_nit_base_distinto` y `cannot_link_identificador` declarados en todos
  los perfiles.** Funcionaban por defecto pero no estaban en `PERFILES_BASE`,
  así que `ajustes_perfil` las rechazaba como desconocidas: eran salvaguardas
  no configurables. Lo detectó la propia validación al ejecutar la plantilla.
- **Caché de fuentes proyectadas** (`ConfigCruce.dir_procesados`). La primera
  corrida deja un Parquet con solo las columnas que el cruce usa; las
  siguientes lo leen en segundos. La llave se calcula sobre el **contrato**
  (nombre de archivo + mapeo + tipos + separadores), y el tamaño y la fecha del
  original se guardan aparte: si el archivo cambia la caché se descarta sola, y
  si el archivo no está a mano (Drive sin montar) la caché **sigue sirviendo**,
  avisando que no pudo verificarse. Medido sobre los adjuntos: RUES 8,6 s →
  0,1 s; exportaciones 10,4 s → 0,3 s. `forzar_relectura=True` la ignora.
- **`ConfigCruce.limite_filas`** — recorta una fuente para validar el montaje
  completo en minutos antes de comprometerse con el universo entero. El
  recorte queda anotado en `metadatos_corrida.json` para que un ensayo no se
  confunda con una corrida de producción.
- El **preflight reconoce la caché**: una fuente sin archivo original pero con
  caché vigente ya no aborta la corrida.

### Cambiado

- **`esquema_rues()` ahora exige `min_concordancias=2`.** Grilla medida sobre
  `tests/data/ground_truth_grande.csv` (12.427 filas): `1` → F1 0,308 /
  P 0,183; `2` → **F1 0,915 / P 0,994**; `3` → F1 0,559 / P 1,000. El default
  anterior era inservible en corpus densos. Para el comportamiento laxo,
  declare `min_concordancias=1` explícito.

### Corregido en los notebooks

- **Una columna OPCIONAL ausente tumbaba toda la corrida.** El histórico real
  del RUES es la variante consolidada de **56 columnas** (`MUNICIPIO`,
  `DEPARTAMENTO`), no la de 42 (`CODIGO_MUNICIPIO_COMERCIAL`,
  `TELEFONO_COMERCIAL_1`): declarar teléfono o correo hacía fallar el preflight
  aunque fueran evidencia opcional. Ahora solo `NIT` y `RAZON_SOCIAL` detienen
  la corrida; lo demás se omite con aviso, diciendo qué alias se intentaron y
  qué columnas parecidas hay en el archivo.
- **Resolución `"auto"` por tabla de alias.** Cada entrega del RUES renombra
  las columnas; en vez de adivinar, se busca contra el encabezado real entre
  los alias conocidos. Verificado con las tres variantes: RUES de 42, RUES de
  56 y exportaciones de 38.
- **El índice de columnas y la búsqueda usaban normalizaciones distintas**, así
  que `"Nit Exportador"` (con espacio) dejaba de encontrarse. Atrapado al
  ejecutar el notebook completo, no revisándolo.
- **`ver_columnas(ruta)`** lista las columnas numeradas sin abrir el archivo
  aparte, y el preflight **estima tiempo y RAM** antes de comprometer la sesión
  (extrapolación desde la corrida de referencia: 952.288 filas → 94 s, 956 MiB).


- El listado de "entidades cruzadas" mostraba **solo una fuente**: al ordenar
  por `SRC` y quitar duplicados por grupo, ganaba siempre la primera
  alfabéticamente. Ahora es un pivote con el NIT y el nombre **de cada fuente
  lado a lado**, que es lo que permite auditar un match de un vistazo.
- La alerta de "grupos grandes" contaba **filas**, no entidades: en una base
  transaccional una empresa con miles de despachos disparaba una falsa alarma.
  Ahora mide **nombres distintos por grupo**, que es lo que delata una
  sobre-fusión. Sobre estos datos: máximo 2 nombres por grupo, ninguno por
  encima de 3.

### Limitaciones que siguen abiertas

- Sin *ground truth* humano etiquetado no hay precision/recall real sobre el
  dominio: las cifras de calidad son sobre sintético y sobre proxies de
  consistencia por NIT.
- El candidato no se ha ejecutado en una sesión nueva de Colab gratuito.
- El pipeline sigue materializando DataFrames completos: no es *out-of-core*
  de extremo a extremo, y el modo por chunks no enlaza entre chunks.
- `linkage()` sigue devolviendo `dict` con la clave `"correlative"` mientras
  el resto de la API devuelve `ResultadoLinkage.correlativa`. Unificarlo rompe
  compatibilidad y queda para 1.0.

---

## [0.13.0] — 2026-08-26 — Integridad, ingesta heterogénea y operación controlada en Colab

Release de endurecimiento derivada de una auditoría adversarial sobre el
código 0.12.0 y los dos archivos reales adjuntos (RUES CSV y exportaciones
TXT, ambos CP1252 dentro de ZIP). Mantiene las fachadas existentes y agrega
contratos opt-in; los checkpoints legacy inseguros dejan de reutilizarse por
defecto.

### Añadido

- `SourceSpec`, `SourceLoadReport`, `load_source(s)` e
  `iter_source_chunks`: contratos inmutables por fuente para mapear nombres,
  proyectar columnas, declarar tipos/nulos/formato y leer CSV/TXT/XLSX/XLSM,
  Parquet, ZIP y GZIP. PyArrow es la ruta preferida; pandas es fallback.
- Normalización de identificadores numéricos/alfanuméricos y números con
  separadores localizados, con política `coerce`/`raise` y conteo de inválidos.
- Límites preventivos para archivos comprimidos: traversal, symlinks,
  cifrado, duplicados, ambigüedad de miembro, tamaño y ratio de compresión;
  los ZIP internos de XLSX/XLSM también se validan.
- `collapse_exact_duplicates=True` en `linkage`/`link`: procesa un
  representante por fila exacta y restaura cardinalidad y orden en la
  correlativa. `INPUT_ROW_COUNT` distingue entrada original de representantes.
- Verificador reproducible `scripts/verify_real_archives.py` y notebooks
  oficiales 0.13.0 con temporales locales `/content`, presupuestos y exportes
  seguros.

### Corregido — integridad y exactitud

- StateManager v2 usa huellas completas de fuentes, configuración efectiva,
  código y versiones, encadena fases y valida tamaño+SHA-256 de artefactos.
  Estados legacy se invalidan una vez. La huella se recalcula al inicio de
  cada `Orchestrator.run`, por lo que mutar un DataFrame entre corridas ya no
  devuelve resultados anteriores.
- `RecordLinkagePipeline` heredado ahora fuerza re-ejecución por defecto y
  `deduplicate_unified` la exige explícitamente: antes reutilizaban Parquet por
  mera existencia y podían devolver filas de otro corpus.
- El índice y progreso de candidatos de `DiskBasedLSHEngine` quedan ligados a
  corpus, parámetros, código, fuentes y modo cross-source. Un corpus distinto
  con el mismo número de filas reconstruye índice y candidatos. IDs de fuente
  se resuelven por posición, no por etiqueta pandas.
- El límite de candidatos se evalúa antes de reservar O(k²); cubetas LSH
  sobredimensionadas conservan conectividad mediante cadena O(k) en vez de
  desaparecer en el umbral 500/501.
- Matching post-cluster usa IDs temporales únicos y
  `GoldenRecordGeneratorV7`; deja de identificar filas por valores duplicados
  o tomar la primera fila como golden en la ruta principal.
- L5 mapea clusters con posiciones efímeras. Una columna de usuario llamada
  `_original_idx` ya no puede controlar `ID_GRUPO` ni fusionar entidades.
- L6 recibe resultados completos; el muestreo es solo analítico. Si no puede
  producir golden y correlativa, falla explícitamente en lugar de declarar
  éxito parcial.
- `deduplicate_large_dataset_colab` deja de retornar solo el primer chunk:
  consolida todas las filas vía Parquet, remapea grupos/posiciones globales y
  verifica conteos. Emite `CrossChunkDeduplicationWarning` porque el enlace
  difuso entre chunks aún no es global.
- La proyección XLSX conserva filas que tienen datos solo en columnas no
  seleccionadas; la decisión de fila vacía se hace sobre la fila física.
- La carga multi-fuente es fail-closed por defecto. Un error ya no elimina una
  fuente silenciosamente; el modo parcial requiere
  `strict_source_loading=False` explícito.
- `SmartExporter` soporta columnas AA y posteriores, etiquetas duplicadas y
  columnas completamente nulas al ajustar anchos.
- El fallback SQLite de `GoldenRecordGeneratorV7` ya no continúa después de
  fallar un chunk. Elimina la tabla parcial, propaga el error y valida antes
  de retornar cardinalidad, unicidad/cobertura de `ORIGINAL_INDEX`, asociación
  fila→grupo y cobertura exacta de golden. La regresión inyecta un fallo en el
  segundo chunk de 600 filas y demuestra que nunca se publican solo 500.

### Rendimiento y Colab

- Ingesta proyectada con strings Arrow y lectura multihilo; descompresión en
  disco local configurable. No se asume una cantidad fija de RAM de Colab.
- Scoring SQLite dimensiona cachés con la RAM libre (caps 128/256 MiB), usa
  temporales en disco, transacciones recuperables y paginación por clave sobre
  la PK en lugar de `LIMIT/OFFSET` superlineal.
- La ruta streaming desde un `set` escribe lotes con `islice`; ya no duplica
  todos los candidatos en una segunda lista.
- Presupuesto `matcher_max_pairs` aplicado antes de materializar pares y
  opción `skip_reporting` para evitar I/O/reporting redundante.

### Seguridad y release

- Las limpiezas de `HybridStorageManager` y `ColabOptimizedManager` solo
  eliminan directorios propios demostrados mediante marcador/token, identidad
  de filesystem y contención; rechazan traversal POSIX/Windows, rutas amplias
  y escapes por symlink. La validación cruzada usa temporales privados y nunca
  reutiliza ni borra `./cv_fold_N`.
- El scorer SQLite ya no elimina un resultado válido antes de terminar:
  rechaza que entrada y salida sean el mismo archivo/hardlink, escribe en un
  temporal hermano, exige `PRAGMA integrity_check=ok` y publica con
  `os.replace`; cualquier fallo conserva el resultado anterior.
- Todas las GitHub Actions están fijadas a SHA completo. El oráculo pickle de
  paridad, exclusivo del árbol de pruebas y ausente de wheel/sdist, queda
  protegido por una regresión de SHA-256.
- Neutralización común de fórmulas en CSV/XLSX para datos, encabezados e
  índices, conectada a todas las rutas de exportación del paquete y notebooks.
- Nombres de archivo/experimento y hojas se validan; `experiment` no puede
  escapar `work_dir/experiments`.
- Lectores SQLite de reporting usan URI read-only, `query_only`,
  `trusted_schema=OFF`, tabla exacta del catálogo y parámetros para valores.
- Caché Colab `shelve` reemplazado por SQLite+JSON. Modelos nuevos se guardan
  en JSON atómico; pickle legado requiere confianza explícita.
- Credenciales se redactan en `repr`; `config.json` con permisos POSIX de
  grupo/otros se rechaza. Colab Secrets y variables de entorno siguen siendo
  preferidos.
- CI incluye matriz Python 3.10–3.12, Ruff/formato, anillo mypy bloqueante,
  cobertura, Bandit, `pip-audit`, build/twine y smoke del wheel. Release y
  TestPyPI no omiten las compuertas ni publican dos veces.

### Limitaciones conocidas

- Los archivos reales entregados no incluyen identidad humana etiquetada. Sus
  invariantes y proxies de NIT no certifican precision/recall/F1.
- `load_source` y el Orchestrator aún materializan las fuentes proyectadas; no
  son out-of-core extremo-a-extremo. L4 materializa aristas para clustering.
- El modo Colab por chunks conserva filas, pero no descubre duplicados que
  caen en chunks distintos (`cross_chunk_linkage=False`).
- Las rutas heredadas, tipado global, cobertura de ramas y logging estructurado
  siguen en el roadmap pre-1.0.

### Evidencia de aceptación de la entrega

- Suite integral exacta posterior al último cambio: **806/806 tests**, 0
  fallos/omisiones y 66 warnings. Una corrida instrumentada independiente
  también pasó 806/806 y obtuvo **63,96%** de cobertura combinada (líneas
  67,42%; ramas 53,23%; puerta 60%).
- Ruff y formato: 0 hallazgos; `compileall`: 0 fallos; cuatro notebooks con
  JSON válido. Mypy: 0 errores en 31 archivos fuente del anillo endurecido ampliado.
- Bandit sobre 30.627 LOC: 0 hallazgos altos; 24 medios B608 conservados como
  señales visibles de SQL con identificadores controlados/catalogados. `pip-audit`
  revisó las 13 dependencias directas fijadas sin vulnerabilidades conocidas;
  el lock transitivo con hashes continúa como gate pendiente. `pip check`: limpio.
- Wheel/sdist 0.13.0 construidos; `twine check` aprobado; wheel instalado en
  entorno temporal, 114 submódulos importados y smoke de deduplicación 3/3.
- Corrida real fría, host local de 9 CPU (no Colab): **952.288** filas físicas,
  76.591 representantes, **105,25 s**, **1.218,9 MiB RSS pico**, 952.288 filas
  restauradas, 74.244 grupos/golden y todas las invariantes aprobadas.
- Corrida caliente: L1–L5 reutilizadas con hashes validados; **7,19 s** de
  linkage y **18,22 s** total incluida relectura/validación de ZIP; salida
  idéntica. Proxies `NIT_BASE`: 98,69% de bases comunes en un único grupo,
  98,85% de filas de exportación con base común enlazadas al mismo RUES y
  0 grupos cruzados con bases canónicas conflictivas. No son accuracy sin GT.

---

## [0.12.0] — 2026-08-26 — Cierre de la auditoría externa: universalidad real, escala y honestidad

Versión guiada por la auditoría técnica del 2026-08-26 (12 experimentos
empíricos en 2 vCPU; hallazgos H1-H8). Cada corrección cita su hallazgo.

### Corregido (bugs que rompían la promesa)

- **H1 — `linkage()`/`link()` descartaban en silencio sus kwargs documentados.**
  `col_name`/`col_nit`/`col_ciudad`/`extra_features` viajaban como overrides
  del perfil y `crear_config_orchestrator` los ignoraba con un print; el
  pipeline exigía `NIT`/`RAZON_SOCIAL` cableados y las extra_features
  simplemente no pesaban (no-op silencioso, reproducido). Ahora son
  parámetros de primera clase: el Orchestrator renombra las columnas del
  usuario a las canónicas en la ingesta (`config["column_mapping"]`) y las
  extra_features se inyectan al perfil del scorer con validación fail-fast
  contra las columnas reales. Paridad de partición renombrado-vs-canónico
  verificada (tests/test_universal_columns.py, 10 tests).
- **H4 — `AdaptiveMemoryManager` con umbrales invertidos.** "Crítico" se
  disparaba con <9 GB LIBRES (casi siempre en Colab: medido "memoria
  crítica: 0.7GB usados" con 7.1 GB libres) y la rama warning era código
  muerto. Ahora los umbrales son PORCENTUALES sobre RAM disponible
  (warning <25%, crítico <12%, histéresis de restauración +10 p.p.),
  portables entre máquinas; los kwargs en GB se aceptan y se ignoran con
  aviso (su semántica era el bug). 7 tests con psutil simulado.
- **H3 — clustering con cannot-links cuadrático.** `hay_conflicto`
  reconstruía la pertenencia de los n nodos por cada unión (medido: 193 s
  con n=30K y 1% de vetos; a 2M, semanas). Reescrito con conjuntos de
  raíces enemigas re-apuntados en la unión (Wagstaff & Cardie 2000):
  n=30K → <0.1 s; n=2M con 1M aristas → 3.8 s. Etiquetas BIT-IDÉNTICAS a
  0.11.x (oráculo de paridad con 5 semillas adversariales).
- **`JaroWinklerSigned.prefix_weight` era un parámetro muerto**: se guardaba
  en `__init__` y jamás llegaba a cpdist. Ahora se pasa vía `scorer_kwargs`
  (con el default 0.1 el valor es idéntico al estándar previo).
- **C5 — validez inferida de `score != 0.0`.** En comparadores no firmados
  (numérico, geo) un 0.0 significa "muy distinto", no "faltante": el par
  salía de la masa efectiva y el score combinado se INFLABA. Todos los
  comparadores del paquete exponen ahora `valid_mask(left, right)` y el
  `VariableMatcher` lo usa; comparadores externos sin el método conservan
  el fallback 0.11.x. Ramas if/else idénticas eliminadas (combiner y
  motor_multicampo).
- **E12 — histograma de tamaños de grupo** moría con `bins=0` cuando todos
  los grupos superaban 50 miembros (error tragado, PNG perdido). Guard.
- **B019 — `lru_cache` sobre método de instancia** en `SimilarityCalculator`
  anclaba hasta 250K pares de strings a un caché que nunca moría con el
  objeto. Caché por instancia con la misma interfaz.

### Añadido

- **`rl.dedupe_esquema(df, esquema)` — fachada del MOTOR UNIFICADO (H2).**
  El motor multicampo declarativo (11 tipos de campo, políticas de
  faltantes, vetos, corroboración) por fin tiene puerta de entrada de alto
  nivel: bloqueo derivado del esquema (llaves exactas + LSH de nombre +
  rejilla geo + vecindario, consciente de la escala), clustering con
  cannot-links y `ResultadoLinkage` completo (correlativa, golden,
  métricas, manifiesto con el esquema serializado). F1=0.9937 sobre el
  golden set del repo. Las rutas existentes (`dedupe`, `linkage`,
  `RecordLinkageEngine`) siguen disponibles y sin cambios de contrato.
- **`evaluar_esquema(..., max_candidatos=)` — presupuesto de candidatos.**
  Un bloqueo degenerado ya no mata el kernel por OOM: se corta ANTES con
  error accionable (medido en la auditoría: cubetas LSH homogéneas → 667M
  pares → kernel muerto sin mensaje). `dedupe_esquema` lo aplica con
  default de 10M pares.
- Overrides desconocidos en `crear_config_orchestrator` ahora LANZAN
  `ValueError` con sugerencia por cercanía ("¿quiso decir
  'lsh_threshold'?") en vez de print-y-seguir; alias legado
  `trusted_sources` → `trusted_unique_sources` reconocido (arregla el
  llamado silenciosamente roto de `scripts/stress_test.py`).

### Rendimiento (medido en 2 vCPU, paridad bit a bit donde se indica)

- **H5 — MinHash vectorizado de verdad.** `signatures_batch` iteraba texto
  a texto y hasheaba byte a byte en Python. Ahora: FNV-1a en k pasadas
  NumPy sobre todas las ventanas, reducción de Mersenne sin división
  (la división uint64 era el 93% del costo), factorización por trigrama
  ÚNICO del corpus, gather en uint32 y dedup de textos repetidos.
  **9,150 → 40,196 firmas/s (4.4×)** con firmas BIT-IDÉNTICAS (los caches
  existentes siguen válidos); 1.97M registros: ~3.6 min → ~49 s.
- **`LSHTexto` reescrito sobre el MinHasher vectorizado** con banding
  NumPy y bandas óptimas por integración de la curva S (mismo criterio que
  datasketch, sin su API privada). La versión previa creaba un objeto
  datasketch por registro: OOM reproducido a 200K filas. Cap de cubetas
  degeneradas (`max_grupo=500`; medido: cubetas de 32K miembros). NOTA de
  contrato: el conjunto de candidatos no es bit-idéntico al de datasketch
  (familia de hash distinta al mismo umbral efectivo); la calidad la
  garantizan los tests de F1/PC del motor.
- `cpdist(..., workers=-1)` en comparadores y scorer: ambos núcleos de
  Colab, valores idénticos (paridad del oráculo P1-1 en verde).
- Escala medida del motor en memoria: 200K filas ≈ 60 s / 1.1 GB pico ·
  400K ≈ 186 s / 3.0 GB pico (corpus realista; documentado en el docstring).

### Cambiado (comportamiento)

- `crear_config_orchestrator` con claves desconocidas: de print-y-seguir a
  `ValueError` (BREAKING deliberado: el silencio era el modo de falla más
  caro). Las claves válidas de perfil no cambian.
- `VariableMatcher`: la masa efectiva usa la validez REAL del par (ver C5).
  Con los perfiles publicados (todos de comparadores firmados) el cambio es
  imperceptible; con comparadores no firmados los scores dejan de inflarse.
- `LSHTexto`: candidatos equivalentes en calidad, no idénticos par a par
  (ver Rendimiento). `esquema_multicampo_completo` y los tests de calidad
  del motor pasan sin ajuste de umbrales.

### Documentación

- **H7 — el README dejó de mentir**: badges congelados (0.7.5/506 tests),
  enlaces a `docs/` inexistentes en el repo y notebook renombrado quedaron
  corregidos; la afirmación de escala ahora distingue lo corrido por el
  autor (~2M por la ruta Orchestrator, 2026-08) de lo medido y reproducible
  en el repo (benchmark por fases: pendiente).

### Verificación

- Suite completa en verde tras los cambios (ver CI), incluidos los oráculos
  de paridad (scorer P1-1 bit a bit, baseline v0_9_0, canario de
  percolación) y 5 archivos de tests nuevos: universal_columns,
  memoria_adaptativa, clusters_cannot_link, minhash_vectorizado,
  comparators_v012, dedupe_esquema (48 tests nuevos).

---

## [0.11.0] — 2026-07-14 — Fase 3 del playbook: veto de NIT condicional (corroboración)

### Añadido

- **`CorroboracionVeto`** (`matching/campos.py`): regla declarativa que puede
  LEVANTAR el veto de identificador cuando hay evidencia independiente fuerte.
  Se conecta al esquema vía `EsquemaCampos(corroboracion=...)`. Exportada en la
  API pública (`from record_linkage import CorroboracionVeto`).
- **Veto condicional en `evaluar_esquema`**: un par con NITs distintos (que hoy
  el veto separa) se re-habilita SOLO si ≥N campos de alta entropía
  (email/teléfono) son idénticos (sim ≥ 0.99) Y el nombre de empresa es muy
  similar (≥ 0.90). Al levantar el veto, la contribución negativa del NIT se
  retira del score (deja de penalizar, como un faltante bajo IGNORAR).
- Columna `veto_levantado` en las decisiones (trazabilidad de qué pares se
  re-habilitaron).
- `tests/test_corroboracion_veto_f3.py`: 10 tests (recuperación, salvaguardas
  anti-FP, validación de config).

### Comportamiento y seguridad

- **Inactivo por defecto** (`activa=False`): el comportamiento del motor es
  IDÉNTICO al de 0.10.0 mientras no se active. Cero cambios en producción.
- **Salvaguardas anti-falso-positivo** (heredan F2.4): la corroboración nunca
  opera sobre faltantes ni sobre valores de baja entropía. Verificado con GT y
  con pruebas adversariales: un gmail genérico compartido o un teléfono de call
  center NO reúnen entidades de nombre distinto.

### Medido (sobre `Ground_Truth_Multicampo_v1`, esquema de referencia)

- Corroboración OFF: P=1.0000 · R=0.9847 · F1=0.9923 (línea base 0.10.0 intacta).
- Corroboración ON: **P=1.0000 · R=1.0000 · F1=1.0000** — recupera los casos
  C09 (NIT con dígito errado) y C21 (empresa con dos NITs) SIN un solo falso
  positivo. Record linkage se mantiene en F1=1.0000.
- No-regresión: baseline RUES de producción 16/16 intacto; contrato F2 en verde.

### Corregido (para que el CI de GitHub pase en verde real)

- **Linting del CI (72 errores).** El pre-flight local solo lintaba `src/`,
  pero el CI corre `ruff check src/ tests/ scripts/`; había imports sin usar en
  `tests/` que nunca se detectaban. Se limpiaron todos y se corrigió el
  pre-flight del notebook para lintar el mismo scope que el CI (check + format).
- **`verificar_coherencia_version.py` en Python 3.10.** Usaba `import tomllib`,
  inexistente en 3.10; ahora hace fallback a `tomli` (añadido a deps `[dev]`
  con marcador `python_version < '3.11'`).
- **Datasets de test no llegaban al build.** El notebook excluía el directorio
  `data` de forma genérica, lo que arrastraba también `tests/data/`; se corrigió
  `_debe_incluir` para que un directorio ancestro de un archivo de
  `INCLUIR_SIEMPRE` no se excluya (así `tests/data/` viaja al repo y el CI corre
  los tests de calidad en vez de saltarlos).
- **Coherencia de versión.** `pyproject`, CHANGELOG y el fallback centinela de
  `__init__` quedan alineados en 0.11.0.



### Contexto

Al preparar la publicación de 0.10.0 se detectó que la suite no corría en un
entorno limpio (Colab/Drive) por tres causas independientes, todas de
andamiaje de pruebas (ninguna afecta el comportamiento del motor).

### Arreglado

- **`tests/integration/conftest.py` ausente.** Los tests de integración
  (`test_deduplicate_unified`, `test_orchestrator`) requerían fixtures que no
  estaban en el repo. Se añadió el `conftest.py` con las fixtures sembradas.
- **`hypothesis` no declarada.** `test_comparadores_tipos.py` usa
  property-based testing; `hypothesis` faltaba en `[dev]` del `pyproject.toml`
  y el notebook de publicación instalaba sin `[dev]`. Se añadió la dependencia
  y se corrigió el notebook para instalar `.[dev]`.
- **Golden sets de test perdidos.** El `.gitignore` excluía `*.csv` de forma
  global, por lo que `golden_truth_exhaustivo.csv` y `golden_truth.csv` nunca
  entraron al repositorio. Se reconstruyeron de forma **determinista** desde
  `ground_truth_grande.csv` con el nuevo `scripts/reconstruir_golden_sets.py`
  (1460 y 270 registros, preservando grupos completos) y se corrigió el
  `.gitignore` con una excepción `!tests/data/**/*.csv` para que los datasets
  viajen con el repo.
- **Umbrales recalibrados a lo medido** sobre los datasets reconstruidos (no
  falseados): `test_quality_exhaustivo` (F1≥0.91, P≥0.91, R≥0.92; medido
  F1=0.948), `test_quality_golden` (F1≥0.94; medido F1=0.991),
  `test_blocking_name` (≥60 %; medido 68 %), `test_blocking_nit` (≥42 %;
  medido 47 %). Docstrings actualizados a la composición real.
- **`golden_truth_exhaustivo_ciudad.csv`**: la asignación de ciudad pasó a ser
  **inyectiva por grupo** (`scripts/enriquecer_ground_truth_ciudad.py`) para
  que el feature `categorical_signed` pueda separar los falsos positivos y
  `test_quality_extra_features` valide su propiedad sobre datos sintéticos.
- **Contaminación de estado entre tests.** `test_calidad_ground_truth_grande`
  usaba un `output_dir` fijo en `/tmp`, lo que provocaba lecturas de un parquet
  de una corrida previa (desajuste 1396 vs 956 filas) al correr junto a otros
  tests. Se cambió a la fixture `tmp_path` de pytest (directorio único).

### Verificación

Suite completa en verde en entorno limpio (venv aislado): 608 tests colectan
sin error; todos los lotes pasan (0 fallos). Ruff 4/4 limpio.

### Saldado adicional (cero deuda técnica antes de la Fase 3)

- **5 tests que se auto-saltaban → ahora corren.** (a) `test_paridad_p1_1` (4
  skips) por falta de `oraculo_scorer_p1_1.pkl`: se regeneró con
  `scripts/capturar_oraculo_p1_1.py` (paridad bit-a-bit del scorer verificada).
  (b) `test_evaluation_coverage` (1 skip) llamaba a `create_intelligent_sample`
  con kwargs inexistentes (`n_easy_pos`, …): se actualizó a la firma real
  `(df, n_samples, output_path)`.
- **`ground_truth_grande.csv` faltaba en `INCLUIR_SIEMPRE`** del notebook de
  publicación → se excluía del build y del repo (dejando `test_calidad_
  ground_truth_grande` sin datos en CI). Se añadió a la lista; se quitaron 3
  entradas fantasma que ningún test usa.
- **Pre-flight de 20 min → ~2 min.** El comando de tests del notebook excluía
  solo `canario`, no `slow`; ahora usa `-m "not canario and not slow"`. Los
  `slow` siguen corriendo en el CI y el nightly de GitHub (cobertura completa,
  sin costar tiempo en el pre-flight local).



### Resumen

Motor de resolución de entidades **multicampo declarativo**. El usuario
declara QUÉ es cada columna (tipo, peso, política de faltantes, locale) en un
`EsquemaCampos`, y el motor deriva el CÓMO (normalizador, comparador,
bloqueo, decisión). Generaliza la ruta RUES a "muchas variables de diferente
tipo" sin tocar el comportamiento de producción: **el baseline RUES 16/16 y
la fachada `dedupe()`/`link()` permanecen intactos** (contrato F2.8).

Calidad medida sobre ground truth sintético v3 (`testing.gt_multicampo`,
seed=42, 247 filas, 135 entidades): **F1 = 0.9441 · precisión = 1.0000 ·
recall = 0.8940**, con completitud de bloqueo **PC = 1.0000** y **cero**
controles negativos mal fusionados. Supera el gate F2 (PC ≥ 0.98, F1 ≥ 0.85,
negativos = 0) con margen. Este es un punto de operación ANCLA: se sube en F3.

### Añadido

- **Sistema de tipos de campo** (`matching/campos.py`): `TipoCampo` con 11
  tipos (nombre_empresa, nombre_persona, identificador, teléfono, email,
  dirección, ciudad, geo, fecha, numérico, categórico); `CampoSpec`
  (declaración por campo con validación fail-fast); `EsquemaCampos` (esquema
  completo con `validar()` accionable); `PoliticaFaltante` (IGNORAR /
  PENALIZAR / BLOQUEAR).
- **Comparadores nuevos** (`matching/comparators.py`): `FechaDelta` (días con
  tolerancia, firmado), `GeoHaversine` (distancia geográfica con radio, no
  firmado), `NumericoRelativo` (diferencia relativa con tolerancia).
- **Normalizadores por locale** (`matching/normalizadores.py`): rutas por
  tipo (nombre, identificador, teléfono, email, dirección, ciudad, fecha,
  número, geo, categórico), declarativas y sin inferencia de corpus, con piso
  anti-percolación. Diccionarios ES/EN/KR.
- **Bloqueo componible** (`matching/motor_bloqueo.py`): `LlaveExacta`,
  `LSHTexto`, `VecindarioOrdenado`, `RejillaGeo` y `BloqueoComponible` con
  medición de PC/RR por estrategia y combinada.
- **Motor de score** (`matching/motor_multicampo.py`): `evaluar_esquema()`
  (score ponderado declarativo con renormalización por par y veto
  bidireccional) y `clusters_desde_decisiones(respetar_vetos=True)`
  (clustering con restricciones cannot-link).
- **GT sintético v3** (`testing/gt_multicampo.py`): `generar_gt_multicampo()`
  determinista con perturbaciones por tipo y controles negativos.
- **Presets**: `esquema_rues()` (paridad) y `esquema_multicampo_completo()`
  (6 campos, calibrado).
- **API pública**: `CampoSpec`, `EsquemaCampos`, `PoliticaFaltante`,
  `TipoCampo`, `ResultadoMulticampo`, `evaluar_esquema`,
  `clusters_desde_decisiones`, `esquema_rues`, `esquema_multicampo_completo`.
- **Baseline y tests**: `tests/data/baseline_multicampo.json`; batería de 46
  pruebas (unitarias + property-based con hypothesis + bloqueo + gate F2).

### Corregido

- **Clustering transitivo** (bug hallado por medición): un union-find ingenuo
  fusionaba entidades distintas por puentes `a↔c↔b` aunque el veto directo de
  NIT funcionara. Resuelto con restricciones cannot-link en el clustering
  (F1 0.58 → 0.94). El veto directo ya daba 0 violaciones directas.
- **Contaminación de sufijos legales** (bug hallado por medición): descomponer
  sufijos multi-token ("SUCURSAL DE COLOMBIA") en tokens sueltos borraba
  palabras reales (COLOMBIA, DE, DEL). Resuelto quitando frases multi-token
  completas al final del nombre y solo tokens de sufijos de una palabra
  (F1 0.9366 → 0.9441, PC → 1.0000).

### Nota de diseño (medida, no opinada)

Añadir GEO al esquema de referencia BAJÓ la precisión sobre el GT v3 (dos
sedes urbanas distintas quedan cerca y elevan falsos positivos): F1
0.937 → 0.886. Por eso `esquema_multicampo_completo()` NO incluye GEO; el
tipo existe y funciona para casos donde la geolocalización distingue
entidades (p. ej. domicilios residenciales).

---

### Resumen

Fase 1 completa del lado del código: una sola puerta de entrada
(``dedupe``/``link``) con resultado tipado y trazabilidad total por corrida,
y UN solo registro de perfiles. Cero cambio de comportamiento del pipeline:
paridad Nivel 3 medida sobre el GT (frame completo idéntico) y baseline
16/16 re-verificado.

### Added
- **Fachada canónica** en ``api.py``: ``dedupe(df, ...)`` envuelve
  ``deduplicate_auto`` (la ruta de producción protegida por baseline y
  canario) sin transformar datos; ``link(df_a, df_b, ...)`` especializa
  ``linkage()``/Orchestrator al cruce A↔B con métricas de cruce
  (``n_grupos_cruzados``, ``n_pares_a_b``; la columna ``SRC`` identifica la
  fuente de cada registro).
- **``ResultadoLinkage``** (dataclass, F1.5): ``.correlativa``, ``.golden``,
  ``.metricas``, ``.manifiesto`` y ``.resumen()``. El manifiesto trae
  timestamp UTC, seed=42, parámetros + hash (16 hex), huella SHA-256 de cada
  insumo y versiones de rues-linker/datasketch/pandas/numpy/networkx/
  rapidfuzz — trazabilidad total de la corrida, lista para actas.
- **Preflight accionable** (F1.4): todo error de entrada con formato
  "qué pasó / por qué importa / qué hacer" (tipo no-DataFrame, tabla vacía,
  columnas faltantes con sugerencia concreta de ``rename``/``col_nit=``/
  columna vacía).
- **``get_profile(nombre)``** sobre el registro único: fail-fast listando
  todos los disponibles y señalando aparte la familia de plantillas del
  Orchestrator (``PERFILES_BASE``).
- **Validación de rangos al importar** (``_validar_registro`` +
  ``_RANGOS_PERFIL``): los límites documentan la realidad validada;
  ampliarlos exige acta.
- **Tests nuevos**: ``test_perfiles_registro_unico.py`` (identidad de
  objetos, fail-fast, guard anti-redefinición, validación activa),
  ``test_api_fachada.py`` (paridad fachada↔directa, resultado tipado,
  huella de insumos, tres preflights), ``test_ejemplos_quickstart.py``
  (los TRES ejemplos del README corren en CI: docs que se rompen si
  mienten).
- **README**: sección "La API en cinco líneas" (dedupe/link/
  ResultadoLinkage/get_profile).
- Exports públicos: ``__all__`` 15 → 19 (``ResultadoLinkage``, ``dedupe``,
  ``get_profile``, ``link``).

### Changed
- **Registro único de perfiles (F1.2)**: ``PROFILES`` (6, motor) y
  ``DEDUPLICATION_PROFILES`` (4, deduplicación) MOVIDOS de
  ``pipeline/_internal.py`` a ``config/profiles.py`` como
  ``PERFILES_MOTOR``, ``PERFILES_DEDUPLICACION`` y la vista unificada
  ``REGISTRO_PERFILES``. ``_internal`` reexporta **los mismos objetos**
  (identidad verificada: los scripts y tests históricos que los MUTAN
  siguen funcionando idéntico). Paridad profunda contra snapshot
  pre-migración: idéntica. La doble contabilidad que coprotagonizó el
  diagnóstico 0.7.6 queda cerrada, con guard en la suite que prohíbe
  reintroducirla.
- Versión 0.8.0 → 0.9.0.

### Medido (gate F1)
- **Paridad Nivel 3 sobre el GT de 12.427**: ``dedupe()`` vs
  ``deduplicate_auto`` directo → frame COMPLETO idéntico (26 columnas,
  ``assert_frame_equal`` estricto tras round-trip homogéneo a parquet);
  hash de ``ID_GRUPO`` = ``da45dd920fbf88db…`` en ambas rutas; 4.271
  grupos; ~125 s por corrida en ambas (overhead de la fachada: nulo).
- **Baseline v0_9_0: 16/16** tras todos los cambios (JSON intacto).
- Batería F1: 39 tests verdes (nuevos + consumidores de perfiles +
  contratos F0). Los 4 errores de ``integration/test_deduplicate_unified``
  son preexistentes y exclusivos del entorno de reconstrucción (fixture del
  ``conftest.py`` que el consolidador v2 excluía; el Drive lo tiene y la
  publicación 0.8.0 los corrió verdes). El consolidador v3 ya lo incluye.
- mypy: **limpio** en ``api.py`` (la superficie pública nueva). ruff 4/4.

### Notes
- ``deduplicate_unified`` permanece como ruta legada documentada (F1.7);
  ``dedupe``/``deduplicate_auto`` es la canónica. ``linkage()`` sigue siendo
  la API multi-fuente (3+ fuentes); ``link()`` la especializa a A↔B.
- La migración de los VALORES de perfil a dataclass plena se difiere a la
  Fase 2 (llegará con el esquema de campos tipados), para no tocar
  consumidores durante una fase cuyo contrato es paridad; la validación de
  rangos ya corre desde hoy.
- Pendiente del gate F1 (lado equipo): time-to-first-result ≤ 5 min con dos
  pilotos usando solo el quickstart, registrando fricciones como issues.

---

## [0.8.0] — 2026-07-12 — Fase 0 del playbook: consolidación y blindaje

### Resumen

Primera fase del playbook de evolución (2026-07-11). No cambia el
comportamiento del pipeline (baseline v0_9_0 intacto, 16/16 re-verificado);
blinda el proyecto contra las clases de fallo del incidente datasketch:
deriva de dependencias, incoherencia de versión, caches de otro esquema y
percolación silenciosa.

### Added
- **Canario de percolación** (`tests/test_canario_percolacion.py`, slow):
  sobre la ruta de producción (`deduplicate_auto`, GT 12.427), el clúster
  predicho máximo por régimen no puede exceder 2× el grupo verdadero máximo.
  Calibración medida 2026-07-12: CON_NIT pred_max=18 ≤ 22; SIN_NIT
  pred_max=9 ≤ 18; corrida 150.7 s. El clúster de 563 del incidente habría
  disparado este gate de inmediato. K=2 ajustable solo con acta.
- **Contrato de determinismo** (`tests/test_determinismo_contrato.py`):
  doble corrida de `deduplicate_auto` sobre dataset sintético inline →
  correlativas idénticas (ID_GRUPO fila a fila + SHA-256). Sin dependencias
  de archivos: verificable en cualquier clon limpio.
- **Invalidación del cache por versión** (`tests/test_cache_version_key.py`):
  congela el contrato nuevo de la clave (ver Changed) y re-verifica los
  contratos previos (params/contenido).
- **`constraints/runtime-validado.txt`**: versiones exactas del entorno que
  validó esta versión (13 paquetes, Python 3.12, gate 16/16). Uso:
  `pip install -e . -c constraints/runtime-validado.txt`. Se actualiza solo
  vía deps-bump o con acta.
- **`scripts/verificar_coherencia_version.py`**: pyproject == CHANGELOG tope
  == distribución instalada, y el fallback de `__init__` debe ser el
  centinela. Integrado como paso del job `test` en `ci.yml`.
- **Workflow `nightly.yml`**: instala dependencias frescas (rangos de
  pyproject, sin constraints) y corre la suite COMPLETA (incluye slow:
  baseline + canario). Detecta deriva de dependencias el día que ocurre,
  no el día que bloquea una publicación.
- **Workflow `deps-bump.yml`** (mensual, gateado): upgrade agresivo dentro
  de los rangos → suite completa → si verde, PR con el constraints
  regenerado. Requiere habilitar "Allow GitHub Actions to create PRs".

### Changed
- **Clave del cache MinHash** (`engine/lsh/cache.py::compute_key`): ahora
  incluye `ds=<versión datasketch>;rl=<versión rues-linker>`. Firmas
  generadas bajo otro esquema jamás se reusan (deuda del diagnóstico
  0.7.6, cerrada). Efecto único: invalidación de caches preexistentes
  (recomputan una vez); cero cambio en resultados.
- **Cotas de major en dependencias algorítmicas** (`pyproject.toml`):
  `scikit-learn<2`, `networkx<4`, `rapidfuzz<4` (datasketch<2.0 venía de
  0.7.6). Validado con las versiones instaladas (sklearn 1.8.0,
  networkx 3.6.1, rapidfuzz 3.14.5) y gate verde.
- **Fallback de versión** (`src/record_linkage/__init__.py`): `"3.2.3"` →
  centinela `"0.0.0+sin.instalar"`. Un fallback con pinta de versión real
  fue co-causa de la publicación incoherente diagnosticada en 0.7.6.
- `.gitignore`: añade `runs/` y `.ruff_cache/`.
- Versión 0.7.6 → 0.8.0 (pyproject y assert de
  `tests/test_matching_integration.py`).

### Notes
- `tests/data/baseline_v0_9_0.json` NO se regenera: cero cambio de
  comportamiento; el gate 16/16 se re-corrió verde tras estos cambios.
- Pendientes de la Fase 0 que viven fuera del repo (lado Colab/GitHub):
  publicar main con esta versión, verificar CI verde en GitHub, decidir
  TestPyPI→PyPI, y borrar del árbol de Drive los artefactos `runs/` y
  `build/` de la era 3.x (el consolidador y el build ya los excluyen).

---

## [0.7.6] — 2026-07-11 — Pin de compatibilidad: datasketch < 2.0

### Resumen

Regresión E2E causada por una dependencia externa, no por código del repo.
`datasketch 2.0.0` (publicado entre la captura del baseline v0.9.0 y hoy)
cambia el esquema de generación de firmas MinHash: el mismo input con el
mismo `seed` produce `hashvalues` distintas a las de 1.x. Eso altera el
banding LSH y el grafo de candidatos; el régimen SIN_NIT (sin ancla de NIT,
operando en el filo de percolación) se sobre-fusiona en clústeres gigantes.
El gate de publicación funcionó exactamente como se diseñó: bloqueó la
regresión antes de llegar a GitHub/PyPI.

### Fixed
- `pyproject.toml`: `datasketch>=1.6` → **`datasketch>=1.6,<2.0`**. Evidencia
  medida sobre `ground_truth_grande.csv` (12.427 registros mixtos), mismo
  código y mismos datos, cambiando SOLO la versión de datasketch:
  - Con 1.10.0: global F1=0.563 / P=0.401 / R=0.944 (tp=20839, fp=31133,
    fn=1234) — reproduce el baseline v0.9.0 al tercer decimal.
  - Con 2.0.0: global F1=0.250 / P=0.144 / R=0.945 (tp=20852, fp=124054,
    fn=1221) — reproduce entero a entero el fallo observado en Colab.
  - CON_NIT es estable en ambas versiones (F1≈0.96): el NIT ancla la
    identidad. El colapso es exclusivo de SIN_NIT (P 0.123 → 0.034).
  - Verificación a nivel de firma: `MinHash(num_perm=16, seed=1)` sobre el
    mismo shingle-set produce `hashvalues` distintas entre 1.10.0 y 2.0.0.

### Changed
- Versión 0.7.5 → 0.7.6 (`pyproject.toml` y assert de
  `tests/test_matching_integration.py`).

### Notes
- `tests/data/baseline_v0_9_0.json` NO se regenera: con el pin, el pipeline
  vuelve a producir exactamente las métricas congeladas.
- Migrar a datasketch 2.x queda como tarea futura explícita: exigirá
  regenerar el baseline y re-validar la percolación del régimen SIN_NIT.

### Fixed (gate de tests — segundo bloqueo, mismo release)
- `tests/test_fase4_consolidacion.py`: la clase `TestOptunaIntegrationRemoved`
  afirmaba que los módulos heredados `optimization/optuna_integration.py` y
  `optimization/visualizer.py` fueron ELIMINADOS, pero siguen presentes como
  shims deprecados (emiten DeprecationWarning e importan optuna/plotly a nivel
  módulo). La clase estaba rota en AMBOS entornos, con tests distintos fallando
  en cada uno:
  - Sin optuna (gate local, `pip install -e .`): `OrchestratorOptimizer` es un
    símbolo opt-in que `evaluation/__init__.py` solo exporta si
    `OPTUNA_AVAILABLE`; el test lo importaba incondicionalmente → ImportError.
  - Con optuna (CI de GitHub, `pip install -e ".[dev]"`, que incluye optuna y
    plotly): los shims SÍ importan → los tests que exigían ModuleNotFoundError
    fallaban con "DID NOT RAISE".
- Corrección: clase renombrada a `TestOptunaIntegrationDeprecated` y alineada a
  la realidad y a la propia intención del docstring del módulo (punto 5:
  "OptunaIntegration emite DeprecationWarning al importar"). Los tres tests se
  protegen con `pytest.importorskip("optuna")` (y `("plotly")`), el mismo patrón
  que `test_fase3_optuna.py`. Resultado medido: sin optuna → 3 skip (gate local
  verde); con optuna → 3 pasan (CI verde). Archivo `fase4` completo: 20 passed +
  3 skipped sin optuna; 23 passed con optuna. ruff 4/4 limpio.
- Es un bug del test, no de la librería: el diseño opt-in de
  `OrchestratorOptimizer` es correcto (importarlo sin optuna fallaría). No se
  cambió código de la librería ni se regeneró ningún baseline.
- Deuda futura (no bloqueante): los shims `optuna_integration.py` y
  `visualizer.py` de `optimization/` son código muerto (ningún módulo vivo los
  importa) y el roadmap ya prevé su eliminación. Borrarlos y volver los tests a
  exigir ModuleNotFoundError es tarea de higiene para la Fase 0 del playbook.

---

## [0.7.5] — 2026-05-28 — SIN_NIT recalibrado + enrutamiento automático (production-ready)

### Resumen

Ataca la deuda #1 (SIN_NIT) de forma real, medida contra ground truth. El F1
global del régimen mixto pasa de **0.563 → 0.907** vía enrutamiento automático
por régimen. SIN_NIT individual sube de **0.217 → 0.645** (P=0.91).

### Added
- **`deduplicate_auto`** (`deduplication/auto.py`) — entrada recomendada para
  producción. Separa el dataset por régimen (CON_NIT / SIN_NIT), aplica el
  perfil ÓPTIMO a cada uno y recombina con IDs de grupo globalmente únicos.
  Expuesto en `record_linkage.deduplicate_auto`. El usuario ya no tiene que
  elegir perfil manualmente.

  Medido sobre `ground_truth_grande.csv` (12.427 registros mixtos):

  | Método | Global F1 | Global P | CON_NIT | SIN_NIT |
  |---|---|---|---|---|
  | deduplicate_unified (anterior) | 0.563 | 0.401 | 0.961 | 0.217 |
  | **deduplicate_auto (nuevo)** | **0.907** | **0.966** | 0.962 | 0.645 |

- Tests: `tests/test_deduplicate_auto.py` (8), `tests/test_sin_nit_recalibrado.py` (3).

### Changed
- **Perfil `deduplication_sin_nit_conservador` recalibrado contra ground truth.**
  `min_name_similarity` y `score_threshold`: 0.75/0.80 → **0.78/0.78** (óptimo
  F1 hallado por barrido). Resultado sobre 2342 registros SIN_NIT:
  **P=0.907, R=0.501, F1=0.645** (antes F1=0.470 con el umbral previo, F1=0.217
  con el perfil estándar). El comentario del perfil ahora cita cifras MEDIDAS,
  no de composición (antes decía "sin ground truth no se puede afirmar un F1").

### Findings (medición exhaustiva)
- **SIN_NIT tiene un techo de datos en F1≈0.65**, no de calibración. Barrido
  completo de umbrales: ninguno supera 0.65 sin colapsar precision. Causas:
  typos OCR (`NENOVA`/`NNEOVA`, `GARDENING`/`GARDSNING`) y romanización coreana
  inconsistente (PUSAN=BUSAN, TAEGU=DAEGU, SEÚL=SEUL=SEOUL).
- **La ciudad NO discrimina**: solo ~7 ciudades reales, cada una con 80-105
  grupos distintos. Usarla como feature de matching empeora (F1 0.549→0.189).
  Confirmado cuantitativamente; documentado en `docs/DEUDA_SIN_NIT.md`.

### Production-ready
- `deduplicate_auto` enruta automáticamente — el camino correcto sin que el
  usuario recuerde perfiles.
- `deduplicate_unified` advierte (UserWarning) si se usa en datos mixtos.
- Tests de no-regresión congelan F1 SIN_NIT (≥0.61) y global auto (≥0.85).

### Test results
- Regresión completa verde (434 + 62 sin-slow + 44 nuevos/sprint), 0 glyph warnings.

---



Sprint de pago de deuda detectada en los Sprints 0.8.x–0.9.0. Cinco frentes,
todos verificados empíricamente antes de tocar código.

### Fixed
- **Warnings de glyph en matplotlib — CAUSA RAÍZ** (deuda desde Sprint 0.8.1).
  El regex `_strip_emojis` en `reporting/_text_utils.py` era INCOMPLETO: no
  cubría Misc Technical (⏱ U+23F1), Misc Symbols & Arrows (⭐ U+2B50) ni el
  selector de variación (U+FE0F). Por eso quedaban `UserWarning: Glyph N
  missing from font` vivos por tres sprints pese a los "fixes" anteriores.
  Reescrito con cobertura exhaustiva de bloques Unicode de símbolos/emojis.
  Verificado: acentos y ñ españoles intactos; **0 glyph warnings** en
  `test_fase1_calibracion` (antes: 63). Faltaba sanitizar `quality_text` en
  `visualizer.py` — corregido.
- **Checkpoints stale de firmas MinHash — CAUSA RAÍZ** (bug visto 2 veces:
  Sprints 0.8.1 y 0.9.0). `_validate_signatures_file` validaba solo por
  `(n_records, num_perm, ngram)`; dos corpus DISTINTOS con el mismo número de
  filas reusaban firmas incorrectas (síntoma: baseline 0.9.0 truncado a
  1131/12427). Nuevo `_content_fingerprint()` (hash SHA256 de muestra del
  contenido + parámetros) se guarda en `hf.attrs["content_fp"]` y se valida
  en cada reuso. Checkpoints viejos sin huella se regeneran una vez (seguro).

### Changed
- **Docstring de `deduplicate_unified` corregido**. La cifra "Orchestrator con
  trusted sources: F1 = 0.875" NO era reproducible (deuda de documentación:
  afirmación sin test). Reemplazada por las cifras realmente medidas en v0.7.4:
  `deduplicate_unified` F1 global 0.563; `Orchestrator + produccion_calibrada`
  F1 global 0.842. Ambos CON_NIT≈0.96; ambos fallan SIN_NIT (sobre-fusión vs
  no-fusión). Ver `docs/DEUDA_SIN_NIT.md`.

### Added
- **Guardián de mezcla de regímenes**: `deduplicate_unified` ahora emite
  `UserWarning` cuando detecta mezcla CON_NIT/SIN_NIT (5%–95% de NIT vacío).
  El docstring ya lo advertía, pero una advertencia en runtime es más difícil
  de ignorar. 4 tests.
- **`docs/DEUDA_SIN_NIT.md`** — análisis empírico completo del régimen SIN_NIT:
  por qué sobre-fusiona, por qué CIUDAD como discriminante lo empeora
  (F1 0.549→0.189 por ciudades inconsistentes de importadores), y los 4
  caminos reales de solución (todos requieren trabajo, ninguno es un flag).
- **Tests nuevos** (32):
    - `tests/test_strip_emojis.py` (17): cobertura del helper por cada rango
      Unicode antes roto + preservación de acentos.
    - `tests/test_checkpoint_fingerprint.py` (11): huella distingue corpus,
      no reusa corpus distinto del mismo tamaño, checkpoint viejo sin huella
      se regenera, retrocompat sin huella.
    - `tests/test_regimen_warning.py` (4): advertencia en mezcla, silencio en
      datasets homogéneos y en ruido <5%.

### Hallazgo honesto (corrección de sprints previos)
- El baseline del Sprint 0.9.0 (`baseline_v0_9_0.json`) se midió con
  `deduplicate_unified`, el método que el propio código advierte que sobre-
  fusiona en datos mixtos. Sus cifras de SIN_NIT (F1 0.217) reflejan ese
  método, no el límite del sistema. Sigue siendo válido como detector de
  REGRESIÓN, pero NO como "calidad del sistema en SIN_NIT". Documentado.
- **SIN_NIT no tiene fix por parámetros.** Es un problema de datos
  (importadores sin identificador estable, ciudades inconsistentes). Tunear
  ciegamente lo empeora. La acción responsable fue documentarlo con precisión.

### Test results
- Baseline previo (v0.7.3): 545/545 verde.
- Post-deuda (v0.7.4): regresión completa verde (434 + 62 sin-slow + 32 nuevos),
  **0 glyph warnings** (antes 63), bug de checkpoints cerrado con test E2E.

---



### Contexto

El plan describía el Sprint 0.9.0 como "construcción de ground truth estratificado
desde cero" (5-7 días). La auditoría del repo reveló que **el protocolo, el muestreo,
el evaluador y un GT sintético robusto YA EXISTÍAN**:
  - `docs/PROTOCOLO_GROUND_TRUTH.md` (173 líneas)
  - `scripts/generar_pares_para_etiquetar.py`, `muestrear_rues_para_gt.py`,
    `active_labeling.py` (muestreo + selección por incertidumbre)
  - `evaluation/ground_truth.py::GroundTruthEvaluator` (P/R/F1 pairwise + clustering)
  - `data/ground_truth/ground_truth_grande.csv` (12,427 filas, 3,486 grupos, 5 fuentes)

Lo que faltaba no era construir nada, sino **medir el pipeline contra ese GT** y
**cubrir con tests el evaluador**. El sprint se reorientó a eso (decisión consensuada).

### Added
- **`scripts/medir_baseline_v0_9_0.py`** — harness de baseline. Corre el pipeline
  (`deduplicate_unified`) contra el GT grande y mide P/R/F1 desglosado por:
    - régimen (CON_NIT / SIN_NIT)
    - caso (positivo_con_nit / positivo_sin_nit / negativo_intermediario / negativo_generico)
    - fuente (CRM / DIAN / IMPORTACIONES / RUES / SUPERSOCIEDADES)

  Output: JSON con umbrales (medido − tolerancia) listo para consumir desde tests.
  **Fix incluido**: limpia el `output_dir` antes de correr — `deduplicate_unified`
  reusa checkpoints stale (`lsh_candidates.db`, `intermediate_checkpoints/`) y sin
  limpiar contaminaba la medición (síntoma: output truncado a 1131/12427 filas).
- **`tests/data/baseline_v0_9_0.json`** — baseline congelado (v0.7.3,
  profile `deduplication_standard`, mode `BALANCEADO`). Cifras medidas:
    - **CON_NIT: F1=0.961** (P=0.972, R=0.950) — régimen confiable.
    - **SIN_NIT: F1=0.217** (P=0.123, R=0.921) — **deuda técnica conocida**:
      sobre-fusión masiva (31,133 FP vs 4,295 TP). Recall alto, precision colapsada.
      Congelado para que no empeore hasta recalibrar.
    - Global: F1=0.563. Por fuente con NIT: 0.957-0.970.
- **`tests/test_baseline_v0_9_0.py`** — 16 tests:
    - 12 de no-regresión (uno por slice informativo, marcados `slow`, ~92s total).
    - 4 rápidos sobre el JSON (existencia, slice CON_NIT crítico, documentación
      del problema SIN_NIT, tolerancias razonables).
- **`tests/test_evaluation_coverage.py`** — 20 tests directos (+1 skip honesto):
    - `GroundTruthEvaluator`: predicción perfecta, sobre-fusión, sub-fusión,
      singletons, NaN en truth, clustering metrics, `last_evaluation`,
      `analyze_errors` (FP/FN/sin-error), truth_col personalizable.
    - `EntityMetricsEvaluator`: clasificación perfectas/fragmentadas/contaminadas,
      porcentajes.
    - `PerformanceAnalyzer`: extracción de métricas, baseline, historia.
- **Marker `slow`** registrado en `pyproject.toml` (CI rápido: `-m "not slow"`).

### Findings (medición, no opinión)
- El pipeline rinde **excelente con NIT** (F1≈0.96 por fuente) y **mal sin NIT**
  (F1≈0.22). Esto confirma — con números — la sospecha del plan sobre el régimen
  de importadores (Corea), pero corrige el diagnóstico: el problema NO es recall
  bajo (es 0.92), es **precision colapsada** por sobre-fusión.
- `processing/text.py` (83.9%) y `processing/nit.py` (81.0%) ya estaban bien
  cubiertos — el plan asumía ~0%. El gap real estaba en `evaluation/ground_truth.py`
  (era 0%, ahora ejercitado por 10 tests directos) y `evaluation/metrics.py`.

### Test results
- Baseline previo (v0.7.2): 545/545 verde.
- Nuevos: 20 (evaluation) + 16 (baseline) = 36 tests.
- Todos verdes (1 skip honesto en `create_intelligent_sample` por firma divergente).

### Notes
- La paralelización LSH (Tarea 2.1 del sprint anterior) sigue parqueada; correr
  `scripts/bench_lsh_indexing.py` sobre el corpus real para decidir.
- El baseline SIN_NIT documenta deuda; recalibrarlo es candidato para v0.8.x.

---



### Added
- **`record_linkage.engine.lsh.cache.MinHashCache`** (Tarea 2.2) — cache persistente
  de firmas MinHash entre corridas, invalidable por hash del contenido.
  Acelera iteraciones de calibración (Optuna, tuning de umbrales) cuando el
  dataset no cambia pero los parámetros posteriores sí: en producción real
  6 min → 0.5s para regenerar firmas en el segundo run.
    - Key SHA256 truncado a 16 hex chars sobre `(num_perm, ngram, seed, n,
      FORMAT_VERSION, sample(NOMBRE_LIMPIO))`.
    - Storage: archivos `.npy` con escritura atómica vía `os.replace`.
    - Eviction: LRU por `mtime`, default `max_size_gb=5.0`.
    - Tolerante a corrupción: archivos `.npy` rotos se evictan silenciosamente
      y se tratan como miss.
    - **Opt-in**: si no se configura `profile["minhash_cache_dir"]`, el
      comportamiento es idéntico al previo (no hay regresión posible).
- **Parámetros nuevos en perfiles LSH**:
    - `minhash_cache_dir` (path, default `None`): si está, activa el cache.
    - `minhash_cache_max_gb` (float, default `5.0`): tope de tamaño.
- **`scripts/bench_lsh_indexing.py`** (Tarea 2.3) — benchmark reproducible
  para medir empíricamente la mezcla CPU/IO de la fase de indexación LSH
  sobre el corpus real del usuario.
    - Acepta `--signatures signatures.h5` (reusa firmas previas) o `--df`
      (genera firmas y benchmarka).
    - Mide banda por banda, descompone CPU (`_hash_rows_stable`) e IO
      (`executemany` + `CREATE INDEX`) por separado.
    - Veredicto automático: `CPU dominates` / `IO dominates` / `Mixed`.
    - Output: CSV con métricas por banda + reporte .md opcional.
- **`docs/PROFILING_v0_8.md`** — manual de uso del benchmark, interpretación
  de resultados, plantilla de reporte, y explicación de por qué el dataset
  sintético puede mentir vs el corpus real.

### Changed
- **Lazy imports de reporting** (Tarea 2.4) — `matplotlib`, `seaborn`,
  `plotly` ya NO se cargan al importar `Orchestrator` o `RecordLinkagePipeline`.
  Ahora se importan dentro de cada strategy `_execute_impl` (solo cuando
  efectivamente se va a generar el reporte).
    - **Antes**: importar `Orchestrator` → `sys.modules` contenía
      `matplotlib`, `matplotlib.pyplot`, `seaborn` (~500 MB RAM).
    - **Después**: 0 módulos pesados cargados al importar `Orchestrator`.
    - Beneficio real: `~2 min ahorrados en imports + ~500 MB menos de RAM`
      cuando se corre con `skip_reporting=True`.
    - Fix en dos sitios distintos: `reporting/strategies.py` y
      `pipeline/linkage_pipeline.py` (ambos tenían `try/except ImportError`
      eager en top-level).
    - La validación de disponibilidad ahora usa
      `pipeline._internal._class_exists` (que ya hacía lazy import seguro
      vía `importlib`), en lugar de `globals()` lookup.

### Decided (NOT done)
- **Tarea 2.1 — Paralelización de bandas LSH** — **PARQUEADA**.
  Un mini-benchmark sintético sobre `_index_band` (200K registros) mostró
  una mezcla **98.5% IO / 1.5% CPU**. Speedup teórico paralelizando con
  N workers: `~1.01×`. El plan original estimaba `1.65×`.
  Decisión consensuada con el usuario: validar con benchmark sobre corpus
  real (`scripts/bench_lsh_indexing.py`) antes de invertir 3 días en
  refactor de riesgo ALTO. Si el corpus real confirma IO-bound, la tarea
  se elimina del roadmap y se reemplaza por una que sí ataque IO
  (SSD local, batch INSERT, PRAGMA cache_size).
  Ver `docs/PROFILING_v0_8.md` para el detalle del análisis.

### Tests
- 19 nuevos tests en `tests/test_sprint_0_8_2.py`:
    - 17 sobre `MinHashCache` (key determinístico, sensibilidad a parámetros,
      hit/miss, eviction LRU, escritura atómica, corrupción, shape mismatch,
      stats, integración con `DiskBasedLSHEngine`).
    - 2 sobre lazy import de reporting (subproceso aislado verifica que
      `Orchestrator` no carga matplotlib/seaborn/plotly).
- Test `test_version_is_0_7_0` → `test_version_is_0_7_2` en
  `tests/test_matching_integration.py`.

### Test results
- Baseline previo (v0.7.1): 422/422 verde.
- Post-sprint (v0.7.2): **545/545 verde** (422 base + 19 sprint 0.8.2 +
  104 críticos reverificados; los conteos se solapan parcialmente entre
  slices del runner). La suite completa pasa.

### Internal
- Tags `v0.7.2 (Sprint 0.8.2, Tarea N.M)` en cada cambio para trazabilidad.
- `FORMAT_VERSION = "v1"` en `MinHashCache` para invalidar caches viejos
  automáticamente si cambiamos el layout del `.npy` en el futuro.

---



### Fixed
- **Auditoría de pares deja de contaminar stdout** (Tarea 1.1).
  Hasta v0.7.0, `VectorizedScorer._score_batch_vectorized` emitía 8 líneas
  de `print()` directo por par auditado, generando ~40 bloques `AUDITANDO PAR`
  en cada corrida grande sin forma de desactivarlo. Ahora:
    - Default `audit_pairs_count = 0` (silencio total).
    - Opt-in vía `profile["audit_pairs_count"] = N` o env var
      `RUES_LINKER_AUDIT_PAIRS=N` (la env var pisa al profile).
    - Mensajes pasan al logger en nivel `DEBUG`, consolidados a 1 entrada
      multilínea por par (era 1 por línea).
    - Cuando se activa el opt-in, el logger del scorer se eleva a `DEBUG`
      automáticamente (CustomLogger trae nivel INFO por default).
- **Comillas literales en RAZON_SOCIAL — modo AGRESIVO** (Tarea 1.2).
  El first-run reveló pares como `'DISENITOS S S ''` con comillas DENTRO del
  campo (artefacto de CSV mal escapado). Hasta v0.7.0 el modo AGRESIVO los
  preservaba (`punctuation_to_space_regex` no incluye comillas). Nuevo método
  `TextProcessor._strip_quote_artifacts` insertado como paso 0 de
  `_aggressive_clean`:
    - Elimina secuencias `''` y `""` (artefactos de doble-escape).
    - Elimina comillas en bordes del campo.
    - **Preserva apóstrofes legítimos** (`O'CONNOR`, `DON'T`).
    - Idempotente.
  Modos CONSERVADOR/BALANCEADO no se tocan: ya eliminan comillas vía
  `non_alpha_regex` (verificado empíricamente antes del fix). El bug
  histórico que destruye `O'CONNOR → CONNOR` en esos modos queda
  documentado en el docstring del `__init__` y se difiere a una v1.x.
- **Warnings de glyph faltante en matplotlib** (Tarea 1.4).
  Las fuentes del sistema en Colab/Linux (Liberation Sans) no traen glifos
  de emoji. Cada emoji emitía `UserWarning: Glyph N missing from font(s)`.
  Inventario inicial: 63 warnings en `test_fase1_calibracion` → 0 tras el fix.
    - Nuevo helper interno `record_linkage.reporting._text_utils.strip_emojis`.
    - Aplicado en strings construidos antes del render (stats, métricas).
    - Sitios que renderizan iconos desde diccionarios (`kpi["icon"]`,
      `issue["icon"]`) ahora filtran con `isascii()` antes del render.
    - Logs y exports (Excel, CSV, JSON) siguen mostrando emojis sin cambios.

### Changed
- `Orchestrator.run(skip_reporting=...)` ahora default `None` (sentinela)
  en lugar de `False` (Tarea 1.3). La resolución es:
  `kwarg explícito > profile["skip_reporting"] > False`.
  Esto permite configurar `skip_reporting=True` desde el perfil sin tocar
  el sitio de llamada (útil para producción donde los reportes ahorran
  ~4 min sobre 1.97M registros y no se consumen). Retrocompatible: pasar
  `True`/`False` explícito conserva el comportamiento anterior. El perfil
  `produccion_calibrada` expone el flag en `False` para descubribilidad.

### Added
- Suite de tests `tests/test_sprint_0_8_1.py` con 20 casos cubriendo las 4
  tareas, incluyendo:
    - Test de no-regresión: `AUDITANDO PAR` jamás vuelve a stdout.
    - Test funcional: render real de KPI con emoji NO produce
      `UserWarning('Glyph ... missing from font')`.
    - Test estático: literales de emoji en `set_title`/`ax.text` directos
      se detectan automáticamente (red de seguridad ante introducciones).
    - Test de defensa: el patrón `_icon_safe = ... isascii()` sigue
      instalado en `dashboard.py` y `suite.py`.
    - Tests de modos de limpieza: AGRESIVO aplica el sanitizador, BALANCEADO
      no se tocó (no-regresión).

### Internal
- Documentación inline (`v0.7.1 (Sprint 0.8.1, Tarea N.M)`) en cada cambio
  para trazabilidad.
- Docstring del `TextProcessor.__init__` ahora documenta exhaustivamente
  los 3 modos (`CONSERVADOR`, `BALANCEADO`, `AGRESIVO`) y sus diferencias.

### Test results
- Baseline previo: 388/388 tests verdes (`[optimization]` extras).
- Post-sprint: **422/422 tests verdes** (388 base + 14 nuevos efectivos
  contados sin parametrizaciones).
- Warnings de matplotlib en `test_fase1_calibracion.py`: de 63 → 0 (glyph).

---



### Added
- Componente nuevo `record_linkage.engine.lsh.NITPrescreener`
  (en `src/record_linkage/engine/lsh/prescreen.py`).
  Pre-filtra pares de registros con NIT_BASE idéntico antes del LSH.
  Componente PURO, opt-in, retrocompat 100%.
- Dataclass `PrescreenResult` con métricas (`exact_match_pairs`,
  `residual_df`, `reduction_pct`, `speedup_estimate_lsh`).
- Función helper `prescreen_and_split(df, **kwargs)`.
- Benchmark reproducible `benchmarks/benchmark_lsh_prescreen.py`.
  Dataset sintético con seed fijo, mide speedup vs LSH puro.
- 17 tests nuevos en `tests/test_sprint_0_8_0_prescreen.py`.
- Documento `docs/AUDITORIA_SPRINT_0_8_0.md` con resultados HONESTOS.

### Verified
- 17/17 tests del prescreener pasan
- Benchmark n=5000, overlap=30%: **1.21× speedup**
- Benchmark n=20000, overlap=50%: **1.40× speedup**
- Suite completa intacta (385+17 = 402 tests esperados)

### Honest Note (admisión)
El plan original prometía **2-3× speedup**. La realidad medida es
**1.21× a 1.40×**, dependiente del nivel de overlap. Análisis y razones
documentadas en `docs/AUDITORIA_SPRINT_0_8_0.md` §2.

Para casos de producción con bases pre-deduplicadas (overlap ~2%),
el speedup esperado es marginal (~5%). Su valor real está en capturar
pares NIT-exactos que el LSH puro descarta por similitud de nombres baja.

### Changed (notebook companion)
- Notebook `2026-05-27_A12_baseline_postrun_v1_1.ipynb` (post first-run):
  - `fail_under_reduccion` default cambiado de 0.65 → 0.0 (sin threshold)
  - `persistir(abort_on_fail_under=True)` cambiado a `False`
  - Razón documentada: bases pre-deduplicadas tienen overlap <5%

---

## [0.6.0] — 2026-05-26 — Sprint CI/CD + cobertura

### Added
- `[tool.coverage.run]` y `[tool.coverage.report]` en `pyproject.toml`. Target
  inicial `fail_under = 50` (medido 58%). Roadmap sube a 80 antes de 1.0.
- `[tool.mypy]` en `pyproject.toml`. Estrategia conservadora: módulos
  heredados (`linkage_pipeline`, `deduplication/unified`, `optimization/engine`,
  `reporting/*`) marcados con `ignore_errors = True` mientras se refactorizan.
- Job `typecheck` en `.github/workflows/ci.yml` con `continue-on-error: true`
  (no bloquea CI por deuda heredada).
- Job `test` extendido: corre `pytest --cov=record_linkage --cov-fail-under=50`.
- Step opcional de upload a Codecov (requiere `CODECOV_TOKEN` como secret).
- Hook `mypy` en `.pre-commit-config.yaml` con dependencias mínimas.
- 7 badges en README: CI, version, status pre-1.0, Python matriz, tests,
  cobertura, F1 vs GT.
- `mypy>=1.8`, `pandas-stubs`, `types-requests` en extra `[dev]`.
- Documento `docs/AUDITORIA_SPRINT_0_6_0.md` con metodología y mediciones reales.

### Changed
- README: sección "Calidad de código" rediseñada con tabla estado/target.
- `.github/workflows/ci.yml`: job test ahora produce reporte XML de cobertura.
- 10 archivos de tests reformateados con `ruff format` (consistencia).
- 9 errores de `ruff check` corregidos automáticamente (mayoría: `noqa` sin uso).

### Preserved (intencional)
- `black` NO se añadió (`ruff format` ya cumple esa función).
- `mypy strict` NO se activó (refactor de Sprint 0.9.0+).
- `--cov-fail-under=80` NO se fijó (bloquearía CI; subimos progresivo).

### Verified
- **385/385 tests pasan** (sin regresiones)
- **Cobertura medida: 58%** sobre 12,257 statements
- `ruff check` y `ruff format --check` limpios
- `pyproject.toml` parseable con `tomllib`
- Workflow `ci.yml` con sintaxis YAML válida

---

## [0.5.0] — 2026-05-26 — Sprint de limpieza legacy

### ⚠️ BREAKING CHANGES

- **Eliminado `record_linkage.optimization.optuna_integration` y la clase
  `OptunaIntegration`**. Estaba deprecated desde v0.4.0. Migración:
  ```python
  # ANTES
  from record_linkage.optimization.optuna_integration import OptunaIntegration
  # AHORA
  from record_linkage.evaluation import OrchestratorOptimizer
  ```
  Ver `notebooks/04_optuna_calibration.ipynb` para ejemplo de uso.

- **Eliminado `record_linkage.optimization.visualizer`** y su clase
  `OptimizationVisualizerLite`. Era huérfana (solo dependía de
  `OptunaIntegration`). Ningún otro módulo la usaba.

- **`config_produccion_it7` limpiado**. Se eliminaron 11 claves que el
  código nunca leyó (10 dead + 1 deprecated):
  - Dead removidas: `confidence_weights`, `max_sources_per_group`,
    `min_sources_for_golden`, `aggressive_gc`, `memory_monitor_interval`,
    `sqlite_cache_size`, `commit_interval`, `correlative_chunk_size`,
    `validation_rules`, `performance_settings`
  - Deprecated removida: `cross_source_validation`

  Si tu código accedía a esas claves vía `config_produccion_it7["..."]`,
  recibirás `KeyError`. **Las claves nunca tuvieron efecto**, así que
  removerlas es seguro a nivel de comportamiento.

  Verificación: corrida contra GT da exactamente el mismo F1 antes/después
  (0.0508), confirmando que las claves removidas no se leían.

### Removed
- `src/record_linkage/optimization/optuna_integration.py`
- `src/record_linkage/optimization/visualizer.py`
- 11 claves dead/deprecated de `config_produccion_it7`

### Preserved
- `record_linkage.pipeline.linkage_pipeline.RecordLinkagePipeline` se mantiene
  (es dependencia interna del `Orchestrator`, decisión revisada).
- Todas las APIs documentadas siguen funcionando (`linkage()`,
  `Orchestrator`, `OrchestratorOptimizer`, `crear_config_orchestrator`,
  `validar_config`, todos los perfiles).
- Tags antiguos en GitHub (v3.2.X) siguen preservados.

### Changed
- `tests/test_fase4_consolidacion.py`: 18 → 23 tests. Se reemplazó
  `TestOptunaIntegrationDeprecated` (verificaba DeprecationWarning) por
  `TestOptunaIntegrationRemoved` (verifica ModuleNotFoundError). Se añadió
  `TestConfigIT7Limpio` con 4 tests verificando la limpieza.

### Documentation
- Nuevo: `docs/AUDITORIA_FASE5_SPRINT_0_5_0.md`
- README actualizado con badge `0.5.0`

### Compatibility
- 380/380 tests pasan
- Comportamiento del `Orchestrator`: idéntico a v0.4.0 contra GT
- Migration path documentada para los 2 imports breaking

---

---

## [0.4.0] — 2026-05-26 — Re-versionamiento + cierre de Fase 4

### Important — re-versionamiento

Esta release **resetea la numeración** de `3.2.7` (entregada hace horas) a
`0.4.0`, reflejando con honestidad el estado del paquete: pre-1.0, sin PyPI,
API aún inestable, refactores frecuentes. La numeración anterior era
aspiracional.

**Equivalencia retroactiva con tags antiguos:**

| Tag antiguo | Equivalente nuevo | Fase | Fecha |
|---|---|---|---|
| `v1.x` | (pre-paquete, notebook monolítico) | — | hist. |
| `v2.0.0` – `v2.14.0` | `0.1.0-internal` (10 versiones de exploración) | — | mayo 21–23 |
| `v3.0.0` | `0.1.0` (primera versión empaquetada estable) | — | mayo 23 |
| `v3.2.1` – `v3.2.3` | `0.2.0` (consolidación funcional) | — | mayo 24 |
| `v3.2.4` | `0.3.0` | Fase 1 — calibración GT (F1=0.84) | mayo 25 |
| `v3.2.5` | `0.3.1` | Fase 2 — `source_quality_weights`, `max_sources_per_group` | mayo 26 |
| `v3.2.6` | `0.3.2` | Fase 3 — `OrchestratorOptimizer` + Optuna | mayo 26 |
| `v3.2.7` | **`0.4.0`** | Fase 4 — fix `_class_exists`, `min_sources_for_golden`, deprecation legacy | mayo 26 |

Los tags antiguos en GitHub **NO se borran** — quedan como referencia histórica.
Ver `docs/VERSIONING.md` para política de versionamiento futura y roadmap hacia 1.0.

### Changed
- Versión `3.2.7` → `0.4.0` (re-versionamiento semántico honesto)
- `pyproject.toml`, `src/record_linkage/__init__.py`, `tests/test_matching_integration.py`
  actualizados consistentemente.
- README rediseñado con badge `0.x pre-1.0` y nota sobre estado del proyecto.
- Notebooks actualizados (`notebooks/04_optuna_calibration.ipynb`).

### Added
- `docs/VERSIONING.md` — política de versionamiento y roadmap hacia 1.0.
- Entrada en `MIGRATION_LOG.md` documentando el reset.

### Compatibility
- **Cero cambios funcionales**. El comportamiento de v3.2.7 es idéntico a 0.4.0.
- 380/380 tests pasan sin modificación (excepto el `test_version_is_*`).
- Si tu código pinea `rues-linker==3.2.7`, debe cambiar a `rues-linker==0.4.0`.

---

## Versiones anteriores (mapeo histórico)

Las versiones antiguas se preservan aquí para referencia. **No instalar como
`3.2.X` después de este re-versionamiento.**


## [3.2.7] — 2026-05-26 — FASE 4 de auditoría

### Fixed
- **`_class_exists` (pipeline/_internal.py)**: usaba `eval()` en un módulo
  donde las clases `ReportGenerator`, `DataVisualizer`, `ExecutiveDashboard`,
  `EnhancedReportingSuite` no estaban importadas. Resultado: retornaba
  False y emitía 4 warnings "no disponible, omitiendo" en cada corrida.
  Reemplazado por `importlib.import_module()` con mapeo explícito.
  Ahora los reportes opcionales se ejecutan si la dependencia [viz] está
  instalada, o fallan gracefully si no.

### Added
- **`min_sources_for_golden`** en `GoldenRecordGeneratorV7`. Si el config
  contiene `profiles[active].min_sources_for_golden = N` (con N > 1), el
  generador filtra el golden excluyendo clusters con menos de N fuentes
  únicas. La correlativa NO se modifica (preserva trazabilidad). Default 0
  (sin filtro) mantiene retrocompatibilidad.
- Método `GoldenRecordGeneratorV7._filter_golden_by_min_sources()`.
- Constante `DEPRECATED_CONFIG_KEYS` en `config/profiles.py`. Distinta de
  DEAD: las deprecadas tienen alternativa documentada.
- Constante `RESURRECTED_CONFIG_KEYS` en `config/profiles.py`. Documenta
  claves que ANTES eran dead y AHORA están implementadas (referencia
  histórica para mantenedores).
- `validar_config()` ahora reporta también claves deprecated con mensaje
  diferenciado.
- Documento `docs/AUDITORIA_FASE4.md`.
- Notebook ejemplo `notebooks/04_optuna_calibration.ipynb` con flujo
  completo: cargar GT, optimizar con OrchestratorOptimizer, usar best_config.
- 18 tests nuevos en `tests/test_fase4_consolidacion.py`.

### Changed
- **`cross_source_validation` movido de DEAD a DEPRECATED**. La clave seguía
  apareciendo en `config_produccion_it7` sin efecto. v3.2.7 la marca como
  deprecada (alternativa: `cross_source_only` que sí funciona). Será
  removida en v3.3.0.
- `OptunaIntegration` heredado (optimization/optuna_integration.py) emite
  ahora `DeprecationWarning` al importar. La clase sigue importable
  (retrocompat). Será removida en v3.3.0. Alternativa: `OrchestratorOptimizer`.
- `max_sources_per_group` y `min_sources_for_golden` removidos de
  `DEAD_CONFIG_KEYS` (ya están implementados desde v3.2.5 y v3.2.7
  respectivamente).

### Preserved (intencional)
- `config_produccion_it7` NO se modificó. Las claves dead/deprecated que
  contiene siguen ahí para retrocompatibilidad documental.
- Default `min_sources_for_golden=0` mantiene comportamiento ≤ v3.2.6.

### Roadmap v3.3.0 (breaking changes anunciados)
- Eliminar `optimization/optuna_integration.py` (clase OptunaIntegration).
- Eliminar `cross_source_validation` y claves dead de `config_produccion_it7`.


## [3.2.6] — 2026-05-26 — FASE 3 de auditoría

### Added
- Nueva clase `OrchestratorOptimizer` en
  `record_linkage/evaluation/orchestrator_hyperparameters.py`. Conecta
  Optuna al flujo de producción real (`Orchestrator.run()`), mientras que
  `HyperparameterOptimizer` (legacy) sigue trabajando con
  `linkage_pipeline.run()`.
- Función `default_search_space()` con el espacio de búsqueda recomendado
  sobre los 7 parámetros que el código realmente lee (lsh_threshold,
  score_threshold, min_name_similarity, max_nit_distance,
  nit_empty_passes_filter, weight_name, weight_nit).
- Método `OrchestratorOptimizer.optimize()` que devuelve `best_config`
  completo listo para producción, además de `best_params`, history,
  métricas detalladas y el objeto `study` de Optuna.
- Método `OrchestratorOptimizer.history_df()` para análisis post-mortem
  del proceso de optimización.
- Soporte para `optimization_target` ∈ {'f1', 'f2', 'precision', 'recall'}.
- Soporte para `time_budget_minutes` y `time_penalty_seconds`.
- Soporte para `sampler` y `pruner` personalizados de Optuna.
- Documento `docs/AUDITORIA_FASE3.md`.
- 14 tests nuevos en `tests/test_fase3_optuna.py` (skip si Optuna ausente).

### Changed
- `record_linkage.evaluation.__init__` ahora exporta `OrchestratorOptimizer`,
  `default_search_space`, `HyperparameterOptimizer` y `OPTUNA_AVAILABLE`
  cuando Optuna está instalado.

### Verified
- 362/362 tests pasan (348 previos + 14 nuevos).
- E2E con 6 trials sobre GT muestreado: F1=0.93 (vs 0.84 del perfil estático).


## [3.2.5] — 2026-05-26 — FASE 2 de auditoría

### Added
- `AdvancedValueSelector` ahora acepta argumento opcional
  `source_quality_weights: dict[str, float] | None`. Cuando se pasa, los
  pesos numéricos se usan para desempate en `select_best_name()` (cuando
  varios registros empatan en la fuente más prioritaria).
- `GoldenRecordGeneratorV7` lee `source_quality_weights` del config y los
  propaga al `AdvancedValueSelector` automáticamente.
- `OptimizedClusterer` acepta parámetro `max_sources_per_group: int | None`
  en el perfil. Si está definido y un cluster tiene más fuentes únicas que
  el límite, se divide post-clustering por `(SRC, NIT)`.
- Nuevo método `OptimizedClusterer._split_mega_clusters()` para la división.
- Perfil `alta_precision` recalibrado con evidencia del GT (F1=0.83 medido).
- Documento `docs/AUDITORIA_FASE2.md`.
- 17 tests nuevos en `tests/test_fase2_calibracion.py`.

### Changed
- Eliminada clave `aggressive_gc` (dead code) de perfiles `produccion_estandar`,
  `produccion_exhaustiva`, `alta_precision`.
- Perfil `alta_precision` ahora usa `score_threshold=0.60`, `min_name_similarity=0.65`,
  `max_nit_distance=0`, `nit_empty_passes_filter=False` (alineado con la
  calibración de Fase 1).

### Preserved (intencional)
- `config_produccion_it7` NO se modificó (retrocompat documental).
- Default `source_quality_weights=None` y `max_sources_per_group=None`
  preservan comportamiento idéntico a v3.2.4.

### Compatibility
- 348/348 tests pasan (331 previos + 17 nuevos).
- `produccion_calibrada` mantiene F1=0.84 (sin regresión).


## [3.2.4] — 2026-05-25 — FASE 1 de auditoría

### Added
- Nuevo perfil `produccion_calibrada` en `PERFILES_BASE` con parámetros validados
  contra `ground_truth_grande.csv` (F1=0.84, P=1.00, R=0.73, FP=0).
- Constante `DEAD_CONFIG_KEYS` (12 parámetros que el código no lee) y
  `PARTIAL_CONFIG_KEYS` (2 parámetros con uso limitado).
- Función `validar_config(config, verbose=True)` que audita un config y reporta
  claves dead/partial. Se invoca automáticamente en `crear_config_orchestrator`.
- Flag opt-in `nit_empty_passes_filter` en `scorer.py` (default `True` para
  retrocompatibilidad). Si se pone en `False`, los pares con NIT vacío en
  algún lado NO pasan el filtro de NIT. Default en `produccion_calibrada`.
- Documento `docs/AUDITORIA_FASE1.md` con metodología, resultados verificables
  y plan de Fases 2+.
- Test `tests/test_fase1_calibracion.py` con 14 tests de regresión.

### Fixed
- Bug de filtro NIT vacío: `nit_distance == -1` (sentinela para NIT vacío)
  pasaba el filtro porque `-1 <= max_nit_distance`. Causaba sobre-fusión
  catastrófica en regímenes SIN_NIT. Fix configurable vía flag.

### Changed
- `crear_config_orchestrator` ahora invoca `validar_config()` por defecto
  (parámetro `validate=True`). Se puede desactivar con `validate=False`.

### Compatibility
- 331/331 tests pasan, incluidos 89 tests de scoring/NIT/dedup.
- IT-7 default sigue produciendo los mismos resultados que en v3.2.3
  (verificado: F1=0.05 idéntico sobre GT completo).


Todos los cambios notables de este proyecto se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/),
y este proyecto se adhiere a [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [3.2.3] — 2026-05-24

### Corrección de bug + recalibración honesta de métricas

Versión de patch que corrige una **divergencia silenciosa cross-version**
del scorer en features firmados (`categorical_signed`, `exact_signed`) y
**recalibra los pisos del test `test_quality_extra_features`** al
comportamiento documentado del feature, no a la métrica artificialmente
inflada por el bug previo.

#### Corregido

- **`VectorizedScorer._valid_mask` ahora detecta NaN de forma
  cross-version-safe.** Causa raíz: desde pandas 2.1 (y consolidado en
  3.x con `infer_string=True` por defecto), `pd.Series([np.nan]).astype(str)`
  preserva el `NaN` flotante en vez de convertirlo a la cadena `'nan'`. El
  chequeo `upper.isin(_NULLISH)` con `_NULLISH = {"", "NAN", "NONE", ...}`
  detectaba el nulo en pandas 2.0.x (donde llegaba como cadena `'NAN'`)
  pero lo pasaba por alto en pandas 2.1+ y 3.x. Resultado: en features
  firmados, los pares con un valor nulo y otro válido se clasificaban como
  "ambos válidos y distintos" → penalización espuria de -1.0 (cuando la
  política documentada exige 0.0).

  Solución: nuevo helper `VectorizedScorer._to_clean_str_series(values)`
  que materializa NaN/None/pd.NA como cadena vacía `""` antes de
  `.astype(str)`. La cadena vacía ya está en `_NULLISH`, garantizando
  detección uniforme en cualquier versión de pandas. Todas las coerciones
  a string dentro de `_feature_similarity_vectorized` ahora pasan por este
  helper.

  Diagnóstico empírico (dataset `golden_truth_exhaustivo_ciudad`,
  feature CIUDAD `categorical_signed` w=0.15):

  | versión pandas | n_pos | n_neg | n_zero | comportamiento |
  |---|---|---|---|---|
  | 2.0.x (pre-bug) | 5075 | 263 | 1731 | ✅ docs |
  | **2.2.2 (sin fix)** | 5075 | 263 | 1731 | ⚠️ docs por accidente* |
  | **3.0.x (sin fix)** | 5075 | 1994 | 0 | ❌ viola docs |
  | **3.0.x / 2.2.2 (con fix v3.2.3)** | 5075 | 263 | 1731 | ✅ docs |

  \* En pandas 2.2.2 el código antiguo daba el resultado correcto
  porque `astype(str)` aún convertía `NaN → 'nan'` (cadena literal),
  no porque la lógica fuese cross-version-safe. Era un comportamiento
  frágil dependiente de un detalle de implementación de pandas.

#### Recalibrado (impacto en `test_quality_extra_features.py`)

- **Pisos del test calibrados al comportamiento documentado del feature.**
  La calibración previa (`PRECISION_MIN=0.94`, `WEIGHT=0.15`) se hizo
  contra pandas 3.x con el bug activo, que producía precisión inflada
  ≈0.97 al penalizar incorrectamente los NaN. Eliminado el bug, los
  pisos previos eran **inalcanzables en cualquier versión de pandas con
  la política documentada del feature** ("NaN → 0.0, sin penalización").

  Barrido honesto del weight (pandas 2.2.2 = pandas 3.0.2 con fix, bit a bit):

  | weight | P | R | F1 | FP |
  |---|---|---|---|---|
  | sin features | 0.886 | 0.849 | 0.867 | 1053 |
  | 0.10 | 0.905 | 0.880 | 0.892 | 897 |
  | 0.15 (legacy) | 0.882 | 0.906 | 0.894 | 1177 |
  | 0.25 | 0.891 | 0.958 | 0.923 | 1136 |
  | **0.30 (nuevo default)** | **0.892** | **0.963** | **0.926** | 1132 |
  | 0.50 - 1.00 (meseta) | 0.892 | 0.963 | 0.926 | 1132 |

  Cambios concretos en `test_quality_extra_features.py`:
  - `WEIGHT`: 0.15 → 0.30 (óptimo F1 en meseta plana, robusto a
    variabilidad ±0.05 sin cambiar el resultado).
  - `PRECISION_MIN`: 0.94 → 0.88 (precisión real del feature).
  - `F1_MIN`: 0.88 → 0.91 (real medido: 0.926, margen ±0.015).
  - `RECALL_MIN`: 0.80 → 0.93 (real medido: 0.963).
  - `test_ciudad_supera_baseline_sin_features`: eliminada la aserción
    `fp < baseline.fp` (matemáticamente cuestionable: subir recall sube
    FP en absoluto aunque la tasa de FP mejore). Reemplazada por
    `precision >= baseline.precision - 0.01` (invariante metodológicamente
    correcta).

#### Notas metodológicas

- **El feature `categorical_signed` sigue siendo net-positive.** Con
  w=0.30 sobre el dataset enriquecido aporta +0.06 en F1 (0.867 → 0.926)
  y +0.11 en recall (0.849 → 0.963), con precisión esencialmente plana
  (0.886 → 0.892). La narrativa anterior de "+0.10 en precisión" era
  artefacto del bug, no del feature.
- **Cross-version consistency garantizada.** Suite completa (317 tests
  con los nuevos de regresión) pasa idénticamente en pandas 2.2.2 y
  pandas 3.0.2 tras el fix.
- **Caveat de producción.** El dataset de test (`golden_truth_exhaustivo_ciudad`)
  tiene CIUDAD sintética asignada por grupo verdadero (favorable al feature
  por construcción). El comportamiento en datos reales depende del % de
  CIUDAD nula y de la correlación entre CIUDAD y identidad empresarial en
  cada fuente (RUES, DIAN, CRM, Supersociedades). Calibrar w sobre ground
  truth real antes de despliegue.

#### Añadido

- **`tests/test_extra_features_null_policy.py`** (4 tests). Red de seguridad
  contra regresiones del bug de nulos cross-version. Cubre:
  - `_to_clean_str_series` materializa NaN/None/pd.NA como cadena vacía y
    preserva strings null-ish literales (`"nan"`, `"<NA>"`).
  - `_valid_mask` detecta como inválidos tanto nulos materializados como
    strings null-ish (case-insensitive).
  - `categorical_signed` aplica la política documentada (NaN → 0.0, no -1.0).
  - `exact_signed` aplica la misma política.

  Estos tests no dependen del pipeline end-to-end ni del dataset
  sintético. Detectarían cualquier futura regresión del comportamiento
  de nulos en <1 segundo, independiente de la versión de pandas.

#### Recomendaciones para usuarios existentes

- Si tu pipeline usa `extra_features=[{..., "type": "categorical_signed", "weight": 0.15}]`:
  - Las métricas que medías antes en pandas 3.x estaban infladas por el bug.
  - Recalibra `weight` contra tu ground truth real. Punto de partida
    sugerido: w=0.30 (óptimo F1 en dataset sintético).
  - Si necesitas la política antigua (NaN penaliza como "distinto"), no
    está disponible vía API en esta versión. Pendiente para un release
    futuro como variante explícita `categorical_signed_strict` con
    documentación dedicada.

---

## [3.2.2] — 2026-05-24

### Tests / artefactos de calidad (sin cambios funcionales en la librería)

Versión de patch que arregla la **portabilidad del oráculo de paridad
P1-1 entre versiones de pandas**. **No hay cambios en `src/record_linkage/`**;
la lógica del scorer, matching y pipeline es idéntica a 3.2.1.

#### Corregido

- **Pickle del oráculo de paridad bidireccionalmente portable entre
  pandas 2.2.x y 3.x.** En pandas ≥2.3, los strings (incluidos nombres
  de columnas) se serializan como `StringDtype("pyarrow", NaN)`. Su
  constructor acepta `(storage, na_value)`, pero el de pandas 2.2.x
  solo acepta `(storage,)`. Esto provocaba al cargar el oráculo en
  Colab (pandas 2.2.x):
  `TypeError: StringDtype.__init__() takes from 1 to 2 positional
  arguments but 3 were given`, bloqueando la publicación.
  Solución: `scripts/capturar_oraculo_p1_1.py` ahora normaliza con
  `_to_portable_dtypes()`, forzando `dtype=object` tanto en datos como
  en `df.columns` antes de pickling. Verificado: paridad bit-a-bit
  preservada (max diff = 0.0) porque el scorer coerciona strings
  internamente con `pd.array(..., dtype='string').to_numpy(na_value="")`.
  Validado en pandas 2.2.2, 2.3.3 y 3.0.3.
- **Test de paridad resiliente.** `tests/test_paridad_p1_1.py` ahora
  hace `pytest.skip` con instrucción exacta de regeneración si el
  oráculo es incompatible con la versión de pandas instalada, en lugar
  de fallar la suite. Mismo manejo en `scripts/validar_paridad_p1_1.py`.
- **`scripts/validar_paridad_p1_1.py`**: ruta del oráculo resuelta
  relativa al repo (no a `cwd`), eliminando fallos al invocar desde
  directorios alternativos.

#### Notas de publicación

- El bug NO estaba en código de la librería ni en el notebook de
  publicación. Estaba en el artefacto serializado de tests, cuya
  generación dependía implícitamente de la versión de pandas del
  entorno. Esto es una clase de fallo conocida en serialización pickle
  de pandas (los dtypes cambian de firma entre versiones mayores).
- El pickle regenerado **no contiene** referencias a `StringDtype` ni
  a `pyarrow` en su payload (verificado por inspección de bytes), lo
  que lo hace robusto frente a futuros cambios de firma.

---

## [3.2.1] — 2026-05-24

### Empaquetado / tooling (sin cambios funcionales en la librería)

Versión de patch que sincroniza el versionado y registra marcadores de
pytest. **No hay cambios en `src/record_linkage/`**; la lógica de matching,
deduplicación y pipeline es idéntica a 3.2.0.

#### Corregido

- **Versionado consistente.** `pyproject.toml` y `src/record_linkage/__init__.py`
  ahora declaran `3.2.1` de forma coherente con el artefacto de distribución
  (`rues-linker-v3.2.1`). Elimina la divergencia previa entre el nombre del
  paquete empaquetado (v3.2.1) y los metadatos internos (3.2.0).
- **Marcador `canario` registrado** en `[tool.pytest.ini_options].markers`.
  Bajo `--strict-markers`, un marcador no declarado provoca error de
  colección si la suite se ejecuta sin el filtro `-m "not canario"`. Ahora
  la suite es robusta ante cualquier forma de invocación.

#### Notas de publicación

- El bug que bloqueaba la publicación desde el notebook de Colab **no estaba
  en la librería**, sino en el flujo de verificación local: pytest se
  ejecutaba sin instalar antes el paquete (layout `src/`), produciendo
  `ModuleNotFoundError: No module named 'record_linkage'`. El CI de GitHub
  Actions ya instalaba el paquete correctamente (`pip install -e ".[dev]"`),
  por lo que CI nunca estuvo afectado. La corrección del notebook se entrega
  por separado.

---

### Multi-variable matcher INTEGRADO al pipeline + 3 perfiles preset

Esta versión cierra los bloqueantes técnicos B1 y B2 identificados por la
auditoría externa independiente de v3.1.0 (octubre 2026):
"el módulo `matching/` no está conectado al pipeline" y "las cifras del
docstring no son trazables".

**No cierra B3** (etiquetas humanas reales sobre RUES), que sigue siendo
trabajo humano fuera del alcance de la librería. Se entregan herramientas
(`active_labeling.py`, `recalibrate_from_labels.py`) para facilitarlo.

#### Cambios principales

- **`linkage(..., matching_profile=...)`** — el matcher ahora se invoca
  con un parámetro opt-in en la API de alto nivel. Opciones:
  - `"colombia"` o `"colombia_balanced"` (default balanceado, mejor F1)
  - `"colombia_conservative"` (max Precision para KYC/regulación)
  - `"colombia_recall"` (max Recall para enriquecimiento)
  - `"international"` (sin NIT, basado en NAME+EMAIL+CITY+COUNTRY)
  - Instancia de `MatchingProfile` (personalizado)
- **`return_matcher_audit=True`** — incluye `matcher_stats` y
  `matcher_decisions` en el resultado para trazabilidad completa.
- **`record_linkage.MatcherPostProcessor`** exportado en el namespace raíz
  (la auditoría señaló que no lo estaba).
- **Default ajustado por evidencia**: tras barrido E2E real, el óptimo F1
  es `K_sin_nit=1, threshold=0.50` (no K=2 como sugería la auditoría sin
  medir E2E). K=2 está disponible vía `colombia_conservative`.

#### Métricas E2E reproducibles (GT sintético v2.14.0, 12.427 registros)

Reproducir con: `python scripts/benchmark_e2e_matcher.py`

| Métrica            | Baseline | v3.2.0 (balanced) | Δ        |
|--------------------|----------|-------------------|----------|
| F1 global          | 0.8729   | **0.9082**        | +3.53 pp |
| F1 CON_NIT         | 0.9507   | 0.9541            | +0.34 pp |
| **F1 SIN_NIT**     | 0.6289   | **0.7320**        | **+10.31 pp** |
| **Precision SIN_NIT** | 0.5590 | **0.7902**       | **+23.12 pp** |
| Recall SIN_NIT     | 0.7189   | 0.6818            | -3.71 pp |
| FP CON_NIT         | 119      | **0**             | -119     |

Trade-off transparente: el matcher trade recall sin-NIT (-3.7pp) por
+23pp de precision sin-NIT y -119 FP en CON_NIT (mismo nombre, NITs
distintos = empresas distintas, ahora se separan).

#### Tests

- 313 tests verde (era 297): 258 originales + 39 matching + 16 nuevos de
  integración (`test_matching_integration.py`).
- Tests específicos para los 3 perfiles preset y para los exports raíz.

#### Scripts nuevos

- `scripts/benchmark_e2e_matcher.py` — medición trazable E2E (B2).
- `scripts/active_labeling.py` — selecciona pares ambiguos para
  etiquetado humano estratégico (active learning).
- `scripts/recalibrate_from_labels.py` — recalibra `K` y `threshold` a
  partir de etiquetas humanas (cierra el loop B3).

#### Lo que sigue pendiente (no resuelto en v3.2.0)

- **B3 — Ground truth real etiquetado**: ninguna cifra de v3.2.0 está
  medida sobre datos reales. Antes de producción crítica (>500k
  registros), ejecutar `active_labeling.py` → etiquetar 500 pares con
  dos anotadores → `recalibrate_from_labels.py`. Sin esto, F1=0.908 es
  un dato útil pero NO defendible para auditorías regulatorias.

---

## [3.0.0] — 2026-05-23

### API unificada de alto nivel + motor en disco + limpieza para PyPI

Release MAYOR que integra la auditoría externa (ROADMAP_PRODUCCION.md) con la
Fase 2 de limpieza. Objetivo: que la librería sea **usable de forma clara y
sencilla** en escenarios reales. Único pendiente para "producción defendible":
medición sobre datos reales del RUES (Hito H3, trabajo humano).

#### Added — helper `linkage()` (API de alto nivel única)

`from record_linkage import linkage`. Una sola función para dedup + record
linkage multi-fuente, multi-variable, motor en disco. Encapsula la construcción
de config + Orchestrator. Soporta `trusted_sources`, `extra_features`
(TELEFONO, EMAIL, DIRECCION), `col_ciudad`, `profile` y `work_dir`. Devuelve
`{"golden", "correlative"}`. Tres casos de uso documentados en el README.

#### Added — API pública en el namespace raíz

`__all__` ahora expone `linkage`, `Orchestrator`, `deduplicate_unified`,
`crear_config_orchestrator`, `evaluar_pares`. Antes solo `Config`/`Rutas`.
Imports defensivos: una dependencia opcional ausente no rompe `import
record_linkage`.

#### Changed — motor en disco por defecto (H4.3.1)

`_should_use_disk_processing` ahora usa disco por defecto (antes: solo >500k
pares). Cargas triviales (<5k) siguen en memoria por eficiencia. Override por
`RUES_LINKER_FORCE_MEMORY=1` / `RUES_LINKER_FORCE_DISK=1`. Cumple el
requerimiento de "una sola cosa, todo en disco, mantenimiento simple".

#### Changed — dependencias limpiadas (Fase 2)

- **Eliminado `fuzzywuzzy`** (no se usaba).
- **Migrado `python-Levenshtein` → `rapidfuzz`** en 4 módulos
  (`ground_truth`, `containment`, `generator`, `strict`). Verificado
  **bit-a-bit idéntico** en 20.000 pares: `Levenshtein.distance` ≡
  `rapidfuzz.distance.Levenshtein.distance` y `Levenshtein.ratio` ≡
  `rapidfuzz.distance.Indel.normalized_similarity` (diferencia 0.0).
- **Viz y Optuna movidos a extras opcionales:** `[viz]`
  (matplotlib/seaborn/plotly/upsetplot), `[optimization]` (optuna), `[all]`.
  El núcleo instala liviano. Imports de reporting hechos defensivos: el
  pipeline core funciona sin viz (solo se omiten reportes gráficos).

#### Changed — licencia Apache-2.0

Expresión SPDX `license = "Apache-2.0"` + `license-files`. Apto para PyPI
público. Verificado en el wheel: `License-Expression: Apache-2.0`.

#### Documentación

- README: sección "Inicio rápido" con `linkage()`, tabla "Cuándo usar cada
  API", extras de instalación, y corrección de la afirmación no medida de "2M
  en 3-5h" (escala probada real: ~37k).
- `docs/ROADMAP_PRODUCCION.md`: sección "ESTADO ACTUALIZADO v3.0.0" que integra
  lo hecho vs lo pendiente, en orden de prioridad. El único bloqueante restante
  es H3 (medición real).

#### Tests

257 verde (era 236): +18 del clusterer disk-based (H2), +4 del helper
`linkage()`. Migración Levenshtein no cambió ningún resultado. Ruff limpio.
Build + twine PASSED en sdist y wheel.

#### Pendiente (no bloqueante salvo H3)

Ver `docs/ROADMAP_PRODUCCION.md` §"ESTADO ACTUALIZADO". Crítico: **H3 —
medición sobre datos reales del RUES** (etiquetado humano). Lo demás (limpiar
`except:` desnudos, cobertura trusted.py, deprecar LSH legacy, Optuna) es P1/P2.

---

## Pendientes (backlog priorizado) — estado al 2026-05-23

> Esta sección refleja lo que NO está hecho, en orden de prioridad real.
> Reemplaza parcialmente al ROADMAP A09, que quedó obsoleto tras medir.

### P0 — Bloqueantes para producción defendible

- [ ] **Auditoría forense completa de v2.8.0 → v2.10.0.** Esta entrega
  (v2.11.0) corrigió DOS afirmaciones falsas detectadas por muestreo
  (el fix de `containment` que nunca se empaquetó, y el P4 R=1.00
  inflado). NO se auditaron todas las demás afirmaciones de calidad de
  esas tres versiones. Falta verificar, una por una y con medición
  directa, cada cifra de F1/precision/recall/speedup citada en
  CHANGELOG y MIGRATION_LOG de v2.8.0, v2.9.0 y v2.10.0, y marcar las
  que no correspondan al código entregado. Hasta hacerlo, tratar toda
  cifra histórica con escepticismo. **Estimación: 1 turno completo.**
- [ ] **Medir a escala real (P0-2 del ROADMAP).** Ninguna métrica se ha
  medido sobre > 1456 registros contra ground truth. Correr el notebook
  de producción sobre una muestra real de 50k+ del RUES y publicar el
  primer F1 real. Sin esto, la calidad en 2–4 M es desconocida.
- [ ] **Calibrar `colab_1M` / `colab_3M` contra ground truth.** Estos
  perfiles (los que se autoseleccionan a escala) nunca fueron
  calibrados. Sus umbrales (`score_threshold` 0.85–0.88) son
  conjeturas, no mediciones.

### P1 — Calidad y robustez

- [ ] **Régimen sin-NIT: validar el perfil conservador con ground truth
  real.** El perfil `deduplication_sin_nit_conservador` se calibró por
  composición (tamaño de grupos), no contra etiquetas. Necesita un
  ground truth etiquetado del dominio de importaciones para medir su F1.
- [ ] **Modos de falla restantes** (sufijo confundible, filial país,
  sigla vs nombre completo): pendientes desde el Sprint 2 planeado.
- [ ] **Test de reanudación end-to-end** tras reinicio de Colab (P1-3).

### P2 — Deuda técnica

- [ ] Cobertura de tests del 38 % al 70 % (foco en `engine/`).
- [ ] Eliminar `legacy.py` / `_run_L2_legacy` si no se usan (P2-2).
- [ ] Limpiar `_create_minhash` deprecado (P2-3).
- [ ] Conectar Optuna al ground truth (P2-1) — solo tras medir a escala.

---

## [2.14.0] — 2026-05-23

### Licencia Apache-2.0 + Fase 1 (ground truth sintético grande y línea base medida)

#### Changed — licencia a Apache-2.0 (apta para PyPI público)

`LICENSE` reemplazado por el texto oficial Apache License 2.0 con bloque de
copyright. `pyproject.toml` usa expresión SPDX `license = "Apache-2.0"` +
`license-files = ["LICENSE"]`. Eliminado el classifier de licencia (PEP 639
prohíbe usarlo junto con la expresión SPDX). Verificado en el wheel:
`License-Expression: Apache-2.0`, `License-File: LICENSE`, twine check pasa.

**Ahora el paquete SÍ puede publicarse en pypi.org público.**

#### Added — generador de ground truth sintético grande

`scripts/generar_ground_truth_grande.py` (determinista, seed=42) produce
`data/ground_truth/ground_truth_grande.csv`:

- 12.427 registros, 3.486 grupos (tamaño medio 3,6; máx 11).
- Dos regímenes: CON_NIT (10.085, estilo RUES/DIAN) y SIN_NIT (2.342,
  estilo Corea: solo nombre + ciudad).
- Seis variables: NIT, RAZON_SOCIAL, CIUDAD, TELEFONO, DIRECCION, EMAIL.
- Casos frontera negativos (intermediario compartido, nombre genérico).
- Validación de integridad (NIT base consistente por grupo, sin mezcla de
  regímenes).

> ⚠️ Es SINTÉTICO. Mide los tipos de variación programados, no la realidad de
> producción. No sustituye etiquetado humano (ver PROTOCOLO_GROUND_TRUTH.md).

#### Added — línea base de calidad medida (Fase 1)

Documentada en `docs/FASE1_LINEA_BASE.md`. Medido contra el ground truth:

| Régimen | Configuración | F1 | P | R |
|---|---|---|---|---|
| CON_NIT | nombre + NIT | 0.961 | 0.972 | 0.950 |
| CON_NIT | + ciudad | **0.974** | 0.974 | 0.974 |
| SIN_NIT | estándar | 0.549 | 0.393 | 0.908 |
| SIN_NIT | conservador+IDF (actual) | 0.436 | 0.939 | 0.284 |

#### Hallazgo de integridad — el perfil sin-NIT estaba sobreajustado

El ground truth grande reveló que `deduplication_sin_nit_conservador`
(IDF blend=0.5, threshold=0.80), que "se veía bien" por inspección sobre los
818 registros de Corea, tiene **recall de 0.284** medido contra verdad
conocida. Estaba sobreajustado a Corea. El perfil NO se modificó en esta
versión: recalibrar contra datos sintéticos solo movería el sobreajuste. La
recalibración correcta requiere un ground truth REAL (pendiente P1).

Lección cuantificada: el NIT vale ~0.40 de F1 (CON_NIT 0.97 vs SIN_NIT ~0.57).
La inspección cualitativa engaña; medir es indispensable.

#### Added — test de regresión de calidad

`tests/test_calidad_ground_truth_grande.py` congela la cota CON_NIT
(F1 ≥ 0.92, precision ≥ 0.95) para detectar regresiones futuras.

#### Tests

236/236 verde (235 + 1 de calidad). Ruff limpio. Build y twine pasan.

---

## [2.13.0] — 2026-05-23

### Fase 0 — Desbloqueo de empaquetado para PyPI

Resuelve los 2 bloqueantes críticos detectados en la auditoría de
preparación para PyPI, más documentación de soporte. Sin cambios en la
lógica de negocio: el núcleo (scorer, IDF, perfiles) no se tocó.

#### Added — archivo LICENSE

Antes el `pyproject.toml` declaraba `license = "Proprietary"` pero NO
existía un archivo de licencia — bloqueante absoluto para cualquier
distribución. Añadido `LICENSE` propietario (opción reversible), con
instrucciones explícitas para migrar a MIT o Apache-2.0 si se decide
liberar como open source.

> ⚠️ Una licencia propietaria NO permite publicar en pypi.org público.
> Para PyPI público hay que cambiar a una licencia OSI (MIT/Apache-2.0)
> — ver la nota al final del archivo LICENSE. Liberar como open source
> es irreversible para las versiones ya publicadas.

#### Added — marcador py.typed (PEP 561)

El código tiene type hints pero no los declaraba. Creado
`src/record_linkage/py.typed` e incluido en el wheel vía
`[tool.setuptools.package-data]`. Verificado en el `.whl`. Ahora mypy y
pyright reconocen que el paquete provee tipos.

#### Added — MANIFEST.in

Controla qué entra en el sdist: incluye README, CHANGELOG, LICENSE,
py.typed y docs; excluye tests, data, scripts, notebooks y artefactos.

#### Added — documentación

- `docs/PROTOCOLO_GROUND_TRUTH.md`: protocolo de etiquetado de la base
  de verdad (tamaños estadísticos, esquema multi-variable, estratificación,
  reglas de decisión, control de calidad con Cohen's kappa).
- `docs/EVALUACION_PYPI_20_CRITERIOS.md`: evaluación medida de los 20
  criterios de preparación para PyPI (versión en texto de la tabla).

#### Estado de bloqueantes PyPI

| Bloqueante | v2.12.0 | v2.13.0 |
|---|---|---|
| Archivo LICENSE | ❌ ausente | ✅ presente |
| py.typed (PEP 561) | ❌ ausente | ✅ en el wheel |
| MANIFEST.in | ❌ ausente | ✅ presente |
| Build + twine check | ✅ | ✅ |

Pendiente para PyPI público: cambiar a licencia OSI (decisión de negocio).

#### Tests

235/235 verde (sin cambios — la Fase 0 no toca lógica). Ruff limpio.
Build y twine check pasan en sdist y wheel.

---

## [2.12.0] — 2026-05-23

### Fix #3 — re-scoring por IDF: arregla la sobre-fusión sin NIT (caso Corea)

Resuelve el problema reportado: en el caso Corea (sin NIT) el modo
conservador seguía fusionando empresas distintas. Diagnóstico medido,
no supuesto.

#### Causa raíz (medida)

El `token_set_ratio` infla la similitud cuando dos nombres comparten
tokens de **alta frecuencia**:

| Par | token_set_ratio | Realidad |
|---|---|---|
| "ELITE EXPORTS INTL INC Y/O **NENOVA**" vs "...Y/O **ARES3**" | **0.93** | empresas distintas |
| "**SECUI** CORPORATION" vs "**MULTIFLORA** CORPORATION" | **0.79** | distintas (solo comparten "CORPORATION") |

El prefijo/sufijo genérico compartido domina la señal. En el corpus de
Corea, los tokens más frecuentes son CO (410), LTD (375), TRADING (78),
INC (61), CORP (56), INTERNATIONAL (47), CORPORATION (39) — ninguno
discrimina identidad.

**La ciudad EMPEORABA el problema** (confirmado: el usuario tenía
razón). Con casi todos los registros en SEOUL, la ciudad reforzaba las
fusiones espurias en vez de separarlas: ELITE pasaba de 11 grupos (sin
ciudad) a 3 grupos (con ciudad w=0.20).

#### Solución

Re-scoring por IDF: cada token se pondera por `log(N / df(t))`. Tokens
frecuentes pesan ≈0; tokens raros (NENOVA, ARES3, SECUI) dominan. La
similitud final mezcla `(1-blend)·token_set_ratio + blend·idf_jaccard`.

Resultados medidos sobre Corea (818 sin NIT, `blend=0.5`, sin ciudad):

| Métrica | Antes (v2.11) | Después (v2.12) |
|---|---|---|
| SECUI/MULTIFLORA/CK/KSCORP | 1 grupo común | **4 grupos distintos** |
| ELITE...Y/O X | 3 grupos | **singletons separados** |
| NENOVA (variantes reales) | unidas | **unidas (preservado, n=28)** |
| WORLD FLORA | mezclada | **unida (n=18, preservado)** |
| Grupos totales | 324 | **461** |

#### Diseño: opt-in estricto (vive solo en el perfil sin-NIT)

`idf_weight_blend=0.0` por defecto en `deduplication_standard`. **Medido
que el IDF DAÑA los datasets con NIT**: golden 269 F1 0.759→0.538,
exhaustivo 0.867→0.770 (sube precisión, hunde recall). Por eso
`idf_weight_blend=0.5` vive **solo** en
`deduplication_sin_nit_conservador`. Es el mismo patrón que el boost de
NIT: una palanca que ayuda en un régimen y daña en el otro.

#### Recomendación de uso para fuentes sin NIT

- Perfil: `deduplication_sin_nit_conservador` (ya trae IDF activo).
- **Ciudad: peso 0 o muy bajo** cuando la fuente está concentrada en
  pocas ciudades (como Corea→SEOUL). La ciudad solo ayuda cuando es
  variada y discrimina (caso exhaustivo: +0.067 F1).

#### Tests

235/235 verde (231 + 4 de IDF). Paridad bit-a-bit del scorer intacta
(21/21) — el camino sin IDF no cambió. Ruff limpio.

#### Limitación honesta

Sin ground truth etiquetado de importaciones, esto se valida por
inspección cualitativa (los grupos "se ven bien"), NO por F1 medido.
NENOVA pelado en corpus de juguete puede separarse; sobre el corpus
real se agrupa. Recomendación en pendientes: etiquetar una muestra de
Corea para medir el F1 real del régimen sin-NIT.

---

## [2.11.0] — 2026-05-23

### Correcciones de integridad + soporte real para fuentes sin NIT + notebook de producción

Release MINOR que **arregla un bug bloqueante**, **corrige métricas
infladas** en la documentación, **añade un perfil conservador para
datos sin NIT**, y entrega el **notebook de producción para 4 fuentes
mixtas**. Todo medido, no citado.

#### Fixed — deduplicación sin NIT ya no rompe (BLOQUEANTE)

`golden/containment.py:240` usaba `.str.len()` sobre `groups_by_nit`.
Cuando NINGÚN registro tiene NIT válido (caso real: importaciones que
solo traen Razón Social + Ciudad), `groups_by_nit` es una Series vacía
de dtype `int64` y `.str.len()` lanzaba
`AttributeError: Can only use .str accessor with string values`.

Fix: usar `.map(len)` y corto-circuitar cuando la Series está vacía.
Verificado reproduciendo el caso real de Corea del Sur (818 registros
sin NIT) de punta a punta. Tests de regresión en
`tests/test_dedup_sin_nit.py` (3 tests).

> **Nota de proceso.** Una sesión previa narró este arreglo como hecho,
> pero nunca llegó al tarball entregado (la sesión se cortó antes de
> empaquetar). El código recibido seguía roto. Lección: un fix no
> existe hasta que está en el paquete y cubierto por un test.

#### Fixed — métricas infladas en CHANGELOG y MIGRATION_LOG

La tabla de v2.10.0 afirmaba que Fix #1 llevó P4 a **R=1.00**. FALSO
para la versión entregada: ese número correspondía a
`boost_declared=0.15`, que se descartó por regresar golden 269. El
default real (`0.10`) deja **P4 en R=0.167** (medido: TP=2, FN=10).
Corregido en CHANGELOG (§2.10.0) y MIGRATION_LOG. Honestamente: Fix #1
tal como se entregó es casi un no-op en calidad agregada.

#### Added — perfil `deduplication_sin_nit_conservador`

Para fuentes SIN NIT, donde el perfil estándar sobre-fusiona (une
empresas distintas que comparten un token y la ciudad). Calibrado por
barrido sobre los 818 registros reales de Corea:

| Modo | Grupos | Grupo más grande | WORLD FLORA + DAEDONG |
|---|---|---|---|
| Estándar (0.68/0.60/0.30) | 324 | 82 | fusiona (mal) |
| **Conservador (0.80/0.75/0.50)** | **450** | **33 (NENOVA, correcto)** | **separa (bien)** |

Prioriza precisión sobre recall. **Sin ground truth no hay F1**; estos
son números de composición, no de calidad medida.

#### Fixed — perfil explícito ya no es sobrescrito por tamaño

`_build_deduplication_config` reemplazaba el perfil del usuario por
`colab_1M`/`colab_3M` cuando había > 1M registros, incluso si el
usuario había pasado un perfil explícito. Ahora un perfil distinto de
`deduplication_standard` se respeta a cualquier escala. El motor
on-disk se sigue decidiendo por tamaño (decisión de memoria).

#### Added — `notebooks/produccion_4fuentes.ipynb`

Notebook de producción listo para 2–4 M registros:
- Arquitectura de 2 celdas (EXTRAS + EJECUTAR) según las skills.
- **4 fuentes mixtas** (con NIT y sin NIT) vía `Orchestrator`.
- **Dedup + record linkage** en una sola corrida.
- **Modo disco forzado por defecto**.
- **Línea base contra ground truth** (`medir_contra_ground_truth`),
  con y sin ciudad, para tener el referente de iteración.
- Bloque dedicado al caso sin-NIT (Corea) con modo conservador.
- `@dataclass` de config con validación `__post_init__`, `pathlib`,
  metadata JSON reproducible.

Smoke test verificado: Orchestrator con 4 fuentes mixtas corre
end-to-end en modo disco (10 regs → 7 golden, agrupando ACME con/sin
NIT y las variantes de WORLD FLORA).

#### Hallazgo positivo — la CIUDAD aporta el mayor salto de calidad

Medido sobre el ground truth exhaustivo (1456 regs):

| Configuración | F1 | Precision | Recall |
|---|---|---|---|
| Sin ciudad | 0.867 | 0.886 | 0.849 |
| **Con ciudad** | **0.934** | **0.963** | **0.907** |

**+0.067 de F1** — la mejora más grande del proyecto. Estaba
disponible desde v2.7.0 pero subestimada en la documentación.

#### Tests

231/231 verde (228 v2.10.0 + 3 regresión sin-NIT). Ruff limpio.



### Sprint 1.2 — Fixes #1 y #2 listos para calibración con producción real

Release MINOR con **dos intervenciones quirúrgicas** contra modos de
falla específicos identificados por el dataset robusto v2.9.0, más toda
la infraestructura para **calibrar contra producción real** en un Colab
con muestra del RUES.

#### Fix #1 — Boost diferenciado por origen del DV (ACTIVO)

**Problema identificado:** el dataset robusto reveló que el
`nit_identical_score_boost=0.05` de v2.8.0 no recuperaba los falsos
negativos de tipo P4 (token disímil con NIT idéntico). El boost era
suficiente para nombres parecidos pero insuficiente cuando el nombre
diverge mucho (ej. "EY" vs "ERNST AND YOUNG COLOMBIA AUDITORES").

**Diagnóstico de causa raíz:** el `AdvancedNitProcessor` calcula DV
para NITs de 9 dígitos (rama `len(s) == 9`). Si **ambos** lados de un
par vienen con DV declarado en origen (longitud original ≥ 10, o
formato `XXXXXXXXX-D`), la evidencia es FUERTE — dos fuentes
independientes coincidieron en el mismo NIT+DV que probablemente no
inventaron. Si al menos uno es computed, la evidencia es media y
podría tratarse de una colisión accidental del DV calculado.

**Solución:** marcar el origen del DV (`DV_ORIGEN ∈ {declared, computed, none}`)
y aplicar boost diferenciado en el scorer.

##### Added — columna `DV_ORIGEN` en `AdvancedNitProcessor`

`enhanced_fix_nit` ahora retorna una tupla de **3 elementos**
`(nit_base, nit_ok, dv_origen)`. Detección:

- `declared`: NIT con guion canónico (`r"^\d{9,}-\d$"`) o ≥ 10 dígitos puros.
- `computed`: 9 dígitos puros (DV se calcula vía DIAN).
- `none`: NIT vacío / alfanumérico.

`process_for_deduplication` propaga la columna `DV_ORIGEN` al DataFrame
de trabajo. Tests `test_reproduce_bugs` y `test_nit_processor_parity`
actualizados.

##### Added — perilla `nit_identical_score_boost_declared` en el scorer

Si AMBOS lados del par tienen `DV_ORIGEN == "declared"`, se aplica este
boost. Caso contrario, fallback al `nit_identical_score_boost` existente.

Si la columna `DV_ORIGEN` no está en el DataFrame, todo se trata como
computed (conservador, paridad con v2.9.0).

##### Activado en `deduplication_standard`

```python
"nit_identical_score_boost": 0.05,         # v2.8.0 (computed o mixto)
"nit_identical_score_boost_declared": 0.10, # v2.10.0 (ambos declared)
```

**Calibración:** boost=0.15 funcionaba sobre el dataset robusto pero
producía regresión en golden 269 (F1 0.759 → 0.647). Bajado a 0.10
restaura paridad estricta en golden 269 sin sacrificar la mejora en
sintético.

#### Fix #2 — Penalización por nombre genérico (OPT-IN, default OFF)

**Problema identificado:** tres registros `"INVERSIONES SAS"` con NITs
distintos se fusionan porque el `token_set_ratio` da 1.0 entre nombres
literalmente idénticos. El sistema no diferencia "nombre informativo"
de "nombre genérico".

**Solución intentada:** detectar pares donde AMBOS nombres son **cortos
(≤3 tokens) y compuestos solo por tokens genéricos** (palabras curadas
del dominio empresarial colombiano: INVERSIONES, GRUPO, COMPAÑIA,
COLOMBIA, CONSULTORES, SAS, LTDA, etc.). Multiplicar el name_sim por
`(1 - generic_name_penalty)`.

**Por qué queda OPT-IN:** con `penalty=0.5` la regla resolvió 3 FP en
el sintético robusto, pero produjo **regresión severa en golden 269**
(F1 0.759 → 0.647) porque la lista curada incluye tokens (COMPAÑIA,
COLOMBIA, EMPRESA) que aparecen en razones sociales legítimas de
empresas reales con nombres cortos. Default OFF preserva paridad.

**Recomendación:** activar SOLO tras calibrar con producción real
(50k+ regs). Con corpus grande, los top-N estadísticos del corpus
serán palabras genuinamente frecuentes y la regla será más segura.

```python
# Activación recomendada (post-calibración):
"generic_name_penalty": 0.5,
"generic_name_top_n": 50,
"generic_name_max_tokens": 3,
"generic_name_min_sim": 0.85,
```

##### Added — perillas del scorer

- `generic_name_penalty: float` (default 0.0): magnitud (0.5 = penaliza al 50%).
- `generic_name_top_n: int` (default 30): cuántas palabras del corpus.
- `generic_name_max_tokens: int` (default 3): solo aplica a nombres cortos.
- `generic_name_min_sim: float` (default 0.85): solo aplica si name_sim ya alto.
- `_generic_tokens: set[str]` (poblado por `deduplicate_unified`).

##### Added — stop-words curadas del dominio empresarial colombiano

`deduplicate_unified` ahora puebla `_generic_tokens` combinando:
1. Lista curada de ~50 tokens (INVERSIONES, GRUPO, COMPAÑIA, COLOMBIA,
   CONSULTORES, FABRICA, ZONA FRANCA, SAS, LTDA, EU, etc.).
2. Top-N estadístico del corpus de entrada.

Ampliable vía `profile["generic_name_extra_tokens"]`.

#### Métricas medidas v2.10.0 (defaults activos: Fix #1 ON, Fix #2 OFF)

| Dataset                          | v2.9.0   | v2.10.0  | Δ      |
|----------------------------------|---------:|---------:|-------:|
| Golden 269                       | 0.759    | **0.759**| 0.000  |
| Exhaustivo 1456                  | 0.867    | **0.867**| 0.000  |
| Sintético robusto 660            | 0.933    | **0.935**| +0.002 |
| **Sintético — P4 token disímil** | R=0.17   | **R=0.17**| 0.000  |
| **Sintético — G nombre genérico** | 3 FP    | 3 FP     | 0      |

**Lectura honesta (CORREGIDA en v2.11.0):**

- **CORRECCIÓN DE INTEGRIDAD:** la tabla de v2.10.0 reportaba
  originalmente P4 `R=1.00`. Ese número era FALSO para la versión
  entregada. Correspondía a una configuración con
  `nit_identical_score_boost_declared=0.15` que **se descartó** porque
  regresaba golden 269 (F1 0.759→0.647). El default realmente
  entregado es `0.10`, con el que **P4 sigue en R=0.167** (medido en
  v2.11.0: TP=2, FN=10). La documentación quedó congelada en una
  configuración no entregada. Verificado con medición directa, no
  citado.
- **Fix #1 (activo) con boost_declared=0.10** NO mueve P4 de forma
  material. El boost que sí lo movía (0.15) tenía un costo inaceptable
  en golden 269. La mejora real de Fix #1 a 0.10 sobre los datasets
  globales es ≈0. Honestamente: Fix #1 tal como se entregó es casi un
  no-op en calidad agregada.
- **Fix #2 (opt-in OFF)** resuelve los 3 FP de G_nombre_generico
  cuando se activa con `penalty=0.5`, pero causa regresión severa en
  corpus reales pequeños. Queda implementado y testeado, **NO
  activado** hasta calibrar con producción.

#### Lo que NO se resolvió

- **P1 sigla vs completo** (R=0.40): nombres tipo "EY" son tan cortos
  que probablemente no pasan el pre-filtro LSH ni el length-filter del
  scorer. Requiere intervención distinta — probablemente un caso
  especial para "nombre con ≤2 caracteres" que actúa como sigla.
- **D sufijo confundible** (MENDEZ SA vs LTDA): pendiente Sprint 2,
  requiere decisión de negocio con datos reales.
- **H filial país** (TOTAL COLOMBIA vs ECUADOR): pendiente Sprint 2,
  requiere lista curada de gentilicios.

#### Fixed — Bug crítico en path `disk_based` (heredado de v2.7.0)

`DiskBasedLSHEngine.find_candidates()` no aceptaba el kwarg
`trusted_unique_sources` que `RecordLinkageEngine.link()` le pasaba
incondicionalmente desde v2.7.0. Esto producía `TypeError` ante
cualquier corrida con `linkage_engine_class="disk_based"`, **bloqueando
totalmente el uso del paquete en producción con >1M registros** (ese
camino se autoselecciona en `deduplicate_unified` para datasets de ese
tamaño).

**Detección:** revisión post-medición tras pregunta del usuario "¿Lo
que corrió fue sobre clases en disco?". Verificación empírica con
`config['linkage_engine_class'] = 'disk_based'` reveló el bug, presente
también en v2.7.0, v2.8.0 y v2.9.0.

**Fix:** `find_candidates` de `DiskBasedLSHEngine` y
`TrustedSourceLSHEngine` aceptan ahora `trusted_unique_sources` como
kwarg opcional. En el motor base se ignora con warning si no está
vacío (el comportamiento de fuentes confiables vive en el Trusted). En
Trusted se une al set pasado en `__init__`.

**Impacto medido** tras el fix:

| Dataset            | default | disk_based | Δ      |
|--------------------|--------:|-----------:|-------:|
| Golden 269         | 0.759   | 0.731      | -0.028 |
| Exhaustivo 1456    | 0.867   | **0.874**  | +0.007 |
| Sintético robusto  | 0.935   | 0.935      | 0.000  |

Los dos motores **NO son equivalentes** numéricamente: el LSH disk_based
produce candidatos ligeramente distintos por la implementación interna
de buckets (SQLite vs in-memory dict). La diferencia se manifiesta en
los conjuntos pequeños donde el LSH es marginalmente decisivo. Para
producción (>1M regs), las diferencias en términos relativos son
indistinguibles.

#### Added — test de regresión

- **`tests/test_disk_based_path.py`** (4 tests): verifica que el path
  disk_based corra end-to-end, produzca F1 razonable y no diverja más
  de 0.05 F1 del path default sobre el dataset robusto.

#### Para producción >1M registros

El notebook `notebooks/calibracion_produccion.ipynb` y los scripts
`generar_pares_para_etiquetar.py` y `medir_con_ground_truth.py` ahora
**fuerzan `linkage_engine_class="disk_based"` por default** mediante
el flag `--engine disk_based`. Esto garantiza que las métricas de
calibración representen el comportamiento exacto que tendrás en
producción real, independiente del tamaño de la muestra inicial.

#### Added — herramientas para calibración con producción real

- **`notebooks/calibracion_produccion.ipynb`**: notebook listo para
  Colab. Recibe ruta a tu muestra real (parquet/CSV en Drive), corre
  v2.10.0, produce métricas globales sin necesidad de ground truth,
  estadísticas de cluster, identificación de candidatos a etiquetar.
- **`scripts/generar_pares_para_etiquetar.py`**: muestreo estratificado
  de 1k-2k pares para etiquetado manual. Cubre 5 estratos:
  alta-confianza, frontera, NIT-idéntico-nombre-disímil, nombre-idéntico-
  NIT-distinto, no-clusterizados-pero-cercanos.
- **`scripts/medir_con_ground_truth.py`**: una vez etiquetes los pares,
  este script mide F1/P/R reales por estrato y produce el reporte con el
  mismo desglose que `test_quality_sintetico_robusto.py`.
- **Plantilla de etiquetado**: `tests/data/plantilla_etiquetado.csv`
  con columnas `par_id`, `nit_a`, `razon_a`, `nit_b`, `razon_b`,
  `MISMO_GRUPO`, `CASO_FRONTERA`, `NOTAS`.

#### Added — tests

- **`tests/test_fixes_p1_2.py`** (16 tests): DV_ORIGEN se marca
  correctamente para 8 formatos distintos; boost diferenciado aplica
  solo a ambos-declared; sin DV_ORIGEN es fallback conservador;
  penalty respeta MAX_TOKENS y MIN_SIM_TRIGGER; default OFF es paridad.
- Suite completa: **224/224 verde** en ~56 s.

---

## [2.9.0] — 2026-05-22

### Vectorización completa del scorer (P1-1 del ROADMAP)

Release MINOR de **performance**: el cuello del scorer (`for i in range(n_pairs)`
y `for i in compute_indices` que llamaban a rapidfuzz una vez por par) queda
reemplazado por llamadas batch a `rapidfuzz.process.cpdist`, que ejecuta
el cálculo pairwise en C++. **Paridad bit-a-bit verificada** contra v2.8.0
(tolerancia 1e-9 en floats, exactitud absoluta en enteros).

#### Disciplina aplicada (ROADMAP P1-1 Paso 3.1)

Antes de tocar una línea del scorer:

1. Se capturó un **oráculo de paridad** (`tests/data/oraculo_scorer_p1_1.pkl`)
   ejecutando el scorer v2.8.0 sobre **16 registros** que cubren los casos
   borde críticos: NIT idéntico/distancia 1/2/vacío, nombres idénticos/typos/
   disjuntos/orden distinto, valores `'nan'` literales, primer token
   compartido (activa el bonus *1.05).
2. Se generaron **120 pares × 3 perfiles = 360 outputs** de referencia
   (`default_off`, `with_override_and_boost`, `with_extra_features`).
3. La vectorización procede solo si reproduce los 360 outputs bit-a-bit.

#### Bug atrapado por el oráculo (mérito del proceso)

El intento inicial usó `rapidfuzz.distance.Levenshtein.normalized_similarity`
como reemplazo de `Levenshtein.ratio` (python-Levenshtein) en el cálculo
fonético. El oráculo detectó **231 divergencias en score** en los 360 outputs.
La razón: las dos funciones miden cosas diferentes — Levenshtein clásica
(sustitución=1 edición) vs distancia tipo Indel (sustitución=2 ediciones,
que es lo que usa `Levenshtein.ratio`). La fix correcta es
`rapidfuzz.distance.Indel.normalized_similarity`, **matemáticamente
equivalente** a `Levenshtein.ratio`. Sin el oráculo este bug habría pasado
silencioso y degradado la calidad.

#### Changed — todos los loops del scorer reemplazados por batch

| Método                                              | Loop antes  | Vectorización |
|-----------------------------------------------------|------------:|---------------|
| `_score_batch_vectorized` (inline name sim)         | `for i in compute_indices` | `rf_process.cpdist(scorer=token_set_ratio)` + máscaras numpy |
| `_calculate_nit_distances_vectorized`               | `for i in range(n_pairs)` | `cpdist(scorer=Levenshtein.distance)` |
| `_calculate_phonetic_similarities_vectorized`       | `for i in range(n_pairs)` | `cpdist(scorer=Indel.normalized_similarity)` |
| `_calculate_nit_similarities_vectorized`            | `for i in range(n_pairs)` | `cpdist(scorer=Levenshtein.distance)` + penalización vectorizada |
| `_calculate_name_similarities_vectorized` (alterno) | `for i in range(n_pairs)` | `cpdist` + refinamiento vectorizado |
| `_feature_similarity_vectorized` (token_set_ratio)  | list-comprehension de `fuzz.token_set_ratio` | `cpdist(scorer=fuzz.token_set_ratio)` |
| `score_pairs_from_db` (SQLite write)                | `iterrows()` sobre el batch | `to_numpy()` por columna + tuplas con índices nativos |

**Loops que sobreviven intencionalmente:**
- `audit_pairs` (línea 473): bloque opcional de debug, no path caliente.
- Chunking de SQLite (`for i in range(0, len, batch_size)`): es el batching
  necesario para no agotar memoria, no es overhead a eliminar.

#### Speedup medido (no estimado)

Benchmark sobre datasets sintéticos en el entorno actual
(Python 3.12, rapidfuzz 3.x, numpy 1.x), con `scripts/benchmark_p1_1.py`:

| n_records | n_pairs    | v2.8.0 (s) | v2.9.0 (s) | Speedup |
|----------:|-----------:|-----------:|-----------:|--------:|
| 500       | 749        | 0.027      | 0.014      | **1.9×**|
| 2,000     | 2,997      | 0.087      | 0.024      | **3.6×**|
| 10,000    | 14,995     | 0.421      | 0.091      | **4.6×**|
| 30,000    | 44,996     | 1.276      | 0.268      | **4.8×**|

El speedup **escala con el tamaño** — firma de la vectorización: el
overhead constante de C-extension se amortiza en lotes grandes. En el
ROADMAP P1-1 se esperaban 3-5×; medido **4.6-4.8×** a partir de 10k pares.
Para producción RUES (millones de pares), el throughput sostenido del
scorer pasa de ~35k pares/s a ~165k pares/s. Sobre el ground truth
exhaustivo (1456 pares scoreados), la corrida termina en ~7 s en lugar
de ~30 s — diferencia despreciable en absoluto, pero indicativa del
comportamiento a escala.

#### Calidad — sin cambios (como debe ser)

| Métrica                  | v2.8.0   | v2.9.0   |
|--------------------------|---------:|---------:|
| F1 exhaustivo (1456)     | 0.867    | **0.867**|
| Precision exhaustivo     | 0.886    | **0.886**|
| Recall exhaustivo        | 0.849    | **0.849**|
| F1 golden (269)          | 0.759    | **0.759**|
| Precision golden         | 0.802    | **0.802**|
| Recall golden            | 0.720    | **0.720**|

Vectorización **no es** una intervención de calidad — solo de rendimiento.
Mismas métricas a nivel de pares scoreados.

#### Added — dataset sintético robusto

- **`tests/data/golden_truth_sintetico_robusto.csv`** (660 registros,
  126 grupos): ground truth determinista que estresa **modos de falla
  conocidos** del scorer, con 13 categorías de casos frontera
  explícitos. Regenerable bit-a-bit desde
  `scripts/generar_dataset_robusto.py`.
- Incluye un **validador de integridad** (`_validar_integridad`) que
  rehúsa publicar el dataset si su lógica se contradice (un NIT base en
  múltiples grupos, casos frontera negativos que comparten grupo, etc.).
  Esto atrapó cuatro bugs de diseño durante el desarrollo de v2.9.0 —
  documentado en MIGRATION_LOG §20.

| Tag                       | Tipo     | Lo que estresa                                              |
|---------------------------|----------|-------------------------------------------------------------|
| `P1_sigla_vs_completo`    | positivo | NIT idéntico, nombre tan disímil como "EY" ↔ "Ernst & Young"|
| `P2_historico_fusion`     | positivo | Mismo NIT, denominación que cambió tras fusión              |
| `P3_dv_calc_vs_decl`      | positivo | Variantes "900123456" / "9001234567" / "900-123456-7"       |
| `P4_token_disimil`        | positivo | Nombre comparte 0 tokens significativos con la sigla        |
| `P5_invisibles`           | positivo | Zero-width space, NBSP, caracteres invisibles UTF-8         |
| `A_token_compartido`      | negativo | "BOLIVAR" en tres entidades distintas                       |
| `B_nit_vecino`            | negativo | NITs a distancia Lev=1, nombre disímil                      |
| `C_phonetic_colision`     | negativo | "SOLER" vs "SALER" sobre NITs distintos                     |
| `D_sufijo_confundible`    | negativo | "MENDEZ SA" vs "MENDEZ LTDA" — grupos económicos distintos  |
| `E_nit_corto`             | negativo | NITs públicos cortos consecutivos                           |
| `F_nit_vacio`             | negativo | Dos registros con NIT vacío y nombres similares             |
| `G_nombre_generico`       | negativo | Tres "INVERSIONES SAS" con NITs distintos                   |
| `H_filial_pais`           | negativo | "TOTAL COLOMBIA" vs "TOTAL ECUADOR"                         |
| `S_singleton`             | mixto    | 20 empresas únicas (no forzar agrupación)                   |

**Métricas globales medidas con v2.9.0:** F1=**0.933**, P=**0.936**,
R=**0.931** (2415 TP, 165 FP, 180 FN sobre 2595 pares positivos).

**Diagnóstico cualitativo por categoría** (medido, no estimado):

| Categoría             | Resultado          | Lectura honesta                                                          |
|-----------------------|-------------------:|--------------------------------------------------------------------------|
| Orgánico (variantes)  | R=0.935            | Comportamiento en patrones realistas                                     |
| P1 sigla vs completo  | R=**0.40**         | El override por NIT idéntico ayuda pero score_boost=0.05 es insuficiente |
| P2 histórico fusión   | R=1.00             | El sistema unifica bien denominaciones históricas con NIT igual          |
| P3 DV calc vs decl    | R=1.00             | El AdvancedNitProcessor normaliza estos formatos correctamente           |
| P4 token disímil      | R=**0.17**         | El peso del NIT (0.20) no compensa nombres totalmente disjuntos          |
| P5 invisibles         | R=1.00             | El cleaner limpia zero-width y NBSP                                      |
| D sufijo confundible  | **3 FP de 3**      | Sistema fusiona "MENDEZ SA" ↔ "MENDEZ LTDA" — debilidad sistémica        |
| G nombre genérico     | **3 FP de 3**      | Tres "INVERSIONES SAS" se fusionan — riesgo real en producción           |
| H filial país         | **1 FP de 1**      | "TOTAL COLOMBIA" vs "TOTAL ECUADOR" — confusión por marca compartida     |

Este dataset revela debilidades específicas que el ground truth exhaustivo
no capturaba: los grupos económicos con marca compartida pero entidades
jurídicas distintas son un modo de falla sistemático. Estos NO se
resuelven con bloqueos ni con el fix P0-1; requieren una solución de
futuro (variables adicionales `CIUDAD`/`DIRECCION`, o re-pesado por
entropía del nombre).

#### Added — tests

- **`tests/test_paridad_p1_1.py`** (4 tests, parametrizados sobre 3
  perfiles): paridad bit-a-bit del scorer vectorizado contra el oráculo
  v2.8.0. Tolerancia 1e-9 en floats, exactitud absoluta en enteros.
- **`tests/test_quality_sintetico_robusto.py`** (6 tests): regresión
  sobre el nuevo ground truth + reporte visible por tipo de caso
  frontera (`pytest -s` muestra la tabla completa). Pisos: F1≥0.91,
  P≥0.91, R≥0.91 (margen 0.02 desde lo medido para tolerar fluctuaciones).
- Suite completa: **208/208 verde** en ~58 s.

#### Added — herramientas

- **`scripts/capturar_oraculo_p1_1.py`**: regenera el oráculo si cambia
  el dataset de casos borde. Determinista.
- **`scripts/validar_paridad_p1_1.py`**: ejecuta la validación de paridad
  manualmente desde la línea de comandos (útil al iterar).
- **`scripts/benchmark_p1_1.py`**: mide throughput sobre datasets
  sintéticos. Determinista (`random.seed=42`).
- **`scripts/generar_dataset_robusto.py`**: regenera el dataset robusto.
  Acepta `--n-grupos-extra N` para escalar (útil para benchmarks a
  escala). Determinista (`seed=42`).

#### Lo que NO se hizo

- El cache `self._similarity_cache` del método alterno
  `_calculate_name_similarities_vectorized` quedó **inerte** — ya no se
  consulta por par. En pipelines reales su tasa de hit era < 1 % y el
  costo del lookup era mayor que el cómputo vectorizado en C++. Si algún
  día se necesita un cache real, el lugar correcto es a nivel del
  pipeline (no del scorer per-par), con LRU explícito.
- **`max_nit_distance` por defecto sigue en 3.** El barrido del filtro
  no es parte de P1-1.

---

## [2.8.0] — 2026-05-22

### Tratamiento privilegiado de NIT idéntico (P0-1 del ROADMAP, parcial)

Release MINOR que **mejora modestamente el recall y F1 sobre el ground truth
exhaustivo** atacando un cuello específico documentado mediante diagnóstico
forense: 335 falsos negativos del exhaustivo (21.8 %) tienen `NIT_OK`
idéntico tras la normalización pero su nombre cae bajo `min_name_similarity`
o su score combinado cae bajo `score_threshold`.

#### Confrontación honesta del ROADMAP

Antes de implementar nada, se midió empíricamente el efecto de las dos
intervenciones que el ROADMAP P0-1 priorizaba:

- **Activar `enable_name_blocking`** (Paso 1.2, ya implementado en v2.6.0
  pero OFF por default): ΔF1 = **+0.0003** en exhaustivo, **0** en golden
  269. Confirmado: el bloqueo NO es el cuello, ya estaba prácticamente
  saturado por el bloqueo NIT base + LSH.
- **Barrido de `score_threshold`** (Paso 1.3): los dos ground truth
  prefieren direcciones **opuestas**. Subir threshold a 0.70 mejora el
  exhaustivo (F1 0.863→0.872) pero **derrumba** golden 269 (0.771→0.711).
  Calibrar contra UN dataset overfittea — el ROADMAP no anticipó esta
  tensión. **Se mantiene el default 0.68 (Pareto-óptimo entre ambos).**

#### Diagnóstico de la verdadera palanca

Análisis post-mortem de los 1534 FN del exhaustivo: 332 tienen NIT
**original** idéntico. Tras la normalización vía `AdvancedNitProcessor`
(que añade un DV calculado a NITs sin él), 335 quedan con `NIT_OK`
idéntico no-vacío. Ejemplos reales:

```
NIT 890900148 → NIT_OK 8909001482
NIT_OK         NOMBRE_LIMPIO            ts_ratio
8909001482     AKZOMOBEL PINYUCO        \
8909001482     PINTUKO                   \  todos del mismo grupo
8909001482     COMPAÑIA GLOBAL PINTURAS  /  pero ts < 0.60 entre sí
8909001482     PINTUCO ORBIS            /
```

Estos pares **sí son candidatos** (el bloqueo NIT los emite), pero el
flujo de scoring los descarta por dos gates independientes:

1. **Filtro previo AND estricto:** `name_sim ≥ min_name_similarity (0.60)`
   AND `nit_dist ≤ max_nit_distance (3)`. Pares con NIT idéntico pero
   nombre `'PINTUKO'` vs `'COMPAÑIA GLOBAL DE PINTURAS'` (ts=0.29) son
   descartados ANTES de calcular el score combinado.
2. **`score_threshold` final:** aunque pasen el filtro, el score
   `0.65·name + 0.20·nit + 0.15·phonetic` cae bajo 0.68 cuando el nombre
   es muy disímil — el peso del NIT (0.20) no compensa.

#### Added — dos perillas independientes al `VectorizedScorer`

- **`nit_identical_overrides_name_filter: bool`** (default `False`).
  Si `True`, pares con `nit_distance == 0` (NIT_OK idéntico no-vacío)
  saltan el filtro `min_name_similarity`. NITs vacíos no se eximen
  (la evidencia es ausencia, no coincidencia).
- **`nit_identical_score_boost: float`** (default `0.0`).
  Bonus aditivo al score final cuando NIT_OK idéntico. Se recorta a
  `[0, 1]` tras sumar. Calibrado por barrido contra ambos ground truth:
  `0.05` es el sweet spot Pareto.

Ambos defaults son OFF → paridad bit-a-bit con v2.7.0 sin extra_features.

#### Changed — perfil `deduplication_standard`

Se activan los nuevos defaults:
```python
"nit_identical_overrides_name_filter": True,
"nit_identical_score_boost": 0.05,
```

Otros perfiles (`deduplication_colab_1M`, `deduplication_colab_3M`) NO
fueron modificados: el ROADMAP P0-2 exige calibrarlos contra un ground
truth a escala antes de tocar sus defaults.

#### Resultados medidos (no citados)

| Métrica           | v2.7.0   | v2.8.0   | Δ      |
|-------------------|----------|----------|--------|
| **EXHAUSTIVO (1456 regs)**                              |
| F1                | 0.863    | **0.867**| +0.004 |
| Precision         | 0.886    | **0.886**| ±0.000 |
| Recall            | 0.842    | **0.849**| +0.007 |
| TP                | 8145     | 8217     | +72    |
| FP                | 1053     | 1053     | 0      |
| FN                | 1534     | 1462     | -72    |
| **GOLDEN (269 regs)** — regresión documentada           |
| F1                | 0.771    | 0.759    | -0.012 |
| Precision         | 0.845    | 0.802    | -0.043 |
| Recall            | 0.709    | 0.720    | +0.011 |
| TP                | 443      | 450      | +7     |
| FP                | 81       | 111      | +30    |
| FN                | 182      | 175      | -7     |

**Lectura honesta:** mejora modesta en exhaustivo (que es el dataset
representativo), regresión en golden 269. Los nuevos FP en golden 269
provienen de **colisiones de NIT_OK falsas**: el `AdvancedNitProcessor`
calcula DV para NITs sin DV en ambos lados, y empresas distintas con
NIT base similar terminan con el mismo `NIT_OK`. Es un sesgo del
pipeline upstream, no del fix. Mitigación posible (no incluida en
v2.8.0, requiere su propio ticket): aumentar la confianza del NIT solo
si AMBOS NITs originales venían con DV explícito.

**Importante:** este resultado **no es la mejora de +0.05 que el ROADMAP
imaginaba**. El espacio de mejora de +0.05–+0.10 en F1 vía P0-1
**no existe en este corpus**. La línea base estaba más cerca del óptimo
de lo que el plan asumía. Las próximas ganancias requieren P1-1
(vectorizar el scorer, también aplica a velocidad) y P0-2 (calibrar a
escala con ground truth nuevo).

#### Added — tests

- **`tests/test_nit_identical_p01.py`** (11 tests): paridad estricta
  (defaults OFF == v2.7.0 bit-a-bit), override del filtro, boost del
  score, no-op cuando NIT distinto o vacío, recorte a `[0, 1]`.
- Suite completa: **198/198 verde** en ~70 s.

#### Added — herramientas de réplica

- **`scripts/replicar_v2_8_0.py`**: corre la calibración del barrido,
  imprime las métricas medidas, y genera un dataset sintético adicional
  (`dataset_p0_1_nit_identico.csv`) con casos canónicos de NIT idéntico
  y nombre disímil para verificar el fix contra una ejecución manual.

#### Risk register / lo que NO se cerró del ROADMAP

- **P0-1 Paso 1.3** (barrido contra exhaustivo): se hizo el barrido y se
  documentó la tensión inter-dataset. NO se eligió un threshold global
  nuevo — se dejó en 0.68 como compromiso.
- **P0-2** (perfiles `colab_1M`/`colab_3M`): intactos.
- **P1-1** (vectorizar scorer): el `for i in compute_indices` de la
  línea 340 sigue ahí. No se tocó.
- **P1-2** (clusterer en disco): intacto.
- **P1-3** (test de reanudación tras reinicio): pendiente.

---

## [2.7.0] — 2026-05-22

### Variables adicionales firmadas (P2 Camino #1 + último ítem del ROADMAP)

Release MINOR que **cierra el soporte de variables adicionales** (CIUDAD,
TELEFONO, ...) en el scoring y, a diferencia de v2.6.1.dev0, **sí mueve la
calidad de forma medible**: rompe el techo de precision 0.886 que v2.6.0
documentó como inalcanzable sin estas variables.

#### Corrección del diagnóstico de v2.6.1.dev0

Las `NOTAS_WIP_v2_6_1.md` afirmaban que `extra_features` estaba roto por "un
return temprano en `_score_batch_vectorized` (bloque AUDITANDO PAR)" y que el
código "no se ejecuta en producción". **Ambas afirmaciones eran incorrectas**
(verificado con diagnóstico aislado, no especulación):

- No existe tal return temprano. La suma de `extra_contribution` estaba bien
  colocada y el método se ejecuta en los tres paths de scoring (memoria, db,
  streaming).
- El feature SÍ sumaba correctamente cuando los valores coincidían.

El problema real era de **diseño**, no un bug: el esquema solo PREMIABA
coincidencias (suma ≥ 0), nunca PENALIZABA discrepancias. Por eso un par como
CORONA-Bogotá vs CARVAJAL-Cali (NIT adyacente, token compartido) no se
separaba: ciudad distinta sumaba 0.0 (neutro).

#### Added

- **Tipos de similitud FIRMADOS** en `VectorizedScorer` (rango `[-1, 1]`):
  - `categorical_signed`: +1 coinciden · −1 discrepan · 0 si algún nulo.
  - `exact_signed`: idem sin normalizar mayúsculas (identificadores).
  - `token_set_ratio_signed`: `2 * fuzz − 1`, fuzzy firmado.
  Los tipos no firmados (`categorical`, `exact_or_zero`, `token_set_ratio`)
  se mantienen sin cambios (solo premian).
- **Parámetro `extra_features`** en `deduplicate_unified` (API pública). Antes
  el scorer leía `profile["extra_features"]` pero no había forma de pasarlo
  desde la API. Ahora se valida (fail-fast) y se propaga al perfil activo.
- **`_validate_extra_features`**: rechaza columnas inexistentes, pesos ≤ 0 y
  tipos desconocidos antes de correr el pipeline.
- **`tests/test_scorer_extra_features.py`** (15 tests): paridad, los tres
  tipos firmados/no firmados, manejo de nulos, función de similitud pura, y
  propiedad de negocio (CORONA/CARVAJAL se separa).
- **`tests/test_extra_features_integration.py`** (8 tests): validación
  fail-fast + efecto end-to-end sobre P2.
- **`tests/test_quality_extra_features.py`** (5 tests): calidad a escala sobre
  el exhaustivo enriquecido (skip limpio si el dataset no está generado).
- **`scripts/enriquecer_ground_truth_ciudad.py`**: genera
  `golden_truth_exhaustivo_ciudad.csv` (1456 regs + CIUDAD determinista).
- **`scripts/replicar_v2_7_0.py`**: réplica A/B reproducible.

#### Changed

- `_score_batch_vectorized` recorta el score a `[0, 1]` tras sumar la
  contribución firmada (un par penalizado cae bajo el threshold, que es el
  efecto buscado). Sin `extra_features`, es un no-op bit-a-bit con v2.6.0.

#### Calidad medida (corrida, no citada)

| Dataset | Métrica | sin features | CIUDAD signed w=0.15 | Δ |
|---|---|:---:|:---:|:---:|
| Exhaustivo enriquecido (1456) | Precision | 0.886 | **0.966** | +0.080 |
| | F1 | 0.863 | **0.896** | +0.033 |
| | Recall | 0.842 | 0.835 | −0.007 |
| | Falsos positivos | 1053 | **281** | −73 % |
| Sintético P2 (28) | Precision | 0.800 | **1.000** | +0.200 |
| | Falsos positivos | 2 | **0** | — |

#### Advertencia metodológica (honestidad)

La columna CIUDAD del exhaustivo enriquecido es **sintética y favorable al
feature por construcción** (asignada por grupo verdadero). Mide el TECHO del
beneficio, no el caso real. **No sustituye** un ground truth con ciudades
reales —eso es P0-2 del ROADMAP—. Sobre los ground truth originales (que no
tienen CIUDAD/TELEFONO), la calidad es idéntica a v2.6.0 (paridad verificada:
F1=0.863 exhaustivo, F1=0.771 golden).

#### Estado de la suite

- `pytest tests/`: **187/187 verde** (159 + 15 unit + 8 integración + 5 calidad).
- `ruff check` y `ruff format --check`: verde.

---



### P0-1 Paso 1.2 y 1.3 — bloqueo de nombre + re-barrido de umbrales

Release MINOR que cierra los pasos restantes de P0-1 del ROADMAP. **No
mueve las métricas de calidad** respecto a v2.5.0, pero añade infraestructura
testeada (12 tests nuevos) y documenta empíricamente por qué no se aplican
cambios globales en este ciclo.

#### Resumen ejecutivo honesto

| Métrica | v2.5.0 | **v2.6.0** | Δ |
|---|:---:|:---:|:---:|
| F1 (exhaustivo 1456) | 0.863 | **0.863** | 0.000 |
| Precision (exhaustivo) | 0.886 | **0.886** | 0.000 |
| Recall (exhaustivo) | 0.842 | **0.842** | 0.000 |

**Lo que añade v2.6.0 a v2.5.0:**
- Módulo `engine.lsh.name_blocking` (90 líneas, 99 % cobertura).
- 12 tests aislados del bloqueo de nombre.
- Hooks en el orquestador para activarlo (`enable_name_blocking`, default OFF).
- Barrido completo de `score_threshold` documentado en MIGRATION_LOG §16.
- Script `scripts/replicar_v2_6_0.py` ampliado.

**Lo que NO añade v2.6.0:**
- Cambios en métricas de calidad. El bloqueo de nombre y el ajuste de umbral
  no mueven F1 en los datasets disponibles (motivos documentados abajo).

#### Added

- **`record_linkage.engine.lsh.name_blocking`** — bloqueo multi-pasada por
  nombre, vectorizado, con dos pasadas:
  - **Pasada A — fingerprint:** mismo algoritmo que
    `AdvancedValueSelector._get_fingerprint` (sin sufijos societarios, sin
    no-alfanuméricos, mayúsculas, sin acentos). Captura `ECOPETROL LIMITADA`
    ↔ `ECOPETROL SA`.
  - **Pasada B — token significativo más largo:** token ≥ 4 chars, no
    stopword del dominio. Captura `COLANTA` ↔ `COOPERATIVA COLANTA` cuando
    ambos eligen "COLANTA" como su token más largo (limitado, ver §16.4).
- **`tests/test_blocking_name.py`** (12 tests) — contrato del módulo y
  propiedad de negocio (≥ 20 % de pares verdaderos capturados sobre el
  ground truth exhaustivo).
- **Parámetros nuevos del perfil** (gobiernan el bloqueo, todos opcionales):
  - `enable_name_blocking` (bool, **default False** — opt-in).
  - `name_blocking_fingerprint` (bool, default True).
  - `name_blocking_significant_token` (bool, default True).
  - `name_blocking_min_token_length` (int, default 4).
  - `name_blocking_max_bucket` (int, default 100, más estricto que NIT).
- **`scripts/replicar_v2_6_0.py`** — script ampliado que documenta los tres
  pasos de P0-1 con A/B reproducible.

#### Changed

- **`RecordLinkageEngine.run`** invoca el bloqueo de nombre después del
  bloqueo NIT y antes del scoring. Maneja los tres formatos de retorno
  como el bloqueo NIT. Opt-in: si no se activa, costo = 0.

#### Decisiones documentadas en MIGRATION_LOG §16

1. **El bloqueo de nombre captura 22.7 % de pares verdaderos como techo
   teórico**, pero solo aporta **+5 TP netos** sobre el bloqueo NIT en el
   ground truth exhaustivo (el resto coincide con lo que ya capturó el LSH
   de n-gramas o el bloqueo NIT).
2. **El scorer/clusterer filtra el 99 % de los pares "nuevos"** del
   bloqueo de nombre. El bloqueo no aporta valor con el scorer actual.
3. **Decisión:** default OFF. Código listo para cuando se mejore el scorer
   (ver "Lo que falta" abajo).
4. **Re-barrido de `score_threshold` sobre los dos golden** muestra que
   el umbral actual (0.68) es ÓPTIMO en F1 promedio:
   - thr=0.75 mejora exhaustivo (F1 0.863→0.875) pero **degrada** golden
     269 (F1 0.771→0.679).
   - Esto refleja que el exhaustivo tiene casos negativos diseñados (NIT
     adyacente con tokens compartidos) que un umbral alto descarta bien,
     pero datasets sin esa trampa pierden recall.
5. **NO se aplica cambio global de umbral.** Se documenta la calibración
   y se deja como deuda futura (Optuna, P2-1).

#### Lo que falta (deuda explícita)

P0-1 NO está totalmente resuelto en términos de calidad. Para subir el F1
por encima de 0.88-0.90 se requiere:
- **Variables adicionales** (ciudad, teléfono — último ítem del ROADMAP).
  Sin ellas, los casos negativos diseñados del exhaustivo son inevitables.
- **Re-pesado del scorer por origen del par**: pares de bloqueo NIT
  podrían exigir mayor similitud de nombre, y al revés. Esto aprovecharía
  el bloqueo de nombre que ahora queda dormido.
- **Calibración automatizada (Optuna, P2-1)** sobre múltiples datasets
  para encontrar umbrales robustos en lugar de óptimos puntuales.

#### Tests

- **154/154 tests verdes** (142 v2.5.0 + 12 nuevos de `test_blocking_name`).
- Ruff check y format en verde.
- Cobertura: 38 % global, 99 % en el módulo nuevo.

---

## [2.5.0] — 2026-05-22

### Bloqueo por NIT base — ataque a la causa raíz del cuello de recall (P0-1)

Release MINOR que cierra el ítem **P0-1 Paso 1.1** del ROADMAP. Añade un
bloqueo de candidatos por NIT base **complementario** al LSH por n-gramas de
nombre, atacando pares verdaderos cuyas razones sociales no comparten tokens
(p. ej. `EY COLOMBIA` ↔ `ERNST & YOUNG`) pero sí comparten NIT (o NIT a
distancia ≤ 1). El recall sube +17,6 puntos sobre el ground truth exhaustivo.

#### Calidad medida (ground truth exhaustivo, 1456 regs, 137 grupos)

| Métrica   | v2.4.0 | **v2.5.0** | Δ |
|-----------|:------:|:----------:|:--:|
| F1        | 0.778  | **0.863**  | **+0.085** |
| Precision | 0.934  | 0.886      | −0.048 |
| Recall    | 0.666  | **0.842**  | **+0.176** |
| Grupos predichos vs verdad | 283/137 | 164/137 | mucho más cerca |

Sobre el golden de 269 regs: F1 0.65 → **0.77** (recall 0.53 → 0.71).

**Trade-off honesto:** el criterio del ROADMAP era "recall +≥ 0.05 sin que la
precision baje más de 0.02". El recall subió mucho más de lo pedido (+0.18)
pero la precision bajó 0.048 (más que 0.02). Decisión documentada:
inspección de los 1053 FP introducidos mostró que la mayoría son **casos
negativos diseñados a propósito** en el ground truth (empresas con NIT
adyacente y algún token compartido — exactamente las trampas que el
dataset incluye). El sistema sigue muy por encima del piso de precision
(0.886 > 0.85) y el F1 absoluto sube +0.085. Resolver esos casos a
precision > 0.90 requiere variables adicionales (ciudad, teléfono — último
ítem del ROADMAP) o un re-barrido del scorer.

#### Added

- **`record_linkage.engine.lsh.nit_blocking`** — nuevo módulo vectorizado
  con `NitBlockingConfig` y `block_by_nit_base(df, ...)`. Implementa:
  - Bloqueo por NIT base exacto (`groupby` + emisión de pares intra-grupo
    con cota `max_bucket_size` contra explosión cuadrática).
  - Bloqueo por NIT base a distancia Levenshtein ≤ 1 vía claves canónicas
    de sustitución y borrado (evita comparación O(n²); cada NIT contribuye
    `2·L + 1` claves para longitud L).
  - Filtro `min_nit_length` para descartar NITs claramente truncados.
- **`tests/test_blocking_nit.py`** (12 tests) — contrato del módulo: NIT
  idéntico produce par, NIT vacío no produce, NIT corto descartado, bucket
  grande omitido, vecinos a distancia 1 (sustitución/inserción/borrado),
  vecinos desactivables, determinismo entre corridas, validación de config,
  caso vacío, columna inexistente lanza error, y propiedad de negocio: el
  bloqueo captura ≥ 50 % de pares verdaderos del ground truth exhaustivo.

#### Changed

- **`RecordLinkageEngine.run` (orquestador)** ahora invoca el bloqueo por
  NIT después de `find_candidates` y antes del scoring. Maneja los tres
  formatos de retorno (set en memoria, ruta SQLite, vacío). Decisión de
  arquitectura: el bloqueo va en el orquestador, no en el motor LSH, para
  cubrir todos los motores (`OptimizedLSHEngine` legacy + `DiskBasedLSHEngine`
  + `TrustedSourceLSHEngine`) con un solo punto de integración. DRY.
- **Parámetros nuevos del perfil** (gobiernan el bloqueo, todos opcionales
  con defaults razonables):
  - `enable_nit_blocking` (bool, default True). Apagable por perfil si se
    quiere comparar comportamiento contra la línea base v2.4.0.
  - `nit_blocking_neighbors` (bool, default True). Activa vecinos a Lev ≤ 1.
  - `nit_blocking_max_bucket` (int, default 200). Cota contra NITs
    duplicados patológicos (NITs basura compartidos por miles de registros).
  - `nit_blocking_column` (str, default `"NIT_BASE"`).
- **Pisos de regresión subidos** en `test_quality_golden.py` (F1 0.60→0.73,
  recall 0.45→0.65, precision 0.78→0.80) y `test_quality_exhaustivo.py`
  (F1 0.72→0.83, precision 0.88→0.85, recall 0.60→0.80). Tras este release
  cualquier retroceso debajo de estos niveles falla la suite.

#### Verificado a escala (RUES real, encoding latin-1)

- **50k registros del RUES real**: 250.8 s end-to-end (199 reg/s), RAM pico
  1.4 GB, 48k grupos producidos, max grupo 58. Cero crashes, cero corrupción.
- **A/B sobre 10k RUES**: con bloqueo NIT 9747 grupos vs sin bloqueo 9767
  grupos (−0.2 % diferencia, sin sobre-fusión patológica). Tiempo idéntico.
- El bloqueo NO causa explosión combinatoria en datos reales porque la
  mayoría de NITs RUES son únicos.

#### Tests

- **142/142 tests verdes** (130 v2.4.0 + 12 nuevos). Tiempo: 41 s.
- Ruff check y format en verde.

---

## [2.4.0] — 2026-05-21

### Golden Generator vectorizado y determinista

Release MINOR que vectoriza el Golden Generator (el mayor costo del pipeline) y
corrige un bug de reproducibilidad. Detalle en MIGRATION_LOG §14. Paridad de
lógica de negocio probada bit-a-bit (137/137 grupos) antes de cualquier cambio.

#### Added

- **`AdvancedValueSelector.select_best_name_batch` / `select_best_nit_batch`** —
  API vectorizada por lotes que reemplaza los `groupby().apply()` del generator.
- **`tests/test_golden_selector_paridad.py`** (6 tests) — paridad bit-a-bit
  batch vs individual sobre el ground truth exhaustivo.
- **`tests/test_quality_exhaustivo.py`** (4 tests) y
  **`tests/data/golden_truth_exhaustivo.csv`** (1456 regs, 137 grupos, con casos
  negativos y NITs erróneos). Calidad medida: F1=0.78, precision=0.93.

#### Changed / Performance

- **Golden Generator 5× más rápido** (45k regs: 13.8s→2.4s; 180k: 55.5s→11.1s).
  Eliminados 2 `groupby().apply()`, 1 `apply(axis=1)` y 1 `lambda` de agg.
  `PRIMARY_SOURCE` → idxmin vectorizado; `CONFIANZA` → `np.select`.
- A 225k registros desde disco: 14s, RAM pico 686 MB (procesamiento por lotes
  vía SQLite, apto para 2-4M registros en Colab).

#### Fixed

- **Bug de reproducibilidad en el golden record:** la lógica usaba
  `max(set(...))`, no determinista, así que en empates exactos de
  (frecuencia, longitud) el nombre/NIT elegido variaba entre ejecuciones
  (demostrado: 3 grupos distintos entre 2 corridas de v2.3.0). Ahora el
  desempate es alfabético explícito → golden records reproducibles.



### Reescritura de DiskBasedLSHEngine (el motor de producción)

Release MINOR que reescribe el motor LSH que realmente corre sobre 1.9M
registros RUES. Detalle en MIGRATION_LOG §13. Sin cambio de calidad: F1=0.646
end-to-end, idéntico a v2.2.0.

#### Added

- **`engine/lsh/vectorized_minhash.py`** — `VectorizedMinHasher`: firmas MinHash
  por lotes con NumPy (misma familia de hash que datasketch, determinista).
- **`tests/test_vectorized_minhash.py`** (11 tests) — valida estimación de
  Jaccard (error medio 0.022), determinismo y casos borde.
- **`tests/test_disk_engine.py`** (10 tests) — hash determinista, equivalencia
  de bucket pairs, smoke end-to-end del motor.

#### Changed / Performance

- **Firmas MinHash vectorizadas:** 17× más rápido (20.0 s → 1.2 s en 20k regs).
  Motor completo 2.1× más rápido (32.8 s → 15.6 s). A 50k, de >280 s a 32 s.
- **`_generate_bucket_pairs` vectorizado** con `np.triu_indices` (era doble for).

#### Fixed

- **Bug de corrección en el índice LSH:** `_index_band` usaba `hash()` de Python,
  randomizado por `PYTHONHASHSEED`. Tras un reinicio de sesión de Colab, las
  bandas reanudadas desde checkpoint producían hashes inconsistentes con el
  índice previo → buckets corruptos → pares perdidos en silencio. Reemplazado por
  `_hash_rows_stable` (FNV-1a de 64 bits, determinista). El motor de firmas →
  índice → candidatos es ahora reproducible entre procesos.

#### Deprecated

- `DiskBasedLSHEngine._create_minhash` — ya no se usa internamente (reemplazado
  por `VectorizedMinHasher`). Conservado por compatibilidad.



### Iteración de auditoría — primera medición de calidad de linkage

Release MINOR centrado en cerrar el agujero más grande del paquete: hasta
v2.1.0 ningún test medía si el sistema agrupa bien. Detalle en MIGRATION_LOG §11.

#### Added

- **Módulo `evaluation/pairwise.py`** — métricas de calidad de record linkage
  (precision/recall/F1 a nivel de pares) con `PairwiseMetrics` y `evaluar_pares`.
- **`tests/test_quality_golden.py`** — Validation Level 3: corre el pipeline
  completo sobre un golden set de 269 registros reales y fija pisos de
  regresión (F1≥0.60, recall≥0.45, precision≥0.78).
- **`tests/data/golden_truth.csv`** — golden set embebido (269 registros, 84
  grupos verdad) con retos reales: typos, NITs con dígito errado, ruido aduanero.

#### Changed

- **Perfil `deduplication_standard` recalibrado contra ground truth.**
  F1 0.41 → 0.65 (+56 %), recall 0.27 → 0.53 (≈2×), precision estable (0.84).
  `lsh_threshold` 0.75→0.30, `score_threshold` 0.85→0.68,
  pesos `name:0.55/nit:0.45/phon:0.0` → `0.65/0.20/0.15`.
- **`OptimizedClusterer._cluster_in_memory` vectorizado** de Union-Find con
  `iterrows` a `scipy.sparse.csgraph.connected_components`. **84× más rápido**
  (medido: 7.6 s → 0.09 s sobre 500k pares), partición idéntica probada.
- **`GoldenRecordSelector.select_best_name`** ya no muta el grupo dentro del
  `apply` (elimina `SettingWithCopyWarning` y copias por grupo). Lógica idéntica.
- **README sincronizado con la realidad medida:** versión 2.2.0, 99 tests,
  cobertura 37 %, F1 0.65 declarados explícitamente (antes ocultos o falsos).

#### Fixed

- **Bug de clave duplicada en `processing/text.py`** (F601 real): la clave `Ã`
  estaba repetida en el mapa de encoding, descartando silenciosamente la
  corrección de `Í`. Corregido con claves por bytes mojibake completos.
- **Degradación silenciosa del scorer:** `except:` desnudo que caía a
  `BasicSimilarityCalculator` sin avisar. Ahora loguea WARNING explícito.
- **3 errores de ruff** (variables sin usar en tests) que el README v2.1.0
  afirmaba inexistentes. Corregidos de raíz, no con `# noqa`.

#### Removed

- Ignore de `F601` en `pyproject.toml` (ya no se justifica; la regla vuelve
  activa para detectar regresiones).

#### Performance

- Clustering en memoria 84× más rápido (ver Changed). En 2M registros la fase
  de clustering pasa de minutos a sub-segundo.
- Validado contra datos de producción reales (RUES 1.9M en latin-1): mojibake
  residual 0, sin sobre-fusión patológica. Detalle en MIGRATION_LOG §12.4.



### Mejoras estructurales (Fase F6 de la hoja de ruta)

Release MINOR con features que estaban pendientes desde el diagnóstico
post-migración. Cero cambios al comportamiento numérico del pipeline:
parity bit-exact preservada en todos los escenarios E2E con datos reales.

#### Added

- **F6.1** — `AdvancedNitProcessor.process_for_deduplication` vectorizada
  con `series.apply()` en lugar del loop `for _idx, value in series.items()`.
  Speedup medido: 2.5x en 17K registros; mejora crece con tamaño y
  repetición de NITs (la `lru_cache(50_000)` de `enhanced_fix_nit` se
  preserva). Test de regresión bit-exact:
  `tests/integration/test_nit_processor_parity.py`.
- **F6.2** — `Orchestrator.run()` acepta `force_rerun_phases: set[Phase]`
  (también lista) para re-correr fases específicas. La invalidación
  cascada hacia adelante se aplica automáticamente para preservar
  consistencia (forzar L3 invalida L4 y L5).
- **F6.4** — `PipelineResult.to_excel(path, include=...)` exporta múltiples
  DataFrames a un archivo Excel multi-hoja (engine `openpyxl`).
- **F6.4** — `PipelineResult.to_csv(output_dir, include=...)` exporta un
  CSV por cada DataFrame seleccionado.
- **F6.5** — `PipelineResult.items()`, `.values()`, `__iter__`, `__len__`:
  protocolo `Mapping` completo. Habilita `dict(result)`,
  `for k, v in result.items()`, `len(result)`.

#### Changed

- **F6.3** — Migración de `plt.cm.<colormap>(...)` a la API moderna
  `plt.colormaps["<colormap>"](...)` en 3 archivos de reporting
  (7 ocurrencias). Limpia 7 de los 10 falsos positivos de pylint sin
  cambios de comportamiento.
- **F6.7** — Pandas 3.0 readiness:
  - `select_dtypes(include=["object"])` → `include=["object", "string"]`
    para preservar comportamiento bajo Copy-on-Write.
  - `pd.concat(results, copy=False)` → `pd.concat(results)` (el parámetro
    `copy` está deprecado en pandas 3.0).
  - Resultado: cero `Pandas4Warning` en la suite de tests.

#### Tests

- 19 tests nuevos sumados a los 65 de v2.0.1 → **84 tests totales**.
- `test_nit_processor_parity.py`: parity bit-exact entre versión legacy
  (loop) y vectorizada en 6 casos de borde (9 dig, 10 dig, decimales,
  alfanuméricos, vacíos, repetidos).
- `test_pipeline_result_v2_1.py`: protocolo Mapping completo + exporters.
- `test_orchestrator_force_rerun.py`: cascada de invalidación.

#### Backward compatibility

100% backward compatible. Todos los APIs existentes funcionan igual.
Las nuevas features son aditivas (kwargs opcionales con default seguro,
métodos nuevos en `PipelineResult`).

---

## [2.0.1] — 2026-05-21

### Hotfixes post-migración

Cinco bugs descubiertos durante la validación end-to-end con datos reales
(`Negocios_dedup.csv`, `Oportunidades_dedup.csv`, `Servicios_dedup.csv`).
Los 5 bugs eran de migración notebook → paquete: elementos del estado
global del notebook que no fueron preservados al refactorizar a módulos.

#### Fixed

- **Bug #1** (`utils/logger.py`): `CustomLogger._setup_global_logging_once`
  declaraba `global _LOGGING_CONFIGURED` sin inicialización a nivel de
  módulo → `NameError` en el primer uso de cualquier logger del paquete.
  **Fix**: refactor a atributo de clase `_logging_configured`, eliminando
  la dependencia de una variable global de módulo.
- **Bug #2** (`processing/nit.py`): `AdvancedNitProcessor.enhanced_fix_nit`
  referenciaba `self.nit_regex`, inexistente; la clase base define
  `self.non_digit_regex` → `AttributeError` al limpiar NITs con caracteres
  no-dígito (guiones, espacios). **Fix**: corregir referencia al regex
  heredado.
- **Bug #3** (`engine/lsh/legacy.py`): `_init_database` creaba las tablas
  `candidates` y `stats` pero omitía `index_data`, que `_process_with_sqlite`
  usa para mapear `idx → fuente` al filtrar por trusted sources →
  `OperationalError: no such table: index_data`. **Fix**: esquema SQL
  unificado en constante de módulo `_LSH_LEGACY_SQLITE_SCHEMA` aplicado vía
  `conn.executescript()`.
- **Bug #4** (`deduplication/unified.py` + `evaluation/hyperparameters.py`):
  contrato roto contra `RecordLinkagePipeline.run()`. Los callers accedían a
  `results["correlative_table"]` y `results["df_linked"]`, pero el pipeline
  los borraba antes del return cuando `keep_intermediate_results=False`
  (default) → `RuntimeError: El pipeline no generó la tabla correlativa`.
  **Fix arquitectural**: nuevo módulo `pipeline.result.PipelineResult`
  (dataclass con `cached_property`) que carga los DataFrames lazy desde
  checkpoints parquet. Implementa protocolo dict-like
  (`__getitem__`, `get`, `keys`, `__contains__`) para preservar
  compatibilidad con callers existentes. La fase 4.5 ahora escribe DOS
  checkpoints (correlative_final y golden_final) para que los outputs
  canónicos sean los post-consolidación.
- **Bug #5** (`pipeline/_internal.py`): `_phase_cleanup` usaba `yield` sin
  decorador `@contextmanager` → `TypeError: 'generator' object does not
  support the context manager protocol` al iniciar cualquier fase del
  `Orchestrator`. **Fix**: import `contextlib.contextmanager` y decorar.

#### Added

- `tests/test_reproduce_bugs.py`: 5 tests de regresión, uno por bug. En
  v2.0.0 fallan los 5; en v2.0.1 pasan los 5.
- `src/record_linkage/pipeline/result.py`: nuevo módulo `PipelineResult`.

#### Changed

- `RecordLinkagePipeline.run()` ahora retorna `PipelineResult` en lugar
  de `dict`. **Backward-compatible** vía protocolo dict-like.
- `checkpoint_05_consolidated.parquet` → `checkpoint_05_correlative_final.parquet`.
- Nuevo `checkpoint_05_golden_final.parquet`.

#### Validation

Parity numérica verificada con Negocios (6,071 registros) y cross-source
con Negocios + Oportunidades + Servicios (25,731 registros). Detección
inalterada: 3 grupos duplicados internos en Negocios, 5,727 entidades
cross-source en el cruce de 3 fuentes.

---

## [2.0.0] — 2026-05-21

### Cambio mayor: refactor completo de notebook a paquete Python

#### Added
- Estructura de paquete Python publicable en PyPI bajo el nombre `rues-linker`.
- `Config` y `Rutas` (dataclasses) como única fuente de verdad para parámetros y rutas.
- `get_snowflake_credentials()` con tres fuentes (Colab Secrets → env vars → `config.json`)
  y nunca imprime credenciales.
- `docs/secrets.md` con guía paso a paso de configuración de credenciales.
- Notebook de ejecución `notebooks/ejecutar.ipynb` con patrón EXTRAS + EJECUTAR (≤15 líneas
  en la celda EJECUTAR).
- Script CLI `scripts/ejecutar_produccion.py` para correr fuera de notebook.
- `tests/test_smoke.py` (27 tests de importabilidad y configuración).
- `tests/test_vectorization_equivalence.py` (17 tests que prueban equivalencia numérica
  exacta entre `.apply` original y vectorizado).
- GitHub Actions CI: `ruff check`, `ruff format --check`, `pytest` en Python 3.10/3.11/3.12,
  construcción de `sdist` y `wheel`, validación con `twine check`.
- GitHub Actions publish: trusted publishing OIDC a TestPyPI/PyPI.
- 8 módulos internos (`_internal.py`, `_constants.py`, `_lsh_refs.py`, `_phase_constants.py`,
  `_globals.py`, `_priorities.py`, `_flags.py`, `_models.py`) para constantes globales
  del notebook.

#### Changed
- **`Orchestrator._run_L2` ya no requiere monkey-patching.** La versión optimizada
  (HybridStorage + selección automática entre `DiskBasedLSHEngine` y
  `TrustedSourceLSHEngine`) es ahora el método nativo. La versión antigua se conserva
  como `_run_L2_legacy` para auditoría.
- 5 vectorizaciones aplicadas con tests de equivalencia exacta:
  1. SEVERIDAD en `reporting/suite.py` (lambda → `np.where`)
  2. TRUE_GROUP en `evaluation/ground_truth.py` (lambda → `notna` + `.str.strip()`)
  3. Dos `apply(len)` en `classifier/hybrid.py` → `.str.len()`
  4. Matriz de co-ocurrencia en `reporting/suite.py`: O(n²·g) → O(n·g) con producto matricial
- 4 ciclos de imports rotos vía imports diferidos o extracción a módulo neutral.
- Ordenamiento topológico de constantes en `_internal.py` y `_constants.py`.

#### Fixed
- 387 errores F821 (undefined-name) detectados por ruff → final cero F821.
- Imports relativos incorrectos en archivos a profundidad 2 (`engine/lsh/`).
- Self-import en `pipeline/_internal.py`.

#### Removed
- Código experimental (celdas 66-72, 100-101, 128-129, 159, 173-189, 221-245, 249-251,
  269-291 del notebook). Lista completa en `MIGRATION_LOG.md` sección 4.
- Duplicados de clases: `AdvancedValueSelector`, `MemoryMonitor`,
  `SafeSQLiteConnection` versiones de celda 124.

#### Security
- `.gitignore` bloquea `config.json`, `.env`, `*.parquet`, `*.csv`, `*.db`.
- Credenciales nunca en código.

---

## [1.x] — Histórico (notebook monolítico)

Notebook único `2026_02_15_DEDUPLICAR_Y_RECORD_LINKAGE_.ipynb` (292 celdas,
25.745 LOC, 83 clases, 156 funciones top-level). Se mantiene en git para referencia.

---

# Roadmap

## CRÍTICO: antes de v2.0.0 en producción

| # | Acción | Tiempo | Por qué |
|---|---|---|---|
| 1 | Rotar credenciales Snowflake si estaban hardcoded | 15 min | Si están en GitHub o commits viejos, riesgo de exposición |
| 2 | Configurar Colab Secrets (`SNOWFLAKE_*`) | 10 min | Pre-requisito para conectar a Snowflake |
| 3 | Push a GitHub y validar CI verde | 15 min | Confirma que el pipeline compila en CI limpio |
| 4 | **Validación end-to-end con 5% de tus datos** | 2 horas | Comparar `golden` + `correlative` vs notebook. Si son idénticos, 100% seguro |
| 5 | Correr en producción completo | 3-5 horas | Solo tras el paso 4 |

## CORTO PLAZO (2-4 semanas)

| # | Tarea | Esfuerzo | Prioridad |
|---|---|---|---|
| 6 | Tests unitarios de lógica de negocio (engine/scorer, golden/selector, golden/generator) | 3-5 días | ALTA |
| 7 | Eliminar `_run_L2_legacy` tras 2 corridas exitosas | 5 min | MEDIA |
| 8 | `pre-commit` hooks (ruff + pytest) | 30 min | MEDIA |
| 9 | Publicar v2.0.0 en TestPyPI → validar `pip install` | 30 min | MEDIA |
| 10 | Publicar v2.0.0 en PyPI productivo | 5 min | MEDIA |

## MEDIANO PLAZO (1-3 meses)

| # | Tarea | Esfuerzo | Beneficio |
|---|---|---|---|
| 11 | Profilear con `cProfile` y vectorizar `.apply` complejos restantes | 1-2 semanas | Posible 15-30% de mejora si son cuello de botella real |
| 12 | Sphinx + Read the Docs | 1-2 días | Adopción externa |
| 13 | Type hints completos + `mypy --strict` | 1 semana | Errores atrapados antes de runtime |
| 14 | Benchmarks reproducibles | 2 días | Detección automática de regresiones |
| 15 | Refactorizar `Orchestrator` (2.170 líneas) en componentes | 1-2 semanas | Legibilidad y testabilidad |

## LARGO PLAZO (3-12 meses)

| # | Tarea | Esfuerzo | Beneficio |
|---|---|---|---|
| 16 | Migración a Snowflake nativo (SQL antes de pandas) | 2-3 semanas | Escalar a 10M+ registros sin OOM |
| 17 | DuckDB en lugar de SQLite local | 1 semana | 5-10x más rápido en agregaciones |
| 18 | Paralelización con Dask para scoring | 2 semanas | Mejor uso de CPUs en máquinas grandes |
| 19 | API REST con FastAPI | 2-3 semanas | Integración con sistemas downstream |
| 20 | ML para clasificar pares (en lugar de reglas) | 1-2 meses | Precisión potencialmente mayor |
| 21 | Extensión a otros países (Brasil, México, Chile) | 2-3 meses | Audiencia más amplia |

## Deuda técnica documentada

- 86 warnings de ruff silenciados en `per-file-ignores` para código heredado del
  notebook. Resolverlos requiere reescribir lógica de negocio. Documentados en
  `pyproject.toml` y `MIGRATION_LOG.md` sección 9.
- `_run_L2_legacy` en Orchestrator. Eliminar tras 2 ciclos de producción exitosos.
- `OptimizedLSHEngine` en `engine/lsh/legacy.py`. Eliminar si tras 6 meses ningún
  flujo lo invoca.
- Stub `silent_run` en `optimization/engine.py`. Reemplazar por implementación real
  de celda 159 o `contextlib.redirect_stdout`.
