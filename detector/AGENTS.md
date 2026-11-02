# Reglas para trabajar en `detector/`

Servicio que detecta prácticas duplicadas en rendiciones médicas. El flujo y las decisiones están en el `README.md` de la raíz. Este archivo dice qué no se puede romper al tocar el código.

## Mapa

Un bloque del flujo = un módulo. Lo que es de un bloque se queda en su módulo.

| Módulo | Bloque |
|---|---|
| `extractor.py` · `parser.py` | 1 · splitter, Document AI con caché, parser de la planilla |
| `preprocesado.py` | 2 · limpieza, agrupaciones, tabla de frecuencias |
| `reglas.py` | 3 · clasificador determinístico (el principal) |
| `llm.py` · `skills/duplicados.md` | 3b · clasificador alternativo con LLM, apagado por defecto |
| `cruzador.py` | 4 · conceptual |
| `trazador.py` | 5 · PDF marcado |
| `db.py` · `metricas.py` · `config.py` | persistencia, métricas, configuración |
| `pipeline.py` · `__main__.py` · `evals.py` | orquestación, CLI, evaluador |

Los notebooks de `notebooks/` son exploración y prototipo. La fuente de verdad es este paquete.

## Invariantes

- **Nada de datos en el repo.** Ni PDFs, ni CSV, ni SQLite, ni cachés, ni salidas de notebooks. Los datos van en `data/` y las salidas en `salida/`, las dos fuera de git. Los notebooks se guardan sin salidas.
- **Nada de datos reales de GCP en el código.** Endpoint, proyecto y credenciales salen de `.env` o del entorno, a través de `config.py`. Un valor nuevo de configuración se agrega también a `.env.example`, con un marcador.
- **Idempotencia.** Correr dos veces sobre el mismo lote da el mismo `hallazgos.csv`.
  - Los ids se derivan del contenido: `fila_id` del sha del PDF + página + renglón, y `hallazgo_id` de la versión de la regla + tipo + filas. Nada de ids aleatorios ni por fecha.
  - Los hallazgos se guardan con `INSERT OR IGNORE`.
  - Toda llamada paga va con caché, y la clave de la caché incluye todo lo que cambia la respuesta (PDF y páginas, endpoint, modelo, prompt, skill, payload).
- **Costo fiel.** Todo bloque nuevo se mide con `Metricas.medir()`. El costo es el que se facturó: lo que viene de caché cuenta USD 0.
- **Trazabilidad.** Un hallazgo siempre lleva sus `fila_ids`, un `motivo` con los números que lo dispararon y su `regla_version`.
- **Importes con `Decimal`**, nunca `float`. En SQLite se guardan como texto.
- **El LLM nunca arma hallazgos.** Decide sobre los grupos que arma el código, cita filas, y el guardrail rechaza lo que no existe. Si el guardrail rechaza un grupo, decide la regla.
- **Cada corrida escribe en su carpeta** (`salida/runs/<fecha-hora>/`). La base y la caché son compartidas entre corridas.

## Antes de cambiar una regla o un umbral

- Si cambia la lógica de una regla, subí la versión en `Config.reglas_version` (`reglas-v1` → `reglas-v2`). Así los hallazgos nuevos no pisan a los anteriores. El umbral ya forma parte de la versión.
- La versión de la skill del LLM se calcula sola a partir del texto de `skills/duplicados.md`.
- El conocimiento sobre prácticas está repartido en tres lugares: `FAMILIAS` y `REPETIBLES` en `preprocesado.py`, y la skill. Si cambia uno, revisá los otros dos.

## Cómo verificar un cambio

No hay tests automáticos. Se verifica corriendo el servicio, sin GCP:

```powershell
python -m detector "data/pdfs/detalle_*_sin_afiliado.pdf" --extraccion "data/extraccion/*.csv"
python -m detector "data/pdfs/detalle_*_sin_afiliado.pdf" --extraccion "data/extraccion/*.csv"
python -m detector comparar
python -m detector evals
```

Tiene que dar: control de totales en $0,00, ✅ en `comparar` y las reglas sin falsos positivos ni negativos contra el golden. No corras nada que llame a Document AI o a Gemini sin avisar: tiene costo.

## Estilo

- Español rioplatense, con voseo, en código, comentarios, mensajes y documentación.
- Comentarios cortos que explican el porqué, no el qué. Docstrings de una o dos líneas.
- Mensajes del CLI claros: si falta configuración, decir qué variable falta. Nada de tracebacks para errores esperables.
