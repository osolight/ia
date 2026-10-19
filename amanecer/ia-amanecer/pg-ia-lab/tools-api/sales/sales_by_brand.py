"""
F019 - Ventas por marca.

Clasificación controlada:
- Grupo Amanecer: 7, 8, 9
- Grupo Aurora: 18
- Terceros/no propias: 10, 58, 142, 143, 145
- SIN MARCA: product_template.model_id IS NULL

No confiar en product_brand_model.brand_type para propiedad de marca.
"""

from datetime import datetime, timezone

from fastapi import HTTPException


OWN_AMANECER_IDS = {7, 8, 9}
OWN_AURORA_IDS = {18}
OWN_IDS = OWN_AMANECER_IDS | OWN_AURORA_IDS

THIRD_PARTY_IDS = {
    10,
    58,
    142,
    143,
    145,
}


async def handle_sales_by_brand(
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
    brand_scope,
    brand_group,
    brand,
):
    if not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail="sales_by_brand requiere date_from y date_to.",
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

    brand_filter = (
        brand.strip()
        if brand
        else None
    )

    # Resolver IDs permitidos
    allowed_ids = None
    include_unbranded = True

    if brand_group == "amanecer":
        allowed_ids = sorted(OWN_AMANECER_IDS)
        include_unbranded = False

    elif brand_group == "aurora":
        allowed_ids = sorted(OWN_AURORA_IDS)
        include_unbranded = False

    elif brand_scope == "own":
        allowed_ids = sorted(OWN_IDS)
        include_unbranded = False

    elif brand_scope == "third_party":
        allowed_ids = sorted(THIRD_PARTY_IDS)
        include_unbranded = False

    elif brand_scope == "all":
        allowed_ids = None
        include_unbranded = True

    else:
        raise HTTPException(
            status_code=400,
            detail=(
                "brand_scope debe ser all, own o third_party."
            ),
        )

    sql = """
        WITH sales_lines AS (
            SELECT
                sol.id AS line_id,
                sol.product_uom_qty,
                sol.price_subtotal,

                so.company_id,
                so.currency_id,

                pt.model_id AS brand_id,

                COALESCE(
                    NULLIF(pbm.name, ''),
                    'SIN MARCA'
                ) AS brand_name

            FROM sale_order_line sol

            JOIN sale_order so
                ON so.id = sol.order_id

            JOIN product_product pp
                ON pp.id = sol.product_id

            JOIN product_template pt
                ON pt.id = pp.product_tmpl_id

            LEFT JOIN product_brand_model pbm
                ON pbm.id = pt.model_id

            JOIN res_partner rp
                ON rp.id = so.partner_id

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

              AND (
                    $3::text IS NULL
                    OR UPPER(sw.name) LIKE
                       '%' || UPPER($3::text) || '%'
              )

              AND (
                    $5::int[] IS NULL

                    OR pt.model_id = ANY($5::int[])

                    OR (
                        $6::boolean = TRUE
                        AND pt.model_id IS NULL
                    )
              )

              AND (
                    $7::text IS NULL
                    OR UPPER(
                        COALESCE(
                            NULLIF(pbm.name, ''),
                            'SIN MARCA'
                        )
                    ) LIKE '%' || UPPER($7::text) || '%'
              )
        ),

        converted AS (
            SELECT
                s.brand_id,
                s.brand_name,
                s.product_uom_qty,
                s.currency_id,

                CASE
                    WHEN s.currency_id = 1
                    THEN s.price_subtotal

                    WHEN s.currency_id = 167
                    THEN (
                        s.price_subtotal
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

            FROM sales_lines s

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

        brand_totals AS (
            SELECT
                brand_id,
                brand_name,

                ROUND(
                    SUM(product_uom_qty)::numeric,
                    2
                ) AS quantity,

                COUNT(*) FILTER (
                    WHERE currency_id = 167
                      AND usd_rate IS NULL
                ) AS missing_rate_count,

                COUNT(*) FILTER (
                    WHERE currency_id NOT IN (1, 167)
                ) AS unsupported_currency_count,

                ROUND(
                    COALESCE(
                        SUM(equivalent_usd),
                        0
                    )::numeric,
                    2
                ) AS total_equivalent_usd

            FROM converted

            GROUP BY
                brand_id,
                brand_name
        )

        SELECT
            brand_id,
            brand_name,
            quantity,
            missing_rate_count,
            unsupported_currency_count,
            total_equivalent_usd

        FROM brand_totals

        ORDER BY
            total_equivalent_usd DESC,
            quantity DESC,
            brand_name ASC
    """

    async with pool_getter().acquire() as conn:
        rows = await conn.fetch(
            sql,
            date_from_dt,
            date_to_dt,
            warehouse_filter,
            conversion_date_obj,
            allowed_ids,
            include_unbranded,
            brand_filter,
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
        "conversion_date": str(conversion_date_obj),
        "limit": limit,
        "sales_scope": "external_only",
        "brand_scope": brand_scope,
        "brand_group": brand_group,
        "brand": brand,
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
                "No se encontraron ventas por marca "
                "para el período y alcance indicados."
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
                "Existen ventas en VES sin tasa "
                "de conversión disponible."
            ),
        )

    unsupported_currency_total = sum(
        row["unsupported_currency_count"]
        for row in rows
    )

    if unsupported_currency_total > 0:
        raise HTTPException(
            status_code=422,
            detail="Existen monedas no soportadas.",
        )

    selected_rows = rows[:limit]

    brands = []

    for rank, row in enumerate(
        selected_rows,
        start=1,
    ):
        brand_id = row["brand_id"]

        if brand_id in OWN_AMANECER_IDS:
            ownership = "own"
            brand_group_value = "amanecer"

        elif brand_id in OWN_AURORA_IDS:
            ownership = "own"
            brand_group_value = "aurora"

        elif brand_id in THIRD_PARTY_IDS:
            ownership = "third_party"
            brand_group_value = None

        else:
            ownership = "unclassified"
            brand_group_value = None

        brands.append({
            "rank": rank,
            "brand_id": brand_id,
            "brand_name": row["brand_name"],
            "ownership": ownership,
            "brand_group": brand_group_value,
            "quantity": float(row["quantity"]),
            "total_equivalent_usd": float(
                row["total_equivalent_usd"]
            ),
        })

    answer_lines = [
        (
            f"Top {len(brands)} marcas "
            f"por monto de ventas equivalente en USD:"
        )
    ]

    for item in brands:
        answer_lines.append(
            (
                f"{item['rank']}. "
                f"{item['brand_name']}: "
                f"${item['total_equivalent_usd']:,.2f} USD, "
                f"{item['quantity']:,.2f} unidades."
            )
        )

    result = {
        "found": True,

        "answer": "\n".join(answer_lines),

        "definition": {
            "operation": "sales_by_brand",
            "dimension": (
                "product_template.model_id "
                "-> product_brand_model.id"
            ),
            "quantity_field": (
                "sale_order_line.product_uom_qty"
            ),
            "amount_field": (
                "sale_order_line.price_subtotal"
            ),
            "sales_scope": "external_only",
            "brand_classification": (
                "Clasificación controlada por IDs; "
                "no usa product_brand_model.brand_type."
            ),
            "own_amanecer_ids": sorted(
                OWN_AMANECER_IDS
            ),
            "own_aurora_ids": sorted(
                OWN_AURORA_IDS
            ),
            "third_party_ids": sorted(
                THIRD_PARTY_IDS
            ),
        },

        "filters": filters,

        "summary": {
            "brand_count": len(rows),
            "total_quantity": round(
                sum(
                    float(row["quantity"])
                    for row in rows
                ),
                2,
            ),
            "total_equivalent_usd": round(
                sum(
                    float(row["total_equivalent_usd"])
                    for row in rows
                ),
                2,
            ),
        },

        "brands": brands,
    }

    audit_write({
        "action": "sales_query",
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": filters,
        "duration_ms": duration_ms,
        "rows_returned": len(brands),
    })

    return result
