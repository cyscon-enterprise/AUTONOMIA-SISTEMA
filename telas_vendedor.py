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


def faixa(icone: str, titulo: str, sub: str = "") -> None:
    """Cabecalho das paginas: faixa azul com titulo e subtitulo."""
    st.markdown(f"<div class='faixa'><div style='font-size:26px'>{icone}</div><div><b>{titulo}</b><br>"
                f"<span>{sub}</span></div></div>", unsafe_allow_html=True)


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
            if feito.get("transcricao"):
                st.caption("Transcrição automática")
                st.write(feito["transcricao"])
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


def dados_compactos(campos: list[tuple[str, str]], reg: dict) -> None:
    """Os dados do cadastro num bloco so, linha colada na outra (um st.markdown por campo deixa um
    espaco grande entre as linhas)."""
    linhas = [f"<div style='margin:0 0 3px'><b>{html.escape(r)}:</b> {html.escape(str(reg[k]))}</div>"
              for k, r in campos if reg.get(k)]
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

def form_conectar_3c(rotulo: str) -> None:
    """Login do ramal na 3C pelo portal: a senha so passa por aqui e vira o token do agente."""
    with st.form("conectar3c"):
        c1, c2 = st.columns([1, 2])
        ramal = c1.text_input("Ramal da 3C", value="1003")
        senha = c2.text_input("Senha da 3C", type="password")
        ok = st.form_submit_button(rotulo, type="primary")
        if ss.get("erro3c"):                            # junto do botao: a faixa do topo passa despercebida
            st.error(ss.pop("erro3c"))
    if ok:
        t = V.turno_aberto()
        if not (t and t["vendedor_id"] == eu()["id"]):
            r = V.iniciar_turno(eu())
            if r.get("erro"):
                avisar(r)
                st.rerun()
        try:
            ss.sessao3c = tresc.autenticar(ramal, senha)
            ss.msg = ("ok", "Conectado. Permita o microfone no telefone da barra lateral e espere ele ficar pronto.")
        except tresc.Erro3C as e:
            msg = str(e)
            ss.erro3c = ("Ramal ou senha da 3C incorretos. Confira e tente de novo." if "incorretos" in msg
                         else f"Não consegui conectar na 3C: {msg}")
        st.rerun()


def sair_3c() -> None:
    tresc.sair(ss.get("sessao3c"))
    ss.sessao3c = None
    ss.pop("ultima_chamada", None)


def pagina_discador() -> None:
    mostrar_msg()
    t = V.turno_aberto()
    meu = bool(t and t["vendedor_id"] == eu()["id"])
    if meu:
        faixa("📞", "Discador", f"Turno aberto desde {t['inicio']:%H:%M} · tudo que o discador conectar agora é seu")
        atual = next((x for x in V.turnos_recentes(1) if x["id"] == t["id"]), {})
        k = st.columns(3)
        kpi(k[0], "Chamadas no turno", atual.get("chamadas", 0))
        kpi(k[1], "Entraram na carteira", atual.get("carteira", 0))
        kpi(k[2], "Minha carteira", len(V.carteira(eu()["id"])))
        st.write("")
        s3 = ss.get("sessao3c")
        if not s3:
            st.warning("Seu ramal não está conectado neste portal.")
            form_conectar_3c("Conectar meu ramal")
        else:
            painel_ramal(s3)
        if st.button("⏹ Encerrar meu turno"):
            with st.spinner("Saindo da 3C e fechando a conta do turno…"):
                sair_3c()
                avisar(V.encerrar_turno(eu()))
            st.rerun()
        if s3:
            painel_na_linha()
    elif t:
        faixa("📞", "Discador", "Ligações automáticas da campanha")
        st.warning(f"O discador está com **{t['nome']}** desde {t['inicio']:%H:%M}. A 3C tem um usuário só: "
                   "espere o turno dele acabar.")
    else:
        faixa("📞", "Discador", "Ligações automáticas da campanha")
        st.info("Ninguém está no discador agora. Entre com o ramal e a senha da 3C para começar.")
        form_conectar_3c("▶ Iniciar discador")
        st.caption("Só as chamadas feitas com o seu turno aberto vão para você.")

    st.markdown("#### Chamadas dos seus últimos turnos")
    meus = [x for x in V.turnos_recentes(7) if x["vendedor_id"] == eu()["id"]]
    if not meus:
        st.caption("Nenhum turno nos últimos 7 dias.")
    for x in meus[:10]:
        fim = f"{x['fim']:%H:%M}" if x["fim"] else "aberto"
        st.markdown(f"**{x['inicio']:%d/%m %H:%M} → {fim}** · {x['chamadas']} chamada(s) · "
                    f"{x['carteira']} entraram na carteira")


def entrar_modo(s3: dict, campanha, manual: bool) -> None:
    try:
        tresc.entrar_campanha(s3["token"], campanha, manual=manual)
        s3["modo"] = "manual" if manual else "discador"
        st.success("Pedido enviado. A 3C liga para o seu telefone web para conectar: atenda na barra lateral."
                   + (" Depois é só clicar nos ☎ das fichas." if manual else " Em seguida o discador começa."))
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
    """Estado do ramal na 3C e a entrada na campanha (depois que o telefone web registrou)."""
    status = tresc.status_ramal(s3["ramal"])
    rotulo = tresc.STATUS_AGENTE.get(status, f"status {status}") if status is not None else "sem resposta da 3C"
    with st.container(border=True):
        cor = {0: "#b42318", 1: "#1a7f4b", 2: "#1565c0", 3: "#d97706"}.get(status, "#8a94a6")
        st.markdown(f"<span class='ponto' style='background:{cor}'></span>**Ramal {s3['ramal']}** "
                    f"{s3.get('nome') or ''} · {rotulo}", unsafe_allow_html=True)
        if not status:                                  # desconectado da campanha
            lista = s3.get("campanhas") or [{"id": s3.get("campanha"), "nome": s3.get("campanha_nome") or "da 3C"}]
            camps = {c["nome"]: c["id"] for c in lista}
            nome = st.selectbox("Campanha", list(camps)) if len(camps) > 1 else next(iter(camps))
            st.caption("Antes: no telefone da barra lateral, permita o microfone e espere aparecer \"Ramal registrado\". "
                       "Depois escolha como quer trabalhar:")
            c1, c2 = st.columns(2)
            if c1.button("📞 Discador automático", type="primary", use_container_width=True,
                         help="O discador liga sozinho para a base e passa para você quem atender."):
                entrar_modo(s3, camps[nome], manual=False)
            if c2.button("☎ Só ligações manuais", use_container_width=True,
                         help="O discador fica parado; você liga pelos botões ☎ das fichas."):
                entrar_modo(s3, camps[nome], manual=True)
        c1, c2 = st.columns(2)
        if status and c1.button("Sair da campanha (pausa longa)"):
            tresc.sair(s3)
            st.rerun(scope="fragment")
        if c2.button("Trocar ramal / reconectar"):
            sair_3c()
            st.rerun()


@st.fragment(run_every="10s")
def painel_na_linha() -> None:
    """Atualiza sozinho: le as chamadas novas, mostra quem o discador conectou e da a baixa na 3C."""
    _sincronizar()
    with st.container(border=True):
        st.markdown("#### 📞 Na linha agora")
        n = V.na_linha()
        if n and n.get("id"):
            ss.ultima_chamada = n                       # some das ativas ao desligar: guarda para a baixa
        if not n and ss.get("ultima_chamada"):
            n = ss.ultima_chamada
            st.caption("Ligação encerrada: dê a baixa abaixo para o discador seguir.")
        if not n:
            st.caption("Ninguém na linha. (atualiza sozinho a cada 10 s)")
            return
        f = V.ficha(n["cnpj"])
        if not f:
            st.caption(f"CNPJ {n['cnpj']} não está na mestra.")
            baixa_na_tela(n)
            return
        e = f["empresa"]
        st.markdown(f"### {e.get('nome_fantasia') or e.get('nome')}")
        st.caption(" · ".join(x for x in [e.get("cidade"), e.get("estado"), e.get("entidade"), e.get("segmento"),
                                          e.get("produto") and f"ERP: {e['produto']}", dados.formatar(n["telefone"])] if x))
        if f["dono"] and f["dono"]["vendedor_id"] != eu()["id"]:
            st.warning(f"Este cliente já é da carteira de {f['dono']['nome']}.")
        for h in f["historico"][:4]:
            st.markdown(f"**{br(h['quando'])}** · {h.get('quem') or ''} · {h.get('texto') or h.get('etapa_para') or ''}")
        baixa_na_tela(n)


def baixa_na_tela(n: dict) -> None:
    """Qualifica a chamada na 3C daqui mesmo; a sincronizacao leva o cliente para a carteira."""
    if not n.get("id"):
        return
    st.markdown("**Baixa da ligação** · Retornar, Enviar e-mail ou Agendar reunião põem o cliente na sua carteira.")
    try:
        quals = tresc.qualificacoes_crm()
    except tresc.Erro3C as e:
        st.caption(f"3C: {e}")
        return
    nota = st.text_input("Anotação (opcional)", key=f"nota{n['id']}")
    c1, c2 = st.columns(2)
    d = c1.date_input("Retorno", value=None, format="DD/MM/YYYY", key=f"rd{n['id']}")
    h = c2.time_input("Hora", value=dt.time(9, 0), key=f"rh{n['id']}")
    cols = st.columns(min(len(quals), 4) or 1)
    for i, q in enumerate(quals):
        if cols[i % len(cols)].button(q["nome"], key=f"q{n['id']}{q['id']}", use_container_width=True):
            ret = f"{d.isoformat()} {h:%H:%M}" if d else None
            try:
                tresc.baixar_chamada(n["id"], q["id"], nota, ret, ss.get("sessao3c"))
                ss.pop("ultima_chamada", None)
                V.sincronizar(forcar=True)
                st.success(f"Baixa dada: {q['nome']}.")
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
    faixa("👥" if todos else "📇", "Carteiras de todos os vendedores" if todos else "Minha carteira",
          "Clientes com quem o time já falou" if todos else "Clientes com quem você falou pelo discador")
    k = st.columns(3)
    hoje = dados.agora().date().isoformat()
    kpi(k[0], "Clientes", len(lista))
    kpi(k[1], "Retornos hoje", sum(1 for l in lista if (l["proximo_contato"] or "")[:10] == hoje))
    kpi(k[2], "Atrasados", sum(1 for l in lista if l["atrasado"]))
    st.write("")
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
            st.rerun()


def ficha_cliente(cnpj: str) -> None:
    f = V.ficha(cnpj)
    if st.button("← voltar à carteira"):
        ss.cliente = None
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
            campos = [("cnpj", "CNPJ"), ("aba", "Aba"), ("entidade", "Entidade"), ("segmento", "Segmento"),
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
    faixa("📊", "Visão geral · AutonomIA Seller", f"Últimos {dias} dia(s) · atualizado {dados.agora():%d/%m %H:%M}")

    vend = [v for v in g["vendedores"] if v["ativo"] and not v["admin"]]
    t = g["turno"]
    k = st.columns(5)
    kpi(k[0], "Discador agora", t["nome"] if t else "livre")
    kpi(k[1], "Chamadas hoje", g["chamadas_hoje"])
    kpi(k[2], f"Chamadas ({dias}d)", sum(v["chamadas"] for v in g["vendedores"]))
    kpi(k[3], f"Novos na carteira ({dias}d)", sum(v["novos"] for v in g["vendedores"]))
    kpi(k[4], "Vendedores ativos", len(vend))
    st.write("")

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
    a_vend, a_turnos, a_ia = st.tabs(["Vendedores", "Turnos do discador", "IA (resumo das ligações)"])
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
