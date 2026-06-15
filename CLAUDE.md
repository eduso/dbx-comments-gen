# CLAUDE.md — dbx-comments-gen

Instrucciones específicas para Claude Code en este proyecto.

## Idioma

Responder y documentar en español. Código, comandos y nombres técnicos en su idioma original.

## Ubicaciones del proyecto

Este proyecto vive en tres lugares que deben mantenerse alineados:

| Ubicación | URL / Path | Uso |
|-----------|------------|-----|
| Repositorio local | `/Users/eduardo.sojo/projects/dbx-comments-gen` | Fuente de verdad para edición |
| Repositorio remoto (GitHub) | https://github.com/eduso/dbx-comments-gen | Versionado y colaboración |
| Workspace de Databricks | https://adb-361426925668745.5.azuredatabricks.net/browse/folders/1195680441524839?o=361426925668745 | Despliegue activo (vía `databricks bundle deploy`) |

**Workflow estándar:**
1. Editar local → commit → push al remoto.
2. `databricks bundle deploy --target <env>` sincroniza local → workspace.
3. **Nunca** editar directamente en el workspace de Databricks (los cambios se pierden en el siguiente deploy).

## Convenciones del proyecto

- **Python:** alineado a PEP 8 (4 espacios, snake_case, líneas razonables).
- **SQL:** todos los identificadores (catalog/schema/table/column) se escapan con backticks.
- **Notebooks:** estructurados en etapas numeradas con `MAGIC %md`.
- **Prompts del modelo:** en español, persona "experto en documentación de datos de una organización", instrucción explícita de no usar conocimiento previo.
- **Defaults:** solo `model_endpoint` tiene default (`databricks-claude-sonnet-4-5`). El resto de parámetros son obligatorios.
- **Estructura aplanada:** todos los notebooks y el dashboard viven en `src/` (sin subdirectorios). El DDL es autoritativo en `src/01_setup_schema.py`.

## Pipelines

El bundle define **tres jobs independientes**:

1. `comments_pipeline` — generación + aplicación, tres tareas encadenadas:

   ```
   setup_schema → generate_comments → apply_comments
   ```

   `apply_comments` solo aplica registros con `status='aprobado'` (default al insertar).

2. `comments_audit_pipeline` — auditoría independiente (`04_audit_comments.py`). Escribe veredicto en `criterio_fallido` / `detalles_criterio_fallido`; no toca `status`.

3. `comments_eval_pipeline` — bake-off offline de modelos fundacionales (`05_eval_models.py`). Compara candidatos sobre el golden set, registra runs en MLflow y produce un scorecard. No genera ni aplica comentarios.

Los criterios de `src/audit_criteria.py` son la **fuente única**: los usa tanto el auditor como el juez del bake-off.

## Comandos frecuentes

```bash
# Validar el bundle
databricks bundle validate --target dev --profile <profile>

# Desplegar al workspace
databricks bundle deploy --target dev --profile <profile>

# Ejecutar el pipeline completo
databricks bundle run comments_pipeline --target dev --profile <profile>
```
