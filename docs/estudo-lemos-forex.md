# Estudo aplicado: Flávio Lemos e o ProspeTraderBot

Data: 24/10/2026. Código examinado: commit `891b54b`, branch `work`.

Identificação do arquivo estudado — SHA-256: `2dbbb48a723180d4c36efa7b95c5ffc7e7f20faaeb6cf54fd30881a8effb009c`.

Fonte: PDF enviado pelo usuário, *Análise Técnica dos Mercados Financeiros*, Flávio Alexandre Caldas de Almeida Lemos, 3ª edição. O arquivo contém **997 páginas**; as referências abaixo usam a numeração do leitor de PDF, não a paginação de uma edição impressa. Não foram consultados serviços pagos nem resultados externos para atribuir rentabilidade a uma técnica.

## Escopo e conclusão

Foi feito um mapeamento dos 28 capítulos e anexos, uma leitura dirigida das seções pertinentes ao Forex e à automação, comparação com o código e inspeção visual de páginas com fórmulas/figuras relevantes. Este documento é um estudo aplicado e crítico; não afirma uma leitura linha a linha das 997 páginas nem valida todas as técnicas catalogadas. As famílias de menor prioridade receberam triagem conceitual, não uma revisão integral de cada exemplo do livro.

**A principal oportunidade é melhorar a relação entre contexto, entrada e resultado financeiro, com evidência verificável.** Acrescentar simultaneamente muitos indicadores correlacionados pode atrasar as entradas e reduzir a amostra sem aumentar a vantagem. O próprio livro recomenda escolher indicadores complementares, discute confirmação e alerta sobre otimização excessiva (pp. 351–355, 723 e 845–849, 881–882).

Há novas hipóteses úteis — pivô 1-2-3, reteste de nível estrutural, falso rompimento, força relativa de moedas e perfil temporal de sessão — mas nenhuma está financeiramente aprovada por aparecer no livro. Também não há justificativa para trocar automaticamente a estratégia atual após este estudo.

**Não houve alteração de código executável, parâmetros, seleção de estratégia, ordens ou filtros nesta etapa.** Este arquivo registra conhecimento e critérios para pesquisas futuras; não cria monitoramento em segundo plano.

## 1. O que o projeto efetivamente já tem

A análise se refere à versão do repositório; não confirma qual versão/estratégia está em execução na instalação Windows.

| Recurso | Evidência no projeto | Limite da implementação atual |
|---|---|---|
| EMA, RSI, Bollinger, MACD, ATR e média de volume | `backend/strategies.py`, `StrategyEngine.enrich_dataframe` | Indicadores derivados das mesmas velas não são evidências independentes; ATR principal usa média móvel simples do TR |
| Confirmação por rompimento, engolfo e rejeição | `backend/forex_quality.py`, `entry_confirmation` | Confirmações locais, não um catálogo completo de price action nem identificação integral de padrões gráficos |
| Contexto M5 e entrada M1 na continuidade | `backend/forex_trend.py`, `continuation_signal` | Já existe análise em dois tempos; não existe ainda uma estrutura ampla M15/H1/D1 nesta estratégia |
| Pivôs locais confirmados para obstáculos do alvo | `backend/forex_trend.py`, `confirmed_targets` | Não equivale a uma estratégia de pivô 1-2-3, zonas persistentes ou pivôs diários calculados |
| Fibonacci de pivôs confirmados | `backend/fibonacci.py`, `fibonacci_context` | **Informativo:** não é filtro de entrada, feature do RF ou regra automática de execução |
| SL por estrutura/ATR e TP com obstáculos e retorno/risco | `backend/trade_plan.py`, `price_plan` | Não é um stop monetário fixo; a estrutura recente ainda pode ser curta para alguns setups |
| Lote condicionado ao risco e aos limites financeiros | `backend/mt5_execution.py`, `backend/trade_plan.py` | Um SL mais distante exige reduzir lote ou reprovar a entrada; não autoriza aumentar risco |
| Modelos RF por contexto de símbolo; validação temporal | `backend/ml_engine.py`, `backend/forex_multi.py` | Alvo atual é a direção do corpo da próxima vela, **não** a probabilidade de atingir o TP antes do SL |
| Risco global de até duas posições, conforme configuração | `backend/portfolio_risk.py` | Já soma risco bruto até SL e informa risco LONG/SHORT USD, sem crédito de hedge; não estima uma matriz dinâmica de correlação nem uma força monetária independente |
| Diagnóstico financeiro por par, horário, gatilho e política | `backend/forex_statistics.py` | Descritivo; agrupamentos lucrativos não aprovam automaticamente filtros ou estratégias |
| Excursões observadas MFE/MAE | `backend/trade_diagnostics.py`, laboratório | São limites inferiores observados/amostrados; não equivalem a trajetórias completas de ticks históricos |
| Break-even e trailing por ATR | `backend/forex_exit_research.py` | **Pesquisa somente:** não são ajustes automáticos de stops nativos implementados por esse módulo |
| Retomada após pullback estrito, reversão Bollinger e rompimento após compressão | `backend/config.py`, `RESEARCH_ONLY_STRATEGIES` | Hipóteses de laboratório; as duas últimas falharam na triagem do lote 02 |
| Triplo ATR | `backend/forex_triple_atr.py`, `DEMO_ONLY_STRATEGIES` | Candidata disponível somente com proteção de conta demo; os estudos anteriores não provaram vantagem positiva consistente |

**O que não existe atualmente:** modo sombra contínuo das candidatas. O estudo histórico é acionado por uma execução do laboratório/rotina de pesquisa, não fica simulando operações continuamente em paralelo.

### Limite de dados que afeta várias ideias do livro

O fluxo principal busca 240 velas e exclui a vela em formação (`backend/bot_manager.py`); o simulador usa até 239 velas fechadas por análise (`backend/forex_backtest.py`). Em M1 isso representa aproximadamente quatro horas, com menos histórico após um gap.

Assim, simplesmente acrescentar uma EMA200 em M15 ou ADX de prazo maior ao snapshot atual seria inadequado: faltam barras e aquecimento. A futura análise em vários tempos requer séries próprias e completas, obtidas/armazenadas com histórico suficiente, alinhadas por **horário de disponibilidade**. A base exportável atual tem teto de 10.000 barras por par e o laboratório avalia até 2.000 barras por requisição; uma validação de meses exige ampliar a infraestrutura ou encadear janelas preservando o estado da carteira. Somar dias reiniciados é diferente de simular uma conta contínua.

## 2. Mapa do livro para aplicação no Forex

Este mapa cobre os temas do sumário; as referências de página são os intervalos dos capítulos no PDF.

| Capítulo / páginas | Aplicação ou adaptação possível | Situação / cuidado |
|---|---|---|
| 1 — Fundamentos, 53–86 | Distinguir descrição de preço, hipótese e evidência quantitativa | Um padrão não é uma previsão garantida; dados técnicos não substituem calendário e fatores macro |
| 2 — Dow, 87–101 | Sequência de topos/fundos, persistência e mudança de tendência | Implementação estrutural mais ampla seria nova; confirmação entre índices de ações não se transplanta literalmente para pares |
| 3 — Gráficos, 102–154 | Candles, múltiplos tempos, construção de barras e leitura de atividade | OHLC já existe; Renko/Kagi/P&F/three-line-break seriam pesquisas próprias e exigem cuidado com tempo de formação e preços executáveis |
| 4 — Tendência, 155–189 | Zonas, inversão suporte/resistência, retestes, canais e falhas | Há elementos locais; falta uma máquina de estados de nível rompido/retestado/inválido |
| 5 — Padrões gráficos, 190–259 | Duplo topo/fundo, OCO, triângulos, bandeiras, flâmulas e retângulos | Novos detectores seriam possíveis; precisam de tolerâncias e confirmação causal, não reconhecimento visual retrospectivo |
| 6 — Fibonacci, 260–300 | Agrupamento de níveis, correções e projeções com estrutura | Recurso atual é informativo; clusters de vários tempos seriam novos. Fibogap depende de gaps/sessões relevantes, pouco transferível à rotina Forex 24h |
| 7 — Elliott, 301–344 | Hipóteses sobre impulsos/correções | Baixa prioridade para automação: contagem subjetiva e revisável; o próprio capítulo discute críticas |
| 8 — Indicadores e testes, 347–355 | Complementaridade, parametrização e avaliação | Alta relevância metodológica. Não transformar a escolha repetida do melhor teste em prova de vantagem |
| 9 — Seguidores, 356–451 | ADX/DMI, Aroon, Donchian, Keltner, regressão, SAR, HiLo, Ichimoku e Didi | EMAs/Bollinger/ATR já usados; novos indicadores merecem comparação isolada, não empilhamento automático |
| 10 — Momentum, 452–498 | Divergências, RSI, MACD, estocástico, %R, CCI, ROC, KST, TRIX, Elder-Ray | RSI/MACD já existem; divergências estruturais e demais famílias não. Muitos osciladores duplicam informação |
| 11 — Volume, 499–526 | Atividade relativa, OBV, acumulação/distribuição, Chaikin, MFI, Force Index, VPCI, VWAP e volume por preço | Sem volume centralizado de Forex, tratar tick volume como proxy local; não afirmar fluxo institucional real |
| 12 — Fôlego, 527–538 | Ideia de participação/amplitude de um conjunto de ativos | Linha AD, TRIN e McClellan originais dependem de universo de ações; não aplicar diretamente a quatro pares com USD |
| 13 — Sentimento, 539–558 | Volatilidade histórica e contexto de risco; TD Sequential como hipótese separada | VIX é de opções de ações dos EUA, não volatilidade direta de cada par. Put/call, short interest e AAII não existem na base atual |
| 14 — Força relativa/RRG, 559–584 | Comparação entre moedas, ranking relativo e concentração no USD | Força relativa não é RSI. Criar medida aberta e transparente; não alegar réplica dos indicadores proprietários do RRG |
| 15 — Ciclos, 585–625 | Sazonalidade por sessão, dia da semana e atividade | Sessões precisam de estudo; efeitos sazonais de ações, eleições e ciclos longos não são gatilhos M1 prontos |
| 16 — Gann, 626–638 | Hipóteses geométricas com unidades explicitadas | Baixa prioridade: ângulos dependem da escala preço/tempo; aparência no gráfico não é regra invariável |
| 17 — Fibonacci no tempo, 639–649 | Duração relativa de impulsos/correções | Exploração posterior; muita liberdade de escolha e risco de coincidências. Não aprova entradas por calendário sozinho |
| 18 — Stops, 652–686 | Invalidação estrutural, volatilidade e acompanhamento do stop | Grande parte já existe. Novidade relevante seria estrutura de prazo maior e comparação controlada de saídas |
| 19 — Planejamento, 687–697 | Expectativa líquida, lote, custos, stop de tempo, diário | Alto valor. Stop temporal e realização parcial ainda precisam de estudo específico; Kelly não é adequado com edge incerto |
| 20 — Psicologia, 698–718 | Auditoria, evitar retrospectiva, excesso de confiança e ajustes por poucas perdas | Aplicável ao processo de desenvolvimento e aprovação, mesmo com robô automático |
| 21 — Confluência, 719–723 | Confirmar vela e contexto antes da entrada | Já excluímos a vela em formação. Exigir todas as confirmações de todas as famílias pode eliminar oportunidades sem ganho líquido |
| 22 — Exemplos, 724–733 | Pivôs calculados, pivô 1-2-3 e sistema de médias 50/200 | Pivô calculado e 1-2-3 ainda não são estratégias no robô; método do diamante é lento e não atende automaticamente à busca por frequência M1 |
| 23 — Price action, 734–743 | Pin bar, inside bar e falso rompimento | Rejeição/engolfo já existem parcialmente; inside bar como estado e falso rompimento ainda seriam novos |
| 24 — Cripto, 744–786 | Princípios gerais de risco e leitura técnica | Staking, tokenomics, halving, DeFi e métricas de rede não fundamentam EURUSD/USDJPY/GBPUSD/USDCHF |
| 25 — Perfis, 787–840 | TPO, equilíbrio inicial, aceitação/rejeição e expansão de faixa | TPO é viável com aproximações; volume profile real exige dados de volume por preço. Forex não tem abertura única de bolsa |
| 26 — Sistemas/robôs, 841–882 | Regras objetivas, custos, drawdown, MFE/MAE, testes e falhas operacionais | Essencial ao desenho da pesquisa e da execução; automação não elimina discrepância entre simulação e corretora |
| 27 — Wyckoff, 883–921 | Faixas, falso rompimento, retomada e deterioração de estrutura | Adaptar eventos objetivos de preço; não declarar fases institucionais completas ou intenção dos bancos a partir de candles/tick volume |
| 28 — Conclusão, 922–934 | Simplicidade, consistência e controle de perdas | Princípios operacionais, não uma nova fonte de sinais |
| Anexo I — Candlesticks, 935–953 | Dicionário para novas features/detectores | Pode ser formalizado, mas padrões isolados e dependentes de gaps devem ser estudados por contexto |
| Anexos II/III, a partir de 954 | Construção de séries de futuros e gráficos de opções | Não são modelos de execução de Forex spot/CFD da XM; só pertinentes se ampliarmos os mercados |

### Inventário adicional de ferramentas

- **ADX/DMI e Aroon:** candidatos a descritores de contexto, não probabilidades de acerto. ADX mede força, não direção; direção vem de estrutura e/ou DI. Os limiares tradicionais 20/25 são pontos de partida de pesquisa, não leis do mercado.
- **Donchian:** máximas/mínimas de uma janela anterior, excluindo a vela do sinal quando a regra exige rompimento. Já temos rompimento de janela curta; um sistema Donchian mais longo precisa demonstrar diferença real, não apenas ganhar outro nome.
- **Keltner:** o livro descreve a versão original com média do preço típico e da amplitude (pp. 411–413). A versão popular com EMA e ATR é uma variante distinta. Devemos versionar qual delas foi testada.
- **Ichimoku:** alternativa completa de contexto/estrutura, não um sexto filtro sobre os demais. As linhas deslocadas exigem índices corretos: nenhuma informação futura pode entrar em uma decisão passada. Nuvem projetada não significa previsão conhecida do futuro.
- **Regressão e canais:** inclinação e erro normalizados por volatilidade podem descrever tendência/ruído. Um ângulo desenhado na tela varia com zoom e não deve definir o sinal.
- **SAR, HiLo e envelope:** alternativas de acompanhamento. Stops derivados de indicadores não podem se afastar para aumentar a perda depois da entrada. Em lateralização podem produzir zigue-zagues.
- **Didi e três médias:** variações de leitura de médias; forte sobreposição com o que já usamos. Prioridade menor que contexto estrutural e avaliação financeira.
- **Estocástico, %R e CCI:** possíveis alternativas ao RSI, não confirmações independentes para somar indiscriminadamente. Deve-se testar substituição e contribuição marginal.
- **ROC, momentum, KST, Coppock e TRIX:** família de tendência/momentum; algumas versões usam horizontes longos. Adaptar para M1 muda a hipótese original, não preserva automaticamente os resultados de outros mercados.
- **Elder-Ray e Force Index:** pressão relativa/atividade como hipóteses; Force Index depende da interpretação correta do volume disponível. Não fornecerão uma medida comprovada de agressão no mercado global de câmbio.
- **Padrões de reversão e continuação:** cada detector exige definição de pivôs, amplitude mínima em ATR, janela máxima, tolerância de níveis, instante de confirmação e invalidação. OCO ou bandeira reconhecidos apenas depois do movimento não contam como sinais disponíveis na entrada.
- **Renko, Kagi, ponto e figura e three-line-break:** pesquisas de representação, não preços de ordem. OHLC M1 não revela a ordem de todos os tijolos intravela. Se o tamanho do tijolo usa ATR, não se pode recalcular retrospectivamente tijolos passados com o ATR atual.
- **Candlevolume/equivolume:** dependem de dados de atividade/volume confiáveis. No Forex da corretora, a limitação de volume continua existindo.

## 3. Hipóteses novas com maior utilidade para o nosso problema

Os exemplos abaixo são **especificações conceituais de pesquisa**, não estratégias já implantadas, nem protocolos congelados ou evidência de lucro. Parâmetros exatos deverão ser registrados antes da comparação.

### A. Pivô 1-2-3 com reteste de nível estrutural

Base: Dow e suportes/resistências (caps. 2/4), pivôs (pp. 728–733) e confirmação (p. 723).

Compra hipotética:

1. Identificar um fundo, um topo posterior e outro fundo mais alto, todos confirmados no tempo da decisão.
2. O fechamento romper o topo intermediário, com tolerância definida por tick/ATR.
3. Aguardar o reteste da região rompida, agora candidata a suporte, e uma retomada confirmada; expirar o setup se o reteste não ocorrer dentro da janela registrada.
4. Colocar o SL além da invalidação estrutural, com buffer compatível com volatilidade/custos. Reduzir lote para manter o risco autorizado.
5. Limitar o alvo por obstáculo confirmado ou projeção declarada. Exigir retorno/risco líquido e os bloqueios globais existentes.

Venda: condições espelhadas. A novidade é **guardar o ciclo de vida de um nível estrutural**, não apenas exigir que uma vela anterior tenha tocado a EMA. O pullback estrito já testado foi reprovado; isso impede apresentar mais um pullback como melhoria comprovada.

Deve registrar: pivôs e seus horários de confirmação, nível, instante do rompimento, reteste, invalidação, expiração e motivo de rejeição. Se o setup depende de duas barras posteriores para confirmar um pivô, a entrada não pode ser registrada retroativamente no próprio extremo.

### B. Falso rompimento de faixa, com retorno confirmado

Base: padrão falsificado (pp. 740–741), aceitação/rejeição (cap. 25) e eventos de faixa de Wyckoff (pp. 898–903, 909).

Hipótese de compra: faixa identificada previamente; preço ultrapassa o limite inferior e fecha novamente dentro; um gatilho posterior confirma a retomada. SL além da mínima da falha; alvo em região previamente definida da faixa, somente se houver espaço líquido. Venda: simétrico acima da faixa.

Devem existir duas variantes separadas: falso rompimento da **barra-mãe/inside bar** e falso rompimento de **faixa estrutural**. Não misturá-las no mesmo detector e depois escolher a definição que teria ganhado.

É diferente da reversão Bollinger já testada: o gatilho depende de um nível/faixa anterior e de sua falha, não somente de sair e voltar para uma banda estatística. Ainda assim, as duas famílias compartilham comportamento de reversão e não constituem provas estatísticas independentes.

Não inferir “caça aos stops”, atuação de bancos ou acumulação institucional a partir desse desenho. O evento observado é preço ultrapassar e retornar; a intenção dos participantes não foi medida.

### C. Contexto de tendência/lateralização/transição

Base: caps. 8/9/15/26, sobretudo ADX/DMI (pp. 422–430).

Hoje há alinhamento EMA M1/M5 e uma classificação simples baseada em distância de EMAs/ATR; isso não equivale a um seletor completo de estratégias por regime. Uma primeira pesquisa deveria **medir o contexto sem mudar as entradas**, usando estrutura de topos/fundos e um descritor adicional, como ADX/DMI, em barras completas de M5/M15.

Depois, comparar:

- estratégia atual, sem alteração;
- a mesma estratégia com um único filtro de contexto;
- família estrutural em tendência e família de reversão em faixa;
- período de transição sem seleção forçada de uma família.

A transição de estado precisa de persistência/histerese predefinida para evitar alternância a cada candle. Regime não deve ser definido pela tendência que só ficou evidente no fim do dia. Mais filtros podem reduzir entradas; o benefício só existe se melhorarem a expectativa líquida/risco fora do desenvolvimento.

### D. Força relativa de moedas e diagnóstico de concentração

Base: cap. 14, pp. 560–568. Adaptação proposta, não réplica do RRG proprietário.

Comparar retornos normalizados por volatilidade em janelas alinhadas. A direção do par importa:

| Operação | Leitura direcional simples |
|---|---|
| BUY EURUSD | Compra EUR, vende USD |
| BUY GBPUSD | Compra GBP, vende USD |
| SELL USDJPY | Vende USD, compra JPY |
| SELL USDCHF | Vende USD, compra CHF |

Essas quatro operações compartilham a direção de venda do USD. O código **já** soma risco bruto e informa USD LONG/SHORT; a melhoria nova seria estudar ranking relativo/concentração de fatores, com limites adicionais se justificáveis. Não conceder desconto no orçamento global com base em correlação passada ou hedge presumido.

Com os quatro pares atuais, podemos observar EUR/GBP/JPY/CHF em relação ao USD e construir comparações relativas aproximadas. Não temos um índice global abrangente e independente de força das moedas. Crosses sintéticos obtidos desses mesmos pares compartilham a mesma informação; também não podem ser tratados como cotações executáveis adicionais.

Uso inicial recomendado: diagnóstico e comparação de oportunidades simultâneas já elegíveis, com regra de desempate registrada. Não autorizar uma entrada reprovada porque a moeda lidera um ranking.

### E. Sessão, equilíbrio inicial e TPO

Base: cap. 25, pp. 794–807, 826–828 e 837–840.

O Forex não tem uma única abertura de pregão. Precisamos definir a sessão de referência e seus fusos — por exemplo, Londres e Nova York — com horário de verão histórico, calendário e horário da corretora separados. Não usar permanentemente o mesmo horário brasileiro/UTC.

Uma hipótese é esperar formar uma faixa inicial da sessão e estudar rompimento/reteste ou falha dessa faixa. Outra é estimar aceitação por TPO e observar retorno à área anterior de negociação.

OHLC de uma janela indica extremos, mas não todo o tempo efetivamente passado em cada preço. TPO construído de barras é uma aproximação que deve declarar granularidade, agrupamento de preços e regra de contagem. Nenhum POC, área de valor, máxima diária ou classificação final do dia pode ser utilizado antes de estar disponível.

Não reutilizar como “nova confirmação” o mesmo rompimento após compressão do lote 02: aquele teve resultado negativo nos quatro casos. Uma nova hipótese de sessão exige motivo econômico/estrutural e protocolo próprios.

## 4. Melhorias de validação e saída sugeridas pelo estudo

### 4.1 Prever o resultado que interessa financeiramente

O cap. 19 enfatiza esperança matemática, e o cap. 26 exige regras de entrada/saída e avaliação financeira. A ligação abaixo com machine learning é **uma adaptação nossa**, não uma estratégia Random Forest ensinada pelo livro.

No código atual, o alvo do RF compara fechamento e abertura da próxima vela (`backend/ml_engine.py`, `extract_features_matrix`). Essa classificação pode estar correta e a operação ainda perder: a posição pode durar várias velas, pagar spread e atingir o SL antes do TP. A probabilidade direcional exibida e a taxa de acerto da validação não são probabilidades calibradas de lucro de uma operação.

Pesquisa recomendada: manter o RF atual como baseline e desenvolver separadamente um rótulo de resultado ligado ao **plano de entrada/SL/TP/horizonte**, com custos. Possíveis desfechos: TP antes de SL, SL antes de TP e término do horizonte sem atingir nenhum. O plano inicial e o horizonte precisam estar definidos sem dados futuros.

Cuidados indispensáveis:

- Barreiras construídas com dados conhecidos na decisão; não escolher retrospectivamente o melhor TP.
- Ticks Bid/Ask para ordem de toque, quando disponíveis; sem ticks, convenção conservadora para candles ambíguos.
- Não treinar com rótulos cujo desfecho atravessa a fronteira da validação: separar/purgar amostras sobrepostas e usar intervalo temporal apropriado.
- Treino, calibração e teste separados; reter modelos por par, política e versão de dados.
- Não contar centenas de observações sobrepostas como centenas de trades independentes.
- Comparar expectativa líquida e drawdown, não somente acurácia, Brier ou quantidade de sinais.
- Não reinterpretar o score técnico de 80/90/100 como chance de ganho de 80/90/100%.

Em uma simplificação sem empates, com ganho bruto médio G, perda bruta média L e custo médio C, `E = p*G − (1−p)*L − C`. Logo, `p > (L+C)/(G+L)` é o ponto de equilíbrio dessa aproximação. Só podemos usar p como probabilidade de ganho se ela corresponder ao mesmo desfecho e tiver validação/calibração adequadas; inserir a estimativa atual da próxima vela nessa fórmula seria inadequado.

### 4.2 SL lógico e lote adaptado, antes da entrada

Caps. 18/19, especialmente pp. 658–662 e 690–693. A ordem recomendada é: localizar invalidação da hipótese → estimar execução/custos → calcular distância de SL → dimensionar lote dentro do risco → verificar alvo e exposição.

O projeto já segue boa parte dessa lógica. O estudo não demonstra que todos os losses vieram de stops curtos. A alteração a pesquisar é comparar estrutura local e estrutura de prazo maior, com o mesmo orçamento. Não ampliar um SL já aberto para evitar realizar uma perda.

Exemplo apenas didático: se a distância técnica de stop dobra e todo o resto fosse constante, o lote aproximadamente cairia pela metade para manter o mesmo risco monetário. Os cálculos efetivos dependem da especificação de cada símbolo, volume mínimo/passo, moeda da conta e custos.

### 4.3 Stop de tempo, trailing estrutural e parciais

O stop temporal é discutido em pp. 690–691 e a realização parcial em pp. 691–692. O trailing e o risco de stops apertados são discutidos no cap. 18.

- **Stop de tempo:** uma hipótese pode expirar se a reação esperada não ocorrer, sem substituir o SL de proteção. Avaliar a distribuição de tempo até MFE/MAE/TP/SL antes de escolher prazo. Não encerrar automaticamente todas as posições após um número escolhido por conveniência.
- **Trailing estrutural:** estudar acompanhamento de pivôs confirmados, com deslocamento monotônico e risco original preservado. Hoje já há trailing ATR/break-even de laboratório; não apresentá-los como novos nem presumir que estejam modificando stops reais.
- **Parciais:** podem suavizar a curva e também reduzir ganho médio. Respeitar lote mínimo/passo; metade de 0,01 lote não é negociável se o mínimo/passo forem 0,01. Revalidar custos, TP restante e orçamento sem liberar risco de forma fictícia.
- **Break-even:** preço de entrada não é necessariamente zero financeiro após spread, taxas, swap e slippage. Uma saída chamada break-even pode resultar em perda em gaps.

Essas alternativas devem ser comparadas separadamente. Não misturar entrada nova, stop novo, parcial e trailing na primeira comparação, pois perderíamos a atribuição do resultado.

### 4.4 Modo sombra como coleta prospectiva

Conecta a discussão anterior do usuário à necessidade de comparar trades teóricos e executados do cap. 26. **É proposta; não foi implementado por este estudo.**

Uma implementação correta teria:

1. Estratégias virtuais com identificadores e versões próprios, isoladas da autorização de execução de ordens.
2. Mesmos dados disponíveis no instante, sem reanalisar o passado depois de ver o resultado.
3. Entradas e saídas virtuais com Bid/Ask, custos e cenários explícitos; sem declarar preenchimento real.
4. Duas leituras distintas: oportunidades sem restrição de carteira, para diagnóstico, e carteira virtual completa com risco/cooldown/limites, para comparação operacional. Não comparar diretamente os lucros dessas duas leituras.
5. Diário persistente com sinal, plano, bloqueios, MFE/MAE observado, duração e resultado, inclusive para hipóteses reprovadas.
6. Análises no fechamento de novas barras, isolamento de falhas e orçamento de processamento. Pesquisa lenta não pode atrasar reconciliação/proteção da conta real.
7. Nenhuma promoção automática a LIVE por um pequeno lucro recente.

## 5. Pontos do livro que exigem revisão crítica

Os problemas abaixo foram identificados nesta versão do PDF. Uma fonte respeitada continua sendo uma fonte de hipóteses e conceitos; a implementação deve conferir fórmulas e definições.

1. **ADX/DMI, pp. 423–426:** a fórmula impressa de DX na p. 425 usa uma subtração onde o texto fala em razão; a seção também é ambígua sobre sinais/suavização. A página foi conferida visualmente. Para uma implementação convencional de Wilder, usar movimentos direcionais positivos mutuamente exclusivos, suavização declarada, `DI± = 100 * RMA(DM±)/RMA(TR)` e `DX = 100 * abs(DI+−DI−)/(DI++DI−)`, com tratamento de denominador zero; ADX é a suavização de DX. Validar semente, aquecimento e convenção contra uma referência/testes independentes. Não copiar literalmente a expressão inconsistente.
2. **Aroon, p. 430:** o exemplo `(25−5)/25 * 100` resulta em 80, não 800. É necessário definir também como contar o candle atual e desempatar extremos repetidos.
3. **POC e VWAP, p. 837:** POC é a faixa de maior volume; VWAP é média ponderada. Não são a mesma medida. Exemplo: preços 100, 101 e 102 com volumes 1, 5 e 4 têm POC 101 e VWAP 101,3.
4. **Inside bar e engolfo, pp. 738–740:** o texto aproxima nomenclaturas. Para programar, distinguir inside bar por máxima/mínima contidas na barra-mãe de engolfo do corpo anterior. Se adotarmos outra convenção, ela deve ter nome e versão próprios.
5. **Drawdown, p. 844, versus MFE/MAE nas pp. 872–875:** separar drawdown da curva de patrimônio, queda desde o pico, de excursão adversa de uma posição. São diagnósticos diferentes; a duração de uma perda também importa.
6. **Backtest de carteira, p. 355:** a limitação didática de testar um ativo não é uma restrição geral da técnica. Nosso caso precisa de simulação conjunta com capital, margem e posições simultâneas; somar curvas isoladas pode exceder o orçamento.
7. **Confluência, pp. 720–723:** várias confirmações derivadas dos mesmos preços não multiplicam suas probabilidades. Há inclusive tensão entre juntar técnicas e evitar indicadores redundantes (p. 353). Resolver por testes de contribuição marginal.
8. **Resultados ilustrativos e afirmações de eficácia:** exemplos vencedores do livro não estimam a taxa de acerto na nossa corretora. Um indicador “antecedente” não conhece o futuro e uma figura bonita não prova execução lucrativa disponível no passado.
9. **Percentuais de perfil e normalidade:** área de valor em torno de 70% é uma regra de construção, não probabilidade garantida de retorno do preço. Retornos e distribuições intradiárias não devem ser presumidos normais.

Não foram transcritos capítulos nem reproduzido o livro no repositório. O documento contém síntese, crítica e propostas próprias.

## 6. O que exige dados diferentes dos que temos

| Ideia | Dados e adaptações necessários |
|---|---|
| Força de moedas, correlação e fator USD | Séries sincronizadas, retornos e volatilidades na mesma janela; universo e referência explícitos; dados adicionais se quisermos uma medida mais abrangente |
| Volume profile, OBV/MFI/Chaikin/VWAP | Fonte e significado do volume definidos. Tick volume local não é volume financeiro global; não distribuir arbitrariamente o volume de uma vela por toda a faixa e chamar de negócios executados |
| TPO, sessão e pivô diário | Fuso/calendário e horário de corte definidos, granularidade de preços/tempo e perfil anterior completo; dias de domingo e horários de verão tratados |
| Fluxo institucional ou liquidez global | Não inferível com rigor a partir do OHLC atual. Eventual fonte de futuros/fluxo é parcial e tem base/horário próprios; não é automaticamente o livro de ofertas da XM |
| Calendário macro, juros e notícias | Provedor, timestamps, moedas afetadas e eventos conhecidos na época; surpresa exige consenso anterior e dado publicado disponível naquele instante. O livro não implementa esse feed para nós |
| VIX, sentimento, COT e opções cambiais | Séries externas com publicação/latência. COT é uma possível extensão externa de sentimento, não um recurso demonstrado neste estudo do livro nem um sinal M1 em tempo real |
| Modelos com desfecho SL/TP e execução curta | Preferência por ticks Bid/Ask, especificações/custos e desfechos históricos; tratamento de ambiguidade e fronteiras temporais |
| Contexto M15/H1/D1 | Histórico e aquecimento próprios, com mapeamento causal para cada decisão M1/M5; não ampliar por interpolação as 239 barras disponíveis |

## 7. Ordem de desenvolvimento recomendada

| Etapa | Entrega proposta | Critério para avançar |
|---|---|---|
| 1 | Ampliar armazenamento/histórico e coleta prospectiva; desenhar modo sombra persistente | Reproduzir as decisões com timestamps, custos e versões; pesquisa não atrasa a execução |
| 2 | Diagnóstico de estrutura e regime em vários tempos, inicialmente sem mudar ordens | Conferência causal, warm-up suficiente e comparação com o comportamento atual |
| 3 | Uma candidata por vez: pivô/reteste estrutural e depois falso rompimento | Regras congeladas antes dos resultados, lucro líquido avaliado com drawdown e amostra suficiente |
| 4 | Força relativa/concentração como diagnóstico e desempate | Benefício além do orçamento global já existente; nenhum crédito fictício de hedge |
| 5 | Experimento separado de rótulo financeiro para ML e de saídas | Validação temporal purgada, custos/execução conservadores e comparação isolada com baseline |
| 6 | Demonstração prospectiva e eventual habilitação operacional | Evidência fora do desenvolvimento, revisão dos riscos e política explícita de ativação |

Métricas necessárias: expectativa líquida, profit factor, drawdown e duração, número de operações, distribuição de resultado em R, custos sobre ganho bruto, MFE/MAE, tempo até alvo/stop, estabilidade por par/sessão/regime e diferenças entre resultado virtual e executado. Wilson de taxa de acerto, que já existe, não é intervalo da expectativa financeira. Se houver reamostragem, preferir blocos temporais compatíveis com a dependência; embaralhar trades individualmente não elimina o risco de seleção.

Amostra: meses e diferentes condições, com uma quantidade útil de operações fora do desenvolvimento; centenas podem ser um objetivo de coleta, mas não existe um número mágico que garanta validação. O livro cita 30–50 sinais como mínimo indicativo na p. 849; isso não assegura precisão estatística ou robustez para nossa estratégia multipares.

Registrar todas as tentativas, inclusive perdas e períodos sem operações. Ter testado mais técnicas aumenta o risco de selecionar uma vencedora por acaso. Proteger dados de confirmação, limitar o conjunto de hipóteses e acompanhar o comportamento futuro são mais importantes do que exigir que cada dia histórico fique positivo.

## Decisão resultante deste estudo

**Vale aproveitar o livro para formalizar contexto e estrutura, melhorar os dados de validação e comparar resultados financeiros.** As duas primeiras famílias de entrada que merecem uma especificação são pivô/reteste estrutural e falso rompimento de faixa. A primeira entrega de infraestrutura mais útil é coleta prospectiva/modo sombra com dados suficientes e isolamento da execução.

Nada aqui demonstra aumento de assertividade ou rentabilidade. Não foram feitos novos backtests nem ativadas novas estratégias neste estudo. As reprovações documentadas em `research/forex_batch_02_results.md` continuam válidas; o livro não as transforma em aprovações.
