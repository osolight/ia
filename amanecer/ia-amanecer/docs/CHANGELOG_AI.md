# Cambios importantes de IA Amanecer

Revisión: 2026-10-19. Historial limitado a los commits alcanzables desde `restructure/ia-amanecer` en `7654359b600e61fa9e789a38cb80a3f7db67b262`.

Las fechas corresponden a incorporación en GitHub, en America/Caracas; no prueban cuándo se desarrolló, ejecutó o aprobó una capacidad en servidores. No se asignan versiones de producto ni promociones no documentadas.

## 2026-10-19 — Registro del estado observado

Evidencia: commit [c9ea547](https://github.com/osolight/ia/commit/c9ea5473719de6d3b0710bf93238b8c7447caba0), incorporado a la rama base mediante PR #1, commit [7654359](https://github.com/osolight/ia/commit/7654359b600e61fa9e789a38cb80a3f7db67b262).

- Añadido [PROJECT_STATE.md](PROJECT_STATE.md): inventario de estructura, capacidades implementadas, despliegue, diferencias de contratos y pendientes técnicos.
- Documentada la distinción entre código presente, certificación declarada y disponibilidad efectiva en PROD.
- El cambio de estado fue documental; no acredita pruebas funcionales ni modifica código.

## 2026-10-19 — Reestructuración GitHub e incorporación del estado DEV

Evidencia: commit [60f155e](https://github.com/osolight/ia/commit/60f155ea75b39a7707d61df110e8c96f16527218), comparado con su padre `43b9ba7`.

### Organización y despliegue

- Consolidado el proyecto bajo `amanecer/ia-amanecer/`: backend, Open WebUI, documentación, despliegue y scripts.
- Movida la configuración Nginx a `deploy/nginx/` y los programas de prueba a `pg-ia-lab/tests/`.
- Incorporados README para DEV/PROD y directorios de patches y scripts. Estos últimos describen trabajo previsto; no equivalen a automatización operativa implementada.
- Los Compose versionados pasan a identificar DEV: backend en 8101 y Open WebUI en 3001, con contenedores y volumen de DEV.

### Modularización y ampliación comercial

- Ampliado el despacho de `sales_tools.py` a 19 operaciones de consulta, con handlers separados en `sales/`.
- Añadidos módulos de comparación de períodos, ticket promedio, zonas, estados, canales, clientes, vendedores, productos, marcas, categorías, frecuencia de compra y clientes nuevos/recurrentes.
- La separación en módulos ya existía para las operaciones base antes de esta incorporación; este commit la amplía, no inicia toda la modularización.
- Añadido gráfico de categorías reutilizando el handler comercial. La declaración previa de gráficos certificados seguía enumerando cuatro tipos.

### Herramientas de facturación

- Incorporado `sales_invoice_detail`: consulta de facturas publicadas con filtros de vendedor, producto, SKU, cliente y sucursal; detalle limitado y resumen completo.
- Incorporado `sales_product_ranking_by_salesperson`: ranking de SKU facturados por vendedor y monto equivalente USD.
- Incorporado `sales_salesperson_ranking_by_invoiced_kg`: ranking de vendedores/sucursales por kilos facturados, con fechas explícitas o ventana calculada mediante `days` en America/Caracas.
- El detalle y el ranking de SKU contienen resolución de vendedor ambiguo: solicitan aclaración en lugar de mezclar identidades.
- Las tres herramientas tienen rutas específicas y consultan `account_move/account_move_line` con facturas `posted/out_invoice`; no se confunden con pedidos confirmados ni cobranzas.

### total_kg_sold y período canónico

- Ampliado `sales/summaries.py` con `total_kg_sold`, calculado desde líneas de pedidos confirmados con los mismos filtros de estado, período y almacén del resumen.
- Incorporada conversión a KG de cantidades secundarias o principales de peso, usando la referencia de unidad id 12; las demás categorías aportan cero.
- Incorporados valor y texto de kilos en el resumen gerencial, respuesta y auditoría.
- Incorporado el objeto canónico `executive_summary.period`: `date_from`, `date_to_exclusive`, `date_to_inclusive` y `period_text`.
- El filtro conserva el intervalo [inicio, fin); el final inclusivo es una representación para lectura humana, derivada de restar un microsegundo al límite exclusivo.

Estos cambios están presentes en el código incorporado. Los comentarios de certificación no aportan por sí solos resultados UAT ni aprobación DEV/PROD. El historial disponible los agrupa en un commit; no permite reconstruir una fecha independiente de desarrollo o certificación para cada uno.

## 2026-10-03 — Arquitectura y operaciones base documentadas

Evidencia: commit [43b9ba7](https://github.com/osolight/ia/commit/43b9ba7859c0eb0f00cbe2b6297178efa7271a2c).

- Documentados flujo Open WebUI → Ollama → herramientas controladas → réplica Odoo, separación PROD/DEV y metodología de validación.
- Declaradas certificadas siete operaciones base: última venta, resumen, rankings de productos, sucursales, vendedores, producto por vendedor y clientes.
- Documentados gráficos de vendedores, sucursales, productos y clientes, y preguntas de demostración.
- La documentación declara validación, pero no contiene resultados numéricos reproducibles ni atribuye aprobaciones individuales a DEV o PROD.

## 2026-10-03 — Incorporación inicial del monorepositorio

Evidencia: commit [8d19d8b](https://github.com/osolight/ia/commit/8d19d8bba63362e67a6770892de72b71c52b7479), “initialize Osolight AI monorepo with Amanecer advisor”.

- Incorporado el proyecto Amanecer al repositorio Osolight IA.
- Este hito registra la incorporación inicial a GitHub; no establece la fecha original de desarrollo ni de puesta en producción.

## Documentación de seguimiento

[TOOL_CATALOG.md](TOOL_CATALOG.md) inventaría las operaciones y separa IMPLEMENTADO, EN UAT, CERTIFICADO DEV y CERTIFICADO PROD. [UAT_CERTIFIED.md](UAT_CERTIFIED.md) conserva las declaraciones respaldadas por las fuentes y explicita la falta de actas para las ampliaciones.

Fuentes funcionales: [PROJECT_STATE.md](PROJECT_STATE.md), [arquitectura.md](arquitectura.md), [operaciones-certificadas.md](operaciones-certificadas.md), [prod-dev.md](prod-dev.md), [demo.md](demo.md), `sales_tools.py` y módulos de `sales/`.
