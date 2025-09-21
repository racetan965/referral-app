import os
import psycopg2
import psycopg2.extras
from datetime import datetime
from typing import Optional

from flask import (
    Flask, g, request, redirect, url_for, render_template,
    abort, flash
)
from jinja2 import ChoiceLoader, DictLoader
import secrets
import string

APP_TITLE = "Referral System"

# ------------------------------
# DB CONFIG (PostgreSQL)
# ------------------------------
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://user:pass@host:5432/dbname",  # عدّل الافتراضي إذا بدك
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# ------------------------------
# DB helpers & schema
# ------------------------------
def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL)
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

SCHEMA = """
CREATE TABLE IF NOT EXISTS ref_users(
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    code TEXT UNIQUE NOT NULL,
    first_name TEXT,
    last_name TEXT,
    phone TEXT,
    telegram_id TEXT,
    referred_by_user_id INTEGER,
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY(referred_by_user_id) REFERENCES ref_users(id)
);

CREATE TABLE IF NOT EXISTS referrals(
    id SERIAL PRIMARY KEY,
    referrer_user_id INTEGER NOT NULL,
    referred_user_id INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(referrer_user_id, referred_user_id),
    FOREIGN KEY(referrer_user_id) REFERENCES ref_users(id),
    FOREIGN KEY(referred_user_id)  REFERENCES ref_users(id)
);

CREATE TABLE IF NOT EXISTS reserved_accounts(
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    currency TEXT,
    is_assigned INTEGER NOT NULL DEFAULT 0,
    assigned_user_id INTEGER,
    assigned_at TIMESTAMP,
    notes TEXT,
    FOREIGN KEY(assigned_user_id) REFERENCES ref_users(id)
);

CREATE TABLE IF NOT EXISTS blacklist(
    id SERIAL PRIMARY KEY,
    kind TEXT NOT NULL,               -- phone | name | telegram_id | referral_code | referral_username
    value TEXT NOT NULL,
    reason TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ref_users_username ON ref_users(username);
CREATE INDEX IF NOT EXISTS idx_ref_users_code     ON ref_users(code);
CREATE INDEX IF NOT EXISTS idx_ref_users_phone    ON ref_users(phone);
CREATE INDEX IF NOT EXISTS idx_ref_users_tg       ON ref_users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_reserved_currency  ON reserved_accounts(currency, is_assigned);
CREATE INDEX IF NOT EXISTS idx_blacklist_kind_val ON blacklist(kind, value);
"""

def init_db():
    db = get_db()
    with db.cursor() as cur:
        cur.execute("BEGIN;")
        for stmt in SCHEMA.split(";"):
            s = stmt.strip()
            if s:
                cur.execute(s + ";")
        db.commit()

# ------------------------------
# Utils
# ------------------------------
def gen_code(n: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))

def normalize(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    return " ".join(s.strip().lower().split())

def dict_cur(db):
    return db.cursor(cursor_factory=psycopg2.extras.DictCursor)

def get_user_by_code(code: str):
    db = get_db()
    with dict_cur(db) as cur:
        cur.execute("SELECT * FROM ref_users WHERE code=%s", (code,))
        return cur.fetchone()

def get_user_by_username(username: str):
    db = get_db()
    with dict_cur(db) as cur:
        cur.execute("SELECT * FROM ref_users WHERE username=%s", (username,))
        return cur.fetchone()

def record_referral(referrer_id: int, referred_id: int):
    db = get_db()
    with db.cursor() as cur:
        try:
            cur.execute(
                """
                INSERT INTO referrals(referrer_user_id, referred_user_id, created_at)
                VALUES(%s,%s,%s)
                ON CONFLICT DO NOTHING
                """,
                (referrer_id, referred_id, datetime.utcnow()),
            )
            db.commit()
        except Exception:
            db.rollback()

def check_blacklist(*, first_name: str|None, last_name: str|None, phone: str|None,
                    telegram_id: str|None, ref_code: str|None, ref_username: str|None) -> Optional[str]:
    db = get_db()
    name_val = None
    if first_name or last_name:
        name_val = normalize(f"{first_name or ''} {last_name or ''}")

    checks = [
        ("phone",           normalize(phone)),
        ("telegram_id",     normalize(telegram_id)),
        ("referral_code",   normalize(ref_code)),
        ("referral_username", normalize(ref_username)),
        ("name",            name_val),
    ]
    with dict_cur(db) as cur:
        for kind, value in checks:
            if not value:
                continue
            cur.execute(
                "SELECT reason FROM blacklist WHERE kind=%s AND value=%s AND active=1 LIMIT 1",
                (kind, value),
            )
            row = cur.fetchone()
            if row:
                return row["reason"] or f"Blocked by blacklist: {kind}"
    return None

def allocate_reserved_username(currency: Optional[str]) -> Optional[str]:
    db = get_db()
    with dict_cur(db) as cur:
        if currency:
            cur.execute(
                "SELECT username FROM reserved_accounts WHERE is_assigned=0 AND currency=%s ORDER BY id ASC LIMIT 1",
                (currency,),
            )
            r = cur.fetchone()
            if r:
                return r["username"]
        cur.execute(
            "SELECT username FROM reserved_accounts WHERE is_assigned=0 ORDER BY id ASC LIMIT 1"
        )
        r = cur.fetchone()
        return r["username"] if r else None

def mark_reserved_assigned(username: str, user_id: int):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE reserved_accounts SET is_assigned=1, assigned_user_id=%s, assigned_at=%s WHERE username=%s",
            (user_id, datetime.utcnow(), username),
        )
        db.commit()

# ------------------------------
# Templates
# ------------------------------

BASE_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ title or "App" }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
<<<<<<< HEAD
    body {font-family: system-ui,-apple-system,Segoe UI,Roboto,Ubuntu; margin: 0; color: #fff; background: #001f3f; display: flex; flex-direction: column; align-items: center; min-height: 100vh;}
    header {width: 100%; background: #000814; color: #fff; padding: 14px 16px; display: flex; justify-content: space-between; align-items: center;}
    header a {color: #fff; text-decoration: none; margin-right: 12px;}
    main {text-align: center; max-width: 900px; width: 100%; padding: 20px;}
=======
    body {font-family: system-ui; margin: 0; color: #fff; background: #001f3f; display: flex; flex-direction: column; align-items: center; min-height: 100vh;}
    header {width: 100%; background: #000814; color: #fff; padding: 14px 16px; display: flex; justify-content: space-between; align-items: center;}
    header a {color: #fff; text-decoration: none; margin-right: 12px;}
    main {text-align: center;}
>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
    input, select, button {padding: 10px 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; color: #000; margin: 5px;}
    button {background: #ff851b; color: #fff; border: none; cursor: pointer;}
    button:hover {background: #e06d00;}
    table {border-collapse: collapse; width: 80%; margin: 30px auto; background: #fff; color: #000; border-radius: 8px; overflow: hidden;}
    thead {background: #ff851b; color: #fff;}
    th, td {padding: 12px 15px; border-bottom: 1px solid #ddd; text-align: left;}
    tbody tr:hover {background: #f1f1f1;}
<<<<<<< HEAD
    .ok{background:#e9fff0;color:#0a6b2c;border:1px solid #a3e0b8;padding:8px 10px;border-radius:8px;display:inline-block}
    .err{background:#ffecec;color:#7a0000;border:1px solid #ffb3b3;padding:8px 10px;border-radius:8px;display:inline-block}
=======
>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
  </style>
</head>
<body>
  <header>
    <div><a href="{{ url_for('index') }}"><strong>{{ APP_TITLE }}</strong></a></div>
    <nav>
      <a href="{{ url_for('signup') }}">Sign-Up</a>
      <a href="{{ url_for('search') }}">Search</a>
      <a href="{{ url_for('fill_user') }}">Fill-In</a>
      <a href="{{ url_for('admin_accounts') }}">Pool</a>
      <a href="{{ url_for('admin_blacklist') }}">Blacklist</a>
<<<<<<< HEAD
      <a href="{{ url_for('admin_bulk_add') }}">Bulk Add</a>
=======
>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
    </nav>
  </header>
  <main>
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for cat, msg in messages %}
<<<<<<< HEAD
          <div class="{{ 'ok' if cat=='ok' else 'err' if cat=='err' else 'muted' }}">{{ msg|safe }}</div>
=======
          <div class="{{ 'ok' if cat=='ok' else 'err' if cat=='err' else 'muted' }}">{{ msg }}</div>
>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
        {% endfor %}
      {% endif %}
    {% endwith %}
    {% block content %}{% endblock %}
  </main>
</body>
</html>
"""

<<<<<<< HEAD
INDEX_HTML = """
{% extends 'base.html' %}
{% block content %}
  <h2>Welcome</h2>
  <p class="muted">Auto-assign username from pool, track referrals, and blacklist signups.</p>
  <a href="{{ url_for('signup') }}"><button>New Sign-Up</button></a>
{% endblock %}
"""

SIGNUP_HTML = """
{% extends 'base.html' %}
{% block content %}
  <h2>Sign-Up</h2>
  <form method="post">
    <input name="username" placeholder="Username" required><br>
    <input name="first_name" placeholder="First name"><br>
    <input name="last_name" placeholder="Last name"><br>
    <input name="phone" placeholder="Phone"><br>
    <input name="telegram_id" placeholder="Telegram ID"><br>
    <input name="referral_code" placeholder="Referral code"><br>
    <input name="referral_username" placeholder="Referral username"><br>
    <button type="submit">Create Account</button>
  </form>
{% endblock %}
"""

=======
>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
FILL_HTML = """
{% extends 'base.html' %}
{% block content %}
  <h2>Fill User Info</h2>
  <form method="post">
    <div>
      <label>Referral Code / Username<br>
        <input name="identifier" placeholder="Enter referral code or username" required>
      </label>
    </div>
    <div>
      <label>First Name<br><input name="first_name"></label>
    </div>
    <div>
      <label>Last Name<br><input name="last_name"></label>
    </div>
    <div>
      <label>Phone<br><input name="phone"></label>
    </div>
    <div>
      <label>Telegram ID<br><input name="telegram_id"></label>
    </div>
    <button type="submit">Save</button>
  </form>
{% endblock %}
"""

<<<<<<< HEAD
=======

INDEX_HTML = """
{% extends 'base.html' %}
{% block content %}
  <h2>Welcome</h2>
  <a href="{{ url_for('signup') }}"><button>New Sign-Up</button></a>
{% endblock %}
"""

SIGNUP_HTML = """
{% extends 'base.html' %}
{% block content %}
  <h2>Sign-Up</h2>
  <form method="post">
    <input name="first_name" placeholder="First name"><br>
    <input name="last_name" placeholder="Last name"><br>
    <input name="phone" placeholder="Phone"><br>
    <input name="telegram_id" placeholder="Telegram ID"><br>
    <select name="currency">
      <option value="">(Any)</option>
      <option value="USD">USD</option>
      <option value="KWD">KWD</option>
    </select><br>
    <input name="referral_code" placeholder="Referral code"><br>
    <input name="referral_username" placeholder="Referral username"><br>
    <button type="submit">Create Account</button>
  </form>
{% endblock %}
"""

>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
DASHBOARD_HTML = """
{% extends 'base.html' %}
{% block content %}
  <h2>User · {{ user.username }}</h2>
<<<<<<< HEAD
  <p>Referral code: <strong>{{ user.code }}</strong></p>
  {% if ref %}<p>Referred by: {{ ref.username }} ({{ ref.code }})</p>{% endif %}
=======
  <p>Referral code: {{ user.code }}</p>
  {% if ref %}<p>Referred by: {{ ref.username }}</p>{% endif %}
  <p><a href="{{ url_for('edit_user', code=user.code) }}"><button>Edit Account</button></a></p>
  <form method="post" action="{{ url_for('delete_user', code=user.code) }}" onsubmit="return confirm('Delete this user?');">
    <button type="submit" style="background:#b30000">Delete Account</button>
  </form>
>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
  <hr>
  <h3>Referrals</h3>
  {% if referrals %}
    <ul>
      {% for r in referrals %}
        <li>{{ r.username }} ({{ r.code }})</li>
      {% endfor %}
    </ul>
  {% else %}
    <p>No referrals yet.</p>
  {% endif %}
{% endblock %}
"""

<<<<<<< HEAD
=======
EDIT_HTML = """
{% extends 'base.html' %}
{% block content %}
  <h2>Edit User · {{ user.username }}</h2>
  <form method="post" style="max-width:400px;margin:auto;text-align:left;">

    <!-- First Name -->
    <div style="margin-bottom:12px;">
      <label style="display:block;margin-bottom:5px;">First Name</label>
      <input name="first_name" value="{{ user.first_name }}" style="width:100%;">
    </div>

    <!-- Last Name -->
    <div style="margin-bottom:12px;">
      <label style="display:block;margin-bottom:5px;">Last Name</label>
      <input name="last_name" value="{{ user.last_name }}" style="width:100%;">
    </div>

    <!-- Telephone -->
    <div style="margin-bottom:12px;">
      <label style="display:block;margin-bottom:5px;">Telephone</label>
      <input name="phone" value="{{ user.phone }}" style="width:100%;">
    </div>

    <!-- Telegram ID -->
    <div style="margin-bottom:12px;">
      <label style="display:block;margin-bottom:5px;">Telegram ID</label>
      <input name="telegram_id" value="{{ user.telegram_id }}" style="width:100%;">
    </div>

    <!-- Currency -->
    <div style="margin-bottom:20px;">
      <label style="display:block;margin-bottom:5px;">Preferred Currency</label>
      <select name="currency" style="width:100%;">
        <option value="">(Any)</option>
        <option value="USD" {% if user_currency=='USD' %}selected{% endif %}>USD</option>
        <option value="KWD" {% if user_currency=='KWD' %}selected{% endif %}>KWD</option>
      </select>
    </div>

    <!-- Buttons -->
    <div style="display:flex;gap:10px;">
      <button type="submit">Save Changes</button>
      <a href="{{ url_for('user_by_code', code=user.code) }}"><button type="button">Cancel</button></a>
    </div>
  </form>
{% endblock %}
"""

>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
SEARCH_HTML = """
{% extends 'base.html' %}
{% block content %}
  <h2>Search</h2>
  <form method="get" style="margin-bottom:20px;">
    <input name="q" value="{{ q or '' }}" placeholder="Search by username / code / phone / telegram id" style="width:300px;">
    <button>Search</button>
  </form>

  {% if q is not none %}
    {% if results %}
      <table>
        <thead>
          <tr>
            <th>Username</th>
            <th>Code</th>
            <th>Name</th>
            <th>Phone</th>
            <th>Telegram ID</th>
          </tr>
        </thead>
        <tbody>
          {% for u in results %}
            <tr>
<<<<<<< HEAD
              <td>
                <a href="{{ url_for('edit_user', code=u.code) }}">{{ u.username }}</a>
              </td>
=======
              <td><a href="{{ url_for('user_by_code', code=u.code) }}">{{ u.username }}</a></td>
>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
              <td>{{ u.code }}</td>
              <td>{{ (u.first_name ~ ' ' ~ u.last_name).strip() }}</td>
              <td>{{ u.phone or '' }}</td>
              <td>{{ u.telegram_id or '' }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    {% else %}
      <p>No results found.</p>
    {% endif %}
  {% endif %}
{% endblock %}
"""

<<<<<<< HEAD
EDIT_HTML = """
{% extends 'base.html' %}
{% block content %}
  <h2>Edit User · {{ user.username }}</h2>
  <form method="post" style="max-width:400px;margin:auto;text-align:left;">
    <div>
      <label>First Name</label>
      <input name="first_name" value="{{ user.first_name or '' }}" style="width:100%;">
    </div>
    <div>
      <label>Last Name</label>
      <input name="last_name" value="{{ user.last_name or '' }}" style="width:100%;">
    </div>
    <div>
      <label>Phone</label>
      <input name="phone" value="{{ user.phone or '' }}" style="width:100%;">
    </div>
    <div>
      <label>Telegram ID</label>
      <input name="telegram_id" value="{{ user.telegram_id or '' }}" style="width:100%;">
    </div>
    <br>
    <button type="submit">Save Changes</button>
  </form>
{% endblock %}
"""

=======
>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
ADMIN_ACCOUNTS_HTML = """
{% extends 'base.html' %}
{% block content %}
  <h2>Reserved Accounts Pool</h2>
<<<<<<< HEAD
  <p>Add entries directly in DB (username, currency, notes). Available entries are auto-assigned on signup.</p>
=======
>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
{% endblock %}
"""

ADMIN_BLACKLIST_HTML = """
{% extends 'base.html' %}
{% block content %}
  <h2>Blacklist</h2>
<<<<<<< HEAD
  <p>Manage in DB: kinds (phone, name, telegram_id, referral_code, referral_username).</p>
{% endblock %}
"""
ADMIN_BULK_ADD_HTML = """
{% extends 'base.html' %}
{% block content %}
  <h2>Bulk Add Users</h2>
  <p>ألزق كل Username في سطر جديد. Referral Code بيتولد أوتوماتيكياً لكل واحد.</p>
  <form method="post">
    <textarea name="usernames" rows="10" style="width:400px;" placeholder="user1\nuser2\nuser3"></textarea><br>
    <button type="submit">Add Users</button>
  </form>
{% endblock %}
"""

=======
{% endblock %}
"""

# Templates loader
>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
TEMPLATES_DICT = {
    "base.html": BASE_HTML,
    "index.html": INDEX_HTML,
    "signup.html": SIGNUP_HTML,
    "dashboard.html": DASHBOARD_HTML,
<<<<<<< HEAD
=======
    "edit.html": EDIT_HTML,
>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
    "search.html": SEARCH_HTML,
    "admin_accounts.html": ADMIN_ACCOUNTS_HTML,
    "admin_blacklist.html": ADMIN_BLACKLIST_HTML,
    "fill.html": FILL_HTML,
<<<<<<< HEAD
    "edit.html": EDIT_HTML,
    "admin_bulk_add.html": ADMIN_BULK_ADD_HTML,
=======
>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
}
existing_loader = app.jinja_loader
app.jinja_loader = ChoiceLoader([DictLoader(TEMPLATES_DICT), existing_loader])

# ------------------------------
# Routes
# ------------------------------
<<<<<<< HEAD
=======

>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
@app.route("/")
def index():
    return render_template("index.html", title=APP_TITLE, APP_TITLE=APP_TITLE)

<<<<<<< HEAD
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username    = request.form.get("username")
        first_name  = request.form.get("first_name")
        last_name   = request.form.get("last_name")
        phone       = request.form.get("phone")
        telegram_id = request.form.get("telegram_id")
        ref_code    = request.form.get("referral_code")
        ref_user    = request.form.get("referral_username")

        try:
            # resolve referrer
            referred_by_id = None
            if ref_code:
                ref = get_user_by_code(ref_code)
                if ref:
                    referred_by_id = ref["id"]
            if referred_by_id is None and ref_user:
                ref = get_user_by_username(ref_user)
                if ref:
                    referred_by_id = ref["id"]

            db = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

            user_code = gen_code()
            cur.execute("""
                INSERT INTO ref_users(username, code, first_name, last_name, phone, telegram_id, referred_by_user_id, created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
            """, (
                username,
                user_code,
                first_name or "",
                last_name or "",
                phone,
                telegram_id,
                referred_by_id,
                datetime.utcnow()
            ))
            new_user = cur.fetchone()
            db.commit()

            if referred_by_id and referred_by_id != new_user["id"]:
                record_referral(referred_by_id, new_user["id"])

            flash(f"Created: {new_user['username']} ({new_user['code']})", "ok")
            return redirect(url_for("user_by_code", code=new_user["code"]))
        except Exception as e:
            db.rollback()
            flash(str(e), "err")

    return render_template("signup.html", title="Sign-Up", APP_TITLE=APP_TITLE)

=======
>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
@app.route("/fill", methods=["GET", "POST"])
def fill_user():
    db = get_db()
    if request.method == "POST":
        identifier = request.form.get("identifier")
        first_name = request.form.get("first_name")
<<<<<<< HEAD
        last_name  = request.form.get("last_name")
        phone      = request.form.get("phone")
        telegram_id= request.form.get("telegram_id")

        with dict_cur(db) as cur:
            cur.execute("SELECT * FROM ref_users WHERE code=%s OR username=%s LIMIT 1", (identifier, identifier))
            user = cur.fetchone()
            if not user:
                flash("❌ User not found", "err")
            else:
                cur.execute("""
                    UPDATE ref_users
                    SET first_name=%s, last_name=%s, phone=%s, telegram_id=%s
                    WHERE id=%s
                """, (first_name, last_name, phone, telegram_id, user["id"]))
                db.commit()
                flash("✅ User updated successfully", "ok")

                # fetch referrals + referrer for dashboard
                cur.execute("SELECT username, code FROM ref_users WHERE id=%s", (user["referred_by_user_id"],)) if user["referred_by_user_id"] else None
                ref = cur.fetchone() if user["referred_by_user_id"] else None
                cur.execute("""
                    SELECT u.username, u.code
                    FROM referrals r JOIN ref_users u ON u.id=r.referred_user_id
                    WHERE r.referrer_user_id=%s
                """, (user["id"],))
                refs = cur.fetchall()
                return render_template("dashboard.html", user=user, ref=ref, referrals=refs, APP_TITLE=APP_TITLE)

    return render_template("fill.html", APP_TITLE=APP_TITLE)

@app.route("/u/<code>")
def user_by_code(code):
    db = get_db()
    with dict_cur(db) as cur:
        cur.execute("SELECT * FROM ref_users WHERE code=%s", (code,))
        user = cur.fetchone()
        if not user:
            abort(404)

        ref = None
        if user["referred_by_user_id"]:
            cur.execute("SELECT username, code FROM ref_users WHERE id=%s", (user["referred_by_user_id"],))
            ref = cur.fetchone()

        cur.execute("""
            SELECT u.username, u.code
            FROM referrals r JOIN ref_users u ON u.id=r.referred_user_id
            WHERE r.referrer_user_id=%s
            ORDER BY r.id DESC
        """, (user["id"],))
        refs = cur.fetchall()
    return render_template("dashboard.html", user=user, ref=ref, referrals=refs, APP_TITLE=APP_TITLE)

@app.route("/admin/bulk_add", methods=["GET", "POST"])
def admin_bulk_add():
    db = get_db()
    if request.method == "POST":
        usernames_text = request.form.get("usernames", "").strip()
        if not usernames_text:
            flash("❌ Please enter at least one username", "err")
            return render_template("admin_bulk_add.html", APP_TITLE=APP_TITLE)

        usernames = [u.strip() for u in usernames_text.splitlines() if u.strip()]
        added, skipped = [], []

        for uname in usernames:
            try:
                code = gen_code()
                with dict_cur(db) as cur:
                    cur.execute(
                        """INSERT INTO ref_users(username, code, created_at)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (username) DO NOTHING
                           RETURNING *""",
                        (uname, code, datetime.utcnow())
                    )
                    row = cur.fetchone()
                    if row:
                        db.commit()
                        added.append(uname)
                    else:
                        skipped.append(uname)
            except Exception as e:
                db.rollback()
                skipped.append(f"{uname} (error)")

        flash(f"✅ Added: {', '.join(added)}" if added else "⚠️ No new users added", "ok")
        if skipped:
            flash(f"⏭ Skipped: {', '.join(skipped)}", "err")

    return render_template("admin_bulk_add.html", APP_TITLE=APP_TITLE)

@app.route("/search")
def search():
    q = request.args.get("q")
    results = []
    if q is not None:
        qlike = f"%{q}%"
        db = get_db()
        with dict_cur(db) as cur:
            cur.execute("""
                SELECT * FROM ref_users
                WHERE username ILIKE %s
                   OR code ILIKE %s
                   OR first_name ILIKE %s
                   OR last_name ILIKE %s
                   OR phone ILIKE %s
                   OR telegram_id ILIKE %s
                ORDER BY id DESC
                LIMIT 200
            """, (qlike, qlike, qlike, qlike, qlike, qlike))
            results = cur.fetchall()
    return render_template("search.html", title="Search", APP_TITLE=APP_TITLE, q=q, results=results)

@app.route("/admin/accounts")
def admin_accounts():
    return render_template("admin_accounts.html", title="Admin Accounts")

@app.route("/admin/blacklist")
def admin_blacklist():
    return render_template("admin_blacklist.html", APP_TITLE=APP_TITLE)

@app.route("/u/<code>/edit", methods=["GET", "POST"])
def edit_user(code):
    db = get_db()
    with dict_cur(db) as cur:
        cur.execute("SELECT * FROM ref_users WHERE code=%s", (code,))
        user = cur.fetchone()
        if not user:
            abort(404)

        if request.method == "POST":
            first_name  = request.form.get("first_name")
            last_name   = request.form.get("last_name")
            phone       = request.form.get("phone")
            telegram_id = request.form.get("telegram_id")

            cur.execute("""
                UPDATE ref_users
=======
        last_name = request.form.get("last_name")
        phone = request.form.get("phone")
        telegram_id = request.form.get("telegram_id")

        # ابحث عن اليوزر بالـ code أو username
        cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM users WHERE code=%s OR username=%s LIMIT 1", (identifier, identifier))
        user = cur.fetchone()
        if not user:
            flash("❌ User not found", "err")
        else:
            cur.execute("""
                UPDATE users
>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
                SET first_name=%s, last_name=%s, phone=%s, telegram_id=%s
                WHERE id=%s
            """, (first_name, last_name, phone, telegram_id, user["id"]))
            db.commit()
            flash("✅ User updated successfully", "ok")
<<<<<<< HEAD
            return redirect(url_for("user_by_code", code=code))

    return render_template("edit.html", user=user, APP_TITLE=APP_TITLE)

# ------------------------------
# Start
# ------------------------------
if __name__ == "__main__":
    with app.app_context():
        init_db()
    port = int(os.environ.get("PORT", 8130))
    app.run(host="0.0.0.0", port=port, debug=True)
=======
            return redirect(url_for("user_by_code", code=user["code"]))

    return render_template("fill.html", APP_TITLE=APP_TITLE)

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        try:
            user = create_user_auto_username(
                first_name=request.form.get("first_name"),
                last_name=request.form.get("last_name"),
                phone=request.form.get("phone"),
                telegram_id=request.form.get("telegram_id"),
                preferred_currency=request.form.get("currency"),
                referred_by_code=request.form.get("referral_code"),
                referred_by_username=request.form.get("referral_username"),
            )
            flash(f"Created: {user['username']} ({user['code']})", "ok")
            return redirect(url_for("user_by_code", code=user["code"]))
        except Exception as e:
            flash(str(e), "err")
    return render_template("signup.html", title="Sign-Up", APP_TITLE=APP_TITLE)

@app.route("/u/<code>")
def user_by_code(code):
    db = get_db()
    user = get_user_by_code(code)
    if not user:
        abort(404)
    ref = None
    if user["referred_by_user_id"]:
        ref = db.execute("SELECT username, code FROM users WHERE id=?", (user["referred_by_user_id"],)).fetchone()
    refs = db.execute("SELECT u.username, u.code FROM referrals r JOIN users u ON u.id=r.referred_user_id WHERE r.referrer_user_id=?", (user["id"],)).fetchall()
    return render_template("dashboard.html", user=user, ref=ref, referrals=refs, APP_TITLE=APP_TITLE)

@app.route("/u/<code>/edit", methods=["GET", "POST"])
def edit_user(code):
    db = get_db()
    user = get_user_by_code(code)
    if not user:
        abort(404)

    if request.method == "POST":
        db.execute("UPDATE users SET first_name=?, last_name=?, phone=?, telegram_id=? WHERE id=?",
                   (request.form.get("first_name"), request.form.get("last_name"), request.form.get("phone"), request.form.get("telegram_id"), user["id"]))
        db.commit()
        if request.form.get("currency"):
            db.execute("UPDATE reserved_accounts SET currency=? WHERE assigned_user_id=?", (request.form.get("currency"), user["id"]))
            db.commit()
        flash("User updated.", "ok")
        return redirect(url_for("user_by_code", code=code))

    row = db.execute("SELECT currency FROM reserved_accounts WHERE assigned_user_id=?", (user["id"],)).fetchone()
    user_currency = row["currency"] if row else ""
    return render_template("edit.html", user=user, user_currency=user_currency, APP_TITLE=APP_TITLE)

@app.route("/u/<code>/delete", methods=["POST"])
def delete_user(code):
    db = get_db()
    user = get_user_by_code(code)
    if not user:
        abort(404)
    db.execute("DELETE FROM referrals WHERE referrer_user_id=? OR referred_user_id=?", (user["id"], user["id"]))
    db.execute("UPDATE reserved_accounts SET is_assigned=0, assigned_user_id=NULL, assigned_at=NULL WHERE assigned_user_id=?", (user["id"],))
    db.execute("DELETE FROM users WHERE id=?", (user["id"],))
    db.commit()
    flash("User deleted.", "ok")
    return redirect(url_for("search"))

@app.route("/search")
def search():
    q = request.args.get("q")
    results = []
    if q:
        qlike = f"%{q}%"
        db = get_db()
        results = db.execute("SELECT * FROM users WHERE username LIKE ? OR code LIKE ?", (qlike, qlike)).fetchall()
    return render_template("search.html", q=q, results=results, APP_TITLE=APP_TITLE)

@app.route("/admin/accounts")
def admin_accounts():
    return render_template("admin_accounts.html", APP_TITLE=APP_TITLE)

@app.route("/admin/blacklist")
def admin_blacklist():
    return render_template("admin_blacklist.html", APP_TITLE=APP_TITLE)

# ------------------------------
# App start
# ------------------------------

if __name__ == "__main__":
    with app.app_context():
        init_db()
    port = int(os.environ.get("PORT", 8000))
    app.run(host="127.0.0.1", port=port, debug=True)

>>>>>>> f3c4714a8f2f4b38e3f984c53704bd3e48fec273
