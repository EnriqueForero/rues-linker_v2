"""Resumen de la corrida: indicadores (hoja RESUMEN), cobertura por país y parámetros (hoja PARAMETROS)."""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import rapidfuzz

import record_linkage
from record_linkage.flujo.vinculacion.config import ConfigVinculacion, Fuente
from record_linkage.flujo.vinculacion.constantes import NOMBRE_MOTOR
from record_linkage.flujo.vinculacion.lectura import sha256_archivo

__all__ = ["huella_enlaces", "parametros", "resumen"]

_COMPARADOR_DE_LA_LIBRERIA = (
    "SimilitudNombre(alfa=0,25, prefix_weight=0,10, max_dif_informativa=0, "
    "min_informativos=1, fraccion_max_distintivo=0,01)"
)


@dataclass
class _Indicadores:
    """Acumula las filas de RESUMEN en el orden en que se declaran."""

    filas: list[dict] = field(default_factory=list)

    def agregar(
        self, seccion: str, indicador: str, valor: object, unidad: str = "", lectura: str = ""
    ) -> None:
        self.filas.append(
            {
                "SECCION": seccion,
                "INDICADOR": indicador,
                "VALOR": valor,
                "UNIDAD": unidad,
                "LECTURA": lectura,
            }
        )


def _nombre_columna_metrica(ancla: Fuente, metrica: str) -> str:
    return ancla.nombres_contexto.get(metrica, metrica).upper().replace(" ", "_")


def _indicadores_de_entradas(
    r: _Indicadores, cfg: ConfigVinculacion, interno: dict, pa: pd.DataFrame, n_emp: int
) -> None:
    ancla = cfg.ancla
    prep = interno["prep"]
    r.agregar(
        "Entradas",
        f"Filas {ancla.etiqueta}",
        len(pa),
        "filas",
        "DETALLE_EXPORTADORES conserva todas.",
    )
    r.agregar(
        "Entradas",
        f"Empresas {ancla.etiqueta} (tras deduplicar)",
        n_emp,
        "empresas",
        f"{interno['graf'].shape[0]} grafías distintas del nombre; ver DEDUPLICACION.",
    )
    for d in cfg.destinos:
        ne = interno["ent_dest"][d.clave]["ID_ENTIDAD"].nunique()
        r.agregar(
            "Entradas",
            f"Registros {d.etiqueta}",
            len(prep[d.clave]),
            "registros",
            f"{ne:,} entidades tras deduplicar.",
        )


def _indicadores_de_vinculacion(
    r: _Indicadores,
    cfg: ConfigVinculacion,
    exportadores: pd.DataFrame,
    vincul: pd.DataFrame,
    n_emp: int,
) -> None:
    destinos = list(cfg.destinos)
    r.agregar(
        "Resultado",
        "Empresas vinculadas a al menos una base",
        len(vincul),
        "empresas",
        f"{len(vincul) / n_emp:.1%} de las empresas." if n_emp else "",
    )
    r.agregar(
        "Resultado",
        "  · con la MISMA EMPRESA en alguna base",
        int((vincul["NIVEL_VINCULO"] == "EMPRESA").sum()),
        "empresas",
        "La razón social existe tal cual (salvo forma legal, erratas o truncamiento).",
    )
    r.agregar(
        "Resultado",
        "  · solo con su GRUPO empresarial",
        int((vincul["NIVEL_VINCULO"] == "GRUPO").sum()),
        "empresas",
        "Otra sociedad de la misma marca/grupo (filial, matriz o hermana). No atribuir sus cifras a la empresa.",
    )
    for d in destinos:
        k = int(exportadores["FUENTES_VINCULADAS"].str.contains(d.etiqueta, regex=False).sum())
        r.agregar("Resultado", f"  · vinculadas a {d.etiqueta}", k, "empresas", "")
    if len(destinos) > 1:
        k = int(exportadores["FUENTES_VINCULADAS"].str.count(r"\+").ge(len(destinos) - 1).sum())
        r.agregar("Resultado", "  · vinculadas a todas las bases", k, "empresas", "")


def _indicadores_de_metricas(
    r: _Indicadores, ancla: Fuente, exportadores: pd.DataFrame, vincul: pd.DataFrame
) -> None:
    nombres_metricas = {_nombre_columna_metrica(ancla, m) for m in ancla.cols_metricas}
    columnas = [
        c for c in exportadores.columns if c in nombres_metricas or c.endswith("_SIN_REPETIDOS")
    ]
    for col in columnas:
        tot = float(exportadores[col].sum())
        if tot > 0:
            cub = float(vincul[col].sum())
            r.agregar(
                "Resultado",
                f"{col} de las empresas vinculadas",
                round(cub / tot, 4),
                "%",
                f"{cub:,.0f} de {tot:,.0f}.",
            )


def _indicadores_de_pares(r: _Indicadores, pares: pd.DataFrame, vinc: pd.DataFrame) -> None:
    hay = len(vinc) > 0
    r.agregar(
        "Resultado",
        "Vínculos (pares empresa–registro) EMPRESA",
        int((vinc["NIVEL_FINAL"] == "EMPRESA").sum()) if hay else 0,
        "pares",
        "ENLACES",
    )
    r.agregar(
        "Resultado",
        "Vínculos (pares empresa–registro) GRUPO",
        int((vinc["NIVEL_FINAL"] == "GRUPO").sum()) if hay else 0,
        "pares",
        "ENLACES",
    )
    rel = int((pares["NIVEL_FINAL"] == "RELACION_PROPIEDAD").sum()) if len(pares) else 0
    if rel:
        r.agregar(
            "Resultado",
            "Relaciones de propiedad sin nombre compartido",
            rel,
            "pares",
            "Aportadas por revisión (p. ej. Quelmar frente a Zelvora). No cuentan como vínculo.",
        )


def _indicadores_de_confianza(
    r: _Indicadores, exportadores: pd.DataFrame, pares: pd.DataFrame, vinc: pd.DataFrame
) -> None:
    hay = len(vinc) > 0
    r.agregar(
        "Confianza",
        "Vínculos de confianza ALTA",
        int((vinc["CONFIANZA"] == "ALTA").sum()) if hay else 0,
        "pares",
        "Automático A, o decidido por una persona.",
    )
    n_asis = int(vinc["ESTADO"].str.contains("asistida").sum()) if hay else 0
    r.agregar(
        "Confianza",
        "Vínculos de confianza MEDIA",
        int((vinc["CONFIANZA"] == "MEDIA").sum()) if hay else 0,
        "pares",
        f"{n_asis} decididos por revisión asistida sin validar (REVISION, prioridad 2) y el resto automáticos B (prioridad 1).",
    )
    pend = pares[pares["ESTADO"].str.startswith("CANDIDATO")] if len(pares) else pares
    r.agregar(
        "Confianza",
        "Candidatos C pendientes de decisión",
        len(pend),
        "pares",
        f"{pend['ENT'].nunique() if len(pend) else 0} empresas; no se vinculan hasta que alguien decida.",
    )
    r.agregar(
        "Confianza",
        "Empresas que requieren revisión",
        int((exportadores["REQUIERE_REVISION"] == "SÍ").sum()),
        "empresas",
        "Vínculos B, candidatos C o posibles duplicados internos sin decidir (hoja REVISION).",
    )


def _indicadores_de_revision(r: _Indicadores, decisiones: pd.DataFrame) -> None:
    if not len(decisiones):
        return
    for archivo, grupo in decisiones.groupby("ARCHIVO", sort=False):
        conteo = "; ".join(f"{k}: {v}" for k, v in grupo["DECISION"].value_counts().items())
        r.agregar("Revisión", f"Decisiones leídas de {archivo}", len(grupo), "decisiones", conteo)


def _indicadores_de_calidad_del_motor(r: _Indicadores, validacion: pd.DataFrame) -> None:
    for fila in validacion.itertuples(index=False):
        r.agregar(
            "Calidad del motor",
            f"{fila.FUENTE_DESTINO}: precisión automática (A+B)",
            fila.PRECISION,
            "%",
            f"IC95 {fila.PRECISION_IC95}; {fila.ACIERTOS} de {fila.VINCULOS_AUTOMATICOS_A_B} contra la referencia.",
        )
        r.agregar(
            "Calidad del motor",
            f"{fila.FUENTE_DESTINO}: recall automático (A+B)",
            fila.RECALL,
            "%",
            f"IC95 {fila.RECALL_IC95}; con candidatos C: {fila.RECALL_CONTANDO_CANDIDATOS_C:.0%}.",
        )


def _indicadores_de_controles(
    r: _Indicadores, controles: pd.DataFrame, calidad: pd.DataFrame
) -> None:
    fallas = controles[controles["ESTADO"] == "FALLA"]
    advert = controles[controles["ESTADO"] == "ADVERTENCIA"]
    lectura = "Todo OK" if fallas.empty else "FALLAN: " + "; ".join(fallas["CONTROL"].head(3))
    if len(advert):
        lectura += f" · {len(advert)} advertencia(s) no crítica(s): " + "; ".join(
            advert["CONTROL"].head(2)
        )
    r.agregar(
        "Controles",
        "Invariantes que cumplen",
        f"{int(controles['CUMPLE'].sum())} de {len(controles)}",
        "",
        lectura,
    )
    r.agregar(
        "Controles",
        "Hallazgos de calidad de datos",
        len(calidad),
        "hallazgos",
        "Ver CALIDAD_DATOS.",
    )


def _fila_de_pais(
    pais: object, sub: pd.DataFrame, destinos: list[Fuente], col_operaciones: str | None
) -> dict:
    v = sub[sub["RESULTADO"] == "VINCULADO"]
    fila = {
        "PAIS": pais,
        "EMPRESAS": len(sub),
        "VINCULADAS": len(v),
        "PCT_VINCULADAS": round(len(v) / len(sub), 4) if len(sub) else 0,
        "NIVEL_EMPRESA": int((v["NIVEL_VINCULO"] == "EMPRESA").sum()),
        "SOLO_GRUPO": int((v["NIVEL_VINCULO"] == "GRUPO").sum()),
        "CANDIDATOS_POR_REVISAR": int((sub["RESULTADO"] == "CANDIDATO POR REVISAR").sum()),
    }
    for d in destinos:
        fila[f"VINCULADAS_{d.clave}"] = int(
            sub["FUENTES_VINCULADAS"].str.contains(d.etiqueta, regex=False).sum()
        )
    if col_operaciones:
        tot = float(sub[col_operaciones].sum())
        fila[f"PCT_{col_operaciones}_CUBIERTO"] = (
            round(float(v[col_operaciones].sum()) / tot, 4) if tot else 0
        )
    return fila


def _cobertura_por_pais(cfg: ConfigVinculacion, exportadores: pd.DataFrame) -> pd.DataFrame:
    col_operaciones = next(
        (c for c in exportadores.columns if c.upper().startswith("OPERACIONES")), None
    )
    filas = [
        _fila_de_pais(pais, sub, list(cfg.destinos), col_operaciones)
        for pais, sub in exportadores.groupby("PAIS", sort=False)
    ]
    return pd.DataFrame(filas).sort_values("EMPRESAS", ascending=False).reset_index(drop=True)


def resumen(
    cfg: ConfigVinculacion,
    interno: dict,
    exportadores: pd.DataFrame,
    pares: pd.DataFrame,
    vinc: pd.DataFrame,
    validacion: pd.DataFrame,
    calidad: pd.DataFrame,
    controles: pd.DataFrame,
    pa: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Indicadores de la corrida y cobertura por país.

    Returns:
        ``(RESUMEN, por_pais)``: la hoja RESUMEN (sección, indicador, valor, unidad, lectura) y una fila por
        país de la ancla ordenada por número de empresas.
    """
    r = _Indicadores()
    n_emp = len(exportadores)
    vincul = exportadores[exportadores["RESULTADO"] == "VINCULADO"]
    _indicadores_de_entradas(r, cfg, interno, pa, n_emp)
    _indicadores_de_vinculacion(r, cfg, exportadores, vincul, n_emp)
    _indicadores_de_metricas(r, cfg.ancla, exportadores, vincul)
    _indicadores_de_pares(r, pares, vinc)
    _indicadores_de_confianza(r, exportadores, pares, vinc)
    _indicadores_de_revision(r, interno["dec"])
    _indicadores_de_calidad_del_motor(r, validacion)
    _indicadores_de_controles(r, controles, calidad)
    return pd.DataFrame(r.filas), _cobertura_por_pais(cfg, exportadores)


def huella_enlaces(df: pd.DataFrame) -> str:
    """SHA-256 (16 hex) del contenido de la tabla como texto; ``'vacía'`` si no tiene filas."""
    if df is None or not len(df):
        return "vacía"
    return hashlib.sha256(
        pd.util.hash_pandas_object(df.astype(str), index=False).values.tobytes()
    ).hexdigest()[:16]


def _referencias(archivo_referencia: str | tuple | list | None) -> list[str]:
    if isinstance(archivo_referencia, str):
        return [archivo_referencia]
    return list(archivo_referencia or [])


def _filas_de_archivos(cfg: ConfigVinculacion) -> list[tuple[str, object]]:
    filas: list[tuple[str, object]] = []
    vistos: set[str] = set()
    for f in [cfg.ancla, *cfg.destinos]:
        if f.archivo not in vistos:
            vistos.add(f.archivo)
            filas.append((f"sha256 · {Path(f.archivo).name}", sha256_archivo(f.archivo)))
        columnas = {k: v for k, v in asdict(f).items() if k.startswith("col") and v}
        filas.append(
            (f"fuente {f.clave}", f"hoja={f.hoja!r} " + json.dumps(columnas, ensure_ascii=False))
        )
    for a in cfg.archivos_decisiones:
        if Path(a).exists():
            filas.append((f"decisiones · {Path(a).name}", sha256_archivo(a)))
    for a in _referencias(cfg.archivo_referencia):
        if a and Path(a).exists():
            filas.append((f"referencia · {Path(a).name}", sha256_archivo(a)))
    return filas


def parametros(
    cfg: ConfigVinculacion, interno: dict, segundos: float, enlaces: pd.DataFrame
) -> pd.DataFrame:
    """Hoja PARAMETROS: versiones, huellas SHA-256 de los insumos, umbrales y listas declaradas.

    ``interno`` no se usa; se conserva para no cambiar la firma del orquestador original.
    """
    filas: list[tuple[str, object]] = [
        ("fecha_ejecucion_utc", datetime.now(timezone.utc).isoformat(timespec="seconds")),
        ("motor", f"{NOMBRE_MOTOR} {record_linkage.__version__}"),
        ("rues_linker", record_linkage.__version__),
        ("python", platform.python_version()),
        ("pandas", pd.__version__),
        ("numpy", np.__version__),
        ("rapidfuzz", rapidfuzz.__version__),
        ("segundos", round(segundos, 1)),
    ]
    filas += _filas_de_archivos(cfg)
    filas += [
        ("umbral_piso_empresa", cfg.umbral_piso_empresa),
        ("umbral_dedup", cfg.umbral_dedup),
        ("umbral_parecido", cfg.umbral_parecido),
        ("max_ediciones_palabra", cfg.max_ediciones_palabra),
        ("propagar_por_web", cfg.propagar_por_web),
        ("comparador rues-linker", _COMPARADOR_DE_LA_LIBRERIA),
        ("alias_paises", json.dumps(cfg.alias_paises, ensure_ascii=False)),
        ("formas_legales_extra", ", ".join(sorted(cfg.formas_legales_extra))),
        (
            "genericos_extra",
            f"{len(cfg.genericos_extra)} términos: " + ", ".join(sorted(cfg.genericos_extra)),
        ),
        ("patrones_intermediario", " | ".join(cfg.patrones_intermediario)),
        ("dominios_genericos", ", ".join(sorted(cfg.dominios_genericos))),
        ("nombres_pila", f"{len(cfg.nombres_pila)} nombres"),
        ("huella ENLACES (sha256/16)", huella_enlaces(enlaces)),
    ]
    return pd.DataFrame(filas, columns=["PARAMETRO", "VALOR"]).astype({"VALOR": str})
