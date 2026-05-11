# Mapping de Insumos

Documentación de cada insumo utilizado como contexto para la generación de comentarios con IA.

## Formato

- **Archivos:** `` `nombre_archivo.ext` ``: descripción de uso
- **Tablas:** `catalogo.esquema.tabla`: descripción de uso

Si una tabla no es accesible o no existe, el proceso emite un warning y continúa sin ella.

### Hints opcionales

Dentro de la descripción se pueden incluir hints entre corchetes para acotar qué cargar:

- **Excel** (`.xls` / `.xlsx`): `[tabs: tab1, tab2]` — lee solo esas hojas.
- **Tablas UC**: `[columnas: col1, col2]` — selecciona solo esas columnas.

Alias aceptados (case-insensitive):

- Tabs: `tab`, `tabs`, `hoja`, `hojas`, `sheet`, `sheets`
- Columnas: `columna`, `columnas`, `column`, `columns`, `campo`, `campos`, `field`, `fields`

# Archivos

<!-- Ejemplos:
`diccionario_datos.docx`: Diccionario de datos oficial de la entidad
`reglas_negocio.md`: Reglas y lógicas de negocio por dominio
`catalogo_productos.xlsx`: Catálogo de productos vigentes. [tabs: productos, sub_productos]
-->

# Tablas

<!-- Ejemplos:
main.referencia.glosario_negocio: Glosario corporativo de términos. [columnas: termino, definicion, dominio]
main.referencia.taxonomia_productos: Taxonomía completa de productos y sub-productos
-->
