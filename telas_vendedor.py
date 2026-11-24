"""Paginas da area do vendedor (Discador, Minha carteira) e da Gestao (so administrador)."""
from __future__ import annotations

import datetime as dt
import html
import re
from urllib.parse import quote

import streamlit as st

import contato
import dados
import email_envio
import ia_ligacoes
import tresc3c as tresc
import vendedores as V

ss = st.session_state

TIPOS = {"ligacao": "ligação", "email": "e-mail", "whatsapp": "WhatsApp", "reuniao": "reunião", "nota": "nota"}
TIPOS_HIST = TIPOS | {"email_plataforma": "e-mail enviado pela plataforma", "email_resposta": "resposta do cliente ao e-mail"}


def eu() -> dict:
    return ss.usuario


def avisar(r: dict) -> None:
    if r.get("erro"):
        ss.msg = ("erro", r["erro"])
    else:
        ss.msg = ("ok", r.get("mensagem") or "Feito.")


def mostrar_msg() -> None:
    abrir_dialogo_ligacao()
    if ss.get("msg"):
        tipo, texto = ss.msg
        (st.error if tipo == "erro" else st.success)(texto)
        ss.msg = None


# ---------------------------------------------------------------- ligacao que nao saiu: pop-up com a causa e o que fazer

def _sem_escape(txt: str) -> str:
    """A 3C devolve o texto com \\u00e7 etc.: volta para letras de verdade."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), str(txt or ""))


def explicar_erro_ligacao(msg: str, conectado: bool, ramal: str) -> dict:
    """Traduz o erro da 3C em: titulo, causa e passos. `conectado`: o vendedor ja entrou no ramal pelo sistema."""
    t = _sem_escape(msg).lower()
    if "422" in t or "logado" in t or "agente" in t or "ramal nao esta" in t or "ramal não está" in t:
        passos = ([] if conectado else
                  ["No menu à esquerda, clique em **Conectar telefone** e entre com o seu ramal."])
        passos += [f"Confira se o ramal **{ramal}** está **logado e disponível** (não em pausa nem ocupado) no discador da 3C Plus.",
                   "Se acabou de entrar, espere uns 10 segundos para o ramal ficar disponível.",
                   "Clique no telefone do cliente de novo. Primeiro toca o seu ramal: atenda, e a 3C disca para o cliente."]
        causa = ("A 3C Plus recusou a ligação porque o seu ramal **não está logado ou não está disponível** agora."
                 + ("" if conectado else " Você ainda não conectou o telefone neste sistema."))
        return {"titulo": "Seu ramal não está conectado", "causa": causa, "passos": passos}
    if "já pedi" in t or "ja pedi" in t:
        return {"titulo": "Já existe um pedido de ligação para esse número",
                "causa": "Você pediu a ligação para esse mesmo telefone há poucos segundos.",
                "passos": [f"Veja se o ramal **{ramal}** está tocando ou em chamada.",
                           "Se não tocou, espere um minuto e tente de novo."]}
    if "401" in t or "403" in t or "token" in t:
        return {"titulo": "O sistema perdeu o acesso à 3C",
                "causa": "A 3C Plus recusou a chave de acesso do sistema (expirada ou sem permissão). Isso não é um problema seu.",
                "passos": ["Avise o administrador para renovar a chave da 3C nas configurações do site.",
                           "Enquanto isso, ligue pelo telefone web da 3C ou pelo discador."]}
    if "nao consegui falar" in t or "não consegui falar" in t or "timeout" in t:
        return {"titulo": "A 3C não respondeu",
                "causa": "O sistema não conseguiu falar com a 3C Plus agora (instabilidade de internet ou da 3C).",
                "passos": ["Espere cerca de um minuto e tente de novo.",
                           f"Antes de repetir, veja se o ramal **{ramal}** chegou a tocar: a ligação pode ter saído.",
                           "Se continuar, avise o administrador."]}
    return {"titulo": "A ligação não saiu",
            "causa": "A 3C Plus não aceitou o pedido de ligação.",
            "passos": ["Confira se o seu ramal está logado e disponível na 3C Plus.",
                       "Tente de novo em alguns segundos.", "Se repetir, envie o texto dos detalhes técnicos ao administrador."]}


@st.dialog("Não foi possível ligar")
def _dialogo_ligacao(info: dict) -> None:
    st.markdown(f"#### {info['titulo']}")
    st.write(info["causa"])
    st.markdown("**O que fazer:**")
    for i, p in enumerate(info["passos"], 1):
        st.markdown(f"{i}. {p}")
    with st.expander("Detalhes técnicos (para o administrador)"):
        st.code(info["tecnico"], language=None)
    if st.button("Entendi", type="primary", use_container_width=True):
        st.rerun()


def erro_ligacao(msg: str, ramal: str | None = None) -> None:
    """Em vez de uma faixa vermelha com o erro cru da 3C, guarda o aviso para abrir como pop-up na proxima tela."""
    ramal = ramal or (ss.get("sessao3c") or {}).get("ramal") or "1003"
    info = explicar_erro_ligacao(msg, bool(ss.get("sessao3c")), ramal)
    info["tecnico"] = _sem_escape(msg)[:600]
    ss.dialogo_ligacao = info


def abrir_dialogo_ligacao() -> None:
    sem = ss.pop("dialogo_sem_lista", None)
    if sem:
        _dialogo_sem_lista(sem)
        return
    linha = ss.pop("dialogo_linha", None)
    if linha:
        _dialogo_linha(linha)
        return
    info = ss.pop("dialogo_ligacao", None)
    if info:
        _dialogo_ligacao(info)


# ---------------------------------------------------------------- linha unica: so um usuario na 3C por vez

def linha_ocupada_por_outro() -> dict | None:
    """O turno aberto de OUTRO usuario (a 3C tem um ramal so), ou None se a linha esta livre ou e minha."""
    t = V.turno_aberto()
    return t if t and t["vendedor_id"] != eu()["id"] else None


def explicar_linha_ocupada(t: dict, em_ligacao: bool | None) -> dict:
    # so o gestor ve quem esta com o ramal; os demais recebem apenas o aviso generico
    return {"titulo": "Ramal ocupado",
            "causa": "O ramal está ocupado por outro usuário. "
                     "A 3C tem um único ramal, então só um usuário usa a linha por vez.",
            "passos": ["Espere o outro usuário terminar a ligação e encerrar o turno.",
                       "Tente de novo em alguns minutos: assim que o ramal ficar livre, você conecta normalmente.",
                       "Se continuar ocupado por muito tempo, avise o gestor."]}


@st.dialog("Linha ocupada")
def _dialogo_linha(info: dict) -> None:
    st.markdown(f"#### {info['titulo']}: outro usuário está em ligação")
    st.write(info["causa"])
    st.markdown("**O que fazer:**")
    for i, p in enumerate(info["passos"], 1):
        st.markdown(f"{i}. {p}")
    if st.button("Entendi", type="primary", use_container_width=True):
        st.rerun()


def aviso_linha_ocupada(t: dict) -> None:
    """Guarda o aviso para abrir como pop-up na proxima tela (o clique que tentou conectar ou ligar nao segue)."""
    em_lig = None
    try:
        s = tresc.status_ramal(tresc.ramal_padrao())
        em_lig = s in (2, 3) if s is not None else None
    except Exception:                       # 3C fora do ar: o aviso sai igual, sem o detalhe da ligacao
        em_lig = None
    ss.dialogo_linha = explicar_linha_ocupada(t, em_lig)


def faixa(icone: str, titulo: str, sub: str = "", numeros: list[tuple[str, object]] | None = None) -> None:
    """Cabecalho das paginas: UMA linha fina azul com o titulo, o subtitulo e os numeros da pagina."""
    nums = "".join(f"<span style='margin-left:24px;color:#B9C8E6;font-size:13px'>{html.escape(str(r))} "
                   f"<b style='color:#FFFFFF;font-size:16px;margin-left:4px'>{html.escape(str(v))}</b></span>"
                   for r, v in (numeros or []))
    st.markdown(
        "<div style='background:linear-gradient(90deg,#0F2A5C,#1565C0);border-radius:10px;padding:4px 16px;"
        "margin:-6px 0 10px;display:flex;align-items:center;flex-wrap:wrap;color:#F6F8FC'>"
        f"<span style='font-size:15px;font-weight:700'>{icone} {titulo}</span>"
        f"<span style='margin-left:12px;color:#DCE6F7;font-size:13px'>{sub}</span>"
        f"<span style='flex:1'></span>{nums}</div>", unsafe_allow_html=True)


def kpi(col, rotulo: str, valor) -> None:
    col.markdown(f"<div class='kpi'><small>{rotulo}</small><div>{valor}</div></div>", unsafe_allow_html=True)


COLS_EMAIL = [("email", "geral"), ("email_2", "geral 2"), ("e_mail_site", "site"), ("e_mail_ti", "TI"),
              ("e_mail_rh", "RH"), ("e_mail_financeiro", "Financeiro"), ("e_mail_fiscal", "Fiscal"),
              ("e_mail_contabil", "Contábil")]


def emails_de(reg: dict) -> list[tuple[str, str]]:
    """(e-mail, de onde veio) de uma empresa ou lead, sem repetir. Pega tambem e-mails escritos
    no meio dos campos de contato (ex.: "Carlos - carlos@empresa.com.br")."""
    vistos: dict[str, str] = {}
    campos = COLS_EMAIL + [(k, k.replace("contato_", "").replace("_", " ")) for k in reg if k.startswith("contato")]
    for col, rot in campos:
        for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", str(reg.get(col) or "")):
            vistos.setdefault(e.lower().rstrip("."), rot)
    return list(vistos.items())


def celular(tel: str) -> bool:
    return len(tel) == 11 and tel[2] == "9"


_EMAIL_OK = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def botoes_contato(nome_empresa: str, emails: list[tuple[str, str]], telefones: list[dict], chave: str,
                   registrar=None, cnpj: str | None = None, lead_id: int | None = None) -> None:
    """Espaco de contato da ficha: e-mail enviado DE DENTRO da plataforma (fica no historico como comprovacao,
    com copia na caixa da plataforma e a resposta indo para o vendedor) e WhatsApp (abre a conversa e registra).
    `registrar(tipo, texto) -> dict` grava no historico da empresa/lead."""
    vend = eu() or {}
    nome_v = vend.get("nome") or ss.get("quem") or ""
    primeiro = nome_v.split(" ")[0]
    empresa = ss.get("empresa", {}).get("nome") or "Cyscon Consultoria"
    assinatura = "\n".join(x for x in [nome_v, f"{empresa} · parceira TOTVS", vend.get("celular"), vend.get("email")] if x)
    aba_em, aba_wa = st.tabs(["✉ E-mail", "💬 WhatsApp"])

    with aba_em:
        op = {f"{e} · {r}": e for e, r in emails} | {"✏ outro e-mail…": ""}
        escolha = st.selectbox("Para", list(op), key=f"em{chave}")
        para = op[escolha] or st.text_input("E-mail do destinatário", key=f"emo{chave}",
                                            placeholder="nome@empresa.com.br")
        assunto = st.text_input("Assunto", value=f"{empresa} | {nome_empresa}", key=f"ema{chave}")
        corpo = st.text_area("Mensagem", height=170, key=f"emc{chave}",
                             value=(f"Olá, tudo bem?\n\nAqui é {primeiro}, da {empresa}, parceira TOTVS.\n\n"
                                    "Conforme conversamos, segue o contato.\n\nFico à disposição.\n\n" + assinatura))
        if st.button("✉ Enviar e-mail", type="primary", use_container_width=True, key=f"emb{chave}"):
            para = (para or "").strip()
            if not _EMAIL_OK.fullmatch(para):
                st.error("Confira o e-mail do destinatário.")
            elif not corpo.strip():
                st.error("Escreva a mensagem.")
            else:
                with st.spinner("Enviando…"):
                    r = email_envio.enviar(para, assunto.strip() or nome_empresa, corpo,
                                           nome_remetente=f"{nome_v} | {empresa}" if nome_v else empresa,
                                           responder_para=vend.get("email") or "", copia_oculta=True)
                if r.get("erro"):
                    st.error(r["erro"])
                else:
                    contato.registrar_envio(cnpj, lead_id, vend, para, assunto, corpo, r)
                    if registrar:
                        registrar("email_plataforma", f"De: {r.get('remetente')}\nPara: {para}\nAssunto: {assunto}\n"
                                                      f"Comprovante: {r.get('message_id')}\n\n{corpo}")
                    ss.msg = ("ok", f"E-mail enviado para {para} e registrado no histórico.")
                    st.rerun()
        st.caption("Sai pelo e-mail da plataforma, com o seu nome; a resposta do cliente chega no seu e-mail. "
                   "Fica registrado no histórico e vale como tentativa de contato. Use para o contato inicial e para "
                   "enviar material: propostas, preços e condições são tratados pela equipe da Cyscon.")
        with st.popover("📩 O cliente respondeu", use_container_width=True):
            with st.form(f"resp{chave}", clear_on_submit=True):
                resp = st.text_area("Cole aqui a resposta do cliente (e quem respondeu)")
                if st.form_submit_button("Registrar resposta", type="primary", use_container_width=True):
                    if not resp.strip():
                        st.error("Cole a resposta.")
                    elif registrar:
                        avisar(registrar("email_resposta", resp.strip()))
                        st.rerun()

    with aba_wa:
        cels = [t for t in telefones if celular(t["tel"])]
        if not cels:
            st.caption("WhatsApp precisa de um celular (11 dígitos começando com 9). "
                       "Inclua um em ➕ Incluir contato.")
        else:
            op = {f"{t['exibir']} · {', '.join(t['rotulos'])}": t for t in cels}
            t = op[st.selectbox("Celular", list(op), key=f"wa{chave}")]
            texto = st.text_area("Mensagem", height=110, key=f"wat{chave}",
                                 value=f"Olá! Aqui é {primeiro}, da {empresa}, parceira TOTVS. Tudo bem?")
            c1, c2 = st.columns(2)
            c1.link_button("💬 Abrir conversa", f"https://wa.me/55{t['tel']}?text={quote(texto)}",
                           use_container_width=True)
            if c2.button("✔ Registrar que mandei", use_container_width=True, key=f"wab{chave}") and registrar:
                avisar(registrar("whatsapp", f"WhatsApp para {t['exibir']}:\n{texto}"))
                st.rerun()
            st.caption("Abre o WhatsApp com a mensagem pronta. Depois de mandar, clique em Registrar.")


def incluir_contato(cnpj: str, lead_id: int | None = None, registrar=None) -> None:
    """Incluir um contato novo na ficha: telefone (vira botao de ligar pela 3C), e-mail (entra na lista de
    destinatarios) ou WhatsApp (celular, entra na aba WhatsApp). `registrar(tipo, texto)` grava no historico
    quando a ficha nao tem lead do funil."""
    quem = ss.get("quem") or eu()["nome"]
    with st.popover("➕ Incluir contato", use_container_width=True):
        tipo = st.radio("O que incluir?", ["Telefone", "E-mail", "WhatsApp"], horizontal=True, key=f"tipoc{cnpj}")
        with st.form(f"novoc{cnpj}{tipo}", clear_on_submit=True):
            if tipo == "E-mail":
                valor = st.text_input("E-mail", placeholder="nome@empresa.com.br")
            elif tipo == "WhatsApp":
                valor = st.text_input("Celular com DDD", placeholder="(11) 91234-5678")
            else:
                valor = st.text_input("Telefone com DDD", placeholder="(11) 91234-5678")
            rot = st.text_input("De quem é (opcional)", placeholder="ex.: Carlos, gerente de TI")
            if st.form_submit_button({"Telefone": "Salvar telefone", "E-mail": "Salvar e-mail", "WhatsApp": "Salvar WhatsApp"}[tipo],
                                       type="primary", use_container_width=True):
                if tipo == "E-mail":
                    r = dados.adicionar_email(cnpj, valor, rot, quem, lead_id)
                elif tipo == "WhatsApp":
                    r = dados.adicionar_whatsapp(cnpj, valor, rot, quem, lead_id)
                else:
                    r = dados.adicionar_telefone(cnpj, valor, rot, quem, lead_id)
                    r["texto"] = f"Telefone incluído: {valor.strip()} ({rot.strip() or 'incluído'})"
                    r["novo"] = "já estava" not in r.get("mensagem", "")
                if r.get("ok") and r.get("novo") and registrar and not lead_id:
                    registrar("nota", r["texto"])          # ficha da carteira: o historico da empresa
                avisar(r)
                st.rerun()


def emails_da_ficha(reg: dict, cnpj: str | None) -> list[tuple[str, str]]:
    """E-mails do cadastro mais os que foram incluidos a mao (sem repetir)."""
    vistos = dict(emails_de(reg))
    for e, rot in dados.emails_extras(cnpj):
        vistos.setdefault(e.lower(), rot)
    return list(vistos.items())


def bloco_ia(chamada_3c: str, contexto: str, titulo: str, salvar_no_historico) -> None:
    """Debaixo de uma ligacao gravada: o resumo da IA, ou o botao que ouve, transcreve e resume."""
    feito = ia_ligacoes.resultado(chamada_3c)
    if feito:
        with st.expander("🧠 Resumo da ligação (IA)", expanded=False):
            st.text(feito["resumo"] or "")
        return
    if st.button("🧠 Transcrever e resumir", key=f"ia{chamada_3c}"):
        try:
            with st.spinner("Ouvindo a gravação e transcrevendo (pode levar 1 a 2 minutos)…"):
                r = ia_ligacoes.processar(chamada_3c, contexto, ss.get("quem") or eu()["nome"])
            if r["novo"]:
                salvar_no_historico(f"{titulo}\n{r['resumo']}")
            ss.msg = ("ok", "Resumo pronto e incluído no histórico.")
        except ia_ligacoes.ErroIA as e:
            ss.msg = ("erro", f"IA: {e}")
        st.rerun()


# enderecos que nao sao o site da empresa: governo, tribunais, cadastros de CNPJ, buscadores, redes sociais
SITE_NAO_E_DA_EMPRESA = re.compile(
    r"(\.gov\.br|\.jus\.br|\.leg\.br|\.mp\.br|cnpj|econodata|casadosdados|empresaqui|solutudo|guiamais|telelistas|"
    r"apontador|facebook\.|instagram\.|linkedin\.|consultasocio|serasa|jusbrasil|escavador|informecadastral|"
    r"listamais|google\.)", re.I)


def site_da_empresa(url) -> str | None:
    """So o endereco principal (https://dominio) e so se o dominio for mesmo da empresa: o cadastro
    as vezes traz um PDF ou uma pagina de governo/diretorio no lugar do site."""
    m = re.match(r"^\s*(?:https?://)?(?:www\.)?([^/\s?#]+\.[a-z]{2,}(?:\.[a-z]{2})?)", str(url or ""), re.I)
    if not m or SITE_NAO_E_DA_EMPRESA.search(m.group(1)):
        return None
    return f"https://{m.group(1).lower()}"


def dados_compactos(campos: list[tuple[str, str]], reg: dict) -> None:
    """Os dados do cadastro num bloco so, linha colada na outra (um st.markdown por campo deixa um
    espaco grande entre as linhas). O site aparece limpo e como link, ou nao aparece."""
    linhas = []
    for k, r in campos:
        v = reg.get(k)
        if k == "site_da_empresa":
            v = site_da_empresa(v)
            if v:
                linhas.append(f"<div style='margin:0 0 3px'><b>{html.escape(r)}:</b> "
                              f"<a href='{html.escape(v)}' target='_blank'>{html.escape(v[8:])}</a></div>")
            continue
        if v:
            linhas.append(f"<div style='margin:0 0 3px'><b>{html.escape(r)}:</b> {html.escape(str(v))}</div>")
    st.markdown("<div style='font-size:14px;line-height:1.45'>" + "".join(linhas) + "</div>"
                if linhas else "<span style='color:#888'>Sem dados.</span>", unsafe_allow_html=True)


def br(v) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        try:
            v = dt.datetime.fromisoformat(v)
        except ValueError:
            return v
    return v.strftime("%d/%m %H:%M")


@st.cache_data(ttl=3600, show_spinner="Buscando a gravação na 3C…")
def _audio(chamada_id: str):
    return tresc.gravacao(chamada_id)


def _sincronizar() -> None:
    r = V.sincronizar()
    if r.get("erro"):
        st.caption(f"3C: {r['erro']}")


# ---------------------------------------------------------------- Discador

def _abrir_turno() -> bool:
    t = V.turno_aberto()
    if t and t["vendedor_id"] == eu()["id"]:
        return True
    r = V.iniciar_turno(eu())
    if r.get("erro"):
        outro = linha_ocupada_por_outro()
        if outro:
            aviso_linha_ocupada(outro)
        else:
            ss.erro3c = r["erro"]
        return False
    return True


def _conectar(ramal: str | None = None, senha: str | None = None) -> None:
    if not _abrir_turno():
        return
    try:
        ss.sessao3c = tresc.autenticar(ramal, senha) if ramal else tresc.autenticar_padrao()
    except tresc.Erro3C as e:
        msg = str(e)
        ss.erro3c = ("Ramal ou senha da 3C incorretos." if "incorretos" in msg
                     else f"Não consegui conectar na 3C: {msg}")


@st.fragment(run_every="60s")
def batimento() -> None:
    """Enquanto a pagina do dono do turno estiver aberta, avisa o servidor. Aba fechada = sem sinal = o turno
    fecha sozinho (V.INATIVO_MIN) e libera o ramal."""
    V.sinal_turno(eu()["id"])


def conectar_3c_lateral() -> None:
    """Barra lateral, antes de conectar: um clique (senha da 3C guardada nos Secrets) ou, se ela nao
    estiver configurada, o ramal e a senha."""
    abrir_dialogo_ligacao()
    t = V.turno_aberto()
    if t and t["vendedor_id"] != eu()["id"]:
        st.caption(f"🔴 Ramal ocupado por **{t['nome']}** desde {t['inicio']:%H:%M}." if eu().get("admin")
                   else "🔴 Ramal ocupado por outro usuário.")
    if tresc.tem_senha_ramal():
        if st.button("📞 Conectar telefone", type="primary", use_container_width=True):
            with st.spinner("Conectando na 3C…"):
                _conectar()
            st.rerun()
    else:
        with st.form("conectar3c"):
            ramal = st.text_input("Ramal da 3C", value=tresc.ramal_padrao())
            senha = st.text_input("Senha da 3C", type="password")
            ok = st.form_submit_button("📞 Conectar telefone", type="primary", use_container_width=True)
        if ok:
            _conectar(ramal, senha)
            st.rerun()
    if ss.get("erro3c"):
        st.error(ss.pop("erro3c"))


def sair_3c() -> None:
    tresc.sair(ss.get("sessao3c"))
    ss.sessao3c = None
    ss.pop("ultima_chamada", None)


def faixa_turno(inicio, chamadas: int, novos: int, carteira: int) -> None:
    faixa("📞", "Discador", f"turno aberto desde {inicio:%H:%M}",
          [("Chamadas no turno", chamadas), ("Entraram na carteira", novos), ("Minha carteira", carteira)])


def quadro_espera(titulo: str, texto: str) -> None:
    """O quadro fixo do Discador quando nao ha ligacao: no mesmo lugar onde a empresa aparece ao atender."""
    with st.container(border=True):
        st.markdown(f"<div style='text-align:center;padding:30px 0 6px;font-size:26px;font-weight:700;color:#5b6b85'>"
                    f"{titulo}</div>", unsafe_allow_html=True)
        _, meio, _ = st.columns([1, 3, 1])
        texto = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", texto)
        meio.markdown(f"<div style='text-align:center;color:#5b6b85;padding-bottom:22px'>{texto}</div>",
                      unsafe_allow_html=True)


@st.cache_data(ttl=300, show_spinner=False)
def _com_contatos() -> set[int]:
    return tresc.campanhas_com_contatos()


def pagina_discador() -> None:
    mostrar_msg()
    t = V.turno_aberto()
    meu = bool(t and t["vendedor_id"] == eu()["id"])
    s3 = ss.get("sessao3c")
    if meu:
        atual = next((x for x in V.turnos_recentes(1) if x["id"] == t["id"]), {})
        faixa_turno(t["inicio"], atual.get("chamadas", 0), atual.get("carteira", 0), len(V.carteira(eu()["id"])))
        if s3:
            painel_na_linha()
        else:
            quadro_espera("📵 Telefone desconectado", "Clique em **📞 Conectar telefone** na barra lateral para voltar a ligar.")
            if st.button("■ Encerrar meu turno"):
                avisar(V.encerrar_turno(eu()))
                st.rerun()
    elif t:
        faixa("📞", "Discador", "Ligações automáticas da campanha")
        if eu().get("admin"):
            st.warning(f"Ramal ocupado por **{t['nome']}** desde {t['inicio']:%H:%M}. Se ela/ele fechou a página, o "
                       "turno fecha sozinho em poucos minutos; ou encerre em Gestão → Turnos do discador.")
        else:
            st.warning("Ramal ocupado por outro usuário. A 3C tem um usuário só: espere o turno dele acabar.")
    else:
        faixa("📞", "Discador", "Ligações automáticas da campanha")
        quadro_espera("📵 Telefone desconectado", "Para começar, clique em **📞 Conectar telefone** na barra lateral. "
                      "Depois clique em **▶ Começar a receber ligações** (o discador liga para você) ou em **☎ Prefiro ligar "
                      "manualmente** (você escolhe para quem ligar, pelas fichas).")
        st.caption("Só as chamadas feitas com o seu turno aberto vão para você.")

    meus = [x for x in V.turnos_recentes(7) if x["vendedor_id"] == eu()["id"]]
    with st.expander("Histórico de chamadas"):
        if not meus:
            st.caption("Nenhum turno nos últimos 7 dias.")
        linhas = []
        for x in meus[:10]:
            fim = f"{x['fim']:%H:%M}" if x["fim"] else "aberto"
            linhas.append(f"<div><b>{x['inicio']:%d/%m %H:%M} → {fim}</b> · {x['chamadas']} chamada(s) · "
                          f"{x['carteira']} entraram na carteira</div>")
        if linhas:
            st.markdown("<div style='font-size:13px;line-height:1.7;color:#344054'>" + "".join(linhas) + "</div>",
                        unsafe_allow_html=True)


# ---------------------------------------------------------------- campanha sem lista: avisar em vez de deixar esperando

@st.cache_data(ttl=30, show_spinner=False)
def _estado_campanha(campanha_id: int) -> dict | None:
    return tresc.estado_campanha(campanha_id)


def explicar_sem_lista(est: dict, com_lista: list[str]) -> dict:
    motivo = {"sem_lista": f"A campanha **{est['nome']}** não tem nenhuma lista de telefones carregada na 3C, então o "
                           "discador não tem para quem ligar.",
              "pausada": (f"A campanha **{est['nome']}** está **pausada** na 3C"
                          + (" e também **não tem nenhuma lista de telefones** carregada" if est["contatos"] <= 0 else "")
                          + ", então o discador não liga."),
              "horario": f"A campanha **{est['nome']}** está **fora do horário** de funcionamento"
                         f"{' (' + est['horario'] + ')' if est.get('horario') else ''}, então o discador não liga agora."
              }[est["problema"]]
    passos = []
    if est["problema"] == "sem_lista":
        passos.append("Avise o administrador para **carregar uma lista de contatos** nessa campanha (3C Plus → Campanhas → "
                      f"{est['nome']} → Listas) e deixá-la ativa.")
    elif est["problema"] == "pausada":
        passos.append("Avise o administrador para "
                      + ("**carregar uma lista de contatos e reativar a campanha**" if est["contatos"] <= 0
                         else "**reativar a campanha**")
                      + f" **{est['nome']}** na 3C Plus (Campanhas).")
    else:
        passos.append("Volte a tentar dentro do horário da campanha, ou peça ao administrador para ajustar o horário.")
    if com_lista:
        passos.append("Enquanto isso, clique em **⏸ Parar de receber ligações** e comece por uma campanha que tem lista: "
                      + ", ".join(f"**{n}**" for n in com_lista) + ".")
    passos.append("Ou use **☎ Prefiro ligar manualmente** e ligue pelos botões ☎ das fichas (Minha carteira e Funil New Names).")
    return {"titulo": "Sem lista para ligar", "causa": motivo, "passos": passos}


@st.dialog("Sem lista para ligar")
def _dialogo_sem_lista(info: dict) -> None:
    st.markdown("#### O discador não vai ligar")
    st.write(info["causa"])
    st.markdown("**O que fazer:**")
    for i, p in enumerate(info["passos"], 1):
        st.markdown(f"{i}. {p}")
    if st.button("Entendi", type="primary", use_container_width=True):
        st.rerun()


def _campanhas_com_lista_nomes(s3: dict) -> list[str]:
    boas = _com_contatos()
    return [c["nome"] for c in (s3.get("campanhas") or []) if int(c["id"]) in boas]


def bloqueia_sem_lista(s3: dict, campanha_id) -> bool:
    """True (e guarda o aviso) se a campanha nao tem para quem ligar: nao adianta entrar e ficar esperando."""
    est = _estado_campanha(int(campanha_id))
    if est and est["problema"]:
        ss.dialogo_sem_lista = explicar_sem_lista(est, _campanhas_com_lista_nomes(s3))
        return True
    return False


PAGINA_DISCADOR = None          # o st.Page do Discador (app.py preenche): "Comecar a receber ligacoes" leva para la


def entrar_modo(s3: dict, campanha, manual: bool) -> None:
    if not manual and bloqueia_sem_lista(s3, campanha):
        st.rerun()                                      # o pop-up "Sem lista para ligar" abre na proxima tela
    try:
        tresc.entrar_campanha(s3["token"], campanha, manual=manual)
        s3["modo"] = "manual" if manual else "discador"
        s3["campanha_ativa"] = campanha
    except tresc.Erro3C as e:
        st.error(f"3C: {e}")
        return
    texto = ("Pronto. Se o telefone da barra lateral tocar, atenda: é a 3C conectando você."
             + (" Depois é só clicar nos ☎ das fichas." if manual else " Em seguida as ligações começam a chegar."))
    if not manual:                                      # de qualquer aba, vai direto para o Discador (app.py)
        ss.msg = ("ok", texto)
        ss._ir_discador = True
        st.rerun()                                      # dentro do fragmento da barra: roda a pagina inteira
    st.success(texto)


def controle_ligacao_manual() -> None:
    """Na ficha: se o ramal esta conectado no portal mas fora da campanha, oferece o modo manual
    (sem ele a 3C recusa o Click2Call com "ramal nao esta logado")."""
    s3 = ss.get("sessao3c")
    if not s3 or not s3.get("token"):
        st.caption("Conecte seu ramal na página **Discador** para ligar daqui.")
        return
    status = tresc.status_ramal(s3["ramal"])
    if status == 0:
        lista = s3.get("campanhas") or [{"id": s3.get("campanha"), "nome": s3.get("campanha_nome") or "da 3C"}]
        if st.button("☎ Entrar em modo manual para ligar", type="primary", use_container_width=True):
            entrar_modo(s3, lista[0]["id"], manual=True)
        st.caption("Seu ramal está fora da campanha. Entre em modo manual, atenda a conexão no telefone da barra "
                   "e depois clique no número.")
    elif status == 1 and s3.get("modo") != "manual":
        st.caption("Você está no discador automático: ele pode passar uma ligação a qualquer momento. "
                   "Para ligar só pelos botões, saia da campanha no Discador e entre em \"Só ligações manuais\".")


@st.fragment(run_every="10s")
def painel_ramal(s3: dict) -> None:
    """Barra lateral, acima do telefone (a vista, sem rolar): estado do ramal, campanha e Comecar/Parar."""
    status = tresc.status_ramal(s3["ramal"])
    rotulo = tresc.STATUS_AGENTE.get(status, f"status {status}") if status is not None else "sem resposta da 3C"
    if status == 0:                                     # o telefone web registrou, mas o ramal ainda nao esta na campanha
        rotulo = "ramal conectado"
    cor = {0: "#d97706", 1: "#1a7f4b", 2: "#1565c0", 3: "#d97706"}.get(status, "#8a94a6")
    st.markdown(f"<span class='ponto' style='background:{cor}'></span>**{rotulo[0].upper() + rotulo[1:]}**",
                unsafe_allow_html=True)
    if status in (2, 3):                                # em ligacao ou pos-atendimento: ligacao nova?
        ligacao_nova_fora_do_discador()
    if status == 0:
        _comecar(s3)
    elif status and st.button("⏸ Parar de receber ligações", use_container_width=True):
        tresc.sair(s3)
        st.rerun(scope="fragment")


def _comecar(s3: dict) -> None:
    """Barra lateral: a campanha (so as que tem telefones) e o botao de comecar."""
    todas = s3.get("campanhas") or [{"id": s3.get("campanha"), "nome": s3.get("campanha_nome") or "da 3C"}]
    nn = tresc.campanha_new_names(todas)
    lista = [c for c in todas if not tresc.eh_new_names(c)] or todas     # New Names tem botao proprio
    boas = _com_contatos()
    lista = [c for c in lista if int(c["id"]) in boas] or lista
    camps = {c["nome"]: c["id"] for c in lista}
    if len(camps) > 1:
        nome = st.selectbox("Campanha", list(camps))
    else:
        nome = next(iter(camps))
        st.caption(f"Campanha: **{nome}**")
    if st.button("▶ Começar a receber ligações", type="primary", use_container_width=True,
                 help="O discador liga sozinho para a base e passa para você quem atender."):
        entrar_modo(s3, camps[nome], manual=False)
    if st.button("🆕 Discar New Names (automático)", use_container_width=True,
                 help="O discador liga sozinho só para os New Names (empresas que não são clientes ativos da "
                      "TOTVS) e passa para você quem atender."):
        if nn:
            entrar_modo(s3, nn["id"], manual=False)
        else:
            st.warning("A campanha dos New Names ainda não existe na 3C. Crie a campanha, coloque seu ramal "
                       "nela e informe o id em CAMPANHA_NEW_NAMES (ou dê a ela um nome com \"New Names\").")
    if st.button("☎ Prefiro ligar manualmente", use_container_width=True,
                 help="O discador fica parado; você liga pelos botões ☎ das fichas."):
        entrar_modo(s3, camps[nome], manual=True)


@st.fragment(run_every="10s")
def painel_na_linha() -> None:
    """Atualiza sozinho: le as chamadas novas, mostra quem o discador conectou e da a baixa na 3C."""
    _sincronizar()
    with st.container(border=True):
        n = V.na_linha()
        if n and n.get("id") in ss.get("baixadas", set()):      # ja deu a baixa: a 3C ainda pode listar por alguns s
            n = None
        if n and n.get("id"):
            ss.ultima_chamada = n                       # some das ativas ao desligar: guarda para a baixa
        if not n and ss.get("ultima_chamada"):
            n = ss.ultima_chamada
            st.caption("Ligação encerrada: dê a baixa abaixo para o discador seguir.")
        if not n:
            s3 = ss.get("sessao3c") or {}
            if s3 and tresc.status_ramal(s3["ramal"]) == 0:          # conectado, mas ainda fora da campanha
                st.markdown("<div style='text-align:center;padding:30px 0 6px'><div style='font-size:30px;"
                            "font-weight:700;color:#1a7f4b'>📞 Ramal conectado</div><div style='color:#5b6b85;"
                            "margin-top:8px'>Na barra lateral, clique em <b>▶ Começar a receber ligações</b> (o "
                            "discador liga para você) ou em <b>☎ Prefiro ligar manualmente</b> (você escolhe para quem "
                            "ligar, pelas fichas).</div></div>", unsafe_allow_html=True)
                return
            camp = s3.get("campanha_ativa") or s3.get("campanha")
            est = _estado_campanha(int(camp)) if camp and s3.get("modo") != "manual" else None
            if est and est["problema"]:
                info = explicar_sem_lista(est, _campanhas_com_lista_nomes(s3))
                st.markdown("<div style='text-align:center;padding:26px 0 4px'><div style='font-size:28px;font-weight:700;"
                            "color:#b45309'>⚠ Não há ninguém para ligar</div></div>", unsafe_allow_html=True)
                st.warning(info["causa"])
                st.markdown("**O que fazer:**")
                for i, p in enumerate(info["passos"], 1):
                    st.markdown(f"{i}. {p}")
                return
            st.markdown("<div style='text-align:center;padding:38px 0 30px'><div style='font-size:30px;font-weight:700;"
                        "color:#1565c0'>⏳ Aguardando chamada…</div><div style='color:#5b6b85;margin-top:8px'>Quando "
                        "alguém atender, os dados da empresa aparecem aqui.</div></div>", unsafe_allow_html=True)
            return
        cartao_ligacao(n)


def cartao_ligacao(n: dict, onde: str = "tela") -> None:
    """Tudo da ligacao atendida: empresa, numero discado, post-it, baixa, insight, dados e contatos anteriores."""
    if True:
        f = V.ficha(n["cnpj"])
        if not f:
            st.caption(f"CNPJ {n['cnpj']} não está na mestra.")
            baixa_na_tela(n, onde)
            return
        e = f["empresa"]
        st.markdown(f"### {e.get('nome_fantasia') or e.get('nome')}")
        st.caption(" · ".join(x for x in [e.get("cidade"), e.get("estado"), e.get("entidade"), e.get("segmento"),
                                          e.get("produto") and f"ERP: {e['produto']}"] if x))
        if f["dono"] and f["dono"]["vendedor_id"] != eu()["id"]:
            st.warning(f"Este cliente já é da carteira de {f['dono']['nome']}.")
        esq, dir_ = st.columns([1.15, 1], gap="large")
        with esq:
            # o numero desta ligacao, em destaque: um ramal passado na conversa e o final DESTE telefone
            c_num, c_desl = st.columns([3, 1.2], vertical_alignment="center")
            c_num.markdown(f"<div style='display:inline-block;background:#E8F5EE;border:1px solid #1a7f4b;border-radius:8px;"
                           f"padding:3px 12px;margin:0 0 8px;font-size:15px;font-weight:600;color:#14532d'>📞 Número discado: "
                           f"{html.escape(dados.formatar(n.get('telefone')) or 'número não informado pela 3C')}</div>",
                           unsafe_allow_html=True)
            k = _k(n, onde)
            s3 = ss.get("sessao3c") or {}
            em_andamento = (not ss.get(f"desligada{k}") and bool(s3)
                            and tresc.status_ramal(s3["ramal"]) == 2)      # 2 = em ligacao na 3C
            if em_andamento:
                if c_desl.button("📴 Desligar", key=f"desl{k}", type="primary", use_container_width=True):
                    try:
                        tresc.desligar(n["id"], s3)
                    except tresc.Erro3C as e:
                        if "404" not in str(e):                # 404 = a chamada ja acabou: so segue para a qualificacao
                            st.error(f"3C: {e}")
                            return
                    ss[f"desligada{k}"] = True
                    ss.ultima_chamada = n                      # o cartao fica para a qualificacao
                    st.rerun(scope="fragment")                 # Discador e pop-up sao fragmentos
            else:
                st.markdown("<div style='background:#FFF4E5;border:1px solid #F0A350;border-radius:8px;padding:6px 12px;"
                            "margin:2px 0 10px;font-weight:600;color:#7a3e00'>📴 Ligação encerrada: qualifique a chamada "
                            "abaixo.</div>", unsafe_allow_html=True)
            baixa_na_tela(n, onde, em_andamento)
        with dir_:
            # tudo do cliente, na hora em que ele atende: insight, faturamento, porte, contatos, historico
            st.markdown("#### Insight")
            ins = V.insight(n["cnpj"])
            if not ins and ia_ligacoes.tem_chave():     # sem pesquisa: a IA monta um com os dados da empresa
                import ia_auto
                cadastro = "\n".join(f"{rot}: {e.get(col)}" for col, rot in
                                     CAMPOS_NA_LINHA + [("historico", "Histórico no CRM")] if e.get(col))
                if not e.get("site_da_empresa"):
                    cadastro += "\nSite: NÃO TEM site cadastrado"
                contatos = "\n".join(f"{br(h['quando'])} {h.get('texto') or ''}" for h in f["historico"][:6])
                ia_auto.preparar_insight(n["cnpj"], f"{cadastro}\n\nÚltimos contatos:\n{contatos}")
                st.markdown('<div class="insight" style="color:#5b6b85">🧠 Preparando o insight desta empresa… '
                            'aparece aqui em alguns segundos.</div>', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="insight">{html.escape(ins)}</div>' if ins else
                            '<div class="insight" style="color:#888">Sem pesquisa de insight para esta empresa ainda.</div>',
                            unsafe_allow_html=True)
            st.markdown("#### Dados da empresa")
            with st.container(border=True):
                dados_compactos(CAMPOS_NA_LINHA, e)
            if f["historico"] or e.get("historico"):
                st.markdown("#### Contatos anteriores")
                with st.container(border=True):
                    linhas = "".join(f"<div><b>{br(h['quando'])}</b> · {html.escape(h.get('quem') or '')} · "
                                     f"{html.escape(h.get('texto') or h.get('etapa_para') or '')}</div>"
                                     for h in f["historico"][:8])
                    if e.get("historico"):
                        linhas += (f"<div style='margin-top:6px'><b>Histórico anterior (CRM):</b> "
                                   f"{html.escape(str(e['historico']))}</div>")
                    st.markdown(f"<div style='font-size:14px;line-height:1.6;color:#344054'>{linhas}</div>",
                                unsafe_allow_html=True)


@st.dialog("📞 Ligação atendida", width="large")
def popup_ligacao(n: dict) -> None:
    """Abre por cima de qualquer aba quando o discador passa uma ligacao e o vendedor nao esta no Discador."""
    _popup_corpo(n)


@st.fragment(run_every="5s")
def _popup_corpo(n: dict) -> None:
    """Atualiza sozinho dentro do pop-up: percebe o fim da ligacao e mostra a qualificacao."""
    cartao_ligacao(n, "pop")


def ligacao_nova_fora_do_discador() -> None:
    """Chamado pela barra lateral (a cada 10 s, em todas as abas): ligacao nova + outra aba = pede o pop-up."""
    if ss.get("_pagina") == "discador":
        return
    n = V.na_linha()
    if not n or not n.get("id") or n["id"] in ss.get("baixadas", set()) or n["id"] == ss.get("_popup_visto"):
        return
    ss.ultima_chamada = n
    ss._popup = n
    ss._popup_visto = n["id"]
    st.rerun()                                          # a pagina inteira roda e o app.py abre o pop-up


CAMPOS_NA_LINHA = [
    ("nome", "Razão social"), ("nome_fantasia", "Nome fantasia"), ("cnpj", "CNPJ"),
    ("faturamento_totvs", "Faturamento TOTVS"), ("faturamento_estimado", "Faturamento estimado"),
    ("capital_social", "Capital social"), ("mrr", "MRR"), ("base", "Base"), ("produto", "ERP"),
    ("entidade", "Entidade"), ("segmento", "Segmento"), ("porte_da_empresa", "Porte"),
    ("quadro_de_funcionarios", "Funcionários"), ("funcionarios_linkedin", "Funcionários (LinkedIn)"),
    ("cnae_descricao", "Atividade"), ("data_de_abertura", "Abertura"), ("situacao_empresa", "Situação"),
    ("matriz", "Matriz/Filial"), ("cidade", "Cidade"), ("estado", "UF"), ("endereco", "Endereço"),
    ("email", "E-mail"), ("site_da_empresa", "Site"),
    ("contato_ti", "Contato TI"), ("contato_financeiro", "Contato Financeiro"), ("contato_fiscal", "Contato Fiscal"),
    ("contato_contabil", "Contato Contábil"), ("contato_rh", "Contato RH"),
    ("socios_rf", "Sócios (Receita)"), ("resultado", "Último resultado"),
]


def _salvar_postit(n: dict, qualif: str = "", k: str = "") -> None:
    """Grava o post-it no historico assim que o vendedor sai da caixa (e de novo na baixa, com a qualificacao)."""
    k = k or _k(n, "tela")
    texto = (ss.get(f"nota{k}") or "").strip()
    if not texto:
        return
    onde = ", ".join(x for x in (qualif, dados.formatar(n.get("telefone"))) if x)
    quem = ss.get("quem") or eu()["nome"]
    ss[f"nota_hist{n['id']}"] = V.anotar_ligacao(n["cnpj"], quem, f"Anotação da ligação ({onde}): {texto}",
                                                 ss.get(f"nota_hist{n['id']}"))
    novos = V.contatos_do_postit(n["cnpj"], texto, n.get("telefone"), quem)   # celular/e-mail anotado -> contatos
    if novos:
        ss[f"nota_contatos{n['id']}"] = ss.get(f"nota_contatos{n['id']}", []) + novos


def _k(n: dict, onde: str) -> str:
    """Sufixo unico das chaves do cartao: a ligacao + onde ele esta (aba Discador ou pop-up)."""
    return f"{onde}_{re.sub(r'[^A-Za-z0-9]', '', str(n.get('id') or ''))}"


def baixa_na_tela(n: dict, onde: str = "tela", em_andamento: bool = False) -> None:
    """Qualifica a chamada na 3C daqui mesmo; a sincronizacao leva o cliente para a carteira."""
    if not n.get("id"):
        return
    try:
        quals = tresc.qualificacoes_crm()
    except tresc.Erro3C as e:
        st.caption(f"3C: {e}")
        return
    # post-it amarelo: para anotar DURANTE a conversa; vai para o historico do lead ao dar a baixa
    st.markdown("<style>[class*='st-key-postit_'] textarea{background:#FFF59D !important;color:#3b3200 !important;"
                "font-size:17px !important;line-height:1.5 !important;border:1px solid #E6D96A !important;"
                "box-shadow:2px 3px 8px rgba(120,100,0,.18)}</style>", unsafe_allow_html=True)
    k = _k(n, onde)
    with st.container(key=f"postit_{k}"):
        nota = st.text_area("📝 Anotações da ligação · salvam sozinhas no histórico", key=f"nota{k}", height=150,
                            placeholder="Anote aqui durante a conversa: nome, cargo, ramal, sistema atual, próximo passo…",
                            on_change=_salvar_postit, args=(n, "", k))
        if ss.get(f"nota_hist{n['id']}"):
            extra = ss.get(f"nota_contatos{n['id']}")
            st.caption("✓ salvo no histórico do lead" + (f" · incluído nos contatos: {'; '.join(extra)}" if extra else ""))
    if em_andamento:                                    # a qualificacao so aparece quando a ligacao termina
        st.caption("Os botões de qualificação aparecem aqui assim que a ligação terminar.")
        return
    st.markdown("**Qualificação da chamada** · Retornar, Enviar e-mail ou Agendar reunião põem o cliente na sua carteira.")
    c1, c2 = st.columns(2)
    d = c1.date_input("Retorno", value=None, format="DD/MM/YYYY", key=f"rd{k}")
    h = c2.time_input("Hora", value=dt.time(9, 0), key=f"rh{k}")
    # nomes inteiros nos botoes: 3 por linha, letra um pouco menor e quebra de linha em vez de "..."
    st.markdown("<style>[class*='st-key-baixa_'] button{min-height:44px;padding:4px 8px}"
                "[class*='st-key-baixa_'] button p{font-size:14px;white-space:normal;overflow:visible;text-overflow:clip;"
                "line-height:1.2}</style>", unsafe_allow_html=True)
    # as que poem o cliente na carteira primeiro (Retornar no topo); depois as demais, na ordem da 3C
    if not (eu() or {}).get("admin"):                  # fechar venda e da Cyscon (contrato, Anexo I, item 2.1)
        quals = [q for q in quals if q["nome"].strip().lower() != "ganho"]
    primeiro = ["retornar", "enviar e-mail", "agendar reuniao", "agendar reunião", "ganho"]
    quals = sorted(quals, key=lambda q: (primeiro.index(q["nome"].strip().lower())
                                         if q["nome"].strip().lower() in primeiro else len(primeiro)))
    caixa = st.container(key=f"baixa_{k}")
    cols = caixa.columns(min(len(quals), 3) or 1)
    for i, q in enumerate(quals):
        if cols[i % len(cols)].button(q["nome"], key=f"q{k}_{q['id']}", use_container_width=True):
            ret = f"{d.isoformat()} {h:%H:%M}" if d else None
            try:
                with st.spinner(f"Dando a baixa: {q['nome']}…"):
                    tresc.baixar_chamada(n["id"], q["id"], nota, ret, ss.get("sessao3c"))
                if (nota or "").strip():
                    _salvar_postit(n, q["nome"], k)
                ss.pop("ultima_chamada", None)
                ss.setdefault("baixadas", set()).add(n["id"])      # some do quadro ja: volta ao "Aguardando chamada"
                st.toast(f"✅ Baixa dada: {q['nome']}", icon="✅")     # aparece na hora, mesmo com a tela recarregando
                ss.msg = ("ok", f"Baixa dada: {q['nome']}. Aguardando a próxima chamada.")
                st.rerun()
            except tresc.Erro3C as e:
                st.error(f"3C: {e}")


# ---------------------------------------------------------------- Carteira

def pagina_carteira() -> None:
    _carteira(todos=False)


def pagina_carteiras_todos() -> None:
    """So do administrador: a carteira de todos os vendedores, com quem e dono de cada cliente."""
    _carteira(todos=True)


def pagina_pool() -> None:
    """So do administrador: o pool de contas (empresas sem vendedor) e a atribuicao a um vendedor."""
    mostrar_msg()
    r = V.pool_resumo()
    faixa("🗃", "Pool de contas", "empresas ainda sem vendedor",
          [("No pool", f"{r['total']:,}".replace(",", ".")), ("New Names sem dono", len(r["new_names"]))])
    vends = [v for v in V.lista() if v["ativo"] and not v["admin"]]
    if not vends:
        st.info("Não há vendedores ativos para receber clientes.")
        return
    op = {v["nome"]: v["id"] for v in vends}
    aba_nn, aba_busca = st.tabs([f"New Names sem dono ({len(r['new_names'])})", "Buscar no pool"])

    def atribuir(escolhas: dict[str, str], chave: str) -> None:
        sel = st.multiselect("Clientes", list(escolhas), key=f"sel_{chave}")
        para = st.selectbox("Atribuir a", list(op), key=f"para_{chave}")
        if st.button("Atribuir selecionados", type="primary", key=f"btn_{chave}", disabled=not sel):
            avisar(V.atribuir_lote(eu(), [escolhas[s] for s in sel], op[para]))
            st.rerun()

    with aba_nn:
        if r["new_names_sem_cnpj"]:
            st.caption(f"{r['new_names_sem_cnpj']} lead(s) do funil sem CNPJ não entram no pool (a carteira é por CNPJ).")
        if not r["new_names"]:
            st.info("Nenhum New Names sem dono: todos estão com algum vendedor.")
        else:
            atribuir({f"{x['nome']} · {x['cidade'] or ''}/{x['estado'] or ''} · {x['cnpj']}": x["cnpj"]
                      for x in r["new_names"]}, "nn")
    with aba_busca:
        busca = st.text_input("Buscar empresa, CNPJ ou cidade (mín. 3 letras)", key="pool_busca")
        achou = V.pool_buscar(busca)
        if busca.strip() and len(busca.strip()) >= 3 and not achou:
            st.info("Nada encontrado no pool para essa busca.")
        elif achou:
            st.caption(f"{len(achou)} resultado(s) (máx. 40). Refine a busca para ver outros.")
            atribuir({f"{x['nome']} · {x['cidade'] or ''}/{x['estado'] or ''} · {x['cnpj']}": x["cnpj"]
                      for x in achou}, "busca")


def _carteira(todos: bool) -> None:
    mostrar_msg()
    _sincronizar()
    if ss.get("cliente"):
        ficha_cliente(ss.cliente)
        return
    lista = V.carteira(None if todos else eu()["id"])
    hoje = dados.agora().date().isoformat()
    faixa("👥" if todos else "📇", "Carteiras de todos os vendedores" if todos else "Minha carteira",
          "clientes com quem o time já falou" if todos else "clientes com quem você falou pelo discador",
          [("Clientes", len(lista)), ("Retornos hoje", sum(1 for l in lista if (l["proximo_contato"] or "")[:10] == hoje)),
           ("Atrasados", sum(1 for l in lista if l["atrasado"]))])
    c1, c2 = st.columns([3, 1])
    busca = c1.text_input("Buscar empresa, CNPJ ou cidade", label_visibility="collapsed",
                          placeholder="Buscar empresa, CNPJ ou cidade")
    so_atr = c2.checkbox("Só retornos atrasados")
    if busca.strip():
        b = busca.strip().lower()
        lista = [l for l in lista if b in f"{l['nome_exibir']} {l['nome']} {l['cnpj']} {l['cidade']}".lower()]
    if so_atr:
        lista = [l for l in lista if l["atrasado"]]
    aba_agenda, aba_lista = st.tabs(["Agenda", f"Clientes ({len(lista)})"])     # a agenda abre primeiro
    with aba_lista:
        if not lista:
            st.info("Nenhum cliente ainda. Os clientes com quem você falar pelo discador aparecem aqui.")
        # duas colunas: New Names (prospeccao) e Servicos (vindos das campanhas #SERVICO)
        col_nn, col_sv = st.columns(2, gap="large")
        for col, titulo, grupo, k in ((col_nn, "🗂 New Names", [l for l in lista if not l["servico"]], "cn"),
                                      (col_sv, "🛠 Serviços", [l for l in lista if l["servico"]], "cs")):
            with col:
                st.markdown(f"<div style='font-size:15px;font-weight:700;color:#0f2a5c;margin:4px 0 6px'>{titulo} "
                            f"({len(grupo)})</div>", unsafe_allow_html=True)
                if not grupo:
                    st.caption("nenhum cliente aqui")
                grade(grupo, lambda l, k=k: cartao(l, k), por_linha=2)
    with aba_agenda:
        hoje = dados.agora().date().isoformat()
        marcados = sorted([l for l in lista if l["proximo_contato"]], key=lambda l: l["proximo_contato"])
        for titulo, grupo in [("Hoje", [l for l in marcados if l["proximo_contato"][:10] == hoje]),
                              ("Atrasados", [l for l in marcados if l["proximo_contato"][:10] < hoje]),
                              ("Próximos", [l for l in marcados if l["proximo_contato"][:10] > hoje])]:
            st.markdown(f"<div style='font-size:15px;font-weight:700;color:#0f2a5c;margin:6px 0 2px'>{titulo} "
                        f"({len(grupo)})</div>", unsafe_allow_html=True)
            if not grupo:
                st.caption("nada aqui")
            grade(grupo, lambda l: cartao(l, "a"))


def grade(itens: list, desenhar, por_linha: int = 3) -> None:
    """Cartoes em linhas de `por_linha`: cada linha abre colunas novas, entao as linhas alinham."""
    for i in range(0, len(itens), por_linha):
        cols = st.columns(por_linha)
        for col, item in zip(cols, itens[i:i + por_linha]):
            with col:
                desenhar(item)


def cartao(l: dict, onde: str) -> None:
    with st.container(border=True, height=222):          # todos iguais: a grade fica alinhada
        dono = f" · 👤 {l['vendedor']}" if l.get("vendedor_id") != eu()["id"] else ""   # so na visao de todos
        retorno = (f"<span class='{'late' if l['atrasado'] else ''}'>{'atrasado' if l['atrasado'] else 'retorno'}: "
                   f"{br(l['proximo_contato'])}</span>") if l["proximo_contato"] else "&nbsp;"
        st.markdown(f"<div class='cart-nome' title=\"{l['nome_exibir']}\">{l['nome_exibir']}</div>"
                    f"<div class='cart-linha'>{l['cidade'] or ''}{('/' + l['estado']) if l['estado'] else ''} · "
                    f"{l['etapa_crm'] or 'sem contato'}{dono}</div><div class='cart-linha'>{retorno}</div>"
                    f"<div class='cart-linha'>na carteira desde {l['desde']:%d/%m} · {l['motivo'] or ''}</div>",
                    unsafe_allow_html=True)
        if st.button("Abrir", key=f"cli{onde}{l['cnpj']}", use_container_width=True):
            ss.cliente = l["cnpj"]
            st.query_params["ficha"] = "1"
            st.rerun()


def ficha_cliente(cnpj: str) -> None:
    f = V.ficha(cnpj)
    if st.button("← voltar à carteira"):
        ss.cliente = None
        st.query_params.pop("ficha", None)
        st.rerun()
    if not f:
        st.error("Empresa não encontrada na mestra.")
        return
    if not (eu().get("admin") or (f["dono"] and f["dono"]["vendedor_id"] == eu()["id"])):
        st.error("Esse cliente não está na sua carteira.")
        return
    e = f["empresa"]
    st.markdown(f"## {e.get('nome_fantasia') or e.get('nome')}")
    st.caption(" · ".join(x for x in [e.get("nome"), e["cnpj"], f"{e.get('cidade') or ''}/{e.get('estado') or ''}",
                                      e.get("entidade"), e.get("etapa_crm") or "sem contato",
                                      f["dono"] and f"carteira de {f['dono']['nome']}"] if x))
    esq, dir_ = st.columns([3, 2], gap="large")
    with esq:
        st.markdown("#### Dados da empresa")          # sempre aberto, antes do historico (como no funil New Names)
        with st.container(border=True):
            campos = [("nome", "Razão social"), ("nome_fantasia", "Nome fantasia"), ("cnpj", "CNPJ"), ("aba", "Aba"), ("entidade", "Entidade"), ("segmento", "Segmento"),
                      ("base", "Base"), ("mrr", "MRR"), ("produto", "ERP"), ("porte_da_empresa", "Porte"),
                      ("cnae_descricao", "Atividade"), ("quadro_de_funcionarios", "Funcionários"),
                      ("endereco", "Endereço"), ("bairro", "Bairro"), ("email", "E-mail"), ("site_da_empresa", "Site"),
                      ("contato_ti", "Contato TI"), ("contato_rh", "Contato RH"), ("contato_financeiro", "Contato Financeiro"),
                      ("contato_fiscal", "Contato Fiscal"), ("contato_contabil", "Contato Contábil"),
                      ("socios_rf", "Sócios (Receita)")]
            dados_compactos(campos, e)
        grav = [c for c in f["chamadas"] if c.get("gravada")]
        if grav:                                       # fora da caixa rolavel do historico: ninguem precisa procurar
            st.markdown(f"#### 🎧 Gravações ({len(grav)})")
            with st.container(border=True):
                for i, x in enumerate(grav):
                    st.markdown(f"**{br(x['quando'])}** · {dados.formatar(x.get('telefone'))} · "
                                f"{x.get('duracao') and 'conversa ' + x['duracao'] or 'sem conversa'} · "
                                f"{x.get('qualificacao') or 'sem qualificação'}")
                    chave = f"ouvir{x['chamada_3c']}"
                    if ss.get(chave):
                        try:
                            audio, fmt = _audio(x["chamada_3c"])
                            st.audio(audio, format=fmt)
                        except tresc.Erro3C as err:
                            st.caption(f"Gravação: {err}")
                    elif st.button("▶ ouvir gravação", key=chave + "b"):
                        ss[chave] = True
                        st.rerun()
                    bloco_ia(x["chamada_3c"], f"{e.get('nome_fantasia') or e.get('nome')} ({e.get('cidade') or ''})",
                             f"Resumo da ligação de {br(x['quando'])} para {dados.formatar(x.get('telefone'))}:",
                             lambda t, c=cnpj: V.registrar_resumo_ia(c, t))
                    if i < len(grav) - 1:
                        st.divider()
        st.markdown("#### Histórico")
        linha = [("ch", c["quando"], c) for c in f["chamadas"]]
        linha += [("h", h["quando"], h) for h in f["historico"] if not str(h.get("texto") or "").startswith("Discador 3C")]
        linha.sort(key=lambda x: x[1] or dt.datetime.min, reverse=True)
        with st.container(border=True):          # sem altura fixa: a pagina cresce, sem barra de rolagem propria
            if not linha and not e.get("historico"):
                st.caption("Sem histórico ainda.")
            for tipo, _, x in linha:
                if tipo == "ch":
                    st.markdown(f"**{br(x['quando'])}** · discador · {x.get('vendedor') or ''} · "
                                f"{dados.formatar(x.get('telefone'))}")
                    st.caption(" · ".join(v for v in [x.get("qualificacao") or "sem qualificação", x.get("resultado"),
                                                      x.get("duracao") and f"conversa {x['duracao']}"] if v))
                    if x.get("gravada"):
                        st.caption("🎧 gravada · ouça na seção Gravações, acima")
                else:
                    st.markdown(f"**{br(x['quando'])}**{(' · ' + x['quem']) if x.get('quem') else ''} · "
                                f"{TIPOS_HIST.get(x.get('tipo'), x.get('tipo') or '')}")
                    if x.get("etapa_para"):
                        st.caption(f"{x.get('etapa_de') or '—'} → {x['etapa_para']}")
                    if x.get("texto"):
                        st.text(x["texto"])
                st.divider()
            if e.get("historico"):
                st.markdown("**Na planilha**")
                st.text(e["historico"])
    with dir_:
        st.markdown("#### Contato")
        botoes_contato(e.get("nome_fantasia") or e.get("nome") or "", emails_da_ficha(e, cnpj), f["telefones"], cnpj,
                       lambda tipo, texto: V.registrar(eu(), cnpj, tipo, texto), cnpj=cnpj)
        st.markdown("#### Ligar pela 3C")
        controle_ligacao_manual()
        if not f["telefones"]:
            st.caption("Sem telefone.")
        ramal = (ss.get("sessao3c") or {}).get("ramal") or "1003"
        for t in f["telefones"]:
            if st.button(f"☎ {t['exibir']}  ·  {', '.join(t['rotulos'])}", key=f"lig{cnpj}{t['tel']}",
                         use_container_width=True):
                outro = linha_ocupada_por_outro()
                if outro:
                    aviso_linha_ocupada(outro)
                    st.rerun()
                with st.spinner("Pedindo a ligação à 3C (pode levar até 1 minuto)…"):
                    try:
                        tresc.ligar("55" + t["tel"], ramal)
                        V.registrar(eu(), cnpj, "ligacao", f"Ligação pedida à 3C para {t['exibir']} (ramal {ramal})")
                        ss.msg = ("ok", f"Ligando: o ramal {ramal} toca primeiro, atenda para discar.")
                    except tresc.Erro3C as err:
                        erro_ligacao(str(err), ramal)
                st.rerun()
        incluir_contato(cnpj, registrar=lambda tipo, texto: V.registrar(eu(), cnpj, tipo, texto))
        st.caption(f"O ramal {ramal} toca primeiro; ao atender, a 3C disca para o cliente. "
                   "Fora da campanha: com o discador ligando, use o telefone da barra.")
        st.markdown("#### Etapa e retorno")
        nomes = [x["nome"] for x in dados.etapas()]
        atual = e.get("etapa_crm") or ""
        nova = st.selectbox("Etapa", nomes, index=nomes.index(atual) if atual in nomes else 0)
        if st.button("Mudar etapa", use_container_width=True):
            avisar(V.mover(eu(), cnpj, nova))
            st.rerun()
        c1, c2 = st.columns(2)
        d = c1.date_input("Retorno", value=None, format="DD/MM/YYYY")
        h = c2.time_input("Hora", value=dt.time(9, 0))
        if st.button("Agendar retorno", use_container_width=True):
            avisar(V.agendar(eu(), cnpj, dt.datetime.combine(d, h) if d else None))
            st.rerun()
        if e.get("proximo_contato"):
            st.caption(f"Retorno marcado: {br(e['proximo_contato'])}")
        st.markdown("#### Registrar")
        with st.form("registrar_cli", clear_on_submit=True):
            tipo = st.selectbox("O que aconteceu", list(TIPOS), format_func=TIPOS.get)
            texto = st.text_area("Falei com quem, o que ficou combinado…")
            if st.form_submit_button("Registrar", type="primary"):
                avisar(V.registrar(eu(), cnpj, tipo, texto))
                st.rerun()
        if eu().get("admin"):
            st.markdown("#### Transferir (admin)")
            vs = [v for v in V.lista() if v["ativo"]]
            op = {"— devolver para a base —": None} | {v["nome"]: v["id"] for v in vs}
            alvo = st.selectbox("Para", list(op))
            if st.button("Transferir", use_container_width=True):
                avisar(V.transferir(eu(), cnpj, op[alvo]))
                ss.cliente = None
                st.query_params.pop("ficha", None)
                st.rerun()


# ---------------------------------------------------------------- Funil Servicos

def pagina_funil_servicos() -> None:
    """Kanban dos clientes que vieram do discador nas campanhas #SERVIÇO. A etapa e a da carteira
    (empresas.etapa_crm): arrastar aqui ou mudar na ficha da no mesmo."""
    import kanban_dnd

    mostrar_msg()
    _sincronizar()
    if ss.get("cliente"):
        ficha_cliente(ss.cliente)
        return
    admin = eu().get("admin")
    cards = V.funil_servicos(None if admin else eu()["id"])
    faixa("🛠", "Funil Serviços", "Clientes atendidos pelo discador nas campanhas #SERVIÇO"
          + (" · todos os vendedores" if admin else ""))
    if not cards:
        st.info("Nenhum cliente ainda. Quem você atender pelo discador numa campanha da 3C cujo nome começa com "
                "**#SERVIÇO** aparece aqui.")
    etapas = dados.etapas()
    primeira = etapas[0]["nome"] if etapas else ""
    st.caption("Arraste o card para outra coluna para mudar a etapa · clique no card para abrir a ficha")
    r = kanban_dnd.quadro(
        [{"nome": e["nome"], "cor": e["cor"]} for e in etapas],
        [{"id": c["cnpj"], "nome": c["nome_exibir"], "etapa": c["etapa_crm"] or primeira,
          "local": f"{c['cidade'] or ''}{('/' + c['estado']) if c['estado'] else ''}"
                   + (f" · {c['vendedor']}" if admin and c.get("vendedor") else ""),
          "retorno": (f"{'atrasado' if c['atrasado'] else 'retorno'}: {br(c['proximo_contato'])}"
                      if c["proximo_contato"] else ""),
          "atrasado": c["atrasado"], "selo": (c["campanha"] or "#SERVIÇO")[:40], "selo_cls": "ok",
          "fone": c["telefones"][0]["exibir"] if c["telefones"] else "sem telefone"} for c in cards],
        key="kanban_servicos")
    if r.mover and r.mover.get("n") != ss.get("kbs_ultimo"):
        ss.kbs_ultimo = r.mover["n"]
        res = V.mover(eu(), str(r.mover["id"]), r.mover["etapa"])
        if res.get("erro"):
            ss.msg = ("erro", res["erro"])
        st.rerun()
    if r.abrir and r.abrir.get("n") != ss.get("kbs_ultimo"):
        ss.kbs_ultimo = r.abrir["n"]
        ss.cliente = str(r.abrir["id"])
        st.query_params["ficha"] = "1"
        st.rerun()


# ---------------------------------------------------------------- Visao geral (admin)

def pagina_visao_geral() -> None:
    """Painel do administrador: funil, produtividade por vendedor e discador, numa tela."""
    import altair as alt
    import pandas as pd

    mostrar_msg()
    dias = st.segmented_control("Período", [1, 7, 30], default=7, format_func=lambda d: "hoje" if d == 1 else f"{d} dias",
                                label_visibility="collapsed") or 7
    g = V.visao_geral(dias)
    q = dados.quadro()
    vend = [v for v in g["vendedores"] if v["ativo"] and not v["admin"]]
    t = g["turno"]
    faixa("📊", "Visão geral", f"últimos {dias} dia(s) · atualizado {dados.agora():%d/%m %H:%M}",
          [("Discador agora", t["nome"] if t else "livre"), ("Chamadas hoje", g["chamadas_hoje"]),
           (f"Chamadas ({dias}d)", sum(v["chamadas"] for v in g["vendedores"])),
           (f"Novos na carteira ({dias}d)", sum(v["novos"] for v in g["vendedores"])), ("Vendedores ativos", len(vend))])

    esq, dir_ = st.columns([1, 1], gap="large")
    with esq:
        st.markdown("#### Funil New Names")
        cont = {e["nome"]: 0 for e in q["etapas"]}
        for c in q["cards"]:
            cont[c["etapa"]] = cont.get(c["etapa"], 0) + 1
        df = pd.DataFrame([{"etapa": e["nome"], "leads": cont[e["nome"]], "cor": e["cor"], "ordem": i}
                           for i, e in enumerate(q["etapas"])])
        st.altair_chart(
            alt.Chart(df).mark_bar(cornerRadiusEnd=4).encode(
                y=alt.Y("etapa:N", sort=alt.SortField("ordem"), title=None),
                x=alt.X("leads:Q", title="leads"),
                color=alt.Color("cor:N", scale=None), tooltip=["etapa", "leads"])
            + alt.Chart(df).mark_text(align="left", dx=4, color="#1b2a41").encode(
                y=alt.Y("etapa:N", sort=alt.SortField("ordem")), x="leads:Q", text="leads:Q"),
            use_container_width=True)
        atr = sum(1 for c in q["cards"] if c["atrasado"])
        st.caption(f"{len(q['cards'])} leads · {atr} retorno(s) atrasado(s)")
    with dir_:
        st.markdown("#### Chamadas do discador por dia")
        if g["por_dia"]:
            df = pd.DataFrame(g["por_dia"]).melt("dia", var_name="tipo", value_name="n")
            df["tipo"] = df["tipo"].map({"chamadas": "chamadas", "carteira": "entraram na carteira"})
            st.altair_chart(alt.Chart(df).mark_bar().encode(
                x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m")), xOffset="tipo:N",
                y=alt.Y("n:Q", title=None), color=alt.Color("tipo:N", title=None,
                                                             scale=alt.Scale(range=["#1565c0", "#22b5d8"])),
                tooltip=["dia:T", "tipo", "n"]), use_container_width=True)
        else:
            st.info("Nenhuma chamada do discador no período.")
        m = g["manuais"]
        st.caption(f"Ligações manuais pelas fichas: {m['total']} · atendidas: {m['atendidas']}")

    st.markdown("#### Vendedores")
    linhas = [{"Vendedor": v["nome"], "Perfil": "admin" if v["admin"] else "vendedor",
               "Situação": "ativo" if v["ativo"] else "desativado",
               "Horas no discador": round(float(v["horas"] or 0), 1), "Chamadas": v["chamadas"],
               "Novos na carteira": v["novos"], "Ligações manuais": v["manuais"],
               "Carteira": v["carteira"], "Retornos atrasados": v["atrasados"],
               "Último acesso": br(v["ultimo_acesso"]) or "nunca"} for v in g["vendedores"]]
    st.dataframe(linhas, hide_index=True, use_container_width=True)
    st.caption("Horas = tempo de turno aberto no discador. Os números são para acompanhar o time, "
               "não para cobrar meta (o vendedor é autônomo: ver o contrato).")


# ---------------------------------------------------------------- Gestao (admin)

def pagina_gestao() -> None:
    mostrar_msg()
    faixa("⚙", "Gestão", "Vendedores e turnos do discador")
    fila = V.pendentes()
    a_vend, a_turnos, a_ia = st.tabs([f"Vendedores{f' · {len(fila)} cadastro(s) novo(s)' if fila else ''}",
                                      "Turnos do discador", "IA (resumo das ligações)"])
    with a_ia:
        if ia_ligacoes.tem_chave():
            st.success("Chave da Anthropic configurada: o botão 🧠 Transcrever e resumir já funciona.")
        else:
            st.warning("Falta a chave da Anthropic. Sem ela a transcrição funciona, mas o resumo não.")
        with st.form("chave_ia", clear_on_submit=True):
            chave = st.text_input("Cole aqui a chave da Anthropic (começa com sk-ant-)", type="password")
            if st.form_submit_button("Salvar chave", type="primary"):
                avisar(ia_ligacoes.salvar_chave(chave))
                st.rerun()
        st.caption("No site publicado, a chave fica nos Secrets do Streamlit (ANTHROPIC_API_KEY).")
    with a_vend:
        if fila:
            st.markdown("#### Cadastros aguardando aprovação")
            for p in fila:
                with st.container(border=True):
                    c1, c2, c3 = st.columns([5, 1.3, 1.3])
                    tel = p["celular"] or ""
                    tel = f"({tel[:2]}) {tel[2:-4]}-{tel[-4:]}" if len(tel) >= 10 else tel
                    doc = p["documento"] or ""
                    doc = (f"CPF {doc[:3]}.{doc[3:6]}.{doc[6:9]}-{doc[9:]}" if len(doc) == 11 else
                           f"CNPJ {doc[:2]}.{doc[2:5]}.{doc[5:8]}/{doc[8:12]}-{doc[12:]}" if len(doc) == 14 else doc)
                    cn = p.get("emp_cnpj") or ""
                    cn = f"{cn[:2]}.{cn[2:5]}.{cn[5:8]}/{cn[8:12]}-{cn[12:]}" if len(cn) == 14 else cn
                    sn = lambda v: "—" if v is None else ("Sim" if v else "Não")
                    ender = ", ".join(x for x in (p.get("emp_logradouro"), p.get("emp_numero"), p.get("emp_complemento"),
                                                  p.get("emp_bairro")) if x)
                    c1.markdown(
                        f"**{p.get('emp_razao') or p['nome']}**"
                        f"{' (' + p['emp_fantasia'] + ')' if p.get('emp_fantasia') else ''} · CNPJ {cn}  \n"
                        f"{ender} · {p.get('emp_cidade') or ''}/{p.get('emp_uf') or ''} · CEP {p.get('emp_cep') or ''}  \n"
                        f"{p.get('emp_email') or ''} · {p.get('emp_telefone') or ''}  \n"
                        f"**Representante:** {p['nome']} · {doc} · {p['email'] or ''} · {tel} · login `{p['login']}`  \n"
                        f"**Perfil:** vendas {sn(p.get('exp_vendas'))}"
                        f"{' (' + str(p['exp_vendas_anos']) + ' anos)' if p.get('exp_vendas_anos') else ''}"
                        f" · TOTVS {sn(p.get('exp_totvs'))} · outro ERP {sn(p.get('exp_outro_erp'))}"
                        f"{' (' + p['exp_outro_erp_qual'] + ')' if p.get('exp_outro_erp_qual') else ''}  \n"
                        f"**Perfil de atuação:** {'Perfil ' + str(p['perfil_atuacao']) if p.get('perfil_atuacao') else '—'}  \n"
                        f"**Disponibilidade (informativa):** até {p.get('disp_vezes') or '?'}x por semana · "
                        f"{(p.get('disp_periodos') or '').replace(',', ', ')}  \n"
                        f"Cadastro em {br(p['criado_em'])} · aceitou os termos v{p['termos_versao']}")
                    if c2.button("✅ Aprovar", key=f"apr{p['id']}", type="primary"):
                        r = V.aprovar(p["id"], True)
                        if r.get("ok") and p.get("email"):
                            import datetime as _dt
                            import email_envio
                            email_envio.enviar_aprovacao(
                                p["email"], p["nome"], (dados.agora() + _dt.timedelta(days=V.PRAZO_CONTRATO_DIAS)).strftime("%d/%m/%Y"))
                        avisar(r)
                        st.rerun()
                    if c3.button("Recusar", key=f"rec{p['id']}"):
                        avisar(V.aprovar(p["id"], False))
                        st.rerun()
            st.markdown("#### Vendedores")
        for v in V.lista():
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
                if c4.button("Tornar vendedor" if v["admin"] else "Tornar administrador", key=f"adm{v['id']}"):
                    r = V.definir_admin(v["id"], not v["admin"])
                    avisar(r)
                    if v["id"] == eu()["id"] and not r.get("erro"):
                        ss.usuario["admin"] = not v["admin"]        # o menu muda ja na proxima tela
                    st.rerun()
                c1.markdown(f"**{v['nome']}** · `{v['login']}`{' · admin' if v['admin'] else ''}"
                            f"{'' if v['ativo'] else ' · _desativado_'}  \n"
                            f"{v['clientes']} cliente(s) · último acesso {br(v['ultimo_acesso']) or 'nunca'}")
                if v.get("emp_cnpj"):
                    with c2.popover("Pagamento ✓" if v.get("pg_atualizado_em") else "Pagamento pendente"):
                        pg = V.pagamento(v["id"])
                        if pg.get("pg_atualizado_em"):
                            st.markdown(
                                f"**Titular:** {pg['emp_razao']}  \n**CNPJ:** {pg['emp_cnpj']}  \n"
                                + (f"**Banco:** {pg['pg_banco']} · {pg['pg_tipo_conta']}  \n"
                                   f"**Agência:** {pg['pg_agencia']} · **Conta:** {pg['pg_conta']}  \n"
                                   if pg.get("pg_conta") else "")
                                + (f"**PIX ({pg['pg_pix_tipo']}):** {pg['pg_pix_chave']}  \n" if pg.get("pg_pix_chave") else "")
                                + f"Informado em {br(pg['pg_atualizado_em'])}")
                            if st.button("Liberar nova edição", key=f"lib{v['id']}"):
                                avisar(V.liberar_pagamento(v["id"]))
                                st.rerun()
                        else:
                            st.write("Ainda não informou. Ele vê a tela de pagamento no próximo acesso.")
                if v.get("contrato_status"):
                    rot = {"aguardando": "Contrato pendente", "enviado": "Contrato: conferir", "conferido": "Contrato ✓"}
                    with c2.popover(rot.get(v["contrato_status"], "Contrato")):
                        st.write(f"Situação: **{v['contrato_status']}**"
                                 + (f" · prazo até {br(v['contrato_prazo'])}" if v.get("contrato_prazo") else ""))
                        arq = V.contrato_assinado_arquivo(v["id"])
                        if arq:
                            st.download_button("⬇ Contrato assinado", arq[1], file_name=arq[0], mime="application/pdf",
                                               key=f"dla{v['id']}")
                            st.caption("Confira a assinatura em validar.iti.gov.br e veja o código do documento.")
                        if v["contrato_status"] == "enviado":
                            if st.button("✅ Assinatura conferida", key=f"cf{v['id']}", type="primary"):
                                avisar(V.conferir_contrato(v["id"], eu()["nome"], True))
                                st.rerun()
                            obs = st.text_input("Motivo, se devolver", key=f"ob{v['id']}")
                            if st.button("Devolver (novo prazo de 7 dias)", key=f"dv{v['id']}"):
                                avisar(V.conferir_contrato(v["id"], eu()["nome"], False, obs))
                                st.rerun()
                        elif v["contrato_status"] == "aguardando":
                            if st.button("Prorrogar mais 7 dias", key=f"pr{v['id']}"):
                                avisar(V.prorrogar_contrato(v["id"]))
                                st.rerun()
                with c2.popover("Trocar senha"):
                    with st.form(f"senha{v['id']}", clear_on_submit=True):
                        nova = st.text_input("Nova senha (mín. 8)", type="password")
                        if st.form_submit_button("Salvar"):
                            avisar(V.trocar_senha(v["id"], nova))
                            st.rerun()
                if v["id"] != eu()["id"]:
                    if not v["ativo"]:
                        if c3.button("Reativar", key=f"at{v['id']}"):
                            avisar(V.ativar(v["id"], True))
                            st.rerun()
                    else:
                        with c3.popover("Desativar"):
                            n = V.qtd_carteira(v["id"])
                            st.warning(f"{v['nome']} perde o acesso e os **{n} cliente(s)** dele voltam para o pool de "
                                       "contas, sem dono. O histórico fica registrado.")
                            if st.button("Confirmar desativação", key=f"atc{v['id']}", type="primary"):
                                avisar(V.ativar(v["id"], False))
                                st.rerun()
        st.markdown("#### Novo vendedor")
        with st.form("novo_vend", clear_on_submit=True):
            c1, c2 = st.columns(2)
            nome = c1.text_input("Nome")
            login = c2.text_input("Login (ex.: paloma)")
            senha = c1.text_input("Senha inicial (mín. 8)", type="password")
            admin = c2.checkbox("Administrador (vê tudo e gerencia)")
            if st.form_submit_button("Criar", type="primary"):
                avisar(V.criar(nome, login, senha, admin))
                st.rerun()
    with a_turnos:
        t = V.turno_aberto()
        if t:
            ult = t.get("ultimo_sinal")
            sinal = (f" · último sinal da página há {max(0, int((dados.agora() - ult).total_seconds() // 60))} min"
                     if ult else " · sem sinal registrado")
            st.warning(f"Turno aberto: {t['nome']} desde {t['inicio']:%d/%m %H:%M}{sinal}. "
                       f"Fecha sozinho depois de {V.INATIVO_MIN} min sem sinal.")
            if st.button("Encerrar este turno"):
                avisar(V.encerrar_turno(eu(), forcar=True))
                st.rerun()
        for x in V.turnos_recentes(14):
            fim = f"{x['fim']:%d/%m %H:%M}" if x["fim"] else "aberto"
            st.markdown(f"**{x['nome']}** · {x['inicio']:%d/%m %H:%M} → {fim} · {x['chamadas']} chamada(s) · "
                        f"{x['carteira']} na carteira{(' · fechado por ' + x['fechado_por']) if x.get('fechado_por') else ''}")
