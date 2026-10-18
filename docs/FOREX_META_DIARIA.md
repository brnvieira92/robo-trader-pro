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

A prévia BUY/SELL no painel mostra preços, perda/lucro estimados, retorno/risco e motivo de bloqueio. Ela é indicativa; os preços e o volume finais dependem da revalidação no envio. Nenhuma técnica ou modelo garante entradas precisas em todas as operações ou R$ 150 diariamente. Dias sem operação ou com perda são possíveis. O laboratório CSV atual é binário e **não valida saídas SL/TP Forex**.

## Acompanhamento e interrupções

- **PAPER Forex/B3:** a posição permanece aberta até preço no SL/TP ou “Encerrar PAPER”. O preenchimento usa o preço observado, incluindo gaps, e o custo fixo configurado. Não é uma execução da corretora; o saldo é virtual.
- Se faltar cotação, não se inventa lucro ou encerramento. PAPER permanece pendente e novas entradas são pausadas. O programa precisa estar aberto para acompanhar esses stops virtuais; não existem ordens protetoras PAPER no servidor.
- Após reinício, posições PAPER baseadas em dados MT5 reais aguardam reconexão à mesma conta/servidor. Em fonte artificial, o reinício interrompe a simulação e debita conservadoramente a reserva; isso não é perda confirmada de mercado.
- **LIVE_MT5:** SL/TP ficam no servidor. O programa acompanha posição/histórico e apura negócios e custos identificados. O terminal é o local para conferir e encerrar manualmente posições reais. Uma resposta incerta não provoca reenvio automático.
- **Pausar** impede novas entradas automáticas; o acompanhamento de posições continua. Uma posição não fecha só porque passou um minuto ou terminou a vela.
- Uma posição carregada para o dia seguinte continua sendo acompanhada. PnL realizado é atribuído ao dia UTC do encerramento no PAPER e dos negócios no MT5.

O diagnóstico de execução também está em `GET /api/execution/diagnostics`, sem credenciais. Telegram e instruções do BotFather permanecem em [MT5_TELEGRAM.md](MT5_TELEGRAM.md).
