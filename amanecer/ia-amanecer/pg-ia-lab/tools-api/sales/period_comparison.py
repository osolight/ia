"""
Comparación certificada de dos períodos de ventas.

Reutiliza handle_sales_summary para mantener una sola lógica
de filtros, monedas y conversión VES -> USD.
"""

from datetime import datetime, timezone

from fastapi import HTTPException

from sales.summaries import handle_sales_summary


def _variation_pct(current, previous):
    """
    Variación porcentual usando B como base.

    Si el período B es cero, la variación porcentual no es
    matemáticamente calculable y se devuelve None.
    """
    if previous is None or previous == 0:
        return None

    return round(
        float(
            ((current - previous) / previous) * 100
        ),
        2,
    )


def _extract_period(result, date_from, date_to):
    """
    Normaliza el resultado de sales_summary.

    Un período sin órdenes se representa con ceros.
    """

    if not result.get("found"):
        return {
            "found": False,
            "date_from": date_from,
            "date_to": date_to,
            "order_count": 0,
            "usd": {
                "order_count": 0,
                "amount_total": 0,
            },
            "ves": {
                "order_count": 0,
                "amount_total": 0,
            },
            "total_equivalent_usd": 0,
            "missing_rate_order_count": 0,
        }

    summary = result["summary"]
    conversion = summary["conversion"]

    return {
        "found": True,
        "date_from": date_from,
        "date_to": date_to,
        "order_count": summary["overall_order_count"],
        "usd": {
            "order_count": summary["usd"]["order_count"],
            "amount_total": summary["usd"]["amount_total"],
        },
        "ves": {
            "order_count": summary["ves"]["order_count"],
            "amount_total": summary["ves"]["amount_total"],
        },
        "total_equivalent_usd": conversion.get(
            "total_equivalent_usd"
        ),
        "missing_rate_order_count": conversion.get(
            "missing_rate_order_count"
        ),
    }


async def handle_sales_period_comparison(
    *,
    operation,
    request,
    pool_getter,
    audit_write,
    client_host,
    started,
    parse_iso_datetime,
    warehouse,
    period_a_from,
    period_a_to,
    period_b_from,
    period_b_to,
    conversion_date,
):

    required = {
        "period_a_from": period_a_from,
        "period_a_to": period_a_to,
        "period_b_from": period_b_from,
        "period_b_to": period_b_to,
    }

    missing = [
        name
        for name, value in required.items()
        if not value
    ]

    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                "sales_period_comparison requiere "
                "period_a_from, period_a_to, "
                "period_b_from y period_b_to."
            ),
        )

    # Validación explícita de fechas antes de ejecutar consultas.
    a_from_dt = parse_iso_datetime(
        period_a_from,
        "period_a_from",
    )
    a_to_dt = parse_iso_datetime(
        period_a_to,
        "period_a_to",
    )
    b_from_dt = parse_iso_datetime(
        period_b_from,
        "period_b_from",
    )
    b_to_dt = parse_iso_datetime(
        period_b_to,
        "period_b_to",
    )

    if a_to_dt <= a_from_dt:
        raise HTTPException(
            status_code=400,
            detail=(
                "period_a_to debe ser posterior "
                "a period_a_from."
            ),
        )

    if b_to_dt <= b_from_dt:
        raise HTTPException(
            status_code=400,
            detail=(
                "period_b_to debe ser posterior "
                "a period_b_from."
            ),
        )

    # ---------------------------------------------------------
    # Reutilizar sales_summary certificado.
    # ---------------------------------------------------------

    result_a = await handle_sales_summary(
        operation="sales_summary",
        request=request,
        pool_getter=pool_getter,
        audit_write=audit_write,
        client_host=client_host,
        started=datetime.now(timezone.utc),
        parse_iso_datetime=parse_iso_datetime,
        warehouse=warehouse,
        date_from=period_a_from,
        date_to=period_a_to,
        conversion_date=conversion_date,
    )

    result_b = await handle_sales_summary(
        operation="sales_summary",
        request=request,
        pool_getter=pool_getter,
        audit_write=audit_write,
        client_host=client_host,
        started=datetime.now(timezone.utc),
        parse_iso_datetime=parse_iso_datetime,
        warehouse=warehouse,
        date_from=period_b_from,
        date_to=period_b_to,
        conversion_date=conversion_date,
    )

    period_a = _extract_period(
        result_a,
        period_a_from,
        period_a_to,
    )

    period_b = _extract_period(
        result_b,
        period_b_from,
        period_b_to,
    )

    orders_a = period_a["order_count"]
    orders_b = period_b["order_count"]

    order_difference = orders_a - orders_b
    order_variation_pct = _variation_pct(
        orders_a,
        orders_b,
    )

    amount_a = period_a["total_equivalent_usd"]
    amount_b = period_b["total_equivalent_usd"]

    amount_difference = None
    amount_variation_pct = None

    amount_comparable = (
        amount_a is not None
        and amount_b is not None
        and period_a["missing_rate_order_count"] == 0
        and period_b["missing_rate_order_count"] == 0
    )

    if amount_comparable:
        amount_difference = amount_a - amount_b
        amount_variation_pct = _variation_pct(
            amount_a,
            amount_b,
        )

    duration_ms = int(
        (
            datetime.now(timezone.utc)
            - started
        ).total_seconds() * 1000
    )

    comparison = {
        "warehouse": warehouse,
        "conversion_date": conversion_date,
        "period_a": period_a,
        "period_b": period_b,
        "difference": {
            "order_count": order_difference,
            "amount_usd": amount_difference,
        },
        "variation": {
            "order_count_pct": order_variation_pct,
            "amount_usd_pct": amount_variation_pct,
        },
        "amount_comparable": amount_comparable,
    }

    audit_write({
        "action": "sales_query",
        "status": "ok",
        "client_host": client_host(request),
        "operation": operation,
        "filters": {
            "warehouse": warehouse,
            "period_a_from": period_a_from,
            "period_a_to": period_a_to,
            "period_b_from": period_b_from,
            "period_b_to": period_b_to,
            "conversion_date": conversion_date,
        },
        "duration_ms": duration_ms,
        "period_a_order_count": orders_a,
        "period_b_order_count": orders_b,
    })

    if amount_comparable:
        amount_a_text = f"$ {float(amount_a):,.2f} USD"
        amount_b_text = f"$ {float(amount_b):,.2f} USD"
        amount_difference_text = (
            f"$ {float(amount_difference):,.2f} USD"
        )

        order_pct_text = (
            f"{order_variation_pct:+.2f}%"
            if order_variation_pct is not None
            else "no calculable"
        )

        amount_pct_text = (
            f"{amount_variation_pct:+.2f}%"
            if amount_variation_pct is not None
            else "no calculable"
        )

        answer = (
            f"El período A registra {orders_a:,} órdenes "
            f"por {amount_a_text}, frente a "
            f"{orders_b:,} órdenes por {amount_b_text} "
            f"en el período B. "
            f"La diferencia es de {order_difference:+,} órdenes "
            f"({order_pct_text}) y "
            f"{amount_difference_text} "
            f"({amount_pct_text}) en monto de ventas."
        )
    else:
        answer = (
            f"El período A registra {orders_a:,} órdenes "
            f"y el período B {orders_b:,}. "
            f"La diferencia es de {order_difference:+,} órdenes. "
            f"No es posible calcular de forma segura la variación "
            f"del monto equivalente en USD porque falta una tasa "
            f"de conversión aplicable en uno de los períodos."
        )

    return {
        "found": (
            period_a["found"]
            or period_b["found"]
        ),
        "answer": answer,
        "comparison": comparison,
    }
