#!/usr/bin/env python
"""construir_benchmark.py — Arma el conjunto de referencia institucional.

Contexto: el banco de pruebas de `rues-linker` necesita un conjunto que sea a
la vez REAL (para que las conclusiones se transfieran) y ETIQUETADO (para que
las métricas signifiquen algo). Ninguna de las fuentes disponibles cumple las
dos cosas por separado, así que el conjunto se arma por estratos y cada
estrato aporta lo que sabe aportar.

    ESTRATO      ORIGEN                        APORTA
    REAL         CRM × RUES × SuperSociedades  variación real de nombre,
                                               geografía, CIIU, tamaño,
                                               personas y empresas, NIT con
                                               y sin dígito verificador
    REAL_NEG     pares minados del CRM         negativos duros REALES:
                                               identificador distinto y
                                               nombre muy parecido
    RUIDO        Ground_Truth_Robusto_V3       niveles de ruido etiquetados
                                               (CLEAN…EXTREME) y 606 casos
                                               negativos diseñados
    CONTACTO     ground_truth_grande           teléfono, dirección, correo,
                                               ciudad e intermediarios

Por qué el archivo del CRM NO puede ser la verdad por sí solo
--------------------------------------------------------------
Es la SALIDA de un proceso de cruce anterior, no una verificación. Medir
contra él sería medir el acuerdo con ese proceso, no el acierto. Se usa solo
la parte anclada en identificador y marcada "Alta Confianza", que es la que
un identificador sostiene con independencia del algoritmo; las 5.122 filas
marcadas "Revisión Manual" se excluyen de la verdad en vez de contarse como
positivas o negativas.

USO
    python scripts/construir_benchmark.py --salida data/benchmark

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-29  ·  Version: 0.19.0
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

SEMILLA = 42

#: Esquema único del conjunto. Toda fila lo cumple, venga del estrato que venga.
COLUMNAS = (
    "ID_REGISTRO",
    "ID_GROUP",
    "ESTRATO",
    "FUENTE",
    "TIPO_ENTIDAD",
    "REGIMEN",
    "CASO",
    "NIVEL_RUIDO",
    "VARIACION",
    "NIT",
    "RAZON_SOCIAL",
    "DEPARTAMENTO",
    "MUNICIPIO",
    "CIIU",
    "TAMANO",
    "TELEFONO",
    "DIRECCION",
    "EMAIL",
)

#: Sufijos societarios que no distinguen a una empresa de otra.
_SUFIJOS = (
    r"\b(S A S|SAS|S A|SA|LTDA|LTD|E U|EU|S EN C|SCA|ESAL|INC|CORP|CO|LLC|"
    r"SOCIEDAD|EMPRESA|FUNDACION|ASOCIACION|EN LIQUIDACION)\b"
)


@dataclass(frozen=True)
class Fuentes:
    """Rutas de los insumos del benchmark."""

    crm: Path
    robusto: Path
    contacto: Path

    def __post_init__(self) -> None:
        for campo in ("crm", "robusto", "contacto"):
            ruta = getattr(self, campo)
            if not Path(ruta).is_file():
                raise FileNotFoundError(f"No existe el insumo '{campo}': {ruta}")


#: Nombre real de la columna del CRM -> alias seguro para acceso por atributo.
#: `itertuples` renombra a `_7`, `_8`… cualquier columna con punto o tilde, y el
#: número depende del ORDEN del archivo. Un cambio de orden en el origen movería
#: los datos de columna en silencio, así que aquí no se accede nunca por posición.
ALIAS_CRM = {
    "NAME": "CRM_NOMBRE",
    "TIPO_DE_IDENTIFICACION__C": "CRM_TIPO_ID",
    "NUMERO_DE_IDENTIFICACION__C": "CRM_NIT",
    "RECORDTYPE.NAME": "CRM_CLASE",
    "DEPARTAMENTO__C": "CRM_DEPARTAMENTO",
    "CIUDAD__R.NAME": "CRM_MUNICIPIO",
    "REGLA_COINCIDENCIA_CRM_RUES": "REGLA",
    "NIT_RUES": "RUES_NIT",
    "RAZON_SOCIAL_RUES": "RUES_NOMBRE",
    "DEPARTAMENTO_RUES": "RUES_DEPARTAMENTO",
    "MUNICIPIO_RUES": "RUES_MUNICIPIO",
    "CIIU_RUES": "RUES_CIIU",
    "TAMA\u00d1O_RUES": "RUES_TAMANO",
    "NIT_SSC": "SSC_NIT",
    "RAZON_SOCIAL_SSC": "SSC_NOMBRE",
    "DEPARTAMENTO_SSC": "SSC_DEPARTAMENTO",
    "MUNICIPIO_SSC": "SSC_MUNICIPIO",
    "CIIU_SSC": "SSC_CIIU",
    "TAMA\u00d1O_SSC": "SSC_TAMANO",
}

#: Columnas del CRM que NO entran al conjunto porque son copia de la fuente con
#: la que se va a comparar. `TAMANO_RUES__C` coincide con `TAMA\u00d1O_RUES` en el
#: 90,4 % de los casos: es el resultado de un cruce anterior guardado en el CRM.
#: Usarla como atributo del registro del CRM regalaría la respuesta al enlazador.
COLUMNAS_CONTAMINADAS = ("TAMANO_RUES__C", "SECTOR_PRINCIPAL__R.NAME")


def preparar_crm(crm: pd.DataFrame) -> pd.DataFrame:
    """Renombra las columnas del CRM a alias seguros y valida que estén todas."""
    faltan = [c for c in ALIAS_CRM if c not in crm.columns]
    if faltan:
        raise KeyError(f"Al archivo del CRM le faltan columnas: {faltan}")
    return crm[list(ALIAS_CRM)].rename(columns=ALIAS_CRM)


def normalizar(texto: object) -> str:
    """Mayúsculas, sin tildes y sin puntuación: la forma para comparar."""
    plano = unicodedata.normalize("NFKD", str(texto).upper())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", plano)).strip()


def sin_sufijo(texto: str) -> str:
    """Quita los sufijos societarios, que no identifican a nadie."""
    return re.sub(r"\s+", " ", re.sub(_SUFIJOS, " ", texto)).strip()


def clasificar_variacion(izquierda: pd.Series, derecha: pd.Series) -> np.ndarray:
    """Etiqueta cómo difieren dos nombres del mismo ente.

    La taxonomía sale de medir 56.119 pares reales anclados en identificador;
    las frecuencias observadas están en `docs/BENCHMARK.md`. Sirve para
    muestrear conservando la dificultad real en vez de una inventada.
    """
    a, b = izquierda.map(normalizar), derecha.map(normalizar)
    a2, b2 = a.map(sin_sufijo), b.map(sin_sufijo)
    ta = a2.str.split().map(frozenset)
    tb = b2.str.split().map(frozenset)
    contenido = [x <= y or y <= x for x, y in zip(ta, tb, strict=True)]
    mayoria = [len(x & y) / max(1, len(x | y)) >= 0.5 for x, y in zip(ta, tb, strict=True)]
    alguno = [len(x & y) > 0 for x, y in zip(ta, tb, strict=True)]
    return np.select(
        [
            (a == b).to_numpy(),
            (a2 == b2).to_numpy(),
            np.array([x == y for x, y in zip(ta, tb, strict=True)]),
            np.array(contenido),
            np.array(mayoria),
            np.array(alguno),
        ],
        [
            "identico",
            "sufijo_legal",
            "orden_alterado",
            "uno_contiene_al_otro",
            "mayoria_de_tokens",
            "algun_token",
        ],
        default="sin_tokens_comunes",
    )


def _fila(**campos: object) -> dict[str, object]:
    """Construye una fila del esquema, con vacío en lo que no aplica."""
    base = dict.fromkeys(COLUMNAS, "")
    base.update(
        {
            k: ("" if v is None or (isinstance(v, float) and pd.isna(v)) else v)
            for k, v in campos.items()
        }
    )
    return base


def estrato_real(crm: pd.DataFrame, entidades: int) -> pd.DataFrame:
    """Entidades reales con sus 2 o 3 registros de fuente.

    Se muestrea de forma estratificada por categoría de variación de nombre,
    para que el conjunto conserve la dificultad REAL: en los datos de
    ProColombia, el 21,7 % de los enlaces verdaderos no comparte ningún token
    entre el nombre del CRM y el del RUES.

    Cada registro lleva SOLO los atributos que su fuente conoce por sí misma.
    El CRM aporta su geografía propia (departamento y ciudad de contacto), no
    la del RUES; el CIIU y el tamaño se quedan del lado de quien los publica.
    """
    confiables = crm[crm["REGLA"].fillna("").str.contains("Alta Confianza")].copy()
    confiables["VARIACION_NOMBRE"] = clasificar_variacion(
        confiables["CRM_NOMBRE"], confiables["RUES_NOMBRE"]
    )
    proporcion = confiables["VARIACION_NOMBRE"].value_counts(normalize=True)
    partes = []
    for categoria, fraccion in proporcion.items():
        bloque = confiables[confiables["VARIACION_NOMBRE"] == categoria]
        cuantos = min(len(bloque), max(1, round(entidades * fraccion)))
        partes.append(bloque.sample(cuantos, random_state=SEMILLA))
    muestra = pd.concat(partes).sample(frac=1.0, random_state=SEMILLA).reset_index(drop=True)

    filas: list[dict[str, object]] = []
    for posicion, registro in enumerate(muestra.itertuples(index=False)):
        grupo = f"REAL-{posicion:06d}"
        tipo = "persona" if _es_persona(registro.CRM_TIPO_ID, registro.CRM_CLASE) else "empresa"
        variacion = registro.VARIACION_NOMBRE
        comun = {
            "ID_GROUP": grupo,
            "ESTRATO": "REAL",
            "TIPO_ENTIDAD": tipo,
            "REGIMEN": "CON_ID",
            "CASO": "positivo_real",
            "NIVEL_RUIDO": "REAL",
            "VARIACION": variacion,
        }
        filas.append(
            _fila(
                **comun,
                ID_REGISTRO=f"{grupo}-CRM",
                FUENTE="CRM",
                NIT=registro.CRM_NIT,
                RAZON_SOCIAL=registro.CRM_NOMBRE,
                DEPARTAMENTO=registro.CRM_DEPARTAMENTO,
                MUNICIPIO=registro.CRM_MUNICIPIO,
            )
        )
        filas.append(
            _fila(
                **comun,
                ID_REGISTRO=f"{grupo}-RUES",
                FUENTE="RUES",
                NIT=registro.RUES_NIT,
                RAZON_SOCIAL=registro.RUES_NOMBRE,
                DEPARTAMENTO=registro.RUES_DEPARTAMENTO,
                MUNICIPIO=registro.RUES_MUNICIPIO,
                CIIU=registro.RUES_CIIU,
                TAMANO=registro.RUES_TAMANO,
            )
        )
        if pd.notna(registro.SSC_NIT) and str(registro.SSC_NIT).strip():
            filas.append(
                _fila(
                    **comun,
                    ID_REGISTRO=f"{grupo}-SSC",
                    FUENTE="SUPERSOCIEDADES",
                    NIT=registro.SSC_NIT,
                    RAZON_SOCIAL=registro.SSC_NOMBRE,
                    DEPARTAMENTO=registro.SSC_DEPARTAMENTO,
                    MUNICIPIO=registro.SSC_MUNICIPIO,
                    CIIU=registro.SSC_CIIU,
                    TAMANO=registro.SSC_TAMANO,
                )
            )
    return pd.DataFrame(filas, columns=list(COLUMNAS))


#: `TIPO_DE_IDENTIFICACION__C` no basta para saber si un registro es persona:
#: hay cédulas escritas de dos formas y pasaportes. `RECORDTYPE.NAME` lo dice
#: de frente. Se usan las dos señales porque ninguna está completa.
_TIPOS_PERSONA = frozenset({"Cédula", "Cedula", "Pasaporte"})

#: Umbral de similitud para considerar que dos nombres son confundibles.
#: 0,90 de Jaro-Winkler sobre el nombre sin sufijo societario: por encima de
#: ahí un humano tiene que leer dos veces para ver que son entes distintos.
SIMILITUD_NEGATIVO = 0.90

#: Fracción de los negativos de empresa a la que se le borra el identificador.
#: Con NIT, separarlos es trivial y solo comprueba que el veto por documento
#: funciona; sin NIT, separarlos exige usar geografía, CIIU o tamaño, que es
#: justo la capacidad multicriterio que este banco existe para medir.
FRACCION_NEGATIVO_SIN_ID = 0.5

#: Reparto de los negativos entre los dos fenómenos, que son distintos:
#: empresas de nombre casi igual (problema de razón social) y personas
#: homónimas (problema de identificador). El CRM real es 45 % personas, pero
#: esta es una librería de enlace EMPRESARIAL: el peso va a las empresas.
FRACCION_NEGATIVO_EMPRESA = 0.65


def _es_persona(tipo: object, clase: object) -> bool:
    """Persona natural según cualquiera de las dos señales del CRM."""
    return str(tipo) in _TIPOS_PERSONA or str(clase) == "Cuenta personal"


def _minar_confundibles(
    nombres: pd.Series, cuantos: int, *, umbral: float = SIMILITUD_NEGATIVO
) -> pd.Index:
    """Devuelve el índice de registros cuyo nombre se confunde con otro.

    Se bloquea por los dos tokens más largos del nombre sin sufijo —los
    largos son los que cargan la identidad— y dentro de cada bloque se exige
    similitud de Jaro-Winkler por encima del umbral. Un bloque solo no basta:
    'UNIDAD DE' agrupa entes que no se parecen en nada.
    """
    from rapidfuzz.distance import JaroWinkler

    limpio = nombres.map(normalizar).map(sin_sufijo)
    limpio = limpio[limpio.str.len() >= 8]
    firma = limpio.str.split().map(lambda t: " ".join(sorted(sorted(t, key=len, reverse=True)[:2])))
    elegidos: list[object] = []
    for _, bloque in limpio.groupby(firma):
        if not 2 <= len(bloque) <= 8:
            continue
        indices, textos = list(bloque.index), list(bloque)
        emparejados = set()
        for a in range(len(textos)):
            for b in range(a + 1, len(textos)):
                if JaroWinkler.normalized_similarity(textos[a], textos[b]) >= umbral:
                    emparejados.update((indices[a], indices[b]))
        elegidos.extend(sorted(emparejados, key=str))
        if len(elegidos) >= cuantos:
            break
    return pd.Index(elegidos[:cuantos])


def estrato_negativos_reales(crm: pd.DataFrame, cuantos: int) -> pd.DataFrame:
    """Entes DISTINTOS con nombres confundibles, tomados de datos reales.

    Un negativo inventado mide la imaginación de quien lo inventa. Estos se
    minan de dos poblaciones con garantías distintas:

    * **Empresas del RUES.** El NIT del registro mercantil es único por
      construcción, así que dos NIT distintos son dos entes distintos: la
      etiqueta negativa no depende de ningún juicio. Traen geografía, CIIU y
      tamaño, de modo que a la mitad se le borra el identificador y separarlas
      exige usar esas variables. Ese es el caso que mide criterio múltiple.
    * **Personas homónimas del CRM.** Cuatro 'Diana Rodríguez' con cédulas
      distintas son cuatro personas. Del nombre no sale nada; separarlas mide
      si el enlazador respeta el documento en vez de dejarse llevar por el
      parecido. Es el modo de falla más caro en producción y por eso está.
    """
    objetivo_empresa = int(cuantos * FRACCION_NEGATIVO_EMPRESA)
    filas: list[dict[str, object]] = []

    empresas = crm[crm["RUES_NIT"].notna()].copy()
    empresas["_canonico"] = canonicalizar_identificador(empresas["RUES_NIT"])
    empresas = empresas[empresas["_canonico"].str.len() >= 6]
    empresas = empresas.drop_duplicates("_canonico")
    elegidas = _minar_confundibles(empresas["RUES_NOMBRE"], objetivo_empresa)
    corte = int(len(elegidas) * FRACCION_NEGATIVO_SIN_ID)
    for posicion, indice in enumerate(elegidas):
        registro = empresas.loc[indice]
        sin_id = posicion < corte
        grupo = f"NEGE-{posicion:06d}"
        filas.append(
            _fila(
                ID_REGISTRO=f"{grupo}-RUES",
                ID_GROUP=grupo,
                ESTRATO="REAL_NEG",
                FUENTE="RUES",
                TIPO_ENTIDAD="empresa",
                REGIMEN="SIN_ID" if sin_id else "CON_ID",
                CASO=(
                    "negativo_empresa_similar_sin_id"
                    if sin_id
                    else "negativo_empresa_similar_con_id"
                ),
                NIVEL_RUIDO="REAL",
                VARIACION="entes_distintos_nombre_confundible",
                NIT="" if sin_id else registro["RUES_NIT"],
                RAZON_SOCIAL=registro["RUES_NOMBRE"],
                DEPARTAMENTO=registro["RUES_DEPARTAMENTO"],
                MUNICIPIO=registro["RUES_MUNICIPIO"],
                CIIU=registro["RUES_CIIU"],
                TAMANO=registro["RUES_TAMANO"],
            )
        )

    es_persona = np.fromiter(
        (_es_persona(t, c) for t, c in zip(crm["CRM_TIPO_ID"], crm["CRM_CLASE"], strict=True)),
        dtype=bool,
        count=len(crm),
    )
    personas = crm[crm["RUES_NIT"].isna().to_numpy() & es_persona].copy()
    personas["_canonico"] = canonicalizar_identificador(personas["CRM_NIT"])
    personas = personas[personas["_canonico"].str.len() >= 6]
    personas = personas.drop_duplicates("_canonico")
    homonimas = _minar_confundibles(personas["CRM_NOMBRE"], cuantos - len(elegidas))
    for posicion, indice in enumerate(homonimas):
        registro = personas.loc[indice]
        grupo = f"NEGP-{posicion:06d}"
        filas.append(
            _fila(
                ID_REGISTRO=f"{grupo}-CRM",
                ID_GROUP=grupo,
                ESTRATO="REAL_NEG",
                FUENTE="CRM",
                TIPO_ENTIDAD="persona",
                REGIMEN="CON_ID",
                CASO="negativo_homonimo_persona",
                NIVEL_RUIDO="REAL",
                VARIACION="personas_distintas_mismo_nombre",
                NIT=registro["CRM_NIT"],
                RAZON_SOCIAL=registro["CRM_NOMBRE"],
                DEPARTAMENTO=registro["CRM_DEPARTAMENTO"],
                MUNICIPIO=registro["CRM_MUNICIPIO"],
            )
        )
    return pd.DataFrame(filas, columns=list(COLUMNAS))


def estrato_ruido(robusto: pd.DataFrame) -> pd.DataFrame:
    """Ground_Truth_Robusto_V3: variación tipográfica con nivel etiquetado.

    Con una corrección deliberada. El archivo original aplica ruido también
    al NIT: dentro de un mismo grupo aparecen 894939884, 895239884, 894139814
    y 8949398844. Los tres últimos no son el mismo número escrito distinto,
    son números DISTINTOS: cambiar dos dígitos de un NIT produce, con alta
    probabilidad, el NIT válido de otra empresa.

    Medido sobre este archivo: 899 de 1.800 grupos (49,9 %) tienen un NIT que
    no se recupera con ninguna canonicalización, y esos grupos concentran
    17.055 de los 18.629 pares del estrato. Dejarlos como están obligaría al
    enlazador a unir registros con identificadores distintos, que es
    exactamente lo que los 1.040 negativos le exigen NO hacer. El conjunto se
    contradiría a sí mismo y ninguna configuración podría alcanzar F1 = 1.

    La corrección: donde el identificador se recupera canonicalizando
    (puntuación, ceros a la izquierda, dígito de verificación) se conserva y
    el grupo queda en régimen CON_ID —esa variación es real y el enlazador
    tiene que resolverla—. Donde hay corrupción de dígitos se borra el
    identificador y el grupo pasa a SIN_ID: se conserva íntegro el valor real
    del archivo, que es el ruido ETIQUETADO sobre la razón social, y se
    descarta la parte que no se puede sostener.
    """
    trabajo = robusto.copy()
    trabajo["CANONICO"] = canonicalizar_identificador(trabajo["NIT"])
    distintos = trabajo.groupby("ID_GROUP")["CANONICO"].transform("nunique")
    trabajo["ID_CORRUPTO"] = (distintos > 1).to_numpy()

    filas = []
    for posicion, registro in enumerate(trabajo.itertuples(index=False)):
        grupo = f"RUIDO-{registro.ID_GROUP}"
        negativo = "NEGATIVO" in str(registro.VARIATION_TYPE).upper()
        corrupto = bool(registro.ID_CORRUPTO) and not negativo
        tiene_id = bool(str(registro.NIT).strip()) and not corrupto
        if negativo:
            caso = "negativo_diseñado"
        elif corrupto:
            caso = "positivo_ruido_sin_id"
        else:
            caso = "positivo_ruido"
        filas.append(
            _fila(
                ID_REGISTRO=f"RUIDO-{posicion:06d}",
                ID_GROUP=grupo,
                ESTRATO="RUIDO",
                FUENTE=str(registro.SOURCE),
                TIPO_ENTIDAD="empresa",
                REGIMEN="CON_ID" if tiene_id else "SIN_ID",
                CASO=caso,
                NIVEL_RUIDO=registro.NOISE_LEVEL,
                VARIACION=registro.VARIATION_TYPE,
                NIT=registro.NIT if tiene_id else "",
                RAZON_SOCIAL=registro.RAZON_SOCIAL,
            )
        )
    return pd.DataFrame(filas, columns=list(COLUMNAS))


def estrato_contacto(contacto: pd.DataFrame) -> pd.DataFrame:
    """ground_truth_grande: aporta teléfono, dirección, correo e intermediarios."""
    filas = []
    for registro in contacto.itertuples(index=False):
        filas.append(
            _fila(
                ID_REGISTRO=f"CONT-{registro.ID_REGISTRO}",
                ID_GROUP=f"CONT-{registro.ID_GROUP}",
                ESTRATO="CONTACTO",
                FUENTE=registro.FUENTE,
                TIPO_ENTIDAD="empresa",
                REGIMEN="CON_ID" if str(registro.REGIMEN) == "CON_NIT" else "SIN_ID",
                CASO=registro.CASO,
                NIVEL_RUIDO="SINTETICO",
                VARIACION=registro.CASO,
                NIT=registro.NIT,
                RAZON_SOCIAL=registro.RAZON_SOCIAL,
                MUNICIPIO=registro.CIUDAD,
                TELEFONO=registro.TELEFONO,
                DIRECCION=registro.DIRECCION,
                EMAIL=registro.EMAIL,
            )
        )
    return pd.DataFrame(filas, columns=list(COLUMNAS))


def construir(fuentes: Fuentes, entidades_reales: int, negativos: int) -> pd.DataFrame:
    """Une los cuatro estratos en un solo conjunto con esquema único."""
    crudo = (
        pd.read_excel(fuentes.crm, dtype=str)
        if fuentes.crm.suffix == ".xlsx"
        else pd.read_parquet(fuentes.crm)
    )
    crm = preparar_crm(crudo)
    robusto = (
        pd.read_excel(fuentes.robusto, sheet_name="Datos_Completos", dtype=str)
        if fuentes.robusto.suffix == ".xlsx"
        else pd.read_parquet(fuentes.robusto)
    )
    contacto = pd.read_csv(fuentes.contacto, dtype=str, keep_default_na=False)

    partes = [
        estrato_real(crm, entidades_reales),
        estrato_negativos_reales(crm, negativos),
        estrato_ruido(robusto),
        estrato_contacto(contacto),
    ]
    conjunto = pd.concat(partes, ignore_index=True)
    conjunto = conjunto.fillna("").astype(str).replace({"nan": "", "None": "", "NaT": ""})
    conjunto, descartes = sanear(conjunto)
    conjunto.attrs["saneamiento"] = descartes
    return conjunto


def canonicalizar_identificador(valores: pd.Series) -> pd.Series:
    """Deja el identificador en su forma comparable: dígitos, sin ceros a la
    izquierda y sin dígito de verificación cuando trae diez."""
    digitos = valores.astype(str).str.replace(r"\D", "", regex=True).str.lstrip("0")
    return digitos.where(digitos.str.len() != 10, digitos.str[:-1])


def sanear(conjunto: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Quita la contaminación que haría inevitable un error de medición.

    Dos grupos distintos que comparten identificador canónico no son dos
    entes: son el mismo partido en dos, y unirlos —que es lo correcto— se
    contaría como falso positivo. Eso no mide al enlazador, mide un defecto
    del conjunto. Se conserva el primer grupo y se descartan los demás.

    Lo mismo con los nombres idénticos ENTRE estratos: dentro del estrato de
    contacto la repetición de nombres genéricos es la dificultad buscada y se
    respeta; entre estratos es un choque accidental de dos universos que no
    se conocen.
    """
    trabajo = conjunto.copy()
    trabajo["_id"] = canonicalizar_identificador(trabajo["NIT"])
    trabajo["_nombre"] = trabajo["RAZON_SOCIAL"].map(normalizar)

    con_id = trabajo[trabajo["_id"].str.len() >= 6]
    grupos_por_id = con_id.groupby("_id")["ID_GROUP"].nunique()
    conflictivos = grupos_por_id[grupos_por_id > 1].index
    a_descartar: set[str] = set()
    for identificador in conflictivos:
        grupos = sorted(con_id.loc[con_id["_id"] == identificador, "ID_GROUP"].unique())
        a_descartar.update(grupos[1:])
    descartados_por_id = len(a_descartar)

    entre_estratos = (
        trabajo[trabajo["_nombre"].str.len() >= 8].groupby("_nombre")["ESTRATO"].nunique()
    )
    nombres_cruzados = entre_estratos[entre_estratos > 1].index
    cruce = trabajo[trabajo["_nombre"].isin(nombres_cruzados)]
    for _, bloque in cruce.groupby("_nombre"):
        grupos = sorted(bloque["ID_GROUP"].unique())
        a_descartar.update(grupos[1:])

    limpio = trabajo[~trabajo["ID_GROUP"].isin(a_descartar)].drop(columns=["_id", "_nombre"])
    return limpio.reset_index(drop=True), {
        "grupos_descartados_por_identificador_compartido": descartados_por_id,
        "grupos_descartados_por_nombre_compartido_entre_estratos": len(a_descartar)
        - descartados_por_id,
        "grupos_descartados_total": len(a_descartar),
    }


def manifiesto(conjunto: pd.DataFrame) -> dict[str, object]:
    """Composición del conjunto, para publicarlo junto al archivo."""
    tamanos = conjunto.groupby("ID_GROUP").size()
    pares = int((tamanos * (tamanos - 1) // 2).sum())
    return {
        "registros": len(conjunto),
        "grupos": int(conjunto["ID_GROUP"].nunique()),
        "pares_verdaderos": pares,
        "por_estrato": conjunto["ESTRATO"].value_counts().to_dict(),
        "por_fuente": conjunto["FUENTE"].value_counts().to_dict(),
        "por_regimen": conjunto["REGIMEN"].value_counts().to_dict(),
        "por_tipo_entidad": conjunto["TIPO_ENTIDAD"].value_counts().to_dict(),
        "por_caso": conjunto["CASO"].value_counts().to_dict(),
        "por_nivel_ruido": conjunto["NIVEL_RUIDO"].value_counts().to_dict(),
        "cobertura_de_variables": {
            columna: round(float((conjunto[columna] != "").mean()), 4)
            for columna in (
                "NIT",
                "RAZON_SOCIAL",
                "DEPARTAMENTO",
                "MUNICIPIO",
                "CIIU",
                "TAMANO",
                "TELEFONO",
                "DIRECCION",
                "EMAIL",
            )
        },
        "tamano_de_grupo": tamanos.value_counts().sort_index().head(10).to_dict(),
        "semilla": SEMILLA,
        "saneamiento": conjunto.attrs.get("saneamiento", {}),
    }


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--crm", type=Path, required=True)
    parser.add_argument("--robusto", type=Path, required=True)
    parser.add_argument(
        "--contacto", type=Path, default=RAIZ / "data" / "ground_truth" / "ground_truth_grande.csv"
    )
    parser.add_argument("--salida", type=Path, default=RAIZ / "data" / "benchmark")
    parser.add_argument("--entidades-reales", type=int, default=4_500)
    parser.add_argument("--negativos", type=int, default=800)
    args = parser.parse_args(argv)

    fuentes = Fuentes(crm=args.crm, robusto=args.robusto, contacto=args.contacto)
    conjunto = construir(fuentes, args.entidades_reales, args.negativos)
    args.salida.mkdir(parents=True, exist_ok=True)
    # Comprimido: 6,0 MB → 1,05 MB. pandas y la ingesta de la librería
    # reconocen la extensión y descomprimen solos, así que el archivo se usa
    # igual que si fuera texto plano y el repositorio no carga 6 MB por
    # versión del conjunto.
    destino = args.salida / "benchmark_institucional.csv.gz"
    conjunto.to_csv(destino, index=False, compression="gzip")
    info = manifiesto(conjunto)
    (args.salida / "benchmark_institucional.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(info, indent=2, ensure_ascii=False))
    print(f"\n💾 {destino}  ({destino.stat().st_size / 1024**2:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
