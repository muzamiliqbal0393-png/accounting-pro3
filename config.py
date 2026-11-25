"""
Application configuration for ACCOUNTING PRO.

Works in two shapes:

* local / VPS / container  - data lives next to the code (``accounting_pro.db``)
* serverless (Vercel etc.) - the code bundle is read-only, so the database and the
  upload folders are redirected to the writable temp directory and the bundled
  demo database is copied there on the first request of each instance.
"""
import os
import shutil
import tempfile
from decimal import Decimal

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Vercel sets VERCEL=1; AWS Lambda (and therefore Vercel's Python runtime) sets
# AWS_LAMBDA_FUNCTION_NAME.  Either one means "read-only bundle, writable /tmp".
IS_SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

# Where the SQLite file and the upload folders live.
DATA_DIR = os.path.abspath(
    os.environ.get("ACCOUNTING_PRO_DATA_DIR")
    or (os.path.join(tempfile.gettempdir(), "accounting-pro-data")
        if IS_SERVERLESS else BASE_DIR)
)

# Copy the demo database shipped with the repo into DATA_DIR on a cold start.
SEED_FROM_BUNDLE = os.environ.get("ACCOUNTING_PRO_SEED_FROM_BUNDLE", "1") != "0"

DB_FILENAME = "accounting_pro.db"


def prepare_data_dir():
    """Make sure the writable data directory exists and holds a database.

    On a normal install this is a no-op: the database already sits next to the
    code.  In a serverless deployment the bundle is read-only, so the bundled
    demo database is copied into the temp directory once per instance.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    target = os.path.join(DATA_DIR, DB_FILENAME)
    if os.path.exists(target):
        return target
    bundled = os.path.join(BASE_DIR, DB_FILENAME)
    if SEED_FROM_BUNDLE and os.path.exists(bundled):
        shutil.copyfile(bundled, target)
    return target


# ---------------------------------------------------------------------------
# Hosted libSQL / Turso support (optional, env-driven)
# ---------------------------------------------------------------------------
# When DATABASE_URL points at a hosted libSQL database (Turso) the driver is
# ``sqlalchemy-libsql``; SQLAlchemy needs the URL rewritten to
# ``sqlite+libsql://...`` and the auth token passed as a connect argument.
# Nothing changes for the normal local/VM SQLite install.
RAW_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite:///" + os.path.join(DATA_DIR, DB_FILENAME),
).strip()

TURSO_AUTH_TOKEN = (os.environ.get("TURSO_AUTH_TOKEN")
                    or os.environ.get("DATABASE_AUTH_TOKEN")
                    or "")


def normalize_db_url(url):
    """Map hosted libSQL URLs onto the SQLAlchemy libsql dialect.

    Turso endpoints answer plain ``libsql://`` connections with a 308 redirect to
    HTTPS, so ``secure=true`` is added for any remote (hosted) endpoint.  Local
    file paths (``libsql:///path/to.db``) are left untouched.
    """
    url = url.strip()
    if url.startswith("libsql://"):
        rest = url[len("libsql://"):]
        if rest.startswith("/") or rest.startswith("file:"):
            return "sqlite+libsql://" + rest
        url = "sqlite+libsql://" + rest
    elif url.startswith(("https://", "http://")) and ".turso." in url:
        url = "sqlite+libsql://" + url.split("://", 1)[1]
    else:
        return url
    if "secure=" not in url:
        url += ("&" if "?" in url else "?") + "secure=true"
    return url


DATABASE_URI = normalize_db_url(RAW_DATABASE_URL)
IS_REMOTE_DB = DATABASE_URI.startswith("sqlite+libsql://")
ENGINE_OPTIONS = ({"connect_args": {"auth_token": TURSO_AUTH_TOKEN}}
                  if IS_REMOTE_DB and TURSO_AUTH_TOKEN else {})


class Config:
    """Base configuration shared by every environment."""

    # --- Administrator panel -------------------------------------------
    # Separate sign-in for /admin. Change these (or set the environment
    # variables) before putting the system in front of real users.
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@accountingpro.local")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@12345")
    ADMIN_NAME = os.environ.get("ADMIN_NAME", "System Administrator")

    # --- Security -------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY", "accounting-pro-dev-secret-change-me")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 hours

    # --- Database -------------------------------------------------------
    SQLALCHEMY_DATABASE_URI = DATABASE_URI
    SQLALCHEMY_ENGINE_OPTIONS = ENGINE_OPTIONS
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Uploads --------------------------------------------------------
    UPLOAD_FOLDER = os.environ.get("ACCOUNTING_PRO_UPLOAD_DIR",
                                   os.path.join(DATA_DIR, "uploads"))
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB per request
    ALLOWED_UPLOAD_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "pdf",
                                 "doc", "docx", "xls", "xlsx", "csv"}
    ALLOWED_DATA_EXTENSIONS = {"xlsx", "xls", "csv"}

    # --- Business defaults ---------------------------------------------
    DEFAULT_CURRENCY = "PKR"
    CURRENCY_SYMBOL = "Rs."
    # Default accounting period requested for this project
    DEFAULT_PERIOD_START = "2026-01-01"
    DEFAULT_PERIOD_END = "2026-03-31"
    DECIMAL_PLACES = Decimal("0.01")

    # --- Reports --------------------------------------------------------
    REPORT_FOOTER = "Generated by ACCOUNTING PRO - Computerized Accounting System"


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    SERVER_NAME = "localhost"


def ensure_dirs():
    """Create runtime directories if they do not exist."""
    for path in (Config.UPLOAD_FOLDER,
                 os.path.join(Config.UPLOAD_FOLDER, "logos"),
                 os.path.join(Config.UPLOAD_FOLDER, "attachments"),
                 os.path.join(Config.UPLOAD_FOLDER, "imports"),
                 os.path.join(Config.UPLOAD_FOLDER, "backups")):
        os.makedirs(path, exist_ok=True)
