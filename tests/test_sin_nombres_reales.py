"""Ningún archivo del trabajo de vinculación nombra una empresa real (regla del proyecto).

El motor original, nacido de un piloto, traía ejemplos con marcas reales en docstrings, comentarios,
plantillas y textos de salida. Los archivos NUEVOS usan marcas inventadas (Zelvora, Kramento, Quelmar…).
Esta prueba busca, palabra por palabra, las marcas reales conocidas; la lista se guarda como hashes
SHA-256 para que el repositorio no contenga los nombres que esta prueba prohíbe. Al reescribir un notebook
o un documento, agréguelo a ARCHIVOS_VIGILADOS.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]

#: SHA-256 de la palabra en minúsculas de cada marca real vista en el motor original.
HASHES_PROHIBIDOS = frozenset(
    {
        "07973ee136c4e4d7d940683d6a1d78d045b3549e33df0d6678084286b0d2d7fe",
        "10be655f06c8ca34ccf5cfde9dfd608e22d0013e34d3a1990fd31a0454290cfc",
        "2699a7ce9d51c853fed1de1f5686153fbba2955954b35406cf58990497f5c624",
        "2b074dfd118f9134aa55dde98afc0feabde5ee707ff0123895db9e57715c74b5",
        "2c46aa11db28ef1fbad4cca57e897ac1d16c49e02d3a35fa8f298090b22c6b72",
        "4524a302bb3c1202d838b418188a938e26d3aeefdda5775e4382d5a2f4fb56af",
        "4ce1dfd1a0af2dfc372344ac551ec25e7d568ad113fc229af906e94ef6ac9101",
        "5666ce0e6bbdfa3028d2e142b7fb23ce537ec076cf9d5a2e57b9c73eea587c29",
        "63d1f5ae1b2672a130ab348bc2304b0b061138244df5e7ba318683c30f0893cb",
        "6dececcb4daccfa0d818571da532e15cc68dbfcc7c5572a76209ff5a1e8bf378",
        "712739d2658cd5ca8abba130cb35e9015aa0df2fbc0013a931aa854589146aee",
        "759ec5ad9490662142b25f82b4e5a1ee552370ad1594863a00ea9b35b492eb2d",
        "7ab15ed0304945feeb4cbc08f89b337d74a38e2a8be47a87aee13d33c9e2aa85",
        "7b8d4d167456dae82bf15f0e69e73f95dff48f2f4403d97ee07a7bdf1d401e8e",
        "7bf606063a6a0748dce3a043ffc5343402162b4672337b6f43835dcc2a8d2574",
        "90c1c28d81e54d97e1efaf25dfa6484dbc000896dca7381b779c161eba98a519",
        "a2daaca1abf688907bde2912c0bc8ebc07ba0b0b5109c17056eb2e3bce99d539",
        "b5c984cb30ce96a9b1590e8c31c811c848ec808b89f3e531e215160a9db5a6e2",
        "b876abc5347ce515291e2769a10e9538047fea315e84e66a0c5fece20da5509d",
        "c04fdcf16791729c02aa85abb0856fc19a55269e6ba99121a3c3fcdafc87d484",
        "c643e730336b1abf98a7a47dc77559aed1db5fd3bc1ce29019446a661cc255fb",
        "d88f7205c2ac02707794089d9c57b50a0f9c9066e0bdd20f921f59819fedbc18",
        "da30282cd4340f22f52fdc034a3a54af26bcfdbdfd69782823b123b0a3204170",
        "f4baf9f1ceab0139d0d887b94247a4e705bac366bcbe16ca64f4c32be8ed0df6",
        "f504bfa492db92a73eb2921995aa4380290f52e69f7cd4bf2e8000b64d4dd958",
        "f693662ed34bace51da31333e5cb522a9ab0619836c2d356256a6afa5282a54e",
        "fc921781776f3e657dd70dedda273ec9d6a42fb9f4489e47dc3df1cbc4230383",
    }
)

#: Patrones (relativos a la raíz) de lo ya reescrito con marcas inventadas.
ARCHIVOS_VIGILADOS = (
    "src/record_linkage/flujo/vinculacion/*.py",
    "src/record_linkage/flujo/vinculacion_excel.py",
    "src/record_linkage/flujo/vinculacion_excel/*.py",
    "tests/apoyo_vinculacion.py",
    "tests/lineas_base_vinculacion.py",
    "tests/test_vinculacion_*.py",
    "tests/test_apoyo_vinculacion.py",
    "tests/apoyo_*_vinculacion.py",
    "docs/REANUDAR.md",
    "docs/PLAN_EJECUCION.md",
    "docs/vinculacion/*.md",
    "scripts/oraculo_vinculacion/*.py",
    "scripts/oraculo_vinculacion/*.md",
)
_PALABRA = re.compile(r"[a-záéíóúüñ0-9]+")


def _archivos() -> list[Path]:
    vistos: dict[Path, None] = {}
    for patron in ARCHIVOS_VIGILADOS:
        for ruta in sorted(RAIZ.glob(patron)):
            vistos[ruta] = None
    return list(vistos)


def _palabras_prohibidas(texto: str) -> set[str]:
    # La «\b» de las expresiones regulares (patrones de agentes de carga) pegaría una «b» a la marca.
    texto = texto.lower().replace("\\b", " ")
    return {
        p
        for p in set(_PALABRA.findall(texto))
        if hashlib.sha256(p.encode("utf-8")).hexdigest() in HASHES_PROHIBIDOS
    }


def test_hay_archivos_que_vigilar():
    assert len(_archivos()) >= 4, "la prueba no está mirando nada: revise ARCHIVOS_VIGILADOS"


@pytest.mark.parametrize("ruta", _archivos(), ids=lambda r: str(r.relative_to(RAIZ)))
def test_el_archivo_no_nombra_empresas_reales(ruta):
    encontradas = _palabras_prohibidas(ruta.read_text(encoding="utf-8"))
    assert not encontradas, (
        f"{ruta.relative_to(RAIZ)} nombra una empresa real ({len(encontradas)} palabra(s) de la lista). "
        "Qué hacer: sustitúyala por una marca inventada (Zelvora, Kramento, Quelmar, Vantiko…)."
    )


def test_la_lista_prohibida_detecta_una_marca_real_conocida():
    # Comprueba la propia prueba: una de las marcas se reconstruye de sus letras para no escribirla aquí.
    marca = "".join(["k", "a", "u", "f", "l", "a", "n", "d"])
    assert _palabras_prohibidas(f"Vinculo con {marca.upper()} S.A.") == {marca}
    assert _palabras_prohibidas("Zelvora Alimentos y Kramento Foods") == set()
