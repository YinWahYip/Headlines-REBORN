import json
from datetime import datetime, time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
import fetcher
import pipeline
from config import DISCORD_TOKEN, FETCH_INTERVAL_MINUTES, DIGEST_HOUR_UTC, CATEGORIES

# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ── Formatting ────────────────────────────────────────────────────────────────

CATEGORY_COLORS = {
    "International Politics": 0x3498db,
    "US Politics":            0xe74c3c,
    "China":                  0xe67e22,
    "Middle East":            0x9b59b6,
    "Economics":              0x2ecc71,
    "Technology":             0x1abc9c,
    "Science":                0x0099ff,
    "Climate":                0x27ae60,
    "Sports":                 0xf39c12,
    "Other":                  0x95a5a6,
}

def make_embed(cluster: dict) -> discord.Embed:
    outlets = json.loads(cluster["outlets"]) if isinstance(cluster["outlets"], str) else cluster["outlets"]
    color = CATEGORY_COLORS.get(cluster["category"], 0x95a5a6)

    # Weight indicator — more outlets = more prominent label
    n = len(outlets)
    if n >= 4:
        weight = f"🔥 {n} sources"
    elif n >= 2:
        weight = f"📡 {n} sources"
    else:
        weight = None

    author = cluster["category"].upper()
    if weight:
        author = f"{author}  ·  {weight}"

    embed = discord.Embed(description=cluster["summary"], color=color)
    embed.set_author(name=author)

    # Add per-source article links
    articles = db.get_cluster_articles(cluster["id"])
    if articles:
        links = "  ·  ".join(f"[{a['source']}]({a['url']})" for a in articles)
        embed.add_field(name="Read more", value=links, inline=False)

    embed.set_footer(text="  ·  ".join(outlets))
    return embed


def chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


async def send_clusters(channel: discord.TextChannel, clusters: list, header: str = None):
    """Send clusters to a channel in batches of 10 (Discord embed limit)."""
    if not clusters:
        await channel.send("No new stories right now.")
        return
    if header:
        await channel.send(f"**{header}**")
    for batch in chunk(clusters, 10):
        embeds = [make_embed(c) for c in batch]
        await channel.send(embeds=embeds)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    db.init_db()
    await bot.tree.sync()
    fetch_loop.start()
    daily_digest.start()
    print(f"[bot] Ready as {bot.user} — fetch every {FETCH_INTERVAL_MINUTES}min, digest at {DIGEST_HOUR_UTC}:00 UTC")


# ── Background tasks ──────────────────────────────────────────────────────────

@tasks.loop(minutes=FETCH_INTERVAL_MINUTES)
async def fetch_loop():
    articles = fetcher.fetch_new_articles()
    if articles:
        pipeline.process_articles(articles)


@tasks.loop(time=time(hour=DIGEST_HOUR_UTC, minute=0))
async def daily_digest():
    clusters = db.get_unposted_clusters()
    if not clusters:
        return

    cluster_ids = [c["id"] for c in clusters]
    subs = db.get_all_subscriptions()

    for sub in subs:
        channel = bot.get_channel(int(sub["channel_id"]))
        if not channel:
            continue

        # Filter by subscribed categories if the guild has preferences set
        sub_cats = json.loads(sub["categories"])
        filtered = (
            [c for c in clusters if c["category"] in sub_cats]
            if sub_cats else clusters
        )

        try:
            await send_clusters(channel, filtered, header="📰 Daily News Digest")
        except discord.Forbidden:
            print(f"[bot] No permission to post in channel {sub['channel_id']}")

    db.mark_posted(cluster_ids)


# ── Slash commands ────────────────────────────────────────────────────────────

@bot.tree.command(name="digest", description="Get the latest news stories")
@app_commands.describe(
    category="Optional topic filter, e.g. Economics or China",
    limit="How many stories to show (default 5, max 20)",
)
async def cmd_digest(interaction: discord.Interaction, category: str = None, limit: int = 5):
    await interaction.response.defer()
    limit = max(1, min(limit, 20))  # clamp between 1 and 20
    clusters = db.get_unposted_clusters(category_filter=category)
    header = f"📰 Latest: {category}" if category else "📰 Latest Stories"
    await send_clusters(interaction.channel, clusters[:limit], header=header)
    await interaction.followup.send("Done.", ephemeral=True)


@bot.tree.command(name="setup", description="Set this channel to receive the daily digest")
async def cmd_setup(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("You need Manage Channels permission.", ephemeral=True)
        return
    db.upsert_subscription(str(interaction.guild_id), str(interaction.channel_id))
    await interaction.response.send_message(
        f"This channel will receive the daily digest at {DIGEST_HOUR_UTC}:00 UTC. "
        f"Use `/focus` to filter by topic.",
        ephemeral=True,
    )


@bot.tree.command(name="categories", description="List available topic categories")
async def cmd_categories(interaction: discord.Interaction):
    sub = db.get_subscription(str(interaction.guild_id))
    focused = json.loads(sub["categories"]) if sub else []

    lines = []
    for c in CATEGORIES:
        if c in focused:
            lines.append(f"• {c} ✓")
        else:
            lines.append(f"• {c}")

    header = "**Available categories** (✓ = in your focus):" if focused else "**Available categories:**"
    await interaction.response.send_message(
        header + "\n" + "\n".join(lines),
        ephemeral=True,
    )


@bot.tree.command(name="focus", description="Set topic focus for this server's digest (comma-separated, or 'all')")
@app_commands.describe(topics="e.g. 'Economics, Technology' or 'all' to reset")
async def cmd_focus(interaction: discord.Interaction, topics: str = None):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("You need Manage Channels permission.", ephemeral=True)
        return

    sub = db.get_subscription(str(interaction.guild_id))
    if not sub:
        await interaction.response.send_message(
            "No digest channel set. Run `/setup` first.", ephemeral=True
        )
        return

    # No arg — show current focus
    if topics is None:
        current = json.loads(sub["categories"])
        if current:
            await interaction.response.send_message(
                f"Current focus: **{', '.join(current)}**\nUse `/focus all` to receive everything.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "No focus set — receiving all categories. Use `/focus <topics>` to narrow it.",
                ephemeral=True,
            )
        return

    # 'all' clears the filter
    if topics.strip().lower() == "all":
        db.upsert_subscription(str(interaction.guild_id), sub["channel_id"], categories=[])
        await interaction.response.send_message("Focus cleared — digest will show all categories.", ephemeral=True)
        return

    # Parse comma-separated topics, validate against known categories
    requested = [t.strip() for t in topics.split(",") if t.strip()]
    valid = [t for t in requested if t in CATEGORIES]
    invalid = [t for t in requested if t not in CATEGORIES]

    if not valid:
        await interaction.response.send_message(
            f"None of those matched. Use `/categories` to see valid options.", ephemeral=True
        )
        return

    db.upsert_subscription(str(interaction.guild_id), sub["channel_id"], categories=valid)

    msg = f"Focus set to: **{', '.join(valid)}**"
    if invalid:
        msg += f"\nIgnored (not recognised): {', '.join(invalid)}"
    await interaction.response.send_message(msg, ephemeral=True)


# ── Entry point ───────────────────────────────────────────────────────────────

def run():
    bot.run(DISCORD_TOKEN)
