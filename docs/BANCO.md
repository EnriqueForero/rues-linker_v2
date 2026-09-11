# El banco de pruebas

Un instrumento único para decidir si un cambio mejoró algo. Existe porque
durante tres versiones cada mejora se justificó con una medición hecha a mano,
distinta cada vez, y así se colaron dos regresiones a producción: una de
velocidad en 0.17.0 y una de memoria en 0.17.3, ambas "verificadas".

## El conjunto de referencia

`data/ground_truth/ground_truth_grande.csv`

| | |
|---|---|
| Registros | 12.427 |
| Grupos verdaderos | 3.486 |
| Pares verdaderos | 22.073 |
| Fuentes | 5 (SUPERSOCIEDADES, CRM, DIAN, RUES, IMPORTACIONES) |
| Régimen CON_NIT | 10.085 registros (81 %) |
| Régimen SIN_NIT | 2.342 registros (19 %) |
| Casos negativos | 43 (33 intermediarios + 10 genéricos) |
| Variables disponibles | NIT, RAZON_SOCIAL, CIUDAD, TELEFONO, DIRECCION, EMAIL |

Se eligió sobre `gt_robusto.parquet` (7.368 registros, solo NIT y nombre) por
tres razones: trae las variables de contacto, lo que permite medir si aportan;
trae casos negativos explícitos, sin los cuales la precisión no significa nada;
y mezcla los dos regímenes, que es como se ven los datos reales.

## Qué mide, en una sola pasada

**Calidad** — precision, recall y F1 por pares; B-cubed (por registro, no
premia acertar un grupo grande y fallar mil chicos); recall estratificado por
régimen y por caso; y falsos positivos que tocan un registro diseñado como
negativo, que son los errores caros.

**Costo** — tiempo total y por fase (L1…L5), pico de RSS muestreado cada 0,25 s
durante toda la corrida, bytes de artefactos en disco, candidatos generados
por el bloqueo y pares que superaron el umbral.

**Identidad** — huella SHA-256 canónica de la partición. Dos corridas con la
misma huella produjeron exactamente la misma respuesta; es lo que permite
afirmar "este refactor no cambió nada" sin revisar 12.427 filas.

## Uso

```bash
python scripts/banco.py --etiqueta antes
python scripts/banco.py --etiqueta despues --ajuste lsh_threshold=0.45
python scripts/banco.py --comparar antes despues
```

`--comparar` devuelve código de salida 0 si pasa y 1 si no, así que sirve
directo en CI.

### Opciones

| Opción | Para qué |
|---|---|
| `--perfil P` | correr con otro perfil de configuración |
| `--ajuste CLAVE=VALOR` | sobrescribir una perilla puntual |
| `--variables-extra COL[:TIPO[:PESO]]` | declarar evidencia adicional |
| `--confiables F1 F2` | fuentes que no se deduplican internamente |
| `--pliegue K --pliegues N` | evaluar solo un pliegue de grupos |

### Validación fuera de muestra

Un umbral elegido mirando todos los datos siempre parece mejor de lo que es.
Los pliegues se reparten **por grupo** —nunca por fila, que rompería grupos
verdaderos y falsearía el recall— con un hash estable del identificador, de
modo que el mismo pliegue contiene siempre los mismos grupos.

```bash
for k in 0 1 2; do
  python scripts/banco.py --etiqueta base_f$k  --pliegue $k
  python scripts/banco.py --etiqueta nueva_f$k --pliegue $k --ajuste ...
done
```

## Umbrales del veredicto

Por defecto (`evaluation.comparador.Umbrales`):

| Métrica | Criterio |
|---|---|
| precision, recall, F1, B³ F1 | no pueden bajar |
| tiempo total | hasta +20 % (ruido de máquina compartida) |
| pico de RSS | hasta +15 % |
| FP sobre casos negativos | 0 |

Se pueden relajar explícitamente pasando otro `Umbrales`, nunca en silencio.

## Evidencia

Cada corrida deja dos archivos en `docs/evidencia/`:

- `corrida_<etiqueta>.json` — todas las métricas, la especificación completa
  con la que se corrió y el entorno (versiones de Python, pandas, numpy).
- `prediccion_<etiqueta>.parquet` — la partición predicha junto a la verdad,
  una fila por registro. Permite analizar cualquier error después sin volver a
  correr el pipeline ni depender de un directorio de trabajo que la siguiente
  corrida pisa.
