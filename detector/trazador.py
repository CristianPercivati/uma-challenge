"""5. Trazador: marca las filas de cada hallazgo sobre las páginas originales.

Una caja por fila, con el color según lo que tiene esa fila:
- rojo: duplicado potencial (o Nro. Int. repetido)
- celeste: error de redondeo en la suma horizontal (Der. + Hon. ≠ Total)
- naranja: las dos cosas

Las marcas son anotaciones del PDF: la imagen original no se modifica. Al pasar el mouse por un
recuadro se ven los motivos. El PDF de salida trae solo las páginas que tienen hallazgos.
"""
from collections import defaultdict

import pymupdf

ROJO, CELESTE, NARANJA = (1, 0, 0), (0, 0.7, 1), (1, 0.5, 0)
DUPLICADOS = {"DUPLICADO_POTENCIAL", "NRO_INT_REPETIDO"}
LEYENDA = "rojo: duplicado potencial · celeste: redondeo · naranja: ambos"


def color(tipos):
    if "REDONDEO" in tipos:
        return NARANJA if tipos & DUPLICADOS else CELESTE
    return ROJO


def trazar(hallazgos, df, lote, destino, tipos, met):
    """Escribe `destino` y devuelve el índice (página de salida -> archivo, página, hallazgos)."""
    filas = df.set_index("fila_id")
    marcas = defaultdict(lambda: defaultdict(list))   # (archivo, página) -> fila_id -> [hallazgos]
    for h in hallazgos:
        if h["tipo"] in tipos:
            for fid in h["fila_ids"]:
                r = filas.loc[fid]
                marcas[(r["archivo"], int(r["pagina"]))][fid].append(h)

    indice = []
    with met.medir("5 trazador") as m, pymupdf.open() as out:
        for archivo in sorted({a for a, _ in marcas}):
            with pymupdf.open(lote[archivo]["path"]) as src:
                for pagina in sorted(p for a, p in marcas if a == archivo):
                    out.insert_pdf(src, from_page=pagina - 1, to_page=pagina - 1)
                    page = out[-1]
                    ancho, alto = page.rect.width, page.rect.height
                    page.add_freetext_annot(pymupdf.Rect(10, 4, ancho / 2, 18), f"{archivo} · pág. {pagina}",
                                            fontsize=9, text_color=ROJO)
                    page.add_freetext_annot(pymupdf.Rect(ancho / 2, 4, ancho - 10, 18), LEYENDA,
                                            fontsize=7, text_color=(0.3, 0.3, 0.3), align=2)
                    for fid, hs in marcas[(archivo, pagina)].items():
                        r, c = filas.loc[fid], color({h["tipo"] for h in hs})
                        rect = pymupdf.Rect(r["x0"] * ancho - 3, r["y0"] * alto - 2,
                                            r["x1"] * ancho + 3, r["y1"] * alto + 2) * page.derotation_matrix
                        a = page.add_rect_annot(rect)
                        a.set_colors(stroke=c)
                        a.set_border(width=1.5)
                        a.set_info(title=", ".join(h["hallazgo_id"] for h in hs),
                                   content="\n".join(h["motivo"] for h in hs))
                        a.update()
                        page.add_freetext_annot(pymupdf.Rect(rect.x0, rect.y0 - 10, rect.x0 + 320, rect.y0),
                                                " · ".join(f"{h['tipo']} {h['hallazgo_id'][:8]}" for h in hs),
                                                fontsize=6, text_color=c)
                    indice.append({"pagina_salida": len(out), "archivo": archivo, "pagina": pagina,
                                   "hallazgos": ", ".join(sorted({h["hallazgo_id"][:8]
                                                                  for hs in marcas[(archivo, pagina)].values()
                                                                  for h in hs}))})
        m["paginas"] = len(out)
        if len(out):
            out.save(destino)
    return indice
