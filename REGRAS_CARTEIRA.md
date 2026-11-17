# Regulamento operacional da carteira — AutonomIA Vendas (v1.1, proposta de 17/11/2026)

Ainda não é cláusula contratual: depois de validado, harmonizar com contrato, Termos de Uso e política de comissão.

## Definições
- **Tentativa válida**: ligação realizada (automática ou manual), atendida ou não, inclusive atendida só pela
  recepção/secretaria. Fica no histórico, mas NÃO é contato efetivo.
- **Contato efetivo**: conversa com alguém do setor responsável pela oportunidade (não a recepção). [ponto 4 em aberto]
- **E-mail válido**: e-mail enviado ao cliente pela plataforma, registrado no histórico.
- **Agendamento válido**: data futura informada numa ligação atendida (pelo responsável ou pela secretaria), com
  data, motivo, quem informou e justificativa. Agendamento escolhido só pelo vendedor não vale.

## Entrada
- Discador distribui as empresas aleatoriamente entre os vendedores disponíveis; o vendedor também pode inserir
  a própria lista. Empresa atribuída = carteira dele. Um vendedor por empresa; os outros não trabalham o lead reservado.

## Trilha sem contato efetivo
| Fase | Prazo | Exigência | Se não cumprir |
|---|---|---|---|
| 1 | 7 dias da entrada | tentativa válida | (a definir: alerta 2 + 3 dias, ou perda) |
| 2 | +7 dias | contato efetivo ou agendamento válido | ALERTA 1 "Contato Não Efetivo — 7 dias corridos" (pop-up + e-mail) |
| 3 | +7 dias | contato efetivo ou e-mail válido | (a definir) |
| 4 | +7 dias após o e-mail | resposta do e-mail ou contato com o setor | ALERTA 2 "Contato Não Efetivo — 3 dias corridos" (pop-up + e-mail) |
| 5 | +3 dias | contato efetivo ou resposta válida | Empresas Perdidas |

Conseguiu contato efetivo/resposta em qualquer fase → segue acompanhamento normal.

## Agendamento
- Suspende os prazos até a data combinada; sem limite máximo quando justificado.
- Depois da data: prazo para o retorno [ponto 5 em aberto].

## Empresas Perdidas e contestação
- Histórico completo: entrada, tentativas, contatos, e-mails, agendamentos, alertas (plataforma e e-mail),
  data da perda, contestação e resultado.
- 3 dias corridos para contestar; empresa bloqueada nesse período.
- Aceita → volta ao vendedor. Rejeitada ou sem contestação → livre para nova atribuição.

## Histórico e transparência
- Tudo registrado automaticamente; o vendedor vê os prazos de cada empresa; o admin audita e julga contestações.

## Decisões aprovadas (17/11/2026)
1. **Tentativas mínimas por fase**: menor valor entre 3 e o número de telefones (3 = em dias diferentes).
   Só contam chamadas iniciadas pelo discador ou pela plataforma, registradas na 3C. Não precisa ser atendida.
2. **E-mail**: enviado pela ficha. Guarda remetente, destinatário, data/hora, assunto, texto integral, vendedor e
   comprovante técnico (Message-ID + resposta do servidor), em `crm_emails_enviados`. Cópia oculta na caixa da
   plataforma. Comprova o envio, não a leitura. Resposta colada pelo vendedor = registro declarado (admin confere).
   Futuro: ler a caixa de entrada para detectar respostas sozinho.
3. **Agendamento**: vendedor informa data, quem informou e motivo; vínculo com a gravação; IA procura a evidência.
   Achou → validado. Não achou/dúvida → A CONFERIR para o admin. A IA nunca exclui agendamento sozinha.
4. **Contato efetivo**: conversa com qualquer pessoa do setor responsável (não precisa ser o decisor).
   Recepção/secretária que só informa indisponibilidade não conta. Vendedor classifica, IA verifica, admin revisa.
5. **Retorno agendado**: 3 dias corridos após a data combinada (dia 20 → até dia 23). Não ligou → Alerta 2 (+3 dias).
   Registrar data combinada e data efetiva da ligação.
6. **Depois do contato efetivo**: 15 dias para o próximo; alertas nos dias 10, 14 e 15.
7. **Fases 1 e 3 sem ação**: vai direto para o Alerta 2 (últimos 3 dias).
