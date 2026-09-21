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

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            try:
                return {int(k): v for k, v in json.load(f).items()}
            except json.JSONDecodeError:
                return {}
    return {}

CHANNEL_CONFIG = load_config()

def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(CHANNEL_CONFIG, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"❌ 儲存設定檔失敗: {e}")

# ---------------------------------------------------------
# 3. Discord Bot 初始化
# ---------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True

class TranslatorBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.http_session = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()
        try:
            synced = await self.tree.sync()
            print(f"✅ 已成功同步 {len(synced)} 個斜線指令")
        except Exception as e:
            print(f"⚠️ 同步指令失敗：{e}")

    async def close(self):
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()

bot = TranslatorBot()

# ---------------------------------------------------------
# 4. 翻譯功能
# ---------------------------------------------------------

async def translate_text(session, text, target_lang, max_retries=3):
    if not text or not text.strip():
        return text

    if target_lang == "TH" or not DEEPL_API_KEY:
        url = "https://translate.googleapis.com/translate_a/single"
        tl = "th" if target_lang == "TH" else target_lang.lower()
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": tl,
            "dt": "t",
            "q": text
        }

        try:
            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return "".join(item[0] for item in data[0] if item and item[0])
                print(f"❌ Google GTX 翻譯失敗: HTTP {resp.status}")
                return text
        except Exception as e:
            print(f"❌ Google GTX 發生錯誤：{e}")
            return text

    url = "https://api-free.deepl.com/v2/translate" if DEEPL_API_KEY.endswith(":fx") else "https://api.deepl.com/v2/translate"
    headers = {"Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}"}
    data = {"text": [text], "target_lang": target_lang}

    for attempt in range(max_retries):
        try:
            async with session.post(url, headers=headers, json=data, timeout=10) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result["translations"][0]["text"]
                elif resp.status == 403:
                    print("❌ DeepL API Key 無效或額度已滿。")
                    return text
                elif resp.status == 429:
                    await asyncio.sleep((attempt + 1) * 1.5)
                    continue
                else:
                    print(f"⚠️ DeepL API 錯誤: HTTP {resp.status}")
                    return text
        except Exception as e:
            print(f"⚠️ DeepL 連線異常：{e}")
            return text

    return text

# ---------------------------------------------------------
# 5. Webhook 翻譯轉發（含 Debug 輸出）
# ---------------------------------------------------------

async def process_and_send(session, message, target_lang, target_cids):
    print(f"🚀 [DEBUG] 開始翻譯內容至語言 [{target_lang}]...")
    translated_text = await translate_text(session, message.content, target_lang)
    print(f"✅ [DEBUG] 翻譯結果：'{translated_text}'")

    for cid in target_cids:
        target_channel = bot.get_channel(cid)
        if not target_channel:
            try:
                target_channel = await bot.fetch_channel(cid)
            except Exception as e:
                print(f"❌ [DEBUG] 抓取頻道 {cid} 失敗：{e}")
                continue

        try:
            webhooks = await target_channel.webhooks()
            webhook = discord.utils.get(webhooks, name="Translator Webhook")

            if webhook is None:
                print(f"🛠️ [DEBUG] 在頻道 {cid} 建立新的 Webhook...")
                webhook = await target_channel.create_webhook(name="Translator Webhook")

            await webhook.send(
                content=translated_text,
                username=message.author.display_name,
                avatar_url=message.author.display_avatar.url,
                allowed_mentions=discord.AllowedMentions.none()
            )
            print(f"🎉 [DEBUG] 成功將翻譯轉發至頻道 {cid}")

        except discord.Forbidden:
            print(f"❌ [DEBUG] 權限不足！機器人在頻道 {cid} 沒有「管理 Webhook (Manage Webhooks)」權限！")
        except Exception as e:
            print(f"⚠️ [DEBUG] 發送訊息至頻道 {cid} 失敗：{e}")

# ---------------------------------------------------------
# 6. 事件與指令（含 Debug 輸出）
# ---------------------------------------------------------

@bot.event
async def on_ready():
    print(f"🎉 成功連線！機器人名稱：{bot.user}")

@bot.tree.command(name="設定頻道", description="設定翻譯機器人的連動頻道與語言")
@app_commands.describe(
    channel="請選擇要綁定的頻道",
    language="請選擇翻譯輸出的語言",
    group="請輸入群組名稱"
)
@app_commands.choices(
    language=[
        app_commands.Choice(name="中文", value="ZH"),
        app_commands.Choice(name="英文 (美式)", value="EN-US"),
        app_commands.Choice(name="日文", value="JA"),
        app_commands.Choice(name="韓文", value="KO"),
        app_commands.Choice(name="俄文", value="RU"),
        app_commands.Choice(name="印尼文", value="ID"),
        app_commands.Choice(name="西班牙文", value="ES"),
        app_commands.Choice(name="泰文", value="TH"),
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
        "group": str(group).strip()
    }
    save_config()

    success_msg = (
        f"✅ **設定成功！**\n"
        f"📍 **目標頻道**：{channel.mention}\n"
        f"🌐 **輸出語言**：{language.name}\n"
        f"👥 **所屬群組**：`{str(group).strip()}`"
    )
    await interaction.response.send_message(success_msg)

@bot.tree.command(name="查詢設定", description="查看目前所有頻道的翻譯設定")
async def check_config(interaction: discord.Interaction):
    if not CHANNEL_CONFIG:
        await interaction.response.send_message("目前沒有任何頻道設定喔！", ephemeral=True)
        return

    msg = "**當前翻譯頻道設定清單：**\n"
    for cid, data in CHANNEL_CONFIG.items():
        msg += f"<#{cid}> ➔ 群組: `{data['group']}` | 語言: `{data['lang']}`\n"

    await interaction.response.send_message(msg, ephemeral=True)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.webhook_id is not None:
        return

    print(f"🔍 [DEBUG] 收到訊息 | 頻道 ID: {message.channel.id} | 內容: '{message.content}'")

    if not message.content.strip():
        print("⚠️ [DEBUG] 訊息內文為空！請確認 Discord Developer Portal 的 'Message Content Intent' 有開啟！")
        return

    src_channel_id = message.channel.id

    if src_channel_id in CHANNEL_CONFIG:
        src_info = CHANNEL_CONFIG[src_channel_id]
        current_group = str(src_info["group"]).strip()
        print(f"🔍 [DEBUG] 發言頻道位在群組：'{current_group}'")

        lang_to_channels = {}

        for cid, config in CHANNEL_CONFIG.items():
            cfg_group = str(config["group"]).strip()
            if cfg_group == current_group and cid != src_channel_id:
                lang = config["lang"]
                if lang not in lang_to_channels:
                    lang_to_channels[lang] = []
                lang_to_channels[lang].append(cid)

        print(f"🔍 [DEBUG] 找到同群組的其他目標頻道：{lang_to_channels}")

        if lang_to_channels and bot.http_session:
            async with message.channel.typing():
                tasks = []
                for lang, target_cids in lang_to_channels.items():
                    tasks.append(
                        process_and_send(bot.http_session, message, lang, target_cids)
                    )
                await asyncio.gather(*tasks)
    else:
        print(f"⚠️ [DEBUG] 頻道 {src_channel_id} 尚未加入設定。當前所有設定檔：{CHANNEL_CONFIG}")

    await bot.process_commands(message)

# ---------------------------------------------------------
# 7. 主程式
# ---------------------------------------------------------

async def main():
    keep_alive()

    if not BOT_TOKEN:
        print("❌ 錯誤：找不到 DC_BOT_TOKEN 環境變數！")
        return

    while True:
        try:
            print("🚀 嘗試連線至 Discord...")
            async with bot:
                await bot.start(BOT_TOKEN)

        except discord.errors.HTTPException as e:
            if e.status == 429:
                print("⚠️ Discord 429 限制，60 秒後重試...")
                await asyncio.sleep(60)
            else:
                print(f"❌ 連線異常 ({e.status})，10 秒後重試...")
                await asyncio.sleep(10)

        except Exception as e:
            print(f"❌ 發生錯誤 ({e})，10 秒後重試...")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
