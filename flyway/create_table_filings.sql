CREATE TABLE IF NOT EXISTS filings (
    id SERIAL PRIMARY KEY,
    accession TEXT UNIQUE,
    insider TEXT,
    issuer TEXT,
    filing_date DATE,
    url TEXT
)