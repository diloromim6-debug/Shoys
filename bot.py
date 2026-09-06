"""
Rasmlardan B-roll video yaratuvchi Telegram bot.

Foydalanuvchi bir nechta rasm yuboradi, format (9:16 yoki 16:9) va video
davomiyligini tanlaydi. Bot FFmpeg yordamida har bir rasmga Ken Burns
(zoom-in / zoom-out) effekti qo'llab, rasmlar orasida crossfade o'tish
bilan yagona video yaratib, foydalanuvchiga yuboradi.

Ishga tushirish:
    export BOT_TOKEN="123456:ABC-YourTelegramBotToken"
    python3 bot.py

Talablar: python-telegram-bot, ffmpeg (server’da o‘rnatilgan bo‘lishi kerak)
"""

import os
import re
import shutil
import logging
import tempfile
import subprocess
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# --- Conversation holatlari ---
COLLECTING_PHOTOS, CHOOSING_FORMAT, ENTERING_DURATION = range(3)

# --- Sozlamalar (kerak bo'lsa o'zgartiring) ---
MAX_PHOTOS = 40
TRANSITION_DURATION = 0.8   # rasmlar orasidagi crossfade uzunligi (soniya)
MIN_DURATION = 10           # video minimal davomiyligi (soniya)
MAX_DURATION = 20 * 60      # video maksimal davomiyligi (soniya)
FPS = 25
ZOOM_RANGE = 0.25           # 1.0 dan 1.0+ZOOM_RANGE gacha zoom qilinadi
UPSCALE_FACTOR = 2          # zoom qilishdan oldin rasmni necha barobar kattalashtirish

FORMATS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
}


# --------------------------------------------------------------------------
# Telegram handlerlari
# --------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data["photos"] = []
    await update.message.reply_text(
        "Salom! 👋\n\n"
        "Men rasmlaringizdan zoom-effektli (Ken Burns) b-roll video yarataman.\n\n"
        "1️⃣ Menga bir nechta rasm yuboring "
        "(sifat yo'qolmasligi uchun 📎 fayl/hujjat sifatida yuborish tavsiya etiladi)\n"
        "2️⃣ Barcha rasmlarni yuborib bo'lgach /done buyrug'ini yuboring\n\n"
        f"Maksimal rasmlar soni: {MAX_PHOTOS} ta\n"
        "Bekor qilish uchun: /cancel"
    )
    return COLLECTING_PHOTOS


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photos = context.user_data.setdefault("photos", [])

    if len(photos) >= MAX_PHOTOS:
        await update.message.reply_text(
            f"⚠️ Maksimal {MAX_PHOTOS} ta rasm chegarasiga yetdingiz. Endi /done yuboring."
        )
        return COLLECTING_PHOTOS

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document and (update.message.document.mime_type or "").startswith("image/"):
        file_id = update.message.document.file_id
    else:
        await update.message.reply_text("Iltimos, rasm (yoki rasm fayli) yuboring.")
        return COLLECTING_PHOTOS

    photos.append(file_id)
    await update.message.reply_text(
        f"✅ Qabul qilindi ({len(photos)} ta rasm). Yana yuboring yoki tugatgach /done bosing."
    )
    return COLLECTING_PHOTOS


async def done_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photos = context.user_data.get("photos", [])
    if len(photos) < 2:
        await update.message.reply_text("Kamida 2 ta rasm kerak. Yana rasm yuboring.")
        return COLLECTING_PHOTOS

    keyboard = [
        [InlineKeyboardButton("📱 9:16 (Reels / Shorts / TikTok)", callback_data="9:16")],
        [InlineKeyboardButton("🖥 16:9 (YouTube)", callback_data="16:9")],
    ]
    await update.message.reply_text(
        f"{len(photos)} ta rasm qabul qilindi.\n\nQaysi formatda video kerak?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_FORMAT


async def choose_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["format"] = query.data
    await query.edit_message_text(f"Format tanlandi: {query.data} ✅")
    await query.message.reply_text(
        "Video necha daqiqa/soniya bo'lishi kerak?\n\n"
        "Masalan:\n"
        "• 2:30  → 2 daqiqa 30 soniya\n"
        "• 90s   → 90 soniya\n"
        "• 1.5m  → 1.5 daqiqa"
    )
    return ENTERING_DURATION


def parse_duration(text: str) -> float:
    """Foydalanuvchi kiritgan matnni soniyaga aylantiradi. Xato bo'lsa -1 qaytaradi."""
    text = text.strip().lower()

    m = re.match(r"^(\d+):(\d{1,2})$", text)          # 2:30
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))

    m = re.match(r"^(\d+(\.\d+)?)\s*s(ec)?$", text)     # 90s
    if m:
        return float(m.group(1))

    m = re.match(r"^(\d+(\.\d+)?)\s*m(in)?$", text)     # 1.5m
    if m:
        return float(m.group(1)) * 60

    m = re.match(r"^(\d+(\.\d+)?)$", text)              # faqat raqam -> soniya
    if m:
        return float(m.group(1))

    return -1


async def enter_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    duration = parse_duration(update.message.text)
    if duration < MIN_DURATION or duration > MAX_DURATION:
        await update.message.reply_text(
            f"❌ Noto'g'ri format yoki chegaradan tashqari.\n"
            f"{MIN_DURATION} soniyadan {MAX_DURATION // 60} daqiqagacha kiriting.\n"
            "Masalan: 2:30 yoki 90s"
        )
        return ENTERING_DURATION

    context.user_data["duration"] = duration
    await update.message.reply_text(
        "⏳ Video tayyorlanmoqda... Rasmlar soni va davomiylikka qarab "
        "bu bir necha daqiqa vaqt olishi mumkin, iltimos kuting."
    )

    await process_video(update, context)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Bekor qilindi. Qaytadan boshlash uchun /start yozing.")
    return ConversationHandler.END


# --------------------------------------------------------------------------
# Video generatsiya
# --------------------------------------------------------------------------

async def process_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    photos = context.user_data["photos"]
    fmt = context.user_data["format"]
    total_duration = context.user_data["duration"]
    width, height = FORMATS[fmt]

    work_dir = Path(tempfile.mkdtemp(prefix="broll_"))
    try:
        # 1) Rasmlarni yuklab olish
        image_paths = []
        for i, file_id in enumerate(photos):
            tg_file = await context.bot.get_file(file_id)
            img_path = work_dir / f"img_{i:03d}.jpg"
            await tg_file.download_to_drive(str(img_path))
            image_paths.append(img_path)

        # 2) FFmpeg orqali video yaratish
        output_path = work_dir / "output.mp4"
        build_and_run_ffmpeg(
            image_paths=image_paths,
            output_path=output_path,
            width=width,
            height=height,
            total_duration=total_duration,
            transition=TRANSITION_DURATION,
        )

        # 3) Natijani foydalanuvchiga yuborish
        with open(output_path, "rb") as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=f"✅ Tayyor! Format: {fmt}, davomiyligi: ~{int(total_duration)}s",
                supports_streaming=True,
                write_timeout=120,
                read_timeout=120,
            )

    except subprocess.CalledProcessError as e:
        logger.error("FFmpeg xatosi:\n%s", e.stderr)
        await update.message.reply_text(
            "❌ Video yaratishda xatolik yuz berdi. Rasmlar formatini tekshirib, qaytadan urinib ko'ring."
        )
    except Exception:
        logger.exception("Kutilmagan xatolik")
        await update.message.reply_text("❌ Kutilmagan xatolik yuz berdi. Qaytadan urinib ko'ring.")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def build_and_run_ffmpeg(image_paths, output_path, width, height, total_duration, transition):
    """
    Har bir rasmdan Ken Burns (zoom in/out) klipi yasaydi, so'ng ularni
    xfade (crossfade) bilan bitta videoga birlashtiradi.
    """
    n = len(image_paths)

    # n * d - (n-1) * t = total_duration  =>  d = (total_duration + (n-1)*t) / n
    # d — har bir klipning overlapdan oldingi davomiyligi
    clip_duration = (total_duration + (n - 1) * transition) / n
    zoom_frames = max(int(round(clip_duration * FPS)), 1)
    zoom_step = ZOOM_RANGE / zoom_frames

    up_w, up_h = width * UPSCALE_FACTOR, height * UPSCALE_FACTOR

    input_args = []
    filter_parts = []

    for i, img_path in enumerate(image_paths):
        # Diqqat: faqat "-loop 1" ishlatiladi, inputga -t/-framerate qo'yilmaydi.
        # Sabab: zoompan filtri HAR BIR kelgan input frame uchun alohida "d" ta
        # chiqish freymi yaratadi. Agar inputga o'zi ham -t/-framerate bilan
        # bir nechta freym berilsa, zoompan ularning har biriga d freym qo'shib
        # ketadi va video keragidan necha barobar uzun chiqadi. Shuning uchun
        # inputni "cheksiz" holda beramiz va pastda trim=end_frame bilan
        # zoompan chiqishini aniq zoom_frames tagacha kesib olamiz.
        input_args += ["-loop", "1", "-i", str(img_path)]

        zoom_in = (i % 2 == 0)
        if zoom_in:
            z_expr = f"min(zoom+{zoom_step:.6f},{1 + ZOOM_RANGE})"
        else:
            z_expr = f"if(eq(on,0),{1 + ZOOM_RANGE},max(1.0,zoom-{zoom_step:.6f}))"

        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

        vf = (
            f"[{i}:v]scale={up_w}:{up_h}:force_original_aspect_ratio=increase,"
            f"crop={up_w}:{up_h},"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
            f"d={zoom_frames}:s={width}x{height}:fps={FPS},"
            f"trim=end_frame={zoom_frames},setpts=PTS-STARTPTS,"
            f"setsar=1[v{i}]"
        )
        filter_parts.append(vf)

    # Klplarni ketma-ket xfade bilan ulash
    xfade_parts = []
    prev_label = "v0"
    cumulative = clip_duration
    for i in range(1, n):
        out_label = f"x{i}" if i < n - 1 else "vout"
        offset = cumulative - transition
        xfade_parts.append(
            f"[{prev_label}][v{i}]xfade=transition=fade:duration={transition:.3f}:"
            f"offset={offset:.3f}[{out_label}]"
        )
        prev_label = out_label
        cumulative += clip_duration - transition

    filter_complex = ";".join(filter_parts + xfade_parts)

    cmd = [
        "ffmpeg", "-y",
        *input_args,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-preset", "medium",
        "-crf", "20",
        str(output_path),
    ]

    logger.info("FFmpeg ishga tushirilmoqda (%d ta rasm, ~%.1fs)", n, total_duration)
    subprocess.run(cmd, check=True, capture_output=True, text=True)


# --------------------------------------------------------------------------
# Bot ishga tushirish
# --------------------------------------------------------------------------

def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN topilmadi. Avval: export BOT_TOKEN=\"...\" qiling."
        )

    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            COLLECTING_PHOTOS: [
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, receive_photo),
                CommandHandler("done", done_photos),
            ],
            CHOOSING_FORMAT: [
                CallbackQueryHandler(choose_format),
            ],
            ENTERING_DURATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_duration),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    logger.info("Bot ishga tushdi...")
    application.run_polling()


if __name__ == "__main__":
    main()
