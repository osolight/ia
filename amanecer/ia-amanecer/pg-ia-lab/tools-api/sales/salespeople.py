"""
Operaciones de ventas relacionadas con vendedores.

Extraído de sales_tools.py sin modificar la lógica certificada.
"""


from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_top_salespeople(
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
    commercial_network,
    conversion_date,
    limit,
):

    if not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail=(
                "top_salespeople requiere "
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
    # Red comercial certificada
    #
    # ITC = almacenes comerciales PTDxx.
    # -----------------------------------------------------

    if commercial_network != "itc":
        raise HTTPException(
            status_code=400,
            detail=(
                "Red comercial no soportada. "
                "Actualmente solo está certificada: itc."
            ),
        )

    warehouse_code_pattern = "PTD%"

    # -----------------------------------------------------
    # Fecha de conversión VES -> USD
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

    warehouse_filter = (
        warehouse.strip()
        if warehouse
        else None
    )

    params = [
        date_from_dt,
        date_to_dt,
        warehouse_code_pattern,
        conversion_date_obj,
        warehouse_filter,
    ]

    sql = """
        WITH sales_period AS (
            SELECT
                so.id,
                so.company_id,
                so.employee_fdv_id,
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
              AND (
                    $5::text IS NULL
                    OR UPPER(sw.name) LIKE
                       '%' || UPPER($5::text) || '%'
              )
        ),

        converted AS (
            SELECT
                s.id,
                s.employee_fdv_id,
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

        salesperson_totals AS (
            SELECT
                he.id AS employee_id,
                he.name AS salesperson_name,

                COUNT(*) AS order_count,

                COUNT(*) FILTER (
                    WHERE c.currency_id = 167
                      AND c.usd_rate IS NULL
                ) AS missing_rate_count,

                COUNT(*) FILTER (
                    WHERE c.currency_id NOT IN (1, 167)
                ) AS unsupported_currency_count,

                COALESCE(
                    SUM(c.equivalent_usd),
                    0
                ) AS total_equivalent_usd

            FROM converted c

            JOIN hr_employee he
                ON he.id = c.employee_fdv_id

            GROUP BY
                he.id,
                he.name
        )

        SELECT
            employee_id,
            salesperson_name,
            order_count,
            missing_rate_count,
            unsupported_currency_count,
            total_equivalent_usd

        FROM salesperson_totals

        ORDER BY
            total_equivalent_usd DESC,
            order_count DESC,
            employee_id ASC
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
                "No se encontraron ventas asociadas "
                "a vendedores ITC para el período indicado."
            ),
        }

    # -----------------------------------------------------
    # Validar cobertura de moneda antes de rankear
    # -----------------------------------------------------

    missing_rate_total = sum(
        row["missing_rate_count"]
        for row in rows
    )

    if missing_rate_total > 0:
        raise HTTPException(
            status_code=422,
            detail=(
                "No es posible generar un ranking "
                "certificado de vendedores porque existen "
                "ventas en VES sin tasa de conversión."
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
                "certificado de vendedores porque existen "
                "monedas aún no soportadas."
            ),
        )

    # -----------------------------------------------------
    # Aplicar límite después de validar el conjunto completo
    # -----------------------------------------------------

    selected_rows = rows[:limit]

    salespeople = []

    for rank, row in enumerate(
        selected_rows,
        start=1,
    ):
        item = dict(row)

        item["rank"] = rank

        item["total_equivalent_usd_text"] = (
            f"$ "
            f"{item['total_equivalent_usd']:,.2f} "
            f"USD"
        )

        salespeople.append(item)

    answer_lines = [
        (
            f"Top {len(salespeople)} vendedores ITC "
            f"por monto de ventas equivalente en USD "
            f"para el período consultado:"
        )
    ]

    for item in salespeople:
        answer_lines.append(
            (
                f"{item['rank']}. "
                f"{item['salesperson_name']}: "
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
        "rows_returned": len(salespeople),
    })

    return {
        "found": True,
        "answer": answer,
        "commercial_network": commercial_network,
        "warehouse": warehouse,
        "conversion_date": str(
            conversion_date_obj
        ),
        "salespeople": salespeople,
    }
