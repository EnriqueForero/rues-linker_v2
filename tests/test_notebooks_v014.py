"""Contratos de los notebooks oficiales (v0.16.0).

Un notebook que nunca se ejecutó no es un entregable. Aquí se verifica lo que
puede verificarse sin abrir Colab: JSON válido, que todo el código **compile**,
que los nombres importados existan de verdad en la API pública, que la celda
de entorno no repita el defecto de la instalación editable, y que la plantilla
mantenga una sola celda de parámetros.
"""

from __future__ import annotations

import ast
import json
from dataclasses import fields
from pathlib import Path

import nbformat
import pytest

NOTEBOOKS = Path(__file__).resolve().parents[1] / "notebooks"
GENERAL = "05_general_cruce_configurable.ipynb"
PLANTILLA = "06_ejemplo_rues_x_exportaciones.ipynb"
ORQUESTADOR = "06_orquestador_configurable.ipynb"
IMPORTADORES = "07_deduplicar_importadores_razon_social_pais.ipynb"
PUBLICACION = "08_PUBLICAR_GITHUB.ipynb"
NUEVOS = (GENERAL, PLANTILLA, IMPORTADORES, PUBLICACION)
#: Notebooks que INSTALAN y consumen la librería. El 08 publica el paquete: su
#: primera celda instala herramientas de build, no rues-linker, así que los
#: contratos de la celda de entorno no le aplican.
CONSUMIDORES = (GENERAL, PLANTILLA, IMPORTADORES)
TODOS = (
    "01_deduplicar_una_base.ipynb",
    "02_cruzar_dos_bases.ipynb",
    "03_produccion_multifuente.ipynb",
    "04_multicampo_y_evaluacion.ipynb",
    GENERAL,
    PLANTILLA,
    ORQUESTADOR,
    IMPORTADORES,
    PUBLICACION,
)


def _version_del_paquete() -> str:
    """La versión vigente sale del pyproject, que es la fuente de verdad."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    datos = tomllib.loads((NOTEBOOKS.parent / "pyproject.toml").read_text(encoding="utf-8"))
    return datos["project"]["version"]


def _versiones_anteriores(actual: str, cuantas: int = 6) -> tuple[str, ...]:
    """Las `cuantas` versiones de parche anteriores a la vigente.

    Se limita al mismo minor: buscar `0.21.x` en el texto daría falsos
    positivos en la prosa que explica de dónde viene cada cosa.
    """
    mayor, menor, parche = (int(x) for x in actual.split("."))
    return tuple(f"{mayor}.{menor}.{p}" for p in range(max(0, parche - cuantas), parche))


_VERSIONES_ANTERIORES = _versiones_anteriores(_version_del_paquete())


def _cargar(nombre: str) -> dict:
    return json.loads((NOTEBOOKS / nombre).read_text(encoding="utf-8"))


def _sin_magics(fuente: str) -> str:
    """Quita las líneas de magics IPython para poder compilar con `ast`."""
    return "".join(
        linea for linea in fuente.splitlines(True) if not linea.lstrip().startswith(("%", "!"))
    )


def _codigo(nb: dict) -> list[str]:
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


# ── Contratos comunes a todos los notebooks nuevos ────────────────────


@pytest.mark.parametrize("nombre", TODOS)
def test_schema_nbformat_formal(nombre: str) -> None:
    nbformat.validate(_cargar(nombre))


@pytest.mark.parametrize("nombre", NUEVOS)
def test_json_valido(nombre: str) -> None:
    assert _cargar(nombre)["nbformat"] == 4


def test_los_notebooks_del_repositorio_no_llevan_salidas() -> None:
    """Al repositorio no van notebooks con salidas: pesan, ensucian el diff y
    pueden filtrar datos del usuario.

    **Dónde se exige esto, y por qué no es donde parecía.** La versión anterior
    lo exigía sobre el árbol de trabajo, y eso no puede cumplirse en Colab por
    dos razones independientes:

    * El **08** es el notebook que corre esta compuerta. Colab autoguarda sus
      salidas en el .ipynb de Drive mientras se ejecuta, así que cuando pytest
      lo lee ya las tiene — las de la celda que está corriendo. Pedirle que no
      las tenga es pedirle que no se esté ejecutando.
    * El **07** es un notebook de análisis: usted lo corre contra sus datos y
      sus salidas SON el resultado. Exigir que estén vacías obliga a borrarlas
      a mano antes de cada publicación, que es la clase de fricción por la que
      la gente termina apagando las compuertas.

    Las dos fallaron en la publicación real. La propiedad es del **repositorio**,
    no del árbol de trabajo, así que se comprueba donde el repositorio existe —
    un checkout de git— y se garantiza en el camino hacia git: `preparar_build()`
    limpia las salidas al copiar. Fuera de un checkout no se calla: se verifica
    que esa garantía siga en pie.
    """
    raiz = NOTEBOOKS.parent
    if (raiz / ".git").exists():
        sucios = []
        for nombre in TODOS:
            nb = _cargar(nombre)
            for celda in nb["cells"]:
                if celda["cell_type"] == "code" and (
                    celda.get("outputs") or celda.get("execution_count") is not None
                ):
                    sucios.append(nombre)
                    break
        assert not sucios, (
            f"notebooks con salidas embebidas en el checkout: {sorted(set(sucios))}. "
            "El build las limpia al publicar; si llegaron a git, alguien hizo "
            "`git add` saltándose el notebook 08."
        )
    else:
        # Copia de trabajo (Drive, un .zip descomprimido): aquí tener salidas es
        # normal y deseable. Lo que no puede faltar es la garantía que impide
        # que lleguen a git.
        codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
        assert "_limpiar_salidas_notebooks(dst)" in codigo, (
            "sin checkout de git, la única garantía es que el build limpie: "
            "y `preparar_build()` ya no la llama"
        )


@pytest.mark.parametrize("nombre", NUEVOS)
def test_todo_el_codigo_compila(nombre: str) -> None:
    for i, fuente in enumerate(_codigo(_cargar(nombre))):
        try:
            ast.parse(_sin_magics(fuente))
        except SyntaxError as exc:  # pragma: no cover - el mensaje importa
            pytest.fail(f"{nombre} celda {i}: {exc}")


@pytest.mark.parametrize("nombre", NUEVOS)
def test_los_nombres_importados_existen(nombre: str) -> None:
    """Todo lo que el notebook importa de la librería debe existir hoy."""
    import importlib

    for fuente in _codigo(_cargar(nombre)):
        for nodo in ast.walk(ast.parse(_sin_magics(fuente))):
            if isinstance(nodo, ast.ImportFrom) and (nodo.module or "").startswith(
                "record_linkage"
            ):
                modulo = importlib.import_module(nodo.module)
                for alias in nodo.names:
                    assert hasattr(modulo, alias.name), (
                        f"{nombre} importa {nodo.module}.{alias.name}, que no existe"
                    )


@pytest.mark.parametrize("nombre", TODOS)
def test_la_version_anunciada_coincide_con_el_paquete(nombre: str) -> None:
    import record_linkage

    texto = json.dumps(_cargar(nombre), ensure_ascii=False)
    assert record_linkage.__version__ in texto


def test_la_plantilla_descarta_modulos_viejos_y_exige_version() -> None:
    entorno = _codigo(_cargar(PLANTILLA))[0]
    import record_linkage

    assert "VERSION_ESPERADA" in entorno and f'"{record_linkage.__version__}"' in entorno
    assert "list(sys.modules)" in entorno and 'm.startswith("record_linkage.")' in entorno
    assert "record_linkage.__version__ != VERSION_ESPERADA" in entorno


@pytest.mark.parametrize("nombre", CONSUMIDORES)
def test_la_celda_de_entorno_nunca_instala_editable(nombre: str) -> None:
    """`pip install -e` deja un .pth que un kernel YA en marcha no lee.

    Reproducido en Colab: pip devuelve 0 y aun así `import record_linkage`
    falla, porque el paquete usa layout ``src/`` y ``__editable__*.pth`` solo
    lo procesa ``site.py`` al arrancar el intérprete. Esto no admite matices y
    vale para cualquier estrategia de carga.
    """
    entorno = _codigo(_cargar(nombre))[0]
    assert '"-e"' not in entorno and "'-e'" not in entorno
    assert "--editable" not in entorno


@pytest.mark.parametrize("nombre", CONSUMIDORES)
def test_la_celda_de_entorno_garantiza_codigo_fresco_y_completo(nombre: str) -> None:
    """El invariante real, que admite dos estrategias.

    Lo que no puede pasar es correr código **rancio** (una copia vieja del
    paquete que quedó en el kernel o en site-packages) o **incompleto** (un
    árbol que Drive dejó a medias). Hay dos formas legítimas de garantizarlo y
    el repositorio usa las dos:

    * **Instalar la rueda** (05, 06): un solo archivo, o está completo o falla
      ruidosamente. Exige `capture_output=True` — un `pip -q` silencioso
      esconde la causa.
    * **Importar desde el árbol de Drive** (07): no hay instalación que pueda
      quedar rancia, pero hay que verificar de dónde se importó de verdad,
      porque un `record_linkage` previo en el kernel ganaría la partida.

    Fijar «tiene que haber un pip install» confundía una implementación con el
    invariante, y rechazaba una celda de entorno correcta. Lo común a las dos
    es lo que se exige aquí.
    """
    entorno = _codigo(_cargar(nombre))[0]

    instala = "pip" in entorno and "install" in entorno and "rueda" in entorno.lower()
    importa_del_arbol = "sys.path.insert" in entorno

    assert instala or importa_del_arbol, (
        "la celda de entorno no instala la rueda ni importa desde el árbol: "
        "no se sabe qué código va a correr"
    )
    # Un kernel en marcha conserva los módulos ya importados: sin esto se
    # seguiría ejecutando el código viejo con la versión nueva en disco.
    assert "invalidate_caches" in entorno
    # Importar el __init__ no prueba que estén los submódulos (FUSE). Hay dos
    # formas de verificarlo: una lista explícita (`_SUBPAQUETES`, 05/06) o
    # recorrer el paquete entero con `pkgutil.walk_packages` (07). Exigir el
    # nombre `_SUBPAQUETES` rechazaba la segunda, que es la más exhaustiva —
    # otra vez un contrato escrito sobre la implementación y no sobre la
    # propiedad. Lo común es que se importen submódulos, uno por uno.
    assert "import_module(" in entorno and (
        "_SUBPAQUETES" in entorno or "walk_packages" in entorno
    ), "falta la verificación de que el paquete está completo (submódulo a submódulo)"
    # Cuando nada de lo anterior alcanza, el usuario necesita saber qué hacer.
    assert (
        "Reiniciar sesion" in entorno or "Reinicie el" in entorno or ("Reiniciar sesión" in entorno)
    ), "falta la salida de emergencia"

    if instala:
        assert "capture_output=True" in entorno, "un pip -q silencioso esconde la causa del fallo"
    if importa_del_arbol:
        assert "record_linkage.__file__" in entorno or "rl.__file__" in entorno, (
            "importar desde el árbol obliga a comprobar de dónde se importó de verdad"
        )


def test_la_instalacion_prefiere_la_rueda_del_zip() -> None:
    """Instalar desde el árbol de 250+ archivos sobre FUSE es el modo de falla.

    Reproducido con el usuario: Drive sincroniza de forma asíncrona, la copia
    del árbol queda incompleta sin lanzar error y pip construye un paquete al
    que le falta `config/`. El síntoma —`No module named
    'record_linkage.config'` DESPUÉS de que pip dijo que instaló— no apunta a
    la causa. Una rueda es un solo archivo: o está completa o falla ruidosa.
    """
    entorno = _codigo(_cargar(PLANTILLA))[0]
    assert 'glob("dist/*.whl")' in entorno, (
        "el zip deja la rueda en dist/: buscar sólo en la raíz la ignora"
    )
    assert "is_zipfile" in entorno, "una rueda a medio sincronizar debe detectarse"


def test_la_instalacion_verifica_que_el_paquete_quedo_completo() -> None:
    """Importar el __init__ no prueba que estén los submódulos."""
    entorno = _codigo(_cargar(PLANTILLA))[0]
    assert "_SUBPAQUETES" in entorno
    for critico in ("config.paths", "flujo.cruce", "processing.text"):
        assert critico in entorno, f"la verificación no cubre {critico}"
    assert "--force-reinstall" in entorno and "--no-deps" in entorno, (
        "una instalación previa rota debe reemplazarse, no complementarse"
    )


@pytest.mark.parametrize("nombre", NUEVOS)
def test_el_trabajo_nunca_se_configura_sobre_drive(nombre: str) -> None:
    codigo = "\n".join(_codigo(_cargar(nombre)))
    if "RUTA_TRABAJO" in codigo:
        linea = [ln for ln in codigo.splitlines() if ln.strip().startswith("RUTA_TRABAJO")]
        assert linea and "/content/drive" not in linea[0]


# ── Contratos de la plantilla universal (06) ──────────────────────────


def test_la_plantilla_tiene_una_sola_celda_de_parametros() -> None:
    """El usuario edita UNA celda: si hay dos, el notebook ya se degradó."""
    celdas = _codigo(_cargar(PLANTILLA))
    con_parametros = [c for c in celdas if "§3 · PARAMETROS" in c or "§3 · PARÁMETROS" in c]
    assert len(con_parametros) == 1, f"se esperaba 1 celda de parámetros, hay {len(con_parametros)}"
    assert "FIN DE LOS PARAMETROS" in con_parametros[0]


def test_la_plantilla_expone_las_palancas_del_motor() -> None:
    """La flexibilidad prometida tiene que estar declarada, no escondida."""
    parametros = next(c for c in _codigo(_cargar(PLANTILLA)) if "§3 · PARAMETROS" in c)
    for palanca in (
        "lsh_permutations",
        "lsh_threshold",
        "score_threshold",
        "min_name_similarity",
        "max_nit_distance",
        "cleaning_mode",
        "VARIABLES_EXTRA",
        "PERFIL_MULTICAMPO",
        "MODO",
    ):
        assert palanca in parametros, f"la celda de parámetros no expone {palanca}"


def test_la_plantilla_soporta_los_dos_modos() -> None:
    codigo = "\n".join(_codigo(_cargar(PLANTILLA)))
    assert '"dedupe"' in codigo and '"linkage"' in codigo


def test_la_plantilla_cronometra_las_fases() -> None:
    celdas = _codigo(_cargar(PLANTILLA))
    assert sum(1 for c in celdas if c.lstrip().startswith("%%time")) >= 4
    assert any("resultado.tiempos()" in c for c in celdas)


def test_la_plantilla_conserva_las_salvaguardas_de_precision() -> None:
    """Las dos defensas contra fusionar NIT válidos distintos siguen en True."""
    parametros = next(c for c in _codigo(_cargar(PLANTILLA)) if "§3 · PARAMETROS" in c)
    assert '"veto_nit_base_distinto":   True' in parametros.replace("  ", "  ") or (
        "veto_nit_base_distinto" in parametros and "True" in parametros
    )
    assert "cannot_link_identificador" in parametros


def test_la_plantilla_apunta_a_las_rutas_reales() -> None:
    codigo = "\n".join(_codigo(_cargar(PLANTILLA)))
    # Contrato de producción: las rutas reales de Drive del proyecto, no las
    # de un ensayo local. La librería se toma ya descomprimida de Drive.
    assert "/content/drive/MyDrive/ProColombia/rues_linker_pruebas" in codigo
    assert "2026_06_30_Historico_Limpio.zip" in codigo
    assert "Base_Exportaciones_Colombianas_2021-2026 (Junio).zip" in codigo
    assert "_procesados" in codigo, "debe reutilizar los insumos ya procesados"
    assert "/content/_rues_linker_trabajo" in codigo, "temporales en disco local, no Drive"
    assert "COLAPSAR_DUPLICADOS = True" in codigo, (
        "la base de exportaciones es transaccional: sin colapso la corrida es ~12x más cara"
    )
    assert all(valor in codigo for valor in ('"-1"', '"00"', "NO DEFINIDO")), (
        "faltan los centinelas reales"
    )


def test_modo_dedupe_no_exige_archivo_de_exportaciones() -> None:
    parametros = next(c for c in _codigo(_cargar(PLANTILLA)) if "§3 · PARAMETROS" in c)
    assert 'MODO = "linkage"' in parametros, "el modo debe ser explícito"
    assert 'if MODO == "dedupe"' in parametros, (
        "la plantilla debe recortar las fuentes declaradas cuando se deduplica "
        "una sola base: exigir el archivo de exportaciones ahí es un error"
    )


def test_la_plantilla_no_carga_las_fuentes_completas_en_el_notebook() -> None:
    """El perfilado y el preflight leen acotado; el universo lo mueve la librería.

    La plantilla usa el flujo pandas con caché Parquet (validado por el
    usuario a 5,26 M de filas en Colab Free). El camino DuckDB disk-first vive
    en `06_orquestador_configurable.ipynb`, que tiene su propio gate.
    """
    codigo = "\n".join(_codigo(_cargar(PLANTILLA)))
    assert "limite_filas=" in codigo, "MUESTRA debe limitar la ingesta, no recortar después"
    assert "dir_procesados=" in codigo, "la caché de insumos es parte del contrato"
    assert "muestra=" in codigo or "nrows" in codigo or "head(" in codigo, (
        "el perfilado debe leer acotado, no la fuente completa"
    )
    assert 'warnings.filterwarnings("ignore")' not in codigo, (
        "silenciar warnings esconde justo lo que hay que ver"
    )


def test_la_plantilla_proyecta_solo_campos_que_puntuan() -> None:
    parametros = next(c for c in _codigo(_cargar(PLANTILLA)) if "§3 · PARAMETROS" in c)
    assert "COLUMNAS_RUES = {" in parametros
    assert "COLUMNAS_EXPORTACIONES = {" in parametros
    assert "VARIABLES_EXTRA" in parametros
    # Las columnas mapeadas que NO están en VARIABLES_EXTRA no deben entrar al
    # motor: la librería las derrama y las re-adjunta al final. Sin eso, el
    # colapso exacto se degrada (19.407 → 32.745 representantes medidos).
    from record_linkage.flujo import ConfigCruce

    assert "separar_columnas_extra" in {f.name for f in fields(ConfigCruce)}
    assert ConfigCruce.separar_columnas_extra is True


def test_la_plantilla_tiene_secciones_numeradas_con_iconos() -> None:
    nb = _cargar(PLANTILLA)
    titulos = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "markdown"]
    texto = "\n".join(titulos)
    for seccion in ("§1", "§2", "§3", "§4", "§5", "§6", "§7", "§8", "§9"):
        assert seccion in texto, f"falta la sección {seccion}"
    assert "🎛️" in texto and "🚀" in texto and "⏱️" in texto


# ── Contratos del notebook sin identificador (v0.22.1) ────────────────


def test_importadores_no_reimplementa_el_motor() -> None:
    """La lógica vive en la librería, no en las celdas (README de notebooks).

    Si alguien vuelve a pegar el comparador o la cobertura en una celda, este
    contrato se cae: es la regla que mantiene el notebook auditable.
    """
    codigo = "\n".join(_codigo(_cargar(IMPORTADORES)))
    for prohibido in ("def cobertura_estrella", "class SimilitudNombre", "def canonizar_pais"):
        assert prohibido not in codigo, f"{prohibido} debe importarse, no definirse aquí"
    assert "from record_linkage.flujo import" in codigo
    assert "deduplicar_importadores" in codigo


def test_importadores_declara_las_invariantes_como_compuerta() -> None:
    """Un notebook que no comprueba `todo_ok` puede entregar basura en silencio."""
    codigo = "\n".join(_codigo(_cargar(IMPORTADORES)))
    assert "todo_ok" in codigo
    assert "raise AssertionError" in codigo


def test_publicacion_protege_el_centinela_de_version() -> None:
    """La plantilla genérica reescribe __version__; aquí eso rompe la invariante.

    `__version__` se lee de importlib.metadata y el literal del archivo es un
    centinela. Si este contrato se cae, una publicación dejaría dos fuentes de
    verdad — que es exactamente lo que pasó con el fallback rancio "3.2.3".
    """
    codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
    assert "SINCRONIZAR_VERSION_EN_INIT: bool = False" in codigo
    assert "SINCRONIZAR_VERSION_EN_INIT" in codigo.split("def verificar_estructura")[-1]


def test_publicacion_corre_banco_y_conformidad() -> None:
    """Publicar sin los dos instrumentos es lo que dejó pasar dos regresiones."""
    codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
    assert "scripts/banco.py" in codigo
    assert "--comparar" in codigo
    assert "scripts/conformidad.py" in codigo
    assert "scripts/verificar_coherencia_version.py" in codigo


def test_publicacion_no_sobrescribe_la_licencia() -> None:
    """rues-linker es Apache-2.0; la plantilla genérica escribe MIT."""
    codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
    assert "MIT License" not in codigo
    assert "LICENCIA = 'Apache-2.0'" in codigo


def test_publicacion_incluye_todos_los_notebooks() -> None:
    """*.ipynb está en la exclusión base: si uno falta aquí, no se publica."""
    codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
    for nombre in TODOS:
        assert f"notebooks/{nombre}" in codigo, f"{nombre} no está en INCLUIR_SIEMPRE"


# ── Regresiones de publicación observadas en Colab (v0.22.1) ──────────


def test_publicacion_instala_el_paquete_antes_de_las_compuertas() -> None:
    """Sin instalar, pytest falla con ModuleNotFoundError y engaña.

    Observado en Colab: el banco y la conformidad PASAN en la misma corrida
    —porque `banco.py` y `conformidad.py` hacen `sys.path.insert(0, src)`— y
    solo pytest falla. El síntoma apunta a los tests; la causa es que nada
    instalaba el paquete, y el proyecto usa layout ``src/``.
    """
    codigo = _codigo(_cargar(PUBLICACION))
    entero = "\n".join(codigo)
    assert "CELDA A.0" in entero, "falta la celda de instalación"
    instalar = next((c for c in codigo if "CELDA A.0" in c), "")
    # La rueda, no el árbol: copiar cientos de archivos desde FUSE queda
    # incompleto sin lanzar error.
    assert "is_zipfile" in instalar, "una rueda a medio sincronizar debe detectarse"
    assert 'glob("dist/*.whl")' in instalar
    assert '"-e"' not in instalar and "'-e'" not in instalar, "editable no sirve en un kernel vivo"
    assert "--force-reinstall" in instalar and "--no-deps" in instalar
    assert "_SUBPAQUETES" in instalar, "importar el __init__ no prueba que estén los submódulos"
    for critico in ("flujo.importadores", "matching.nombre_idf", "paises.catalogo"):
        assert critico in instalar, f"la verificación no cubre {critico}"
    # Y debe correr ANTES que las compuertas.
    assert entero.index("CELDA A.0") < entero.index("CELDA A.1")


def test_publicacion_no_regenera_el_pyproject() -> None:
    """El pyproject generado publica un repositorio cuyo CI no puede pasar.

    Declara los marcadores paridad/canario/smoke y NO declara 'slow': con
    ``--strict-markers`` los tests marcados @pytest.mark.slow ERROR-an al
    colectar. Además pierde [tool.ruff] y [tool.mypy] completas.
    """
    codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
    assert "REGENERAR_PYPROJECT: bool = False" in codigo
    assert "globals().get('REGENERAR_PYPROJECT', True)" in codigo, (
        "el motor debe consultar la perilla, no regenerar siempre"
    )
    assert "CONSERVADO" in codigo, "debe dejar constancia de que lo conservó"


def test_el_pyproject_declara_los_marcadores_que_los_tests_usan() -> None:
    """La razón concreta por la que no se puede regenerar el pyproject.

    Se parsea con ``ast``, no rascando texto: un fichero que MENCIONA
    "@pytest.mark.slow" en una cadena no lo está usando, y la primera versión
    de esta prueba se detectó a sí misma.
    """
    import tomllib

    raiz = Path(__file__).resolve().parents[1]
    with open(raiz / "pyproject.toml", "rb") as fh:
        declarados = tomllib.load(fh)["tool"]["pytest"]["ini_options"]["markers"]
    nombres = {m.split(":")[0].strip() for m in declarados}
    integrados = {"parametrize", "skipif", "skip", "xfail", "usefixtures", "filterwarnings"}

    def _marca(nodo: ast.expr) -> str | None:
        if isinstance(nodo, ast.Call):
            nodo = nodo.func
        partes: list[str] = []
        while isinstance(nodo, ast.Attribute):
            partes.append(nodo.attr)
            nodo = nodo.value
        if isinstance(nodo, ast.Name) and nodo.id == "pytest" and len(partes) >= 2:
            return partes[-2]  # pytest.mark.<marca>
        return None

    usados: set[str] = set()
    for archivo in sorted((raiz / "tests").glob("test_*.py")):
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for dec in nodo.decorator_list:
                    marca = _marca(dec)
                    if marca and marca not in integrados:
                        usados.add(marca)
    faltan = usados - nombres
    assert not faltan, (
        f"marcadores usados y no declarados: {sorted(faltan)}. Con "
        f"--strict-markers esos tests no colectan."
    )
    assert "slow" in usados and "slow" in nombres, (
        "'slow' debe seguir usándose y declarado: es el caso que el pyproject "
        "generado por la plantilla rompía"
    )


def test_publicacion_apunta_al_repositorio_y_ruta_correctos() -> None:
    codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
    assert "NOMBRE_REPO_GITHUB = 'rues-linker_v2'" in codigo
    assert "/content/drive/MyDrive/ProColombia/rues_linker_pruebas" in codigo
    # La DISTRIBUCIÓN no cambia de nombre: romperla rompe el centinela.
    assert "NOMBRE_DISTRIBUCION = 'rues-linker'" in codigo


def test_publicacion_separa_calidad_de_costo_en_el_banco() -> None:
    """El reloj de otra sesión no es evidencia de regresión.

    Medido: la MISMA versión 0.21.0 tardó 51,0 s por la mañana y 65,1 s por la
    tarde en el mismo contenedor. Un veredicto binario que mezcla calidad y
    costo produce falsas alarmas, y una falsa alarma repetida enseña a ignorar
    el instrumento — que es como se colaron dos regresiones a producción.
    """
    codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
    assert "ANTIGUEDAD_MAXIMA_BASE_HORAS" in codigo
    assert '_COSTO = {"segundos_total", "rss_pico_mib"}' in codigo
    # La calidad y la huella SIEMPRE bloquean.
    assert "_fallan_calidad" in codigo and "FALLOS.append" in codigo
    # No se toca el umbral del banco: el problema era la línea base.
    assert "aumento_maximo_tiempo" not in codigo, (
        "el umbral del banco no se ajusta desde el notebook; se corrige la base"
    )


def test_publicacion_convierte_un_salto_de_conformidad_en_fallo() -> None:
    """pytest devuelve 0 con saltos: la compuerta decía OK sin haber medido.

    El fixture de conformidad hace `pytest.skip` cuando no encuentra el
    conjunto. Con el paquete instalado —lo que hace la Celda A.0— la ruta se
    resolvía contra site-packages y 8 casos se saltaban en silencio. Una
    prueba que falla avisa; una que se salta, no.
    """
    codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
    assert "tests/test_conformidad_v020.py" in codigo, (
        "el camino rápido de la compuerta tiene que incluir conformidad"
    )
    assert '"skipped" in _salida_conf' in codigo, (
        "la compuerta tiene que inspeccionar los saltos, no solo el código de salida"
    )
    assert "conformidad con pruebas saltadas" in codigo, (
        "un salto en conformidad debe entrar en FALLOS, no en AVISOS"
    )


def test_publicacion_no_deja_fuera_los_conjuntos_versionados() -> None:
    """El build perdía 16 archivos y decía «Build listo».

    `_EXCLUIR_EXT_BASE` bota `*.csv` porque la regla sana es «solo va a git el
    código». En este repositorio eso es falso: los conjuntos de conformidad,
    banco y ground truth son CSV, y los fixtures de la suite también. Sin
    rescatarlos se publicaba un repositorio con el CI roto desde el primer
    commit — y sin ningún error: copiaba 404 de 420 archivos.
    """
    codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
    assert "DIRECTORIOS_VERSIONADOS" in codigo
    for carpeta in (
        "data/conformidad",
        "data/benchmark",
        "data/ground_truth",
        "tests/data",
        "tests/data_sintetica",
    ):
        assert f"'{carpeta}'" in codigo, f"{carpeta} no se rescata del filtro de extensiones"
    # Se expande contra el árbol real: un fixture nuevo no depende de que
    # alguien se acuerde de listarlo.
    assert "_expandir_directorios" in codigo


def test_publicacion_reincluye_el_directorio_antes_que_el_archivo() -> None:
    """Refuerzo, no corrección de un defecto observado.

    Git no desciende a un directorio excluido, así que una excepción por
    archivo dentro de un directorio ignorado es letra muerta. **Se comprobó
    que hoy no ocurre**: el `.gitignore` *generado* no contiene `data/*` —esa
    línea es una adición a mano del archivo del repositorio— y saboteando el
    generador a propósito los 8 archivos de `data/conformidad/` se commitean
    igual. Esto protege el caso en que alguien añada `data` a
    `EXCLUIR_DIRS_EXTRA`.
    """
    codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
    assert 'f"!{d}/\\n!{d}/**\\n"' in codigo, (
        "el .gitignore generado tiene que re-incluir el directorio, no solo los archivos"
    )
    assert "directorios_versionados" in codigo


def test_publicacion_falla_si_el_build_queda_incompleto() -> None:
    """Un build incompleto anunciado como completo es peor que uno que revienta.

    El error aparecería en el CI, lejos de la causa. Y se comprueba con el
    propio git (`check-ignore`), no razonando sobre patrones de .gitignore.
    """
    codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
    assert "raise FileNotFoundError(" in codigo and "el build quedó INCOMPLETO" in codigo, (
        "los archivos que no llegan al build deben abortar, no imprimir un aviso"
    )
    assert "'git', 'check-ignore', '--stdin'" in codigo, (
        "hay que preguntarle a git qué ignora, no deducirlo"
    )


def test_publicacion_limpia_las_salidas_de_los_notebooks() -> None:
    """La garantía que sustituye a la compuerta imposible.

    El 08 no puede exigirse a sí mismo estar sin salidas mientras se ejecuta
    (ver `test_sin_salidas_guardadas`). Así que el build las limpia al copiar:
    es más fuerte que una prueba que se queja, porque no depende de que nadie
    se acuerde de limpiar antes de publicar.
    """
    codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
    assert "_limpiar_salidas_notebooks" in codigo
    assert '"outputs"] = []' in codigo or "'outputs'] = []" in codigo
    assert '"execution_count"] = None' in codigo or "'execution_count'] = None" in codigo


@pytest.mark.parametrize("nombre", TODOS)
def test_ninguna_version_operativa_del_notebook_se_queda_atras(nombre: str) -> None:
    """La versión no solo vive en `VERSION_OBJETIVO`.

    Al subir 0.22.1 → 0.22.2 quedaron atrás **22 líneas** que el contrato
    anterior no miraba: el tag de `ORIGEN_GIT` (`...@v0.22.1`, que instalaría
    la versión equivocada desde GitHub), el nombre exacto de la rueda que el
    orquestador busca en `/content`, y los encabezados que anuncian el motor.
    Un notebook que dice 0.22.2 en un sitio y pide v0.22.1 en otro instala lo
    que pide, no lo que dice.

    Se exceptúan las referencias **históricas** —cuándo se midió algo, en qué
    versión se introdujo— porque cambiarlas falsifica el registro. Se
    reconocen por su texto, no por omisión: si aparece una versión vieja en una
    línea que no es histórica, esto falla.
    """
    # Una línea puede nombrar una versión vieja legítimamente, y son tres
    # clases: decir CUÁNDO se midió algo, EN QUÉ VERSIÓN se introdujo, o
    # COMPARAR la actual con una anterior. Fuera de esas tres, nombrar una
    # versión vieja es deriva. La lista describe clases de enunciado, no
    # excepciones puntuales: ampliarla para callar un fallo concreto la
    # convierte en un sello de goma.
    historico = (
        "medido",
        "29-ago-2026",
        "garantiza desde",
        "Entre ambos",
        "hasta la 0.2",
        "Hasta la 0.2",
        "Hasta 0.2",
        "hasta 0.2",
        "identico a",
        "idéntico a",
    )
    actual = _version_del_paquete()
    rezagadas = []
    for i, celda in enumerate(_cargar(nombre)["cells"]):
        for linea in "".join(celda["source"]).splitlines():
            if any(h in linea for h in historico):
                continue
            for viejo in _VERSIONES_ANTERIORES:
                if viejo in linea:
                    rezagadas.append(f"celda {i}: {linea.strip()[:90]}")
    assert not rezagadas, (
        f"{nombre} anuncia {actual} pero conserva versiones viejas en líneas "
        f"operativas:\n" + "\n".join(f"  - {r}" for r in rezagadas)
    )


def test_importadores_comprueba_la_cobertura_de_paises_antes_del_smoke_test() -> None:
    """La primera corrida sobre `snowflake_v2` se detuvo DESPUÉS de emparejar.

    Trajo grafías de `PAIS_ESTANDAR` fuera del catálogo; la invariante las
    atrapó, pero minutos tarde y sin decir cuáles eran. Y medido: con la
    etiqueta única `SIN CLASIFICAR`, la misma razón social bajo tres grafías se
    fusionaba en un solo importador. El notebook tiene que (a) exponer el modo
    en la celda de configuración, (b) comprobar la cobertura sobre la base
    COMPLETA antes del smoke test — la muestra vería 3 filas donde hay 300 — y
    (c) delegar la decisión a la librería, no repetirla.
    """
    codigo = _codigo(_cargar(IMPORTADORES))
    todo = "\n".join(codigo)
    assert 'PAISES_SIN_CLASIFICAR = "detener"' in todo, "el defecto es detenerse y listar"
    assert "paises_sin_clasificar=PAISES_SIN_CLASIFICAR" in todo, (
        "la perilla tiene que llegar a CFG"
    )
    assert "cobertura_paises" in todo and "exigir_cobertura_paises" in todo, (
        "la decisión vive en la librería; el notebook solo la invoca"
    )
    # Orden: cobertura sobre BASE antes de smoke_test, en la misma celda.
    celda = next(c for c in codigo if "reportar_cobertura_paises(BASE, CFG)" in c)
    assert celda.index("reportar_cobertura_paises(BASE, CFG)") < celda.index(
        "smoke_test(BASE, CFG"
    ), "la cobertura de países va ANTES del smoke test"


def test_importadores_declara_lo_que_no_es_pais_sin_apagar_la_guardia() -> None:
    """`OTROS` (444 filas en snowflake_v2) no es un país; `aislar` global sí apaga la guardia.

    La forma correcta es declararlo: `NO_SON_PAISES` en la celda del catálogo,
    cableado a `CFG.paises_aislar`, con el modo en `detener`. Así lo conocido
    pasa y lo nuevo sigue deteniendo la celda 6.
    """
    codigo = "\n".join(_codigo(_cargar(IMPORTADORES)))
    assert 'NO_SON_PAISES: tuple[str, ...] = ("OTROS",)' in codigo
    assert "CFG.paises_aislar = NO_SON_PAISES" in codigo
    assert 'PAISES_SIN_CLASIFICAR = "detener"' in codigo, (
        "declarar OTROS no es motivo para 'aislar'"
    )
    assert "grafias_aisladas(cfg)" in codigo, "la celda 6 muestra qué quedó declarado"


def test_publicacion_instala_las_extras_dev_desde_el_pyproject() -> None:
    """La publicación real se detuvo al COLECTAR: `ImportError: hypothesis`.

    La celda de dependencias traía una lista escrita a mano y la lista no
    tenía `hypothesis`. El CI no lo sufría porque instala `.[dev]` desde el
    pyproject. Una lista a mano siempre se desincroniza: la Celda A.0 lee las
    extras `dev` del pyproject de Drive, las instala y verifica que estén.
    """
    codigo = _codigo(_cargar(PUBLICACION))
    a0 = next(c for c in codigo if "CELDA A.0" in c)
    assert '"optional-dependencies"' in a0 and '["dev"]' in a0, "las extras dev salen del pyproject"
    assert "PackageNotFoundError" in a0, "hay que VERIFICAR que quedaron, no solo pedirlas"
    # Y la línea de instalación de arranque ya no lista dependencias del paquete
    # a mano (la prosa puede nombrarlas; la orden de pip, no).
    pip_arranque = [ln for ln in codigo[0].splitlines() if ln.lstrip().startswith("%pip")]
    assert len(pip_arranque) == 1
    for dep in ("datasketch", "rapidfuzz", "hypothesis", "scikit-learn"):
        assert dep not in pip_arranque[0], f"{dep} no se lista a mano: viene del pyproject vía A.0"


def test_publicacion_corre_los_tests_como_el_ci() -> None:
    """Lo que se verifica localmente es lo que el CI verificará: mismos marcadores."""
    codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
    assert '-m "not canario and not slow"' in codigo, "ci.yml excluye canario y slow"
    ci = (NOTEBOOKS.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert '-m "not canario and not slow"' in ci, "si el CI cambia de marcadores, esto avisa"


def test_publicacion_muestra_la_causa_cuando_los_tests_fallan() -> None:
    """Hasta 0.22.3 se imprimían 600 caracteres y el ImportError quedaba fuera."""
    codigo = "\n".join(_codigo(_cargar(PUBLICACION)))
    assert "[-3000:]" in codigo and "[:600]" not in codigo, "el recorte de 600 escondía la causa"
    assert "Líneas de causa" in codigo


def test_importadores_no_mantiene_una_lista_propia_de_centinelas() -> None:
    """Dos listas de centinelas —la del motor y una copia en el notebook— derivan.

    Medido: `NO DISPONIBLE` no estaba en ninguna y era «el importador más grande»
    de 69 países con el 21,7 % del FOB. El notebook cuenta con la lista con la
    que el motor decide: `PLACEHOLDERS`.
    """
    codigo = "\n".join(_codigo(_cargar(IMPORTADORES)))
    assert "from record_linkage.matching.normalizadores import PLACEHOLDERS" in codigo
    assert "CENTINELAS_SIN_NOMBRE: frozenset[str] = PLACEHOLDERS" in codigo
    assert '"NO REGISTRA", "CONFIDENCIAL"' not in codigo, "la copia a mano tiene que desaparecer"
