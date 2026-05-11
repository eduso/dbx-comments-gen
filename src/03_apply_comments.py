# Databricks notebook source
# MAGIC %md
# MAGIC # Aplicar comentarios a Unity Catalog
# MAGIC
# MAGIC Lee la tabla `resultados` y aplica al catálogo los comentarios cuyo
# MAGIC `status = 'aprobado'`. Soporta tres niveles:
# MAGIC
# MAGIC - **Esquema**: `nombre_tabla='__esquema__'`,
# MAGIC   `nombre_columna='__esquema__'`.
# MAGIC - **Tabla**: `nombre_columna='__tabla__'`.
# MAGIC - **Columna**: cualquier otro `nombre_columna`.
# MAGIC
# MAGIC ## Parámetros
# MAGIC
# MAGIC - `catalog_name` (obligatorio): catálogo destino donde se aplican.
# MAGIC - `results_catalog` / `results_schema` (obligatorios): donde está
# MAGIC   `resultados`.
# MAGIC - `id_ejecucion` (opcional): si se indica, aplica solo los de esa
# MAGIC   ejecución. Si está vacío, aplica todos los aprobados.

# COMMAND ----------

import logging

dbutils.widgets.text("catalog_name", "", "Catálogo destino")
dbutils.widgets.text("results_catalog", "", "Catálogo de resultados")
dbutils.widgets.text("results_schema", "", "Esquema de resultados")
dbutils.widgets.text(
    "id_ejecucion", "", "ID de ejecución (vacío = todos los aprobados)"
)

CATALOG_NAME = dbutils.widgets.get("catalog_name").strip()
RESULTS_CATALOG = dbutils.widgets.get("results_catalog").strip()
RESULTS_SCHEMA = dbutils.widgets.get("results_schema").strip()
ID_EJECUCION = dbutils.widgets.get("id_ejecucion").strip()

_required = {
    "catalog_name": CATALOG_NAME,
    "results_catalog": RESULTS_CATALOG,
    "results_schema": RESULTS_SCHEMA,
}
_missing = [k for k, v in _required.items() if not v]
if _missing:
    raise ValueError(
        f"Parámetros obligatorios sin valor: {', '.join(_missing)}"
    )

# Intentar resolver exec_id desde la tarea anterior si no se pasó
if not ID_EJECUCION:
    try:
        ID_EJECUCION = (
            dbutils.jobs.taskValues.get(
                taskKey="generate_comments",
                key="exec_id",
                default="",
                debugValue="",
            )
            or ""
        )
        if ID_EJECUCION:
            print(
                f"Usando id_ejecucion de la tarea generate_comments: "
                f"{ID_EJECUCION}"
            )
    except Exception:
        ID_EJECUCION = ""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("apply_comments")

RESULTS_TABLE = (
    f"`{RESULTS_CATALOG}`.`{RESULTS_SCHEMA}`.`resultados`"
)

print("=" * 60)
print("APLICANDO COMENTARIOS APROBADOS")
print("=" * 60)
print(f"  Catálogo destino : {CATALOG_NAME}")
print(f"  Tabla resultados : {RESULTS_CATALOG}.{RESULTS_SCHEMA}.resultados")
print(f"  ID ejecución     : {ID_EJECUCION or '(todos los aprobados)'}")
print("=" * 60)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Lectura de comentarios aprobados

# COMMAND ----------

filter_exec = (
    f" AND id_ejecucion = '{ID_EJECUCION}'" if ID_EJECUCION else ""
)

approved = spark.sql(
    f"""
    SELECT
        nombre_esquema,
        nombre_tabla,
        nombre_columna,
        comentario
    FROM {RESULTS_TABLE}
    WHERE status = 'aprobado'
      {filter_exec}
    ORDER BY nombre_esquema, nombre_tabla, nombre_columna
    """
).collect()

logger.info(f"Comentarios aprobados a aplicar: {len(approved)}")

if not approved:
    logger.warning("No hay comentarios aprobados para aplicar. Fin.")
    dbutils.notebook.exit("0 comentarios aplicados")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Aplicación al catálogo

# COMMAND ----------


def _esc(value: str) -> str:
    """Escapa comillas simples para sentencias SQL."""
    return (value or "").replace("'", "''")


def apply_comment(
    schema: str,
    table: str,
    column: str,
    comment: str,
) -> None:
    """Aplica un comentario al nivel correspondiente."""
    comment_sql = f"'{_esc(comment)}'"

    if table == "__esquema__" and column == "__esquema__":
        sql = (
            f"COMMENT ON SCHEMA `{CATALOG_NAME}`.`{schema}` "
            f"IS {comment_sql}"
        )
    elif column == "__tabla__":
        sql = (
            f"COMMENT ON TABLE "
            f"`{CATALOG_NAME}`.`{schema}`.`{table}` "
            f"IS {comment_sql}"
        )
    else:
        sql = (
            f"ALTER TABLE `{CATALOG_NAME}`.`{schema}`.`{table}` "
            f"ALTER COLUMN `{column}` COMMENT {comment_sql}"
        )

    spark.sql(sql)


applied = {"esquemas": 0, "tablas": 0, "columnas": 0}
errors: list = []

for row in approved:
    schema = row["nombre_esquema"]
    table = row["nombre_tabla"]
    column = row["nombre_columna"]
    comment = row["comentario"] or ""

    target = (
        f"esquema {schema}"
        if table == "__esquema__"
        else (
            f"tabla {schema}.{table}"
            if column == "__tabla__"
            else f"columna {schema}.{table}.{column}"
        )
    )

    try:
        apply_comment(schema, table, column, comment)
        if table == "__esquema__":
            applied["esquemas"] += 1
        elif column == "__tabla__":
            applied["tablas"] += 1
        else:
            applied["columnas"] += 1
        logger.info(f"  ✓ {target}")
    except Exception as exc:
        msg = f"{target}: {str(exc)[:200]}"
        errors.append(msg)
        logger.error(f"  ✗ {msg}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Resumen

# COMMAND ----------

total_applied = sum(applied.values())
print("=" * 60)
print("RESUMEN DE APLICACIÓN")
print("=" * 60)
print(f"  Esquemas : {applied['esquemas']}")
print(f"  Tablas   : {applied['tablas']}")
print(f"  Columnas : {applied['columnas']}")
print(f"  Total OK : {total_applied}")
print(f"  Errores  : {len(errors)}")
print("=" * 60)

if errors:
    print("\nPrimeros errores:")
    for err in errors[:10]:
        print(f"  - {err}")

dbutils.notebook.exit(
    f"Aplicados: {total_applied} | Errores: {len(errors)}"
)
