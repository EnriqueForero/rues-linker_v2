# Entrega 0.22.3 — la base nueva, el país que no estaba, y el ±1 que no era mío

> **Qué se pidió.** Que la publicación a GitHub funcione, integrar el notebook 07
> que Enrique estructuró para la base nueva, hacer configurables los nombres de
> columna de la consulta `snowflake_v2`, y coherencia en toda la documentación.
>
> **Qué se encontró.** El bloqueo de la publicación eran tres contratos sobre el
> 07 nuevo. Pero en las salidas guardadas del 07 había algo que usted no mencionó:
> **el smoke test había fallado sobre la base nueva**. Y al medir la corrección,
> apareció un defecto de reproducibilidad que llevaba ahí desde 0.22.0.

---

## 1. La publicación: tres contratos, un notebook nuevo

El 08 llegó a las compuertas con 0.22.2 instalado, banco PASA, conformidad 46/0
saltos. Fallaron tres contratos sobre el 07, porque usted lo había reemplazado por
su versión nueva: versión anunciada 0.22.0, sin `VERSION_OBJETIVO` actualizado, y
—el más interesante— el contrato de entorno **exigía el nombre `_SUBPAQUETES`** y
rechazaba su verificación, que es **más exhaustiva** (`pkgutil.walk_packages`
sobre los 146 módulos). Tercer contrato en tres versiones escrito sobre la
implementación en vez de la propiedad. Corregido: ahora exige que se importen
submódulos uno a uno, por la vía que sea.

Su notebook es el que se entrega. Ya traía `PERFILES_ENTRADA` con el perfil
`snowflake_v2` (`RAZON_SOCIAL_IMPORTADOR`, `PAIS_ESTANDAR`, `USD_FOB_TOTAL`,
`NUM_REGISTROS`) como **único** lugar donde viven los nombres de columna. No lo
reescribí; le añadí lo que faltaba.

## 2. Lo que sus salidas decían: el smoke test falló

```
❌ Smoke test FALLÓ: alguna invariante no se cumple sobre la muestra.
       el país final está en el catálogo o marcado   False  FALLA
```

`PAIS_ESTANDAR` viene estandarizado, no catalogado: trae grafías que
`record_linkage.paises` no conoce. La invariante acertó — **minutos tarde y sin
decir cuáles**. Al reproducirlo con grafías inyectadas apareció lo grave:

| | 0.22.2 |
|---|---|
| tres grafías fuera del catálogo | mismo `PAIS_FINAL = SIN CLASIFICAR`, mismo `ISO3 = ZZZ` |
| la misma razón social bajo las tres | **un solo importador** |

El país es el bloqueo duro; una grafía sin catalogar lo desactivaba. Corrección
en la librería, no en el notebook:

- `paises_sin_clasificar="detener"` (defecto): `preparar()` se detiene **antes**
  de emparejar, con cada grafía, sus filas, el país más parecido y los tres
  remedios. `"aislar"`: cada grafía conserva su `PAIS_FINAL` y no se fusiona
  con nada. Medido: 3 grafías → 3 grupos.
- **Décima invariante**: «ningún grupo mezcla dos grafías de país». Mide el
  aislamiento en vez de suponerlo.
- El notebook comprueba la cobertura **sobre la base completa** como paso `0d`,
  antes del smoke test: la muestra veía 3 filas donde había 300. Perilla
  `PAISES_SIN_CLASIFICAR` en la celda 3.

Y un defecto latente que `aislar` destapó: la unión base↔representantes iba por
`(ISO3, nombre)` cuando la identidad es `(ISO3, PAIS_FINAL, nombre)`.
`agrupar_no_pais=False` lo tenía desde 0.22.0. Cerrado, con prueba.

## 3. El ±1 que no era mío

Verificando que nada cambiara: **99.897** en vez de 99.898. Con el árbol 0.22.0
intacto: 99.897. Otra vez con 0.22.3: 99.898. **El mismo código, dos
resultados.** Diff fila a fila: un grupo, AVIATECA/GTM. Partición aislada bajo
tres `PYTHONHASHSEED`: el **nombre normalizado** cambiaba
(`AVIATECA` / `AVIATECA ANONIMA`).

Causa, en `matching/normalizadores.py`: los sufijos multi-token se ordenaban
desde un `set` con empates en orden de hash del proceso, y cada frase se probaba
una sola vez — los sufijos apilados nunca llegaban al punto fijo, y cuál quedaba
dependía de la semilla. A escala completa, código viejo: seed 0 → 99.898,
seed 1 → 99.897. **`test_es_determinista` no podía verlo: corre en un proceso.**

Corrección: orden total y un regex con grupo repetido hasta el punto fijo.
Verificado: GTM bajo 3 semillas, misma huella; base completa bajo 2 semillas,
**correlativas bit a bit idénticas**, 99.897. Tres pruebas nuevas en
**subprocesos con semillas distintas**. El motor multicampo usa el mismo
normalizador: banco vuelto a correr, **PASA**, cuatro métricas sin mover un
decimal.

**La cifra de referencia pasa a 99.897.** El 99.898 de 0.22.0–0.22.2 —el suyo en
Colab incluido— era la semilla afortunada.

## 4. Verificación

| | |
|---|---|
| banco institucional | **PASA** · precision 0,9385 · recall 0,8249 · F1 0,8780 · B³ 0,9534 · huella idéntica |
| referencia (211.949 filas) | **99.897**, idéntica bajo `PYTHONHASHSEED` 0 y 1 · 10/10 invariantes · recall del bloqueo 1,000 en GTM/NLD/DEU |
| flujo sin identificador | 87 pruebas (16 + 2 + 3 nuevas) |
| contratos de notebooks | 80 |
| su notebook, celdas 2→3→6 con grafías inyectadas | `detener` para en 0d con 300 filas; `aislar` sigue, smoke OK, por fases = fachada |
| build → commit → clon | 428 archivos · ruff limpio · 142 pruebas sobre el clon |
| coherencia de versión | 0.22.3 en los cuatro puntos |
| suite completa (`-m "not slow"`) | **1.550 pasan, 2 se saltan** (gating de Windows) · 1.566 casos colectados |

## 5. Documentación: qué se hizo coherente

Auditoría por grep de todo el repositorio —versiones, conteos, nombres de
archivo, afirmaciones que envejecen—, separando lo **histórico** (una entrada
de CHANGELOG dice lo que era cierto en su versión) de lo **operativo**. Corregido:
`README_notebooks` (título sin versión congelada, celdas 1–10, notebooks sin
contar a mano), `README.md` (fila del 07, 99.897, invariantes), `docs/README.md`
(índice: análisis, entregas, evidencia de importadores, ADR-0009), ADR-0009
(addendum), `PLAN.md` (el flujo sin identificador y lo que le queda abierto),
`ANALISIS_IMPORTADORES.md` (cobertura de países, determinismo, cifra), `CLAUDE.md`
(dos reglas nuevas), CHANGELOG y bitácora. Y un contrato que vigila las
versiones operativas en los notebooks, con excepciones por **clase de enunciado**
(cuándo se midió, en qué versión se introdujo, comparación), no por caso.

## 6. Qué hacer ahora

1. Descomprimir sobre `/content/drive/MyDrive/ProColombia/rues_linker_pruebas`,
   reemplazando. Trae `dist/rues_linker-0.22.3-py3-none-any.whl`.
2. Correr el 07 con `PAISES_SIN_CLASIFICAR = "detener"`. La celda 6 le va a
   listar las grafías de `PAIS_ESTANDAR` que no están en el catálogo. Para cada
   una: si es un país, alias en `GRAFIAS_ADICIONALES` (celda 4); si no lo es,
   patrón; si no tiene sentido catalogarla, `"aislar"`. **No elija la sugerencia
   sin mirar**: `GUINEA` y `GUINEA-BISSAU`.
3. Vuelva a etiquetar `MUESTRA_REVISION`: la precisión de 0,966 es de la base
   DIAN, no de esta.
4. Publicar con el 08, contra una rama de prueba primero. El push desde esta
   sesión sigue en 403 por permisos de la GitHub App; eso no cambió.
