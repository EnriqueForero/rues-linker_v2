# Documentación

| Archivo | Para qué |
|---|---|
| [`../CLAUDE.md`](../CLAUDE.md) | Guía de trabajo del repositorio: reglas, mapa, cómo medir |
| [`BANCO.md`](BANCO.md) | El banco de pruebas: conjunto de referencia, métricas, uso |
| [`BENCHMARK.md`](BENCHMARK.md) | El conjunto de referencia institucional: cómo se armó, qué mide y qué no |
| [`CONFORMIDAD.md`](CONFORMIDAD.md) | La suite de 43 casos: qué sabe hacer la librería, caso por caso |
| [`BITACORA.md`](BITACORA.md) | Registro cronológico: qué se hizo, con qué evidencia, qué no funcionó |
| [`ANALISIS_IMPORTADORES.md`](ANALISIS_IMPORTADORES.md) | Deduplicación sin identificador: resultados sobre la base de referencia, calidad medida, qué queda |
| [`ENTREGA_0.22.4.md`](ENTREGA_0.22.4.md) | Última entrega: qué se encontró, qué se corrigió, cómo se verificó (anteriores: [`0.22.3`](ENTREGA_0.22.3.md), [`0.22.2`](ENTREGA_0.22.2.md), [`0.20.0`](ENTREGA_0.20.0.md)) |
| [`PLAN.md`](PLAN.md) | Los 37 criterios de mejora y su estado |
| [`CONSUMIDORES.md`](CONSUMIDORES.md) | Quién lee qué salida (notebooks, scripts, librería, pruebas, consumidores externos): la lista que decide los alias de nombres de v1 y su plazo. Borrador para revisión |
| [`adr/`](adr/) | Decisiones de arquitectura, una por archivo |
| [`evidencia/`](evidencia/) | JSON de cada corrida del banco y la partición predicha |
| [`evidencia_importadores/`](evidencia_importadores/) | Tablas de la corrida de referencia del flujo sin identificador: métricas, invariantes, muestra revisada, sensibilidad, recall del bloqueo |
| [`../CHANGELOG.md`](../CHANGELOG.md) | Qué cambió en cada versión |

## Cómo leer esto

**Si va a cambiar código:** `CLAUDE.md` primero, después `BANCO.md`. No toque
nada sin una corrida del banco antes.

**Si quiere saber por qué algo está como está:** `adr/`. Cada decisión que
cambia comportamiento tiene su archivo, con las alternativas que se
descartaron y por qué.

**Si quiere saber qué falta:** `PLAN.md`.

**Si no cree una cifra:** `evidencia/`. Cada número publicado sale de un JSON
que se puede volver a producir con un comando.

## Decisiones registradas

| ADR | Decisión |
|---|---|
| [0001](adr/0001-banco-de-pruebas-unico.md) | Un solo banco de pruebas para toda decisión |
| [0002](adr/0002-registro-de-comparadores.md) | Los comparadores viven en un registro, no en un `if` |
| [0003](adr/0003-idf-por-regimen.md) | La ponderación IDF se aplica por régimen, no globalmente |
| [0009](adr/0009-cuando-el-nombre-es-toda-la-evidencia.md) | Sin identificador: país como bloqueo duro con catálogo declarado, comparador IDF con tres puertas, cobertura por estrellas |
