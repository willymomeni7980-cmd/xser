import os

# Bot Token
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8589891234:AAGEnEZ9n9OygvOr5QHWghps8xbbZf_a63Y")

# آیدی ادمین (عددی)
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "2083913926").split(",") if x.strip()]

# شماره کارت برای پرداخت
CARD_NUMBER = os.environ.get("CARD_NUMBER", "6219-8618-8507-2834")
CARD_HOLDER = os.environ.get("CARD_HOLDER", "شریفی")

# آدرس‌های کیف پول ارزی
CRYPTO_WALLETS = {
    "ton": {
        "name": "تون (TON)",
        "symbol": "TON",
        "address": os.environ.get("WALLET_TON", "UQAv5Wbirrsy2PhkHO8rJAmiM45cHmSwEU5t0R_cZSv-ho2A"),
        "emoji": "💎",
    },
    "trx": {
        "name": "ترون (TRX)",
        "symbol": "TRX",
        "address": os.environ.get("WALLET_TRX", "TMxfiJjp95DNFjCgtkBgkhgLJyHv8wtf93"),
        "emoji": "🔴",
    },
    "usdt": {
        "name": "تتر (USDT/TON)",
        "symbol": "USDT",
        "address": os.environ.get("WALLET_USDT", "UQAv5Wbirrsy2PhkHO8rJAmiM45cHmSwEU5t0R_cZSv-ho2A"),
        "emoji": "💵",
    },
}

# زمان انقضای پرداخت (دقیقه)
PAYMENT_TIMEOUT_MINUTES = 20

# پلن‌های اشتراک (قیمت تومان)
PLANS = {
    "1gb": {
        "name": "اشتراک ۱ گیگ",
        "size": "۱ گیگابایت",
        "duration": "نامحدود",
        "price": int(os.environ.get("PRICE_1GB", "220000")),
    },
    "2gb": {
        "name": "اشتراک ۲ گیگ",
        "size": "۲ گیگابایت",
        "duration": "نامحدود",
        "price": int(os.environ.get("PRICE_2GB", "440000")),
    },
    "3gb": {
        "name": "اشتراک ۳ گیگ",
        "size": "۳ گیگابایت",
        "duration": "نامحدود",
        "price": int(os.environ.get("PRICE_3GB", "660000")),
    },
    "4gb": {
        "name": "اشتراک ۴ گیگ",
        "size": "۴ گیگابایت",
        "duration": "نامحدود",
        "price": int(os.environ.get("PRICE_4GB", "880000")),
    },
    "5gb": {
        "name": "اشتراک ۵ گیگ",
        "size": "۵ گیگابایت",
        "duration": "نامحدود",
        "price": int(os.environ.get("PRICE_5GB", "1000000")),
    },
    "7gb": {
        "name": "اشتراک ۷ گیگ",
        "size": "۷ گیگابایت",
        "duration": "نامحدود",
        "price": int(os.environ.get("PRICE_7GB", "1400000")),
    },
    "10gb": {
        "name": "اشتراک ۱۰ گیگ",
        "size": "۱۰ گیگابایت",
        "duration": "نامحدود",
        "price": int(os.environ.get("PRICE_10GB", "1850000")),
    },
    "50gb": {
        "name": "اشتراک ۵۰ گیگ (Outbound)",
        "size": "۵۰ گیگابایت",
        "duration": "نامحدود",
        "price": int(os.environ.get("PRICE_50GB", "8500000")),
    },
    "100gb": {
        "name": "اشتراک ۱۰۰ گیگ",
        "size": "۱۰۰ گیگابایت",
        "duration": "نامحدود",
        "price": int(os.environ.get("PRICE_100GB", "17000000")),
    },
}

# پلن‌های بسته‌ای (خرید چندتایی)
BUNDLE_PLANS = {
    "bundle_3x1gb": {
        "name": "بسته ۳ تا ۱ گیگ",
        "size": "۳ × ۱ گیگابایت",
        "count": 3,
        "plan_key": "1gb",
        "price": int(os.environ.get("PRICE_BUNDLE_3X1GB", "594000")),   # ~10% تخفیف
    },
    "bundle_5x1gb": {
        "name": "بسته ۵ تا ۱ گیگ",
        "size": "۵ × ۱ گیگابایت",
        "count": 5,
        "plan_key": "1gb",
        "price": int(os.environ.get("PRICE_BUNDLE_5X1GB", "935000")),   # ~15% تخفیف
    },
    "bundle_3x2gb": {
        "name": "بسته ۳ تا ۲ گیگ",
        "size": "۳ × ۲ گیگابایت",
        "count": 3,
        "plan_key": "2gb",
        "price": int(os.environ.get("PRICE_BUNDLE_3X2GB", "1122000")),
    },
    "bundle_5x2gb": {
        "name": "بسته ۵ تا ۲ گیگ",
        "size": "۵ × ۲ گیگابایت",
        "count": 5,
        "plan_key": "2gb",
        "price": int(os.environ.get("PRICE_BUNDLE_5X2GB", "1870000")),
    },
    "bundle_3x3gb": {
        "name": "بسته ۳ تا ۳ گیگ",
        "size": "۳ × ۳ گیگابایت",
        "count": 3,
        "plan_key": "3gb",
        "price": int(os.environ.get("PRICE_BUNDLE_3X3GB", "1683000")),
    },
    "bundle_5x3gb": {
        "name": "بسته ۵ تا ۳ گیگ",
        "size": "۵ × ۳ گیگابایت",
        "count": 5,
        "plan_key": "3gb",
        "price": int(os.environ.get("PRICE_BUNDLE_5X3GB", "2805000")),
    },
}

# اکانت تست — رایگان، ۲۰ مگابایت
TEST_PLANS = {
    "20mb": {
        "name": "۲۰ مگابایت تست رایگان",
        "size": "۲۰ مگابایت",
        "price": 0,
    },
}

# تعداد دعوت برای دریافت اشتراک رایگان رفرال
REFERRAL_THRESHOLD = 4
# پلن رفرال (۱۰۰ مگ)
REFERRAL_PLAN_KEY = "100mb_referral"
REFERRAL_PLAN_NAME = "۱۰۰ مگابایت رایگان (رفرال)"
REFERRAL_PLAN_SIZE = "۱۰۰ مگابایت"

# کانال اجباری برای رفرال
REFERRAL_CHANNEL = "@xservpn"

# ── تنظیمات VIP ──────────────────────────────────────────
VIP_TIERS = [
    {"min": 10_000_000, "label": "VIP طلایی 👑", "discount": 40},
    {"min":  5_000_000, "label": "VIP نقره‌ای 🥈", "discount": 25},
]

def get_vip_tier(total_topup: int) -> dict | None:
    """بر اساس مجموع شارژ کاربر، tier مناسب برمی‌گردونه"""
    for tier in VIP_TIERS:
        if total_topup >= tier["min"]:
            return tier
    return None

def apply_vip_discount(price: int, total_topup: int) -> int:
    """قیمت با تخفیف VIP"""
    tier = get_vip_tier(total_topup)
    if not tier:
        return price
    return int(price * (100 - tier["discount"]) / 100)
