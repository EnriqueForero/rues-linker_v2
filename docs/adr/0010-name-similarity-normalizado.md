# ADR-0010 — `NAME_SIMILARITY_SCORE` compara normalizado contra normalizado, y el golden promete sus tipos

- **Estado:** aceptada
- **Fecha:** 2026-10-06
- **Versión:** F1.14 (rues-linker v2, sobre 0.22.4)

## Contexto

ADR-0008 dejó un caveat anotado como C38: `NAME_SIMILARITY_SCORE` comparaba
`NOMBRE_LIMPIO` —el nombre normalizado para el motor— contra
`RAZON_SOCIAL_FINAL` —el nombre adoptado por el grupo, **sin** normalizar—.
Dos filas con el mismo nombre real podían dar 0,69 solo por eso. Se conservó
entonces a propósito, porque la columna ya estaba en resultados publicados y
merecía su propia medición. Esta es esa medición.

### Lo que se verificó

**Qué compara el código.** La columna la calcula
`golden/columnas_finales.py::garantizar_columnas_finales`, llamada desde
`generator.py::_load_results_from_db`. El método
`GoldenRecordGeneratorV7._add_diagnostic_metrics` (~líneas 1349-1410), que la
tarea pedía leer, era **código muerto**: ningún módulo lo llamaba desde 0.21.0.
Hacía lo mismo —`NOMBRE_LIMPIO` contra `RAZON_SOCIAL_FINAL` crudo— y se borró
para que la regla viva una sola vez.

**Lo que mide en el banco** (`benchmark_institucional`, 30.486 filas, misma
partición antes y después, fila a fila):

| | antes (C38 abierto) | después |
|---|---|---|
| media | 0,739 | 0,895 |
| mediana | 0,789 | 1,000 |
| filas con 1,0 | 15,6 % | 64,8 % |
| filas ≥ 0,9 | 25,9 % | 72,4 % |
| filas < 0,7 | 31,5 % | 13,0 % |
| filas en 0 | 0,10 % | 0,01 % |

El 15,6 % que puntuaba 1,0 son las filas cuyo `NOMBRE_LIMPIO` coincidía
letra a letra con la razón social cruda adoptada: es decir, nombres sin
tildes, sin forma societaria y sin palabras frecuentes de origen. En un
conjunto donde 11.478 grupos están bien resueltos, una columna de diagnóstico
que marca al 74 % de las filas por debajo de 0,9 no le sirve al revisor para
nada: no distingue «otra empresa» de «la misma con S.A.S.».

**Por qué `NOMBRE_LIMPIO` no sirve como lado normalizado.** Es una
normalización para bloquear y puntuar, no para comparar con un humano al
lado: poda 511 palabras frecuentes y deja residuos. Medido con el limpiador
del perfil `produccion_estandar` (modo `BALANCEADO`):

```
COMERCIALIZADORA ANDINA S.A.S.  →  COMERCIALIZADORA ANDINA S S
Comercializadora Andina SAS     →  COMERCIALIZADORA ANDINA
```

«S.A.S.» y «SAS» no miden 1,0 ni comparando `NOMBRE_LIMPIO` contra
`NOMBRE_LIMPIO`. Y al revés, la poda hace iguales nombres que no lo son:
`INDUSTRIAS PACIFICO SUCURSAL ESAL` e `INDUSTRIAS PACIFICO ESAL` daban 1,0
porque «SUCURSAL» es palabra frecuente.

**Los tipos del golden.** En el parquet publicado del banco `SOURCES_COUNT`,
`RECORD_COUNT`, `NAME_VARIATIONS` y `NIT_VARIATIONS` salían `float64` y
`REQUIRES_REVIEW` `float64`. Dos causas: SQLite no tiene booleano (el 0/1
llegaba hasta el parquet) y la consolidación por NIT deja dos filas huérfanas
con las métricas en NaN (F1.1), lo que fuerza a flotante toda la columna.

## Criterios de decisión

- Una columna de diagnóstico tiene que medir lo que un revisor entiende como
  diferencia: forma societaria, puntuación, mayúsculas y tildes no lo son.
- Los dos lados pasan por la **misma** normalización, y esa normalización
  debe ser una que ya exista en la librería (una regla se escribe una vez).
- La partición no se toca: `NAME_SIMILARITY_SCORE` no decide nada aguas
  arriba ni aguas abajo. Verificado con `grep` sobre `src/`: fuera de
  `golden/columnas_finales.py` nadie lee la columna (ni el selector de golden,
  ni `cannot_link`, ni la revisión, ni los reportes L6).
- Nada se repara en silencio: un conteo con nulos no se rellena para poder
  tiparlo.

## Opciones consideradas

- **`NOMBRE_LIMPIO` contra `NOMBRE_LIMPIO` del nombre adoptado.** Descartada
  por lo medido arriba: hereda los residuos («S S») y la poda de palabras
  frecuentes, que en un sentido esconde diferencias y en el otro las inventa.
- **Un normalizador nuevo para el diagnóstico.** Descartada: sería la cuarta
  copia de «quitar forma societaria» en el repositorio.
- **La huella del selector de golden (`AdvancedValueSelector._fingerprint_series`).**
  Elegida. Es la normalización bajo la cual el selector **decidió** qué nombre
  adoptar (mayúsculas sin tildes, sin forma societaria, sin nada que no sea
  letra o dígito). Medir cada fila contra el adoptado con esa misma lente es
  coherente por construcción: «qué tan lejos está esta fila del nombre que el
  grupo eligió, visto como lo vio el selector».

## Decisión

1. **`golden/selector.py`** expone `huella_de_nombre(serie)` como única
   implementación vectorizada de la huella; `_fingerprint_series` delega en
   ella (paridad verificada por `tests/test_golden_selector_paridad.py`,
   que sigue en verde). El patrón de formas societarias queda como constante
   de módulo `PATRON_FORMA_SOCIETARIA`.
2. **`golden/columnas_finales.py`**: `NAME_SIMILARITY_SCORE` es la similitud
   de Levenshtein normalizada entre `huella_de_nombre(RAZON_SOCIAL)` y
   `huella_de_nombre(RAZON_SOCIAL_FINAL)`. Se parte de `RAZON_SOCIAL` (de
   donde sale el adoptado) y solo se cae a `NOMBRE_LIMPIO` cuando no existe.
   Si un nombre es solo forma societaria («LTDA») y su huella queda vacía, se
   compara el crudo en mayúsculas para que una fila idéntica al adoptado nunca
   puntúe 0. El `origen` del reporte lo declara: «calculada sobre RAZON_SOCIAL
   normalizada contra RAZON_SOCIAL_FINAL normalizada (huella del selector, C38)».
3. **`golden/tipos.py`** declara el contrato de tipos
   (`TIPOS_METRICAS_GOLDEN`: cuatro conteos `int64`, `REQUIRES_REVIEW`
   `bool`) y `tipar_golden()` lo aplica en los dos puntos por donde sale un
   golden: el generador al cargar desde SQLite (estricto: allí no puede haber
   nulos) y el orquestador tras la consolidación por NIT (tolerante: una
   métrica con nulos se deja como viene y se advierte nombrándola; no se
   inventa un valor). En modo estricto el error es `GoldenSinTiparError` con
   mensaje «qué pasó / por qué importa / qué hacer», en
   `pipeline/errores.py`.
4. El SQL de `golden_records` conserva `REQUIRES_REVIEW INTEGER` —SQLite no
   tiene otra cosa— con el comentario de que el tipo del contrato lo aplica
   `tipar_golden`.
5. `tests/contratos/esquema_salida_v0.json` se regeneró: el único cambio es
   `REQUIRES_REVIEW` de `int64` a `bool`. Es el cambio a propósito que ese
   fixture documenta.

## Consecuencias

### Medido

- **La huella de la partición es idéntica**:
  `1e365ba81c4df45e410dd09154cafef1d38e96d9fb2846998260c10ad69cbe31`,
  `F1 0.8780`, `macro-F1 0.8920`, mismos `ID_GRUPO` para las 30.486 filas.
  Cambia la columna, no el enlace.
- `NAME_SIMILARITY_SCORE`: 73,9 % de las filas suben, 15,6 % quedan igual
  (las que ya estaban en 1,0) y 10,4 % bajan. Las que bajan son, en su
  mayoría, diferencias reales que la poda de `NOMBRE_LIMPIO` escondía
  («SUCURSAL», «PLANTA») o siglas que no son forma societaria y ahora cuentan
  («ESAL», «S EN C»).
- Tras `linkage()` sobre un conjunto sintético: conteos `int64`,
  `REQUIRES_REVIEW` `bool`, «COMERCIALIZADORA ANDINA S.A.S.» contra
  «Comercializadora Andina SAS» = 1,0, «FERRETERIA EL TORNILLO» contra
  «FERRETERÍA EL TORNILLO & CIA LTDA» = 1,0, «COMERCIALIZADORA ANDINA DEL SUR
  SAS» < 0,9 (`tests/test_golden_tipos.py`, 19 pruebas).
- En el banco de esta rama, el golden **publicado** sigue saliendo con
  `float64`/`object` en las métricas: la consolidación por NIT deja dos filas
  con NaN y el orquestador, en modo tolerante, lo advierte y no las tipa. Es
  el defecto que F1.1 corrige en su rama; con las dos ramas integradas no hay
  nulos y `tipar_golden` aplica los tipos del contrato (verificado sobre
  `ground_truth_grande`, donde la consolidación no deja huérfanas: golden
  3.731 × 13, `int64`/`bool`, sin NaN).

### Lo que cambia para quien consume resultados

`NAME_SIMILARITY_SCORE` ya no es comparable con corridas anteriores a F1.14:
un umbral calibrado sobre la semántica vieja (p. ej. «revisar si < 0,8»)
marca ahora muchas menos filas, y las que marca son diferencias reales. Los
reportes L6 no leen la columna, así que no cambian.

### Lo que no se resolvió

- `PATRON_FORMA_SOCIETARIA` no reconoce «ESAL», «S EN C», «S C A» ni «BIC».
  Ampliarlo cambiaría el consenso del selector (comportamiento de L5), que
  esta fase no toca; queda para F2 con medición.
- La huella de nombre sigue existiendo en tres copias: la del selector (ahora
  la canónica), `engine/lsh/name_blocking.py::_fingerprint_series` y
  `generator.py::_get_fingerprint`. Las dos restantes son del motor y se
  unifican en F5.

## Referencias

- ADR-0008 (caveat original, C38).
- `docs/PLAN.md`, criterio C38.
- `tests/test_golden_tipos.py`, `tests/test_columnas_finales_v021.py`,
  `tests/test_golden_selector_paridad.py`.
