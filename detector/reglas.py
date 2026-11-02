"""3. Clasificación por reglas: un hallazgo = un grupo de filas.

| Tipo                  | Agrupa por                                   | Monto en riesgo              |
|-----------------------|----------------------------------------------|------------------------------|
| NRO_INT_REPETIDO      | Nro. Int. (en todo el lote)                  | suma − máximo                |
| DUPLICADO_POTENCIAL   | día + familia candidata, 2+ filas            | suma − máximo                |
| REDONDEO              | arancel (código + Der. + Hon. + Total)       | lo facturado de más × veces  |
"""
from decimal import Decimal

import pandas as pd

from .metricas import sha

ORDEN_TIPOS = ["NRO_INT_REPETIDO", "DUPLICADO_POTENCIAL", "REDONDEO"]
COLS_VISTA = ["tipo", "fecha", "familia", "codigos", "veces", "monto_en_riesgo", "contiguas",
              "nro_ints", "ubicacion", "motivo", "regla_version", "hallazgo_id"]


def importes(serie):
    return [x for x in serie if isinstance(x, Decimal)]


def contiguas(g):
    """¿Hay dos filas seguidas en el mismo archivo? Es lo más cercano al paciente que tenemos."""
    o = sorted(zip(g["archivo"], g["orden"]))
    return any(a == c and d - b == 1 for (a, b), (c, d) in zip(o, o[1:]))


def monto_en_riesgo(totales):
    """Se asume legítima la fila más cara: el débito es el resto."""
    t = importes(totales)
    return sum(t) - max(t) if t else Decimal(0)


def hallazgo(tipo, version, g, motivo, monto, fecha="", contig=None):
    ids = sorted(g["fila_id"])
    return {
        "hallazgo_id": sha(version, tipo, *ids), "tipo": tipo, "fecha": fecha,
        "familia": " / ".join(sorted(set(g["practica"]))), "codigos": ", ".join(sorted(set(g["codigo"]))),
        "veces": len(g), "monto_en_riesgo": monto, "contiguas": contig,
        "nro_ints": ", ".join(g["nro_int"]),
        "ubicacion": "; ".join(f"{a} p.{', '.join(map(str, sorted(set(x['pagina']))))}"
                               for a, x in g.groupby("archivo")),
        "motivo": motivo, "regla_version": version, "fila_ids": ids,
    }


def regla_nro_int(df, version):
    return [hallazgo("NRO_INT_REPETIDO", version, g, f"Nro. Int. {nro} aparece {len(g)} veces",
                     monto_en_riesgo(g["total"]))
            for nro, g in df.groupby("nro_int") if len(g) > 1]


def regla_mismo_dia(df, freq, version):
    out = []
    cand = df[df["familia"].isin(freq.index[freq["candidata"]]) & (df["dia"] != "")]
    for (dia, fam), g in cand.groupby(["dia", "familia"]):
        if len(g) < 2:
            continue
        f, c = freq.loc[fam], contiguas(g)
        motivo = (f"{f['practica']} {len(g)} veces el {dia}; esperado {f['lambda']:.2f}/día "
                  f"(P(≥2) = {f['p_2']:.1%})" + ("; filas contiguas" if c else ""))
        out.append(hallazgo("DUPLICADO_POTENCIAL", version, g, motivo, monto_en_riesgo(g["total"]), dia, c))
    return out


def regla_redondeo(df, version):
    """Der. + Hon. ≠ Total. Se agrupa por arancel: el error de redondeo se repite en cada fila."""
    trio = [all(isinstance(v, Decimal) for v in x) for x in zip(df["der"], df["hon"], df["total"])]
    d = df[trio]
    d = d[[a + b != t for a, b, t in zip(d["der"], d["hon"], d["total"])]]
    out = []
    for (cod, der, hon, tot), g in d.groupby(["codigo", "der", "hon", "total"]):
        dif = tot - (der + hon)
        motivo = f"{cod}: Der. {der} + Hon. {hon} = {der + hon}, Total {tot} ({dif:+}) en {len(g)} filas"
        out.append(hallazgo("REDONDEO", version, g, motivo, max(dif, Decimal(0)) * len(g)))
    return out


def clasificar(df, freq, cfg, met):
    with met.medir("3 clasificación reglas"):
        v = cfg.reglas_version
        return regla_nro_int(df, v) + regla_mismo_dia(df, freq, v) + regla_redondeo(df, v)


def control_totales(df, totales):
    """Suma de las filas contra el TOTAL impreso. Si no cuadra, lo primero a sospechar es la extracción."""
    filas = []
    for a, g in df.groupby("archivo"):
        suma, impreso = sum(importes(g["total"])), totales.get(a)
        filas.append({"archivo": a, "filas": len(g), "suma_filas": suma, "total_impreso": impreso,
                      "diferencia": impreso - suma if isinstance(impreso, Decimal) else None})
    return pd.DataFrame(filas).set_index("archivo")


def vista(hallazgos):
    """Hallazgos separados por tipo y ordenados por monto en riesgo."""
    h = pd.DataFrame(hallazgos, columns=COLS_VISTA)
    h["_t"] = h["tipo"].map(ORDEN_TIPOS.index)
    h["_m"] = h["monto_en_riesgo"].map(float)
    return h.sort_values(["_t", "_m"], ascending=[True, False])[COLS_VISTA].reset_index(drop=True)
