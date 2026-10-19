"""
Visualizaciones de ventas para Open WebUI.

No implementa lógica de negocio ni SQL propio.
Reutiliza los handlers certificados de ventas y transforma
sus resultados estructurados en HTML para Rich UI.
"""

from datetime import datetime, timezone
from html import escape
from typing import Literal, Optional

from fastapi import HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from sales.customers import handle_top_customers
from sales.products import handle_top_products
from sales.salespeople import handle_top_salespeople
from sales.warehouses import handle_top_warehouses
from sales.sales_by_category import handle_sales_by_category
from sales_tools import parse_iso_datetime


AUTOHEIGHT_SCRIPT = """
<script>
function reportHeight() {
    const h = document.documentElement.scrollHeight;

    parent.postMessage(
        { type: "iframe:height", height: h },
        "*"
    );
}

window.addEventListener("load", reportHeight);

new ResizeObserver(reportHeight).observe(document.body);
</script>
"""


def _html_response(html: str) -> HTMLResponse:
    return HTMLResponse(
        content=html,
        headers={
            "Content-Disposition": 'inline; filename="sales-chart.html"'
        },
    )


def _render_empty_chart(title: str) -> HTMLResponse:
    html = f"""
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<style>
* {{
    box-sizing: border-box;
}}

body {{
    margin: 0;
    padding: 8px;
    font-family:
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
    background: #ffffff;
    color: #262626;
    font-size: 13px;
}}

h2 {{
    margin: 0 0 6px 0;
    font-size: 15px;
}}

p {{
    margin: 0;
    color: #666666;
}}
</style>
</head>

<body>

<h2>{escape(title)}</h2>
<p>No se encontraron datos para el período consultado.</p>

{AUTOHEIGHT_SCRIPT}

</body>
</html>
"""

    return _html_response(html)


def _render_ranked_bar_chart(
    *,
    title: str,
    subtitle: str,
    items: list,
    name_key: str,
    value_key: str = "total_equivalent_usd",
    value_mode: str = "usd",
    secondary_key: str = "order_count",
    secondary_label: str = "órdenes",
) -> HTMLResponse:

    max_value = max(
        float(item.get(value_key) or 0)
        for item in items
    )

    rows = []

    for item in items:
        name = escape(
            str(item.get(name_key) or "Sin nombre")
        )

        value = float(
            item.get(value_key) or 0
        )

        secondary_value = float(
            item.get(secondary_key) or 0
        )

        rank = int(
            item.get("rank") or 0
        )

        if max_value > 0:
            width = (value / max_value) * 100
        else:
            width = 0

        if value_mode == "quantity":
            value_text = f"{value:,.0f}"
            title_text = f"{value:,.4f} unidades"
        else:
            value_text = f"${value:,.2f}"
            title_text = f"${value:,.2f} USD"

        if secondary_label == "unidades":
            secondary_text = (
                f"{secondary_value:,.0f} unidades"
            )
        else:
            secondary_text = (
                f"{int(secondary_value):,} {secondary_label}"
            )

        rows.append(
            f"""
            <div class="row">

                <div class="label">
                    <span class="rank">{rank}</span>
                    <span class="name">{name}</span>
                </div>

                <div class="bar-area">
                    <div
                        class="bar"
                        style="width: {width:.2f}%"
                        title="{title_text}"
                    ></div>
                </div>

                <div class="value">
                    <strong>{value_text}</strong>
                    <span>{secondary_text}</span>
                </div>

            </div>
            """
        )

    html = f"""
<!doctype html>
<html lang="es">
<head>

<meta charset="utf-8">
<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>{escape(title)}</title>

<style>

* {{
    box-sizing: border-box;
}}

html,
body {{
    overflow: hidden;
}}

body {{
    margin: 0;
    padding: 4px 8px;

    font-family:
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    background: #ffffff;
    color: #262626;

    font-size: 13px;
    line-height: 1.25;
}}

.card {{
    width: 100%;
    border: 0;
    padding: 2px 4px;
    background: #ffffff;
}}

h2 {{
    margin: 0 0 2px 0;
    font-size: 15px;
    font-weight: 600;
}}

.subtitle {{
    margin: 0 0 5px 0;
    color: #666666;
    font-size: 12px;
}}

.row {{
    display: grid;

    grid-template-columns:
        minmax(260px, 2.4fr)
        3fr
        minmax(120px, 1fr);

    gap: 8px;
    align-items: center;

    margin: 3px 0;
}}

.label {{
    display: flex;
    align-items: center;
    gap: 7px;
    min-width: 0;
}}

.rank {{
    display: inline-flex;
    align-items: center;
    justify-content: center;

    width: 21px;
    height: 21px;

    border-radius: 50%;

    background: #553328;
    color: #ffffff;

    font-size: 11px;
    font-weight: 600;

    flex: 0 0 auto;
}}

.name {{
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;

    font-weight: 500;
}}

.bar-area {{
    width: 100%;
    height: 14px;

    border-radius: 7px;
    background: #e5e5e5;

    overflow: hidden;
}}

.bar {{
    height: 100%;
    min-width: 2px;

    border-radius: 7px;

    background:
        linear-gradient(
            90deg,
            #553328 0%,
            #B17940 100%
        );
}}

.value {{
    display: flex;
    flex-direction: column;

    text-align: right;
    white-space: nowrap;
}}

.value strong {{
    font-size: 13px;
    font-weight: 600;
}}

.value span {{
    color: #666666;
    font-size: 11px;
}}

@media (max-width: 720px) {{

    .row {{
        grid-template-columns:
            minmax(190px, 2fr)
            2fr
            minmax(100px, 1fr);
    }}

}}

</style>
</head>

<body>

<div class="card">

    <h2>{escape(title)}</h2>

    <p class="subtitle">
        {escape(subtitle)}
    </p>

    {''.join(rows)}

</div>

{AUTOHEIGHT_SCRIPT}

</body>
</html>
"""

    return _html_response(html)


def register_sales_chart_routes(
    *,
    app,
    pool_getter,
    require_api_key,
    audit_write,
    client_host,
):

    @app.get(
        "/sales/chart",
        operation_id="sales_chart",
        summary="Gráfico certificado de ventas",
        description=(
            "Genera visualizaciones usando los mismos datos certificados "
            "de sales_query. "
            "Use esta herramienta UNICAMENTE cuando el usuario solicite "
            "explícitamente una gráfica, gráfico, chart o visualización. "
            "No la invoque automáticamente después de sales_query. "
            "No interprete ejemplos o descripciones de parámetros como "
            "solicitudes del usuario ni como datos empresariales. "
            "Soporta top_salespeople, top_warehouses, top_products, top_customers y sales_by_category. ""Cuando el usuario solicite un gráfico de categorías, ranking visual de categorías o barras de categorías, use operation='sales_by_category'. Para la certificación del 29/09/2026 utilice conversion_date='2026-10-05'."
            "Cuando el usuario indique una sola fecha, "
            "date_from debe ser el inicio de ese día y "
            "date_to debe ser el inicio del día siguiente, "
            "porque date_to es exclusivo. "
            "Ejemplo: para el 29/09/2026 use "
            "date_from='2026-09-29T00:00:00' y "
            "date_to='2026-09-30T00:00:00'. "
        ),
        response_class=HTMLResponse,
    )
    async def sales_chart(
        request: Request,

        operation: Literal[
            "top_salespeople",
            "top_warehouses",
            "top_products",
            "top_customers",
            "sales_by_category",
        ] = Query(
            "top_salespeople",
            description=(
                "Tipo de gráfico: "
                "top_salespeople, top_warehouses, top_products, top_customers o sales_by_category."
            ),
        ),

        warehouse: Optional[str] = Query(
            None,
            description=(
                "Sucursal opcional para vendedores, productos o clientes, "
                "Indique el nombre o código de la sucursal."
            ),
        ),

        state: Optional[str] = Query(
            None,
            description=(
                "Estado geográfico opcional para top_customers, "
                "Indique el nombre del estado geográfico."
            ),
        ),

        zone: Optional[str] = Query(
            None,
            description=(
                "Zona comercial opcional para top_customers, "
                "Indique el nombre de la zona comercial."
            ),
        ),

        date_from: str = Query(
            ...,
            description="Inicio del período, inclusivo.",
        ),

        date_to: str = Query(
            ...,
            description="Fin del período, exclusivo.",
        ),

        commercial_network: str = Query(
            "itc",
            description="Red comercial. Actualmente: itc.",
        ),

        conversion_date: Optional[str] = Query(
            None,
            description="Fecha de conversión monetaria opcional.",
        ),

        sort_by: Literal[
            "quantity",
            "amount",
        ] = Query(
            "quantity",
            description=(
                "Para top_products: quantity por unidades "
                "o amount por monto equivalente USD."
            ),
        ),

        limit: int = Query(
            5,
            ge=1,
            le=20,
            description="Cantidad de elementos a mostrar.",
        ),
    ):

        require_api_key(request)

        started = datetime.now(timezone.utc)

        # -----------------------------------------------------
        # TOP VENDEDORES
        # -----------------------------------------------------

        if operation == "top_salespeople":

            result = await handle_top_salespeople(
                operation="top_salespeople",
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

            items = result.get("salespeople", [])

            if not items:
                return _render_empty_chart(
                    "Ranking de vendedores"
                )

            location = (
                warehouse.upper()
                if warehouse
                else "Nacional"
            )

            return _render_ranked_bar_chart(
                title=f"Top {len(items)} vendedores",
                subtitle=(
                    f"{location} · "
                    f"{date_from} → {date_to}"
                ),
                items=items,
                name_key="salesperson_name",
            )

        # -----------------------------------------------------
        # TOP SUCURSALES
        # -----------------------------------------------------

        if operation == "top_warehouses":

            result = await handle_top_warehouses(
                operation="top_warehouses",
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

            items = result.get("warehouses", [])

            if not items:
                return _render_empty_chart(
                    "Ranking de sucursales"
                )

            return _render_ranked_bar_chart(
                title=f"Top {len(items)} sucursales",
                subtitle=(
                    f"ITC · "
                    f"{date_from} → {date_to}"
                ),
                items=items,
                name_key="warehouse_name",
            )

        # -----------------------------------------------------
        # TOP PRODUCTOS
        # -----------------------------------------------------

        if operation == "top_products":

            result = await handle_top_products(
                operation="top_products",
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

            items = result.get("products", [])

            if not items:
                return _render_empty_chart(
                    "Ranking de productos"
                )

            location = (
                warehouse.upper()
                if warehouse
                else "Nacional"
            )

            if sort_by == "quantity":

                return _render_ranked_bar_chart(
                    title=f"Top {len(items)} productos por cantidad",
                    subtitle=(
                        f"{location} · "
                        f"{date_from} → {date_to}"
                    ),
                    items=items,
                    name_key="product_name",
                    value_key="qty_sold",
                    value_mode="quantity",
                    secondary_key="qty_sold",
                    secondary_label="unidades",
                )

            return _render_ranked_bar_chart(
                title=f"Top {len(items)} productos por monto",
                subtitle=(
                    f"{location} · "
                    f"{date_from} → {date_to}"
                ),
                items=items,
                name_key="product_name",
                value_key="total_equivalent_usd",
                value_mode="usd",
                secondary_key="qty_sold",
                secondary_label="unidades",
            )

        # -----------------------------------------------------
        # TOP CLIENTES
        # -----------------------------------------------------

        if operation == "top_customers":

            result = await handle_top_customers(
                operation="top_customers",
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

            items = result.get("customers", [])

            if not items:
                return _render_empty_chart(
                    "Ranking de clientes"
                )

            location_parts = []

            if warehouse:
                location_parts.append(warehouse.upper())

            if state:
                location_parts.append(
                    f"Estado {state.upper()}"
                )

            if zone:
                location_parts.append(
                    f"Zona {zone.upper()}"
                )

            location = (
                " · ".join(location_parts)
                if location_parts
                else "Nacional"
            )

            return _render_ranked_bar_chart(
                title=f"Top {len(items)} clientes",
                subtitle=(
                    f"{location} · "
                    f"{date_from} → {date_to}"
                ),
                items=items,
                name_key="customer_name",
                value_key="total_equivalent_usd",
                value_mode="usd",
                secondary_key="order_count",
                secondary_label="órdenes",
            )

        # -----------------------------------------------------
        # TOP CATEGORIAS
        # F020 - reutiliza handler certificado
        # -----------------------------------------------------

        if operation == "sales_by_category":

            result = await handle_sales_by_category(
                operation="sales_by_category",
                request=request,
                pool_getter=pool_getter,
                audit_write=audit_write,
                client_host=client_host,
                started=started,
                parse_iso_datetime=parse_iso_datetime,
                warehouse=warehouse,
                category=None,
                date_from=date_from,
                date_to=date_to,
                conversion_date=conversion_date,
                limit=limit,
            )

            items = result.get("categories", [])

            if not items:
                return _render_empty_chart(
                    "Ranking de categorías"
                )

            location = (
                warehouse.upper()
                if warehouse
                else "Nacional"
            )

            return _render_ranked_bar_chart(
                title=f"Top {len(items)} categorías por ventas",
                subtitle=(
                    f"{location} · "
                    f"{date_from} → {date_to}"
                ),
                items=items,
                name_key="category_name",
                value_key="total_equivalent_usd",
                value_mode="usd",
                secondary_key="quantity",
                secondary_label="unidades",
            )

        raise HTTPException(
            status_code=400,
            detail="Operación gráfica no soportada.",
        )
