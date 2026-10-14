# Plantilla de base real y verdad real

*Escrita el 2026-10-14 y corregida el mismo día tras la verificación (§7). Es el modelo para
estructurar la base real del usuario y su archivo de verdad real. Todo lo que contiene es inventado:
las razones sociales usan el vocabulario de prueba del repositorio (KRONOS, ZELVORA, TRIVENTA,
PELUVIKA, DULCEVENT, VALDORIA, ORBITAL, MARMOLERIA QUINTAL…) y no identifican a ninguna empresa
real; los correos y sitios web usan el dominio reservado `.example`; los NIT, cédulas y teléfonos son
números al azar dentro de los rangos reales (cualquier coincidencia es casual). La guarda
`tests/test_sin_nombres_reales.py` lo vigila.*

| Qué | Dónde |
|---|---|
| El Excel con las ocho hojas | `data/ejemplos/plantilla_base_real.xlsx` (no se versiona: `*.xlsx` está en `.gitignore`; se regenera con el comando de abajo) |
| Las mismas hojas en CSV (sí se versionan) | `data/ejemplos/plantilla_<hoja>.csv` + `plantilla_etiquetas_anotador_A.csv`, `_B.csv` y `plantilla_banco.csv` |
| El generador (semilla 42, determinista entre procesos) | `scripts/generar_plantilla_base_real.py` |
| El verificador: motor caso por caso, correlativa, muestreo de pares, CSV del banco | `scripts/verificar_plantilla_base_real.py` |
| Las pruebas (20) | `tests/test_plantilla_base_real.py` |
| La evidencia de esta corrida | `docs/evidencia/plantilla_base_real_casos.json`, `plantilla_base_real_verdad_real.json`, `plantilla_base_real_verdad_real_AB.json` |

```bash
python scripts/generar_plantilla_base_real.py --salida data/ejemplos --semilla 42
python scripts/verificar_plantilla_base_real.py --datos data/ejemplos --dir-trabajo /tmp/plantilla_trabajo \
    --salida docs/evidencia/plantilla_base_real_casos.json --correlativa /tmp/plantilla_trabajo/correlativa.parquet
```

## 1. Por qué una plantilla y no las tablas de verdad que había

Las tres tablas del Drive (`Ground_Truth_Robusto_V3`, `Ground_Truth_Exhaustivo_RecordLinkage`,
`Ground_Truth_Multicampo_v1`) sirven para regresión, pero no para estructurar una base real
(`docs/BENCHMARK.md` §1 y la inspección de 2026-10-14):

- ninguna tiene un **identificador de registro** estable ni una columna **FUENTE** con fuentes reales;
- la verdad va como columna de grupo y el tipo de caso embebido en texto libre (`EXPLICACIÓN DEL
  RETO`, `VARIATION_TYPE`); no hay **REGIMEN** ni **CASO** por columna, ni archivo de **pares**
  `ID_1, ID_2, IS_MATCH, ANOTADOR` que es lo que exige `docs/PROTOCOLO_GROUND_TRUTH.md`;
- los **NIT están corrompidos dentro de los grupos positivos** (49,9 % de los grupos en Robusto V3,
  65,7 % en Exhaustivo) y el 88 % de los «dígitos de verificación» no validan: un vinculador que
  confíe en el NIT es castigado por la etiqueta;
- los negativos no son difíciles (vecino más parecido a mediana 68,9 de similitud) o son ocho
  familias con NIT correlativos; faltan homónimos, filiales y personas naturales;
- traen marcas reales como semilla, así que no pueden entrar al repositorio.

La plantilla corrige cada punto: `ID_REGISTRO_FUENTE` único por fuente, tres fuentes con nombre
real (CRM, RUES, ADUANAS), `VERDAD_GRUPOS` con `TIPO_ENTIDAD`/`REGIMEN`/`CASO`/`NIVEL_RUIDO`/
`VARIACION` por columna, `ETIQUETAS_PARES` en el formato de `leer_etiquetas`, NIT inventados con DV
correcto (módulo 11, `digito_verificacion_dian` de la librería) que **nunca cambian de dígitos dentro
de un grupo positivo**, negativos diseñados (homónimos de persona, razón social idéntica con NIT
distinto, genéricos, filial/matriz, NIT a un dígito, raíces confundibles) y nombres que no apuntan a
nadie.

## 2. Qué hay en cada hoja

| Hoja | Filas | Para qué |
|---|---|---|
| `LEEME` | 38 líneas | Instrucciones: qué es cada hoja, cómo copiar la estructura, cómo se escribe el NIT, qué va en `IS_MATCH`, la regla de decisión y los comandos exactos |
| `FUENTE_CRM` / `FUENTE_RUES` / `FUENTE_ADUANAS` | 187 / 176 / 173 | Un registro por fila, 15 columnas iguales en las tres: `ID_REGISTRO_FUENTE`, `NIT`, `DV`, `TIPO_IDENTIFICACION`, `RAZON_SOCIAL`, `NOMBRE_COMERCIAL`, `DEPARTAMENTO`, `MUNICIPIO`, `DIRECCION`, `TELEFONO`, `EMAIL`, `SITIO_WEB`, `CIIU`, `PAIS`, `FECHA_ACTUALIZACION` |
| `VERDAD_GRUPOS` | 536 | `ID_GRUPO` por registro (278 grupos: 84 de 1, 133 de 2, 58 de 3, 3 de 4), `TIPO_ENTIDAD` (513 empresa / 23 persona), `REGIMEN` (471 CON_ID / 65 SIN_ID), `CASO`, `NIVEL_RUIDO` (390 CLEAN, 104 LIGHT, 30 MODERATE, 12 SEVERE), `VARIACION`. Opcional en una base real |
| `ETIQUETAS_PARES` | 109 | 76 pares `1`, 30 pares `0`, 3 `UNCLEAR`; `ANOTADOR` A (49) / B (48) / resuelto (12); `BANDA_ESPERADA` (la dificultad diseñada por el generador, **no** la decisión del motor); `COMENTARIO` |
| `DICCIONARIO` | 40 | Cada columna: hoja, obligatoria, tipo, formato, ejemplo y regla; incluye las columnas de `plantilla_banco.csv` y la `BANDA_PUNTAJE` de la base real |
| `CASOS` | 24 | El catálogo: descripción, qué debe hacer el motor, ids de ejemplo, cuántos registros. Opcional en una base real |

Los NIT: 236 filas traen `DV` aparte y **todos validan** (`base_canonica(NIT+DV) == NIT`); los
que van con el DV pegado (10 dígitos) se reducen a la misma base; ninguna base está en dos grupos,
y las únicas parejas de NIT a menos de tres dígitos son las del caso `negativo_nit_corrupto_un_digito`.

Los 23 casos del encargo (el contrato que `tests/test_plantilla_base_real.py::CASOS_OBLIGATORIOS`
exige con al menos dos registros cada uno; `singleton` con uno):

- 16 positivos: `positivo_nit_igual`, `positivo_nit_con_y_sin_dv`, `positivo_nit_con_ceros_o_puntuacion`,
  `positivo_sufijo_legal`, `positivo_errata_1_letra`, `positivo_errata_2_letras`,
  `positivo_orden_de_palabras`, `positivo_sigla`, `positivo_sigla_sin_nit`, `positivo_truncado`,
  `positivo_nombre_comercial_vs_razon_social`, `positivo_sin_nit_en_una_fuente`,
  `positivo_sin_nit_en_ninguna`, `positivo_sucursal`, `positivo_persona_natural`, `positivo_mojibake`;
- 6 negativos: `negativo_homonimo_persona`, `negativo_nombre_identico_nit_distinto`,
  `negativo_nombre_generico`, `negativo_filial_matriz`, `negativo_nit_corrupto_un_digito`,
  `negativo_raiz_confundible`;
- `singleton`;
- y un 24.º no pedido, `ambiguo_revisar`, para que los tres pares `UNCLEAR` vivan en registros con
  grupo propio sin contaminar `singleton`.

## 3. Cómo usar la plantilla para estructurar la base real (paso a paso)

1. **Abra el Excel y lea `LEEME`.** Después `DICCIONARIO`: ahí dice, columna por columna, qué es
   obligatorio (`ID_REGISTRO_FUENTE`, `RAZON_SOCIAL`, `TIPO_IDENTIFICACION`, `PAIS`; las dos últimas
   no las lee el motor, separan personas y extranjeros en el análisis) y qué es opcional (todo lo
   demás, que se deja vacío si la fuente no lo tiene). De las opcionales el motor solo usa
   `MUNICIPIO`; `TELEFONO`, `EMAIL`, `DIRECCION` y `CIIU` solo cuentan si al correr el motor se piden
   (`extra_features=["TELEFONO", "EMAIL"]` en `linkage()` o `--variables-extra
   TELEFONO:telefono_signed:0.10` en `scripts/banco.py`); con los comandos de esta guía son
   informativas.
2. **Exporte cada fuente a su hoja `FUENTE_*`** con esos encabezados exactos (el orden no importa,
   el nombre sí). Una fila por registro, sin consolidar nada. Puede renombrar, agregar o quitar hojas
   de fuente mientras el nombre empiece por `FUENTE_` (`FUENTE_DIAN`, `FUENTE_SUPERSOCIEDADES`…): el
   verificador las descubre por ese prefijo.
3. **Numere cada registro** con `ID_REGISTRO_FUENTE` = prefijo de la fuente + clave primaria de su
   sistema (`CRM-000017`). Ese id no cambia nunca: es la llave de la verdad.
4. **Escriba el NIT solo con dígitos y SIN ceros a la izquierda** (quítelos antes de cargar:
   `NIT.str.lstrip("0")`). Puntos, guiones, espacios y el DV pegado los tolera el motor (`800123456` y
   `8001234565` son el mismo), pero se recomienda quitarlos; los ceros a la izquierda **no** los tolera
   hoy (hallazgo abierto: `000901156189` queda sin pareja; fila `positivo_nit_con_ceros_o_puntuacion`
   de la tabla de §5). Si la fuente trae el DV aparte, va en `DV`. Guarde la columna como texto y
   léala con `dtype=str` (paso 6). Vacío = sin identificador. Las personas naturales llevan la cédula
   en `NIT` y `TIPO_IDENTIFICACION` = `CC` (o `CE`).
5. **Guarde la base real fuera del repositorio** (Drive, carpeta `verdad_real/`): tiene nombres
   reales y datos licenciados. Al repositorio solo entran cifras.
6. **Corra el motor y guarde la correlativa** con un solo comando:
   `python scripts/verificar_plantilla_base_real.py --datos mi_base.xlsx --dir-trabajo salida/trabajo --correlativa salida/correlativa.parquet`
   (sin `VERDAD_GRUPOS` ni `CASOS` solo corre el motor). Si prefiere Python, lea cada hoja con
   `pd.read_excel(ruta, sheet_name="FUENTE_CRM", dtype=str, keep_default_na=False)` (y los CSV con
   `pd.read_csv(..., dtype=str, keep_default_na=False)`) y llame
   `linkage({"CRM": df_crm, "RUES": df_rues, "ADUANAS": df_aduanas}, col_nit="NIT", col_name="RAZON_SOCIAL", col_ciudad="MUNICIPIO", work_dir="salida/trabajo")`,
   luego `resultado.correlativa.to_parquet("salida/correlativa.parquet")`. **Sin `dtype=str` pandas
   vuelve numérico el NIT, pierde los ceros y los vacíos, y el motor da otra partición** (279 grupos
   en vez de 281 sobre la plantilla: los dos grupos con ceros a la izquierda se unen porque el float
   los perdió). No pase `col_id`: el motor antepondría la fuente otra vez (`CRM-CRM-000001`); la
   correlativa conserva `ID_REGISTRO_FUENTE` en cada fila, que es la columna con la que se mide, y
   `MUNICIPIO` aparece en ella como `CIUDAD`.

## 4. Cómo producir la verdad real a partir de ella

7. **Muestree los pares** con el mismo script:
   `python scripts/verificar_plantilla_base_real.py --datos mi_base.xlsx --dir-trabajo salida/trabajo --pares-a-etiquetar salida/pares_a_etiquetar.csv --n-pares 500`.
   Salen dos archivos: `pares_a_etiquetar.csv` (`ID_1`, `ID_2`, razón social, NIT y municipio de los
   dos registros, e `IS_MATCH`, `ANOTADOR` y `COMENTARIO` vacíos; **sin ninguna columna del motor**)
   para los anotadores, y `pares_a_etiquetar_bandas.csv` (`REGIMEN`, `BANDA_PUNTAJE`, `PUNTAJE`,
   `UNIDO_POR_EL_MOTOR`) que se pega al archivo final. La muestra se reparte por turnos entre régimen
   (los dos con identificador / ninguno / mixto) y banda de puntaje, y las bandas salen de lo que el
   motor hizo de verdad (`scored.db` y `candidates.db` del directorio de trabajo), con el umbral del
   perfil (`score_threshold`, 0,45 en `produccion_estandar`) y un margen de 0,10:
   `unido_holgado` = unidos con puntaje ≥ umbral + 0,10 o por identificador; `unido_borde` = unidos
   con puntaje entre el umbral y umbral + 0,10; `no_unido_borde` = candidatos que el motor no unió
   (puntaje bajo el umbral, o sobre él pero vetados por identificador distinto o por la cobertura);
   `no_unido_lejos` = nunca fueron candidatos (control). Empiece por 500; para certificar cifras por
   régimen hacen falta 3.000–5.000. Incluya a propósito homónimos de persona, sucursales, genéricos
   y pares con NIT igual y nombre distinto. `scripts/active_labeling.py` **no sirve** para esto: no lee
   la correlativa, corre su propio `linkage` con `col_ciudad="CIUDAD"` y escribe `id_left`/`id_right`
   como índices internos que no son `ID_REGISTRO_FUENTE`.
8. **Dos personas etiquetan el mismo archivo a ciegas**, cada una en su copia (como
   `plantilla_etiquetas_anotador_A.csv` y `_B.csv`), sin ver la decisión del motor ni la del otro.
   En `IS_MATCH`: `1` misma empresa, `0` distinta, `UNCLEAR` no se puede decidir; en `ANOTADOR`, su letra.
9. **Regla de decisión** (se entrega por escrito): «misma empresa» = la misma persona jurídica o
   natural (misma matrícula / NIT raíz aunque cambie la sucursal, la forma legal o la grafía). Una
   **sucursal es la misma** empresa. Una **filial, una matriz o un grupo NO** es la misma empresa. Dos
   personas con el mismo nombre y cédulas distintas son distintas. El mismo NIT con nombres muy
   distintos se marca `UNCLEAR`; si la consulta al RUES lo resuelve antes de cerrar el archivo, se
   escribe `1` o `0` con `ANOTADOR = resuelto` y el porqué en `COMENTARIO`.
10. **Mida el acuerdo** con los dos archivos a ciegas:
    `python scripts/medir_verdad_real.py --etiquetas A.csv --etiquetas-b B.csv --correlativa salida/correlativa.parquet --col-id ID_REGISTRO_FUENTE --col-nit NIT`.
    Kappa > 0,80: verdad confiable; 0,60–0,80: resolver los desacuerdos en una sesión; < 0,60: el
    manual está mal definido, se reescribe la regla y se vuelve a etiquetar. **El kappa solo se
    calcula con los dos archivos**: `medir_verdad_real.py` exige que `ANOTADOR` tenga exactamente dos
    valores, así que el archivo final (A / B / resuelto) no lo imprime. Sobre la plantilla, A y B
    difieren en los 12 pares marcados `resuelto`: kappa 0,703 (sustancial).
11. **Archivo final** = `ETIQUETAS_PARES`: una etiqueta por par; `ANOTADOR` = A cuando coincidieron
    (o quien consolidó), `resuelto` si se decidió en sesión (con el porqué en `COMENTARIO`); se le
    pega `BANDA_PUNTAJE` desde `pares_a_etiquetar_bandas.csv`. Nunca se reetiqueta lo ya resuelto; la
    verdad crece con pares nuevos.
12. **Mida y publique**:
    `python scripts/medir_verdad_real.py --etiquetas verdad_real/etiquetas_resueltas.csv --correlativa salida/correlativa.parquet --col-id ID_REGISTRO_FUENTE --col-grupo ID_GRUPO --col-nit NIT --salida docs/evidencia/verdad_real_<fecha>.json`.
    El archivo de etiquetas es un CSV o un Excel con los pares en la primera hoja; si están en otra
    hoja (como `ETIQUETAS_PARES` en el libro de la plantilla), añada `--hoja ETIQUETAS_PARES`. Reporta
    pares, TP/FP/FN, precisión y recall con intervalo de Wilson 95 % y F1, global y por régimen; los
    `UNCLEAR` se descartan y se cuentan.
13. **Regresión con la partición completa** (`VERDAD_GRUPOS`): el CSV plano del banco se construye
    desde las hojas (`construir_banco`: `VERDAD_GRUPOS` + `FUENTE_*`; en una base real `REGIMEN` y
    `TIPO_ENTIDAD` se deducen si faltan y `CASO` puede ir vacío) con
    `python scripts/verificar_plantilla_base_real.py --datos mi_base.xlsx --banco salida/banco.csv --estrato MI_LOTE`,
    y corre con `python scripts/banco.py --datos salida/banco.csv --etiqueta mi_lote --dir-trabajo /tmp/banco_mi_lote --evidencia /tmp/evid_mi_lote`.
    El de la plantilla es `data/ejemplos/plantilla_banco.csv` (`--datos data/ejemplos/plantilla_banco.csv --etiqueta plantilla`).

## 5. Qué hizo el motor con la plantilla (rues-linker 0.22.4, `produccion_estandar`)

`scripts/verificar_plantilla_base_real.py`, 2026-10-14. 536 registros → **281 grupos predichos**
(278 verdaderos). Pares contra `VERDAD_GRUPOS`: 325 verdaderos, 321 predichos, TP 317, FP 4, FN 8,
**precisión 0,9875, recall 0,9754, F1 0,9814**. Casos acertados: **18 de 24**. Los fallos no se
ocultan: son lo que el motor no sabe hacer hoy.

| CASO | Esperado | Motor | Detalle |
|---|---|---|---|
| `positivo_nit_igual` | unir | acertó | 118/118 grupos unidos completos |
| `positivo_nit_con_y_sin_dv` | unir | acertó | 3/3 grupos unidos completos |
| `positivo_nit_con_ceros_o_puntuacion` | unir | **falló** | 1/3 grupos unidos completos |
| `positivo_sufijo_legal` | unir | acertó | 4/4 grupos unidos completos |
| `positivo_errata_1_letra` | unir | acertó | 4/4 grupos unidos completos |
| `positivo_errata_2_letras` | unir | **falló** | 3/4 grupos unidos completos |
| `positivo_orden_de_palabras` | unir | acertó | 3/3 grupos unidos completos |
| `positivo_sigla` | unir | acertó | 3/3 grupos unidos completos |
| `positivo_sigla_sin_nit` | revisar | acertó | 0/2 grupos unidos y 0 pares entre grupos unidos (se esperaba 0: sin evidencia suficiente, el motor no debe adivinar) |
| `positivo_truncado` | unir | acertó | 3/3 grupos unidos completos |
| `positivo_nombre_comercial_vs_razon_social` | unir | acertó | 3/3 grupos unidos completos |
| `positivo_sin_nit_en_una_fuente` | unir | acertó | 8/8 grupos unidos completos |
| `positivo_sin_nit_en_ninguna` | unir | acertó | 8/8 grupos unidos completos |
| `positivo_sucursal` | unir | acertó | 3/3 grupos unidos completos |
| `positivo_persona_natural` | unir | acertó | 4/4 grupos unidos completos |
| `positivo_mojibake` | unir | **falló** | 2/3 grupos unidos completos |
| `negativo_homonimo_persona` | no unir | acertó | 0 pares de entidades distintas unidos; 3/3 entidades con varios registros unidas internamente |
| `negativo_nombre_identico_nit_distinto` | no unir | acertó | 0 pares de entidades distintas unidos; 3/3 entidades con varios registros unidas internamente |
| `negativo_nombre_generico` | no unir | acertó | 0 pares de entidades distintas unidos; 3/3 entidades con varios registros unidas internamente |
| `negativo_filial_matriz` | no unir | acertó | 0 pares de entidades distintas unidos; 3/3 entidades con varios registros unidas internamente |
| `negativo_nit_corrupto_un_digito` | no unir | acertó | 0 pares de entidades distintas unidos; 3/3 entidades con varios registros unidas internamente |
| `negativo_raiz_confundible` | no unir | **falló** | 2 pares de entidades distintas unidos; 3/3 entidades con varios registros unidas internamente |
| `singleton` | no unir | **falló** | 1 de 60 singletons mezclados con otro registro |
| `ambiguo_revisar` | revisar | **falló** | 0/0 grupos unidos y 1 pares entre grupos unidos (se esperaba 0: sin evidencia suficiente, el motor no debe adivinar) |

Qué hay detrás de cada fallo (ids de `data/ejemplos/`):

- **NIT con ceros a la izquierda** (`CRM-000094` = `000901156189`, `CRM-000096` = `000901229927`):
  el motor los deja `sin_pareja` aunque el resto del grupo (`901156189`, `901.156.189`,
  `901229927-5`) sí se une. `base_canonica` quita los ceros, pero el camino de bloqueo por
  identificador no. Hallazgo para el motor; en la base real, quite los ceros antes de cargar (§3.4).
- **Dos erratas sin NIT** (`INVERSIONES NOVARQUE Y LOPEZ S EN C` vs `INVERSIOONES NOVARQUE Y LPOEZ`,
  `G0031`): con dos tokens dañados el comparador Soft TF-IDF (tolerancia 0,90 por token) no llega al
  umbral. Con NIT (los otros tres grupos) sí une.
- **Mojibake sin NIT** (`DISEÑOS LONDOÑO Y QUIÑONES` vs `DISEÃ‘OS LONDOÃ‘O Y QUIÃ‘ONES`, `G0182`):
  la reparación de mojibake no alcanza a esta forma cuando el nombre es toda la evidencia. Con NIT une.
- **Raíz confundible** (`TRUVEL ENERGIA SAS` con NIT vs `TRUVELA ENERGIA SAS` sin NIT, misma
  ciudad): se unen por nombre (0,49). Con NIT en los dos lados (`ZELVORA` vs `ZELVORO`) el veto
  por identificador distinto los separa, y sin NIT en ninguno (`KALMIRA` vs `KALMIRO`) la cobertura
  por estrellas los separa. El par mixto es el hueco.
- **Singleton mezclado** (`COMERCIALIZADORA LAVIRENA S.A.` sin NIT, Envigado, con
  `COMERCIALIZADORA OLVERANA LTDA` con NIT, Cartagena; 0,48): la palabra de sector arrastra el
  puntaje por encima del umbral cuando un lado no tiene identificador. Es el mismo patrón que el
  anterior y el que más conviene vigilar en la base real.
- **Ambiguo** (`JUAN CARLOS PEREZ GOMEZ` sin cédula vs `PEREZ GOMEZ JUAN CARLOS` con cédula, otra
  ciudad): el motor une un nombre de persona muy común sin identificador; una persona lo marcaría
  `UNCLEAR`. Los otros dos pares ambiguos (`COMERCIALIZADORA ORBITAL` / `ORBITAL COMERCIALIZADORA
  DEL CARIBE SAS`; `INVERSIONES VALDORIA` / `VALDORIA INVERSIONES Y CONSTRUCCIONES SAS`) quedaron
  separados.

Lo que sí hace bien y conviene saber: une el NIT con y sin DV, con puntos y guion y con espacios;
tolera sufijos legales, una errata, orden de palabras, truncados, siglas (con NIT), nombre
comercial contra razón social (con NIT), sucursales con ciudad en el nombre, personas naturales con
el orden de nombres cambiado y el régimen mixto (NIT en una sola fuente); y no une homónimos de
persona, razones sociales idénticas con NIT distinto, genéricos, filial/matriz ni NIT a un dígito.

`BANDA_ESPERADA` contra el motor: en 6 de los 109 pares de `ETIQUETAS_PARES` la banda diseñada no
es lo que el motor hizo (`CRM-000096`/`RUES-000133` y `CRM-000094`/`RUES-000078` `unido_holgado` pero
sin unir; `CRM-000059`/`ADUANAS-000003` y `RUES-000105`/`ADUANAS-000044` `unido_borde` pero sin unir;
`CRM-000037`/`RUES-000016` y `CRM-000077`/`ADUANAS-000029` `no_unido_borde` pero unidos). Son los
mismos seis fallos de arriba vistos por par; por eso la columna se llama *esperada* y no *puntaje*.

### Contra `ETIQUETAS_PARES` (`scripts/medir_verdad_real.py`)

106 pares evaluables (3 `UNCLEAR` descartados):

| REGIMEN | PARES | TP | FP | FN | PRECISION (IC 95 %) | RECALL (IC 95 %) | F1 |
|---|---|---|---|---|---|---|---|
| GLOBAL | 106 | 70 | 1 | 6 | 0,9859 (0,9244–0,9975) | 0,9211 (0,8383–0,9633) | 0,9524 |
| CON_ID | 78 | 50 | 0 | 2 | 1,0000 (0,9286–1,0) | 0,9615 (0,8702–0,9894) | 0,9804 |
| SIN_ID | 16 | 11 | 0 | 4 | 1,0000 (0,7412–1,0) | 0,7333 (0,4805–0,891) | 0,8462 |
| MIXTO | 12 | 9 | 1 | 0 | 0,9000 (0,5958–0,9821) | 1,0000 (0,7008–1,0) | 0,9474 |

Los intervalos son anchos porque la muestra es pequeña: es exactamente lo que el protocolo dice que
pasa con 100 pares, y por qué pide 3.000–5.000 para certificar. Los 2 FN de CON_ID son los dos NIT
con ceros a la izquierda; los 4 de SIN_ID, las dos siglas sin NIT (que el protocolo cuenta como
positivas aunque el motor deba revisarlas), la errata doble y el mojibake; el FP de MIXTO es
`TRUVEL`/`TRUVELA`.

### Con `scripts/banco.py` sobre `plantilla_banco.csv`

Huella `b847d835…`, precisión 0,9875, recall 0,9754, F1 0,9814, B³ F1 0,9907, 281 grupos vs 278,
2 FP que tocan un negativo, recall CON_ID 0,9865 / SIN_ID 0,8000, 2,6 s, 260 MiB.

## 6. Qué no es esta plantilla

- **No es verdad real.** Es sintética; certifica el formato, no el motor. La primera cifra válida
  sobre empresas reales sale de la base real etiquetada según §4.
- **No es el banco.** El banco sigue siendo `data/benchmark/benchmark_institucional.csv.gz`; la
  plantilla no entra en `tests/lineas_base.py` ni en `compuertas.json`.
- La cobertura de las columnas opcionales cambia por fuente a propósito (el RUES trae CIIU y
  dirección; aduanas casi nunca trae correo): es cobertura, no ruido, y `NIVEL_RUIDO` describe solo
  `NIT`, `RAZON_SOCIAL` y `MUNICIPIO`.

## 7. Lo que corrigió la verificación del mismo día

Tres verificadores revisaron la primera versión; lo que era cierto se corrigió, sin mover ninguna
cifra de §5 (los ids, NIT, nombres y municipios no cambiaron: el generador consume el azar en el
mismo orden):

- correos y sitios con TLD reales (`.com`, `.co`, `gmail.com`, `café.co`…) → dominio reservado
  `.example`, sin tildes; buzones de persona también en `.example`;
- el módulo 11 estaba escrito dos veces → solo `digito_verificacion_dian` de la librería;
- `DICCIONARIO` y la guía decían que la librería tolera ceros a la izquierda → una sola regla en
  los tres sitios (§3.4);
- `BANDA_PUNTAJE` se presentaba como la banda del motor y atribuida a `active_labeling.py` → se
  llama `BANDA_ESPERADA`, y la banda real la escribe el muestreador nuevo (§4.7) con umbrales escritos;
- el paso de muestreo con `active_labeling.py` no producía ids medibles → `--pares-a-etiquetar`;
- el verificador tenía las hojas fijas y exigía `VERDAD_GRUPOS`/`CASOS` → descubre `FUENTE_*` por
  prefijo y corre en modo base real; `--banco` escribe el CSV del banco (`construir_banco`, una sola
  implementación para la plantilla y para la base real; `VERDAD_GRUPOS` ganó `TIPO_ENTIDAD`);
- `medir_verdad_real.py` leía siempre la primera hoja → `--hoja`;
- lectura del Excel sin `dtype=str` cambia la partición → documentado (§3.6);
- `TELEFONO`/`EMAIL`/`CIIU` descritos con jerga interna y como si contaran → regla en lenguaje llano
  y advertencia de que son informativas salvo con `extra_features`;
- `plantilla_banco.csv` sin diccionario, LEEME con ruta relativa al banco y frases ambiguas
  (`ANOTADOR`, `CASO`, `REGIMEN`, `FECHA_ACTUALIZACION`, kappa) → corregidas;
- KRONOS se describía como marca inventada; es vocabulario de prueba de 22 archivos del
  repositorio y existe como marca en otros sectores → los textos dicen que los nombres no
  identifican a ninguna empresa, no que sean inventados; nombres de uso común (`DISTRIBUIDORA DEL
  NORTE SAS`, `PANADERIA LA ESPIGA DORADA`) anotados como tales en `LEEME`.
