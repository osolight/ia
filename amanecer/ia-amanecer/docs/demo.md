# Demo — Asesor Gerencial IA

## Objetivo

Disponer de un conjunto corto de preguntas ya validadas para demostrar el funcionamiento del Asesor Gerencial IA sin improvisar durante una presentación.

## Recomendaciones

- iniciar cada demostración en un chat nuevo;
- utilizar fechas cerradas cuando se trate de agregados;
- no mezclar en una misma pregunta conceptos de ventas, facturación, pagos o cobranzas;
- validar que la herramienta usada corresponda al tipo de consulta;
- evitar preguntas ambiguas que requieran reglas de negocio todavía no certificadas.

## Preguntas sugeridas

### 1. Última venta confirmada

    ¿En qué sucursal se realizó la última venta confirmada?

Resultado esperado:

- uso de la operación de última orden confirmada;
- respuesta sin pedir período;
- identificación de la sucursal correspondiente.

### 2. Monto de la última venta

    ¿Cuál fue el monto de la última venta confirmada?

Resultado esperado:

- consulta directa de la última orden confirmada;
- respuesta factual;
- sin inferencias adicionales.

### 3. Top vendedores

    Muéstrame gráficamente los 5 vendedores con mayor monto de ventas en Caracas el 29 de septiembre de 2026.

Resultado esperado:

- gráfico horizontal;
- cinco vendedores;
- orden descendente por monto de ventas;
- filtro de sucursal Caracas;
- período cerrado del 29 de septiembre de 2026.

### 4. Top sucursales

    Muéstrame gráficamente las 10 sucursales con mayor monto de ventas el 29 de septiembre de 2026.

Resultado esperado:

- gráfico de sucursales;
- orden descendente;
- red comercial correspondiente;
- período cerrado.

### 5. Top productos

    Muéstrame gráficamente los 5 productos con mayor monto de ventas en Caracas el 29 de septiembre de 2026.

Resultado esperado:

- gráfico de productos;
- filtro de Caracas;
- orden por monto de ventas;
- cinco resultados.

### 6. Top clientes por estado

    Muéstrame gráficamente los 10 clientes con mayor monto de ventas en el estado Miranda el 29 de septiembre de 2026.

Resultado esperado:

- gráfico de clientes;
- filtro por estado Miranda;
- diez resultados;
- orden descendente por monto.

### 7. Top clientes por zona

    Muéstrame gráficamente los 10 clientes con mayor monto de ventas en la zona BARINAS-EL CARMEN el 29 de septiembre de 2026.

Resultado esperado:

- gráfico de clientes;
- filtro por zona;
- orden descendente;
- resultados consistentes con la consulta certificada.

## Validación durante la demo

Para cada pregunta se debe comprobar:

1. que la herramienta correcta haya sido utilizada;
2. que no se inventen datos fuera de la respuesta del backend;
3. que no se utilicen términos como facturación, ingresos, cobranzas o utilidad cuando la consulta corresponde solamente a ventas;
4. que los gráficos representen exactamente los datos devueltos por las operaciones certificadas.

## Criterio de éxito

La demostración se considera correcta cuando el usuario puede hacer preguntas naturales y obtener respuestas y gráficos coherentes con los datos certificados del backend, sin exposición de SQL, parámetros internos ni detalles técnicos innecesarios.
