# ADR-0011 — Cobertura por estrellas en `linkage()` para los grupos sin identificador

- **Estado:** aceptada
- **Fecha:** 2026-10-06
- **Versión:** F2.1 (rues-linker v2, sobre 0.22.4 + F1)

## Contexto

`clusters_desde_decisiones` y el clusterer de L4 agrupan por componentes
conexas, que es single-linkage: si `a≈b` y `b≈c`, `a` y `c` quedan en el mismo
grupo aunque no se parezcan. Con identificador válido eso casi no importa: el
veto del scorer y el cannot-link de L5 (v0.14.0) cortan los puentes. Sin
identificador no hay quien los corte, y un prefijo genérico encadena empresas
distintas. ADR-0009 resolvió este modo de falla para el camino de importadores
(`flujo.importadores`, solo nombre y país) con `engine.cobertura`; el camino de
producción —`linkage()`, L1…L6— seguía sin esa protección.

### Lo que se midió antes de decidir (prototipo fuera del repositorio, 5–6 de octubre)

- Banco institucional (30.486 filas): sin estrellas P 0,9385 · R 0,8249 ·
  F1 0,8780 · macro-F1 0,8920 · 287 FP que tocan un negativo.
- Sintético de 139.028 filas (`scripts/escala.py`, semilla 42): sin estrellas
  P 0,3387 · R 0,9647 · F1 0,5014, y el grupo mayor **sin identificador tenía
  630 registros de 198 entidades distintas** (12 grupos ≥ 50). A 0,80 el grupo
  mayor baja a 13 registros / 2 entidades y la precisión sube a 0,98.

## Criterios de decisión

- Unir dos empresas distintas duele más que dejar sin unir (regla de Enrique).
- Una regla se escribe una vez: la cobertura ya existe (`cobertura_estrella`)
  y el comparador también (`SimilitudNombre`); se reutilizan, no se copian.
- Un cambio de comportamiento se declara y se mide con la huella del banco
  antes y después; la perilla debe poder apagarse y reproducir la huella de
  0.22.4 exactamente.
- Los grupos con identificador válido no se tocan: allí el identificador ya
  decidió y la cobertura no tiene nada que opinar.

## Decisión

1. **Un único punto de aplicación.** `engine.cobertura.aplicar_cobertura_sin_identificador`
   recibe la correlativa clusterizada de L5 y reparte en estrellas SOLO los
   grupos en los que ninguna fila trae identificador válido (misma definición
   que el cannot-link: `engine.cannot_link.identificadores_validos`, extraída
   para que las dos reglas nunca discrepen) y que tienen al menos dos nombres
   distintos. Compara nombres distintos, no filas (las repeticiones pesan como
   `masa`), así que el costo es O(k²) en nombres distintos por grupo.
2. **El comparador es el del corpus.** `matching.nombre_idf.comparador_desde_corpus`
   construye el `SimilitudNombre` —vocabulario ordenado con clave total, IDF
   de `NOMBRE_LIMPIO` sobre los nombres distintos de TODA la tabla,
   `matching.genericos.genericos()` neutralizados y `GENERICOS_ESTRUCTURALES`
   como ruido—. `flujo.importadores` pasa a llamar a la misma función (su
   huella no cambia: `tests/test_flujo_importadores_v022.py` y `v0223` en
   verde). La geografía NO es ruido en `linkage()`: en un padrón con
   identificador «X USA» y «X CANADA» son dos sociedades (ADR-0009).
3. **Dónde.** `Orchestrator._run_L5`, justo después de
   `aplicar_cannot_link_identificador` y antes del golden. El reporte
   (`ReporteCoberturaSinIdentificador`) va al manifiesto en
   `L5_golden.meta.cobertura_sin_identificador` y al log.
4. **Perilla de perfil** `cobertura_sin_identificador: {activa, umbral, regla_lider}`,
   interpretada una sola vez por `ConfigCoberturaSinIdentificador.desde_perfil`:
   ausente → inactiva (paridad), `False`/`True` como atajo, diccionario parcial
   (lo que falta toma el valor por defecto), clave desconocida → `ValueError`
   accionable. `similitud_minima = 2·umbral − 1` es la escala firmada que
   recibe `cobertura_estrella`. `scripts/banco.py --ajuste` acepta claves con
   punto (`cobertura_sin_identificador.umbral=0.70`) para medir el barrido.
5. **Valor por defecto.** `produccion_estandar` la trae **activa con umbral
   0,80 y `regla_lider: "cobertura"`**: es el cambio declarado de esta tarea.
   El 0,80 no se adivinó: lo elige la regla de la sección «Umbral medido»
   (F2.2) sobre el barrido 0,70–0,88 medido con los dos instrumentos,
   registrado en `docs/evidencia/cobertura_umbral.json`.
   Los otros siete perfiles del Orchestrator la declaran apagada (no están
   medidos). Las etiquetas nuevas se numeran desde `max(ID_GRUPO) + 1`; la
   estrella con más filas conserva la etiqueta original.

## Consecuencias

### Medido en el banco (`benchmark_institucional`, 30.486 filas, misma máquina)

| corrida | P | R | F1 | macro-F1 | FP sobre negativos | recall SIN_ID | huella |
|---|---|---|---|---|---|---|---|
| `activa: False` (paridad) | 0,9385 | 0,8249 | 0,8780 | 0,8920 | 287 | 0,6523 | `1e365ba8…` (= base_f0) |
| umbral 0,70 | 0,9469 | 0,8224 | 0,8803 | 0,8935 | 277 | 0,6471 | `1f0f8d10…` |
| umbral 0,75 | 0,9513 | 0,8142 | 0,8774 | 0,8906 | 264 | 0,6297 | `43ebf746…` |
| **umbral 0,80 (defecto)** | **0,9557** | **0,7927** | **0,8666** | **0,8803** | **246** | **0,5836** | `5bfed0d1…` |

Con 0,80 la cobertura examina 947 grupos sin identificador, parte 282 en 345
grupos nuevos y reasigna 410 registros; el estrato RUIDO pierde recall (0,6472
→ 0,5889) y el estrato CONTACTO gana precisión (0,9760 → 0,9952). L5 pasa de
4,7 s a 8,3 s (el comparador del corpus más las matrices por grupo) y el total
de 47,9 s a 53,9 s. Las dos corridas se repitieron en una sesión distinta y
dieron huella y cifras idénticas (`scripts/banco.py --etiqueta f2_1_antes
--ajuste cobertura_sin_identificador.activa=false` → `1e365ba8…`, PASA frente
a `base_f0`; `--etiqueta f2_1` → `5bfed0d1…`). `--comparar base_f0 f2_1` dice
FALLA porque su criterio es «recall y F1 ≥ base»: es la pérdida declarada; la
compuerta de F2 (macro-F1 ≥ 0,880 y FP sobre negativos ≤ 287) se cumple.

### Medido en el sintético de 139.028 filas (umbral 0,80, `scripts/banco.py` sobre el CSV de `escala.py`)

| | sin estrellas (F1, prototipo) | con estrellas 0,80 |
|---|---|---|
| precisión por pares | 0,3387 | **0,9856** |
| recall por pares | 0,9647 | 0,9475 |
| F1 por pares | 0,5014 | **0,9662** |
| recall SIN_NIT | — | 0,7145 |
| FP que tocan un negativo | — | 34 |
| grupo mayor sin identificador | 630 registros / 198 entidades (12 grupos ≥ 50) | **13 registros / 2 entidades (0 grupos ≥ 50)** |

La cobertura examinó 2.059 grupos sin identificador, partió 1.038 en 2.642
grupos nuevos y reasignó 5.419 registros; L5 costó 24,7–32,4 s (18,0 s en la
línea base de escala) y el total 392–413 s (415–438 s en las corridas de
F0/F1). Huella `d904ea16…`, reproducida en dos sesiones.

Con **umbral 0,75** a 139k (misma máquina, `--ajuste
cobertura_sin_identificador.umbral=0.75`): precisión 0,9191 · recall 0,9478 ·
F1 0,9332 · FP que tocan un negativo **92** (34 con 0,80) · recall SIN_NIT
0,7163 · grupo mayor sin identificador **33 filas** (628 grupos partidos en
1.464 nuevos, 3.990 registros reasignados). Cumple la compuerta ≤ 50, pero
triplica los FP sobre negativos y deja la precisión por pares en 0,92 frente
a 0,99: el recall que 0,75 recupera a 30k (+0,046 en SIN_ID) se paga en
fusiones de empresas distintas a 139k.

### Lo que la compuerta exige y lo que se obtiene

- FP sobre negativos ≤ 287: **246** ✔ (−14 %).
- macro-F1 ≥ 0,880: **0,8803** ✔ (en el límite; −0,012 respecto a la base).
- conformidad 0 fallas: **PASA** (dedup y linkage, 43 casos).
- recall sin NIT (régimen `SIN_ID`) ≥ 0,62: **0,5836 ✘ con 0,80**; 0,6297 con
  0,75 y 0,6471 con 0,70. **Es la única cifra que el valor por defecto no
  cumple.** No se forzó: el plan fija 0,80 como defecto propuesto, Enrique
  prefiere perder recall a fusionar empresas distintas, y a 139k el 0,70 deja
  un grupo mayor de 100 registros / 52 entidades (falla la compuerta ≤ 50)
  mientras el 0,80 lo deja en 13. Queda declarado aquí y en el docstring de
  `ConfigCoberturaSinIdentificador`. Medido después a 139k, 0,75 cumple el
  grupo mayor ≤ 50 (33) pero con precisión 0,92 y 92 FP sobre negativos
  frente a 0,99 y 34 con 0,80: la regla de Enrique inclina el empate hacia
  0,80. La decisión final, con el barrido completo y la regla escrita, está
  en la sección «Umbral medido, no adivinado (F2.2)».

### Lo que cambia para quien consume resultados

- Una corrida de `produccion_estandar` produce más grupos sin identificador y
  más pequeños; `ID_GRUPO` de esos registros cambia respecto a 0.22.4. Con
  `ajustes_perfil={"cobertura_sin_identificador": {"activa": False}}` se
  recupera la partición anterior, huella incluida.
- `manifest.json` gana la clave `L5_golden.meta.cobertura_sin_identificador`
  (solo cuando la perilla está activa). El fixture del esquema de salida
  (`tests/contratos/esquema_salida_v0.json`) se regeneró con
  `ACTUALIZAR_ESQUEMA_V0=1` y el diff es exactamente esa clave: ni una
  columna ni un archivo ni el número de filas cambian en ese dataset.
- La línea base congelada (`tests/lineas_base.py::BANCO_F0`,
  `docs/evidencia/corrida_base_f0.json`) deja de describir el tronco: la mueve
  el coordinador con la corrida nueva, como manda el docstring de esa prueba.

### Lo que no se resolvió

- Una fila con `NOMBRE_LIMPIO` vacío dentro de un grupo examinado queda en
  estrella propia (el comparador la puntúa 0): es el lado conservador y está
  contado en el reporte, pero no se midió cuántas hay en producción.
- El comparador se construye sobre `NOMBRE_LIMPIO`, que poda palabras
  frecuentes y deja residuos («S S», ADR-0010). Medir la cobertura sobre la
  huella del selector (`golden.selector.huella_de_nombre`) queda para F2.2 o
  F5, con banco.

## Umbral medido, no adivinado (F2.2)

- **Fecha:** 2026-10-07 · **Tabla de decisión:** `docs/evidencia/cobertura_umbral.json`
  (la compone `scripts/cobertura_umbral.py`; `tests/test_cobertura_umbral_f22.py`
  reaplica la regla sobre la tabla y exige que el elegido sea el del perfil).

### Cómo se midió

Barrido 0,70 / 0,75 / 0,80 / 0,84 / 0,88 (más la perilla apagada como
referencia) con los dos instrumentos, sin tocar el motor de F2.1:

```bash
E=/tmp/barrido_umbral   # fuera de docs/evidencia/: ahí solo entra la tabla
python scripts/banco.py  --etiqueta u080 --evidencia $E --datos data/benchmark/benchmark_institucional.csv.gz \
    --ajuste cobertura_sin_identificador.umbral=0.80          # ≈ 55 s por umbral
python scripts/escala.py --etiqueta u080 --evidencia $E --tamanos 139k \
    --ajuste cobertura_sin_identificador.umbral=0.80          # ≈ 400 s por umbral
python scripts/cobertura_umbral.py --evidencia $E --sin-estrellas apagada
```

Para que la compuerta «grupo mayor sin identificador ≤ 50» se lea del JSON y
no de un parquet, `evaluar_calidad` reporta desde F2.2
`grupo_mayor_sin_identificador` (filas y entidades verdaderas del grupo
predicho mayor cuyas filas son todas `SIN_*`, grupos sin identificador y
cuántos llegan a 50 filas); `scripts/escala.py` acepta `--ajuste` y
`scripts/banco.py` acepta `--evidencia`, y los dos comparten
`evaluation.banco.parsear_ajustes`. Las huellas reproducen las de arriba
(apagada `1e365ba8`, 0,70 `1f0f8d10`, 0,75 `43ebf746`, 0,80 `5bfed0d1`; a 139k
0,80 `d904ea16`). Las cinco corridas de 139k se ejecutaron de dos y tres en
paralelo en 4 vCPU: sus tiempos (396–401 s) no son comparables con
`escala_f1.json`; la calidad y las huellas no dependen de ello.

### Lo medido

Banco institucional (30.486 filas; FP-neg = FP que tocan un negativo):

| umbral | P | R | F1 | macro-F1 | FP-neg | recall SIN_ID | F1 RUIDO | F1 CONTACTO | F1 REAL | huella |
|---|---|---|---|---|---|---|---|---|---|---|
| apagada | 0,9385 | 0,8249 | 0,8780 | 0,8920 | 287 | 0,6523 | 0,7454 | 0,9659 | 0,9648 | `1e365ba8` |
| 0,70 | 0,9469 | 0,8224 | 0,8803 | 0,8935 | 277 | 0,6471 | 0,7478 | 0,9680 | 0,9648 | `1f0f8d10` |
| 0,75 | 0,9513 | 0,8142 | 0,8774 | 0,8906 | 264 | 0,6297 | 0,7382 | 0,9688 | 0,9648 | `43ebf746` |
| **0,80** | **0,9557** | **0,7927** | **0,8666** | **0,8803** | **246** | **0,5836** | 0,7097 | 0,9665 | 0,9648 | `5bfed0d1` |
| 0,84 | 0,9577 | 0,7645 | 0,8503 | 0,8647 | 180 | 0,5235 | 0,6677 | 0,9617 | 0,9648 | `787e42d7` |
| 0,88 | 0,9569 | 0,7363 | 0,8322 | 0,8471 | 160 | 0,4633 | 0,6213 | 0,9553 | 0,9648 | `fd3e5bd1` |

Sintético de 139.028 filas (`scripts/escala.py --tamanos 139k`):

| umbral | P | R | F1 | FP-neg | recall SIN_NIT | grupo mayor sin id (filas / entidades) | grupos ≥ 50 | huella |
|---|---|---|---|---|---|---|---|---|
| 0,70 | 0,7990 | 0,9507 | 0,8683 | 94 | 0,7365 | **100 / 52** | 8 | `09d9527d` |
| 0,75 | 0,9191 | 0,9478 | 0,9332 | 92 | 0,7163 | 33 / 20 | 0 | `0b12d8e7` |
| **0,80** | **0,9856** | **0,9475** | **0,9662** | **34** | **0,7145** | **13 / 3** | **0** | `d904ea16` |
| 0,84 | 0,9971 | 0,9402 | 0,9678 | 5 | 0,6637 | 11 / 2 | 0 | `a5a48394` |
| 0,88 | 0,9994 | 0,9327 | 0,9649 | 0 | 0,6117 | 10 / 2 | 0 | `c6226dd1` |

(El «13 / 2» de la sección anterior era uno de tres grupos de 13 filas; la
métrica nueva toma, a igual tamaño, el que mezcla más entidades: 13 / 3.) En
el banco de 30.486 el grupo mayor sin identificador nunca pasa de 20 filas
(apagada: 20 / 4; 0,80: 16 / 1): el encadenamiento que la cobertura corta
solo aparece a escala, y por eso el barrido necesita los dos instrumentos.

### La regla y lo que dice

`evaluation.cobertura_umbral.elegir_umbral`:

1. **Compuerta de F2** (las cifras del plan): macro-F1 ≥ 0,880 y FP-neg ≤ 287
   en el banco; grupo mayor sin identificador ≤ 50 filas a 139k.
   Descartados: **0,70** (grupo mayor 100 filas / 52 entidades, 8 grupos ≥ 50),
   **0,84** (macro-F1 0,8647) y **0,88** (macro-F1 0,8471). Pasan 0,75 y 0,80.
2. **Entre los que pasan, gana el que menos une mal** (regla de Enrique:
   unir dos empresas distintas duele más que dejar sin unir): menos FP-neg en
   el banco; a igual cifra, mayor precisión en el banco, luego a 139k, luego el
   umbral más alto. **Elegido: 0,80** (246 FP-neg y P 0,9557 en el banco; 34
   FP-neg y P 0,9856 a 139k) frente a 0,75 (264 / 0,9513; 92 / 0,9191).

La macro-F1 es el piso, no el objetivo: maximizarla entre los que pasan
habría elegido **0,75** (0,8906 frente a 0,8803, +0,046 de recall SIN_ID en el
banco) a cambio de 18 FP-neg más en el banco y 58 más a 139k (92 frente a 34,
con el grupo mayor en 33 filas / 20 entidades frente a 13 / 3). La tabla deja
esa alternativa escrita (`decision.alternativa_mayor_macro_f1`) para que la
decisión se pueda revertir con las mismas cifras si Enrique pesa distinto el
recall. Lo que 0,80 no cumple sigue declarado: recall SIN_ID 0,5836 < 0,62.

### Consecuencias de F2.2

- El perfil no cambia (0,80 ya era el defecto de F2.1); la huella del banco
  por defecto sigue siendo `5bfed0d1`.
- `MetricasCalidad` gana `grupo_mayor_sin_identificador` (aditivo: las
  corridas anteriores se siguen cargando con el campo vacío) y el resumen del
  banco lo imprime. `escala_<etiqueta>.json` gana `ajustes_perfil`.
- Mover el umbral exige rehacer la tabla (`scripts/cobertura_umbral.py`) y que
  `tests/test_cobertura_umbral_f22.py` siga en verde: el perfil y la tabla no
  pueden discrepar.

## Referencias

- ADR-0009 (cobertura por estrellas en importadores; genéricos declarados).
- ADR-0004 (identificador en forma canónica; `identificadores_validos`).
- `tests/test_cobertura_sin_identificador_f21.py`.
- F2.2: `docs/evidencia/cobertura_umbral.json`, `scripts/cobertura_umbral.py`,
  `src/record_linkage/evaluation/cobertura_umbral.py`,
  `tests/test_cobertura_umbral_f22.py`.
