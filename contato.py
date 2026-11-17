"""Registro dos e-mails que o vendedor manda ao cliente pela plataforma (comprovacao das regras da carteira).

Guarda remetente, destinatario, data e hora, assunto, texto integral, vendedor e o comprovante tecnico do envio
(Message-ID e a resposta do servidor de e-mail). Isso prova que o e-mail SAIU pela plataforma; nao prova que o
cliente recebeu ou leu. Resposta colada pelo vendedor e um registro declarado (o admin confere).
"""
from __future__ import annotations

import sqlalchemy as sa

import dados

DDL = """CREATE TABLE IF NOT EXISTS crm_emails_enviados (
    id SERIAL PRIMARY KEY,
    cnpj VARCHAR(14),
    lead_id INTEGER,
    vendedor_id INTEGER,
    vendedor_nome VARCHAR(120),
    remetente VARCHAR(160),
    destinatario VARCHAR(200) NOT NULL,
    assunto VARCHAR(300),
    texto TEXT NOT NULL,
    message_id VARCHAR(300),
    resposta_servidor VARCHAR(300),
    enviado_em TIMESTAMP NOT NULL)"""

_pronto: dict = {}


def _preparar(conn) -> None:
    if not _pronto:
        conn.execute(sa.text(DDL))
        _pronto["ok"] = True


def registrar_envio(cnpj, lead_id, vendedor: dict, destinatario: str, assunto: str, texto: str, r: dict) -> None:
    with dados.engine().begin() as conn:
        _preparar(conn)
        conn.execute(sa.text(
            "INSERT INTO crm_emails_enviados (cnpj, lead_id, vendedor_id, vendedor_nome, remetente, destinatario, "
            "assunto, texto, message_id, resposta_servidor, enviado_em) VALUES (:c, :l, :vi, :vn, :rem, :d, :a, :t, "
            ":m, :rs, :q)"),
            {"c": (cnpj or None), "l": lead_id, "vi": vendedor.get("id"), "vn": (vendedor.get("nome") or "")[:120],
             "rem": (r.get("remetente") or "")[:160], "d": destinatario[:200], "a": (assunto or "")[:300], "t": texto,
             "m": (r.get("message_id") or "")[:300], "rs": (r.get("resposta_servidor") or "")[:300],
             "q": dados.agora()})
