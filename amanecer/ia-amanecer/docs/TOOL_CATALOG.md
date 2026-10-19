# Catálogo de herramientas IA Amanecer

Revisión: 2026-10-19. Base: `restructure/ia-amanecer`, commit `7654359b600e61fa9e789a38cb80a3f7db67b262`.

## Estados y evidencia

| Estado | Criterio |
| --- | --- |
| IMPLEMENTADO | Ruta/dispatcher y handler presentes en el commit revisado. No acredita despliegue ni corrección funcional. |
| EN UAT | Hay evidencia de ejecución de aceptación en curso, aún sin aprobación. |
| CERTIFICADO DEV | Comparación SQL/backend y validación Open WebUI DEV aprobadas e identificables. |
| CERTIFICADO PROD | Evidencia de promoción y validación aprobada en PROD para una versión identificada. |

Las 22 operaciones de negocio están IMPLEMENTADAS. No hay evidencia suficiente para asignar EN UAT, CERTIFICADO DEV o CERTIFICADO PROD a una operación concreta. Esto significa **no acreditado**, no que se haya demostrado ausencia de pruebas en los servidores.

La declaración histórica de [operaciones-certificadas.md](operaciones-certificadas.md) se conserva en una columna separada porque no identifica entorno, versión aprobada ni resultados reproducibles. Los comentarios “certificado” del código no ascienden un estado. Véase [UAT_CERTIFIED.md](UAT_CERTIFIED.md).

## Dispatcher GET /sales/query

`sales_tools.py` define `operation_id="sales_query"` y acepta exactamente estas 19 operaciones. Los módulos se indican relativos a `pg-ia-lab/tools-api/sales/`. Cada handler se llama `handle_<operación>`.

| Operación | Módulo | Función de negocio | Estado verificable | Certificación documental sin entorno |
| --- | --- | --- | --- | --- |
| `latest_confirmed` | `latest.py` | Último pedido confirmado; sucursal y corte as_of. | IMPLEMENTADO | Declarada |
| `sales_summary` | `summaries.py` | Resumen por moneda, conversión, total_kg_sold y período canónico. | IMPLEMENTADO | Declarada solo para operación base |
| `top_products` | `products.py` | Productos por cantidad o monto. | IMPLEMENTADO | Declarada |
| `top_warehouses` | `warehouses.py` | Sucursales de red ITC. | IMPLEMENTADO | Declarada |
| `top_salespeople` | `salespeople.py` | Vendedores por monto, red ITC. | IMPLEMENTADO | Declarada |
| `top_product_by_salesperson` | `products_by_salesperson.py` | Producto principal por vendedor. | IMPLEMENTADO | Declarada |
| `top_customers` | `customers.py` | Clientes; identificación fiscal y filtros geográficos. | IMPLEMENTADO | Declarada |
| `sales_period_comparison` | `period_comparison.py` | Comparación de dos períodos reutilizando resumen. | IMPLEMENTADO | No consta |
| `average_ticket` | `average_ticket.py` | Ticket promedio reutilizando resumen. | IMPLEMENTADO | No consta |
| `sales_by_zone` | `zones.py` | Agregado por zona. | IMPLEMENTADO | No consta |
| `sales_by_state` | `states.py` | Agregado por estado. | IMPLEMENTADO | No consta |
| `sales_by_channel` | `channels.py` | Agregado por canal. | IMPLEMENTADO | No consta |
| `sales_by_customer` | `sales_by_customer.py` | Ventas por cliente. | IMPLEMENTADO | No consta |
| `sales_by_salesperson` | `sales_by_salesperson.py` | Ventas por vendedor; alcance general external_only. | IMPLEMENTADO | No consta |
| `sales_by_product` | `sales_by_product.py` | Ventas por producto, orden por monto equivalente USD. | IMPLEMENTADO | No consta |
| `sales_by_brand` | `sales_by_brand.py` | Marcas; alcance propias/terceros y grupo. | IMPLEMENTADO | No consta |
| `sales_by_category` | `sales_by_category.py` | Ventas por categoría. | IMPLEMENTADO | No consta |
| `purchase_frequency` | `purchase_frequency.py` | Frecuencia de compra de clientes. | IMPLEMENTADO | No consta |
| `new_vs_recurring_customers` | `new_vs_recurring_customers.py` | Clientes nuevos frente a recurrentes. | IMPLEMENTADO | No consta |

No todos los filtros de la ruta genérica se transmiten a todos los handlers. El contrato efectivo requiere revisar la rama de despacho y la firma del módulo, no solamente las descripciones de parámetros.

## Rutas específicas en sales_tools.py

| operation_id | Ruta GET | Implementación | Alcance | Estado |
| --- | --- | --- | --- | --- |
| `sales_latest_confirmed` | `/sales/latest-confirmed` | SQL dentro de `sales_tools.py`. | Último pedido confirmado; ruta adicional a `operation=latest_confirmed`. | IMPLEMENTADO; certificación de esta ruta concreta no acreditada por entorno. |
| `sales_invoice_detail` | `/sales/invoice-detail` | `sales/sales_invoice_detail.py`, `handle_sales_invoice_detail`. | Facturas publicadas por vendedor, producto, SKU y cliente; detalle limitado y resumen completo. | IMPLEMENTADO. |
| `sales_product_ranking_by_salesperson` | `/sales/product-ranking-by-salesperson` | `sales/sales_product_ranking_by_salesperson.py`, handler homónimo con prefijo `handle_`. | SKU facturados por vendedor y monto equivalente USD. | IMPLEMENTADO. |
| `sales_salesperson_ranking_by_invoiced_kg` | `/sales/salesperson-ranking-by-invoiced-kg` | `sales/sales_salesperson_ranking_by_invoiced_kg.py`, handler homónimo con prefijo `handle_`. | Ranking de vendedores/sucursales por kilos facturados. | IMPLEMENTADO. |

`sales_invoice_detail` figura en textos de `/sales/query`, pero está ausente de `supported_operations` y del despacho. Debe invocarse por su ruta específica. Las otras dos herramientas de facturación también tienen rutas propias.

## Contratos que requieren especial atención

| Capacidad | Contrato observado | Certificación específica |
| --- | --- | --- |
| `sales_summary.total_kg_sold` | Líneas de pedidos confirmados, conversión de unidades de peso, mismos filtros de estado/período/almacén que el resumen. Devuelve valor y texto en `executive_summary`. | No acreditada; la declaración genérica de sales_summary no prueba este campo. |
| Período canónico del resumen | `executive_summary.period`: `date_from`, `date_to_exclusive`, `date_to_inclusive`, `period_text`. | No acreditada. |
| Detalle de facturación | Facturas `posted/out_invoice`, fecha `invoice_date`; detalle máximo 20, resumen de todas las filas consultadas. | No acreditada. |
| Ranking de SKU | Vendedor obligatorio; orden por USD, kilos y desempates; máximo 20 resultados expuestos. | No acreditada. |
| Ranking por kilos | Fecha de factura; ventana explícita o `days`, calculada en America/Caracas. No reemplaza el total vendido. | No acreditada. |
| Vendedor ambiguo | Detalle y ranking SKU retornan aclaración con coincidencias; no se mezclan identidades. | No acreditada. |

Los intervalos usan inicio inclusivo y fin exclusivo. Los pedidos filtran `date_order`; facturación filtra `invoice_date`. No se deben sumar ni comparar kilos vendidos y facturados suponiendo que representan el mismo universo.

## Gráficos relacionados

Fuera del dispatcher, `sales_charts.py` reutiliza handlers para `top_salespeople`, `top_warehouses`, `top_products`, `top_customers` y `sales_by_category`. Los cuatro primeros aparecen declarados certificados en la documentación; categorías está implementado sin esa declaración. Ninguno tiene acreditación por entorno en las fuentes.

## Fuentes y límites

Inventario obtenido de `pg-ia-lab/tools-api/sales_tools.py` y los 22 módulos de negocio de `sales/`; `__init__.py` no añade una operación. El conteo de 22 corresponde a 19 operaciones del dispatcher y tres de facturación. La ruta adicional de última venta no añade un módulo.

Contexto contrastado con [PROJECT_STATE.md](PROJECT_STATE.md), [arquitectura.md](arquitectura.md), [operaciones-certificadas.md](operaciones-certificadas.md), [prod-dev.md](prod-dev.md) y [demo.md](demo.md). No se verificó la configuración activa de herramientas ni los modelos del volumen de Open WebUI; el catálogo describe código, no disponibilidad en producción.
