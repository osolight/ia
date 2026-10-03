"""
pg-ai-tools backend seguro
- Consultas PostgreSQL solo lectura
- Auditoría backend JSONL
- Endpoints de auditoría compacta
"""

import os
import json
import re
import logging
import hashlib
from uuid import uuid4
from datetime import datetime, timezone
from typing import Any, Optional
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@postgres:5432/postgres",
)

AUDIT_FILE = os.getenv("AUDIT_FILE", "/app/audit/pg_tools_audit.jsonl")
STATEMENT_TIMEOUT_MS = int(os.getenv("STATEMENT_TIMEOUT_MS", "60000"))
MAX_LIMIT = int(os.getenv("MAX_LIMIT", "500"))
PG_TOOLS_API_KEY = os.getenv("PG_TOOLS_API_KEY", "").strip()
API_KEY_HEADER = os.getenv("API_KEY_HEADER", "X-API-Key")

pool: Optional[asyncpg.Pool] = None


# ─────────────────────────────────────────────────────────────────────────────
# API Key
# ─────────────────────────────────────────────────────────────────────────────

def require_api_key(request: Request) -> None:
    """
    Protege endpoints de datos. /health queda libre para healthcheck.
    Si PG_TOOLS_API_KEY está vacío, se permite acceso para no romper despliegues.
    Recomendado: definir PG_TOOLS_API_KEY en .env.
    """
    if not PG_TOOLS_API_KEY:
        return

    received = request.headers.get(API_KEY_HEADER)
    if not received or received != PG_TOOLS_API_KEY:
        try:
            audit_write({
                "action": "auth",
                "status": "denied",
                "client_host": client_host(request),
                "error": "API key inválida o ausente.",
            })
        except Exception:
            pass
        raise HTTPException(status_code=401, detail="API key inválida o ausente.")


# ─────────────────────────────────────────────────────────────────────────────
# Modelos
# ─────────────────────────────────────────────────────────────────────────────

class SQLRequest(BaseModel):
    query: str
    limit: int = Field(default=100, ge=1, le=500)


class TableRequest(BaseModel):
    schema_name: str = "public"
    table_name: str


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades auditoría
# ─────────────────────────────────────────────────────────────────────────────

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_audit_dir() -> None:
    audit_dir = os.path.dirname(AUDIT_FILE)
    if audit_dir:
        os.makedirs(audit_dir, exist_ok=True)


def json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def make_hash(payload: dict) -> str:
    payload_for_hash = dict(payload)
    payload_for_hash.pop("payload_sha256", None)
    raw = json.dumps(payload_for_hash, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def audit_write(payload: dict) -> dict:
    ensure_audit_dir()

    payload.setdefault("backend_version", "pg_tools_backend_v3_apikey")
    payload.setdefault("generated_at_utc", utc_now())
    payload.setdefault("run_id", str(uuid4()))

    payload["payload_sha256"] = make_hash(payload)

    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    return payload


def compact_audit_record(record: dict) -> dict:
    compact = {
        "run_id": record.get("run_id"),
        "generated_at_utc": record.get("generated_at_utc"),
        "backend_version": record.get("backend_version"),
        "action": record.get("action"),
        "status": record.get("status"),
        "client_host": record.get("client_host"),
        "payload_sha256": record.get("payload_sha256"),
    }

    for key in (
        "schema_name",
        "table_name",
        "sql_requested",
        "sql_executed",
        "columns",
        "row_count_returned",
        "error",
        "duration_ms",
    ):
        if key in record:
            compact[key] = record.get(key)

    rows = record.get("rows")
    if isinstance(rows, list):
        compact["rows_saved_in_audit"] = len(rows)
        compact["rows_preview"] = rows[:2]

    return compact


def audit_last(limit: int = 5) -> dict:
    limit = max(1, min(int(limit), 50))

    if not os.path.exists(AUDIT_FILE):
        return {
            "status": "ok",
            "audit_file": AUDIT_FILE,
            "records_returned": 0,
            "records": [],
        }

    with open(AUDIT_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()[-limit:]

    records = []
    for line in lines:
        try:
            records.append(compact_audit_record(json.loads(line)))
        except Exception:
            records.append({"raw_preview": line[:500], "warning": "Registro no parseable."})

    return {
        "status": "ok",
        "audit_file": AUDIT_FILE,
        "records_returned": len(records),
        "mode": "compact",
        "records": records,
    }


def audit_find(run_id: str) -> dict:
    if not os.path.exists(AUDIT_FILE):
        return {
            "status": "not_found",
            "audit_file": AUDIT_FILE,
            "run_id": run_id,
        }

    with open(AUDIT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line)
            except Exception:
                continue
            if record.get("run_id") == run_id:
                return {
                    "status": "ok",
                    "audit_file": AUDIT_FILE,
                    "run_id": run_id,
                    "mode": "compact",
                    "record": compact_audit_record(record),
                }

    return {
        "status": "not_found",
        "audit_file": AUDIT_FILE,
        "run_id": run_id,
    }


def client_host(request: Request) -> Optional[str]:
    try:
        return request.client.host
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Seguridad SQL
# ─────────────────────────────────────────────────────────────────────────────

def clean_sql(sql: str) -> str:
    sql = str(sql or "").strip()
    while sql.endswith(";"):
        sql = sql[:-1].strip()
    return sql


def first_sql_word(sql: str) -> str:
    sql = sql.strip()
    return sql.split(None, 1)[0].upper() if sql else ""


def validate_schema_or_table_name(value: str, label: str) -> None:
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", value or ""):
        raise HTTPException(status_code=400, detail=f"{label} inválido.")


def validate_readonly_sql(sql: str) -> None:
    first = first_sql_word(sql)
    if first not in ("SELECT", "WITH", "EXPLAIN"):
        raise HTTPException(
            status_code=400,
            detail="Solo se permiten consultas SELECT, WITH o EXPLAIN.",
        )

    low = sql.lower()

    forbidden_patterns = [
        r"\binsert\b",
        r"\bupdate\b",
        r"\bdelete\b",
        r"\bdrop\b",
        r"\balter\b",
        r"\btruncate\b",
        r"\bcreate\b",
        r"\bgrant\b",
        r"\brevoke\b",
        r"\bcopy\b",
        r"\bvacuum\b",
        r"\banalyze\b",
        r"\breindex\b",
        r"\brefresh\s+materialized\s+view\b",
        r"\bcall\b",
        r"\bdo\s+\$\$",
        r"\bexecute\b",
        r"\bpg_sleep\s*\(",
        r"\bdblink\b",
        r"\bpostgres_fdw\b",
    ]

    for pattern in forbidden_patterns:
        if re.search(pattern, low, re.IGNORECASE):
            raise HTTPException(status_code=400, detail=f"SQL bloqueado: {pattern}")

    if ";" in sql.strip().rstrip(";"):
        raise HTTPException(status_code=400, detail="Múltiples sentencias SQL bloqueadas.")


def apply_limit(sql: str, limit: int) -> str:
    sql = clean_sql(sql)
    first = first_sql_word(sql)

    safe_limit = max(1, min(int(limit or 100), MAX_LIMIT))

    if first == "SELECT" and "LIMIT" not in sql.upper():
        return f"{sql} LIMIT {safe_limit}"

    return sql


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool

    # No imprimir DATABASE_URL completo porque puede contener clave.
    logger.info("Conectando a Postgres con DATABASE_URL configurado.")
    ensure_audit_dir()

    async def init_connection(conn: asyncpg.Connection):
        await conn.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
        await conn.execute("SET default_transaction_read_only = on")
        await conn.execute("SET idle_in_transaction_session_timeout = 30000")

    pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        init=init_connection,
    )

    logger.info("Pool de conexiones listo.")
    yield

    await pool.close()
    logger.info("Pool cerrado.")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="pg-ai-tools",
    description="Herramientas seguras de consulta PostgreSQL para Open WebUI",
    version="3.0.0-apikey",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "backend_version": "pg_tools_backend_v3_apikey",
        "audit_file": AUDIT_FILE,
        "statement_timeout_ms": STATEMENT_TIMEOUT_MS,
        "max_limit": MAX_LIMIT,
        "api_key_enabled": bool(PG_TOOLS_API_KEY),
        "api_key_header": API_KEY_HEADER,
    }


@app.get("/schemas")
async def list_schemas(request: Request):
    require_api_key(request)
    started = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
              AND schema_name NOT LIKE 'pg_temp_%'
              AND schema_name NOT LIKE 'pg_toast_temp_%'
            ORDER BY schema_name
        """)

    result = {"schemas": [r["schema_name"] for r in rows]}

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    audit_write({
        "action": "list_schemas",
        "status": "ok",
        "client_host": client_host(request),
        "duration_ms": duration_ms,
        "schemas_returned": len(result["schemas"]),
    })

    return result


@app.get("/schema")
async def get_schema(request: Request, schema_name: str = "public"):
    require_api_key(request)
    validate_schema_or_table_name(schema_name, "schema_name")
    started = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        # Query ligero. Evita joins pesados de constraints y FK en Odoo grande.
        columns = await conn.fetch("""
            SELECT
                c.table_name,
                c.column_name,
                c.data_type,
                c.is_nullable,
                c.column_default,
                NULL::text AS constraint_type
            FROM information_schema.columns c
            WHERE c.table_schema = $1
            ORDER BY c.table_name, c.ordinal_position
        """, schema_name)

        counts = await conn.fetch("""
            SELECT relname, reltuples::bigint AS estimate
            FROM pg_class
            WHERE relkind = 'r'
              AND relnamespace = (
                SELECT oid FROM pg_namespace WHERE nspname = $1
              )
        """, schema_name)

    row_counts = {r["relname"]: r["estimate"] for r in counts}

    schema: dict[str, Any] = {}
    for row in columns:
        table_name = row["table_name"]
        if table_name not in schema:
            schema[table_name] = {
                "columns": [],
                "row_count": row_counts.get(table_name),
                "foreign_keys": [],
            }

        schema[table_name]["columns"].append({
            "name": row["column_name"],
            "type": row["data_type"],
            "nullable": row["is_nullable"] == "YES",
            "default": row["column_default"],
            "constraint": row["constraint_type"],
        })

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    audit_write({
        "action": "get_schema",
        "status": "ok",
        "client_host": client_host(request),
        "schema_name": schema_name,
        "duration_ms": duration_ms,
        "tables_returned": len(schema),
    })

    return {"schema_name": schema_name, "tables": schema}


@app.post("/query")
async def run_query(req: SQLRequest, request: Request):
    require_api_key(request)
    started = datetime.now(timezone.utc)
    sql_requested = req.query
    sql = clean_sql(req.query)

    validate_readonly_sql(sql)
    sql_executed = apply_limit(sql, req.limit)

    try:
        async with pool.acquire() as conn:
            # Doble seguro: aunque el rol sea read-only, marcamos transacción read-only.
            async with conn.transaction(readonly=True):
                rows = await conn.fetch(sql_executed)

    except asyncpg.PostgresError as e:
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        audit_write({
            "action": "run_query",
            "status": "error",
            "client_host": client_host(request),
            "sql_requested": sql_requested,
            "sql_executed": sql_executed,
            "duration_ms": duration_ms,
            "error": str(e),
        })
        raise HTTPException(status_code=400, detail=str(e))

    columns = list(rows[0].keys()) if rows else []
    data = [dict(r) for r in rows]
    data = [{k: json_safe(v) for k, v in row.items()} for row in data]

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    audit_record = audit_write({
        "action": "run_query",
        "status": "ok",
        "client_host": client_host(request),
        "sql_requested": sql_requested,
        "sql_executed": sql_executed,
        "columns": columns,
        "rows": data,
        "row_count_returned": len(data),
        "duration_ms": duration_ms,
    })

    return {
        "columns": columns,
        "rows": data,
        "count": len(data),
        "sql": sql_executed,
        "backend_run_id": audit_record["run_id"],
        "backend_payload_sha256": audit_record["payload_sha256"],
        "backend_generated_at_utc": audit_record["generated_at_utc"],
    }


@app.get("/sample/{table_name}")
async def sample_table(
    table_name: str,
    request: Request,
    schema_name: str = "public",
    limit: int = 10,
):
    require_api_key(request)
    validate_schema_or_table_name(schema_name, "schema_name")
    validate_schema_or_table_name(table_name, "table_name")

    safe_limit = max(1, min(int(limit or 10), 50))
    sql = f'SELECT * FROM "{schema_name}"."{table_name}" LIMIT {safe_limit}'

    req = SQLRequest(query=sql, limit=safe_limit)
    return await run_query(req, request)


@app.get("/stats/{table_name}")
async def table_stats(
    table_name: str,
    request: Request,
    schema_name: str = "public",
):
    require_api_key(request)
    validate_schema_or_table_name(schema_name, "schema_name")
    validate_schema_or_table_name(table_name, "table_name")

    started = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        cols = await conn.fetch("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
              AND data_type IN (
                'integer','bigint','numeric','real','double precision',
                'smallint','decimal','money'
              )
        """, schema_name, table_name)

        if not cols:
            return {"message": "No hay columnas numéricas en esta tabla."}

        stats = {}
        async with conn.transaction(readonly=True):
            for col in cols:
                col_name = col["column_name"]

                # column_name viene desde information_schema, no desde usuario directo.
                row = await conn.fetchrow(f"""
                    SELECT
                        MIN("{col_name}") AS min,
                        MAX("{col_name}") AS max,
                        AVG("{col_name}") AS avg,
                        COUNT(*) FILTER (WHERE "{col_name}" IS NULL) AS nulls,
                        COUNT(*) AS total
                    FROM "{schema_name}"."{table_name}"
                """)

                stats[col_name] = {k: json_safe(v) for k, v in dict(row).items()}

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    audit_write({
        "action": "table_stats",
        "status": "ok",
        "client_host": client_host(request),
        "schema_name": schema_name,
        "table_name": table_name,
        "duration_ms": duration_ms,
        "numeric_columns": len(stats),
    })

    return {"table": table_name, "numeric_stats": stats}


@app.get("/audit/last")
async def backend_audit_last(request: Request, limit: int = 5):
    require_api_key(request)
    return audit_last(limit)


@app.get("/audit/{run_id}")
async def backend_audit_find(run_id: str, request: Request):
    require_api_key(request)
    return audit_find(run_id)
