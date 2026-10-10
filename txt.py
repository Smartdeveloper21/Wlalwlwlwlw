# language: Python, file: bot.py, target: Python 3.10+ (works on 3.13)
# Snipy Store Bot — Single File — Python 3.13 compatible + all features

import sys, subprocess, importlib, asyncio, time, urllib.request, email.utils, os

# --- clock fix (server offset) ---
def _sync_clock():
    try:
        req = urllib.request.Request("https://www.google.com", method="HEAD")
        with urllib.request.urlopen(req, timeout=8) as r:
            d = r.headers.get("Date")
        if not d: return 0.0
        return time.time() - email.utils.parsedate_to_datetime(d).timestamp()
    except Exception as e:
        print(f"[clock] fail: {e}"); return 0.0

_OFF = _sync_clock()
print(f"[clock] device offset: {_OFF:+.2f}s")
_rt = time.time
time.time = lambda: _rt() - _OFF

# --- asyncio loop fix (py3.12+) ---
try:
    asyncio.get_running_loop()
except RuntimeError:
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass

# --- auto install deps ---
REQUIRED = [
    ("pyrogram", "kurigram"),
    ("apscheduler", "apscheduler"),
    ("qrcode", "qrcode[pil]"),
    ("PIL", "pillow"),
]
def _imp(m):
    try: importlib.import_module(m); return True
    except ImportError: return False

def _boot():
    miss = [p for m, p in REQUIRED if not _imp(m)]
    if not miss: return
    print(f"[bootstrap] installing: {', '.join(miss)}")
    for p in miss:
        try: subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", p])
        except subprocess.CalledProcessError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--user", p])
    for m, _ in REQUIRED:
        if m in sys.modules: del sys.modules[m]
    print("[bootstrap] deps ready")

_boot()

# Optional speedups — non-fatal
try:
    importlib.import_module("tgcrypto")
except ImportError:
    try:
        importlib.import_module("pycryptodome")
    except ImportError:
        print("[boot] tgcrypto/pycryptodome missing — will run in slower mode (safe)")


import re, io, json, sqlite3, logging, shutil, urllib.parse, secrets
from datetime import datetime, timezone, timedelta
from contextlib import closing
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from pyrogram.errors import (
    SessionRevoked, AuthKeyUnregistered, PhoneCodeInvalid, PhoneCodeExpired,
    SessionPasswordNeeded, PhoneNumberInvalid, PhoneNumberBanned, FloodWait,
    UserNotParticipant
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import qrcode

HTML = enums.ParseMode.HTML


# ============================================================
#  CONFIG
# ============================================================
API_ID    = 39737479
API_HASH  = "0a5d5cee01fe1f2622ada43137e21454"
BOT_TOKEN = "8647748004:AAFwN7zl5Nu_RprlKbVOGMKMHA5-8O4taSc"
OWNER_ID  = 8573155257

ADMIN_USERNAME = "SnipyFlex"
BOT_USERNAME   = "SnipyOtp_iBot"
LOG_CHANNEL_ID = -1004450233216

DEFAULT_USDT_RATE   = 100.0
DEFAULT_UPI_ID      = "snixl"
DEFAULT_UPI_NAME    = "Snipy Store"
DEFAULT_SUPPORT     = "https://t.me/SnipyFsub"
DEFAULT_MIN_DEP     = 40
REFERRAL_REWARD_INR = 30

DB_PATH      = "seller.db"
BACKUP_DIR   = "backups"
BACKUP_HOURS = 24

DISCOUNT_TIERS = [(2, 0), (5, 20), (10, 30)]

WELCOME_MSG = (
    "👋 Hey <b>{name}</b>! Welcome to <b>Fresh Tg Store</b> 🏪\n\n"
    "<blockquote>"
    "📱 Fresh TG Accounts  |  📁 Sessions\n"
    "⚡ Instant Delivery    |  🔒 Verified Stock"
    "</blockquote>\n"
    "<blockquote>"
    "Best Prices  •  24/7 Support  •  Trusted Seller"
    "</blockquote>\n\n"
    "👇 <b>Use the menu to get started!</b>"
)

SUPPORT_DESCRIPTION = (
    "📞 <b>SUPPORT CENTER</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Welcome to <b>Fresh Tg Store Support</b>!\n\n"
    "Kisi bhi problem ke liye directly admin se contact karo:\n\n"
    "✅ Payment issue\n"
    "✅ OTP na aaye\n"
    "✅ Account login fail\n"
    "✅ Deposit problem\n"
    "✅ Koi bhi query\n\n"
    "⏱ <b>Response:</b> 5-30 minutes\n"
    "🕒 <b>Available:</b> 24/7"
)

DEPOSIT_DESCRIPTION = (
    "💳 <b>Select Payment Method:</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🟢 <b>Automatic</b> — instant credit, direct QR scan\n"
    "⏳ <b>Manual</b> — screenshot bhejo, admin 5-30 min me approve karega\n\n"
    "⬇️ <b>Neeche se method choose karo</b>"
)

os.makedirs(BACKUP_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()]
)
log = logging.getLogger("seller")


# ============================================================
#  UTILS
# ============================================================
def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def gen_ref_id(uid):
    return f"dep-{uid}-{int(time.time() * 1000)}"

def mask_phone(p):
    if not p or len(p) < 6: return p or "—"
    return p[:-4] + "••••" + p[-4:]

def mask_otp(c):
    if not c or len(c) < 4: return c or "—"
    return c[0] + "•••" + c[-1]

def gen_order_num(oid):
    return f"ORD{21804 + oid}"


# ============================================================
#  DB
# ============================================================
def db():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    with closing(db()) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            api_id TEXT NOT NULL, api_hash TEXT NOT NULL,
            session TEXT NOT NULL, twofa TEXT DEFAULT '',
            country_code TEXT DEFAULT '', country_name TEXT NOT NULL DEFAULT 'Unknown',
            flag TEXT DEFAULT '', year INTEGER DEFAULT 2026,
            price INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'available',
            health TEXT DEFAULT 'unknown',
            sold_to INTEGER, sold_at TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, username TEXT,
            acc_id INTEGER, country TEXT, price INTEGER,
            discount_pct INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'awaiting_payment',
            payment_file TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY, name TEXT,
            perms TEXT NOT NULL DEFAULT 'all',
            added_by INTEGER, added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, first_name TEXT DEFAULT '', username TEXT DEFAULT '',
            balance INTEGER NOT NULL DEFAULT 0,
            banned INTEGER NOT NULL DEFAULT 0,
            referrer INTEGER DEFAULT 0, ref_code TEXT UNIQUE,
            total_spent INTEGER NOT NULL DEFAULT 0,
            total_deposited INTEGER NOT NULL DEFAULT 0,
            referral_earned INTEGER NOT NULL DEFAULT 0,
            joined_at TEXT NOT NULL, last_seen TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ref_id TEXT UNIQUE,
            user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
            method TEXT DEFAULT 'upi', method_name TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            proof_file TEXT, approved_by INTEGER,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payment_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, emoji TEXT DEFAULT '💳',
            type TEXT NOT NULL DEFAULT 'upi',
            address TEXT NOT NULL, note TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS otp_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER, acc_id INTEGER, user_id INTEGER,
            code TEXT, raw_text TEXT, received_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, status TEXT DEFAULT 'open',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ticket_msgs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL, sender TEXT, text TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS banners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot TEXT, file_id TEXT, added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL UNIQUE,
            valid INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            validated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS force_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            title TEXT DEFAULT '',
            invite_link TEXT DEFAULT '',
            is_private INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rank INTEGER NOT NULL,
            prize TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS giveaway_winners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL,
            rank INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            prize TEXT DEFAULT '',
            valid_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );
        """)
        c.commit()
        defaults = {
            "owner_id": str(OWNER_ID),
            "upi_id": DEFAULT_UPI_ID,
            "upi_name": DEFAULT_UPI_NAME,
            "support_url": DEFAULT_SUPPORT,
            "admin_username": ADMIN_USERNAME,
            "support_desc": SUPPORT_DESCRIPTION,
            "deposit_desc": DEPOSIT_DESCRIPTION,
            "min_deposit": str(DEFAULT_MIN_DEP),
            "usdt_rate": str(DEFAULT_USDT_RATE),
            "log_channel": str(LOG_CHANNEL_ID),
            "welcome_msg": WELCOME_MSG,
            "referral_on": "1",
            "referral_reward": str(REFERRAL_REWARD_INR),
            "images_on": "1",
            "stock_layout": "one",
            "bot_status": "on",
        }
        for k, v in defaults.items():
            if not c.execute("SELECT 1 FROM settings WHERE key=?", (k,)).fetchone():
                c.execute("INSERT INTO settings(key,value) VALUES(?,?)", (k, v))
        if not c.execute("SELECT 1 FROM payment_methods").fetchone():
            c.execute("""INSERT INTO payment_methods(name,emoji,type,address,note,sort_order,created_at)
                         VALUES(?,?,?,?,?,?,?)""",
                      ("Auto Payment (instant)", "🤖", "upi", DEFAULT_UPI_ID,
                       "PhonePe / GPay / Paytm / any UPI", 1, now()))
        c.commit()

# ---- settings ----
def get_setting(k, d=None):
    with closing(db()) as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r["value"] if r else d

def set_setting(k, v):
    with closing(db()) as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
        c.commit()

def owner_id():
    v = get_setting("owner_id", "0")
    return int(v) if v else 0

def log_channel_id():
    v = get_setting("log_channel", "0")
    try: return int(v) if v and int(v) else 0
    except: return 0

def referral_reward():
    v = get_setting("referral_reward", str(REFERRAL_REWARD_INR))
    try: return int(v)
    except: return REFERRAL_REWARD_INR

# ---- payment methods ----
def list_payment_methods(only_enabled=True):
    with closing(db()) as c:
        q = "SELECT * FROM payment_methods"
        if only_enabled: q += " WHERE enabled=1"
        q += " ORDER BY sort_order, id"
        return [dict(r) for r in c.execute(q).fetchall()]

def get_payment_method(pid):
    with closing(db()) as c:
        r = c.execute("SELECT * FROM payment_methods WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None

def add_payment_method(name, emoji, ptype, address, note=""):
    with closing(db()) as c:
        cur = c.execute("""INSERT INTO payment_methods(name,emoji,type,address,note,created_at)
                           VALUES(?,?,?,?,?,?)""", (name, emoji, ptype, address, note, now()))
        c.commit(); return cur.lastrowid

def del_payment_method(pid):
    with closing(db()) as c:
        c.execute("DELETE FROM payment_methods WHERE id=?", (pid,)); c.commit()

def toggle_payment_method(pid):
    with closing(db()) as c:
        c.execute("UPDATE payment_methods SET enabled=1-enabled WHERE id=?", (pid,)); c.commit()

# ---- accounts ----
def add_account(phone, api_id, api_hash, session, twofa, cc, cname, flag, year, price):
    with closing(db()) as c:
        cur = c.execute("""INSERT INTO accounts(phone,api_id,api_hash,session,twofa,
                           country_code,country_name,flag,year,price,status,health,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,'available','unknown',?)""",
                        (phone, str(api_id), api_hash, session, twofa or "", cc or "",
                         cname or "Unknown", flag or "", year or 2026, int(price), now()))
        c.commit(); return cur.lastrowid

def get_account(aid):
    with closing(db()) as c:
        r = c.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
        return dict(r) if r else None

def list_available():
    with closing(db()) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM accounts WHERE status='available' ORDER BY id").fetchall()]

def stock_groups():
    with closing(db()) as c:
        return [dict(r) for r in c.execute("""
            SELECT country_name, flag, year, price, COUNT(*) cnt
            FROM accounts WHERE status='available'
            GROUP BY country_name, year, price ORDER BY price""").fetchall()]

def account_ids_in_group(country, year, price):
    with closing(db()) as c:
        return [r["id"] for r in c.execute("""SELECT id FROM accounts
            WHERE status='available' AND country_name=? AND year=? AND price=?
            ORDER BY id""", (country, year, price)).fetchall()]

def mark_sold(aid, uid):
    with closing(db()) as c:
        c.execute("UPDATE accounts SET status='sold',sold_to=?,sold_at=? WHERE id=?",
                  (uid, now(), aid)); c.commit()

def set_health(aid, h):
    with closing(db()) as c:
        c.execute("UPDATE accounts SET health=? WHERE id=?", (h, aid)); c.commit()

def delete_account(aid):
    with closing(db()) as c:
        c.execute("DELETE FROM accounts WHERE id=?", (aid,)); c.commit()

def all_accounts():
    with closing(db()) as c:
        return [dict(r) for r in c.execute("SELECT * FROM accounts ORDER BY id").fetchall()]

def user_bought_accounts(uid):
    with closing(db()) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM accounts WHERE sold_to=? ORDER BY sold_at DESC", (uid,)).fetchall()]

# ---- orders ----
def create_order(uid, uname, acc_id, country, price, disc=0):
    with closing(db()) as c:
        cur = c.execute("""INSERT INTO orders(user_id,username,acc_id,country,price,discount_pct,
                           status,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,'awaiting_payment',?,?)""",
                        (uid, uname, acc_id, country, price, disc, now(), now()))
        c.commit(); return cur.lastrowid

def get_order(oid):
    with closing(db()) as c:
        r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        return dict(r) if r else None

def update_order(oid, **f):
    if not f: return
    f["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in f)
    with closing(db()) as c:
        c.execute(f"UPDATE orders SET {cols} WHERE id=?", (*f.values(), oid)); c.commit()

def user_orders(uid, limit=20):
    with closing(db()) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (uid, limit)).fetchall()]

def user_orders_count(uid):
    with closing(db()) as c:
        r = c.execute("SELECT COUNT(*) n FROM orders WHERE user_id=? AND status='approved'",
                      (uid,)).fetchone()
    return r["n"]

def pending_orders():
    with closing(db()) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM orders WHERE status='pending_approval' ORDER BY id").fetchall()]

# ---- admins ----
def is_admin(uid):
    if owner_id() == uid: return True
    with closing(db()) as c:
        return c.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,)).fetchone() is not None

def has_perm(uid, perm):
    if owner_id() == uid: return True
    with closing(db()) as c:
        r = c.execute("SELECT perms FROM admins WHERE user_id=?", (uid,)).fetchone()
    if not r: return False
    return r["perms"] == "all" or perm in r["perms"].split(",")

def add_admin(uid, name, perms, by):
    with closing(db()) as c:
        c.execute("""INSERT INTO admins(user_id,name,perms,added_by,added_at) VALUES(?,?,?,?,?)
                     ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,perms=excluded.perms""",
                  (uid, name, perms, by, now())); c.commit()

def del_admin(uid):
    with closing(db()) as c:
        c.execute("DELETE FROM admins WHERE user_id=?", (uid,)); c.commit()

def list_admins():
    with closing(db()) as c:
        return [dict(r) for r in c.execute("SELECT * FROM admins").fetchall()]

# ---- users ----
def upsert_user(u, referrer=None):
    with closing(db()) as c:
        exists = c.execute("SELECT 1 FROM users WHERE user_id=?", (u.id,)).fetchone()
        if not exists:
            rc = secrets.token_hex(4)
            c.execute("""INSERT INTO users(user_id,first_name,username,joined_at,last_seen,ref_code,referrer)
                         VALUES(?,?,?,?,?,?,?)""",
                      (u.id, u.first_name or "", u.username or "", now(), now(), rc, referrer or 0))
            if referrer and referrer != u.id:
                try:
                    c.execute("""INSERT OR IGNORE INTO referrals(referrer_id,referred_id,valid,created_at)
                                 VALUES(?,?,0,?)""", (referrer, u.id, now()))
                except Exception: pass
        else:
            c.execute("UPDATE users SET first_name=?,username=?,last_seen=? WHERE user_id=?",
                      (u.first_name or "", u.username or "", now(), u.id))
        c.commit()

def get_user(uid):
    with closing(db()) as c:
        r = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        return dict(r) if r else None

def get_user_by_ref(code):
    with closing(db()) as c:
        r = c.execute("SELECT * FROM users WHERE ref_code=?