"""
Ranking de sucursales comerciales.

Extraído de sales_tools.py sin modificar la lógica certificada.
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_top_warehouses(
    *,
    operation,
    request,
    pool_getter,
    audit_write,
    client_host,
    started,
    parse_iso_datetime,
    date_from,
    date_to,
    commercial_network,
    conversion_date,
    limit,
):

    if not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail=(
                "top_warehouses requiere "
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

    # ITC:
    # PTDxx = sucursales comerciales.
    #
    # Se excluyen PTERM, M-APT, consignados,
    # compras, plantas, Aurora Comercial
    # y otros almacenes técnicos.

    if commercial_network != "itc":
        raise HTTPException(
            status_code=400,
            detail=(
                "Red comercial no soportada. "
                "Actualmente solo está certificada: itc."
            ),
        )

    warehouse_code_pattern = "PTD%"

    # Conversión VES -> USD

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

    params = [
        date_from_dt,
        date_to_dt,
        warehouse_code_pattern,
        conversion_date_obj,
    ]

    sql = """
        WITH sales_period AS (
            SELECT
                so.id,
                so.company_id,
                so.warehouse_id,
                sw.code AS warehouse_code,
                sw.name AS warehouse_name,
                so.currency_id,
                so.amount_total

            FROM sale_order so

            JOIN stock_warehouse sw
                ON sw.id = so.warehouse_id

            WHERE so.state = 'sale'
              AND so.date_order IS NOT NULL
              AND so.date_order >= $1
              AND so.date_order < $2
              AND sw.code LIKE $3
        ),

        converted AS (
            SELECT
                s.id,
                s.warehouse_id,
                s.warehouse_code,
                s.warehouse_name,
                s.currency_id,
                s.amount_total,

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

        warehouse_totals AS (
            SELECT
                warehouse_id,
                warehouse_code,
                warehouse_name,

                COUNT(*) AS order_count,

                COUNT(*) FILTER (
                    WHERE currency_id = 1
                ) AS usd_order_count,

                COALESCE(
                    SUM(amount_total) FILTER (
                        WHERE currency_id = 1
                    ),
                    0
                ) AS amount_usd,

                COUNT(*) FILTER (
                    WHERE currency_id = 167
                ) AS ves_order_count,

                COALESCE(
                    SUM(amount_total) FILTER (
                        WHERE currency_id = 167
                    ),
                    0
                ) AS amount_ves,

                COUNT(*) FILTER (
                    WHERE currency_id = 167
                      AND usd_rate IS NULL
                ) AS missing_rate_count,

                COUNT(*) FILTER (
                    WHERE currency_id NOT IN (1, 167)
                ) AS unsupported_currency_count,

                COALESCE(
                    SUM(equivalent_usd),
                    0
                ) AS total_equivalent_usd

            FROM converted

            GROUP BY
                warehouse_id,
                warehouse_code,
                warehouse_name
        )

        SELECT
            warehouse_id,
            warehouse_code,
            warehouse_name,

            order_count,

            usd_order_count,
            amount_usd,

            ves_order_count,
            amount_ves,

            missing_rate_count,
            unsupported_currency_count,

            total_equivalent_usd

        FROM warehouse_totals

        ORDER BY
            total_equivalent_usd DESC,
            warehouse_id ASC
    """

    async with pool_getter().acquire() as conn:
        rows = await conn.fetch(
            sql,
            *params,
        )

    duration_ms = int(
        (
            datetime.now(timezone.utc)
            - started
        ).total_seconds() * 1000
    )

    filters = {
        "operation": operation,
        "commercial_network": commercial_network,
        "date_from": date_from,
        "date_to": date_to,
        "date_to_inclusive": False,
        "conversion_date": str(
            conversion_date_obj
        ),
        "limit": limit,
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
                "No se encontraron ventas de sucursales "
                "ITC para el período indicado."
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
                "No es posible generar un ranking "
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
                "No es posible generar un ranking "
                "certificado porque existen ventas en "
                "monedas aún no soportadas."
            ),
        )

    selected_rows = rows[:limit]

    warehouses = []

    for rank, row in enumerate(
        selected_rows,
        start=1,
    ):
        item = dict(row)

        item["rank"] = rank

        item["amount_usd_text"] = (
            f"$ {item['amount_usd']:,.4f} USD"
        )

        item["amount_ves_text"] = (
            f"VES {item['amount_ves']:,.4f}"
        )

        item["total_equivalent_usd_text"] = (
            f"$ "
            f"{item['total_equivalent_usd']:,.2f} "
            f"USD"
        )

        warehouses.append(item)

    answer_lines = [
        (
            f"Top {len(warehouses)} sucursales comerciales "
            f"ITC por ventas equivalentes en USD "
            f"para el período consultado:"
        )
    ]

    for item in warehouses:
        answer_lines.append(
            (
                f"{item['rank']}. "
                f"{item['warehouse_name']}: "
                f"{item['order_count']:,} órdenes, "
                f"{item['total_equivalent_usd_text']}."
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
        "rows_returned": len(warehouses),
    })

    return {
        "found": True,
        "answer": answer,
        "commercial_network": commercial_network,
        "conversion_date": str(
            conversion_date_obj
        ),
        "warehouses": warehouses,
    }
