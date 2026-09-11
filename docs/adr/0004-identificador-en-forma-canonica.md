# ADR-0004 — El dígito de verificación no crea un identificador distinto

- **Estado:** aceptado
- **Fecha:** 2026-08-29
- **Versión:** 0.19.0
- **Reemplaza a:** nada. Complementa a ADR-0002 (registro de comparadores).

## Contexto

En Colombia el NIT circula en dos formas: `890903436` y `8909034362`. La
segunda es la primera más su dígito de verificación, calculado por módulo 11
con los pesos que publica la DIAN. Es el mismo número.

Hasta 0.18.0 la librería los trataba como identificadores distintos, en
**tres** lugares independientes:

1. `VectorizedScorer._calculate_nit_distances_vectorized` — distancia de
   Levenshtein sobre `NIT_OK`, que da 1 o 2 en vez de 0. El par pierde la
   exención del filtro de nombre y el boost por identificador idéntico.
2. `VectorizedScorer._veto_nit_base_distinto` — veta el par por
   "identificadores válidos distintos".
3. `engine.cannot_link.aplicar_cannot_link_identificador` — **parte el grupo
   ya formado**, deshaciendo en L5 lo que L4 acababa de unir correctamente.

El caso concreto que lo destapó:

```
REAL-000009-CRM   NIT 102829482   FRESH PISCINAS         NIT_OK 1028294826
REAL-000009-RUES  NIT 10282948    SANCHEZ LOPEZ FREDY    NIT_OK 10282948
```

Un establecimiento de comercio y su dueño, persona natural comerciante. Los
nombres no comparten un token: el identificador es la **única** evidencia que
existe, y era justo la que se estaba tirando.

### Cuánto pesaba

Medido sobre `benchmark_institucional.csv.gz`, de los 5.312 pares en que dos
registros del mismo grupo traen identificadores textualmente distintos:

| | pares | % |
|---|---:|---:|
| uno es el otro más un dígito al final | 5.269 | **99,2 %** |
| y ese dígito valida por módulo 11 | 5.002 | 94,9 % de los anteriores |

No es un caso borde. Es la forma normal en que dos fuentes escriben el mismo
número.

## Decisión

**Comparar identificadores en forma canónica: quitar dígitos finales, y solo
los que validan.**

`matching.identificadores.base_canonica` reduce un identificador quitando un
dígito final si —y solo si— es exactamente el dígito de verificación de lo que
queda, hasta `PASOS_MAXIMOS_DV = 2` veces. Un dígito que no valida se queda:
la función **nunca adivina**.

Dos pasos, no uno, porque la cadena real llega a serlo: la fuente escribe
`NIT+DV` y el preprocesador le calcula y añade otro DV, de modo que
`10282948` viaja como `1028294826`.

La reducción se aplica en un solo sitio conceptual —el módulo de
identificadores— y los tres puntos de decisión la consumen. La perilla
`dv_es_mismo_identificador` (default `True`) permite recuperar la semántica
literal de 0.18.0.

### Por qué no otras opciones

- **Emitir las dos formas como claves de bloqueo y no tocar el scorer.** El
  bloqueo ya encontraba estos pares —el recall de bloqueo del estrato REAL era
  0,982—. El problema no estaba ahí.
- **Bajar el umbral de score.** Compra recall con precisión en todas partes
  para arreglar un caso concreto. La medición de abajo muestra que el arreglo
  correcto no cuesta precisión: ni un falso positivo.
- **Aumentar `tolerancia_digitacion_identificador` a 1.** Trata al DV como si
  fuera un error de digitación. No lo es: es información estructural, y
  confundir las dos cosas abre la puerta a unir NITs que difieren en un dígito
  de verdad. La regla que se implementó **verifica** el dígito.
- **Elegir siempre la forma corta.** Un valor de nueve dígitos puede ser un
  NIT de empresa tal cual, o una cédula de ocho más su DV. Por eso
  `formas_canonicas` devuelve las dos lecturas y no elige.

## Riesgo, medido

Colapsar `X` con `X+DV` podría unir dos entes ajenos si `X` y `X+DV` fueran
ambos identificadores reales de entidades distintas. Se midió sobre **145.082
identificadores distintos** de CRM, RUES y Superintendencia de Sociedades:

| profundidad | claves colapsadas | que agrupan >2 formas | agrupaciones que no son prefijo común |
|---|---:|---:|---:|
| 1 paso | 31.798 | 0 | **0** |
| **2 pasos** | **35.045** | 5 | **0** |
| 3 pasos | 35.337 | 7 | **0** |

Las 5 agrupaciones de más de dos formas a profundidad 2 son cadenas legítimas
del mismo número (`2202953` / `22029539` / `220295394`). **Cero** colisiones
entre números ajenos, a cualquier profundidad. Se fija el tope en 2: con 3 el
riesgo empieza a crecer sin ganar casos.

## Consecuencias

### Medido, muestra completa (`inst_base` → `inst_dv`)

| métrica | antes | después | Δ |
|---|---:|---:|---:|
| precisión | 0,9381 | 0,9385 | **+0,0004** |
| recall | 0,8189 | 0,8249 | **+0,0060** |
| F1 | 0,8745 | 0,8780 | **+0,0035** |
| B³ F1 | 0,9486 | 0,9534 | +0,0048 |
| **macro-F1 por estrato** | 0,8819 | **0,8920** | **+0,0101** |
| **F1 estrato REAL** | 0,9344 | **0,9648** | **+0,0304** |
| **recall estrato REAL** | 0,8769 | **0,9319** | **+0,0550** |
| precisión estrato REAL | 1,0000 | **1,0000** | 0 |
| recall CON_ID | 0,9657 | 0,9768 | +0,0111 |
| **FP que tocan un negativo** | 287 | **287** | **0** |
| tiempo | 35,5 s | 36,8 s | +1,3 s |

TP +275, FP **+0**, FN −275. Es una mejora **estrictamente dominante**: no
hay nada que se pague a cambio.

### Fuera de muestra, 3 pliegues por grupo

| pliegue | F1 antes | F1 después | Δ | REAL antes | REAL después | FP negativos |
|---|---:|---:|---:|---:|---:|---|
| 0 | 0,9014 | 0,9045 | +0,0031 | 0,9301 | 0,9596 | 95 → 95 |
| 1 | 0,8836 | 0,8874 | +0,0038 | 0,9374 | 0,9673 | 46 → 46 |
| 2 | 0,9086 | 0,9123 | +0,0037 | 0,9354 | 0,9670 | 35 → 35 |

Media +0,0035 global y +0,0303 en REAL, **idéntico a la muestra completa**.
No es un umbral afinado sobre los datos con que se midió: es una corrección de
comportamiento, y por eso generaliza por construcción.

### Costos y deudas

- El oráculo de paridad `tests/test_paridad_p1_1.py` congela la semántica de
  v2.8.0. Se corre con la perilla apagada, que reproduce esa semántica bit a
  bit; el comportamiento nuevo lo cubre `tests/test_identificadores_v019.py`.
  Que la paridad se conserve con la perilla apagada es, en sí, la prueba de que
  el cambio está aislado.
- El módulo 11 con pesos de la DIAN es colombiano. La *estructura*
  —identificador base más dígito de control— es universal (CNPJ en Brasil, RUT
  en Chile, CUIT en Argentina, IVA europeo). Añadir un país es añadir una
  función de verificación, no tocar el scorer. Falta el registro por país, que
  queda anotado como pendiente.
- La igualdad de identificadores se decidía en tres sitios. Ahora los tres
  llaman al mismo módulo, pero **siguen siendo tres llamadas**: la unificación
  real en un único servicio de identidad queda pendiente (C26).

## Referencias

- Garbe, W. (2012). *SymSpell*: vecindades por borrado. Fundamento del bloqueo
  por identificador, `engine/lsh/nit_blocking.py`.
- DIAN, estructura del NIT y cálculo del dígito de verificación (módulo 11).
- Christen, P. (2012). *Data Matching*, cap. 4: normalización de
  identificadores antes de comparar, no durante.
- Evidencia: `docs/evidencia/corrida_inst_base.json`,
  `corrida_inst_dv.json`, `corrida_f{0,1,2}_{base,dv}.json`.
