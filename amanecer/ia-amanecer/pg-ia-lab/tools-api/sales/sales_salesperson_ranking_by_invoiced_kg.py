"""
Ranking de vendedores por kilos facturados.

Usa facturas publicadas y la política certificada de conversión a KG.
"""

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException


async def handle_sales_salesperson_ranking_by_invoiced_kg(
    *,
    operation,
    request,
    pool_getter,
    audit_write,
    client_host,
    started,
    parse_iso_datetime,
    warehouse,
    product,
    date_from,
    date_to,
    days,
    limit,
):

    calculated_from_days = False

    if days is not None:
        if days < 1:
            raise HTTPException(
                status_code=400,
                detail="days debe ser mayor o igual a 1.",
            )

        business_tz = ZoneInfo("America/Caracas")

        today = datetime.now(
            business_tz
        ).date()

        calculated_date_from = (
            today - timedelta(days=days - 1)
        )

        calculated_date_to = (
            today + timedelta(days=1)
        )

        date_from = (
            calculated_date_from.isoformat()
            + "T00:00:00"
        )

        date_to = (
            calculated_date_to.isoformat()
            + "T00:00:00"
        )

        calculated_from_days = True

    elif not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail=(
                "Debe indicar days o ambos "
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

    warehouse_filter = (
        warehouse.strip()
        if warehouse
        else None
    )

    product_filter = (
        product.strip()
        if product
        else None
    )

    sql = """
        SELECT
            he.id AS salesperson_id,
            he.name AS salesperson_name,

            sw.code AS warehouse_code,
            sw.name AS warehouse_name,

            COUNT(
                DISTINCT am.id
            ) AS invoice_count,

            ROUND(
                SUM(
                    CASE
                        WHEN aml.secondary_uom_id = 12
                             AND aml.secondary_quantity IS NOT NULL
                        THEN aml.secondary_quantity

                        WHEN uu.category_id = 2
                             AND uu.factor IS NOT NULL
                             AND uu.factor <> 0
                        THEN aml.quantity / uu.factor

                        ELSE 0
                    END
                )::numeric,
                2
            ) AS total_kg_invoiced

        FROM account_move am

        JOIN account_move_line aml
            ON aml.move_id = am.id

        JOIN sale_order_line_invoice_rel rel
            ON rel.invoice_line_id = aml.id

        JOIN sale_order_line sol
            ON sol.id = rel.order_line_id

        JOIN sale_order so
            ON so.id = sol.order_id

        JOIN hr_employee he
            ON he.id = so.employee_fdv_id

        JOIN stock_warehouse sw
            ON sw.id = so.warehouse_id

        JOIN res_partner rp
            ON rp.id = am.partner_id

        JOIN product_product pp
            ON pp.id = aml.product_id

        JOIN product_template pt
            ON pt.id = pp.product_tmpl_id

        LEFT JOIN uom_uom uu
            ON uu.id = aml.product_uom_id

        WHERE am.state = 'posted'
          AND am.move_type = 'out_invoice'

          AND am.invoice_date >= $1::date
          AND am.invoice_date < $2::date

          AND (
                $3::text IS NULL

                OR UPPER(sw.name) LIKE
                   '%' || UPPER($3::text) || '%'

                OR UPPER(sw.code) LIKE
                   '%' || UPPER($3::text) || '%'
          )

          AND (
                $4::text IS NULL

                OR unaccent(
                    UPPER(
                        COALESCE(
                            pt.name ->> 'es_VE',
                            pt.name ->> 'en_US',
                            pt.name::text
                        )
                    )
                ) LIKE
                   '%' ||
                   unaccent(
                       UPPER($4::text)
                   ) ||
                   '%'
          )

          AND NOT EXISTS (
              SELECT 1
              FROM res_company rc
              WHERE rc.partner_id = rp.id
          )

        GROUP BY
            he.id,
            he.name,
            sw.code,
            sw.name

        ORDER BY
            total_kg_invoiced DESC,
            he.name ASC,
            he.id ASC
    """

    async with pool_getter().acquire() as conn:
        rows = await conn.fetch(
            sql,
            date_from_dt,
            date_to_dt,
            warehouse_filter,
            product_filter,
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
        "product": product,
        "date_from": date_from,
        "date_to": date_to,
        "days": days,
        "period_calculated_by_backend": calculated_from_days,
        "date_to_inclusive": False,
        "date_field": "account_move.invoice_date",
        "limit": limit,
        "sales_scope": "external_only",
    }

    if not rows:
        audit_write({
            "action": operation,
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
                "No se encontraron vendedores con facturación "
                "que cumpla los filtros indicados."
            ),
            "filters": filters,
        }

    selected_rows = rows[:limit]

    ranking = []

    for position, row in enumerate(
        selected_rows,
        start=1,
    ):
        ranking.append({
            "rank": position,
            "salesperson_id": row["salesperson_id"],
            "salesperson_name": row["salesperson_name"],
            "warehouse_code": row["warehouse_code"],
            "warehouse_name": row["warehouse_name"],
            "invoice_count": row["invoice_count"],
            "total_kg_invoiced": float(
                row["total_kg_invoiced"]
            ),
        })

    winner = ranking[0]

    answer = (
        f"El vendedor con mayor cantidad facturada fue "
        f"{winner['salesperson_name']} con "
        f"{winner['total_kg_invoiced']:,.2f} kg."
    )

    audit_write({
        "action": operation,
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": filters,
        "duration_ms": duration_ms,
        "rows_returned": len(ranking),
    })

    return {
        "found": True,
        "answer": answer,

        "definition": {
            "operation": operation,
            "date_field": "account_move.invoice_date",
            "invoice_state": "posted",
            "move_type": "out_invoice",
            "salesperson_dimension": (
                "sale_order.employee_fdv_id "
                "-> hr_employee.id"
            ),
            "invoice_relation": (
                "sale_order_line_invoice_rel"
            ),
            "ranking_dimension": "salesperson",
            "ranking_metric": (
                "total_kg_invoiced DESC"
            ),
            "sales_scope": "external_only",
        },

        "filters": filters,

        "summary": {
            "salesperson_count": len(rows),
        },

        "winner": winner,
        "ranking": ranking,
    }
