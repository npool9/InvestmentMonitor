import sqlite3
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


app = Flask(__name__)
DB_PATH = "govtrades.db"
HEADERS = {"User-Agent": "GovTradeMonitor/1.0"}

prev_url = None
TRANSACTION_CODES = {
    "P": "Purchase",
    "S": "Sale",
    "A": "Award/Grant",
    "D": "Disposition to Issuer",
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


# ---------------------- Database Setup ----------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS filings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        accession TEXT UNIQUE,
        insider TEXT,
        issuer TEXT,
        filing_date TEXT,
        url TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filing_id INTEGER,
        transaction_date TEXT,
        security_title TEXT,
        transaction_type TEXT,
        amount INTEGER,
        price REAL,
        FOREIGN KEY(filing_id) REFERENCES filings(id),
        UNIQUE(filing_id, transaction_date, security_title, transaction_type, amount, price)
    )
    """)

    # >>> ADDED FOR TRACKING <<<
    c.execute("""
    CREATE TABLE IF NOT EXISTS tracked_insiders (
        insider TEXT PRIMARY KEY
    )
    """)

    conn.commit()
    conn.close()

init_db()


# ---------------------- Tracking Endpoints ----------------------
# >>> ADDED FOR TRACKING <<<
@app.route("/track/<insider>", methods=["POST"])
def track_insider(insider):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO tracked_insiders (insider) VALUES (?)", (insider,))
    conn.commit()
    conn.close()
    return jsonify({"status": "tracked", "insider": insider})


# >>> ADDED FOR TRACKING <<<
@app.route("/untrack/<insider>", methods=["POST"])
def untrack_insider(insider):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM tracked_insiders WHERE insider=?", (insider,))
    conn.commit()
    conn.close()
    return jsonify({"status": "untracked", "insider": insider})



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


# ---------------------- Pull Recent Form 4 Feed ----------------------
start = 0
increment = 100
FORM4_FEED_URL = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&datea=&dateb=&company=&type=4&SIC=&State=&Country=&CIK=&owner=include&accno=&start={start}&count={increment}"
doc_type = {"html": 0, "xml": 1}
file_type = "xml"

@app.route("/pull_once")
def pull_once():
    global start
    global increment
    driver = get_headless_driver()
    for i in range(50):
        print("Page:", i)
        FORM4_FEED_URL = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&datea=&dateb=&company=&type=4&SIC=&State=&Country=&CIK=&owner=include&accno=&start={start}&count={increment}"
        driver.get(FORM4_FEED_URL)

        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located(
                    (By.XPATH, "/html/body/div/table[2]")
                )
            )
        except:
            driver.quit()
            return jsonify({"error": "Form 4 table not fully loaded"})

        soup = BeautifulSoup(driver.page_source, "html.parser")
        tables = soup.find_all("table")
        table = tables[6]
        if not table:
            return jsonify({"error": "Form 4 table could not be parsed"})

        rows = table.find_all("tr")
        processed = 0
        inserted = 0

        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 5:
                continue

            link = cols[1].find_all("a")[doc_type[file_type]]
            if not link:
                continue

            index_url = urljoin("https://www.sec.gov", link.get("href"))
            accession = link.get("href").strip()

            if process_filing(index_url, accession):
                inserted += 1
            processed += 1

        start += increment

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


# ---------------------- Primary Document Detection ----------------------
def find_primary_document(index_url, file_type):
    if file_type == "xml":
        return index_url

    driver = get_headless_driver()
    driver.get(index_url)

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//table[@class='tableFile']"))
        )
    except:
        driver.quit()
        return None

    soup = BeautifulSoup(driver.page_source, "html.parser")
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

        accession = re.search(r"SEC FILE NUMBER:\s*([0-9\-]+)", content).group(1)

    else:
        insider = driver.find_element(By.XPATH, "/html/body/table[2]/tbody/tr[1]/td[1]/table[1]/tbody/tr/td/a").text
        issuer = driver.find_element(By.XPATH, "/html/body/table[2]/tbody/tr[1]/td[2]/a").text
        filing_date = driver.find_element(By.XPATH, "/html/body/table[2]/tbody/tr[2]/td/span[2]").text

    filing_id = insert_filing(accession, insider, issuer, filing_date, index_url)

    count = 0

    if parser_type == "xml":
        transactions = root.findall(".//nonDerivativeTable/nonDerivativeTransaction")

        for trans in transactions:
            date = xml_extract(trans, "./transactionDate/value")
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


# ---------------------- DB Helpers ----------------------
def insert_filing(accession, insider, issuer, filing_date, url):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        INSERT OR IGNORE INTO filings (accession, insider, issuer, filing_date, url)
        VALUES (?, ?, ?, ?, ?)
    """, (accession, insider, issuer, filing_date, url))

    conn.commit()

    c.execute("SELECT id FROM filings WHERE accession=?", (accession,))
    row = c.fetchone()

    if row is None:
        conn.close()
        raise RuntimeError(f"Filing row not found for accession: {accession}")

    filing_id = row[0]
    conn.close()
    return filing_id



def normalize_number(s, integer=False):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s) if integer else float(s)

    s = str(s).strip()
    if not s:
        return None

    s = s.replace('$', '').replace(',', '').strip()

    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]

    try:
        value = float(s)
        if integer:
            value = int(value)
        return -value if neg else value
    except:
        return None



def insert_trade(filing_id, date, title, ttype, amount, price):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    amount_val = normalize_number(amount, integer=True)
    price_val = normalize_number(price, integer=False)

    try:
        c.execute("""
            INSERT OR IGNORE INTO trades
            (filing_id, transaction_date, security_title, transaction_type, amount, price)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (filing_id, date, title, ttype, amount_val, price_val))

        conn.commit()
        inserted = c.rowcount > 0
        conn.close()

        if not inserted:
            print(f"[DEBUG] Duplicate trade ignored: ({filing_id}, {date}, {title}, {ttype})")

        return inserted

    except Exception as e:
        print("[ERROR] Trade insert failed:", e)
        conn.close()
        return False



# ---------------------- Dashboard ----------------------
@app.route("/dashboard")
def dashboard():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # >>> UPDATED QUERY FOR TRACKING <<<
    c.execute("""
        SELECT filings.insider, filings.issuer, trades.transaction_date,
               trades.security_title, trades.transaction_type,
               trades.amount, trades.price, filings.url,
               CASE WHEN tracked_insiders.insider IS NOT NULL THEN 1 ELSE 0 END AS is_tracked
        FROM trades
        JOIN filings ON trades.filing_id = filings.id
        LEFT JOIN tracked_insiders ON filings.insider = tracked_insiders.insider
        ORDER BY is_tracked DESC, trades.transaction_date DESC
        LIMIT 100000
    """)

    rows = c.fetchall()
    conn.close()

    html = """
    <h1>Government Insider Trades</h1>

    <style>
        tr.tracked {
            background-color: #fff3cd !important;  /* yellow highlight */
        }
        tr:hover {
            background: #e0e0e0;
            cursor: pointer;
        }
    </style>

    <button onclick="pullOnce()" 
            style="padding:10px 20px; background:#0074D9; color:white; border:none; cursor:pointer; font-size:16px;">
        Pull Latest Filings
    </button>

    <p id="status" style="font-weight:bold; margin-top:10px;"></p>

    <script>
    function pullOnce() {
        document.getElementById("status").innerText = "Pulling latest filings...";
        fetch('/pull_once')
            .then(r => r.json())
            .then(data => {
                document.getElementById("status").innerText =
                    "Processed " + data.processed + ", Inserted " + data.inserted;
                setTimeout(() => location.reload(), 1000);
            })
            .catch(err => {
                document.getElementById("status").innerText = "Error pulling filings";
            });
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

    <br><br>

    <table border='1' cellpadding='5' width='100%'>
        <tr>
            <th>Insider</th><th>Issuer</th><th>Date</th>
            <th>Security</th><th>Type</th><th>Amount</th><th>Price</th>
        </tr>

        {% for r in rows %}
        <tr class="{% if r[8] == 1 %}tracked{% endif %}"
            onclick="toggleTrack(`{{ r[0] }}`, this)">
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



# ---------------------- Run App ----------------------
if __name__ == "__main__":
    app.run(port=5050, debug=True)
