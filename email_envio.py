"""Envio de e-mail do sistema (codigo de confirmacao do cadastro).

Por enquanto sai pelo Gmail da Cyscon via SMTP, com uma "senha de app" do Google (nao a senha da conta).
Secrets: SMTP_USUARIO (o e-mail remetente) e SMTP_SENHA (a senha de app, 16 letras).
Opcionais: SMTP_HOST (padrao smtp.gmail.com) e SMTP_PORTA (padrao 465). Quando houver o dominio
autonomiavendas.com.br, basta trocar esses valores pelo provedor do dominio.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

REMETENTE_NOME = "AutonomIA Vendas"

_cfg: dict = {}


def configurar(usuario: str, senha: str, host: str = "", porta: str = "") -> None:
    _cfg.update(usuario=(usuario or "").strip(), senha=(senha or "").replace(" ", ""),
                host=(host or "smtp.gmail.com").strip(), porta=int(porta or 465))


def configurado() -> bool:
    return bool(_cfg.get("usuario") and _cfg.get("senha"))


def enviar(para: str, assunto: str, texto: str, html: str | None = None) -> dict:
    if not configurado() and os.environ.get("CRM_LOCAL") == "1":
        print(f"[email local, nao enviado] para={para} assunto={assunto}", flush=True)   # teste no PC
        return {"ok": True}
    if not configurado():
        return {"erro": "O envio de e-mail ainda não foi configurado no sistema. Avise o administrador."}
    msg = EmailMessage()
    msg["From"] = formataddr((REMETENTE_NOME, _cfg["usuario"]))
    msg["To"] = para
    msg["Subject"] = assunto
    msg.set_content(texto)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP_SSL(_cfg["host"], _cfg["porta"], context=ssl.create_default_context(), timeout=20) as s:
            s.login(_cfg["usuario"], _cfg["senha"])
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return {"erro": "O servidor de e-mail recusou o login do sistema. Avise o administrador."}
    except (smtplib.SMTPException, OSError):
        return {"erro": "Não foi possível enviar o e-mail agora. Tente de novo em alguns minutos."}
    return {"ok": True}


def enviar_codigo(para: str, nome: str, codigo: str) -> dict:
    primeiro = (nome or "").strip().split()[0] if (nome or "").strip() else ""
    texto = (f"Olá{', ' + primeiro if primeiro else ''}!\n\n"
             f"Seu código de confirmação do cadastro na AutonomIA Vendas é: {codigo}\n\n"
             "Ele vale por 15 minutos. Se você não pediu este cadastro, ignore este e-mail.\n")
    html = (f"<div style='font-family:Arial,sans-serif;font-size:15px;color:#1f2a44'>"
            f"<p>Olá{', ' + primeiro if primeiro else ''}!</p>"
            f"<p>Seu código de confirmação do cadastro na <b>AutonomIA Vendas</b> é:</p>"
            f"<p style='font-size:30px;font-weight:bold;letter-spacing:6px;color:#1565c0'>{codigo}</p>"
            f"<p>Ele vale por 15 minutos. Se você não pediu este cadastro, ignore este e-mail.</p></div>")
    return enviar(para, f"Seu código de confirmação: {codigo}", texto, html)
