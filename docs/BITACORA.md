# Bitácora de mejora — rues-linker

Registro cronológico de cada intervención: qué se midió, qué se cambió, qué
evidencia la sostiene y qué quedó pendiente. Una entrada por iteración.

Regla de la bitácora: **ninguna mejora entra sin una corrida del banco antes
y otra después**. Si la comparación no pasa, la entrada registra el fallo y
el ajuste, no se borra.

El banco se corre siempre igual:

```bash
python scripts/banco.py --etiqueta <nombre> [--perfil P] [--variables-extra ...]
python scripts/banco.py --comparar base_0174 <nombre>
```

Conjunto de referencia: `data/ground_truth/ground_truth_grande.csv`
— 12.427 registros · 3.486 grupos · 5 fuentes · 22.073 pares verdaderos ·
CON_NIT 81 % / SIN_NIT 19 % · 43 registros diseñados como casos negativos.

---

## IT-00 · Línea base (0.17.4)

**Fecha:** 2026-08-29 · **Etiqueta:** `base_0174`

Sin cambios de código. Establece el punto de comparación de todo lo que sigue.

| Dimensión | Valor |
|---|---|
| precision / recall / F1 | 0,9760 / 0,9560 / **0,9659** |
| B³ precision / recall / F1 | 0,9918 / 0,9679 / **0,9797** |
| TP / FP / FN | 21.101 / 519 / 972 |
| Grupos predichos vs verdad | 3.731 vs 3.486 |
| **FP que tocan un caso negativo** | **62** |
| Recall CON_NIT | 0,9885 |
| **Recall SIN_NIT** | **0,8344** |
| Tiempo total | 24,9 s |
| — L2 (bloqueo) | 19,7 s · **79 % del total** |
| Pico de RSS | 420 MiB |
| Candidatos → válidos | 849.825 → 20.721 (**41:1**) |
| Huella de la partición | `c9d30223fd6ce9ae…` |

**Determinismo verificado:** dos corridas consecutivas produjeron la misma
huella y las mismas métricas al cuarto decimal.

**Lo que la línea base señala como prioridad**

1. `SIN_NIT` tiene 15 puntos menos de recall que `CON_NIT`. Es la brecha de
   calidad más grande y no está causada por umbrales sino por falta de
   evidencia: sin identificador, el único campo que decide es el nombre.
2. 62 falsos positivos tocan registros diseñados como negativos. Son los
   errores caros: precisión aparente alta (0,976) con fuga concentrada.
3. L2 consume el 79 % del tiempo. Cualquier mejora de velocidad que no toque
   L2 es cosmética.
4. La sobre-fragmentación (3.731 grupos frente a 3.486) es coherente con los
   972 falsos negativos: el sistema parte grupos que deberían estar unidos.

---

## IT-01 · Registro de comparadores y comparadores de contacto

**Fecha:** 2026-08-29 · **Etiquetas:** `paridad_registro`, `contacto_005`

### Qué se verificó primero

Declarar TELEFONO y EMAIL como evidencia adicional producía una corrida
**bit-idéntica** a la línea base: misma huella `c9d30223…`, mismos 21.101 TP.
La instrumentación del scorer mostró que la contribución sí se calculaba
(17.063 valores no nulos), pero el comparador era exacto y dentro de un mismo
grupo el teléfono aparece así:

```
753038943  SERVICIOS DEL VALLE        312 1897799     SERVICIOSDEL@hotmail.com
753038943  SERVICIOS DEL VALLE ESAL   312-189-7799    serviciosdel.co@hotmail.com
753038943  SERVICIOS DEL VALLE        +573121897799   serviciosdel@hotmail.com
```

El comparador exacto los declara distintos y **penaliza a los pares
verdaderos**. La evidencia estaba; el instrumento para leerla, no.

### Qué se hizo

- `matching/comparadores_extra.py`: registro de comparadores. Los seis
  heredados se movieron ahí con paridad bit-a-bit; se añadieron
  `telefono_signed`, `email_signed` y `documento_signed`, que canonicalizan
  antes de comparar.
- `deduplication/unified.py`: la lista de tipos válidos, que estaba escrita a
  mano y duplicada, ahora se lee del registro.
- `engine/scorer.py`: la cadena de 118 líneas de `if ftype == ...` se
  reemplazó por una consulta al registro.

Detalle en [ADR-0002](adr/0002-registro-de-comparadores.md).

### Evidencia

| | resultado |
|---|---|
| Paridad de los 6 comparadores heredados | bit-a-bit sobre 3.000 pares con nulos, espacios y caja mixta |
| Huella de la partición sin variables extra | `c9d30223…` — idéntica a la línea base |
| Suite relacionada | 58 aprobadas |

### Qué NO mejoró

La calidad no se movió: `contacto_005` da exactamente la línea base. Los
comparadores nuevos funcionan —hay pruebas que lo verifican valor por valor—
pero con peso 0,05 su contribución no alcanza a mover ningún par a través del
umbral. **El instrumento quedó disponible; el problema de calidad estaba en
otra parte.** Eso lo dijo el diagnóstico siguiente.

---

## IT-02 · Diagnóstico: dónde están realmente los errores

**Fecha:** 2026-08-29 · **Sin cambios de código**

Antes de seguir tocando perillas, se clasificaron los 972 falsos negativos y
los 519 falsos positivos de la línea base.

### Por régimen

| | falsos negativos | falsos positivos |
|---|---|---|
| SIN identificador × SIN identificador | 772 (79 %) | **519 (100 %)** |
| CON × CON | 200 (21 %) | 0 |

### Por etapa donde se pierde el par verdadero

| etapa | pares | % |
|---|---|---|
| **Nunca fue candidato (bloqueo)** | **705** | **72,5 %** |
| Fue candidato pero no pasó el score | 267 | 27,5 % |
| Pasó el score pero no quedó agrupado | 0 | 0 % |

**Conclusión que cambió el plan de trabajo:** el cuello no está en el scoring
sino en el **bloqueo**, y todo el error de calidad vive en el régimen sin
identificador. Subir la similitud de nombre no podía servir de mucho porque
esos pares nunca llegaban al scorer — y efectivamente, la similitud compactada
aplicada sola movió el F1 de 0,9659 a 0,9663.

### Las dos formas del error

```
Falsos positivos  → tokens genéricos compartidos
   HWANGJUNG TECH LLC        vs  HANJUNG TECH LLC
   JINSHIN LOGIS CO.,LTD.    vs  SHINSHIN LOGIS CO.,LTD.

Falsos negativos  → el espacio movido rompe la tokenización
   CHOIMIN GLOBAL CORP       vs  CHOIMING LOBAL INC
   HANYOO GLOBALCO., LTD     vs  HANYOO GLOBAL CO. LTD
```

---

## IT-03 · IDF por régimen, similitud compactada y bloqueo abierto

**Fecha:** 2026-08-29 · **Evidencia:** `docs/evidencia/corrida_*.json`

### Barrido

| etiqueta | configuración | precision | recall | F1 | R SIN_NIT | seg |
|---|---|---|---|---|---|---|
| `base_0174` | — | 0,9760 | 0,9560 | 0,9659 | 0,8344 | 25,6 |
| `idf_sin_05` | IDF sin-id 0,50 | **1,0000** | 0,8562 | 0,9225 | 0,3617 | 24,5 |
| `idf_sin_010` | IDF sin-id 0,10 | 0,9971 | 0,9355 | 0,9653 | 0,7374 | 25,6 |
| `comp_096` | compacta 0,96 | 0,9760 | 0,9568 | 0,9663 | 0,8382 | 25,7 |
| `lsh_045` | bloqueo 0,45 | 0,9421 | **0,9764** | 0,9590 | **0,9313** | 38,8 |
| `combo_a` | 0,45 + IDF 0,05 | 0,9905 | 0,9629 | 0,9765 | 0,8672 | 38,8 |
| **`combo_b`** | **0,45 + IDF 0,05 + compacta 0,96** | **0,9866** | **0,9733** | **0,9799** | **0,9165** | 39,8 |
| `veto_a` | + veto IDF 0,30 | 0,9415 | 0,9578 | 0,9496 | 0,8430 | 40,4 |

Ninguna perilla mejora el F1 por separado. Las tres juntas sí, y en las dos
direcciones a la vez.

### Validación fuera de muestra

Tres pliegues repartidos **por grupo**:

| pliegue | F1 base | F1 nueva | Δ |
|---|---|---|---|
| 0 | 0,9763 | 0,9869 | +0,0106 |
| 1 | 0,9701 | 0,9834 | +0,0133 |
| 2 | 0,9755 | 0,9832 | +0,0077 |
| **media** | | | **+0,0105** |

Contra +0,0140 en el conjunto completo, donde se calibró. La diferencia de
0,0035 está muy por debajo del criterio de 0,05: **no es sobreajuste**.

### Qué se hizo

- `matching/idf.py`: ponderación de tokens por informatividad, vectorizada con
  matrices dispersas. Reemplaza un bucle de Python por par.
- `engine/scorer.py`: perillas `idf_weight_blend_sin_identificador`,
  `idf_veto_min_sin_identificador` y `similitud_compacta_min`. Las tres con
  valor 0,0 por defecto — paridad verificada por huella de partición.
- `config/profiles.py`: perfil `fuentes_mixtas` que empaqueta el punto de
  operación calibrado.

Detalle en [ADR-0003](adr/0003-idf-por-regimen.md).

### Qué NO mejoró, y hay que decirlo

Los falsos positivos que tocan un caso negativo **subieron de 62 a 111**. Son
intermediarios comerciales con nombre casi idéntico usados por clientes
distintos (`COMMERCIAL ZELECTA TRADING GROUP CORP`). El bloqueo más abierto los
expone y el IDF no alcanza a separarlos.

Se probó y se descartó la hipótesis de que la frecuencia del nombre los
identificara: los intermediarios aparecen 1 o 2 veces, no muchas. Desde el
nombre solo no son separables. Queda como limitación conocida, no como cosa
resuelta.

El costo en tiempo es **+55 %** (25,6 s → 39,8 s). Por eso el punto de
operación vive en un perfil aparte y `produccion_estandar` no cambió.

---

## IT-04 · Veredicto formal y cierre de la versión 0.18.0

**Fecha:** 2026-08-29

```
COMPARACIÓN   base_0174  →  perfil_mixtas
  ✅ precision              0,9760 → 0,9866   +0,0106
  ✅ recall                 0,9560 → 0,9733   +0,0173
  ✅ f1                     0,9659 → 0,9799   +0,0140
  ✅ b3_f1                  0,9797 → 0,9868   +0,0071
  ❌ segundos_total           25,6 →   41,4   +61 %      (≤ base × 1,2)
  ✅ rss_pico_mib            406,7 →  466,5   +15 %
  ❌ fp_que_tocan_negativo      62 →    111   +49        (≤ 0)
  VEREDICTO: FALLA
```

**El veredicto dice FALLA y se publica igual, con el detalle a la vista.** Los
dos ❌ son concesiones declaradas, no descuidos:

- **+61 % de tiempo** es el precio medido de abrir el bloqueo. Está en el
  comentario del perfil, en el ADR y en el README. Quien no elija el perfil no
  lo paga: `produccion_estandar` no cambió.
- **+49 falsos positivos sobre casos negativos** son intermediarios
  comerciales con nombre casi idéntico. Se probó y se descartó la hipótesis de
  la frecuencia del nombre. Desde el nombre solo no son separables.

Un comparador que se ajusta para que todo pase deja de servir. Los umbrales se
quedan como están y la concesión se argumenta en la documentación, que es
donde un lector puede discutirla.

### 5. El que no habría aparecido leyendo el código

Los cuatro anteriores salieron de leer y razonar. Este no: salió de **correr
`preparar_build()` contra el árbol real y cotejar el resultado archivo por
archivo** contra el origen.

```
ARCHIVOS EN EL REPO : 420
ARCHIVOS EN EL BUILD: 404
NO LLEGARON         : 16
```

Ningún error, ninguna excepción. La celda imprime "Build listo". Los 16 son
los 7 CSV del conjunto de conformidad, el ground truth y los 6 fixtures de la
suite — o sea, **todo lo que hace falta para que el repositorio publicado
pueda medirse a sí mismo**. El CI habría estado rojo desde el primer commit y
el diagnóstico habría apuntado a los tests.

### 5bis. Una hipótesis que resultó falsa, y por qué se deja escrita

Al arreglarlo supuse un segundo defecto encadenado: que aunque los archivos
llegaran al build, el `.gitignore` generado los ignoraría, porque git **no
desciende a un directorio excluido** y `data/*` está ignorado. Escribí la
corrección y la documenté como defecto.

**Era falsa.** El `.gitignore` *generado* no contiene `data/*` — esa línea es
una adición a mano del archivo que vive en el repositorio, y el generador nunca
la produce. Se comprobó saboteando el generador a propósito (quitándole la
re-inclusión de directorio) y contando lo que git commiteaba: **los 8 archivos
de `data/conformidad/` entraban igual**.

La corrección se queda, pero cambia de categoría: no arregla un defecto
observado, protege el caso en que alguien añada `data` a `EXCLUIR_DIRS_EXTRA`.
Se deja escrito porque el error de método es el interesante: acababa de
encontrar el hallazgo 5 **ejecutando y cotejando**, y a los diez minutos
documenté como defecto medido algo que solo había razonado. La disciplina no
se sostiene sola; hay que aplicarla también a la corrección.

**Lección metodológica, y es la que vale de las cinco.** Los hallazgos 1 a 4
se encontraron leyendo. Este exigía ejecutar y **comparar contra el origen**.
Un pipeline que transforma archivos hay que auditarlo por diferencia, no por
lectura: el modo de fallo característico no es una excepción, es una omisión
silenciosa que se anuncia como éxito. Y la verificación tiene que llegar hasta
el final: aquí se hizo `git init && add && commit` sobre el build y se **clonó**
para correr la suite sobre lo que GitHub recibiría de verdad, no sobre el
directorio de trabajo.

| verificación sobre el clon | resultado |
|---|---|
| archivos | 426 |
| `pytest tests/test_conformidad_v020.py` | **45 pasan, 0 saltos** |
| los 23 archivos de test que dependen de fixtures | **165 pasan, 0 fallan, 0 se saltan** (4 min 9 s) |
| `scripts/conformidad.py --corroborar` | PASA |
| `ruff check src/ tests/ scripts/` | limpio |

Los 23 archivos se eligieron por grep de las rutas de datos
(`tests/data`, `data/ground_truth`, `data/benchmark`, `golden_truth`,
`oraculo_scorer`, …), no a ojo: son exactamente los que el build mutilado
habría dejado sin datos.

**Corrección:** `DIRECTORIOS_VERSIONADOS` declara directorios y los expande
contra el árbol real (un fixture nuevo se publica solo, y un directorio ausente
aborta); y `preparar_build()` **aborta** si falta algo en vez de avisar.
Verificado con el escenario adverso: quitando `data/conformidad/` del árbol, la
celda para y lista los 19 archivos ausentes.

### Estado al cerrar

| | |
|---|---|
| Suite | 1.252 aprobadas · 2 omitidas · 0 fallos |
| Pruebas nuevas | 78 (43 comparadores e IDF · 31 banco · 4 perfil) |
| `ruff check` / `format` | limpios |
| `twine check` | PASSED |
| Corridas de evidencia | 24 JSON en `docs/evidencia/` |
| Criterios del plan | 8 ✅ · 6 🟡 · 16 ⬜ |

### Lo que sigue (ver `docs/PLAN.md`)

1. **C01** — el diagnóstico de identificadores del notebook reporta una cifra
   calculada sobre una muestra sesgada y presentada como exacta. Es P0 porque
   hoy induce a error a quien la lee.
2. **C02 + C03** — L5 gasta el 30 % de una corrida real procesando grupos de
   una sola fila. No se ve en un banco de 12 K registros; se ve mucho a 4,4 M.
3. **C25** — 97 errores de mypy con el job en `continue-on-error`.
4. **C12** — el banco existe y devuelve código de salida; falta que CI lo corra.

---

# v0.19.0 — El conjunto de referencia institucional

> Objetivo declarado por Enrique: *"llegar a una base que pueda ser de base
> para hacer pruebas e institucionalizarla… que sea multicriterio y variable,
> no sólo el NIT y Razón Social sino otras variables como departamento, CIIU,
> Dirección, teléfono."*

## IT-05 · Contrastar los dos archivos y decidir

Se recibieron `Ground_Truth_Robusto_V3.xlsx` y
`2026_08_27_Tamaño_Empresas_CRM.xlsx`. La pregunta era cuál sirve. La
respuesta medida es **ninguno solo**, y por razones distintas:

| | Ground_Truth_Robusto_V3 | CRM × RUES × SSC |
|---|---|---|
| realismo | sintético | **real** |
| certeza de la etiqueta | **por construcción** | es la salida de otro cruce |
| columnas | NIT, RAZON_SOCIAL | 33, con geografía, CIIU, tamaño |
| negativos | 606 diseñados | ninguno etiquetado |
| defecto | corrompe el identificador | no es verificación independiente |

Del CRM se usan solo las 50.997 filas `Alta Confianza` ancladas en
identificador. Las 5.122 de `Revisión Manual` se **excluyen**: una etiqueta
dudosa envenena la métrica en las dos direcciones, no en una.

**Medición que cambió el diseño.** Sobre 56.119 pares reales, el **21,7 % de
los enlaces verdaderos no comparte ningún token** entre el nombre del CRM y el
del RUES. Casi siempre nombre comercial contra persona natural comerciante
(`FRESH PISCINAS` ↔ `SANCHEZ LOPEZ FREDY`). Para ese quinto de los enlaces el
nombre no aporta nada. El muestreo del estrato REAL es estratificado por
categoría de variación para conservar esa proporción, en vez de una inventada.

## IT-06 · La contradicción interna, medida y corregida

El primer conjunto armado daba F1 0,8177 con **recall CON_ID 0,687, peor que
SIN_ID 0,834**. Un absurdo así no se explica por el algoritmo.

Causa: Ground_Truth_Robusto_V3 aplica ruido al identificador. En 899 de 1.800
grupos (49,9 %) el NIT no se recupera con ninguna canonicalización, y esos
grupos concentran 17.055 de los 18.629 pares del estrato — el 36,8 % de todos
los pares del conjunto. Pedían unir registros con identificadores distintos, y
los 1.040 negativos exigen lo contrario. **Ninguna configuración podía sacar
F1 = 1.**

Corrección: donde el identificador se recupera canonicalizando se conserva
(`CON_ID`); donde hay corrupción de dígitos se borra y el grupo pasa a
`SIN_ID` con caso `positivo_ruido_sin_id`. Se conserva lo que el archivo sí
sabe aportar y se descarta lo que no se puede sostener.

| | con la contradicción | corregido |
|---|---:|---:|
| F1 | 0,8177 | **0,8745** |
| recall CON_ID | 0,6867 | **0,9657** |

**La lección:** un conjunto de referencia hay que auditarlo con la misma
severidad que al código. Este estuvo mal seis meses y nadie lo notó porque
nadie miró el recall por régimen.

## IT-07 · Métricas por estrato: dejar de promediar peras con manzanas

`RUIDO` tiene 7.368 registros y 19.305 pares; `REAL` tiene 9.101 registros y
4.996 pares. Los pares crecen con el **cuadrado** del tamaño de grupo, así que
el estrato con grupos grandes decide el F1 global y esconde a los demás.

El banco ahora reporta precisión, recall y F1 **por estrato** más un
**macro-F1** que pesa igual a cada uno. Con eso, el diagnóstico se lee de un
golpe:

```
  ESTRATO        CONTACTO     F1 0.9659  P 0.9760  R 0.9560  (22,073 pares)
  ESTRATO        REAL         F1 0.9344  P 1.0000  R 0.8769  (4,996 pares)
  ESTRATO        RUIDO        F1 0.7454  P 0.8786  R 0.6472  (19,305 pares)
```

`CONTACTO` da 0,9659 — exactamente el F1 que 0.18.0 reportaba sobre
`ground_truth_grande.csv`. La consistencia confirma que el estrato es el mismo
conjunto y que la caída del global viene de dificultad nueva, no de un error.

## IT-08 · Multicriterio: el comparador es parte del diseño

Primer intento, declarando las columnas con los comparadores existentes:

| configuración | F1 | macro-F1 |
|---|---:|---:|
| sin variables extra | **0,8745** | **0,8819** |
| + geografía (`categorical_signed`) | 0,8441 | 0,8620 |
| + las cinco | 0,8464 | 0,8638 |

Añadir geografía **quitó 3 puntos**. La conclusión tentadora —la geografía no
sirve— es falsa. El desacuerdo dominante era `BOGOTA` contra `BOGOTA D C`:
15.781 casos, casi un tercio.

Con contención de tokens en vez de igualdad exacta, la separación entre pares
verdaderos y negativos duros pasa de 40,3 pp a **69,9 pp** en DEPARTAMENTO y
de 30,1 a **67,7** en MUNICIPIO. Y el CIIU, que no es un valor sino una lista
(2,52 actividades en RUES contra 1 en SuperSociedades), pasa de reconocerse en
el 18,1 % de los pares verdaderos a **99,3 %** usando solapamiento en vez de
igualdad — con Jaccard sería 19,5 %, peor que inútil.

> **Corrección de un juicio anterior.** En una nota previa concluí que
> "departamento coincide solo el 53 % y es una variable floja". La cifra
> estaba mal calculada y la conclusión estaba mal fundada: era el comparador,
> no la variable. Queda anotado porque el error importa más que el acierto.

Se implementaron `categoria_tolerante_signed` y `conjunto_signed` (ADR-0005).
`conjunto_signed` sobre CIIU baja los FP de 2.505 a 2.429 sin tocar el recall.

**Pero la geografía sigue sin ayudar end-to-end, y ahí está el hallazgo
arquitectónico.** Recall de bloqueo contra recall final por estrato:

| estrato | bloqueo | final | dónde se pierde |
|---|---:|---:|---|
| REAL | 0,982 | 0,877 | **scoring** |
| CONTACTO | 0,959 | 0,956 | bloqueo |
| RUIDO | 0,730 | 0,647 | **bloqueo** |

En RUIDO y CONTACTO el par verdadero **nunca llega a ser candidato**. Ninguna
perilla del scorer lo rescata. La palanca correcta es bloqueo multivariable
(C27), no evidencia adicional en el scorer.

## IT-09 · El dígito de verificación partía entidades en dos

Persiguiendo los 533 pares del estrato REAL que morían después de ser
candidatos, el patrón saltó a la vista:

```
NIT 102829482 / 10282948     FRESH PISCINAS         / SANCHEZ LOPEZ FREDY
NIT 8002156345 / 800215634   Harold Salazar Y Cia   / HSC INGENIERIA...
NIT 912490524 / 91249052     PAOLINI                / PRADA PARADA WILLIAM
```

El mismo número con y sin dígito de verificación. Medido: de los 5.312 pares
con identificadores textualmente distintos dentro de un grupo, **5.269
(99,2 %)** son esto, y **5.002** validan por módulo 11.

La librería los trataba como distintos en **tres** lugares. El tercero
—`aplicar_cannot_link_identificador` en L5— **deshacía lo que L4 acababa de
unir bien**: L4 producía 12.232 grupos y L5 los volvía 13.344. Arreglar solo
el scorer no cambió una sola métrica; el arreglo no sirve hasta que están los
tres.

Riesgo medido antes de tocar nada: sobre 145.082 identificadores reales,
colapsar `X` con `X+DV` produce **cero** colisiones entre números ajenos, a
cualquier profundidad probada.

| métrica | antes | después | Δ |
|---|---:|---:|---:|
| F1 | 0,8745 | **0,8780** | +0,0035 |
| macro-F1 | 0,8819 | **0,8920** | +0,0101 |
| F1 REAL | 0,9344 | **0,9648** | +0,0304 |
| recall REAL | 0,8769 | **0,9319** | +0,0550 |
| precisión REAL | 1,0000 | **1,0000** | 0 |
| FP sobre negativos | 287 | **287** | 0 |

TP +275, FP +0, FN −275. Fuera de muestra en 3 pliegues: +0,0035 y +0,0303,
**idéntico a la muestra completa** — es una corrección de comportamiento, no
un umbral afinado, y por eso generaliza por construcción.

El comparador daba `FALLA` por un criterio mal planteado: exigía
`fp_que_tocan_negativo ≤ 0` en absoluto, cuando 287 → 287 no empeora nada. Se
corrigió a `≤ base`. Un umbral que reprueba lo que no empeora no protege
nada; solo enseña a ignorarlo.

## IT-10 · Lo que quedó desmentido

`fuentes_mixtas`, la mejora estrella de 0.18.0 (+0,014 de F1 sobre el conjunto
sintético), **pierde 0,014 sobre el institucional**:

| | `produccion_estandar` | `fuentes_mixtas` |
|---|---:|---:|
| F1 | **0,8745** | 0,8606 |
| recall SIN_ID | **0,6523** | 0,5909 |
| recall `positivo_ruido_sin_id` | **0,6026** | 0,5021 |
| FP sobre negativos | **287** | 419 |
| tiempo | **35,5 s** | 62,3 s |

Era sobreajuste a un conjunto sin variación real de nombre. Se mantiene
documentado para datos de contacto sintéticos y **deja de recomendarse como
perfil general**. Es exactamente el error que este conjunto se construyó para
atrapar, y lo atrapó a la primera corrida — a costa de desmentir el titular de
la versión anterior, que es como debe ser.

### Estado al cerrar 0.19.0

| | |
|---|---|
| Línea base | F1 0,8780 · macro-F1 0,8920 · 36,8 s · 503 MiB |
| Conjunto | 30.486 registros · 11.478 grupos · 46.374 pares · 4 estratos · 11 casos |
| Pruebas nuevas | 37 (`test_identificadores_v019.py`) |
| Criterios del plan | 10 ✅ · 6 🟡 · 16 ⬜ |

### Lo que sigue

1. **C27 (nuevo, P0)** — bloqueo multivariable. Medido: RUIDO pierde el 27 %
   de sus pares verdaderos antes de llegar al scorer. Es el techo de todo.
2. **C01** — el diagnóstico de identificadores sigue reportando una cifra de
   muestra sesgada como si fuera exacta.
3. **C02 + C03** — L5 y los grupos unitarios a escala de millones.
4. **C26 (nuevo)** — la igualdad de identificadores se decide en tres sitios;
   ahora los tres llaman al mismo módulo, pero siguen siendo tres.
5. **C12** — el banco devuelve código de salida; falta que CI lo corra.

---

# v0.20.0 — Dos instrumentos y un solo catálogo

> Pregunta de Enrique: *"¿al fin ya tenemos una base para hacer pruebas, o nos
> toca definir para evaluación y construcción?"* — con un archivo nuevo,
> `Ground_Truth_Multicampo_v1.xlsx`, hecho para evaluación multicriterio.

## IT-11 · La respuesta: dos instrumentos, y particiones para lo demás

El archivo nuevo **no compite** con el conjunto institucional: mide otra cosa.

| | banco | conformidad |
|---|---|---|
| pregunta | ¿mejoró? | ¿sabe hacerlo? |
| registros | 30.486 | 207 |
| falla si | una métrica retrocede | **un solo caso** reprueba |

Con 46.374 pares, un comportamiento roto que afecta a cuatro casos mueve el F1
en la cuarta cifra decimal. Ese es el punto ciego que ADR-0001 no cubría, y es
justo donde viven las fusiones escandalosas: dos empresas en la misma torre,
dos que comparten call center, dos con correo de gmail.

Evaluación contra construcción **no exige dos conjuntos**: exige particionar
el mismo por grupo y sellar un pliegue. Ya estaba implementado y es como se
validó ADR-0004.

## IT-12 · El instrumento, y la primera cifra que lo valida

`evaluation/conformidad.py` + `scripts/conformidad.py`. El conjunto se importa
del libro y queda versionado en `data/conformidad/` (100 KB, 7 hojas).

La primera corrida dio **P=1,0000 R=0,9847 F1=0,9923** en deduplicación y
**1,0000** en linkage — que es **exactamente** lo que declara la hoja LEEME
del libro, medido con la versión 0.10.0 del motor. Dos implementaciones
independientes con el mismo número: eso es lo que da confianza en el
instrumento, más que cualquier revisión de código.

Los dos casos que el libro marcaba pendientes para "Fase 3" (C09 y C21)
reprobaban, tal como el libro anticipaba.

## IT-13 · F3 estaba implementado y apagado

`CorroboracionVeto` existía desde 0.11.0 con todas sus salvaguardas y nunca se
había activado. Activarla resuelve C09 y C21 y deja las seis trampas intactas:

| | C09 | C21 | trampas |
|---|---|---|---|
| sin corroboración | reprueba | reprueba | 6/6 |
| con corroboración | **aprueba** | **aprueba** | **6/6** |

F1 de deduplicación 0,9923 → **1,0000**.

> **Autocorrección.** Escribí en el código que exigir DOS corroborantes era
> "lo que separa C09/C21 de las trampas MC04 y MC05". Lo medí y **era falso**:
> con un solo corroborante la suite también da F1 = 1,0000 y ninguna trampa se
> cae. Lo que las sostiene es que sus comparadores ya devuelven 0 ante valores
> de baja entropía, más la exigencia de similitud de nombre. Se corrigió el
> comentario: 2 es prudencia declarada, no evidencia. Anotado como C35.

## IT-14 · La deuda que el archivo destapó: dos catálogos de comparadores

El libro declara 11 tipos de campo. Al verificarlos apareció que la librería
tenía **dos catálogos que no se conocían entre sí**:

- `matching.campos` + `comparators.py`: 14 tipos declarativos, con haversine,
  fecha, numérico, dirección y nombre de persona. Lo usa el motor multicampo,
  en memoria, hasta ~500 K filas.
- `matching.comparadores_extra`: 11 tipos. Lo usa el **scorer de producción**,
  el que procesa millones en disco.

**El camino que escala no podía usar la mitad de lo que la librería ya sabía
hacer.** Y explicaba una rareza previa: ADR-0005 concluyó que "el criterio
múltiple en el scorer no ayuda", cuando el scorer solo tenía acceso a las
variables más pobres.

`matching/puente_campos.py` registra en el catálogo de producción el
comparador canónico de cada tipo, delegando en la misma instancia. Cero lógica
duplicada. **11 → 24 comparadores disponibles.**

### Dos defectos reales que el puente destapó

Exponer los comparadores a la prueba de propiedades —que los alimenta con
nulos de todas las formas— reveló dos fallos que llevaban tiempo ahí:

1. **pandas 3.0 dejó de convertir los ausentes a la cadena `'nan'`.** Los deja
   como `NaN` flotante y el `.map` posterior revienta con `normalize()
   argument 2 must be str, not float`. **Bastaba una celda vacía en una
   columna de texto para tumbar una corrida real.**
2. **El comparador de direcciones no separaba la placa.** `'CRA 7 # 71-21'`
   contra `'CARRERA 7 NO 71 21'` daba 0,600. Ahora da 1,000.

> **Segunda autocorrección.** La primera versión de la corrección de
> direcciones borraba TODA la puntuación. Medida contra el baseline
> multicampo congelado, bajaba el recall de 0,8940 a 0,8808 sin arreglar
> ningún caso adicional: eliminaba también los guiones sueltos de
> `'CALLE 11 # 2 - 11'`, que hoy cuentan como token compartido. Se redujo a
> puntuación **entre dos dígitos**, que es el defecto real. Baseline intacto,
> defecto corregido. La tentación de "ya que estoy, limpio todo" cuesta
> recall.

## IT-15 · C31: bloqueo multivariable, medido, y el hallazgo es negativo

Se implementó `engine/lsh/llaves_extra.py` con dos mecanismos: llaves
declaradas (teléfono, correo, dirección, celda geográfica, documento) y
bloqueo por token raro. Apagados por defecto, seguros en memoria: ~4 bytes por
registro y llave, frente a los 6–10 GB de la implementación que causó el OOM
de 0.17.3.

| configuración | F1 | macro-F1 | RUIDO recall | FP negativos |
|---|---:|---:|---:|---:|
| línea base | **0,8780** | **0,8920** | 0,6472 | **287** |
| + llaves (tel., correo, dirección) | 0,8780 | 0,8920 | 0,6472 | 287 |
| + token raro (frec. ≤ 5) | 0,8779 | 0,8921 | 0,6482 | 324 |
| + token raro (frec. ≤ 50) | 0,8454 | 0,8712 | **0,6717** | 2.575 |
| umbral LSH 0,35 | 0,8537 | 0,8676 | 0,5547 | 787 |
| 504 permutaciones | 0,8761 | 0,8905 | 0,6430 | 289 |

Las llaves añaden 61.293 candidatos y **recuperan cero pares verdaderos**: el
recall de bloqueo se queda clavado en 0,730 / 0,959 / 0,982. Cuando dos
registros del mismo ente comparten teléfono, ya compartían suficientes
trigramas.

**El hallazgo que importa es el negativo, y corrige lo que yo mismo escribí en
0.19.0.** Dije que el techo de recall del estrato de ruido era un problema de
bloqueo y lo puse como P0. No lo es. Los pares perdidos **son alcanzables** —
el 82,8 % tiene Jaccard de trigramas ≥ 0,30, y la mediana es 0,475, por encima
del umbral configurado— pero **toda** forma de alcanzarlos inunda el scorer de
pares falsos que cuestan más de lo que ganan. Bajar el umbral empeora el
recall (0,647 → 0,555), afilar la curva también (0,643), y el token raro solo
ayuda al precio de multiplicar por nueve los falsos positivos sobre negativos.

El cuello está en **discriminar bajo corrupción extrema del nombre sin
identificador**, no en proponer candidatos. Eso mueve el trabajo pendiente del
bloqueo al scorer, que es lo contrario de lo que decía el plan.

Los mecanismos se conservan —apagados, probados y documentados— porque son
correctos y son la palanca adecuada para datos donde el contacto sea el único
puente. Pero no se afirma que mejoren nada: aquí no lo hacen.

## IT-16 · Una optimización probada y descartada

Vectorizar el módulo 11 con multiplicación de matrices: **39,5 s contra
35,3 s** sobre 5 M de identificadores, y 15 % mejor por lote, a cambio de 35
líneas. El costo no está en la aritmética sino en las operaciones de cadena de
pandas sobre 4,2 M de valores únicos. Se revirtió y quedó anotado en el
docstring para que nadie vuelva a intentarlo sin medir.

## IT-17 · El arreglo que decide si la corrida de 5 M termina

Al medir el costo del cannot-link a escala apareció un cuello que llevaba ahí
desde antes de esta versión y que nadie había medido: la función convertía la
columna ENTERA de identificadores a numpy **dentro del bucle**, una vez por
grupo en conflicto. Con Arrow detrás esa conversión no es barata.

cProfile sobre 200 K filas: **5.316 llamadas a `ArrowStringArray.to_numpy`,
el 53 % del tiempo de la función**.

| | antes | después |
|---|---:|---:|
| 1 M de filas, 2 % en conflicto | 437,2 s | **10,2 s** (43×) |
| 5 M de filas, 2 % en conflicto | no terminaba en 10 min | **56,2 s** |
| RSS a 5 M | — | 555 → 618 MiB (+63) |

Resultado idéntico —mismas etiquetas, mismo número de grupos—. A 5 M esta fase
pasaba de más de media hora a menos de un minuto: la diferencia entre terminar
una corrida en Colab gratuito y no terminarla.

> **Tercera autocorrección, y la más instructiva.** Mi primera hipótesis fue
> que el culpable era `np.flatnonzero(grupos == grupo)` dentro del bucle —un
> recorrido completo por grupo, que es un patrón sospechoso de libro—. Lo
> reemplacé por `argsort` + `searchsorted`, volví a medir y **seguía tardando
> 421 s**. Solo entonces perfilé, y el culpable era otro. La corrección del
> `flatnonzero` se conserva porque es correcta y barata, pero no era el
> problema. **Perfile antes de optimizar; la intuición sobre dónde está el
> tiempo acierta menos de lo que uno cree.**

### Estado al cerrar 0.20.0

| | |
|---|---|
| Banco | F1 0,8780 · macro-F1 0,8920 · huella **idéntica** a 0.19.0 |
| Conformidad | dedup 43/43 con `--corroborar` · linkage 10/10 |
| Comparadores en producción | 11 → **24** |
| Pruebas nuevas | 39 (`test_conformidad_v020.py`) |
| Escala | cannot-link a 5 M: >30 min → **56 s** · `bases_canonicas` 35 s sobre 5 M |

### Lo que sigue

1. **C37 (nuevo, P0)** — discriminación bajo corrupción extrema sin
   identificador. Es donde está el 27 % de pares perdidos del estrato de
   ruido, y la evidencia dice que el bloqueo no es el camino.
2. **C35** — medir `min_corroborantes` 1 contra 2 sobre datos reales.
3. **C01** — el diagnóstico de identificadores sigue reportando una cifra de
   muestra sesgada como si fuera exacta.
4. **C36** — el catálogo de bloqueo sigue partido entre los dos motores; el de
   comparación ya no.
5. **C12** — el banco y la conformidad devuelven código de salida; falta que
   CI los corra.


---

# v0.21.0 — El contrato de salida

> Enrique: *"veo que correlative.parquet no tiene NAME_SIMILARITY_SCORE ni
> NIT_FINAL/RAZON_SOCIAL_FINAL… parte del resultado final es que quede con un
> NIT y Razón Social Final. ¿Eso requiere sólo cambio del notebook o es
> necesario hacer cambios adicionales?"*

## IT-18 · Primero verificar, y el síntoma no se reproduce

Antes de tocar nada se comprobó si las columnas existen. **Existen**, en los
cuatro caminos: pandas y DuckDB, con y sin colapso de duplicados, modo
dataframe y modo disco. El caso reportado no se reprodujo con 0.20.0.

La tentación aquí era doble y las dos son malas: cerrar el asunto diciendo
"funciona en mi máquina", o parchear el notebook para que muestre algo.
Ninguna responde la pregunta que Enrique hizo, que era la correcta: **¿basta
el notebook o hace falta más?**

## IT-19 · Lo que sí se encontró, y es peor que el síntoma

Buscando por dónde podrían faltar aparecieron **tres puntos de degradación
silenciosa**, todos escribiendo una advertencia y siguiendo adelante:
`generator.py` en dos sitios y `orchestrator.py` en uno.

Y el hallazgo que los explica: **`flujo/cruce.py` no mencionaba `NIT_FINAL` ni
una sola vez.** `_verificar_invariantes` comprobaba filas, nulos y coherencia
entre tablas — pero no el entregable. La garantía que el usuario daba por
descontada **no estaba escrita en ninguna parte**.

Que funcionara era suerte. Esa es la respuesta a su pregunta: **no es solo el
notebook, y la parte que importa es la librería.**

## IT-20 · Reparar y decirlo, no fallar ni callar

Tres políticas posibles y ninguna obvia:

- **Fallar** castiga al usuario después de cuarenta minutos de cómputo por un
  defecto que no es suyo.
- **Reparar en silencio** es la política actual, y es la causa del problema.
- **Reparar, dejar constancia, y fallar solo si es imposible** — elegida.

`golden/columnas_finales.py` distingue **calcular** (trabajo normal, INFO) de
**reconstruir** (anomalía, WARNING). La distinción no es cosmética: en la
primera versión el aviso saltaba también en el curso normal, lo que en tres
corridas habría enseñado a ignorarlo. Se separó tras verlo en la salida.

La invariante se hizo explícita en los dos caminos, y `identidad_adoptada`
entró al protocolo `ControlCalidad` para que los dos modos de resultado sigan
siendo sustituibles.

### Estado al cerrar

| | |
|---|---|
| Banco | huella **idéntica** a 0.20.0 · F1 0,8780 · macro-F1 0,8920 |
| Caminos verificados | 4/4 entregan las cuatro columnas |
| Pruebas nuevas | 17 (`test_columnas_finales_v021.py`) |

### Lo que sigue

1. **C38 (nuevo)** — `NAME_SIMILARITY_SCORE` compara el nombre normalizado
   contra el final sin normalizar. Da 0,69 donde debería dar 1,0. Se conservó
   la semántica de 0.20.0 a propósito; corregirla exige medir el impacto sobre
   resultados ya publicados.
2. **Sin resolver** — cuál de las tres hipótesis explica el caso de Enrique.
   Su registro de corrida lo diría.


---

## IT-21 · Deduplicar cuando no hay identificador (0.22.0)

**Fecha:** 2026-09-11 · **Etiqueta:** `despues` · **ADR:** 0009

**Caso:** 211.949 destinatarios de exportación con dos columnas —razón social y
país— y ningún NIT. El régimen SIN_NIT llevado a su extremo.

### Lo que se midió primero

Correr el caso con los defectos de 0.21.0 —`JaroWinklerSigned` y
`clusters_desde_decisiones`— y **mirar los grupos más grandes uno por uno**.
Ninguna métrica interna delataba el problema: la corrida se veía impecable.

| Grupo | Miembros | Qué los unía |
|---|---:|---|
| `INTERNATIONAL` | 157 | la palabra está contenida en todos ellos |
| `MQE` | 214 | ídem: consolidador + su clientela |
| `COMERCIAL` | 133 | ídem |
| `HINCAPIE SPORTSWEAR` encadenado | 209 | `a≈b`, `b≈c`, `a≉c` |

Más `COMERCIALIZADORA ATLANTA C.A.` con `COMERCIALIZADORA ATLANTIC C.A.`
(JW 0,972, dos empresas) y el campo país con 529 grafías para 203 países.

### Lo que se cambió

Cinco módulos nuevos, todos aditivos: `matching.nombre_idf`,
`matching.genericos`, `paises`, `engine.cobertura`, `processing.saneamiento`,
y el flujo `flujo.importadores` que los ata con su control de calidad.

**El hallazgo que más costó:** el IDF aprendido del corpus **no distingue una
marca de un genérico**, y no por azar. Medido sobre 105.705 razones sociales:

| Token | Frecuencia documental | IDF |
|---|---:|---:|
| `IMPORTADORA` | 737 | 5,96 |
| `ZELECTA` (marca) | 490 | **6,37** |

La marca pesa MENOS que el genérico porque su frecuencia documental crece con
el número de variantes de la misma empresa — justo lo que estamos deduplicando.
Ningún umbral sobre el IDF separa los dos casos: se solapan. Por eso la lista
de genéricos es un dato declarado.

### Tres decisiones que la medición revirtió

1. **El bloqueo que parecía razonable perdía un cuarto de los pares.** Medido
   por fuerza bruta sobre particiones completas (todos los pares i<j):

   | Configuración | PC (GTM) | PC (NLD) | PC (CHL) | PC (DEU) | PC (CRI) |
   |---|---:|---:|---:|---:|---:|
   | 64 perm @ 0,35 | 0,731 | 0,668 | 0,669 | 0,695 | 0,809 |
   | 64 perm @ 0,30 | 0,986 | 0,967 | 0,984 | 0,968 | 0,988 |
   | **128 perm @ 0,25** | **0,992** | **0,990** | **1,000** | **0,989** | **1,000** |

   La primera fila daba menos candidatos, corrida más rápida y métricas
   internas idénticas. Sin la fuerza bruta, invisible.

2. **El umbral inicial de 0,88 era demasiado estricto.** Revisar a mano 40
   pares del tramo 0,84–0,88 que quedaban fuera: **37 correctos, 1 error,
   2 dudosos** (92,5 %). Se descartaban ~7.000 empalmes buenos. Defecto: 0,84.

3. **El piso de distintividad estaba en frecuencia absoluta.** Se detectó
   escribiendo la prueba de regresión del modo de falla "palabra de sector como
   identidad": pasaba en producción (105.705 nombres) y fallaba en el corpus
   sintético (1.717). Un token en 1.000 nombres es genérico en un corpus de
   100.000 y es *todo el corpus* en uno de 1.000. Ahora es una fracción.

Un cuarto ajuste, menor pero medible: el patrón de prelimpieza que quita el
código de cliente antepuesto solo cubría 1–7 caracteres, y los NIT/RNC/RUC
llegan a 13. Se detectó escribiendo la prueba
(`13158733- CONSTRUCTORA SCHEKER` no se limpiaba). Ensancharlo a 15 unió 22
grafías más y bajó el conteo final de 99.914 a **99.898 importadores**.

También se corrigió que el nombre final heredara mojibake: la regla "la grafía
más larga" escogía sistemáticamente la versión corrupta, porque los caracteres
de mojibake suman longitud. Con la penalización de rareza, los nombres finales
con caracteres sospechosos bajan de 1,28 % a 1,04 % de las filas.

### Calidad medida

160 asignaciones revisadas a mano, estratificadas por banda:

| Banda | Asignaciones | Precisión estricta | Precisión amplia |
|---|---:|---:|---:|
| 0,84–0,88 | 6.898 | 0,825 | 0,925 |
| 0,88–0,92 | 4.237 | 0,950 | 1,000 |
| 0,92–0,96 | 3.520 | 0,850 | 0,900 |
| 0,96–1,00 | 41.966 | 1,000 | 1,000 |
| **Ponderada** | **56.621** | **0,966** | **0,985** |

### Banco — paridad exacta

| Dimensión | 0.21.0 (`antes`) | 0.22.1 (`despues`) |
|---|---|---|
| precision / recall / F1 | 0,9385 / 0,8249 / **0,8780** | 0,9385 / 0,8249 / **0,8780** |
| B³ F1 | 0,9534 | 0,9534 |
| macro-F1 | 0,8920 | 0,8920 |
| Recall CON_ID / SIN_ID | 0,9768 / 0,6523 | 0,9768 / 0,6523 |
| Candidatos → válidos | 968.673 → 41.737 | 968.673 → 41.737 |
| Tiempo | 51,0 s | 50,7 s |
| Pico de RSS | 503 MiB | 506 MiB |
| **Huella de la partición** | `1e365ba81c4df45e…` | **`1e365ba81c4df45e…`** |

**Huella idéntica.** El cambio es aditivo: no toca la ruta de producción RUES.

### Estado al cerrar

| | |
|---|---|
| Banco | huella idéntica a 0.21.0 |
| Suite | **1.517** casos colectados (1.421 en la 0.21.0; 77 funciones nuevas que parametrizan a 96 casos) |
| ruff | `check` y `format --check` limpios |
| Corrida de referencia | 211.949 → 99.898 importadores · 226 s · 2,6 GiB |

### Lo que quedó pendiente

1. **La precisión de 0,966 tiene n=160.** El IC 95 % de la banda peor
   (0,84–0,88, p=0,825) va de 0,67 a 0,93. Cerrarlo exige más etiquetas, no
   más código.
2. **Empresas hermanas del mismo grupo.** `BARRY CALLEBAUT USA` y
   `BARRY CALLEBAUT CANADA` quedan juntos con `geografia_es_ruido=True`. No hay
   defecto correcto para todos los usos; la perilla expone el intercambio.
3. **Se encontró una prueba que ya venía fallando**:
   `06_ejemplo_rues_x_exportaciones.ipynb` se publicó con 9 celdas de salidas
   embebidas y `test_json_valido_y_sin_salidas_guardadas` lo prohíbe. Corregido
   aquí (153 KiB → 78 KiB), pero conviene revisar por qué llegó a publicarse.


---

## IT-22 · Publicar sin engañarse (0.22.1)

**Fecha:** 2026-09-11 · **Etiqueta:** `despues` vs `antes` (misma sesión)

**Origen:** la publicación real al repositorio `rues-linker_v2` se detuvo en las
compuertas. Cinco hallazgos, en orden de lo que costó verlos.

### 1. El síntoma señalaba al lugar equivocado

`ModuleNotFoundError: No module named 'record_linkage'` en pytest, mientras el
banco y la conformidad **pasaban en la misma corrida**. La diferencia: los dos
scripts hacen `sys.path.insert(0, RAIZ/"src")` y pytest no. Nada instalaba el
paquete. Corregido con la Celda A.0, que instala la rueda y verifica nueve
submódulos.

### 2. Lo que no llegó a fallar era peor

`preparar_build()` reescribía `pyproject.toml`. Cuantificado sobre este
repositorio: se perdían el marcador `slow` (**7 tests en 5 archivos** dejarían
de colectar bajo `--strict-markers`), `[tool.ruff]` completa (4 secciones) y
`[tool.mypy]`. Se habría publicado un repositorio cuyo CI no puede pasar.
Corregido con `REGENERAR_PYPROJECT = False`.

### 3. El banco reprobó por el reloj, no por el código

`--comparar` dio FALLA en `segundos_total` (+24 %) con **huella idéntica**. El
experimento que lo resolvió: correr el código **0.21.0 original** en la máquina
de ese momento.

| Código | Cuándo | segundos_total |
|---|---|---:|
| 0.21.0 | mañana | 51,0 |
| 0.21.0 | **tarde, misma máquina** | **65,1** |
| 0.22.1 | tarde, misma máquina | **63,2** |

El contenedor estaba ~28 % más lento. La versión nueva es 1,9 s **más rápida**
que la anterior medida a la vez.

**Lección, y es general:** la huella y las métricas de calidad son
deterministas y comparables entre máquinas; el reloj y la RSS no lo son. Un
veredicto binario que los mezcla produce falsas alarmas, y una falsa alarma
repetida es peor que no medir: enseña a ignorar el instrumento. La Celda A.1
los separa — calidad siempre bloquea, costo solo con línea base de la misma
sesión.

**No se tocó el umbral del banco** (`aumento_maximo_tiempo = 0.20`). El
problema no era la tolerancia sino comparar contra una base no comparable.

### 4. Arreglar el 1 destapó 8 pruebas que se saltaban solas

Efecto secundario de la Celda A.0, y el hallazgo más incómodo de los cuatro.
Instalar la rueda hace que pytest importe `record_linkage` desde
`site-packages` —que es lo correcto: se prueba lo que se publica— y ahí
`Path(__file__).parents[3] / "data" / "conformidad"` deja de apuntar al
repositorio y apunta a `/usr/local/lib/python3.11/data/conformidad`.

El fixture atrapaba el `FileNotFoundError` y hacía `pytest.skip`. **Ocho casos
del conjunto de conformidad se saltaban en silencio.** La compuerta decía
verde sin haber medido.

Lo primero fue comprobar de quién era la culpa. Se corrió la suite sobre el
zip original sin tocar: `31 passed, 8 skipped`. **No lo introdujo la 0.22.1.**
Estaba latente desde la 0.20.0 y nadie lo vio porque nadie corría la suite con
el paquete instalado — siempre se corría desde el checkout, donde `parents[3]`
sí acierta.

**Lección:** una prueba que falla avisa; una que se salta, no. `pytest.skip`
dentro de un `except FileNotFoundError` convierte un problema de entorno en
silencio, y el silencio pasa las compuertas. Si un fixture puede saltarse,
hace falta una prueba que verifique que **no** se salta cuando no debe — que es
una de las seis que se añadieron.

**Corrección:** `localizar_conjunto()` con resolución por capas (variable de
entorno → constante del módulo → búsqueda hacia arriba desde el directorio de
trabajo → constante, para que el error nombre una ruta). El directorio se
reconoce por `dedup_registros.csv`, no por existir: uno vacío pasaría la
comprobación y fallaría lejos de la causa. `scripts/conformidad.py` ancla
`--datos` a su propia raíz, que siempre conoce, y no usa la heurística.

| `pytest tests/test_conformidad_v020.py`, paquete instalado | resultado |
|---|---|
| antes | 31 pasan, **8 se saltan** |
| ahora | **45 pasan, 0 se saltan** |

### Estado al cerrar

| | |
|---|---|
| Banco (base de la misma sesión) | **PASA** · huella idéntica · 63,9 s vs 65,1 s |
| Conformidad | **PASA** |
| Suite | **1.533** casos colectados (1.517 en la 0.22.0) · **0 saltos por conformidad** |
| Pruebas nuevas | 8 de contrato del notebook 08 + 7 de localización del conjunto |
| ruff | `check` y `format --check` limpios |

### Lo que queda pendiente

1. **La publicación real sigue sin verificarse de extremo a extremo.** Aquí se
   probaron A.0 y A.1 contra el árbol real; el push, el tag y el release
   requieren Colab, Drive y un token. Córralo primero contra una rama de
   prueba.
2. **`ANTIGUEDAD_MAXIMA_BASE_HORAS = 2` es un número elegido, no medido.** Es
   un proxy de "misma sesión". Si Colab le da una VM distinta dentro de esas
   dos horas, el aviso no saltará y el costo bloqueará sin razón.

## IT-23 · Dos compuertas que medían el artefacto equivocado (0.22.2)

**Fecha:** 2026-09-11 · **Origen:** la publicación real de Enrique, que llegó
hasta las pruebas de contrato.

De las tres pruebas que fallaron, **dos eran defectos míos y una no era un
fallo**. Ninguna estaba en la librería.

### 1. La compuerta que no podía pasar nunca

`test_json_valido_y_sin_salidas_guardadas` exigía notebooks sin salidas
embebidas, leyendo el árbol de trabajo. Falla por dos razones independientes,
y las dos se dieron a la vez:

- El **08** es el notebook que corre la compuerta. Colab autoguarda sus salidas
  en Drive mientras se ejecuta; cuando pytest lo lee, ya las tiene.
- El **07** es un notebook de análisis: sus salidas **son** el resultado.

**Es la segunda vez en esta serie.** La primera fue la prueba de marcadores que
se detectaba a sí misma leyendo su propio código fuente. El patrón es el mismo:
una prueba que inspecciona archivos del repositorio se incluye a sí misma y al
contexto que la ejecuta. **Regla que queda:** cuando una prueba lee archivos del
árbol, hay que preguntarse si el archivo que la contiene —o el que la está
ejecutando— está en el conjunto que inspecciona.

La propiedad es del **repositorio**, no del árbol de trabajo. Se mudó a los dos
sitios donde sí es exigible: `preparar_build()` limpia al copiar (garantía
incondicional, no depende de que nadie se acuerde) y la prueba comprueba el
checkout de git cuando lo hay, y cuando no, que la garantía del build siga en
pie. Ninguna rama se calla.

| rama | árbol | resultado |
|---|---|---|
| A | copia de trabajo con salidas | **pasa** (era el falso bloqueo) |
| B | checkout de git con salidas | **falla**, nombrando los notebooks |
| C | el build | **limpia 2**, Drive intacto |

### 2. Un contrato que fijaba la implementación, no el invariante

`test_la_instalacion_no_es_editable` exigía literalmente un `pip install` en la
celda de entorno. El notebook 07 nuevo importa desde el árbol de Drive vía
`sys.path`, sin instalar, y el contrato lo rechazaba.

El invariante real nunca fue "tiene que haber un pip install": es **no correr
código rancio ni incompleto, y fallar ruidosamente si ocurre**. Instalar la
rueda es *una* forma de conseguirlo; importar del árbol verificando el origen y
los submódulos es otra, igual de válida.

**Y reescribirlo destapó un hueco real:** el notebook 05 no verificaba que el
paquete quedara completo. Llevaba ahí desde que se escribió; el contrato viejo
no lo veía porque solo exigía que hubiera un `pip install`, y lo había.

**Lección:** un contrato escrito sobre la implementación concreta que había el
día que se escribió no protege el invariante — lo congela. Se nota cuando
rechaza una solución correcta, y para entonces lleva tiempo sin proteger nada.

### 3. El banco: no era un fallo

AVISO en `segundos_total` (+23,8 %), huella idéntica, las cuatro métricas de
calidad sin mover un decimal. Es el comportamiento que la 0.22.1 introdujo a
propósito: el reloj de otra sesión no es evidencia de regresión. **Funcionó.**

### El notebook 07 de Enrique

Corrió completo en Colab: 211.949 → 99.898 (−52,9 %), 9 invariantes OK, 396 s.
Sustituye al mío. Dos cosas que hacía mejor y se conservan: lee las
dependencias del `pyproject` del propio paquete (no una lista copiada a mano,
que se desincroniza) y verifica de dónde se importó `record_linkage` de verdad.
Se le añadió la verificación de completitud del árbol (9 submódulos) — la única
protección que le faltaba a su estrategia, y el modo de falla característico de
FUSE.

### Estado al cerrar

| | |
|---|---|
| Contratos de notebooks | **79** pasan (9 nuevos o reescritos) |
| Conformidad | 46 pasan, 0 saltos |
| Suite completa | **1.547** casos colectados (1.533 en la 0.22.1) |
| Banco | **PASA** · huella idéntica · calidad sin mover un decimal |
| ruff | limpio |
| Librería | **sin cambios**: solo notebooks y pruebas |

## IT-24 · La invariante que acertaba tarde (0.22.3)

**Fecha:** 2026-09-11 · **Origen:** primera corrida del notebook 07 sobre la
base `snowflake_v2` (355.681 filas, columnas nuevas). El smoke test se detuvo
en «el país final está en el catálogo o marcado».

### Lo que se hizo primero: reproducir, no razonar

Con los datos viejos renombrados al perfil nuevo el smoke test **pasa**. El
fallo era específico de la base nueva, y la única pista era el nombre de la
invariante. Se inyectaron tres grafías fuera del catálogo en una muestra y se
puso la misma razón social bajo las tres. Resultado: **un solo importador**.
La invariante estricta estaba atrapando un problema de integridad verdadero —
y no era ese. El que atrapaba era «hay filas sin clasificar»; el que importaba
era «esas filas se fusionan entre sí», y ninguna invariante lo miraba.

### Dónde debe fallar

No en una invariante, después de minutos de emparejamiento y sin decir qué
grafías. En `preparar()`, con un mapeo vectorizado, con la lista completa y con
los remedios. Y con una escotilla explícita —`aislar`— para las grafías que no
tiene sentido catalogar, cuyo funcionamiento lo **mide** una invariante nueva
en vez de suponerlo.

### Lo que `aislar` destapó

Al activarlo, «una fila por fila de entrada» falló: la unión base↔representantes
iba por `(ISO3, nombre)` y la identidad real es `(ISO3, PAIS_FINAL, nombre)`.
`agrupar_no_pais=False` tenía el mismo defecto desde 0.22.0. Nadie lo pisó.
**Lección:** una opción de configuración que nadie usa no está probada aunque
tenga pruebas; el camino por defecto es el único que la realidad ejercita.

### Tercer contrato escrito sobre la implementación

El de la celda de entorno exigía el nombre `_SUBPAQUETES` y rechazaba la
verificación más exhaustiva del 07 (`walk_packages` sobre 146 módulos). Es la
tercera vez en tres versiones. La regla ya está en CLAUDE.md §5; lo que falta
es aplicarla al escribir, no al corregir.

### El ±1 que no era mío

Verificando que nada cambiara en la referencia: 99.897 en vez de 99.898.
Primer impulso: mi cambio de llaves. Se midió antes de creerlo: el árbol
0.22.0 intacto dio 99.897 y 0.22.3 dio 99.898 en la corrida siguiente. **El
mismo código, dos resultados.** Diff fila a fila: un grupo, AVIATECA/GTM.
Aislada la partición bajo tres `PYTHONHASHSEED`: el nombre **normalizado**
cambia. Causa: sufijos multi-token ordenados desde un `set` con empates en
orden de hash, y una sola pasada que no llega al punto fijo. A escala:
seed 0 → 99.898, seed 1 → 99.897, con el código viejo.

**Lección de método, y es la del día:** cuando un número cambia, la primera
hipótesis es «lo rompí yo» y la segunda «es ruido». Las dos son cómodas y las
dos se comprueban en diez minutos con el árbol anterior. Aquí la respuesta era
la tercera: un defecto de reproducibilidad que llevaba ahí desde 0.22.0 y que
`test_es_determinista` no podía ver porque corre en un solo proceso. El
determinismo entre procesos solo se prueba con procesos.

### Estado al cierre

| | |
|---|---|
| determinismo | GTM bajo 3 semillas: misma huella; base completa bajo 2 semillas: correlativa idéntica |
| `detener` | se detiene en `preparar()` con la tabla; medido sobre muestra real |
| `aislar` | 3 grafías → 3 grupos, 10/10 invariantes; medido |
| base de referencia (0 sin clasificar) | mismo resultado en los dos modos; **99.897** importadores, idéntico bajo semillas distintas (el 99.898 anterior era la semilla afortunada) |
| librería | +18 pruebas; defecto latente de `agrupar_no_pais=False` cerrado |
| suite completa | **1.566** casos colectados (1.547 en la 0.22.2) · 1.550 pasan, 2 saltos de Windows |
| notebook 07 | el de Enrique, con `PAISES_SIN_CLASIFICAR` y cobertura en `0d` |

## IT-25 · La guardia funcionó, y lo que atrapó era mío (0.22.4)

**Fecha:** 2026-09-11 · **Origen:** primera corrida real del 07 sobre
`snowflake_v2`. La celda 6 se detuvo con 19 grafías de país fuera del catálogo.

Es la primera vez en esta línea de trabajo que un fallo llega por el canal
diseñado para él: antes de emparejar, con la lista, con las filas, con la
sugerencia. Y la lista dijo algo incómodo: **18 de las 19 eran países** que un
catálogo de 203 no tenía — Irán y Afganistán entre ellos. El catálogo se
construyó sobre lo que la base DIAN trajo; "203 países" sonaba completo y no
lo era. Lo otro que dijo: la sugerencia por similitud acertó **6 de 18**. Está
en el código como "ayuda, no regla" desde 0.22.0; ahora está medido.

### La tentación que se evitó

La 19ª grafía, `OTROS` (444 filas), no es un país. La escotilla disponible era
`paises_sin_clasificar="aislar"`. Habría funcionado hoy — y mañana, con una
grafía nueva, la corrida habría seguido en silencio. Una escotilla global
convierte la guardia en decoración. Se añadió `paises_aislar`: declara grafías
concretas, el modo sigue en `detener`, y lo no declarado sigue deteniendo.

### La corrida real: el importador más grande no existía

Enrique compartió la base real para verificar. Tres primeras filas por FOB:
`NO DISPONIBLE`. 69 filas, una por país, 21,7 % del FOB, y no era placeholder.
En la base DIAN el centinela era `'0'` y está declarado desde 0.22.0; en
Snowflake el centinela se llama `NO DISPONIBLE` y nadie lo había visto porque
nadie había corrido esta base. **Un placeholder es un dato de la fuente, no
del motor**: cada fuente nueva puede traer el suyo, y la única defensa es
mirar el top del ranking en la primera corrida. Ahora está declarado y hay una
prueba con las cifras reales.

Y la copia: el notebook contaba centinelas con una lista propia, el motor
decidía con otra. Ninguna tenía `NO DISPONIBLE`. Tercera lista a mano
eliminada en esta versión.

### La publicación: la compuerta siguiente

El 08 pasó A.1 entera y cayó en `correr_tests_locales` con un `ImportError`
al colectar. Causa: `hypothesis` no estaba instalado — la celda 3 traía una
lista a mano y la lista se quedó corta. El CI instala `.[dev]` del pyproject;
el notebook, no. Es el mismo defecto que la regla «una lista a mano se
desincroniza» describe, en el mismo repositorio que la escribió. Y la causa
no se vio: `_run` recortaba la salida a 600 caracteres.

**Lección:** una compuerta que pasa no prueba nada sobre la siguiente. A.1
corría un subconjunto (por configuración, para no agotar Colab) y la
publicación corre la suite entera; el hueco estaba en la diferencia. Ahora
A.0 instala las extras `dev` del pyproject —la misma lista que el CI—, los
marcadores de pytest son los del `ci.yml` y hay un contrato que lo vigila.
Reproducido en un venv limpio antes y después.

### El error de método del día

La edición del notebook 07 no se aplicó y no me di cuenta hasta que el
contrato lo dijo: escribí un archivo auxiliar sin salto de línea final, `read`
devolvió 1, y el `&&` saltó el script sin ruido. **Lección:** un pipeline de
shell que encadena con `&&` puede omitir un paso entero sin error visible;
la única defensa es verificar el efecto, no la orden. El contrato lo hizo.

### Estado al cierre

| | |
|---|---|
| catálogo | 214 países · 378 grafías · índice sin alias ambiguos |
| las 19 grafías reales | 18 mapean; `OTROS` declarado en `NO_SON_PAISES` → aislado; sin declarar → detiene listando solo `OTROS` |
| su notebook, celdas 2→3→4→6 con las 19 inyectadas | cobertura OK · smoke OK · por fases = fachada |
| referencia DIAN | **100.008** = 99.897 + 111: las 204 filas de `A LA ORDEN`/`TO ORDER`/`TO THE ORDER` dejan de formar 95 "importadores" y quedan como singletons sin nombre; partición idéntica fuera de ellas (verificado fila a fila) |
| contratos de notebooks | 84 |
| suite completa (desarrollo) | 1577 pasan, 2 saltos de Windows |
| banco | PASA · huella idéntica |
| venv limpio (rueda + extras dev, como Colab) | 1.582 casos colectados, 0 errores |

## IT-26 · La corrida real murió por memoria (0.22.4)

Con las 19 grafías resueltas y `OTROS` aislado, la corrida completa sobre las
355.681 filas de la base `snowflake_v2` llegó a la partición 175 de 214, con
26,4 M de candidatos acumulados, y el contenedor de 15 GiB la mató (`exit 137`)
sin que hubiera nada más corriendo. Para Colab Free (12,7 GiB) el diagnóstico era
peor, no mejor.

### Reproducir antes de razonar

Se corrió la partición USA sola —54.669 nombres, 3,3× la siguiente (ECU
16.418)— con un hilo vigilante que muestrea el RSS y aborta a 11,5 GiB:
24.476.082 candidatos, 520 s, **pico de 11,42 GiB** y una tabla de decisiones de
**2,58 GiB** para una sola partición. La corrida completa acumulaba además las
decisiones de las 174 anteriores. No era un caso patológico del bloqueo (el
presupuesto de 60 M no se tocó): era el costo por par, multiplicado por 24,5 M.

### Tres causas, tres correcciones, tres pruebas de identidad

1. El motor puntuaba **toda la unión de una vez**: quince arrays por par y las
   tablas del comparador. Ahora `evaluar_esquema` puntúa por lotes
   (`pares_por_lote`); la aritmética se extrajo a `_evaluar_pares` sin cambiar
   una operación. Prueba: lotes de 1, 3, 7 y "todo" dan `decisiones` y `desglose`
   iguales con `assert_frame_equal`, sobre un caso que ejercita fusiones, vetos,
   vetos levantados por corroboración y faltantes.
2. La unión LSH se construía con `np.unique(np.vstack(piezas), axis=0)` sobre
   pares int64 de todas las bandas. Ahora es por clave escalar `i·n + j` con
   compactación amortizada. Prueba: igual a `np.unique(axis=0)` sobre piezas
   aleatorias con duplicados; y `_UnionIncremental` con lote 1 igual a la unión
   de una vez.
3. `ejecutar()` guardaba **todos** los candidatos con los dos nombres por fila.
   Ahora conserva fusionados, vetados y pares ≥ `sim_minima_auditoria` (0,70), con
   índices globales y nombres bajo demanda. Prueba: la tabla podada es exactamente
   el filtro de la completa; `sensibilidad_umbral` da lo mismo con y sin poda y
   rechaza umbrales bajo el piso.

### Lo que se midió después

| | antes | después |
|---|---:|---:|
| partición USA: candidatos · fusiones · grupos | 24.476.082 · 28.409 · 44.761 | **idénticos** |
| partición USA: tiempo | 520 s | **340 s** |
| partición USA: pico de RSS | 11,42 GiB | **4,58 GiB** |
| partición USA: tabla de decisiones | 24,5 M filas · 2,58 GiB | 307.380 filas · 0,01 GiB |
| referencia DIAN | 100.008 | **100.008, correlativa bit-idéntica** |
| base real completa | muerta en 175/214 (15 GiB) | **209.038 importadores · 52,8 M candidatos · 12 min 54 s · pico 4,81 GiB** |

### Lo que apareció cuando por fin terminó: `\x1a`

La corrida completa terminó el emparejamiento (14 min, 10/10 invariantes,
checkpoint Parquet escrito) y cayó escribiendo el XLSX: `IllegalCharacterError:
COMPAÃ\x1aIA DE GALLETAS POZUELO DCR SA`. Diez razones sociales traen `\x1a`
(mojibake de "Ñ"/"Ó"); openpyxl no admite controles fuera de tab/CR/LF. El
normalizador ya los quitaba del nombre normalizado —el resultado era correcto—,
pero el entregable no se podía escribir. Corregido en la única puerta por la
que pasa todo lo que va a hoja de cálculo (`prepare_spreadsheet_data`), con la
grafía real como caso de prueba, y `METRICAS` pasa a ser Parquet-segura. El
orden "Parquet primero, XLSX después" del notebook hizo su trabajo: el resultado
ya estaba en disco cuando el Excel falló.

### Una falla que no es de esta versión, y que hay que decir

La suite completa deja **una** prueba en rojo: `test_sin_nit_perfil_recalibrado_no_regresa`
exige precisión ≥ 0,87 al perfil `deduplication_sin_nit_conservador` del pipeline
clásico (con NIT) sobre `tests/data/ground_truth_grande.csv`, y mide 0,864
(TP 2.548, FP 400). Se corrió contra el árbol 0.22.0 original del usuario, contra
0.22.3 y contra este, en el intérprete del sistema y en un venv limpio, con tres
semillas de hash: **0,864 en todos**. No la mueve nada de 0.22.4 —el flujo de
importadores no toca ese pipeline— y por eso no se "arregla" bajando el umbral ni
marcándola `slow`: sería esconder una deuda del perfil SIN_NIT (documentada como
techo de datos en 0.7.5) detrás de una versión que no la causó. Lo que sí es de
esta sesión: las corridas completas de la tarde la daban en verde y no se
encontró qué cambió en el entorno; queda anotado como pendiente de diagnóstico.
Consecuencia práctica: la compuerta `correr_tests_locales` del 08 se detendrá en
esa prueba si en Colab también da 0,864. Decisión del propietario: recalibrar el
perfil, o declarar la cota medida.

### El error de método

El primer diagnóstico de la sesión anterior atribuyó la muerte a "la partición
USA es grande" y no a un costo por par. Era verdad, pero no era la causa: con el
mismo tamaño y un costo por par tres veces menor, la partición cabe. Lo que
cambió el rumbo fue medir el pico con un vigilante en vez de estimarlo, y
descomponer la tabla de decisiones por columna (`memory_usage(deep=True)`): los
dos nombres eran más de la mitad.

### Estado al cierre

| | |
|---|---|
| pruebas nuevas | 24 (`test_scoring_por_lotes_v0224.py`) · 6 (`test_exportacion_caracteres_control_v0224.py`) |
| suite completa (desarrollo) | **1.630 pasan, 2 saltos de Windows, 1 falla preexistente** (`test_sin_nit_recalibrado.py::test_sin_nit_perfil_recalibrado_no_regresa`, precisión 0,864 < 0,87 en el perfil SIN_NIT con NIT del pipeline clásico; falla igual, número por número, con su árbol 0.22.0 sin tocar, con 0.22.3 y con este; no la toca ningún cambio de 0.22.4) |
| banco (compuerta A.1 del 08, 30.486 registros) | **PASA · huella idéntica** · P 0,9385 · R 0,8249 · F1 0,878, iguales a la base · 66,5 s · 505 MiB |
| corrida real | 355.681 filas → 209.038 importadores en 17 min de pared (12 min 54 s de emparejamiento, 52,8 M de candidatos) con **pico de 4,81 GiB**, 10/10 invariantes · XLSX + 12 Parquet + metadata escritos |
