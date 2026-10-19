"""
Herramientas certificadas de negocio - Ventas
Grupo Amanecer

Este módulo contiene las rutas y lógica del dominio de ventas.
No contiene credenciales ni crea conexiones propias a PostgreSQL.
Utiliza el pool administrado por main.py.
"""

from datetime import datetime, timezone
from typing import Optional, Literal

from fastapi import HTTPException, Request, Query

from sales.salespeople import handle_top_salespeople
from sales.sales_by_salesperson import handle_sales_by_salesperson
from sales.sales_by_product import handle_sales_by_product
from sales.sales_invoice_detail import handle_sales_invoice_detail
from sales.sales_salesperson_ranking_by_invoiced_kg import handle_sales_salesperson_ranking_by_invoiced_kg
from sales.sales_product_ranking_by_salesperson import handle_sales_product_ranking_by_salesperson
from sales.sales_by_brand import handle_sales_by_brand
from sales.sales_by_category import handle_sales_by_category
from sales.purchase_frequency import handle_purchase_frequency
from sales.new_vs_recurring_customers import handle_new_vs_recurring_customers
from sales.latest import handle_latest_confirmed
from sales.products import handle_top_products
from sales.products_by_salesperson import handle_top_product_by_salesperson
from sales.warehouses import handle_top_warehouses
from sales.summaries import handle_sales_summary
from sales.period_comparison import handle_sales_period_comparison
from sales.average_ticket import handle_average_ticket
from sales.zones import handle_sales_by_zone
from sales.states import handle_sales_by_state
from sales.channels import handle_sales_by_channel
from sales.customers import handle_top_customers
from sales.sales_by_customer import handle_sales_by_customer


# ---------------------------------------------------------------------------
# Utilidad compartida para fechas ISO-8601
# ---------------------------------------------------------------------------

def parse_iso_datetime(
    value: str,
    field_name: str,
) -> datetime:
    try:
        normalized = value.strip()

        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"

        parsed = datetime.fromisoformat(normalized)

        # date_order en Odoo es timestamp sin timezone.
        # Si recibimos timezone, normalizamos a UTC
        # y eliminamos tzinfo antes de consultar PostgreSQL.
        if parsed.tzinfo is not None:
            parsed = (
                parsed
                .astimezone(timezone.utc)
                .replace(tzinfo=None)
            )

        return parsed

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Formato inválido para {field_name}. "
                "Utilice ISO-8601, por ejemplo "
                "2026-09-30T22:30:00"
            ),
        )


def register_sales_routes(
    app,
    pool_getter,
    require_api_key,
    audit_write,
    client_host,
):
    @app.get(
        "/sales/latest-confirmed",
        operation_id="sales_latest_confirmed",
        summary="Última orden de venta confirmada",
        description=(
            "Devuelve la última orden de venta confirmada en Odoo. "
            "La definición certificada utiliza sale_order con state='sale' "
            "y ordena por date_order descendente."
        ),
    )
    async def sales_latest_confirmed(request: Request):
        require_api_key(request)
        started = datetime.now(timezone.utc)

        sql = """
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

            WHERE so.state = 'sale'
              AND so.date_order IS NOT NULL

            ORDER BY
                so.date_order DESC,
                so.id DESC

            LIMIT 1
        """

        async with pool_getter().acquire() as conn:
            row = await conn.fetchrow(sql)

        duration_ms = int(
            (datetime.now(timezone.utc) - started).total_seconds() * 1000
        )

        if row is None:
            audit_write({
                "action": "sales_latest_confirmed",
                "status": "ok",
                "client_host": client_host(request),
                "duration_ms": duration_ms,
                "rows_returned": 0,
            })

            return {
                "found": False,
                "message": "No existen órdenes de venta confirmadas."
            }

        result = dict(row)

        audit_write({
            "action": "sales_latest_confirmed",
            "status": "ok",
            "client_host": client_host(request),
            "duration_ms": duration_ms,
            "rows_returned": 1,
            "order_id": result["order_id"],
            "order_name": result["order_name"],
        })

        return {
            "found": True,
            "definition": {
                "table": "sale_order",
                "state": "sale",
                "date_field": "date_order",
                "meaning": "Última orden de venta confirmada"
            },
            "sale": result,
        }

    @app.get(
        "/sales/invoice-detail",
        operation_id="sales_invoice_detail",
        summary="Detalle de facturación por vendedor, producto y cliente",
        description=(
            "Consulta facturas de venta publicadas de Grupo Amanecer. "
            "Use esta herramienta cuando el usuario pregunte por facturado, "
            "facturas, kilos facturados, SKU facturados o clientes facturados. "
            "Permite combinar fecha, sucursal, vendedor, producto, SKU y cliente. "
            "Si el usuario menciona una sucursal o localidad, conserve ese dato "
            "en warehouse. "
            "Si menciona un producto junto con su marca, conserve la expresión "
            "completa en product. "
            "No reduzca ni omita filtros explícitos proporcionados por el usuario."
        ),
    )
    async def sales_invoice_detail(
        request: Request,

        date_from: str = Query(
            ...,
            description=(
                "Inicio inclusivo del período de facturación en ISO-8601. "
                "Filtra account_move.invoice_date."
            ),
        ),

        date_to: str = Query(
            ...,
            description=(
                "Fin exclusivo del período de facturación en ISO-8601. "
                "Para consultar un día completo use como date_to "
                "el inicio del día siguiente."
            ),
        ),

        warehouse: Optional[str] = Query(
            None,
            description=(
                "Sucursal o localidad mencionada por el usuario. "
                "No omitir si la pregunta identifica explícitamente "
                "una sucursal o localidad."
            ),
        ),

        salesperson: Optional[str] = Query(
            None,
            description=(
                "Nombre del vendedor. "
                "Conserve todas las palabras proporcionadas por el usuario."
            ),
        ),

        product: Optional[str] = Query(
            None,
            description=(
                "Producto indicado por el usuario. "
                "Conserve la expresión completa, incluyendo marca, "
                "presentación o variante cuando estén mencionadas. "
                "No reduzca el producto a un término más general."
            ),
        ),

        sku: Optional[str] = Query(
            None,
            description="Código SKU opcional.",
        ),

        customer: Optional[str] = Query(
            None,
            description="Nombre o RIF/VAT del cliente opcional.",
        ),

        conversion_date: Optional[str] = Query(
            None,
            description=(
                "Fecha de referencia opcional para conversión monetaria "
                "en formato YYYY-MM-DD."
            ),
        ),

        limit: int = Query(
            20,
            ge=1,
            description=(
                "Cantidad solicitada de líneas de detalle. "
                "El backend devuelve como máximo 20 líneas. "
                "Si se solicita un valor mayor, se limita automáticamente a 20. "
                "El resumen siempre considera todas las líneas encontradas."
            ),
        ),
    ):
        limit = min(limit, 20)

        return await handle_sales_invoice_detail(
            operation="sales_invoice_detail",
            request=request,
            pool_getter=pool_getter,
            audit_write=audit_write,
            client_host=client_host,
            started=datetime.now(timezone.utc),
            parse_iso_datetime=parse_iso_datetime,
            warehouse=warehouse,
            salesperson=salesperson,
            product=product,
            sku=sku,
            customer=customer,
            date_from=date_from,
            date_to=date_to,
            conversion_date=conversion_date,
            limit=limit,
        )


    @app.get(
        "/sales/product-ranking-by-salesperson",
        operation_id="sales_product_ranking_by_salesperson",
        summary="Ranking de SKU facturados por vendedor y monto",
        description=(
            "Obtiene los SKU facturados por un vendedor dentro de un período "
            "y los ordena por monto equivalente en USD de mayor a menor. "
            "Use esta herramienta cuando el usuario pregunte qué SKU, producto "
            "o arroz generó el mayor monto de ventas o facturación para un "
            "vendedor específico. "
            "Permite filtrar por sucursal y por expresión de producto. "
            "La consulta utiliza facturas publicadas, no órdenes de venta."
        ),
    )
    async def sales_product_ranking_by_salesperson(
        request: Request,

        date_from: str = Query(
            ...,
            description=(
                "Inicio inclusivo del período de facturación "
                "en ISO-8601."
            ),
        ),

        date_to: str = Query(
            ...,
            description=(
                "Fin exclusivo del período de facturación "
                "en ISO-8601."
            ),
        ),

        salesperson: str = Query(
            ...,
            description=(
                "Nombre del vendedor. Conserve todas las palabras "
                "proporcionadas por el usuario."
            ),
        ),

        warehouse: Optional[str] = Query(
            None,
            description=(
                "Sucursal o localidad opcional. "
                "No omitir cuando el usuario la mencione."
            ),
        ),

        product: Optional[str] = Query(
            None,
            description=(
                "Producto o familia indicada por el usuario. "
                "Ejemplos: arroz, arroz Amanecer."
            ),
        ),

        conversion_date: Optional[str] = Query(
            None,
            description=(
                "Fecha de referencia opcional para conversión "
                "monetaria en formato YYYY-MM-DD."
            ),
        ),

        limit: int = Query(
            10,
            ge=1,
            description=(
                "Cantidad solicitada de SKU en el ranking. "
                "El backend devuelve como máximo 20. "
                "Si se solicita un valor mayor se limita "
                "automáticamente a 20."
            ),
        ),
    ):
        require_api_key(request)

        limit = min(limit, 20)

        return await handle_sales_product_ranking_by_salesperson(
            operation="sales_product_ranking_by_salesperson",
            request=request,
            pool_getter=pool_getter,
            audit_write=audit_write,
            client_host=client_host,
            started=datetime.now(timezone.utc),
            parse_iso_datetime=parse_iso_datetime,
            warehouse=warehouse,
            salesperson=salesperson,
            product=product,
            date_from=date_from,
            date_to=date_to,
            conversion_date=conversion_date,
            limit=limit,
        )


    @app.get(
        "/sales/salesperson-ranking-by-invoiced-kg",
        operation_id="sales_salesperson_ranking_by_invoiced_kg",
        summary="Ranking de vendedores por kilos facturados",
        description=(
            "Obtiene vendedores ordenados por cantidad de kilos facturados. "
            "Use esta herramienta cuando el usuario pregunte qué vendedores "
            "facturaron mayor cantidad, más kilos o lideraron por kilos "
            "de un producto dentro de un período. "
            "Permite filtrar por sucursal y producto. "
            "Utiliza facturas publicadas, no órdenes de venta. "
            "Esta herramienta es únicamente para rankings por vendedor y kilos facturados; "
            "NO debe utilizarse para calcular el total global de kilogramos vendidos "
            "de un período. Para ese total use sales_query con operation='sales_summary'."
        ),
    )
    async def sales_salesperson_ranking_by_invoiced_kg(
        request: Request,

        date_from: Optional[str] = Query(
            None,
            description=(
                "Inicio inclusivo del período en ISO-8601. "
                "Úselo para períodos explícitos. "
                "No es necesario si se usa days."
            ),
        ),

        date_to: Optional[str] = Query(
            None,
            description=(
                "Fin exclusivo del período en ISO-8601. "
                "Úselo para períodos explícitos. "
                "No es necesario si se usa days."
            ),
        ),

        days: Optional[int] = Query(
            None,
            ge=1,
            le=3650,
            description=(
                "Para períodos relativos como 'últimos N días'. "
                "Ejemplo: para 'últimos 30 días', enviar days=30. "
                "El backend calcula el período incluyendo hoy "
                "completo según America/Caracas. "
                "Cuando se usa days, no calcular date_from/date_to "
                "manualmente."
            ),
        ),

        warehouse: Optional[str] = Query(
            None,
            description=(
                "Sucursal o localidad opcional. "
                "No omitir cuando el usuario la mencione."
            ),
        ),

        product: Optional[str] = Query(
            None,
            description=(
                "Producto o familia indicada por el usuario. "
                "Ejemplo: arroz."
            ),
        ),

        limit: int = Query(
            10,
            ge=1,
            description=(
                "Cantidad de vendedores solicitados. "
                "El backend devuelve como máximo 20."
            ),
        ),
    ):
        require_api_key(request)

        limit = min(limit, 20)

        return await handle_sales_salesperson_ranking_by_invoiced_kg(
            operation="sales_salesperson_ranking_by_invoiced_kg",
            request=request,
            pool_getter=pool_getter,
            audit_write=audit_write,
            client_host=client_host,
            started=datetime.now(timezone.utc),
            parse_iso_datetime=parse_iso_datetime,
            warehouse=warehouse,
            product=product,
            date_from=date_from,
            date_to=date_to,
            days=days,
            limit=limit,
        )


    @app.get(
        "/sales/query",
        operation_id="sales_query",
        summary="Consulta genérica certificada de ventas",
        description=(
            "Consulta controlada de ventas de Grupo Amanecer. "
            "Soporta latest_confirmed, sales_summary, top_products, "
            "top_warehouses, top_salespeople y "
            "top_product_by_salesperson. "
            "Permite filtros controlados por warehouse y períodos. "
            "Para totales generales del período, incluyendo total de kilogramos vendidos, "
            "use sales_summary. "
            "Para preguntas gerenciales de ventas por producto use sales_by_product; "
            "para rankings o cantidades por producto en unidades explícitas use top_products."
        ),
    )

    async def sales_query(
        request: Request,

        operation: Literal[
            "latest_confirmed",
            "sales_summary",
            "sales_period_comparison",
            "average_ticket",
            "sales_by_zone",
            "sales_by_state",
            "sales_by_channel",
            "sales_by_customer",
            "sales_by_salesperson",
            "sales_by_product",
            "purchase_frequency",
            "new_vs_recurring_customers",
            "sales_by_brand",
            "sales_by_category",
            "top_products",
            "top_warehouses",
            "top_salespeople",
            "top_product_by_salesperson",
            "top_customers",
        ] = Query(
            ...,
            description=(
                "Operación obligatoria. "
                "Use 'latest_confirmed' para obtener la última orden "
                "de venta confirmada. "
                "Use 'sales_summary' para totales generales del período: "
                "montos, cantidad de órdenes y total de kilogramos vendidos. "
                "También debe usarse para preguntas como 'cuántos kg se vendieron', "
                "'cuántos kilogramos se han vendido' o 'total de kilos vendidos'. "
                "Use 'sales_period_comparison' para comparar dos períodos "
                "de ventas confirmadas y calcular diferencias y variaciones. "
                "Use 'average_ticket' para obtener el ticket promedio de las "
                "órdenes confirmadas dentro de un período. "
                "Use 'sales_by_zone' para obtener las zonas comerciales con "
                "mayor monto de ventas dentro de un período. "
                "Use 'sales_by_state' para obtener los estados geográficos con "
                "mayor monto de ventas. "
                "Use 'sales_by_channel' para obtener los canales comerciales con "
                "mayor monto de ventas dentro de un período. "
                "Use 'sales_by_customer' para obtener los clientes con mayor "
                "monto de ventas dentro de un período. "
                "Use 'sales_by_salesperson' para obtener las ventas "
                "por vendedor en el alcance general externo de Grupo Amanecer, "
                "excluyendo intercompany; esta operación NO está limitada a ITC. "
                "Use 'sales_by_product' para obtener las ventas "
                "por producto en el alcance general externo de Grupo Amanecer, "
                "incluyendo cantidad y monto equivalente en USD, "
                "excluyendo intercompany. "
                "Cuando el usuario pregunte qué producto vendió más, "
                "cuáles productos tuvieron mayores ventas, top de productos "
                "por ventas o expresiones equivalentes sin mencionar unidades, "
                "debe utilizar operation='sales_by_product'. "
                "sales_by_product ordena por monto equivalente en USD. "
                                "Use 'purchase_frequency' para analizar la frecuencia "
                "de compra de clientes dentro de un período. "
                "Permite responder cuántas órdenes realizó un cliente, "
                "su primera y última compra y cada cuántos días u horas "
                "compra aproximadamente. "
                "Use 'new_vs_recurring_customers' para comparar "
                "clientes nuevos y recurrentes dentro de un período. "
                "Cliente nuevo significa que su primera compra histórica "
                "ocurre dentro del período; recurrente significa que ya "
                "había comprado antes y vuelve a comprar en el período. "
                "Use 'sales_by_brand' para analizar ventas por marca "
                "dentro de un período, ordenadas por monto equivalente en USD. "
                "Permite consultar todas las marcas, marcas propias, "
                "marcas de terceros y separar las marcas de Amanecer y Aurora. "
                "La clasificación de propiedad de marca es controlada por IDs "
                "certificados y NO utiliza product_brand_model.brand_type. "
                "Cuando el usuario mencione una marca concreta, "
                "debe usar el parámetro brand con el nombre solicitado. "
                "Ejemplos: brand='AURORA', brand='AMANECER', "
                "brand='DELLA NONNA'. "
                "Para consultas del 29/09/2026 usadas en certificación, "
                "utilice conversion_date='2026-10-05'. "
                "Use 'sales_by_category' para analizar ventas "
                "por categoría de producto dentro de un período, "
                "ordenadas por monto equivalente en USD. "
                "La categoría corresponde a product_template.categ_id "
                "y se presenta usando product_category.complete_name. "
                "Cuando el usuario mencione una categoría concreta, "
                "utilice el parámetro category. "
                "Para consultas del 29/09/2026 usadas en certificación, "
                "utilice conversion_date='2026-10-05'. "
"Use operation='top_products' solamente cuando el usuario "
                "pida explícitamente cantidad o unidades vendidas. "
                "Use 'top_products' para obtener los productos más "
                "vendidos por cantidad o monto dentro de un período. "
                "Use 'top_warehouses' para obtener el ranking de "
                "sucursales comerciales por ventas dentro de un período. "
                "Use 'top_salespeople' para obtener el ranking de "
                "vendedores por monto de ventas dentro de un período. "
                "Use 'top_product_by_salesperson' para obtener el producto "
                "más vendido por unidades de cada uno de los vendedores "
                "líderes por monto de ventas dentro de un período. "
                "Use 'top_customers' para obtener el ranking de clientes "
                "consolidados por RIF/VAT según monto de ventas."
            ),
        ),

        warehouse: Optional[str] = Query(
            None,
            description=(
                "Sucursal o warehouse opcional. "
                "Nombre o código de la sucursal a filtrar. "
                "Si el usuario menciona una sucursal o localidad asociada "
                "a la consulta, conserve ese dato y envíelo en warehouse. "
                "Para operation='sales_invoice_detail' no omita warehouse "
                "cuando la pregunta identifique explícitamente una sucursal "
                "o localidad."
            ),
        ),

        customer: Optional[str] = Query(
            None,
            description=(
                "Cliente opcional para operation='sales_by_customer', "
                "operation='purchase_frequency' y "
                "operation='sales_invoice_detail'. "
                "Permite buscar por nombre o RIF/VAT del cliente."
            ),
        ),

        salesperson: Optional[str] = Query(
            None,
            description=(
                "Vendedor opcional para "
                "operation='sales_invoice_detail'. "
                "Busca por una o varias palabras del nombre del empleado. "
                "Conserve todas las palabras del nombre proporcionadas "
                "por el usuario. No reduzca el nombre a un término más general. "
                "Si el usuario asocia al vendedor con una sucursal o localidad, "
                "también debe enviar esa localidad en warehouse."
            ),
        ),

        product: Optional[str] = Query(
            None,
            description=(
                "Producto opcional para "
                "operation='sales_invoice_detail'. "
                "Si el usuario menciona explícitamente un producto, "
                "tipo de producto o producto junto con su marca, DEBE enviar "
                "esa expresión en product. "
                "Conserve todas las palabras relevantes indicadas por el usuario, "
                "incluyendo marca, presentación o variante. "
                "No omita product cuando el usuario haya indicado el producto "
                "y no reduzca el filtro a un término más general."
            ),
        ),

        sku: Optional[str] = Query(
            None,
            description=(
                "SKU opcional para "
                "operation='sales_invoice_detail'. "
                "Permite filtrar por código SKU."
            ),
        ),

        category: Optional[str] = Query(
            None,
            description=(
                "Solo para operation='sales_by_category'. "
                "Permite consultar una categoría específica "
                "por nombre o parte del complete_name."
            ),
        ),

        brand: Optional[str] = Query(
            None,
            description=(
                "Solo para operation='sales_by_brand'. "
                "Permite consultar una marca específica por nombre."
            ),
        ),

        brand_scope: Literal[
            "all",
            "own",
            "third_party",
        ] = Query(
            "all",
            description=(
                "Solo para operation='sales_by_brand'. "
                "all incluye todas las marcas y SIN MARCA; "
                "own incluye marcas propias de Grupo Amanecer/Aurora; "
                "third_party incluye marcas de terceros clasificadas. Cuando el usuario pregunte por marcas de terceros, marcas externas, marcas no propias o expresiones equivalentes, debe ejecutar operation='sales_by_brand' con brand_scope='third_party'."
            ),
        ),

        brand_group: Optional[
            Literal[
                "amanecer",
                "aurora",
            ]
        ] = Query(
            None,
            description=(
                "Solo para operation='sales_by_brand'. "
                "Filtra marcas propias por grupo. "
                "amanecer incluye AMANECER, DELLA NONNA y FAVORITO. "
                "aurora incluye AURORA."
            ),
        ),

        state: Optional[str] = Query(
            None,
            description=(
                "Estado geográfico opcional para operation='top_customers'. "
                "Nombre del estado geográfico a filtrar. "
                "Si se omite, no se filtra por estado."
            ),
        ),

        zone: Optional[str] = Query(
            None,
            description=(
                "Zona comercial opcional para operation='top_customers'. "
                "Nombre de la zona comercial a filtrar. "
                "Si se omite, no se filtra por zona comercial."
            ),
        ),

        as_of: Optional[str] = Query(
            None,
            description=(
                "Solo para operation='latest_confirmed'. "
                "Fecha/hora máxima ISO-8601 para buscar la última orden "
                "confirmada existente hasta ese momento."
            ),
        ),

        date_from: Optional[str] = Query(
            None,
            description=(
                "Obligatorio para operation='sales_summary', "
                "operation='top_products', "
                "operation='top_warehouses', "
                "operation='top_salespeople', "
                "operation='sales_by_salesperson', "
                "operation='sales_by_brand' y "
                "operation='top_product_by_salesperson'. "
                "Para operation='sales_invoice_detail' es obligatorio "
                "y filtra account_move.invoice_date. "
                "Inicio inclusivo del período en ISO-8601. "
                "Ejemplo: 2026-09-29T00:00:00."
            ),
        ),

        date_to: Optional[str] = Query(
            None,
            description=(
                "Obligatorio para operation='sales_summary', "
                "operation='top_products', "
                "operation='top_warehouses', "
                "operation='top_salespeople', "
                "operation='sales_by_brand' y "
                "operation='top_product_by_salesperson'. "
                "Para operation='sales_invoice_detail' es obligatorio "
                "y filtra account_move.invoice_date; el fin es exclusivo. "
                "Fin exclusivo del período en ISO-8601. "
                "Ejemplo: para consultar todo el 29/09/2026 use "
                "2026-09-30T00:00:00."
            ),
        ),

        period_a_from: Optional[str] = Query(
            None,
            description=(
                "Solo para operation='sales_period_comparison'. "
                "Inicio inclusivo del período A en ISO-8601."
            ),
        ),

        period_a_to: Optional[str] = Query(
            None,
            description=(
                "Solo para operation='sales_period_comparison'. "
                "Fin exclusivo del período A en ISO-8601."
            ),
        ),

        period_b_from: Optional[str] = Query(
            None,
            description=(
                "Solo para operation='sales_period_comparison'. "
                "Inicio inclusivo del período B en ISO-8601."
            ),
        ),

        period_b_to: Optional[str] = Query(
            None,
            description=(
                "Solo para operation='sales_period_comparison'. "
                "Fin exclusivo del período B en ISO-8601."
            ),
        ),

        sort_by: Literal[
            "quantity",
            "amount",
        ] = Query(
            "quantity",
            description=(
                "Solo para operation='top_products'. Este parámetro NO aplica a operation='sales_by_product'. sales_by_product siempre ordena por monto equivalente en USD. "
                "Use 'quantity' para ordenar por unidades vendidas "
                "o 'amount' para ordenar por monto equivalente en USD."
            ),
        ),

        commercial_network: Literal[
            "itc",
        ] = Query(
            "itc",
            description=(
                "Red comercial para operation='top_warehouses', "
                "operation='top_salespeople' y "
                "operation='top_product_by_salesperson'. "
                "Actualmente soporta 'itc', correspondiente a "
                "las sucursales comerciales ITC cuyos almacenes "
                "utilizan códigos PTDxx. "
                "Este parámetro NO aplica a operation='sales_by_salesperson'; "
                "sales_by_salesperson utiliza alcance general external_only."
            ),
        ),

        limit: int = Query(
            5,
            ge=1,
            le=20,
            description=(
                "Para operation='top_products', "
                "operation='top_warehouses', "
                "operation='top_salespeople' y "
                "operation='top_product_by_salesperson'. "
                                "También aplica a operation='sales_by_product'. "
                "También aplica a operation='purchase_frequency'. "
                "También aplica a operation='sales_by_brand'. "
                "También aplica a operation='sales_by_category'. "
"Cantidad máxima de resultados a devolver. "
                "Valor predeterminado: 5. Máximo: 20."
            ),
        ),

        conversion_date: Optional[str] = Query(
            None,
            description=(
                "Fecha de referencia para convertir VES a USD. "
                "Formato YYYY-MM-DD. "
                "Si se omite, se utiliza la fecha actual del servidor."
            ),
        ),
    ):

        require_api_key(request)
        started = datetime.now(timezone.utc)

        operation = operation.strip().lower()

        supported_operations = {
            "latest_confirmed",
            "sales_summary",
            "sales_period_comparison",
            "average_ticket",
            "sales_by_zone",
            "sales_by_state",
            "sales_by_channel",
            "sales_by_customer",
            "sales_by_salesperson",
            "sales_by_product",
            "purchase_frequency",
            "new_vs_recurring_customers",
            "sales_by_brand",
            "sales_by_category",
            "top_products",
            "top_warehouses",
            "top_salespeople",
            "top_product_by_salesperson",
            "top_customers",
        }

        if operation not in supported_operations:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Operación no soportada. "
                    "Operaciones disponibles: "
                    "latest_confirmed, sales_summary, "
                    "sales_period_comparison, average_ticket, "
                    "sales_by_zone, sales_by_state, sales_by_channel, "
                    "sales_by_customer, sales_invoice_detail, top_products, "
                    "top_warehouses, top_salespeople, "
                    "top_product_by_salesperson, top_customers, sales_by_brand, sales_by_category"
                ),
            )

        if operation == "latest_confirmed":
            return await handle_latest_confirmed(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                as_of=as_of,
            )


        if operation == "top_products":
            return await handle_top_products(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                date_from=date_from,
                date_to=date_to,
                sort_by=sort_by,
                conversion_date=conversion_date,
                limit=limit,
            )


        if operation == "top_product_by_salesperson":
            return await handle_top_product_by_salesperson(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                date_from=date_from,
                date_to=date_to,
                commercial_network=commercial_network,
                conversion_date=conversion_date,
                limit=limit,
            )


        if operation == "top_salespeople":
            return await handle_top_salespeople(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                date_from=date_from,
                date_to=date_to,
                commercial_network=commercial_network,
                conversion_date=conversion_date,
                limit=limit,
            )


        if operation == "top_warehouses":
            return await handle_top_warehouses(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                date_from=date_from,
                date_to=date_to,
                commercial_network=commercial_network,
                conversion_date=conversion_date,
                limit=limit,
            )


        if operation == "top_customers":
            return await handle_top_customers(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                state=state,
                zone=zone,
                date_from=date_from,
                date_to=date_to,
                commercial_network=commercial_network,
                conversion_date=conversion_date,
                limit=limit,
            )


        if operation == "sales_period_comparison":
            return await handle_sales_period_comparison(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                period_a_from=period_a_from,
                period_a_to=period_a_to,
                period_b_from=period_b_from,
                period_b_to=period_b_to,
                conversion_date=conversion_date,
            )


        if operation == "sales_by_state":
            return await handle_sales_by_state(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                date_from=date_from,
                date_to=date_to,
                conversion_date=conversion_date,
                limit=limit,
            )


        if operation == "sales_by_channel":
            return await handle_sales_by_channel(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                date_from=date_from,
                date_to=date_to,
                conversion_date=conversion_date,
                limit=limit,
            )

        if operation == "sales_by_customer":
            return await handle_sales_by_customer(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                customer=customer,
                date_from=date_from,
                date_to=date_to,
                conversion_date=conversion_date,
                limit=limit,
            )


        if operation == "sales_by_category":
            return await handle_sales_by_category(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                category=category,
                date_from=date_from,
                date_to=date_to,
                conversion_date=conversion_date,
                limit=limit,
            )


        if operation == "sales_by_brand":
            return await handle_sales_by_brand(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                date_from=date_from,
                date_to=date_to,
                conversion_date=conversion_date,
                limit=limit,
                brand_scope=brand_scope,
                brand_group=brand_group,
                brand=brand,
            )


        if operation == "new_vs_recurring_customers":
            return await handle_new_vs_recurring_customers(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                date_from=date_from,
                date_to=date_to,
            )


        if operation == "purchase_frequency":
            return await handle_purchase_frequency(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                customer=customer,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            )


        if operation == "sales_by_product":
            return await handle_sales_by_product(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                date_from=date_from,
                date_to=date_to,
                conversion_date=conversion_date,
                limit=limit,
            )


        if operation == "sales_by_salesperson":
            return await handle_sales_by_salesperson(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                date_from=date_from,
                date_to=date_to,
                conversion_date=conversion_date,
                limit=limit,
            )


        if operation == "sales_by_zone":
            return await handle_sales_by_zone(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                date_from=date_from,
                date_to=date_to,
                conversion_date=conversion_date,
                limit=limit,
            )


        if operation == "average_ticket":
            return await handle_average_ticket(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                date_from=date_from,
                date_to=date_to,
                conversion_date=conversion_date,
            )


        if operation == "sales_summary":
            return await handle_sales_summary(
                operation=operation,
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                date_from=date_from,
                date_to=date_to,
                conversion_date=conversion_date,
            )
