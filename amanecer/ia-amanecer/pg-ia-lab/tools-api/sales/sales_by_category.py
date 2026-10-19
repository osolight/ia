"""
F020 - Ventas por categoría.

Definición certificada:
- dimensión:
    product_template.categ_id -> product_category.id
- nombre mostrado:
    product_category.complete_name
- alcance:
    external_only
- excluye intercompany
- cantidad:
    sale_order_line.product_uom_qty
- monto:
    sale_order_line.price_subtotal
- USD directo
- VES convertido a USD con tasa de compañía
  y fallback a compañía matriz
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_sales_by_category(
    *,
    operation,
    request,
    pool_getter,
    audit_write,
    client_host,
    started,
    parse_iso_datetime,
    warehouse,
    category,
    date_from,
    date_to,
    conversion_date,
    limit,
):
    if not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail=(
                "sales_by_category requiere "
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

    category_filter = (
        category.strip()
        if category
        else None
    )

    sql = """
        WITH sales_lines AS (
            SELECT
                sol.id AS line_id,

                sol.product_uom_qty,
                sol.price_subtotal,

                so.company_id,
                so.currency_id,

                pc.id AS category_id,

                COALESCE(
                    NULLIF(pc.complete_name, ''),
                    NULLIF(pc.name, ''),
                    'SIN CATEGORIA'
                ) AS category_name

            FROM sale_order_line sol

            JOIN sale_order so
                ON so.id = sol.order_id

            JOIN product_product pp
                ON pp.id = sol.product_id

            JOIN product_template pt
                ON pt.id = pp.product_tmpl_id

            LEFT JOIN product_category pc
                ON pc.id = pt.categ_id

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

                    OR UPPER(
                        COALESCE(
                            NULLIF(pc.complete_name, ''),
                            NULLIF(pc.name, ''),
                            'SIN CATEGORIA'
                        )
                    ) LIKE
                       '%' || UPPER($5::text) || '%'
              )
        ),

        converted AS (
            SELECT
                s.category_id,
                s.category_name,
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

        category_totals AS (
            SELECT
                category_id,
                category_name,

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

            GROUP BY
                category_id,
                category_name
        )

        SELECT
            category_id,
            category_name,
            quantity,
            missing_rate_count,
            unsupported_currency_count,
            total_equivalent_usd

        FROM category_totals

        ORDER BY
            total_equivalent_usd DESC,
            quantity DESC,
            category_name ASC
    """

    async with pool_getter().acquire() as conn:
        rows = await conn.fetch(
            sql,
            date_from_dt,
            date_to_dt,
            warehouse_filter,
            conversion_date_obj,
            category_filter,
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
        "category": category,
        "date_from": date_from,
        "date_to": date_to,
        "date_to_inclusive": False,
        "conversion_date": str(
            conversion_date_obj
        ),
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
                "No se encontraron ventas por categoría "
                "para el período indicado."
            ),
            "filters": filters,
        }

    missing_rate_total = sum(
        row["missing_rate_count"]
        for row in rows
    )

    if missing_rate_total > 0:
        raise HTTPException(
            status_code=422,
            detail=(
                "Existen ventas en VES sin tasa "
                "de conversión disponible."
            ),
        )

    unsupported_currency_total = sum(
        row["unsupported_currency_count"]
        for row in rows
    )

    if unsupported_currency_total > 0:
        raise HTTPException(
            status_code=422,
            detail="Existen monedas no soportadas.",
        )

    selected_rows = rows[:limit]

    categories = []

    for rank, row in enumerate(
        selected_rows,
        start=1,
    ):
        categories.append({
            "rank": rank,
            "category_id": row["category_id"],
            "category_name": row["category_name"],
            "quantity": float(row["quantity"]),
            "total_equivalent_usd": float(
                row["total_equivalent_usd"]
            ),
        })

    if category_filter and len(categories) == 1:
        item = categories[0]

        answer = (
            f"{item['category_name']} registró "
            f"${item['total_equivalent_usd']:,.2f} USD "
            f"en ventas y "
            f"{item['quantity']:,.2f} unidades."
        )

    else:
        answer_lines = [
            (
                f"Top {len(categories)} categorías "
                f"por monto de ventas equivalente en USD:"
            )
        ]

        for item in categories:
            answer_lines.append(
                (
                    f"{item['rank']}. "
                    f"{item['category_name']}: "
                    f"${item['total_equivalent_usd']:,.2f} USD, "
                    f"{item['quantity']:,.2f} unidades."
                )
            )

        answer = "\n".join(answer_lines)

    result = {
        "found": True,

        "answer": answer,

        "definition": {
            "operation": "sales_by_category",
            "dimension": (
                "product_template.categ_id "
                "-> product_category.id"
            ),
            "category_name_field": (
                "product_category.complete_name"
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

        "summary": {
            "category_count": len(rows),

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
        },

        "categories": categories,
    }

    audit_write({
        "action": "sales_query",
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": filters,
        "duration_ms": duration_ms,
        "rows_returned": len(categories),
    })

    return result
