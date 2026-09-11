# ADR-0005 — Comparar cada variable con la medida que le corresponde

- **Estado:** aceptado
- **Fecha:** 2026-08-29
- **Versión:** 0.19.0
- **Depende de:** ADR-0002 (registro de comparadores)

## Contexto

El objetivo declarado es que la librería sea multicriterio: que use
departamento, CIIU, dirección, teléfono y no solo NIT y razón social.

El primer intento fue directo: declarar esas columnas como evidencia adicional
con los comparadores que ya existían. **Falló, y falló hacia abajo.**

| configuración | F1 global | macro-F1 | F1 CONTACTO |
|---|---:|---:|---:|
| sin variables extra | **0,8745** | **0,8819** | **0,9659** |
| + DEPARTAMENTO y MUNICIPIO (`categorical_signed`) | 0,8441 | 0,8620 | 0,9062 |
| + CIIU (`conjunto_signed`) | 0,8752 | 0,8819 | 0,9659 |
| + las cinco | 0,8464 | 0,8638 | 0,9115 |

Añadir geografía con el comparador exacto **quitó 3 puntos de F1**. La
tentación es concluir que la geografía no sirve. Es la conclusión equivocada.

## Decisión

**Cada variable se compara con la medida que corresponde a su naturaleza. El
comparador es parte del diseño de la variable, no un detalle.**

Se añaden dos comparadores al registro de ADR-0002 —sin tocar el scorer, que
es lo que ese registro existe para permitir—:

### `categoria_tolerante_signed` — igualdad por contención de tokens

Dos fuentes que nombran el mismo lugar rara vez lo escriben igual. La regla es
genérica: un conjunto de tokens contiene al otro, descartando artículos y
preposiciones. No hay tabla de sinónimos de ningún país.

Separación entre pares verdaderos y negativos de nombre confundible:

| variable | regla | verdaderos | negativos | separación |
|---|---|---:|---:|---:|
| DEPARTAMENTO | exacta | 60,9 % | 20,6 % | 40,3 pp |
| DEPARTAMENTO | **contención** | **90,9 %** | 21,0 % | **69,9 pp** |
| MUNICIPIO | exacta | 44,6 % | 14,6 % | 30,1 pp |
| MUNICIPIO | **contención** | **82,2 %** | 14,6 % | **67,7 pp** |

El desacuerdo dominante con la regla exacta era `BOGOTA` contra `BOGOTA D C`:
15.781 casos, casi un tercio del total. Resuelve también `Cali` ≡ `Santiago de
Cali`, `Cúcuta` ≡ `San José de Cúcuta`, `Cartagena` ≡ `Cartagena de Indias`.

### `conjunto_signed` — solapamiento, no Jaccard

El CIIU no es un valor: es una lista. El RUES publica 2,52 actividades por
empresa; la Superintendencia publica 1, la principal.

| medida | verdaderos (n=297) | negativos (n=986) | al azar |
|---|---:|---:|---:|
| igualdad exacta de la cadena | 18,1 % | — | — |
| Jaccard = 1 | 19,5 % | — | — |
| **solapamiento = 1** | **99,3 %** | 20,7 % | 1,3 % |

`|A∩B| / min(|A|,|B|)` reconoce el 99,3 % de los pares verdaderos y solo el
20,7 % de los negativos duros. Jaccard —`|A∩B| / |A∪B|`— daría 1/2,52 ≈ 0,40
cuando el conjunto chico está contenido en el grande y **castigaría a un par
correcto**: con Jaccard el CIIU parece inútil.

## Consecuencias

### Lo que sí cambió

`conjunto_signed` sobre CIIU: F1 0,8745 → 0,8752, FP 2.505 → 2.429 (−76) sin
tocar el recall. Mejora limpia, aunque pequeña: solo 297 pares verdaderos del
conjunto tienen CIIU en los dos lados.

### Lo que no cambió, y por qué importa saberlo

Declarar geografía como evidencia adicional **sigue sin ayudar**, ni siquiera
con el comparador correcto, porque la contribución llega tarde:

| estrato | recall de bloqueo | recall final | dónde se pierde |
|---|---:|---:|---|
| REAL | 0,982 | 0,877 | **scoring** |
| CONTACTO | 0,959 | 0,956 | bloqueo |
| RUIDO | 0,730 | 0,647 | **bloqueo** |

En `RUIDO` y `CONTACTO` el par verdadero **nunca llega a ser candidato**:
ninguna perilla del scorer lo puede rescatar. Y en `CONTACTO` la geografía
además resta, porque la ciudad discrepa entre fuentes de un mismo ente y el
comparador firmado lo castiga.

**Conclusión:** el criterio múltiple aplicado en el scorer es la palanca
equivocada para el problema que queda. La palanca correcta es **bloqueo
multivariable** —usar geografía y CIIU para *generar candidatos*, no para
re-puntuar los que ya existen—. Queda como C27, con la evidencia ya medida
para justificarlo.

### Lo que quedó desmentido

El perfil `fuentes_mixtas`, introducido en 0.18.0 con una ganancia de +0,014
de F1 sobre `ground_truth_grande.csv`, **pierde 0,014 sobre el conjunto
institucional**:

| | `produccion_estandar` | `fuentes_mixtas` |
|---|---:|---:|
| F1 | **0,8745** | 0,8606 |
| recall SIN_ID | **0,6523** | 0,5909 |
| recall `positivo_ruido_sin_id` | **0,6026** | 0,5021 |
| FP que tocan un negativo | **287** | 419 |
| tiempo | **35,5 s** | 62,3 s |

Era sobreajuste a un conjunto que no tenía variación real de nombre. Se
mantiene disponible y documentado para datos de contacto sintéticos, pero
**deja de recomendarse como perfil general**. Es exactamente el error que el
conjunto institucional se construyó para atrapar, y lo atrapó a la primera.

## Referencias

- Winkler, W. E. (1990). *String comparator metrics*. Sobre elegir la métrica
  según la naturaleza del campo.
- Christen, P. (2012). *Data Matching*, cap. 5: comparación de campos
  multivaluados y jerárquicos.
- Evidencia: `docs/evidencia/corrida_inst_{base,geo,ciiu,todo,mixtas}.json`.
- Reproducción de las mediciones de discriminación:
  `scripts/diagnostico_variables.py`.
