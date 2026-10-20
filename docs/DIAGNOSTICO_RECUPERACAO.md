# Etapa 1 — Diagnóstico e recuperação no fluxo de um par

Esta entrega prepara a expansão multipares com registros verificáveis. Continua analisando e executando somente o símbolo configurado. Não adiciona Radar ou seleção de vários ativos. Random Forest, validação, confiança mínima configurada (78% na configuração discutida), limites financeiros e regras de SL/TP permanecem exigidos.

## Monitorar, bloquear e pausar

O pedido de automação (`is_running` / `automation_requested`) é separado da possibilidade de abrir uma ordem:

| Estado | Comportamento |
| --- | --- |
| `MONITORING` | Serviço acompanha o mercado e verifica os filtros; não significa que exista ordem aprovada. |
| `RECOVERING` | Serviço continua ativo, com erro ou retentativa em acompanhamento. O motivo e o próximo horário estão disponíveis no diagnóstico. |
| `PAUSED` | Usuário pausou ou o processo acabou de iniciar. Posições existentes continuam em acompanhamento. A recuperação não reativa a automação sozinha. |

**Iniciar MT5** solicita monitoramento automático. Pode ser ativado mesmo quando um limite impede entradas; não autoriza uma ordem fora dos limites. O botão muda para **Pausar MT5** enquanto esse pedido estiver ativo. Mudança de parâmetros continua exigindo pausa e ausência de operação pendente.

O serviço continua verificando mesmo quando há stop diário, meta atingida, limite de entradas ou perdas consecutivas. Esses bloqueios não foram removidos. Na mudança da sessão UTC, os limites diários são reavaliados; se o pedido de automação continuar ativo, os demais critérios voltarem a passar e não houver exposição pendente, novas entradas podem voltar automaticamente. Isso não altera o orçamento para recuperar perdas.

## Tratamento de falhas

- Pré-validação recusada: registra o motivo e aguarda antes de outra tentativa. Intervalos: 5, 10, 20, 40 e até 60 segundos. O sinal e todas as condições são novamente avaliados; uma tentativa não é uma ordem repetida.
- Histórico/erro de análise: mantém um estado sem sinal operável, bloqueia entradas e tenta uma nova consulta/avaliação com espera progressiva. O painel recebe um estado de recuperação, sem preço fictício ou reutilização do sinal antigo.
- Falha de modelo: registra `MODEL_UNAVAILABLE`; não desativa a automação e não aprova um sinal sem a validação exigida.
- Rejeição explícita pela corretora: registra código e motivo, mantém cooldown e proteção de reutilização de candle. Não cria um laço de reenvio da ordem rejeitada.
- Resposta incerta depois do envio: mantém a intenção/posição bloqueando o canal e segue a reconciliação. **Não repete `order_send`.** Ausência de confirmação pode exigir intervenção no terminal.
- SL/TP não confirmados: alerta e mantém acompanhamento, sem liberar outra entrada nem inventar encerramento.
- Falha ao gravar operação: mantém o estado pendente em memória e tenta persistir antes de liberar novas entradas. A intenção continua reservada. Se a confirmação da reserva no banco for incerta, relê o diário antes de qualquer nova tentativa.
- Falha no diário de diagnóstico: bloqueia novas entradas, mantém até 1.000 eventos pendentes em memória e tenta gravá-los novamente. O campo `dropped` informa se esse limite de memória foi excedido. Os registros pendentes em memória não sobrevivem a encerramento do processo.
- Telegram indisponível: não interrompe análise ou reconciliação. A fila persistida mantém as regras de entrega existentes; falhas ao enfileirar deixam uma fila temporária limitada a 100 mensagens para nova tentativa enquanto o processo estiver aberto.

As chamadas assíncronas de pré-validação, envio e reconciliação têm alerta de demora a cada período de 15 segundos. A chamada original continua sendo acompanhada; o sistema não inicia uma segunda chamada nativa sobreposta para “resolver” o timeout. O terminal precisa voltar a responder para essa operação prosseguir com segurança.

Falhas são notificadas no Telegram, quando configurado, com controle de repetição de cinco minutos por componente/código. Recuperações também têm controle de repetição. Insuficiência normal de sinal ou score não gera uma mensagem por segundo: fica no diagnóstico persistente. `/status` agora inclui o estado de monitoramento e o motivo atual; `/pausar` continua tendo prioridade.

O serviço depende do processo Python, Windows, disco e terminal. Fechar o programa, desligar o computador, falha de energia ou travamento externo não permite garantir disponibilidade permanente. Ao reiniciar, a automação continua pausada; intenções existentes são recuperadas. A continuidade aqui significa recuperação de erros durante a execução, preservando os bloqueios financeiros.

## Registros no painel e SQLite

No Dashboard, abra **Diagnóstico persistente de decisões**, clique em **Atualizar registros** ou **Exportar CSV do par**. A consulta não envia ordens. O cabeçalho informa monitoramento, recuperação e bloqueios; o contador lateral passa a representar eventos de diagnóstico, não supostas análises inferidas de operações fechadas.

A tabela `diagnostic_events` é criada no SQLite existente (`data/paper.sqlite3` por padrão). Operações e configurações existentes não são apagadas. Os eventos incluem:

- `ANALYSIS`: candle, sinal antes/depois dos filtros, direção estimada pelo modelo, métricas, versões de dados/modelo/configuração, planos indicativos e gates.
- `DECISION`: pré-validação aprovada/rejeitada, tentativa e análise de origem.
- `EXECUTION`: intenção/operação, resultado do envio e código da corretora.
- `RECONCILIATION`: mudanças confirmadas de estado da operação.
- `ERROR` / `RECOVERY`: incidente, componente, repetição, espera e recuperação.
- `RISK` / `CONFIG`: bloqueio global e versões de configuração.

Gates usam `PASS`, `FAIL` e `NOT_EVALUATED`. Uma regra nativa ainda não consultada não aparece como aprovada. A sinalização é explicativa e não substitui a validação efetiva antes do envio.

`analysis_id` associa uma análise ao contexto do candle/modelo/configuração; `attempt_id` associa a tentativa à execução, e `trade_id` identifica a operação. Uma análise pode apresentar múltiplos estados intrabar, como tick recente e depois antigo. Leituras consecutivas idênticas do painel não geram novas linhas. Estados repetidos que voltam após uma transição são agregados, com `observations`, primeiro e último horário.

Horários de observação são UTC. `bar_time_source` preserva o horário representado pela fonte; `bar_time_basis` informa UTC para dados MT5 normalizados e horário local da fonte para outras fontes. Não confunda estatísticas globais de risco com uma avaliação individual de candle.

A seleção de amostra/modelo e a decisão não são alteradas pelo registro. O ajuste na mensagem de estimativa direcional inferior a 60% explica o gate real, em vez de informar ambiguamente “validação favorável”. As métricas exatas de aprovação acompanham os valores arredondados exibidos no painel.

Os registros persistentes não são apagados automaticamente nesta etapa. Exporte e acompanhe o tamanho do diário; falha de armazenamento impede novas entradas e é reportada. Use backup SQLite consistente para preservar operações e diagnóstico antes de manutenção.

## Consultas locais

- `GET /api/state`: snapshot e campo `operational`, com pedido de automação, falhas, auditoria e filas pendentes.
- `GET /api/diagnostics/events?symbol=EURUSD%23&limit=100`: eventos de `EURUSD#`.
- `GET /api/diagnostics/events?kind=ERROR&limit=100`: incidentes persistidos.
- `GET /api/diagnostics/summary`: quantidade por símbolo, tipo e código.
- `GET /api/diagnostics/export.csv?symbol=EURUSD%23`: exportação com BOM UTF-8 para facilitar a leitura no Excel.

Eventos aceitam `limit` de 1 a 500 e cursor `before` com o ID do último item da página. `next_before` indica continuação. CSV aceita até 5.000 linhas por solicitação; o cabeçalho `X-Next-Before` permite buscar a próxima página. Símbolos com `#` devem ser codificados como `%23` nas URLs; o painel faz isso automaticamente.

Exemplo PowerShell, sem envio de ordens:

```powershell
$s = Invoke-RestMethod 'http://127.0.0.1:8000/api/state'
$s.operational | ConvertTo-Json -Depth 6
Invoke-RestMethod 'http://127.0.0.1:8000/api/diagnostics/events?symbol=EURUSD%23&kind=ERROR&limit=20' | ConvertTo-Json -Depth 8
```

## Verificação

Os testes controlam SDK e Telegram: rejeição transitória e retomada, pausas explícitas, limites diários, intenção incerta, persistência após envio, falha do modelo, recuperação dos dados, alertas limitados, watchdog sem reenvio, consulta/exportação e preservação dos critérios anteriores. Não enviam ordens reais. O roteiro `tests/browser_diagnostics.py` verifica desktop/celular e confirma que consultar o painel de diagnóstico não faz POST de negociação.

A integração com seu MT5 Windows e a entrega ao seu Telegram precisam de validação local. Esta etapa não habilita os quatro pares; as próximas etapas dependem de autorização própria.
