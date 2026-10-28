# Triplo ATR experimental — implementação, comparação e limites

`forex_triple_atr`, política `forex-triple-atr-v1`, é uma estratégia independente do RF e das EMAs de Continuidade. Está implementada para execução automática **em conta DEMO confirmada pelo SDK**, além de pesquisa histórica/PAPER. Não é apresentada como estratégia rentável: o conjunto testado com custos continuou negativo. A atualização não muda a estratégia salva nem inicia o robô.

## Regras fixadas antes da simulação

- M1 Forex, somente velas fechadas. Score mínimo 78 preservado.
- Três linhas de stop com HL2 `(máxima + mínima) / 2`, ATR de período 10 e multiplicadores **1,5; 2; 3**. O ATR usa EWM com alpha 1/10, inicializada pelo primeiro TR e publicada após dez observações; essa inicialização é explícita e pode diferir do indicador de outro fornecedor.
- TR usa o maior entre amplitude e distâncias ao fechamento anterior. Bandas são recursivas: a inferior não recua numa tendência de alta e a superior não avança numa tendência de baixa. O fechamento cruzando a banda muda sua direção.
- Compra: as três direções passam a ser de alta e o preço fecha acima das três linhas. Venda: inverso. O consenso da vela anterior deve ser diferente; um alinhamento antigo não autoriza reentradas repetidas.
- A vela deve ter corpo direcional e fechar nos 40% favoráveis da amplitude. A proteção comum de vela maior que três ATR continua ativa.
- As regras obrigatórias somam 80 pontos. RSI 50–75 em compras/25–50 em vendas soma dez; fechamento nos 25% favoráveis soma dez. Score é contagem técnica, nunca probabilidade de vitória. Checks e linhas ficam no diagnóstico.

A transcrição não fornece fórmula e parâmetros completos. Esta é uma implementação explícita inspirada no conceito, **não uma reprodução exata do robô do vídeo**. As três linhas derivam dos mesmos preços e não representam três evidências estatísticas independentes. São recalculadas na janela de até 239 velas fechadas usada pelo pipeline; novo alinhamento compara as duas últimas velas dessa mesma janela. Essa inicialização não equivale a um indicador com estado ilimitado desde o início do histórico. Não foi adicionado filtro M5 a essa nova estratégia M1; a opção Continuidade mantém integralmente suas regras anteriores.

O SL usa a linha ATR mais distante do consenso e o planejamento estrutural/ATR existente (os limites financeiros de distância continuam usando o ATR de 14 períodos do motor), com conversão Bid/Ask, piso ATR, teto de quatro ATR e distâncias da corretora. O alvo mantém projeção de 2R/ATR, limitada por pivô oposto confirmado, e retorno/risco mínimo de 1,5 após reservas. Não há inversão imediata, remoção de TP nem trailing nativo. Saídas dinâmicas continuam sendo pesquisas separadas.

## Execução na demo

No Setup, com automação pausada e posições/intentos resolvidos, a opção é **7. Triplo ATR — experimental, somente conta demo**. Após selecionar/salvar e iniciar MT5, a execução é automática. O sistema consulta `account_info().trade_mode`: apenas o modo DEMO (0) é permitido para essa estratégia. Conta real (2), competição (1), modo desconhecido e troca posterior para conta não demo bloqueiam novas entradas. Reconciliação e monitoramento continuam; o bloqueio não encerra a aplicação.

As mesmas verificações permanecem: até duas posições confirmadas/protegidas em pares distintos, orçamento bruto global compartilhado, intenção incerta bloqueando entradas, risco diário, limite de perdas seguidas, cooldown, spread, conta vinculada, margem, volume mínimo/passo/teto e diário persistente. Configuração não é salva se a conta não for demo. O Radar só apresenta diagnósticos.

Um alinhamento pode ter começado antes de iniciar o robô. Nesse caso, aguarda novo alinhamento; isso evita perseguir o mesmo estado após cada stop. A estratégia gerou menos entradas no estudo, sem promessa de frequência mínima.

## Quatro entradas anteriores de 23/10, no mesmo instante

Foram usados os 239 candles anteriores ao instante, sem preços futuros na decisão. Nos planos elegíveis, reutilizou-se a cotação real registrada no histórico; os preços futuros Ask foram aproximados pelo spread M1. Horizonte diagnóstico de 120 minutos, sem reconstruir a carteira anterior inteira.

| Entrada original, Brasília | Decisão do Triplo ATR |
|---|---|
| USDJPY compra, 02:00:02 | Sem entrada: três direções ainda não concordavam. |
| USDCHF compra, 04:06:02 | Sem entrada: alinhamento já existente, sem novo acionamento. |
| EURUSD venda, 04:24:01 | Sem entrada: alinhamento já existente. |
| USDJPY venda, 06:26:02 | Venda elegível. Novo SL 156,717; TP 156,359; lote 0,09. Continuaria perdendo. |

Na última operação, perda aproximada de **US$ 6,89** sem slippage assumido, **US$ 7,12** com dois pontos adversos por preenchimento e **US$ 7,46** com cinco pontos. A vela de saída seria aproximadamente 07:39, não o segundo exato. Valores monetários de JPY são snapshots da base, não câmbio histórico exato. **Entrada descartada não é ganho**; a nova estratégia pode ter outras operações em outros horários.

## Comparação de quatro recortes, sem otimização

Dias 20–23/10/2026, sempre **03:00–17:56 Brasília**. A janela uniforme evita lacunas anteriores nos quatro históricos; a entrada das 02:00 foi analisada separadamente acima. Não é um filtro de horário adicionado à execução. Saldo inicial sintético de US$ 10.000 em cada recorte, mesmos pares/orçamento/vagas, SL/TP original, comissões e swap zero como hipóteses e spreads M1 da base. Foram predefinidos slippages de 0, 2 e 5 **pontos MT5 por preenchimento**, sem escolher parâmetros vencedores.

Com dois pontos de slippage, variação da equity incluindo posições abertas:

| Dia | Continuidade | Triplo ATR | Entradas Continuidade / Triplo |
|---|---:|---:|---:|
| 20/10 | −24,29 | −14,28 | 6 / 2 |
| 21/10 | −29,93 | −4,66 | 4 / 6 |
| 22/10 | −3,19 | −0,61 | 20 / 6 |
| 23/10 | −4,36 | +4,01 | 20 / 5 |

Em 22/10 o Triplo ATR tinha uma posição ainda aberta: realizado +5,19, flutuante −5,80. Mostrar somente o realizado esconderia essa exposição. Não se forçou encerramento ao fim da amostra.

Soma descritiva das variações dos quatro ensaios reiniciados (não uma curva de conta contínua):

| Slippage por preenchimento | Continuidade | Triplo ATR |
|---|---:|---:|
| 0 pontos | −40,56 | −5,25 |
| 2 pontos | −61,78 | −15,54 |
| 5 pontos | −65,23 | −44,12 |

Houve redução de perdas em parte da comparação e menos operações, mas **nenhum desses conjuntos de custos demonstrou rentabilidade**. Há também mudança da seleção de negócios: maior custo pode reprovar um plano, alterar vagas e sequência, não apenas descontar taxas das mesmas operações. O ensaio de cinco pontos em 23/10 foi pior no Triplo que na Continuidade. O período é curto e já foi inspecionado, sem holdout independente. A liberação é para experimentar na demo, não para conta real nem para prometer ganhos.

## Correção do dimensionamento histórico com custos

O laboratório versão 4 considera a comissão assumida de ida/volta e slippage das duas pontas **antes** de calcular o lote. O adaptador monetário virtual informa esses custos na perda/retorno do candidato; o dimensionamento compartilhado reduz o lote até caber no orçamento e verifica o retorno/risco. Lote mínimo, margem e reservas globais continuam obrigatórios. Não se cobram novamente os custos já reservados das outras posições.

A liquidação usa o lucro bruto pelos preenchimentos com slippage e debita comissões uma única vez. O pós-cálculo ainda confere o orçamento. Assim, não se rejeita um lote que poderia ser reduzido mantendo os limites. Essa correção só altera a pesquisa; o cálculo real usa `order_calc_profit` do MT5 e o buffer configurado. Resultados antigos com custos positivos podem mudar, identificados por `version=4` e `cost_sizing_version=known-friction-before-lot-v1`. Resultados anteriores com slippage zero não devem ser apresentados como se incluíssem fricções reais.

## Reproduzir a análise

```bash
python tools/compare_forex.py --dataset "base-forex (1).json" --history "historico-operacoes (1).json" --output "estudo-triplo-atr"
```

O comando não conecta a um terminal nem envia ordens. Salva protocolo com hash da base e hipóteses, resultados completos das 24 combinações e `previous_entries.json`. O cache local reutiliza apenas sinais puros do mesmo prefixo da base imutável, sem alterar decisões por resultados futuros. Os resultados não equivalem ao diário real: cotação de abertura, spread aproximado, valores monetários congelados, estado inicial e limites UTC anteriores são diferentes.
