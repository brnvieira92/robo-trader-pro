# Correções após a auditoria Windows — 24/10/2026

A mudança de PAUSED para MONITORING durante a auditoria foi esclarecida pelo usuário: ele ativou o robô. Não foi tratada como falha e não foi adicionada nenhuma regra de ativação, pausa ou troca de estratégia.

Esta atualização corrige observabilidade, notificações e armazenamento. Não altera estratégia selecionada, critérios de entrada, SL/TP, orçamento, quantidade de posições ou PnL. Não houve acesso ao Windows, envio de ordens ou mensagens reais durante o desenvolvimento.

## Eventos por símbolo

Reconciliações, falhas e recuperações de uma posição passam a usar o símbolo, timeframe e conta da própria operação, mesmo quando o gráfico principal mostra outro par.

Na inicialização, depois de adquirir o bloqueio de instância, o backend tenta corrigir rótulos históricos de RECONCILIATION usando trade_id e confirmação de posição/conta. Casos sem correlação segura são preservados sem inferência. Eventos corrigidos conservam id, chave, horários, código e observações; o payload recebe context_repair com o rótulo original. Nenhuma linha financeira de trades é regravada por esse reparo.

O resultado aparece em GET /api/diagnostics/storage, campo reconciliation_context_repair. Uma indisponibilidade desse reparo é registrada e não encerra o processo.

## Telegram

Causa confirmada no código: a seleção de mensagens exigia attempts<5. Após cinco falhas uma mensagem permanecia sem envio, mas não era mais tentada. O contador operacional antigo media somente mensagens que ainda não tinham entrado no SQLite e escondia essa pendência.

Agora:

- Mensagens persistidas continuam elegíveis depois da quinta falha, com espera progressiva limitada a cinco minutos para falhas comuns. Telegram 429 respeita espera adicional informada pelo serviço, limitada a uma hora.
- Mensagens confirmadas como enviadas permanecem marcadas e não são selecionadas novamente. Notificações com cinco ou mais falhas recebem no envio um aviso de que o conteúdo é antigo; o texto original do banco permanece preservado.
- O status público inclui pending, queue.pending, queue.retrying, queue.attempts_at_least_five e queue.next_retry_at. Com Telegram desabilitado, não afirma que tentativas estão ativas. Se a consulta ao banco falhar, pending é null, não zero.
- operational.pending_notifications inclui fila persistida e mensagens em memória ainda não gravadas; pending_notification_saves separa estas últimas. Falhas de entrega não bloqueiam nem autorizam operações financeiras.
- Polling bem-sucedido não apaga um erro de entrega ainda ativo.
- last_error_code distingue TLS_CERTIFICATE_VERIFY_FAILED, TLS_ERROR, DNS_RESOLUTION, NETWORK_TIMEOUT, NETWORK_REFUSED, NETWORK_ERROR, AUTHENTICATION, FORBIDDEN, POLLING_CONFLICT, RATE_LIMIT, entre outros.
- Exceções de transporte e corpos de erro não são reproduzidos: podem conter URL com token. As mensagens públicas são controladas e não expõem credenciais.
- A validação TLS permanece obrigatória. TELEGRAM_CA_FILE é uma opção de arquivo PEM adicional de autoridades confiáveis, carregado sobre os certificados padrão; não desativa a validação nem substitui uma análise da origem do certificado.

O ambiente de desenvolvimento não pode confirmar a causa de rede/certificado da instalação Windows. Depois de atualizar e reiniciar, consulte GET /api/telegram/status para identificar a categoria concreta. Certificado inválido requer conferir relógio, autoridades confiáveis e eventual inspeção HTTPS de proxy/antivírus. Não use verify=False, certificados aleatórios ou desative verificações para contornar o erro.

Envio remoto tem semântica de pelo menos uma tentativa: se o Telegram receber uma mensagem mas sua resposta se perder, uma repetição pode ocorrer. O código não promete entrega exatamente uma vez em falhas de transporte. O endpoint POST /api/telegram/test envia mensagem real e não foi usado nesta verificação.

## Identificação do backend

GET /api/runtime retorna PID, horário UTC de carregamento, git_head_at_load, tracked_changes_at_load, backend_fingerprint_at_load e backend_files_match_load. Os dados de carregamento não mudam quando se atualiza o Git com o processo ainda aberto.

Isso permite distinguir código no disco de processo que precisa ser reiniciado. É uma identificação de origem capturada ao carregar o backend, não uma prova do SHA exato de cada objeto Python em memória. O fingerprint considera somente arquivos Python do backend, sem ler .env ou dados de credenciais.

## Crescimento do banco

Novos payloads de diagnóstico grandes são armazenados de forma comprimida e sem perda. Operações financeiras, configurações e fila Telegram continuam em seus formatos originais. Consultas da API, relatórios por par e CSV decodificam o conteúdo automaticamente; a semântica dos dados exportados permanece a mesma.

O armazenamento interno pode começar com PTD-ZLIB-JSON-1:. Consultas SQL próprias devem usar backend.diagnostic_codec.decode_payload em vez de json.loads diretamente sobre diagnostic_events.payload. Versões antigas do código não conhecem esse formato; mantenha o código compatível ao consultar o banco e preserve backups em qualquer procedimento de reversão.

Amostra verificada: mil eventos de um CSV histórico enviado pelo usuário, 13.629.552 bytes de payload antes e 4.807.968 bytes codificados depois, redução de 64,72%; os mil objetos decodificados foram comparados integralmente. Isso mede payloads dessa amostra, não a redução garantida do arquivo SQLite de 972 MB.

Não há expurgo de operações, análises ou eventos antigos. O arquivo existente não é comprimido nem submetido a VACUUM automaticamente durante a negociação. GET /api/diagnostics/storage mostra tamanho do banco, WAL e páginas reutilizáveis sem varrer todos os payloads.

### Manutenção opcional do arquivo existente

O comando de compactação exige backend encerrado, ausência de posições/intenções pendentes no diário, arquivo de backup novo e espaço livre de pelo menos três vezes o tamanho atual nos volumes envolvidos. Ele usa o mesmo bloqueio de instância da aplicação; falha se outro servidor estiver usando o diário. Uma aplicação pausada, mas ainda aberta, continua segurando esse bloqueio.

1. Pause entradas e aguarde encerramento e reconciliação de todas as posições. Não interrompa o backend com posições pendentes apenas para executar manutenção.
2. Encerre o backend pelo procedimento habitual.
3. Na pasta do projeto, consulte os metadados:

```powershell
.venv\Scripts\python.exe -m backend.journal_maintenance inspect --journal data\paper.sqlite3
```

4. Para compactar, escolha um nome de backup que ainda não exista:

```powershell
.venv\Scripts\python.exe -m backend.journal_maintenance compact --journal data\paper.sqlite3 --backup data\backups\antes-compactacao-20261024.sqlite3
```

O comando cria backup consistente do SQLite, inclusive dados no WAL; converte payloads antigos em lotes; executa VACUUM e verifica que o número de eventos e os conteúdos de trades, settings e telegram_outbox continuam iguais. Não exclui registros, não renomeia operações e não envia nada ao MT5/Telegram. O backup continua intacto se a manutenção falhar. Não apague um WAL manualmente.

5. Confira o resultado e reinicie o backend. Ele continua iniciando pausado, conforme a regra existente. Não reative automaticamente se houver uma falha de verificação de preservação; nesse caso, revise o backup antes de continuar.

Use --journal com o caminho correto se a instalação utiliza um diário personalizado. O comando inspect é leitura de metadados; não substitui uma verificação integral de integridade do SQLite.

## Aplicação e conferência no Windows

Primeiro confira que a CLI local não tem alterações em andamento. Pause o robô, aguarde posições/intenções encerrarem e atualize a branch work sem apagar alterações locais ou a pasta data. Reinicie o backend pelo procedimento habitual e atualize o navegador com Ctrl+F5. A correção de rótulos e o novo tratamento da fila passam a valer após carregar a nova versão; a compactação histórica é um passo separado e opcional.

Consultas de conferência, uma linha por vez no PowerShell:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/runtime' | ConvertTo-Json -Depth 5
```

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/telegram/status' | ConvertTo-Json -Depth 5
```

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/diagnostics/storage' | ConvertTo-Json -Depth 5
```

A confirmação da conexão Telegram deve ser feita nessa instalação. O código melhora a recuperação e mostra a causa; não pode instalar uma autoridade certificadora ausente no Windows a partir desta sessão remota.

## Verificação da entrega

A suíte completa passou com 461 testes. Foram verificados correlação histórica conservadora, contexto por operação, preservação de PnL, leitura de formatos antigo/novo, limites de descompressão, retomada de mensagens com cinco falhas, backoff, categorias de erro sem exposição de token, validação TLS, contagem persistida, bloqueio da manutenção com servidor/posições pendentes, backup e preservação de configurações/fila financeira. Build do frontend e interface em desktop/celular passaram em servidor isolado, sem chamadas de negociação ou Telegram reais.
