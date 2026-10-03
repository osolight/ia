"""
Ranking gerencial de clientes.

Regla provisional de negocio:
- Cliente gerencial = RIF/VAT consolidado.
- Si el RIF/VAT está vacío, se utiliza partner_id como fallback.
- Las distintas sedes/puntos de entrega pueden tener partner_id distintos.
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_top_customers(
    *,
    operation,
    request,
    pool_getter,
    audit_write,
    client_host,
    started,
    parse_iso_datetime,
    warehouse,
    state,
    zone,
    date_from,
    date_to,
    commercial_network,
    conversion_date,
    limit,
):
    if not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail="top_customers requiere date_from y date_to.",
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

    if commercial_network != "itc":
        raise HTTPException(
            status_code=400,
            detail=(
                "Red comercial no soportada. "
                "Actualmente solo está certificada: itc."
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

    warehouse_filter = warehouse.strip() if warehouse else None

    state_filter = state.strip() if state else None
    zone_filter = zone.strip() if zone else None
    sql = """
        WITH sales_period AS (
            SELECT
                so.id,
                so.company_id,
                so.partner_id,
                so.currency_id,
                so.amount_total,

                rp.name AS customer_name,

                NULLIF(
                    UPPER(
                        REPLACE(
                            REPLACE(
                                TRIM(rp.vat),
                                ' ',
                                ''
                            ),
                            '.',
                            ''
                        )
                    ),
                    ''
                ) AS customer_vat

            FROM sale_order so

            JOIN stock_warehouse sw
                ON sw.id = so.warehouse_id

            JOIN res_partner rp
                ON rp.id = so.partner_id


            LEFT JOIN zones z
                ON z.id = so.zone_id

            LEFT JOIN res_country_state rcs
                ON rcs.id = z.state_id
            WHERE so.state = 'sale'
              AND so.date_order IS NOT NULL
              AND so.date_order >= $1
              AND so.date_order < $2
              AND sw.code LIKE 'PTD%'

              AND (
                    $3::text IS NULL
                    OR UPPER(sw.name) LIKE
                       '%' || UPPER($3::text) || '%'
              )

              AND (
                    $5::text IS NULL
                    OR UPPER(rcs.name) = UPPER($5::text)
              )

              AND (
                    $6::text IS NULL
                    OR UPPER(z.name) = UPPER($6::text)
              )
        ),

        converted AS (
            SELECT
                s.*,

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

                COALESCE(
                    SUM(equivalent_usd),
                    0
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
            state_filter,
            zone_filter,
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
        "state": state,
        "zone": zone,
        "date_from": date_from,
        "date_to": date_to,
        "date_to_inclusive": False,
        "conversion_date": str(conversion_date_obj),
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
                "No se encontraron ventas de clientes ITC "
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
                "No es posible generar un ranking certificado "
                "de clientes porque existen ventas en VES "
                "sin tasa de conversión."
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
                "No es posible generar un ranking certificado "
                "de clientes porque existen monedas "
                "aún no soportadas."
            ),
        )

    selected_rows = rows[:limit]

    customers = []

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

        customers.append(item)

    answer_lines = [
        (
            f"Top {len(customers)} clientes ITC "
            f"por monto de ventas equivalente en USD:"
        )
    ]

    for item in customers:
        answer_lines.append(
            (
                f"{item['rank']}. "
                f"{item['customer_name']}: "
                f"{item['total_equivalent_usd_text']} "
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
        "commercial_network": commercial_network,
        "warehouse": warehouse,
        "state": state,
        "zone": zone,
        "conversion_date": str(conversion_date_obj),
        "customers": customers,
    }
