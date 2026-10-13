import os
import re
import ast
import json
import math
import random
import operator
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE = "rpg_bot.db"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ==================== CONSTANTES ====================
MAX_PJ = 3
PROFESSION_LEVEL = 10
RANK_LEVELS = {"C": 10, "B": 20, "A": 30, "S": 50}
GRADE_ORDER = {"Comun": 0, "Refinado": 1, "Superior": 2, "Magistral": 3}
RANK_ORDER = {"C": 0, "B": 1, "A": 2, "S": 3}
GRADE_BY_RANK = {"C": "Comun", "B": "Refinado", "A": "Superior", "S": "Magistral"}
EFFECTS = ["Sangrado", "Quemadura", "Aturdimiento", "Sueño", "Veneno", "Congelamiento", "Parálisis"]
ELEMENTS = ["Fuego", "Agua", "Tierra", "Aire", "Ninguno"]
MESA_BY_PROF = {
    "Herrero": "Mesa de Herrería",
    "Artífice": "Mesa de Artífice",
    "Elixirista": "Mesa de Elixir",
    "Toxicólogo": "Mesa de Toxicología",
    "Cocinero": "Mesa de Cocina",
}
DEFAULT_STATS = {
    "VIDA": 20, "VIDA_MAX": 20, "FUE": 5, "VEL": 5,
    "RES": 5, "DEF": 3, "MANA": 10, "MANA_MAX": 10
}

SAFE_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.Mod: operator.mod,
}

# ==================== UTILIDADES ====================
def safe_eval(expr: str):
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**")
    expr = re.sub(r"(\d+(?:\.\d+)?)%", r"(\1/100)", expr)
    try:
        node = ast.parse(expr, mode="eval")
        def _eval(n):
            if isinstance(n, ast.Expression):
                return _eval(n.body)
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
                return n.value
            if isinstance(n, ast.UnaryOp) and type(n.op) in SAFE_OPS:
                return SAFE_OPS[type(n.op)](_eval(n.operand))
            if isinstance(n, ast.BinOp) and type(n.op) in SAFE_OPS:
                return SAFE_OPS[type(n.op)](_eval(n.left), _eval(n.right))
            raise ValueError("unsafe")
        return _eval(node)
    except Exception:
        return None

def parse_codes(text: str) -> dict:
    """Extrae códigos tipo $FUE=5 o $DAÑO=3 del texto."""
    if not text:
        return {}
    found = {}
    for m in re.finditer(r"\$([A-Z_]+)\s*=\s*([^\s,$]+)", text.upper()):
        key, val = m.group(1), m.group(2)
        try:
            if "." in val:
                found[key] = float(val)
            else:
                found[key] = int(val)
        except ValueError:
            found[key] = val
    # También captura $EFECTO=Sangrado sin forzar número
    for m in re.finditer(r"\$([A-Z_]+)\s*=\s*([A-Za-zÁÉÍÓÚáéíóúñÑ_]+)", text):
        key = m.group(1).upper()
        if key not in found:
            found[key] = m.group(2)
    return found

def rank_from_level(level: int) -> str:
    if level >= 50:
        return "S"
    if level >= 30:
        return "A"
    if level >= 20:
        return "B"
    if level >= 10:
        return "C"
    return "-"

def xp_for_level(level: int) -> int:
    return 100 * level * (level + 1) // 2

def calculate_level(xp: int) -> int:
    level = 0
    while xp >= xp_for_level(level + 1):
        level += 1
        if level > 500:
            break
    return level

# ==================== DB ====================
async def init_db():
    async with aiosqlite.connect(DATABASE) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY,
                admin_roles TEXT DEFAULT '[]',
                xp_channel_id INTEGER,
                max_level INTEGER DEFAULT 50
            );
            CREATE TABLE IF NOT EXISTS classes (
                guild_id INTEGER,
                name TEXT,
                description TEXT DEFAULT '',
                stats_json TEXT DEFAULT '{}',
                PRIMARY KEY (guild_id, name)
            );
            CREATE TABLE IF NOT EXISTS professions (
                guild_id INTEGER,
                name TEXT,
                description TEXT DEFAULT '',
                PRIMARY KEY (guild_id, name)
            );
            CREATE TABLE IF NOT EXISTS characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                image_url TEXT DEFAULT '',
                color INTEGER DEFAULT 5793266,
                class_name TEXT DEFAULT '',
                profession TEXT DEFAULT '',
                level INTEGER DEFAULT 1,
                xp INTEGER DEFAULT 0,
                money INTEGER DEFAULT 0,
                stats_json TEXT DEFAULT '{}',
                alive INTEGER DEFAULT 1,
                tupper_name TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS inventory (
                character_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                quantity INTEGER DEFAULT 1,
                item_type TEXT DEFAULT 'Objeto',
                data_json TEXT DEFAULT '{}',
                image_url TEXT DEFAULT '',
                description TEXT DEFAULT '',
                PRIMARY KEY (character_id, item_name)
            );
            CREATE TABLE IF NOT EXISTS shops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shop_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shop_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                price INTEGER NOT NULL,
                item_type TEXT DEFAULT 'Objeto',
                description TEXT DEFAULT '',
                image_url TEXT DEFAULT '',
                data_json TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS materials (
                guild_id INTEGER,
                name TEXT,
                description TEXT DEFAULT '',
                image_url TEXT DEFAULT '',
                PRIMARY KEY (guild_id, name)
            );
            CREATE TABLE IF NOT EXISTS recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                profession TEXT NOT NULL,
                grade TEXT DEFAULT 'Comun',
                result_name TEXT NOT NULL,
                result_type TEXT DEFAULT 'Arma',
                result_data TEXT DEFAULT '{}',
                materials_json TEXT DEFAULT '{}',
                needs_magic INTEGER DEFAULT 0,
                magic_material TEXT DEFAULT '',
                image_url TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS level_rewards (
                guild_id INTEGER,
                level INTEGER,
                rewards TEXT DEFAULT '[]',
                stats_json TEXT DEFAULT '{}',
                PRIMARY KEY (guild_id, level)
            );
            CREATE TABLE IF NOT EXISTS combats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                combat_type TEXT DEFAULT 'amistoso',
                status TEXT DEFAULT 'active',
                turn_index INTEGER DEFAULT 0,
                data_json TEXT DEFAULT '{}',
                log_json TEXT DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS combat_participants (
                combat_id INTEGER NOT NULL,
                character_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                team INTEGER DEFAULT 0,
                role TEXT DEFAULT 'Atacante',
                vida INTEGER DEFAULT 20,
                mana INTEGER DEFAULT 10,
                stats_json TEXT DEFAULT '{}',
                effects_json TEXT DEFAULT '[]',
                defending_target INTEGER,
                alive INTEGER DEFAULT 1,
                PRIMARY KEY (combat_id, character_id)
            );
            """
        )
        for col_sql in [
            "ALTER TABLE guild_config ADD COLUMN xp_channels TEXT DEFAULT '[]'",
            "ALTER TABLE guild_config ADD COLUMN xp_categories TEXT DEFAULT '[]'",
            "ALTER TABLE characters ADD COLUMN tupper_name TEXT DEFAULT ''",
        ]:
            try:
                await db.execute(col_sql)
            except Exception:
                pass
        await db.commit()

# ==================== GUILD / ADMIN ====================
async def get_guild_config(guild_id: int) -> dict:
    async with aiosqlite.connect(DATABASE) as db:
        try:
            async with db.execute(
                "SELECT admin_roles, xp_channel_id, max_level, xp_channels, xp_categories FROM guild_config WHERE guild_id=?",
                (guild_id,),
            ) as cur:
                row = await cur.fetchone()
        except Exception:
            async with db.execute(
                "SELECT admin_roles, xp_channel_id, max_level FROM guild_config WHERE guild_id=?",
                (guild_id,),
            ) as cur:
                r = await cur.fetchone()
                row = (r[0], r[1], r[2], "[]", "[]") if r else None
    if not row:
        return {"admin_roles": [], "xp_channel_id": None, "max_level": 50, "xp_channels": [], "xp_categories": []}
    try:
        channels = json.loads(row[3] or "[]") if len(row) > 3 else []
    except Exception:
        channels = []
    if row[1] and row[1] not in channels:
        channels.append(row[1])
    try:
        categories = json.loads(row[4] or "[]") if len(row) > 4 else []
    except Exception:
        categories = []
    return {
        "admin_roles": json.loads(row[0] or "[]"),
        "xp_channel_id": row[1],
        "max_level": row[2] or 50,
        "xp_channels": channels,
        "xp_categories": categories,
    }

async def set_guild_field(guild_id: int, field: str, value):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "INSERT INTO guild_config (guild_id) VALUES (?) ON CONFLICT(guild_id) DO NOTHING",
            (guild_id,),
        )
        await db.execute(f"UPDATE guild_config SET {field}=? WHERE guild_id=?", (value, guild_id))
        await db.commit()

async def has_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    cfg = await get_guild_config(interaction.guild_id)
    return any(r.id in cfg["admin_roles"] for r in getattr(interaction.user, "roles", []))

def admin_only():
    async def predicate(interaction: discord.Interaction):
        if not await has_admin(interaction):
            await interaction.response.send_message("❌ No tienes permiso de admin.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

# ==================== PERSONAJES ====================
async def count_characters(guild_id: int, user_id: int) -> int:
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM characters WHERE guild_id=? AND user_id=? AND alive=1",
            (guild_id, user_id),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def get_user_characters(guild_id: int, user_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT id, name, level, class_name, profession FROM characters WHERE guild_id=? AND user_id=? AND alive=1 ORDER BY id",
            (guild_id, user_id),
        ) as cur:
            return await cur.fetchall()

async def get_character(char_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM characters WHERE id=?", (char_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def get_class(guild_id: int, name: str):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT name, description, stats_json FROM classes WHERE guild_id=? AND name=?",
            (guild_id, name),
        ) as cur:
            return await cur.fetchone()

async def list_classes(guild_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT name FROM classes WHERE guild_id=? ORDER BY name", (guild_id,)
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

async def list_professions(guild_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT name FROM professions WHERE guild_id=? ORDER BY name", (guild_id,)
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

# ==================== INVENTARIO / ECONOMÍA ====================
async def get_inventory(char_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT item_name, quantity, item_type, data_json, image_url, description FROM inventory WHERE character_id=? AND quantity>0 ORDER BY item_name",
            (char_id,),
        ) as cur:
            return await cur.fetchall()

async def get_item(char_id: int, item_name: str):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT item_name, quantity, item_type, data_json, image_url, description FROM inventory WHERE character_id=? AND item_name=?",
            (char_id, item_name),
        ) as cur:
            return await cur.fetchone()

async def add_item(char_id: int, item_name: str, qty: int = 1, item_type="Objeto",
                   data=None, image_url="", description=""):
    data = data or {}
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT quantity, data_json FROM inventory WHERE character_id=? AND item_name=?",
            (char_id, item_name),
        ) as cur:
            row = await cur.fetchone()
        if row:
            new_qty = max(0, row[0] + qty)
            if new_qty == 0:
                await db.execute(
                    "DELETE FROM inventory WHERE character_id=? AND item_name=?",
                    (char_id, item_name),
                )
            else:
                await db.execute(
                    "UPDATE inventory SET quantity=? WHERE character_id=? AND item_name=?",
                    (new_qty, char_id, item_name),
                )
        else:
            if qty > 0:
                await db.execute(
                    """INSERT INTO inventory (character_id, item_name, quantity, item_type, data_json, image_url, description)
                       VALUES (?,?,?,?,?,?,?)""",
                    (char_id, item_name, qty, item_type, json.dumps(data), image_url, description),
                )
        await db.commit()

async def set_money(char_id: int, amount: int):
    amount = max(0, int(amount))
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("UPDATE characters SET money=? WHERE id=?", (amount, char_id))
        await db.commit()
    return amount

async def add_money(char_id: int, delta: int):
    ch = await get_character(char_id)
    if not ch:
        return 0
    return await set_money(char_id, ch["money"] + delta)

# ==================== TIENDA ====================
async def get_shops(guild_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT id, name FROM shops WHERE guild_id=? ORDER BY name", (guild_id,)
        ) as cur:
            return await cur.fetchall()

async def get_shop_by_name(guild_id: int, name: str):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT id, name FROM shops WHERE guild_id=? AND name=?", (guild_id, name)
        ) as cur:
            return await cur.fetchone()

async def get_shop_items(shop_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT item_name, price, item_type, description, image_url, data_json FROM shop_items WHERE shop_id=? ORDER BY item_name",
            (shop_id,),
        ) as cur:
            return await cur.fetchall()

async def get_shop_item(shop_id: int, item_name: str):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT item_name, price, item_type, description, image_url, data_json FROM shop_items WHERE shop_id=? AND item_name=?",
            (shop_id, item_name),
        ) as cur:
            return await cur.fetchone()

# ==================== RECETAS ====================
async def get_recipes(guild_id: int, profession: str = None):
    async with aiosqlite.connect(DATABASE) as db:
        if profession:
            async with db.execute(
                "SELECT id, name, profession, grade, result_name, result_type, result_data, materials_json, needs_magic, magic_material, image_url FROM recipes WHERE guild_id=? AND profession=? ORDER BY name",
                (guild_id, profession),
            ) as cur:
                return await cur.fetchall()
        async with db.execute(
            "SELECT id, name, profession, grade, result_name, result_type, result_data, materials_json, needs_magic, magic_material, image_url FROM recipes WHERE guild_id=? ORDER BY name",
            (guild_id,),
        ) as cur:
            return await cur.fetchall()

async def get_recipe(recipe_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT id, name, profession, grade, result_name, result_type, result_data, materials_json, needs_magic, magic_material, image_url FROM recipes WHERE id=?",
            (recipe_id,),
        ) as cur:
            return await cur.fetchone()

# ==================== COMBATE HELPERS ====================
async def get_active_combat_for_char(char_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            """SELECT c.id FROM combats c
               JOIN combat_participants p ON p.combat_id=c.id
               WHERE p.character_id=? AND c.status='active'""",
            (char_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

async def get_combat(combat_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM combats WHERE id=?", (combat_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def get_participants(combat_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM combat_participants WHERE combat_id=? ORDER BY team, character_id",
            (combat_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

async def append_log(combat_id: int, line: str):
    combat = await get_combat(combat_id)
    if not combat:
        return
    log = json.loads(combat.get("log_json") or "[]")
    log.append(line)
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("UPDATE combats SET log_json=? WHERE id=?", (json.dumps(log[-100:]), combat_id))
        await db.commit()

async def update_participant(combat_id: int, char_id: int, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [combat_id, char_id]
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            f"UPDATE combat_participants SET {sets} WHERE combat_id=? AND character_id=?",
            vals,
        )
        await db.commit()

# ==================== AUTOCOMPLETE ====================
async def ac_user_chars(interaction: discord.Interaction, current: str):
    chars = await get_user_characters(interaction.guild_id, interaction.user.id)
    current = (current or "").lower()
    out = []
    for cid, name, level, cls, prof in chars:
        label = f"{name} (Nv.{level})"
        if current in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=str(cid)))
    return out[:25]

async def get_all_characters(guild_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            """SELECT id, name, level, class_name, user_id FROM characters
               WHERE guild_id=? AND alive=1 ORDER BY name""",
            (guild_id,),
        ) as cur:
            return await cur.fetchall()

async def ac_all_chars(interaction: discord.Interaction, current: str):
    """Autocomplete de TODOS los PJs del servidor (filtrable por nombre)."""
    chars = await get_all_characters(interaction.guild_id)
    current = (current or "").lower()
    out = []
    for cid, name, level, cls, user_id in chars:
        label = f"{name} · Nv.{level}"
        if cls:
            label += f" · {cls}"
        label += f" · ID {cid}"
        if current in name.lower() or current in label.lower() or current in str(cid):
            out.append(app_commands.Choice(name=label[:100], value=str(cid)))
    return out[:25]

async def ac_shops(interaction: discord.Interaction, current: str):
    shops = await get_shops(interaction.guild_id)
    current = (current or "").lower()
    return [
        app_commands.Choice(name=n, value=n)
        for _id, n in shops if current in n.lower()
    ][:25]

async def ac_shop_items(interaction: discord.Interaction, current: str):
    tienda = getattr(interaction.namespace, "tienda", None)
    if not tienda:
        return []
    shop = await get_shop_by_name(interaction.guild_id, tienda)
    if not shop:
        return []
    items = await get_shop_items(shop[0])
    current = (current or "").lower()
    return [
        app_commands.Choice(name=n, value=n)
        for n, *_ in items if current in n.lower()
    ][:25]

async def ac_inv_items(interaction: discord.Interaction, current: str):
    pj = getattr(interaction.namespace, "pj", None) or getattr(interaction.namespace, "pj_origen", None)
    if not pj:
        return []
    try:
        char_id = int(pj)
    except ValueError:
        return []
    items = await get_inventory(char_id)
    current = (current or "").lower()
    return [
        app_commands.Choice(name=f"{n} x{q}", value=n)
        for n, q, *_ in items if current in n.lower()
    ][:25]

async def ac_classes(interaction: discord.Interaction, current: str):
    classes = await list_classes(interaction.guild_id)
    current = (current or "").lower()
    return [app_commands.Choice(name=c, value=c) for c in classes if current in c.lower()][:25]

async def ac_professions(interaction: discord.Interaction, current: str):
    profs = await list_professions(interaction.guild_id)
    current = (current or "").lower()
    return [app_commands.Choice(name=p, value=p) for p in profs if current in p.lower()][:25]

# ==================== VIEWS COMBATE ====================
class CombatView(discord.ui.View):
    def __init__(self, combat_id: int):
        super().__init__(timeout=None)
        self.combat_id = combat_id

    async def _current_actor(self):
        combat = await get_combat(self.combat_id)
        if not combat or combat["status"] != "active":
            return None, None
        parts = [p for p in await get_participants(self.combat_id) if p["alive"]]
        if not parts:
            return combat, None
        # Orden: quien inicia primero ya está en turn_index; luego por velocidad
        idx = combat["turn_index"] % len(parts)
        # Ordenar por VEL desc manteniendo estabilidad
        parts_sorted = sorted(parts, key=lambda p: (-(json.loads(p["stats_json"]).get("VEL", 0)), p["character_id"]))
        # turn_index recorre parts_sorted
        actor = parts_sorted[idx % len(parts_sorted)]
        return combat, actor

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        combat, actor = await self._current_actor()
        if not combat or not actor:
            await interaction.response.send_message("Este combate ya no está activo.", ephemeral=True)
            return False
        if interaction.user.id != actor["user_id"]:
            await interaction.response.send_message("❌ No es tu turno.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Atacar", style=discord.ButtonStyle.danger, emoji="⚔️")
    async def atacar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Escribe el ataque así:\n`!ataque <id_pj_objetivo> <nombre_arma> | descripción`\n"
            "Ejemplo: `!ataque 3 Espada de Hierro | Golpeo con fuerza`",
            ephemeral=True,
        )

    @discord.ui.button(label="Defender", style=discord.ButtonStyle.primary, emoji="🛡️")
    async def defender(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Escribe la defensa así:\n`!defensa <nombre_arma_o_escudo> | descripción`\n"
            "O si eres tanque protegiendo a alguien:\n`!defensa <id_aliado> <arma> | descripción`",
            ephemeral=True,
        )

    @discord.ui.button(label="Esquivar", style=discord.ButtonStyle.secondary, emoji="💨")
    async def esquivar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Escribe: `!esquivar | descripción del esquive`",
            ephemeral=True,
        )

    @discord.ui.button(label="Inventario", style=discord.ButtonStyle.success, emoji="🎒")
    async def inventario(self, interaction: discord.Interaction, button: discord.ui.Button):
        combat, actor = await self._current_actor()
        if not actor:
            return
        items = await get_inventory(actor["character_id"])
        if not items:
            await interaction.response.send_message("Inventario vacío.", ephemeral=True)
            return
        text = "\n".join(f"• **{n}** x{q} ({t})" for n, q, t, *_ in items)
        await interaction.response.send_message(
            f"🎒 Inventario (usa `!usar-combate <item> | descripción`):\n{text}",
            ephemeral=True,
        )

    @discord.ui.button(label="Huir", style=discord.ButtonStyle.secondary, emoji="🏃")
    async def huir(self, interaction: discord.Interaction, button: discord.ui.Button):
        combat, actor = await self._current_actor()
        if not combat or not actor:
            return
        if combat["combat_type"] == "amistoso":
            await update_participant(self.combat_id, actor["character_id"], alive=0, vida=0)
            await append_log(self.combat_id, f"🏃 {actor['character_id']} huyó (amistoso).")
            await interaction.response.send_message("Has huido. Combate amistoso: pierdes.")
            await check_combat_end(self.combat_id, interaction.channel)
        else:
            roll = random.randint(1, 10)
            if roll >= 8:
                await update_participant(self.combat_id, actor["character_id"], alive=0, vida=0)
                await append_log(self.combat_id, f"🏃 {actor['character_id']} huyó (dado {roll}).")
                await interaction.response.send_message(f"Dado: **{roll}** — ¡Lograste huir!")
                await check_combat_end(self.combat_id, interaction.channel)
            else:
                await update_participant(self.combat_id, actor["character_id"], alive=0, vida=0)
                await append_log(self.combat_id, f"💀 {actor['character_id']} falló huida (dado {roll}) y cayó.")
                await interaction.response.send_message(f"Dado: **{roll}** — Fallaste. Tu vida cae a 0.")
                await handle_downed(self.combat_id, actor, interaction.channel)

    @discord.ui.button(label="Ver log", style=discord.ButtonStyle.secondary, emoji="📜", row=1)
    async def ver_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        combat = await get_combat(self.combat_id)
        log = json.loads(combat.get("log_json") or "[]")
        text = "\n".join(log[-20:]) if log else "Sin acciones aún."
        await interaction.response.send_message(f"📜 **Log del combate**\n{text}", ephemeral=True)

async def advance_turn(combat_id: int, channel):
    combat = await get_combat(combat_id)
    if not combat or combat["status"] != "active":
        return
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "UPDATE combats SET turn_index=turn_index+1 WHERE id=?", (combat_id,)
        )
        await db.commit()
    combat, actor = await CombatView(combat_id)._current_actor()
    if not actor:
        return
    ch = await get_character(actor["character_id"])
    name = ch["name"] if ch else str(actor["character_id"])
    embed = discord.Embed(
        title="⚔️ Turno de combate",
        description=f"Le toca a <@{actor['user_id']}> — **{name}**\nVida: **{actor['vida']}** | Maná: **{actor['mana']}**",
        color=discord.Color.orange(),
    )
    await channel.send(content=f"<@{actor['user_id']}>", embed=embed, view=CombatView(combat_id))

async def handle_downed(combat_id: int, actor: dict, channel):
    combat = await get_combat(combat_id)
    ch = await get_character(actor["character_id"])
    if not combat or not ch:
        return
    if combat["combat_type"] == "amistoso":
        await channel.send(f"💔 **{ch['name']}** ha caído (amistoso). No se elimina el PJ.")
        await check_combat_end(combat_id, channel)
        return
    # Buscar ítem revivir
    items = await get_inventory(actor["character_id"])
    revive_item = None
    for n, q, t, data_json, img, desc in items:
        data = json.loads(data_json or "{}")
        if data.get("REVIVIR") or "$REVIVIR" in (desc or "").upper():
            revive_item = n
            break
    if revive_item:
        view = ReviveView(combat_id, actor["character_id"], revive_item)
        await channel.send(
            f"💀 **{ch['name']}** cayó a muerte. ¿Usar **{revive_item}** para revivir?",
            view=view,
        )
    else:
        await delete_character(actor["character_id"])
        await channel.send(f"💀 **{ch['name']}** ha muerto y fue eliminado. Cupo liberado.")
        await check_combat_end(combat_id, channel)

async def delete_character(char_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("DELETE FROM inventory WHERE character_id=?", (char_id,))
        await db.execute("DELETE FROM characters WHERE id=?", (char_id,))
        await db.commit()

async def check_combat_end(combat_id: int, channel):
    parts = await get_participants(combat_id)
    alive = [p for p in parts if p["alive"]]
    if len(alive) <= 1:
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("UPDATE combats SET status='ended' WHERE id=?", (combat_id,))
            await db.commit()
        if len(alive) == 1:
            ch = await get_character(alive[0]["character_id"])
            await channel.send(f"🏆 Combate terminado. Gana **{ch['name'] if ch else 'alguien'}**.")
        else:
            await channel.send(" Combate terminado. Nadie queda en pie.")
        return
    # Grupo: preguntar si continuar cuando alguien cae — simplificado: auto-continúa
    await advance_turn(combat_id, channel)

class ReviveView(discord.ui.View):
    def __init__(self, combat_id: int, char_id: int, item_name: str):
        super().__init__(timeout=120)
        self.combat_id = combat_id
        self.char_id = char_id
        self.item_name = item_name

    @discord.ui.button(label="Revivir", style=discord.ButtonStyle.success, emoji="✨")
    async def revivir(self, interaction: discord.Interaction, button: discord.ui.Button):
        ch = await get_character(self.char_id)
        if not ch or interaction.user.id != ch["user_id"]:
            await interaction.response.send_message("No es tu personaje.", ephemeral=True)
            return
        item = await get_item(self.char_id, self.item_name)
        if not item:
            await interaction.response.send_message("Ya no tienes ese ítem.", ephemeral=True)
            return
        await add_item(self.char_id, self.item_name, -1)
        stats = json.loads(ch["stats_json"] or "{}")
        vida = stats.get("VIDA_MAX", 20)
        await update_participant(self.combat_id, self.char_id, alive=1, vida=vida)
        await append_log(self.combat_id, f"✨ {ch['name']} revivió con {self.item_name}.")
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("UPDATE combats SET status='ended' WHERE id=?", (self.combat_id,))
            await db.commit()
        await interaction.response.send_message(f"✨ **{ch['name']}** revive. Combate finalizado.")
        self.stop()

    @discord.ui.button(label="No revivir", style=discord.ButtonStyle.danger)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        ch = await get_character(self.char_id)
        if not ch or interaction.user.id != ch["user_id"]:
            await interaction.response.send_message("No es tu personaje.", ephemeral=True)
            return
        await delete_character(self.char_id)
        await interaction.response.send_message(f"💀 **{ch['name']}** eliminado.")
        await check_combat_end(self.combat_id, interaction.channel)
        self.stop()

class MesaView(discord.ui.View):
    def __init__(self, char_id: int, profession: str):
        super().__init__(timeout=120)
        self.char_id = char_id
        self.profession = profession

    @discord.ui.button(label="Craftear", style=discord.ButtonStyle.primary, emoji="🛠️")
    async def craftear(self, interaction: discord.Interaction, button: discord.ui.Button):
        ch = await get_character(self.char_id)
        if not ch or ch["user_id"] != interaction.user.id:
            await interaction.response.send_message("No es tu PJ.", ephemeral=True)
            return
        recipes = await get_recipes(interaction.guild_id, self.profession)
        rank = rank_from_level(ch["level"])
        max_grade = GRADE_ORDER.get(GRADE_BY_RANK.get(rank, "Comun"), 0)
        available = [r for r in recipes if GRADE_ORDER.get(r[3], 0) <= max_grade]
        if not available:
            await interaction.response.send_message("No hay recetas disponibles para tu grado.", ephemeral=True)
            return
        lines = []
        for r in available[:20]:
            mats = json.loads(r[7] or "{}")
            mat_txt = ", ".join(f"{k}x{v}" for k, v in mats.items())
            magic = f" | Magia: {r[9]}" if r[8] else ""
            lines.append(f"**ID {r[0]}** — {r[1]} [{r[3]}] → {r[4]} ({mat_txt}){magic}")
        await interaction.response.send_message(
            "🛠️ Recetas (usa `!craft <id_receta> <nombre_opcional>`):\n" + "\n".join(lines),
            ephemeral=True,
        )

    @discord.ui.button(label="Reparar", style=discord.ButtonStyle.secondary, emoji="🔧")
    async def reparar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.profession != "Herrero":
            await interaction.response.send_message("Solo herreros reparan armas/armaduras.", ephemeral=True)
            return
        await interaction.response.send_message(
            "🔧 Escribe: `!reparar-arma <nombre_arma>`\n"
            "Necesitas la mitad de materiales de la receta original y el grado adecuado.",
            ephemeral=True,
        )

# ==================== BOT EVENTS ====================
@bot.event
async def on_ready():
    await init_db()
    try:
        synced = await tree.sync()
        print(f"✅ Conectado como {bot.user} | {len(synced)} comandos")
    except Exception as e:
        print("Error sync:", e)

@bot.event
async def on_message(message: discord.Message):
    if not message.guild:
        return
    # Nunca procesar mensajes del propio bot (evita bucles de dados)
    if bot.user and message.author.id == bot.user.id:
        return

    # Mención al bot
    if bot.user and bot.user.mentioned_in(message) and not message.mention_everyone:
        await message.reply("Estoy aquí ✅", mention_author=False)
        # Si solo lo mencionaron, no seguir procesando
        if message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip() == "":
            return

    if not message.content:
        return

    content = message.content.strip()
    content_lower = content.lower()
    clean = content.replace(" ", "")

    # Calculadora
    if re.match(r"^[\d\s\+\-\*\/\×\÷\^\(\)\.\%]+$", clean) and any(op in content for op in "+-*/×÷^%"):
        result = safe_eval(content)
        if result is not None:
            if isinstance(result, float) and result.is_integer():
                result = int(result)
            await message.reply(f"**{content} = {result}**", mention_author=False)
            return

    # Dados: SOLO si el mensaje entero es NdM
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
                text = f"🎲 **{num_dice}d{sides}** → {', '.join(map(str, results))}\n**Total: {total}**"
            await message.reply(text, mention_author=False)
            return

    # Elige
    if content_lower.startswith("elige:"):
        options = [o.strip() for o in content[6:].split(",") if o.strip()]
        if len(options) >= 2:
            await message.reply(f"🎯 **He elegido:** {random.choice(options)}", mention_author=False)
            return

    # Prefijo combate / crafteo rápido
    if not message.author.bot:
        await handle_prefix_commands(message)

    # XP solo con tupper vinculado en canal/categoría XP
    cfg = await get_guild_config(message.guild.id)
    xp_channels = cfg.get("xp_channels") or []
    xp_categories = cfg.get("xp_categories") or []
    in_xp_place = message.channel.id in xp_channels
    cat_id = getattr(message.channel, "category_id", None)
    if cat_id and cat_id in xp_categories:
        in_xp_place = True
    if in_xp_place and message.webhook_id:
        tupper = (message.author.display_name or message.author.name or "").strip()
        if tupper:
            char_id = await find_char_by_tupper(message.guild.id, tupper)
            if char_id:
                await grant_xp(char_id, random.randint(5, 15), message.channel)

async def find_char_by_tupper(guild_id: int, tupper_name: str):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT id FROM characters WHERE guild_id=? AND alive=1 AND LOWER(tupper_name)=LOWER(?)",
            (guild_id, tupper_name.strip()),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

async def grant_xp(char_id: int, amount: int, channel=None):
    ch = await get_character(char_id)
    if not ch:
        return
    cfg = await get_guild_config(ch["guild_id"])
    max_level = cfg.get("max_level") or 50
    new_xp = ch["xp"] + amount
    new_level = min(calculate_level(new_xp), max_level)
    old_level = ch["level"]
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "UPDATE characters SET xp=?, level=? WHERE id=?",
            (new_xp, new_level, char_id),
        )
        await db.commit()
    if new_level > old_level and channel:
        rewards = await get_level_reward(ch["guild_id"], new_level)
        # Aplicar stats de nivel
        if rewards and rewards[1]:
            stats = json.loads(ch["stats_json"] or "{}")
            bonus = json.loads(rewards[1] or "{}")
            stats.update(bonus)
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    "UPDATE characters SET stats_json=? WHERE id=?",
                    (json.dumps(stats), char_id),
                )
                await db.commit()
        text = f"🎉 **{ch['name']}** subió al nivel **{new_level}**!"
        if rewards and rewards[0]:
            rw = json.loads(rewards[0] or "[]")
            if rw:
                text += "\nDesbloqueos:\n" + "\n".join(f"• {x}" for x in rw)
        if new_level == PROFESSION_LEVEL:
            text += "\n🛠️ ¡Puedes elegir profesión con `/profesion`!"
        await channel.send(text)

async def get_level_reward(guild_id: int, level: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT rewards, stats_json FROM level_rewards WHERE guild_id=? AND level=?",
            (guild_id, level),
        ) as cur:
            return await cur.fetchone()

async def handle_prefix_commands(message: discord.Message):
    content = message.content.strip()
    if content.startswith("!craft "):
        parts = content[7:].strip().split(maxsplit=1)
        if not parts:
            return
        try:
            rid = int(parts[0])
        except ValueError:
            await message.reply("ID de receta inválido.")
            return
        custom_name = parts[1] if len(parts) > 1 else None
        await do_craft(message, rid, custom_name)
    elif content.startswith("!reparar-arma "):
        arma = content[14:].strip()
        await do_repair(message, arma)
    elif content.startswith("!ataque "):
        await do_attack(message)
    elif content.startswith("!defensa "):
        await do_defend(message)
    elif content.startswith("!esquivar"):
        await do_dodge(message)
    elif content.startswith("!usar-combate "):
        await do_combat_use(message)

async def do_craft(message, recipe_id: int, custom_name: str = None):
    chars = await get_user_characters(message.guild.id, message.author.id)
    if not chars:
        await message.reply("No tienes personajes.")
        return
    # Usar primer PJ con profesión compatible; mejor: buscar quién tiene la mesa
    recipe = await get_recipe(recipe_id)
    if not recipe:
        await message.reply("Receta no existe.")
        return
    char = None
    for cid, name, level, cls, prof in chars:
        if prof == recipe[2]:
            char = await get_character(cid)
            break
    if not char:
        await message.reply(f"Ninguno de tus PJs es **{recipe[2]}**.")
        return
    rank = rank_from_level(char["level"])
    if GRADE_ORDER.get(recipe[3], 0) > GRADE_ORDER.get(GRADE_BY_RANK.get(rank, "Comun"), 0):
        await message.reply("Tu rango no alcanza para ese grado.")
        return
    mats = json.loads(recipe[7] or "{}")
    if recipe[8] and recipe[9]:
        mats[recipe[9]] = mats.get(recipe[9], 0) + 1
    for mat, need in mats.items():
        it = await get_item(char["id"], mat)
        if not it or it[1] < need:
            await message.reply(f"Te falta material: **{mat}** (necesitas {need}).")
            return
    for mat, need in mats.items():
        await add_item(char["id"], mat, -need)
    result_data = json.loads(recipe[6] or "{}")
    result_data["GRADO"] = recipe[3]
    result_data["RECETA_ID"] = recipe[0]
    result_data["MATERIALS"] = json.loads(recipe[7] or "{}")
    item_name = custom_name or recipe[4]
    await add_item(
        char["id"], item_name, 1, recipe[5], result_data,
        recipe[10] or "", f"Creado por {char['name']}"
    )
    await message.reply(f"✅ **{char['name']}** crafteó **{item_name}** [{recipe[3]}].")

async def do_repair(message, arma_name: str):
    chars = await get_user_characters(message.guild.id, message.author.id)
    herrero = None
    for cid, name, level, cls, prof in chars:
        if prof == "Herrero":
            herrero = await get_character(cid)
            break
    if not herrero:
        await message.reply("Ningún PJ tuyo es Herrero.")
        return
    # Buscar arma en inventarios del usuario
    target_char = None
    item = None
    for cid, *_ in chars:
        it = await get_item(cid, arma_name)
        if it:
            target_char = await get_character(cid)
            item = it
            break
    if not item:
        await message.reply("No tienes esa arma.")
        return
    data = json.loads(item[3] or "{}")
    grade = data.get("GRADO", "Comun")
    rank = rank_from_level(herrero["level"])
    if GRADE_ORDER.get(grade, 0) > GRADE_ORDER.get(GRADE_BY_RANK.get(rank, "Comun"), 0):
        await message.reply(f"Necesitas rango mayor para reparar armas **{grade}**.")
        return
    mats = data.get("MATERIALS") or {}
    half = {k: max(1, v // 2) for k, v in mats.items()} if mats else {}
    if not half:
        await message.reply("Esta arma no tiene materiales de receta guardados; admin puede reparar con `/reparar`.")
        return
    for mat, need in half.items():
        it = await get_item(herrero["id"], mat)
        if not it or it[1] < need:
            await message.reply(f"Falta material para reparar: **{mat}** x{need}")
            return
    for mat, need in half.items():
        await add_item(herrero["id"], mat, -need)
    # Restaurar durabilidad máxima si existe
    if "DUR_MAX" in data:
        data["DUR"] = data["DUR_MAX"]
    elif "DUR" in data:
        data["DUR"] = max(data.get("DUR", 1), data.get("DUR", 1))
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "UPDATE inventory SET data_json=? WHERE character_id=? AND item_name=?",
            (json.dumps(data), target_char["id"], arma_name),
        )
        await db.commit()
    await message.reply(f"🔧 **{arma_name}** reparada por **{herrero['name']}**.")

async def do_attack(message):
    # !ataque <obj_id> <arma> | descripcion
    body = message.content[8:].strip()
    if "|" not in body:
        await message.reply("Formato: `!ataque <id_objetivo> <arma> | descripción`")
        return
    left, desc = body.split("|", 1)
    left_parts = left.strip().split(maxsplit=1)
    if len(left_parts) < 2:
        await message.reply("Indica objetivo y arma.")
        return
    try:
        target_id = int(left_parts[0])
    except ValueError:
        await message.reply("ID de objetivo inválido.")
        return
    arma = left_parts[1].strip()
    desc = desc.strip()
    chars = await get_user_characters(message.guild.id, message.author.id)
    actor_ch = None
    combat_id = None
    for cid, *_ in chars:
        cid_combat = await get_active_combat_for_char(cid)
        if cid_combat:
            actor_ch = await get_character(cid)
            combat_id = cid_combat
            break
    if not actor_ch or not combat_id:
        await message.reply("No estás en combate activo.")
        return
    combat = await get_combat(combat_id)
    parts = await get_participants(combat_id)
    actor_p = next((p for p in parts if p["character_id"] == actor_ch["id"]), None)
    target_p = next((p for p in parts if p["character_id"] == target_id and p["alive"]), None)
    if not actor_p or not actor_p["alive"]:
        await message.reply("Tu PJ no está activo en este combate.")
        return
    # Verificar turno
    view = CombatView(combat_id)
    _, current = await view._current_actor()
    if not current or current["character_id"] != actor_ch["id"]:
        await message.reply("No es tu turno.")
        return
    if not target_p:
        await message.reply("Objetivo inválido.")
        return
    item = await get_item(actor_ch["id"], arma)
    if not item:
        await message.reply("No tienes esa arma.")
        return
    data = json.loads(item[3] or "{}")
    dmg = int(data.get("DAÑO") or data.get("PODER") or 1)
    mana_cost = int(data.get("MANA_COST") or 0)
    if mana_cost and actor_p["mana"] < mana_cost:
        await message.reply("No tienes maná suficiente.")
        return
    if mana_cost:
        await update_participant(combat_id, actor_ch["id"], mana=actor_p["mana"] - mana_cost)

    actor_stats = json.loads(actor_p["stats_json"] or "{}")
    target_stats = json.loads(target_p["stats_json"] or "{}")
    # ¿Está defendiendo alguien a este objetivo?
    defender = next(
        (p for p in parts if p["alive"] and p.get("defending_target") == target_id),
        None,
    )
    applied_to = target_p
    note = ""
    if defender:
        applied_to = defender
        note = " (interceptado por tanque)"
        def_ch = await get_character(defender["character_id"])
        # daño a arma del defensor si tiene pending defense weapon en stats temp — simplificado a vida
        new_hp = max(0, applied_to["vida"] - dmg)
        await update_participant(combat_id, applied_to["character_id"], vida=new_hp, alive=1 if new_hp > 0 else 0)
        await append_log(combat_id, f"⚔️ {actor_ch['name']} ataca a {target_id} con {arma}: {dmg} dmg{note}. {desc}")
        await message.channel.send(
            f"⚔️ **{actor_ch['name']}** ataca con **{arma}** → {dmg} daño{note}\n_{desc}_"
        )
        if new_hp <= 0:
            await handle_downed(combat_id, applied_to, message.channel)
        else:
            await advance_turn(combat_id, message.channel)
        return

    # Esquiva se resolvió en turno previo si marcó dodge — simplificado: comparar VEL si target marcó esquivar en data
    # Impacto directo
    efecto = data.get("EFECTO")
    new_hp = max(0, target_p["vida"] - dmg)
    effects = json.loads(target_p["effects_json"] or "[]")
    if efecto and new_hp > 0 and efecto in EFFECTS:
        if len(effects) < 2:
            effects.append({"name": efecto, "turns": int(data.get("EFECTO_TURNOS") or 2)})
    await update_participant(
        combat_id, target_id,
        vida=new_hp,
        alive=1 if new_hp > 0 else 0,
        effects_json=json.dumps(effects),
    )
    tch = await get_character(target_id)
    await append_log(combat_id, f"⚔️ {actor_ch['name']} → {tch['name'] if tch else target_id} con {arma}: {dmg} dmg. {desc}")
    await message.channel.send(
        f"⚔️ **{actor_ch['name']}** ataca a **{tch['name'] if tch else target_id}** con **{arma}**\n"
        f"Daño: **{dmg}** | Vida restante: **{new_hp}**\n_{desc}_"
    )
    if new_hp <= 0:
        await handle_downed(combat_id, target_p, message.channel)
    else:
        await advance_turn(combat_id, message.channel)

async def do_defend(message):
    body = message.content[9:].strip()
    if "|" not in body:
        await message.reply("Formato: `!defensa <arma> | descripción` o `!defensa <id_aliado> <arma> | desc`")
        return
    left, desc = body.split("|", 1)
    parts_l = left.strip().split()
    chars = await get_user_characters(message.guild.id, message.author.id)
    actor_ch = None
    combat_id = None
    for cid, *_ in chars:
        c = await get_active_combat_for_char(cid)
        if c:
            actor_ch = await get_character(cid)
            combat_id = c
            break
    if not actor_ch:
        await message.reply("No estás en combate.")
        return
    view = CombatView(combat_id)
    _, current = await view._current_actor()
    if not current or current["character_id"] != actor_ch["id"]:
        await message.reply("No es tu turno.")
        return
    protect_id = actor_ch["id"]
    arma = left.strip()
    if parts_l and parts_l[0].isdigit():
        protect_id = int(parts_l[0])
        arma = " ".join(parts_l[1:]) if len(parts_l) > 1 else ""
    await update_participant(combat_id, actor_ch["id"], defending_target=protect_id)
    await append_log(combat_id, f"🛡️ {actor_ch['name']} defiende a {protect_id} con {arma}. {desc.strip()}")
    await message.channel.send(f"🛡️ **{actor_ch['name']}** se prepara para defender.\n_{desc.strip()}_")
    await advance_turn(combat_id, message.channel)

async def do_dodge(message):
    desc = ""
    if "|" in message.content:
        desc = message.content.split("|", 1)[1].strip()
    chars = await get_user_characters(message.guild.id, message.author.id)
    actor_ch = None
    combat_id = None
    for cid, *_ in chars:
        c = await get_active_combat_for_char(cid)
        if c:
            actor_ch = await get_character(cid)
            combat_id = c
            break
    if not actor_ch:
        await message.reply("No estás en combate.")
        return
    view = CombatView(combat_id)
    _, current = await view._current_actor()
    if not current or current["character_id"] != actor_ch["id"]:
        await message.reply("No es tu turno.")
        return
    # Marcar alta velocidad temporal como intención de esquivar: guardamos flag en effects
    effects = json.loads(current["effects_json"] or "[]")
    effects = [e for e in effects if e.get("name") != "ESQUIVANDO"]
    effects.append({"name": "ESQUIVANDO", "turns": 1})
    await update_participant(combat_id, actor_ch["id"], effects_json=json.dumps(effects))
    await append_log(combat_id, f"💨 {actor_ch['name']} intenta esquivar. {desc}")
    await message.channel.send(f"💨 **{actor_ch['name']}** se prepara para esquivar.\n_{desc}_")
    await advance_turn(combat_id, message.channel)

async def do_combat_use(message):
    body = message.content[14:].strip()
    if "|" not in body:
        await message.reply("Formato: `!usar-combate <item> | descripción`")
        return
    item_name, desc = body.split("|", 1)
    item_name = item_name.strip()
    desc = desc.strip()
    chars = await get_user_characters(message.guild.id, message.author.id)
    actor_ch = None
    combat_id = None
    for cid, *_ in chars:
        c = await get_active_combat_for_char(cid)
        if c:
            actor_ch = await get_character(cid)
            combat_id = c
            break
    if not actor_ch:
        await message.reply("No estás en combate.")
        return
    view = CombatView(combat_id)
    _, current = await view._current_actor()
    if not current or current["character_id"] != actor_ch["id"]:
        await message.reply("No es tu turno.")
        return
    item = await get_item(actor_ch["id"], item_name)
    if not item:
        await message.reply("No tienes ese ítem.")
        return
    data = json.loads(item[3] or "{}")
    item_type = item[2]
    # Canciones y mesas no se gastan; pociones sí
    consumible = item_type.lower() in ("poción", "pocion", "elixir", "veneno", "comida")
    if data.get("REVIVIR"):
        consumible = True
    if consumible:
        await add_item(actor_ch["id"], item_name, -1)
    # Efecto simple de curación si tiene $VIDA en data como heal
    heal = int(data.get("CURA") or 0)
    if heal:
        new_hp = current["vida"] + heal
        await update_participant(combat_id, actor_ch["id"], vida=new_hp)
    await append_log(combat_id, f"🎒 {actor_ch['name']} usa {item_name}. {desc}")
    await message.channel.send(f"🎒 **{actor_ch['name']}** usa **{item_name}**\n_{desc}_")
    await advance_turn(combat_id, message.channel)

# ==================== COMANDOS SLASH ====================
@tree.command(name="help", description="Lista de comandos del bot RPG")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="📖 Comandos RPG", color=discord.Color.blurple())
    embed.add_field(
        name="👤 Personajes",
        value="`/crear` `/borrar-pj` `/pjs` `/perfil` `/editar-pj` `/profesion`",
        inline=False,
    )
    embed.add_field(
        name="💰 Inventario",
        value="`/dinero` `/inv` `/dar-item` `/dar-dinero` `/usar`",
        inline=False,
    )
    embed.add_field(
        name="🛒 Tienda",
        value="`/tiendas` `/ver-tienda` `/comprar`",
        inline=False,
    )
    embed.add_field(
        name="🛠️ Crafteo",
        value="`/recetas` — Usa la **mesa** del inventario con `/usar`",
        inline=False,
    )
    embed.add_field(
        name="⚔️ Combate",
        value="`/duelo` `/combate` `/log`\nBotones: Atacar, Defender, Esquivar, Inventario, Huir",
        inline=False,
    )
    embed.add_field(
        name="🎲 Utilidades",
        value="`1d20` · `Elige: a, b` · `1+2` · `/embed`",
        inline=False,
    )
    embed.add_field(
        name="🛡️ Admin",
        value="`/crear-clase` `/crear-profesion` `/crear-tienda` `/item-tienda` `/crear-material` `/crear-receta` `/craftear` `/reparar` `/dar-xp` `/quitar-xp` `/admin-dar-dinero` `/quitar-dinero` `/config-nivel` `/reward-nivel` `/canal-xp` `/categoria-xp` `/vincular-tupper` `/roles-admin` `/fin-combate`",
        inline=False,
    )
    embed.set_footer(text="Creado por 《JEFP25》")
    await interaction.response.send_message(embed=embed)

@tree.command(name="crear", description="Crea un personaje (máx. 3)")
@app_commands.describe(
    nombre="Nombre del personaje",
    clase="Clase del personaje",
    descripcion="Descripción / historia",
    imagen="URL de imagen",
    color="Color hex sin # (ej: FF5500)",
)
@app_commands.autocomplete(clase=ac_classes)
async def crear_pj(
    interaction: discord.Interaction,
    nombre: str,
    clase: str,
    descripcion: str = "",
    imagen: str = "",
    color: str = "5865F2",
):
    if await count_characters(interaction.guild_id, interaction.user.id) >= MAX_PJ:
        await interaction.response.send_message("❌ Ya tienes 3 personajes. Borra uno primero.", ephemeral=True)
        return
    cls = await get_class(interaction.guild_id, clase)
    if not cls:
        await interaction.response.send_message("❌ Esa clase no existe. Un admin debe crearla.", ephemeral=True)
        return
    # Stats de la clase (prioridad: stats_json guardado > códigos en descripción > defaults)
    stats = dict(DEFAULT_STATS)
    try:
        class_stats = json.loads(cls[2] or "{}")
        if isinstance(class_stats, dict):
            stats.update(class_stats)
    except Exception:
        pass
    stats.update(parse_codes(cls[1] or ""))
    stats["VIDA_MAX"] = int(stats.get("VIDA_MAX", stats.get("VIDA", 20)))
    stats["VIDA"] = stats["VIDA_MAX"]
    stats["MANA_MAX"] = int(stats.get("MANA_MAX", stats.get("MANA", 10)))
    stats["MANA"] = stats["MANA_MAX"]
    try:
        color_val = int(color.replace("#", ""), 16)
    except Exception:
        color_val = 0x5865F2
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute(
            """INSERT INTO characters
               (guild_id, user_id, name, description, image_url, color, class_name, stats_json)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                interaction.guild_id, interaction.user.id, nombre.strip(), descripcion,
                imagen, color_val, clase, json.dumps(stats),
            ),
        )
        await db.commit()
        char_id = cur.lastrowid
    embed = discord.Embed(
        title=f"✨ Personaje creado: {nombre}",
        description=descripcion or "Sin descripción",
        color=color_val,
    )
    embed.add_field(name="Clase", value=clase, inline=True)
    embed.add_field(name="Nivel", value="1", inline=True)
    embed.add_field(
        name="Stats",
        value="\n".join(f"{k}: {v}" for k, v in stats.items() if k in DEFAULT_STATS),
        inline=False,
    )
    if imagen:
        embed.set_thumbnail(url=imagen)
    embed.set_footer(text=f"ID: {char_id}")
    await interaction.response.send_message(embed=embed)

@tree.command(name="borrar-pj", description="Borra uno de tus personajes")
@app_commands.describe(pj="Personaje a borrar")
@app_commands.autocomplete(pj=ac_user_chars)
async def borrar_pj(interaction: discord.Interaction, pj: str):
    try:
        char_id = int(pj)
    except ValueError:
        await interaction.response.send_message("PJ inválido.", ephemeral=True)
        return
    ch = await get_character(char_id)
    if not ch or ch["user_id"] != interaction.user.id or ch["guild_id"] != interaction.guild_id:
        await interaction.response.send_message("No es tu personaje.", ephemeral=True)
        return
    if await get_active_combat_for_char(char_id):
        await interaction.response.send_message("No puedes borrar un PJ en combate.", ephemeral=True)
        return
    await delete_character(char_id)
    await interaction.response.send_message(f"🗑️ Personaje **{ch['name']}** eliminado.")

@tree.command(name="pjs", description="Lista tus personajes")
async def pjs_cmd(interaction: discord.Interaction):
    chars = await get_user_characters(interaction.guild_id, interaction.user.id)
    if not chars:
        await interaction.response.send_message("No tienes personajes. Usa `/crear`.")
        return
    lines = [
        f"**{name}** — Nv.{level} | {cls or '—'} | {prof or 'Sin profesión'} (ID `{cid}`)"
        for cid, name, level, cls, prof in chars
    ]
    embed = discord.Embed(
        title=f"👥 Tus personajes ({len(chars)}/{MAX_PJ})",
        description="\n".join(lines),
        color=discord.Color.green(),
    )
    await interaction.response.send_message(embed=embed)

@tree.command(name="perfil", description="Ver perfil de un personaje")
@app_commands.describe(pj="Personaje")
@app_commands.autocomplete(pj=ac_user_chars)
async def perfil(interaction: discord.Interaction, pj: str):
    try:
        char_id = int(pj)
    except ValueError:
        await interaction.response.send_message("PJ inválido.", ephemeral=True)
        return
    ch = await get_character(char_id)
    if not ch or ch["guild_id"] != interaction.guild_id:
        await interaction.response.send_message("Personaje no encontrado.", ephemeral=True)
        return
    stats = json.loads(ch["stats_json"] or "{}")
    embed = discord.Embed(
        title=f"👤 {ch['name']}",
        description=ch["description"] or "Sin descripción",
        color=ch["color"] or 0x5865F2,
    )
    embed.add_field(name="Clase", value=ch["class_name"] or "—", inline=True)
    embed.add_field(name="Profesión", value=ch["profession"] or "—", inline=True)
    embed.add_field(name="Nivel", value=f"{ch['level']} ({rank_from_level(ch['level'])})", inline=True)
    embed.add_field(name="XP", value=str(ch["xp"]), inline=True)
    embed.add_field(name="💰 Dinero", value=f"{ch['money']:,}", inline=True)
    embed.add_field(
        name="Stats",
        value="\n".join(
            f"**{k}:** {stats.get(k, 0)}"
            for k in ["VIDA", "VIDA_MAX", "FUE", "VEL", "RES", "DEF", "MANA", "MANA_MAX"]
            if k in stats or True
        ),
        inline=False,
    )
    if ch["image_url"]:
        embed.set_thumbnail(url=ch["image_url"])
    embed.set_footer(text=f"ID {ch['id']} · Dueño: {ch['user_id']}")
    await interaction.response.send_message(embed=embed)

@tree.command(name="editar-pj", description="Edita nombre, descripción, imagen o color")
@app_commands.describe(pj="Personaje", nombre="Nuevo nombre", descripcion="Nueva descripción", imagen="URL imagen", color="Hex color")
@app_commands.autocomplete(pj=ac_user_chars)
async def editar_pj(
    interaction: discord.Interaction,
    pj: str,
    nombre: str = None,
    descripcion: str = None,
    imagen: str = None,
    color: str = None,
):
    try:
        char_id = int(pj)
    except ValueError:
        await interaction.response.send_message("PJ inválido.", ephemeral=True)
        return
    ch = await get_character(char_id)
    if not ch or ch["user_id"] != interaction.user.id:
        await interaction.response.send_message("No es tu personaje.", ephemeral=True)
        return
    fields = {}
    if nombre:
        fields["name"] = nombre.strip()
    if descripcion is not None:
        fields["description"] = descripcion
    if imagen is not None:
        fields["image_url"] = imagen
    if color:
        try:
            fields["color"] = int(color.replace("#", ""), 16)
        except Exception:
            pass
    if not fields:
        await interaction.response.send_message("No indicaste cambios.", ephemeral=True)
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            f"UPDATE characters SET {sets} WHERE id=?",
            (*fields.values(), char_id),
        )
        await db.commit()
    await interaction.response.send_message("✅ Personaje actualizado.")

@tree.command(name="profesion", description="Elige profesión (nivel 10+)")
@app_commands.describe(pj="Personaje", profesion="Profesión")
@app_commands.autocomplete(pj=ac_user_chars, profesion=ac_professions)
async def profesion_cmd(interaction: discord.Interaction, pj: str, profesion: str):
    try:
        char_id = int(pj)
    except ValueError:
        await interaction.response.send_message("PJ inválido.", ephemeral=True)
        return
    ch = await get_character(char_id)
    if not ch or ch["user_id"] != interaction.user.id:
        await interaction.response.send_message("No es tu personaje.", ephemeral=True)
        return
    if ch["level"] < PROFESSION_LEVEL:
        await interaction.response.send_message(f"Necesitas nivel {PROFESSION_LEVEL}.", ephemeral=True)
        return
    if ch["profession"]:
        await interaction.response.send_message("Ya tiene profesión.", ephemeral=True)
        return
    profs = await list_professions(interaction.guild_id)
    if profesion not in profs:
        await interaction.response.send_message("Profesión no existe.", ephemeral=True)
        return
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("UPDATE characters SET profession=? WHERE id=?", (profesion, char_id))
        await db.commit()
    await interaction.response.send_message(f"🛠️ **{ch['name']}** ahora es **{profesion}**.")

@tree.command(name="dinero", description="Ver dinero de un PJ")
@app_commands.describe(pj="Personaje")
@app_commands.autocomplete(pj=ac_user_chars)
async def dinero_cmd(interaction: discord.Interaction, pj: str):
    ch = await get_character(int(pj))
    if not ch:
        await interaction.response.send_message("PJ no encontrado.", ephemeral=True)
        return
    await interaction.response.send_message(f"💰 **{ch['name']}** tiene **{ch['money']:,}** monedas.")

@tree.command(name="inv", description="Inventario de un PJ")
@app_commands.describe(pj="Personaje")
@app_commands.autocomplete(pj=ac_user_chars)
async def inv_cmd(interaction: discord.Interaction, pj: str):
    ch = await get_character(int(pj))
    if not ch:
        await interaction.response.send_message("PJ no encontrado.", ephemeral=True)
        return
    items = await get_inventory(ch["id"])
    if not items:
        await interaction.response.send_message(f"🎒 Inventario de **{ch['name']}** vacío.")
        return
    text = "\n".join(f"• **{n}** x{q} — _{t}_" for n, q, t, *_ in items)
    embed = discord.Embed(title=f"🎒 Inventario de {ch['name']}", description=text, color=discord.Color.green())
    await interaction.response.send_message(embed=embed)

@tree.command(name="dar-item", description="Dar ítem de tu PJ a otro PJ")
@app_commands.describe(pj_origen="Tu personaje", pj_destino="Personaje destino (escribe el nombre)", item="Ítem", cantidad="Cantidad")
@app_commands.autocomplete(pj_origen=ac_user_chars, pj_destino=ac_all_chars, item=ac_inv_items)
async def dar_item_cmd(
    interaction: discord.Interaction,
    pj_origen: str,
    pj_destino: str,
    item: str,
    cantidad: app_commands.Range[int, 1, 1000] = 1,
):
    origen = await get_character(int(pj_origen))
    dest = await get_character(int(pj_destino))
    if not origen or origen["user_id"] != interaction.user.id:
        await interaction.response.send_message("PJ origen inválido.", ephemeral=True)
        return
    if not dest or dest["guild_id"] != interaction.guild_id:
        await interaction.response.send_message("PJ destino inválido.", ephemeral=True)
        return
    it = await get_item(origen["id"], item)
    if not it or it[1] < cantidad:
        await interaction.response.send_message("No tienes suficiente.", ephemeral=True)
        return
    data = json.loads(it[3] or "{}")
    await add_item(origen["id"], item, -cantidad)
    await add_item(dest["id"], item, cantidad, it[2], data, it[4] or "", it[5] or "")
    await interaction.response.send_message(
        f"✅ **{origen['name']}** dio **{item}** x{cantidad} a **{dest['name']}**."
    )

@tree.command(name="dar-dinero", description="Dar dinero de tu PJ a otro PJ")
@app_commands.describe(pj_origen="Tu personaje", pj_destino="Personaje destino (escribe el nombre)", cantidad="Monedas")
@app_commands.autocomplete(pj_origen=ac_user_chars, pj_destino=ac_all_chars)
async def dar_dinero_cmd(
    interaction: discord.Interaction,
    pj_origen: str,
    pj_destino: str,
    cantidad: app_commands.Range[int, 1, 1_000_000_000],
):
    origen = await get_character(int(pj_origen))
    dest = await get_character(int(pj_destino))
    if not origen or origen["user_id"] != interaction.user.id:
        await interaction.response.send_message("PJ origen inválido.", ephemeral=True)
        return
    if not dest:
        await interaction.response.send_message("PJ destino inválido.", ephemeral=True)
        return
    if origen["money"] < cantidad:
        await interaction.response.send_message("No tienes suficiente dinero.", ephemeral=True)
        return
    await add_money(origen["id"], -cantidad)
    await add_money(dest["id"], cantidad)
    await interaction.response.send_message(
        f"💰 **{origen['name']}** dio **{cantidad:,}** a **{dest['name']}**."
    )

@tree.command(name="usar", description="Usar un ítem del inventario (mesa, poción, etc.)")
@app_commands.describe(pj="Personaje", item="Ítem a usar")
@app_commands.autocomplete(pj=ac_user_chars, item=ac_inv_items)
async def usar_cmd(interaction: discord.Interaction, pj: str, item: str):
    ch = await get_character(int(pj))
    if not ch or ch["user_id"] != interaction.user.id:
        await interaction.response.send_message("No es tu PJ.", ephemeral=True)
        return
    it = await get_item(ch["id"], item)
    if not it:
        await interaction.response.send_message("No tienes ese ítem.", ephemeral=True)
        return
    item_type = (it[2] or "").lower()
    # Mesa
    if "mesa" in item.lower() or item_type == "mesa":
        prof = ch["profession"]
        if not prof or prof == "Bardo":
            await interaction.response.send_message("Este PJ no usa mesa de crafteo.", ephemeral=True)
            return
        expected = MESA_BY_PROF.get(prof, "")
        if expected and expected.lower() not in item.lower() and item.lower() not in expected.lower():
            # Permitir si el nombre contiene la profesión
            if prof.lower() not in item.lower():
                await interaction.response.send_message(
                    f"Esta mesa no corresponde a tu profesión (**{prof}**).", ephemeral=True
                )
                return
        await interaction.response.send_message(
            f"🛠️ Mesa de **{prof}** — elige una opción:",
            view=MesaView(ch["id"], prof),
            ephemeral=True,
        )
        return
    # Consumible simple fuera de combate
    data = json.loads(it[3] or "{}")
    if item_type in ("poción", "pocion", "elixir", "comida"):
        await add_item(ch["id"], item, -1)
        heal = int(data.get("CURA") or 0)
        if heal:
            stats = json.loads(ch["stats_json"] or "{}")
            stats["VIDA"] = min(stats.get("VIDA_MAX", 20), stats.get("VIDA", 0) + heal)
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    "UPDATE characters SET stats_json=? WHERE id=?",
                    (json.dumps(stats), ch["id"]),
                )
                await db.commit()
        await interaction.response.send_message(f"✅ **{ch['name']}** usó **{item}**.")
        return
    await interaction.response.send_message(
        "Este ítem se usa en combate o no tiene uso directo fuera.", ephemeral=True
    )

# ---- Tienda ----
@tree.command(name="tiendas", description="Lista las tiendas")
async def tiendas_cmd(interaction: discord.Interaction):
    shops = await get_shops(interaction.guild_id)
    if not shops:
        await interaction.response.send_message("No hay tiendas.")
        return
    embed = discord.Embed(
        title="🛒 Tiendas",
        description="\n".join(f"• **{n}**" for _i, n in shops),
        color=discord.Color.orange(),
    )
    await interaction.response.send_message(embed=embed)

@tree.command(name="ver-tienda", description="Ver productos de una tienda")
@app_commands.describe(tienda="Tienda")
@app_commands.autocomplete(tienda=ac_shops)
async def ver_tienda(interaction: discord.Interaction, tienda: str):
    shop = await get_shop_by_name(interaction.guild_id, tienda)
    if not shop:
        await interaction.response.send_message("No existe.", ephemeral=True)
        return
    items = await get_shop_items(shop[0])
    if not items:
        await interaction.response.send_message("Tienda vacía.")
        return
    text = ""
    thumb = None
    for name, price, itype, desc, img, data in items:
        text += f"• **{name}** ({itype}) — {price:,} 💰\n"
        if img and not thumb:
            thumb = img
    embed = discord.Embed(title=f"🛒 {tienda}", description=text, color=discord.Color.orange())
    if thumb:
        embed.set_thumbnail(url=thumb)
    await interaction.response.send_message(embed=embed)

@tree.command(name="comprar", description="Comprar ítem para un PJ")
@app_commands.describe(pj="Personaje que compra", tienda="Tienda", item="Ítem", cantidad="Cantidad")
@app_commands.autocomplete(pj=ac_user_chars, tienda=ac_shops, item=ac_shop_items)
async def comprar_cmd(
    interaction: discord.Interaction,
    pj: str,
    tienda: str,
    item: str,
    cantidad: app_commands.Range[int, 1, 100] = 1,
):
    ch = await get_character(int(pj))
    if not ch or ch["user_id"] != interaction.user.id:
        await interaction.response.send_message("No es tu PJ.", ephemeral=True)
        return
    shop = await get_shop_by_name(interaction.guild_id, tienda)
    if not shop:
        await interaction.response.send_message("Tienda no existe.", ephemeral=True)
        return
    product = await get_shop_item(shop[0], item)
    if not product:
        await interaction.response.send_message("Ítem no está en la tienda.", ephemeral=True)
        return
    name, price, itype, desc, img, data_json = product
    total = price * cantidad
    if ch["money"] < total:
        await interaction.response.send_message(f"Te faltan monedas ({total:,}).", ephemeral=True)
        return
    await add_money(ch["id"], -total)
    data = json.loads(data_json or "{}")
    await add_item(ch["id"], name, cantidad, itype, data, img or "", desc or "")
    embed = discord.Embed(
        title="✅ Compra realizada",
        description=f"**{ch['name']}** compró **{name}** x{cantidad}\nPrecio: **{total:,}**",
        color=discord.Color.green(),
    )
    if img:
        embed.set_thumbnail(url=img)
    await interaction.response.send_message(embed=embed)

@tree.command(name="recetas", description="Lista recetas de crafteo")
async def recetas_cmd(interaction: discord.Interaction):
    recipes = await get_recipes(interaction.guild_id)
    if not recipes:
        await interaction.response.send_message("No hay recetas.")
        return
    lines = [
        f"**{r[1]}** [{r[3]}] — {r[2]} → {r[4]} (ID {r[0]})"
        for r in recipes[:30]
    ]
    embed = discord.Embed(title="📜 Recetas", description="\n".join(lines), color=discord.Color.teal())
    await interaction.response.send_message(embed=embed)

# ---- Combate ----
@tree.command(name="duelo", description="Inicia un combate")
@app_commands.describe(
    tu_pj="Tu personaje",
    enemigo="Usuario enemigo",
    enemigo_pj="Personaje enemigo (escribe el nombre)",
    tipo="amistoso o muerte",
    tu_rol="Tu rol",
    enemigo_rol="Rol del enemigo",
)
@app_commands.autocomplete(tu_pj=ac_user_chars, enemigo_pj=ac_all_chars)
@app_commands.choices(
    tipo=[
        app_commands.Choice(name="Amistoso", value="amistoso"),
        app_commands.Choice(name="A muerte", value="muerte"),
    ],
    tu_rol=[
        app_commands.Choice(name="Atacante", value="Atacante"),
        app_commands.Choice(name="Tanque", value="Tanque"),
        app_commands.Choice(name="Mago", value="Mago"),
    ],
    enemigo_rol=[
        app_commands.Choice(name="Atacante", value="Atacante"),
        app_commands.Choice(name="Tanque", value="Tanque"),
        app_commands.Choice(name="Mago", value="Mago"),
    ],
)
async def duelo_cmd(
    interaction: discord.Interaction,
    tu_pj: str,
    enemigo: discord.Member,
    enemigo_pj: str,
    tipo: str = "amistoso",
    tu_rol: str = "Atacante",
    enemigo_rol: str = "Atacante",
):
    a = await get_character(int(tu_pj))
    b = await get_character(int(enemigo_pj))
    if not a or a["user_id"] != interaction.user.id:
        await interaction.response.send_message("Tu PJ inválido.", ephemeral=True)
        return
    if not b or b["user_id"] != enemigo.id:
        await interaction.response.send_message("PJ enemigo inválido.", ephemeral=True)
        return
    if await get_active_combat_for_char(a["id"]) or await get_active_combat_for_char(b["id"]):
        await interaction.response.send_message("Uno de los PJs ya está en combate.", ephemeral=True)
        return
    sa = json.loads(a["stats_json"] or "{}")
    sb = json.loads(b["stats_json"] or "{}")
    async with aiosqlite.connect(DATABASE) as db:
        cur = await db.execute(
            """INSERT INTO combats (guild_id, channel_id, combat_type, status, turn_index, log_json)
               VALUES (?,?,?,'active',0,?)""",
            (interaction.guild_id, interaction.channel_id, tipo, json.dumps([f"Combate {tipo} iniciado."])),
        )
        combat_id = cur.lastrowid
        await db.execute(
            """INSERT INTO combat_participants
               (combat_id, character_id, user_id, team, role, vida, mana, stats_json, effects_json, alive)
               VALUES (?,?,?,?,?,?,?,?,?,1)""",
            (combat_id, a["id"], a["user_id"], 0, tu_rol, sa.get("VIDA", 20), sa.get("MANA", 10), a["stats_json"], "[]"),
        )
        await db.execute(
            """INSERT INTO combat_participants
               (combat_id, character_id, user_id, team, role, vida, mana, stats_json, effects_json, alive)
               VALUES (?,?,?,?,?,?,?,?,?,1)""",
            (combat_id, b["id"], b["user_id"], 1, enemigo_rol, sb.get("VIDA", 20), sb.get("MANA", 10), b["stats_json"], "[]"),
        )
        await db.commit()
    embed = discord.Embed(
        title="⚔️ Combate iniciado",
        description=f"**{a['name']}** vs **{b['name']}**\nTipo: **{tipo}**\nEmpieza: **{a['name']}**",
        color=discord.Color.red(),
    )
    await interaction.response.send_message(embed=embed, view=CombatView(combat_id))

@tree.command(name="combate", description="Estado del combate de tu PJ")
@app_commands.describe(pj="Personaje")
@app_commands.autocomplete(pj=ac_user_chars)
async def combate_cmd(interaction: discord.Interaction, pj: str):
    char_id = int(pj)
    combat_id = await get_active_combat_for_char(char_id)
    if not combat_id:
        await interaction.response.send_message("Ese PJ no está en combate.", ephemeral=True)
        return
    parts = await get_participants(combat_id)
    lines = []
    for p in parts:
        ch = await get_character(p["character_id"])
        status = "✅" if p["alive"] else "💀"
        lines.append(f"{status} **{ch['name'] if ch else p['character_id']}** — ❤️ {p['vida']} | 💧 {p['mana']} | {p['role']}")
    await interaction.response.send_message(
        embed=discord.Embed(title=f"⚔️ Combate #{combat_id}", description="\n".join(lines), color=discord.Color.orange()),
        view=CombatView(combat_id),
    )

@tree.command(name="log", description="Ver log del combate de un PJ")
@app_commands.describe(pj="Personaje")
@app_commands.autocomplete(pj=ac_user_chars)
async def log_cmd(interaction: discord.Interaction, pj: str):
    combat_id = await get_active_combat_for_char(int(pj))
    if not combat_id:
        # último combate? solo activos por ahora
        await interaction.response.send_message("Sin combate activo.", ephemeral=True)
        return
    combat = await get_combat(combat_id)
    log = json.loads(combat.get("log_json") or "[]")
    await interaction.response.send_message(
        "📜 **Log**\n" + ("\n".join(log[-25:]) if log else "Vacío"),
        ephemeral=True,
    )

@tree.command(name="fin-combate", description="Admin: fuerza el fin de un combate")
@admin_only()
@app_commands.describe(combat_id="ID del combate")
async def fin_combate(interaction: discord.Interaction, combat_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("UPDATE combats SET status='ended' WHERE id=?", (combat_id,))
        await db.commit()
    await interaction.response.send_message(f"🛑 Combate `{combat_id}` finalizado por admin.")

# ---- Admin content ----
@tree.command(name="crear-clase", description="Admin: crear clase con stats base")
@admin_only()
@app_commands.describe(
    nombre="Nombre de la clase",
    descripcion="Descripción / lore (opcional)",
    vida="Vida máxima base",
    fuerza="Fuerza base",
    velocidad="Velocidad base",
    resistencia="Resistencia base",
    defensa="Defensa base",
    mana="Maná máximo base",
)
async def crear_clase(
    interaction: discord.Interaction,
    nombre: str,
    descripcion: str = "",
    vida: app_commands.Range[int, 1, 10000] = 20,
    fuerza: app_commands.Range[int, 0, 1000] = 5,
    velocidad: app_commands.Range[int, 0, 1000] = 5,
    resistencia: app_commands.Range[int, 0, 1000] = 5,
    defensa: app_commands.Range[int, 0, 1000] = 3,
    mana: app_commands.Range[int, 0, 10000] = 10,
):
    stats = {
        "VIDA": vida,
        "VIDA_MAX": vida,
        "FUE": fuerza,
        "VEL": velocidad,
        "RES": resistencia,
        "DEF": defensa,
        "MANA": mana,
        "MANA_MAX": mana,
    }
    # Por si también ponen códigos en la descripción
    stats.update(parse_codes(descripcion))
    stats["VIDA"] = stats.get("VIDA_MAX", stats.get("VIDA", vida))
    stats["MANA"] = stats.get("MANA_MAX", stats.get("MANA", mana))
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO classes (guild_id, name, description, stats_json) VALUES (?,?,?,?)
               ON CONFLICT(guild_id, name) DO UPDATE SET description=excluded.description, stats_json=excluded.stats_json""",
            (interaction.guild_id, nombre.strip(), descripcion, json.dumps(stats)),
        )
        await db.commit()
    embed = discord.Embed(title=f"✅ Clase: {nombre}", description=descripcion or "Sin descripción", color=discord.Color.purple())
    embed.add_field(
        name="Stats base",
        value="\n".join(f"**{k}:** {v}" for k, v in stats.items()),
        inline=False,
    )
    embed.set_footer(text="Al crear un PJ con esta clase, recibirá estas stats")
    await interaction.response.send_message(embed=embed)

@tree.command(name="clases", description="Lista de clases")
async def clases_cmd(interaction: discord.Interaction):
    classes = await list_classes(interaction.guild_id)
    await interaction.response.send_message(
        "⚔️ **Clases:** " + (", ".join(classes) if classes else "Ninguna")
    )

@tree.command(name="crear-profesion", description="Admin: crear profesión")
@admin_only()
@app_commands.describe(nombre="Nombre", descripcion="Descripción")
async def crear_profesion(interaction: discord.Interaction, nombre: str, descripcion: str = ""):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO professions (guild_id, name, description) VALUES (?,?,?)
               ON CONFLICT(guild_id, name) DO UPDATE SET description=excluded.description""",
            (interaction.guild_id, nombre.strip(), descripcion),
        )
        await db.commit()
    await interaction.response.send_message(f"✅ Profesión **{nombre}** creada.")

@tree.command(name="profesiones", description="Lista profesiones")
async def profesiones_cmd(interaction: discord.Interaction):
    profs = await list_professions(interaction.guild_id)
    await interaction.response.send_message(
        "🛠️ **Profesiones:** " + (", ".join(profs) if profs else "Ninguna")
    )

@tree.command(name="crear-tienda", description="Admin: crear tienda")
@admin_only()
async def crear_tienda(interaction: discord.Interaction, nombre: str):
    if await get_shop_by_name(interaction.guild_id, nombre):
        await interaction.response.send_message("Ya existe.", ephemeral=True)
        return
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO shops (guild_id, name) VALUES (?,?)", (interaction.guild_id, nombre.strip()))
        await db.commit()
    await interaction.response.send_message(f"✅ Tienda **{nombre}** creada.")

@tree.command(name="borrar-tienda", description="Admin: borrar tienda")
@admin_only()
@app_commands.describe(tienda="Tienda")
@app_commands.autocomplete(tienda=ac_shops)
async def borrar_tienda(interaction: discord.Interaction, tienda: str):
    shop = await get_shop_by_name(interaction.guild_id, tienda)
    if not shop:
        await interaction.response.send_message("No existe.", ephemeral=True)
        return
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("DELETE FROM shop_items WHERE shop_id=?", (shop[0],))
        await db.execute("DELETE FROM shops WHERE id=?", (shop[0],))
        await db.commit()
    await interaction.response.send_message(f"🗑️ Tienda **{tienda}** eliminada.")

@tree.command(name="item-tienda", description="Admin: añadir ítem a tienda")
@admin_only()
@app_commands.describe(
    tienda="Tienda", item="Nombre", precio="Precio", tipo="Tipo",
    descripcion="Descripción con códigos $DAÑO=5 $DUR=10 etc.",
    imagen="URL imagen",
)
@app_commands.autocomplete(tienda=ac_shops)
async def item_tienda_cmd(
    interaction: discord.Interaction,
    tienda: str,
    item: str,
    precio: app_commands.Range[int, 1, 1_000_000_000],
    tipo: str = "Arma",
    descripcion: str = "",
    imagen: str = "",
):
    shop = await get_shop_by_name(interaction.guild_id, tienda)
    if not shop:
        await interaction.response.send_message("Tienda no existe.", ephemeral=True)
        return
    data = parse_codes(descripcion)
    if "DUR" in data and "DUR_MAX" not in data:
        data["DUR_MAX"] = data["DUR"]
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO shop_items (shop_id, item_name, price, item_type, description, image_url, data_json)
               VALUES (?,?,?,?,?,?,?)""",
            (shop[0], item, precio, tipo, descripcion, imagen, json.dumps(data)),
        )
        await db.commit()
    embed = discord.Embed(title="✅ Ítem añadido", description=f"**{item}** en **{tienda}**", color=discord.Color.orange())
    embed.add_field(name="Precio", value=f"{precio:,}", inline=True)
    embed.add_field(name="Tipo", value=tipo, inline=True)
    if data:
        embed.add_field(name="Stats", value=str(data), inline=False)
    if imagen:
        embed.set_thumbnail(url=imagen)
    await interaction.response.send_message(embed=embed)

@tree.command(name="quitar-item", description="Admin: quitar ítem de tienda")
@admin_only()
@app_commands.describe(tienda="Tienda", item="Ítem")
@app_commands.autocomplete(tienda=ac_shops, item=ac_shop_items)
async def quitar_item_cmd(interaction: discord.Interaction, tienda: str, item: str):
    shop = await get_shop_by_name(interaction.guild_id, tienda)
    if not shop:
        await interaction.response.send_message("No existe.", ephemeral=True)
        return
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("DELETE FROM shop_items WHERE shop_id=? AND item_name=?", (shop[0], item))
        await db.commit()
    await interaction.response.send_message(f"🗑️ **{item}** eliminado de **{tienda}**.")

@tree.command(name="crear-material", description="Admin: crear material de crafteo")
@admin_only()
@app_commands.describe(nombre="Nombre", descripcion="Descripción", imagen="URL")
async def crear_material(interaction: discord.Interaction, nombre: str, descripcion: str = "", imagen: str = ""):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO materials (guild_id, name, description, image_url) VALUES (?,?,?,?)
               ON CONFLICT(guild_id, name) DO UPDATE SET description=excluded.description, image_url=excluded.image_url""",
            (interaction.guild_id, nombre.strip(), descripcion, imagen),
        )
        await db.commit()
    await interaction.response.send_message(f"✅ Material **{nombre}** creado. Añádelo a tiendas o inv con admin `/craftear`.")

@tree.command(name="crear-receta", description="Admin: crear receta de crafteo")
@admin_only()
@app_commands.describe(
    nombre="Nombre de la receta",
    profesion="Profesión requerida",
    grado="Comun/Refinado/Superior/Magistral",
    resultado="Nombre del ítem resultante",
    tipo_resultado="Arma/Armadura/Artefacto/Poción...",
    materiales="Ej: Hierro:2,Madera:1",
    stats_resultado="Códigos $DAÑO=5 $DUR=10",
    con_magia="¿Requiere material mágico?",
    material_magico="Nombre del material de afinidad",
    imagen="URL",
)
@app_commands.autocomplete(profesion=ac_professions)
@app_commands.choices(grado=[
    app_commands.Choice(name="Comun", value="Comun"),
    app_commands.Choice(name="Refinado", value="Refinado"),
    app_commands.Choice(name="Superior", value="Superior"),
    app_commands.Choice(name="Magistral", value="Magistral"),
])
async def crear_receta(
    interaction: discord.Interaction,
    nombre: str,
    profesion: str,
    grado: str,
    resultado: str,
    tipo_resultado: str = "Arma",
    materiales: str = "",
    stats_resultado: str = "",
    con_magia: bool = False,
    material_magico: str = "",
    imagen: str = "",
):
    mats = {}
    for part in materiales.split(","):
        part = part.strip()
        if ":" in part:
            k, v = part.split(":", 1)
            try:
                mats[k.strip()] = int(v.strip())
            except ValueError:
                pass
    data = parse_codes(stats_resultado)
    if "DUR" in data:
        data["DUR_MAX"] = data["DUR"]
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO recipes
               (guild_id, name, profession, grade, result_name, result_type, result_data, materials_json, needs_magic, magic_material, image_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                interaction.guild_id, nombre, profesion, grado, resultado, tipo_resultado,
                json.dumps(data), json.dumps(mats), 1 if con_magia else 0, material_magico, imagen,
            ),
        )
        await db.commit()
    await interaction.response.send_message(
        f"✅ Receta **{nombre}** creada.\nMateriales: `{mats}`\nStats: `{data}`"
    )

@tree.command(name="craftear", description="Admin: crear ítem directo en inventario de un PJ")
@admin_only()
@app_commands.describe(pj_id="ID del personaje", item="Nombre", tipo="Tipo", cantidad="Cantidad", stats="Códigos $DAÑO=5", imagen="URL")
async def admin_craftear(
    interaction: discord.Interaction,
    pj_id: int,
    item: str,
    tipo: str = "Arma",
    cantidad: app_commands.Range[int, 1, 1000] = 1,
    stats: str = "",
    imagen: str = "",
):
    ch = await get_character(pj_id)
    if not ch:
        await interaction.response.send_message("PJ no existe.", ephemeral=True)
        return
    data = parse_codes(stats)
    if "DUR" in data:
        data["DUR_MAX"] = data["DUR"]
    await add_item(pj_id, item, cantidad, tipo, data, imagen, stats)
    await interaction.response.send_message(f"✅ Admin dio **{item}** x{cantidad} a **{ch['name']}**.")

@tree.command(name="reparar", description="Admin: reparar arma de un PJ (restaura DUR)")
@admin_only()
@app_commands.describe(pj_id="ID del PJ", arma="Nombre del arma")
async def admin_reparar(interaction: discord.Interaction, pj_id: int, arma: str):
    it = await get_item(pj_id, arma)
    if not it:
        await interaction.response.send_message("Ítem no encontrado.", ephemeral=True)
        return
    data = json.loads(it[3] or "{}")
    if "DUR_MAX" in data:
        data["DUR"] = data["DUR_MAX"]
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "UPDATE inventory SET data_json=? WHERE character_id=? AND item_name=?",
            (json.dumps(data), pj_id, arma),
        )
        await db.commit()
    await interaction.response.send_message(f"🔧 **{arma}** reparada (admin).")

@tree.command(name="dar-xp", description="Admin: dar XP a un PJ")
@admin_only()
@app_commands.describe(pj_id="ID del PJ", cantidad="XP")
async def dar_xp_cmd(interaction: discord.Interaction, pj_id: int, cantidad: app_commands.Range[int, 1, 10_000_000]):
    ch = await get_character(pj_id)
    if not ch:
        await interaction.response.send_message("PJ no existe.", ephemeral=True)
        return
    await grant_xp(pj_id, cantidad, interaction.channel)
    ch2 = await get_character(pj_id)
    await interaction.response.send_message(f"✅ +{cantidad} XP a **{ch['name']}** → Nv.{ch2['level']} ({ch2['xp']} XP)")

@tree.command(name="quitar-xp", description="Admin: quitar XP a un PJ")
@admin_only()
@app_commands.describe(pj_id="ID del PJ", cantidad="XP")
async def quitar_xp_cmd(interaction: discord.Interaction, pj_id: int, cantidad: app_commands.Range[int, 1, 10_000_000]):
    ch = await get_character(pj_id)
    if not ch:
        await interaction.response.send_message("PJ no existe.", ephemeral=True)
        return
    new_xp = max(0, ch["xp"] - cantidad)
    new_level = calculate_level(new_xp)
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("UPDATE characters SET xp=?, level=? WHERE id=?", (new_xp, new_level, pj_id))
        await db.commit()
    await interaction.response.send_message(f"✅ XP actualizado: **{new_xp}** (Nv.{new_level})")

@tree.command(name="ver-xp", description="Ver XP de un PJ")
@app_commands.describe(pj="Personaje")
@app_commands.autocomplete(pj=ac_user_chars)
async def ver_xp_cmd(interaction: discord.Interaction, pj: str):
    ch = await get_character(int(pj))
    if not ch:
        await interaction.response.send_message("No encontrado.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"📊 **{ch['name']}** — Nivel **{ch['level']}** | XP **{ch['xp']}** | Rango **{rank_from_level(ch['level'])}**"
    )

@tree.command(name="admin-dar-dinero", description="Admin: dar dinero a un PJ")
@admin_only()
@app_commands.describe(pj_id="ID del PJ", cantidad="Monedas")
async def admin_dar_dinero(interaction: discord.Interaction, pj_id: int, cantidad: app_commands.Range[int, 1, 1_000_000_000]):
    ch = await get_character(pj_id)
    if not ch:
        await interaction.response.send_message("PJ no existe.", ephemeral=True)
        return
    new = await add_money(pj_id, cantidad)
    await interaction.response.send_message(f"💰 **{ch['name']}** ahora tiene **{new:,}**.")

@tree.command(name="quitar-dinero", description="Admin: quitar dinero a un PJ")
@admin_only()
@app_commands.describe(pj_id="ID del PJ", cantidad="Monedas")
async def admin_quitar_dinero(interaction: discord.Interaction, pj_id: int, cantidad: app_commands.Range[int, 1, 1_000_000_000]):
    ch = await get_character(pj_id)
    if not ch:
        await interaction.response.send_message("PJ no existe.", ephemeral=True)
        return
    new = await add_money(pj_id, -cantidad)
    await interaction.response.send_message(f"💰 **{ch['name']}** ahora tiene **{new:,}**.")

@tree.command(name="config-nivel", description="Admin: nivel máximo del server")
@admin_only()
@app_commands.describe(maximo="Nivel máximo (ej. 50)")
async def config_nivel(interaction: discord.Interaction, maximo: app_commands.Range[int, 1, 500]):
    await set_guild_field(interaction.guild_id, "max_level", maximo)
    await interaction.response.send_message(f"✅ Nivel máximo: **{maximo}**")

@tree.command(name="reward-nivel", description="Admin: recompensas/stats al subir de nivel")
@admin_only()
@app_commands.describe(nivel="Nivel", recompensas="Texto separado por |", stats="Códigos $FUE=1 $VIDA_MAX=2")
async def reward_nivel(interaction: discord.Interaction, nivel: app_commands.Range[int, 1, 500], recompensas: str = "", stats: str = ""):
    rw = [x.strip() for x in recompensas.split("|") if x.strip()]
    st = parse_codes(stats)
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            """INSERT INTO level_rewards (guild_id, level, rewards, stats_json) VALUES (?,?,?,?)
               ON CONFLICT(guild_id, level) DO UPDATE SET rewards=excluded.rewards, stats_json=excluded.stats_json""",
            (interaction.guild_id, nivel, json.dumps(rw), json.dumps(st)),
        )
        await db.commit()
    await interaction.response.send_message(f"✅ Nivel {nivel}: rewards={rw} stats={st}")

@tree.command(name="canal-xp", description="Admin: añadir/quitar canal XP (repetir el mismo lo quita)")
@admin_only()
@app_commands.describe(canal="Canal de roleplay para XP")
async def canal_xp(interaction: discord.Interaction, canal: discord.TextChannel):
    cfg = await get_guild_config(interaction.guild_id)
    channels = list(cfg.get("xp_channels") or [])
    if canal.id in channels:
        channels.remove(canal.id)
        await set_guild_field(interaction.guild_id, "xp_channels", json.dumps(channels))
        await set_guild_field(interaction.guild_id, "xp_channel_id", channels[0] if channels else None)
        await interaction.response.send_message(
            f"🗑️ {canal.mention} quitado de XP.\nActivos: {', '.join(f'<#{c}>' for c in channels) or 'ninguno'}"
        )
    else:
        channels.append(canal.id)
        await set_guild_field(interaction.guild_id, "xp_channels", json.dumps(channels))
        await set_guild_field(interaction.guild_id, "xp_channel_id", canal.id)
        await interaction.response.send_message(
            f"✅ {canal.mention} añadido a XP.\nActivos: {', '.join(f'<#{c}>' for c in channels)}\n_(Repetir el mismo canal lo quita.)_"
        )

@tree.command(name="categoria-xp", description="Admin: añadir/quitar categoría XP (repetir la misma la quita)")
@admin_only()
@app_commands.describe(categoria="Categoría donde se gana XP con tuppers")
async def categoria_xp(interaction: discord.Interaction, categoria: discord.CategoryChannel):
    cfg = await get_guild_config(interaction.guild_id)
    cats = list(cfg.get("xp_categories") or [])
    if categoria.id in cats:
        cats.remove(categoria.id)
        await set_guild_field(interaction.guild_id, "xp_categories", json.dumps(cats))
        await interaction.response.send_message(f"🗑️ Categoría **{categoria.name}** quitada de XP.")
    else:
        cats.append(categoria.id)
        await set_guild_field(interaction.guild_id, "xp_categories", json.dumps(cats))
        await interaction.response.send_message(
            f"✅ Categoría **{categoria.name}** añadida a XP.\n_(Repetir la misma la quita.)_"
        )

@tree.command(name="vincular-tupper", description="Vincula un tupper de Tupperbox a tu PJ")
@app_commands.describe(pj="Tu personaje", nombre_tupper="Nombre exacto del tupper")
@app_commands.autocomplete(pj=ac_user_chars)
async def vincular_tupper(interaction: discord.Interaction, pj: str, nombre_tupper: str):
    ch = await get_character(int(pj))
    if not ch or ch["user_id"] != interaction.user.id:
        await interaction.response.send_message("No es tu PJ.", ephemeral=True)
        return
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "UPDATE characters SET tupper_name=? WHERE id=?",
            (nombre_tupper.strip(), ch["id"]),
        )
        await db.commit()
    await interaction.response.send_message(
        f"🔗 **{ch['name']}** ↔ tupper **{nombre_tupper.strip()}**\n"
        f"XP solo cuando escribas con ese tupper en canal/categoría XP."
    )

@tree.command(name="desvincular-tupper", description="Quita el tupper vinculado de un PJ")
@app_commands.describe(pj="Tu personaje")
@app_commands.autocomplete(pj=ac_user_chars)
async def desvincular_tupper(interaction: discord.Interaction, pj: str):
    ch = await get_character(int(pj))
    if not ch or ch["user_id"] != interaction.user.id:
        await interaction.response.send_message("No es tu PJ.", ephemeral=True)
        return
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("UPDATE characters SET tupper_name='' WHERE id=?", (ch["id"],))
        await db.commit()
    await interaction.response.send_message(f"✅ Tupper desvinculado de **{ch['name']}**.")

@tree.command(name="roles-admin", description="Admin: roles que pueden usar comandos admin")
@admin_only()
async def roles_admin(interaction: discord.Interaction, rol1: discord.Role, rol2: discord.Role = None, rol3: discord.Role = None):
    roles = [rol1.id]
    for r in (rol2, rol3):
        if r and r.id not in roles:
            roles.append(r.id)
    await set_guild_field(interaction.guild_id, "admin_roles", json.dumps(roles))
    await interaction.response.send_message("✅ Roles admin actualizados.")

@tree.command(name="config", description="Admin: ver configuración")
@admin_only()
async def config_cmd(interaction: discord.Interaction):
    cfg = await get_guild_config(interaction.guild_id)
    classes = await list_classes(interaction.guild_id)
    profs = await list_professions(interaction.guild_id)
    embed = discord.Embed(title="⚙️ Configuración", color=discord.Color.blue())
    embed.add_field(name="Nivel máx.", value=str(cfg["max_level"]), inline=True)
    chs = cfg.get("xp_channels") or []
    cats = cfg.get("xp_categories") or []
    embed.add_field(name="Canales XP", value=", ".join(f"<#{c}>" for c in chs) or "—", inline=False)
    embed.add_field(name="Categorías XP", value=", ".join(f"`{c}`" for c in cats) or "—", inline=False)
    embed.add_field(name="Clases", value=", ".join(classes) or "—", inline=False)
    embed.add_field(name="Profesiones", value=", ".join(profs) or "—", inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="embed", description="Admin: crear un embed en este mismo canal")
@admin_only()
@app_commands.describe(titulo="Título", descripcion="Descripción", color="Hex", imagen="URL", thumbnail="URL mini")
async def embed_cmd(
    interaction: discord.Interaction,
    titulo: str = None,
    descripcion: str = None,
    color: str = "5865F2",
    imagen: str = None,
    thumbnail: str = None,
):
    try:
        color_val = int(color.replace("#", ""), 16)
    except Exception:
        color_val = 0x5865F2
    emb = discord.Embed(color=color_val)
    if titulo:
        emb.title = titulo
    if descripcion:
        emb.description = descripcion
    if imagen:
        emb.set_image(url=imagen)
    if thumbnail:
        emb.set_thumbnail(url=thumbnail)
    await interaction.channel.send(embed=emb)
    await interaction.response.send_message("✅ Embed enviado aquí.", ephemeral=True)

# ==================== MAIN ====================
if __name__ == "__main__":
    if not TOKEN:
        print("❌ Falta DISCORD_TOKEN en variables de entorno")
    else:
        bot.run(TOKEN)

