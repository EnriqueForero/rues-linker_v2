"""evaluation.ground_truth — muestra estratificada de pares para construir una verdad.

Componentes:
    - ``ConfigGroundTruth``: qué proporción de cada clase, umbrales y columnas.
    - ``GroundTruthGenerator``: muestrea pares fáciles, difíciles, ambiguos y
      negativos para que una persona los revise (``IS_MATCH``) y produzca el
      archivo que ``evaluation.quality.QualityEvaluator`` carga.
    - ``GroundTruthEvaluator``: se RE-EXPORTA desde ``evaluador_verdad.py``,
      que es su única implementación (regla 3: una regla se escribe una vez).

Historia. El generador nació en el notebook (celda [131]) y F2.8 lo retiró el
6 de octubre de 2026 junto con el paquete ``optimization/`` porque solo tenía
dos pruebas humo. Enrique decidió el 7 de octubre conservar la funcionalidad
(«no porque no se use seguido se debe eliminar»), así que F2.14 lo restaura
aquí. Lo que cambia respecto a 4dccfaf:

* Ya no importa ``optimization/`` (``OptimizationEngine`` y ``ParameterSpace``
  solo los usaba ``GroundTruthEvaluator.cross_validate``, que ahora vive UNA
  vez en ``OrchestratorOptimizer.validacion_cruzada``). El «calculador de
  similitud perezoso» era un ``token_set_ratio`` para nombres y una distancia
  de Levenshtein normalizada para identificadores: aquí se usan el comparador
  ``token_set_ratio`` ya registrado en ``matching.comparadores_extra`` y
  ``rapidfuzz`` directo, sin volver a escribirlos.
* Sin ``iterrows`` ni bucles por par (regla 2 del CLAUDE.md): los candidatos
  difíciles y ambiguos salen de UNA matriz de similitud sobre una muestra de
  tamaño acotado (``muestra_max``) y de máscaras numpy; el resto es
  ``groupby``/``sample`` de pandas.
* Reproducible: toda la aleatoriedad sale de ``ConfigGroundTruth.semilla``
  (la versión original muestreaba los negativos sin semilla).
* Los negativos «claros» se verifican (similitud de nombre por debajo del
  umbral ambiguo y NIT canónico distinto o ausente); antes solo se suponía
  que dos fuentes al azar no coincidían.

Las columnas de salida (``ID_1``, ``ID_2``, ``IS_MATCH``, ``CASE_TYPE``…) se
conservan en inglés porque son el contrato que ``QualityEvaluator`` lee.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..exporters._spreadsheet import prepare_spreadsheet_data
from ..matching.comparadores_extra import obtener as obtener_comparador
from ..matching.comparators import _a_texto
from ..utils.logger import CustomLogger
from .evaluador_verdad import GroundTruthEvaluator

__all__ = [
    "CLASES",
    "COLUMNAS_SALIDA",
    "ConfigGroundTruth",
    "GroundTruthEvaluator",
    "GroundTruthGenerator",
]

#: Las cuatro clases del muestreo, en el orden en que se generan.
CLASES: tuple[str, ...] = ("easy_positive", "difficult_positive", "ambiguous", "easy_negative")

#: Orden de columnas del archivo de revisión (contrato de ``QualityEvaluator``).
COLUMNAS_SALIDA: tuple[str, ...] = (
    "ID_1",
    "ID_2",
    "IS_MATCH",
    "RAZON_SOCIAL_1",
    "RAZON_SOCIAL_2",
    "NIT_1",
    "NIT_2",
    "NAME_SIMILARITY",
    "NIT_SIMILARITY",
    "CASE_TYPE",
    "SRC_1",
    "SRC_2",
    "CONFIDENCE",
    "REVIEWER",
    "REVIEW_DATE",
    "NOTES",
)

_COLUMNAS_REVISION: tuple[str, ...] = ("IS_MATCH", "REVIEWER", "REVIEW_DATE", "CONFIDENCE", "NOTES")
_COLUMNA_ID = "_ID_REGISTRO_INTERNO"


def _proporciones_por_defecto() -> dict[str, float]:
    return {
        "easy_positive": 0.20,  # mismo identificador
        "difficult_positive": 0.30,  # nombre casi igual, identificador distinto
        "ambiguous": 0.30,  # nombre parecido a medias
        "easy_negative": 0.20,  # nombres que no se parecen, fuentes distintas
    }


@dataclass(frozen=True)
class ConfigGroundTruth:
    """Configuración del muestreo. Cero números mágicos fuera de aquí.

    Attributes:
        semilla: única fuente de aleatoriedad (muestras, orden final).
        proporciones: fracción de ``n_samples`` por clase; deben sumar 1 y
            cubrir exactamente las cuatro ``CLASES``.
        umbral_dificil: similitud de nombre mínima de un positivo difícil.
        umbral_ambiguo: similitud mínima de un ambiguo (máxima exclusiva: el
            umbral difícil) y máxima exclusiva de un negativo claro.
        muestra_max: tamaño de la muestra sobre la que se calcula la matriz de
            similitud de nombres. El costo es O(muestra_max²) en memoria
            (``float32``: 3.000² ≈ 36 MB) y en tiempo; súbalo con cuidado.
        tamano_grupo_max: los grupos por identificador más grandes que esto no
            aportan positivos fáciles (suelen ser identificadores basura).
        col_nombre, col_nit_canonico, col_nit, col_razon_social, col_fuente:
            columnas del DataFrame preprocesado. ``col_nombre`` es obligatoria
            (sin ella toda similitud sería 0 y solo saldrían negativos: el
            generador falla en vez de muestrear a ciegas). Las demás, si
            faltan, se tratan como vacías (sin identificador no hay positivos
            fáciles; sin fuente los negativos salen de pares al azar).
    """

    semilla: int = 42
    proporciones: Mapping[str, float] = field(default_factory=_proporciones_por_defecto)
    umbral_dificil: float = 0.85
    umbral_ambiguo: float = 0.60
    muestra_max: int = 3000
    tamano_grupo_max: int = 10
    col_nombre: str = "NOMBRE_LIMPIO"
    col_nit_canonico: str = "NIT_OK"
    col_nit: str = "NIT"
    col_razon_social: str = "RAZON_SOCIAL"
    col_fuente: str = "SRC"

    def __post_init__(self) -> None:
        if set(self.proporciones) != set(CLASES) or abs(sum(self.proporciones.values()) - 1) > 1e-9:
            raise ValueError(
                f"proporciones debe cubrir exactamente {CLASES} y sumar 1; "
                f"recibido {dict(self.proporciones)}. Sin eso el muestreo no "
                "reparte n_samples entre las clases."
            )
        if not 0.0 <= self.umbral_ambiguo < self.umbral_dificil <= 1.0:
            raise ValueError(
                f"umbral_ambiguo ({self.umbral_ambiguo}) debe ser menor que "
                f"umbral_dificil ({self.umbral_dificil}) y ambos estar en [0, 1]: "
                "definen bandas disjuntas de similitud."
            )
        if self.muestra_max < 2 or self.tamano_grupo_max < 2:
            raise ValueError("muestra_max y tamano_grupo_max deben ser >= 2.")
        object.__setattr__(self, "proporciones", dict(self.proporciones))


class GroundTruthGenerator:
    """Genera una muestra estratificada de pares para revisión humana.

    Cubre casos fáciles, difíciles, ambiguos y negativos para que la verdad
    resultante mida lo que importa (los pares donde el motor puede
    equivocarse) y no solo lo trivial.

    Uso::

        generador = GroundTruthGenerator(ConfigGroundTruth(semilla=42))
        pares = generador.create_intelligent_sample(df, n_samples=1000,
                                                    output_path="verdad.xlsx")
        # Una persona llena IS_MATCH (1/0) y QualityEvaluator carga el archivo.
    """

    def __init__(self, config: ConfigGroundTruth | None = None) -> None:
        if config is not None and not isinstance(config, ConfigGroundTruth):
            raise TypeError(
                f"config debe ser ConfigGroundTruth, no {type(config).__name__}. "
                "Un dict ya no se acepta: una clave mal escrita se ignoraba en silencio."
            )
        self.config = config or ConfigGroundTruth()
        self.logger = CustomLogger("GroundTruthGenerator")
        self._comparar_nombres = obtener_comparador("token_set_ratio")
        if self._comparar_nombres is None:  # pragma: no cover - registro estático
            raise RuntimeError("matching.comparadores_extra no registra 'token_set_ratio'.")

    # ── API pública ───────────────────────────────────────────────────────
    def create_intelligent_sample(
        self,
        df: pd.DataFrame,
        n_samples: int = 1000,
        output_path: str | Path | None = None,
    ) -> pd.DataFrame:
        """Muestra estratificada de ``n_samples`` pares (menos si no hay oferta).

        Args:
            df: DataFrame preprocesado (ver columnas en ``ConfigGroundTruth``).
            n_samples: número de pares deseado; cada clase recibe
                ``int(n_samples · proporción)``.
            output_path: si se indica, se guarda con :meth:`guardar`.

        Returns:
            DataFrame con ``COLUMNAS_SALIDA``, mezclado con la semilla, con las
            columnas de revisión vacías.

        Raises:
            ValueError: si ``n_samples`` < 1, ``df`` está vacío o no trae la
                columna ``config.col_nombre``.
        """
        if n_samples < 1:
            raise ValueError(f"n_samples debe ser >= 1; recibido {n_samples}.")
        if df.empty:
            raise ValueError("df está vacío: no hay registros de los que muestrear pares.")
        if self.config.col_nombre not in df.columns:
            raise ValueError(
                f"df no trae la columna de nombre '{self.config.col_nombre}' (col_nombre). "
                "Sin nombre toda similitud es 0 y solo saldrían negativos: la muestra no "
                "serviría para revisar nada. Pase ConfigGroundTruth(col_nombre=...) con la "
                "columna que sí tiene (p. ej. 'RAZON_SOCIAL') o preprocese antes "
                f"(columnas disponibles: {list(df.columns)})."
            )

        marco = df.copy()
        marco[_COLUMNA_ID] = marco.index
        self.logger.info(f"Generando {n_samples} pares para ground truth")

        muestra, matriz = self._muestra_con_matriz(marco)
        partes: list[pd.DataFrame] = []
        for clase in CLASES:
            n_clase = int(n_samples * self.config.proporciones[clase])
            if n_clase == 0:
                continue
            if clase == "easy_positive":
                pares = self._positivos_faciles(marco, n_clase)
            elif clase in ("difficult_positive", "ambiguous"):
                pares = self._por_banda(muestra, matriz, n_clase, clase)
            else:
                pares = self._negativos_claros(marco, n_clase)
            if len(pares) < n_clase:
                self.logger.warning(
                    f"Clase '{clase}': se pidieron {n_clase} pares y solo hay {len(pares)} "
                    "en los datos (la muestra queda más corta en esa clase)."
                )
            partes.append(pares)

        # pd.concat([]) lanza «No objects to concatenate»: la lista vacía se
        # resuelve antes, no después (la guarda posterior nunca corría).
        no_vacias = [p for p in partes if not p.empty]
        if not no_vacias:
            self.logger.warning(
                "Ninguna clase produjo pares: la muestra sale vacía. Revise que los "
                "datos tengan nombres parecidos o identificadores repetidos, o suba n_samples."
            )
            salida = pd.DataFrame(columns=list(COLUMNAS_SALIDA))
        else:
            salida = pd.concat(no_vacias, ignore_index=True)
            for columna in _COLUMNAS_REVISION:
                salida[columna] = None
            salida = salida.sample(frac=1, random_state=self.config.semilla).reset_index(drop=True)
            salida = salida[list(COLUMNAS_SALIDA)]

        if output_path is not None:
            self.guardar(salida, output_path)
        self.logger.info(f"Ground truth generado con {len(salida)} pares")
        return salida

    def guardar(self, ground_truth: pd.DataFrame, ruta: str | Path) -> Path:
        """Escribe el archivo de revisión (``.xlsx`` o CSV) con las columnas en orden.

        Raises:
            ValueError: si faltan columnas de ``COLUMNAS_SALIDA`` (antes salía
                el ``KeyError`` crudo de pandas).
        """
        ruta = Path(ruta)
        faltan = set(COLUMNAS_SALIDA) - set(ground_truth.columns)
        if faltan:
            raise ValueError(
                f"Faltan columnas {sorted(faltan)}: guardar espera la salida de "
                "create_intelligent_sample (COLUMNAS_SALIDA); no recorte columnas antes "
                "de guardar."
            )
        seguro = prepare_spreadsheet_data(ground_truth[list(COLUMNAS_SALIDA)])
        if ruta.suffix.lower() == ".xlsx":
            seguro.to_excel(ruta, index=False)
        else:
            seguro.to_csv(ruta, index=False)
        self.logger.info(f"Ground truth guardado en: {ruta}")
        return ruta

    # ── Clases del muestreo ───────────────────────────────────────────────
    def _positivos_faciles(self, marco: pd.DataFrame, n: int) -> pd.DataFrame:
        """Un par por identificador compartido (grupos de 2..tamano_grupo_max)."""
        col = self.config.col_nit_canonico
        if col not in marco.columns:
            return pd.DataFrame()
        nit = _a_texto(marco[col].to_numpy()).str.strip()
        con_nit = marco.loc[(nit != "").to_numpy()].assign(**{col: nit[nit != ""].to_numpy()})
        if con_nit.empty:
            return pd.DataFrame()
        tamano = con_nit.groupby(col)[col].transform("size")
        elegibles = con_nit.loc[(tamano >= 2) & (tamano <= self.config.tamano_grupo_max)]
        if elegibles.empty:
            return pd.DataFrame()
        # Dos registros al azar por grupo, con semilla; luego n grupos al azar.
        barajado = elegibles.sample(frac=1, random_state=self.config.semilla)
        grupos = barajado.groupby(col, sort=False)
        izq = grupos.nth(0).set_index(col, drop=False)
        der = grupos.nth(1).set_index(col, drop=False)
        claves = izq.index.intersection(der.index)
        rng = np.random.default_rng(self.config.semilla)
        claves = claves[rng.permutation(len(claves))[:n]]
        return self._construir_pares(izq.loc[claves], der.loc[claves], "easy_positive")

    def _muestra_con_matriz(self, marco: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
        """Muestra acotada y su matriz de similitud de nombres (0..1, ``float32``).

        Una sola matriz sirve a las clases difícil y ambigua. Los nombres
        vacíos quedan con fila y columna en 0 y no entran a ninguna banda.
        No se usa ``uint8``: rapidfuzz redondea (59,57 → 60) y un par quedaría
        dentro de la banda por redondeo mientras ``NAME_SIMILARITY`` (exacta)
        dice lo contrario.
        """
        from rapidfuzz import fuzz, process as rf_process

        n = min(self.config.muestra_max, len(marco))
        muestra = marco.sample(n=n, random_state=self.config.semilla).reset_index(drop=True)
        nombres = self._texto(muestra, self.config.col_nombre)
        matriz = rf_process.cdist(
            nombres.tolist(), nombres.tolist(), scorer=fuzz.token_set_ratio, dtype=np.float32
        )
        matriz /= 100.0
        vacios = nombres == ""
        matriz[vacios, :] = 0
        matriz[:, vacios] = 0
        return muestra, matriz

    def _por_banda(
        self, muestra: pd.DataFrame, matriz: np.ndarray, n: int, clase: str
    ) -> pd.DataFrame:
        """Pares cuya similitud de nombre cae en la banda de la clase.

        ``difficult_positive``: ≥ umbral_dificil y NIT distinto (o ausente).
        ``ambiguous``: [umbral_ambiguo, umbral_dificil).

        Para cada registro se toma su mejor pareja dentro de la banda; después
        se exige que ningún registro aparezca en dos pares (vectorizado:
        ``drop_duplicates`` + ``isin``), se verifica la banda sobre la
        similitud exacta del par y se eligen ``n`` al azar con semilla.
        """
        if len(muestra) < 2:
            return pd.DataFrame()
        banda = self._banda(clase)
        mascara = (matriz >= banda[0]) & (matriz < banda[1])
        if clase == "difficult_positive":
            nit = self._texto(muestra, self.config.col_nit_canonico).to_numpy()
            mismo_nit = (nit[:, None] == nit[None, :]) & (nit[:, None] != "")
            mascara &= ~mismo_nit
        mascara &= np.triu(np.ones(matriz.shape, dtype=bool), k=1)
        if not mascara.any():
            return pd.DataFrame()

        puntajes = np.where(mascara, matriz, -1.0)
        j = puntajes.argmax(axis=1)
        i = np.arange(len(muestra))
        hay = puntajes[i, j] >= 0
        pares = pd.DataFrame({"i": i[hay], "j": j[hay]})
        pares = pares.drop_duplicates("j")
        pares = pares.loc[~pares["j"].isin(pares["i"])]
        construidos = self._construir_pares(
            muestra.iloc[pares["i"].to_numpy()], muestra.iloc[pares["j"].to_numpy()], clase
        )
        exacta = construidos["NAME_SIMILARITY"]
        construidos = construidos.loc[((exacta >= banda[0]) & (exacta < banda[1])).to_numpy()]
        rng = np.random.default_rng(self.config.semilla)
        return construidos.iloc[rng.permutation(len(construidos))[:n]].reset_index(drop=True)

    def _banda(self, clase: str) -> tuple[float, float]:
        """[mínimo, máximo) de similitud de nombre de la clase.

        El tope de la clase difícil es ``inf``, no ``1 + ε``: la matriz es
        ``float32`` y NumPy (NEP 50) compara en ese tipo, donde ``1 + 1e-9``
        redondea a ``1.0`` y excluiría los nombres idénticos.
        """
        if clase == "difficult_positive":
            return self.config.umbral_dificil, float("inf")
        return self.config.umbral_ambiguo, self.config.umbral_dificil

    def _negativos_claros(self, marco: pd.DataFrame, n: int) -> pd.DataFrame:
        """Pares que no se parecen (nombre < umbral_ambiguo) con NIT canónico distinto o ausente.

        Primero se emparejan registros de las dos fuentes más pobladas; si no
        alcanza, se completan con pares al azar. Se sobremuestrea ×3 porque la
        verificación de similitud descarta una parte.
        """
        rng = np.random.default_rng(self.config.semilla)
        objetivo = 3 * n
        izq_partes: list[pd.DataFrame] = []
        der_partes: list[pd.DataFrame] = []
        col_fuente = self.config.col_fuente
        if col_fuente in marco.columns:
            fuentes = marco[col_fuente].value_counts().index
            if len(fuentes) >= 2:
                a = marco.loc[marco[col_fuente] == fuentes[0]]
                b = marco.loc[marco[col_fuente] == fuentes[1]]
                k = min(objetivo, len(a), len(b))
                izq_partes.append(a.sample(n=k, random_state=self.config.semilla))
                der_partes.append(b.sample(n=k, random_state=self.config.semilla + 1))
        faltan = objetivo - sum(len(p) for p in izq_partes)
        if faltan > 0 and len(marco) >= 2:
            k = min(faltan, len(marco) // 2)
            posiciones = rng.permutation(len(marco))[: 2 * k].reshape(k, 2)
            izq_partes.append(marco.iloc[posiciones[:, 0]])
            der_partes.append(marco.iloc[posiciones[:, 1]])
        if not izq_partes:
            return pd.DataFrame()
        pares = self._construir_pares(pd.concat(izq_partes), pd.concat(der_partes), "easy_negative")
        if pares.empty:
            return pares
        # NIT_SIMILARITY sale del NIT canónico (NIT_OK) y vale 0 si falta
        # alguno de los dos; comparar el crudo (NIT_1/NIT_2, con DV o guiones)
        # dejaba entrar pares de la misma empresa como negativos.
        claros = (pares["NAME_SIMILARITY"] < self.config.umbral_ambiguo) & (
            pares["NIT_SIMILARITY"] < 1.0
        )
        pares = pares.loc[claros]
        llave = pd.DataFrame(
            {"a": pares[["ID_1", "ID_2"]].min(axis=1), "b": pares[["ID_1", "ID_2"]].max(axis=1)}
        )
        pares = pares.loc[~llave.duplicated().to_numpy()]
        return pares.head(n).reset_index(drop=True)

    # ── Construcción vectorizada de los pares ─────────────────────────────
    def _construir_pares(self, izq: pd.DataFrame, der: pd.DataFrame, clase: str) -> pd.DataFrame:
        """Filas alineadas de ``izq`` y ``der`` → un par por fila, con similitudes."""
        if len(izq) != len(der):
            raise ValueError(f"izq y der deben alinearse: {len(izq)} vs {len(der)} filas.")
        if izq.empty:
            return pd.DataFrame()
        from rapidfuzz import process as rf_process
        from rapidfuzz.distance import Levenshtein

        cfg = self.config
        nombre_1 = self._texto(izq, cfg.col_nombre)
        nombre_2 = self._texto(der, cfg.col_nombre)
        sim_nombre = self._comparar_nombres(nombre_1.to_numpy(), nombre_2.to_numpy())

        nit_1 = self._texto(izq, cfg.col_nit_canonico)
        nit_2 = self._texto(der, cfg.col_nit_canonico)
        sim_nit = rf_process.cpdist(
            nit_1.tolist(),
            nit_2.tolist(),
            scorer=Levenshtein.normalized_similarity,
            dtype=np.float64,
        )
        sim_nit = np.where((nit_1 != "").to_numpy() & (nit_2 != "").to_numpy(), sim_nit, 0.0)

        return pd.DataFrame(
            {
                "ID_1": izq[_COLUMNA_ID].to_numpy(),
                "ID_2": der[_COLUMNA_ID].to_numpy(),
                "NIT_1": self._texto(izq, cfg.col_nit).to_numpy(),
                "NIT_2": self._texto(der, cfg.col_nit).to_numpy(),
                "RAZON_SOCIAL_1": self._texto(izq, cfg.col_razon_social).to_numpy(),
                "RAZON_SOCIAL_2": self._texto(der, cfg.col_razon_social).to_numpy(),
                "SRC_1": self._texto(izq, cfg.col_fuente).to_numpy(),
                "SRC_2": self._texto(der, cfg.col_fuente).to_numpy(),
                "CASE_TYPE": clase,
                "NAME_SIMILARITY": np.round(sim_nombre, 4),
                "NIT_SIMILARITY": np.round(sim_nit, 4),
            }
        )

    @staticmethod
    def _texto(marco: pd.DataFrame, columna: str) -> pd.Series:
        """Columna como texto real, sin ausentes disfrazados; vacía si no existe."""
        if columna not in marco.columns:
            return pd.Series([""] * len(marco), index=marco.index, dtype=str)
        serie = _a_texto(marco[columna].to_numpy()).str.strip()
        serie.index = marco.index
        return serie
