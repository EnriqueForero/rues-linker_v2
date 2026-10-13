"""Contratos de entrada del motor de vinculación: la base y el papel de cada columna.

``Fuente`` describe UNA base (ancla o destino); ``ConfigVinculacion`` junta la ancla, los destinos y los
parámetros del cruce. Ambas validan al construirse y fallan con un mensaje accionable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from record_linkage.flujo.vinculacion.constantes import NOMBRES_PILA_BASE

__all__ = ["ConfigVinculacion", "Fuente"]

_CLAVE_VALIDA = re.compile(r"[A-Z][A-Z0-9_]{1,30}")


def _exigir_coleccion(campo: str, valor: object) -> None:
    """Rechaza un texto suelto donde va una colección.

    F3.1b (g1-O3): un ``str`` se recorría letra a letra (``patrones_intermediario="CARGO"`` compilaba ``C|A|R|G|O`` y casi
    toda la ancla quedaba como «agente de carga»; ``formas_legales_extra="S.L."`` borraba las palabras S y L).
    """
    if isinstance(valor, str):
        raise ValueError(
            f"Qué pasó: {campo}={valor!r} es un texto suelto y debe ser una colección.\n"
            "Por qué importa: un texto se recorre letra a letra y la configuración no haría lo que escribió.\n"
            f"Qué hacer: escríbalo como colección, p. ej. ({valor!r},)."
        )


@dataclass
class Fuente:
    """Una base de entrada y el papel de cada columna.

    Solo ``col_nombre`` es obligatoria. Cada variable adicional que se declare
    en la ancla Y en un destino se usa como evidencia (ver ``evidencia_extra``).

    Args:
        clave: nombre corto y único (``EXPORTADORES``, ``ORBIS``, ``FDI``).
        archivo: ruta del Excel/CSV/Parquet.
        hoja: hoja del Excel (nombre o posición).
        col_nombre: razón social.
        col_pais: país del registro.
        cols_pais_extra: otras columnas de país que también cuentan como
            "mismo país" (FDI: ``HQ country`` además de ``País de consulta``).
        col_id: identificador nativo único (BvD ID). Si falta, se usa la fila.
        prefijo_id: prefijo del ID por fila cuando no hay ``col_id``.
        col_grupo: nombre de la matriz / grupo (Parent company, GUO - Name).
        col_web, col_email, col_telefono, col_ciudad, col_direccion: contacto.
        col_id_comparable: identificador comparable ENTRE bases (NIF/VAT/NIT).
        cols_metricas: numéricas que se suman por empresa (solo ancla).
        cols_contexto: columnas que viajan a las salidas, en ese orden.
        nombres_contexto: renombre legible de columnas de contexto/métricas.
        largo_truncamiento: ancho del campo de nombre en el sistema de origen
            (aduana: 35). El último fragmento puede estar cortado.
        detectar_intermediarios: marcar agentes de carga (solo aduana).
        transformaciones: (columna, "hs6") para formatear códigos.
        etiqueta: nombre para mostrar (``FDI MARKETS``).
    """

    clave: str
    archivo: str
    col_nombre: str
    hoja: str | int = 0
    col_pais: str | None = None
    cols_pais_extra: tuple[str, ...] = ()
    col_id: str | None = None
    prefijo_id: str = ""
    col_grupo: str | None = None
    col_web: str | None = None
    col_email: str | None = None
    col_telefono: str | None = None
    col_ciudad: str | None = None
    col_direccion: str | None = None
    col_id_comparable: str | None = None
    #: Columna numérica de tamaño (ingresos, proyectos) para desempatar el vínculo principal:
    #: entre dos sociedades del mismo grupo igual de parecidas, se muestra la más grande.
    col_tamano: str | None = None
    cols_metricas: tuple[str, ...] = ()
    cols_contexto: tuple[str, ...] = ()
    nombres_contexto: dict = field(default_factory=dict)
    largo_truncamiento: int | None = None
    detectar_intermediarios: bool = False
    transformaciones: tuple[tuple[str, str], ...] = ()
    #: Métricas que el sistema de origen REPITE en varias filas (valor de un nivel de
    #: agregación superior). Se informa también su suma sin repetidos por
    #: (empresa, *claves_repeticion, valor). Medido en el piloto: Kilos se repite
    #: idéntico en filas de la misma empresa y partida con operaciones distintas.
    metricas_repetidas: tuple[str, ...] = ()
    claves_repeticion: tuple[str, ...] = ()
    etiqueta: str = ""

    def __post_init__(self) -> None:
        if not _CLAVE_VALIDA.fullmatch(self.clave):
            raise ValueError(
                f"clave={self.clave!r}: use MAYÚSCULAS, dígitos o '_' (p. ej. 'ORBIS')."
            )
        for campo in (
            "cols_pais_extra", "cols_metricas", "cols_contexto", "metricas_repetidas", "claves_repeticion",
        ):  # fmt: skip
            _exigir_coleccion(campo, getattr(self, campo))
        self.prefijo_id = self.prefijo_id or self.clave[:3]
        self.etiqueta = self.etiqueta.strip() or self.clave
        self.cols_pais_extra = tuple(self.cols_pais_extra)
        self.cols_metricas = tuple(self.cols_metricas)
        self.cols_contexto = tuple(self.cols_contexto)
        ajenas = [c for c in self.metricas_repetidas if c not in self.cols_metricas]
        if ajenas:
            raise ValueError(
                f"Qué pasó: la fuente {self.clave} declara en metricas_repetidas {ajenas}, que no están en cols_metricas.\n"
                "Por qué importa: la cifra «sin repetidos» solo existe para una métrica que se suma; con una errata la\n"
                "métrica se ignoraría sin aviso.\n"
                f"Qué hacer: corrija el nombre o agréguela a cols_metricas ({list(self.cols_metricas)})."
            )

    def columnas_declaradas(self) -> list[str]:
        """Las columnas que la fuente declara, sin repetidas y en el orden de declaración."""
        cols = [
            self.col_nombre, self.col_pais, *self.cols_pais_extra, self.col_id, self.col_grupo,
            self.col_web, self.col_email, self.col_telefono, self.col_ciudad, self.col_direccion,
            self.col_id_comparable, self.col_tamano, *self.cols_metricas, *self.cols_contexto,
        ]  # fmt: skip
        vistos: set[str] = set()
        salida: list[str] = []
        for c in cols:
            if c and c not in vistos:
                vistos.add(c)
                salida.append(c)
        return salida


@dataclass
class ConfigVinculacion:
    """Parámetros del cruce. Los defectos están medidos en el piloto agroalimentos 2026-09.

    Args:
        ancla: la base cuyas empresas se quieren enriquecer (una fila por empresa en la salida).
        destinos: bases contra las que se cruza (0..N). Sin destinos = solo deduplicación.
        alias_paises: ISO3 → grafías que el catálogo de la librería no trae (FRANCE, ITALY…).
        formas_legales_extra: formas legales a retirar además de las de la librería.
        genericos_extra: palabras de sector/geografía/forma de grupo que NUNCA identifican.
        patrones_intermediario: regex de agentes de carga (en la ancla).
        dominios_genericos: dominios web/correo que no identifican a nadie.
        umbral_piso_empresa: SCORE_RUES_LINKER mínimo para EMPRESA con palabras alineadas.
        umbral_dedup: SCORE_RUES_LINKER mínimo para unir dos grafías de la ancla sin revisión.
        umbral_parecido: SCORE mínimo para proponer a revisión un par sin palabra compartida.
        max_ediciones_palabra: errata admitida por palabra (Levenshtein).
        archivos_decisiones: CSV/XLSX con decisiones de revisión (se aplican en orden).
        archivo_referencia: decisiones de referencia SOLO para medir al motor (no se aplican).
        propagar_por_web: activar la propagación por dominio web.
        carpeta_salida, prefijo_salida, versionar_salida: dónde y cómo se guarda el Excel.
    """

    ancla: Fuente
    destinos: list[Fuente] = field(default_factory=list)
    alias_paises: dict = field(default_factory=dict)
    formas_legales_extra: frozenset = frozenset()
    genericos_extra: frozenset = frozenset()
    patrones_intermediario: tuple[str, ...] = ()
    dominios_genericos: frozenset = frozenset()
    nombres_pila: frozenset = NOMBRES_PILA_BASE
    umbral_piso_empresa: float = 0.70
    umbral_dedup: float = 0.84
    umbral_parecido: float = 0.88
    max_ediciones_palabra: int = 1
    archivos_decisiones: tuple[str, ...] = ()
    #: Decisiones cuyo AUTOR contiene esta marca son de revisión ASISTIDA (no validada por una persona):
    #: se aplican, pero el vínculo queda con confianza MEDIA y en REVISION como "validar".
    marca_sin_validar: str = "(verificar)"
    archivo_referencia: str | tuple | list | None = None
    propagar_por_web: bool = True
    carpeta_salida: str = "."
    prefijo_salida: str = "RESULTADO_VINCULACION"
    versionar_salida: bool = True
    titulo: str = "Vinculación de empresas"

    def __post_init__(self) -> None:
        claves = [self.ancla.clave, *[d.clave for d in self.destinos]]
        if len(set(claves)) != len(claves):
            raise ValueError(
                f"Qué pasó: claves de fuente repetidas {claves}.\nQué hacer: use una clave única por base."
            )
        for campo in (
            "formas_legales_extra", "genericos_extra", "patrones_intermediario", "dominios_genericos", "nombres_pila",
        ):  # fmt: skip
            _exigir_coleccion(campo, getattr(self, campo))
        for iso, grafias in self.alias_paises.items():
            _exigir_coleccion(f"alias_paises[{iso!r}]", grafias)
        prefijos = [d.etiqueta.split()[0].upper() for d in self.destinos]
        if len(set(prefijos)) != len(prefijos):
            raise ValueError(
                f"Qué pasó: dos destinos comparten la primera palabra de la etiqueta {prefijos}.\n"
                "Por qué importa: esa palabra prefija sus columnas en EXPORTADORES y se pisarían sin aviso.\n"
                "Qué hacer: cambie la etiqueta de uno para que su primera palabra sea distinta."
            )
        umbrales = (
            ("umbral_piso_empresa", self.umbral_piso_empresa),
            ("umbral_dedup", self.umbral_dedup),
            ("umbral_parecido", self.umbral_parecido),
        )
        for nombre, v in umbrales:
            if not 0.5 <= v < 1.0:
                raise ValueError(f"{nombre}={v}: debe estar en [0,5; 1,0).")
        if self.max_ediciones_palabra not in (0, 1, 2):
            raise ValueError("max_ediciones_palabra debe ser 0, 1 o 2.")
        if isinstance(self.archivos_decisiones, str):
            # F3.1b (C1 de g2): un archivo suelto es UN archivo, no una lista de letras.
            self.archivos_decisiones = (self.archivos_decisiones,)
        self.archivos_decisiones = tuple(a for a in self.archivos_decisiones if a)
        self.formas_legales_extra = frozenset(
            str(x).strip().upper() for x in self.formas_legales_extra
        )
        self.genericos_extra = frozenset(str(x).strip().upper() for x in self.genericos_extra)
        self.dominios_genericos = frozenset(str(x).strip().lower() for x in self.dominios_genericos)
        self.nombres_pila = frozenset(str(x).strip().upper() for x in self.nombres_pila)
