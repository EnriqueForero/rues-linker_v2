# ADR-0014 · La cota del perfil sin identificador se declara donde el motor está

**Fecha:** 2026-10-13 · **Estado:** aceptada · **Fase:** 0 de la ruta (`docs/DIAGNOSTICO_Y_RUTA_2026-10-13.md`)

## Contexto

`tests/test_sin_nit_recalibrado.py` exige, sobre los 2.342 registros SIN_NIT sintéticos, P ≥ 0,87, R ≥ 0,47 y F1 ≥ 0,61
(cotas de v0.7.4: P 0,907 · R 0,501 · F1 0,645). Desde el 24 de septiembre el motor da P 0,864 · R 0,547 · F1 0,670 (TP 2.548,
FP 400, FN 2.113) en todas las combinaciones de dependencias probadas y en las 20 corridas del Nightly. El CI diario no corre la
prueba (`slow`), así que nadie lo vio. Detalle en `docs/DEUDA_SIN_NIT.md`.

## Decisión

1. Las cotas pasan a **P ≥ 0,85 · R ≥ 0,50 · F1 ≥ 0,64**: lo medido, con margen de 0,015–0,03 para absorber jitter sin tapar
   una degradación real. La prueba sigue siendo de no regresión.
2. No se bisecá la causa: el camino `deduplicate_unified` es heredado y la Fase 3 lo sustituye por el puntaje probabilístico
   único; la Fase 6 lo retira. Si antes de eso alguien toca ese camino, debe medir contra estas cotas y declarar.
3. El Nightly corre ahora con dos jobs (pinado = compuerta; rangos frescos = aviso) para que «deriva de dependencias» y
   «regresión del código» se distingan el día que ocurran.

## Consecuencias

- El Nightly vuelve a ser una señal: si está en rojo, el código regresó.
- Queda escrito que el perfil sin identificador une mal ≈ 14 de cada 100 pares y deja ≈ 45 de cada 100 sin unir, sobre
  datos sintéticos. La verdad real (Fase 1) dirá la cifra que importa.
- Se renuncia a saber qué commit movió el balance entre v0.7.4 y v0.22.4.
