"""
Resumen certificado de ventas por período.

Extraído de sales_tools.py sin modificar la lógica certificada.
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_sales_summary(
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
):

    # Para evitar períodos ambiguos,
    # exigimos inicio y fin.
    if not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail=(
                "sales_summary requiere "
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

    where_clauses = [
        "so.state = 'sale'",
        "so.date_order IS NOT NULL",
    ]

    params = [
        date_from_dt,
        date_to_dt,
    ]

    where_clauses.append(
        "so.date_order >= $1"
    )

    # IMPORTANTE:
    # date_to es límite EXCLUSIVO.
    where_clauses.append(
        "so.date_order < $2"
    )

    if warehouse:
        warehouse = warehouse.strip()

        if warehouse:
            params.append(f"%{warehouse}%")
            where_clauses.append(
                f"sw.name ILIKE ${len(params)}"
            )
        else:
            warehouse = None

    where_sql = "\n          AND ".join(
        where_clauses
    )

    # -----------------------------------------------------
    # NO sumamos monedas diferentes.
    #
    # Cada moneda se devuelve por separado.
    # -----------------------------------------------------
    sql = f"""
        SELECT
            rc.id AS currency_id,
            rc.name AS currency_name,
            rc.symbol AS currency_symbol,

            COUNT(*) AS order_count,

            COALESCE(
                SUM(so.amount_untaxed),
                0
            ) AS amount_untaxed,

            COALESCE(
                SUM(so.amount_tax),
                0
            ) AS amount_tax,

            COALESCE(
                SUM(so.amount_total),
                0
            ) AS amount_total,

            MIN(so.date_order)
                AS first_order_date,

            MAX(so.date_order)
                AS last_order_date

        FROM sale_order so

        LEFT JOIN stock_warehouse sw
            ON sw.id = so.warehouse_id

        LEFT JOIN res_currency rc
            ON rc.id = so.currency_id

        WHERE {where_sql}

        GROUP BY
            rc.id,
            rc.name,
            rc.symbol

        ORDER BY
            rc.id
    """

    async with pool_getter().acquire() as conn:
        rows = await conn.fetch(
            sql,
            *params,
        )

    # -----------------------------------------------------
    # Conversión certificada VES -> USD
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

    conversion_params = list(params)
    conversion_params.append(conversion_date_obj)

    conversion_date_index = len(conversion_params)

    conversion_sql = f"""
        WITH sales_period AS (
            SELECT
                so.id,
                so.company_id,
                so.currency_id,
                so.amount_total

            FROM sale_order so

            LEFT JOIN stock_warehouse sw
                ON sw.id = so.warehouse_id

            WHERE {where_sql}
        ),

        usd_sales AS (
            SELECT
                COUNT(*) AS order_count,
                COALESCE(
                    SUM(amount_total),
                    0
                ) AS amount_usd
            FROM sales_period
            WHERE currency_id = 1
        ),

        ves_sales AS (
            SELECT
                s.id,
                s.company_id,
                s.amount_total AS amount_ves,

                COALESCE(
                    own_rate.rate,
                    parent_rate.rate
                ) AS usd_rate,

                COALESCE(
                    own_rate.rate,
                    parent_rate.rate
                ) * s.amount_total
                    AS amount_equivalent_usd

            FROM sales_period s

            JOIN res_company c
                ON c.id = s.company_id

            LEFT JOIN LATERAL (
                SELECT r.rate
                FROM res_currency_rate r
                WHERE r.currency_id = 1
                  AND r.company_id = s.company_id
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

            WHERE s.currency_id = 167
        ),

        ves_summary AS (
            SELECT
                COUNT(*) AS order_count,

                COUNT(*) FILTER (
                    WHERE usd_rate IS NOT NULL
                ) AS converted_order_count,

                COUNT(*) FILTER (
                    WHERE usd_rate IS NULL
                ) AS missing_rate_order_count,

                COALESCE(
                    SUM(amount_ves),
                    0
                ) AS amount_ves,

                COALESCE(
                    SUM(amount_equivalent_usd),
                    0
                ) AS equivalent_usd,

                MIN(usd_rate) AS min_rate,
                MAX(usd_rate) AS max_rate

            FROM ves_sales
        )

        SELECT
            u.order_count AS usd_order_count,
            u.amount_usd,

            v.order_count AS ves_order_count,
            v.amount_ves,

            v.converted_order_count,
            v.missing_rate_order_count,

            v.min_rate AS min_usd_rate,
            v.max_rate AS max_usd_rate,

            CASE
                WHEN v.min_rate > 0
                THEN 1 / v.min_rate
                ELSE NULL
            END AS bs_per_usd,

            v.equivalent_usd
                AS ves_equivalent_usd,

            CASE
                WHEN v.missing_rate_order_count = 0
                THEN u.amount_usd + v.equivalent_usd
                ELSE NULL
            END AS total_equivalent_usd

        FROM usd_sales u
        CROSS JOIN ves_summary v
    """

    async with pool_getter().acquire() as conn:
        conversion_row = await conn.fetchrow(
            conversion_sql,
            *conversion_params,
        )

    conversion = dict(conversion_row)

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

    if conversion["bs_per_usd"] is not None:
        conversion["bs_per_usd_text"] = (
            f"{conversion['bs_per_usd']:,.4f} Bs/USD"
        )
    else:
        conversion["bs_per_usd_text"] = None

    conversion["ves_equivalent_usd_text"] = (
        f"$ {conversion['ves_equivalent_usd']:,.2f} USD"
    )

    if conversion["total_equivalent_usd"] is not None:
        conversion["total_equivalent_usd_text"] = (
            f"$ {conversion['total_equivalent_usd']:,.2f} USD"
        )
    else:
        conversion["total_equivalent_usd_text"] = None

        return {
            "found": False,
            "definition": {
                "operation": "sales_summary",
                "table": "sale_order",
                "state": "sale",
                "date_field": "date_order",
                "meaning": (
                    "Resumen de órdenes de venta "
                    "confirmadas por período"
                ),
                "currency_policy": (
                    "Los importes nunca se suman "
                    "entre monedas diferentes."
                ),
            },
            "filters": filters,
            "message": (
                "No se encontraron órdenes "
                "confirmadas para los filtros indicados."
            ),
        }


    # -----------------------------------------------------
    # Preparar salida segura por moneda
    # -----------------------------------------------------
    totals_by_currency = []

    for row in rows:
        item = dict(row)

        currency_name = item.get("currency_name") or ""
        currency_symbol = item.get("currency_symbol") or currency_name

        item["currency_label"] = (
            f"{currency_name} ({currency_symbol})"
            if currency_symbol
            else currency_name
        )

        # Valores textuales canónicos para evitar que el LLM
        # cambie escala, separadores o mezcle monedas.
        item["amount_untaxed_text"] = (
            f"{currency_symbol} "
            f"{item['amount_untaxed']:,.4f} "
            f"{currency_name}"
        )

        item["amount_tax_text"] = (
            f"{currency_symbol} "
            f"{item['amount_tax']:,.4f} "
            f"{currency_name}"
        )

        item["amount_total_text"] = (
            f"{currency_symbol} "
            f"{item['amount_total']:,.4f} "
            f"{currency_name}"
        )

        totals_by_currency.append(item)

    total_order_count = sum(
        row["order_count"]
        for row in rows
    )

    currency_count = len(rows)
    mixed_currencies = currency_count > 1

    first_order_date = min(
        row["first_order_date"]
        for row in rows
        if row["first_order_date"] is not None
    )

    last_order_date = max(
        row["last_order_date"]
        for row in rows
        if row["last_order_date"] is not None
    )

    audit_write({
        "action": "sales_query",
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": filters,
        "duration_ms": duration_ms,
        "rows_returned": len(rows),
        "order_count": total_order_count,
    })

    # ---------------------------------------------------------
    # Resumen gerencial canónico
    # ---------------------------------------------------------

    usd_item = next(
        (
            item
            for item in totals_by_currency
            if item.get("currency_id") == 1
        ),
        None,
    )

    ves_item = next(
        (
            item
            for item in totals_by_currency
            if item.get("currency_id") == 167
        ),
        None,
    )

    executive_summary = {
        "overall_order_count": total_order_count,
        "mixed_currencies": mixed_currencies,

        "usd": {
            "order_count": (
                usd_item.get("order_count", 0)
                if usd_item
                else 0
            ),
            "amount_total": (
                usd_item.get("amount_total", 0)
                if usd_item
                else 0
            ),
            "amount_total_text": (
                usd_item.get("amount_total_text")
                if usd_item
                else None
            ),
        },

        "ves": {
            "order_count": (
                ves_item.get("order_count", 0)
                if ves_item
                else 0
            ),
            "amount_total": (
                ves_item.get("amount_total", 0)
                if ves_item
                else 0
            ),
            "amount_total_text": (
                ves_item.get("amount_total_text")
                if ves_item
                else None
            ),
        },

        "conversion": {
            "conversion_date": str(conversion_date_obj),
            "bs_per_usd_text": conversion.get(
                "bs_per_usd_text"
            ),
            "ves_equivalent_usd_text": conversion.get(
                "ves_equivalent_usd_text"
            ),
            "total_equivalent_usd_text": conversion.get(
                "total_equivalent_usd_text"
            ),
            "missing_rate_order_count": conversion.get(
                "missing_rate_order_count"
            ),
        },
    }

    executive_answer = (
        f"El período consultado registra "
        f"{total_order_count:,} órdenes confirmadas. "
        f"En USD: "
        f"{conversion.get('usd_order_count', 0):,} órdenes por "
        f"{usd_item.get('amount_total_text') if usd_item else 'USD 0'}. "
        f"En VES: "
        f"{conversion.get('ves_order_count', 0):,} órdenes por "
        f"{ves_item.get('amount_total_text') if ves_item else 'VES 0'}. "
    )

    if (
        conversion.get("missing_rate_order_count") == 0
        and conversion.get("total_equivalent_usd_text")
    ):
        executive_answer += (
            f"A la tasa de "
            f"{conversion.get('bs_per_usd_text')}, "
            f"el monto en VES equivale a "
            f"{conversion.get('ves_equivalent_usd_text')}. "
            f"Total equivalente en USD: "
            f"{conversion.get('total_equivalent_usd_text')}."
        )

    return {
        "found": True,
        "answer": executive_answer,
    }
