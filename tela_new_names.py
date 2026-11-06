"""Pagina PAINELNEWNAMES (secao Motor de Vendas): o funil dos leads New Names, a ficha e a ligacao 3C.

Era o app.py inteiro ate 30/10/2026; virou uma pagina do CRM quando entrou o login por vendedor.
So o administrador ve esta pagina. Quem assina o historico e o usuario logado (`ss.quem`).
"""
from __future__ import annotations

import datetime as dt

import streamlit as st

import dados
import kanban_dnd
import telas_vendedor
import tresc3c as tresc

ss = st.session_state


def selo(g: dict | None) -> str:
    if not g:
        return '<span class="selo esp">grupo M/F não verificado</span>'
    v = g["veredito"]
    cls = "ok" if v == "NEW NAME" else "err" if v.startswith("CLIENTE") else "esp"
    em = g["verificado_em"].strftime("%d/%m/%Y") if g.get("verificado_em") else ""
    return f'<span class="selo {cls}" title="{g.get("detalhe") or ""}">{"grupo M/F ok" if v == "NEW NAME" else v} · {em}</span>'


def avisar(r: dict, ok_msg: str = "") -> None:
    if r.get("erro"):
        ss.msg = ("erro", r["erro"])
    else:
        ss.msg = ("aviso", r["aviso"]) if r.get("aviso") else ("ok", r.get("mensagem") or ok_msg)


def br(v) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        try:
            v = dt.datetime.fromisoformat(v)
        except ValueError:
            return v
    return v.strftime("%d/%m %H:%M")



def ramal_atual() -> str:
    return (ss.sessao3c or {}).get("ramal") or "1003"


# ---------------------------------------------------------------- ficha do lead

@st.fragment(run_every="4s")
def status_ao_vivo(lig_id: int) -> None:
    """O que esta acontecendo com a chamada, atualizado sozinho. Na 3C o cliente e discado primeiro e o
    vendedor so entra na linha quando ele atende: sem este aviso, o vendedor fica no silencio sem saber."""
    lig = dados.ligacao(lig_id)
    if not lig:
        return
    viva = dados.chamada_ativa(lig) if lig["status"] != "finalizada" else None
    if viva:
        if str(viva.get("speaking_time") or "00:00:00") not in ("", "00:00:00") or "talk" in str(viva.get("_grupo")).lower():
            st.success("🟢 Em conversa.")
        else:
            st.info(f"📳 Chamando {dados.formatar(lig['telefone'])}… Você entra na linha quando atenderem "
                    "(não há som de chamando do seu lado).")
        return
    if lig["status"] != "finalizada":
        dados.sincronizar(lig["lead_id"])
        lig = dados.ligacao(lig_id) or lig
    if lig.get("resultado") or lig["status"] == "finalizada":
        conversa = lig.get("duracao") and lig["duracao"] != "00:00:00"
        texto = f"Encerrada: {lig.get('resultado') or 'sem resultado'}" + (f" · conversa {lig['duracao']}" if conversa else "")
        (st.success if conversa else st.warning)(("✅ " if conversa else "⚠️ ") + texto)
    elif lig["status"] == "recusada":
        st.error(f"❌ A 3C recusou: {lig.get('erro') or ''}")
    else:
        st.info("⏳ Pedido enviado à 3C, esperando a chamada começar…")


def bloco_ligacao(lig: dict) -> None:
    """Ligacao recente sem baixa: estado, desligar e dar baixa pelas etapas do funil."""
    with st.container(border=True):
        st.markdown(f"**📞 Ligação para {dados.formatar(lig['telefone'])}** · pedida {br(lig['pedido_em'])} · "
                    f"ramal {lig.get('ramal') or ''}")
        status_ao_vivo(lig["id"])
        c1, c2 = st.columns(2)
        if c1.button("🔄 Atualizar", key=f"att{lig['id']}", use_container_width=True):
            dados.sincronizar(lig["lead_id"])
            st.rerun()
        if c2.button("⛔ Desligar", key=f"des{lig['id']}", use_container_width=True):
            avisar(dados.desligar(lig["id"], ss.quem, ss.sessao3c), "Ligação desligada.")
            st.rerun()
        with st.form(f"baixa{lig['id']}"):
            st.markdown("**Dar baixa**")
            nomes = [e["nome"] for e in dados.etapas()]
            etapa = st.selectbox("Resultado (etapa do funil)", nomes, index=nomes.index("retornar") if "retornar" in nomes else 0)
            nota = st.text_input("Observação")
            c3, c4 = st.columns(2)
            d_ret = c3.date_input("Retorno (opcional)", value=None, format="DD/MM/YYYY")
            h_ret = c4.time_input("Hora", value=dt.time(9, 0))
            if st.form_submit_button("Dar baixa", type="primary"):
                ret = dt.datetime.combine(d_ret, h_ret) if d_ret else None
                avisar(dados.baixar(lig["id"], etapa, nota, ret, ss.quem, ss.sessao3c), f"Baixa dada: {etapa}.")
                st.rerun()


@st.cache_data(ttl=3600, show_spinner="Buscando a gravação na 3C…")
def _audio(chamada_id: str):
    return tresc.gravacao(chamada_id)


def item_ligacao(g: dict) -> None:
    rot = {"pedindo": "pedindo à 3C", "pedida": "em andamento", "incerta": "3C não respondeu",
           "recusada": "recusada", "finalizada": "finalizada", "sem_registro": "não achada na 3C"}.get(g["status"], g["status"])
    linha = [g.get("resultado"), g.get("qualificacao") and f"baixa: {g['qualificacao']}",
             g.get("duracao") and g["duracao"] != "00:00:00" and f"conversa {g['duracao']}"]
    st.markdown(f"**{br(g['pedido_em'])}** · ligação 3C para **{dados.formatar(g['telefone'])}** "
                f"<span style='color:#888'>{g.get('rotulo') or ''}</span> · _{rot}_", unsafe_allow_html=True)
    if any(linha):
        st.caption(" · ".join(x for x in linha if x))
    if g.get("erro") and g["status"] != "finalizada":
        st.caption(g["erro"])
    cid = g.get("chamada_3c") or ""
    if cid and g.get("gravada"):
        chave = f"ouvir{g['id']}"
        if ss.get(chave):
            try:
                audio, tipo = _audio(cid)
                st.audio(audio, format=tipo)
            except tresc.Erro3C as e:
                st.caption(f"Gravação: {e}")
        elif st.button("▶ ouvir gravação", key=chave + "b"):
            ss[chave] = True
            st.rerun()
        telas_vendedor.bloco_ia(cid, g.get("empresa") or "",
                                f"Resumo da ligação de {br(g['pedido_em'])} para {dados.formatar(g['telefone'])}:",
                                lambda t: dados.registrar_resumo_ia(g["lead_id"], g.get("cnpj"), t))


def ver_ficha(lead_id: int) -> None:
    f = dados.ficha(lead_id)
    if not f:
        st.error("Lead não encontrado.")
        ss.lead = None
        st.query_params.pop("ficha", None)
        return
    if any(l["status"] in ("pedindo", "pedida", "incerta") for l in f["ligacoes"]):
        dados.sincronizar(lead_id)                 # traz o resultado assim que a 3C tiver
        f = dados.ficha(lead_id)
    L, est = f["lead"], f["estado"]
    if st.button("← voltar ao funil"):
        ss.lead = None
        st.query_params.pop("ficha", None)
        st.rerun()
    st.markdown(f"## {L.get('nome_fantasia') or L.get('razao_social') or ''}")
    st.markdown(f"{est.get('etapa') or ''} · {selo(f['grupo'])}", unsafe_allow_html=True)
    if f["grupo"] and f["grupo"].get("detalhe"):
        st.caption(f["grupo"]["detalhe"])

    esq, dir_ = st.columns([3, 2], gap="large")
    with esq:
        st.markdown("#### Insight")
        ins = L.get("insights_abordagem_cyscon")
        st.markdown(f'<div class="insight">{ins}</div>' if ins else
                    '<div class="insight" style="color:#888">Sem pesquisa de insight para esta empresa ainda.</div>',
                    unsafe_allow_html=True)
        st.markdown("#### Dados do lead")             # sempre aberto, logo abaixo do insight (pedido do usuario)
        with st.container(border=True):
            campos = [("razao_social", "Razão social"), ("nome_fantasia", "Nome fantasia"), ("cnpj", "CNPJ"), ("cidade", "Cidade"), ("estado", "UF"),
                      ("status_crmtotvs", "Status no CRM TOTVS"), ("codigo_crmtotvs", "Código CRM"),
                      ("entidade", "Entidade"), ("base", "Base"), ("segmento", "Segmento"), ("mrr", "MRR"),
                      ("faturamento_totvs", "Faturamento TOTVS"), ("porte_da_empresa", "Porte"),
                      ("funcionarios", "Funcionários"), ("contato_crmtotvs", "Contato no CRM"),
                      ("contato_de_ti", "Contato TI"), ("contato_rh", "Contato RH"), ("contato_fiscal", "Contato Fiscal"),
                      ("contato_financeiro", "Contato Financeiro"), ("email", "E-mail"), ("site_da_empresa", "Site"),
                      ("historico_crmtotvs", "Histórico no CRM TOTVS")]
            telas_vendedor.dados_compactos(campos, L)
        st.markdown("#### Histórico")
        c_at = st.columns([3, 1])[1]
        if c_at.button("🔄 atualizar ligações", use_container_width=True):
            r = dados.sincronizar(lead_id)
            ss.msg = ("ok", f"{r['achadas']} ligação(ões) atualizada(s)" + (f" · {r['erros']} erro(s)" if r["erros"] else ""))
            st.rerun()
        linha = [("lig", g["pedido_em"], g) for g in f["ligacoes"]]
        linha += [("nota", h["quando"], h) for h in f["historico"] if not h.get("ligacao_id")]
        linha.sort(key=lambda x: x[1], reverse=True)
        with st.container(height=420, border=True):
            for tipo, _, x in linha:
                if tipo == "lig":
                    item_ligacao(x)
                else:
                    st.markdown(f"**{br(x['quando'])}**{(' · ' + x['quem']) if x.get('quem') else ''} · {x.get('tipo') or ''}")
                    if x.get("etapa_para"):
                        st.caption(f"{x.get('etapa_de') or '—'} → {x['etapa_para']}")
                    if x.get("texto"):
                        st.text(x["texto"])
                st.divider()
            if L.get("historico"):
                st.markdown("**Na planilha**")
                st.text(L["historico"])
            if not linha and not L.get("historico"):
                st.caption("Sem histórico ainda.")

    with dir_:
        aberta = dados.ligacao_aberta(lead_id)
        if aberta:
            bloco_ligacao(aberta)
        st.markdown("#### Contato")
        telas_vendedor.botoes_contato(L.get("nome_fantasia") or L.get("razao_social") or "",
                                      telas_vendedor.emails_de(L), f["telefones"], f"nn{lead_id}")
        st.markdown("#### Ligar pela 3C")
        telas_vendedor.controle_ligacao_manual()
        if not f["telefones"]:
            st.caption("Sem telefone.")
        for t in f["telefones"]:
            if st.button(f"☎ {t['exibir']}  ·  {', '.join(t['rotulos'])}", key=f"lig{lead_id}{t['tel']}",
                         use_container_width=True):
                with st.spinner("Pedindo a ligação à 3C (pode levar até 1 minuto)…"):
                    avisar(dados.ligar(lead_id, t["tel"], ss.quem, ramal_atual()))
                st.rerun()
        telas_vendedor.incluir_telefone(L.get("cnpj"), lead_id)
        st.caption(f"O ramal {ramal_atual()} toca primeiro; ao atender, a 3C disca para o cliente.")

        st.markdown("#### Etapa e retorno")
        nomes = [e["nome"] for e in dados.etapas()]
        nova = st.selectbox("Etapa", nomes, index=nomes.index(est["etapa"]) if est.get("etapa") in nomes else 0)
        if st.button("Mudar etapa", use_container_width=True):
            avisar(dados.mover(lead_id, nova, ss.quem), f"Etapa: {nova}")
            st.rerun()
        c1, c2 = st.columns(2)
        d = c1.date_input("Retorno", value=None, format="DD/MM/YYYY", key="dret")
        h = c2.time_input("Hora", value=dt.time(9, 0), key="hret")
        if st.button("Agendar retorno", use_container_width=True):
            avisar(dados.agendar(lead_id, dt.datetime.combine(d, h) if d else None, ss.quem),
                   "Retorno agendado." if d else "Retorno desmarcado.")
            st.rerun()
        if est.get("proximo_contato"):
            st.caption(f"Retorno marcado: {br(est['proximo_contato'])}")

        st.markdown("#### Registrar")
        with st.form("registrar", clear_on_submit=True):
            tipo = st.selectbox("O que aconteceu", ["ligacao", "email", "whatsapp", "reuniao", "nota"],
                                format_func={"ligacao": "ligação", "email": "e-mail", "whatsapp": "WhatsApp",
                                             "reuniao": "reunião", "nota": "nota"}.get)
            texto = st.text_area("Falei com quem, o que ficou combinado…")
            if st.form_submit_button("Registrar", type="primary"):
                avisar(dados.interagir(lead_id, tipo, texto, ss.quem) if texto.strip()
                       else {"erro": "Escreva o que aconteceu."}, "Registrado.")
                st.rerun()


# ---------------------------------------------------------------- funil e agenda

ALTURA_CARTAO = 222                         # todos iguais: a grade fica alinhada


def grade(itens: list, desenhar, por_linha: int = 3) -> None:
    """Cartoes em linhas de `por_linha`: cada linha abre colunas novas, entao as linhas alinham."""
    for i in range(0, len(itens), por_linha):
        cols = st.columns(por_linha)
        for col, item in zip(cols, itens[i:i + por_linha]):
            with col:
                desenhar(item)


def cartao(c: dict, onde: str = "f") -> None:
    with st.container(border=True, height=ALTURA_CARTAO):
        st.markdown(f"<div class='cart-nome' title=\"{c['nome']}\">{c['nome']}</div>"
                    f"<div class='cart-linha'>{c['cidade']}{('/' + c['estado']) if c['estado'] else ''}"
                    f"{(' · ' + c['status_crm']) if c['status_crm'] else ''}</div>{selo(c['grupo'])}",
                    unsafe_allow_html=True)
        retorno = (f"<span class='{'late' if c['atrasado'] else ''}'>{'atrasado' if c['atrasado'] else 'retorno'}: "
                   f"{br(c['proximo_contato'])}</span>") if c["proximo_contato"] else "&nbsp;"
        fones = " · ".join(t["exibir"] for t in c["telefones"][:2]) or "sem telefone"
        st.markdown(f"<div class='cart-linha'>{retorno}</div><div class='cart-linha'>☎ {fones}</div>",
                    unsafe_allow_html=True)
        if st.button("Abrir", key=f"abrir{onde}{c['id']}", use_container_width=True):
            ss.lead = c["id"]
            st.query_params["ficha"] = "1"
            st.rerun()


def _selo_texto(g: dict | None) -> tuple[str, str]:
    """O selo M/F em texto puro + classe (o kanban arrastavel nao recebe HTML)."""
    if not g:
        return "grupo M/F não verificado", "esp"
    v = g["veredito"]
    em = g["verificado_em"].strftime("%d/%m/%Y") if g.get("verificado_em") else ""
    return (f"{'grupo M/F ok' if v == 'NEW NAME' else v} · {em}",
            "ok" if v == "NEW NAME" else "err" if v.startswith("CLIENTE") else "esp")


def kanban(etapas: list[dict], cards: list[dict]) -> None:
    """O funil como quadro: uma coluna por etapa (todas, mesmo vazias), na ordem de `crm_etapas`.
    Arrastar um card para outra coluna muda a etapa; clicar abre a ficha."""
    dados_cards = []
    for c in cards:
        txt, cls = _selo_texto(c["grupo"])
        dados_cards.append({
            "id": c["id"], "nome": c["nome"], "etapa": c["etapa"],
            "local": f"{c['cidade']}{('/' + c['estado']) if c['estado'] else ''}",
            "retorno": (f"{'atrasado' if c['atrasado'] else 'retorno'}: {br(c['proximo_contato'])}"
                        if c["proximo_contato"] else ""),
            "atrasado": bool(c["atrasado"]), "selo": txt, "selo_cls": cls,
            "fone": (c["telefones"][0]["exibir"] if c["telefones"] else "sem telefone")})
    st.caption("Arraste o card para outra coluna para mudar a etapa · clique no card para abrir a ficha")
    r = kanban_dnd.quadro([{"nome": e["nome"], "cor": e["cor"]} for e in etapas], dados_cards)
    if r.mover and r.mover.get("n") != ss.get("kb_ultimo"):
        ss.kb_ultimo = r.mover["n"]
        res = dados.mover(int(r.mover["id"]), r.mover["etapa"], ss.quem)
        if res.get("erro"):
            ss.msg = ("erro", res["erro"])
        st.rerun()
    if r.abrir and r.abrir.get("n") != ss.get("kb_ultimo"):
        ss.kb_ultimo = r.abrir["n"]
        ss.lead = int(r.abrir["id"])
        st.query_params["ficha"] = "1"
        st.rerun()


def pagina() -> None:
    with st.sidebar:
        st.markdown("**Discador 3C Plus**")
        s3 = ss.sessao3c
        if s3:
            st.success(f"Ramal {s3['ramal']} logado · {s3.get('campanha_nome', '')}")
            if st.button("Sair da 3C"):
                tresc.sair(s3)
                ss.sessao3c = None
                st.rerun()
        else:
            with st.form("login3c"):
                ramal = st.text_input("Ramal", value="1003")
                senha3c = st.text_input("Senha da 3C", type="password")
                camp = None
                if ss.get("campanhas3c"):
                    op = {c["nome"]: c["id"] for c in ss.campanhas3c}
                    camp = op[st.selectbox("Campanha", list(op))]
                entrar = st.form_submit_button("Entrar na 3C")
            if entrar:
                try:
                    r = tresc.entrar(ramal, senha3c, camp)
                    if r.get("escolher"):
                        ss.campanhas3c = r["escolher"]
                        st.info("Escolha a campanha e clique em Entrar de novo.")
                    else:
                        ss.sessao3c = r["sessao"]
                        ss.pop("campanhas3c", None)
                        st.rerun()
                except tresc.Erro3C as e:
                    st.error(str(e))
            st.caption("Sem entrar aqui, o botão de ligar usa o ramal 1003, que precisa estar logado e "
                       "disponível no discador da 3C.")
        st.divider()
        busca = st.text_input("Buscar empresa, CNPJ ou cidade")
        so_atrasados = st.checkbox("Só retornos atrasados")

    if ss.msg:
        tipo, texto = ss.msg
        {"erro": st.error, "aviso": st.warning}.get(tipo, st.success)(texto)
        ss.msg = None

    if ss.lead:
        ver_ficha(ss.lead)
        return

    q = dados.quadro()
    cards = q["cards"]
    alvo = (busca or "").strip().lower()
    if alvo:
        cards = [c for c in cards if alvo in f"{c['nome']} {c['razao_social']} {c['cnpj']} {c['cidade']}".lower()]
    if so_atrasados:
        cards = [c for c in cards if c["atrasado"]]
    cards.sort(key=lambda c: (c["proximo_contato"] is None, c["proximo_contato"] or "", c["nome"]))

    telas_vendedor.faixa("🗂", "New Names · Motor de Vendas", f"{len(cards)} leads · abra o card para ligar pela 3C")
    aba_funil, aba_agenda = st.tabs(["Funil", "Agenda"])
    with aba_funil:
        kanban(q["etapas"], cards)
    with aba_agenda:
        hoje = dados.agora().date().isoformat()
        marcados = [c for c in q["cards"] if c["proximo_contato"]]
        grupos = {"Atrasados": [c for c in marcados if c["atrasado"] and c["proximo_contato"][:10] < hoje],
                  "Hoje": [c for c in marcados if c["proximo_contato"][:10] == hoje],
                  "Próximos": [c for c in marcados if c["proximo_contato"][:10] > hoje]}
        for titulo, lista in grupos.items():
            st.markdown(f"#### {titulo} ({len(lista)})")
            if not lista:
                st.caption("nada aqui")
            grade(sorted(lista, key=lambda c: c["proximo_contato"]), lambda c: cartao(c, "a"))
