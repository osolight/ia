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
from sales.latest import handle_latest_confirmed
from sales.products import handle_top_products
from sales.products_by_salesperson import handle_top_product_by_salesperson
from sales.warehouses import handle_top_warehouses
from sales.summaries import handle_sales_summary
from sales.customers import handle_top_customers


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
        "/sales/query",
        operation_id="sales_query",
        summary="Consulta genérica certificada de ventas",
        description=(
            "Consulta controlada de ventas de Grupo Amanecer. "
            "Soporta latest_confirmed, sales_summary, top_products, "
            "top_warehouses, top_salespeople y "
            "top_product_by_salesperson. "
            "Permite filtros controlados por warehouse y períodos."
        ),
    )

    async def sales_query(
        request: Request,

        operation: Literal[
            "latest_confirmed",
            "sales_summary",
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
                "Use 'sales_summary' para totales, montos o cantidad "
                "de órdenes dentro de un período. "
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
                "Ejemplos: VALENCIA, CALABOZO, CARACAS. "
                "Si se omite, la consulta aplica a todas las sucursales."
            ),
        ),

        state: Optional[str] = Query(
            None,
            description=(
                "Estado geográfico opcional para operation='top_customers'. "
                "Ejemplos: MIRANDA, CARABOBO, LARA. "
                "Si se omite, no se filtra por estado."
            ),
        ),

        zone: Optional[str] = Query(
            None,
            description=(
                "Zona comercial opcional para operation='top_customers'. "
                "Ejemplos: BARINAS-EL CARMEN, VALENCIA-SAN BLAS. "
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
                "operation='top_salespeople' y "
                "operation='top_product_by_salesperson'. "
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
                "operation='top_salespeople' y "
                "operation='top_product_by_salesperson'. "
                "Fin exclusivo del período en ISO-8601. "
                "Ejemplo: para consultar todo el 29/09/2026 use "
                "2026-09-30T00:00:00."
            ),
        ),

        sort_by: Literal[
            "quantity",
            "amount",
        ] = Query(
            "quantity",
            description=(
                "Solo para operation='top_products'. "
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
                "utilizan códigos PTDxx."
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
                    "top_products, top_warehouses, top_salespeople, "
                    "top_product_by_salesperson, top_customers"
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

