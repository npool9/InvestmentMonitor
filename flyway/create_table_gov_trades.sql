CREATE TABLE IF NOT EXISTS gov_trades (
    id SERIAL PRIMARY KEY,
    official_id INTEGER,
    transaction_date DATE,
    security_title TEXT,
    transaction_type TEXT,
    amount INTEGER,
    price REAL,
    source_url TEXT,
    FOREIGN KEY(official_id) REFERENCES gov_officials(id),
    UNIQUE(official_id, transaction_date, security_title, transaction_type, amount)
)