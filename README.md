# rues-linker

> **Pipeline de deduplicación y record linkage para fuentes empresariales colombianas**
> (RUES, DIAN, CRM, SUPERSOCIEDADES, EXPORTACIONES). Refactor estructurado del
> notebook `2026_02_15_DEDUPLICAR_Y_RECORD_LINKAGE_.ipynb` a paquete `.py`
> con auditoría completa y métricas validadas contra ground truth **sintético**.

[![CI](https://img.shields.io/badge/CI-configurado-blue)](.github/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-0.22.4-orange)](CHANGELOG.md)
[![Status](https://img.shields.io/badge/status-pre--1.0-yellow)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-suite%20de%20release-brightgreen)](tests/)
[![Coverage gate](https://img.shields.io/badge/cobertura%20de%20l%C3%ADneas-gate%2060%25-yellow)](pyproject.toml)
[![F1 vs GT](https://img.shields.io/badge/F1%20vs%20GT%20sint%C3%A9tico-0.84-brightgreen)](CHANGELOG.md)

**Autor:** Enrique Forero · **Versión:** `0.22.4` (pre-1.0) · **Python:** ≥ 3.10 · **Licencia:** Apache-2.0

> ## 📍 Estado actual: pre-1.0 (`0.x`)
>
> Esta es una **candidata de producción supervisada**. Aún NO está publicada
> en PyPI y la API puede cambiar entre minor releases.
>
> - **¿Funcional?** Sí: compuertas de tests, tipado del anillo crítico,
>   seguridad, build y pruebas con archivos reales están documentadas en la
>   auditoría de la entrega.
> - **¿Listo para decisiones críticas autónomas?** No. Los archivos reales
>   adjuntos no tienen etiquetas humanas de identidad; sin ground truth real no
>   se pueden certificar precision/recall/F1.
> - **¿Listo para operación controlada?** Sí, con revisión de casos ambiguos,
>   presupuesto de candidatos y monitoreo de recursos.

---

## ¿Qué hace este paquete?

A partir de varias fuentes con razones sociales y NITs que se superponen
(RUES, DIAN, CRM, SUPERSOCIEDADES, EXPORTACIONES), construye un **registro
único por empresa real** (golden record) usando:

1. **Limpieza** vectorizada de texto y NITs (`processing/`)
2. **Bloqueo LSH** con MinHash sobre n-gramas (`engine/lsh/`)
3. **Scoring** de pares candidatos (similitud de nombre + NIT + fonética)
4. **Clustering** de entidades por componentes conexos
5. **Golden record** generado por reglas de prioridad + pesos de calidad de fuente

Trabaja con una arquitectura **híbrida disco/memoria**. En el flujo 0.17.3,
DuckDB proyecta, limita y colapsa los archivos comprimidos en disco; pandas
recibe solo los representantes. Checkpoints, candidatos y scores intermedios
también viven en disco, el clustering los recorre con Union-Find incremental y
`modo_resultado="disco"` publica la expansión física y el payload con DuckDB,
sin crear una correlativa final de millones de filas en pandas. Compactos y
resultados se publican como generaciones inmutables señaladas por manifiestos
atómicos; los metadatos se adjuntan después mediante un segundo commit
protegido contra interleaving.

La ruta de bajo consumo es explícita para no romper compatibilidad: los
defaults públicos continúan siendo ingesta pandas, resultado `DataFrame` y
Excel. Para volumen use conjuntamente `motor_ingesta="duckdb"`,
`modo_resultado="disco"`, `exportar_excel=False` y
`ajustes_perfil={"liberar_fuentes_tras_l1": True}`.

### Bases SIN identificador: solo nombre y país (v0.22.0)

Hay bases donde no existe ningún NIT: destinatarios de exportación, padrones de
compradores, listas de contrapartes. Ahí el nombre es **toda** la evidencia y
los comparadores de cadena solos producen desastres silenciosos — medido sobre
211.949 destinatarios reales, la palabra `INTERNATIONAL` terminó agrupando 157
razones sociales distintas.

```python
from record_linkage.flujo import ConfigImportadores, deduplicar_importadores

cfg = ConfigImportadores(
    col_razon_social="RAZON_SOCIAL_DESTINATARIO",
    col_pais="PAIS_DESTINO_FINAL",
    cols_metricas=("VALOR_FOB_USD_TOTAL",),
    col_peso_economico="VALOR_FOB_USD_TOTAL",
)
r = deduplicar_importadores(df, cfg)
assert r.todo_ok                     # once invariantes; si falla, no use nada
r.correlativa.to_parquet("correlativa.parquet")
print(r.resumen())
```

El país es **bloqueo duro** (dos registros de países distintos jamás se
comparan) y se canoniza contra un catálogo ISO 3166 declarado, no por parecido:
`TURQUIA`/`TURKIYE` no se parecen y son el mismo país; `GUINEA`/`GUINEA-BISSAU`
se parecen y son distintos.

Sobre la base de referencia: 211.949 registros → 100.008 importadores (−52,8 %;
99.897 hasta 0.22.3, antes de tratar `A LA ORDEN`/`TO ORDER` como sin nombre),
con las 10 invariantes en OK (desde F2.12 son 11: se añadió la de `CONFIANZA`),
y **el mismo resultado bit a bit en procesos con
distinta semilla de hash** (desde 0.22.3; antes variaba ±1). Tiempos: 226 s en
el contenedor de desarrollo, 396 s en Colab Free. Precisión ponderada **0,966** sobre 160 asignaciones revisadas a mano;
recall del bloqueo **1,000** medido por fuerza bruta. Ver
[`docs/ANALISIS_IMPORTADORES.md`](docs/ANALISIS_IMPORTADORES.md) y ADR-0009.

### Fuentes donde solo algunos registros traen identificador (v0.18.0)

Es el caso común fuera de un registro mercantil: un padrón de damnificados,
una matrícula escolar, un censo de pacientes. El régimen con identificador se
resuelve solo; **todo el error se concentra en el que no lo trae**.

```python
resultado = linkage(fuentes, profile="fuentes_mixtas")
```

Medido sobre `data/ground_truth/ground_truth_grande.csv` (12.427 registros,
22.073 pares verdaderos, 81 % con identificador):

| métrica | `produccion_estandar` | `fuentes_mixtas` |
|---|---|---|
| precision | 0,9760 | **0,9866** |
| recall | 0,9560 | **0,9733** |
| F1 | 0,9659 | **0,9799** |
| recall SIN identificador | 0,8344 | **0,9165** |
| tiempo | 25,6 s | 39,8 s |

Validado fuera de muestra en tres pliegues por grupo: ΔF1 medio +0,0105.
Cuesta 55 % más de tiempo, porque el bloqueo más abierto genera 1,4 M de
candidatos en vez de 850 K.

### Variables de contacto como evidencia

Un teléfono escrito `312 1897799`, `312-189-7799` y `+573121897799` es el
mismo número. Los comparadores canonicalizan antes de comparar:

```python
resultado = linkage(
    fuentes,
    extra_features=[
        {"column": "TELEFONO", "type": "telefono_signed", "weight": 0.10},
        {"column": "EMAIL", "type": "email_signed", "weight": 0.10},
    ],
)
```

| tipo | qué normaliza |
|---|---|
| `telefono_signed` | últimos 7 dígitos: ignora prefijo país, indicativo y separadores |
| `email_signed` | dominio distinto → −1; mismo dominio → similitud graduada del buzón |
| `documento_signed` | dígitos, sin separadores ni ceros a la izquierda |

Registrar uno nuevo no obliga a tocar el scorer:

```python
from record_linkage.matching.comparadores_extra import registrar

@registrar("mi_comparador", firmado=True, descripcion="...")
def _mio(a, b):
    ...
```

> **Medido:** CIUDAD como evidencia adicional **empeora** el F1
> (0,9659 → 0,9514). Coincidir de ciudad es evidencia débil que empuja pares
> dudosos por encima del umbral. No la active sin medir en sus datos.

### Cómo se decide que algo mejoró

```bash
python scripts/banco.py --etiqueta antes
python scripts/banco.py --etiqueta despues --perfil fuentes_mixtas
python scripts/banco.py --comparar antes despues     # PASA / FALLA
```

Un conjunto de referencia fijo, una invocación, un formato de salida. Ver
[`docs/BANCO.md`](docs/BANCO.md).

### Elegir el motor sin adivinar (v0.17.3)

`motor_ingesta="auto"` mide el universo **antes** de leer nada y decide solo:

```python
cfg = ConfigCruce(
    fuentes=[...],
    workspace="/content/drive/MyDrive/resultados",
    motor_ingesta="auto",        # mide y elige
    modo_resultado="auto",       # sigue al motor elegido
    umbral_filas_disco=1_500_000,
    exportar_excel=False,
)
```

El dimensionado cuesta **kilobytes leídos, no gigabytes**: `num_rows` del pie
de un Parquet, el tamaño descomprimido que el ZIP ya declara en su directorio
central, el trailer `ISIZE` de un GZIP; el ancho de fila sale de 16 ventanas de
128 KiB repartidas por todo el archivo (muestreo sistemático estratificado).
Contra las bases reales de ProColombia el error fue **+0,17 % en 2 segundos**
sobre 952.288 filas, y **+0,19 % en 3 segundos** sobre 2,69 M.

Tres reglas gobiernan la decisión:

1. Si el tamaño **no se puede establecer**, gana DuckDB. Correr más lento es
   recuperable; quedarse sin RAM a mitad de camino no lo es.
2. Una cifra estimada se compara **inflada un 25 %**, para que el error del
   muestreo nunca empuje hacia el motor que se cae.
3. `dir_procesados` y `forzar_relectura` —cachés del camino pandas— se
   desactivan con aviso si la resolución va a DuckDB. En cambio
   `colapsar_duplicados_exactos=False` cambia el contrato de salida, así que
   ahí se respeta la decisión del usuario y se conserva pandas, avisando.

Para decidir por su cuenta sin ejecutar el cruce:

```python
from record_linkage.ingestion import resumir_universo

universo = resumir_universo(fuentes)
print(universo.filas, universo.exacto)
for est in universo.por_fuente:
    print(est.fuente, est.filas, est.metodo, est.detalle)
```

> **Escala y Colab.** Use `motor_ingesta="duckdb"`, temporales en `/content` y
> un `DuckDBIngestionSettings(memory_limit="512MB", threads=2, ...)`. El límite
> de muestra se aplica durante el staging, no después de leer todo el ZIP. Esto
> elimina la causa concreta del OOM auditado, pero no convierte al pipeline en
> streaming extremo-a-extremo: LSH, scoring y golden record aún consumen memoria
> proporcional a los representantes compactados. El notebook 06 arranca con
> una muestra segura; quite el límite solo después de medir el runtime concreto.
> En Colab publica primero en `/content`, porque lock/`replace`/`fsync` no tienen
> garantías equivalentes sobre Drive/FUSE, y opcionalmente copia al final un
> snapshot único cuyos artefactos vuelve a verificar con SHA-256.

Los dos ZIP auditados contienen 952.288 filas físicas (57.186 RUES y 895.102
exportaciones), ambas en CP1252 estricto. Proyectar solo NIT y razón social deja
76.591 combinaciones distintas; añadir `DEPARTAMENTO` aunque no puntúe eleva el
trabajo a 89.931 representantes (+17,4 %). Los adjuntos no traen etiquetas
humanas: las comprobaciones por NIT y las invariantes estructurales no sustituyen
precision, recall ni F1 reales.

En la prueba integral 0.17.3 del host auditado, la ruta disk-first procesó y
restituyó **952.288/952.288 filas**, compactó a 76.591 representantes, produjo
74.217 entidades, 286.520 candidatos y 2.366 pares puntuados, y cumplió ocho
invariantes estructurales. Tardó 220,137 s y alcanzó 677,945 MiB de RSS pico.
La partición canónica coincide fila por fila con la 0.15.0. Allí se habían
observado 285,578 s y 693,758 MiB, pero no fue una configuración operacional
idéntica: 0.15.0 materializó `DataFrame` sin payload; 0.17.3 liberó fuentes tras
L1 y publicó Parquet con payload. La comparación describe el conjunto de
cambios, no atribuye causalidad ni promete recursos para Colab.

### Métricas medidas (no proyectadas)

Sobre el ground truth **sintético** `ground_truth_grande.csv` (12,427 registros, 3,486 grupos verdad; generado de forma determinista con `seed=42` — **no son datos reales del RUES**):

| Perfil | F1 | Precision | Recall | False Positives |
|---|---:|---:|---:|---:|
| `enterprise_scale_4_sources` (IT-7, default antiguo) | 0.0508 | 0.026 | 0.909 | 746,954 |
| **`produccion_calibrada`** (calibrado vs GT — v0.3.0+) | **0.8424** | **1.0000** | 0.7277 | **0** |
| `produccion_calibrada` + Optuna (20 trials, v0.3.2+) | 0.9304 | 0.9996 | 0.8701 | 1 |

**Mejora de IT-7 → calibrado: +79 puntos F1, FP de 747k → 0.**
Metodología de calibración: ver CHANGELOG [0.3.0] y `scripts/medir_con_ground_truth.py`.

---

## Instalación

### Local (recomendado mientras esté pre-1.0)

```bash
git clone https://github.com/EnriqueForero/rues-linker_v2.git
cd rues-linker
pip install -e .

# Con optimización Optuna (opt-in)
pip install -e ".[optimization]"

# Con visualización de reportes (opt-in)
pip install -e ".[viz]"

# Todo
pip install -e ".[all]"
```

### Desde el wheel de la entrega

```bash
pip install rues_linker-0.17.3-py3-none-any.whl
pip install "rues_linker-0.17.3-py3-none-any.whl[optimization]"
```

---

## Inicio rápido

### La API en cinco líneas (v0.9.0)

```python
import pandas as pd
import record_linkage as rl

df = pd.read_parquet("empresas.parquet")   # necesita columnas NIT y RAZON_SOCIAL
res = rl.dedupe(df, carpeta_salida="salidas")  # ruta de producción validada (auto)
print(res.resumen())
print(res.manifiesto["carpeta_salida"])    # salidas/<AAAA-MM-DD_HHMM>_dedupe/: parquet, Excel, manifest
```

`carpeta_salida=` escribe la carpeta del estándar de salida con el escritor
único (atómica, con `manifest.json` y `diccionario.csv`); vale igual para
`rl.link(...)` y `rl.linkage(...)`. Sin ella, nada se escribe fuera del
directorio de trabajo.

### Un cruce completo, con preflight y evidencia (v0.17.3)

`record_linkage.flujo` ejecuta el flujo entero —preflight, copia anti-Drive,
carga proyectada, smoke test, corrida con checkpoints, invariantes, exportes y
metadatos JSON— con la configuración en un solo `dataclass`:

```python
from record_linkage import ColumnType, SourceSpec
from record_linkage.flujo import ConfigCruce, ejecutar_cruce
from record_linkage.ingestion import DuckDBIngestionSettings

cfg = ConfigCruce(
    fuentes=[
        SourceSpec(name="RUES", path=".../rues.zip", compression="zip",
                   delimiter=",", encoding="cp1252",
                   column_mapping={"NIT": "NUMERO_IDENTIFICACION",
                                   "RAZON_SOCIAL": "RAZON_SOCIAL"},
                   column_types={"NIT": ColumnType.IDENTIFIER},
                   null_values={"NIT": ("0000000000000",)}),
        SourceSpec(name="EXPORTACIONES", path=".../dane.zip", compression="zip",
                   delimiter="\t", encoding="cp1252",
                   column_mapping={"NIT": "Nit Exportador",
                                   "RAZON_SOCIAL": "Razon Social"},
                   column_types={"NIT": ColumnType.IDENTIFIER},
                   null_values={"NIT": ("-1", "0", "00"),
                                "RAZON_SOCIAL": ("NO DEFINIDO",)}),
    ],
    workspace="/content/drive/MyDrive/resultados",
    dir_trabajo="/content/rues_linker_trabajo",
    confiables={"RUES"},
    motor_ingesta="duckdb",
    modo_resultado="disco",
    preservar_payload=True,
    exportar_excel=False,
    duckdb_settings=DuckDBIngestionSettings(
        memory_limit="512MB", threads=2,
        temp_directory="/content/rues_linker_duckdb_spill",
    ),
)
resultado = ejecutar_cruce(cfg)
print(resultado.resumen())
print(resultado.cruce_por_fuente().to_string(index=False))
display(resultado.correlativa.preview(20))  # carga acotada y explícita
display(resultado.matriz_presencia())       # QA correcto para N fuentes
```

Los umbrales se ajustan por el camino documentado, con fail-fast:

```python
cfg = ConfigCruce(..., perfil="produccion_estandar", ajustes_perfil={
    "lsh_permutations": 252,      # firma MinHash: + fiel, + costo
    "lsh_threshold": 0.55,        # + alto = menos candidatos, más rápido
    "score_threshold": 0.45,      # + alto = más precisión, menos recall
    "min_name_similarity": 0.30,
    "max_nit_distance": 3,        # typo de NIT solo si el DV no valida
    "cleaning_mode": "BALANCEADO",  # AGRESIVO | BALANCEADO | CONSERVADOR
})
print(resultado.tiempos())        # desglose por fase, no solo el total
```

Una clave mal escrita **falla con la sugerencia correcta**; nunca se ignora en
silencio. `dir_procesados` sigue disponible como caché de fuentes para el motor
`pandas`; la ruta DuckDB vuelve a hacer staging para garantizar que el mapa de
expansión corresponda exactamente al archivo y al límite de la corrida.

Para comparar motores o repetir la medición de recursos use
`scripts/benchmark_duckdb_flow.py`. El script escribe huellas de entrada y
partición, tiempos, RSS, conteos y el resultado de cada invariante en JSON; una
corrida parcial nunca se presenta como si fuera el universo completo.

Deduplicación con el **motor multicampo declarativo** (v0.12.0+) — cualquier
nombre de columna, cualquier combinación de tipos, sin renombrar nada:

```python
import record_linkage as rl
from record_linkage import CampoSpec, EsquemaCampos, TipoCampo

esq = EsquemaCampos(campos=[
    CampoSpec("TAX_ID",  TipoCampo.IDENTIFICADOR, peso=3.0),
    CampoSpec("COMPANY", TipoCampo.NOMBRE_EMPRESA, peso=2.0),
    CampoSpec("PHONE",   TipoCampo.TELEFONO, peso=1.0),
], umbral_score=0.60, min_concordancias=1)

res = rl.dedupe_esquema(df, esq)      # bloqueo derivado del esquema
res.correlativa[["TAX_ID", "COMPANY", "ID_GRUPO"]]
```

**14 tipos soportados** (desde v0.14.0): nombre de empresa/persona, identificador
(veto por discrepancia), teléfono, email, dirección, ciudad, geo (lat/lon),
fecha, numérico (tolerancia relativa o absoluta), categórico, **jerárquico**
(CIIU/HS por niveles), **conjunto** (multi-valor `A;B;C` por Jaccard) y
**booleano** — con política de faltantes por campo (IGNORAR/PENALIZAR/BLOQUEAR)
y corroboración opcional que levanta el veto ante evidencia independiente
fuerte. Comparadores adicionales disponibles: `LevenshteinSigned` y
`FoneticoEspanolSigned` (fonética española real: B/V, S/Z, C(e,i), G dura y
suave, LL/Y, H muda). Recomendado hasta ~500K filas en memoria; a millones, la
ruta Orchestrator en disco de abajo.

¿No sabe qué tipo poner en cada columna? `rl.sugerir_esquema(df)` propone uno
con una tabla de motivos auditable —incluidas las columnas que **omite** y por
qué—. Propone; usted decide.

Cruce de dos bases (record linkage A↔B):

```python
res = rl.link(df_rues, df_aduanas, nombre_a="RUES", nombre_b="ADUANAS",
              trusted={"RUES"})
print(res.metricas["n_grupos_cruzados"], "entidades presentes en ambas bases")
```

`res` es un `ResultadoLinkage`: `.correlativa`, `.golden`, `.metricas` y
`.manifiesto` (trazabilidad total: huella de cada insumo, versiones del
entorno, hash de parámetros, seed). Perfiles por nombre: `rl.get_profile(...)`
sobre el registro único de `config/profiles.py`. Errores de entrada con
formato accionable: qué pasó / por qué importa / qué hacer. **Estos ejemplos
corren en CI** (`tests/test_ejemplos_quickstart.py`): si el README miente, la
suite se pone roja.


### Caso 1: archivos heterogéneos y nombres de columnas distintos

```python
from record_linkage import ColumnType, SourceSpec, linkage

sources = [
    SourceSpec(
        name="RUES",
        path="data_CSV_RUES.zip",
        archive_member="data_CSV_RUES.csv",
        column_mapping={
            "NIT": "NUMERO_IDENTIFICACION",
            "RAZON_SOCIAL": "RAZON_SOCIAL",
        },
        column_types={"NIT": ColumnType.IDENTIFIER},
        temp_dir="/content/rues-linker-tmp",
    ),
    SourceSpec(
        name="EXPORTACIONES",
        path="exportaciones.zip",
        archive_member="Base_Exportaciones_Colombianas_2021-2026 (Junio).txt",
        column_mapping={"NIT": "Nit Exportador", "RAZON_SOCIAL": "Razon Social"},
        column_types={"NIT": ColumnType.IDENTIFIER},
        temp_dir="/content/rues-linker-tmp",
    ),
]

result = linkage(
    sources,
    trusted_sources={"RUES"},
    profile="produccion_calibrada",
    work_dir="/content/rues-linker-run",
    skip_reporting=True,
    collapse_exact_duplicates=True,
)
result["golden"].to_parquet("golden_records.parquet", index=False)
result["correlative"].to_parquet("tabla_correlativa.parquet", index=False)
```

El mapeo siempre tiene dirección `{nombre_canónico: nombre_en_el_archivo}`.
Se soportan CSV/TXT/XLSX/XLSM/Parquet, ZIP y GZIP; si un ZIP contiene varios
archivos candidatos, `archive_member` es obligatorio. `load_source()` lee por
bloques pero concatena la fuente proyectada; `iter_source_chunks()` solo debe
usarse cuando el consumidor también es incremental.

### Caso 2: Calibrar con Optuna contra tu ground truth

```python
from record_linkage.evaluation import OrchestratorOptimizer

# Cargar ground truth (formato: ID_REGISTRO, ID_GROUP, FUENTE, ...)
gt = pd.read_csv("ground_truth_grande.csv", dtype=str)
gt["NIT"] = gt["NIT"].fillna("")
truth = gt[["ID_REGISTRO", "ID_GROUP"]].copy()
sources = {
    src: g[["ID_REGISTRO", "RAZON_SOCIAL", "NIT", "CIUDAD"]].reset_index(drop=True).copy()
    for src, g in gt.groupby("FUENTE") if len(g) >= 2
}

base_cfg = crear_config_orchestrator(perfil="produccion_calibrada", validate=False)
optimizer = OrchestratorOptimizer(base_config=base_cfg, sources=sources, truth=truth)
result = optimizer.optimize(n_trials=20, optimization_target="f1")

best_cfg = result["best_config"]   # listo para producción
print(f"Mejor F1: {result['best_score']:.4f}")
print(f"Mejores params: {result['best_params']}")
```

Notebook completo: **[`notebooks/04_multicampo_y_evaluacion.ipynb`](notebooks/04_multicampo_y_evaluacion.ipynb)**

### Caso 3: Auditar tu config existente

```python
from record_linkage.config import validar_config

reporte = validar_config(mi_config, verbose=True)
# Imprime warnings sobre claves dead/deprecated/partial
print(f"Dead: {reporte['dead']}")
print(f"Deprecated: {reporte['deprecated']}")
print(f"Partial: {reporte['partial']}")
```

---

## Perfiles disponibles

| Perfil | Cuándo usar | Métricas medidas |
|---|---|---|
| **`produccion_calibrada`** ⭐ | Producción real (calibrado vs GT) | F1=0.84, P=1.00, R=0.73 |
| `alta_precision` | Cuando un FP es costoso | F1=0.83, P=1.00 |
| `produccion_estandar` | Balanceado, configuración heredada | No medido contra GT |
| `produccion_exhaustiva` | Máxima cobertura, tolera FP | No medido contra GT |
| `deduplicacion_simple` | Solo dedup intra-fuente | — |
| `prueba_rapida` | Smoke tests | — |
| `config_produccion_it7` (constante) | **Retrocompat** con notebook IT-7 | F1=0.05 (no recomendado) |

`config_produccion_it7` se mantiene por retrocompatibilidad documental.
Tiene parámetros dead/deprecated que el validador reporta.

---

## Ground truth — cómo construir el tuyo

El ground truth (GT) es la única forma honesta de medir si el pipeline funciona
en tus datos. Sin GT, no hay métrica objetiva.

**Flujo de generación de GT:**

Use [`scripts/generar_pares_para_etiquetar.py`](scripts/generar_pares_para_etiquetar.py),
etiquete con [`scripts/active_labeling.py`](scripts/active_labeling.py) y mida
acuerdo con [`scripts/medir_kappa.py`](scripts/medir_kappa.py). El notebook
[`04_multicampo_y_evaluacion.ipynb`](notebooks/04_multicampo_y_evaluacion.ipynb)
muestra cómo evaluar un conjunto ya etiquetado.

**Formato esperado:**

| Columna | Tipo | Descripción |
|---|---|---|
| `ID_REGISTRO` | str | ID único por registro |
| `ID_GROUP` | str | ID del grupo verdad (mismo grupo = misma empresa real) |
| `FUENTE` | str | Nombre de la fuente (RUES, CRM, ...) |
| `RAZON_SOCIAL` | str | Razón social |
| `NIT` | str | NIT (puede estar vacío) |
| `CIUDAD` | str | (opcional) |

GT de ejemplo incluido: `tests/data/ground_truth_grande.csv` (12,427 registros).

---

## Arquitectura

### Componentes principales

```
src/record_linkage/
├── config/            Perfiles, validación de configs, paths
├── processing/        Limpieza de texto, NITs, phonetic keys
├── ingestion/         Contratos de fuente + staging/compactación DuckDB
├── engine/
│   ├── lsh/          MinHash + LSH (DiskBased, TrustedSource, ...)
│   ├── scorer.py     Scoring de pares candidatos
│   └── clusterer.py  Componentes conexos + split mega-clusters
├── golden/           Selección de representante por cluster
├── pipeline/
│   ├── orchestrator.py    API recomendada (producción)
│   └── linkage_pipeline.py  API alterna (legacy, no usar)
├── evaluation/       Métricas + OrchestratorOptimizer (Optuna)
├── reporting/        Reportes opcionales (extra [viz])
├── deduplication/    Helpers de dedup intra-fuente
└── exporters/        Excel, Parquet, etc.
```

### Flujo del Orchestrator

```
sources (dict)  ──┐
                  ▼
                L1: limpieza (texto, NIT, phonetic keys)
                  │
                L2: bloqueo LSH (genera pares candidatos)
                  │
                L3: scoring (nombre + NIT + filtros previos)
                  │
                L4: clustering (componentes conexos)
                  │
                L5: golden record (selector con pesos de fuente)
                  │
                L6: reporting (opcional, extra [viz])
                  ▼
            {golden, correlative, stats}
```

---

## Historia del proyecto

### Cómo llegamos a 0.6.0

| Fase | Lo que se hizo | Métricas medidas |
|---|---|---|
| **0.1.0** | Refactor notebook → paquete `.py` (mayo 21–23) | — |
| **0.2.0** | Consolidación funcional (mayo 24) | — |
| **0.3.0** (Fase 1) | Auditoría dead code: 12 keys ignoradas. Bug NIT vacío. Nuevo perfil `produccion_calibrada` | F1: 0.05 → 0.84 |
| **0.3.1** (Fase 2) | `source_quality_weights` numéricos. `max_sources_per_group`. Perfiles auxiliares limpios | F1=0.83 alta_precision |
| **0.3.2** (Fase 3) | `OrchestratorOptimizer` conectando Optuna al pipeline real | F1=0.93 con 20 trials |
| **0.4.0** (Fase 4) | Fix `_class_exists` (reportes). `min_sources_for_golden`. Deprecation legacy | 380/380 tests |
| **0.5.0** (Sprint 0.5.0) | Eliminado `optuna_integration` heredado. `config_produccion_it7` limpio (11 keys removidas) | 380/380 tests |
| **0.6.0** (Sprint 0.6.0) | CI/CD con cobertura (`pytest-cov` 50% mínimo). `mypy` + pre-commit hooks. Badges. | **385/385 tests · 58% cobertura medida** |

### Documentos clave

- **[`CHANGELOG.md`](CHANGELOG.md)** — todas las versiones + tabla de equivalencia retroactiva
> Nota v0.13.0: los documentos `docs/AUDITORIA_*` de fases anteriores viven
> fuera de este repositorio (Drive del proyecto); los enlaces se retiraron
> del README porque apuntaban a rutas inexistentes aquí. La fuente de verdad
> versionada del historial es `CHANGELOG.md`.

---

## Credenciales

Nunca van en el código. Tres opciones (en orden de prioridad):

1. **Colab Secrets** (recomendado en Colab): `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`,
   `SNOWFLAKE_PASSWORD`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`,
   `SNOWFLAKE_ROLE`. Acceder con `userdata.get('SNOWFLAKE_USER')`.
2. **Variables de entorno** (local): `export SNOWFLAKE_USER=...`
3. **`config.json` local** en el directorio de ejecución, con sección
   `"snowflake"` (solo como último recurso; `chmod 600 config.json` en POSIX y
   nunca versionarlo).

El paquete las carga automáticamente con `get_snowflake_credentials()`.

---

## Calidad de código

| Aspecto | Estado | Target |
|---|---|---|
| Tests | ✅ Suite completa de release y regresiones focales; conteos reproducibles en el informe de auditoría | mantener |
| **Cobertura** | ✅ 63,96% combinada (líneas 67,42%; ramas 53,23%); gate ≥60% | ≥80% de ramas antes de 1.0 |
| **CI/CD** | ✅ GitHub Actions (lint + test + typecheck + build) | mantener |
| Ruff | ✅ Sin errores | mantener |
| Format | ✅ Consistente (`ruff format`) | mantener |
| **mypy** | ✅ Anillo de producción bloqueante; legado visible | strict global antes de 1.0 |
| **Pre-commit** | ✅ Configurado (ruff + mypy + higiene) | mantener |
| Gate de cobertura de líneas | ✅ Umbral 60 % en `pyproject.toml`; el badge no afirma una medición nueva | publicar medición por commit en Codecov |

Correr tests:

```bash
# Tests sin cobertura (rápido)
pytest tests/

# Tests con cobertura local
pytest tests/ --cov=record_linkage --cov-report=html
# Abre htmlcov/index.html para ver detalle

# Subset de tests por fase
pytest tests/test_fase4_*.py -v
pytest tests/ -k "not slow"

# Lint y format
ruff check src/ tests/ scripts/
ruff format --check src/ tests/ scripts/

# Type check
mypy --config-file=pyproject.toml src/record_linkage/

# Pre-commit (instalar una vez por clon)
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

### Workflows de CI activos

| Workflow | Trigger | Qué hace |
|---|---|---|
| `.github/workflows/ci.yml` | Push y PR a main/master/develop | Lint/format, anillo mypy bloqueante, tests con cobertura ≥60%, auditoría de seguridad y smoke test del wheel |
| `.github/workflows/publish.yml` | Manual | Gatea y publica un candidato exclusivamente en TestPyPI mediante OIDC |
| `.github/workflows/release.yml` | Tag `vX.Y.Z` | Suite completa sin bypass, build único, publicación única en PyPI y GitHub Release |
| `.github/workflows/deps-bump.yml` | Mensual/manual | Resuelve constraints transitivos con hashes y abre PR solo tras la suite completa |

---

## Roadmap hacia 1.0

Resumen de la política de versionamiento (detalle en `CHANGELOG.md`):

```
0.13.x  Pilotos controlados y salvaguardas por identificador
0.14.x  Flujo configurable, contratos de fuente y auditoría de precisión
0.15.x  Staging DuckDB, clustering incremental y correcciones de auditoría
0.16.x  Cobertura de ramas ≥80%, mypy strict y benchmark en Colab fresco
1.0.0   API congelada y calidad estadística certificada por dominio
```

---

## Notebooks de ejemplo

| Notebook | Propósito |
|---|---|
| [`notebooks/01_deduplicar_una_base.ipynb`](notebooks/01_deduplicar_una_base.ipynb) | Deduplicación de una fuente |
| [`notebooks/02_cruzar_dos_bases.ipynb`](notebooks/02_cruzar_dos_bases.ipynb) | Linkage de dos fuentes |
| [`notebooks/03_produccion_multifuente.ipynb`](notebooks/03_produccion_multifuente.ipynb) | Corrida local multifuente con `SourceSpec` y checkpoints |
| [`notebooks/04_multicampo_y_evaluacion.ipynb`](notebooks/04_multicampo_y_evaluacion.ipynb) | Esquema multicampo y evaluación etiquetada con presupuesto |
| [`notebooks/05_general_cruce_configurable.ipynb`](notebooks/05_general_cruce_configurable.ipynb) | **General (v0.17.3)**: cualquier par de bases por contrato, con preflight, smoke test, invariantes y metadatos |
| [`notebooks/06_orquestador_configurable.ipynb`](notebooks/06_orquestador_configurable.ipynb) | **Producción disk-first (v0.17.3)**: lista declarativa de N fuentes, DuckDB con presupuesto explícito, payload separado, salida Parquet lazy y QA N-fuente sin materialización masiva |
| [`notebooks/07_deduplicar_importadores_razon_social_pais.ipynb`](notebooks/07_deduplicar_importadores_razon_social_pais.ipynb) | **Sin identificador (v0.22.0, revisado en 0.22.4)**: empalme por razón social y país; perfiles de columnas por fuente (`snowflake_v2`, `destinatarios_dian`); catálogo de países y listas de limpieza editables; cobertura del catálogo comprobada **antes** del smoke test (`detener`/`aislar`); diez invariantes (once desde F2.12); cronómetro por fase y exportación con metadata; scoring por lotes (0.22.4): la base real de 355.681 filas termina en 17 min con 4,8 GiB de pico |
| [`notebooks/08_PUBLICAR_GITHUB.ipynb`](notebooks/08_PUBLICAR_GITHUB.ipynb) | **Publicación (v0.22.4)**: empaqueta, verifica compuertas de calidad y publica el paquete a GitHub con tag y release |

---

## Licencia

Apache-2.0. Ver [`LICENSE`](LICENSE).

---

## Reportar problemas

- 🐛 Bug → abrir issue en GitHub con: versión, comando reproducible, traceback
- 🆕 Feature request → discutir antes de PR (estamos en 0.x, la API cambia)
- ❓ Pregunta de uso → revisar README y CHANGELOG primero, luego abrir discussion

---

> **Construido con disciplina forense:** cada fase queda registrada en `CHANGELOG.md`
> con metodología, métricas y limitaciones documentadas. La transparencia sobre
> lo que NO funciona es tan importante como mostrar lo que sí.
