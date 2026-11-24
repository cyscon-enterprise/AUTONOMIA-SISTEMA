"""Contrato personalizado: monta o texto com os dados do cadastro, gera o PDF e o codigo de integridade.

O modelo vive em termos/01_contrato.md (campos {{RAZAO}}, {{CNPJ}}...). O texto ja preenchido e o hash
(SHA-256) ficam gravados em `crm_contratos` no aceite: prova exatamente o que a pessoa aceitou.
"""
from __future__ import annotations

import hashlib
import pathlib
import re

import dados

_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
          "novembro", "dezembro"]
_DIR = pathlib.Path(__file__).parent / "termos"


def fmt_cnpj(c: str) -> str:
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}" if len(c) == 14 else c


def fmt_cpf(c: str) -> str:
    return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}" if len(c) == 11 else c


def data_extenso(d=None) -> str:
    d = d or dados.agora()
    return f"{d.day} de {_MESES[d.month - 1]} de {d.year}"


def montar(d: dict) -> dict:
    """`d` = dados validados do cadastro. Devolve {"texto", "hash"}; o texto termina com o codigo do documento."""
    modelo = (_DIR / "01_contrato.md").read_text(encoding="utf-8")
    ender = ", ".join(x for x in (d["emp_logradouro"], f"nº {d['emp_numero']}", d.get("emp_complemento"), d["emp_bairro"],
                                  f"{d['emp_cidade']}/{d['emp_uf']}", f"CEP {d['emp_cep'][:5]}-{d['emp_cep'][5:]}") if x)
    perfil = d["perfil_atuacao"]
    campos = {"RAZAO": d["emp_razao"], "CNPJ": fmt_cnpj(d["emp_cnpj"]), "ENDERECO": ender, "REPRESENTANTE": d["nome"],
              "CPF": fmt_cpf(d["documento"]), "P1": "[X]" if perfil == 1 else "[  ]", "P2": "[X]" if perfil == 2 else "[  ]",
              "PERFIL": f"Perfil {perfil}", "MUNICIPIO": d["emp_cidade"], "DATA": data_extenso()}
    texto = re.sub(r"\{\{(\w+)\}\}", lambda m: campos[m.group(1)], modelo).strip()
    h = hashlib.sha256(texto.encode("utf-8")).hexdigest()
    texto += f"\n\n---\n\nCódigo do documento: {h[:16].upper()} (SHA-256 {h})"
    return {"texto": texto, "hash": h}


# ---------------------------------------------------------------- PDF

def _latin(t: str) -> str:
    """As fontes padrao do PDF so tem Latin-1: troca o que fica de fora (travessao, bolinha, aspas curvas)."""
    for a, b in (("–", "-"), ("—", "-"), ("●", "____"), ("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"), ("•", "-"),
                 ("→", "->"), ("≥", ">="), ("✓", "ok")):
        t = t.replace(a, b)
    return t.encode("latin-1", "replace").decode("latin-1")


def pdf(textos: list[str], titulo: str) -> bytes:
    """PDF A4 simples a partir de textos em Markdown leve (#, ##, **negrito**, listas, tabela vira linhas)."""
    from fpdf import FPDF
    p = FPDF(format="A4")
    p.set_margins(20, 20, 20)
    p.set_auto_page_break(True, 18)
    p.set_title(_latin(titulo))
    p.set_author("AutonomIA Vendas")
    p.add_page()
    p.set_font("Helvetica", size=10)
    for i, t in enumerate(textos):
        if i:
            p.add_page()
        for bruta in t.splitlines():
            l = _latin(bruta.rstrip())
            if not l or l.strip() == "---":
                p.ln(3 if not l else 1)
                continue
            if l.startswith("# "):
                p.set_font("Helvetica", "B", 13)
                p.multi_cell(0, 6.5, l[2:], align="C", new_x="LMARGIN", new_y="NEXT")
                p.ln(3)
            elif l.startswith("## "):
                p.ln(2)
                p.set_font("Helvetica", "B", 11)
                p.multi_cell(0, 6, l[3:], new_x="LMARGIN", new_y="NEXT")
                p.ln(1)
            elif l.startswith("|"):
                if set(l.replace("|", "").strip()) <= set("- "):
                    continue
                p.set_font("Helvetica", size=9)
                p.multi_cell(0, 4.8, "  ".join(c.strip() for c in l.strip("|").split("|")), new_x="LMARGIN", new_y="NEXT")
            else:
                l = l.lstrip("> ")
                p.set_font("Helvetica", size=10)
                recuo = 8 if re.match(r"^([a-n]\)|- )", l) else 0
                p.set_x(20 + recuo)
                p.multi_cell(0, 5, l, markdown=True, align="J", new_x="LMARGIN", new_y="NEXT")
                p.ln(1.2)
    return bytes(p.output())
