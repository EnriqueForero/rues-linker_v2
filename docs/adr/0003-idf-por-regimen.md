# ADR-0003 · La ponderación IDF se aplica por régimen, no globalmente

**Estado:** aceptado · **Fecha:** 2026-08-29 · **Versión:** 0.18.0

## Contexto

El diagnóstico de errores sobre `ground_truth_grande.csv` con el perfil
`produccion_estandar` fue inequívoco:

- **100 %** de los 519 falsos positivos son pares SIN identificador.
- **79 %** de los 972 falsos negativos también.
- El régimen CON identificador tiene recall 0,9885 y cero falsos positivos.

Es decir: el problema de calidad **es exclusivamente el emparejamiento por
nombre cuando no hay identificador que decida**.

Los errores tienen dos formas:

1. *Falsos positivos* — nombres que comparten tokens genéricos:
   `HWANGJUNG TECH LLC` vs `HANJUNG TECH LLC`, `JINSHIN LOGIS CO.,LTD.` vs
   `SHINSHIN LOGIS CO.,LTD.`. Dos de tres tokens coinciden y el comparador
   plano los une.
2. *Falsos negativos* — errores de espaciado que rompen la tokenización:
   `CHOIMIN GLOBAL CORP` vs `CHOIMING LOBAL INC`, `HANYOO GLOBALCO., LTD` vs
   `HANYOO GLOBAL CO. LTD`. No comparten **ningún** token.

Ya existía una perilla `idf_weight_blend`, pero global y con esta advertencia
escrita en el código: *"⚠️ Este valor DAÑA los datasets con NIT (F1
0,759→0,538)"*. Además su implementación era un bucle de Python sobre los
pares del lote, y el mapa token→IDF tenía que proveerlo el llamador, cosa que
el Orchestrator nunca hacía: la perilla era un no-op por ese camino.

## Decisión

Tres cambios:

1. **`idf_weight_blend_sin_identificador`** — mezcla que se aplica solo a los
   pares donde ningún lado tiene identificador utilizable (≥ 6 dígitos, no
   centinela). Donde el identificador decide, el nombre es corroboración y
   re-pesarlo solo agrega ruido.
2. **Cálculo vectorizado** — `matching/idf.py` construye la matriz de
   incidencia dispersa del corpus una vez y resuelve la intersección ponderada
   como una multiplicación. La frecuencia es **documental**: un token repetido
   dentro del mismo nombre cuenta una vez, para que `SEGUROS SEGUROS DEL SUR`
   no haga parecer genérico a `SEGUROS`.
3. **`similitud_compacta_min`** — un piso de Jaro-Winkler sobre el nombre sin
   espacios, que solo puede SUBIR la similitud. Ataca la segunda forma de
   error, que el IDF no toca.

## Evidencia

Barrido completo en `docs/BITACORA.md`. Lo esencial:

| configuración | precision | recall | F1 | recall SIN_NIT |
|---|---|---|---|---|
| base (`produccion_estandar`) | 0,9760 | 0,9560 | 0,9659 | 0,8344 |
| IDF global 0,50 | **1,0000** | 0,8562 | 0,9225 | 0,3617 |
| solo bloqueo abierto (`lsh 0,45`) | 0,9421 | 0,9764 | 0,9590 | 0,9313 |
| **las tres perillas juntas** | **0,9866** | **0,9733** | **0,9799** | **0,9165** |

Ninguna de las tres mejora el F1 por separado. Juntas sí, y en las dos
direcciones a la vez: el bloqueo abierto recupera recall, la similitud
compactada recupera los errores de espaciado, y el IDF paga la precisión que
cuesta abrir el bloqueo.

**Validación fuera de muestra** (3 pliegues por grupo): ΔF1 medio **+0,0105**
(mín +0,0077, máx +0,0133) contra +0,0140 en el conjunto donde se calibró. La
diferencia de 0,0035 dice que no es sobreajuste.

## Consecuencias

**A favor**

- Mejoran precisión y recall a la vez; el recall del régimen sin identificador
  sube 8,2 puntos.
- Todas las perillas tienen 0,0 por defecto: el comportamiento de 0.17.4 se
  preserva bit a bit, verificado por huella de partición.
- El cálculo IDF pasó de un bucle de Python a matrices dispersas, así que la
  perilla es utilizable a escala real y no solo en un banco de 12 K registros.

**En contra**

- **55 % más de tiempo** (25,6 s → 39,8 s en el banco), porque el bloqueo más
  abierto genera 1,4 M de candidatos en vez de 850 K. Por eso vive en un
  perfil aparte (`fuentes_mixtas`) y no cambia el default.
- **Los falsos positivos sobre casos negativos suben de 62 a 111.** Son
  intermediarios comerciales con nombre casi idéntico
  (`COMMERCIAL ZELECTA TRADING GROUP CORP` repetido para clientes distintos):
  el bloqueo más abierto los expone y el IDF no alcanza a separarlos. Desde el
  nombre solo, no son separables; hace falta evidencia de la entidad detrás.
  Queda registrado como limitación conocida, no como cosa resuelta.

## Alternativas descartadas

- **Subir `idf_weight_blend` global.** Lleva la precisión a 1,0000 y hunde el
  recall a 0,8562. Es un dial de precisión útil cuando fusionar de más es
  catastrófico, no una mejora de F1.
- **Veto por IDF** (descartar el par si la similitud IDF es muy baja). Se
  implementó y se midió: F1 0,9496–0,9553, peor que la mezcla. La perilla
  quedó en el código con default 0,0 por si un caso de uso la necesita.
- **Ponderar por frecuencia del nombre completo.** Se midió: los
  intermediarios aparecen 1 o 2 veces, no muchas. La hipótesis era falsa.
