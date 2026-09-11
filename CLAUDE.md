# CLAUDE.md — Guía de trabajo para este repositorio

Este archivo lo lee un agente (o una persona nueva) antes de tocar el código.
Contiene lo que no es obvio leyendo los archivos y lo que, si se ignora,
produce una regresión.

---

## 1. Qué es esto

`rues-linker` resuelve **entidades**: dado un conjunto de registros que vienen
de fuentes distintas y describen a las mismas personas u organizaciones,
decide cuáles son la misma y produce un registro consolidado con su tabla
correlativa.

Nació para cruzar el registro mercantil colombiano (RUES) contra bases de
comercio exterior, pero el problema es universal: censos de damnificados tras
una catástrofe, matrículas escolares entre secretarías, historias clínicas
entre prestadores, padrones de beneficiarios entre programas sociales. En
todos ellos hay identificadores que a veces están y a veces no, nombres mal
escritos, y un costo asimétrico entre fusionar de más y fusionar de menos.

**Restricción de diseño que lo explica casi todo:** debe correr en Google
Colab Free —unos 12,7 GB de RAM, sesión de 12 horas, disco por FUSE— sobre
bases de 2 a 5 millones de registros. Toda decisión de arquitectura sale de
ahí.

---

## 2. Reglas que no se negocian

1. **Ninguna mejora entra sin una corrida del banco antes y otra después.**
   El banco está en `scripts/banco.py` y usa siempre el mismo conjunto de
   referencia. Ver §4.
2. **Nada de bucles de Python sobre pares o filas.** Dos incidentes de
   producción salieron de ahí: un diccionario de 83 millones de cadenas en el
   bloqueo por NIT (0.17.3, OOM en Colab) y un bucle por par en el re-scoring
   IDF. Si una función recibe un lote, se vectoriza.
3. **Una regla se escribe una sola vez.** La política de fuentes vivía
   duplicada en dos módulos; se corrigió una copia y la otra siguió rota
   durante una versión entera. Hoy vive en
   `engine/lsh/politica_pares.py` y los dos caminos la llaman.
4. **Un cambio de comportamiento se declara.** Perillas nuevas entran con
   valor por defecto que preserva la paridad exacta, y la paridad se verifica
   con la huella de la partición, no de memoria.
5. **Fail-fast con mensaje accionable.** El formato es: qué pasó, por qué
   importa, qué hacer. Un parámetro desconocido nunca se ignora en silencio.
6. **El español es el idioma del código nuevo.** Nombres, docstrings y
   mensajes. El código heredado en inglés se traduce cuando se toca, no antes.

---

## 3. Mapa del repositorio

```
src/record_linkage/
  api.py                    Fachada de alto nivel: linkage(), dedupe(), link()
  config/profiles.py        Perfiles. CERO números mágicos fuera de aquí
  ingestion/                Lectura, contratos de fuente, DuckDB, dimensionado
  processing/
    text.py, nit.py         Limpieza de texto y canonicalización de identificadores
    saneamiento.py          Entidades HTML, mojibake, siglas partidas (v0.22.0)
  engine/
    lsh/                    Bloqueo: MinHash-LSH, bloqueo por NIT, política de pares
    scorer.py               Scoring vectorizado de pares candidatos
    cannot_link.py          Restricciones duras entre registros
    cobertura.py            Cobertura por estrellas: corta el encadenamiento (v0.22.0)
  matching/
    comparadores_extra.py   Registro de comparadores de variables adicionales
    idf.py                  Ponderación de tokens por informatividad
    nombre_idf.py           Comparador de razones sociales sin NIT (v0.22.0)
    genericos.py            Términos no distintivos, DECLARADOS (v0.22.0)
  paises/                   Catálogo ISO 3166 en español + canonizador (v0.22.0)
  golden/                   Selección del registro consolidado
  flujo/
    cruce.py                Orquestación de alto nivel: ConfigCruce, ejecutar_cruce
    importadores.py         Deduplicar sin identificador: nombre + país (v0.22.0)
  evaluation/
    banco.py                Banco de pruebas reproducible
    comparador.py           Veredicto entre dos corridas
docs/
  BITACORA.md               Qué se hizo, cuándo, con qué evidencia
  PLAN.md                   Los 32 criterios de mejora y su estado
  BANCO.md                  Cómo se mide
  BENCHMARK.md              Qué mide el conjunto de referencia y qué no
  CONFORMIDAD.md            Los 43 casos frontera, con veredicto por caso
  adr/                      Decisiones de arquitectura, una por archivo
  evidencia/                JSON de cada corrida del banco
```

Las fases del pipeline se llaman L1…L6 en todo el código:
**L1** preparación · **L2** candidatos (bloqueo) · **L3** scoring ·
**L4** clustering · **L5** registro consolidado · **L6** reportes.

`flujo/importadores.py` es un camino aparte para bases **sin identificador**
(solo nombre y una categórica de bloqueo). No toca la ruta de producción RUES:
es aditivo y el banco entrega la misma huella (ADR-0009).

---

## 4. Cómo se mide (obligatorio antes y después de cualquier cambio)

```bash
BANCO="--datos data/benchmark/benchmark_institucional.csv.gz"

python scripts/banco.py $BANCO --etiqueta antes      # línea base
# ...cambio...
python scripts/banco.py $BANCO --etiqueta despues
python scripts/banco.py --comparar antes despues     # veredicto PASA / FALLA
```

**Conjunto de referencia: `data/benchmark/benchmark_institucional.csv.gz`**
— 30.486 registros · 11.478 grupos · 46.374 pares verdaderos · 9 fuentes ·
4 estratos · 11 casos · 18 columnas. Se arma con
`scripts/construir_benchmark.py` (semilla 42) a partir de datos reales del CRM,
RUES y SuperSociedades más dos generadores. Ver
[docs/BENCHMARK.md](docs/BENCHMARK.md) para qué mide cada estrato y qué NO
mide el conjunto.

`data/ground_truth/ground_truth_grande.csv` sigue existiendo y es hoy el
estrato `CONTACTO` del conjunto grande. **No lo use solo:** no tiene variación
real de nombre, y calibrar contra él produjo el sobreajuste de
`fuentes_mixtas` en 0.18.0.

El banco mide en una sola pasada calidad (precision, recall, F1, B³, recall
por régimen y por caso, precisión/recall/F1 **por estrato** y macro-F1),
tiempo total y por fase, pico de RSS, bytes en disco, candidatos generados y
huella de la partición.

**Lea el macro-F1, no solo el F1.** Los pares crecen con el cuadrado del
tamaño de grupo, así que el estrato de grupos grandes domina el F1 global y
puede esconder por completo lo que pasa en los demás.

### El segundo instrumento: conformidad por caso

```bash
python scripts/conformidad.py --corroborar    # 43 casos, veredicto individual
```

El banco dice **cuánto** mejoró; la conformidad dice **qué sabe hacer**. Con
46.374 pares, un comportamiento roto que afecta a cuatro casos mueve el F1 en
la cuarta cifra decimal y no se nota nunca — y son justo los que producen una
fusión escandalosa en producción. **Corra los dos antes de publicar un
cambio.** Ver [docs/CONFORMIDAD.md](docs/CONFORMIDAD.md) y ADR-0006.

> **Y compruebe que corrió.** El fixture de conformidad hace `pytest.skip` si
> no encuentra `data/conformidad/`. Hasta la 0.22.0, con el paquete **instalado**
> la ruta se resolvía contra `site-packages` y 8 casos se saltaban en silencio:
> verde sin haber medido. Desde la 0.22.1 lo resuelve `localizar_conjunto()` y
> hay pruebas que verifican que **no** se salta. Si ve `skipped` en
> `test_conformidad_v020.py`, la compuerta no midió nada — trátelo como fallo.

**Validación fuera de muestra.** Un umbral elegido mirando todos los datos
siempre parece mejor de lo que es. Para calibrar:

```bash
for k in 0 1 2; do
  python scripts/banco.py --etiqueta fold${k} --pliegue $k --ajuste ...
done
```

Los pliegues se reparten **por grupo**, nunca por fila.

---

## 5. Cosas que parecen razonables y no lo son

- **Escribir una prueba que lea archivos del repositorio sin preguntarse si se
  incluye a sí misma.** Ha pasado **dos veces**: la prueba que verificaba los
  marcadores de pytest se detectaba leyendo su propio código fuente, y la que
  exigía notebooks sin salidas embebidas incluía al notebook 08 — que es el que
  la ejecuta, y al que Colab le autoguarda las salidas mientras corre. **Una
  compuerta que no puede pasar no es estricta: está rota**, y enseña a
  desactivarla. Antes de escribirla: ¿el archivo que la contiene, o el que la
  está ejecutando, está en el conjunto que inspecciona?
- **Dejar que una invariante sea el primer aviso de un hueco de datos.** La
  invariante «el país final está en el catálogo o marcado» acertaba, pero
  minutos tarde y sin decir qué grafías. Y no veía lo grave: que esas filas se
  fusionaban entre sí. Si un hueco se puede detectar con un mapeo barato,
  se detecta en `preparar()`, con la lista y los remedios; la invariante queda
  como red, no como detector. Y lo que se aísla se **mide** con una invariante
  propia, no se supone.
- **Creer que `test_es_determinista` prueba determinismo.** Prueba que dos
  llamadas en el **mismo** proceso coinciden. La semilla de hash de Python es
  fija dentro de un proceso y distinta entre procesos: cualquier orden que
  salga de un `set` de cadenas cambia de sesión a sesión sin que esa prueba
  lo vea. Pasó: los sufijos multi-token se ordenaban desde un `set` y la misma
  base daba 99.897 o 99.898 importadores según la sesión. Si algo construye
  un orden a partir de un `set`, use una clave **total** (`sorted(...,
  key=(…, x))`), y pruébelo en **subprocesos** con `PYTHONHASHSEED` distinto.
- **Escribir un contrato sobre la implementación que hay hoy.** `«la celda de
  entorno tiene que hacer pip install»` parecía un contrato y era una foto. El
  invariante era «no correr código rancio ni incompleto». La foto rechazó una
  celda de entorno correcta (el notebook 07, que importa desde Drive) y, peor,
  **llevaba tiempo sin ver un hueco real** en el notebook 05, que no verificaba
  que el paquete quedara completo: cumplía la letra —tenía su `pip install`— sin
  cumplir el invariante. Un contrato se escribe sobre la propiedad, no sobre
  cómo se consigue.

- **Usar el perfil `fuentes_mixtas`.** Ganaba +0,014 de F1 sobre el conjunto
  sintético y **pierde 0,014** sobre el institucional, con 75 % más tiempo y
  46 % más falsos positivos sobre negativos. Era sobreajuste. Úselo solo si
  sus datos se parecen a contacto sintético, y mida.
- **Bajar `lsh_threshold` para ganar recall.** Funciona, pero cuesta 55 % más
  de tiempo y baja la precisión. Solo tiene sentido acompañado de un
  discriminador.
- **Comparar identificadores como cadenas.** El mismo NIT viaja con y sin
  dígito de verificación en el 99,2 % de las discrepancias internas de un
  grupo. Use `matching.identificadores.base_canonica`; la perilla
  `dv_es_mismo_identificador` está en `True` por algo (ADR-0004).
- **Comparar una lista con igualdad exacta.** El CIIU trae 2,52 actividades
  por empresa en RUES y 1 en SuperSociedades: igualdad reconoce el 18 % de los
  pares verdaderos y solapamiento el 99 %. Use `conjunto_signed`.
- **Creer que el recall que falta se arregla con más candidatos.** Medido en
  v0.20.0: bajar el umbral LSH, afilar la curva con más permutaciones y añadir
  llaves de bloqueo empeoran o no mueven el recall del estrato de ruido. Los
  pares perdidos ya son alcanzables; lo que falta es discriminarlos.
- **Usar `pd.Series(x).astype(str)` antes de un `.map`.** En pandas 3.0 los
  ausentes se quedan como `NaN` flotante y el `.map` revienta. Use
  `matching.comparators._a_texto`.
- **Convertir una columna a numpy dentro de un bucle.** Con Arrow detrás la
  conversión no es barata y cuesta O(n) cada vez. Medido en v0.20.0: el
  cannot-link tardaba 437 s sobre 1 M de filas por esto, y 10,2 s después de
  sacar la conversión del bucle.
- **Optimizar por intuición.** En ese mismo caso mi primera hipótesis fue otra
  —un `flatnonzero` por grupo—, la corregí y el tiempo no bajó. Perfile
  (`cProfile`) antes de tocar nada.
- **Descartar una variable por su tasa de coincidencia.** DEPARTAMENTO parecía
  flojo (60,9 %) hasta que se comparó por contención de tokens (90,9 %). Antes
  de descartar una variable, revise con qué la está comparando:
  `python scripts/diagnostico_variables.py`.
- **Declarar TELEFONO o EMAIL como evidencia con el comparador exacto.** No
  cambia nada: dentro de un mismo grupo el teléfono aparece en tres formatos.
  Use `telefono_signed` y `email_signed`.
- **Subir `idf_weight_blend` global.** Mejora la precisión y destruye el
  recall del régimen con identificador. La perilla que sirve es
  `idf_weight_blend_sin_identificador`.
- **Confiar en el diagnóstico de identificadores de la §7 del notebook.**
  Muestrea, y hasta 0.18.0 muestreaba mal. Ver ADR-0003.
- **Medir con un conjunto distinto cada vez.** Es como se colaron las dos
  regresiones que llegaron a producción.
- **Dar por garantizada una columna de salida que ninguna invariante exige.**
  `NIT_FINAL` y `RAZON_SOCIAL_FINAL` se producían desde hacía versiones y
  `flujo/cruce.py` no las mencionaba ni una vez: tres `try/except` podían
  perderlas escribiendo solo una advertencia. Si algo es entregable, va en
  `_verificar_invariantes` (v0.21.0, ADR-0008).
- **Inferir los genéricos del corpus por frecuencia cuando no hay NIT.** El
  IDF aprendido no distingue una marca de un genérico, y no por azar: la
  frecuencia documental de una marca crece con el número de variantes de la
  MISMA empresa. Medido sobre 105.705 razones sociales, `IMPORTADORA` (df=737)
  pesa 5,96 y `ZELECTA` (df=490) pesa 6,37 — la marca pesa MENOS. Use
  `matching.genericos`, que es un dato declarado (ADR-0009).
- **Dejar que "A contenido en B" cuente como evidencia sin más.** Cualquier
  nombre corto absorbe todo lo que lo mencione: medido, `INTERNATIONAL` se
  llevó 157 razones sociales a un mismo grupo y `MQE` 214. La contención de
  `nombre_idf` entra solo tras tres puertas.
- **Agrupar por componentes conexas cuando el nombre es la única evidencia.**
  Es single-linkage y encadena: `a≈b`, `b≈c` ⇒ une `a` con `c`. Produjo grupos
  de 200 empresas distintas. Use `engine.cobertura` después del clustering.
- **Canonizar países por similitud de cadenas.** `TURQUIA`/`TURKIYE` y
  `CHEQUIA`/`REPÚBLICA CHECA` no se parecen; `GUINEA`/`GUINEA-BISSAU` sí se
  parecen y son países distintos. Use `paises.canonizar_pais`.
- **Convertir puntuación a espacio antes de plegar tildes.** El patrón
  `[^\w\s()]` se evalúa con semántica ASCII (pandas 3 delega en Arrow/RE2), así
  que borra la "É" de "AMÉRICA". Medido: la canonización de países cayó del
  100 % al 57 % sin ningún error visible. Use `processing.saneamiento`, que fija
  el orden.
- **Poner un piso de informatividad en frecuencia absoluta.** Un token en 1.000
  nombres es genérico en un corpus de 100.000 y es TODO el corpus en uno de
  1.000. `fraccion_max_distintivo` es relativa por eso.
- **Puntuar toda la unión de candidatos de una vez, o guardar texto por par.**
  El costo es por par y el número de pares no lo decide usted: la partición
  USA de la base real trajo 24,5 M de candidatos, 11,4 GiB de pico y una tabla
  de decisiones de 2,6 GiB con los dos nombres en cada fila (0.22.4). Puntúe
  por lotes (`pares_por_lote`), acumule claves escalares y no pares, y guarde
  índices, no strings — los nombres se resuelven al pedirlos.
- **Reparar en silencio.** Es lo que convirtió tres fallos internos en un
  problema que el usuario descubrió días después. Repare, pero deje
  constancia — y distinga el trabajo normal de la anomalía, o el aviso se
  vuelve ruido que se aprende a ignorar.

---

## 6. Entorno de desarrollo

```bash
pip install -e ".[dev]"
ruff check src tests scripts && ruff format --check src tests scripts
pytest -q -m "not slow"          # rápido, para iterar
pytest -q                         # completo, incluye el banco
```

La suite completa consume memoria; en un contenedor chico córrala por
bloques: `pytest $(ls tests/test_[a-e]*.py) -q` y así.

---

## 7. Antes de entregar

- [ ] `ruff check` y `ruff format --check` limpios
- [ ] Suite completa verde
- [ ] Banco corrido antes y después, con `--comparar` en PASA
- [ ] Entrada en `docs/BITACORA.md` con la evidencia
- [ ] ADR si la decisión cambia comportamiento
- [ ] `CHANGELOG.md` actualizado
- [ ] Versión subida en `pyproject.toml` y en los notebooks
