"""Orquestación: 1 Extractor → 2 Pre-procesado → 3 Clasificación (reglas · 3b LLM) → 4 Cruzador → 5 Trazador."""
import hashlib
import json
from datetime import datetime

import pandas as pd

from . import cruzador, extractor, llm, preprocesado, reglas, trazador
from .db import Base
from .metricas import Metricas


def carpeta_corrida(cfg, tipo="runs"):
    """salida/<tipo>/AAAAMMDD-HHMMSS: una carpeta por corrida, para poder comparar corridas."""
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    corrida, n = base, 1
    while (cfg.salida / tipo / corrida).exists():
        n += 1
        corrida = f"{base}-{n}"
    (cfg.salida / tipo / corrida).mkdir(parents=True)
    return corrida, cfg.salida / tipo / corrida


def huella(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def correr(pdfs, cfg, extraccion=None, trazar_tipos=("NRO_INT_REPETIDO", "DUPLICADO_POTENCIAL", "REDONDEO")):
    """Corre el flujo completo sobre un lote.

    Las salidas de la corrida van a `salida/runs/<corrida>/`. La caché y la base se comparten
    entre corridas: son las que hacen que correr dos veces no duplique hallazgos ni vuelva a pagar.
    `extraccion`: CSV de una extracción ya hecha. Si se pasa, no se llama a Document AI.
    """
    met = Metricas()
    corrida, run = carpeta_corrida(cfg)
    base = Base(cfg.db)
    en_base_antes = base.contar("hallazgos")

    # 1. Extractor
    lote = extractor.dividir(pdfs, cfg, met)
    crudo = (extractor.cargar_extraccion(extraccion, lote, met) if extraccion
             else extractor.extraer(lote, cfg, met))
    pd.DataFrame(crudo).to_csv(run / "extraccion.csv", index=False)

    # 2. Pre-procesado
    df, totales, limpieza = preprocesado.limpiar(crudo, met)
    freq, dias_lote = preprocesado.frecuencias(df, cfg)
    base.guardar_filas(df)
    base.guardar_frecuencias(freq)

    # 3. Clasificación: reglas, y opcionalmente el LLM en lugar de la regla de duplicados
    hallazgos_reglas = reglas.clasificar(df, freq, cfg, met)
    base.guardar_hallazgos(hallazgos_reglas)
    activos, comp_llm, info_llm = hallazgos_reglas, None, None
    if cfg.usar_llm:
        hallazgos_llm, comp_llm, info_llm = llm.clasificar(df, freq, lote, hallazgos_reglas, cfg, met)
        base.guardar_hallazgos(hallazgos_llm)
        activos = [h for h in hallazgos_reglas if h["tipo"] != "DUPLICADO_POTENCIAL"] + hallazgos_llm
        comp_llm.to_csv(run / "comparacion_llm.csv", index=False)

    # 4. Cruzador (ficticio) y 5. Trazador
    finales = cruzador.cruzar(activos)
    pdf_trazado = run / "hallazgos_trazados.pdf"
    indice = trazador.trazar(finales, df, lote, pdf_trazado, trazar_tipos, met)

    # Salidas de la corrida
    vista = reglas.vista(finales)
    vista.to_csv(run / "hallazgos.csv", index=False)
    metricas = met.tabla(sum(len(x["rutas"]) for x in lote.values()))
    metricas.to_csv(run / "metricas.csv", index=False)
    base.guardar_metricas(corrida, metricas)
    en_base = base.contar("hallazgos")

    total = metricas[metricas["bloque"] == "TOTAL"].iloc[0]
    resumen = {
        "corrida": corrida,
        "hallazgos": len(vista),
        "por_tipo": vista["tipo"].value_counts().to_dict(),
        "monto_en_riesgo": str(sum(vista["monto_en_riesgo"])),
        "hallazgos_nuevos_en_base": en_base - en_base_antes,
        "hallazgos_en_base": en_base,
        "costo_usd": round(float(total["costo_usd"]), 5),
        "latencia_s": round(float(total["latencia_s"]), 3),
        "cache_hits": int(total["cache_hits"]),
        # Misma huella en dos corridas = mismo resultado, byte a byte.
        "huella_hallazgos_csv": huella(run / "hallazgos.csv"),
        "huella_extraccion_csv": huella(run / "extraccion.csv"),
    }
    (run / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "corrida": corrida, "run": run, "resumen": resumen, "lote": lote, "limpieza": limpieza,
        "dias_lote": dias_lote, "familias": len(freq), "candidatas": int(freq["candidata"].sum()),
        "control": reglas.control_totales(df, totales), "hallazgos": finales, "vista": vista,
        "indice": indice, "metricas": metricas, "llm": info_llm, "hallazgos_en_base": en_base,
        "archivos": {"hallazgos": run / "hallazgos.csv", "pdf_trazado": pdf_trazado if indice else None,
                     "metricas": run / "metricas.csv", "extraccion": run / "extraccion.csv",
                     "resumen": run / "resumen.json", "base": cfg.db, "cache": cfg.cache},
    }
