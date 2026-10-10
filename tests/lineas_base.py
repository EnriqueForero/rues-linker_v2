"""Líneas base congeladas: las cifras que las pruebas exigen, en UN solo sitio.

Una línea base que vive en dos sitios deriva: la constante de una prueba se
actualiza y el JSON de evidencia no, o al revés, y nadie lo nota hasta que las
dos discrepan en un release. Aquí viven las cifras medidas, con la fecha y el
commit en que se midieron, y la única función que las compara con una
medición; `tests/test_banco_linea_base.py` la usa tanto contra el JSON de
evidencia como contra una corrida real del banco.

Cómo se mueve una línea base
----------------------------
Nunca editando una constante "para que pase". Un cambio de comportamiento del
motor se declara (CLAUDE.md §2, regla 4; plan F0, regla 3): ADR que explique
el cambio, entrada en CHANGELOG y corrida nueva con `scripts/banco.py`
(`--etiqueta base_<fase>`) que el coordinador deja en `docs/evidencia/`. Una
línea base nueva son TRES cosas y ninguna reemplaza a las anteriores:

1. un JSON de evidencia nuevo (`docs/evidencia/corrida_base_<fase>.json`);
2. una constante nueva aquí (`BANCO_<FASE>`) con las cifras exactas de ese
   JSON, su fecha y el commit en que se midió;
3. `BANCO_VIGENTE` apuntando a la constante nueva.

Las líneas base anteriores no se borran ni se reescriben: son la historia
(qué producía el motor en cada fase) y `tests/test_banco_linea_base.py` exige
que cada JSON siga coincidiendo con su constante, para que la historia tampoco
derive. Las pruebas que miden el tronco de hoy leen `BANCO_VIGENTE`; las que
miden una conducta histórica (p. ej. la paridad con una perilla apagada) leen
la constante de esa fase por su nombre.

Historia:

* `BANCO_F0` (2026-10-06, commit 33827cc): el motor de 0.22.4 sin tocar.
  Vigente hasta F2.1 (ADR-0011).
* `BANCO_F2` (2026-10-10, commit ef13f83): cobertura por estrellas activa por
  defecto en `produccion_estandar` (umbral 0,80, ADR-0011). Vigente.

Este módulo no es una prueba (no empieza por `test_`): pytest lo importa
desde `tests/` porque el directorio no es un paquete y queda en `sys.path`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "BANCO_F0",
    "BANCO_F2",
    "BANCO_VIGENTE",
    "LineaBaseBanco",
    "discrepancias_con_linea_base",
]


@dataclass(frozen=True)
class LineaBaseBanco:
    """Lo que una corrida del banco debe reproducir para ser la misma.

    Attributes:
        huella: SHA-256 canónico de la partición (`banco.huella_particion`).
            Identifica el resultado completo: si cambia, el motor produce
            otra partición sobre el mismo conjunto.
        f1: F1 por pares, redondeado a cuatro decimales por el banco.
        macro_f1: media del F1 por estrato (lea este, no solo el F1).
        b3_f1: F1 B-cubed, por registro.
        fp_que_tocan_negativo: falsos positivos que involucran un registro
            diseñado como negativo; los errores más caros, se exigen exactos.
        datos: conjunto de referencia, relativo a la raíz del repositorio.
        perfil: perfil de la librería con el que se midió.
        evidencia: JSON de la corrida, relativo a la raíz del repositorio.
        commit: commit en que se midió y se congeló.
        fecha: fecha de la medición (ISO 8601).
        tolerancia_metricas: holgura absoluta para F1, macro-F1 y B³ F1. El
            banco redondea a cuatro decimales, así que 1e-4 absorbe el ruido
            de redondeo y nada más; la huella no tiene holgura.
    """

    huella: str
    f1: float
    macro_f1: float
    b3_f1: float
    fp_que_tocan_negativo: int
    datos: Path
    perfil: str
    evidencia: Path
    commit: str
    fecha: str
    tolerancia_metricas: float = 1e-4


#: Línea base F0 del banco institucional. Medida el 2026-10-06 sobre
#: b45e335 + ab67f86 (sin cambios de motor) y congelada en el commit 33827cc,
#: con Python 3.11.15, pandas 3.0.6 y numpy 2.3.5. Evidencia:
#: ``docs/evidencia/corrida_base_f0.json`` (30.486 registros, 11.478 grupos,
#: 46.374 pares verdaderos; precision 0,9385 · recall 0,8249).
#:
#: Vigente hasta F2.1 (ADR-0011: cobertura por estrellas activa por defecto).
#: Se conserva tal cual como referencia histórica y para la prueba de paridad
#: con la perilla apagada (``tests/test_cobertura_sin_identificador_f21.py``
#: lee ``BANCO_F0.huella``): apagar ``cobertura_sin_identificador`` debe
#: devolver exactamente esta partición.
BANCO_F0 = LineaBaseBanco(
    huella="1e365ba81c4df45e410dd09154cafef1d38e96d9fb2846998260c10ad69cbe31",
    f1=0.8780,
    macro_f1=0.8920,
    b3_f1=0.9534,
    fp_que_tocan_negativo=287,
    datos=Path("data/benchmark/benchmark_institucional.csv.gz"),
    perfil="produccion_estandar",
    evidencia=Path("docs/evidencia/corrida_base_f0.json"),
    commit="33827cc",
    fecha="2026-10-06",
)

#: Línea base F2 del banco institucional, tras F2.1 (cobertura por estrellas
#: activa por defecto en ``produccion_estandar``, umbral 0,80, ADR-0011; F2.2
#: fija el umbral con ``docs/evidencia/cobertura_umbral.json``). Cambio de
#: partición DECLARADO: la huella pasa de ``1e365ba8…`` (F0) a ``5bfed0d1…``.
#: Medida el 2026-10-10 por el coordinador sobre el tronco ``claude/f2-motor``
#: en ef13f83 (merge de F2.1), con Python 3.11.15, pandas 3.0.6 y numpy 2.3.5.
#: Evidencia: ``docs/evidencia/corrida_base_f2.json`` (30.486 registros,
#: 11.478 grupos verdad, 13.414 predichos, 46.374 pares verdaderos;
#: precision 0,9557 · recall 0,7927). Respecto a F0: macro-F1 0,8920 → 0,8803
#: (compuerta F2: ≥ 0,880) y FP sobre negativos 287 → 246 (≤ 287): la
#: cobertura cambia recall por precisión, que es lo que la regla de Enrique
#: pide (unir dos empresas distintas duele más que dejar sin unir).
BANCO_F2 = LineaBaseBanco(
    huella="5bfed0d16d078380ca26f3a6fe230c85203ab64b5eeb7612b5f4f8c46308d59b",
    f1=0.8666,
    macro_f1=0.8803,
    b3_f1=0.9487,
    fp_que_tocan_negativo=246,
    datos=Path("data/benchmark/benchmark_institucional.csv.gz"),
    perfil="produccion_estandar",
    evidencia=Path("docs/evidencia/corrida_base_f2.json"),
    commit="ef13f83",
    fecha="2026-10-10",
)

#: La línea base que describe el tronco HOY. Las pruebas que miden el banco
#: actual leen esta; al mover la línea base (docstring del módulo) solo cambia
#: a qué constante apunta.
BANCO_VIGENTE = BANCO_F2


def discrepancias_con_linea_base(
    linea: LineaBaseBanco,
    *,
    huella: str,
    f1: float,
    macro_f1: float,
    b3_f1: float,
    fp_que_tocan_negativo: int,
    origen: str,
) -> list[str]:
    """Compara una medición con la línea base y devuelve las discrepancias.

    Lista vacía significa que la medición reproduce la línea base. Cada
    discrepancia es un mensaje completo —qué pasó, por qué importa, qué
    hacer— listo para ser el texto de un `assert`.

    Args:
        linea: la línea base exigida.
        huella, f1, macro_f1, b3_f1, fp_que_tocan_negativo: lo medido.
        origen: de dónde salió la medición (aparece en los mensajes).

    Returns:
        Mensajes de discrepancia, en el orden huella → métricas → FP.
    """
    discrepancias: list[str] = []
    if huella != linea.huella:
        discrepancias.append(
            f"la huella del banco cambió ({origen}): esperada {linea.huella[:16]}…, "
            f"obtenida {huella[:16]}…. La huella identifica la partición completa: si "
            f"cambió, el motor produce otro resultado sobre el mismo conjunto y la línea "
            f"base {linea.commit} ({linea.fecha}) dejó de describirlo. Eso solo es "
            f"admisible con un ADR que declare el cambio de comportamiento y una entrada "
            f"en CHANGELOG (regla 3 del plan); después, corra scripts/banco.py, añada el JSON "
            f"nuevo junto a {linea.evidencia} y una constante nueva en tests/lineas_base.py, "
            f"y apunte BANCO_VIGENTE a ella (las líneas base anteriores no se reescriben)."
        )
    for nombre, esperado, obtenido in (
        ("F1", linea.f1, f1),
        ("macro-F1", linea.macro_f1, macro_f1),
        ("B³ F1", linea.b3_f1, b3_f1),
    ):
        # `not (… <= tol)` y no `… > tol`: un NaN (la métrica dejó de calcularse)
        # debe contar como discrepancia, y con `>` pasaría en silencio.
        if not (abs(obtenido - esperado) <= linea.tolerancia_metricas):
            obtenido_txt = (
                "nan (la métrica no se calculó)" if obtenido != obtenido else f"{obtenido:.4f}"
            )
            discrepancias.append(
                f"{nombre} fuera de tolerancia ({origen}): esperado {esperado:.4f} "
                f"± {linea.tolerancia_metricas:g}, obtenido {obtenido_txt}. Una métrica "
                f"que se mueve más que el redondeo es un cambio de comportamiento: "
                f"declárelo con ADR y CHANGELOG antes de mover la línea base."
            )
    if fp_que_tocan_negativo != linea.fp_que_tocan_negativo:
        discrepancias.append(
            f"fp_que_tocan_negativo cambió ({origen}): esperado "
            f"{linea.fp_que_tocan_negativo}, obtenido {fp_que_tocan_negativo}. Son los "
            f"errores más caros (fusiones con un registro diseñado como negativo) y se "
            f"exigen exactos: declárelo con ADR y CHANGELOG antes de mover la línea base."
        )
    return discrepancias
