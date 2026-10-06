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
