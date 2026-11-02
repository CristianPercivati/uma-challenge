# Skill: duplicados potenciales (mismo criterio que el flujo de reglas)

Cada grupo es la misma práctica facturada 2 o más veces el mismo día. No hay datos del paciente.
Tu trabajo es aplicar esta regla, no tu criterio clínico.

## Regla
es_hallazgo = true solo si se cumplen las DOS condiciones:
1. La práctica NO es repetible por definición. Es repetible si:
   - algún código termina en X1 (medicamentos e insumos: se consolidan con la cantidad), o
   - la descripción dice CADA, SUBSIGUIENTE, SUBISGUIENTE, SEGUNDA/SEGUND o POR REGION
     (puede venir truncada, por ejemplo "POR REGIO").
2. Es rara: p_2_pct es menor que 5.
Si falla cualquiera de las dos, es_hallazgo = false.

## No cambian la decisión
- Que las filas estén en páginas o archivos distintos.
- Que haya 3 o más filas.
- Que los códigos sean distintos dentro del grupo (el sistema ya agrupó códigos equivalentes).
- El tipo de estudio, el servicio o tu intuición sobre si es el mismo paciente.

## Motivo
Una oración: la condición que se cumple o la que falla, con el valor de p_2_pct.
