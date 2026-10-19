"""
F015 - Ventas por vendedor.

Alcance gerencial general:
- sale_order.state = sale
- agrupación por employee_fdv_id / hr_employee
- clientes externos solamente
- excluye intercompany
- USD directo
- VES convertido a USD con tasa de compañía y fallback a matriz
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_sales_by_salesperson(
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
    conversion_date,
    limit,
):
    if not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail=(
                "sales_by_salesperson requiere "
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
                so.company_id,
                so.employee_fdv_id,
                so.currency_id,
                so.amount_total,
                he.name AS salesperson_name

            FROM sale_order so

            JOIN hr_employee he
                ON he.id = so.employee_fdv_id

            JOIN res_partner rp
                ON rp.id = so.partner_id

            LEFT JOIN stock_warehouse sw
                ON sw.id = so.warehouse_id

            WHERE so.state = 'sale'
              AND so.date_order IS NOT NULL
              AND so.date_order >= $1
              AND so.date_order < $2
              AND so.employee_fdv_id IS NOT NULL

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

        converted AS (
            SELECT
                s.id,
                s.employee_fdv_id,
                s.salesperson_name,
                s.currency_id,

                CASE
                    WHEN s.currency_id = 1
                    THEN s.amount_total

                    WHEN s.currency_id = 167
                    THEN (
                        s.amount_total
                        *
                        COALESCE(
                            own_rate.rate,
                            parent_rate.rate
                        )
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
                SELECT r.rate

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
                SELECT r.rate

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

        salesperson_totals AS (
            SELECT
                employee_fdv_id AS salesperson_id,
                MAX(salesperson_name) AS salesperson_name,

                COUNT(*) AS order_count,

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
                employee_fdv_id
        )

        SELECT
            salesperson_id,
            salesperson_name,
            order_count,
            missing_rate_count,
            unsupported_currency_count,
            total_equivalent_usd

        FROM salesperson_totals

        ORDER BY
            total_equivalent_usd DESC,
            order_count DESC,
            salesperson_id ASC
    """

    async with pool_getter().acquire() as conn:
        rows = await conn.fetch(
            sql,
            date_from_dt,
            date_to_dt,
            warehouse_filter,
            conversion_date_obj,
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
        "conversion_date": str(conversion_date_obj),
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
                "No se encontraron ventas externas "
                "asociadas a vendedores para el período indicado."
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
                "certificado porque existen ventas en VES "
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
                "certificado porque existen monedas "
                "aún no soportadas."
            ),
        )

    selected_rows = rows[:limit]

    salespeople = []

    for rank, row in enumerate(
        selected_rows,
        start=1,
    ):
        item = dict(row)

        item["rank"] = rank
        item["total_equivalent_usd"] = float(
            item["total_equivalent_usd"]
        )

        salespeople.append(item)

    summary = {
        "salesperson_count": len(rows),
        "order_count": sum(
            row["order_count"]
            for row in rows
        ),
        "total_equivalent_usd": round(
            sum(
                float(row["total_equivalent_usd"])
                for row in rows
            ),
            2,
        ),
    }

    answer_lines = [
        (
            f"Top {len(salespeople)} vendedores "
            f"por monto de ventas equivalente en USD:"
        )
    ]

    for item in salespeople:
        answer_lines.append(
            (
                f"{item['rank']}. "
                f"{item['salesperson_name']}: "
                f"${item['total_equivalent_usd']:,.2f} USD "
                f"en {item['order_count']} órdenes."
            )
        )

    result = {
        "found": True,
        "answer": "\n".join(answer_lines),
        "definition": {
            "operation": "sales_by_salesperson",
            "table": "sale_order",
            "state": "sale",
            "date_field": "date_order",
            "dimension": (
                "sale_order.employee_fdv_id -> hr_employee"
            ),
            "sales_scope": "external_only",
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
        "summary": summary,
        "salespeople": salespeople,
    }

    audit_write({
        "action": "sales_query",
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": filters,
        "duration_ms": duration_ms,
        "rows_returned": len(salespeople),
    })

    return result
