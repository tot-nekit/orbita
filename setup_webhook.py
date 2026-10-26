"""Switch this bot to its already deployed HTTPS site. Reads secrets only from env."""
import os
from app import database, store, webhooks
from app.telegram_bot import Telegram


def main():
    if os.getenv('TELEGRAM_MODE') != 'webhook':raise ValueError('Установи TELEGRAM_MODE=webhook в секретах сервера.')
    database.validate_runtime()
    store.init()
    base = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
    if not base.startswith('https://'):raise ValueError('Для webhook нужен HTTPS origin.')
    client = Telegram()
    secret = webhooks.secret()
    client.call('setWebhook', {'url':base+'/api/telegram/webhook', 'secret_token':secret,
                'max_connections':1, 'allowed_updates':['message','callback_query'], 'drop_pending_updates':False})
    info = client.call('getWebhookInfo')
    if info.get('url') != base+'/api/telegram/webhook':raise RuntimeError('Telegram не подтвердил адрес webhook.')
    client.call('setMyCommands', {'commands':[{'command':'start','description':'Начать и открыть планировщик'},
                {'command':'today','description':'Планы на сегодня'},{'command':'new','description':'Новая запись'},
                {'command':'link','description':'Связать с веб-сайтом'},{'command':'help','description':'Помощь'}]})
    client.call('setChatMenuButton', {'menu_button':{'type':'web_app','text':'Орбита','web_app':{'url':base+'/mini'}}})
    print('Webhook and Mini App configured; credentials omitted.', flush=True)


if __name__ == '__main__':
    try:main()
    except Exception:raise SystemExit('Webhook setup failed; credentials omitted.') from None
