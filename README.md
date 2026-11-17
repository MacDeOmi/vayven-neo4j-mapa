# Red de Transporte "Va y Ven" (Neo4j AuraDB + FastAPI)

Mapa interactivo de paradas, conexiones dirigidas y cálculo de trayectos óptimos para la red **Va y Ven** (Mérida, Yucatán), conectado en tiempo real a **Neo4j AuraDB** (Cloud) con **FastAPI** bajo los principios **KISS, DRY, SOLID y YAGNI**.

## Requerimientos

- **Python:** 3.11 o superior (probado en Python 3.14).
- **Base de datos en la nube:** Cuenta e instancia activa de **Neo4j AuraDB** (Free o Professional) con URI `neo4j+s://...`.
- **Conectividad:** Acceso a internet con salida al puerto `7687` (protocolo Bolt sobre TLS).
- **Dependencias Python** (definidas en `requirements.txt`):
  - `fastapi`, `uvicorn` (API REST y servidor ASGI)
  - `neo4j` (driver oficial de Neo4j para Python)
  - `pydantic`, `python-dotenv`, `certifi`
  - `httpx`, `pytest` (suite de pruebas automatizadas)

## Estructura del proyecto

```text
├── app/
│   ├── __init__.py
│   ├── config.py          # Lectura y validación explícita de variables de entorno (.env)
│   ├── database.py        # Driver oficial de Neo4j (Singleton) + transacciones gestionadas
│   ├── models.py          # Esquemas Pydantic v2 con validación estricta
│   └── main.py            # Endpoints FastAPI (CRUD Paradas/Conexiones + Consultas de Grafo)
├── scripts/
│   ├── __init__.py
│   └── seed.py            # Restricciones únicas + poblado atómico de la red Va y Ven
├── static/
│   └── index.html         # Mapa interactivo visual (consulta de trayectos y CRUD directo)
├── tests/
│   ├── __init__.py
│   └── test_api.py        # Suite de pruebas automatizadas con pytest
├── .env.example           # Plantilla de variables de entorno
├── .env                   # Credenciales reales locales (ignorado por Git)
├── .gitignore
├── requirements.txt
└── README.md
```

## Modelo de Grafo en Neo4j

- **Nodos**:
  - `(:Parada {id, nombre, zona, tipo})` (`id` único con restricción `parada_id_unique`, `tipo`: `Terminal` o `Paradero`).
- **Relaciones**:
  - `(:Parada)-[:CONECTA_CON {minutos, distancia_km}]->(:Parada)` (relación dirigida; sin auto-bucles ni duplicados en el mismo sentido).

## Guía de instalación

1. Clona el repositorio y entra a la carpeta del proyecto:
   ```bash
   git clone https://github.com/MacDeOmi/vayven-neo4j-mapa.git
   cd vayven-neo4j-mapa
   ```

2. Crea y activa el entorno virtual e instala las dependencias:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Crea tu archivo `.env` a partir de la plantilla y coloca las credenciales de tu instancia **Neo4j AuraDB**:
   ```bash
   cp .env.example .env
   ```
   Edita `.env` con tus valores reales (`NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`).

## Pasos para ejecutar la aplicación

1. **Poblar la base de datos en la nube** (crea la restricción única e inserta 10 paradas y 19 conexiones iniciales):
   ```bash
   python -m scripts.seed
   ```

2. **Iniciar el servidor FastAPI**:
   ```bash
   uvicorn app.main:app --reload
   ```
   *(Para abrir desde otro dispositivo en la misma red Wi-Fi, usa `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`).*

3. **Abrir la aplicación**:
   - Mapa interactivo (consulta de rutas y CRUD): `http://127.0.0.1:8000/`
   - Documentación interactiva Swagger UI: `http://127.0.0.1:8000/docs`
   - Estado de conexión con AuraDB: `http://127.0.0.1:8000/api/health`

## Endpoints principales

- **CRUD Paradas:** `GET /api/paradas`, `GET /api/paradas/{parada_id}`, `POST /api/paradas`, `PUT /api/paradas/{parada_id}`, `DELETE /api/paradas/{parada_id}`
- **CRUD Conexiones:** `GET /api/red`, `POST /api/conexiones`, `PUT /api/conexiones/{origen_id}/{destino_id}`, `DELETE /api/conexiones/{origen_id}/{destino_id}`
- **Consultas de Grafo:**
  - `GET /api/viaje/{origen_id}/{destino_id}?tipo_tarifa=general|estudiante` (`shortestPath`)
  - `GET /api/paradas/{parada_id}/salidas` (vecindad directa a 1 salto)
  - `GET /api/paradas/{parada_id}/alcance` (traversal multi-salto `1..2`)

## Pruebas automatizadas

```bash
pytest -v
```
