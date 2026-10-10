"""Constantes compartidas del motor de vinculación.

Son datos DECLARADOS, no inferidos: palabras que no identifican a una empresa (conectores, nombres de
pila) y el orden en que se prefiere un nivel o una banda. F3.2 los traslada a ``matching.genericos``
para que la regla viva en un solo lugar.
"""

from __future__ import annotations

#: Nombres de pila frecuentes (ES/IT/DE/FR/EN): compartir SOLO uno de ellos no es evidencia de
#: identidad ('ALFREDO ZELVORA' ≠ 'Alfredo Kramento'; 'PAULA REBER' ≠ 'Paula'). No afecta a EMPRESA,
#: que exige que TODAS las palabras se correspondan.
_TEXTO_NOMBRES_PILA = """
JOSE JOSEP JOSEF JOSEPH GIUSEPPE ANTONIO ANTONI ANTOINE ANTON JUAN JEAN JOHN JOHANN JOHANNES GIOVANNI PAUL PABLO
PAOLO PAULO PEDRO PIETRO PIERRE PETER MANUEL LUIS LUIGI LOUIS LUDWIG LUCA LUCAS CARLOS CARLO CARL KARL CHARLES
FRANCISCO FRANCESCO FRANZ FRANCOIS FRANK MIGUEL MICHEL MICHAEL MICHELE ANDRES ANDREA ANDREAS ANDRE AUGUST AUGUSTO
ALFRED ALFREDO DAVID DAVIDE HENRI HENRY HEINRICH HEINZ ENRIQUE ENRICO HANS GUIDO MARIO MARIA MARIE ANNA ANA ROSA
CRISTINA CHRISTINA KRISTINA ELLA JULIE MONICA ROBERT ROBERTO RICHARD RICARDO WILHELM WILLIAM GUILLERMO FRIEDRICH
FEDERICO FERNANDO RAFAEL RAFFAELE ALFONSO ALESSANDRO ALEXANDER ALEJANDRO GEORG GEORGES GEORGE JORGE GIORGIO THOMAS
TOMAS MARTIN MARTINO OTTO ERICH ERIK EMIL EMILIO FELIX JAKOB JACOB JACQUES IGNACIO VICENTE VINCENZO SALVATORE
DOMENICO GABRIEL GABRIELE NICOLAS NICOLA SEBASTIAN SIMON VICTOR VITTORIO HERMANN OSCAR GUSTAV GUSTAVO PHILIPP
PHILIPPE FELIPE ALBERT ALBERTO ALBERTINE BERNARD BERNARDO ERNST WALTER WERNER KURT HELMUT JUERGEN JURGEN RAUL
"""
NOMBRES_PILA_BASE: frozenset[str] = frozenset(_TEXTO_NOMBRES_PILA.split())

#: Palabras de enlace: no identifican ni cuentan como diferencia.
CONECTORES: frozenset[str] = frozenset(
    {
        "Y", "E", "UND", "AND", "ET", "DE", "DEL", "DI", "DU", "VON", "V", "LA", "EL", "LOS", "LAS", "THE", "OF",
        "DA", "DO", "DER", "DEN", "VAN", "LE", "LES", "DES", "AL", "A", "EN", "CON", "FOR", "PER", "ZU", "ZUR",
        "IM", "AM", "GES", "D", "L", "I",
    }
)  # fmt: skip

#: Formas legales como PALABRAS sueltas (además de las frases que retira la librería).
LEGALES_BASE: frozenset[str] = frozenset(
    {
        "GMBH", "KG", "SA", "SL", "SLU", "SAU", "SRL", "SRLS", "SPA", "SAS", "SASU", "SARL", "BV", "NV", "AG", "SE",
        "LTD", "LIMITED", "INC", "LLC", "CO", "SOCIEDAD", "LIMITADA", "ANONIMA", "UNIPERSONAL", "SOCIETA",
        "RESPONSABILITA", "LIMITATA", "AZIONI", "SOCIETE", "ANONYME", "GESELLSCHAFT", "HAFTUNG", "BESCHRANKTER",
        "BESCHRAENKTER", "AKTIENGESELLSCHAFT", "STIFTUNG", "KGAA", "MBH", "LTDA", "STI", "COOP", "COOPERATIVA",
        "COOPERATIVE", "SCOOP", "OHG", "UG", "EG", "EV", "EURL", "SNC", "SCA", "SCARL", "SOCIO", "UNICO",
        "SIMPLIFIEE", "ACTIONS", "PLC", "CORP", "CIA", "SC", "SPZOO", "ZOO", "SP", "AS", "AB", "OY", "APS",
        "SUCURSAL", "BRANCH", "FILIALE", "CV", "RL", "DE CV",
    }
)  # fmt: skip

#: Preferencia entre niveles de vínculo (menor = mejor) y entre bandas de confianza.
ORDEN_NIVEL: dict[str, int] = {
    "EMPRESA": 0,
    "GRUPO": 1,
    "RELACION_PROPIEDAD": 2,
    "RELACION_MATRIZ": 3,
    "REVISAR": 4,
    "": 9,
}
ORDEN_BANDA: dict[str, int] = {"A": 0, "B": 1, "C": 2, "": 9}
