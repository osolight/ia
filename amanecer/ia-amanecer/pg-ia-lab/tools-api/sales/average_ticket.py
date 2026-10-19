"""
Ticket promedio certificado de ventas.

Reutiliza sales_summary para mantener una sola definición
de ventas, período, sucursal y conversión monetaria.
"""

from datetime import datetime, timezone

from fastapi import HTTPException

from sales.summaries import handle_sales_summary


async def handle_average_ticket(
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

    if not date_from or not date_to:
        raise HTTPException(
            status_code=400,
            detail=(
                "average_ticket requiere "
                "date_from y date_to."
            ),
        )

    # La validación real de fechas y toda la lógica monetaria
    # permanecen centralizadas en sales_summary.
    result = await handle_sales_summary(
        operation="sales_summary",
        request=request,
        pool_getter=pool_getter,
        audit_write=audit_write,
        client_host=client_host,
        started=datetime.now(timezone.utc),
        parse_iso_datetime=parse_iso_datetime,
        warehouse=warehouse,
        date_from=date_from,
        date_to=date_to,
        conversion_date=conversion_date,
    )

    duration_ms = int(
        (
            datetime.now(timezone.utc)
            - started
        ).total_seconds() * 1000
    )

    if not result.get("found"):
        audit_write({
            "action": "sales_query",
            "status": "ok",
            "client_host": client_host(request),
            "operation": operation,
            "filters": {
                "warehouse": warehouse,
                "date_from": date_from,
                "date_to": date_to,
                "conversion_date": conversion_date,
            },
            "duration_ms": duration_ms,
            "order_count": 0,
        })

        return {
            "found": False,
            "answer": (
                "No se encontraron órdenes confirmadas "
                "en el período consultado; por tanto, "
                "no existe ticket promedio para ese período."
            ),
            "average_ticket": {
                "warehouse": warehouse,
                "date_from": date_from,
                "date_to": date_to,
                "order_count": 0,
                "total_equivalent_usd": 0,
                "average_ticket_usd": None,
                "average_ticket_usd_text": None,
            },
        }

    summary = result["summary"]
    conversion = summary["conversion"]

    order_count = summary["overall_order_count"]
    amount = conversion.get("total_equivalent_usd")
    missing_rate_count = conversion.get(
        "missing_rate_order_count",
        0,
    )

    comparable = (
        order_count > 0
        and amount is not None
        and missing_rate_count == 0
    )

    average = None

    if comparable:
        average = amount / order_count

    data = {
        "warehouse": warehouse,
        "date_from": date_from,
        "date_to": date_to,
        "conversion_date": conversion.get(
            "conversion_date"
        ),
        "order_count": order_count,
        "total_equivalent_usd": amount,
        "average_ticket_usd": average,
        "average_ticket_usd_text": (
            f"$ {float(average):,.2f} USD"
            if average is not None
            else None
        ),
        "amount_comparable": comparable,
        "missing_rate_order_count": missing_rate_count,
    }

    audit_write({
        "action": "sales_query",
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": {
            "warehouse": warehouse,
            "date_from": date_from,
            "date_to": date_to,
            "conversion_date": conversion_date,
        },
        "duration_ms": duration_ms,
        "order_count": order_count,
    })

    if comparable:
        answer = (
            f"El período consultado registra "
            f"{order_count:,} órdenes confirmadas, "
            f"con un ticket promedio de "
            f"$ {float(average):,.2f} USD."
        )
    else:
        answer = (
            f"El período consultado registra "
            f"{order_count:,} órdenes confirmadas, "
            f"pero no es posible calcular de forma segura "
            f"el ticket promedio equivalente en USD."
        )

    return {
        "found": True,
        "answer": answer,
        "average_ticket": data,
    }
