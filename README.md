# ProspeTraderBot — pesquisa e simulação de estratégias

Dashboard FastAPI para estudar sinais de EMA/RSI, Bollinger, rompimentos, confluência e Random Forest. O modo de execução desta revisão é **PAPER**: nenhuma ordem do painel é enviada à corretora, mesmo com uma ponte Safirion ou terminal MT5 conectado. Dados reais e dados artificiais são identificados separadamente.

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

Use um único processo Uvicorn. A aplicação é local, sem autenticação multiusuário; não exponha sua porta diretamente à Internet. Uma instância equivale a uma sessão de pesquisa, não a um serviço de execução financeira de produção.

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

## Diário PAPER e limites

Padrões: banca virtual de R$ 1.000, entrada de R$ 5, risco máximo de 1% por entrada, três perdas consecutivas, vinte entradas por sessão e intervalo de sessenta segundos. São parâmetros de pesquisa, não uma recomendação de investimento. Configuração, operações e resultados são gravados em `data/paper.sqlite3`, ignorado pelo Git. Para testes isolados, defina `TRADER_JOURNAL_PATH` apontando a outro arquivo.

O dia da sessão é UTC. O histórico de dias anteriores permanece no diário. Reiniciar o processo não apaga perdas nem operações do dia. Ao reiniciar com uma operação PAPER aberta, ela é marcada `INTERRUPTED` e sua reserva integral é debitada como hipótese conservadora; **isso não é uma perda confirmada de mercado**. Não há botão para apagar o histórico e contornar limites. Faça backup do SQLite com o servidor parado.

No PAPER binário, o payout é um parâmetro hipotético, sem alegação de valor real da Safirion; empate devolve a entrada. Em dados de MT5, o PAPER calcula retorno não alavancado sobre o valor nominal até o encerramento, **não lotes, contratos ou execução de stops**. Custos e slippage não entram no painel PAPER; podem ser modelados como custo fixo no laboratório binário. Não confunda esses resultados com saldo ou extrato de corretora.

## Laboratório CSV

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

A execução real pelo painel está bloqueada porque o fluxo anterior tratava envio de comando como preenchimento e estimava resultados financeiros sem confirmação. Para reativá-la é necessário implementar e testar confirmação de preenchimentos, identificadores de conta/ativo/ordem, resultados correlacionados por ordem, reconciliação após falhas, custos, limites específicos do instrumento e persistência de ordens pendentes. Não basta trocar um rótulo de conta na interface.

Também não há calendário econômico integrado nem validação de múltiplos usuários. O filtro de notícias que existia apenas visualmente foi removido.

## Validar

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
npm run build --prefix frontend
```

Os testes cobrem separação temporal, ausência de dados futuros, RSI, estimativas da IA, validação OHLCV, cálculo de custos no backtest, limites de risco, concorrência, ausência de envio real, empate, fonte desatualizada, reinício, persistência e rotas HTTP/WebSocket. Um teste de navegador reproduzível está em `tests/browser_smoke.py`.
