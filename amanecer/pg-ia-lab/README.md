# pg-ia-lab

Backend del Asesor Gerencial IA de Grupo Amanecer.

## Arquitectura

    Usuario
      ↓
    Open WebUI
      ↓
    Asesor Gerencial IA
      ↓
    sales_query / sales_chart
      ↓
    pg-tools (FastAPI)
      ↓
    PostgreSQL réplica Odoo

El LLM no ejecuta SQL directamente.

Las consultas empresariales se realizan mediante operaciones controladas en el backend.

## Operaciones actuales

- latest_confirmed
- sales_summary
- top_products
- top_warehouses
- top_salespeople
- top_product_by_salesperson
- top_customers

## Gráficos

El endpoint `sales_chart` genera gráficos reutilizando las mismas operaciones certificadas del backend.

No mantiene SQL independiente.

## Regla principal

**Backend estable + contratos simples + modelo intercambiable.**

Las reglas de negocio deben permanecer en el backend y no depender del comportamiento particular del modelo de lenguaje.

## Validación

Cada nueva capacidad sigue esta metodología:

    SQL de referencia
      ↓
    implementación
      ↓
    curl
      ↓
    comparación
      ↓
    aprobación
      ↓
    Open WebUI

Para datos variables se congela un identificador o fecha de corte.

Para agregados se utilizan períodos cerrados cuando sea posible.
