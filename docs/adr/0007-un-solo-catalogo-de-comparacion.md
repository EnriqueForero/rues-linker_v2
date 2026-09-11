# ADR-0007 — Un solo catálogo de comparación para los dos motores

- **Estado:** aceptado
- **Fecha:** 2026-08-29
- **Versión:** 0.20.0
- **Complementa a:** ADR-0002 (registro de comparadores), ADR-0005 (comparadores multicriterio)

## Contexto

Al integrar el conjunto de conformidad, que declara **11 tipos de campo del
motor**, apareció un defecto estructural que ninguna métrica había mostrado:
la librería tenía **dos catálogos de comparadores que no se conocían**.

| | `matching.campos` + `matching.comparators` | `matching.comparadores_extra` |
|---|---|---|
| tipos | 14 declarativos | 11 |
| lo consume | motor multicampo, en memoria | **scorer de producción**, en disco |
| tiene geo, fecha, numérico | sí | **no** |
| escala | hasta ~500 K filas | millones |

Consecuencia práctica: **el camino que procesa millones de registros no podía
usar la mitad de lo que la librería ya sabía hacer.** Un usuario con
coordenadas, fechas o montos tenía que elegir entre escala y criterio. Eso no
es una funcionalidad que falta: es la misma capacidad implementada en un lado
y ausente en el otro, que es la definición de deuda técnica.

Además explicaba una rareza previa: ADR-0005 concluyó que "el criterio
múltiple en el scorer no ayuda", cuando en realidad el scorer solo tenía
acceso a las variables más pobres.

## Decisión

**El catálogo de producción es el único catálogo. `matching.puente_campos`
registra en él el comparador canónico de cada `TipoCampo`, delegando en la
misma instancia que usa el motor multicampo.**

No se reimplementa nada. Una sola implementación por forma de comparar (DRY),
el scorer sigue sin saber cuántas hay (OCP) y los dos motores dependen del
protocolo `Comparator`, no de clases concretas (DIP).

    tipos disponibles para producción:  11  →  24

Los nombres llevan el prefijo `tipo_` para no chocar con los comparadores
heredados, cuya paridad bit a bit está congelada por el oráculo y no debe
cambiar. Uso:

```python
linkage(..., extra_features=[
    ("FECHA_CONSTITUCION", "tipo_fecha",     0.10),
    ("COORDENADAS",        "tipo_geo",       0.10),
    ("VENTAS_ANUALES",     "tipo_numerico",  0.05),
])
```

`GEO` necesita dos números y el registro pasa una columna por variable, así
que se admite el par empaquetado —`"4.65,-74.05"`, con coma, punto y coma o
espacio—. Es toda la adaptación que hay; no hay lógica de comparación en el
puente.

`IDENTIFICADOR` se excluye a propósito: el scorer ya lo trata como evidencia
de primera clase, con veto, boost y forma canónica propios (ADR-0004), y
exponerlo además como variable adicional invitaría a contarlo dos veces.

## Consecuencias

### Dos defectos que el puente destapó

Exponer los comparadores a la prueba de propiedades del catálogo de producción
—que los alimenta con nulos de todas las formas— reveló dos fallos reales que
llevaban tiempo ahí:

1. **`pandas 3.0` dejó de convertir los ausentes a la cadena `'nan'`.**
   `pd.Series(arr).astype(str)` los deja como `NaN` flotante, y el `.map`
   posterior revienta con `normalize() argument 2 must be str, not float`.
   Es un fallo en datos reales, no en un test: basta una celda vacía en una
   columna de texto. Corregido con un único ayudante `_a_texto` usado por
   todos los normalizadores del módulo.

2. **El comparador de direcciones no separaba la placa.** `'CRA 7 # 71-21'`
   contra `'CARRERA 7 NO 71 21'` daba 0,600 porque `'71-21'` quedaba como un
   token y `'71' '21'` como dos. Corregido reemplazando por espacio la
   puntuación **entre dos dígitos**: ahora da 1,000.

   La primera corrección fue más amplia —borrar toda la puntuación— y **se
   retiró tras medirla**: bajaba el recall del baseline multicampo de 0,8940 a
   0,8808 sin arreglar ningún caso adicional, porque eliminaba los guiones
   sueltos de `'CALLE 11 # 2 - 11'`, que hoy cuentan como token compartido. Se
   corrige el defecto y nada más.

### Costo

Ninguno medible. La línea base del banco es **bit a bit idéntica** a la de
0.19.0 (huella `1e365ba8…`): los tipos nuevos están disponibles y no cambian
nada mientras no se declaren.

### Lo que queda

El puente unifica el catálogo de **comparación**. El de **bloqueo** sigue
partido: el motor multicampo tiene bloqueo componible (llave exacta, LSH,
rejilla geo, vecindario ordenado) y el de producción tiene el suyo en disco.
C31 acercó los dos con llaves declaradas, pero no son el mismo objeto. Queda
como C36.

## Referencias

- Martin, R. C. *Agile Software Development*: DIP — depender de abstracciones,
  no de implementaciones.
- Evidencia: `docs/evidencia/corrida_v020_base.json`, y la paridad de
  `tests/test_baseline_multicampo.py` y `tests/test_paridad_p1_1.py`.
