# dbx-comments-gen

Generador automático de comentarios de negocio para esquemas, tablas y columnas de Unity Catalog usando Foundation Model API de Databricks.

> **Estado:** v6 — scope desde tabla de control, soporte multi-catálogo y proceso de auditoría independiente.

---

## Tabla de contenidos

- [¿Qué hace?](#qué-hace)
- [Pipelines](#pipelines)
- [Estructura del repositorio](#estructura-del-repositorio)
- [Quick start](#quick-start)
- [Configuración mínima para pruebas](#configuración-mínima-para-pruebas)
- [Parámetros](#parámetros)
- [Tabla de scope](#tabla-de-scope)
- [Insumos de contexto](#insumos-de-contexto)
- [Tablas de resultados](#tablas-de-resultados)
- [Flujo de revisión](#flujo-de-revisión)
- [Auditoría de comentarios](#auditoría-de-comentarios)
- [Dashboard](#dashboard)
- [Desarrollo](#desarrollo)
- [Roadmap](#roadmap)
- [Release notes](#release-notes)

---

## ¿Qué hace?

1. Lee una **tabla de control** (scope) que indica qué tablas de Unity Catalog se deben documentar. Las tablas pueden vivir en distintos catálogos.
2. Genera comentarios de negocio en español para **esquemas**, **tablas** y **columnas** usando un modelo fundacional (default: `databricks-claude-sonnet-4-5`).
3. Usa como contexto **archivos** del directorio `input/` y/o **tablas** del lakehouse listadas en `input/mapping.md`.
4. Opcionalmente, muestrea datos reales de cada tabla para enriquecer el prompt.
5. Persiste resultados en una tabla `resultados` con campos `status` y `user_comments` para flujo de revisión.
6. Aplica al catálogo los comentarios con `status='aprobado'` (default) vía `COMMENT ON SCHEMA / TABLE` y `ALTER TABLE ... ALTER COLUMN`. Soporta múltiples catálogos en una sola corrida.
7. (Opcional) Audita los comentarios generados contra los insumos provistos y un catálogo de criterios declarativo, escribiendo el veredicto en `criterio_fallido` y `detalles_criterio_fallido`.

---

## Pipelines

El bundle define **dos jobs independientes**:

### `comments_pipeline` — generación + aplicación

```
┌──────────────────┐    ┌────────────────────┐    ┌──────────────────┐
│  setup_schema    │───▶│ generate_comments  │───▶│ apply_comments   │
│  (DDL)           │    │ (LLM + persiste)   │    │ (status=aprobado)│
└──────────────────┘    └────────────────────┘    └──────────────────┘
        │                        │                          │
        ▼                        ▼                          ▼
  ejecuciones /            scope_table              COMMENT ON / ALTER
  resultados            + information_schema        sobre Unity Catalog
                        + Foundation Model API
```

Por defecto todos los comentarios se insertan con `status='aprobado'`, así que el pipeline ejecuta de punta a punta. Para un flujo con revisión humana ver [Flujo de revisión](#flujo-de-revisión).

### `comments_audit_pipeline` — auditoría

```
┌──────────────────┐
│ audit_comments   │  Lee resultados + insumos (mapping.md +
│ (LLM auditor)    │  audit_mapping.md) y escribe veredicto en
└──────────────────┘  criterio_fallido / detalles_criterio_fallido.
```

Independiente del job principal. No modifica `status` ni el contenido del comentario.

---

## Estructura del repositorio

```
dbx-comments-gen/
├── databricks.yml                # Bundle: jobs, dashboard, vars, targets
├── README.md
├── CLAUDE.md                     # Instrucciones para Claude Code
├── .gitignore
├── input/
│   ├── mapping.md                # Insumos: archivos y tablas
│   ├── audit_mapping.md          # Insumos extra solo para auditoría
│   └── (documentos del usuario)
└── src/
    ├── 01_setup_schema.py        # DDL: esquema + ejecuciones + resultados
    ├── 02_generate_comments.py   # Generador con IA (desde scope_table)
    ├── 03_apply_comments.py      # Aplicador multi-catálogo
    ├── 04_audit_comments.py      # Auditor independiente
    ├── audit_criteria.py         # Catálogo declarativo de criterios
    └── dashboard.lvdash.json     # Dashboard Lakeview
```

---

## Quick start

### 1. Prerrequisitos

- Databricks CLI v0.200+ con un perfil autenticado.
- Workspace con permisos para crear catálogos, esquemas, jobs y dashboards.
- Un SQL Warehouse disponible.
- Una **tabla de scope** existente en Unity Catalog (ver [Tabla de scope](#tabla-de-scope)).

### 2. Configurar el bundle

Edita `databricks.yml` y reemplaza los placeholders en `targets:`:

```yaml
targets:
  dev:
    workspace:
      host: https://<tu-workspace>.azuredatabricks.net
  prod:
    workspace:
      host: https://<tu-workspace-prod>.azuredatabricks.net
```

### 3. (Opcional) Agregar insumos de contexto

Edita `input/mapping.md`. Soporta dos secciones:

```markdown
# Archivos
`diccionario.docx`: Diccionario de datos oficial
`reglas_negocio.md`: Reglas de negocio por dominio

# Tablas
main.referencia.glosario_negocio: Glosario corporativo de términos
main.referencia.taxonomia_productos: Taxonomía de productos
```

Coloca los archivos referenciados en `input/`. Si una tabla no es accesible, el proceso emite warning y continúa sin ella.

### 4. Desplegar y ejecutar

```bash
# Validar
databricks bundle validate --target dev --profile <profile>

# Desplegar
databricks bundle deploy --target dev --profile <profile>

# Ejecutar el pipeline de generación + aplicación
databricks bundle run comments_pipeline --target dev --profile <profile> \
  --params scope_table=main.control.scope_documentacion,\
scope_catalog_column=catalog_name,\
scope_schema_column=schema_name,\
scope_table_column=table_name,\
results_catalog=mi_resultados,\
results_schema=ai_comments,\
enable_sampling=yes,sampling_pct=15

# (Opcional) Ejecutar la auditoría sobre los comentarios persistidos
databricks bundle run comments_audit_pipeline --target dev --profile <profile> \
  --params results_catalog=mi_resultados,results_schema=ai_comments
```

---

## Configuración mínima para pruebas

El cuello de botella del pipeline es la **latencia del Foundation Model API**, no Spark. El loop es secuencial por columna y no aprovecha paralelismo, así que conviene un cluster mínimo y barato para validar end-to-end.

### Opción A — Single-node clásico (default del bundle)

Funciona en cualquier workspace. Configuración recomendada para `new_cluster` en `databricks.yml`:

```yaml
new_cluster: &single_node_cluster
  spark_version: "15.4.x-scala2.12"
  num_workers: 0
  node_type_id: "Standard_D4ads_v5"     # 4 vCPU / 16 GB — ~30% más barato que DS3_v2
  data_security_mode: "SINGLE_USER"     # requerido para UC + identity columns
  runtime_engine: "STANDARD"            # Photon off (no aporta a este workload)
  spark_conf:
    spark.master: "local[*, 4]"
    spark.databricks.cluster.profile: "singleNode"
  custom_tags:
    ResourceClass: "SingleNode"
    project: "dbx-comments-gen"
```

### Opción B — Serverless jobs compute

Si está habilitado en el workspace, es la opción más simple y de arranque más rápido (< 30 s). Reemplaza el bloque `new_cluster: *single_node_cluster` de cada tarea por:

```yaml
- task_key: setup_schema
  description: "Crea esquema y tablas de control"
  notebook_task:
    notebook_path: src/01_setup_schema.py
    base_parameters:
      results_catalog: "{{job.parameters.results_catalog}}"
      results_schema:  "{{job.parameters.results_schema}}"
    source: WORKSPACE
  # ← sin new_cluster: serverless se infiere por ausencia de compute
```

Aplicar el mismo cambio a las tareas `generate_comments`, `apply_comments` y `audit_comments`.

### Para el dashboard

SQL Warehouse **Serverless 2X-Small** con auto-stop a 10 min es suficiente — las queries son agregaciones simples sobre dos tablas Delta pequeñas.

### Estrategia de prueba mínima

Para validar el pipeline end-to-end con el menor costo posible:

1. **Scope table chiquita**: 1–2 tablas con pocas columnas (≤ 20). El costo crece linealmente con `tablas × columnas`.
2. `enable_sampling=no` en la primera corrida — evita queries adicionales a las tablas a documentar.
3. Mantener `model_endpoint=databricks-claude-sonnet-4-5` (pago por token, sin endpoint dedicado).
4. Correr **solo** `setup_schema + generate_comments` primero (desde la UI del job, deseleccionar `apply_comments`) para revisar los comentarios antes de aplicarlos al catálogo.
5. La auditoría (`comments_audit_pipeline`) es opcional — saltarla en la primera prueba.

---

## Parámetros

### `comments_pipeline`

| Parámetro | Descripción | Default |
|-----------|-------------|---------|
| `scope_table` | Tabla de control `catalogo.esquema.tabla` con los objetos a documentar | _(obligatorio)_ |
| `scope_catalog_column` | Nombre de la columna de `scope_table` que contiene el catálogo | _(obligatorio)_ |
| `scope_schema_column` | Nombre de la columna que contiene el esquema | _(obligatorio)_ |
| `scope_table_column` | Nombre de la columna que contiene la tabla | _(obligatorio)_ |
| `model_endpoint` | Endpoint del modelo fundacional | `databricks-claude-sonnet-4-5` |
| `results_catalog` | Catálogo donde se guardan los resultados | _(obligatorio)_ |
| `results_schema` | Esquema donde se guardan los resultados | _(obligatorio)_ |
| `enable_sampling` | Habilitar muestreo de datos reales (`yes`/`no`) | `no` |
| `sampling_pct` | Porcentaje para tablas > 500 registros (1-100) | _(requerido si sampling=yes)_ |

### `comments_audit_pipeline`

| Parámetro | Descripción | Default |
|-----------|-------------|---------|
| `results_catalog` | Catálogo donde está la tabla `resultados` | _(obligatorio)_ |
| `results_schema` | Esquema donde está la tabla `resultados` | _(obligatorio)_ |
| `id_ejecucion` | ID de ejecución a auditar. Vacío = todas las filas | _(vacío)_ |
| `audit_only_approved` | Auditar solo filas con `status='aprobado'` (`yes`/`no`) | `yes` |
| `audit_model_endpoint` | Modelo a usar como auditor | `databricks-claude-sonnet-4-5` |

---

## Tabla de scope

A partir de v6 el generador no recorre un catálogo completo, sino que lee una **tabla de control** suministrada por el usuario. Esto permite:

- Documentar tablas de **múltiples catálogos** en una sola corrida.
- Mantener listas curadas de objetos a documentar (por dominio, por proyecto, por sprint…).
- Versionar el alcance fuera del notebook.

### Formato esperado

La tabla debe tener al menos tres columnas con los nombres del catálogo, esquema y tabla. Los nombres exactos se configuran vía parámetros (`scope_catalog_column`, `scope_schema_column`, `scope_table_column`).

Ejemplo:

```sql
CREATE TABLE main.control.scope_documentacion (
    catalog_name STRING,
    schema_name  STRING,
    table_name   STRING,
    prioridad    INT,
    dominio      STRING
);

INSERT INTO main.control.scope_documentacion VALUES
    ('ventas',    'mart',     'fact_pedidos',  1, 'comercial'),
    ('ventas',    'mart',     'dim_cliente',   1, 'comercial'),
    ('finanzas',  'staging',  'movimientos',   2, 'finanzas');
```

Filas con valores nulos o vacíos en cualquiera de las tres columnas se omiten. Las combinaciones `(catalogo, esquema, tabla)` se deduplican automáticamente. Si una tabla del scope no existe o no se tiene permiso, se omite con warning.

---

## Insumos de contexto

### Sección `# Archivos`

Formatos: `.docx`, `.tsv`, `.csv`, `.xlsx`, `.txt`, `.md`, `.json`, `.yaml`.

```markdown
# Archivos
`diccionario.docx`: Diccionario oficial
```

### Sección `# Tablas`

Tablas de Unity Catalog en formato `catalogo.esquema.tabla: descripción`. Se cargan las primeras 200 filas como texto.

```markdown
# Tablas
main.referencia.glosario_negocio: Glosario corporativo
```

Si una tabla no existe o no se tiene permiso, se emite un warning y el proceso continúa.

### Hints opcionales en la descripción

Dentro de la descripción se pueden incluir hints entre corchetes para acotar qué cargar:

| Insumo | Sintaxis | Efecto |
|--------|----------|--------|
| Excel (`.xls` / `.xlsx`) | `[tabs: tab1, tab2]` | Lee solo esas hojas |
| Tabla UC | `[columnas: col1, col2]` | Selecciona solo esas columnas |

Alias aceptados (case-insensitive): `tab`/`tabs`/`hoja`/`hojas`/`sheet`/`sheets`; `columna`/`columnas`/`column`/`columns`/`campo`/`campos`/`field`/`fields`.

Ejemplos:

```markdown
# Archivos
`catalogo_productos.xlsx`: Catálogo vigente. [tabs: productos, sub_productos]

# Tablas
main.referencia.glosario: Glosario corporativo. [columnas: termino, definicion, dominio]
```

Si una hoja del Excel no existe, el proceso emite un warning y continúa con las hojas restantes. Si una columna especificada no existe, la query falla y la tabla se omite (con warning).

### Priorización dinámica

Cada insumo recibe un **score** por esquema/tabla:

- **+10** si el nombre del esquema aparece en contenido/nombre/descripción.
- **+15** si el nombre de la tabla aparece.
- **+4** si es tabla del lakehouse.
- **+3** si es `.docx`; **+2** si es `.tsv`; **+1.5** si es `.md`/`.txt`.
- **+1** por keyword en la descripción (`definic`, `negocio`, `regla`, `glosario`, `taxonom`, etc.).

Los insumos se concatenan en orden descendente respetando un límite de **30K caracteres** (~7.500 tokens). El último se trunca si no cabe.

---

## Tablas de resultados

Se crean automáticamente en `{results_catalog}.{results_schema}`.

### `ejecuciones`

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `id_ejecucion` | STRING (PK) | GUID único de la ejecución |
| `fecha_ejecucion` | TIMESTAMP | Fecha/hora UTC |
| `estado` | VARCHAR(50) | `INICIADO`, `EN_PROCESO`, `COMPLETADO`, `COMPLETADO_CON_ERRORES`, `ERROR` |
| `resultado` | VARCHAR(4000) | Detalle del resultado o errores |

### `resultados`

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `id_resultado` | BIGINT (PK, identity) | ID autoincrementable |
| `id_ejecucion` | STRING (FK) | Referencia a `ejecuciones.id_ejecucion` |
| `fecha_resultado` | TIMESTAMP | Fecha/hora UTC de generación |
| **`nombre_catalogo`** | VARCHAR(255) | Catálogo de la tabla procesada |
| `nombre_esquema` | VARCHAR(255) | Esquema procesado |
| `nombre_tabla` | VARCHAR(255) | Tabla procesada (`__esquema__` para esquemas) |
| `nombre_columna` | VARCHAR(255) | Columna procesada (`__tabla__` para tablas, `__esquema__` para esquemas) |
| `comentario` | VARCHAR(4000) | Comentario generado por IA |
| `status` | VARCHAR(20) | `por revisar`, `aprobado` (default), `rechazado` |
| `user_comments` | VARCHAR(4000) | Comentarios del revisor (vacío por defecto) |
| **`criterio_fallido`** | STRING | Criterio de auditoría fallido (NULL si OK). Lo escribe `04_audit_comments` |
| **`detalles_criterio_fallido`** | STRING | Justificación del criterio fallido (NULL si OK) |

Ambas tablas tienen `delta.enableChangeDataFeed = true`. La columna `status` tiene un `CHECK` constraint para los tres valores permitidos.

---

## Flujo de revisión

El default `status='aprobado'` significa que el pipeline aplica automáticamente todos los comentarios generados. Para revisar antes de aplicar:

1. **Ejecutar solo `setup_schema` + `generate_comments`** (desde la UI de Databricks, seleccionar y correr solo esas tareas).
2. **(Opcional) Correr la auditoría** (`comments_audit_pipeline`) para obtener un primer screening automático.
3. **Revisar y actualizar** los registros:
   ```sql
   UPDATE my_results.ai_comments.resultados
   SET status = 'por revisar',
       user_comments = 'Pendiente validación con stakeholder'
   WHERE id_ejecucion = '<exec-id>' AND nombre_tabla = 'cliente';

   UPDATE my_results.ai_comments.resultados
   SET status = 'rechazado',
       user_comments = 'El término no aplica para nuestro negocio'
   WHERE id_resultado IN (123, 456);
   ```
4. **Aprobar lo que esté bien**:
   ```sql
   UPDATE my_results.ai_comments.resultados
   SET status = 'aprobado'
   WHERE id_ejecucion = '<exec-id>' AND status = 'por revisar';
   ```
5. **Ejecutar `apply_comments`** (manualmente desde la UI), opcionalmente filtrando por `id_ejecucion`.

---

## Auditoría de comentarios

A partir de v6 existe un proceso independiente (`04_audit_comments.py`, expuesto como job `comments_audit_pipeline`) que evalúa cada comentario contra los insumos provistos usando un LLM como auditor.

### Cómo funciona

1. Carga insumos de `input/mapping.md` **y** `input/audit_mapping.md` (este último es opcional y solo lo lee la auditoría — útil para glosarios estrictos, lineamientos editoriales, ejemplos correctos/incorrectos, etc.).
2. Lee las filas de `resultados` (filtrables por `id_ejecucion` y por `status='aprobado'`).
3. Para cada fila construye un prompt con el contexto relevante y le pide al modelo un veredicto en JSON.
4. Si el comentario está bien: escribe `NULL` en `criterio_fallido` / `detalles_criterio_fallido` (idempotente — borra veredictos previos).
5. Si falla un criterio: escribe el `id` del criterio en `criterio_fallido` y una justificación corta en `detalles_criterio_fallido`.

**Nunca modifica `status` ni el contenido del comentario**. La decisión final de aprobar/rechazar sigue siendo humana.

### Catálogo de criterios

Definido de forma declarativa en `src/audit_criteria.py`. Para agregar/quitar/modificar un criterio basta editar ese archivo:

| ID | Detecta |
|----|---------|
| `FUERA_DE_CONTEXTO` | Afirmaciones no respaldadas por los insumos (alucinación). |
| `TERMINOLOGIA_INCORRECTA` | Uso de términos distintos a los canónicos del glosario. |
| `GRANULARIDAD` | Comentario con nivel incorrecto (p. ej. una columna describe la tabla). |
| `IDIOMA_O_ESTILO` | Idioma, longitud, formato markdown, prefijos tipo `Definición:`, etc. |
| `INFORMACION_FALTANTE` | Omite información clave disponible en los insumos. |

Cada criterio tiene un `id` (string corto en MAYÚSCULAS) y un `description` que se inyecta literalmente en el prompt del auditor.

### Ejecución

```bash
# Auditar todas las filas aprobadas
databricks bundle run comments_audit_pipeline --target dev --profile <profile> \
  --params results_catalog=mi_resultados,results_schema=ai_comments

# Auditar una ejecución específica, incluyendo no-aprobadas
databricks bundle run comments_audit_pipeline --target dev --profile <profile> \
  --params results_catalog=mi_resultados,results_schema=ai_comments,\
id_ejecucion=<exec-id>,audit_only_approved=no
```

Si la auditoría se lanza directamente después de `generate_comments` (encadenada manualmente o desde una orquestación externa), toma automáticamente el `id_ejecucion` mediante `taskValues`.

---

## Dashboard

Recurso DAB: `comments_dashboard` (archivo `src/dashboard.lvdash.json`).

**Totalmente parametrizado:** las queries usan `IDENTIFIER(:results_catalog || '.' || :results_schema || '.<tabla>')` para resolver dinámicamente las tablas de resultados, así que el mismo dashboard sirve para cualquier deploy.

**Parámetros del dashboard:**

| Parámetro | Descripción |
|-----------|-------------|
| `results_catalog` | Catálogo donde están las tablas de resultados |
| `results_schema` | Esquema donde están las tablas de resultados |
| `fecha` | Filtra todo por fecha de ejecución |
| `catalogo` | Filtra por catálogo procesado |
| `esquema` | Filtra por esquema procesado |
| `tabla` | Filtra por tabla procesada (multi-select) |
| `status` | Filtra por estado de revisión |

Al abrir el dashboard por primera vez, hay que seleccionar `results_catalog` y `results_schema`; Lakeview persiste la selección.

**Widgets:**

- Filtros: catálogo/esquema (dataset), fecha, catálogo, esquema, tabla, status.
- KPIs: ejecuciones, comentarios, catálogos, esquemas, tablas, aprobados, por revisar, rechazados.
- Tabla: historial de ejecuciones.
- Tabla: detalle de comentarios (incluye `status`, `user_comments`, `criterio_fallido` y `detalles_criterio_fallido`).

---

## Desarrollo

### Convenciones

- **Python:** alineado a PEP 8.
- **SQL:** identificadores escapados con backticks (soporta nombres con guiones).
- **Notebooks:** estructurados en etapas numeradas con `MAGIC %md`.
- **Prompts:** en español, persona "experto en documentación de datos de una organización", instrucción explícita de no usar conocimiento previo.

### Mapa de cambios frecuentes

| Quieres cambiar... | Edita... |
|--------------------|----------|
| Prompt enviado al modelo generador | `02_generate_comments.py` → `generate_*_comment` |
| Prompt del auditor | `04_audit_comments.py` → `audit_row` |
| Catálogo de criterios | `audit_criteria.py` → `AUDIT_CRITERIA` |
| Scoring de relevancia | `02_generate_comments.py` → `_score_relevance` |
| Límite de contexto | `02_generate_comments.py` / `04_audit_comments.py` → `MAX_CONTEXT_CHARS` |
| Reglas de sampling | `02_generate_comments.py` → `get_table_sample` |
| Esquema de tablas de control | `01_setup_schema.py` |
| Lógica de aplicación | `03_apply_comments.py` → `apply_comment` |
| Parámetros expuestos | `databricks.yml` + widgets de cada notebook |

### Targets

| Target | Modo | Notas |
|--------|------|-------|
| `dev` | development | Default. |
| `prod` | production | Override de host (y variables si aplica). |

---

## Roadmap

- [ ] **Modo dry-run** — generar prompts sin invocar al modelo (debug).
- [ ] **Reintentos** con backoff exponencial cuando el endpoint falla.
- [ ] **Paralelismo** en generación de columnas dentro de una tabla.
- [ ] **Auditoría → status automático** (opt-in para mover a `por revisar` cuando hay observación).
- [ ] **Soporte multilenguaje** (`output_language=es|en|pt`).
- [ ] **Marcador de "aplicado"** en `resultados` para evitar reaplicar.

---

## Release notes

### v6 (en curso)

- **Scope desde tabla de control**: el generador ya no recorre catálogos completos. Lee una tabla configurable (`scope_table` + tres columnas con catálogo/esquema/tabla) y procesa exactamente esos objetos. Permite mezclar varios catálogos en una sola corrida.
- **Soporte multi-catálogo**: la tabla `resultados` ahora persiste `nombre_catalogo`, y `apply_comments` aplica los comentarios al catálogo correcto fila por fila.
- **Proceso de auditoría** (`04_audit_comments.py` + job `comments_audit_pipeline`): notebook independiente que evalúa los comentarios contra los insumos provistos usando un LLM, escribe veredicto en `criterio_fallido` / `detalles_criterio_fallido` y es idempotente.
- **Catálogo declarativo de criterios** en `src/audit_criteria.py` (`FUERA_DE_CONTEXTO`, `TERMINOLOGIA_INCORRECTA`, `GRANULARIDAD`, `IDIOMA_O_ESTILO`, `INFORMACION_FALTANTE`).
- **Nuevo archivo de insumos** `input/audit_mapping.md`: insumos extra que solo carga la auditoría (glosarios estrictos, lineamientos editoriales, ejemplos buenos/malos, etc.).
- **Dashboard ampliado**: filtro y KPI por `catalogo`, columnas `Criterio fallido` y `Detalles criterio fallido` en la tabla de detalle.

### v5

- **Pipeline completo**: nueva tarea `apply_comments` que aplica al catálogo los comentarios con `status='aprobado'`.
- **Tablas como insumo**: `input/mapping.md` ahora soporta una sección `# Tablas` con tablas de Unity Catalog. Si no son accesibles, warning y continúa.
- **Flujo de revisión**: nuevas columnas `status` (`por revisar` / `aprobado` / `rechazado`, default `aprobado`) y `user_comments` en `resultados`.
- **Parámetros sin defaults**: solo `model_endpoint` tiene default. El resto debe especificarse explícitamente.
- **PEP 8** en todos los archivos.
- **Estructura compactada**: aplanado `src/` (eliminados `src/notebooks/`, `src/pipelines/`, `src/__init__.py` y `src/pipelines/01_repositorio_resultados.sql`).
- **.gitignore** ampliado (notebooks/IDE/Terraform/Databricks bundle locks).
- **Dashboard dinámico**: queries con `IDENTIFIER(:results_catalog || '.' || :results_schema || ...)`, filtro de `status`, KPIs de aprobados/por revisar/rechazados, columnas `status` y `user_comments` en la tabla de detalle.
- **Hints en mapping.md**: la descripción de cada insumo acepta `[tabs: ...]` (Excel multi-hoja) y `[columnas: ...]` (tablas UC) para acotar la carga.

### v4.1 (2026-04-07)

- Dashboard desplegado como recurso DAB.
- Tarea `setup_schema` agregada al job.
- Backtick quoting en todos los identificadores SQL.
- Soporte para `.xlsx` (`openpyxl`).
- Prompt: instrucción explícita de usar solo el contexto provisto.

### v4.0 (2026-04-06)

- Recorrido por catálogo completo (esquema opcional).
- Comentarios a nivel de esquema (no solo tabla/columna).
- Modelo, catálogo y esquema de resultados parametrizables.
- Auto-provisioning de tablas de control.
- Contexto dinámico priorizado desde `input/mapping.md`.
- Sampling de datos opcional con porcentaje configurable.
- Empaquetado como Databricks Asset Bundle.
- Dashboard con filtros cascading, 4 KPIs e historial.
