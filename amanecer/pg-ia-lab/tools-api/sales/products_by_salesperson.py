"""
Producto más vendido por vendedor.

Extraído de sales_tools.py sin modificar la lógica certificada.
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_top_product_by_salesperson(
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
                "top_product_by_salesperson requiere "
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

    if commercial_network != "itc":
        raise HTTPException(
            status_code=400,
            detail=(
                "Red comercial no soportada. "
                "Actualmente solo está certificada: itc."
            ),
        )

    warehouse_code_pattern = "PTD%"

    warehouse_filter = (
        warehouse.strip()
        if warehouse
        else None
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

    salesperson_sql = """
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
              AND so.employee_fdv_id IS NOT NULL
        ),
        converted AS (
            SELECT
                s.id,
                s.employee_fdv_id,
                s.currency_id,
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
                ORDER BY r.name DESC, r.id DESC
                LIMIT 1
            ) own_rate ON TRUE
            LEFT JOIN LATERAL (
                SELECT r.rate
                FROM res_currency_rate r
                WHERE r.currency_id = 1
                  AND r.company_id = c.parent_id
                  AND r.name <= $4::date
                ORDER BY r.name DESC, r.id DESC
                LIMIT 1
            ) parent_rate ON TRUE
        )
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
        ORDER BY
            total_equivalent_usd DESC,
            order_count DESC,
            he.id ASC
    """

    async with pool_getter().acquire() as conn:
        salesperson_rows = await conn.fetch(
            salesperson_sql,
            date_from_dt,
            date_to_dt,
            warehouse_code_pattern,
            conversion_date_obj,
            warehouse_filter,
        )

    if not salesperson_rows:
        return {
            "found": False,
            "answer": (
                "No se encontraron vendedores ITC "
                "para el período indicado."
            ),
        }

    missing_rate_total = sum(
        row["missing_rate_count"]
        for row in salesperson_rows
    )

    if missing_rate_total > 0:
        raise HTTPException(
            status_code=422,
            detail=(
                "Existen ventas en VES sin tasa de conversión."
            ),
        )

    unsupported_currency_total = sum(
        row["unsupported_currency_count"]
        for row in salesperson_rows
    )

    if unsupported_currency_total > 0:
        raise HTTPException(
            status_code=422,
            detail=(
                "Existen monedas aún no soportadas."
            ),
        )

    selected_salespeople = salesperson_rows[:limit]

    employee_ids = [
        row["employee_id"]
        for row in selected_salespeople
    ]

    salesperson_rank = {
        row["employee_id"]: index
        for index, row in enumerate(
            selected_salespeople,
            start=1,
        )
    }

    salesperson_info = {
        row["employee_id"]: row
        for row in selected_salespeople
    }

    product_sql = """
        WITH product_sales AS (
            SELECT
                so.employee_fdv_id AS employee_id,
                sol.product_id,
                COALESCE(
                    pt.name ->> 'es_VE',
                    pt.name ->> 'en_US',
                    pt.name::text
                ) AS product_name,
                SUM(sol.product_uom_qty) AS quantity_sold,
                COUNT(DISTINCT so.id) AS product_order_count
            FROM sale_order so
            JOIN sale_order_line sol
                ON sol.order_id = so.id
            JOIN product_product pp
                ON pp.id = sol.product_id
            JOIN product_template pt
                ON pt.id = pp.product_tmpl_id
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
              AND so.employee_fdv_id = ANY($4::int[])
              AND sol.product_id IS NOT NULL
            GROUP BY
                so.employee_fdv_id,
                sol.product_id,
                pt.name
        ),
        ranked AS (
            SELECT
                employee_id,
                product_id,
                product_name,
                quantity_sold,
                product_order_count,
                ROW_NUMBER() OVER (
                    PARTITION BY employee_id
                    ORDER BY
                        quantity_sold DESC,
                        product_id ASC
                ) AS product_rank
            FROM product_sales
        )
        SELECT
            employee_id,
            product_id,
            product_name,
            quantity_sold,
            product_order_count
        FROM ranked
        WHERE product_rank = 1
    """

    async with pool_getter().acquire() as conn:
        product_rows = await conn.fetch(
            product_sql,
            date_from_dt,
            date_to_dt,
            warehouse_code_pattern,
            employee_ids,
            warehouse_filter,
        )

    product_by_employee = {
        row["employee_id"]: row
        for row in product_rows
    }

    results = []

    for employee_id in employee_ids:
        seller = salesperson_info[employee_id]
        product = product_by_employee.get(employee_id)

        if product is None:
            continue

        results.append({
            "salesperson_rank": salesperson_rank[employee_id],
            "employee_id": employee_id,
            "salesperson_name": seller["salesperson_name"],
            "salesperson_order_count": seller["order_count"],
            "salesperson_total_equivalent_usd": (
                seller["total_equivalent_usd"]
            ),
            "product_id": product["product_id"],
            "product_name": product["product_name"],
            "quantity_sold": product["quantity_sold"],
            "product_order_count": (
                product["product_order_count"]
            ),
        })

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
        "conversion_date": str(conversion_date_obj),
        "limit": limit,
    }

    answer_lines = [
        (
            f"Producto más vendido por unidades de cada uno "
            f"de los {len(results)} vendedores ITC líderes "
            f"por monto de ventas:"
        )
    ]

    for item in results:
        answer_lines.append(
            (
                f"{item['salesperson_rank']}. "
                f"{item['salesperson_name']}: "
                f"{item['product_name']} — "
                f"{item['quantity_sold']:,.0f} unidades."
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
        "rows_returned": len(results),
    })

    return {
        "found": True,
        "answer": answer,
        "commercial_network": commercial_network,
        "warehouse": warehouse,
        "conversion_date": str(conversion_date_obj),
        "salespeople": results,
    }
