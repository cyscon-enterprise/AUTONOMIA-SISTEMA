"""Resumo automatico das ligacoes: sem botao, em segundo plano.

A sincronizacao do discador (`vendedores.sincronizar`) chama `agendar` para toda conversa gravada de
60 s ou mais. Uma thread do servidor baixa a gravacao da 3C, transcreve (Whisper), resume (Claude) e
grava o resumo no historico do lead. O vendedor segue ligando enquanto isso.

Cada chamada e processada uma vez so (`crm_ia_ligacoes` guarda o resultado); falha tenta de novo nas
proximas sincronizacoes, ate TENTATIVAS vezes.
"""
from __future__ import annotations

import threading
import time

import ia_ligacoes

ESPERA_GRAVACAO_S = 20       # a 3C leva alguns segundos para liberar o audio depois do fim da chamada
TENTATIVAS = 3

_trava = threading.Lock()
_rodando: set[str] = set()
_falhas: dict[str, int] = {}


def agendar(chamada_3c: str, cnpj: str, contexto: str, quem: str, titulo: str, salvar) -> bool:
    """Poe a chamada na fila. `salvar(cnpj, texto)` grava o resumo no historico. True se agendou agora."""
    if not ia_ligacoes.tem_chave():
        return False
    with _trava:
        if chamada_3c in _rodando or _falhas.get(chamada_3c, 0) >= TENTATIVAS:
            return False
        if ia_ligacoes.resultado(chamada_3c):
            return False
        _rodando.add(chamada_3c)
    threading.Thread(target=_trabalhar, args=(chamada_3c, cnpj, contexto, quem, titulo, salvar),
                     daemon=True, name=f"ia-{chamada_3c[:8]}").start()
    return True


def _trabalhar(chamada_3c, cnpj, contexto, quem, titulo, salvar) -> None:
    try:
        time.sleep(ESPERA_GRAVACAO_S)
        r = ia_ligacoes.processar(chamada_3c, contexto, quem)
        if r.get("novo") and not (r.get("resumo") or "").startswith("Gravação sem conversa"):
            salvar(cnpj, f"{titulo}\n{r['resumo']}")
    except Exception as e:                          # thread: registra e tenta de novo na proxima leitura
        with _trava:
            _falhas[chamada_3c] = _falhas.get(chamada_3c, 0) + 1
        print(f"[ia_auto] {chamada_3c}: {type(e).__name__}: {str(e)[:200]}", flush=True)
    finally:
        with _trava:
            _rodando.discard(chamada_3c)
