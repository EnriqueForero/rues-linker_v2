# Optimizar parámetros con Optuna

> Vigente para **0.22.4** (F2.14 y F2.15). Notebook:
> [`notebooks/12_optimizar_parametros_con_optuna.ipynb`](../notebooks/12_optimizar_parametros_con_optuna.ipynb).
> Código: `record_linkage.evaluation` (`GroundTruthGenerator`,
> `OrchestratorOptimizer`, `espacio_de_busqueda`, `evaluar_pares_etiquetados`,
> `graficar_convergencia`, `graficar_importancia`). Extras: `optimization`
> (optuna) y `viz` (matplotlib).

## Cuándo optimizar (y cuándo no)

Optimice cuando **su base** se comporta distinto de la de referencia: otro
país, otro tipo de identificador, nombres con convenciones propias, una
fuente sin identificador en la mitad de las filas. La señal es que `linkage()`
con el perfil por defecto deja sin unir lo que una persona une a simple vista,
o une lo que no debe.

No optimice para «subir el F1» del banco institucional: los valores por
defecto de los perfiles se deciden con `scripts/banco.py` y conformidad
(`docs/BANCO.md`, ADR-0001 y 0006), no con un estudio sobre una base
particular. Un parámetro elegido mirando una base siempre parece mejor de lo
que es: calibrar contra `ground_truth_grande.csv` solo produjo el sobreajuste
de `fuentes_mixtas` en 0.18.0.

Regla de la casa: **unir dos empresas distintas duele más que dejar sin
unir**. Optimice `precision` o `f1`; `recall` y `f2` solo si de verdad el
costo está invertido.

## Cómo construir la verdad

1. **Muestra estratificada** con `GroundTruthGenerator`: pares fáciles (mismo
   identificador), difíciles (nombre casi igual, identificador distinto o
   ausente), ambiguos (nombre parecido a medias) y negativos (no se parecen).
   Una muestra al azar está llena de pares triviales y no mide nada donde el
   motor puede equivocarse. Los ids de cada par (`ID_1`, `ID_2`) son el índice
   del DataFrame: póngale la columna única por fila (`COL_ID`) antes. Esa misma
   columna se pasa como `col_id=` al optimizador (y a `linkage()`): si no se
   llama `ID` ni `ID_REGISTRO`, sin `col_id` ningún trial se puede medir.
2. **Etiquetar**: una persona abre `pares_para_etiquetar.xlsx` y llena
   `IS_MATCH` con `1` (misma empresa) o `0` (distinta). Lo que no sabe lo deja
   vacío y no cuenta (vacío es `NaN` o `""`: el archivo releído con
   `dtype=str` y `fillna("")` sigue valiendo). Cualquier otro valor (`si`,
   `x`) hace fallar al optimizador **al construirlo**, con el mensaje que
   dice cuáles corregir: ningún trial se gasta con una verdad inválida. En el notebook la etiqueta se **simula** desde la
   columna `ENTIDAD` de la base de demostración; con datos reales la pone una
   persona, y eso no se puede automatizar sin convertir el estudio en una
   medición de la regla que usted mismo escribió.
3. **Dos formatos de verdad**, con dos métricas distintas:

   | Verdad | Columnas | Qué mide cada trial | Objetivos |
   |---|---|---|---|
   | **Por pares** (lo que sale de etiquetar) | `ID_1`, `ID_2`, `IS_MATCH` | `evaluar_pares_etiquetados`: solo los pares etiquetados. Un positivo acierta si el motor junta a sus dos registros; un negativo falla si los junta. | `f1`, `f2`, `precision`, `recall` |
   | **Por registro** (una partición completa) | `ID_REGISTRO`, `ID_GROUP` (+ `ESTRATO`, `REGIMEN`, `CASO`) | `evaluar_pares`: todos los pares de la partición, con las métricas del banco si hay `ESTRATO`. | los anteriores y `macro_f1` |

   No convierta pares etiquetados en una partición cerrando los positivos en
   componentes: toda unión correcta entre registros que nadie etiquetó (el
   tercer registro de un mismo NIT) contaría como falso positivo. La
   validación cruzada (`validacion_cruzada`) reparte grupos entre pliegues y
   por eso exige la verdad por registro.

## Cómo optimizar y aplicar

```python
from record_linkage import crear_config_orchestrator, linkage
from record_linkage.evaluation import OrchestratorOptimizer, espacio_de_busqueda

optimizer = OrchestratorOptimizer(
    base_config=crear_config_orchestrator(perfil="produccion_estandar", validate=False),
    sources={"BASE": base[["ID", "NIT", "RAZON_SOCIAL", "CIUDAD"]]},
    truth=pares_etiquetados,                      # ID_1, ID_2, IS_MATCH
    search_space=espacio_de_busqueda({            # solo claves que el perfil declare
        "score_threshold": (0.40, 0.80, 0.05),
        "min_name_similarity": (0.30, 0.80, 0.05),
        "max_nit_distance": (0, 3),
    }),
)
antes = optimizer.evaluar_config({}, "f1")        # línea base: el perfil tal cual
resultado = optimizer.optimize(n_trials=8, optimization_target="f1", seed=42)
res = linkage(fuentes, col_id="ID", profile="produccion_estandar",
              ajustes_perfil=optimizer.ajustes_perfil(), carpeta_salida=salida)
despues, _ = optimizer.evaluar_correlativa(res.correlativa[["ID", "ID_GRUPO"]], "f1")
optimizer.guardar_estudio(salida / "estudio_optuna.json")
```

- `ajustes_perfil()` traduce `best_params` al formato de
  `linkage(ajustes_perfil=...)`: la misma regla con la que el optimizador armó
  cada trial, escrita una vez. `best_config` es el mismo resultado para quien
  llama al `Orchestrator` directamente.
- El espacio se contrasta con el perfil **antes del primer trial**: una clave
  que el perfil no declare (`nit_empty_passes_filter` en
  `produccion_estandar`, aunque el scorer la lea) falla al construir el
  optimizador, porque `linkage` la rechazaría al aplicar. El espacio por
  defecto (`default_search_space`) incluye `nit_empty_passes_filter`, así que
  solo vale con perfiles que la declaren (`produccion_calibrada`,
  `alta_precision`); con `produccion_estandar` use `espacio_de_busqueda({...})`
  sin esa clave.
- Un trial cuya correlativa no se puede medir contra la verdad (los ids de
  `ID_1`/`ID_2` no son los de la correlativa) queda como `failed` con su
  `error` en el historial, no como un score 0,0: si todos fallan, el estudio
  termina sin trials completados y `guardar_estudio` se niega a escribirlo.
- `evaluar_correlativa` mide la correlativa de producción sobre los mismos
  pares: el score aplicado debe ser el del estudio. Si no lo es, cambió la
  base, el perfil o la versión; no aplique los ajustes.
- El JSON del estudio trae versión, fecha, objetivo, mejores parámetros,
  `ajustes_perfil` e historial por trial: basta para aplicar en otro notebook
  sin volver a optimizar.

## Lo que NO hace

- No inventa la verdad ni la deduce del identificador.
- No cambia los valores por defecto de la librería ni la huella del banco:
  los ajustes viven en `ajustes_perfil` de su corrida y en el `manifest.json`
  de su carpeta de salida.
- No mide `macro_f1` con pares etiquetados (no hay estratos).
- No garantiza que 8 trials basten: lea la convergencia; si el mejor
  acumulado sigue subiendo, pida más trials o acote el espacio.
- No sustituye el juicio sobre el tamaño de la muestra: con 160 pares, 0,02
  de F1 son tres pares. Etiquete más pares de la clase donde fallan los
  FP/FN antes de llevar nada a producción.

## Medido (base de demostración del notebook, 304 registros, 120 entidades)

160 pares etiquetados (61 positivos, 99 negativos). `produccion_estandar` sin
ajustes: precisión 1,000 · recall 0,984 · F1 0,9917 (1 FN). Ocho trials en
≈ 24 s (≈ 3 s cada uno): mejor F1 1,000 (`score_threshold` 0,50,
`min_name_similarity` 0,55, `lsh_threshold` 0,55, `max_nit_distance` 1);
`linkage` con esos ajustes reproduce 1,000 sobre los mismos pares. Seis de los
ocho trials dan F1 0,688 (recall 0,52): un `score_threshold` ≥ 0,55 deja sin
unir los registros sin NIT. La precisión fue 1,000 en todos: la base de
demostración no hace tropezar al motor en los ambiguos; una base real sí. El
notebook entero corre en ≈ 35 s con `nbclient`.
