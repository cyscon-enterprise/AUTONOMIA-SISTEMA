"""Dados do CRM online: as MESMAS tabelas do painel local (Supabase), as mesmas regras.

Espelha `cyscon_leads/funil_mv.py` do PC. O que se faz aqui aparece no painel local e vice-versa:

    new_names_cyscon_motor_vendas   os leads da secao (so leitura)
    crm_etapas                      as colunas do funil
    crm_mv_cards                    etapa e retorno de cada lead
    crm_mv_interacoes               historico (so cresce)
    crm_mv_ligacoes                 ligacoes da 3C
    crm_mv_tel_descartados          telefones tirados dos botoes
    grupo_mf_veredito               selo do sistema de pesquisa matriz/filial

Diferencas do online:
  - HORARIO: o servidor do Streamlit roda em UTC; o banco guarda horario de Brasilia sem fuso (como
    o painel local grava). Todo "agora" passa por `agora()`.
  - GRAVACAO: nao ha disco; o audio vem da 3C na hora de ouvir (`tresc3c.gravacao`).
  - SINCRONIZACAO: sem laco em segundo plano; roda ao abrir a ficha / clicar em atualizar.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from zoneinfo import ZoneInfo

import sqlalchemy as sa

import tresc3c as tresc

FONTE = "new_names_cyscon_motor_vendas"
SP = ZoneInfo("America/Sao_Paulo")
INTERVALO_MIN = 90                 # segundos entre dois pedidos para o mesmo telefone
JANELA_BUSCA_H = 4
DESISTIR_H = 6
ESPERA_GRAVACAO_MIN = 20

_ENGINE: dict = {}


def agora() -> dt.datetime:
    return dt.datetime.now(SP).replace(tzinfo=None)


def configurar(database_url: str) -> None:
    url = database_url.strip()
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.split("://", 1)[1]
    if _ENGINE.get("url") != url:
        _ENGINE["e"] = sa.create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2,
                                        pool_recycle=600)
        _ENGINE["url"] = url


def engine():
    if "e" not in _ENGINE:
        raise RuntimeError("banco nao configurado (DATABASE_URL nos Secrets)")
    return _ENGINE["e"]


def _q(conn, sql: str, **p) -> list[dict]:
    return [dict(r) for r in conn.execute(sa.text(sql), p).mappings()]


# ---------------------------------------------------------------- telefones

COLS_TELEFONE = [("telefone_atual", "atual"), ("telefone_1", "tel 1"), ("telefone_2", "tel 2"),
                 ("telefone_3", "tel 3"), ("telefone_4", "tel 4"), ("telefone_google_1", "Google")]


def normalizar(v) -> str:
    d = re.sub(r"\D", "", str(v or ""))
    if len(d) in (12, 13) and d.startswith("55"):
        d = d[2:]
    if len(d) in (11, 12) and d.startswith("0"):
        d = d[1:]
    return d if len(d) in (10, 11) else ""


def formatar(d: str) -> str:
    d = str(d or "")
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return d


def telefones_extras(conn, cnpj: str | None = None) -> dict[str, list[dict]]:
    """Telefones incluidos a mao no CRM (`crm_telefones_extra`), por CNPJ."""
    sql = ("SELECT cnpj, telefone, rotulo FROM crm_telefones_extra" + (" WHERE cnpj = :c" if cnpj else "")
           + " ORDER BY criado_em DESC")                       # o mais recente (da ultima ligacao) e o principal
    out: dict[str, list[dict]] = {}
    try:
        for r in _q(conn, sql + " ORDER BY criado_em", **({"c": cnpj} if cnpj else {})):
            out.setdefault(r["cnpj"], []).append(r)
    except sa.exc.DBAPIError:                      # tabela ainda nao criada
        conn.rollback()
    return out


def registrar_resumo_ia(lead_id: int, cnpj: str | None, texto: str) -> None:
    """O resumo da ligacao feito pela IA entra no historico do lead (funil New Names)."""
    with engine().begin() as conn:
        _registrar(conn, lead_id, cnpj, "nota", "IA · resumo da ligação", texto)


def adicionar_telefone(cnpj: str, telefone: str, rotulo: str, quem: str, lead_id: int | None = None) -> dict:
    """Inclui um telefone novo na empresa; ele vira botao de ligar pela 3C nas fichas."""
    d = normalizar(telefone)
    if not d:
        return {"erro": "Telefone inválido: use DDD + número (10 ou 11 dígitos)."}
    if not cnpj:
        return {"erro": "Empresa sem CNPJ: não dá para guardar o telefone."}
    rot = (rotulo or "").strip()[:40] or "incluído"
    with engine().begin() as conn:
        n = conn.execute(sa.text(
            "INSERT INTO crm_telefones_extra (cnpj, telefone, rotulo, quem, criado_em) VALUES (:c, :t, :r, :q, :w) "
            "ON CONFLICT (cnpj, telefone) DO NOTHING"),
            {"c": cnpj, "t": d, "r": rot, "q": (quem or "")[:60], "w": agora()}).rowcount
        if n and lead_id:
            _registrar(conn, lead_id, cnpj, "nota", quem, f"Telefone incluído: {formatar(d)} ({rot})")
    return {"ok": True, "mensagem": f"Telefone {formatar(d)} incluído." if n else "Esse telefone já estava na ficha."}


def telefones(lead: dict, fora: set[str] | None = None, extras: list[dict] | None = None) -> list[dict]:
    vistos: dict[str, dict] = {}
    fontes = [(x["telefone"], x["rotulo"]) for x in (extras or [])]
    fontes += [(lead.get(c), r) for c, r in COLS_TELEFONE]
    fontes += [(p, "outro") for p in str(lead.get("telefone_todos") or "").split("/")]
    for bruto, rotulo in fontes:
        d = normalizar(bruto)
        if not d or d in (fora or ()):
            continue
        if d in vistos:
            if rotulo != "outro" and rotulo not in vistos[d]["rotulos"]:
                vistos[d]["rotulos"].append(rotulo)
            continue
        vistos[d] = {"tel": d, "exibir": formatar(d), "rotulos": [rotulo]}
    return list(vistos.values())


def _descartados(conn) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    try:
        for r in conn.execute(sa.text("SELECT lead_id, telefone FROM crm_mv_tel_descartados")):
            out.setdefault(r[0], set()).add(r[1])
    except sa.exc.DBAPIError:
        conn.rollback()
    return out


_NOTA_CONVITE = re.compile(r"\s*\([^()]*convite[^()]*\)", re.IGNORECASE)


def limpar_contato(v):
    return _NOTA_CONVITE.sub("", v).strip() if isinstance(v, str) else v


# ---------------------------------------------------------------- leitura

def etapas(conn=None) -> list[dict]:
    if conn is None:
        with engine().connect() as c:
            return etapas(c)
    return _q(conn, "SELECT nome, tipo, cor FROM crm_etapas ORDER BY ordem")


def _vereditos(conn) -> dict[str, dict]:
    try:
        return {r["cnpj"]: r for r in _q(conn, "SELECT cnpj, veredito, detalhe, verificado_em FROM grupo_mf_veredito")}
    except sa.exc.DBAPIError:
        conn.rollback()
        return {}


def quadro() -> dict:
    """Todos os cards, com o que a lista precisa. Os leads sao poucos (dezenas): tudo numa ida."""
    with engine().connect() as conn:
        fases = etapas(conn)
        leads = _q(conn, f"SELECT * FROM {FONTE} ORDER BY id")
        estados = {r["lead_id"]: r for r in _q(conn, "SELECT * FROM crm_mv_cards")}
        fora = _descartados(conn)
        verif = _vereditos(conn)
        extras = telefones_extras(conn)
    hoje = agora().isoformat(sep=" ", timespec="minutes")
    cards = []
    for l in leads:
        e = estados.get(l["id"]) or {}
        prox = e.get("proximo_contato")
        cards.append({
            "id": l["id"], "cnpj": l.get("cnpj"), "nome": l.get("nome_fantasia") or l.get("razao_social") or "",
            "razao_social": l.get("razao_social") or "", "cidade": l.get("cidade") or "",
            "estado": l.get("estado") or "", "status_crm": l.get("status_crmtotvs") or "",
            "etapa": e.get("etapa") or (fases[0]["nome"] if fases else ""), "proximo_contato": prox,
            "atrasado": bool(prox and prox <= hoje),
            "telefones": telefones(l, fora.get(l["id"]), extras.get(l.get("cnpj"))),
            "grupo": verif.get(l.get("cnpj")),
        })
    return {"etapas": fases, "cards": cards}


def ficha(lead_id: int) -> dict | None:
    with engine().connect() as conn:
        ls = _q(conn, f"SELECT * FROM {FONTE} WHERE id = :i", i=lead_id)
        if not ls:
            return None
        estado = (_q(conn, "SELECT * FROM crm_mv_cards WHERE lead_id = :i", i=lead_id) or [{}])[0]
        hist = _q(conn, "SELECT * FROM crm_mv_interacoes WHERE lead_id = :i ORDER BY quando DESC LIMIT 200", i=lead_id)
        ligs = _q(conn, "SELECT * FROM crm_mv_ligacoes WHERE lead_id = :i ORDER BY pedido_em DESC LIMIT 100", i=lead_id)
        fora = _descartados(conn).get(lead_id)
        verif = _vereditos(conn).get(ls[0].get("cnpj"))
        extras = telefones_extras(conn, ls[0].get("cnpj")).get(ls[0].get("cnpj"))
    lead = {k: (limpar_contato(v) if k.startswith("contato_") else v) for k, v in ls[0].items()}
    return {"lead": lead, "estado": estado, "historico": hist, "ligacoes": ligs,
            "telefones": telefones(lead, fora, extras), "grupo": verif}


# ---------------------------------------------------------------- escrita no card

def _registrar(conn, lead_id: int, cnpj, tipo: str, quem: str, texto: str = "", de: str = "",
               para: str = "", ligacao_id: int | None = None) -> None:
    conn.execute(sa.text(
        "INSERT INTO crm_mv_interacoes (lead_id, cnpj, quando, quem, tipo, etapa_de, etapa_para, texto, ligacao_id) "
        "VALUES (:l, :c, :q, :w, :t, :de, :para, :tx, :lg)"),
        {"l": lead_id, "c": cnpj, "q": agora(), "w": (quem or "").strip()[:60], "t": tipo,
         "de": de or None, "para": para or None, "tx": (texto or "").strip() or None, "lg": ligacao_id})


def _card(conn, lead_id: int) -> dict | None:
    r = _q(conn, "SELECT * FROM crm_mv_cards WHERE lead_id = :i", i=lead_id)
    if r:
        return r[0]
    # lead novo que o painel local ainda nao preparou: abre o card na primeira etapa
    l = _q(conn, f"SELECT cnpj FROM {FONTE} WHERE id = :i", i=lead_id)
    if not l:
        return None
    primeira = etapas(conn)[0]["nome"]
    conn.execute(sa.text("INSERT INTO crm_mv_cards (lead_id, cnpj, etapa, atualizado) VALUES (:i, :c, :e, :a)"),
                 {"i": lead_id, "c": l[0]["cnpj"], "e": primeira, "a": agora()})
    return {"lead_id": lead_id, "cnpj": l[0]["cnpj"], "etapa": primeira}


def mover(lead_id: int, etapa: str, quem: str, nota: str = "") -> dict:
    with engine().begin() as conn:
        if etapa not in {e["nome"] for e in etapas(conn)}:
            return {"erro": f"etapa desconhecida: {etapa}"}
        atual = _card(conn, lead_id)
        if atual is None:
            return {"erro": "lead nao esta no funil"}
        if atual["etapa"] == etapa and not nota.strip():
            return {"ok": True}
        conn.execute(sa.text("UPDATE crm_mv_cards SET etapa = :e, atualizado = :a WHERE lead_id = :i"),
                     {"e": etapa, "a": agora(), "i": lead_id})
        _registrar(conn, lead_id, atual["cnpj"], "etapa", quem, nota, atual["etapa"], etapa)
    return {"ok": True}


def agendar(lead_id: int, quando: dt.datetime | None, quem: str, nota: str = "") -> dict:
    with engine().begin() as conn:
        atual = _card(conn, lead_id)
        if atual is None:
            return {"erro": "lead nao esta no funil"}
        conn.execute(sa.text("UPDATE crm_mv_cards SET proximo_contato = :p, atualizado = :a WHERE lead_id = :i"),
                     {"p": quando.isoformat(sep=" ", timespec="minutes") if quando else None,
                      "a": agora(), "i": lead_id})
        _registrar(conn, lead_id, atual["cnpj"], "agenda", quem,
                   nota or (f"retorno para {quando:%d/%m/%Y %H:%M}" if quando else "retorno desmarcado"))
    return {"ok": True}


def interagir(lead_id: int, tipo: str, texto: str, quem: str) -> dict:
    if tipo not in {"ligacao", "email", "whatsapp", "reuniao", "nota"}:
        return {"erro": f"tipo invalido: {tipo}"}
    with engine().begin() as conn:
        atual = _card(conn, lead_id)
        if atual is None:
            return {"erro": "lead nao esta no funil"}
        _registrar(conn, lead_id, atual["cnpj"], tipo, quem, texto)
    return {"ok": True}


# ---------------------------------------------------------------- ligar / desligar / baixa

def ligar(lead_id: int, telefone: str, quem: str, ramal: str) -> dict:
    tel = normalizar(telefone)
    momento = agora()
    with engine().begin() as conn:
        ls = _q(conn, f"SELECT * FROM {FONTE} WHERE id = :i", i=lead_id)
        if not ls:
            return {"erro": "lead nao encontrado"}
        cnpj = ls[0].get("cnpj")
        do_lead = {t["tel"]: t for t in telefones(ls[0], extras=telefones_extras(conn, cnpj).get(cnpj))}
        if tel not in do_lead:
            return {"erro": "esse telefone nao e deste lead"}
        recente = conn.execute(sa.text(
            "SELECT pedido_em FROM crm_mv_ligacoes WHERE telefone = :t AND status IN ('pedindo','pedida','incerta') "
            "AND pedido_em >= :lim"), {"t": tel, "lim": momento - dt.timedelta(seconds=INTERVALO_MIN)}).first()
        if recente:
            seg = int((momento - recente[0]).total_seconds())
            return {"erro": f"Já pedi ligação para {formatar(tel)} há {seg} s. Veja se o ramal {ramal} "
                            "tocou antes de tentar de novo."}
        lig_id = conn.execute(sa.text(
            "INSERT INTO crm_mv_ligacoes (lead_id, cnpj, telefone, rotulo, quem, ramal, pedido_em, status, atualizado) "
            "VALUES (:l, :c, :t, :r, :w, :ra, :p, 'pedindo', :p) RETURNING id"),
            {"l": lead_id, "c": ls[0].get("cnpj"), "t": tel, "r": ", ".join(do_lead[tel]["rotulos"])[:40],
             "w": (quem or "").strip()[:60], "ra": str(ramal), "p": momento}).scalar()
    status, erro, id_3c = "pedida", None, None
    try:
        r = tresc.ligar("55" + tel, ramal)
        d = (r or {}).get("data", r) or {}
        id_3c = str(((d.get("call") or {}).get("id")) or "") or None
    except tresc.Erro3C as e:
        msg = str(e)
        if "Timeout" in msg:
            status, erro = "incerta", "a 3C nao respondeu a tempo; a ligacao pode ter saido"
        else:
            status = "recusada"
            erro = "o ramal nao esta logado ou esta ocupado na 3C" if "422" in msg else msg
    with engine().begin() as conn:
        conn.execute(sa.text("UPDATE crm_mv_ligacoes SET status = :s, erro = :e, atualizado = :a "
                             "WHERE id = :i AND status = 'pedindo'"),
                     {"s": status, "e": erro, "a": agora(), "i": lig_id})
        if id_3c:
            conn.execute(sa.text("UPDATE crm_mv_ligacoes SET id_3c = :c WHERE id = :i"), {"c": id_3c, "i": lig_id})
    if status == "recusada":
        return {"erro": f"A 3C recusou: {erro}", "ligacao": lig_id}
    return {"ok": True, "ligacao": lig_id, "aviso": erro,
            "mensagem": f"O ramal {ramal} vai tocar; atenda para discar para {formatar(tel)}."}


def _lig(lig_id: int) -> dict | None:
    with engine().connect() as conn:
        r = _q(conn, "SELECT * FROM crm_mv_ligacoes WHERE id = :i", i=lig_id)
    return r[0] if r else None


def ligacao_aberta(lead_id: int) -> dict | None:
    """A ligacao recente deste lead que ainda nao recebeu baixa (painel de ligacao em curso)."""
    with engine().connect() as conn:
        r = _q(conn, "SELECT * FROM crm_mv_ligacoes WHERE lead_id = :l AND pedido_em >= :lim "
                     "AND status IN ('pedindo','pedida','incerta','finalizada') "
                     "AND (qualificacao IS NULL OR qualificacao = '') ORDER BY id DESC LIMIT 1",
               l=lead_id, lim=agora() - dt.timedelta(minutes=30))
    return r[0] if r else None


def em_andamento(lig: dict) -> bool:
    return chamada_ativa(lig) is not None


def chamada_ativa(lig: dict) -> dict | None:
    """A chamada deste telefone que esta acontecendo agora na 3C (tocando ou em conversa), ou None."""
    try:
        final = lig["telefone"][-8:]
        return next((c for c in tresc.ativas() if final in re.sub(r"\D", "", str(c))), None)
    except tresc.Erro3C:
        return None


def ligacao(lig_id: int) -> dict | None:
    return _lig(lig_id)


def desligar(lig_id: int, quem: str, sessao: dict | None) -> dict:
    lig = _lig(lig_id)
    if not lig:
        return {"erro": "ligacao nao encontrada"}
    chamada = lig["chamada_3c"] or lig["id_3c"]
    if not chamada:
        return {"erro": "a 3C ainda nao me deu o numero dessa chamada; tente de novo em alguns segundos"}
    try:
        tresc.desligar(str(chamada), sessao)
    except tresc.Erro3C as e:
        return {"erro": f"A 3C nao desligou: {e}"}
    with engine().begin() as conn:
        _registrar(conn, lig["lead_id"], lig["cnpj"], "ligacao", quem,
                   f"Ligação para {formatar(lig['telefone'])} desligada pelo CRM")
    return {"ok": True}


# A atendente classifica a ligacao pelas ETAPAS DO FUNIL (decisao do usuario, 04/10/2026); a 3C so
# aceita as qualificacoes dela, entao cada etapa da baixa na mais proxima. Mesmo mapa do painel local.
MAPA_3C = {"sem contato": "Sem retorno", "retornar": "Retornar", "enviar e-mail": "Enviar e-mail",
           "e-mail enviado": "Enviar e-mail", "agendar reunião": "Agendar reuniao",
           "reunião agendada": "Agendar reuniao", "enviar proposta": "Retornar",
           "proposta enviada": "Retornar", "fechado": "Ganho", "declinou": "Sem interesse",
           "sem interesse": "Sem interesse", "telefone inválido": "Contato incorreto"}


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower().strip()


def _detalhe_3c(msg: str) -> str:
    m = re.search(r'"(?:detail|message)"\s*:\s*"([^"]+)', msg)
    return m.group(1) if m else msg


def baixar(lig_id: int, etapa: str, nota: str, retorno: dt.datetime | None, quem: str, sessao: dict | None) -> dict:
    """Da baixa na ligacao pela 3C e move o card para a etapa escolhida. Se a 3C recusar por a chamada
    ja ter saido do pos-atendimento (422), a classificacao vale no CRM e o aviso diz o motivo."""
    if etapa not in {e["nome"] for e in etapas()}:
        return {"erro": f"etapa desconhecida: {etapa}"}
    lig = _lig(lig_id)
    if not lig:
        return {"erro": "ligacao nao encontrada"}
    chamada = lig["chamada_3c"] or lig["id_3c"]
    aviso = None
    if not chamada:
        aviso = "A 3C ainda não tinha o número da chamada; a baixa ficou registrada só no CRM."
    else:
        try:
            alvo = _sem_acento(MAPA_3C.get(etapa.lower().strip(), "Retornar"))
            q = next((q for q in tresc.qualificacoes_crm() if _sem_acento(q["nome"]) == alvo), None)
            if not q:
                aviso = "Não achei a qualificação equivalente na 3C; a baixa ficou só no CRM."
            else:
                tresc.baixar_chamada(str(chamada), q["id"], nota,
                                     retorno.strftime("%Y-%m-%d %H:%M:%S") if retorno else None, sessao)
        except tresc.Erro3C as e:
            msg = str(e)
            if "422" not in msg:
                return {"erro": f"A 3C não aceitou a baixa: {_detalhe_3c(msg)}"}
            aviso = f"A 3C não aceitou a baixa ({_detalhe_3c(msg)}). Ficou registrada só no CRM."
    texto = f"Baixa da ligação para {formatar(lig['telefone'])}: {etapa}" + (f". {nota}" if nota else "")
    with engine().begin() as conn:
        conn.execute(sa.text("UPDATE crm_mv_ligacoes SET qualificacao = :q, atualizado = :a WHERE id = :i"),
                     {"q": etapa, "a": agora(), "i": lig_id})
        _registrar(conn, lig["lead_id"], lig["cnpj"], "ligacao", quem, texto)
    mover(lig["lead_id"], etapa, quem)
    if retorno:
        agendar(lig["lead_id"], retorno, quem, f"retorno agendado na baixa: {etapa}")
    return {"ok": True, "aviso": aviso}


# ---------------------------------------------------------------- sincronizar com a 3C

CAUSAS = {1: "número inexistente", 3: "sem rota para o número", 17: "ocupado", 18: "não atendeu",
          19: "não atendeu", 21: "chamada rejeitada", 22: "número mudou", 27: "fora de serviço",
          28: "número inválido", 34: "sem linha disponível", 38: "rede fora do ar", 41: "falha temporária"}


def resultado(ch: dict) -> str:
    st = str(ch.get("readable_status_text") or "").strip()
    try:
        causa = CAUSAS.get(int(ch.get("hangup_cause")))
    except (TypeError, ValueError):
        causa = None
    if causa and str(ch.get("speaking_time") or "00:00:00") == "00:00:00":
        return f"{st} — {causa}" if st else causa
    return st


def _qualificacao_de(ch: dict) -> str:
    q = ch.get("qualification")
    nome = str((q.get("name") if isinstance(q, dict) else q) or "").strip()
    return "" if nome in ("-", "None") else nome


def _quando_3c(ch: dict) -> dt.datetime | None:
    for k in ("call_date_rfc3339", "call_date"):
        v = ch.get(k)
        if not v:
            continue
        try:
            t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            continue
        return t.astimezone(SP).replace(tzinfo=None) if t.tzinfo else t
    return None


def _achar(lig: dict, usados: set[str]) -> dict | None:
    if lig.get("chamada_3c") or lig.get("id_3c"):
        ch = tresc.chamada(lig.get("chamada_3c") or lig["id_3c"])
        if ch:
            return ch
    ini = lig["pedido_em"] - dt.timedelta(minutes=3)
    fim = lig["pedido_em"] + dt.timedelta(hours=JANELA_BUSCA_H)
    cand = []
    for ch in tresc.chamadas(ini.strftime("%Y-%m-%d %H:%M:%S"), fim.strftime("%Y-%m-%d %H:%M:%S")):
        if str(ch.get("id")) in usados or normalizar(ch.get("number")) != lig["telefone"]:
            continue
        t = _quando_3c(ch)
        if t is None or t >= ini:
            cand.append((t or fim, ch))
    return min(cand, key=lambda x: x[0])[1] if cand else None


def sincronizar(lead_id: int | None = None) -> dict:
    """Traz da 3C o resultado das ligacoes pendentes (de um lead ou de todos). Nao liga para ninguem.
    A gravacao nao e copiada: o card a busca na 3C quando alguem clica para ouvir."""
    cont = {"achadas": 0, "erros": 0}
    momento = agora()
    filtro = "AND lead_id = :l" if lead_id else ""
    with engine().connect() as conn:
        par = {"lim": momento - dt.timedelta(days=2)}
        if lead_id:
            par["l"] = lead_id
        fila = _q(conn, "SELECT * FROM crm_mv_ligacoes WHERE status IN ('pedida','incerta','pedindo') "
                        f"AND pedido_em >= :lim {filtro}", **par)
        usados = {str(r[0]) for r in conn.execute(sa.text(
            "SELECT chamada_3c FROM crm_mv_ligacoes WHERE chamada_3c IS NOT NULL"))}
    for lig in fila:
        idade = momento - lig["pedido_em"]
        if idade < dt.timedelta(seconds=15):           # a 3C leva uns segundos para registrar a chamada
            continue
        try:
            ch = _achar(lig, usados - {str(lig.get("chamada_3c"))})
        except tresc.Erro3C:
            cont["erros"] += 1
            continue
        if ch is None:
            if idade > dt.timedelta(hours=DESISTIR_H):
                with engine().begin() as conn:
                    conn.execute(sa.text("UPDATE crm_mv_ligacoes SET status = 'sem_registro', atualizado = :a, "
                                         "erro = 'a 3C nao tem chamada para este telefone no horario do pedido' "
                                         "WHERE id = :i"), {"a": agora(), "i": lig["id"]})
            continue
        cid = str(ch["id"])
        usados.add(cid)
        cont["achadas"] += 1
        conversou = str(ch.get("speaking_time") or "") not in ("", "00:00:00")
        gravada = ch.get("recorded") in (True, "True", "true", 1, "1")
        # fecha quando a chamada ja acabou: sem conversa, ou depois de 2 min (a 3C demora a liberar o audio)
        fecha = idade > dt.timedelta(minutes=2) or not conversou
        texto = "\n".join([
            f"Ligação 3C para {formatar(lig['telefone'])} ({lig.get('rotulo') or '-'})",
            f"Resultado: {resultado(ch) or '-'} · Qualificação: {_qualificacao_de(ch) or 'sem qualificação'}",
            f"Conversa: {ch.get('speaking_time') or '00:00:00'}",
            "Gravação no card" if gravada else "Sem gravação"])
        with engine().begin() as conn:
            conn.execute(sa.text(
                "UPDATE crm_mv_ligacoes SET chamada_3c = :c, resultado = :r, duracao = :d, gravada = :g, "
                "qualificacao = COALESCE(NULLIF(qualificacao, ''), NULLIF(:q, '')), atualizado = :a"
                + (", status = 'finalizada'" if fecha else "") + " WHERE id = :i"),
                {"c": cid, "r": resultado(ch)[:80] or None, "d": str(ch.get("speaking_time") or "")[:12] or None,
                 "g": gravada, "q": _qualificacao_de(ch)[:80], "a": agora(), "i": lig["id"]})
            if fecha and lig["status"] != "finalizada":
                _registrar(conn, lig["lead_id"], lig.get("cnpj"), "ligacao", lig.get("quem") or "", texto,
                           ligacao_id=lig["id"])
    return cont
