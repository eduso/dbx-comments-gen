---
type: knowledge
customer: bcp
topic: model-evaluation
source: internal
tags: [knowledge, bcp, foundation-models, evaluation, reliability, mlflow, agent-evaluation, comments-generator]
created: 2026-06-03
updated: 2026-06-03
---

# Cómo medir la fiabilidad de un modelo fundacional para el generador de comentarios

**Nota de campo — Databricks | Junio 2026**

## Contexto

El generador de comentarios de negocio sobre Unity Catalog se está expandiendo. El modelo fundacional (LLM) es una decisión del banco: van a probar varios candidatos y necesitan una forma objetiva de medir la fiabilidad de cada uno **frente al trabajo concreto que hará**, para comparar y elegir con criterio.

Esta nota propone el enfoque antes de construir nada. El principio es simple: no se elige el modelo por intuición ni por una corrida, sino por un **bake-off reproducible** que produce un tablero comparable.

## Principio rector: comparación offline, no monitoreo

Elegir el modelo y vigilarlo en producción son dos cosas distintas. Hoy estamos en la primera.

- **Selección (esta nota):** evaluación batch offline. Todos los candidatos corren sobre el **mismo set de prueba fijo**, con el **mismo prompt y contexto**. Produce un scorecard comparable.
- **Producción (fase posterior):** una vez elegido el modelo, se vigila con tablas de inferencia, Lakehouse Monitoring y detección de *drift*. Reusa los mismos criterios de esta fase.

## Qué significa "fiabilidad" para este trabajo

Traducimos el concepto abstracto a criterios medibles. Para generar metadata de negocio en un banco, el orden de criticidad es:

| Dimensión | Qué mide | Cómo se mide |
|---|---|---|
| **Grounding / sin alucinación** | El comentario refleja solo lo que el contexto soporta | Juez LLM contra el contexto fuente (criterio existente `FUERA_DE_CONTEXTO`) |
| **Terminología correcta** | Usa términos canónicos del banco | Juez LLM (`TERMINOLOGIA_INCORRECTA`) |
| **Granularidad** | El alcance del comentario es el correcto (schema/tabla/columna) | Juez LLM (`GRANULARIDAD`) |
| **Completitud** | No omite información clave | Juez LLM (`INFORMACION_FALTANTE`) |
| **Idioma / formato** | Español, dentro del límite de longitud, sin PII | Reglas determinísticas (regex, conteo) |
| **Consistencia** | Estabilidad entre corridas del mismo input | Varianza de N corridas a `temperature=0` |

El generador **ya incluye un auditor** (`04_audit_comments.py`) que evalúa los primeros cuatro criterios contra el contexto. No partimos de cero: ese auditor es la base del juez para comparar modelos.

## Cómo lograr una comparación justa

Estas son las barandas que hacen válido el resultado:

1. **Juez neutral y fijo.** Usar un modelo juez fuerte que **no sea ninguno de los candidatos**, para evitar que un modelo se favorezca a sí mismo. El mismo juez para todos.
2. **Fijar todo excepto el modelo.** Mismo prompt, mismo contexto, misma temperatura (0 para configuración productiva), mismo set de prueba. Solo cambia el modelo.
3. **Muestra suficiente y estratificada.** No se decide con 10 ejemplos. El set debe representar la diversidad real del estate.
4. **Calibración humana.** Antes de confiar en el juez automático a escala, un experto del banco revisa una muestra y medimos el acuerdo juez ↔ humano.

> Caveat honesto: el mejor *prompt* puede diferir por modelo. Recomendamos dos fases — Fase A con un prompt único (comparación justa) y, opcionalmente, Fase B de ajuste ligero de prompt solo para los 2-3 finalistas.

## La inversión clave: el golden set

Un conjunto curado de ~80–150 tablas/columnas representativas del banco, variando:
- Dominios (riesgos, finanzas, clientes).
- Convenciones de nombres (crípticas vs descriptivas).
- Datos sensibles vs no sensibles.
- Tablas bien documentadas vs sin documentación.

Donde un experto ya escribió el comentario "correcto", se usa como referencia. Donde no, el juez evalúa contra el contexto fuente (no requiere referencia). Este set es la pieza de mayor impacto: sin él, la comparación es subjetiva.

## El tablero de decisión

El "mejor" modelo es un trade-off entre calidad, costo y velocidad — no calidad sola. Propuesta de scorecard ponderado; **los pesos los define el banco**:

| Dimensión | Peso sugerido |
|---|---|
| Grounding / sin alucinación | 35% |
| Completitud + terminología | 25% |
| Consistencia | 15% |
| Costo por 1.000 comentarios | 15% |
| Latencia (p50/p95) | 10% |

Esto vuelve la decisión defendible y trazable.

## El flujo del harness

```
golden set fijo
   → generar comentarios con cada modelo candidato (vía AI Gateway = cambiar modelo es config)
   → evaluar: auditor LLM (grounding/terminología/...) + reglas determinísticas (idioma/formato/PII)
   → N corridas para medir consistencia
   → registrar cada modelo como un run en MLflow
   → capturar latencia + costo por modelo
   → scorecard ponderado + comparación lado a lado en el Evaluation UI de MLflow
```

Herramientas Databricks: **MLflow 3 + Mosaic AI Agent Evaluation** (jueces LLM nativos + Evaluation UI), **AI Gateway** (intercambio de modelos por configuración, captura de costo/latencia) y **Foundation Model API**.

## Próximos pasos

1. Validar pesos del scorecard y tamaño del golden set con el equipo del banco.
2. Definir la lista de modelos candidatos a comparar.
3. Construir el golden set estratificado (mayor esfuerzo humano, mayor retorno).
4. Ejecutar el harness (notebook adjunto) y revisar el tablero comparativo.
5. Elegir el modelo; pasar a la fase de monitoreo en producción.
