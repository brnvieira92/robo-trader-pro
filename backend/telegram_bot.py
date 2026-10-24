"""Owner-only Telegram alerts, /status and /pausar. No credentials in logs.

The BotFather token is loaded from the environment, never stored in SQLite or
returned by the API. Telegram errors never interfere with order reconciliation.
"""
import asyncio
import json
import os
import sqlite3
import ssl
import socket
import time
import urllib.request
import urllib.error

class TelegramError(ValueError):
    def __init__(self,message,code='TEMPORARY_ERROR',retry_after=None):
        super().__init__(message)
        self.code=code
        self.retry_after=retry_after


class TelegramAPI:
    def __init__(self, token):
        self._token=token
        self.ca_file=os.environ.get('TELEGRAM_CA_FILE','').strip() or None

    def call(self, method, payload=None):
        request=urllib.request.Request('https://api.telegram.org/bot'+self._token+'/'+method,
                                       data=json.dumps(payload or {}).encode(),headers={'Content-Type':'application/json'})
        try:
            context=ssl.create_default_context()
            if self.ca_file:context.load_verify_locations(cafile=self.ca_file)
        except (OSError,ValueError,ssl.SSLError):
            raise TelegramError('Autoridade certificadora adicional inválida; confira TELEGRAM_CA_FILE. TLS permanece obrigatório.','TLS_CA_CONFIGURATION',300) from None
        try:
            with urllib.request.urlopen(request,timeout=15,context=context) as response:
                data=json.load(response)
        except urllib.error.HTTPError as exc:
            code=exc.code
            categories={400:('BAD_REQUEST','Telegram recusou os dados da solicitação.'),401:('AUTHENTICATION','Telegram recusou a autenticação. Confira a configuração local do bot.'),
                403:('FORBIDDEN','Telegram recusou acesso ao chat. Confira permissões e se o bot foi bloqueado.'),
                409:('POLLING_CONFLICT','Conflito de polling Telegram. Verifique outra instância ou webhook do mesmo bot.'),
                429:('RATE_LIMIT','Limite de requisições Telegram; nova tentativa será adiada.')}
            name,message=categories.get(code,('HTTP_SERVER' if code>=500 else 'HTTP_ERROR',f'Telegram respondeu HTTP {code}.'))
            delay=300 if code in (400,401,403,409) else None
            if code==429:
                try:
                    value=json.load(exc).get('parameters',{}).get('retry_after')
                    if isinstance(value,(int,float)) and not isinstance(value,bool):delay=min(3600,max(1,int(value)))
                except (OSError,ValueError,TypeError,AttributeError,OverflowError):pass
            raise TelegramError(message,name,delay) from None
        except (OSError,ValueError,TimeoutError) as exc:
            reason=getattr(exc,'reason',exc)
            if isinstance(reason,ssl.SSLCertVerificationError):
                code,message='TLS_CERTIFICATE_VERIFY_FAILED','Certificado TLS do Telegram não pôde ser validado. Confira relógio do Windows, certificados confiáveis e proxy/antivírus HTTPS; não desative a verificação TLS.'
            elif isinstance(reason,ssl.SSLError):code,message='TLS_ERROR','Falha na negociação TLS com Telegram. Confira certificados e proxy HTTPS.'
            elif isinstance(reason,socket.gaierror):code,message='DNS_RESOLUTION','Falha de DNS ao resolver api.telegram.org.'
            elif isinstance(reason,(TimeoutError,socket.timeout)):code,message='NETWORK_TIMEOUT','Tempo limite ao conectar ao Telegram.'
            elif isinstance(reason,ConnectionRefusedError):code,message='NETWORK_REFUSED','Conexão com Telegram recusada pela rede ou proxy.'
            elif isinstance(exc,ValueError):code,message='INVALID_RESPONSE','Resposta inválida recebida do Telegram.'
            else:code,message='NETWORK_ERROR','Telegram indisponível por falha de rede/proxy. Consulte o diagnóstico de conexão local.'
            # Never stringify transport exceptions: they can contain token URLs.
            raise TelegramError(message,code,300 if code.startswith('TLS') else None) from None
        if not isinstance(data,dict) or not data.get('ok'):
            raise TelegramError('Resposta de sucesso do Telegram não confirmada.','INVALID_RESPONSE')
        return data.get('result')

class TelegramBot:
    def __init__(self, journal, status_callback, pause_callback):
        token=os.environ.get('TELEGRAM_BOT_TOKEN','').strip()
        self.chat_id=os.environ.get('TELEGRAM_CHAT_ID','').strip()
        self.enabled=bool(token and self.chat_id and self.chat_id.lstrip('-').isdigit())
        self.api=TelegramAPI(token)
        self.journal=journal
        self.status_callback=status_callback
        self.pause_callback=pause_callback
        self.last_error=None
        self.connected=False
        self.last_error_code=None
        self.last_success_at=None
        self._errors={}
        self._poll_retry_at=0.
        self._task=None
        self._wake=asyncio.Event()
        # Dedicated bot: the cursor is namespaced per destination; never save the token.
        self.offset_key='telegram_offset:'+self.chat_id
        with journal.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS telegram_outbox (id TEXT PRIMARY KEY, chat TEXT NOT NULL, text TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0, sent INTEGER NOT NULL DEFAULT 0)')
            db.execute('CREATE INDEX IF NOT EXISTS telegram_pending ON telegram_outbox(chat,sent,next_try)')

    def _failure(self,channel,error):
        self._errors[channel]=(str(error),getattr(error,'code','TEMPORARY_ERROR'))
        self.last_error,self.last_error_code=self._errors[channel]
        self.connected=False

    def _success(self,channel):
        self._errors.pop(channel,None)
        self.last_success_at=time.time()
        self.connected=not self._errors
        self.last_error,self.last_error_code=next(iter(self._errors.values()),(None,None))

    def public_status(self):
        now=time.time()
        try:
            with self.journal.connection() as db:
                pending,retried,legacy,next_try=db.execute('SELECT COUNT(*),COALESCE(SUM(attempts>0),0),COALESCE(SUM(attempts>=5),0),MIN(next_try) FROM telegram_outbox WHERE chat=? AND sent=0',(self.chat_id,)).fetchone()
            queue=dict(pending=pending,retrying=retried,attempts_at_least_five=legacy,
                next_retry_at=max(now,next_try) if next_try is not None and self.enabled else None,
                automatic_retry=self.enabled,error=None)
        except sqlite3.Error:
            pending=None
            queue=dict(pending=None,error='Não foi possível consultar a fila persistida.',automatic_retry=self.enabled)
        return dict(configured=self.enabled,connected=self.connected,last_error=self.last_error,
                    last_error_code=self.last_error_code,last_success_at=self.last_success_at,
                    pending=pending,queue=queue,tls_verification=True,additional_ca_configured=bool(getattr(self.api,'ca_file',None)),
                    commands=['/status','/pausar','/ajuda'])

    def enqueue(self, event_id, text):
        if not self.enabled:
            return True
        try:
            with self.journal.connection() as db:
                db.execute('INSERT OR IGNORE INTO telegram_outbox (id,chat,text) VALUES (?,?,?)',
                           (event_id,self.chat_id,text[:3900]))
        except sqlite3.Error:
            self.last_error='Fila de notificações indisponível. Execução MT5 segue com seu próprio diário.'
            return False
        self._wake.set()
        return True

    async def test(self):
        if not self.enabled:
            raise TelegramError('Defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env e reinicie o servidor.')
        try:
            me=await asyncio.to_thread(self.api.call,'getMe')
            await asyncio.to_thread(self.api.call,'sendMessage',dict(chat_id=self.chat_id,text='ProspeTraderBot: Telegram conectado. Use /status e /pausar.'))
        except TelegramError as exc:
            self._failure('delivery',exc)
            raise
        self._success('delivery');self._success('poll')
        return dict(success=True,username=me.get('username'),message='Mensagem de teste enviada ao chat configurado.')

    async def handle_update(self, update):
        message=update.get('message') or {}
        chat=message.get('chat') or {}
        if str(chat.get('id'))!=self.chat_id or chat.get('type')!='private' or message.get('from',{}).get('is_bot'):
            return
        text=str(message.get('text') or '')
        command=text.split()[0].split('@')[0].lower() if text.strip() else ''
        if command=='/status':
            reply=self.status_callback()
        elif command in ('/pausar','/pause'):
            self.pause_callback()
            reply='Robô pausado. Nenhuma nova entrada automática. Posições já abertas continuam com SL/TP e monitoramento no MT5; não foram fechadas por este comando.'
        elif command in ('/start','/ajuda','/help'):
            reply='ProspeTraderBot\n/status — modo, conta, risco e operações\n/pausar — pausa novas entradas automáticas\nOrdens e reativação são feitas somente no painel local.'
        else:
            return
        self.enqueue('command:'+self.chat_id+':'+str(update['update_id']),reply)

    async def _deliver(self):
        with self.journal.connection() as db:
            row=db.execute('SELECT id,text,attempts FROM telegram_outbox WHERE chat=? AND sent=0 AND next_try<=? ORDER BY next_try,rowid LIMIT 1',
                           (self.chat_id,time.time())).fetchone()
        if row is None:
            return
        event_id,text,attempts=row
        if attempts>=5:
            text=('Mensagem pendente reenviada. O conteúdo reflete o momento original; não confirma o estado atual.\n\n'+text)[:3900]
        try:
            await asyncio.to_thread(self.api.call,'sendMessage',dict(chat_id=self.chat_id,text=text))
        except TelegramError as exc:
            self._failure('delivery',exc)
            delay=max(min(300,2**min(attempts+2,9)),exc.retry_after or 0)
            with self.journal.connection() as db:
                db.execute('UPDATE telegram_outbox SET attempts=?,next_try=? WHERE id=?',(attempts+1,time.time()+delay,event_id))
        else:
            with self.journal.connection() as db:
                db.execute('UPDATE telegram_outbox SET sent=1 WHERE id=?',(event_id,))
            self._success('delivery')

    async def run(self):
        polling_ready=False
        while True:
            try:
                if time.time()<self._poll_retry_at:
                    await asyncio.sleep(1)
                    continue
                if not polling_ready:
                    webhook=await asyncio.to_thread(self.api.call,'getWebhookInfo')
                    if webhook.get('url'):
                        raise TelegramError('Este bot tem webhook ativo. Use um bot dedicado ou remova o webhook conscientemente antes de ativar o polling.','WEBHOOK_ACTIVE',300)
                    polling_ready=True
                await self._deliver()
                updates=await asyncio.to_thread(self.api.call,'getUpdates',dict(offset=self.journal.setting(self.offset_key,0),timeout=0,allowed_updates=['message']))
                for update in updates:
                    await self.handle_update(update)
                    self.journal.set_setting(self.offset_key,int(update['update_id'])+1)
                self._success('poll')
            except TelegramError as exc:
                self._failure('poll',exc)
                self._poll_retry_at=time.time()+max(15,exc.retry_after or 0)
            except Exception:
                self._failure('poll',TelegramError('Falha interna no bot Telegram; consulte o diagnóstico local.','INTERNAL_ERROR'))
                self._poll_retry_at=time.time()+15
            try:
                await asyncio.wait_for(self._wake.wait(),timeout=3 if self.connected else 15)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def start(self):
        if self.enabled:
            self._task=asyncio.create_task(self.run())

    async def close(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task,return_exceptions=True)
