"""Script idempotente y atómico para inicializar restricciones y poblar paradas y conexiones en Neo4j AuraDB."""

from __future__ import annotations

import sys

from neo4j import ManagedTransaction

from app.config import ConfigValidationError, get_settings
from app.database import DatabaseNotConfiguredError, DatabaseUnavailableError, db

CONSTRAINT_STATEMENTS = (
    "CREATE CONSTRAINT parada_id_unique IF NOT EXISTS FOR (p:Parada) REQUIRE p.id IS UNIQUE",
)

CLEAR_GRAPH_QUERY = "MATCH (n) DETACH DELETE n"

CREATE_GRAPH_QUERY = """
// 1. Crear Paradas de Mérida (Centro, Norte, Poniente, Oriente)
CREATE (plancha:Parada {id: 'PAR-01', nombre: 'Parque La Plancha', zona: 'Centro', tipo: 'Terminal'})
CREATE (paseo60:Parada {id: 'PAR-02', nombre: 'Paseo 60', zona: 'Centro', tipo: 'Paradero'})
CREATE (tec:Parada {id: 'PAR-03', nombre: 'Tecnológico de Mérida', zona: 'Norte', tipo: 'Paradero'})
CREATE (altabrisa:Parada {id: 'PAR-04', nombre: 'Plaza Altabrisa', zona: 'Noreste', tipo: 'Paradero'})
CREATE (uady:Parada {id: 'PAR-05', nombre: 'Facultad de Ingeniería UADY', zona: 'Norte', tipo: 'Terminal'})
CREATE (montejo:Parada {id: 'PAR-06', nombre: 'Francisco de Montejo', zona: 'Poniente', tipo: 'Paradero'})
CREATE (teya:Parada {id: 'PAR-07', nombre: 'Estación Tren Maya Teya', zona: 'Oriente', tipo: 'Terminal'})
CREATE (macro:Parada {id: 'PAR-08', nombre: 'Macroplaza', zona: 'Oriente', tipo: 'Paradero'})
CREATE (nora:Parada {id: 'PAR-09', nombre: 'Nora Quintana', zona: 'Poniente', tipo: 'Base'})
CREATE (chedraui:Parada {id: 'PAR-10', nombre: 'Chedraui Oriente', zona: 'Oriente', tipo: 'Paradero'})

// 2. Conectar tramos dirigidos entre paradas ([:CONECTA_CON])
CREATE (paseo60)-[:CONECTA_CON {minutos: 8, distancia_km: 2.1}]->(plancha)
CREATE (plancha)-[:CONECTA_CON {minutos: 22, distancia_km: 11.5}]->(teya)
CREATE (teya)-[:CONECTA_CON {minutos: 22, distancia_km: 11.5}]->(plancha)
CREATE (plancha)-[:CONECTA_CON {minutos: 8, distancia_km: 2.1}]->(paseo60)

CREATE (plancha)-[:CONECTA_CON {minutos: 15, distancia_km: 6.4}]->(tec)
CREATE (tec)-[:CONECTA_CON {minutos: 12, distancia_km: 5.8}]->(uady)
CREATE (uady)-[:CONECTA_CON {minutos: 12, distancia_km: 5.8}]->(tec)
CREATE (tec)-[:CONECTA_CON {minutos: 15, distancia_km: 6.4}]->(plancha)

CREATE (montejo)-[:CONECTA_CON {minutos: 14, distancia_km: 5.2}]->(tec)
CREATE (tec)-[:CONECTA_CON {minutos: 16, distancia_km: 6.1}]->(altabrisa)
CREATE (altabrisa)-[:CONECTA_CON {minutos: 12, distancia_km: 4.5}]->(macro)
CREATE (macro)-[:CONECTA_CON {minutos: 10, distancia_km: 3.8}]->(chedraui)
CREATE (chedraui)-[:CONECTA_CON {minutos: 15, distancia_km: 5.0}]->(plancha)
CREATE (plancha)-[:CONECTA_CON {minutos: 18, distancia_km: 7.4}]->(montejo)

CREATE (nora)-[:CONECTA_CON {minutos: 11, distancia_km: 4.9}]->(montejo)
CREATE (montejo)-[:CONECTA_CON {minutos: 15, distancia_km: 7.1}]->(uady)
CREATE (uady)-[:CONECTA_CON {minutos: 14, distancia_km: 6.8}]->(altabrisa)
CREATE (altabrisa)-[:CONECTA_CON {minutos: 20, distancia_km: 10.4}]->(teya)
CREATE (teya)-[:CONECTA_CON {minutos: 24, distancia_km: 13.0}]->(nora)
"""

SUMMARY_QUERY = """
CALL () { MATCH (p:Parada) RETURN count(p) AS paradas }
CALL () { MATCH ()-[c:CONECTA_CON]->() RETURN count(c) AS conexiones }
RETURN paradas, conexiones
"""


def ensure_constraints() -> None:
    """Crea la restricción de unicidad en transacciones DDL independientes."""
    db.run_ddl(CONSTRAINT_STATEMENTS)


def _seed_graph_tx(tx: ManagedTransaction) -> dict[str, int]:
    """Limpia y vuelve a poblar todo el grafo dentro de una única transacción atómica."""
    tx.run(CLEAR_GRAPH_QUERY).consume()
    tx.run(CREATE_GRAPH_QUERY).consume()
    summary = tx.run(SUMMARY_QUERY).single()
    if summary is None:
        raise RuntimeError("No se pudo verificar el resumen después del poblado.")
    return summary.data()


def run_seed() -> dict[str, int]:
    """Valida credenciales, aplica restricciones y puebla Neo4j AuraDB."""
    get_settings().validate()
    try:
        ensure_constraints()
        return db.execute_write_tx(_seed_graph_tx)
    finally:
        db.close()


def main() -> int:
    """Punto de entrada CLI para ejecutar `python -m scripts.seed`."""
    try:
        summary = run_seed()
    except (ConfigValidationError, DatabaseNotConfiguredError) as exc:
        print(f"[ERROR DE CONFIGURACIÓN] {exc}", file=sys.stderr)
        print("Edita el archivo .env con tus credenciales reales de Neo4j AuraDB e inténtalo de nuevo.", file=sys.stderr)
        return 1
    except DatabaseUnavailableError as exc:
        print(f"[ERROR DE CONEXIÓN] {exc}", file=sys.stderr)
        return 1

    print("Red Va y Ven poblada exitosamente en Neo4j AuraDB:")
    print(f"  - Paradas:    {summary['paradas']}\n  - Conexiones: {summary['conexiones']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
