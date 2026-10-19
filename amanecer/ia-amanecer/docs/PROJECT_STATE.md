# Estado del proyecto IA Amanecer

Fecha de revisión: 2026-10-19.
Repositorio: `osolight/ia`.
Rama base: `restructure/ia-amanecer`.
Commit revisado para esta actualización documental: `a8b563637d9f0f61a80b689958290aac5c7e2e9b`.
Observaciones técnicas originales basadas en `60f155ea75b39a7707d61df110e8c96f16527218`; se conservan salvo las actualizaciones de certificación indicadas.

## Alcance y conclusión

IA Amanecer contiene un backend analítico FastAPI para Odoo, integración con Open WebUI y configuración de despliegue DEV. Hay lógica empresarial implementada para ventas, facturación, gráficos, exportaciones y auditoría. El repositorio conserva también herramientas genéricas de SQL y componentes históricos.

Este estado incorpora las certificaciones DEV/PROD y los resultados confirmados por el responsable del proyecto en [UAT_CERTIFIED.md](UAT_CERTIFIED.md), contrastados con [TOOL_CATALOG.md](TOOL_CATALOG.md) y [CHANGELOG_AI.md](CHANGELOG_AI.md). Esta actualización documental no vuelve a ejecutar pruebas ni verifica servicios, credenciales o datos de Odoo en vivo. Las certificaciones se limitan a las capacidades y casos documentados; código presente no implica certificación del resto.

## Estructura real

Todas las rutas siguientes son relativas a `amanecer/ia-amanecer/`.

| Ruta | Contenido observado |
| --- | --- |
| `README.md` | Objetivo, arquitectura y flujo de promoción. |
| `docs/` | Arquitectura, operaciones certificadas, UAT DEV/PROD, catálogo de herramientas, changelog, demos y separación PROD/DEV. |
| `pg-ia-lab/tools-api/main.py` | Aplicación, pool PostgreSQL, SQL genérico, exportaciones y auditoría. |
| `pg-ia-lab/tools-api/sales_tools.py` | Contratos HTTP y despacho de operaciones comerciales. |
| `pg-ia-lab/tools-api/sales/` | 22 módulos de negocio y un `__init__.py`. |
| `pg-ia-lab/tools-api/sales_charts.py` | Gráficos HTML que reutilizan handlers comerciales. |
| `pg-ia-lab/tests/` | Dos programas de prueba de modelos con servicios reales. |
| `pg-ia-lab/versions/` | Backend anterior y Pipe de análisis Odoo; no son el entrypoint del contenedor actual. |
| `pg-ia-lab/openwebui-tool.py` | Herramienta genérica de esquema, SQL, muestras y estadísticas. |
| `pg-ia-lab/pg-ai-explorer.jsx` | Explorador React con Recharts, llamadas a PostgreSQL vía API y a Anthropic. |
| `open-webui/` | Dockerfile y Compose DEV; `patches/` contiene solamente un README. |
| `deploy/nginx/` | Proxies PROD/DEV y CSS de personalización. |
| `deploy/dev/`, `deploy/prod/` | README descriptivo por entorno, sin Compose propio. |
| `scripts/` | README de utilidades previstas; no hay scripts operativos implementados allí. |

No hay `AGENTS.md`, workflows de CI ni manifiesto de construcción del explorador React en el árbol revisado. El README raíz del repositorio aún referencia las rutas anteriores a la reestructuración.

## Arquitectura implementada

El flujo documentado es usuario → Open WebUI → Asesor Gerencial IA → Ollama → herramientas FastAPI → réplica PostgreSQL/Odoo. La lógica comercial reside en los handlers del backend y utiliza el pool de `main.py`.

El backend usa Python 3.12, FastAPI 0.115.5, Uvicorn 0.32.1, asyncpg 0.30.0 y Pydantic 2.10.3. El Dockerfile arranca `uvicorn main:app` en el puerto 8100 y copia los módulos de `sales/`.

La arquitectura objetivo privilegia herramientas de negocio controladas, pero el código también expone `POST /query`, `/schemas`, `/schema`, `/sample/{table_name}` y `/stats/{table_name}`. La herramienta genérica de Open WebUI permite enviar SQL; el explorador JSX genera consultas mediante Anthropic. Estos componentes requieren distinguirse de la experiencia del Asesor Gerencial. No hay evidencia versionada suficiente para afirmar cuáles están habilitados a usuarios finales.

## Capacidades comerciales presentes

### Ventas mediante GET /sales/query

El conjunto aceptado por `supported_operations` incluye 19 operaciones:

- Base y rankings: `latest_confirmed`, `sales_summary`, `top_products`, `top_warehouses`, `top_salespeople`, `top_product_by_salesperson`, `top_customers`.
- Comparación y ticket: `sales_period_comparison`, `average_ticket`.
- Segmentación: `sales_by_zone`, `sales_by_state`, `sales_by_channel`, `sales_by_customer`, `sales_by_salesperson`, `sales_by_product`, `sales_by_brand`, `sales_by_category`.
- Clientes: `purchase_frequency`, `new_vs_recurring_customers`.

Los filtros y reglas varían por operación; no todos los parámetros aplican a todos los handlers. Se utilizan períodos con inicio inclusivo y fin exclusivo. La conversión de fechas ISO con zona horaria se normaliza a UTC antes de consultar timestamps de Odoo.

Las reglas incluyen órdenes confirmadas, filtros de sucursal, clasificación comercial y conversión monetaria. La red ITC se identifica mediante códigos de almacén `PTD%` en las operaciones correspondientes. Los rankings tradicionales y las operaciones generales tienen alcances distintos; no deben asumirse intercambiables. Los identificadores fiscales normalizados y el respaldo por partner forman parte de las reglas de clientes.

### Facturación mediante rutas específicas

- `GET /sales/invoice-detail`: detalle por período, sucursal, vendedor, producto, SKU y cliente; limita el detalle a 20 líneas y mantiene un resumen del conjunto consultado.
- `GET /sales/product-ranking-by-salesperson`: ranking de SKU facturados por vendedor y monto equivalente en USD.
- `GET /sales/salesperson-ranking-by-invoiced-kg`: ranking de vendedores por kilos facturados, con período explícito o ventana de días.

Estas consultas utilizan facturas de cliente publicadas (`account_move.state = 'posted'`, `move_type = 'out_invoice'`) y relaciones con pedidos. El detalle y el ranking de SKU incorporan resolución de vendedores ambiguos. El comportamiento está validado en las herramientas que lo soportan: `needs_clarification`, sin sumar identidades distintas. Las consultas incluyen reglas de kilos según unidades de medida.

`sales_invoice_detail` aparece en textos del contrato de `/sales/query`, pero no está en su conjunto de operaciones aceptadas ni tiene despacho allí. La ruta específica es la implementación disponible. Ventas confirmadas, facturación y cobranzas deben interpretarse por separado.

### Gráficos, exportaciones y auditoría

`sales_charts.py` implementa gráficos para vendedores, sucursales, productos, clientes y categorías reutilizando handlers de negocio. La documentación de gráficos certificados enumera solamente los cuatro primeros.

`main.py` ofrece CSV de contactos creados y clientes con deuda vencida, emisión de tokens temporales de descarga, consulta de tokens activos y auditoría. Los tokens viven en memoria, caducan y se pierden al reiniciar. El consumo marca `used`, pero no rechaza reutilización antes de expirar.

La auditoría JSONL registra identificadores de ejecución, fecha UTC y hash SHA-256, además de SQL y datos según la operación. Las exportaciones guardan hasta 20 filas de muestra. Los endpoints de auditoría permiten consultar registros compactos.

## Entornos y despliegue

| Componente | DEV versionado | PROD documentado |
| --- | --- | --- |
| Open WebUI | Compose `open-webui-dev`, puerto 3001, imagen basada en v0.11.4. | Puerto 3000. |
| pg-tools | Compose `pg-ia-dev`, contenedor `pg-tools-dev`, publicación 8101 → 8100. | Puerto 8100. |
| Proxy | `asesor-dev.grupo-amanecer.com` → Open WebUI 3001. | `asesor.grupo-amanecer.com` → Open WebUI 3000, inyección de CSS. |
| Ollama | Acceso al host mediante `host.docker.internal:11434`. | Servicio compartido según `docs/prod-dev.md`. |

Open WebUI necesita un volumen externo ya existente. PostgreSQL y Ollama no se aprovisionan en los Compose presentes. El backend requiere variables de conexión y configuración externas. Los proxies versionados escuchan HTTP en el puerto 80; no documentan terminación TLS en esos archivos.

El Dockerfile de Open WebUI modifica el nombre mostrado por la versión base. No instala automáticamente el Asesor, sus prompts, permisos ni herramientas. La configuración efectiva residente en el volumen de Open WebUI no está incluida.

Los Compose son de DEV; los README describen PROD y las rutas `/opt/ia-amanecer`, pero no permiten reconstruir por sí solos el despliegue productivo completo. El comentario de acceso del Compose backend todavía menciona localhost:8100 aunque publica 8101; su Dockerfile declara EXPOSE 8000 aunque Uvicorn usa 8100.

## Controles y límites observados

- La API valida la clave configurada mediante `PG_TOOLS_API_KEY` y `API_KEY_HEADER`. Si la clave está vacía, `require_api_key` permite acceso.
- La ruta `/sales/invoice-detail` no llama a `require_api_key`; su handler tampoco recibe ese control. Las otras dos rutas específicas de facturación sí lo invocan. No se observa una dependencia global que compense esa diferencia.
- `/query` restringe tipos de sentencia, bloquea patrones SQL y ejecuta una transacción de solo lectura. El límite se agrega a SELECT sin LIMIT; no constituye un límite uniforme para todas las consultas aceptadas.
- El acceso de solo lectura de la réplica es un requisito documentado. Los privilegios reales del rol PostgreSQL no se pueden demostrar con estos archivos; los valores por defecto de conexión usan el usuario `postgres`.
- CORS permite todos los orígenes por defecto. Las descargas se autorizan por token; no requieren la cabecera API key.
- Ocultar controles mediante CSS es personalización visual; los permisos efectivos de usuarios y herramientas requieren verificar Open WebUI.
- Los archivos de secretos, bases locales, datos y auditorías están excluidos mediante gitignore. No se inspeccionaron secretos ni registros de usuarios.

## Validación y certificación

[operaciones-certificadas.md](operaciones-certificadas.md) conserva la declaración histórica de las siete operaciones base y gráficos de vendedores, sucursales, productos y clientes. [UAT_CERTIFIED.md](UAT_CERTIFIED.md) y [TOOL_CATALOG.md](TOOL_CATALOG.md) actualizan el alcance certificado confirmado por el responsable del proyecto; [CHANGELOG_AI.md](CHANGELOG_AI.md) recoge estos hitos.

| Capacidad | Estado actual | Alcance validado documentado |
| --- | --- | --- |
| `sales_invoice_detail` | CERTIFICADO DEV / CERTIFICADO PROD | Vendedor 4259, Caracas, arroz, 2026-10-06: 604.8 kg facturados; clientes Quinta Crespo y Catia II. |
| `sales_product_ranking_by_salesperson` | CERTIFICADO DEV / CERTIFICADO PROD | Ranking de SKU del vendedor 4259, últimos 30 días de la validación. |
| `sales_salesperson_ranking_by_invoiced_kg` | CERTIFICADO DEV / CERTIFICADO PROD | Ranking de vendedores por kilos facturados en Caracas, últimos 30 días de la validación. |
| `sales_summary.total_kg_sold` | CERTIFICADO DEV / CERTIFICADO PROD | Prueba final de octubre en PROD: 9,234,140.3826 kg vendidos. |
| Período canónico | CERTIFICADO DEV / CERTIFICADO PROD | Octubre: inicio inclusivo, fin exclusivo y presentación inclusiva. |

Para **¿Cuántos kilogramos se han vendido en octubre?**, la herramienta validada es `sales_query`, `operation=sales_summary`. El resultado PROD de **9,234,140.3826 kg vendidos** corresponde al punto de corte de la validación y puede cambiar si cambia la base de datos. La semántica es `sale_order`, `state='sale'`: kilos vendidos, no kilos facturados.

Período canónico certificado:

- `date_from = 2026-10-01`
- `date_to_exclusive = 2026-11-01`
- `date_to_inclusive = 2026-10-31`
- `period_text = 01/10/2026 al 31/10/2026`

La resolución de vendedor ambiguo está validada dentro de `sales_invoice_detail` y `sales_product_ranking_by_salesperson`: ante varias identidades compatibles se devuelve `needs_clarification` y no se suman vendedores distintos. No se extiende esa certificación a herramientas que no soportan la resolución.

Las demás operaciones nuevas sin UAT certificado permanecen **IMPLEMENTADO**: `sales_period_comparison`, `average_ticket`, `sales_by_zone`, `sales_by_state`, `sales_by_channel`, `sales_by_customer`, `sales_by_salesperson`, `sales_by_product`, `sales_by_brand`, `sales_by_category`, `purchase_frequency` y `new_vs_recurring_customers`. Las certificaciones anteriores no las elevan de estado.

Los programas `tests/test_model_sales.py` y `tests/test_model_sales_multi.py` prueban decisiones y respuestas de modelos mediante Ollama y el backend. Requieren servicios y API key; sus valores por defecto apuntan a pg-tools en 8100, no al puerto DEV 8101. No constituyen una suite aislada de pruebas unitarias con aserciones.

En esta revisión documental no se ejecutaron pruebas funcionales ni consultas contra Odoo, ni se arrancaron contenedores o modelos. Se registran las certificaciones DEV/PROD ya confirmadas en UAT_CERTIFIED.md; la disponibilidad en vivo de los servicios no se volvió a comprobar.

## Pendientes derivados del estado observado

1. Validar en DEV las operaciones nuevas que permanecen IMPLEMENTADO y no tienen UAT certificado, con SQL de referencia y casos reproducibles; registrar resultados, modelo utilizado y aprobación.
2. Revisar la autenticación de invoice-detail y comprobar claves, permisos PostgreSQL, CORS y exposición real de herramientas SQL.
3. Conciliar los contratos de facturación con el dispatcher y mantener actualizado el inventario de operaciones y gráficos certificados.
4. Versionar la configuración reproducible del Asesor y los procedimientos de despliegue, regresión y promoción, sin incluir secretos ni datos privados.
5. Actualizar referencias de rutas, puertos y componentes históricos; definir el uso previsto del explorador y de las herramientas genéricas.
6. **PENDIENTE: “Última factura en Caracas, vendedor y cliente”.** `latest_confirmed` obtiene el último pedido confirmado; no equivale a última factura ni resuelve este pendiente.

El flujo de promoción documentado sigue siendo SQL de referencia → backend → curl → comparación → Open WebUI DEV → aprobación → PROD. Este documento refleja las certificaciones confirmadas y conserva los pendientes técnicos; esta actualización no modifica código ni realiza una nueva promoción.

## Fuentes revisadas

- Para esta actualización: [UAT_CERTIFIED.md](UAT_CERTIFIED.md), [TOOL_CATALOG.md](TOOL_CATALOG.md) y [CHANGELOG_AI.md](CHANGELOG_AI.md), presentes en el commit revisado.
- Fuentes de las observaciones técnicas conservadas: `README.md` raíz y del proyecto; `docs/arquitectura.md`, `docs/prod-dev.md`, `docs/operaciones-certificadas.md`, `docs/demo.md`.
- `pg-ia-lab/tools-api/main.py`, `sales_tools.py`, `sales_charts.py` y módulos de `sales/`.
- Dockerfiles, requisitos y Compose del backend y Open WebUI; configuración Nginx y README de deploy.
- Herramienta genérica Open WebUI, explorador JSX, versiones históricas, programas de prueba y archivos gitignore.
