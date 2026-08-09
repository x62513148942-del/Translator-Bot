import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import json
import os
from dotenv import load_dotenv
from deep_translator import GoogleTranslator
from web import keep_alive

load_dotenv()
BOT_TOKEN = os.getenv("DC_BOT_TOKEN")
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")
CONFIG_FILE = "channel_config.json"

# 語言代碼對照表 (DeepL 代碼 ➔ GoogleTranslator 代碼)
GOOGLE_LANG_MAP = {
    "ZH": "zh-TW",
    "EN-US": "en",
    "JA": "ja",
    "KO": "ko",
    "RU": "ru",
    "ID": "id",
    "ES": "es",
    "TH": "th"
}

# 讀取頻道設定檔
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        CHANNEL_CONFIG = {int(k): v for k, v in json.load(f).items()}
else:
    CHANNEL_CONFIG = {}

def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(CHANNEL_CONFIG, f, indent=4, ensure_ascii=False)

intents = discord.Intents.default()
intents.message_content = True

# 自訂 Bot 類別以優化 Session 與 Hook 管理
class TranslatorBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.session = None

    async def setup_hook(self):
        # 建立全域共享的 aiohttp Session
        self.session = aiohttp.ClientSession()
        # 同步斜線指令
        try:
            synced = await self.tree.sync()
            print(f"✅ 已成功同步 {len(synced)} 個斜線指令")
        except Exception as e:
            print(f"⚠️ 同步指令失敗：{e}")

    async def close(self):
        # 關閉 Bot 時同時關閉 Session
        if self.session:
            await self.session.close()
        await super().close()

bot = TranslatorBot()

# Google 翻譯（使用 deep-translator 搭配 asyncio.to_thread 防止阻塞）
async def translate_with_google(text: str, target_lang: str) -> str:
    target = GOOGLE_LANG_MAP.get(target_lang, "zh-TW")
    try:
        translated = await asyncio.to_thread(
            lambda: GoogleTranslator(source='auto', target=target).translate(text)
        )
        return translated if translated else text
    except Exception as e:
        print(f"❌ Google 翻譯 ({target_lang}) 發生錯誤：{e}")
        return text

# DeepL 翻譯主邏輯
async def translate_text(session: aiohttp.ClientSession, text: str, target_lang: str, max_retries: int = 3) -> str:
    # 泰文或沒有設定 DeepL Key 時直接走 Google 翻譯
    if target_lang == "TH" or not DEEPL_API_KEY:
        return await translate_with_google(text, target_lang)

    url = "https://api-free.deepl.com/v2/translate" if DEEPL_API_KEY.endswith(":fx") else "https://api.deepl.com/v2/translate"
    headers = {"Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}"}
    data = {
        "text": [text],
        "target_lang": target_lang
    }

    for attempt in range(max_retries):
        try:
            async with session.post(url, headers=headers, json=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result["translations"][0]["text"]
                elif resp.status == 403:
                    print("⚠️ DeepL Key 無效或額度上限，改用 Google 翻譯備援。")
                    return await translate_with_google(text, target_lang)
                elif resp.status == 429:
                    await asyncio.sleep((attempt + 1) * 1.5)
                    continue
                else:
                    print(f"⚠️ DeepL 錯誤 HTTP {resp.status}，改用 Google 翻譯備援。")
                    return await translate_with_google(text, target_lang)
        except Exception as e:
            print(f"⚠️ DeepL 請求異常：{e}，改用 Google 翻譯備援。")
            return await translate_with_google(text, target_lang)

    return await translate_with_google(text, target_lang)

async def process_and_send(session: aiohttp.ClientSession, message: discord.Message, target_lang: str, target_cids: list):
    translated_text = await translate_text(session, message.content, target_lang)
    send_text = f"**{message.author.display_name}**：{translated_text}"
    
    for cid in target_cids:
        target_channel = bot.get_channel(cid)
        if target_channel:
            try:
                await target_channel.send(send_text)
            except Exception as e:
                print(f"❌ 無法發送訊息至頻道 {cid}：{e}")

@bot.event
async def on_ready():
    print(f'🎉 成功連線！機器人名稱：{bot.user}')

@bot.tree.command(name="設定頻道", description="設定翻譯機器人的連動頻道與語言")
@app_commands.describe(
    channel="請選擇要綁定的頻道 (必填)",
    language="請選擇翻譯輸出的語言 (必填)",
    group="請輸入群組名稱，相同名稱的頻道會互相轉發 (必填)"
)
@app_commands.choices(language=[
    app_commands.Choice(name="中文", value="ZH"),
    app_commands.Choice(name="英文", value="EN-US"),
    app_commands.Choice(name="日文", value="JA"),
    app_commands.Choice(name="韓文", value="KO"),
    app_commands.Choice(name="俄文", value="RU"),
    app_commands.Choice(name="印尼文", value="ID"),
    app_commands.Choice(name="西班牙文", value="ES"),
    app_commands.Choice(name="泰文", value="TH"),
])
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
        
        lang_to_channels = {}
        for cid, config in CHANNEL_CONFIG.items():
            if config["group"] == current_group and cid != src_channel_id:
                lang = config["lang"]
                if lang not in lang_to_channels:
                    lang_to_channels[lang] = []
                lang_to_channels[lang].append(cid)

        if lang_to_channels and bot.session:
            tasks = []
            for lang, target_cids in lang_to_channels.items():
                tasks.append(
                    process_and_send(bot.session, message, lang, target_cids)
                )
            await asyncio.gather(*tasks)

    await bot.process_commands(message)

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ 未設定 DC_BOT_TOKEN 環境變數！")
    else:
        keep_alive()
        bot.run(BOT_TOKEN)
