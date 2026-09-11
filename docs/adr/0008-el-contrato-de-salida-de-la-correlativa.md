# ADR-0008 — La identidad adoptada por cada grupo es parte del contrato, no un extra

- **Estado:** aceptada
- **Fecha:** 2026-08-30
- **Versión:** 0.21.0

## Contexto

Enrique reportó que `correlativa.parquet` salía sin `NAME_SIMILARITY_SCORE`,
`NIT_FINAL` ni `RAZON_SOCIAL_FINAL`, y lo dijo con precisión: *"parte del
resultado final es que quede con un NIT y Razón Social Final."*

Tiene razón, y el diagnóstico da algo peor que el síntoma.

### Lo que se verificó

Sobre 0.20.0, las cuatro columnas **sí salen** en los cuatro caminos probados
—pandas y DuckDB, con y sin colapso de duplicados, modo dataframe y modo
disco—. **No se pudo reproducir el defecto.**

Pero al buscar por qué podría faltar, aparecieron **tres puntos donde la
entrega se degrada en silencio**:

```
generator.py:1269   except Exception: logger.warning("No se pudieron añadir
                    métricas de diagnóstico")
generator.py:1341   if missing_cols: logger.warning(...); return df
orchestrator.py     except Exception: log.warning("Consolidación falló,
                    usando resultados directos")
```

Tres caminos por los que el resultado sale incompleto escribiendo solo una
advertencia, en el registro de una corrida de cuarenta minutos que nadie lee
entero.

Y el hallazgo que lo explica todo: **`flujo/cruce.py` no mencionaba `NIT_FINAL`
ni una sola vez.** `_verificar_invariantes` comprobaba el número de filas, la
ausencia de `ID_GRUPO` nulos y la coherencia entre golden y correlativa — pero
no que el resultado dijera qué identidad adoptó cada grupo. **La garantía que
el usuario daba por descontada no existía en el código.**

Que en la versión probada funcione es suerte, no diseño.

## Criterios de decisión

- Las cuatro columnas son el entregable, no un adorno de diagnóstico.
- Una corrida de cuarenta minutos no debe fallar por un defecto interno que se
  puede reparar.
- Pero tampoco debe repararse en silencio: eso es exactamente lo que produjo
  el problema.

## Opciones consideradas

- **Fallar si faltan.** Descartada como opción única: castiga al usuario
  después de cuarenta minutos de cómputo por un defecto que no es suyo.
- **Reparar en silencio.** Descartada: es la política actual y es la causa.
- **Reparar, dejar constancia, y fallar solo si la reparación es imposible.**
  Elegida.
- **Arreglarlo solo en el notebook.** Descartada, y merece decirse por qué:
  el notebook no puede garantizar lo que la librería no produce. Un arreglo
  ahí habría tapado el síntoma en un consumidor y dejado el defecto para
  todos los demás.

## Decisión

**`golden/columnas_finales.py`** define el contrato y lo garantiza:

```python
COLUMNAS_IDENTIDAD   = ("NIT_FINAL", "RAZON_SOCIAL_FINAL")
COLUMNAS_DIAGNOSTICO = ("NAME_SIMILARITY_SCORE", "NIT_DISTANCE")
COLUMNAS_FINALES     = COLUMNAS_IDENTIDAD + COLUMNAS_DIAGNOSTICO
```

`garantizar_columnas_finales(correlativa, golden)` distingue dos situaciones
que no son la misma:

- **Calcular el diagnóstico** es trabajo normal del proceso. Se hace y se
  registra en INFO.
- **Reconstruir la identidad** desde el golden es una anomalía: significa que
  algo falló antes sin decirlo. Se hace, y se registra en WARNING.

La reparación es siempre posible porque el golden lleva `ID_GRUPO`,
`NIT_FINAL` y `RAZON_SOCIAL_FINAL` por construcción. Solo cuando el golden no
sirve se levanta un error, y ahí sí es un fallo real.

**La invariante se hace explícita en los dos caminos del flujo:**

- `flujo/cruce.py::_verificar_invariantes` exige las cuatro columnas.
- `flujo/resultados_disco.py::_validate_final_correlation` las comprueba sobre
  el parquet publicado con `DESCRIBE`, que lee el esquema y no las filas.

**Y se expone como pregunta, no como conocimiento tribal:**
`identidad_adoptada(limite)` entra en el protocolo `ControlCalidad` y en sus
dos implementaciones. El notebook ya no tiene que saber cómo se llaman las
columnas ni cómo leerlas sin materializar millones de filas.

## Consecuencias

### Medido

- Las cuatro columnas salen en los cuatro caminos, **verificado tras el
  cambio**: pandas sin colapso, pandas con colapso, DuckDB en modo dataframe y
  DuckDB en modo disco.
- **La línea base del banco no se movió**: `F1 0.8780`, `macro-F1 0.8920`,
  huella `1e365ba81c4df45e410dd09154cafef1` — bit a bit idéntica a 0.20.0. El
  cambio garantiza la salida, no altera el enlace.
- `identidad_adoptada` devuelve la misma forma en los dos modos de resultado.

### Caveat honesto que queda documentado

`NAME_SIMILARITY_SCORE` compara `NOMBRE_LIMPIO` —el nombre normalizado— contra
`RAZON_SOCIAL_FINAL` —el nombre adoptado **sin** normalizar—. Dos filas con el
mismo nombre real pueden dar 0,69 en vez de 1,0 solo por eso. Es la semántica
que traía 0.20.0 y **se conservó a propósito**: cambiarla alteraría una
columna que ya está en resultados publicados, y merece su propia medición.
Queda anotado como C38.

### Lo que no se resolvió

**No se pudo reproducir el caso de Enrique.** Las tres hipótesis vivas son que
corriera una versión anterior a 0.19.0, que una de las tres excepciones se
disparara en su corrida, o que estuviera mirando otro artefacto. Las tres
quedan cerradas hacia adelante por este cambio, pero **cuál fue no se sabe**;
si conserva el registro de esa corrida, buscar `Consolidación falló` o
`métricas de diagnóstico` lo resolvería.

## Referencias

- Evidencia: `docs/evidencia/corrida_v021_base.json`.
- `tests/test_columnas_finales_v021.py` (17 pruebas).
