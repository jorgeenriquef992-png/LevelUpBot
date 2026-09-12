import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import random
import os
import json
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

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
cooldowns = {}

async def init_db():
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY,
                levelup_channel_id INTEGER,
                admin_roles TEXT DEFAULT '[]',
                max_level INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ignored_channels (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, channel_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS classes (
                guild_id INTEGER NOT NULL,
                class_name TEXT NOT NULL,
                PRIMARY KEY (guild_id, class_name)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS class_rewards (
                guild_id INTEGER NOT NULL,
                class_name TEXT NOT NULL,
                level INTEGER NOT NULL,
                rewards TEXT NOT NULL,
                PRIMARY KEY (guild_id, class_name, level)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_class (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                class_name TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        await db.commit()

async def get_user_data(guild_id: int, user_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT xp, level FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"xp": row[0], "level": row[1]}
            return {"xp": 0, "level": 0}

async def set_user_xp(guild_id: int, user_id: int, xp: int):
    level = calculate_level(xp)
    config = await get_guild_config(guild_id)
    max_level = config.get("max_level", 0)
    if max_level > 0 and level > max_level:
        level = max_level
        xp = xp_for_level(max_level)
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            INSERT INTO users (guild_id, user_id, xp, level)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                xp = excluded.xp,
                level = excluded.level
        """, (guild_id, user_id, xp, level))
        await db.commit()
    return level

async def add_xp(guild_id: int, user_id: int, amount: int):
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
    leveled_up = new_level > old_level
    return new_xp, new_level, leveled_up

async def get_guild_config(guild_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT levelup_channel_id, admin_roles, max_level FROM guild_config WHERE guild_id = ?",
            (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                admin_roles = json.loads(row[1]) if row[1] else []
                return {
                    "levelup_channel_id": row[0],
                    "admin_roles": admin_roles,
                    "max_level": row[2] or 0
                }
            return {"levelup_channel_id": None, "admin_roles": [], "max_level": 0}

async def set_levelup_channel(guild_id: int, channel_id):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            INSERT INTO guild_config (guild_id, levelup_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET levelup_channel_id = excluded.levelup_channel_id
        """, (guild_id, channel_id))
        await db.commit()

async def set_admin_roles(guild_id: int, role_ids: list):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            INSERT INTO guild_config (guild_id, admin_roles)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET admin_roles = excluded.admin_roles
        """, (guild_id, json.dumps(role_ids)))
        await db.commit()

async def set_max_level(guild_id: int, max_level: int):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            INSERT INTO guild_config (guild_id, max_level)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET max_level = excluded.max_level
        """, (guild_id, max_level))
        await db.commit()

async def is_channel_ignored(guild_id: int, channel_id: int) -> bool:
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT 1 FROM ignored_channels WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id)
        ) as cursor:
            return await cursor.fetchone() is not None

async def add_ignored_channel(guild_id: int, channel_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO ignored_channels (guild_id, channel_id) VALUES (?, ?)",
            (guild_id, channel_id)
        )
        await db.commit()

async def remove_ignored_channel(guild_id: int, channel_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "DELETE FROM ignored_channels WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id)
        )
        await db.commit()

async def get_ignored_channels(guild_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT channel_id FROM ignored_channels WHERE guild_id = ?",
            (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def add_class(guild_id: int, class_name: str) -> bool:
    async with aiosqlite.connect(DATABASE) as db:
        try:
            await db.execute(
                "INSERT INTO classes (guild_id, class_name) VALUES (?, ?)",
                (guild_id, class_name)
            )
            await db.commit()
            return True
        except:
            return False

async def remove_class(guild_id: int, class_name: str):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("DELETE FROM classes WHERE guild_id = ? AND class_name = ?", (guild_id, class_name))
        await db.execute("DELETE FROM class_rewards WHERE guild_id = ? AND class_name = ?", (guild_id, class_name))
        await db.execute("DELETE FROM user_class WHERE guild_id = ? AND class_name = ?", (guild_id, class_name))
        await db.commit()

async def get_classes(guild_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT class_name FROM classes WHERE guild_id = ? ORDER BY class_name",
            (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def class_exists(guild_id: int, class_name: str) -> bool:
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT 1 FROM classes WHERE guild_id = ? AND class_name = ?",
            (guild_id, class_name)
        ) as cursor:
            return await cursor.fetchone() is not None

async def set_rewards(guild_id: int, class_name: str, level: int, rewards: list):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            INSERT INTO class_rewards (guild_id, class_name, level, rewards)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, class_name, level) DO UPDATE SET rewards = excluded.rewards
        """, (guild_id, class_name, level, json.dumps(rewards)))
        await db.commit()

async def get_rewards(guild_id: int, class_name: str, level: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT rewards FROM class_rewards WHERE guild_id = ? AND class_name = ? AND level = ?",
            (guild_id, class_name, level)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return json.loads(row[0])
            return []

async def get_all_rewards(guild_id: int, class_name: str):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT level, rewards FROM class_rewards WHERE guild_id = ? AND class_name = ? ORDER BY level",
            (guild_id, class_name)
        ) as cursor:
            rows = await cursor.fetchall()
            return {row[0]: json.loads(row[1]) for row in rows}

async def delete_rewards(guild_id: int, class_name: str, level: int):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "DELETE FROM class_rewards WHERE guild_id = ? AND class_name = ? AND level = ?",
            (guild_id, class_name, level)
        )
        await db.commit()

async def get_user_class(guild_id: int, user_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(
            "SELECT class_name FROM user_class WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def set_user_class(guild_id: int, user_id: int, class_name: str):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("""
            INSERT INTO user_class (guild_id, user_id, class_name)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET class_name = excluded.class_name
        """, (guild_id, user_id, class_name))
        await db.commit()

async def remove_user_class(guild_id: int, user_id: int):
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute(
            "DELETE FROM user_class WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        await db.commit()

async def has_admin_permission(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    config = await get_guild_config(interaction.guild_id)
    admin_roles = config["admin_roles"]
    if not admin_roles:
        return interaction.user.guild_permissions.administrator
    user_role_ids = [role.id for role in interaction.user.roles]
    return any(role_id in user_role_ids for role_id in admin_roles)

def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not await has_admin_permission(interaction):
            await interaction.response.send_message("❌ No tienes permiso para usar este comando.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

@bot.event
async def on_ready():
    await init_db()
    try:
        synced = await tree.sync()
        print(f"✅ Bot conectado como {bot.user}")
        print(f"✅ Sincronizados {len(synced)} comandos")
    except Exception as e:
        print(f"Error: {e}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
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
    max_level = config.get("max_level", 0)
    data = await get_user_data(message.guild.id, message.author.id)
    if max_level > 0 and data["level"] >= max_level:
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
        rewards_text = "\n".join(f"• {r}" for r in rewards)
        embed.add_field(name=f"🎁 Has desbloqueado en el nivel {level}:", value=rewards_text, inline=False)
    embed.set_footer(text="Level Up • Creado por 《JEFP25》")
    try:
        await channel.send(content=member.mention, embed=embed)
    except:
        pass

@tree.command(name="rank", description="Muestra tu nivel y XP")
async def rank(interaction: discord.Interaction, usuario: discord.Member = None):
    target = usuario or interaction.user
    data = await get_user_data(interaction.guild_id, target.id)
    user_class = await get_user_class(interaction.guild_id, target.id)
    current_level = data["level"]
    current_xp = data["xp"]
    next_level_xp = xp_for_level(current_level + 1)
    xp_needed = max(0, next_level_xp - current_xp)
    progress = 0
    prev_xp = xp_for_level(current_level)
    if next_level_xp > prev_xp:
        progress = (current_xp - prev_xp) / (next_level_xp - prev_xp)
    progress = max(0, min(1, progress))
    bar = "█" * int(12 * progress) + "░" * (12 - int(12 * progress))
    embed = discord.Embed(title=f"📊 Rank de {target.display_name}", color=discord.Color.blurple())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Nivel", value=f"**{current_level}**", inline=True)
    embed.add_field(name="XP Total", value=f"**{current_xp:,}**", inline=True)
    embed.add_field(name="Siguiente nivel", value=f"**{xp_needed:,}** XP", inline=True)
    embed.add_field(name="Clase", value=user_class if user_class else "❌ Sin clase", inline=True)
    embed.add_field(name="Progreso", value=f"`{bar}` {int(progress*100)}%", inline=False)
    embed.set_footer(text="Creado por 《JEFP25》")
    await interaction.response.send_message(embed=embed)

@tree.command(name="leaderboard", description="Top 10 del servidor")
async def leaderboard(interaction: discord.Interaction):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT user_id, xp, level FROM users WHERE guild_id = ? ORDER BY xp DESC LIMIT 10", (interaction.guild_id,)) as cursor:
            rows = await cursor.fetchall()
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
        clase = f" ({user_class})" if user_class else ""
        description += f"{medal} {name}{clase} — Nivel **{level}** ({xp:,} XP)\n"
    embed = discord.Embed(title="🏆 Leaderboard", description=description, color=discord.Color.gold())
    embed.set_footer(text="Creado por 《JEFP25》")
    await interaction.response.send_message(embed=embed)

@tree.command(name="elegir-clase", description="Elige tu clase (solo una vez)")
async def elegir_clase(interaction: discord.Interaction, clase: str):
    current = await get_user_class(interaction.guild_id, interaction.user.id)
    if current:
        await interaction.response.send_message(f"❌ Ya tienes la clase **{current}**.", ephemeral=True)
        return
    if not await class_exists(interaction.guild_id, clase):
        clases = await get_classes(interaction.guild_id)
        lista = ", ".join(f"`{c}`" for c in clases) if clases else "Ninguna"
        await interaction.response.send_message(f"❌ La clase no existe. Disponibles: {lista}", ephemeral=True)
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
    if clase is None:
        clase = await get_user_class(interaction.guild_id, interaction.user.id)
        if not clase:
            await interaction.response.send_message("❌ Indica una clase o elige una primero.", ephemeral=True)
            return
    if not await class_exists(interaction.guild_id, clase):
        await interaction.response.send_message("❌ Esa clase no existe.", ephemeral=True)
        return
    rewards = await get_all_rewards(interaction.guild_id, clase)
    if not rewards:
        await interaction.response.send_message(f"La clase **{clase}** no tiene recompensas.")
        return
    description = ""
    for level in sorted(rewards.keys()):
        items = "\n".join(f"  • {r}" for r in rewards[level])
        description += f"**Nivel {level}:**\n{items}\n\n"
    embed = discord.Embed(title=f"📜 Recompensas de {clase}", description=description, color=discord.Color.purple())
    embed.set_footer(text="Creado por 《JEFP25》")
    await interaction.response.send_message(embed=embed)

@tree.command(name="help", description="Lista de comandos")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="📖 Level Up - Comandos", color=discord.Color.blue())
    embed.add_field(name="👤 Usuario", value="`/rank` `/leaderboard` `/elegir-clase` `/mi-clase` `/ver-lista` `/help`", inline=False)
    embed.add_field(name="🛡️ Admin", value="`/dar-xp` `/quitar-xp` `/ver-xp` `/dar-xp-rol` `/quitar-xp-rol` `/resetear-xp` `/resetear-xp-rol` `/añadir-clase` `/borrar-clase` `/añadir-recompensa` `/borrar-recompensa` `/resetear-clase` `/set-nivel-maximo`", inline=False)
    embed.add_field(name="⚙️ Config", value="`/config-canal-levelup` `/config-desactivar-levelup` `/config-ignorar-canal` `/config-permitir-canal` `/config-canales-ignorados` `/config-roles-admin` `/config-ver`", inline=False)
    embed.add_field(name="🔗 Enlaces", value="[Invitar](https://www.youtube.com/watch?v=dQw4w9WgXcQ) • [Soporte](https://www.youtube.com/watch?v=dQw4w9WgXcQ)", inline=False)
    embed.set_footer(text="Creado por 《JEFP25》")
    await interaction.response.send_message(embed=embed)

@tree.command(name="dar-xp", description="Da XP a un usuario")
@admin_only()
async def dar_xp(interaction: discord.Interaction, usuario: discord.Member, cantidad: app_commands.Range[int, 1, 1000000]):
    new_xp, new_level, leveled_up = await add_xp(interaction.guild_id, usuario.id, cantidad)
    user_class = await get_user_class(interaction.guild_id, usuario.id)
    msg = f"✅ Se dieron **{cantidad:,} XP** a {usuario.mention}. Ahora: **{new_xp:,} XP** (Nivel {new_level})"
    if leveled_up and user_class:
        await send_levelup_message(interaction.guild, usuario, new_level, user_class)
    await interaction.response.send_message(msg)

@tree.command(name="quitar-xp", description="Quita XP a un usuario")
@admin_only()
async def quitar_xp(interaction: discord.Interaction, usuario: discord.Member, cantidad: app_commands.Range[int, 1, 1000000]):
    new_xp, new_level, _ = await add_xp(interaction.guild_id, usuario.id, -cantidad)
    await interaction.response.send_message(f"✅ Se quitaron **{cantidad:,} XP** a {usuario.mention}. Ahora: **{new_xp:,} XP** (Nivel {new_level})")

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
    for member in members:
        await add_xp(interaction.guild_id, member.id, cantidad)
    await interaction.followup.send(f"✅ Se dieron **{cantidad:,} XP** a **{len(members)}** miembros de {rol.mention}.")

@tree.command(name="quitar-xp-rol", description="Quita XP a un rol")
@admin_only()
async def quitar_xp_rol(interaction: discord.Interaction, rol: discord.Role, cantidad: app_commands.Range[int, 1, 100000]):
    await interaction.response.defer()
    members = [m for m in rol.members if not m.bot]
    for member in members:
        await add_xp(interaction.guild_id, member.id, -cantidad)
    await interaction.followup.send(f"✅ Se quitaron **{cantidad:,} XP** a **{len(members)}** miembros de {rol.mention}.")

@tree.command(name="resetear-xp", description="Resetea XP de un usuario")
@admin_only()
async def resetear_xp(interaction: discord.Interaction, usuario: discord.Member):
    await set_user_xp(interaction.guild_id, usuario.id, 0)
    await interaction.response.send_message(f"✅ XP de {usuario.mention} reseteado a 0.")

@tree.command(name="resetear-xp-rol", description="Resetea XP de un rol")
@admin_only()
async def resetear_xp_rol(interaction: discord.Interaction, rol: discord.Role):
    await interaction.response.defer()
    members = [m for m in rol.members if not m.bot]
    for member in members:
        await set_user_xp(interaction.guild_id, member.id, 0)
    await interaction.followup.send(f"✅ XP reseteado a **{len(members)}** miembros de {rol.mention}.")

@tree.command(name="añadir-clase", description="Crea una clase")
@admin_only()
async def añadir_clase(interaction: discord.Interaction, nombre: str):
    if await add_class(interaction.guild_id, nombre.strip()):
        await interaction.response.send_message(f"✅ Clase **{nombre}** creada.")
    else:
        await interaction.response.send_message("❌ Esa clase ya existe.", ephemeral=True)

@tree.command(name="borrar-clase", description="Borra una clase")
@admin_only()
async def borrar_clase(interaction: discord.Interaction, nombre: str):
    if not await class_exists(interaction.guild_id, nombre):
        await interaction.response.send_message("❌ Esa clase no existe.", ephemeral=True)
        return
    await remove_class(interaction.guild_id, nombre)
    await interaction.response.send_message(f"✅ Clase **{nombre}** eliminada.")

@tree.command(name="añadir-recompensa", description="Añade recompensas a un nivel")
@admin_only()
async def añadir_recompensa(interaction: discord.Interaction, clase: str, nivel: app_commands.Range[int, 1, 500], recompensas: str):
    if not await class_exists(interaction.guild_id, clase):
        await interaction.response.send_message("❌ Esa clase no existe.", ephemeral=True)
        return
    items = [r.strip() for r in recompensas.split("|") if r.strip()]
    await set_rewards(interaction.guild_id, clase, nivel, items)
    lista = "\n".join(f"• {i}" for i in items)
    await interaction.response.send_message(f"✅ Recompensas añadidas a **{clase}** nivel **{nivel}**:\n{lista}")

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
        await interaction.response.send_message("Ese usuario no tiene clase.", ephemeral=True)
        return
    await remove_user_class(interaction.guild_id, usuario.id)
    await interaction.response.send_message(f"✅ Se quitó la clase **{current}** a {usuario.mention}.")

@tree.command(name="set-nivel-maximo", description="Pone el nivel máximo (0 = sin límite)")
@admin_only()
async def set_nivel_maximo(interaction: discord.Interaction, nivel: app_commands.Range[int, 0, 500]):
    await set_max_level(interaction.guild_id, nivel)
    msg = "✅ Límite eliminado." if nivel == 0 else f"✅ Nivel máximo: **{nivel}**"
    await interaction.response.send_message(msg)

@tree.command(name="config-canal-levelup", description="Canal de mensajes de level up")
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

@tree.command(name="config-roles-admin", description="Roles que pueden administrar")
@admin_only()
async def config_roles_admin(interaction: discord.Interaction, rol1: discord.Role, rol2: discord.Role = None, rol3: discord.Role = None):
    roles = [rol1]
    for r in [rol2, rol3]:
        if r and r not in roles:
            roles.append(r)
    await set_admin_roles(interaction.guild_id, [r.id for r in roles])
    await interaction.response.send_message("✅ Roles de admin actualizados: " + " ".join(r.mention for r in roles))

@tree.command(name="config-ver", description="Ver configuración")
@admin_only()
async def config_ver(interaction: discord.Interaction):
    config = await get_guild_config(interaction.guild_id)
    classes = await get_classes(interaction.guild_id)
    embed = discord.Embed(title="⚙️ Configuración", color=discord.Color.blue())
    embed.add_field(name="Nivel máximo", value=str(config["max_level"]) if config["max_level"] else "Sin límite", inline=True)
    embed.add_field(name="Clases", value=", ".join(classes) if classes else "Ninguna", inline=False)
    embed.set_footer(text="Creado por 《JEFP25》")
    await interaction.response.send_message(embed=embed)

if __name__ == "__main__":
    if not TOKEN:
        print("❌ Falta DISCORD_TOKEN")
    else:
        bot.run(TOKEN)
