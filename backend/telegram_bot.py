"""Owner-only Telegram alerts, /status and /pausar. No credentials in logs.

The BotFather token is loaded from the environment, never stored in SQLite or
returned by the API. Telegram errors never interfere with order reconciliation.
"""
import asyncio
import json
import os
import sqlite3
import time
import urllib.request
import urllib.error

class TelegramError(ValueError):
    pass

class TelegramAPI:
    def __init__(self, token):
        self._token=token

    def call(self, method, payload=None):
        request=urllib.request.Request('https://api.telegram.org/bot'+self._token+'/'+method,
                                       data=json.dumps(payload or {}).encode(),headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=15) as response:
                data=json.load(response)
        except urllib.error.HTTPError as exc:
            code=exc.code
            raise TelegramError(f'Telegram respondeu HTTP {code}. Confira token, chat e permissões.') from None
        except (OSError,ValueError,TimeoutError):
            raise TelegramError('Telegram indisponível: verifique conexão, certificado e acesso a api.telegram.org.') from None
        if not data.get('ok'):
            raise TelegramError('Telegram recusou a solicitação. Confira a configuração do bot.')
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
        self._task=None
        self._wake=asyncio.Event()
        # Dedicated bot: the cursor is namespaced per destination; never save the token.
        self.offset_key='telegram_offset:'+self.chat_id
        with journal.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS telegram_outbox (id TEXT PRIMARY KEY, chat TEXT NOT NULL, text TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0, sent INTEGER NOT NULL DEFAULT 0)')

    def public_status(self):
        return dict(configured=self.enabled,connected=self.connected,last_error=self.last_error,
                    commands=['/status','/pausar','/ajuda'])

    def enqueue(self, event_id, text):
        if not self.enabled:
            return
        try:
            with self.journal.connection() as db:
                db.execute('INSERT OR IGNORE INTO telegram_outbox (id,chat,text) VALUES (?,?,?)',
                           (event_id,self.chat_id,text[:3900]))
        except sqlite3.Error:
            self.last_error='Fila de notificações indisponível. Execução MT5 segue com seu próprio diário.'
            return
        self._wake.set()

    async def test(self):
        if not self.enabled:
            raise TelegramError('Defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env e reinicie o servidor.')
        me=await asyncio.to_thread(self.api.call,'getMe')
        await asyncio.to_thread(self.api.call,'sendMessage',dict(chat_id=self.chat_id,text='ProspeTraderBot: Telegram conectado. Use /status e /pausar.'))
        self.connected=True;self.last_error=None
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
            row=db.execute('SELECT id,text,attempts FROM telegram_outbox WHERE chat=? AND sent=0 AND attempts<5 AND next_try<=? ORDER BY rowid LIMIT 1',
                           (self.chat_id,time.time())).fetchone()
        if row is None:
            return
        event_id,text,attempts=row
        try:
            await asyncio.to_thread(self.api.call,'sendMessage',dict(chat_id=self.chat_id,text=text))
        except TelegramError as exc:
            self.last_error=str(exc);self.connected=False
            with self.journal.connection() as db:
                db.execute('UPDATE telegram_outbox SET attempts=?,next_try=? WHERE id=?',(attempts+1,time.time()+min(300,2**(attempts+2)),event_id))
        else:
            with self.journal.connection() as db:
                db.execute('UPDATE telegram_outbox SET sent=1 WHERE id=?',(event_id,))
            self.last_error=None;self.connected=True

    async def run(self):
        polling_ready=False
        while True:
            try:
                if not polling_ready:
                    webhook=await asyncio.to_thread(self.api.call,'getWebhookInfo')
                    if webhook.get('url'):
                        raise TelegramError('Este bot tem webhook ativo. Use um bot dedicado ou remova o webhook conscientemente antes de ativar o polling.')
                    polling_ready=True
                await self._deliver()
                updates=await asyncio.to_thread(self.api.call,'getUpdates',dict(offset=self.journal.setting(self.offset_key,0),timeout=0,allowed_updates=['message']))
                for update in updates:
                    await self.handle_update(update)
                    self.journal.set_setting(self.offset_key,int(update['update_id'])+1)
                self.connected=True
            except TelegramError as exc:
                self.last_error=str(exc);self.connected=False
            except Exception:
                self.last_error='Falha interna no bot Telegram; consulte o diagnóstico local.';self.connected=False
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
