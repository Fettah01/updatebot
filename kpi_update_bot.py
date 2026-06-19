"""
KPI Update Bot — Abdufattoh PM
-------------------------------------------------------------
Ikkala Google Sheet'dan o'qiydi (KPI + Meeting Tracker), har manager
bo'yicha update tuzadi va Telegram guruhga avtomatik tashlaydi.

Ishlash tartibi:
  - Har N kunda (default 2) avtomatik update yuboradi
  - /update  -> hozir darrov update yuboradi (qo'lda)
  - /chatid  -> shu chat'ning ID sini qaytaradi (sozlashda kerak bo'ladi)
  - /start   -> qisqa ma'lumot

Talab qilinadigan kutubxonalar:
  pip install "python-telegram-bot[job-queue]==21.*" gspread google-auth python-dotenv
"""

import os
import json
import logging
from datetime import time as dtime

import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv

load_dotenv()

# ----------------------------- CONFIG -----------------------------
BOT_TOKEN        = os.getenv("BOT_TOKEN", "")           # BotFather'dan
GROUP_CHAT_ID    = os.getenv("GROUP_CHAT_ID", "")       # guruh ID (/chatid bilan top)
GOOGLE_CREDS     = os.getenv("GOOGLE_CREDS_FILE", "service_account.json")

# Sheet ID'lar (URL ichidagi /d/<ID>/ qismi). Sizniki oldindan to'ldirilgan:
KPI_SHEET_ID     = os.getenv("KPI_SHEET_ID", "1gMZJUowOA1tZglUuaLv6E1jaBSkoPCBZgMPv8-qqYBc")
MEETING_SHEET_ID = os.getenv("MEETING_SHEET_ID", "1kuKZNPbL26qN_idHtJXiTgA0oMkprD24gR8ja0fXDYg")

# Tab nomlari (kerak bo'lsa o'zgartiring)
KPI_WORKSHEET     = os.getenv("KPI_WORKSHEET", "Main")
MEETING_WORKSHEET = os.getenv("MEETING_WORKSHEET", "Meeting Tracker")

UPDATE_EVERY_DAYS = int(os.getenv("UPDATE_EVERY_DAYS", "2"))
SEND_AT_HOUR      = int(os.getenv("SEND_AT_HOUR", "9"))   # mahalliy soat (server vaqti)
MEETINGS_PER_PROJECT = 2

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("kpi_bot")

# ----------------------- GOOGLE SHEETS ----------------------------
def _gc():
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    # Railway/serverda: JSON ni to'g'ridan env var ichida saqlaymiz.
    # Lokalda: service_account.json faylidan o'qiydi.
    raw = os.getenv("GOOGLE_CREDS_JSON", "").strip()
    if raw:
        info = json.loads(raw)
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(GOOGLE_CREDS, scopes=scopes)
    return gspread.authorize(creds)


def _num(val):
    """'41,2' / '1 326 000' kabi qiymatlarni float ga aylantiradi."""
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


def _find_header_row(rows, must_have):
    """must_have ichidagi so'zlarni o'z ichiga olgan birinchi qatorni topadi."""
    for i, row in enumerate(rows):
        joined = " ".join(c.lower() for c in row)
        if all(any(k in c.lower() for c in row) for k in must_have):
            return i
    return None


def read_kpi():
    """KPI sheet'dan loyiha ballarini o'qiydi.
    Qaytaradi: (per_manager dict, team_avg_percent)."""
    gc = _gc()
    ws = gc.open_by_key(KPI_SHEET_ID).worksheet(KPI_WORKSHEET)
    rows = ws.get_all_values()

    hr = _find_header_row(rows, ["manager", "proekt"])
    if hr is None:
        raise RuntimeError("KPI sheet: sarlavha qatori topilmadi")

    header = [c.lower() for c in rows[hr]]
    def col(*keys):
        for idx, h in enumerate(header):
            if any(k in h for k in keys):
                return idx
        return None

    c_mgr   = col("manager")
    c_proj  = col("proekt", "loyiha")
    c_ideal = col("ideal")
    c_act   = col("proekt bali", "fakt bali", "bali")

    managers = {}
    tot_ideal = tot_act = 0.0
    for row in rows[hr + 1:]:
        if c_proj is None or len(row) <= c_proj:
            continue
        proj = row[c_proj].strip()
        mgr  = row[c_mgr].strip() if c_mgr is not None and len(row) > c_mgr else ""
        if not proj or not mgr:
            continue
        ideal = _num(row[c_ideal]) if c_ideal is not None and len(row) > c_ideal else 0.0
        act   = _num(row[c_act])   if c_act   is not None and len(row) > c_act   else 0.0
        if ideal <= 0:
            continue
        pct = act / ideal * 100
        managers.setdefault(mgr, []).append({
            "project": proj, "ideal": ideal, "actual": act,
            "gap": round(ideal - act, 1), "pct": round(pct),
        })
        tot_ideal += ideal
        tot_act += act

    team_avg = round(tot_act / tot_ideal * 100) if tot_ideal else 0
    return managers, team_avg


def read_meetings():
    """Meeting Tracker'dan har loyiha bo'yicha bajarilgan uchrashuvlarni o'qiydi.
    Qaytaradi: (per_manager dict, done_total, target_total)."""
    gc = _gc()
    ws = gc.open_by_key(MEETING_SHEET_ID).worksheet(MEETING_WORKSHEET)
    rows = ws.get_all_values()

    hr = _find_header_row(rows, ["manager", "loyiha"])
    if hr is None:
        raise RuntimeError("Meeting sheet: sarlavha qatori topilmadi")

    header = [c.lower() for c in rows[hr]]
    def col(*keys):
        for idx, h in enumerate(header):
            if any(k in h for k in keys):
                return idx
        return None

    c_mgr  = col("manager")
    c_proj = col("loyiha", "proekt")
    c_m1   = col("1-uchrashuv", "uchrashuv (1")
    c_m2   = col("2-uchrashuv", "uchrashuv (16")

    managers = {}
    done_total = 0
    proj_count = 0
    for row in rows[hr + 1:]:
        if c_proj is None or len(row) <= c_proj:
            continue
        proj = row[c_proj].strip()
        mgr  = row[c_mgr].strip() if c_mgr is not None and len(row) > c_mgr else ""
        if not proj or not mgr:
            continue
        m1 = (len(row) > c_m1 and row[c_m1].strip()) if c_m1 is not None else False
        m2 = (len(row) > c_m2 and row[c_m2].strip()) if c_m2 is not None else False
        done = int(bool(m1)) + int(bool(m2))
        managers.setdefault(mgr, []).append({
            "project": proj, "done": done,
            "remaining": MEETINGS_PER_PROJECT - done,
        })
        done_total += done
        proj_count += 1

    return managers, done_total, proj_count * MEETINGS_PER_PROJECT


# --------------------------- MESSAGE ------------------------------
def build_message():
    kpi_mgr, team_avg = read_kpi()
    meet_mgr, done_total, target_total = read_meetings()

    lines = []
    lines.append("📊 <b>KPI UPDATE — Iyun</b>")
    lines.append(f"Jamoa o'rtacha bali: <b>{team_avg}%</b> "
                 f"{'🟢' if team_avg >= 70 else '🔴 (70% dan past — bonus yo‘q)'}")
    lines.append(f"Uchrashuvlar: <b>{done_total}/{target_total}</b>")
    lines.append("")

    # barcha managerlar (ikkala sheet'dan birlashtirib)
    all_mgrs = sorted(set(kpi_mgr) | set(meet_mgr))
    for mgr in all_mgrs:
        lines.append(f"👤 <b>{mgr}</b>")

        # Uchrashuvlar bo'yicha qolgani
        for m in meet_mgr.get(mgr, []):
            if m["remaining"] > 0:
                lines.append(f"  • {m['project']} — uchrashuv: yana <b>{m['remaining']} ta</b>")

        # KPI ball bo'yicha orqada qolganlar (100% dan past)
        lagging = sorted([p for p in kpi_mgr.get(mgr, []) if p["pct"] < 100],
                         key=lambda p: p["gap"], reverse=True)
        for p in lagging:
            lines.append(f"  • {p['project']} — ball: {p['pct']}% "
                         f"(+{p['gap']} kerak) ⬆️")

        if not meet_mgr.get(mgr) and not lagging:
            lines.append("  ✅ hammasi joyida")
        lines.append("")

    lines.append("⏰ Muddat: <b>30-iyun</b>. Uchrashuv qilingach sanasini sheet'ga yozing.")
    return "\n".join(lines)


# --------------------------- HANDLERS -----------------------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men KPI update botiman.\n"
        f"Har {UPDATE_EVERY_DAYS} kunda guruhga avtomatik update tashlayman.\n"
        "/update — hozir update\n/chatid — shu chat ID si"
    )


async def cmd_chatid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"chat_id = {update.effective_chat.id}")


async def cmd_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        msg = build_message()
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.exception("update xato")
        await update.message.reply_text(f"Xatolik: {e}")


async def scheduled_update(ctx: ContextTypes.DEFAULT_TYPE):
    if not GROUP_CHAT_ID:
        log.warning("GROUP_CHAT_ID o'rnatilmagan — avtomatik update tashlanmadi")
        return
    try:
        msg = build_message()
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

    # Har UPDATE_EVERY_DAYS kunda, SEND_AT_HOUR da
    interval = UPDATE_EVERY_DAYS * 24 * 60 * 60
    app.job_queue.run_repeating(
        scheduled_update,
        interval=interval,
        first=dtime(hour=SEND_AT_HOUR, minute=0),
        name="kpi_update",
    )

    log.info("Bot ishga tushdi. Har %d kunda update yuboradi.", UPDATE_EVERY_DAYS)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
