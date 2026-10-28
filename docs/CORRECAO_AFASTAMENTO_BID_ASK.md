# Correção do afastamento entre sinal e execução — 26/10/2026

O diagnóstico do USDCHF de 26/10 mostrou candidatos BUY rejeitados por
`ENTRY_DRIFT`, inclusive com Bid observado igual ao fechamento Bid do sinal.
O planejador comparava o Ask de compra diretamente ao fechamento Bid e,
portanto, classificava o spread como deslocamento posterior ao sinal.

## Regra corrigida

- Referência: fechamento Bid da vela que produziu o sinal (`indicators.price`).
- Bid atual para BUY: preço Ask de execução menos o spread atual.
- Bid atual para SELL: próprio preço Bid de execução.
- A distância absoluta Bid/Bid continua limitada ao `max_entry_drift_atr`
  configurado, em ambas as direções. Apenas tolerância numérica de ponto
  flutuante é aplicada na fronteira exata.

O spread continua sendo pago: compra no Ask, venda no Bid, SL/TP nos preços
apropriados de encerramento e dimensionamento pelo resultado financeiro
estimado pelo MT5. Permanecem os limites de spread absoluto, spread/stop,
distância máxima de stop, retorno/risco, cotação recente, lote, margem,
exposição global e capacidade de posições. Não há aumento de tolerância,
risco, score nem mudança no padrão estrutural de entrada.

Um BUY que passe a ser elegível por esta correção ainda precisa passar pelos
demais filtros. Isso pode aumentar as compras analisadas pela pré-validação;
não demonstra aumento de lucro ou de taxa de acerto.

## Diagnósticos e laboratório

`plan.entry_drift` e `entry_diagnostics.entry_drift` registram base Bid,
fechamento de referência, Bid atual, preço de execução, spread, distância em
preço/ATR e limite. Rejeições `ENTRY_DRIFT` também persistem esses valores no
evento DECISION. Isso permite separar movimento real de custo de execução.

O planejador comum é usado na execução nativa e no laboratório. A versão de
preços passa a `bid-ask-structure-v3` e o resultado do laboratório a versão 6.
O estudo de saída antecipada não reutiliza baselines de versões anteriores.
Relatórios históricos já produzidos não são reescritos e não comprovam o
desempenho desta versão. Os arquivos recebidos não trazem todo o OHLC/ticks
necessário para reproduzir financeiramente as compras rejeitadas em 26/10.

Não foi acrescentado um novo filtro de extensão com limiar escolhido a partir
da perda do USDCHF. Essa hipótese precisa de teste com dados de candles e
custos antes de modificar a estratégia.

## Aplicação no Windows

Atualize a branch `work` e reinicie o backend para carregar a correção.
Os offsets de horário continuam sendo configuração da instalação: o ajuste
Bid/Ask não os modifica. Histórico de operações, ganhos/perdas e posições
existentes não são migrados, apagados ou encerrados por esta alteração.
Nenhuma ordem real é necessária para executar os testes automatizados.

## Validação

`pytest -q`: 520 testes aprovados. Os 23 novos casos cobrem simetria BUY/SELL,
fronteira exata de um ATR, rejeição de afastamento verdadeiro, evidência
numérica persistida, manutenção dos bloqueios nativos e aplicação da mesma
regra no simulador histórico. O SDK dos testes é simulado; nenhum envio ao
terminal Windows ou ao Telegram foi realizado.
