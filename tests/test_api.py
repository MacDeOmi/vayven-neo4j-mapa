"""Pruebas unitarias y de integración FastAPI para la Red de Transporte Va y Ven."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigValidationError, Settings
from app.database import Neo4jConnection
from app.main import _tarifa_vayven, app, get_db
from scripts import seed as seed_script


@dataclass
class _StubRecord:
    payload: dict[str, Any]

    def data(self) -> dict[str, Any]:
        return dict(self.payload)


class _StubResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = [_StubRecord(r) for r in rows]

    def single(self) -> _StubRecord | None:
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class InMemoryVaYVenTx:
    """Simulador fiel en memoria del grafo Va y Ven definido en scripts/seed.py."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    def run(self, query: str, params: dict[str, Any] | None = None) -> _StubResult:
        params = params or {}
        q = " ".join(query.split())

        if "MATCH (p:Parada {id: $id}) RETURN p.id AS id, p.nombre AS nombre" in q:
            par = self.state["paradas"].get(params["id"])
            return _StubResult([dict(par)] if par else [])

        if "MATCH (p:Parada) RETURN p.id AS id" in q:
            return _StubResult(sorted(self.state["paradas"].values(), key=lambda x: x["id"]))

        if "MATCH (o:Parada)-[c:CONECTA_CON]->(d:Parada)" in q:
            rows = []
            for c in sorted(self.state["conexiones"], key=lambda x: (x["origen_id"], x["destino_id"])):
                rows.append(
                    {
                        "origen_id": c["origen_id"],
                        "origen": self.state["paradas"][c["origen_id"]]["nombre"],
                        "destino_id": c["destino_id"],
                        "destino": self.state["paradas"][c["destino_id"]]["nombre"],
                        "minutos": int(c["minutos"]),
                        "distancia_km": float(c["distancia_km"]),
                    }
                )
            return _StubResult(rows)

        if "OPTIONAL MATCH (existing:Parada {id: $id})" in q:
            if params["id"] in self.state["paradas"]:
                return _StubResult([])
            created = {
                "id": params["id"],
                "nombre": params["nombre"],
                "zona": params["zona"],
                "tipo": params["tipo"],
            }
            self.state["paradas"][params["id"]] = created
            return _StubResult([dict(created)])

        if "MATCH (p:Parada {id: $id}) SET p.nombre = coalesce" in q:
            par = self.state["paradas"].get(params["id"])
            if not par:
                return _StubResult([])
            for field in ("nombre", "zona", "tipo"):
                if params.get(field) is not None:
                    par[field] = params[field]
            return _StubResult([dict(par)])

        if "FOREACH (n IN nodos | DETACH DELETE n)" in q:
            pid = params["id"]
            if pid not in self.state["paradas"]:
                return _StubResult([{"eliminados": 0}])
            del self.state["paradas"][pid]
            self.state["conexiones"] = [
                c for c in self.state["conexiones"] if c["origen_id"] != pid and c["destino_id"] != pid
            ]
            return _StubResult([{"eliminados": 1}])

        if "RETURN elementId(c) AS rel_id" in q:
            existing = next(
                (
                    c
                    for c in self.state["conexiones"]
                    if c["origen_id"] == params["origen_id"]
                    and c["destino_id"] == params["destino_id"]
                ),
                None,
            )
            return _StubResult([{"rel_id": "1"}] if existing else [])

        if "CREATE (o)-[c:CONECTA_CON {minutos: $minutos, distancia_km: $distancia_km}]->(d)" in q:
            created = {
                "origen_id": params["origen_id"],
                "destino_id": params["destino_id"],
                "minutos": int(params["minutos"]),
                "distancia_km": float(params["distancia_km"]),
            }
            self.state["conexiones"].append(created)
            return _StubResult(
                [
                    {
                        "origen_id": params["origen_id"],
                        "origen": self.state["paradas"][params["origen_id"]]["nombre"],
                        "destino_id": params["destino_id"],
                        "destino": self.state["paradas"][params["destino_id"]]["nombre"],
                        "minutos": created["minutos"],
                        "distancia_km": created["distancia_km"],
                    }
                ]
            )

        if "SET c.minutos = coalesce($minutos, c.minutos)" in q:
            existing = next(
                (
                    c
                    for c in self.state["conexiones"]
                    if c["origen_id"] == params["origen_id"]
                    and c["destino_id"] == params["destino_id"]
                ),
                None,
            )
            if existing is None:
                return _StubResult([])
            if params.get("minutos") is not None:
                existing["minutos"] = int(params["minutos"])
            if params.get("distancia_km") is not None:
                existing["distancia_km"] = float(params["distancia_km"])
            return _StubResult(
                [
                    {
                        "origen_id": params["origen_id"],
                        "origen": self.state["paradas"][params["origen_id"]]["nombre"],
                        "destino_id": params["destino_id"],
                        "destino": self.state["paradas"][params["destino_id"]]["nombre"],
                        "minutos": int(existing["minutos"]),
                        "distancia_km": float(existing["distancia_km"]),
                    }
                ]
            )

        if "FOREACH (r IN rels | DELETE r)" in q:
            before = len(self.state["conexiones"])
            self.state["conexiones"] = [
                c
                for c in self.state["conexiones"]
                if not (
                    c["origen_id"] == params["origen_id"]
                    and c["destino_id"] == params["destino_id"]
                )
            ]
            return _StubResult([{"eliminados": before - len(self.state["conexiones"])}])

        if "shortestPath((o)-[:CONECTA_CON*..8]->(d))" in q:
            path = self._bfs_shortest_path(params["origen_id"], params["destino_id"], max_hops=8)
            if not path:
                return _StubResult([])
            return _StubResult(
                [
                    {
                        "desde_id": edge["origen_id"],
                        "desde_nombre": self.state["paradas"][edge["origen_id"]]["nombre"],
                        "hacia_id": edge["destino_id"],
                        "hacia_nombre": self.state["paradas"][edge["destino_id"]]["nombre"],
                        "minutos": int(edge["minutos"]),
                        "distancia_km": float(edge["distancia_km"]),
                    }
                    for edge in path
                ]
            )

        if "MATCH (:Parada {id: $parada_id})-[c:CONECTA_CON]->(d:Parada)" in q:
            rows = []
            for c in self.state["conexiones"]:
                if c["origen_id"] == params["parada_id"]:
                    dest = self.state["paradas"][c["destino_id"]]
                    rows.append(
                        {
                            "destino_id": dest["id"],
                            "destino": dest["nombre"],
                            "zona_destino": dest["zona"],
                            "minutos": int(c["minutos"]),
                            "distancia_km": float(c["distancia_km"]),
                        }
                    )
            rows.sort(key=lambda x: (x["minutos"], x["destino"]))
            return _StubResult(rows)

        if "MATCH (o:Parada {id: $parada_id})-[rels:CONECTA_CON*1..2]->(d:Parada)" in q:
            return _StubResult(self._reachability_1_to_2(params["parada_id"]))

        raise AssertionError(f"Consulta Cypher no manejada en test double: {q}")

    def _bfs_shortest_path(self, start: str, target: str, max_hops: int) -> list[dict[str, Any]] | None:
        queue: deque[tuple[str, list[dict[str, Any]]]] = deque([(start, [])])
        visited = {start}
        while queue:
            node, path = queue.popleft()
            if len(path) >= max_hops:
                continue
            for edge in self.state["conexiones"]:
                if edge["origen_id"] != node:
                    continue
                nxt = edge["destino_id"]
                new_path = [*path, edge]
                if nxt == target:
                    return new_path
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, new_path))
        return None

    def _reachability_1_to_2(self, start: str) -> list[dict[str, Any]]:
        best: dict[str, tuple[int, int]] = {}
        for c1 in self.state["conexiones"]:
            if c1["origen_id"] != start:
                continue
            d1 = c1["destino_id"]
            if d1 != start:
                prev = best.get(d1)
                cand = (1, int(c1["minutos"]))
                if prev is None or cand < prev:
                    best[d1] = cand
            for c2 in self.state["conexiones"]:
                if c2["origen_id"] != d1:
                    continue
                d2 = c2["destino_id"]
                if d2 == start:
                    continue
                mins = int(c1["minutos"]) + int(c2["minutos"])
                prev = best.get(d2)
                if prev is None:
                    best[d2] = (2, mins)
                else:
                    best[d2] = (min(prev[0], 2), min(prev[1], mins))

        rows = []
        for dest_id, (saltos, mins) in best.items():
            dest = self.state["paradas"][dest_id]
            rows.append(
                {
                    "destino_id": dest["id"],
                    "destino": dest["nombre"],
                    "zona_destino": dest["zona"],
                    "saltos": saltos,
                    "minutos_totales": mins,
                }
            )
        rows.sort(key=lambda x: (x["minutos_totales"], x["saltos"], x["destino"]))
        return rows


class InMemoryVaYVenGateway:
    """Gateway en memoria con las paradas y conexiones definidas en scripts/seed.py."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "paradas": {
                "PAR-01": {"id": "PAR-01", "nombre": "Parque La Plancha", "zona": "Centro", "tipo": "Terminal"},
                "PAR-02": {"id": "PAR-02", "nombre": "Paseo 60", "zona": "Centro", "tipo": "Paradero"},
                "PAR-03": {"id": "PAR-03", "nombre": "Tecnológico de Mérida", "zona": "Norte", "tipo": "Paradero"},
                "PAR-04": {"id": "PAR-04", "nombre": "Plaza Altabrisa", "zona": "Noreste", "tipo": "Paradero"},
                "PAR-05": {"id": "PAR-05", "nombre": "Facultad de Ingeniería UADY", "zona": "Norte", "tipo": "Terminal"},
                "PAR-06": {"id": "PAR-06", "nombre": "Francisco de Montejo", "zona": "Poniente", "tipo": "Paradero"},
                "PAR-07": {"id": "PAR-07", "nombre": "Estación Tren Maya Teya", "zona": "Oriente", "tipo": "Terminal"},
                "PAR-08": {"id": "PAR-08", "nombre": "Macroplaza", "zona": "Oriente", "tipo": "Paradero"},
                "PAR-09": {"id": "PAR-09", "nombre": "Nora Quintana", "zona": "Poniente", "tipo": "Base"},
                "PAR-10": {"id": "PAR-10", "nombre": "Chedraui Oriente", "zona": "Oriente", "tipo": "Paradero"},
            },
            "conexiones": [
                {"origen_id": "PAR-02", "destino_id": "PAR-01", "minutos": 8, "distancia_km": 2.1},
                {"origen_id": "PAR-01", "destino_id": "PAR-07", "minutos": 22, "distancia_km": 11.5},
                {"origen_id": "PAR-07", "destino_id": "PAR-01", "minutos": 22, "distancia_km": 11.5},
                {"origen_id": "PAR-01", "destino_id": "PAR-02", "minutos": 8, "distancia_km": 2.1},
                {"origen_id": "PAR-01", "destino_id": "PAR-03", "minutos": 15, "distancia_km": 6.4},
                {"origen_id": "PAR-03", "destino_id": "PAR-05", "minutos": 12, "distancia_km": 5.8},
                {"origen_id": "PAR-05", "destino_id": "PAR-03", "minutos": 12, "distancia_km": 5.8},
                {"origen_id": "PAR-03", "destino_id": "PAR-01", "minutos": 15, "distancia_km": 6.4},
                {"origen_id": "PAR-06", "destino_id": "PAR-03", "minutos": 14, "distancia_km": 5.2},
                {"origen_id": "PAR-03", "destino_id": "PAR-04", "minutos": 16, "distancia_km": 6.1},
                {"origen_id": "PAR-04", "destino_id": "PAR-08", "minutos": 12, "distancia_km": 4.5},
                {"origen_id": "PAR-08", "destino_id": "PAR-10", "minutos": 10, "distancia_km": 3.8},
                {"origen_id": "PAR-10", "destino_id": "PAR-01", "minutos": 15, "distancia_km": 5.0},
                {"origen_id": "PAR-01", "destino_id": "PAR-06", "minutos": 18, "distancia_km": 7.4},
                {"origen_id": "PAR-09", "destino_id": "PAR-06", "minutos": 11, "distancia_km": 4.9},
                {"origen_id": "PAR-06", "destino_id": "PAR-05", "minutos": 15, "distancia_km": 7.1},
                {"origen_id": "PAR-05", "destino_id": "PAR-04", "minutos": 14, "distancia_km": 6.8},
                {"origen_id": "PAR-04", "destino_id": "PAR-07", "minutos": 20, "distancia_km": 10.4},
                {"origen_id": "PAR-07", "destino_id": "PAR-09", "minutos": 24, "distancia_km": 13.0},
            ],
        }

    def health_check(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "configured": True,
            "connected": True,
            "database": "neo4j",
            "issues": [],
            "detail": "Conectado exitosamente a Neo4j AuraDB.",
        }

    def execute_read_tx(self, work):
        return work(InMemoryVaYVenTx(self.state))

    def execute_write_tx(self, work):
        return work(InMemoryVaYVenTx(self.state))

    def read_all(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return [r.data() for r in InMemoryVaYVenTx(self.state).run(query, params)]

    def read_single(self, query: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rec = InMemoryVaYVenTx(self.state).run(query, params).single()
        return rec.data() if rec is not None else None

    def write_single(self, query: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rec = InMemoryVaYVenTx(self.state).run(query, params).single()
        return rec.data() if rec is not None else None

    def close(self) -> None:
        return None


@pytest.fixture()
def client():
    gateway = InMemoryVaYVenGateway()
    app.dependency_overrides[get_db] = lambda: gateway
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ==================== CONFIGURACIÓN Y CONECTIVIDAD ====================


def test_settings_rejects_placeholders_and_invalid_schemes() -> None:
    s1 = Settings(
        neo4j_uri="neo4j+s://xxxxxxxx.databases.neo4j.io",
        neo4j_username="neo4j",
        neo4j_password="tu_password_generado",
    )
    assert s1.is_configured() is False
    with pytest.raises(ConfigValidationError):
        s1.validate()


def test_unconfigured_connection_returns_503_and_health_unconfigured() -> None:
    unconfigured = Neo4jConnection(
        settings_provider=lambda: Settings(
            neo4j_uri="neo4j+s://xxxxxxxx.databases.neo4j.io",
            neo4j_username="neo4j",
            neo4j_password="tu_password_generado",
        )
    )
    app.dependency_overrides[get_db] = lambda: unconfigured
    try:
        with TestClient(app) as c:
            assert c.get("/api/health").json()["status"] == "unconfigured"
            assert c.get("/api/paradas").status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_static_index_served_at_root(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Va y Ven" in resp.text


# ==================== CRUD PARADAS Y CONEXIONES ====================


def test_crud_paradas_y_conexiones(client: TestClient) -> None:
    red = client.get("/api/red")
    assert red.status_code == 200
    assert len(red.json()["paradas"]) == 10
    assert len(red.json()["conexiones"]) == 19

    create = client.post(
        "/api/paradas",
        json={"nombre": " Parque San Juan ", "zona": "Centro", "tipo": "Paradero"},
    )
    assert create.status_code == 201
    assert create.json()["parada"]["id"] == "PAR-11"

    create2 = client.post(
        "/api/paradas",
        json={"nombre": "Parque Mejorada", "zona": "Centro", "tipo": "Paradero"},
    )
    assert create2.status_code == 201
    assert create2.json()["parada"]["id"] == "PAR-12"
    assert client.delete("/api/paradas/PAR-12").status_code == 200

    assert client.post(
        "/api/paradas",
        json={"id": "par-11", "nombre": "Duplicada", "zona": "Centro", "tipo": "Paradero"},
    ).status_code == 409

    upd = client.put("/api/paradas/par-11", json={"nombre": "Parque San Juan Centro"})
    assert upd.status_code == 200
    assert upd.json()["parada"]["nombre"] == "Parque San Juan Centro"

    con = client.post(
        "/api/conexiones",
        json={
            "origen_id": "par-11",
            "destino_id": "par-01",
            "minutos": 12,
            "distancia_km": 3.456,
        },
    )
    assert con.status_code == 201
    assert con.json()["distancia_km"] == 3.46

    dup_con = client.post(
        "/api/conexiones",
        json={
            "origen_id": "par-11",
            "destino_id": "par-01",
            "minutos": 20,
            "distancia_km": 5.0,
        },
    )
    assert dup_con.status_code == 409

    upd_con = client.put(
        "/api/conexiones/par-11/par-01",
        json={"minutos": 15},
    )
    assert upd_con.status_code == 200
    assert upd_con.json()["minutos"] == 15
    assert upd_con.json()["distancia_km"] == 3.46
    assert client.put("/api/conexiones/PAR-11/PAR-02", json={"minutos": 10}).status_code == 404

    del_con = client.delete("/api/conexiones/PAR-11/PAR-01")
    assert del_con.status_code == 200
    assert del_con.json()["eliminados"] == 1
    assert client.delete("/api/conexiones/PAR-11/PAR-01").status_code == 404

    assert client.delete("/api/paradas/PAR-11").status_code == 200
    assert client.delete("/api/paradas/PAR-11").status_code == 404


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"id": "   ", "nombre": "Parada", "zona": "Centro", "tipo": "Paradero"},
        {"id": "PAR 11!", "nombre": "Parada", "zona": "Centro", "tipo": "Paradero"},
        {"id": "PAR-11", "nombre": "P", "zona": "Centro", "tipo": "Paradero"},
        {"id": "PAR-11", "nombre": "Parada", "zona": "Centro", "tipo": "Base"},
        {"id": "PAR-11", "nombre": "Parada", "zona": "Centro", "tipo": "Paradero", "extra": 1},
    ],
)
def test_crear_parada_rechaza_entradas_invalidas(client: TestClient, bad_payload: dict[str, Any]) -> None:
    assert client.post("/api/paradas", json=bad_payload).status_code == 422


def test_crear_conexion_rechaza_auto_bucle_y_booleano(client: TestClient) -> None:
    assert (
        client.post(
            "/api/conexiones",
            json={
                "origen_id": "PAR-01",
                "destino_id": "PAR-01",
                "minutos": 5,
                "distancia_km": 1.0,
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/conexiones",
            json={
                "origen_id": "PAR-01",
                "destino_id": "PAR-02",
                "minutos": True,
                "distancia_km": 1.0,
            },
        ).status_code
        == 422
    )


# ==================== CONSULTAS ESPECIALIZADAS DE GRAFO ====================


def test_tarifa_vayven() -> None:
    assert _tarifa_vayven("general") == 12.0
    assert _tarifa_vayven("estudiante") == 5.0


def test_consulta_a_planear_viaje_shortest_path(client: TestClient) -> None:
    directo = client.get("/api/viaje/par-02/par-07?tipo_tarifa=general")
    assert directo.status_code == 200
    d_json = directo.json()
    assert d_json["costo_total"] == 12.0
    assert d_json["minutos_totales"] == 30
    assert d_json["numero_paradas"] == 3

    est = client.get("/api/viaje/PAR-02/PAR-04?tipo_tarifa=estudiante")
    assert est.status_code == 200
    e_json = est.json()
    assert e_json["costo_total"] == 5.0
    assert len(e_json["tramos"]) >= 2

    mismo = client.get("/api/viaje/PAR-01/PAR-01")
    assert mismo.status_code == 200
    assert mismo.json()["minutos_totales"] == 0
    assert mismo.json()["costo_total"] == 0.0

    assert client.get("/api/viaje/PAR-99/PAR-04").status_code == 404


def test_consulta_b_salidas_y_consulta_c_alcance(client: TestClient) -> None:
    salidas = client.get("/api/paradas/PAR-01/salidas")
    assert salidas.status_code == 200
    assert len(salidas.json()) == 4

    alcance = client.get("/api/paradas/PAR-01/alcance")
    assert alcance.status_code == 200
    destinos = {item["destino_id"]: item for item in alcance.json()}
    assert "PAR-07" in destinos
    assert destinos["PAR-07"]["saltos"] == 1
    assert destinos["PAR-05"]["saltos"] == 2

    assert client.get("/api/paradas/NO-EXISTE/alcance").status_code == 404


# ==================== SCRIPT DE POBLADO ====================


def test_seed_script_translates_driver_errors_during_constraints(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from neo4j import exceptions

    class _FailingSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def run(self, _stmt: str):
            raise exceptions.AuthError("invalid credentials")

    class _FailingDriver:
        def session(self, **_kwargs):
            return _FailingSession()

        def close(self) -> None:
            return None

    valid_settings = Settings(
        neo4j_uri="neo4j+s://a1b2c3d4.databases.neo4j.io",
        neo4j_username="a1b2c3d4",
        neo4j_password="ValidPassword_2026!",
        neo4j_database="a1b2c3d4",
    )
    conn = Neo4jConnection(settings_provider=lambda: valid_settings)
    monkeypatch.setattr(conn, "_get_driver", lambda: (_FailingDriver(), valid_settings))
    monkeypatch.setattr(seed_script, "db", conn)
    monkeypatch.setattr(seed_script, "get_settings", lambda: valid_settings)

    exit_code = seed_script.main()
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[ERROR DE CONEXIÓN]" in captured.err
