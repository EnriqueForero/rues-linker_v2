# Plan de mejora — 38 criterios

Cada criterio lleva la evidencia que lo motiva, cómo se evalúa y el umbral
exacto que decide si pasó. El estado se actualiza con cada iteración de
`docs/BITACORA.md`.

**Leyenda:** ✅ hecho y verificado · 🟡 parcial · ⬜ pendiente

---

## Bloque A · Defectos vivos

| # | Criterio | Estado | Pasa si |
|---|---|---|---|
| C01 | Diagnóstico de identificadores insesgado | ⬜ | error relativo < 10 % en 5 semillas · el reporte declara el tamaño de muestra |
| C02 | Atajo para grupos unitarios en L5 | ⬜ | L5 ≤ 3 min a 4,4 M de grupos · parquet byte-idéntico |
| C03 | Eliminar `to_dict("records")` del camino de millones | ⬜ | ≥ 50.000 grupos/s · pico de L5 < 2 GB |
| C04 | Caché de limpieza que nunca acierta | ⬜ | L1 ≥ 10 % más rápido · hash de NOMBRE_LIMPIO idéntico |
| C05 | Entregables que no dependan de una celda opcional | ⬜ | el manifiesto lista 5 entregables y los 5 existen |

**C01 sigue pendiente y es P0.** `diagnosticar_identificadores` toma
`unicos.sort_values().head(200_000)` —la cabeza lexicográfica, no una muestra—
y reporta el resultado como si fuera exhaustivo. Medido sobre el RUES real de
57 k, ese muestreo sobreestima la anomalía 4,5 veces.

---

## Bloque B · Memoria

| # | Criterio | Estado | Pasa si |
|---|---|---|---|
| C06 | Predicción de RAM que nunca sea optimista | ⬜ | predicción ≥ pico real en 3 corridas · sin exceder 30 % |
| C07 | Presupuesto de memoria por fase, verificado en pruebas | 🟡 | cada fase bajo su presupuesto · la prueba falla si lo excede |
| C08 | Particionar la fuente confiable para romper el techo | ⬜ | 12 M de registros en 11 GB · partición idéntica |

**C07 parcial:** el banco ya mide el pico por fase y lo deja en la evidencia
(`rss_por_fase`). Falta declarar los presupuestos y la prueba que los exige.

**0.22.4, flujo de importadores (sin NIT):** el scoring va por lotes
(`pares_por_lote`), la unión LSH por claves escalares y la tabla de decisiones
conserva solo lo auditable. La partición más grande de la base real (54.669
nombres, 24,5 M de pares) pasó de 11,4 a 4,6 GiB de pico con resultado idéntico.
C06–C08 siguen referidos al pipeline con NIT.

---

## Bloque C · Velocidad

| # | Criterio | Estado | Pasa si |
|---|---|---|---|
| C09 | Indexación LSH sin materializar 184 M de filas | ⬜ | índice ≤ 3 min a 4,4 M · candidatos idénticos |
| C10 | Firmas MinHash por encima de 25.000 rec/s | ⬜ | ≥ 25.000 rec/s · firmas byte-idénticas |
| C11 | Relación candidatos/válidos bajo control | ✅ | ≤ 50:1 sin perder recall |
| C12 | Benchmark de regresión en CI | 🟡 | CI falla si una fase se degrada > 20 % |

**C11 verificado:** 41:1 en `produccion_estandar` y 66:1 en `fuentes_mixtas`.
El segundo excede el umbral a propósito: es el precio medido del bloqueo más
abierto, y queda documentado en el perfil.

**C12 parcial:** existe el banco y el comparador con código de salida. Falta
el job nocturno que los invoque.

---

## Bloque D · Calidad

| # | Criterio | Estado | Pasa si |
|---|---|---|---|
| C13 | Que el perfil por defecto no sea el peor en recall | ✅ | F1 ≥ 0,80 sobre el conjunto de referencia, o justificación escrita |
| C14 | Precisión medida sobre datos reales | 🟡 | precisión estimada ≥ 0,98 con IC 95 % ≤ 0,03 |
| C15 | Contribución marginal de cada mecanismo de bloqueo | ✅ | cada mecanismo aporta ≥ 2 %, o se apaga |
| C16 | Evidencia multivariable realmente en el score | ✅ | F1 sube ≥ 2 puntos, o queda documentado por qué no |
| C17 | Tratar el registro histórico como historia | ⬜ | grupos con >1 nombre bajan ≥ 30 % sin perder enlaces |
| C18 | Umbrales calibrados con validación cruzada | ✅ | \|F1 dentro − F1 fuera\| ≤ 0,05 |
| C19 | Recall estratificado por tipo de identificador | ✅ | el banco publica recall por categoría |
| C20 | Invariantes de negocio como pruebas | ⬜ | toda invariante con prueba positiva y negativa |

**C13 ✅** — `produccion_estandar` da F1 0,9659 sobre el conjunto de
referencia, muy por encima del piso. El 0,3652 que se reportaba antes venía de
`gt_robusto.parquet`, un conjunto sintético mucho más adverso; medir con dos
conjuntos y comparar los números entre sí era el error.

**C16 ✅ con resultado negativo documentado y explicado (v0.19.0).** La
evidencia adicional en el scorer **no sube el F1**, y ahora se sabe por qué.
Medido el recall de bloqueo por estrato: RUIDO 0,730 y CONTACTO 0,959 pierden
sus pares **antes** de llegar al scorer, así que ninguna perilla del scorer los
rescata. Donde sí se pierde en scoring —estrato REAL, bloqueo 0,982 contra
final 0,877— el problema no era falta de evidencia sino comparar mal el
identificador, y arreglarlo subió el recall a 0,932 (ADR-0004).

Lo que sí quedó, con su medición: `conjunto_signed` sobre CIIU baja los falsos
positivos de 2.505 a 2.429 sin tocar el recall, y `categoria_tolerante_signed`
sube la separación de DEPARTAMENTO de 40,3 pp a 69,9 pp. La geografía como
evidencia adicional sigue restando y sigue desaconsejada. La palanca pendiente
es **C31, bloqueo multivariable**.

**C18 ✅** — ΔF1 fuera de muestra +0,0105 contra +0,0140 dentro. Diferencia
0,0035, muy por debajo del criterio.

**C14 parcial:** el banco mide precisión sobre 43 casos negativos diseñados, y
`prediccion_<etiqueta>.parquet` permite exportar una muestra estratificada para
revisión. Falta la revisión humana sobre datos reales, que no es código.

**C19 ✅ (v0.19.0)** — el banco publica recall por régimen, por caso, y
precisión/recall/F1 **por estrato** más un macro-F1. Con eso el diagnóstico se
lee de un golpe: `RUIDO F1 0,7454` contra `REAL F1 0,9648` en la misma corrida.
El F1 global no puede volver a esconder un estrato, que era el riesgo real.

---

## Bloque E · Arquitectura

| # | Criterio | Estado | Pasa si |
|---|---|---|---|
| C21 | Responsabilidad única: partir los módulos gigantes | ⬜ | ningún módulo del núcleo > 800 líneas |
| C22 | Estrategias intercambiables (OCP/DIP) | 🟡 | una estrategia nueva no modifica el motor |
| C23 | Sustituibilidad verificada (LSP) | ⬜ | las implementaciones pasan la misma batería |
| C24 | Segregar la configuración (ISP) | ⬜ | ≤ 8 campos por dataclass |

**C22 parcial:** los comparadores de variables adicionales ya son un registro
extensible, con una prueba que añade uno de juguete sin tocar el scorer. Las
estrategias de **bloqueo** siguen cableadas dentro de `disk_based.py`.

---

## Bloque F · Ingeniería

| # | Criterio | Estado | Pasa si |
|---|---|---|---|
| C25 | Tipado sin deuda | ⬜ | 0 errores de mypy · sin `continue-on-error` |
| C31 | Bloqueo multivariable | ✅ | implementado, medido y documentado — incluido el resultado negativo |
| C32 | Un solo servicio de identidad de identificadores | 🟡 | un único punto decide igualdad · los tres consumidores lo llaman |
| C33 | Suite de conformidad por caso | ✅ | 43 casos con veredicto individual · código de salida |
| C34 | Un solo catálogo de comparación | ✅ | los dos motores usan el mismo registro · cero lógica duplicada |
| C35 | Calibrar `min_corroborantes` con datos reales | ⬜ | medición que distinga 1 de 2 · decisión con evidencia |
| C36 | Un solo catálogo de BLOQUEO | ⬜ | las estrategias del motor multicampo disponibles en el camino en disco |
| C37 | Discriminar bajo corrupción extrema sin identificador | ⬜ | recall de `positivo_ruido_sin_id` ≥ 0,75 sin perder precisión |
| C38 | `NAME_SIMILARITY_SCORE` comparable | ⬜ | compara nombres normalizados entre sí · impacto medido sobre resultados publicados |

**C31 ✅ con resultado negativo documentado.** Los dos mecanismos —llaves
declaradas y token raro— están implementados, probados y apagados por defecto.
Medido: **no mejoran este conjunto**. Las llaves añaden 61.293 candidatos y
recuperan cero pares verdaderos; el token raro solo ayuda al estrato de ruido
(0,6472 → 0,6717) multiplicando por nueve los falsos positivos sobre
negativos. Se conservan porque son correctos, baratos y son la palanca
adecuada para datos donde el contacto sea el único puente — pero no se afirma
que mejoren nada aquí.

**C34 ✅.** El catálogo de comparación pasó de 11 a 24 tipos disponibles en
producción, sin lógica duplicada (ADR-0007). De paso destapó dos defectos
reales: los ausentes de pandas 3.0 y la placa de dirección.
| C26 | Cobertura del núcleo al 85 % | ⬜ | ≥ 85 % en engine/, matching/, processing/, flujo/ |
| C27 | Determinismo verificado de punta a punta | ✅ | huella idéntica entre corridas |
| C28 | Superficie pública congelada | ⬜ | la prueba falla ante cualquier cambio no declarado |
| C29 | Evidencia del CHANGELOG regenerable | ✅ | las tablas se regeneran sin edición manual |
| C30 | Decisiones registradas (ADR) | ✅ | cada cambio de comportamiento con ADR y evidencia |

**C27 ✅** — verificado en cada corrida de paridad de esta versión: la huella
`c9d30223…` se reprodujo idéntica cinco veces con código distinto.

**C29 ✅** — todas las cifras de este release salen de
`docs/evidencia/corrida_*.json`, producidos por `scripts/banco.py`.

---

## Resumen de estado

| | criterios |
|---|---|
| ✅ Hechos y verificados | 14 |
| 🟡 Parciales | 6 |
| ⬜ Pendientes | 18 |

## Qué sigue, en orden

0. **C37 — discriminar bajo corrupción extrema del nombre sin identificador.
   Es el P0, y desplaza a C31, que era el P0 anterior.**

   En v0.19.0 escribí que el techo del estrato de ruido era un problema de
   bloqueo. **Lo medí en v0.20.0 y no lo es.** Los pares perdidos son
   alcanzables —el 82,8 % tiene Jaccard de trigramas ≥ 0,30 y la mediana,
   0,475, está por encima del umbral configurado— pero toda forma de
   alcanzarlos inunda el scorer de pares falsos que cuestan más de lo que
   ganan: bajar el umbral empeora el recall de 0,647 a 0,555, afilar la curva
   lo deja en 0,643, y el bloqueo por token raro solo ayuda multiplicando por
   nueve los falsos positivos sobre negativos.

   El cuello está en el scorer, no en el bloqueo. Son 17.055 pares, el 37 % del
   conjunto, y hoy se resuelven al 60 %.
1. **C01** — el diagnóstico de identificadores reporta una cifra que no es.
   Es P0 porque hoy induce a error a quien lee el notebook.
2. **C02 + C03** — L5 gasta el 30 % de una corrida real procesando grupos de
   una sola fila. No se nota en el banco de 12 K registros; se nota mucho a
   4,4 M.
3. **C25** — 97 errores de mypy con el job en `continue-on-error`. Un tipo mal
   declarado fue exactamente lo que permitió el incidente de 0.17.3.
4. **C12** — el banco existe; falta que CI lo corra solo.


## Fuera de la numeración: el flujo sin identificador (0.22.0 → 0.22.3)

No es uno de los 38 criterios —esos son del motor con NIT— sino una línea de
trabajo aparte para bases donde no hay identificador: destinatarios de
exportación, importadores. Vive en `flujo.importadores` con sus pruebas, su
notebook (07) y su análisis (`ANALISIS_IMPORTADORES.md`); lo cerrado en cada
versión está en el CHANGELOG. Lo abierto:

- **Precisión sobre `snowflake_v2`.** Las cifras de 0,966 son de la base DIAN.
  Hay que volver a etiquetar `MUESTRA_REVISION` en la primera corrida real.
- **Grupos económicos.** `BARRY CALLEBAUT USA` y `… CANADA` se unen por diseño
  (`geografia_es_ruido=True`). Es el error dominante de la banda 0,92–0,96 y
  hay que decidir si se quiere, no solo documentarlo.
- **Catálogo de países vivo.** La base nueva trajo grafías fuera del catálogo;
  cada una que aparezca se declara como alias, no se adivina. El flujo ya se
  detiene antes de emparejar y las lista.
