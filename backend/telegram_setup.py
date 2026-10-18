"""Secure local BotFather setup: python -m backend.telegram_setup.

Prompts without echo, verifies the token and finds chats from /start updates.
Does not print the token or send a message. Existing .env keys are preserved.
"""
import getpass
from pathlib import Path
from backend.telegram_bot import TelegramAPI, TelegramError
from dotenv import dotenv_values, set_key

def main():
    path=Path(__file__).resolve().parent.parent/'.env'
    existing=dotenv_values(path) if path.exists() else {}
    token=existing.get('TELEGRAM_BOT_TOKEN') or getpass.getpass('Token entregue pelo BotFather (oculto): ').strip()
    if not token:
        raise SystemExit('Token não informado; nenhum arquivo alterado.')
    api=TelegramAPI(token)
    try:
        me=api.call('getMe')
        webhook=api.call('getWebhookInfo')
        if webhook.get('url'):
            raise TelegramError('Bot com webhook ativo. Crie um bot dedicado para esta aplicação.')
        print('Bot verificado: @'+me['username'])
        input('Abra esse bot no Telegram, envie /start e pressione Enter aqui: ')
        updates=api.call('getUpdates',{'timeout':0,'allowed_updates':['message']})
        chats={str(u['message']['chat']['id']):u['message']['chat'] for u in updates if 'message' in u and u['message'].get('chat',{}).get('type')=='private'}
        if not chats:
            raise TelegramError('Nenhum chat privado encontrado. Pare outros consumidores do bot, envie /start e tente novamente.')
        for identifier,chat in chats.items():
            print(identifier,chat.get('first_name',''),chat.get('username',''))
        chat_id=input('Digite o ID do seu chat listado acima: ').strip()
        if chat_id not in chats:
            raise TelegramError('Chat não corresponde à lista verificada; nenhum arquivo alterado.')
        path.touch(exist_ok=True)
        if __import__('os').name!='nt':
            path.chmod(0o600)
        set_key(path,'TELEGRAM_BOT_TOKEN',token)
        set_key(path,'TELEGRAM_CHAT_ID',chat_id)
        print('Configuração salva no .env local (ignorado pelo Git). Reinicie o servidor e use Testar Telegram no painel.')
    except TelegramError as exc:
        raise SystemExit(str(exc)) from None

if __name__=='__main__':
    main()
