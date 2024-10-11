import asyncio
import logging
import os

import aiohttp
import asyncpg
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.callback_answer import CallbackAnswerMiddleware
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')
DB_NAME = os.getenv('DB_NAME')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')

ERROR_MESSAGES = {
    400: "Ошибка: Неверный запрос. Проверьте правильность параметров.",
    401: "Ошибка: Пользователь не авторизован. Проверьте ваш API ключ.",
    403: "Ошибка: Доступ запрещён. У вас нет прав на выполнение этого действия.",
    404: "Ошибка: Адрес не найден. Проверьте правильность параметров.",
    429: "Ошибка: Слишком много запросов. Попробуйте позже.",
    500: "Ошибка: Внутренняя ошибка сервера. Повторите запрос позднее."
}

WAREHOUSES_ENDPOINT = 'https://supplies-api.wildberries.ru/api/v1/warehouses'
COFFICIENTS_ENDPOINT = 'https://supplies-api.wildberries.ru/api/v1/acceptance/coefficients'
PING_ENDPOINT = 'https://supplies-api.wildberries.ru/ping'

WAREHOUSES_PER_PAGE = 6

# Логирование
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
dp.callback_query.middleware(CallbackAnswerMiddleware())


# Подключение к базе данных
async def create_db_pool():
    return await asyncpg.create_pool(
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        host=DB_HOST,
        port=DB_PORT,
    )


# Проверка и создание таблиц, если их нет
async def setup_database():
    pool = await create_db_pool()
    async with pool.acquire() as conn:
        with open('database.sql', 'r', encoding='UTF-8') as sql_file:
            sql = sql_file.read()
        await conn.execute(sql)
    await pool.close()


async def update_warehouses(api_key: str):
    headers = {'Authorization': api_key}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(WAREHOUSES_ENDPOINT) as response:
            if response.status == 200:
                data = await response.json()
                await update_warehouses_in_db(data)
            return {
                'status': response.status,
                'message': ERROR_MESSAGES.get(response.status, 'OK')
            }


async def update_warehouses_in_db(data):
    pool = await create_db_pool()
    async with pool.acquire() as conn:
        insert_query = """
            INSERT INTO warehouses (warehouse_id, name, last_updated)
            VALUES ($1, $2, NOW()) ON CONFLICT (warehouse_id) DO UPDATE 
            SET name = EXCLUDED.name, last_updated = EXCLUDED.last_updated
        """
        for warehouse in data:
            warehouse_id = warehouse.get('ID')
            name = warehouse.get('name')
            if "СЦ" not in name:
                await conn.execute(insert_query, warehouse_id, name)


async def get_coefficients(api_key: str, warehouses: list[int]):
    headers = {'Authorization': api_key}
    params = {'warehouseIDs': ','.join([str(x) for x in warehouses])}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(COFFICIENTS_ENDPOINT,
                               headers=headers, params=params) as response:
            if response.status == 200:
                data = await response.json()
            else:
                data = {"error": response.status}
            await session.close()
            return data


# Проверка API ключа запросом на обновление складов
async def validate_api_key(api_key: str) -> bool:
    headers = {'Authorization': api_key}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(PING_ENDPOINT) as response:
            return response.status == 200


# Определяем состояния для регистрации
class RegistrationStates(StatesGroup):
    entering_api_key = State()
    choosing_warehouses = State()
    editing_warehouses = State()
    editing_notification_threshold = State()
    editing_polling_frequency = State()
    editing_api_key = State()


# Роутер для регистрации
registration_router = Router()


# Начало регистрации: Запрос API ключа
@registration_router.message(Command('start'))
async def start_registration(message: Message, state: FSMContext):
    telegram_id = message.from_user.id
    pool = await create_db_pool()

    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            'SELECT user_id FROM users WHERE telegram_id = $1', telegram_id)
        if user:
            await message.answer('Вы уже зарегистрированы!')
        else:
            await message.answer(
                '🌟 Добро пожаловать! Пожалуйста, введите ваш API ключ для работы с Wildberries.\n'
                '🔑 Необходимо, чтобы ключ имел доступ к категории Поставки!')
            await state.set_state(RegistrationStates.entering_api_key)

    await pool.close()


# Обработка API ключа с проверкой
@registration_router.message(RegistrationStates.entering_api_key)
async def process_api_key(message: Message, state: FSMContext):
    api_key = message.text
    telegram_id = message.from_user.id

    await message.answer('Проверка ключа, ожидайте')

    if await validate_api_key(api_key):
        # Сохранение API ключа в базе данных
        pool = await create_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO users (telegram_id, api_key) '
                'VALUES ($1, $2) ON CONFLICT (telegram_id) DO UPDATE SET api_key = $2',
                telegram_id, api_key
            )

        await pool.close()

        # Переход сразу к выбору складов после успешного сохранения ключа
        await message.answer(
            '✅ API ключ сохранён! Теперь выберите склады для отслеживания.')
        await update_warehouses(api_key)
        await show_warehouse_selection(message)
        await state.set_state(RegistrationStates.choosing_warehouses)
    else:
        await message.answer(
            '❌ API ключ недействителен. Пожалуйста, проверьте его и введите заново.')


# Функция для получения списка складов из базы данных
async def get_available_warehouses():
    pool = await create_db_pool()
    async with pool.acquire() as conn:
        warehouses = await conn.fetch(
            'SELECT warehouse_id, name FROM warehouses')
    await pool.close()
    return warehouses


# Генерация клавиатуры с доступными складами
async def generate_warehouse_keyboard(selected_warehouses=[], page: int = 0):
    warehouses = await get_available_warehouses()
    builder = InlineKeyboardBuilder()

    selected_ids = selected_warehouses

    # Рассчитываем, какие склады показать на текущей странице
    start_index = page * WAREHOUSES_PER_PAGE
    end_index = start_index + WAREHOUSES_PER_PAGE
    paginated_warehouses = warehouses[start_index:end_index]

    # Генерируем кнопки для складов на текущей странице
    buttons = []
    for index, warehouse in enumerate(paginated_warehouses):
        is_selected = warehouse['warehouse_id'] in selected_ids
        button_text = f'✅ {warehouse["name"]}' if is_selected else warehouse[
            'name']
        buttons.append(InlineKeyboardButton(
            text=button_text,
            callback_data=f'select_{warehouse["warehouse_id"]}_page_{page}')
        )

        if index % 2 == 1:
            builder.row(*buttons)
            buttons.clear()

    # Добавляем кнопки <<, Сохранить, >>
    navigation_row = []
    if page > 0:
        navigation_row.append(
            {'text': '<<', 'callback_data': f'page_{page - 1}'})
    navigation_row.append({'text': 'Сохранить', 'callback_data': 'done'})
    if end_index < len(warehouses):
        navigation_row.append(
            {'text': '>>', 'callback_data': f'page_{page + 1}'})

    # Добавляем кнопки на клавиатуру
    builder.row(
        *[InlineKeyboardButton(**nav_button) for nav_button in navigation_row])

    return builder.as_markup()


# Выбор складов
async def show_warehouse_selection(message: Message, selected_warehouses=[]):
    # Отправляем клавиатуру с выбором складов
    keyboard = await generate_warehouse_keyboard(selected_warehouses)
    await message.answer('📦 Выберите склады для отслеживания:',
                         reply_markup=keyboard)


# Обработка выбора склада
@registration_router.callback_query(
    lambda callback: callback.data.startswith('select_'))
async def warehouse_selected(callback: CallbackQuery, state: FSMContext):
    warehouse_id = int(callback.data.split('_')[1])
    page = int(callback.data.split('_')[3])
    telegram_id = callback.from_user.id

    pool = await create_db_pool()
    async with pool.acquire() as conn:
        user_warehouses = await conn.fetch(
            'SELECT warehouse_id FROM user_warehouses '
            'WHERE user_id = (SELECT user_id FROM users WHERE telegram_id = $1)',
            telegram_id)

    selected_ids = [row['warehouse_id'] for row in user_warehouses]

    # Сохранение выбранного склада или удаление, если уже выбран
    if warehouse_id in selected_ids:
        selected_ids.remove(warehouse_id)
        await callback.answer(
            f'❌ Склад {warehouse_id} удалён из вашего списка.')
        # Удаление склада из базы данных
        async with pool.acquire() as conn:
            await conn.execute(
                'DELETE FROM user_warehouses '
                'WHERE user_id = (SELECT user_id FROM users WHERE telegram_id = $1) AND warehouse_id = $2',
                telegram_id, warehouse_id)
    else:
        selected_ids.append(warehouse_id)
        await callback.answer(f'✅ Склад {warehouse_id} добавлен в ваш список.')
        # Сохранение нового склада в базе данных
        async with pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO user_warehouses (warehouse_id, user_id) '
                'VALUES ($1, (SELECT user_id FROM users WHERE telegram_id = $2))',
                warehouse_id, telegram_id)

    # Обновляем клавиатуру с текущими выбранными складами
    keyboard = await generate_warehouse_keyboard(selected_ids, page=page)
    await callback.message.edit_reply_markup(reply_markup=keyboard)

    await pool.close()


@registration_router.callback_query(lambda c: c.data.startswith('page_'))
async def change_page_callback(callback_query: CallbackQuery):
    page = int(callback_query.data.split('_')[1])  # Извлекаем номер страницы
    pool = await create_db_pool()
    async with pool.acquire() as conn:
        user_warehouses = await conn.fetch(
            'SELECT warehouse_id FROM user_warehouses '
            'WHERE user_id = (SELECT user_id FROM users WHERE telegram_id = $1)',
            callback_query.from_user.id)

    selected_ids = [row['warehouse_id'] for row in user_warehouses]
    keyboard = await generate_warehouse_keyboard(selected_ids,
                                                 page=page)
    await callback_query.message.edit_reply_markup(reply_markup=keyboard)


# Завершение выбора складов и переход в главное меню
@registration_router.callback_query(lambda callback: callback.data == 'done')
async def finish_warehouse_selection(callback: CallbackQuery,
                                     state: FSMContext):
    await callback.message.answer(
        '🎉 Настройка завершена! Теперь мы будем отслеживать коэффициенты на выбранных вами складах.')
    await state.clear()  # Очищаем состояние
    await show_main_menu(callback.message)


# Главное меню с настройками
@registration_router.message(Command('menu'))
async def show_main_menu(message: Message):
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text='⚙️ Настройки:', callback_data='settings')
    await message.answer('🏠 Главное меню:', reply_markup=keyboard.as_markup())


# Меню настроек
@registration_router.callback_query(
    lambda callback: callback.data == 'settings')
async def settings_menu(callback: CallbackQuery):
    telegram_id = callback.from_user.id

    pool = await create_db_pool()
    async with pool.acquire() as conn:
        user_settings = await conn.fetchrow(
            'SELECT polling_frequency, notification_threshold FROM users WHERE telegram_id = $1',
            telegram_id)

        warehouses = await conn.fetch(
            'SELECT name FROM warehouses w JOIN user_warehouses uw ON w.warehouse_id = uw.warehouse_id '
            'WHERE uw.user_id = (SELECT user_id FROM users WHERE telegram_id = $1)',
            telegram_id)

    if user_settings:
        warehouse_names = ', '.join(
            [w['name'] for w in warehouses]) if warehouses else 'Не выбрано'
        await callback.message.answer(
            f'Ваши настройки:'
            f'\n📦 Склады: {warehouse_names}'
            f'\n🔄 Частота опроса: {user_settings['polling_frequency']} минут'
            f'\n📊 Порог коэффициента: {user_settings['notification_threshold']}'
        )
        keyboard = InlineKeyboardBuilder()
        warehouses = InlineKeyboardButton(text='📦 Изменить склады',
                                          callback_data='edit_warehouses')
        polling = InlineKeyboardButton(text='🔄 Частота опроса',
                                       callback_data='edit_polling_frequency')
        threshold = InlineKeyboardButton(text='📊 Порог коэффициента',
                                         callback_data='edit_threshold')
        api_key = InlineKeyboardButton(text='🔑 Изменить API ключ',
                                       callback_data='edit_api_key')
        keyboard.row(warehouses, polling)
        keyboard.row(threshold, api_key)
        await callback.message.answer('🛠️ Выберите действие:',
                                      reply_markup=keyboard.as_markup())
    else:
        await callback.message.answer('❌ Настройки не найдены.')
    await pool.close()


@registration_router.callback_query(
    lambda callback: callback.data == 'edit_warehouses')
async def edit_warehouses(callback: CallbackQuery, state: FSMContext):
    telegram_id = callback.from_user.id
    pool = await create_db_pool()

    async with pool.acquire() as conn:
        user_warehouses = await conn.fetch(
            'SELECT warehouse_id FROM user_warehouses '
            'WHERE user_id = (SELECT user_id FROM users WHERE telegram_id = $1)',
            telegram_id)

    selected_ids = [row['warehouse_id'] for row in user_warehouses]
    await show_warehouse_selection(callback.message, selected_ids)

    await state.set_state(RegistrationStates.editing_warehouses)
    await pool.close()


# Изменение частоты опроса
@registration_router.callback_query(
    lambda callback: callback.data == 'edit_polling_frequency')
async def edit_polling_frequency(callback: CallbackQuery):
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text='5 минут', callback_data='set_polling_5')
    keyboard.button(text='15 минут', callback_data='set_polling_15')
    keyboard.button(text='30 минут', callback_data='set_polling_30')
    keyboard.button(text='60 минут', callback_data='set_polling_60')
    await callback.message.answer('🔄 Выберите новую частоту опроса:',
                                  reply_markup=keyboard.as_markup())


@registration_router.callback_query(
    lambda callback: callback.data.startswith('set_polling_'))
async def process_new_polling_frequency(callback: CallbackQuery):
    new_frequency = int(callback.data.split('_')[-1])
    telegram_id = callback.from_user.id

    pool = await create_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            'UPDATE users SET polling_frequency = $1 WHERE telegram_id = $2',
            new_frequency, telegram_id)
    await pool.close()

    await callback.message.answer('🔄 Частота опроса обновлена!')
    await show_main_menu(callback.message)


# Изменение порога коэффициента
@registration_router.callback_query(
    lambda callback: callback.data == 'edit_threshold')
async def edit_notification_threshold(callback: CallbackQuery,
                                      state: FSMContext):
    await callback.message.answer('📊 Введите новый порог коэффициента:')
    await state.set_state(RegistrationStates.editing_notification_threshold)


@registration_router.message(RegistrationStates.editing_notification_threshold)
async def process_new_notification_threshold(message: Message,
                                             state: FSMContext):
    try:
        new_threshold = float(message.text)
        telegram_id = message.from_user.id

        pool = await create_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                'UPDATE users SET notification_threshold = $1 WHERE telegram_id = $2',
                new_threshold, telegram_id)
        await pool.close()

        await message.answer('📊 Порог коэффициента обновлён!')
        await show_main_menu(message)
    except (ValueError, asyncpg.exceptions.CheckViolationError):
        await message.answer('⚠️ Пожалуйста, введите число от 0 до 20.')


# Изменение API ключа
@registration_router.callback_query(
    lambda callback: callback.data == 'edit_api_key')
async def edit_api_key(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        '🔑 Введите новый API ключ для работы с Wildberries:')
    await state.set_state(RegistrationStates.editing_api_key)


@registration_router.message(RegistrationStates.editing_api_key)
async def process_new_api_key(message: Message, state: FSMContext):
    new_api_key = message.text
    telegram_id = message.from_user.id

    if await validate_api_key(new_api_key):
        pool = await create_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                'UPDATE users SET api_key = $1 WHERE telegram_id = $2',
                new_api_key, telegram_id)
        await pool.close()

        await message.answer('API ключ обновлён!')
        await show_main_menu(message)
    else:
        await message.answer(
            '⚠️ Недействительный API ключ. Пожалуйста, введите корректный API ключ.')


async def start_polling():
    pool = await create_db_pool()
    loop_num = 0
    while True:
        async with pool.acquire() as conn:
            users = await conn.fetch(
                'SELECT user_id, api_key, polling_frequency, notification_threshold FROM users')

        for user in users:
            if loop_num % user['polling_frequency'] == 0:
                await check_coefficients_for_user(
                    user['user_id'],
                    user['api_key'],
                    user['notification_threshold']
                )

        await asyncio.sleep(60)
        loop_num += 1


async def check_coefficients_for_user(user_id, api_key, threshold):
    # Получаем склады пользователя
    pool = await create_db_pool()
    async with pool.acquire() as conn:
        warehouses = await conn.fetch(
            'SELECT warehouse_id FROM user_warehouses WHERE user_id = $1',
            user_id)

    if not warehouses:
        return

    warehouse_ids = [warehouse['warehouse_id'] for warehouse in warehouses]
    coefficients = await get_coefficients(api_key, warehouse_ids)

    good_coefficients = []
    for coefficient in coefficients:
        if -1 < int(coefficient['coefficient']) <= threshold:
            good_coefficients.append(coefficient)
    await notify_user(user_id, good_coefficients)


async def notify_user(user_id, coefficients):
    pool = await create_db_pool()
    async with pool.acquire() as conn:
        telegram_id = await conn.fetchval(
            'SELECT telegram_id FROM users WHERE user_id = $1', user_id)
    await pool.close()
    # Отправляем уведомление в Telegram
    message = (f'❗️ Найдено {len(coefficients)} подходящих коэффициентов:\n' +
               '\n'.join(
                   [f'{c['warehouseName']} | {c['coefficient']} | '
                    f'{c['boxTypeName']} | {c['date']}' for c in coefficients])
               )
    await bot.send_message(telegram_id, message)


# Регистрация роутеров
dp.include_router(registration_router)

if __name__ == '__main__':
    print('Starting bot')
    asyncio.run(setup_database())
    loop = asyncio.new_event_loop()
    loop.create_task(start_polling())  # Запускаем проверку коэффициентов
    loop.run_until_complete(dp.start_polling(bot))
