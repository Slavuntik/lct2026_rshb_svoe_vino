-- Схема Postgres «Свой Сомелье» v0.1 — ЗАМОРОЖЕНА, правки через оркестратора.
-- Принципы: consent-ledger append-only; ПД минимальны и локализуемы; события — без свободного текста.

CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text UNIQUE NOT NULL,
    password_hash text NOT NULL,               -- argon2id
    birth_date    date NOT NULL,               -- гейт 18+ проверяется на регистрации и при логине
    created_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz                  -- soft-delete до фоновой очистки, затем строка удаляется
);

-- Append-only журнал согласий: доказуемость 152-ФЗ/GDPR. Отзыв — новая строка с granted=false.
CREATE TABLE consent_ledger (
    id              bigserial PRIMARY KEY,
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    consent_version text NOT NULL,             -- версия текста согласия, напр. "2026-09-01.1"
    scope           text NOT NULL,             -- base | profiling | geo | marketing
    granted         boolean NOT NULL,
    at              timestamptz NOT NULL DEFAULT now(),
    ip_hash         text                       -- sha256(ip+соль суток), не сырой IP
);
CREATE INDEX ON consent_ledger (user_id, scope, at DESC);

CREATE TABLE swipes (
    id       bigserial PRIMARY KEY,
    user_id  uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    wine_id  text NOT NULL,                    -- slug каталога vines
    verdict  text NOT NULL CHECK (verdict IN ('like','dislike','skip')),
    at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON swipes (user_id, at DESC);

-- Кэш вкусового паспорта (пересчитывается из swipes; истина — swipes)
CREATE TABLE taste_profiles (
    user_id      uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    vector       jsonb NOT NULL,               -- 7 осей 0..1
    top_styles   jsonb NOT NULL DEFAULT '[]',
    swipes_count integer NOT NULL DEFAULT 0,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE scans (
    id         bigserial PRIMARY KEY,
    user_id    uuid REFERENCES users(id) ON DELETE SET NULL,
    query_text text NOT NULL,                  -- распознанный текст этикетки (не фото)
    matched    text,                           -- wine_id или NULL
    confidence real,
    at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE chat_messages (
    id         bigserial PRIMARY KEY,
    user_id    uuid REFERENCES users(id) ON DELETE CASCADE,
    role       text NOT NULL CHECK (role IN ('user','assistant')),
    content    text NOT NULL,
    citations  jsonb NOT NULL DEFAULT '[]',    -- [{wine_id|chunk_id, url}]
    trace      jsonb,                          -- retrieval-трасса: фильтры, кандидаты, index_version
    at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON chat_messages (user_id, at DESC);

-- Продуктовая аналитика: только имена и структурные props, НИКОГДА свободный текст/ПД.
CREATE TABLE events (
    id      bigserial PRIMARY KEY,
    user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    name    text NOT NULL,                     -- словарь имён в contracts/events.md
    props   jsonb NOT NULL DEFAULT '{}',
    at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON events (name, at DESC);

CREATE TABLE waitlist (
    id              bigserial PRIMARY KEY,
    email           text UNIQUE NOT NULL,
    consent_version text NOT NULL,
    at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE feedback (                        -- 👍/👎 на ответы чата -> пополнение голд-сета
    id         bigserial PRIMARY KEY,
    user_id    uuid REFERENCES users(id) ON DELETE SET NULL,
    message_id bigint REFERENCES chat_messages(id) ON DELETE CASCADE,
    verdict    text NOT NULL CHECK (verdict IN ('up','down')),
    at         timestamptz NOT NULL DEFAULT now()
);
