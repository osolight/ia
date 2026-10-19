"""
F016 - Ventas por producto.

Alcance gerencial general:
- sale_order.state = sale
- clientes externos solamente
- excluye intercompany
- dimensión: sale_order_line.product_id
- cantidad: product_uom_qty
- monto: price_subtotal
- USD directo
- VES convertido a USD con tasa de compañía y fallback a matriz
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_sales_by_product(
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
                "sales_by_product requiere "
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
        WITH sales_lines AS (
            SELECT
                sol.id AS line_id,
                so.id AS order_id,
                sol.product_id,

                COALESCE(
                    pt.name ->> 'es_VE',
                    pt.name ->> 'en_US',
                    pt.name::text
                ) AS product_name,

                sol.product_uom_qty,
                sol.price_subtotal,

                so.company_id,
                so.currency_id

            FROM sale_order_line sol

            JOIN sale_order so
                ON so.id = sol.order_id

            JOIN product_product pp
                ON pp.id = sol.product_id

            JOIN product_template pt
                ON pt.id = pp.product_tmpl_id

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
        ),

        converted AS (
            SELECT
                s.line_id,
                s.order_id,
                s.product_id,
                s.product_name,
                s.product_uom_qty,
                s.currency_id,

                CASE
                    WHEN s.currency_id = 1
                    THEN s.price_subtotal

                    WHEN s.currency_id = 167
                    THEN (
                        s.price_subtotal
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

            FROM sales_lines s

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

        product_totals AS (
            SELECT
                product_id,
                MAX(product_name) AS product_name,

                COUNT(*) AS line_count,
                COUNT(DISTINCT order_id) AS order_count,

                ROUND(
                    SUM(product_uom_qty)::numeric,
                    2
                ) AS quantity,

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

            GROUP BY product_id
        )

        SELECT
            product_id,
            product_name,
            line_count,
            order_count,
            quantity,
            missing_rate_count,
            unsupported_currency_count,
            total_equivalent_usd

        FROM product_totals

        ORDER BY
            total_equivalent_usd DESC,
            quantity DESC,
            product_id ASC
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
                "por producto para el período indicado."
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
                "certificado porque existen líneas en VES "
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

    products = []

    for rank, row in enumerate(
        selected_rows,
        start=1,
    ):
        item = dict(row)

        item["rank"] = rank
        item["quantity"] = float(
            item["quantity"]
        )
        item["total_equivalent_usd"] = float(
            item["total_equivalent_usd"]
        )

        products.append(item)

    summary = {
        "product_count": len(rows),
        "total_quantity": round(
            sum(
                float(row["quantity"])
                for row in rows
            ),
            2,
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
            f"Top {len(products)} productos "
            f"por monto de ventas equivalente en USD:"
        )
    ]

    for item in products:
        answer_lines.append(
            (
                f"{item['rank']}. "
                f"{item['product_name']}: "
                f"${item['total_equivalent_usd']:,.2f} USD, "
                f"{item['quantity']:,.2f} unidades."
            )
        )

    result = {
        "found": True,
        "answer": "\n".join(answer_lines),

        "definition": {
            "operation": "sales_by_product",
            "table": (
                "sale_order + sale_order_line"
            ),
            "state": "sale",
            "date_field": "sale_order.date_order",
            "dimension": (
                "sale_order_line.product_id -> "
                "product_product -> product_template"
            ),
            "quantity_field": (
                "sale_order_line.product_uom_qty"
            ),
            "amount_field": (
                "sale_order_line.price_subtotal"
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
        "products": products,
    }

    audit_write({
        "action": "sales_query",
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": filters,
        "duration_ms": duration_ms,
        "rows_returned": len(products),
    })

    return result
