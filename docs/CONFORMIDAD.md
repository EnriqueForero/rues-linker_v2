# La suite de conformidad — 43 casos, veredicto por caso

> **Qué es.** `data/conformidad/` — 207 registros, 155 pares etiquetados,
> 43 casos frontera, 13 columnas y 11 tipos de campo. Extraído de
> `Ground_Truth_Multicampo_v1.xlsx` y versionado.
>
> **Para qué.** El banco (`docs/BANCO.md`) dice **cuánto** mejoró. Esta suite
> dice **qué sabe hacer** la librería. Un caso reprobado reprueba la corrida,
> sin promediarse con nada.

```bash
python scripts/conformidad.py                    # dedup + linkage, motor multicampo
python scripts/conformidad.py --corroborar       # con levantamiento de veto (F3)
python scripts/conformidad.py --camino linkage   # los mismos 43 por api.linkage() (F2.13)
python scripts/conformidad.py --camino flujo.cruce   # … y por flujo.cruce con arrastre
python scripts/conformidad.py --camino todos
python scripts/conformidad.py --escenario dedup --etiqueta mi_cambio
python scripts/conformidad.py --importar ruta/Ground_Truth_Multicampo_v1.xlsx
```

Devuelve 0 si todos los casos **firmes** aprueban, 1 si alguno reprueba. Los
casos que el catálogo marca `TP_DIFICIL` son deuda declarada, se listan aparte
y no reprueban. **Cada camino tiene su propia línea base** (§4): el veredicto
del camino de producción se registra tal cual, no se ajusta el motor para
cuadrarlo.

> **Dónde busca el conjunto (v0.22.1).** El conjunto vive en el repositorio,
> no dentro del paquete, así que la ruta no se puede derivar de la ubicación
> del módulo: con la rueda instalada eso apunta a `site-packages` y el conjunto
> desaparece. `localizar_conjunto()` resuelve por capas, de lo más explícito a
> lo más adivinado:
>
> 1. `RUES_LINKER_CONFORMIDAD`, tal cual y sin comprobarla — si el operador la
>    define y es incorrecta, el error debe verse.
> 2. `DIRECTORIO_POR_DEFECTO` si contiene el conjunto (checkout con `src/`).
> 3. Búsqueda hacia arriba desde el directorio de trabajo, buscando
>    `data/conformidad/` (paquete instalado; pytest y los scripts corren desde
>    la raíz del repositorio).
> 4. Si nada aparece, la constante, para que el error nombre una ruta.
>
> Un directorio se reconoce por `dedup_registros.csv`, no por existir: un
> `data/conformidad/` vacío pasaría la comprobación y fallaría lejos de la
> causa. `scripts/conformidad.py` no usa nada de esto — ancla `--datos` a su
> propia raíz, que siempre conoce.
>
> **Por qué importa.** Hasta la 0.22.0, con el paquete instalado el fixture
> capturaba `FileNotFoundError` y hacía `pytest.skip`: **8 casos se saltaban en
> silencio** y la compuerta reportaba verde sin medir. Una prueba que falla
> avisa; una que se salta, no.

---

## 1. Por qué hacen falta dos instrumentos

Con 46.374 pares en el banco, un comportamiento roto que afecta a cuatro casos
mueve el F1 en la cuarta cifra decimal. **No es que se note poco: no se nota
nunca.** Y son exactamente los que en producción producen una fusión
escandalosa:

- dos empresas distintas en la misma torre de oficinas (misma geo);
- dos empresas distintas que tercerizan el mismo call center;
- dos empresas distintas con correo `@gmail.com`;
- una persona natural y una empresa con nombre casi igual.

La pregunta "¿mejoró?" y la pregunta "¿sabe hacer esto?" son distintas, y
ningún promedio responde la segunda.

| | banco | conformidad |
|---|---|---|
| pregunta | ¿mejoró? | ¿sabe hacerlo? |
| naturaleza | estadística, variación real | 43 casos diseñados |
| registros | 30.486 | 207 |
| falla si | una métrica retrocede | **un solo caso** reprueba |
| responde bien | "el recall subió 0,6 puntos" | "no sabe separar homónimos" |
| responde mal | "no sabe separar homónimos" | "el recall subió 0,6 puntos" |

## 2. Evaluación contra construcción: particiones, no dos conjuntos

Es la pregunta natural y tiene respuesta conocida: se parte **el mismo**
conjunto por grupo, se calibra en unos pliegues y se verifica en otro.

```bash
for k in 0 1 2; do
  python scripts/banco.py --datos data/benchmark/benchmark_institucional.csv.gz \
      --etiqueta fold${k} --pliegue $k --pliegues 3
done
```

El reparto es **por grupo, nunca por fila**: partir por fila rompería grupos
verdaderos y haría imposible medir recall. Así se validó ADR-0004 fuera de
muestra, y el resultado coincidió con el de la muestra completa hasta la
cuarta cifra.

Dos conjuntos separados tendrían el defecto contrario: sus cifras no serían
comparables entre sí, que es justo lo que ADR-0001 existe para evitar.

## 3. Qué cubre el catálogo

**Deduplicación (27 casos firmes + 2 frontera).** Match exacto cross-source;
NIT idéntico con variación leve, sufijo legal, nombre truncado, prefijo C.I.,
ruido administrativo, orden de palabras y formato de NIT; homónimos; NIT
idéntico con nombres muy distintos; persona contra empresa; cadena transitiva
con veto; fuente confiable que no se deduplica; encoding y tildes; nombres muy
cortos y muy largos; datos faltantes; grupo excesivamente grande; score en la
frontera del umbral; stopwords críticas; singleton sin match; y cinco casos
multicampo (`MC01`, `MC04`–`MC07`).

**Record linkage (13 casos firmes + 1 frontera).** Cruce exacto, con sufijo,
comercial contra legal, truncado, prefijo C.I., orden alterado, NIT
formateado, typo, sin tildes, abreviado; solo en A y solo en B; homónimo
cross-base; y cruce sin NIT en A.

> **Seis casos sin pares declarados, juzgados por registro (F2.13).** Las
> hojas de pares del libro nombran 37 de los 43 casos. `C20` (datos
> faltantes), `C24` (stopwords críticas), `C25` (ocho singletons), `solo_A`,
> `solo_B` y `homonimo_cross` aciertan cuando el registro **no se une a
> nadie**, y «con nadie» no se puede enumerar en una hoja de pares. Hasta
> F2.13 quedaban sin veredicto: verde sin haber medido. Ahora cada registro
> del caso se juzga contra la columna de verdad (`ID_GRUPO_ESPERADO` /
> `ENTIDAD_ID_ESPERADO`): acierta si el grupo que le tocó tiene exactamente
> los miembros que la verdad dice (para el cruce: si cruza exactamente con
> los registros del otro lado que la verdad manda, ninguno si la entidad es
> negativa). El JSON lo declara en `origen` (`pares_declarados` /
> `verdad_por_registro`) y el resumen los marca `·reg`; los 37 con pares se
> evalúan exactamente igual que antes. Un singleton que absorbe a un ajeno
> reprueba, y es firme: `C25` es `TN`, no deuda.

**Los dos casos frontera** que el libro original declaraba pendientes:

- **C09** — el NIT difiere en un dígito y el nombre es idéntico. Un error de
  captura, no dos entidades.
- **C21** — la entidad tiene dos NIT legítimos por reestructuración societaria.

## 4. Línea base, por camino

Los mismos 43 casos corren por **tres caminos** del motor (`--camino`), y la
evidencia registra con cuál se midió (`camino` en el JSON y en el nombre del
archivo: `conformidad_<escenario>[_<camino>]_<etiqueta>.json`; el camino
histórico conserva los nombres de F0).

| camino | qué es | escenario | precisión | recall | F1 | casos firmes |
|---|---|---|---:|---:|---:|---|
| `dedupe_esquema` | `api.dedupe_esquema`, motor multicampo, esquema derivado del DICCIONARIO del libro | deduplicación | 1,0000 | 0,9847 | 0,9923 | 27/27 |
| `dedupe_esquema` **con `--corroborar`** | ídem, con levantamiento de veto por contacto (F3) | deduplicación | 1,0000 | **1,0000** | **1,0000** | 27/27 |
| `dedupe_esquema` | | record linkage | 1,0000 | 1,0000 | 1,0000 | 13/13 |
| `linkage` | `api.linkage()`, la fachada de producción (Orchestrator L1…L5, perfil `produccion_estandar`) | deduplicación | 1,0000 | 0,9695 | 0,9845 | **23/27** |
| `linkage` | | record linkage | 1,0000 | 1,0000 | 1,0000 | 13/13 |
| `flujo.cruce` | `flujo.cruce.ejecutar_cruce` sobre CSV por fuente, con columnas de arrastre (`DIRECCION`, `DEPARTAMENTO`, `SECTOR`, `REG_ID`) que viajan fuera del motor y se re-adjuntan | deduplicación | 1,0000 | 0,9695 | 0,9845 | **23/27** |
| `flujo.cruce` | | record linkage | 1,0000 | 1,0000 | 1,0000 | 13/13 |

Hasta F2.13 los firmes eran 24/24 y 10/10: son los mismos casos más los seis
juzgados por registro (§3); ningún veredicto de los 37 anteriores cambió. La
primera fila reproduce **exactamente** la cifra que publica la hoja LEEME del
libro, medida con la versión 0.10.0 del motor. Que dos implementaciones
independientes den el mismo número es la mejor evidencia de que el instrumento
mide lo que dice medir. `flujo.cruce` da **exactamente** la misma partición que
`linkage` en los dos escenarios: el arrastre de columnas no mueve ningún grupo,
que es lo que ese camino mide.

### Cómo se monta el camino de producción

`linkage()` entiende la confianza **por fuente** (una fuente confiable no se
deduplica internamente); el libro la declara **por fila** (`ES_TRUSTED`): el
bloque `GEN` trae registros RUES y SUPERSOCIEDADES marcados `False` que **sí**
deben unirse entre sí. Marcar RUES entera como confiable reprobaría `GEN`; no
marcarla dejaría `C15` sin la política que mide. La proyección fiel es partir
las fuentes declaradas confiables por el DICCIONARIO (RUES, SUPERSOCIEDADES)
en dos: las filas verificadas con su nombre y las demás en
`<SRC>_SIN_VERIFICAR` (`scripts/conformidad.py::fuentes_por_confianza`). La
evidencia lo registra en `extra.fuentes` y `extra.confiables`. Evidencia
adicional: `CIUDAD` (`col_ciudad`) y `TELEFONO`/`EMAIL` con los comparadores
`telefono_signed`/`email_signed`, peso 0,10 cada uno. En el cruce A↔B, CRM y
RUES son las fuentes y RUES la confiable.

### Veredicto de los 43, por camino

`par` = pares de la hoja; `reg` = registros juzgados contra la verdad (§3).
🟡 = frontera (`TP_DIFICIL`, no reprueba). Generado desde los JSON de
`docs/evidencia/`; `tests/test_conformidad_caminos.py` exige que los 43 estén.

| caso | tipo | unidad | `dedupe_esquema` | `dedupe_esquema --corroborar` | `linkage` | `flujo.cruce` |
|---|---|---|:--:|:--:|:--:|:--:|
| `C01` | TP | par | ✅ 3/3 | ✅ 3/3 | ✅ 3/3 | ✅ 3/3 |
| `C02` | TP | par | ✅ 6/6 | ✅ 6/6 | ✅ 6/6 | ✅ 6/6 |
| `C03` | TP | par | ✅ 6/6 | ✅ 6/6 | ✅ 6/6 | ✅ 6/6 |
| `C04` | TP | par | ✅ 3/3 | ✅ 3/3 | ✅ 3/3 | ✅ 3/3 |
| `C05` | TP | par | ✅ 3/3 | ✅ 3/3 | ✅ 3/3 | ✅ 3/3 |
| `C06` | TP | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `C07` | TP | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `C08` | TP | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `C09` | TP_DIFICIL | par | 🟡 0/1 | ✅ 1/1 | 🟡 0/1 | 🟡 0/1 |
| `C11` | FP_trap | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `C12` | FP_trap | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `C13` | FP_trap | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `C14` | CHAIN | par | ✅ 2/2 | ✅ 2/2 | ✅ 2/2 | ✅ 2/2 |
| `C15` | TRUSTED | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `C17` | TP | par | ✅ 3/3 | ✅ 3/3 | ✅ 3/3 | ✅ 3/3 |
| `C18` | EDGE | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `C19` | TP | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `C20` | EDGE | reg | ✅ 1/1 | ✅ 1/1 | ❌ 0/1 | ❌ 0/1 |
| `C21` | TP_DIFICIL | par | 🟡 0/1 | ✅ 1/1 | 🟡 0/1 | 🟡 0/1 |
| `C22` | ANOMALY | par | ✅ 28/28 | ✅ 28/28 | ✅ 28/28 | ✅ 28/28 |
| `C23` | BOUNDARY | par | ✅ 1/1 | ✅ 1/1 | ❌ 0/1 | ❌ 0/1 |
| `C24` | EDGE | reg | ✅ 1/1 | ✅ 1/1 | ❌ 0/1 | ❌ 0/1 |
| `C25` | TN | reg | ✅ 8/8 | ✅ 8/8 | ✅ 8/8 | ✅ 8/8 |
| `MC01` | TP | par | ✅ 1/1 | ✅ 1/1 | ❌ 0/1 | ❌ 0/1 |
| `MC04` | FP_trap | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `MC05` | FP_trap | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `MC06` | FP_trap | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `MC07` | TP | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `GEN` | MIXTO | par | ✅ 69/69 | ✅ 69/69 | ✅ 69/69 | ✅ 69/69 |
| `exacto` | TP | par | ✅ 3/3 | ✅ 3/3 | ✅ 3/3 | ✅ 3/3 |
| `sufijo` | TP | par | ✅ 2/2 | ✅ 2/2 | ✅ 2/2 | ✅ 2/2 |
| `comercial_vs_legal` | TP | par | ✅ 2/2 | ✅ 2/2 | ✅ 2/2 | ✅ 2/2 |
| `truncado` | TP | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `prefijo_ci` | TP | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `orden` | TP | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `nit_formato` | TP | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `sin_nit_en_A` | TP_DIFICIL | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `typo` | TP | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `tildes` | TP | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `abreviado` | TP | par | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 | ✅ 1/1 |
| `solo_A` | TN | reg | ✅ 4/4 | ✅ 4/4 | ✅ 4/4 | ✅ 4/4 |
| `solo_B` | TN | reg | ✅ 5/5 | ✅ 5/5 | ✅ 5/5 | ✅ 5/5 |
| `homonimo_cross` | FP_trap | reg | ✅ 2/2 | ✅ 2/2 | ✅ 2/2 | ✅ 2/2 |

### Lo que el camino de producción no sabe hacer hoy (medido en F2.13)

Cuatro casos firmes reprueban por `linkage`/`flujo.cruce` y pasan por el motor
multicampo. Se registran tal cual —esta tarea no ajusta el motor— y son
insumo de F2 (cobertura por estrellas, genéricos) y de F3:

- **`C20` + `C24`, una fusión escandalosa.** El registro 56 (`EMPRESA SAS`,
  sin NIT, CRM) y el 57 (`DE LA Y SAS`, NIT 9004344457, RUES) acaban en el
  **mismo grupo**: dos empresas distintas unidas. Al limpiar el nombre, uno
  queda en un genérico (`EMPRESA`) y el otro en stopwords (`DE LA Y`), y lo
  que sobrevive se parece. Es la clase de error que la regla de Enrique
  castiga más —unir dos empresas distintas duele más que dejar sin unir— y el
  motor multicampo no lo comete. Los dos casos reprueban juntos porque los
  dos registros son los que se unen.
- **`MC01`.** Sin NIT en los dos registros, correo y teléfono idénticos: el
  multicampo los une; `linkage()` no, porque en el régimen sin identificador
  el peso de `TELEFONO`/`EMAIL` (0,10 cada uno) no alcanza para que
  `SOLUCIONES TECNOLOGICAS APLICADAS SAS` y `SOLUCIONES TEC APLICADAS` pasen
  el umbral.
- **`C23`.** `PROYECTOS Y DISEÑOS URBANOS SAS` contra `PROYECTOS URBANOS`
  (nombre parcial, sin NIT, mismo teléfono y dirección): score en la frontera;
  el multicampo lo une, `linkage()` lo deja fuera.

> **Regla para `--corroborar` en el camino de producción.** La corroboración
> por contacto (F3) vive **solo** en el motor multicampo. `linkage()` y
> `flujo.cruce` no tienen esa perilla, así que `scripts/conformidad.py` **se
> niega** a correr `--corroborar` con `--camino linkage` o `flujo.cruce`
> (error con qué pasó / por qué importa / qué hacer): una evidencia con
> `corroborar: true` sobre un motor que no la tiene mentiría. En esos caminos
> `C09` y `C21` reprueban **siempre** y cuentan como frontera, sin flag que lo
> cambie, hasta que F3 lleve la corroboración a la librería.

### El levantamiento de veto (F3)

`--corroborar` activa una regla que ya existía y estaba apagada: dos
identificadores distintos se reúnen **solo si** hay evidencia independiente
fuerte —correo y teléfono idénticos— y el nombre es casi igual. Resuelve C09 y
C21 sin perder ni una de las seis trampas de falso positivo.

> **Regla, no flag opcional.** C09 y C21 **solo pasan con la corroboración por
> contacto activa** (`--corroborar`); sin ella reprueban y cuentan como frontera
> (deuda declarada, la corrida sigue en PASA). Desde esta versión la evidencia
> publicada registra con qué se midió: `conformidad_{dedup,linkage}_base.json`
> es la corrida **sin** corroborar y `..._corroborado.json` la corrida **con**,
> y cada JSON lleva `corroborar` (true/false) y `camino` (`dedupe_esquema`,
> `linkage` o `flujo.cruce`; los dos últimos van también en el nombre del
> archivo: `conformidad_dedup_linkage_base.json`…). Una evidencia sin esos
> campos no dice qué midió; `test_conformidad_evidencia.py` y
> `test_conformidad_caminos.py` lo exigen.

**Advertencia honesta sobre el parámetro.** `min_corroborantes=2` se eligió por
criterio de riesgo, no porque esta suite lo respalde: con 1 también da
F1 = 1,0000 y ninguna trampa se cae. Lo que sostiene las trampas no es el
número de corroborantes, sino que sus comparadores ya devuelven 0 ante valores
de baja entropía —un `@gmail.com` compartido, un teléfono de call center— más
la exigencia de similitud de nombre. Cuál conviene en datos reales está
pendiente de medir (C35).

## 5. Los 11 tipos de campo que ejercita

`identificador`, `nombre_empresa`, `nombre_persona`, `email`, `telefono`,
`direccion`, `ciudad`, `categorico`, `geo`, `fecha`, `numerico`.

Esos tipos son los que destaparon la deuda de ADR-0007: cinco de ellos
existían en el motor declarativo y **no** en el catálogo que usa el scorer de
producción. Hoy los 24 comparadores están en un solo catálogo.

## 6. Reglas del conjunto

1. **No se regenera sin acta.** Es el referente de comparación; si cambia, las
   métricas históricas dejan de ser comparables. La regla viene del libro
   original y se respeta.
2. **Se valida al cargar.** Si la hoja de pares y la columna de grupo se
   contradicen, `cargar_conjunto` lanza un error. En 0.19.0 el otro conjunto
   sí se contradecía y costó un diagnóstico entero descubrirlo; esta
   comprobación existe para que no vuelva a pasar.
3. **Un caso reprobado reprueba.** No se promedia.
4. **Los `TP_DIFICIL` no reprueban pero se cuentan.** Separarlos impide que un
   caso realmente roto se esconda entre las fronteras conocidas.
5. **Los 43 tienen veredicto, siempre.** Los seis casos sin pares declarados
   se juzgan por registro contra la columna de verdad (§3) y el JSON dice de
   qué regla salió cada veredicto (`origen`). Un caso sin fila es un caso sin
   medir, y eso ya pasó una vez.
6. **El camino va en la evidencia.** `camino` es uno de `dedupe_esquema`,
   `linkage`, `flujo.cruce` (un nombre libre se rechaza) y cada camino tiene
   su línea base. Un caso que da distinto entre caminos se registra con su
   explicación (§4); el motor no se ajusta desde la suite.

## 7. Lo que esta suite NO mide

- **Escala.** 207 registros. Para eso está el banco y la corrida de 5 M.
- **Poder estadístico.** Un caso son uno o dos pares: sirve para decir "sabe" o
  "no sabe", nunca para decir "mejoró un 2 %".
- **Variación real de nombre.** Los casos están diseñados; el 21,7 % de
  enlaces sin ningún token en común que trae el mundo real está en el banco.
- **Direcciones con formatos distintos.** Todas las del conjunto siguen el
  patrón `CALLE 11 # 2 - 11`; la suite no distinguió entre dos
  normalizaciones de dirección que sí difieren en datos reales.

---

*Autor: Claude (asesor de Enrique Forero) · 2026-08-29 · v0.20.0 · caminos de producción (F2.13): 2026-10-06*
