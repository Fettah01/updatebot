"""
KPI / SMM Update Bot — Abdufattoh PM
=============================================================
Funksiyalar:
  1. Har loyihaning Instagram sahifasini tekshiradi (Apify):
       - oxirgi post necha kun oldin
       - shu oyda nechta post / video / motion
       - o'rtacha layk / koment (engagement)
  2. Loyihalarni YAXSHI / YOMON deb ajratadi.
  3. Content turi taqsimoti (video / post target bo'yicha).
  4. Manager reytingi (loyihalar natijasiga qarab).
  5. Har hafta oxirida ENG YAXSHI VIDEO -> manager mukofot yutadi.
  6. Oy oxirida yakuniy hisobot (reja vs fakt, bonus chizig'i).
  7. Meeting holatini Google Sheet'dan o'qiydi.
 
Buyruqlar:
  /update  -> hozir SMM update
  /best    -> hozir haftaning eng yaxshi videosi
  /report  -> hozir oy yakuni hisobot
  /chatid  -> shu chat ID si
  /start   -> ma'lumot
"""
 
import os
import json
import logging
from datetime import datetime, timedelta, time as dtime, timezone
 
try:
    from zoneinfo import ZoneInfo
except ImportError:
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
APIFY_TOKEN   = os.getenv("APIFY_TOKEN", "")
GOOGLE_CREDS  = os.getenv("GOOGLE_CREDS_FILE", "service_account.json")
 
KPI_SHEET_ID     = os.getenv("KPI_SHEET_ID", "1gMZJUowOA1tZglUuaLv6E1jaBSkoPCBZgMPv8-qqYBc")
MEETING_SHEET_ID = os.getenv("MEETING_SHEET_ID", "1kuKZNPbL26qN_idHtJXiTgA0oMkprD24gR8ja0fXDYg")
MEETING_WORKSHEET = os.getenv("MEETING_WORKSHEET", "Meeting Tracker")
 
# Jadval
UPDATE_EVERY_DAYS = int(os.getenv("UPDATE_EVERY_DAYS", "2"))
SEND_AT_HOUR      = int(os.getenv("SEND_AT_HOUR", "9"))
TZ_NAME           = os.getenv("TZ_NAME", "Asia/Tashkent")
LATE_AFTER_DAYS   = int(os.getenv("LATE_AFTER_DAYS", "3"))
 
# Haftalik mukofot
WEEKLY_DAY   = int(os.getenv("WEEKLY_DAY", "6"))      # 0=Dush ... 6=Yak
WEEKLY_PRIZE = os.getenv("WEEKLY_PRIZE", "100 000 so'm")
 
# "Eng yaxshi video" ballash og'irliklari (ko'rish / layk / koment)
W_VIEWS    = float(os.getenv("W_VIEWS", "1"))
W_LIKES    = float(os.getenv("W_LIKES", "10"))
W_COMMENTS = float(os.getenv("W_COMMENTS", "30"))
 
BONUS_PCT = int(os.getenv("BONUS_PCT", "70"))         # bonus chizig'i
 
# Har content turining bali (KPI hisobi uchun)
POINTS = {"video": 3, "post": 1, "motion": 2, "kpi": 5, "hisobot": 5}
 
# ---------------------- LOYIHALAR RO'YXATI -----------------------
PROJECTS = [
    {"name": "Marja",              "manager": "Omilxon",   "ig": "marja.uz",
     "target": {"total": 12, "video": 6, "motion": 2, "post": 4}},
    {"name": "Tanho",              "manager": "Omilxon",   "ig": "tanho.uz",
     "target": {"total": 14, "video": 5, "motion": 5, "post": 4}},
    {"name": "Fortex",             "manager": "Omilxon",   "ig": "fortex.gisht",
     "target": {"total": 12, "video": 6, "motion": 2, "post": 4}},
    {"name": "Basaltium",          "manager": "Omilxon",   "ig": "basaltium_uz",
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
    # Kingstone — akkaunt blokda; Otfiv — kerak emas
]
 
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("kpi_bot")
 
 
# ---------------------------- yordamchi --------------------------
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
    if dt.month == 12:
        nxt = dt.replace(year=dt.year + 1, month=1, day=1)
    else:
        nxt = dt.replace(month=dt.month + 1, day=1)
    return (nxt - dt.replace(day=1)).days
 
 
def _fmt(n):
    """1234567 -> '1 234 567'"""
    return f"{int(n):,}".replace(",", " ")
 
 
MONTHS_UZ = ["", "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
             "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr"]
 
 
def _proj(name):
    for p in PROJECTS:
        if p["name"] == name:
            return p
    return None
 
 
# ====================== INSTAGRAM (Apify) ========================
async def fetch_instagram():
    """Qaytaradi: (summary, posts).
      summary[project] = {last_days, posts, videos, images, avg_likes,
                          avg_comments, ok}
      posts = [{project, manager, type('video'|'post'), ts(utc),
                likes, comments, views, url}, ...]"""
    if not APIFY_TOKEN:
        log.warning("APIFY_TOKEN yo'q — Instagram tekshiruvi o'tkazib yuborildi")
        return {}, []
 
    now = _now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    since = min(month_start, now - timedelta(days=8))
    month_start_utc = month_start.astimezone(timezone.utc)
    now_utc = now.astimezone(timezone.utc)
 
    direct_urls = [f"https://www.instagram.com/{p['ig']}/" for p in PROJECTS]
    payload = {
        "directUrls": direct_urls,
        "resultsType": "posts",
        "resultsLimit": 40,
        "onlyPostsNewerThan": since.strftime("%Y-%m-%d"),
        "addParentData": False,
    }
    url = ("https://api.apify.com/v2/acts/apify~instagram-scraper/"
           f"run-sync-get-dataset-items?token={APIFY_TOKEN}")
 
    try:
        async with httpx.AsyncClient(timeout=240) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            items = r.json()
    except Exception as e:
        log.exception("Apify so'rovi xato: %s", e)
        return {}, []
 
    by_user = {p["ig"].lower(): p for p in PROJECTS}
    posts = []
    for it in items:
        user = (it.get("ownerUsername") or "").lower()
        p = by_user.get(user)
        if not p:
            continue
        try:
            dt = datetime.fromisoformat(str(it.get("timestamp")).replace("Z", "+00:00"))
        except Exception:
            continue
        typ_raw = (it.get("type") or "").lower()
        is_video = typ_raw in ("video", "reel") or (it.get("productType") or "").lower() == "clips"
        posts.append({
            "project": p["name"],
            "manager": p["manager"],
            "type": "video" if is_video else "post",
            "ts": dt,
            "likes": int(it.get("likesCount") or 0),
            "comments": int(it.get("commentsCount") or 0),
            "views": int(it.get("videoViewCount") or it.get("videoPlayCount") or 0),
            "url": it.get("url") or it.get("inputUrl") or "",
        })
 
    summary = {}
    for p in PROJECTS:
        pp = [x for x in posts if x["project"] == p["name"] and x["ts"] >= month_start_utc]
        n = len(pp)
        last = max((x["ts"] for x in pp), default=None)
        last_days = (now_utc - last).days if last else None
        summary[p["name"]] = {
            "last_days": last_days,
            "posts": n,
            "videos": sum(1 for x in pp if x["type"] == "video"),
            "images": sum(1 for x in pp if x["type"] == "post"),
            "avg_likes": round(sum(x["likes"] for x in pp) / n) if n else 0,
            "avg_comments": round(sum(x["comments"] for x in pp) / n) if n else 0,
            "ok": last_days is not None and last_days <= LATE_AFTER_DAYS,
        }
    return summary, posts
 
 
# ====================== GOOGLE SHEETS (meeting) ==================
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
 
 
def read_meetings():
    """Qaytaradi: (managers dict, done_total, target_total)."""
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
 
 
# ========================= HISOB-KITOB ===========================
def manager_ranking(summary):
    """Managerlarni bajarilgan post foiziga qarab saralaydi."""
    stats = {}
    for p in PROJECTS:
        d = summary.get(p["name"])
        if not d:
            continue
        s = stats.setdefault(p["manager"], {"done": 0, "target": 0})
        s["done"] += d["posts"]
        s["target"] += p["target"]["total"]
    ranked = []
    for mgr, s in stats.items():
        pct = round(s["done"] / s["target"] * 100) if s["target"] else 0
        ranked.append({"manager": mgr, "pct": pct, "done": s["done"], "target": s["target"]})
    ranked.sort(key=lambda x: x["pct"], reverse=True)
    return ranked
 
 
def best_video(posts):
    """Oxirgi 7 kundagi eng yaxshi videoni qaytaradi (ball: views+likes+comments)."""
    week_start = (_now() - timedelta(days=7)).astimezone(timezone.utc)
    vids = [x for x in posts if x["type"] == "video" and x["ts"] >= week_start]
    if not vids:
        return None
    for v in vids:
        v["score"] = v["views"] * W_VIEWS + v["likes"] * W_LIKES + v["comments"] * W_COMMENTS
    return max(vids, key=lambda x: x["score"])
 
 
# =========================== XABARLAR ============================
def build_message(summary, meeting=None):
    now = _now()
    days_left = max(_days_in_month(now) - now.day, 0)
    month = MONTHS_UZ[now.month]
 
    good, bad = [], []
    for p in PROJECTS:
        d = summary.get(p["name"])
        if d is None:
            continue
        tgt = p["target"]
        v_tgt = tgt["video"] + tgt["motion"]   # IG'da motion ham video
        breakdown = f"🎬{d['videos']}/{v_tgt} 🖼{d['images']}/{tgt['post']}"
        eng = f"❤️{d['avg_likes']} 💬{d['avg_comments']}" if d["posts"] else ""
        last = d["last_days"]
        remaining = max(tgt["total"] - d["posts"], 0)
 
        if last is None:
            bad.append((p, f"shu oyda post yo'q ❗ | reja {tgt['total']}"))
        elif last <= LATE_AFTER_DAYS:
            good.append((p, f"oxirgi post {last} kun oldin | {d['posts']}/{tgt['total']} "
                            f"({breakdown}) {eng} ✅"))
        else:
            extra = f" — yana {remaining} ta, {days_left} kun qoldi" if remaining else ""
            bad.append((p, f"oxirgi post {last} kun oldin ⚠️ | {d['posts']}/{tgt['total']} "
                           f"({breakdown}){extra}"))
 
    lines = [f"📊 <b>SMM UPDATE — {now.day}-{month}</b>"]
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
 
    # Manager reytingi
    ranked = manager_ranking(summary)
    if ranked:
        medals = ["🥇", "🥈", "🥉"] + ["▪️"] * 10
        lines.append("🏅 <b>Manager reytingi:</b>")
        for i, r in enumerate(ranked):
            lines.append(f"  {medals[i]} {r['manager']} — {r['pct']}% "
                         f"({r['done']}/{r['target']})")
        lines.append("")
 
    # Meeting
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
 
    lines.append("⏰ Post chiqqach IG'da ko'rinadi — keyingi tekshiruvда hisobga olinadi.")
    return "\n".join(lines)
 
 
def build_weekly_award(posts):
    bv = best_video(posts)
    if bv is None:
        return "🏆 <b>Haftaning eng yaxshi videosi</b>\n\nBu hafta video topilmadi."
    lines = ["🏆 <b>HAFTANING ENG YAXSHI VIDEOSI</b>", ""]
    lines.append(f"📹 <b>{bv['project']}</b> — {bv['manager']}")
    lines.append(f"👁 {_fmt(bv['views'])} ko'rish  |  ❤️ {_fmt(bv['likes'])}  |  💬 {_fmt(bv['comments'])}")
    if bv["url"]:
        lines.append(f"🔗 {bv['url']}")
    lines.append("")
    lines.append(f"🎉 <b>{bv['manager']}</b> — {WEEKLY_PRIZE} mukofot yutib oldi! 👏")
    return "\n".join(lines)
 
 
def build_monthly_report(summary, meeting=None):
    now = _now()
    month = MONTHS_UZ[now.month]
    lines = [f"📅 <b>OY YAKUNI — {month}</b>", ""]
 
    for p in PROJECTS:
        d = summary.get(p["name"])
        if d is None:
            continue
        tgt = p["target"]["total"]
        pct = round(d["posts"] / tgt * 100) if tgt else 0
        mark = "✅" if pct >= 100 else ("🟡" if pct >= BONUS_PCT else "🔴")
        lines.append(f"{mark} {p['name']} — {p['manager']} — {d['posts']}/{tgt} ({pct}%)")
    lines.append("")
 
    ranked = manager_ranking(summary)
    if ranked:
        lines.append(f"🏅 <b>Yakuniy reyting</b> (bonus chizig'i {BONUS_PCT}%):")
        medals = ["🥇", "🥈", "🥉"] + ["▪️"] * 10
        for i, r in enumerate(ranked):
            bonus = "🟢 bonus" if r["pct"] >= BONUS_PCT else "🔴 bonus yo'q"
            lines.append(f"  {medals[i]} {r['manager']} — {r['pct']}% — {bonus}")
        lines.append("")
 
    if meeting is not None:
        _, done, total = meeting
        pct = round(done / total * 100) if total else 0
        lines.append(f"🤝 Uchrashuvlar: {done}/{total} ({pct}%)")
    return "\n".join(lines)
 
 
# ------------------------ yig'uvchilar ---------------------------
async def make_update():
    summary, _ = await fetch_instagram()
    meeting = None
    try:
        meeting = read_meetings()
    except Exception as e:
        log.warning("Meeting o'qilmadi: %s", e)
    return build_message(summary, meeting)
 
 
async def make_weekly():
    _, posts = await fetch_instagram()
    return build_weekly_award(posts)
 
 
async def make_monthly():
    summary, _ = await fetch_instagram()
    meeting = None
    try:
        meeting = read_meetings()
    except Exception as e:
        log.warning("Meeting o'qilmadi: %s", e)
    return build_monthly_report(summary, meeting)
 
 
# =========================== HANDLERS ============================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men SMM/KPI update botiman.\n"
        f"Har {UPDATE_EVERY_DAYS} kunda Instagram bo'yicha hisobot,\n"
        "har hafta eng yaxshi video mukofoti, oy oxirida yakun tashlayman.\n"
        "/update — hozir update\n/best — haftaning eng yaxshi videosi\n"
        "/report — oy yakuni\n/chatid — shu chat ID si"
    )
 
 
async def cmd_chatid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"chat_id = {update.effective_chat.id}")
 
 
async def cmd_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Tekshiryapman... (IG so'rovi 1-2 daqiqa olishi mumkin)")
    try:
        await update.message.reply_text(await make_update(), parse_mode=ParseMode.HTML)
    except Exception as e:
        log.exception("update xato")
        await update.message.reply_text(f"Xatolik: {e}")
 
 
async def cmd_best(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Eng yaxshi videoni aniqlayapman...")
    try:
        await update.message.reply_text(await make_weekly(), parse_mode=ParseMode.HTML)
    except Exception as e:
        log.exception("best xato")
        await update.message.reply_text(f"Xatolik: {e}")
 
 
async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Oy yakunini tayyorlayapman...")
    try:
        await update.message.reply_text(await make_monthly(), parse_mode=ParseMode.HTML)
    except Exception as e:
        log.exception("report xato")
        await update.message.reply_text(f"Xatolik: {e}")
 
 
async def scheduled_update(ctx: ContextTypes.DEFAULT_TYPE):
    if not GROUP_CHAT_ID:
        log.warning("GROUP_CHAT_ID yo'q — update tashlanmadi")
        return
    try:
        msg = await make_update()
        await ctx.bot.send_message(chat_id=int(GROUP_CHAT_ID), text=msg, parse_mode=ParseMode.HTML)
        log.info("Avtomatik update yuborildi")
    except Exception as e:
        log.exception("scheduled update xato: %s", e)
 
 
async def daily_checker(ctx: ContextTypes.DEFAULT_TYPE):
    """Har kuni: hafta oxiri -> mukofot, oy oxiri -> yakuniy hisobot."""
    if not GROUP_CHAT_ID:
        return
    now = _now()
    try:
        if now.weekday() == WEEKLY_DAY:
            msg = await make_weekly()
            await ctx.bot.send_message(chat_id=int(GROUP_CHAT_ID), text=msg, parse_mode=ParseMode.HTML)
            log.info("Haftalik mukofot yuborildi")
        if now.day == _days_in_month(now):
            msg = await make_monthly()
            await ctx.bot.send_message(chat_id=int(GROUP_CHAT_ID), text=msg, parse_mode=ParseMode.HTML)
            log.info("Oy yakuni yuborildi")
    except Exception as e:
        log.exception("daily_checker xato: %s", e)
 
 
def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN o'rnatilmagan (.env faylga qo'ying)")
 
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(CommandHandler("best", cmd_best))
    app.add_handler(CommandHandler("report", cmd_report))
 
    at = dtime(hour=SEND_AT_HOUR, minute=0, tzinfo=_tz())
    interval = UPDATE_EVERY_DAYS * 24 * 60 * 60
    app.job_queue.run_repeating(scheduled_update, interval=interval, first=at, name="smm_update")
    app.job_queue.run_daily(daily_checker, time=at, name="weekly_monthly")
 
    log.info("Bot ishga tushdi. Update har %d kunda; mukofot haftada; yakun oy oxirida.",
             UPDATE_EVERY_DAYS)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
 
 
if __name__ == "__main__":
    main()
