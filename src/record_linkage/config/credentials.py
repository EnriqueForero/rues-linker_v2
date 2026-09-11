"""record_linkage.config.credentials — Gestión segura de credenciales.

Orden de búsqueda (primero gana, sin fallthrough silencioso):
    1. Google Colab Secrets (recomendado en Colab)
    2. Variables de entorno (ideal para CI/CD y Docker)
    3. Archivo config.json local (último recurso, NUNCA committear)

Nunca imprime el valor de las credenciales. Solo confirma de dónde se cargó.

Uso típico::

    from record_linkage.config.credentials import get_snowflake_credentials

    creds = get_snowflake_credentials()
    conn = snowflake.connector.connect(**creds)
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnowflakeCredentials:
    """Credenciales mínimas para conectar a Snowflake.

    Frozen para evitar mutación accidental tras la carga.
    """

    account: str
    user: str
    password: str = field(repr=False)
    warehouse: str
    database: str
    schema: str
    role: str | None = None

    def to_connector_kwargs(self) -> dict[str, str]:
        """Convierte a kwargs aceptables por snowflake.connector.connect."""
        kwargs = {
            "account": self.account,
            "user": self.user,
            "password": self.password,
            "warehouse": self.warehouse,
            "database": self.database,
            "schema": self.schema,
        }
        if self.role:
            kwargs["role"] = self.role
        return kwargs


# ════════════════════════════════════════════════════════════════════
# Cargadores ordenados por prioridad
# ════════════════════════════════════════════════════════════════════

_REQUIRED_KEYS = ("account", "user", "password", "warehouse", "database", "schema")
_COLAB_SECRET_KEYS = {key: f"SNOWFLAKE_{key.upper()}" for key in (*_REQUIRED_KEYS, "role")}
_ENV_KEYS = {key: f"SNOWFLAKE_{key.upper()}" for key in (*_REQUIRED_KEYS, "role")}


def _load_from_colab_secrets() -> dict[str, str] | None:
    """Carga credenciales desde Colab Secrets (google.colab.userdata).

    Retorna None si no está en Colab o si algún secreto requerido falta.
    No lanza excepciones; el fallback al siguiente método debe ser limpio.
    """
    try:
        from google.colab import userdata  # type: ignore[import-not-found]
    except ImportError:
        return None

    creds: dict[str, str] = {}
    for key, secret_name in _COLAB_SECRET_KEYS.items():
        try:
            value = userdata.get(secret_name)
        except Exception:
            value = None
        if value:
            creds[key] = value

    # Validar que están todos los requeridos
    missing = [k for k in _REQUIRED_KEYS if k not in creds]
    if missing:
        logger.debug("Colab Secrets incompleto, faltantes: %s", missing)
        return None

    logger.info("Credenciales Snowflake cargadas desde Colab Secrets")
    return creds


def _load_from_env() -> dict[str, str] | None:
    """Carga credenciales desde variables de entorno SNOWFLAKE_*."""
    creds: dict[str, str] = {}
    for key, env_name in _ENV_KEYS.items():
        value = os.environ.get(env_name)
        if value:
            creds[key] = value

    missing = [k for k in _REQUIRED_KEYS if k not in creds]
    if missing:
        logger.debug("Variables de entorno incompletas, faltantes: %s", missing)
        return None

    logger.info("Credenciales Snowflake cargadas desde variables de entorno")
    return creds


def _load_from_file(config_path: Path) -> dict[str, str] | None:
    """Carga credenciales desde un archivo config.json.

    Estructura esperada::

        {
          "snowflake": {
            "account": "...",
            "user": "...",
            "password": "...",
            "warehouse": "...",
            "database": "...",
            "schema": "...",
            "role": "..."
          }
        }
    """
    if not config_path.exists():
        logger.debug("config.json no existe en %s", config_path)
        return None

    try:
        if os.name == "posix":
            permissions = stat.S_IMODE(config_path.stat().st_mode)
            if permissions & 0o077:
                logger.error(
                    "Credenciales rechazadas por permisos inseguros en %s (%o); use chmod 600",
                    config_path,
                    permissions,
                )
                return None
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("No se pudo leer config.json de forma segura: %s", exc)
        return None

    if not isinstance(data, dict):
        logger.warning("config.json debe contener un objeto JSON en la raíz")
        return None

    sf = data.get("snowflake")
    if not isinstance(sf, dict):
        logger.warning("config.json no tiene sección 'snowflake'")
        return None

    missing = [
        key
        for key in _REQUIRED_KEYS
        if not isinstance(sf.get(key), str) or not str(sf[key]).strip()
    ]
    if missing:
        logger.warning("config.json incompleto, faltantes: %s", missing)
        return None

    logger.info("Credenciales Snowflake cargadas desde %s", config_path)
    return {
        key: str(sf[key])
        for key in (*_REQUIRED_KEYS, "role")
        if isinstance(sf.get(key), str) and str(sf[key]).strip()
    }


# ════════════════════════════════════════════════════════════════════
# API pública
# ════════════════════════════════════════════════════════════════════


def get_snowflake_credentials(
    config_path: Path | None = None,
) -> SnowflakeCredentials:
    """Obtiene credenciales Snowflake siguiendo el orden de prioridad.

    Args:
        config_path: Ruta opcional a config.json (solo se usa si Colab Secrets
            y env vars fallan). Por defecto, busca ./config.json.

    Returns:
        SnowflakeCredentials con todos los campos requeridos.

    Raises:
        RuntimeError: Si ninguna fuente provee las credenciales completas.

    Example:
        >>> creds = get_snowflake_credentials()
        >>> import snowflake.connector
        >>> conn = snowflake.connector.connect(**creds.to_connector_kwargs())
    """
    # 1. Colab Secrets
    data = _load_from_colab_secrets()

    # 2. Variables de entorno
    if data is None:
        data = _load_from_env()

    # 3. Archivo config.json
    if data is None:
        path = config_path or Path("config.json")
        data = _load_from_file(path)

    if data is None:
        raise RuntimeError(
            "No se pudieron cargar credenciales Snowflake. Configure una de:\n"
            "  1) Colab Secrets: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,\n"
            "     SNOWFLAKE_WAREHOUSE, SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA (opcional ROLE)\n"
            "  2) Variables de entorno con los mismos nombres\n"
            "  3) config.json en ./ con sección 'snowflake' (no committear)\n"
            "Ver docs/secrets.md para instrucciones detalladas."
        )

    return SnowflakeCredentials(
        account=data["account"],
        user=data["user"],
        password=data["password"],
        warehouse=data["warehouse"],
        database=data["database"],
        schema=data["schema"],
        role=data.get("role"),
    )
