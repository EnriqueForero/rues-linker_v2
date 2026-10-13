# Protocolo de verdad real (ground truth) para deduplicación y record linkage

*Escrito el 2026-10-13 (Fase 1 de `DIAGNOSTICO_Y_RUTA_2026-10-13.md`). `scripts/medir_kappa.py` citaba este documento
desde v0.9 y no existía. Una página; si necesita más, está mal.*

## 1. Qué es y qué no es

- **Verdad real** = pares de registros de una base **real** que dos personas etiquetaron como misma empresa (1) o distinta (0).
  Es una **muestra de pares**, no una partición completa: las métricas se calculan solo sobre esos pares y llevan intervalo
  de confianza (`evaluation/verdad_real.py`).
- **No es verdad real** nada generado: `tests/data/ground_truth_grande.csv`, `golden_truth_*.csv`, el banco institucional y
  los Excel `Ground_Truth_Robusto_V3`, `Ground_Truth_Exhaustivo_RecordLinkage` y `Ground_Truth_Multicampo_v1` del Drive
  (traen la columna «EXPLICACIÓN DEL RETO» con la perturbación que los generó). Sirven para regresión, no para certificar.

## 2. Dónde viven los archivos

Fuera del repositorio, siempre (datos licenciados y nombres reales): en el Drive del proyecto, carpeta `verdad_real/`.
Al repositorio entran **solo cifras**: el JSON que escribe `scripts/medir_verdad_real.py --salida` en `docs/evidencia/`.
La guarda `tests/test_sin_nombres_reales.py` vigila.

## 3. Muestra: qué pares se etiquetan

1. Correr el motor sobre la base real (`linkage()` o `dedupe()`); guardar la correlativa y los pares puntuados.
2. `python scripts/active_labeling.py` sobre esa salida: selecciona los pares más informativos, estratificados por
   **régimen** (los dos con identificador / ninguno / mixto) y por **banda de puntaje** (unidos con holgura, unidos al borde,
   no unidos al borde, no unidos lejos). Las bandas al borde son las que enseñan; las lejanas, las que vigilan.
3. Tamaño: **3.000–5.000 pares** para certificar cifras por régimen con intervalos de ± 2 puntos; 300–500 bien elegidos ya
   dan el 80 % del valor para calibrar. Empiece por 500 y crezca.
4. Incluir a propósito: homónimos de persona, sucursales (mismo NIT raíz, nombre con ciudad), razones sociales genéricas
   («INVERSIONES», «COMERCIALIZADORA»), y pares con NIT igual y nombre distinto.

## 4. Etiquetado: dos personas, a ciegas

- Cada anotador recibe el mismo archivo sin ver la decisión del motor ni la del otro.
- Formato único (lo lee `leer_etiquetas`): columnas `ID_1`, `ID_2`, `IS_MATCH`, `ANOTADOR`. En `IS_MATCH` vale
  `1`/`0`, `SAME`/`DIFFERENT`, `MISMA`/`DISTINTA` o `SI`/`NO`; `UNCLEAR` cuando no se puede decidir (se descarta y se cuenta).
- Regla de decisión que se entrega por escrito con el archivo: «misma empresa» = la misma persona jurídica o natural
  (misma matrícula/NIT raíz aunque cambie sucursal, forma legal o grafía). Una filial, una matriz o un grupo **no** es la
  misma empresa. Si hace falta buscar en RUES o en la web para decidir, se busca.
- Tiempo esperado: 20–40 pares por hora por persona en los casos al borde.

## 5. Acuerdo y resolución

`python scripts/medir_verdad_real.py --etiquetas a.xlsx --etiquetas-b b.xlsx --correlativa ...` imprime el kappa de Cohen
(McHugh 2012, https://doi.org/10.11613/BM.2012.031) y la lista de desacuerdos:
- kappa > 0,80: la verdad es confiable;
- 0,60–0,80: aceptable; los desacuerdos se resuelven en una sesión de las dos personas (y se anota el criterio);
- < 0,60: **el manual está mal definido**, no el motor: se reescribe la regla de decisión y se vuelve a etiquetar.
El archivo final tiene una sola etiqueta por par (la resuelta) y `ANOTADOR = "resuelto"` en las que cambiaron.

## 6. Medir y publicar

```bash
python scripts/medir_verdad_real.py --etiquetas verdad_real/etiquetas_resueltas.xlsx \
    --correlativa salida/correlativa.parquet --col-nit NIT --salida docs/evidencia/verdad_real_<fecha>.json
```
Se reporta por régimen: pares, TP/FP/FN, precisión y recall con intervalo de Wilson al 95 %, F1. Las compuertas de
`docs/evidencia/compuertas.json` (sección `verdad_real`) dicen qué cifra se exige; hasta que exista la primera medición,
ninguna afirmación de calidad del repositorio vale para empresas reales.

## 7. Repetir

Cada cambio del motor que cambie huellas se vuelve a medir contra la misma verdad; la verdad crece (nunca se reetiqueta
lo ya resuelto) con los pares nuevos que el motor empieza a unir o a separar.
