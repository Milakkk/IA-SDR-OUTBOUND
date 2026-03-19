from pathlib import Path
import json

import product_kb


def summarize_excel(items):
    by_cat = {}
    for it in items:
        cat = it.get("category") or "geral"
        by_cat.setdefault(cat, []).append(it)
    lines = []
    for cat, lst in by_cat.items():
        names = [i.get("name") for i in lst if i.get("name")]
        lines.append(f"Categoria {cat}: {len(lst)} itens")
        if names:
            lines.append(" - Exemplos: " + ", ".join(names[:8]))
    return "\n".join(lines)


def main():
    lines = []
    samples = {}
    try:
        excel = product_kb.load_excel_index()
        lines.append("=== Excel ===")
        if excel.get("ok"):
            lines.append(f"Abas lidas: {', '.join(excel.get('sheets', []))}")
            lines.append(summarize_excel(excel.get("items", [])))
        else:
            lines.append("Falhou ao ler Excel: " + str(excel.get("error")))
    except Exception as e:
        lines.append(f"Erro Excel: {e}")

    try:
        site = product_kb.crawl_site(max_pages=20)
        lines.append("\n=== Site (top páginas) ===")
        if site.get("ok"):
            pages = site.get("pages", [])
            lines.append(f"Páginas coletadas: {len(pages)}")
            for p in pages[:10]:
                text = p.get("text", "")
                head = (text[:200] + "...") if len(text) > 200 else text
                lines.append(f"- {p.get('url')}: {head}")
        else:
            lines.append("Falhou ao rastrear site: " + str(site.get("error")))
    except Exception as e:
        lines.append(f"Erro Site: {e}")

    try:
        for q in [
            "Audace Pro",
            "Usina 4.0",
            "Silicon Smart",
            "Silicon Care",
            "energia solar",
            "iluminação esportiva",
            "grow",
            "garantia luminárias",
        ]:
            res = product_kb.query_product_knowledge(q, top_k=3)
            samples[q] = res
    except Exception as e:
        samples["error"] = str(e)

    out_txt = Path(__file__).parent / "kb_summary.txt"
    out_json = Path(__file__).parent / "kb_samples.json"
    try:
        out_txt.write_text("\n".join(lines), encoding="utf-8")
        out_json.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Salvo: {out_txt}")
        print(f"Salvo: {out_json}")
    except Exception as e:
        print(f"Falha ao salvar arquivos: {e}")


if __name__ == "__main__":
    main()