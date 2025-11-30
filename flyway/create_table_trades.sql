CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    filing_id INTEGER,
    transaction_date DATE,
    security_title TEXT,
    transaction_type TEXT,
    amount INTEGER,
    price REAL,
    FOREIGN KEY(filing_id) REFERENCES filings(id),
    UNIQUE(filing_id, transaction_date, security_title, transaction_type, amount, price)
)