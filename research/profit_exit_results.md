# Saída integral antecipada — comparação histórica

Política `profit-weakness-v1`, regras financeiras registradas no commit `a0ed3de` antes da comparação. Nenhuma otimização ou mudança dos gatilhos foi feita após observar resultados. A versão final preserva o controle separado de pico financeiro da posição e pico do patrimônio da carteira. Um ensaio preliminar foi invalidado por erro na variável de diagnóstico de drawdown; somente a execução completa corrigida em `profit-exit-study-v2` integra este relatório.

Base: quatro pares, 10.000 velas M1 por par. Datas conhecidas 20–23/10/2026, 03:00–17:56 em São Paulo. Estratégia de entrada Continuidade M1/M5, mesmos filtros/limites/orçamento, saldo sintético inicial US$10.000 em cada dia/custo. Comissão/swap zero como hipóteses; spread por vela e slippage de 2 ou 5 pontos MT5. Não é confirmação independente e não reclassifica as operações reais do usuário.

Baseline FIXED reaproveitada de resultados anteriores somente depois de verificar configuração normalizada, hashes dos mercados, janela, saldo e hipóteses de custos. Na ausência desses arquivos, o script recalcula a baseline. São ensaios comparáveis, não operações do mesmo conjunto fixo: saídas diferentes mudam vagas, cooldown e sequência de perdas, podendo mudar as próximas entradas.

| Dia | Slippage | TP/SL original USD | Saída antecipada USD | Wins/encerradas original → nova | Saídas antecipadas |
|---|---:|---:|---:|---:|---:|
| 2026-10-20 | 2 pontos | -24.29 | -24.29 | 1/6 → 1/6 | 0 |
| 2026-10-20 | 5 pontos | -28.79 | +24.62 | 0/4 → 8/11 | 5 |
| 2026-10-21 | 2 pontos | -29.93 | -29.93 | 0/4 → 0/4 | 0 |
| 2026-10-21 | 5 pontos | -21.79 | -21.79 | 0/3 → 0/3 | 0 |
| 2026-10-22 | 2 pontos | -3.19 | -8.58 | 7/20 → 11/20 | 8 |
| 2026-10-22 | 5 pontos | -35.45 | -13.52 | 3/13 → 10/17 | 9 |
| 2026-10-23 | 2 pontos | -4.36 | -0.73 | 7/20 → 11/20 | 8 |
| 2026-10-23 | 5 pontos | +20.80 | -9.72 | 5/10 → 5/10 | 4 |

## Agregados dos quatro ensaios diários

| Slippage | Política | PnL USD | Wins/encerradas | Acerto | Ganho médio USD | Perda média USD |
|---|---|---:|---:|---:|---:|---:|
| 2 pontos | Original | -61.78 | 15/50 | 30.0% | 13.10 | 7.38 |
| 2 pontos | Antecipada | -63.53 | 23/50 | 46.0% | 5.88 | 7.36 |
| 5 pontos | Original | -65.23 | 8/30 | 26.7% | 11.65 | 7.20 |
| 5 pontos | Antecipada | -20.41 | 23/41 | 56.1% | 4.73 | 7.18 |

Em todos os casos, as posições terminaram encerradas: resultado realizado e variação final de patrimônio coincidiram. A soma acima não é o retorno de uma conta contínua, pois cada dia foi reiniciado.

**A taxa de acerto aumentou, mas não surgiu vantagem financeira comprovada.** Com 2 pontos, o agregado piorou ligeiramente; com 5 pontos, o prejuízo diminuiu, mas continuou negativo. Em 22/10 com 2 pontos, a quantidade de Wins aumentou de 7 para 11 e o saldo piorou de −3,19 para −8,58 USD. Em 23/10 com 5 pontos, o saldo positivo de +20,80 virou −9,72 USD: encerrar antes também pode cortar vencedoras que chegariam ao TP. Não selecionar apenas o cenário favorável.

Esses resultados não justificam chamar entradas de certeiras nem afirmar que todo lucro de US$1 é mais seguro financeiramente. É necessário comparar frequência, ganho/perda médios, custos, drawdown e estabilidade em dados separados. O dia reservado 19/10 não foi usado.

Execução histórica: observa apenas velas fechadas anteriores e preço da abertura seguinte; nunca usa máxima/mínima futura para decidir a saída. SL/TP em gap são tratados antes, com convenção conservadora. Lucro estimado reserva custos; resultado contabilizado usa preenchimento simulado e custos definidos. Não há fechamento forçado de posições no final.

Execução no MT5: o mesmo gatilho foi integrado ao executor real, com posição/ticket/volume, persistência e reconciliação. A pesquisa não envia ordens ao MT5. Não existe modo sombra nesta entrega.

Reprodução: `scripts/evaluate_profit_exit.py BASE_JSON NOVO_DIRETORIO`. Resumos e hipóteses em `profit_exit_results.json`; artefatos completos desta sessão em `/workspace/artifacts/profit-exit-study-v2`.
