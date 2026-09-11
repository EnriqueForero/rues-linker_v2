# Entrega 0.22.2 — las dos compuertas que medían el artefacto equivocado

> **Qué pasó.** Enrique corrió la publicación real. Pasó A, A.0, ruff,
> coherencia de versión, conformidad (46 casos, 0 saltos), gobernanza
> documental y el banco con huella idéntica. Se detuvo en **tres pruebas de
> contrato**.
>
> **De las tres, dos eran defectos míos y una no era un fallo.** Ninguna estaba
> en la librería.

```
FAILED test_json_valido_y_sin_salidas_guardadas[07_...ipynb]
FAILED test_json_valido_y_sin_salidas_guardadas[08_PUBLICAR_GITHUB.ipynb]
FAILED test_la_instalacion_no_es_editable[07_...ipynb]
```

---

## 1. Una compuerta que no podía pasar nunca

La regla —los notebooks del repositorio no llevan salidas embebidas— es
correcta. **El sitio donde se exigía, no.** Falla por dos razones
independientes, y las dos se dieron a la vez:

| Notebook | Por qué no puede cumplirla |
|---|---|
| **08** | es el notebook que **corre** la compuerta. Colab autoguarda sus salidas en el .ipynb de Drive mientras se ejecuta: cuando pytest lo lee, ya las tiene — las de la celda que está corriendo. Pedirle que no las tenga es pedirle que no se esté ejecutando |
| **07** | es un notebook de análisis. Usted lo corre contra sus datos y **sus salidas son el resultado**. Exigir que estén vacías obliga a borrarlas a mano antes de cada publicación |

Es la **segunda vez** en esta serie que una prueba se mide a sí misma. La
primera fue la de marcadores, que se detectaba leyendo su propio código fuente.

### Corrección

La propiedad es del **repositorio**, no del árbol de trabajo. Se mudó a los dos
sitios donde sí es exigible:

1. **`preparar_build()` limpia las salidas al copiar.** Garantía incondicional:
   no depende de que nadie se acuerde. En Drive usted conserva las salidas de su
   corrida —que son la evidencia de que funcionó— y a git llega limpio.
2. **La prueba comprueba el checkout de git** cuando lo hay; cuando no lo hay
   —Drive, un zip descomprimido— verifica que la garantía del build siga en pie.
   **Ninguna de las dos ramas se calla.**

Verificado en las tres:

| rama | árbol | resultado |
|---|---|---|
| A | copia de trabajo con salidas | **pasa** — era el falso bloqueo |
| B | checkout de git con salidas | **falla**, nombrando los notebooks |
| C | el build | **limpia 2 notebooks**, Drive intacto |

---

## 2. Un contrato que fijaba la implementación, no el invariante

`test_la_instalacion_no_es_editable` exigía literalmente que la celda de entorno
hiciera `pip install`. Su notebook 07 carga la librería **desde el árbol de
Drive** vía `sys.path`, sin instalar. El contrato lo rechazaba.

Pero el invariante real nunca fue "tiene que haber un pip install". Es: **no
correr código rancio ni incompleto, y fallar ruidosamente si ocurre.** Hay dos
estrategias legítimas y el repositorio usa las dos:

| | 05, 06 | **07 (la suya)** |
|---|---|---|
| estrategia | instalar la rueda | importar desde el árbol |
| no queda rancio porque… | se reinstala con `--force-reinstall` | se lee el árbol directamente **y se verifica de dónde se importó** |
| no queda incompleto porque… | una rueda es un archivo: o está o falla | se importan los submódulos uno por uno |

El contrato se reescribió sobre lo común a las dos.

### Y al reescribirlo apareció un hueco real

**El notebook 05 no verificaba que el paquete quedara completo.** Llevaba ahí
desde que se escribió. El contrato viejo no lo veía porque solo exigía que
hubiera un `pip install`, y lo había. Corregido: 6 submódulos comprobados.

> **La lección:** un contrato escrito sobre la implementación concreta que había
> el día que se escribió no protege el invariante — **lo congela**. Se nota
> cuando rechaza una solución correcta, y para entonces lleva tiempo sin
> proteger nada.

---

## 3. El banco no falló

**AVISO**, no FALLA, en `segundos_total` (+23,8 %), con la huella idéntica y las
cuatro métricas de calidad sin mover un decimal:

```
✅ precision   0.9385 → 0.9385     ✅ f1      0.8780 → 0.8780
✅ recall      0.8249 → 0.8249     ✅ b3_f1   0.9534 → 0.9534
❌ segundos_total  65.06 → 80.53   (base de otra sesión → AVISO)
   huella de la partición: idéntica
```

Es el comportamiento que la 0.22.1 introdujo a propósito. **Funcionó.**

---

## 4. Un tercer hueco, destapado al subir la versión

Subir 0.22.1 → 0.22.2 dejó atrás **22 líneas** que ningún contrato miraba: el
tag de `ORIGEN_GIT` (`...@v0.22.1`, que instalaría la versión equivocada desde
GitHub), el nombre exacto de la rueda que el orquestador busca, y los
encabezados que anuncian el motor. Un notebook que dice 0.22.2 en un sitio y
pide v0.22.1 en otro **instala lo que pide, no lo que dice**.

Contrato nuevo: `test_ninguna_version_operativa_del_notebook_se_queda_atras`.
Exceptúa las referencias históricas, y por **clase de enunciado**, no por
excepción puntual: decir cuándo se midió algo, en qué versión se introdujo, o
comparar con una anterior. Fuera de esas tres, nombrar una versión vieja es
deriva.

---

## 5. Su notebook 07, integrado

Sustituye al mío. Corrió completo en Colab contra los datos reales:

| | |
|---|---|
| entrada | 211.949 filas |
| salida | **99.898 importadores (−52,9 %)** |
| invariantes | **9 de 9 en OK** |
| emparejamiento | 396 s · 14.058.508 pares candidatos |
| países | 529 grafías → 204 canónicos · 0 sin clasificar |
| exportación | XLSX + CORRELATIVA.parquet a Drive |

**Dos cosas que hacía mejor que el mío, y se conservan:**

- **Lee las dependencias del `pyproject.toml` del propio paquete**, en vez de
  una lista copiada a mano en el notebook. No se puede desincronizar. Las
  versiones instaladas fuera de rango se **reportan, no se tocan**: degradar
  numpy en Colab obliga a reiniciar y es decisión suya.
- **Verifica de dónde se importó `record_linkage` de verdad**, y aborta si ya
  había otro cargado en el kernel.

**Lo que se le añadió:** verificación de que el árbol de Drive está **completo**
(9 submódulos, uno por uno). Era la única protección que su estrategia no tenía,
y es el modo de falla característico de FUSE: Drive sincroniza de forma
asíncrona, y el error habría aparecido **a los siete minutos de cómputo**, con
el emparejamiento a medias.

---

## 6. Qué hacer ahora

1. Descomprimir el zip sobre
   `/content/drive/MyDrive/ProColombia/rues_linker_pruebas`, **reemplazando**.
   Trae `dist/rues_linker-0.22.2-py3-none-any.whl`.
2. Esperar a que Drive termine de sincronizar.
3. Correr **A → A.0 → A.1**. A.0 es obligatoria.
4. Publicar (Celda D).

Ya no hace falta borrar las salidas de ningún notebook antes de publicar: el
build las limpia y su copia de Drive se queda como está.

### Lo que sigue sin verificarse

**El push, el tag y el release.** Requieren token, y desde esta sesión el push
se rechaza con **403** contra los dos repositorios (`rues-linker` y
`rues-linker_v2`): la GitHub App no tiene permiso de escritura. Todo lo anterior
a `git push` está probado contra el árbol real.

**Córralo primero contra una rama de prueba**
(`RAMA_DESTINO = 'experiment/prueba-publicacion'`), no contra `main`.
