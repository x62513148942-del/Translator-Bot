import os
import asyncio
import threading
import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask

# ---------------------------------------------------------
# 1. Web 保活伺服器 (多執行緒背景執行，避免擋住 Discord Bot)
# ---------------------------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()

# ---------------------------------------------------------
# 2. 環境變數讀取
# ---------------------------------------------------------
TOKEN = os.environ.get("DC_BOT_TOKEN")
DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY")

# ---------------------------------------------------------
# 3. 翻譯核心邏輯 (DeepL + Google GTX API 防封鎖通道)
# ---------------------------------------------------------
# 支援 DeepL 的語言清單
DEEPL_SUPPORTED = {"zh-TW": "ZH", "zh-CN": "ZH", "EN": "EN-US", "JA": "JA", "KO": "KO", "RU": "RU"}

async def translate_with_google_gtx(session: aiohttp.ClientSession, text: str, target_lang: str) -> str:
    """使用 Google GTX 專用免封鎖 API 通道 (支援泰文 TH、印尼文 ID 等)"""
    lang_map = {
        "zh-TW": "zh-TW",
        "zh-CN": "zh-CN",
        "EN": "en",
        "JA": "ja",
        "KO": "ko",
        "TH": "th",
        "ID": "id",
        "VI": "vi",
        "RU": "ru"
    }
    tl = lang_map.get(target_lang, target_lang.lower())
    url = "https://translate.googleapis.com/translate_a/single"
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
                translated_text = "".join([sentence[0] for sentence in data[0] if sentence[0]])
                return translated_text
            else:
                print(f"⚠️ Google GTX 翻譯回應異常 HTTP {resp.status}")
                return text
    except Exception as e:
        print(f"⚠️ Google GTX 翻譯連線失敗：{e}")
        return text

async def translate_text(session: aiohttp.ClientSession, text: str, target_lang: str) -> str:
    # 如果目標語言是泰文(TH)或 DeepL 不支援的語言，直接走 Google GTX 通道
    if target_lang not in DEEPL_SUPPORTED or not DEEPL_API_KEY:
        return await translate_with_google_gtx(session, text, target_lang)

    # DeepL 支援的語言走 DeepL API
    deepl_lang = DEEPL_SUPPORTED[target_lang]
    url = "https://api-free.deepl.com/v2/translate" if DEEPL_API_KEY.endswith(":fx") else "https://api.deepl.com/v2/translate"
    headers = {"Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}"}
    data = {"text": [text], "target_lang": deepl_lang}

    try:
        async with session.post(url, headers=headers, json=data, timeout=10) as resp:
            if resp.status == 200:
                result = await resp.json()
                return result["translations"][0]["text"]
            else:
                return await translate_with_google_gtx(session, text, target_lang)
    except Exception:
        return await translate_with_google_gtx(session, text, target_lang)

# ---------------------------------------------------------
# 4. Discord Bot 機器人邏輯
# ---------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 儲存頻道翻譯設定 {channel_id: target_lang}
channel_settings = {}
session: aiohttp.ClientSession = None

@bot.event
async def on_ready():
    global session
    session = aiohttp.ClientSession()
    try:
        synced = await bot.tree.sync()
        print(f"🎉 成功連線！機器人名稱：{bot.user}")
        print(f"✅ 已成功同步 {len(synced)} 個斜線指令")
    except Exception as e:
        print(f"❌ 指令同步失敗：{e}")

@bot.tree.command(name="設定頻道", description="設定此頻道的自動翻譯目標語言")
@app_commands.choices(語言=[
    app_commands.Choice(name="簡文", value="zh-CN"),
    app_commands.Choice(name="英文", value="EN"),
    app_commands.Choice(name="日文", value="JA"),
    app_commands.Choice(name="韓文", value="KO"),
    app_commands.Choice(name="泰文", value="TH"),
    app_commands.Choice(name="印尼文", value="ID"),
    app_commands.Choice(name="越南文", value="VI"),
    app_commands.Choice(name="俄文", value="RU"),
])
async def set_channel(interaction: discord.Interaction, 語言: app_commands.Choice[str]):
    channel_settings[interaction.channel_id] = 語言.value
    await interaction.response.send_message(f"✅ 已將此頻道的自動翻譯語言設定為：**{語言.name}**")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    target_lang = channel_settings.get(message.channel.id)
    if target_lang:
        async with message.channel.typing():
            translated = await translate_text(session, message.content, target_lang)
            if translated and translated != message.content:
                await message.reply(f"🌐 **[{target_lang}]** {translated}", mention_author=False)

    await bot.process_commands(message)

# ---------------------------------------------------------
# 5. 啟動程序
# ---------------------------------------------------------
if __name__ == "__main__":
    keep_alive()  # 背景啟動 Web 伺服器
    if TOKEN:
        bot.run(TOKEN)  # 正式啟動 Discord 機器人
    else:
        print("❌ 錯誤：找不到 DC_BOT_TOKEN 環境變數！")
