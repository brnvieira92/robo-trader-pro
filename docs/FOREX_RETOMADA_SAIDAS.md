# Retomada após correção e pesquisa de saídas

O experimento **Retomada Forex — correção na EMA e confirmação M1/M5** (`forex_pullback_retest`, política `forex-pullback-v1`) é uma estratégia técnica independente do Random Forest, disponível **somente no laboratório**. A comparação inicial não demonstrou melhora e a seleção para executar ordens está bloqueada. Ela é uma hipótese de pesquisa baseada nos rompimentos sem continuidade observados no histórico recebido. Não é uma reprodução do robô Triplo ATR do vídeo: a transcrição não especifica a fórmula e os multiplicadores necessários para reproduzi-lo.

## Entrada e escopo

A estratégia exige MT5 Forex, M1, score mínimo 78 e o mesmo contexto M5 completo/alinhamento M1 da opção 6. Não altera o cálculo das EMAs ou do contexto M5. A diferença de entrada é explícita:

1. A vela fechada imediatamente anterior deve ser uma correção: corpo de baixa para futura compra, corpo de alta para futura venda.
2. O intervalo máxima/mínima dessa vela deve intersectar a região da EMA rápida, com tolerância de 0,15 ATR. Uma correção antiga ou uma vela inteira distante da EMA não basta.
3. O fechamento da correção deve preservar a EMA lenta: acima dela menos 0,15 ATR para compra, abaixo dela mais 0,15 ATR para venda.
4. A vela atual fechada precisa retomar a direção, superar a máxima/mínima da correção em mais de 0,05 ATR e recuperar a EMA rápida.
5. Permanecem confirmação do corpo/posição de fechamento, extensão máxima de 1,5 ATR da EMA rápida e proteção contra vela acima de três ATR.

Rompimento isolado não autoriza entrada nessa opção. Os checks são registrados em `PULLBACK_RETEST`, com toque na região, correção, preservação da estrutura e retomada observados separadamente. O gatilho é `PULLBACK_RETEST`, sem reclassificar o histórico da opção 6.

O score usa as mesmas regras técnicas da Continuidade: 80 pontos obrigatórios, mais 10 por RSI e 10 por força de fechamento. É uma contagem de regras, não probabilidade de vitória. Score 100 não significa certeza nem validação financeira. A nova opção pode gerar MENOS entradas; sua frequência e resultado precisam ser medidos.

## Escopo e bloqueio de execução

A estratégia é selecionada em **Estratégia somente da pesquisa**, dentro do laboratório. Não aparece no seletor do Setup operacional. O endpoint de configuração rejeita sua seleção e a verificação de risco recusa ordens mesmo se uma configuração antiga ou alterada contiver esse identificador. A configuração ativa permanece preservada.

A simulação continua utilizando o mesmo planejamento estrutural/ATR, projeção de 2R sujeita a obstáculos, retorno/risco mínimo, até duas posições em símbolos distintos e orçamento/margem compartilhados. A hipótese pode ser estudada com o mesmo período e custos da Continuidade. Não há trailing nativo ou break-even ativo nessa entrega. Os experimentos de saída também pertencem exclusivamente ao laboratório.

## Experimentos de saída no laboratório

Em **Backtest Forex — SL/TP e risco global**, escolha uma das saídas somente da pesquisa:

- `FIXED`: conserva SL/TP original (padrão e comportamento das bases antigas).
- `BREAK_EVEN`: após avanço confirmado no fechamento, aproxima o SL do preço de entrada com cobertura dos custos assumidos.
- `ATR_TRAILING`: após ativação, acompanha o fechamento a uma distância ATR.
- `BREAK_EVEN_ATR`: usa a proteção mais favorável entre as duas opções.

`exit_management` é um campo do pedido de pesquisa, separado de `config`. Os parâmetros iniciais são `activation_r=1` e `trailing_atr_mult=2`; são hipóteses predefinidas, sem otimização. A API limita a ativação a 0,25–5R e a distância a 0,5–5 ATR. 1R é a distância entre preenchimento de entrada e SL ORIGINAL, preservada durante toda a operação.

A ativação usa somente o FECHAMENTO da vela; seu máximo intravela não ativa proteção. O ATR usado é o conhecido no início da vela, obtido da análise das velas anteriores fechadas. O novo SL vale apenas na PRÓXIMA vela. Não se utiliza a mínima da mesma vela para simular um stop recém-ativado em ordem de preços desconhecida.

Após o primeiro ajuste válido, a proteção permanece ativada. O stop só pode aproximar-se na direção favorável: sobe em compras e desce em vendas, respeitando tamanho de tick, distância mínima e TP. Uma expansão do ATR não afasta o stop. A reserva original e o denominador R permanecem congelados, sem liberar orçamento para outras posições. O TP original permanece no servidor simulado; o estudo não mede uma estratégia sem limite de alvo.

O offset de proteção considera comissão de ida/volta, buffer configurado, swap já debitado e slippage de saída assumido. Custos futuros, gaps, preços inexistentes e especificações históricas aproximadas ainda podem causar perda. Não há garantia de resultado líquido zero. As regras conservadoras de gaps e SL primeiro em vela ambígua permanecem.

O resultado JSON e o CSV incluem `initial_stop_loss`, `initial_risk_distance`, `exit_management_activated` e `stop_updates`: stop antigo, novo, motivo, fechamento observado, ATR utilizado e instante a partir do qual vale. Os campos também identificam a mudança de política de entrada. O Radar continua sendo somente leitura.

## Comparação e interpretação

Compare a mesma base, período, custos, orçamento e vagas: Continuidade com saída original; Retomada experimental com saída original; Retomada experimental com saída dinâmica. A mudança de saída pode alterar duração, vagas e entradas seguintes, portanto os resultados de carteira não são pares de operações idênticas. Desempenho deve ser comparado por resultado líquido, drawdown, expectativa, frequência e posições ainda abertas; um empate não deve ser contado como lucro.

Os quatro losses de 23/10 já analisados não chegaram a 1R favorável antes do stop, de modo que uma ativação de proteção em 1R não teria salvado essas quatro entradas. Escolher limiares para salvar perdas conhecidas gera viés. O material recebido é um período já inspecionado, sem holdout independente; qualquer avaliação nele é exploratória. Use outro período e acompanhamento em demo antes de concluir superioridade financeira ou implementar movimentação de stops no MT5.


## Avaliação exploratória inicial, sem otimização

Base recebida de 10.000 velas por par. Três variantes predefinidas, mesmos quatro pares, orçamento, limite de duas posições e saldo inicial sintético de US$ 10.000 em cada dia. Proteção em 1R e trailing de 2 ATR. Comissão, swap e slippage zero como hipóteses; spread M1 aproximado da base. Períodos já inspecionados, sem holdout independente.

| Dia em Brasília | Estratégia / saída | Entradas | PnL realizado USD | Drawdown amostrado USD |
|---|---|---:|---:|---:|
| 22/10/2026 | Continuidade / original | 24 | +64,60 | 41,39 |
| 22/10/2026 | Retomada / original | 9 | −0,003 | 36,51 |
| 22/10/2026 | Retomada / proteção + ATR | 9 | −0,003 | 36,51 |
| 23/10/2026 até 17:56 | Continuidade / original | 20 | +5,54 | 47,75 |
| 23/10/2026 até 17:56 | Retomada / original | 4 | −29,76 | 30,40 |
| 23/10/2026 até 17:56 | Retomada / proteção + ATR | 4 | −29,76 | 30,40 |

Não havia posições abertas ao final desses recortes. A versão dinâmica registrou quatro ajustes em 22/10 sem alterar os resultados encerrados; em 23/10 não houve acionamento. A nova entrada foi inferior à referência nesses dois recortes e não foi promovida para execução.

As contagens são por dia local; os limites de entradas do sistema são por dia UTC. O dia local atravessa duas datas UTC, portanto 24 entradas locais não significa violação do teto de 20 em uma sessão UTC. Este estudo começa do zero no início de cada dia local e não reconstrói a sessão UTC anterior, o horário real de inicialização do robô ou seu diário real. Não é reprodução das quatro operações reais de 23/10 nem prova de rentabilidade da Continuidade. Especificações monetárias são snapshots atuais; o período é curto e as hipóteses de custos são incompletas.
