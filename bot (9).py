import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import json
import os
import random
import re
import time
import uuid
from datetime import datetime, timezone

# ============================================================
# CONFIG
# ============================================================

TOKEN = os.environ.get("Discord_token") or os.environ.get("TOKEN")

STAFF_ROLE_ID = 1449349282489696297
VERIFIED_ROLE_ID = 1449349322763145339

COINFLIP_CHANNEL_ID = 1539329155752009819
GAME_LOG_CHANNEL_ID = 1536388486180114483
PROFIT_TRACKER_CHANNEL_ID = 1536388275814535209
TIP_CHANNEL_ID = 1537092723763314689

DEPOSIT_LOG_CHANNEL_ID = 1536573870067294301
WITHDRAW_LOG_CHANNEL_ID = 1536664785599336498

DEPOSIT_CATEGORY_ID = 1459533708762546219
WITHDRAW_CATEGORY_ID = 1459533852597948450

DATA_FILE = "casino_data.json"

MIN_GAME_AMOUNT = 10_000_000

MILESTONE_ROLES = {
    500_000_000: 1458426011816562740,
    1_000_000_000: 1458426285855473873,
    5_000_000_000: 1458426717755539456,
    15_000_000_000: 1458427186292719616,
    30_000_000_000: 1458427481970311260,
    50_000_000_000: 1458427832303751322,
    100_000_000_000: 1458428376661491866,
}

# ============================================================
# BOT
# ============================================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

tree = bot.tree

# ============================================================
# DATA
# ============================================================

DEFAULT_DATA = {
    "users": {},
    "verification": {},
    "tickets": {},
    "affiliates": {},
    "global_stats": {
        "total_deposits": 0,
        "total_withdraws": 0,
        "bot_game_profit": 0,
        "profit_tracker_message_id": None
    },
    "settings": {
        "paused": False
    }
}

if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            DATA = json.load(f)
    except Exception:
        DATA = DEFAULT_DATA.copy()
else:
    DATA = DEFAULT_DATA.copy()

if not isinstance(DATA, dict):
    DATA = {}

if not isinstance(DATA.get("users"), dict):
    DATA["users"] = {}

if not isinstance(DATA.get("verification"), dict):
    DATA["verification"] = {}

if not isinstance(DATA.get("tickets"), dict):
    DATA["tickets"] = {}

if not isinstance(DATA.get("affiliates"), dict):
    DATA["affiliates"] = {}

if not isinstance(DATA.get("global_stats"), dict):
    DATA["global_stats"] = {
        "total_deposits": 0, 
        "total_withdraws": 0, 
        "bot_game_profit": 0, 
        "profit_tracker_message_id": None
    }

DATA["global_stats"].setdefault("total_deposits", 0)
DATA["global_stats"].setdefault("total_withdraws", 0)
DATA["global_stats"].setdefault("bot_game_profit", 0)
DATA["global_stats"].setdefault("profit_tracker_message_id", None)

if not isinstance(DATA.get("settings"), dict):
    DATA["settings"] = {}

DATA["settings"].setdefault("paused", False)


def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(DATA, f, indent=4)


def ensure_user(user_id):
    uid = str(user_id)

    if uid not in DATA["users"]:
        DATA["users"][uid] = {
            "balance": 0,
            "wagered": 0,
            "history": [],
            "roblox": None,
            "last_rakeback": 0,
            "affiliate_code": f"REF-{random.randint(1000, 9999)}",
            "referred_by": None
        }

    return DATA["users"][uid]


def add_history(user_id, game, amount, result):
    user = ensure_user(user_id)

    user["history"].append({
        "game": game,
        "amount": amount,
        "result": result,
        "time": int(time.time())
    })

    user["history"] = user["history"][-100:]


def parse_amount(amount_str: str) -> int | None:
    if not isinstance(amount_str, str):
        return None
    amount_str = amount_str.lower().strip().replace(",", "").replace(" ", "")
    match = re.match(r"^(\d+(?:\.\d+)?)([kmbt])?$", amount_str)
    if not match:
        return None
    val, mult = match.groups()
    val = float(val)
    if mult == "k":
        val *= 1_000
    elif mult == "m":
        val *= 1_000_000
    elif mult == "b":
        val *= 1_000_000_000
    elif mult == "t":
        val *= 1_000_000_000_000
    return int(val)


def format_amount(amount: int) -> str:
    amount = int(amount)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)

    if amount >= 1_000_000_000_000:
        val = amount / 1_000_000_000_000
        return f"{sign}{val:.1f}t".replace(".0t", "t")
    elif amount >= 1_000_000_000:
        val = amount / 1_000_000_000
        return f"{sign}{val:.1f}b".replace(".0b", "b")
    elif amount >= 1_000_000:
        val = amount / 1_000_000
        return f"{sign}{val:.1f}m".replace(".0m", "m")
    elif amount >= 1_000:
        val = amount / 1_000
        return f"{sign}{val:.1f}k".replace(".0k", "k")
    return f"{sign}{amount}"


OWNER_IDS = {1399688332912365589}

def is_staff(member):
    if isinstance(member, (discord.Member, discord.User)) and member.id in OWNER_IDS:
        return True
    return isinstance(member, discord.Member) and any(
        role.id == STAFF_ROLE_ID for role in member.roles
    )


def is_verified(member):
    return isinstance(member, discord.Member) and any(
        role.id == VERIFIED_ROLE_ID for role in member.roles
    )


async def verification_check(interaction):
    if not is_verified(interaction.user):
        await interaction.response.send_message(
            "❌ Please verify first using `/verify`.",
            ephemeral=True
        )
        return False

    return True


def normal_embed(title, description="", colour=None):
    if colour is None:
        colour = discord.Colour.blurple()

    embed = discord.Embed(
        title=title,
        description=description,
        colour=colour,
        timestamp=datetime.now(timezone.utc)
    )

    return embed


def get_live_profit_embed():
    deposits = DATA["global_stats"].get("total_deposits", 0)
    withdraws = DATA["global_stats"].get("total_withdraws", 0)
    game_profit = DATA["global_stats"].get("bot_game_profit", 0)
    
    total_net_profit = (deposits - withdraws) + game_profit

    embed = discord.Embed(
        title="📊 Live Profit Tracker",
        colour=discord.Colour.gold()
    )
    embed.add_field(name="📥 Total Deposits", value=f"**💎 {format_amount(deposits)}**", inline=False)
    embed.add_field(name="📤 Total Withdraws", value=f"**💎 {format_amount(withdraws)}**", inline=False)
    embed.add_field(name="🎲 Net Game Profit", value=f"**💎 {format_amount(game_profit)}**", inline=False)
    embed.add_field(name="📈 Total Net Bot Profit", value=f"**💎 {format_amount(total_net_profit)}**", inline=False)
    embed.set_footer(text="Aqua Gems Casino")
    return embed


# ============================================================
# LOGGING & LIVE TRACKER
# ============================================================

async def send_log(guild, title, description, colour=None):
    game_channel = guild.get_channel(GAME_LOG_CHANNEL_ID)
    if game_channel:
        try:
            log_embed = normal_embed(title, description, colour)
            await game_channel.send(embed=log_embed)
        except Exception:
            pass

    stats_channel = guild.get_channel(PROFIT_TRACKER_CHANNEL_ID)
    if stats_channel:
        try:
            profit_embed = get_live_profit_embed()
            msg_id = DATA["global_stats"].get("profit_tracker_message_id")
            
            if msg_id:
                try:
                    msg = await stats_channel.fetch_message(msg_id)
                    await msg.edit(embed=profit_embed)
                    return
                except discord.NotFound:
                    pass
            
            new_msg = await stats_channel.send(embed=profit_embed)
            DATA["global_stats"]["profit_tracker_message_id"] = new_msg.id
            save_data()
        except Exception as e:
            print(f"Profit tracker update error: {e}")


# ============================================================
# MILESTONE ROLES
# ============================================================

async def update_milestone_roles(member):
    user = ensure_user(member.id)
    total = user["wagered"]
    highest_role_id = None

    for amount, role_id in sorted(MILESTONE_ROLES.items(), reverse=True):
        if total >= amount:
            highest_role_id = role_id
            break

    if highest_role_id is None:
        return

    for role_id in MILESTONE_ROLES.values():
        role = member.guild.get_role(role_id)
        if role is None:
            continue

        if role_id == highest_role_id:
            if role not in member.roles:
                try:
                    await member.add_roles(role)
                    try:
                        await member.send(
                            "🎉 **Congratulations!**\n\n"
                            f"You reached **{format_amount(total)}** "
                            f"of total game activity and unlocked {role.mention}!"
                        )
                    except discord.Forbidden:
                        pass
                except discord.Forbidden:
                    pass
        else:
            if role in member.roles:
                try:
                    await member.remove_roles(role)
                except discord.Forbidden:
                    pass


# ============================================================
# PAUSE
# ============================================================

async def game_paused(interaction):
    if DATA["settings"]["paused"]:
        await interaction.response.send_message(
            "⏸️ **All current games are currently paused.**\n"
            "Please be patient!",
            ephemeral=True
        )
        return True
    return False


@tree.command(name="pausebets", description="Pause all active games.")
async def pausebets(interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return

    DATA["settings"]["paused"] = True
    save_data()

    await interaction.response.send_message("⏸️ **All games are now paused.**")


@tree.command(name="resumebets", description="Resume all games.")
async def resumebets(interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return

    DATA["settings"]["paused"] = False
    save_data()

    await interaction.response.send_message("▶️ **All games have been resumed!**")


# ============================================================
# ROBLOX API & VERIFY
# ============================================================

async def get_roblox_user(username):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [username], "excludeBannedUsers": False}
            ) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                if not data.get("data"):
                    return None
                return data["data"][0]
        except Exception:
            return None


async def get_roblox_avatar(user_id):
    async with aiohttp.ClientSession() as session:
        try:
            url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=150x150&format=Png&isCircular=false"
            async with session.get(url) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                if data.get("data"):
                    return data["data"][0].get("imageUrl")
        except Exception:
            pass
    return None


class VerifyConfirmView(discord.ui.View):
    def __init__(self, owner_id, username, roblox_id, avatar):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.username = username
        self.roblox_id = roblox_id
        self.avatar = avatar

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ This verification isn't yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes, this is me", style=discord.ButtonStyle.success)
    async def yes(self, interaction, button):
        code = f"VERIFY-{random.randint(100000, 999999)}"
        DATA["verification"][str(self.owner_id)] = {
            "username": self.username,
            "roblox_id": self.roblox_id,
            "code": code,
            "confirmed": False
        }
        save_data()

        embed = normal_embed(
            "📝 Roblox Verification",
            f"Put this exact code in your Roblox bio:\n\n```{code}```\n\nOnce added, press **Verify Now**.",
            discord.Colour.orange()
        )
        view = VerifyNowView(self.owner_id, self.username, self.roblox_id, code)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="No, this is not me", style=discord.ButtonStyle.danger)
    async def no(self, interaction, button):
        DATA["verification"].pop(str(self.owner_id), None)
        save_data()
        await interaction.response.edit_message(
            embed=normal_embed("❌ Verification Cancelled", "Run `/verify` again whenever you're ready.", discord.Colour.red()),
            view=None
        )


class VerifyNowView(discord.ui.View):
    def __init__(self, owner_id, username, roblox_id, code):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.username = username
        self.roblox_id = roblox_id
        self.code = code

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ This verification isn't yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Verify Now", style=discord.ButtonStyle.success)
    async def verify_now(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"https://users.roblox.com/v1/users/{self.roblox_id}") as response:
                    if response.status != 200:
                        await interaction.followup.send("❌ Roblox couldn't be reached.", ephemeral=True)
                        return
                    data = await response.json()
                    description = data.get("description", "")
            except Exception:
                await interaction.followup.send("❌ Couldn't check Roblox right now.", ephemeral=True)
                return

        if self.code not in description:
            await interaction.followup.send("❌ Code not found in bio.", ephemeral=True)
            return

        role = interaction.guild.get_role(VERIFIED_ROLE_ID)
        if role:
            try:
                await interaction.user.add_roles(role)
            except discord.Forbidden:
                pass

        user = ensure_user(interaction.user.id)
        user["roblox"] = self.username
        DATA["verification"][str(interaction.user.id)]["confirmed"] = True
        save_data()

        await interaction.followup.send(f"✅ **Successfully verified as {self.username}!**", ephemeral=True)


@tree.command(name="verify", description="Verify your Roblox account.")
@app_commands.describe(username="Your Roblox username")
async def verify(interaction, username: str):
    await interaction.response.defer(ephemeral=True)
    roblox = await get_roblox_user(username)
    if not roblox:
        await interaction.followup.send("❌ Roblox username not found.", ephemeral=True)
        return

    avatar = await get_roblox_avatar(roblox["id"])
    embed = normal_embed(
        "🔐 Is this your Roblox account?",
        f"**Username:** {roblox['name']}\n**Display Name:** {roblox.get('displayName', roblox['name'])}"
    )
    if avatar:
        embed.set_thumbnail(url=avatar)

    await interaction.followup.send(
        embed=embed,
        view=VerifyConfirmView(interaction.user.id, roblox["name"], roblox["id"], avatar),
        ephemeral=True
    )


# ============================================================
# BALANCE & MANAGEMENT
# ============================================================

@tree.command(name="balance", description="Check your balance.")
async def balance(interaction):
    if not await verification_check(interaction):
        return

    user = ensure_user(interaction.user.id)
    roblox_name = user.get("roblox", "Not linked")

    embed = normal_embed("💎 Your Balance", "", discord.Colour.gold())
    embed.add_field(name="Linked User", value=roblox_name, inline=False)
    embed.add_field(name="Balance", value=f"💎 **{format_amount(user['balance'])}**", inline=True)
    embed.add_field(name="Total Wagered", value=f"💎 {format_amount(user['wagered'])}", inline=True)

    await interaction.response.send_message(embed=embed)


@tree.command(name="add-gems", description="Add balance to a user.")
@app_commands.describe(user="User", amount="Example: 10m, 500m, 1b")
async def add_gems(interaction, user: discord.Member, amount: str):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return

    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0:
        await interaction.response.send_message("❌ Invalid amount.", ephemeral=True)
        return

    target = ensure_user(user.id)
    target["balance"] += parsed
    save_data()

    await interaction.response.send_message(f"✅ Added **{format_amount(parsed)}** to {user.mention}.")


@tree.command(name="remove-gems", description="Remove balance from a user.")
@app_commands.describe(user="User", amount="Example: 10m, 500m, 1b")
async def remove_gems(interaction, user: discord.Member, amount: str):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return

    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0:
        await interaction.response.send_message("❌ Invalid amount.", ephemeral=True)
        return

    target = ensure_user(user.id)
    target["balance"] = max(0, target["balance"] - parsed)
    save_data()

    await interaction.response.send_message(f"✅ Removed **{format_amount(parsed)}** from {user.mention}.")


# ============================================================
# MINES GAME
# ============================================================

def calculate_mines_multiplier(mines_count, revealed_count):
    if revealed_count == 0:
        return 1.00
    
    total_tiles = 25
    safe_tiles = total_tiles - mines_count
    
    mult = 0.99
    for i in range(revealed_count):
        mult *= (total_tiles - i) / (safe_tiles - i)
        
    return round(mult, 2)


class MinesTileButton(discord.ui.Button):
    def __init__(self, index):
        super().__init__(style=discord.ButtonStyle.secondary, label="\u200b", row=index // 5)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        await self.view.process_click(interaction, self.index)


class MinesCashOutButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.success,
            label="💰 Cash Out",
            row=4,
            disabled=True
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.cash_out(interaction)


class MinesGameView(discord.ui.View):
    def __init__(self, owner_id, amount, num_mines):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.amount = amount
        self.num_mines = num_mines
        self.revealed = set()
        self.game_over = False

        self.bomb_positions = set(random.sample(range(25), self.num_mines))

        for i in range(25):
            self.add_item(MinesTileButton(i))

        self.cash_out_btn = MinesCashOutButton()
        self.add_item(self.cash_out_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ This game is not yours!", ephemeral=True)
            return False
        if await game_paused(interaction):
            return False
        return True

    def get_current_multiplier(self):
        return calculate_mines_multiplier(self.num_mines, len(self.revealed))

    def get_next_multiplier(self):
        return calculate_mines_multiplier(self.num_mines, len(self.revealed) + 1)

    def build_embed(self, status="in_progress"):
        current_mult = self.get_current_multiplier()
        current_winnings = int(self.amount * current_mult)
        next_mult = self.get_next_multiplier()
        next_winnings = int(self.amount * next_mult)

        if status == "cashed_out":
            title = f"{self.num_mines} Mines Cashed Out"
            colour = discord.Colour.green()
            winnings_str = f"💎 **Winnings:** {format_amount(current_winnings)}"
        elif status == "hit_bomb":
            title = f"{self.num_mines} Mines Hit a Bomb"
            colour = discord.Colour.red()
            winnings_str = "💎 **Current winnings:** 0"
        else:
            title = f"{self.num_mines} Mines"
            colour = discord.Colour.purple()
            winnings_str = f"💎 **Current winnings:** {format_amount(current_winnings)}"

        description = (
            f"**Game Stats**\n"
            f"💎 **Bet:** {format_amount(self.amount)}\n"
            f"✨ **Multiplier:** {current_mult:.2f}x\n"
            f"{winnings_str}\n"
            f"⏳ **Next click:** {format_amount(next_winnings)}\n\n"
            f"Click tiles to reveal diamonds, or press Cash Out!"
        )

        embed = discord.Embed(
            title=title,
            description=description,
            colour=colour
        )
        embed.set_author(name="Aqua Gems Casino")
        embed.set_footer(text="Aqua Gems Casino")
        return embed

    async def process_click(self, interaction: discord.Interaction, index: int):
        if self.game_over:
            return

        if index in self.revealed:
            return

        if index not in self.bomb_positions:
            self.revealed.add(index)
            btn = self.children[index]
            btn.style = discord.ButtonStyle.success
            btn.emoji = "💎"
            btn.label = None

            current_mult = self.get_current_multiplier()
            current_winnings = int(self.amount * current_mult)
            self.cash_out_btn.disabled = False
            self.cash_out_btn.label = f"💰 Cash Out ({format_amount(current_winnings)})"

            total_safe = 25 - self.num_mines
            if len(self.revealed) == total_safe:
                await self.cash_out(interaction)
                return

            await interaction.response.edit_message(
                embed=self.build_embed(status="in_progress"),
                view=self
            )
        else:
            await self.hit_bomb(interaction, hit_index=index)

    async def cash_out(self, interaction: discord.Interaction):
        if len(self.revealed) == 0:
            await interaction.response.send_message("❌ You must reveal at least one tile before cashing out!", ephemeral=True)
            return

        self.game_over = True
        mult = self.get_current_multiplier()
        payout = int(self.amount * mult)
        net_profit = payout - self.amount

        user = ensure_user(self.owner_id)
        user["balance"] += payout
        user["wagered"] += self.amount
        add_history(self.owner_id, f"Mines ({self.num_mines})", self.amount, "Win")
        
        DATA["global_stats"]["bot_game_profit"] -= net_profit
        save_data()

        await update_milestone_roles(interaction.user)

        for i, child in enumerate(self.children):
            child.disabled = True
            if i < 25:
                if i in self.bomb_positions:
                    child.style = discord.ButtonStyle.danger
                    child.emoji = "💣"
                    child.label = None
                else:
                    child.style = discord.ButtonStyle.success
                    child.emoji = "💎"
                    child.label = None

        embed = self.build_embed(status="cashed_out")
        await interaction.response.edit_message(embed=embed, view=self)

        await send_log(
            interaction.guild,
            "💣 Mines Cashed Out",
            f"Player: {interaction.user.mention}\nAmount: **{format_amount(self.amount)}**\nMines: **{self.num_mines}**\nPayout: **{format_amount(payout)}** ({mult:.2f}x)",
            discord.Colour.green()
        )
        self.stop()

    async def hit_bomb(self, interaction: discord.Interaction, hit_index: int):
        self.game_over = True

        user = ensure_user(self.owner_id)
        user["wagered"] += self.amount
        add_history(self.owner_id, f"Mines ({self.num_mines})", self.amount, "Loss")
        
        DATA["global_stats"]["bot_game_profit"] += self.amount
        save_data()

        await update_milestone_roles(interaction.user)

        for i, child in enumerate(self.children):
            child.disabled = True
            if i < 25:
                if i in self.bomb_positions:
                    child.style = discord.ButtonStyle.danger
                    child.emoji = "💣"
                    child.label = None
                else:
                    child.style = discord.ButtonStyle.success
                    child.emoji = "💎"
                    child.label = None

        embed = self.build_embed(status="hit_bomb")
        await interaction.response.edit_message(embed=embed, view=self)

        await send_log(
            interaction.guild,
            "💣 Mines Hit a Bomb",
            f"Player: {interaction.user.mention}\nAmount: **{format_amount(self.amount)}**\nMines: **{self.num_mines}**\nResult: **Loss**",
            discord.Colour.red()
        )
        self.stop()


@tree.command(name="mines", description="Play Aqua Gems Casino Mines game.")
@app_commands.describe(amount="Bet amount (e.g., 10m, 1b)", mines="Number of mines (1-24)")
async def mines(interaction: discord.Interaction, amount: str, mines: int = 3):
    if not await verification_check(interaction) or await game_paused(interaction):
        return

    if mines < 1 or mines > 24:
        await interaction.response.send_message("❌ Mines count must be between 1 and 24.", ephemeral=True)
        return

    parsed = parse_amount(amount)
    if parsed is None or parsed < MIN_GAME_AMOUNT:
        await interaction.response.send_message("❌ Minimum bet amount is 10M.", ephemeral=True)
        return

    user = ensure_user(interaction.user.id)
    if user["balance"] < parsed:
        await interaction.response.send_message("❌ Insufficient balance.", ephemeral=True)
        return

    user["balance"] -= parsed
    save_data()

    view = MinesGameView(interaction.user.id, parsed, mines)
    embed = view.build_embed(status="in_progress")

    await interaction.response.send_message(embed=embed, view=view)


# ============================================================
# UPDATED TOWERS GAME
# ============================================================

TOWER_DIFFICULTIES = {
    "easy": {"tiles_per_row": 3, "bombs_per_row": 1, "labels": ["Left", "Middle", "Right"]},
    "medium": {"tiles_per_row": 2, "bombs_per_row": 1, "labels": ["Left", "Right"]},
    "hard": {"tiles_per_row": 3, "bombs_per_row": 2, "labels": ["Left", "Middle", "Right"]}
}

TOTAL_TOWER_ROWS = 8


def calculate_towers_multiplier(difficulty: str, current_row: int) -> float:
    if current_row == 0:
        return 1.00
    
    cfg = TOWER_DIFFICULTIES[difficulty]
    safe_tiles = cfg["tiles_per_row"] - cfg["bombs_per_row"]
    win_prob = safe_tiles / cfg["tiles_per_row"]
    
    mult = (1.0 / win_prob) ** current_row * 0.99
    return round(mult, 2)


class TowerDirectionButton(discord.ui.Button):
    def __init__(self, col_idx: int, label: str):
        super().__init__(
            style=discord.ButtonStyle.secondary, 
            label=label, 
            row=0
        )
        self.col_idx = col_idx

    async def callback(self, interaction: discord.Interaction):
        await self.view.process_step(interaction, self.col_idx)


class TowersGameView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, amount: int, difficulty: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.amount = amount
        self.difficulty = difficulty
        self.current_row = 0
        self.game_over = False
        self.hit_row = None
        self.hit_col = None

        cfg = TOWER_DIFFICULTIES[difficulty]
        self.tiles_per_row = cfg["tiles_per_row"]
        self.bombs_per_row = cfg["bombs_per_row"]
        self.labels = cfg["labels"]

        self.choices = [None] * TOTAL_TOWER_ROWS

        self.tower_bombs = []
        for _ in range(TOTAL_TOWER_ROWS):
            bomb_indices = set(random.sample(range(self.tiles_per_row), self.bombs_per_row))
            self.tower_bombs.append(bomb_indices)

        for col_idx, label in enumerate(self.labels):
            self.add_item(TowerDirectionButton(col_idx, label))

        self.cash_out_btn = discord.ui.Button(
            label="💰 Cash Out",
            style=discord.ButtonStyle.success,
            row=1
        )
        self.cash_out_btn.callback = self.cash_out_callback
        self.add_item(self.cash_out_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ This game is not yours!", ephemeral=True)
            return False
        if await game_paused(interaction):
            return False
        return True

    def get_current_mult(self) -> float:
        return calculate_towers_multiplier(self.difficulty, self.current_row)

    def get_next_mult(self) -> float:
        if self.current_row >= TOTAL_TOWER_ROWS:
            return self.get_current_mult()
        return calculate_towers_multiplier(self.difficulty, self.current_row + 1)

    def render_board(self, reveal_all=False) -> str:
        board_rows = []
        for r in range(TOTAL_TOWER_ROWS - 1, -1, -1):
            row_emojis = []
            for c in range(self.tiles_per_row):
                if reveal_all or r < self.current_row:
                    if r == self.hit_row and c == self.hit_col:
                        row_emojis.append("💥")
                    elif c in self.tower_bombs[r]:
                        row_emojis.append("💣")
                    else:
                        row_emojis.append("✅")
                else:
                    row_emojis.append("⬛")
            board_rows.append("".join(row_emojis))

        return "\n".join(board_rows)

    def build_embed(self, status="in_progress"):
        curr_m = self.get_current_mult()
        next_m = self.get_next_mult()
        
        winnings = int(self.amount * curr_m)
        next_click = int(self.amount * next_m)

        board_display = self.render_board(reveal_all=self.game_over)

        if status == "cashed_out":
            colour = discord.Colour.green()
        elif status == "lost":
            colour = discord.Colour.red()
        else:
            colour = discord.Colour.purple()

        desc = (
            f"**Game Stats**\n"
            f"💎 **Bet:** {format_amount(self.amount)}\n"
            f"✨ **Multiplier:** {curr_m:.2f}x\n"
            f"💎 **Winnings:** {format_amount(winnings)}\n"
            f"⏳ **Next click:** {format_amount(next_click)}\n\n"
            f"{board_display}"
        )

        embed = discord.Embed(
            title=f"Towers | {self.user_name}",
            description=desc,
            colour=colour
        )
        return embed

    async def process_step(self, interaction: discord.Interaction, col_idx: int):
        if self.game_over:
            return

        row = self.current_row
        self.choices[row] = col_idx
        is_bomb = col_idx in self.tower_bombs[row]

        if is_bomb:
            self.game_over = True
            self.hit_row = row
            self.hit_col = col_idx

            for item in self.children:
                item.disabled = True

            user = ensure_user(self.owner_id)
            user["wagered"] += self.amount
            add_history(self.owner_id, f"Towers ({self.difficulty})", self.amount, "Loss")
            
            DATA["global_stats"]["bot_game_profit"] += self.amount
            save_data()

            await update_milestone_roles(interaction.user)

            embed = self.build_embed(status="lost")
            await interaction.response.edit_message(embed=embed, view=self)

            await send_log(
                interaction.guild,
                "🏰 Towers Game Lost",
                f"Player: {interaction.user.mention}\nAmount: **{format_amount(self.amount)}**\nDifficulty: **{self.difficulty.title()}**\nReached Level: **{self.current_row}**",
                discord.Colour.red()
            )
            self.stop()
        else:
            self.current_row += 1

            if self.current_row >= TOTAL_TOWER_ROWS:
                await self.execute_cashout(interaction)
                return

            embed = self.build_embed(status="in_progress")
            await interaction.response.edit_message(embed=embed, view=self)

    async def cash_out_callback(self, interaction: discord.Interaction):
        if self.current_row == 0:
            await interaction.response.send_message("❌ You must complete at least 1 row to cash out!", ephemeral=True)
            return
        await self.execute_cashout(interaction)

    async def execute_cashout(self, interaction: discord.Interaction):
        self.game_over = True
        mult = self.get_current_mult()
        payout = int(self.amount * mult)
        net_profit = payout - self.amount

        user = ensure_user(self.owner_id)
        user["balance"] += payout
        user["wagered"] += self.amount
        add_history(self.owner_id, f"Towers ({self.difficulty})", self.amount, "Win")

        DATA["global_stats"]["bot_game_profit"] -= net_profit
        save_data()

        await update_milestone_roles(interaction.user)

        for item in self.children:
            item.disabled = True

        embed = self.build_embed(status="cashed_out")
        
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

        await send_log(
            interaction.guild,
            "🏰 Towers Cashed Out",
            f"Player: {interaction.user.mention}\nAmount: **{format_amount(self.amount)}**\nDifficulty: **{self.difficulty.title()}**\nPayout: **{format_amount(payout)}** ({mult:.2f}x)",
            discord.Colour.green()
        )
        self.stop()


@tree.command(name="towers", description="Play Aqua Gems Casino Towers game.")
@app_commands.describe(
    difficulty="Select difficulty mode",
    bet="Bet amount (e.g., 10m, 500m, 1b)"
)
@app_commands.choices(difficulty=[
    app_commands.Choice(name="Easy (3 tiles, 1 bomb)", value="easy"),
    app_commands.Choice(name="Medium (2 tiles, 1 bomb)", value="medium"),
    app_commands.Choice(name="Hard (3 tiles, 2 bombs)", value="hard")
])
async def towers(interaction: discord.Interaction, difficulty: app_commands.Choice[str], bet: str):
    if not await verification_check(interaction) or await game_paused(interaction):
        return

    parsed = parse_amount(bet)
    if parsed is None or parsed < MIN_GAME_AMOUNT:
        await interaction.response.send_message("❌ Minimum bet amount is **10M**.", ephemeral=True)
        return

    user = ensure_user(interaction.user.id)
    if user["balance"] < parsed:
        await interaction.response.send_message("❌ Insufficient balance.", ephemeral=True)
        return

    user["balance"] -= parsed
    save_data()

    view = TowersGameView(interaction.user.id, interaction.user.display_name, parsed, difficulty.value)
    embed = view.build_embed(status="in_progress")

    await interaction.response.send_message(embed=embed, view=view)


# ============================================================
# AFFILIATES & RAIN
# ============================================================

@tree.command(name="affiliates", description="View or claim affiliate referrals.")
@app_commands.describe(code="Affiliate Code to redeem (optional)")
async def affiliates(interaction, code: str = None):
    if not await verification_check(interaction):
        return

    user = ensure_user(interaction.user.id)

    if code:
        if user["referred_by"]:
            await interaction.response.send_message("❌ You have already redeemed an affiliate code.", ephemeral=True)
            return

        for owner_id, udata in DATA["users"].items():
            if udata.get("affiliate_code") == code.upper():
                if owner_id == str(interaction.user.id):
                    await interaction.response.send_message("❌ You cannot use your own code.", ephemeral=True)
                    return

                user["referred_by"] = code.upper()
                save_data()
                await interaction.response.send_message(f"✅ Successfully linked to affiliate code `{code.upper()}`!", ephemeral=True)
                return

        await interaction.response.send_message("❌ Invalid affiliate code.", ephemeral=True)
        return

    embed = normal_embed(
        "🤝 Affiliate Program",
        f"Your Affiliate Code: `{user['affiliate_code']}`\n\n"
        f"Share your code with friends to earn bonuses on their games!"
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="rain", description="Start a balance rain in the server.")
@app_commands.describe(amount="Amount to throw", duration="Duration in minutes")
async def rain(interaction, amount: str, duration: int = 5):
    if not await verification_check(interaction):
        return

    parsed = parse_amount(amount)
    if parsed is None or parsed < 1_000_000:
        await interaction.response.send_message("❌ Minimum rain amount is 1M.", ephemeral=True)
        return

    user = ensure_user(interaction.user.id)
    if user["balance"] < parsed:
        await interaction.response.send_message("❌ Insufficient balance.", ephemeral=True)
        return

    user["balance"] -= parsed
    save_data()

    participants = set()

    class RainView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=duration * 60)

        @discord.ui.button(label="Join Rain 🌧️", style=discord.ButtonStyle.primary)
        async def join(self, idx, button):
            participants.add(idx.user.id)
            await idx.response.send_message("✅ You joined the rain!", ephemeral=True)

    embed = normal_embed(
        "🌧️ Rain Event Started!",
        f"{interaction.user.mention} is raining **💎 {format_amount(parsed)}**!\n"
        f"Ends in **{duration} minutes**. Click below to participate!"
    )

    msg = await interaction.channel.send(embed=embed, view=RainView())
    await interaction.response.send_message("🌧️ Rain started!", ephemeral=True)

    await asyncio.sleep(duration * 60)

    if not participants:
        user["balance"] += parsed
        save_data()
        await interaction.channel.send("🌧️ Rain ended, but no one joined! Amount refunded.")
        return

    share = parsed // len(participants)
    for p_id in participants:
        p_user = ensure_user(p_id)
        p_user["balance"] += share

    save_data()
    await interaction.channel.send(
        f"🌧️ **Rain Ended!**\n"
        f"Distributed **💎 {format_amount(parsed)}** among **{len(participants)}** players "
        f"(**💎 {format_amount(share)}** each)!"
    )


# ============================================================
# DEPOSIT / WITHDRAW TICKETS
# ============================================================

def sanitize_channel_name(name):
    cleaned = "".join(c for c in name.lower() if c.isalnum() or c in "-_")
    return cleaned[:80] or "user"


async def create_ticket_channel(guild, member, kind):
    category_id = DEPOSIT_CATEGORY_ID if kind == "deposit" else WITHDRAW_CATEGORY_ID
    category = guild.get_channel(category_id)
    staff_role = guild.get_role(STAFF_ROLE_ID)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    channel_name = f"{kind}-{sanitize_channel_name(member.name)}"

    channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic=f"{kind.title()} ticket for {member} ({member.id})",
        reason=f"{kind.title()} ticket opened by {member}"
    )

    return channel


async def send_ticket_log(guild, channel_id, title, ticket, staff_member, colour):
    channel = guild.get_channel(channel_id)
    if channel is None:
        return
    try:
        await channel.send(
            embed=normal_embed(
                title,
                f"👤 User: <@{ticket['user_id']}>\n"
                f"🎮 Roblox: **{ticket['roblox_username']}**\n"
                f"💎 Amount: **{format_amount(ticket['amount'])}**\n"
                f"🛡️ Handled by: {staff_member.mention}",
                colour
            )
        )
    except Exception:
        pass


async def close_ticket_channel(channel):
    try:
        await channel.send("🔒 **This ticket will close in 10 seconds...**")
        await asyncio.sleep(10)
        await channel.delete()
    except Exception:
        pass


class DepositTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Approve & Credit", emoji="✅", style=discord.ButtonStyle.success, custom_id="deposit_approve")
    async def approve(self, interaction, button):
        ticket = DATA["tickets"].get(str(interaction.channel.id))
        if not ticket or ticket.get("status") != "open":
            await interaction.response.send_message("❌ This ticket is no longer active.", ephemeral=True)
            return

        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return

        user = ensure_user(ticket["user_id"])
        user["balance"] += ticket["amount"]
        DATA["global_stats"]["total_deposits"] += ticket["amount"]
        ticket["status"] = "approved"
        save_data()

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        await interaction.channel.send(
            embed=normal_embed(
                "✅ Deposit Approved",
                f"{interaction.user.mention} credited **💎 {format_amount(ticket['amount'])}** to <@{ticket['user_id']}>.",
                discord.Colour.green()
            )
        )

        await send_ticket_log(interaction.guild, DEPOSIT_LOG_CHANNEL_ID, "✅ Deposit Approved", ticket, interaction.user, discord.Colour.green())
        await send_log(interaction.guild, "Deposit Approved", f"Amount: {ticket['amount']}", discord.Colour.green())
        await close_ticket_channel(interaction.channel)

    @discord.ui.button(label="Deny", emoji="❌", style=discord.ButtonStyle.danger, custom_id="deposit_deny")
    async def deny(self, interaction, button):
        ticket = DATA["tickets"].get(str(interaction.channel.id))
        if not ticket or ticket.get("status") != "open":
            await interaction.response.send_message("❌ This ticket is no longer active.", ephemeral=True)
            return

        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return

        ticket["status"] = "denied"
        save_data()

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        await interaction.channel.send(
            embed=normal_embed("❌ Deposit Denied", f"Denied by {interaction.user.mention}.", discord.Colour.red())
        )

        await send_ticket_log(interaction.guild, DEPOSIT_LOG_CHANNEL_ID, "❌ Deposit Denied", ticket, interaction.user, discord.Colour.red())
        await close_ticket_channel(interaction.channel)


class WithdrawTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Mark Paid", emoji="✅", style=discord.ButtonStyle.success, custom_id="withdraw_paid")
    async def mark_paid(self, interaction, button):
        ticket = DATA["tickets"].get(str(interaction.channel.id))
        if not ticket or ticket.get("status") != "open":
            await interaction.response.send_message("❌ This ticket is no longer active.", ephemeral=True)
            return

        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return

        DATA["global_stats"]["total_withdraws"] += ticket["amount"]
        ticket["status"] = "paid"
        save_data()

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        await interaction.channel.send(
            embed=normal_embed(
                "✅ Withdrawal Paid",
                f"{interaction.user.mention} confirmed payout of **💎 {format_amount(ticket['amount'])}** to <@{ticket['user_id']}>.",
                discord.Colour.green()
            )
        )

        await send_ticket_log(interaction.guild, WITHDRAW_LOG_CHANNEL_ID, "✅ Withdrawal Paid", ticket, interaction.user, discord.Colour.green())
        await send_log(interaction.guild, "Withdrawal Paid", f"Amount: {ticket['amount']}", discord.Colour.green())
        await close_ticket_channel(interaction.channel)

    @discord.ui.button(label="Deny & Refund", emoji="❌", style=discord.ButtonStyle.danger, custom_id="withdraw_deny")
    async def deny(self, interaction, button):
        ticket = DATA["tickets"].get(str(interaction.channel.id))
        if not ticket or ticket.get("status") != "open":
            await interaction.response.send_message("❌ This ticket is no longer active.", ephemeral=True)
            return

        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return

        user = ensure_user(ticket["user_id"])
        user["balance"] += ticket["amount"]
        ticket["status"] = "denied"
        save_data()

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        await interaction.channel.send(
            embed=normal_embed(
                "❌ Withdrawal Denied",
                f"Denied by {interaction.user.mention}. **💎 {format_amount(ticket['amount'])}** refunded to <@{ticket['user_id']}>.",
                discord.Colour.red()
            )
        )

        await send_ticket_log(interaction.guild, WITHDRAW_LOG_CHANNEL_ID, "❌ Withdrawal Denied", ticket, interaction.user, discord.Colour.red())
        await close_ticket_channel(interaction.channel)


@tree.command(name="deposit", description="Open a ticket to deposit Robux for gems.")
@app_commands.describe(amount="Example: 10m, 500m, 1b", roblox_username="Your Roblox username")
async def deposit(interaction, amount: str, roblox_username: str):
    if not await verification_check(interaction):
        return

    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0:
        await interaction.response.send_message("❌ Invalid amount.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    channel = await create_ticket_channel(interaction.guild, interaction.user, "deposit")

    embed = normal_embed(
        "💰 Deposit Ticket",
        f"👤 **User:** {interaction.user.mention}\n"
        f"🎮 **Roblox Username:** `{roblox_username}`\n"
        f"💎 **Amount:** **{format_amount(parsed)}**\n\n"
        f"⏳ Please wait for staff to assist you!",
        discord.Colour.gold()
    )

    view = DepositTicketView()
    staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
    ping = staff_role.mention if staff_role else ""

    ticket_msg = await channel.send(content=f"{interaction.user.mention} {ping}", embed=embed, view=view)

    DATA["tickets"][str(channel.id)] = {
        "type": "deposit",
        "user_id": interaction.user.id,
        "roblox_username": roblox_username,
        "amount": parsed,
        "status": "open",
        "message_id": ticket_msg.id
    }
    save_data()

    await interaction.followup.send(f"✅ Deposit ticket created: {channel.mention}", ephemeral=True)


@tree.command(name="withdraw", description="Open a ticket to withdraw gems for Robux.")
@app_commands.describe(amount="Example: 10m, 500m, 1b", roblox_username="Your Roblox username")
async def withdraw(interaction, amount: str, roblox_username: str):
    if not await verification_check(interaction):
        return

    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0:
        await interaction.response.send_message("❌ Invalid amount.", ephemeral=True)
        return

    user = ensure_user(interaction.user.id)
    if user["balance"] < parsed:
        await interaction.response.send_message("❌ Insufficient balance.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    user["balance"] -= parsed
    save_data()

    channel = await create_ticket_channel(interaction.guild, interaction.user, "withdraw")

    embed = normal_embed(
        "💸 Withdrawal Ticket",
        f"👤 **User:** {interaction.user.mention}\n"
        f"🎮 **Roblox Username:** `{roblox_username}`\n"
        f"💎 **Amount:** **{format_amount(parsed)}** (held from balance)\n\n"
        f"⏳ Please wait for staff to assist you!",
        discord.Colour.gold()
    )

    view = WithdrawTicketView()
    staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
    ping = staff_role.mention if staff_role else ""

    ticket_msg = await channel.send(content=f"{interaction.user.mention} {ping}", embed=embed, view=view)

    DATA["tickets"][str(channel.id)] = {
        "type": "withdraw",
        "user_id": interaction.user.id,
        "roblox_username": roblox_username,
        "amount": parsed,
        "status": "open",
        "message_id": ticket_msg.id
    }
    save_data()

    await interaction.followup.send(f"✅ Withdrawal ticket created: {channel.mention}", ephemeral=True)


# ============================================================
# ANIMATED BLACKJACK
# ============================================================

def card():
    return random.randint(2, 11)


def blackjack_total(cards):
    total = sum(cards)
    aces = cards.count(11)

    while total > 21 and aces:
        total -= 10
        aces -= 1

    return total


class AnimatedBlackjackView(discord.ui.View):
    def __init__(self, owner_id, amount):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.amount = amount

        self.player = []
        self.dealer = []
        self.finished = False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ This blackjack game isn't yours.", ephemeral=True)
            return False

        if await game_paused(interaction):
            return False

        return True

    def build_embed(self, title="🃏 Blackjack Table", description="", reveal_dealer=False, status=None, status_colour=None):
        embed = normal_embed(title, description, status_colour or discord.Colour.blurple())

        p_cards = " ".join(f"[{x}]" for x in self.player) if self.player else "—"
        embed.add_field(
            name="👤 Your Hand",
            value=f"Cards: {p_cards}\nTotal Value: **{blackjack_total(self.player)}**",
            inline=True
        )

        if reveal_dealer or self.finished:
            d_cards = " ".join(f"[{x}]" for x in self.dealer) if self.dealer else "—"
            embed.add_field(
                name="🤖 Dealer Hand",
                value=f"Cards: {d_cards}\nTotal Value: **{blackjack_total(self.dealer)}**",
                inline=True
            )
        elif self.dealer:
            embed.add_field(
                name="🤖 Dealer Hand",
                value=f"Cards: [{self.dealer[0]}] [❓]\nTotal Value: **?**",
                inline=True
            )
        else:
            embed.add_field(name="🤖 Dealer Hand", value="Cards: —\nTotal Value: **?**", inline=True)

        embed.add_field(name="💰 Wager", value=f"{format_amount(self.amount)}", inline=False)

        if status:
            embed.add_field(name="🎰 Status", value=status, inline=False)

        embed.set_footer(text="Aqua Gems")
        return embed

    @discord.ui.button(label="Hit", emoji="🃏", style=discord.ButtonStyle.primary)
    async def hit(self, interaction, button):
        await interaction.response.edit_message(
            embed=self.build_embed(description="🎴 Drawing a card..."),
            view=None
        )
        await asyncio.sleep(0.8)

        self.player.append(card())

        if blackjack_total(self.player) > 21:
            await self.finish(interaction, "loss")
            return

        await interaction.edit_original_response(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Stand", emoji="✋", style=discord.ButtonStyle.success)
    async def stand(self, interaction, button):
        await self.finish(interaction, None)

    @discord.ui.button(label="Double Down", emoji="💵", style=discord.ButtonStyle.secondary)
    async def double_down(self, interaction, button):
        if len(self.player) != 2:
            await interaction.response.send_message("❌ You can only double down before hitting.", ephemeral=True)
            return

        user = ensure_user(self.owner_id)
        if user["balance"] < self.amount:
            await interaction.response.send_message("❌ Insufficient balance to double down.", ephemeral=True)
            return

        self.amount *= 2

        await interaction.response.edit_message(
            embed=self.build_embed(description="💵 Doubling down — drawing your final card..."),
            view=None
        )
        await asyncio.sleep(0.8)

        self.player.append(card())

        if blackjack_total(self.player) > 21:
            await self.finish(interaction, "loss")
            return

        await self.finish(interaction, None)

    async def finish(self, interaction, forced_result):
        self.finished = True

        if not interaction.response.is_done():
            await interaction.response.edit_message(
                embed=self.build_embed(description="🎴 Dealer is revealing their hidden card...", reveal_dealer=True),
                view=None
            )
        else:
            await interaction.edit_original_response(
                embed=self.build_embed(description="🎴 Dealer is revealing their hidden card...", reveal_dealer=True),
                view=None
            )

        await asyncio.sleep(1.0)

        while blackjack_total(self.dealer) < 17 and forced_result != "loss":
            self.dealer.append(card())
            await interaction.edit_original_response(
                embed=self.build_embed(description="🎴 Dealer hits another card...", reveal_dealer=True),
                view=None
            )
            await asyncio.sleep(1.0)

        player_total = blackjack_total(self.player)
        dealer_total = blackjack_total(self.dealer)
        user = ensure_user(self.owner_id)

        if forced_result == "loss" or player_total > 21:
            result = "Loss"
        elif dealer_total > 21 or player_total > dealer_total:
            result = "Win"
        elif player_total == dealer_total:
            result = "Push"
        else:
            result = "Loss"

        if result == "Win":
            user["balance"] += self.amount
            DATA["global_stats"]["bot_game_profit"] -= self.amount
            status = f"🏆 **You Win!** ({player_total} vs {dealer_total})\nPayout: **{format_amount(self.amount)}**"
            colour = discord.Colour.green()
        elif result == "Loss":
            user["balance"] -= min(self.amount, user["balance"])
            DATA["global_stats"]["bot_game_profit"] += self.amount
            status = f"💀 **You Lost!** ({player_total} vs {dealer_total})\nLost: **{format_amount(self.amount)}**"
            colour = discord.Colour.red()
        else:
            status = f"🟡 **Push!** ({player_total} vs {dealer_total})\nWager returned."
            colour = discord.Colour.orange()

        user["wagered"] += self.amount
        add_history(self.owner_id, "Blackjack", self.amount, result)
        save_data()

        await update_milestone_roles(interaction.user)

        final_embed = self.build_embed(reveal_dealer=True, status=status, status_colour=colour)

        await interaction.edit_original_response(embed=final_embed, view=None)

        await send_log(
            interaction.guild,
            "🃏 Blackjack Finished",
            f"Player: {interaction.user.mention}\nAmount: **{format_amount(self.amount)}**\nResult: **{result}**",
            colour
        )
        self.stop()


@tree.command(name="blackjack", description="Play animated Blackjack.")
@app_commands.describe(amount="Example: 10m or 1b")
async def blackjack(interaction, amount: str):
    if not await verification_check(interaction):
        return

    if await game_paused(interaction):
        return

    parsed = parse_amount(amount)
    if parsed is None or parsed < MIN_GAME_AMOUNT:
        await interaction.response.send_message("❌ Minimum amount is **10M**.", ephemeral=True)
        return

    user = ensure_user(interaction.user.id)
    if user["balance"] < parsed:
        await interaction.response.send_message("❌ Insufficient balance.", ephemeral=True)
        return

    view = AnimatedBlackjackView(interaction.user.id, parsed)

    await interaction.response.send_message(
        embed=normal_embed("🃏 Blackjack Table", "🎰 Shuffling & dealing...", discord.Colour.blurple())
    )

    try:
        await asyncio.sleep(3)

        view.player = [card(), card()]
        view.dealer = [card(), card()]

        if blackjack_total(view.player) == 21:
            await view.finish(interaction, None)
            return

        await interaction.edit_original_response(
            embed=view.build_embed(description="Choose your move!"),
            view=view
        )
    except Exception as e:
        print(f"Blackjack deal error: {e}")
        try:
            await interaction.edit_original_response(
                embed=normal_embed(
                    "❌ Blackjack Error",
                    "Something went wrong starting the game. Your balance was not affected.",
                    discord.Colour.red()
                ),
                view=None
            )
        except Exception:
            pass


# ============================================================
# COINFLIP
# ============================================================

class CoinflipView(discord.ui.View):
    def __init__(self, creator: discord.Member, amount_str: str, raw_amount: int, side: str = "heads"):
        super().__init__(timeout=None)
        self.creator = creator
        self.amount_str = amount_str
        self.raw_amount = raw_amount
        self.creator_side = side.lower()
        self.opponent = None
        self.game_over = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if DATA["settings"]["paused"]:
            await interaction.response.send_message("⏸️ **Games are paused.**", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Join", style=discord.ButtonStyle.blurple, emoji="👤")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id == self.creator.id:
            await interaction.response.send_message("You cannot join your own coinflip!", ephemeral=True)
            return

        if self.opponent:
            await interaction.response.send_message("This coinflip already has an opponent!", ephemeral=True)
            return

        opp = ensure_user(interaction.user.id)
        if opp["balance"] < self.raw_amount:
            await interaction.response.send_message("❌ Insufficient balance to join.", ephemeral=True)
            return

        self.opponent = interaction.user
        self.game_over = True
        opponent_side = "tails" if self.creator_side == "heads" else "heads"

        winner = random.choice([self.creator, self.opponent])
        loser = self.opponent if winner == self.creator else self.creator
        winning_side = self.creator_side if winner == self.creator else opponent_side

        creator_user = ensure_user(self.creator.id)
        opponent_user = ensure_user(self.opponent.id)

        if winner == self.creator:
            creator_user["balance"] += self.raw_amount
            opponent_user["balance"] -= min(self.raw_amount, opponent_user["balance"])
        else:
            opponent_user["balance"] += self.raw_amount
            creator_user["balance"] -= min(self.raw_amount, creator_user["balance"])

        creator_user["wagered"] += self.raw_amount
        opponent_user["wagered"] += self.raw_amount

        add_history(self.creator.id, "Coinflip (PvP)", self.raw_amount, "Win" if winner == self.creator else "Loss")
        add_history(self.opponent.id, "Coinflip (PvP)", self.raw_amount, "Win" if winner == self.opponent else "Loss")
        save_data()

        await update_milestone_roles(self.creator)
        await update_milestone_roles(self.opponent)

        embed = normal_embed("🪙 Coinflip Result", colour=discord.Colour.green())
        embed.add_field(name="Result", value=f"**{winning_side.upper()}**", inline=False)
        embed.add_field(name="", value=f"🟢 **{winner.mention} WON!**", inline=False)
        embed.add_field(name="", value=f"+💎 {self.amount_str}", inline=False)

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

        await send_log(
            interaction.guild,
            "🪙 Coinflip PvP",
            f"Winner: {winner.mention}\nLoser: {loser.mention}\nAmount: **{self.amount_str}**",
            discord.Colour.green()
        )

    @discord.ui.button(label="Call Bot", style=discord.ButtonStyle.secondary, emoji="🤖")
    async def call_bot_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.creator.id:
            await interaction.response.send_message("Only the creator can trigger the bot!", ephemeral=True)
            return

        if self.opponent:
            await interaction.response.send_message("An opponent has already joined!", ephemeral=True)
            return

        self.game_over = True
        creator_user = ensure_user(self.creator.id)
        outcomes = ["heads", "tails"]
        result = random.choice(outcomes)
        user_won = (result == self.creator_side)

        if user_won:
            creator_user["balance"] += self.raw_amount
            DATA["global_stats"]["bot_game_profit"] -= self.raw_amount
            status_text = "🟢 **YOU WIN!**"
            amount_text = f"+💎 {self.amount_str}"
            colour = discord.Colour.green()
        else:
            creator_user["balance"] -= min(self.raw_amount, creator_user["balance"])
            DATA["global_stats"]["bot_game_profit"] += self.raw_amount
            status_text = "🔴 **YOU LOST!**"
            amount_text = f"-💎 {self.amount_str}"
            colour = discord.Colour.red()

        creator_user["wagered"] += self.raw_amount
        add_history(self.creator.id, "Coinflip (vs Bot)", self.raw_amount, "Win" if user_won else "Loss")
        save_data()

        await update_milestone_roles(self.creator)

        embed = normal_embed("🪙 Coinflip vs Bot — Result", colour=colour)
        embed.add_field(name="Result", value=f"**{result.upper()}**", inline=False)
        embed.add_field(name="", value=status_text, inline=False)
        embed.add_field(name="", value=amount_text, inline=False)

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

        await send_log(
            interaction.guild,
            "🪙 Coinflip vs Bot",
            f"Player: {self.creator.mention}\nAmount: **{self.amount_str}**\nResult: **{'Win' if user_won else 'Loss'}**",
            colour
        )

    @discord.ui.button(label="Flip", style=discord.ButtonStyle.success, emoji="🪙")
    async def flip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.call_bot_button(interaction, button)


@tree.command(name="coinflip", description="Create a coinflip bet.")
@app_commands.describe(amount="The amount of gems to bet (e.g. 10m, 500m, 1b)", side="Choose heads or tails (default: heads)")
@app_commands.choices(side=[
    app_commands.Choice(name="Heads", value="heads"),
    app_commands.Choice(name="Tails", value="tails")
])
async def coinflip(interaction: discord.Interaction, amount: str, side: app_commands.Choice[str] = None):
    if not await verification_check(interaction) or await game_paused(interaction):
        return

    parsed = parse_amount(amount)
    if parsed is None or parsed < MIN_GAME_AMOUNT:
        await interaction.response.send_message("❌ Minimum bet amount is **10M**.", ephemeral=True)
        return

    user = ensure_user(interaction.user.id)
    if user["balance"] < parsed:
        await interaction.response.send_message("❌ Insufficient balance.", ephemeral=True)
        return

    chosen_side = side.value if side else "heads"
    formatted_bet = format_amount(parsed)

    await interaction.response.send_message(
        f"✅ {interaction.user.mention} created a coinflip!",
        ephemeral=False
    )

    heads_val = interaction.user.mention if chosen_side == "heads" else "No one"
    tails_val = interaction.user.mention if chosen_side == "tails" else "No one"

    target_channel = interaction.client.get_channel(COINFLIP_CHANNEL_ID)
    if not target_channel:
        target_channel = await interaction.client.fetch_channel(COINFLIP_CHANNEL_ID)

    embed = normal_embed("🪙 Coinflip", "Choose your side!", discord.Colour.blue())
    embed.set_author(name="Aqua Gems Casino", icon_url=interaction.client.user.display_avatar.url)
    embed.add_field(name="Heads", value=heads_val, inline=False)
    embed.add_field(name="Tails", value=tails_val, inline=False)
    embed.add_field(name="Bet Amount", value=f"💎 {formatted_bet}", inline=False)

    view = CoinflipView(
        creator=interaction.user,
        amount_str=formatted_bet,
        raw_amount=parsed,
        side=chosen_side
    )
    
    gui_message = await target_channel.send(embed=embed, view=view)

    is_active = True
    elapsed_time = 0
    timeout_limit = 300

    while is_active:
        await asyncio.sleep(5)
        elapsed_time += 5

        if view.game_over:
            is_active = False

        elif elapsed_time >= timeout_limit:
            is_active = False
            for child in view.children:
                child.disabled = True

            embed.description = "⏰ **Coinflip expired!**"
            try:
                await gui_message.edit(embed=embed, view=view)
            except Exception:
                pass


# ============================================================
# STARTUP & RUN
# ============================================================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    
    bot.add_view(DepositTicketView())
    bot.add_view(WithdrawTicketView())
    
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

if __name__ == "__main__":
    if not TOKEN:
        print("❌ ERROR: Discord_token environment variable is missing or empty!")
    else:
        print("🚀 Starting bot...")
        bot.run(TOKEN)
