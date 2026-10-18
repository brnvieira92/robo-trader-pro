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
