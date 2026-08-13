"""
Прайм Качалка: Прокачай Носорога — бэкенд тап-игры (FastAPI + SQLAlchemy).
БД: Postgres (DATABASE_URL) на проде, SQLite локально. Авторизация — Telegram WebApp initData (HMAC).
Античит: серверный кап скорости тапов + дневной кап тапов, идущих в личный рейтинг.
"""
import os, json, hmac, hashlib, random, datetime, time, urllib.request
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import create_engine, text

# ---------- Конфиг ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBHOOK_BASE = os.environ.get("WEBHOOK_BASE", "https://primeliga.onrender.com")
WEBHOOK_SECRET = hashlib.sha256(("wh:" + (BOT_TOKEN or "x")).encode()).hexdigest()[:32]
DEV = os.environ.get("DEV", "0") == "1"
ADMIN_IDS = {x.strip() for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
BOT_USERNAME = os.environ.get("BOT_USERNAME", "primebirthday_bot")
SHOP_URL = "https://primekraft.ru/"

# Даты игры (МСК). Переопределяются env для теста.
GAME_START = os.environ.get("GAME_START", "2026-08-20 00:00")   # старт тапа
GAME_FREEZE = os.environ.get("GAME_FREEZE", "2026-08-27 12:00")  # СТОП-ИГРА, рейтинг зафиксирован
GOAL = int(os.environ.get("GOAL", "1000000"))                   # общий челлендж комьюнити

# Античит
MAX_TAP_RATE = int(os.environ.get("MAX_TAP_RATE", "15"))        # тапов/сек максимум засчитываем
DAILY_RANK_CAP = int(os.environ.get("DAILY_RANK_CAP", "5000"))  # тапов/день в личный РЕЙТИНГ (сверх — только в общий счётчик)

# Начисления
CHECKIN_POINTS = 100
REF_POINTS = 200
QUIZ_POINTS = 50
DAILY_QUIZ = 3                    # вопросов викторины в день
SUB_BONUS = 300                   # бонус за подписку на канал
STREAK_BONUS = {3: 200, 7: 500}   # бонусы за серию чек-инов
RAFFLE_MIN_TAPS = 50              # порог участия в розыгрыше 10 сертификатов

CHANNEL = os.environ.get("CHANNEL", "")  # @username канала для подписки (проверка getChatMember); пусто = засчитываем по кнопке

# Эволюция носорога: (порог тапов, ключ, упражнение, эмодзи, подпись)
STAGES = [
    {"min": 0,     "key": "warmup",  "ex": "Разминка с гантелей", "emoji": "🦏", "label": "Разминается"},
    {"min": 1000,  "key": "bench",   "ex": "Жим штанги лёжа",     "emoji": "🦏", "label": "Подкачался"},
    {"min": 3000,  "key": "squat",   "ex": "Приседания со штангой","emoji": "🦏", "label": "Мускулистее"},
    {"min": 5000,  "key": "box",     "ex": "Бьёт боксёрскую грушу","emoji": "🦏", "label": "Мощный, в повязке"},
    {"min": 10000, "key": "beast",   "ex": "Рвёт цепи",           "emoji": "🦏🔥", "label": "BEAST MODE"},
]

# Лут-дропы. Промокоды — ПЛЕЙСХОЛДЕРЫ, Денис заменит на реальные с primekraft.ru.
LOOT = [
    {"type": "points", "weight": 55, "amount": 200, "title": "+200 баллов ⚡"},
    {"type": "points", "weight": 20, "amount": 500, "title": "+500 баллов 💪"},
    {"type": "promo",  "weight": 18, "code": "КАЧОК15", "desc": "−15% на заказ от 1500 ₽", "ttl_h": 48},
    {"type": "promo",  "weight": 7,  "code": "КАЧОК25", "desc": "−25% на заказ от 2000 ₽", "ttl_h": 48},
]

# Викторина по спортпиту (правильный ответ — индекс 0, на фронте перемешивается).
QUIZ = [
    {"id": 1,  "q": "Сколько примерно белка в порции сывороточного протеина (~30 г)?", "options": ["Около 24 г", "Около 5 г", "Около 50 г"], "answer": 0},
    {"id": 2,  "q": "Для чего в первую очередь нужен креатин?", "options": ["Повышает силу и работоспособность", "Сжигает жир", "Заменяет сон"], "answer": 0},
    {"id": 3,  "q": "Когда протеин особенно полезен для восстановления?", "options": ["После тренировки", "Только натощак", "Только раз в неделю"], "answer": 0},
    {"id": 4,  "q": "Как расшифровывается BCAA?", "options": ["Аминокислоты с разветвлённой цепью", "Быстрые углеводы", "Комплекс витаминов"], "answer": 0},
    {"id": 5,  "q": "Для чего чаще всего берут гейнер?", "options": ["Набор массы", "Похудение", "Крепкий сон"], "answer": 0},
    {"id": 6,  "q": "Чем изолят отличается от концентрата протеина?", "options": ["Меньше лактозы и жира", "Ничем", "Это углевод"], "answer": 0},
    {"id": 7,  "q": "Сколько воды желательно пить в день при активных тренировках?", "options": ["2–3 литра", "0,5 литра", "10 литров"], "answer": 0},
    {"id": 8,  "q": "Что помогает делать L-карнитин?", "options": ["Использовать жиры как источник энергии", "Строить мышцы напрямую", "Заменять протеин"], "answer": 0},
    {"id": 9,  "q": "Зачем нужны углеводы после тренировки?", "options": ["Восстановить гликоген", "Только набрать жир", "Ни за чем"], "answer": 0},
    {"id": 10, "q": "Что важнее всего для роста мышц?", "options": ["Регулярность и восстановление", "Один тяжёлый подход", "Только добавки"], "answer": 0},
    {"id": 11, "q": "Для чего полезны Омега-3?", "options": ["Суставы и сердце", "Только вкус", "Ни для чего"], "answer": 0},
    {"id": 12, "q": "Почему витамин D особенно важен зимой?", "options": ["Мало солнца — падает синтез", "Летом его слишком много", "Он не нужен"], "answer": 0},
    {"id": 13, "q": "Что такое казеин?", "options": ["Медленный белок, хорош на ночь", "Быстрый углевод", "Витамин"], "answer": 0},
    {"id": 14, "q": "Зачем нужна разминка перед тренировкой?", "options": ["Разогреть мышцы, снизить риск травм", "Устать заранее", "Заменить тренировку"], "answer": 0},
    {"id": 15, "q": "Что даёт кофеин перед тренировкой?", "options": ["Бодрость и фокус", "Мгновенный рост мышц", "Сонливость"], "answer": 0},
    {"id": 16, "q": "Сколько раз в неделю новичку тренировать одну мышечную группу?", "options": ["Около 2 раз", "7 раз", "0 раз"], "answer": 0},
    {"id": 17, "q": "Протеиновый батончик — это удобный источник чего?", "options": ["Белка в перекусе", "Только сахара", "Воды"], "answer": 0},
    {"id": 18, "q": "Что такое дефицит калорий?", "options": ["Тратишь больше, чем ешь", "Ешь больше нормы", "Не связано с весом"], "answer": 0},
]

# ---------- БД ----------
DB_URL = os.environ.get("DATABASE_URL", "")
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DB_URL.startswith("postgresql://"):
    DB_URL = DB_URL.replace("postgresql://", "postgresql+psycopg://", 1)
if not DB_URL:
    DB_URL = "sqlite:///" + os.environ.get("DB_PATH", "kachalka.db")
_connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, pool_pre_ping=True, connect_args=_connect_args)

def init_db():
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS users(
            user_id BIGINT PRIMARY KEY, first_name TEXT,
            taps BIGINT DEFAULT 0, points BIGINT DEFAULT 0,
            day_taps INTEGER DEFAULT 0, day_key TEXT,
            last_tap_ms BIGINT DEFAULT 0, next_drop BIGINT DEFAULT 200,
            last_check_day TEXT, streak INTEGER DEFAULT 0,
            referred_by BIGINT, sub_ok INTEGER DEFAULT 0,
            started INTEGER DEFAULT 0, last_seen TEXT, created_at TEXT)"""))
        c.execute(text("CREATE TABLE IF NOT EXISTS quiz_answers(user_id BIGINT, q_id INTEGER, correct INTEGER, day TEXT, PRIMARY KEY(user_id, q_id))"))
        c.execute(text("CREATE TABLE IF NOT EXISTS promo_issued(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id BIGINT, code TEXT, expires TEXT)") if DB_URL.startswith("sqlite")
                  else text("CREATE TABLE IF NOT EXISTS promo_issued(id BIGSERIAL PRIMARY KEY, user_id BIGINT, code TEXT, expires TEXT)"))
    # миграции: чтобы код разворачивался и поверх старой таблицы users (Прайм Лига)
    for stmt in ["ALTER TABLE users ADD COLUMN taps BIGINT DEFAULT 0",
                 "ALTER TABLE users ADD COLUMN points BIGINT DEFAULT 0",
                 "ALTER TABLE users ADD COLUMN day_taps INTEGER DEFAULT 0",
                 "ALTER TABLE users ADD COLUMN day_key TEXT",
                 "ALTER TABLE users ADD COLUMN last_tap_ms BIGINT DEFAULT 0",
                 "ALTER TABLE users ADD COLUMN next_drop BIGINT DEFAULT 200",
                 "ALTER TABLE users ADD COLUMN sub_ok INTEGER DEFAULT 0",
                 "ALTER TABLE users ADD COLUMN streak INTEGER DEFAULT 0",
                 "ALTER TABLE users ADD COLUMN referred_by BIGINT",
                 "ALTER TABLE users ADD COLUMN started INTEGER DEFAULT 0",
                 "ALTER TABLE users ADD COLUMN last_seen TEXT",
                 "ALTER TABLE users ADD COLUMN last_check_day TEXT"]:
        try:
            with engine.begin() as c:
                c.execute(text(stmt))
        except Exception:
            pass
    # сброс очков/тапов под новую игру (RESET_SCORES=1) — при переезде поверх старой базы Прайм Лиги
    if os.environ.get("RESET_SCORES", "0") == "1":
        try:
            with engine.begin() as c:
                c.execute(text("UPDATE users SET taps=0, points=0, day_taps=0, day_key=NULL, last_tap_ms=0, next_drop=200, streak=0, last_check_day=NULL, referred_by=NULL, sub_ok=0"))
        except Exception:
            pass

app = FastAPI(title="Прайм Качалка API")
init_db()

def today():
    return datetime.date.today().isoformat()

def msk_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=3)

def _parse(dt):
    return datetime.datetime.strptime(dt, "%Y-%m-%d %H:%M")

def game_state():
    now = msk_now()
    if now < _parse(GAME_START):
        return "before"
    if now >= _parse(GAME_FREEZE):
        return "frozen"
    return "live"

def stage_for(taps):
    st = STAGES[0]
    idx = 0
    for i, s in enumerate(STAGES):
        if taps >= s["min"]:
            st, idx = s, i
    nxt = STAGES[idx + 1]["min"] if idx + 1 < len(STAGES) else None
    return idx, st, nxt

# ---------- Telegram ----------
def tg_call(method, payload):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)}

WELCOME = (
    "🦏🔥 <b>Прайм Качалка — Прокачай Носорога!</b>\n\n"
    "Тапай по экрану и качай фирменного носорога PRIMEKRAFT. Каждый тап — одно повторение. "
    "Чем больше тапов — тем сильнее носорог: новые упражнения, BEAST MODE.\n\n"
    "Вместе собираем <b>1 000 000 повторений</b> за неделю. Топ-3 — сертификаты и боксы новинок, "
    "среди всех — розыгрыш на финальном эфире 27 августа.\n\nЖми кнопку и качай 👇"
)
def _play_kb():
    return {"inline_keyboard": [[{"text": "🦏 Качать носорога", "web_app": {"url": WEBHOOK_BASE + "/"}}]]}

@app.on_event("startup")
def _register_bot():
    if not BOT_TOKEN:
        return
    tg_call("setWebhook", {"url": WEBHOOK_BASE + "/webhook", "secret_token": WEBHOOK_SECRET, "allowed_updates": ["message"]})
    tg_call("setChatMenuButton", {"menu_button": {"type": "web_app", "text": "Качать", "web_app": {"url": WEBHOOK_BASE + "/"}}})

def _on_bot_message(chat, first_name):
    with engine.begin() as c:
        row = c.execute(text("SELECT 1 FROM users WHERE user_id=:u"), {"u": chat}).first()
        if row is None:
            c.execute(text("INSERT INTO users(user_id, first_name, created_at, started, day_key, next_drop) VALUES(:u,:f,:d,1,:d,200)"),
                      {"u": chat, "f": first_name, "d": today()})
        else:
            c.execute(text("UPDATE users SET started=1 WHERE user_id=:u"), {"u": chat})
    tg_call("sendMessage", {"chat_id": chat, "text": WELCOME, "parse_mode": "HTML", "reply_markup": _play_kb()})

@app.post("/webhook")
async def webhook(request: Request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise HTTPException(403, "bad secret")
    update = await request.json()
    msg = update.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    frm = msg.get("from") or {}
    if chat and msg.get("text"):
        await run_in_threadpool(_on_bot_message, chat, frm.get("first_name", ""))
    return {"ok": True}

# ---------- Авторизация ----------
def verify_init_data(init_data):
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    got = pairs.pop("hash", None)
    if not got or not BOT_TOKEN:
        return None
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        return None
    try:
        return json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError:
        return None

async def current_user(request: Request):
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    if user is None and DEV:
        user = {"id": int(request.headers.get("X-Dev-Id", "1001")), "first_name": "Денис (dev)"}
    if user is None:
        raise HTTPException(401, "Не удалось подтвердить Telegram-подпись")
    with engine.begin() as c:
        row = c.execute(text("SELECT * FROM users WHERE user_id=:id"), {"id": user["id"]}).mappings().first()
        if row is None:
            c.execute(text("INSERT INTO users(user_id, first_name, created_at, day_key, next_drop) VALUES(:id,:fn,:ca,:ca,200)"),
                      {"id": user["id"], "fn": user.get("first_name", ""), "ca": today()})
            row = c.execute(text("SELECT * FROM users WHERE user_id=:id"), {"id": user["id"]}).mappings().first()
    return dict(row)

def is_admin(uid):
    return str(uid) in ADMIN_IDS

def community_total():
    with engine.connect() as c:
        return int(c.execute(text("SELECT COALESCE(SUM(taps),0) FROM users")).scalar() or 0)

# ---------- API ----------
@app.get("/api/config")
def config():
    return {"name": "Прайм Качалка", "goal": GOAL, "start": GAME_START, "freeze": GAME_FREEZE,
            "state": game_state(), "shop": SHOP_URL,
            "stages": [{"min": s["min"], "ex": s["ex"], "emoji": s["emoji"], "label": s["label"]} for s in STAGES],
            "checkin": CHECKIN_POINTS, "ref": REF_POINTS, "quiz_points": QUIZ_POINTS,
            "prizes": {"top3": ["Сертификат 3000 ₽ + бокс новинок", "Сертификат 2000 ₽ + бокс", "Сертификат 1000 ₽ + бокс"],
                       "raffle": "10 сертификатов по 1000 ₽ разыгрываем на финальном эфире 27 августа",
                       "all": "Общий промокод −40% на эфире (24 часа)"}}

def _rank(uid, points):
    with engine.connect() as c:
        higher = c.execute(text("SELECT COUNT(*) FROM users WHERE points > :p"), {"p": points}).scalar()
    return (higher or 0) + 1

@app.get("/api/me")
async def me(request: Request):
    u = await current_user(request)
    if u.get("last_seen") != today():
        with engine.begin() as c:
            c.execute(text("UPDATE users SET last_seen=:d WHERE user_id=:id"), {"d": today(), "id": u["user_id"]})
    idx, st, nxt = stage_for(u["taps"])
    with engine.connect() as c:
        active_promos = [dict(r) for r in c.execute(text(
            "SELECT code, expires FROM promo_issued WHERE user_id=:u AND expires > :now ORDER BY expires DESC"),
            {"u": u["user_id"], "now": msk_now().isoformat()}).mappings()]
    return {"user_id": u["user_id"], "first_name": u["first_name"], "taps": u["taps"], "points": u["points"],
            "stage": idx, "stage_ex": st["ex"], "stage_emoji": st["emoji"], "stage_label": st["label"],
            "next_stage_at": nxt, "community": community_total(), "goal": GOAL,
            "rank": _rank(u["user_id"], u["points"]), "state": game_state(),
            "checked_in_today": u["last_check_day"] == today(), "streak": u.get("streak") or 0,
            "sub_ok": bool(u.get("sub_ok")), "channel": CHANNEL,
            "referral_link": f"https://t.me/{BOT_USERNAME}?startapp=ref{u['user_id']}",
            "active_promos": active_promos, "is_admin": is_admin(u["user_id"])}

@app.post("/api/tap")
async def tap(request: Request):
    u = await current_user(request)
    if game_state() != "live":
        raise HTTPException(423, "Игра не активна")
    body = await request.json()
    n = int(body.get("n", 0))
    if n <= 0:
        raise HTTPException(400, "Нет тапов")
    now_ms = int(time.time() * 1000)
    last = u.get("last_tap_ms") or 0
    elapsed = max(0, now_ms - last)
    allowed = int(elapsed / 1000 * MAX_TAP_RATE) + 5          # античит: кап скорости + маленький буфер
    accepted = max(0, min(n, allowed, 300))                   # и жёсткий потолок на один батч
    # дневной кап тапов, идущих в личный рейтинг
    dk = u.get("day_key")
    day_taps = u.get("day_taps") or 0
    if dk != today():
        day_taps = 0
    rank_add = max(0, min(accepted, DAILY_RANK_CAP - day_taps))
    day_taps += rank_add
    new_taps = u["taps"] + accepted
    new_points = u["points"] + rank_add
    # лут-дроп
    loot = None
    next_drop = u.get("next_drop") or 200
    if accepted > 0 and new_taps >= next_drop and game_state() == "live":
        d = random.choices(LOOT, weights=[x["weight"] for x in LOOT], k=1)[0]
        if d["type"] == "points":
            new_points += d["amount"]
            loot = {"type": "points", "title": d["title"]}
        else:
            exp = (msk_now() + datetime.timedelta(hours=d["ttl_h"])).isoformat()
            with engine.begin() as c:
                c.execute(text("INSERT INTO promo_issued(user_id, code, expires) VALUES(:u,:c,:e)"),
                          {"u": u["user_id"], "c": d["code"], "e": exp})
            loot = {"type": "promo", "code": d["code"], "desc": d["desc"], "ttl_h": d["ttl_h"]}
        next_drop = new_taps + random.randint(150, 400)
    with engine.begin() as c:
        c.execute(text("UPDATE users SET taps=:t, points=:p, day_taps=:dt, day_key=:dk, last_tap_ms=:ms, next_drop=:nd WHERE user_id=:id"),
                  {"t": new_taps, "p": new_points, "dt": day_taps, "dk": today(), "ms": now_ms, "nd": next_drop, "id": u["user_id"]})
    idx, st, nxt = stage_for(new_taps)
    return {"accepted": accepted, "taps": new_taps, "points": new_points, "stage": idx,
            "stage_ex": st["ex"], "stage_emoji": st["emoji"], "next_stage_at": nxt,
            "community": community_total(), "loot": loot,
            "rank_capped": rank_add < accepted}

@app.post("/api/checkin")
async def checkin(request: Request):
    u = await current_user(request)
    if game_state() == "frozen":
        raise HTTPException(423, "Игра завершена")
    if u["last_check_day"] == today():
        raise HTTPException(409, "Сегодня уже отмечался")
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    streak = (u.get("streak") or 0) + 1 if u["last_check_day"] == yesterday else 1
    bonus = STREAK_BONUS.get(streak, 0)
    added = CHECKIN_POINTS + bonus
    with engine.begin() as c:
        c.execute(text("UPDATE users SET points=points+:a, last_check_day=:d, streak=:s WHERE user_id=:id"),
                  {"a": added, "d": today(), "s": streak, "id": u["user_id"]})
    return {"added": added, "bonus": bonus, "streak": streak}

@app.post("/api/referral")
async def referral(request: Request):
    u = await current_user(request)
    if game_state() == "frozen":
        return {"ok": False, "reason": "frozen"}
    try:
        ref = int((await request.json()).get("ref"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Некорректная ссылка")
    if ref == u["user_id"] or u.get("referred_by"):
        return {"ok": False, "reason": "self_or_used"}
    with engine.begin() as c:
        if not c.execute(text("SELECT 1 FROM users WHERE user_id=:r"), {"r": ref}).first():
            return {"ok": False, "reason": "no_ref"}
        c.execute(text("UPDATE users SET referred_by=:r WHERE user_id=:id"), {"r": ref, "id": u["user_id"]})
        c.execute(text("UPDATE users SET points=points+:p WHERE user_id=:r"), {"p": REF_POINTS, "r": ref})
    return {"ok": True, "awarded": REF_POINTS}

@app.get("/api/quizzes")
async def quizzes(request: Request):
    u = await current_user(request)
    with engine.connect() as c:
        done = {r["q_id"]: r["correct"] for r in c.execute(
            text("SELECT q_id, correct FROM quiz_answers WHERE user_id=:u"), {"u": u["user_id"]}).mappings()}
        answered_today = c.execute(text("SELECT COUNT(*) FROM quiz_answers WHERE user_id=:u AND day=:d"),
                                   {"u": u["user_id"], "d": today()}).scalar() or 0
    remaining = max(0, DAILY_QUIZ - answered_today)
    questions = [{"id": q["id"], "q": q["q"], "options": q["options"], "answered": q["id"] in done,
                  "was_correct": bool(done.get(q["id"], 0)),
                  "correct_index": q["answer"] if q["id"] in done else None} for q in QUIZ]
    return {"points": QUIZ_POINTS, "daily_limit": DAILY_QUIZ, "remaining": remaining, "questions": questions}

@app.post("/api/quiz_answer")
async def quiz_answer(request: Request):
    u = await current_user(request)
    if game_state() == "frozen":
        raise HTTPException(423, "Игра завершена")
    body = await request.json()
    qid, choice = body.get("id"), body.get("choice")
    q = next((x for x in QUIZ if x["id"] == qid), None)
    if not q:
        raise HTTPException(404, "Вопрос не найден")
    correct = 1 if choice == q["answer"] else 0
    with engine.begin() as c:
        if c.execute(text("SELECT 1 FROM quiz_answers WHERE user_id=:u AND q_id=:q"), {"u": u["user_id"], "q": qid}).first():
            raise HTTPException(409, "На этот вопрос уже отвечал")
        answered_today = c.execute(text("SELECT COUNT(*) FROM quiz_answers WHERE user_id=:u AND day=:d"),
                                   {"u": u["user_id"], "d": today()}).scalar() or 0
        if answered_today >= DAILY_QUIZ:
            raise HTTPException(429, "На сегодня вопросы закончились — приходи завтра")
        c.execute(text("INSERT INTO quiz_answers(user_id, q_id, correct, day) VALUES(:u,:q,:c,:d)"),
                  {"u": u["user_id"], "q": qid, "c": correct, "d": today()})
        if correct:
            c.execute(text("UPDATE users SET points=points+:p WHERE user_id=:id"), {"p": QUIZ_POINTS, "id": u["user_id"]})
    return {"correct": bool(correct), "correct_index": q["answer"], "added": QUIZ_POINTS if correct else 0}

@app.post("/api/subscribe")
async def subscribe(request: Request):
    u = await current_user(request)
    if u.get("sub_ok"):
        return {"ok": True, "already": True}
    ok = True
    if CHANNEL:  # реальная проверка подписки, если задан канал и бот в нём админ
        r = tg_call("getChatMember", {"chat_id": CHANNEL, "user_id": u["user_id"]})
        status = ((r.get("result") or {}).get("status")) if r.get("ok") else None
        ok = status in ("member", "administrator", "creator")
    if not ok:
        return {"ok": False, "reason": "not_subscribed"}
    with engine.begin() as c:
        c.execute(text("UPDATE users SET sub_ok=1, points=points+:b WHERE user_id=:id"), {"b": SUB_BONUS, "id": u["user_id"]})
    return {"ok": True, "added": SUB_BONUS}

@app.get("/api/leaderboard")
async def leaderboard(request: Request):
    u = await current_user(request)
    with engine.connect() as c:
        top = [dict(r) for r in c.execute(text(
            "SELECT first_name, points, taps FROM users ORDER BY points DESC, taps DESC LIMIT 20")).mappings()]
    return {"top": top, "community": community_total(), "goal": GOAL,
            "me_rank": _rank(u["user_id"], u["points"]), "me_points": u["points"]}

# ---------- Финал / админ ----------
@app.get("/api/final/preview")
async def final_preview(request: Request):
    u = await current_user(request)
    if not is_admin(u["user_id"]):
        raise HTTPException(403, "Только админ")
    with engine.connect() as c:
        top3 = [dict(r) for r in c.execute(text(
            "SELECT user_id, first_name, points, taps FROM users ORDER BY points DESC, taps DESC LIMIT 3")).mappings()]
        eligible = c.execute(text("SELECT COUNT(*) FROM users WHERE taps >= :m"), {"m": RAFFLE_MIN_TAPS}).scalar()
    return {"community": community_total(), "goal": GOAL, "top3": top3, "raffle_eligible": eligible}

@app.post("/api/final/raffle")
async def final_raffle(request: Request):
    u = await current_user(request)
    if not is_admin(u["user_id"]):
        raise HTTPException(403, "Только админ")
    with engine.connect() as c:
        pool = [dict(r) for r in c.execute(text(
            "SELECT user_id, first_name FROM users WHERE taps >= :m"), {"m": RAFFLE_MIN_TAPS}).mappings()]
    random.shuffle(pool)
    winners = pool[:10]
    return {"winners": winners, "pool_size": len(pool)}

# ---------- Мини-апп ----------
@app.get("/")
def index():
    return FileResponse("index.html")

@app.get("/img/{name}")
def img(name: str):
    path = os.path.join("img", name)
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(404, "not found")
