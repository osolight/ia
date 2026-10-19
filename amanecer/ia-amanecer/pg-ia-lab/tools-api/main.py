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
import csv
import io
import secrets
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Literal
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
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
EXPORT_MAX_LIMIT = int(os.getenv("EXPORT_MAX_LIMIT", "5000"))
DOWNLOAD_TOKEN_TTL_SECONDS = int(os.getenv("DOWNLOAD_TOKEN_TTL_SECONDS", "300"))
DOWNLOAD_TOKENS: dict[str, dict] = {}

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

    payload.setdefault("backend_version", "pg_tools_backend_v3_2_download_token")
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
    version="3.2.0-download-token",
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
        "backend_version": "pg_tools_backend_v3_2_download_token",
        "audit_file": AUDIT_FILE,
        "statement_timeout_ms": STATEMENT_TIMEOUT_MS,
        "max_limit": MAX_LIMIT,
        "api_key_enabled": bool(PG_TOOLS_API_KEY),
        "api_key_header": API_KEY_HEADER,
        "export_max_limit": EXPORT_MAX_LIMIT,
        "download_token_ttl_seconds": DOWNLOAD_TOKEN_TTL_SECONDS,
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

# ─────────────────────────────────────────────────────────────────────────────
# Herramientas certificadas de negocio - Ventas
# Implementación separada en sales_tools.py
# ─────────────────────────────────────────────────────────────────────────────

from sales_tools import register_sales_routes

register_sales_routes(
    app=app,
    pool_getter=lambda: pool,
    require_api_key=require_api_key,
    audit_write=audit_write,
    client_host=client_host,
)

# ─────────────────────────────────────────────────────────────────────────────
# Visualizaciones certificadas de ventas
# ─────────────────────────────────────────────────────────────────────────────

from sales_charts import register_sales_chart_routes

register_sales_chart_routes(
    app=app,
    pool_getter=lambda: pool,
    require_api_key=require_api_key,
    audit_write=audit_write,
    client_host=client_host,
)

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



# ─────────────────────────────────────────────────────────────────────────────
# Exportaciones CSV seguras
# ─────────────────────────────────────────────────────────────────────────────

def validate_date_yyyy_mm_dd(value: str, label: str) -> None:
    if not re.match(r"^20\d{2}-\d{2}-\d{2}$", value or ""):
        raise HTTPException(status_code=400, detail=f"{label} inválida. Usa YYYY-MM-DD.")


def csv_response(filename: str, columns: list[str], rows: list[dict], audit_record: dict) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: json_safe(row.get(col)) for col in columns})

    content = output.getvalue()
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Backend-Run-Id": audit_record.get("run_id", ""),
        "X-Backend-Payload-Sha256": audit_record.get("payload_sha256", ""),
        "X-Backend-Generated-At-Utc": audit_record.get("generated_at_utc", ""),
        "X-Row-Count": str(len(rows)),
    }

    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )



@app.get("/export/clients")
async def export_clients(
    request: Request,
    date_from: str,
    date_to: str,
    limit: int = 500,
):
    require_api_key(request)
    return await build_clients_export_response(request, date_from=date_from, date_to=date_to, limit=limit)


@app.get("/export/overdue-customers")
async def export_overdue_customers(
    request: Request,
    cutoff_date: Optional[str] = None,
    limit: int = 500,
):
    require_api_key(request)
    return await build_overdue_customers_export_response(request, cutoff_date=cutoff_date, limit=limit)


def cleanup_download_tokens() -> None:
    now = datetime.now(timezone.utc)
    expired = [
        token for token, info in DOWNLOAD_TOKENS.items()
        if info.get("expires_at") and info["expires_at"] < now
    ]
    for token in expired:
        DOWNLOAD_TOKENS.pop(token, None)


def create_download_token(export_type: str, params: dict, created_by: Optional[str] = None) -> dict:
    cleanup_download_tokens()
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=DOWNLOAD_TOKEN_TTL_SECONDS)

    DOWNLOAD_TOKENS[token] = {
        "token": token,
        "export_type": export_type,
        "params": params,
        "created_at": now,
        "expires_at": expires_at,
        "created_by": created_by,
        "used": False,
    }

    return {
        "token": token,
        "export_type": export_type,
        "expires_at": expires_at.isoformat(),
        "ttl_seconds": DOWNLOAD_TOKEN_TTL_SECONDS,
        "download_url": f"/export/download/{token}",
    }


def consume_download_token(token: str) -> dict:
    cleanup_download_tokens()
    info = DOWNLOAD_TOKENS.get(token)
    if not info:
        raise HTTPException(status_code=404, detail="Token de descarga inválido o expirado.")

    now = datetime.now(timezone.utc)
    if info.get("expires_at") and info["expires_at"] < now:
        DOWNLOAD_TOKENS.pop(token, None)
        raise HTTPException(status_code=404, detail="Token de descarga expirado.")

    info["used"] = True
    info["used_at"] = now
    return info


async def build_clients_export_response(
    request: Request,
    date_from: str,
    date_to: str,
    limit: int = 500,
) -> StreamingResponse:
    validate_date_yyyy_mm_dd(date_from, "date_from")
    validate_date_yyyy_mm_dd(date_to, "date_to")

    safe_limit = max(1, min(int(limit or 500), EXPORT_MAX_LIMIT))
    started = datetime.now(timezone.utc)

    sql_executed = """
SELECT
    rp.id,
    rp.name,
    rp.vat,
    rp.email,
    rp.phone,
    rp.mobile,
    rp.city,
    rp.create_date
FROM res_partner rp
WHERE rp.create_date >= $1::timestamp
  AND rp.create_date < $2::timestamp
ORDER BY rp.create_date DESC
LIMIT $3
"""

    date_from_dt = datetime.fromisoformat(f"{date_from} 00:00:00")
    date_to_dt = datetime.fromisoformat(f"{date_to} 00:00:00")

    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            rows = await conn.fetch(sql_executed, date_from_dt, date_to_dt, safe_limit)

    columns = ["id", "name", "vat", "email", "phone", "mobile", "city", "create_date"]
    data = [dict(r) for r in rows]
    data = [{k: json_safe(v) for k, v in row.items()} for row in data]

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    filename = f"clientes_creados_{date_from}_a_{date_to}.csv"

    audit_record = audit_write({
        "action": "export_clients",
        "status": "ok",
        "client_host": client_host(request),
        "date_from": date_from,
        "date_to": date_to,
        "limit": safe_limit,
        "filename": filename,
        "sql_requested": "EXPORT_CLIENTS",
        "sql_executed": sql_executed,
        "columns": columns,
        "row_count_returned": len(data),
        "rows": data[:20],
        "rows_saved_note": "Solo se guardan hasta 20 filas de muestra en auditoría; el CSV puede contener más.",
        "duration_ms": duration_ms,
    })

    return csv_response(filename, columns, data, audit_record)


async def build_overdue_customers_export_response(
    request: Request,
    cutoff_date: Optional[str] = None,
    limit: int = 500,
) -> StreamingResponse:
    if cutoff_date:
        validate_date_yyyy_mm_dd(cutoff_date, "cutoff_date")

    safe_limit = max(1, min(int(limit or 500), EXPORT_MAX_LIMIT))
    started = datetime.now(timezone.utc)

    cutoff_sql = "$1::date" if cutoff_date else "CURRENT_DATE"

    sql_executed = f"""
SELECT
    rp.id AS partner_id,
    rp.name AS cliente,
    rp.vat,
    rp.email,
    rp.phone,
    rp.mobile,
    COUNT(am.id) AS facturas_vencidas,
    ROUND(COALESCE(SUM(am.amount_residual), 0)::numeric, 2) AS saldo_vencido,
    MIN(am.invoice_date_due) AS factura_mas_vencida,
    MAX(am.invoice_date_due) AS ultima_fecha_vencida
FROM account_move am
JOIN res_partner rp ON rp.id = am.partner_id
WHERE am.move_type = 'out_invoice'
  AND am.state = 'posted'
  AND am.payment_state NOT IN ('paid','in_payment')
  AND am.invoice_date_due IS NOT NULL
  AND am.invoice_date_due < {cutoff_sql}
  AND am.amount_residual > 0
GROUP BY rp.id, rp.name, rp.vat, rp.email, rp.phone, rp.mobile
ORDER BY saldo_vencido DESC
LIMIT {safe_limit}
"""

    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            if cutoff_date:
                cutoff_date_obj = datetime.fromisoformat(cutoff_date).date()
                rows = await conn.fetch(sql_executed, cutoff_date_obj)
            else:
                rows = await conn.fetch(sql_executed)

    columns = [
        "partner_id", "cliente", "vat", "email", "phone", "mobile",
        "facturas_vencidas", "saldo_vencido", "factura_mas_vencida", "ultima_fecha_vencida",
    ]
    data = [dict(r) for r in rows]
    data = [{k: json_safe(v) for k, v in row.items()} for row in data]

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    suffix = cutoff_date or "hoy"
    filename = f"clientes_deuda_vencida_{suffix}.csv"

    audit_record = audit_write({
        "action": "export_overdue_customers",
        "status": "ok",
        "client_host": client_host(request),
        "cutoff_date": cutoff_date or "CURRENT_DATE",
        "limit": safe_limit,
        "filename": filename,
        "sql_requested": "EXPORT_OVERDUE_CUSTOMERS",
        "sql_executed": sql_executed,
        "columns": columns,
        "row_count_returned": len(data),
        "rows": data[:20],
        "rows_saved_note": "Solo se guardan hasta 20 filas de muestra en auditoría; el CSV puede contener más.",
        "duration_ms": duration_ms,
    })

    return csv_response(filename, columns, data, audit_record)


@app.post("/export/token")
async def export_token(request: Request, payload: dict):
    require_api_key(request)

    export_type = payload.get("export_type")
    params = payload.get("params") or {}

    if export_type == "clients":
        validate_date_yyyy_mm_dd(params.get("date_from"), "date_from")
        validate_date_yyyy_mm_dd(params.get("date_to"), "date_to")
        params["limit"] = max(1, min(int(params.get("limit", 500)), EXPORT_MAX_LIMIT))

    elif export_type == "overdue-customers":
        if params.get("cutoff_date"):
            validate_date_yyyy_mm_dd(params.get("cutoff_date"), "cutoff_date")
        params["limit"] = max(1, min(int(params.get("limit", 500)), EXPORT_MAX_LIMIT))

    else:
        raise HTTPException(status_code=400, detail="export_type no soportado.")

    token_info = create_download_token(export_type, params, created_by=client_host(request))
    full_url = f"http://{request.headers.get('host')}{token_info['download_url']}"

    audit_record = audit_write({
        "action": "create_export_token",
        "status": "ok",
        "client_host": client_host(request),
        "export_type": export_type,
        "params": params,
        "download_url": token_info["download_url"],
        "expires_at": token_info["expires_at"],
        "ttl_seconds": token_info["ttl_seconds"],
    })

    return {
        "status": "ok",
        "export_type": export_type,
        "params": params,
        "token": token_info["token"],
        "download_url": token_info["download_url"],
        "full_download_url": full_url,
        "expires_at": token_info["expires_at"],
        "ttl_seconds": token_info["ttl_seconds"],
        "backend_run_id": audit_record["run_id"],
        "backend_payload_sha256": audit_record["payload_sha256"],
        "backend_generated_at_utc": audit_record["generated_at_utc"],
    }


@app.get("/export/download/{token}")
async def export_download(token: str, request: Request):
    info = consume_download_token(token)
    export_type = info.get("export_type")
    params = info.get("params") or {}

    if export_type == "clients":
        return await build_clients_export_response(
            request,
            date_from=params["date_from"],
            date_to=params["date_to"],
            limit=int(params.get("limit", 500)),
        )

    if export_type == "overdue-customers":
        return await build_overdue_customers_export_response(
            request,
            cutoff_date=params.get("cutoff_date"),
            limit=int(params.get("limit", 500)),
        )

    raise HTTPException(status_code=400, detail="export_type del token no soportado.")


@app.get("/export/info")
async def export_info(request: Request):
    require_api_key(request)
    return {
        "status": "ok",
        "exports": [
            {
                "name": "clients",
                "url": "/export/clients?date_from=2026-06-01&date_to=2026-06-02&limit=500",
                "token_payload": {"export_type": "clients", "params": {"date_from": "2026-06-01", "date_to": "2026-06-02", "limit": 500}},
                "description": "Clientes/contactos creados por rango de fecha. date_to es exclusivo.",
            },
            {
                "name": "overdue-customers",
                "url": "/export/overdue-customers?cutoff_date=2026-06-20&limit=500",
                "token_payload": {"export_type": "overdue-customers", "params": {"cutoff_date": "2026-06-20", "limit": 500}},
                "description": "Clientes con saldo vencido.",
            },
        ],
        "export_max_limit": EXPORT_MAX_LIMIT,
        "download_token_ttl_seconds": DOWNLOAD_TOKEN_TTL_SECONDS,
    }


@app.get("/export/tokens/status")
async def export_tokens_status(request: Request):
    require_api_key(request)
    cleanup_download_tokens()
    return {
        "status": "ok",
        "active_tokens": len(DOWNLOAD_TOKENS),
        "ttl_seconds": DOWNLOAD_TOKEN_TTL_SECONDS,
    }


@app.get("/audit/last")
async def backend_audit_last(request: Request, limit: int = 5):
    require_api_key(request)
    return audit_last(limit)


@app.get("/audit/{run_id}")
async def backend_audit_find(run_id: str, request: Request):
    require_api_key(request)
    return audit_find(run_id)
