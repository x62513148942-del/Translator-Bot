import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import json
import os
from aiohttp import web

# 載入 .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------
# 1. Web 保活 (aiohttp 原生)
# ---------------------------------------------------------

async def handle_home(request):
    return web.Response(text="Bot is alive and running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_home)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 保活伺服器已啟動，Port: {port}", flush=True)

# ---------------------------------------------------------
# 2. 設定檔載入/儲存
# ---------------------------------------------------------

BOT_TOKEN = os.getenv("DC_BOT_TOKEN")
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
        print(f"❌ 儲存設定檔失敗: {e}", flush=True)

# ---------------------------------------------------------
# 3. Discord Bot 初始化
# ---------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"🎉 機器人已成功登入：{bot.user}", flush=True)
    print(f"🟢 翻譯引擎已切換為：微軟 Edge 官方 API (防封鎖最終版)", flush=True)
    try:
        synced = await bot.tree.sync()
        print(f"✅ 已成功同步 {len(synced)} 個斜線指令", flush=True)
    except Exception as e:
        print(f"⚠️ 同步指令失敗：{e}", flush=True)

# ---------------------------------------------------------
# 4. 微軟 Edge 授權翻譯核心 (徹底繞過 429 限制)
# ---------------------------------------------------------

# 微軟專用語系代碼對照表
MS_LANG_MAP = {
    "ZH": "zh-Hant",   # 繁體中文
    "EN-US": "en",     # 英文
    "JA": "ja",        # 日文
    "KO": "ko",        # 韓文
    "RU": "ru",        # 俄文
    "ID": "id",        # 印尼文
    "ES": "es",        # 西班牙文
    "TH": "th"         # 泰文
}

async def translate_text(text, target_lang):
    if not text or not text.strip():
        return text

    tl = MS_LANG_MAP.get(target_lang, target_lang.lower())
    
    # 步驟 1：向微軟伺服器索取合法通行證 (Token)
    auth_url = "https://edge.microsoft.com/translate/auth"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            # 取得 Token
            async with session.get(auth_url, headers=headers, timeout=5) as auth_resp:
                if auth_resp.status == 200:
                    raw_token = await auth_resp.text()
                    token = raw_token.strip(' "') # 確保移除多餘引號
                else:
                    print(f"❌ [獲取 Token 失敗] HTTP {auth_resp.status}", flush=True)
                    return text

            # 步驟 2：帶入 Token 進行合法翻譯請求
            if token:
                api_url = f"https://api.cognitive.microsofttranslator.com/translate?api-version=3.0&to={tl}"
                api_headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": headers["User-Agent"]
                }
                body = [{"Text": text}]
                
                async with session.post(api_url, headers=api_headers, json=body, timeout=8) as trans_resp:
                    if trans_resp.status == 200:
                        data = await trans_resp.json()
                        translated = data[0]["translations"][0]["text"]
                        print(f"🈳 [微軟 Edge 翻譯成功]: '{text}' ➔ '{translated}'", flush=True)
                        return translated
                    else:
                        error_msg = await trans_resp.text()
                        print(f"❌ [翻譯請求被拒絕] HTTP {trans_resp.status}: {error_msg}", flush=True)

    except Exception as e:
        print(f"⚠️ 翻譯模組發生嚴重錯誤: {e}", flush=True)
        
    return text

# ---------------------------------------------------------
# 5. 訊息轉發與 Webhook
# ---------------------------------------------------------

async def process_and_send(message, target_lang, target_cids):
    translated_text = await translate_text(message.content, target_lang)

    for cid in target_cids:
        target_channel = bot.get_channel(cid)
        if not target_channel:
            try:
                target_channel = await bot.fetch_channel(cid)
            except Exception as e:
                print(f"❌ 無法讀取目標頻道 {cid}: {e}", flush=True)
                continue

        try:
            webhooks = await target_channel.webhooks()
            webhook = discord.utils.get(webhooks, name="Translator Webhook")

            if webhook is None:
                webhook = await target_channel.create_webhook(name="Translator Webhook")

            await webhook.send(
                content=translated_text,
                username=message.author.display_name,
                avatar_url=message.author.display_avatar.url,
                allowed_mentions=discord.AllowedMentions.none()
            )
            print(f"🎉 成功將翻譯訊息轉發至頻道 {cid}！", flush=True)

        except discord.Forbidden:
            print(f"❌ 權限錯誤：機器人在頻道 {cid} 缺少「管理 Webhook」權限！", flush=True)
        except Exception as e:
            print(f"⚠️ 發送訊息至頻道 {cid} 失敗：{e}", flush=True)

# ---------------------------------------------------------
# 6. 事件監聽與指令
# ---------------------------------------------------------

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
    clean_group = str(group).strip()
    CHANNEL_CONFIG[channel.id] = {
        "lang": language.value,
        "group": clean_group
    }
    save_config()

    success_msg = (
        f"✅ **設定成功！**\n"
        f"📍 **目標頻道**：{channel.mention}\n"
        f"🌐 **輸出語言**：{language.name}\n"
        f"👥 **所屬群組**：`{clean_group}`"
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

    src_channel_id = message.channel.id

    if src_channel_id in CHANNEL_CONFIG:
        src_info = CHANNEL_CONFIG[src_channel_id]
        current_group = str(src_info["group"]).strip()

        lang_to_channels = {}

        for cid, config in CHANNEL_CONFIG.items():
            cfg_group = str(config["group"]).strip()
            if cfg_group == current_group and cid != src_channel_id:
                lang = config["lang"]
                if lang not in lang_to_channels:
                    lang_to_channels[lang] = []
                lang_to_channels[lang].append(cid)

        if lang_to_channels:
            async with message.channel.typing():
                tasks = []
                for lang, target_cids in lang_to_channels.items():
                    tasks.append(process_and_send(message, lang, target_cids))
                await asyncio.gather(*tasks)

    await bot.process_commands(message)

# ---------------------------------------------------------
# 7. 主程式
# ---------------------------------------------------------

async def main():
    await start_web_server()

    if not BOT_TOKEN:
        print("❌ 錯誤：找不到 DC_BOT_TOKEN 環境變數！", flush=True)
        return

    while True:
        try:
            print("🚀 嘗試連線至 Discord...", flush=True)
            async with bot:
                await bot.start(BOT_TOKEN)

        except discord.errors.HTTPException as e:
            if e.status == 429:
                print("⚠️ Discord 429 限制，60 秒後重試...", flush=True)
                await asyncio.sleep(60)
            else:
                print(f"❌ 連線異常 ({e.status})，10 秒後重試...", flush=True)
                await asyncio.sleep(10)

        except Exception as e:
            print(f"❌ 發生錯誤 ({e})，10 秒後重試...", flush=True)
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
