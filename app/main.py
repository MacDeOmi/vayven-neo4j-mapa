"""API FastAPI para la Red de Paradas y Conexiones 'Va y Ven' (Neo4j AuraDB)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import re
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Path as ApiPath, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from neo4j import ManagedTransaction

from app.database import (
    DatabaseNotConfiguredError,
    DatabaseUnavailableError,
    DuplicateEntityError,
    EntityNotFoundError,
    GraphDatabaseGateway,
    db,
)
from app.models import (
    CodeStr,
    ConexionCreate,
    ConexionDeletedResponse,
    ConexionOut,
    ConexionUpdate,
    HealthResponse,
    ParadaAlcanceOut,
    ParadaCreate,
    ParadaCreatedResponse,
    ParadaDeletedResponse,
    ParadaOut,
    ParadaUpdate,
    ParadaUpdatedResponse,
    RedTransporteResponse,
    SalidaDirectaOut,
    ViajePlanResponse,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def get_db() -> GraphDatabaseGateway:
    """Proveedor de dependencia para inyectar el gateway de base de datos."""
    return db


DBDep = Annotated[GraphDatabaseGateway, Depends(get_db)]
ParadaIdPath = Annotated[
    CodeStr,
    ApiPath(description="Código de parada Va y Ven (ej. PAR-01)", examples=["PAR-01"]),
]


def _require_parada(tx: ManagedTransaction, parada_id: str) -> dict[str, Any]:
    record = tx.run(
        "MATCH (p:Parada {id: $id}) RETURN p.id AS id, p.nombre AS nombre, p.zona AS zona, p.tipo AS tipo",
        {"id": parada_id},
    ).single()
    if record is None:
        raise EntityNotFoundError(f"Parada '{parada_id}' no encontrada.")
    return record.data()


def _tarifa_vayven(tipo_tarifa: Literal["general", "estudiante"]) -> float:
    """Devuelve la tarifa única vigente Va y Ven según tipo de tarjeta."""
    return 12.0 if tipo_tarifa == "general" else 5.0


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Cierra ordenadamente el pool de conexiones de Neo4j al apagar el servidor."""
    yield
    db.close()


app = FastAPI(
    title="Mapa Va y Ven — Neo4j",
    description=(
        "API REST sobre Neo4j AuraDB para consulta de rutas óptimas entre paradas Va y Ven (shortestPath), "
        "vecindades de salida, alcance multi-salto y CRUD completo de paradas y conexiones."
    ),
    version="2.1.0",
    lifespan=lifespan,
)


@app.exception_handler(DatabaseNotConfiguredError)
async def _handle_db_not_configured(_request: Request, exc: DatabaseNotConfiguredError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": str(exc)})


@app.exception_handler(DatabaseUnavailableError)
async def _handle_db_unavailable(_request: Request, exc: DatabaseUnavailableError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": str(exc)})


@app.exception_handler(DuplicateEntityError)
async def _handle_duplicate_entity(_request: Request, exc: DuplicateEntityError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})


@app.exception_handler(EntityNotFoundError)
async def _handle_entity_not_found(_request: Request, exc: EntityNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})


# ==================== 0. SALUD Y TOPOLOGÍA ====================


@app.get("/api/health", response_model=HealthResponse, tags=["Sistema"])
def verificar_salud(database: DBDep) -> dict[str, Any]:
    """Verifica configuración (.env) y conectividad real con Neo4j AuraDB."""
    return database.health_check()


@app.get("/api/red", response_model=RedTransporteResponse, tags=["Catálogos y Red"])
def obtener_red_completa(database: DBDep) -> dict[str, Any]:
    """Obtiene todas las paradas y conexiones activas del grafo Va y Ven."""
    def _tx(tx: ManagedTransaction) -> dict[str, Any]:
        paradas = [
            r.data()
            for r in tx.run(
                """
                MATCH (p:Parada)
                RETURN p.id AS id, p.nombre AS nombre, p.zona AS zona, p.tipo AS tipo
                ORDER BY p.id ASC
                """
            )
        ]
        conexiones = [
            r.data()
            for r in tx.run(
                """
                MATCH (o:Parada)-[c:CONECTA_CON]->(d:Parada)
                RETURN o.id AS origen_id,
                       o.nombre AS origen,
                       d.id AS destino_id,
                       d.nombre AS destino,
                       toInteger(c.minutos) AS minutos,
                       toFloat(c.distancia_km) AS distancia_km
                ORDER BY o.id ASC, d.id ASC
                """
            )
        ]
        return {"paradas": paradas, "conexiones": conexiones}

    return database.execute_read_tx(_tx)


# ==================== 1. OPERACIONES CRUD (PARADAS Y CONEXIONES) ====================


@app.get("/api/paradas", response_model=list[ParadaOut], tags=["CRUD Paradas y Tramos"])
def listar_paradas(database: DBDep) -> list[dict[str, Any]]:
    """READ: Lista todas las paradas ordenadas por código."""
    query = """
    MATCH (p:Parada)
    RETURN p.id AS id, p.nombre AS nombre, p.zona AS zona, p.tipo AS tipo
    ORDER BY p.id ASC
    """
    return database.read_all(query)


@app.get("/api/paradas/{parada_id}", response_model=ParadaOut, tags=["CRUD Paradas y Tramos"])
def obtener_parada(parada_id: ParadaIdPath, database: DBDep) -> dict[str, Any]:
    """READ: Obtiene el detalle de una parada por código."""
    return database.execute_read_tx(lambda tx: _require_parada(tx, parada_id))


def _next_parada_id(tx: ManagedTransaction) -> str:
    """Genera el siguiente código incremental PAR-XX según las paradas existentes."""
    max_num = 0
    for rec in tx.run("MATCH (p:Parada) RETURN p.id AS id"):
        match = re.fullmatch(r"PAR-(\d+)", str(rec.data().get("id", "")).upper())
        if match:
            max_num = max(max_num, int(match.group(1)))
    return f"PAR-{max_num + 1:02d}"


@app.post(
    "/api/paradas",
    response_model=ParadaCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["CRUD Paradas y Tramos"],
)
def crear_parada(parada: ParadaCreate, database: DBDep) -> dict[str, Any]:
    """CREATE: Crea un nuevo nodo Parada (con código incremental si no se envía)."""
    def _tx(tx: ManagedTransaction) -> dict[str, Any]:
        parada_id = parada.id or _next_parada_id(tx)
        payload = {**parada.model_dump(), "id": parada_id}
        record = tx.run(
            """
            OPTIONAL MATCH (existing:Parada {id: $id})
            WITH existing
            WHERE existing IS NULL
            CREATE (p:Parada {id: $id, nombre: $nombre, zona: $zona, tipo: $tipo})
            RETURN p.id AS id, p.nombre AS nombre, p.zona AS zona, p.tipo AS tipo
            """,
            payload,
        ).single()
        if record is None:
            raise DuplicateEntityError(f"Ya existe una parada registrada con el código '{parada_id}'.")
        return {"mensaje": "Parada creada", "parada": record.data()}

    return database.execute_write_tx(_tx)


@app.put("/api/paradas/{parada_id}", response_model=ParadaUpdatedResponse, tags=["CRUD Paradas y Tramos"])
def actualizar_parada(parada_id: ParadaIdPath, data: ParadaUpdate, database: DBDep) -> dict[str, Any]:
    """UPDATE: Actualiza nombre, zona y/o tipo de una parada existente."""
    query = """
    MATCH (p:Parada {id: $id})
    SET p.nombre = coalesce($nombre, p.nombre),
        p.zona = coalesce($zona, p.zona),
        p.tipo = coalesce($tipo, p.tipo)
    RETURN p.id AS id, p.nombre AS nombre, p.zona AS zona, p.tipo AS tipo
    """
    updated = database.write_single(query, {"id": parada_id, **data.model_dump()})
    if updated is None:
        raise EntityNotFoundError(f"Parada '{parada_id}' no encontrada.")
    return {"mensaje": "Parada actualizada", "parada": updated}


@app.delete("/api/paradas/{parada_id}", response_model=ParadaDeletedResponse, tags=["CRUD Paradas y Tramos"])
def eliminar_parada(parada_id: ParadaIdPath, database: DBDep) -> dict[str, Any]:
    """DELETE: Elimina una parada y todas sus conexiones del grafo (DETACH DELETE)."""
    query = """
    MATCH (p:Parada {id: $id})
    WITH collect(p) AS nodos
    FOREACH (n IN nodos | DETACH DELETE n)
    RETURN size(nodos) AS eliminados
    """
    result = database.write_single(query, {"id": parada_id})
    eliminados = int(result["eliminados"]) if result else 0
    if eliminados == 0:
        raise EntityNotFoundError(f"Parada '{parada_id}' no encontrada.")
    return {"mensaje": f"Parada {parada_id} eliminada del grafo", "id": parada_id, "eliminados": eliminados}


@app.post(
    "/api/conexiones",
    response_model=ConexionOut,
    status_code=status.HTTP_201_CREATED,
    tags=["CRUD Paradas y Tramos"],
)
def crear_conexion(data: ConexionCreate, database: DBDep) -> dict[str, Any]:
    """CREATE: Crea una relación dirigida CONECTA_CON entre dos paradas (rechaza duplicados)."""
    def _tx(tx: ManagedTransaction) -> dict[str, Any]:
        _require_parada(tx, data.origen_id)
        _require_parada(tx, data.destino_id)
        existing = tx.run(
            """
            MATCH (o:Parada {id: $origen_id})-[c:CONECTA_CON]->(d:Parada {id: $destino_id})
            RETURN elementId(c) AS rel_id
            LIMIT 1
            """,
            {"origen_id": data.origen_id, "destino_id": data.destino_id},
        ).single()
        if existing is not None:
            raise DuplicateEntityError(
                f"Ya existe una conexión de '{data.origen_id}' a '{data.destino_id}'."
            )

        record = tx.run(
            """
            MATCH (o:Parada {id: $origen_id}), (d:Parada {id: $destino_id})
            CREATE (o)-[c:CONECTA_CON {minutos: $minutos, distancia_km: $distancia_km}]->(d)
            RETURN o.id AS origen_id,
                   o.nombre AS origen,
                   d.id AS destino_id,
                   d.nombre AS destino,
                   toInteger(c.minutos) AS minutos,
                   toFloat(c.distancia_km) AS distancia_km
            """,
            data.model_dump(),
        ).single()
        if record is None:
            raise EntityNotFoundError("No se pudo crear la conexión solicitada.")
        return record.data()

    return database.execute_write_tx(_tx)


@app.put(
    "/api/conexiones/{origen_id}/{destino_id}",
    response_model=ConexionOut,
    tags=["CRUD Paradas y Tramos"],
)
def actualizar_conexion(
    origen_id: ParadaIdPath,
    destino_id: ParadaIdPath,
    data: ConexionUpdate,
    database: DBDep,
) -> dict[str, Any]:
    """UPDATE: Actualiza minutos o distancia de una conexión existente."""
    query = """
    MATCH (o:Parada {id: $origen_id})-[c:CONECTA_CON]->(d:Parada {id: $destino_id})
    SET c.minutos = coalesce($minutos, c.minutos),
        c.distancia_km = coalesce($distancia_km, c.distancia_km)
    RETURN o.id AS origen_id,
           o.nombre AS origen,
           d.id AS destino_id,
           d.nombre AS destino,
           toInteger(c.minutos) AS minutos,
           toFloat(c.distancia_km) AS distancia_km
    """
    result = database.write_single(
        query,
        {"origen_id": origen_id, "destino_id": destino_id, **data.model_dump()},
    )
    if result is None:
        raise EntityNotFoundError(f"No existe conexión de '{origen_id}' a '{destino_id}'.")
    return result


@app.delete(
    "/api/conexiones/{origen_id}/{destino_id}",
    response_model=ConexionDeletedResponse,
    tags=["CRUD Paradas y Tramos"],
)
def eliminar_conexion(
    origen_id: ParadaIdPath,
    destino_id: ParadaIdPath,
    database: DBDep,
) -> dict[str, Any]:
    """DELETE: Elimina un tramo dirigido entre dos paradas."""
    query = """
    MATCH (:Parada {id: $origen_id})-[c:CONECTA_CON]->(:Parada {id: $destino_id})
    WITH collect(c) AS rels
    FOREACH (r IN rels | DELETE r)
    RETURN size(rels) AS eliminados
    """
    result = database.write_single(query, {"origen_id": origen_id, "destino_id": destino_id})
    eliminados = int(result["eliminados"]) if result else 0
    if eliminados == 0:
        raise EntityNotFoundError(f"No existe conexión de '{origen_id}' a '{destino_id}'.")
    return {"mensaje": "Conexión eliminada", "eliminados": eliminados}


# ==================== 2. CONSULTAS ESPECIALIZADAS DE GRAFO ====================


@app.get(
    "/api/viaje/{origen_id}/{destino_id}",
    response_model=ViajePlanResponse,
    tags=["Consultas de Grafo"],
)
def planear_viaje(
    origen_id: ParadaIdPath,
    destino_id: ParadaIdPath,
    database: DBDep,
    tipo_tarifa: Literal["general", "estudiante"] = Query(
        default="general",
        description="Tarifa Va y Ven aplicable (general o estudiante)",
    ),
) -> dict[str, Any]:
    """CONSULTA A (Shortest Path): Calcula la ruta mínima entre dos paradas en Neo4j."""
    def _tx(tx: ManagedTransaction) -> dict[str, Any]:
        origen = _require_parada(tx, origen_id)
        destino = _require_parada(tx, destino_id)
        if origen_id == destino_id:
            return {
                "origen_id": origen["id"],
                "origen": origen["nombre"],
                "destino_id": destino["id"],
                "destino": destino["nombre"],
                "tipo_tarifa": tipo_tarifa,
                "minutos_totales": 0,
                "distancia_total_km": 0.0,
                "numero_paradas": 1,
                "costo_total": 0.0,
                "tramos": [],
            }

        row = tx.run(
            """
            MATCH (o:Parada {id: $origen_id}), (d:Parada {id: $destino_id})
            MATCH path = shortestPath((o)-[:CONECTA_CON*..8]->(d))
            WITH nodes(path) AS ns, relationships(path) AS rs
            UNWIND range(0, size(rs) - 1) AS i
            WITH ns[i] AS desde, ns[i + 1] AS hacia, rs[i] AS rel
            RETURN desde.id AS desde_id,
                   desde.nombre AS desde_nombre,
                   hacia.id AS hacia_id,
                   hacia.nombre AS hacia_nombre,
                   toInteger(rel.minutos) AS minutos,
                   toFloat(rel.distancia_km) AS distancia_km
            """,
            {"origen_id": origen_id, "destino_id": destino_id},
        )
        tramos = [record.data() for record in row]
        if not tramos:
            raise EntityNotFoundError(
                f"No hay una ruta conectada entre '{origen['nombre']}' y '{destino['nombre']}'."
            )

        return {
            "origen_id": origen["id"],
            "origen": origen["nombre"],
            "destino_id": destino["id"],
            "destino": destino["nombre"],
            "tipo_tarifa": tipo_tarifa,
            "minutos_totales": sum(t["minutos"] for t in tramos),
            "distancia_total_km": round(sum(t["distancia_km"] for t in tramos), 2),
            "numero_paradas": len(tramos) + 1,
            "costo_total": _tarifa_vayven(tipo_tarifa),
            "tramos": tramos,
        }

    return database.execute_read_tx(_tx)


@app.get(
    "/api/paradas/{parada_id}/salidas",
    response_model=list[SalidaDirectaOut],
    tags=["Consultas de Grafo"],
)
def listar_salidas_parada(parada_id: ParadaIdPath, database: DBDep) -> list[dict[str, Any]]:
    """CONSULTA B (Vecindad directa): Destinos alcanzables en 1 tramo desde una parada."""
    def _tx(tx: ManagedTransaction) -> list[dict[str, Any]]:
        _require_parada(tx, parada_id)
        rows = tx.run(
            """
            MATCH (:Parada {id: $parada_id})-[c:CONECTA_CON]->(d:Parada)
            RETURN d.id AS destino_id,
                   d.nombre AS destino,
                   d.zona AS zona_destino,
                   toInteger(c.minutos) AS minutos,
                   toFloat(c.distancia_km) AS distancia_km
            ORDER BY minutos ASC, destino ASC
            """,
            {"parada_id": parada_id},
        )
        return [record.data() for record in rows]

    return database.execute_read_tx(_tx)


@app.get(
    "/api/paradas/{parada_id}/alcance",
    response_model=list[ParadaAlcanceOut],
    tags=["Consultas de Grafo"],
)
def consultar_alcance_parada(parada_id: ParadaIdPath, database: DBDep) -> list[dict[str, Any]]:
    """CONSULTA C (Traversal multi-salto): Paradas alcanzables en 1 o 2 tramos desde el origen."""
    def _tx(tx: ManagedTransaction) -> list[dict[str, Any]]:
        _require_parada(tx, parada_id)
        rows = tx.run(
            """
            MATCH (o:Parada {id: $parada_id})-[rels:CONECTA_CON*1..2]->(d:Parada)
            WHERE d.id <> o.id
            WITH d,
                 size(rels) AS saltos,
                 reduce(total = 0, r IN rels | total + toInteger(r.minutos)) AS minutos_totales
            WITH d,
                 min(saltos) AS saltos,
                 min(minutos_totales) AS minutos_totales
            RETURN d.id AS destino_id,
                   d.nombre AS destino,
                   d.zona AS zona_destino,
                   saltos,
                   minutos_totales
            ORDER BY minutos_totales ASC, saltos ASC, destino ASC
            """,
            {"parada_id": parada_id},
        )
        return [record.data() for record in rows]

    return database.execute_read_tx(_tx)


if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
