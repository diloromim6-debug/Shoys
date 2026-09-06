# Rasm → B-roll Video Telegram Bot

Rasmlarni yuklaysiz → bot ularni Ken Burns (zoom-in / zoom-out) effektli,
crossfade o'tishlar bilan bitta videoga birlashtiradi. Format: 9:16 yoki 16:9.

## Qanday ishlaydi

1. /start → rasmlarni ketma-ket yuborasiz → /done
2. Format tanlaysiz: 9:16 (Reels/Shorts/TikTok) yoki 16:9 (YouTube)
3. Davomiylikni yozasiz: 2:30, 90s, 1.5m
4. Bot FFmpeg orqali videoni serverda render qiladi va sizga yuboradi

## Sozlash

Railway/Render kabi platformada BOT_TOKEN muhit o'zgaruvchisini
qo'shish kifoya — Dockerfile FFmpeg'ni avtomatik o'rnatadi.
