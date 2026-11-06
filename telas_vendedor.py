"""Paginas da area do vendedor (Discador, Minha carteira) e da Gestao (so administrador)."""
from __future__ import annotations

import datetime as dt
import html
import re
from urllib.parse import quote

import streamlit as st

import dados
import ia_ligacoes
import tresc3c as tresc
import vendedores as V

ss = st.session_state

TIPOS = {"ligacao": "ligação", "email": "e-mail", "whatsapp": "WhatsApp", "reuniao": "reunião", "nota": "nota"}


def eu() -> dict:
    return ss.usuario


def avisar(r: dict) -> None:
    if r.get("erro"):
        ss.msg = ("erro", r["erro"])
    else:
        ss.msg = ("ok", r.get("mensagem") or "Feito.")


def mostrar_msg() -> None:
    if ss.get("msg"):
        tipo, texto = ss.msg
        (st.error if tipo == "erro" else st.success)(texto)
        ss.msg = None


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


def botoes_contato(nome_empresa: str, emails: list[tuple[str, str]], telefones: list[dict], chave: str) -> None:
    """Botoes de e-mail (abre o e-mail do vendedor ja preenchido) e WhatsApp (abre a conversa)."""
    vendedor = (ss.get("quem") or "").split(" ")[0]
    empresa = ss.get("empresa", {}).get("nome") or "Cyscon Consultoria"
    c1, c2 = st.columns(2)
    with c1:
        if emails:
            op = {f"{e} · {r}": e for e, r in emails}
            alvo = op[st.selectbox("E-mail", list(op), key=f"em{chave}", label_visibility="collapsed")] \
                if len(op) > 1 else emails[0][0]
            assunto = f"{empresa} | {nome_empresa}"
            corpo = (f"Olá, tudo bem?\n\nAqui é {vendedor}, da {empresa}, parceira TOTVS.\n\n"
                     "Conforme conversamos, segue o contato.\n\nFico à disposição.\n\n" + (vendedor or ""))
            st.link_button("✉ Enviar e-mail", f"mailto:{alvo}?subject={quote(assunto)}&body={quote(corpo)}",
                           use_container_width=True)
        else:
            st.button("✉ Sem e-mail", disabled=True, use_container_width=True, key=f"sem_em{chave}")
    with c2:
        cels = [t for t in telefones if celular(t["tel"])]
        if cels:
            op = {f"{t['exibir']} · {', '.join(t['rotulos'])}": t["tel"] for t in cels}
            tel = op[st.selectbox("WhatsApp", list(op), key=f"wa{chave}", label_visibility="collapsed")] \
                if len(op) > 1 else cels[0]["tel"]
            texto = f"Olá! Aqui é {vendedor}, da {empresa}, parceira TOTVS. Tudo bem?"
            st.link_button("💬 Conversar no WhatsApp", f"https://wa.me/55{tel}?text={quote(texto)}",
                           use_container_width=True)
        else:
            st.button("💬 Sem celular", disabled=True, use_container_width=True, key=f"sem_wa{chave}",
                      help="WhatsApp precisa de um celular (11 dígitos começando com 9). Inclua um em ➕ Incluir telefone.")


def incluir_telefone(cnpj: str, lead_id: int | None = None) -> None:
    """Campo para incluir um telefone novo; depois de salvo ele vira um botao de ligar pela 3C."""
    with st.popover("➕ Incluir telefone", use_container_width=True):
        with st.form(f"novotel{cnpj}", clear_on_submit=True):
            tel = st.text_input("Telefone com DDD", placeholder="(11) 91234-5678")
            rot = st.text_input("De quem é (opcional)", placeholder="ex.: Carlos, gerente de TI")
            if st.form_submit_button("Salvar telefone", type="primary", use_container_width=True):
                avisar(dados.adicionar_telefone(cnpj, tel, rot, ss.get("quem") or eu()["nome"], lead_id))
                st.rerun()


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


def conectar_3c_lateral() -> None:
    """Barra lateral, antes de conectar: um clique (senha da 3C guardada nos Secrets) ou, se ela nao
    estiver configurada, o ramal e a senha."""
    t = V.turno_aberto()
    if t and t["vendedor_id"] != eu()["id"]:
        st.caption(f"O discador está com **{t['nome']}** desde {t['inicio']:%H:%M}. "
                   "A 3C tem um ramal só: espere o turno dele acabar.")
        return
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
        st.warning(f"O discador está com **{t['nome']}** desde {t['inicio']:%H:%M}. A 3C tem um usuário só: "
                   "espere o turno dele acabar.")
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


def entrar_modo(s3: dict, campanha, manual: bool) -> None:
    try:
        tresc.entrar_campanha(s3["token"], campanha, manual=manual)
        s3["modo"] = "manual" if manual else "discador"
        st.success("Pronto. Se o telefone da barra lateral tocar, atenda: é a 3C conectando você."
                   + (" Depois é só clicar nos ☎ das fichas." if manual else " Em seguida as ligações começam a chegar."))
    except tresc.Erro3C as e:
        st.error(f"3C: {e}")


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
    if status == 0:
        _comecar(s3)
    elif status and st.button("⏸ Parar de receber ligações", use_container_width=True):
        tresc.sair(s3)
        st.rerun(scope="fragment")


def _comecar(s3: dict) -> None:
    """Barra lateral: a campanha (so as que tem telefones) e o botao de comecar."""
    lista = s3.get("campanhas") or [{"id": s3.get("campanha"), "nome": s3.get("campanha_nome") or "da 3C"}]
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
            st.markdown("<div style='text-align:center;padding:38px 0 30px'><div style='font-size:30px;font-weight:700;"
                        "color:#1565c0'>⏳ Aguardando chamada…</div><div style='color:#5b6b85;margin-top:8px'>Quando "
                        "alguém atender, os dados da empresa aparecem aqui.</div></div>", unsafe_allow_html=True)
            return
        f = V.ficha(n["cnpj"])
        if not f:
            st.caption(f"CNPJ {n['cnpj']} não está na mestra.")
            baixa_na_tela(n)
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
            st.markdown(f"<div style='display:inline-block;background:#E8F5EE;border:1px solid #1a7f4b;border-radius:8px;"
                        f"padding:3px 12px;margin:0 0 8px;font-size:15px;font-weight:600;color:#14532d'>📞 Número discado: "
                        f"{html.escape(dados.formatar(n.get('telefone')) or 'número não informado pela 3C')}</div>",
                        unsafe_allow_html=True)
            baixa_na_tela(n)
        with dir_:
            # tudo do cliente, na hora em que ele atende: insight, faturamento, porte, contatos, historico
            st.markdown("#### Insight")
            ins = V.insight(n["cnpj"])
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


def _salvar_postit(n: dict, qualif: str = "") -> None:
    """Grava o post-it no historico assim que o vendedor sai da caixa (e de novo na baixa, com a qualificacao)."""
    texto = (ss.get(f"nota{n['id']}") or "").strip()
    if not texto:
        return
    onde = ", ".join(x for x in (qualif, dados.formatar(n.get("telefone"))) if x)
    quem = ss.get("quem") or eu()["nome"]
    ss[f"nota_hist{n['id']}"] = V.anotar_ligacao(n["cnpj"], quem, f"Anotação da ligação ({onde}): {texto}",
                                                 ss.get(f"nota_hist{n['id']}"))
    novos = V.contatos_do_postit(n["cnpj"], texto, n.get("telefone"), quem)   # celular/e-mail anotado -> contatos
    if novos:
        ss[f"nota_contatos{n['id']}"] = ss.get(f"nota_contatos{n['id']}", []) + novos


def baixa_na_tela(n: dict) -> None:
    """Qualifica a chamada na 3C daqui mesmo; a sincronizacao leva o cliente para a carteira."""
    if not n.get("id"):
        return
    try:
        quals = tresc.qualificacoes_crm()
    except tresc.Erro3C as e:
        st.caption(f"3C: {e}")
        return
    # post-it amarelo: para anotar DURANTE a conversa; vai para o historico do lead ao dar a baixa
    st.markdown("<style>.st-key-postit textarea{background:#FFF59D !important;color:#3b3200 !important;"
                "font-size:17px !important;line-height:1.5 !important;border:1px solid #E6D96A !important;"
                "box-shadow:2px 3px 8px rgba(120,100,0,.18)}</style>", unsafe_allow_html=True)
    with st.container(key="postit"):
        nota = st.text_area("📝 Anotações da ligação · salvam sozinhas no histórico", key=f"nota{n['id']}", height=150,
                            placeholder="Anote aqui durante a conversa: nome, cargo, ramal, sistema atual, próximo passo…",
                            on_change=_salvar_postit, args=(n,))
        if ss.get(f"nota_hist{n['id']}"):
            extra = ss.get(f"nota_contatos{n['id']}")
            st.caption("✓ salvo no histórico do lead" + (f" · incluído nos contatos: {'; '.join(extra)}" if extra else ""))
    st.markdown("**Baixa da ligação** · Retornar, Enviar e-mail ou Agendar reunião põem o cliente na sua carteira.")
    c1, c2 = st.columns(2)
    d = c1.date_input("Retorno", value=None, format="DD/MM/YYYY", key=f"rd{n['id']}")
    h = c2.time_input("Hora", value=dt.time(9, 0), key=f"rh{n['id']}")
    # nomes inteiros nos botoes: 3 por linha, letra um pouco menor e quebra de linha em vez de "..."
    st.markdown("<style>.st-key-baixa button{min-height:44px;padding:4px 8px}"
                ".st-key-baixa button p{font-size:14px;white-space:normal;overflow:visible;text-overflow:clip;"
                "line-height:1.2}</style>", unsafe_allow_html=True)
    caixa = st.container(key="baixa")
    cols = caixa.columns(min(len(quals), 3) or 1)
    for i, q in enumerate(quals):
        if cols[i % len(cols)].button(q["nome"], key=f"q{n['id']}{q['id']}", use_container_width=True):
            ret = f"{d.isoformat()} {h:%H:%M}" if d else None
            try:
                tresc.baixar_chamada(n["id"], q["id"], nota, ret, ss.get("sessao3c"))
                if (nota or "").strip():
                    _salvar_postit(n, q["nome"])
                ss.pop("ultima_chamada", None)
                ss.setdefault("baixadas", set()).add(n["id"])      # some do quadro ja: volta ao "Aguardando chamada"
                V.sincronizar(forcar=True)
                ss.msg = ("ok", f"Baixa dada: {q['nome']}.")
                st.rerun()
            except tresc.Erro3C as e:
                st.error(f"3C: {e}")


# ---------------------------------------------------------------- Carteira

def pagina_carteira() -> None:
    _carteira(todos=False)


def pagina_carteiras_todos() -> None:
    """So do administrador: a carteira de todos os vendedores, com quem e dono de cada cliente."""
    _carteira(todos=True)


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
    aba_lista, aba_agenda = st.tabs([f"Clientes ({len(lista)})", "Agenda"])
    with aba_lista:
        if not lista:
            st.info("Nenhum cliente ainda. Os clientes com quem você falar pelo discador aparecem aqui.")
        grade(lista, lambda l: cartao(l, "c"))
    with aba_agenda:
        hoje = dados.agora().date().isoformat()
        marcados = sorted([l for l in lista if l["proximo_contato"]], key=lambda l: l["proximo_contato"])
        for titulo, grupo in [("Atrasados", [l for l in marcados if l["proximo_contato"][:10] < hoje]),
                              ("Hoje", [l for l in marcados if l["proximo_contato"][:10] == hoje]),
                              ("Próximos", [l for l in marcados if l["proximo_contato"][:10] > hoje])]:
            st.markdown(f"#### {titulo} ({len(grupo)})")
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
        st.markdown("#### Histórico")
        linha = [("ch", c["quando"], c) for c in f["chamadas"]]
        linha += [("h", h["quando"], h) for h in f["historico"] if not str(h.get("texto") or "").startswith("Discador 3C")]
        linha.sort(key=lambda x: x[1] or dt.datetime.min, reverse=True)
        with st.container(height=460, border=True):
            if not linha and not e.get("historico"):
                st.caption("Sem histórico ainda.")
            for tipo, _, x in linha:
                if tipo == "ch":
                    st.markdown(f"**{br(x['quando'])}** · discador · {x.get('vendedor') or ''} · "
                                f"{dados.formatar(x.get('telefone'))}")
                    st.caption(" · ".join(v for v in [x.get("qualificacao") or "sem qualificação", x.get("resultado"),
                                                      x.get("duracao") and f"conversa {x['duracao']}"] if v))
                    if x.get("gravada"):
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
                else:
                    st.markdown(f"**{br(x['quando'])}**{(' · ' + x['quem']) if x.get('quem') else ''} · "
                                f"{TIPOS.get(x.get('tipo'), x.get('tipo') or '')}")
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
        botoes_contato(e.get("nome_fantasia") or e.get("nome") or "", emails_de(e), f["telefones"], cnpj)
        st.markdown("#### Ligar pela 3C")
        controle_ligacao_manual()
        if not f["telefones"]:
            st.caption("Sem telefone.")
        ramal = (ss.get("sessao3c") or {}).get("ramal") or "1003"
        for t in f["telefones"]:
            if st.button(f"☎ {t['exibir']}  ·  {', '.join(t['rotulos'])}", key=f"lig{cnpj}{t['tel']}",
                         use_container_width=True):
                with st.spinner("Pedindo a ligação à 3C (pode levar até 1 minuto)…"):
                    try:
                        tresc.ligar("55" + t["tel"], ramal)
                        V.registrar(eu(), cnpj, "ligacao", f"Ligação pedida à 3C para {t['exibir']} (ramal {ramal})")
                        ss.msg = ("ok", f"Ligando: o ramal {ramal} toca primeiro, atenda para discar.")
                    except tresc.Erro3C as err:
                        ss.msg = ("erro", f"3C: {err}")
                st.rerun()
        incluir_telefone(cnpj)
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
                    c1.markdown(f"**{p['nome']}** · {doc} · `{p['login']}` · {p['cidade'] or ''}  \n"
                                f"{p['email'] or ''} · {tel}  \n"
                                f"Cadastro em {br(p['criado_em'])} · aceitou os termos v{p['termos_versao']}")
                    if c2.button("✅ Aprovar", key=f"apr{p['id']}", type="primary"):
                        avisar(V.aprovar(p["id"], True))
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
                with c2.popover("Trocar senha"):
                    with st.form(f"senha{v['id']}", clear_on_submit=True):
                        nova = st.text_input("Nova senha (mín. 8)", type="password")
                        if st.form_submit_button("Salvar"):
                            avisar(V.trocar_senha(v["id"], nova))
                            st.rerun()
                if v["id"] != eu()["id"]:
                    if c3.button("Reativar" if not v["ativo"] else "Desativar", key=f"at{v['id']}"):
                        avisar(V.ativar(v["id"], not v["ativo"]))
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
            st.warning(f"Turno aberto: {t['nome']} desde {t['inicio']:%d/%m %H:%M}")
            if st.button("Encerrar este turno"):
                avisar(V.encerrar_turno(eu(), forcar=True))
                st.rerun()
        for x in V.turnos_recentes(14):
            fim = f"{x['fim']:%d/%m %H:%M}" if x["fim"] else "aberto"
            st.markdown(f"**{x['nome']}** · {x['inicio']:%d/%m %H:%M} → {fim} · {x['chamadas']} chamada(s) · "
                        f"{x['carteira']} na carteira{(' · fechado por ' + x['fechado_por']) if x.get('fechado_por') else ''}")
