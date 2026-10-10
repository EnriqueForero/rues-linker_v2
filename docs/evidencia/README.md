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
| `conformidad_{dedup,linkage}_{base,corroborado}.json` | los 43 casos sin y con `--corroborar` (C09 y C21 solo pasan con él) | `tests/test_conformidad_evidencia.py` |
| `escala_base_f0.json` | tiempo por fase y pico de RSS a 139k y 463k filas sintéticas; `scripts/escala.py --comparar` falla con una regresión > 10 % | `tests/test_escala.py` |
| `deuda_f0.json` | el techo de deuda técnica (sección anterior); el nombre es el de F0, el contenido es el techo VIGENTE (F1 lo bajó: `except` 121 → 103, mypy 108 → 102) | job `deuda` del CI |
| `escala_f1.json` | la misma medición que `escala_base_f0.json` sobre el tronco de F1 (contrato de salida); `scripts/escala.py --comparar base_f0 f1` es la compuerta | `tests/test_escala.py` |
| `../../tests/contratos/esquema_salida_v0.json` | columnas, tipos y archivos que `linkage()` produce hoy (contrato de salida v0) | `tests/test_contrato_salida_v0.py` |

## Líneas base de F2 (motor con cobertura por estrellas)

Una línea base nueva no reemplaza a la anterior: es un JSON nuevo, una
constante nueva en `tests/lineas_base.py` y `BANCO_VIGENTE` apuntando a ella
(docstring de ese módulo). Las anteriores quedan como historia y una prueba
parametrizada exige que cada JSON siga coincidiendo con su constante.

| archivo | qué fija | quién lo lee |
|---|---|---|
| `corrida_base_f2.json` | el banco (30.486 registros) sobre el tronco `claude/f2-motor` en ef13f83 (merge de F2.1): huella `5bfed0d1…`, F1 0,8666, macro-F1 0,8803, B³ F1 0,9487, 246 FP que tocan negativos, recursos por fase. Medido por el coordinador con `python scripts/banco.py --etiqueta base_f2 --datos data/benchmark/benchmark_institucional.csv.gz` (perfil `produccion_estandar`, semilla 42, sin ajustes). **Por qué cambió la huella**: F2.1 activa por defecto la cobertura por estrellas en los grupos sin identificador válido (`cobertura_sin_identificador`, umbral 0,80; ADR-0011; F2.2 fija el umbral con `cobertura_umbral.json`). Es un cambio de partición declarado que cambia recall por precisión: precision 0,9385 → 0,9557, recall 0,8249 → 0,7927, FP sobre negativos 287 → 246; pasa la compuerta F2 (macro-F1 ≥ 0,880, FP-neg ≤ 287) | `tests/lineas_base.py` (`BANCO_F2` = `BANCO_VIGENTE`) y `tests/test_banco_linea_base.py` (la prueba `slow` reproduce la huella) |

`python scripts/banco.py --comparar base_f0 base_f2` muestra el cambio
(huella distinta, precisión y FP-neg mejoran, recall y F1 bajan); su veredicto
«FALLA» es el del comparador de regresión (exige que ninguna métrica baje) y no
el de la compuerta F2, que se juzga por macro-F1 y FP sobre negativos.

El determinismo entre procesos (dos `PYTHONHASHSEED` distintos → misma huella)
no deja JSON: lo verifica `tests/test_determinismo_linkage_procesos.py` en cada
corrida.

```bash
python scripts/banco.py --etiqueta base_f0                       # ≈ 55 s
python scripts/banco.py --etiqueta base_f2                       # ≈ 75 s (F2.1: + cobertura por estrellas)
python scripts/banco.py --comparar base_f0 base_f2                # el cambio declarado de F2.1
python scripts/conformidad.py --etiqueta base                     # y --corroborar --etiqueta corroborado
python scripts/escala.py --etiqueta base_f0                       # 139k + 463k, decenas de minutos
python scripts/escala.py --comparar base_f0 <nueva>               # la compuerta de escala
ACTUALIZAR_ESQUEMA_V0=1 pytest tests/test_contrato_salida_v0.py   # solo con un cambio de contrato declarado
```
