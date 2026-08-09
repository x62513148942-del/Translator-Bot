import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import json
import os
from dotenv import load_dotenv
from web import keep_alive

load_dotenv()
BOT_TOKEN = os.getenv("DC_BOT_TOKEN")
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")
CONFIG_FILE = "channel_config.json"

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
bot = commands.Bot(command_prefix="!", intents=intents)

async def translate_text(session, text, target_lang, max_retries=3):
    if target_lang == "TH":
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": "th",
            "dt": "t",
            "q": text
        }
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return "".join([item[0] for item in data[0] if item[0]])
                else:
                    print(f"❌ Google 翻譯(泰文) 失敗: HTTP {resp.status}")
                    return text
        except Exception as e:
            print(f"❌ Google 翻譯(泰文) 發生錯誤：{e}")
            return text

    if not DEEPL_API_KEY:
        print("❌ 未找到 DEEPL_API_KEY 環境變數！")
        return text

    if DEEPL_API_KEY.endswith(":fx"):
        url = "https://api-free.deepl.com/v2/translate"
    else:
        url = "https://api.deepl.com/v2/translate"

    headers = {"Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}"}
    data = {
        "text": [text],
        "target_lang": target_lang
    }

    for attempt in range(max_retries):
        async with session.post(url, headers=headers, json=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                return result["translations"][0]["text"]
            elif resp.status == 403:
                print("❌ DeepL API Key 無效，請檢查 Key 是否填錯或額度已滿。")
                return text
            elif resp.status == 429:
                await asyncio.sleep((attempt + 1) * 1.5)
                continue
            else:
                print(f"DeepL API 錯誤: HTTP {resp.status}")
                return text 
    return text

async def process_and_send(session, message, target_lang, target_cids):
    translated_text = await translate_text(session, message.content, target_lang)
    send_text = f"**{message.author.display_name}**：{translated_text}"
    
    for cid in target_cids:
        target_channel = bot.get_channel(cid)
        if target_channel:
            try:
                await target_channel.send(send_text)
            except Exception as e:
                print(f"無法發送訊息至頻道 {cid}：{e}")

@bot.event
async def on_ready():
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
    app_commands.Choice(name="🇹🇼 繁體中文", value="ZH"),
    app_commands.Choice(name="🇺🇸 英文 (美式)", value="EN-US"),
    app_commands.Choice(name="🇯🇵 日文", value="JA"),
    app_commands.Choice(name="🇰🇷 韓文", value="KO"),
    app_commands.Choice(name="🇷🇺 俄文", value="RU"),
    app_commands.Choice(name="🇮🇩 印尼文", value="ID"),
    app_commands.Choice(name="🇪🇸 西班牙文", value="ES"),
    app_commands.Choice(name="🇹🇭 泰文", value="TH"),
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
async def on_message(message):
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

        if lang_to_channels:
            async with aiohttp.ClientSession() as session:
                tasks = []
                for lang, target_cids in lang_to_channels.items():
                    tasks.append(
                        process_and_send(session, message, lang, target_cids)
                    )
                await asyncio.gather(*tasks)

    await bot.process_commands(message)

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ 未設定 DC_BOT_TOKEN 環境變數！")
    else:
        keep_alive()
        bot.run(BOT_TOKEN)
