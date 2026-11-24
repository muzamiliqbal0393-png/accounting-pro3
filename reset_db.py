"""
Reset the database.

    python reset_db.py            -> delete and recreate an empty database
    python reset_db.py --demo     -> recreate and load the demo data

A timestamped copy of the existing database is saved in uploads/backups/ first.
"""
import os
import shutil
import sys
from datetime import datetime

from config import Config
from app import create_app
from extensions import db


def main():
    db_path = Config.SQLALCHEMY_DATABASE_URI.replace("sqlite:///", "")
    backup_dir = os.path.join(Config.UPLOAD_FOLDER, "backups")
    os.makedirs(backup_dir, exist_ok=True)

    if os.path.exists(db_path):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = os.path.join(backup_dir, f"pre_reset_{stamp}.db")
        shutil.copy2(db_path, target)
        print(f"Safety copy saved to {target}")
        os.remove(db_path)
        print("Existing database deleted.")

    app = create_app()
    with app.app_context():
        db.create_all()
    print("Empty database created.")

    if "--demo" in sys.argv:
        import seed_demo
        seed_demo.build()


if __name__ == "__main__":
    main()
