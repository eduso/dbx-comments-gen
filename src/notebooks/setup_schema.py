# Databricks notebook source
# MAGIC %md
# MAGIC # Setup: Repositorio de resultados
# MAGIC Crea el esquema y las tablas de control para la generación de comentarios con IA.

# COMMAND ----------

dbutils.widgets.text("results_catalog", "mi_catalogo_de_resultados", "Catálogo de resultados")
dbutils.widgets.text("results_schema", "mi_esquema_de_resultados", "Esquema de resultados")

results_catalog = dbutils.widgets.get("results_catalog")
results_schema = dbutils.widgets.get("results_schema")

print(f"Creando esquema de resultados: `{results_catalog}`.`{results_schema}`")

# COMMAND ----------

spark.sql(f"""
CREATE SCHEMA IF NOT EXISTS `{results_catalog}`.`{results_schema}`
COMMENT 'Esquema para el demo de generación automática de comentarios usando IA'
""")

print(f"Esquema `{results_catalog}`.`{results_schema}` OK")

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS `{results_catalog}`.`{results_schema}`.`ejecuciones` (
  id_ejecucion    STRING        NOT NULL
                                COMMENT 'GUID de la ejecución — generado con uuid() al insertar',
  fecha_ejecucion TIMESTAMP     COMMENT 'Fecha y hora de inicio de la ejecución',
  estado          VARCHAR(50)   COMMENT 'Estado: INICIADO, EN_PROCESO, COMPLETADO, ERROR',
  resultado       VARCHAR(4000) COMMENT 'Detalle del resultado final de la ejecución',
  CONSTRAINT pk_ejecuciones PRIMARY KEY (id_ejecucion)
)
COMMENT 'Registro de cada ejecución del proceso de generación de comentarios'
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
""")

print(f"Tabla `{results_catalog}`.`{results_schema}`.`ejecuciones` OK")

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS `{results_catalog}`.`{results_schema}`.`resultados` (
  id_resultado    BIGINT        GENERATED ALWAYS AS IDENTITY
                                COMMENT 'Identificador autoincrementable del resultado',
  id_ejecucion    STRING        NOT NULL
                                COMMENT 'Referencia a la ejecución que generó este resultado',
  fecha_resultado TIMESTAMP     COMMENT 'Fecha y hora en que se generó el comentario',
  nombre_esquema  VARCHAR(255)  COMMENT 'Nombre del esquema al que pertenece la tabla',
  nombre_tabla    VARCHAR(255)  COMMENT 'Nombre de la tabla procesada',
  nombre_columna  VARCHAR(255)  COMMENT 'Nombre de la columna para la que se generó el comentario',
  comentario      VARCHAR(4000) COMMENT 'Comentario generado por IA para la columna',
  CONSTRAINT pk_resultados PRIMARY KEY (id_resultado),
  CONSTRAINT fk_ejecucion  FOREIGN KEY (id_ejecucion)
    REFERENCES `{results_catalog}`.`{results_schema}`.`ejecuciones` (id_ejecucion)
)
COMMENT 'Comentarios generados por IA por columna de cada tabla procesada'
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
""")

print(f"Tabla `{results_catalog}`.`{results_schema}`.`resultados` OK")

# COMMAND ----------

print("Setup completado exitosamente.")
