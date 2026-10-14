# Dónde estamos y cómo retomar

*Actualizado: 2026-10-14 (Fases 0 a 3 de la ruta: vigilancia, verdad real, candidatos híbridos, comparador Soft TF-IDF). Puerta de entrada para quien retome el plan «v2 → producción» (otra sesión, otra persona u otro modelo): léala antes de tocar nada.
El plan completo está en [`PLAN_EJECUCION.md`](PLAN_EJECUCION.md); el diario, en [`BITACORA.md`](BITACORA.md) (IT-27 a IT-33); las reglas de trabajo, en [`../CLAUDE.md`](../CLAUDE.md).*

## 1. Estado en una mirada

| Fase | Qué es | Estado | Peso en el plan |
|---|---|---|---|
| F0 | Fundaciones (CI, líneas base, trinquetes) | ✅ en `main` (PR #1) | 7 % |
| F1 | Contrato de salida | ✅ en `main` (PR #2) | 18 % |
| F2 | Calidad y unificación del motor | ✅ en `main` (PR #3 el motor, PR #4 el cierre documental) | 18 % |
| **F3** | **Vinculación de empresas dentro de la librería** | F3.1 (motor extraído con paridad, PR #5) y F3.1b (arreglos declarados, PR #6) están en `main`. F3.2 a F3.7 pendientes | 18 % (≈ 48 % hecho) |
| F4 | Escala (4 M de registros en Colab) | pendiente | 18 % |
| F5/F6 | Ingeniería y release 1.0 | pendiente | 18 % |

**La ruta vigente es `DIAGNOSTICO_Y_RUTA_2026-10-13.md` (seis fases).** Su estado: Fase 0 ✅ (vigilancia, IT-32) · Fase 1 ✅ instrumentos (la verdad real la produce Enrique) · Fase 2 ✅ (híbrido por defecto, ADR-0016) · Fase 3 ≈ 50 % (comparador y exención por identificador, ADR-0015; falta el puntaje probabilístico y las otras variables) · Fase 4 pendiente (escala y Colab con el nuevo perfil) · Fases 5 y 6 pendientes. **Todo lo de IT-32 e IT-33 vive en la rama `claude/fase0-vigilancia` hasta que su PR se mezcle.** El porcentaje de abajo es del plan anterior. Avance del plan anterior ≈ 53 %. F0 + F1 + F2 = 44 %; F3 aporta ≈ 9 puntos (48 % de su 18 %). Los pesos son las semanas del plan (F0 una; F1 a F5/F6, dos y media cada una; total 13,5). Es una estimación, no una medición.

Subfases de F3: **F3.0 oráculo ✅ · F3.1 extracción con paridad ✅ · F3.1b arreglos declarados ✅ (rama `claude/f3-arreglos`)** · F3.2 reutilizar la librería · F3.3 contrato estándar (`vincular_empresas`) · F3.4 decisiones unificadas · F3.5 notebook 10 delgado · F3.6 notebook 09 · F3.7 ADR, documentación, skill (sin versión ni tag).

## 2. Qué contiene el hito F3.1 (ya en `main`) y qué agrega F3.1b

**F3.1b** (rama `claude/f3-arreglos`, IT-31): hizo todo el nivel 1 del plan de arreglos y cinco del nivel 2, cada uno con su prueba y su declaración. El detalle (qué cambia, qué tablas de los escenarios toca, qué NO se hizo y por qué) está en `docs/vinculacion/PLAN_ARREGLOS_F3.1b.md`, sección «Estado». **Lo que sigue sin medirse:** precisión y recall sobre empresas reales (los insumos del piloto se perdieron al reciclarse el contenedor). Lo de abajo describe F3.1, que sigue valiendo como evidencia de la extracción.

El motor del notebook 10 (`vinculador_empresas` 1.0.0, 2.984 líneas en una celda `%%writefile`) vive ahora en la librería, partido por responsabilidad y **con el mismo comportamiento**:

| Qué | Dónde |
|---|---|
| Paquete del motor (config, lectura, normalización, comparación, decisiones, deduplicación, candidatos, clasificación, vínculos, web, validación, calidad, controles, resumen, tablas, `ejecutar`) | `src/record_linkage/flujo/vinculacion/` (26 módulos, ninguno > 600 líneas, complejidad ciclomática ≤ 15) |
| Excel de entrega de 16 hojas | `src/record_linkage/flujo/vinculacion_excel/` (`exportar_vinculacion_excel`, alias `exportar_excel`) |
| API del paquete | `from record_linkage.flujo.vinculacion import ejecutar, ConfigVinculacion, Fuente, Resultado, leer_decisiones, plantilla_decisiones` (+ `exportar_excel` por carga perezosa) |
| Un solo punto para componer un locale | `matching/locales.py::registrar_locale_compuesto` (`flujo.importadores.registrar_locale` delega) |
| Arnés de paridad (reconstruye el motor original del historial de git) | `scripts/oraculo_vinculacion/` (ver su `README.md`) |
| Compuerta de paridad | `tests/test_vinculacion_paridad.py` + `tests/lineas_base_vinculacion.py` (huellas del motor original) |
| Insumos inventados (4 escenarios) | `tests/apoyo_vinculacion.py` |
| Hallazgos y plan de arreglos | `docs/vinculacion/` |

**Evidencia de que no cambió el comportamiento** (todo verificado en Python 3.10 + pandas 2.3 y Python 3.11 + pandas 3.0):
- `ejecutar` + el Excel contra el motor original: 15 tablas, `resumen`, `por_pais`, avisos y el Excel celda por celda (valor y estilo) → **IDÉNTICO** en los 4 escenarios (`scripts/oraculo_vinculacion/comparar.py`).
- `tests/test_vinculacion_paridad.py`: 85 casos (17 huellas por escenario, avisos y controles) → pasan. **Un solo cambio declarado**: una celda del RESUMEN que nombraba dos empresas reales (ahora marcas inventadas); deshaciendo ese texto sale la huella del original.
- 1.107 casos nuevos de prueba del paquete (1.278 con las guardas y meta-pruebas vecinas), 100 % de líneas y ramas en los módulos de A, B, C y D (medido por cada autor); los valores esperados salen de ejecutar el ORIGINAL. mypy: 0 errores en 66 archivos del anillo bloqueante (en frío).
- Revisión independiente con un modelo más fuerte sobre lectura+normalización, comparar+decisiones y deduplicación: equivalentes salvo 1 diferencia no declarada (corregida y declarada) y 4 menores con entradas malformadas.

**Lo que NO se hizo y conviene saber:**
- La revisión independiente de equivalencia **falta** para candidatos+clasificar, vínculos+web+validación, calidad+controles+resumen+`ejecutar`, y tablas. El encargo está listo en `docs/vinculacion/REVISION_EQUIVALENCIA.md`. Las pruebas, el arnés, el fuzz y la mutación de esos módulos sí se hicieron; la revisión humana-equivalente no. Riesgo: una diferencia que los 4 escenarios no ejercen (en la mutación, 4 de cada 10 comportamientos no los ejerce el arnés y los cubren solo las pruebas unitarias).
- La suite completa del repositorio **no se corrió localmente** (≈ 25 min en máquina quieta): se verificó el subconjunto que puede ver el cambio (1.278 pruebas en py311 y en py310: todo `test_vinculacion_*`, las guardas y las meta-pruebas vecinas) y el CI la corre al abrir el PR. Ver la nota del final de la sección 6.
- **No** se comparó contra los datos reales del piloto con la huella `9e3950f469ae0523` (faltan `decisiones_revision_2026-09-29.csv`, `referencia_revision_2026-09-24.csv` y el Excel entregado el 5-oct). Sí se comparó original contra nuevo sobre los mismos insumos reales con la referencia adjudicada (ver `BITACORA.md` IT-30).
- `vincular_empresas` (devolver `ResultadoLinkage` con `correlativa`, `golden`, `enlaces`, `revision`) **no existe aún**; el notebook 10 sigue con su celda `%%writefile`; no hay versión 0.23.0 ni tag.

## 3. Qué falta, en orden

| # | Qué | Cómo se sabe que terminó | Costo relativo |
|---|---|---|---|
| 0 | ✅ PR #4 y PR #5 mezclados. **Enrique borra las ramas viejas** (sección 4) | — | mínimo |
| 1 | ✅ PR #6 mezclado (14-oct). **PR de `claude/fase0-vigilancia`** (Fases 0 a 3: IT-32 e IT-33) abierto; se mezcla con el CI verde (autorizado) | CI verde | bajo |
| 1b | **Fase 3 restante:** puntaje probabilístico (Fellegi–Sunter/EM) sobre el comparador nuevo, otras variables en producción (`extra_features` medidas), umbrales por costo cuando exista verdad real | banco ≥ compuertas; verdad real medida | alto |
| 1c | **Fase 4:** `scripts/escala.py` con el nuevo perfil (139k y 463k), presupuesto de memoria fail-fast, reanudación probada, 1 M/4 M sintéticos, notebook Colab delgado | `compuertas.json` colab_4M con evidencia | alto |
| 2 | ✅ **F3.1b**: arreglos declarados del motor (nivel 1 completo + B2, B3, B8, g3-O1, g3-O2). Pendientes con decisión de Enrique: C9, B7, M-c, A13 (`PLAN_ARREGLOS_F3.1b.md`) | `pytest` de `test_vinculacion_*` verde en py311 y py310; el arnés solo difiere en lo declarado | hecho |
| 3 | F3.2: catálogos a `matching/genericos.py`; locale por configuración (un `Normalizador` no pisa al otro) | prueba «vinculacion no define catálogos» | bajo |
| 4 | F3.3: `vincular_empresas` → `ResultadoLinkage` (+ Excel técnico como vista). **La vista VPI queda vacía hasta resolver el Excel de Caro** | contrato de salida verde | alto |
| 5 | F3.4: lector único de decisiones (`flujo/decisiones.py`) | ida y vuelta: el Excel de salida sirve como archivo de decisiones | medio |
| 6 | F3.5/F3.6: notebooks 10 y 09 delgados, sin `%%writefile` | `nbclient` sin errores; prueba «ningún notebook reimplementa el motor» | medio |
| 7 | F3.7: ADR-0014, `docs/VINCULACION.md`, skill, CHANGELOG/BITÁCORA. Sin versión ni tag (los asigna Enrique) | — | bajo |
| 8 | F4 (escala) y F5/F6 (ingeniería, release 1.0) | ver `PLAN_EJECUCION.md` | alto |

## 4. Cómo llevar lo hecho a `main`

Nadie empuja a `main`: se hace por PR y **Enrique mezcla** (regla del proyecto).
1. ✅ PR #4, PR #5 y PR #6 ya están mezclados. Desde la interfaz de GitHub (Branches → papelera; el proxy de las sesiones no puede borrar ramas) borrar `claude/f2-motor`, `claude/f2/t8`, `claude/f2/t10`, `claude/f2/t11`, `claude/f2/t12`, `claude/f2-cierre`, `claude/f3-vinculacion` y, una vez mezclado su PR, `claude/f3-arreglos` y `claude/fase0-vigilancia`. **Conservar** `feature/vinculacion-0.23.0` hasta que F3.7 decida.
2. ✅ PR #6 (F3.1b) mezclado el 14-oct.
3. PR de `claude/fase0-vigilancia` → `main`: **cambia el perfil `produccion_estandar`** (ADR-0015 y ADR-0016, huella del banco `5bfed0d1` → `969145ac`) y las cotas de `compuertas.json`. Nada de esto está en una versión publicada. Después de mezclarlo, la siguiente rama sale de `main`.

## 5. Decisiones y preguntas abiertas (de Enrique)

- **Excel de Caro / formato VPI → al final** (2026-10-11). No pedir archivo nuevo: releer las cuatro «matrices VPI» del Drive (ids en el archivo de decisiones de Enrique del 2026-10-07) con la hipótesis de que alguna hoja (`LÍNEA 1–3`, `CASOS DE ÉXITO`…) es el formato; **no inventar columnas**. Hasta entonces la vista VPI queda vacía.
- **Notebook 11**: solo existe en el plan («notebooks 01–11»); no existe en el repositorio, el Drive ni el zip entregado. Recomendación: no crear un 11 ahora; cuando se resuelva el Excel de Caro, un notebook de tres celdas que genere la vista VPI. Por defecto F3.5 cubre solo el 10. Falta que Enrique elija (o confirme el defecto).
- **Archivos de septiembre** (`referencia_revision_2026-09-24.csv`, `decisiones_revision_2026-09-29.csv`) y el Excel entregado el 5-oct: prioridad baja; su hoja `DECISIONES` basta para reconstruir ambos. Sirven solo para reproducir la huella `9e3950f469ae0523`.
- **F3.1b (2026-10-13): hecho el nivel 1 completo y cinco del nivel 2** (uniones transitivas con vetos resueltas con union-find restringido). Quedan para decidir con datos reales: **C9** (propagar por web solo desde vínculos ALTA: más precisión, menos recall), **M-c** (el umbral de «parecido» hace inalcanzable esa rama de REVISAR: bajarlo da más recall y más preguntas de revisión), **B7** (`raiz_id` corta en el primer «-») y A13 (promoción del alias).
- Vigentes (2026-10-07): F3 antes de F4; se conservan Optuna y `GroundTruth`; umbral de estrellas 0,80 («unir mal duele más»); alias en español solo en `diccionario`/`leer_resultado`; Snowflake no lee los nombres de v1; equipos consumidores en `CONSUMIDORES.md`.

## 6. Cómo comprobar el estado en una máquina nueva

```bash
git clone https://github.com/EnriqueForero/rues-linker_v2 && cd rues-linker_v2     # main trae F3.1; para F3.1b: git checkout claude/f3-arreglos
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"      # Python 3.11 (y otro entorno con 3.10 para la matriz)
pytest tests/test_vinculacion_*.py tests/test_sin_nombres_reales.py -q       # ≈ 4 min; usar `pytest` simple, como el CI
export LIB_SRC=$PWD/src                                                       # arnés contra el motor original
python scripts/oraculo_vinculacion/extraer_oraculo.py                         # reconstruye el original del commit bab03c2 (verifica SHA-256)
python scripts/oraculo_vinculacion/correr_original.py py311_pandas3           # referencia, ≈ 45 s
python scripts/oraculo_vinculacion/comparar.py py311_pandas3 record_linkage.flujo.vinculacion   # IDÉNTICA ×4
python scripts/reglas_estrictas.py --base origin/main && python scripts/deuda.py --referencia docs/evidencia/deuda_f0.json
```
El arnés necesita `.oraculo_vinculacion/textos_declarados.json` (cinco frases con marcas reales que el repositorio no puede contener); el `README.md` del arnés dice cómo reconstruirlo.

*Nota sobre la suite completa:* un agente vio fallar una vez, bajo carga, `tests/test_contrato_salida.py::test_id_entidad_nit_y_ent[cruce_disco]` (`'NIT-80020000' != 'NIT-800200007'`); suelto, y el archivo completo, pasan. Si el CI del PR lo muestra rojo, es intermitente de orden/carga y no del paquete nuevo (que no lo importa): investigar `cruce_disco` aparte.

## 7. Reglas que no se negocian

Una sola cosa a la vez · prueba primero · **nada se repara en silencio** (todo cambio de comportamiento se declara: ADR o línea base) · una regla se escribe una vez · código, mensajes y docstrings en español · sin datos licenciados ni nombres de empresas reales en el repositorio (`tests/test_sin_nombres_reales.py` vigila; ampliar `ARCHIVOS_VIGILADOS` al escribir un documento nuevo) · nunca empujar a `main` · el pie de commit lo fija el sistema de cada sesión · si algo bloquea, dejar un archivo de estado y parar; no inventar.

## 8. Costo y modo económico

Lo gastado en F3.1 (tokens de subagentes reportados, sin contar al coordinador ni al agente del Excel): lectura/normalización 0,46 M · comparar/decisiones/deduplicar 0,79 M · candidatos…validación 0,44 M · tablas 0,46 M · tres revisiones independientes 1,05 M ≈ **3,2 M**. El método (agentes en paralelo, arnés híbrido, fuzz, mutación y revisión independiente) compra confianza y es caro.

Para seguir gastando menos, sin perder la compuerta de paridad:
- Un solo modelo, sin abanico de agentes; **prueba primero y el arnés/`test_vinculacion_paridad.py` como único verificador** de cada arreglo.
- F3.1b ya está hecho (se trabajó con un solo modelo, sin abanico de agentes, y con `declarar_cambio.py` para cada declaración).
- Saltar la revisión independiente salvo en lo que toque la clasificación o las uniones (los puntos donde un error cuesta vínculos falsos).
- Parar en cualquier hito cerrado: cada fila de la sección 3 deja el repositorio en un estado coherente.

## 9. Mapa de documentos

`REANUDAR.md` (esto) · **`DIAGNOSTICO_Y_RUTA_2026-10-13.md` (la revisión integral del 13-oct y la ruta que reordena el plan: léala antes que el plan)** · `PLAN_EJECUCION.md` (el plan) · `BITACORA.md` IT-30 e IT-31 (qué pasó y por qué) · `vinculacion/HALLAZGOS_MOTOR_ORIGINAL.md` (≈ 90 hallazgos del motor original; los de nivel 1 ya corregidos) · `vinculacion/PLAN_ARREGLOS_F3.1b.md` (qué se arregló, qué no y por qué) · `vinculacion/REVISION_EQUIVALENCIA.md` (encargo para la revisión independiente) · `CONSUMIDORES.md` (quién lee qué; notas del 2026-10-11 y 2026-10-13) · `adr/` (decisiones; la próxima es 0014).
