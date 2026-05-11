# dbx-comments-gen

Generador automático de comentarios de negocio para esquemas, tablas y columnas de Unity Catalog usando Foundation Model API de Databricks.

> **Estado:** v5 — pipeline completo (generación + aplicación) con flujo de revisión humana opcional.

---

## Tabla de contenidos

- [¿Qué hace?](#qué-hace)
- [Pipeline](#pipeline)
- [Estructura del repositorio](#estructura-del-repositorio)
- [Quick start](#quick-start)
- [Parámetros](#parámetros)
- [Insumos de contexto](#insumos-de-contexto)
- [Tablas de resultados](#tablas-de-resultados)
- [Flujo de revisión](#flujo-de-revisión)
- [Dashboard](#dashboard)
- [Desarrollo](#desarrollo)
- [Roadmap](#roadmap)
- [Release notes](#release-notes)

---

## ¿Qué hace?

1. Recorre un catálogo de Unity Catalog (o un esquema específico).
2. Genera comentarios de negocio en español para **esquemas**, **tablas** y **columnas** usando un modelo fundacional (default: `databricks-claude-sonnet-4-5`).
3. Usa como contexto **archivos** del directorio `input/` y/o **tablas** del lakehouse listadas en `input/mapping.md`.
4. Opcionalmente, muestrea datos reales de cada tabla para enriquecer el prompt.
5. Persiste resultados en una tabla `resultados` con campos `status` y `user_comments` para flujo de revisión.
6. Aplica al catálogo los comentarios con `status='aprobado'` (default) vía `COMMENT ON SCHEMA / TABLE` y `ALTER TABLE ... ALTER COLUMN`.

---

## Pipeline

Tres tareas encadenadas en un solo job `comments_pipeline`:

```
┌──────────────────┐    ┌────────────────────┐    ┌──────────────────┐
│  setup_schema    │───▶│ generate_comments  │───▶│ apply_comments   │
│  (DDL)           │    │ (LLM + persiste)   │    │ (status=aprobado)│
└──────────────────┘    └────────────────────┘    └──────────────────┘
        │                        │                          │
        ▼                        ▼                          ▼
  ejecuciones /            information_schema       COMMENT ON / ALTER
  resultados               + Foundation Model API   sobre Unity Catalog
```

Por defecto todos los comentarios se insertan con `status='aprobado'`, así que el pipeline ejecuta de punta a punta. Para un flujo con revisión humana, ver [Flujo de revisión](#flujo-de-revisión).

---

## Estructura del repositorio

```
dbx-comments-gen/
├── databricks.yml                # Bundle: pipeline, dashboard, vars, targets
├── README.md
├── CLAUDE.md                     # Instrucciones para Claude Code
├── .gitignore
├── input/
│   ├── mapping.md                # Insumos: archivos y tablas
│   └── (documentos del usuario)
└── src/
    ├── 01_setup_schema.py        # DDL: esquema + ejecuciones + resultados
    ├── 02_generate_comments.py   # Generador con IA
    ├── 03_apply_comments.py      # Aplicador de comentarios aprobados
    └── dashboard.lvdash.json     # Dashboard Lakeview
```

---

## Quick start

### 1. Prerrequisitos

- Databricks CLI v0.200+ con un perfil autenticado.
- Workspace con permisos para crear catálogos, esquemas, jobs y dashboards.
- Un SQL Warehouse disponible.

### 2. Configurar el bundle

Edita `databricks.yml` y reemplaza los placeholders:

```yaml
targets:
  dev:
    workspace:
      host: https://<tu-workspace>.azuredatabricks.net   # ← tu workspace
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

Y coloca los archivos referenciados en `input/`. Si una tabla no es accesible, el proceso emite un warning y continúa sin ella.

### 4. Desplegar y ejecutar

```bash
# Validar
databricks bundle validate --target dev --profile <profile>

# Desplegar
databricks bundle deploy --target dev --profile <profile>

# Ejecutar con todos los parámetros
databricks bundle run comments_pipeline --target dev --profile <profile> \
  --params catalog_name=mi_catalogo,schema_name=ventas,\
results_catalog=mi_resultados,results_schema=ai_comments,\
enable_sampling=yes,sampling_pct=15
```

---

## Parámetros

Todos son obligatorios excepto `model_endpoint`, `schema_name`, `enable_sampling` y `sampling_pct`.

| Parámetro | Descripción | Default |
|-----------|-------------|---------|
| `catalog_name` | Catálogo de Unity Catalog a procesar | _(obligatorio)_ |
| `schema_name` | Esquema específico. Vacío = todo el catálogo | _(vacío)_ |
| `model_endpoint` | Endpoint del modelo fundacional | `databricks-claude-sonnet-4-5` |
| `results_catalog` | Catálogo donde se guardan los resultados | _(obligatorio)_ |
| `results_schema` | Esquema donde se guardan los resultados | _(obligatorio)_ |
| `enable_sampling` | Habilitar muestreo de datos reales (`yes`/`no`) | `no` |
| `sampling_pct` | Porcentaje para tablas > 500 registros (1-100) | _(requerido si sampling=yes)_ |

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
| `nombre_esquema` | VARCHAR(255) | Esquema procesado |
| `nombre_tabla` | VARCHAR(255) | Tabla procesada (`__esquema__` para esquemas) |
| `nombre_columna` | VARCHAR(255) | Columna procesada (`__tabla__` para tablas, `__esquema__` para esquemas) |
| `comentario` | VARCHAR(4000) | Comentario generado por IA |
| **`status`** | VARCHAR(20) | `por revisar`, `aprobado` (default), `rechazado` |
| **`user_comments`** | VARCHAR(4000) | Comentarios del revisor (vacío por defecto) |

Ambas tablas tienen `delta.enableChangeDataFeed = true`. La columna `status` tiene un `CHECK` constraint para los tres valores permitidos.

---

## Flujo de revisión

El default `status='aprobado'` significa que el pipeline aplica automáticamente todos los comentarios generados. Para revisar antes de aplicar:

1. **Ejecutar solo `setup_schema` + `generate_comments`** (desde la UI de Databricks, seleccionar y correr solo esas tareas).
2. **Revisar y actualizar** los registros:
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
3. **Aprobar lo que esté bien**:
   ```sql
   UPDATE my_results.ai_comments.resultados
   SET status = 'aprobado'
   WHERE id_ejecucion = '<exec-id>' AND status = 'por revisar';
   ```
4. **Ejecutar `apply_comments`** (manualmente desde la UI), opcionalmente filtrando por `id_ejecucion`.

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
| `esquema` | Filtra por esquema procesado |
| `tabla` | Filtra por tabla procesada (multi-select) |
| `status` | Filtra por estado de revisión |

Al abrir el dashboard por primera vez, hay que seleccionar `results_catalog` y `results_schema`; Lakeview persiste la selección.

**Widgets:**

- Filtros: catálogo/esquema (dataset), fecha, esquema, tabla, status.
- KPIs: ejecuciones, comentarios, esquemas, tablas, aprobados, por revisar, rechazados.
- Tabla: historial de ejecuciones.
- Tabla: detalle de comentarios (incluye `status` y `user_comments`).

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
| Prompt enviado al modelo | `02_generate_comments.py` → `generate_*_comment` |
| Scoring de relevancia | `02_generate_comments.py` → `_score_relevance` |
| Límite de contexto | `02_generate_comments.py` → `MAX_CONTEXT_CHARS` |
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
- [ ] **Evaluación de calidad** (LLM-as-judge sobre los generados).
- [ ] **Soporte multilenguaje** (`output_language=es|en|pt`).
- [ ] **Filtros include/exclude** por pattern de tablas.
- [ ] **Marcador de "aplicado"** en `resultados` para evitar reaplicar.

---

## Release notes

### v5 (en curso)

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
