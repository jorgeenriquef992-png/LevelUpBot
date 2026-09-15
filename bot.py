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
    expr = re.sub(r'(\d+(?:\.\d+)?)%', r'(\1/100)', expr)
    try:
        node = ast.parse(expr, mode='eval').body
        return _eval_node(node)
    except Exception:
        return None

def _eval_node(node):
    if isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.Num):
        return node.n
    elif isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        op = SAFE_OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError("Operador no permitido")
        return op(left, right)
    elif isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        op = SAFE_OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError("Operador no permitido")
        return op(operand)
    else:
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
        await db.commit()

async def get_user_data(guild_id, user_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT xp, level FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)) as cur:
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
        await db.execute("""INSERT INTO users (guild_id, user_id, xp, level) VALUES (?,?,?,?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=excluded.xp, level=excluded.level""",
            (guild_id, user_id, xp, level))
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
        async with db.execute("SELECT levelup_channel_id, admin_roles, max_level FROM guild_config WHERE guild_id=?", (guild_id,)) as cur:
            row = await cur.fetchone()
            if row:
                return {"levelup_channel_id": row[0], "admin_roles": json.loads(row[1]) if row[1] else [], "max_level": row[2] or 0}
            return {"levelup_channel_id": None, "admin_roles": [], "max_level": 0}

async def set_levelup_channel(guild_id, channel_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""INSERT INTO guild_config (guild_id, levelup_channel_id) VALUES (?,?)
            ON CONFLICT(guild_id) DO UPDATE SET levelup_channel_id=excluded.levelup_channel_id""", (guild_id, channel_id))
        await db.commit()

async def set_admin_roles(guild_id, role_ids):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""INSERT INTO guild_config (guild_id, admin_roles) VALUES (?,?)
            ON CONFLICT(guild_id) DO UPDATE SET admin_roles=excluded.admin_roles""", (guild_id, json.dumps(role_ids)))
        await db.commit()

async def set_max_level(guild_id, max_level):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""INSERT INTO guild_config (guild_id, max_level) VALUES (?,?)
            ON CONFLICT(guild_id) DO UPDATE SET max_level=excluded.max_level""", (guild_id, max_level))
        await db.commit()

async def is_channel_ignored(guild_id, channel_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT 1 FROM ignored_channels WHERE guild_id=? AND channel_id=?", (guild_id, channel_id)) as cur:
            return await cur.fetchone() is not None

async def add_ignored_channel(guild_id, channel_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT OR IGNORE INTO ignored_channels (guild_id, channel_id) VALUES (?,?)", (guild_id, channel_id))
        await db.commit()

async def remove_ignored_channel(guild_id, channel_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("DELETE FROM ignored_channels WHERE guild_id=? AND channel_id=?", (guild_id, channel_id))
        await db.commit()

async def get_ignored_channels(guild_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT channel_id FROM ignored_channels WHERE guild_id=?", (guild_id,)) as cur:
            return [r[0] for r in await cur.fetchall()]

async def add_class(guild_id, class_name):
    async with aiosqlite.connect(DATABASE) as db:
        try:
            await db.execute("INSERT INTO classes (guild_id, class_name) VALUES (?,?)", (guild_id, class_name))
            await db.commit()
            return True
        except:
            return False

async def remove_class(guild_id, class_name):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("DELETE FROM classes WHERE guild_id=? AND class_name=?", (guild_id, class_name))
        await db.execute("DELETE FROM class_rewards WHERE guild_id=? AND class_name=?", (guild_id, class_name))
        await db.execute("DELETE FROM user_class WHERE guild_id=? AND class_name=?", (guild_id, class_name))
        await db.commit()

async def get_classes(guild_id):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT class_name FROM classes WHERE guild_id=? ORDER BY class_name", (guild_id,)) as cur:
            return [r[0] for r in await cur.fetchall()]

async def class_exists(guild_id, class_name):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT 1 FROM classes WHERE guild_id=? AND class_name=?", (guild_id, class_name)) as cur:
            return await cur.fetchone() is not None

async def set_rewards(guild_id, class_name, level, rewards):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""INSERT INTO class_rewards (guild_id, class_name, level, rewards) VALUES (?,?,?,?)
            ON CONFLICT(guild_id, class_name, level) DO UPDATE SET rewards=excluded.rewards""",
            (guild_id, class_name, level, json.dumps(rewards)))
        await db.commit()
        
