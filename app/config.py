"""Lectura y validación explícita de configuración desde variables de entorno (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

# Carga variables desde .env sin sobrescribir variables ya inyectadas en el entorno.
load_dotenv(dotenv_path=ENV_FILE, override=False)

ALLOWED_NEO4J_SCHEMES = (
    "neo4j+s://",
    "neo4j+ssc://",
    "neo4j://",
    "bolt+s://",
    "bolt+ssc://",
    "bolt://",
)

_PLACEHOLDER_PASSWORDS = frozenset(
    {
        "tu_password_generado",
        "your_password",
        "changeme",
        "password",
    }
)


class ConfigValidationError(ValueError):
    """Se lanza cuando las credenciales requeridas de Neo4j son inválidas o están incompletas."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Parámetros de conexión a Neo4j AuraDB validados explícitamente."""

    neo4j_uri: str
    neo4j_username: str
    neo4j_password: str
    neo4j_database: str = "neo4j"

    def configuration_issues(self) -> list[str]:
        """Devuelve la lista de problemas de configuración detectados (vacía si es válida)."""
        issues: list[str] = []

        if not self.neo4j_uri:
            issues.append("Falta definir NEO4J_URI en el archivo .env.")
        elif "xxxxxxxx" in self.neo4j_uri.lower():
            issues.append("NEO4J_URI todavía contiene el host de ejemplo (xxxxxxxx.databases.neo4j.io).")
        elif not self.neo4j_uri.startswith(ALLOWED_NEO4J_SCHEMES):
            issues.append(
                "NEO4J_URI debe iniciar con un esquema válido (neo4j+s://, neo4j://, bolt+s:// o bolt://)."
            )
        else:
            parsed = urlparse(self.neo4j_uri)
            if not parsed.hostname:
                issues.append("NEO4J_URI no contiene un hostname válido.")

        if not self.neo4j_username:
            issues.append("Falta definir NEO4J_USERNAME en el archivo .env.")

        if not self.neo4j_password:
            issues.append("Falta definir NEO4J_PASSWORD en el archivo .env.")
        elif self.neo4j_password.lower() in _PLACEHOLDER_PASSWORDS:
            issues.append("NEO4J_PASSWORD todavía contiene la contraseña de ejemplo.")

        if not self.neo4j_database:
            issues.append("NEO4J_DATABASE no puede estar vacío (usa 'neo4j' por defecto en AuraDB).")

        return issues

    def is_configured(self) -> bool:
        """Indica si todas las credenciales mínimas están configuradas y sin valores de plantilla."""
        return len(self.configuration_issues()) == 0

    def validate(self) -> None:
        """Valida la configuración y falla rápido con un diagnóstico claro si es inválida."""
        issues = self.configuration_issues()
        if issues:
            raise ConfigValidationError(" ".join(issues))


def get_settings() -> Settings:
    """Construye un objeto Settings leyendo el estado actual de las variables de entorno."""
    return Settings(
        neo4j_uri=(os.getenv("NEO4J_URI") or "").strip(),
        neo4j_username=(os.getenv("NEO4J_USERNAME") or "").strip(),
        neo4j_password=(os.getenv("NEO4J_PASSWORD") or "").strip(),
        neo4j_database=(os.getenv("NEO4J_DATABASE") or "neo4j").strip(),
    )


_initial_settings = get_settings()
NEO4J_URI = _initial_settings.neo4j_uri
NEO4J_USERNAME = _initial_settings.neo4j_username
NEO4J_PASSWORD = _initial_settings.neo4j_password
NEO4J_DATABASE = _initial_settings.neo4j_database

