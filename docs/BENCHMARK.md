# El conjunto de referencia institucional

> **Qué es.** `data/benchmark/benchmark_institucional.csv.gz` — 30.486 registros,
> 11.478 grupos, 46.374 pares verdaderos, 9 fuentes, 4 estratos, 11 casos
> etiquetados y 18 columnas. Se construye de forma reproducible con
> `scripts/construir_benchmark.py` y semilla fija 42.
>
> **Para qué.** Es la única vara con la que se decide si un cambio de esta
> librería mejoró algo. Todo número publicado en `docs/BITACORA.md` y en el
> `CHANGELOG` sale de correr `scripts/banco.py` contra este archivo.

---

## 1. El problema: nadie tiene un conjunto que sirva

Un conjunto de referencia útil tiene que ser dos cosas a la vez, y casi
siempre solo se consigue una:

- **Real**, para que lo que se aprenda midiendo se transfiera a producción.
- **Etiquetado con certeza**, para que las métricas signifiquen algo.

Se evaluaron los dos archivos disponibles. Ninguno cumple las dos.

### 1.1 `Ground_Truth_Robusto_V3.xlsx` — etiquetado, no real

**Qué aporta.** 7.368 registros en 2.000 grupos con el nivel de ruido
**declarado por construcción**: `CLEAN`, `LIGHT`, `MODERATE`, `SEVERE`,
`EXTREME`, más 606 negativos diseñados. Eso es valioso y no se consigue de
otra forma: permite decir "el recall cae a partir de SEVERE", que es una
frase accionable.

**Qué le falta.** Solo trae `NIT` y `RAZON_SOCIAL`. No hay geografía, ni
actividad económica, ni contacto. Con dos columnas no se puede medir nada
multicriterio.

**Y un defecto que obliga a corregirlo.** El archivo aplica el ruido también
al identificador. Dentro de un mismo grupo aparecen:

```
894939884   ENERGIA TITÁN NARIÑO CORPORATION      (SOURCE_D, CLEAN)
894-939-884 ENERGIA TITÁN NARIÑO CORPORATION      (SOURCE_C, LIGHT)
895239884   ENERGIA TITÁN NARIÑO                  (SOURCE_A, SEVERE)
894139814   ENERGIA TITÁN NARIÑO CORPORATION      (SOURCE_C, SEVERE)
8949398844  ENERGI4 7ITÁN NARIÑO CORPORATIION     (SOURCE_B, EXTREME)
```

`894-939-884` y `8949398844` son el mismo número escrito distinto —
puntuación y dígito de verificación—. `895239884` y `894139814` **no**: son
números diferentes. Cambiar dos dígitos de un NIT produce, con alta
probabilidad, el NIT válido de otra empresa.

Medido sobre el archivo completo:

| | grupos | % |
|---|---:|---:|
| NIT idéntico en todo el grupo | 513 | 28,5 % |
| NIT recuperable canonicalizando (puntuación, ceros, DV) | 901 | 50,1 % |
| **NIT con corrupción de dígitos** | **899** | **49,9 %** |

Esos 899 grupos concentran **17.055 de los 18.629 pares** del estrato
(91,6 %), que a su vez eran el 36,8 % de todos los pares verdaderos del
conjunto. Dejarlos como venían obligaba al enlazador a unir registros con
identificadores distintos — que es exactamente lo que los 1.040 negativos le
exigen NO hacer. **El conjunto se contradecía a sí mismo y ninguna
configuración podía alcanzar F1 = 1.**

No es una hipótesis: se midió el costo. Con la contradicción dentro, el
recall del régimen con identificador era **0,687**, *peor* que el del régimen
sin identificador (0,834) — un absurdo que solo se explica por labels
imposibles. Al corregirla, el mismo código sin tocar una línea pasó a **0,966**.

| | con la contradicción | corregido |
|---|---:|---:|
| F1 global | 0,8177 | **0,8745** |
| recall global | 0,7015 | **0,8189** |
| recall CON_ID | 0,6867 | **0,9657** |

**Corrección aplicada.** Donde el identificador se recupera canonicalizando,
se conserva y el grupo queda en régimen `CON_ID`. Donde hay corrupción de
dígitos se **borra el identificador** y el grupo pasa a `SIN_ID` con el caso
`positivo_ruido_sin_id`: se conserva íntegro lo que ese archivo sí sabe
aportar —ruido etiquetado sobre la razón social— y se descarta lo que no se
puede sostener. Nada se inventa y nada se contradice.

### 1.2 `2026_08_27_Tamaño_Empresas_CRM.xlsx` — real, no etiquetado

**Qué es.** 111.174 filas del CRM de ProColombia cruzadas contra RUES y
Superintendencia de Sociedades. Trae geografía, CIIU, tamaño, tipo de
identificación y la variación de nombre que ocurre de verdad.

**Por qué no puede ser la verdad por sí solo.** Es la **salida de un proceso
de cruce anterior**, no una verificación independiente. Medir contra él sería
medir el acuerdo con ese proceso, no el acierto. Un enlazador que reprodujera
sus errores sacaría 1,0.

**Qué parte sí sirve.** Las 50.997 filas marcadas `Alta Confianza`, que están
ancladas en identificador: esa parte la sostiene el NIT con independencia del
algoritmo que la produjo. Las 5.122 filas de `Revisión Manual` se **excluyen**
en vez de contarse como positivas o negativas — una etiqueta dudosa envenena
la métrica en las dos direcciones.

---

## 2. La decisión: cuatro estratos, cada uno aportando lo que sabe

Ninguna de las dos fuentes basta. El conjunto se arma por estratos y cada
estrato aporta una dificultad distinta, con su etiqueta explícita para que se
pueda medir por separado.

| ESTRATO | origen | registros | pares | qué aporta |
|---|---|---:|---:|---|
| `CONTACTO` | `ground_truth_grande.csv` | 12.427 | 22.073 | teléfono, dirección, correo, ciudad, intermediarios |
| `RUIDO` | Ground_Truth_Robusto_V3 | 7.368 | 19.305 | ruido tipográfico con nivel etiquetado |
| `REAL` | CRM × RUES × SuperSociedades | 9.101 | 4.996 | variación real de nombre, geografía, CIIU, tamaño |
| `REAL_NEG` | minado de RUES y CRM | 1.590 | 0 | negativos duros con garantía |

Cobertura de variables: `RAZON_SOCIAL` 100 %, `NIT` 74,4 %, `MUNICIPIO`
75,8 %, `DEPARTAMENTO` 35,1 %, `TELEFONO`/`DIRECCION`/`EMAIL` 33,1 %,
`CIIU`/`TAMANO` 18,8 %.

La cobertura es desigual **a propósito**: así son las fuentes reales. Un
conjunto donde todas las columnas están llenas mide un mundo que no existe.

### 2.1 Los negativos: por qué se minan y no se inventan

Un negativo inventado mide la imaginación de quien lo inventa. Estos salen de
dos poblaciones con garantías distintas, y se reportan por separado porque
son dos problemas distintos:

- **`negativo_empresa_similar_*` (1.040).** Empresas del RUES. El NIT del
  registro mercantil es único por construcción: dos NIT distintos son dos
  entes distintos, sin que medie ningún juicio. Se bloquea por los dos tokens
  más largos del nombre sin sufijo societario y se exige Jaro-Winkler ≥ 0,90.
  A la mitad se le **borra el identificador** (`_sin_id`): separarlas exige
  usar geografía, CIIU o tamaño. Ese es el caso que mide criterio múltiple.

  ```
  GLOBAL COLOMBIA SAS         Bogotá, D.C.      Micro
  GLOBAL WIRE DE COLOMBIA SAS Bogotá, D.C.      Micro
  GLOBAL OPCO COLOMBIA S.A.S  Bogotá, D.C.      Micro
  ```

- **`negativo_homonimo_persona` (550).** Cuatro `DIANA RODRIGUEZ` con cédulas
  distintas son cuatro personas. Del nombre no sale nada; separarlas mide si
  el enlazador respeta el documento en vez de dejarse llevar por el parecido.
  Es el modo de falla más caro en producción.

El reparto es 65 % empresas / 35 % personas. El CRM real es 45 % personas,
pero esta es una librería de enlace **empresarial** y el peso va donde está el
uso.

### 2.2 Saneamiento: quitar la contaminación antes de medir

Dos grupos distintos que comparten identificador canónico no son dos entes:
son el mismo partido en dos, y unirlos —que es lo correcto— se contaría como
falso positivo. Eso no mide al enlazador, mide un defecto del conjunto.
`sanear()` descarta esos grupos y los que chocan por nombre **entre**
estratos. Dentro del estrato de contacto la repetición de nombres genéricos es
la dificultad buscada y se respeta.

Última corrida: 108 grupos descartados (50 por identificador compartido, 58
por nombre compartido entre estratos).

---

## 3. Lo que el conjunto reveló y que no se sabía

### 3.1 La variación real de nombre: el 21,7 % no comparte ni un token

Medido sobre 56.119 pares reales anclados en identificador:

| categoría | frecuencia |
|---|---:|
| `sin_tokens_comunes` | **21,7 %** |
| `sufijo_legal` (solo cambia S.A.S. / LTDA) | alta |
| `uno_contiene_al_otro` | media |
| `identico` | media |
| `algun_token`, `mayoria_de_tokens`, `orden_alterado` | resto |

Uno de cada cinco enlaces verdaderos **no comparte una sola palabra** entre
las dos fuentes. Casi siempre es nombre comercial contra razón social de
persona natural comerciante:

```
FRESH PISCINAS         ↔  SANCHEZ LOPEZ FREDY
PAOLINI                ↔  PRADA PARADA WILLIAM
SOTER MARROQUINERA     ↔  FELIPE SALVADOR ROMERO ORTIZ
MAQUICONOS INGENIERIA  ↔  LLANOS URBANO CARLOS ALBERTO
```

Para esos pares **el nombre no aporta nada**: el identificador es la única
evidencia. Cualquier diseño que trate al identificador como "una columna más"
pierde el 21,7 % de los enlaces por construcción.

### 3.2 La geografía no era una variable floja; el comparador lo era

Primera medición, con comparación exacta: departamento coincide en el 59,7 %
de los pares verdaderos. Conclusión aparente: variable poco fiable, no vale la
pena usarla.

**Esa conclusión era falsa.** El desacuerdo más frecuente era `BOGOTA` contra
`BOGOTA D C` — 15.781 casos, casi un tercio del total. No es un desacuerdo, es
otra forma de escribir lo mismo.

Comparando por **contención de tokens** (un conjunto de tokens contiene al
otro), sobre pares verdaderos frente a negativos de nombre confundible:

| variable | regla | verdaderos | negativos | separación |
|---|---|---:|---:|---:|
| DEPARTAMENTO | exacta | 60,9 % | 20,6 % | 40,3 pp |
| DEPARTAMENTO | **contención** | **90,9 %** | 21,0 % | **69,9 pp** |
| MUNICIPIO | exacta | 44,6 % | 14,6 % | 30,1 pp |
| MUNICIPIO | **contención** | **82,2 %** | 14,6 % | **67,7 pp** |

La regla es genérica —no hay tabla de sinónimos de ningún país— y resuelve
`Cali` ≡ `Santiago de Cali`, `Cúcuta` ≡ `San José de Cúcuta`, `Cartagena` ≡
`Cartagena de Indias`. El residuo (Bogotá contra Cundinamarca, Medellín contra
Itagüí) es desacuerdo real: domicilio registrado contra sede de operación.

**La lección general: antes de descartar una variable, revise con qué la está
comparando.** Se implementó como `categoria_tolerante_signed`.

### 3.3 El CIIU es multivaluado, y Jaccard lo destruye

El RUES publica **2,52 actividades económicas por empresa**; la
Superintendencia de Sociedades publica **1**, la principal. Comparar las
cadenas completas da igualdad en el 18,1 % de los pares verdaderos.

| medida | pares verdaderos (n=297) | negativos confundibles (n=986) | pares al azar |
|---|---:|---:|---:|
| igualdad exacta de la cadena | 18,1 % | — | — |
| Jaccard = 1 | 19,5 % | — | — |
| **solapamiento = 1** | **99,3 %** | 20,7 % | 1,3 % |

`|A∩B| / min(|A|,|B|)` separa: 99,3 % contra 20,7 % contra 1,3 %. Jaccard
—`|A∩B| / |A∪B|`— daría 1/2,52 ≈ 0,40 cuando el conjunto chico está contenido
en el grande, y castigaría a un par que es correcto. **Con Jaccard el CIIU
parece inútil; con solapamiento es una de las señales más limpias del
conjunto.** Se implementó como `conjunto_signed`.

### 3.4 El dígito de verificación estaba partiendo entidades en dos

De los 5.312 pares en que dos registros del mismo grupo traen identificadores
textualmente distintos:

- en **5.269 (99,2 %)** uno es el otro más un dígito al final;
- en **5.002 (94,9 % de esos)** ese dígito es exactamente el de verificación
  por módulo 11 de la DIAN.

No es ruido: es la forma normal en que dos fuentes escriben el mismo número.
La librería los trataba como identificadores distintos y **partía el grupo**.
Ver [ADR-0004](adr/0004-identificador-en-forma-canonica.md) para el arreglo y
su medición.

---

## 4. Cómo se usa

```bash
# Reconstruir el conjunto (determinista, semilla 42)
python scripts/construir_benchmark.py \
    --crm  ruta/al/CRM.xlsx \
    --robusto ruta/al/Ground_Truth_Robusto_V3.xlsx

# Medir una configuración
python scripts/banco.py --datos data/benchmark/benchmark_institucional.csv.gz \
    --etiqueta mi_cambio --perfil produccion_estandar

# Comparar contra la línea base, con veredicto y código de salida
python scripts/banco.py --comparar inst_base mi_cambio

# Verificar fuera de muestra (partición por grupo, no por fila)
python scripts/banco.py --datos ... --etiqueta f0 --pliegue 0 --pliegues 3
```

### 4.1 Lea el macro-F1, no solo el F1

El F1 global lo domina el estrato con más pares, y los pares crecen con el
**cuadrado** del tamaño de grupo: `RUIDO` tiene 7.368 registros y 19.305
pares, `REAL` tiene 9.101 registros y 4.996 pares. Un cambio que arruine
`REAL` y no toque `RUIDO` casi no se nota en el F1 global.

Por eso el banco reporta calidad **por estrato** y un **macro-F1** que da a
cada estrato el mismo peso:

```
  ESTRATO        CONTACTO     F1 0.9659  P 0.9760  R 0.9560  (22,073 pares)
  ESTRATO        REAL         F1 0.9648  P 1.0000  R 0.9319  (4,996 pares)
  ESTRATO        RUIDO        F1 0.7454  P 0.8786  R 0.6472  (19,305 pares)
                 macro-F1 0.8920
```

### 4.2 Línea base vigente

`produccion_estandar`, corrida `inst_dv`, versión 0.19.0:

| métrica | valor |
|---|---:|
| precisión / recall / F1 | 0,9385 / 0,8249 / **0,8780** |
| B³ precisión / recall / F1 | 0,9844 / 0,9244 / 0,9534 |
| macro-F1 por estrato | **0,8920** |
| recall CON_ID / SIN_ID | 0,9768 / 0,6523 |
| FP que tocan un negativo | 287 |
| tiempo / memoria pico | 36,8 s / 503 MiB |

---

## 5. Lo que este conjunto todavía no mide

Decirlo importa tanto como lo que sí mide.

1. **Escala.** 30.486 registros. El comportamiento en memoria a 4,4 millones
   se mide aparte, con el cruce RUES × exportaciones.
2. **`positivo_ruido_sin_id` es el 37 % de los pares y no tiene más columnas
   que el nombre.** Ninguna mejora multicriterio puede tocarlo, y como pesa
   tanto, arrastra el F1 global hacia abajo. Léalo en el macro-F1.
3. **CIIU solo aparece en 297 pares verdaderos** (RUES contra
   SuperSociedades). El efecto medido es enorme pero la muestra es chica.
4. **Un solo país.** La estructura —identificador con dígito de control,
   topónimos con formas larga y corta, actividad multivaluada— es general; los
   datos no.
5. **Los negativos de persona homónima son irresolubles sin el documento.**
   Están para vigilar que el enlazador no los una, no para que los resuelva.

---

*Autor: Claude (asesor de Enrique Forero) · 2026-08-29 · v0.19.0*
