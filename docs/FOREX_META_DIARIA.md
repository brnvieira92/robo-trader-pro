# Forex: entrada, stop loss, take profit e meta diária

**PAPER não envia ordens ao MT5**, mesmo com AlgoTrading habilitado. O campo “Modo de execução” controla isso separadamente da conexão de dados. Uma conta demo conectada com LIVE_MT5 recebe ordens demonstrativas no terminal; uma conta real recebe ordens reais.

## Atualizar e conferir

1. Atualize os arquivos da branch `work` e reinicie o backend pelo iniciador do Windows. Recarregue a página com Ctrl+F5.
2. Pause a automação. Em Mercado, selecione **MT5 Forex** e o símbolo exato da corretora (incluindo sufixos).
3. O diagnóstico deve mostrar `forex-price-exits-v1`. Forex/B3 não deve exibir expiração nem payout binário. Cada posição exibe entrada, SL e TP.
4. Conecte o terminal Windows nas Conexões. Confira login, servidor e moeda. Habilite AlgoTrading e permita negociação pela API Python nas opções do terminal. Veja [configuração MT5](MT5_TELEGRAM.md).
5. Valide primeiro em conta demo. Para enviar ordens ao terminal, selecione **MT5 — enviar ordens ao terminal conectado**. Confirme o tipo de conta exibido. A troca é recusada enquanto houver posições pendentes ou automação ligada.
6. Confira o diagnóstico e os motivos de espera. Comprar/vender manualmente também precisa passar pelos limites e ter plano SL/TP válido. “Ligar” habilita a procura automática; a ausência de oportunidade pode resultar em nenhuma entrada.

Esta revisão foi testada com SDK MT5 controlado e navegador em Linux; o envio ao seu terminal Windows depende dessa configuração local e da corretora.

## Exemplo de objetivo: R$ 150 por dia

No painel pausado, sem posição aberta:

- **Meta diária:** `150` e **Moeda da meta:** `Reais — BRL`. Em PAPER os valores são BRL virtuais.
- Em conta USD, informe o símbolo de câmbio oferecido pela corretora, por exemplo `USDBRL` ou `USDBRL.s`. Pode ser inverso se os metadados do símbolo indicarem BRL/USD. Se a corretora não oferecer cotação compatível e atualizada, o robô bloqueia novas entradas com meta em BRL. Alternativamente use meta na moeda da conta, escolhendo explicitamente seu valor nessa moeda.
- **Teto de lucro projetado por entrada:** por exemplo `15` **na moeda da conta**. Em conta USD, `15` significa USD 15, mesmo se a meta diária estiver em BRL. Esse teto reduz o lote se necessário; não promete que o lucro será atingido.
- **Orçamento por entrada**, **risco percentual** e **stop loss diário** continuam limitando a exposição. O valor mais restritivo prevalece. Configure o limite de perda diário conforme o capital e sua tolerância; ele é independente da meta de lucro e expresso na moeda da conta.
- Confira spread, reserva de custos, intervalo entre entradas, máximo de operações e perdas consecutivas. Uma reserva de custos nula não significa ausência de taxas.

A conversão para BRL usa o preço médio bid/ask atual do MT5, com cotação de até 60 segundos. É indicativa: variação cambial pode mudar o valor exibido em BRL. O PnL de origem continua na moeda da conta. A sessão diária é **UTC**, identificada assim no painel e Telegram.

O robô reduz o orçamento se o lucro projetado exceder o teto por entrada ou o valor restante da meta. Caso o lote mínimo já exceda esse orçamento, ele espera. **Não aumenta apostas para recuperar perdas ou completar a meta.** Ao alcançar a meta de resultado realizado, pausa novas entradas, inclusive manuais. Não fecha posições existentes por essa razão. Gaps, taxas e variação cambial podem fazer o resultado ultrapassar os limites estimados.

## Como o plano escolhe SL e TP

1. A estratégia avalia velas fechadas. Os filtros técnicos e de validação temporal continuam exigidos para entradas automáticas. A validação de direção de uma vela não comprova lucratividade de uma estratégia SL/TP.
2. O ATR mede volatilidade. O stop parte do multiplicador de ATR e pode ser afastado para além do suporte, numa compra, ou da resistência, numa venda, com margem de estrutura.
3. O take profit parte de uma projeção de ATR e é limitado antes da próxima resistência/suporte observado. Suporte e resistência são extremos das últimas velas fechadas configuradas; podem falhar ou ser rompidos.
4. A ordem é descartada se o alvo estiver perto demais para o retorno/risco mínimo, o stop estrutural estiver distante demais, a cotação tiver se afastado do sinal ou os custos inviabilizarem a relação.
5. No LIVE_MT5 o plano é revalidado pelo bid/ask, spread, especificação do contrato, distância mínima, lote, margem, permissões e `order_check`. SL/TP seguem junto da ordem ao servidor.

A prévia BUY/SELL no painel mostra preços, perda/lucro estimados, retorno/risco e motivo de bloqueio. Ela é indicativa, sem uma cotação Bid/Ask executável; os preços e o volume finais dependem da revalidação no envio. Nenhuma técnica ou modelo garante entradas precisas em todas as operações ou R$ 150 diariamente. Dias sem operação ou com perda são possíveis. O laboratório CSV atual é binário e **não valida saídas SL/TP Forex**.

### Referência Bid/Ask e diagnóstico das entradas

Os planos nativos novos usam `pricing_version=bid-ask-structure-v2`. As velas e os extremos de suporte/resistência são Bid. Compras entram no Ask e encerram no Bid; vendas entram no Bid e encerram no Ask. Nas vendas, o spread observado é somado às referências estruturais de resistência (SL) e suporte (TP), antes das margens de ATR e do arredondamento. Isso preserva a referência visual Bid; não duplica o spread no PnL. O spread futuro pode variar: o ajuste de entrada não garante que o stop só será acionado após a mesma resistência Bid no futuro.

Após a conversão, o motor revalida espaço até o alvo, retorno/risco mínimo, distância máxima de quatro ATR (ou valor configurado), regras do símbolo, custo e lote monetário. Uma oportunidade pode ser rejeitada por deixar de atender aos critérios. Não foram afrouxados os filtros ML, score de 78%, limites globais ou número de posições. Não se alteram SL/TP de posições já enviadas nem resultados antigos.

O diário e a exportação JSON das novas entradas incluem `entry_diagnostics` (Bid, Ask, spread, timestamp UTC normalizado da cotação, especificação e margem), `signal_snapshot` (indicadores e validação ML disponíveis) e `plan_parameters`. O plano guarda ATR, estrutura Bid, referência de saída, spread/stop, spread/ATR e distâncias em ATR. A reconciliação discrimina profit, commission, swap, fee, preço, volume e motivo bruto disponível por negócio; `execution_summary` registra preço solicitado/preenchido e desvio de entrada (positivo = desfavorável). Dados novos não são inventados para operações antigas. Isso não equivale a ticks históricos nem permite reconstruir MAE/MFE intravela a partir de apenas duas execuções.

O backtest Forex SL/TP usa o mesmo cálculo corrigido e conserva a hipótese de Ask aproximado pelo spread da vela; permanece limitado a uma posição simultânea. Novas simulações podem diferir de relatórios produzidos pelo cálculo anterior. A correção de preços e os testes de software não comprovam vantagem financeira da estratégia nem calibram a estimativa RF para TP antes do SL.

## Fibonacci informativo

Em MT5 Forex, cada análise calcula Fibonacci separadamente para o símbolo/timeframe atual. O cálculo usa até 60 velas Bid já fechadas, sem adicionar dados ao treinamento RF. Um pivô precisa ter máxima estritamente maior, ou mínima estritamente menor, que as duas velas de cada lado; só fica disponível após o fechamento das duas velas posteriores. Empates e velas que seriam simultaneamente pivô de máxima e mínima são descartados porque a ordem intravela é desconhecida. O movimento de referência liga o pivô confirmado mais recente ao último pivô oposto anterior. Ele não é uma classificação garantida de tendência nem uma escolha retrospectiva do movimento mais lucrativo.

As retrações de 0%, 23,6%, 38,2%, 50%, 61,8%, 78,6% e 100% são medidas a partir do fim desse movimento. Extensões de 127,2% e 161,8% são medidas a partir da origem do movimento; são referências matemáticas, não previsões. Sem dois pivôs válidos, o painel informa indisponibilidade. Preço fora do intervalo é marcado explicitamente, e não descrito como retração ativa. Os pivôs podem mudar à medida que novos candles os confirmam; o contexto de cada análise permanece registrado para comparação posterior.

Consulte **Fibonacci — informativo** no card de cotação do ativo principal ou em cada card do Radar. O objeto `indicators.fibonacci` também acompanha os diagnósticos ANALYSIS/RADAR e o `signal_snapshot` das novas operações no histórico JSON. Os horários dos pivôs seguem a base temporal das velas (UTC normalizado na fonte MT5), não necessariamente o fuso visual do terminal.

Nesta etapa, Fibonacci não altera score, validação ML, seleção de pares, SL/TP, lote ou permissões de entrada; não há filtro obrigatório de retração nem promessa de mais operações. Operações antigas não recebem níveis retrospectivos inventados. A validação de eventual uso decisório deve comparar resultados líquidos fora da amostra antes de mudar a estratégia.

## Acompanhamento e interrupções

- **PAPER Forex/B3:** a posição permanece aberta até preço no SL/TP ou “Encerrar PAPER”. O preenchimento usa o preço observado, incluindo gaps, e o custo fixo configurado. Não é uma execução da corretora; o saldo é virtual.
- Se faltar cotação, não se inventa lucro ou encerramento. PAPER permanece pendente e novas entradas são bloqueadas; o monitoramento continua tentando recuperar a cotação. O programa precisa estar aberto para acompanhar esses stops virtuais; não existem ordens protetoras PAPER no servidor.
- Após reinício, posições PAPER baseadas em dados MT5 reais aguardam reconexão à mesma conta/servidor. Em fonte artificial, o reinício interrompe a simulação e debita conservadoramente a reserva; isso não é perda confirmada de mercado.
- **LIVE_MT5:** SL/TP ficam no servidor. O programa acompanha posição/histórico e apura negócios e custos identificados. O terminal é o local para conferir e encerrar manualmente posições reais. Uma resposta incerta não provoca reenvio automático.
- **Pausar** impede novas entradas automáticas; o acompanhamento de posições continua. Uma posição não fecha só porque passou um minuto ou terminou a vela.
- Uma posição carregada para o dia seguinte continua sendo acompanhada. PnL realizado é atribuído ao dia UTC do encerramento no PAPER e dos negócios no MT5.

O diagnóstico de execução também está em `GET /api/execution/diagnostics`, sem credenciais. Telegram e instruções do BotFather permanecem em [MT5_TELEGRAM.md](MT5_TELEGRAM.md).

O funcionamento contínuo, os alertas e os registros persistentes estão descritos em [Diagnóstico e recuperação](DIAGNOSTICO_RECUPERACAO.md).
