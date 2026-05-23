import logging, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

import config
import database as db

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

CHANNELS = [config.REFERRAL_CHANNEL]  # کانال‌های اجباری (پیش‌فرض)

def get_all_channels():
    """لیست کانال‌های اجباری: پیش‌فرض + کانال‌های اضافه‌شده از پنل ادمین"""
    extra = db.get_forced_channels()
    base = [config.REFERRAL_CHANNEL]
    all_ch = list(dict.fromkeys(base + extra))  # بدون تکرار
    return all_ch

PLAN_LABELS = {
    "1gb":            "اشتراک ۱ گیگ",
    "2gb":            "اشتراک ۲ گیگ",
    "3gb":            "اشتراک ۳ گیگ",
    "4gb":            "اشتراک ۴ گیگ",
    "5gb":            "اشتراک ۵ گیگ",
    "7gb":            "اشتراک ۷ گیگ",
    "10gb":           "اشتراک ۱۰ گیگ",
    "50gb":           "اشتراک ۵۰ گیگ",
    "100gb":          "اشتراک ۱۰۰ گیگ",
    "20mb":           "تست ۲۰ مگ رایگان",
    "100mb_referral": "رفرال ۱۰۰ مگ",
    "referral":       "رفرال",
    **{k: v["name"] for k, v in config.BUNDLE_PLANS.items()},
}

# ── Helpers ───────────────────────────────────────────────

def is_admin(uid): return uid in config.ADMIN_IDS or uid in db.get_admin_ids()
def all_admins(): return list(set(config.ADMIN_IDS + db.get_admin_ids()))
def fmt(p): return f"{p:,} تومان"
def is_bot_enabled(): return db.get_setting("bot_enabled", "1") != "0"
def flag(key, default="1"): return db.get_setting(key, default) != "0"

def card(): return db.get_setting("card_number") or config.CARD_NUMBER
def cardholder(): return db.get_setting("card_holder") or config.CARD_HOLDER

VIP_WARN_THRESHOLD  = 3_000_000   # هشدار موجودی VIP زیر ۳ میلیون
VIP_EXIT_THRESHOLD  = 2_500_000   # خروج از VIP زیر ۲.۵ میلیون

def price(key):
    """قیمت پلن، با احتساب override از دیتابیس"""
    base = int(db.get_setting(f"price_{key}") or 0) or \
        (config.PLANS.get(key) or config.TEST_PLANS.get(key) or config.BUNDLE_PLANS.get(key) or {}).get("price", 0)
    return base

def vip_price(key, user_id):
    """قیمت با تخفیف VIP کاربر (اگه موجودی زیر ۲.۵م باشه VIP اعمال نمی‌شه)"""
    u = db.get_user(user_id)
    total = u.get("total_topup", 0) if u else 0
    bal = u.get("balance", 0) if u else 0
    if bal <= VIP_EXIT_THRESHOLD:
        return price(key)  # بدون تخفیف
    return config.apply_vip_discount(price(key), total)

def crypto_rate(coin):
    val = db.get_setting(f"crypto_rate_{coin}")
    return int(val) if val else 0

def crypto_wallet(coin):
    val = db.get_setting(f"crypto_wallet_{coin}")
    if val: return val
    return config.CRYPTO_WALLETS.get(coin, {}).get("address", "")

def escape_md(text: str) -> str:
    if not text:
        return text
    for ch in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(ch, f'\\{ch}')
    return text

def uinfo(u):
    un = f"@{escape_md(u['username'])}" if u.get("username") else "ندارد"
    full_name = escape_md(u['full_name'])
    return f"👤 {full_name}\n🔗 {un}\n🆔 `{u['user_id']}`"

def vip_badge(user_id):
    """بج VIP کاربر"""
    u = db.get_user(user_id)
    if not u: return ""
    tier = config.get_vip_tier(u.get("total_topup", 0))
    return f"\n{tier['label']} — {tier['discount']}% تخفیف" if tier else ""

async def check_vip_balance_warning(bot, user_id):
    """بعد از کم شدن موجودی VIP، هشدار یا خروج از VIP رو بررسی می‌کنه"""
    u = db.get_user(user_id)
    if not u: return
    tier = config.get_vip_tier(u.get("total_topup", 0))
    if not tier: return  # کاربر VIP نیست
    bal = u.get("balance", 0)
    if bal <= VIP_EXIT_THRESHOLD:
        try:
            await bot.send_message(user_id,
                f"⚠️ *توجه مهم*\n\n"
                f"موجودی شما به {fmt(bal)} کاهش یافته است.\n"
                f"چون موجودی زیر {fmt(VIP_EXIT_THRESHOLD)} رسیده، "
                f"*تخفیف VIP شما موقتاً غیرفعال می‌شود.*\n\n"
                f"برای فعال‌سازی مجدد، موجودی خود را افزایش دهید.",
                parse_mode="Markdown")
        except Exception: pass
    elif bal <= VIP_WARN_THRESHOLD:
        try:
            await bot.send_message(user_id,
                f"⚠️ *هشدار موجودی VIP*\n\n"
                f"موجودی کیف پول شما به {fmt(bal)} رسیده است.\n"
                f"در صورتی که موجودی به زیر {fmt(VIP_EXIT_THRESHOLD)} برسد، "
                f"از سطح VIP خارج خواهید شد.\n\n"
                f"📌 *موجودی شما رو به اتمام است.*",
                parse_mode="Markdown")
        except Exception: pass

async def is_member(bot, user_id):
    for ch in get_all_channels():
        try:
            m = await bot.get_chat_member(ch, user_id)
            if m.status not in ("member", "administrator", "creator"):
                return False
        except Exception:
            return False
    return True

def main_kb(uid=None):
    rows = [
        [KeyboardButton("🛒 خرید اشتراک"), KeyboardButton("📦 خرید بسته‌ای")],
        [KeyboardButton("🧪 اکانت تست"),   KeyboardButton("👥 زیرمجموعه‌گیری")],
        [KeyboardButton("🎧 پشتیبانی"),    KeyboardButton("👤 حساب من")],
        [KeyboardButton("💳 افزایش موجودی"), KeyboardButton("👑 VIP")],
    ]
    if uid and is_admin(uid):
        rows.append([KeyboardButton("🔧 پنل ادمین")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def join_kb():
    channels = get_all_channels()
    buttons = []
    for ch in channels:
        ch_clean = ch.lstrip("@")
        buttons.append([InlineKeyboardButton(f"📢 کانال {ch_clean}", url=f"https://t.me/{ch_clean}")])
    buttons.append([InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")])
    return InlineKeyboardMarkup(buttons)

def admin_kb():
    s_sales  = "🟢 فروش باز"  if flag("sales_open")  else "🔴 فروش بسته"
    s_card   = "🟢 کارت باز"  if flag("card_open")   else "🔴 کارت بسته"
    s_topup  = "🟢 شارژ باز"  if flag("topup_open")  else "🔴 شارژ بسته"
    s_crypto = "🟢 ارز باز"   if flag("crypto_open") else "🔴 ارز بسته"
    s_bot    = "🟢 ربات روشن" if is_bot_enabled()    else "🔴 ربات خاموش"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 کاربران", callback_data="a_users"),
         InlineKeyboardButton("💰 پرداخت‌های در انتظار", callback_data="a_pays")],
        [InlineKeyboardButton("📦 مدیریت کانفیگ‌ها", callback_data="a_configs")],
        [InlineKeyboardButton("💲 قیمت‌ها", callback_data="a_prices"),
         InlineKeyboardButton("💳 اطلاعات کارت", callback_data="a_card_menu")],
        [InlineKeyboardButton("💎 تنظیمات ارز دیجیتال", callback_data="a_crypto")],
        [InlineKeyboardButton("👤 مدیریت ادمین‌ها", callback_data="a_admins")],
        [InlineKeyboardButton("💰 موجودی کاربر", callback_data="a_balance"),
         InlineKeyboardButton("📢 پیام همگانی", callback_data="a_broadcast")],
        [InlineKeyboardButton("🎫 صدور کارت تخفیف", callback_data="a_discount"),
         InlineKeyboardButton("🔍 جستجوی کاربر", callback_data="a_search_user_btn")],
        [InlineKeyboardButton("👑 تنظیمات VIP", callback_data="a_vip_settings")],
        [InlineKeyboardButton("📢 مدیریت کانال‌های اجباری", callback_data="a_channels")],
        [InlineKeyboardButton("🎰 قرعه‌کشی", callback_data="a_lottery")],
        [InlineKeyboardButton(f"{s_sales} ← تغییر", callback_data="a_toggle_sales")],
        [InlineKeyboardButton(f"{s_card} ← تغییر", callback_data="a_toggle_card"),
         InlineKeyboardButton(f"{s_topup} ← تغییر", callback_data="a_toggle_topup")],
        [InlineKeyboardButton(f"{s_crypto} ← تغییر", callback_data="a_toggle_crypto")],
        [InlineKeyboardButton(f"{s_bot} ← تغییر", callback_data="a_toggle_bot")],
    ])

def back_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")]])

# ── State ─────────────────────────────────────────────────

def gs(uid): return db.load_state(uid)
def ss(uid, state): db.save_state(uid, state)
def cs(uid): db.clear_state(uid)

# ── Payment timeout ───────────────────────────────────────

async def pay_timeout(bot, pay_id, user_id, chat_id, secs):
    await asyncio.sleep(secs)
    pay = db.get_payment(pay_id)
    if pay and pay["status"] == "pending":
        db.cancel_payment(pay_id)
        cs(user_id)
        try:
            await bot.send_message(chat_id, "⏰ زمان پرداخت تمام شد و سفارش لغو شد.", reply_markup=main_kb(user_id))
        except Exception: pass

# ── Channel check ─────────────────────────────────────────

async def require_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if is_admin(uid): return True
    if not await is_member(context.bot, uid):
        text = f"⛔️ برای استفاده از ربات باید عضو کانال ما باشید:"
        kb = join_kb()
        if update.message:
            await update.message.reply_text(text, reply_markup=kb)
        elif update.callback_query:
            await update.callback_query.answer("ابتدا عضو کانال شوید", show_alert=True)
        return False
    return True

# ── /start ────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    cs(user.id)

    if not is_bot_enabled() and not is_admin(user.id):
        await update.message.reply_text("🔴 ربات در حال حاضر غیرفعال است. بعداً امتحان کنید.")
        return

    if db.is_banned(user.id) and not is_admin(user.id):
        await update.message.reply_text("⛔️ حساب شما مسدود شده است.")
        return

    ref = None
    if context.args:
        ru = db.get_user_by_referral(context.args[0])
        if ru and ru["user_id"] != user.id:
            ref = ru["user_id"]

    db_user = db.get_or_create_user(user.id, user.username or "", user.full_name or "", ref)

    # اطلاع دعوت‌کننده
    if ref and db_user["_is_new"]:
        ref_owner = db.get_user(ref)
        if ref_owner:
            try:
                await context.bot.send_message(
                    ref, f"🎉 دعوت شما موفق بود!\n"
                         f"👤 {db_user['full_name']} عضو شد.\n"
                         f"📊 مجموع دعوت‌های شما: {ref_owner['referral_count'] + 1}"
                )
            except Exception: pass

            # بررسی آستانه رفرال — هر ۴ نفر = ۱۰۰ مگ رایگان (بدون محدودیت)
            updated = db.get_user(ref)
            if updated:
                total_ref = updated["referral_count"]
                # بررسی می‌کنیم آیا به مضرب جدیدی از ۴ رسیده‌ایم
                rewarded_times = updated.get("referral_rewarded", 0)  # تعداد دفعات جایزه گرفته
                if total_ref > 0 and total_ref // config.REFERRAL_THRESHOLD > rewarded_times:
                    db.increment_referral_rewarded(ref)
                    cfg = db.assign_config(config.REFERRAL_PLAN_KEY, ref)
                    new_total = total_ref // config.REFERRAL_THRESHOLD
                    if cfg:
                        try:
                            await context.bot.send_message(ref,
                                f"🎊 تبریک! به مضرب {new_total * config.REFERRAL_THRESHOLD} دعوت رسیدید!\n\n"
                                f"🎁 {config.REFERRAL_PLAN_NAME}",
                                parse_mode="Markdown")
                            await context.bot.send_message(ref, cfg)
                        except Exception: pass
                    else:
                        for aid in all_admins():
                            try:
                                await context.bot.send_message(aid,
                                    f"🎉 کاربر به {new_total * config.REFERRAL_THRESHOLD} دعوت رسید:\n{uinfo(updated)}\n\n"
                                    f"⚠️ کانفیگ رفرال ({config.REFERRAL_PLAN_KEY}) موجود نیست!")
                            except Exception: pass
                        try:
                            await context.bot.send_message(ref,
                                f"🎊 تبریک! به مضرب {new_total * config.REFERRAL_THRESHOLD} دعوت رسیدید!\n"
                                f"🎁 {config.REFERRAL_PLAN_NAME} شما به زودی ارسال می‌شود.")
                        except Exception: pass

    if not await is_member(context.bot, user.id) and not is_admin(user.id):
        await update.message.reply_text(
            f"سلام {user.first_name} عزیز! 👋\n\n"
            "برای استفاده از ربات ابتدا باید عضو کانال ما شوید:",
            reply_markup=join_kb()
        )
        return

    await update.message.reply_text(
        f"سلام {user.first_name} عزیز! 👋\nبه ربات xservpn خوش آمدید.",
        reply_markup=main_kb(user.id)
    )

# ── Message router ────────────────────────────────────────

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    tg = update.effective_user
    if not db.get_user(uid):
        db.get_or_create_user(uid, tg.username or "", tg.full_name or "")
    text = update.message.text or ""
    state = gs(uid)
    w = state.get("w")

    if w:
        if w == "receipt":            await recv_receipt(update, context); return
        if w == "topup_receipt":      await recv_topup_receipt(update, context); return
        if w == "crypto_receipt":     await recv_crypto_receipt(update, context); return
        if w == "support":            await recv_support(update, context); return
        if w == "topup_amount":       await recv_topup_amount(update, context); return
        if w == "a_bal_uid":          await a_recv_bal_uid(update, context); return
        if w == "a_bal_amt":          await a_recv_bal_amt(update, context); return
        if w == "a_price":            await a_recv_price(update, context); return
        if w == "a_configs":          await a_recv_configs(update, context); return
        if w == "a_broadcast":        await a_recv_broadcast(update, context); return
        if w == "a_add_admin":        await a_recv_add_admin(update, context); return
        if w == "a_del_admin":        await a_recv_del_admin(update, context); return
        if w == "a_card":             await a_recv_card(update, context); return
        if w == "a_cardholder":       await a_recv_cardholder(update, context); return
        if w == "a_send_cfg":         await a_recv_send_cfg(update, context); return
        if w == "a_crypto_rate":      await a_recv_crypto_rate(update, context); return
        if w == "a_crypto_wallet":    await a_recv_crypto_wallet(update, context); return
        if w == "a_discount_code":    await a_recv_discount_code(update, context); return
        if w == "a_discount_end":     await a_recv_discount_end(update, context); return
        if w == "a_search_user":      await a_recv_search_user(update, context); return
        if w == "a_vip_tier":         await a_recv_vip_tier(update, context); return
        if w == "a_add_channel":       await a_recv_add_channel(update, context); return
        if w == "a_del_channel":       await a_recv_del_channel(update, context); return
        if w == "discount_code":      await recv_discount_code(update, context); return
        cs(uid)

    # بان
    if db.is_banned(uid) and not is_admin(uid):
        await update.message.reply_text("⛔️ حساب شما مسدود شده است. با پشتیبانی تماس بگیرید.")
        return

    if not await require_member(update, context): return

    if text == "🛒 خرید اشتراک":             await show_plans(update, context, config.PLANS, "sub")
    elif text == "📦 خرید بسته‌ای":           await show_bundles(update, context)
    elif text == "🧪 اکانت تست":             await show_test(update, context)
    elif text == "👥 زیرمجموعه‌گیری":        await show_referral(update, context)
    elif text == "🎧 پشتیبانی":              await start_support(update, context)
    elif text == "👤 حساب من":               await show_account(update, context)
    elif text == "💳 افزایش موجودی":         await start_topup(update, context)
    elif text == "👑 VIP":                   await show_vip(update, context)
    elif text == "🔧 پنل ادمین" and is_admin(uid): await show_admin(update, context)

async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = gs(uid)
    w = state.get("w")
    if w == "receipt":
        await recv_receipt(update, context)
    elif w == "topup_receipt":
        await recv_topup_receipt(update, context)
    elif w == "crypto_receipt":
        await recv_crypto_receipt(update, context)

# ── Callbacks ─────────────────────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    uid = q.from_user.id

    if d == "check_join":
        if await is_member(context.bot, uid):
            await q.edit_message_text("✅ عضویت تایید شد!", reply_markup=None)
            await context.bot.send_message(uid, "به ربات xservpn خوش آمدید! 👋", reply_markup=main_kb(uid))
        else:
            await q.answer("هنوز عضو نشدید! ابتدا عضو کانال شوید.", show_alert=True)
        return

    if not is_admin(uid) and not d.startswith("a_"):
        if not await is_member(context.bot, uid):
            await q.answer("ابتدا عضو کانال شوید", show_alert=True)
            return

    # انتخاب پلن اشتراک
    if d.startswith("plan_"):
        key = d[5:]
        plan = config.PLANS.get(key)
        if plan:
            if not flag("sales_open"):
                await q.edit_message_text("🔴 فروش در حال حاضر بسته است.")
                return
            p = dict(plan); p["price"] = vip_price(key, uid)
            await show_invoice(q, uid, p, key, "sub")

    # انتخاب بسته
    elif d.startswith("bundle_"):
        key = d  # مثلاً bundle_5x1gb
        plan = config.BUNDLE_PLANS.get(key)
        if plan:
            if not flag("sales_open"):
                await q.edit_message_text("🔴 فروش در حال حاضر بسته است.")
                return
            p = dict(plan); p["price"] = vip_price(key, uid)
            await show_invoice(q, uid, p, key, "bundle")

    elif d == "get_free_test":
        await do_free_test(q, uid, context)

    elif d.startswith("pay_card_"):
        key = d[9:]
        await do_card_payment(q, uid, key, context)

    elif d.startswith("pay_wallet_"):
        key = d[11:]
        await do_wallet_payment(q, uid, key, context)

    elif d.startswith("pay_crypto_"):
        rest = d[11:]
        coin, key = rest.split("_", 1)
        await do_crypto_payment(q, uid, coin, key, context)

    elif d == "cancel":
        cs(uid)
        await q.edit_message_text("❌ عملیات لغو شد.")

    elif d.startswith("disc_"):
        key = d[5:]
        parts = key.split("_", 1); ptype = parts[0]; plan_key = parts[1]
        ss(uid, {"w": "discount_code", "plan_key": plan_key, "ptype": ptype})
        await q.edit_message_text("🎫 کد تخفیف خود را وارد کنید:")

    elif d.startswith("ac_"):
        pay_id = int(d[3:])
        await admin_confirm(q, pay_id, context)

    elif d.startswith("ar_"):
        pay_id = int(d[3:])
        await admin_reject(q, pay_id, context)

    elif d.startswith("am_"):
        target = int(d[3:])
        if not is_admin(uid): return
        ss(uid, {"w": "a_send_cfg", "target": target, "mode": "msg"})
        await q.edit_message_text("✍️ پیام خود را بنویسید:")

    elif d.startswith("asc_"):
        pay_id = int(d[4:])
        if not is_admin(uid): return
        pay = db.get_payment(pay_id)
        if not pay: return
        ss(uid, {"w": "a_send_cfg", "target": pay["user_id"], "pay_id": pay_id, "mode": "cfg"})
        await q.edit_message_text(
            f"📦 کانفیگ را برای ارسال به کاربر `{pay['user_id']}` وارد کنید:",
            parse_mode="Markdown"
        )

    # پنل ادمین
    elif d == "a_back":
        await q.edit_message_text("🔧 *پنل مدیریت*", parse_mode="Markdown", reply_markup=admin_kb())
    elif d == "a_users":   await a_show_users(q)
    elif d == "a_pays":    await a_show_pays(q)
    elif d == "a_configs": await a_show_configs(q, uid)
    elif d == "a_prices":  await a_show_prices(q, uid)
    elif d == "a_crypto":  await a_show_crypto(q, uid)
    elif d == "a_card_menu":
        if not is_admin(uid): return
        await q.edit_message_text(
            f"💳 *اطلاعات کارت فعلی*\n\n"
            f"شماره: `{card()}`\nبه نام: {cardholder()}\n\nچه چیزی را تغییر می‌دهید؟",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔢 شماره کارت", callback_data="a_card"),
                 InlineKeyboardButton("👤 نام دارنده", callback_data="a_cardholder")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")],
            ])
        )
    elif d == "a_card":
        if not is_admin(uid): return
        ss(uid, {"w": "a_card"})
        await q.edit_message_text(f"💳 شماره کارت فعلی: `{card()}`\n\nشماره جدید را وارد کنید:", parse_mode="Markdown")
    elif d == "a_cardholder":
        if not is_admin(uid): return
        ss(uid, {"w": "a_cardholder"})
        await q.edit_message_text(f"👤 نام دارنده فعلی: {cardholder()}\n\nنام جدید را وارد کنید:")
    elif d == "a_admins":  await a_show_admins(q, uid)
    elif d == "a_add_admin":
        if not is_admin(uid): return
        ss(uid, {"w": "a_add_admin"})
        await q.edit_message_text("آیدی عددی کاربر جدید را وارد کنید:")
    elif d == "a_del_admin":
        if not is_admin(uid): return
        ss(uid, {"w": "a_del_admin"})
        await q.edit_message_text("آیدی عددی ادمین را برای حذف وارد کنید:")
    elif d == "a_balance":
        if not is_admin(uid): return
        ss(uid, {"w": "a_bal_uid"})
        await q.edit_message_text("آیدی عددی کاربر را وارد کنید:")
    elif d == "a_broadcast":
        if not is_admin(uid): return
        ss(uid, {"w": "a_broadcast"})
        await q.edit_message_text("📢 متن پیام همگانی را بنویسید:")
    elif d == "a_toggle_sales":
        if not is_admin(uid): return
        v = flag("sales_open"); db.set_setting("sales_open", "0" if v else "1")
        await q.edit_message_text(f"✅ فروش {'بسته' if v else 'باز'} شد.", reply_markup=back_kb())
    elif d == "a_toggle_card":
        if not is_admin(uid): return
        v = flag("card_open"); db.set_setting("card_open", "0" if v else "1")
        await q.edit_message_text(f"✅ پرداخت کارت {'بسته' if v else 'باز'} شد.", reply_markup=back_kb())
    elif d == "a_toggle_topup":
        if not is_admin(uid): return
        v = flag("topup_open"); db.set_setting("topup_open", "0" if v else "1")
        await q.edit_message_text(f"✅ افزایش موجودی {'بسته' if v else 'باز'} شد.", reply_markup=back_kb())
    elif d == "a_toggle_crypto":
        if not is_admin(uid): return
        v = flag("crypto_open"); db.set_setting("crypto_open", "0" if v else "1")
        await q.edit_message_text(f"✅ پرداخت ارزی {'بسته' if v else 'باز'} شد.", reply_markup=back_kb())
    elif d == "a_toggle_bot":
        if not is_admin(uid): return
        v = is_bot_enabled(); db.set_setting("bot_enabled", "0" if v else "1")
        await q.edit_message_text(f"✅ ربات {'خاموش' if v else 'روشن'} شد.", reply_markup=back_kb())

    # ─── صدور کارت تخفیف ───
    elif d == "a_discount":
        if not is_admin(uid): return
        codes = db.get_all_discount_codes()
        active = [c for c in codes if c["is_active"]]
        text = "🎫 *مدیریت کدهای تخفیف*\n\n"
        if active:
            for c in active:
                text += f"🟢 `{c['code']}` — {c['percent']}%  {c.get('note','')}\n"
        else:
            text += "هیچ کد فعالی وجود ندارد.\n"
        kb = [
            [InlineKeyboardButton("➕ صدور کد جدید", callback_data="a_new_discount")],
            [InlineKeyboardButton("🛑 پایان همه کدها", callback_data="a_end_all_discount")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")],
        ]
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    elif d == "a_new_discount":
        if not is_admin(uid): return
        ss(uid, {"w": "a_discount_code", "step": "percent"})
        await q.edit_message_text(
            "🎫 *صدور کد تخفیف جدید*\n\nابتدا درصد تخفیف را وارد کنید (مثلاً ۲۰):",
            parse_mode="Markdown"
        )
    elif d == "a_end_all_discount":
        if not is_admin(uid): return
        db.deactivate_all_discount_codes()
        await q.edit_message_text("✅ همه کدهای تخفیف غیرفعال شدند.", reply_markup=back_kb())
    elif d.startswith("a_end_discount_"):
        code = d[15:]
        if not is_admin(uid): return
        db.deactivate_discount_code(code)
        await q.edit_message_text(f"✅ کد `{code}` غیرفعال شد.", parse_mode="Markdown", reply_markup=back_kb())

    # ─── جستجوی کاربر ───
    elif d == "a_search_user_btn":
        if not is_admin(uid): return
        ss(uid, {"w": "a_search_user"})
        await q.edit_message_text("🔍 آیدی عددی کاربر مورد نظر را وارد کنید:")
    elif d.startswith("a_ban_"):
        tid = int(d[6:])
        if not is_admin(uid): return
        db.ban_user(tid)
        try: await context.bot.send_message(tid, "⛔️ حساب شما مسدود شد.")
        except: pass
        await q.edit_message_text(f"✅ کاربر `{tid}` مسدود شد.", parse_mode="Markdown", reply_markup=back_kb())
    elif d.startswith("a_unban_"):
        tid = int(d[8:])
        if not is_admin(uid): return
        db.unban_user(tid)
        try: await context.bot.send_message(tid, "✅ حساب شما رفع مسدودی شد.")
        except: pass
        await q.edit_message_text(f"✅ کاربر `{tid}` رفع مسدودی شد.", parse_mode="Markdown", reply_markup=back_kb())
    elif d.startswith("a_addbal_"):
        tid = int(d[9:])
        if not is_admin(uid): return
        ss(uid, {"w": "a_bal_amt", "tid": tid})
        t = db.get_user(tid)
        await q.edit_message_text(f"موجودی فعلی: {fmt(t['balance'])}\\nمقدار تغییر (مثبت/منفی):")
    elif d.startswith("a_msg_user_"):
        tid = int(d[11:])
        if not is_admin(uid): return
        ss(uid, {"w": "a_send_cfg", "target": tid, "mode": "msg"})
        await q.edit_message_text("✍️ پیام خود را بنویسید:")
    elif d.startswith("a_addsub_"):
        tid = int(d[9:])
        if not is_admin(uid): return
        await q.edit_message_text(
            f"برای افزودن اشتراک به کاربر `{tid}`، از دکمه «📤 ارسال کانفیگ دستی» در فاکتور مربوطه استفاده کنید.",
            parse_mode="Markdown", reply_markup=back_kb()
        )

    # ─── تنظیمات VIP ───
    elif d == "a_vip_settings":
        if not is_admin(uid): return
        tiers = config.VIP_TIERS
        text = "👑 *تنظیمات VIP*\n\n"
        for i, t in enumerate(tiers):
            text += f"{i+1}. {t['label']}\n   حداقل شارژ: {fmt(t['min'])}\n   تخفیف: {t['discount']}%\n\n"
        kb = [[InlineKeyboardButton(f"✏️ {t['label']}", callback_data=f"a_edit_vip_{i}")] for i, t in enumerate(tiers)]
        kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    # ─── مدیریت کانال‌های اجباری ───
    elif d == "a_channels":
        if not is_admin(uid): return
        channels = get_all_channels()
        text = "📢 *کانال‌های اجباری*\n\n"
        for ch in channels:
            text += f"• `{ch}`\n"
        if not channels:
            text += "_هیچ کانالی تنظیم نشده_\n"
        kb = [
            [InlineKeyboardButton("➕ افزودن کانال", callback_data="a_add_channel"),
             InlineKeyboardButton("➖ حذف کانال", callback_data="a_del_channel")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")],
        ]
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    elif d == "a_add_channel":
        if not is_admin(uid): return
        ss(uid, {"w": "a_add_channel"})
        await q.edit_message_text(
            "📢 *افزودن کانال اجباری*\n\nیوزرنیم کانال را وارد کنید (مثال: @mychannel):",
            parse_mode="Markdown"
        )
    elif d == "a_del_channel":
        if not is_admin(uid): return
        channels = db.get_forced_channels()
        if not channels:
            await q.edit_message_text("⚠️ کانال اضافه‌ای برای حذف وجود ندارد.\n(کانال پیش‌فرض قابل حذف نیست)",
                reply_markup=back_kb())
            return
        kb = [[InlineKeyboardButton(f"🗑 {ch}", callback_data=f"a_delch_{ch}")] for ch in channels]
        kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="a_channels")])
        await q.edit_message_text("کدام کانال را حذف کنیم؟", reply_markup=InlineKeyboardMarkup(kb))
    elif d.startswith("a_delch_"):
        if not is_admin(uid): return
        ch = d[8:]
        db.remove_forced_channel(ch)
        await q.edit_message_text(f"✅ کانال `{ch}` از لیست اجباری حذف شد.", parse_mode="Markdown",
            reply_markup=back_kb())

    # ─── قرعه‌کشی ───
    elif d == "a_lottery":
        if not is_admin(uid): return
        ids = db.get_all_user_ids()
        if not ids:
            await q.edit_message_text("⚠️ هیچ کاربری در بات وجود ندارد.", reply_markup=back_kb())
            return
        import random as _rnd
        winner_id = _rnd.choice(ids)
        winner = db.get_user(winner_id)
        name = winner.get("full_name", "") if winner else ""
        uname = winner.get("username", "") if winner else ""
        uname_str = f"@{uname}" if uname else "ندارد"
        text = (
            f"🎰 *نتیجه قرعه‌کشی*\n\n"
            f"👤 نام: {name}\n"
            f"🆔 آیدی: `{winner_id}`\n"
            f"📲 یوزرنیم: {uname_str}\n\n"
            f"از بین *{len(ids)}* کاربر انتخاب شد."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 قرعه‌کشی مجدد", callback_data="a_lottery")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")],
        ])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    elif d.startswith("a_edit_vip_"):
        idx = int(d[11:])
        if not is_admin(uid): return
        ss(uid, {"w": "a_vip_tier", "idx": idx, "step": "discount"})
        t = config.VIP_TIERS[idx]
        await q.edit_message_text(
            f"✏️ *ویرایش {t['label']}*\n\nدرصد تخفیف جدید را وارد کنید (فعلی: {t['discount']}%):",
            parse_mode="Markdown"
        )
    elif d.startswith("a_addcfg_"):
        plan_key = d[9:]
        if not is_admin(uid): return
        ss(uid, {"w": "a_configs", "plan_key": plan_key})
        await q.edit_message_text(
            f"📦 *افزودن کانفیگ — {PLAN_LABELS.get(plan_key, plan_key)}*\n\nهر کانفیگ را در یک خط جداگانه بنویسید:",
            parse_mode="Markdown"
        )
    elif d.startswith("a_setprice_"):
        key = d[11:]
        if not is_admin(uid): return
        ss(uid, {"w": "a_price", "key": key})
        cur = price(key)
        await q.edit_message_text(f"قیمت فعلی «{PLAN_LABELS.get(key,key)}»: {fmt(cur)}\n\nقیمت جدید (تومان):")
    elif d.startswith("a_set_crypto_rate_"):
        coin = d[18:]
        if not is_admin(uid): return
        ss(uid, {"w": "a_crypto_rate", "coin": coin})
        cur = crypto_rate(coin)
        cinfo = config.CRYPTO_WALLETS.get(coin, {})
        await q.edit_message_text(
            f"{cinfo.get('emoji','💎')} نرخ فعلی {cinfo.get('name', coin)}: {cur:,} تومان\n\n"
            f"نرخ جدید را وارد کنید (تومان به ازای ۱ {cinfo.get('symbol',coin)}):",
        )
    elif d.startswith("a_set_crypto_wallet_"):
        coin = d[20:]
        if not is_admin(uid): return
        ss(uid, {"w": "a_crypto_wallet", "coin": coin})
        cur = crypto_wallet(coin)
        cinfo = config.CRYPTO_WALLETS.get(coin, {})
        await q.edit_message_text(
            f"{cinfo.get('emoji','💎')} آدرس فعلی {cinfo.get('name', coin)}:\n`{cur}`\n\nآدرس جدید را وارد کنید:",
            parse_mode="Markdown"
        )

# ── Discount code (admin input) ───────────────────────────

async def a_recv_discount_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = gs(uid)
    step = state.get("step", "percent")
    text = update.message.text.strip()

    if step == "percent":
        try:
            pct = int(text)
            if not 1 <= pct <= 99:
                await update.message.reply_text("⚠️ درصد باید بین ۱ تا ۹۹ باشد."); return
            ss(uid, {"w": "a_discount_code", "step": "note", "percent": pct})
            await update.message.reply_text(
                f"✅ درصد تخفیف: {pct}%\n\nحالا متن/توضیح کد را بنویسید (مثلاً: «نوروز ۱۴۰۴»):\n"
                "یا عدد ۰ بزنید تا بدون توضیح باشد:"
            )
        except ValueError:
            await update.message.reply_text("⚠️ عدد وارد کنید.")
    elif step == "note":
        note = "" if text == "0" else text
        pct = state.get("percent", 10)
        ss(uid, {"w": "a_discount_code", "step": "code", "percent": pct, "note": note})
        await update.message.reply_text(
            f"✅ توضیح ثبت شد.\n\nحالا کد تخفیف را وارد کنید (حروف/اعداد انگلیسی، مثلاً NOROUZ1404):\n"
            "یا عدد ۰ بزنید تا کد تصادفی ساخته شود:"
        )
    elif step == "code":
        import random, string as _str
        if text == "0":
            code = ''.join(random.choices(_str.ascii_uppercase + _str.digits, k=8))
        else:
            code = text.upper()
        pct = state.get("percent", 10)
        note = state.get("note", "")
        db.create_discount_code(code, pct, note)
        cs(uid)
        await update.message.reply_text(
            f"✅ *کد تخفیف صادر شد!*\n\n"
            f"🎫 کد: `{code}`\n"
            f"💸 تخفیف: {pct}%\n"
            f"📝 توضیح: {note or '—'}",
            parse_mode="Markdown", reply_markup=main_kb(uid)
        )

async def a_recv_discount_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    code = update.message.text.strip().upper()
    db.deactivate_discount_code(code)
    cs(uid)
    await update.message.reply_text(f"✅ کد `{code}` غیرفعال شد.", parse_mode="Markdown", reply_markup=main_kb(uid))

# ── Discount code (user input) ────────────────────────────

async def recv_discount_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = gs(uid)
    code = update.message.text.strip().upper()
    plan_key = state.get("plan_key")
    ptype = state.get("ptype", "sub")

    dc = db.get_discount_code(code)
    if not dc:
        await update.message.reply_text("❌ کد تخفیف معتبر نیست یا منقضی شده.", reply_markup=main_kb(uid))
        cs(uid); return

    base = vip_price(plan_key, uid) if plan_key else 0
    discounted = int(base * (100 - dc["percent"]) / 100)
    cs(uid)
    if ptype == "bundle":
        plan = config.BUNDLE_PLANS.get(plan_key)
    else:
        plan = config.PLANS.get(plan_key) or config.TEST_PLANS.get(plan_key)
    if not plan:
        await update.message.reply_text("❌ خطا.", reply_markup=main_kb(uid)); return
    u = db.get_user(uid)
    bal = u["balance"] if u else 0
    key = f"{ptype}_{plan_key}"
    text = (
        f"🧾 *فاکتور خرید — xservpn*\n\n"
        f"📦 پلن: {plan['name']}\n"
        f"💵 قیمت اصلی: {fmt(base)}\n"
        f"🎫 کد تخفیف: `{code}` ({dc['percent']}% تخفیف)\n"
        f"💵 مبلغ نهایی: *{fmt(discounted)}*\n"
        f"💰 موجودی شما: {fmt(bal)}\n\nروش پرداخت را انتخاب کنید:"
    )
    kb = [
        [InlineKeyboardButton("💳 پرداخت با کارت", callback_data=f"pay_disc_card_{code}_{key}")],
        [InlineKeyboardButton(f"💰 پرداخت با موجودی {'✅' if bal>=discounted else '(ناکافی)'}", callback_data=f"pay_disc_wallet_{code}_{key}")],
        [InlineKeyboardButton("❌ لغو", callback_data="cancel")],
    ]
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# ── Search user (admin) ───────────────────────────────────

async def a_recv_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        tid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("⚠️ آیدی باید عدد باشد."); return
    cs(uid)
    t = db.get_user(tid)
    if not t:
        await update.message.reply_text("⚠️ کاربر یافت نشد."); return

    subs = db.get_user_subscriptions(tid)
    tier = config.get_vip_tier(t.get("total_topup", 0))
    vip_line = f"\n👑 VIP: {tier['label']} ({tier['discount']}%)" if tier else ""
    banned = "⛔️ مسدود" if t.get("is_banned") else "✅ فعال"

    text = (
        f"🔍 *اطلاعات کاربر*\n\n"
        f"📛 نام: {escape_md(t['full_name'])}\n"
        f"🆔 آیدی: `{tid}`\n"
        f"🔗 یوزرنیم: {'@' + escape_md(t['username']) if t.get('username') else '—'}\n"
        f"💰 موجودی: {fmt(t['balance'])}\n"
        f"💳 مجموع شارژ: {fmt(t.get('total_topup', 0))}{vip_line}\n"
        f"👥 دعوت‌ها: {t['referral_count']}\n"
        f"🔒 وضعیت: {banned}\n"
        f"📅 عضویت: {t.get('joined_at','')[:10]}\n"
        f"📦 اشتراک‌ها: {len(subs)} عدد"
    )
    ban_btn = InlineKeyboardButton("✅ رفع مسدودی", callback_data=f"a_unban_{tid}") if t.get("is_banned") else InlineKeyboardButton("⛔️ مسدود کردن", callback_data=f"a_ban_{tid}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 تغییر موجودی", callback_data=f"a_addbal_{tid}"),
         ban_btn],
        [InlineKeyboardButton("✉️ پیام مستقیم", callback_data=f"a_msg_user_{tid}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")],
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

# ── VIP tier edit (admin) ─────────────────────────────────

async def a_recv_vip_tier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = gs(uid)
    idx = state.get("idx", 0)
    step = state.get("step", "discount")
    text_in = update.message.text.strip()

    if step == "discount":
        try:
            pct = int(text_in)
            ss(uid, {"w": "a_vip_tier", "idx": idx, "step": "min", "discount": pct})
            await update.message.reply_text(
                f"✅ درصد: {pct}%\n\nحالا حداقل شارژ را وارد کنید (تومان، فعلی: {fmt(config.VIP_TIERS[idx]['min'])}):"
            )
        except ValueError:
            await update.message.reply_text("⚠️ عدد وارد کنید.")
    elif step == "min":
        try:
            min_val = int(text_in.replace(",", ""))
            pct = state.get("discount", config.VIP_TIERS[idx]["discount"])
            config.VIP_TIERS[idx]["discount"] = pct
            config.VIP_TIERS[idx]["min"] = min_val
            db.set_setting(f"vip_tier_{idx}_discount", str(pct))
            db.set_setting(f"vip_tier_{idx}_min", str(min_val))
            cs(uid)
            await update.message.reply_text(
                f"✅ سطح VIP «{config.VIP_TIERS[idx]['label']}» به‌روز شد:\n"
                f"   درصد تخفیف: {pct}%\n"
                f"   حداقل شارژ: {fmt(min_val)}",
                reply_markup=main_kb(uid)
            )
        except ValueError:
            await update.message.reply_text("⚠️ عدد وارد کنید.")

# ── Plans / Invoice ───────────────────────────────────────

async def show_plans(update, context, plans, ptype):
    if not flag("sales_open"):
        await update.message.reply_text("🔴 فروش در حال حاضر بسته است.")
        return
    uid = update.effective_user.id
    kb = []
    for key, plan in plans.items():
        p = vip_price(key, uid)
        kb.append([InlineKeyboardButton(f"📦 {plan['name']} — {fmt(p)}", callback_data=f"plan_{key}")])
    kb.append([InlineKeyboardButton("❌ لغو", callback_data="cancel")])
    badge = vip_badge(uid)
    await update.message.reply_text(
        f"📋 *پلن‌های اشتراک xservpn*{badge}\n\nیکی از پلن‌های زیر را انتخاب کنید:",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def show_bundles(update, context):
    """نمایش پلن‌های بسته‌ای"""
    if not flag("sales_open"):
        await update.message.reply_text("🔴 فروش در حال حاضر بسته است.")
        return
    uid = update.effective_user.id
    kb = []
    for key, plan in config.BUNDLE_PLANS.items():
        p = vip_price(key, uid)
        normal = price(key)
        saving = normal - p
        label = f"📦 {plan['name']} — {fmt(p)}"
        if saving > 0:
            label += f" (صرفه‌جویی {fmt(saving)})"
        kb.append([InlineKeyboardButton(label, callback_data=key)])
    kb.append([InlineKeyboardButton("❌ لغو", callback_data="cancel")])
    badge = vip_badge(uid)
    await update.message.reply_text(
        f"📦 *خرید بسته‌ای xservpn*{badge}\n\nبا خرید بسته‌ای صرفه‌جویی بیشتری دارید:",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def show_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """صفحه VIP"""
    uid = update.effective_user.id
    u = db.get_user(uid)
    total = u.get("total_topup", 0) if u else 0
    tier = config.get_vip_tier(total)

    lines = [f"👑 *باشگاه VIP — xservpn*\n"]
    if tier:
        lines.append(f"✅ شما عضو {tier['label']} هستید!\n🎁 {tier['discount']}% تخفیف روی تمام پلن‌ها\n")
    else:
        lines.append("شما هنوز عضو VIP نیستید.\n")

    lines.append(f"💰 مجموع شارژ شما: {fmt(total)}\n")
    lines.append("─────────────────")
    for t in config.VIP_TIERS:
        remaining = max(0, t["min"] - total)
        if remaining > 0:
            lines.append(f"\n{t['label']} — {t['discount']}% تخفیف\n   حداقل شارژ: {fmt(t['min'])}\n   باقیمانده: {fmt(remaining)}")
        else:
            lines.append(f"\n{t['label']} — ✅ فعال")

    lines.append("\n─────────────────\n💡 با افزایش موجودی می‌توانید به سطح VIP برسید.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb(uid))

async def show_invoice(q, uid, plan, plan_key, ptype):
    u = db.get_user(uid)
    bal = u["balance"] if u else 0
    p = plan["price"]
    text = (
        f"🧾 *فاکتور خرید — xservpn*\n\n"
        f"📦 پلن: {plan['name']}\n"
        f"📊 حجم: {plan['size']}\n"
    )
    if plan.get("duration"):
        text += f"⏱ مدت: {plan['duration']}\n"
    text += f"💵 مبلغ: *{fmt(p)}*\n💰 موجودی شما: {fmt(bal)}\n\nروش پرداخت را انتخاب کنید:"

    key = f"{ptype}_{plan_key}"
    kb = [
        [InlineKeyboardButton("💳 پرداخت با کارت" + ("" if flag("card_open") else " (غیرفعال)"),
                              callback_data=f"pay_card_{key}")],
        [InlineKeyboardButton(f"💰 پرداخت با موجودی {'✅' if bal>=p else '(ناکافی)'}",
                              callback_data=f"pay_wallet_{key}")],
    ]
    if flag("crypto_open"):
        for coin, cinfo in config.CRYPTO_WALLETS.items():
            rate = crypto_rate(coin)
            if rate > 0:
                crypto_amount = round(p / rate, 4)
                kb.append([InlineKeyboardButton(
                    f"{cinfo['emoji']} پرداخت با {cinfo['symbol']} ({crypto_amount} {cinfo['symbol']})",
                    callback_data=f"pay_crypto_{coin}_{key}"
                )])
    kb.append([InlineKeyboardButton("🎫 دارم کد تخفیف", callback_data=f"disc_{key}")])
    kb.append([InlineKeyboardButton("❌ لغو", callback_data="cancel")])
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def do_card_payment(q, uid, key, context):
    if not flag("card_open"):
        await q.edit_message_text("🔴 پرداخت کارت به کارت غیرفعال است.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ بستن", callback_data="cancel")]]))
        return
    parts = key.split("_", 1); ptype = parts[0]; plan_key = parts[1]
    if ptype == "bundle":
        plan = config.BUNDLE_PLANS.get(plan_key)
    else:
        plan = (config.PLANS if ptype == "sub" else config.TEST_PLANS).get(plan_key)
    if not plan: return
    p = vip_price(plan_key, uid)
    pay_id, inv = db.create_payment(uid, p, ptype, plan_key, plan["name"], pay_method="card")
    ss(uid, {"w": "receipt", "pay_id": pay_id, "plan_key": plan_key, "plan_name": plan["name"], "ptype": ptype})
    await q.edit_message_text(
        f"💳 *اطلاعات پرداخت — xservpn*\n\n"
        f"🔖 کد فاکتور: `{inv}`\n"
        f"📦 پلن: {plan['name']}\n"
        f"💵 مبلغ: *{fmt(p)}*\n\n"
        f"شماره کارت:\n`{card()}`\n"
        f"به نام: {cardholder()}\n\n"
        f"⏰ *{config.PAYMENT_TIMEOUT_MINUTES} دقیقه* فرصت دارید.\n"
        f"پس از واریز، تصویر رسید را ارسال کنید.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel")]])
    )
async def do_wallet_payment(q, uid, key, context):
    parts = key.split("_", 1); ptype = parts[0]; plan_key = parts[1]
    if ptype == "bundle":
        plan = config.BUNDLE_PLANS.get(plan_key)
    else:
        plan = (config.PLANS if ptype == "sub" else config.TEST_PLANS).get(plan_key)
    if not plan: return
    p = vip_price(plan_key, uid)
    u = db.get_user(uid)
    if not u or u["balance"] < p:
        await q.edit_message_text("❌ موجودی کافی نیست.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ بستن", callback_data="cancel")]]))
        return

    db.update_balance(uid, -p)
    pay_id, inv = db.create_payment(uid, p, ptype, plan_key, plan["name"], pay_method="wallet")
    # بررسی هشدار موجودی VIP بعد از کسر
    asyncio.create_task(check_vip_balance_warning(context.bot, uid))

    if ptype == "bundle":
        count = plan.get("count", 1)
        inner_key = plan.get("plan_key", plan_key)
        cfgs = db.assign_configs_bulk(inner_key, uid, count)
        db.confirm_payment(pay_id, "\n".join(cfgs) if cfgs else "")
        db.create_subscription(uid, pay_id, plan_key, plan["name"], plan["size"], p, "\n".join(cfgs) if cfgs else "")
        u = db.get_user(uid)
        if cfgs:
            try:
                await context.bot.send_message(uid,
                    f"✅ *خرید بسته موفق — xservpn!*\n\n🔖 فاکتور: `{inv}`\n📦 {plan['name']}\n\n"
                    f"کانفیگ‌های شما:",
                    parse_mode="Markdown")
                for cfg in cfgs:
                    await context.bot.send_message(uid, cfg)
            except Exception: pass
        else:
            try:
                await context.bot.send_message(uid,
                    f"✅ پرداخت دریافت شد.\n🔖 فاکتور: `{inv}`\nکانفیگ‌ها به زودی ارسال می‌شوند.",
                    parse_mode="Markdown")
            except Exception: pass
        await q.edit_message_text(
            f"✅ خرید بسته انجام شد.\n🔖 فاکتور: `{inv}`\n"
            f"{'کانفیگ‌ها در پیام‌های بعدی ارسال شدند.' if cfgs else 'به زودی ارسال می‌شود.'}",
            parse_mode="Markdown")
        return

    cfg = db.assign_config(plan_key, uid)
    db.confirm_payment(pay_id, cfg or "")
    db.create_subscription(uid, pay_id, plan_key, plan["name"], plan["size"], p, cfg or "")
    u = db.get_user(uid)

    if cfg:
        try:
            await context.bot.send_message(uid,
                f"✅ *خرید موفق — xservpn!*\n\n🔖 فاکتور: `{inv}`\n📦 {plan['name']}",
                parse_mode="Markdown")
            await context.bot.send_message(uid, cfg)
        except Exception: pass
        msg_to_admin = (
            f"🛍 *خرید با موجودی — کانفیگ ارسال شد*\n\n"
            f"{uinfo(u)}\n\n"
            f"🔖 فاکتور: `{inv}`\n📦 {plan['name']}\n💵 {fmt(p)}\n✅ کانفیگ ارسال شد"
        )
    else:
        try:
            await context.bot.send_message(uid,
                f"✅ پرداخت شما دریافت شد.\n🔖 فاکتور: `{inv}`\n\nکانفیگ شما به زودی ارسال خواهد شد.",
                parse_mode="Markdown")
        except Exception: pass
        msg_to_admin = (
            f"🛍 *خرید با موجودی — نیاز به ارسال کانفیگ*\n\n"
            f"{uinfo(u)}\n\n"
            f"🔖 فاکتور: `{inv}`\n📦 {plan['name']}\n💵 {fmt(p)}\n⚠️ کانفیگ موجود نبود"
        )

    for aid in all_admins():
        try:
            await context.bot.send_message(aid, msg_to_admin, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 ارسال کانفیگ دستی", callback_data=f"asc_{pay_id}")],
                    [InlineKeyboardButton("✉️ پیام مستقیم", callback_data=f"am_{uid}")],
                ]))
        except Exception: pass

    await q.edit_message_text(
        f"✅ پرداخت انجام شد.\n🔖 فاکتور: `{inv}`\n{'کانفیگ در پیام بعدی ارسال شد.' if cfg else 'کانفیگ شما به زودی ارسال می‌شود.'}",
        parse_mode="Markdown"
    )

async def do_crypto_payment(q, uid, coin, key, context):
    if not flag("crypto_open"):
        await q.edit_message_text("🔴 پرداخت ارزی در حال حاضر غیرفعال است.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ بستن", callback_data="cancel")]]))
        return
    rate = crypto_rate(coin)
    if rate <= 0:
        await q.edit_message_text("⚠️ نرخ این ارز تنظیم نشده است. با پشتیبانی تماس بگیرید.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ بستن", callback_data="cancel")]]))
        return
    parts = key.split("_", 1); ptype = parts[0]; plan_key = parts[1]
    if ptype == "bundle":
        plan = config.BUNDLE_PLANS.get(plan_key)
    else:
        plan = (config.PLANS if ptype == "sub" else config.TEST_PLANS).get(plan_key)
    if not plan: return
    p = vip_price(plan_key, uid)
    crypto_amount = round(p / rate, 4)
    cinfo = config.CRYPTO_WALLETS.get(coin, {})
    wallet_addr = crypto_wallet(coin)

    pay_id, inv = db.create_payment(uid, p, ptype, plan_key, plan["name"], pay_method="crypto", crypto_coin=coin)
    ss(uid, {"w": "crypto_receipt", "pay_id": pay_id, "plan_key": plan_key, "plan_name": plan["name"], "ptype": ptype, "coin": coin})

    await q.edit_message_text(
        f"{cinfo.get('emoji','💎')} *پرداخت با {cinfo.get('name', coin)} — xservpn*\n\n"
        f"🔖 کد فاکتور: `{inv}`\n"
        f"📦 پلن: {plan['name']}\n"
        f"💵 معادل تومانی: {fmt(p)}\n"
        f"💱 مبلغ ارزی: *{crypto_amount} {cinfo.get('symbol', coin)}*\n\n"
        f"📬 آدرس کیف پول:\n`{wallet_addr}`\n\n"
        f"⚠️ دقیقاً همین مقدار را به این آدرس واریز کنید.\n"
        f"⏰ *{config.PAYMENT_TIMEOUT_MINUTES} دقیقه* فرصت دارید.\n"
        f"پس از واریز، اسکرین‌شات یا هش تراکنش را ارسال کنید.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel")]])
    )
# ── Receipt ───────────────────────────────────────────────

async def recv_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = gs(uid)
    pay_id = state.get("pay_id")
    if not pay_id: return

    pay = db.get_payment(pay_id)
    if not pay or pay["status"] in ("cancelled", "confirmed"):
        cs(uid)
        await update.message.reply_text("⚠️ این سفارش منقضی یا لغو شده است.", reply_markup=main_kb(uid))
        return

    msg = update.message
    if msg.photo:
        file_id = msg.photo[-1].file_id; is_photo = True
    elif msg.document:
        file_id = msg.document.file_id; is_photo = False
    else:
        await msg.reply_text("لطفاً تصویر رسید را به صورت عکس یا فایل ارسال کنید.")
        return

    db.set_receipt(pay_id, file_id, is_photo)
    plan_name = state.get("plan_name", "")
    u = db.get_user(uid)
    cs(uid)

    await msg.reply_text("✅ رسید شما دریافت شد.\nپس از تایید، کانفیگ برایتان ارسال می‌شود.", reply_markup=main_kb(uid))

    caption = (
        f"🧾 *رسید پرداخت جدید — xservpn*\n\n"
        f"{uinfo(u)}\n\n"
        f"🔖 فاکتور: `{pay['invoice_code']}`\n"
        f"📦 پلن: {plan_name}\n"
        f"💵 مبلغ: {fmt(pay['amount'])}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید + ارسال کانفیگ", callback_data=f"ac_{pay_id}"),
         InlineKeyboardButton("❌ رد", callback_data=f"ar_{pay_id}")],
        [InlineKeyboardButton("📤 تایید + کانفیگ دستی", callback_data=f"asc_{pay_id}")],
        [InlineKeyboardButton("✉️ پیام مستقیم", callback_data=f"am_{uid}")],
    ])
    for aid in all_admins():
        try:
            if is_photo:
                await context.bot.send_photo(aid, photo=file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
            else:
                await context.bot.send_document(aid, document=file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            logger.error(f"send to admin {aid} failed: {e}")

async def recv_crypto_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = gs(uid)
    pay_id = state.get("pay_id")
    if not pay_id: return

    pay = db.get_payment(pay_id)
    if not pay or pay["status"] in ("cancelled", "confirmed"):
        cs(uid)
        await update.message.reply_text("⚠️ این سفارش منقضی یا لغو شده است.", reply_markup=main_kb(uid))
        return

    msg = update.message
    coin = state.get("coin", "")
    cinfo = config.CRYPTO_WALLETS.get(coin, {})

    file_id = None
    is_photo = False
    tx_hash = ""

    if msg.photo:
        file_id = msg.photo[-1].file_id; is_photo = True
    elif msg.document:
        file_id = msg.document.file_id; is_photo = False
    elif msg.text and len(msg.text.strip()) > 10:
        tx_hash = msg.text.strip()
    else:
        await msg.reply_text("لطفاً اسکرین‌شات یا هش تراکنش را ارسال کنید."); return

    if file_id:
        db.set_receipt(pay_id, file_id, is_photo)

    plan_name = state.get("plan_name", "")
    u = db.get_user(uid)
    cs(uid)

    await msg.reply_text(
        f"✅ رسید پرداخت {cinfo.get('name', coin)} دریافت شد.\nپس از تایید، کانفیگ برایتان ارسال می‌شود.",
        reply_markup=main_kb(uid)
    )

    rate = crypto_rate(coin)
    crypto_amount = round(pay['amount'] / rate, 4) if rate > 0 else "?"
    caption = (
        f"💎 *رسید پرداخت ارزی — xservpn*\n\n"
        f"{uinfo(u)}\n\n"
        f"🔖 فاکتور: `{pay['invoice_code']}`\n"
        f"📦 پلن: {plan_name}\n"
        f"💵 معادل تومانی: {fmt(pay['amount'])}\n"
        f"{cinfo.get('emoji','💎')} مبلغ ارزی: {crypto_amount} {cinfo.get('symbol', coin)}\n"
        f"🪙 ارز: {cinfo.get('name', coin)}"
    )
    if tx_hash:
        caption += f"\n🔗 تراکنش: {tx_hash}"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید + ارسال کانفیگ", callback_data=f"ac_{pay_id}"),
         InlineKeyboardButton("❌ رد", callback_data=f"ar_{pay_id}")],
        [InlineKeyboardButton("📤 تایید + کانفیگ دستی", callback_data=f"asc_{pay_id}")],
        [InlineKeyboardButton("✉️ پیام مستقیم", callback_data=f"am_{uid}")],
    ])
    for aid in all_admins():
        try:
            if file_id:
                if is_photo:
                    await context.bot.send_photo(aid, photo=file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
                else:
                    await context.bot.send_document(aid, document=file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
            else:
                await context.bot.send_message(aid, caption, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            logger.error(f"send crypto receipt to admin {aid} failed: {e}")

# ── Admin confirm/reject ──────────────────────────────────

async def admin_confirm(q, pay_id, context):
    if not is_admin(q.from_user.id): return
    pay = db.get_payment(pay_id)
    if not pay:
        try: await q.edit_message_caption("⚠️ پرداخت یافت نشد.")
        except: await q.edit_message_text("⚠️ پرداخت یافت نشد.")
        return

    if pay["purpose"] == "topup":
        db.update_balance(pay["user_id"], pay["amount"])
        db.add_topup(pay["user_id"], pay["amount"])   # VIP tracking
        db.confirm_payment(pay_id)
        u = db.get_user(pay["user_id"])
        tier = config.get_vip_tier(u.get("total_topup", 0)) if u else None
        vip_msg = f"\n\n{tier['label']} — {tier['discount']}% تخفیف فعال شد! 🎉" if tier else ""
        try:
            await context.bot.send_message(pay["user_id"],
                f"✅ موجودی کیف پول شما {fmt(pay['amount'])} افزایش یافت.{vip_msg}")
        except Exception: pass
        try: await q.edit_message_caption(f"✅ شارژ `{pay['invoice_code']}` تایید شد.", parse_mode="Markdown")
        except: await q.edit_message_text(f"✅ شارژ `{pay['invoice_code']}` تایید شد.", parse_mode="Markdown")
        return

    # بسته‌ای
    if pay["purpose"] == "bundle":
        bplan = config.BUNDLE_PLANS.get(pay["plan_key"])
        if bplan:
            count = bplan.get("count", 1)
            inner_key = bplan.get("plan_key", pay["plan_key"])
            cfgs = db.assign_configs_bulk(inner_key, pay["user_id"], count)
            db.confirm_payment(pay_id, "\n".join(cfgs) if cfgs else "")
            db.create_subscription(pay["user_id"], pay_id, pay["plan_key"], pay["plan_name"], bplan["size"], pay["amount"], "\n".join(cfgs) if cfgs else "")
            if cfgs:
                try:
                    await context.bot.send_message(pay["user_id"],
                        f"✅ *خرید بسته موفق — xservpn!*\n\n🔖 فاکتور: `{pay['invoice_code']}`\n📦 {pay['plan_name']}\n\nکانفیگ‌های شما:",
                        parse_mode="Markdown")
                    for cfg in cfgs:
                        await context.bot.send_message(pay["user_id"], cfg)
                except Exception: pass
                result_text = f"✅ تایید شد — {len(cfgs)} کانفیگ ارسال شد\nفاکتور: `{pay['invoice_code']}`"
            else:
                try:
                    await context.bot.send_message(pay["user_id"],
                        f"✅ پرداخت تایید شد.\nکانفیگ‌ها به زودی ارسال می‌شوند.", parse_mode="Markdown")
                except Exception: pass
                result_text = f"✅ تایید شد — ⚠️ کانفیگ ناکافی\nفاکتور: `{pay['invoice_code']}`"
            try: await q.edit_message_caption(result_text, parse_mode="Markdown")
            except: await q.edit_message_text(result_text, parse_mode="Markdown")
            return

    cfg = db.assign_config(pay["plan_key"], pay["user_id"])
    db.confirm_payment(pay_id, cfg or "")
    db.create_subscription(pay["user_id"], pay_id, pay["plan_key"], pay["plan_name"], "", pay["amount"], cfg or "")

    if cfg:
        try:
            await context.bot.send_message(pay["user_id"],
                f"✅ *خرید موفق — xservpn!*\n\n🔖 فاکتور: `{pay['invoice_code']}`\n📦 {pay['plan_name']}",
                parse_mode="Markdown")
            await context.bot.send_message(pay["user_id"], cfg)
        except Exception: pass
        result_text = f"✅ تایید شد — کانفیگ ارسال شد\nفاکتور: `{pay['invoice_code']}`"
    else:
        try:
            await context.bot.send_message(pay["user_id"],
                f"✅ پرداخت تایید شد.\n🔖 فاکتور: `{pay['invoice_code']}`\nکانفیگ شما به زودی ارسال می‌شود.",
                parse_mode="Markdown")
        except Exception: pass
        result_text = f"✅ تایید شد — ⚠️ کانفیگ موجود نبود\nفاکتور: `{pay['invoice_code']}`"

    try: await q.edit_message_caption(result_text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📤 ارسال کانفیگ دستی", callback_data=f"asc_{pay_id}")]]))
    except: await q.edit_message_text(result_text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📤 ارسال کانفیگ دستی", callback_data=f"asc_{pay_id}")]]))

async def admin_reject(q, pay_id, context):
    if not is_admin(q.from_user.id): return
    pay = db.get_payment(pay_id)
    db.cancel_payment(pay_id)
    if pay:
        try:
            await context.bot.send_message(pay["user_id"],
                f"❌ پرداخت شما (فاکتور `{pay['invoice_code']}`) تایید نشد.\nلطفاً با پشتیبانی تماس بگیرید.",
                parse_mode="Markdown")
        except Exception: pass
    try: await q.edit_message_caption(f"❌ رد شد — فاکتور `{pay['invoice_code']}`", parse_mode="Markdown")
    except: await q.edit_message_text(f"❌ رد شد — فاکتور `{pay['invoice_code']}`", parse_mode="Markdown")

# ── Admin send config manually ────────────────────────────

async def a_recv_send_cfg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = gs(uid)
    target = state.get("target")
    mode = state.get("mode")
    pay_id = state.get("pay_id")
    text = update.message.text.strip()
    cs(uid)

    if mode == "cfg":
        try:
            await context.bot.send_message(target,
                f"✅ *سابسکریپشن شما — xservpn!*",
                parse_mode="Markdown")
            await context.bot.send_message(target, text)
        except Exception:
            await update.message.reply_text("❌ ارسال ناموفق بود.", reply_markup=main_kb(uid))
            return
        if pay_id:
            db.confirm_payment(pay_id, text)
        await update.message.reply_text("✅ کانفیگ با موفقیت ارسال شد.", reply_markup=main_kb(uid))
    else:
        try:
            await context.bot.send_message(target, f"📨 *پیام از پشتیبانی xservpn:*\n\n{text}", parse_mode="Markdown")
            await update.message.reply_text("✅ پیام ارسال شد.", reply_markup=main_kb(uid))
        except Exception:
            await update.message.reply_text("❌ ارسال ناموفق.", reply_markup=main_kb(uid))

# ── Referral ──────────────────────────────────────────────

async def show_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = db.get_user(uid)
    if not u: return
    bot_info = await context.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={u['referral_code']}"
    rem = max(0, config.REFERRAL_THRESHOLD - u["referral_count"])

    # چک عضویت در کانال رفرال
    in_channel = await is_member(context.bot, uid)

    ch = config.REFERRAL_CHANNEL
    channel_status = "✅ عضو هستید" if in_channel else f"❌ باید عضو {ch} شوید"

    await update.message.reply_text(
        f"👥 *زیرمجموعه‌گیری — xservpn*\n\n"
        f"📢 کانال: {ch}\n"
        f"وضعیت عضویت: {channel_status}\n\n"
        f"🔗 لینک اختصاصی:\n`{link}`\n\n"
        f"👫 دعوت‌های موفق: {u['referral_count']}\n"
        f"🎁 تا اشتراک رایگان: {rem} نفر دیگر\n\n"
        f"هر {config.REFERRAL_THRESHOLD} دعوت = {config.REFERRAL_PLAN_NAME}\n\n"
        f"⚠️ دعوت شده باید عضو {ch} باشد تا دعوت شمارش بشه.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📢 عضویت در {ch}", url=f"https://t.me/{ch.lstrip('@')}")],
        ]) if not in_channel else None
    )

# ── Support ───────────────────────────────────────────────

async def start_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ss(uid, {"w": "support"})
    await update.message.reply_text("🎧 پیام خود را بنویسید:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel")]]))

async def recv_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = db.get_user(uid)
    msg = update.message.text
    cs(uid)
    await update.message.reply_text("✅ پیام شما ارسال شد.", reply_markup=main_kb(uid))
    for aid in all_admins():
        try:
            await context.bot.send_message(aid,
                f"📩 *پشتیبانی — xservpn*\n\n{uinfo(u)}\n\n💬 {escape_md(msg)}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✉️ پاسخ", callback_data=f"am_{uid}")]]))
        except Exception: pass

# ── Account ───────────────────────────────────────────────

async def show_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = db.get_user(uid)
    if not u:
        tg = update.effective_user
        u = db.get_or_create_user(uid, tg.username or "", tg.full_name or "")
    username = f"@{escape_md(u['username'])}" if u.get("username") else "ندارد"
    subs = db.get_user_subscriptions(uid)
    tier = config.get_vip_tier(u.get("total_topup", 0))
    vip_line = f"\n👑 سطح VIP: {tier['label']} ({tier['discount']}% تخفیف)" if tier else ""

    sub_text = ""
    for s in subs[:10]:
        if s.get("config_sent"):
            sub_text += f"\n\n📦 {s['plan_name']} — {s['created_at'][:10]}"
        else:
            sub_text += f"\n\n📦 {s['plan_name']} — {s['created_at'][:10]}\n   ⏳ در انتظار ارسال سابسکریپشن"
    if not sub_text:
        sub_text = "\nاشتراکی یافت نشد."

    await update.message.reply_text(
        f"👤 *حساب من — xservpn*\n\n"
        f"📛 نام: {escape_md(u['full_name'])}\n"
        f"🆔 آیدی عددی: `{uid}`\n"
        f"🔗 آیدی متنی: {username}\n"
        f"💰 موجودی: {u['balance']:,} تومان\n"
        f"💳 مجموع شارژ: {u.get('total_topup', 0):,} تومان{vip_line}\n"
        f"👥 دعوت‌ها: {u['referral_count']}\n\n"
        f"📋 *اشتراک‌های من:*{sub_text}",
        parse_mode="Markdown"
    )

    for s in subs[:10]:
        if s.get("config_sent"):
            await update.message.reply_text(
                f"🔑 {s['plan_name']}:\n{s['config_sent']}"
            )

# ── Top-up ────────────────────────────────────────────────

async def start_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not flag("topup_open"):
        await update.message.reply_text("🔴 افزایش موجودی در حال حاضر غیرفعال است.")
        return
    ss(uid, {"w": "topup_amount"})
    await update.message.reply_text(
        "💳 مبلغ شارژ را وارد کنید (50,000 تا 10,000,000 تومان):\n\n"
        "💡 با شارژ ۵ میلیون → VIP نقره‌ای (۲۵٪ تخفیف)\n"
        "💡 با شارژ ۱۰ میلیون → VIP طلایی (۴۰٪ تخفیف)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel")]]))

async def recv_topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        amount = int(update.message.text.replace(",", "").replace("،", "").strip())
    except ValueError:
        await update.message.reply_text("⚠️ لطفاً یک عدد وارد کنید."); return
    if amount < 50000:
        await update.message.reply_text("⚠️ حداقل 50,000 تومان."); return
    if amount > 10000000:
        await update.message.reply_text("⚠️ حداکثر 10,000,000 تومان."); return

    pay_id, inv = db.create_payment(uid, amount, "topup", pay_method="card")
    ss(uid, {"w": "topup_receipt", "pay_id": pay_id})
    await update.message.reply_text(
        f"🧾 *فاکتور شارژ کیف پول — xservpn*\n\n"
        f"🔖 کد فاکتور: `{inv}`\n"
        f"💵 مبلغ: *{fmt(amount)}*\n\n"
        f"💳 شماره کارت:\n`{card()}`\nبه نام: {cardholder()}\n\n"
        f"⏰ {config.PAYMENT_TIMEOUT_MINUTES} دقیقه فرصت دارید.\nتصویر رسید را ارسال کنید.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel")]]))
    asyncio.create_task(pay_timeout(context.bot, pay_id, uid, update.effective_chat.id, config.PAYMENT_TIMEOUT_MINUTES * 60))

async def recv_topup_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = gs(uid)
    pay_id = state.get("pay_id")
    if not pay_id: return
    msg = update.message
    if msg.photo:
        file_id = msg.photo[-1].file_id; is_photo = True
    elif msg.document:
        file_id = msg.document.file_id; is_photo = False
    else:
        await msg.reply_text("لطفاً تصویر رسید را ارسال کنید."); return

    db.set_receipt(pay_id, file_id, is_photo)
    pay = db.get_payment(pay_id)
    if not pay or pay["status"] in ("cancelled", "confirmed"):
        cs(uid)
        await update.message.reply_text("⚠️ این سفارش منقضی یا لغو شده است.", reply_markup=main_kb(uid))
        return
    u = db.get_user(uid)
    cs(uid)
    await msg.reply_text("✅ رسید دریافت شد. پس از تایید موجودی افزایش می‌یابد.", reply_markup=main_kb(uid))

    caption = (
        f"💳 *درخواست شارژ کیف پول — xservpn*\n\n"
        f"{uinfo(u)}\n\n"
        f"🔖 فاکتور: `{pay['invoice_code']}`\n"
        f"💵 مبلغ: {fmt(pay['amount'])}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید", callback_data=f"ac_{pay_id}"),
         InlineKeyboardButton("❌ رد", callback_data=f"ar_{pay_id}")],
        [InlineKeyboardButton("✉️ پیام مستقیم", callback_data=f"am_{uid}")],
    ])
    for aid in all_admins():
        try:
            if is_photo:
                await context.bot.send_photo(aid, photo=file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
            else:
                await context.bot.send_document(aid, document=file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            logger.error(f"topup receipt to admin {aid}: {e}")

# ── Admin panel ───────────────────────────────────────────

async def show_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text("🔧 *پنل مدیریت xservpn*", parse_mode="Markdown", reply_markup=admin_kb())

async def a_show_users(q):
    users = db.get_all_users()
    text = f"👥 *کاربران ({len(users)} نفر)*\n\n"
    for u in users[:20]:
        un = f"@{escape_md(u['username'])}" if u.get("username") else "—"
        tier = config.get_vip_tier(u.get("total_topup", 0))
        vip = f" {tier['label']}" if tier else ""
        text += f"• {escape_md(u['full_name'])} | {un} | {fmt(u['balance'])}{vip}\n"
    if len(users) > 20: text += f"\n... و {len(users)-20} نفر دیگر"
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())

async def a_show_pays(q):
    pays = db.get_pending_payments()
    if not pays:
        await q.edit_message_text("✅ پرداخت در انتظاری وجود ندارد.", reply_markup=back_kb()); return
    text = f"💰 *در انتظار تایید ({len(pays)})*\n\n"
    for p in pays:
        un = f"@{escape_md(p['username'])}" if p.get("username") else "—"
        method = p.get("pay_method", "card")
        method_label = {"card": "💳", "wallet": "💰", "crypto": f"💎{p.get('crypto_coin','')}"}.get(method, "💳")
        text += f"• `{p['invoice_code']}` | {escape_md(p['full_name'])} | {un} | {fmt(p['amount'])} | {method_label}\n"
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())

async def a_show_configs(q, uid):
    summary = db.get_configs_summary()
    counts = {r["plan_key"]: r["available"] for r in summary}
    all_keys = (list(config.PLANS.keys()) + list(config.TEST_PLANS.keys()) +
                [config.REFERRAL_PLAN_KEY, "referral"] +
                list(config.BUNDLE_PLANS[k]["plan_key"] for k in config.BUNDLE_PLANS if config.BUNDLE_PLANS[k]["plan_key"] not in config.PLANS))
    # حذف تکراری
    seen = set(); unique_keys = []
    for k in all_keys:
        if k not in seen:
            seen.add(k); unique_keys.append(k)
    text = "📦 *موجودی کانفیگ‌ها*\n\n"
    for k in unique_keys:
        text += f"• {PLAN_LABELS.get(k,k)}: {counts.get(k,0)} عدد\n"
    kb = [[InlineKeyboardButton(f"➕ {PLAN_LABELS.get(k,k)}", callback_data=f"a_addcfg_{k}")] for k in unique_keys]
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")])
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def a_show_prices(q, uid):
    all_plans = {**config.PLANS, **config.TEST_PLANS, **config.BUNDLE_PLANS}
    text = "💲 *قیمت‌های فعلی — xservpn*\n\n"
    for k, plan in all_plans.items():
        text += f"• {plan['name']}: {fmt(price(k))}\n"
    kb = [[InlineKeyboardButton(f"✏️ {plan['name']}", callback_data=f"a_setprice_{k}")] for k, plan in all_plans.items()]
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")])
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def a_show_crypto(q, uid):
    if not is_admin(uid): return
    text = "💎 *تنظیمات ارز دیجیتال — xservpn*\n\n"
    kb = []
    for coin, cinfo in config.CRYPTO_WALLETS.items():
        rate = crypto_rate(coin)
        wallet_addr = crypto_wallet(coin)
        short_addr = wallet_addr[:12] + "..." if len(wallet_addr) > 12 else wallet_addr
        text += (
            f"{cinfo['emoji']} *{cinfo['name']}*\n"
            f"   نرخ: {rate:,} تومان / {cinfo['symbol']}\n"
            f"   آدرس: `{short_addr}`\n\n"
        )
        kb.append([
            InlineKeyboardButton(f"💱 نرخ {cinfo['symbol']}", callback_data=f"a_set_crypto_rate_{coin}"),
            InlineKeyboardButton(f"📬 آدرس {cinfo['symbol']}", callback_data=f"a_set_crypto_wallet_{coin}"),
        ])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")])
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def a_show_admins(q, uid):
    aids = db.get_admin_ids()
    text = "👤 *ادمین‌ها*\n\n" + "\n".join([f"• `{a}`" for a in aids]) if aids else "👤 *ادمین‌ها*\n\nهیچ ادمینی ثبت نشده"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن", callback_data="a_add_admin"),
         InlineKeyboardButton("➖ حذف", callback_data="a_del_admin")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="a_back")],
    ])
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

# ── Admin input handlers ──────────────────────────────────

async def a_recv_bal_uid(update, context):
    uid = update.effective_user.id
    try:
        tid = int(update.message.text.strip())
        t = db.get_user(tid)
        if not t: await update.message.reply_text("⚠️ کاربر یافت نشد."); return
        ss(uid, {"w": "a_bal_amt", "tid": tid})
        await update.message.reply_text(f"موجودی فعلی: {fmt(t['balance'])}\nمقدار تغییر (مثبت/منفی):")
    except ValueError:
        await update.message.reply_text("⚠️ آیدی باید عدد باشد.")

async def a_recv_bal_amt(update, context):
    uid = update.effective_user.id
    state = gs(uid)
    tid = state.get("tid")
    try:
        delta = int(update.message.text.strip().replace(",", ""))
        db.update_balance(tid, delta)
        t = db.get_user(tid)
        cs(uid)
        await update.message.reply_text(f"✅ موجودی به‌روز شد: {fmt(t['balance'])}", reply_markup=main_kb(uid))
        try:
            await context.bot.send_message(tid, f"💰 موجودی کیف پول شما تغییر کرد.\nموجودی جدید: {fmt(t['balance'])}")
        except Exception: pass
        # بررسی هشدار VIP اگه موجودی کاهش پیدا کرده
        if delta < 0:
            asyncio.create_task(check_vip_balance_warning(context.bot, tid))
    except ValueError:
        await update.message.reply_text("⚠️ عدد وارد کنید.")

async def a_recv_price(update, context):
    uid = update.effective_user.id
    state = gs(uid)
    key = state.get("key")
    try:
        p = int(update.message.text.strip().replace(",", ""))
        db.set_setting(f"price_{key}", str(p))
        cs(uid)
        await update.message.reply_text(f"✅ قیمت {PLAN_LABELS.get(key,key)} → {fmt(p)}", reply_markup=main_kb(uid))
    except ValueError:
        await update.message.reply_text("⚠️ عدد وارد کنید.")

async def a_recv_crypto_rate(update, context):
    uid = update.effective_user.id
    state = gs(uid)
    coin = state.get("coin")
    try:
        rate = int(update.message.text.strip().replace(",", ""))
        db.set_setting(f"crypto_rate_{coin}", str(rate))
        cs(uid)
        cinfo = config.CRYPTO_WALLETS.get(coin, {})
        await update.message.reply_text(
            f"✅ نرخ {cinfo.get('name', coin)} → {rate:,} تومان / {cinfo.get('symbol', coin)}",
            reply_markup=main_kb(uid))
    except ValueError:
        await update.message.reply_text("⚠️ عدد وارد کنید.")

async def a_recv_crypto_wallet(update, context):
    uid = update.effective_user.id
    state = gs(uid)
    coin = state.get("coin")
    addr = update.message.text.strip()
    if len(addr) < 10:
        await update.message.reply_text("⚠️ آدرس معتبر نیست."); return
    db.set_setting(f"crypto_wallet_{coin}", addr)
    cs(uid)
    cinfo = config.CRYPTO_WALLETS.get(coin, {})
    await update.message.reply_text(
        f"✅ آدرس {cinfo.get('name', coin)} به‌روز شد:\n`{addr}`",
        parse_mode="Markdown", reply_markup=main_kb(uid))

async def a_recv_configs(update, context):
    uid = update.effective_user.id
    state = gs(uid)
    plan_key = state.get("plan_key", "referral")
    lines = [l.strip() for l in update.message.text.strip().split("\n") if l.strip()]
    if not lines:
        await update.message.reply_text("⚠️ هیچ کانفیگی یافت نشد."); return
    db.add_configs(plan_key, lines)
    cs(uid)
    cnt = db.get_config_count(plan_key)
    await update.message.reply_text(
        f"✅ {len(lines)} کانفیگ برای «{PLAN_LABELS.get(plan_key,plan_key)}» اضافه شد.\n📊 موجودی: {cnt}",
        reply_markup=main_kb(uid))

async def a_recv_broadcast(update, context):
    uid = update.effective_user.id
    text = update.message.text.strip()
    cs(uid)
    await update.message.reply_text("📢 ارسال پیام همگانی شروع شد. وقتی تمام شد اطلاع می‌دم.", reply_markup=main_kb(uid))

    async def _do_broadcast():
        ids = db.get_all_user_ids()
        ok = fail = 0
        for i in ids:
            try:
                await context.bot.send_message(i, f"📢 *پیام مدیریت xservpn:*\n\n{text}", parse_mode="Markdown")
                ok += 1
            except Exception:
                fail += 1
            await asyncio.sleep(0.3)
        try:
            await context.bot.send_message(uid, f"✅ پیام همگانی تموم شد!\n✅ {ok} موفق | ❌ {fail} ناموفق")
        except Exception: pass

    asyncio.create_task(_do_broadcast())

async def a_recv_add_channel(update, context):
    uid = update.effective_user.id
    ch = update.message.text.strip()
    if not ch.startswith("@"):
        ch = "@" + ch
    db.add_forced_channel(ch)
    cs(uid)
    await update.message.reply_text(
        f"✅ کانال `{ch}` به لیست اجباری اضافه شد.",
        parse_mode="Markdown", reply_markup=main_kb(uid))

async def a_recv_del_channel(update, context):
    uid = update.effective_user.id
    ch = update.message.text.strip()
    if not ch.startswith("@"):
        ch = "@" + ch
    db.remove_forced_channel(ch)
    cs(uid)
    await update.message.reply_text(
        f"✅ کانال `{ch}` از لیست حذف شد.",
        parse_mode="Markdown", reply_markup=main_kb(uid))

async def a_recv_add_admin(update, context):
    uid = update.effective_user.id
    try:
        nid = int(update.message.text.strip())
        db.add_admin(nid)
        cs(uid)
        await update.message.reply_text(f"✅ ادمین {nid} اضافه شد.", reply_markup=main_kb(uid))
    except ValueError:
        await update.message.reply_text("⚠️ آیدی باید عدد باشد.")

async def a_recv_del_admin(update, context):
    uid = update.effective_user.id
    try:
        rid = int(update.message.text.strip())
        if rid in config.ADMIN_IDS:
            await update.message.reply_text("⚠️ ادمین اصلی قابل حذف نیست."); return
        db.remove_admin(rid)
        cs(uid)
        await update.message.reply_text(f"✅ ادمین {rid} حذف شد.", reply_markup=main_kb(uid))
    except ValueError:
        await update.message.reply_text("⚠️ آیدی باید عدد باشد.")

async def a_recv_card(update, context):
    uid = update.effective_user.id
    c_num = update.message.text.strip()
    db.set_setting("card_number", c_num)
    cs(uid)
    await update.message.reply_text(f"✅ شماره کارت به‌روز شد:\n`{c_num}`", parse_mode="Markdown", reply_markup=main_kb(uid))

async def a_recv_cardholder(update, context):
    uid = update.effective_user.id
    name = update.message.text.strip()
    db.set_setting("card_holder", name)
    cs(uid)
    await update.message.reply_text(f"✅ نام دارنده کارت به‌روز شد:\n{name}", reply_markup=main_kb(uid))

# ── Test account ──────────────────────────────────────────

async def show_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await require_member(update, context): return
    u = db.get_user(uid)
    if u and u.get("test_used"):
        await update.message.reply_text("⚠️ شما قبلاً از اکانت تست استفاده کرده‌اید.\nهر کاربر فقط یک بار می‌تواند تست بگیرد.")
        return
    cnt = db.get_config_count("20mb")
    if cnt == 0:
        await update.message.reply_text(
            "⚠️ در حال حاضر اکانت تست موجود نیست.\nلطفاً بعداً امتحان کنید یا با پشتیبانی تماس بگیرید."
        )
        return
    await update.message.reply_text(
        "🧪 *اکانت تست رایگان*\n\n"
        "✅ ۲۰ مگابایت حجم رایگان\n"
        "⚠️ هر کاربر فقط یک بار مجاز است\n\n"
        "آیا می‌خواهید اکانت تست دریافت کنید؟",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ دریافت اکانت تست", callback_data="get_free_test")],
            [InlineKeyboardButton("❌ لغو", callback_data="cancel")],
        ])
    )

async def do_free_test(q, uid, context):
    u = db.get_user(uid)
    if u and u.get("test_used"):
        await q.edit_message_text("⚠️ شما قبلاً از اکانت تست استفاده کرده‌اید.")
        return
    cfg = db.assign_config("20mb", uid)
    if not cfg:
        await q.edit_message_text("⚠️ در حال حاضر اکانت تست موجود نیست. لطفاً بعداً امتحان کنید.")
        return
    db.mark_test_used(uid)
    try:
        await context.bot.send_message(uid,
            f"🎁 *اکانت تست رایگان xservpn*\n\n📊 حجم: ۲۰ مگابایت",
            parse_mode="Markdown")
        await context.bot.send_message(uid, cfg)
    except Exception: pass
    await q.edit_message_text("✅ اکانت تست برای شما ارسال شد!")
    u2 = db.get_user(uid)
    for aid in all_admins():
        try:
            await context.bot.send_message(aid,
                f"🧪 *اکانت تست ارسال شد*\n\n{uinfo(u2)}\n\n🔑 {cfg}",
                parse_mode="Markdown")
        except Exception: pass

# ── Unified message handler ───────────────────────────────

async def on_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    msg = update.message
    has_photo = bool(msg.photo)
    has_doc   = bool(msg.document)
    has_text  = bool(msg.text)

    state = gs(uid)
    w = state.get("w")

    if (has_photo or has_doc) and w in ("receipt", "topup_receipt", "crypto_receipt"):
        if w == "receipt":
            await recv_receipt(update, context)
        elif w == "topup_receipt":
            await recv_topup_receipt(update, context)
        else:
            await recv_crypto_receipt(update, context)
        return

    if has_text:
        await on_message(update, context)

# ── Commands ──────────────────────────────────────────────

async def cmd_admin(update, context):
    if is_admin(update.effective_user.id):
        await show_admin(update, context)

async def cmd_setbalance(update, context):
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 2:
        await update.message.reply_text("استفاده: /setbalance <uid> <amount>"); return
    try:
        db.set_balance(int(context.args[0]), int(context.args[1]))
        await update.message.reply_text("✅ موجودی تنظیم شد.")
    except ValueError:
        await update.message.reply_text("⚠️ مقادیر نامعتبر.")

# ── Main ──────────────────────────────────────────────────

def main():
    db.init_db()
    for aid in config.ADMIN_IDS:
        db.add_admin(aid)

    # لود تنظیمات VIP از دیتابیس (اگر ادمین تغییر داده)
    for i, tier in enumerate(config.VIP_TIERS):
        pct = db.get_setting(f"vip_tier_{i}_discount")
        min_v = db.get_setting(f"vip_tier_{i}_min")
        if pct:
            config.VIP_TIERS[i]["discount"] = int(pct)
        if min_v:
            config.VIP_TIERS[i]["min"] = int(min_v)

    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("setbalance", cmd_setbalance))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_any_message))

    logger.info("Bot xservpn started.")
    app.run_polling(
        drop_pending_updates=False,
        allowed_updates=["message", "callback_query", "channel_post", "edited_message"]
    )

if __name__ == "__main__":
    main()
