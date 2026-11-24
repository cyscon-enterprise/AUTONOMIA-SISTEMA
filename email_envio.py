"""Envio de e-mail do sistema (codigo de confirmacao do cadastro e e-mails do vendedor para o cliente).

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
from email.utils import formataddr, make_msgid

REMETENTE_NOME = "AutonomIA Vendas"

_cfg: dict = {}


def configurar(usuario: str, senha: str, host: str = "", porta: str = "") -> None:
    _cfg.update(usuario=(usuario or "").strip(), senha=(senha or "").replace(" ", ""),
                host=(host or "smtp.gmail.com").strip(), porta=int(porta or 465))


def configurado() -> bool:
    return bool(_cfg.get("usuario") and _cfg.get("senha"))


def enviar(para: str, assunto: str, texto: str, html: str | None = None, nome_remetente: str = "",
           responder_para: str = "", copia_oculta: bool = False, anexos: list | None = None) -> dict:
    """`responder_para`: a resposta do cliente vai para esse e-mail (o do vendedor). `copia_oculta`: a caixa
    da plataforma recebe uma copia (comprovacao do envio)."""
    if not configurado() and os.environ.get("CRM_LOCAL") == "1":
        print(f"[email local, nao enviado] para={para} assunto={assunto}", flush=True)   # teste no PC
        return {"ok": True, "message_id": make_msgid(domain="teste.local"), "remetente": "teste-local",
                "resposta_servidor": "teste local: nao enviado"}
    if not configurado():
        return {"erro": "O envio de e-mail ainda não foi configurado no sistema. Avise o administrador."}
    msg = EmailMessage()
    msg["From"] = formataddr((nome_remetente or REMETENTE_NOME, _cfg["usuario"]))
    msg["To"] = para
    if responder_para:
        msg["Reply-To"] = responder_para
    if copia_oculta:
        msg["Bcc"] = _cfg["usuario"]
    msg["Subject"] = assunto
    msg["Message-ID"] = make_msgid(domain=_cfg.get("usuario", "local@local").split("@")[-1])
    msg.set_content(texto)
    if html:
        msg.add_alternative(html, subtype="html")
    for nome_arq, conteudo, tipo in (anexos or []):          # (nome, bytes, "application/pdf")
        principal, _, sub = tipo.partition("/")
        msg.add_attachment(conteudo, maintype=principal, subtype=sub, filename=nome_arq)
    try:
        with smtplib.SMTP_SSL(_cfg["host"], _cfg["porta"], context=ssl.create_default_context(), timeout=20) as s:
            s.login(_cfg["usuario"], _cfg["senha"])
            recusados = s.send_message(msg)
            resposta = s.noop()
    except smtplib.SMTPAuthenticationError:
        return {"erro": "O servidor de e-mail recusou o login do sistema. Avise o administrador."}
    except (smtplib.SMTPException, OSError):
        return {"erro": "Não foi possível enviar o e-mail agora. Tente de novo em alguns minutos."}
    if recusados:
        return {"erro": "O servidor de e-mail recusou o destinatário. Confira o endereço."}
    return {"ok": True, "message_id": msg["Message-ID"], "remetente": _cfg["usuario"],
            "resposta_servidor": f"aceito pelo servidor {_cfg['host']} ({resposta[0]})"}


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



def enviar_codigo_cadastro(para: str, nome: str, codigo: str, anexos: list, docs: list[str], versao: str) -> dict:
    """Codigo de confirmacao + copia do contrato e das politicas que a pessoa acabou de aceitar na tela."""
    primeiro = (nome or "").strip().split()[0] if (nome or "").strip() else ""
    lista = "\n".join(f"- {d}" for d in docs)
    texto = (f"Olá{', ' + primeiro if primeiro else ''}!\n\n"
             f"Seu código de confirmação do cadastro na AutonomIA Vendas é: {codigo}\n\n"
             "Digite esse código na tela para concluir o cadastro. Ele vale por 15 minutos.\n\n"
             f"Em anexo estão o seu contrato e as políticas que você leu e aceitou (versão {versao}):\n{lista}\n\n"
             "Guarde estes arquivos. Depois que o seu cadastro for aprovado, você receberá as instruções para "
             "assinar o contrato pelo gov.br.\n\n"
             "Se você não pediu este cadastro, ignore este e-mail.\n")
    itens = "".join(f"<li>{d}</li>" for d in docs)
    html = (f"<div style='font-family:Arial,sans-serif;font-size:15px;color:#1f2a44'>"
            f"<p>Olá{', ' + primeiro if primeiro else ''}!</p>"
            f"<p>Seu código de confirmação do cadastro na <b>AutonomIA Vendas</b> é:</p>"
            f"<p style='font-size:30px;font-weight:bold;letter-spacing:6px;color:#1565c0'>{codigo}</p>"
            f"<p>Digite esse código na tela para concluir o cadastro. Ele vale por 15 minutos.</p>"
            f"<p>Em anexo estão o seu contrato e as políticas que você leu e aceitou (versão {versao}):</p><ul>{itens}</ul>"
            f"<p>Guarde estes arquivos. Depois que o seu cadastro for aprovado, você receberá as instruções para "
            f"assinar o contrato pelo gov.br.</p>"
            f"<p style='color:#5b6b85'>Se você não pediu este cadastro, ignore este e-mail.</p></div>")
    return enviar(para, f"Seu código de confirmação: {codigo}", texto, html, anexos=anexos)


def enviar_aprovacao(para: str, nome: str, prazo_txt: str) -> dict:
    primeiro = (nome or "").strip().split()[0] if (nome or "").strip() else ""
    texto = (f"Olá{', ' + primeiro if primeiro else ''}!\n\nSeu cadastro na AutonomIA Vendas foi aprovado. "
             "Você já pode entrar com o login e a senha que criou.\n\n"
             f"Pendência: assinar o contrato pelo gov.br e enviá-lo na própria plataforma até {prazo_txt}. "
             "Dentro da plataforma você baixa o contrato, assina e envia o arquivo assinado. "
             "Depois do prazo, o acesso fica bloqueado até a regularização.\n")
    return enviar(para, "Cadastro aprovado: assine o contrato em até 7 dias", texto)
