# Operação nativa no MT5 e bot Telegram

Esta versão mantém PAPER e acrescenta **LIVE_MT5**. LIVE_MT5 significa envio ao terminal: opera com dinheiro real se a conta conectada for real, e com saldo demonstrativo se a conta for demo. A Safirion continua em PAPER. O Telegram envia notificações e aceita `/status` e `/pausar`; não aceita comandos de compra, venda ou reativação.

## Preparar o computador Windows

1. Use Windows com Python **64 bits**, preferencialmente 3.12, e MetaTrader 5 instalado. O SDK Python oficial do MT5 não funciona nativamente neste ambiente Linux em nuvem. Execute o backend e o terminal no mesmo computador Windows; para uso contínuo, pode ser um VPS Windows.
2. Abra o terminal da sua corretora e entre na conta desejada. Confira conta e servidor. Para a primeira validação operacional, use uma conta demo.
3. No MT5, habilite a negociação algorítmica e permita negociação pela API Python externa nas opções de Expert Advisors. A nomenclatura depende da versão: a opção **“Desabilitar negociação automática pela API Python externa”** precisa ficar desmarcada. A conta também precisa permitir negociação por robôs; acesso de investidor/somente leitura não basta.
4. No terminal do projeto, atualize as dependências:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

O painel tem recursos compilados e não exige Node.js para o uso comum. Para desenvolver a interface: `npm ci --prefix frontend` e `npm run build --prefix frontend`.

O inicializador `iniciar_robo.bat` também pode ser usado. Mantenha uma única instância/worker por diário e uma única instância do robô por conta. A aplicação restringe os hosts a localhost/127.0.0.1 e rejeita alterações de navegador de outras origens; ela não fornece autenticação para exposição pública. Não publique sua porta diretamente na Internet.

## Conectar e habilitar o envio

1. No navegador desse computador, abra `http://127.0.0.1:8000`.
2. Clique em **Conexões**, deixe o MT5 aberto e conecte. Login e servidor podem ficar vazios quando o terminal já está conectado corretamente. Se houver vários terminais, informe o caminho completo do `terminal64.exe`. O caminho também pode ser definido em `MT5_TERMINAL_PATH` no `.env` local.
3. Selecione **MT5 Forex** ou **MT5 B3** na plataforma de dados.
4. Informe o **símbolo exato negociável da corretora**. Sufixos com `#` são aceitos: se `EURUSD` estiver desabilitado e `EURUSD#` tiver acesso completo nessa conta, informe `EURUSD#` no painel. EURUSD pode ter sufixos; para B3, use o contrato efetivamente negociável com vencimento. `WIN$N` e `WDO$N` podem ser apenas séries contínuas de análise e não contratos que aceitam ordens.
5. No campo **Modo de execução**, selecione **MT5 — enviar ordens ao terminal conectado**. O backend vincula login e servidor e a tela mostra conta, tipo e moeda. Não há fallback para PAPER quando o modo é MT5.
6. Confira os limites abaixo e clique em **INICIAR MT5** para permitir novas entradas automáticas. Os botões manuais também enviam ordens ao terminal quando esse modo está selecionado.

A seleção do modo fica salva; **a automação sempre reinicia pausada**. Posições e intenções pendentes são recuperadas e monitoradas mesmo com o robô pausado. Para trocar de conta, encerre/reconcilie as posições, pause, volte a PAPER, conecte a outra conta e selecione novamente MT5. Salvar um parâmetro não troca silenciosamente a conta vinculada.

## Erro: negociação algorítmica/API Python não autorizada

Receber preços do MT5 não comprova permissão para enviar ordens. A versão atual informa o campo exato que bloqueou a negociação, inclusive enquanto o painel permanece em PAPER:

| Campo bloqueado | Ação no Windows / MT5 |
| --- | --- |
| `terminal.trade_allowed=False` | Habilite o botão **AlgoTrading**. Em **Ferramentas > Opções > Expert Advisors**, marque **Permitir negociação algorítmica**. |
| `terminal.tradeapi_disabled=True` | Na mesma aba, **desmarque** **Desabilitar negociação automática pela API Python externa** (em inglês, **Disable automated trading through the external Python API**). A caixa marcada bloqueia a API, mesmo com AlgoTrading ligado. |
| `account.trade_allowed=False` | Confira se o login usa a **senha principal de negociação**. A senha de investidor é apenas leitura. A corretora também pode restringir a conta. |
| `account.trade_expert=False` | A conta não permite negociação por robôs. Solicite à corretora a verificação da permissão de Expert Advisors / API nessa conta. |

Clique em **OK**, mantenha o robô pausado, reconecte pelo painel e tente selecionar o modo MT5. Se houver vários terminais instalados, indique o caminho correto do `terminal64.exe` em **Conexões**; habilitar uma instalação diferente não resolve a permissão do terminal usado pela aplicação. Não é necessário anexar um EA ao gráfico para a API Python externa enviar ordens. Não é necessário habilitar DLLs para resolver esses quatro bloqueios.

Se ainda aparecer a mensagem genérica antiga, atualize a branch `work`, reinicie o backend e faça Ctrl+F5 no navegador. O diagnóstico está também em `http://127.0.0.1:8000/api/execution/diagnostics`, acessado **no seu computador**. Não compartilhe senhas nem tokens; basta o nome do campo bloqueado e sua mensagem.

O assistente na nuvem não possui controle remoto do Windows ou privilégios administrativos no seu computador. Pode alterar os arquivos do repositório; essas opções do MT5 precisam ser ajustadas localmente. A validação com SDK simulado não comprova as permissões efetivas da sua corretora.

Se o painel informar idade de tick negativa, confira **Configurações do Windows > Hora e idioma > Data e hora**, a hora automática e o fuso correto, e sincronize o relógio. Se o problema persistir com o relógio certo, confira a cotação/servidor do terminal. O robô recusa ticks significativamente no futuro, sem remover essa verificação para forçar ordens. Poucas velas disponíveis também impedem a validação de estratégia: carregue histórico no terminal para o símbolo/timeframe escolhido. Corrigir permissões não elimina esses filtros.

## Conectado, mas sem dados reais de EURUSD / M5

Login e autorização de negociação não garantem que o histórico de preços esteja disponível. Mantenha o robô pausado enquanto confere:

1. No MT5 correto, abra **Exibir > Observação do Mercado** (`Ctrl+M`). Confira o nome exato do símbolo oferecido pela conta, incluindo eventuais sufixos; use esse mesmo nome no painel.
2. Abra o gráfico desse símbolo, selecione **M5** e pressione **Home** algumas vezes para solicitar velas anteriores. Aguarde o carregamento e confira a conexão do terminal com o servidor. O robô solicita 240 velas; conseguir ler uma quantidade pequena ainda pode ser insuficiente para a estratégia.
3. Em **Ferramentas > Opções > Gráficos**, confira se o máximo de barras no gráfico permite pelo menos 240 velas; pode usar 5.000 ou mais. Siga a solicitação do terminal caso seja preciso reabrir o gráfico ou reiniciar para aplicar a alteração.
4. Reconecte no painel, mantendo a mesma conta e instalação, e confira uma mensagem com horário posterior à reconexão. Erros antigos permanecem no diário e não comprovam uma falha atual.

A aplicação atual identifica falha de seleção de símbolo e, quando a leitura retorna vazia, mostra o símbolo, período, quantidade solicitada e `last_error()` do MT5. No modo LIVE_MT5 essa informação aparece também na origem dos dados e no motivo de espera. Não usa dados artificiais para contornar a indisponibilidade. A API pode levar algum tempo para sincronizar o histórico; mercado fechado ou cotação antiga também pode impedir novas entradas, mesmo quando há histórico.

Se persistir, compartilhe a mensagem detalhada mais recente. Não é necessário reinstalar o pacote Python ou mudar novamente as permissões de AlgoTrading para investigar um erro de histórico.

## API retorna tick cerca de três horas no futuro

Alterar o fuso de um indicador ou a exibição do gráfico não altera os timestamps retornados pelo pacote Python. Quando uma consulta mostra o relógio UTC do computador correto e o tick aproximadamente três horas à frente, a aplicação precisa de uma configuração explícita para esse formato de timestamp. Não há correção automática por corretora, nem inferência de fuso a partir de uma cotação antiga.

Atualize os arquivos e reinicie o backend. Com automação pausada e sem posição pendente, em **Gestão de risco > Limites específicos do MT5**, há dois ajustes independentes:

- **Ajuste de timestamp do tick (minutos):** informe `180` se o tick estiver comprovadamente três horas à frente do UTC real. O valor é **subtraído** do timestamp recebido. Assim, computador `01:37:26 UTC` e tick bruto `04:37:21` tornam-se idade de aproximadamente 5 segundos, em vez de −10.795 segundos. Não use −180 nesse exemplo. A idade exata continua variando com o recebimento de novos ticks.
- **Ajuste de timestamp das velas (minutos):** o padrão continua `0`. Confira a **última vela bruta interpretada como UTC**, exibida na mesma seção. Só informe `180` se essa vela também estiver três horas à frente do UTC do computador, respeitando o período do gráfico. Exemplo: relógio `01:37 UTC`, última vela M5 bruta `04:35 UTC` → normalizada `01:35 UTC`. Se a vela já vier como `01:35`, mantenha `0`.

Os horários brutos, normalizados e UTC do computador também aparecem em `http://127.0.0.1:8000/api/execution/diagnostics`, no campo `clock`. Em caso de dúvida, compartilhe esse campo antes de alterar o ajuste das velas.

Os ajustes são salvos no diário, têm padrão zero e limites de −840 a +840 minutos. Não alteram o relógio do Windows, preços, spread ou distância dos stops. Aplicam-se aos dados de mercado: tick da entrada/saída PAPER, validação de tick LIVE, conversão cambial e velas da análise/gráfico. Não reinterpretam timestamps dos negócios no histórico da corretora. Mudanças de configuração são recusadas com automação ligada ou operação pendente.

Cotações antigas ou no futuro após a normalização continuam bloqueadas. A aplicação não deve ser usada para forçar uma cotação antiga a parecer recente. Confira a configuração ao mudar de corretora/servidor ou em mudança sazonal do fuso do servidor; a compensação não muda sozinha.

Receber 240 velas não garante 239 velas contínuas válidas: depois de uma lacuna de mercado, como o fim de semana, a análise usa o trecho posterior à última lacuna. Uma amostra insuficiente ainda bloqueia o plano ou o filtro de ML. Isso é separado da normalização de horário e não é resolvido mudando o fuso.

## Dimensionamento e encerramento

- No MT5, o campo de entrada representa **orçamento monetário de risco até o stop**, na moeda da conta, e não lotes. Com entrada 5 e conta em USD, o orçamento é de até 5 USD, também limitado pelo percentual de risco e pelo stop diário restante.
- O volume é calculado com `order_calc_profit`, respeitando lote mínimo, passo de volume, teto configurado e margem livre. Se o lote mínimo exceder o orçamento, a ordem é recusada em vez de arredondada para cima.
- O SL e TP combinam ATR e suporte/resistência de velas fechadas, respeitam retorno/risco mínimo após custos, tick e distância mínima do símbolo. São enviados junto da ordem. Forex/B3, tanto PAPER quanto LIVE_MT5, **não usa expiração por tempo**. Veja [meta diária e plano de saída](FOREX_META_DIARIA.md).
- Em **Limites específicos do MT5**, ajuste spread máximo, desvio permitido, teto de volume e reserva para custos. Spread/desvio usam **pontos do MT5**, não pips. A reserva deve refletir os custos observados na sua corretora; custos, gaps e slippage podem fazer a perda efetiva exceder a estimativa de SL.
- Uma posição existente ou ordem pendente no mesmo símbolo, mesmo manual, bloqueia outra abertura. Não aumente/reverta manualmente uma posição do robô: isso pode exigir reconciliação manual. O sistema não mistura nem fecha automaticamente posições de terceiros.
- **PAUSAR MT5** e `/pausar` impedem novas entradas automáticas. Não fecham posições já abertas e não impedem uma ordem manual solicitada pelo painel. Para encerrar uma posição, use o terminal MT5; o robô acompanhará o fechamento real.
- Stop diário, stop gain, sequência de perdas e quantidade diária controlam novas entradas; não são uma ordem de liquidação imediata da conta.

O robô registra a intenção no SQLite **antes** de enviar. Faz pré-validação por `order_check` e usa o preenchimento suportado pelo símbolo. Não repete automaticamente uma ordem recusada, parcialmente preenchida ou cuja resposta se perdeu. Nas duas últimas situações, aguarda posições/negócios confirmados.

O resultado vem do histórico de negócios da posição e inclui `profit`, `commission`, `swap` e `fee` presentes nesses negócios. Custos lançados separadamente pela corretora, sem vínculo com a posição, não podem ser atribuídos automaticamente. O PnL exibido é das operações rastreadas pelo robô, não o extrato de todas as operações manuais da conta. PAPER e MT5 usam históricos separados para cálculo dos limites.

## Criar o bot no BotFather

A criação da identidade do bot precisa ser feita por você na sua conta Telegram:

1. Abra o bot oficial **[@BotFather](https://t.me/BotFather)**, conferindo que é o oficial.
2. Envie `/newbot`.
3. Escolha um nome, por exemplo, “Meu ProspeTrader”.
4. Escolha um username disponível que termine em `bot`.
5. O BotFather entregará um **token do Bot API**. Ele permite controlar o bot. Não o coloque no Git, em capturas de tela, na URL do navegador ou nesta conversa. Não é necessário obter `api_id`/`api_hash` em my.telegram.org.

Use um bot dedicado a esta aplicação, sem outro serviço consumindo suas mensagens ou webhook ativo.

## Vincular o bot sem expor o token

Pare o servidor antes de executar o assistente, para que ele não dispute o recebimento de mensagens:

```powershell
.\.venv\Scripts\python.exe -m backend.telegram_setup
```

O assistente:

1. Solicita o token com entrada oculta.
2. Verifica o bot e mostra seu username.
3. Pede que você abra o bot no Telegram e envie `/start`.
4. Lista os chats privados encontrados e pede que você selecione **seu chat ID**.
5. Grava `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` no `.env` local, preservando as demais configurações. Esse arquivo é ignorado pelo Git. O token não vai para o SQLite nem para a resposta da API.

Reinicie o servidor. No painel, clique em **Testar Telegram**. O bot enviará uma mensagem ao chat configurado.

Alternativamente, use `.env.example` como referência e preencha o `.env` local com esses dois valores. Nunca compartilhe o token. Se ele vazar, revogue pelo BotFather e configure o novo valor localmente. Em ambiente de rede restrita, é necessário liberar HTTPS para **api.telegram.org**, mantendo verificação TLS.

Se quiser configurar o menu do Telegram, envie `/setcommands` ao BotFather, escolha seu bot e informe:

```text
status - Consultar modo, conta e resultado do robo
pausar - Pausar novas entradas automaticas
ajuda - Mostrar os comandos disponiveis
```

## O que o Telegram faz

- Notifica envio/estado incerto ou rejeição, preenchimento confirmado, fechamento com PnL confirmado, problemas de reconciliação/proteção e acionamento dos limites de risco.
- `/status`: mostra modo, conta, ativo, estado da automação, PnL realizado do dia UTC e posições/intenção pendentes.
- `/pausar` ou `/pause`: pausa novas entradas automáticas, mantendo acompanhamento das posições.
- `/ajuda`, `/help` ou `/start`: mostra os comandos.

Somente o chat privado configurado pode emitir comandos. Outros chats e grupos são ignorados. O bot não aceita `/comprar`, `/vender`, `/iniciar` ou `/resume`. As mensagens têm fila persistente e retentativas limitadas; uma falha de Telegram não interrompe a reconciliação MT5. Como em qualquer envio sujeito a perda de confirmação HTTP, uma mensagem pode se repetir. O terminal e o diário são as referências operacionais, não a chegada do aviso Telegram.

## Entender os estados e recuperar falhas

| Estado | Ação |
| --- | --- |
| SUBMITTING / PENDING | Envio persistido ou aguardando confirmação. Não repetir a entrada; use **Sincronizar posições MT5** e confira o terminal. |
| OPEN | Posição confirmada. O robô acompanha posição e histórico; não usa prazo PAPER para encerrá-la. |
| REJECTED | Rejeição explícita ou cancelamento sem preenchimento confirmado pelo histórico. Verifique o motivo antes de tentar outra entrada. |
| WIN / LOSS / DRAW | Posição fechada e resultado apurado pelo histórico do MT5. |
| Erro de conta/conexão | Reconecte a conta e servidor vinculados. O robô pausa novas entradas e mantém o registro pendente. |
| Proteções não confirmadas | Confira e ajuste imediatamente o SL/TP no terminal. A automação pausa; a posição não é apresentada como encerrada. |

Um envio incerto sem evidência suficiente permanece bloqueado. Não apague o diário para “destravar” e não reenvie pelo painel sem conferir o terminal. Se a corretora alterar os identificadores/comentários a ponto de impedir correlação, a apuração exige análise do histórico e do registro local. Ausência de posição em uma consulta isolada não prova que a ordem nunca foi executada.

Mantenha `data/paper.sqlite3` (ou o caminho em `TRADER_JOURNAL_PATH`) entre reinícios. Esse arquivo agora guarda tanto PAPER quanto MT5, com modos e contas identificados. Faça backup com o servidor parado; não copie um diário vazio sobre o diário de operações existentes.

## Validação disponível

Os testes automatizados usam um SDK MT5 controlado para exercitar envio, SL/TP, cálculo de risco, rejeição, preenchimento parcial, resposta perdida, reinício e fechamento com custos; Telegram usa transporte simulado. Isso não substitui a validação com a corretora.

Este ambiente Linux não possui o terminal MT5 Windows nem o token/chat do seu bot. Portanto não foi enviada uma ordem a uma corretora, nem uma mensagem ao seu Telegram durante a implementação. A etapa operacional pendente é conectar seu terminal Windows e seu bot e verificar esses fluxos na conta demo antes de usar uma conta real. Não há garantia de precisão dos sinais ou retorno financeiro.

## Diagnóstico persistente e recuperação contínua

Erros recuperáveis não desativam mais o pedido de automação. O serviço mantém monitoramento, alertas controlados e novas avaliações, respeitando os bloqueios de dados, risco e reconciliação. Consulte [Diagnóstico e recuperação](DIAGNOSTICO_RECUPERACAO.md) para interpretar os estados, consultar registros e exportar CSV. Ao encontrar descrição antiga de “robô pausado” em mensagens de proteção do histórico, interprete a operação como bloqueada para novas entradas; o estado atual de monitoramento é mostrado separadamente no painel e `/status`.
