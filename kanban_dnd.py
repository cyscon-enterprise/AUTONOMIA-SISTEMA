"""Kanban com arrastar-e-soltar (st.components.v2): o card solto em outra coluna muda de etapa.

O componente so desenha e avisa o Python; quem grava a etapa e `dados.mover`, como no botao da ficha.
Eventos (trigger values): `mover` = {"id", "etapa", "n"} ao soltar numa coluna nova; `abrir` = {"id", "n"}
ao clicar no card. `n` (hora do clique) torna dois eventos iguais seguidos distinguiveis.

Todo texto vindo do banco entra por `textContent`, nunca por innerHTML: nomes de empresa nao viram HTML.
"""
from __future__ import annotations

import streamlit as st

CSS = """
.kb-board{display:flex;gap:10px;overflow-x:scroll;padding:2px 2px 10px;scrollbar-width:auto;
  scrollbar-color:#9fb0cc #e3e8f2}
.kb-board::-webkit-scrollbar{height:14px}
.kb-board::-webkit-scrollbar-track{background:#e3e8f2;border-radius:8px}
.kb-board::-webkit-scrollbar-thumb{background:#9fb0cc;border-radius:8px;border:3px solid #e3e8f2}
.kb-col{flex:0 0 205px;background:#eef2f9;border-radius:10px;display:flex;flex-direction:column;
  max-height:72vh;font-family:"Source Sans Pro",sans-serif}
.kb-col.over{background:#dbe7fb;outline:2px dashed #1565c0}
.kb-head{display:flex;justify-content:space-between;align-items:center;background:#fff;margin:6px;
  border-radius:8px;border-top:4px solid #94a3b8;padding:6px 9px;font-weight:600;color:#0f2a5c;font-size:13px}
.kb-head b{background:#eef2f9;border-radius:10px;padding:0 7px;font-size:11px;color:#5b6b85}
.kb-list{overflow-y:auto;padding:0 6px 6px;flex:1;min-height:60px}
.kb-card{background:#fff;border:1px solid #dfe5f0;border-radius:8px;padding:8px 9px;margin-bottom:6px;
  height:104px;box-sizing:border-box;cursor:grab;box-shadow:0 1px 2px rgba(15,42,92,.06);overflow:hidden}
.kb-card:hover{border-color:#1565c0}
.kb-card.drag{opacity:.45}
.kb-nome{font-weight:700;color:#0f2a5c;font-size:13px;line-height:1.25;height:2.5em;overflow:hidden;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.kb-l{color:#5b6b85;font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:3px}
.kb-l.late{color:#b42318;font-weight:600}
.kb-selo{display:inline-block;font-size:10.5px;padding:1px 6px;border-radius:4px;margin-top:3px}
.kb-selo.ok{background:#e7f6ec;color:#1a7f4b}.kb-selo.err{background:#fdecec;color:#b42318}
.kb-selo.esp{background:#fff7ed;color:#b0562a}
.kb-vazio{color:#8a94a6;font-size:12px;padding:4px 4px 8px}
"""

JS = """
export default function(component) {
  const { data, setTriggerValue, parentElement } = component;
  let root = parentElement.querySelector('.kb-board');
  const scroll = root ? root.scrollLeft : 0;               // redesenho nao volta o quadro para o inicio
  if (root) root.remove();
  root = document.createElement('div');
  root.className = 'kb-board';
  parentElement.appendChild(root);
  const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls;
                                  if (txt !== undefined) e.textContent = txt; return e; };
  let arrastando = null;

  for (const etapa of data.etapas) {
    const col = el('div', 'kb-col');
    const head = el('div', 'kb-head');
    head.style.borderTopColor = etapa.cor || '#94a3b8';
    head.appendChild(el('span', '', etapa.nome));
    const cards = data.cards.filter(c => c.etapa === etapa.nome);
    const cont = el('b', '', String(cards.length));
    head.appendChild(cont);
    col.appendChild(head);
    const lista = el('div', 'kb-list');
    if (!cards.length) lista.appendChild(el('div', 'kb-vazio', 'arraste um card para cá'));
    for (const c of cards) {
      const card = el('div', 'kb-card');
      card.draggable = true;
      card.title = c.nome;
      card.appendChild(el('div', 'kb-nome', c.nome));
      card.appendChild(el('div', 'kb-l', c.local));
      if (c.retorno) card.appendChild(el('div', 'kb-l' + (c.atrasado ? ' late' : ''), c.retorno));
      else card.appendChild(el('span', 'kb-selo ' + c.selo_cls, c.selo));
      card.appendChild(el('div', 'kb-l', '☎ ' + c.fone));
      card.addEventListener('dragstart', (e) => { arrastando = c; card.classList.add('drag');
                                                  e.dataTransfer.effectAllowed = 'move'; });
      card.addEventListener('dragend', () => { card.classList.remove('drag'); });
      card.addEventListener('click', () => setTriggerValue('abrir', { id: c.id, n: Date.now() }));
      lista.appendChild(card);
    }
    col.appendChild(lista);
    col.addEventListener('dragover', (e) => { e.preventDefault(); col.classList.add('over'); });
    col.addEventListener('dragleave', (e) => { if (!col.contains(e.relatedTarget)) col.classList.remove('over'); });
    col.addEventListener('drop', (e) => {
      e.preventDefault(); col.classList.remove('over');
      if (!arrastando || arrastando.etapa === etapa.nome) return;
      const vazio = lista.querySelector('.kb-vazio'); if (vazio) vazio.remove();
      const card = root.querySelector('.kb-card.drag'); if (card) lista.prepend(card);   // ja mostra no lugar novo
      setTriggerValue('mover', { id: arrastando.id, etapa: etapa.nome, n: Date.now() });
      arrastando = null;
    });
    root.appendChild(col);
  }
  root.scrollLeft = scroll;
}
"""

_componente = st.components.v2.component("kanban_cyscon", css=CSS, js=JS)


def quadro(etapas: list[dict], cards: list[dict], key: str = "kanban"):
    """Desenha o quadro. Devolve o resultado do componente: `.mover` e `.abrir` (ou None)."""
    return _componente(data={"etapas": etapas, "cards": cards}, key=key,
                       on_mover_change=lambda: None, on_abrir_change=lambda: None)
