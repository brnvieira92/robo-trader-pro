import asyncio
import urllib.error
import pytest
from backend.journal import PaperJournal
from backend.telegram_bot import TelegramAPI, TelegramBot, TelegramError

@pytest.fixture
def notifier(tmp_path,monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','test-token-not-a-real-credential')
    monkeypatch.setenv('TELEGRAM_CHAT_ID','123')
    state={'running':True}
    def pause():state['running']=False
    bot=TelegramBot(PaperJournal(tmp_path/'tg.db'),lambda:'Status de teste',pause)
    return bot,state

def message(chat,text,update=1,kind='private'):
    return dict(update_id=update,message={'chat':{'id':chat,'type':kind},'text':text,'from':{'is_bot':False}})

def test_owner_can_pause_and_other_chat_cannot(notifier):
    bot,state=notifier
    asyncio.run(bot.handle_update(message(999,'/pausar')))
    assert state['running']
    asyncio.run(bot.handle_update(message(123,'/pausar',kind='group')))
    assert state['running']
    asyncio.run(bot.handle_update(message(123,'/pausar')))
    assert not state['running']

def test_status_reply_and_command_dedup_are_persisted(notifier):
    bot,_=notifier
    asyncio.run(bot.handle_update(message(123,'/status')))
    asyncio.run(bot.handle_update(message(123,'/status')))
    with bot.journal.connection() as db:
        rows=db.execute('SELECT text FROM telegram_outbox').fetchall()
    assert rows==[('Status de teste',)]

def test_unknown_commands_never_trade_or_resume(notifier):
    bot,state=notifier;state['running']=False
    for text in ['/comprar','/buy','/resume','/iniciar']:
        asyncio.run(bot.handle_update(message(123,text)))
    assert not state['running']

def test_api_errors_do_not_include_token_or_full_url(monkeypatch):
    token='do-not-leak-this-token'
    def failed(*args,**kwargs):raise urllib.error.URLError('https://api.telegram.org/bot'+token+'/getMe')
    monkeypatch.setattr('urllib.request.urlopen',failed)
    with pytest.raises(TelegramError) as exc:TelegramAPI(token).call('getMe')
    assert token not in str(exc.value) and '/getMe' not in str(exc.value)

def test_notifications_retry_without_failing_trading_loop(notifier):
    bot,_=notifier
    bot.enqueue('operation:1','Ordem confirmada')
    def failed(*args,**kwargs):raise TelegramError('indisponível')
    bot.api.call=failed
    asyncio.run(bot._deliver())
    with bot.journal.connection() as db:
        assert db.execute('SELECT attempts,sent FROM telegram_outbox').fetchone()==(1,0)
    assert bot.last_error=='indisponível'

def test_successful_notification_is_not_resent(notifier):
    bot,_=notifier;calls=[]
    bot.api.call=lambda method,payload:calls.append((method,payload))
    bot.enqueue('operation:1','Teste');bot.enqueue('operation:1','Teste')
    asyncio.run(bot._deliver());asyncio.run(bot._deliver())
    assert len(calls)==1 and calls[0][1]['chat_id']=='123'

def test_disabled_telegram_never_requires_credentials(tmp_path,monkeypatch):
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN',raising=False);monkeypatch.delenv('TELEGRAM_CHAT_ID',raising=False)
    bot=TelegramBot(PaperJournal(tmp_path/'off.db'),lambda:'',lambda:None)
    bot.enqueue('id','text')
    assert not bot.public_status()['configured']
    with pytest.raises(TelegramError):asyncio.run(bot.test())

def test_public_status_never_returns_token(notifier):
    bot,_=notifier
    assert 'token' not in repr(bot.public_status()).lower()


def test_previously_exhausted_message_is_retried_and_sent_once(notifier):
    bot,_=notifier;calls=[]
    bot.enqueue('legacy-five','Mensagem antiga')
    with bot.journal.connection() as db:db.execute('UPDATE telegram_outbox SET attempts=5,next_try=0')
    assert bot.public_status()['pending']==1
    bot.api.call=lambda *a:calls.append(a)
    asyncio.run(bot._deliver());asyncio.run(bot._deliver())
    assert len(calls)==1 and bot.public_status()['pending']==0
    assert 'momento original' in calls[0][1]['text']


def test_transient_delivery_keeps_retrying_beyond_five_with_bounded_backoff(notifier):
    import time
    bot,_=notifier;bot.enqueue('retry','Mensagem')
    bot.api.call=lambda *a:(_ for _ in ()).throw(TelegramError('Rede indisponível','NETWORK_ERROR'))
    for attempt in range(8):
        with bot.journal.connection() as db:db.execute('UPDATE telegram_outbox SET next_try=0')
        asyncio.run(bot._deliver())
    with bot.journal.connection() as db:
        attempts,next_try,sent=db.execute('SELECT attempts,next_try,sent FROM telegram_outbox').fetchone()
    assert attempts==8 and not sent and 0<next_try-time.time()<=300
    bot._success('poll')
    assert not bot.public_status()['connected'] and bot.public_status()['last_error_code']=='NETWORK_ERROR'
    bot.api.call=lambda *a:{}
    with bot.journal.connection() as db:db.execute('UPDATE telegram_outbox SET next_try=0')
    asyncio.run(bot._deliver())
    assert bot.public_status()['connected'] and bot.public_status()['last_error'] is None


@pytest.mark.parametrize('error,code',[
    (__import__('ssl').SSLCertVerificationError(1,'secret certificate URL'),'TLS_CERTIFICATE_VERIFY_FAILED'),
    (__import__('socket').gaierror(-2,'secret DNS URL'),'DNS_RESOLUTION'),
    (TimeoutError('secret timeout URL'),'NETWORK_TIMEOUT')])
def test_network_error_categories_are_precise_without_exception_content(monkeypatch,error,code):
    def fail(*a,**k):raise urllib.error.URLError(error)
    monkeypatch.setattr('urllib.request.urlopen',fail)
    with pytest.raises(TelegramError) as result:TelegramAPI('secret-bot-token').call('getMe')
    assert result.value.code==code and 'secret' not in str(result.value)


def test_tls_verification_stays_required(monkeypatch):
    import ssl
    import io
    seen=[]
    def success(*a,**k):
        seen.append(k['context'])
        return io.BytesIO(b'{"ok":true,"result":{}}')
    monkeypatch.setattr('urllib.request.urlopen',success)
    TelegramAPI('secret').call('getMe')
    assert seen[0].verify_mode==ssl.CERT_REQUIRED and seen[0].check_hostname


def test_rate_limit_uses_server_retry_delay_without_exposing_response(monkeypatch,notifier):
    import io
    def fail(*a,**k):raise urllib.error.HTTPError('https://secret-token-url',429,'secret',{},io.BytesIO(b'{"parameters":{"retry_after":600},"description":"secret"}'))
    monkeypatch.setattr('urllib.request.urlopen',fail)
    with pytest.raises(TelegramError) as result:TelegramAPI('secret').call('getMe')
    assert result.value.code=='RATE_LIMIT' and result.value.retry_after==600
    assert 'secret' not in str(result.value)


def test_unknown_queue_count_is_not_reported_as_zero(notifier,monkeypatch):
    import sqlite3
    bot,_=notifier
    monkeypatch.setattr(bot.journal,'connection',lambda:(_ for _ in ()).throw(sqlite3.OperationalError('locked')))
    assert bot.public_status()['pending'] is None and bot.public_status()['queue']['error']
