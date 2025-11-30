# gov_and_form4_app.py
from pg_flyway import PGFlyway

from flask import Flask, jsonify, render_template_string
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from urllib.parse import urljoin
import time
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import re
import html
import datetime

app = Flask(__name__)
pg_flyway = PGFlyway()
HEADERS = {"User-Agent": "GovTradeMonitor/1.0"}

# ---- TRANSACTION CODES (for Form 4) ----
TRANSACTION_CODES = {
    "P": "Purchase",
    "S": "Sale",
    "A": "Award/Grant",
    "D": "Disposition",
    "G": "Gift",
    "F": "Payment of Taxes",
    "M": "Option Exercise",
    "C": "Conversion",
    "W": "Will/Inheritance",
    "X": "Exercise (Same-Day)",
    "O": "Other",
    "E": "Expiration",
    "H": "Non-Market Transfer",
    "I": "Discretionary Transaction",
    "L": "Small Acquisition",
    "R": "Return Transaction",
    "T": "Related Transaction",
    "J": "Other (Unclassified)"
}

prev_url = None

# ---------------------- Database Setup ----------------------
def init_db():
    # existing filings & trades (SEC Form 4)
    global pg_flyway
    pg_flyway.create_database("publictrades")
    pg_flyway = PGFlyway("publictrades")
    pg_flyway.create_table("filings")
    pg_flyway.create_table("trades")
    pg_flyway.create_table("gov_officials")
    pg_flyway.create_table("gov_trades")
    pg_flyway.create_table("tracked_insiders")
    pg_flyway.conn.commit()

init_db()

# ---------------------- Headless Chrome Setup ----------------------
def get_headless_driver():
    options = Options()
    options.headless = True
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver

# ---------------------- Utilities ----------------------
def normalize_number(s, integer=False):
    """Strip $ , parentheses and convert to int/float. Return None on failure or empty."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s) if integer else float(s)
    s = str(s).strip()
    if not s:
        return None
    s = s.replace('$', '').replace(',', '').strip()
    neg = False
    if s.startswith('(') and s.endswith(')'):
        neg = True
        s = s[1:-1].strip()
    try:
        v = int(float(s)) if integer else float(s)
        return -v if neg else v
    except Exception:
        return None

# ---------------------- Existing SEC/Form4 Pull (unchanged conceptually) ----------------------
# For brevity I include a compact pull_once that uses the same logic you've been using.
increment = 100
FORM4_FEED_URL_TEMPLATE = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&start={start}&count={count}"
doc_type = {"html": 0, "xml": 1}
file_type = "xml"

@app.route("/pull_once")
def pull_once():
    """
    Existing SEC Form 4 pull. Keeps behavior mostly the same as your working app.
    Only scans a single page for speed. Expand as needed.
    """
    global increment
    pages = 5
    processed = 0
    inserted = 0
    for i in range(pages):
        print(f"Page {i+1}/{pages}")
        driver = get_headless_driver()
        feed_url = FORM4_FEED_URL_TEMPLATE.format(start=i*increment, count=increment)
        driver.get(feed_url)
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "/html/body/div/table[2]"))
            )
        except:
            driver.quit()
            return jsonify({"error": "Form 4 table not fully loaded"})
        soup = BeautifulSoup(driver.page_source, "html.parser")
        driver.quit()

        tables = soup.find_all("table")
        if len(tables) <= 6:
            return jsonify({"error": "Form 4 table could not be parsed"})
        table = tables[6]
        rows = table.find_all("tr")[1:]  # skip header
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 5:
                continue
            # second column, pick xml/html link depending on file_type
            try:
                link = cols[1].find_all("a")[doc_type[file_type]]
            except Exception:
                continue
            index_url = urljoin("https://www.sec.gov", link.get("href"))
            accession = link.get("href").strip()
            if process_filing(index_url, accession):
                inserted += 1
            processed += 1
    return jsonify({"processed": processed, "inserted": inserted})

# ---------------------- Filing Processing ----------------------
def process_filing(index_url, accession):
    global prev_url
    url = find_primary_document(index_url, file_type)
    if url == prev_url:
        return False
    print("URL:", str(url))
    if not url:
        print("[WARN] No primary document found for", accession)
        return False
    print("[INFO] Parsing:", url)
    prev_url = url
    return parse_form4(accession, index_url, url, file_type)

# ---------------------- XML/HTML Parsing ----------------------
def parse_form4(accession, index_url, url, parser_type):
    driver = get_headless_driver()
    driver.get(url)
    time.sleep(1)
    content = None
    if parser_type == "xml":
        content = driver.page_source
    def xml_extract(parent, path, cast=str):
        try:
            node = parent.find(path)
            return cast(node.text.strip()) if node is not None and node.text else None
        except:
            return None
    root = None
    if parser_type == "xml" and content:
        content = html.unescape(content)
        match = re.search(
            r'(<\?xml[^>]*\?>.*?</ownershipDocument>)',
            content,
            flags=re.DOTALL
        )
        if not match:
            print("Not a Form 4")
            return
        xml_block = match.group(1)
        root = ET.fromstring(xml_block)
        insider = xml_extract(root, ".//reportingOwner/reportingOwnerId/rptOwnerName")
        issuer = xml_extract(root, ".//issuer/issuerName")
        filing_date = xml_extract(root, ".//periodOfReport")
        filing_date = filing_date[:10]  # only capture date, not time
        filing_date = datetime.datetime.strptime(filing_date.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
        accession = re.search(r"SEC FILE NUMBER:\s*([0-9\-]+)", content).group(1)
    else:
        insider = driver.find_element(By.XPATH, "/html/body/table[2]/tbody/tr[1]/td[1]/table[1]/tbody/tr/td/a").text
        issuer = driver.find_element(By.XPATH, "/html/body/table[2]/tbody/tr[1]/td[2]/a").text
        filing_date = driver.find_element(By.XPATH, "/html/body/table[2]/tbody/tr[2]/td/span[2]").text
        filing_date = datetime.datetime.strptime(filing_date.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
    filing_id = insert_filing(accession, insider, issuer, filing_date, index_url)
    count = 0
    if parser_type == "xml":
        transactions = root.findall(".//nonDerivativeTable/nonDerivativeTransaction")
        for trans in transactions:
            date = xml_extract(trans, "./transactionDate/value")
            date = date[:10]  #  only capture date, not time
            date = datetime.datetime.strptime(date.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
            title = xml_extract(trans, "./securityTitle/value")
            code = xml_extract(trans, "./transactionCoding/transactionCode")
            if code not in TRANSACTION_CODES:
                continue
            ttype = TRANSACTION_CODES[code]
            amount = xml_extract(trans, "./transactionAmounts/transactionShares/value", int)
            price = xml_extract(trans, "./transactionAmounts/transactionPricePerShare/value", float)
            if price == '0' or amount == '0':
                continue
            if not date or not title or not ttype:
                continue
            insert_trade(filing_id, date, title, ttype, amount, price)
            count += 1
    print(f"[INFO] Inserted {count} trades for {accession}")
    return count > 0

# ---------------------- Helper DB insert functions ----------------------
def insert_filing(accession, insider, issuer, filing_date, url):
    c = pg_flyway.conn.cursor()
    c.execute("""
        INSERT INTO filings (accession, insider, issuer, filing_date, url)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (accession) DO NOTHING
    """, (accession, insider, issuer, filing_date, url))
    pg_flyway.conn.commit()
    c.execute("SELECT id FROM filings WHERE accession=%s", (accession,))
    row = c.fetchone()
    if not row:
        raise RuntimeError(f"Could not get filing id for accession {accession}")
    return row[0]

def insert_trade(filing_id, date, title, ttype, amount, price):
    c = pg_flyway.conn.cursor()
    amount_val = normalize_number(amount, integer=True)
    price_val = normalize_number(price, integer=False)
    try:
        c.execute("""
            INSERT INTO trades
            (filing_id, transaction_date, security_title, transaction_type, amount, price)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (filing_id, transaction_date, security_title, transaction_type, amount, price) DO NOTHING
        """, (filing_id, date, title, ttype, amount_val, price_val))
        pg_flyway.conn.commit()
        inserted = c.rowcount > 0
        return inserted
    except Exception as e:
        print("[ERROR] Trade insert failed:", e)
        return False

# ---------------------- Minimal Form4 parsing (kept concise) ----------------------
def find_primary_document(index_url, file_type):
    # When file_type == 'xml' we treat index_url as the xml URL; otherwise extract primary doc.
    if file_type == "xml":
        return index_url
    driver = get_headless_driver()
    driver.get(index_url)
    try:
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, "//table[@class='tableFile']")))
    except:
        driver.quit()
        return None
    soup = BeautifulSoup(driver.page_source, "html.parser")
    driver.quit()
    table = soup.find("table", class_="tableFile")
    if not table:
        return None
    markers = ['form4.xml', 'primary-document.xml', '.xml', '.htm', '.html']
    for row in table.find_all("tr")[1:]:
        link = row.find("a")
        if not link:
            continue
        href = link.get("href", "").lower()
        if any(m in href for m in markers):
            return urljoin("https://www.sec.gov", link.get("href"))
    return None

# ---------------------- Government scraping helpers ----------------------
def insert_gov_official(name, role, source_url):
    c = pg_flyway.conn.cursor()
    c.execute("INSERT INTO gov_officials (name, role) VALUES (%s, %s, %s) ON CONFLICT (name, role) DO NOTHING",
              (name, role))
    pg_flyway.conn.commit()
    c.execute("SELECT id FROM gov_officials WHERE name=%s AND role=%s AND source_url=%s", (name, role, source_url))
    row = c.fetchone()
    return row[0] if row else None

def delete_gov_officials():
    c = pg_flyway.conn.cursor()
    c.execute("DELETE FROM gov_officials")
    pg_flyway.conn.commit()

def insert_gov_trade(official_id, date, title, ttype, amount, price, source_url):
    c = pg_flyway.conn.cursor()
    amount_val = normalize_number(amount, integer=True)
    price_val = normalize_number(price, integer=False)
    try:
        c.execute("""
            INSERT INTO gov_trades
            (official_id, transaction_date, security_title, transaction_type, amount, price, source_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (official_id, transaction_date, security_title, transaction_type, amount, price, source_url) DO NOTHING
        """, (official_id, date, title, ttype, amount_val, price_val, source_url))
        pg_flyway.conn.commit()
        inserted = c.rowcount > 0
        return inserted
    except Exception as e:
        print("[ERROR] Gov trade insert failed:", e)
        return False

def delete_gov_trades():
    c = pg_flyway.conn.cursor()
    c.execute("DELETE FROM gov_trades")
    pg_flyway.conn.commit()

# --- Example: scrape House PTR listings ---
def scrape_house_ptrs(limit=10):
    """
    Example scraper for House Financial Disclosure PTRs. This function is intentionally generic:
    - It navigates to a list page (CHANGE URL as needed)
    - Finds links to individual disclosure pages
    - For each disclosure with an HTML table, extracts rows with headers like "Transaction Date"
    NOTE: site structures vary — adjust selectors below for the actual listing page you target.
    """
    LIST_URL = "https://clerk.house.gov/public_disc/financial-ptrs"  # example; adjust if needed
    driver = get_headless_driver()
    driver.get(LIST_URL)
    time.sleep(1)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    driver.quit()

    # find candidate links - this might need to be adjusted
    links = []
    for a in soup.find_all("a", href=True):
        href = a['href']
        if "financial-ptr" in href or "financial" in href:
            links.append(urljoin("https://clerk.house.gov", href))
        if len(links) >= limit:
            break

    results = []
    driver = get_headless_driver()
    for link in links:
        driver.get(link)
        time.sleep(0.8)
        doc_soup = BeautifulSoup(driver.page_source, "html.parser")

        # Try to find official name
        name_tag = doc_soup.find(lambda tag: tag.name in ("h1", "h2", "h3") and "Statement" in tag.text or "Financial" in tag.text)
        name = None
        if name_tag:
            name = name_tag.get_text(strip=True)
        else:
            # fallback: try find strong name
            possible = doc_soup.find(text=re.compile(r"Reporting Person|Reporting-Owner|Reporting Person", re.I))
            name = possible.strip() if possible else "Unknown"

        # PUBLIC: find tables with headers that include "Transaction Date" (common)
        for table in doc_soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if any("transaction" in h for h in headers) and any("title" in h or "security" in h for h in headers):
                # parse rows
                body_rows = table.find_all("tr")[1:]
                trades = []
                for r in body_rows:
                    cols = [td.get_text(strip=True) for td in r.find_all(["td", "th"])]
                    if len(cols) < 4:
                        continue
                    # heuristics: try to map columns
                    # common patterns: [Title, Date, Code, Amount, Price, ...]
                    tx_date = None; sec_title = None; tx_code = None; amount = None; price = None
                    # naive mapping:
                    # find first column that looks like a date (YYYY or MM/DD/YYYY)
                    for c in cols:
                        if re.search(r'\d{4}-\d{2}-\d{2}', c) or re.search(r'\d{1,2}/\d{1,2}/\d{4}', c):
                            tx_date = c
                            break
                    # title: first column containing letters and 'Stock' or 'Common' or 'ETF' or 'Inc' etc
                    for c in cols:
                        if re.search(r'(stock|common|inc|corp|etf|shares)', c, re.I):
                            sec_title = c
                            break
                    # amount: first numeric-looking column
                    for c in cols[::-1]:
                        if re.search(r'[\d,\$]+', c):
                            # could be price or amount — prefer big numbers for amount
                            num = c.replace('$', '').replace(',', '').strip()
                            try:
                                val = float(num)
                                if val > 1000:
                                    amount = c
                                    break
                                elif amount is None:
                                    price = c
                            except:
                                continue
                    # fallback simple guess
                    if not sec_title:
                        sec_title = cols[0]
                    if not tx_date:
                        tx_date = cols[1] if len(cols) > 1 else None
                    trades.append((tx_date, sec_title, tx_code or "Trade", amount, price))
                results.append((name, "House PTR", link, trades))
                break
    return results

# --- Example: scrape Senate PTRs (placeholder; structure varies) ---
def scrape_senate_ptrs(pages=10, limit=None):
    # Placeholder similar to scrape_house_ptrs; adjust selectors when targeting actual senate site
    # For now we return empty list (or you can replicate above approach for a known senate listing URL)
    LIST_URL = "https://efdsearch.senate.gov"
    driver = get_headless_driver()
    processed, inserted = 0, 0
    row_count = 0
    page_count = 0
    while page_count < pages:
        driver.get(LIST_URL)
        time.sleep(1)
        if driver.current_url == f"{LIST_URL}/search/home/":  # agree
            checkbox = driver.find_element(By.XPATH, "//input[@id=\"agree_statement\"]")
            checkbox.click()
            time.sleep(1)
        # Click periodic reports checkbox
        if EC.presence_of_element_located((By.XPATH, "//input[@id=\"reportTypes\" and @value=\"11\"]")):
            checkbox = driver.find_element(By.XPATH, "//input[@id=\"reportTypes\" and @value=\"11\"]")
            checkbox.click()
            # Click search button
            search_button = driver.find_element(By.XPATH, "//*[@id=\"searchForm\"]/div/button")
            search_button.click()
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//table[@id=\"filedReports\"]"))
            )
        except:
            driver.quit()
            raise Exception("error: Senate PTR table not fully loaded")
        # Sort by date descending
        time.sleep(1)
        driver.execute_script("window.scrollTo(0, 0);")
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//th[contains(@aria-label, 'Date')]"))
            )
        except:
            driver.quit()
            raise Exception("error: Date sorting did not appear")
        date_sort = driver.find_element(By.XPATH, "//th[contains(@aria-label, 'Date')]")
        date_sort.click()
        time.sleep(0.5)
        date_sort.click()
        time.sleep(2)
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//table[@id=\"filedReports\"]"))
            )
        except:
            driver.quit()
            raise Exception("error: Senate PTR table not fully loaded")
        for _ in range(page_count):
            try:
                WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//a[@class=\'paginate_button next\']"))
                )
            except:
                driver.quit()
                raise Exception("error: Next page button did not load in time")
            next_page = driver.find_element(By.XPATH, "//a[@class=\'paginate_button next\']")
            next_page.click()
            time.sleep(1.5)
        # Iterate through rows
        soup = BeautifulSoup(driver.page_source, "html.parser")
        # driver.quit()
        table = soup.find("tbody")
        for row in table.find_all("tr"):
            cols = row.find_all("td")
            first_name = cols[0].text
            last_name = cols[1].text
            office = cols[2].text
            if '(' in office:
                office = office[office.index('(')+1:office.index(')')]
            report_link = cols[3].find('a').get("href")
            date_filed = cols[4].text
            official_id = insert_gov_official(f"{first_name} {last_name}", office, LIST_URL)
            processed, inserted = process_senate_ptr(LIST_URL, report_link, official_id, driver)
            row_count += 1
            if limit and row_count == limit:
                break
        if limit and row_count == limit:
            break
        page_count += 1
    driver.quit()
    return processed, inserted

def process_senate_ptr(LIST_URL, report_link, official_id, driver):
    driver.get(f"{LIST_URL}/{report_link}")
    time.sleep(1)
    if driver.current_url == f"{LIST_URL}/search/home/":  # agree
        checkbox = driver.find_element(By.XPATH, "//input[@id=\"agree_statement\"]")
        checkbox.click()
        time.sleep(1)
        driver.get(f"{LIST_URL}/{report_link}")
    try:
        WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.XPATH, "//tbody"))
        )
    except:
        print("Transactions table did not load. Skipping this filing.")
        return 0, 0
    soup = BeautifulSoup(driver.page_source, "html.parser")
    # driver.quit()
    table = soup.find("tbody")
    processed = 0
    inserted = 0
    for row in table.find_all("tr"):
        cols = row.find_all("td")
        transaction_date = cols[1].text
        transaction_date = datetime.datetime.strptime(transaction_date.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
        owner = cols[2].text
        ticker = cols[3]
        if ticker.find('a'):
            ticker = ticker.find('a').get("href")[ticker.find('a').get("href").index('=')+1:]
        else:
            ticker = cols[3].text
        asset_name = cols[4].text
        asset_type = cols[5].text
        ttype = cols[6].text
        amount = cols[7].text
        if '-' in amount:
            low = amount[:amount.index('-')].strip()
            high = amount[amount.index('-')+1:].strip()
            low = low.replace('$', '')
            high = high.replace('$', '')
            high = high.replace(',', '')
            low = low.replace(',', '')
            amount = round((int(high) + int(low)) / 2, 2)
        amount = float(str(amount).replace('$', ''))

        ok = insert_gov_trade(official_id, transaction_date, f"{asset_name} ({ticker})", ttype, amount, "N/A", f"{LIST_URL}/{report_link}")
        processed += 1
        inserted += ok
    return processed, inserted

# ---------------------- Pull government disclosures ----------------------
@app.route("/pull_gov_once")
def pull_gov_once():
    """
    Pull recent government disclosures (house PTRs, senate, etc.) and ingest into gov_officials/gov_trades.
    This endpoint orchestrates the example scrapers above. Tweak as needed for your reliable sources.
    """
    inserted = 0
    processed = 0
    # House PTRs
    # house_results = scrape_house_ptrs(limit=20)
    # for name, role, source_url, trades in house_results:
    #     processed += 1
    #     off_id = insert_gov_official(name, role, source_url)
    #     for (tx_date, sec_title, tx_code, amount, price) in trades:
    #         # tx_code may be None; we keep as-is
    #         if tx_date is None or sec_title is None:
    #             continue
    #         ok = insert_gov_trade(off_id, tx_date, sec_title, tx_code or "N/A", amount, price, source_url)
    #         if ok:
    #             inserted += 1
    # Senate & others (placeholders)
    processed, inserted = scrape_senate_ptrs()
    return jsonify({"processed": processed, "inserted": inserted})

# ---------------------- Tracked endpoints (shared) ----------------------
@app.route("/track/<insider>", methods=["POST"])
def track_insider(insider):
    c = pg_flyway.conn.cursor()
    c.execute("INSERT INTO tracked_insiders (insider) VALUES (%s) ON CONFLICT (insider) DO NOTHING", (insider,))
    pg_flyway.conn.commit()
    pg_flyway.conn.close()
    return jsonify({"status": "tracked", "insider": insider})

@app.route("/untrack/<insider>", methods=["POST"])
def untrack_insider(insider):
    c = pg_flyway.conn.cursor()
    c.execute("DELETE FROM tracked_insiders WHERE insider=%s", (insider,))
    pg_flyway.conn.commit()
    return jsonify({"status": "untracked", "insider": insider})

# ---------------------- Dashboards ----------------------

@app.route("/sec_dashboard")
def dashboard():
    """Original Form 4 dashboard (shows trades from 'trades' table)."""
    c = pg_flyway.conn.cursor()
    c.execute("""
        SELECT filings.insider, filings.issuer, trades.transaction_date,
               trades.security_title, trades.transaction_type,
               trades.amount, trades.price, filings.url,
               CASE WHEN tracked_insiders.insider IS NOT NULL THEN 1 ELSE 0 END AS is_tracked
        FROM trades
        JOIN filings ON trades.filing_id = filings.id
        LEFT JOIN tracked_insiders ON filings.insider = tracked_insiders.insider
        ORDER BY is_tracked DESC, trades.transaction_date DESC
        LIMIT 1000
    """)
    rows = c.fetchall()

    html = """
    <h1>SEC Form 4 — Government Insider Trades</h1>

    <style>
        tr.tracked { background-color: #fff3cd !important; }
        tr:hover { background: #e0e0e0; cursor: pointer; }
    </style>

    <button onclick="pullOnce()" style="padding:10px 20px; background:#0074D9; color:white; border:none; cursor:pointer; font-size:16px;">Pull Latest Form 4s</button>
    <button onclick="location.href='/gov_dashboard'" style="padding:10px 20px; margin-left:10px;">Open Gov Dashboard</button>
    <a href="/dashboard_tracked" style="padding:10px 20px; background:#FF851B; color:white; text-decoration:none; border-radius:4px; margin-right:10px;">View Tracked Only</a>
    <p id="status" style="font-weight:bold; margin-top:10px;"></p>
    <script>
    function pullOnce() {
        document.getElementById("status").innerText = "Pulling latest filings...";
        fetch('/pull_once')
            .then(r => r.json())
            .then(data => {
                document.getElementById("status").innerText = "Processed " + data.processed + ", Inserted " + data.inserted;
                setTimeout(() => location.reload(), 1000);
            }).catch(e=> { document.getElementById("status").innerText = "Error"; })
    }
    function toggleTrack(insider, row) {
        const isTracked = row.classList.contains("tracked");
        const url = isTracked ? `/untrack/${insider}` : `/track/${insider}`;
        fetch(url, { method: "POST" })
          .then(r => r.json())
          .then(data => {
              row.classList.toggle("tracked");
              setTimeout(() => location.reload(), 500);
          });
    }
    </script>

    <table border='1' cellpadding='5' width='100%'>
        <tr>
            <th>Insider</th><th>Issuer</th><th>Date</th><th>Security</th><th>Type</th><th>Amount</th><th>Price</th>
        </tr>
        {% for r in rows %}
        <tr class="{% if r[8] == 1 %}tracked{% endif %}" onclick="toggleTrack(`{{ r[0] }}`, this)">
            <td>{{ r[0] }}</td>
            <td>{{ r[1] }}</td>
            <td><a href="{{ r[7] }}" target="_blank">{{ r[2] }}</a></td>
            <td>{{ r[3] }}</td>
            <td>{{ r[4] }}</td>
            <td>{{ r[5] }}</td>
            <td>{{ r[6] }}</td>
        </tr>
        {% endfor %}
    </table>
    """
    return render_template_string(html, rows=rows)

@app.route("/gov_dashboard")
def gov_dashboard():
    """
    Government officials dashboard (shows gov_trades joined with gov_officials).
    Tracked insiders from tracked_insiders table will float to the top and be highlighted.
    """
    c = pg_flyway.conn.cursor()
    c.execute("""
        SELECT go.name, go.role, gt.transaction_date, gt.security_title,
               gt.transaction_type, gt.amount, gt.price, gt.source_url,
               CASE WHEN ti.insider IS NOT NULL THEN 1 ELSE 0 END AS is_tracked
        FROM gov_trades gt
        JOIN gov_officials go ON gt.official_id = go.id
        LEFT JOIN tracked_insiders ti ON go.name = ti.insider
        ORDER BY is_tracked DESC, gt.transaction_date DESC
        LIMIT 1000
    """)
    rows = c.fetchall()

    html = """
    <h1>Periodic Trade Reports — Government Disclosures</h1>

    <style>
        tr.tracked { background-color: #fff3cd !important; }
        tr:hover { background: #e0e0e0; cursor: pointer; }
    </style>

    <button onclick="pullGovOnce()" style="padding:10px 20px; background:#28a745; color:white; border:none; cursor:pointer; font-size:16px;">Pull Gov Disclosures</button>
    <button onclick="location.href='/sec_dashboard'" style="padding:10px 20px; margin-left:10px;">Open SEC Form4 Dashboard</button>
    <a href="/dashboard_tracked" style="padding:10px 20px; background:#FF851B; color:white; text-decoration:none; border-radius:4px; margin-right:10px;">View Tracked Only</a>

    <p id="status" style="font-weight:bold; margin-top:10px;"></p>

    <script>
    function pullGovOnce() {
        document.getElementById("status").innerText = "Pulling government disclosures...";
        fetch('/pull_gov_once')
            .then(r => r.json())
            .then(data => {
                document.getElementById("status").innerText = "Processed " + data.processed + ", Inserted " + data.inserted;
                setTimeout(() => location.reload(), 1000);
            }).catch(e=> { document.getElementById("status").innerText = "Error"; })
    }
    function toggleTrack(name, row) {
        const isTracked = row.classList.contains("tracked");
        const url = isTracked ? `/untrack/${name}` : `/track/${name}`;
        fetch(url, { method: "POST" })
          .then(r => r.json())
          .then(data => {
              row.classList.toggle("tracked");
              setTimeout(() => location.reload(), 500);
          });
    }
    </script>

    <table border='1' cellpadding='5' width='100%'>
        <tr>
            <th>Official</th><th>Role</th><th>Date</th><th>Security</th><th>Type</th><th>Amount</th><th>Price</th>
        </tr>
        {% for r in rows %}
        <tr class="{% if r[8] == 1 %}tracked{% endif %}" onclick="toggleTrack(`{{ r[0] }}`, this)">
            <td>{{ r[0] }}</td>
            <td>{{ r[1] }}</td>
            <td><a href="{{ r[7] }}" target="_blank">{{ r[2] }}</a></td>
            <td>{{ r[3] }}</td>
            <td>{{ r[4] }}</td>
            <td>{{ r[5] }}</td>
            <td>{{ r[6] }}</td>
        </tr>
        {% endfor %}
    </table>
    """
    return render_template_string(html, rows=rows)

@app.route("/dashboard_tracked")
def dashboard_tracked():
    c = pg_flyway.conn.cursor()

    # --- SEC tracked insiders ---
    c.execute("""
        SELECT filings.insider, filings.issuer, trades.transaction_date,
               trades.security_title, trades.transaction_type,
               trades.amount, trades.price, filings.url, 'SEC' AS source
        FROM trades
        JOIN filings ON trades.filing_id = filings.id
        JOIN tracked_insiders ON filings.insider = tracked_insiders.insider
    """)

    sec_rows = c.fetchall()

    # --- GOV tracked officials ---
    c.execute("""
        SELECT go.name, go.role, gt.transaction_date,
               gt.security_title, gt.transaction_type,
               gt.amount, gt.price, gt.source_url, 'GOV' AS source
        FROM gov_trades gt
        JOIN gov_officials go ON gt.official_id = go.id
        JOIN tracked_insiders ti ON go.name = ti.insider
    """)

    gov_rows = c.fetchall()

    # merge and sort by date desc
    rows = sec_rows + gov_rows
    rows.sort(key=lambda r: r[2], reverse=True)  # sort by transaction_date

    html = """
    <h1>Tracked Trades (SEC + Government)</h1>
    <p>This view shows <b>ALL</b> tracked insiders + government officials.</p>

    <a href="/sec_dashboard"
       style="padding:10px 20px; background:#0074D9; color:white; text-decoration:none;">
       ← Back to Main Dashboard
    </a>

    <br><br>

    <style>
        tr:hover { background: #e0e0e0; }
        th { background: #f0f0f0; }
    </style>

    <table border='1' cellpadding='5' width='100%'>
        <tr>
            <th>Name</th>
            <th>Issuer/Role</th>
            <th>Date</th>
            <th>Security</th>
            <th>Type</th>
            <th>Amount</th>
            <th>Price</th>
            <th>Source</th>
        </tr>

        {% for r in rows %}
        <tr>
            <td>{{ r[0] }}</td>
            <td>{{ r[1] }}</td>
            <td><a href="{{ r[7] }}" target="_blank">{{ r[2] }}</a></td>
            <td>{{ r[3] }}</td>
            <td>{{ r[4] }}</td>
            <td>{{ r[5] }}</td>
            <td>{{ r[6] }}</td>
            <td>{{ r[8] }}</td>
        </tr>
        {% endfor %}
    </table>
    """

    return render_template_string(html, rows=rows)

# ---------------------- Run App ----------------------
if __name__ == "__main__":
    app.run(port=5050, debug=True)
    # delete_gov_officials()
    # delete_gov_trades()