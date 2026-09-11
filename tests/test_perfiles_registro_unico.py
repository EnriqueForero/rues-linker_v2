"""Registro único de perfiles (F1.2, v0.9.0).

Congela el contrato de la unificación: config/profiles.py es la ÚNICA fuente
de verdad; pipeline/_internal.py reexporta LOS MISMOS objetos (identidad, no
copia — hay scripts y tests históricos que los mutan); get_profile falla
rápido listando lo disponible; y la validación de rangos corre al importar.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from record_linkage.config import profiles as cp
from record_linkage.pipeline import _internal as pi

_INTERNAL_SRC = (
    Path(__file__).resolve().parents[1] / "src" / "record_linkage" / "pipeline" / "_internal.py"
)


def test_reexport_es_el_mismo_objeto() -> None:
    """Mutar el registro vía el alias histórico debe ser mutar el canónico."""
    assert pi.PROFILES is cp.PERFILES_MOTOR
    assert pi.DEDUPLICATION_PROFILES is cp.PERFILES_DEDUPLICACION
    assert pi.ALL_PROFILES == cp.REGISTRO_PERFILES
    assert len(cp.REGISTRO_PERFILES) == len(cp.PERFILES_MOTOR) + len(cp.PERFILES_DEDUPLICACION)


def test_get_profile_devuelve_el_objeto_real() -> None:
    p = cp.get_profile("deduplication_standard")
    assert p is cp.PERFILES_DEDUPLICACION["deduplication_standard"]
    assert p["lsh_permutations"] == 128


def test_get_profile_falla_rapido_listando_disponibles() -> None:
    with pytest.raises(KeyError) as exc:
        cp.get_profile("perfil_inexistente")
    msg = str(exc.value)
    assert "perfil_inexistente" in msg
    assert "deduplication_standard" in msg  # lista del registro
    assert "produccion_estandar" in msg  # y señala la otra familia


def test_internal_no_redefine_perfiles() -> None:
    """Prohibido reintroducir literales de perfiles en _internal (guard CI)."""
    src = _INTERNAL_SRC.read_text(encoding="utf-8")
    assert "PROFILES = {" not in src, (
        "pipeline/_internal.py volvió a definir un registro de perfiles: "
        "la fuente única es config/profiles.py (F1.2)."
    )
    assert "from ..config.profiles import" in src


def test_validacion_de_rangos_activa() -> None:
    """La validación fail-fast rechaza valores fuera de rango."""
    with pytest.raises(ValueError, match="fuera de rango"):
        cp.PERFILES_DEDUPLICACION["deduplication_standard"]["lsh_threshold"] = 7.0
        try:
            cp._validar_registro()
        finally:  # restaurar SIEMPRE (el registro es un objeto vivo compartido)
            cp.PERFILES_DEDUPLICACION["deduplication_standard"]["lsh_threshold"] = 0.3
    cp._validar_registro()  # restaurado → vuelve a pasar
