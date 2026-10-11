"""matching.locales — locales compuestos: extender ``LOCALES`` sin tocar el registro a mano (F3.2).

``matching.normalizadores.LOCALES`` es un registro global por proceso con los sufijos legales y los
genéricos DECLARADOS de cada idioma. Dos flujos necesitan un locale compuesto —los de otros idiomas más
formas legales y genéricos propios del caso— y cada uno lo armaba a mano: ``flujo.importadores`` y
``flujo.vinculacion``. Esta función es el punto único donde se compone y se registra.

El registro sigue siendo global: dos locales con el mismo ``nombre`` se pisan. Quien componga varios en un
mismo proceso debe darles nombres distintos.
"""

from __future__ import annotations

from collections.abc import Iterable

from record_linkage.matching.normalizadores import LOCALES

__all__ = ["registrar_locale_compuesto"]


def registrar_locale_compuesto(
    nombre: str,
    *,
    bases: Iterable[str],
    sufijos_extra: Iterable[str] = (),
    genericos: Iterable[str] = (),
) -> str:
    """Registra en ``LOCALES`` un locale compuesto y devuelve su ``nombre``.

    Args:
        nombre: clave con la que queda registrado; es la que se pasa a ``normalizar_nombre(locale=...)``.
        bases: locales ya declarados cuyos sufijos legales se heredan (p. ej. ``("ES", "EN")``).
        sufijos_extra: formas legales adicionales; se normalizan a mayúsculas sin espacios de borde.
        genericos: palabras que no identifican (sector, geografía…); se guardan tal como llegan.

    Returns:
        ``nombre``. Es idempotente: registrar dos veces lo mismo deja el mismo locale.

    Raises:
        KeyError: alguna de las ``bases`` no está declarada (el mensaje lista las disponibles).
    """
    sufijos: set[str] = set()
    for base in bases:
        if base not in LOCALES:
            raise KeyError(
                f"Qué pasó: el locale base '{base}' no está declarado. "
                f"Disponibles: {sorted(LOCALES)}. "
                "Qué hacer: use uno de esos o declare el suyo en matching.normalizadores.LOCALES."
            )
        sufijos |= set(LOCALES[base]["sufijos"])
    sufijos |= {str(s).strip().upper() for s in sufijos_extra}
    LOCALES[nombre] = {"sufijos": frozenset(sufijos), "genericos": frozenset(genericos)}
    return nombre
