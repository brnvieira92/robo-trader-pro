# Motor e Radar Forex multipares

O modo multipares é opcional. Configurações existentes continuam no modo individual até a ativação explícita. O motor monitora EURUSD, USDJPY, GBPUSD e USDCHF, cada um com seu símbolo exato e seleção independente. Não exige quatro pares ativos.

Também está disponível a [Continuidade Forex M1/M5](FOREX_CONTINUIDADE.md), independente do RF. Escolha a opção 6 no Setup, pausado e sem posições; a seleção não inicia a automação. Score mínimo 78/100 e toda a gestão global permanecem. As referências a validação ML abaixo se aplicam ao modo RF; no modo Continuidade, o Radar apresenta as regras técnicas e o contexto M5.

## Ativar na sua conta demo

1. Pause o robô. Aguarde qualquer posição, ordem ou intenção pendente ser reconciliada. Pausar impede entradas, mas não fecha posições.
2. No MT5, confira a especificação de cada símbolo. Negociação precisa permitir a direção desejada. Receber cotações não significa poder negociar. Na sua conta, o EURUSD sem sufixo estava desativado; EURUSD# tinha acesso completo. Confira os demais separadamente, sem presumir que usam o mesmo sufixo.
3. No aplicativo, mantenha MT5 Forex, modo MT5 e conta/servidor corretos. Escolha **IA Pura — Machine Learning** com filtro ML ativo, ou **Continuidade Forex** independente do RF, com score mínimo **78/100**. O timeframe do Setup é compartilhado pelos pares; a nova Continuidade exige **M1** e deriva seu contexto M5 de candles fechados. O timeframe do gráfico aberto no terminal não controla a API do robô.
4. Abra **Radar Forex → Configurar pares monitorados**. Marque apenas os pares desejados e informe os códigos exatos da corretora, por exemplo `EURUSD#`. Marque **Ativar motor multipares automático** e clique **Salvar seleção de pares**. Essa operação não inicia o robô nem diminui filtros. O símbolo do gráfico precisa estar na seleção; ao removê-lo, o formulário passa o foco ao primeiro selecionado.
5. Confirme no Radar os dados, permissões, validação ML e motivos de bloqueio. Clique **Iniciar MT5** para solicitar a execução automática. Não há confirmação individual por operação.

Para atualizar uma instalação local que acompanha a branch `work`, pause antes o servidor e as entradas, preserve seu `.env` e seu diário SQLite, e use `git pull origin work`. Se estiver em outra branch ou houver alterações locais, confira `git status` antes de integrar a branch. Reinicie o servidor com o Python da `.venv` e recarregue a página. A atualização não altera opções no terminal Windows.

## Decisões e orçamento

Cada par tem instância própria de Random Forest, cache, estado de análise, último candle operado e recuperação. O identificador do modelo inclui conta, servidor, par, timeframe, fonte, parâmetros e versão do conjunto de dados. Não há compartilhamento de treinamento entre pares. Continuam valendo a divisão cronológica, purga da fronteira, critérios de acurácia/referência, acurácia balanceada, Brier, direção ML, volatilidade e score mínimo.

As análises e chamadas ao SDK continuam sequenciais; as posições podem coexistir. O limite `forex_max_open_positions` é 2 por padrão no motor multipares, configurável para 1. Depois da varredura, o coordenador considera candidatos em rodízio, revalida os filtros e envia uma ordem por vez. Somente uma posição confirmada, correlacionada com o diário e protegida por SL/TP permite uma segunda posição em outro símbolo. Envio inconclusivo/pendente bloqueia novas entradas até reconciliação. Não há fila de sinais antigos, nem duas posições do robô no mesmo símbolo. O cooldown de 60 segundos configurado continua global, inclusive entre pares diferentes.

O orçamento não é multiplicado: a carteira inteira usa o menor valor entre patrimônio × percentual configurado, orçamento fixo (quando aplicável) e perda diária restante. Cada nova posição recebe no máximo metade desse teto com limite 2, sempre descontando o risco já reservado das demais. O teto de lucro por entrada e lote mínimo/passo podem reduzir mais o volume ou impedir a entrada. Posições antigas podem consumir mais da metade; o saldo global disponível continua obrigatório. As reservas SQLite são atômicas e a exposição é consultada novamente antes do envio. Posições externas/manuais, ordens pendentes, histórico/SL não confirmado e erros de consulta bloqueiam novas entradas. Perda/meta diárias, sequência de perdas, número de entradas e cooldown permanecem globais.

O Radar e o Telegram mostram o risco reservado/global e o risco até SL das posições compradas/vendidas em USD. São valores monetários de risco, não exposição nominal nem uma matriz estatística de correlação. Todos os riscos são somados sem crédito por hedge, inclusive em direções opostas. Gaps, slippage e custos podem superar estimativas. Meta diária significa pausar novas entradas ao atingir o PnL realizado líquido do dia UTC; não obriga operar e não fecha posições já abertas. Não foi alterado o valor da meta salvo pelo usuário.

Antes de enviar, o executor consulta a especificação real de cada símbolo: point, tamanho do tick, dígitos, stops mínimos, volume mínimo/máximo/passo, filling e permissão de direção. Lucro/perda e margem são calculados pelas funções da corretora. Não se presume que um lote ou pip de USDJPY equivale ao de EURUSD. O limite de spread continua expresso em **pontos MT5 do símbolo**, não pips.

O PnL e os limites do diário contabilizam as operações identificadas do robô; não substituem um controle financeiro completo de operações manuais externas. A presença de exposições externas bloqueia novas entradas, em vez de estimar um risco desconhecido.

## Recuperação e estados

- **DESATIVADO:** par não selecionado; não é analisado.
- **PAUSADO:** dados monitorados, sem autorização de executar.
- **BLOQUEADO:** um ou mais critérios impedem a entrada. Pode existir sinal direcional sem aprovação de risco/validação.
- **CANDIDATO:** passou a análise e será revalidado pelo coordenador. Não significa ordem enviada.
- **ERRO:** falha local de análise/especificação; os demais pares continuam. Retentativas usam espera de 5 a 60 segundos.
- **POSIÇÃO/INTENÇÃO:** aquele símbolo tem exposição. Outros símbolos podem operar se houver vaga, risco global e todos os filtros aprovados; intenção incerta bloqueia todos.

Falhas de conta, consulta da exposição, persistência e resultados de envio não confirmados bloqueiam novas entradas de todos os pares. O processo mantém monitoramento/reconciliação e recuperação. Alertas de erro e recuperação usam o Telegram configurado, com limitação de frequência. Falha no Telegram não autoriza operações nem encerra o motor. Pausa explícita continua válida; reiniciar o processo deixa a execução pausada e conserva intenções pendentes. Nenhum software pode garantir execução contínua se o Windows, terminal ou processo forem encerrados.

## Diagnóstico sem influenciar as operações

O Radar exibe somente projeções em cache do motor: sinal, confiança ML, score do sinal, validação, qualidade dos dados, especificação, bloqueio local/global e horário da análise. Atualizar a página ou consultar `GET /api/forex/radar` e `GET /api/state` no modo multipares não executa modelos, não chama o SDK e não gera ordens ou novos registros. Não há botões de compra/venda no Radar; as ordens manuais legadas ficam indisponíveis no modo multipares.

Os registros `ANALYSIS` preservam todos os filtros da análise; `RADAR` registra também disponibilidade/especificação e bloqueios do coordenador. `DECISION` registra pré-validação, plano, volume e rejeições; `EXECUTION` e `RECONCILIATION` registram intenção e resultado. As relações usam símbolo exato, timeframe, candle e `analysis_id`. Observações repetidas do mesmo evento podem ser agregadas; não representam trades adicionais. Use **Exportar diagnóstico deste símbolo** ou a API paginada:

```powershell
# O # do sufixo precisa ser codificado na URL.
$simbolo = [uri]::EscapeDataString('EURUSD#')
Invoke-RestMethod "http://127.0.0.1:8000/api/diagnostics/events?symbol=$simbolo&limit=100"
Invoke-RestMethod "http://127.0.0.1:8000/api/forex/radar" | ConvertTo-Json -Depth 12
```

Um período sem operações é compatível com validação reprovada, score abaixo de 78%, cotações antigas, spread, stops, retorno/risco, lote mínimo ou limites globais. Mais pares ampliam o conjunto analisado; não garantem sinais aprovados nem lucro. A validação direcional de uma vela continua sem demonstrar, por si só, rentabilidade de SL/TP Forex.

## Verificação em demo

Testes automatizados usam SDK falso e diários temporários. Eles cobrem isolamento, falha por par, bloqueios globais, reserva única, envio inconclusivo, reinício, especificações diferentes e consultas sem efeitos. A interface é verificada em desktop e celular. Isso não equivale a executar ordens no terminal Windows da XM.

Na demo, confira no MT5 símbolo, volume, SL/TP, correspondência com o diário e risco agregado das duas posições. Todos os pares continuam sendo analisados mesmo com as vagas ocupadas. Uma terceira entrada, repetição de símbolo, exposição desconhecida ou soma de riscos acima do teto deve ser bloqueada. Se houver falha após envio, aguarde reconciliação; não repita manualmente a ordem. Isso não garante uma frequência mínima de negócios: score 78%, validação ML e demais filtros foram preservados.

O laboratório histórico aceita **até duas posições** em símbolos distintos com risco/margem compartilhados e cooldown global. Bases antigas sem limite explícito mantêm uma posição. A simulação OHLC não reproduz toda a execução MT5 e continua exploratória; a validação financeira exige períodos independentes e acompanhamento em demo.

Para voltar ao modo individual, pause, aguarde a resolução de posições/intentos, desmarque o motor multipares e salve. Conta, parâmetros e diário são preservados.

### Reproduzir os testes no ambiente de desenvolvimento Linux

```bash
.venv/bin/python -m pytest -q
npm run build --prefix frontend
# Terminal separado: fixture temporária, API falsa, sem conexão ao terminal/Telegram.
PYTHONPATH=. .venv/bin/python tests/multi_browser_server.py
# Outro terminal:
.venv/bin/python tests/browser_multi.py http://127.0.0.1:8011
```

A fixture de navegador inicia pausada, cria e remove seu próprio diário temporário e impede `order_send`. Não use o teste de seleção de configuração contra sua instalação operacional.


O laboratório permite pesquisar `forex_pullback_retest` com risco multipares, mas essa estratégia experimental não pode ser selecionada para executar ordens. Consulte [Retomada e saídas de pesquisa](FOREX_RETOMADA_SAIDAS.md).


`forex_triple_atr` também está integrado ao motor multipares como experimento de conta demo, com o mesmo limite de duas posições e orçamento global. A conta é verificada antes da seleção e de cada nova preparação; monitoramento/reconciliação continuam ativos se houver bloqueio. Consulte [Triplo ATR](FOREX_TRIPLO_ATR.md).
