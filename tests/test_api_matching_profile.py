"""``_resolver_matching_profile`` (extraída de ``linkage()`` por el trinquete de complejidad)."""

from __future__ import annotations

import pytest

from record_linkage import api
from record_linkage.matching import default_colombia_profile_recall
from record_linkage.matching.spec import MatchingProfile


def test_nombres_conocidos_resuelven_a_perfiles() -> None:
    assert isinstance(api._resolver_matching_profile("colombia"), MatchingProfile)
    assert isinstance(api._resolver_matching_profile("international"), MatchingProfile)
    recall = api._resolver_matching_profile("colombia_recall")
    assert recall.name == default_colombia_profile_recall().name


def test_una_instancia_se_devuelve_tal_cual() -> None:
    perfil = default_colombia_profile_recall()
    assert api._resolver_matching_profile(perfil) is perfil


def test_nombre_desconocido_y_tipo_ajeno_fallan_con_las_opciones() -> None:
    with pytest.raises(ValueError, match=r"matching_profile string desconocido.*colombia_recall"):
        api._resolver_matching_profile("marte")
    with pytest.raises(TypeError, match="str o MatchingProfile"):
        api._resolver_matching_profile(42)
