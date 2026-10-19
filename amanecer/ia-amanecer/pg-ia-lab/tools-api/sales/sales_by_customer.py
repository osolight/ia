"""
F014 - Ventas por cliente
Grupo Amanecer

Reglas certificadas:
- sale_order.state = 'sale'
- Cliente gerencial = RIF/VAT normalizado.
- Si no existe RIF/VAT, fallback a partner_id.
- Se excluyen operaciones intercompany.
- USD se conserva.
- VES se convierte a USD utilizando tasa de la compañía
  y fallback a la compañía matriz.
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_sales_by_customer(
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
    conversion_date,
    limit,
):
    if not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail="sales_by_customer requiere date_from y date_to.",
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

    if conversion_date:
        try:
            conversion_date_obj = datetime.strptime(
                conversion_date,
                "%Y-%m-%d",
            ).date()

        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(
                    "conversion_date debe utilizar formato "
                    "YYYY-MM-DD."
                ),
            )
    else:
        conversion_date_obj = datetime.now(
            timezone.utc
        ).date()

    warehouse_filter = (
        warehouse.strip()
        if warehouse
        else None
    )

    sql = """
        WITH sales_period AS (
            SELECT
                so.id,
                so.partner_id,
                so.company_id,
                so.currency_id,
                so.amount_total,

                rp.name AS customer_name,

                NULLIF(
                    regexp_replace(
                        UPPER(TRIM(rp.vat)),
                        '[^A-Z0-9]',
                        '',
                        'g'
                    ),
                    ''
                ) AS customer_vat

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
                $5::text IS NULL
                OR UPPER(rp.name) LIKE
                   '%' || UPPER($5::text) || '%'
                OR regexp_replace(
                    UPPER(COALESCE(rp.vat, '')),
                    '[^A-Z0-9]',
                    '',
                    'g'
                ) LIKE
                   '%' || regexp_replace(
                       UPPER($5::text),
                       '[^A-Z0-9]',
                       '',
                       'g'
                   ) || '%'
            )

        ),

        converted AS (
            SELECT
                s.id,
                s.partner_id,
                s.customer_name,
                s.customer_vat,
                s.currency_id,
                s.amount_total,

                CASE
                    WHEN s.currency_id = 1
                        THEN s.amount_total

                    WHEN s.currency_id = 167
                        THEN s.amount_total * COALESCE(
                            own_rate.rate,
                            parent_rate.rate
                        )

                    ELSE NULL
                END AS equivalent_usd,

                CASE
                    WHEN s.currency_id = 167
                        THEN COALESCE(
                            own_rate.rate,
                            parent_rate.rate
                        )

                    ELSE NULL
                END AS usd_rate

            FROM sales_period s

            JOIN res_company c
                ON c.id = s.company_id

            LEFT JOIN LATERAL (
                SELECT
                    r.rate

                FROM res_currency_rate r

                WHERE r.currency_id = 1
                  AND r.company_id = s.company_id
                  AND r.name <= $4::date

                ORDER BY
                    r.name DESC,
                    r.id DESC

                LIMIT 1
            ) own_rate ON TRUE

            LEFT JOIN LATERAL (
                SELECT
                    r.rate

                FROM res_currency_rate r

                WHERE r.currency_id = 1
                  AND r.company_id = c.parent_id
                  AND r.name <= $4::date

                ORDER BY
                    r.name DESC,
                    r.id DESC

                LIMIT 1
            ) parent_rate ON TRUE
        ),

        customer_totals AS (
            SELECT
                COALESCE(
                    customer_vat,
                    'PARTNER:' || partner_id::text
                ) AS customer_key,

                MIN(customer_name) AS customer_name,

                customer_vat,

                COUNT(*) AS order_count,

                COUNT(
                    DISTINCT partner_id
                ) AS partner_records,

                COUNT(*) FILTER (
                    WHERE currency_id = 167
                      AND usd_rate IS NULL
                ) AS missing_rate_count,

                COUNT(*) FILTER (
                    WHERE currency_id NOT IN (1, 167)
                ) AS unsupported_currency_count,

		ROUND(
                COALESCE(
                SUM(equivalent_usd),
                0
                )::numeric,
                2
        ) AS total_equivalent_usd

            FROM converted

            GROUP BY
                COALESCE(
                    customer_vat,
                    'PARTNER:' || partner_id::text
                ),
                customer_vat
        )

        SELECT
            customer_key,
            customer_name,
            customer_vat,
            order_count,
            partner_records,
            missing_rate_count,
            unsupported_currency_count,
            total_equivalent_usd

        FROM customer_totals

        ORDER BY
            total_equivalent_usd DESC,
            order_count DESC,
            customer_key ASC
    """

    async with pool_getter().acquire() as conn:
        rows = await conn.fetch(
            sql,
            date_from_dt,
            date_to_dt,
            warehouse_filter,
            conversion_date_obj,
                customer,
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
        "conversion_date": str(
            conversion_date_obj
        ),
        "limit": limit,
        "customer_scope": "external_only",
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
                "No se encontraron ventas externas "
                "para el período indicado."
            ),
        }

    missing_rate_total = sum(
        row["missing_rate_count"]
        for row in rows
    )

    if missing_rate_total > 0:
        raise HTTPException(
            status_code=422,
            detail=(
                "No es posible generar un resultado "
                "certificado porque existen órdenes en VES "
                "sin tasa de conversión disponible."
            ),
        )

    unsupported_currency_total = sum(
        row["unsupported_currency_count"]
        for row in rows
    )

    if unsupported_currency_total > 0:
        raise HTTPException(
            status_code=422,
            detail=(
                "No es posible generar un resultado "
                "certificado porque existen ventas en "
                "monedas aún no soportadas."
            ),
        )

    selected_rows = rows[:limit]

    customers = []

    for rank, row in enumerate(
        selected_rows,
        start=1,
    ):
        customers.append({
            "rank": rank,
            "customer_key": row["customer_key"],
            "customer_name": row["customer_name"],
            "customer_vat": row["customer_vat"],
            "order_count": row["order_count"],
            "partner_records": row["partner_records"],
            "total_equivalent_usd": float(
                row["total_equivalent_usd"]
            ),
        })

    total_orders = sum(
        row["order_count"]
        for row in rows
    )

    total_equivalent_usd = sum(
        float(row["total_equivalent_usd"])
        for row in rows
    )

    answer_lines = [
        (
            f"Top {len(customers)} clientes "
            f"por monto de ventas equivalente en USD:"
        )
    ]

    for item in customers:
        answer_lines.append(
            (
                f"{item['rank']}. "
                f"{item['customer_name']}: "
                f"${item['total_equivalent_usd']:,.2f} USD "
                f"en {item['order_count']:,} órdenes."
            )
        )

    answer = "\n".join(answer_lines)

    audit_write({
        "action": "sales_query",
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": filters,
        "duration_ms": duration_ms,
        "rows_returned": len(customers),
    })

    return {
        "found": True,

        "answer": answer,

        "definition": {
            "operation": "sales_by_customer",
            "table": "sale_order",
            "state": "sale",
            "date_field": "date_order",
            "dimension": (
                "RIF/VAT normalizado; fallback a partner_id"
            ),
            "customer_scope": "external_only",
            "intercompany_policy": (
                "Se excluyen clientes cuyo partner resuelto "
                "pertenece a res_company."
            ),
            "currency_policy": (
                "USD se conserva. VES se convierte a USD "
                "usando la tasa de la compañía y fallback "
                "a su compañía matriz."
            ),
        },

        "filters": filters,

        "summary": {
            "customer_count": len(rows),
            "order_count": total_orders,
            "total_equivalent_usd": total_equivalent_usd,
        },

        "customers": customers,
    }
