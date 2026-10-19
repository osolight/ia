"""
Ventas por estado geográfico.

Agrupa órdenes confirmadas por:
sale_order.zone_id -> zones.state_id -> res_country_state.name
y utiliza la política certificada de conversión VES -> USD.
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_sales_by_state(
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
                "sales_by_state requiere "
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
        conversion_date_obj,
    ]

    warehouse_filter = ""

    if warehouse:
        warehouse = warehouse.strip()

        if warehouse:
            params.append(f"%{warehouse}%")
            warehouse_filter = (
                f"AND sw.name ILIKE ${len(params)}"
            )
        else:
            warehouse = None

    sql = f"""
        WITH sales_period AS (
            SELECT
                so.id,
                so.company_id,
                rcs.id AS state_id,
                rcs.name AS state_name,
                so.currency_id,
                so.amount_total

            FROM sale_order so

            LEFT JOIN zones z
                ON z.id = so.zone_id

            LEFT JOIN res_country_state rcs
                ON rcs.id = z.state_id

            LEFT JOIN stock_warehouse sw
                ON sw.id = so.warehouse_id

            WHERE so.state = 'sale'
              AND so.date_order IS NOT NULL
              AND so.date_order >= $1
              AND so.date_order < $2
              {warehouse_filter}
        ),

        converted AS (
            SELECT
                s.id,
                s.state_id,
                s.state_name,
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
                  AND r.name <= $3::date
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
                  AND r.name <= $3::date
                ORDER BY
                    r.name DESC,
                    r.id DESC
                LIMIT 1
            ) parent_rate ON TRUE
        ),

        state_totals AS (
            SELECT
                state_id,

                COALESCE(
                    state_name,
                    'SIN ESTADO ASIGNADO'
                ) AS state_name,

                COUNT(*) AS order_count,

                COUNT(*) FILTER (
                    WHERE currency_id = 1
                ) AS usd_order_count,

                COUNT(*) FILTER (
                    WHERE currency_id = 167
                ) AS ves_order_count,

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
                state_id,
                state_name
        )

        SELECT
            state_id,
            state_name,
            order_count,
            usd_order_count,
            ves_order_count,
            missing_rate_count,
            unsupported_currency_count,
            total_equivalent_usd

        FROM state_totals

        ORDER BY
            total_equivalent_usd DESC,
            state_id ASC NULLS FIRST
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
        "warehouse": warehouse,
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
                "No se encontraron ventas "
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
    states = []

    for rank, row in enumerate(
        selected_rows,
        start=1,
    ):
        item = dict(row)

        item["rank"] = rank
        item["total_equivalent_usd_text"] = (
            f"$ {float(item['total_equivalent_usd']):,.2f} USD"
        )

        states.append(item)

    audit_write({
        "action": "sales_query",
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": filters,
        "duration_ms": duration_ms,
        "rows_returned": len(states),
    })

    leader = states[0]

    answer = (
        f"El estado con mayor monto de ventas en el período "
        f"es {leader['state_name']}, con "
        f"{leader['order_count']:,} órdenes confirmadas y "
        f"{leader['total_equivalent_usd_text']} "
        f"de monto equivalente."
    )

    return {
        "found": True,
        "answer": answer,
        "filters": filters,
        "states": states,
    }
