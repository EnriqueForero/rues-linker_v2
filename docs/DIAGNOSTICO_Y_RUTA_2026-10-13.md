# Diagnóstico integral y ruta de trabajo (2026-10-13)

*Revisión de todo lo avanzado en el plan «v2 → producción» (F0 a F3.1b), con medición propia, y la ruta para que la librería
sea de talla mundial: precisa, rápida y ejecutable en Google Colab gratuito. Escrito para Enrique; sustituye, en lo que
contradiga, el orden y los plazos de `PLAN_EJECUCION.md` (que sigue siendo la referencia de las tareas ya cerradas).*

---

## 0. Veredicto en diez líneas

1. **Lo que se dice hecho, está hecho y se reproduce.** Volví a correr el banco de 30.486 registros (huella `5bfed0d1…`, idéntica
   a la línea base F2, 43 s) y la escala de 139.028 (huella `d904ea16…`, idéntica, 333 s). Las 85 pruebas de paridad de la
   vinculación pasan; los 36 arreglos de F3.1b tienen prueba y declaración; la deuda no sube; mypy en el anillo da 0.
2. **Pero «hecho» no es «bueno».** Toda la calidad está medida sobre datos **sintéticos**. No existe una verdad real etiquetada
   del RUES ni del piloto de vinculación. Hoy nadie puede afirmar cuántas empresas reales une mal o deja de unir.
3. **El Nightly de `main` está rojo desde el 24 de septiembre, en sus 20 corridas**, por una regresión de precisión del perfil
   sin identificador (0,864 < 0,87), determinista y anterior a F0. El CI diario excluye las pruebas `slow`, así que nunca bloqueó
   nada. El documento que esa prueba cita (`docs/DEUDA_SIN_NIT.md`) no existe.
4. **El motor gasta el 90 % del tiempo en candidatos que descarta**: 23 candidatos por par válido a 30k, 65 a 139k, 59 a 463k.
   El bloqueo LSH pierde el **28 % de los pares verdaderos sin identificador** (recall de pares 0,718); un TF-IDF de trigramas
   ingenuo que escribí hoy recupera el 94 % con la mitad de candidatos y en la mitad del tiempo. Ahí está el salto, no en afinar umbrales.
5. **El puntaje no es probabilístico ni está calibrado**: suma lineal nombre 0,5 + NIT 0,5 con umbral fijo 0,45, y el peso IDF
   que la librería ya sabe calcular está **apagado** (`idf_weight_blend = 0.0`) en el perfil de producción.
6. **Hay dos reglas para «mismo nombre»**: el motor principal usa `token_set_ratio`; la vinculación usa `SimilitudNombre`
   (IDF + Jaro-Winkler). Viola la regla de la casa («una regla se escribe una vez») y hace que los dos flujos no sean comparables.
7. **77.489 líneas de código fuente** para un problema que motores de referencia resuelven en una fracción: 8.628 líneas no son
   alcanzables desde la API pública; `orchestrator.py` tiene 2.373 líneas, `scorer.py` 2.126 (una función con complejidad 62).
8. **Colab no está resuelto**: 463k filas toman 33 min y 2,5 GiB; la proyección a 4 M es ≈ 9 GiB (al borde) y sin reanudación probada.
9. **F3.1b sí mejoró el motor de vinculación** (uniones con restricciones, siglas en los dos sentidos, Excel seguro), pero no hay
   medición en datos reales: las decisiones se validaron con pruebas unitarias y 4 escenarios inventados.
10. **La ruta correcta invierte el orden del plan vigente**: primero verdad real e instrumentos, después el bloqueo (la palanca
    grande), después el puntaje calibrado, luego escala en Colab, luego la vinculación sobre ese núcleo, y al final el
    endurecimiento de ingeniería. Estimación honesta: **14 a 16 semanas** de trabajo enfocado, no las 7,5 que suma el plan actual para F3–F6.

---

## 1. Qué revisé y cómo

| Qué | Cómo | Resultado |
|---|---|---|
| Línea base del banco (F2) | `scripts/banco.py --datos data/benchmark/benchmark_institucional.csv.gz` en esta máquina (4 vCPU) | huella `5bfed0d1…` **idéntica**; 43,2 s (evidencia: 67 s); macro-F1 0,8803; P 0,9557 R 0,7927; FP sobre negativos 246 |
| Escala 139k (F2) | `scripts/escala.py --tamanos 139k` | huella `d904ea16…` **idéntica**; 333 s (evidencia 466 s); 12.739.949 candidatos; RSS 1.151 MiB; F1 0,9662 |
| Recall del bloqueo actual | pares verdaderos ∩ `candidates.db` de la corrida del banco | **0,8662** (CON_ID 0,9964 · SIN_ID 0,7183) |
| Bloqueo alternativo | TF-IDF trigramas + producto disperso por lotes (`docs/evidencia/revision_2026-10-13/bloqueo_tfidf.py`) | 532.258 candidatos, recall de pares 0,9041 (SIN_ID 0,9427), 12 s |
| Paridad y arreglos de la vinculación | `tests/test_vinculacion_*` en py3.11+pandas 3 y py3.10+pandas 2; arnés `comparar.py` | 1.345 pruebas verdes en los dos entornos; el arnés solo difiere en lo declarado |
| Regresión del Nightly | logs de las corridas 13, 25, 31 y 32; la prueba corrida aquí con rapidfuzz 3.14.5/3.14.6, pandas 3.0.5/3.0.6, scikit-learn 1.9.0/1.9.1, scipy 1.17/1.18 | misma cifra siempre (TP 2.548, FP 400): **no es la versión de una dependencia** |
| Código muerto | grafo de importaciones por AST desde `record_linkage`, `api` y `flujo.*` | 47 módulos / 8.628 líneas no alcanzables desde la API; algunos los usan scripts o notebooks |
| Deuda | `scripts/deuda.py`, `scripts/reglas_estrictas.py`, mypy en frío | 11 funciones CC ≥ 20 · 101 `except` sin relanzar · 152 `print` · 117 `os.path` · 80 errores mypy fuera del anillo |
| CI | corridas de `main` del 13-oct | CI 36 min (pruebas 29–35 min por versión de Python); Nightly 28 min, rojo |

**Lo que no pude verificar:** nada sobre datos reales (los insumos del piloto se perdieron al reciclarse el contenedor); nada en
Colab (este entorno no es Colab y no pude abrir `https://research.google.com/colaboratory/faq.html` por la política de red: las
cifras de Colab que uso son las conocidas del nivel gratuito, ≈ 12,7 GB de RAM, 2 vCPU, sesiones de hasta 12 h sin garantía,
y hay que confirmarlas en esa página).

Evidencia nueva de hoy: `docs/evidencia/revision_2026-10-13/` (banco, escala, experimento de bloqueo y su script).

---

## 2. Estado verificado de lo avanzado

| Fase | Afirmación del plan | Verificado hoy | Veredicto |
|---|---|---|---|
| F0 fundaciones | CI, líneas base, trinquetes (deuda, reglas estrictas), evidencia de escala | Banco y escala reproducen; `deuda.py` y `reglas_estrictas.py` pasan; Codecov sube cobertura | ✅ sólido. Falta: el Nightly rojo nadie lo mira |
| F1 contrato de salida | Nada se degrada en silencio; `ResultadoLinkage`, manifiesto `omitidos` | Contrato probado en los 5 caminos (`test_contrato_salida.py`, `test_conformidad_caminos.py`) | ✅. Costo: 5 caminos conviven (`linkage`, `dedupe`, `link`, `flujo.cruce`, `flujo.importadores`) |
| F2 calidad del motor | Estrellas 0,80 en `linkage()`, `ID_GRUPO` determinista, `ID_ENTIDAD` estable | Huella del banco idéntica; FP 287→246 y recall SIN_ID 0,65→0,58 como declara ADR-0011 | ✅ medido y declarado. Es un **canje**: −14 % de FP por −10 % de recall; la decisión fue «unir mal duele más» |
| F3.0/F3.1 vinculación | Motor del notebook 10 en la librería con paridad | 85 casos de paridad; Excel celda por celda; 2 entornos | ✅. El notebook 10 **sigue** con su celda `%%writefile` de 177.842 caracteres (F3.5 pendiente) |
| F3.1b arreglos | Nivel 1 completo + 5 del nivel 2, declarados | 36 arreglos, 1.345 pruebas, el arnés solo difiere en lo declarado | ✅ en la rama `claude/f3-arreglos` (sin PR). **Sin medición real** |

Conclusión de la sección: **las mejoras sí se implementaron** y el método (línea base + huella + declaración) funciona: lo
que cambió, cambió a propósito. **Lo que no se ha hecho es demostrar que el motor acierta en empresas reales**, y ese es el único
juicio que importa para «talla mundial».

---

## 3. Diagnóstico: los hallazgos que cambian el rumbo

### H1 · No hay verdad real (gravedad: máxima)

Todo F1, recall y precisión del repositorio sale de `benchmark_institucional.csv.gz` y de `ground_truth_grande.csv`, ambos
**sintéticos** (generados con `scripts/generar_ground_truth_grande.py`, semilla 42). El propio README lo admite: «sin ground
truth real no se pueden certificar precision/recall/F1». La auditoría externa de septiembre lo marcó como bloqueante B3 y los
scripts para resolverlo existen (`scripts/active_labeling.py`, `scripts/recalibrate_from_labels.py`) pero nunca se usaron.
En la vinculación pasa lo mismo: las decisiones humanas del piloto (EMPRESA / GRUPO / NO_VINCULAR) son etiquetas gratis y
no alimentan ninguna medición ni ningún ajuste.

**Costo de no hacerlo:** cada semana de optimización sobre sintético puede estar optimizando para un ruido que no existe.
El canje de F2 (−10 % de recall sin identificador) se decidió sin saber qué recall real se perdía.

### H2 · El Nightly lleva 20 corridas en rojo y nadie lo sabe (gravedad: alta)

`tests/test_sin_nit_recalibrado.py` exige precisión ≥ 0,87 en el perfil `deduplication_sin_nit_conservador`; da 0,864 (TP 2.548,
FP 400, FN 2.113) desde al menos el 24 de septiembre (0.22.4) hasta hoy, y lo reproduzco aquí con cuatro combinaciones de
versiones de dependencias: **es el código, no el entorno**. El CI diario corre con `-m "not canario and not slow"`, así que
la prueba solo vive en el Nightly y el Nightly no bloquea nada. La prueba cita `docs/DEUDA_SIN_NIT.md`, que no existe.

Además, el CI instala `pip install -e ".[dev]"` **sin** `constraints/runtime-validado.txt`: el archivo de pines existe pero
no protege ninguna corrida.

### H3 · El bloqueo es la palanca grande, y está mal apuntada (gravedad: alta)

| Escala | Candidatos | Válidos | Desperdicio | L2 + L3 del total |
|---|---|---|---|---|
| 30k (banco) | 968.673 | 41.737 | 23 : 1 | 76 % |
| 139k | 12.739.949 | 195.368 | 65 : 1 | 90 % |
| 463k | 38.646.798 | 650.992 | 59 : 1 | 94 % |
| Real importadores 355k (CLAUDE.md 0.22.4) | 52.801.652 | — | 148 por registro | partición USA: 24,5 M y **11,4 GiB** de pico |

Y, peor que el costo, el **recall**: el LSH de MinHash sobre trigramas (252 permutaciones, umbral 0,55) recupera el 99,6 %
de los pares con identificador **porque los recupera el bloqueo por NIT**, y solo el **71,8 %** de los pares sin identificador.
El recall final SIN_ID (0,58) está tapado por el bloqueo antes de que el puntaje opine.

Experimento de hoy (mismo banco, misma máquina, implementación ingenua en 60 líneas):

| Método | Candidatos / registro | Recall de pares (SIN_ID) | Tiempo |
|---|---|---|---|
| LSH actual (L2) | 31,8 | 0,866 (**0,718**) | 27,6 s |
| TF-IDF trigramas, coseno ≥ 0,4, top-50 | 17,5 | 0,904 (**0,943**) | 12,2 s |
| TF-IDF trigramas, coseno ≥ 0,35, top-60 | 23,9 | 0,919 (**0,957**) | 14,4 s |

Un bloqueo híbrido (NIT exacto y por raíz + TF-IDF top-k por nombre + llaves declaradas) daría ≈ 0,996 CON_ID y ≈ 0,95
SIN_ID con menos candidatos. Es la práctica estándar en los motores de referencia (reglas de bloqueo compuestas, no un solo
índice: Papadakis et al., *A Survey of Blocking and Filtering Techniques for Entity Resolution*,
https://arxiv.org/abs/1905.06167; y la librería `sparse_dot_topn` de ING para el top-n disperso en C++,
https://github.com/ing-bank/sparse_dot_topn).

El plan vigente (F4.1–F4.4) propone acelerar el LSH y su SQLite. Eso es optimizar el camino equivocado: el `OR` de la
paginación (F4.1) sigue en `scorer.py:1903` y el bucle por par (F4.3) en `scorer.py:1955`, pero resolverlos deja intactos los
65 candidatos por válido.

### H4 · El puntaje no es probabilístico ni está calibrado (gravedad: alta)

`produccion_estandar`: `weights = {name: 0.5, nit: 0.5, phonetic: 0}`, `score_threshold = 0.45`, `idf_weight_blend = 0.0`.
La similitud de nombre es `token_set_ratio` (rapidfuzz) con el peso IDF **apagado**, aunque `matching/idf.py` y
`matching/nombre_idf.py` existen y la vinculación sí los usa. No hay modelo de Fellegi–Sunter, ni EM, ni regresión logística
calibrada, ni probabilidad por par: el umbral 0,45 es un número elegido por barrido sobre sintético (Optuna, notebook 12).

Lo que hace un motor de referencia: Fellegi & Sunter (1969), *A Theory for Record Linkage*, JASA,
https://doi.org/10.1080/01621459.1969.10501049; implementado con EM y comparaciones por columna en Splink (Ministerio de
Justicia del Reino Unido, https://github.com/moj-analytical-services/splink, documentación
https://moj-analytical-services.github.io/splink/). Splink corre sobre DuckDB —que ya es dependencia de este repositorio— y
escala a decenas de millones de registros en una máquina.

Esto importa para la regla de Enrique «unir mal duele más»: con probabilidades calibradas el umbral se fija por **costo**
(cuánto vale un falso positivo frente a un falso negativo), no por un barrido de F1.

### H5 · Dos motores de nombre, cinco caminos, 77 mil líneas (gravedad: media-alta)

- Nombre: `engine/scorer.py` usa `token_set_ratio`; `flujo/vinculacion/comparar.py` usa `SimilitudNombre` (IDF + Jaro-Winkler
  + tres puertas de contención). Dos reglas para la misma pregunta.
- Caminos: `linkage`, `dedupe`, `link`, `flujo.cruce`, `flujo.importadores` (+ `deduplicate_unified`, que es el que falla en
  el Nightly) y ahora `flujo.vinculacion.ejecutar`. F1 los puso bajo un contrato; no los unificó.
- Tamaño: 195 módulos, 77.489 líneas; 8.628 no alcanzables desde la API ni desde `flujo` (`evaluation.orchestrator_hyperparameters`
  1.104, `evaluation.conformidad` 753, `classifier/*` 696 —un clasificador empresa/persona que nada llama—, `deduplication.colab` 435,
  `processing.utilities` 379, `engine.lsh.minhash` 351, `engine.lsh.prescreen` 299…). Algunos los usan scripts y notebooks; ninguno
  el motor.
- `orchestrator.py` 2.373 líneas con `_run_L2` y `_run_L2_legacy`; `scorer.py` 2.126 líneas y una función de complejidad 62;
  `disk_based.py` 2.236 líneas (firmas en HDF5 + cubetas en SQLite + pares en SQLite).

### H6 · Colab no está resuelto (gravedad: alta para el objetivo)

- 463k: 33 min, 2,5 GiB; 4 M proyectados: ≈ 9 GiB de RSS (exponente 0,62 medido) con ≈ 12,7 GB disponibles sin garantía.
- Sin presupuesto de memoria *fail-fast* (F4.5), sin reanudación probada (F4.6), salidas a 4 M sin resolver (F4.7).
- La ruta de bajo consumo existe (`motor_ingesta="duckdb"`, `modo_resultado="disco"`, `exportar_excel=False`,
  `liberar_fuentes_tras_l1`) pero **no es el defecto**: el defecto es pandas en memoria y Excel.
- Dependencias pesadas y frágiles: `datasketch<2` (2.0 cambia las firmas y el F1 sin NIT cae de 0,563 a 0,250, medido),
  `h5py`, `networkx`, `scikit-learn`, `scipy`. Cada una es un punto donde una actualización de Colab rompe la calidad en silencio.

### H7 · La vinculación: buena ingeniería, escala no medida (gravedad: media)

- `_pares_por_cadena` hace `rapidfuzz.cdist` **de todas las superficies contra todas** (matriz `|A|×|B|` en float32): 20.000 grafías
  contra 50.000 registros son 4 GB. En el piloto cupo; en una base nacional no cabe.
- `propagar_por_web` puntúa fila a fila (145 s para 17.356 filas, C13) y `tabla_exportadores` filtra O(empresas × pares).
- `componentes_restringidas` (mío, F3.1b) es correcto en lo que promete; tiene un borde conservador que hay que documentar:
  si una persona unió dos formas legales en una empresa, ninguna unión **automática** posterior entra en esa empresa (va a pendientes).
- 36 arreglos sin medición real; C9, M-c y B7 esperan datos.

### H8 · Pruebas: muchas, lentas, y las que importan no bloquean (gravedad: media)

3.869 pruebas, 29–35 min por versión de Python en CI, > 25 min aquí; 11 archivos `slow`, excluidos del CI; `fail_under = 60`.
La suite protege la **forma** (contrato, esquema, paridad) mucho más que el **fondo** (calidad en datos reales).

### H9 · Deuda que frena (gravedad: media)

101 `except` que no relanzan (errores que se tragan), 152 `print` en una librería, 117 `os.path`, 80 errores de mypy fuera
del anillo, 11 funciones con complejidad ≥ 20. El trinquete impide que suba; nada la baja.

---

## 4. Qué significa «talla mundial» aquí, en cifras

Objetivos medibles (los números de calidad son propuestas hasta que exista la verdad real de la Fase 1; los de velocidad son firmes):

| Dimensión | Hoy | Objetivo |
|---|---|---|
| Recall de pares del bloqueo, sin identificador (banco) | 0,718 | ≥ 0,95 con ≤ 20 candidatos por registro |
| Recall final sin identificador (banco) | 0,584 | ≥ 0,80 sin subir los FP sobre negativos (246) |
| macro-F1 (banco) | 0,880 | ≥ 0,92 |
| Precisión en verdad **real** (CON_ID / SIN_ID) | no existe | ≥ 0,97 / ≥ 0,90 en lo automático; lo demás a revisión con probabilidad |
| 139k filas | 333 s / 1,15 GiB | ≤ 60 s / ≤ 1 GiB |
| 463k filas | 1.977 s / 2,5 GiB | ≤ 4 min / ≤ 2 GiB |
| 4 M filas en Colab gratuito | no cabe (≈ 9 GiB proyectados) | ≤ 2 h, ≤ 8 GiB, reanudable tras desconexión |
| Suite rápida | 30 min | ≤ 10 min (`pytest-xdist`); punta a punta en Nightly **verde** |
| Código alcanzable desde la API | 68.861 de 77.489 líneas | ≤ 45.000 líneas totales; 0 módulos muertos |
| Reglas de negocio escritas dos veces | nombre, decisiones, catálogos | 0 |
| Dependencias que cambian la calidad al actualizarse | datasketch, rapidfuzz (sin pines en CI) | pines validados en CI y Nightly con rangos; el banco es la compuerta de cada bump |

---

## 5. Ruta de trabajo

Principios que no cambian: una cosa a la vez · prueba primero · todo cambio de comportamiento se declara (ADR + huella) · una regla
se escribe una vez · español · sin nombres reales en el repositorio · nunca a `main` sin PR · el banco y la escala son la compuerta
de **cada** fase.

Duraciones: una persona con un agente, jornada completa. Son estimaciones, no compromisos.

### Fase 0 · Cerrar lo abierto y poner en verde la vigilancia (1 semana)

**Objetivo:** que ninguna señal esté en rojo o apagada antes de tocar el motor.

| # | Paso | Prueba / compuerta |
|---|---|---|
| 0.1 | PR de F3.1b (`claude/f3-arreglos` → `main`); borrar las 7 ramas viejas desde GitHub | CI verde; Enrique mezcla |
| 0.2 | Decidir la regresión SIN_NIT: re-medir el perfil `deduplication_sin_nit_conservador` sobre `ground_truth_grande.csv`, escribir el `docs/DEUDA_SIN_NIT.md` que falta y **o** corregir **o** bajar la cota con ADR-0014 (declarado, no escondido) | Nightly verde; la prueba cita un documento que existe |
| 0.3 | Instalar en CI y en el Nightly «pinado» con `-c constraints/runtime-validado.txt`; un segundo job Nightly con rangos libres que **avisa** (no bloquea) | dos jobs; el pinado es compuerta |
| 0.4 | Partir la suite: `pytest -n auto -m "not slow"` en CI (≤ 10 min), `slow` en Nightly; `--durations=30` publicado | CI ≤ 12 min de pared |
| 0.5 | Borrar lo no alcanzable que nadie usa (lista de la sección 3-H5, cotejada con scripts y notebooks); lo que usan scripts pasa a `scripts/` | −5.000 líneas; importabilidad de submódulos verde; banco y escala con huella idéntica |

### Fase 1 · Verdad real e instrumentos (2 semanas)

**Objetivo:** medir contra empresas reales antes de cambiar el algoritmo. Sin esto, todo lo demás es fe.

| # | Paso | Prueba / compuerta |
|---|---|---|
| 1.1 | Muestra estratificada de pares del RUES real (`scripts/muestrear_rues_para_gt.py`, `scripts/active_labeling.py`): 3.000–5.000 pares, estratos CON_ID/SIN_ID × banda de puntaje × fuente | archivo de etiquetas **fuera del repositorio**; en el repo solo huellas y conteos |
| 1.2 | Dos anotadores, guía de etiquetado de una página, kappa de Cohen ≥ 0,8 (McHugh 2012, https://doi.org/10.11613/BM.2012.031); desacuerdos los decide Enrique | `scripts/medir_kappa.py` |
| 1.3 | `evaluation/verdad_real.py`: precisión y recall de pares por régimen y por banda sobre esas etiquetas; el banco sintético queda como prueba de **regresión**, no de verdad | reporte reproducible con huella |
| 1.4 | Vinculación: convertir las decisiones humanas del piloto (hojas `DECISIONES` de los Excel entregados) en referencia; `validar_contra_referencia` ya existe | precisión/recall de A, B, C del motor nuevo frente a esa referencia |
| 1.5 | Banco de velocidad con presupuesto: 139k, 463k, 1 M sintéticos; medir RSS por fase (F4.2) y publicar como JSON | `scripts/escala.py --comparar` falla si algo empeora > 10 % |
| 1.6 | Fijar las compuertas numéricas de la sección 4 en `docs/evidencia/compuertas.json` y en una prueba | la prueba lee el JSON |

### Fase 2 · Nuevo generador de candidatos (2–3 semanas)

**Objetivo:** recall de pares ≥ 0,95 sin identificador con ≤ 20 candidatos por registro, en segundos, sin HDF5 ni SQLite de cubetas.

| # | Paso | Prueba / compuerta |
|---|---|---|
| 2.1 | `engine/candidatos/` nuevo, con una interfaz única `generar(df, config) -> tabla Arrow (i, j, origen)`; cada estrategia es una función pura: `por_identificador` (exacto y raíz), `por_nombre_tfidf` (trigramas, coseno, top-k, `sparse_dot_topn`), `por_llaves` (lo que hoy es `llaves_extra`), `por_telefono_email` | pruebas unitarias por estrategia con pares inventados; PC/RR medidos por estrategia (ya existe `motor_bloqueo` con PC/RR: reutilizarlo) |
| 2.2 | Unión y deduplicación de pares en DuckDB (no en Python): `UNION` + `DISTINCT` + política de fuentes como `JOIN` | 1 M de filas sin pasar por pandas |
| 2.3 | Bandera `motor_candidatos = "lsh" | "hibrido"`; `hibrido` debe igualar o superar el recall de pares de `lsh` por régimen en el banco y en la verdad real | compuerta: recall de pares CON_ID ≥ 0,99, SIN_ID ≥ 0,95; candidatos ≤ 20/registro; L2 ≤ 10 s a 139k |
| 2.4 | Partición por país y por régimen de identificador como opción explícita (la base real de importadores ya lo necesitó) | 1 M en ≤ 8 GiB |
| 2.5 | Retirar `lsh` (y `datasketch`, `h5py`) cuando `hibrido` pase las compuertas dos semanas seguidas; ADR | −3.000 líneas; una dependencia frágil menos |

### Fase 3 · Puntaje probabilístico, calibrado y único (2–3 semanas)

**Objetivo:** una probabilidad por par, un solo comparador de nombre, umbrales por costo.

| # | Paso | Prueba / compuerta |
|---|---|---|
| 3.1 | Un solo comparador de nombre para toda la librería: `SimilitudNombre` (IDF + Jaro-Winkler + contención, el de la vinculación) sustituye a `token_set_ratio` en L3; ADR | huella del banco cambia y se declara; verdad real no empeora |
| 3.2 | Comparaciones vectorizadas por campo (nombre, identificador con dígito de verificación, ciudad, teléfono, correo, web) que devuelven **niveles** discretos (p. ej. exacto / casi / parcial / distinto / sin dato), estilo Splink | pruebas por campo |
| 3.3 | Modelo Fellegi–Sunter con EM sobre los niveles (sin etiquetas) **y** regresión logística calibrada con la verdad real de la Fase 1; se elige por la verdad real, no por sintético | curva precisión-recall por régimen publicada; Brier/calibración |
| 3.4 | Umbrales por costo: `costo_fp`, `costo_fn` en la configuración; «unir mal duele más» se vuelve un número declarado | ADR; prueba de que cambiar el costo mueve el umbral |
| 3.5 | L3 en DuckDB/Arrow por lotes; retirar la paginación `OR` (F4.1) y el bucle por par (F4.3) de paso | L3 ≤ 15 s a 139k |
| 3.6 | Las bandas A/B/C de la vinculación se derivan de la probabilidad (no de reglas sueltas), conservando la explicación textual (`MOTIVO`) | paridad de `MOTIVO` donde no cambia; cambios declarados |

### Fase 4 · Escala en Colab gratuito (2 semanas)

**Objetivo:** 4 M de registros en ≤ 2 h, ≤ 8 GiB, reanudable.

| # | Paso | Prueba / compuerta |
|---|---|---|
| 4.1 | Ruta de bajo consumo como **defecto**: DuckDB de principio a fin, Arrow entre fases, pandas solo para muestras y Excel; Excel solo si cabe (F4.7) | 463k sin pasar de 2 GiB |
| 4.2 | Presupuesto de memoria *fail-fast* calibrado con 30k/139k/463k/1 M (F4.5): se detiene **antes** de L2 con un mensaje que dice qué partir | prueba del estimador (error ≤ 30 %) |
| 4.3 | Puntos de control por fase en disco (Drive en Colab), reanudación con la misma huella; prueba que mata el proceso a mitad de cada fase (F4.6) | huella idéntica tras reanudar |
| 4.4 | 1 M y 4 M sintéticos en esta máquina; luego la base real en Colab (Enrique), evidencia sin nombres | `escala_4M.json` |
| 4.5 | Notebook de Colab delgado y probado con `nbclient` sobre datos sintéticos: instala del tag, configura, corre, guarda en Drive | prueba en Nightly |

### Fase 5 · Vinculación sobre el nuevo núcleo (2–3 semanas) = F3.2 a F3.7 reescritas

| # | Paso | Prueba / compuerta |
|---|---|---|
| 5.1 | Catálogos (genéricos, formas legales, países) y locale a `matching/` (F3.2); la vinculación no define catálogos | prueba «vinculacion no define catálogos» |
| 5.2 | Candidatos de la vinculación por `engine/candidatos` (fuera el `cdist` todos contra todos); `propagar_por_web` por lotes | 50k × 200k en ≤ 2 GiB |
| 5.3 | `vincular_empresas` → `ResultadoLinkage` (F3.3); Excel técnico como vista; la vista VPI espera el formato de Caro | contrato verde |
| 5.4 | Lector único de decisiones (F3.4) y **aprendizaje de las decisiones**: cada archivo de decisiones alimenta la calibración de 3.3 | recall/precisión contra la referencia de 1.4 mejora o se declara por qué no |
| 5.5 | Notebooks 10 y 09 delgados (F3.5/F3.6; sin notebook 11); ADR, `docs/VINCULACION.md`, skill (F3.7); 0.23.0 lo decide Enrique | `nbclient` sin errores; prueba «ningún notebook reimplementa el motor» |

### Fase 6 · Ingeniería y release 1.0 (3–4 semanas) = F5/F6 podadas

| # | Paso | Prueba / compuerta |
|---|---|---|
| 6.1 | Un solo camino: `linkage()` sobre el núcleo nuevo; `dedupe`, `link`, `flujo.cruce`, `flujo.importadores`, `deduplicate_unified` se vuelven envolturas finas o se retiran con aviso de deprecación | contrato verde en lo que quede; −15.000 líneas |
| 6.2 | Partir `orchestrator.py`, `scorer.py`, `cruce.py`, `disk_based.py` en fases puras `ejecutar(entrada, config) -> salida` (F5.1–F5.3); configuración tipada por etapa (F5.4) | ningún archivo > 600 líneas; CC ≤ 15 (la regla que ya cumple la vinculación) |
| 6.3 | Excepciones y logging (F5.7, F5.8): 0 `except` silenciosos, 0 `print` | trinquete en 0 |
| 6.4 | Tipado y cobertura (F5.10, F5.11): mypy 0 en todo `src/`, cobertura ≥ 75 % | CI |
| 6.5 | Documentación y publicación (F6.1–F6.7): contrato, migración, README con 4 «cómo empezar», `release.yml`, pines por versión | `smoke_installed_release.py` en venv limpio |

**Total: 14–16 semanas.** Si hay que cortar, se corta por el final (Fase 6), nunca por el principio (Fases 0–2): la verdad real y el
bloqueo son el 80 % del valor.

---

## 6. Reglas de ingeniería para que corra en Colab (se aplican desde la Fase 2)

1. **Memoria antes que CPU.** Nunca una matriz `n × m` densa (ni `cdist` todos contra todos ni `np.triu_indices` sobre cubetas):
   todo candidato nace disperso, con umbral y top-k, y vive en Arrow/DuckDB.
2. **Tipos estrechos.** `int32` para posiciones, `float32` para similitudes, `string[pyarrow]`/categorías para texto; prohibido
   `object` fuera de la lectura.
3. **Por lotes, siempre.** Cada fase consume y produce tablas por lotes de tamaño fijado por el presupuesto; el presupuesto se
   estima antes de empezar y la corrida se detiene con un mensaje accionable si no cabe.
4. **Reanudable.** Cada fase escribe un punto de control atómico con huella; la siguiente corrida continúa desde el último.
   En Colab la desconexión es la norma, no la excepción.
5. **Determinista.** Semillas fijas, orden estable (`kind="stable"`), huella por fase; dos corridas, misma huella.
6. **Rápido en C, no en Python.** `rapidfuzz.process.cdist` con `score_cutoff` y `workers`, `sparse_dot_topn`, DuckDB; nada de
   `iterrows`/`itertuples` en rutas calientes (hoy hay 20 en la vinculación).
7. **Pines validados.** `constraints/runtime-validado.txt` en CI y en el notebook de instalación; el banco es la compuerta de
   cada actualización.
8. **Observable.** Progreso y recursos por fase en el manifiesto; nunca `print`.
9. **Probado en el tamaño real.** La escala (139k/463k/1 M/4 M) es una compuerta de CI nocturna, no un experimento.

Referencias de método: Christen, *Data Matching* (Springer, 2012), https://doi.org/10.1007/978-3-642-31164-2 ·
Leskovec, Rajaraman y Ullman, *Mining of Massive Datasets*, cap. 3 (LSH), http://www.mmds.org/ · RapidFuzz,
https://github.com/rapidfuzz/RapidFuzz · DuckDB, https://duckdb.org/ · dedupe (aprendizaje activo),
https://github.com/dedupeio/dedupe · Konda et al., *Magellan: Toward Building Entity Matching Management Systems*, VLDB 2016,
https://www.vldb.org/pvldb/vol9/p1197-pkonda.pdf · Li et al., *Deep Entity Matching with Pre-Trained Language Models* (Ditto),
https://arxiv.org/abs/2004.00584 (para cuando exista verdad real suficiente; no antes) · pytest-xdist,
https://github.com/pytest-dev/pytest-xdist.

---

## 7. Decisiones que son de Enrique

1. **Verdad real:** quién etiqueta (dos personas), cuándo, y dónde viven las etiquetas (fuera del repositorio). Sin esto la ruta no arranca.
2. **El canje precisión/recall en números:** `costo_fp` y `costo_fn`. «Unir mal duele más» ¿cuánto más? ¿2×, 5×, 10×?
3. **La regresión SIN_NIT (0.2):** corregir o declarar.
4. **Retirar el LSH y `datasketch`** cuando el híbrido pase las compuertas (Fase 2.5): es irreversible en comportamiento y cambia huellas.
5. **Un solo camino (6.1):** qué entradas públicas sobreviven en 1.0.
6. **Los insumos del piloto de vinculación** y las matrices VPI, para la Fase 5.

---

## 8. Dónde me puedo estar equivocando

- El experimento de bloqueo es sobre **sintético** y mide recall de pares, no recall final; el verdadero juez será la verdad real
  de la Fase 1. Lo que sí es independiente del dato: el LSH gasta 23–65 candidatos por válido y pierde SIN_ID en el bloqueo.
- Las duraciones son estimaciones mías; el historial del proyecto (F3.1 costó ≈ 3,2 M de tokens de agentes) dice que
  la verificación cuesta más que la escritura.
- Las cifras de Colab no las pude confirmar desde este entorno.
- No revisé con el mismo detalle `reporting/` (8.376 líneas) ni `golden/` (3.506): asumo que entran en 6.1/6.2.

**Nivel de confianza:** alto en que lo avanzado se reproduce y en el diagnóstico de bloqueo y puntaje (medido hoy); medio en los
objetivos numéricos de calidad (dependen de una verdad real que no existe) y en los plazos.
