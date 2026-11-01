"""CRM Cyscon online (Streamlit + Supabase): login por vendedor, discador e carteira.

Cada vendedor entra com login e senha proprios (`crm_vendedores`). O vendedor ve o Discador e a
carteira DELE; o administrador ve tudo, mais o funil New Names (secao Motor de Vendas) e a Gestao.

Secrets do site (Streamlit > Settings > Secrets):
    APP_SENHA     so para o PRIMEIRO acesso: cria o administrador quando ainda nao ha ninguem
    DATABASE_URL  conexao Postgres do Supabase (pooler, projeto sieqohnzcrjgrhirzocj)
    TRESC_TOKEN   token de servico da 3C Plus
Rodando no PC sem secrets.toml, cai nas variaveis CYSCON_DATABASE_URL / CYSCON_3C_TOKEN.
"""
from __future__ import annotations

import hmac
import os
import time

import streamlit as st
import streamlit.components.v1 as components

import dados
import tela_new_names
import telas_vendedor
import tresc3c as tresc
import vendedores as V

PASTA = os.path.dirname(os.path.abspath(__file__))
LOGO = os.path.join(PASTA, "static", "logo.png")          # logo e icone feitos no Canva (31/10/2026)
ICONE = os.path.join(PASTA, "static", "icone.png")

st.set_page_config(page_title="CRM Cyscon", page_icon=ICONE, layout="wide")
st.logo(LOGO, size="large", icon_image=ICONE)


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


dados.configurar(_segredo("DATABASE_URL", "CYSCON_DATABASE_URL"))
tresc.configurar(_segredo("TRESC_TOKEN", "CYSCON_3C_TOKEN"))


@st.cache_resource
def _preparar() -> bool:
    V.preparar()
    return True


_preparar()
ss = st.session_state
ss.setdefault("usuario", None)
ss.setdefault("lead", None)
ss.setdefault("cliente", None)
ss.setdefault("sessao3c", None)
ss.setdefault("msg", None)

st.markdown("""<style>
.block-container{padding-top:3.6rem;max-width:100%;padding-left:2.5rem;padding-right:2.5rem}
[data-testid="stSidebarUserContent"]{padding-top:1rem}
h1,h2,h3{color:#0f2a5c;letter-spacing:-.01em}
h1{font-size:2rem !important}
/* cartoes: fundo branco e sombra leve sobre o fundo cinza-azulado */
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]){background:#fff;
  box-shadow:0 1px 2px rgba(15,42,92,.06),0 4px 14px rgba(15,42,92,.05)}
[data-testid="stForm"]{background:#fff;box-shadow:0 1px 2px rgba(15,42,92,.06),0 4px 14px rgba(15,42,92,.05)}
.stButton button,.stFormSubmitButton button{font-weight:600}
[data-testid="stHeader"]{background:#ffffffee;border-bottom:1px solid #e3e8f2}
.selo{font-size:12px;padding:2px 8px;border-radius:5px;display:inline-block}
.ok{background:#e7f6ec;color:#1a7f4b}.err{background:#fdecec;color:#b42318}.esp{background:#fff7ed;color:#b0562a}
.late{color:#b42318;font-weight:600}
.insight{background:#f5f8ff;border:1px solid #dbe6fb;border-left:3px solid #1565c0;border-radius:8px;
         padding:10px 12px;font-size:14px;line-height:1.5}
.faixa{display:flex;align-items:center;gap:14px;background:linear-gradient(90deg,#0f2a5c,#1565c0);color:#fff;
       border-radius:12px;padding:14px 20px;margin-bottom:14px}
.faixa b{font-size:20px}.faixa span{opacity:.85;font-size:14px}
.kpi{background:#fff;border:1px solid #e3e8f2;border-radius:12px;padding:12px 16px}
.kpi small{color:#5b6b85;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.kpi div{font-size:26px;font-weight:700;color:#0f2a5c;line-height:1.2}
/* cartoes da grade: nome em ate 2 linhas e demais linhas em 1, para caber na altura fixa */
.cart-nome{font-weight:700;color:#0f2a5c;font-size:15.5px;line-height:1.3;height:2.6em;overflow:hidden;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;margin-bottom:4px}
.cart-linha{color:#5b6b85;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:3px 0}
/* barra lateral estreita: sobra tela para o kanban */
section[data-testid="stSidebar"]{width:240px !important;min-width:240px !important;max-width:240px !important}
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"]{padding-left:.9rem;padding-right:.9rem}
.ponto{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
</style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- acesso

def primeiro_acesso() -> None:
    """Ainda nao ha nenhum usuario: quem souber a APP_SENHA cria o administrador."""
    st.title("CRM Cyscon · primeiro acesso")
    st.write("Ainda não há usuários. Crie o administrador.")
    mestra = _segredo("APP_SENHA")
    if not mestra and os.environ.get("CRM_LOCAL") != "1":
        st.error("Falta a APP_SENHA nos Secrets do site.")
        st.stop()
    with st.form("primeiro"):
        chave = st.text_input("Senha do site (APP_SENHA)", type="password") if mestra else ""
        nome = st.text_input("Seu nome")
        login = st.text_input("Login")
        senha = st.text_input("Senha (mín. 8)", type="password")
        ok = st.form_submit_button("Criar administrador", type="primary")
    if ok:
        if mestra and not hmac.compare_digest(chave.encode(), mestra.encode()):
            st.error("Senha do site incorreta.")
            st.stop()
        r = V.criar(nome, login, senha, admin=True)
        if r.get("erro"):
            st.error(r["erro"])
        else:
            ss.usuario = V.entrar(login, senha)
            st.rerun()
    st.stop()


def tela_login() -> None:
    _, meio, _ = st.columns([1, 1.1, 1])
    with meio:
        st.write("")
        st.image(LOGO, use_container_width=True)
        st.markdown("<p style='text-align:center;color:#5b6b85;margin-top:-6px'>Área do vendedor · discador e carteira</p>",
                    unsafe_allow_html=True)
        with st.form("entrar"):
            login = st.text_input("Login")
            senha = st.text_input("Senha", type="password")
            ok = st.form_submit_button("Entrar", type="primary", use_container_width=True)
    if ok:
        u = V.entrar(login, senha)
        if u:
            ss.usuario = u
            st.rerun()
        time.sleep(1)
        st.error("Login ou senha incorretos.")
    st.stop()


if not ss.usuario:
    if not V.tem_vendedor():
        primeiro_acesso()
    tela_login()

ss.quem = ss.usuario["nome"]                       # assina tudo que se registra no CRM

with st.sidebar:
    if ss.get("sessao3c"):
        # o telefone web (WebRTC) da 3C: primeiro da barra (a vista) e fora das paginas, para nao cair
        st.markdown(f"**📞 Telefone · ramal {ss.sessao3c['ramal']}**")
        components.iframe(tresc.webphone_url(ss.sessao3c["token"]), height=360, scrolling=True)
        st.divider()
    st.markdown(f"**{ss.usuario['nome']}**{' · admin' if ss.usuario.get('admin') else ''}")
    if st.button("Sair do CRM"):
        tresc.sair(ss.get("sessao3c"))
        for k in list(ss.keys()):
            del ss[k]
        st.rerun()

paginas = [st.Page(telas_vendedor.pagina_discador, title="Discador", icon="📞", url_path="discador", default=True),
           st.Page(telas_vendedor.pagina_carteira, title="Minha carteira", icon="📇", url_path="carteira")]
if ss.usuario.get("admin"):
    paginas += [st.Page(telas_vendedor.pagina_carteiras_todos, title="Carteiras (todos)", icon="👥",
                        url_path="carteiras"),
                st.Page(tela_new_names.pagina, title="New Names (Motor de Vendas)", icon="🗂", url_path="new-names"),
                st.Page(telas_vendedor.pagina_gestao, title="Gestão", icon="⚙", url_path="gestao")]
st.navigation(paginas, position="top").run()             # menu no topo: a barra fica so para o telefone
