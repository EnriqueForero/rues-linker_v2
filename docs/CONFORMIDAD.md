# La suite de conformidad — 43 casos, veredicto por caso

> **Qué es.** `data/conformidad/` — 207 registros, 155 pares etiquetados,
> 43 casos frontera, 13 columnas y 11 tipos de campo. Extraído de
> `Ground_Truth_Multicampo_v1.xlsx` y versionado.
>
> **Para qué.** El banco (`docs/BANCO.md`) dice **cuánto** mejoró. Esta suite
> dice **qué sabe hacer** la librería. Un caso reprobado reprueba la corrida,
> sin promediarse con nada.

```bash
python scripts/conformidad.py                    # dedup + linkage
python scripts/conformidad.py --corroborar       # con levantamiento de veto (F3)
python scripts/conformidad.py --escenario dedup --etiqueta mi_cambio
python scripts/conformidad.py --importar ruta/Ground_Truth_Multicampo_v1.xlsx
```

Devuelve 0 si todos los casos **firmes** aprueban, 1 si alguno reprueba. Los
casos que el catálogo marca `TP_DIFICIL` son deuda declarada, se listan aparte
y no reprueban.

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

**Deduplicación (24 casos firmes + 2 frontera).** Match exacto cross-source;
NIT idéntico con variación leve, sufijo legal, nombre truncado, prefijo C.I.,
ruido administrativo, orden de palabras y formato de NIT; homónimos; NIT
idéntico con nombres muy distintos; persona contra empresa; cadena transitiva
con veto; fuente confiable que no se deduplica; encoding y tildes; nombres muy
cortos y muy largos; datos faltantes; grupo excesivamente grande; score en la
frontera del umbral; stopwords críticas; singleton sin match; y cinco casos
multicampo (`MC01`, `MC04`–`MC07`).

**Record linkage (10 casos firmes + 1 frontera).** Cruce exacto, con sufijo,
comercial contra legal, truncado, prefijo C.I., orden alterado, NIT
formateado, typo, sin tildes, abreviado; solo en A y solo en B; homónimo
cross-base; y cruce sin NIT en A.

**Los dos casos frontera** que el libro original declaraba pendientes:

- **C09** — el NIT difiere en un dígito y el nombre es idéntico. Un error de
  captura, no dos entidades.
- **C21** — la entidad tiene dos NIT legítimos por reestructuración societaria.

## 4. Línea base

Motor multicampo, esquema derivado del propio DICCIONARIO del libro:

| escenario | precisión | recall | F1 | casos firmes |
|---|---:|---:|---:|---|
| deduplicación | 1,0000 | 0,9847 | 0,9923 | 24/24 |
| deduplicación **con `--corroborar`** | 1,0000 | **1,0000** | **1,0000** | 24/24 |
| record linkage | 1,0000 | 1,0000 | 1,0000 | 10/10 |

La primera fila reproduce **exactamente** la cifra que publica la hoja LEEME
del libro, medida con la versión 0.10.0 del motor. Que dos implementaciones
independientes den el mismo número es la mejor evidencia de que el instrumento
mide lo que dice medir.

### El levantamiento de veto (F3)

`--corroborar` activa una regla que ya existía y estaba apagada: dos
identificadores distintos se reúnen **solo si** hay evidencia independiente
fuerte —correo y teléfono idénticos— y el nombre es casi igual. Resuelve C09 y
C21 sin perder ni una de las seis trampas de falso positivo.

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

*Autor: Claude (asesor de Enrique Forero) · 2026-08-29 · v0.20.0*
