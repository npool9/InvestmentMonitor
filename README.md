# Government & SEC Insider Trade Monitor

A Flask-based web app that scrapes, stores, and displays insider trading activity from:

- **SEC Form 4 filings (corporate insiders)**
- **House & Senate PTR reports (government officials)**

The app provides dashboards for monitoring trades, tracking insiders/officials, and pulling new filings.

---

## Features

- **SEC Form 4 Scraper**
- **Government PTR Scraper (Senate + House)**
- **Dashboards**
  - `/sec_dashboard`
  - `/gov_dashboard`
  - `/dashboard_tracked` (tracked insiders only)
- **Tracking System**
  - `/track/<name>`
  - `/untrack/<name>`
- **Selenium-based headless scraping**
- **PostgreSQL storage and flyway definitions**

---

## How to Run

```bash
pip install -r requirements.txt
python main.py
