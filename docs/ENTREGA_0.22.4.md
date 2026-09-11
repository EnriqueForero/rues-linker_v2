# Entrega 0.22.4 — la guardia funcionó, y lo que atrapó era del catálogo

> **Qué pasó.** Usted corrió el 07 sobre `snowflake_v2` y la celda 6 se detuvo,
> antes de emparejar, con 19 grafías de `PAIS_ESTANDAR` fuera del catálogo
> (617 filas, 0,17 %). Es el comportamiento de 0.22.3 funcionando como se
> diseñó. Lo que la lista decía es lo que importa.

---

## 1. Qué eran las 19

| | grafías | filas | |
|---|---:|---:|---|
| países que **faltaban** en el catálogo | 11 | 63 | Irán, Afganistán, Liechtenstein, Vanuatu, Gibraltar, Isla Norfolk, Islas Salomón, Santo Tomé y Príncipe, Ciudad del Vaticano, Islas Marianas del Norte, Samoa |
| países presentes **sin la grafía en español** | 7 | 110 | Zimbabue, Fiyi, Lesoto, Bosnia, Guinea-Bisáu, República de Macedonia, Islas Vírgenes Estadounidenses |
| **no es un país** | 1 | 444 | `OTROS` |

**18 de 19 eran de la librería, no suyas.** Que a un catálogo de 203 países le
faltaran Irán y Afganistán no es un detalle: se construyó sobre la base DIAN,
que nunca los trajo. Y la sugerencia automática acertó **6 de 18**
(`IRAN → IRLANDA 0,89`, `AFGANISTAN → ALBANIA 0,76`) — la prueba empírica de
por qué es ayuda y no regla.

**Corrección:** catálogo 203 → 214 países, 333 → 378 grafías, con nombres
canónicos en español y alias ISO. `SAMOA` y `SAMOA AMERICANA` siguen siendo
distintas. Una prueba por cada grafía real.

## 2. `OTROS`: aceptar lo conocido sin apagar la guardia

No es un país. La escotilla que existía (`paises_sin_clasificar="aislar"`) es
**global**: usarla para aceptar `OTROS` habría dejado pasar en silencio cualquier
grafía nueva mañana — exactamente lo que la guardia existe para impedir.

Nuevo: `paises_aislar` declara grafías concretas como no-país. Cada una se aísla
con su propio `PAIS_FINAL` (`SIN CLASIFICAR: OTROS`), se deduplica por nombre
dentro de sí misma, nunca se fusiona con un país real — **y el modo sigue en
`detener`**. En su notebook: `NO_SON_PAISES = ("OTROS",)` en la celda 4.

Verificado con **sus 19 grafías exactas** inyectadas en la base:

| | resultado |
|---|---|
| `NO_SON_PAISES = ()` | se detiene en 0d listando **solo** `OTROS` (444 filas) — las otras 18 ya mapean |
| `NO_SON_PAISES = ("OTROS",)` | cobertura OK · `OTROS` aislado · smoke test OK · por fases = fachada |

## 3. La publicación: el 08 cayó en la compuerta siguiente

Con 0.22.3 pasó A.1 completa —banco con huella idéntica incluido— y
`publicar_profesional()` se detuvo en `correr_tests_locales`:
`ImportError` al colectar `tests/test_comparadores_tipos.py:15`. Esa línea es
`from hypothesis import …`, y la celda 3 del 08 instalaba una **lista a mano**
sin `hypothesis`. El CI instala `.[dev]` desde el pyproject; el notebook no.
Reproducido en un venv limpio: sin `hypothesis`, 2 errores de colección; con
las extras `[dev]` del pyproject, 1.582 casos colectados y 0 errores.

Y usted no pudo ver la causa porque `_run` recortaba la salida a 600
caracteres. Corregido: A.0 instala y verifica las extras `dev` **del
pyproject**; `correr_tests_locales` usa los marcadores del CI
(`not canario and not slow`); la salida conserva los últimos 3.000 caracteres
y separa las líneas de causa. Tres contratos lo vigilan.

**Orden en Colab, y por qué:** A → A.0 → A.1 → B → C → D. A.0 es la que
instala; si la salta, A.1 falla con `ModuleNotFoundError` y D con
`ImportError: hypothesis`. Los dos síntomas apuntan a los tests; la causa es
la instalación.

## 4. Su base real: el importador más grande no existía

Corrí la deduplicación sobre las 355.681 filas que compartió. Antes de mirar el
resultado, las tres primeras filas por FOB ya decían algo: `NO DISPONIBLE` en
Estados Unidos, Panamá y China. Es un centinela de nombre —como el `'0'` de la
base DIAN— y **no estaba en la lista del motor**: 69 filas, una por país, con
el **21,7 % del FOB**, convertidas en "el importador más grande" de cada país.
Con `'0'` (9,3 %) y `A LA ORDEN`/`TO ORDER` (1,6 %), el **32,5 % del FOB** de su
base no tiene importador nombrado.

Corregido en la librería (`PLACEHOLDERS`), con una prueba que usa las cifras
reales, y su notebook deja de tener una copia propia de la lista. La lista
deduplicada que le entrego los muestra en su propio grupo, marcados
`sin_nombre_utilizable`, con el FOB contado aparte. **No los borre ni los
fusione**: borrarlos sesga el ranking y fusionarlos inventa un importador
gigante. `TO ORDER OF ‹banco›` (1.181 filas, 1,75 %) nombra a la parte y se
conserva como nombre; decida usted si eso es un importador.

## 5. La corrida completa murió por memoria — y ya no

La primera corrida completa sobre sus 355.681 filas murió en la partición 175
de 214 (`exit 137`, 15 GiB). Reproducido sobre la partición USA sola: 54.669
nombres, 24,5 M de pares candidatos, **11,4 GiB de pico** y una tabla de
decisiones de 2,6 GiB. En Colab Free (12,7 GiB) habría muerto antes.

Tres causas, todas de costo por par y ninguna de calidad: el motor puntuaba los
24,5 M de pares de una sola vez; la unión del bloqueo LSH se construía con pares
de 16 bytes de todas las bandas; y la tabla de decisiones guardaba los dos
nombres en cada uno de los ~50 M de candidatos. Corregido en la librería:
**scoring por lotes** (`pares_por_lote`, 1 M), unión por clave escalar y una
tabla de decisiones que conserva solo lo auditable (fusionados, vetados y pares
con similitud ≥ 0,70), con los nombres resueltos bajo demanda.

**Lo que no cambió, y está probado**: la partición USA da los mismos 24.476.082
candidatos, 28.409 fusiones y 44.761 grupos; la referencia DIAN da la misma
correlativa bit a bit. Lo que cambió: USA pasa de 520 s a 340 s y de 11,4 GiB a
4,6 GiB; la base completa termina: **355.681 filas → 209.038 importadores**, 52,8 M
de candidatos, 12 min 54 s de emparejamiento, 17 min de pared con exportación,
**pico de 4,81 GiB**, 10/10 invariantes. Cabe en Colab Free.

**Y una segunda caída, ya con la memoria resuelta:** la corrida terminó el
emparejamiento (10 invariantes en OK, Parquet escrito) y falló al escribir el
XLSX porque diez razones sociales de su base traen un byte `\x1a` —mojibake de
"Ñ"/"Ó": `COMPAÃ\x1aIA DE GALLETAS POZUELO`— que Excel no admite. El
emparejamiento no lo sufría (el normalizador ya lo quitaba); el archivo sí.
Corregido en el exportador de la librería, con esa grafía como prueba. Le
conviene saberlo por otra razón: esas diez filas están mal codificadas en el
origen, y la de `GALLETAS POZUELO` convive con `COMPAÑÍA DE GALLETAS POZUELO DCR
S.A.` bien escrita.

Si usa `RESULTADO.decisiones` directamente: ya no trae `NOMBRE_A`/`NOMBRE_B`
(`RESULTADO.decisiones_con_nombres()` los añade) y solo contiene los pares
auditables; `n_candidatos` sigue siendo el total.

## 6. Lo que debe hacer

1. Descomprimir el zip reemplazando. Trae `dist/rues_linker-0.22.4-py3-none-any.whl`.
2. Correr el 07 tal cual. Con la configuración entregada, la celda 6 pasa: las
   18 grafías mapean y `OTROS` está declarado. Si en su base hay alguna otra
   que yo no vi, se detendrá y la listará: decida en la celda 4.
3. La celda 6 imprime `✅ 1 declarada(s) en NO_SON_PAISES → OTROS`. Si no lo
   ve, la celda 4 no se ejecutó.
4. Vuelva a etiquetar `MUESTRA_REVISION` en la primera corrida completa: la
   precisión de 0,966 es de la base DIAN.
5. La lista deduplicada de su base va adjunta a esta entrega, tal como la
   produce el 07 (§5). El XLSX completo de 12 hojas pesa 47 MiB y supera el
   límite de adjuntos de este canal, así que va en tres piezas equivalentes:
   `…__CORRELATIVA.parquet` y `…__CORRELATIVA.csv.gz` (la misma tabla, una fila
   por fila de su base, con `ID_IMPORTADOR`, `RAZON_SOCIAL_FINAL` y
   `PAIS_FINAL` — **es el entregable**), `…__sin_CORRELATIVA.xlsx` (las otras
   11 hojas: GOLDEN, PAISES, REVISION, MUESTRA_REVISION, METRICAS, INVARIANTES,
   SENSIBILIDAD, RECALL_BLOQUEO, PARAMETROS, SUGERENCIAS_PAIS y TIEMPOS), más
   `…__GOLDEN.parquet` y `…__METADATA.json` con el SHA-256 de su archivo de
   entrada, los parámetros y los tiempos. Al correr el 07 en su Drive obtiene
   el XLSX completo.

## 7. Verificación

| | |
|---|---|
| catálogo | 214 países · 378 grafías · índice sin alias ambiguos |
| contratos de notebooks | 84 |
| referencia DIAN (0 sin clasificar) | **100.008**: correlativa idéntica a 0.22.3 salvo las 204 filas de `A LA ORDEN`/`TO ORDER`/`TO THE ORDER` (1,9 % del FOB), que dejan de formar 95 "importadores" y quedan como singletons sin nombre (+111) |
| banco institucional | **PASA** · huella idéntica · calidad sin mover un decimal |
| compuertas A→A.0→A.1 con banco | **0 fallos** |
| suite completa (entorno de desarrollo) | **1.630 pasan, 2 saltos de Windows, 1 falla preexistente** (`test_sin_nit_recalibrado.py::test_sin_nit_perfil_recalibrado_no_regresa`, precisión 0,864 < 0,87 en el perfil SIN_NIT con NIT del pipeline clásico; falla igual, número por número, con su árbol 0.22.0 sin tocar, con 0.22.3 y con este; no la toca ningún cambio de 0.22.4) |
| venv limpio: rueda + extras dev, como Colab | 1.633 casos colectados, 0 errores · **1.630 pasan, 2 saltos, la misma falla preexistente** |
| scoring por lotes (0.22.4, §5) | partición USA idéntica (candidatos, fusiones, grupos) · DIAN bit-idéntica · 24 pruebas nuevas |
| exportación con `\x1a` (0.22.4, §5) | XLSX se escribe con la grafía real · 6 pruebas nuevas |
| corrida real completa | 355.681 filas → 209.038 importadores en 17 min de pared (12 min 54 s de emparejamiento, 52,8 M de candidatos) con **pico de 4,81 GiB**, 10/10 invariantes · XLSX + 12 Parquet + metadata escritos |

## 8. Una prueba en rojo que no es de esta versión

`tests/test_sin_nit_recalibrado.py::test_sin_nit_perfil_recalibrado_no_regresa`
mide la precisión del perfil `deduplication_sin_nit_conservador` —el pipeline
clásico con NIT, no el flujo de importadores— sobre el ground truth del
repositorio y exige ≥ 0,87. Da **0,864** (TP 2.548, FP 400). Lo corrí contra su
árbol 0.22.0 sin tocar, contra 0.22.3 y contra 0.22.4, en dos intérpretes y con
tres semillas de hash: el mismo 0,864 en todos. **No la causa nada de esta
entrega**, y no la escondí bajando el umbral ni marcándola lenta. Lo que le
afecta: la celda de tests del 08 (`correr_tests_locales`) se detendrá ahí si en
Colab también da 0,864. Tiene dos salidas honestas: recalibrar el perfil SIN_NIT
(su techo de datos está documentado desde 0.7.5) o declarar la cota medida en la
prueba, con la cifra y la fecha. Ninguna de las dos es un cambio de 0.22.4.

## 9. Un error de método que conviene dejar escrito

La edición del notebook 07 no se aplicó la primera vez y no me di cuenta hasta
que el contrato lo dijo: escribí un archivo auxiliar sin salto de línea final,
`read` devolvió 1 y el `&&` saltó el script sin ruido. Un pipeline de shell
puede omitir un paso entero sin error visible; la única defensa es verificar el
**efecto**, no la orden. Los contratos existen para eso, y funcionaron.
