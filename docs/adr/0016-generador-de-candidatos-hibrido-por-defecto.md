# ADR-0016 · El generador de candidatos por defecto es el híbrido (NIT + tokens distintivos + TF-IDF de trigramas)

**Fecha:** 2026-10-14 · **Estado:** aceptada · **Fase:** 2 de la ruta · **Evidencia:** `docs/evidencia/fase2_candidatos_hibridos.md`,
`docs/evidencia/fase3_comparador_nombre.md`

## Contexto

El bloqueo MinHash-LSH pierde el 28 % de los pares verdaderos sin identificador (recall de pares SIN_ID 0,718; H3 del diagnóstico).
El generador híbrido (`engine/candidatos/`) sube ese recall a 0,925–0,947 con menos tiempo de L2 (39 s → 13–22 s en el banco). Con el
comparador legado, ese recall se convertía en daño (macro-F1 0,880 → 0,793) porque el comparador daba 1,0 a la contención; con el
comparador de ADR-0015 se convierte en calidad.

## Decisión

- **`motor_candidatos = "hibrido"`** en `produccion_estandar`, con las vías NIT (vecinos y radio del perfil), tokens distintivos
  (`k = 2`, cubetas ≤ 300) y TF-IDF de trigramas (coseno ≥ 0,4, `top_k` 50, lotes de 2.000 filas) y las llaves exactas que el perfil
  declare. **Las variantes por borrado quedan apagadas** (`tokens_borrados = False`): 138.000 candidatos y 8 s de L2 más sin cambiar
  un solo resultado.
- El LSH sigue disponible (`motor_candidatos = "lsh"`): para quien prefiera la precisión del LSH + soft (0,982, 112 FP) a la recall
  del híbrido + soft (0,970, 207 FP, +3 puntos de recall SIN_ID), es un `--ajuste`.
- La política de fuentes (confiables, `cross_source_only`) y el formato de `candidate_pairs` no cambian: L3–L5 no saben qué motor corrió.

## Lo medido (banco, clústeres finales, comparador de ADR-0015 en los dos)

| motor | candidatos | recall pares SIN_ID | P | R | macro-F1 | FP que tocan negativo | recall SIN_ID | L2 | total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LSH | 968.673 | 0,718 | 0,9820 | 0,8178 | 0,9061 | 112 | 0,6183 | 39,5 s | 67,8 s |
| **híbrido** | 1.116.045 | 0,925 | 0,9697 | 0,8319 | **0,9104** | 207 | 0,6484 | 12,9 s | 45,1 s |

## Consecuencias

- La compuerta del bloqueo (`compuertas.json`) sube su piso a lo que el híbrido cumple (SIN_ID ≥ 0,92).
- TF-IDF de trigramas guarda la matriz dispersa del corpus en memoria: sobre 4 M de nombres son ~1,5 GB; `tfidf_max_filas` (1,5 M)
  apaga la vía por encima y la Fase 4 debe medir y, si hace falta, partirla por bloques de filas con índice en disco.
- `scripts/recall_bloqueo.py` es la medición de referencia del bloqueo; vive junto al banco.
