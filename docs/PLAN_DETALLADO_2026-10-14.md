# Plan detallado paso a paso · rues-linker v2 → producción (2026-10-14)

*Es el plan vigente. Sustituye en lo operativo a `PLAN_EJECUCION.md` (5-oct) y desarrolla las fases 3 a 6 de
`DIAGNOSTICO_Y_RUTA_2026-10-13.md`. Está escrito para que lo ejecute un modelo de menor tamaño o una persona que no
conozca el proyecto: cada tarea trae objetivo, prerrequisitos, pasos con comandos exactos, archivos que toca, prueba que
se escribe primero, criterio de aceptación y cómo se declara. Si un paso falla dos veces, no se improvisa: se escribe
en `docs/BITACORA.md` qué pasó y se pide decisión.*

**Quién hace qué.** Las tareas numeradas (3.1, 3.2, …) las hace el modelo. Las tareas **E1…E6** las hace Enrique y
tienen fecha relativa; sin E2 (verdad real) ninguna cifra de calidad vale para empresas reales.

---

## 0. Reglas y chuleta

### 0.1 Reglas que no se negocian

1. **Prueba primero.** Se escribe la prueba que falla, luego el cambio, luego la prueba pasa. Lo que no tiene prueba
   no entra.
2. **Nada se repara en silencio.** Si un cambio mueve la huella del banco, se declara (sección 0.4). Si falta un insumo,
   la corrida falla con mensaje accionable («qué pasó · por qué importa · qué hacer»).
3. **Una regla se escribe una vez.** Si dos módulos la necesitan, los dos la importan del mismo sitio.
4. **El banco y la escala corren antes y después de todo cambio que toque L1…L5.** Sin excepción.
5. **Ningún dato licenciado ni nombre de empresa real en el repositorio.** En pruebas y documentos, empresas inventadas
   (KRONOS, ZELVORA, TRIVENTA, PELUVIKA, DULCEVENT, VALDORIA). `tests/test_sin_nombres_reales.py` vigila los archivos
   listados en `ARCHIVOS_VIGILADOS`; todo archivo nuevo de documentación o evidencia se agrega a esa lista.
6. **Español en el código nuevo** (nombres, docstrings, mensajes), `@dataclass` para configuración, `pathlib`, sin
   `iterrows`/`apply` fila a fila, sin `print` en la librería, excepciones específicas.
7. **Nunca a `main` directo.** Rama `claude/<tema>`, commits pequeños con un tema, PR, CI verde, merge.
8. **Sin versión ni tag.** Los asigna Enrique (tarea E5).
9. **«Unir mal duele más que dejar sin unir».** Ante dos configuraciones con macro-F1 parecido, gana la de menos
   uniones que tocan un negativo. Así se decidió ADR-0015 (0,90 en vez de 0,85).

### 0.2 Definición de «terminado» para cualquier tarea

Una tarea está terminada solo cuando TODO esto es cierto:

- [ ] La prueba nueva existe, falló antes del cambio y pasa después.
- [ ] `ruff check src tests scripts` y `ruff format --check src tests scripts` sin errores.
- [ ] `python scripts/reglas_estrictas.py --base origin/main` → `RESULTADO: PASA`.
- [ ] `python scripts/deuda.py --referencia docs/evidencia/deuda_f0.json` → `DEUDA: PASA` (si bajó, fijar el techo nuevo
      con `--escribir`).
- [ ] mypy del anillo bloqueante (comando en 0.3) → `Success`.
- [ ] Suite rápida verde: `pytest tests/ -q -n 4 --dist loadfile -m "not canario and not slow"`.
- [ ] Si tocó L1…L5: banco antes y después con `--comparar`, y `tests/test_compuertas.py` verde.
- [ ] Si movió la huella: ADR + `tests/lineas_base.py` + `compuertas.json` + evidencia + CHANGELOG + BITACORA (0.4).
- [ ] PR abierto con el cuerpo «Qué trae / Verificación / Lo que NO está»; CI verde; merge.

### 0.3 Chuleta de comandos

```bash
# Entorno (una vez)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" -c constraints/runtime-validado.txt      # 3.11/3.12; en 3.10 use -py310.txt

# Calidad de código (cada tarea)
ruff check src tests scripts && ruff format --check src tests scripts
python scripts/reglas_estrictas.py --base origin/main
python scripts/deuda.py --referencia docs/evidencia/deuda_f0.json
mypy --config-file=pyproject.toml src/record_linkage/api.py src/record_linkage/config src/record_linkage/exporters \
  src/record_linkage/flujo/vinculacion src/record_linkage/flujo/vinculacion_excel src/record_linkage/ingestion \
  src/record_linkage/matching/motor_bloqueo.py src/record_linkage/matching/normalizadores.py \
  src/record_linkage/matching/pipeline_integration.py src/record_linkage/pipeline/fingerprints.py \
  src/record_linkage/pipeline/orchestrator.py src/record_linkage/pipeline/state_manager.py src/record_linkage/utils

# Pruebas
pytest tests/ -q -n 4 --dist loadfile -m "not canario and not slow"      # ≈ 8 min
pytest tests/ -q -n 2 --dist loadfile -m "slow and not canario"          # ≈ 5 min, lo que corre el Nightly
pytest tests/test_compuertas.py tests/test_banco_linea_base.py -q       # pisos y línea base

# Banco (calidad) — SIEMPRE con el conjunto institucional
BANCO="--datos data/benchmark/benchmark_institucional.csv.gz"
python scripts/banco.py $BANCO --etiqueta antes --dir-trabajo /tmp/banco_antes
# ... cambio ...
python scripts/banco.py $BANCO --etiqueta despues --dir-trabajo /tmp/banco_despues
python scripts/banco.py --comparar antes despues                          # PASA / FALLA
python scripts/banco.py $BANCO --etiqueta exp --ajuste clave=valor otra.clave=valor   # una perilla sin tocar código

# Escala (tiempo, memoria, candidatos)
mkdir -p /tmp/escala && python scripts/escala.py --etiqueta exp --tamanos 139k --dir-trabajo /tmp/escala --conservar-trabajo

# Entender un resultado (leen el --dir-trabajo del banco)
python scripts/recall_bloqueo.py --trabajo /tmp/banco_despues --salida /tmp/recall.json
python scripts/anatomia_fp.py --trabajo /tmp/banco_despues --hibrido
python scripts/barrido_comparador_nombre.py --trabajo /tmp/banco_despues

# Conformidad por caso (43 casos)
python scripts/conformidad.py --camino linkage --nota "camino de producción api.linkage(); sin corroboración (F3 no existe en este camino)"

# Verdad real (lo corre Enrique)
python scripts/medir_verdad_real.py --etiquetas etiquetas.csv --correlativa correlativa.parquet --salida medicion.json

# Git
git checkout -b claude/<tema> origin/main
git push -u origin claude/<tema>        # luego PR a main; merge con CI verde
```

### 0.4 Cómo declarar un cambio de comportamiento (cuando la huella del banco cambia)

1. Escribir `docs/adr/00NN-<tema>.md` (el próximo número libre es **0017**) con Contexto, Decisión, Lo medido (tabla
   antes/ahora: macro-F1, P, R, recall SIN_ID, uniones que tocan un negativo, tiempo, huella) y Consecuencias.
2. Correr `python scripts/banco.py $BANCO --etiqueta base_fN` **sin ajustes** (el perfil tal cual queda) → deja
   `docs/evidencia/corrida_base_fN.json`. Esa es la evidencia oficial.
3. En `tests/lineas_base.py`: constante nueva `BANCO_FN = LineaBaseBanco(...)` con los números del JSON, `BANCO_VIGENTE =
   BANCO_FN`, agregarla a `__all__` y a la historia del docstring; en `tests/test_banco_linea_base.py` agregarla a
   `LINEAS_BASE_CONOCIDAS`.
4. En `docs/evidencia/compuertas.json`: `banco_institucional.evidencia` → el JSON nuevo; subir los pisos a lo que cumple
   (nunca bajarlos sin ADR). `pytest tests/test_compuertas.py`.
5. Si cambia lo que L3 acepta: regenerar conformidad (`scripts/conformidad.py --camino linkage` y `--camino flujo.cruce`
   con sus notas) y explicar en `docs/CONFORMIDAD.md` §4 qué casos cambiaron; regenerar el fixture del contrato
   (`ACTUALIZAR_ESQUEMA_V0=1 pytest tests/test_contrato_salida_v0.py -k conteo_filas`) y revisar el diff.
6. Pruebas lentas que fijan cifras del perfil: `tests/test_cobertura_sin_identificador_f21.py` (huella con cobertura
   apagada), `tests/test_perfil_fuentes_mixtas_v018.py` (`F1_BASE`). Remedirlas y mover las constantes con comentario.
7. `CHANGELOG.md` (sección «Sin publicar»), `docs/BITACORA.md` (IT-NN), `docs/REANUDAR.md` (estado).
8. Agregar los archivos nuevos a `ARCHIVOS_VIGILADOS` en `tests/test_sin_nombres_reales.py`.

### 0.5 Qué NO hacer

- No calibrar contra `ground_truth_grande.csv` solo: produjo el sobreajuste de `fuentes_mixtas` en 0.18.0. Siempre
  el banco institucional y la escala.
- No elegir un punto por 0,001 de macro-F1: mirar primero las uniones que tocan un negativo y después la escala
  (ADR-0015 cuenta cómo 0,85 ganaba en el banco y hundía la precisión a 0,77 en 139k).
- No crear notebooks nuevos con `%%writefile` ni reimplementar el motor en un notebook.
- No subir el umbral de puntaje por encima de 0,50 mientras el puntaje sea lineal: un par sin NIT nunca pasa de 0,5.
- No borrar ni saltar una prueba para poner el CI en verde.
- No usar `pkill -f` con un patrón que esté en la propia línea de comando (mata la consola).

---

## 1. Punto de partida (lo que hay en `main` el 2026-10-14)

| Qué | Dónde | Cifra |
|---|---|---|
| Perfil de producción | `config/profiles.py::PERFILES_BASE["produccion_estandar"]` | híbrido + Soft TF-IDF 0,90 + compactada 0,95 + exención por identificador idéntico |
| Línea base del banco | `tests/lineas_base.py::BANCO_F3`, `docs/evidencia/corrida_base_f3.json` | huella `0b4ea2ec…`, macro-F1 0,9107, P 0,9844, R 0,8255, recall SIN_ID 0,6347, 73 uniones sobre negativos, 44 s |
| Escala 139k | `docs/evidencia/escala_f3_default.json` | F1 0,9722, P 0,9794, R 0,9652, 5 uniones sobre negativos, 6,77 M candidatos, 309 s, RSS 1.174 MiB |
| Pisos vigentes | `docs/evidencia/compuertas.json` | banco: macro-F1 ≥ 0,91 · P ≥ 0,98 · recall SIN_ID ≥ 0,63 · FP ≤ 73; escala: F1 ≥ 0,97 · candidatos ≤ 6.774.079 · RSS ≤ 1.250 MiB; bloqueo: recall de pares SIN_ID ≥ 0,92 |
| Conformidad | `docs/evidencia/conformidad_dedup_linkage_base.json` | 43 casos; C09, C21 y MC01 siguen en frontera |
| Deuda (techo) | `docs/evidencia/deuda_f0.json` | print 147 · os.path 115 · except sin relanzar 100 · mypy 77 · cc ≥ 20: 11 |
| Suite | CI (3.10/3.11/3.12 con pines) y Nightly 07:00 UTC | rápida ≈ 8 min con `-n 4`; lenta ≈ 5 min |
| Módulos gigantes (> 600 líneas) | `pipeline/orchestrator.py` 2.413 · `engine/lsh/disk_based.py` 2.236 · `engine/scorer.py` 2.166 · `golden/generator.py` 1.350 · `golden/containment.py` 694 · `engine/lsh/nit_blocking.py` 636 | tarea 6.2 |
| Verdad real | ninguna | `docs/PROTOCOLO_GROUND_TRUTH.md`; las tablas del Drive son sintéticas |

Lo que ya sabe el proyecto y conviene no reaprender:

- El comparador heredado daba 1,0 a la contención; el LSH tapaba el defecto. Con cualquier bloqueo de más recall
  aparecen los pares de contención: primero el comparador, después el recall (ADR-0015).
- El puntaje es lineal: `score = 0,5·nombre + 0,5·nit`, umbral 0,45. Un par sin NIT necesita nombre ≥ 0,90. Esa
  rigidez es lo que la tarea 3.3 sustituye.
- Los negativos sintéticos con el mismo nombre normalizado («MORALES BOGOTA» en dos entidades) no los separa ningún
  comparador de nombre: hacen falta otras variables (tarea 3.2) y el puntaje probabilístico (3.3).
- El generador sintético de importadores produce raíces cortas confundibles («HANSHIN» ~ «HANSUN»); la tolerancia
  entre tokens no puede bajar de 0,90 sin revisar la escala.

---

## 2. Lo que hace Enrique (y cuándo)

| Tarea | Cuándo | Qué desbloquea |
|---|---|---|
| **E1 · Borrar ramas en GitHub** (Branches → papelera): `claude/f2-motor`, `claude/f2/t8`, `claude/f2/t10`, `claude/f2/t11`, `claude/f2/t12`, `claude/f2-cierre`, `claude/f3-vinculacion`, `claude/f3-arreglos`, `claude/fase0-vigilancia`, `claude/plan-detallado` (tras su merge). Decidir `feature/vinculacion-0.23.0`. | ahora | orden |
| **E2 · Verdad real** (pasos abajo) | antes de 3.4 (ideal: 2 semanas) | 3.4, 3.5, cualquier afirmación sobre empresas reales |
| **E3 · Costo relativo** de unir mal frente a dejar sin unir (un número: «una unión falsa cuesta lo que N uniones perdidas») | con E2 | 3.4 |
| **E4 · Insumos del piloto de vinculación** (`decisiones_revision_2026-09-29.csv`, `referencia_revision_2026-09-24.csv`, el Excel del 5-oct) o declarar que no existen | antes de Fase 5 | 5.x |
| **E5 · Versión y tag** | al cerrar Fase 6 | release 1.0 |
| **E6 · Decidir el perfil `fuentes_mixtas`** con la tabla de 3.1 (¿0 uniones sobre negativos vale 0,0017 de F1?) | tras 3.1 | 3.1 |

### E2 · Verdad real, paso a paso

1. Elegir **una base real** con identificador parcial (por ejemplo CRM + RUES) y correrla con el perfil de producción:
   `resultado = linkage({"CRM": df_crm, "RUES": df_rues}, work_dir="/ruta/trabajo", skip_reporting=True)`. Guardar
   `resultado.correlativa` como Parquet. Esto NO entra al repositorio.
2. Extraer la **muestra de pares a etiquetar**, estratificada por régimen (ambos con NIT, uno sin NIT, ninguno con
   NIT) y por banda de puntaje (`SCORE_PAR` < 0,45 pero candidato; 0,45–0,60; 0,60–0,80; ≥ 0,80), 250 pares por celda
   hasta ≥ 3.000. El puntaje de los candidatos rechazados está en `L3_scoring/scored.db` solo si se corre el banco con
   `--ajuste score_threshold=0.0`; para una base real, repetir la corrida con ese ajuste en otro `work_dir`.
3. Formato del archivo de etiquetas: CSV con columnas `ID_1, ID_2, IS_MATCH, ANOTADOR` (`IS_MATCH` ∈ {1, 0, ?}; los
   textos SAME/DIFFERENT, MISMA/DISTINTA, SI/NO también se aceptan). Dos anotadores a ciegas, regla de decisión escrita
   antes de empezar (`docs/PROTOCOLO_GROUND_TRUTH.md`).
4. Medir: `python scripts/medir_verdad_real.py --etiquetas a.csv --etiquetas-b b.csv --correlativa correlativa.parquet
   --salida medicion.json`. Exige kappa ≥ 0,8; si no, se revisa la regla y se vuelve a anotar las discrepancias.
5. Entregar `medicion.json` (solo cifras: entra al repositorio como `docs/evidencia/verdad_real/<fecha>.json`) y
   actualizar `compuertas.json → verdad_real.evidencia`. Los CSV y el Parquet quedan fuera del repositorio.

---

## 3. Fase 3 restante · el puntaje (≈ 3 semanas)

### 3.1 · Los demás perfiles base con el comparador nuevo (1 día)

**Objetivo.** Que `fuentes_mixtas`, `produccion_calibrada` y `alta_precision` dejen de llevar el comparador legado, o
quede escrito por qué lo conservan. La medición ya existe (`docs/evidencia/fase3_comparador_nombre.md`, sección «Los demás
perfiles base»): con soft + híbrido + exención, `fuentes_mixtas` pasa de 111 a 0 uniones sobre negativos y pierde 0,0017
de F1; `produccion_calibrada` y `alta_precision` ganan +0,004 de F1 a la mitad del tiempo.

**Prerrequisito.** E6 (decisión de Enrique sobre `fuentes_mixtas`). Las otras dos no la necesitan.

**Pasos.**
1. Prueba primero: en `tests/test_comparador_nombre.py::test_la_perilla_y_su_umbral_existen_en_todos_los_perfiles_base`
   agregar la lista de perfiles que deben llevar `comparador_nombre == "soft_tfidf"` y `motor_candidatos == "hibrido"`
   (los que se cambien). Debe fallar.
2. En `src/record_linkage/config/profiles.py`, dentro del literal de cada perfil elegido, poner las cinco claves igual que
   en `produccion_estandar` (`similitud_compacta_min: 0.95`, `comparador_nombre: "soft_tfidf"`,
   `soft_tfidf_umbral_token: 0.90`, `nit_identical_overrides_name_filter: True`, `motor_candidatos: "hibrido"`). Para
   `fuentes_mixtas` poner además `idf_weight_blend_sin_identificador: 0.0` (la mezcla IDF era un remiendo del
   comparador legado) y `similitud_compacta_min: 0.95` (tenía 0,96).
3. Remedir: `python scripts/banco.py --etiqueta mixtas_f3 --perfil fuentes_mixtas` (sin `--datos`: ese perfil se mide
   sobre `ground_truth_grande.csv`) y lo mismo para los otros dos. Comparar con las corridas `*_soft` ya guardadas: la
   huella debe coincidir.
4. Mover las cotas: `tests/test_perfil_fuentes_mixtas_v018.py` (`PISO_F1` 0,975 → 0,978; `PISO_PRECISION` 0,980 →
   0,970; `PISO_RECALL_SIN_NIT` 0,900 → 0,930; y agregar `FP_NEGATIVOS_MAX = 0`), README (tabla de perfiles).
5. ADR-0017 «Los perfiles base comparten el comparador de ADR-0015», CHANGELOG, BITACORA.

**Aceptación.** Cada perfil cambiado reproduce la huella de su corrida `*_soft`; ninguna prueba lenta en rojo; `README`
y el ADR cuentan la pérdida de F1 de `fuentes_mixtas` sin esconderla.

### 3.2 · Otras variables en el puntaje de producción (2 días)

**Objetivo.** Que el perfil de producción use ciudad, teléfono y correo cuando la base los trae, y medir cuánto ayudan.
El banco institucional ya trae `DEPARTAMENTO`, `MUNICIPIO`, `TELEFONO`, `DIRECCION`, `EMAIL`; el 100 % de las uniones
falsas que quedan son negativos con el mismo nombre, y ahí solo otra variable puede separar.

**Pasos.**
1. Medir, sin tocar código, con `--ajuste` y `extra_features` en `scripts/banco.py` (`--variables-extra`, formato
   `COLUMNA[:TIPO[:PESO]]`; tipos base en `engine/scorer.py::_feature_similarity_vectorized` (`categorical`,
   `categorical_signed`, `exact_or_zero`, `exact_signed`, `token_set_ratio_signed`) y registrados en
   `matching/comparadores_extra.py` (`telefono_signed`, `email_signed`).
   Corridas mínimas (una perilla por corrida, etiquetas `v_ciudad`, `v_tel`, `v_email`, `v_todas`):
   ```bash
   python scripts/banco.py $BANCO --etiqueta v_ciudad --variables-extra MUNICIPIO:categorical_signed:0.10
   python scripts/banco.py $BANCO --etiqueta v_tel    --variables-extra TELEFONO:telefono_signed:0.10
   python scripts/banco.py $BANCO --etiqueta v_email  --variables-extra EMAIL:email_signed:0.10
   python scripts/banco.py $BANCO --etiqueta v_todas  --variables-extra MUNICIPIO:categorical_signed:0.10 TELEFONO:telefono_signed:0.10 EMAIL:email_signed:0.10
   ```
2. Leer con `scripts/anatomia_fp.py` qué uniones falsas desaparecen y qué verdaderas se pierden (los tipos `_signed`
   restan cuando la variable difiere: pueden romper pares verdaderos con teléfonos desactualizados).
3. Si una combinación domina a la línea base (macro-F1 ≥ 0,9107, uniones sobre negativos < 73, recall SIN_ID ≥ 0,63),
   declararla como `extra_features` por defecto del perfil **solo cuando la columna existe** (ya es así: el scorer omite
   con un aviso las columnas ausentes). Si ninguna domina, dejarlo documentado en la evidencia y no cambiar el perfil.
4. Prueba: `tests/test_scorer_extra_features.py` ya cubre los tipos; agregar una prueba de perfil que afirme la lista de
   `extra_features` por defecto y su peso total ≤ 0,30.

**Aceptación.** Tabla en `docs/evidencia/fase3_variables.md` con las cuatro corridas y la decisión; ADR-0018 si cambia
el perfil.

### 3.3 · Puntaje probabilístico Fellegi–Sunter con EM (2 semanas)

**Por qué.** El puntaje lineal (`0,5·nombre + 0,5·nit`, umbral 0,45) no sabe cuánto vale cada evidencia: un NIT igual
pesa lo mismo que un nombre igual, y un par sin NIT nunca pasa de 0,5. Fellegi y Sunter (1969) definen el peso de un
par como la suma de log-verosimilitudes de cada comparación, y el algoritmo EM estima esos pesos sin verdad etiquetada.
Referencias: Fellegi & Sunter, «A theory for record linkage», JASA 1969, https://doi.org/10.1080/01621459.1969.10501049 ·
Winkler, «Using the EM algorithm for weight computation in the Fellegi-Sunter model of record linkage», 2000,
https://www.census.gov/content/dam/Census/library/working-papers/2000/adrm/rr2000-05.pdf · implementación de referencia
abierta (misma teoría, útil para comparar fórmulas): https://moj-analytical-services.github.io/splink/topic_guides/theory/fellegi_sunter.html

**Diseño (fijo; no se improvisa).**

1. **Vector de comparación por par** `γ = (γ_nombre, γ_id, γ_1 … γ_k)`:
   - `γ_nombre` ∈ {3, 2, 1, 0} según la similitud Soft TF-IDF que L3 ya calcula: ≥ 0,95 → 3; [0,85, 0,95) → 2;
     [0,70, 0,85) → 1; < 0,70 → 0.
   - `γ_id` ∈ {2, 1, 0}: identificadores válidos iguales (en base canónica) → 2; ambos válidos y distintos → 0; alguno
     ausente o inválido → 1.
   - cada variable extra `γ_j` ∈ {2, 1, 0}: igual → 2; ausente en algún lado → 1; distinta → 0.
2. **Parámetros**: para cada comparación `k` y nivel `v`, `m_k(v) = P(γ_k = v | mismo ente)` y `u_k(v) = P(γ_k = v |
   entes distintos)`; `p = P(mismo ente)` entre los candidatos. Supuesto de independencia condicional.
3. **EM** sobre una muestra de hasta 2 M de candidatos (los primeros por orden de la tabla `candidate_pairs`, que es
   aleatoria respecto del contenido):
   - E: `g_i = p·Π_k m_k(γ_ik) / (p·Π_k m_k(γ_ik) + (1−p)·Π_k u_k(γ_ik))`.
   - M: `m_k(v) = Σ_i g_i·1[γ_ik = v] / Σ_i g_i`; `u_k(v) = Σ_i (1−g_i)·1[γ_ik = v] / Σ_i (1−g_i)`; `p = media(g_i)`.
   - Inicio: `p = 0,05`; `m_nombre = (0,70, 0,20, 0,07, 0,03)` para niveles (3, 2, 1, 0); `u_nombre` = frecuencias
     observadas de cada nivel en la muestra; `m_id = (0,90, 0,09, 0,01)` para (2, 1, 0); `u_id = (0,001, 0,60, 0,399)`
     reajustado a las frecuencias observadas; variables extra `m = (0,8, 0,15, 0,05)`, `u` observado.
   - Parar cuando el cambio máximo de cualquier parámetro sea < 1e-6 o a las 100 iteraciones. Suavizar cada
     probabilidad con `max(valor, 1e-6)` para que ningún logaritmo sea infinito.
4. **Peso y puntaje**: `w_i = Σ_k log2(m_k(γ_ik) / u_k(γ_ik))`; el `score` que L3 escribe pasa a ser la posterior
   `g_i` (en [0, 1]), para que L4, L5, el contrato de salida y `SCORE_PAR` conserven su escala. Umbral por defecto 0,50
   sobre la posterior; `score_threshold` del perfil sigue mandando.
5. **Dónde vive**: módulo nuevo `src/record_linkage/engine/puntaje_probabilistico.py` con `@dataclass(frozen=True)
   ConfigFellegiSunter(niveles_nombre=(0.95, 0.85, 0.70), muestra_max=2_000_000, iteraciones_max=100, tolerancia=1e-6,
   p_inicial=0.05)`, `vector_de_comparacion(name_sim, nit_distances, extras) -> np.ndarray[int8]` (vectorizado),
   `estimar_em(vectores, config) -> ParametrosFS` (arreglos `m`, `u`, `p`, `iteraciones`, `convergio`) y
   `posterior(vectores, parametros) -> np.ndarray`. Perilla del perfil `puntaje: "lineal" | "fellegi_sunter"`
   (`setdefault("puntaje", "lineal")` en el bucle de `PERFILES_BASE`; valor desconocido → `ValueError` accionable).
6. **Integración en L3** (`engine/scorer.py`): el streaming hace dos pasadas cuando `puntaje == "fellegi_sunter"`: la
   primera recorre hasta `muestra_max` candidatos, calcula `γ` y estima los parámetros una sola vez por corrida (se
   guardan en el manifiesto de L3 como `parametros_fs` con `m`, `u`, `p`, `iteraciones`); la segunda puntúa todos los
   candidatos con la posterior. `name_sim`, `nit_sim` y `nit_dist` se siguen escribiendo tal cual.
7. **Pruebas primero** (`tests/test_puntaje_probabilistico.py`):
   - Generador sintético con `m`, `u`, `p` conocidos (50.000 vectores) → `estimar_em` recupera cada parámetro con error
     ≤ 0,03 y `p` con error ≤ 0,01.
   - La posterior es monótona en el nivel de nombre a igualdad del resto.
   - `puntaje = "lineal"` reproduce la huella del banco (`BANCO_VIGENTE`): paridad exacta.
   - Un perfil con `puntaje = "otro"` falla al construir el scorer con el mensaje de los tres tramos.
   - El manifiesto de L3 trae `parametros_fs` cuando se usa.
8. **Medir** (banco y escala, con la chuleta de 0.3): `--ajuste puntaje=fellegi_sunter` y, si gana, `score_threshold`
   en 0,50 (posterior). Mirar `scripts/anatomia_fp.py`: deben desaparecer uniones falsas de nombre igual sin NIT (la `u`
   del nombre las castiga) y aparecer pares sin NIT con nombre entre 0,85 y 0,95 que hoy mueren en 0,90.

**Aceptación (compuerta de 3.3).** Banco: macro-F1 ≥ 0,9107, P ≥ 0,98, uniones sobre negativos ≤ 73, recall SIN_ID ≥
0,6347 (ninguna cifra peor que el perfil actual). Escala 139k: F1 ≥ 0,97, RSS ≤ 1.250 MiB, tiempo ≤ 1,2× el actual.
Conformidad sin casos nuevos en FALLA. Si gana en todo, `puntaje = "fellegi_sunter"` pasa a ser el valor del perfil con
ADR-0019 (sección 0.4 completa). Si no gana, queda disponible tras la perilla y se escribe por qué.

### 3.4 · Umbrales por costo con verdad real (2 días; requiere E2 y E3)

1. Con `medicion.json` de E2 y el costo de E3 (`c = costo de una unión falsa / costo de una unión perdida`), calcular para
   cada umbral `t` en 0,30…0,95 (paso 0,01) la utilidad esperada `U(t) = −c·FP(t) − FN(t)` sobre los pares etiquetados,
   por régimen. Script nuevo `scripts/umbral_por_costo.py --medicion medicion.json --costo c` que imprime la tabla y el
   `t*` por régimen, con intervalo de Wilson de precisión y recall en `t*`.
2. Si `t*` difiere del umbral del perfil en más de 0,05, cambiarlo por perfil (`score_threshold`) con ADR y volver a
   correr banco y escala: las cotas sintéticas no pueden empeorar más de lo que la verdad real justifica, y eso se escribe.
3. Registrar en `compuertas.json → verdad_real.piso` la precisión y el recall medidos por régimen.

### 3.5 · Calibrar el comparador con verdad real (1 día; requiere E2)

Correr `scripts/barrido_comparador_nombre.py --trabajo <dir de la corrida real con score_threshold=0.0>` y leer la
tabla de pares verdaderos que soft pierde y falsos que acepta con las etiquetas reales (no con `ID_GROUP`: adaptar el
script para aceptar `--etiquetas etiquetas.csv`, un cambio de 30 líneas con prueba). Decidir `soft_tfidf_umbral_token`
(0,85 / 0,90 / 0,95) y `similitud_compacta_min` (0,93 / 0,95 / 0,97) con la misma regla de 0.5: menos uniones falsas
primero. Declarar.

### 3.6 · L3 sin bucle por par (1 día)

`engine/scorer.py` conserva dos bucles de Python por par: el bono por primer token compartido (`first_tokens_0/1`, solo
en el comparador legado) y la penalización por nombre genérico (`for k in range(n_pairs)` bajo `generic_name_penalty`).
Vectorizarlos (`np.char.partition` / máscaras) con paridad exacta por huella (`BANCO_VIGENTE` y, para el legado,
`--ajuste comparador_nombre=legado` contra `corrida_lsh_base`-equivalente). Prueba: el oráculo
`tests/data/oraculo_scorer_p1_1.pkl` sigue bit a bit.

---

## 4. Fase 4 · escala y Colab (≈ 3 semanas)

### 4.1 · Escala 463k con el perfil actual (1 día)

```bash
mkdir -p /tmp/escala && python scripts/escala.py --etiqueta f3_463k --tamanos 463k --dir-trabajo /tmp/escala --conservar-trabajo --verboso
```
Esperado por extrapolación (no medido): 20–25 M de candidatos, L2 ≈ 15–20 min, L3 ≈ 8 min, RSS 2–3 GB. **Aceptación:**
la corrida termina; F1 ≥ 0,96; registrar `docs/evidencia/escala_f3_463k.json` y crear en `compuertas.json` la sección
`escala_463k` con el piso medido. Si el RSS supera 4 GB o el tiempo 45 min, abrir la tarea 4.4 antes que cualquier otra.

### 4.2 · Presupuesto de memoria fail-fast (3 días)

**Objetivo.** Que una corrida que no cabe en la RAM disponible falle en L1 con un mensaje accionable, no en L2 por OOM
a los 20 minutos (lo que pasó con la matriz k×k).

1. Prueba primero (`tests/test_presupuesto_memoria.py`): con `ajustes_perfil={"presupuesto_memoria_gb": 0.1}` y 30 filas,
   `linkage()` lanza `MemoryError` cuyo mensaje contiene «qué pasó», «por qué importa» y «qué hacer»; con el presupuesto
   por defecto (`None` = la RAM disponible de `MemoryManager.get_memory_status()["available_gb"]`) la misma corrida pasa.
2. Módulo `src/record_linkage/pipeline/presupuesto.py`: `estimar_memoria_gb(n_registros, perfil) -> EstimacionMemoria`
   con tres sumandos medidos a 139k y escalados linealmente: L1 (≈ 3,4 KB por registro), L2 híbrido (matriz TF-IDF ≈ 25
   trigramas × 12 bytes por registro + producto por lote 500 × n × 8 bytes), L3 (lote de `scoring_batch_size` × 7
   columnas × 8 bytes + IDF). Las constantes se leen de `docs/evidencia/escala_f3_default.json` y se escriben en el
   módulo con su origen.
3. El orquestador llama a la estimación al final de L1 y compara con el presupuesto; si no cabe, falla con los tres
   tramos y sugiere `tfidf_max_filas`, `scoring_batch_size` o partir la base.
4. Declarar en README («Memoria: cuánto necesita y qué hacer si no cabe»).

### 4.3 · Reanudación probada (2 días)

`pipeline/state_manager.py` ya guarda por fase un hash de configuración + datos y marca `done`; falta la prueba de que
sirve. 1) Prueba `tests/test_reanudacion.py`: correr `linkage()` sobre 50 filas; borrar `L3_scoring/`, `L4_clustering/` y
`L5_golden/` del `work_dir`; volver a correr con el mismo `work_dir`: L1 y L2 se reutilizan (el manifiesto los marca
reutilizados), la huella de la correlativa es idéntica. 2) Segunda prueba: cambiar una perilla de L3 (`score_threshold`)
y comprobar que L3 se rehace y L2 no. 3) Documentar en README la receta de Colab: `work_dir` en Drive, misma llamada,
sin flags. Si alguna de las dos pruebas no pasa, arreglar `state_manager` sin cambiar su contrato.

### 4.4 · 1 M y 4 M sintéticos (1 semana)

**Hecho duro:** TF-IDF todos contra todos es cuadrático. 139k tardó 116 s en esa vía; 1 M son 52× más pares (≈ 1,7 h) y
4 M, 830× (≈ 27 h). `tfidf_max_filas = 1.500.000` apaga la vía por encima de ese tamaño, y entonces el recall sin
identificador vuelve al de los tokens distintivos (0,80 en el banco). No es aceptable para «millones de registros».

1. Generar los conjuntos con `scripts/generar_ground_truth_grande.py` (semilla 42): 1 M ≈ `--empresas-extra 345000
   --importadores-extra 69000`; 4 M ≈ `--empresas-extra 1380000 --importadores-extra 276000` (la razón 5:1 de
   `escala.py`; verificar el conteo y anotarlo). Agregar los tamaños `1M` y `4M` a `scripts/escala.py::TAMANOS` con sus
   parámetros.
2. Implementar en `engine/candidatos/nombre.py` la vía `pares_por_nombre_tfidf_bloqueado`: el mismo coseno de trigramas,
   pero solo entre pares que comparten uno de sus `k = 3` tokens más raros (reutiliza el índice de
   `pares_por_token_distintivo`, con `max_bucket` 5.000). Costo ∝ Σ tamaño² de cubeta, no n². Para cada cubeta se arma la
   submatriz TF-IDF y se multiplica por lotes de 500 filas como hoy. Perilla `candidatos_hibrido.tfidf_modo: "global" |
   "por_token"`; por defecto `"global"` hasta `tfidf_max_filas` y `"por_token"` por encima (declarado).
3. Pruebas: sobre el fixture de `tests/test_candidatos_hibrido.py` la vía bloqueada propone todo par que la global
   propone y comparte un token raro; recall de pares SIN_ID en el banco ≥ 0,90 (hoy 0,925 con la global).
4. Medir 1 M en la máquina de 4 vCPU (tiempo, RSS, candidatos, F1) y registrar `escala_f3_1M.json`; 4 M solo si 1 M cabe
   en ≤ 4 GB y ≤ 1 h; si no, parar y escribir el cuello de botella con cifras.

**Aceptación.** `compuertas.json → colab_4M` con evidencia: ≤ 7.200 s, RSS ≤ 8 GB, reanudable. Si 4 M no se alcanza,
el piso queda en el tamaño que sí se alcanzó y se dice.

### 4.5 · Notebook Colab delgado (2 días)

No crear notebooks nuevos. Dejar `notebooks/03_produccion_multifuente.ipynb` como el notebook de Colab: celda 1
`pip install "rues-linker @ git+https://github.com/EnriqueForero/rues-linker_v2@main"`; celda 2 montar Drive y fijar
`work_dir` en Drive; celda 3 `linkage(...)` con `motor_ingesta="duckdb"`, `modo_resultado="disco"`,
`exportar_excel=False`, `ajustes_perfil={"liberar_fuentes_tras_l1": True}`; celda 4 lectura del manifiesto (tiempos,
RSS, candidatos); celda 5 cómo reanudar (misma llamada). Sin `%%writefile`, sin lógica del motor. Prueba: `tests/
test_notebooks_contrato.py` (lenta) lo ejecuta con `nbclient` sobre 100 filas sintéticas. El notebook se corre de verdad
en Colab gratuito una vez con el conjunto de 139k y se anota en BITACORA tiempo y RAM observados.

---

## 5. Fase 5 · vinculación de empresas dentro de la librería (F3.2–F3.7; ≈ 3 semanas; requiere E4)

El motor de vinculación (`flujo/vinculacion/`, 26 módulos) ya está en `main` con paridad y los arreglos de F3.1b.
Falta que use la librería en vez de duplicarla y que entregue el contrato estándar. Orden obligatorio:

| # | Tarea | Pasos | Aceptación |
|---|---|---|---|
| 5.1 | **Catálogos a un solo sitio.** Las listas de genéricos, formas legales y conectores de `flujo/vinculacion/constantes.py` y `normalizar.py` pasan a `matching/genericos.py` y `matching/locales.py`; `vinculacion` las importa. | Prueba primero: «`flujo.vinculacion` no define catálogos» (`grep` en la prueba sobre el código fuente). Mover; `scripts/oraculo_vinculacion/comparar.py` sobre los 4 escenarios → IDÉNTICO. | paridad y prueba verde |
| 5.2 | **Candidatos de vinculación con el generador híbrido.** `flujo/vinculacion/candidatos.py` arma sus propios pares (palabra con una letra de tolerancia, Jaro-Winkler ≥ 0,85, siglas). Sustituir las vías 1 y 2 por `engine/candidatos` (tokens distintivos + TF-IDF) conservando la vía 3 (siglas) y el `Comparador`. | Prueba primero sobre los 4 escenarios: todo par que el motor original clasificaba sigue siendo candidato (recall de candidatos = 1,0 contra la salida original). Medir tiempo en el escenario grande. | ningún vínculo cambia en los 4 escenarios; tiempo ≤ actual |
| 5.3 | **`vincular_empresas(cfg) -> ResultadoLinkage`.** Contrato estándar (`correlativa`, `golden`, `enlaces`, `revision`, manifiesto) sobre `ejecutar`; el Excel de 16 hojas pasa a ser una vista (`exportar_excel`) del resultado. | Prueba primero: `tests/test_contrato_salida_v0.py`-equivalente para vinculación (esquema fijo en `tests/contratos/esquema_vinculacion_v0.json`). | esquema verde en los 4 escenarios; el Excel sigue IDÉNTICO |
| 5.4 | **Lector único de decisiones** (`flujo/decisiones.py`): el Excel de salida sirve como archivo de decisiones de la siguiente corrida (ida y vuelta). | Prueba de ida y vuelta: exportar → leer → ejecutar → mismas decisiones. | verde |
| 5.5 | **Notebooks 10 y 09 delgados**: sin `%%writefile`, llaman `vincular_empresas`. No crear el notebook 11. | `nbclient` sobre los escenarios inventados; prueba «ningún notebook reimplementa el motor» (`grep` de `%%writefile` y de `def clasificar`). | verde |
| 5.6 | **Medición real del piloto** (requiere E4): correr el piloto con `vincular_empresas` y comparar con la referencia adjudicada; precisión y recall con `scripts/medir_verdad_real.py`. | — | cifras en `docs/evidencia/verdad_real/` |
| 5.7 | **ADR, `docs/VINCULACION.md`, skill `record-linkage-empresas` actualizada, CHANGELOG.** Sin versión ni tag. | — | documentos cruzados |

Pendientes con decisión de Enrique dentro de esta fase: C9, B7, M-c, A13 de `docs/vinculacion/PLAN_ARREGLOS_F3.1b.md`
y el Excel de Caro/VPI (al final de todo, según su instrucción).

---

## 6. Fase 6 · ingeniería y release 1.0 (≈ 3 semanas)

### 6.1 · Camino único (3 días)

`deduplicate_unified` (perfil SIN_NIT, ADR-0014) y `flujo/importadores.py` son caminos aparte del de producción.
Objetivo: que los tres caminos (`linkage`, `flujo.cruce`, `importadores`) pasen por el mismo orquestador con perfiles
distintos. Pasos: 1) prueba de equivalencia: `importadores` sobre su fixture da la misma partición por el orquestador
con perfil `importadores_nombre_pais` (nuevo perfil: `comparador_nombre: soft_tfidf`, bloqueo por `PAIS`, sin
identificador); 2) retirar el código duplicado; 3) `tests/test_sin_nit_recalibrado.py` se remide con el camino único y
sus cotas se mueven con ADR (ADR-0014 lo anticipa).

### 6.2 · Partir los módulos gigantes (1 semana; movimientos puros, huella idéntica)

| Módulo | Líneas | Destino |
|---|---:|---|
| `pipeline/orchestrator.py` | 2.413 | `pipeline/fases/l1_preparacion.py`, `l2_candidatos.py`, `l3_puntaje.py`, `l4_agrupacion.py`, `l5_consolidado.py`, `l6_reportes.py`; `orchestrator.py` solo orquesta |
| `engine/lsh/disk_based.py` | 2.236 | `engine/lsh/firmas.py`, `indice.py`, `candidatos_sqlite.py` (la escritura SQLite se comparte con `engine/candidatos/hibrido.py::escribir_candidatos_sqlite`: una regla, un sitio) |
| `engine/scorer.py` | 2.166 | `engine/puntaje/comparadores.py` (nombre, NIT, fonética, extras), `filtros.py` (piso, exención, veto), `lotes.py` (streaming y no streaming), `scorer.py` solo compone |
| `golden/generator.py` | 1.350 | `golden/seleccion.py`, `golden/columnas.py`, `golden/escritura.py` |
| `golden/containment.py`, `engine/lsh/nit_blocking.py` | 694, 636 | partir solo si al tocarlos pasan de 600 |

Regla: un PR por módulo; antes y después, `BANCO_VIGENTE` con huella idéntica y `pytest` verde; `reglas_estrictas.py`
no puede subir ninguna violación (mover código no es excusa para heredar `print` o `os.path`: se corrigen al mover).

### 6.3 · Deuda a cero (2 semanas, en lotes)

Techos de hoy: `print` 147 · `os.path` 115 · `except` sin relanzar 100 · mypy 77 · funciones con complejidad ≥ 20: 11.
Lotes de 20 por PR, cada uno baja el techo con `python scripts/deuda.py --escribir docs/evidencia/deuda_f0.json`. Orden:
`print` → `logging` del módulo; `os.path` → `pathlib`; `except Exception` → excepción específica o relanzar con
contexto; mypy en el anillo bloqueante primero. Cobertura: `--cov-fail-under` sube de 60 a 75 en el CI cuando se
alcance.

### 6.4 · Release 1.0 (2 días; E5)

Lista: CHANGELOG consolidado desde 0.22.4; `pyproject` versión (Enrique); `constraints` remedidos; wheel construido por
el CI; README con la tabla final de banco, escala y verdad real; tag; `docs/REANUDAR.md` cerrado.

---

## 7. Compuertas vigentes y cómo leer un resultado

| Compuerta | Archivo | Piso (no puede empeorar) | Objetivo de la ruta |
|---|---|---|---|
| Banco institucional | `docs/evidencia/corrida_base_f3.json` | macro-F1 ≥ 0,91 · P ≥ 0,98 · recall SIN_ID ≥ 0,63 · uniones sobre negativos ≤ 73 · grupo mayor sin id ≤ 50 filas | macro-F1 0,92 · recall SIN_ID 0,80 |
| Escala 139k | `docs/evidencia/escala_f3_default.json` | F1 ≥ 0,97 · candidatos ≤ 6.774.079 · RSS ≤ 1.250 MiB | ≤ 20 candidatos por registro · RSS ≤ 1 GB · ≤ 60 s |
| Bloqueo | `docs/evidencia/fase3_comparador/recall_h_s085c093_id.json` | recall de pares SIN_ID ≥ 0,92 · CON_ID ≥ 0,99 | 0,95 / 0,99 |
| Conformidad | `conformidad_dedup_*_base.json` | ningún caso que pasa deja de pasar | C09, C21, MC01 pasan |
| Verdad real | ninguna todavía | — | ≥ 3.000 pares, kappa ≥ 0,8, P CON_ID ≥ 0,97, P SIN_ID ≥ 0,90 |
| Colab 4 M | ninguna todavía | — | ≤ 7.200 s · RSS ≤ 8 GB · reanudable |
| Ingeniería | `deuda_f0.json` | ningún conteo sube | todos en 0; cobertura 75 %; archivo ≤ 600 líneas; cc ≤ 15 |

**Cómo decidir con una medición nueva** (en este orden, sin saltar):
1. ¿Alguna unión que toca un negativo subió? → no entra, salvo ADR que explique por qué vale la pena.
2. ¿macro-F1 y recall SIN_ID iguales o mejores? → sigue.
3. ¿La escala 139k mantiene F1 ≥ 0,97 y RSS ≤ 1.250 MiB? → sigue. (Esta es la que descubrió el error de 0,85.)
4. ¿Conformidad sin casos nuevos en FALLA? → entra, con la sección 0.4 completa.

---

## 8. Riesgos y señales de alarma

| Riesgo | Cómo se nota | Qué hacer |
|---|---|---|
| Sobreajuste a lo sintético | mejora en el banco que no aparece en 139k, o al revés | ninguna decisión sin las dos; la verdad real (E2) manda |
| Componente gigante en L4 | `cannot-link` o `cobertura` reportan grupos de miles de filas; RSS sube en L5 | `scripts/anatomia_fp.py` sobre el `work_dir`; revisar tolerancia del comparador y genéricos |
| Memoria en L2 | RSS de L2 > 1,5× lo esperado | bajar `lote_filas` (500 hoy), `tfidf_max_df`, o la tarea 4.4 |
| Pines rotos | el CI cae en `pip install` | `constraints/runtime-validado.txt` se remide desde un entorno donde la suite pasó; comprobar cada pin con `pip download --python-version` |
| Nightly en rojo | job pinado en rojo | es el código (ADR-0014): bisecar con el banco, no tocar cotas |
| Nombres reales en el repositorio | `tests/test_sin_nombres_reales.py` en rojo | reemplazar por marcas inventadas; nunca ampliar la lista de excepciones |
| Proxy de la sesión | `git push --delete` o API devuelven 403 | las ramas las borra Enrique (E1) |

---

## 9. Glosario para quien llega nuevo

- **Banco:** `scripts/banco.py`; corre el pipeline sobre un conjunto con verdad conocida y deja un JSON con calidad,
  recursos y huella. **Huella:** SHA-256 de la partición; si cambia, el resultado cambió.
- **Régimen:** CON_ID (ambos registros con identificador válido), SIN_ID (ninguno), MIXTO (uno).
- **Uniones que tocan un negativo:** pares unidos donde al menos un registro fue diseñado como negativo (otra empresa).
  Es el error que más cuesta.
- **macro-F1:** media del F1 por estrato; no lo domina el estrato de grupos grandes.
- **Candidatos / bloqueo (L2):** pares que L3 puntúa; recall de pares del bloqueo = fracción de pares verdaderos que
  L2 propone. **Híbrido:** identificador + tokens distintivos + TF-IDF de trigramas. **LSH:** MinHash sobre n-gramas.
- **Soft TF-IDF:** coseno de tokens ponderados por IDF con tolerancia Jaro-Winkler entre tokens (ADR-0015).
- **Exención por identificador idéntico:** un par con el mismo NIT válido no lo veta el piso de nombre.
- **Cobertura por estrellas (L5):** parte un grupo sin identificador en estrellas cuyos miembros se parecen al líder.
- **Compuerta / piso:** cifra que una medición nueva no puede empeorar (`compuertas.json`, vigilada por prueba).
- **Declarar:** ADR + línea base + pisos + evidencia + CHANGELOG + BITACORA (sección 0.4).

---

## 10. Orden recomendado y estimación

| Semana | Tareas | Depende de |
|---|---|---|
| 1 | 3.1 (perfiles), 3.2 (variables), 3.6 (L3 sin bucles), 4.1 (463k) | E6 para `fuentes_mixtas` |
| 2–3 | 3.3 (Fellegi–Sunter con EM) | — |
| 3 | 4.2 (presupuesto), 4.3 (reanudación) | — |
| 4 | 4.4 (1 M / 4 M, TF-IDF por token), 4.5 (notebook Colab) | 4.1 |
| 4–5 | 3.4 y 3.5 (umbrales y comparador con verdad real) | **E2, E3** |
| 5–7 | Fase 5 (vinculación) | **E4** |
| 7–9 | Fase 6 (camino único, módulos, deuda, 1.0) | E5 |

Total estimado: 9 semanas de trabajo del modelo si E2 llega en la semana 4. Sin E2, las semanas 4–5 se corren y el
producto sigue calibrado solo contra datos sintéticos: eso es lo que hoy limita la calidad, no el código.
