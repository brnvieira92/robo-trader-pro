# Pesquisa Forex — lote 02, 24/10/2026

**Nenhuma candidata aprovada.** Não houve alteração da estratégia selecionada, dos filtros ou do gerenciamento de risco da execução. As duas hipóteses novas são exclusivamente de pesquisa e são recusadas pela configuração do robô e pela preparação nativa de ordens.

O protocolo foi registrado no commit `0dc2ad7` antes da implementação e da avaliação. Não houve otimização nem ajuste depois dos resultados. A condição de encerramento deste lote é concluir as oito avaliações registradas; não repetir ajustes até produzir artificialmente um histórico positivo.

## Método

Base fornecida pelo usuário: quatro pares, 10.000 velas M1 por par. SHA-256 registrado no [protocolo](forex_batch_02_protocol.json). Desenvolvimento: 15 e 16/10, das 03:00 às 17:56, horário de São Paulo; 897 velas avaliadas por par/dia. Saldo sintético de US$10.000 reiniciado em cada caso, risco e critérios originais da base, até duas posições, sem martingale. Custos: spread de cada vela, slippage adverso de 2 ou 5 pontos MT5 em cada preenchimento, comissão zero conforme informação da conta e swap zero como hipótese para estas janelas intradiárias. Janelas sem fechamento forçado de posições.

- **Retorno à média:** rejeição e reentrada em Bollinger 20/2, filtro de eficiência para lateralização, confirmação de candle/RSI e alvo obrigatoriamente limitado pela média. Não inventa um alvo de 2R além da média para aprovar a entrada.
- **Rompimento após compressão:** largura anterior de Bollinger abaixo do quantil histórico predefinido, rompimento de dez velas e fechamento direcional. Stops e alvos usam o planejamento comum com estrutura, ATR e custos.

Regras de aprovação: lucro realizado e variação de patrimônio positivos em cada caso, pelo menos uma operação encerrada por caso e vinte encerradas somando os dias por cenário de custo. Só a candidata aprovada iria para 19/10, uma única vez. Esse mínimo serve apenas para triagem, não demonstra vantagem financeira.

## Resultados

Valores em USD; não são operações executadas no MT5. Nos oito casos não restou posição aberta, portanto lucro realizado e variação final de patrimônio coincidem.

| Estratégia | Dia | Slippage por preenchimento | Encerradas | Wins / Losses | Resultado líquido |
|---|---|---:|---:|---:|---:|
| Retorno à média | 15/10 | 2 pontos | 2 | 1 / 1 | +5,93 |
| Retorno à média | 15/10 | 5 pontos | 1 | 1 / 0 | +11,32 |
| Retorno à média | 16/10 | 2 pontos | 0 | 0 / 0 | 0,00 |
| Retorno à média | 16/10 | 5 pontos | 0 | 0 / 0 | 0,00 |
| Rompimento após compressão | 15/10 | 2 pontos | 5 | 0 / 5 | −37,24 |
| Rompimento após compressão | 15/10 | 5 pontos | 4 | 0 / 4 | −29,02 |
| Rompimento após compressão | 16/10 | 2 pontos | 12 | 4 / 8 | −6,85 |
| Rompimento após compressão | 16/10 | 5 pontos | 9 | 2 / 7 | −28,63 |

Maior slippage muda quais operações passam na pré-validação e a sequência da carteira. Por isso não se deve interpretar um resultado melhor com mais slippage como benefício do custo: são conjuntos de operações diferentes.

A reversão à média encontrou sinais técnicos, mas poucas oportunidades com espaço líquido suficiente até a média. Em 16/10, nenhuma entrada passou em todos os requisitos. O rompimento gerou mais entradas, porém perdeu nos quatro casos e encontrou o bloqueio de sequência de perdas. Mais operações, por si só, não resolveram a expectativa financeira.

**19/10 não foi avaliado neste lote:** nenhuma candidata passou na triagem. Não selecionamos pares, horários ou subgrupos lucrativos após observar perdas para apresentar uma aprovação.

## Limites e próximos dados

Dois dias são insuficientes para avaliar a robustez de uma estratégia. Os períodos 20–23/10 já usados em estudos anteriores não servem como confirmação independente. A base de 10.000 velas corresponde a poucos dias de mercado por par, não a milhares de operações independentes.

Novas pesquisas precisam de histórico mais longo, cobrindo meses e diferentes condições, com períodos separados para desenvolvimento, confirmação e demonstração futura. Exportar novamente as mesmas últimas 10.000 velas não resolve essa limitação. Para operações curtas, ticks Bid/Ask históricos melhoram a avaliação do custo e da ordem de toque do SL/TP.

A simulação usa OHLC Bid/Ask aproximado, especificações monetárias/margens congeladas e hipóteses de slippage; não reproduz todos os custos/executabilidade históricos. Não se pode garantir lucro em todo dia ou em dados futuros.

## Reprodução e verificação

```bash
PYTHONPATH=. .venv/bin/python scripts/research_forex_batch02.py /caminho/base-forex.json /caminho/novo-diretorio
```

O programa verifica o hash da base, recusa sobrescrever o diretório de saída, registra inclusive falhas e não abre a confirmação sem aprovação. Repetir o comando reproduz resultados conhecidos e não constitui nova validação independente.

Resumo numérico e bloqueios por par: [forex_batch_02_results.json](forex_batch_02_results.json). Os arquivos completos com análises, negócios e curvas ficaram em `/workspace/artifacts/forex-batch02` nesta sessão. A execução financeira usa o mesmo simulador de SL/TP, orçamento e pré-validação do laboratório.

Verificação de software: **419 testes passaram**, incluindo simetria compra/venda, alvo obrigatório, confirmação apenas com dados passados, recusa de ativação operacional e triagem que reprova amostras pequenas/prejuízo aberto. Testes de software aprovados não significam estratégia financeiramente aprovada.
