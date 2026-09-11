# rues-linker 0.20.0 — Estado, pendientes y aprendizajes

*Entrega del 29 de agosto de 2026. Todo lo que sigue está medido; cada cifra
sale de un JSON en `docs/evidencia/` y se reproduce con un script del repo.*

---

## 1. La respuesta a su pregunta: sí, ya hay base

**No hay que definir una base para evaluación y otra para construcción.** Hay
**dos instrumentos que miden cosas distintas**, y las particiones resuelven lo
demás.

| | **banco** | **conformidad** |
|---|---|---|
| pregunta | ¿mejoró? | ¿sabe hacerlo? |
| conjunto | `data/benchmark/benchmark_institucional.csv.gz` | `data/conformidad/` |
| origen | CRM × RUES × SuperSociedades + 2 generadores | `Ground_Truth_Multicampo_v1.xlsx` (el suyo) |
| tamaño | 30.486 registros · 46.374 pares | 207 registros · 155 pares |
| naturaleza | estadística, variación real | 43 casos frontera diseñados |
| variables | 9 | 13, con **11 tipos de campo** |
| salida | F1, recall, macro-F1 por estrato | **aprobado/reprobado por caso** |
| reprueba si | una métrica retrocede | **un solo caso** falla |
| script | `scripts/banco.py` | `scripts/conformidad.py` |

**Su archivo sirve, y hacía falta.** Con 46.374 pares, un comportamiento roto
que afecta a cuatro casos mueve el F1 en la cuarta cifra decimal: no es que se
note poco, es que no se nota nunca. Y son justo los que producen una fusión
escandalosa en producción — dos empresas en la misma torre, dos que comparten
call center, dos con correo de gmail.

**Evaluación contra construcción se resuelve con particiones**, no con dos
archivos:

```bash
for k in 0 1 2; do
  python scripts/banco.py --datos data/benchmark/benchmark_institucional.csv.gz \
      --etiqueta fold${k} --pliegue $k --pliegues 3
done
```

El reparto es **por grupo, nunca por fila**. Calibre en los pliegues 0 y 1,
reporte con el 2 sellado. Dos conjuntos separados tendrían el defecto
contrario: sus cifras no serían comparables entre sí.

---

## 2. Cómo se usa, en tres órdenes

```bash
pip install rues_linker-0.20.0-py3-none-any.whl

# ¿Mejoró?  (40 s, 476 MiB)
python scripts/banco.py --datos data/benchmark/benchmark_institucional.csv.gz \
    --etiqueta mi_cambio
python scripts/banco.py --comparar v020_base mi_cambio    # veredicto PASA/FALLA

# ¿Sabe hacerlo?  (43 casos, veredicto individual)
python scripts/conformidad.py --corroborar
```

Los dos devuelven código de salida: sirven en CI tal cual.

---

## 3. Estado medido

### Banco — línea base 0.20.0

| métrica | valor |
|---|---:|
| precisión / recall / F1 | 0,9385 / 0,8249 / **0,8780** |
| B³ precisión / recall / F1 | 0,9844 / 0,9244 / 0,9534 |
| **macro-F1 por estrato** | **0,8920** |
| recall CON_ID / SIN_ID | 0,9768 / 0,6523 |
| FP que tocan un negativo | 287 |
| tiempo / memoria pico | 40,0 s / 476 MiB |

Por estrato: CONTACTO 0,9659 · REAL 0,9648 (precisión **1,0000**) · RUIDO 0,7454.

**Lea el macro-F1, no solo el F1.** Los pares crecen con el cuadrado del
tamaño de grupo, así que el estrato de grupos grandes domina el global.

### Conformidad — 43 casos

| escenario | precisión | recall | F1 | casos firmes |
|---|---:|---:|---:|---|
| deduplicación | 1,0000 | 1,0000 | **1,0000** | 24/24 |
| record linkage | 1,0000 | 1,0000 | **1,0000** | 10/10 |

Sin `--corroborar` da 0,9923 y reprueban C09 y C21 — **exactamente la cifra
que declara la hoja LEEME de su archivo**, medida con la versión 0.10.0 del
motor. Que dos implementaciones independientes coincidan es la mejor evidencia
de que el instrumento mide lo que dice.

### Calidad de ingeniería

| | |
|---|---|
| Suite | **1.384 aprobadas · 2 omitidas · 0 fallos** |
| `ruff check` / `ruff format` | limpios |
| `twine check` | PASSED (wheel y sdist) |
| Instalación limpia | verificada en venv nuevo |
| Corridas de evidencia | 40 JSON en `docs/evidencia/` |
| Criterios del plan | 13 ✅ · 6 🟡 · 18 ⬜ |

---

## 4. Escala: 5 millones en Colab gratuito

El arreglo que más pesa de esta versión.

`aplicar_cannot_link_identificador` convertía la columna **entera** de
identificadores a numpy **dentro del bucle**, una vez por grupo en conflicto.
Con Arrow detrás, cProfile la señaló como el **53 % del tiempo** de la función.

| | antes | después |
|---|---:|---:|
| 1 M de filas, 2 % en conflicto | 437,2 s | **10,2 s** (43×) |
| 5 M de filas, 2 % en conflicto | no terminaba en 10 min | **56,2 s** |
| RSS a 5 M | — | 555 → 618 MiB (+63) |

Resultado idéntico: mismas etiquetas, mismo número de grupos. A 5 M esta fase
pasaba de más de media hora a menos de un minuto — la diferencia entre
terminar una corrida en Colab gratuito y no terminarla.

También medido a escala: la forma canónica del identificador cuesta 35 s sobre
5 M de valores (4,2 M únicos) y ~80 s repartidos en una corrida de 20 M de
pares, sin crecimiento de memoria.

---

## 5. Lo que se hizo en esta versión

### 5.1 Suite de conformidad (ADR-0006)

Su archivo, versionado en `data/conformidad/` (100 KB, 7 hojas) y automatizado.
Valida su propia coherencia al cargar: si la hoja de pares y la columna de
grupo se contradicen, error — esa comprobación existe porque en 0.19.0 el otro
conjunto **sí** se contradecía y costó un diagnóstico entero descubrirlo.

Los dos casos que su archivo marcaba pendientes para "Fase 3" se resuelven
activando `CorroboracionVeto`, que ya existía y estaba apagada:

| | C09 (NIT con un dígito mal) | C21 (dos NIT legítimos) | trampas |
|---|---|---|---|
| sin corroboración | reprueba | reprueba | 6/6 aprueban |
| con corroboración | **aprueba** | **aprueba** | **6/6 aprueban** |

Las seis trampas que siguen aprobando: homónimos, NIT idéntico con nombres
distintos, persona contra empresa, correo genérico compartido, teléfono de
call center y geo idéntica en el mismo edificio.

### 5.2 Un solo catálogo de comparación (ADR-0007)

Su archivo declara 11 tipos de campo. Al verificarlos apareció que la librería
tenía **dos catálogos de comparadores que no se conocían**: el declarativo (14
tipos, con geo/fecha/numérico/dirección/nombre de persona) y el que consume el
**scorer de producción** (11, sin ninguno de esos).

**El camino que procesa millones no podía usar la mitad de lo que la librería
ya sabía hacer.** `matching/puente_campos.py` los unifica delegando en la
misma instancia. **11 → 24 tipos disponibles**, cero lógica duplicada.

```python
linkage(..., extra_features=[("FECHA_CONST", "tipo_fecha",    0.10),
                             ("COORDENADAS", "tipo_geo",      0.10),
                             ("VENTAS",      "tipo_numerico", 0.05)])
```

### 5.3 Dos defectos reales que el puente destapó

- **`pandas 3.0` dejó de convertir los ausentes a la cadena `'nan'`**: los deja
  como `NaN` flotante y el `.map` posterior revienta. **Bastaba una celda vacía
  en una columna de texto para tumbar una corrida real.**
- **El comparador de direcciones no separaba la placa**: `'CRA 7 # 71-21'`
  contra `'CARRERA 7 NO 71 21'` daba 0,600. Ahora da **1,000**.

### 5.4 Bloqueo multivariable (C31): implementado, medido, y **no ayuda aquí**

`engine/lsh/llaves_extra.py`, apagado por defecto, seguro en memoria (~4 bytes
por registro y llave). Se dice con números:

| configuración | F1 | macro-F1 | RUIDO recall | FP negativos |
|---|---:|---:|---:|---:|
| línea base | **0,8780** | **0,8920** | 0,6472 | **287** |
| + llaves (tel., correo, dirección) | 0,8780 | 0,8920 | 0,6472 | 287 |
| + token raro (frec. ≤ 5) | 0,8779 | 0,8921 | 0,6482 | 324 |
| + token raro (frec. ≤ 50) | 0,8454 | 0,8712 | **0,6717** | 2.575 |
| umbral LSH 0,35 | 0,8537 | 0,8676 | 0,5547 | 787 |
| 504 permutaciones | 0,8761 | 0,8905 | 0,6430 | 289 |

Se conservan porque son correctos y son la palanca adecuada para datos donde
el contacto sea el único puente. **Pero aquí no mejoran nada, y así se
documenta.**

---

## 6. Lo que sigue, en orden

1. **C37 (P0)** — discriminar bajo corrupción extrema del nombre **sin**
   identificador. Son 17.055 pares, el 37 % del conjunto, resueltos hoy al
   60 %. **Y cambia el diagnóstico anterior:** en 0.19.0 escribí que era un
   problema de bloqueo; lo medí y no lo es. Los pares son alcanzables —el
   82,8 % tiene Jaccard de trigramas ≥ 0,30— pero toda forma de alcanzarlos
   inunda el scorer de pares falsos que cuestan más de lo que ganan. El cuello
   está en el scorer.
2. **C35** — medir `min_corroborantes` 1 contra 2 sobre datos reales. La suite
   no los distingue; la elección actual es prudencia declarada, no evidencia.
3. **C01** — el diagnóstico de identificadores del notebook reporta una cifra
   de muestra sesgada como si fuera exacta. Induce a error a quien la lee.
4. **C36** — el catálogo de **bloqueo** sigue partido entre los dos motores;
   el de comparación ya no.
5. **C12** — banco y conformidad devuelven código de salida; falta que CI los
   corra solo.

**Lo que hace falta de usted:** correr el proceso sobre una base real y revisar
a mano una muestra estratificada. `docs/evidencia/prediccion_<etiqueta>.parquet`
trae la partición predicha con `ID_GROUP`, `CASO`, `REGIMEN` y `FUENTE` por
fila, lista para muestrear. Eso es lo único que ningún banco reemplaza: C14
(precisión sobre datos reales con revisión humana) sigue parcial por eso.

---

## 7. Aprendizajes de esta fase

1. **Un conjunto de referencia hay que auditarlo con la misma severidad que el
   código.** El anterior se contradecía a sí mismo —pedía unir registros con
   identificadores distintos y a la vez prohibía hacerlo— y ninguna
   configuración podía sacar F1 = 1. Estuvo así seis meses porque nadie miró el
   recall por régimen.
2. **Antes de descartar una variable, revise con qué la está comparando.**
   Departamento parecía flojo (60,9 % de coincidencia) hasta compararlo por
   contención de tokens (90,9 %). El comparador es parte del diseño de la
   variable, no un detalle de implementación.
3. **Perfile antes de optimizar.** En el cuello del cannot-link mi primera
   hipótesis fue un `flatnonzero` por grupo, la corregí y el tiempo no bajó.
   El culpable era otro, y solo apareció con cProfile.
4. **Una optimización sin medición es deuda.** Vectorizar el módulo 11 con
   matrices *parecía* obvio y salió **peor** (39,5 s contra 35,3 s). Se
   revirtió y quedó anotado en el docstring.
5. **"Ya que estoy, limpio todo" cuesta recall.** La primera corrección de
   direcciones borraba toda la puntuación y bajaba el recall del baseline de
   0,8940 a 0,8808 sin arreglar ningún caso adicional. Se redujo al defecto
   real —puntuación entre dos dígitos— y el baseline quedó intacto.
6. **Un resultado negativo bien medido vale tanto como uno positivo.** Que
   `fuentes_mixtas` no generalice, que el bloqueo multivariable no ayude aquí y
   que bajar el umbral LSH empeore el recall son tres cosas que ahora nadie
   tiene que volver a descubrir.
7. **Dos instrumentos, no uno.** Ninguna métrica agregada responde "¿sabe hacer
   esto?", y ningún conjunto de 43 casos responde "¿mejoró un 2 %?".

---

## 8. Contenido de la entrega

| archivo | qué es |
|---|---|
| `rues-linker-0.20.0-codigo.zip` | **el repositorio completo** — descomprimir y subir a Drive |
| `rues-linker-0.20.0-codigo.tar.gz` | lo mismo, como complemento |
| `paquete/rues_linker-0.20.0-py3-none-any.whl` | instalable con `pip install` |
| `paquete/rues_linker-0.20.0.tar.gz` | distribución de fuentes (PyPI) |

Dentro del repositorio: `CLAUDE.md` (reglas de trabajo), `CHANGELOG.md`,
`docs/BITACORA.md` (qué se hizo y qué no funcionó), `docs/PLAN.md` (37
criterios con su estado), `docs/BENCHMARK.md`, `docs/CONFORMIDAD.md`,
`docs/adr/` (7 decisiones con su evidencia) y `docs/evidencia/` (40 corridas).
