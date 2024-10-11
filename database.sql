-- Таблица пользователей
CREATE TABLE IF NOT EXISTS users (
    user_id SERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL UNIQUE,  -- Telegram ID клиента
    api_key VARCHAR,  -- API ключ для работы с Wildberries
    updated_key_at TIMESTAMP,
    polling_frequency INT DEFAULT 60 CHECK (polling_frequency >= 1 AND polling_frequency <= 60),  -- Частота опроса в минутах (1-1440)
    notification_threshold DECIMAL(5, 2) DEFAULT 0 CHECK (notification_threshold >= 0 AND notification_threshold <= 20.00),  -- Коэффициент для оповещения (0.00-20.00)
    created_at TIMESTAMP DEFAULT NOW()
);

-- Таблица складов
CREATE TABLE IF NOT EXISTS warehouses (
    warehouse_id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    last_updated TIMESTAMP DEFAULT NOW()
);

-- Таблица связи м-м между пользователями и складами
CREATE TABLE IF NOT EXISTS user_warehouses (
    user_id INT REFERENCES users(user_id) ON DELETE CASCADE,
    warehouse_id INT REFERENCES warehouses(warehouse_id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, warehouse_id)
);

-- Индексы для оптимизации запросов
CREATE INDEX IF NOT EXISTS idx_telegram_id ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_warehouse_id ON user_warehouses(warehouse_id);
