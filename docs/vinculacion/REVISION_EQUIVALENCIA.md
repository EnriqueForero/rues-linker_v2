# Encargo para la revisión independiente de equivalencia (plantilla)

> Plantilla usada para las revisiones g1–g3 de F3.1; sirve para las pendientes (candidatos+clasificar, vínculos+web+validación,
> calidad+controles+resumen+ejecutar, tablas) y para verificar cada arreglo de F3.1b. Se entrega a un revisor con el modelo más fuerte
> disponible, junto con el trecho del original y los módulos nuevos que le toquen.


Contexto: el motor de vinculación de empresas del notebook 10 (archivo ORIGINAL, solo lectura:
`.oraculo_vinculacion/oracle/vinculador_empresas.py` (se reconstruye con `scripts/oraculo_vinculacion/extraer_oraculo.py`), 2200 líneas)
se portó al paquete `src/record_linkage/flujo/vinculacion/` (y `flujo/vinculacion_excel/`) de la rama de integración
la raíz del repositorio (rama `claude/f3-vinculacion`). La regla es **comportamiento idéntico**: lo único que debe cambiar son (a) la partición en funciones, (b) estilo y tipos,
(c) `print` → `logging`/`registro`, (d) marcas de empresas reales → inventadas, y (e) los cambios DECLARADOS en
`docs/vinculacion/HALLAZGOS_MOTOR_ORIGINAL.md` (esos defectos del original se CONSERVAN a propósito y van marcados `# Hallazgo F3:`).

Ya hay evidencia automática: un arnés híbrido (el motor original con las piezas nuevas sustituidas) da tablas y Excel idénticos en 4 escenarios
sobre insumos inventados, y las pruebas unitarias salen del original. Lo que NO prueban esa evidencia es todo lo que los 4 escenarios no ejercen.
Su trabajo es encontrar **cualquier diferencia de comportamiento** entre original y nuevo que esa evidencia no vea.

## Qué hacer

1. Lea el trecho del original y el módulo nuevo que le asignan, función por función, y compare LÍNEA A LÍNEA. Busque, con malicia:
   orden de evaluación y de iteración (dict/set/`pd.unique`/`groupby(sort=)`/`sort_values(kind=)`), mutación en sitio contra copia
   (el original puede modificar un argumento que el llamador reutiliza), valores por omisión, `copy()` omitidos o añadidos, dtypes
   resultantes (object/string/bool/float/Int64), tratamiento de `NaN`/`None`/`pd.NA`/cadenas vacías, comparaciones de flotantes en la
   frontera (`>=` contra `>`), redondeos, índices (`reset_index`, alineación por índice al asignar columnas), excepciones y su MENSAJE,
   caché compartida entre llamadas, estado global, efectos laterales (archivos, `attrs`, logging), y diferencias de pandas 2 contra pandas 3
   (`astype(str)` sobre tipos anulables da «<NA>»; dtype `str` contra `object`). Python 3.10 y 3.11.
2. Para cada sospecha, **demuéstrela ejecutando**: importe el original (`sys.path.insert(0, ".oraculo_vinculacion/oracle"); import vinculador_empresas as ve`)
   y el paquete nuevo (use `LIB_SRC=<repo>/src`; `python -I` ignora PYTHONPATH: use `sys.path.insert`), llame a ambos con las
   mismas entradas adversarias (vacías, una fila, NaN, `pd.NA`, Unicode, nombres larguísimos, IDs repetidos, columnas faltantes, tipos mixtos)
   y compare resultados Y excepciones. Entornos: uno con Python 3.11 + pandas 3 y otro con Python 3.10 + pandas 2.
   Los scripts y salidas van a su carpeta de trabajo (una carpeta temporal fuera del repositorio), **nunca** dentro del repositorio.
3. NO modifique el repositorio ni ninguna rama; no haga commits. Solo lee, ejecuta y reporta.
4. No repita lo ya conocido: lo declarado en `docs/vinculacion/HALLAZGOS_MOTOR_ORIGINAL.md` es intencional. Si encuentra una diferencia que NO está declarada, es un hallazgo.
   Si encuentra un defecto del ORIGINAL que no está en esas listas, repórtelo aparte como «defecto del original no catalogado».

## Cómo reportar

Su texto final es un informe técnico con: (1) por cada función del trecho: veredicto «equivalente» o «difiere» y CÓMO lo verificó
(qué leyó, qué ejecutó); (2) lista de hallazgos con gravedad (alta = cambia una tabla de salida en un caso realista; media = cambia un caso raro
o un mensaje; baja = cosmético), línea del original, archivo:línea del nuevo, entrada mínima que lo muestra, salida del original contra la del nuevo,
y arreglo propuesto; (3) lo que no pudo verificar y por qué. Sea breve en lo que está bien y detallado en lo que no.
