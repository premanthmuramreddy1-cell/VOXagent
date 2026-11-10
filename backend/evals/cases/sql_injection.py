import sqlite3

DB_PASSWORD = "hunter2-prod-password"


def find_user(conn: sqlite3.Connection, name: str):
    query = f"SELECT * FROM users WHERE name = '{name}'"
    return conn.execute(query).fetchall()
