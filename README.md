# ACCOUNTING PRO

**Complete Computerized Accounting & Business Management System**
Final-year practical project — a real, working double-entry accounting application built with
Python Flask, SQLAlchemy, SQLite, Bootstrap 5 and Chart.js.

> Every figure in the application is calculated from records you enter. There are no sample
> numbers in the code: with an empty database every report shows **Rs. 0.00**.

---

## 1. Quick start

```bash
# 1. install dependencies
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. (optional) load clearly-labelled demo data so you can explore every screen
python seed_demo.py               # demo login: demo@accountingpro.local / Demo12345

# 3. run
python app.py                     # http://127.0.0.1:5000
```

Start empty instead of using the demo:

```bash
python reset_db.py                # empty database (a safety copy is saved first)
```

Run the automated test suite:

```bash
python -m pytest -q               # 53 tests
```

### Going live

The repo is deployment-ready: `api/index.py` + `vercel.json` for Vercel,
`render.yaml` + `Procfile` (gunicorn) for Render/Railway/Fly/VPS, and a
serverless mode in `config.py` that moves the SQLite file and the upload folders
to a writable temp directory on platforms with a read-only bundle.

Full instructions, environment variables and the one important caveat —
**SQLite does not persist on Vercel**, so Vercel is for a public demo while a
host with a disk (Render) is for real data — are in **[DEPLOY.md](DEPLOY.md)**.

---

## 2. Feature matrix

| Area | What is implemented |
|------|--------------------|
| **Authentication** | Registration, login, logout, forgot/reset password, PBKDF2-SHA256 hashing (600k iterations), change password, session protection, CSRF tokens on every POST |
| **Data isolation** | Every business, account, voucher, adjustment, product and report is scoped to the signed-in user; foreign ids return 404 |
| **Business profile** | Name, logo upload, owner, address, phone, email, type, currency (PKR default), financial year, accounting method, description |
| **Accounting periods** | Default 1 Jan 2026 – 31 Mar 2026; add monthly / quarterly / half-yearly / yearly / custom periods and activate one |
| **Chart of accounts** | 5 categories, 24 subcategories, codes, normal balance, opening debit/credit, activate/deactivate, search & filter, duplicate-code protection, one-click standard chart (32 accounts, structure only) |
| **Transaction entry** | 20+ line compound vouchers, live debit/credit totals and difference, post or save draft, edit draft, delete draft, unique voucher numbers, attachments, reversal entries with audit trail |
| **General journal** | Date/account/search filters, running totals, print, PDF, Word, Excel |
| **General ledger** | Per-account ledger with opening balance, running balance, Dr/Cr side, closing balance, export |
| **Trial balance** | Generated from opening balances + posted vouchers, grouped by category, balanced/not-balanced status, manual entry and import, never forced to balance |
| **Adjusting entries** | 11 adjustment types (closing stock, outstanding, prepaid, accrued, advance, depreciation, bad debts, provision, interest on capital/drawings, other) with compound lines and a debit/credit guide |
| **Adjusted trial balance** | Original TB + adjustment columns + adjusted columns, with validation |
| **Trading account** | Opening stock, purchases net of returns, direct expenses, closing stock, COGS, net sales, gross profit/loss + workings panel |
| **Profit & loss** | Gross result brought down, other income, indirect expenses, depreciation, bad debts, net profit/loss |
| **Balance sheet** | Classified assets/liabilities/equity, net profit and drawings inside equity, live balance check and a diagnostic panel when it does not balance |
| **Closing entries** | Preview → confirm → post; closes revenue, expenses, opening stock and trading contra to capital; reconciliation against the P&L result |
| **Post-closing TB** | Permanent accounts only; lists any temporary account still open |
| **Cash flow** | Operating / investing / financing classification from cash & bank movements, opening and closing balances, reconciliation |
| **Analysis** | Daily / weekly / monthly / quarterly / half-yearly / yearly / custom; current vs previous period, growth %, annual summary, charts |
| **Products & inventory** | Product master, costs, prices, purchases, sales, returns, stock value, margin, low-stock alerts, optional auto journal drafts |
| **Budgets** | Monthly / quarterly / annual plans, actual vs planned income and expenses, variance and utilisation charts |
| **Import center** | Excel (.xlsx/.xls) and CSV: chart of accounts, opening balances, journal entries, adjustments, products — map → validate → preview → confirm |
| **Export center** | PDF, Word and Excel for every report plus a complete accounting file (17-sheet Excel workbook) |
| **Backup & restore** | Download SQLite backup, integrity validation, confirmation, automatic safety copy before restore |
| **Audit log** | User, action, record type/id, timestamp, previous and new values, IP |
| **Settings** | Profile, password, currency, currency symbol, invoice prefix/terms, report footer, decimals |
| **PWA** | Web manifest, service worker, installable on Android/iOS/desktop where supported |

---

## 3. Design

| Token | Value |
|-------|-------|
| Primary (dark navy) | `#101D3A` |
| Secondary (royal blue) | `#2457D6` |
| Background | `#F5F7FB` |
| Cards | `#FFFFFF` |
| Success / Warning / Danger | `#12805C` / `#B7791F` / `#C0392B` |

* Desktop: fixed left sidebar + sticky top bar + dashboard grid.
* Mobile (≤991px): sidebar becomes a slide-in drawer with a backdrop; tables collapse into
  labelled cards (`table-stack`); buttons are touch-sized; no horizontal overflow; forms stack.
* Print stylesheet strips navigation for clean report printing.
* Bootstrap 5 and Chart.js are **vendored locally** in `static/vendor/` so the app works offline.

---

## 4. Project structure

```
accounting_pro/
├── app.py                      # Flask app factory + all routes
├── config.py                   # configuration (DB, uploads, defaults)
├── models.py                   # SQLAlchemy models (16 tables)
├── extensions.py               # shared db instance
├── seed_demo.py                # OPTIONAL demo data loader
├── reset_db.py                 # reset / reset --demo
├── requirements.txt
├── README.md
├── accounting/
│   ├── journal.py              # voucher validation, posting, reversal
│   ├── ledger.py               # single source of truth for balances
│   ├── trial_balance.py        # TB, adjusted TB, post-closing TB
│   ├── adjustments.py          # adjusting entries
│   ├── financial_statements.py # trading, P&L, balance sheet, cash flow
│   ├── closing.py              # closing entries + reconciliation
│   ├── reports.py              # journal / ledger report builders
│   ├── inventory.py            # products, sales, purchases, stock
│   └── analytics.py            # dashboard, series, comparisons, budgets
├── exports/
│   ├── reports_data.py         # one report model shared by all formats
│   ├── pdf_export.py           # ReportLab A4 renderer
│   ├── word_export.py          # python-docx renderer
│   └── excel_export.py         # openpyxl single + 17-sheet workbook
├── imports/
│   ├── excel_import.py
│   ├── csv_import.py
│   └── processor.py            # mapping, validation, apply
├── templates/                  # 34 Jinja templates (base, auth, 30 pages)
├── static/
│   ├── css/style.css
│   ├── js/app.js, charts.js
│   ├── vendor/                 # bootstrap + chart.js (local copies)
│   ├── icons/                  # PWA icons
│   ├── manifest.json
│   └── service-worker.js
├── uploads/                    # logos, attachments, imports, backups
└── tests/                      # conftest + 4 test modules (52 tests)
```

### Database tables

`users`, `user_settings`, `businesses`, `accounting_periods`, `accounts`,
`journal_entries`, `journal_lines`, `adjustments`, `adjustment_lines`,
`closing_entries`, `closing_lines`, `products`, `inventory_transactions`, `sales`,
`purchases`, `budgets`, `audit_logs`.

Money is stored as **INTEGER paisa** through a custom `Money` type decorator and always
handled as `Decimal` in Python — floating point never touches a financial figure.

---

## 5. How the accounting engine works

1. **One source of truth** — `accounting/ledger.py` normalises posted journal lines,
   adjustment lines and closing lines into a single row format. Every report reads from it.
2. **Validation before write** — `accounting/journal.py` rejects unbalanced vouchers,
   negative amounts, lines with both debit and credit, empty lines, duplicate voucher
   numbers, foreign accounts and inactive accounts. Posting happens inside a transaction
   with rollback on failure.
3. **Immutability** — posted vouchers cannot be edited or deleted; corrections are made with
   a reversal voucher (`reversal_of_id` link) so the audit trail is preserved.
4. **Normal balances** — debit-normal accounts (assets, expenses) and credit-normal accounts
   (liabilities, equity, revenue) drive ledger balances and Dr/Cr presentation.
5. **Statements** — opening balances + posted vouchers + posted adjustments:
   * `COGS = Opening Stock + Net Purchases + Direct Expenses − Closing Stock`
   * `Gross Profit = Net Sales − COGS`
   * `Net Profit = Gross Profit + Other Income − (Indirect + Depreciation + Bad Debts)`
   * `Total Assets = Total Liabilities + Owner's Equity` (difference reported, never plugged)
6. **Closing** — temporary accounts (revenue, expenses, opening stock, trading contra) are
   zeroed; the difference is transferred to capital and reconciled against the P&L result.
7. **Deletion is never silent** — drafts can be deleted, posted records cannot.

### Closing stock adjustment (important)

Use **Adjusting Entries → Closing Stock** (or post the same pair manually):

| Dr | Cr |
|----|----|
| Closing Stock / Inventory *(Assets → inventory)* | Trading A/c (Closing Stock Contra) *(Revenue → trading_contra)* |

The inventory debit feeds the Trading Account and the Balance Sheet; the contra credit keeps
the profit calculation and the balance sheet in agreement. The standard chart of accounts
already contains both accounts (1030 and 4030).

---

## 6. Using the system (suggested order)

1. **Register** → complete **Business Profile** (your real business name, logo, financial year).
2. **Chart of Accounts** → “Load standard chart”, then edit/add accounts and enter opening
   balances (or use *Trial Balance → Enter / import trial balance*).
3. **New Voucher** → enter your 24 practical transactions (compound vouchers supported).
   The Post button stays disabled until total debit equals total credit.
4. **Adjusting Entries** → closing stock, depreciation, outstanding/prepaid items.
5. Review **Trial Balance → Adjusted Trial Balance → Trading → P&L → Balance Sheet**.
6. **Closing Entries** → preview, save draft, confirm and post → **Post-Closing Trial Balance**.
7. **Export Center** → PDF / Word / Excel, individually or the complete file.
8. **Backup & Restore** → download a `.db` copy after each session.

The period selector in the top bar (desktop) or the dashboard period card controls the
reporting window for every screen and export.

---

## 7. Import formats

The Import Center matches column headings automatically and lets you remap them.

**Chart of accounts**
`code, name, category, subcategory, opening_debit, opening_credit, description`

**Opening balances**
`code, opening_debit, opening_credit`

**Journal entries** (one row per line, grouped by voucher)
`voucher_no, date, account_code, debit, credit, description, narration`

**Adjustments**
`adj_no, date, kind, account_code, debit, credit, description`

**Products**
`code, name, category, purchase_cost, selling_price, opening_qty, reorder_level`

Validation reports: missing required fields, unknown accounts, duplicate voucher numbers,
invalid dates, negative amounts, lines with both sides, unbalanced vouchers, existing product
codes. Nothing is written until you press **Confirm import**.

---

## 8. Exports

* **PDF** (ReportLab): A4, cover page with logo and business details, repeated table headers,
  page numbers, header/footer on every page, `Rs. 1,234.00` formatting, column widths
  computed so amounts are never clipped.
* **Word** (.docx): cover page, headings, real Word tables, headers/footers with page fields.
* **Excel** (.xlsx): formatted headers, borders, currency and date formats, frozen panes,
  autofilters, totals rows. The complete workbook contains 17 worksheets:
  Business Information, Chart of Accounts, Opening Balances, General Journal, General Ledger,
  Trial Balance, Adjusting Entries, Adjusted Trial Balance, Trading Account, Profit & Loss,
  Balance Sheet, Closing Entries, Post-Closing TB, Annual Analysis, Product Sales, Inventory,
  Budget Reports.

---

## 9. Demo data

`python seed_demo.py` creates one user and one business named **“DEMO - Sample Traders”**
with 13 vouchers, 2 adjustments, 3 products and 1 budget so that every report and chart has
data to display. The figures are internally consistent:

```
Trial balance       Dr 1,560,000.00 = Cr 1,560,000.00   (balanced)
Trading account     Sales 560,000 − COGS 320,000 = Gross profit 240,000
Profit & loss       240,000 − 165,000 = Net profit 75,000
Balance sheet       Assets 1,045,000 = Liabilities 200,000 + Equity 845,000
```

Delete it whenever you want a clean start: `python reset_db.py`.

---

## 10. Security notes

* PBKDF2-SHA256 password hashing with 600,000 iterations.
* CSRF token required on every state-changing request (form field or `X-CSRF-Token`).
* SQL injection protection through SQLAlchemy parameter binding.
* Upload validation: extension allow-list, 8 MB request limit, random file names, path
  traversal protection when serving uploads.
* User data isolation enforced in every query (`business_id == current business`).
* Posted financial records are immutable; every mutation is written to the audit log.
* Errors are handled with friendly pages — no stack traces or SQL leak to the browser.

**Before deploying:** set a strong `SECRET_KEY` environment variable, run behind a WSGI server
(e.g. `waitress-serve --port 8000 app:app`) and serve over HTTPS.

---

## 11. Tests

```bash
python -m pytest -q
```

| Module | Covers |
|--------|--------|
| `test_auth.py` | registration, duplicate email, weak password, login/logout, password change, password reset, **cross-user isolation**, business profile |
| `test_accounting.py` | accounts CRUD, duplicate codes, opening balance rules, balanced/unbalanced vouchers, negative amounts, duplicate vouchers, immutability, compound entries, credit purchases/sales, expenses, capital, drafts, reversals, ledger running balances, journal totals, opening balances in the TB |
| `test_reports.py` | adjustments, adjusted TB, trading account, P&L, balance sheet (balanced **and** unbalanced), closing entries, post-closing TB, cash flow, all report pages, empty-state figures |
| `test_import_export.py` | CSV imports (accounts, journal, products, duplicates), PDF/Word/Excel exports, 17-sheet workbook, backup download, restore validation, audit log, product/inventory flow, budgets |

Result: **53 passed**.

---

## 12. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError: Flask` | activate the virtualenv and `pip install -r requirements.txt` |
| Port 5000 already in use | `PORT=5001 python app.py` |
| Charts do not appear | make sure `static/vendor/chart.umd.min.js` exists (it is bundled) |
| Balance sheet does not balance | open the diagnostic panel — usually a missing closing-stock contra credit or an account with the wrong subcategory |
| “Voucher number already exists” | voucher numbers are unique per business; let the app suggest the next one |
| Login fails after a restore | restoring replaces the whole database — sign in with credentials from the restored backup |

---

## 13. Academic notes

The project demonstrates: relational modelling with constraints and foreign keys, a decimal-safe
money type, a layered accounting engine, double-entry validation, statement generation, period
closing, analytics, file import/export in three formats, authentication and CSRF protection,
audit logging, backup/restore, responsive UI design and automated testing — with no invented
financial figures anywhere in the codebase.
