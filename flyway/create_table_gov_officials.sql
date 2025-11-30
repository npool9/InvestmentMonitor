CREATE TABLE IF NOT EXISTS gov_officials (
    id SERIAL PRIMARY KEY,
    name TEXT,
    role TEXT,
    source_url TEXT,
    UNIQUE(name, role)
)