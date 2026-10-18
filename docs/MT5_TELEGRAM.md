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
4. Informe o **símbolo exato negociável da corretora**. EURUSD pode ter sufixos; para B3, use o contrato efetivamente negociável com vencimento. `WIN$N` e `WDO$N` podem ser apenas séries contínuas de análise e não contratos que aceitam ordens.
5. No campo **Modo de execução**, selecione **MT5 — enviar ordens ao terminal conectado**. O backend vincula login e servidor e a tela mostra conta, tipo e moeda. Não há fallback para PAPER quando o modo é MT5.
6. Confira os limites abaixo e clique em **INICIAR MT5** para permitir novas entradas automáticas. Os botões manuais também enviam ordens ao terminal quando esse modo está selecionado.

A seleção do modo fica salva; **a automação sempre reinicia pausada**. Posições e intenções pendentes são recuperadas e monitoradas mesmo com o robô pausado. Para trocar de conta, encerre/reconcilie as posições, pause, volte a PAPER, conecte a outra conta e selecione novamente MT5. Salvar um parâmetro não troca silenciosamente a conta vinculada.

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
