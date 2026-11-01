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
]


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
    return re.sub(r"[^a-z0-9._-]", "", (login or "").strip().lower())


def tem_vendedor() -> bool:
    with dados.engine().connect() as conn:
        return bool(conn.execute(sa.text("SELECT 1 FROM crm_vendedores LIMIT 1")).first())


def entrar(login: str, senha: str) -> dict | None:
    """O vendedor (sem o hash) ou None. Mesmo tempo de resposta com login certo ou errado."""
    with dados.engine().begin() as conn:
        r = _q(conn, "SELECT * FROM crm_vendedores WHERE login = :l AND ativo", l=_login_ok(login))
        if not r:
            hash_senha(senha or "")               # gasta o mesmo tempo: nao entrega quais logins existem
            return None
        if not confere(senha or "", r[0]["senha_hash"]):
            return None
        conn.execute(sa.text("UPDATE crm_vendedores SET ultimo_acesso = :a WHERE id = :i"),
                     {"a": dados.agora(), "i": r[0]["id"]})
    return {k: v for k, v in r[0].items() if k != "senha_hash"}


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


def ativar(vendedor_id: int, ativo: bool) -> dict:
    with dados.engine().begin() as conn:
        conn.execute(sa.text("UPDATE crm_vendedores SET ativo = :a WHERE id = :i"), {"a": ativo, "i": vendedor_id})
    return {"ok": True, "mensagem": "Vendedor reativado." if ativo else "Vendedor desativado (a carteira fica)."}


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
            FROM crm_vendedores v ORDER BY v.ativo DESC, v.nome""",
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
                        "(SELECT count(*) FROM crm_carteira c WHERE c.vendedor_id = v.id) AS clientes "
                        "FROM crm_vendedores v ORDER BY v.ativo DESC, v.nome")


# ---------------------------------------------------------------- turno no discador

def _fechar_esquecidos(conn) -> None:
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
            return {"erro": f"O discador está com {r[0]['nome']} desde {r[0]['inicio']:%H:%M}. "
                            "A 3C tem um usuário só: espere ele encerrar o turno."}
        conn.execute(sa.text("INSERT INTO crm_discador_turnos (vendedor_id, inicio) VALUES (:v, :i)"),
                     {"v": vend["id"], "i": dados.agora()})
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
    if not q and _segundos(ch.get("speaking_time")) >= CONVERSA_MIN_S:
        return "a qualificar (conversa sem baixa)"
    return ""


def sincronizar(forcar: bool = False) -> dict:
    """Le as chamadas do discador desde a ultima leitura e reparte pelos turnos.
    Roda no maximo uma vez por minuto para o site todo (marca em `crm_discador_sync`)."""
    momento = dados.agora()
    cont = {"chamadas": 0, "novas_carteira": 0}
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
                          "(SELECT max(i.quando) FROM crm_interacoes i WHERE i.cnpj = c.cnpj) AS ultimo "
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


def registrar(vend: dict, cnpj: str, tipo: str, texto: str) -> dict:
    if not _pode(vend, cnpj):
        return {"erro": "Esse cliente não está na sua carteira."}
    if tipo not in {"ligacao", "email", "whatsapp", "reuniao", "nota"}:
        return {"erro": f"tipo invalido: {tipo}"}
    if not (texto or "").strip():
        return {"erro": "Escreva o que aconteceu."}
    with dados.engine().begin() as conn:
        conn.execute(sa.text("UPDATE empresas SET data_contato = :d WHERE cnpj = :c"),
                     {"d": dados.agora().strftime("%d/%m/%Y"), "c": cnpj})
        _historico(conn, cnpj, tipo, vend["nome"], texto.strip())
    return {"ok": True, "mensagem": "Registrado."}


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
