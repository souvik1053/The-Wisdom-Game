"""One-time migration from the old wisdom_game.db SQLite database to MySQL.

Usage:
    pip install mysql-connector-python
    python scripts/migrate_sqlite_to_mysql.py

Configure MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD and
MYSQL_DATABASE in the environment before running.
"""

import os
import sqlite3
from datetime import date
import mysql.connector

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQLITE_DB = os.path.join(BASE_DIR, "wisdom_game.db")


def mysql_config():
    return {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DATABASE", "trader_quest"),
    }


def normalize_date(value):
    if not value:
        return None
    return str(value)[:10]


def main():
    if not os.path.exists(SQLITE_DB):
        raise FileNotFoundError(f"SQLite database not found: {SQLITE_DB}")

    sqlite_conn = sqlite3.connect(SQLITE_DB)
    mysql_conn = mysql.connector.connect(**mysql_config())
    sqlite_cur = sqlite_conn.cursor()
    mysql_cur = mysql_conn.cursor()

    try:
        mysql_conn.start_transaction()

        # Preserve IDs for books and reading logs so existing references remain stable.
        books = sqlite_cur.execute(
            "SELECT book_id,title,author,category,total_pages,start_date,status,finish_date,pages_read FROM books"
        ).fetchall()
        mysql_cur.executemany(
            """INSERT INTO books
            (book_id,title,author,category,total_pages,start_date,status,finish_date,pages_read)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              title=VALUES(title), author=VALUES(author), category=VALUES(category),
              total_pages=VALUES(total_pages), start_date=VALUES(start_date),
              status=VALUES(status), finish_date=VALUES(finish_date), pages_read=VALUES(pages_read)""",
            [(*r[:4], r[4], normalize_date(r[5]), r[6], normalize_date(r[7]), r[8]) for r in books],
        )

        logs = sqlite_cur.execute(
            "SELECT id,date,book,category,pages_from,pages_read,session_type,notes FROM reading_log"
        ).fetchall()
        for r in logs:
            mysql_cur.execute(
                """INSERT INTO reading_log
                (id,date,book,category,pages_from,pages_read,session_type,notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  date=VALUES(date), book=VALUES(book), category=VALUES(category),
                  pages_from=VALUES(pages_from), pages_read=VALUES(pages_read),
                  session_type=VALUES(session_type), notes=VALUES(notes)""",
                (r[0], normalize_date(r[1]), r[2], r[3], r[4], r[5], r[6], r[7]),
            )

        goals = sqlite_cur.execute(
            "SELECT target_month,books_goal,pages_goal,xp_goal FROM monthly_goals"
        ).fetchall()
        mysql_cur.executemany(
            """INSERT INTO monthly_goals(target_month,books_goal,pages_goal,xp_goal)
            VALUES (%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              books_goal=VALUES(books_goal), pages_goal=VALUES(pages_goal), xp_goal=VALUES(xp_goal)""",
            [(normalize_date(r[0]), r[1], r[2], r[3]) for r in goals],
        )

        levels = sqlite_cur.execute("SELECT level,xp,title FROM levels").fetchall()
        mysql_cur.executemany(
            """INSERT INTO levels(level,xp,title) VALUES (%s,%s,%s)
            ON DUPLICATE KEY UPDATE xp=VALUES(xp), title=VALUES(title)""",
            levels,
        )

        rules = sqlite_cur.execute("SELECT id,action,xp FROM xp_rules").fetchall()
        mysql_cur.executemany(
            """INSERT INTO xp_rules(id,action,xp) VALUES (%s,%s,%s)
            ON DUPLICATE KEY UPDATE action=VALUES(action), xp=VALUES(xp)""",
            rules,
        )

        settings = sqlite_cur.execute("SELECT `key`,value FROM settings").fetchall()
        mysql_cur.executemany(
            """INSERT INTO settings(`key`,`value`) VALUES (%s,%s)
            ON DUPLICATE KEY UPDATE `value`=VALUES(`value`)""",
            settings,
        )

        mysql_conn.commit()
        print("Migration completed successfully.")
        print(f"Books: {len(books)}")
        print(f"Reading logs: {len(logs)}")
        print(f"Monthly goals: {len(goals)}")
        print(f"Levels: {len(levels)}")
        print(f"XP rules: {len(rules)}")
        print(f"Settings: {len(settings)}")
    except Exception:
        mysql_conn.rollback()
        raise
    finally:
        mysql_cur.close()
        sqlite_cur.close()
        mysql_conn.close()
        sqlite_conn.close()


if __name__ == "__main__":
    main()
