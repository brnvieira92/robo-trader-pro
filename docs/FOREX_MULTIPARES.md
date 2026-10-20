# Motor e Radar Forex multipares

O modo multipares é opcional. Configurações existentes continuam no modo individual até a ativação explícita. O motor monitora EURUSD, USDJPY, GBPUSD e USDCHF, cada um com seu símbolo exato e seleção independente. Não exige quatro pares ativos.

## Ativar na sua conta demo

1. Pause o robô. Aguarde qualquer posição, ordem ou intenção pendente ser reconciliada. Pausar impede entradas, mas não fecha posições.
2. No MT5, confira a especificação de cada símbolo. Negociação precisa permitir a direção desejada. Receber cotações não significa poder negociar. Na sua conta, o EURUSD sem sufixo estava desativado; EURUSD# tinha acesso completo. Confira os demais separadamente, sem presumir que usam o mesmo sufixo.
3. No aplicativo, mantenha MT5 Forex, modo MT5, conta/servidor corretos, estratégia **IA Pura — Machine Learning**, filtro ML ativo e **78%** ou mais. O timeframe do Setup é compartilhado pelos pares; para manter a configuração atual, deixe **M1**. O timeframe do gráfico aberto no terminal não controla a API do robô.
4. Abra **Radar Forex → Configurar pares monitorados**. Marque apenas os pares desejados e informe os códigos exatos da corretora, por exemplo `EURUSD#`. Marque **Ativar motor multipares automático** e clique **Salvar seleção de pares**. Essa operação não inicia o robô nem diminui filtros. O símbolo do gráfico precisa estar na seleção; ao removê-lo, o formulário passa o foco ao primeiro selecionado.
5. Confirme no Radar os dados, permissões, validação ML e motivos de bloqueio. Clique **Iniciar MT5** para solicitar a execução automática. Não há confirmação individual por operação.

Para atualizar uma instalação local que acompanha a branch `work`, pause antes o servidor e as entradas, preserve seu `.env` e seu diário SQLite, e use `git pull origin work`. Se estiver em outra branch ou houver alterações locais, confira `git status` antes de integrar a branch. Reinicie o servidor com o Python da `.venv` e recarregue a página. A atualização não altera opções no terminal Windows.

## Decisões e orçamento

Cada par tem instância própria de Random Forest, cache, estado de análise, último candle operado e recuperação. O identificador do modelo inclui conta, servidor, par, timeframe, fonte, parâmetros e versão do conjunto de dados. Não há compartilhamento de treinamento entre pares. Continuam valendo a divisão cronológica, purga da fronteira, critérios de acurácia/referência, acurácia balanceada, Brier, direção ML, volatilidade e score mínimo.

As análises são sequenciais. A API Python MT5 não recebe chamadas concorrentes do motor e da interface. Depois da varredura, o coordenador considera candidatos elegíveis em rodízio, revalida os filtros e a exposição e prepara uma única ordem. Rejeição de pré-validação local permite avaliar outro par. Um envio aceito ou inconclusivo ocupa a vaga global; a reconciliação precisa resolver essa intenção antes de qualquer nova entrada. Não existe fila de sinais antigos para operar após uma posição encerrar.

O orçamento não é multiplicado pelo número de pares: dimensionamento, perda diária, meta, sequência de perdas, número de entradas e intervalo entre operações permanecem globais. A reserva SQLite é atômica e comum a todos os símbolos. Qualquer posição ou ordem encontrada na conta, inclusive manual e fora dos quatro pares, impede nova entrada automática. Falha ao consultar exposição também bloqueia todas as novas entradas.

Antes de enviar, o executor consulta a especificação real de cada símbolo: point, tamanho do tick, dígitos, stops mínimos, volume mínimo/máximo/passo, filling e permissão de direção. Lucro/perda e margem são calculados pelas funções da corretora. Não se presume que um lote ou pip de USDJPY equivale ao de EURUSD. O limite de spread continua expresso em **pontos MT5 do símbolo**, não pips.

O PnL e os limites do diário contabilizam as operações identificadas do robô; não substituem um controle financeiro completo de operações manuais externas. A presença de exposições externas bloqueia novas entradas, em vez de estimar um risco desconhecido.

## Recuperação e estados

- **DESATIVADO:** par não selecionado; não é analisado.
- **PAUSADO:** dados monitorados, sem autorização de executar.
- **BLOQUEADO:** um ou mais critérios impedem a entrada. Pode existir sinal direcional sem aprovação de risco/validação.
- **CANDIDATO:** passou a análise e será revalidado pelo coordenador. Não significa ordem enviada.
- **ERRO:** falha local de análise/especificação; os demais pares continuam. Retentativas usam espera de 5 a 60 segundos.
- **POSIÇÃO/INTENÇÃO:** a vaga global está ocupada, inclusive enquanto a confirmação do envio estiver incerta.

Falhas de conta, consulta da exposição, persistência e resultados de envio não confirmados bloqueiam novas entradas de todos os pares. O processo mantém monitoramento/reconciliação e recuperação. Alertas de erro e recuperação usam o Telegram configurado, com limitação de frequência. Falha no Telegram não autoriza operações nem encerra o motor. Pausa explícita continua válida; reiniciar o processo deixa a execução pausada e conserva intenções pendentes. Nenhum software pode garantir execução contínua se o Windows, terminal ou processo forem encerrados.

## Diagnóstico sem influenciar as operações

O Radar exibe somente projeções em cache do motor: sinal, confiança ML, score do sinal, validação, qualidade dos dados, especificação, bloqueio local/global e horário da análise. Atualizar a página ou consultar `GET /api/forex/radar` e `GET /api/state` no modo multipares não executa modelos, não chama o SDK e não gera ordens ou novos registros. Não há botões de compra/venda no Radar; as ordens manuais legadas ficam indisponíveis no modo multipares.

Os registros `ANALYSIS` preservam todos os filtros da análise; `RADAR` registra também disponibilidade/especificação e bloqueios do coordenador. `DECISION` registra pré-validação, plano, volume e rejeições; `EXECUTION` e `RECONCILIATION` registram intenção e resultado. As relações usam símbolo exato, timeframe, candle e `analysis_id`. Observações repetidas do mesmo evento podem ser agregadas; não representam trades adicionais. Use **Exportar diagnóstico deste símbolo** ou a API paginada:

```powershell
# O # do sufixo precisa ser codificado na URL.
$simbolo = [uri]::EscapeDataString('EURUSD#')
Invoke-RestMethod "http://127.0.0.1:8000/api/diagnostics/events?symbol=$simbolo&limit=100"
Invoke-RestMethod "http://127.0.0.1:8000/api/forex/radar" | ConvertTo-Json -Depth 12
```

Um período sem operações é compatível com validação reprovada, score abaixo de 78%, cotações antigas, spread, stops, retorno/risco, lote mínimo ou limites globais. Mais pares ampliam o conjunto analisado; não garantem sinais aprovados nem lucro. A validação direcional de uma vela continua sem demonstrar, por si só, rentabilidade de SL/TP Forex.

## Verificação em demo

Testes automatizados usam SDK falso e diários temporários. Eles cobrem isolamento, falha por par, bloqueios globais, reserva única, envio inconclusivo, reinício, especificações diferentes e consultas sem efeitos. A interface é verificada em desktop e celular. Isso não equivale a executar ordens no terminal Windows da XM.

Na sua demo, primeiro selecione só o símbolo já validado. Confira dados e motivos no Radar, depois acrescente um par por vez. Compare os CSVs no mesmo período e mantenha os filtros. Na primeira operação aprovada, confira no MT5 símbolo, volume, SL/TP e correspondência com o diário. Durante uma posição, os outros pares precisam continuar sendo analisados e permanecer bloqueados pelo limite global. Se houver falha de comunicação após envio, aguarde a reconciliação; não repita manualmente a ordem.

Para voltar ao modo individual, pause, aguarde a resolução de posições/intentos, desmarque o motor multipares e salve. Conta, parâmetros e diário são preservados.

### Reproduzir os testes no ambiente de desenvolvimento Linux

```bash
.venv/bin/python -m pytest -q
npm run build --prefix frontend
# Terminal separado: fixture temporária, API falsa, sem conexão ao terminal/Telegram.
PYTHONPATH=. .venv/bin/python tests/multi_browser_server.py
# Outro terminal:
.venv/bin/python tests/browser_multi.py http://127.0.0.1:8011
```

A fixture de navegador inicia pausada, cria e remove seu próprio diário temporário e impede `order_send`. Não use o teste de seleção de configuração contra sua instalação operacional.
