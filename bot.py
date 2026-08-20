import asyncio
import os
import random
import re
import sqlite3
import uuid
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands


TOKEN = os.getenv("DISCORD_TOKEN")

OWNER_IDS = {
    1215611779774947331,
    1532388783545253999,
    1526960703444090964,
    1399688332912365589,
}

GAME_MINIMUM = 1_000_000
BLACKJACK_MINIMUM = 10_000_000

# ============================================================
# ALLOWED CHANNELS
# ============================================================

GAME_CHANNEL_IDS = {
    1537861744855220234,
    1537861798252904538,
    1537861857703108619,
    1537862014435860590,
}

WITHDRAW_CHANNEL_ID = 1537865968003321916
DEPOSIT_CHANNEL_ID = 1537865883194753114

GAME_COMMANDS = {
    "dice",
    "blackjack",
    "balance",
    "crash",
    "mines",
    "towers",
    "tip",
}


# ============================================================
# DATABASE
# ============================================================

data_dir = Path("/app/data")

if not data_dir.exists():
    data_dir = Path("data")

data_dir.mkdir(parents=True, exist_ok=True)

db = sqlite3.connect(data_dir / "bot.db")
db.row_factory = sqlite3.Row

db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    gems INTEGER NOT NULL DEFAULT 0,
    total_wagered INTEGER NOT NULL DEFAULT 0,
    total_won INTEGER NOT NULL DEFAULT 0,
    total_lost INTEGER NOT NULL DEFAULT 0,
    games_played INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    neutral INTEGER NOT NULL DEFAULT 0,
    gems_added INTEGER NOT NULL DEFAULT 0,
    gems_removed INTEGER NOT NULL DEFAULT 0,
    roblox_username TEXT,
    roblox_id INTEGER,
    pending_roblox_id INTEGER,
    pending_roblox_username TEXT,
    verify_code TEXT
)
""")

for _column, _coltype in (
    ("roblox_username", "TEXT"),
    ("roblox_id", "INTEGER"),
    ("pending_roblox_id", "INTEGER"),
    ("pending_roblox_username", "TEXT"),
    ("verify_code", "TEXT"),
):
    try:
        db.execute(
            f"ALTER TABLE users ADD COLUMN {_column} {_coltype}"
        )
        db.commit()
    except sqlite3.OperationalError:
        pass


db.execute("""
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    ticket_type TEXT NOT NULL,
    amount INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_by INTEGER
)
""")

db.commit()


# ============================================================
# HELPERS
# ============================================================

def ensure_user(user_id: int):
    db.execute(
        """
        INSERT OR IGNORE INTO users(user_id, gems)
        VALUES (?, 0)
        """,
        (user_id,),
    )
    db.commit()


def get_gems(user_id: int) -> int:
    ensure_user(user_id)

    row = db.execute(
        "SELECT gems FROM users WHERE user_id=?",
        (user_id,),
    ).fetchone()

    return int(row["gems"])


def change_gems(user_id: int, amount: int):
    ensure_user(user_id)

    db.execute(
        "UPDATE users SET gems=gems+? WHERE user_id=?",
        (amount, user_id),
    )
    db.commit()


def add_stats(user_id: int, **values):
    ensure_user(user_id)

    allowed = {
        "total_wagered",
        "total_won",
        "total_lost",
        "games_played",
        "wins",
        "losses",
        "neutral",
        "gems_added",
        "gems_removed",
    }

    values = {
        key: int(value)
        for key, value in values.items()
        if key in allowed
    }

    if not values:
        return

    assignments = ", ".join(
        f"{key}={key}+?"
        for key in values
    )

    db.execute(
        f"""
        UPDATE users
        SET {assignments}
        WHERE user_id=?
        """,
        (*values.values(), user_id),
    )
    db.commit()


def get_stats(user_id: int):
    ensure_user(user_id)

    return db.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,),
    ).fetchone()


def format_gems(amount: int) -> str:
    return f"{int(amount):,} 💎"


def parse_amount(value: str) -> int:
    text = (
        str(value)
        .strip()
        .lower()
        .replace(",", "")
        .replace(" ", "")
    )

    multipliers = {
        "k": 1_000,
        "m": 1_000_000,
        "b": 1_000_000_000,
        "t": 1_000_000_000_000,
    }

    try:
        if text[-1] in multipliers:
            return int(
                float(text[:-1]) * multipliers[text[-1]]
            )

        return int(float(text))

    except (ValueError, IndexError):
        raise ValueError(
            "Invalid amount. Examples: `1m`, `25m`, or `1b`."
        )


def check_bet(user_id: int, amount: int, minimum: int):
    if amount < minimum:
        return (
            f"❌ Minimum bet: "
            f"**{format_gems(minimum)}**."
        )

    if amount > get_gems(user_id):
        return (
            f"❌ You only have "
            f"**{format_gems(get_gems(user_id))}**."
        )

    return None


async def is_owner(interaction: discord.Interaction):
    try:
        info = await bot.application_info()

        return (
            interaction.user.id == info.owner.id
            or interaction.user.id in OWNER_IDS
        )

    except Exception:
        return interaction.user.id in OWNER_IDS


TICKET_STAFF_ROLE_NAME = "main mod"


def has_ticket_staff_role(member) -> bool:
    return isinstance(member, discord.Member) and any(
        role.name == TICKET_STAFF_ROLE_NAME for role in member.roles
    )


async def is_ticket_staff(interaction: discord.Interaction) -> bool:
    if await is_owner(interaction):
        return True

    return has_ticket_staff_role(interaction.user)


# ============================================================
# ROBLOX VERIFICATION
# ============================================================

ROBLOX_USERNAME_LOOKUP_URL = (
    "https://users.roblox.com/v1/usernames/users"
)

ROBLOX_USER_INFO_URL = (
    "https://users.roblox.com/v1/users/{user_id}"
)

UNVERIFIED_ALLOWED_COMMANDS = {
    "link",
    "verify",
}


async def roblox_lookup_user(username: str):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                ROBLOX_USERNAME_LOOKUP_URL,
                json={
                    "usernames": [username],
                    "excludeBannedUsers": True,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:

                if response.status != 200:
                    return None

                payload = await response.json()

    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None

    data = payload.get("data") or []

    if not data:
        return None

    return data[0]["id"], data[0]["name"]


async def roblox_get_description(user_id: int):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                ROBLOX_USER_INFO_URL.format(user_id=user_id),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:

                if response.status != 200:
                    return None

                payload = await response.json()

    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None

    return payload.get("description", "")


class GuardedCommandTree(app_commands.CommandTree):

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:

        command = interaction.command

        if command is None:
            return True

        command_name = command.name

        if command_name in UNVERIFIED_ALLOWED_COMMANDS:
            return True

        if interaction.user.id in OWNER_IDS:
            return True

        try:
            info = await bot.application_info()

            if interaction.user.id == info.owner.id:
                return True

        except Exception:
            pass

        row = db.execute(
            "SELECT roblox_username FROM users WHERE user_id=?",
            (interaction.user.id,),
        ).fetchone()

        if row and row["roblox_username"]:
            return True

        if not interaction.response.is_done():
            await interaction.response.send_message(
                "🔒 You need to verify your Roblox account first.\n"
                "Use `/link` to get started.",
                ephemeral=True,
            )

        return False


# ============================================================
# BOT
# ============================================================

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    tree_cls=GuardedCommandTree,
)


@bot.event
async def on_ready():

    total = 0

    for guild in bot.guilds:

        try:
            bot.tree.copy_global_to(guild=guild)

            synced = await bot.tree.sync(
                guild=guild
            )

            total += len(synced)

        except Exception as error:
            print(
                f"Failed syncing guild "
                f"{guild.id}: {error}"
            )

    print(
        f"Synced {total} slash commands across "
        f"{len(bot.guilds)} server(s)."
    )

    print(f"Logged in as {bot.user}")


# ============================================================
# LINK / VERIFY
# ============================================================

VERIFIED_ROLE_NAME = "Verified"


async def grant_verified_role(
    guild: discord.Guild,
    user_id: int,
):

    if guild is None:
        return

    member = guild.get_member(user_id)

    if member is None:
        return

    role = discord.utils.get(
        guild.roles,
        name=VERIFIED_ROLE_NAME,
    )

    if role is None:

        try:
            role = await guild.create_role(
                name=VERIFIED_ROLE_NAME,
                reason="Roblox verification system",
            )

        except discord.Forbidden:
            return

    try:
        await member.add_roles(
            role,
            reason="Roblox verified",
        )

    except discord.Forbidden:
        pass


@bot.tree.command(
    name="link",
    description="Start Roblox verification. One-time only.",
)
@app_commands.describe(
    roblox_username="Your exact Roblox username",
)
async def link(
    interaction: discord.Interaction,
    roblox_username: str,
):

    ensure_user(interaction.user.id)

    existing = db.execute(
        "SELECT roblox_username FROM users WHERE user_id=?",
        (interaction.user.id,),
    ).fetchone()

    if existing and existing["roblox_username"]:

        return await interaction.response.send_message(
            f"❌ Your account is already linked to "
            f"`{existing['roblox_username']}`.\n"
            "This can only be set once. "
            "Contact an owner if it needs to be corrected.",
            ephemeral=True,
        )

    await interaction.response.defer(
        ephemeral=True
    )

    lookup = await roblox_lookup_user(
        roblox_username
    )

    if lookup is None:

        return await interaction.followup.send(
            "❌ That Roblox username does not exist. "
            "Double-check the spelling and try again.",
            ephemeral=True,
        )

    roblox_id, exact_username = lookup

    code = f"PS99-{random.randint(100000, 999999)}"

    db.execute(
        """
        UPDATE users
        SET pending_roblox_id=?,
            pending_roblox_username=?,
            verify_code=?
        WHERE user_id=?
        """,
        (
            roblox_id,
            exact_username,
            code,
            interaction.user.id,
        ),
    )

    db.commit()

    await interaction.followup.send(
        f"🔎 Found Roblox account `{exact_username}`.\n\n"
        "Add this code to your Roblox profile "
        "**About** section:\n"
        f"`{code}`\n\n"
        "Then run `/verify` here.",
        ephemeral=True,
    )


@bot.tree.command(
    name="verify",
    description="Finish verifying your Roblox account.",
)
async def verify(
    interaction: discord.Interaction,
):

    ensure_user(interaction.user.id)

    row = db.execute(
        """
        SELECT roblox_username,
               pending_roblox_id,
               pending_roblox_username,
               verify_code
        FROM users
        WHERE user_id=?
        """,
        (interaction.user.id,),
    ).fetchone()

    if row["roblox_username"]:

        return await interaction.response.send_message(
            f"❌ Already linked to "
            f"`{row['roblox_username']}`.",
            ephemeral=True,
        )

    if not row["pending_roblox_id"]:

        return await interaction.response.send_message(
            "❌ Run `/link` first.",
            ephemeral=True,
        )

    await interaction.response.defer(
        ephemeral=True
    )

    description = await roblox_get_description(
        row["pending_roblox_id"]
    )

    if (
        description is None
        or not re.search(r"\b" + re.escape(row["verify_code"]) + r"\b", description)
    ):

        return await interaction.followup.send(
            "❌ Code not found on that profile yet.\n"
            f"Add `{row['verify_code']}` to your Roblox "
            "About section, save it, then run `/verify` again.",
            ephemeral=True,
        )

    db.execute(
        """
        UPDATE users
        SET roblox_username=?,
            roblox_id=?,
            pending_roblox_id=NULL,
            pending_roblox_username=NULL,
            verify_code=NULL
        WHERE user_id=?
        """,
        (
            row["pending_roblox_username"],
            row["pending_roblox_id"],
            interaction.user.id,
        ),
    )

    db.commit()

    await grant_verified_role(
        interaction.guild,
        interaction.user.id,
    )

    await interaction.followup.send(
        f"✅ Verified as "
        f"`{row['pending_roblox_username']}`.",
        ephemeral=True,
    )


@bot.tree.command(
    name="forcelink",
    description="Override a member's Roblox username. Owner only.",
)
@app_commands.describe(
    user="Member to update",
    roblox_username="Correct Roblox username",
)
async def forcelink(
    interaction: discord.Interaction,
    user: discord.Member,
    roblox_username: str,
):

    if not await is_owner(interaction):

        return await interaction.response.send_message(
            "❌ Owner only.",
            ephemeral=True,
        )

    ensure_user(user.id)

    db.execute(
        """
        UPDATE users
        SET roblox_username=?,
            pending_roblox_id=NULL,
            pending_roblox_username=NULL,
            verify_code=NULL
        WHERE user_id=?
        """,
        (
            roblox_username,
            user.id,
        ),
    )

    db.commit()

    await grant_verified_role(
        interaction.guild,
        user.id,
    )

    await interaction.response.send_message(
        f"✅ Set **{user.display_name}**'s Roblox "
        f"username to `{roblox_username}`.",
        ephemeral=True,
    )


# ============================================================
# BALANCE
# ============================================================

@bot.tree.command(
    name="balance",
    description="View your virtual gem balance.",
)
async def balance(
    interaction: discord.Interaction,
):

    row = get_stats(
        interaction.user.id
    )

    await interaction.response.send_message(
        f"💎 **Balance:** "
        f"`{format_gems(row['gems'])}`\n"
        f"📥 Added: "
        f"`{format_gems(row['gems_added'])}`\n"
        f"📤 Removed: "
        f"`{format_gems(row['gems_removed'])}`",
        ephemeral=True,
    )


# ============================================================
# TIP
# ============================================================

@bot.tree.command(
    name="tip",
    description="Tip another member virtual gems.",
)
@app_commands.describe(
    user="The member you want to tip",
    amount="Amount such as 1m, 500k, 1b, or 1t",
)
async def tip(
    interaction: discord.Interaction,
    user: discord.Member,
    amount: str,
):

    if user.id == interaction.user.id:
        return await interaction.response.send_message(
            "❌ You cannot tip yourself.", ephemeral=True
        )

    if user.bot:
        return await interaction.response.send_message(
            "❌ You cannot tip a bot.", ephemeral=True
        )

    try:
        number = parse_amount(amount)
    except ValueError as error:
        return await interaction.response.send_message(
            f"❌ {error}", ephemeral=True
        )

    if number <= 0:
        return await interaction.response.send_message(
            "❌ Amount must be greater than zero.", ephemeral=True
        )

    sender_balance = get_gems(interaction.user.id)
    if number > sender_balance:
        return await interaction.response.send_message(
            f"❌ You only have **{format_gems(sender_balance)}**.",
            ephemeral=True,
        )

    ensure_user(user.id)
    change_gems(interaction.user.id, -number)
    change_gems(user.id, number)

    await interaction.response.send_message(
        f"💸 **TIP SENT**\n\n"
        f"From: {interaction.user.mention}\n"
        f"To: {user.mention}\n"
        f"Amount: `{format_gems(number)}`\n"
        f"Your balance: `{format_gems(get_gems(interaction.user.id))}`"
    )


@bot.tree.command(
    name="stats",
    description="View your game statistics.",
)
async def stats_command(
    interaction: discord.Interaction,
):

    row = get_stats(
        interaction.user.id
    )

    games = row["games_played"]

    win_rate = (
        row["wins"] / games * 100
        if games
        else 0
    )

    profit = (
        row["total_won"]
        - row["total_lost"]
    )

    await interaction.response.send_message(
        f"📊 **Statistics**\n\n"
        f"Games: `{games:,}`\n"
        f"Wins: `{row['wins']:,}`\n"
        f"Losses: `{row['losses']:,}`\n"
        f"Neutral: `{row['neutral']:,}`\n"
        f"Wagered: `{format_gems(row['total_wagered'])}`\n"
        f"Won: `{format_gems(row['total_won'])}`\n"
        f"Lost: `{format_gems(row['total_lost'])}`\n"
        f"Net: `{profit:+,} 💎`\n"
        f"Win rate: `{win_rate:.1f}%`",
        ephemeral=True,
    )


# ============================================================
# OWNER GEM COMMANDS
# ============================================================

@bot.tree.command(
    name="addgems",
    description="Add virtual gems. Owner only.",
)
@app_commands.describe(
    user="Member receiving gems",
    amount="Example: 1m, 25m, or 1b",
)
async def addgems(
    interaction: discord.Interaction,
    user: discord.Member,
    amount: str,
):

    if not await is_owner(interaction):

        return await interaction.response.send_message(
            "❌ Owner only.",
            ephemeral=True,
        )

    try:
        number = parse_amount(amount)

    except ValueError as error:

        return await interaction.response.send_message(
            f"❌ {error}",
            ephemeral=True,
        )

    if number <= 0:

        return await interaction.response.send_message(
            "❌ Amount must be greater than zero.",
            ephemeral=True,
        )

    change_gems(
        user.id,
        number,
    )

    add_stats(
        user.id,
        gems_added=number,
    )

    await interaction.response.send_message(
        f"✅ Added `{format_gems(number)}` to "
        f"**{user.display_name}**."
    )


@bot.tree.command(
    name="removegems",
    description="Remove virtual gems. Owner only.",
)
@app_commands.describe(
    user="Member losing gems",
    amount="Example: 1m, 25m, or 1b",
)
async def removegems(
    interaction: discord.Interaction,
    user: discord.Member,
    amount: str,
):

    if not await is_owner(interaction):

        return await interaction.response.send_message(
            "❌ Owner only.",
            ephemeral=True,
        )

    try:
        number = parse_amount(amount)

    except ValueError as error:

        return await interaction.response.send_message(
            f"❌ {error}",
            ephemeral=True,
        )

    if (
        number <= 0
        or number > get_gems(user.id)
    ):

        return await interaction.response.send_message(
            "❌ Invalid amount or insufficient gems.",
            ephemeral=True,
        )

    change_gems(
        user.id,
        -number,
    )

    add_stats(
        user.id,
        gems_removed=number,
    )

    await interaction.response.send_message(
        f"✅ Removed `{format_gems(number)}` from "
        f"**{user.display_name}**."
    )


# ============================================================
# DICE
# ============================================================

@bot.tree.command(
    name="dice",
    description="Play Dice using virtual gems.",
)
@app_commands.describe(
    amount="Minimum 1m",
    number="Choose a number from 1 to 6",
)
async def dice(
    interaction: discord.Interaction,
    amount: str,
    number: int,
):

    try:
        bet = parse_amount(amount)

    except ValueError as error:

        return await interaction.response.send_message(
            f"❌ {error}",
            ephemeral=True,
        )

    if number < 1 or number > 6:

        return await interaction.response.send_message(
            "❌ Choose a number from 1 to 6.",
            ephemeral=True,
        )

    error = check_bet(
        interaction.user.id,
        bet,
        GAME_MINIMUM,
    )

    if error:

        return await interaction.response.send_message(
            error,
            ephemeral=True,
        )

    change_gems(
        interaction.user.id,
        -bet,
    )

    add_stats(
        interaction.user.id,
        total_wagered=bet,
        games_played=1,
    )

    outcome = random.choices(
        ["win", "loss", "neutral"],
        weights=[35, 60, 5],
        k=1,
    )[0]

    if outcome == "win":
        rolled = number
    else:
        rolled = random.choice([n for n in range(1, 7) if n != number])

    if outcome == "neutral":

        change_gems(
            interaction.user.id,
            bet,
        )

        add_stats(
            interaction.user.id,
            neutral=1,
        )

        result = (
            "🟡 Neutral: your bet was returned."
        )

    elif outcome == "win":

        change_gems(
            interaction.user.id,
            bet * 2,
        )

        add_stats(
            interaction.user.id,
            total_won=bet,
            wins=1,
        )

        result = (
            f"🟢 You won!\n"
            f"Rolled: `{rolled}`\n"
            f"Profit: `+{format_gems(bet)}`"
        )

    else:

        add_stats(
            interaction.user.id,
            total_lost=bet,
            losses=1,
        )

        result = (
            f"🔴 You lost.\n"
            f"Rolled: `{rolled}`\n"
            f"Lost: `-{format_gems(bet)}`"
        )

    await interaction.response.send_message(
        f"🎲 **DICE**\n"
        f"Bet: `{format_gems(bet)}`\n"
        f"Chosen: `{number}`\n\n"
        f"{result}\n"
        f"Balance: "
        f"`{format_gems(get_gems(interaction.user.id))}`"
    )


# ============================================================
# MINES
# ============================================================

MINES_HOUSE_EDGE = 0.97


class MinesView(discord.ui.View):

    def __init__(
        self,
        user_id,
        bet,
        num_mines,
    ):

        super().__init__(
            timeout=300
        )

        self.user_id = user_id
        self.bet = bet
        self.num_mines = num_mines
        self.total_tiles = 24
        self.bomb_positions = set(
            random.sample(range(self.total_tiles), num_mines)
        )
        self.opened = 0
        self.finished = False
        self.current_multiplier = 1.0
        self.current_payout = bet

        for index in range(24):

            button = discord.ui.Button(
                label="⬜",
                style=discord.ButtonStyle.secondary,
                row=min(
                    index // 5,
                    4,
                ),
            )

            button.callback = self.tile_callback(
                index,
                button,
            )

            self.add_item(button)

        cashout = discord.ui.Button(
            label="💰 CASH OUT",
            style=discord.ButtonStyle.success,
            row=4,
        )

        cashout.callback = self.cashout

        self.add_item(cashout)

    def tile_callback(
        self,
        index,
        button,
    ):

        async def callback(
            interaction,
        ):

            if interaction.user.id != self.user_id:

                return await interaction.response.send_message(
                    "❌ This is not your game.",
                    ephemeral=True,
                )

            if self.finished:

                return await interaction.response.send_message(
                    "❌ This game has ended.",
                    ephemeral=True,
                )

            if button.disabled:

                return await interaction.response.send_message(
                    "❌ Tile already opened.",
                    ephemeral=True,
                )

            hit_bomb = index in self.bomb_positions

            if hit_bomb:

                self.finished = True

                button.label = "💣"
                button.style = discord.ButtonStyle.danger

                for child in self.children:
                    child.disabled = True

                add_stats(
                    self.user_id,
                    total_lost=self.bet,
                    losses=1,
                )

                return await interaction.response.edit_message(
                    content=(
                        "💥 **BOMB!**\n"
                        f"Lost: `-{format_gems(self.bet)}`\n"
                        f"Balance: "
                        f"`{format_gems(get_gems(self.user_id))}`"
                    ),
                    view=self,
                )

            remaining_tiles = self.total_tiles - self.opened
            self.opened += 1

            button.label = "💎"
            button.style = discord.ButtonStyle.success
            button.disabled = True

            safe_probability = (remaining_tiles - self.num_mines) / remaining_tiles
            self.current_multiplier *= (1 / safe_probability) * MINES_HOUSE_EDGE

            self.current_payout = int(
                self.bet * self.current_multiplier
            )

            if self.opened == self.total_tiles - self.num_mines:

                self.finished = True

                for child in self.children:
                    child.disabled = True

                change_gems(
                    self.user_id,
                    self.current_payout,
                )

                profit = (
                    self.current_payout
                    - self.bet
                )

                add_stats(
                    self.user_id,
                    total_won=max(
                        profit,
                        0,
                    ),
                    wins=1,
                )

                return await interaction.response.edit_message(
                    content=(
                        "🏆 **ALL SAFE!**\n"
                        f"Payout: "
                        f"`{format_gems(self.current_payout)}`\n"
                        f"Profit: `{profit:+,} 💎`\n"
                        f"Balance: "
                        f"`{format_gems(get_gems(self.user_id))}`"
                    ),
                    view=self,
                )

            await interaction.response.edit_message(
                content=(
                    "💣 **MINES**\n"
                    f"Mines: `{self.num_mines}`\n"
                    f"Safe tiles: `{self.opened}`\n"
                    f"Multiplier: "
                    f"`{self.current_multiplier:.2f}x`\n"
                    f"Cash out: "
                    f"`{format_gems(self.current_payout)}`"
                ),
                view=self,
            )

        return callback

    async def cashout(
        self,
        interaction,
    ):

        if interaction.user.id != self.user_id:

            return await interaction.response.send_message(
                "❌ This is not your game.",
                ephemeral=True,
            )

        if self.finished:

            return await interaction.response.send_message(
                "❌ This game has ended.",
                ephemeral=True,
            )

        self.finished = True

        for child in self.children:
            child.disabled = True

        change_gems(
            self.user_id,
            self.current_payout,
        )

        profit = (
            self.current_payout
            - self.bet
        )

        if profit > 0:

            add_stats(
                self.user_id,
                total_won=profit,
                wins=1,
            )

        else:

            add_stats(
                self.user_id,
                neutral=1,
            )

        await interaction.response.edit_message(
            content=(
                "💰 **CASHED OUT!**\n"
                f"Payout: "
                f"`{format_gems(self.current_payout)}`\n"
                f"Profit: `{profit:+,} 💎`\n"
                f"Balance: "
                f"`{format_gems(get_gems(self.user_id))}`"
            ),
            view=self,
        )


@bot.tree.command(
    name="mines",
    description="Play Mines with 24 tiles.",
)
@app_commands.describe(
    amount="Minimum 1m",
    mines="How many mines (1-23)",
)
async def mines(
    interaction: discord.Interaction,
    amount: str,
    mines: app_commands.Range[int, 1, 23],
):

    try:
        bet = parse_amount(amount)

    except ValueError as error:

        return await interaction.response.send_message(
            f"❌ {error}",
            ephemeral=True,
        )

    error = check_bet(
        interaction.user.id,
        bet,
        GAME_MINIMUM,
    )

    if error:

        return await interaction.response.send_message(
            error,
            ephemeral=True,
        )

    num_mines = mines

    change_gems(
        interaction.user.id,
        -bet,
    )

    add_stats(
        interaction.user.id,
        total_wagered=bet,
        games_played=1,
    )

    await interaction.response.send_message(
        "💣 **MINES**\n"
        f"Bet: `{format_gems(bet)}`\n"
        f"Mines: `{num_mines}`\n"
        "Safe tiles show 💎. Bombs show 💣.",
        view=MinesView(
            interaction.user.id,
            bet,
            num_mines,
        ),
    )


# ============================================================
# TOWERS
# ============================================================

TOWERS_ROW_MULTIPLIERS = {
    1: [1.05, 1.10, 1.16, 1.22, 1.28, 1.35, 1.42, 1.50],
    2: [1.10, 1.22, 1.35, 1.50, 1.66, 1.84, 2.05, 2.30],
    3: [1.20, 1.45, 1.75, 2.10, 2.55, 3.10, 3.75, 4.50],
}

TOWERS_COLUMN_LABELS = ["Left", "Middle", "Right"]

TOWERS_ICON_EMPTY = "⬛"
TOWERS_ICON_SAFE = "✅"
TOWERS_ICON_BOMB = "💥"


class TowersView(discord.ui.View):

    def __init__(self, user_id, username, bet, bombs_per_row):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.username = username
        self.bet = bet
        self.bombs_per_row = bombs_per_row
        self.current_row = 0
        self.finished = False
        self.game_id = str(uuid.uuid4())
        # One entry per resolved row: {"col": int, "result": "safe"|"bomb"}
        self.history = []
        # The one safe column (0=Left, 1=Middle, 2=Right) per row, chosen
        # at random ahead of time — each tile has a 33.33% chance of being it.
        self.safe_columns = [random.randint(0, 2) for _ in range(8)]

        self.build_components()

    def get_multiplier(self, row_idx):
        if row_idx < 0:
            return 1.0
        return TOWERS_ROW_MULTIPLIERS[self.bombs_per_row][row_idx]

    def current_payout(self):
        mult = self.get_multiplier(self.current_row - 1) if self.current_row > 0 else 1.0
        return int(self.bet * mult)

    def build_components(self):
        self.clear_items()

        for col, label in enumerate(TOWERS_COLUMN_LABELS):
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                disabled=self.finished,
                row=0,
            )
            btn.callback = self.make_tile_callback(col)
            self.add_item(btn)

        cashout_btn = discord.ui.Button(
            label=f"💰 Cash Out ({self.current_payout():,} gems)",
            style=discord.ButtonStyle.success,
            disabled=self.finished or self.current_row == 0,
            row=1,
        )
        cashout_btn.callback = self.cashout
        self.add_item(cashout_btn)

    def render_grid(self):
        icon_map = {
            "safe": TOWERS_ICON_SAFE,
            "bomb": TOWERS_ICON_BOMB,
        }

        lines = []

        for row in range(8):
            if row < len(self.history):
                entry = self.history[row]
                cells = [TOWERS_ICON_EMPTY] * 3
                cells[entry["col"]] = icon_map[entry["result"]]
                lines.append("".join(cells))
            else:
                lines.append(TOWERS_ICON_EMPTY * 3)

        return "\n".join(lines)

    def earned_amount(self):
        if self.finished and self.history and self.history[-1]["result"] == "bomb":
            return 0
        return self.current_payout() - self.bet

    def build_embed(self):
        embed = discord.Embed(
            title=f"Towers | {self.username}",
            color=discord.Color.purple(),
        )
        embed.add_field(name="Game ID", value=f"`{self.game_id}`", inline=False)
        embed.add_field(name="Bet", value=f"{self.bet:,} gems", inline=True)
        embed.add_field(name="Earned", value=f"{self.earned_amount():,} gems", inline=True)
        embed.description = self.render_grid()
        embed.set_footer(text=f"Row {self.current_row}/8 • Bombs/Row: {self.bombs_per_row}")
        return embed

    def make_tile_callback(self, col):

        async def callback(interaction: discord.Interaction):

            if interaction.user.id != self.user_id:
                return await interaction.response.send_message(
                    "❌ This is not your game.", ephemeral=True
                )

            if self.finished:
                return await interaction.response.send_message(
                    "❌ This game has ended.", ephemeral=True
                )

            safe_col = self.safe_columns[self.current_row]

            if col != safe_col:
                self.history.append({"col": col, "result": "bomb"})
                self.finished = True
                add_stats(self.user_id, total_lost=self.bet, losses=1)

                self.build_components()

                return await interaction.response.edit_message(
                    content=None,
                    embed=self.build_embed(),
                    view=self,
                )

            # progress
            self.history.append({"col": col, "result": "safe"})
            self.current_row += 1

            if self.current_row == 8:
                self.finished = True
                payout = int(self.bet * self.get_multiplier(7))
                profit = payout - self.bet

                change_gems(self.user_id, payout)
                add_stats(self.user_id, total_won=profit, wins=1)

                self.build_components()

                return await interaction.response.edit_message(
                    content=None,
                    embed=self.build_embed(),
                    view=self,
                )

            self.build_components()
            await interaction.response.edit_message(
                content=None,
                embed=self.build_embed(),
                view=self,
            )

        return callback

    async def cashout(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "❌ This is not your game.", ephemeral=True
            )

        if self.finished or self.current_row == 0:
            return await interaction.response.send_message(
                "❌ Cannot cash out right now.", ephemeral=True
            )

        self.finished = True
        payout = self.current_payout()
        profit = payout - self.bet

        change_gems(self.user_id, payout)

        if profit > 0:
            add_stats(self.user_id, total_won=profit, wins=1)
        else:
            add_stats(self.user_id, neutral=1)

        self.build_components()

        await interaction.response.edit_message(
            content=None,
            embed=self.build_embed(),
            view=self,
        )


@bot.tree.command(
    name="towers",
    description="Play Towers game.",
)
@app_commands.describe(
    amount="Minimum 1m",
    bombs="Number of bombs per row (1, 2, or 3)",
)
async def towers(
    interaction: discord.Interaction,
    amount: str,
    bombs: int = 1,
):
    try:
        bet = parse_amount(amount)
    except ValueError as error:
        return await interaction.response.send_message(
            f"❌ {error}", ephemeral=True
        )

    error = check_bet(interaction.user.id, bet, GAME_MINIMUM)
    if error:
        return await interaction.response.send_message(error, ephemeral=True)

    if bombs not in (1, 2, 3):
        return await interaction.response.send_message(
            "❌ Bombs per row must be 1, 2, or 3.", ephemeral=True
        )

    change_gems(interaction.user.id, -bet)
    add_stats(interaction.user.id, total_wagered=bet, games_played=1)

    view = TowersView(
        interaction.user.id,
        interaction.user.display_name,
        bet,
        bombs,
    )
    await interaction.response.send_message(embed=view.build_embed(), view=view)


# ============================================================
# CRASH
# ============================================================

CRASH_MAX_MULTIPLIER = 5.0
CRASH_STEP_MIN = 0.02
CRASH_STEP_MAX = 0.15
CRASH_TICK_SECONDS = 2.2
CRASH_BASE_CHANCE = 0.02
CRASH_CHANCE_GROWTH = 0.035


class CrashView(discord.ui.View):

    def __init__(
        self,
        user_id,
        bet,
    ):

        super().__init__(
            timeout=300
        )

        self.user_id = user_id
        self.bet = bet
        self.multiplier = 1.0
        self.finished = False

        button = discord.ui.Button(
            label="💰 CASH OUT",
            style=discord.ButtonStyle.success,
        )

        button.callback = self.cashout

        self.add_item(button)

    async def cashout(
        self,
        interaction,
    ):

        if interaction.user.id != self.user_id:

            return await interaction.response.send_message(
                "❌ This is not your game.",
                ephemeral=True,
            )

        if self.finished:

            return await interaction.response.send_message(
                "❌ This game has ended.",
                ephemeral=True,
            )

        self.finished = True

        payout = int(
            self.bet
            * self.multiplier
        )

        profit = payout - self.bet

        change_gems(
            self.user_id,
            payout,
        )

        if profit > 0:

            add_stats(
                self.user_id,
                total_won=profit,
                wins=1,
            )

        else:

            add_stats(
                self.user_id,
                neutral=1,
            )

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=(
                "💰 **CASHED OUT!**\n"
                f"Multiplier: "
                f"`{self.multiplier:.2f}x`\n"
                f"Payout: `{format_gems(payout)}`\n"
                f"Profit: `{profit:+,} 💎`\n"
                f"Balance: "
                f"`{format_gems(get_gems(self.user_id))}`"
            ),
            view=self,
        )


@bot.tree.command(
    name="crash",
    description="Play Crash using virtual gems.",
)
@app_commands.describe(
    amount="Minimum 1m",
)
async def crash(
    interaction: discord.Interaction,
    amount: str,
):

    try:
        bet = parse_amount(amount)

    except ValueError as error:

        return await interaction.response.send_message(
            f"❌ {error}",
            ephemeral=True,
        )

    error = check_bet(
        interaction.user.id,
        bet,
        GAME_MINIMUM,
    )

    if error:

        return await interaction.response.send_message(
            error,
            ephemeral=True,
        )

    change_gems(
        interaction.user.id,
        -bet,
    )

    add_stats(
        interaction.user.id,
        total_wagered=bet,
        games_played=1,
    )

    view = CrashView(
        interaction.user.id,
        bet,
    )

    await interaction.response.send_message(
        "🚀 **CRASH STARTING**\n"
        f"Bet: `{format_gems(bet)}`\n"
        "Multiplier: `1.00x`\n"
        "Cash out before it crashes.",
        view=view,
    )

    message = await interaction.original_response()

    while (
        not view.finished
        and view.multiplier < CRASH_MAX_MULTIPLIER
    ):

        await asyncio.sleep(
            CRASH_TICK_SECONDS
        )

        if view.finished:
            return

        view.multiplier = round(
            min(
                view.multiplier
                + random.uniform(
                    CRASH_STEP_MIN,
                    CRASH_STEP_MAX,
                ),
                CRASH_MAX_MULTIPLIER,
            ),
            2,
        )

        crash_chance = min(
            CRASH_BASE_CHANCE
            + (
                view.multiplier - 1
            )
            * CRASH_CHANCE_GROWTH,
            0.9,
        )

        if random.random() < crash_chance:

            view.finished = True

            for child in view.children:
                child.disabled = True

            add_stats(
                interaction.user.id,
                total_lost=bet,
                losses=1,
            )

            await message.edit(
                content=(
                    "💥 **CRASHED!**\n"
                    f"Crashed at: "
                    f"`{view.multiplier:.2f}x`\n"
                    f"Lost: "
                    f"`-{format_gems(bet)}`\n"
                    f"Balance: "
                    f"`{format_gems(get_gems(interaction.user.id))}`"
                ),
                view=view,
            )

            return

        await message.edit(
            content=(
                "🚀 **CRASH**\n"
                f"Bet: `{format_gems(bet)}`\n"
                f"Multiplier: "
                f"`{view.multiplier:.2f}x`\n"
                "Cash out before it crashes."
            ),
            view=view,
        )

    if view.finished:
        return

    view.finished = True

    payout = int(
        bet
        * CRASH_MAX_MULTIPLIER
    )

    profit = payout - bet

    change_gems(
        interaction.user.id,
        payout,
    )

    add_stats(
        interaction.user.id,
        total_won=profit,
        wins=1,
    )

    for child in view.children:
        child.disabled = True

    await message.edit(
        content=(
            "🏆 **MAX MULTIPLIER REACHED!**\n"
            f"Auto cashed out at "
            f"`{CRASH_MAX_MULTIPLIER:.2f}x`\n"
            f"Payout: `{format_gems(payout)}`\n"
            f"Profit: `{profit:+,} 💎`\n"
            f"Balance: "
            f"`{format_gems(get_gems(interaction.user.id))}`"
        ),
        view=view,
    )


# ============================================================
# BLACKJACK
# ============================================================

RANKS = [
    "A",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "J",
    "Q",
    "K",
]

VALUES = {
    "A": 11,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "J": 10,
    "Q": 10,
    "K": 10,
}

DECK = RANKS * 4


def hand_value(hand):

    total = sum(
        VALUES[card]
        for card in hand
    )

    aces = hand.count("A")

    while total > 21 and aces:

        total -= 10
        aces -= 1

    return total


def is_blackjack(hand):

    return (
        len(hand) == 2
        and hand_value(hand) == 21
    )


class BlackjackView(discord.ui.View):

    def __init__(
        self,
        user_id,
        player_hand,
        dealer_hand,
        bet,
    ):

        super().__init__(
            timeout=180
        )

        self.user_id = user_id
        self.player_hand = player_hand
        self.dealer_hand = dealer_hand
        self.bet = bet
        self.finished = False

    def display(self):

        return (
            "🃏 **BLACKJACK**\n\n"
            f"Dealer: "
            f"`{self.dealer_hand[0]} ❔`\n"
            f"Your hand: "
            f"`{' '.join(self.player_hand)}`\n"
            f"Score: "
            f"`{hand_value(self.player_hand)}`\n"
            f"Bet: "
            f"`{format_gems(self.bet)}`"
        )

    async def finish(
        self,
        interaction,
    ):

        self.finished = True

        player = hand_value(
            self.player_hand
        )

        if player > 21:

            dealer = hand_value(
                self.dealer_hand
            )

            result = "🔴 You busted."

            add_stats(
                self.user_id,
                total_lost=self.bet,
                losses=1,
            )

        else:

            while (
                hand_value(self.dealer_hand)
                < 17
            ):

                self.dealer_hand.append(
                    random.choice(DECK)
                )

            dealer = hand_value(
                self.dealer_hand
            )

            if (
                dealer > 21
                or player > dealer
            ):

                change_gems(
                    self.user_id,
                    self.bet * 2,
                )

                add_stats(
                    self.user_id,
                    total_won=self.bet,
                    wins=1,
                )

                result = (
                    f"🟢 You won "
                    f"`+{format_gems(self.bet)}`."
                )

            elif player == dealer:

                change_gems(
                    self.user_id,
                    self.bet,
                )

                add_stats(
                    self.user_id,
                    neutral=1,
                )

                result = (
                    "🟡 Draw. Bet returned."
                )

            else:

                add_stats(
                    self.user_id,
                    total_lost=self.bet,
                    losses=1,
                )

                result = (
                    f"🔴 You lost "
                    f"`-{format_gems(self.bet)}`."
                )

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=(
                "🃏 **BLACKJACK RESULT**\n"
                f"Dealer: "
                f"`{' '.join(self.dealer_hand)}` "
                f"({dealer})\n"
                f"Player: "
                f"`{' '.join(self.player_hand)}` "
                f"({player})\n\n"
                f"{result}\n"
                f"Balance: "
                f"`{format_gems(get_gems(self.user_id))}`"
            ),
            view=self,
        )

    @discord.ui.button(
        label="👊 HIT",
        style=discord.ButtonStyle.primary,
    )
    async def hit(
        self,
        interaction,
        button,
    ):

        if interaction.user.id != self.user_id:

            return await interaction.response.send_message(
                "❌ This is not your game.",
                ephemeral=True,
            )

        if self.finished:

            return await interaction.response.send_message(
                "❌ This game has ended.",
                ephemeral=True,
            )

        self.player_hand.append(
            random.choice(DECK)
        )

        if hand_value(
            self.player_hand
        ) >= 21:

            return await self.finish(
                interaction
            )

        await interaction.response.edit_message(
            content=self.display(),
            view=self,
        )

    @discord.ui.button(
        label="✋ STAND",
        style=discord.ButtonStyle.success,
    )
    async def stand(
        self,
        interaction,
        button,
    ):

        if interaction.user.id != self.user_id:

            return await interaction.response.send_message(
                "❌ This is not your game.",
                ephemeral=True,
            )

        if self.finished:

            return await interaction.response.send_message(
                "❌ This game has ended.",
                ephemeral=True,
            )

        await self.finish(
            interaction
        )

    @discord.ui.button(
        label="💰 DOUBLE DOWN",
        style=discord.ButtonStyle.secondary,
    )
    async def double_down(
        self,
        interaction,
        button,
    ):

        if interaction.user.id != self.user_id:

            return await interaction.response.send_message(
                "❌ This is not your game.",
                ephemeral=True,
            )

        if (
            self.finished
            or len(self.player_hand) != 2
        ):

            return await interaction.response.send_message(
                "❌ Double Down is only available initially.",
                ephemeral=True,
            )

        if get_gems(
            self.user_id
        ) < self.bet:

            return await interaction.response.send_message(
                "❌ You do not have enough gems.",
                ephemeral=True,
            )

        change_gems(
            self.user_id,
            -self.bet,
        )

        add_stats(
            self.user_id,
            total_wagered=self.bet,
        )

        self.bet *= 2

        self.player_hand.append(
            random.choice(DECK)
        )

        await self.finish(
            interaction
        )

    @discord.ui.button(
        label="🔀 SPLIT",
        style=discord.ButtonStyle.secondary,
    )
    async def split(
        self,
        interaction,
        button,
    ):

        await interaction.response.send_message(
            "⚠️ Split is not enabled.",
            ephemeral=True,
        )


@bot.tree.command(
    name="blackjack",
    description="Play Blackjack. Minimum 10m.",
)
@app_commands.describe(
    amount="Minimum 10m",
)
async def blackjack_command(
    interaction: discord.Interaction,
    amount: str,
):

    try:
        bet = parse_amount(amount)

    except ValueError as error:

        return await interaction.response.send_message(
            f"❌ {error}",
            ephemeral=True,
        )

    error = check_bet(
        interaction.user.id,
        bet,
        BLACKJACK_MINIMUM,
    )

    if error:

        return await interaction.response.send_message(
            error,
            ephemeral=True,
        )

    change_gems(
        interaction.user.id,
        -bet,
    )

    add_stats(
        interaction.user.id,
        total_wagered=bet,
        games_played=1,
    )

    player_hand = [
        random.choice(DECK),
        random.choice(DECK),
    ]

    dealer_hand = [
        random.choice(DECK),
        random.choice(DECK),
    ]

    if is_blackjack(
        player_hand
    ):

        payout = int(
            bet * 2.5
        )

        profit = payout - bet

        change_gems(
            interaction.user.id,
            payout,
        )

        add_stats(
            interaction.user.id,
            total_won=profit,
            wins=1,
        )

        return await interaction.response.send_message(
            "🃏 **BLACKJACK!**\n"
            f"Hand: "
            f"`{' '.join(player_hand)}`\n"
            f"Profit: "
            f"`+{format_gems(profit)}`\n"
            f"Balance: "
            f"`{format_gems(get_gems(interaction.user.id))}`"
        )

    view = BlackjackView(
        interaction.user.id,
        player_hand,
        dealer_hand,
        bet,
    )

    await interaction.response.send_message(
        view.display(),
        view=view,
    )


# ============================================================
# TICKET SYSTEM
# ============================================================

TICKET_CATEGORY_NAME = "GEM REQUESTS"


def ticket_label(ticket_type: str) -> str:
    return "Deposit" if ticket_type == "deposit" else "Withdrawal"


def ticket_code(ticket_type: str, ticket_id: int) -> str:
    prefix = "DEP" if ticket_type == "deposit" else "WDR"
    return f"{prefix}-{ticket_id:04d}"


def ticket_overwrites(guild, member):
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
            manage_channels=True,
            manage_messages=True,
        ),
        member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        ),
    }

    for owner_id in OWNER_IDS:
        owner = guild.get_member(owner_id)
        if owner:
            overwrites[owner] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
            )

    staff_role = discord.utils.get(guild.roles, name=TICKET_STAFF_ROLE_NAME)
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
        )

    return overwrites


class TicketView(discord.ui.View):
    def __init__(self, ticket_id):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id

    @discord.ui.button(
        label="✅ Approve",
        style=discord.ButtonStyle.success,
    )
    async def approve(self, interaction, button):
        if not await is_ticket_staff(interaction):
            return await interaction.response.send_message(
                "❌ Staff only.", ephemeral=True
            )

        row = db.execute(
            "SELECT * FROM tickets WHERE id=?",
            (self.ticket_id,),
        ).fetchone()

        if not row or row["status"] != "open":
            return await interaction.response.send_message(
                "❌ This ticket is no longer pending.", ephemeral=True
            )

        if row["ticket_type"] == "deposit":
            change_gems(row["user_id"], row["amount"])
            add_stats(row["user_id"], gems_added=row["amount"])
            action_text = (
                "✅ **Deposit approved**\n"
                f"Added `{format_gems(row['amount'])}` to the user's balance."
            )
        else:
            if get_gems(row["user_id"]) < row["amount"]:
                return await interaction.response.send_message(
                    "❌ The member no longer has enough gems for this withdrawal.",
                    ephemeral=True,
                )
            change_gems(row["user_id"], -row["amount"])
            add_stats(row["user_id"], gems_removed=row["amount"])
            action_text = (
                "✅ **Withdrawal approved**\n"
                f"Removed `{format_gems(row['amount'])}` from the user's balance."
            )

        db.execute(
            "UPDATE tickets SET status='approved', reviewed_by=? WHERE id=?",
            (interaction.user.id, self.ticket_id),
        )
        db.commit()

        for child in self.children:
            child.disabled = True

        embed = discord.Embed(
            title=f"💎 {ticket_label(row['ticket_type'])} Ticket",
            description=action_text,
        )
        embed.add_field(
            name="Ticket ID",
            value=f"`{ticket_code(row['ticket_type'], row['id'])}`",
            inline=True,
        )
        embed.add_field(name="Status", value="🟢 Approved", inline=True)
        embed.add_field(
            name="Amount",
            value=f"`{format_gems(row['amount'])}`",
            inline=False,
        )
        embed.set_footer(text=f"Reviewed by {interaction.user.display_name}")

        await interaction.response.edit_message(embed=embed, view=self)

        if row["ticket_type"] == "deposit":
            member = interaction.guild.get_member(row["user_id"])
            user_mention = member.mention if member else f"<@{row['user_id']}>"

            await interaction.channel.send(
                "📩 Please wait for staff to contact you."
            )
        else:
            member = interaction.guild.get_member(row["user_id"])
            user_mention = member.mention if member else f"<@{row['user_id']}>"

            linked_row = db.execute(
                "SELECT roblox_username FROM users WHERE user_id=?",
                (row["user_id"],),
            ).fetchone()
            roblox_username = (
                linked_row["roblox_username"]
                if linked_row and linked_row["roblox_username"]
                else "Not linked"
            )

            await interaction.channel.send(
                f"📤 {user_mention}, your withdrawal has been approved!\n"
                f"Our team will mail **`{format_gems(row['amount'])}`** to your Roblox "
                f"account `{roblox_username}` shortly."
            )

    @discord.ui.button(
        label="❌ Reject",
        style=discord.ButtonStyle.danger,
    )
    async def reject(self, interaction, button):
        if not await is_ticket_staff(interaction):
            return await interaction.response.send_message(
                "❌ Staff only.", ephemeral=True
            )

        row = db.execute(
            "SELECT * FROM tickets WHERE id=?",
            (self.ticket_id,),
        ).fetchone()

        if not row or row["status"] != "open":
            return await interaction.response.send_message(
                "❌ This ticket is no longer pending.", ephemeral=True
            )

        db.execute(
            "UPDATE tickets SET status='rejected', reviewed_by=? WHERE id=?",
            (interaction.user.id, self.ticket_id),
        )
        db.commit()

        for child in self.children:
            child.disabled = True

        embed = discord.Embed(
            title=f"💎 {ticket_label(row['ticket_type'])} Ticket",
            description="❌ **Request rejected.**",
        )
        embed.add_field(
            name="Ticket ID",
            value=f"`{ticket_code(row['ticket_type'], row['id'])}`",
            inline=True,
        )
        embed.add_field(name="Status", value="🔴 Rejected", inline=True)
        embed.add_field(
            name="Amount",
            value=f"`{format_gems(row['amount'])}`",
            inline=False,
        )
        embed.set_footer(text=f"Rejected by {interaction.user.display_name}")

        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="🔒 Close Ticket",
        style=discord.ButtonStyle.secondary,
    )
    async def close(self, interaction, button):
        row = db.execute(
            "SELECT * FROM tickets WHERE id=?",
            (self.ticket_id,),
        ).fetchone()

        if not row:
            return await interaction.response.send_message(
                "❌ Ticket not found.", ephemeral=True
            )

        is_ticket_owner = interaction.user.id == row["user_id"]
        if not is_ticket_owner and not await is_ticket_staff(interaction):
            return await interaction.response.send_message(
                "❌ Only the ticket creator or staff can close this ticket.",
                ephemeral=True,
            )

        db.execute(
            "UPDATE tickets SET status='closed', reviewed_by=? WHERE id=?",
            (interaction.user.id, self.ticket_id),
        )
        db.commit()

        for child in self.children:
            child.disabled = True

        embed = discord.Embed(
            title=f"💎 {ticket_label(row['ticket_type'])} Ticket",
            description="🔒 **Ticket closed.**",
        )
        embed.add_field(
            name="Ticket ID",
            value=f"`{ticket_code(row['ticket_type'], row['id'])}`",
            inline=True,
        )
        embed.add_field(name="Status", value="⚫ Closed", inline=True)
        embed.add_field(
            name="Amount",
            value=f"`{format_gems(row['amount'])}`",
            inline=False,
        )
        embed.set_footer(text=f"Closed by {interaction.user.display_name}")

        await interaction.response.edit_message(embed=embed, view=self)

        member = interaction.guild.get_member(row["user_id"])
        if member:
            try:
                await interaction.channel.set_permissions(
                    member,
                    view_channel=False,
                    send_messages=False,
                )
            except discord.Forbidden:
                pass


async def create_ticket(interaction, ticket_type, amount_text):
    if not interaction.guild:
        return await interaction.response.send_message(
            "❌ Use this command inside a server.", ephemeral=True
        )

    try:
        amount = parse_amount(amount_text)
    except ValueError as error:
        return await interaction.response.send_message(
            f"❌ {error}", ephemeral=True
        )

    if amount <= 0:
        return await interaction.response.send_message(
            "❌ Amount must be greater than zero.", ephemeral=True
        )

    if ticket_type == "withdrawal" and amount > get_gems(interaction.user.id):
        return await interaction.response.send_message(
            "❌ You don't have enough gems for this withdrawal.",
            ephemeral=True,
        )

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    guild = interaction.guild

    try:
        category = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
        if category is None:
            category = await guild.create_category(
                TICKET_CATEGORY_NAME,
                reason="Gem ticket system",
            )

        prefix = "deposit" if ticket_type == "deposit" else "withdrawal"
        channel = await guild.create_text_channel(
            f"{prefix}-{interaction.user.name}-{random.randint(1000, 9999)}"[:90],
            category=category,
            overwrites=ticket_overwrites(guild, interaction.user),
            reason="Gem request ticket",
        )
    except discord.Forbidden:
        return await interaction.followup.send(
            "❌ I don't have permission to create the ticket category/channel.\n\n"
            "Give the bot **Manage Channels** permission and try again.",
            ephemeral=True,
        )
    except discord.HTTPException as error:
        print(f"Ticket creation HTTP error: {error}")
        return await interaction.followup.send(
            "❌ Discord returned an error while creating the ticket. "
            "Check the bot's permissions and try again.",
            ephemeral=True,
        )

    try:
        cursor = db.execute(
            """
            INSERT INTO tickets(channel_id, user_id, ticket_type, amount)
            VALUES (?, ?, ?, ?)
            """,
            (channel.id, interaction.user.id, ticket_type, amount),
        )
        db.commit()
        ticket_id = cursor.lastrowid
    except sqlite3.Error as error:
        print(f"Ticket database error: {error}")
        try:
            await channel.delete(reason="Ticket database failure")
        except Exception:
            pass
        return await interaction.followup.send(
            "❌ Database error while creating the ticket.",
            ephemeral=True,
        )

    linked_row = db.execute(
        "SELECT roblox_username FROM users WHERE user_id=?",
        (interaction.user.id,),
    ).fetchone()
    roblox_username = (
        linked_row["roblox_username"]
        if linked_row and linked_row["roblox_username"]
        else "Not linked"
    )

    title = f"{ticket_label(ticket_type)} Ticket"
    description = (
        "Please provide the deposit details below.\n"
        "⏳ **Please wait for a staff member to respond.**"
        if ticket_type == "deposit"
        else "Please provide the withdrawal details below.\n"
        "⏳ **Please wait for a staff member to respond.**"
    )

    embed = discord.Embed(
        title=f"💎  {title}",
        description=description,
    )
    embed.add_field(
        name="Ticket ID",
        value=f"`{ticket_code(ticket_type, ticket_id)}`",
        inline=True,
    )
    embed.add_field(
        name="Status",
        value="🟡 Pending",
        inline=True,
    )
    embed.add_field(
        name="User",
        value=interaction.user.mention,
        inline=False,
    )
    embed.add_field(
        name="Roblox username",
        value=f"`{roblox_username}`",
        inline=True,
    )
    embed.add_field(
        name="💎 Amount",
        value=f"`{format_gems(amount)}`",
        inline=True,
    )
    embed.set_footer(text="Only you and the deposit/withdrawal team can see this")

    try:
        await channel.send(
            embed=embed,
            view=TicketView(ticket_id),
        )
    except discord.HTTPException as error:
        print(f"Ticket message error: {error}")
        return await interaction.followup.send(
            f"⚠️ Ticket channel was created: {channel.mention}\n"
            "But I couldn't send the ticket message. Check the bot's channel permissions.",
            ephemeral=True,
        )

    await interaction.followup.send(
        f"✅ **{ticket_label(ticket_type)} ticket created in:** {channel.mention}",
        ephemeral=True,
    )


# ============================================================
# DEPOSIT
# ============================================================

@bot.tree.command(
    name="deposit",
    description="Create a deposit ticket.",
)
@app_commands.describe(
    amount="Amount to deposit, such as 100k, 1m, 25m, or 1b",
)
async def deposit(interaction: discord.Interaction, amount: str):
    await create_ticket(interaction, "deposit", amount)


# ============================================================
# WITHDRAW
# ============================================================

@bot.tree.command(
    name="withdraw",
    description="Create a withdrawal ticket.",
)
@app_commands.describe(
    amount="Amount to withdraw, such as 100k, 1m, 25m, or 1b",
)
async def withdraw(interaction: discord.Interaction, amount: str):
    await create_ticket(interaction, "withdrawal", amount)


# ============================================================
# ERROR HANDLING
# ============================================================

@bot.event
async def on_error(
    event,
    *args,
    **kwargs,
):

    import traceback

    print(
        f"Unhandled bot error in event: {event}"
    )

    traceback.print_exc()


# ============================================================
# START
# ============================================================

if not TOKEN:

    raise RuntimeError(
        "Missing DISCORD_TOKEN environment variable."
    )


bot.run(TOKEN)
