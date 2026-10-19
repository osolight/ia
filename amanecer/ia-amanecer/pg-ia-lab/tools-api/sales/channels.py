"""
Ventas externas por canal comercial.

F013 - sales_by_channel

Reglas validadas:
- Fuente: sale_order
- Solo órdenes confirmadas: state = 'sale'
- Fecha: sale_order.date_order
- Canal: sale_order.cons_channel_id -> cons_channel.name
- Canal nulo: SIN CANAL
- Excluye ventas intercompañía según el cliente
- USD: currency_id = 1
- VES: currency_id = 167
- VES se convierte a USD usando la tasa USD vigente
- La tasa NO se restringe por sale_order.company_id
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_sales_by_channel(
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
                "sales_by_channel requiere "
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
                so.cons_channel_id,
                cc.name AS channel_name,
                so.currency_id,
                so.amount_total

            FROM sale_order so

            JOIN res_partner rp
                ON rp.id = so.partner_id

            LEFT JOIN cons_channel cc
                ON cc.id = so.cons_channel_id

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

              {warehouse_filter}
        ),

        usd_rate AS (
            SELECT
                r.rate,
                r.name AS rate_date

            FROM res_currency_rate r

            WHERE r.currency_id = 1
              AND r.name <= $3::date

            ORDER BY
                r.name DESC,
                r.company_id ASC NULLS LAST,
                r.id DESC

            LIMIT 1
        ),

        converted AS (
            SELECT
                s.id,
                s.cons_channel_id,
                s.channel_name,
                s.currency_id,
                s.amount_total,

                CASE
                    WHEN s.currency_id = 1
                    THEN s.amount_total

                    WHEN s.currency_id = 167
                    THEN (
                        s.amount_total
                        * ur.rate
                    )

                    ELSE NULL
                END AS equivalent_usd,

                CASE
                    WHEN s.currency_id = 167
                    THEN ur.rate
                    ELSE NULL
                END AS usd_rate

            FROM sales_period s

            LEFT JOIN usd_rate ur
                ON TRUE
        ),

        channel_totals AS (
            SELECT
                cons_channel_id AS channel_id,

                COALESCE(
                    channel_name,
                    'SIN CANAL'
                ) AS channel_name,

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
                cons_channel_id,
                channel_name
        )

        SELECT
            channel_id,
            channel_name,
            order_count,

            usd_order_count,
            amount_usd,

            ves_order_count,
            amount_ves,

            missing_rate_count,
            unsupported_currency_count,

            total_equivalent_usd

        FROM channel_totals

        ORDER BY
            total_equivalent_usd DESC,
            channel_id ASC NULLS LAST
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

    channels = []

    for rank, row in enumerate(
        selected_rows,
        start=1,
    ):
        channels.append({
            "rank": rank,
            "channel_id": row["channel_id"],
            "channel_name": row["channel_name"],
            "order_count": row["order_count"],
            "usd_order_count": row["usd_order_count"],
            "amount_usd": float(row["amount_usd"]),
            "ves_order_count": row["ves_order_count"],
            "amount_ves": float(row["amount_ves"]),
            "total_equivalent_usd": float(
                row["total_equivalent_usd"]
            ),
        })

    total_equivalent_usd = sum(
        float(row["total_equivalent_usd"])
        for row in rows
    )

    total_orders = sum(
        row["order_count"]
        for row in rows
    )

    audit_write({
        "action": "sales_query",
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": filters,
        "duration_ms": duration_ms,
        "rows_returned": len(channels),
    })

    return {
        "found": True,

        "definition": {
            "operation": "sales_by_channel",
            "table": "sale_order",
            "state": "sale",
            "date_field": "date_order",
            "dimension": (
                "sale_order.cons_channel_id "
                "-> cons_channel.name"
            ),
            "customer_scope": "external_only",
            "intercompany_policy": (
                "Se excluyen clientes cuyo partner resuelto "
                "pertenece a res_company."
            ),
            "currency_policy": (
                "USD se conserva. VES se convierte a USD "
                "usando la tasa USD vigente."
            ),
        },

        "filters": filters,

        "summary": {
            "channel_count": len(rows),
            "order_count": total_orders,
            "total_equivalent_usd": total_equivalent_usd,
        },

        "channels": channels,
    }
