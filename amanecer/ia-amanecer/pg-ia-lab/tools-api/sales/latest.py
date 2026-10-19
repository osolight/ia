"""
Última orden confirmada dentro de sales_query.

Extraído de sales_tools.py sin modificar la lógica certificada.
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_latest_confirmed(
    *,
    operation,
    request,
    pool_getter,
    audit_write,
    client_host,
    started,
    parse_iso_datetime,
    warehouse,
    as_of,
):

        where_clauses = [
            "so.state = 'sale'",
            "so.date_order IS NOT NULL",
        ]

        params = []

        if warehouse:
            warehouse = warehouse.strip()

            if warehouse:
                params.append(f"%{warehouse}%")
                where_clauses.append(
                    f"sw.name ILIKE ${len(params)}"
                )
            else:
                warehouse = None

        as_of_dt = None

        if as_of:
            as_of_dt = parse_iso_datetime(
                as_of,
                "as_of",
            )

            params.append(as_of_dt)
            where_clauses.append(
                f"so.date_order <= ${len(params)}"
            )

        where_sql = "\n          AND ".join(
            where_clauses
        )

        sql = f"""
            SELECT
                so.id AS order_id,
                so.name AS order_name,
                rp.name AS customer_name,
                so.partner_id AS customer_id,

                so.date_order AS confirmed_date,

                so.amount_untaxed,
                so.amount_tax,
                so.amount_total,

                so.currency_id,
                rc.name AS currency_name,
                rc.symbol AS currency_symbol,

                so.company_id,

                so.warehouse_id,
                sw.name AS warehouse_name,

                so.user_id AS salesperson_user_id

            FROM sale_order so

            LEFT JOIN res_partner rp
                ON rp.id = so.partner_id

            LEFT JOIN stock_warehouse sw
                ON sw.id = so.warehouse_id

            LEFT JOIN res_currency rc
                ON rc.id = so.currency_id

            WHERE {where_sql}

            ORDER BY
                so.date_order DESC,
                so.id DESC

            LIMIT 1
        """

        async with pool_getter().acquire() as conn:
            row = await conn.fetchrow(
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
            "as_of": as_of,
        }

        if row is None:
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
                "definition": {
                    "operation": "latest_confirmed",
                    "table": "sale_order",
                    "state": "sale",
                    "date_field": "date_order",
                    "meaning": (
                        "Última orden de venta confirmada"
                    ),
                },
                "filters": filters,
                "message": (
                    "No se encontraron órdenes "
                    "confirmadas para los filtros indicados."
                ),
            }

        result = dict(row)

        audit_write({
            "action": "sales_query",
            "status": "ok",
            "client_host": client_host(request),
            "operation": operation,
            "filters": filters,
            "duration_ms": duration_ms,
            "rows_returned": 1,
            "order_id": result["order_id"],
            "order_name": result["order_name"],
        })

        return {
            "found": True,
            "definition": {
                "operation": "latest_confirmed",
                "table": "sale_order",
                "state": "sale",
                "date_field": "date_order",
                "meaning": (
                    "Última orden de venta confirmada"
                ),
            },
            "filters": filters,
            "sale": result,
        }

    # =========================================================
    # OPERATION: sales_summary
    # =========================================================
    # =========================================================
    # OPERATION: top_products
    # =========================================================
