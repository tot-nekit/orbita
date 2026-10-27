# Работающий сервер и обслуживание

Орбита работает на ВМ Cloud.ru (Ubuntu 22.04, 2 vCPU, 4 ГБ RAM) по адресу `https://213.171.29.94/`. Секреты хранятся только в `~/orbita/.env` на сервере (права 600) и в приватной копии владельца. В репозитории и архиве их нет.

## Как устроено

```text
Интернет ──443/80── Nginx (системный) ──127.0.0.1:8000── Docker: orbita
                     │ IP-сертификат Let's Encrypt         ├─ Caddy (внутренний) → Waitress (сайт и API)
                     │ лимиты запросов и входа             ├─ Telegram-бот (polling через TELEGRAM_PROXY)
                     └ ACME webroot для продления          └─ резервное копирование (SQLCipher)
```

Сайт, бот и копирование запускает `run.py all` внутри одного контейнера. Если падает сайт или внутренний прокси, контейнер перезапускается целиком (`restart: unless-stopped`). Бот и копирование — вспомогательные процессы: при сбое они перезапускаются отдельно с паузой от 5 секунд до 5 минут, сайт продолжает работать.

Cloud.ru блокирует прямой доступ ВМ к api.telegram.org, поэтому бот ходит в Telegram через HTTP-прокси из `TELEGRAM_PROXY`. Прокси используется только для Telegram; ИИ и остальные соединения идут напрямую. Если Telegram недоступен, бот ждёт и пробует снова, не трогая сайт. `bot_heartbeat` обновляется только после реального ответа Telegram, поэтому статус «Бот работает» в настройках правдивый.

Нельзя запускать второй экземпляр бота с тем же токеном — например, `run.py all` на своём компьютере с тем же `.env`. Telegram отдаёт обновления только одному получателю. Сервер напишет в журнал «Another bot instance uses this token».

## Файлы на сервере

| Путь | Назначение |
|---|---|
| `~/orbita/` | исходники, `compose.yaml`, `compose.public.yaml` (старый внешний Caddy отключён профилем `legacy-caddy`) |
| `~/orbita/.env` | ключ базы, секрет сессий, ключ ИИ, токен бота, прокси Telegram, `PUBLIC_BASE_URL=https://213.171.29.94` |
| `/etc/nginx/sites-available/orbita` | HTTPS, редирект с 80, лимиты, проксирование на 127.0.0.1:8000 |
| `/etc/letsencrypt/live/orbita-ip/` | сертификат на IP (короткий срок, продлевается автоматически) |
| `orbita-cert-renew.timer` | дважды в сутки проверяет продление и перезагружает Nginx |
| Docker volume `orbita_orbita-data` | зашифрованная база и `backups/` |
| fail2ban (`sshd`), unattended-upgrades | защита SSH от перебора, обновления безопасности без автоперезагрузки |

## Резервные копии

Каждые 6 часов создаётся проверенная зашифрованная копия, хранятся 28 последних — около недели. Перезапуск контейнера не создаёт внеочередную копию, если последняя моложе 6 часов, поэтому старые точки восстановления не вытесняются.

Если задан `TELEGRAM_BACKUP_CHAT_ID`, раз в сутки последняя копия отправляется владельцу в Telegram. Файл зашифрован ключом базы и без `DATABASE_KEY` не читается. Так данные переживут потерю ВМ. Ключ храни отдельно: без него ни база, ни копии не открываются.

Восстановление: останови контейнер, положи нужный файл копии на место `/app/data/orbita.sqlite3` в volume, запусти контейнер с тем же `DATABASE_KEY`. Перед этим сделай копию текущего файла.

## Обычные операции

```sh
cd ~/orbita
sudo docker compose -f compose.yaml -f compose.public.yaml ps
sudo docker compose -f compose.yaml -f compose.public.yaml logs --tail 100 orbita
sudo docker compose -f compose.yaml -f compose.public.yaml up -d --build orbita
sudo docker compose -f compose.yaml -f compose.public.yaml exec orbita python backup.py
sudo nginx -t && sudo systemctl reload nginx
sudo fail2ban-client status sshd
```

Не запускай `up` для сервиса `https` из `compose.public.yaml`: порты 80 и 443 занимает Nginx. Журналы контейнера ограничены 5 файлами по 10 МБ.

## Сеть Cloud.ru

В группе безопасности `SSH-access_ru.AZ-1` должны быть открыты входящие TCP 22, 80 и 443. Порт 80 нужен для продления сертификата, 443 — для сайта. Порт 8000 наружу не открывается.

## Перенос на другой сервер

Нужны Docker, Nginx и Certbot. Перенеси `.env` без изменения `DATABASE_KEY` и `APP_SESSION_SECRET`, а также последнюю копию базы. Укажи новый `PUBLIC_BASE_URL` и получи сертификат: для IP — `certbot certonly --webroot --ip-address <IP> --preferred-profile shortlived`, для домена — обычный.
