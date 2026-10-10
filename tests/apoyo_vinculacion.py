"""Insumos INVENTADOS para probar la vinculación de empresas (F3).

Ninguna empresa de este módulo existe ni procede de una base licenciada: las
marcas se arman con sílabas sueltas (``BALVENTA``, ``KLORNELIA``…). Sirve para
dos cosas:

* **Casos manuales** (``_casos_manuales``): cada uno ejerce UNA regla del motor
  y lleva una etiqueta (``EMP_A_IDENTICO``, ``GRP_B_OTRO_PAIS``…) con la que las
  pruebas buscan su fila en las tablas de salida. La etiqueta dice qué regla se
  quiso ejercer; lo que el motor decide lo fijan las pruebas, no este archivo.
* **Bloque aleatorio** (``_bloque_aleatorio``): cientos de registros con
  perturbaciones (forma legal, errata, truncamiento, alias, otro país…) y
  semilla fija. No prueba una regla: amplía la cobertura de las pruebas de
  paridad con el motor original.

``construir_insumos`` escribe los archivos en una carpeta y devuelve sus rutas.
``configurar`` arma la ``ConfigVinculacion`` con el módulo que se le pase (el
motor original del notebook o el paquete nuevo), para que las dos
implementaciones lean exactamente la misma configuración.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# ── Vocabulario inventado ────────────────────────────────────────────────────
_SILABAS_1 = ["BA", "CA", "DO", "FE", "GA", "HI", "JO", "KA", "LA", "MI", "NO", "PU", "RA", "SE", "TO",
              "VA", "ZE", "BRU", "CRA", "DRO", "FRI", "GLA", "KLO", "PLE", "TRA"]  # fmt: skip
_SILABAS_2 = ["LV", "RN", "ST", "MB", "ND", "RK", "LM", "SP", "NT", "RD"]
_FINALES = [
    "ENTA",
    "ARIA",
    "IDO",
    "ORA",
    "UNDO",
    "ELIA",
    "ANTO",
    "IMAR",
    "OVEX",
    "ITAN",
    "ADEL",
    "ONIA",
]
_SECTORES = ["ALIMENTOS", "BEBIDAS", "LACTEOS", "CARNICOS", "FRUTAS", "CEREALES", "CONSERVAS", "VINOS",
             "ACEITES", "DULCES", "CHOCOLATES", "GALLETAS"]  # fmt: skip
_COLAS_LARGAS = [" DEL VALLE Y LA SIERRA", " DE LA RIBERA ALTA", " Y HERMANOS DEL CAMPO ABIERTO"]
_GENERICOS_FINALES = ["HOLDING", "IBERIA", "INTERNATIONAL", "GROUP", "EUROPE"]
_CIUDADES = ["VALENCIA", "HAMBURGO", "LYON", "TURIN", "ROTTERDAM", "BILBAO", "MUNICH", "MARSELLA"]
# (nombre en español de la ancla, nombre en inglés de Orbis, ISO3 que el catálogo reconoce)
_PAISES = [
    ("España", "Spain", "ESP"),
    ("Alemania", "Germany", "DEU"),
    ("Francia", "France", "FRA"),
    ("Italia", "Italy", "ITA"),
    ("Países Bajos", "Netherlands", "NLD"),
    ("Estados Unidos", "United States", "USA"),
]
#: Grafías que el catálogo de la librería no trae (hacen falta para Orbis en inglés).
ALIAS_PAISES = {"FRA": ("FRANCE",), "ITA": ("ITALY",)}
FORMAS_LEGALES_EXTRA = frozenset({"SOCIEDAD COOPERATIVA", "A SOCIO UNICO", "S COOP"})
GENERICOS_EXTRA = frozenset(
    {"HOLDING", "IBERIA", "INTERNATIONAL", "GROUP", "EUROPE", "NORTE", "SUR"}
) | frozenset(_SECTORES)
PATRONES_INTERMEDIARIO = (r"\bLOGISTIC", r"\bCARGO\b", r"\bFREIGHT\b", r"\bAS AGENTS? OF\b")
DOMINIOS_GENERICOS = frozenset({"gmail.com", "hotmail.com", "outlook.com"})

#: Variantes de configuración sobre los mismos insumos: ejercen ramas que el umbral por defecto no alcanza
#: (el parecido de cadena sin palabra compartida topa en ≈ 0,72, por ejemplo).
ESCENARIOS = {
    "base": {},
    "relajado": {"umbral_parecido": 0.70, "umbral_piso_empresa": 0.60, "umbral_dedup": 0.95},
    "automatico_puro": {
        "con_decisiones": False,
        "max_ediciones_palabra": 0,
        "propagar_por_web": False,
        "marca_sin_validar": "",
    },
    "errata_doble": {"max_ediciones_palabra": 2, "umbral_dedup": 0.70},
}

COL_ANCLA = [
    "Empresa",
    "País de Empresa",
    "Operaciones",
    "Valor U$S",
    "Kilos",
    "Fletes",
    "HS6",
    "Descripción",
    "Régimen",
    "NIF",
    "Teléfono",
    "Correo",
    "Sitio web",
    "Ciudad",
]
COL_ORBIS = ["Company name Latin alphabet", "Country", "BvD ID number", "GUO - Name", "Website address",
             "E-mail address", "Telephone number", "City Latin Alphabet", "Address line 1 Latin Alphabet",
             "Operating revenue (Turnover) m USD Last avail. yr", "Number of employees Last avail. yr", "VAT",
             "Contactos"]  # fmt: skip
COL_FDI = ["Company name", "País de consulta", "HQ country", "Parent company", "Website", "Company email",
           "Company telephone #", "HQ city (Parent)", "HQ address", "No. of projects (in current search)",
           "Primary sector"]  # fmt: skip


@dataclass
class InsumosVinculacion:
    """Rutas de los archivos escritos y las etiquetas de los casos manuales."""

    carpeta: Path
    archivo_ancla: Path
    archivo_descargas: Path
    archivo_decisiones: Path
    archivo_decisiones_2: Path
    #: etiqueta → {"clave_ancla": "País|Nombre", "id_orbis": ..., "id_fdi": ...} (solo lo que aplica)
    casos: dict = field(default_factory=dict)
    #: filas de cada hoja, por si una prueba necesita el contenido sin releer el Excel
    ancla: pd.DataFrame | None = None
    orbis: pd.DataFrame | None = None
    fdi: pd.DataFrame | None = None


class _Libro:
    """Acumula filas de las tres bases y las etiquetas de los casos manuales."""

    def __init__(self) -> None:
        self.ancla: list[dict] = []
        self.orbis: list[dict] = []
        self.fdi: list[dict] = []
        self.casos: dict[str, dict] = {}
        self._n_orbis = 0

    def a(self, tag: str, nombre: str, pais: str = "España", *, ops: int = 3, valor: float = 12000.0,
          kilos: float = 800.0, hs6: object = "190590", desc: str = "Galletas", nif: str = "", tel: str = "", correo: str = "",
          web: str = "", ciudad: str = "") -> None:  # fmt: skip
        self.ancla.append({"Empresa": nombre, "País de Empresa": pais, "Operaciones": ops, "Valor U$S": valor,
                           "Kilos": kilos, "Fletes": 0.0, "HS6": hs6, "Descripción": desc,
                           "Régimen": "EXPORTACION DEFINITIVA", "NIF": nif, "Teléfono": tel,
                           "Correo": correo, "Sitio web": web, "Ciudad": ciudad})  # fmt: skip
        if tag:
            self.casos.setdefault(tag, {})["clave_ancla"] = f"{pais}|{nombre}"

    def o(self, tag: str, nombre: str, pais: str = "Spain", *, id_: str = "", guo: str = "", web: str = "",
          email: str = "", tel: str = "", ciudad: str = "", direccion: str = "", ingresos: float = 5.5,
          empleados: int = 40, vat: str = "") -> str:  # fmt: skip
        self._n_orbis += 1
        id_ = id_ or f"ESB{self._n_orbis:07d}"
        self.orbis.append({"Company name Latin alphabet": nombre, "Country": pais, "BvD ID number": id_,
                           "GUO - Name": guo, "Website address": web, "E-mail address": email,
                           "Telephone number": tel, "City Latin Alphabet": ciudad,
                           "Address line 1 Latin Alphabet": direccion,
                           "Operating revenue (Turnover) m USD Last avail. yr": ingresos,
                           "Number of employees Last avail. yr": empleados, "VAT": vat,
                           "Contactos": "Your access does not allow you to see this field"})  # fmt: skip
        if tag:
            self.casos.setdefault(tag, {}).setdefault("ids_orbis", []).append(id_)
        return id_

    def f(self, tag: str, nombre: str, pais: str = "España", *, hq: str = "", matriz: str = "", web: str = "",
          email: str = "", tel: str = "", ciudad: str = "", direccion: str = "", proyectos: int = 2,
          sector: str = "Food") -> None:  # fmt: skip
        self.fdi.append({"Company name": nombre, "País de consulta": pais, "HQ country": hq or pais,
                         "Parent company": matriz, "Website": web, "Company email": email,
                         "Company telephone #": tel, "HQ city (Parent)": ciudad, "HQ address": direccion,
                         "No. of projects (in current search)": proyectos, "Primary sector": sector})  # fmt: skip
        if tag:
            self.casos.setdefault(tag, {}).setdefault("nombres_fdi", []).append(nombre)


def _casos_manuales(L: _Libro) -> None:
    """Un caso por regla. Todas las marcas son inventadas."""
    # ── EMPRESA, banda A: mismo nombre normalizado y mismo país ─────────────────
    L.a("EMP_A_IDENTICO", "Zelvora Alimentos S.A.", "España")
    L.o("EMP_A_IDENTICO", "ZELVORA ALIMENTOS SA", "Spain")
    L.a("EMP_A_FORMA_LEGAL", "Kramento Foods S.L.", "España")
    L.o("EMP_A_FORMA_LEGAL", "Kramento Foods, S.L.U.", "Spain")
    L.a("EMP_A_TILDES_Y_CASO", "Orbélia Conservas", "España")
    L.o("EMP_A_TILDES_Y_CASO", "ORBELIA CONSERVAS", "Spain")
    # ── EMPRESA, banda B: errata, truncamiento, alias, nombre común, sin país ─────
    L.a(
        "EMP_B_ERRATA", "Tavorín Conservas Marinas", "España"
    )  # inserción de una letra en el destino
    L.o("EMP_B_ERRATA", "TAVORIIN CONSERVAS MARINAS SL", "Spain")
    L.a(
        "EMP_B_SUSTITUCION_LARGA", "Nordavia Industrias Conserveras", "Italia"
    )  # 11 letras: se admite
    L.o("EMP_B_SUSTITUCION_LARGA", "NORDAVIA INDUSTRIAS CONSERVERAZ", "Italy")
    L.a(
        "EMP_NO_SUSTITUCION_CORTA", "Galmira Dulces", "España"
    )  # 6 letras: una sustitución no se admite
    L.o("EMP_NO_SUSTITUCION_CORTA", "GALMIRO DULCES SL", "Spain")
    L.a(
        "EMP_B_TRUNCADO", "COOPERATIVA DE CONSERVAS DEL CANTAB", "España"
    )  # 35 caracteres: corte de aduana
    L.o("EMP_B_TRUNCADO", "COOPERATIVA DE CONSERVAS DEL CANTABRICO SL", "Spain")
    L.a(
        "EMP_B_TRUNCADO_UNA_LETRA", "CONSERVAS Y SALAZONES DEL CANTABRIC", "España"
    )  # una letra: errata
    L.o("EMP_B_TRUNCADO_UNA_LETRA", "CONSERVAS Y SALAZONES DEL CANTABRICO SL", "Spain")
    L.a("EMP_B_ALIAS", "Peluvika Dulces (Dulcevent)", "España")
    L.o("EMP_B_ALIAS", "DULCEVENT SA", "Spain")
    L.a("EMP_B_NOMBRE_COMUN", "Quelmar", "España")
    L.o("EMP_B_NOMBRE_COMUN", "QUELMAR SL", "Spain")
    for sector in ("LACTEOS", "BEBIDAS", "CARNICOS"):  # vuelven «frecuente» la palabra del nombre
        L.o("", f"QUELMAR {sector} SL", "Spain")
    L.a("EMP_B_SIN_PAIS", "Mirelda Chocolates", "")
    L.o("EMP_B_SIN_PAIS", "MIRELDA CHOCOLATES SA", "Spain")
    L.a("EMP_B_INTERMEDIARIO", "Logistica Norte Cargo S.L.", "España")
    L.o("EMP_B_INTERMEDIARIO", "LOGISTICA NORTE CARGO SL", "Spain")
    # ── GRUPO: mismo nombre en otro país, marca con sobrantes genéricos ────────────
    L.a("GRP_B_OTRO_PAIS", "Holvexa Foods GmbH", "Alemania")
    L.o("GRP_B_OTRO_PAIS", "HOLVEXA FOODS", "Spain")
    L.a("GRP_C_OTRO_PAIS_ERRATA", "Fervano Bebidas", "Alemania")
    L.o("GRP_C_OTRO_PAIS_ERRATA", "FERVANNO BEBIDAS", "Spain")
    L.a("GRP_B_MARCA_GENERICOS", "Galdimar", "España")
    L.o("GRP_B_MARCA_GENERICOS", "GALDIMAR HOLDING IBERIA SA", "Spain")
    L.a("GRP_C_MARCA_OTRO_PAIS", "Juvenda Foods", "España")
    L.o("GRP_C_MARCA_OTRO_PAIS", "JUVENDA FOODS GROUP INC", "United States")
    # ── Candidatos C (REVISAR): palabra compartida, sigla, parecido ──────────────
    L.a("REV_C_COMPARTE_PALABRA", "Korimba Quesos", "España")
    L.o("REV_C_COMPARTE_PALABRA", "KORIMBA ACEITES SL", "Spain")
    L.a("REV_C_SIGLA", "PLVK SLU", "España")
    L.o("REV_C_SIGLA", "PELUVIKA SLU", "Spain")
    # ── Bordes de las reglas: palabra de 4 letras, marca de 5, sigla larga, desempate por tamaño ──
    L.a(
        "EMP_B_ERRATA_PALABRA_4", "Mirvana Luna", "España"
    )  # 4 letras es el mínimo para admitir errata
    L.o("EMP_B_ERRATA_PALABRA_4", "MIRVANA LUNAS SL", "Spain")
    L.a("GRP_B_MARCA_5_LETRAS", "Zorva Foods GmbH", "Alemania")  # la marca más corta que da banda B
    L.o("GRP_B_MARCA_5_LETRAS", "ZORVA FOODS", "Spain")
    L.a("REV_C_SIGLA_LARGA", "KR EMP VAL ALIM IB", "España")  # 5 fichas: el máximo que busca siglas
    L.o("REV_C_SIGLA_LARGA", "KRONOS EMPRESARIAL VALDORIA ALIMENTACION IBERICA SL", "Spain")
    L.a("PRINCIPAL_POR_TAMANO", "Pelvorta", "España")
    L.o("PRINCIPAL_POR_TAMANO", "PELVORTA IBERIA SA", "Spain", ingresos=50.0)
    L.o("PRINCIPAL_POR_TAMANO", "PELVORTA HOLDING SL", "Spain", ingresos=500.0)
    # ── Matriz reportada (RELACION_MATRIZ, banda C) ────────────────────────────
    L.a("MATRIZ_REPORTADA", "Solenta Alimentos S.A.", "España")
    L.o("MATRIZ_REPORTADA", "ARDENTA SA", "Spain", guo="Solenta Alimentos SA")
    L.a("MATRIZ_SIN_DECISION", "Valdoria Conservas S.A.", "España")
    L.o("MATRIZ_SIN_DECISION", "RODALTA SA", "Spain", guo="Valdoria Conservas SA")
    # ── Propagación por sitio web ─────────────────────────────────────────────
    L.a("WEB_EMPRESA", "Vantiko Chocolates S.A.", "España")
    L.o("WEB_EMPRESA", "VANTIKO CHOCOLATES SA", "Spain", web="www.vantiko.es", ciudad="Valencia")
    L.f(
        "WEB_EMPRESA",
        "Vantiko Sweets",
        "España",
        hq="Spain",
        web="https://vantiko.es/inicio",
        ciudad="Valencia",
    )
    L.f(
        "WEB_GRUPO_B", "Vantiko USA", "España", hq="United States", web="vantiko.es", ciudad="Miami"
    )
    L.f(
        "WEB_GRUPO_C",
        "Marca Nueva Ibérica",
        "España",
        hq="Spain",
        web="vantiko.es",
        ciudad="Madrid",
    )
    # ── Variables adicionales: identificador comparable, teléfono, correo ────────
    L.a("EVID_ID_COMPARABLE", "Casa Lumbreras Hermanos", "España", nif="B-1234 5678")
    L.o("EVID_ID_COMPARABLE", "LUMBRERAS ACEITES SL", "Spain", vat="B12345678")
    L.a("EVID_TELEFONO", "Castelbro Vinos", "España", tel="+34 961 234 567")
    L.o("EVID_TELEFONO", "CASTELBRO LICORES SL", "Spain", tel="961234567")
    L.f("EVID_TELEFONO", "Castelbro Spirits", "España", hq="Spain", tel="0034961999999")
    L.a("EVID_CORREO", "Ibertona Frutas", "España", correo="exportacion@ibertona.es")
    L.o("EVID_CORREO", "IBERTONA ZUMOS SL", "Spain", email="ventas@ibertona.es")
    L.a("EVID_CORREO_GENERICO", "Marbelia Carnes", "España", correo="marbelia@gmail.com")
    L.o("EVID_CORREO_GENERICO", "MARBELIA EMBUTIDOS SL", "Spain", email="marbelia@gmail.com")
    L.a(
        "EVID_WEB",
        "Tulvanto Quesos",
        "España",
        web="https://www.tulvanto.es/contacto",
        ciudad="Bilbao",
    )
    L.o("EVID_WEB", "TULVANTO LACTEOS ARTESANOS SL", "Spain", web="tulvanto.es", ciudad="Bilbao")
    L.a("REV_C_SIN_DECISION", "Tulvanto Aceites", "España")
    L.o("REV_C_SIN_DECISION", "TULVANTO VINAGRES SL", "Spain")
    # ── Deduplicación interna de la ancla ─────────────────────────────────────
    for variante in (
        "Brumalia Cereales SA",
        "BRUMALIA CEREALES S.A.",
        "Brumalia Cereales",
        "Brumalia Cereales SA",
    ):
        L.a("DUP_MISMO_NOMBRE", variante, "España", ops=1)
    L.a("DUP_ESPACIOS", "GB Foods", "España", ops=2)
    L.a("", "GBFoods", "España", ops=2)
    L.a("DUP_ERRATA_PENDIENTE", "Dromanto Vinagres", "España")
    L.a("", "Dromanto Vinagress", "España")
    L.a("DUP_ERRATA_AUTOMATICA", "Dromanto Vinagres Artesanales", "España")
    L.a("", "Dromanto Vinagres Artesanalez", "España")
    L.a("DUP_FORMA_LEGAL", "Fribelia SL", "España")
    L.a("", "Fribelia S. COOP", "España")
    L.a("DUP_SIGLA", "Garandel Foods", "España")
    L.a("", "GRDL", "España")
    L.a("DUP_ALIAS_MARCA", "Pustorán Foods (Lamberto)", "España")
    L.a("", "Lamberto", "España")
    L.a("DUP_TRUNCADA", "INDUSTRIAS ALIMENTARIAS TRIVENTOLA DEL SU", "España")
    L.a("", "INDUSTRIAS ALIMENTARIAS TRIVENTOLA DEL SUR", "España")
    L.a("DUP_VETADO", "Sementa Pastas", "España")
    L.a("", "Sementa Pastas SA", "España")
    L.a("DUP_FORZADO", "Cravinto Salsas", "España")
    L.a("", "Cravinto Foods", "España")
    # ── Deduplicación interna de los destinos ───────────────────────────────────
    L.a("DEST_RAIZ_ID", "Triventa Bebidas", "España")
    L.o("DEST_RAIZ_ID", "TRIVENTA BEBIDAS SA", "Spain", id_="ESB9000001")
    L.o("", "TRIVENTA BEBIDAS SA - SUCURSAL", "Spain", id_="ESB9000001-1003")
    L.a("DEST_SIRET", "Brandelia Fromages", "Francia")
    L.o("DEST_SIRET", "BRANDELIA FROMAGES SAS", "France", id_="FR12345678900012")
    L.o("", "BRANDELIA FROMAGES SAS", "France", id_="FR12345678900034")
    L.a("DEST_FDI_REPETIDO", "Nolmendo Cervezas", "España")
    L.f("DEST_FDI_REPETIDO", "Nolmendo Cervezas", "España", hq="Spain")
    L.f("", "Nolmendo Cervezas", "España", hq="Spain")
    # ── Calidad de datos ─────────────────────────────────────────────────────
    L.a("SIN_NOMBRE_UTIL", "S.L.", "España")
    L.a("ENTIDAD_HTML", "Sardovia &AMP; Hijos SL", "España")
    L.a("PAIS_SIN_COBERTURA", "Pelmonte Foods", "Colombia")
    for k, kilos in enumerate((500.0, 500.0, 120.0)):  # Kilos repetido en misma empresa y partida
        L.a(
            "KILOS_REPETIDOS",
            "Balvarda Mieles SL",
            "España",
            ops=k + 1,
            kilos=kilos,
            hs6=1704.9 if k < 2 else 1806.32,
        )
    L.a("INTERMEDIARIO_SIN_DESTINO", "Transportes Rapidos Freight SA", "España")


def _nombre_base(rng: random.Random, usados: set[str]) -> str:
    while True:
        stem = rng.choice(_SILABAS_1) + rng.choice(_SILABAS_2) + rng.choice(_FINALES)
        if stem not in usados:
            usados.add(stem)
            return stem


def _perturbar(rng: random.Random, nombre: str, forma: str, *, largo: bool) -> str:
    """Una variante plausible del mismo nombre: forma legal, caso, errata, alias, orden."""
    opcion = rng.choice(["forma", "forma", "caso", "errata", "alias", "orden", "ident", "ident"])
    palabras = nombre.split()
    if opcion == "forma":
        return f"{nombre} {rng.choice([forma, 'S.L.', 'SA', 'SRL', 'GMBH'])}"
    if opcion == "caso":
        return nombre.title()
    if opcion == "errata" and len(palabras[0]) >= 6:
        i = rng.randrange(1, len(palabras[0]) - 1)
        return " ".join([palabras[0][:i] + palabras[0][i + 1 :], *palabras[1:]]) + f" {forma}"
    if opcion == "alias":
        return f"{nombre} ({palabras[0].title()})"
    if opcion == "orden" and len(palabras) >= 2:
        return " ".join([palabras[1], palabras[0], *palabras[2:]])
    if largo and opcion == "ident":
        return (nombre + f" {forma}")[:35]
    return nombre + f" {forma}"


def _bloque_aleatorio(L: _Libro, rng: random.Random, n: int) -> list[dict]:
    """Empresas aleatorias con ancla, Orbis y FDI; devuelve la verdad de campo para las decisiones."""
    usados: set[str] = set()
    verdad: list[dict] = []
    for i in range(n):
        stem = _nombre_base(rng, usados)
        sector = rng.choice(_SECTORES)
        largo = rng.random() < 0.25
        base = f"{stem} {sector}" + (rng.choice(_COLAS_LARGAS) if largo else "")
        es, en, _ = rng.choice(_PAISES)
        forma = rng.choice(["SA", "SL", "S.A.", "SRL", "GMBH", "SAS"])
        dominio = f"{stem.lower()}.{rng.choice(['es', 'com', 'de'])}"
        # La ancla puede traer varias grafías de la misma empresa.
        grafias = [
            _perturbar(rng, base, forma, largo=largo) for _ in range(1 + (rng.random() < 0.3))
        ]
        for g in grafias:
            L.a("", g, es, ops=rng.randint(1, 40), valor=float(rng.randint(500, 900000)),
                kilos=float(rng.randint(10, 90000)), hs6=rng.choice(["190590", "180632", "170490"]),
                desc="Productos", nif=f"X{i:07d}" if rng.random() < 0.1 else "")  # fmt: skip
        tipo = "ninguno"
        if rng.random() < 0.55:
            otro_pais = rng.random() < 0.15
            pais_o = rng.choice([p[1] for p in _PAISES if p[1] != en]) if otro_pais else en
            nombre_o = _perturbar(rng, base, forma, largo=False).upper()
            if rng.random() < 0.2:
                nombre_o += f" {rng.choice(_GENERICOS_FINALES)}"
            id_o = L.o("", nombre_o, pais_o, web=f"www.{dominio}" if rng.random() < 0.6 else "",
                       email=f"info@{dominio}" if rng.random() < 0.4 else "",
                       tel=f"+34 9{rng.randint(10, 99)} {rng.randint(100, 999)} {rng.randint(100, 999)}"
                       if rng.random() < 0.4 else "",
                       ciudad=rng.choice(_CIUDADES) if rng.random() < 0.7 else "",
                       guo=f"{stem} GLOBAL" if rng.random() < 0.1 else "",
                       vat=f"X{i:07d}" if rng.random() < 0.1 else "")  # fmt: skip
            tipo = "orbis_otro_pais" if otro_pais else "orbis"
            verdad.append({"clave_ancla": f"{es}|{grafias[0]}", "fuente": "ORBIS", "id": id_o, "nombre": nombre_o,
                           "tipo": tipo})  # fmt: skip
        if rng.random() < 0.4:
            nombre_f = rng.choice([stem.title(), _perturbar(rng, base, forma, largo=False).title()])
            L.f("", nombre_f, es, hq=es, web=dominio if rng.random() < 0.5 else "",
                ciudad=rng.choice(_CIUDADES) if rng.random() < 0.6 else "",
                matriz=f"{stem.title()} Group" if rng.random() < 0.1 else "")  # fmt: skip
            verdad.append({"clave_ancla": f"{es}|{grafias[0]}", "fuente": "FDI", "id": None, "nombre": nombre_f,
                           "tipo": "fdi"})  # fmt: skip
        if rng.random() < 0.2:  # homónimo parcial de otro sector: debe quedar como candidato
            L.o(
                "",
                f"{stem} {rng.choice([s for s in _SECTORES if s != sector])} {forma}".upper(),
                en,
            )
    for _ in range(25):  # ancla sin pareja
        L.a("", f"{_nombre_base(rng, usados)} {rng.choice(_SECTORES)} {rng.choice(['SA', 'SL'])}",
            rng.choice(_PAISES)[0], ops=rng.randint(1, 9))  # fmt: skip
    for _ in range(40):  # Orbis sin pareja
        L.o("", f"{_nombre_base(rng, usados)} {rng.choice(_SECTORES)} {rng.choice(['SA', 'SL'])}",
            rng.choice(_PAISES)[1])  # fmt: skip
    for _ in range(30):  # FDI sin pareja
        L.f(
            "",
            f"{_nombre_base(rng, usados).title()} {rng.choice(_SECTORES).title()}",
            rng.choice(_PAISES)[0],
        )
    return verdad


def _decisiones(
    L: _Libro, verdad: list[dict], rng: random.Random
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Dos archivos de decisiones: el primero revisa; el segundo contradice y trae claves inexistentes."""
    cols = [
        "TIPO",
        "FUENTE",
        "CLAVE_A",
        "NOMBRE_A",
        "CLAVE_B",
        "NOMBRE_B",
        "DECISION",
        "AUTOR",
        "FECHA",
        "RAZON",
    ]
    fila_fdi = {
        n: i + 2 for i, n in enumerate(f["Company name"] for f in L.fdi)
    }  # FILA_EXCEL = índice + 2
    filas: list[list] = []

    def vinculo(
        fuente: str, clave_a: str, id_b: str, nombre_b: str, decision: str, autor: str, razon: str
    ) -> None:
        filas.append(["VINCULO", fuente, clave_a, clave_a.split("|", 1)[1], id_b, nombre_b, decision, autor,
                      "2026-09-29", razon])  # fmt: skip

    # Casos manuales: una decisión de cada tipo sobre una regla distinta.
    orbis_de = {r["BvD ID number"]: r["Company name Latin alphabet"] for r in L.orbis}
    for tag, decision, autor in (("EMP_A_IDENTICO", "EMPRESA", "Revisora Uno"),
                                 ("EMP_B_INTERMEDIARIO", "EMPRESA", "Revisora Uno"),
                                 ("REV_C_COMPARTE_PALABRA", "GRUPO", "Revisora Uno"),
                                 ("REV_C_SIGLA", "EMPRESA", "(verificar) paneles"),
                                 ("GRP_B_MARCA_GENERICOS", "NO_VINCULAR", "Revisora Uno"),
                                 ("MATRIZ_REPORTADA", "RELACION_PROPIEDAD", "(verificar) paneles"),
                                 ("EMP_A_FORMA_LEGAL", "EMPRESA", "(verificar) paneles")):  # fmt: skip
        caso = L.casos[tag]
        id_b = caso["ids_orbis"][0]
        vinculo("ORBIS", caso["clave_ancla"], id_b, orbis_de[id_b], decision, autor, f"regla {tag}")
    filas.append(["DUPLICADO", "EXPORTADORES", L.casos["DUP_VETADO"]["clave_ancla"], "Sementa Pastas",
                  "España|Sementa Pastas SA", "Sementa Pastas SA", "DISTINTA", "Revisora Uno", "2026-09-29", "x"])  # fmt: skip
    filas.append(["DUPLICADO", "EXPORTADORES", L.casos["DUP_FORZADO"]["clave_ancla"], "Cravinto Salsas",
                  "España|Cravinto Foods", "Cravinto Foods", "MISMA_ENTIDAD", "(verificar) paneles", "2026-09-29", "x"])  # fmt: skip
    # Bloque aleatorio: una cuarta parte de los pares con decisión de la «verdad» (con algún error).
    for v in verdad:
        if rng.random() > 0.25:
            continue
        correcta = {"orbis": "EMPRESA", "fdi": "GRUPO", "orbis_otro_pais": "GRUPO"}[v["tipo"]]
        decision = correcta if rng.random() < 0.85 else "NO_VINCULAR"
        autor = rng.choice(["Revisora Uno", "Revisor Dos", "(verificar) paneles"])
        if v["fuente"] == "ORBIS":
            vinculo(
                "ORBIS", v["clave_ancla"], v["id"], v["nombre"], decision, autor, "bloque aleatorio"
            )
        elif v["nombre"] in fila_fdi:
            vinculo("FDI", v["clave_ancla"], f"FDI-F{fila_fdi[v['nombre']]:04d}", v["nombre"], decision, autor,
                    "bloque aleatorio")  # fmt: skip
    primero = pd.DataFrame(filas, columns=cols)
    # Segundo archivo: contradice la primera decisión (gana la última) y trae claves inexistentes.
    caso = L.casos["EMP_A_IDENTICO"]
    segundo = [["VINCULO", "ORBIS", caso["clave_ancla"], "x", caso["ids_orbis"][0], orbis_de[caso["ids_orbis"][0]],
                "GRUPO", "Revisor Dos", "2026-10-02", "contradice al archivo anterior"],
               ["VINCULO", "NINGUNA", caso["clave_ancla"], "x", "ZZ", "x", "EMPRESA", "Revisor Dos", "2026-10-02", "fuente inexistente"],
               ["VINCULO", "ORBIS", "España|NO EXISTE SA", "x", caso["ids_orbis"][0], "x", "EMPRESA", "Revisor Dos", "2026-10-02", "ancla inexistente"],
               ["VINCULO", "ORBIS", caso["clave_ancla"], "x", "ESB0000000", "x", "EMPRESA", "Revisor Dos", "2026-10-02", "destino inexistente"]]  # fmt: skip
    return primero, pd.DataFrame(segundo, columns=cols)


def construir_insumos(
    carpeta: Path, *, n_aleatorias: int = 120, semilla: int = 42
) -> InsumosVinculacion:
    """Escribe ancla, descargas (hojas Orbis y FDI) y dos archivos de decisiones en ``carpeta``."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    rng = random.Random(semilla)
    L = _Libro()
    _casos_manuales(L)
    verdad = _bloque_aleatorio(L, rng, n_aleatorias)
    ancla = pd.DataFrame(L.ancla, columns=COL_ANCLA)
    orbis = pd.DataFrame(L.orbis, columns=COL_ORBIS)
    fdi = pd.DataFrame(L.fdi, columns=COL_FDI)
    dec1, dec2 = _decisiones(L, verdad, rng)
    ruta_ancla, ruta_desc = carpeta / "ancla.xlsx", carpeta / "descargas.xlsx"
    ancla.to_excel(ruta_ancla, index=False)
    with pd.ExcelWriter(ruta_desc) as w:
        orbis.to_excel(w, sheet_name="Orbis", index=False)
        fdi.to_excel(w, sheet_name="FDI", index=False)
    ruta_d1, ruta_d2 = carpeta / "decisiones_1.csv", carpeta / "decisiones_2.csv"
    dec1.to_csv(ruta_d1, index=False, encoding="utf-8-sig")
    dec2.to_csv(ruta_d2, index=False, encoding="utf-8-sig")
    return InsumosVinculacion(
        carpeta, ruta_ancla, ruta_desc, ruta_d1, ruta_d2, L.casos, ancla, orbis, fdi
    )


def configurar(modulo, ins: InsumosVinculacion, *, escenario: str = "base", **cambios):
    """``ConfigVinculacion`` de los insumos inventados, armada con ``modulo.Fuente``/``ConfigVinculacion``.

    ``escenario`` elige una variante de ``ESCENARIOS``; ``cambios`` pisa cualquier parámetro encima de ella.
    """
    cambios = {**ESCENARIOS[escenario], **cambios}
    con_decisiones = cambios.pop("con_decisiones", True)
    ancla = modulo.Fuente(
        clave="EXPORTADORES", etiqueta="EXPORTADORES", archivo=str(ins.archivo_ancla), hoja=0, prefijo_id="EXP",
        col_nombre="Empresa",
        col_pais="País de Empresa",
        col_id_comparable="NIF",
        col_telefono="Teléfono",
        col_email="Correo",
        col_web="Sitio web",
        col_ciudad="Ciudad",
        cols_metricas=("Operaciones", "Valor U$S", "Kilos", "Fletes"), cols_contexto=("HS6", "Descripción", "Régimen"),
        nombres_contexto={"Descripción": "DESCRIPCION_PRODUCTO", "Valor U$S": "VALOR_USD"},
        transformaciones=(("HS6", "hs6"),), metricas_repetidas=("Kilos",), claves_repeticion=("HS6",),
        largo_truncamiento=35, detectar_intermediarios=True,
    )  # fmt: skip
    orbis = modulo.Fuente(
        clave="ORBIS", etiqueta="ORBIS", archivo=str(ins.archivo_descargas), hoja="Orbis", prefijo_id="ORB",
        col_nombre="Company name Latin alphabet", col_pais="Country", col_id="BvD ID number",
        col_grupo="GUO - Name", col_web="Website address", col_email="E-mail address", col_telefono="Telephone number",
        col_ciudad="City Latin Alphabet", col_direccion="Address line 1 Latin Alphabet", col_id_comparable="VAT",
        col_tamano="Operating revenue (Turnover) m USD Last avail. yr",
        cols_contexto=("Operating revenue (Turnover) m USD Last avail. yr", "Number of employees Last avail. yr"),
        nombres_contexto={"Operating revenue (Turnover) m USD Last avail. yr": "INGRESOS_USD_M",
                          "Number of employees Last avail. yr": "EMPLEADOS"},
    )  # fmt: skip
    fdi = modulo.Fuente(
        clave="FDI", etiqueta="FDI MARKETS", archivo=str(ins.archivo_descargas), hoja="FDI", prefijo_id="FDI",
        col_nombre="Company name", col_pais="País de consulta", cols_pais_extra=("HQ country",),
        col_grupo="Parent company", col_web="Website", col_email="Company email", col_telefono="Company telephone #",
        col_ciudad="HQ city (Parent)", col_direccion="HQ address", col_tamano="No. of projects (in current search)",
        cols_contexto=("Primary sector", "No. of projects (in current search)"),
        nombres_contexto={"Primary sector": "SECTOR", "No. of projects (in current search)": "PROYECTOS_BUSQUEDA"},
    )  # fmt: skip
    decisiones = (
        (str(ins.archivo_decisiones), str(ins.archivo_decisiones_2)) if con_decisiones else ()
    )
    parametros = {
        "ancla": ancla, "destinos": [orbis, fdi], "alias_paises": ALIAS_PAISES,
        "formas_legales_extra": FORMAS_LEGALES_EXTRA, "genericos_extra": GENERICOS_EXTRA,
        "patrones_intermediario": PATRONES_INTERMEDIARIO, "dominios_genericos": DOMINIOS_GENERICOS,
        "archivos_decisiones": decisiones, "archivo_referencia": decisiones[:1],
        "carpeta_salida": str(ins.carpeta / "salida"), "prefijo_salida": "VINCULACION_PRUEBA",
        "versionar_salida": False, "titulo": "Vinculación de prueba (empresas inventadas)",
    }  # fmt: skip
    parametros.update(cambios)
    return modulo.ConfigVinculacion(**parametros)


# ── Huella canónica de una tabla (no depende de la versión de pandas) ──────────────
#: Filas de PARAMETROS que cambian en cada corrida (fecha, tiempos, versiones, archivos regenerados).
_PARAMETROS_VOLATILES = (
    "fecha_ejecucion_utc",
    "segundos",
    "motor",
    "python",
    "pandas",
    "numpy",
    "rapidfuzz",
)


def _celda(valor: object) -> str:
    if valor is None or valor is pd.NA or valor is pd.NaT:
        return ""
    if isinstance(valor, (bool, np.bool_)):
        return "True" if valor else "False"
    if isinstance(valor, (int, np.integer)):
        return str(int(valor))
    if isinstance(valor, (float, np.floating)):
        return "" if valor != valor else repr(float(valor))
    return str(valor)


def huella_tabla(tabla: pd.DataFrame, nombre: str = "") -> str:
    """SHA-256 (16 hex) de columnas, orden de filas y valores; ``astype(str)`` varía entre pandas 2 y 3, esto no."""
    if nombre == "PARAMETROS":
        mascara = ~tabla["PARAMETRO"].astype(str).str.startswith("sha256 ·") & ~tabla[
            "PARAMETRO"
        ].isin(_PARAMETROS_VOLATILES)
        tabla = tabla[mascara]
    h = hashlib.sha256()
    h.update("\x1f".join(map(str, tabla.columns)).encode())
    for fila in tabla.itertuples(index=False):
        h.update(("\x1f".join(_celda(v) for v in fila) + "\x1e").encode())
    return h.hexdigest()[:16]
