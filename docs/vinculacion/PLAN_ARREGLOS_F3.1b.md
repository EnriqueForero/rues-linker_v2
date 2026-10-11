# F3.1b — plan de arreglos declarados del motor de vinculación (2026-10-11)

Se ejecuta DESPUÉS de la compuerta de paridad de F3.1 (paquete completo = huellas del motor original). Reglas:
un commit por hallazgo (prueba que falla primero → arreglo → prueba verde), cada uno con su línea en «CAMBIOS DECLARADOS» (ADR-0014 / CHANGELOG) y, si altera una tabla de los
4 escenarios, un sobrescrito en `tests/lineas_base_vinculacion.py` (`CAMBIOS_DECLARADOS[(escenario, tabla)] = (huella_nueva, motivo)` (ya existe; ver el RESUMEN, fila 16); `HUELLAS_MOTOR_ORIGINAL` NO se toca:
es el registro del oráculo). Fuente: `docs/vinculacion/HALLAZGOS_MOTOR_ORIGINAL.md` (A1–A17, B1–B10, C1–C13, D, E1–E14 y las revisiones g1–g3).

## Nivel 1 — ARREGLAR en F3.1b (alta, o media con disparador realista; arreglo pequeño y seguro)
| id | defecto | arreglo | efecto en salidas |
| --- | --- | --- | --- |
| g1-O1 (ALTA) | `id_comparable(pd.NA)` → 'NA' ⇒ falso «mismo identificador» ⇒ EMPRESA/A entre empresas sin NIF (Parquet con dtype string) | `_es_ausente` acepta pd.NA/NaT (pd.isna escalar); revisar los 4 extractores y `separar_alias` | cambia solo con Parquet/tipos anulables |
| B1 (ALTA) | rama `COMPACTO_IGUAL` de deduplicar_ancla une 'J Martinez'↔'A Martinez' y 'Kramento 2'↔'Kramento 3' sin mirar MARCAS_OK | exigir MARCAS_OK o mandar el par a pendientes | menos uniones automáticas en la ancla; verificar contra fixtures |
| g3-O6 (media, LatAm) | faltan Ltda, L.T.D.A., Lda, S. en C., E.U., EIRL en FAMILIAS_LEGALES; `LIMITED\b`/`LIMITADA` sin `\b` inicial («Unlimited»→LTD, «Ilimitada»→SL) | añadir familias y `\b` inicial | cambia uniones/pendientes de la ancla con esas formas |
| g1-O3 + g2-C1 (media) | str donde va tupla se recorre letra a letra (patrones_intermediario="CARGO" ⇒ casi toda la ancla es «agente de carga»; formas_legales_extra; alias_paises; cols_pais_extra; archivos_decisiones) | validar en `ConfigVinculacion.__post_init__` y `_catalogo`; `archivos_decisiones` str = UN archivo | solo errores nuevos claros / corrige uso accidental |
| g3-O5 (media, pandas 2↔3) | `\s` de `_clave` en pandas 3 (Arrow/RE2) no reconoce NBSP, U+2007, U+202F, U+3000, \x0b, \x85 ⇒ claves distintas entre versiones | `re` de Python o normalizar espacios Unicode antes | iguala pandas 2 y 3 con espacios Unicode |
| A2 (media) | pandas lee 'NA' (Namibia), 'N/A', 'None', 'null'… como ausente: país 'NA' llega vacío sin aviso | `keep_default_na=False, na_values=[""]` en CSV y Excel | cambia solo celdas con esos textos |
| g1-O2 (media) | floats enteros: `id_comparable(900123456.0)`→'9001234560'; `digitos_telefono(3001234567.0)`→'12345670' | convertir floats enteros a int antes de extraer | Parquet float64/Int64 con ausentes |
| A8 (media) | `_registrable`: 'tienda.zelvora.gob.es'→'gob.es' (también gob.mx, mil.co, or.jp) ⇒ falso vínculo por web | ampliar lista de segundos niveles (gob, mil, or, ne, go, ac…) | cambia dominios con esos sufijos |
| A4 (media) | `_hs6`: 'nan'→'000nan', 'inf'→OverflowError, pd.NA→'<NA>', negativo→'-00500' | tratar nan/inf/NA como vacío; negativos como llegan | cambia solo valores raros |
| B9 + D1 (baja + diferencia) | `_cubre` recursivo sin memoizar: 12×24 palabras repetidas ≈ 4,3 s; RecursionError ≥ ~990 palabras (el nuevo falla 1–2 niveles antes) | memoizar o iterar `_cubre` | ninguno (mismo resultado) |
| A1 (media, F4) | `read_bytes()[:4096]` lee el archivo entero para mirar 4 KiB | leer solo 4096 bytes | ninguno |
| A5 (baja) | FILA_EXCEL descuadrada en CSV con líneas en blanco | `skip_blank_lines=False` (las filas vacías se descartan después conservando la numeración) | FILA_EXCEL/ID_REGISTRO solo en CSV con líneas en blanco |
| A10, g1-O6 (baja) | `dominio_web(';zelvora.es')`→''; IP '192.168.1.1'→'1.1'; 'https://user:pw@zelvora.es'→'' | quitar separadores iniciales; ignorar userinfo; IP→'' | dominios raros |
| A11, A12, A3, A6, g1-O4 (baja) | 'SIN PAIS' en las sugerencias; preparar_fuente con 0 filas revienta; .xls sin xlrd; columna de transformación sin validar; columnas reservadas (FUENTE, ID_REGISTRO, FILA_EXCEL) | mensajes qué pasó / por qué / qué hacer; 0 filas → tabla vacía | solo errores |
| g3-O4, g3-O3 (baja) | `pe` 0×0 si todos los pendientes se absorben; decisión con CLAVE_A==CLAVE_B (autopar) sin aviso | filtrar con máscara booleana; avisar autopares | `pe` conserva columnas |
| B6 (baja) | docstring de `deduplicar_destino` promete «misma forma legal» y el código compara cadena original + países | corregir el docstring (o la regla, si Enrique lo pide) | ninguno |
| M-a (media) | invariante «todo vínculo A tiene todas las palabras alineadas» FALLA cuando `evidencia_extra` sube un par por identificador comparable (mismo NIF, nombre distinto) | la invariante exceptúa los pares promovidos por identificador | solo CONTROL_QA |
| M-b (baja) | COMPARACION_REVISIONES: esquema de 7 columnas vacío vs 8 lleno | un solo esquema | tabla vacía |

## Nivel 2 — CONOCIDO, diseño más delicado: se documenta y pasa a la lista de F5 (decide Enrique si sube)
- g3-O1 (media-alta): una grafía sin forma legal hace de puente y une familias incompatibles (SL ↔ S. Coop.) sin revisión.
- g3-O2 (media): DISTINTA solo quita la arista directa; con un puente la invariante de vetos da FALLA (el aviso existe; no es silencioso).
  O1 y O2 comparten mecanismo (union-find con restricciones): el resultado depende del orden de unión ⇒ requiere diseño y corpus real.
- B2 (media): `deduplicar_destino` trata las decisiones distinto que la ancla (MISMA no levanta DISTINTA previo; claves desconocidas ignoradas en silencio).
- B3 (baja-media): `alinear` con palabras repetidas mide mal la pérdida por truncamiento (index() toma la primera).
- B7 (media): `raiz_id` corta en el primer «-» (IDs con guion en la raíz se fusionan); validar el formato de `col_id`.
- B8: `misma_palabra` con max_ed=2 deja pasar sustitución+inserción en palabras cortas (MIRVANA↔MIRVAXAS) contra su docstring (¿docstring o regla?).

## Nivel 3 — PREGUNTAS A ENRIQUE / sin arreglo
- A13: promoción del alias («S.L. (Peluvika Dulces)» queda en 'SL' y el alias no pasa a principal): ¿intención?
- A9: `marcas` descarta conectores de una letra (A, D, E, I, L, V, Y) antes de buscar iniciales: es el diseño (los conectores no son marca); solo se corrigió el docstring.
- M-c: la rama REVISAR por «parecido» es inalcanzable con el umbral por defecto (tope ≈ 0,72 sin palabra compartida): ¿el umbral 0,88 es intencional?
- A7, A14, A15, B4(ver F3.4), B10, observaciones de B: cosméticos / F5.

## Pasan a F3.4 (lector único de decisiones; ver g2)
B4/O3 (archivo vacío), B5 (plantilla escribe filas como decisiones válidas), g2-O1 (DECISION_HUMANA en Excel se descarta sin aviso), g2-O2 (separador), g2-O4 (encabezados duplicados/numéricos),
g2-O5 (.xls o XLSX renombrado), g2-O6 (espacios múltiples en DECISION), C1 (str suelto), fechas en celdas XLSX.

## Pasan a F3.2
A16 + g1-O5: locale global (un Normalizador cambia el del anterior; un constructor fallido ya registró el suyo) ⇒ `registrar_locale_compuesto` al final de `__init__`.
## Pasan a F4/F5
A17 (`normalizar_nombre` recalcula sufijos en cada llamada, ~0,25 s fijos), B9 (rendimiento), `Comparador.vocab` sin uso, `rasgos` calculado antes de descartar pares sin palabras útiles.

## Cambios DECLARADOS ya aplicados / por declarar (no son arreglos de defectos)
- H1 de g1: `sha256_archivo` usa Path().open() (aplicado + prueba).
- g3-H1/H2/H3: `dec["TIPO"]` (KeyError en vez de AttributeError), `NOMBRE_NORM` leído antes del cortocircuito, `zip(strict=True)`: solo con entradas malformadas que ejecutar() no genera ⇒ declararlos como deliberados.
- print → logging/registro (B, ejecutar); assert → RuntimeError (ejecutar); `leer_tabla` acepta Path y `hoja=0`.

## Hallazgos de C, D y E (clasificación en niveles; ids en HALLAZGOS_MOTOR_ORIGINAL.md)
- **Nivel 1 (arreglar):** C1 (ALTA: `ID_COMPARABLE` igual sube un par a EMPRESA/A aunque el país y las palabras no coincidan; deja dos invariantes en FALLA; es también el M-a de arriba),
  C10 (KeyError('MISMO_PAIS') con referencia y sin candidatos), C11 (F1 = NaN con precisión o recall 0), C3 (la sigla del destino no se busca), E1 (ALTA en pandas 3: caída al exportar con una columna sin valores),
  E4/E5 (inyección de fórmula en RESUMEN y encabezados), E8 (hojas DETALLE con nombre repetido en 31 caracteres), D: etiqueta en blanco / dos destinos con la misma primera palabra (validar en `ConfigVinculacion`), D: claves_repeticion inexistentes (fallar al validar).
- **Nivel 2 (documentar, F5):** C9 (web propaga desde vínculos B/asistidos mientras la fusión exige ALTA), C13 y D (O(empresas × pares), propagar_por_web 145 s a 17 356 filas: F4), C8, C4–C7, D: columna numérica con 'ND'.
- **Nivel 3 (preguntas/cosmético):** C2, C12, E3, E6, E7, E9–E13, D: PREGUNTA con «()», ENLACES sin reset_index, CONTACTOS con 'None'.
