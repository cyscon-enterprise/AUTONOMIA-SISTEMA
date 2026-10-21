"""CRM Cyscon online — PAINELNEWNAMES (secao Motor de Vendas) no Streamlit, sobre o Supabase.

Mesmas tabelas e regras do painel local (`cyscon painel`). Secrets do site (Streamlit > Settings > Secrets):
    APP_SENHA     senha para abrir o CRM
    DATABASE_URL  conexao Postgres do Supabase (pooler, projeto sieqohnzcrjgrhirzocj)
    TRESC_TOKEN   token de servico da 3C Plus
Rodando no PC sem secrets.toml, cai nas variaveis CYSCON_DATABASE_URL / CYSCON_3C_TOKEN.
"""
from __future__ import annotations

import datetime as dt
import hmac
import os

import streamlit as st

import dados
import tresc3c as tresc

st.set_page_config(page_title="CRM Cyscon", page_icon="📞", layout="wide")


def _segredo(nome: str, *alternativas: str) -> str:
    try:
        v = st.secrets.get(nome)
    except Exception:                              # sem secrets.toml (rodando no PC)
        v = None
    if v:
        return str(v)
    for a in (nome, *alternativas):
        if os.environ.get(a):
            return os.environ[a]
    return ""


# ---------------------------------------------------------------- acesso

def exigir_senha() -> None:
    senha = _segredo("APP_SENHA")
    if not senha:
        if os.environ.get("CRM_LOCAL") == "1":     # teste no PC
            return
        st.error("Acesso bloqueado: falta a senha (APP_SENHA) nos Secrets do site.")
        st.stop()
    if st.session_state.get("liberado"):
        return
    st.title("CRM Cyscon")
    with st.form("entrar"):
        digitada = st.text_input("Senha do CRM", type="password")
        ok = st.form_submit_button("Entrar", type="primary")
    if ok:
        if hmac.compare_digest(digitada.encode(), senha.encode()):
            st.session_state["liberado"] = True
            st.rerun()
        st.error("Senha incorreta.")
    st.stop()


exigir_senha()
dados.configurar(_segredo("DATABASE_URL", "CYSCON_DATABASE_URL"))
tresc.configurar(_segredo("TRESC_TOKEN", "CYSCON_3C_TOKEN"))

ss = st.session_state
ss.setdefault("lead", None)
ss.setdefault("quem", "")
ss.setdefault("sessao3c", None)
ss.setdefault("msg", None)

st.markdown("""<style>
.block-container{padding-top:1.4rem}
.selo{font-size:12px;padding:2px 8px;border-radius:5px;display:inline-block}
.ok{background:#e7f6ec;color:#1a7f4b}.err{background:#fdecec;color:#b42318}.esp{background:#fff7ed;color:#b0562a}
.late{color:#b42318;font-weight:600}
.insight{background:#f5f8ff;border:1px solid #dbe6fb;border-left:3px solid #1f6feb;border-radius:8px;
         padding:10px 12px;font-size:14px;line-height:1.5}
</style>""", unsafe_allow_html=True)


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


# ---------------------------------------------------------------- barra lateral: quem e 3C

with st.sidebar:
    st.subheader("CRM Cyscon")
    ss.quem = st.text_input("Seu nome (vai no histórico)", value=ss.quem, placeholder="ex.: Paloma")
    st.divider()
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


def ramal_atual() -> str:
    return (ss.sessao3c or {}).get("ramal") or "1003"


if ss.msg:
    tipo, texto = ss.msg
    {"erro": st.error, "aviso": st.warning}.get(tipo, st.success)(texto)
    ss.msg = None


# ---------------------------------------------------------------- ficha do lead

def bloco_ligacao(lig: dict) -> None:
    """Ligacao recente sem baixa: estado, desligar e dar baixa pelas etapas do funil."""
    with st.container(border=True):
        st.markdown(f"**📞 Ligação para {dados.formatar(lig['telefone'])}** · pedida {br(lig['pedido_em'])} · "
                    f"ramal {lig.get('ramal') or ''}")
        if lig["status"] == "finalizada":
            st.caption(f"Encerrada. {lig.get('resultado') or ''} {('· conversa ' + lig['duracao']) if lig.get('duracao') else ''}")
        elif dados.em_andamento(lig):
            st.caption("Em ligação…")
        else:
            st.caption("Chamando: o ramal toca primeiro, atenda para discar. Se já terminou, dê a baixa abaixo.")
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


def ver_ficha(lead_id: int) -> None:
    f = dados.ficha(lead_id)
    if not f:
        st.error("Lead não encontrado.")
        ss.lead = None
        return
    if any(l["status"] in ("pedindo", "pedida", "incerta") for l in f["ligacoes"]):
        dados.sincronizar(lead_id)                 # traz o resultado assim que a 3C tiver
        f = dados.ficha(lead_id)
    L, est = f["lead"], f["estado"]
    if st.button("← voltar ao funil"):
        ss.lead = None
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
        st.markdown("#### Ligar pela 3C")
        if not f["telefones"]:
            st.caption("Sem telefone.")
        for t in f["telefones"]:
            if st.button(f"☎ {t['exibir']}  ·  {', '.join(t['rotulos'])}", key=f"lig{lead_id}{t['tel']}",
                         use_container_width=True):
                with st.spinner("Pedindo a ligação à 3C (pode levar até 1 minuto)…"):
                    avisar(dados.ligar(lead_id, t["tel"], ss.quem, ramal_atual()))
                st.rerun()
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

    with st.expander("Dados do lead"):
        campos = [("razao_social", "Razão social"), ("cnpj", "CNPJ"), ("cidade", "Cidade"), ("estado", "UF"),
                  ("status_crmtotvs", "Status no CRM TOTVS"), ("codigo_crmtotvs", "Código CRM"),
                  ("entidade", "Entidade"), ("base", "Base"), ("segmento", "Segmento"), ("mrr", "MRR"),
                  ("faturamento_totvs", "Faturamento TOTVS"), ("porte_da_empresa", "Porte"),
                  ("funcionarios", "Funcionários"), ("contato_crmtotvs", "Contato no CRM"),
                  ("contato_de_ti", "Contato TI"), ("contato_rh", "Contato RH"), ("contato_fiscal", "Contato Fiscal"),
                  ("contato_financeiro", "Contato Financeiro"), ("email", "E-mail"), ("site_da_empresa", "Site"),
                  ("historico_crmtotvs", "Histórico no CRM TOTVS")]
        for k, r in campos:
            if L.get(k):
                st.markdown(f"**{r}:** {L[k]}")


# ---------------------------------------------------------------- funil e agenda

def cartao(c: dict, onde: str = "f") -> None:
    with st.container(border=True):
        st.markdown(f"**{c['nome'][:60]}**  \n<span style='color:#666;font-size:13px'>{c['cidade']}"
                    f"{('/' + c['estado']) if c['estado'] else ''}{(' · ' + c['status_crm']) if c['status_crm'] else ''}"
                    f"</span><br>{selo(c['grupo'])}", unsafe_allow_html=True)
        if c["proximo_contato"]:
            st.markdown(f"<span class='{'late' if c['atrasado'] else ''}'>"
                        f"{'atrasado' if c['atrasado'] else 'retorno'}: {br(c['proximo_contato'])}</span>",
                        unsafe_allow_html=True)
        st.caption(" · ".join(t["exibir"] for t in c["telefones"][:3]) or "sem telefone")
        if st.button("Abrir", key=f"abrir{onde}{c['id']}", use_container_width=True):
            ss.lead = c["id"]
            st.rerun()


if ss.lead:
    ver_ficha(ss.lead)
    st.stop()

q = dados.quadro()
cards = q["cards"]
alvo = (busca or "").strip().lower()
if alvo:
    cards = [c for c in cards if alvo in f"{c['nome']} {c['razao_social']} {c['cnpj']} {c['cidade']}".lower()]
if so_atrasados:
    cards = [c for c in cards if c["atrasado"]]
cards.sort(key=lambda c: (c["proximo_contato"] is None, c["proximo_contato"] or "", c["nome"]))

st.title("PAINELNEWNAMES")
st.caption(f"{len(cards)} leads New Names · abra o card para ligar pela 3C")
aba_funil, aba_agenda = st.tabs(["Funil", "Agenda"])
with aba_funil:
    usadas = [e for e in q["etapas"] if any(c["etapa"] == e["nome"] for c in cards)]
    if not usadas:
        st.info("Nenhum lead com esse filtro.")
    else:
        abas = st.tabs([f"{e['nome']} ({sum(c['etapa'] == e['nome'] for c in cards)})" for e in usadas])
        for aba, e in zip(abas, usadas):
            with aba:
                da_etapa = [c for c in cards if c["etapa"] == e["nome"]]
                cols = st.columns(3)
                for i, c in enumerate(da_etapa):
                    with cols[i % 3]:
                        cartao(c)
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
        cols = st.columns(3)
        for i, c in enumerate(sorted(lista, key=lambda c: c["proximo_contato"])):
            with cols[i % 3]:
                cartao(c, "a")
