# Evidencia

Un par de archivos por corrida del banco.

| Archivo | Contenido |
|---|---|
| `corrida_<etiqueta>.json` | Todas las métricas, la especificación con la que se corrió y el entorno (versiones de Python, pandas, numpy) |
| `prediccion_<etiqueta>.parquet` | La partición predicha junto a la verdad, una fila por registro |

El JSON se versiona a propósito: es la prueba de cada afirmación del CHANGELOG.
El Parquet de predicción queda **local** —`*.parquet` está en el `.gitignore`
del repositorio y el build del 08 aplica la misma regla— y se regenera con el
comando de abajo cuando haga falta analizar un error fila a fila. (Hasta 0.22.4
este párrafo decía que ambos se versionaban; el `.gitignore` nunca lo hizo.)

## Carpetas por fase de la ruta 2026-10-13

| Carpeta | Contenido |
|---|---|
| `revision_2026-10-13/` | la revisión integral: banco, escala 139k y el experimento de bloqueo TF-IDF que motivó la Fase 2 |
| `fase2_candidatos/` | las corridas del generador híbrido (`corrida_*.json`) y el recall de pares del bloqueo (`recall_*.json`); lectura en `fase2_candidatos_hibridos.md` |
| `fase3_comparador/` | las corridas del comparador Soft TF-IDF y del perfil nuevo; lectura en `fase3_comparador_nombre.md`; `corrida_h_s085c093_id.json` reproduce, con `--ajuste`, la huella de `corrida_base_f3.json` (la línea base F3 y la evidencia de `compuertas.json`) |

## Plantilla de base real (2026-10-14)

| Archivo | Contenido |
|---|---|
| `plantilla_base_real_casos.json` | `scripts/verificar_plantilla_base_real.py` sobre `data/ejemplos/`: 536 registros, 281 grupos predichos vs 278, pares P 0,9875 / R 0,9754 / F1 0,9814 y la tabla caso → acertó/falló (18 de 24); lectura en `docs/PLANTILLA_BASE_REAL.md` §5 |
| `plantilla_base_real_verdad_real.json` | `scripts/medir_verdad_real.py` contra `plantilla_etiquetas_pares.csv` (106 pares, 3 UNCLEAR descartados): precisión y recall con Wilson 95 % por régimen (CON_ID, SIN_ID, MIXTO) |
| `plantilla_base_real_verdad_real_AB.json` | lo mismo con los dos archivos «a ciegas» (`_anotador_A.csv`, `_anotador_B.csv`): kappa 0,703, 12 desacuerdos |

Es sintético: certifica el formato de la base real y de la verdad real, no el motor. No entra en
`compuertas.json` ni en `tests/lineas_base.py`.

## Corridas de referencia de 0.18.0

| etiqueta | qué es |
|---|---|
| `base_0174` | línea base de 0.17.4, punto de comparación de todo |
| `perfil_mixtas` | el perfil `fuentes_mixtas` publicado |
| `fold{0,1,2}_base` / `fold{0,1,2}_nueva` | validación fuera de muestra por pliegues |
| `paridad_*` | corridas que verifican que un refactor no cambió nada |
| `idf_*`, `comp_*`, `lsh_*`, `combo_*`, `veto_*` | el barrido de calibración completo |

Para reproducir cualquiera:

```bash
python scripts/banco.py --etiqueta <nombre> [las opciones de "especificacion" del JSON]
```

## Trinquete de deuda técnica (F0.8)

`deuda_f0.json` es el **techo** de deuda de `src/record_linkage/`: cinco conteos
(`cc_ge_20`, `except_sin_relanzar`, `print`, `os_path`, `mypy`) con sus
ubicaciones `archivo:línea`, la versión del paquete, el commit y las versiones
de ruff y mypy con que se midió. El job `deuda` del CI corre
`python scripts/deuda.py --referencia docs/evidencia/deuda_f0.json` y falla si
cualquier conteo sube. Cuando la deuda baja, el script lo dice y el techo se
actualiza en el mismo PR:

```bash
python scripts/deuda.py --escribir docs/evidencia/deuda_f0.json   # ≈ 1–2 min (mypy)
python scripts/deuda.py --sin-mypy                                 # vistazo rápido
```

Qué mide cada métrica y por qué `print` se cuenta con `ast` y no con ruff T201
está en el docstring de `scripts/deuda.py`.

## Líneas base de F0 (plan v2 → producción)

Todo lo que F1…F5 compara "antes/después" nace aquí, medido sobre el árbol de
0.22.4 sin tocar el motor. Cada línea base tiene una prueba que la lee, así que
moverla sin declararlo rompe la suite.

| archivo | qué fija | quién lo lee |
|---|---|---|
| `corrida_base_f0.json` | el banco (30.486 registros): huella `1e365ba8…`, F1 0,878, macro-F1 0,892, 287 FP que tocan negativos, recursos por fase. **Histórica desde F2** (F2.1 cambió la partición por defecto, ADR-0011): no se reescribe; es el motor de 0.22.4 sin tocar y la referencia de la paridad con la perilla apagada | `tests/lineas_base.py` (`BANCO_F0`), `tests/test_banco_linea_base.py` (prueba de historia: el JSON sigue coincidiendo con la constante) y `tests/test_cobertura_sin_identificador_f21.py` (paridad `slow`: con `cobertura_sin_identificador.activa=false` el banco reproduce esta huella) |
| `conformidad_{dedup,linkage}_{base,corroborado}.json` | los 43 casos por `dedupe_esquema`, sin y con `--corroborar` (C09 y C21 solo pasan con él) | `tests/test_conformidad_evidencia.py` |
| `conformidad_{dedup,linkage}_{linkage,flujo_cruce}_base.json` | los mismos 43 por el camino de producción `api.linkage()` y por `flujo.cruce` (F2.13); `--corroborar` no existe ahí. Veredicto por caso y camino en `docs/CONFORMIDAD.md` §4 | `tests/test_conformidad_caminos.py` |
| `escala_base_f0.json` | tiempo por fase y pico de RSS a 139k y 463k filas sintéticas; `scripts/escala.py --comparar` falla con una regresión > 10 % | `tests/test_escala.py` |
| `deuda_f0.json` | el techo de deuda técnica (sección anterior); el nombre es el de F0, el contenido es el techo VIGENTE (F1 lo bajó: `except` 121 → 103, mypy 108 → 102; F2 lo bajó otra vez: `print` 180 → 152, `os_path` 124 → 117, mypy 102 → 80, `except` 103 → 101) | job `deuda` del CI |
| `escala_f1.json` | la misma medición que `escala_base_f0.json` sobre el tronco de F1 (contrato de salida); `scripts/escala.py --comparar base_f0 f1` es la compuerta | `tests/test_escala.py` |
| `escala_f2.json` | la misma medición sobre el tronco de F2 en 723d23e (cobertura por estrellas activa, `ID_GRUPO` por contenido, `ID_ENTIDAD` estable), con un agente de pulido corriendo en paralelo (los tiempos son ligeramente altos). **139k:** 466,5 s (L2 264,8 · L3 156,6 · L5 31,1), RSS 1.154 MiB, precisión 0,9856, recall 0,9475, grupo mayor sin identificador 13 filas / 3 entidades, 0 grupos ≥ 50, huella `d904ea16…`. **463k:** 1.977,2 s (L2 899,2 · L3 952,8 · L5 77,5), RSS 2.485 MiB, precisión 0,9943, recall 0,9743, grupo mayor 14 filas / 5 entidades, huella `9b903d58…`. `scripts/escala.py --comparar base_f0 f2 --holgura-segundos 2`: tiempo total (+6,5 % y +4,2 %), L1, L2, L3, L4, memoria (−3 % y −4 %) y candidatos (idénticos) dentro del 10 %; **L5** sube +13,1 s a 139k y +16,0 s a 463k, el costo declarado de la cobertura por estrellas (ADR-0011). El comparador dice FALLA por esa fase y por la huella distinta (declarada); la compuerta F2 se juzga por tiempo total, memoria y grupo mayor | `tests/test_escala.py` (el comparador) |
| `../../tests/contratos/esquema_salida_v0.json` | columnas, tipos y archivos que `linkage()` produce hoy (contrato de salida v0) | `tests/test_contrato_salida_v0.py` |
| `cobertura_umbral.json` | la tabla de decisión del umbral de `cobertura_sin_identificador` (F2.2, ADR-0011): por umbral, precisión, recall por régimen, macro-F1 y FP sobre negativos en el banco, y precisión, FP sobre negativos y grupo mayor sin identificador a 139k; la regla aplicada y el elegido. Las corridas del barrido NO se versionan (se corren con `--evidencia` fuera de esta carpeta); la tabla se compone con `scripts/cobertura_umbral.py` | `tests/test_cobertura_umbral_f22.py` (reaplica la regla y exige que el elegido sea el del perfil) |

## Líneas base de F2 (motor con cobertura por estrellas)

Una línea base nueva no reemplaza a la anterior: es un JSON nuevo, una
constante nueva en `tests/lineas_base.py` y `BANCO_VIGENTE` apuntando a ella
(docstring de ese módulo). Las anteriores quedan como historia y una prueba
parametrizada exige que cada JSON siga coincidiendo con su constante.

| archivo | qué fija | quién lo lee |
|---|---|---|
| `corrida_base_f2.json` | el banco (30.486 registros) sobre `main` en 059f80b (F2 mezclada), remedido el 2026-10-10 en máquina sin carga: huella `5bfed0d1…`, F1 0,8666, macro-F1 0,8803, B³ F1 0,9487, 246 FP que tocan negativos, recursos por fase. Medido con `python scripts/banco.py --etiqueta base_f2 --datos data/benchmark/benchmark_institucional.csv.gz` (perfil `produccion_estandar`, semilla 42, sin ajustes). **Por qué cambió la huella**: F2.1 activa por defecto la cobertura por estrellas en los grupos sin identificador válido (`cobertura_sin_identificador`, umbral 0,80; ADR-0011; F2.2 fija el umbral con `cobertura_umbral.json`). Es un cambio de partición declarado que cambia recall por precisión: precision 0,9385 → 0,9557, recall 0,8249 → 0,7927, FP sobre negativos 287 → 246; pasa la compuerta F2 (macro-F1 ≥ 0,880, FP-neg ≤ 287) | `tests/lineas_base.py` (`BANCO_F2` = `BANCO_VIGENTE`) y `tests/test_banco_linea_base.py` (la prueba `slow` reproduce la huella) |

`python scripts/banco.py --comparar base_f0 base_f2` muestra el cambio
(huella distinta, precisión y FP-neg mejoran, recall y F1 bajan); su veredicto
«FALLA» es el del comparador de regresión (exige que ninguna métrica baje) y no
el de la compuerta F2, que se juzga por macro-F1 y FP sobre negativos.

> **Nota (2026-10-10, remedición):** la primera medición de este JSON (sobre
> ef13f83) registró 113,5 s porque la máquina tenía agentes corriendo en
> paralelo. Se remidió sobre `main` (059f80b) sin carga: **67,5 s** (L1 1,3 ·
> L2 43,9 · L3 7,8 · L4 0,5 · L5 11,6), RSS pico 501 MiB, **misma huella
> `5bfed0d1…` y mismas cifras de calidad**. La única diferencia de contenido es
> el campo `calidad.grupo_mayor_sin_identificador` (16 filas / 1 entidad, 0
> grupos ≥ 50), la métrica que añadió F2.2 después de la primera medición. Así
> `--comparar base_f2 <nueva>` usa un tiempo base real (criterio ≤ ×1,2).

El determinismo entre procesos (dos `PYTHONHASHSEED` distintos → misma huella)
no deja JSON: lo verifica `tests/test_determinismo_linkage_procesos.py` en cada
corrida.

```bash
python scripts/banco.py --etiqueta base_f0                       # ≈ 55 s
python scripts/banco.py --etiqueta base_f2                       # ≈ 70–75 s en máquina sin carga (F2.1: + cobertura por estrellas)
python scripts/banco.py --comparar base_f0 base_f2                # el cambio declarado de F2.1
python scripts/conformidad.py --etiqueta base                     # y --corroborar --etiqueta corroborado
python scripts/escala.py --etiqueta base_f0                       # 139k + 463k, decenas de minutos
python scripts/escala.py --comparar base_f0 <nueva>               # la compuerta de escala
ACTUALIZAR_ESQUEMA_V0=1 pytest tests/test_contrato_salida_v0.py   # solo con un cambio de contrato declarado
```
