"""
F017 - Frecuencia de compra por cliente.

Definición certificada:
- sale_order.state = 'sale'
- alcance external_only
- excluye intercompany
- consolida clientes por RIF/VAT normalizado
- fallback a partner_id si no existe RIF/VAT
- frecuencia promedio:
    (ultima_compra - primera_compra) / (ordenes - 1)
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_purchase_frequency(
    *,
    operation,
    request,
    pool_getter,
    audit_write,
    client_host,
    started,
    parse_iso_datetime,
    warehouse,
    customer,
    date_from,
    date_to,
    limit,
):
    if not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail=(
                "purchase_frequency requiere "
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

    customer_filter = (
        customer.strip()
        if customer
        else None
    )

    sql = """
        WITH customer_orders AS (
            SELECT
                so.id AS order_id,
                so.date_order,

                rp.id AS partner_id,

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
              AND so.date_order >= $1
              AND so.date_order < $2

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

              AND (
                    $4::text IS NULL

                    OR UPPER(rp.name) LIKE
                       '%' || UPPER($4::text) || '%'

                    OR REGEXP_REPLACE(
                           UPPER(COALESCE(rp.vat, '')),
                           '[^A-Z0-9]',
                           '',
                           'g'
                       ) LIKE
                       '%' ||
                       REGEXP_REPLACE(
                           UPPER($4::text),
                           '[^A-Z0-9]',
                           '',
                           'g'
                       ) ||
                       '%'
              )
        ),

        customer_frequency AS (
            SELECT
                customer_key,

                MAX(customer_name)
                    AS customer_name,

                MAX(customer_vat)
                    AS customer_vat,

                COUNT(DISTINCT order_id)
                    AS order_count,

                MIN(date_order)
                    AS first_purchase,

                MAX(date_order)
                    AS last_purchase,

                EXTRACT(
                    EPOCH FROM (
                        MAX(date_order)
                        - MIN(date_order)
                    )
                ) / 86400.0
                    AS active_days

            FROM customer_orders

            GROUP BY customer_key
        )

        SELECT
            customer_key,
            customer_name,
            customer_vat,
            order_count,
            first_purchase,
            last_purchase,

            ROUND(
                active_days::numeric,
                2
            ) AS active_days,

            CASE
                WHEN order_count > 1
                THEN ROUND(
                    (
                        active_days
                        / (order_count - 1)
                    )::numeric,
                    2
                )
                ELSE NULL
            END AS avg_days_between_purchases

        FROM customer_frequency

        ORDER BY
            order_count DESC,
            avg_days_between_purchases ASC NULLS LAST,
            customer_name ASC
    """

    async with pool_getter().acquire() as conn:
        rows = await conn.fetch(
            sql,
            date_from_dt,
            date_to_dt,
            warehouse_filter,
            customer_filter,
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
        "customer": customer,
        "date_from": date_from,
        "date_to": date_to,
        "date_to_inclusive": False,
        "limit": limit,
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
                "No se encontraron compras de clientes "
                "para el período indicado."
            ),
            "filters": filters,
        }

    selected_rows = rows[:limit]

    customers = []

    for rank, row in enumerate(
        selected_rows,
        start=1,
    ):
        avg_days = (
            float(row["avg_days_between_purchases"])
            if row["avg_days_between_purchases"] is not None
            else None
        )

        avg_hours = (
            round(avg_days * 24, 2)
            if avg_days is not None
            else None
        )

        customers.append({
            "rank": rank,
            "customer_key": row["customer_key"],
            "customer_name": row["customer_name"],
            "customer_vat": row["customer_vat"],
            "order_count": row["order_count"],
            "first_purchase": (
                row["first_purchase"].isoformat()
                if row["first_purchase"]
                else None
            ),
            "last_purchase": (
                row["last_purchase"].isoformat()
                if row["last_purchase"]
                else None
            ),
            "active_days": (
                float(row["active_days"])
                if row["active_days"] is not None
                else None
            ),
            "avg_days_between_purchases": avg_days,
            "avg_hours_between_purchases": avg_hours,
        })

    answer_lines = []

    if customer_filter and len(customers) == 1:
        item = customers[0]

        if item["avg_days_between_purchases"] is None:
            frequency_text = (
                "solo registra una orden en el período"
            )
        else:
            frequency_text = (
                f"compra aproximadamente cada "
                f"{item['avg_days_between_purchases']:.2f} días "
                f"({item['avg_hours_between_purchases']:.2f} horas)"
            )

        answer_lines.append(
            (
                f"{item['customer_name']} registró "
                f"{item['order_count']} órdenes y "
                f"{frequency_text}."
            )
        )

    else:
        answer_lines.append(
            (
                f"Top {len(customers)} clientes "
                f"por frecuencia de compra:"
            )
        )

        for item in customers:
            if item["avg_days_between_purchases"] is None:
                freq = "sin frecuencia calculable"
            else:
                freq = (
                    f"cada "
                    f"{item['avg_days_between_purchases']:.2f} días"
                )

            answer_lines.append(
                (
                    f"{item['rank']}. "
                    f"{item['customer_name']}: "
                    f"{item['order_count']} órdenes, "
                    f"{freq}."
                )
            )

    summary = {
        "customer_count": len(rows),
        "total_orders": sum(
            row["order_count"]
            for row in rows
        ),
    }

    result = {
        "found": True,

        "answer": "\n".join(answer_lines),

        "definition": {
            "operation": "purchase_frequency",
            "table": "sale_order",
            "state": "sale",
            "date_field": "date_order",
            "customer_consolidation": (
                "RIF/VAT normalizado; "
                "fallback a partner_id."
            ),
            "frequency_formula": (
                "(last_purchase - first_purchase) "
                "/ (order_count - 1)"
            ),
            "sales_scope": "external_only",
            "intercompany_policy": (
                "Se excluyen clientes cuyo partner resuelto "
                "pertenece a res_company."
            ),
        },

        "filters": filters,
        "summary": summary,
        "customers": customers,
    }

    audit_write({
        "action": "sales_query",
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": filters,
        "duration_ms": duration_ms,
        "rows_returned": len(customers),
    })

    return result
