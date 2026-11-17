# Red de Transporte "Va y Ven" (Neo4j AuraDB + FastAPI)

Mapa interactivo de paradas, conexiones y cálculo de trayectos para la red **Va y Ven** (Mérida, Yucatán), construido con **Neo4j AuraDB** y **FastAPI** bajo los principios **KISS, DRY, SOLID y YAGNI**.

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
│   └── index.html         # Mapa interactivo visual para viajar y administrar el grafo
├── tests/
│   ├── __init__.py
│   └── test_api.py        # Suite de pruebas automatizadas con pytest
├── .env.example
├── .env                   # Credenciales reales locales (ignorado por Git)
├── .gitignore
├── requirements.txt
└── README.md
```

## Modelo de Grafo en Neo4j

- **Nodos**:
  - `(:Parada {id, nombre, zona, tipo})`
- **Relaciones**:
  - `(:Parada)-[:CONECTA_CON {minutos, distancia_km}]->(:Parada)`

## Instalación y puesta en marcha

1. Activa el entorno virtual e instala dependencias:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Configura `.env` con las credenciales reales de tu instancia AuraDB (`NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`).

3. Pobla la base de datos en Neo4j AuraDB:
   ```bash
   python -m scripts.seed
   ```

4. Inicia el servidor:
   ```bash
   uvicorn app.main:app --reload
   ```

5. Abre en el navegador:
   - Aplicación para pasajeros: `http://127.0.0.1:8000/`
   - Documentación interactiva Swagger UI: `http://127.0.0.1:8000/docs`

## Pruebas automatizadas

```bash
pytest -v
```
