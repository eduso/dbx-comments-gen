# dbx-comments-gen

Generador automático de comentarios de negocio para esquemas, tablas y columnas de Unity Catalog usando modelos fundacionales de IA.

## Descripción

Este proyecto automatiza la documentación de activos de datos en Databricks. Utiliza documentos proporcionados por el usuario como contexto para que un modelo de IA genere comentarios descriptivos orientados a usuarios de negocio.

**Capacidades principales:**
- Recorre un catálogo completo o un esquema específico
- Genera comentarios para esquemas, tablas y columnas
- Usa documentos de contexto priorizados dinámicamente por relevancia
- Opcionalmente, muestrea datos reales de cada tabla para enriquecer el contexto
- Persiste resultados y trazabilidad completa de cada ejecución
- Dashboard interactivo para navegar los resultados

## Estructura del Proyecto

```
dbx-comments-gen/
├── databricks.yml                # Databricks Asset Bundle (DAB) para despliegue
├── input/                        # Insumos del usuario (documentos de contexto)
│   └── mapping.md                # Mapeo de archivos y su propósito
└── src/
    ├── notebooks/
    │   ├── comments_generator_v4.py                              # Notebook principal
    │   ├── setup_schema.py                                       # Setup de esquema y tablas de control
    │   └── 01_dashboard_generacion_de_comentarios.lvdash.json    # Dashboard Lakeview
    └── pipelines/
        └── 01_repositorio_resultados.sql                         # DDL de tablas de control (referencia)
```

## Parámetros

| Parámetro | Descripción | Default |
|-----------|-------------|---------|
| `catalog_name` | Catálogo de Unity Catalog a procesar | `mi_catalogo` |
| `schema_name` | Esquema específico. Vacío = todo el catálogo | _(vacío)_ |
| `model_endpoint` | Endpoint del modelo fundacional | `databricks-claude-sonnet-4-5` |
| `results_catalog` | Catálogo donde se guardan los resultados | `mi_catalogo_de_resultados` |
| `results_schema` | Esquema donde se guardan los resultados | `mi_esquema_de_resultados` |
| `enable_sampling` | Habilitar muestreo de datos reales (`yes`/`no`) | `no` |
| `sampling_pct` | Porcentaje de muestreo para tablas >500 registros (1-100) | `10` |

## Insumos

Los documentos de contexto se colocan en la carpeta `input/` y se registran en `input/mapping.md` con el formato:

```
`nombre_archivo.ext`: Descripción de para qué sirve este archivo
```

**Formatos soportados:** `.docx`, `.tsv`, `.csv`, `.xlsx`, `.txt`, `.md`, `.json`, `.yaml`

El proceso prioriza dinámicamente los insumos más relevantes para cada esquema/tabla, respetando el límite de contexto del modelo.

## Despliegue con DAB

### Prerrequisitos

- Databricks CLI v0.200+ con autenticación configurada
- Acceso al workspace destino

### Comandos

```bash
# Validar el bundle
databricks bundle validate --target dev --profile <profile>

# Desplegar al workspace
databricks bundle deploy --target dev --profile <profile>

# Ejecutar el job con parámetros por defecto
databricks bundle run comments_generator --target dev --profile <profile>

# Ejecutar con parámetros personalizados
databricks bundle run comments_generator --target dev --profile <profile> \
  --params catalog_name=mi_catalogo,schema_name=mi_esquema,enable_sampling=yes,sampling_pct=15
```

### Targets disponibles

| Target | Modo | Descripción |
|--------|------|-------------|
| `dev` | development | Desarrollo y pruebas (default) |
| `prod` | production | Producción con catálogos definitivos |

## Dashboard

El dashboard interactivo (`01_dashboard_generacion_de_comentarios.lvdash.json`) se despliega automáticamente como recurso del DAB y permite navegar los resultados con filtros cascading:

1. **Fecha de ejecución** — Filtra todo el dashboard
2. **Esquema** (opcional) — Se filtra según la fecha seleccionada
3. **Tabla** (opcional, multi-select) — Se filtra según fecha y esquema

**Widgets incluidos:**
- 4 KPI counters (ejecuciones, comentarios, esquemas, tablas)
- Historial de ejecuciones con estado y conteo
- Detalle de comentarios generados por columna

## Tablas de Resultados

Las tablas se crean automáticamente en el esquema configurado:

### `ejecuciones`
| Columna | Tipo | Descripción |
|---------|------|-------------|
| `id_ejecucion` | STRING (PK) | GUID único de la ejecución |
| `fecha_ejecucion` | TIMESTAMP | Fecha/hora UTC |
| `estado` | VARCHAR(50) | INICIADO, EN_PROCESO, COMPLETADO, COMPLETADO_CON_ERRORES, ERROR |
| `resultado` | VARCHAR(4000) | Detalle del resultado |

### `resultados`
| Columna | Tipo | Descripción |
|---------|------|-------------|
| `id_resultado` | BIGINT (PK, identity) | ID autoincrementable |
| `id_ejecucion` | STRING (FK) | Referencia a la ejecución |
| `fecha_resultado` | TIMESTAMP | Fecha/hora UTC de generación |
| `nombre_esquema` | VARCHAR(255) | Esquema procesado |
| `nombre_tabla` | VARCHAR(255) | Tabla procesada (`__esquema__` para esquemas) |
| `nombre_columna` | VARCHAR(255) | Columna procesada (`__tabla__` para tablas) |
| `comentario` | VARCHAR(4000) | Comentario generado por IA |

---

## Release Notes

### v4.1 (2026-04-07)

**Mejoras:**
- **Dashboard desplegado como recurso DAB:** El dashboard Lakeview ahora se despliega automáticamente con `databricks bundle deploy`
- **Tarea `setup_schema`:** El job ahora incluye una tarea inicial que crea el esquema y tablas de control antes de generar comentarios (`setup_schema` → `generate_comments`)
- **Backtick quoting en SQL:** Todos los identificadores de catálogo, esquema y tabla se escapan con backticks para soportar nombres con caracteres especiales (e.g., guiones)
- **Soporte para `.xlsx`:** Agregado `openpyxl` como dependencia para lectura de archivos Excel
- **Prompt mejorado:** Instrucción explícita al modelo para usar solo el contexto proporcionado y no conocimiento previo
- **Variables genéricas:** Los defaults de las variables usan placeholders descriptivos en lugar de valores específicos de un workspace

### v4.0 (2026-04-06)

**Nuevas funcionalidades:**
- **Recorrido por catálogo completo:** Si no se especifica esquema, el proceso descubre y recorre todos los esquemas del catálogo automáticamente
- **Comentarios de esquema:** Genera comentarios de negocio a nivel de esquema, no solo de tablas y columnas
- **Modelo parametrizable:** El endpoint del modelo fundacional se puede configurar (default: `databricks-claude-sonnet-4-5`)
- **Esquema de resultados parametrizable:** El usuario decide en qué catálogo/esquema se persisten los resultados al desplegar
- **Auto-provisioning de tablas:** El esquema y tablas de control se crean automáticamente si no existen
- **Contexto dinámico desde mapping.md:** Lee `input/mapping.md` para entender el propósito de cada insumo y prioriza los archivos más relevantes por esquema/tabla
- **Sampling de datos:** Muestra aleatoria de cada tabla (configurable: `enable_sampling`, `sampling_pct`) para enriquecer el contexto del modelo con datos reales
- **Porcentaje de sampling parametrizable:** El usuario controla qué porcentaje de datos muestrear (1-100%, default: 10%)
- **Databricks Asset Bundle (DAB):** Bundle completo para despliegue con `databricks bundle deploy`, con variables parametrizables por target
- **Dashboard mejorado:** Filtros cascading (fecha → esquema → tabla), 4 KPIs, historial de ejecuciones, detalle de comentarios
- **Logging detallado:** Registro estructurado de cada etapa del proceso con timestamps

**Cambios respecto a v3:**
- El esquema ya no es requerido — puede estar vacío para procesar todo el catálogo
- Los insumos ya no se leen desde una ruta fija — se leen desde `input/mapping.md`
- Soporte para múltiples formatos de insumo (.docx, .tsv, .csv, .xlsx, .txt, .md, .json, .yaml)
- El porcentaje de sampling ahora es un parámetro configurable (antes fijo al 10%)
