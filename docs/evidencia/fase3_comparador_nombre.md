# Fase 3 · Comparador de nombre Soft TF-IDF: lo medido y lo decidido

**Fecha:** 2026-10-14 · **Banco:** `benchmark_institucional.csv.gz` (30.486 registros; 46.374 pares verdaderos: 24.658 CON_ID,
21.716 SIN_ID) · **Perfil:** `produccion_estandar` · **Evidencia:** `docs/evidencia/fase3_comparador/` · **Instrumentos:**
`scripts/anatomia_fp.py` (anatomía de los pares de L3 de una corrida) y `scripts/barrido_comparador_nombre.py` (barrido de umbral a
nivel de PAR sobre los candidatos de una corrida, legado contra Soft TF-IDF y sus variantes).

## Por qué

La Fase 2 mostró que el 100 % de los falsos positivos del banco tienen `name_sim = 1,0` sin NIT comparable: el comparador heredado
(`token_set_ratio`) devuelve 1,0 cuando los tokens de un nombre están contenidos en el otro («RODRIGUEZ» ⊂ «LAURA RODRIGUEZ»). Y la
mezcla IDF existente es un Jaccard exacto: «TRIVENTA» vs «TRIVENTE» vale 0. Ningún ajuste de umbral arregla eso.

## Qué se construyó

`src/record_linkage/matching/soft_tfidf.py::similitud_soft_tfidf`: coseno de tokens ponderados por IDF (`construir_idf`, el mismo
del corpus) con emparejamiento uno a uno entre tokens distintos cuando su Jaro-Winkler supera `umbral_token` (Cohen, Ravikumar y
Fienberg, 2003). Contención ≠ identidad (lo que un lado trae y el otro no, resta) y una errata dentro de un token conserva casi toda
la evidencia. Vectorizado por lotes: tablas de tokens con `explode`, emparejamientos con `merge`, una sola llamada a `rapidfuzz` en
C++. Se elige con `comparador_nombre: "soft_tfidf"` (`"legado"` por defecto hasta esta decisión) y `soft_tfidf_umbral_token`. El
respaldo de cadena compactada (`similitud_compacta_min`, v0.18.0) sigue disponible y aquí se mide combinado. 8 + 5 pruebas
(`tests/test_soft_tfidf.py`, `tests/test_comparador_nombre.py`).

## Barrido a nivel de par (candidatos del LSH, 968.673; precisión y recall de PARES en L3)

| comparador | umbral | precisión | recall CON / SIN | FP |
|---|---:|---:|---:|---:|
| legado | 0,45 | 0,9191 | 0,9951 / 0,6609 | 3.423 |
| legado | 0,40 | 0,8168 | 0,9951 / 0,7039 | 8.931 |
| soft 0,90 | 0,45 | 0,9839 | 0,9942 / 0,5490 | 595 |
| soft 0,90 | 0,35 | 0,9127 | 0,9951 / 0,6284 | 3.654 |
| soft 0,85 | 0,45 | 0,9817 | 0,9942 / 0,5791 | 691 |
| soft 0,90 + compacta 0,95 | 0,45 | 0,9811 | 0,9942 / 0,6281 | 734 |
| **soft 0,85 + compacta 0,93** | **0,45** | **0,9708** | **0,9942 / 0,6628** | **1.170** |
| legado + compacta 0,95 | 0,45 | 0,9190 | 0,9951 / 0,6727 | 3.450 |

Soft solo (0,90) cambia recall por precisión: a precisión igualada (≈ 0,91) recupera menos SIN_ID que el legado (0,628 vs 0,661).
Lo que pierde son nombres truncados y erratas pesadas («UON COLOMBIA» vs «UNO COLOMBIA», «BOLIVAR S» vs «BOLIVAR CIAS»): el
respaldo de cadena compactada los recupera. **Soft 0,85 + compacta 0,93 domina al legado en su propio umbral:** misma recall SIN_ID
(0,663 vs 0,661), precisión 0,971 vs 0,919, un tercio de los falsos. Los falsos que quedan son negativos sintéticos con el mismo
nombre («LACTEOS DULCEVENT» vs «LACTEOS DULCEVENT S», «HIJOS VALDORIA» vs «VALDORIA HIJOS»): ningún comparador de nombre los separa;
hacen falta otras variables.

## Barrido a nivel de par (candidatos del híbrido, 1.254.470)

| comparador | umbral | precisión | recall CON / SIN | FP |
|---|---:|---:|---:|---:|
| legado | 0,45 | 0,7377 | 0,9951 / 0,7588 | 14.581 |
| soft 0,90 | 0,45 | 0,9785 | 0,9942 / 0,6069 | 830 |
| soft 0,90 + compacta 0,95 | 0,45 | 0,9707 | 0,9942 / 0,7236 | 1.213 |
| **soft 0,85 + compacta 0,93** | **0,45** | **0,9403** | **0,9942 / 0,7894** | **2.646** |
| soft 0,85 + compacta 0,93 | 0,47 | 0,9666 | 0,9940 / 0,7348 | 1.398 |

Con el híbrido, soft 0,85 + compacta 0,93 da **+13 puntos de recall SIN_ID de pares y +2 de precisión** frente al LSH con el legado
(0,789 / 0,940 contra 0,661 / 0,919): el bloqueo híbrido por fin convierte su recall en calidad.

## Banco completo (clústeres, L1–L5)

| corrida | candidatos | recall pares CON / SIN | válidos L3 | P | R | macro-F1 | FP que tocan negativo | recall SIN_ID | L2 s | L3 s | total s | huella |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `lsh_base` | 968,673 | 0.9964 / 0.7183 | 41,737 | 0.9557 | 0.7927 | **0.8803** | 246 | 0.5836 | 39.3 | 6.8 | 59.0 | `5bfed0d1` |
| `lsh_soft` | 968,673 | 0.9964 / 0.7183 | 35,116 | 0.9909 | 0.7428 | **0.8527** | 32 | 0.5167 | 39.0 | 13.9 | 66.6 | `453ef940` |
| `lsh_s090c095` | 968,673 | 0.9964 / 0.7183 | 37,334 | 0.9883 | 0.7831 | **0.8765** | 47 | 0.6004 | 38.1 | 15.1 | 65.8 | `cbf3b95e` |
| `lsh_s085c093` | 968,673 | 0.9964 / 0.7183 | 38,764 | 0.9816 | 0.7924 | **0.8809** | 108 | 0.6183 | 39.1 | 15.2 | 67.5 | `11caf5b2` |
| `h_base` | 1,254,470 | 0.9969 / 0.9469 | 55,021 | 0.8590 | 0.7062 | **0.7930** | 2859 | 0.4038 | 21.7 | 8.4 | 44.1 | `75c499a5` |
| `h_soft` | 1,254,470 | 0.9969 / 0.9469 | 36,610 | 0.9883 | 0.7545 | **0.8591** | 36 | 0.5419 | 21.4 | 19.8 | 55.0 | `d34c6237` |
| `h_s090c095` | 1,116,045 | 0.9969 / 0.9251 | 39,776 | 0.9841 | 0.7991 | **0.8842** | 68 | 0.6347 | 12.8 | 18.5 | 44.9 | `5564dcaa` |
| `h_s085c093` | 1,254,470 | 0.9969 / 0.9469 | 42,989 | 0.9688 | 0.8066 | **0.8853** | 207 | 0.6486 | 19.6 | 21.2 | 54.4 | `521c7022` |
| `lsh_legado_id` | 968,673 | 0.9964 / 0.7183 | 42,306 | 0.9553 | 0.8015 | **0.8893** | 252 | 0.5836 | 38.0 | 6.3 | 57.2 | `7d9cc9bd` |
| `lsh_s085c093_id` | 968,673 | 0.9964 / 0.7183 | 40,066 | 0.9820 | 0.8178 | **0.9061** | 112 | 0.6183 | 39.5 | 15.4 | 67.8 | `9739d0ae` |
| **`h_s085c093_id`** | 1,116,045 | 0.9969 / 0.9251 | 44,141 | **0.9697** | **0.8319** | **0.9104** | 207 | **0.6484** | 12.9 | 18.6 | 45.1 | `969145ac` |

`_id` = `nit_identical_overrides_name_filter = True`. `h_s085c093_id` corre sin variantes por borrado (ADR-0016); `h_s085c093` con ellas:
misma calidad, 138.000 candidatos más.

## Hallazgos

1. **Soft solo cambia recall por precisión; con el respaldo de cadena compactada domina.** `lsh_soft` (0,90, sin respaldo): P 0,991 y
   32 FP, pero macro-F1 0,853 y recall SIN_ID 0,517. `lsh_s085c093`: P 0,982, macro-F1 0,881 (= la base), recall SIN_ID 0,618.
2. **El piso de nombre vetaba pares con el mismo NIT.** Con cualquier comparador honesto, el recall CON_ID caía de 0,977 a 0,946:
   795 pares verdaderos con el mismo NIT válido y sin tokens en común (siglas) que el legado pasaba con 0,31–0,43 de solapamiento de
   letras. `nit_identical_overrides_name_filter = True` (ADR-0015) los recupera en los dos comparadores (CON_ID 0,9935) sin tocar los
   FP que tocan un negativo (en el banco ningún negativo comparte NIT válido; en una base real, el veto por NIT distinto sigue).
3. **Con el híbrido, el comparador convierte el recall de bloqueo en calidad:** macro-F1 0,9104 (antes 0,8803), P 0,9697 (0,9557),
   R 0,8319 (0,7927), recall SIN_ID 0,6484 (0,5836), FP que tocan un negativo 207 (246), 45 s (59 s).
4. **Lo que ningún comparador de nombre resuelve:** negativos con el mismo nombre normalizado («MORALES BOGOTA» vs «MORALES BOGOTA»,
   «HIJOS VALDORIA» vs «VALDORIA HIJOS»). Son el grueso de los 207 FP que quedan. Hacen falta otras variables (ciudad, dirección,
   teléfono: `extra_features`) y el puntaje probabilístico que las pese.

## Decisión

`produccion_estandar` pasa a `comparador_nombre = "soft_tfidf"`, `soft_tfidf_umbral_token = 0,85`, `similitud_compacta_min = 0,93`,
`nit_identical_overrides_name_filter = True` (ADR-0015) y `motor_candidatos = "hibrido"` (ADR-0016). Huella nueva del banco:
`969145ac`. Los demás perfiles base conservan el legado hasta medirse.
