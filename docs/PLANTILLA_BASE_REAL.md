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
| La prueba multicriterio: paquete, notebook 13, script y pruebas (36) | §8: `src/record_linkage/evaluation/multicriterio/` (paquete), `notebooks/13_multicriterio_plantilla_base_real.ipynb`, `scripts/multicriterio_plantilla.py`, `tests/test_multicriterio.py` |
| La evidencia de esta corrida | `docs/evidencia/plantilla_base_real_casos.json`, `plantilla_base_real_verdad_real.json`, `plantilla_base_real_verdad_real_AB.json`, `plantilla_base_real_multicriterio.json` |

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

### 5.1 ¿Sirve para una prueba multicriterio? (`scripts/multicriterio_plantilla.py`, 2026-10-14)

Multicriterio = el motor usa, además del NIT y la razón social, otras columnas de la plantilla como
evidencia: `linkage(..., extra_features=[{"column": "EMAIL", "weight": 0.10, "type": "email_signed"}, ...])`.
El puntaje es lineal (0,5·nombre + 0,5·nit + Σ peso·similitud, umbral 0,45) y los comparadores
firmados valen −1..+1 (0 si falta un lado). `scripts/multicriterio_plantilla.py` corrió doce
configuraciones sobre las mismas fuentes y midió cada una contra `VERDAD_GRUPOS`, contra
`ETIQUETAS_PARES` y contra la base; todo está en
`docs/evidencia/plantilla_base_real_multicriterio.json` (ids y cifras, sin datos de registros).
Cómo repetirla sobre la plantilla o sobre su base (notebook 13, módulo del paquete): §8.

**Respuesta corta: sí, para lo que la plantilla tiene; no, para lo que le falta.** Sirve para probar
de punta a punta que las variables extra llegan al scorer y mueven decisiones, para ver la diferencia
entre comparadores firmados y no firmados, y para ver que correo y teléfono separan los dos falsos
positivos de borde. No sirve para medir el riesgo real de las variables de contacto (ningún negativo
comparte correo o teléfono), ni el rescate de recall (ningún FN es rescatable por las extras), ni la
sensibilidad a los pesos (0,10 y 0,15 dan lo mismo).

**Qué aporta cada variable** (comparador aplicado a los 325 pares positivos y a 210 negativos
difíciles: los 30 `IS_MATCH=0` de ETIQUETAS_PARES más los pares entre entidades distintas de los
casos `negativo_*`; «llenos» = las dos celdas con dato; el llenado redondea el medio hacia arriba, como se
mide a mano: EMAIL 264 de 536 = 49,25 % → 49,3 %):

| Columna | Comparador | Llenado | Positivos llenos / premia / castiga | Negativos llenos / premia / castiga | Separación (pp) |
|---|---|---|---|---|---|
| EMAIL | `email_signed` | 49,3 % | 59 / 59 / 0 | 69 / 0 / 69 | +100,0 |
| TELEFONO | `telefono_signed` | 61,6 % | 114 / 114 / 0 | 97 / 0 / 97 | +100,0 |
| TELEFONO | `categorical_signed` (exacto) | 61,6 % | 114 / 4 / 110 | 97 / 0 / 97 | +3,5 |
| DIRECCION | `tipo_direccion` | 68,5 % | 147 / 147 / 0 | 106 / 38 / 66 | +64,2 |
| DIRECCION | `categorical_signed` (exacto) | 68,5 % | 147 / 1 / 146 | 106 / 0 / 106 | +0,7 |
| MUNICIPIO | `categoria_tolerante_signed` | 90,1 % | 268 / 261 / 7 | 172 / 21 / 151 | +85,2 |
| MUNICIPIO | `categorical_signed` (exacto) | 90,1 % | 268 / 215 / 53 | 172 / 21 / 151 | +68,0 |
| DEPARTAMENTO | `categoria_tolerante_signed` | 65,7 % | 138 / 135 / 3 | 75 / 14 / 61 | +79,2 |
| CIIU | `conjunto_signed` | 37,1 % | 12 / 12 / 0 | 33 / 0 / 33 | +100,0 |
| SITIO_WEB | `exact_signed` | 11,9 % | 8 / 8 / 0 | 1 / 0 / 1 | +100,0 |

Lectura: correo y teléfono discriminan del todo, pero porque la plantilla es sintética (59 de 59
correos idénticos crudos; ningún negativo comparte contacto). El comparador exacto de teléfono ve
el formato y no el número (4 de 114): es la regla de CLAUDE.md medida. La dirección exacta no
reconoce casi nada (1 de 147) y `tipo_direccion` reconoce todo, pero premia 38 de 106 negativos.
El municipio tolerante premia 21 de 172 negativos: coincidir de ciudad no es evidencia fuerte.
CIIU y SITIO_WEB están llenos en 12 y 8 pares positivos: no hay con qué medirlos.

**Qué hace el motor con cada configuración** (pares contra `VERDAD_GRUPOS`; 325 verdaderos):

| Configuración | Variables extra | Grupos | P | R | F1 | FP | FN | Casos |
|---|---|---|---|---|---|---|---|---|
| `A_base` | ninguna | 281 | 0,9875 | 0,9754 | 0,9814 | 4 | 8 | 18/24 |
| `B_contacto` | EMAIL 0,10 `email_signed` + TELEFONO 0,10 `telefono_signed` | 283 | **0,9937** | 0,9754 | 0,9845 | 2 | 8 | 20/24 |
| `C_contacto_geo` | B + MUNICIPIO 0,05 tolerante + DIRECCION 0,05 `tipo_direccion` | 283 | 0,9937 | 0,9754 | 0,9845 | 2 | 8 | 20/24 |
| `D_contacto_geo_alto` | como C con 0,15 / 0,15 / 0,10 / 0,10 | 283 | 0,9937 | 0,9754 | 0,9845 | 2 | 8 | 20/24 |
| `E_solo_geo` | MUNICIPIO 0,10 tolerante | 282 | 0,9906 | 0,9754 | 0,9829 | 3 | 8 | 19/24 |
| `F_no_firmados` | las de C con `categorical` / `token_set_ratio` | 279 | 0,9814 | 0,9754 | 0,9784 | 6 | 8 | 18/24 |
| `G_solo_email` | EMAIL 0,10 | 282 | 0,9906 | 0,9754 | 0,9829 | 3 | 8 | 19/24 |
| `H_solo_telefono` | TELEFONO 0,10 | 282 | 0,9906 | 0,9754 | 0,9829 | 3 | 8 | 19/24 |
| `I_solo_direccion` | DIRECCION 0,10 | 281 | 0,9875 | 0,9754 | 0,9814 | 4 | 8 | 18/24 |
| `J_todas` | C + DEPARTAMENTO, CIIU (`conjunto_signed`), SITIO_WEB, 0,05 cada una | 283 | 0,9937 | 0,9754 | 0,9845 | 2 | 8 | 20/24 |
| `K_llave_email` | B + `llaves_bloqueo: ["EMAIL"]` (candidatos por correo idéntico) | 283 | 0,9937 | 0,9754 | 0,9845 | 2 | 8 | 20/24 |
| `L_llave_email_pesos_altos` | K con EMAIL y TELEFONO a 0,30 | 283 | 0,9937 | 0,9754 | 0,9845 | 2 | 8 | 20/24 |

Contra `ETIQUETAS_PARES` las doce dan lo mismo (P 0,9859 / R 0,9211): los pares que las extras
mueven están `UNCLEAR` o sin etiquetar. Las etiquetas de la plantilla no ven el multicriterio.

**Qué pares se mueven** (ids de `data/ejemplos/`; nunca cambia un par verdadero):

- Correo y teléfono (B, C, D, J, K, L) separan los dos falsos positivos de borde de §5: el singleton
  `CRM-000051`/`CRM-000161` (`OLVERANA`/`LAVIRENA`, 0,48: el correo tiene dominio distinto, −0,10) y el
  ambiguo `CRM-000037`/`RUES-000016` (`JUAN CARLOS PEREZ GOMEZ`, 0,50: el teléfono es distinto, −0,10).
  Por eso `singleton` y `ambiguo_revisar` pasan a acertar (18 → 20 casos). `G` solo separa el
  primero y `H` solo el segundo: cada variable hace exactamente una cosa.
- `TRUVEL`/`TRUVELA` (`ADUANAS-000029` con `CRM-000077` y `RUES-000057`) **no se separa con nada**: el
  registro de ADUANAS no tiene teléfono ni correo, el municipio coincide (premia) y la dirección
  distinta pesa poco. Siguen siendo los 2 FP de la recomendada.
- Solo municipio (E) separa los dos de borde pero **une uno nuevo**: `CRM-000130`/`RUES-000119`
  (`VALDORIA`, ambiguo, las dos en CALI). Coincidir de ciudad premia mal.
- Los no firmados (F) no separan nada (no castigan) y unen dos falsos: `ADUANAS-000027`/`CRM-000115`
  (`ORBITAL`) y el de `VALDORIA`. El motivo está en el motor: `categorical` devuelve **0,5 cuando un
  lado está vacío**, así que un dato ausente suma +0,05 por variable y empuja pares de borde.
- Dirección sola (I) no mueve ningún par. Pesos altos (D) dan lo mismo que B: no hay ningún par cuyo
  veredicto dependa de que el peso sea 0,10 o 0,15.
- Los 8 FN no cambian en ninguna configuración, y se sabe por qué cada uno: los 4 pares de NIT con
  ceros a la izquierda son candidatos pero el filtro de NIT distinto los descarta **antes** de sumar
  las extras (aun con correo y teléfono idénticos, +0,60 en L); la errata doble
  (`ADUANAS-000003`/`CRM-000059`) puntúa 0,4585 ≥ 0,45 y la corta la cobertura por estrellas, que
  solo mira el nombre, y además ese registro no trae contacto; el mojibake solo comparte la
  dirección y no alcanza; y las dos siglas sin NIT nunca son candidatas por nombre. Con EMAIL como
  llave de bloqueo (K) la sigla `ADUANAS-000172`/`CRM-000182` (mismo correo) sí entra como
  candidata (3.566 → 3.567), pero el filtro `min_name_similarity` 0,30 la descarta antes de las
  extras, con cualquier peso (L). La otra sigla comparte el teléfono solo en dígitos
  (`576078095288` / `6078095288`): una llave exacta no lo ve.

**Hallazgos del motor** (escritos aquí y en la docstring del script; no se arregla nada en silencio):

1. Con `col_ciudad="MUNICIPIO"` el motor renombra la columna a `CIUDAD` antes de validar las
   variables extra: `{"column": "MUNICIPIO"}` lanza `ValueError`. El script traduce y lo anota.
2. El scorer solo avisa por log si una columna extra no llega a su DataFrame, y `silenciar_pipeline`
   apaga ese log al medir. El script comprueba las columnas en `L1_prep/data.parquet`
   (`columnas_extra_en_scorer`: todas llegan).
3. Las variables extra se suman **después** del filtro de nombre (`min_name_similarity` 0,30) y del
   filtro de NIT distinto (`engine/scorer.py`, `valid_pairs_mask`): no pueden rescatar un par que
   esos filtros descartan, y la cobertura por estrellas del régimen sin NIT tampoco las ve.
4. `categorical` (no firmado) devuelve 0,5 si falta un lado: premia el dato ausente.
5. `llaves_bloqueo` es igualdad exacta de la columna; una llave que no existe solo deja un aviso.
6. La docstring de `matching/puente_campos.py` muestra `extra_features=[("FECHA", "tipo_fecha", 0.10)]`;
   `_normalizar_extra_features` rechaza tuplas. Use dicts.

**Recomendación** (regla del proyecto: precisión antes que recall): **`B_contacto`** —
`[{"column": "EMAIL", "weight": 0.10, "type": "email_signed"}, {"column": "TELEFONO", "weight": 0.10, "type": "telefono_signed"}]`.
Es la de mayor precisión (0,9937) con el mismo recall y la más simple de las empatadas; municipio y
dirección no cambian nada aquí y E y F muestran cómo pueden unir de más. Determinismo: dos
subprocesos con `PYTHONHASHSEED` distinto dan la misma huella (`714c1cc4…`) y las mismas cifras.

**Lo que esto NO prueba y lo que le falta a la plantilla.** Es sintética: los correos de un mismo
grupo son idénticos y ningún negativo comparte contacto, así que la precisión de contacto sale
perfecta por construcción. Para que la plantilla pruebe multicriterio de verdad faltan casos que
hoy no tiene: (MC01) sin NIT, nombre con dos erratas o nombre comercial distinto, mismo correo y
teléfono, con similitud de nombre ≥ 0,30 (rescate de recall); (MC02) entidades distintas que
comparten teléfono o correo —el contador, el centro comercial— (riesgo de FP de contacto);
(MC03) sucursal con teléfono y dirección distintos (extras en contra de un positivo); (MC04) pares de
nombre entre 0,40 y 0,50 con contacto a favor y en contra (sensibilidad al peso); (MC05) SITIO_WEB y
CIIU con datos. Y etiquetar en `ETIQUETAS_PARES` los pares que las extras mueven (hoy `UNCLEAR` o
ausentes, como `CRM-000051`/`CRM-000161` y `ADUANAS-000029`/`RUES-000057`). La certificación sale de
la base real y sus etiquetas, no de aquí.

Reproducir: `python scripts/multicriterio_plantilla.py --datos data/ejemplos --dir-trabajo /tmp/multicriterio --salida docs/evidencia/plantilla_base_real_multicriterio.json`
(unos 40 s; `--configuraciones mis_configs.json` para otras variables y pesos, `--determinismo 0`
para saltar los subprocesos).

## 6. Qué no es esta plantilla

- **No es una prueba multicriterio completa.** Ningún negativo comparte correo o teléfono y ningún FN
  es rescatable por las variables extra (§5.1): mide la fontanería y la dirección del efecto, no el
  riesgo real de las variables de contacto.
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

## 8. Prueba multicriterio: cómo correrla (notebook 13 y `evaluation.multicriterio`, 2026-10-14)

Multicriterio = el motor usa, además del NIT y la razón social, otras columnas como evidencia
(§5.1 tiene la medición completa y los hallazgos del motor; aquí está cómo repetirla y cómo
aplicarla a su base). Desde 2026-10-14 la lógica vive en el paquete, una sola vez:

| Qué | Dónde |
|---|---|
| Leer la plantilla (hojas `FUENTE_*` por prefijo, del Excel o de los CSV), correr el motor y medir por casos y por pares | `src/record_linkage/evaluation/plantilla_base_real.py` (`leer_plantilla`, `correr_motor`, `evaluar_casos`, `metricas_pares`) |
| Configuraciones, validación, aporte por variable, medición de cada corrida, pares que cambian con sus valores, exportación | `src/record_linkage/evaluation/multicriterio/` (paquete de cuatro módulos ≤ 600 líneas: `configuraciones.py`, `datos.py`, `medicion.py`, `informe.py`; todo se importa desde el paquete: `comparar_off_on`, `medir_configuracion`, `tabla_pares_que_cambian`, `exportar_comparacion`, `descripcion_tipos`) |
| El notebook: OFF contra ON con UNA configuración editable, sobre la plantilla y sobre su Excel | `notebooks/13_multicriterio_plantilla_base_real.ipynb` |
| El script: doce configuraciones de una vez, con determinismo en subprocesos | `scripts/multicriterio_plantilla.py` (envoltorio delgado) |
| Las pruebas (36: cifras de §5.1 reproducidas, traducción `MUNICIPIO` → `CIUDAD`, validación, ids únicos, VERDAD_GRUPOS mínima, aviso sin NIT/MUNICIPIO, columnas en el scorer, sin verdad, exportación, subprocesos) | `tests/test_multicriterio.py`; el notebook corre entero en `tests/test_notebooks_contrato.py` (prueba `slow`) |

### 8.1 En el notebook 13

Celda A (entorno: instala desde la rama `main` si el paquete instalado no trae el módulo —el tag
`v0.22.4` no lo tiene— con `--force-reinstall --no-deps`, porque pip no reinstala una URL git con la
misma versión ya instalada; exige versión ≥ 0.22.4 con el módulo presente, no igualdad; y hasta que
el PR de la rama `claude/multicriterio-plantilla` se fusione, `main` no trae el módulo: la celda lo
detecta y dice qué cambiar), Celda A1 (`RUTA_DATOS`, `RUTA_RESULTADOS`, `RUTA_TRABAJO`; nada en
Drive; la carpeta de datos la crea la primera celda EJECUTAR) y Celda B, lo único que se edita:

```python
ARCHIVO = "plantilla_base_real.xlsx"          # su Excel con hojas FUENTE_* dentro de RUTA_DATOS
VARIABLES_EXTRA = [
    {"column": "EMAIL", "weight": 0.10, "type": "email_signed"},
    {"column": "TELEFONO", "weight": 0.10, "type": "telefono_signed"},
]
NOMBRE_CORRIDA = "multicriterio"
```

Luego cuatro secciones: (1) la tabla de comparadores registrados (`descripcion_tipos()`, generada
desde el registro de la librería; los firmados son los que marca `FIRMADO = sí`: todos los `*_signed`
y los `tipo_*` salvo `tipo_conjunto`, `tipo_geo` y `tipo_numerico`, que solo premian) y una tabla
columna → tipo / peso sugerido con la regla de pesos (0,05–0,10 por variable, suma ≤ 0,30; subir el
peso no es una prueba); (2) la demo sobre la plantilla —la localiza en `RUTA_DATOS`, en un checkout
o la descarga de GitHub (`main`)—, con el resumen OFF/ON y su leyenda, las columnas extra que vio el
motor, la tabla contra `ETIQUETAS_PARES` por régimen con Wilson 95 %, el aporte de cada variable
(con `*_PREMIA_UN_LADO_VACIO`, que delata a `categorical`) y los pares que cambian de decisión con
los valores de las dos filas (`RAZON_SOCIAL_1/_2`, `NIT_1/_2`, `MUNICIPIO_1/_2`, `TELEFONO_1/_2`,
`EMAIL_1/_2`, `DIRECCION_1/_2` y las columnas de sus extras); (3) lo mismo sobre su Excel: la
lectura exige `ID_REGISTRO_FUENTE` único en todas las hojas y `RAZON_SOCIAL`, avisa si falta `NIT` o
`MUNICIPIO` y dice antes de correr qué cifras va a haber —con `VERDAD_GRUPOS` (mínimo
`ID_REGISTRO_FUENTE` e `ID_GRUPO`), precisión y recall por pares; con la columna `CASO` y la hoja
`CASOS`, además la tabla de casos y el aporte; con `ETIQUETAS_PARES`, P y R globales en el resumen y
por régimen con intervalos de Wilson 95 % en la tabla aparte y en el JSON (clave `etiquetas_pares`);
sin verdad, solo los pares que cambian, que es lo que una persona revisa—; (4) la exportación:
`<NOMBRE_CORRIDA>.xlsx` con las hojas `RESUMEN`, `PARES_QUE_CAMBIAN`, `CORRELATIVA_ON`,
`CORRELATIVA_OFF` y, con verdad, `CASOS` y `APORTE`, más `<NOMBRE_CORRIDA>.json` (cifras, ids de
FP/FN y de los pares que cambian, configuración exacta que recibió el motor; ningún dato de
registro); si no corrió la sección 3, la celda lo dice en vez de fallar con `NameError`. Las columnas
se declaran con el nombre de su archivo (`MUNICIPIO`): la librería las traduce a lo que el motor ve
(`CIUDAD`), comprueba por archivo que llegaron al scorer, lo imprime y **se detiene** si alguna no
llegó (el aviso del motor es un log que la medición apaga). «Interpretación y límites» cierra con
qué hacer con cada fila de `PARES_QUE_CAMBIAN` (se une / se separa × verdadero sí / no / vacío) y
un glosario de la jerga (scorer, parquet de L1, régimen, singleton, Wilson, huella).

Desde Python, sin notebook:

```python
from record_linkage.evaluation.multicriterio import comparar_off_on, exportar_comparacion, leer_etiquetas_de
from record_linkage.evaluation.plantilla_base_real import leer_plantilla

libro = leer_plantilla("data/ejemplos")                       # o "mi_base.xlsx"
resultado = comparar_off_on(libro, leer_etiquetas_de("data/ejemplos"), VARIABLES_EXTRA, "/tmp/trabajo")
resultado.resumen; resultado.pares_que_cambian; resultado.aporte
exportar_comparacion(resultado, "salida", "multicriterio")
```

### 8.2 Las cifras (plantilla de `data/ejemplos`, pares contra `VERDAD_GRUPOS`; detalle en §5.1)

| Configuración | Variables extra | Grupos | P | R | F1 | FP | FN | Casos |
|---|---|---|---|---|---|---|---|---|
| `A_base` | ninguna | 281 | 0,9875 | 0,9754 | 0,9814 | 4 | 8 | 18/24 |
| **`B_contacto`** (recomendada) | EMAIL 0,10 `email_signed` + TELEFONO 0,10 `telefono_signed` | 283 | **0,9937** | 0,9754 | 0,9845 | 2 | 8 | 20/24 |
| `C_contacto_geo`, `D_contacto_geo_alto`, `J_todas`, `K_llave_email`, `L_llave_email_pesos_altos` | B más municipio, dirección, departamento, CIIU, sitio web, pesos 0,15/0,30 o EMAIL como llave de bloqueo | 283 | 0,9937 | 0,9754 | 0,9845 | 2 | 8 | 20/24 |
| `E_solo_geo` | MUNICIPIO 0,10 `categoria_tolerante_signed` | 282 | 0,9906 | 0,9754 | 0,9829 | 3 | 8 | 19/24 |
| `G_solo_email` / `H_solo_telefono` | una sola variable firmada 0,10 | 282 | 0,9906 | 0,9754 | 0,9829 | 3 | 8 | 19/24 |
| `I_solo_direccion` | DIRECCION 0,10 `tipo_direccion` | 281 | 0,9875 | 0,9754 | 0,9814 | 4 | 8 | 18/24 |
| `F_no_firmados` | las de C con `categorical` / `token_set_ratio` | 279 | 0,9814 | 0,9754 | 0,9784 | 6 | 8 | 18/24 |

Contra `ETIQUETAS_PARES` las doce dan lo mismo (P 0,9859 / R 0,9211): los pares movidos están
`UNCLEAR` o sin etiquetar. Determinismo: dos subprocesos con `PYTHONHASHSEED` distinto, misma
huella (`714c1cc4…`). Evidencia: `docs/evidencia/plantilla_base_real_multicriterio.json`. Sobre las
huellas: la del banco (`b847d835…`, `scripts/banco.py` sobre `plantilla_banco.csv`, §5) y la de
`huella_particion` del módulo (`d858130f…` para la base, `714c1cc4…` para la recomendada) miden la
misma partición con funciones distintas; la partición base no cambió.

**Recomendada** (regla del proyecto, precisión antes que recall; empate → más recall; empate → menos
variables): `B_contacto`, la que trae la Celda B del notebook. Separa los dos falsos positivos de borde
(`CRM-000051`/`CRM-000161`, dominio de correo distinto; `CRM-000037`/`RUES-000016`, teléfono distinto)
y no mueve ningún par verdadero.

**Límites de lo que esto prueba**: la plantilla es sintética; los correos de un mismo grupo son
idénticos y ningún negativo comparte correo ni teléfono, así que la precisión del contacto sale
perfecta por construcción; ningún FN es rescatable por las extras (los filtros de nombre y de NIT van
antes; la cobertura por estrellas solo mira el nombre); 0,10 y 0,15 de peso dan lo mismo;
`TRUVEL`/`TRUVELA` no se separa con nada (ADUANAS sin contacto); CIIU y SITIO_WEB casi no tienen con
qué medirse. El riesgo real del contacto (contador común, teléfono de centro comercial) y la
certificación salen de la base real y sus etiquetas, con el mismo notebook. Lo que le falta a la
plantilla (casos MC01–MC05 y etiquetas de los pares de borde) está en §5.1.
