# UAT y certificaciones de IA Amanecer

Revisión: 2026-10-19. Base: `restructure/ia-amanecer`, commit `7654359b600e61fa9e789a38cb80a3f7db67b262`.

## Alcance de la evidencia

Las certificaciones y resultados de las secciones siguientes fueron confirmados por el responsable del proyecto en esta actualización. Se registran como CERTIFICADO DEV y CERTIFICADO PROD en el alcance indicado. Esta revisión documental no vuelve a ejecutar las pruebas. No se inventan fechas de ejecución, commits de despliegue ni límites temporales que no fueron proporcionados.

[operaciones-certificadas.md](operaciones-certificadas.md) declara operaciones ya implementadas y validadas; [demo.md](demo.md) presenta preguntas descritas como validadas y sus resultados **esperados**. Se preservan esas declaraciones históricas sin convertirlas en evidencia de ejecución reproducible ni asignarles DEV o PROD.

[PROJECT_STATE.md](PROJECT_STATE.md) refleja la evidencia disponible en la revisión anterior; las confirmaciones del responsable incorporadas aquí actualizan la certificación de las capacidades indicadas. [arquitectura.md](arquitectura.md) define al backend como fuente controlada; [prod-dev.md](prod-dev.md) exige validar y aprobar en DEV antes de promover.

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

Entorno y aprobador no identificados en esas declaraciones históricas. Las certificaciones específicas confirmadas ahora se registran a continuación; no se extienden automáticamente a todas las operaciones base.

Las siete preguntas de demo cubren última venta, su monto y gráficos de rankings (incluidos clientes por estado y zona). Las fechas y filtros de esos ejemplos no constituyen cifras observadas ni nuevas pruebas aprobadas.

## Certificaciones confirmadas por el responsable del proyecto

| Capacidad | DEV | PROD | Evidencia registrada |
| --- | --- | --- | --- |
| `sales_invoice_detail` | CERTIFICADO DEV | CERTIFICADO PROD | Detalle del 2026-10-06, clientes y referencia histórica de octubre. |
| `sales_product_ranking_by_salesperson` | CERTIFICADO DEV | CERTIFICADO PROD | Ranking de SKU del vendedor 4259, últimos 30 días de la validación. |
| `sales_salesperson_ranking_by_invoiced_kg` | CERTIFICADO DEV | CERTIFICADO PROD | Ranking de cinco vendedores por kilos facturados. |
| `sales_summary.total_kg_sold` | CERTIFICADO DEV | CERTIFICADO PROD | Pregunta, selección de herramienta y semántica de kilos vendidos. |
| Período canónico | CERTIFICADO DEV | CERTIFICADO PROD | Octubre: inicio inclusivo, fin exclusivo y presentación inclusiva. |

La resolución de vendedor ambiguo está validada dentro de las herramientas certificadas que la soportan: detalle de factura y ranking de SKU. No constituye una herramienta independiente ni certifica ese comportamiento en rutas que no lo implementan.

### sales_invoice_detail — día, vendedor y producto

Estado: **CERTIFICADO DEV / CERTIFICADO PROD**.

UAT: vendedor **4259 — DUARTE RAMIREZ JESUS ALBERTO**, Caracas, **2026-10-06**, producto **arroz**. Resultado validado: **604.8 kg facturados**.

| Factura | Cliente | SKU | Kg facturados |
| --- | --- | --- | --- |
| A65425 | Quinta Crespo | 054052 | 280.8 |
| M52743 | Catia II | 052543 | 324.0 |

### Vendedor ambiguo “Jesus Duarte”

Coincidencias validadas:

| ID | Vendedor | Sucursal | Valor de referencia |
| --- | --- | --- | --- |
| 4259 | DUARTE RAMIREZ JESUS ALBERTO | PTD03 ITC-CARACAS | 604.8 kg |
| 4832 | DUARTE VILLASMIL RONAL JESUS | PTD06 ITC-ZULIA | 22.5 kg |

Comportamiento validado: **needs_clarification**; no sumar vendedores distintos. Los valores se conservan separados por identidad.

### Referencia histórica de octubre y clientes

Vendedor **4259**, Caracas, octubre: **2082.60 kg facturados**, **13 facturas**, **4 SKU**. Es el resultado histórico validado proporcionado; no se presenta como total definitivo de un mes cerrado ni se inventa un punto de corte.

Clientes del vendedor **4259** el **6 de octubre de 2026**:

| Cliente | Kg facturados |
| --- | --- |
| Quinta Crespo | 280.8 |
| Catia II | 324.0 |

### sales_product_ranking_by_salesperson

Estado: **CERTIFICADO DEV / CERTIFICADO PROD**.

Vendedor **4259**, **últimos 30 días** de la validación. No se proporcionó la fecha de corte; se conserva el resultado histórico sin recalcular la ventana.

| Posición | SKU | Kg facturados | USD | Facturas |
| --- | --- | --- | --- | --- |
| 1 | 054052 | 2052.0 | 1639.07 | 8 |
| 2 | 052542 | 1166.4 | 919.30 | 4 |
| 3 | 052543 | 993.6 | 818.58 | 3 |
| 4 | 054051 | 9.0 | 7.62 | 5 |
| 5 | 052545 | 2.7 | 2.89 | 1 |

### sales_salesperson_ranking_by_invoiced_kg

Estado: **CERTIFICADO DEV / CERTIFICADO PROD**.

Ranking validado; el período y los filtros de esta ejecución no fueron proporcionados. No se le atribuye la ventana de 30 días del caso anterior.

| Posición | ID | Vendedor | Kg facturados | Facturas |
| --- | --- | --- | --- | --- |
| 1 | 4292 | RONDON SUAREZ YORMAN JESUS | 21902.4 | 26 |
| 2 | 4276 | RIVERO FAURE ZHAMIR ABELAR | 21288.6 | 95 |
| 3 | 4281 | MENDOZA MARCANO ADRIAN DAVID | 14277.6 | 67 |
| 4 | 5748 | AÑANGUREN TEJADA FRANCISCO JAVIER | 12011.4 | 64 |
| 5 | 4275 | GONZALEZ JURADO KAROL YUBISAY | 10089.9 | 66 |

### sales_summary.total_kg_sold

Estado: **CERTIFICADO DEV / CERTIFICADO PROD**.

Pregunta validada: **¿Cuántos kilogramos se han vendido en octubre?**

Herramienta correcta: **sales_query**, **operation=sales_summary**.

Semántica certificada: kilos vendidos desde `sale_order`, `state='sale'`; **no kilos facturados**. No se proporcionó una cifra global para esta prueba; no se reutilizan los valores de facturación como resultado.

### Período canónico

Estado: **CERTIFICADO DEV / CERTIFICADO PROD**.

Entrada validada: `date_from = 2026-10-01`, `date_to = 2026-11-01`.

Presentación correcta: **01/10/2026 al 31/10/2026**.

Regla certificada: **date_from inclusivo / date_to exclusivo**.

## PENDIENTE

**“Última factura en Caracas, vendedor y cliente”** permanece PENDIENTE. `latest_confirmed` obtiene el último pedido confirmado; no equivale a última factura y no resuelve este pendiente.

## Contratos de código y límites de alcance

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

## Seguimiento de certificaciones

Para ampliar la trazabilidad de futuras entradas se debe registrar: commit, entorno, fecha, aprobador, consulta/pregunta y parámetros exactos, SQL de referencia, punto de corte de datos, resultado SQL, respuesta API, comparación, resultado en Open WebUI DEV y evidencia de aprobación. PROD requiere evidencia de promoción y validación de esa versión.

Las certificaciones anteriores se incorporan por confirmación explícita del responsable del proyecto. No se ejecutaron consultas ni pruebas funcionales en esta revisión documental; no se asigna EN UAT a otras operaciones sin constancia de una ejecución en curso.

## Fuentes de código

Rutas relativas a `pg-ia-lab/tools-api/`: `sales_tools.py`, `sales/summaries.py`, `sales/sales_invoice_detail.py`, `sales/sales_product_ranking_by_salesperson.py` y `sales/sales_salesperson_ranking_by_invoiced_kg.py`.
