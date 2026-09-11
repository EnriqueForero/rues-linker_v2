# ADR-0006 — Dos instrumentos: el banco mide cuánto, la conformidad mide qué

- **Estado:** aceptado
- **Fecha:** 2026-08-29
- **Versión:** 0.20.0
- **Complementa a:** ADR-0001 (banco de pruebas único)

## Contexto

ADR-0001 estableció un banco estadístico único y prohibió medir con un
conjunto distinto cada vez. Fue la decisión correcta y sigue vigente. Pero
tiene un punto ciego que el conjunto institucional hizo evidente al crecer.

Con 46.374 pares, un comportamiento roto que afecta a cuatro casos —geo
idéntica en entidades distintas, teléfono de call center compartido, grupo con
dos NIT legítimos, persona natural contra empresa homónima— mueve el F1 en la
cuarta cifra decimal. **No es que se note poco: es que no se nota nunca.** Y
son exactamente los casos que en producción producen una fusión escandalosa.

La pregunta "¿mejoró?" y la pregunta "¿sabe hacer esto?" son distintas y
ningún promedio responde la segunda.

## Decisión

**Dos instrumentos, con roles separados y ninguno reemplaza al otro.**

| | banco | conformidad |
|---|---|---|
| pregunta | ¿mejoró? | ¿sabe hacerlo? |
| conjunto | `benchmark_institucional.csv.gz` | `data/conformidad/` |
| tamaño | 30.486 registros · 46.374 pares | 207 registros · 155 pares |
| naturaleza | estadística, variación real | 43 casos frontera diseñados |
| salida | F1, recall, precisión, macro-F1 | aprobado/reprobado **por caso** |
| falla si | una métrica retrocede | **un solo caso** reprueba |
| script | `scripts/banco.py` | `scripts/conformidad.py` |

El conjunto de conformidad viene de `Ground_Truth_Multicampo_v1.xlsx`,
construido para evaluación multicriterio, y se versiona extraído a CSV. Cubre
**11 tipos de campo** —identificador, nombre de empresa, nombre de persona,
correo, teléfono, dirección, ciudad, categórico, geo, fecha, numérico— que el
banco estadístico no ejercita porque sus fuentes no los traen.

### Reglas del instrumento

1. **Un caso reprobado reprueba la corrida.** No se promedia con los demás.
2. **Los casos marcados `TP_DIFICIL` en el catálogo son deuda declarada**: se
   listan aparte y no reprueban. Separarlos es lo que impide que un caso
   realmente roto se esconda entre las fronteras conocidas.
3. **El conjunto se valida al cargar.** Si la hoja de pares y la columna de
   grupo no dicen lo mismo, se lanza un error: un conjunto que se contradice
   mide su propio defecto y conviene enterarse antes de publicar cifras, no
   después. Esa comprobación existe porque en 0.19.0 el otro conjunto sí se
   contradecía y costó un diagnóstico entero descubrirlo.
4. **No se regenera sin acta**, como pide el libro original: si cambia, las
   métricas históricas dejan de ser comparables.

### Evaluación contra construcción: particiones, no dos conjuntos

La pregunta natural —"¿hace falta un conjunto para calibrar y otro para
evaluar?"— tiene respuesta conocida y no exige duplicar nada: se parte el
mismo conjunto **por grupo**, se calibra en unos pliegues y se verifica en
otro. Ya está implementado (`--pliegue`, `--pliegues`) y es lo que se usó para
validar ADR-0004 fuera de muestra. Dos conjuntos distintos tendrían el defecto
contrario: sus cifras no serían comparables entre sí.

## Consecuencias

### Medido

Línea base de conformidad, motor multicampo, esquema del propio DICCIONARIO:

| escenario | precisión | recall | F1 | casos firmes |
|---|---:|---:|---:|---|
| deduplicación | 1,0000 | 0,9847 | 0,9923 | 24/24 |
| record linkage | 1,0000 | 1,0000 | 1,0000 | 10/10 |

Reproduce **exactamente** la línea base que declara la hoja LEEME del libro
original, medida con la versión 0.10.0 del motor. Que dos implementaciones
independientes den la misma cifra es la mejor evidencia de que el instrumento
mide lo que dice medir.

Con la corroboración de veto activa (F3), los dos casos frontera se resuelven:

| | C09 (NIT con un dígito mal) | C21 (dos NIT legítimos) | trampas |
|---|---|---|---|
| sin corroboración | reprueba | reprueba | 6/6 aprueban |
| con corroboración | **aprueba** | **aprueba** | **6/6 aprueban** |

F1 de deduplicación 0,9923 → **1,0000**, con las seis trampas de falso
positivo intactas: homónimos, NIT idéntico con nombres distintos, persona
contra empresa, correo genérico compartido, teléfono de call center y geo
idéntica en el mismo edificio.

### Lo que NO se pudo justificar

`min_corroborantes=2` se eligió por criterio de riesgo, **no porque la
medición lo respalde**: con 1 la suite también da F1 = 1,0000 y ninguna trampa
se cae. Lo que sostiene las trampas no es el número de corroborantes sino que
sus comparadores ya devuelven 0 ante valores de baja entropía, más la
exigencia de similitud de nombre. Cuál de los dos conviene en datos reales
queda pendiente de medir (C35), y hasta entonces la elección es prudencia
declarada, no evidencia.

## Addendum (0.22.1) — un instrumento que se salta no mide

El conjunto vive en el repositorio, no dentro del paquete. Eso fue una
decisión razonable —el archivo no se regenera sin acta y versionarlo aparte lo
hace evidente— pero tenía un costo que no se había visto: la ruta se derivaba
de la ubicación del módulo, así que **con el paquete instalado el conjunto
desaparecía**, el fixture hacía `pytest.skip` y 8 casos se dejaban de medir en
silencio.

El modo de fallo es peor que el de un caso reprobado, y es justo el que este
ADR dice querer evitar: un caso reprobado es una capacidad ausente que se ve;
un caso saltado es una capacidad **no medida** que se lee como verde.

Dos consecuencias que quedan fijadas:

1. `localizar_conjunto()` resuelve por capas y hay una prueba que llama
   `cargar_conjunto()` **sin el fixture**: si el conjunto no está, falla, no
   se salta.
2. La compuerta de publicación inspecciona los saltos, no solo el código de
   salida de pytest — que es 0 aunque todo se salte.

**Regla general:** todo `pytest.skip` dentro de un `except` convierte un
problema de entorno en silencio. Si un fixture puede saltarse, hace falta una
prueba que verifique que no se salta cuando no debe.

## Referencias

- Christen, P. (2012). *Data Matching*, cap. 7: evaluación de linkage y por
  qué las métricas agregadas ocultan clases de error.
- `Ground_Truth_Multicampo_v1.xlsx`, hojas LEEME y CATALOGO_CASOS.
- Evidencia: `docs/evidencia/conformidad_{dedup,linkage}_{base,corroborado}.json`.
