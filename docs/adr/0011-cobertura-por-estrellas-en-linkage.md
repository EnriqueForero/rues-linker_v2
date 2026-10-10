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
  0,80. **F2.2** barre el umbral por estrato (macro-F1) y toma la decisión
  final.

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

## Referencias

- ADR-0009 (cobertura por estrellas en importadores; genéricos declarados).
- ADR-0004 (identificador en forma canónica; `identificadores_validos`).
- `tests/test_cobertura_sin_identificador_f21.py`.
