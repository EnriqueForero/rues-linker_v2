# Arnés de paridad de la vinculación (F3)

Mide que el paquete `record_linkage.flujo.vinculacion` hace **lo mismo que el motor original** (la celda
`%%writefile vinculador_empresas.py` del notebook 10, versión 1.0.0). Existe porque «sin cambiar comportamiento» no
se afirma: se mide, con insumos inventados, en cuatro escenarios y en dos entornos (Python 3.10 + pandas 2.3 y
Python 3.11 + pandas 3.0).

Qué compara: las 15 tablas del Excel, `resumen`, `por_pais`, los avisos y el Excel celda por celda (valor y estilo).

## Uso (desde la raíz del repositorio)

```bash
export LIB_SRC=$PWD/src                                           # la librería a medir
python scripts/oraculo_vinculacion/extraer_oraculo.py             # 1. saca el motor original del historial de git
python scripts/oraculo_vinculacion/correr_original.py py311_pandas3   # 2. referencia (≈ 45 s); repita con el otro entorno
# 3a. híbrido: el original con SUS piezas sustituidas por las nuevas (aísla la función culpable)
python scripts/oraculo_vinculacion/hibrido.py py311_pandas3 \
    record_linkage.flujo.vinculacion.clasificar:clasificar record_linkage.flujo.vinculacion.normalizar:Normalizador
# 3b. paquete completo contra la referencia (cuando exista ejecutar / exportar_excel en el módulo)
python scripts/oraculo_vinculacion/comparar.py py311_pandas3 record_linkage.flujo.vinculacion
```

La etiqueta (`py311_pandas3`, `py310_pandas2`) es solo un nombre: use el intérprete del entorno que corresponda a cada
corrida. Salida `IDÉNTICO` / `IDÉNTICA` = paridad; si no, lista qué tabla, columna y fila difieren.
Sustitución: `modulo:nombre_nuevo[=nombre_original]` (el `=` solo si el nombre cambió; ver `hibrido.py`).

## Qué NO está en el repositorio (a propósito)

- **El motor original**: trae marcas reales de empresas en docstrings y textos; el repositorio no puede contenerlas
  (regla del proyecto, `tests/test_sin_nombres_reales.py`). `extraer_oraculo.py` lo reconstruye del commit `bab03c2`
  y verifica su SHA-256 (`b4484359…`). Queda en `.oraculo_vinculacion/` (ignorado por git).
- **`textos_declarados.json`** (en `.oraculo_vinculacion/`): cinco frases de la salida original con marcas reales que el
  paquete nuevo escribe con marcas inventadas (los «p. ej.» del `RESUMEN`, el ejemplo de RELACIÓN DE PROPIEDAD del glosario
  del `LEEME` y tres ejemplos de la guía de revisión). Formato `{"texto original": "texto nuevo"}`; se aplica a los dos lados.
  En una máquina nueva, el primer `comparar.py`/`hibrido.py` reportará esas diferencias de texto: cree el archivo con esos
  pares (el texto nuevo está en `vinculacion_excel/leeme.py` y `controles.py`/`resumen.py`).
- **Los insumos reales del piloto** (segmentación y descargas de Orbis/FDI): nunca entran al repositorio.

## Límites

Cuatro escenarios no ejercen todas las ramas (en las pruebas de mutación, 12 de 18 mutantes pasaron «IDÉNTICO» por el arnés
y los mató una prueba unitaria). La protección completa es: este arnés + las pruebas unitarias cuyos valores salen del
original + `tests/test_vinculacion_paridad.py` (huellas congeladas en `tests/lineas_base_vinculacion.py`). Un cambio declarado
del motor (`docs/VINCULACION_HALLAZGOS.md`) hace que el arnés ya no dé IDÉNTICO en lo que cambió: se declara en la línea base,
no se esconde.
