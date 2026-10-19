"""
Detalle de ventas facturadas.

Nueva operación:
    sales_invoice_detail

Definición:
- Fuente de facturación: account_move / account_move_line
- Solo facturas de cliente publicadas:
      account_move.state = 'posted'
      account_move.move_type = 'out_invoice'
- Fecha de negocio:
      account_move.invoice_date
- Vendedor:
      sale_order.employee_fdv_id -> hr_employee
- Relación pedido/factura:
      sale_order_line_invoice_rel
- Sucursal:
      sale_order.warehouse_id -> stock_warehouse
- Cliente:
      account_move.partner_id -> res_partner
- Producto / SKU:
      account_move_line.product_id
- Kg:
      1) secondary_quantity cuando secondary_uom = KG
      2) fallback quantity / uom.factor para UoM categoría peso
- Clientes externos solamente
- Intercompany excluido
- USD directo
- VES convertido a USD con tasa de compañía y fallback a matriz
"""

from datetime import datetime, timezone

from fastapi import HTTPException


async def handle_sales_invoice_detail(
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
    sku,
    customer,
    date_from,
    date_to,
    conversion_date,
    limit,
):
    if not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail=(
                "sales_invoice_detail requiere "
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

    salesperson_filter = (
        salesperson.strip()
        if salesperson
        else None
    )

    product_filter = (
        product.strip()
        if product
        else None
    )

    sku_filter = (
        sku.strip()
        if sku
        else None
    )

    customer_filter = (
        customer.strip()
        if customer
        else None
    )

    # --------------------------------------------------------
    # Resolución controlada de identidad del vendedor.
    #
    # Un nombre natural como "Jesús Duarte" puede coincidir
    # con más de un hr_employee. La sucursal, cuando viene
    # indicada por el usuario, participa en la desambiguación.
    #
    # Si persiste más de una coincidencia, no se mezclan las
    # ventas: se devuelve una solicitud de aclaración.
    # --------------------------------------------------------

    if salesperson_filter:
        salesperson_resolution_sql = """
            SELECT
                he.id AS salesperson_id,
                he.name AS salesperson_name,

                ARRAY_AGG(
                    DISTINCT sw.code
                    ORDER BY sw.code
                ) FILTER (
                    WHERE sw.code IS NOT NULL
                ) AS warehouse_codes,

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

        if len(salesperson_matches) > 1:
            matches = []

            for row in salesperson_matches:
                warehouse_codes = list(
                    row["warehouse_codes"] or []
                )

                warehouse_names = list(
                    row["warehouse_names"] or []
                )

                matches.append({
                    "salesperson_id": row[
                        "salesperson_id"
                    ],
                    "salesperson_name": row[
                        "salesperson_name"
                    ],
                    "warehouse_codes": warehouse_codes,
                    "warehouse_names": warehouse_names,
                })

            match_lines = []

            for item in matches:
                warehouses = []

                for code, name in zip(
                    item["warehouse_codes"],
                    item["warehouse_names"],
                ):
                    if name:
                        warehouses.append(name)
                    elif code:
                        warehouses.append(code)

                warehouse_text = (
                    ", ".join(warehouses)
                    if warehouses
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
                "action": "sales_invoice_detail",
                "status": "needs_clarification",
                "client_host": client_host(request),
                "operation": operation,
                "filters": {
                    "warehouse": warehouse,
                    "salesperson": salesperson,
                    "product": product,
                    "sku": sku,
                    "customer": customer,
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

    sql = """
        WITH invoice_lines AS (
            SELECT DISTINCT
                aml.id AS invoice_line_id,

                am.id AS invoice_id,
                am.name AS invoice_name,
                am.invoice_date,
                am.company_id,
                am.currency_id,

                so.id AS order_id,
                so.name AS order_name,
                so.date_order,

                sw.id AS warehouse_id,
                sw.code AS warehouse_code,
                sw.name AS warehouse_name,

                he.id AS salesperson_id,
                he.name AS salesperson_name,

                rp.id AS customer_id,
                rp.name AS customer_name,
                rp.vat AS customer_vat,

                pp.id AS product_id,
                pp.default_code AS sku,

                COALESCE(
                    pt.name ->> 'es_VE',
                    pt.name ->> 'en_US',
                    pt.name::text
                ) AS product_name,

                aml.quantity,
                aml.product_uom_id,

                COALESCE(
                    uom.name ->> 'es_VE',
                    uom.name ->> 'en_US',
                    uom.name::text
                ) AS uom_name,

                aml.secondary_quantity,
                aml.secondary_uom_id,

                COALESCE(
                    secondary_uom.name ->> 'es_VE',
                    secondary_uom.name ->> 'en_US',
                    secondary_uom.name::text
                ) AS secondary_uom_name,

                CASE
                    WHEN aml.secondary_quantity IS NOT NULL
                     AND secondary_uom.id = 12
                    THEN aml.secondary_quantity

                    WHEN uom.category_id = 2
                     AND uom.factor IS NOT NULL
                     AND uom.factor <> 0
                    THEN aml.quantity / uom.factor

                    ELSE NULL
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

            JOIN res_partner rp
                ON rp.id = am.partner_id

            LEFT JOIN stock_warehouse sw
                ON sw.id = so.warehouse_id

            JOIN product_product pp
                ON pp.id = aml.product_id

            JOIN product_template pt
                ON pt.id = pp.product_tmpl_id

            LEFT JOIN uom_uom uom
                ON uom.id = aml.product_uom_id

            LEFT JOIN uom_uom secondary_uom
                ON secondary_uom.id = aml.secondary_uom_id

            WHERE am.state = 'posted'
              AND am.move_type = 'out_invoice'
              AND am.invoice_date IS NOT NULL

              AND am.invoice_date >= $1::date
              AND am.invoice_date < $2::date

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
                    OR UPPER(sw.code) LIKE
                       '%' || UPPER($3::text) || '%'
              )

              AND (
                    $4::text IS NULL
                    OR NOT EXISTS (
                        SELECT 1
                        FROM unnest(
                            regexp_split_to_array(
                                unaccent(UPPER(TRIM($4::text))),
                                '[[:space:]]+'
                            )
                        ) AS w(word)
                        WHERE unaccent(UPPER(he.name))
                              NOT LIKE
                              '%' || w.word || '%'
                    )
              )

              AND (
                    $5::text IS NULL
                    OR UPPER(
                        COALESCE(
                            pt.name ->> 'es_VE',
                            pt.name ->> 'en_US',
                            pt.name::text
                        )
                    ) LIKE
                       '%' || UPPER($5::text) || '%'
              )

              AND (
                    $6::text IS NULL
                    OR UPPER(
                        COALESCE(
                            pp.default_code,
                            ''
                        )
                    ) LIKE
                       '%' || UPPER($6::text) || '%'
              )

              AND (
                    $7::text IS NULL
                    OR UPPER(rp.name) LIKE
                       '%' || UPPER($7::text) || '%'
                    OR UPPER(
                        COALESCE(
                            rp.vat,
                            ''
                        )
                    ) LIKE
                       '%' || UPPER($7::text) || '%'
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
                SELECT r.rate

                FROM res_currency_rate r

                WHERE r.currency_id = 1
                  AND r.company_id = i.company_id
                  AND r.name <= $8::date

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
                  AND r.name <= $8::date

                ORDER BY
                    r.name DESC,
                    r.id DESC

                LIMIT 1
            ) parent_rate ON TRUE
        )

        SELECT
            invoice_line_id,

            invoice_id,
            invoice_name,
            invoice_date,

            order_id,
            order_name,
            date_order,

            warehouse_id,
            warehouse_code,
            warehouse_name,

            salesperson_id,
            salesperson_name,

            customer_id,
            customer_name,
            customer_vat,

            product_id,
            sku,
            product_name,

            quantity,
            product_uom_id,
            uom_name,

            secondary_quantity,
            secondary_uom_id,
            secondary_uom_name,

            ROUND(
                kg_invoiced::numeric,
                4
            ) AS kg_invoiced,

            currency_id,

            ROUND(
                price_subtotal::numeric,
                2
            ) AS subtotal_original,

            ROUND(
                equivalent_usd::numeric,
                2
            ) AS equivalent_usd,

            CASE
                WHEN currency_id = 167
                THEN usd_rate
                ELSE NULL
            END AS usd_rate,

            CASE
                WHEN currency_id = 167
                 AND usd_rate IS NULL
                THEN TRUE
                ELSE FALSE
            END AS missing_rate,

            CASE
                WHEN currency_id NOT IN (1, 167)
                THEN TRUE
                ELSE FALSE
            END AS unsupported_currency

        FROM converted

        ORDER BY
            invoice_date DESC,
            invoice_name ASC,
            sku ASC,
            invoice_line_id ASC
    """

    async with pool_getter().acquire() as conn:
        rows = await conn.fetch(
            sql,
            date_from_dt,
            date_to_dt,
            warehouse_filter,
            salesperson_filter,
            product_filter,
            sku_filter,
            customer_filter,
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
        "product": product,
        "sku": sku,
        "customer": customer,
        "date_from": date_from,
        "date_to": date_to,
        "date_to_inclusive": False,
        "date_field": "account_move.invoice_date",
        "conversion_date": str(conversion_date_obj),
        "limit": limit,
        "sales_scope": "external_only",
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
                "No se encontraron líneas de facturación "
                "que cumplan los filtros indicados."
            ),
            "filters": filters,
        }

    missing_rate_total = sum(
        1
        for row in rows
        if row["missing_rate"]
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
        1
        for row in rows
        if row["unsupported_currency"]
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

    details = []

    for row in selected_rows:
        item = dict(row)

        for field in (
            "quantity",
            "secondary_quantity",
            "kg_invoiced",
            "subtotal_original",
            "equivalent_usd",
            "usd_rate",
        ):
            if item[field] is not None:
                item[field] = float(item[field])

        if item["invoice_date"] is not None:
            item["invoice_date"] = str(
                item["invoice_date"]
            )

        if item["date_order"] is not None:
            item["date_order"] = item[
                "date_order"
            ].isoformat()

        details.append(item)

    total_kg = round(
        sum(
            float(row["kg_invoiced"])
            for row in rows
            if row["kg_invoiced"] is not None
        ),
        2,
    )

    total_equivalent_usd = round(
        sum(
            float(row["equivalent_usd"])
            for row in rows
            if row["equivalent_usd"] is not None
        ),
        2,
    )

    invoice_count = len({
        row["invoice_id"]
        for row in rows
    })

    customer_count = len({
        row["customer_id"]
        for row in rows
    })

    sku_count = len({
        row["sku"]
        for row in rows
        if row["sku"]
    })

    summary = {
        "line_count": len(rows),
        "returned_line_count": len(details),
        "invoice_count": invoice_count,
        "customer_count": customer_count,
        "sku_count": sku_count,
        "total_kg_invoiced": total_kg,
        "total_equivalent_usd": total_equivalent_usd,
    }

    answer_lines = [
        (
            f"Se encontraron {invoice_count} factura(s), "
            f"{customer_count} cliente(s) y "
            f"{total_kg:,.2f} kg facturados."
        )
    ]

    for item in details:
        kg = (
            item["kg_invoiced"]
            if item["kg_invoiced"] is not None
            else 0
        )

        answer_lines.append(
            (
                f"{item['invoice_name']} | "
                f"{item['salesperson_name']} | "
                f"{item['customer_name']} | "
                f"SKU {item['sku'] or 'SIN SKU'} | "
                f"{item['product_name']} | "
                f"{kg:,.2f} kg"
            )
        )

    result = {
        "found": True,
        "answer": "\n".join(answer_lines),

        "definition": {
            "operation": "sales_invoice_detail",
            "date_field": "account_move.invoice_date",
            "invoice_state": "posted",
            "move_type": "out_invoice",
            "salesperson_dimension": (
                "sale_order.employee_fdv_id -> "
                "hr_employee.id"
            ),
            "invoice_relation": (
                "sale_order_line_invoice_rel"
            ),
            "kg_policy": (
                "Usa account_move_line.secondary_quantity "
                "cuando secondary_uom es KG; "
                "si no existe, convierte quantity usando "
                "uom_uom.factor para unidades de peso."
            ),
            "sales_scope": "external_only",
            "intercompany_policy": (
                "Se excluyen clientes cuyo partner resuelto "
                "pertenece a res_company."
            ),
            "currency_policy": (
                "USD se conserva. VES se convierte a USD "
                "usando la tasa de la compañía y fallback "
                "a su compañía matriz."
            ),
        },

        "filters": filters,
        "summary": summary,
        "details": details,
    }

    audit_write({
        "action": "sales_query",
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": filters,
        "duration_ms": duration_ms,
        "rows_returned": len(details),
    })

    return result
