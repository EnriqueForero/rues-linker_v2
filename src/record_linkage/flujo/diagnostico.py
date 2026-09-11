"""Diagnóstico de identificadores antes de cruzar (v0.14.0).

Motivación medida: el pipeline consolida entidades entre fuentes cuando el
NIT canónico (base + dígito de verificación) coincide. Una fuente que declara
NIT de 10 dígitos con el DV equivocado produce una base correcta pero un
canónico distinto, y el registro NO se enlaza aunque el nombre sea idéntico.
La pérdida es silenciosa: no hay error, simplemente aparecen dos entidades.

Esta función la vuelve visible ANTES de la corrida, que es cuando todavía se
puede decidir qué hacer (recalcular el DV, mapear solo la base, o aceptar la
pérdida sabiendo su tamaño).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..processing.nit import NitProcessor

__all__ = ["DiagnosticoIdentificador", "diagnosticar_identificadores"]


@dataclass(frozen=True)
class DiagnosticoIdentificador:
    """Resultado del diagnóstico de una columna de identificadores.

    Attributes:
        filas: total de filas analizadas.
        vacios: filas sin identificador.
        invalidos: filas cuyo identificador no supera la validación.
        dv_declarado_no_cuadra: filas con 10 dígitos cuyo DV declarado no
            corresponde a la base — el caso que rompe el enlace en silencio.
        por_tipo: conteo por tipo detectado (STANDARD_9, STANDARD_10_VALID...).
    """

    filas: int
    vacios: int
    invalidos: int
    dv_declarado_no_cuadra: int
    por_tipo: dict[str, int]

    def resumen(self) -> str:
        """Texto accionable, con la recomendación cuando aplica."""
        lineas = [
            f"Identificadores analizados : {self.filas:,}",
            f"  vacíos                   : {self.vacios:,} ({self._pct(self.vacios)})",
            f"  no válidos               : {self.invalidos:,} ({self._pct(self.invalidos)})",
            f"  DV declarado que no cuadra: {self.dv_declarado_no_cuadra:,} "
            f"({self._pct(self.dv_declarado_no_cuadra)})",
        ]
        for tipo, n in sorted(self.por_tipo.items(), key=lambda kv: -kv[1])[:6]:
            lineas.append(f"    {tipo:<22} {n:>10,}")
        if self.dv_declarado_no_cuadra:
            lineas.append(
                "  ⚠️ Esas filas NO se enlazarán por identificador aunque su base "
                "coincida.\n     Opción: mapear solo los 9 dígitos de la base y dejar "
                "que el pipeline calcule el DV."
            )
        return "\n".join(lineas)

    def _pct(self, n: int) -> str:
        return f"{n / self.filas:.2%}" if self.filas else "n/a"


def diagnosticar_identificadores(
    serie: pd.Series, *, muestra_maxima: int = 200_000
) -> DiagnosticoIdentificador:
    """Analiza una columna de NIT/documento y reporta lo que romperá el cruce.

    El análisis es sobre valores ÚNICOS (una base de 895.102 filas suele tener
    ~19.400 identificadores distintos), así que el costo es independiente del
    número de filas repetidas.

    Args:
        serie: columna de identificadores tal como llega de la fuente.
        muestra_maxima: tope de valores únicos a analizar; por encima se toma
            una muestra determinista para acotar el tiempo.

    Returns:
        DiagnosticoIdentificador con conteos y recomendación.
    """
    texto = serie.astype("string").fillna("").str.strip()
    filas = len(texto)
    vacios = int((texto == "").sum())

    unicos = pd.Series(texto[texto != ""].unique())
    if len(unicos) > muestra_maxima:
        unicos = unicos.sort_values(kind="stable").head(muestra_maxima)

    procesador = NitProcessor()
    resultados = {valor: procesador.process_single_nit(valor) for valor in unicos.tolist()}
    conteos = texto[texto != ""].value_counts()

    invalidos = 0
    dv_malo = 0
    por_tipo: dict[str, int] = {}
    for valor, resultado in resultados.items():
        n = int(conteos.get(valor, 0))
        tipo = str(resultado.get("NIT_TYPE", "DESCONOCIDO"))
        por_tipo[tipo] = por_tipo.get(tipo, 0) + n
        if not resultado.get("IS_VALID"):
            invalidos += n
        # 10 dígitos cuyo DV no cuadra: el pipeline lo trata como otro documento
        # y su canónico deja de coincidir con el de la misma base bien digitada.
        if tipo == "OTHER_DOCUMENT_10":
            dv_malo += n

    return DiagnosticoIdentificador(
        filas=filas,
        vacios=vacios,
        invalidos=invalidos,
        dv_declarado_no_cuadra=dv_malo,
        por_tipo=por_tipo,
    )
