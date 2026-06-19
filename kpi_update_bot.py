"""
KPI / SMM Update Bot — Abdufattoh PM
=============================================================
Nima qiladi:
  1. Har loyihaning Instagram sahifasini tekshiradi (Apify orqali):
       - oxirgi post necha kun oldin bo'lgan
       - shu oyda nechta post / video chiqarilgan
  2. Loyihalarni YAXSHI / YOMON deb ajratadi (posting izchilligi + reja).
  3. Google Sheet'lardan KPI ball va meeting holatini o'qiydi (qo'shimcha).
  4. Hammasini birlashtirib Telegram guruhga update tashlaydi.
       - Har N kunda (default 2) avtomatik (Toshkent vaqti)
       - /update  -> hozir darrov
       - /chatid  -> shu chat ID si
       - /start   -> ma'lumot
 
Talab qilinadigan kutubxonalar:
  pip install -r requirements.txt
"""
 
import os
import json
import logging
import re
from datetime import datetime, time as dtime, timezone
 
try:
    from zoneinfo import ZoneInfo            # Python 3.9+
except ImportError:                          # eski Python uchun
    ZoneInfo = None
 
import httpx
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv
 
load_dotenv()
 
# ============================= CONFIG =============================
BOT_TOKEN     = os.getenv("BOT_TOKEN", "")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "")
APIFY_TOKEN   = os.getenv("APIFY_TOKEN", "")          # apify.com -> Settings -> API tokens
GOOGLE_CREDS  = os.getenv("GOOGLE_CREDS_FILE", "service_account.json")
 
# Sheet ID'lar
KPI_SHEET_ID     = os.getenv("KPI_SHEET_ID", "1gMZJUowOA1tZglUuaLv6E1jaBSkoPCBZgMPv8-qqYBc")
MEETING_SHEET_ID = os.getenv("MEETING_SHEET_ID", "1kuKZNPbL26qN_idHtJXiTgA0oMkprD24gR8ja0fXDYg")
KPI_WORKSHEET     = os.getenv("KPI_WORKSHEET", "Main")
MEETING_WORKSHEET = os.getenv("MEETING_WORKSHEET", "Meeting Tracker")
 
# Jadval
UPDATE_EVERY_DAYS = int(os.getenv("UPDATE_EVERY_DAYS", "2"))
SEND_AT_HOUR      = int(os.getenv("SEND_AT_HOUR", "9"))     # Toshkent vaqti
TZ_NAME           = os.getenv("TZ_NAME", "Asia/Tashkent")
 
# IG postni "kech qolgan" deyish chegarasi (kun)
LATE_AFTER_DAYS   = int(os.getenv("LATE_AFTER_DAYS", "3"))
 
# Har content turining bali (KPI hisobi uchun)
POINTS = {"video": 3, "post": 1, "motion": 2, "kpi": 5, "hisobot": 5}
 
# ---------------------- LOYIHALAR RO'YXATI -----------------------
# name      : loyiha nomi
# manager   : mas'ul SMM manager
# ig        : instagram username (link emas, faqat handle)
# target    : oylik reja {jami, video, motion, post}
PROJECTS = [
    {"name": "Marja",              "manager": "Omilxon",   "ig": "marja.uz",
     "target": {"total": 12, "video": 6, "motion": 2, "post": 4}},
    {"name": "Tanho",              "manager": "Omilxon",   "ig": "tanho.uz",
     "target": {"total": 14, "video": 5, "motion": 5, "post": 4}},
    {"name": "Fortex",             "manager": "Omilxon",   "ig": "fortex.gisht",
     "target": {"total": 12, "video": 6, "motion": 2, "post": 4}},
    {"name": "Basaltium",          "manager": "Omilxon",   "ig": "basaltiumuz",
     "target": {"total": 14, "video": 8, "motion": 2, "post": 4}},
    {"name": "Aventos",            "manager": "Saydulloh", "ig": "aventosgroup.uz",
     "target": {"total": 10, "video": 7, "motion": 2, "post": 1}},
    {"name": "Shanggong",          "manager": "Saydulloh", "ig": "shanggong.uz",
     "target": {"total": 12, "video": 6, "motion": 2, "post": 4}},
    {"name": "Panel construction", "manager": "Saydulloh", "ig": "forza.uz",
     "target": {"total": 12, "video": 6, "motion": 2, "post": 4}},
    {"name": "VGR",                "manager": "Mustafo",   "ig": "vgr_uzbekistan_official",
     "target": {"total": 12, "video": 6, "motion": 2, "post": 4}},
    {"name": "Abba",               "manager": "Mustafo",   "ig": "abba.uz",
     "target": {"total": 6,  "video": 6, "motion": 0, "post": 0}},
    {"name": "Binogroup",          "manager": "Mustafo",   "ig": "binogroup.uz",
     "target": {"total": 12, "video": 6, "motion": 2, "post": 4}},
    {"name": "Delta",              "manager": "Mustafo",   "ig": "deltavita.uz",
     "target": {"total": 14, "video": 8, "motion": 2, "post": 4}},
    {"name": "Welle",              "manager": "Mustafo",   "ig": "welle_fonte",
     "target": {"total": 14, "video": 8, "motion": 2, "post": 4}},
    # Kingstone — akkaunt blokda; ochilsa qo'shiladi
    # Otfiv — kerak emas
]
 
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("kpi_bot")
 
 
def _tz():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(TZ_NAME)
        except Exception:
            pass
    return timezone.utc
 
 
def _now():
    return datetime.now(_tz())
 
 
def _days_in_month(dt):
    """Shu oydagi kunlar soni."""
    if dt.month == 12:
        nxt = dt.replace(year=dt.year + 1, month=1, day=1)
    else:
        nxt = dt.replace(month=dt.month + 1, day=1)
    return (nxt - dt.replace(day=1)).days
 
 
# ====================== INSTAGRAM (Apify) ========================
async def fetch_instagram():
    """Har loyihaning IG sahifasidan shu oydagi postlarni oladi.
    Qaytaradi: dict {project_name: {last_days, posts, videos, ok}}.
    Apify ishlamasa yoki token bo'lmasa — bo'sh dict qaytaradi."""
    result = {}
    if not APIFY_TOKEN:
        log.warning("APIFY_TOKEN yo'q — Instagram tekshiruvi o'tkazib yuborildi")
        return result
 
    now = _now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
 
    direct_urls = [f"https://www.instagram.com/{p['ig']}/" for p in PROJECTS]
    payload = {
        "directUrls": direct_urls,
        "resultsType": "posts",
        "resultsLimit": 30,
        "onlyPostsNewerThan": month_start.strftime("%Y-%m-%d"),
        "addParentData": False,
    }
    url = ("https://api.apify.com/v2/acts/apify~instagram-scraper/"
           f"run-sync-get-dataset-items?token={APIFY_TOKEN}")
 
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            items = r.json()
    except Exception as e:
        log.exception("Apify so'rovi xato: %s", e)
        return result
 
    # username -> project nomi
    by_user = {p["ig"].lower(): p["name"] for p in PROJECTS}
    buckets = {p["name"]: {"posts": 0, "videos": 0, "last": None} for p in PROJECTS}
 
    for it in items:
        user = (it.get("ownerUsername") or "").lower()
        pname = by_user.get(user)
        if not pname:
            continue
        ts = it.get("timestamp")
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            continue
        if dt < month_start.astimezone(timezone.utc):
            continue
        b = buckets[pname]
        b["posts"] += 1
        if (it.get("type") or "").lower() in ("video", "reel"):
            b["videos"] += 1
        if b["last"] is None or dt > b["last"]:
            b["last"] = dt
 
    for pname, b in buckets.items():
        if b["last"] is not None:
            last_days = (now.astimezone(timezone.utc) - b["last"]).days
        else:
            last_days = None
        result[pname] = {
            "last_days": last_days,
            "posts": b["posts"],
            "videos": b["videos"],
            "ok": last_days is not None and last_days <= LATE_AFTER_DAYS,
        }
    return result
 
 
# ====================== GOOGLE SHEETS (ixtiyoriy) ================
def _gc():
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    b64 = os.getenv("GOOGLE_CREDS_B64", "").strip()
    if b64:
        import base64
        info = json.loads(base64.b64decode(b64).decode("utf-8"))
        return gspread.authorize(Credentials.from_service_account_info(info, scopes=scopes))
    raw = os.getenv("GOOGLE_CREDS_JSON", "").strip()
    if raw:
        info = json.loads(raw)
        return gspread.authorize(Credentials.from_service_account_info(info, scopes=scopes))
    creds = Credentials.from_service_account_file(GOOGLE_CREDS, scopes=scopes)
    return gspread.authorize(creds)
 
 
def _num(val):
    if val is None:
        return 0.0
    s = str(val).strip().replace("\xa0", "").replace(" ", "").replace("%", "")
    if not s:
        return 0.0
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0
 
 
def read_meetings():
    """Meeting Tracker'dan har manager/loyiha bo'yicha uchrashuvlarni o'qiydi.
    Qaytaradi: (managers dict, done_total, target_total).
      managers[mgr] = [{project, done, remaining}, ...]"""
    gc = _gc()
    ws = gc.open_by_key(MEETING_SHEET_ID).worksheet(MEETING_WORKSHEET)
    rows = ws.get_all_values()
 
    hr = None
    for i, row in enumerate(rows):
        low = [c.lower() for c in row]
        if any("manager" in c for c in low) and any("loyiha" in c for c in low):
            hr = i
            break
    if hr is None:
        raise RuntimeError("Meeting sheet: sarlavha topilmadi")
 
    header = [c.lower() for c in rows[hr]]
 
    def col(*keys):
        for idx, h in enumerate(header):
            if any(k in h for k in keys):
                return idx
        return None
 
    c_mgr = col("manager")
    c_proj = col("loyiha", "proekt")
    c_m1 = col("1-uchrashuv", "uchrashuv (1")
    c_m2 = col("2-uchrashuv", "uchrashuv (16")
 
    managers = {}
    done_total = total = 0
    for row in rows[hr + 1:]:
        if c_proj is None or len(row) <= c_proj or not row[c_proj].strip():
            continue
        proj = row[c_proj].strip()
        mgr = row[c_mgr].strip() if c_mgr is not None and len(row) > c_mgr else "—"
        if not mgr:
            mgr = "—"
        m1 = bool(len(row) > c_m1 and row[c_m1].strip()) if c_m1 is not None else False
        m2 = bool(len(row) > c_m2 and row[c_m2].strip()) if c_m2 is not None else False
        done = int(m1) + int(m2)
        managers.setdefault(mgr, []).append({
            "project": proj, "done": done, "remaining": 2 - done,
        })
        done_total += done
        total += 2
    return managers, done_total, total
 
 
# =========================== MESSAGE =============================
def build_message(ig_data, meeting=None):
    now = _now()
    days_in_month = _days_in_month(now)
    days_left = max(days_in_month - now.day, 0)
 
    months_uz = ["", "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
                 "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr"]
    month_name = months_uz[now.month]
 
    good, bad = [], []
    for p in PROJECTS:
        d = ig_data.get(p["name"])
        if d is None:
            continue
        tgt = p["target"]["total"]
        posts = d["posts"]
        last = d["last_days"]
        remaining = max(tgt - posts, 0)
 
        if last is None:
            status = f"shu oyda post yo'q ❗ | reja {tgt}"
            bad.append((p, status))
        elif last <= LATE_AFTER_DAYS and posts >= 1:
            status = f"oxirgi post {last} kun oldin | {posts}/{tgt} ✅"
            good.append((p, status))
        else:
            extra = f" (yana {remaining} ta, {days_left} kun qoldi)" if remaining else ""
            status = f"oxirgi post {last} kun oldin ⚠️ | {posts}/{tgt}{extra}"
            bad.append((p, status))
 
    lines = [f"📊 <b>SMM UPDATE — {now.day}-{month_name}</b>"]
    lines.append(f"Yaxshi: <b>{len(good)}</b>  |  Orqada: <b>{len(bad)}</b>  |  Oygacha {days_left} kun")
    lines.append("")
 
    if good:
        lines.append("🟢 <b>Yaxshi ketayotgan loyihalar:</b>")
        for p, st in good:
            lines.append(f"  • {p['name']} — {p['manager']} — {st}")
        lines.append("")
 
    if bad:
        lines.append("🔴 <b>Orqada qolgan loyihalar:</b>")
        for p, st in bad:
            lines.append(f"  • {p['name']} — {p['manager']} — {st}")
        lines.append("")
 
    if meeting is not None:
        mgrs, done, total = meeting
        pct = round(done / total * 100) if total else 0
        lines.append(f"🤝 <b>Uchrashuvlar: {done}/{total}</b> ({pct}%)")
        for mgr in sorted(mgrs):
            pending = [m for m in mgrs[mgr] if m["remaining"] > 0]
            if not pending:
                lines.append(f"  👤 {mgr} — ✅ hammasi bajarilgan")
                continue
            lines.append(f"  👤 <b>{mgr}</b>")
            for m in pending:
                lines.append(f"    • {m['project']} — yana {m['remaining']} ta")
        lines.append("")
 
    lines.append("⏰ Postni chiqargach IG'da ko'rinadi — bot keyingi tekshiruvда hisobga oladi.")
    return "\n".join(lines)
 
 
async def make_update():
    ig = await fetch_instagram()
    meeting = None
    try:
        meeting = read_meetings()
    except Exception as e:
        log.warning("Meeting o'qilmadi: %s", e)
    return build_message(ig, meeting)
 
 
# =========================== HANDLERS ============================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men SMM/KPI update botiman.\n"
        f"Har {UPDATE_EVERY_DAYS} kunda guruhga Instagram postlar bo'yicha "
        "yaxshi/yomon loyihalar hisobotini tashlayman.\n"
        "/update — hozir tekshir\n/chatid — shu chat ID si"
    )
 
 
async def cmd_chatid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"chat_id = {update.effective_chat.id}")
 
 
async def cmd_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Tekshiryapman... (IG so'rovi 1-2 daqiqa olishi mumkin)")
    try:
        msg = await make_update()
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.exception("update xato")
        await update.message.reply_text(f"Xatolik: {e}")
 
 
async def scheduled_update(ctx: ContextTypes.DEFAULT_TYPE):
    if not GROUP_CHAT_ID:
        log.warning("GROUP_CHAT_ID yo'q — avtomatik update tashlanmadi")
        return
    try:
        msg = await make_update()
        await ctx.bot.send_message(chat_id=int(GROUP_CHAT_ID), text=msg,
                                   parse_mode=ParseMode.HTML)
        log.info("Avtomatik update yuborildi")
    except Exception as e:
        log.exception("scheduled update xato: %s", e)
 
 
def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN o'rnatilmagan (.env faylga qo'ying)")
 
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("update", cmd_update))
 
    interval = UPDATE_EVERY_DAYS * 24 * 60 * 60
    app.job_queue.run_repeating(
        scheduled_update,
        interval=interval,
        first=dtime(hour=SEND_AT_HOUR, minute=0, tzinfo=_tz()),
        name="smm_update",
    )
 
    log.info("Bot ishga tushdi. Har %d kunda (%s) update yuboradi.",
             UPDATE_EVERY_DAYS, TZ_NAME)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
 
 
if __name__ == "__main__":
    main()
