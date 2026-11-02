"""Evaluador: mide el clasificador contra el golden dataset (`python -m detector evals`).

No corre el pipeline: carga una extracción ya hecha, la limpia igual que el pipeline y corre el
clasificador sobre ella. No llama a Document AI ni necesita los PDFs; solo llama a Gemini con --llm.

| Qué se mide                | Cómo                                                        | Costo  |
|----------------------------|-------------------------------------------------------------|--------|
| Reglas vs. golden          | precisión, recall, falsos positivos y negativos, por caso   | 0      |
| Idempotencia de las reglas | 2 corridas, mismos hallazgos                                | 0      |
| 3b LLM vs. golden (--llm)  | lo mismo + guardrail; 2 corridas sin caché (estabilidad)    | tokens |
| Guardrail                  | salidas válidas corrompidas de 7 maneras                    | 0      |

Un caso del golden es un grupo de filas. Se cuenta como marcado si algún hallazgo de duplicado
toca sus filas. Falso positivo (alucinación): marcar un NO_DUPLICADO. Falso negativo (omisión):
no marcar un DUPLICADO.
"""
import hashlib
import json
import random
from collections import Counter

import pandas as pd

from . import extractor, llm, preprocesado, reglas
from .metricas import Metricas, sha

# Nombres cortos de archivo que usa el golden.
ALIAS = {"L0": "detalle_ANONIMIZADO_sin_afiliado.pdf",
         "L1": "detalle_(1)_ANONIMIZADO_sin_afiliado.pdf",
         "L2": "detalle_(2)_ANONIMIZADO_sin_afiliado.pdf"}
DUPLICADOS = {"NRO_INT_REPETIDO", "DUPLICADO_POTENCIAL"}
RUNS_LLM = 2


def cargar_lote(csvs, met):
    """Lote mínimo desde la extracción (sin PDFs): las páginas salen de la página más alta extraída."""
    t = pd.concat([pd.read_csv(c, dtype=str, keep_default_na=False) for c in csvs], ignore_index=True)
    lote = {a: {"archivo": a, "sha": hashlib.sha256(a.encode()).hexdigest(),
                "rutas": dict.fromkeys(range(g["pagina"].astype(int).max()), "imagen")}
            for a, g in t.groupby("archivo")}
    return lote, extractor.cargar_extraccion(csvs, lote, met)


def cargar_golden(path, df):
    """Golden -> un conjunto de fila_id por caso, buscando cada fila por (archivo, Nro. Int.)."""
    g = pd.read_csv(path, dtype=str, keep_default_na=False)
    clave = dict(zip(zip(df["archivo"], df["nro_int"]), df["fila_id"]))
    filas, faltan = [], []
    for c in g.itertuples():
        ids = set()
        for p, n in zip(c.paginas.split(", "), c.nro_ints.split(", ")):
            k = (ALIAS.get(p.split()[0], p.split()[0]), n)
            ids.add(clave[k]) if k in clave else faltan.append(k)
        filas.append(frozenset(ids))
    if faltan:
        raise RuntimeError(f"Filas del golden que no están en la extracción: {faltan[:5]}")
    g["filas"] = filas
    return g


def resultados(golden, marcadas):
    """TP / FN / FP / TN por caso."""
    return [("T" if bool(f & marcadas) == (e == "DUPLICADO") else "F") + ("P" if f & marcadas else "N")
            for e, f in zip(golden["etiqueta"], golden["filas"])]


def metricas(res):
    c = Counter(res)
    return {"precision": round(c["TP"] / max(c["TP"] + c["FP"], 1), 3),
            "recall": round(c["TP"] / max(c["TP"] + c["FN"], 1), 3),
            "falsos_positivos": c["FP"], "falsos_negativos": c["FN"], "TP": c["TP"], "TN": c["TN"]}


def filas_marcadas(hallazgos):
    return {f for h in hallazgos if h["tipo"] in DUPLICADOS for f in h["fila_ids"]}


def grupos_del_golden(df, freq, golden, cfg):
    """Para cada caso, el grupo candidato del 3b (día + familia) que lo contiene, si pasa el filtro laxo."""
    cands = {c["grupo_id"]: c for c in llm.candidatos(df, freq, cfg)}
    filas = df.set_index("fila_id")
    gid_caso = []
    for f in golden["filas"]:
        r = filas.loc[sorted(f)[0]]
        gid = sha(r["dia"], r["familia"], n=8)
        gid_caso.append(gid if gid in cands else None)
    return cands, gid_caso


def eval_llm(df, freq, lote, golden, cfg, met):
    """El 3b sobre los grupos del golden, RUNS_LLM veces sin caché, con guardrail."""
    cands, gid_caso = grupos_del_golden(df, freq, golden, cfg)
    enviar = {gid: cands[gid] for gid in gid_caso if gid}
    payload = json.dumps([{k: v for k, v in c.items() if k != "_g"} for c in enviar.values()], ensure_ascii=False)
    paginas_pdf = {x["archivo"]: len(x["rutas"]) for x in lote.values()}
    filas_lote = set(zip(df["archivo"], df["pagina"].astype(int), df["nro_int"]))
    corridas = []
    with met.medir("3b LLM vs. golden") as m:
        for _ in range(RUNS_LLM):
            decisiones = llm.Respuesta.model_validate_json(llm.llamar(payload, cfg, m, cache=False)["text"]).decisiones
            rechazos = {d.grupo_id: mot for d in decisiones if (mot := llm.guardrail(d, enviar, paginas_pdf, filas_lote))}
            marcadas = {f for d in decisiones if d.es_hallazgo and d.grupo_id in enviar and d.grupo_id not in rechazos
                        for f in enviar[d.grupo_id]["_g"]["fila_id"]}
            corridas.append({"res": resultados(golden, marcadas), "rechazos": rechazos,
                             "decisiones": {d.grupo_id: d for d in decisiones},
                             "sin_decision": len(set(enviar) - {d.grupo_id for d in decisiones})})
    return corridas, gid_caso, len(enviar)


def eval_guardrail(df, freq, lote, golden, cfg):
    """Salidas válidas (citan las filas reales de un DUPLICADO del golden) corrompidas de 7 maneras.
    El guardrail tiene que rechazar todas las corruptas y ninguna válida."""
    cands, gid_caso = grupos_del_golden(df, freq, golden, cfg)
    paginas_pdf = {x["archivo"]: len(x["rutas"]) for x in lote.values()}
    filas_lote = set(zip(df["archivo"], df["pagina"].astype(int), df["nro_int"]))
    gids = sorted({gid for gid, e in zip(gid_caso, golden["etiqueta"]) if gid and e == "DUPLICADO"})
    validas = [llm.Decision(grupo_id=gid, es_hallazgo=True, motivo="válida",
                            citas=[llm.Cita(**f) for f in cands[gid]["filas"]]) for gid in gids]
    rnd = random.Random(0)

    def cita0(d, **cambios):
        return d.model_copy(update={"citas": [d.citas[0].model_copy(update=cambios)] + d.citas[1:]})

    def otra_fila(d):
        propias = {(c.archivo, c.pagina, c.nro_int) for c in d.citas}
        a, p, n = rnd.choice(sorted(filas_lote - propias))
        return d.model_copy(update={"citas": d.citas + [llm.Cita(archivo=a, pagina=p, nro_int=n)]})

    corrupciones = {
        "grupo inventado": lambda d: d.model_copy(update={"grupo_id": "ffffffff"}),
        "sin citas": lambda d: d.model_copy(update={"citas": []}),
        "archivo inexistente": lambda d: cita0(d, archivo="otro_prestador.pdf"),
        "página inexistente": lambda d: cita0(d, pagina=999),
        "página equivocada": lambda d: cita0(d, pagina=d.citas[0].pagina % paginas_pdf[d.citas[0].archivo] + 1),
        "Nro. Int. inventado": lambda d: cita0(d, nro_int="0000000"),
        "fila de otro grupo": otra_fila,
    }

    def rechaza(d):
        return bool(llm.guardrail(d, cands, paginas_pdf, filas_lote))

    filas = [{"salida": "válida (no debe rechazarse)", "casos": len(validas),
              "rechazadas": sum(map(rechaza, validas))}]
    filas += [{"salida": nombre, "casos": len(validas), "rechazadas": sum(rechaza(f(d)) for d in validas)}
              for nombre, f in corrupciones.items()]
    return pd.DataFrame(filas)


def correr_evals(csvs, golden_path, cfg, carpeta):
    """Corre el evaluador y escribe sus salidas en `carpeta`. Devuelve el resumen."""
    met = Metricas()
    lote, crudo = cargar_lote(csvs, met)
    df, _, _ = preprocesado.limpiar(crudo, met)
    freq, _ = preprocesado.frecuencias(df, cfg)
    golden = cargar_golden(golden_path, df)

    # Reglas: 2 corridas para la idempotencia
    corridas = [reglas.clasificar(df, freq, cfg, met) for _ in range(2)]
    idem = sorted(h["hallazgo_id"] for h in corridas[0]) == sorted(h["hallazgo_id"] for h in corridas[1])
    res_reglas = resultados(golden, filas_marcadas(corridas[0]))

    casos = golden[["caso_id", "etiqueta", "estrato", "practica"]].copy()
    casos["reglas"] = res_reglas
    resumen = {"golden": golden["etiqueta"].value_counts().to_dict(),
               "reglas": {**metricas(res_reglas), "idempotencia_2_corridas": idem}}

    if cfg.usar_llm:
        corr_llm, gid_caso, enviados = eval_llm(df, freq, lote, golden, cfg, met)
        for i, c in enumerate(corr_llm, 1):
            casos[f"llm_{i}"] = c["res"]
        d1, rech = corr_llm[0]["decisiones"], corr_llm[0]["rechazos"]
        casos["motivo_llm"] = [("no pasa el filtro por código" if g is None else "sin decisión") if g not in d1
                               else d1[g].motivo + (" · rechazada por el guardrail" if g in rech else "")
                               for g in gid_caso]
        resumen["llm"] = {**metricas(corr_llm[0]["res"]), "casos_enviados": enviados,
                          "estable_entre_corridas": all(c["res"] == corr_llm[0]["res"] for c in corr_llm),
                          "rechazadas_por_guardrail": len(corr_llm[0]["rechazos"]),
                          "sin_decision": corr_llm[0]["sin_decision"],
                          "rechazos": {g: m for g, m in corr_llm[0]["rechazos"].items()}}

    guard = eval_guardrail(df, freq, lote, golden, cfg)
    validas = guard.iloc[0]
    corruptas = guard.iloc[1:]
    resumen["guardrail"] = {"corruptas_rechazadas": f"{int(corruptas['rechazadas'].sum())}/{int(corruptas['casos'].sum())}",
                            "validas_rechazadas": f"{int(validas['rechazadas'])}/{int(validas['casos'])}"}

    tabla = met.tabla(sum(len(x["rutas"]) for x in lote.values()))
    total = tabla[tabla["bloque"] == "TOTAL"].iloc[0]
    resumen["costo_usd"] = round(float(total["costo_usd"]), 5)
    resumen["tokens"] = {"in": int(total["tokens_in"]), "out": int(total["tokens_out"])}

    casos.to_csv(carpeta / "golden_resultados.csv", index=False)
    guard.to_csv(carpeta / "guardrail.csv", index=False)
    tabla.to_csv(carpeta / "metricas.csv", index=False)
    (carpeta / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumen, casos, guard
