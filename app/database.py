"""Gestión Singleton del driver oficial de Neo4j AuraDB y transacciones seguras."""

from __future__ import annotations

import os
import ssl
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, LiteralString, Protocol, TypeVar

import certifi
from neo4j import Driver, GraphDatabase, ManagedTransaction, Session, exceptions

from app.config import ConfigValidationError, Settings, get_settings

T = TypeVar("T")


class DatabaseNotConfiguredError(RuntimeError):
    """Error lanzado cuando el archivo .env aún no tiene credenciales válidas."""


class DatabaseUnavailableError(RuntimeError):
    """Error lanzado cuando Neo4j AuraDB rechaza la conexión, autenticación o red."""


class DuplicateEntityError(RuntimeError):
    """Error lanzado ante colisiones de clave única (SKU o ID duplicado)."""


class EntityNotFoundError(LookupError):
    """Error de dominio lanzado cuando una Sucursal, Producto o Kit no existe."""


class GraphDatabaseGateway(Protocol):
    """Contrato mínimo de acceso a grafo para desacoplar FastAPI de la implementación."""

    def health_check(self) -> dict[str, Any]:
        """Verifica configuración y conectividad real contra la base de datos."""

    def execute_read_tx(self, work: Callable[[ManagedTransaction], T]) -> T:
        """Ejecuta una transacción gestionada de solo lectura."""

    def execute_write_tx(self, work: Callable[[ManagedTransaction], T]) -> T:
        """Ejecuta una transacción gestionada de escritura."""

    def read_all(self, query: LiteralString, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Ejecuta una lectura y devuelve todos los registros como diccionarios."""

    def read_single(self, query: LiteralString, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Ejecuta una lectura y devuelve un único registro o None."""

    def write_single(self, query: LiteralString, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Ejecuta una escritura y devuelve un único registro o None."""

    def close(self) -> None:
        """Libera el pool de conexiones del driver."""


class Neo4jConnection:
    """Singleton que reutiliza el connection pool oficial de GraphDatabase.driver."""

    def __init__(self, settings_provider: Callable[[], Settings] = get_settings) -> None:
        self._settings_provider = settings_provider
        self._driver: Driver | None = None
        self._driver_signature: tuple[str, str, str] | None = None
        self._lock = threading.Lock()

    def _get_driver(self) -> tuple[Driver, Settings]:
        settings = self._settings_provider()
        try:
            settings.validate()
        except ConfigValidationError as exc:
            raise DatabaseNotConfiguredError(str(exc)) from exc

        signature = (settings.neo4j_uri, settings.neo4j_username, settings.neo4j_database)

        with self._lock:
            if self._driver is not None and self._driver_signature != signature:
                self._driver.close()
                self._driver = None

            if self._driver is None:
                default_ca = ssl.get_default_verify_paths().openssl_cafile
                if not default_ca or not Path(default_ca).exists():
                    # En instalaciones macOS sin Install Certificates.command, usa el bundle Mozilla de certifi.
                    os.environ["SSL_CERT_FILE"] = certifi.where()
                try:
                    self._driver = GraphDatabase.driver(
                        settings.neo4j_uri,
                        auth=(settings.neo4j_username, settings.neo4j_password),
                        connection_timeout=10.0,
                        max_connection_lifetime=300.0,
                        max_transaction_retry_time=10.0,
                    )
                    self._driver_signature = signature
                except exceptions.ConfigurationError as exc:
                    raise DatabaseNotConfiguredError(
                        f"Configuración de URI/TLS de Neo4j inválida: {exc}"
                    ) from exc
                except exceptions.DriverError as exc:
                    raise DatabaseUnavailableError(
                        f"No fue posible inicializar el driver de Neo4j: {exc}"
                    ) from exc

            return self._driver, settings

    def get_session(self) -> Session:
        """Abre una sesión apuntando a la base configurada."""
        driver, settings = self._get_driver()
        return driver.session(database=settings.neo4j_database)

    def _run_in_session(self, fn: Callable[[Session], T]) -> T:
        driver, settings = self._get_driver()
        try:
            with driver.session(database=settings.neo4j_database) as session:
                return fn(session)
        except exceptions.ConstraintError as exc:
            raise DuplicateEntityError(
                "Ya existe un registro con el identificador único proporcionado."
            ) from exc
        except exceptions.AuthError as exc:
            raise DatabaseUnavailableError(
                "Autenticación rechazada por Neo4j AuraDB. Verifica NEO4J_USERNAME y NEO4J_PASSWORD en .env."
            ) from exc
        except (exceptions.ServiceUnavailable, exceptions.ConfigurationError, exceptions.DriverError) as exc:
            raise DatabaseUnavailableError(
                f"No hay conectividad con Neo4j AuraDB: {exc}"
            ) from exc

    def _run_managed(
        self,
        mode: str,
        work: Callable[[ManagedTransaction], T],
    ) -> T:
        return self._run_in_session(
            lambda session: session.execute_read(work) if mode == "read" else session.execute_write(work)
        )

    def run_ddl(self, statements: tuple[LiteralString, ...] | list[LiteralString]) -> None:
        """Ejecuta sentencias DDL en sesiones auto-commit reutilizando la traducción de errores."""
        def _run(session: Session) -> None:
            for stmt in statements:
                session.run(stmt).consume()

        self._run_in_session(_run)

    def execute_read_tx(self, work: Callable[[ManagedTransaction], T]) -> T:
        """Ejecuta una función transaccional de solo lectura con reintentos gestionados."""
        return self._run_managed("read", work)

    def execute_write_tx(self, work: Callable[[ManagedTransaction], T]) -> T:
        """Ejecuta una función transaccional de escritura con reintentos gestionados."""
        return self._run_managed("write", work)

    def read_all(self, query: LiteralString, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Ejecuta una consulta Cypher de lectura y retorna la lista de registros."""
        def _tx(tx: ManagedTransaction) -> list[dict[str, Any]]:
            result = tx.run(query, params or {})
            return [record.data() for record in result]

        return self.execute_read_tx(_tx)

    def read_single(self, query: LiteralString, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Ejecuta una consulta Cypher de lectura esperando 0 o 1 registro."""
        def _tx(tx: ManagedTransaction) -> dict[str, Any] | None:
            record = tx.run(query, params or {}).single()
            return record.data() if record is not None else None

        return self.execute_read_tx(_tx)

    def write_single(self, query: LiteralString, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Ejecuta una consulta Cypher de escritura esperando 0 o 1 registro."""
        def _tx(tx: ManagedTransaction) -> dict[str, Any] | None:
            record = tx.run(query, params or {}).single()
            return record.data() if record is not None else None

        return self.execute_write_tx(_tx)

    def health_check(self) -> dict[str, Any]:
        """Comprueba si .env está configurado y si Neo4j AuraDB responde al handshake."""
        settings = self._settings_provider()
        issues = settings.configuration_issues()
        if issues:
            return {
                "status": "unconfigured",
                "configured": False,
                "connected": False,
                "database": settings.neo4j_database or "neo4j",
                "issues": issues,
                "detail": "Configura tus credenciales reales de Neo4j AuraDB en .env.",
            }

        try:
            driver, _ = self._get_driver()
            driver.verify_connectivity()
            return {
                "status": "ok",
                "configured": True,
                "connected": True,
                "database": settings.neo4j_database,
                "issues": [],
                "detail": "Conectado exitosamente a Neo4j AuraDB.",
            }
        except DatabaseNotConfiguredError as exc:
            return {
                "status": "unconfigured",
                "configured": False,
                "connected": False,
                "database": settings.neo4j_database,
                "issues": [str(exc)],
                "detail": str(exc),
            }
        except (DatabaseUnavailableError, exceptions.Neo4jError, exceptions.DriverError) as exc:
            return {
                "status": "degraded",
                "configured": True,
                "connected": False,
                "database": settings.neo4j_database,
                "issues": [str(exc)],
                "detail": str(exc),
            }

    def close(self) -> None:
        """Cierra ordenadamente el driver oficial y libera conexiones activas."""
        with self._lock:
            if self._driver is not None:
                self._driver.close()
                self._driver = None
                self._driver_signature = None


db = Neo4jConnection()

