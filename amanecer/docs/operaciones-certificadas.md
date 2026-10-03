# Operaciones certificadas

## Objetivo

Documentar las operaciones de ventas que ya fueron implementadas y validadas en el backend del Asesor Gerencial IA.

Las operaciones certificadas son la base de las consultas empresariales expuestas al modelo.

## Metodología de certificación

Cada operación debe seguir:

    SQL de referencia
      ↓
    implementación
      ↓
    prueba con curl
      ↓
    comparación
      ↓
    validación en Open WebUI
      ↓
    aprobación

Para datos variables se congela un identificador, fecha o punto de corte.

Para agregados se prefieren períodos cerrados.

## Operaciones disponibles

### latest_confirmed

Obtiene la última orden de venta confirmada.

Regla principal:

    sale_order.state = 'sale'

No significa necesariamente:

- entregada;
- facturada;
- pagada.

Si el usuario pregunta por la última venta confirmada sin indicar fecha límite, se utiliza directamente la última disponible.

El parámetro `as_of` se utiliza únicamente cuando el usuario solicita explícitamente un corte temporal.

---

### sales_summary

Obtiene un resumen agregado de ventas para un período y filtros determinados.

Puede utilizar filtros empresariales como:

- sucursal;
- zona;
- estado;
- vendedor;
- período;
- red comercial.

Los resultados deben interpretarse como ventas u órdenes confirmadas, no como cobranzas ni ingresos financieros.

---

### top_products

Obtiene los productos con mayor desempeño dentro del criterio solicitado.

Permite ordenar por:

- cantidad;
- monto de ventas.

Puede combinarse con filtros como:

- sucursal;
- fecha;
- red comercial.

La cantidad puede incluir productos con diferentes unidades de medida, por lo que debe interpretarse con cuidado.

---

### top_warehouses

Obtiene las sucursales con mayor monto de ventas dentro de un período.

En ITC se utilizan las sucursales cuyo código corresponde al patrón:

    PTD%

Las sucursales Aurora pertenecen a una codificación distinta y no deben mezclarse automáticamente con ITC.

---

### top_salespeople

Obtiene los vendedores con mayor monto de ventas.

La relación técnica actual utiliza:

    sale_order.employee_fdv_id
      ↓
    hr_employee

Esta regla está técnicamente validada, aunque la definición comercial definitiva debe mantenerse documentada por separado si cambia.

---

### top_product_by_salesperson

Obtiene el producto principal vendido por un vendedor dentro de un período y conjunto de filtros.

Reutiliza las reglas certificadas de vendedores y productos.

---

### top_customers

Obtiene los clientes con mayor monto de ventas.

La identificación gerencial del cliente utiliza preferentemente:

    VAT / RIF normalizado

y utiliza `partner_id` como respaldo cuando no existe un identificador fiscal válido.

Puede utilizar filtros como:

- sucursal;
- zona;
- estado;
- período;
- red comercial.

## Gráficos certificados

El endpoint `sales_chart` reutiliza las mismas operaciones del backend.

Actualmente soporta gráficos para:

- vendedores;
- sucursales;
- productos;
- clientes.

No mantiene consultas SQL independientes para los gráficos.

## Conversión de moneda

Cuando se requiere conversión a USD, el backend utiliza la tasa disponible de Odoo conforme a las reglas implementadas.

La conversión debe tratarse como una regla del backend.

El modelo no debe calcular ni inferir tasas por su cuenta.

## Terminología

Para estas operaciones se utiliza preferentemente:

- ventas;
- monto de ventas;
- órdenes confirmadas;
- valor vendido.

No debe sustituirse automáticamente por:

- ingresos;
- facturación;
- cobranzas;
- utilidad;
- margen;
- rentabilidad.

Esos conceptos requieren consultas y reglas específicas.
