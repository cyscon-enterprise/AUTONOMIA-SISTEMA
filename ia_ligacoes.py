"""Ouve a gravacao de uma ligacao da 3C, transcreve e resume; o resumo vai para o historico do cliente.

- Transcricao: Whisper (faster-whisper) rodando no proprio servidor, em portugues. Gratis, mas leva
  1 a 2 minutos por ligacao na CPU. Modelo em WHISPER_MODELO: padrao "base", que cabe no Streamlit
  Cloud gratis (1 GB de memoria); "small" transcreve melhor mas precisa de mais memoria.
- Resumo: Claude (API da Anthropic), chave em ANTHROPIC_API_KEY (Secrets do site ou variavel do PC).

O resultado fica em `crm_ia_ligacoes` (uma linha por chamada da 3C): a mesma gravacao nunca e
transcrita nem paga duas vezes.
"""
from __future__ import annotations

import io
import os

import anthropic
import sqlalchemy as sa

import dados
import tresc3c as tresc

MODELO_CLAUDE = "claude-opus-5-5"
MODELO_WHISPER = os.environ.get("WHISPER_MODELO", "base")   # "base" cabe no servidor gratis (1 GB)
MIN_PALAVRAS = 12                     # menos que isso = so chamando, caixa postal ou silencio

DDL = """CREATE TABLE IF NOT EXISTS crm_ia_ligacoes (
    chamada_3c VARCHAR(40) PRIMARY KEY,
    transcricao TEXT,
    resumo TEXT,
    modelo VARCHAR(60),
    quem VARCHAR(60),
    criado_em TIMESTAMP NOT NULL)"""

SISTEMA = """Você analisa ligações de prospecção B2B da Cyscon, revenda TOTVS que vende ERP e sistemas de gestão \
para empresas. Recebe a transcrição automática de uma ligação (sem identificação de quem fala: deduza pelo \
contexto quem é o vendedor e quem é o cliente; a transcrição pode ter erros de reconhecimento).

Escreva em português do Brasil, curto e direto, só com o que a ligação mostra. Não invente: se algo não foi \
dito, escreva "não citado". Use exatamente este formato:

Resumo: <2 ou 3 frases sobre como foi a conversa>
Falou com: <nome e cargo, se citados>
Interesse: <alto | médio | baixo | nenhum> — <por quê>
Sistema atual: <ERP ou sistema que usam hoje, se citado>
Objeções: <o que o cliente levantou contra>
Próximo passo: <o que ficou combinado, com data/hora se houver>
Etapa sugerida: <uma destas: {etapas}>
Dados novos: <telefones, e-mails, nomes de decisores ou outros dados que apareceram>"""

_CLIENTE: dict = {}
_WHISPER: dict = {}


class ErroIA(RuntimeError):
    """Falha ao transcrever ou resumir. Nunca carrega chave de API na mensagem."""


def configurar(chave_anthropic: str) -> None:
    _CLIENTE["chave"] = (chave_anthropic or "").strip()
    _CLIENTE.pop("c", None)


def tem_chave() -> bool:
    return bool(_CLIENTE.get("chave") or os.environ.get("ANTHROPIC_API_KEY"))


def preparar() -> None:
    with dados.engine().begin() as conn:
        conn.execute(sa.text(DDL))


def _claude() -> anthropic.Anthropic:
    if "c" not in _CLIENTE:
        if not tem_chave():
            raise ErroIA("falta a chave da Anthropic (ANTHROPIC_API_KEY) para fazer o resumo.")
        _CLIENTE["c"] = anthropic.Anthropic(api_key=_CLIENTE.get("chave") or None)
    return _CLIENTE["c"]


def _whisper():
    if "m" not in _WHISPER:
        from faster_whisper import WhisperModel  # pesado: so carrega quando alguem pede a primeira transcricao
        _WHISPER["m"] = WhisperModel(MODELO_WHISPER, device="cpu", compute_type="int8")
    return _WHISPER["m"]


def _decodificar(audio: bytes):
    """Audio da 3C -> amostras mono 16 kHz float32. Feito aqui (e nao pelo faster-whisper) porque o
    decodificador dele quebra com o PyAV 19 (parametro `metadata_errors` removido)."""
    import av
    import numpy as np

    pedacos = []
    with av.open(io.BytesIO(audio), mode="r") as cont:
        conv = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for quadro in cont.decode(audio=0):
            for q in conv.resample(quadro):
                pedacos.append(q.to_ndarray().reshape(-1))
        for q in conv.resample(None):                     # esvazia o que ficou no conversor
            pedacos.append(q.to_ndarray().reshape(-1))
    if not pedacos:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(pedacos).astype(np.float32) / 32768.0


def transcrever(audio: bytes, tipo: str = "") -> str:
    amostras = _decodificar(audio)
    if not len(amostras):
        return ""
    partes, _ = _whisper().transcribe(amostras, language="pt", vad_filter=True, beam_size=5)
    return " ".join(p.text.strip() for p in partes).strip()


def resumir(transcricao: str, contexto: str = "") -> str:
    etapas = ", ".join(e["nome"] for e in dados.etapas())
    try:
        r = _claude().beta.messages.create(
            model=MODELO_CLAUDE,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium"},
            system=SISTEMA.format(etapas=etapas),
            messages=[{"role": "user", "content": (f"Empresa: {contexto}\n\n" if contexto else "")
                       + f"Transcrição da ligação:\n{transcricao}"}],
        )
    except anthropic.AuthenticationError:
        raise ErroIA("a Anthropic recusou a chave (ANTHROPIC_API_KEY). Ela está certa e ativa?") from None
    except anthropic.RateLimitError:
        raise ErroIA("limite de uso da Anthropic atingido; tente de novo em alguns minutos.") from None
    except anthropic.APIStatusError as e:
        raise ErroIA(f"a Anthropic respondeu {e.status_code}.") from None
    except anthropic.APIConnectionError:
        raise ErroIA("não consegui falar com a Anthropic (rede).") from None
    if r.stop_reason == "refusal":
        raise ErroIA("o Claude não quis resumir esta ligação.")
    return "\n".join(b.text for b in r.content if b.type == "text").strip()


def resultado(chamada_3c: str) -> dict | None:
    with dados.engine().connect() as conn:
        r = conn.execute(sa.text("SELECT * FROM crm_ia_ligacoes WHERE chamada_3c = :c"), {"c": chamada_3c}).mappings().first()
    return dict(r) if r else None


def processar(chamada_3c: str, contexto: str, quem: str) -> dict:
    """Gravacao da 3C -> transcricao -> resumo, guardado. Devolve {"transcricao", "resumo", "novo"}."""
    feito = resultado(chamada_3c)
    if feito:
        return {**feito, "novo": False}
    try:
        audio, tipo = tresc.gravacao(chamada_3c)
    except tresc.Erro3C as e:
        raise ErroIA(f"não consegui baixar a gravação: {e}") from None
    texto = transcrever(audio, tipo)
    if len(texto.split()) < MIN_PALAVRAS:
        resumo = "Gravação sem conversa (só chamando, caixa postal ou silêncio)."
    else:
        resumo = resumir(texto, contexto)
    with dados.engine().begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO crm_ia_ligacoes (chamada_3c, transcricao, resumo, modelo, quem, criado_em) "
            "VALUES (:c, :t, :r, :m, :q, :a) ON CONFLICT (chamada_3c) DO NOTHING"),
            {"c": chamada_3c, "t": texto, "r": resumo, "m": f"whisper-{MODELO_WHISPER} + {MODELO_CLAUDE}",
             "q": (quem or "")[:60], "a": dados.agora()})
    return {"transcricao": texto, "resumo": resumo, "novo": True}
