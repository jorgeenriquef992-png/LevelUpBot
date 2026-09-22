import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiosqlite
import random
import os
import json
import re
import ast
import operator
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE = "levelup.db"

XP_MIN = 15
XP_MAX = 25
XP_COOLDOWN = 60

def calculate_level(xp: int) -> int:
    return int(0.1 * (xp ** 0.5))

def xp_for_level(level: int) -> int:
    return int((level / 0.1) ** 2)

SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}

def safe_eval(expr: str):
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**")
    expr = re.sub(r"(\d+(?:\.\d+)?)%", r"(\1/100)", expr)
    try:
        node = ast.parse(expr, mode="eval").body
        return _eval_node(node)
    except Exception:
        return None

def _eval_node(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Num):
        return node.n
    if isinstance(node, ast.BinOp):
        op = SAFE_OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError("Operador no permitido")
        return op(_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op = SAFE_OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError("Operador no permitido")
        return op(_eval_node(node.operand))
    raise ValueError("Expresión no permitida")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
cooldowns = {}

async def init_db():
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            xp INTEGER DEFAULT 0, level INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS guild_config (
            guild_id INTEGER PRIMARY KEY, levelup_channel_id INTEGER,
            admin_roles TEXT DEFAULT '[]', max_level INTEGER DEFAULT 0)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS ignored_channels (
            guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, channel_id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS classes (
            guild_id INTEGER NOT NULL, class_name TEXT NOT NULL,
            PRIMARY KEY (guild_id, class_name))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS class_rewards (
            guild_id INTEGER NOT NULL, class_name TEXT NOT NULL,
            level INTEGER NOT NULL, rewards TEXT NOT NULL,
            PRIMARY KEY (guild_id, class_name, level))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS user_class (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            class_name TEXT NOT NULL, PRIMARY KEY (guild_id, user_id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS auto_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            trigger_word TEXT NOT NULL, responses TEXT NOT NULL,
            channel_ids TEXT DEFAULT '[]')""")
        await db.execute("""CREATE TABLE IF NOT EXISTS scheduled_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL, content TEXT NOT NULL,
            embed_data TEXT, send_at TEXT NOT NULL, sent INTEGER DEFAULT 0)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS economy (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            money INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS shops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL, name TEXT NOT NULL)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL, item_name TEXT NOT NULL,
            price INTEGER NOT NULL, description TEXT DEFAULT '')""")
        await db.execute("""CREATE TABLE IF NOT EXISTS inventory (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            item_name TEXT NOT NULL, quantity INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id, item_name))""")
        await db.commit()

# ---------- XP ----------
async def get_user_data(guild_id, user_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT xp, level FROM users WHERE guild_id=? AND user_id=?",
            (guild_id, user_id)
        ) as cur:
            row = await cur.fetchone()
            return {"xp": row[0], "level": row[1]} if row else {"xp": 0, "level": 0}

async def set_user_xp(guild_id, user_id, xp):
    level = calculate_level(xp)
    config = await get_guild_config(guild_id)
    max_level = config.get("max_level", 0)
    if max_level > 0 and level > max_level:
        level = max_level
        xp = xp_for_level(max_level)
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO users (guild_id, user_id, xp, level) VALUES (?,?,?,?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=excluded.xp, level=excluded.level""",
            (guild_id, user_id, xp, level)
        )
        await db.commit()
    return level

async def add_xp(guild_id, user_id, amount):
    data = await get_user_data(guild_id, user_id)
    old_level = data["level"]
    new_xp = max(0, data["xp"] + amount)
    config = await get_guild_config(guild_id)
    max_level = config.get("max_level", 0)
    if max_level > 0:
        max_xp = xp_for_level(max_level)
        if new_xp > max_xp:
            new_xp = max_xp
    new_level = await set_user_xp(guild_id, user_id, new_xp)
    return new_xp, new_level, new_level > old_level

async def get_guild_config(guild_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT levelup_channel_id, admin_roles, max_level FROM guild_config WHERE guild_id=?",
            (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {
                    "levelup_channel_id": row[0],
                    "admin_roles": json.loads(row[1]) if row[1] else [],
                    "max_level": row[2] or 0
                }
            return {"levelup_channel_id": None, "admin_roles": [], "max_level": 0}

async def set_levelup_channel(guild_id, channel_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO guild_config (guild_id, levelup_channel_id) VALUES (?,?)
            ON CONFLICT(guild_id) DO UPDATE SET levelup_channel_id=excluded.levelup_channel_id""",
            (guild_id, channel_id)
        )
        await db.commit()

async def set_admin_roles(guild_id, role_ids):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO guild_config (guild_id, admin_roles) VALUES (?,?)
            ON CONFLICT(guild_id) DO UPDATE SET admin_roles=excluded.admin_roles""",
            (guild_id, json.dumps(role_ids))
        )
        await db.commit()

async def set_max_level(guild_id, max_level):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO guild_config (guild_id, max_level) VALUES (?,?)
            ON CONFLICT(guild_id) DO UPDATE SET max_level=excluded.max_level""",
            (guild_id, max_level)
        )
        await db.commit()

async def is_channel_ignored(guild_id, channel_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT 1 FROM ignored_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id)
        ) as cur:
            return await cur.fetchone() is not None

async def add_ignored_channel(guild_id, channel_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO ignored_channels (guild_id, channel_id) VALUES (?,?)",
            (guild_id, channel_id)
        )
        await db.commit()

async def remove_ignored_channel(guild_id, channel_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "DELETE FROM ignored_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id)
        )
        await db.commit()

async def get_ignored_channels(guild_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT channel_id FROM ignored_channels WHERE guild_id=?",
            (guild_id,)
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

async def set_max_level(guild_id, max_level):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO guild_config (guild_id, max_level) VALUES (?,?)
            ON CONFLICT(guild_id) DO UPDATE SET max_level=excluded.max_level""",
            (guild_id, max_level)
        )
        await db.commit()

async def is_channel_ignored(guild_id, channel_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT 1 FROM ignored_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id)
        ) as cur:
            return await cur.fetchone() is not None

async def add_ignored_channel(guild_id, channel_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO ignored_channels (guild_id, channel_id) VALUES (?,?)",
            (guild_id, channel_id)
        )
        await db.commit()

async def remove_ignored_channel(guild_id, channel_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "DELETE FROM ignored_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id)
        )
        await db.commit()

async def get_ignored_channels(guild_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT channel_id FROM ignored_channels WHERE guild_id=?",
            (guild_id,)
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

async def add_class(guild_id, class_name):
    async with aiosqlite.connect(DATABASE) as db:
        try:
            await db.execute(
                "INSERT INTO classes (guild_id, class_name) VALUES (?,?)",
                (guild_id, class_name)
            )
            await db.commit()
            return True
        except Exception:
            return False

async def remove_class(guild_id, class_name):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("DELETE FROM classes WHERE guild_id=? AND class_name=?", (guild_id, class_name))
        await db.execute("DELETE FROM class_rewards WHERE guild_id=? AND class_name=?", (guild_id, class_name))
        await db.execute("DELETE FROM user_class WHERE guild_id=? AND class_name=?", (guild_id, class_name))
        await db.commit()

async def get_classes(guild_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT class_name FROM classes WHERE guild_id=? ORDER BY class_name",
            (guild_id,)
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

async def class_exists(guild_id, class_name):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT 1 FROM classes WHERE guild_id=? AND class_name=?",
            (guild_id, class_name)
        ) as cur:
            return await cur.fetchone() is not None

async def set_rewards(guild_id, class_name, level, rewards):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO class_rewards (guild_id, class_name, level, rewards) VALUES (?,?,?,?)
            ON CONFLICT(guild_id, class_name, level) DO UPDATE SET rewards=excluded.rewards""",
            (guild_id, class_name, level, json.dumps(rewards))
        )
        await db.commit()

async def get_rewards(guild_id, class_name, level):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT rewards FROM class_rewards WHERE guild_id=? AND class_name=? AND level=?",
            (guild_id, class_name, level)
        ) as cur:
            row = await cur.fetchone()
            return json.loads(row[0]) if row else []

async def get_all_rewards(guild_id, class_name):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT level, rewards FROM class_rewards WHERE guild_id=? AND class_name=? ORDER BY level",
            (guild_id, class_name)
        ) as cur:
            return {r[0]: json.loads(r[1]) for r in await cur.fetchall()}

async def delete_rewards(guild_id, class_name, level):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "DELETE FROM class_rewards WHERE guild_id=? AND class_name=? AND level=?",
            (guild_id, class_name, level)
        )
        await db.commit()

async def get_user_class(guild_id, user_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT class_name FROM user_class WHERE guild_id=? AND user_id=?",
            (guild_id, user_id)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

async def set_user_class(guild_id, user_id, class_name):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO user_class (guild_id, user_id, class_name) VALUES (?,?,?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET class_name=excluded.class_name""",
            (guild_id, user_id, class_name)
        )
        await db.commit()

async def remove_user_class(guild_id, user_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "DELETE FROM user_class WHERE guild_id=? AND user_id=?",
            (guild_id, user_id)
        )
        await db.commit()

async def add_auto_message(guild_id, trigger, responses, channel_ids):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "INSERT INTO auto_messages (guild_id, trigger_word, responses, channel_ids) VALUES (?,?,?,?)",
            (guild_id, trigger.lower(), json.dumps(responses), json.dumps(channel_ids))
        )
        await db.commit()

async def get_auto_messages(guild_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT id, trigger_word, responses, channel_ids FROM auto_messages WHERE guild_id=?",
            (guild_id,)
        ) as cur:
            return [
                {"id": r[0], "trigger": r[1], "responses": json.loads(r[2]), "channels": json.loads(r[3])}
                for r in await cur.fetchall()
            ]

async def delete_auto_message(guild_id, msg_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "DELETE FROM auto_messages WHERE guild_id=? AND id=?",
            (guild_id, msg_id)
        )
        await db.commit()

async def add_scheduled_message(guild_id, channel_id, content, send_at, embed_data=None):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "INSERT INTO scheduled_messages (guild_id, channel_id, content, embed_data, send_at) VALUES (?,?,?,?,?)",
            (guild_id, channel_id, content, embed_data, send_at)
        )
        await db.commit()

async def get_pending_scheduled():
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT id, guild_id, channel_id, content, embed_data, send_at FROM scheduled_messages WHERE sent=0"
        ) as cur:
            return await cur.fetchall()

async def mark_scheduled_sent(msg_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("UPDATE scheduled_messages SET sent=1 WHERE id=?", (msg_id,))
        await db.commit()

# ---------- ECONOMY / INVENTORY / SHOPS ----------
async def get_money(guild_id, user_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT money FROM economy WHERE guild_id=? AND user_id=?",
            (guild_id, user_id)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def set_money(guild_id, user_id, amount):
    amount = max(0, int(amount))
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO economy (guild_id, user_id, money) VALUES (?,?,?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET money=excluded.money""",
            (guild_id, user_id, amount)
        )
        await db.commit()
    return amount

async def add_money(guild_id, user_id, amount):
    current = await get_money(guild_id, user_id)
    return await set_money(guild_id, user_id, current + amount)

async def get_inventory(guild_id, user_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT item_name, quantity FROM inventory WHERE guild_id=? AND user_id=? AND quantity>0 ORDER BY item_name",
            (guild_id, user_id)
        ) as cur:
            return await cur.fetchall()

async def get_item_qty(guild_id, user_id, item_name):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT quantity FROM inventory WHERE guild_id=? AND user_id=? AND item_name=?",
            (guild_id, user_id, item_name)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def add_item(guild_id, user_id, item_name, quantity):
    current = await get_item_qty(guild_id, user_id, item_name)
    new_qty = max(0, current + quantity)
    async with aiosqlite.connect(DATABASE) as db:
        if new_qty == 0:
            await db.execute(
                "DELETE FROM inventory WHERE guild_id=? AND user_id=? AND item_name=?",
                (guild_id, user_id, item_name)
            )
        else:
            await db.execute(
                """INSERT INTO inventory (guild_id, user_id, item_name, quantity) VALUES (?,?,?,?)
                ON CONFLICT(guild_id, user_id, item_name) DO UPDATE SET quantity=excluded.quantity""",
                (guild_id, user_id, item_name, new_qty)
            )
        await db.commit()
    return new_qty

async def create_shop(guild_id, name):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT 1 FROM shops WHERE guild_id=? AND name=?",
            (guild_id, name)
        ) as cur:
            if await cur.fetchone():
                return False
        await db.execute("INSERT INTO shops (guild_id, name) VALUES (?,?)", (guild_id, name))
        await db.commit()
        return True

async def delete_shop(guild_id, name):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT id FROM shops WHERE guild_id=? AND name=?",
            (guild_id, name)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return False
            shop_id = row[0]
        await db.execute("DELETE FROM shop_items WHERE shop_id=?", (shop_id,))
        await db.execute("DELETE FROM shops WHERE id=?", (shop_id,))
        await db.commit()
        return True

async def get_shops(guild_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT id, name FROM shops WHERE guild_id=? ORDER BY name",
            (guild_id,)
        ) as cur:
            return await cur.fetchall()

async def get_shop_by_name(guild_id, name):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT id, name FROM shops WHERE guild_id=? AND name=?",
            (guild_id, name)
        ) as cur:
            return await cur.fetchone()

async def add_shop_item(shop_id, item_name, price, description=""):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "INSERT INTO shop_items (shop_id, item_name, price, description) VALUES (?,?,?,?)",
            (shop_id, item_name, price, description)
        )
        await db.commit()

async def remove_shop_item(shop_id, item_name):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "DELETE FROM shop_items WHERE shop_id=? AND item_name=?",
            (shop_id, item_name)
        )
        await db.commit()

async def get_shop_items(shop_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT item_name, price, description FROM shop_items WHERE shop_id=? ORDER BY item_name",
            (shop_id,)
        ) as cur:
            return await cur.fetchall()

async def get_shop_item(shop_id, item_name):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT item_name, price, description FROM shop_items WHERE shop_id=? AND item_name=?",
            (shop_id, item_name)
        ) as cur:
            return await cur.fetchone()

async def has_admin_permission(interaction):
    if interaction.user.guild_permissions.administrator:
        return True
    config = await get_guild_config(interaction.guild_id)
    return any(role.id in config["admin_roles"] for role in interaction.user.roles)

def admin_only():
    async def predicate(interaction):
        if not await has_admin_permission(interaction):
            await interaction.response.send_message("❌ No tienes permiso.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

@bot.event
async def on_ready():
    await init_db()
    if not check_scheduled.is_running():
        check_scheduled.start()
    try:
        synced = await tree.sync()
        print(f"✅ Bot conectado como {bot.user}")
        print(f"✅ Sincronizados {len(synced)} comandos")
    except Exception as e:
        print(f"Error: {e}")

@tasks.loop(seconds=30)
async def check_scheduled():
    now = datetime.now(timezone.utc)
    pending = await get_pending_scheduled()
    for msg_id, guild_id, channel_id, content, embed_data, send_at in pending:
        try:
            send_time = datetime.fromisoformat(send_at)
            if send_time.tzinfo is None:
                send_time = send_time.replace(tzinfo=timezone.utc)
            if now >= send_time:
                guild = bot.get_guild(guild_id)
                if guild:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        await channel.send(content)
                await mark_scheduled_sent(msg_id)
        except Exception as e:
            print(f"Error programado {msg_id}: {e}")

@bot.event
async def on_message(message):
    if not message.guild or not message.content:
        return
    if bot.user and message.author.id == bot.user.id:
        return

    content = message.content.strip()
    content_lower = content.lower()
    clean = content.replace(" ", "")

    math_pattern = r"^[\d\s\+\-\*\/\×\÷\^\(\)\.\%]+$"
    if re.match(math_pattern, clean) and any(op in content for op in "+-*/×÷^%"):
        result = safe_eval(content)
        if result is not None:
            if isinstance(result, float) and result.is_integer():
                result = int(result)
            await message.reply(f"**{content} = {result}**", mention_author=False)
            return

    dice_match = re.fullmatch(r"(\d{1,3})d(\d{1,5})", content_lower.replace(" ", ""))
    if dice_match:
        num_dice = int(dice_match.group(1))
        sides = int(dice_match.group(2))
        if 1 <= num_dice <= 50 and 2 <= sides <= 100000:
            results = [random.randint(1, sides) for _ in range(num_dice)]
            total = sum(results)
            if num_dice == 1:
                text = f"🎲 **{num_dice}d{sides}** = **{results[0]}**"
            else:
                details = ", ".join(str(n) for n in results)
                text = f"🎲 **{num_dice}d{sides}** → {details}\n**Total: {total}**"
            await message.reply(text, mention_author=False)
            return

    if content_lower.startswith("elige:"):
        options = [opt.strip() for opt in content[6:].split(",") if opt.strip()]
        if len(options) >= 2:
            chosen = random.choice(options)
            await message.reply(f"🎯 **He elegido:** {chosen}", mention_author=False)
            return

    for auto in await get_auto_messages(message.guild.id):
        if auto["trigger"] and auto["trigger"] in content_lower:
            if auto["channels"] and message.channel.id not in auto["channels"]:
                continue
            if auto["responses"]:
                await message.channel.send(random.choice(auto["responses"]))
            break

    if message.author.bot:
        return

    user_class = await get_user_class(message.guild.id, message.author.id)
    if not user_class:
        return

    key = f"{message.guild.id}:{message.author.id}"
    now = datetime.now(timezone.utc).timestamp()
    if key in cooldowns and now - cooldowns[key] < XP_COOLDOWN:
        return
    if await is_channel_ignored(message.guild.id, message.channel.id):
        return

    config = await get_guild_config(message.guild.id)
    data = await get_user_data(message.guild.id, message.author.id)
    if config.get("max_level", 0) > 0 and data["level"] >= config["max_level"]:
        return

    xp_gain = random.randint(XP_MIN, XP_MAX)
    new_xp, new_level, leveled_up = await add_xp(message.guild.id, message.author.id, xp_gain)
    cooldowns[key] = now
    if leveled_up:
        await send_levelup_message(message.guild, message.author, new_level, user_class)

async def send_levelup_message(guild, member, level, class_name):
    config = await get_guild_config(guild.id)
    channel_id = config["levelup_channel_id"]
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if not channel:
        return
    rewards = await get_rewards(guild.id, class_name, level)
    embed = discord.Embed(
        title="🎉 ¡Subiste de Nivel!",
        description=f"¡Felicidades {member.mention}!\n\nHas alcanzado el **Nivel {level}** 🚀\n**Clase:** {class_name}",
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if rewards:
        embed.add_field(
            name=f"🎁 Has desbloqueado en el nivel {level}:",
            value="\n".join(f"• {r}" for r in rewards),
            inline=False
        )
    embed.set_footer(text="Level Up • Creado por 《JEFP25》")
    try:
        await channel.send(content=member.mention, embed=embed)
    except Exception:
        pass

@tree.command(name="rank", description="Muestra tu nivel y XP")
async def rank(interaction: discord.Interaction, usuario: discord.Member = None):
    target = usuario or interaction.user
    data = await get_user_data(interaction.guild_id, target.id)
    user_class = await get_user_class(interaction.guild_id, target.id)
    current_level, current_xp = data["level"], data["xp"]
    next_level_xp = xp_for_level(current_level + 1)
    xp_needed = max(0, next_level_xp - current_xp)
    prev_xp = xp_for_level(current_level)
    progress = 0
    if next_level_xp > prev_xp:
        progress = max(0, min(1, (current_xp - prev_xp) / (next_level_xp - prev_xp)))
    bar = "█" * int(12 * progress) + "░" * (12 - int(12 * progress))
    embed = discord.Embed(title=f"📊 Rank de {target.display_name}", color=discord.Color.blurple())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Nivel", value=f"**{current_level}**", inline=True)
    embed.add_field(name="XP Total", value=f"**{current_xp:,}**", inline=True)
    embed.add_field(name="Siguiente nivel", value=f"**{xp_needed:,}** XP", inline=True)
    embed.add_field(name="Clase", value=user_class or "❌ Sin clase", inline=True)
    embed.add_field(name="Dinero", value=f"**{await get_money(interaction.guild_id, target.id):,}**", inline=True)
    embed.add_field(name="Progreso", value=f"`{bar}` {int(progress*100)}%", inline=False)
    embed.set_footer(text="Creado por 《JEFP25》")
    await interaction.response.send_message(embed=embed)

@tree.command(name="leaderboard", description="Top 10 de XP del servidor")
async def leaderboard(interaction: discord.Interaction):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT user_id, xp, level FROM users WHERE guild_id=? ORDER BY xp DESC LIMIT 10",
            (interaction.guild_id,)
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        await interaction.response.send_message("Aún no hay datos.", ephemeral=True)
        return
    description = ""
    medals = ["🥇", "🥈", "🥉"]
    for i, (user_id, xp, level) in enumerate(rows):
        medal = medals[i] if i < 3 else f"**{i+1}.**"
        member = interaction.guild.get_member(user_id)
        name = member.display_name if member else f"Usuario {user_id}"
        user_class = await get_user_class(interaction.guild_id, user_id)
        extra = f" ({user_class})" if user_class else ""
        description += f"{medal} {name}{extra} — Nivel **{level}** ({xp:,} XP)\n"
    embed = discord.Embed(title="🏆 Leaderboard XP", description=description, color=discord.Color.gold())
    embed.set_footer(text="Creado por 《JEFP25》")
    await interaction.response.send_message(embed=embed)

@tree.command(name="elegir-clase", description="Elige tu clase (solo una vez)")
async def elegir_clase(interaction: discord.Interaction, clase: str):
    if await get_user_class(interaction.guild_id, interaction.user.id):
        await interaction.response.send_message("❌ Ya tienes clase.", ephemeral=True)
        return
    if not await class_exists(interaction.guild_id, clase):
        clases = await get_classes(interaction.guild_id)
        await interaction.response.send_message(f"❌ No existe. Disponibles: {', '.join(clases) or 'Ninguna'}", ephemeral=True)
        return
    await set_user_class(interaction.guild_id, interaction.user.id, clase)
    await interaction.response.send_message(f"✅ Has elegido la clase **{clase}**.")

@tree.command(name="mi-clase", description="Muestra tu clase actual")
async def mi_clase(interaction: discord.Interaction):
    user_class = await get_user_class(interaction.guild_id, interaction.user.id)
    if user_class:
        await interaction.response.send_message(f"🛡️ Tu clase es: **{user_class}**")
    else:
        await interaction.response.send_message("❌ No tienes clase. Usa `/elegir-clase`.")

@tree.command(name="ver-lista", description="Ver recompensas de una clase")
async def ver_lista(interaction: discord.Interaction, clase: str = None):
    if not clase:
        clase = await get_user_class(interaction.guild_id, interaction.user.id)
        if not clase:
            await interaction.response.send_message("❌ Indica una clase.", ephemeral=True)
            return
    if not await class_exists(interaction.guild_id, clase):
        await interaction.response.send_message("❌ Esa clase no existe.", ephemeral=True)
        return
    rewards = await get_all_rewards(interaction.guild_id, clase)
    if not rewards:
        await interaction.response.send_message(f"La clase **{clase}** no tiene recompensas.")
        return
    description = "".join(f"**Nivel {lv}:**\n" + "\n".join(f"  • {r}" for r in rw) + "\n\n" for lv, rw in sorted(rewards.items()))
    embed = discord.Embed(title=f"📜 Recompensas de {clase}", description=description, color=discord.Color.purple())
    embed.set_footer(text="Creado por 《JEFP25》")
    await interaction.response.send_message(embed=embed)

@tree.command(name="dinero", description="Muestra tu dinero o el de otro usuario")
async def dinero(interaction: discord.Interaction, usuario: discord.Member = None):
    target = usuario or interaction.user
    amount = await get_money(interaction.guild_id, target.id)
    await interaction.response.send_message(f"💰 {target.mention} tiene **{amount:,}** monedas.")

@tree.command(name="top-dinero", description="Top 10 de dinero del servidor")
async def top_dinero(interaction: discord.Interaction):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT user_id, money FROM economy WHERE guild_id=? ORDER BY money DESC LIMIT 10",
            (interaction.guild_id,)
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        await interaction.response.send_message("Aún no hay economía en el servidor.")
        return
    description = ""
    medals = ["🥇", "🥈", "🥉"]
    for i, (user_id, money) in enumerate(rows):
        medal = medals[i] if i < 3 else f"**{i+1}.**"
        member = interaction.guild.get_member(user_id)
        name = member.display_name if member else f"Usuario {user_id}"
        description += f"{medal} {name} — **{money:,}** monedas\n"
    embed = discord.Embed(title="💰 Top Economía", description=description, color=discord.Color.gold())
    embed.set_footer(text="Creado por 《JEFP25》")
    await interaction.response.send_message(embed=embed)

@tree.command(name="inventario", description="Muestra tu inventario o el de otro usuario")
async def inventario(interaction: discord.Interaction, usuario: discord.Member = None):
    target = usuario or interaction.user
    items = await get_inventory(interaction.guild_id, target.id)
    if not items:
        await interaction.response.send_message(f"🎒 El inventario de {target.mention} está vacío.")
        return
    text = "\n".join(f"• **{name}** ×{qty}" for name, qty in items)
    embed = discord.Embed(title=f"🎒 Inventario de {target.display_name}", description=text, color=discord.Color.green())
    embed.set_footer(text="Creado por 《JEFP25》")
    await interaction.response.send_message(embed=embed)

@tree.command(name="pagar", description="Dale dinero a otro jugador")
async def pagar(interaction: discord.Interaction, usuario: discord.Member, cantidad: app_commands.Range[int, 1, 100000000]):
    if usuario.id == interaction.user.id:
        await interaction.response.send_message("❌ No puedes pagarte a ti mismo.", ephemeral=True)
        return
    if usuario.bot:
        await interaction.response.send_message("❌ No puedes pagarle a un bot.", ephemeral=True)
        return
    current = await get_money(interaction.guild_id, interaction.user.id)
    if current < cantidad:
        await interaction.response.send_message(f"❌ No tienes suficiente dinero. Tienes **{current:,}**.", ephemeral=True)
        return
    await add_money(interaction.guild_id, interaction.user.id, -cantidad)
    await add_money(interaction.guild_id, usuario.id, cantidad)
    await interaction.response.send_message(f"✅ {interaction.user.mention} le dio **{cantidad:,}** monedas a {usuario.mention}.")

@tree.command(name="dar-item", description="Dale un ítem de tu inventario a otro jugador")
async def dar_item(interaction: discord.Interaction, usuario: discord.Member, item: str, cantidad: app_commands.Range[int, 1, 1000] = 1):
    if usuario.id == interaction.user.id:
        await interaction.response.send_message("❌ No puedes dártelo a ti mismo.", ephemeral=True)
        return
    if usuario.bot:
        await interaction.response.send_message("❌ No puedes dárselo a un bot.", ephemeral=True)
        return
    have = await get_item_qty(interaction.guild_id, interaction.user.id, item)
    if have < cantidad:
        await interaction.response.send_message(f"❌ No tienes suficientes **{item}**. Tienes **{have}**.", ephemeral=True)
        return
    await add_item(interaction.guild_id, interaction.user.id, item, -cantidad)
    await add_item(interaction.guild_id, usuario.id, item, cantidad)
    await interaction.response.send_message(f"✅ {interaction.user.mention} le dio **{item}** ×{cantidad} a {usuario.mention}.")

@tree.command(name="tiendas", description="Lista las tiendas del servidor")
async def tiendas(interaction: discord.Interaction):
    shops = await get_shops(interaction.guild_id)
    if not shops:
        await interaction.response.send_message("No hay tiendas creadas.")
        return
    text = "\n".join(f"• **{name}**" for _sid, name in shops)
    embed = discord.Embed(title="🛒 Tiendas", description=text, color=discord.Color.orange())
    embed.set_footer(text="Usa /ver-tienda para ver los productos")
    await interaction.response.send_message(embed=embed)

@tree.command(name="ver-tienda", description="Muestra los productos de una tienda")
async def ver_tienda(interaction: discord.Interaction, tienda: str):
    shop = await get_shop_by_name(interaction.guild_id, tienda)
    if not shop:
        await interaction.response.send_message("❌ Esa tienda no existe.", ephemeral=True)
        return
    items = await get_shop_items(shop[0])
    if not items:
        await interaction.response.send_message(f"La tienda **{tienda}** no tiene productos.")
        return
    text = ""
    for name, price, desc in items:
        extra = f" — {desc}" if desc else ""
        text += f"• **{name}** — {price:,} monedas{extra}\n"
    embed = discord.Embed(title=f"🛒 {tienda}", description=text, color=discord.Color.orange())
    embed.set_footer(text="Usa /comprar para comprar")
    await interaction.response.send_message(embed=embed)

@tree.command(name="comprar", description="Compra un ítem de una tienda")
async def comprar(interaction: discord.Interaction, tienda: str, item: str, cantidad: app_commands.Range[int, 1, 100] = 1):
    shop = await get_shop_by_name(interaction.guild_id, tienda)
    if not shop:
        await interaction.response.send_message("❌ Esa tienda no existe.", ephemeral=True)
        return
    product = await get_shop_item(shop[0], item)
    if not product:
        await interaction.response.send_message("❌ Ese ítem no está en esa tienda.", ephemeral=True)
        return
    price = product[1] * cantidad
    money = await get_money(interaction.guild_id, interaction.user.id)
    if money < price:
        await interaction.response.send_message(f"❌ Te faltan monedas. Cuesta **{price:,}** y tienes **{money:,}**.", ephemeral=True)
        return
    await add_money(interaction.guild_id, interaction.user.id, -price)
    await add_item(interaction.guild_id, interaction.user.id, product[0], cantidad)
    await interaction.response.send_message(f"✅ Compraste **{product[0]}** ×{cantidad} por **{price:,}** monedas.")

@tree.command(name="usar", description="Usa un objeto de tu inventario")
@app_commands.describe(item="Nombre del ítem", cantidad="Cantidad a usar (por defecto 1)")
async def usar_item(interaction: discord.Interaction, item: str, cantidad: app_commands.Range[int, 1, 100] = 1):
    have = await get_item_qty(interaction.guild_id, interaction.user.id, item)
    if have < cantidad:
        await interaction.response.send_message(
            f"❌ No tienes suficientes **{item}**. Tienes **{have}**.",
            ephemeral=True
        )
        return

    await add_item(interaction.guild_id, interaction.user.id, item, -cantidad)
    await interaction.response.send_message(
        f"✅ {interaction.user.mention} usó **{item}** ×{cantidad}."
    )

@tree.command(name="help", description="Lista de todos los comandos")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="📖 Level Up - Comandos", description="Bot de niveles, economía, tiendas e inventario.", color=discord.Color.blue())
    embed.add_field(name="👤 Usuario", value="`/rank` `/leaderboard` `/elegir-clase` `/mi-clase` `/ver-lista` `/help`", inline=False)
    embed.add_field(name="💰 Economía", value="`/dinero` `/top-dinero` `/inventario` `/pagar` `/dar-item` `/usar` `/tiendas` `/ver-tienda` `/comprar`", inline=False)
    embed.add_field(name="🎲 Chat", value="`1d20` `5d60` `Elige: sí, no`\n`1+2` `10%*30`", inline=False)
    embed.add_field(name="🛡️ Admin XP", value="`/dar-xp` `/quitar-xp` `/ver-xp` `/dar-xp-rol` `/quitar-xp-rol` `/resetear-xp` `/resetear-xp-rol` `/añadir-clase` `/borrar-clase` `/añadir-recompensa` `/borrar-recompensa` `/resetear-clase` `/set-nivel-maximo`", inline=False)
    embed.add_field(name="🛡️ Admin Economía", value="`/dar-dinero` `/quitar-dinero` `/crear-tienda` `/borrar-tienda` `/item-tienda` `/quitar-item-tienda`", inline=False)
    embed.add_field(name="🛡️ Mensajes", value="`/embed` `/programar-mensaje` `/auto-mensaje` `/auto-lista` `/auto-borrar`", inline=False)
    embed.add_field(name="⚙️ Config", value="`/config-canal-levelup` `/config-desactivar-levelup` `/config-ignorar-canal` `/config-permitir-canal` `/config-canales-ignorados` `/config-roles-admin` `/config-ver`", inline=False)
    embed.set_footer(text="Creado por 《JEFP25》")
    await interaction.response.send_message(embed=embed)

@tree.command(name="dar-xp", description="Da XP a un usuario")
@admin_only()
async def dar_xp(interaction: discord.Interaction, usuario: discord.Member, cantidad: app_commands.Range[int, 1, 1000000]):
    new_xp, new_level, leveled_up = await add_xp(interaction.guild_id, usuario.id, cantidad)
    user_class = await get_user_class(interaction.guild_id, usuario.id)
    msg = f"✅ **{cantidad:,} XP** a {usuario.mention}. Ahora: **{new_xp:,} XP** (Nivel {new_level})"
    if leveled_up and user_class:
        await send_levelup_message(interaction.guild, usuario, new_level, user_class)
    await interaction.response.send_message(msg)

@tree.command(name="quitar-xp", description="Quita XP a un usuario")
@admin_only()
async def quitar_xp(interaction: discord.Interaction, usuario: discord.Member, cantidad: app_commands.Range[int, 1, 1000000]):
    new_xp, new_level, _ = await add_xp(interaction.guild_id, usuario.id, -cantidad)
    await interaction.response.send_message(f"✅ Quitados **{cantidad:,} XP** a {usuario.mention}. Ahora: **{new_xp:,} XP** (Nivel {new_level})")

@tree.command(name="ver-xp", description="Ver XP de un usuario")
@admin_only()
async def ver_xp(interaction: discord.Interaction, usuario: discord.Member):
    data = await get_user_data(interaction.guild_id, usuario.id)
    user_class = await get_user_class(interaction.guild_id, usuario.id)
    await interaction.response.send_message(f"📊 {usuario.mention}: **{data['xp']:,} XP** | Nivel **{data['level']}** | Clase: **{user_class or 'Sin clase'}**")

@tree.command(name="dar-xp-rol", description="Da XP a un rol")
@admin_only()
async def dar_xp_rol(interaction: discord.Interaction, rol: discord.Role, cantidad: app_commands.Range[int, 1, 100000]):
    await interaction.response.defer()
    members = [m for m in rol.members if not m.bot]
    for m in members:
        await add_xp(interaction.guild_id, m.id, cantidad)
    await interaction.followup.send(f"✅ **{cantidad:,} XP** a **{len(members)}** miembros de {rol.mention}.")

@tree.command(name="quitar-xp-rol", description="Quita XP a un rol")
@admin_only()
async def quitar_xp_rol(interaction: discord.Interaction, rol: discord.Role, cantidad: app_commands.Range[int, 1, 100000]):
    await interaction.response.defer()
    members = [m for m in rol.members if not m.bot]
    for m in members:
        await add_xp(interaction.guild_id, m.id, -cantidad)
    await interaction.followup.send(f"✅ Quitados **{cantidad:,} XP** a **{len(members)}** miembros de {rol.mention}.")

@tree.command(name="resetear-xp", description="Resetea XP de un usuario")
@admin_only()
async def resetear_xp(interaction: discord.Interaction, usuario: discord.Member):
    await set_user_xp(interaction.guild_id, usuario.id, 0)
    await interaction.response.send_message(f"✅ XP de {usuario.mention} reseteado.")

@tree.command(name="resetear-xp-rol", description="Resetea XP de un rol")
@admin_only()
async def resetear_xp_rol(interaction: discord.Interaction, rol: discord.Role):
    await interaction.response.defer()
    members = [m for m in rol.members if not m.bot]
    for m in members:
        await set_user_xp(interaction.guild_id, m.id, 0)
    await interaction.followup.send(f"✅ XP reseteado a **{len(members)}** miembros de {rol.mention}.")

@tree.command(name="añadir-clase", description="Crea una clase")
@admin_only()
async def añadir_clase(interaction: discord.Interaction, nombre: str):
    if await add_class(interaction.guild_id, nombre.strip()):
        await interaction.response.send_message(f"✅ Clase **{nombre}** creada.")
    else:
        await interaction.response.send_message("❌ Ya existe.", ephemeral=True)

@tree.command(name="borrar-clase", description="Borra una clase")
@admin_only()
async def borrar_clase(interaction: discord.Interaction, nombre: str):
    if not await class_exists(interaction.guild_id, nombre):
        await interaction.response.send_message("❌ No existe.", ephemeral=True)
        return
    await remove_class(interaction.guild_id, nombre)
    await interaction.response.send_message(f"✅ Clase **{nombre}** eliminada.")

@tree.command(name="añadir-recompensa", description="Añade recompensas a un nivel")
@admin_only()
async def añadir_recompensa(interaction: discord.Interaction, clase: str, nivel: app_commands.Range[int, 1, 500], recompensas: str):
    if not await class_exists(interaction.guild_id, clase):
        await interaction.response.send_message("❌ Clase no existe.", ephemeral=True)
        return
    items = [r.strip() for r in recompensas.split("|") if r.strip()]
    await set_rewards(interaction.guild_id, clase, nivel, items)
    await interaction.response.send_message(f"✅ Recompensas en **{clase}** nivel **{nivel}**:\n" + "\n".join(f"• {i}" for i in items))

@tree.command(name="borrar-recompensa", description="Borra recompensas de un nivel")
@admin_only()
async def borrar_recompensa(interaction: discord.Interaction, clase: str, nivel: app_commands.Range[int, 1, 500]):
    await delete_rewards(interaction.guild_id, clase, nivel)
    await interaction.response.send_message(f"✅ Recompensas del nivel **{nivel}** eliminadas.")

@tree.command(name="resetear-clase", description="Quita la clase a un usuario")
@admin_only()
async def resetear_clase(interaction: discord.Interaction, usuario: discord.Member):
    current = await get_user_class(interaction.guild_id, usuario.id)
    if not current:
        await interaction.response.send_message("No tiene clase.", ephemeral=True)
        return
    await remove_user_class(interaction.guild_id, usuario.id)
    await interaction.response.send_message(f"✅ Se quitó la clase **{current}** a {usuario.mention}.")

@tree.command(name="set-nivel-maximo", description="Nivel máximo (0 = sin límite)")
@admin_only()
async def set_nivel_maximo(interaction: discord.Interaction, nivel: app_commands.Range[int, 0, 500]):
    await set_max_level(interaction.guild_id, nivel)
    await interaction.response.send_message("✅ Límite eliminado." if nivel == 0 else f"✅ Nivel máximo: **{nivel}**")

@tree.command(name="dar-dinero", description="Da dinero a un usuario (admin)")
@admin_only()
async def dar_dinero(interaction: discord.Interaction, usuario: discord.Member, cantidad: app_commands.Range[int, 1, 100000000]):
    new_amount = await add_money(interaction.guild_id, usuario.id, cantidad)
    await interaction.response.send_message(f"✅ Se dieron **{cantidad:,}** monedas a {usuario.mention}. Ahora tiene **{new_amount:,}**.")

@tree.command(name="quitar-dinero", description="Quita dinero a un usuario (admin)")
@admin_only()
async def quitar_dinero(interaction: discord.Interaction, usuario: discord.Member, cantidad: app_commands.Range[int, 1, 100000000]):
    new_amount = await add_money(interaction.guild_id, usuario.id, -cantidad)
    await interaction.response.send_message(f"✅ Se quitaron **{cantidad:,}** monedas a {usuario.mention}. Ahora tiene **{new_amount:,}**.")

@tree.command(name="crear-tienda", description="Crea una tienda nueva")
@admin_only()
async def crear_tienda(interaction: discord.Interaction, nombre: str):
    if await create_shop(interaction.guild_id, nombre.strip()):
        await interaction.response.send_message(f"✅ Tienda **{nombre}** creada.")
    else:
        await interaction.response.send_message("❌ Ya existe una tienda con ese nombre.", ephemeral=True)

@tree.command(name="borrar-tienda", description="Borra una tienda y sus productos")
@admin_only()
async def borrar_tienda(interaction: discord.Interaction, nombre: str):
    if await delete_shop(interaction.guild_id, nombre):
        await interaction.response.send_message(f"✅ Tienda **{nombre}** eliminada.")
    else:
        await interaction.response.send_message("❌ Esa tienda no existe.", ephemeral=True)

@tree.command(name="item-tienda", description="Añade un producto a una tienda")
@admin_only()
async def item_tienda(interaction: discord.Interaction, tienda: str, item: str, precio: app_commands.Range[int, 1, 100000000], descripcion: str = ""):
    shop = await get_shop_by_name(interaction.guild_id, tienda)
    if not shop:
        await interaction.response.send_message("❌ Esa tienda no existe.", ephemeral=True)
        return
    await add_shop_item(shop[0], item, precio, descripcion)
    await interaction.response.send_message(f"✅ **{item}** añadido a **{tienda}** por **{precio:,}** monedas.")

@tree.command(name="quitar-item-tienda", description="Quita un producto de una tienda")
@admin_only()
async def quitar_item_tienda(interaction: discord.Interaction, tienda: str, item: str):
    shop = await get_shop_by_name(interaction.guild_id, tienda)
    if not shop:
        await interaction.response.send_message("❌ Esa tienda no existe.", ephemeral=True)
        return
    await remove_shop_item(shop[0], item)
    await interaction.response.send_message(f"✅ **{item}** eliminado de **{tienda}**.")

@tree.command(name="embed", description="Crea y envía un embed")
@admin_only()
async def crear_embed(interaction: discord.Interaction, canal: discord.TextChannel, titulo: str = None, descripcion: str = None, color: str = "5865F2", footer: str = None, imagen: str = None, thumbnail: str = None):
    try:
        color_value = int(color.replace("#", ""), 16)
    except Exception:
        color_value = 0x5865F2
    embed = discord.Embed(color=color_value)
    if titulo: embed.title = titulo
    if descripcion: embed.description = descripcion
    if footer: embed.set_footer(text=footer)
    if imagen: embed.set_image(url=imagen)
    if thumbnail: embed.set_thumbnail(url=thumbnail)
    try:
        await canal.send(embed=embed)
        await interaction.response.send_message(f"✅ Embed enviado en {canal.mention}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

@tree.command(name="auto-mensaje", description="Mensaje automático por palabra clave")
@admin_only()
async def auto_mensaje(interaction: discord.Interaction, palabra: str, respuestas: str, canales: str = None):
    resp_list = [r.strip() for r in respuestas.split("|") if r.strip()]
    if not resp_list:
        await interaction.response.send_message("❌ Pon al menos una respuesta.", ephemeral=True)
        return
    channel_ids = []
    if canales:
        for c in canales.split(","):
            if c.strip().isdigit():
                channel_ids.append(int(c.strip()))
    await add_auto_message(interaction.guild_id, palabra, resp_list, channel_ids)
    await interaction.response.send_message(f"✅ Auto-mensaje creado.\nPalabra: `{palabra}`\nRespuestas: {len(resp_list)}")

@tree.command(name="auto-lista", description="Lista mensajes automáticos")
@admin_only()
async def auto_lista(interaction: discord.Interaction):
    autos = await get_auto_messages(interaction.guild_id)
    if not autos:
        await interaction.response.send_message("No hay mensajes automáticos.")
        return
    text = "\n".join(f"**ID {a['id']}** — `{a['trigger']}` ({len(a['responses'])} respuestas)" for a in autos)
    embed = discord.Embed(title="📋 Mensajes Automáticos", description=text, color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)

@tree.command(name="auto-borrar", description="Borra un auto-mensaje por ID")
@admin_only()
async def auto_borrar(interaction: discord.Interaction, id: int):
    await delete_auto_message(interaction.guild_id, id)
    await interaction.response.send_message(f"✅ Auto-mensaje **{id}** eliminado.")

@tree.command(name="programar-mensaje", description="Programa un mensaje")
@admin_only()
async def programar_mensaje(interaction: discord.Interaction, canal: discord.TextChannel, mensaje: str, fecha: str):
    try:
        send_at = datetime.strptime(fecha, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        await interaction.response.send_message("❌ Formato: `YYYY-MM-DD HH:MM`", ephemeral=True)
        return
    if send_at < datetime.now(timezone.utc):
        await interaction.response.send_message("❌ La fecha debe ser futura.", ephemeral=True)
        return
    await add_scheduled_message(interaction.guild_id, canal.id, mensaje, send_at.isoformat())
    await interaction.response.send_message(f"✅ Mensaje programado para **{fecha} UTC** en {canal.mention}")

@tree.command(name="config-canal-levelup", description="Canal de level up")
@admin_only()
async def config_canal_levelup(interaction: discord.Interaction, canal: discord.TextChannel):
    await set_levelup_channel(interaction.guild_id, canal.id)
    await interaction.response.send_message(f"✅ Canal de level up: {canal.mention}")

@tree.command(name="config-desactivar-levelup", description="Desactiva mensajes de level up")
@admin_only()
async def config_desactivar_levelup(interaction: discord.Interaction):
    await set_levelup_channel(interaction.guild_id, None)
    await interaction.response.send_message("✅ Mensajes de level up desactivados.")

@tree.command(name="config-ignorar-canal", description="No ganar XP en un canal")
@admin_only()
async def config_ignorar_canal(interaction: discord.Interaction, canal: discord.TextChannel):
    await add_ignored_channel(interaction.guild_id, canal.id)
    await interaction.response.send_message(f"✅ Ya no se gana XP en {canal.mention}")

@tree.command(name="config-permitir-canal", description="Permitir XP en un canal")
@admin_only()
async def config_permitir_canal(interaction: discord.Interaction, canal: discord.TextChannel):
    await remove_ignored_channel(interaction.guild_id, canal.id)
    await interaction.response.send_message(f"✅ Ahora se gana XP en {canal.mention}")

@tree.command(name="config-canales-ignorados", description="Lista canales bloqueados")
@admin_only()
async def config_canales_ignorados(interaction: discord.Interaction):
    channels = await get_ignored_channels(interaction.guild_id)
    if not channels:
        await interaction.response.send_message("No hay canales ignorados.")
        return
    mentions = [interaction.guild.get_channel(c).mention if interaction.guild.get_channel(c) else str(c) for c in channels]
    await interaction.response.send_message("**Canales ignorados:**\n" + "\n".join(mentions))

@tree.command(name="config-roles-admin", description="Roles de administración")
@admin_only()
async def config_roles_admin(interaction: discord.Interaction, rol1: discord.Role, rol2: discord.Role = None, rol3: discord.Role = None):
    roles = [rol1]
    for r in [rol2, rol3]:
        if r and r not in roles:
            roles.append(r)
    await set_admin_roles(interaction.guild_id, [r.id for r in roles])
    await interaction.response.send_message("✅ Roles actualizados: " + " ".join(r.mention for r in roles))

@tree.command(name="config-ver", description="Ver configuración")
@admin_only()
async def config_ver(interaction: discord.Interaction):
    config = await get_guild_config(interaction.guild_id)
    classes = await get_classes(interaction.guild_id)
    shops = await get_shops(interaction.guild_id)
    embed = discord.Embed(title="⚙️ Configuración", color=discord.Color.blue())
    embed.add_field(name="Nivel máximo", value=str(config["max_level"]) if config["max_level"] else "Sin límite", inline=True)
    embed.add_field(name="Clases", value=", ".join(classes) if classes else "Ninguna", inline=False)
    embed.add_field(name="Tiendas", value=", ".join(n for _i, n in shops) if shops else "Ninguna", inline=False)
    embed.set_footer(text="Creado por 《JEFP25》")
    await interaction.response.send_message(embed=embed)

if __name__ == "__main__":
    if not TOKEN:
        print("❌ Falta DISCORD_TOKEN")
    else:
        bot.run(TOKEN)

