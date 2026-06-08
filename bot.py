import os
import re
import json
import zipfile
import asyncio
import logging
import tempfile
import shutil
import subprocess
from pathlib import Path

from telegram import Update, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")


# ─── Extraction username ──────────────────────────────────────────────────────

def extract_username(text: str) -> str | None:
    text = text.strip()
    match = re.search(
        r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/?",
        text,
    )
    if match:
        candidate = match.group(1)
        if candidate in ("p", "reel", "reels", "stories", "explore", "tv", "accounts"):
            return None
        return candidate
    match = re.match(r"^@?([A-Za-z0-9_.]{1,30})$", text)
    if match:
        return match.group(1)
    return None


# ─── Téléchargement via yt-dlp ───────────────────────────────────────────────

def run_ytdlp(username: str, output_dir: str) -> dict:
    """
    Utilise yt-dlp pour télécharger uniquement les posts d'un profil Instagram.
    Retourne {"images": [...], "videos": [...], "full_name": str}
    """
    profile_url = f"https://www.instagram.com/{username}/"
    out_template = str(Path(output_dir) / "%(upload_date)s_%(id)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--quiet",
        # Format : préférer mp4 pour vidéos, jpg pour images
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        # Écrire les métadonnées JSON pour chaque post
        "--write-info-json",
        # Pas de thumbnails séparés
        "--no-write-thumbnail",
        # Télécharger uniquement les posts (pas reels, pas stories)
        "--playlist-items", "1-200",
        # Exclure les Reels via le filtre sur la durée / type
        "--match-filter", "!is_live & webpage_url_basename != 'reel'",
        # Output template
        "--output", out_template,
        # Retries réseau
        "--retries", "5",
        "--fragment-retries", "5",
        "--socket-timeout", "30",
        # User-agent navigateur
        "--user-agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        # Source : page posts du profil
        f"https://www.instagram.com/{username}/",
    ]

    logger.info(f"yt-dlp commande : {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max
            cwd=output_dir,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("Le téléchargement a pris trop de temps (>10 min).")
    except FileNotFoundError:
        raise RuntimeError("yt-dlp n'est pas installé sur ce serveur.")

    logger.info(f"yt-dlp stdout: {result.stdout[-500:] if result.stdout else '(vide)'}")
    if result.stderr:
        logger.warning(f"yt-dlp stderr: {result.stderr[-500:]}")

    # Analyser les erreurs connues
    combined = (result.stdout + result.stderr).lower()
    if "private" in combined or "login required" in combined:
        raise ValueError(f"Le profil @{username} est privé ou nécessite une connexion.")
    if "does not exist" in combined or "not found" in combined:
        raise ValueError(f"Le profil @{username} n'existe pas.")
    if "429" in combined or "too many requests" in combined:
        raise ValueError(
            "Instagram limite temporairement les requêtes (rate limit).\n"
            "Attends 5–10 minutes et réessaie."
        )

    # Récupérer le full_name depuis les fichiers JSON générés
    full_name = username
    json_files = list(Path(output_dir).glob("*.json"))
    if json_files:
        try:
            with open(json_files[0]) as f:
                meta = json.load(f)
            full_name = meta.get("uploader", meta.get("channel", username))
        except Exception:
            pass

    # Collecter les médias téléchargés
    images = sorted(
        f for f in Path(output_dir).iterdir()
        if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        and not f.name.endswith(".json")
    )
    videos = sorted(
        f for f in Path(output_dir).iterdir()
        if f.suffix.lower() in (".mp4", ".mkv", ".mov", ".webm")
    )

    if not images and not videos and result.returncode != 0:
        raise ValueError(
            f"yt-dlp a échoué (code {result.returncode}). "
            "Le profil est peut-être privé ou vide."
        )

    return {"images": images, "videos": videos, "full_name": full_name}


def create_video_zip(video_paths: list, zip_path: str) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for p in video_paths:
            zf.write(p, arcname=Path(p).name)


# ─── Helpers Telegram ─────────────────────────────────────────────────────────

async def get_or_create_topic(bot, chat_id: int, username: str) -> int | None:
    try:
        topic = await bot.create_forum_topic(
            chat_id=chat_id,
            name=f"@{username}",
        )
        return topic.message_thread_id
    except TelegramError as e:
        err = str(e).lower()
        if any(k in err for k in ("not a forum", "forum_disabled", "supergroup",
                                   "method is available", "chat not found")):
            logger.info(f"Topics non disponibles pour chat {chat_id}.")
        else:
            logger.warning(f"create_forum_topic échoué : {e}")
        return None


async def send_msg(bot, chat_id: int, thread_id: int | None, text: str):
    kwargs = {"chat_id": chat_id, "text": text, "parse_mode": ParseMode.HTML}
    if thread_id:
        kwargs["message_thread_id"] = thread_id
    await bot.send_message(**kwargs)


# ─── Handlers ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>Instagram Post Downloader</b>\n\n"
        "Envoie un lien ou un nom de profil :\n"
        "• <code>https://www.instagram.com/username</code>\n"
        "• <code>@username</code> ou <code>username</code>\n\n"
        "Je récupère uniquement les <b>posts</b> (photos + vidéos).\n"
        "Stories, Highlights et Reels sont exclus.",
        parse_mode=ParseMode.HTML,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    chat_id = message.chat_id
    username = extract_username(message.text)

    if not username:
        await message.reply_text(
            "❌ Profil Instagram non reconnu.\n"
            "Envoie un lien ou un <code>@username</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    thread_id = await get_or_create_topic(context.bot, chat_id, username)

    await send_msg(
        context.bot, chat_id, thread_id,
        f"🔍 Téléchargement des posts de <b>@{username}</b>…\n"
        f"⏳ Cela peut prendre quelques minutes selon la taille du profil.",
    )

    tmpdir = tempfile.mkdtemp(prefix="insta_")
    try:
        loop = asyncio.get_event_loop()

        try:
            result = await loop.run_in_executor(
                None, run_ytdlp, username, tmpdir
            )
        except (ValueError, RuntimeError) as e:
            await send_msg(context.bot, chat_id, thread_id, f"❌ {e}")
            return

        images = result["images"]
        videos = result["videos"]
        full_name = result["full_name"]

        if not images and not videos:
            await send_msg(
                context.bot, chat_id, thread_id,
                "😕 Aucun post trouvé (profil vide ou privé).",
            )
            return

        await send_msg(
            context.bot, chat_id, thread_id,
            f"✅ <b>{full_name}</b> (@{username})\n"
            f"📸 {len(images)} image(s) · 🎬 {len(videos)} vidéo(s)\n"
            f"📤 Envoi en cours…",
        )

        # ── Envoi des images par lots de 10 ───────────────────────────────
        for i in range(0, len(images), 10):
            batch = images[i:i + 10]
            media_group = []
            opened = []
            try:
                for j, p in enumerate(batch):
                    fh = open(p, "rb")
                    opened.append(fh)
                    caption = f"📸 @{username}" if i == 0 and j == 0 else None
                    media_group.append(InputMediaPhoto(media=fh, caption=caption))
                kwargs = {"chat_id": chat_id, "media": media_group}
                if thread_id:
                    kwargs["message_thread_id"] = thread_id
                await context.bot.send_media_group(**kwargs)
            finally:
                for fh in opened:
                    fh.close()

        # ── Vidéos → ZIP ──────────────────────────────────────────────────
        if videos:
            zip_path = str(Path(tmpdir) / f"{username}_videos.zip")
            await loop.run_in_executor(
                None, create_video_zip, videos, zip_path
            )
            zip_mb = os.path.getsize(zip_path) / 1024 / 1024

            if zip_mb <= 50:
                with open(zip_path, "rb") as zf:
                    kwargs = {
                        "chat_id": chat_id,
                        "document": zf,
                        "filename": f"{username}_videos.zip",
                        "caption": f"🎬 {len(videos)} vidéo(s) · @{username}",
                    }
                    if thread_id:
                        kwargs["message_thread_id"] = thread_id
                    await context.bot.send_document(**kwargs)
            else:
                await send_msg(
                    context.bot, chat_id, thread_id,
                    f"⚠️ ZIP trop lourd ({zip_mb:.1f} MB), envoi vidéo par vidéo…",
                )
                for idx, vp in enumerate(videos, 1):
                    vmb = os.path.getsize(vp) / 1024 / 1024
                    if vmb > 50:
                        logger.warning(f"Vidéo ignorée ({vmb:.1f} MB > 50 MB) : {vp}")
                        continue
                    with open(vp, "rb") as vf:
                        kwargs = {
                            "chat_id": chat_id,
                            "video": vf,
                            "caption": f"🎬 @{username} ({idx}/{len(videos)})",
                            "supports_streaming": True,
                        }
                        if thread_id:
                            kwargs["message_thread_id"] = thread_id
                        await context.bot.send_video(**kwargs)

        await send_msg(
            context.bot, chat_id, thread_id,
            f"✅ Terminé — {len(images)} photo(s) et {len(videos)} vidéo(s) envoyées.",
        )

    except Exception as e:
        logger.exception(f"Erreur inattendue pour @{username}")
        await send_msg(
            context.bot, chat_id, thread_id,
            f"❌ Erreur inattendue : {e}",
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN manquant dans les variables d'environnement.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot démarré.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
