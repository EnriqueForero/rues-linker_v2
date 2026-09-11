# ADR-0002 · Los comparadores viven en un registro, no en un `if`

**Estado:** aceptado · **Fecha:** 2026-08-29 · **Versión:** 0.18.0

## Contexto

Comparar una variable adicional (ciudad, teléfono, correo) se resolvía con una
cadena de `if ftype == ...` de 118 líneas dentro de `engine/scorer.py`, el
módulo más crítico del sistema y uno de los cuatro que superan las 2.000
líneas. Añadir una forma de comparar obligaba a editarlo.

Peor: la lista de tipos válidos estaba escrita **dos veces** —una implícita en
la cadena de `if` y otra explícita en `deduplication/unified.py`—. Registrar un
comparador nuevo lo dejaba funcionando en el scorer y rechazado por la
validación de configuración. Es la misma falla de fondo que causó el OOM de
0.17.3: una regla escrita dos veces se corrige una vez.

La consecuencia práctica se midió: declarar TELEFONO y EMAIL como evidencia
adicional **no cambiaba ni un par**, porque el único comparador disponible era
exacto y dentro de un mismo grupo el teléfono aparece como `312 1897799`,
`312-189-7799` y `+573121897799`.

## Decisión

Un registro (`matching/comparadores_extra.py`) donde cada comparador es un
objeto con nombre, implementación vectorizada, rango declarado (firmado o no)
y descripción. El scorer pide uno por nombre y lo aplica. La validación de
configuración lee **el mismo registro**.

Comparadores nuevos en esta versión, con canonicalización previa:

| Tipo | Qué normaliza |
|---|---|
| `telefono_signed` | últimos 7 dígitos: ignora prefijo país, indicativo y separadores |
| `email_signed` | dominio distinto → −1; mismo dominio → similitud graduada del buzón |
| `documento_signed` | dígitos, sin separadores ni ceros a la izquierda |

## Consecuencias

**A favor**

- Un comparador nuevo no toca el scorer. Hay una prueba que lo demuestra
  registrando uno de juguete.
- Una sola lista de tipos válidos.
- Los comparadores se prueban aislados, sin levantar el pipeline.
- Paridad bit-a-bit de los seis heredados, verificada sobre 3.000 pares con
  nulos de todas las formas, espacios y diferencias de caja.

**En contra**

- Un nivel más de indirección entre el scorer y la comparación.
- El registro es estado global de módulo; registrar dos veces el mismo nombre
  lanza excepción a propósito, porque un registro pisado en silencio produce
  resultados irreproducibles.

## Nota sobre los centinelas de nulo

Los comparadores heredados reconocen `{"", "NAN", "NONE", "NULL", "<NA>"}`.
Los nuevos reconocen además `{"N/A", "NA", "-", "--", "SIN DATO",
"NO DEFINIDO"}`, que es lo que las fuentes reales escriben.

**No se unificó a propósito.** Ampliar el conjunto de los heredados cambiaría
el resultado de corridas ya calibradas, y esa decisión merece su propia
versión, su propia medición y su propia entrada en la bitácora. Queda como
pendiente explícito en `docs/PLAN.md`.
