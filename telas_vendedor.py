"""Paginas da area do vendedor (Discador, Minha carteira) e da Gestao (so administrador)."""
from __future__ import annotations

import datetime as dt

import streamlit as st

import dados
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
            ss.msg = ("erro", f"3C: {e}")
        st.rerun()


def sair_3c() -> None:
    tresc.sair(ss.get("sessao3c"))
    ss.sessao3c = None
    ss.pop("ultima_chamada", None)


def pagina_discador() -> None:
    mostrar_msg()
    st.title("Discador")
    t = V.turno_aberto()
    meu = bool(t and t["vendedor_id"] == eu()["id"])
    if meu:
        st.success(f"Seu turno está aberto desde {t['inicio']:%H:%M}. Tudo que o discador conectar agora é seu.")
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
        st.warning(f"O discador está com **{t['nome']}** desde {t['inicio']:%H:%M}. A 3C tem um usuário só: "
                   "espere o turno dele acabar.")
    else:
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


@st.fragment(run_every="10s")
def painel_ramal(s3: dict) -> None:
    """Estado do ramal na 3C e a entrada na campanha (depois que o telefone web registrou)."""
    status = tresc.status_ramal(s3["ramal"])
    rotulo = tresc.STATUS_AGENTE.get(status, f"status {status}") if status is not None else "sem resposta da 3C"
    with st.container(border=True):
        st.markdown(f"**Ramal {s3['ramal']}** {s3.get('nome') or ''} · {rotulo}")
        if not status:                                  # desconectado da campanha
            lista = s3.get("campanhas") or [{"id": s3.get("campanha"), "nome": s3.get("campanha_nome") or "da 3C"}]
            camps = {c["nome"]: c["id"] for c in lista}
            nome = st.selectbox("Campanha", list(camps)) if len(camps) > 1 else next(iter(camps))
            st.caption("1) Na barra lateral, permita o microfone e espere o telefone ficar pronto (uns 10 s). "
                       "2) Clique abaixo: o discador começa a ligar e as chamadas tocam no telefone da barra.")
            if st.button(f"📞 Entrar na campanha {nome} e começar a discar", type="primary"):
                try:
                    tresc.entrar_campanha(s3["token"], camps[nome])
                    st.success("Pedido enviado. A 3C liga para o seu telefone web para conectar: atenda na barra lateral.")
                except tresc.Erro3C as e:
                    st.error(f"3C: {e}")
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
    mostrar_msg()
    _sincronizar()
    if ss.get("cliente"):
        ficha_cliente(ss.cliente)
        return
    admin = eu().get("admin")
    st.title("Carteira de clientes" if admin else "Minha carteira")
    vend_id = None if admin and st.toggle("Ver de todos os vendedores", value=False) else eu()["id"]
    lista = V.carteira(vend_id)
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
        cols = st.columns(3)
        for i, l in enumerate(lista):
            with cols[i % 3]:
                cartao(l, "c")
    with aba_agenda:
        hoje = dados.agora().date().isoformat()
        marcados = sorted([l for l in lista if l["proximo_contato"]], key=lambda l: l["proximo_contato"])
        for titulo, grupo in [("Atrasados", [l for l in marcados if l["proximo_contato"][:10] < hoje]),
                              ("Hoje", [l for l in marcados if l["proximo_contato"][:10] == hoje]),
                              ("Próximos", [l for l in marcados if l["proximo_contato"][:10] > hoje])]:
            st.markdown(f"#### {titulo} ({len(grupo)})")
            if not grupo:
                st.caption("nada aqui")
            cols = st.columns(3)
            for i, l in enumerate(grupo):
                with cols[i % 3]:
                    cartao(l, "a")


def cartao(l: dict, onde: str) -> None:
    with st.container(border=True):
        st.markdown(f"**{l['nome_exibir'][:60]}**  \n<span style='color:#666;font-size:13px'>"
                    f"{l['cidade'] or ''}{('/' + l['estado']) if l['estado'] else ''} · {l['etapa_crm'] or 'sem contato'}"
                    f"</span>", unsafe_allow_html=True)
        if eu().get("admin"):
            st.caption(f"👤 {l['vendedor']}")
        if l["proximo_contato"]:
            cor = "#b42318" if l["atrasado"] else "#444"
            st.markdown(f"<span style='color:{cor}'>{'atrasado' if l['atrasado'] else 'retorno'}: "
                        f"{br(l['proximo_contato'])}</span>", unsafe_allow_html=True)
        st.caption(f"na carteira desde {l['desde']:%d/%m} · {l['motivo'] or ''}")
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
        st.markdown("#### Telefones")
        if not f["telefones"]:
            st.caption("Sem telefone.")
        for t in f["telefones"]:
            st.markdown(f"[☎ {t['exibir']}](tel:+55{t['tel']}) · {', '.join(t['rotulos'])}")
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
    with st.expander("Dados da empresa"):
        campos = [("cnpj", "CNPJ"), ("aba", "Aba"), ("entidade", "Entidade"), ("segmento", "Segmento"),
                  ("base", "Base"), ("mrr", "MRR"), ("produto", "ERP"), ("porte_da_empresa", "Porte"),
                  ("cnae_descricao", "Atividade"), ("quadro_de_funcionarios", "Funcionários"),
                  ("endereco", "Endereço"), ("bairro", "Bairro"), ("email", "E-mail"), ("site_da_empresa", "Site"),
                  ("contato_ti", "Contato TI"), ("contato_rh", "Contato RH"), ("contato_financeiro", "Contato Financeiro"),
                  ("contato_fiscal", "Contato Fiscal"), ("contato_contabil", "Contato Contábil"),
                  ("socios_rf", "Sócios (Receita)")]
        for k, r in campos:
            if e.get(k):
                st.markdown(f"**{r}:** {e[k]}")


# ---------------------------------------------------------------- Gestao (admin)

def pagina_gestao() -> None:
    mostrar_msg()
    st.title("Gestão")
    a_vend, a_turnos = st.tabs(["Vendedores", "Turnos do discador"])
    with a_vend:
        for v in V.lista():
            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 2, 2])
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
