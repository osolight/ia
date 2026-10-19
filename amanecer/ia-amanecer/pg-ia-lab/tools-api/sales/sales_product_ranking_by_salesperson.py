"""
Ranking de productos facturados por vendedor.

Agrupa facturas publicadas por SKU y ordena por monto equivalente en USD.
Mantiene la semántica certificada de account_move / account_move_line.
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_sales_product_ranking_by_salesperson(
    *,
    operation,
    request,
    pool_getter,
    audit_write,
    client_host,
    started,
    parse_iso_datetime,
    warehouse,
    salesperson,
    product,
    date_from,
    date_to,
    conversion_date,
    limit,
):

    if not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail=(
                "sales_product_ranking_by_salesperson requiere "
                "date_from y date_to."
            ),
        )

    if not salesperson:
        raise HTTPException(
            status_code=400,
            detail=(
                "sales_product_ranking_by_salesperson requiere "
                "salesperson."
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

    salesperson_filter = salesperson.strip()

    product_filter = (
        product.strip()
        if product
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

    # --------------------------------------------------------
    # Resolver identidad del vendedor.
    #
    # La sucursal, cuando está presente, puede desambiguar.
    # Si continúan existiendo varios vendedores compatibles
    # con el nombre indicado, no se mezclan sus resultados.
    # --------------------------------------------------------

    salesperson_resolution_sql = """
        SELECT
            he.id AS salesperson_id,
            he.name AS salesperson_name,

            ARRAY_AGG(
                DISTINCT sw.name
                ORDER BY sw.name
            ) FILTER (
                WHERE sw.name IS NOT NULL
            ) AS warehouse_names

        FROM hr_employee he

        JOIN sale_order so
            ON so.employee_fdv_id = he.id

        LEFT JOIN stock_warehouse sw
            ON sw.id = so.warehouse_id

        WHERE NOT EXISTS (
            SELECT 1
            FROM unnest(
                regexp_split_to_array(
                    unaccent(
                        UPPER(
                            TRIM($1::text)
                        )
                    ),
                    '[[:space:]]+'
                )
            ) AS w(word)

            WHERE unaccent(UPPER(he.name))
                  NOT LIKE
                  '%' || w.word || '%'
        )

          AND (
                $2::text IS NULL

                OR UPPER(sw.name) LIKE
                   '%' || UPPER($2::text) || '%'

                OR UPPER(sw.code) LIKE
                   '%' || UPPER($2::text) || '%'
          )

        GROUP BY
            he.id,
            he.name

        ORDER BY
            he.name,
            he.id
    """

    async with pool_getter().acquire() as conn:
        salesperson_matches = await conn.fetch(
            salesperson_resolution_sql,
            salesperson_filter,
            warehouse_filter,
        )

    if not salesperson_matches:
        duration_ms = int(
            (
                datetime.now(timezone.utc)
                - started
            ).total_seconds() * 1000
        )

        filters = {
            "operation": operation,
            "warehouse": warehouse,
            "salesperson": salesperson,
            "product": product,
            "date_from": date_from,
            "date_to": date_to,
            "conversion_date": str(
                conversion_date_obj
            ),
            "limit": limit,
        }

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
                "No se encontró un vendedor que coincida "
                "con los filtros indicados."
            ),
            "filters": filters,
        }

    if len(salesperson_matches) > 1:
        matches = []

        for row in salesperson_matches:
            matches.append({
                "salesperson_id": row[
                    "salesperson_id"
                ],
                "salesperson_name": row[
                    "salesperson_name"
                ],
                "warehouse_names": list(
                    row["warehouse_names"] or []
                ),
            })

        match_lines = []

        for item in matches:
            warehouse_text = (
                ", ".join(
                    item["warehouse_names"]
                )
                if item["warehouse_names"]
                else "sin sucursal identificada"
            )

            match_lines.append(
                f"{item['salesperson_name']} "
                f"— {warehouse_text}"
            )

        answer = (
            f'Encontré {len(matches)} vendedores que '
            f'coinciden con "{salesperson_filter}":\n'
            + "\n".join(
                f"- {line}"
                for line in match_lines
            )
            + "\n¿A cuál te refieres?"
        )

        duration_ms = int(
            (
                datetime.now(timezone.utc)
                - started
            ).total_seconds() * 1000
        )

        audit_write({
            "action": operation,
            "status": "needs_clarification",
            "client_host": client_host(request),
            "operation": operation,
            "filters": {
                "warehouse": warehouse,
                "salesperson": salesperson,
                "product": product,
                "date_from": date_from,
                "date_to": date_to,
            },
            "duration_ms": duration_ms,
            "rows_returned": 0,
        })

        return {
            "found": False,
            "needs_clarification": True,
            "reason": "ambiguous_salesperson",
            "answer": answer,
            "requested_salesperson": salesperson,
            "requested_warehouse": warehouse,
            "matches": matches,
        }

    resolved_salesperson_id = (
        salesperson_matches[0][
            "salesperson_id"
        ]
    )

    resolved_salesperson_name = (
        salesperson_matches[0][
            "salesperson_name"
        ]
    )

    sql = """
        WITH invoice_lines AS (
            SELECT
                aml.id AS invoice_line_id,
                am.id AS invoice_id,

                am.company_id,
                am.currency_id,

                pp.id AS product_id,
                pp.default_code AS sku,

                COALESCE(
                    pt.name ->> 'es_VE',
                    pt.name ->> 'en_US',
                    pt.name::text
                ) AS product_name,

                CASE
                    WHEN aml.secondary_uom_id = 12
                         AND aml.secondary_quantity IS NOT NULL
                    THEN aml.secondary_quantity

                    WHEN uu.category_id = 2
                         AND uu.factor IS NOT NULL
                         AND uu.factor <> 0
                    THEN aml.quantity / uu.factor

                    ELSE 0
                END AS kg_invoiced,

                aml.price_subtotal

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

              AND he.id = $3::int

              AND (
                    $4::text IS NULL

                    OR UPPER(sw.name) LIKE
                       '%' || UPPER($4::text) || '%'

                    OR UPPER(sw.code) LIKE
                       '%' || UPPER($4::text) || '%'
              )

              AND (
                    $5::text IS NULL

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
                           UPPER($5::text)
                       ) ||
                       '%'
              )

              AND NOT EXISTS (
                  SELECT 1

                  FROM res_company rc

                  WHERE rc.partner_id = rp.id
              )
        ),

        converted AS (
            SELECT
                i.*,

                CASE
                    WHEN i.currency_id = 1
                    THEN i.price_subtotal

                    WHEN i.currency_id = 167
                    THEN (
                        i.price_subtotal
                        *
                        COALESCE(
                            own_rate.rate,
                            parent_rate.rate
                        )
                    )

                    ELSE NULL
                END AS equivalent_usd,

                CASE
                    WHEN i.currency_id = 167
                    THEN COALESCE(
                        own_rate.rate,
                        parent_rate.rate
                    )

                    ELSE NULL
                END AS usd_rate

            FROM invoice_lines i

            JOIN res_company c
                ON c.id = i.company_id

            LEFT JOIN LATERAL (
                SELECT
                    r.rate

                FROM res_currency_rate r

                WHERE r.currency_id = 1
                  AND r.company_id = i.company_id
                  AND r.name <= $6::date

                ORDER BY
                    r.name DESC,
                    r.id DESC

                LIMIT 1
            ) own_rate ON TRUE

            LEFT JOIN LATERAL (
                SELECT
                    r.rate

                FROM res_currency_rate r

                WHERE r.currency_id = 1
                  AND r.company_id = c.parent_id
                  AND r.name <= $6::date

                ORDER BY
                    r.name DESC,
                    r.id DESC

                LIMIT 1
            ) parent_rate ON TRUE
        )

        SELECT
            product_id,
            sku,
            product_name,

            COUNT(DISTINCT invoice_id)
                AS invoice_count,

            COUNT(*)
                AS line_count,

            ROUND(
                SUM(kg_invoiced)::numeric,
                2
            ) AS total_kg_invoiced,

            ROUND(
                SUM(equivalent_usd)::numeric,
                2
            ) AS total_equivalent_usd,

            COUNT(*) FILTER (
                WHERE currency_id = 167
                  AND usd_rate IS NULL
            ) AS missing_rate_count,

            COUNT(*) FILTER (
                WHERE currency_id NOT IN (1, 167)
            ) AS unsupported_currency_count

        FROM converted

        GROUP BY
            product_id,
            sku,
            product_name

        ORDER BY
            total_equivalent_usd DESC NULLS LAST,
            total_kg_invoiced DESC,
            sku ASC NULLS LAST,
            product_id ASC
    """

    async with pool_getter().acquire() as conn:
        rows = await conn.fetch(
            sql,
            date_from_dt,
            date_to_dt,
            resolved_salesperson_id,
            warehouse_filter,
            product_filter,
            conversion_date_obj,
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
        "salesperson": salesperson,
        "resolved_salesperson_id": (
            resolved_salesperson_id
        ),
        "resolved_salesperson_name": (
            resolved_salesperson_name
        ),
        "product": product,
        "date_from": date_from,
        "date_to": date_to,
        "date_to_inclusive": False,
        "date_field": "account_move.invoice_date",
        "conversion_date": str(
            conversion_date_obj
        ),
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
                "No se encontraron productos facturados "
                "que cumplan los filtros indicados."
            ),
            "filters": filters,
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
                "certificado porque existen líneas facturadas "
                "en VES sin tasa de conversión disponible."
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
                "certificado porque existen monedas "
                "aún no soportadas."
            ),
        )

    selected_rows = rows[:limit]

    ranking = []

    for position, row in enumerate(
        selected_rows,
        start=1,
    ):
        ranking.append({
            "rank": position,
            "product_id": row["product_id"],
            "sku": row["sku"],
            "product_name": row["product_name"],
            "invoice_count": row["invoice_count"],
            "line_count": row["line_count"],
            "total_kg_invoiced": float(
                row["total_kg_invoiced"]
            ),
            "total_equivalent_usd": float(
                row["total_equivalent_usd"]
            ),
        })

    winner = ranking[0]

    total_kg = round(
        sum(
            float(row["total_kg_invoiced"])
            for row in rows
        ),
        2,
    )

    total_usd = round(
        sum(
            float(row["total_equivalent_usd"])
            for row in rows
        ),
        2,
    )

    answer = (
        f"El SKU con mayor monto facturado fue "
        f"{winner['sku']} | "
        f"{winner['product_name']} | "
        f"USD {winner['total_equivalent_usd']:,.2f} | "
        f"{winner['total_kg_invoiced']:,.2f} kg | "
        f"{winner['invoice_count']} factura(s)."
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
            "ranking_dimension": "SKU",
            "ranking_metric": (
                "total_equivalent_usd DESC"
            ),
            "sales_scope": "external_only",
        },

        "filters": filters,

        "summary": {
            "sku_count": len(rows),
            "total_kg_invoiced": total_kg,
            "total_equivalent_usd": total_usd,
        },

        "winner": winner,
        "ranking": ranking,
    }
