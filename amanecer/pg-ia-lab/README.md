# pg-ia-lab 🧠🐘

Chat con IA (Ollama + qwen) que consulta PostgreSQL directamente.

## Arquitectura

```
Open WebUI (chat)
     ↓ llama tool calls
  pg-tools (FastAPI :8100)
     ↓ asyncpg
  PostgreSQL
```

## Inicio rápido

### 1. Configura el entorno

Edita `.env` con los datos de tu Postgres:

```bash
cp .env .env.local   # opcional, para no tocar .env
nano .env
```

### 2. Si ya tienes Ollama y Open WebUI corriendo

Comenta los servicios `ollama` y `open-webui` en `docker-compose.yml`
y asegúrate de que están en la misma red Docker (`ai-lab`) o ajusta
la URL en las variables de entorno.

Si tu Postgres está fuera de Docker, comenta el servicio `postgres`
y pon en `.env`:
```
POSTGRES_HOST=192.168.1.X   # IP de tu servidor Postgres
```

### 3. Levanta el stack

```bash
docker compose up -d --build
```

Verifica que `pg-tools` está funcionando:
```bash
curl http://localhost:8100/health
# → {"status":"ok"}

curl http://localhost:8100/schemas
# → {"schemas":["public", ...]}

curl "http://localhost:8100/schema?schema_name=public"
# → esquema completo con tablas, columnas, FKs y row counts
```

## Configurar la Tool en Open WebUI

### Opción A: Tool nativa (recomendada)

1. Entra en Open WebUI → **Workspace → Tools → + New Tool**
2. Pega el código de `openwebui-tool.py` (ver abajo)
3. Guarda y asigna la tool al modelo qwen2.5 o qwen3

### Opción B: System prompt con API externa

En el System Prompt del modelo pon:

```
Tienes acceso a una API REST que expone una base de datos PostgreSQL.
Base URL: http://pg-tools:8100

Endpoints disponibles:
- GET /schema?schema_name=public → esquema completo (tablas, columnas, tipos, FKs, row counts)
- POST /query → ejecuta SQL SELECT. Body: {"query": "SELECT ...", "limit": 500}
- GET /sample/{table_name} → muestra 10 filas de una tabla
- GET /stats/{table_name} → estadísticas de columnas numéricas

Flujo para responder preguntas sobre datos:
1. Consulta /schema para entender la estructura
2. Genera la SQL adecuada
3. Llama a /query con esa SQL
4. Presenta los resultados de forma clara, con tablas o resúmenes

Usa siempre comillas dobles para nombres de tabla y columna en el SQL.
Solo puedes ejecutar SELECT, WITH y EXPLAIN. Nunca INSERT/UPDATE/DELETE.
```

## Endpoints de pg-tools

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/health` | Estado del servicio |
| GET | `/schemas` | Lista de schemas |
| GET | `/schema` | Esquema completo (tablas, columnas, FKs) |
| POST | `/query` | Ejecuta SQL SELECT |
| GET | `/sample/{table}` | Muestra N filas de una tabla |
| GET | `/stats/{table}` | Stats numéricas (min/max/avg/nulls) |

Documentación interactiva: http://localhost:8100/docs

## Seguridad

- `pg-tools` solo permite SELECT, WITH y EXPLAIN. Bloquea INSERT/UPDATE/DELETE/DROP.
- En producción, crea un usuario Postgres de solo lectura:

```sql
CREATE USER ai_readonly WITH PASSWORD 'password-seguro';
GRANT CONNECT ON DATABASE tu_db TO ai_readonly;
GRANT USAGE ON SCHEMA public TO ai_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ai_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ai_readonly;
```

Luego actualiza `DATABASE_URL` en `.env` con ese usuario.
