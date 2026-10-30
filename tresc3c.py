"""Cliente da 3C Plus para o CRM online — o mesmo de `cyscon_leads/tresc.py`, com uma diferenca.

No painel local o login do ramal fica numa variavel do modulo (`SESSAO`): la so existe uma pessoa.
No Streamlit online o mesmo processo atende todo mundo que abre o site, entao o login da atendente
(o token do agente) vive no `st.session_state` de quem entrou e e passado em cada funcao que precisa
dele. Nada de token do agente em variavel global.

O token de SERVICO (relatorio de chamadas, gravacoes, Click2Call) vem dos Secrets do site
(`TRESC_TOKEN`) e nunca aparece em mensagem de erro.
"""
from __future__ import annotations

import os

import requests

BASE = "https://app.3c.plus/api/v1"
DOMINIO = os.environ.get("CYSCON_3C_DOMINIO", "cybersolution")
LISTA_QUALIFICACAO = 34728          # "Qualificacoes do CRM" (Retornar, Enviar e-mail, Agendar reuniao...)
TIMEOUT = 20


class Erro3C(RuntimeError):
    """Falha de comunicacao ou de credencial. Nunca carrega token nem senha na mensagem."""


_TOKEN = {"v": ""}


def configurar(token_servico: str) -> None:
    _TOKEN["v"] = (token_servico or "").strip()


def tem_token() -> bool:
    return bool(_TOKEN["v"])


def _req(metodo: str, caminho: str, *, dados=None, params=None, timeout: int = TIMEOUT) -> dict:
    if not _TOKEN["v"]:
        raise Erro3C("falta o token da 3C (TRESC_TOKEN) nos Secrets do site.")
    cab = {"Authorization": f"Bearer {_TOKEN['v']}", "Accept": "application/json"}
    try:
        r = requests.request(metodo, f"{BASE}{caminho}", headers=cab, data=dados, params=params, timeout=timeout)
    except requests.RequestException as e:
        raise Erro3C(f"nao consegui falar com a 3C Plus: {type(e).__name__}") from None
    if r.status_code == 401:
        raise Erro3C("a 3C Plus recusou o token (401). Ele expirou ou foi revogado?")
    if r.status_code == 403:
        raise Erro3C("token sem permissao para esta operacao (403).")
    if r.status_code >= 400:
        raise Erro3C(f"3C Plus respondeu {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError:
        return {}


def _agente_req(metodo: str, url: str, token_agente: str | None = None, *, dados=None, timeout: int = 45) -> dict:
    cab = {"Accept": "application/json"}
    if token_agente:
        cab["Authorization"] = f"Bearer {token_agente}"
    try:
        r = requests.request(metodo, url, headers=cab, data=dados, timeout=timeout)
    except requests.RequestException as e:
        raise Erro3C(f"nao consegui falar com a 3C Plus: {type(e).__name__}") from None
    if r.status_code in (401, 403, 422) and url.endswith("/authenticate"):
        raise Erro3C("ramal ou senha incorretos na 3C Plus.")
    if r.status_code == 401:
        raise Erro3C("a 3C Plus recusou o login do agente (401). Entre de novo.")
    if r.status_code >= 400:
        try:
            msg = (r.json().get("message") or r.json().get("detail") or "")[:160]
        except ValueError:
            msg = r.text[:160]
        raise Erro3C(f"3C Plus respondeu {r.status_code}: {msg}")
    try:
        return r.json()
    except ValueError:
        return {}


def _lista(r) -> list:
    d = r.get("data", r) if isinstance(r, dict) else r
    return d if isinstance(d, list) else []


# ---------------------------------------------------------------- login da atendente

def entrar(ramal: str, senha: str, campanha: int | str | None = None) -> dict:
    """Login oficial do ramal pela API. Devolve {"sessao": {...}} ou {"escolher": [campanhas]}.
    A senha so passa por aqui: vira token do agente e some."""
    ramal = str(ramal or "").strip()
    if not ramal or not senha:
        raise Erro3C("informe o ramal e a senha da 3C.")
    base = f"https://{DOMINIO}.3c.plus/api/v1"
    r = _agente_req("POST", f"{base}/authenticate", dados={"user": ramal, "password": senha})
    u = r.get("data", r) if isinstance(r, dict) else {}
    tk = u.get("api_token") or u.get("token")
    if not tk:
        raise Erro3C("a 3C nao devolveu o token do agente.")
    lista = [c for c in _lista(_agente_req("GET", f"{base}/agent/campaigns", tk))
             if isinstance(c, dict) and c.get("id")]
    if campanha is None:
        if len(lista) == 1:
            campanha = lista[0]["id"]
        elif len(lista) > 1:
            return {"escolher": [{"id": c["id"], "nome": c.get("name") or str(c["id"])} for c in lista]}
        else:
            raise Erro3C("esse ramal nao pertence a nenhuma campanha na 3C.")
    _agente_req("POST", f"{base}/agent/login", tk, dados={"campaign": int(campanha), "mode": "manual"})
    nome_camp = next((c.get("name") for c in lista if str(c["id"]) == str(campanha)), str(campanha))
    return {"sessao": {"ramal": ramal, "nome": u.get("name") or "", "campanha": int(campanha),
                       "campanha_nome": nome_camp, "token": tk}}


def sair(sessao: dict | None) -> None:
    tk = (sessao or {}).get("token")
    if tk:
        try:
            _agente_req("POST", f"https://{DOMINIO}.3c.plus/api/v1/agent/logout", tk, timeout=20)
        except Erro3C:
            pass


# ---------------------------------------------------------------- ligacao

def ligar(telefone: str, ramal: str) -> dict:
    """Click2Call: toca no ramal da atendente e, quando ela atende, disca para o lead."""
    tel = "".join(ch for ch in str(telefone) if ch.isdigit())
    if len(tel) < 10:
        raise Erro3C(f"telefone curto demais para discar: {telefone!r}")
    # a 3C chega a levar ~60 s para responder (medido em 02/10/2026)
    return _req("POST", "/click2call", dados={"extension": str(ramal).strip(), "phone": tel}, timeout=75)


def desligar(id_chamada: str, sessao: dict | None) -> dict:
    tk = (sessao or {}).get("token")
    if tk:
        return _agente_req("POST", f"https://{DOMINIO}.3c.plus/api/v1/agent/call/{id_chamada}/hangup", tk,
                           timeout=30)
    return _req("POST", f"/agent/call/{id_chamada}/hangup", timeout=30)


def ativas() -> list[dict]:
    """Chamadas em andamento na empresa (achatadas: a 3C agrupa por status)."""
    r = _req("GET", "/company/calls", timeout=15)
    d = r.get("data", r) if isinstance(r, dict) else r
    out: list[dict] = []

    def achatar(x, grupo=""):
        if isinstance(x, list):
            for i in x:
                achatar(i, grupo)
        elif isinstance(x, dict):
            if x.get("id") or x.get("number") or x.get("phone"):
                out.append({**x, "_grupo": grupo})
            else:
                for k, v in x.items():
                    achatar(v, str(k))
    achatar(d)
    return out


_QUAL: dict = {}


def qualificacoes_crm() -> list[dict]:
    if not _QUAL.get("lista"):
        d = _lista(_req("GET", f"/qualification_lists/{LISTA_QUALIFICACAO}/qualifications"))
        _QUAL["lista"] = [{"id": q["id"], "nome": q.get("name") or str(q["id"])}
                          for q in d if isinstance(q, dict) and q.get("id")]
    return _QUAL["lista"]


def baixar_chamada(id_chamada: str, id_qualificacao, nota: str, retorno: str | None, sessao: dict | None) -> dict:
    """Da baixa (qualifica) a chamada na 3C, como a atendente faria no discador."""
    tk = (sessao or {}).get("token")
    dados = {"qualification_id": str(id_qualificacao), "end_mode": "manual"}
    if nota:
        dados["qualification_note"] = nota[:500]
    if retorno:
        dados["date"] = retorno if len(retorno) > 16 else retorno + ":00"
    ultimo: Erro3C | None = None
    for caminho in (f"/agent/call/{id_chamada}/qualify", f"/agent/manual_call/{id_chamada}/qualify"):
        d = dict(dados)
        if "manual_call" in caminho:
            d = {k: v for k, v in d.items() if k in ("qualification_id", "qualification_note")}
        try:
            if tk:
                return _agente_req("POST", f"https://{DOMINIO}.3c.plus/api/v1{caminho}", tk, dados=d)
            return _req("POST", caminho, dados=d)
        except Erro3C as e:
            ultimo = e
            if "401" in str(e) or "token" in str(e).lower():
                break
    raise ultimo or Erro3C("a 3C nao aceitou a baixa.")


# ---------------------------------------------------------------- relatorio e gravacao

def chamadas(inicio: str, fim: str) -> list[dict]:
    p = {"start_date": inicio, "end_date": fim, "per_page": 100, "with_mailing": "true"}
    return _lista(_req("GET", "/calls", params=p))


def chamadas_periodo(inicio: str, fim: str, max_paginas: int = 50) -> list[dict]:
    """Todas as chamadas do periodo, pagina por pagina (o discador passa fácil de 100 por hora)."""
    out: list[dict] = []
    for pag in range(1, max_paginas + 1):
        p = {"start_date": inicio, "end_date": fim, "per_page": 100, "page": pag, "with_mailing": "true"}
        lote = _lista(_req("GET", "/calls", params=p, timeout=60))
        out += [c for c in lote if isinstance(c, dict) and c.get("id")]
        if len(lote) < 100:
            break
    return out


def chamada(id_chamada: str) -> dict | None:
    try:
        r = _req("GET", f"/calls/{id_chamada}", timeout=60)
    except Erro3C as e:
        if " 404" in str(e) or " 422" in str(e):
            return None
        raise
    d = r.get("data", r) if isinstance(r, dict) else None
    return d if isinstance(d, dict) and d.get("id") else None


def gravacao(id_chamada: str) -> tuple[bytes, str]:
    """O audio da chamada direto da 3C: online nao ha a copia em disco do painel local."""
    if not _TOKEN["v"]:
        raise Erro3C("falta o token da 3C (TRESC_TOKEN) nos Secrets do site.")
    try:
        r = requests.get(f"{BASE}/calls/{id_chamada}/recording",
                         headers={"Authorization": f"Bearer {_TOKEN['v']}"}, timeout=120)
    except requests.RequestException as e:
        raise Erro3C(f"nao consegui baixar a gravacao: {type(e).__name__}") from None
    tipo = r.headers.get("Content-Type", "")
    if r.status_code >= 400 or not r.content or "json" in tipo or "html" in tipo:
        raise Erro3C(f"gravacao indisponivel ({r.status_code})")
    return r.content, tipo.split(";")[0].strip() or "audio/mpeg"
