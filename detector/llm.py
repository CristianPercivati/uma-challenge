"""3b. Clasificación alternativa: LLM + skill, con citas y guardrail (apagada por defecto).

El código arma los grupos candidatos y el LLM decide cada uno con la skill (`skills/duplicados.md`).
Los hallazgos los arma el código, con el mismo esquema que las reglas. Si el guardrail rechaza una
decisión, ese grupo lo decide la regla.
"""
import json
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from .metricas import sha
from .reglas import contiguas, hallazgo, monto_en_riesgo

SKILL = (Path(__file__).parent / "skills" / "duplicados.md").read_text(encoding="utf-8")
SKILL_VERSION = f"skill-{sha(SKILL, n=8)}"   # cambia sola si se edita la skill
INSTRUCCIONES = """Sos un auditor de facturación médica que aplica una regla fija descripta en la skill.
Devolvé una decisión por cada grupo_id recibido, sin omitir ni inventar grupos. Si es_hallazgo = true,
citá en `citas` todas las filas del grupo (archivo, pagina, nro_int) copiadas tal cual del input."""
SISTEMA = INSTRUCCIONES + "\n\n" + SKILL


class Cita(BaseModel):
    archivo: str
    pagina: int
    nro_int: str


class Decision(BaseModel):
    grupo_id: str
    es_hallazgo: bool
    motivo: str
    citas: list[Cita]


class Respuesta(BaseModel):
    decisiones: list[Decision]


def candidatos(df, freq, cfg):
    """Grupos día + familia con 2+ filas y P(≥2) < umbral laxo. La skill decide el resto."""
    laxas = freq.index[freq["p_2"] < cfg.umbral_p_llm]
    out = []
    for (dia, fam), g in df[df["familia"].isin(laxas) & (df["dia"] != "")].groupby(["dia", "familia"]):
        if len(g) < 2:
            continue
        f = freq.loc[fam]
        out.append({"grupo_id": sha(dia, fam, n=8), "fecha": dia, "practica": f["practica"],
                    "codigos": sorted(set(g["codigo"])), "descripciones": sorted(set(g["descripcion"])),
                    "servicios": sorted(set(g["servicio"])), "veces": len(g),
                    "totales": [str(t) for t in g["total"]], "contiguas": contiguas(g),
                    "lambda": float(f["lambda"]), "p_2_pct": round(100 * float(f["p_2"]), 2),
                    "filas": [{"archivo": a, "pagina": int(p), "nro_int": n}
                              for a, p, n in zip(g["archivo"], g["pagina"], g["nro_int"])],
                    "_g": g})
    return out


def guardrail(d, por_id, paginas_pdf, filas_lote):
    """Motivos para rechazar una decisión (vacío = pasa): lo citado tiene que existir y ser del grupo."""
    c = por_id.get(d.grupo_id)
    if c is None:
        return ["grupo inexistente"]
    propias = {(f["archivo"], f["pagina"], f["nro_int"]) for f in c["filas"]}
    citadas = [(ct.archivo, ct.pagina, ct.nro_int) for ct in d.citas]
    motivos = []
    if d.es_hallazgo and set(citadas) != propias:
        motivos.append("no cita todas las filas del grupo")
    if len(citadas) != len(set(citadas)):
        motivos.append("cita filas repetidas")
    for archivo, pagina, nro in citadas:
        if archivo not in paginas_pdf:
            motivos.append("archivo inexistente")
        elif not 1 <= pagina <= paginas_pdf[archivo]:
            motivos.append("página inexistente")
        elif (archivo, pagina, nro) not in filas_lote:
            motivos.append("la fila no está en esa página")
        elif (archivo, pagina, nro) not in propias:
            motivos.append("fila de otro grupo")
    return sorted(set(motivos))


def llamar(payload, cfg, m, cache=True):
    """Gemini con salida estructurada y temperatura 0. La respuesta queda en caché.

    `cache=False` (lo usa el evaluador): siempre llama, para medir si el modelo da lo mismo dos veces.
    """
    f = cfg.cache / f"llm_{sha(cfg.model, SISTEMA, payload)}.json"
    if cache and f.exists():
        m["cache_hits"] += 1
        return json.loads(f.read_text(encoding="utf-8"))
    cfg.requerir(GCP_PROJECT=cfg.project_id)
    from google import genai
    from google.genai import types
    client = genai.Client(vertexai=True, project=cfg.project_id, location=cfg.location)
    resp = client.models.generate_content(
        model=cfg.model, contents=payload,
        config=types.GenerateContentConfig(system_instruction=SISTEMA, response_mime_type="application/json",
                                           response_schema=Respuesta, temperature=0))
    u = resp.usage_metadata
    r = {"text": resp.text, "tokens_in": u.prompt_token_count or 0,
         "tokens_out": (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)}
    if cache:
        cfg.cache.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(r), encoding="utf-8")
    m["tokens_in"] += r["tokens_in"]
    m["tokens_out"] += r["tokens_out"]
    m["costo_usd"] += (r["tokens_in"] * cfg.price_in + r["tokens_out"] * cfg.price_out) / 1e6
    return r


def clasificar(df, freq, lote, hallazgos_reglas, cfg, met):
    """Devuelve (hallazgos del LLM, comparación reglas vs. LLM, rechazos del guardrail)."""
    version = f"llm {cfg.model} {SKILL_VERSION}"
    reglas = {(h["fecha"], h["familia"]): h for h in hallazgos_reglas if h["tipo"] == "DUPLICADO_POTENCIAL"}
    paginas_pdf = {x["archivo"]: len(x["rutas"]) for x in lote.values()}
    filas_lote = set(zip(df["archivo"], df["pagina"].astype(int), df["nro_int"]))
    hallazgos, rechazos = [], {}
    with met.medir("3b clasificación LLM") as m:
        cands = candidatos(df, freq, cfg)
        payload = json.dumps([{k: v for k, v in c.items() if k != "_g"} for c in cands], ensure_ascii=False)
        decisiones = Respuesta.model_validate_json(llamar(payload, cfg, m)["text"]).decisiones
        por_id = {c["grupo_id"]: c for c in cands}
        for d in decisiones:
            if motivos := guardrail(d, por_id, paginas_pdf, filas_lote):
                rechazos[d.grupo_id] = motivos
                c = por_id.get(d.grupo_id)
                if c and (c["fecha"], c["practica"]) in reglas:   # respaldo: decide la regla
                    hallazgos.append(reglas[(c["fecha"], c["practica"])])
                continue
            if d.es_hallazgo:
                c = por_id[d.grupo_id]
                hallazgos.append(hallazgo("DUPLICADO_POTENCIAL", version, c["_g"], f"[LLM] {d.motivo}",
                                          monto_en_riesgo(c["_g"]["total"]), c["fecha"], c["contiguas"]))

    llm = {d.grupo_id: d for d in decisiones}
    comp = pd.DataFrame([{
        "fecha": c["fecha"], "practica": c["practica"], "veces": c["veces"], "contiguas": c["contiguas"],
        "p_2_pct": c["p_2_pct"], "reglas": (c["fecha"], c["practica"]) in reglas,
        "llm": llm[c["grupo_id"]].es_hallazgo if c["grupo_id"] in llm else None,
        "guardrail": ", ".join(rechazos.get(c["grupo_id"], [])) or "ok",
        "motivo_llm": llm[c["grupo_id"]].motivo if c["grupo_id"] in llm else "",
    } for c in cands])
    sin_decision = set(por_id) - set(llm)
    return hallazgos, comp, {"candidatos": len(cands), "rechazos": rechazos, "sin_decision": len(sin_decision)}
