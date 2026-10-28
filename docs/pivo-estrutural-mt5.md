# Pivô estrutural M1/M5 — execução automática MT5

Implementação solicitada pelo usuário em 24/10/2026, após o estudo do livro de Flávio Lemos. Identificador: `forex_structure_retest`. Política: `forex-structure-retest-v1`.

**É uma estratégia operacional selecionável, não modo sombra.** Pode preparar e enviar ordens automaticamente ao MT5 em conta real ou demo vinculada, mediante as permissões e os bloqueios existentes. Esta sessão de desenvolvimento não tem acesso ao terminal Windows do usuário; não iniciou o robô nem enviou ordens à corretora.

Disponibilidade técnica para execução não equivale a aprovação financeira. A estratégia não tem rentabilidade demonstrada. Os resultados exploratórios estão no relatório abaixo; não há promessa de aumentar a frequência ou a assertividade.

## Regras de entrada

Compra:

1. Confirmar fundo, topo e fundo mais alto — pivô 1-2-3. Cada extremo precisa de duas velas anteriores e duas posteriores, todas fechadas, para ser conhecido. Uma vela com máxima e mínima simultaneamente extremas é omitida porque a ordem intravela é desconhecida.
2. Exigir amplitude da primeira perna de pelo menos 1 ATR e até 60 velas entre os pontos 1 e 3.
3. Após o ponto 3 estar confirmado, aguardar o fechamento cruzar o ponto 2 mais 0,05 ATR. O ATR da confirmação do setup fixa as tolerâncias, evitando deslocar o critério a cada atualização.
4. Em uma vela posterior ao rompimento, observar o reteste da região de ±0,20 ATR em torno do ponto 2, com fechamento preservando essa região.
5. Em outra vela fechada, confirmar a retomada: corpo de alta, fechamento nos 35% superiores da vela, acima do nível rompido e da máxima da vela anterior mais 0,05 ATR do setup.
6. Exigir contexto M5 completo de alta, pelo cálculo existente de EMA9/21, inclinação da EMA lenta e preço.

Venda: regras espelhadas, com topo, fundo, topo mais baixo, rompimento/reteste e retomada de baixa; contexto M5 de baixa.

Janelas máximas: vinte velas para romper após a confirmação do pivô; doze para retestar após o rompimento; quatro para retomar após o primeiro reteste. Toque/violação do ponto 3 antes da entrada invalida o setup. Uma estrutura gera somente uma confirmação; rejeição por risco ou M5 não transforma o mesmo setup em um novo sinal tardio.

Todos os estágios são reconstruídos cronologicamente com as velas fechadas disponíveis. Não se confirma rompimento, reteste e retomada dentro da mesma vela, nem se registra retroativamente uma entrada no extremo de um pivô.

## Score, SL/TP e risco

- Score técnico: 80 pelos requisitos obrigatórios, mais dez por RSI favorável e dez por fechamento nos 25% favoráveis. O mínimo configurado continua sendo pelo menos 78. **Não é probabilidade calibrada de ganho.**
- Esta alternativa é técnica e não usa o Random Forest para autorizar entradas. O RF existente e seus critérios permanecem disponíveis em sua própria estratégia, sem alteração de treinamento ou validação.
- SL: invalidação no ponto 3, com piso/buffer ATR, distância máxima e regras de tick do planejamento comum. Um stop estrutural maior reduz o lote ou reprova a entrada; não aumenta o orçamento.
- TP: projeção ATR/R, limitada por obstáculo oposto confirmado quando houver. Retorno/risco líquido mínimo, spread, desvio, lote mínimo/passo, margem, permissões e pré-validação MT5 continuam obrigatórios.
- Para esta estratégia, a configuração exige stops e alvos estruturais habilitados. Não é possível selecioná-la desativando esses componentes.
- Orçamento global, até duas posições em pares distintos, exposição incerta, cooldown, limite de entradas, perda/meta diária e sequência de perdas permanecem sob os controles existentes. Nenhum martingale foi introduzido.
- Não foram adicionados trailing nativo, realização parcial ou stop temporal. As experiências anteriores de trailing continuam exclusivamente no laboratório.

Os parâmetros fixos e as regras foram registrados no commit `99f35f3`, antes da avaliação histórica desta implementação. Não houve ajuste das regras para melhorar os resultados observados.

## Diagnóstico e histórico

O painel principal e o Radar apresentam o estágio do setup. Os filtros recebem nomes próprios: pivô confirmado, contexto M5, integridade da estrutura, rompimento do ponto 2, reteste e retomada.

As análises guardam pontos e horários de confirmação, nível rompido, ATR de referência, invalidação, fase, horários de rompimento/reteste/retomada e identificador do setup no contexto de cada símbolo. Quando uma ordem é enviada, o diário já existente persiste o snapshot do sinal, a versão da política, o plano, o risco e a conta. Não foi alterado o esquema do diário nem apagado histórico de ganhos/perdas.

Consultar o Radar ou exportar diagnósticos continua sem enviar ordens. Nenhuma pesquisa roda continuamente em paralelo.

## Como selecionar na instalação Windows

1. Pause o robô e aguarde as posições/intenções pendentes encerrarem e serem reconciliadas, conforme o controle existente de alterações de configuração.
2. Atualize a branch `work` e reinicie o backend pelo procedimento habitual. Preserve a pasta `data` e seu diário SQLite.
3. Atualize a página com Ctrl+F5. No Setup, selecione **8. Pivô estrutural M1/M5 — rompimento e reteste**, mercado **MT5 Forex**, timeframe **M1**. A seleção salva a configuração existente pela API do painel.
4. Mantenha stops/alvos por estrutura habilitados e revise os limites de risco da conta. Os quatro símbolos exatos e a seleção individual permanecem na configuração multipares.
5. Confira no terminal qual conta está conectada. `LIVE_MT5` significa envio ao terminal; o tipo real/demo depende da conta vinculada, não da palavra LIVE no painel.
6. Use **Iniciar MT5** para executar automaticamente quando todas as condições forem atendidas. A atualização do código não inicia nem migra automaticamente a estratégia salva.

Em uma cópia do projeto já na branch `work`, a atualização é:

```powershell
git pull --ff-only origin work
```

Não é necessário importar uma base JSON ou executar o laboratório para o novo Setup poder analisar e enviar ordens. Os testes históricos desta entrega servem para mostrar comportamento e limitações; a seleção operacional não depende de uma execução do laboratório.

## Verificação

- 443 testes de software passaram, incluindo 24 específicos desta estratégia.
- Compras e vendas, confirmação causal, estágios em velas diferentes, invalidação, expiração, não repetição e bloqueio por contexto contrário foram verificados.
- O fluxo automático foi exercitado com SDK MT5 falso, tanto no tipo demo quanto no tipo real; nenhuma ordem foi enviada a um terminal real durante testes.
- Permissões, conta, spread, score, margem, stop máximo, até duas posições e orçamento global continuam bloqueando entradas inadequadas.
- A interface é verificada em desktop e celular num servidor isolado com envio de ordens proibido.

## Avaliação financeira

Ver `research/forex_structure_retest_results.md` e `.json`. Dados já estudados, custos assumidos e amostra curta: a avaliação não é uma confirmação independente nem demonstra rentabilidade futura. A disponibilidade em conta real foi implementada por solicitação expressa; não é uma recomendação de colocar capital real em uma estratégia com vantagem ainda não demonstrada.
