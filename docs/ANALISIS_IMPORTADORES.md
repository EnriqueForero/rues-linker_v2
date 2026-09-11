# Deduplicación de la base de importadores por razón social y país

Informe de la corrida de referencia · base `empresas_importadoras.csv`
(211.949 registros de destinatarios de exportación colombianos).
Notebook: [`notebooks/07_deduplicar_importadores_razon_social_pais.ipynb`](../notebooks/07_deduplicar_importadores_razon_social_pais.ipynb).

---

## 1. Resultado en una línea

**211.949 registros → 100.008 importadores distintos (−52,8 %).** La
concentración del valor FOB en el top-100 pasa de **22,4 % a 35,2 %**: la base
sin deduplicar subestima a la mitad qué tan concentrado está el comercio.

| | Antes | Después |
|---|---:|---:|
| Entidades (razón social × país) | 211.891 | 99.856 |
| FOB en el top 10 | 5,32 % | 10,17 % |
| FOB en el top 100 | 22,40 % | 35,15 % |
| FOB en el top 1.000 | 51,95 % | 69,32 % |
| Grafías de país | 529 | 204 |

Cifras sin los 58 registros de destinatario reservado (§5). La cifra de
importadores es la de 0.22.4: hasta 0.22.3 era 99.897, porque `A LA ORDEN`,
`TO ORDER` y `TO THE ORDER` (204 filas, 1,9 % del FOB) contaban como un
importador por país; ahora son singletons sin nombre y el resto de la partición
es idéntico. La tabla de concentración se midió sobre la corrida de 0.22.0.

---

## 2. Qué se hizo

Empalme por **razón social + país**, con el país como bloqueo duro: dos
registros solo se unen si el país canónico coincide exactamente y la
similitud de nombre supera 0,84.

```
saneamiento → canonización de país → normalización de nombre
   → bloqueo LSH por partición de país → score multicampo (rues-linker)
   → componentes conexas → cobertura por estrellas → registro consolidado
```

Todo vive en `rues-linker 0.22.4`, con pruebas: el saneamiento
(`processing.saneamiento`), la canonización del país (`paises`), el comparador
de razones sociales (`matching.nombre_idf`), las listas declaradas
(`matching.genericos`), la cobertura por estrellas (`engine.cobertura`) y el
flujo con su control de calidad (`flujo.importadores`). El notebook
[`07`](../notebooks/07_deduplicar_importadores_razon_social_pais.ipynb) solo
configura y audita — 16 celdas numeradas 1–10.

```python
from record_linkage.flujo import ConfigImportadores, deduplicar_importadores

r = deduplicar_importadores(df, ConfigImportadores(
    col_razon_social="RAZON_SOCIAL_DESTINATARIO",
    col_pais="PAIS_DESTINO_FINAL",
))
assert r.todo_ok
```

### Países que el catálogo no reconoce (0.22.3)

La base `snowflake_v2` trajo grafías de `PAIS_ESTANDAR` fuera del catálogo. No
es cosmético: el país es el bloqueo duro, y medido sobre una muestra, la misma
razón social bajo tres grafías sin clasificar distintas se fusionaba en **un**
importador porque las tres compartían `PAIS_FINAL = SIN CLASIFICAR`. Desde
0.22.3 `preparar()` se detiene antes de emparejar con la lista y los remedios
(`paises_sin_clasificar="detener"`), o aísla cada grafía con su propio
`PAIS_FINAL` (`"aislar"`) y una décima invariante mide que no se mezclen. Con
cobertura total —esta base— los dos modos dan el mismo resultado.

### Lo que `snowflake_v2` le enseñó al catálogo (0.22.4)

La primera corrida real se detuvo, como debía, con 19 grafías de `PAIS_ESTANDAR`
fuera del catálogo. **18 eran países**: 11 faltaban del todo (Irán, Afganistán,
Liechtenstein, Vanuatu, Gibraltar, Norfolk, Salomón, Santo Tomé, Vaticano,
Marianas del Norte, Samoa) y 7 estaban sin su grafía en español (Zimbabue,
Fiyi, Lesoto, Bosnia, Guinea-Bisáu, República de Macedonia, Islas Vírgenes
Estadounidenses). El catálogo pasó de 203 a 214 países. La sugerencia por
similitud acertó 6 de 18 — `IRAN → IRLANDA`, `AFGANISTAN → ALBANIA` — que es la
prueba empírica de por qué se declara y no se adivina. La 19ª, `OTROS` (444
filas), se declara como no-país (`paises_aislar`) y va aparte sin apagar la
guardia para grafías nuevas.

### Los centinelas de nombre de `snowflake_v2` (0.22.4)

En la base DIAN el destinatario reservado era `'0'`: 58 filas, 20,5 % del FOB.
En `snowflake_v2` el centinela se llama `NO DISPONIBLE` —69 filas, una por
país, **21,7 %** del FOB— y además `A LA ORDEN` / `TO ORDER` / `TO THE ORDER`
(199 filas, 1,6 %), el consignatario genérico del conocimiento de embarque.
Ninguno estaba en `PLACEHOLDERS`; `NO DISPONIBLE` quedaba como el importador
más grande de 69 países. Desde 0.22.4 los seis son placeholders: grupo propio,
`sin_nombre_utilizable`, FOB visible en REVISION. Regla que queda: **cada
fuente trae su propio centinela; mírese el top del ranking en la primera
corrida**.

### La base `snowflake_v2` completa: la memoria (0.22.4)

La primera corrida sobre las 355.681 filas murió en la partición 175 de 214
(`exit 137`, contenedor de 15 GiB). La partición USA sola —54.669 nombres,
24.476.082 pares candidatos— costaba 520 s y **11,42 GiB de pico**, con una
tabla de decisiones de 2,58 GiB. Era costo por par, no un bloqueo degenerado:
quince arrays por par en el motor, la unión LSH como pares de 16 bytes de todas
las bandas, y los dos nombres guardados en cada candidato. Desde 0.22.4 el motor
puntúa por lotes de 1 M de pares, la unión se calcula por clave escalar y la
tabla de decisiones conserva solo lo auditable. La partición USA da lo mismo
(24.476.082 candidatos, 28.409 fusiones, 44.761 grupos) en 340 s y 4,58 GiB.

La corrida completa, ya con eso: **355.681 filas → 209.038 importadores
(−41,2 %)**; 315.624 razones sociales distintas → 230.009 nombres normalizados
→ 247.518 representantes (país, nombre) → 208.714 grupos, más 324 filas sin
nombre utilizable en grupo propio. 52.801.652 candidatos, 71.548 fusiones, 2.422
cortes por cohesión; 12 min 54 s de emparejamiento, 17 min de pared con la
exportación; **pico de 4,81 GiB**. Las 10 invariantes en OK; recall del bloqueo
por fuerza bruta 0,999 (GTM), 1,000 (NLD), 1,000 (DEU). Concentración del FOB:
top 10 = 26,4 %, top 100 = 48,3 %. El 74,0 % de los grupos tiene una sola
variante; el más grande, 257 (`HINCAPIE SPORTSWEAR`, Estados Unidos, cohesión
mínima 0,841). `OTROS` (444 filas) queda aislado en sus propios grupos. La
precisión por banda de esta base **no está medida**: la muestra de 160
asignaciones (`MUESTRA_REVISION`) está lista para etiquetar.

### Dos corridas, dos máquinas

| | contenedor de desarrollo | **Colab Free (Enrique, 11-sep-2026)** |
|---|---:|---:|
| filas de entrada | 211.949 | 211.949 |
| importadores finales | 100.008 (0.22.4) · 99.897 (0.22.3) | **99.898** (0.22.2) |
| reducción | −52,9 % | **−52,9 %** |
| pares candidatos | 14.055.460 (0.22.4) · 14,1 M | 14.058.508 |
| tiempo de emparejamiento | 216 s (0.22.4) · 226 s | **396 s** |
| RSS pico | **1,65 GiB** (0.22.4, por lotes) · 2,6 GiB (0.22.3) | — |
| invariantes | 10/10 OK | **9/9 OK** (0.22.2; la décima se añadió en 0.22.3) |

El resultado coincidió en las dos (99.898 con 0.22.2), y eso era en parte **suerte**:
la normalización de sufijos dependía de la semilla de hash del proceso y la
misma base daba 99.897 o 99.898 según la sesión (un grupo, AVIATECA/GTM).
Desde 0.22.3 es determinista por construcción y está probado en subprocesos con
semillas distintas; el resultado correcto es **99.897** (AVIATECA SUCURSAL COLOMBIA
se une a su grupo). Con 0.22.4 la cifra es **100.008** por la sola razón de los
placeholders (§2), no por el emparejamiento. Las demás cifras de este documento —concentración del FOB,
precisión por banda, recall del bloqueo— se midieron sobre la corrida de 0.22.0 y
un grupo de diferencia no las mueve a la precisión reportada. Lo que cambia es el reloj —Colab Free es ~75 % más
lento— y es la misma razón por la que el banco separa calidad de costo. Cabe en
Colab Free con holgura.

---

## 3. Calidad del emparejamiento

### 3.1 Precisión — 160 asignaciones revisadas a mano

| Banda de similitud | Asignaciones | Precisión estricta | Precisión amplia |
|---|---:|---:|---:|
| 0,84 – 0,88 | 6.898 | 0,825 | 0,925 |
| 0,88 – 0,92 | 4.237 | 0,950 | 1,000 |
| 0,92 – 0,96 | 3.520 | 0,850 | 0,900 |
| 0,96 – 1,00 | 41.966 | 1,000 | 1,000 |
| **Ponderada** | **56.621** | **0,966** | **0,985** |

"Estricta" cuenta los casos dudosos como error. La muestra es aleatoria
estratificada (40 por banda, semilla 42) y se exporta en la hoja
`MUESTRA_REVISION` para que cualquiera repita el conteo.

### 3.2 Recall del bloqueo — medido por fuerza bruta, no estimado

El bloqueo decide qué pares llegan a compararse. Los que no pasan son
invisibles para todas las métricas posteriores: un bloqueo malo produce un
resultado que *parece* impecable. Por eso se midió comparando **todos** los
pares de tres particiones completas:

| País | Nombres | Pares totales | Pares verdaderos | PC (recall) | RR |
|---|---:|---:|---:|---:|---:|
| GTM | 2.681 | 3.592.540 | 301 | **1,000** | 0,942 |
| NLD | 2.630 | 3.457.135 | 478 | **1,000** | 0,956 |
| DEU | 1.683 | 1.415.403 | 230 | **1,000** | 0,975 |

**Esta medición cambió la configuración.** El ajuste que parecía razonable
—64 permutaciones, umbral 0,35— recuperaba solo entre el **67 % y el 81 %** de
los pares verdaderos, sin ningún síntoma visible: menos candidatos, corrida
más rápida, métricas internas iguales. Un cuarto de los empalmes correctos
se perdía en silencio.

### 3.3 Invariantes — se verifican en cada corrida y detienen el notebook

Las nueve pasan: una fila de salida por fila de entrada, ningún grupo cruza
dos países, toda asignación cumple el umbral declarado, cada grupo tiene
exactamente un nombre final, el FOB se conserva (diferencia relativa 1,5·10⁻¹⁶).

---

## 4. Las tres fallas que hubo que corregir

No son hipotéticas: las tres estaban en la primera versión de este pipeline y
salieron al revisar resultados, no al leer código.

**1. Imán genérico.** `INTERNATIONAL` absorbió 157 razones sociales; `MQE`,
214; `COMERCIAL`, 133. Causa: si "A está contenido en B" cuenta como
evidencia, cualquier nombre corto se traga todo lo que lo mencione.
Corrección: la contención exige compartir al menos un token no genérico y
suficientemente distintivo.

**2. El IDF aprendido no distingue marca de genérico.** Medido en esta base:
`IMPORTADORA` (737 nombres) pesa 5,96 de IDF y `ZELECTA` (490 nombres) pesa
6,37 — la marca pesa *menos* que la palabra genérica, porque la frecuencia de
una marca crece con el número de variantes de la misma empresa. Corrección:
la lista de genéricos es un dato declarado y editable (Celda D), no una
inferencia del corpus. Es la misma doctrina que los `LOCALES` de la librería.

**3. Encadenamiento transitivo.** Las componentes conexas son single-linkage:
si `a≈b` y `b≈c`, unen `a` con `c` aunque no se parezcan. Produjo grupos de
200 empresas distintas encadenadas por prefijos genéricos. Corrección:
cobertura por estrellas, que garantiza que **todo miembro está a ≤ 0,16 de su
nombre final** — exactamente lo que la correlativa afirma.

Y una cuarta, de calibración: el umbral inicial de 0,88 era demasiado
estricto. Revisar 40 pares del tramo 0,84–0,88 mostró **92,5 % de aciertos**:
se estaban descartando cerca de 7.000 empalmes correctos. Bajarlo a 0,84 es
la decisión mejor respaldada de toda la configuración.

---

## 5. Hallazgos sobre los datos (no sobre el método)

**El 20,5 % del valor FOB no tiene destinatario identificable.** 53 registros
con razón social `"0"` concentran **USD 42.797 millones**. Es el patrón típico
de destinatario reservado en oro, carbón y petróleo. Quedan marcados como
`sin_nombre_utilizable` y **nunca se fusionan entre sí**: unirlos inventaría
una empresa gigante inexistente. Cualquier ranking, participación de mercado o
análisis de concentración que ignore esta quinta parte del valor está mal.

**76 grafías del campo "país de destino final" no son países** sino zonas
francas colombianas (`ZONA FRANCA PERMANENTE…`, `ZFP…`, `ZFPE…`): 397 filas y
USD 3.297 millones (1,58 %). Mezclarlas con destinos reales contamina
cualquier lectura por mercado.

**El campo país tenía 529 grafías para 203 países.** El 58,3 % de las filas
cambió de valor de país al canonizar. Los pares que ninguna similitud de
cadenas resuelve —`TURQUÍA`/`TÜRKIYE`, `CHEQUIA`/`REPÚBLICA CHECA`,
`YIBUTI`/`DJIBOUTI`, `COSTA DE MARFIL`/`CÔTE D'IVOIRE`— son la razón de que el
catálogo sea declarado y no inferido. Y los que *sí* se parecen —`GUINEA` /
`GUINEA ECUATORIAL` / `GUINEA-BISSAU`, `CONGO` / `REPÚBLICA DEMOCRÁTICA DEL
CONGO`— son países distintos: el fuzzy matching los habría unido.

**La dispersión de grafías es masiva en los grandes.** `ES WINDOWS`: 36
grafías, USD 1.878 millones. `DILLON GAGE`: 13 grafías, y la mayor por sí sola
es apenas el **45 %** del total consolidado — en la base cruda esa empresa
aparece con menos de la mitad de su tamaño real.

---

## 6. Lo que este resultado NO es

- **No resuelve personas jurídicas, resuelve grupos comerciales por destino.**
  `BARRY CALLEBAUT USA` y `BARRY CALLEBAUT CANADA` quedan juntos; también
  `LATAM AIRLINES GROUP` con `LATAM AIRLINES ECUADOR` y `KOREA EAST-WEST
  POWER` con `KOREA SOUTH-EAST POWER`. Es consecuencia directa de tratar la
  geografía como ruido —lo que a su vez permite unir `NETAFIM QUITO` con
  `NETAFIM ECUADOR S.A.`—. Se apaga con `GEOGRAFIA_ES_RUIDO = False`, y ese
  es el modo de error dominante de la banda 0,92–0,96 (4 de 40 revisadas).
- **No une empresas entre países.** Por diseño: el país es variable de
  empalme. `INTEROCEAN COAL SALES` aparece siete veces, una por destino. La
  columna `ID_EMPRESA_GLOBAL` las une, pero solo por coincidencia **exacta**
  del nombre normalizado.
- **Los consolidadores de carga se quedan con su clientela.**
  `X PRESS SHAPEWEAR LL ‹cliente›` cae en el grupo de la marca (49 grafías).
  El destinatario declarado ante la DIAN *es* el consolidador, así que el
  resultado es defendible, pero si le interesa el cliente final no sirve.
- **La precisión de 0,966 es una estimación con 160 observaciones**, no un
  censo. El intervalo de confianza al 95 % sobre la banda peor medida
  (0,84–0,88, n=40, p=0,825) va de 0,67 a 0,93. Para cerrarlo hacen falta más
  etiquetas, no más código.

---

## 7. Reproducir y recalibrar

```
notebooks/07_deduplicar_importadores_razon_social_pais.ipynb
  Celda A  entorno            Celda B  parámetros      ← lo que se edita
  Celda C  catálogo de países Celda D  listas de limpieza
  Celda E  motor              F ejecutar · G auditar · H exportar
```

Para recalibrar: cambie **un** parámetro, corra, y vuelva a contar la muestra
de `MUESTRA_REVISION`. La hoja `SENSIBILIDAD` muestra, sin volver a correr
nada, cuántas fusiones añade o quita cada umbral:

| umbral | pares fusionados | vs. defecto |
|---:|---:|---:|
| 0,80 | 60.261 | +27.753 |
| 0,82 | 50.853 | +18.345 |
| **0,84** | **32.508** | **0** |
| 0,88 | 15.115 | −17.393 |
| 0,92 | 9.976 | −22.532 |

---

## 8. Método

Fellegi, I. & Sunter, A. (1969). *A Theory for Record Linkage*. JASA 64(328),
1183–1210. — Spärck Jones, K. (1972). *A statistical interpretation of term
specificity and its application in retrieval*. Journal of Documentation 28(1).
— Winkler, W. (1990). *String comparator metrics and enhanced decision rules
in the Fellegi-Sunter model of record linkage*. — Wagstaff, K. & Cardie, C.
(2000). *Clustering with instance-level constraints*. ICML. — Christen, P.
(2012). *Data Matching*. Springer.
