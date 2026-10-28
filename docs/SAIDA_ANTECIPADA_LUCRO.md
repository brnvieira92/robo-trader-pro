# Saída integral antecipada com lucro — MT5 Forex

Implementação de 25/10/2026, política `profit-weakness-v1`, conforme a escolha do usuário: **encerrar a operação inteira com lucro menor se houver enfraquecimento, mantendo a possibilidade de atingir o TP quando o movimento permanecer favorável**.

É gerenciamento operacional de posições do MT5, não modo sombra. Não abre uma estratégia nova e não altera os filtros de entrada. O resultado financeiro só é classificado como WIN/LOSS/DRAW pelo histórico confirmado do terminal. Não transforma uma posição aberta ou um fechamento parcial em Win.

## Quando pode fechar antes do TP

Requisitos simultâneos:

1. Posição Forex própria, integralmente identificada e protegida, conta/servidor corretos, volume e direção correspondentes ao diário.
2. Conta **hedge confirmada**. Netting, exchange ou modo desconhecido são recusados nesta versão: não se usa uma ordem oposta sem vínculo para tentar simular fechamento. A restrição evita que redução/reversão de uma posição agregada seja confundida com fechamento individual.
3. Cotação Bid/Ask atual, com a idade máxima existente; especificação real de tick, ponto, lote e preenchimento consultada no MT5.
4. Lucro líquido executável **estimado** igual ou maior que o mínimo configurado, após custos e reservas.
5. Uma vela fechada após o preenchimento da entrada apresenta corpo contra a posição, fechamento pior que o anterior e nos 35% desfavoráveis da amplitude.
6. Além dessa vela adversa, o fechamento perdeu a EMA9 **ou** o lucro líquido atual devolveu pelo menos a fração configurada do melhor lucro líquido observado.
7. A cotação atual ainda não recuperou a máxima dessa vela para BUY ou a mínima para SELL. Uma recuperação antes do envio cancela a saída antecipada.

Se o lucro mínimo for atingido mas o movimento continuar favorável, **não fecha apenas porque apareceu US$1 ou US$2**. O TP inicial segue como possibilidade. O lucro pode ultrapassar o mínimo; ele é um piso estimado para tentar fechar, não um alvo exato ou uma garantia de preenchimento.

O contexto da saída usa até sessenta velas fechadas e EMA9, com pelo menos cinquenta velas contínuas e recentes. O candle em formação é excluído. São regras técnicas explícitas, não uma previsão de ganho certo nem uma nova probabilidade Random Forest.

## Configuração no painel

Depois de atualizar/reiniciar o backend e usar Ctrl+F5 no navegador, abra **Gestão de risco**:

- **Saída antecipada com lucro por enfraquecimento:** habilita para novas operações.
- **Lucro líquido mínimo estimado:** padrão 1,00 na moeda da conta; para a conta USD do usuário, US$1.
- **Reserva extra de custos:** padrão 0,25 na moeda da conta.
- **Devolução do lucro observado:** padrão 35%.
- **Reserva de slippage da saída:** padrão 2 pontos MT5. Pontos não são pips e dependem do símbolo.

A função vem **desabilitada por padrão para compatibilidade** e só passa a valer quando habilitada. Exige MT5 Forex e LIVE_MT5, que significa envio ao terminal vinculado: a conta desse terminal pode ser real ou demo. A conta hedge é verificada na seleção e novamente antes de fechar.

A política é salva no momento de cada nova entrada. Posições antigas não ganham saídas novas retroativamente, e modificar a configuração com posições abertas continua sujeito ao bloqueio existente. A função não exige laboratório para operar, mas habilitá-la não representa aprovação financeira.

**Pausar novas entradas não suspende o gerenciamento já autorizado das posições abertas.** Enquanto a aplicação continuar funcionando, essas posições continuam sendo reconciliadas e podem ter saída antecipada por sua política salva. Encerrar a aplicação interrompe essa avaliação; SL e TP originais continuam nativos no MT5. Não foi implementado trailing nativo, alteração do SL, realização parcial intencional ou cancelamento do TP.

## Cálculo de lucro e limitações

O cálculo usa `order_calc_profit` do próprio MT5, Bid para fechar BUY e Ask para fechar SELL. Considera o preenchimento original confirmado, o fluxo financeiro já registrado no histórico da posição, swap aberto negativo, outra parcela de comissão/taxa observada na entrada como reserva de custo da saída e a reserva extra configurada. Crédito de swap aberto positivo não é antecipado na estimativa conservadora.

O preço estimado também inclui slippage adverso. O desvio solicitado no fechamento é limitado pelo menor entre a reserva de pontos configurada e o desvio geral do MT5. Essa solicitação não garante limite de execução em todos os modos de mercado/corretora. As tarifas futuras podem ser diferentes das estimadas.

Por isso, uma tentativa que parecia lucrativa pode resultar em valor menor ou até Loss. O código confirma e registra o resultado real, sem forçar a classificação para Win. O mínimo e as reservas são na moeda da conta, não convertidos automaticamente para dólares em contas de outra moeda.

## Proteções de execução e histórico

- O pedido de fechamento contém o **ticket atual da posição**, símbolo, lado oposto e volume integral confirmado. O ticket pode ser diferente do identificador persistente; ambos são tratados corretamente.
- Antes de `order_send`, uma intenção SUBMITTING é persistida. Se a gravação falhar, nenhuma ordem é enviada. A cotação, a posição, os custos e o gatilho são revalidados antes do envio.
- Conta, propriedade, volume, proteções e especificações inconsistentes impedem a saída adicional. O SL/TP existente não é removido para tentar fechar.
- Resposta inconclusiva, exceção ou ausência de resposta não provocam reenvio cego, inclusive depois de reiniciar. A intenção fica pendente para reconciliação.
- Novas entradas aguardam enquanto um fechamento está incerto. Os demais pares e as posições continuam sendo monitorados; a solicitação de automação não é desligada por isso.
- Rejeição explícita preserva a posição/SL/TP e não gera repetição da mesma solicitação naquela vela. Outra avaliação pode ocorrer em vela posterior.
- Se o broker executar apenas parte do volume, o saldo permanece aberto/protegido, com **FECHAMENTO PARCIAL • REVISAR**, sem Win fictício nem pedido residual repetido automaticamente. A reserva de risco não é liberada artificialmente. Uma situação inconclusiva persistente pode exigir revisão no terminal; não há um prazo depois do qual ausência de prova se torne autorização de reenvio.
- Quando o histórico confirma volume totalmente encerrado, o resultado inclui custos disponíveis. Se o fechamento foi por SL/TP ou ação manual, ele não é atribuído falsamente ao pedido antecipado. Uma saída parcial antecipada seguida de encerramento pelo histórico recebe causa combinada.

Diagnóstico: campos `profit_exit_policy`, `profit_exit_state` e `close_intent` no registro da operação; eventos EXIT com intenção, decisão, envio e confirmação; política selecionada em `/api/execution/diagnostics`. O painel mostra **FECHAMENTO EM CONFIRMAÇÃO** enquanto aguarda prova. O histórico persistente e os eventos continuam disponíveis após atualizações, inclusive com a compressão de diagnósticos introduzida anteriormente.

## Avaliação feita nesta entrega

As regras financeiras foram registradas no commit `a0ed3de` antes da avaliação. Houve comparação com TP/SL original nos períodos conhecidos de 20 a 23/10, usando a mesma base e controles. O simulador decide apenas com dados anteriores e preenche a saída na abertura seguinte, sem usar máximas/mínimas futuras. As amostras não são confirmação independente e não reclassificam as operações reais do usuário.

| Slippage por preenchimento | PnL original, soma dos quatro ensaios | PnL com saída antecipada | Acerto original → novo |
|---|---:|---:|---:|
| 2 pontos MT5 | −US$61,78 | −US$63,53 | 30,0% → 46,0% |
| 5 pontos MT5 | −US$65,23 | −US$20,41 | 26,7% → 56,1% |

**Mais Wins não provaram maior rentabilidade.** Com dois pontos, o resultado piorou ligeiramente; com cinco, o prejuízo diminuiu, mas continuou negativo. Em alguns dias, realizar antes reduziu perdas; em outros, cortou operações que depois atingiriam o TP. Não se deve escolher apenas a comparação favorável.

Todos os ensaios terminaram sem posições abertas. Cada dia iniciou com saldo sintético de US$10.000: os valores acima são somas de ensaios, não uma conta contínua. Spread por vela, comissão/swap zero assumidos, valores monetários/margem congelados e OHLC impõem limites. O funcionamento real monitora ticks entre as velas e pode ter desfechos diferentes. O dia reservado 19/10 não foi aberto.

Relatório completo: `research/profit_exit_results.md` e `.json`. Essa evidência não fundamenta afirmar que a função é mais garantida ou recomendar capital real por causa de uma taxa maior de Win.

## Aplicação no Windows

1. Confira que a CLI local não está modificando o projeto em paralelo.
2. Pause novas entradas e aguarde posições/intenções encerrarem e serem reconciliadas antes de atualizar/reiniciar. Preserve `data` e o diário SQLite.
3. Atualize a branch `work`, reinicie o backend pelo procedimento habitual e recarregue o painel com Ctrl+F5.
4. No modo MT5 Forex/LIVE_MT5 com conta hedge vinculada, habilite a nova opção na gestão de risco e mantenha os parâmetros desejados. Eles serão usados nas próximas entradas.
5. Confira a configuração em `/api/state` e `profit_exit` em `/api/execution/diagnostics`, depois use Iniciar MT5 quando desejar executar. Não há envio de ordem ao apenas salvar a configuração.

Nenhuma conexão ao terminal Windows nem ordem/mensagem real foi realizada nesta sessão de desenvolvimento. Os testes nativos usam SDK falso com forma equivalente ao MT5.

## Verificação de software

497 testes passaram, incluindo 36 específicos de saída antecipada. Os fluxos nativos foram exercitados com SDK falso em contas dos tipos real e demo/hedge: persistência antes do envio, confirmação por histórico, resultado negativo apesar de estimativa positiva, resposta perdida com/sem preenchimento, reinício, preenchimento parcial, ticket diferente de identificador, conta/permissão/volume/cotação inválidos, incapacidade de gravar a intenção e reserva global enquanto o fechamento aguarda confirmação. As falhas locais da avaliação têm espera curta para não repetir consultas incessantemente; SL/TP continuam nativos.

Controles, estados de fechamento pendente/parcial, preservação dos limites e ausência de ordens foram verificados em desktop e celular. Build do frontend aprovado. Esses testes comprovam comportamento de software, não expectativa financeira positiva.
