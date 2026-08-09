import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import json
import os
import threading
from flask import Flask

# 嘗試載入本地端的 .env (如果是 Render 雲端環境會自動忽略)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------
# 1. 雲端環境 Web 保活伺服器 (整合至單一檔案)
# ---------------------------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive and running!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()

# ---------------------------------------------------------
# 2. 環境變數與配置檔設定
# ---------------------------------------------------------
BOT_TOKEN = os.getenv("DC_BOT_TOKEN")
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")
CONFIG_FILE = "channel_config.json"

# 讀取本地頻道配置 (注意：Render 免費版每次重啟會清空此檔案)
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        try:
            CHANNEL_CONFIG = {int(k): v for k, v in json.load(f).items()}
        except json.JSONDecodeError:
            CHANNEL_CONFIG = {}
else:
    CHANNEL_CONFIG = {}

def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(CHANNEL_CONFIG, f, indent=4, ensure_ascii=False)

# ---------------------------------------------------------
# 3. Discord Bot 初始化
# ---------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 全局 aiohttp Session，提升效能
http_session = None 

# ---------------------------------------------------------
# 4. 翻譯核心邏輯 (Google GTX 泰文 + DeepL)
# ---------------------------------------------------------
async def translate_text(session, text, target_lang, max_retries=3):
    # 泰文或沒有設定 DeepL Key 時，強制走 Google GTX 免費通道
    if target_lang == "TH" or not DEEPL_API_KEY:
        url = "https://translate.googleapis.com/translate_a/single"
        tl = "th" if target_lang == "TH" else target_lang.lower()
        params = {"client": "gtx", "sl": "auto", "tl": tl, "dt": "t", "q": text}
        try:
            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return "".join([item[0] for item in data[0] if item[0]])
                else:
                    print(f"❌ Google GTX 翻譯失敗: HTTP {resp.status}")
                    return text
        except Exception as e:
            print(f"❌ Google GTX 發生錯誤：{e}")
            return text

    # DeepL 翻譯邏輯
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
# 5. 轉發與指令邏輯
# ---------------------------------------------------------
async def process_and_send(session, message, target_lang, target_cids):
    translated_text = await translate_text(session, message.content, target_lang)
    # 加上原作者名稱標籤
    send_text = f"**{message.author.display_name}**：{translated_text}"
    
    for cid in target_cids:
        target_channel = bot.get_channel(cid)
        if target_channel:
            try:
                await target_channel.send(send_text)
            except Exception as e:
                print(f"⚠️ 無法發送訊息至頻道 {cid}：{e}")

@bot.event
async def on_ready():
    global http_session
    if http_session is None or http_session.closed:
        http_session = aiohttp.ClientSession()
        
    print(f'🎉 成功連線！機器人名稱：{bot.user}')
    try:
        synced = await bot.tree.sync()
        print(f"✅ 已成功同步 {len(synced)} 個斜線指令")
    except Exception as e:
        print(f"⚠️ 同步指令失敗：{e}")

@bot.tree.command(name="設定頻道", description="設定翻譯機器人的連動頻道與語言")
@app_commands.describe(
    channel="請選擇要綁定的頻道 (必填)",
    language="請選擇翻譯輸出的語言 (必填)",
    group="請輸入群組名稱，相同名稱的頻道會互相轉發 (必填)"
)
@app_commands.choices(language=[
    app_commands.Choice(name="中文", value="ZH"),
    app_commands.Choice(name="英文 (美式)", value="EN-US"),
    app_commands.Choice(name="日文", value="JA"),
    app_commands.Choice(name="韓文", value="KO"),
    app_commands.Choice(name="俄文", value="RU"),
    app_commands.Choice(name="印尼文", value="ID"),
    app_commands.Choice(name="西班牙文", value="ES"),
    app_commands.Choice(name="泰文", value="TH"),
])
async def setup_channel(interaction: discord.Interaction, channel: discord.TextChannel, language: app_commands.Choice[str], group: str):
    CHANNEL_CONFIG[channel.id] = {
        "lang": language.value,
        "group": group
    }
    
    save_config()

    success_msg = (
        f"✅ **設定成功！**\n"
        f"📍 **目標頻道**：{channel.mention}\n"
        f"🌐 **輸出語言**：{language.name}\n"
        f"👥 **所屬群組**：`{group}`"
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
    if message.author.bot or not message.content.strip():
        return

    src_channel_id = message.channel.id
    if src_channel_id in CHANNEL_CONFIG:
        src_info = CHANNEL_CONFIG[src_channel_id]
        current_group = src_info["group"]
        
        # 整理出同群組且非原發言頻道的 目標語言 ➔ 目標頻道ID清單
        lang_to_channels = {}
        for cid, config in CHANNEL_CONFIG.items():
            if config["group"] == current_group and cid != src_channel_id:
                lang = config["lang"]
                if lang not in lang_to_channels:
                    lang_to_channels[lang] = []
                lang_to_channels[lang].append(cid)

        # 進行翻譯與轉發
        if lang_to_channels and http_session:
            async with message.channel.typing():
                tasks = []
                for lang, target_cids in lang_to_channels.items():
                    tasks.append(process_and_send(http_session, message, lang, target_cids))
                await asyncio.gather(*tasks)

    await bot.process_commands(message)

# ---------------------------------------------------------
# 6. 主程式進入點 (加入雲端防斷線重連循環)
# ---------------------------------------------------------
async def main():
    keep_alive()  # 啟動 Web 保活
    
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
                print("⚠️ 觸發 Discord 防火牆 429 限制，等待 60 秒後自動重試...")
                await asyncio.sleep(60)
            else:
                print(f"❌ 連線異常 ({e.status})，10 秒後重試...")
                await asyncio.sleep(10)
        except Exception as e:
            print(f"❌ 發生錯誤 ({e})，10 秒後重試...")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
