# Fase 2 · Generador de candidatos híbrido: lo medido y lo decidido

**Fecha:** 2026-10-14 · **Banco:** `data/benchmark/benchmark_institucional.csv.gz` (30.486 registros, 46.374 pares verdaderos:
24.658 CON_ID y 21.716 SIN_ID) · **Perfil:** `produccion_estandar` · **Evidencia:** `docs/evidencia/fase2_candidatos/`
(`corrida_<etiqueta>.json` del banco y `recall_<etiqueta>.json` de `scripts/recall_bloqueo.py`).

## Qué se construyó

`src/record_linkage/engine/candidatos/` genera pares candidatos por cuatro vías, cada una un generador de lotes `(m, 2)`:

| vía | qué propone | de dónde viene |
|---|---|---|
| `por_identificador` | mismo NIT base, vecinos y radio del perfil | reutiliza `iter_pares_por_nit` (el bloqueo por NIT del LSH) |
| `por_token` | comparten uno de sus `k` tokens más raros (IDF), con variantes de una letra borrada opcionales | `construir_idf`, `genericos()`, `pares_por_bloque` |
| `por_tfidf` | coseno TF-IDF de trigramas de caracteres ≥ umbral, `top_k` por fila, por bloques de 2.000 filas | `sklearn` + producto disperso por lotes |
| `por_llave:<col>` | igualdad exacta de una columna del perfil (`llaves_bloqueo`) | `pares_por_bloque` |

La política de fuentes (`matriz_politica_fuentes`) es la misma del LSH: `cross_source_only` y fuentes confiables vetan pares dentro de
la misma fuente. `escribir_candidatos_sqlite` deja la misma tabla `candidate_pairs` que el LSH, así que L3–L5 no cambian. Se elige con
`motor_candidatos: "hibrido"` en el perfil (por defecto `"lsh"`); `candidatos_hibrido.*` ajusta cada vía. `scripts/recall_bloqueo.py`
mide el recall de PARES del bloqueo contra la verdad, por régimen. 14 pruebas en `tests/test_candidatos_hibrido.py`.

## Lo medido

| corrida | candidatos | recall pares CON / SIN | válidos L3 | P | R | macro-F1 | FP que tocan negativo | recall SIN_ID | L2 s | L3 s | total s | huella |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `lsh_base` | 968,673 | 0.9964 / 0.7183 | 41,737 | 0.9557 | 0.7927 | **0.8803** | 246 | 0.5836 | 39.3 | 6.8 | 59.0 | `5bfed0d1` |
| `h_base` | 1,254,470 | 0.9969 / 0.9469 | 55,021 | 0.8590 | 0.7062 | **0.7930** | 2859 | 0.4038 | 21.7 | 8.4 | 44.1 | `75c499a5` |
| `h_solo_tokens` | 637,238 | 0.9967 / 0.8043 | 43,721 | 0.9507 | 0.7642 | **0.8586** | 285 | 0.5235 | 11.6 | 4.5 | 29.5 | `53620d1a` |
| `h_solo_tfidf` | 813,393 | 0.9968 / 0.9206 | 54,465 | 0.8583 | 0.7119 | **0.7973** | 2859 | 0.4161 | 11.2 | 6.0 | 30.1 | `70378852` |
| `h_sin_borrados` | 1,116,045 | 0.9969 / 0.9251 | 54,974 | 0.8590 | 0.7062 | **0.7930** | 2859 | 0.4038 | 13.2 | 7.4 | 34.0 | `75c499a5` |
| `h_tfidf05` | 916,664 | 0.9968 / 0.9221 | 50,793 | 0.9689 | 0.7357 | **0.8437** | 370 | 0.4657 | 20.2 | 6.3 | 40.3 | `5d5f7bac` |
| `h_idf05` | 1,254,470 | 0.9969 / 0.9469 | 32,070 | 0.9944 | 0.6810 | **0.8208** | 30 | 0.3813 | 20.4 | 8.3 | 39.5 | `a9c04e6d` |

`lsh_base` reproduce la huella de la revisión del 13 de octubre (`5bfed0d1`) tras los cambios de L3 de esta iteración (paginación por
clave y escritura vectorizada): no cambian ningún resultado. `h_base` = NIT + tokens (k=2, con borrados) + TF-IDF (0,4 / top 50).
`h_tfidf05` = TF-IDF 0,5 / top 30. `h_idf05` = `h_base` con `idf_weight_blend` 0,5 en los dos regímenes. Tiempos con la máquina
compartida con otras corridas: comparables entre sí, no con la revisión.

## Hallazgos

1. **El bloqueo híbrido cumple el objetivo de recall de pares.** SIN_ID pasa de 0,718 (LSH) a 0,947 (objetivo de `compuertas.json`:
   0,95) con menos tiempo de L2 (39 s → 22 s) y 41 candidatos por registro. TF-IDF de trigramas es la vía que más cubre (41.184 de
   46.374 pares verdaderos; 1.590 que ninguna otra vía propone); los tokens distintivos cubren 23.935 (69 exclusivos); el NIT 24.538.

2. **Pero la calidad final empeora: macro-F1 0,880 → 0,793 y los FP que tocan un negativo se multiplican por 12 (246 → 2.859).**
   Y el recall final SIN_ID baja (0,584 → 0,404) aunque el recall de pares de L3 sube (0,661 → 0,759): los falsos positivos funden
   grupos y la cobertura por estrellas de L5 (0,80) los rechaza enteros. El daño ocurre aguas abajo del bloqueo.

3. **Anatomía de los falsos positivos (`scratchpad/analizar_fp.py` sobre los `scored.db`): el 100 % tiene `name_sim = 1,0` y
   ningún NIT comparable.** Son pares de contención: «RODRIGUEZ» dentro de «LAURA RODRIGUEZ», «CAFE S» dentro de «INVERSIONES FENIX
   CAFE S S», «BANCO ZELVORA BOGOTA» dentro de «C1AL IN7ER BANCO Z3LVORA BOGOTA». `token_set_ratio` devuelve 100 cuando un conjunto de tokens
   está contenido en el otro, y con `score = 0,5·nombre + 0,5·nit` eso basta para pasar el umbral 0,45 sin NIT. Con el LSH esos pares
   también se unen mal (3.423 FP de pares en L3), pero el MinHash propone pocos porque un nombre corto y uno largo comparten pocos
   shingles: **la selectividad del LSH tapaba el defecto del comparador** (H4 del diagnóstico). El híbrido lo destapa: de sus 14.581
   FP de pares, 14.094 vienen de TF-IDF (9.710 solo de ahí) y 4.871 de tokens (487 solo de ahí); el NIT no produce ninguno.

4. **Las variantes por borrado no aportan.** `h_sin_borrados` da la misma huella que `h_base` con 138.000 candidatos menos y 8 s menos
   de L2: los trigramas de TF-IDF ya cubren las erratas de una letra. Quedan apagadas por defecto (`tokens_borrados = False`).

5. **La mezcla IDF (Jaccard exacto) no es la solución:** `h_idf05` sube la precisión a 0,994 pero hunde el recall SIN_ID a 0,381
   porque un token con errata no cuenta nada. Subir el umbral de TF-IDF (`h_tfidf05`) recorta candidatos sin resolver el defecto.

6. **Un hallazgo de instrumento:** `--ajuste a=1 --ajuste b=2` dejaba solo `b=2` (`nargs="*"` sin `action="extend"`). Las primeras
   corridas de esta fase midieron el LSH creyendo medir el híbrido; se detectó porque el JSON guardado solo traía un ajuste. Arreglado
   con prueba en `banco.py` y `escala.py`.

## Decisión

* `motor_candidatos` sigue en `"lsh"` por defecto hasta que el comparador de nombre deje de dar 1,0 a la contención: con el comparador
  de hoy, más recall de bloqueo es más daño. Ese comparador es la Fase 3 (`docs/evidencia/fase3_comparador_nombre.md`), que se mide
  con los dos motores.
* La compuerta del bloqueo (`compuertas.json`) sube su piso a lo que el híbrido ya cumple cuando el híbrido sea el motor por defecto;
  mientras tanto el piso sigue siendo el del LSH.
