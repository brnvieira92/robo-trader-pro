# Diagnósticos, comparação por par e backtest Forex

Estas melhorias não alteram os filtros, a confiança mínima de 78%, o treinamento Random Forest nem a execução automática. O laboratório é uma avaliação separada; não envia ordens nem aplica ajustes à estratégia.

## 1. Entender todos os bloqueios

No Radar, abra **Ver filtros e valores** no card do par. O painel mantém esse detalhe aberto durante as atualizações. **Todos os filtros do ativo do gráfico** apresenta o mesmo tipo de detalhamento para o símbolo principal.

Cada critério informa estado, valor observado, comparação, limite e, quando necessário, explicação:

- **APROVADO:** o critério foi satisfeito naquela análise.
- **REPROVADO:** o critério impede a oportunidade, ou é uma consequência de outro filtro que a rejeitou.
- **NÃO AVALIADO:** ainda não foi possível ou necessário verificar. Não significa aprovado.
- **Informativo:** ajuda a interpretar os dados, sem substituir os critérios de autorização.

A estimativa ML e o score final são diferentes. Um modelo pode estimar 80% em uma direção e continuar sem autorização, por exemplo, por falhar na validação temporal. O sinal pode ficar NEUTRAL com score zero. O detalhe mostra ambos, sem reinterpretar a estimativa como chance de lucro.

Acurácia precisa superar a referência; acurácia balanceada precisa atingir 55%; Brier deve ser menor que 0,25. Os indicadores exibidos são arredondados, mas os resultados desses critérios continuam usando as comparações exatas calculadas pelo modelo. Também são expostas as quantidades de amostras e classes de treinamento/validação quando disponíveis.

Spread, volume, margem e viabilidade final dos stops são revalidados na tentativa de execução. Antes disso, alguns aparecem como **não avaliados**, com os limites conhecidos. Os planos BUY/SELL existentes são prévias. Consulte os eventos **DECISION** para a aprovação/rejeição efetiva, com mensagem, plano e orçamento disponíveis. O detalhe do Radar inclui também permissão do símbolo, exposição global e espera de recuperação local.

## 2. Comparar os pares

Abra **Relatório comparativo por par**, escolha o período e clique **Comparar pares**. Os campos usam o horário do seu navegador. Fim vazio significa agora. O backend converte o período em instantes UTC e filtra pela última observação persistida do evento (`last_seen`).

O relatório mostra, por símbolo e timeframe:

- Candles analisados distintos; candles com validação ML aprovada e score final suficiente.
- Sinais qualificados por validação, direção, score e volatilidade.
- Candidatos à pré-validação: nenhum filtro conhecido reprovado, com automação solicitada. Ainda podem ser rejeitados no MT5.
- Tentativas de pré-validação aprovadas/rejeitadas e motivos das rejeições.
- Intenções registradas e seus estados; isso não comprova preenchimento ou lucro.
- Bloqueios por critério, fontes dos dados, erros e rejeições durante revalidação, no JSON completo.

**A unidade de oportunidade é um candle por símbolo/timeframe.** O relatório usa a última projeção conhecida desse candle nos registros selecionados. Vinte polls do painel não se tornam vinte oportunidades. Se o estado mudar dentro do candle, a projeção mais recente prevalece. Vários filtros podem reprovar o mesmo candle: não some suas contagens como se fossem oportunidades distintas. Uma estimativa ML informativa reprovada não vira bloqueio obrigatório de outra estratégia.

O modo é o modo atual da aplicação, e LIVE é limitado à conta/servidor vinculados. Os dados de PAPER continuam identificados, inclusive fontes simuladas. Registros antigos sem todos os critérios têm cobertura parcial. Eventos agregados atualizados depois do fim consultado não permitem reconstruir seu estado exato no passado; esse limite é informado no resultado. Para comparação reproduzível, baixe o relatório JSON com período e escopo. O SQLite usa WAL para que a leitura de relatórios não bloqueie as gravações do diário. Para copiar o banco como backup, encerre o servidor antes da cópia; não copie apenas o arquivo principal enquanto houver gravações ou arquivos `-wal`/`-shm` ativos.

API de consulta, sem chamadas ao SDK ou decisões de negociação:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/diagnostics/pairs?mode=LIVE_MT5' | ConvertTo-Json -Depth 12
```

Os parâmetros opcionais `start` e `end` são segundos Unix. LIVE exige conta vinculada para evitar mistura de contas.

## 3. Executar o backtest Forex

1. Pause o robô e aguarde a resolução das posições/intentos. Mantenha sua configuração Forex, Random Forest, filtro ML e score mínimo de 78% ou superior.
2. Abra **Backtest Forex — SL/TP e risco global**.
3. Clique **Carregar histórico real do MT5** no computador com o terminal conectado. O aplicativo consulta até 1.000 candles por par selecionado, exclui a vela em formação e aplica o ajuste de timestamp das velas já configurado. Não cria preços substitutos se a consulta falhar.
4. Confira as hipóteses: saldo inicial, comissão por lote **por lado**, slippage adverso em pontos MT5 **por preenchimento**, custo de swap por lote por dia UTC e, se a meta for em reais com conta em outra moeda, taxa assumida na moeda da conta por BRL. Por exemplo, 0,20 USD por BRL converte uma meta de R$ 150 em US$ 30.
5. Revise e confirme a caixa das hipóteses. Valores de custo zero significam custo assumido zero, não tarifa consultada na corretora.
6. Escolha até 200 candles de avaliação; o padrão é 100. O histórico anterior é usado para treinamento, respeitando a janela de até **239 candles fechados**, como o fluxo atual que recebe 240 e exclui a vela em formação.
7. Clique **Executar backtest Forex** e acompanhe o progresso. Iniciar o robô, mudar sua configuração ou enviar ordens manuais fica bloqueado durante o laboratório. O motor permanece pausado; não retoma a execução automaticamente ao concluir.
8. Exporte resultado JSON, operações CSV e análises/rejeições CSV. Baixe também a **base JSON** para preservar os candles usados. O último resultado concluído fica salvo no SQLite e pode ser recuperado após reiniciar a aplicação. Se a consulta do navegador falhar, o estudo no servidor pode continuar; aguarde e recupere o último resultado quando concluir.

A captura pelo MT5 consulta as especificações próprias de cada símbolo, incluindo valores de tick de ganho/perda em moeda da conta, tamanho do tick, point, dígitos, volumes, stops, permissão e margem estimada por lote. Não presume equivalência monetária entre EURUSD e USDJPY. Falta de histórico, spread, valor de tick ou margem válidos impede o carregamento.

Também é possível importar uma base JSON com 150 a 2.000 candles por símbolo, usando uma base baixada como referência. São aceitos de um a quatro símbolos, correspondendo exatamente aos selecionados na configuração. Cada candle precisa de `time` Unix UTC, OHLC Bid, `volume` e `spread_points`. Cada mercado precisa de `specification`, `source` e `specification_basis`. Só são aceitas velas fechadas em ordem, sem duplicatas ou timestamps futuros. Lacunas são diagnosticadas, e o treinamento descarta o prefixo anterior à última lacuna. Se faltar uma vela de um par com posição aberta enquanto a linha do tempo dos outros pares continua, a avaliação falha com mensagem explícita: não inventa o caminho de SL/TP através desse trecho ausente. A origem declarada de um arquivo importado não é autenticação da sua procedência.

## O que o simulador calcula

Cada par recebe uma instância própria do modelo. A cada abertura histórica, somente candles anteriores são fornecidos ao modelo. O Random Forest continua prevendo a direção de uma vela; seu alvo de treinamento não foi trocado por lucro de trade. A diferença é que o resultado da operação passa a ser acompanhado por SL/TP e custos, em vez de usar a cor do candle como resultado financeiro.

O simulador reutiliza o planejamento/dimensionamento de produção por um adaptador histórico sem SDK nativo. Considera orçamento de entrada, risco percentual, perda e meta diárias, sequência de perdas, máximo de entradas, cooldown, uma posição global, permissão direcional, spread, volume/passo/teto, margem e retorno/risco planejado. Os pares elegíveis são considerados em rodízio, com o mesmo princípio do coordenador. Uma rejeição local de plano/especificação permite tentar outro par; uma posição aberta impede todos os demais.

A preparação considera a reserva de custos configurada em `mt5_cost_buffer`. Os custos históricos informados são depois cobrados nos preenchimentos; não são usados para alterar silenciosamente essa reserva. Assim, custos e slippage efetivos podem tornar uma operação pior que o risco/retorno estimado, como também pode ocorrer no ambiente real.

Regras conservadoras de execução:

- Compra entra no Ask estimado e encerra no Bid; venda entra no Bid e encerra no Ask estimado. Ask é aproximado por OHLC Bid mais o spread daquele candle.
- Se SL e TP forem atingidos na mesma vela sem ordem intravela conhecida, considera SL primeiro e registra `ambiguous=true`.
- Gap que ultrapassa o stop executa na abertura adversa, acrescida do slippage. Gap favorável ao TP usa o alvo, sem crédito adicional pelo gap.
- Não existe expiração. Uma posição aberta no fim permanece aberta, com PnL flutuante separado do realizado.
- Saídas intravela são contabilizadas no fechamento do candle, iniciando o cooldown nesse instante. Não há reentrada na mesma vela. Isso pode produzir menos entradas que a execução com ticks reais.
- Comissão é cobrada em cada lado. Swap é uma aproximação por mudança de dia UTC, não o calendário de rollover/triplo da XM.

O resultado inclui PnL realizado, PnL líquido de operações encerradas, saldo/equity final, drawdown amostrado em fechamento de candle, profit factor, expectativa, curva de equity, operações, ambiguidades, estado da posição aberta e comparação por par. O realizado pode ser negativo com posição ainda aberta por causa da comissão de entrada/swap já debitados. Métricas de operações encerradas são calculadas com seus custos completos.

Um teste sem trades retorna **NO_TRADES / SEM OPERAÇÕES**, sem profit factor ou expectativa inventados. A meta pode ficar sem ser alcançada exatamente: lote mínimo, arredondamento do volume e teto de risco podem impedir uma nova entrada quando resta um valor pequeno.

## Limites de interpretação

Este é um backtest OHLC exploratório. Não possui sequência de ticks, spread intravela completo, requotes, fila, latência, preenchimentos parciais ou `order_check` histórico. As permissões e valores monetários importados são snapshots atuais assumidos constantes; não reconstroem a conversão histórica de JPY/CHF, a margem variável ou as regras antigas da corretora. Não reconstitui ordens manuais externas. Idade de ticks e permissões históricas de conta são explicitamente não avaliadas, não marcadas como aprovadas.

O drawdown não captura toda a excursão adversa dentro da vela. Custos, gaps e a aproximação dos valores monetários podem exceder o risco estimado. O período comparável é limitado à sobreposição dos históricos dos pares. O laboratório não otimiza parâmetros nem seleciona automaticamente o melhor par. Repetir ajustes no mesmo conjunto cria viés de seleção: reserve outro período para confirmação e, depois, valide na conta demo.

O laboratório binário anterior continua disponível e identificado como avaliação de uma vela. Seus resultados não devem ser confundidos com o backtest Forex.

## APIs do laboratório

- `POST /api/research/forex/history?count=1000`: captura somente leitura; exige pausa e SDK real disponível.
- `POST /api/research/forex/jobs`: inicia avaliação validada e retorna HTTP 202 com `id`.
- `GET /api/research/forex/jobs/{id}`: progresso ou resultado.
- `GET /api/research/forex/latest`: último resultado persistido.
- `GET /api/research/forex/jobs/{id}/export?kind=json|trades|analyses`: exportação.

Uma única avaliação é executada por vez. Falha de avaliação libera o bloqueio do laboratório e apresenta o erro; não muda parâmetros, não inicia o robô e não interfere no diário financeiro de trades. O resultado do estudo é salvo em uma chave separada do SQLite, sem virar ordem ou PnL da conta.

## Testes reproduzíveis no ambiente Linux

```bash
.venv/bin/python -m pytest -q
npm run build --prefix frontend
# Terminal 1, SDK falso e diário temporário, sem conexão a corretora/Telegram:
PYTHONPATH=. TRADER_TEST_PORT=8021 .venv/bin/python tests/multi_browser_server.py
# Terminal 2:
.venv/bin/python tests/browser_forex_research.py http://127.0.0.1:8021
```

Os testes de execução usam SDK simulado ou o adaptador histórico, nunca uma ordem na XM. A confirmação de carregamento com o SDK nativo continua dependendo do terminal Windows.
