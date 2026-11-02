# Detector de prácticas duplicadas

Challenge técnico · AI Engineer. Un servicio que extrae los ítems facturados de los PDFs de un lote de rendiciones y devuelve **hallazgos potenciales** de prácticas duplicadas, con trazabilidad hasta la fila del PDF original.

Este trabajo no busca ser perfecto ni resolver el problema. Resolverlo requiere entrar en la lógica de negocio, trabajar con datos reales, reunirse con el cliente e iterar varias veces. Es un **puntapié inicial**: un prototipo pensado para iterarse y mejorarse, que marca una intención y una dirección.

## Resultado sobre el lote

| | |
|---|---|
| Lote | 3 PDFs escaneados · 146 páginas · 2.169 filas |
| Extracción | la suma de las filas coincide al centavo con el TOTAL impreso en los 3 PDFs |
| Hallazgos | 65 `DUPLICADO_POTENCIAL` (148 filas, $10.641.226,02 en riesgo) · 30 `REDONDEO` (114 filas, $2,07) · 0 `NRO_INT_REPETIDO` |
| Costo | USD 0,219 (todo es OCR: 146 páginas × USD 0,0015) · la segunda corrida cuesta USD 0 |
| Latencia | desde Colab (dentro de GCP): 54,6 s de punta a punta, 0,36 s por página de OCR · desde una PC en Argentina: ~205 s, ~1,4 s por página, por la distancia al endpoint de Document AI en EE. UU. · el resto del flujo tarda ~1,5 s |

```
PDFs ─► Extractor ─► Pre-procesado ─► Clasificador ─► Cruzador ─► Trazador
        splitter      limpieza          reglas          (conceptual)  PDF marcado
        Document AI   agrupaciones      LLM + skill
        parser        frecuencias       (alternativa,
                      SQLite             apagada)
```

---

## 1. Cómo correrlo

Comandos para PowerShell, desde la carpeta del proyecto. En Linux/macOS son equivalentes.

**Instalación (una sola vez):**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Los datos no están en el repo**: ni los PDFs, ni la extracción, ni el golden, ni las salidas. Copialos a `data/` (detalle en [`data/README.md`](data/README.md)):
- `data/pdfs/`: los PDFs del lote. Siempre hacen falta.
- `data/extraccion/`: los CSV de una extracción ya hecha. Solo para correr sin GCP (`--extraccion`).
- `data/golden_duplicados.csv`: el golden dataset. Solo para `python -m detector evals`.

**Configuración de GCP** (solo si se va a llamar a Document AI). Copiá `.env.example` a `.env` y completalo:
- `DOCAI_ENDPOINT`: el endpoint del procesador.
- `GCP_PROJECT`: el proyecto (solo para `--llm`).
- Credenciales: `GOOGLE_APPLICATION_CREDENTIALS` con una service account, o `gcloud auth application-default login`.

`.env` no se sube a git, y el código no trae endpoints ni proyectos reales.

**Correr el pipeline:**

```powershell
python -m detector "data/pdfs/detalle_*_sin_afiliado.pdf"
```

Sin GCP, desde la extracción ya guardada (no llama a ninguna API; el resto del flujo es idéntico):

```powershell
python -m detector "data/pdfs/detalle_*_sin_afiliado.pdf" --extraccion "data/extraccion/*.csv"
```

| Opción | Qué hace |
|---|---|
| `--salida DIR` | carpeta de salida (por defecto, `salida/`) |
| `--extraccion CSV…` | usa una extracción guardada: los CSV de `data/extraccion/` o el `extraccion.csv` de una corrida anterior |
| `--llm` | usa el clasificador alternativo (LLM + skill) para los duplicados |
| `--umbral 0.05` | P(≥2 el mismo día) máxima para considerar rara una práctica |

El comando imprime un reporte (lote, control de totales, hallazgos, métricas por bloque). Cada corrida guarda sus salidas en su propia carpeta:

```
salida/
├── detector.sqlite          compartida: filas, hallazgos, frecuencias, métricas de todas las corridas
├── cache/                   compartida: respuestas de Document AI y Gemini
├── runs/
│   └── 20261101-233927/     una carpeta por corrida
│       ├── hallazgos.csv            una línea por hallazgo
│       ├── hallazgos_trazados.pdf   las páginas con hallazgos, con las filas marcadas
│       ├── metricas.csv             latencia, páginas, tokens y costo por bloque
│       ├── extraccion.csv           las filas tal como se leyeron, con su caja
│       └── resumen.json             conteos, costo, hallazgos nuevos en la base y huellas (sha256) de los CSV
└── evals/
    └── 20261102-101500/     una carpeta por evaluación (ver Evals)
```

**Comprobar la idempotencia: correrlo dos veces.** Arrancá limpio (`Remove-Item -Recurse -Force .\salida`) y corré el mismo comando dos veces seguidas. Al final, el reporte de la segunda corrida se compara solo contra la anterior:

```
== Contra la corrida anterior (20261101-233927)
✅ Mismos hallazgos: huella de hallazgos.csv igual · hallazgos 95 → 95 · nuevos en la base: 0 · costo USD 0.219 → 0.0
   extraccion.csv: idéntica
```

- **Misma huella de `hallazgos.csv`:** el resultado es idéntico byte a byte.
- **0 hallazgos nuevos en la base:** no se duplicó nada.
- **Costo USD 0:** la extracción salió de la caché.

Si se borra la caché entre corridas, Document AI vuelve a leer las páginas, y puede devolver diferencias mínimas en los decimales de confianza o de las cajas. En ese caso `extraccion.csv` cambia ("con diferencias"), pero `hallazgos.csv` sigue siendo idéntico: la idempotencia está garantizada sobre los hallazgos, que es lo que se le entrega al auditor.

**Verificar con SHA-256 que todas las corridas dieron lo mismo:**

```powershell
python -m detector comparar
```

Calcula el SHA-256 de `hallazgos.csv` en cada corrida de `salida/runs/` y dice si son todos iguales:

```
== SHA-256 de hallazgos.csv · 2 corridas en salida\runs
20261102-003439      8fa444fcd7cb90020edfc02d372c319d3e83a96bc2adab8463d4391bcde0390c
20261102-003445      8fa444fcd7cb90020edfc02d372c319d3e83a96bc2adab8463d4391bcde0390c

✅ Todas iguales: las 2 corridas dieron exactamente el mismo hallazgos.csv
```

Si alguna corrida difiere, lista qué corridas dieron cada resultado y termina con código de salida 1, así que sirve también en un script.

Para hacerlo a mano, sin el comando:

```powershell
Get-FileHash salida\runs\*\hallazgos.csv -Algorithm SHA256 | Select-Object Hash, Path
```

`metricas.csv` sí cambia entre corridas, porque registra la latencia de cada una. El PDF tampoco es idéntico byte a byte, porque PyMuPDF guarda la fecha en que lo genera, pero sus marcas son las mismas.

**Evaluar contra el golden:**

```powershell
python -m detector evals
python -m detector evals --llm
```

El primero es gratis. El segundo también evalúa el clasificador con LLM, y tiene costo. Ver [Evals](#evals).

---

## 2. Extractor

El lote son PDFs escaneados, sin capa de texto: cada página es una imagen. Se leen con **Document AI (Enterprise Document OCR)** y un **parser propio**, que ubica la fila de títulos y corta las columnas según la posición de cada título.

**Por qué Document AI.** El trade-off entre costo, latencia y robustez, y la comparación con dos alternativas que consideré (**Tesseract** en Cloud Run y **GLM-OCR** en Cloud Run con GPU), está en una página aparte:

- [**Elección del OCR**](https://claude.ai/artifact/5ghsTEQmWSjiqMBiUxuzwZ) (si no se puede abrir el link, está en el repo: [`docs/eleccion_ocr.html`](docs/eleccion_ocr.html))

En resumen:
- **Es la única opción medida sobre el lote:** 0,36 s por página desde Colab (~1,4 s desde una PC en Argentina, por la red), USD 0,0015 por página, los mismos hallazgos en todas las corridas, y la suma de las 2.169 filas coincide al centavo con el TOTAL impreso.
- **Devuelve la caja de cada palabra.** De eso dependen el parser y el trazador.
- **Las alternativas podrían ser más baratas** con 1.000.000 de páginas por día: Tesseract ≈ USD 900–3.300/mes, GLM-OCR ≈ USD 4.800–19.400/mes, contra ≈ USD 22.500/mes de Document AI. Pero son estimaciones, y les falta una evaluación real de calidad sobre estas planillas. El checksum contra el TOTAL impreso hace barata esa evaluación.

**Minimizar el costo cuando el PDF trae texto.** El extractor empieza con un **splitter** que mira, página por página, si hay capa de texto o solo imagen. La intención es mandar a OCR solo las páginas que lo necesitan. Si el PDF trae texto, o si la rendición llega como Excel, alcanza con un extractor interno, sin costo de OCR. Esa rama no está implementada por tiempo (en este lote las 146 páginas son imagen), pero el splitter funciona y la separación ya está hecha. Además, la caché por hash evita volver a pagar un lote que ya se procesó.

**Control de la extracción.** El parser depende del formato de la planilla, así que hay un control gratis que dice si leyó bien: **la suma de las filas tiene que coincidir con el TOTAL impreso.** En el lote da $0,00 de diferencia en los 3 PDFs.

---

## 3. Exploración de datos

Antes de definir el criterio exploré las 2.169 filas extraídas (notebook `00_exploracion_extraccion`). Lo que salió:

- **El Nro. Int. es un id de fila.** No se repite nunca: buscar duplicados por Nro. Int. da 0 resultados.
- **El criterio ingenuo explota.** "Misma práctica, mismo día" da 426 grupos, con 1.050 filas "de más". Con eso no se puede trabajar.
- **El importe no discrimina.** Es el arancel de la práctica, igual para todos los pacientes. Que dos filas tengan el mismo importe no indica nada.
- **Pocas prácticas concentran el volumen.** Consulta médica (369 filas), "en consultorio externo, cada" (196), fisioterapia (184) y consulta pediátrica (169). Que una consulta aparezca varias veces en un día es lo normal.
- **Los medicamentos e insumos** (códigos terminados en `X1`) se consolidan con la cantidad y se dan a muchos pacientes. Repetidos en un día no son sospechosos.
- **Hay prácticas que se explican solas.** Su descripción ya dice que se facturan por unidad: "CADA", "por exposición subsiguiente", "segunda exposición", "por región".
- **La misma práctica aparece escrita de formas distintas.** `CONSULTA MEDICA` / `CONSULTA GINECOLOGICA` con el mismo código, `TOMOGRAFIA` / `TOOMOGRAFIA`, espacios distintos, letras griegas que el OCR confunde con latinas. Se normaliza a la descripción más frecuente de cada código.
- **El mismo estudio aparece con códigos distintos**, de códigos diferentes:
  - eco mamaria: `180106` y `861220`;
  - eco transvaginal: `180161` y `861219`;
  - urotomografía: `341018` y `341177`;
  - TAC de otros órganos: 4 códigos.

  Se agrupan en un **grupo** o familia y se conserva el código original de cada fila.
- **Dentro de cada fecha, las filas parecen agrupadas por atención.** Por ejemplo, la eco de vejiga (`180114`) suele facturarse junto con una eco renal (`180116`), y hay días con más vejigas que renales. Por eso se marca si las filas de un grupo están una al lado de la otra (`contiguas`): es lo más cercano al paciente que tenemos.
- **Problemas de extracción que se corrigen en el pre-procesado:**
  - 45 prestaciones que el parser corrió a la columna "Apellido y Nombre" (tapada, así que si trae un código es la prestación);
  - renglones de ruido;
  - la fila de TOTAL.
- **Las 114 filas donde Der. + Hon. ≠ Total** tienen diferencias de $0,10 o menos, y están en el propio documento: no son errores de OCR. Es redondeo del prestador, y se repite en cada fila que usa el mismo arancel.

Por eso el criterio deja afuera **los medicamentos, las prácticas repetibles por su propia descripción y las prácticas demasiado frecuentes**.

---

## 4. Criterio de duplicación

**Lo más importante: no tenemos el dato del paciente.** El afiliado y el nombre están tapados. Sin paciente, el agrupamiento es muy limitado: dos filas iguales el mismo día pueden ser la misma práctica facturada dos veces, o dos pacientes distintos atendidos ese día.

**Tampoco tenemos la documentación respaldatoria** (órdenes, informes, facturas) para confirmar la sospecha.

Por eso **solo podemos ofrecer hallazgos potenciales, no hallazgos confirmados.** Cada uno lo tiene que confirmar un auditor. El objetivo es que el auditor revise lo que vale la pena revisar, ordenado por monto, y con la evidencia a la vista.

**El criterio:**
- **Nro. Int. repetido → hallazgo automático.** Es el id de la fila. Repetido, sea dentro de un PDF o entre rendiciones, es la misma atención facturada dos veces. En este lote no hay ninguno.
- **Una práctica poco frecuente que aparece varias veces el mismo día → hallazgo potencial.** Una consulta repetida es normal. Una ecografía de tiroides repetida el mismo día es rara.
- **Además, `REDONDEO`:** Der. + Hon. ≠ Total en la suma horizontal de una fila.

| Tipo | Agrupa por | Monto en riesgo |
|---|---|---|
| `NRO_INT_REPETIDO` | Nro. Int. en todo el lote | suma − máximo |
| `DUPLICADO_POTENCIAL` | día + grupo, 2+ filas, práctica poco frecuente y no repetible | suma − máximo |
| `REDONDEO` | Der. + Hon. ≠ Total, agrupado por arancel (código + Der. + Hon. + Total) | lo facturado de más × veces |

**La tabla de frecuencias: qué es "poco frecuente".** Para cada grupo se calcula cuántas veces aparece en promedio por día:

- λ = filas de la grupo / días con actividad en el lote.
- Con Poisson, la probabilidad de verla 2 o más veces el mismo día es P(≥2) = 1 − e^(−λ)·(1 + λ).
- Es candidata si P(≥2) < 5 % y no es repetible (medicamento `X1`, o descripción con "CADA", "subsiguiente", "segunda", "por región").

| Grupo | λ (por día) | P(≥2 el mismo día) | ¿Candidata? |
|---|---|---|---|
| Consulta médica `420101` | 3,73 | 88,6 % | no: es lo normal |
| Eco de vejiga `180114` | 0,09 | 0,4 % | sí |

Se divide por los días **del lote**, no por los días en que aparece la práctica. Con la segunda opción, los propios duplicados inflan la frecuencia: la eco de vejiga de la p.5, el caso más claro del lote, quedaba afuera.

**Cómo se guarda.** Cada corrida escribe la tabla en la tabla `frecuencias` de `salida/detector.sqlite` (grupo, práctica, renglones, días con la práctica, λ, P(≥2), repetible, candidata), y el `motivo` de cada hallazgo trae los números que lo dispararon. Así se puede reconstruir por qué un grupo fue sospechoso. En la misma base quedan las filas extraídas con su caja (`filas`), los hallazgos (`hallazgos`), qué filas forman cada uno (`hallazgo_filas`) y las métricas de cada corrida (`metricas`).

**Otras decisiones:**
- **Un hallazgo es un grupo de filas, no un par.** Tres filas iguales son un solo hallazgo con `veces = 3`.
- **Monto en riesgo = suma − máximo.** Se asume legítima la fila más cara: el débito queda más chico, pero es más difícil de impugnar.
- **`contiguas`** no filtra. Queda como columna para que el auditor priorice.
- **La salida para el auditor** separa por tipo, agrupa por día + grupo y ordena por monto en riesgo.

---

## 5. Clasificador

**El clasificador es determinístico: reglas en código.** La regla es simple (Nro. Int. repetido, o práctica poco frecuente y no repetible repetida el mismo día), y un `if` la resuelve en milisegundos, gratis, siempre igual y explicable. Un LLM no se justifica: agregaría costo, latencia y variabilidad para llegar al mismo resultado.

**Cuándo sí convendría un LLM.** Cuando el criterio deja de ser una regla y pasa a necesitar interpretación o conocimiento experto. Hay que distinguir dos tipos de casos:

- **Hallazgos evidentes:** el mismo Nro. Int. dos veces, o la misma resonancia con la misma orden. No hace falta interpretar nada: los resuelve una regla.
- **Hallazgos dudosos:** el mismo paciente con dos radiografías de tórax el mismo día. Puede haber tres explicaciones:
  - dos estudios distintos, por ejemplo una Rx antes y otra de control después de colocar un catéter central o un drenaje (legítimo, se verifica en la historia clínica);
  - un solo estudio con dos incidencias (frente y perfil) facturado como dos primeras exposiciones, cuando la segunda debía ir como "exposición subsiguiente", que es más barata (débito parcial por la diferencia);
  - la misma práctica facturada dos veces (débito total).

  Distinguirlas requiere mirar el contexto de la atención (servicio, guardia o internación, qué otras prácticas se hicieron) y aplicar criterio médico. Ahí un LLM con una skill de conocimiento experto puede proponer una explicación y un motivo, que después confirma el auditor.

**El clasificador con LLM está como ejemplo, apagado** (`--llm`). Muestra cómo se escalaría si hicieran falta más criterios:
- El código arma los grupos candidatos, y el LLM decide cada uno con una skill (`detector/skills/duplicados.md`) que se puede editar sin tocar código. La versión de la skill cambia sola cuando se edita.
- El LLM tiene que citar las filas (archivo, página, Nro. Int.). Un **guardrail** rechaza las citas que no existen o que no son del grupo, y en ese caso el grupo lo decide la regla.
- Los hallazgos los arma el código, con el mismo esquema que las reglas, y la respuesta queda en caché.

Hoy la skill replica la regla del clasificador determinístico. Así el evaluador puede comprobar que el LLM sigue instrucciones y cita bien, pero no le aporta criterio propio. Por eso no se usa en la corrida principal.

---

## 6. Cruzador

**Por ahora es conceptual** (`detector/cruzador.py` deja pasar todos los hallazgos).

La idea: tomar cada hallazgo potencial e intentar **confirmarlo o descartarlo con la documentación respaldatoria de esa rendición**, como facturas, órdenes médicas, recetas, informes o autorizaciones. Por ejemplo, si hay dos órdenes distintas para las dos radiografías, el hallazgo se descarta. Si hay una sola orden, se refuerza.

Acá sí tiene sentido un LLM: hay que leer documentos heterogéneos y relacionarlos con las filas. Según cómo el cliente arme y entregue la rendición, quizás convenga algo más agéntico, que busque los documentos relacionados en sus sistemas. Pero primero hay que ver **qué datos tienen y qué podemos usar de ellos**: sin eso, diseñar el cruzador sería adivinar. Las mismas piezas del clasificador servirían acá: citas a documentos reales, un guardrail que las verifique y evaluación contra casos conocidos.

---

## 7. Trazador

La intención es que el resultado del proceso tenga un **alcance visual**: que el auditor vea dónde está cada hallazgo en el documento original, sin buscarlo a mano.

El trazador genera `hallazgos_trazados.pdf` con solo las páginas que tienen hallazgos (78 en el lote), y marca cada fila con una caja:
- **rojo:** duplicado potencial;
- **celeste:** redondeo en la suma horizontal;
- **naranja:** las dos cosas.

En el lote son 126 filas rojas, 92 celestes y 22 naranjas.

- **Las marcas son anotaciones del PDF:** la imagen original no se modifica, que importa si el documento respalda un débito.
- **Al pasar el mouse por una caja se ve el motivo** de cada hallazgo de esa fila, y cada página indica de qué archivo y qué página original sale.
- **Lo hace PyMuPDF, sin LLM**, con las cajas que dio la extracción. Es exacto, gratis e idempotente.

---

## 8. Un prototipo de producto

Esto sirve como prototipo de producto para un caso: rendiciones ambulatorias con este formato de planilla. Antes de llevarlo más lejos, hay que **investigar y evaluar cómo escala a otros escenarios**.

**Otros tipos de rendición** (medicamentos, internaciones, atención de especialistas, etc.). El esquema del hallazgo, la trazabilidad, la caché y las métricas ya sirven. Lo que cambia es la regla, y cada escenario tiene la suya.

La forma de escalar es armar esas reglas con pocas primitivas con parámetros: agrupar por clave + ventana, tope por período, incompatibilidad, intervalos que se superponen y aritmética. Los parámetros saldrían de **un conocimiento estructurado sobre las prácticas**. Hoy ese conocimiento está repartido entre `GRUPOS`, `REPETIBLES` y la skill.

**Otros formatos.** Una cascada que va de lo más barato a lo más caro:
1. estructurado (Excel, XML), con un extractor interno sin OCR;
2. PDF con capa de texto, cuyas palabras con su caja van al mismo parser;
3. PDF imagen con un formato conocido: OCR + parser;
4. formato desconocido: extracción con un LLM de visión, o un template generado una sola vez y revisado por una persona.

**Si tuviera un dato que asocie la práctica con el paciente**, el criterio cambiaría por completo. El modo estadístico de este prototipo es un modo degradado por falta de ese dato. Con paciente se podría:
- **agrupar por paciente + práctica + ventana:** la misma práctica dos veces el mismo día al mismo paciente es un hallazgo de alta confianza, sin estadística;
- **reconstruir la atención completa** (todas las prácticas de un paciente en un día o ventana) y buscar incompatibilidades e incoherencias. Si tuviéramos acceso a la facturación, poder controlar que sea coherente con los estudios realizados también sería un vector importante.
- **controlar la coherencia clínica:** prácticas que no corresponden al sexo o la edad, o frecuencias por paciente que no siguen ningún protocolo;
- **aplicar un LLM sobre la atención completa** para establecer criterios nuevos: como en el ejemplo de las dos radiografías, evaluar si una secuencia de prácticas tiene sentido clínico, y explicar por qué.

**Producto:**
- **El umbral lo elige el cliente.** Se le muestra la curva de hallazgos contra monto en riesgo.
- **La decisión del auditor es el activo principal.** Con aprobado/rechazado y el motivo de rechazo se obtiene la precisión real de cada regla y una confianza aprendida, y las reglas nuevas pueden correr en modo sombra.
- **Métrica principal:** tasa de hallazgos confirmados.

---

## Requisitos del challenge

### Idempotencia
- **Caché de extracción.** Cada tramo de Document AI se guarda con la clave `hash(sha256 del PDF, páginas, endpoint, field mask)`. Si el tramo ya está, no se llama a la API.
- **Caché del LLM.** La respuesta se guarda con la clave `hash(modelo, instrucciones + skill, candidatos)`, con `temperature = 0`.
- **Ids derivados del contenido.**
  - `fila_id = sha del PDF + página + renglón`.
  - `hallazgo_id = hash(versión de la regla, tipo, fila_ids ordenados)`.
- **Persistencia.** `INSERT OR IGNORE` en SQLite: un hallazgo existente no se duplica ni se pisa.
- **Verificación.** Correr el comando dos veces da la misma huella de `hallazgos.csv`, 0 hallazgos nuevos en la base y USD 0 en la segunda corrida; `python -m detector comparar` lo comprueba con SHA-256. Ver [Cómo correrlo](#1-cómo-correrlo).
- **Si cambia una regla o un umbral**, cambia `regla_version` y se generan hallazgos nuevos sin pisar los anteriores.

### Costo
Cada bloque registra latencia, páginas, tokens de entrada y salida (incluido el razonamiento), costo y aciertos de caché. El costo es el que **efectivamente se facturó** en la corrida:
- **OCR:** páginas enviadas × precio.
- **LLM:** `usage_metadata` de la respuesta × precio.

Lo que viene de caché cuenta USD 0. Las métricas quedan en `metricas.csv` de cada corrida, y la tabla `metricas` de la base guarda el historial de todas las corridas.

### Trazabilidad
Desde un hallazgo se reconstruye:
- **Qué filas lo forman** (`hallazgo_filas`) y, de cada una, archivo, página, renglón y caja en la página (`filas`).
- **Qué regla lo generó, con qué versión y umbral** (`regla_version`), y con qué números: el `motivo` trae λ y P(≥2), y la tabla de frecuencias queda en `frecuencias`.
- **Qué texto devolvió el OCR**, en el JSON de Document AI guardado en la caché.
- **Dónde está en el documento**, en el PDF trazado.

### Evals

#### El golden dataset: ¿cómo sabemos si esto anda?

Que el pipeline corra y sea idempotente no dice si detecta bien. Para saberlo hace falta un **golden dataset**: casos donde sabemos cuál es la respuesta correcta, contra los que medimos el sistema cada vez que cambia algo (una regla, un umbral, la skill, el modelo, el OCR).

Un golden útil no es una muestra al azar. Se arma con **los casos que le cuestan al flujo**, elegidos entre los casos problemáticos que aparecen en producción por sus características:
- **Duplicados difíciles de ver:** filas que no están una al lado de la otra, el mismo estudio repartido entre dos archivos, el mismo estudio con dos códigos  distintos, importes distintos, tres o más filas.
- **No duplicados que parecen duplicados:** prácticas que se repiten por su propia descripción ("segunda exposición", "por región"), medicamentos que se consolidan con la cantidad, prácticas muy frecuentes o de frecuencia media, y casos justo en el límite del umbral.

Cada caso queda etiquetado con su **estrato** (el tipo de dificultad), así se ve en qué tipo de caso falla el sistema y no solo cuánto falla.

**Ese golden lo construye una persona con conocimiento del dominio.** El espacio para iterar es el **entorno de QA**, y ahí entra el *human in the loop*: alguien que sabe de auditoría médica tiene que poder decidir qué hace mal el sistema. Para eso no alcanza con un PDF marcado. Lo ideal es una **plataforma que le muestre los hallazgos y le permita validarlos**: confirmar o rechazar cada uno, decir por qué, y marcar los duplicados que el sistema no detectó. Cada validación es un caso nuevo para el golden, y cada rechazo con su motivo muestra qué regla ajustar.

El ciclo queda así:
1. El sistema corre sobre lotes reales.
2. QA valida los hallazgos en la plataforma.
3. Los casos validados, sobre todo los errores, entran al golden.
4. Se ajusta el sistema (reglas, umbral, skill).
5. Se vuelve a medir contra el golden antes de pasar a producción.

**El golden de este repo es demostrativo.** `data/golden_duplicados.csv` tiene 32 casos (16 DUPLICADO y 16 NO_DUPLICADO), elegidos por estrato a partir del propio lote. Alcanza para mostrar la intención y para que el evaluador funcione de punta a punta. Pero salió de las propias reglas, así que para ellas funciona como test de regresión, no como medida de calidad. El golden real se arma a medida que el sistema se itera en producción, con las validaciones de QA.

#### El evaluador

**El evaluador del servicio** mide el clasificador contra el golden. No corre el pipeline: carga la extracción ya guardada, la limpia igual que el pipeline y corre el clasificador. No llama a Document AI y no necesita los PDFs.

| Qué se mide | Cómo | Resultado |
|---|---|---|
| Reglas vs. golden | precisión, recall, falsos positivos y negativos, caso por caso | 100 % / 100 % · 0 FP · 0 FN |
| Idempotencia de las reglas | 2 corridas, mismos hallazgos | ✅ |
| LLM vs. golden (`--llm`) | lo mismo, con guardrail; 2 corridas sin caché para ver si el modelo da lo mismo | _correr con `--llm`_ |
| Guardrail | 16 salidas válidas, corrompidas de 7 maneras (sin LLM) | 112/112 corruptas rechazadas · 0/16 válidas rechazadas |

Cada evaluación queda en `salida/evals/<fecha-hora>/`:
- `resumen.json`: métricas, estabilidad, rechazos y costo.
- `golden_resultados.csv`: caso por caso (TP/FP/FN/TN) para las reglas y para cada corrida del LLM, con el motivo del LLM.
- `guardrail.csv`
- `metricas.csv`

**Los bloques que necesitan APIs o el PDF trazado** se evalúan en el notebook `02_evaluacion`:

| Bloque | Qué se mide |
|---|---|
| Extractor | idempotencia: 3 corridas de Document AI y un juez LLM que clasifica las diferencias. Integridad: un juez compara la imagen con las filas extraídas. |
| Trazador | completitud, color de cada caja, geometría e imagen intacta. En local: 240/240 filas con su caja y el color correcto, 0 cajas de más, imagen intacta en las 78 páginas. |

---

## ¿Qué cambiaría con 1.000.000 de páginas por día?

Son ~11,6 páginas por segundo en promedio, unas 30 millones por mes. El resultado se necesita por lote, en horas, no en tiempo real: eso permite procesar en batch. El detalle de costos y escalado del OCR está en [Elección del OCR](https://claude.ai/artifact/5ghsTEQmWSjiqMBiUxuzwZ).

- **OCR con Document AI:** ~USD 22.500 por mes a precio de lista. Son 5M de páginas a USD 1,50 y el resto a USD 0,60 por 1.000.
  - **Escalar es agregar workers propios** que mandan requests en paralelo. Con ~3,3 págs/s por worker (medido desde Colab; los workers correrían en GCP, cerca del endpoint), el promedio pide ~4 workers en paralelo. La cuota online da hasta ~30 págs/s y se puede ampliar.
  - Para volumen sostenido, el modo batch es asíncrono y trabaja sobre Cloud Storage.
- **Bajar el costo:**
  - el extractor interno para páginas con texto y Excel;
  - caché por hash de página, para lotes reenviados;
  - precio por volumen o compromiso;
  - evaluar Tesseract o GLM-OCR con el checksum como test de aceptación, y un esquema híbrido que use Document AI solo cuando el checksum no cuadra.
- **Throughput.** Una cola (Pub/Sub) con workers stateless (Cloud Run), reintentos con backoff.
- **Reglas.** Corren por lote y tardan milisegundos. No son el cuello de botella.
- **Trazador bajo demanda:** el PDF se genera cuando el auditor abre un hallazgo, no para todos.
- **Observabilidad.** Métricas por bloque al monitoreo, y una alerta cuando el checksum de un prestador empieza a fallar.

---

## Estructura del repo

| | |
|---|---|
| `detector/extractor.py` | splitter, Document AI con caché, carga de una extracción guardada |
| `detector/parser.py` | tokens de Document AI → una fila por renglón |
| `detector/preprocesado.py` | limpieza, grupos, tabla de frecuencias |
| `detector/reglas.py` | clasificador determinístico, control de totales, vista para el auditor |
| `detector/llm.py` + `skills/duplicados.md` | clasificador alternativo con LLM, citas y guardrail |
| `detector/cruzador.py` | cruzador (conceptual) |
| `detector/trazador.py` | PDF marcado |
| `detector/db.py` | SQLite: filas, hallazgos, hallazgo_filas, frecuencias, métricas |
| `detector/pipeline.py` · `__main__.py` | orquestación y CLI |
| `detector/evals.py` | evaluador contra el golden (`python -m detector evals`) |
| `detector/config.py` · `.env.example` | configuración; los datos de GCP salen de `.env` |
| `detector/AGENTS.md` · `CLAUDE.md` | reglas para trabajar en el código (invariantes, versionado, cómo verificar); `CLAUDE.md` importa `AGENTS.md` |
| `docs/eleccion_ocr.html` | elección del OCR: trade-off y costos a 1.000.000 de páginas por día |
| `data/` | fuera del repo: ahí van los PDFs, la extracción y el golden (ver `data/README.md`) |

| Notebook (Colab) | Para qué |
|---|---|
| `00_exploracion_extraccion` | exploración: parser de la planilla, medición de Document AI (latencia, costo, determinismo) y análisis inicial de duplicados |
| `01_detector` | el prototipo del que sale el servicio |

Los notebooks van sin salidas, porque las salidas mostraban filas del lote.
| `02_evaluacion` | evals del extractor y del trazador. En `/content` van los CSV de `data/extraccion/`, `data/golden_duplicados.csv`, el PDF de 3 páginas y un `hallazgos_trazados.pdf`; `DOCAI_ENDPOINT` y `GCP_PROJECT` van en los Secretos de Colab. |

## Qué faltó
- Correr `python -m detector evals --llm` con Gemini real, y el notebook `02_evaluacion` (extractor y trazador) en Colab. (el notebook ya está corrido y evaluado en mi entorno, pero no subo los resultados al repo)
- El extractor interno para páginas con capa de texto y Excel (en este lote no hay).
- Una evaluación real de Tesseract y GLM-OCR sobre estas planillas.
- Una API HTTP sobre el pipeline. Hoy es un CLI: `pipeline.correr()` ya es la función que la API expondría.
- La plataforma de QA para que una persona valide los hallazgos y alimente el golden.

## Uso de IA
El desarrollo se hizo con Claude Code como asistente: exploración de datos, código y revisión. Los criterios, las decisiones y los trade-offs son propios, y se defienden en la entrevista.
