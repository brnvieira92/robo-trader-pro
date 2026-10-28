# Pivô estrutural — resultados exploratórios

Política fixa: `forex-structure-retest-v1`, registrada no commit `99f35f3` antes dos resultados. A estratégia está disponível para execução MT5 por solicitação expressa do usuário; **não foi aprovada financeiramente**. Não houve ajuste das regras após as perdas observadas.

Dados: base enviada pelo usuário, quatro pares com 10.000 velas M1 cada; períodos conhecidos de 20–23/10/2026. Janelas 03:00–17:56 em America/Sao_Paulo, 897 velas por par/dia; saldo sintético de US$10.000 reiniciado por caso. Mesmo orçamento, limites e critérios compartilhados da base. Sem encerramento forçado no fim. O dia reservado 19/10 não foi avaliado.

Custos: spread histórico de cada vela; slippage adverso de 2 ou 5 pontos MT5 por preenchimento; comissão e swap zero como hipóteses explícitas destas janelas. Pontos MT5 não são pips e diferem por símbolo. Câmbio do valor do tick e margem são especificações congeladas da base, não uma reprodução histórica exata.

| Dia | Slippage | Encerradas | Wins/Losses | PnL realizado USD | Variação de patrimônio USD | Abertas no fim |
|---|---:|---:|---:|---:|---:|---:|
| 2026-10-20 | 2 pontos | 4 | 0/4 | -29.34 | -29.34 | 0 |
| 2026-10-20 | 5 pontos | 3 | 0/3 | -21.20 | -22.66 | 1 |
| 2026-10-21 | 2 pontos | 4 | 0/4 | -28.59 | -28.59 | 0 |
| 2026-10-21 | 5 pontos | 4 | 0/4 | -28.18 | -28.18 | 0 |
| 2026-10-22 | 2 pontos | 5 | 3/2 | +25.19 | +25.19 | 0 |
| 2026-10-22 | 5 pontos | 4 | 2/2 | +9.65 | +9.65 | 0 |
| 2026-10-23 | 2 pontos | 1 | 0/1 | -7.14 | -7.14 | 0 |
| 2026-10-23 | 5 pontos | 1 | 0/1 | -7.13 | -7.13 | 0 |

## Interpretação

- Slippage 2 pontos: soma dos quatro ensaios com PnL realizado -39.88 USD e variação de patrimônio -39.88 USD; 14 encerradas, 3 wins e 11 losses. Não é o retorno de uma conta contínua, pois cada dia foi reiniciado.
- Slippage 5 pontos: soma dos quatro ensaios com PnL realizado -46.86 USD e variação de patrimônio -48.31 USD; 12 encerradas, 2 wins e 10 losses. Não é o retorno de uma conta contínua, pois cada dia foi reiniciado.

Apenas 22/10 ficou positivo. O cenário de 5 pontos em 20/10 terminou com uma posição aberta, com perda flutuante incluída na variação de patrimônio. Aumentar o slippage muda elegibilidade, lotes e sequência da carteira; comparar resultados não é simplesmente descontar um custo de uma lista fixa de trades.

Os dias já tinham sido usados em outras pesquisas. Estes resultados servem para inspecionar o comportamento da regra congelada e não são confirmação independente. A amostra é pequena, mas o resultado agregado negativo já impede alegar que o livro ou esta implementação melhoraram a rentabilidade. Não recomendo interpretar a disponibilidade técnica como justificativa financeira para usar capital real.

O robô não ganhou uma promessa de maior frequência: os ensaios encerraram de uma a cinco operações por dia/custo e continuam sujeitos à sequência de perdas, cooldown, estrutura, espaço para alvo e risco compartilhado. Nem mais entradas nem um SL estrutural mais distante garantem ganho.

Limitações: OHLC Bid, Ask aproximado pelo spread da vela, stop primeiro em vela ambígua, gaps tratados de forma conservadora, ausência de ticks e especificações/custos históricos completos. Sem lucro presumido para posições abertas. Nenhum backtest envia ordens ao terminal.

Código de reprodução: `scripts/evaluate_forex_structure.py`; resumo e bloqueios por par em `forex_structure_retest_results.json`. Arquivos completos desta sessão em `/workspace/artifacts/forex-structure-study`.

Verificação de software: 443 testes aprovados, incluindo 24 específicos; seleção/etapas do Radar em desktop e celular e build do frontend verificados. Testes de software não aprovam vantagem financeira.
