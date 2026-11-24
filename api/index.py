"""
Vercel serverless entry point for ACCOUNTING PRO.

Vercel's Python runtime imports this module and looks for a WSGI callable named
``app``.  The real application lives one directory up (project root), so the root
is put on ``sys.path`` first; ``vercel.json`` ships the templates, static files,
python packages and the demo database alongside this function.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Lambda/Vercel: the bundle is read-only, config.py redirects the database and the
# upload folders into /tmp and copies the bundled demo database there on boot.
os.environ.setdefault("VERCEL", "1")

from app import app  # noqa: E402  (Flask application object, WSGI callable)

# Vercel also accepts a "handler" attribute; keep it for compatibility.
handler = app
