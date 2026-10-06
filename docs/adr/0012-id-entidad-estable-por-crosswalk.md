# ADR-0012 — `ID_ENTIDAD` estable entre corridas: herencia por crosswalk, no por posición ni por azar

- **Estado:** aceptada
- **Fecha:** 2026-10-06
- **Versión:** 0.23.0 (F2.4)

## Contexto

`ID_GRUPO` es el id de la corrida: cambia con cualquier fila nueva o
reordenada. El estándar de salida separa ese id interno (volátil) del id
público (`ID_ENTIDAD`) y pide que el público sea **estable**: un consumidor
que guardó `ENT-…` en su sistema debe encontrarlo en la corrida siguiente
aunque el grupo haya ganado o perdido filas, se haya fusionado con otro o se
haya dividido. Es lo que Senzing recomienda (id público propio con
redirecciones al fusionar), lo que Zingg hace con `ZINGG_ID` en su flujo
incremental y lo que Google formaliza como «estabilidad semántica del id».

F1 dejó `NIT-<base canónica>` para los grupos con identificador válido y
`ENT-<16 hex de SHA-256 de los ID_REGISTRO ordenados>` para el resto:
determinista por contenido, pero sin memoria. Cualquier fila nueva en un
grupo sin NIT le cambiaba el id. El plan pedía `ENT-<ulid>` con herencia
por mayor solapamiento desde `entidades_ids.parquet` de la corrida anterior.

## Decisión

1. **Módulo único:** `identidad/estable.py` decide el `ENT-…`, hereda y
   mantiene el crosswalk. `salida/completar.py` solo decide qué grupos llevan
   `NIT-…` (el principal por base, como en F1) y delega el resto. El escritor
   publica el crosswalk que el módulo calculó; si un resultado no lo trae, lo
   deriva de la correlativa con la misma función. Una regla, un sitio.

2. **El crosswalk se basta a sí mismo.** `entidades_ids.parquet` gana la
   columna `REGISTROS` (los `ID_REGISTRO` de la entidad, ordenados y separados
   por `|`). Con ella, la corrida siguiente mide el solapamiento leyendo un
   solo archivo: no hace falta la correlativa anterior ni reconstruir nada.
   El parámetro `corrida_anterior=` (en `linkage()` y en `ConfigCruce`) acepta
   la carpeta del estándar o el archivo, se valida ANTES de L1
   (`CorridaAnteriorError` con qué pasó · por qué importa · qué hacer) y el
   manifiesto declara la ruta y la huella SHA-256 usadas
   (`completar.id_entidad.herencia`, `parametros.corrida_anterior`).

3. **Regla de herencia** (vectorizada: `merge` + `groupby`, ningún bucle por
   fila ni por par):
   - *Destino* de cada `ENT-…` activo de la corrida anterior: el grupo actual
     con más `ID_REGISTRO` compartidos (empate: el grupo mayor, luego el de
     menor `ID_REGISTRO`).
   - *Herencia*: un grupo sin identificador toma, entre las entidades cuyo
     destino es él, la de más registros compartidos (empate: la anterior más
     grande, luego el id menor). Fusión → sobrevive la mayor; división →
     conserva el id la parte con más compartidos y la otra recibe id nuevo.
   - *Retiro*: la entidad anterior cuyo destino quedó con otro id se escribe
     con `RETIRADO_EN = <sobreviviente>`, `N_REGISTROS = 0` y el `ID_GRUPO`
     del sobreviviente. Incluye el caso `ENT-… → NIT-…` (el grupo adoptó un
     identificador). Un `NIT-…` nunca se retira ni se hereda: es natural; si
     el identificador desaparece, el grupo recibe `ENT-…` y el reporte cuenta
     el `NIT-…` ausente.
   - *Arrastre*: los retiros anteriores se copian y las cadenas se comprimen
     (`X → Y`, `Y → Z` ⇒ `X → Z`): cualquier id publicado se resuelve con un
     salto. Una entidad sin ningún registro en esta corrida desaparece del
     crosswalk y el reporte la cuenta.

4. **Id nuevo = id por contenido (no ULID).** Sin corrida anterior el
   resultado es el de F1, byte a byte: misma entrada → mismos ids, sin
   estado. Con corrida anterior, los grupos que no heredan reciben el mismo
   hash por contenido. Un ULID daría orden temporal a cambio de romper esa
   propiedad y de añadir una dependencia; con el crosswalk, la estabilidad ya
   no depende de cómo se genere el id nuevo.

5. **Un id retirado no resucita.** El hash por contenido se compara con todos
   los ids del crosswalk anterior (activos y retirados) y con los de esta
   corrida; si choca —el mismo conjunto de registros volvió a formar grupo
   tras haberse fusionado—, se rehace con un sufijo determinista
   (`<clave>#1`, `#2`…) y el reporte lo cuenta en `colisiones_resueltas`. Sin
   esto, un id que el crosswalk redirige a otro volvería a aparecer activo y
   el consumidor tendría dos verdades.

## Opciones consideradas

- **`ENT-<ulid>` como pide el plan.** Descartada por la nota del coordinador
  y por la razón del punto 4: sin corrida anterior dos corridas de la misma
  entrada darían ids distintos y el banco de determinismo (F0.6) dejaría de
  verificar `ID_ENTIDAD`.
- **Leer la correlativa anterior para medir el solapamiento.** Descartada:
  obliga a conservar dos archivos coherentes y a leer millones de filas para
  obtener lo que cabe en una columna del crosswalk. `REGISTROS` cuesta lo que
  cuesta la correlativa comprimida (ver cifras) y hace el archivo autónomo.
- **Emparejamiento óptimo (asignación) entre grupos y entidades anteriores.**
  Descartada: la regla «mejor destino + mejor entre las que llegan» cubre la
  fila nueva, la fusión y la división con una pasada, es explicable en una
  frase y determinista; un emparejamiento global sería mejor en casos
  patológicos que la práctica no muestra y peor de explicar.
- **Retirar también los `NIT-…`.** Descartada: un NIT que desaparece y
  vuelve debe dar el mismo `NIT-…`; si se retirara, volvería como activo y
  retirado a la vez.

## Consecuencias

- El contrato 1.0 no cambia de versión: `ENTIDADES_IDS` añade `REGISTROS` al
  final (añadir, no reordenar) y `RETIRADO_EN` deja de estar siempre vacío.
  `diccionario.csv` la documenta sola (viene del contrato).
- `ResultadoLinkage.entidades_ids` (opcional) lleva el crosswalk de la corrida;
  `leer_resultado` lo devuelve. `ReporteCompletar.entidades_ids` lo transporta
  desde `completar_correlativa` sin pasar por el manifiesto (es una tabla).
- Un crosswalk escrito antes de F2.4 (sin `REGISTROS`) no sirve para heredar:
  `cargar_corrida_anterior` lo dice y dice qué hacer (correr sin
  `corrida_anterior=`; los ids serán nuevos y deterministas).
- La huella de la partición del banco no cambia: L1…L5 no se tocan. Costo
  medido de `asignar_id_entidad` (grupos sintéticos, 50 % con NIT; segunda
  corrida con 5 % de filas nuevas, 5 % retiradas y 2 % de grupos fusionados):
  30.486 filas / 11.478 grupos → 0,10 s sin herencia, 0,34 s con herencia
  (crosswalk 0,4 MB); 139.028 / 50.000 → 0,24 s y 1,24 s (1,8 MB);
  1.000.000 / 350.000 → 1,8 s y 8,8 s (12,2 MB). Dos trampas medidas y
  evitadas: `groupby().agg("|".join)` costaba 3,0 s a 139k (se une con
  `pyarrow.compute.binary_join`, 0,1 s) y `agg("min")` sobre texto Arrow
  reduce grupo a grupo en Python, 5 s a 139k (el menor `ID_REGISTRO` sale del
  orden).
- Pruebas: `tests/test_identidad_estable.py` congela (a) determinismo con y
  sin corrida anterior, (b) fila nueva, (c) fusión con retiro y desempate,
  (d) división, `ENT → NIT`, arrastre con compresión de cadenas, no
  resurrección, desaparecidas, `NIT-…` no heredable, fail-fast de la carga,
  `linkage(corrida_anterior=…)` de punta a punta y `ConfigCruce`.
