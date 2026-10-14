# Hallazgos del motor original de vinculación (F3)

Al portar el motor del notebook 10 (`vinculador_empresas` 1.0.0) a `record_linkage.flujo.vinculacion` se midió, módulo por módulo y
con revisión independiente, qué hace de más o de menos. **En el hito F3.1 el paquete conservó cada uno de estos comportamientos a propósito** (la regla era paridad:
tablas y Excel idénticos al original; ver `scripts/oraculo_vinculacion/` y `tests/test_vinculacion_paridad.py`) y los marcó en el código con `# Hallazgo F3:`.

**Estado desde F3.1b (2026-10-13):** el nivel 1 del plan de arreglos y cinco del nivel 2 ya están corregidos, cada uno con su prueba y su declaración (qué cambia y qué tablas de los escenarios
toca: `docs/vinculacion/PLAN_ARREGLOS_F3.1b.md`, sección «Estado»). Las tablas de abajo describen el defecto **tal como estaba en el original**; el comentario `# Hallazgo F3:` sigue en el código
solo donde el hallazgo no se ha corregido. Corregidos: A1–A6, A8, A10–A12, B1–B3, B6, B8, B9, C1, C3, C10, C11, D (etiquetas, claves de repetición, columna `ND` no), E1, E2, E4–E6, E8, g1-O1…O4, g1-O6, g2-C1,
g3-O1…O6, M-a, M-b. Pendientes: ver el plan.

Cómo leer los identificadores: A1…A17 (lectura/normalización), B1…B10 (comparar/decisiones/deduplicar), C1…C13 (candidatos…validación),
D (tablas; sin número, por línea del original), E1…E14 (Excel), g1/g2/g3 = defectos y diferencias que halló la revisión independiente
(`g1-O1`, `g2-O1`, `g3-O1`…). «Línea» = línea del archivo del motor original (se reconstruye con `extraer_oraculo.py`).

Revisión independiente hecha: lectura+normalización (g1), comparar+decisiones (g2) y deduplicación (g3). **Pendiente**: candidatos+clasificar,
vínculos+web+validación, calidad+controles+resumen+ejecutar y tablas (encargo listo en `REVISION_EQUIVALENCIA.md`); sus pruebas unitarias
salen del original y el arnés da IDÉNTICO, pero el análisis de mutación mostró que los 4 escenarios no ejercen todas las ramas.

---

# Parte A · lectura y normalización

## Hallazgos del motor original (lectura y normalización) — agente F3.1-A, integrados en f3_int c7c2cc5

Todos verificados ejecutando el original (`vinculador_empresas.py` del oráculo). El paquete los CONSERVA (paridad) y los marca
con `# Hallazgo F3:` junto al código; 12 pruebas `hallazgo_f3` en tests/test_vinculacion_{lectura,normalizar}.py fijan el
comportamiento defectuoso y se actualizan al corregir cada uno (F3.1b: un commit por hallazgo, con prueba y declaración).

| # | dónde (original) | hallazgo | gravedad / propuesta |
|---|---|---|---|
| A1 | L272 `read_bytes()[:4096]` | lee el archivo entero para mirar 4 KiB | media (F4: bases de millones) → leer solo el bloque |
| A2 | L268/276/278 | pandas convierte 'NA' (Namibia), 'N/A', 'None', 'null', 'NaN', 'n/a' en NaN: un país 'NA' llega vacío sin aviso | media → `keep_default_na=False, na_values=[""]` (cambio declarado) |
| A3 | L267 `.xls` | se acepta pero xlrd no es dependencia: ImportError ajeno al contrato | baja → rechazar con mensaje accionable o declarar xlrd |
| A4 | L284-295 `_hs6` | 'nan'→'000nan'; 'inf'→OverflowError; pd.NA→'<NA>'; negativo→'-00500' | media (Parquet con tipos anulables) |
| A5 | L313 FILA_EXCEL | índice+2: en CSV con líneas en blanco no coincide con la línea real; ID_REGISTRO hereda el desfase | baja |
| A6 | L319 | columna de la transformación no se valida contra el encabezado: KeyError pelado | baja |
| A7 | L307+ | errores de contrato con KeyError (str() muestra \n escapados) | baja (F5 excepciones) |
| A8 | L363 `_registrable` | lista corta de segundos niveles: tienda.zelvora.gob.es → gob.es (gob.mx, mil.co, or.jp): dos sitios distintos bajo el mismo gob.xx darían el mismo dominio y podrían propagar un vínculo por web | media (falso vínculo por web) |
| A9 | L474 `marcas` | conectores de una letra (A, D, E, I, L, V, Y) se descartan antes de buscar iniciales: 'A ORBELIA' y 'ORBELIA' tienen las mismas marcas | media (puede dar EMPRESA de más); el ejemplo del docstring original era engañoso |
| A10 | L378 `dominio_web` | celda que empieza con separador: primer fragmento '' → pierde el dominio (';zelvora.es' → '') | baja |
| A11 | L487/491-497 `paises` | el marcador 'SIN PAIS' se cuela en las sugerencias y en los ejemplos del mensaje de error | baja (cosmético) |
| A12 | L512-513 `preparar_fuente` | con 0 filas np.array([]) es float64 y `&` lanza TypeError (fuente de solo encabezado revienta sin mensaje accionable) | baja |
| A13 | L515 | promoción del alias: el comentario dice «sin nombre utilizable» pero la condición es NOMBRE_NORM == "": 'S.L. (Peluvika Dulces)' queda en 'SL' y el alias no pasa a principal | baja, A CONFIRMAR si es intención |
| A14 | L354/L424 | la frase 'DE CV' entra entera y queda 'DECV' (inofensivo: 'CV' está aparte); 'CO' en la lista de exclusión ya está en las de la librería | cosmético |
| A15 | L369 `_num` | replace redundante con errors="coerce" | cosmético |
| A16 | L419 locale global | un segundo Normalizador con otras formas_legales_extra cambia en silencio el locale del primero | media → F3.2 (registrar_locale_compuesto) |
| A17 | fuera del motor, matching/normalizadores.py `normalizar_nombre` | recalcula las normas de sufijos en cada llamada: ~0,25 s fijos por llamada | F4/F5 (rendimiento) |

Cambios mecánicos sin efecto (declarados por A): segundos niveles en frozenset de módulo; `set([x]) - {""}` → `{x} - {""}`;
columnas opcionales leídas una vez; `leer_tabla` acepta Path y `hoja=0` por omisión; `round(v)` en vez de `int(round(v))` (RUF046).
Marcas reales de docstrings/pruebas sustituidas por inventadas. Limitación de insumos: 12 de 18 mutantes del arnés pasaron
IDÉNTICO (los insumos inventados no ejercen esas ramas): la protección real son las pruebas unitarias y la comparación diferencial
(diferencial aleatorio original contra nuevo: 25 127 comparaciones, 0 diferencias).

### Cambio DECLARADO por la revisión Opus g1 (H1) — aplicado
`sha256_archivo`: `open(ruta, "rb")` → `Path(ruta).open("rb")` (lint PTH123). Con ruta de fuente con barra final el original reventaba AL FINAL de ejecutar() con
NotADirectoryError (leer_tabla ya la leía bien con Path); ahora la huella se calcula. Prueba: test_sha256_archivo_acepta_la_ruta_con_barra_final_como_leer_tabla.

---

# Parte B · comparación, decisiones y deduplicación

## Hallazgos del motor original (comparar, decisiones, deduplicar) — agente F3.1-B, integrados en f3_int 9a3034a

Verificados ejecutando el original. El paquete los CONSERVA (paridad) y los marca con `# Hallazgo F3:`; las pruebas fijan el
comportamiento y se actualizan al corregir cada uno (F3.1b: un commit por hallazgo, con prueba y declaración).

| # | dónde (original) | hallazgo | gravedad / propuesta |
|---|---|---|---|
| B1 | L974-975 `deduplicar_ancla`, rama `COMPACTO_IGUAL` | une SOLA («mismas letras, otros espacios») grafías que difieren en una inicial o un número: 'J Martinez' ↔ 'A Martinez', 'Kramento 2' ↔ 'Kramento 3'. `COMPACTO_IGUAL` ignora dígitos e iniciales y la rama NO consulta `MARCAS_OK` (sí lo hacen la rama de errata L976 y `clasificar`) | **ALTA, la más importante**: duplicados internos falsos en la ancla → exigir MARCAS_OK o mandar el par a pendientes |
| B2 | L1055-1061 vs L1004-1013 | `deduplicar_destino` trata las decisiones distinto que la ancla: MISMA_ENTIDAD no levanta un DISTINTA previo (DISTINTA y luego MISMA deja el veto; en la ancla manda la última) y las claves desconocidas se ignoran en silencio (en la ancla van a attrs["desconocidas"] y a avisos) | media → simetría |
| B3 | L616-622 `alinear` | `index()` toma la primera aparición; con palabras repetidas la pérdida por truncamiento se mide mal: alinear(['SAN','PEDRO'],['SAN','PEDRO','SAN'],1,True) da sobra_b=['SAN'] (debería ser []) | baja-media → medir por posición |
| B4 | L852 `leer_decisiones` | `splitlines()[0]` lanza IndexError con un CSV de 0 bytes | baja → error accionable o tabla vacía |
| B5 | L889-895 `plantilla_decisiones` | el docstring dice «CSV vacío … ejemplo comentadas» pero escribe 2 filas como decisiones válidas (con nombres/ID reales, ya cambiados); si no se borran, entran como decisiones | media → dejarlas comentadas o fuera del archivo |
| B6 | L1028-1031 vs L1042-1051 `deduplicar_destino` | el docstring promete «nombre normalizado … y misma forma legal»; el código compara la cadena original (casefold y espacios) más los países: 'Triventa SA' ↔ 'Triventa S.A.' no se unen y FORMA_LEGAL solo se informa | media → corregir docstring o regla |
| B7 | L906 `_raiz_id` | corta en el primer «-»: un ID con guion en la raíz ('FDI-F0002') queda en 'FDI' y se fusiona todo; solo es correcto para BvD ID con sufijo de sucursal | media → validar el formato de col_id |
| B8 | L577 `misma_palabra` | con max_ed=2, sustitución más inserción pasa en palabras cortas: MIRVANA ↔ MIRVAXAS = True, contra su docstring | media → aplicar la regla de sustitución por tipo de edición |
| B9 | L645-660 `cubre` | recursión sin memoización: 12 contra 24 palabras repetidas ≈ 4,3 s (×16-19 por cada dos palabras más) | baja (rendimiento; F4) → memoizar |
| B10 | L994-996 | `rango < vistos[clave][0]` nunca se cumple; el desempate real es «gana la primera superficie»; código muerto conservado | informativo |

Observaciones sin comentario en el código: L244 (hoy config.py) `archivos_decisiones="d.csv"` (str suelto) se itera como caracteres ('d','.','c','s','v'):
CORREGIDO por la revisión Opus g2 (C1): avisa UNA vez por 'd' y luego IsADirectoryError ('.'); con ruta absoluta falla en '/'; un nombre sin punto da 10 avisos y 0 decisiones
→ ConfigVinculacion debe aceptar str/Path suelto como UN archivo y leer_decisiones usar is_file() (F3.4); L694 `Comparador.vocab` nunca se lee; L964-966 `rasgos` se calcula antes
de descartar pares sin palabras útiles (trabajo de más); L717-718/745-746 `rasgos` devuelve el mismo dict que guarda la caché (nadie lo muta hoy);
L837 `pd.ExcelFile(p)` no se cierra; L862 KeyError multilínea se muestra con \n literales.

Cambios deliberados respecto del original: el `print` de «archivo de decisiones no encontrado» pasó a `logger.warning`; 3 pruebas de B fijan eso
y la plantilla. `tests/test_escritor.py`: entrada `flujo/vinculacion/decisiones.py` en ESCRITORES_PERMITIDOS (plantilla_decisiones escribe un CSV).
Evidencia de B: 307 casos (100 % líneas y ramas), 228 mutantes/204 muertos (24 vivos equivalentes), fuzz diferencial 0 diferencias en >20 corridas,
1500 filas con igual resultado y tiempo (17,3 s original, 16,8 s nuevo), determinismo PYTHONHASHSEED 1-4.

---

# Parte C y D · candidatos, clasificación, vínculos, web, validación y tablas

## Hallazgos del motor original — agentes F3.1-C (candidatos, clasificar, vínculos, web, validación) y F3.1-D (tablas)
Integrados en f3_int (merges a7b8886 y fcdc6ae). El paquete los CONSERVA (paridad) y los marca `# Hallazgo F3:`.

### C (informe final; commit 40f22aa)
| # | dónde (original) | hallazgo | gravedad / propuesta |
| --- | --- | --- | --- |
| C1 | L1266-1268 `evidencia_extra` | un `ID_COMPARABLE` igual sube CUALQUIER par a EMPRESA/A sin mirar palabras, país ni MOTIVO. Mínimo con `ejecutar`: ancla «Zelvora Alimentos SA» (España, NIF «B-1234 5678») y destino «ZELVORA INDUSTRIAS GMBH» (Alemania, VAT «B12345678») ⇒ EMPRESA/A con MISMO_PAIS=NO, MOTIVO «Comparte la marca ZELVORA pero está en otro país…» y EVIDENCIA_EXTRA «mismo identificador». Dos invariantes quedan en FALLA («Ningún vínculo EMPRESA automático cruza países», «Todo vínculo automático A tiene todas las palabras alineadas») | **ALTA**. Subir solo si ya comparte palabra y el país coincide (o dejarlo en B) y reescribir el MOTIVO |
| C2 | L1098-1129 `generar_candidatos` | `cfg` no se usa y el piso Jaro-Winkler 0,85 está escrito en el código; bajar `umbral_parecido` no amplía los candidatos (ese umbral mide `SCORE_RUES_LINKER`) | baja: quitar el parámetro o leer el piso de `cfg` |
| C3 | L1139-1145 | la vía de siglas solo busca la sigla del lado de la ancla: ancla «Kronos Empresarial Valdoria» y destino «KEV» ⇒ 0 pares (al revés da REVISAR/C). `Comparador.rasgos` sí reconoce la abreviatura en los dos sentidos | **media** (se pierde recall): buscar también la sigla del destino |
| C4 | L1250-1265 `evidencia_extra` | el docstring dice que los valores distintos «se informan», pero solo se anotan los iguales; notas con género equivocado («mismo web», «mismo ciudad») | baja (texto; cambia EVIDENCIA_EXTRA) |
| C5 | L1283 `_agregar_por_entidad` | con `pares` vacío la tabla no trae `GRAFIA_ANCLA`; con pares sí (esquema dependiente de los datos) | baja: agregar la columna vacía |
| C6 | L1344-1345 `aplicar_decisiones` | la lista `base` a descartar no incluye NIVEL_FINAL, ESTADO, CONFIANZA ni ORIGEN: al reaplicar cambia el orden de las columnas (los valores coinciden) | baja |
| C7 | L1315 `_mapear_decisiones` | construye una `Serie` de pandas por cada decisión | baja (rendimiento con muchas decisiones) |
| C8 | L1372-1379 | ORIGEN «agregado por revisión» para un candidato que el motor no vinculó y una persona RECHAZA con NO_VINCULAR (no se agregó nada; ESTADO sí dice «DESCARTADO (revisión)») | media→baja: añadir rama «candidato, descartado por revisión» |
| C9 | L1435 `propagar_por_web` (vs L1392) | propaga desde CUALQUIER vínculo EMPRESA (también automático B y asistido sin validar, confianza MEDIA); `fusionar_por_empresa_comun` exige ALTA ⇒ un vínculo dudoso arrastra a todo su dominio | **media**: exigir ALTA como la fusión (cambia tablas) o declararlo |
| C10 | L1542 y 1564 `validar_contra_referencia` | sin ningún par candidato `L_auto` no trae `MISMO_PAIS`: con referencia `ejecutar` termina con `KeyError('MISMO_PAIS')` (reproducido); sin referencia termina bien | **media** (caída en caso raro): crear la columna vacía o `.get`, métricas en cero |
| C11 | L1556 | F1 = NaN cuando la precisión o el recall valen 0 (`prec and rec` es falso) | media (métrica): dar 0,0 si ambas están definidas |
| C12 | L1532 vs L1547 | el docstring dice «Universo: pares del mismo país» pero se evalúan todos los vínculos automáticos A/B; los de otro país cuentan como falsos positivos salvo que la referencia los incluya | baja (documentación) |
| C13 | L1503 `propagar_por_web` | llama a `Comparador.puntajes` una vez por fila nueva: con 276 grafías y 750 registros que comparten dominios crea 17 356 filas y tarda ≈ 145 s de CPU (igual en el original y en el nuevo) | baja/media (rendimiento, F4): puntuar por lotes |
Observación: `clasificar(nz)` no usa `nz`. Cambios mecánicos sin efecto declarados por C: marcas reales → inventadas (Vantiko en `propagar_por_web`), `zip(..., strict=False)` explícito (B905; con `strict=True` el fuzz con entradas mal formadas daba 13 diferencias contra el original),
`posicion_columna`/`cast` por mypy, `clave_texto` en lugar de `_clave`. Evidencia de C: 216 casos (100 % líneas y ramas), 187 mutantes (173 evaluables, 173 muertos por pruebas; 14 equivalentes; 69 sobreviven al arnés pero mueren a pruebas unitarias),
fuzz diferencial 0 diferencias (cadena, por función, candidatos, bordes y 16 353 variantes mal formadas), determinismo PYTHONHASHSEED 1–4, rendimiento igual al original (147,9 s vs 144,1 s de CPU en un caso con 17 356 filas web).
Límites de C: suite completa no corrida; Python 3.12 no verificado (sin entorno).

### D (informe 005c48e)
| línea original | qué hace | qué debería hacer | gravedad |
| --- | --- | --- | --- |
| 1619 `_unir` | solo None y NaN float cuentan como vacío; pd.NA/NaT entran como '<NA>'/'NaT' | tratar todo ausente de pandas como vacío | baja |
| 1758-1761 `_info_destino` | con ≥ 80 % de números la columna pasa a numérica y 'ND', '1,5 M', '1,5' se vuelven NaN sin aviso | conservar texto o avisar | media |
| 1805-1810 | un par que solo viene de una decisión se compara con el nombre normalizado de la grafía REPRESENTANTE (no la de la decisión) y con trunc_a=False aunque esté truncada | usar la grafía del par y su marca de truncado | media-baja |
| 1922 y 2108 | una clave de `claves_repeticion` que no es columna se ignora sin aviso (errata 'HS' por 'HS6'): la cifra «sin repetidos» deduplica solo por empresa y valor | fallar al validar la fuente | media |
| 1927 | `sort_values("N_FILAS")` con quicksort: el orden de GRAFIAS empatadas depende de numpy | kind="stable" | baja |
| 1931 y 2105 | prefijo `d.etiqueta.split()[0].upper()`: dos destinos con la misma primera palabra se pisan en EXPORTADORES sin aviso; etiqueta en blanco da IndexError al final de la corrida sin nombrar el destino | validar etiquetas en la configuración | media |
| 1945 | el contexto que choca con una columna base se busca como nom o nom_clave en la tabla ya completa: se muestra la columna base | buscar la columna renombrada | baja-media |
| 1876-1879 | tres filtros completos por empresa (g, vinc, L): O(empresas × pares) | un groupby | baja hoy, media a escala (F4) |
| 1979-1984 | ENLACES conserva el índice de los pares (sin reset_index) | reiniciar el índice como las demás hojas | baja |
| 2018 | la PREGUNTA sin país queda «()» | omitir el paréntesis | baja |
| 2068 | REVISION saca sus columnas de las claves de las filas: sin vínculos 19 columnas, sin filas ninguna | definir las 29 columnas | baja-media |
| 2085 | CONTACTOS descarta 'nan' pero no 'None' (EXPORTADORES sí) | descartar ambos | baja |
| 2110 | DETALLE_EXPORTADORES nombra `<COLUMNA.upper()>_REPETIDO`; EXPORTADORES usa el nombre legible (VALOR_USD_SIN_REPETIDOS) | un solo criterio | baja |
Observaciones de D fuera de su rango: `comparar_fuentes_decision` (controles.py, port del original) devuelve un DataFrame sin columnas cuando hay ≥ 2 archivos de decisiones y ningún par repetido
(con menos de 2 archivos sí trae las 7) — es el hallazgo M-b del plan; con pandas 3, `exportar_excel` falló en una variante del fuzz de D con columnas totalmente NaN (NO reanalizado: verificar si ocurre en el original y en el nuevo).
Evidencia de D: 210 casos (100 % líneas y ramas en 8 módulos), 121 mutantes (116 muertos por pruebas, 76 por el arnés; 40 sobreviven al arnés pero mueren a pruebas unitarias; 5 equivalentes), fuzz diferencial
513 corridas 0 diferencias (py311), fixture parquet `tests/data_sintetica/vinculacion_tablas/` (19 parquet + estado.json, 268 KB, empresas inventadas; su generación está documentada en `tests/apoyo_tablas_vinculacion.py`).
Límite declarado por D: la suite completa del repo no se corrió; `tests/test_contrato_salida.py::test_id_entidad_nit_y_ent[cruce_disco]` falló UNA vez en una corrida -x con la máquina cargada
(`'NIT-80020000' != 'NIT-800200007'`) y pasa suelto — posible flake (comprobar; ver nota del coordinador en docs/REANUDAR.md).

---

# Parte E · Excel de entrega

## Hallazgos del motor original (Excel de entrega: `exportar_excel`) — agente F3.1-E, integrado en f3_int (05636b0 / f4c54b4)
Verificados ejecutando el original. El paquete `flujo/vinculacion_excel` los CONSERVA (paridad celda por celda) y los marca `# Hallazgo F3:`.

| # | dónde (original) | hallazgo | gravedad / propuesta |
| --- | --- | --- | --- |
| E1 | L2649/2652 `formatear` | `int(muestra[c].str.len().quantile(0.9))` lanza ValueError «cannot convert float NaN to integer» con **pandas 3** cuando una columna no tiene ningún valor en las primeras 300 filas (EXPORTADORES con una sola fila, o una columna numérica toda NaN). Con pandas 2 no falla porque `astype(str)` da 'None'/'nan'. El ancho de columnas con ausentes también depende de la versión. El `else 0` de L2652 es inalcanzable | **ALTA en pandas 3** (caída real al exportar): ignorar los ausentes |
| E2 | L2905 | `EXP[c].astype(str).nunique() <= 1` decide ocultar columnas constantes y depende de la versión: una métrica con un valor y 5 ausentes se oculta con pandas 3 y no con pandas 2 | media: contar solo no ausentes |
| E3 | L2837/2840/2844 | usa `4 + k` como fila: supone que `res.resumen` tiene índice 0..n-1 | baja |
| E4 | L2870-2880 | las advertencias del RESUMEN no pasan por `_escribir_tabla`: FUENTE="=1+1" queda como fórmula; un carácter de control en HALLAZGO lanza IllegalCharacterError | media (inyección de fórmula / caída): pasar por el escritor seguro |
| E5 | L2595 | los encabezados se escriben con `str(c)` sin limpiar: «=MOTIVO» queda como fórmula | media (inyección de fórmula) |
| E6 | L2887-2893 | cualquier texto con punto y sin espacios se vuelve hipervínculo ('N.A.', '1.5', 'x@y.com' → https://…); `or c == 'CONTACTO_WEB'` sobra | baja-media |
| E7 | L2722 vs L2932-2933 | el paso 3 del LEEME dice ARCHIVO_DECISIONES y el comentario de REVISION, ARCHIVOS_DECISIONES; el campo es `archivos_decisiones` | baja (texto) |
| E8 | L2695 | dos `DETALLE_<clave>` iguales en sus 31 primeros caracteres: openpyxl renombra el segundo a 32 caracteres (UserWarning) y Excel puede no abrirlo | media |
| E9 | L2625 | el sello `_AAAA-MM-DD_HHMM` llega al minuto: dos exportaciones del mismo minuto pisan el mismo archivo | baja-media |
| E10 | L2534 | el LEEME lista «VALIDACION*» pero la hoja se llama VALIDACION; el asterisco no se explica | baja (texto) |
| E11 | L2918/2936 | `hoja_df` tolera df=None, pero REVISION y DECISIONES hacen len(None) → TypeError; ENLACES, EXPORTADORES y CONTROL_QA con None → AttributeError | baja |
| E12 | L2683 | con una tabla sin filas el autofiltro cubre una fila de más (REVISION vacía: A1:I2 con datos solo en la fila 1) | baja |
| E13 | — | `hoja_df` no protege el tope de 1.048.576 filas: openpyxl lanza un ValueError sin nombrar la hoja | baja (F4: bases grandes) |
| E14 | L2548, L2544, L2565, L2572, L2785/2789 | nombres reales en textos del Excel: el ejemplo de RELACIÓN DE PROPIEDAD del glosario (dos empresas reales) (YA CAMBIADO por marcas inventadas: commit 9a1780f, cambio declarado), y nombres de productos de datos (fDi Markets, BvD ID, Orbis; se conservan: no son empresas del universo) y «M. Pérez» (nombre genérico de persona en el ejemplo) | resuelto / informativo |

Notas de E: `tests/test_escritor.py` ganó la entrada `flujo/vinculacion_excel/exportador.py` en ESCRITORES_PERMITIDOS (única llamada a Workbook del subpaquete); `exportar_vinculacion_excel` se re-exporta de forma perezosa
desde `flujo/vinculacion/__init__.py` (openpyxl se importa al cargar el subpaquete). Evidencia de E: 54 pruebas, paridad con el original celda por celda (valor y estilo) en 4 escenarios y 2 entornos.

---

# Parte Revisión independiente g1 (lectura y normalización)

## Revisión Opus g1 (lectura + normalización + config/constantes) — sobre f3_int 4299880 — recibida 2026-10-10 ~22:50 UTC

Veredicto: puerto FIEL salvo H1 (no declarada, media). Método: AST + diferenciales estrictos en py311/pandas3 y py310/pandas2 (tipo y repr de cada celda,
dtypes, índice, attrs, tipo y mensaje de excepciones): t1 config 185 comparaciones; t2 lectura 37 archivos/1 271 comparaciones (BOM, cp1252, UTF-16, 0 bytes, TSV,
CRLF/CR, Parquet con string/Int64/Float64/boolean/category/datetime, Excel por nombre/posición/None, .xls sin xlrd, carpeta x.csv, symlink…); t3 escalares 929; t4 Normalizador
7 255 (260 nombres adversarios: apóstrofos, &amp;, ß, cirílico, CJK, emoji, 5 000 caracteres…); t5b preparar_fuente 888 (12 variantes de Fuente × 4 configs × 8 marcos);
t6 estado global LOCALES; t8 PYTHONHASHSEED 0-3; diferencial aleatorio de A re-corrido contra f3_int (18 460 y 18 534 comparaciones, 0 diferencias).
Scripts de la revisión: no se conservaron en el repositorio (el encargo está en REVISION_EQUIVALENCIA.md).

### H1 — diferencia NO declarada (media) → DECLARAR como cambio deliberado + prueba
`sha256_archivo`: original `open(ruta,"rb")` (L256) → nuevo `Path(ruta).open("rb")` (lectura.py:33; por el lint PTH123 y el trinquete os_path). `Path()` normaliza:
ruta con barra final («…/ancla.csv/», «/.», «//»): original NotADirectoryError, nuevo devuelve la huella; `""`: FileNotFoundError «''» vs IsADirectoryError «'.'»;
bytes/int/PathLike-bytes: ok vs TypeError (no alcanzables desde ejecutar). Alcanzable desde ejecutar: solo la barra final. `leer_tabla` ya leía esa ruta bien (Path en ambos);
`parametros()` (L2486; nuevo resumen.py:343) la reabre para la huella: el ejecutar() ORIGINAL procesa todo y revienta AL FINAL con NotADirectoryError; el nuevo entrega PARAMETROS con huella.
Decisión: el comportamiento nuevo es coherente con leer_tabla → declararlo en HALLAZGOS_A y fijarlo con prueba `sha256_archivo(f"{ruta}/") == huella`. (Alternativa de paridad estricta: `open(ruta,"rb")  # noqa: PTH123`, sube el trinquete.)

### Defectos del ORIGINAL no catalogados (el nuevo los conserva)
- **g1-O1 ALTA**: `id_comparable(pd.NA)` devuelve 'NA' (L399-402; nuevo normalizar.py:58 y :123). Ocurre con Parquet cuya columna de identificador es texto anulable (dtype string; read_parquet
  lo restaura así en pandas 2.3.3 y 3.0.6; to_numpy() conserva pd.NA): todos los ausentes → 'NA'; `evidencia_extra` (L1263-1268) marca «mismo identificador» y sube el par a EMPRESA banda A.
  Reproducción t7b: ancla 'Zelvora Dulces SL' vs destino 'Zelvora Cosmetica Iberia SL', ambos sin NIF → ENLACES EMPRESA/ALTA «mismo identificador» aunque el MOTIVO dice «difieren en: DULCES / COSMETICA IBERIA». Con NaN no hay vínculo.
  Arreglo: `_es_ausente` acepta también pd.NA y pd.NaT (pd.isna sobre escalares). (A4 solo cubría _hs6.)
- **g1-O2 media**: enteros guardados como float: `id_comparable(900123456.0)` → '9001234560'; `digitos_telefono(3001234567.0)` → '12345670' (como texto: '01234567'). Parquet float64 o Int64 con ausentes (to_numpy → float64).
  Pierde evidencia de identificador/teléfono entre base de texto y numérica; puede dar falsos «mismo teléfono» ('+34 912 345 670' → '12345670' = 3001234567.0). Arreglo: convertir floats enteros a int antes de extraer.
- **g1-O3 media (patrones) / baja**: un str donde va una tupla se recorre letra a letra: `patrones_intermediario="CARGO"` → 'C|A|R|G|O' (L429) → casi toda la ancla es intermediario (EMPRESA no puede ser A (L1209), GRUPO suprimido (L1218), «agente de carga» en todas);
  `formas_legales_extra="S.L."` (L245) → {'.','L','S'} borra las palabras S y L; `alias_paises={"FRA":"FRANCE"}` agrega letras como grafías de Francia; `cols_pais_extra="HQ"` → ('H','Q') (L173; este falla a la vista).
  Arreglo: rechazar str en `ConfigVinculacion.__post_init__` y en `_catalogo`. (Subsume la observación C1 de g2 sobre archivos_decisiones.)
- **g1-O4 baja**: columna con nombre reservado (FUENTE, ID_REGISTRO, FILA_EXCEL) → ValueError «cannot insert FUENTE, already exists» (L313, L329, L331, L332) sin formato qué pasó/qué hacer. Validar en leer_fuente.
- **g1-O5 baja (variante de A16)**: un Normalizador cuyo constructor falla (ISO3 inexistente L432, regex inválida L429) ya registró su locale (L419) y reemplaza en silencio el del Normalizador vivo ('Zelvora Hnos' pasa de 'ZELVORA' a 'ZELVORA HNOS').
  Arreglo: registrar al final de __init__ o resolver en F3.2 (registrar_locale_compuesto).
- **g1-O6 cosmético**: dominio_web con IP '192.168.1.1' → '1.1'; URL con credenciales 'https://user:pw@zelvora.es' → ''.

### No verificado por el revisor
Flujo completo del nuevo (ejecutar.py sin commit); llamadores de `_num` aún no portados (_info_destino L1759, construir_tablas L1792: deben usar serie_numerica); .xls reales (xlrd no instalado);
Excel guardado por Excel real (fórmulas en caché, fechas 1904, celdas de error); Python ≥ 3.12; otras versiones de pandas; rendimiento (A17).

---

# Parte Revisión independiente g2 (comparar y decisiones)

## Revisión Opus g2 (comparar + decisiones) — sobre f3_int 4299880 — recibida 2026-10-10 ~22:40 UTC

Veredicto: EQUIVALENTE en todo el trecho salvo D1. Método: AST normalizado idéntico (sin docstrings/comentarios/anotaciones) +
pruebas diferenciales en py311/pandas3 y py310/pandas2 (100 000 comparaciones por función en misma_palabra/alinear/es_subsecuencia/
es_abreviatura; 20 000 nombres en fichas_abreviatura; estado completo de Comparador con 21 corpus; ~31 000 llamadas a rasgos; 72 escenarios × 2 entornos
de leer_decisiones). Los scripts de la revisión no se conservaron en el repositorio.

### D1 — diferencia NO declarada (media por rúbrica; mínima en la práctica) → se resuelve con B9 en F3.1b
`rasgos` busca la sigla con un `any(...)` inline (original L741-744); el nuevo lo hace en `_hay_abreviatura` (comparar.py:290-297, llamada :366-367):
un nivel más de pila antes de la recursión de `_cubre`. Entrada mínima: `Comparador(nz, ["QR ZZ"], cfg).rasgos("QR ZZ", "QRS " + " ".join(["WK"]*992), False)`:
original ABREVIATURA=False; nuevo RecursionError (con 993 palabras fallan los dos; es_abreviatura directa falla en 995 en ambos). En el motor completo la
cadena de `deduplicar` nuevo es más profunda y el margen baja unos niveles más. Arreglo: `_cubre` iterativa o memoizada (resuelve también B9 y el límite).
Hasta entonces: nombres de ≥ ~990 palabras; irrelevante para empresas reales, pero queda DECLARADO.

### C1 — corrección al catálogo de B (media)
Observación «archivos_decisiones="d.csv" avisa 5 veces» es inexacta. Real (igual en original y nuevo): "d.csv" avisa una vez (por 'd') y luego falla con
IsADirectoryError: '.' (Path('.') existe); ruta absoluta tipo Colab '/content/drive/MyDrive/decisiones.xlsx' falla de inmediato en '/'; un nombre relativo
sin punto ('decisiones') da 10 avisos y 0 decisiones sin error; leer_decisiones([""]) también IsADirectoryError.
Arreglo: ConfigVinculacion acepta str/Path suelto como UN archivo; leer_decisiones usa p.is_file() con mensaje accionable. (Entra en F3.4 decisiones unificadas.)

### Defectos del ORIGINAL no catalogados (el nuevo los conserva igual)
- O1 media: en XLSX una hoja con columna `DECISION_HUMANA` se descarta SIN aviso (el filtro L845-846 exige "DECISION" antes del renombre L858-859;
  nuevo :92-93 vs :114-115): hoja REVISION con DECISION_HUMANA y 2 filas válidas da 0 filas sin error. En CSV sí se acepta. El Excel del motor escribe DECISION.
  Arreglo: renombrar hoja por hoja antes del filtro o avisar cuando una hoja leída no trae DECISION.
- O2 baja: separador decidido solo con la primera línea y solo entre ';' y ',' (L851-852): CSV con ';' y primera línea en blanco, TSV o UTF-16 → KeyError «faltan cinco columnas».
  La lectura de fuentes (L272-274) sí detecta tabulador → aplicar la misma regla sobre la primera línea no vacía.
- O3 baja (extiende B4): archivo con solo BOM / salto de línea / espacios → EmptyDataError: No columns to parse from file.
- O4 baja: `DECISION` y `decision` juntos → ValueError cannot reindex duplicate labels; encabezado numérico en XLSX → AttributeError 'int' has no attribute 'strip' (L844/L857).
  Arreglo: str(c).strip().upper() y detectar duplicados con mensaje claro.
- O5 baja: .xls o XLSX renombrado .csv entra por la rama CSV → UnicodeDecodeError 'charmap'.
- O6 baja: normalización de DECISION (L870 / decisiones.py:133) solo reemplaza espacios simples: "NO  VINCULAR" (dos espacios) se rechaza (ruidoso y claro). Arreglo: .str.replace(r"\s+", "_", regex=True).
- Informativos: es_abreviatura → RecursionError con ≥ ~990 palabras (B9 cubre tiempo, no profundidad); clave_ancla alinea por índice con Series de índices distintos
  (en el motor no ocurre); fechas en celdas XLSX quedan "2026-09-29 00:00:00".

### No verificado por el revisor
Llamadores aún no portados (clasificar L1169, web L1470, L1814) → comprobar de nuevo que nadie muta el dict cacheado de `rasgos` cuando entren C y D;
ejecutar.py (sin commit entonces) no se ejecutó; no corrió la suite del repo; solo XLSX generados con openpyxl (no guardados desde Excel real).
Nota: un proceso ajeno reescribió f3_int/.../__pycache__/resumen.cpython-311.pyc a las 22:35 (no el revisor; git status limpio).

---

# Parte Revisión independiente g3 (deduplicación)

## Revisión Opus g3 (deduplicación interna) — sobre f3_int 4299880 (HEAD movió a 7280361 solo por pruebas de g1) — recibida 2026-10-10 ~23:35 UTC

Veredicto: equivalente con toda entrada que el flujo puede producir (prep de preparar_fuente, dec de leer_decisiones). 3 diferencias, TODAS solo con dec/prep malformados
(ejecutar() nunca los genera). 6 defectos del original no catalogados (conservados idénticos).
Método: lectura línea a línea; cadena de reglas L968-990 cortada del original sin reescribir y comparada con `_motivo_de_union` en 829 440 combinaciones (puntajes a cada lado de
0,75/0,84/0,88 con nextafter y NaN): 0 diferencias; 600 corridas completas con dobles deterministas (misma secuencia de llamadas a puntajes/rasgos); 2 500 filas/2 168 grafías/600 decisiones
idénticas con 160 441 llamadas en el mismo orden; 50 casos adversarios en dos modos (extremo a extremo e híbrido) + 14 escenarios de decisiones; fuzz con Normalizador real
(60 semillas × 6 rondas, 30 por entorno; ejercita las 13 ramas, 561 uniones por decisión, 197 vetos, 120 claves desconocidas). Los scripts de la revisión no se conservaron en el repositorio.

### Diferencias (solo con entradas malformadas) → DECLARAR como cambios deliberados (el nuevo falla más claro)
- g3-H1 (media por rúbrica; impacto real nulo): original L1002/L1053 usa `dec.TIPO`/`dec.FUENTE`; nuevo deduplicar.py:343 (`_filas_duplicado`) usa `dec["TIPO"]`/`dec["FUENTE"]`.
  `dec` no vacío sin TIPO/FUENTE (o «tipo» en minúscula): original AttributeError, nuevo KeyError.
- g3-H2 (media; inalcanzable porque preparar_fuente siempre crea NOMBRE_NORM): original L1048 lee `prep["NOMBRE_NORM"].iat[...]` tras un cortocircuito `if not nom or …`; nuevo :450-451 lo
  evalúa antes (`primero = …`). Destino sin col_id y prep sin NOMBRE_NORM: original devuelve IDs y ninguna unión; nuevo KeyError. Arreglo si se quiere paridad: `if not nom or not nz.utiles(cast("str", prep["NOMBRE_NORM"].iat[int(idx.iloc[0])])): continue`.
- g3-H3 (media; inalcanzable): original L1046 `zip(crudo, prep["PAISES_EVIDENCIA"])`; nuevo :447 con `strict=True`. Destino sin col_id con PAISES_EVIDENCIA duplicada: el original agrupa MAL en silencio
  (toma la etiqueta de columna como país); el nuevo ValueError. Mantener el error (mejor) y declararlo. Los otros `strict=` (:85, :384) no cambian nada.

### Defectos del ORIGINAL no catalogados (conservados)
- **g3-O1 media-alta**: una grafía sin forma legal reconocida hace de PUENTE y une familias incompatibles sin revisión (compatibilidad solo par a par L965/L975; componentes transitivos L1015; L1020 descarta el pendiente).
  «Zelvora», «Zelvora SL», «Zelvora S. Coop.» → una sola empresa y `pe` vacío; sin «Zelvora» el par SL/COOP iría a pendientes. Contradice el docstring (SL ≠ S. COOP.). Propuesta: union-find que no fusione componentes con familias no vacías distintas; el puente va a pendientes.
- **g3-O2 media**: DISTINTA solo quita la arista directa: «Zelvora» une automáticamente con «Zelvora SL» y «Zelvora SA»; DISTINTA(SL,SA) no las separa; la invariante «los pares separados por revisión no quedan en la misma empresa» da FALLA en ejecutar() del original.
  `fusionar_por_empresa_comun` (L1412) sí respeta vetos entre componentes; la deduplicación no. Propuesta: union-find con vetos + aviso.
- **g3-O3 baja**: decisión con CLAVE_A == CLAVE_B: DISTINTA crea veto (p,p) y esa invariante falla siempre; MISMA crea unión (p,p) sin efecto ni aviso (L1008-1013). Rechazar o avisar autopares.
- **g3-O4 baja, latente**: `pe` queda sin columnas (0×0) cuando todos los pendientes quedan absorbidos (si el primer filtro deja pe vacío, `pe[[]]` interpreta la lista vacía como selección de columnas; L1021; nuevo :383-385).
  Rompe el contrato de test_ancla_devuelve_las_tablas_con_sus_columnas (solo probado con pe vacío de origen). Arreglo: filtrar con np.array(dtype=bool) o .loc.
- **g3-O5 media; depende de la versión de pandas**: en pandas 3 el `\s` de `_clave` (L820; nuevo decisiones.py:62-70) lo evalúa Arrow/RE2, que NO reconoce NBSP, U+2007, U+202F, U+3000, \x0b, \x85.
  «Zelvora Alimentos» con NBSP → 1 grafía (N_FILAS=2) en pandas 2, 2 grafías + una unión en pandas 3. Las claves de decisión cambian: un archivo de decisiones hecho en pandas 2 no casa en pandas 3 (filas a desconocidas).
  Afecta igual al destino. Arreglo: `re` de Python o normalizar los espacios Unicode antes. (Rompe la equivalencia entre versiones fuera de los insumos inventados.)
- **g3-O6 media para anclas latinoamericanas**: faltan formas legales en la tabla de familias (L776-784; nuevo :46-69): «Ltda», «L.T.D.A.», «Lda», «S. en C.», «E.U.», «EIRL» quedan SIN familia (= compatibles con todo).
  «Zelvora Ltda» y «Zelvora S.A.S.» se unen solas; «Zelvora Limitada» y «Zelvora S.A.S.» van a pendientes. Además `LIMITED\b` y `LIMITADA` sin `\b` inicial: «Unlimited» cae en LTD, «Ilimitada» en SL.
  Propuesta: `\bLTDA\b|\bLDA\b` a la familia SL y `\b` inicial en esos dos patrones. (Directamente relevante a empresas colombianas.)

Confirmado conservados: B1, B2, B6, B7, B10.

### No verificado por el revisor
ejecutar.py (sin commit entonces); datos reales del piloto; comparar/normalizar/decisiones solo como piezas (no línea a línea); Python 3.12 / pandas 2.0–2.2;
diferencias pandas 2 vs 3 con NaN/None/pd.NA en ID_REGISTRO o PAISES_EVIDENCIA (dentro de cada entorno coinciden; no alcanzables desde leer_fuente/preparar_fuente).

---

