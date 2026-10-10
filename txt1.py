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
DEFAULT_UPI_ID      = "snipyowner@axl"
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
        r = c.execute("SELECT * FROM users WHERE ref_code=?", (code,)).fetchone()
        return dict(r) if r else None

def add_balance(uid, amt):
    with closing(db()) as c:
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amt, uid)); c.commit()

def deduct_balance(uid, amt):
    with closing(db()) as c:
        c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amt, uid)); c.commit()

def add_spent(uid, amt):
    with closing(db()) as c:
        c.execute("UPDATE users SET total_spent=total_spent+? WHERE user_id=?", (amt, uid)); c.commit()

def add_deposited(uid, amt):
    with closing(db()) as c:
        c.execute("UPDATE users SET total_deposited=total_deposited+? WHERE user_id=?",
                  (amt, uid)); c.commit()

def add_referral_earned(uid, amt):
    with closing(db()) as c:
        c.execute("UPDATE users SET referral_earned=referral_earned+? WHERE user_id=?",
                  (amt, uid)); c.commit()

def ban_user(uid, on=True):
    with closing(db()) as c:
        c.execute("UPDATE users SET banned=? WHERE user_id=?", (1 if on else 0, uid)); c.commit()

def all_users():
    with closing(db()) as c:
        return [dict(r) for r in c.execute("SELECT user_id FROM users").fetchall()]

def search_users(q):
    with closing(db()) as c:
        return [dict(r) for r in c.execute("""
            SELECT * FROM users WHERE CAST(user_id AS TEXT) LIKE ?
            OR username LIKE ? OR first_name LIKE ? LIMIT 20""",
            (f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()]

def top_buyers(limit=10):
    with closing(db()) as c:
        return [dict(r) for r in c.execute("""
            SELECT user_id, first_name, username, total_spent
            FROM users WHERE total_spent > 0
            ORDER BY total_spent DESC LIMIT ?""", (limit,)).fetchall()]

# ---- deposits ----
def create_deposit(uid, amt, method, method_name="", ref_id=None):
    ref = ref_id or gen_ref_id(uid)
    with closing(db()) as c:
        cur = c.execute("""INSERT INTO deposits(ref_id,user_id,amount,method,method_name,status,created_at,updated_at)
                           VALUES(?,?,?,?,?,'pending',?,?)""",
                        (ref, uid, amt, method, method_name, now(), now()))
        c.commit(); return cur.lastrowid, ref

def get_deposit(did):
    with closing(db()) as c:
        r = c.execute("SELECT * FROM deposits WHERE id=?", (did,)).fetchone()
        return dict(r) if r else None

def update_deposit(did, **f):
    if not f: return
    f["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in f)
    with closing(db()) as c:
        c.execute(f"UPDATE deposits SET {cols} WHERE id=?", (*f.values(), did)); c.commit()

def pending_deposits():
    with closing(db()) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM deposits WHERE status='pending_approval' ORDER BY id").fetchall()]

def log_otp(oid, aid, uid, code, raw):
    with closing(db()) as c:
        c.execute("""INSERT INTO otp_log(order_id,acc_id,user_id,code,raw_text,received_at)
                     VALUES(?,?,?,?,?,?)""", (oid, aid, uid, code, raw, now())); c.commit()

# ---- banners ----
def set_banner(slot, fid):
    with closing(db()) as c:
        c.execute("DELETE FROM banners WHERE slot=?", (slot,))
        c.execute("INSERT INTO banners(slot,file_id,added_at) VALUES(?,?,?)", (slot, fid, now()))
        c.commit()

def get_banner(slot):
    with closing(db()) as c:
        r = c.execute("SELECT file_id FROM banners WHERE slot=?", (slot,)).fetchone()
    return r["file_id"] if r else None

# ---- tickets ----
def create_ticket(uid):
    with closing(db()) as c:
        cur = c.execute("INSERT INTO tickets(user_id,created_at,updated_at) VALUES(?,?,?)",
                        (uid, now(), now())); c.commit(); return cur.lastrowid

def add_ticket_msg(tid, sender, text):
    with closing(db()) as c:
        c.execute("INSERT INTO ticket_msgs(ticket_id,sender,text,created_at) VALUES(?,?,?,?)",
                  (tid, sender, text, now())); c.commit()
        c.execute("UPDATE tickets SET updated_at=? WHERE id=?", (now(), tid)); c.commit()

def open_tickets():
    with closing(db()) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM tickets WHERE status='open' ORDER BY updated_at DESC").fetchall()]

def ticket_owner(tid):
    with closing(db()) as c:
        r = c.execute("SELECT user_id FROM tickets WHERE id=?", (tid,)).fetchone()
    return r["user_id"] if r else None

# ---- referrals ----
def mark_referral_valid(referred_id: int):
    with closing(db()) as c:
        r = c.execute("SELECT * FROM referrals WHERE referred_id=? AND valid=0",
                      (referred_id,)).fetchone()
        if not r: return None
        ref = dict(r)
        c.execute("UPDATE referrals SET valid=1, validated_at=? WHERE id=?",
                  (now(), ref["id"]))
        c.commit()
    reward = referral_reward()
    add_balance(ref["referrer_id"], reward)
    add_referral_earned(ref["referrer_id"], reward)
    return ref["referrer_id"]

def user_referral_stats(uid: int):
    with closing(db()) as c:
        all_valid = c.execute("""SELECT COUNT(*) n FROM referrals
                                 WHERE referrer_id=? AND valid=1""", (uid,)).fetchone()["n"]
        total = c.execute("""SELECT COUNT(*) n FROM referrals WHERE referrer_id=?""",
                          (uid,)).fetchone()["n"]
        week_start = week_start_iso()
        this_week = c.execute("""SELECT COUNT(*) n FROM referrals
                                 WHERE referrer_id=? AND valid=1 AND validated_at>=?""",
                              (uid, week_start)).fetchone()["n"]
        waiting = total - all_valid
        earned = c.execute("""SELECT referral_earned FROM users WHERE user_id=?""",
                           (uid,)).fetchone()
    return {
        "all_time_valid": all_valid,
        "total_invited": total,
        "this_week": this_week,
        "waiting": max(0, waiting),
        "earned": earned["referral_earned"] if earned else 0,
    }

def user_referral_list(uid: int, limit: int = 30):
    with closing(db()) as c:
        rows = c.execute("""
            SELECT r.referred_id, r.valid, r.created_at, r.validated_at,
                   u.username, u.first_name
            FROM referrals r
            LEFT JOIN users u ON u.user_id = r.referred_id
            WHERE r.referrer_id=?
            ORDER BY r.id DESC LIMIT ?""", (uid, limit)).fetchall()
    return [dict(x) for x in rows]

def referral_leaderboard(period: str = "week", limit: int = 10):
    with closing(db()) as c:
        if period == "all":
            rows = c.execute("""
                SELECT r.referrer_id AS uid, COUNT(*) AS valid,
                       u.username, u.first_name
                FROM referrals r
                LEFT JOIN users u ON u.user_id = r.referrer_id
                WHERE r.valid=1
                GROUP BY r.referrer_id
                ORDER BY valid DESC LIMIT ?""", (limit,)).fetchall()
        else:
            since = period_start_iso(period)
            rows = c.execute("""
                SELECT r.referrer_id AS uid, COUNT(*) AS valid,
                       u.username, u.first_name
                FROM referrals r
                LEFT JOIN users u ON u.user_id = r.referrer_id
                WHERE r.valid=1 AND r.validated_at>=?
                GROUP BY r.referrer_id
                ORDER BY valid DESC LIMIT ?""", (since, limit)).fetchall()
    return [dict(x) for x in rows]

def referral_rank(uid: int, period: str = "week"):
    board = referral_leaderboard(period, 10000)
    my_idx = None
    my_valid = 0
    for i, r in enumerate(board):
        if r["uid"] == uid:
            my_idx = i
            my_valid = r["valid"]
            break
    total = len(board)
    if my_idx is None:
        return {"rank": total + 1 if total else 1, "total": total,
                "my_valid": 0, "above_user": None, "above_valid": 0, "diff": 0}
    rank = my_idx + 1
    above = board[my_idx - 1] if my_idx > 0 else None
    above_valid = above["valid"] if above else 0
    diff = above_valid - my_valid if above else 0
    return {"rank": rank, "total": total, "my_valid": my_valid,
            "above_user": above, "above_valid": above_valid, "diff": diff}

def period_start_iso(period: str):
    nowd = datetime.now(timezone.utc)
    if period == "day":
        start = nowd.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = nowd - timedelta(days=nowd.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        start = nowd.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return "1970-01-01T00:00:00"
    return start.isoformat(timespec="seconds")

def week_start_iso():
    return period_start_iso("week")

def period_reset_in(period: str) -> str:
    nowd = datetime.now(timezone.utc)
    if period == "day":
        nxt = (nowd + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        days_until_mon = (7 - nowd.weekday()) % 7
        if days_until_mon == 0: days_until_mon = 7
        nxt = (nowd + timedelta(days=days_until_mon)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        if nowd.month == 12:
            nxt = nowd.replace(year=nowd.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            nxt = nowd.replace(month=nowd.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return "never"
    delta = nxt - nowd
    total_min = int(delta.total_seconds() // 60)
    d = total_min // (24 * 60); h = (total_min % (24 * 60)) // 60; m = total_min % 60
    if d > 0: return f"{d}d {h}h"
    if h > 0: return f"{h}h {m}m"
    return f"{m}m"

# ---- force channels ----
def list_force_channels(only_enabled=True):
    with closing(db()) as c:
        q = "SELECT * FROM force_channels"
        if only_enabled: q += " WHERE enabled=1"
        q += " ORDER BY sort_order, id"
        return [dict(r) for r in c.execute(q).fetchall()]

def add_force_channel(chat_id: str, title: str, invite_link: str, is_private: int = 0):
    with closing(db()) as c:
        cur = c.execute("""INSERT INTO force_channels(chat_id,title,invite_link,is_private,added_at)
                           VALUES(?,?,?,?,?)""", (chat_id, title, invite_link, is_private, now()))
        c.commit(); return cur.lastrowid

def del_force_channel(cid: int):
    with closing(db()) as c:
        c.execute("DELETE FROM force_channels WHERE id=?", (cid,)); c.commit()

def toggle_force_channel(cid: int):
    with closing(db()) as c:
        c.execute("UPDATE force_channels SET enabled=1-enabled WHERE id=?", (cid,)); c.commit()

# ---- giveaways ----
def list_giveaways(only_enabled=True):
    with closing(db()) as c:
        q = "SELECT * FROM giveaways"
        if only_enabled: q += " WHERE enabled=1"
        q += " ORDER BY rank"
        return [dict(r) for r in c.execute(q).fetchall()]

def add_giveaway(rank: int, prize: str):
    with closing(db()) as c:
        cur = c.execute("""INSERT INTO giveaways(rank,prize,enabled,created_at)
                           VALUES(?,?,1,?)""", (rank, prize, now()))
        c.commit(); return cur.lastrowid

def update_giveaway(gid: int, prize: str, enabled: int = 1):
    with closing(db()) as c:
        c.execute("UPDATE giveaways SET prize=?, enabled=? WHERE id=?",
                  (prize, enabled, gid)); c.commit()

def del_giveaway(gid: int):
    with closing(db()) as c:
        c.execute("DELETE FROM giveaways WHERE id=?", (gid,)); c.commit()

def save_winner(week_start, rank, uid, username, prize, valid_count):
    with closing(db()) as c:
        c.execute("""INSERT INTO giveaway_winners(week_start,rank,user_id,username,prize,valid_count,created_at)
                     VALUES(?,?,?,?,?,?,?)""",
                  (week_start, rank, uid, username or "", prize, valid_count, now()))
        c.commit()

def winners_for_week(week_start):
    with closing(db()) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM giveaway_winners WHERE week_start=? ORDER BY rank",
            (week_start,)).fetchall()]


# ============================================================
#  CLIENT + STATE
# ============================================================
app = Client("snipy_bot_v13", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

USER_CLIENTS: dict[int, Client] = {}
LOGIN_SESSIONS: dict[int, dict] = {}
ADD_STATE: dict[int, dict] = {}
PENDING_PAYMENT: dict[int, int] = {}
PENDING_DEPOSIT: dict[int, dict] = {}
BROADCAST_STATE: set[int] = set()
CLAIM_STATE: set[int] = set()
TICKET_STATE: dict[int, int] = {}
SEARCH_STATE: set[int] = set()
SETBAL_STATE: dict[int, int] = {}
BANNER_STATE: dict[int, str] = {}
ADDADMIN_STATE: dict[int, dict] = {}
SETRATE_STATE: set[int] = set()
SETWELCOME_STATE: set[int] = set()
ADDPAY_STATE: dict[int, dict] = {}
ADDFSUB_STATE: dict[int, dict] = {}
ADDGIVE_STATE: dict[int, dict] = {}
LB_PERIOD: dict[int, str] = {}


# ============================================================
#  HELPERS
# ============================================================
def is_on(): return get_setting("bot_status", "on") == "on"
def usdt_rate(): return float(get_setting("usdt_rate", DEFAULT_USDT_RATE))
def inr_to_usd(inr): return round(inr / usdt_rate(), 2) if usdt_rate() else 0
def usd_pair(inr): return f"${inr_to_usd(inr):.2f} • ₹{inr}"
def images_on(): return get_setting("images_on", "1") == "1"
def referral_on(): return get_setting("referral_on", "1") == "1"
def stock_layout(): return get_setting("stock_layout", "one")
def discount_for(qty):
    best = 0
    for min_q, pct in DISCOUNT_TIERS:
        if qty >= min_q and pct > best: best = pct
    return best
def fmt_date(iso_str):
    try: return iso_str.split("T")[0]
    except: return iso_str or "—"

def extract_otp_code(raw: str) -> str:
    if not raw: return ""
    m = re.search(r"Login code[:\s]+(\d{4,7})", raw, re.IGNORECASE)
    if m: return m.group(1)
    m = re.search(r"\bcode[:\s]+(\d{4,7})", raw, re.IGNORECASE)
    if m: return m.group(1)
    m = re.search(r"\b(\d{5,6})\b", raw)
    if m: return m.group(1)
    return ""


# ============================================================
#  KEYBOARDS
# ============================================================
def main_menu(uid):
    rows = [
        [KeyboardButton("🛒 Buy Account"), KeyboardButton("📁 Buy Full Stock")],
        [KeyboardButton("👤 My Profile"), KeyboardButton("📊 My Stats")],
        [KeyboardButton("💰 Deposit"),    KeyboardButton("🏆 Top Buyers")],
        [KeyboardButton("🎁 Refer & Earn")],
        [KeyboardButton("📞 Support")],
    ]
    if is_admin(uid):
        rows.append([KeyboardButton("🔐 Admin Panel")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)


def admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Status On/Off", callback_data="a_status"),
         InlineKeyboardButton("📈 Statistics", callback_data="a_stats")],
        [InlineKeyboardButton("➕ Add Account", callback_data="a_addacc"),
         InlineKeyboardButton("📦 Stock", callback_data="a_stock")],
        [InlineKeyboardButton("💵 Set Price", callback_data="a_addprice"),
         InlineKeyboardButton("💱 USDT Rate", callback_data="a_rate")],
        [InlineKeyboardButton("💳 Payment Methods", callback_data="a_paymethods"),
         InlineKeyboardButton("📢 Log Channel", callback_data="a_logchannel")],
        [InlineKeyboardButton("🔗 Force Channels", callback_data="a_fsub"),
         InlineKeyboardButton("🎁 Giveaway", callback_data="a_giveaway")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="a_broadcast"),
         InlineKeyboardButton("🔎 User Info", callback_data="a_userinfo")],
        [InlineKeyboardButton("💼 Change Balance", callback_data="a_setbal"),
         InlineKeyboardButton("🚫 Ban User", callback_data="a_ban")],
        [InlineKeyboardButton("💳 Payments", callback_data="a_payments"),
         InlineKeyboardButton("💬 Support", callback_data="a_support")],
        [InlineKeyboardButton("✏️ Welcome", callback_data="a_welcome"),
         InlineKeyboardButton("🖼 Banners", callback_data="a_banner")],
        [InlineKeyboardButton("💾 Backup", callback_data="a_backup"),
         InlineKeyboardButton("🔍 Search User", callback_data="a_search")],
        [InlineKeyboardButton("👥 Admins", callback_data="a_admins")],
    ])


def admin_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Menu", callback_data="a_panel")]])


def fsub_kb():
    channels = list_force_channels(only_enabled=True)
    rows = []
    for ch in channels:
        url = ch.get("invite_link") or (f"https://t.me/{str(ch['chat_id']).lstrip('@')}"
                                        if not str(ch['chat_id']).startswith("-") else None)
        if url:
            rows.append([InlineKeyboardButton(f"📢 {ch['title'] or 'Join'}", url=url)])
        else:
            rows.append([InlineKeyboardButton(f"⏳ {ch['title'] or 'Request Join'}",
                                              callback_data=f"fsub_req_{ch['id']}")])
    rows.append([InlineKeyboardButton("✅ I Have Joined", callback_data="check_sub")])
    return InlineKeyboardMarkup(rows)


def support_kb():
    admin_un = get_setting("admin_username", ADMIN_USERNAME).lstrip("@")
    free_url = get_setting("support_url", DEFAULT_SUPPORT)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{admin_un}")],
        [InlineKeyboardButton("🎁 Free Support Channel", url=free_url)],
        [InlineKeyboardButton("📝 Send Message via Bot", callback_data="support_ticket")],
    ])


def refer_kb(uid, bot_username):
    link = f"https://t.me/{bot_username}?start=ref_{uid}"
    share_url = (
        f"https://t.me/share/url?url={urllib.parse.quote(link)}"
        f"&text={urllib.parse.quote('🎁 Get fresh Telegram accounts & sessions instantly! Join using my link:')}"
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Share My Link", url=share_url)],
        [InlineKeyboardButton("📋 Copy My Link", callback_data="ref_copy")],
        [InlineKeyboardButton("👥 My Referral List", callback_data="ref_list")],
        [InlineKeyboardButton("🏆 Weekly Leaderboard", callback_data="lb_week")],
        [InlineKeyboardButton("🏠 Back to Home", callback_data="back_home")],
    ])


def leaderboard_kb(period="week"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(("✅ " if period=="day" else "") + "📅 Day", callback_data="lb_day"),
         InlineKeyboardButton(("✅ " if period=="week" else "") + "🗓 Week", callback_data="lb_week"),
         InlineKeyboardButton(("✅ " if period=="month" else "") + "📆 Month", callback_data="lb_month"),
         InlineKeyboardButton(("✅ " if period=="all" else "") + "♾ All Time", callback_data="lb_all")],
        [InlineKeyboardButton("📍 Where Am I?", callback_data="lb_where")],
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"lb_{period}")],
        [InlineKeyboardButton("⬅️ Back to Home", callback_data="back_home")],
    ])


# ============================================================
#  NOTIFY
# ============================================================
async def notify_owner(text=None, photo=None, doc=None, cap=None, kb=None):
    oid = owner_id()
    if not oid: return
    try:
        if photo: await app.send_photo(oid, photo, caption=cap or "", reply_markup=kb, parse_mode=HTML)
        elif doc: await app.send_document(oid, doc, caption=cap or "", reply_markup=kb, parse_mode=HTML)
        elif text: await app.send_message(oid, text, reply_markup=kb, parse_mode=HTML)
    except Exception as e:
        log.warning(f"owner notify: {e}")

async def notify_log(text=None, photo=None, cap=None):
    lc = log_channel_id()
    if not lc: return
    try:
        if photo: await app.send_photo(lc, photo, caption=cap or "", parse_mode=HTML)
        elif text: await app.send_message(lc, text, parse_mode=HTML)
    except Exception as e:
        log.warning(f"log notify: {e}")

async def safe_edit(cq: CallbackQuery, text: str, kb=None):
    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode=HTML)
    except Exception as e:
        log.warning(f"edit fail: {e}")
        try:
            await cq.message.reply(text, reply_markup=kb, parse_mode=HTML)
        except Exception as e2:
            log.warning(f"reply fail: {e2}")


# ============================================================
#  FORCE SUB
# ============================================================
async def is_subscribed(client, uid):
    channels = list_force_channels(only_enabled=True)
    if not channels: return True
    for ch in channels:
        try:
            cid = ch["chat_id"]
            if not str(cid).startswith("-") and not str(cid).startswith("@"):
                continue
            await client.get_chat_member(cid, uid)
        except UserNotParticipant:
            return False
        except Exception as e:
            log.warning(f"fsub check {cid}: {e}")
    return True

async def require_sub(client, msg_or_cq):
    uid = msg_or_cq.from_user.id
    if await is_subscribed(client, uid):
        try:
            referrer = mark_referral_valid(uid)
            if referrer:
                u = get_user(uid) or {}
                uname = f"@{u.get('username')}" if u.get("username") else u.get("first_name", "user")
                try:
                    await client.send_message(
                        referrer,
                        f"🎉 <b>New valid referral!</b>\n\n👤 {uname}\n"
                        f"💰 +₹{referral_reward()} credited instantly!",
                        parse_mode=HTML)
                except Exception: pass
        except Exception as e:
            log.warning(f"ref valid: {e}")
        return True
    txt = ("🔒 <b>Force Subscribe Required!</b>\n\n"
           "Bot use karne ke liye pehle neeche wale channels join karo 👇\n\n"
           "<i>Private channel ke liye request bhejo, approval ke baad Start dabao.</i>")
    if isinstance(msg_or_cq, Message):
        await msg_or_cq.reply(txt, reply_markup=fsub_kb(), parse_mode=HTML)
    else:
        await msg_or_cq.message.reply(txt, reply_markup=fsub_kb(), parse_mode=HTML)
    return False


@app.on_callback_query(filters.regex("^check_sub$"))
async def cb_check_sub(client, cq: CallbackQuery):
    if await is_subscribed(client, cq.from_user.id):
        try:
            referrer = mark_referral_valid(cq.from_user.id)
            if referrer:
                try:
                    await client.send_message(referrer,
                        f"🎉 <b>New valid referral!</b>\n💰 +₹{referral_reward()}",
                        parse_mode=HTML)
                except Exception: pass
        except Exception: pass
        await cq.answer("✅ Verified!", show_alert=True)
        await safe_edit(cq, "✅ <b>Subscribed!</b> Ab /start dobara bhejo.", None)
    else:
        await cq.answer("❌ Pehle join karo!", show_alert=True)


@app.on_callback_query(filters.regex(r"^fsub_req_(\d+)$"))
async def cb_fsub_req(client, cq: CallbackQuery):
    ch_id = int(cq.matches[0].group(1))
    with closing(db()) as c:
        r = c.execute("SELECT * FROM force_channels WHERE id=?", (ch_id,)).fetchone()
    if not r:
        return await cq.answer("Channel missing.", show_alert=True)
    ch = dict(r)
    try:
        invite_link = await client.create_chat_invite_link(ch["chat_id"], creates_join_request=True)
        url = invite_link.invite_link
    except Exception as e:
        log.warning(f"invite req fail: {e}")
        url = ch.get("invite_link") or f"https://t.me/{str(ch['chat_id']).lstrip('@')}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📩 Send Join Request", url=url)],
        [InlineKeyboardButton("✅ I Have Joined", callback_data="check_sub")],
    ])
    await safe_edit(cq,
        "⏳ <b>Join Request Sent!</b>\n\n"
        "Your request to join has been received.\n"
        "You can use the bot while waiting for approval!\n\n"
        "Send /start to begin.", kb)


# ============================================================
#  SUPPORT
# ============================================================
async def show_support(client, chat_id, reply_to=None):
    desc = get_setting("support_desc", SUPPORT_DESCRIPTION)
    banner = get_banner("support") if images_on() else None
    if banner:
        try:
            if reply_to:
                await reply_to.reply_photo(banner, caption=desc, reply_markup=support_kb(), parse_mode=HTML)
            else:
                await app.send_photo(chat_id, banner, caption=desc, reply_markup=support_kb(), parse_mode=HTML)
            return
        except Exception as e:
            log.warning(f"support banner: {e}")
    if reply_to:
        await reply_to.reply(desc, reply_markup=support_kb(), parse_mode=HTML)
    else:
        await app.send_message(chat_id, desc, reply_markup=support_kb(), parse_mode=HTML)


@app.on_callback_query(filters.regex("^support_ticket$"))
async def cb_support_ticket(client, cq: CallbackQuery):
    uid = cq.from_user.id
    tid = TICKET_STATE.get(uid)
    if not tid:
        tid = create_ticket(uid); TICKET_STATE[uid] = tid
    await cq.answer(f"Ticket #{tid} open.", show_alert=True)
    await cq.message.reply(f"📝 <b>Ticket #{tid} active.</b>\n\nApna message yahan bhejo.", parse_mode=HTML)


# ============================================================
#  MAIN MENU DISPATCH
# ============================================================
async def dispatch_main_btn(client, msg: Message, cb: str):
    uid = msg.from_user.id
    u = get_user(uid) or {}

    if cb == "u_buy":
        try: groups = stock_groups()
        except Exception as e: return await msg.reply(f"<b>Error:</b> {e}", parse_mode=HTML)
        if not groups:
            return await msg.reply("<b>❌ Stock is Empty right now. Check back later!</b>", parse_mode=HTML)
        rate = usdt_rate()
        lines = ["<b>🛒 SELECT TELEGRAM (ACCOUNTS)</b>",
                 "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "",
                 f"<b>⚡ Rate:</b> 1 USDT = ₹{rate}", "", "<blockquote>"]
        for g in groups:
            lines.append(f"• {g['flag'] or ''} {g['country_name']} {g['year']}: "
                         f"${inr_to_usd(g['price'])} (₹{g['price']}) - Stock: {g['cnt']}")
        lines.append("</blockquote>")
        lines += ["", "<b>Select an item to view details:</b>"]
        btns = []
        for g in groups:
            key = urllib.parse.quote(f"{g['country_name']}|{g['year']}|{g['price']}")
            btns.append([InlineKeyboardButton(
                f"{g['flag'] or ''} {g['country_name']} {g['year']} · ₹{g['price']} · x{g['cnt']}",
                callback_data=f"u_group_{key}")])
        banner = get_banner("shop") if images_on() else None
        if banner:
            try:
                return await msg.reply_photo(banner, caption="\n".join(lines),
                                             reply_markup=InlineKeyboardMarkup(btns), parse_mode=HTML)
            except Exception: pass
        return await msg.reply("\n".join(lines), reply_markup=InlineKeyboardMarkup(btns), parse_mode=HTML)

    if cb == "u_fullstock":
        try: groups = stock_groups()
        except Exception as e: return await msg.reply(f"<b>Error:</b> {e}", parse_mode=HTML)
        if not groups:
            return await msg.reply("<b>❌ Stock is Empty right now. Check back later!</b>", parse_mode=HTML)
        lines = ["<b>📁 BUY FULL STOCK</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "",
                 "Bulk: <b>5+ items = 20% off</b>, <b>10+ = 30% off</b>", "", "<blockquote>"]
        total = 0; qty = 0
        for g in groups:
            lines.append(f"• {g['flag'] or ''} {g['country_name']} {g['year']}: ₹{g['price']} x {g['cnt']}")
            total += g["price"] * g["cnt"]; qty += g["cnt"]
        lines.append("</blockquote>")
        disc = discount_for(qty); discounted = int(total * (100 - disc) / 100)
        lines += ["", f"<b>Total items:</b> {qty}",
                  f"<b>Subtotal:</b> ₹{total}",
                  f"<b>Discount:</b> {disc}% (-₹{total-discounted})",
                  f"<b>Pay:</b> <b>{usd_pair(discounted)}</b>"]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🛒 Buy ALL ({qty}) · ₹{discounted}", callback_data="u_buyall_confirm")]])
        return await msg.reply("\n".join(lines), reply_markup=kb, parse_mode=HTML)

    if cb == "u_profile":
        bal = u.get("balance", 0)
        deposited = u.get("total_deposited", 0)
        joined = fmt_date(u.get("joined_at", ""))
        text = (
            "👤 <b>USER PROFILE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 <b>User ID:</b> <code>{u.get('user_id', uid)}</code>\n"
            f"💰 <b>Balance:</b> <b>{usd_pair(bal)}</b>\n"
            f"💳 <b>Deposited:</b> <b>{usd_pair(deposited)}</b>\n"
            f"📅 <b>Joined:</b> {joined}"
        )
        banner = get_banner("profile") if images_on() else None
        if banner:
            try: return await msg.reply_photo(banner, caption=text, parse_mode=HTML)
            except Exception: pass
        return await msg.reply(text, parse_mode=HTML)

    if cb == "u_stats":
        bought = user_orders_count(uid)
        spent = u.get("total_spent", 0)
        deposited = u.get("total_deposited", 0)
        text = (
            "📊 <b>My Statistics</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🛒 <b>Accounts Bought:</b> {bought}\n"
            f"💰 <b>Total Spent:</b>\n<b>${inr_to_usd(spent):.2f}</b>\n"
            f"💳 <b>Total Deposited:</b>\n<b>${inr_to_usd(deposited):.2f}</b>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 View Purchase Logs", callback_data="u_purchaselogs")]])
        return await msg.reply(text, reply_markup=kb, parse_mode=HTML)

    if cb == "u_deposit":
        methods = list_payment_methods()
        if not methods:
            return await msg.reply("<b>⚠️ No payment methods configured.</b>", parse_mode=HTML)
        desc = get_setting("deposit_desc", DEPOSIT_DESCRIPTION)
        btns = [[InlineKeyboardButton(f"{m['emoji']} {m['name']}",
                                      callback_data=f"dep_method_{m['id']}")] for m in methods]
        banner = get_banner("deposit") if images_on() else None
        if banner:
            try:
                return await msg.reply_photo(banner, caption=desc,
                                             reply_markup=InlineKeyboardMarkup(btns), parse_mode=HTML)
            except Exception: pass
        return await msg.reply(desc, reply_markup=InlineKeyboardMarkup(btns), parse_mode=HTML)

    if cb == "u_topbuyers":
        buyers = top_buyers(10)
        if not buyers:
            return await msg.reply("🏆 <b>TOP BUYERS</b>\n\n<i>Abhi tak koi purchase nahi hui.</i>", parse_mode=HTML)
        lines = ["🏆 <b>TOP BUYERS</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", ""]
        medals = ["🥇", "🥈", "🥉"]
        for i, b in enumerate(buyers, 1):
            medal = medals[i-1] if i <= 3 else f"<b>{i}.</b>"
            uname = f"@{b['username']}" if b.get("username") else b.get("first_name", "user")
            lines.append(f"{medal} {uname} — <b>${inr_to_usd(b['total_spent']):.2f}</b>")
        return await msg.reply("\n".join(lines), parse_mode=HTML)

    if cb == "u_refer":
        return await show_refer_page(client, uid, reply_to=msg)

    if cb == "u_support":
        return await show_support(client, uid, reply_to=msg)

    if cb == "a_panel":
        if not is_admin(uid):
            return await msg.reply("<b>Only admins.</b>", parse_mode=HTML)
        return await msg.reply("💻 <b>ADVANCED ADMIN DASHBOARD</b>",
                               reply_markup=admin_kb(), parse_mode=HTML)


MAIN_BTN_HANDLERS = {
    "🛒 Buy Account":    "u_buy",
    "📁 Buy Full Stock": "u_fullstock",
    "👤 My Profile":     "u_profile",
    "📊 My Stats":       "u_stats",
    "💰 Deposit":        "u_deposit",
    "🏆 Top Buyers":     "u_topbuyers",
    "🎁 Refer & Earn":   "u_refer",
    "📞 Support":        "u_support",
    "🔐 Admin Panel":    "a_panel",
}


# ============================================================
#  REFER PAGE
# ============================================================
async def show_refer_page(client, uid, reply_to: Message = None, cq: CallbackQuery = None):
    try:
        me_tg = await client.get_me()
        bot_username = me_tg.username
    except Exception:
        bot_username = BOT_USERNAME

    stats = user_referral_stats(uid)
    ref_link = f"https://t.me/{bot_username}?start=ref_{uid}"
    reward = referral_reward()
    earned_inr = stats["earned"]
    earned_usd = inr_to_usd(earned_inr)

    text = (
        "🎁 <b>SnipyStore — Refer & Earn</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 <b>Per Referral:</b> ${inr_to_usd(reward):.4f} — credited instantly when valid!\n"
        f"💵 <b>Total Earned:</b> ${earned_usd:.2f}\n\n"
        "📊 <b>Your Referrals</b>\n"
        f"┣ 📆 This week: {stats['this_week']} (resets in {period_reset_in('week')})\n"
        f"┣ ✅ All-time valid: {stats['all_time_valid']}\n"
        f"┗ ⏳ Waiting to join all chats: {stats['waiting']}\n\n"
        "🔗 <b>Your Referral Link</b>\n"
        f"<code>{ref_link}</code>\n"
        "Tap to copy, then share anywhere!\n\n"
        "📋 <b>How it counts:</b>\n"
        "1️⃣ Share your link with friends\n"
        "2️⃣ They join ALL required chats\n"
        "3️⃣ They press Start — then it's valid and you're paid ✅\n\n"
        "🏆 There's also a weekly top-referrer leaderboard for bragging rights — check it below."
    )
    kb = refer_kb(uid, bot_username)
    banner = get_banner("refer") if images_on() else None
    if banner and reply_to and not cq:
        try:
            return await reply_to.reply_photo(banner, caption=text, reply_markup=kb, parse_mode=HTML)
        except Exception: pass
    if cq:
        return await safe_edit(cq, text, kb)
    elif reply_to:
        return await reply_to.reply(text, reply_markup=kb, parse_mode=HTML)


@app.on_callback_query(filters.regex("^ref_copy$"))
async def cb_ref_copy(client, cq: CallbackQuery):
    uid = cq.from_user.id
    try:
        me = await client.get_me()
        bot_username = me.username
    except Exception:
        bot_username = BOT_USERNAME
    link = f"https://t.me/{bot_username}?start=ref_{uid}"
    await cq.answer(f"Link copied: {link}", show_alert=True)


@app.on_callback_query(filters.regex("^ref_list$"))
async def cb_ref_list(client, cq: CallbackQuery):
    uid = cq.from_user.id
    refs = user_referral_list(uid, 30)
    back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back_refer")]])
    if not refs:
        return await safe_edit(cq,
            "👥 <b>My Referral List</b>\n\n<i>Abhi tak koi referral nahi.</i>", back_kb)
    lines = ["👥 <b>My Referral List</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", ""]
    valid_c = 0
    for r in refs:
        uname = f"@{r['username']}" if r.get("username") else (r.get("first_name") or f"User{r['referred_id']}")
        if r["valid"]:
            status = "✅ Valid"
            valid_c += 1
        else:
            status = "⏳ Waiting"
        lines.append(f"• {uname} — {status}")
    lines += ["", f"<b>Total:</b> {len(refs)}  ·  <b>Valid:</b> {valid_c}"]
    await safe_edit(cq, "\n".join(lines), back_kb)


@app.on_callback_query(filters.regex("^back_refer$"))
async def cb_back_refer(client, cq: CallbackQuery):
    await show_refer_page(client, cq.from_user.id, cq=cq)


# ============================================================
#  LEADERBOARD
# ============================================================
def _period_title(period):
    return {"day": "Today", "week": "This Week",
            "month": "This Month", "all": "All Time"}.get(period, "This Week")

def _lb_body(period):
    board = referral_leaderboard(period, 10)
    lines = [f"🏆 <b>Referral Leaderboard — {_period_title(period)}</b>",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    if period != "all":
        lines.append(f"⏰ <b>Resets in:</b> {period_reset_in(period)}")
    lines.append("")
    if not board:
        lines.append("<i>Abhi tak koi valid referral nahi hua.</i>")
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(board, 1):
        medal = medals[i-1] if i <= 3 else f"{i}️⃣"
        uname = f"@{r['username']}" if r.get("username") else (r.get("first_name") or f"User{r['uid']}")
        lines.append(f"{medal} <b>{uname}</b>")
        lines.append(f"   ✅ Valid: {r['valid']}  👥 Total: {r['valid']}")
        lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # THIS WEEK'S GIVEAWAY (only on week view)
    if period == "week":
        gws = list_giveaways(only_enabled=True)
        if gws:
            lines.append("")
            lines.append("🎁 <b>THIS WEEK'S GIVEAWAY</b>")
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            gmedals = {1: "🥇", 2: "🥈", 3: "🥉"}
            for g in sorted(gws, key=lambda x: x["rank"]):
                em = gmedals.get(g["rank"], f"{g['rank']}️⃣")
                lines.append(f"{em} Rank {g['rank']}: <b>{g['prize']}</b>")
            lines.append("")
            lines.append(f"⏰ <b>Resets in:</b> {period_reset_in('week')}")
    return "\n".join(lines)

async def _show_lb(client, cq: CallbackQuery, period: str):
    LB_PERIOD[cq.from_user.id] = period
    await safe_edit(cq, _lb_body(period), leaderboard_kb(period))


@app.on_callback_query(filters.regex("^lb_day$"))
async def cb_lb_day(client, cq): await _show_lb(client, cq, "day")

@app.on_callback_query(filters.regex("^lb_week$"))
async def cb_lb_week(client, cq): await _show_lb(client, cq, "week")

@app.on_callback_query(filters.regex("^lb_month$"))
async def cb_lb_month(client, cq): await _show_lb(client, cq, "month")

@app.on_callback_query(filters.regex("^lb_all$"))
async def cb_lb_all(client, cq): await _show_lb(client, cq, "all")


@app.on_callback_query(filters.regex("^lb_where$"))
async def cb_lb_where(client, cq: CallbackQuery):
    uid = cq.from_user.id
    period = LB_PERIOD.get(uid, "week")
    rank_info = referral_rank(uid, period)
    above = rank_info.get("above_user")
    text = (
        "🏆 <b>Your Leaderboard Position</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📍 You are <b>#{rank_info['rank']}</b> of <b>{rank_info['total']}</b> referrers\n"
        f"✅ Your valid referrals: <b>{rank_info['my_valid']}</b>\n"
    )
    if above:
        above_un = f"@{above['username']}" if above.get("username") else (above.get("first_name") or "someone")
        diff = rank_info["diff"]
        text += f"\n⬆️ <b>{above_un}</b> is #{rank_info['rank']-1} with {rank_info['above_valid']} valid — {diff} more to overtake!"
    else:
        text += "\n🥇 <b>You are #1! Keep it up.</b>"
    await safe_edit(cq, text, leaderboard_kb(period))


# ============================================================
#  /start
# ============================================================
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client, msg: Message):
    if not is_on() and not is_admin(msg.from_user.id):
        return await msg.reply("<b>Bot is offline. Try later.</b>", parse_mode=HTML)
    ref = None
    if len(msg.command) > 1:
        arg = msg.command[1]
        if arg.startswith("ref_"):
            try: ref = int(arg[4:])
            except: ref = None
        else:
            try:
                ru = get_user_by_ref(arg)
                if ru: ref = ru["user_id"]
            except: pass
    if ref == msg.from_user.id: ref = None
    upsert_user(msg.from_user, referrer=ref)
    u = get_user(msg.from_user.id)
    if u and u.get("banned"):
        return await msg.reply("<b>You are banned.</b>", parse_mode=HTML)
    if not owner_id():
        CLAIM_STATE.add(msg.from_user.id)
        return await msg.reply("Setup — send <code>YES OWNER</code> to claim ownership.", parse_mode=HTML)
    if not await require_sub(client, msg):
        return
    welcome = get_setting("welcome_msg", WELCOME_MSG).replace("{name}", msg.from_user.first_name or "user")
    text = f"{welcome}\n\n💰 <b>Balance:</b> {usd_pair(u['balance'] if u else 0)}"
    banner = get_banner("welcome") if images_on() else None
    if banner:
        try:
            return await msg.reply_photo(banner, caption=text,
                                         reply_markup=main_menu(msg.from_user.id), parse_mode=HTML)
        except Exception: pass
    await msg.reply(text, reply_markup=main_menu(msg.from_user.id), parse_mode=HTML)


@app.on_message(filters.command("menu") & filters.private)
async def cmd_menu(client, msg: Message):
    u = get_user(msg.from_user.id) or {}
    await msg.reply(f"🏠 <b>Main Menu</b>\n\n💰 Balance: <b>{usd_pair(u.get('balance', 0))}</b>",
                    reply_markup=main_menu(msg.from_user.id), parse_mode=HTML)


@app.on_message(filters.command("refer") & filters.private)
async def cmd_refer(client, msg: Message):
    await show_refer_page(client, msg.from_user.id, reply_to=msg)


@app.on_message(filters.command("leaderboard") & filters.private)
async def cmd_leaderboard(client, msg: Message):
    await msg.reply(_lb_body("week"), reply_markup=leaderboard_kb("week"), parse_mode=HTML)


# ============================================================
#  BUY FLOW
# ============================================================
@app.on_callback_query(filters.regex(r"^u_group_"))
async def cb_buy_group(client, cq: CallbackQuery):
    try:
        raw = urllib.parse.unquote(cq.data[len("u_group_"):])
        country, year_s, price_s = raw.split("|")
        year, price = int(year_s), int(price_s)
    except Exception as e:
        return await cq.answer(f"Parse error: {e}", show_alert=True)
    ids = account_ids_in_group(country, year, price)
    if not ids:
        return await cq.answer("Stock is Empty right now. Check back later!", show_alert=True)
    lines = [f"<b>📦 {country} · {year} · ₹{price}</b>", "",
             f"<b>Available:</b> {len(ids)}", "", "Tap to buy."]
    btns = []
    for aid in ids[:30]:
        btns.append([InlineKeyboardButton(f"🛒 Buy #{aid}", callback_data=f"u_buyone_{aid}")])
    await safe_edit(cq, "\n".join(lines), InlineKeyboardMarkup(btns))


@app.on_callback_query(filters.regex(r"^u_buyone_(\d+)$"))
async def cb_buy_one(client, cq: CallbackQuery):
    aid = int(cq.matches[0].group(1)); a = get_account(aid)
    if not a or a["status"] != "available":
        return await cq.answer("Stock is Empty right now. Check back later!", show_alert=True)
    u = get_user(cq.from_user.id)
    oid = create_order(cq.from_user.id, cq.from_user.username or cq.from_user.first_name,
                       aid, a["country_name"], a["price"])
    PENDING_PAYMENT[cq.from_user.id] = oid
    has_balance = u["balance"] >= a["price"]
    text = (
        f"🧾 <b>Order {gen_order_num(oid)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌍 <b>{a['country_name']} · {a['year']}</b>\n"
        f"💰 <b>Price:</b> <b>{usd_pair(a['price'])}</b>\n"
        f"💼 <b>Your Balance:</b> <b>{usd_pair(u['balance'])}</b>\n\n"
    )
    if has_balance:
        text += "<b>Pay from balance or send screenshot.</b>"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"💳 Pay ₹{a['price']} from Balance", callback_data=f"u_paybal_{oid}")],
            [InlineKeyboardButton("🧾 Send Payment Screenshot", callback_data=f"u_sspay_{oid}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"u_cancel_{oid}")]])
    else:
        shortfall = a["price"] - u["balance"]
        text += f"<b>⚠️ Balance not enough.</b>\n\n<b>You need {usd_pair(shortfall)} more.</b>"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Deposit Now", callback_data="u_deposit")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"u_cancel_{oid}")]])
    await safe_edit(cq, text, kb)


@app.on_callback_query(filters.regex(r"^u_paybal_(\d+)$"))
async def cb_pay_balance(client, cq: CallbackQuery):
    oid = int(cq.matches[0].group(1)); o = get_order(oid)
    if not o or o["user_id"] != cq.from_user.id:
        return await cq.answer("Not yours.")
    u = get_user(cq.from_user.id)
    if u["balance"] < o["price"]:
        return await cq.answer("Balance kam.", show_alert=True)
    deduct_balance(cq.from_user.id, o["price"])
    add_spent(cq.from_user.id, o["price"])
    update_order(oid, status="approved")
    if o["acc_id"]: mark_sold(o["acc_id"], o["user_id"])
    await safe_edit(cq, "<b>✅ Paid.</b>", None)
    await deliver(client, oid)


@app.on_callback_query(filters.regex(r"^u_sspay_(\d+)$"))
async def cb_ss_info(client, cq: CallbackQuery):
    await cq.answer("Screenshot bhejo is chat me.", show_alert=True)


@app.on_callback_query(filters.regex(r"^u_cancel_(\d+)$"))
async def cb_cancel_order(client, cq: CallbackQuery):
    oid = int(cq.matches[0].group(1)); o = get_order(oid)
    if o and o["user_id"] == cq.from_user.id:
        update_order(oid, status="cancelled")
    PENDING_PAYMENT.pop(cq.from_user.id, None)
    await safe_edit(cq, "<b>❌ Order cancelled.</b>", None)


@app.on_callback_query(filters.regex("^u_buyall_confirm$"))
async def cb_buyall_confirm(client, cq: CallbackQuery):
    ids = [a["id"] for a in list_available()]
    if not ids:
        return await cq.answer("Stock is Empty!", show_alert=True)
    qty = len(ids); disc = discount_for(qty)
    u = get_user(cq.from_user.id)
    full_price = sum(get_account(aid)["price"] for aid in ids if get_account(aid))
    pay = int(full_price * (100 - disc) / 100)
    if u["balance"] < pay:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💰 Deposit Now", callback_data="u_deposit")]])
        return await safe_edit(cq, f"<b>⚠️ Balance not enough.</b>\n\n"
                                    f"<b>You need {usd_pair(pay - u['balance'])} more.</b>", kb)
    deduct_balance(cq.from_user.id, pay)
    add_spent(cq.from_user.id, pay)
    for aid in ids:
        a = get_account(aid)
        if not a: continue
        mark_sold(aid, cq.from_user.id)
        oid = create_order(cq.from_user.id, cq.from_user.username or cq.from_user.first_name,
                           aid, a["country_name"], a["price"], disc)
        update_order(oid, status="approved")
        await deliver(client, oid)
    await safe_edit(cq, f"<b>✅ Bought {qty} accounts for ₹{pay}.</b>", None)


# ============================================================
#  PURCHASE LOGS
# ============================================================
@app.on_callback_query(filters.regex("^u_purchaselogs$"))
async def cb_u_purchaselogs(client, cq: CallbackQuery):
    uid = cq.from_user.id
    orders = [o for o in user_orders(uid, 50) if o["status"] == "approved"]
    if not orders:
        return await cq.answer("❌ No purchases yet.", show_alert=True)
    lines = ["<b>📋 PURCHASE LOGS</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", ""]
    for o in orders[:30]:
        lines.append(f"<b>{gen_order_num(o['id'])}</b> · {o['country']} · ₹{o['price']} · <i>{o['created_at'][:10]}</i>")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back to Home", callback_data="back_home")]])
    await safe_edit(cq, "\n".join(lines), kb)


# ============================================================
#  SESSIONS
# ============================================================
@app.on_callback_query(filters.regex("^sessions$"))
async def cb_sessions(client, cq: CallbackQuery):
    bought = user_bought_accounts(cq.from_user.id)
    if not bought:
        return await cq.answer("Koi account nahi.", show_alert=True)
    btns = []
    for a in bought:
        live = "🟢" if a["id"] in USER_CLIENTS else "⚪"
        btns.append([InlineKeyboardButton(f"{live} {a['country_name']} · {a['phone']}",
                                          callback_data=f"sessinfo_{a['id']}")])
    btns.append([InlineKeyboardButton("🏠 Back to Home", callback_data="back_home")])
    await safe_edit(cq, "🔐 <b>Sessions</b>\n\n🟢 active · ⚪ idle", InlineKeyboardMarkup(btns))


@app.on_callback_query(filters.regex(r"^sessinfo_(\d+)$"))
async def cb_sessinfo(client, cq: CallbackQuery):
    aid = int(cq.matches[0].group(1)); a = get_account(aid)
    if not a or a["sold_to"] != cq.from_user.id:
        return await cq.answer("Not yours.", show_alert=True)
    live = "running" if aid in USER_CLIENTS else "idle"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 Request New OTP", callback_data=f"reotp_{aid}_0")],
        [InlineKeyboardButton("⬅️ Back to Sessions", callback_data="sessions")]])
    await safe_edit(cq,
        f"🔐 <b>Session · {a['country_name']}</b>\n\n"
        f"📱 <b>Phone:</b> <code>{a['phone']}</code>\n"
        f"📡 <b>Status:</b> {live}", kb)


@app.on_callback_query(filters.regex(r"^reotp_(\d+)_(\d+)$"))
async def cb_reotp(client, cq: CallbackQuery):
    aid = int(cq.matches[0].group(1)); oid = int(cq.matches[0].group(2))
    a = get_account(aid)
    if not a: return await cq.answer("Account gone.")
    if a["sold_to"] != cq.from_user.id:
        return await cq.answer("Not yours.", show_alert=True)
    if aid not in USER_CLIENTS:
        await start_otp_listener(client, a, a["sold_to"], oid or 0)
        await asyncio.sleep(2)
    ua = USER_CLIENTS.get(aid)
    if not ua:
        return await cq.answer("Session not connected.", show_alert=True)
    try:
        await ua.send_code(a["phone"])
        await cq.message.reply("🔁 <b>New OTP requested!</b>\n\nLogin karo, OTP yahin aayega.",
                               parse_mode=HTML)
    except Exception as e:
        await cq.answer(f"Fail: {e}", show_alert=True)


@app.on_callback_query(filters.regex("^back_home$"))
async def cb_back_home_btn(client, cq: CallbackQuery):
    u = get_user(cq.from_user.id) or {}
    await safe_edit(cq, f"🏠 <b>Main Menu</b>\n\n💰 Balance: <b>{usd_pair(u.get('balance', 0))}</b>", None)


@app.on_callback_query(filters.regex("^u_home$"))
async def cb_home(client, cq: CallbackQuery):
    u = get_user(cq.from_user.id) or {}
    await safe_edit(cq, f"🏠 <b>Main Menu</b>\n\n💰 Balance: <b>{usd_pair(u.get('balance', 0))}</b>", None)


# ============================================================
#  DEPOSIT
# ============================================================
def make_qr(data):
    q = qrcode.QRCode(box_size=8, border=2)
    q.add_data(data); q.make(fit=True)
    img = q.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0); return buf

def upi_uri(upi_id, upi_name, amount, note):
    params = {"pa": upi_id, "pn": upi_name, "am": str(amount), "cu": "INR", "tn": note}
    return "upi://pay?" + urllib.parse.urlencode(params)


@app.on_callback_query(filters.regex(r"^dep_method_(\d+)$"))
async def cb_dep_method(client, cq: CallbackQuery):
    pid = int(cq.matches[0].group(1))
    m = get_payment_method(pid)
    if not m or not m["enabled"]:
        return await cq.answer("Method not available.", show_alert=True)
    PENDING_DEPOSIT[cq.from_user.id] = {"awaiting_amount": True, "method_id": pid}
    min_d = get_setting("min_deposit", DEFAULT_MIN_DEP)
    await safe_edit(cq,
        f"{m['emoji']} <b>{m['name']}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 <b>Kitna amount deposit karna hai?</b>\n"
        f"<b>Min:</b> ₹{min_d}\n\n"
        f"Number bhejo (e.g. <code>100</code>):",
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="u_home")]]))


async def deposit_amount_received(client, msg: Message, amt: int):
    uid = msg.from_user.id
    min_d = int(get_setting("min_deposit", DEFAULT_MIN_DEP))
    if amt < min_d:
        return await msg.reply(f"<b>❌ Min deposit ₹{min_d}.</b>", parse_mode=HTML)
    pd = PENDING_DEPOSIT.get(uid, {})
    pid = pd.get("method_id")
    m = get_payment_method(pid) if pid else None
    if not m:
        return await msg.reply("<b>⚠️ Method expired. Restart.</b>", parse_mode=HTML)

    did, ref_id = create_deposit(uid, amt, m["type"], m["name"])
    PENDING_DEPOSIT[uid] = {"deposit_id": did, "ref_id": ref_id, "amount": amt,
                             "method_id": pid, "awaiting_proof": True}

    if m["type"] == "upi":
        upi_str = upi_uri(m["address"], get_setting("upi_name", DEFAULT_UPI_NAME), amt, ref_id)
        qr_data = upi_str
    else:
        qr_data = m["address"]; upi_str = None
    qr = make_qr(qr_data)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ I Have Paid", callback_data=f"deppaid_{did}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="u_home")]])

    if m["type"] == "upi":
        caption = (
            f"⚡ <b>Scan The Above QR Code to Pay:</b> ₹{amt} (${inr_to_usd(amt):.2f} (~₹{amt}))\n\n"
            f"🔖 <b>Ref ID:</b> <code>{ref_id}</code>\n"
            f"💳 <b>UPI ID:</b> <code>{m['address']}</code>\n\n"
            f"👉 Open any UPI app and scan the QR above\n"
            f"   or manually enter UPI ID above\n"
            f"👉 <b>Pay exactly ₹{amt}</b> (⚠️ Do Not Change The Amount)\n"
            f"🟢 After Successful Payment Click '✅ I Have Paid' below\n\n"
            f"💡 If this QR fails, tap Cancel, reopen Deposit and try another QR.\n\n"
            f"⚠️ <b>The QR Code Is Only Valid For 15 Minutes!</b>"
        )
    else:
        caption = (
            f"⚡ <b>Send exactly ₹{amt} (${inr_to_usd(amt):.2f})</b>\n\n"
            f"🔖 <b>Ref ID:</b> <code>{ref_id}</code>\n"
            f"📮 <b>Address:</b>\n<code>{m['address']}</code>\n\n"
            f"⚠️ <b>Network:</b> {m['type'].upper()}\n\n"
            f"🟢 After Payment Click '✅ I Have Paid'"
        )
    try:
        await msg.reply_photo(qr, caption=caption, reply_markup=kb, parse_mode=HTML)
    except Exception as e:
        log.warning(f"qr fail: {e}")
        try: await msg.reply(caption, reply_markup=kb, parse_mode=HTML)
        except Exception as e2:
            log.error(f"fallback: {e2}")


@app.on_callback_query(filters.regex(r"^deppaid_(\d+)$"))
async def cb_dep_paid(client, cq: CallbackQuery):
    did = int(cq.matches[0].group(1)); d = get_deposit(did)
    if not d or d["user_id"] != cq.from_user.id:
        return await cq.answer("Not yours.")
    PENDING_DEPOSIT[cq.from_user.id] = {"deposit_id": did, "ref_id": d["ref_id"],
                                         "amount": d["amount"], "awaiting_proof": True}
    text = (
        "✅ <b>Payment Noted!</b>\n\n"
        "📸 <b>Please send a screenshot of your payment now.</b>\n\n"
        f"<b>Amount:</b> ₹{d['amount']} (${inr_to_usd(d['amount']):.2f} (~₹{d['amount']}))\n"
        f"🔖 <b>Ref ID:</b> <code>{d['ref_id']}</code>\n\n"
        "Send the screenshot image..."
    )
    await safe_edit(cq, text, None)


# ============================================================
#  PHOTO ROUTER
# ============================================================
@app.on_message(filters.private & filters.photo)
async def photo_router(client, msg: Message):
    uid = msg.from_user.id
    dep = PENDING_DEPOSIT.get(uid)
    if dep and dep.get("awaiting_proof"):
        did = dep["deposit_id"]
        update_deposit(did, status="pending_approval", proof_file=msg.photo.file_id)
        PENDING_DEPOSIT.pop(uid, None)
        d = get_deposit(did)
        ref_id = d.get("ref_id", gen_ref_id(uid))
        txt = (
            "✅ <b>Screenshot received!</b>\n\n"
            "Your deposit is awaiting admin approval.\n\n"
            f"🔖 <b>Ref ID:</b> <code>{ref_id}</code>\n\n"
            "Keep this ID in case you need to follow up with support.\n"
            "You will be notified once approved."
        )
        await msg.reply(txt, parse_mode=HTML)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"dappr_{did}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"drej_{did}")]])
        cap = (f"💰 <b>NEW DEPOSIT</b>\n"
               "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
               f"<b>Ref:</b> <code>{ref_id}</code>\n"
               f"<b>User:</b> <a href='tg://user?id={uid}'>{msg.from_user.first_name}</a>\n"
               f"<b>ID:</b> <code>{uid}</code>\n"
               f"<b>Amount:</b> ₹{d['amount']} (${inr_to_usd(d['amount']):.2f})\n"
               f"<b>Method:</b> {d.get('method_name', d['method'])}")
        await notify_owner(photo=msg.photo.file_id, cap=cap, kb=kb)
        return
    oid = PENDING_PAYMENT.get(uid)
    if oid:
        o = get_order(oid)
        if o:
            update_order(oid, status="pending_approval", payment_file=msg.photo.file_id)
            PENDING_PAYMENT.pop(uid, None)
            await msg.reply(f"<b>✅ Screenshot mila. Order {gen_order_num(oid)} approval me hai.</b>",
                            parse_mode=HTML)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=f"oappr_{oid}"),
                InlineKeyboardButton("❌ Reject",  callback_data=f"orej_{oid}")]])
            cap = (f"🛒 <b>NEW ORDER PAYMENT</b>\n"
                   "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                   f"<b>Order:</b> {gen_order_num(oid)}\n"
                   f"<b>User:</b> <a href='tg://user?id={uid}'>{msg.from_user.first_name}</a>\n"
                   f"<b>ID:</b> <code>{uid}</code>\n"
                   f"<b>Country:</b> {o['country']}\n"
                   f"<b>Price:</b> ₹{o['price']}")
            await notify_owner(photo=msg.photo.file_id, cap=cap, kb=kb)
            return
    if uid in BANNER_STATE:
        slot = BANNER_STATE.pop(uid)
        set_banner(slot, msg.photo.file_id)
        await msg.reply(f"<b>✅ Banner set: {slot}</b>", reply_markup=admin_kb(), parse_mode=HTML)
        return


# ============================================================
#  APPROVE / REJECT
# ============================================================
@app.on_callback_query(filters.regex(r"^oappr_(\d+)$"))
async def cb_order_approve(client, cq: CallbackQuery):
    if not has_perm(cq.from_user.id, "approve"):
        return await cq.answer("No perm.", show_alert=True)
    oid = int(cq.matches[0].group(1)); o = get_order(oid)
    if not o: return await cq.answer("Not found.")
    if o["status"] != "pending_approval":
        return await cq.answer(f"Already {o['status']}.")
    update_order(oid, status="approved")
    if o["acc_id"]: mark_sold(o["acc_id"], o["user_id"])
    try:
        await cq.message.edit_caption((cq.message.caption or "") + "\n\n<b>✅ APPROVED</b>",
                                       parse_mode=HTML)
    except Exception: pass
    await deliver(client, oid)


@app.on_callback_query(filters.regex(r"^orej_(\d+)$"))
async def cb_order_reject(client, cq: CallbackQuery):
    if not has_perm(cq.from_user.id, "approve"):
        return await cq.answer("No perm.", show_alert=True)
    oid = int(cq.matches[0].group(1)); o = get_order(oid)
    if not o: return
    update_order(oid, status="rejected")
    try:
        await cq.message.edit_caption((cq.message.caption or "") + "\n\n<b>❌ REJECTED</b>",
                                       parse_mode=HTML)
    except Exception: pass
    try:
        await client.send_message(o["user_id"], f"<b>❌ Order {gen_order_num(oid)} rejected.</b>",
                                   parse_mode=HTML)
    except Exception: pass


@app.on_callback_query(filters.regex(r"^dappr_(\d+)$"))
async def cb_dep_approve(client, cq: CallbackQuery):
    if not has_perm(cq.from_user.id, "approve"):
        return await cq.answer("No perm.", show_alert=True)
    did = int(cq.matches[0].group(1)); d = get_deposit(did)
    if not d or d["status"] != "pending_approval":
        return await cq.answer("Already processed.")
    approver = cq.from_user
    update_deposit(did, status="approved", approved_by=approver.id)
    add_balance(d["user_id"], d["amount"])
    add_deposited(d["user_id"], d["amount"])
    try:
        await cq.message.edit_caption((cq.message.caption or "") + "\n\n<b>✅ APPROVED</b>",
                                       parse_mode=HTML)
    except Exception: pass
    try:
        await client.send_message(d["user_id"],
            f"✅ <b>Deposit approved!</b>\n\n₹{d['amount']} added to balance.\n"
            f"🔖 Ref ID: <code>{d.get('ref_id', '—')}</code>", parse_mode=HTML)
    except Exception: pass

    approver_un = f"@{approver.username}" if approver.username else approver.first_name
    try:
        await notify_log(text=(
            f"✅ <b>Payment Approved!</b>\n\n"
            f"🆔 <b>User:</b> <code>{d['user_id']}</code>\n"
            f"💰 <b>Amount:</b> ₹{d['amount']}\n"
            f"👤 <b>Approved By:</b> {approver_un}"
        ))
    except Exception: pass


@app.on_callback_query(filters.regex(r"^drej_(\d+)$"))
async def cb_dep_reject(client, cq: CallbackQuery):
    if not has_perm(cq.from_user.id, "approve"):
        return await cq.answer("No perm.", show_alert=True)
    did = int(cq.matches[0].group(1)); d = get_deposit(did)
    if not d: return
    update_deposit(did, status="rejected")
    try:
        await cq.message.edit_caption((cq.message.caption or "") + "\n\n<b>❌ REJECTED</b>",
                                       parse_mode=HTML)
    except Exception: pass
    try:
        await client.send_message(d["user_id"], f"<b>❌ Deposit #{did} rejected.</b>", parse_mode=HTML)
    except Exception: pass


# ============================================================
#  DELIVER + OTP LISTENER
# ============================================================
async def deliver(bot, oid):
    o = get_order(oid)
    a = get_account(o["acc_id"]) if o and o["acc_id"] else None
    if not a: return
    u = get_user(o["user_id"]) or {}
    order_num = gen_order_num(oid)

    await start_otp_listener(bot, a, o["user_id"], oid)

    text = (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✅ <b>PURCHASE COMPLETE!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Order Details:</b>\n"
        f"🆔 <b>Order:</b> <code>{order_num}</code>\n"
        f"📱 <b>Phone:</b> <code>{a['phone']}</code>\n"
        f"💰 <b>Paid:</b> ${inr_to_usd(a['price']):.2f} • ₹{a['price']}\n"
        f"💵 <b>Balance:</b> ${inr_to_usd(u.get('balance',0)):.2f} • ₹{u.get('balance',0)}\n\n"
        "⏳ <b>Auto OTP Delivery</b>\n"
        "<b>Please login now!</b>\n"
        "OTP will arrive in ~30 seconds\n\n"
        "Check messages below for OTP..."
    )
    try:
        await bot.send_message(o["user_id"], text, parse_mode=HTML)
    except Exception as e:
        log.warning(f"deliver: {e}")


async def start_otp_listener(bot, acc, buyer_id, oid):
    aid = acc["id"]
    if aid in USER_CLIENTS:
        log.info(f"[listener] already active for acc {aid}")
        return
    try:
        ua = Client(f"usr_{aid}", api_id=int(acc["api_id"]), api_hash=acc["api_hash"],
                    session_string=acc["session"], in_memory=True)
    except Exception as e:
        log.error(f"[listener] client fail {aid}: {e}"); return

    @ua.on_message(filters.private)
    async def otp_handler(cl, msg: Message):
        raw = msg.text or ""
        sender_id = msg.from_user.id if msg.from_user else 0
        log.info(f"[listener acc={aid}] from {sender_id}: {raw[:100]}")
        if sender_id != 777000:
            if "login code" not in raw.lower() and "code:" not in raw.lower():
                return
        code = extract_otp_code(raw)
        if not code: return
        log.info(f"[listener acc={aid}] OTP: {code}")
        log_otp(oid, aid, buyer_id, code, raw)

        twofa = acc.get("twofa") or ""
        txt_user = (
            "✅ <b>Number Acquired!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🌍 <b>Country:</b> {acc.get('flag','')} {acc.get('country_name','')}\n"
            f"📞 <b>Number:</b> <code>{acc['phone']}</code>\n\n"
            f"💬 <b>OTP Code:</b> <code>{code}</code>\n"
        )
        if twofa:
            txt_user += f"🔐 <b>2FA Password:</b> <code>{twofa}</code>\n"
        else:
            txt_user += f"🔐 <b>2FA Password:</b> <i>Not set</i>\n"
        txt_user += "\n✅ <b>Thank you for purchasing!</b>"
        kb_user = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔔 Request New OTP", callback_data=f"reotp_{aid}_{oid}")],
            [InlineKeyboardButton("🏠 Back to Home", callback_data="back_home")]])
        try:
            await bot.send_message(buyer_id, txt_user, reply_markup=kb_user, parse_mode=HTML)
        except Exception as e:
            log.warning(f"otp send: {e}")

        try:
            log_txt = (
                "🛍️ <b>New Number Purchased</b>\n\n"
                f"🌍 <b>Country :</b> {acc.get('country_name','')} {acc.get('flag','')}\n"
                f"📅 <b>Year :</b> {acc.get('year','')}\n"
                f"📦 <b>Qty :</b> 1\n\n"
                f"📱 <b>Number :</b> <code>{mask_phone(acc['phone'])}</code>\n"
                f"🔑 <b>OTP :</b> <code>{mask_otp(code)}</code>\n"
                f"🔒 <b>2FA :</b> {'Enabled' if twofa else 'Disabled'}\n\n"
                f"💰 <b>Price :</b> ₹{acc.get('price','')}\n"
                f"🕐 <b>Time :</b> {now_str()}\n\n"
                f"🆔 <b>User ID :</b> <code>{buyer_id}</code>\n\n"
                f"✦ @{BOT_USERNAME} ✦"
            )
            await notify_log(text=log_txt)
        except Exception as e:
            log.warning(f"log send: {e}")

    try:
        await ua.start()
        USER_CLIENTS[aid] = ua
        log.info(f"[listener] ACTIVE acc {aid} phone {acc['phone']}")
    except (SessionRevoked, AuthKeyUnregistered) as e:
        log.error(f"[listener] DEAD {aid}: {e}"); set_health(aid, "dead")
    except Exception as e:
        log.error(f"[listener] FAIL {aid}: {e}")


# ============================================================
#  ADMIN PANEL
# ============================================================
@app.on_message(filters.command("admin") & filters.private)
async def cmd_admin(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    await msg.reply("💻 <b>ADVANCED ADMIN DASHBOARD</b>", reply_markup=admin_kb(), parse_mode=HTML)


@app.on_callback_query(filters.regex("^a_panel$"))
async def cb_panel(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id):
        return await cq.answer("Not admin.")
    await safe_edit(cq, "💻 <b>ADVANCED ADMIN DASHBOARD</b>", admin_kb())


@app.on_callback_query(filters.regex("^a_status$"))
async def cb_a_status(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    cur = get_setting("bot_status", "on"); new = "off" if cur == "on" else "on"
    set_setting("bot_status", new)
    await cq.answer(f"Bot {new.upper()}")
    await safe_edit(cq, f"🟢 <b>Bot status:</b> {new}", admin_back())


@app.on_callback_query(filters.regex("^a_stats$"))
async def cb_a_stats(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    with closing(db()) as c:
        q = lambda s: c.execute(s).fetchone()
        total = q("SELECT COUNT(*) n FROM accounts")["n"]
        avail = q("SELECT COUNT(*) n FROM accounts WHERE status='available'")["n"]
        sold  = q("SELECT COUNT(*) n FROM accounts WHERE status='sold'")["n"]
        pend  = q("SELECT COUNT(*) n FROM orders WHERE status='pending_approval'")["n"]
        dpend = q("SELECT COUNT(*) n FROM deposits WHERE status='pending_approval'")["n"]
        ords  = q("SELECT COUNT(*) n FROM orders")["n"]
        users = q("SELECT COUNT(*) n FROM users")["n"]
        refs  = q("SELECT COUNT(*) n FROM referrals WHERE valid=1")["n"]
        rev   = q("SELECT COALESCE(SUM(price),0) s FROM orders WHERE status='approved'")["s"]
        bal   = q("SELECT COALESCE(SUM(balance),0) s FROM users")["s"]
    await safe_edit(cq,
        f"📈 <b>STATISTICS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 <b>Accounts:</b> {total} (avail <b>{avail}</b> · sold <b>{sold}</b>)\n"
        f"🧾 <b>Orders:</b> {ords} (pending <b>{pend}</b>)\n"
        f"💳 <b>Deposits pending:</b> {dpend}\n"
        f"👥 <b>Users:</b> {users}\n"
        f"🎁 <b>Valid referrals:</b> {refs}\n"
        f"💰 <b>Revenue:</b> ₹{rev}\n"
        f"💼 <b>Balances:</b> ₹{bal}", admin_back())


@app.on_callback_query(filters.regex("^a_stock$"))
async def cb_a_stock(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    accs = all_accounts()
    if not accs:
        return await safe_edit(cq, "<b>📦 No stock.</b>", admin_back())
    lines = ["📦 <b>STOCK</b>", ""]
    for a in accs[:30]:
        lines.append(f"<code>{a['id']}</code> | {a['flag'] or ''} {a['country_name']} {a['year']} | "
                     f"₹{a['price']} | {a['status']} | {a['health']}")
    lines += ["", "Delete: <code>/delacc ID</code>"]
    await safe_edit(cq, "\n".join(lines), admin_back())


@app.on_message(filters.command("delacc") & filters.private)
async def cmd_delacc(client, msg: Message):
    if not has_perm(msg.from_user.id, "add_acc"): return
    p = msg.text.split()
    if len(p) < 2:
        return await msg.reply("<code>/delacc ID</code>", parse_mode=HTML)
    delete_account(int(p[1]))
    await msg.reply(f"<b>🗑 Deleted {p[1]}</b>", parse_mode=HTML)


@app.on_callback_query(filters.regex("^a_addacc$"))
async def cb_a_addacc(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    old = LOGIN_SESSIONS.pop(cq.from_user.id, None)
    if old and old.get("client"):
        try: await old["client"].disconnect()
        except Exception: pass
    ADD_STATE[cq.from_user.id] = {"step": "phone", "data": {}}
    await safe_edit(cq,
        "📱 <b>Enter Phone Number</b> (+919999...):\n\n"
        "Bot us number pe OTP bhejega.\n\n<i>(Type /cancel to abort)</i>",
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="a_panel")]]))


@app.on_message(filters.command("cancel") & filters.private)
async def cmd_cancel(client, msg: Message):
    uid = msg.from_user.id
    ls = LOGIN_SESSIONS.pop(uid, None)
    if ls and ls.get("client"):
        try: await ls["client"].disconnect()
        except Exception: pass
    ADD_STATE.pop(uid, None); PENDING_DEPOSIT.pop(uid, None); PENDING_PAYMENT.pop(uid, None)
    BANNER_STATE.pop(uid, None); SETBAL_STATE.pop(uid, None)
    SETRATE_STATE.discard(uid); SETWELCOME_STATE.discard(uid)
    ADDPAY_STATE.pop(uid, None); ADDFSUB_STATE.pop(uid, None)
    ADDGIVE_STATE.pop(uid, None)
    if is_admin(uid):
        await msg.reply("<b>Cancelled.</b>", reply_markup=admin_kb(), parse_mode=HTML)
    else:
        await msg.reply("<b>Cancelled.</b>", reply_markup=main_menu(uid), parse_mode=HTML)


async def _begin_phone_login(admin_id, phone):
    try:
        tmp = Client(f"login_{admin_id}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        await tmp.connect()
        sent = await tmp.send_code(phone)
        LOGIN_SESSIONS[admin_id] = {"client": tmp, "phone": phone,
                                     "phone_code_hash": sent.phone_code_hash}
        return True, None
    except PhoneNumberInvalid: return False, "Phone number invalid."
    except PhoneNumberBanned: return False, "Ye number banned hai."
    except FloodWait as e: return False, f"Flood wait {e.value}s."
    except Exception as e: return False, f"Error: {e}"


@app.on_callback_query(filters.regex("^a_paymethods$"))
async def cb_a_paymethods(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    methods = list_payment_methods(only_enabled=False)
    lines = ["💳 <b>PAYMENT METHODS</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", ""]
    if not methods: lines.append("<i>No methods yet.</i>")
    for m in methods:
        status = "🟢" if m["enabled"] else "🔴"
        lines.append(f"{status} <b>{m['id']}.</b> {m['emoji']} <b>{m['name']}</b>\n"
                     f"    <code>{m['address']}</code>")
    lines += ["", "Add: <code>/addpay Name type address</code>",
              "Delete: <code>/delpay ID</code>",
              "Toggle: <code>/togglepay ID</code>"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Method", callback_data="a_addpay")],
        [InlineKeyboardButton("⬅ Back", callback_data="a_panel")]])
    await safe_edit(cq, "\n".join(lines), kb)


@app.on_callback_query(filters.regex("^a_addpay$"))
async def cb_a_addpay(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    ADDPAY_STATE[cq.from_user.id] = {"step": "name"}
    await safe_edit(cq, "💳 <b>New payment method</b>\n\n<b>Step 1/3:</b> Name bhejo:",
                    admin_back())


@app.on_message(filters.command("addpay") & filters.private)
async def cmd_addpay(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split(maxsplit=3)
    if len(p) < 4:
        return await msg.reply("<code>/addpay Name type address</code>", parse_mode=HTML)
    _, name, ptype, address = p
    pid = add_payment_method(name, "💳", ptype, address)
    await msg.reply(f"<b>✅ Payment method #{pid} added</b>\n{name}\n<code>{address}</code>",
                    parse_mode=HTML)


@app.on_message(filters.command("delpay") & filters.private)
async def cmd_delpay(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split()
    if len(p) < 2: return
    del_payment_method(int(p[1]))
    await msg.reply(f"<b>🗑 Deleted #{p[1]}</b>", parse_mode=HTML)


@app.on_message(filters.command("togglepay") & filters.private)
async def cmd_togglepay(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split()
    if len(p) < 2: return
    toggle_payment_method(int(p[1]))
    await msg.reply(f"<b>🔄 Toggled #{p[1]}</b>", parse_mode=HTML)


@app.on_callback_query(filters.regex("^a_logchannel$"))
async def cb_a_logchannel(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    lc = log_channel_id()
    await safe_edit(cq,
        f"📢 <b>LOG CHANNEL</b>\n\nCurrent: <code>{lc if lc else 'not set'}</code>\n\n"
        f"Set: <code>/set log_channel -100XXXXXXXXX</code>", admin_back())


@app.on_callback_query(filters.regex("^a_fsub$"))
async def cb_a_fsub(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    chans = list_force_channels(only_enabled=False)
    lines = ["🔗 <b>FORCE CHANNELS</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", ""]
    if not chans:
        lines.append("<i>Koi channel nahi. Add karo niche se.</i>")
    for ch in chans:
        st = "🟢" if ch["enabled"] else "🔴"
        lines.append(f"{st} <b>#{ch['id']}</b> {ch['title'] or ch['chat_id']}\n"
                     f"  <code>{ch['chat_id']}</code>\n"
                     f"  Link: {ch.get('invite_link') or '—'}")
    lines += ["", "Add: <code>/addfsub CHAT_ID Title invite_link</code>",
              "Delete: <code>/delfsub ID</code>",
              "Toggle: <code>/togglefsub ID</code>"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Channel", callback_data="a_addfsub")],
        [InlineKeyboardButton("⬅ Back", callback_data="a_panel")]])
    await safe_edit(cq, "\n".join(lines), kb)


@app.on_callback_query(filters.regex("^a_addfsub$"))
async def cb_a_addfsub(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    ADDFSUB_STATE[cq.from_user.id] = {"step": "chat_id"}
    await safe_edit(cq, "🔗 <b>Add Force Channel</b>\n\n<b>Step 1/4:</b> Chat ID bhejo "
                          "(e.g. <code>-1001234567890</code> or <code>@channelname</code>):",
                    admin_back())


@app.on_message(filters.command("addfsub") & filters.private)
async def cmd_addfsub(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split(maxsplit=3)
    if len(p) < 2:
        return await msg.reply("<code>/addfsub CHAT_ID Title invite_link</code>", parse_mode=HTML)
    chat_id = p[1]
    title = p[2] if len(p) > 2 else ""
    link = p[3] if len(p) > 3 else ""
    is_priv = 1 if chat_id.startswith("-100") else 0
    cid = add_force_channel(chat_id, title, link, is_priv)
    await msg.reply(f"<b>✅ Force channel #{cid} added</b>", parse_mode=HTML)


@app.on_message(filters.command("delfsub") & filters.private)
async def cmd_delfsub(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split()
    if len(p) < 2: return
    del_force_channel(int(p[1]))
    await msg.reply(f"<b>🗑 Deleted #{p[1]}</b>", parse_mode=HTML)


@app.on_message(filters.command("togglefsub") & filters.private)
async def cmd_togglefsub(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split()
    if len(p) < 2: return
    toggle_force_channel(int(p[1]))
    await msg.reply(f"<b>🔄 Toggled #{p[1]}</b>", parse_mode=HTML)


@app.on_callback_query(filters.regex("^a_giveaway$"))
async def cb_a_giveaway(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    gws = list_giveaways(only_enabled=False)
    lines = ["🎁 <b>WEEKLY GIVEAWAY</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", ""]
    lines.append("<i>Weekly leaderboard ke top winners ko yeh prizes milenge (Monday 00:00 UTC pe auto-announce).</i>")
    lines.append("")
    if not gws:
        lines.append("<b>No prizes set.</b>")
    for g in gws:
        st = "🟢" if g["enabled"] else "🔴"
        lines.append(f"{st} <b>Rank #{g['rank']}:</b> {g['prize']}")
    lines += ["", "Add: <code>/addgive rank prize_text</code>",
              "Edit: <code>/editgive ID new_prize</code>",
              "Delete: <code>/delgive ID</code>"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Prize", callback_data="a_addgive")],
        [InlineKeyboardButton("🎉 Pick Winners Now", callback_data="a_pickwinners")],
        [InlineKeyboardButton("📜 Past Winners", callback_data="a_pastwinners")],
        [InlineKeyboardButton("⬅ Back", callback_data="a_panel")]])
    await safe_edit(cq, "\n".join(lines), kb)


@app.on_callback_query(filters.regex("^a_addgive$"))
async def cb_a_addgive(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    ADDGIVE_STATE[cq.from_user.id] = {"step": "rank"}
    await safe_edit(cq, "🎁 <b>Add Prize</b>\n\n<b>Rank number bhejo</b> (1, 2, 3...):",
                    admin_back())


@app.on_message(filters.command("addgive") & filters.private)
async def cmd_addgive(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split(maxsplit=2)
    if len(p) < 3:
        return await msg.reply("<code>/addgive rank prize_text</code>", parse_mode=HTML)
    try: rank = int(p[1])
    except: return await msg.reply("<b>Rank number daalo.</b>", parse_mode=HTML)
    add_giveaway(rank, p[2])
    await msg.reply(f"<b>✅ Prize added for rank #{rank}</b>", parse_mode=HTML)


@app.on_message(filters.command("editgive") & filters.private)
async def cmd_editgive(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split(maxsplit=2)
    if len(p) < 3:
        return await msg.reply("<code>/editgive ID new_prize</code>", parse_mode=HTML)
    update_giveaway(int(p[1]), p[2])
    await msg.reply(f"<b>✅ Updated #{p[1]}</b>", parse_mode=HTML)


@app.on_message(filters.command("delgive") & filters.private)
async def cmd_delgive(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split()
    if len(p) < 2: return
    del_giveaway(int(p[1]))
    await msg.reply(f"<b>🗑 Deleted #{p[1]}</b>", parse_mode=HTML)


@app.on_callback_query(filters.regex("^a_pickwinners$"))
async def cb_a_pickwinners(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    await cq.answer("Picking winners...")
    board = referral_leaderboard("week", 10)
    gws = list_giveaways(only_enabled=True)
    if not board or not gws:
        return await cq.message.reply("⚠️ <b>No leaderboard data or no prizes set.</b>",
                                       parse_mode=HTML)
    week_start = week_start_iso()
    with closing(db()) as c:
        c.execute("DELETE FROM giveaway_winners WHERE week_start=?", (week_start,)); c.commit()
    announce_lines = ["🎉 <b>WEEKLY GIVEAWAY WINNERS</b>",
                      "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", ""]
    for gw in gws:
        idx = gw["rank"] - 1
        if idx < 0 or idx >= len(board): continue
        winner = board[idx]
        uname = f"@{winner['username']}" if winner.get("username") else (winner.get("first_name") or "user")
        save_winner(week_start, gw["rank"], winner["uid"], winner.get("username", ""),
                    gw["prize"], winner["valid"])
        medal = ["🥇", "🥈", "🥉"][gw["rank"]-1] if gw["rank"] <= 3 else f"{gw['rank']}️⃣"
        announce_lines.append(f"{medal} <b>{uname}</b>")
        announce_lines.append(f"   ✅ {winner['valid']} valid")
        announce_lines.append(f"   🎁 Prize: <b>{gw['prize']}</b>")
        announce_lines.append("")
        try:
            await client.send_message(
                winner["uid"],
                f"🎉 <b>Congratulations!</b>\n\n"
                f"You are rank #{gw['rank']} in this week's referral leaderboard!\n"
                f"🎁 <b>Prize:</b> {gw['prize']}\n\n"
                f"Contact admin to claim: @{get_setting('admin_username', ADMIN_USERNAME)}",
                parse_mode=HTML)
        except Exception: pass
    announce_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    txt = "\n".join(announce_lines)
    await cq.message.reply(txt, parse_mode=HTML)
    lc = log_channel_id()
    if lc:
        try: await app.send_message(lc, txt, parse_mode=HTML)
        except Exception: pass


@app.on_callback_query(filters.regex("^a_pastwinners$"))
async def cb_a_pastwinners(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    with closing(db()) as c:
        weeks = [r["week_start"] for r in c.execute(
            "SELECT DISTINCT week_start FROM giveaway_winners ORDER BY week_start DESC LIMIT 5"
        ).fetchall()]
    if not weeks:
        return await safe_edit(cq, "<b>📜 No past winners yet.</b>", admin_back())
    lines = ["📜 <b>PAST WINNERS</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for wk in weeks:
        lines.append(f"\n<b>Week of {wk[:10]}:</b>")
        for w in winners_for_week(wk):
            uname = f"@{w['username']}" if w.get("username") else f"User{w['user_id']}"
            lines.append(f"  {w['rank']}️⃣ {uname} — {w['prize']} ({w['valid_count']} valid)")
    await safe_edit(cq, "\n".join(lines), admin_back())


@app.on_callback_query(filters.regex("^a_rate$"))
async def cb_a_rate(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    SETRATE_STATE.add(cq.from_user.id)
    await safe_edit(cq, f"💱 <b>Current: 1 USDT = ₹{usdt_rate()}</b>\n\nNaya rate bhejo:", admin_back())


@app.on_callback_query(filters.regex("^a_addprice$"))
async def cb_a_addprice(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    await safe_edit(cq, "💵 Send: <code>/setprice ID price</code>", admin_back())


@app.on_message(filters.command("setprice") & filters.private)
async def cmd_setprice(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split()
    if len(p) < 3:
        return await msg.reply("<code>/setprice ID price</code>", parse_mode=HTML)
    with closing(db()) as c:
        c.execute("UPDATE accounts SET price=? WHERE id=?", (int(p[2]), int(p[1]))); c.commit()
    await msg.reply(f"<b>✅ Price #{p[1]} = ₹{p[2]}</b>", parse_mode=HTML)


@app.on_callback_query(filters.regex("^a_broadcast$"))
async def cb_a_broadcast(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    BROADCAST_STATE.add(cq.from_user.id)
    await safe_edit(cq, "📢 <b>Broadcast text bhejo:</b>", admin_back())


@app.on_callback_query(filters.regex("^a_userinfo$"))
async def cb_a_userinfo(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    SEARCH_STATE.add(cq.from_user.id)
    await safe_edit(cq, "🔎 <b>User ID bhejo:</b>", admin_back())


@app.on_callback_query(filters.regex("^a_search$"))
async def cb_a_search(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    SEARCH_STATE.add(cq.from_user.id)
    await safe_edit(cq, "🔍 <b>Search user:</b>", admin_back())


@app.on_callback_query(filters.regex("^a_setbal$"))
async def cb_a_setbal(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    await safe_edit(cq, "💼 Send: <code>/setbal user_id</code>", admin_back())


@app.on_message(filters.command("setbal") & filters.private)
async def cmd_setbal(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split()
    if len(p) < 2: return
    SETBAL_STATE[msg.from_user.id] = int(p[1])
    await msg.reply(f"<b>Amount bhejo for {p[1]}</b>:", parse_mode=HTML)


@app.on_callback_query(filters.regex("^a_ban$"))
async def cb_a_ban(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    await safe_edit(cq, "🚫 <code>/ban user_id</code>\n<code>/unban user_id</code>", admin_back())


@app.on_message(filters.command("ban") & filters.private)
async def cmd_ban(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split()
    if len(p) < 2: return
    ban_user(int(p[1]), True)
    await msg.reply(f"<b>🚫 Banned {p[1]}</b>", parse_mode=HTML)


@app.on_message(filters.command("unban") & filters.private)
async def cmd_unban(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split()
    if len(p) < 2: return
    ban_user(int(p[1]), False)
    await msg.reply(f"<b>✅ Unbanned {p[1]}</b>", parse_mode=HTML)


@app.on_callback_query(filters.regex("^a_payments$"))
async def cb_a_payments(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    orders = pending_orders(); deposits = pending_deposits()
    lines = ["💳 <b>PENDING PAYMENTS</b>", ""]
    if orders:
        lines.append("<b>Orders:</b>")
        for o in orders: lines.append(f"#{o['id']} {o['user_id']} ₹{o['price']}")
    if deposits:
        lines.append(""); lines.append("<b>Deposits:</b>")
        for d in deposits: lines.append(f"#{d['id']} {d['user_id']} ₹{d['amount']}")
    if not orders and not deposits: lines.append("<i>none</i>")
    await safe_edit(cq, "\n".join(lines), admin_back())


@app.on_callback_query(filters.regex("^a_support$"))
async def cb_a_support(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    tickets = open_tickets()
    if not tickets:
        return await safe_edit(cq, "<b>💬 No open tickets.</b>", admin_back())
    lines = ["💬 <b>OPEN TICKETS</b>", ""]
    for t in tickets[:20]:
        lines.append(f"#{t['id']} · user {t['user_id']} · {t['updated_at']}")
    lines += ["", "Reply: <code>/reply TID message</code>"]
    await safe_edit(cq, "\n".join(lines), admin_back())


@app.on_message(filters.command("reply") & filters.private)
async def cmd_reply(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split(maxsplit=2)
    if len(p) < 3:
        return await msg.reply("<code>/reply TID message</code>", parse_mode=HTML)
    tid = int(p[1]); text = p[2]
    add_ticket_msg(tid, "admin", text)
    target = ticket_owner(tid)
    if target:
        try:
            await client.send_message(target, f"📞 <b>Support reply:</b>\n\n{text}", parse_mode=HTML)
        except Exception: pass
    await msg.reply("<b>✅ Sent.</b>", parse_mode=HTML)


@app.on_callback_query(filters.regex("^a_welcome$"))
async def cb_a_welcome(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    SETWELCOME_STATE.add(cq.from_user.id)
    await safe_edit(cq, f"✏ <b>Naya welcome bhejo:</b>", admin_back())


@app.on_callback_query(filters.regex("^a_banner$"))
async def cb_a_banner(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Welcome Banner", callback_data="a_banset_welcome")],
        [InlineKeyboardButton("Shop Banner", callback_data="a_banset_shop")],
        [InlineKeyboardButton("Support Banner", callback_data="a_banset_support")],
        [InlineKeyboardButton("Deposit Banner", callback_data="a_banset_deposit")],
        [InlineKeyboardButton("Profile Banner", callback_data="a_banset_profile")],
        [InlineKeyboardButton("Refer Banner", callback_data="a_banset_refer")],
        [InlineKeyboardButton("⬅ Back", callback_data="a_panel")]])
    await safe_edit(cq, "🖼 <b>Banner slot select karo:</b>", kb)


@app.on_callback_query(filters.regex(r"^a_banset_(\w+)$"))
async def cb_banset(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    slot = cq.matches[0].group(1)
    BANNER_STATE[cq.from_user.id] = slot
    await safe_edit(cq, f"📷 <b>Send image for slot:</b> <code>{slot}</code>", admin_back())


@app.on_callback_query(filters.regex("^a_backup$"))
async def cb_a_backup(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    await cq.answer("Building...")
    await send_backup(client)


@app.on_callback_query(filters.regex("^a_admins$"))
async def cb_a_admins(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    admins = list_admins()
    lines = ["👥 <b>MANAGE SUB-ADMINS</b>", ""]
    for a in admins:
        lines.append(f"<code>{a['user_id']}</code> — {a['name']} — <code>{a['perms']}</code>")
    if not admins: lines.append("<i>none</i>")
    lines += ["", "Add: <code>/addadmin ID perms|all</code>",
              "Remove: <code>/deladmin ID</code>"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Admin", callback_data="a_addadmin")],
        [InlineKeyboardButton("⬅ Back", callback_data="a_panel")]])
    await safe_edit(cq, "\n".join(lines), kb)


@app.on_callback_query(filters.regex("^a_addadmin$"))
async def cb_a_addadmin(client, cq: CallbackQuery):
    if not is_admin(cq.from_user.id): return
    ADDADMIN_STATE[cq.from_user.id] = {"step": "id"}
    await safe_edit(cq, "👤 <b>New admin ka user ID bhejo:</b>", admin_back())


@app.on_message(filters.command("addadmin") & filters.private)
async def cmd_addadmin(client, msg: Message):
    if not has_perm(msg.from_user.id, "add_admin"): return
    p = msg.text.split(maxsplit=2)
    if len(p) < 3:
        return await msg.reply("<code>/addadmin ID perms|all</code>", parse_mode=HTML)
    add_admin(int(p[1]), f"admin_{p[1]}", p[2], msg.from_user.id)
    await msg.reply(f"<b>✅ Admin {p[1]} added:</b> {p[2]}", parse_mode=HTML)


@app.on_message(filters.command("deladmin") & filters.private)
async def cmd_deladmin(client, msg: Message):
    if not has_perm(msg.from_user.id, "add_admin"): return
    p = msg.text.split()
    if len(p) < 2: return
    if int(p[1]) == owner_id():
        return await msg.reply("<b>Owner remove nahi.</b>", parse_mode=HTML)
    del_admin(int(p[1]))
    await msg.reply(f"<b>🗑 Removed {p[1]}</b>", parse_mode=HTML)


@app.on_message(filters.command("set") & filters.private)
async def cmd_set(client, msg: Message):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split(maxsplit=2)
    if len(p) < 3:
        return await msg.reply("<code>/set key value</code>", parse_mode=HTML)
    k, v = p[1], p[2]
    set_setting(k, v)
    await msg.reply(f"<b>✅ {k}</b> = <code>{v}</code>", parse_mode=HTML)


# ============================================================
#  TEXT ROUTER
# ============================================================
@app.on_message(filters.private & filters.text)
async def text_router(client, msg: Message):
    if msg.text and msg.text.startswith("/"): return
    uid = msg.from_user.id
    txt = msg.text.strip()

    if uid in CLAIM_STATE:
        if txt.upper() == "YES OWNER":
            CLAIM_STATE.discard(uid)
            set_setting("owner_id", uid)
            with closing(db()) as c:
                c.execute("""INSERT INTO admins(user_id,name,perms,added_by,added_at)
                             VALUES(?,?,?,?,?)
                             ON CONFLICT(user_id) DO UPDATE SET perms='all'""",
                          (uid, "Owner", "all", uid, now())); c.commit()
            return await msg.reply("<b>👑 Owner set.</b>", reply_markup=main_menu(uid), parse_mode=HTML)
        else:
            return await msg.reply("<i>Send YES OWNER to confirm.</i>", parse_mode=HTML)

    if txt in MAIN_BTN_HANDLERS:
        return await dispatch_main_btn(client, msg, MAIN_BTN_HANDLERS[txt])

    st = ADDPAY_STATE.get(uid)
    if st:
        s = st["step"]
        if s == "name":
            st["name"] = txt; st["step"] = "type"
            return await msg.reply("<b>Step 2/3:</b> Type: <code>upi</code> · "
                                    "<code>usdt_bep20</code> · <code>usdt_trc20</code> · "
                                    "<code>custom</code>", parse_mode=HTML)
        if s == "type":
            if txt not in ("upi", "usdt_bep20", "usdt_trc20", "custom"):
                return await msg.reply("<b>Invalid.</b>", parse_mode=HTML)
            st["type"] = txt; st["step"] = "address"
            return await msg.reply("<b>Step 3/3:</b> Address bhejo:", parse_mode=HTML)
        if s == "address":
            st["address"] = txt
            pid = add_payment_method(st["name"], "💳", st["type"], st["address"])
            ADDPAY_STATE.pop(uid, None)
            return await msg.reply(f"<b>✅ Method #{pid} added.</b>",
                                    reply_markup=admin_kb(), parse_mode=HTML)

    st = ADDFSUB_STATE.get(uid)
    if st:
        s = st["step"]
        if s == "chat_id":
            st["chat_id"] = txt; st["step"] = "title"
            return await msg.reply("<b>Step 2/4:</b> Channel ka title bhejo:", parse_mode=HTML)
        if s == "title":
            st["title"] = txt; st["step"] = "link"
            return await msg.reply("<b>Step 3/4:</b> Invite link bhejo (ya <code>skip</code>):",
                                    parse_mode=HTML)
        if s == "link":
            st["link"] = "" if txt.lower() == "skip" else txt
            st["step"] = "confirm"
            return await msg.reply(
                f"<b>Confirm:</b>\nChat ID: <code>{st['chat_id']}</code>\n"
                f"Title: {st['title']}\nLink: {st['link'] or '—'}\n\n"
                f"Type <code>yes</code> to save:",
                parse_mode=HTML)
        if s == "confirm":
            if txt.lower() != "yes":
                ADDFSUB_STATE.pop(uid, None)
                return await msg.reply("<b>Cancelled.</b>", reply_markup=admin_kb(), parse_mode=HTML)
            is_priv = 1 if st["chat_id"].startswith("-100") else 0
            cid = add_force_channel(st["chat_id"], st["title"], st["link"], is_priv)
            ADDFSUB_STATE.pop(uid, None)
            return await msg.reply(f"<b>✅ Force channel #{cid} added</b>",
                                    reply_markup=admin_kb(), parse_mode=HTML)

    st = ADDGIVE_STATE.get(uid)
    if st:
        s = st["step"]
        if s == "rank":
            try: st["rank"] = int(txt)
            except:
                return await msg.reply("<b>Number daalo.</b>", parse_mode=HTML)
            st["step"] = "prize"
            return await msg.reply("<b>Prize text bhejo</b> (e.g. <code>2 Indian Telegram Account</code>):",
                                    parse_mode=HTML)
        if s == "prize":
            add_giveaway(st["rank"], txt)
            ADDGIVE_STATE.pop(uid, None)
            return await msg.reply(f"<b>✅ Prize for rank #{st['rank']} added</b>",
                                    reply_markup=admin_kb(), parse_mode=HTML)

    dep = PENDING_DEPOSIT.get(uid)
    if dep and dep.get("awaiting_amount"):
        try: amt = int(txt)
        except ValueError:
            return await msg.reply("<b>Number daalo.</b>", parse_mode=HTML)
        return await deposit_amount_received(client, msg, amt)

    ls = LOGIN_SESSIONS.get(uid)
    if ls:
        if ls.get("awaiting_otp"):
            code = txt.replace(" ", "").replace("-", "")
            try:
                await ls["client"].sign_in(ls["phone"], ls["phone_code_hash"], code)
                session_str = await ls["client"].export_session_string()
                ls["session"] = session_str; ls["awaiting_otp"] = False
                ADD_STATE[uid]["step"] = "country"
                return await msg.reply("<b>✅ Signed in.</b>\n\nAb country name bhejo:", parse_mode=HTML)
            except SessionPasswordNeeded:
                ls["awaiting_otp"] = False; ls["awaiting_2fa"] = True
                return await msg.reply("🔐 <b>2FA required. Enter password:</b>", parse_mode=HTML)
            except PhoneCodeInvalid:
                return await msg.reply("<b>OTP galat.</b>", parse_mode=HTML)
            except PhoneCodeExpired:
                LOGIN_SESSIONS.pop(uid, None); ADD_STATE.pop(uid, None)
                try: await ls["client"].disconnect()
                except Exception: pass
                return await msg.reply("<b>OTP expire.</b> /cancel karke dobara.", parse_mode=HTML)
            except Exception as e:
                return await msg.reply(f"<b>Sign-in fail:</b> {e}", parse_mode=HTML)
        if ls.get("awaiting_2fa"):
            try:
                await ls["client"].check_password(txt)
                session_str = await ls["client"].export_session_string()
                ls["session"] = session_str; ls["twofa"] = txt; ls["awaiting_2fa"] = False
                ADD_STATE[uid]["step"] = "country"
                return await msg.reply("<b>✅ 2FA verified.</b>\n\nAb country name bhejo:",
                                        parse_mode=HTML)
            except Exception as e:
                return await msg.reply(f"<b>2FA fail:</b> {e}", parse_mode=HTML)

    if uid in SETRATE_STATE:
        SETRATE_STATE.discard(uid)
        try: r = float(txt)
        except ValueError:
            return await msg.reply("<b>Number daalo.</b>", reply_markup=admin_back(), parse_mode=HTML)
        set_setting("usdt_rate", r)
        return await msg.reply(f"<b>💱 USDT rate set: ₹{r}</b>",
                                reply_markup=admin_kb(), parse_mode=HTML)

    if uid in SETWELCOME_STATE:
        SETWELCOME_STATE.discard(uid)
        set_setting("welcome_msg", txt)
        return await msg.reply("<b>✅ Welcome msg updated.</b>",
                                reply_markup=admin_kb(), parse_mode=HTML)

    st = ADD_STATE.get(uid)
    if st:
        s, d = st["step"], st["data"]
        if s == "phone":
            phone = txt; d["phone"] = phone
            ok, err = await _begin_phone_login(uid, phone)
            if not ok:
                ADD_STATE.pop(uid, None)
                return await msg.reply(f"<b>❌ {err}</b>", reply_markup=admin_kb(), parse_mode=HTML)
            ls = LOGIN_SESSIONS.get(uid)
            if ls: ls["awaiting_otp"] = True
            st["step"] = "otp"
            return await msg.reply(f"🔢 <b>Enter OTP sent to</b> <code>{phone}</code>:",
                                    parse_mode=HTML)
        if s == "otp":
            return await msg.reply("<i>OTP login issue. /cancel karo.</i>", parse_mode=HTML)
        if s == "country":
            d["country_name"] = txt; st["step"] = "flag"
            return await msg.reply("🏳 <b>Flag emoji</b> or <code>skip</code>:", parse_mode=HTML)
        if s == "flag":
            d["flag"] = "" if txt.lower() == "skip" else txt; st["step"] = "year"
            return await msg.reply("📅 <b>Year</b>:", parse_mode=HTML)
        if s == "year":
            try: d["year"] = int(txt)
            except ValueError:
                return await msg.reply("<b>Number daalo.</b>", parse_mode=HTML)
            st["step"] = "price"
            return await msg.reply("💰 <b>Price in ₹</b>:", parse_mode=HTML)
        if s == "price":
            try: d["price"] = int(txt)
            except ValueError:
                return await msg.reply("<b>Number daalo.</b>", parse_mode=HTML)
            ls = LOGIN_SESSIONS.get(uid)
            if not ls or not ls.get("session"):
                ADD_STATE.pop(uid, None)
                return await msg.reply("<b>Session missing.</b> /cancel karo.",
                                        reply_markup=admin_kb(), parse_mode=HTML)
            twofa = ls.get("twofa", ""); session_str = ls["session"]
            try: await ls["client"].disconnect()
            except Exception: pass
            LOGIN_SESSIONS.pop(uid, None)
            try:
                aid = add_account(d["phone"], API_ID, API_HASH, session_str,
                                  twofa, "", d["country_name"], d["flag"],
                                  d["year"], d["price"])
                ADD_STATE.pop(uid, None)
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Add Another", callback_data="a_addacc")],
                    [InlineKeyboardButton("✅ Done", callback_data="a_panel")]])
                return await msg.reply(
                    f"<b>✅ Account Added!</b>\n\n"
                    f"📱 <b>Phone:</b> <code>{d['phone']}</code>\n"
                    f"🏳 <b>Country:</b> {d['flag']} {d['country_name']}\n"
                    f"📅 <b>Year:</b> {d['year']}\n"
                    f"💰 <b>Price:</b> ₹{d['price']}\n"
                    f"🔐 <b>2FA:</b> {'Set' if twofa else 'None'}",
                    reply_markup=kb, parse_mode=HTML)
            except sqlite3.IntegrityError:
                ADD_STATE.pop(uid, None)
                return await msg.reply("<b>Phone already exists.</b>",
                                        reply_markup=admin_kb(), parse_mode=HTML)

    if uid in BROADCAST_STATE:
        BROADCAST_STATE.discard(uid)
        users = [u["user_id"] for u in all_users()]
        sent = 0
        for u in users:
            try:
                await client.send_message(u, txt, parse_mode=HTML); sent += 1
                await asyncio.sleep(0.05)
            except Exception: pass
        return await msg.reply(f"<b>✅ Sent to {sent}.</b>",
                                reply_markup=admin_kb(), parse_mode=HTML)

    if uid in SEARCH_STATE:
        SEARCH_STATE.discard(uid)
        results = search_users(txt)
        if not results:
            return await msg.reply("<i>No users.</i>", reply_markup=admin_back(), parse_mode=HTML)
        lines = ["<b>Search results:</b>", ""]
        for r in results:
            lines.append(f"<code>{r['user_id']}</code> | @{r['username'] or '—'} | "
                         f"{r['first_name']} | ₹{r['balance']}")
        return await msg.reply("\n".join(lines), reply_markup=admin_back(), parse_mode=HTML)

    if uid in SETBAL_STATE:
        target = SETBAL_STATE.pop(uid)
        try: amt = int(txt)
        except ValueError:
            return await msg.reply("<b>Number daalo.</b>", reply_markup=admin_back(), parse_mode=HTML)
        if amt >= 0: add_balance(target, amt)
        else: deduct_balance(target, -amt)
        await msg.reply(f"<b>✅ Balance updated for</b> <code>{target}</code> by ₹{amt}",
                        reply_markup=admin_kb(), parse_mode=HTML)
        try:
            await client.send_message(target, f"💰 <b>Balance updated:</b> ₹{amt}", parse_mode=HTML)
        except Exception: pass
        return

    tid = TICKET_STATE.get(uid)
    if tid:
        add_ticket_msg(tid, "user", txt)
        await msg.reply("<b>Sent to support.</b>", parse_mode=HTML)
        await notify_owner(text=f"📞 Ticket #{tid} · user <code>{uid}</code>:\n{txt}")
        return

    st = ADDADMIN_STATE.get(uid)
    if st:
        s = st["step"]
        if s == "id":
            try: st["target"] = int(txt)
            except ValueError:
                return await msg.reply("<b>Number daalo.</b>", parse_mode=HTML)
            st["step"] = "perms"
            return await msg.reply("<b>Perms bhejo</b>:", parse_mode=HTML)
        if s == "perms":
            add_admin(st["target"], f"admin_{st['target']}", txt, uid)
            ADDADMIN_STATE.pop(uid, None)
            return await msg.reply(f"<b>✅ Admin {st['target']} added.</b>",
                                    reply_markup=admin_kb(), parse_mode=HTML)


# ============================================================
#  BACKUP
# ============================================================
async def build_backup_zip():
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snap = os.path.join(BACKUP_DIR, f"snap_{ts}.db")
    src = sqlite3.connect(DB_PATH); dst = sqlite3.connect(snap)
    with dst: src.backup(dst)
    dst.close(); src.close()
    with closing(db()) as c:
        data = {t: [dict(r) for r in c.execute(f"SELECT * FROM {t}").fetchall()]
                for t in ("accounts","orders","deposits","users","settings",
                          "otp_log","payment_methods","referrals","force_channels",
                          "giveaways","giveaway_winners")}
    jpath = os.path.join(BACKUP_DIR, f"dump_{ts}.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    zip_base = os.path.join(BACKUP_DIR, f"backup_{ts}")
    arch = shutil.make_archive(zip_base, "zip", BACKUP_DIR)
    os.remove(snap); os.remove(jpath); return arch


async def send_backup(bot, chat_id=None):
    chat_id = chat_id or owner_id()
    if not chat_id: return
    try:
        path = await build_backup_zip()
    except Exception:
        log.exception("backup"); return
    try:
        await bot.send_document(chat_id, path, caption=f"💾 <b>AUTO-BACKUP</b>", parse_mode=HTML)
    except Exception as e:
        log.warning(f"backup send: {e}")


async def auto_weekly_winners():
    log.info("[auto-giveaway] picking winners")
    board = referral_leaderboard("week", 10)
    gws = list_giveaways(only_enabled=True)
    if not board or not gws: return
    week_start = week_start_iso()
    with closing(db()) as c:
        c.execute("DELETE FROM giveaway_winners WHERE week_start=?", (week_start,)); c.commit()
    txt = "🎉 <b>WEEKLY GIVEAWAY WINNERS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for gw in gws:
        idx = gw["rank"] - 1
        if idx < 0 or idx >= len(board): continue
        w = board[idx]
        uname = f"@{w['username']}" if w.get("username") else (w.get("first_name") or "user")
        save_winner(week_start, gw["rank"], w["uid"], w.get("username", ""),
                    gw["prize"], w["valid"])
        medal = ["🥇", "🥈", "🥉"][gw["rank"]-1] if gw["rank"] <= 3 else f"{gw['rank']}️⃣"
        txt += f"{medal} <b>{uname}</b> — {w['valid']} valid\n🎁 {gw['prize']}\n\n"
        try:
            await app.send_message(w["uid"],
                f"🎉 <b>You won rank #{gw['rank']}!</b>\n\n🎁 <b>Prize:</b> {gw['prize']}\n"
                f"Contact @{get_setting('admin_username', ADMIN_USERNAME)} to claim.",
                parse_mode=HTML)
        except Exception: pass
    txt += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    lc = log_channel_id()
    if lc:
        try: await app.send_message(lc, txt, parse_mode=HTML)
        except Exception: pass


# ============================================================
#  BOOT
# ============================================================
async def on_start():
    init_db()
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(lambda: asyncio.create_task(send_backup(app)),
                  "interval", hours=BACKUP_HOURS, id="backup")
    sched.add_job(lambda: asyncio.create_task(auto_weekly_winners()),
                  "cron", day_of_week="mon", hour=0, minute=0, id="weekly_giveaway")
    sched.start()
    log.info("bot up")
    oid = owner_id()
    if oid:
        try:
            await app.send_message(oid, "✅ <b>Bot started.</b>", parse_mode=HTML)
        except Exception: pass


if __name__ == "__main__":
    print("[boot] starting snipy bot...")
    app.start()
    app.loop.run_until_complete(on_start())
    asyncio.get_event_loop().run_forever()