"""
title: Analizador Odoo Seguro Pipe
author: Baloo / Amanecer
version: 2.3.0
description: Pipe seguro para Open WebUI que llama directamente a pg-tools, con auditoría, protecciones SQL, comandos naturales Odoo y respuestas JSON verificables.
requirements: httpx,pydantic
"""

import json
import re
import os
import hashlib
from uuid import uuid4
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

import httpx
from pydantic import BaseModel, Field


class Pipe:
    class Valves(BaseModel):
        PG_TOOLS_URL: str = Field(
            default="http://10.17.71.19:8100",
            description="URL base de pg-tools. Ejemplo: http://10.17.71.19:8100",
        )
        DEFAULT_LIMIT: int = Field(
            default=100,
            description="Límite por defecto para consultas SELECT.",
        )
        MAX_LIMIT: int = Field(
            default=500,
            description="Límite máximo permitido por el Pipe.",
        )
        PG_TOOLS_API_KEY: str = Field(
            default="",
            description="API key para autenticar contra pg-tools. Debe coincidir con PG_TOOLS_API_KEY del backend.",
        )
        API_KEY_HEADER: str = Field(
            default="X-API-Key",
            description="Nombre del header usado para enviar la API key.",
        )
        AUDIT_FILE: str = Field(
            default="/tmp/analizador_odoo_seguro_audit.jsonl",
            description="Archivo local del contenedor Open WebUI donde se guarda auditoría JSONL.",
        )
        STORE_USER_PII: bool = Field(
            default=False,
            description="Si es True guarda nombre/email del usuario en auditoría. Por defecto solo guarda id y role.",
        )
        BLOCK_SENSITIVE_TABLES: bool = Field(
            default=True,
            description="Bloquea consultas directas a tablas sensibles.",
        )
        RESPONSE_MODE_DEFAULT: str = Field(
            default="json",
            description="json o resumen. json devuelve JSON completo. resumen devuelve explicación corta con JSON al final.",
        )

    def __init__(self):
        self.valves = self.Valves()

    def pipes(self):
        return [
            {
                "id": "analizador-odoo-seguro-v2-3",
                "name": "Analizador Odoo Seguro v2.3",
            }
        ]

    # ---------------------------------------------------------------------
    # Utilidades generales
    # ---------------------------------------------------------------------

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _last_user_message(self, body: dict) -> str:
        messages = body.get("messages", [])
        if not messages:
            return ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                return str(content)
        return ""

    def _get_user_info(self, __user__: Optional[dict]) -> dict:
        if not __user__:
            return {}

        info = {
            "id": __user__.get("id"),
            "role": __user__.get("role"),
        }

        if self.valves.STORE_USER_PII:
            info["name"] = __user__.get("name")
            info["email"] = __user__.get("email")

        return info


    def _make_payload(self, payload: dict, __user__: Optional[dict] = None) -> dict:
        payload.setdefault("pipe_version", "analizador_odoo_seguro_v2_3")
        payload.setdefault("generated_at_utc", self._now())
        payload.setdefault("run_id", str(uuid4()))
        payload.setdefault("user", self._get_user_info(__user__))

        payload_for_hash = dict(payload)
        payload_for_hash.pop("payload_sha256", None)
        raw = json.dumps(payload_for_hash, ensure_ascii=False, sort_keys=True, default=str)
        payload["payload_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()

        # No auditar consultas de auditoría, para evitar ruido/recursión:
        # "auditoría últimos 5" no debe crear otro registro audit_last.
        if payload.get("action") not in {"audit_last", "audit_find"}:
            self._audit_write(payload)

        return payload

    def _json(self, payload: dict, __user__: Optional[dict] = None) -> str:
        final_payload = self._make_payload(payload, __user__)
        return json.dumps(final_payload, ensure_ascii=False, indent=2, default=str)

    def _audit_write(self, payload: dict) -> None:
        try:
            audit_dir = os.path.dirname(self.valves.AUDIT_FILE)
            if audit_dir:
                os.makedirs(audit_dir, exist_ok=True)
            with open(self.valves.AUDIT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except Exception:
            # La auditoría no debe tumbar el Pipe.
            pass

    def _compact_audit_record(self, record: dict) -> dict:
        """Devuelve una versión liviana de un registro de auditoría para no congelar Open WebUI."""
        compact = {
            "run_id": record.get("run_id"),
            "generated_at_utc": record.get("generated_at_utc"),
            "pipe_version": record.get("pipe_version"),
            "action": record.get("action"),
            "status": record.get("status"),
            "source": record.get("source"),
            "payload_sha256": record.get("payload_sha256"),
        }

        if record.get("sql_executed"):
            compact["sql_executed"] = record.get("sql_executed")
        elif record.get("sql_sent_to_pg_tools"):
            compact["sql_sent_to_pg_tools"] = record.get("sql_sent_to_pg_tools")

        if record.get("columns"):
            compact["columns"] = record.get("columns")

        if "row_count_returned" in record:
            compact["row_count_returned"] = record.get("row_count_returned")

        rows = record.get("rows")
        if isinstance(rows, list):
            compact["rows_saved_in_audit"] = len(rows)
            compact["rows_preview"] = rows[:2]

        results = record.get("results")
        if isinstance(results, dict):
            compact_results = {}
            for key, value in results.items():
                if isinstance(value, dict):
                    compact_results[key] = {
                        "value": value.get("value"),
                        "status": value.get("status"),
                        "row_count_returned": value.get("row_count_returned"),
                        "sql_executed": value.get("sql_executed"),
                    }
                else:
                    compact_results[key] = value
            compact["results"] = compact_results

        if record.get("error"):
            compact["error"] = record.get("error")

        return compact

    def _audit_last(self, limit: int = 5) -> dict:
        limit = max(1, min(int(limit), 20))
        if not os.path.exists(self.valves.AUDIT_FILE):
            return {
                "status": "ok",
                "audit_file": self.valves.AUDIT_FILE,
                "records": [],
                "message": "No existe archivo de auditoría todavía.",
            }

        with open(self.valves.AUDIT_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]

        records = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                records.append(self._compact_audit_record(record))
            except Exception:
                records.append({
                    "raw_preview": line[:500],
                    "warning": "Registro no parseable o demasiado grande."
                })

        return {
            "status": "ok",
            "audit_file": self.valves.AUDIT_FILE,
            "records_returned": len(records),
            "mode": "compact",
            "records": records,
        }

    def _audit_find(self, run_id: str) -> dict:
        if not os.path.exists(self.valves.AUDIT_FILE):
            return {
                "status": "not_found",
                "audit_file": self.valves.AUDIT_FILE,
                "run_id": run_id,
                "message": "No existe archivo de auditoría todavía.",
            }

        with open(self.valves.AUDIT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if record.get("run_id") == run_id:
                    return {
                        "status": "ok",
                        "audit_file": self.valves.AUDIT_FILE,
                        "run_id": run_id,
                        "mode": "compact",
                        "record": self._compact_audit_record(record),
                        "important_note": "La consulta auditada existe. Se devuelve versión compacta para evitar congelar Open WebUI.",
                    }

        return {
            "status": "not_found",
            "audit_file": self.valves.AUDIT_FILE,
            "run_id": run_id,
            "message": "No encontré ese run_id en la auditoría local del Pipe.",
        }


    def _wants_summary(self, text: str) -> bool:
        low = text.lower()
        return (
            "resumen" in low
            or "explica" in low
            or "analiza" in low
            or "en palabras" in low
            or self.valves.RESPONSE_MODE_DEFAULT.lower() == "resumen"
        )

    def _format_response(self, payload: dict, user_text: str, __user__: Optional[dict] = None) -> str:
        final_payload = self._make_payload(payload, __user__)

        if not self._wants_summary(user_text):
            return json.dumps(final_payload, ensure_ascii=False, indent=2, default=str)

        summary = self._human_summary(final_payload)
        return (
            f"{summary}\n\n"
            "JSON verificable:\n"
            "```json\n"
            f"{json.dumps(final_payload, ensure_ascii=False, indent=2, default=str)}\n"
            "```"
        )

    def _human_summary(self, payload: dict) -> str:
        status = payload.get("status", "desconocido")
        action = payload.get("action", "acción")
        if status != "ok":
            return f"Resultado: {status}. Acción: {action}. Revisa el JSON para ver el error exacto."

        rows = payload.get("rows")
        if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
            pairs = [f"{k}: {v}" for k, v in rows[0].items()]
            return f"Consulta ejecutada correctamente. Resultado: " + ", ".join(pairs)

        results = payload.get("results")
        if isinstance(results, dict):
            simple = []
            for k, v in results.items():
                if isinstance(v, dict) and "value" in v:
                    simple.append(f"{k}: {v.get('value')}")
            if simple:
                return "Conteos ejecutados correctamente. " + ", ".join(simple)

        return f"Consulta ejecutada correctamente. Acción: {action}. Filas devueltas: {payload.get('row_count_returned', 'N/A')}."

    # ---------------------------------------------------------------------
    # SQL parsing y protección
    # ---------------------------------------------------------------------

    def _clean_sql(self, sql: str) -> str:
        sql = str(sql or "").strip()
        sql = sql.replace("```sql", "").replace("```", "").strip()
        while sql.endswith(";"):
            sql = sql[:-1].strip()
        return sql

    def _extract_sql(self, text: str) -> Optional[str]:
        block = re.search(r"```sql\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
        if block:
            return self._clean_sql(block.group(1))

        m = re.search(r"SQL exacto\s*:\s*(.*)", text, re.IGNORECASE | re.DOTALL)
        if m:
            candidate = m.group(1).strip()
            cut_patterns = [
                r"\n\s*Responde\b",
                r"\n\s*Copia\b",
                r"\n\s*No cambies\b",
                r"\n\s*La respuesta\b",
                r"\n\s*Solo\b",
            ]
            for p in cut_patterns:
                cut = re.search(p, candidate, re.IGNORECASE)
                if cut:
                    candidate = candidate[:cut.start()].strip()
                    break
            return self._clean_sql(candidate)

        stripped = text.strip()
        if re.match(r"^(SELECT|WITH|EXPLAIN)\b", stripped, re.IGNORECASE):
            return self._clean_sql(stripped)

        return None

    def _first_sql_word(self, sql: str) -> str:
        cleaned = sql.strip()
        if not cleaned:
            return ""
        return cleaned.split(None, 1)[0].upper()

    def _contains_forbidden_sql(self, sql: str) -> Optional[str]:
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
                return f"Patrón SQL bloqueado: {pattern}"

        # Evita múltiples statements simples.
        if ";" in sql.strip().rstrip(";"):
            return "Múltiples sentencias SQL bloqueadas."

        return None

    def _tables_in_sql(self, sql: str) -> List[str]:
        # Extracción básica de tablas después de FROM/JOIN.
        tables = []
        for m in re.finditer(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_\.]*)", sql, re.IGNORECASE):
            tables.append(m.group(1).split(".")[-1])
        return tables

    def _sensitive_table_reason(self, sql: str) -> Optional[str]:
        if not self.valves.BLOCK_SENSITIVE_TABLES:
            return None

        sensitive = {
            "res_users": "usuarios del sistema",
            "res_partner_bank": "cuentas bancarias",
            "ir_config_parameter": "parámetros y secretos",
            "ir_attachment": "adjuntos/documentos",
            "auth_oauth_provider": "configuración de autenticación",
            "payment_token": "tokens de pago",
            "payment_transaction": "transacciones de pago",
            "mail_mail": "correos salientes",
            "fetchmail_server": "credenciales/configuración de correo",
        }

        for table in self._tables_in_sql(sql):
            if table in sensitive:
                return f"Consulta bloqueada sobre tabla sensible {table}: {sensitive[table]}."

        return None

    def _validate_sql(self, sql: str) -> Tuple[bool, Optional[str]]:
        first = self._first_sql_word(sql)
        if first not in {"SELECT", "WITH", "EXPLAIN"}:
            return False, "Solo se permiten SELECT, WITH o EXPLAIN."

        forbidden = self._contains_forbidden_sql(sql)
        if forbidden:
            return False, forbidden

        sensitive = self._sensitive_table_reason(sql)
        if sensitive:
            return False, sensitive

        return True, None

    def _extract_date_range(self, text: str) -> Optional[Tuple[str, str]]:
        """
        Detecta:
        - 2026-06-01
        - desde 2026-06-01 hasta 2026-06-02
        - del 2026-06-01 al 2026-06-02
        """
        dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
        if not dates:
            return None

        if len(dates) == 1:
            start = dates[0]
            # El SQL hará < start + interval '1 day'
            return (start, "")

        return (dates[0], dates[1])

    def _date_filter_sql(self, column: str, start: str, end: str = "") -> str:
        if end:
            return f"{column} >= '{start} 00:00:00' AND {column} < '{end} 00:00:00'"
        return f"{column} >= '{start} 00:00:00' AND {column} < ('{start} 00:00:00'::timestamp + interval '1 day')"

    # ---------------------------------------------------------------------
    # HTTP pg-tools
    # ---------------------------------------------------------------------

    def _headers(self) -> dict:
        headers = {}
        if self.valves.PG_TOOLS_API_KEY:
            headers[self.valves.API_KEY_HEADER] = self.valves.PG_TOOLS_API_KEY
        return headers

    async def _pg_get(self, path: str) -> dict:
        url = f"{self.valves.PG_TOOLS_URL}{path}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(url, headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def _pg_post(self, path: str, body: dict) -> dict:
        url = f"{self.valves.PG_TOOLS_URL}{path}"
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, json=body, headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def _run_sql(self, sql: str, limit: Optional[int] = None) -> dict:
        sql_clean = self._clean_sql(sql)

        valid, reason = self._validate_sql(sql_clean)
        if not valid:
            return {
                "status": "blocked",
                "error": reason,
                "sql_requested": sql,
                "sql_sent_to_pg_tools": None,
            }

        safe_limit = limit or self.valves.DEFAULT_LIMIT
        safe_limit = max(1, min(int(safe_limit), int(self.valves.MAX_LIMIT)))

        data = await self._pg_post("/query", {"query": sql_clean, "limit": safe_limit})

        return {
            "status": "ok",
            "sql_requested": sql,
            "sql_sent_to_pg_tools": sql_clean,
            "sql_executed": data.get("sql", sql_clean),
            "columns": data.get("columns", []),
            "rows": data.get("rows", []),
            "row_count_returned": data.get("count", 0),
        }

    # ---------------------------------------------------------------------
    # Acciones controladas
    # ---------------------------------------------------------------------

    async def _health(self, text: str, __user__: Optional[dict]) -> str:
        try:
            data = await self._pg_get("/health")
            payload = {
                "pipe": "analizador_odoo_seguro",
                "action": "health_check",
                "source": "pg-tools",
                "status": "ok",
                "pg_tools_url": self.valves.PG_TOOLS_URL,
                "response": data,
            }
        except Exception as e:
            payload = {
                "pipe": "analizador_odoo_seguro",
                "action": "health_check",
                "source": "pg-tools",
                "status": "error",
                "pg_tools_url": self.valves.PG_TOOLS_URL,
                "error": str(e),
            }
        return self._format_response(payload, text, __user__)

    async def _validate_counts(self, text: str, __user__: Optional[dict]) -> str:
        queries = {
            "total_contactos": "SELECT COUNT(id) AS total_contactos FROM res_partner",
            "total_facturas_cliente": "SELECT COUNT(id) AS total_facturas_cliente FROM account_move WHERE move_type IN ('out_invoice','out_refund')",
            "total_lineas_contables": "SELECT COUNT(id) AS total_lineas_contables FROM account_move_line",
            "total_ordenes_venta": "SELECT COUNT(id) AS total_ordenes_venta FROM sale_order",
        }

        results = {}
        errors = {}

        for key, sql in queries.items():
            try:
                res = await self._run_sql(sql, limit=10)
                value = None
                if res.get("rows") and key in res["rows"][0]:
                    value = res["rows"][0][key]
                results[key] = {"value": value, **res}
            except Exception as e:
                errors[key] = str(e)

        payload = {
            "pipe": "analizador_odoo_seguro",
            "action": "validate_odoo_basic_counts",
            "source": "pg-tools",
            "status": "ok" if not errors else "partial_error",
            "results": results,
            "errors": errors,
            "important_note": "Estos conteos fueron ejecutados directamente por el Pipe contra pg-tools.",
        }
        return self._format_response(payload, text, __user__)

    async def _schema(self, text: str, __user__: Optional[dict]) -> str:
        max_tables = 20
        m = re.search(r"max_tables\s*=\s*(\d+)", text, re.IGNORECASE)
        if m:
            max_tables = max(1, min(int(m.group(1)), 50))

        try:
            data = await self._pg_get("/schema?schema_name=public")
            tables = data.get("tables", {})
            tables_sorted = sorted(
                tables.items(),
                key=lambda item: item[1].get("row_count") or 0,
                reverse=True,
            )

            out_tables = []
            for table, info in tables_sorted[:max_tables]:
                cols = info.get("columns", [])
                out_tables.append({
                    "table": table,
                    "estimated_rows": info.get("row_count"),
                    "total_columns": len(cols),
                    "shown_columns": [
                        {
                            "name": c.get("name"),
                            "type": c.get("type"),
                            "nullable": c.get("nullable"),
                        }
                        for c in cols[:12]
                    ],
                    "hidden_columns": max(0, len(cols) - 12),
                })

            payload = {
                "pipe": "analizador_odoo_seguro",
                "action": "get_database_schema",
                "source": "pg-tools",
                "status": "ok",
                "schema": "public",
                "total_tables_found": len(tables_sorted),
                "max_tables_returned": max_tables,
                "ordering": "estimated_rows_desc",
                "tables": out_tables,
            }
        except Exception as e:
            payload = {
                "pipe": "analizador_odoo_seguro",
                "action": "get_database_schema",
                "source": "pg-tools",
                "status": "error",
                "error": str(e),
            }

        return self._format_response(payload, text, __user__)

    async def _sample(self, text: str, __user__: Optional[dict]) -> str:
        table = None

        m = re.search(r"table_name\s*=\s*[\"']?([a-zA-Z_][a-zA-Z0-9_]*)[\"']?", text)
        if m:
            table = m.group(1)

        if not table:
            m = re.search(r"muestra\s+(?:de|tabla)?\s*([a-zA-Z_][a-zA-Z0-9_]*)", text, re.IGNORECASE)
            if m:
                table = m.group(1)

        if not table:
            payload = {
                "pipe": "analizador_odoo_seguro",
                "action": "get_table_sample",
                "status": "error",
                "error": "No pude detectar table_name. Usa: muestra table_name=sale_order limit=5",
            }
            return self._format_response(payload, text, __user__)

        # Bloqueo de muestras de tablas sensibles
        fake_sql = f"SELECT * FROM {table}"
        sensitive = self._sensitive_table_reason(fake_sql)
        if sensitive:
            payload = {
                "pipe": "analizador_odoo_seguro",
                "action": "get_table_sample",
                "status": "blocked",
                "table": f"public.{table}",
                "error": sensitive,
            }
            return self._format_response(payload, text, __user__)

        limit = 5
        lm = re.search(r"limit\s*=\s*(\d+)", text, re.IGNORECASE)
        if lm:
            limit = max(1, min(int(lm.group(1)), 20))

        try:
            data = await self._pg_get(f"/sample/{table}?schema_name=public&limit={limit}")
            payload = {
                "pipe": "analizador_odoo_seguro",
                "action": "get_table_sample",
                "source": "pg-tools",
                "status": "ok",
                "table": f"public.{table}",
                "columns": data.get("columns", []),
                "rows": data.get("rows", []),
                "row_count_returned": data.get("count", len(data.get("rows", []))),
                "important_note": "Muestra real devuelta por PostgreSQL. No eliminar ni modificar filas.",
            }
        except Exception as e:
            payload = {
                "pipe": "analizador_odoo_seguro",
                "action": "get_table_sample",
                "source": "pg-tools",
                "status": "error",
                "table": f"public.{table}",
                "error": str(e),
            }

        return self._format_response(payload, text, __user__)

    async def _sql_from_user(self, sql: str, text: str, __user__: Optional[dict]) -> str:
        try:
            res = await self._run_sql(sql)
            payload = {
                "pipe": "analizador_odoo_seguro",
                "action": "run_sql_query",
                "source": "pg-tools",
                **res,
                "important_note": "SQL ejecutado directamente por el Pipe contra pg-tools.",
            }
        except Exception as e:
            payload = {
                "pipe": "analizador_odoo_seguro",
                "action": "run_sql_query",
                "source": "pg-tools",
                "status": "error",
                "sql_requested": sql,
                "error": str(e),
            }

        return self._format_response(payload, text, __user__)

    async def _count_sale_orders_until_id(self, max_id: int, text: str, __user__: Optional[dict]) -> str:
        sql = f"SELECT COUNT(id) AS total_ordenes_venta_id_corte FROM sale_order WHERE id <= {int(max_id)}"
        return await self._sql_from_user(sql, text, __user__)

    async def _audit_action(self, text: str, __user__: Optional[dict]) -> str:
        low = text.lower()

        m = re.search(r"run_id\s*[:=]\s*([a-f0-9\-]{20,80})", text, re.IGNORECASE)
        if m:
            result = self._audit_find(m.group(1))
            payload = {
                "pipe": "analizador_odoo_seguro",
                "action": "audit_find",
                "source": "pipe_local_audit",
                **result,
                "important_note": "Esta consulta de auditoría no se guarda dentro de la auditoría para evitar ruido.",
            }
            return self._format_response(payload, text, __user__)

        limit = 5
        lm = re.search(r"(?:ultimos|últimos|last)\s+(\d+)", low)
        if lm:
            limit = int(lm.group(1))

        result = self._audit_last(limit)
        payload = {
            "pipe": "analizador_odoo_seguro",
            "action": "audit_last",
            "source": "pipe_local_audit",
            **result,
            "important_note": "Esta consulta de auditoría no se guarda dentro de la auditoría para evitar ruido.",
        }
        return self._format_response(payload, text, __user__)

    # ---------------------------------------------------------------------
    # Comandos naturales Odoo
    # ---------------------------------------------------------------------

    async def _natural_odoo(self, text: str, __user__: Optional[dict]) -> Optional[str]:
        low = text.lower()
        date_range = self._extract_date_range(text)

        # Contactos creados en fecha/rango
        if ("contactos" in low or "clientes" in low or "res_partner" in low) and ("cread" in low or date_range):
            if date_range:
                start, end = date_range
                where = self._date_filter_sql("create_date", start, end)
                sql = f"SELECT COUNT(id) AS contactos_creados FROM res_partner WHERE {where}"
                return await self._sql_from_user(sql, text, __user__)

        # Órdenes de venta por fecha/rango
        if ("ordenes" in low or "órdenes" in low or "sale_order" in low or "ventas" in low) and date_range:
            start, end = date_range
            where = self._date_filter_sql("create_date", start, end)
            sql = f"SELECT COUNT(id) AS ordenes_venta_creadas FROM sale_order WHERE {where}"
            return await self._sql_from_user(sql, text, __user__)

        # Facturas cliente por fecha/rango
        if ("facturas" in low or "account_move" in low) and date_range:
            start, end = date_range
            where = self._date_filter_sql("create_date", start, end)
            sql = (
                "SELECT COUNT(id) AS facturas_cliente_creadas "
                "FROM account_move "
                "WHERE move_type IN ('out_invoice','out_refund') "
                f"AND {where}"
            )
            return await self._sql_from_user(sql, text, __user__)

        # Líneas contables por fecha/rango
        if ("lineas contables" in low or "líneas contables" in low or "account_move_line" in low) and date_range:
            start, end = date_range
            where = self._date_filter_sql("create_date", start, end)
            sql = f"SELECT COUNT(id) AS lineas_contables_creadas FROM account_move_line WHERE {where}"
            return await self._sql_from_user(sql, text, __user__)

        # Top clientes por facturación, rango opcional
        if ("top clientes" in low or "mejores clientes" in low) and ("factur" in low or "venta" in low):
            limit = 10
            lm = re.search(r"(?:top|limit)\s*=?\s*(\d+)", low)
            if lm:
                limit = max(1, min(int(lm.group(1)), 50))

            date_sql = ""
            if date_range:
                start, end = date_range
                date_sql = "AND " + self._date_filter_sql("am.create_date", start, end)

            sql = f"""
SELECT
    rp.id AS partner_id,
    rp.name AS cliente,
    COUNT(am.id) AS total_facturas,
    ROUND(COALESCE(SUM(am.amount_total), 0)::numeric, 2) AS monto_total
FROM account_move am
JOIN res_partner rp ON rp.id = am.partner_id
WHERE am.move_type IN ('out_invoice','out_refund')
  AND am.state = 'posted'
  {date_sql}
GROUP BY rp.id, rp.name
ORDER BY monto_total DESC
LIMIT {limit}
"""
            return await self._sql_from_user(sql, text, __user__)

        # Productos más vendidos, rango opcional
        if ("productos mas vendidos" in low or "productos más vendidos" in low or "top productos" in low):
            limit = 10
            lm = re.search(r"(?:top|limit)\s*=?\s*(\d+)", low)
            if lm:
                limit = max(1, min(int(lm.group(1)), 50))

            date_sql = ""
            if date_range:
                start, end = date_range
                date_sql = "AND " + self._date_filter_sql("so.create_date", start, end)

            sql = f"""
SELECT
    sol.product_id,
    COALESCE(pt.name::text, pp.default_code::text, sol.name::text) AS producto,
    ROUND(SUM(sol.product_uom_qty)::numeric, 2) AS cantidad_vendida,
    ROUND(SUM(sol.price_total)::numeric, 2) AS monto_total
FROM sale_order_line sol
JOIN sale_order so ON so.id = sol.order_id
LEFT JOIN product_product pp ON pp.id = sol.product_id
LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id
WHERE so.state IN ('sale','done')
  AND sol.product_id IS NOT NULL
  {date_sql}
GROUP BY sol.product_id, producto
ORDER BY cantidad_vendida DESC
LIMIT {limit}
"""
            return await self._sql_from_user(sql, text, __user__)

        # Conteos simples sin fecha
        if "contactos" in low or "res_partner" in low:
            return await self._sql_from_user("SELECT COUNT(id) AS total_contactos FROM res_partner", text, __user__)

        if "facturas" in low and ("cliente" in low or "clientes" in low):
            return await self._sql_from_user("SELECT COUNT(id) AS total_facturas_cliente FROM account_move WHERE move_type IN ('out_invoice','out_refund')", text, __user__)

        if "lineas contables" in low or "líneas contables" in low or "account_move_line" in low:
            return await self._sql_from_user("SELECT COUNT(id) AS total_lineas_contables FROM account_move_line", text, __user__)

        if "ordenes de venta" in low or "órdenes de venta" in low or "sale_order" in low:
            return await self._sql_from_user("SELECT COUNT(id) AS total_ordenes_venta FROM sale_order", text, __user__)

        return None

    # ---------------------------------------------------------------------
    # Entrada principal
    # ---------------------------------------------------------------------

    async def pipe(self, body: dict, __user__: dict = None) -> str:
        text = self._last_user_message(body)
        low = text.lower()

        # Auditoría
        if "auditoria" in low or "auditoría" in low or "audit" in low or "run_id" in low:
            return await self._audit_action(text, __user__)

        # Health
        if "health" in low or "salud" in low or "pg-tools responde" in low:
            return await self._health(text, __user__)

        # Validación básica
        if "validate_odoo_basic_counts" in low or "conteos básicos" in low or "conteos basicos" in low:
            return await self._validate_counts(text, __user__)

        # Esquema
        if "get_database_schema" in low or "esquema" in low or "schema" in low:
            return await self._schema(text, __user__)

        # Muestra de tabla
        if "get_table_sample" in low or "muestra" in low or "sample" in low:
            return await self._sample(text, __user__)

        # Conteo de sale_order con corte por id
        id_match = re.search(r"sale_order\s+where\s+id\s*<=\s*(\d+)", low)
        if not id_match:
            id_match = re.search(r"id\s*<=\s*(\d+)", low)

        if "sale_order" in low and id_match:
            return await self._count_sale_orders_until_id(int(id_match.group(1)), text, __user__)

        # SQL explícito
        sql = self._extract_sql(text)
        if sql:
            return await self._sql_from_user(sql, text, __user__)

        # Lenguaje natural
        natural = await self._natural_odoo(text, __user__)
        if natural:
            return natural

        payload = {
            "pipe": "analizador_odoo_seguro",
            "status": "no_ejecutado",
            "message": "NO_EJECUTADO",
            "reason": "No se detectó una acción soportada.",
            "examples": [
                "health",
                "Usa validate_odoo_basic_counts",
                "SELECT COUNT(id) AS total_contactos FROM res_partner",
                "SELECT COUNT(id) AS total_ordenes_venta_id_corte FROM sale_order WHERE id <= 1421712",
                "contactos creados el 2026-06-01",
                "órdenes de venta creadas del 2026-06-01 al 2026-06-02",
                "facturas cliente creadas el 2026-06-01",
                "top clientes por facturación el 2026-06-01",
                "productos más vendidos el 2026-06-01 top 10",
                "muestra table_name=sale_order limit=5",
                "auditoría últimos 5",
                "auditoría run_id=<uuid>",
            ],
        }
        return self._format_response(payload, text, __user__)
