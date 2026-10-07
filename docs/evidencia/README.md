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
| `corrida_base_f0.json` | el banco (30.486 registros): huella `1e365ba8…`, F1 0,878, macro-F1 0,892, 287 FP que tocan negativos, recursos por fase | `tests/lineas_base.py` (`BANCO_F0`) y `tests/test_banco_linea_base.py` (la prueba `slow` reproduce la huella) |
| `conformidad_{dedup,linkage}_{base,corroborado}.json` | los 43 casos sin y con `--corroborar` (C09 y C21 solo pasan con él) | `tests/test_conformidad_evidencia.py` |
| `escala_base_f0.json` | tiempo por fase y pico de RSS a 139k y 463k filas sintéticas; `scripts/escala.py --comparar` falla con una regresión > 10 % | `tests/test_escala.py` |
| `deuda_f0.json` | el techo de deuda técnica (sección anterior) | job `deuda` del CI |
| `../../tests/contratos/esquema_salida_v0.json` | columnas, tipos y archivos que `linkage()` produce hoy (contrato de salida v0) | `tests/test_contrato_salida_v0.py` |

El determinismo entre procesos (dos `PYTHONHASHSEED` distintos → misma huella)
no deja JSON: lo verifica `tests/test_determinismo_linkage_procesos.py` en cada
corrida.

```bash
python scripts/banco.py --etiqueta base_f0                       # ≈ 55 s
python scripts/conformidad.py --etiqueta base                     # y --corroborar --etiqueta corroborado
python scripts/escala.py --etiqueta base_f0                       # 139k + 463k, decenas de minutos
python scripts/escala.py --comparar base_f0 <nueva>               # la compuerta de escala
ACTUALIZAR_ESQUEMA_V0=1 pytest tests/test_contrato_salida_v0.py   # solo con un cambio de contrato declarado
```
