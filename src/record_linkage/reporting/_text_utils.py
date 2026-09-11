"""reporting._text_utils — helpers internos de saneamiento de strings.

v0.7.1 (Sprint 0.8.1, Tarea 1.4): centraliza el filtrado de emojis que
matplotlib no puede renderizar con las fuentes del sistema (Liberation Sans,
default en Colab/Linux). Solo se usa en el último paso antes de pasar el
string a ``ax.text``/``set_title``/``suptitle``. Logs y exports a Excel/CSV
siguen mostrando emojis sin tocarlos.

v0.7.4 (cierre de deuda): el rango original era INCOMPLETO — no cubría
Misc Technical (⏱ U+23F1), Misc Symbols & Arrows (⭐ U+2B50) ni el selector
de variación (U+FE0F). Eso dejaba warnings 'Glyph N missing from font' vivos
desde el Sprint 0.8.1. Ahora la cobertura es exhaustiva sobre los bloques
Unicode donde viven los símbolos/emojis usados por los reportes.
"""

from __future__ import annotations

import re

# Cobertura exhaustiva de bloques Unicode con símbolos/emojis/dingbats.
# Cada rango está anotado con el bloque que cubre para mantenibilidad.
_EMOJI_RE = re.compile(
    "["
    "\u2190-\u21ff"  # Arrows
    "\u2300-\u23ff"  # Misc Technical (⏱ ⏭ ⌚ …)
    "\u2460-\u24ff"  # Enclosed Alphanumerics
    "\u25a0-\u25ff"  # Geometric Shapes (▶ ◀ …)
    "\u2600-\u26ff"  # Misc Symbols (☀ ☑ ⚠ ⚡ …)
    "\u2700-\u27bf"  # Dingbats (✅ ✂ ✈ …)
    "\u2b00-\u2bff"  # Misc Symbols & Arrows (⭐ ⬆ ⬇ …)
    "\ufe00-\ufe0f"  # Variation Selectors (el ️ que sigue a ⏱)
    "\U0001f000-\U0001f02f"  # Mahjong Tiles
    "\U0001f0a0-\U0001f0ff"  # Playing Cards
    "\U0001f100-\U0001f1ff"  # Enclosed Alphanumeric Supplement (banderas regionales)
    "\U0001f200-\U0001f2ff"  # Enclosed Ideographic Supplement
    "\U0001f300-\U0001f5ff"  # Misc Symbols and Pictographs
    "\U0001f600-\U0001f64f"  # Emoticons
    "\U0001f650-\U0001f67f"  # Ornamental Dingbats
    "\U0001f680-\U0001f6ff"  # Transport and Map Symbols
    "\U0001f700-\U0001f77f"  # Alchemical Symbols
    "\U0001f780-\U0001f7ff"  # Geometric Shapes Extended
    "\U0001f800-\U0001f8ff"  # Supplemental Arrows-C
    "\U0001f900-\U0001f9ff"  # Supplemental Symbols and Pictographs
    "\U0001fa00-\U0001fa6f"  # Chess Symbols / Symbols and Pictographs Extended-A
    "\U0001fa70-\U0001faff"  # Symbols and Pictographs Extended-A
    "]+"
)


def strip_emojis(text: str) -> str:
    """Elimina emojis/símbolos no renderizables y normaliza espacios.

    Cubre exhaustivamente los bloques Unicode de símbolos y emojis (incluido
    el selector de variación U+FE0F que sigue a algunos símbolos). Tras la
    sustitución, colapsa espacios múltiples a uno y recorta extremos.

    Args:
        text: Cadena posiblemente con emojis. Si es vacía o ``None``, se
            devuelve sin tocar.

    Returns:
        El mismo texto sin emojis ni símbolos no renderizables.
    """
    if not text:
        return text
    cleaned = _EMOJI_RE.sub("", text)
    return re.sub(r"\s+", " ", cleaned).strip()
