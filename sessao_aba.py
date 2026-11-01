"""Login que sobrevive ao F5: enquanto a aba estiver aberta, a pessoa continua logada.

O Streamlit perde o `session_state` a cada recarga da pagina. Para nao pedir a senha de novo, o login
gera um token aleatorio que fica no `sessionStorage` do navegador (vale so para aquela aba e some ao
fechar a aba) e, no banco, so o hash dele (`crm_sessoes`), com a empresa escolhida e o login da 3C.
Ao recarregar, um componente le o token da aba e o servidor reabre a sessao.

Expira depois de SESSAO_OCIOSA_H horas sem uso. "Sair" apaga o token da aba e a linha do banco.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets

import sqlalchemy as sa
import streamlit as st

import dados

SESSAO_OCIOSA_H = 12
CHAVE = "autonomia_seller_sessao"

DDL = """CREATE TABLE IF NOT EXISTS crm_sessoes (
    token_hash CHAR(64) PRIMARY KEY,
    vendedor_id INTEGER NOT NULL REFERENCES crm_vendedores(id) ON DELETE CASCADE,
    empresa TEXT,
    sessao3c TEXT,
    criado_em TIMESTAMP NOT NULL,
    usado_em TIMESTAMP NOT NULL)"""

JS = """
export default function(component) {
  const { data, setStateValue } = component;
  const chave = data.chave;
  try {
    if (data.gravar) window.sessionStorage.setItem(chave, data.gravar);
    if (data.apagar) window.sessionStorage.removeItem(chave);
    setStateValue('token', window.sessionStorage.getItem(chave) || '');
  } catch (e) {
    setStateValue('token', '');                    // navegador sem sessionStorage: pede login normal
  }
}
"""

_componente = st.components.v2.component("sessao_aba", js=JS)


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def preparar() -> None:
    with dados.engine().begin() as conn:
        conn.execute(sa.text(DDL))


def ler_token(apagar: bool = False) -> str | None:
    """O token guardado nesta aba: None enquanto o navegador ainda nao respondeu, "" se nao ha.
    Com `apagar` (logo depois do Sair), tira o token da aba antes de ler."""
    r = _componente(data={"chave": CHAVE, "apagar": apagar}, key="sessao_aba_ler", on_token_change=lambda: None)
    return r.token


def gravar(vendedor_id: int) -> None:
    """Depois do login: cria a sessao no banco e guarda o token na aba."""
    token = secrets.token_urlsafe(32)
    agora = dados.agora()
    with dados.engine().begin() as conn:
        conn.execute(sa.text("DELETE FROM crm_sessoes WHERE usado_em < :lim"),
                     {"lim": agora - dt.timedelta(hours=SESSAO_OCIOSA_H)})
        conn.execute(sa.text("INSERT INTO crm_sessoes (token_hash, vendedor_id, criado_em, usado_em) "
                             "VALUES (:h, :v, :a, :a)"), {"h": _h(token), "v": vendedor_id, "a": agora})
    st.session_state["_token_aba"] = token


def escrever_na_aba() -> None:
    """Grava o token pendente no navegador (chamado em toda pagina enquanto houver um)."""
    token = st.session_state.get("_token_aba")
    if token:
        _componente(data={"chave": CHAVE, "gravar": token}, key="sessao_aba_gravar")


def retomar(token: str) -> dict | None:
    """Sessao valida para este token: {"vendedor_id", "empresa", "sessao3c"}; renova o prazo."""
    if not token:
        return None
    agora = dados.agora()
    with dados.engine().begin() as conn:
        r = conn.execute(sa.text("SELECT * FROM crm_sessoes WHERE token_hash = :h AND usado_em >= :lim"),
                         {"h": _h(token), "lim": agora - dt.timedelta(hours=SESSAO_OCIOSA_H)}).mappings().first()
        if not r:
            return None
        conn.execute(sa.text("UPDATE crm_sessoes SET usado_em = :a WHERE token_hash = :h"), {"a": agora, "h": _h(token)})
    return {"vendedor_id": r["vendedor_id"],
            "empresa": json.loads(r["empresa"]) if r["empresa"] else None,
            "sessao3c": json.loads(r["sessao3c"]) if r["sessao3c"] else None}


def salvar_estado(empresa: dict | None, sessao3c: dict | None) -> None:
    """Guarda na sessao a empresa escolhida e o login da 3C, para voltarem apos o F5."""
    token = st.session_state.get("_token_aba")
    if not token:
        return
    with dados.engine().begin() as conn:
        conn.execute(sa.text("UPDATE crm_sessoes SET empresa = :e, sessao3c = :s, usado_em = :a WHERE token_hash = :h"),
                     {"e": json.dumps(empresa) if empresa else None,
                      "s": json.dumps(sessao3c) if sessao3c else None, "a": dados.agora(), "h": _h(token)})


def encerrar() -> None:
    """Sair: apaga a sessao do banco e o token da aba."""
    token = st.session_state.get("_token_aba")
    if token:
        with dados.engine().begin() as conn:
            conn.execute(sa.text("DELETE FROM crm_sessoes WHERE token_hash = :h"), {"h": _h(token)})
