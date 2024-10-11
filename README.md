# Coefficients Now Bot

Этот Telegram-бот создан для автоматизации управления складами и оповещениями о
коэффициентах на Wildberries. Бот позволяет пользователям регистрироваться,
выбирать склады и получать уведомления при изменении коэффициентов складов.

## Функции

- Регистрация пользователей с запросом API ключа Wildberries.
- Выбор и управление списком складов.
- Настройка оповещений по коэффициентам складов.
- Настройка частоты опроса и коэффициентов.

## Технологии

- **Python**
- **aiogram** (v3)
- **asyncpg** для работы с PostgreSQL

## Установка и запуск

### Установите зависимости

```bash
pip install -r requirements.txt
```

### Создайте и заполните файл .env
```
BOT_TOKEN=your_token
DB_HOST=your_host
DB_PORT=your_port
DB_NAME=your_db_name
DB_USER=your_db_user
DB_PASSWORD=your_db_password
```

### Запустите программу
```bash
python main.py
```
