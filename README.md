# ProspeTraderBot — pesquisa e simulação de estratégias

Dashboard FastAPI para estudar sinais de EMA/RSI, Bollinger, rompimentos, confluência e Random Forest. O padrão é **PAPER**, com ordens virtuais. O modo **LIVE_MT5**, selecionado explicitamente no painel, envia ordens ao terminal Windows conectado, tanto em conta demo quanto real. Inclui cálculo de volume pelo risco até o SL, intenção persistida antes do envio e apuração por posições/negócios reais. A Safirion permanece em PAPER. Dados reais e artificiais são identificados separadamente.

Consulte o [manual MT5 e Telegram](docs/MT5_TELEGRAM.md) para habilitar o terminal, configurar o BotFather e vincular seu chat sem expor credenciais.

Score técnico mede confluência de regras; não é probabilidade de acerto. As estimativas da IA não são calibradas para lucro. Não há promessa de retorno ou acerto em todas as entradas.

## Executar

Python 3.10+ (validado com 3.12) e, para recompilar o painel, Node.js/npm.

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
npm ci --prefix frontend
npm run build --prefix frontend
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

No Windows, use `.venv\Scripts\python` nos comandos Python; o inicializador `iniciar_robo.bat` também inicia o servidor. A dependência nativa MetaTrader5 é instalada apenas no Windows. A ausência desse pacote no Linux é suportada pelo simulador. Os recursos compilados do painel estão em `frontend/vendor`, portanto o painel não exige CDNs nem Node em tempo de execução. Licenças dos recursos distribuídos acompanham esses arquivos. Lightweight Charts: [TradingView](https://www.tradingview.com/).

Use um único processo Uvicorn. A aplicação é local, sem autenticação multiusuário; não exponha sua porta diretamente à Internet. Use um único processo por diário e uma única instância do robô por conta; o diário possui bloqueio de instância. O uso real depende da validação operacional do terminal e da corretora, detalhada no manual MT5.

## O que foi revisado

- Indicadores causais, sem preenchimento de valores usando o futuro; RSI de uma sequência de alta não vira artificialmente neutro.
- Sinais e modelos usam apenas velas fechadas. O gráfico mantém a vela em formação para visualização.
- RF avaliado na porção final de uma janela cronológica, com separação de uma amostra na fronteira. A tela informa acurácia fora do treino, referência majoritária, acurácia balanceada, Brier e tamanho da validação. A IA precisa superar a referência e os critérios mínimos para liberar o filtro; isso ainda é evidência exploratória.
- Estimativas não são multiplicadas para parecer mais confiantes. Resultados de trades com outra expiração não viram rótulos artificiais de uma vela. Cache separado por mercado, ativo, timeframe e fonte.
- Nenhum histórico aleatório é criado ao conectar a Safirion. O sistema espera velas observadas, verifica o ativo, rejeita dados inválidos e não troca uma fonte real interrompida por um simulador silenciosamente. Depois de uma lacuna, usa somente o trecho contínuo mais recente.
- Todos os modos de dimensionamento respeitam o orçamento de risco. Martingale e aumento por sequência de vitórias estão desativados; os nomes legados SMART_AI/SOROS, aceitos pela API para compatibilidade, usam valor fixo limitado.
- Ordens manuais e automáticas compartilham limites de perda, saldo, intervalo e operação simultânea. A automação também bloqueia reutilização do mesmo candle e exige o score configurado. Ordem manual é uma escolha discricionária e não depende do filtro da IA.
- API valida parâmetros antes de alterar estado. Os controles são bloqueados enquanto há operação em curso; erros não são apresentados como sucesso.
- Interface responsiva, diagnóstico de dados/IA/risco, estado offline, navegação por teclado, modal com Escape e foco contido, redução de animações e tratamento seguro de texto de logs.

## Diagnóstico persistente e monitoramento contínuo — etapa 1

O monitoramento continua solicitado após erros recuperáveis, rejeições de pré-validação e limites de risco; novas entradas permanecem bloqueadas enquanto os critérios não forem satisfeitos. Pausa explícita pelo painel/Telegram continua válida, e o processo reinicia pausado. Não há reenvio automático de ordem incerta.

O painel inclui **Diagnóstico persistente de decisões**, consulta dos registros e exportação CSV por símbolo. Análises, filtros, tentativas, execução e recuperação ficam em `diagnostic_events` no mesmo SQLite do diário. O monitoramento e a consulta continuam separados da autorização financeira de uma ordem. Veja [estados, retentativas e consultas](docs/DIAGNOSTICO_RECUPERACAO.md).

A etapa 1 preservou o fluxo individual. As etapas seguintes acrescentam o **motor multipares opcional**, mantendo o modo individual como padrão e preservando filtros e risco.

## Radar e motor Forex multipares

O motor permite até duas posições confirmadas em símbolos diferentes (`forex_max_open_positions=2`, opção de limite 1). O percentual de risco passa a ser o orçamento global simultâneo, compartilhado, com até metade para cada nova posição e soma bruta de riscos até SL, sem crédito por hedge USD. Intenções incertas, posições manuais/externas e proteções não confirmadas continuam bloqueando novas entradas. Cooldown, limites diários e score 78% permanecem globais. O Radar e `/status` mostram vagas e risco agregado. O laboratório histórico também aceita até duas posições, com risco e margem compartilhados; resultados continuam exploratórios e não comprovam rentabilidade. Veja [regras e verificação em demo](docs/FOREX_MULTIPARES.md).

Seleção independente de EURUSD, USDJPY, GBPUSD e USDCHF, com códigos exatos da corretora e análise independente. Escolha RF com validação temporal ou [Continuidade Forex M1/M5 — opção 6](docs/FOREX_CONTINUIDADE.md), com score mínimo de 78/100. O score técnico da nova estratégia não é probabilidade de vitória e ela ainda não demonstrou superioridade financeira. Um coordenador serializa consultas/execução, permite até duas posições confirmadas em símbolos distintos e conserva o orçamento global compartilhado; intenção incerta bloqueia novas entradas. Falhas locais ficam isoladas; incerteza de exposição ou persistência bloqueia novas entradas sem desligar o monitoramento. O Radar é uma projeção em cache, sem decisões ou ordens disparadas por consultas da interface. Consulte [ativação em demo, estados, diagnóstico e limitações](docs/FOREX_MULTIPARES.md).

## Diário PAPER e limites

Padrões: banca virtual de R$ 1.000, entrada de R$ 5, risco máximo de 1% por entrada, três perdas consecutivas, vinte entradas por sessão e intervalo de sessenta segundos. São parâmetros de pesquisa, não uma recomendação de investimento. Configuração, operações e resultados são gravados em `data/paper.sqlite3`, ignorado pelo Git. Para testes isolados, defina `TRADER_JOURNAL_PATH` apontando a outro arquivo.

O dia da sessão é UTC. O histórico de dias anteriores permanece no diário. Reiniciar o processo não apaga perdas nem operações do dia. Ao reiniciar com uma operação PAPER binária ou baseada no simulador aberta, ela é marcada `INTERRUPTED` e sua reserva integral é debitada como hipótese conservadora; **isso não é uma perda confirmada de mercado**. Posições PAPER Forex/B3 com fonte MT5 real permanecem abertas e aguardam reconexão à mesma conta para retomar o monitoramento por preço, inclusive de um dia para outro. Não há botão para apagar o histórico e contornar limites. Faça backup do SQLite com o servidor parado.

O arquivo local padrão é `data/paper.sqlite3` (também guarda operações LIVE_MT5); `TRADER_JOURNAL_PATH` pode substituir esse caminho. A pasta `data/` não é versionada: atualizar o código com `git pull` na mesma instalação preserva o diário. Não apague essa pasta, não use `git clean -fdx` e não substitua o banco por um arquivo vazio. Ao mudar de computador/pasta, pare o servidor e copie o diário para o mesmo caminho configurado, preservando os arquivos auxiliares SQLite, se existirem. Guarde uma cópia da pasta `data/` fora do projeto antes de atualizar; ela pode conter configurações privadas e não deve ser publicada no Git.

Em **Últimas Operações → Histórico persistente — todos os dias**, consulte os 200 registros mais recentes ou exporte todos em JSON. A consulta inclui apenas o modo e, em LIVE_MT5, a conta/servidor selecionados; não mistura contas nem altera os limites diários. `GET /api/trades/history?limit=200&offset=0` permite paginação e `GET /api/trades/history.json` exporta os registros. O JSON facilita análise, mas não substitui o backup completo do SQLite. Operações pendentes permanecem salvas: após atualizar/reiniciar, a reconciliação busca seu resultado real no MT5, sem recriar ordens nem inventar ganhos/perdas.

Se uma consulta de histórico por ticket falhar, retornar `None` ou não encontrar negócios, a reconciliação tenta a busca por período. A alternativa exige a mesma correlação de símbolo, magic/tag/tickets; nunca presume que o ticket da ordem seja o identificador da posição. Falhas de consulta preservam o erro original da API quando disponível. Histórico da posição indisponível, incompleto ou ambíguo continua bloqueando novas entradas até confirmação real.

No PAPER binário, o payout é hipotético; empate devolve a entrada. **PAPER Forex/B3 usa SL/TP por preço, sem expiração**: dimensiona uma quantidade virtual pelo orçamento de risco, desconta o custo fixo configurado e encerra no preço observado ao cruzar SL/TP ou por ação manual. Gaps podem exceder o risco estimado. Essa quantidade não representa o lote negociável da corretora e não modela integralmente liquidez, slippage ou swaps. No modo LIVE_MT5, o lote é calculado pela especificação real do símbolo e os stops são enviados ao servidor.

O plano combina ATR e suporte/resistência de velas fechadas. Descarta entradas sem retorno/risco mínimo após custos ou que se afastaram do sinal. A meta diária pausa novas entradas; o teto de lucro projetado por operação limita o tamanho, sem elevar exposição após perdas. Metas em BRL exigem conversão MT5 válida quando a conta usa outra moeda. Consulte o [manual Forex e meta diária](docs/FOREX_META_DIARIA.md).

## Diagnóstico e backtest Forex

O Radar mostra os filtros, valores observados e limites sem alterar as decisões. O relatório comparativo conta candles distintos por par, validação, score, bloqueios e tentativas no escopo de conta e período selecionados. O novo laboratório Forex acompanha SL/TP por preço, spread, comissão, slippage, gaps e risco global, com modelos históricos separados e até duas posições em símbolos distintos. Inclui diagnóstico financeiro por horário/gatilho e excursões observadas para estudar SL/TP. Captura dados reais em modo somente leitura ou importa bases JSON; executa em segundo plano e exporta resultados e rejeições. Veja [uso, hipóteses e limites do backtest Forex](docs/FOREX_DIAGNOSTICO_BACKTEST.md).

## Laboratório CSV binário

Abra “Laboratório de estratégia”, selecione um CSV e avalie a estratégia salva. Exemplo de formato, com todas as velas fechadas:

```csv
time,open,high,low,close,volume
2026-01-01T00:00:00Z,1.1000,1.1004,1.0998,1.1002,100
2026-01-01T00:01:00Z,1.1002,1.1005,1.1000,1.1001,90
```

São necessárias 150 a 2.000 linhas, ordenadas, sem duplicatas nem lacunas no timeframe escolhido. `time` aceita segundos Unix na API; a interface também converte ISO. `volume` é opcional no CSV, com padrão zero; estratégias de volume não terão confirmação útil sem esse dado. Use ponto decimal. Arquivos com vírgula ou ponto e vírgula como delimitador são aceitos, sem campos CSV entre aspas.

O laboratório é **binário e de uma vela**, independentemente da plataforma de dados escolhida. Ele entra na abertura seguinte e avalia o fechamento dessa vela, usando somente o passado para cada sinal. Avalia até os últimos duzentos resultados possíveis. Apresenta PnL líquido, expectativa, drawdown, fator de lucro, ponto de equilíbrio e intervalo de Wilson de 95% do acerto entre resultados não empatados. Nenhuma entrada é um resultado válido quando não há critérios suficientes; zero operações não valida a estratégia.

A análise usa payout e custo fixos; não modela latência, gaps de execução ou a expiração real da corretora. O sistema não consegue verificar se o CSV fornecido contém dados reais. Repetir a seleção de parâmetros no mesmo período cria viés: confirme em outro período e em PAPER com dados reais. Não foi fornecido histórico de mercado para demonstrar precisão ou rentabilidade nesta revisão.

## Fontes reais e execução

A extensão `safirion-extension` pode fornecer ticks da aba autenticada. Escolha o mesmo ativo no painel e na corretora. O backend aguarda histórico observado suficiente e interrompe a análise se a cotação ficar velha. Heartbeats sem identidade de ativo não são preços. A extensão permanece uma integração experimental por DOM/WebSocket.

A execução MT5 é habilitada pelo modo LIVE_MT5, com a conta e servidor vinculados e terminal autorizado para a API Python. O backend não substitui um envio real que falhou por uma simulação. Pausar não fecha posições abertas; elas mantêm os stops no servidor e o acompanhamento por histórico, inclusive após reinício. A aplicação inicia a automação pausada. A extensão Safirion continua uma fonte experimental para PAPER.

O Telegram é opcional: `/status`, `/pausar` e alertas de ordens, resultados e falhas, restritos ao chat configurado. Use `python -m backend.telegram_setup` depois de criar um bot dedicado no BotFather. O token fica no `.env` local ignorado pelo Git. Veja o manual acima.

O painel e o `/status` distinguem falhas operacionais ativas de rejeições anteriores aguardando revalidação. Rejeições conhecidas de uma oportunidade (por exemplo, stop estrutural ou spread) aparecem em recuperação durante a espera; ao vencer o prazo, passam para `operational.pending_revalidation`. O incidente interno e seu histórico permanecem até uma revalidação bem-sucedida. O fim da espera nunca aprova uma ordem, reduz filtros ou remove bloqueios de conta, exposição, persistência ou SDK. `operational.faults` apresenta as falhas ativas, incluindo as locais de cada par. O `/status` separa a causa operacional do resultado dos sinais e informa o horário UTC de geração e a última atualização de cada par; uma mensagem atrasada no Telegram ou horários congelados não comprovam monitoramento atual.

A reconciliação MT5 procura primeiro os negócios pelo **ticket da ordem** (`history_deals_get(ticket=order_ticket)`), depois consulta a posição identificada. O argumento `ticket` dessa API filtra ordens, não o ticket individual do negócio. Na ausência de identificador, a busca cobre diferenças de relógio de até 14 horas, mantendo a correlação por magic/tag/tickets e recusando posições misturadas. Nunca reenvia uma ordem para resolver pendência. Os timestamps dos negócios são independentes de tick/velas: o ajuste opcional `mt5_deal_time_offset_minutes` fica vazio por padrão, para calibração pelo ticket de uma execução imediata e seu horário local de envio. Uma diferença incompatível, execução demorada ou sem confirmação de preenchimento imediato exige ajuste explícito (0=UTC; 180=subtrair três horas). Somente esse ajuste pode ser salvo com automação pausada e posição pendente; parâmetros de entrada/risco continuam bloqueados. Timestamps brutos são preservados em `time_raw`/`settled_at_raw`, junto da origem do ajuste. Registros antigos com evidência suficiente são normalizados uma única vez ao carregar o diário, sem alterar seu PnL; casos ambíguos preservam os dados e recebem `deal_time_warning`. Alertas de fechamento distinguem o horário UTC do fechamento e o horário em que o robô o confirmou.
Também não há calendário econômico integrado nem validação de múltiplos usuários. O filtro de notícias que existia apenas visualmente foi removido.

## Validar

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
npm run build --prefix frontend
```

Os testes cobrem separação temporal, ausência de dados futuros, RSI, estimativas da IA, validação OHLCV, cálculo de custos no backtest, limites de risco, concorrência, ausência de envio real em PAPER, envio/reconciliação MT5 com SDK controlado, notificações Telegram, empate, fonte desatualizada, reinício, persistência e rotas HTTP/WebSocket. Um teste de navegador reproduzível está em `tests/browser_smoke.py`.

Para verificar a interface Forex em um servidor isolado PAPER: `python tests/browser_forex.py http://127.0.0.1:8001`. Esse roteiro altera apenas a configuração do servidor de teste, abre e encerra posições virtuais e usa parâmetros permissivos para testar a interface; não valida a estratégia.

Se a API MT5 retornar timestamps de mercado fora de UTC, configure os ajustes explícitos e independentes de tick e velas em **Limites específicos do MT5**, após conferir os horários brutos no diagnóstico. O padrão é zero; consulte [o procedimento de normalização](docs/MT5_TELEGRAM.md#api-retorna-tick-cerca-de-três-horas-no-futuro).


O laboratório inclui o experimento **Retomada Forex — M1/M5**, com correção recente na EMA e retomada confirmada, independente do RF. Também permite testar proteção no preço de entrada e trailing ATR, com ajustes válidos na próxima vela e reserva inicial preservada. A avaliação inicial da nova entrada foi inferior à Continuidade nos recortes recebidos; ela permanece bloqueada para execução. Veja [regras, resultados e limites](docs/FOREX_RETOMADA_SAIDAS.md).
