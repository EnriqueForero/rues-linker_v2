# ADR-0009 — Cuando el nombre es toda la evidencia

**Fecha:** 2026-09-11 · **Versión:** 0.22.0 · **Estado:** aceptada

## Contexto

Una base de 211.949 destinatarios de exportación con dos columnas —razón
social y país— y ningún identificador. Es el régimen SIN_NIT llevado a su
extremo: no hay NIT que vetar (ADR-0004) ni evidencia independiente que
corrobore un veto (F3). El nombre es todo.

Correr ese caso con los defectos de 0.21.0 —`JaroWinklerSigned` y
`clusters_desde_decisiones`— produjo grupos absurdos sobre datos reales:
`INTERNATIONAL` con 157 razones sociales dentro, `MQE` con 214, `COMERCIAL`
con 133, y grupos de 200 empresas encadenadas por prefijos genéricos.

Ninguno de esos fallos era visible en las métricas internas. La corrida se veía
impecable: reducción alta, cohesión aceptable, invariantes de fila cumplidas.
Aparecieron mirando los grupos más grandes, uno por uno.

## Decisión

Cuatro decisiones, cada una atada a una falla medida.

### 1. La contención es la excepción, no la regla

La medida base de tokens es **Jaccard ponderado por IDF**, que es simétrica: no
premia que un nombre esté contenido en otro. La contención —que sí lo premia—
entra **solo** cuando el par pasa tres puertas:

| Puerta | Qué exige | Falla que cierra |
|---|---|---|
| `max_diferencia_informativa` | lo que sobra en el nombre largo es ruido declarado | `MQE` absorbía 214 |
| `min_informativos_compartidos` | el nombre corto tiene identidad propia | `INTERNATIONAL` absorbía 22 |
| `fraccion_max_distintivo` | lo compartido es distintivo | `KA DK FLOWERS` = `E FLOWERS` por "FLOWERS" |

### 2. Los genéricos se declaran, no se infieren

Medido sobre 105.705 razones sociales: `IMPORTADORA` aparece en 737 nombres y
pesa **5,96** de IDF; `ZELECTA` aparece en 490 y pesa **6,37**. La marca pesa
menos que el genérico.

La causa es estructural, no un accidente del dataset: la frecuencia documental
de una marca **crece con el número de variantes de la misma empresa**, que es
justo lo que estamos tratando de deduplicar. El corpus castiga a la marca por
el motivo equivocado, y ningún umbral sobre el IDF aprendido separa los dos
casos — se solapan.

Por eso `matching.genericos` es un dato versionado y editable, igual que
`normalizadores.LOCALES` y que `paises.catalogo`. No es una preferencia
estética: es la única forma de que la distinción sea auditable.

### 3. El país se canoniza contra un catálogo, no por parecido

En la base real había 529 grafías para 203 países. Dos hechos que hacen
inviable el fuzzy matching, y que están fijados como pruebas de regresión:

- Pares que **son el mismo país y no se parecen**: `TURQUIA`/`TURKIYE`,
  `CHEQUIA`/`REPÚBLICA CHECA`, `YIBUTI`/`DJIBOUTI`,
  `COSTA DE MARFIL`/`CÔTE D'IVOIRE`.
- Pares que **se parecen mucho y son países distintos**: `GUINEA` /
  `GUINEA ECUATORIAL` / `GUINEA-BISSAU`, `CONGO` / `REPÚBLICA DEMOCRÁTICA DEL
  CONGO`, `COREA DEL SUR` / `COREA DEL NORTE`.

`sugerir_alias_pais` existe para ayudar a **editar** el catálogo, y su propia
prueba deja el contraejemplo: `NARNIA` se parece a `ARMENIA` con 0,85.

Además, 76 grafías del campo no eran países sino zonas francas colombianas.
Se marcan aparte: mezclarlas con destinos reales contamina cualquier lectura
por mercado.

### 4. La correlativa afirma algo verificable

`clusters_desde_decisiones` agrupa por componentes conexas, que es
single-linkage. Con identificador casi no importa —el veto corta el puente—,
pero deduplicando solo por nombre el encadenamiento es el modo de falla
dominante.

`engine.cobertura` reparte cada componente en estrellas, con la garantía de
que **todo miembro queda a ≤ (1 − umbral) de su líder**. Es exactamente lo que
la tabla correlativa afirma cuando dice "este nombre original corresponde a
este nombre final". Antes, la tabla afirmaba algo que el pipeline nunca había
comprobado. Es la misma lección de ADR-0008 —si algo es entregable, va en las
invariantes— aplicada a la asignación, no solo a las columnas.

## Alternativas descartadas

- **Subir el umbral y ya.** Probado: con 0,92 los imanes genéricos siguen
  ahí (un nombre contenido en otro da contención 1,0 a cualquier umbral) y se
  pierden miles de empalmes correctos.
- **Quitar los genéricos del nombre en vez de ponderarlos.** Borrar
  "COMERCIALIZADORA" destruye información que sirve para desempatar y puede
  dejar un nombre vacío. Ponderar conserva y decide.
- **Inferir los genéricos del corpus por frecuencia.** Es exactamente lo que
  `remove_top_words` hacía antes de 0.7.6 y lo que dejó al régimen SIN_NIT en
  el filo de percolación. La §2 explica por qué no puede funcionar aquí.
- **Fuzzy matching para el país.** Uniría GUINEA con GUINEA-BISSAU y no uniría
  TURQUIA con TURKIYE. Lo peor de ambos lados.
- **Complete-linkage en vez de estrellas.** Es O(k²) sobre la componente y no
  da un representante natural; la cobertura por estrellas da el líder, que es
  el nombre final, y la garantía que la correlativa necesita.

## Consecuencias

**A favor.** El caso sin identificador queda cubierto con una precisión
ponderada medida de 0,966 sobre 160 asignaciones revisadas a mano, y el notebook
queda delgado porque la lógica vive en `flujo.importadores` con pruebas.

**En contra, y declarado.** Resuelve **grupos comerciales por destino**, no
personas jurídicas: `BARRY CALLEBAUT USA` y `BARRY CALLEBAUT CANADA` quedan
juntos. Es consecuencia directa de tratar la geografía como ruido — lo que a su
vez permite unir `NETAFIM QUITO` con `NETAFIM ECUADOR S.A.`. La perilla
`geografia_es_ruido` expone el intercambio; no hay un defecto correcto para
todos los usos y no se pretende que lo haya.

**Costo de mantenimiento.** Tres diccionarios más que envejecen (países,
genéricos, sufijos). Es el precio de que la distinción sea auditable en vez de
emergente.

**Costo por par (0.22.4).** Sin identificador, el bloqueo propone muchos más
pares de los que sobreviven: la partición USA de la base real trajo 24,5 M de
candidatos para 54.669 nombres, y puntuarlos de una vez costó 11,4 GiB. El
motor puntúa por lotes (`pares_por_lote`) —el score de un par no depende de
otro, así que el resultado es idéntico— y la tabla de decisiones conserva solo
lo auditable, con índices y no nombres. Es una decisión de costo, no de calidad,
y está probada como tal.

## Referencias

- Spärck Jones, K. (1972). *A statistical interpretation of term specificity*.
  Journal of Documentation 28(1). doi:10.1108/eb026526
- Winkler, W. (1990). *String comparator metrics and enhanced decision rules in
  the Fellegi-Sunter model of record linkage*.
- Wagstaff, K. & Cardie, C. (2000). *Clustering with instance-level
  constraints*. ICML.
- Christen, P. (2012). *Data Matching*. Springer. doi:10.1007/978-3-642-31164-2
- ADR-0003 (IDF por régimen), ADR-0004 (identificador canónico),
  ADR-0008 (contrato de salida de la correlativa).


## Addendum (0.22.3) — lo que el bloqueo duro exige del catálogo

La decisión de usar el país como bloqueo duro con un catálogo **declarado**
tiene una consecuencia que no se había medido: una grafía que el catálogo no
reconoce **desactiva el bloqueo** para todas las filas que la comparten. Con la
etiqueta única `SIN CLASIFICAR`, la misma razón social bajo tres grafías
distintas (`NO DEFINIDO`, `SIN INFORMACION`, `TERRITORIO X`) se fusionó en un
solo importador — y la invariante «ningún grupo cruza dos países» dijo OK,
porque mira el ISO3 y los tres eran `ZZZ`.

Se fijan tres cosas:

1. El hueco se detecta en `preparar()`, **antes** de emparejar, con la lista
   completa de grafías y los remedios (`paises_sin_clasificar="detener"`,
   defecto). Una invariante que acierta minutos tarde y sin decir qué falta no
   es un detector; es una red.
2. Si una grafía no tiene sentido catalogarla, se **aísla**: conserva su propio
   `PAIS_FINAL`, que es el campo categórico que veta la fusión
   (`"aislar"`). Nunca se asigna al país «más parecido»: `GUINEA` y
   `GUINEA-BISSAU`.
3. Lo que se aísla se **mide**: la décima invariante, «ningún grupo mezcla dos
   grafías de país», exige `PAIS_FINAL` constante dentro de cada importador.

Y un defecto latente que esto destapó: la unión base↔representantes iba por
`(ISO3, nombre)` cuando la identidad del representante es
`(ISO3, PAIS_FINAL, nombre)`. `agrupar_no_pais=False` lo tenía desde 0.22.0;
nadie lo pisó porque el defecto es `True`.
