# UAT y certificaciones de IA Amanecer

Revisión: 2026-10-19. Base: `restructure/ia-amanecer`, commit `7654359b600e61fa9e789a38cb80a3f7db67b262`.

## Alcance de la evidencia

No hay actas de ejecución UAT con SQL de referencia, salida del backend, comparación y aprobación por entorno en las fuentes revisadas. Este registro no acredita nuevas pruebas ni contiene resultados numéricos inventados.

[operaciones-certificadas.md](operaciones-certificadas.md) declara operaciones ya implementadas y validadas; [demo.md](demo.md) presenta preguntas descritas como validadas y sus resultados **esperados**. Se preservan esas declaraciones históricas sin convertirlas en evidencia de ejecución reproducible ni asignarles DEV o PROD.

[PROJECT_STATE.md](PROJECT_STATE.md) distingue implementación de certificación y señala la falta de evidencias para las ampliaciones. [arquitectura.md](arquitectura.md) define al backend como fuente controlada; [prod-dev.md](prod-dev.md) exige validar y aprobar en DEV antes de promover.

## Certificación declarada en las fuentes

| Capacidad | Alcance declarado | Resultado de ejecución disponible |
| --- | --- | --- |
| `latest_confirmed` | Última orden con `sale_order.state = 'sale'`; `as_of` solamente para corte solicitado. | Sin salida, identificador congelado ni acta por entorno. |
| `sales_summary` | Agregado de ventas confirmadas por período. | Sin valores comparados ni acta; la declaración no identifica `total_kg_sold`. |
| `top_products` | Ranking por cantidad o monto; precaución con unidades heterogéneas. | Sin salida comparada. |
| `top_warehouses` | Ranking de sucursales ITC, códigos `PTD%`. | Sin salida comparada. |
| `top_salespeople` | Ranking por monto; relación `employee_fdv_id → hr_employee` descrita como técnicamente validada. | Sin acta de ejecución. |
| `top_product_by_salesperson` | Producto principal por vendedor. | Sin salida comparada. |
| `top_customers` | Ranking; VAT/RIF normalizado con respaldo por partner. | Sin salida comparada. |
| Gráficos de vendedores, sucursales, productos y clientes | Reutilizan operaciones comerciales, según el documento de certificación. | Sin captura, payload ni comparación archivada. |

Entorno y aprobador no identificados en esas declaraciones. No se registra ninguna de ellas como CERTIFICADO DEV o CERTIFICADO PROD.

Las siete preguntas de demo cubren última venta, su monto y gráficos de rankings (incluidos clientes por estado y zona). Las fechas y filtros de esos ejemplos no constituyen cifras observadas ni nuevas pruebas aprobadas.

## Capacidades solicitadas sin acta de certificación

Esta sección delimita contratos observados en el código; no registra casos como ejecutados o aprobados.

| Capacidad | Comportamiento implementado | Resultado certificado |
| --- | --- | --- |
| `sales_invoice_detail` | `GET /sales/invoice-detail`; facturas publicadas, filtros de sucursal, vendedor, producto, SKU y cliente; detalle máximo de 20 líneas, resumen de todas las filas consultadas. | No acreditado en las fuentes. |
| `sales_product_ranking_by_salesperson` | `GET /sales/product-ranking-by-salesperson`; SKU facturados por vendedor, ordenados por monto equivalente USD, después kilos y desempates. | No acreditado en las fuentes. |
| `sales_salesperson_ranking_by_invoiced_kg` | `GET /sales/salesperson-ranking-by-invoiced-kg`; vendedores y sucursales por kilos facturados; admite período explícito o `days`. | No acreditado en las fuentes. |
| `sales_summary.total_kg_sold` | Total global de kilos de líneas de pedidos confirmados; comparte filtros de estado, período y almacén con el resumen monetario. | No hay comparación SQL/API ni aprobación específica. |
| Período canónico | `sales_summary` genera `period` dentro de `executive_summary`, con fechas exclusiva e inclusiva y texto de presentación. | Sin acta que certifique límites ni presentación. |
| Vendedor ambiguo | Detalle de factura y ranking de SKU retornan solicitud de aclaración ante varias identidades compatibles. | Sin respuesta ejecutada ni aprobación registrada. |

Los comentarios que califican lógica o kilos como “certificados” son declaraciones del código; no sustituyen el registro de una prueba con resultado y entorno.

### Ventas y facturación

- **Ventas:** `sales_summary` consulta `sale_order`, estado `sale`, por `date_order`. `total_kg_sold` usa líneas de pedido; prioriza cantidad secundaria de peso y convierte a la referencia KG de id 12; como respaldo usa cantidad principal de peso. Las categorías ajenas al peso aportan cero.
- **Facturación:** las tres rutas específicas consultan `account_move/account_move_line`, facturas de cliente `posted` y `out_invoice`, por `invoice_date`. Aplican relaciones con pedidos y exclusión de clientes internos según sus consultas. Su métrica es `total_kg_invoiced`.
- Kilos vendidos y kilos facturados tienen fuentes distintas. El ranking de vendedores no debe utilizarse para obtener el total global vendido. Ninguna de estas métricas demuestra cobranza o pago.

### Períodos inclusivo/exclusivo y período canónico

El predicado observado es `fecha >= date_from AND fecha < date_to`: intervalo **[inicio, fin)**. Ventas filtra timestamps de pedido; facturación compara fechas de factura con parámetros convertidos a date.

En el resumen, `period.date_from` es la fecha inicial, `date_to_exclusive` la fecha del límite recibido, y `date_to_inclusive` la fecha de `date_to - 1 microsegundo`; `period_text` muestra el rango inclusivo. Es presentación canónica del resumen, no un nuevo filtro. Pierde el detalle horario al presentar fechas.

Ejemplo de contrato, no prueba ejecutada: consultar un día completo con inicio `2026-09-29T00:00:00` y fin `2026-09-30T00:00:00` excluye el día 30; el texto inclusivo termina el 29. Los ISO con zona se normalizan a UTC en el parser compartido. No debe inferirse un día local a partir de un timestamp sin confirmar la zona del contrato.

En el ranking por kilos, `days` calcula la ventana con fecha de negocio America/Caracas, incluye el día actual y utiliza mañana como fin exclusivo. Si se recibe `days`, esa rama reemplaza el período explícito. No hay resultado UAT de esa precedencia.

### Vendedor ambiguo

En `sales_invoice_detail.py` y `sales_product_ranking_by_salesperson.py`, la búsqueda compara palabras del nombre sin acentos y usa la sucursal suministrada para desambiguar. Si persiste más de una coincidencia, retorna `found: false`, `needs_clarification: true`, `reason: ambiguous_salesperson` y `matches`, y registra auditoría de aclaración. No mezcla resultados para resolver la ambigüedad.

No se extiende esa garantía al ranking por kilos: esa ruta obtiene un ranking, no resuelve un vendedor indicado por nombre.

## Cómo incorporar una certificación futura

Una entrada aprobada necesita: commit, entorno, fecha, aprobador, consulta/pregunta y parámetros exactos, SQL de referencia, punto de corte de datos, resultado SQL, respuesta API, comparación, resultado en Open WebUI DEV y evidencia de aprobación. PROD requiere evidencia de promoción y validación de esa versión.

Actualmente no hay entradas con ese conjunto de evidencia. No se ejecutaron consultas ni pruebas funcionales en esta revisión; no se asigna EN UAT sin constancia de una ejecución en curso.

## Fuentes de código

Rutas relativas a `pg-ia-lab/tools-api/`: `sales_tools.py`, `sales/summaries.py`, `sales/sales_invoice_detail.py`, `sales/sales_product_ranking_by_salesperson.py` y `sales/sales_salesperson_ranking_by_invoiced_kg.py`.
