import sqlite3, os, json

DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")

_db_dir = os.path.dirname(DB_PATH)
if _db_dir and not os.path.exists(_db_dir):
    try:
        os.makedirs(_db_dir, exist_ok=True)
    except Exception:
        DB_PATH = "bot.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn(); c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id           INTEGER PRIMARY KEY,
        username          TEXT DEFAULT '',
        full_name         TEXT DEFAULT '',
        balance           INTEGER DEFAULT 0,
        total_topup       INTEGER DEFAULT 0,
        referral_code     TEXT UNIQUE,
        referred_by       INTEGER DEFAULT NULL,
        referral_count    INTEGER DEFAULT 0,
        referral_rewarded INTEGER DEFAULT 0,
        test_used         INTEGER DEFAULT 0,
        joined_at         TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS payments (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_code    TEXT UNIQUE,
        user_id         INTEGER,
        amount          INTEGER,
        purpose         TEXT,
        plan_key        TEXT DEFAULT '',
        plan_name       TEXT DEFAULT '',
        status          TEXT DEFAULT 'pending',
        receipt_file_id TEXT DEFAULT '',
        is_photo        INTEGER DEFAULT 1,
        config_sent     TEXT DEFAULT '',
        pay_method      TEXT DEFAULT 'card',
        crypto_coin     TEXT DEFAULT '',
        created_at      TEXT DEFAULT (datetime('now')),
        confirmed_at    TEXT DEFAULT ''
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER,
        payment_id   INTEGER,
        plan_key     TEXT,
        plan_name    TEXT,
        plan_size    TEXT,
        price        INTEGER,
        config_sent  TEXT DEFAULT '',
        created_at   TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS admins (
        user_id  INTEGER PRIMARY KEY,
        added_at TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT DEFAULT ''
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS configs (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_key TEXT NOT NULL,
        config   TEXT NOT NULL,
        is_used  INTEGER DEFAULT 0,
        used_by  INTEGER DEFAULT NULL,
        used_at  TEXT DEFAULT ''
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS user_state (
        user_id INTEGER PRIMARY KEY,
        state   TEXT DEFAULT '{}'
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS discount_codes (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        code       TEXT UNIQUE NOT NULL,
        percent    INTEGER NOT NULL,
        note       TEXT DEFAULT '',
        is_active  INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')),
        expires_at TEXT DEFAULT ''
    )""")

    # مهاجرت: اضافه کردن ستون‌های جدید در صورت نبودن
    migrations = [
        "ALTER TABLE users ADD COLUMN test_used INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN total_topup INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0",
        "ALTER TABLE payments ADD COLUMN pay_method TEXT DEFAULT 'card'",
        "ALTER TABLE payments ADD COLUMN crypto_coin TEXT DEFAULT ''",
    ]
    for sql in migrations:
        try:
            c.execute(sql)
            conn.commit()
        except Exception:
            pass

    conn.commit(); conn.close()

# ── Settings ──────────────────────────────────────────────

def get_setting(key, default=None):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone(); conn.close()
    return row["value"] if row else default

def set_setting(key, value):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))
    conn.commit(); conn.close()

# ── State ─────────────────────────────────────────────────

def save_state(user_id: int, state: dict):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO user_state(user_id,state) VALUES(?,?)",
              (user_id, json.dumps(state, ensure_ascii=False)))
    conn.commit(); conn.close()

def load_state(user_id: int) -> dict:
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT state FROM user_state WHERE user_id=?", (user_id,))
    row = c.fetchone(); conn.close()
    if row:
        try:
            return json.loads(row["state"]) or {}
        except Exception:
            return {}
    return {}

def clear_state(user_id: int):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO user_state(user_id,state) VALUES(?,?)", (user_id, "{}"))
    conn.commit(); conn.close()

# ── Users ─────────────────────────────────────────────────

def get_or_create_user(user_id, username, full_name, referred_by=None):
    import random, string
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    is_new = False
    if not row:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        c.execute("INSERT INTO users(user_id,username,full_name,referral_code,referred_by) VALUES(?,?,?,?,?)",
                  (user_id, username or '', full_name or '', code, referred_by))
        conn.commit()
        is_new = True
        if referred_by:
            c.execute("UPDATE users SET referral_count=referral_count+1 WHERE user_id=?", (referred_by,))
            conn.commit()
        c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = c.fetchone()
    else:
        c.execute("UPDATE users SET username=?,full_name=? WHERE user_id=?", (username or '', full_name or '', user_id))
        conn.commit()
    result = dict(row); conn.close()
    result["_is_new"] = is_new
    return result

def get_user(user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone(); conn.close()
    return dict(row) if row else None

def get_user_by_referral(code):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE referral_code=?", (code,))
    row = c.fetchone(); conn.close()
    return dict(row) if row else None

def update_balance(user_id, delta):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (delta, user_id))
    conn.commit(); conn.close()

def set_balance(user_id, amount):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE users SET balance=? WHERE user_id=?", (amount, user_id))
    conn.commit(); conn.close()

def add_topup(user_id, amount):
    """مجموع شارژ واریزی کاربر رو افزایش بده (برای محاسبه VIP)"""
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE users SET total_topup=total_topup+? WHERE user_id=?", (amount, user_id))
    conn.commit(); conn.close()

def mark_test_used(user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE users SET test_used=1 WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()

def mark_referral_rewarded(user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE users SET referral_rewarded=1 WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()

def get_all_users():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM users ORDER BY joined_at DESC")
    rows = [dict(r) for r in c.fetchall()]; conn.close()
    return rows

def get_all_user_ids():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = [r["user_id"] for r in c.fetchall()]; conn.close()
    return rows

# ── Payments ──────────────────────────────────────────────

def _make_invoice_code():
    import random
    return f"INV{random.randint(100000,999999)}"

def create_payment(user_id, amount, purpose, plan_key='', plan_name='', pay_method='card', crypto_coin=''):
    conn = get_conn(); c = conn.cursor()
    code = _make_invoice_code()
    while True:
        c.execute("SELECT id FROM payments WHERE invoice_code=?", (code,))
        if not c.fetchone():
            break
        code = _make_invoice_code()
    c.execute("""INSERT INTO payments(invoice_code,user_id,amount,purpose,plan_key,plan_name,pay_method,crypto_coin)
                 VALUES(?,?,?,?,?,?,?,?)""", (code, user_id, amount, purpose, plan_key, plan_name, pay_method, crypto_coin))
    pay_id = c.lastrowid; conn.commit(); conn.close()
    return pay_id, code

def get_payment(pay_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM payments WHERE id=?", (pay_id,))
    row = c.fetchone(); conn.close()
    return dict(row) if row else None

def set_receipt(pay_id, file_id, is_photo):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE payments SET receipt_file_id=?,is_photo=?,status='waiting' WHERE id=?",
              (file_id, 1 if is_photo else 0, pay_id))
    conn.commit(); conn.close()

def confirm_payment(pay_id, config_sent=''):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE payments SET status='confirmed',confirmed_at=datetime('now'),config_sent=? WHERE id=?",
              (config_sent, pay_id))
    conn.commit(); conn.close()

def cancel_payment(pay_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE payments SET status='cancelled' WHERE id=?", (pay_id,))
    conn.commit(); conn.close()

def get_pending_payments():
    conn = get_conn(); c = conn.cursor()
    c.execute("""SELECT p.*, u.username, u.full_name
                 FROM payments p JOIN users u ON p.user_id=u.user_id
                 WHERE p.status='waiting' ORDER BY p.created_at DESC""")
    rows = [dict(r) for r in c.fetchall()]; conn.close()
    return rows

# ── Subscriptions ─────────────────────────────────────────

def create_subscription(user_id, payment_id, plan_key, plan_name, plan_size, price, config_sent=''):
    conn = get_conn(); c = conn.cursor()
    c.execute("""INSERT INTO subscriptions(user_id,payment_id,plan_key,plan_name,plan_size,price,config_sent)
                 VALUES(?,?,?,?,?,?,?)""", (user_id, payment_id, plan_key, plan_name, plan_size, price, config_sent))
    conn.commit(); conn.close()

def get_user_subscriptions(user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM subscriptions WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    rows = [dict(r) for r in c.fetchall()]; conn.close()
    return rows

# ── Admins ────────────────────────────────────────────────

def add_admin(user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (user_id,))
    conn.commit(); conn.close()

def remove_admin(user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()

def get_admin_ids():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT user_id FROM admins")
    rows = [r["user_id"] for r in c.fetchall()]; conn.close()
    return rows

# ── Configs ───────────────────────────────────────────────

def add_configs(plan_key, configs: list):
    conn = get_conn(); c = conn.cursor()
    for cfg in configs:
        if cfg.strip():
            c.execute("INSERT INTO configs(plan_key,config) VALUES(?,?)", (plan_key, cfg.strip()))
    conn.commit(); conn.close()

def get_config_count(plan_key=None):
    conn = get_conn(); c = conn.cursor()
    if plan_key:
        c.execute("SELECT COUNT(*) as n FROM configs WHERE plan_key=? AND is_used=0", (plan_key,))
    else:
        c.execute("SELECT COUNT(*) as n FROM configs WHERE is_used=0")
    row = c.fetchone(); conn.close()
    return row["n"] if row else 0

def assign_config(plan_key, user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM configs WHERE plan_key=? AND is_used=0 ORDER BY id LIMIT 1", (plan_key,))
    row = c.fetchone()
    if not row:
        conn.close(); return None
    c.execute("UPDATE configs SET is_used=1,used_by=?,used_at=datetime('now') WHERE id=?", (user_id, row["id"]))
    conn.commit(); conn.close()
    return row["config"]

def assign_configs_bulk(plan_key, user_id, count):
    """چند کانفیگ یکجا اختصاص بده (برای خرید بسته‌ای)"""
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM configs WHERE plan_key=? AND is_used=0 ORDER BY id LIMIT ?", (plan_key, count))
    rows = c.fetchall()
    if len(rows) < count:
        conn.close(); return None  # کافی نیست
    cfgs = []
    for row in rows:
        c.execute("UPDATE configs SET is_used=1,used_by=?,used_at=datetime('now') WHERE id=?", (user_id, row["id"]))
        cfgs.append(row["config"])
    conn.commit(); conn.close()
    return cfgs

# ── Ban ───────────────────────────────────────────────────

def ban_user(user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()

def unban_user(user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()

def is_banned(user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone(); conn.close()
    return bool(row and row["is_banned"])

# ── Discount codes ────────────────────────────────────────

def create_discount_code(code, percent, note=''):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO discount_codes(code,percent,note,is_active) VALUES(?,?,?,1)",
              (code.upper(), percent, note))
    conn.commit(); conn.close()

def get_discount_code(code):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM discount_codes WHERE code=? AND is_active=1", (code.upper(),))
    row = c.fetchone(); conn.close()
    return dict(row) if row else None

def deactivate_discount_code(code):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE discount_codes SET is_active=0 WHERE code=?", (code.upper(),))
    conn.commit(); conn.close()

def get_all_discount_codes():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM discount_codes ORDER BY created_at DESC")
    rows = [dict(r) for r in c.fetchall()]; conn.close()
    return rows

def deactivate_all_discount_codes():
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE discount_codes SET is_active=0")
    conn.commit(); conn.close()

def get_configs_summary():
    conn = get_conn(); c = conn.cursor()
    c.execute("""SELECT plan_key,
                 SUM(CASE WHEN is_used=0 THEN 1 ELSE 0 END) as available,
                 COUNT(*) as total
                 FROM configs GROUP BY plan_key""")
    rows = [dict(r) for r in c.fetchall()]; conn.close()
    return rows
