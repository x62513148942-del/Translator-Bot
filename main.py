import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import json
import os
import threading
from flask import Flask

# 嘗試載入本地端的 .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------
# 1. Web 保活
# ---------------------------------------------------------

app = Flask("")

@app.route("/")
def home():
    return "Bot is alive and running!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()

# ---------------------------------------------------------
# 2. 環境變數與設定
# ---------------------------------------------------------

BOT_TOKEN = os.getenv("DC_BOT_TOKEN")
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")
CONFIG_FILE = "channel_config.json"

if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        try:
            CHANNEL_CONFIG = {
                int(k): v for k, v in json.load(f).items()
            }
        except json.JSONDecodeError:
            CHANNEL_CONFIG = {}
else:
    CHANNEL_CONFIG = {}

def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(
            CHANNEL_CONFIG,
            f,
            indent=4,
            ensure_ascii=False
        )

# ---------------------------------------------------------
# 3. Discord Bot 初始化
# ---------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

http_session = None

# ---------------------------------------------------------
# 4. 翻譯
# ---------------------------------------------------------

async def translate_text(
    session,
    text,
    target_lang,
    max_retries=3
):

    # 泰文或沒有 DeepL Key 時走 Google
    if target_lang == "TH" or not DEEPL_API_KEY:

        url = "https://translate.googleapis.com/translate_a/single"

        tl = (
            "th"
            if target_lang == "TH"
            else target_lang.lower()
        )

        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": tl,
            "dt": "t",
            "q": text
        }

        try:
            async with session.get(
                url,
                params=params,
                timeout=10
            ) as resp:

                if resp.status == 200:
                    data = await resp.json()

                    return "".join(
                        item[0]
                        for item in data[0]
                        if item[0]
                    )

                print(
                    f"❌ Google GTX 翻譯失敗: "
                    f"HTTP {resp.status}"
                )

                return text

        except Exception as e:
            print(f"❌ Google GTX 發生錯誤：{e}")
            return text

    # DeepL
    if DEEPL_API_KEY.endswith(":fx"):
        url = "https://api-free.deepl.com/v2/translate"
    else:
        url = "https://api.deepl.com/v2/translate"

    headers = {
        "Authorization":
        f"DeepL-Auth-Key {DEEPL_API_KEY}"
    }

    data = {
        "text": [text],
        "target_lang": target_lang
    }

    for attempt in range(max_retries):

        try:
            async with session.post(
                url,
                headers=headers,
                json=data,
                timeout=10
            ) as resp:

                if resp.status == 200:
                    result = await resp.json()

                    return (
                        result["translations"][0]["text"]
                    )

                elif resp.status == 403:
                    print(
                        "❌ DeepL API Key 無效"
                        "或額度已滿。"
                    )
                    return text

                elif resp.status == 429:
                    await asyncio.sleep(
                        (attempt + 1) * 1.5
                    )
                    continue

                else:
                    print(
                        f"⚠️ DeepL API 錯誤: "
                        f"HTTP {resp.status}"
                    )
                    return text

        except Exception as e:
            print(f"⚠️ DeepL 連線異常：{e}")
            return text

    return text

# ---------------------------------------------------------
# 5. Webhook 翻譯轉發
# ---------------------------------------------------------

async def process_and_send(
    session,
    message,
    target_lang,
    target_cids
):

    translated_text = await translate_text(
        session,
        message.content,
        target_lang
    )

    for cid in target_cids:

        target_channel = bot.get_channel(cid)

        if not target_channel:
            continue

        try:
            # 找現有 Webhook
            webhooks = await target_channel.webhooks()

            webhook = discord.utils.get(
                webhooks,
                name="Translator Webhook"
            )

            # 沒有就建立
            if webhook is None:
                webhook = (
                    await target_channel.create_webhook(
                        name="Translator Webhook"
                    )
                )

            # 用原作者名稱、頭像發送
            await webhook.send(
                content=translated_text,
                username=message.author.display_name,
                avatar_url=(
                    message.author.display_avatar.url
                ),
                allowed_mentions=
                discord.AllowedMentions.none()
            )

        except discord.Forbidden:
            print(
                f"❌ 頻道 {cid} 無法使用 Webhook，"
                f"請確認 Bot 有管理 Webhook 權限。"
            )

        except Exception as e:
            print(
                f"⚠️ 無法發送訊息至頻道 "
                f"{cid}：{e}"
            )

# ---------------------------------------------------------
# 6. 啟動
# ---------------------------------------------------------

@bot.event
async def on_ready():

    global http_session

    if (
        http_session is None
        or http_session.closed
    ):
        http_session = aiohttp.ClientSession()

    print(
        f"🎉 成功連線！"
        f"機器人名稱：{bot.user}"
    )

    try:
        synced = await bot.tree.sync()

        print(
            f"✅ 已成功同步 "
            f"{len(synced)} 個斜線指令"
        )

    except Exception as e:
        print(f"⚠️ 同步指令失敗：{e}")

# ---------------------------------------------------------
# 7. 設定頻道指令
# ---------------------------------------------------------

@bot.tree.command(
    name="設定頻道",
    description="設定翻譯機器人的連動頻道與語言"
)
@app_commands.describe(
    channel="請選擇要綁定的頻道",
    language="請選擇翻譯輸出的語言",
    group="請輸入群組名稱"
)
@app_commands.choices(
    language=[
        app_commands.Choice(
            name="中文",
            value="ZH"
        ),
        app_commands.Choice(
            name="英文 (美式)",
            value="EN-US"
        ),
        app_commands.Choice(
            name="日文",
            value="JA"
        ),
        app_commands.Choice(
            name="韓文",
            value="KO"
        ),
        app_commands.Choice(
            name="俄文",
            value="RU"
        ),
        app_commands.Choice(
            name="印尼文",
            value="ID"
        ),
        app_commands.Choice(
            name="西班牙文",
            value="ES"
        ),
        app_commands.Choice(
            name="泰文",
            value="TH"
        ),
    ]
)
async def setup_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    language: app_commands.Choice[str],
    group: str
):

    CHANNEL_CONFIG[channel.id] = {
        "lang": language.value,
        "group": group
    }

    save_config()

    success_msg = (
        f"✅ **設定成功！**\n"
        f"📍 **目標頻道**："
        f"{channel.mention}\n"
        f"🌐 **輸出語言**："
        f"{language.name}\n"
        f"👥 **所屬群組**："
        f"`{group}`"
    )

    await interaction.response.send_message(
        success_msg
    )

# ---------------------------------------------------------
# 8. 查詢設定
# ---------------------------------------------------------

@bot.tree.command(
    name="查詢設定",
    description="查看目前所有頻道的翻譯設定"
)
async def check_config(
    interaction: discord.Interaction
):

    if not CHANNEL_CONFIG:
        await interaction.response.send_message(
            "目前沒有任何頻道設定喔！",
            ephemeral=True
        )
        return

    msg = "**當前翻譯頻道設定清單：**\n"

    for cid, data in CHANNEL_CONFIG.items():
        msg += (
            f"<#{cid}> ➔ "
            f"群組: `{data['group']}` | "
            f"語言: `{data['lang']}`\n"
        )

    await interaction.response.send_message(
        msg,
        ephemeral=True
    )

# ---------------------------------------------------------
# 9. 收到訊息後翻譯
# ---------------------------------------------------------

@bot.event
async def on_message(
    message: discord.Message
):

    # 忽略 Bot / Webhook / 空訊息
    if (
        message.author.bot
        or message.webhook_id is not None
        or not message.content.strip()
    ):
        return

    src_channel_id = message.channel.id

    if src_channel_id in CHANNEL_CONFIG:

        src_info = (
            CHANNEL_CONFIG[src_channel_id]
        )

        current_group = src_info["group"]

        lang_to_channels = {}

        for cid, config in CHANNEL_CONFIG.items():

            if (
                config["group"]
                == current_group
                and cid != src_channel_id
            ):

                lang = config["lang"]

                if lang not in lang_to_channels:
                    lang_to_channels[lang] = []

                lang_to_channels[lang].append(cid)

        if (
            lang_to_channels
            and http_session
        ):

            async with message.channel.typing():

                tasks = []

                for (
                    lang,
                    target_cids
                ) in lang_to_channels.items():

                    tasks.append(
                        process_and_send(
                            http_session,
                            message,
                            lang,
                            target_cids
                        )
                    )

                await asyncio.gather(*tasks)

    await bot.process_commands(message)

# ---------------------------------------------------------
# 10. 主程式
# ---------------------------------------------------------

async def main():

    keep_alive()

    if not BOT_TOKEN:
        print(
            "❌ 錯誤：找不到 "
            "DC_BOT_TOKEN 環境變數！"
        )
        return

    while True:

        try:
            print(
                "🚀 嘗試連線至 Discord..."
            )

            async with bot:
                await bot.start(BOT_TOKEN)

        except discord.errors.HTTPException as e:

            if e.status == 429:
                print(
                    "⚠️ Discord 429 限制，"
                    "60 秒後重試..."
                )
                await asyncio.sleep(60)

            else:
                print(
                    f"❌ 連線異常 ({e.status})，"
                    f"10 秒後重試..."
                )
                await asyncio.sleep(10)

        except Exception as e:
            print(
                f"❌ 發生錯誤 ({e})，"
                f"10 秒後重試..."
            )
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
