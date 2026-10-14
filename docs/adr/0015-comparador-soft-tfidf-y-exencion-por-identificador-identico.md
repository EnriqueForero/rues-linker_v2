# ADR-0015 · El nombre se compara con Soft TF-IDF y un identificador válido idéntico no lo veta el piso de nombre

**Fecha:** 2026-10-14 · **Estado:** aceptada · **Fase:** 3 de la ruta (`docs/DIAGNOSTICO_Y_RUTA_2026-10-13.md`) ·
**Evidencia:** `docs/evidencia/fase3_comparador_nombre.md` y `docs/evidencia/fase3_comparador/`

## Contexto

El comparador de nombre de L3 era `token_set_ratio` con refinamientos (ADR-0010 lo normalizó, no lo cambió). Devuelve 1,0 cuando los
tokens de un nombre están contenidos en el otro: «RODRIGUEZ» dentro de «LAURA RODRIGUEZ», «CAFE S» dentro de «INVERSIONES TRIVENTA CAFE
S S». Medido sobre el banco institucional, el 100 % de los falsos positivos de L3 (3.423 pares con el LSH, 14.581 con el generador
híbrido) son de esa forma, sin NIT comparable. El bloqueo LSH tapaba el defecto porque un nombre corto y uno largo comparten pocos
shingles; cualquier bloqueo con más recall lo destapa (Fase 2). La mezcla IDF existente (Jaccard exacto de tokens) no sirve de
sustituto: «TRIVENTA» vs «TRIVENTE» vale 0 y el recall SIN_ID se hunde (0,58 → 0,38).

El piso de nombre `min_name_similarity = 0,30` se aplicaba también a los pares con el mismo NIT válido. En el banco hay 2.728 pares
verdaderos con el mismo NIT y ningún token en común (siglas: «FZA SCA» vs «FONDO ZELVORA AHORRO»). El legado los pasaba por azar
(0,31–0,43 de solapamiento de letras); un comparador honesto les da 0 y los pierde (recall CON_ID 0,977 → 0,946).

## Decisión

1. **`comparador_nombre = "soft_tfidf"`** en `produccion_estandar`: coseno de tokens ponderados por IDF del corpus, con emparejamiento
   uno a uno entre tokens distintos cuando su Jaro-Winkler ≥ **0,85** (`soft_tfidf_umbral_token`), y respaldo de cadena compactada
   **0,93** (`similitud_compacta_min`) para erratas que mueven espacios o nombres truncados. Contención ya no es identidad; una
   errata dentro de un token conserva casi toda la evidencia.
2. **`nit_identical_overrides_name_filter = True`** en `produccion_estandar`: un par cuyos dos identificadores son válidos e idénticos
   (en base canónica, ADR del DV) no lo veta el piso de nombre. Es la lógica de Fellegi–Sunter: el acuerdo en un identificador único
   pesa más que cualquier desacuerdo de nombre. La perilla existía desde v2.8.0 en el scorer y en los perfiles heredados; los perfiles
   base no la declaraban y por eso `--ajuste` la rechazaba. El veto por NIT base válido **distinto** sigue intacto: es evidencia en
   contra y ninguna regla lo levanta.
3. El legado queda disponible (`comparador_nombre = "legado"`) y es el valor de los demás perfiles base hasta que se midan.

## Lo medido (banco institucional, 30.486 registros, clústeres finales)

| configuración | P | R | macro-F1 | FP que tocan negativo | recall CON_ID | recall SIN_ID |
|---|---:|---:|---:|---:|---:|---:|
| antes (LSH + legado) | 0,9557 | 0,7927 | 0,8803 | 246 | 0,9768 | 0,5836 |
| LSH + legado + exención | 0,9553 | 0,8015 | 0,8893 | 252 | 0,9935 | 0,5836 |
| LSH + soft 0,85 + compacta 0,93 + exención | 0,9820 | 0,8178 | 0,9061 | 112 | 0,9936 | 0,6183 |
| **híbrido + soft 0,85 + compacta 0,93 + exención** (ADR-0016) | **0,9697** | **0,8319** | **0,9104** | **207** | **0,9934** | **0,6484** |

A nivel de par, soft 0,85 + compacta 0,93 domina al legado en su propio umbral: misma recall SIN_ID (0,663 vs 0,661), precisión 0,971
vs 0,919, un tercio de los falsos. Los falsos que quedan son negativos con el mismo nombre («LACTEOS DULCEVENT» vs «LACTEOS DULCEVENT
S»): no los separa ningún comparador de nombre; hacen falta otras variables (Fase 3.3).

## Consecuencias

- La huella del banco cambia (`5bfed0d1` → `969145ac`): toda comparación anterior a esta fecha es contra el comparador legado.
- L3 tarda más (6,8 s → 18,6 s sobre 1,1 M de candidatos): el IDF se construye una vez y el emparejamiento de tokens va en C++, pero
  las tablas de tokens por lote tienen costo. Sobre 4 M de registros se mide en la Fase 4.
- `min_name_similarity` ya no decide los pares con identificador idéntico: en una base real con NIT reutilizados (error de captura),
  esos pares se unen. El veto por NIT distinto y la verdad real de Enrique (Fase 1) son la red.
- Los demás perfiles base conservan el legado: cambiarlos exige medirlos con sus propias cotas.
- El puntaje sigue siendo lineal (`0,5·nombre + 0,5·nit`, umbral 0,45): un par sin NIT necesita nombre ≥ 0,90. El puntaje
  probabilístico (Fellegi–Sunter/EM) sigue pendiente en la Fase 3; esta decisión le deja un comparador calibrable.
