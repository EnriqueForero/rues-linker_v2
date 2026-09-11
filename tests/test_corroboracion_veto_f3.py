"""Tests de la corroboración de veto (F3, v0.11.0).

Verifican el contrato del veto condicional del motor multicampo:

    1. Con corroboración inactiva (default), el comportamiento es IDÉNTICO al
       previo a F3: los NITs distintos siguen vetando.
    2. Con corroboración activa, un par de NITs distintos se REÚNE si hay
       evidencia independiente fuerte (email/teléfono idéntico) + nombre
       muy similar.
    3. Salvaguardas anti-falso-positivo: la corroboración NO reúne entidades
       distintas que comparten un valor de baja entropía (email genérico
       gmail, teléfono de call center) ni valores faltantes/placeholder.
    4. La similitud de nombre es condición necesaria: sin ella, el veto no se
       levanta aunque el email coincida (homónimos con distinto giro).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.matching.campos import (
    CorroboracionVeto,
    EsquemaCampos,
    esquema_multicampo_completo,
)
from record_linkage.matching.motor_bloqueo import (
    BloqueoComponible,
    LlaveExacta,
    LSHTexto,
)
from record_linkage.matching.motor_multicampo import (
    clusters_desde_decisiones,
    evaluar_esquema,
)


def _bloqueo() -> BloqueoComponible:
    return BloqueoComponible(
        [
            LlaveExacta("NIT"),
            LlaveExacta("EMAIL"),
            LlaveExacta("TELEFONO"),
            LSHTexto("RAZON_SOCIAL", umbral=0.35, permutaciones=64, ngram=3),
        ]
    )


def _fila(reg_id, nit, razon, email="", tel="", ciudad="BOGOTA"):
    return {
        "REG_ID": reg_id,
        "NIT": nit,
        "RAZON_SOCIAL": razon,
        "EMAIL": email,
        "TELEFONO": tel,
        "DIRECCION": "",
        "CIUDAD": ciudad,
        "LATITUD": np.nan,
        "LONGITUD": np.nan,
    }


def _clusters(df: pd.DataFrame, esquema: EsquemaCampos) -> dict[int, int]:
    res = evaluar_esquema(df, esquema, _bloqueo())
    labels = clusters_desde_decisiones(len(df), res.decisiones, respetar_vetos=True)
    return dict(zip(df["REG_ID"].tolist(), labels.tolist(), strict=True))


# ── Fixtures de casos ───────────────────────────────────────────────────────


@pytest.fixture
def par_multi_nit_corroborado() -> pd.DataFrame:
    """Misma entidad, NITs distintos, email idéntico real y nombre idéntico."""
    return pd.DataFrame(
        [
            _fila(1, "900111111", "TEXTILES DEL PACIFICO SAS", email="info@textilpacifico.com"),
            _fila(2, "900222222", "TEXTILES DEL PACIFICO SAS", email="info@textilpacifico.com"),
        ]
    )


@pytest.fixture
def trampa_email_generico() -> pd.DataFrame:
    """Entidades distintas que comparten un email gmail genérico."""
    return pd.DataFrame(
        [
            _fila(1, "900111111", "PANADERIA LA ESPIGA SAS", email="negocios@gmail.com"),
            _fila(2, "900222222", "TRANSPORTES VELOZ SAS", email="negocios@gmail.com"),
        ]
    )


@pytest.fixture
def trampa_telefono_callcenter() -> pd.DataFrame:
    """Entidades distintas que comparten un teléfono de call center."""
    return pd.DataFrame(
        [
            _fila(1, "900333333", "SEGUROS DEL CARIBE SAS", tel="6015551000"),
            _fila(2, "900444444", "INMOBILIARIA COSTA SAS", tel="6015551000"),
        ]
    )


# ── Tests ────────────────────────────────────────────────────────────────────


def test_default_inactivo_mantiene_veto(par_multi_nit_corroborado) -> None:
    """Sin corroboración (default), NITs distintos siguen separados."""
    esq = esquema_multicampo_completo()
    assert esq.corroboracion.activa is False
    g = _clusters(par_multi_nit_corroborado, esq)
    assert g[1] != g[2], "sin corroboración, los NITs distintos no deben unirse"


def test_corroboracion_reune_multi_nit(par_multi_nit_corroborado) -> None:
    """Con corroboración, email idéntico + nombre idéntico reúne los NITs distintos."""
    esq = esquema_multicampo_completo()
    esq.corroboracion = CorroboracionVeto(campos_corroborantes=("EMAIL", "TELEFONO"), activa=True)
    g = _clusters(par_multi_nit_corroborado, esq)
    assert g[1] == g[2], "email idéntico + nombre idéntico debe levantar el veto"


def test_no_reune_por_email_generico(trampa_email_generico) -> None:
    """Un gmail compartido NO debe reunir entidades de nombre distinto."""
    esq = esquema_multicampo_completo()
    esq.corroboracion = CorroboracionVeto(campos_corroborantes=("EMAIL", "TELEFONO"), activa=True)
    g = _clusters(trampa_email_generico, esq)
    assert g[1] != g[2], "gmail genérico compartido no es evidencia suficiente"


def test_no_reune_por_telefono_callcenter(trampa_telefono_callcenter) -> None:
    """Un teléfono de call center compartido NO debe reunir entidades distintas."""
    esq = esquema_multicampo_completo()
    esq.corroboracion = CorroboracionVeto(campos_corroborantes=("EMAIL", "TELEFONO"), activa=True)
    g = _clusters(trampa_telefono_callcenter, esq)
    assert g[1] != g[2], "call center compartido no es evidencia suficiente"


def test_nombre_distinto_no_levanta_veto() -> None:
    """Email idéntico pero nombre distinto (homónimo cruzado) NO reúne."""
    df = pd.DataFrame(
        [
            _fila(1, "900111111", "CONSTRUCTORA ANDINA SAS", email="contacto@grupo.com"),
            _fila(2, "900222222", "ALIMENTOS DEL VALLE SAS", email="contacto@grupo.com"),
        ]
    )
    esq = esquema_multicampo_completo()
    esq.corroboracion = CorroboracionVeto(
        campos_corroborantes=("EMAIL",), umbral_nombre_empresa=0.90, activa=True
    )
    g = _clusters(df, esq)
    assert g[1] != g[2], "sin similitud de nombre, el veto no debe levantarse"


def test_faltante_no_corrobora() -> None:
    """Emails ambos vacíos no cuentan como corroboración (F2.4)."""
    df = pd.DataFrame(
        [
            _fila(1, "900111111", "TEXTILES DEL PACIFICO SAS", email=""),
            _fila(2, "900222222", "TEXTILES DEL PACIFICO SAS", email=""),
        ]
    )
    esq = esquema_multicampo_completo()
    esq.corroboracion = CorroboracionVeto(campos_corroborantes=("EMAIL",), activa=True)
    g = _clusters(df, esq)
    assert g[1] != g[2], "un email faltante en ambos lados no puede corroborar"


def test_config_invalida_activa_sin_campos() -> None:
    """CorroboracionVeto activa sin campos corroborantes es un error de config."""
    with pytest.raises(ValueError, match="al menos un campo corroborante"):
        CorroboracionVeto(activa=True)


def test_config_invalida_min_supera_campos() -> None:
    """min_corroborantes no puede superar el nº de campos corroborantes."""
    with pytest.raises(ValueError, match="supera el nº"):
        CorroboracionVeto(campos_corroborantes=("EMAIL",), min_corroborantes=2, activa=True)


def test_campo_corroborante_inexistente_falla() -> None:
    """El esquema rechaza un campo corroborante que no existe."""
    esq = esquema_multicampo_completo()
    with pytest.raises(ValueError, match="no existe en el esquema"):
        EsquemaCampos(
            campos=esq.campos,
            umbral_score=esq.umbral_score,
            min_concordancias=esq.min_concordancias,
            corroboracion=CorroboracionVeto(campos_corroborantes=("NO_EXISTE",), activa=True),
        )


def test_identificador_no_puede_corroborar() -> None:
    """El identificador vetado no puede ser su propio corroborante (circular)."""
    esq = esquema_multicampo_completo()
    with pytest.raises(ValueError, match="no puede ser el identificador"):
        EsquemaCampos(
            campos=esq.campos,
            umbral_score=esq.umbral_score,
            min_concordancias=esq.min_concordancias,
            corroboracion=CorroboracionVeto(campos_corroborantes=("NIT",), activa=True),
        )
