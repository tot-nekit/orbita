"""Configure the Orbita Telegram bot profile, commands and optional Mini App button."""
import os
from pathlib import Path
from app.telegram_bot import BotError, Telegram

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTION = os.getenv(
    'TELEGRAM_BOT_DESCRIPTION',
    'Орбита — личный планировщик: задачи, встречи, заметки и напоминания. '
    'Напиши запрос обычными словами или открой Mini App.'
)
SHORT_DESCRIPTION = os.getenv(
    'TELEGRAM_BOT_SHORT_DESCRIPTION',
    'Задачи, заметки и напоминания в одном планировщике.'
)
COMMANDS = [
    {'command': 'start', 'description': 'Открыть планировщик'},
    {'command': 'today', 'description': 'Планы на сегодня'},
    {'command': 'tomorrow', 'description': 'Планы на завтра'},
    {'command': 'week', 'description': 'Планы на неделю'},
    {'command': 'new', 'description': 'Новая запись'},
    {'command': 'note', 'description': 'Быстрая заметка'},
    {'command': 'notes', 'description': 'Последние заметки'},
    {'command': 'link', 'description': 'Связать с сайтом'},
    {'command': 'account', 'description': 'Статус привязки'},
    {'command': 'help', 'description': 'Помощь'},
]


def configure(client=None, photo=None):
    client = client or Telegram()
    if not 1 <= len(DESCRIPTION) <= 512:
        raise ValueError('TELEGRAM_BOT_DESCRIPTION должен содержать 1–512 символов.')
    if not 1 <= len(SHORT_DESCRIPTION) <= 120:
        raise ValueError('TELEGRAM_BOT_SHORT_DESCRIPTION должен содержать 1–120 символов.')
    client.call('setMyDescription', {'description': DESCRIPTION})
    client.call('setMyShortDescription', {'short_description': SHORT_DESCRIPTION})
    client.call('setMyCommands', {'commands': COMMANDS})
    photo_path = Path(photo or os.getenv('TELEGRAM_BOT_PHOTO', str(ROOT / 'resources' / 'orbita-bot.jpg')))
    if photo_path.is_file():
        client.set_profile_photo(photo_path)
    elif photo:
        raise FileNotFoundError('Фото бота не найдено: ' + str(photo_path))
    base = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
    if base.startswith('https://'):
        client.call('setChatMenuButton', {'menu_button': {
            'type': 'web_app', 'text': 'Орбита', 'web_app': {'url': base + '/mini'}
        }})
    return photo_path if photo_path.is_file() else None


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Настроить описание, команды и фото Telegram-бота Орбиты')
    parser.add_argument('--photo', type=Path, help='JPG-аватар бота; по умолчанию resources/orbita-bot.jpg')
    args = parser.parse_args()
    try:
        photo = configure(photo=args.photo)
    except (BotError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    print('Профиль Telegram-бота обновлён: описание, короткое описание и команды.')
    print('Фото: ' + ('загружено.' if photo else 'не задано.'))


if __name__ == '__main__':
    main()
