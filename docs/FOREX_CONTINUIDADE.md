# Continuidade Forex — M1/M5

A opção **6. Continuidade Forex — M1/M5, rompimento e pullback** (`forex_trend_continuation`, política `forex-trend-v1`) é uma estratégia técnica nova, independente do Random Forest. Está disponível no motor multipares e no laboratório Forex SL/TP. O código não troca a estratégia salva nem inicia o robô automaticamente. Não é uma promessa de mais operações ou maior lucro; é uma hipótese causal de continuidade para avaliar separadamente na demo.

## Ativação

1. Atualize a branch `work`, preserve `data/` e reinicie o servidor. Recarregue com Ctrl+F5.
2. Pause a automação e aguarde o encerramento/reconciliação das posições e intenções. O bloqueio de configuração permanece.
3. Mantenha **MT5 Forex**, **M1**, conta demo correta, pares selecionados e orçamento existente. Escolha a opção **6** e mantenha score mínimo 78/100.
4. Confira no Radar **Score técnico** e **RF não utilizado**, o contexto M5 e os checks de tendência. Clique **Iniciar MT5** para execução automática. O score é uma contagem de regras, sem significado de probabilidade de vitória.

## Regras de entrada

- O contexto usa apenas grupos completos de cinco velas M1 fechadas, ao menos 24 grupos M5. EMA 9 acima da EMA 21, EMA 21 crescente nos últimos três intervalos e fechamento acima dela indicam alta; a inversa indica baixa. Contexto misto/incompleto não gera entrada.
- O M1 precisa concordar: EMA 9/21, preço em relação à EMA 21 e inclinação da EMA 9 nos últimos três intervalos na direção M5.
- **Rompimento:** fechamento além do extremo das cinco velas anteriores, com margem de 0,05 ATR, na direção M5.
- **Pullback:** nas três velas anteriores, uma vela de correção toca a região da EMA 9 (tolerância 0,15 ATR); a vela atual retoma a direção, fecha além do extremo da vela anterior e recupera a EMA 9.
- Em ambos: corpo na direção da entrada e fechamento nos 40% favoráveis da vela. Não persegue preço a mais de 1,5 ATR da EMA 9. A proteção existente de vela maior que três ATR continua valendo.

Contexto, alinhamento, gatilho e confirmação são obrigatórios e correspondem a 80 pontos. RSI entre 50–75 na compra ou 25–50 na venda soma 10; fechamento nos 25% favoráveis soma outros 10. O limite de extensão é obrigatório e não soma pontos. Uma regra obrigatória reprovada produz sinal neutro, sem compensação por outros pontos. Não há aumento artificial da confiança do RF: o modelo não é consultado nessa estratégia, mesmo se a configuração anterior mantiver `use_ml_filter=true`. Nas demais estratégias, os filtros ML existentes continuam ativos conforme sua configuração.

## SL/TP e risco

O SL combina o piso ATR existente com o extremo das três velas mais recentes e a margem estrutural; vendas continuam convertendo Bid para Ask com spread. O teto de quatro ATR e as regras da corretora permanecem. Isso evita usar sempre o extremo de 20 velas como stop de uma entrada curta.

Suportes/resistências para limitar o **alvo** são separados do nível de **stop**. Procura-se o pivô oposto confirmado mais próximo à frente do preço nas últimas 60 velas, com duas velas de confirmação de cada lado. A última sombra ainda não confirmada não é tratada automaticamente como uma barreira. O alvo inicial é a maior distância entre a projeção ATR configurada e duas vezes a distância de stop (2R); ele é limitado antes do pivô confirmado, se existir. É uma projeção, não um destino garantido. Se faltar espaço para o retorno/risco mínimo após custos e arredondamento, a entrada é rejeitada. Não se remove um pivô real para conseguir aprovar uma ordem.

O lote é calculado com a especificação real, orçamento, margem e teto de lucro. Permanecem até duas posições em símbolos distintos, intenção incerta bloqueando novas entradas, orçamento global compartilhado, cooldown global, limite diário, perdas seguidas, permissões e cotação atual. O spread máximo absoluto e o limite de spread/SL (30% por padrão) continuam obrigatórios. Não há martingale, fechamento por cronômetro ou alteração retroativa dos stops existentes.

Os parâmetros versionados em `strategy_params` são `trend_breakout_lookback=5`, `trend_stop_lookback=3`, `trend_max_extension_atr=1.5` e `trend_target_r=2`. Eles são hipóteses iniciais, não valores otimizados financeiramente. Fibonacci permanece informativo.

## Diagnóstico e avaliação

O Radar exibe score técnico e regras atendidas/aguardando, sem dizer que a validação ML reprovou. `TREND_CONTEXT`, `TREND_LOCAL`, `TREND_TRIGGER`, `TREND_CANDLE` e `TREND_EXTENSION` mostram os motivos específicos. O histórico guarda estratégia, `forex-trend-v1`, contexto, gatilho, score e preços/estrutura usados; resultados antigos não são reclassificados. O histórico persistente e os limites financeiros continuam incluindo as operações antigas da conta.

O laboratório Forex aceita esta estratégia, usa velas anteriores à entrada e aceita até duas posições em símbolos distintos com risco e margem compartilhados, conforme a configuração da base. Não confundir esse laboratório com o teste binário de uma vela. Compare custos reais, resultado líquido, drawdown e frequência em períodos separados; gráficos ou quatro negócios não demonstram rentabilidade. Testes automatizados verificam lógica, causalidade e segurança, não uma taxa de acerto futura.

Na simulação exploratória da base previamente enviada pelo usuário (últimos 100 minutos disponíveis de 20/10/2026, quatro pares, limite histórico de uma posição, comissão/swap zero e hipótese de slippage adverso de dois pontos MT5 por preenchimento), apareceram 16 sinais técnicos qualificados, três entradas, duas perdas encerradas somando US$ 16,28 e uma posição aberta ao final. O resultado não demonstra superioridade financeira; não foi usado para retocar parâmetros ou selecionar apenas operações vencedoras. A estratégia é disponibilizada para pesquisa e avaliação em demo, sem mudança automática da configuração ativa do usuário.
