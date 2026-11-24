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

import contrato
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
tresc.configurar_campanha_new_names(_segredo("CAMPANHA_NEW_NAMES"))
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
/* largura fixa so com a barra ABERTA: fechada, ela some e a pagina ocupa a tela toda */
section[data-testid="stSidebar"][aria-expanded="true"]{width:240px !important;min-width:240px !important;max-width:240px !important}
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
        # cadastro aberto desligado por enquanto (pedido da dona): o botao fica a vista, cinza e sem acao
        CADASTRO_ABERTO = os.environ.get("CADASTRO_ABERTO") == "1"   # liga so quando a dona liberar
        if st.button("Quero me cadastrar como vendedor parceiro (PJ)", use_container_width=True,
                     disabled=not CADASTRO_ABERTO, help=None if CADASTRO_ABERTO else "Cadastro em breve"):
            ss.tela_cadastro = True
            st.rerun()
    st.stop()


CODIGO_VALIDADE_MIN = 15
CODIGO_TENTATIVAS = 5
CODIGO_REENVIO_S = 60


def _anexos(d: dict) -> list:
    """PDFs que vao no e-mail do codigo: o contrato preenchido e as politicas aceitas (gerados uma vez por cadastro)."""
    if "_anexos" not in d:
        d["_anexos"] = [
            ("Contrato_AutonomIA_Vendas.pdf", contrato.pdf([d["contrato_texto"]], "Contrato de prestação de serviços"),
             "application/pdf"),
            (f"Politicas_AutonomIA_Vendas_v{V.TERMOS_VERSAO}.pdf",
             contrato.pdf([V.texto_doc(k) for k, _ in V.DOCS[1:]], "Políticas AutonomIA Vendas"), "application/pdf")]
    return d["_anexos"]


def _mandar_codigo(d: dict) -> dict:
    """Gera um codigo de 6 digitos, manda para o e-mail do cadastro (com o contrato e as politicas aceitas em PDF)
    e guarda so o hash nesta sessao."""
    pendente = ss.get("cadastro_codigo")
    if pendente and time.time() - pendente["enviado"] < CODIGO_REENVIO_S:
        return {"erro": f"Aguarde {CODIGO_REENVIO_S} segundos para pedir outro código."}
    if time.time() - ss.get("_cad_ultimo_envio", 0) < 10:
        return {"erro": "Aguarde alguns segundos."}
    envios = [t for t in ss.get("_cad_envios", []) if time.time() - t < 3600]
    if len(envios) >= 5:
        return {"erro": "Muitos envios de código nesta hora. Tente mais tarde."}
    codigo = f"{secrets.randbelow(10**6):06d}"
    r = email_envio.enviar_codigo_cadastro(d["email"], d["nome"], codigo, _anexos(d), [t for _, t in V.DOCS],
                                           V.TERMOS_VERSAO)
    if r.get("erro"):
        return r
    ss._cad_envios, ss._cad_ultimo_envio = envios + [time.time()], time.time()
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
                ss.pop("cad_pre", None)
                for k in [k for k in ss.keys() if k.startswith("c_")]:
                    del ss[k]
                ss.cadastro_ok = d["nome"].split()[0]
                st.rerun()
    c1, c2 = st.columns(2)
    if c1.button("Reenviar código", use_container_width=True):
        r = _mandar_codigo(d)
        if r.get("erro"):
            st.error(r["erro"])
        else:
            st.success("Enviamos um código novo.")
    if c2.button("Voltar aos documentos", use_container_width=True):
        ss.pop("cadastro_codigo", None)
        st.rerun()
    st.stop()


def _buscar_cnpj() -> None:
    """Botao "Buscar dados": preenche razao social, endereco etc. pelo CNPJ (Receita)."""
    r = V.consulta_cnpj(ss.get("c_emp_cnpj", ""))
    if r.get("erro"):
        ss.c_aviso = ("erro", r["erro"])
    elif r.get("indisponivel"):
        ss.c_aviso = ("info", "A consulta da Receita está fora do ar agora. Preencha os dados à mão.")
    else:
        for k, v in (("emp_razao", "razao"), ("emp_fantasia", "fantasia"), ("emp_cep", "cep"),
                     ("emp_logradouro", "logradouro"), ("emp_numero", "numero"), ("emp_complemento", "complemento"),
                     ("emp_bairro", "bairro"), ("emp_cidade", "cidade"), ("emp_uf", "uf"),
                     ("emp_email", "email"), ("emp_telefone", "telefone")):
            if r.get(v):
                ss["c_" + k] = r[v]
        if r["situacao"].lower().startswith("ativa"):
            ss.c_aviso = ("ok", f"Dados carregados: {r['razao']}. Confira e complete o que faltar.")
        else:
            ss.c_aviso = ("erro", f"Esse CNPJ consta como {r['situacao'] or 'não ativo'} na Receita Federal.")


def _buscar_cep() -> None:
    r = V.consulta_cep(ss.get("c_emp_cep", ""))
    if r.get("ok"):
        for k in ("logradouro", "bairro", "cidade", "uf"):
            if r.get(k):
                ss["c_emp_" + k] = r[k]
    elif r.get("erro"):
        ss.c_aviso = ("erro", r["erro"])


def _ler_formulario() -> dict:
    sim = lambda k: None if ss.get(k) is None else ss[k] == "Sim"
    return {"emp_cnpj": ss.get("c_emp_cnpj"), "emp_razao": ss.get("c_emp_razao"),
            "emp_fantasia": ss.get("c_emp_fantasia"), "emp_cep": ss.get("c_emp_cep"),
            "emp_logradouro": ss.get("c_emp_logradouro"), "emp_numero": ss.get("c_emp_numero"),
            "emp_complemento": ss.get("c_emp_complemento"), "emp_bairro": ss.get("c_emp_bairro"),
            "emp_cidade": ss.get("c_emp_cidade"), "emp_uf": ss.get("c_emp_uf"),
            "emp_email": ss.get("c_emp_email"), "emp_telefone": ss.get("c_emp_telefone"),
            "nome": ss.get("c_nome"), "cpf": ss.get("c_cpf"), "email": ss.get("c_email"),
            "celular": ss.get("c_celular"),
            "exp_vendas": sim("c_exp_vendas"),
            "exp_vendas_anos": int(ss["c_exp_vendas_anos"]) if ss.get("c_exp_vendas") == "Sim" else None,
            "exp_totvs": sim("c_exp_totvs"), "exp_outro_erp": sim("c_exp_outro_erp"),
            "exp_outro_erp_qual": ss.get("c_exp_outro_erp_qual"),
            "perfil": {"Perfil 1": 1, "Perfil 2": 2}.get((ss.get("c_perfil") or "")[:8]),
            "disp_vezes": ss.get("c_disp_vezes"), "disp_periodos": ss.get("c_disp_periodos") or [],
            "login": ss.get("c_login"), "senha": ss.get("c_senha")}


def _md(t: str) -> str:
    return re.sub(r"(?<!\n)\n(?!\n)", "  \n", t)


def etapa_documentos() -> None:
    """Passo 2: o contrato ja preenchido com os dados do passo 1 e as politicas, cada uma com o seu aceite."""
    pre = ss.cad_pre
    st.markdown("<p style='text-align:center;color:#5b6b85;margin-top:-6px'><b>Passo 2 de 3 · leia e aceite</b></p>",
                unsafe_allow_html=True)
    st.info("Este é o seu contrato, já preenchido com os dados do passo 1. Leia cada aba e marque o aceite de cada "
            f"documento (versão {V.TERMOS_VERSAO}). Depois enviamos um código por e-mail, com uma cópia de tudo em PDF.")
    abas = st.tabs([t for _, t in V.DOCS])
    for aba, (chave, titulo) in zip(abas, V.DOCS):
        with aba:
            with st.container(height=420, border=True):
                st.markdown(_md(pre["texto"]) if chave == "01_contrato" else V.texto_doc(chave))
            st.checkbox(f"Li e aceito: {titulo}", key=f"c_aceito_{chave}")
    faltam = [t for ch, t in V.DOCS if not ss.get(f"c_aceito_{ch}")]
    if faltam:
        st.caption("Falta aceitar: " + ", ".join(faltam) + ".")
    c1, c2 = st.columns(2)
    if c1.button("← Voltar e corrigir meus dados", use_container_width=True):
        ss.update(pre["raw"])                      # os campos do passo 1 voltam preenchidos
        ss.pop("cad_pre", None)
        st.rerun()
    if c2.button("Aceitar e receber o código", type="primary", use_container_width=True, disabled=bool(faltam)):
        d = {**pre["d"], "form": pre["form"], "contrato_texto": pre["texto"], "contrato_hash": pre["hash"],
             "aceites": "; ".join(f"{t} (v{V.TERMOS_VERSAO})" for _, t in V.DOCS)}
        with st.spinner("Gerando seus documentos e enviando o e-mail…"):
            r = _mandar_codigo(d)
        if r.get("erro"):
            st.error(r["erro"])
        else:
            st.rerun()
    st.stop()


def tela_cadastro() -> None:
    """Botao "Quero me cadastrar" (so PJ): empresa, representante, perfil comercial e disponibilidade.
    A pessoa cria o login e aceita os termos; so entra depois que o gestor aprovar."""
    _, meio, _ = st.columns([1, 1.5, 1])
    with meio:
        st.write("")
        st.image(LOGO, use_container_width=True)
        if ss.get("cadastro_ok"):
            st.success(f"Cadastro enviado, {ss.cadastro_ok}! O gestor vai analisar e liberar seu acesso. "
                       "Depois é só entrar com o login e a senha que você criou.")
            if st.button("Voltar para o login", type="primary", use_container_width=True):
                ss.pop("cadastro_ok", None)
                ss.tela_cadastro = False
                st.rerun()
            st.stop()
        if ss.get("cadastro_codigo"):
            confirmar_email()
        if ss.get("cad_pre"):
            etapa_documentos()
        st.markdown("<p style='text-align:center;color:#5b6b85;margin-top:-6px'>Cadastro de vendedor parceiro "
                    "(pessoa jurídica) · comissão de 10% por venda<br><b>Passo 1 de 3 · seus dados</b></p>",
                    unsafe_allow_html=True)

        st.markdown("#### A. Dados da empresa")
        c1, c2 = st.columns([3, 1.4])
        c1.text_input("CNPJ", key="c_emp_cnpj", placeholder="00.000.000/0000-00")
        c2.write("")
        c2.write("")
        c2.button("Buscar dados", on_click=_buscar_cnpj, use_container_width=True,
                  help="Preenche razão social e endereço pela Receita Federal")
        if ss.get("c_aviso"):
            tipo, msg = ss.c_aviso
            {"erro": st.error, "ok": st.success}.get(tipo, st.info)(msg)
        st.text_input("Razão social", key="c_emp_razao")
        st.text_input("Nome fantasia (se houver)", key="c_emp_fantasia")
        c1, c2 = st.columns([1.3, 1])
        c1.text_input("CEP", key="c_emp_cep", on_change=_buscar_cep, placeholder="00000-000")
        c2.text_input("Número", key="c_emp_numero")
        st.text_input("Logradouro", key="c_emp_logradouro")
        c1, c2 = st.columns(2)
        c1.text_input("Complemento", key="c_emp_complemento")
        c2.text_input("Bairro", key="c_emp_bairro")
        c1, c2 = st.columns([3, 1])
        c1.text_input("Cidade", key="c_emp_cidade")
        c2.text_input("UF", key="c_emp_uf", max_chars=2)
        c1, c2 = st.columns(2)
        c1.text_input("E-mail da empresa", key="c_emp_email")
        c2.text_input("Telefone da empresa (com DDD)", key="c_emp_telefone")

        st.markdown("#### B. Representante da empresa")
        st.text_input("Nome completo", key="c_nome")
        c1, c2 = st.columns(2)
        c1.text_input("CPF", key="c_cpf", placeholder="000.000.000-00")
        c2.text_input("Celular / WhatsApp (com DDD)", key="c_celular")
        st.text_input("E-mail do representante (é o que recebe o código de confirmação)", key="c_email")

        st.markdown("#### C. Perfil comercial")
        c1, c2 = st.columns([2, 1])
        c1.radio("Tem experiência com vendas?", ["Sim", "Não"], index=None, horizontal=True, key="c_exp_vendas")
        if ss.get("c_exp_vendas") == "Sim":
            c2.number_input("Quantos anos?", min_value=1, max_value=60, value=1, step=1, key="c_exp_vendas_anos")
        st.radio("Tem experiência com ERP/TOTVS?", ["Sim", "Não"], index=None, horizontal=True, key="c_exp_totvs")
        c1, c2 = st.columns([2, 1.4])
        c1.radio("Tem experiência com outro ERP?", ["Sim", "Não"], index=None, horizontal=True, key="c_exp_outro_erp")
        if ss.get("c_exp_outro_erp") == "Sim":
            c2.text_input("Qual?", key="c_exp_outro_erp_qual")

        st.markdown("#### D. Perfil de atuação (Anexo I do contrato)")
        st.radio("Escolha o perfil", [
            "Perfil 1 – Prospecção e agendamento: identificar empresas, fazer o contato inicial, qualificar e agendar "
            "reuniões para a Cyscon.",
            "Perfil 2 – Prospecção, qualificação e acompanhamento de contatos: tudo do Perfil 1, mais atualizar o "
            "histórico, organizar retornos e verificar a continuidade do interesse."], index=None, key="c_perfil")
        st.caption("Nenhum perfil inclui propostas, negociação de preços ou fechamento de vendas: isso é feito pela Cyscon.")

        st.markdown("#### E. Disponibilidade (informativa)")
        st.caption("Não é obrigação nem jornada. Serve só para organizar a fila de turnos do discador, porque a linha de "
                   "discagem é compartilhada. Você escolhe se e quando usar a plataforma.")
        st.radio("Quantas vezes por semana, no máximo?", [1, 2, 3, 4, 5], index=None, horizontal=True,
                 key="c_disp_vezes", format_func=lambda n: f"Até {n}")
        st.multiselect("Períodos disponíveis", V.PERIODOS, key="c_disp_periodos")

        st.markdown("#### Acesso")
        c1, c2 = st.columns(2)
        c1.text_input("Login (ex.: maria.souza)", key="c_login")
        c2.text_input("Senha (mín. 8)", type="password", key="c_senha")
        c2.text_input("Repita a senha", type="password", key="c_senha2")
        if st.button("Prosseguir", type="primary", use_container_width=True):
            if ss.get("c_senha") != ss.get("c_senha2"):
                st.error("As senhas não conferem.")
            else:
                form = _ler_formulario()
                d = V.validar_cadastro(form)
                if d.get("erro"):
                    st.error(d["erro"])
                else:
                    c = contrato.montar(d)
                    ss.cad_pre = {"form": form, "d": d, "texto": c["texto"], "hash": c["hash"],
                                  "raw": {k: ss[k] for k in list(ss.keys()) if k.startswith("c_")}}
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


def tela_pagamento() -> None:
    """Primeiro acesso depois da aprovacao: onde a empresa recebe a comissao (entra no contrato).
    A conta e a chave PIX tem de ser da PJ (mesmo CNPJ do cadastro)."""
    u = ss.usuario
    v = V.por_id(u["id"]) or u
    _, meio, _ = st.columns([1, 1.5, 1])
    with meio:
        st.write("")
        st.image(LOGO, use_container_width=True)
        st.markdown(f"### Cadastro aprovado, {u['nome'].split()[0]}!")
        st.info("Falta só um passo: informe onde a sua empresa vai receber a comissão. Esses dados entram no "
                "contrato e só o gestor tem acesso a eles.")
        st.markdown(f"**Titular:** {v.get('emp_razao') or '—'}  \n**CNPJ:** {v['emp_cnpj']}  \n"
                    "<span style='color:#5b6b85'>A conta e a chave PIX precisam estar no CNPJ da empresa.</span>",
                    unsafe_allow_html=True)
        st.markdown("#### Conta bancária")
        c1, c2 = st.columns(2)
        banco = c1.selectbox("Banco", [""] + V.BANCOS, key="p_banco")
        tipo = c2.selectbox("Tipo de conta", [""] + V.TIPOS_CONTA, key="p_tipo")
        c1, c2 = st.columns(2)
        agencia = c1.text_input("Agência (sem dígito)", key="p_agencia")
        conta = c2.text_input("Conta com dígito", key="p_conta", placeholder="12345-6")
        st.markdown("#### Chave PIX")
        c1, c2 = st.columns([1, 2])
        pix_tipo = c1.selectbox("Tipo da chave", [""] + V.TIPOS_PIX, key="p_pix_tipo")
        pix = c2.text_input("Chave PIX", key="p_pix")
        st.caption("Preencha a conta, o PIX ou os dois.")
        if st.button("Salvar e continuar", type="primary", use_container_width=True):
            r = V.salvar_pagamento(u, {"banco": banco, "tipo_conta": tipo, "agencia": agencia, "conta": conta,
                                       "pix_tipo": pix_tipo, "pix_chave": pix})
            if r.get("erro"):
                st.error(r["erro"])
            else:
                ss.usuario["pg_atualizado_em"] = dados.agora()
                st.rerun()
        if st.button("Sair", use_container_width=True):
            sessao_aba.encerrar()
            for k in list(ss.keys()):
                del ss[k]
            ss._sair = True
            st.rerun()
    st.stop()


def _pdf_contrato(vid: int) -> bytes:
    return contrato.pdf([V.contrato_texto(vid)], "Contrato de prestação de serviços")


def painel_contrato(c: dict, chave: str) -> None:
    """Baixar o contrato, assinar no gov.br e enviar o PDF assinado."""
    st.markdown("**1.** Baixe o contrato, **2.** assine em [assinador.iti.br](https://assinador.iti.br) com a sua conta "
                "gov.br e **3.** envie o PDF assinado aqui.")
    st.download_button("⬇ Baixar o contrato (PDF)", _pdf_contrato(c["vendedor_id"]),
                       file_name="Contrato_AutonomIA_Vendas.pdf", mime="application/pdf", key=f"dl_{chave}",
                       use_container_width=True)
    up = st.file_uploader("Contrato assinado (PDF)", type=["pdf"], key=f"up_{chave}")
    if up is not None and st.button("Enviar contrato assinado", type="primary", key=f"env_{chave}",
                                    use_container_width=True):
        r = V.enviar_contrato_assinado(c["vendedor_id"], up.name, up.getvalue())
        if r.get("erro"):
            st.error(r["erro"])
        else:
            ss.msg = ("ok", r["mensagem"])
            st.rerun()


def tela_contrato_bloqueado(c: dict) -> None:
    _, meio, _ = st.columns([1, 1.5, 1])
    with meio:
        st.write("")
        st.image(LOGO, use_container_width=True)
        st.error("Seu acesso está bloqueado: o prazo de 7 dias para enviar o contrato assinado terminou.")
        if c.get("obs"):
            st.warning(f"Observação do gestor: {c['obs']}")
        painel_contrato(c, "pg")
        st.caption("Assim que você enviar o arquivo, o acesso é liberado.")
        if st.button("Sair", use_container_width=True):
            sessao_aba.encerrar()
            for k in list(ss.keys()):
                del ss[k]
            ss._sair = True
            st.rerun()
    st.stop()


_ctr = None if ss.usuario.get("admin") else V.contrato_do(ss.usuario["id"])
if _ctr and _ctr["situacao"] == "bloqueado":
    tela_contrato_bloqueado(_ctr)


if V.precisa_pagamento(ss.usuario):
    tela_pagamento()


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
    if _ctr and _ctr["situacao"] == "prazo":
        st.warning(f"📄 Contrato pendente: assine pelo gov.br e envie em até {int(_ctr['dias'])} dia(s) "
                   "para não bloquear o acesso.")
        if _ctr.get("obs"):
            st.caption(f"Observação do gestor: {_ctr['obs']}")
        with st.expander("Assinar e enviar o contrato"):
            painel_contrato(_ctr, "sb")
    elif _ctr and _ctr["status"] == "enviado":
        st.caption("📄 Contrato enviado, aguardando conferência do gestor.")
    telas_vendedor.batimento()                          # sinal de vida: aba fechada libera o ramal sozinho
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
telas_vendedor.PAGINA_DISCADOR = st.Page(telas_vendedor.pagina_discador, title="Discador", icon="📞",
                                         url_path="discador", default=not admin)
paginas = [telas_vendedor.PAGINA_DISCADOR,
           st.Page(telas_vendedor.pagina_carteira, title="Minha carteira", icon="📇", url_path="carteira"),
           st.Page(tela_new_names.pagina, title="Funil New Names", icon="🗂", url_path="new-names"),
           st.Page(telas_vendedor.pagina_funil_servicos, title="Funil Serviços", icon="🛠", url_path="servicos")]
if admin:
    paginas = [st.Page(telas_vendedor.pagina_visao_geral, title="Visão geral", icon="📊", url_path="visao-geral",
                       default=True)] + paginas + [
        st.Page(telas_vendedor.pagina_carteiras_todos, title="Carteiras (todos)", icon="👥", url_path="carteiras"),
        st.Page(telas_vendedor.pagina_pool, title="Pool de contas", icon="🗃", url_path="pool"),
        st.Page(telas_vendedor.pagina_gestao, title="Gestão", icon="⚙", url_path="gestao")]
# clicar na aba em que ja se esta nao recarrega a pagina no Streamlit: este ouvinte avisa o servidor
_NAV_JS = """
export default function(component) {
  const { setTriggerValue } = component;
  if (window.__navFicha) { window.__navFicha.set = setTriggerValue; return; }
  window.__navFicha = { set: setTriggerValue };
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-testid="stTopNavLink"]');
    if (a && new URL(a.href).pathname === location.pathname) window.__navFicha.set('mesma', Date.now());
  }, true);
}
"""
_nav = st.components.v2.component("nav_ficha", js=_NAV_JS)
if _nav(key="nav_ficha", on_mesma_change=lambda: None).mesma:
    st.query_params.pop("ficha", None)

pg = st.navigation(paginas, position="top")                # menu no topo: a barra fica so para o telefone
# clicar numa aba do menu abre a LISTA dela, nao o cliente que estava aberto antes. O cliente aberto deixa a marca
# ?ficha na URL; o menu navega sem ela (mesmo clicando na aba em que ja se esta) e isso fecha a ficha.
# a pagina inicial tem url_path "" (endereco principal): por isso o Discador e reconhecido pelo titulo
_pagina = "discador" if pg.title == "Discador" else (pg.url_path or pg.title)
if ss.get("_pagina") != _pagina or "ficha" not in st.query_params:
    ss.cliente = None
    ss.lead = None
ss._pagina = _pagina
if ss.pop("_ir_discador", False) and _pagina != "discador":        # clicou "Comecar a receber ligacoes" em outra aba
    st.switch_page(telas_vendedor.PAGINA_DISCADOR)
pg.run()
_pop = ss.pop("_popup", None)                       # ligacao atendida com o vendedor em outra aba: pop-up por cima
if _pop and _pagina != "discador" and _pop.get("id") not in ss.get("baixadas", set()):
    telas_vendedor.popup_ligacao(_pop)
