"""Tests de la política anti-FUSE (v0.13.0, T2 de dian-comercio).

Escribir SQLite/HDF5 intensivamente sobre Drive/FUSE es el modo de falla
clásico de Colab ("transport endpoint is not connected"). Contrato:

    1. Destino local → cero redirección, cero copias (mismo comportamiento).
    2. Destino "en Drive" (simulado vía prefijos) → el trabajo ocurre en un
       dir local determinista y el destino recibe copias por fase.
    3. Reanudación: tras "morir la VM" (cache local borrado), una segunda
       corrida RECUPERA los artefactos desde el destino y no regenera firmas.
    4. Los candidatos son idénticos con y sin redirección.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.utils import almacenamiento
from record_linkage.utils.almacenamiento import (
    copiar_si_existe,
    dir_trabajo_seguro,
    es_ruta_fuse,
)


def _df_demo(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    bases = ["COMERCIALIZADORA ANDINA", "TEXTILES PACIFICO", "CAFE EXPORT", "FERRETERIA CENTRAL"]
    filas = []
    for i in range(n):
        nombre = f"{bases[i % 4]} {i // 4}"
        filas.append((nombre, str(800000000 + i)))
        if i % 3 == 0:
            filas.append((nombre + " SAS", str(800000000 + i)))
    df = pd.DataFrame(filas, columns=["NOMBRE_LIMPIO", "NIT_OK"])
    df["FUENTE"] = "A"
    return df.sample(frac=1.0, random_state=int(rng.integers(0, 99))).reset_index(drop=True)


def test_ruta_local_no_redirige(tmp_path):
    destino = tmp_path / "workspace"
    assert not es_ruta_fuse(destino)
    assert dir_trabajo_seguro(destino) == destino


def test_ruta_fuse_redirige_deterministicamente(tmp_path, monkeypatch):
    fake_drive = tmp_path / "content" / "drive" / "MyDrive"
    monkeypatch.setattr(almacenamiento, "_PREFIJOS_FUSE", (str(tmp_path / "content" / "drive"),))
    destino = fake_drive / "ws"
    local_1 = dir_trabajo_seguro(destino)
    local_2 = dir_trabajo_seguro(destino)
    assert local_1 == local_2, "el dir local debe ser determinista por destino"
    assert not str(local_1).startswith(str(fake_drive))
    otro = dir_trabajo_seguro(fake_drive / "ws2")
    assert otro != local_1, "workspaces distintos no deben colisionar"


def test_copiar_si_existe_no_propaga_inexistente(tmp_path):
    assert copiar_si_existe(tmp_path / "no_existe.bin", tmp_path / "x.bin") is False
    origen = tmp_path / "a.bin"
    origen.write_bytes(b"123")
    assert copiar_si_existe(origen, tmp_path / "b.bin") is True
    assert (tmp_path / "b.bin").read_bytes() == b"123"


@pytest.fixture
def perfil_lsh():
    return {
        "lsh_permutations": 64,
        "lsh_threshold": 0.5,
        "lsh_ngram": 3,
        "lsh_chunk_size": 10_000,
        "batch_size": 10_000,
    }


def test_motor_disco_trabaja_local_y_copia_por_fase(tmp_path, monkeypatch, perfil_lsh):
    from record_linkage.engine.lsh.disk_based import DiskBasedLSHEngine

    fake_drive_root = tmp_path / "content" / "drive"
    monkeypatch.setattr(almacenamiento, "_PREFIJOS_FUSE", (str(fake_drive_root),))
    workspace = fake_drive_root / "MyDrive" / "corrida"

    df = _df_demo()
    engine = DiskBasedLSHEngine(perfil_lsh, {})
    engine.find_candidates(df, output_dir=str(workspace))

    # El trabajo NO ocurrió dentro del "Drive" simulado…
    assert not str(engine._storage_dir).startswith(str(fake_drive_root))
    assert engine._sync_final is True
    # …pero el destino final SÍ recibió los artefactos por fase.
    final = workspace / "lsh_disk_cache"
    for nombre in ("signatures.h5", "lsh_index.db", "candidates.db"):
        assert (final / nombre).exists(), f"falta {nombre} en el destino final"
    engine.cleanup(force=True)


def test_reanudacion_recupera_artefactos_del_destino(tmp_path, monkeypatch, perfil_lsh):
    import shutil

    from record_linkage.engine.lsh.disk_based import DiskBasedLSHEngine

    fake_drive_root = tmp_path / "content" / "drive"
    monkeypatch.setattr(almacenamiento, "_PREFIJOS_FUSE", (str(fake_drive_root),))
    workspace = fake_drive_root / "MyDrive" / "corrida"

    df = _df_demo()
    engine_1 = DiskBasedLSHEngine(perfil_lsh, {})
    r1 = engine_1.find_candidates(df, output_dir=str(workspace))
    t_firmas_1 = engine_1.metrics.time_signatures
    local_dir = engine_1._storage_dir
    # Simular reinicio de VM: el disco local se pierde; el "Drive" persiste.
    shutil.rmtree(local_dir, ignore_errors=True)

    engine_2 = DiskBasedLSHEngine(perfil_lsh, {})
    r2 = engine_2.find_candidates(df, output_dir=str(workspace))
    # Las firmas se RECUPERARON (huella de contenido validada): no se regeneran.
    assert engine_2.metrics.time_signatures == 0, (
        f"se regeneraron firmas pese al artefacto recuperado "
        f"(t={engine_2.metrics.time_signatures:.2f}s vs corrida 1 {t_firmas_1:.2f}s)"
    )
    # Y el resultado es el mismo conjunto de candidatos.
    if isinstance(r1, set) and isinstance(r2, set):
        assert r1 == r2
    engine_2.cleanup(force=True)


def test_paridad_con_y_sin_redireccion(tmp_path, monkeypatch, perfil_lsh):
    from record_linkage.engine.lsh.disk_based import DiskBasedLSHEngine

    df = _df_demo()
    # Corrida A: destino local normal (sin redirección).
    engine_a = DiskBasedLSHEngine(perfil_lsh, {})
    r_a = engine_a.find_candidates(df, output_dir=str(tmp_path / "local_ws"))
    # Corrida B: destino "en Drive" simulado (con redirección).
    fake_drive_root = tmp_path / "content" / "drive"
    monkeypatch.setattr(almacenamiento, "_PREFIJOS_FUSE", (str(fake_drive_root),))
    engine_b = DiskBasedLSHEngine(perfil_lsh, {})
    r_b = engine_b.find_candidates(df, output_dir=str(fake_drive_root / "MyDrive" / "ws"))

    if isinstance(r_a, set) and isinstance(r_b, set):
        assert r_a == r_b, "la redirección anti-FUSE alteró los candidatos"
    engine_a.cleanup(force=True)
    engine_b.cleanup(force=True)
