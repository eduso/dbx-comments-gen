# Databricks notebook source
# MAGIC %md
# MAGIC # Setup: Repositorio de resultados
# MAGIC
# MAGIC Crea el esquema y las tablas de control para la generación de
# MAGIC comentarios con IA.
# MAGIC
# MAGIC ## Tablas creadas
# MAGIC
# MAGIC - `ejecuciones`: registro de cada corrida del proceso.
# MAGIC - `resultados`: comentarios generados con campos de revisión
# MAGIC   (`status`, `user_comments`).

# COMMAND ----------

dbutils.widgets.text("results_catalog", "", "Catálogo de resultados")
dbutils.widgets.text("results_schema", "", "Esquema de resultados")

results_catalog = dbutils.widgets.get("results_catalog").strip()
results_schema = dbutils.widgets.get("results_schema").strip()

if not results_catalog or not results_schema:
    raise ValueError(
        "Los parámetros 'results_catalog' y 'results_schema' son obligatorios."
    )

print(f"Creando esquema de resultados: `{results_catalog}`.`{results_schema}`")

# COMMAND ----------

spark.sql(
    f"""
    CREATE SCHEMA IF NOT EXISTS `{results_catalog}`.`{results_schema}`
    COMMENT 'Esquema para generación automática de comentarios usando IA'
    """
)

print(f"Esquema `{results_catalog}`.`{results_schema}` OK")

# COMMAND ----------

spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS
        `{results_catalog}`.`{results_schema}`.`ejecuciones` (
        id_ejecucion    STRING        NOT NULL
            COMMENT 'GUID de la ejecución — generado con uuid() al insertar',
        fecha_ejecucion TIMESTAMP
            COMMENT 'Fecha y hora de inicio de la ejecución',
        estado          VARCHAR(50)
            COMMENT 'INICIADO, EN_PROCESO, COMPLETADO, '
                    'COMPLETADO_CON_ERRORES, ERROR',
        resultado       VARCHAR(4000)
            COMMENT 'Detalle del resultado final de la ejecución',
        CONSTRAINT pk_ejecuciones PRIMARY KEY (id_ejecucion)
    )
    COMMENT 'Registro de cada ejecución del proceso de generación'
    TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
    """
)

print(f"Tabla `{results_catalog}`.`{results_schema}`.`ejecuciones` OK")

# COMMAND ----------

spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS
        `{results_catalog}`.`{results_schema}`.`resultados` (
        id_resultado    BIGINT GENERATED ALWAYS AS IDENTITY
            COMMENT 'Identificador autoincrementable del resultado',
        id_ejecucion    STRING        NOT NULL
            COMMENT 'Referencia a la ejecución que generó este resultado',
        fecha_resultado TIMESTAMP
            COMMENT 'Fecha y hora en que se generó el comentario',
        nombre_catalogo VARCHAR(255)
            COMMENT 'Nombre del catálogo al que pertenece la tabla',
        nombre_esquema  VARCHAR(255)
            COMMENT 'Nombre del esquema al que pertenece la tabla',
        nombre_tabla    VARCHAR(255)
            COMMENT 'Nombre de la tabla procesada',
        nombre_columna  VARCHAR(255)
            COMMENT 'Nombre de la columna para la que se generó el comentario',
        comentario      VARCHAR(4000)
            COMMENT 'Comentario generado por IA para la columna',
        status          VARCHAR(20)   NOT NULL DEFAULT 'aprobado'
            COMMENT 'Estado de revisión: por revisar, aprobado, rechazado',
        user_comments   VARCHAR(4000)
            COMMENT 'Comentarios del revisor (vacío por defecto)',
        criterio_fallido         STRING
            COMMENT 'Identificador del criterio de calidad fallido (NULL si OK)',
        detalles_criterio_fallido STRING
            COMMENT 'Detalles del criterio fallido (NULL si OK)',
        CONSTRAINT pk_resultados PRIMARY KEY (id_resultado),
        CONSTRAINT fk_ejecucion FOREIGN KEY (id_ejecucion)
            REFERENCES
                `{results_catalog}`.`{results_schema}`.`ejecuciones` (
                    id_ejecucion
                ),
        CONSTRAINT chk_status CHECK (
            status IN ('por revisar', 'aprobado', 'rechazado')
        )
    )
    COMMENT 'Comentarios generados por IA con flujo de revisión'
    TBLPROPERTIES (
        'delta.enableChangeDataFeed' = 'true',
        'delta.feature.allowColumnDefaults' = 'supported'
    )
    """
)

print(f"Tabla `{results_catalog}`.`{results_schema}`.`resultados` OK")

# COMMAND ----------

print("Setup completado exitosamente.")
