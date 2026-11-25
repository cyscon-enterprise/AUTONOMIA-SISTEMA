"""Area do vendedor: login proprio, turno no discador e carteira de clientes.

## Como funciona (desenho do usuario, 30/10/2026)

A base e a mestra (`empresas`, Supabase). Ela vai como mailing para a campanha da 3C pelo
`cyscon 3c-enviar` do PC, com o CNPJ como `identifier` de cada item. A 3C tem UM usuario so: quem
diferencia o vendedor e o login no CRM Cyscon.

    1. o vendedor entra no CRM com login e senha (tabela `crm_vendedores`)
    2. clica "Iniciar discador": abre um TURNO dele (`crm_discador_turnos`). So existe um turno
       aberto por vez, porque a 3C so tem um usuario
    3. o discador liga; toda chamada da campanha feita dentro do turno e DELE
    4. se ele conseguiu falar (qualificacao de conversa, ou conversa longa sem qualificacao), a
       empresa entra na CARTEIRA dele (`crm_carteira`) e aparece na area de trabalho dele

Toda chamada do discador que chega aqui fica em `crm_discador_chamadas`, com o id da 3C e se ha
gravacao: e dali que sai a analise das gravacoes, depois, para qualificar melhor.

A carteira mexe na MESMA mestra do painel local: `Responsavel`, `Etapa CRM`, `Proximo Contato` e o
historico em `crm_interacoes`. Nada de copia.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
import secrets

import sqlalchemy as sa

import dados
import ia_auto
import tresc3c as tresc

# Qualificacoes da 3C que querem dizer "o vendedor falou com alguem e ha o que trabalhar".
# "Sem interesse", "Contato sem perfil" e "Caixa postal" ficam so registradas.
QUALIF_CARTEIRA = {"retornar", "enviar e-mail", "agendar reuniao", "ganho"}
# sem qualificacao, conversa a partir disto tambem entra (como "a qualificar"): melhor o vendedor
# ter o cliente na mao do que perder uma conversa porque esqueceu de dar a baixa
CONVERSA_MIN_S = 60
# qualificacao da 3C -> etapa da mestra, so quando a empresa ainda esta no comeco do funil
ETAPA_DA_QUALIF = {"retornar": "retornar", "enviar e-mail": "enviar e-mail",
                   "agendar reuniao": "reunião agendada", "ganho": "fechado"}
ETAPAS_INICIAIS = {"", "sem contato"}
TURNO_MAX_H = 12                   # turno esquecido aberto fecha sozinho depois disso
INATIVO_MIN = 5                    # sem sinal da pagina do vendedor por tanto tempo: o turno fecha (aba fechada)
SYNC_INTERVALO_S = 60

DDL = [
    """CREATE TABLE IF NOT EXISTS crm_vendedores (
        id SERIAL PRIMARY KEY,
        nome VARCHAR(60) NOT NULL,
        login VARCHAR(40) NOT NULL UNIQUE,
        senha_hash TEXT NOT NULL,
        admin BOOLEAN NOT NULL DEFAULT FALSE,
        ativo BOOLEAN NOT NULL DEFAULT TRUE,
        criado_em TIMESTAMP NOT NULL,
        ultimo_acesso TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS crm_discador_turnos (
        id SERIAL PRIMARY KEY,
        vendedor_id INTEGER NOT NULL REFERENCES crm_vendedores(id),
        inicio TIMESTAMP NOT NULL,
        fim TIMESTAMP,
        fechado_por VARCHAR(60))""",
    "ALTER TABLE crm_discador_turnos ADD COLUMN IF NOT EXISTS ultimo_sinal TIMESTAMP",
    """CREATE TABLE IF NOT EXISTS crm_turno_fila (
        id SERIAL PRIMARY KEY,
        data DATE NOT NULL,
        turno INTEGER NOT NULL,
        vendedor_id INTEGER NOT NULL REFERENCES crm_vendedores(id),
        origem VARCHAR(12) NOT NULL,
        criado_em TIMESTAMP NOT NULL,
        UNIQUE (data, turno, vendedor_id))""",
    """CREATE TABLE IF NOT EXISTS crm_discador_chamadas (
        chamada_3c VARCHAR(40) PRIMARY KEY,
        cnpj VARCHAR(14),
        telefone VARCHAR(20),
        quando TIMESTAMP,
        turno_id INTEGER REFERENCES crm_discador_turnos(id),
        vendedor_id INTEGER REFERENCES crm_vendedores(id),
        qualificacao VARCHAR(80),
        resultado VARCHAR(80),
        duracao VARCHAR(12),
        gravada BOOLEAN,
        entrou_carteira BOOLEAN NOT NULL DEFAULT FALSE,
        lido_em TIMESTAMP NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_disc_cham_cnpj ON crm_discador_chamadas (cnpj)",
    """CREATE TABLE IF NOT EXISTS crm_servicos_cards (
        cnpj VARCHAR(14) PRIMARY KEY,
        vendedor_id INTEGER REFERENCES crm_vendedores(id),
        campanha VARCHAR(120),
        chamada_3c VARCHAR(40),
        desde TIMESTAMP NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS crm_empresas_parceiras (
        id SERIAL PRIMARY KEY,
        nome VARCHAR(80) NOT NULL UNIQUE,
        descricao TEXT,
        produto VARCHAR(120),
        comissao_pct NUMERIC(5,2) NOT NULL DEFAULT 10,
        ativa BOOLEAN NOT NULL DEFAULT TRUE,
        criado_em TIMESTAMP NOT NULL DEFAULT now())""",
    """INSERT INTO crm_empresas_parceiras (nome, descricao, produto, comissao_pct)
       VALUES ('Cyscon Consultoria', 'Revenda TOTVS: ERP e sistemas de gestão para empresas de todos os portes.',
               'ERP TOTVS (Protheus, Datasul, RM, Winthor e outros)', 10)
       ON CONFLICT (nome) DO NOTHING""",
    """CREATE TABLE IF NOT EXISTS crm_convites (
        token_hash CHAR(64) PRIMARY KEY,
        admin BOOLEAN NOT NULL DEFAULT FALSE,
        criado_em TIMESTAMP NOT NULL,
        expira_em TIMESTAMP NOT NULL,
        usado_em TIMESTAMP,
        login VARCHAR(40))""",
    """CREATE TABLE IF NOT EXISTS crm_telefones_extra (
        cnpj VARCHAR(14) NOT NULL,
        telefone VARCHAR(11) NOT NULL,
        rotulo VARCHAR(40),
        quem VARCHAR(60),
        criado_em TIMESTAMP NOT NULL,
        PRIMARY KEY (cnpj, telefone))""",
    """CREATE TABLE IF NOT EXISTS crm_emails_extra (
        cnpj VARCHAR(14) NOT NULL,
        email VARCHAR(120) NOT NULL,
        rotulo VARCHAR(40),
        quem VARCHAR(60),
        criado_em TIMESTAMP NOT NULL,
        PRIMARY KEY (cnpj, email))""",
    """CREATE TABLE IF NOT EXISTS crm_carteira (
        cnpj VARCHAR(14) PRIMARY KEY,
        vendedor_id INTEGER NOT NULL REFERENCES crm_vendedores(id),
        desde TIMESTAMP NOT NULL,
        origem VARCHAR(20) NOT NULL,
        chamada_3c VARCHAR(40),
        motivo VARCHAR(80))""",
    "CREATE INDEX IF NOT EXISTS ix_carteira_vend ON crm_carteira (vendedor_id)",
    """CREATE TABLE IF NOT EXISTS crm_discador_sync (
        id INTEGER PRIMARY KEY,
        ate TIMESTAMP NOT NULL,
        rodou_em TIMESTAMP NOT NULL)""",
    # cadastro aberto (botao "Quero me cadastrar"): fica pendente ate um administrador aprovar
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS email VARCHAR(120)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS celular VARCHAR(11)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS cidade VARCHAR(80)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS pendente BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS termos_versao VARCHAR(20)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS termos_aceite_em TIMESTAMP",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS termos_ip VARCHAR(45)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS documento VARCHAR(14)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_vendedores_documento ON crm_vendedores (documento)",
    # cadastro so de PJ (v2): empresa + representante + perfil comercial + disponibilidade.
    # `nome`, `documento` (CPF), `email` e `celular` passam a ser os do REPRESENTANTE.
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS emp_razao VARCHAR(150)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS emp_fantasia VARCHAR(150)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS emp_cnpj VARCHAR(14)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS emp_cep VARCHAR(8)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS emp_logradouro VARCHAR(150)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS emp_numero VARCHAR(20)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS emp_complemento VARCHAR(100)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS emp_bairro VARCHAR(80)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS emp_cidade VARCHAR(80)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS emp_uf VARCHAR(2)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS emp_email VARCHAR(120)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS emp_telefone VARCHAR(11)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS exp_vendas BOOLEAN",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS exp_vendas_anos SMALLINT",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS exp_totvs BOOLEAN",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS exp_outro_erp BOOLEAN",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS exp_outro_erp_qual VARCHAR(80)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS disp_vezes SMALLINT",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS disp_periodos VARCHAR(60)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_vendedores_emp_cnpj ON crm_vendedores (emp_cnpj)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS perfil_atuacao SMALLINT",
    # contrato personalizado: texto aceito + hash; depois da aprovacao, 7 dias para enviar a versao assinada (gov.br)
    """CREATE TABLE IF NOT EXISTS crm_contratos (
        id SERIAL PRIMARY KEY,
        vendedor_id INTEGER NOT NULL UNIQUE REFERENCES crm_vendedores(id) ON DELETE CASCADE,
        versao VARCHAR(20) NOT NULL,
        texto TEXT NOT NULL,
        hash CHAR(64) NOT NULL,
        aceites TEXT NOT NULL,
        aceito_em TIMESTAMP NOT NULL,
        aceito_ip VARCHAR(45),
        status VARCHAR(12) NOT NULL DEFAULT 'aguardando',
        prazo_ate TIMESTAMP,
        assinado_pdf BYTEA,
        assinado_nome VARCHAR(120),
        enviado_em TIMESTAMP,
        conferido_em TIMESTAMP,
        conferido_por VARCHAR(60),
        obs TEXT)""",
    # dados de pagamento da PJ (preenchidos DEPOIS da aprovacao; entram no contrato)
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS pg_banco VARCHAR(60)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS pg_agencia VARCHAR(10)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS pg_conta VARCHAR(20)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS pg_tipo_conta VARCHAR(20)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS pg_pix_tipo VARCHAR(12)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS pg_pix_chave VARCHAR(100)",
    "ALTER TABLE crm_vendedores ADD COLUMN IF NOT EXISTS pg_atualizado_em TIMESTAMP",
]

PERIODOS = ["08h–10h", "10h–12h", "14h–16h30"]

TERMOS_VERSAO = "2.0"

# documentos que o vendedor le e aceita no cadastro (um aceite por documento); o texto de cada um vive em termos/
DOCS = [("01_contrato", "Contrato de prestação de serviços"), ("02_comissionamento", "Política de Comissionamento"),
        ("03_carteira", "Política de Carteira e Oportunidades"), ("04_privacidade", "Política de Privacidade"),
        ("05_seguranca", "Segurança e Confidencialidade"), ("06_gravacao", "Gravação das ligações"),
        ("07_ia", "Utilização da IA")]


def texto_doc(chave: str) -> str:
    """Texto de um documento dos termos (Markdown). Quebra simples de linha vira quebra visivel."""
    import pathlib
    t = (pathlib.Path(__file__).parent / "termos" / f"{chave}.md").read_text(encoding="utf-8")
    t = t.replace("@@PAGEBREAK", "\n---\n")
    return re.sub(r"(?<!\n)\n(?!\n)", "  \n", t)


def preparar() -> None:
    with dados.engine().begin() as conn:
        for sql in DDL:
            conn.execute(sa.text(sql))


def _q(conn, sql: str, **p) -> list[dict]:
    return dados._q(conn, sql, **p)


# ---------------------------------------------------------------- senha e login

ITERACOES = 240_000


def hash_senha(senha: str) -> str:
    sal = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal, ITERACOES)
    return f"pbkdf2${ITERACOES}${sal.hex()}${h.hex()}"


def confere(senha: str, guardado: str) -> bool:
    try:
        _, it, sal, h = guardado.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal), int(it))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calc.hex(), h)


def _login_ok(login: str) -> str:
    return re.sub(r"[^a-z0-9._@-]", "", (login or "").strip().lower())      # aceita o e-mail como login


def tem_vendedor() -> bool:
    with dados.engine().connect() as conn:
        return bool(conn.execute(sa.text("SELECT 1 FROM crm_vendedores LIMIT 1")).first())


def entrar(login: str, senha: str) -> dict | None:
    """O vendedor (sem o hash) ou None. Mesmo tempo de resposta com login certo ou errado.
    Cadastro ainda nao aprovado, com a senha certa: {"pendente": True}."""
    with dados.engine().begin() as conn:
        r = _q(conn, "SELECT * FROM crm_vendedores WHERE login = :l OR lower(email) = :l "
                     "ORDER BY (login = :l) DESC LIMIT 1", l=_login_ok(login))     # entra pelo login ou pelo e-mail
        if not r:
            hash_senha(senha or "")               # gasta o mesmo tempo: nao entrega quais logins existem
            return None
        if not confere(senha or "", r[0]["senha_hash"]):
            return None
        if r[0]["pendente"]:
            return {"pendente": True}
        if not r[0]["ativo"]:
            return None
        conn.execute(sa.text("UPDATE crm_vendedores SET ultimo_acesso = :a WHERE id = :i"),
                     {"a": dados.agora(), "i": r[0]["id"]})
    return {k: v for k, v in r[0].items() if k != "senha_hash"}


def por_id(vendedor_id: int) -> dict | None:
    """O vendedor ativo (sem o hash), para reabrir a sessao guardada na aba."""
    with dados.engine().connect() as conn:
        r = _q(conn, "SELECT * FROM crm_vendedores WHERE id = :i AND ativo", i=vendedor_id)
    return {k: v for k, v in r[0].items() if k != "senha_hash"} if r else None


def criar(nome: str, login: str, senha: str, admin: bool = False) -> dict:
    login = _login_ok(login)
    if not nome.strip() or not login:
        return {"erro": "Preencha o nome e o login (letras, números, ponto ou traço)."}
    if len(senha or "") < 8:
        return {"erro": "A senha precisa ter pelo menos 8 caracteres."}
    try:
        with dados.engine().begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO crm_vendedores (nome, login, senha_hash, admin, ativo, criado_em) "
                "VALUES (:n, :l, :s, :a, TRUE, :c)"),
                {"n": nome.strip()[:60], "l": login, "s": hash_senha(senha), "a": admin, "c": dados.agora()})
    except sa.exc.IntegrityError:
        return {"erro": f"Já existe alguém com o login '{login}'."}
    return {"ok": True, "mensagem": f"{nome.strip()} criado (login: {login})."}


def trocar_senha(vendedor_id: int, senha: str) -> dict:
    if len(senha or "") < 8:
        return {"erro": "A senha precisa ter pelo menos 8 caracteres."}
    with dados.engine().begin() as conn:
        conn.execute(sa.text("UPDATE crm_vendedores SET senha_hash = :s WHERE id = :i"),
                     {"s": hash_senha(senha), "i": vendedor_id})
    return {"ok": True, "mensagem": "Senha trocada."}


def documento_ok(doc: str) -> str:
    """CPF (11) ou CNPJ (14) so com digitos, se os digitos verificadores baterem; senao ""."""
    d = re.sub(r"\D", "", doc or "")
    if len(d) not in (11, 14) or len(set(d)) == 1:
        return ""
    if len(d) == 11:
        for n in (9, 10):
            soma = sum(int(d[i]) * (n + 1 - i) for i in range(n))
            if (soma * 10 % 11) % 10 != int(d[n]):
                return ""
        return d
    for n in (12, 13):
        pesos = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2] if n == 12 else [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        resto = sum(int(d[i]) * pesos[i] for i in range(n)) % 11
        if (0 if resto < 2 else 11 - resto) != int(d[n]):
            return ""
    return d


def consulta_cnpj(cnpj: str) -> dict:
    """Dados publicos do CNPJ (opencnpj) para preencher o cadastro: {"erro"} ou os campos da empresa.
    Se o servico estiver fora do ar devolve {"indisponivel": True}: o cadastro segue e o gestor confere."""
    import requests
    c = documento_ok(cnpj)
    if len(c) != 14:
        return {"erro": "CNPJ inválido. Confira os números."}
    try:
        r = requests.get(f"https://api.opencnpj.org/{c}", timeout=10)
    except requests.RequestException:
        return {"indisponivel": True}
    if r.status_code == 404:
        return {"erro": "CNPJ não encontrado na Receita Federal."}
    if r.status_code != 200:
        return {"indisponivel": True}
    j = r.json()
    tel = (j.get("telefones") or [{}])[0]
    tipo, log = (j.get("tipo_logradouro") or "").strip(), (j.get("logradouro") or "").strip()
    rua = log if (not tipo or log.upper().startswith(tipo.upper())) else f"{tipo} {log}"
    return {"ok": True, "cnpj": c, "situacao": j.get("situacao_cadastral") or "",
            "razao": j.get("razao_social") or "", "fantasia": j.get("nome_fantasia") or "",
            "cep": re.sub(r"\D", "", j.get("cep") or ""), "logradouro": rua.title(),
            "numero": j.get("numero") or "", "complemento": " ".join((j.get("complemento") or "").split()).title(),
            "bairro": (j.get("bairro") or "").title(), "cidade": (j.get("municipio") or "").title(),
            "uf": j.get("uf") or "", "email": (j.get("email") or "").lower(),
            "telefone": f"{tel.get('ddd') or ''}{tel.get('numero') or ''}"}


def consulta_cep(cep: str) -> dict:
    """Endereco pelo CEP (ViaCEP), para quando a Receita nao traz o endereco."""
    import requests
    c = re.sub(r"\D", "", cep or "")
    if len(c) != 8:
        return {"erro": "CEP inválido."}
    try:
        j = requests.get(f"https://viacep.com.br/ws/{c}/json/", timeout=8).json()
    except (requests.RequestException, ValueError):
        return {"indisponivel": True}
    if j.get("erro"):
        return {"erro": "CEP não encontrado."}
    return {"ok": True, "logradouro": j.get("logradouro") or "", "bairro": j.get("bairro") or "",
            "cidade": j.get("localidade") or "", "uf": j.get("uf") or ""}


def _fone(txt: str) -> str:
    f = re.sub(r"\D", "", txt or "")
    return f[2:] if f.startswith("55") and len(f) > 11 else f


def _email_ok(e: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e))


def validar_cadastro(f: dict) -> dict:
    """Confere o formulario de cadastro (so PJ) antes de mandar o codigo por e-mail.
    Devolve {"erro"} ou {"ok": True, ...dados limpos}. O e-mail do representante e o que recebe o codigo."""
    def g(k):
        return (f.get(k) or "").strip()
    emp_cnpj, cpf = documento_ok(g("emp_cnpj")), documento_ok(g("cpf"))
    email, emp_email = g("email").lower(), g("emp_email").lower()
    celular, emp_tel = _fone(g("celular")), _fone(g("emp_telefone"))
    cep = re.sub(r"\D", "", g("emp_cep"))
    uf = g("emp_uf").upper()
    login = _login_ok(g("login"))
    if len(emp_cnpj) != 14:
        return {"erro": "CNPJ da empresa inválido. Confira os números."}
    if not g("emp_razao"):
        return {"erro": "Informe a razão social."}
    if len(cep) != 8 or not g("emp_logradouro") or not g("emp_numero") or not g("emp_bairro"):
        return {"erro": "Complete o endereço da empresa: CEP, logradouro, número e bairro."}
    if not g("emp_cidade") or len(uf) != 2:
        return {"erro": "Informe a cidade e o estado (UF) da empresa."}
    if not _email_ok(emp_email):
        return {"erro": "Informe um e-mail válido para a empresa."}
    if len(emp_tel) not in (10, 11):
        return {"erro": "Informe o telefone da empresa com DDD."}
    if len(g("nome").split()) < 2:
        return {"erro": "Informe o nome completo do representante."}
    if len(cpf) != 11:
        return {"erro": "CPF do representante inválido. Confira os números."}
    if not _email_ok(email):
        return {"erro": "Informe um e-mail válido para o representante."}
    if len(celular) not in (10, 11):
        return {"erro": "Informe o celular do representante com DDD (ex.: 71 99999-8888)."}
    if f.get("exp_vendas") is None or f.get("exp_totvs") is None or f.get("exp_outro_erp") is None:
        return {"erro": "Responda as três perguntas do perfil comercial (Sim ou Não)."}
    anos = f.get("exp_vendas_anos")
    if f["exp_vendas"] and not (isinstance(anos, int) and 1 <= anos <= 60):
        return {"erro": "Informe há quantos anos você tem experiência com vendas."}
    if f["exp_outro_erp"] and not g("exp_outro_erp_qual"):
        return {"erro": "Informe qual outro ERP você conhece."}
    if f.get("perfil") not in (1, 2):
        return {"erro": "Escolha o perfil de atuação (Perfil 1 ou Perfil 2)."}
    vezes = f.get("disp_vezes")
    periodos = [p for p in (f.get("disp_periodos") or []) if p in PERIODOS]
    if vezes not in (1, 2, 3, 4, 5):
        return {"erro": "Escolha quantas vezes por semana você pode usar a plataforma."}
    if not periodos:
        return {"erro": "Escolha pelo menos um período de disponibilidade."}
    if not login:
        return {"erro": "Escolha um login (letras, números, ponto ou traço)."}
    if len(f.get("senha") or "") < 8:
        return {"erro": "A senha precisa ter pelo menos 8 caracteres."}
    sit = consulta_cnpj(emp_cnpj)                   # a Receita fora do ar nao trava o cadastro
    if sit.get("ok") and not sit["situacao"].lower().startswith("ativa"):
        return {"erro": f"O CNPJ consta como {sit['situacao'] or 'não ativo'} na Receita Federal."}
    with dados.engine().connect() as conn:
        for col, val, msg in (("emp_cnpj", emp_cnpj, "Já existe um cadastro com esse CNPJ."),
                              ("documento", cpf, "Já existe um cadastro com esse CPF."),
                              ("lower(email)", email, "Já existe um cadastro com esse e-mail."),
                              ("login", login, f"Já existe alguém com o login '{login}'. Escolha outro.")):
            if conn.execute(sa.text(f"SELECT 1 FROM crm_vendedores WHERE {col} = :v"), {"v": val}).first():
                return {"erro": msg}
    return {"ok": True, "nome": g("nome"), "documento": cpf, "email": email, "celular": celular, "login": login,
            "senha": f["senha"], "cidade": f"{g('emp_cidade')}/{uf}",
            "emp_razao": g("emp_razao"), "emp_fantasia": g("emp_fantasia") or None, "emp_cnpj": emp_cnpj,
            "emp_cep": cep, "emp_logradouro": g("emp_logradouro"), "emp_numero": g("emp_numero"),
            "emp_complemento": g("emp_complemento") or None, "emp_bairro": g("emp_bairro"),
            "emp_cidade": g("emp_cidade"), "emp_uf": uf, "emp_email": emp_email, "emp_telefone": emp_tel,
            "exp_vendas": bool(f["exp_vendas"]), "exp_vendas_anos": anos if f["exp_vendas"] else None,
            "exp_totvs": bool(f["exp_totvs"]), "exp_outro_erp": bool(f["exp_outro_erp"]),
            "exp_outro_erp_qual": g("exp_outro_erp_qual") if f["exp_outro_erp"] else None,
            "disp_vezes": vezes, "disp_periodos": ",".join(periodos), "perfil_atuacao": f["perfil"]}


_CAMPOS_PJ = ("emp_razao", "emp_fantasia", "emp_cnpj", "emp_cep", "emp_logradouro", "emp_numero", "emp_complemento",
              "emp_bairro", "emp_cidade", "emp_uf", "emp_email", "emp_telefone", "exp_vendas", "exp_vendas_anos",
              "exp_totvs", "exp_outro_erp", "exp_outro_erp_qual", "disp_vezes", "disp_periodos", "perfil_atuacao")


def cadastrar(d: dict, ip: str | None) -> dict:
    """Cadastro aberto (depois de confirmar o e-mail): cria o vendedor PENDENTE (nao entra) ate um
    administrador aprovar. Guarda o aceite dos termos (versao, data e IP), como pede a clausula 8.2."""
    texto, hash_, aceites = d["contrato_texto"], d["contrato_hash"], d["aceites"]
    d = validar_cadastro(d["form"])
    if d.get("erro"):
        return d
    r = criar(d["nome"], d["login"], d["senha"])
    if r.get("erro"):
        return r
    sets = ", ".join(f"{c} = :{c}" for c in _CAMPOS_PJ)
    with dados.engine().begin() as conn:
        conn.execute(sa.text(
            f"UPDATE crm_vendedores SET ativo = FALSE, pendente = TRUE, documento = :doc, email = :e, celular = :c, "
            f"cidade = :ci, termos_versao = :tv, termos_aceite_em = :ta, termos_ip = :ip, {sets} WHERE login = :l"),
            {"doc": d["documento"], "e": d["email"], "c": d["celular"], "ci": d["cidade"][:80], "tv": TERMOS_VERSAO,
             "ta": dados.agora(), "ip": (ip or "")[:45] or None, "l": d["login"], **{c: d[c] for c in _CAMPOS_PJ}})
        vid = conn.execute(sa.text("SELECT id FROM crm_vendedores WHERE login = :l"), {"l": d["login"]}).scalar()
        conn.execute(sa.text(
            "INSERT INTO crm_contratos (vendedor_id, versao, texto, hash, aceites, aceito_em, aceito_ip) "
            "VALUES (:v, :ve, :t, :h, :a, :q, :ip)"),
            {"v": vid, "ve": TERMOS_VERSAO, "t": texto, "h": hash_, "a": aceites, "q": dados.agora(),
             "ip": (ip or "")[:45] or None})
    return {"ok": True}


def pendentes() -> list[dict]:
    with dados.engine().connect() as conn:
        return _q(conn, "SELECT id, nome, login, documento, email, celular, cidade, criado_em, termos_versao, "
                        "termos_aceite_em, " + ", ".join(_CAMPOS_PJ) + " FROM crm_vendedores WHERE pendente "
                        "ORDER BY criado_em")


BANCOS = ["001 – Banco do Brasil", "033 – Santander", "104 – Caixa Econômica", "237 – Bradesco", "341 – Itaú",
          "260 – Nubank", "077 – Inter", "336 – C6 Bank", "290 – PagBank", "380 – PicPay", "748 – Sicredi",
          "756 – Sicoob", "422 – Safra", "070 – BRB", "Outro"]
TIPOS_CONTA = ["Conta corrente", "Conta de pagamento", "Poupança"]
TIPOS_PIX = ["CNPJ", "E-mail", "Celular", "Chave aleatória"]


def precisa_pagamento(u: dict) -> bool:
    """Vendedor PJ aprovado que ainda nao informou onde receber a comissao (o contrato depende disso)."""
    return bool(u.get("emp_cnpj")) and not u.get("admin") and not u.get("pg_atualizado_em")


def validar_pagamento(f: dict, emp_cnpj: str) -> dict:
    """Confere os dados de pagamento. A conta e a chave PIX tem de ser DA EMPRESA (mesmo CNPJ do cadastro).
    Vale conta completa OU chave PIX; pode mandar as duas."""
    g = lambda k: (f.get(k) or "").strip()
    agencia, conta = re.sub(r"\D", "", g("agencia")), re.sub(r"[^0-9xX-]", "", g("conta"))
    banco, tipo_conta = g("banco"), g("tipo_conta")
    pix_tipo, pix = g("pix_tipo"), g("pix_chave")
    tem_conta = any((agencia, conta, banco))
    if not (tem_conta or pix):
        return {"erro": "Informe a conta bancária ou a chave PIX da empresa."}
    if tem_conta:
        if banco not in BANCOS or tipo_conta not in TIPOS_CONTA or not agencia or len(conta) < 3:
            return {"erro": "Complete a conta: banco, tipo, agência e número com dígito."}
    if pix:
        if pix_tipo == "CNPJ":
            if re.sub(r"\D", "", pix) != emp_cnpj:
                return {"erro": "A chave PIX do tipo CNPJ precisa ser o CNPJ da sua empresa."}
            pix = emp_cnpj
        elif pix_tipo == "E-mail":
            pix = pix.lower()
            if not _email_ok(pix):
                return {"erro": "Chave PIX (e-mail) inválida."}
        elif pix_tipo == "Celular":
            pix = _fone(pix)
            if len(pix) != 11:
                return {"erro": "Chave PIX (celular) inválida. Use DDD + número."}
        elif pix_tipo == "Chave aleatória":
            if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", pix):
                return {"erro": "Chave aleatória inválida (formato 8-4-4-4-12)."}
            pix = pix.lower()
        else:
            return {"erro": "Escolha o tipo da chave PIX."}
    return {"ok": True, "pg_banco": banco if tem_conta else None, "pg_agencia": agencia or None,
            "pg_conta": conta or None, "pg_tipo_conta": tipo_conta if tem_conta else None,
            "pg_pix_tipo": pix_tipo if pix else None, "pg_pix_chave": pix or None}


def salvar_pagamento(u: dict, f: dict) -> dict:
    """Grava os dados de pagamento do proprio vendedor (so uma vez: depois, so o gestor libera nova edicao)."""
    v = por_id(u["id"])
    if not v or not v.get("emp_cnpj"):
        return {"erro": "Cadastro de empresa não encontrado."}
    if v.get("pg_atualizado_em"):
        return {"erro": "Seus dados de pagamento já foram enviados. Para alterar, fale com o gestor."}
    d = validar_pagamento(f, v["emp_cnpj"])
    if d.get("erro"):
        return d
    cols = ("pg_banco", "pg_agencia", "pg_conta", "pg_tipo_conta", "pg_pix_tipo", "pg_pix_chave")
    with dados.engine().begin() as conn:
        conn.execute(sa.text("UPDATE crm_vendedores SET " + ", ".join(f"{c} = :{c}" for c in cols) +
                             ", pg_atualizado_em = :q WHERE id = :i"),
                     {**{c: d[c] for c in cols}, "q": dados.agora(), "i": u["id"]})
    return {"ok": True}


def pagamento(vendedor_id: int) -> dict:
    """Empresa + dados de pagamento, para o gestor montar o contrato."""
    with dados.engine().connect() as conn:
        r = _q(conn, "SELECT emp_razao, emp_cnpj, pg_banco, pg_agencia, pg_conta, pg_tipo_conta, pg_pix_tipo, "
                     "pg_pix_chave, pg_atualizado_em FROM crm_vendedores WHERE id = :i", i=vendedor_id)
    return r[0] if r else {}


def liberar_pagamento(vendedor_id: int) -> dict:
    """O gestor pede novo preenchimento (a pessoa volta a ver a tela de pagamento no proximo acesso)."""
    with dados.engine().begin() as conn:
        conn.execute(sa.text("UPDATE crm_vendedores SET pg_atualizado_em = NULL WHERE id = :i"), {"i": vendedor_id})
    return {"ok": True, "mensagem": "Pagamento liberado: a pessoa preenche de novo no próximo acesso."}


PRAZO_CONTRATO_DIAS = 7
PDF_MAX_BYTES = 5 * 1024 * 1024


def contrato_do(vendedor_id: int) -> dict | None:
    """Contrato aceito no cadastro (sem o arquivo assinado) + situacao: 'ok', 'prazo' (pendente, dentro dos 7 dias)
    ou 'bloqueado' (pendente e vencido). Quem nao tem contrato (cadastro antigo) nao tem pendencia."""
    with dados.engine().connect() as conn:
        r = _q(conn, "SELECT id, vendedor_id, versao, hash, aceito_em, status, prazo_ate, assinado_nome, enviado_em, "
                     "conferido_em, conferido_por, obs FROM crm_contratos WHERE vendedor_id = :i", i=vendedor_id)
    if not r:
        return None
    c = r[0]
    if c["status"] != "aguardando" or not c["prazo_ate"]:
        c["situacao"], c["dias"] = "ok", None
    else:
        resta = c["prazo_ate"] - dados.agora()
        c["dias"] = max(0, -(-resta.total_seconds() // 86400))
        c["situacao"] = "prazo" if resta.total_seconds() > 0 else "bloqueado"
    return c


def contrato_texto(vendedor_id: int) -> str:
    with dados.engine().connect() as conn:
        r = conn.execute(sa.text("SELECT texto FROM crm_contratos WHERE vendedor_id = :i"), {"i": vendedor_id}).scalar()
    return r or ""


def contrato_assinado_arquivo(vendedor_id: int) -> tuple[str, bytes] | None:
    with dados.engine().connect() as conn:
        r = conn.execute(sa.text("SELECT assinado_nome, assinado_pdf FROM crm_contratos WHERE vendedor_id = :i"),
                         {"i": vendedor_id}).first()
    return (r[0], bytes(r[1])) if r and r[1] else None


def enviar_contrato_assinado(vendedor_id: int, nome_arquivo: str, conteudo: bytes) -> dict:
    """O vendedor sobe o PDF assinado no gov.br. Regulariza a pendencia; o gestor confere depois."""
    if not conteudo.startswith(b"%PDF"):
        return {"erro": "Envie o contrato em PDF (o arquivo assinado pelo gov.br)."}
    if len(conteudo) > PDF_MAX_BYTES:
        return {"erro": "O arquivo passa de 5 MB."}
    with dados.engine().begin() as conn:
        n = conn.execute(sa.text(
            "UPDATE crm_contratos SET assinado_pdf = :b, assinado_nome = :n, enviado_em = :q, status = 'enviado', "
            "obs = NULL WHERE vendedor_id = :i AND status IN ('aguardando', 'recusado')"),
            {"b": conteudo, "n": (nome_arquivo or "contrato.pdf")[:120], "q": dados.agora(), "i": vendedor_id}).rowcount
    if not n:
        return {"erro": "Este contrato já foi enviado ou conferido."}
    return {"ok": True, "mensagem": "Contrato assinado enviado. O gestor vai conferir a assinatura."}


def conferir_contrato(vendedor_id: int, por: str, aceito: bool, obs: str = "") -> dict:
    """O gestor confere a assinatura (validador do ITI). Recusado: volta a pendente com mais 7 dias."""
    with dados.engine().begin() as conn:
        if aceito:
            n = conn.execute(sa.text("UPDATE crm_contratos SET status = 'conferido', conferido_em = :q, conferido_por = :p, "
                                     "obs = NULL, prazo_ate = NULL WHERE vendedor_id = :i AND status = 'enviado'"),
                             {"q": dados.agora(), "p": por, "i": vendedor_id}).rowcount
        else:
            n = conn.execute(sa.text("UPDATE crm_contratos SET status = 'aguardando', obs = :o, prazo_ate = :pz, "
                                     "assinado_pdf = NULL, assinado_nome = NULL, enviado_em = NULL "
                                     "WHERE vendedor_id = :i AND status = 'enviado'"),
                             {"o": (obs or "")[:300] or "Assinatura não validada", "i": vendedor_id,
                              "pz": dados.agora() + dt.timedelta(days=PRAZO_CONTRATO_DIAS)}).rowcount
    if not n:
        return {"erro": "Não há contrato enviado aguardando conferência."}
    return {"ok": True, "mensagem": "Contrato conferido." if aceito else "Contrato devolvido: novo prazo de 7 dias."}


def prorrogar_contrato(vendedor_id: int, dias: int = PRAZO_CONTRATO_DIAS) -> dict:
    with dados.engine().begin() as conn:
        conn.execute(sa.text("UPDATE crm_contratos SET prazo_ate = :p WHERE vendedor_id = :i AND status = 'aguardando'"),
                     {"p": dados.agora() + dt.timedelta(days=dias), "i": vendedor_id})
    return {"ok": True, "mensagem": f"Prazo prorrogado: mais {dias} dias a partir de hoje."}


def aprovar(vendedor_id: int, aprovado: bool) -> dict:
    """Aprova (passa a entrar como vendedor) ou recusa (apaga o cadastro pendente)."""
    with dados.engine().begin() as conn:
        if aprovado:
            n = conn.execute(sa.text("UPDATE crm_vendedores SET ativo = TRUE, pendente = FALSE "
                                     "WHERE id = :i AND pendente"), {"i": vendedor_id}).rowcount
            if n:
                conn.execute(sa.text("UPDATE crm_contratos SET prazo_ate = :p WHERE vendedor_id = :i AND prazo_ate IS NULL"),
                             {"p": dados.agora() + dt.timedelta(days=PRAZO_CONTRATO_DIAS), "i": vendedor_id})
        else:
            n = conn.execute(sa.text("DELETE FROM crm_vendedores WHERE id = :i AND pendente"),
                             {"i": vendedor_id}).rowcount
    if not n:
        return {"erro": "Esse cadastro já foi analisado."}
    return {"ok": True, "mensagem": "Cadastro aprovado: já pode entrar." if aprovado else "Cadastro recusado."}


def ativar(vendedor_id: int, ativo: bool) -> dict:
    """Desativar = o vendedor saiu: todos os clientes dele voltam para o pool de contas (sem dono)."""
    n = 0
    with dados.engine().begin() as conn:
        conn.execute(sa.text("UPDATE crm_vendedores SET ativo = :a WHERE id = :i"), {"a": ativo, "i": vendedor_id})
        if not ativo:
            nome = conn.execute(sa.text("SELECT nome FROM crm_vendedores WHERE id = :i"), {"i": vendedor_id}).scalar() or ""
            cnpjs = [r[0] for r in conn.execute(sa.text("SELECT cnpj FROM crm_carteira WHERE vendedor_id = :i"),
                                                {"i": vendedor_id})]
            for cnpj in cnpjs:
                _historico(conn, cnpj, "nota", "Sistema", f"Carteira: {nome} → pool de contas (vendedor desativado).")
            conn.execute(sa.text("DELETE FROM crm_carteira WHERE vendedor_id = :i"), {"i": vendedor_id})
            conn.execute(sa.text("DELETE FROM crm_servicos_cards WHERE vendedor_id = :i"), {"i": vendedor_id})
            if cnpjs:
                conn.execute(sa.text("UPDATE empresas SET responsavel = NULL WHERE cnpj = ANY(:l)"), {"l": cnpjs})
            n = len(cnpjs)
    if ativo:
        return {"ok": True, "mensagem": "Vendedor reativado."}
    return {"ok": True, "mensagem": f"Vendedor desativado: {n} cliente(s) voltaram para o pool de contas."}


def empresas_parceiras() -> list[dict]:
    """As empresas para quem o vendedor pode trabalhar (a escolha logo depois do login)."""
    with dados.engine().connect() as conn:
        return _q(conn, "SELECT * FROM crm_empresas_parceiras WHERE ativa ORDER BY nome")


def criar_convite(admin: bool = False, horas: int = 48) -> str:
    """Link de convite de uso unico: quem abre cria o proprio login e senha. So o hash fica no banco."""
    token = secrets.token_urlsafe(24)
    with dados.engine().begin() as conn:
        conn.execute(sa.text("INSERT INTO crm_convites (token_hash, admin, criado_em, expira_em) VALUES (:h, :a, :c, :e)"),
                     {"h": hashlib.sha256(token.encode()).hexdigest(), "a": admin, "c": dados.agora(),
                      "e": dados.agora() + dt.timedelta(hours=horas)})
    return token


def convite(token: str) -> dict | None:
    """O convite valido (nao usado, nao vencido) desse token, ou None."""
    if not token:
        return None
    with dados.engine().connect() as conn:
        r = _q(conn, "SELECT * FROM crm_convites WHERE token_hash = :h AND usado_em IS NULL AND expira_em > :a",
               h=hashlib.sha256(token.encode()).hexdigest(), a=dados.agora())
    return r[0] if r else None


def usar_convite(token: str, nome: str, login: str, senha: str) -> dict:
    c = convite(token)
    if not c:
        return {"erro": "Convite inválido, vencido ou já usado."}
    r = criar(nome, login, senha, admin=c["admin"])
    if r.get("erro"):
        return r
    with dados.engine().begin() as conn:
        conn.execute(sa.text("UPDATE crm_convites SET usado_em = :a, login = :l WHERE token_hash = :h"),
                     {"a": dados.agora(), "l": _login_ok(login), "h": c["token_hash"]})
    return r


def definir_admin(vendedor_id: int, admin: bool) -> dict:
    """Muda o perfil (vendedor <-> administrador). Nunca deixa o CRM sem nenhum administrador ativo."""
    with dados.engine().begin() as conn:
        if not admin:
            outros = conn.execute(sa.text("SELECT count(*) FROM crm_vendedores WHERE admin AND ativo AND id <> :i"),
                                  {"i": vendedor_id}).scalar()
            if not outros:
                return {"erro": "Crie outro administrador antes: o CRM precisa de pelo menos um."}
        conn.execute(sa.text("UPDATE crm_vendedores SET admin = :a WHERE id = :i"), {"a": admin, "i": vendedor_id})
    return {"ok": True, "mensagem": "Agora é administrador." if admin else "Agora é vendedor."}


def visao_geral(dias: int = 7) -> dict:
    """Numeros do CRM inteiro para a pagina do administrador."""
    agora = dados.agora()
    desde = agora - dt.timedelta(days=dias)
    hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    with dados.engine().connect() as conn:
        vend = _q(conn, """
            SELECT v.id, v.nome, v.login, v.admin, v.ativo, v.ultimo_acesso,
              (SELECT count(*) FROM crm_carteira c WHERE c.vendedor_id = v.id) AS carteira,
              (SELECT count(*) FROM crm_carteira c JOIN empresas e ON e.cnpj = c.cnpj
                WHERE c.vendedor_id = v.id AND e.proximo_contato IS NOT NULL AND e.proximo_contato <= :agora_txt) AS atrasados,
              (SELECT count(*) FROM crm_discador_chamadas d WHERE d.vendedor_id = v.id AND d.quando >= :desde) AS chamadas,
              (SELECT count(*) FROM crm_discador_chamadas d WHERE d.vendedor_id = v.id AND d.quando >= :desde
                AND d.entrou_carteira) AS novos,
              (SELECT count(*) FROM crm_mv_ligacoes l WHERE l.quem = v.nome AND l.pedido_em >= :desde) AS manuais,
              (SELECT coalesce(sum(extract(epoch FROM coalesce(t.fim, :agora) - t.inicio)), 0) / 3600.0
                 FROM crm_discador_turnos t WHERE t.vendedor_id = v.id AND t.inicio >= :desde) AS horas
            FROM crm_vendedores v WHERE NOT v.pendente ORDER BY v.ativo DESC, v.nome""",
                  desde=desde, agora=agora, agora_txt=agora.isoformat(sep=" ", timespec="minutes"))
        por_dia = _q(conn, """
            SELECT date(quando) AS dia, count(*) AS chamadas, count(*) FILTER (WHERE entrou_carteira) AS carteira
            FROM crm_discador_chamadas WHERE quando >= :desde GROUP BY 1 ORDER BY 1""", desde=desde)
        manuais = _q(conn, """
            SELECT count(*) AS total, count(*) FILTER (WHERE duracao IS NOT NULL AND duracao <> '00:00:00') AS atendidas
            FROM crm_mv_ligacoes WHERE pedido_em >= :desde""", desde=desde)[0]
        hoje_n = _q(conn, "SELECT count(*) AS n FROM crm_discador_chamadas WHERE quando >= :h", h=hoje)[0]["n"]
    return {"vendedores": vend, "por_dia": por_dia, "manuais": manuais, "chamadas_hoje": hoje_n,
            "turno": turno_aberto(), "dias": dias}


def lista() -> list[dict]:
    with dados.engine().connect() as conn:
        return _q(conn, "SELECT v.id, v.nome, v.login, v.admin, v.ativo, v.ultimo_acesso, "
                        "(SELECT count(*) FROM crm_carteira c WHERE c.vendedor_id = v.id) AS clientes, "
                        "v.emp_cnpj, v.pg_atualizado_em, (SELECT k.status FROM crm_contratos k WHERE k.vendedor_id = v.id) AS contrato_status, "
                        "(SELECT k.prazo_ate FROM crm_contratos k WHERE k.vendedor_id = v.id) AS contrato_prazo "
                        "FROM crm_vendedores v WHERE NOT v.pendente ORDER BY v.ativo DESC, v.nome")


# ---------------------------------------------------------------- turno no discador

# Turnos de discagem (horario de Brasilia). O vendedor entra a qualquer hora DENTRO do turno e fica ate o fim
# dele; no fim o turno fecha sozinho. Fora desses horarios so o administrador usa o discador.
TURNOS = [(1, dt.time(8, 0), dt.time(10, 0)),
          (2, dt.time(10, 0), dt.time(12, 0)),
          (3, dt.time(14, 0), dt.time(16, 0))]
RESERVA_MIN = 10          # o 1o da fila de preferencia tem este tempo, a partir do inicio do turno, so para ele


def linhas_turnos() -> list[str]:
    return [f"Turno {n}: {i:%H:%M} às {f:%H:%M}" for n, i, f in TURNOS]


def turno_de(momento: dt.datetime) -> tuple[int, dt.datetime, dt.datetime] | None:
    """O turno de discagem que contem `momento`: (numero, inicio, fim), ou None se esta fora de todos."""
    for n, i, f in TURNOS:
        ini, fim = dt.datetime.combine(momento.date(), i), dt.datetime.combine(momento.date(), f)
        if ini <= momento < fim:
            return n, ini, fim
    return None


def proximo_turno(momento: dt.datetime) -> tuple[int, dt.datetime, dt.datetime]:
    """O proximo turno a comecar depois de `momento` (no fim do dia, o 1o de amanha)."""
    for dia in (momento.date(), momento.date() + dt.timedelta(days=1)):
        for n, i, f in TURNOS:
            ini = dt.datetime.combine(dia, i)
            if ini > momento:
                return n, ini, dt.datetime.combine(dia, f)
    raise RuntimeError("sem turnos")


def proximos_turnos(momento: dt.datetime, k: int = 3) -> list[tuple[int, dt.datetime, dt.datetime]]:
    """Os `k` proximos turnos que comecam depois de `momento`, na ordem (cruzando para o dia seguinte)."""
    saida, dia = [], momento.date()
    while len(saida) < k:
        for n, i, f in TURNOS:
            ini = dt.datetime.combine(dia, i)
            if ini > momento and len(saida) < k:
                saida.append((n, ini, dt.datetime.combine(dia, f)))
        dia += dt.timedelta(days=1)
    return saida


def opcoes_reserva(momento: dt.datetime) -> list[tuple[int, dt.datetime, dt.datetime]]:
    """Turnos que quem achou o ramal ocupado pode reservar: os que restam HOJE (no turno 1: 2 e 3; no turno 2: so
    o 3); no ultimo turno do dia, os 3 turnos de amanha."""
    hoje = [t for t in proximos_turnos(momento, len(TURNOS)) if t[1].date() == momento.date()]
    return hoje or proximos_turnos(momento, len(TURNOS))


def _fila(conn, dia, turno: int) -> list[int]:
    return [r["vendedor_id"] for r in _q(conn, "SELECT vendedor_id FROM crm_turno_fila WHERE data = :d AND turno = :t "
                                               "ORDER BY criado_em, id", d=dia, t=turno)]


def entrar_fila(vendedor_id: int, turno: tuple, origem: str, conn=None) -> int:
    """Garante a preferencia do vendedor no turno `turno` (numero, inicio, fim). Devolve a posicao na fila (1 = 1o)."""
    def _fazer(c) -> int:
        c.execute(sa.text("INSERT INTO crm_turno_fila (data, turno, vendedor_id, origem, criado_em) "
                          "VALUES (:d, :t, :v, :o, :a) ON CONFLICT (data, turno, vendedor_id) DO NOTHING"),
                  {"d": turno[1].date(), "t": turno[0], "v": vendedor_id, "o": origem, "a": dados.agora()})
        return _fila(c, turno[1].date(), turno[0]).index(vendedor_id) + 1
    if conn is not None:
        return _fazer(conn)
    with dados.engine().begin() as c:
        return _fazer(c)


def turno_encerrado(turno_id: int) -> dict | None:
    """O turno de `turno_id`, se ja fechou: com o numero do turno de discagem e como fechou."""
    with dados.engine().connect() as conn:
        r = _q(conn, "SELECT id, inicio, fim, fechado_por FROM crm_discador_turnos WHERE id = :i AND fim IS NOT NULL",
               i=turno_id)
    if not r:
        return None
    t = turno_de(r[0]["inicio"])
    return r[0] | {"numero": t[0] if t else None}


def sinal_turno(vendedor_id: int) -> None:
    """Batimento: a pagina do dono do turno avisa que continua aberta (a cada ~60 s)."""
    with dados.engine().begin() as conn:
        conn.execute(sa.text("UPDATE crm_discador_turnos SET ultimo_sinal = :a "
                             "WHERE fim IS NULL AND vendedor_id = :v"), {"a": dados.agora(), "v": vendedor_id})


def _fechar_esquecidos(conn) -> None:
    # aba fechada sem encerrar: sem sinal ha INATIVO_MIN minutos, o turno acaba no ultimo sinal
    conn.execute(sa.text("UPDATE crm_discador_turnos SET fim = COALESCE(ultimo_sinal, inicio), "
                         "fechado_por = 'inatividade' WHERE fim IS NULL AND COALESCE(ultimo_sinal, inicio) < :lim"),
                 {"lim": dados.agora() - dt.timedelta(minutes=INATIVO_MIN)})
    # fim do turno de discagem: quem abriu dentro de um turno sai quando ele acaba
    agora = dados.agora()
    for r in _q(conn, "SELECT id, inicio FROM crm_discador_turnos WHERE fim IS NULL"):
        t = turno_de(r["inicio"])
        if t and agora >= t[2]:
            conn.execute(sa.text("UPDATE crm_discador_turnos SET fim = :f, fechado_por = 'fim do turno' "
                                 "WHERE id = :i AND fim IS NULL"), {"f": t[2], "i": r["id"]})
    conn.execute(sa.text("UPDATE crm_discador_turnos SET fim = inicio + make_interval(hours => :h), "
                         "fechado_por = 'automatico' WHERE fim IS NULL AND inicio < :lim"),
                 {"h": TURNO_MAX_H, "lim": dados.agora() - dt.timedelta(hours=TURNO_MAX_H)})


def turno_aberto() -> dict | None:
    with dados.engine().begin() as conn:
        _fechar_esquecidos(conn)
        r = _q(conn, "SELECT t.*, v.nome FROM crm_discador_turnos t JOIN crm_vendedores v ON v.id = t.vendedor_id "
                     "WHERE t.fim IS NULL ORDER BY t.id DESC LIMIT 1")
    return r[0] if r else None


def iniciar_turno(vend: dict) -> dict:
    with dados.engine().begin() as conn:
        conn.execute(sa.text("LOCK TABLE crm_discador_turnos IN EXCLUSIVE MODE"))
        _fechar_esquecidos(conn)
        r = _q(conn, "SELECT t.*, v.nome FROM crm_discador_turnos t JOIN crm_vendedores v ON v.id = t.vendedor_id "
                     "WHERE t.fim IS NULL LIMIT 1")
        if r:
            if r[0]["vendedor_id"] == vend["id"]:
                return {"ok": True, "mensagem": "Seu turno já está aberto."}
            quem = (f"{r[0]['nome']} desde {r[0]['inicio']:%H:%M}" if vend.get("admin")
                    else "outro usuário")                      # so o gestor ve quem esta ligado
            # quem tentou e achou o ramal ocupado tem preferencia no proximo turno: a tela pergunta se quer reservar
            return {"erro": f"Ramal ocupado por {quem}.", "tipo": "ocupado", "opcoes": opcoes_reserva(dados.agora())}
        agora = dados.agora()
        if not vend.get("admin"):                               # o gestor usa o discador a qualquer hora
            atual = turno_de(agora)
            if not atual:
                return {"erro": "Fora do horário dos turnos de discagem.\n\n" + "  \n".join(linhas_turnos()),
                        "tipo": "fora"}
            n, ini, _ = atual
            fila = _fila(conn, ini.date(), n)
            if fila and fila[0] != vend["id"] and agora < ini + dt.timedelta(minutes=RESERVA_MIN):
                lib = ini + dt.timedelta(minutes=RESERVA_MIN)
                return {"erro": f"Turno reservado para outro usuário. Aguarde até {lib:%H:%M} e tente novamente.",
                        "tipo": "reservado", "ate": lib, "turno": n, "proximos": proximos_turnos(agora, 3)}
        conn.execute(sa.text("INSERT INTO crm_discador_turnos (vendedor_id, inicio, ultimo_sinal) VALUES (:v, :i, :i)"),
                     {"v": vend["id"], "i": agora})
    return {"ok": True, "mensagem": "Turno aberto. Tudo que o discador conectar agora é seu."}


def encerrar_turno(vend: dict, forcar: bool = False) -> dict:
    sincronizar(forcar=True)                     # fecha a conta do turno antes de soltar o discador
    with dados.engine().begin() as conn:
        cond = "" if forcar else "AND vendedor_id = :v"
        n = conn.execute(sa.text(f"UPDATE crm_discador_turnos SET fim = :f, fechado_por = :q "
                                 f"WHERE fim IS NULL {cond}"),
                         {"f": dados.agora(), "q": vend["nome"][:60], "v": vend["id"]}).rowcount
    sincronizar(forcar=True)
    return {"ok": True, "mensagem": "Turno encerrado." if n else "Não havia turno aberto."}


# ---------------------------------------------------------------- 3C -> carteira

def _segundos(hms) -> int:
    try:
        h, m, s = (int(x) for x in str(hms or "0:0:0").split(":"))
        return h * 3600 + m * 60 + s
    except ValueError:
        return 0


def _identificador(ch: dict) -> str:
    v = str((ch.get("mailing_data") or {}).get("identifier") or ch.get("identifier") or "").strip()
    return v if v.isdigit() and len(v) == 14 else ""


def motivo_carteira(ch: dict) -> str:
    """Por que esta chamada poe a empresa na carteira; vazio = nao poe."""
    q = dados._sem_acento(dados._qualificacao_de(ch))
    if q in QUALIF_CARTEIRA:
        return dados._qualificacao_de(ch)
    # "Nao qualificada" (ou "-") e o que a 3C poe quando ninguem deu a baixa: vale a regra dos 60 s de conversa
    if q.strip(" -").lower() in ("", "nao qualificada") and _segundos(ch.get("speaking_time")) >= CONVERSA_MIN_S:
        return "a qualificar (conversa sem baixa)"
    return ""


def campanha_de_servico(nome: str | None) -> bool:
    """Campanhas de servico sao as da 3C cujo nome comeca com #SERVIÇO (atuais e futuras)."""
    return dados._sem_acento(nome or "").upper().replace(" ", "").startswith("#SERVICO")


def _para_servicos(conn, cnpj: str, t: dict, cid: str, campanha: str) -> bool:
    """Poe a empresa no Funil Servicos (a etapa e a mesma da carteira: empresas.etapa_crm)."""
    n = conn.execute(sa.text("INSERT INTO crm_servicos_cards (cnpj, vendedor_id, campanha, chamada_3c, desde) "
                             "VALUES (:c, :v, :cp, :ch, :d) ON CONFLICT (cnpj) DO NOTHING"),
                     {"c": cnpj, "v": t["vendedor_id"], "cp": (campanha or "")[:120], "ch": cid, "d": dados.agora()}).rowcount
    if n:
        _historico(conn, cnpj, "nota", t["nome"], f"Entrou no Funil Serviços (campanha {campanha}).")
    return bool(n)


def funil_servicos(vendedor_id: int | None) -> list[dict]:
    """Os cards do Funil Servicos (de um vendedor, ou todos para o admin)."""
    filtro = "WHERE s.vendedor_id = :v" if vendedor_id else ""
    with dados.engine().connect() as conn:
        linhas = _q(conn, f"SELECT e.*, s.campanha, s.desde, s.vendedor_id, v.nome AS vendedor "
                          "FROM crm_servicos_cards s JOIN empresas e ON e.cnpj = s.cnpj "
                          f"LEFT JOIN crm_vendedores v ON v.id = s.vendedor_id {filtro} ORDER BY s.desde DESC",
                    **({"v": vendedor_id} if vendedor_id else {}))
    hoje = dados.agora().isoformat(sep=" ", timespec="minutes")
    for l in linhas:
        l["nome_exibir"] = l["nome_fantasia"] or l["nome"] or l["cnpj"]
        l["atrasado"] = bool(l["proximo_contato"] and l["proximo_contato"] <= hoje)
        l["telefones"] = telefones_empresa(l)
    return linhas


def sincronizar(forcar: bool = False) -> dict:
    """Le as chamadas do discador desde a ultima leitura e reparte pelos turnos.
    Roda no maximo uma vez por minuto para o site todo (marca em `crm_discador_sync`)."""
    momento = dados.agora()
    cont = {"chamadas": 0, "novas_carteira": 0}
    para_ia = []                                  # conversas gravadas: resumo automatico depois do commit
    with dados.engine().begin() as conn:
        m = _q(conn, "SELECT * FROM crm_discador_sync WHERE id = 1 FOR UPDATE")
        if m and not forcar and (momento - m[0]["rodou_em"]).total_seconds() < SYNC_INTERVALO_S:
            return cont
        turnos = _q(conn, "SELECT t.id, t.vendedor_id, t.inicio, COALESCE(t.fim, :a) AS fim, v.nome "
                          "FROM crm_discador_turnos t JOIN crm_vendedores v ON v.id = t.vendedor_id "
                          "WHERE COALESCE(t.fim, :a) >= :desde",
                    a=momento, desde=(m[0]["ate"] if m else momento - dt.timedelta(days=1)) - dt.timedelta(minutes=30))
        if not turnos:
            _marcar(conn, m, momento, momento)
            return cont
        ini = min(t["inicio"] for t in turnos)
        if m:
            ini = max(ini, m[0]["ate"] - dt.timedelta(minutes=30))   # a 3C fecha a chamada com atraso
        try:
            chamadas = tresc.chamadas_periodo(ini.strftime("%Y-%m-%d %H:%M:%S"),
                                              momento.strftime("%Y-%m-%d %H:%M:%S"))
        except tresc.Erro3C as e:
            return {**cont, "erro": str(e)}
        for ch in chamadas:
            cnpj = _identificador(ch)
            quando = dados._quando_3c(ch)
            if not cnpj or not quando:
                continue                          # Click2Call (sem mailing) nao e do discador
            t = next((t for t in turnos if t["inicio"] <= quando <= t["fim"]), None)
            if not t:
                continue
            cont["chamadas"] += 1
            motivo = motivo_carteira(ch)
            cid = str(ch["id"])
            antes = _q(conn, "SELECT entrou_carteira, qualificacao FROM crm_discador_chamadas WHERE chamada_3c = :c", c=cid)
            vals = {"c": cid, "cnpj": cnpj, "tel": dados.normalizar(ch.get("number"))[:20], "q": quando,
                    "t": t["id"], "v": t["vendedor_id"], "ql": dados._qualificacao_de(ch)[:80] or None,
                    "r": dados.resultado(ch)[:80] or None, "d": str(ch.get("speaking_time") or "")[:12] or None,
                    "g": ch.get("recorded") in (True, "True", "true", 1, "1"), "l": momento}
            conn.execute(sa.text(
                "INSERT INTO crm_discador_chamadas (chamada_3c, cnpj, telefone, quando, turno_id, vendedor_id, "
                "qualificacao, resultado, duracao, gravada, lido_em) VALUES (:c, :cnpj, :tel, :q, :t, :v, :ql, :r, :d, :g, :l) "
                "ON CONFLICT (chamada_3c) DO UPDATE SET qualificacao = EXCLUDED.qualificacao, "
                "resultado = EXCLUDED.resultado, duracao = EXCLUDED.duracao, gravada = EXCLUDED.gravada, lido_em = :l"),
                vals)
            falou = _segundos(ch.get("speaking_with_agent_time")) or _segundos(ch.get("speaking_time"))
            if vals["g"] and falou >= CONVERSA_MIN_S:
                emp = _q(conn, "SELECT nome, nome_fantasia, cidade, estado FROM empresas WHERE cnpj = :c", c=cnpj)
                e = emp[0] if emp else {}
                para_ia.append((cid, cnpj, f"Empresa: {e.get('nome_fantasia') or e.get('nome') or cnpj} "
                                f"({e.get('cidade') or ''}/{e.get('estado') or ''})", t["nome"],
                                f"Ligação de {quando:%d/%m %H:%M} com {t['nome']} · conversa {vals['d'] or ''} · "
                                f"{dados.formatar(vals['tel'])}"))
            if motivo and campanha_de_servico(ch.get("campaign")):
                _para_servicos(conn, cnpj, t, cid, str(ch.get("campaign")))
            if antes and antes[0]["entrou_carteira"]:
                continue
            if not antes or (antes[0]["qualificacao"] or "") != (vals["ql"] or ""):
                dados_txt = (f"Discador 3C ({t['nome']}): {vals['ql'] or 'sem qualificação'} · "
                             f"{vals['r'] or '-'} · conversa {vals['d'] or '00:00:00'} · {dados.formatar(vals['tel'])}")
                _historico(conn, cnpj, "ligacao", t["nome"], dados_txt)
            if motivo and _para_carteira(conn, cnpj, t, cid, motivo):
                conn.execute(sa.text("UPDATE crm_discador_chamadas SET entrou_carteira = TRUE WHERE chamada_3c = :c"),
                             {"c": cid})
                cont["novas_carteira"] += 1
        _marcar(conn, m, momento, momento)
    for cid, cnpj, contexto, quem, titulo in para_ia:
        ia_auto.agendar(cid, cnpj, contexto, quem, titulo, registrar_resumo_ia, anotacoes_recentes)
    return cont


def _marcar(conn, m, ate, rodou) -> None:
    if m:
        conn.execute(sa.text("UPDATE crm_discador_sync SET ate = :a, rodou_em = :r WHERE id = 1"), {"a": ate, "r": rodou})
    else:
        conn.execute(sa.text("INSERT INTO crm_discador_sync (id, ate, rodou_em) VALUES (1, :a, :r) "
                             "ON CONFLICT (id) DO NOTHING"), {"a": ate, "r": rodou})


def _historico(conn, cnpj: str, tipo: str, quem: str, texto: str, de: str = "", para: str = "",
               proximo: dt.datetime | None = None) -> None:
    conn.execute(sa.text(
        "INSERT INTO crm_interacoes (cnpj, quando, quem, tipo, etapa_de, etapa_para, texto, proximo_contato) "
        "VALUES (:c, :q, :w, :t, :de, :para, :tx, :p)"),
        {"c": cnpj, "q": dados.agora(), "w": (quem or "")[:60], "t": tipo, "de": de or None,
         "para": para or None, "tx": texto or None, "p": proximo})


def _para_carteira(conn, cnpj: str, turno: dict, cid: str, motivo: str) -> bool:
    """Primeiro que falou, leva. Quem ja tem dono continua com o dono (o admin transfere, se quiser)."""
    ok = conn.execute(sa.text(
        "INSERT INTO crm_carteira (cnpj, vendedor_id, desde, origem, chamada_3c, motivo) "
        "VALUES (:c, :v, :d, 'discador', :ch, :m) ON CONFLICT (cnpj) DO NOTHING"),
        {"c": cnpj, "v": turno["vendedor_id"], "d": dados.agora(), "ch": cid, "m": motivo[:80]}).rowcount
    if not ok:
        return False
    emp = _q(conn, "SELECT etapa_crm FROM empresas WHERE cnpj = :c", c=cnpj)
    if emp:
        etapa = ETAPA_DA_QUALIF.get(dados._sem_acento(motivo))
        atual = (emp[0]["etapa_crm"] or "").strip()
        vals = {"r": turno["nome"][:60], "c": cnpj, "dc": dados.agora().strftime("%d/%m/%Y")}
        extra = ""
        if etapa and atual in ETAPAS_INICIAIS:
            extra = ", etapa_crm = :e"
            vals["e"] = etapa
        conn.execute(sa.text(f"UPDATE empresas SET responsavel = :r, data_contato = :dc{extra} WHERE cnpj = :c"), vals)
        if extra:
            _historico(conn, cnpj, "etapa", turno["nome"], f"pelo discador: {motivo}", atual, etapa)
    _historico(conn, cnpj, "nota", turno["nome"], f"Entrou na carteira de {turno['nome']} ({motivo}).")
    return True


# ---------------------------------------------------------------- carteira

COLS_LISTA = ("e.cnpj, e.nome, e.nome_fantasia, e.cidade, e.estado, e.entidade, e.aba, e.etapa_crm, "
              "e.proximo_contato, e.telefone_1, e.telefone_site")


def carteira(vendedor_id: int | None) -> list[dict]:
    """Clientes da carteira (de um vendedor, ou de todos para o admin), com o ultimo contato."""
    filtro = "WHERE c.vendedor_id = :v" if vendedor_id else ""
    with dados.engine().connect() as conn:
        linhas = _q(conn, f"SELECT {COLS_LISTA}, c.vendedor_id, c.desde, c.motivo, v.nome AS vendedor, "
                          "(SELECT max(i.quando) FROM crm_interacoes i WHERE i.cnpj = c.cnpj) AS ultimo, "
                          "EXISTS (SELECT 1 FROM crm_servicos_cards s WHERE s.cnpj = c.cnpj) AS servico "
                          "FROM crm_carteira c JOIN empresas e ON e.cnpj = c.cnpj "
                          f"JOIN crm_vendedores v ON v.id = c.vendedor_id {filtro} ORDER BY c.desde DESC",
                    **({"v": vendedor_id} if vendedor_id else {}))
    hoje = dados.agora().isoformat(sep=" ", timespec="minutes")
    for l in linhas:
        l["nome_exibir"] = l["nome_fantasia"] or l["nome"] or l["cnpj"]
        l["atrasado"] = bool(l["proximo_contato"] and l["proximo_contato"] <= hoje)
    return linhas


def dono(cnpj: str) -> dict | None:
    with dados.engine().connect() as conn:
        r = _q(conn, "SELECT c.*, v.nome FROM crm_carteira c JOIN crm_vendedores v ON v.id = c.vendedor_id "
                     "WHERE c.cnpj = :c", c=cnpj)
    return r[0] if r else None


COLS_TEL = [("telefone_site", "site"), ("telefone_1", "tel 1"), ("telefone_2", "tel 2"), ("telefone_3", "tel 3"),
            ("telefone_4", "tel 4"), ("telefone_5", "tel 5"), ("telefone_receita_1", "Receita"),
            ("telefone_receita_2", "Receita"), ("telefone_receita_3", "Receita"), ("whatsapp_ti", "WhatsApp TI"),
            ("whatsapp_rh", "WhatsApp RH"), ("whatsapp_financeiro", "WhatsApp Financeiro"),
            ("whatsapp_fiscal", "WhatsApp Fiscal"), ("whatsapp_contabil", "WhatsApp Contábil")]


def telefones_empresa(e: dict, extras: list[dict] | None = None) -> list[dict]:
    vistos: dict[str, dict] = {}
    fontes = [(x["telefone"], x["rotulo"]) for x in (extras or [])]       # incluidos a mao vem primeiro
    fontes += [(e.get(col), rot) for col, rot in COLS_TEL]
    for bruto, rot in fontes:
        for parte in re.split(r"[/;,]", str(bruto or "")):
            d = dados.normalizar(parte)
            if not d:
                continue
            if d in vistos:
                if rot not in vistos[d]["rotulos"]:
                    vistos[d]["rotulos"].append(rot)
                continue
            vistos[d] = {"tel": d, "exibir": dados.formatar(d), "rotulos": [rot]}
    return list(vistos.values())


def ficha(cnpj: str) -> dict | None:
    with dados.engine().connect() as conn:
        e = _q(conn, "SELECT * FROM empresas WHERE cnpj = :c", c=cnpj)
        if not e:
            return None
        hist = _q(conn, "SELECT * FROM crm_interacoes WHERE cnpj = :c ORDER BY quando DESC LIMIT 200", c=cnpj)
        cham = _q(conn, "SELECT d.*, v.nome AS vendedor FROM crm_discador_chamadas d "
                        "LEFT JOIN crm_vendedores v ON v.id = d.vendedor_id WHERE d.cnpj = :c "
                        "ORDER BY d.quando DESC LIMIT 100", c=cnpj)
        extras = dados.telefones_extras(conn, cnpj).get(cnpj)
    return {"empresa": e[0], "historico": hist, "chamadas": cham, "telefones": telefones_empresa(e[0], extras),
            "dono": dono(cnpj)}


def _pode(vend: dict, cnpj: str) -> bool:
    d = dono(cnpj)
    return bool(vend.get("admin") or (d and d["vendedor_id"] == vend["id"]))


def mover(vend: dict, cnpj: str, etapa: str) -> dict:
    if not _pode(vend, cnpj):
        return {"erro": "Esse cliente não está na sua carteira."}
    if etapa not in {e["nome"] for e in dados.etapas()}:
        return {"erro": f"etapa desconhecida: {etapa}"}
    with dados.engine().begin() as conn:
        atual = (_q(conn, "SELECT etapa_crm FROM empresas WHERE cnpj = :c", c=cnpj) or [{}])[0].get("etapa_crm") or ""
        if atual == etapa:
            return {"ok": True}
        conn.execute(sa.text("UPDATE empresas SET etapa_crm = :e WHERE cnpj = :c"), {"e": etapa, "c": cnpj})
        _historico(conn, cnpj, "etapa", vend["nome"], "", atual, etapa)
    return {"ok": True, "mensagem": f"Etapa: {etapa}"}


def agendar(vend: dict, cnpj: str, quando: dt.datetime | None) -> dict:
    if not _pode(vend, cnpj):
        return {"erro": "Esse cliente não está na sua carteira."}
    with dados.engine().begin() as conn:
        conn.execute(sa.text("UPDATE empresas SET proximo_contato = :p WHERE cnpj = :c"),
                     {"p": quando.isoformat(sep=" ", timespec="minutes") if quando else None, "c": cnpj})
        _historico(conn, cnpj, "agenda", vend["nome"],
                   f"retorno para {quando:%d/%m/%Y %H:%M}" if quando else "retorno desmarcado", proximo=quando)
    return {"ok": True, "mensagem": "Retorno agendado." if quando else "Retorno desmarcado."}


def insight(cnpj: str) -> str:
    """A pesquisa de abordagem (insight) da empresa, quando ela esta na base de New Names."""
    with dados.engine().connect() as conn:
        for tab in ("new_names_cyscon_motor_vendas", "new_names_cyscon"):
            r = conn.execute(sa.text(f"SELECT insights_abordagem_cyscon FROM {tab} WHERE regexp_replace(cnpj, '[^0-9]', '', 'g') = :c "
                                     "AND coalesce(insights_abordagem_cyscon, '') <> '' LIMIT 1"), {"c": cnpj}).scalar()
            if r:
                return str(r)
    return ia_auto.ia_ligacoes.insight_salvo(cnpj)       # o feito pela IA na hora da ligacao


def anotacoes_recentes(cnpj: str, horas: int = 2) -> str:
    """O post-it das ligacoes recentes desta empresa (para a IA usar no resumo)."""
    with dados.engine().connect() as conn:
        r = _q(conn, "SELECT texto FROM crm_interacoes WHERE cnpj = :c AND texto LIKE 'Anotação da ligação%' "
                     "AND quando >= :d ORDER BY quando", c=cnpj, d=dados.agora() - dt.timedelta(hours=horas))
    return "\n".join(x["texto"] for x in r)


_TEL_NO_TEXTO = re.compile(r"(?<![\d@])(?:\(?0?(\d{2})\)?[\s.-]*)?(9?\d{4})[\s.-]?(\d{4})(?![\d@])")
_EMAIL_NO_TEXTO = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


_AREAS = [("TI", r"\b(ti|t\.i\.|tecnologia|inform[aá]tica)\b"), ("RH", r"\b(rh|recursos humanos|dp|departamento pessoal)\b"),
          ("Fiscal", r"\bfiscal\b"), ("Financeiro", r"\bfinanceir[oa]\b"), ("Contábil", r"\bcont[aá]b(il|ilidade)\b"),
          ("Controladoria", r"\bcontroladoria\b"), ("Compras", r"\bcompras?\b"), ("Comercial", r"\b(comercial|vendas)\b"),
          ("Diretoria", r"\b(diretori[ao]|diretor[a]?|dono|s[oó]cio|propriet[aá]rio)\b"), ("Administrativo", r"\badministrativo\b"),
          ("Logística", r"\blog[ií]stica\b"), ("Produção", r"\bprodu[cç][aã]o\b")]


def _rotulo_area(resto: str) -> str:
    """Rotulo do telefone: a AREA (TI, RH, Fiscal...) na frente e o nome/resto depois: "Controladoria · Ticiana"."""
    resto = re.sub(r"\s{2,}", " ", resto).strip(" -–:·,;()")
    for area, padrao in _AREAS:
        m = re.search(padrao, resto, re.I)
        if m:
            nome = re.sub(r"\s{2,}", " ", (resto[:m.start()] + " " + resto[m.end():])).strip(" -–:·,;()")
            nome = re.sub(r"^(falar|ligar|conversar)\s+(com|para|p/)\s+|\s+(do|da|de|no|na)$", "", nome, flags=re.I).strip()
            return f"{area} · {nome}"[:40] if nome else area
    return resto[:40]


def contatos_do_postit(cnpj: str, texto: str, tel_da_ligacao: str, quem: str) -> list[str]:
    """Telefones e e-mails escritos no post-it entram nos contatos da empresa. Telefone sem DDD ganha o DDD
    da ligacao; o resto da linha (nome, area) vira o rotulo. Devolve o que foi incluido agora."""
    ddd = (dados.normalizar(tel_da_ligacao) or "")[:2]
    novos = []
    for linha in (texto or "").splitlines():
        sem_email = _EMAIL_NO_TEXTO.sub(" ", linha)
        for m in _TEL_NO_TEXTO.finditer(sem_email):
            numero = (m.group(1) or ddd) + m.group(2) + m.group(3)
            rotulo = _rotulo_area(sem_email[:m.start()] + " " + sem_email[m.end():])
            r = dados.adicionar_telefone(cnpj, numero, rotulo or "do post-it", quem)
            if r.get("ok") and "incluído" in r.get("mensagem", ""):
                novos.append(f"telefone {dados.formatar(dados.normalizar(numero))}" + (f" ({rotulo})" if rotulo else ""))
        for em in _EMAIL_NO_TEXTO.findall(linha):
            em = em.lower().rstrip(".")
            with dados.engine().begin() as conn:
                e = _q(conn, "SELECT email, email_2 FROM empresas WHERE cnpj = :c", c=cnpj)
                if not e or em in f"{e[0]['email'] or ''} {e[0]['email_2'] or ''}".lower():
                    continue
                col = "email" if not e[0]["email"] else "email_2"
                atual = e[0][col] or ""
                conn.execute(sa.text(f"UPDATE empresas SET {col} = :v WHERE cnpj = :c"),
                             {"v": f"{atual}; {em}" if atual else em, "c": cnpj})
            novos.append(f"e-mail {em}")
    if novos:
        with dados.engine().begin() as conn:
            _historico(conn, cnpj, "nota", quem, "Contatos incluídos pelo post-it: " + "; ".join(novos))
    return novos


def anotar_ligacao(cnpj: str, quem: str, texto: str, hist_id: int | None = None) -> int | None:
    """O post-it da ligacao no historico da empresa: cria a linha na primeira vez e depois so a atualiza
    (salva sozinho enquanto o vendedor escreve, com ou sem baixa). Devolve o id da linha."""
    with dados.engine().begin() as conn:
        if hist_id:
            n = conn.execute(sa.text("UPDATE crm_interacoes SET texto = :t WHERE id = :i AND cnpj = :c"),
                             {"t": texto, "i": hist_id, "c": cnpj}).rowcount
            if n:
                return hist_id
        _historico(conn, cnpj, "ligacao", quem, texto)
        return conn.execute(sa.text("SELECT max(id) FROM crm_interacoes WHERE cnpj = :c AND texto = :t"),
                            {"c": cnpj, "t": texto}).scalar()


def registrar_resumo_ia(cnpj: str, texto: str) -> None:
    """O resumo da ligacao feito pela IA entra no historico da empresa (carteira). Telefones e e-mails do
    "Contato indicado" (menos os marcados "conferir") entram nos contatos, com a area no rotulo."""
    with dados.engine().begin() as conn:
        _historico(conn, cnpj, "nota", "IA · resumo da ligação", texto)
    linha = next((l for l in texto.splitlines() if l.lower().startswith("contato indicado")), "")
    if linha and "conferir" not in linha.lower():
        tel = re.search(r"\(\d{2}\) \d{4,5}-\d{4}", texto.splitlines()[0])      # o telefone da ligacao, no titulo
        contatos_do_postit(cnpj, linha.split(":", 1)[-1], tel.group(0) if tel else "", "IA · resumo da ligação")


def registrar(vend: dict, cnpj: str, tipo: str, texto: str) -> dict:
    if not _pode(vend, cnpj):
        return {"erro": "Esse cliente não está na sua carteira."}
    if tipo not in {"ligacao", "email", "email_plataforma", "email_resposta", "whatsapp", "reuniao", "nota"}:
        return {"erro": f"tipo invalido: {tipo}"}
    if not (texto or "").strip():
        return {"erro": "Escreva o que aconteceu."}
    with dados.engine().begin() as conn:
        conn.execute(sa.text("UPDATE empresas SET data_contato = :d WHERE cnpj = :c"),
                     {"d": dados.agora().strftime("%d/%m/%Y"), "c": cnpj})
        _historico(conn, cnpj, tipo, vend["nome"], texto.strip())
    return {"ok": True, "mensagem": "Registrado."}


def qtd_carteira(vendedor_id: int) -> int:
    with dados.engine().connect() as conn:
        return conn.execute(sa.text("SELECT count(*) FROM crm_carteira WHERE vendedor_id = :i"), {"i": vendedor_id}).scalar()


def pool_resumo() -> dict:
    """O pool de contas = empresas da mestra sem vendedor. Devolve o total e os New Names (funil) ainda sem dono."""
    with dados.engine().connect() as conn:
        total = conn.execute(sa.text("SELECT count(*) FROM empresas e WHERE NOT EXISTS "
                                     "(SELECT 1 FROM crm_carteira c WHERE c.cnpj = e.cnpj)")).scalar()
        nn = _q(conn, f"SELECT l.id, l.cnpj, coalesce(l.nome_fantasia, l.razao_social) AS nome, l.cidade, l.estado "
                      f"FROM {dados.FONTE} l WHERE l.cnpj IS NOT NULL AND NOT EXISTS "
                      "(SELECT 1 FROM crm_carteira c WHERE c.cnpj = l.cnpj) ORDER BY l.id")
        sem_cnpj = conn.execute(sa.text(f"SELECT count(*) FROM {dados.FONTE} WHERE cnpj IS NULL")).scalar()
    return {"total": total, "new_names": nn, "new_names_sem_cnpj": sem_cnpj}


def pool_buscar(texto: str, limite: int = 40) -> list[dict]:
    """Empresas sem dono que combinam com o texto (nome, fantasia, CNPJ ou cidade)."""
    t = (texto or "").strip()
    if len(t) < 3:
        return []
    with dados.engine().connect() as conn:
        return _q(conn, "SELECT e.cnpj, coalesce(e.nome_fantasia, e.nome) AS nome, e.cidade, e.estado, e.porte_da_empresa "
                        "FROM empresas e WHERE NOT EXISTS (SELECT 1 FROM crm_carteira c WHERE c.cnpj = e.cnpj) "
                        "AND (e.nome ILIKE :t OR e.nome_fantasia ILIKE :t OR e.cnpj LIKE :c OR e.cidade ILIKE :t) "
                        "ORDER BY e.nome LIMIT :n",
                  t=f"%{t}%", c=f"{''.join(ch for ch in t if ch.isdigit())}%" if any(ch.isdigit() for ch in t) else "#", n=limite)


def atribuir_lote(admin: dict, cnpjs: list[str], para_id: int) -> dict:
    """Admin: tira do pool e poe na carteira do vendedor. Quem ja tem dono nao e tocado (use Carteiras para transferir)."""
    if not admin.get("admin"):
        return {"erro": "Só o administrador atribui clientes."}
    ok = pulou = 0
    for cnpj in cnpjs:
        if dono(cnpj):
            pulou += 1
            continue
        ok += bool(transferir(admin, cnpj, para_id).get("ok"))
    msg = f"{ok} cliente(s) atribuído(s)." + (f" {pulou} já tinham dono e não foram alterados." if pulou else "")
    return {"ok": True, "mensagem": msg}


def transferir(admin: dict, cnpj: str, para_id: int | None) -> dict:
    """Admin: passa o cliente para outro vendedor, ou devolve para a base (para_id None)."""
    if not admin.get("admin"):
        return {"erro": "Só o administrador transfere clientes."}
    with dados.engine().begin() as conn:
        antes = _q(conn, "SELECT v.nome FROM crm_carteira c JOIN crm_vendedores v ON v.id = c.vendedor_id "
                         "WHERE c.cnpj = :c", c=cnpj)
        de = antes[0]["nome"] if antes else "base"
        if para_id is None:
            conn.execute(sa.text("DELETE FROM crm_carteira WHERE cnpj = :c"), {"c": cnpj})
            conn.execute(sa.text("UPDATE empresas SET responsavel = NULL WHERE cnpj = :c"), {"c": cnpj})
            para = "base"
        else:
            para = _q(conn, "SELECT nome FROM crm_vendedores WHERE id = :i", i=para_id)[0]["nome"]
            conn.execute(sa.text(
                "INSERT INTO crm_carteira (cnpj, vendedor_id, desde, origem, motivo) VALUES (:c, :v, :d, 'admin', :m) "
                "ON CONFLICT (cnpj) DO UPDATE SET vendedor_id = :v, desde = :d, origem = 'admin', motivo = :m"),
                {"c": cnpj, "v": para_id, "d": dados.agora(), "m": f"transferido por {admin['nome']}"[:80]})
            conn.execute(sa.text("UPDATE empresas SET responsavel = :r WHERE cnpj = :c"), {"r": para[:60], "c": cnpj})
        _historico(conn, cnpj, "nota", admin["nome"], f"Carteira: {de} → {para}.")
    return {"ok": True, "mensagem": f"Cliente passou de {de} para {para}."}


def turnos_recentes(dias: int = 7) -> list[dict]:
    with dados.engine().connect() as conn:
        return _q(conn, "SELECT t.*, v.nome, (SELECT count(*) FROM crm_discador_chamadas d WHERE d.turno_id = t.id) "
                        "AS chamadas, (SELECT count(*) FROM crm_discador_chamadas d WHERE d.turno_id = t.id "
                        "AND d.entrou_carteira) AS carteira FROM crm_discador_turnos t "
                        "JOIN crm_vendedores v ON v.id = t.vendedor_id WHERE t.inicio >= :d ORDER BY t.id DESC",
                  d=dados.agora() - dt.timedelta(days=dias))


def na_linha() -> dict | None:
    """A empresa que o discador esta com o vendedor agora (pelo CNPJ do mailing ou pelo telefone)."""
    try:
        ativas = tresc.ativas()
    except tresc.Erro3C:
        return None
    for ch in ativas:
        # o discador preditivo disca varios numeros ao mesmo tempo: so a chamada ja passada a um atendente
        # (agent != 0) e a que esta com o vendedor; as outras ainda estao chamando
        if str(ch.get("agent") or "0") in ("0", ""):
            continue
        cnpj = _identificador(ch)
        if not cnpj:
            tel = dados.normalizar(ch.get("number") or ch.get("phone"))
            if not tel:
                continue
            with dados.engine().connect() as conn:
                r = _q(conn, "SELECT cnpj FROM crm_discador_chamadas WHERE telefone = :t ORDER BY quando DESC LIMIT 1", t=tel)
            cnpj = r[0]["cnpj"] if r else ""
        if cnpj:
            return {"cnpj": cnpj, "telefone": dados.normalizar(ch.get("number") or ch.get("phone")),
                    "status": ch.get("_grupo") or "", "id": str(ch.get("id") or ch.get("call_id") or "")}
    return None
