CREATE TABLE servers (
    id SERIAL PRIMARY KEY,
    discord_id BIGINT UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL
);

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    discord_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(100) NOT NULL
);

CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    user_id BIGINT  REFERENCES users(discord_id) ON DELETE CASCADE,
    server_id BIGINT  REFERENCES servers(discord_id) ON DELETE CASCADE,
    content TEXT NOT NULL DEFAULT 'Default message'
);