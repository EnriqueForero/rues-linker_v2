"""Tests del helper strip_emojis — cierre de deuda v0.7.4.

El regex original (Sprint 0.8.1) era incompleto: no cubría Misc Technical
(⏱ U+23F1), Misc Symbols & Arrows (⭐ U+2B50) ni el selector de variación
(U+FE0F). Eso dejaba warnings 'Glyph N missing from font' vivos por 3 sprints.
Estos tests fijan la cobertura para que no vuelva a romperse.
"""

from __future__ import annotations

import pytest

from record_linkage.reporting._text_utils import strip_emojis


@pytest.mark.parametrize(
    "char,nombre",
    [
        ("\u23f1", "STOPWATCH (Misc Technical) — el que rompía"),
        ("\u2b50", "WHITE MEDIUM STAR (Misc Symbols & Arrows) — el que rompía"),
        ("\ufe0f", "VARIATION SELECTOR-16 — el que rompía"),
        ("\u2600", "BLACK SUN (Misc Symbols)"),
        ("\u26a1", "HIGH VOLTAGE (Misc Symbols)"),
        ("\u2705", "WHITE HEAVY CHECK (Dingbats)"),
        ("\u26a0", "WARNING SIGN"),
        ("\U0001f4ca", "BAR CHART"),
        ("\U0001f3af", "DIRECT HIT / TARGET"),
        ("\U0001f517", "LINK"),
        ("\u25b6", "BLACK RIGHT-POINTING TRIANGLE (Geometric)"),
        ("\u2190", "LEFTWARDS ARROW"),
    ],
)
def test_strip_emojis_elimina_cada_simbolo(char, nombre):
    """Cada símbolo problemático debe desaparecer del texto."""
    entrada = f"Texto {char} con simbolo"
    salida = strip_emojis(entrada)
    assert char not in salida, f"{nombre} ({char!r}) sobrevivió: {salida!r}"


def test_strip_emojis_no_deja_ningun_no_ascii_de_emoji():
    """Conjunto representativo de los emojis usados en los reportes → 0 sobrevivientes."""
    cocktail = "⏱️ ⭐ ⚡ 📊 🎯 ✅ ⚠️ 🔗 🔍 📉 📥 🏆 💡 🔷 💾 ▶ ☑ ➡ ⬆"
    salida = strip_emojis(cocktail)
    no_ascii = [hex(ord(c)) for c in salida if ord(c) > 127]
    assert not no_ascii, f"Sobrevivieron símbolos no-ASCII: {no_ascii} en {salida!r}"


def test_strip_emojis_preserva_acentos_espanoles():
    """Acentos, ñ y caracteres latinos legítimos NO deben tocarse."""
    casos = [
        "Reducción: 55%",
        "Compañía Niño S.A.",
        "Évaluación rápida",
        "tasa 99.5% (alta)",
        "RAZÓN SOCIAL — A&B",
    ]
    for c in casos:
        assert strip_emojis(c) == c, f"Modificó texto legítimo: {c!r} -> {strip_emojis(c)!r}"


def test_strip_emojis_colapsa_espacios():
    """Tras quitar un emoji rodeado de espacios, no debe dejar doble espacio."""
    assert strip_emojis("Total 📊 procesado") == "Total procesado"
    assert strip_emojis("⭐ Inicio") == "Inicio"
    assert strip_emojis("Fin ⚡") == "Fin"


def test_strip_emojis_vacio_y_none_safe():
    """Entradas degeneradas no rompen."""
    assert strip_emojis("") == ""
    assert strip_emojis(None) is None  # type: ignore[arg-type]


def test_strip_emojis_texto_sin_emojis_inalterado():
    """Texto ASCII puro pasa sin cambios (salvo strip de extremos)."""
    assert strip_emojis("Hello World 123") == "Hello World 123"
