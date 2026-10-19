"""
F018 - Clientes nuevos vs recurrentes.

Definición:
- NEW:
  la primera compra histórica del cliente ocurre dentro del período.
- RECURRING:
  el cliente ya tenía al menos una compra confirmada antes del inicio
  del período y vuelve a comprar dentro del período.

Alcance:
- sale_order.state = 'sale'
- external_only
- excluye intercompany
- consolidación por RIF/VAT normalizado
- fallback a partner_id
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_new_vs_recurring_customers(
    *,
    operation,
    request,
    pool_getter,
    audit_write,
    client_host,
    started,
    parse_iso_datetime,
    warehouse,
    date_from,
    date_to,
):
    if not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail=(
                "new_vs_recurring_customers requiere "
                "date_from y date_to."
            ),
        )

    date_from_dt = parse_iso_datetime(
        date_from,
        "date_from",
    )

    date_to_dt = parse_iso_datetime(
        date_to,
        "date_to",
    )

    if date_to_dt <= date_from_dt:
        raise HTTPException(
            status_code=400,
            detail="date_to debe ser posterior a date_from.",
        )

    warehouse_filter = (
        warehouse.strip()
        if warehouse
        else None
    )

    sql = """
        WITH all_external_orders AS (
            SELECT
                so.id AS order_id,
                so.date_order,

                COALESCE(
                    NULLIF(
                        REGEXP_REPLACE(
                            UPPER(COALESCE(rp.vat, '')),
                            '[^A-Z0-9]',
                            '',
                            'g'
                        ),
                        ''
                    ),
                    'PARTNER_' || rp.id::text
                ) AS customer_key,

                rp.name AS customer_name,
                rp.vat AS customer_vat

            FROM sale_order so

            JOIN res_partner rp
                ON rp.id = so.partner_id

            LEFT JOIN stock_warehouse sw
                ON sw.id = so.warehouse_id

            WHERE so.state = 'sale'
              AND so.date_order IS NOT NULL

              AND NOT EXISTS (
                  SELECT 1
                  FROM res_company internal_company
                  WHERE internal_company.partner_id =
                        COALESCE(
                            rp.parent_id,
                            rp.id
                        )
              )

              AND (
                    $3::text IS NULL
                    OR UPPER(sw.name) LIKE
                       '%' || UPPER($3::text) || '%'
              )
        ),

        first_purchase AS (
            SELECT
                customer_key,
                MIN(date_order) AS first_purchase_date

            FROM all_external_orders

            GROUP BY customer_key
        ),

        period_customers AS (
            SELECT
                o.customer_key,

                MAX(o.customer_name)
                    AS customer_name,

                MAX(o.customer_vat)
                    AS customer_vat,

                COUNT(DISTINCT o.order_id)
                    AS order_count,

                MIN(o.date_order)
                    AS first_purchase_in_period,

                MAX(o.date_order)
                    AS last_purchase_in_period,

                fp.first_purchase_date

            FROM all_external_orders o

            JOIN first_purchase fp
                ON fp.customer_key = o.customer_key

            WHERE o.date_order >= $1
              AND o.date_order < $2

            GROUP BY
                o.customer_key,
                fp.first_purchase_date
        ),

        classified AS (
            SELECT
                *,

                CASE
                    WHEN first_purchase_date >= $1
                     AND first_purchase_date < $2
                    THEN 'NEW'
                    ELSE 'RECURRING'
                END AS customer_type

            FROM period_customers
        ),

        summary AS (
            SELECT
                customer_type,
                COUNT(*) AS customer_count,
                SUM(order_count) AS order_count

            FROM classified

            GROUP BY customer_type
        )

        SELECT
            customer_type,
            customer_count,
            order_count

        FROM summary

        ORDER BY customer_type
    """

    async with pool_getter().acquire() as conn:
        rows = await conn.fetch(
            sql,
            date_from_dt,
            date_to_dt,
            warehouse_filter,
        )

    duration_ms = int(
        (
            datetime.now(timezone.utc)
            - started
        ).total_seconds() * 1000
    )

    filters = {
        "operation": operation,
        "warehouse": warehouse,
        "date_from": date_from,
        "date_to": date_to,
        "date_to_inclusive": False,
        "sales_scope": "external_only",
    }

    if not rows:
        audit_write({
            "action": "sales_query",
            "status": "ok",
            "client_host": client_host(request),
            "operation": operation,
            "filters": filters,
            "duration_ms": duration_ms,
            "rows_returned": 0,
        })

        return {
            "found": False,
            "answer": (
                "No se encontraron clientes activos "
                "para el período indicado."
            ),
            "filters": filters,
        }

    data = {
        row["customer_type"]: {
            "customer_count": int(row["customer_count"]),
            "order_count": int(row["order_count"]),
        }
        for row in rows
    }

    new_data = data.get(
        "NEW",
        {
            "customer_count": 0,
            "order_count": 0,
        },
    )

    recurring_data = data.get(
        "RECURRING",
        {
            "customer_count": 0,
            "order_count": 0,
        },
    )

    total_customers = (
        new_data["customer_count"]
        + recurring_data["customer_count"]
    )

    total_orders = (
        new_data["order_count"]
        + recurring_data["order_count"]
    )

    new_customer_pct = (
        round(
            new_data["customer_count"]
            / total_customers
            * 100,
            2,
        )
        if total_customers
        else 0.0
    )

    recurring_customer_pct = (
        round(
            recurring_data["customer_count"]
            / total_customers
            * 100,
            2,
        )
        if total_customers
        else 0.0
    )

    new_order_pct = (
        round(
            new_data["order_count"]
            / total_orders
            * 100,
            2,
        )
        if total_orders
        else 0.0
    )

    recurring_order_pct = (
        round(
            recurring_data["order_count"]
            / total_orders
            * 100,
            2,
        )
        if total_orders
        else 0.0
    )

    answer = (
        f"En el período hubo {total_customers:,} clientes activos: "
        f"{new_data['customer_count']:,} nuevos "
        f"({new_customer_pct:.2f}%) y "
        f"{recurring_data['customer_count']:,} recurrentes "
        f"({recurring_customer_pct:.2f}%). "
        f"Los clientes nuevos generaron "
        f"{new_data['order_count']:,} órdenes y los recurrentes "
        f"{recurring_data['order_count']:,} órdenes."
    )

    result = {
        "found": True,

        "answer": answer,

        "definition": {
            "operation": "new_vs_recurring_customers",
            "table": "sale_order",
            "state": "sale",
            "date_field": "date_order",
            "customer_consolidation": (
                "RIF/VAT normalizado; "
                "fallback a partner_id."
            ),
            "new_customer_definition": (
                "La primera compra histórica ocurre "
                "dentro del período consultado."
            ),
            "recurring_customer_definition": (
                "Ya tenía una compra confirmada antes "
                "del inicio del período y vuelve a comprar."
            ),
            "sales_scope": "external_only",
            "intercompany_policy": (
                "Se excluyen clientes cuyo partner resuelto "
                "pertenece a res_company."
            ),
        },

        "filters": filters,

        "summary": {
            "total_customers": total_customers,
            "total_orders": total_orders,

            "new_customers": {
                "customer_count": new_data["customer_count"],
                "customer_percentage": new_customer_pct,
                "order_count": new_data["order_count"],
                "order_percentage": new_order_pct,
            },

            "recurring_customers": {
                "customer_count": recurring_data["customer_count"],
                "customer_percentage": recurring_customer_pct,
                "order_count": recurring_data["order_count"],
                "order_percentage": recurring_order_pct,
            },
        },
    }

    audit_write({
        "action": "sales_query",
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": filters,
        "duration_ms": duration_ms,
        "rows_returned": len(rows),
    })

    return result
