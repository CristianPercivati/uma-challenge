"""CLI.

    python -m detector PDF [PDF ...] [--salida DIR] [--extraccion CSV ...] [--llm]   corre el pipeline
    python -m detector evals [--extraccion CSV ...] [--golden CSV] [--llm]          evalúa contra el golden
    python -m detector comparar [--salida DIR]                                       SHA-256 de hallazgos.csv entre corridas
"""
import argparse
import glob
import hashlib
import json
import logging
import sys
from pathlib import Path

import pandas as pd

from .config import Config
from .pipeline import carpeta_corrida, correr


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="aviso: %(message)s")
    pd.set_option("display.width", 200)
    pd.set_option("display.max_colwidth", 70)
    if argv[:1] == ["evals"]:
        return main_evals(argv[1:])
    if argv[:1] == ["comparar"]:
        return main_comparar(argv[1:])

    p = argparse.ArgumentParser(prog="python -m detector",
                                description="Detecta prácticas duplicadas en un lote de rendiciones (PDF). "
                                            "Para evaluar contra el golden: python -m detector evals")
    p.add_argument("pdfs", nargs="+", type=Path, help="PDFs del lote")
    p.add_argument("--salida", type=Path, default=Path("salida"), help="carpeta de salida (default: salida/)")
    p.add_argument("--extraccion", nargs="+", type=Path,
                   help="CSV de una extracción ya hecha: no llama a Document AI ni necesita credenciales")
    p.add_argument("--llm", action="store_true", help="usar el flujo 3b (LLM + skill) para los duplicados")
    p.add_argument("--umbral", type=float, default=0.05, help="P(≥2 el mismo día) máxima (default: 0.05)")
    a = p.parse_args(argv)

    a.pdfs, a.extraccion = expandir(a.pdfs), expandir(a.extraccion) if a.extraccion else None
    verificar_existen(p, a.pdfs + (a.extraccion or []))
    cfg = Config(salida=a.salida, usar_llm=a.llm, umbral_p=a.umbral)
    reporte(ejecutar(p, lambda: correr(a.pdfs, cfg, extraccion=a.extraccion)))


def main_evals(argv):
    p = argparse.ArgumentParser(prog="python -m detector evals",
                                description="Evalúa el clasificador contra el golden dataset. No corre el pipeline: "
                                            "usa una extracción ya hecha y no llama a Document AI.")
    p.add_argument("--extraccion", nargs="+", type=Path, default=[Path("data/extraccion/*.csv")],
                   help="CSV de la extracción del lote (default: data/extraccion/*.csv)")
    p.add_argument("--golden", type=Path, default=Path("data/golden_duplicados.csv"),
                   help="golden dataset (default: data/golden_duplicados.csv)")
    p.add_argument("--salida", type=Path, default=Path("salida"), help="carpeta de salida (default: salida/)")
    p.add_argument("--llm", action="store_true", help="evaluar también el flujo 3b (llama a Gemini, tiene costo)")
    p.add_argument("--umbral", type=float, default=0.05, help="P(≥2 el mismo día) máxima (default: 0.05)")
    a = p.parse_args(argv)

    a.extraccion = expandir(a.extraccion)
    verificar_existen(p, a.extraccion + [a.golden])
    from .evals import correr_evals
    cfg = Config(salida=a.salida, usar_llm=a.llm, umbral_p=a.umbral)
    corrida, carpeta = carpeta_corrida(cfg, "evals")
    resumen, casos, guard = ejecutar(p, lambda: correr_evals(a.extraccion, a.golden, cfg, carpeta))
    reporte_evals(corrida, carpeta, resumen, casos, guard)


def main_comparar(argv):
    p = argparse.ArgumentParser(prog="python -m detector comparar",
                                description="Calcula el SHA-256 de hallazgos.csv en cada corrida de salida/runs/ "
                                            "y dice si todas dieron el mismo resultado.")
    p.add_argument("--salida", type=Path, default=Path("salida"), help="carpeta de salida (default: salida/)")
    a = p.parse_args(argv)

    corridas = sorted(d for d in (a.salida / "runs").glob("*") if (d / "hallazgos.csv").exists())
    if not corridas:
        p.exit(1, f"error: no hay corridas con hallazgos.csv en {a.salida / 'runs'}\n")
    hashes = {d.name: hashlib.sha256((d / "hallazgos.csv").read_bytes()).hexdigest() for d in corridas}

    print(f"\n== SHA-256 de hallazgos.csv · {len(hashes)} corridas en {a.salida / 'runs'}")
    for corrida, h in hashes.items():
        print(f"{corrida:<20} {h}")
    distintos = set(hashes.values())
    if len(hashes) == 1:
        print("\nHay una sola corrida: corré el pipeline otra vez para comparar.")
    elif len(distintos) == 1:
        print(f"\n✅ Todas iguales: las {len(hashes)} corridas dieron exactamente el mismo hallazgos.csv")
    else:
        print(f"\n❌ Hay {len(distintos)} resultados distintos:")
        for h in distintos:
            print(f"   {h[:16]}…  {', '.join(c for c, x in hashes.items() if x == h)}")
        sys.exit(1)


def verificar_existen(p, rutas):
    faltan = [str(x) for x in rutas if not x.exists()]
    if not rutas or faltan:
        p.error(f"no existen: {', '.join(faltan) or 'ningún archivo'}")


def ejecutar(p, fn):
    """Corre `fn` y convierte los errores de configuración y de credenciales en un mensaje claro."""
    try:
        return fn()
    except RuntimeError as e:
        p.exit(1, f"error: {e}\n")
    except Exception as e:
        if type(e).__module__.startswith("google.auth"):
            p.exit(1, f"error de credenciales de GCP: {e}\n")
        raise


def ok(x):
    return "✅" if x else "❌"


def reporte_evals(corrida, carpeta, resumen, casos, guard):
    print(f"\n== Evals contra el golden · {corrida}")
    print(f"golden: {resumen['golden']}")
    sistemas = [("reglas", "Reglas")] + ([("llm", "3b LLM (corrida 1)")] if "llm" in resumen else [])
    for clave, nombre in sistemas:
        m = resumen[clave]
        print(f"\n{nombre}: precisión {m['precision']:.0%} · recall {m['recall']:.0%} · "
              f"falsos positivos {m['falsos_positivos']} · falsos negativos {m['falsos_negativos']}")
    print(f"{ok(resumen['reglas']['idempotencia_2_corridas'])} Reglas: 2 corridas dan los mismos hallazgos")
    if "llm" in resumen:
        m = resumen["llm"]
        print(f"{ok(m['estable_entre_corridas'])} 3b: las corridas sin caché dan el mismo resultado · "
              f"{m['casos_enviados']} grupos enviados · {m['rechazadas_por_guardrail']} rechazadas por el guardrail · "
              f"{m['sin_decision']} sin decisión")

    columnas = [c for c in ["reglas", "llm_1"] if c in casos]
    errores = casos[casos[columnas].isin(["FP", "FN"]).any(axis=1)]
    print(f"\nCasos con error: {len(errores)} de {len(casos)}")
    if len(errores):
        print(errores.to_string(index=False))

    g = resumen["guardrail"]
    print(f"\n{ok(g['corruptas_rechazadas'].split('/')[0] == g['corruptas_rechazadas'].split('/')[1])} Guardrail: "
          f"corruptas rechazadas {g['corruptas_rechazadas']} · "
          f"{ok(g['validas_rechazadas'].startswith('0/'))} válidas rechazadas {g['validas_rechazadas']}")
    print(guard.to_string(index=False))
    print(f"\nCosto de la evaluación: USD {resumen['costo_usd']} · tokens {resumen['tokens']['in']:,} in / "
          f"{resumen['tokens']['out']:,} out")
    print(f"\n== Salidas en {carpeta}")
    for f in sorted(carpeta.iterdir()):
        print(f"  {f.name}")


def expandir(rutas):
    """Expande comodines (PowerShell no lo hace solo)."""
    out = []
    for r in rutas:
        out += sorted(map(Path, glob.glob(str(r)))) if any(c in str(r) for c in "*?") else [r]
    return out


def reporte(r):
    lim = r["limpieza"]
    print(f"\n== Lote · corrida {r['corrida']}")
    print(f"{len(r['lote'])} PDFs · {sum(len(x['rutas']) for x in r['lote'].values())} páginas · {lim['filas']} filas · "
          f"{lim['prestaciones_reparadas']} prestaciones corridas reparadas · {lim['renglones_ruido']} renglones de ruido")
    print(f"{r['dias_lote']} días con actividad · {r['familias']} familias · {r['candidatas']} candidatas")

    print("\n== Control de totales (suma de filas vs. TOTAL impreso)")
    print(r["control"].to_string())

    v = r["vista"]
    print(f"\n== {len(v)} hallazgos potenciales")
    if len(v):
        print(v.groupby("tipo", sort=False).agg(hallazgos=("hallazgo_id", "size"), filas=("veces", "sum"),
                                                monto_en_riesgo=("monto_en_riesgo", "sum")).to_string())
        print("\nPrincipales por monto en riesgo:")
        print(v.head(10)[["tipo", "fecha", "familia", "veces", "monto_en_riesgo", "contiguas", "ubicacion"]].to_string())

    if r["llm"]:
        info = r["llm"]
        print(f"\n== 3b LLM: {info['candidatos']} candidatos · {info['sin_decision']} sin decisión · "
              f"{len(info['rechazos'])} rechazadas por el guardrail (esos grupos los decide la regla)")
        for gid, motivos in info["rechazos"].items():
            print(f"   {gid}: {', '.join(motivos)}")

    met = r["metricas"]
    total = met[met["bloque"] == "TOTAL"].iloc[0]
    print("\n== Métricas por bloque")
    print(met.set_index(["bloque", "detalle"]).to_string())
    print(f"Por página: {total['latencia_s'] / total['paginas']:.3f}s · USD {total['costo_usd'] / total['paginas']:.5f} "
          f"en esta corrida (lo que vino de caché cuenta USD 0)")

    res = r["resumen"]
    print(f"\n== Salidas de la corrida en {r['run']}")
    for nombre, path in r["archivos"].items():
        if path:
            print(f"{nombre:<12} {path}")
    print(f"huella de hallazgos.csv: {res['huella_hallazgos_csv']} · huella de extraccion.csv: {res['huella_extraccion_csv']}")
    print(f"hallazgos nuevos en la base: {res['hallazgos_nuevos_en_base']} (total {res['hallazgos_en_base']})")

    # Comparación con la corrida anterior. El resultado es hallazgos.csv: misma huella = mismos hallazgos.
    # extraccion.csv se informa aparte: sin caché, el OCR puede variar en decimales (confianza, cajas)
    # sin cambiar ningún hallazgo.
    anteriores = sorted(p for p in r["run"].parent.iterdir() if p != r["run"] and (p / "resumen.json").exists())
    if anteriores:
        prev = json.loads((anteriores[-1] / "resumen.json").read_text(encoding="utf-8-sig"))
        igual = prev["huella_hallazgos_csv"] == res["huella_hallazgos_csv"]
        misma_extraccion = prev["huella_extraccion_csv"] == res["huella_extraccion_csv"]
        print(f"\n== Contra la corrida anterior ({prev['corrida']})")
        print(f"{ok(igual)} {'Mismos hallazgos' if igual else 'HALLAZGOS DISTINTOS'}: huella de hallazgos.csv "
              f"{'igual' if igual else 'distinta'} · hallazgos {prev['hallazgos']} → {res['hallazgos']} · "
              f"nuevos en la base: {res['hallazgos_nuevos_en_base']} · costo USD {prev['costo_usd']} → {res['costo_usd']}")
        print(f"   extraccion.csv: {'idéntica' if misma_extraccion else 'con diferencias'}"
              + ("" if misma_extraccion else " (sin caché el OCR puede variar en decimales de confianza o cajas; "
                                             "los hallazgos son lo que se compara)"))


if __name__ == "__main__":
    main()
