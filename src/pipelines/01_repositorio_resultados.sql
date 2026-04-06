-- ============================================================
-- Repositorio de resultados: Generación de comentarios con IA
-- Catálogo: main_eduardo_sojo
-- Esquema:  ai_comments_generation
-- Nota: id_ejecucion es un GUID que debe generarse con uuid()
--       al momento del INSERT desde el proceso llamador.
-- ============================================================

-- Crear esquema si no existe
CREATE SCHEMA IF NOT EXISTS main_eduardo_sojo.ai_comments_generation
COMMENT 'Esquema para el demo de generación automática de comentarios usando IA';

-- ------------------------------------------------------------
-- Tabla: ejecuciones
-- Registra cada ejecución del proceso de generación de comentarios
-- id_ejecucion: GUID generado con uuid() en el INSERT
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS main_eduardo_sojo.ai_comments_generation.ejecuciones (
  id_ejecucion    STRING        NOT NULL
                                COMMENT 'GUID de la ejecución — generado con uuid() al insertar',
  fecha_ejecucion TIMESTAMP     COMMENT 'Fecha y hora de inicio de la ejecución',
  estado          VARCHAR(50)   COMMENT 'Estado: INICIADO, EN_PROCESO, COMPLETADO, ERROR',
  resultado       VARCHAR(4000) COMMENT 'Detalle del resultado final de la ejecución',
  CONSTRAINT pk_ejecuciones PRIMARY KEY (id_ejecucion)
)
COMMENT 'Registro de cada ejecución del proceso de generación de comentarios'
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

-- ------------------------------------------------------------
-- Tabla: resultados
-- Almacena los comentarios generados por columna de cada tabla
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS main_eduardo_sojo.ai_comments_generation.resultados (
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
    REFERENCES main_eduardo_sojo.ai_comments_generation.ejecuciones (id_ejecucion)
)
COMMENT 'Comentarios generados por IA por columna de cada tabla procesada'
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');
