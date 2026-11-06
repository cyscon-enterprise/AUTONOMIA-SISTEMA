"""AutonomIA Vendas (CRM online da Cyscon) (Streamlit + Supabase): login por vendedor, discador e carteira.

Cada vendedor entra com login e senha proprios (`crm_vendedores`). O vendedor ve o Discador e a
carteira DELE; o administrador ve tudo, mais o funil New Names (secao Motor de Vendas) e a Gestao.

Secrets do site (Streamlit > Settings > Secrets):
    APP_SENHA     so para o PRIMEIRO acesso: cria o administrador quando ainda nao ha ninguem
    DATABASE_URL  conexao Postgres do Supabase (pooler, projeto sieqohnzcrjgrhirzocj)
    TRESC_TOKEN   token de servico da 3C Plus
    TRESC_RAMAL / TRESC_RAMAL_SENHA   ramal do discador: o vendedor conecta sem digitar a senha da 3C
    SMTP_USUARIO / SMTP_SENHA   Gmail remetente + senha de app (codigo de confirmacao do cadastro)
Rodando no PC sem secrets.toml, cai nas variaveis CYSCON_DATABASE_URL / CYSCON_3C_TOKEN.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import os
import re
import secrets
import time

import streamlit as st
import streamlit.components.v1 as components

import dados
import email_envio
import ia_ligacoes
import sessao_aba
import tela_new_names
import telas_vendedor
import tresc3c as tresc
import vendedores as V

PASTA = os.path.dirname(os.path.abspath(__file__))
LOGO = os.path.join(PASTA, "static", "logo.png")          # logo e icone feitos no Canva (31/10/2026)
ICONE = os.path.join(PASTA, "static", "icone.png")

st.set_page_config(page_title="AutonomIA Vendas", page_icon=ICONE, layout="wide", initial_sidebar_state="expanded")
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
tresc.configurar_ramal(_segredo("TRESC_RAMAL") or "1003", _segredo("TRESC_RAMAL_SENHA"))
ia_ligacoes.configurar(_segredo("ANTHROPIC_API_KEY"))
email_envio.configurar(_segredo("SMTP_USUARIO"), _segredo("SMTP_SENHA"), _segredo("SMTP_HOST"), _segredo("SMTP_PORTA"))


@st.cache_resource
def _preparar() -> bool:
    V.preparar()
    ia_ligacoes.preparar()
    sessao_aba.preparar()
    return True


_preparar()
ss = st.session_state
ss.setdefault("usuario", None)
ss.setdefault("lead", None)
ss.setdefault("cliente", None)
ss.setdefault("sessao3c", None)
ss.setdefault("msg", None)

st.markdown("""<style>
.block-container{padding-top:2.6rem;max-width:100%;padding-left:2.5rem;padding-right:2.5rem}
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
/* a tela nao "apaga" quando o quadro da ligacao se atualiza (a cada 10 s) */
[data-stale="true"]{opacity:1 !important;transition:none !important}
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
    st.title("AutonomIA Vendas · primeiro acesso")
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
            sessao_aba.gravar(ss.usuario["id"])
            st.rerun()
    st.stop()


def tela_convite(token: str, c: dict) -> None:
    """Link de convite (?convite=...): a pessoa cria o proprio login e senha, de uso unico."""
    _, meio, _ = st.columns([1, 1.1, 1])
    with meio:
        st.write("")
        st.image(LOGO, use_container_width=True)
        st.markdown(f"<p style='text-align:center;color:#5b6b85;margin-top:-6px'>Convite: crie seu acesso de "
                    f"<b>{'administrador' if c['admin'] else 'vendedor'}</b></p>", unsafe_allow_html=True)
        with st.form("convite"):
            nome = st.text_input("Seu nome")
            login = st.text_input("Login (ex.: maria)")
            senha = st.text_input("Senha (mín. 8)", type="password")
            senha2 = st.text_input("Repita a senha", type="password")
            ok = st.form_submit_button("Criar meu acesso", type="primary", use_container_width=True)
        if ok:
            if senha != senha2:
                st.error("As senhas não conferem.")
                st.stop()
            r = V.usar_convite(token, nome, login, senha)
            if r.get("erro"):
                st.error(r["erro"])
                st.stop()
            st.query_params.clear()
            ss.usuario = V.entrar(login, senha)
            sessao_aba.gravar(ss.usuario["id"])
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
            if u and u.get("pendente"):
                st.info("Seu cadastro está em análise. Você vai poder entrar assim que um administrador aprovar.")
                st.stop()
            if u:
                ss.usuario = u
                sessao_aba.gravar(u["id"])
                st.rerun()
            time.sleep(1)
            st.error("Login ou senha incorretos.")
        st.markdown("<p style='text-align:center;color:#5b6b85;margin:14px 0 4px'>Ainda não tem acesso?</p>",
                    unsafe_allow_html=True)
        if st.button("Quero me cadastrar como vendedor autônomo", use_container_width=True):
            ss.tela_cadastro = True
            st.rerun()
    st.stop()


TERMOS = f"""**Termos do Vendedor Parceiro · versão {V.TERMOS_VERSAO}**

- Você trabalha como **autônomo, sem vínculo empregatício**: escolhe se, quando e quanto tempo usa a plataforma. \
Não há horário, metas nem exclusividade.
- Você recebe **10% de comissão** sobre o valor líquido efetivamente pago pelo cliente em cada venda de lead da sua \
carteira. Não há salário, ajuda de custo nem outros pagamentos.
- A empresa entra na sua carteira quando a ligação feita por você termina em Retornar, Enviar e-mail, Agendar reunião \
ou Ganho, ou quando a conversa dura 60 segundos ou mais.
- Se você ficar **90 dias sem ligar** (nem tentativa) para um lead da sua carteira, perde o direito sobre ele.
- **Todas as ligações são gravadas** e podem ser transcritas e resumidas por IA, para comprovar a carteira e \
qualificar os leads.
- Os dados dos leads são da plataforma (LGPD): é proibido copiar, exportar ou usar esses dados fora dela.
- Você usa seu próprio computador, internet e fone, e cuida dos seus tributos.
- O acesso é pessoal: não compartilhe seu login.
"""


CODIGO_VALIDADE_MIN = 15
CODIGO_TENTATIVAS = 5
CODIGO_REENVIO_S = 60


def _mandar_codigo(d: dict) -> dict:
    """Gera um codigo de 6 digitos, manda para o e-mail do cadastro e guarda so o hash nesta sessao."""
    pendente = ss.get("cadastro_codigo")
    if pendente and time.time() - pendente["enviado"] < CODIGO_REENVIO_S:
        return {"erro": f"Aguarde {CODIGO_REENVIO_S} segundos para pedir outro código."}
    codigo = f"{secrets.randbelow(10**6):06d}"
    r = email_envio.enviar_codigo(d["email"], d["nome"], codigo)
    if r.get("erro"):
        return r
    ss.cadastro_codigo = {"dados": d, "hash": hashlib.sha256(codigo.encode()).hexdigest(),
                          "enviado": time.time(), "tentativas": 0}
    return {"ok": True}


def confirmar_email() -> None:
    """Segundo passo do cadastro: digitar o codigo que chegou no e-mail."""
    c = ss.cadastro_codigo
    d = c["dados"]
    st.markdown(f"<p style='text-align:center;color:#5b6b85;margin-top:-6px'>Enviamos um código de 6 números para "
                f"<b>{html.escape(d['email'])}</b>. Confira também a caixa de spam.</p>", unsafe_allow_html=True)
    with st.form("codigo"):
        digitado = st.text_input("Código de confirmação", max_chars=6, placeholder="000000")
        ok = st.form_submit_button("Confirmar e enviar cadastro", type="primary", use_container_width=True)
    if ok:
        if time.time() - c["enviado"] > CODIGO_VALIDADE_MIN * 60:
            st.error("O código venceu. Clique em Reenviar código.")
        elif c["tentativas"] >= CODIGO_TENTATIVAS:
            st.error("Muitas tentativas erradas. Clique em Reenviar código.")
        elif not hmac.compare_digest(hashlib.sha256(re.sub(r"\D", "", digitado).encode()).hexdigest(), c["hash"]):
            c["tentativas"] += 1
            st.error(f"Código incorreto. Restam {CODIGO_TENTATIVAS - c['tentativas']} tentativa(s).")
        else:
            r = V.cadastrar(d, st.context.ip_address)
            if r.get("erro"):
                st.error(r["erro"])
            else:
                ss.pop("cadastro_codigo", None)
                ss.cadastro_ok = d["nome"].split()[0]
                st.rerun()
    c1, c2 = st.columns(2)
    if c1.button("Reenviar código", use_container_width=True):
        r = _mandar_codigo(d)
        if r.get("erro"):
            st.error(r["erro"])
        else:
            st.success("Enviamos um código novo.")
    if c2.button("Corrigir meus dados", use_container_width=True):
        ss.pop("cadastro_codigo", None)
        st.rerun()
    st.stop()


def tela_cadastro() -> None:
    """Botao "Quero me cadastrar": a pessoa cria o login e aceita os termos; entra so depois da aprovacao."""
    _, meio, _ = st.columns([1, 1.3, 1])
    with meio:
        st.write("")
        st.image(LOGO, use_container_width=True)
        if ss.get("cadastro_ok"):
            st.success(f"Cadastro enviado, {ss.cadastro_ok}! Um administrador vai analisar e liberar seu acesso. "
                       "Depois é só entrar com o login e a senha que você criou.")
            if st.button("Voltar para o login", type="primary", use_container_width=True):
                ss.pop("cadastro_ok", None)
                ss.tela_cadastro = False
                st.rerun()
            st.stop()
        if ss.get("cadastro_codigo"):
            confirmar_email()
        st.markdown("<p style='text-align:center;color:#5b6b85;margin-top:-6px'>Cadastro de vendedor autônomo · "
                    "comissão de 10% por venda</p>", unsafe_allow_html=True)
        with st.form("cadastro"):
            nome = st.text_input("Nome completo ou razão social")
            documento = st.text_input("CPF ou CNPJ", placeholder="só os números ou com pontuação")
            c1, c2 = st.columns(2)
            email = c1.text_input("E-mail")
            celular = c2.text_input("Celular / WhatsApp (com DDD)")
            cidade = st.text_input("Cidade / UF")
            c1, c2 = st.columns(2)
            login = c1.text_input("Login (ex.: maria.souza)")
            senha = c2.text_input("Senha (mín. 8)", type="password")
            senha2 = c2.text_input("Repita a senha", type="password")
            st.markdown("**Nossas políticas: leia antes de aceitar**")
            with st.container(height=230, border=True):
                st.markdown(TERMOS)
            aceito = st.checkbox(f"Li e aceito os Termos do Vendedor Parceiro, versão {V.TERMOS_VERSAO}")
            ok = st.form_submit_button("Continuar", type="primary", use_container_width=True)
        if ok:
            if not aceito:
                st.error("Para se cadastrar, marque que leu e aceita os termos.")
            elif senha != senha2:
                st.error("As senhas não conferem.")
            else:
                d = V.validar_cadastro(nome, documento, email, celular, cidade, login, senha)
                if d.get("erro"):
                    st.error(d["erro"])
                else:
                    r = _mandar_codigo(d)
                    if r.get("erro"):
                        st.error(r["erro"])
                    else:
                        st.rerun()
        if st.button("Já tenho login", use_container_width=True):
            ss.tela_cadastro = False
            st.rerun()
    st.stop()


if not ss.usuario and st.query_params.get("convite"):
    _c = V.convite(st.query_params["convite"])
    if _c:
        tela_convite(st.query_params["convite"], _c)

if not ss.usuario:
    # F5 ou pagina recarregada: a aba guarda o token da sessao (sessao_aba.py); so pede senha se nao houver
    _tok = sessao_aba.ler_token(apagar=bool(ss.pop("_sair", False)))
    if _tok is None:
        st.stop()                                  # o navegador ainda vai responder (roda de novo sozinho)
    _s = sessao_aba.retomar(_tok)
    _u = V.por_id(_s["vendedor_id"]) if _s else None
    if _u:
        ss.usuario, ss._token_aba = _u, _tok
        ss.empresa = _s["empresa"]
        ss.sessao3c = _s["sessao3c"]
        ss._salvo = (_s["empresa"], _s["sessao3c"])

if not ss.usuario:
    if not V.tem_vendedor():
        primeiro_acesso()
    if ss.get("tela_cadastro"):
        tela_cadastro()
    tela_login()

sessao_aba.escrever_na_aba()
if ss.get("_salvo") != (ss.get("empresa"), ss.get("sessao3c")):    # empresa ou 3C mudou: guarda p/ o F5
    sessao_aba.salvar_estado(ss.get("empresa"), ss.get("sessao3c"))
    ss._salvo = (ss.get("empresa"), ss.get("sessao3c"))

ss.quem = ss.usuario["nome"]                       # assina tudo que se registra no CRM


def escolher_empresa() -> None:
    """Logo depois do login: o vendedor escolhe para qual empresa vai trabalhar nesta sessao."""
    empresas = V.empresas_parceiras()
    st.write("")
    _, meio, _ = st.columns([1, 2.2, 1])
    with meio:
        st.image(LOGO, width=340)
        st.markdown(f"### Olá, {ss.usuario['nome'].split()[0]},")
        st.markdown("Bem-vindo à **AutonomIA Vendas**, sua plataforma de vendedor autônomo com assistente "
                    "pessoal de IA.")
        st.markdown("#### Para qual empresa você vai vender hoje?")
        st.caption("Cada empresa tem a sua base de clientes, o seu produto e a sua comissão.")
        if not empresas:
            st.info("Nenhuma empresa disponível no momento.")
        cols = st.columns(2)
        for i, e in enumerate(empresas):
            with cols[i % 2].container(border=True):
                st.markdown(f"#### {e['nome']}")
                st.write(e.get("descricao") or "")
                if e.get("produto"):
                    st.caption(f"Produto: {e['produto']}")
                # deixa claro, antes de comecar, que a unica remuneracao e a comissao (contrato sem vinculo)
                st.markdown(
                    f"<div style='background:#eef4ff;border-radius:8px;padding:10px 14px;margin:6px 0 10px'>"
                    f"<div style='font-size:17px'><b>Comissão: {float(e['comissao_pct']):g}%</b> sobre as vendas</div>"
                    f"<div style='font-size:17px'><b>Outros pagamentos: não</b></div>"
                    f"<div style='color:#5b6b85;font-size:13px;margin-top:4px'>Sem salário, ajuda de custo ou valor "
                    f"por hora: você recebe só a comissão das vendas que fizer.</div></div>",
                    unsafe_allow_html=True)
                if st.button(f"Trabalhar com a {e['nome']}", key=f"emp{e['id']}", type="primary",
                             use_container_width=True):
                    ss.empresa = {"id": e["id"], "nome": e["nome"]}
                    st.rerun()
    st.stop()


if not ss.get("empresa"):
    _emps = V.empresas_parceiras()
    if len(_emps) == 1:                            # uma empresa so: nao ha o que escolher
        ss.empresa = {"id": _emps[0]["id"], "nome": _emps[0]["nome"]}
    else:
        escolher_empresa()

with st.sidebar:
    # tudo da 3C fica aqui: o telefone web (WebRTC) fora das paginas nao cai ao trocar de pagina
    if ss.get("sessao3c"):
        st.markdown(f"**📞 Telefone · ramal {ss.sessao3c['ramal']}**")
        telas_vendedor.painel_ramal(ss.sessao3c)       # status e botoes no topo, a vista sem rolar
        if st.button("■ Encerrar meu turno", use_container_width=True):
            with st.spinner("Saindo da 3C e fechando a conta do turno…"):
                telas_vendedor.sair_3c()
                telas_vendedor.avisar(V.encerrar_turno(ss.usuario))
            st.rerun()
        components.iframe(tresc.webphone_url(ss.sessao3c["token"]), height=300, scrolling=True)
    else:
        st.markdown("**📞 Telefone**")
        telas_vendedor.conectar_3c_lateral()
    st.divider()
    st.markdown(f"**{ss.usuario['nome']}**{' · admin' if ss.usuario.get('admin') else ''}  \n"
                f"Empresa: **{ss.empresa['nome']}**")
    if len(V.empresas_parceiras()) > 1 and st.button("Trocar de empresa"):
        ss.pop("empresa", None)
        st.rerun()
    if st.button("Sair"):
        tresc.sair(ss.get("sessao3c"))
        sessao_aba.encerrar()
        for k in list(ss.keys()):
            del ss[k]
        ss._sair = True                            # na proxima rodada o token sai da aba tambem
        st.rerun()

# vendedor: discador, carteira e funil. Administrador: a visao geral do CRM (abre nela) + tudo do vendedor
admin = bool(ss.usuario.get("admin"))
paginas = [st.Page(telas_vendedor.pagina_discador, title="Discador", icon="📞", url_path="discador", default=not admin),
           st.Page(telas_vendedor.pagina_carteira, title="Minha carteira", icon="📇", url_path="carteira"),
           st.Page(tela_new_names.pagina, title="Funil New Names", icon="🗂", url_path="new-names"),
           st.Page(telas_vendedor.pagina_funil_servicos, title="Funil Serviços", icon="🛠", url_path="servicos")]
if admin:
    paginas = [st.Page(telas_vendedor.pagina_visao_geral, title="Visão geral", icon="📊", url_path="visao-geral",
                       default=True)] + paginas + [
        st.Page(telas_vendedor.pagina_carteiras_todos, title="Carteiras (todos)", icon="👥", url_path="carteiras"),
        st.Page(telas_vendedor.pagina_gestao, title="Gestão", icon="⚙", url_path="gestao")]
st.navigation(paginas, position="top").run()             # menu no topo: a barra fica so para o telefone
