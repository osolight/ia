"""
Ranking de productos por cantidad o monto.

Extraído de sales_tools.py sin modificar la lógica certificada.
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_top_products(
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
    sort_by,
    conversion_date,
    limit,
):

    if not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail=(
                "top_products requiere "
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
            detail=(
                "date_to debe ser posterior "
                "a date_from."
            ),
        )

    # -----------------------------------------------------
    # Fecha de conversión.
    #
    # Solo es necesaria para sort_by='amount',
    # pero se normaliza aquí para mantener salida consistente.
    # -----------------------------------------------------

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

    where_clauses = [
        "so.state = 'sale'",
        "so.date_order IS NOT NULL",
        "so.date_order >= $1",
        "so.date_order < $2",
    ]

    params = [
        date_from_dt,
        date_to_dt,
    ]

    if warehouse:
        warehouse = warehouse.strip()

        if warehouse:
            params.append(f"%{warehouse}%")
            where_clauses.append(
                f"sw.name ILIKE ${len(params)}"
            )
        else:
            warehouse = None

    where_sql = "\n                  AND ".join(
        where_clauses
    )

    # =====================================================
    # RANKING POR CANTIDAD
    # =====================================================

    if sort_by == "quantity":

        params.append(limit)
        limit_index = len(params)

        sql = f"""
            WITH base AS (
                SELECT
                    pp.id AS product_id,

                    COALESCE(
                        pt.name ->> 'es_VE',
                        pt.name ->> 'en_US',
                        pt.name::text
                    ) AS product_name,

                    sol.product_uom_qty AS qty_sold,
                    sol.price_total AS amount_total,

                    so.currency_id

                FROM sale_order_line sol

                JOIN sale_order so
                    ON so.id = sol.order_id

                JOIN product_product pp
                    ON pp.id = sol.product_id

                JOIN product_template pt
                    ON pt.id = pp.product_tmpl_id

                LEFT JOIN stock_warehouse sw
                    ON sw.id = so.warehouse_id

                WHERE {where_sql}
            ),

            product_totals AS (
                SELECT
                    product_id,
                    product_name,

                    SUM(qty_sold)
                        AS qty_sold

                FROM base

                GROUP BY
                    product_id,
                    product_name

                HAVING SUM(qty_sold) > 0
            ),

            top_products AS (
                SELECT
                    product_id,
                    product_name,
                    qty_sold

                FROM product_totals

                ORDER BY
                    qty_sold DESC,
                    product_id ASC

                LIMIT ${limit_index}
            )

            SELECT
                tp.product_id,
                tp.product_name,
                tp.qty_sold,

                b.currency_id,
                rc.name AS currency_name,
                rc.symbol AS currency_symbol,

                COALESCE(
                    SUM(b.amount_total),
                    0
                ) AS amount_total

            FROM top_products tp

            JOIN base b
                ON b.product_id = tp.product_id

            LEFT JOIN res_currency rc
                ON rc.id = b.currency_id

            GROUP BY
                tp.product_id,
                tp.product_name,
                tp.qty_sold,
                b.currency_id,
                rc.name,
                rc.symbol

            ORDER BY
                tp.qty_sold DESC,
                tp.product_id ASC,
                b.currency_id ASC
        """

        async with pool_getter().acquire() as conn:
            rows = await conn.fetch(
                sql,
                *params,
            )

        if not rows:
            products = []

        else:
            products_by_id = {}

            for row in rows:
                item = dict(row)

                product_id = item["product_id"]

                if product_id not in products_by_id:
                    products_by_id[product_id] = {
                        "rank": len(products_by_id) + 1,
                        "product_id": product_id,
                        "product_name": item["product_name"],
                        "qty_sold": item["qty_sold"],
                        "amounts_by_currency": [],
                    }

                currency_name = (
                    item.get("currency_name")
                    or ""
                )

                currency_symbol = (
                    item.get("currency_symbol")
                    or currency_name
                )

                amount_total = item["amount_total"]

                products_by_id[product_id][
                    "amounts_by_currency"
                ].append({
                    "currency_id": item.get("currency_id"),
                    "currency_name": currency_name,
                    "currency_symbol": currency_symbol,
                    "amount_total": amount_total,
                    "amount_total_text": (
                        f"{currency_symbol} "
                        f"{amount_total:,.4f} "
                        f"{currency_name}"
                    ).strip(),
                })

            products = list(
                products_by_id.values()
            )

    # =====================================================
    # RANKING POR MONTO
    # =====================================================

    else:

        conversion_params = list(params)
        conversion_params.append(
            conversion_date_obj
        )
        conversion_date_index = len(
            conversion_params
        )

        conversion_params.append(limit)
        limit_index = len(
            conversion_params
        )

        sql = f"""
            WITH base AS (
                SELECT
                    pp.id AS product_id,

                    COALESCE(
                        pt.name ->> 'es_VE',
                        pt.name ->> 'en_US',
                        pt.name::text
                    ) AS product_name,

                    sol.product_uom_qty AS qty_sold,
                    sol.price_total AS amount_total,

                    so.currency_id,
                    so.company_id

                FROM sale_order_line sol

                JOIN sale_order so
                    ON so.id = sol.order_id

                JOIN product_product pp
                    ON pp.id = sol.product_id

                JOIN product_template pt
                    ON pt.id = pp.product_tmpl_id

                LEFT JOIN stock_warehouse sw
                    ON sw.id = so.warehouse_id

                WHERE {where_sql}
            ),

            converted AS (
                SELECT
                    b.product_id,
                    b.product_name,
                    b.qty_sold,
                    b.amount_total,
                    b.currency_id,

                    CASE
                        WHEN b.currency_id = 1
                        THEN b.amount_total

                        WHEN b.currency_id = 167
                        THEN (
                            b.amount_total
                            *
                            COALESCE(
                                own_rate.rate,
                                parent_rate.rate
                            )
                        )

                        ELSE NULL
                    END AS equivalent_usd,

                    CASE
                        WHEN b.currency_id = 167
                        THEN COALESCE(
                            own_rate.rate,
                            parent_rate.rate
                        )

                        ELSE NULL
                    END AS usd_rate

                FROM base b

                JOIN res_company c
                    ON c.id = b.company_id

                LEFT JOIN LATERAL (
                    SELECT r.rate

                    FROM res_currency_rate r

                    WHERE r.currency_id = 1
                      AND r.company_id = b.company_id
                      AND r.name <= ${conversion_date_index}::date

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
                      AND r.name <= ${conversion_date_index}::date

                    ORDER BY
                        r.name DESC,
                        r.id DESC

                    LIMIT 1

                ) parent_rate ON TRUE
            ),

            product_totals AS (
                SELECT
                    product_id,
                    product_name,

                    SUM(qty_sold)
                        AS qty_sold,

                    COUNT(*) FILTER (
                        WHERE currency_id = 167
                          AND usd_rate IS NULL
                    ) AS missing_rate_count,

                    COUNT(*) FILTER (
                        WHERE currency_id NOT IN (1, 167)
                    ) AS unsupported_currency_count,

                    SUM(equivalent_usd)
                        AS total_equivalent_usd

                FROM converted

                GROUP BY
                    product_id,
                    product_name

                HAVING SUM(qty_sold) > 0
            )

            SELECT
                product_id,
                product_name,
                qty_sold,
                missing_rate_count,
                unsupported_currency_count,
                total_equivalent_usd

            FROM product_totals

            ORDER BY
                total_equivalent_usd DESC,
                qty_sold DESC,
                product_id ASC

            LIMIT ${limit_index}
        """

        async with pool_getter().acquire() as conn:
            rows = await conn.fetch(
                sql,
                *conversion_params,
            )

        missing_rate_total = sum(
            row["missing_rate_count"]
            for row in rows
        )

        if missing_rate_total > 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    "No es posible generar un ranking "
                    "certificado por monto porque existen "
                    "ventas en VES sin tasa disponible."
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
                    "No es posible generar un ranking "
                    "certificado por monto porque existen "
                    "monedas aún no soportadas."
                ),
            )

        products = []

        for rank, row in enumerate(
            rows,
            start=1,
        ):
            item = dict(row)

            item["rank"] = rank

            item["total_equivalent_usd_text"] = (
                f"$ "
                f"{item['total_equivalent_usd']:,.2f} "
                f"USD"
            )

            products.append(item)

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
        "sort_by": sort_by,
        "limit": limit,
    }

    if sort_by == "amount":
        filters["conversion_date"] = str(
            conversion_date_obj
        )

    if not products:
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
                "No se encontraron productos vendidos "
                "para los filtros indicados."
            ),
        }

    location_text = (
        warehouse
        if warehouse
        else "todas las sucursales"
    )

    if sort_by == "quantity":

        answer_lines = [
            (
                f"Top {len(products)} productos más vendidos "
                f"por cantidad en {location_text} "
                f"para el período consultado:"
            )
        ]

        for product in products:
            answer_lines.append(
                (
                    f"{product['rank']}. "
                    f"{product['product_name']}: "
                    f"{product['qty_sold']:,.4f} unidades."
                )
            )

    else:

        answer_lines = [
            (
                f"Top {len(products)} productos por monto "
                f"equivalente en USD en {location_text} "
                f"para el período consultado:"
            )
        ]

        for product in products:
            answer_lines.append(
                (
                    f"{product['rank']}. "
                    f"{product['product_name']}: "
                    f"{product['qty_sold']:,.4f} unidades, "
                    f"{product['total_equivalent_usd_text']}."
                )
            )

    answer = "\n".join(
        answer_lines
    )

    audit_write({
        "action": "sales_query",
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": filters,
        "duration_ms": duration_ms,
        "rows_returned": len(products),
    })

    return {
        "found": True,
        "answer": answer,
        "sort_by": sort_by,
        "products": products,
    }
