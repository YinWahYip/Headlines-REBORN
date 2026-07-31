import asyncio
import json
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
import fetcher
import pipeline
from config import DISCORD_TOKEN, FETCH_INTERVAL_MINUTES, CATEGORIES, FEEDS

# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

last_fetched_at: datetime | None = None


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

    # Timestamp: when the cluster was processed
    created_at = cluster.get("created_at")
    footer_parts = ["  ·  ".join(outlets)]
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at)
            footer_parts.append(dt.strftime("Pulled %b %d %H:%M UTC"))
        except ValueError:
            pass
    embed.set_footer(text="  ·  ".join(footer_parts))
    return embed


def apply_filters(clusters: list, sub: dict) -> list:
    """Apply category blacklist, category whitelist (focus), and source filter."""
    blacklist = json.loads(sub.get("blacklist") or "[]")
    whitelist = json.loads(sub.get("categories") or "[]")
    allowed_sources = json.loads(sub.get("sources") or "[]")
    result = clusters
    if blacklist:
        result = [c for c in result if c["category"] not in blacklist]
    if whitelist:
        result = [c for c in result if c["category"] in whitelist]
    if allowed_sources:
        # Keep cluster if at least one of its outlets is in the allowed list
        def has_allowed_source(c):
            outlets = json.loads(c["outlets"]) if isinstance(c["outlets"], str) else c["outlets"]
            return any(o in allowed_sources for o in outlets)
        result = [c for c in result if has_allowed_source(c)]
    return result


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
    digest_loop.start()
    print(f"[bot] Ready as {bot.user} — fetch every {FETCH_INTERVAL_MINUTES}min, digest check every hour")


# ── Background tasks ──────────────────────────────────────────────────────────

@tasks.loop(minutes=FETCH_INTERVAL_MINUTES)
async def fetch_loop():
    global last_fetched_at
    try:
        articles = fetcher.fetch_new_articles()
        last_fetched_at = datetime.utcnow()
        if articles:
            pipeline.process_articles(articles)
    except Exception as e:
        print(f"[fetch_loop] Error (will retry next cycle): {e}")


@tasks.loop(minutes=60)
async def digest_loop():
    """Check every hour whether any guild is due for a digest."""
    now = datetime.utcnow()
    subs = db.get_all_subscriptions()

    for sub in subs:
        interval_hours = sub.get("interval_hours", 24)
        last_posted = sub.get("last_posted_at")

        # Determine if this guild is due
        if last_posted:
            due_at = datetime.fromisoformat(last_posted) + timedelta(hours=interval_hours)
            if now < due_at:
                continue  # not yet

        channel = bot.get_channel(int(sub["channel_id"]))
        if not channel:
            continue

        clusters = db.get_unposted_clusters()
        filtered = apply_filters(clusters, sub)

        if not filtered:
            continue

        try:
            await send_clusters(channel, filtered, header=f"📰 News Digest (every {interval_hours}h)")
            db.update_last_posted(sub["guild_id"])
            db.mark_posted([c["id"] for c in filtered])
        except discord.Forbidden:
            print(f"[bot] No permission to post in channel {sub['channel_id']}")


# ── Slash commands ────────────────────────────────────────────────────────────

@bot.tree.command(name="digest", description="Get the latest news stories")
@app_commands.describe(
    category="Optional topic filter, e.g. Economics or China",
    limit="How many stories to show (default 5, max 20)",
)
async def cmd_digest(interaction: discord.Interaction, category: str = None, limit: int = 5):
    await interaction.response.defer()
    limit = max(1, min(limit, 20))

    async def _run():
        sub = db.get_subscription(str(interaction.guild_id))
        clusters = db.get_unposted_clusters(category_filter=category)
        if sub and not category:
            clusters = apply_filters(clusters, sub)
        header = f"📰 Latest: {category}" if category else "📰 Latest Stories"
        await send_clusters(interaction.channel, clusters[:limit], header=header)
        await interaction.followup.send("Done.", ephemeral=True)

    try:
        await asyncio.wait_for(_run(), timeout=10)
    except asyncio.TimeoutError:
        await interaction.followup.send("⏱ Timed out — try again in a moment.", ephemeral=True)


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


@bot.tree.command(name="blacklist", description="Block categories from appearing in digests (comma-separated, or 'clear')")
@app_commands.describe(topics="e.g. 'Sports, Other' or 'clear' to remove all blocks")
async def cmd_blacklist(interaction: discord.Interaction, topics: str = None):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("You need Manage Channels permission.", ephemeral=True)
        return

    sub = db.get_subscription(str(interaction.guild_id))
    if not sub:
        await interaction.response.send_message("No digest channel set. Run `/setup` first.", ephemeral=True)
        return

    # No arg — show current blacklist
    if topics is None:
        current = json.loads(sub.get("blacklist") or "[]")
        if current:
            await interaction.response.send_message(
                f"Currently blocked: **{', '.join(current)}**\nUse `/blacklist clear` to unblock all.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "No categories blocked. Use `/blacklist <topics>` to block some.", ephemeral=True
            )
        return

    if topics.strip().lower() == "clear":
        db.upsert_subscription(str(interaction.guild_id), sub["channel_id"], blacklist=[])
        await interaction.response.send_message("Blacklist cleared — all categories allowed.", ephemeral=True)
        return

    requested = [t.strip() for t in topics.split(",") if t.strip()]
    valid = [t for t in requested if t in CATEGORIES]
    invalid = [t for t in requested if t not in CATEGORIES]

    if not valid:
        await interaction.response.send_message(
            "None matched. Use `/categories` to see valid options.", ephemeral=True
        )
        return

    db.upsert_subscription(str(interaction.guild_id), sub["channel_id"], blacklist=valid)
    msg = f"Blocked: **{', '.join(valid)}** — these won't appear in digests."
    if invalid:
        msg += f"\nIgnored (not recognised): {', '.join(invalid)}"
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="status", description="Show bot status and last fetch time")
async def cmd_status(interaction: discord.Interaction):
    sub = db.get_subscription(str(interaction.guild_id))
    lines = []

    if last_fetched_at:
        lines.append(f"🕐 Last fetch: {last_fetched_at.strftime('%b %d %H:%M UTC')}")
    else:
        lines.append("🕐 Last fetch: not yet (restarts clear this)")

    if sub:
        interval = sub.get("interval_hours", 24)
        last_posted = sub.get("last_posted_at")
        lines.append(f"📬 Digest interval: every {interval}h")
        if last_posted:
            try:
                dt = datetime.fromisoformat(last_posted)
                lines.append(f"📤 Last digest posted: {dt.strftime('%b %d %H:%M UTC')}")
                next_post = dt + timedelta(hours=interval)
                lines.append(f"⏭ Next digest: {next_post.strftime('%b %d %H:%M UTC')}")
            except ValueError:
                pass
        blacklisted = json.loads(sub.get("blacklist") or "[]")
        focused = json.loads(sub.get("categories") or "[]")
        if blacklisted:
            lines.append(f"🚫 Blocked: {', '.join(blacklisted)}")
        if focused:
            lines.append(f"✅ Focus: {', '.join(focused)}")
    else:
        lines.append("⚠️ No digest channel set — run `/setup`")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="sources", description="List available news sources and which ones are active for this server")
async def cmd_sources(interaction: discord.Interaction):
    sub = db.get_subscription(str(interaction.guild_id))
    active = json.loads(sub.get("sources") or "[]") if sub else []

    lines = []
    for feed in FEEDS:
        name = feed["name"]
        check = "✓" if (not active or name in active) else "✗"
        lines.append(f"• {check} {name}")

    header = "**News sources** (✓ = included in your digest):"
    if active:
        header += f"\nUsing {len(active)} of {len(FEEDS)} sources. Use `/setsources all` to reset."
    await interaction.response.send_message(header + "\n" + "\n".join(lines), ephemeral=True)


@bot.tree.command(name="setsources", description="Choose which news sources to include (comma-separated, or 'all')")
@app_commands.describe(names="e.g. 'BBC, NPR' or 'all' to include everything")
async def cmd_setsources(interaction: discord.Interaction, names: str = None):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("You need Manage Channels permission.", ephemeral=True)
        return

    sub = db.get_subscription(str(interaction.guild_id))
    if not sub:
        await interaction.response.send_message("No digest channel set. Run `/setup` first.", ephemeral=True)
        return

    # No arg — show current
    if names is None:
        current = json.loads(sub.get("sources") or "[]")
        if current:
            await interaction.response.send_message(
                f"Active sources: **{', '.join(current)}**\nUse `/setsources all` to include everything.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "All sources are included. Use `/setsources <names>` to narrow it.", ephemeral=True
            )
        return

    if names.strip().lower() == "all":
        db.upsert_subscription(str(interaction.guild_id), sub["channel_id"], sources=[])
        await interaction.response.send_message("Reset — all sources included.", ephemeral=True)
        return

    available = {f["name"].lower(): f["name"] for f in FEEDS}
    requested = [n.strip() for n in names.split(",") if n.strip()]
    valid = [available[n.lower()] for n in requested if n.lower() in available]
    invalid = [n for n in requested if n.lower() not in available]

    if not valid:
        source_list = ", ".join(f["name"] for f in FEEDS)
        await interaction.response.send_message(
            f"None matched. Available sources: {source_list}", ephemeral=True
        )
        return

    db.upsert_subscription(str(interaction.guild_id), sub["channel_id"], sources=valid)
    msg = f"Now pulling from: **{', '.join(valid)}**"
    if invalid:
        msg += f"\nNot recognised: {', '.join(invalid)}"
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="setinterval", description="Set how often the digest posts automatically (in hours)")
@app_commands.describe(hours="Posting interval in hours, e.g. 6, 12, or 24")
async def cmd_setinterval(interaction: discord.Interaction, hours: int):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("You need Manage Channels permission.", ephemeral=True)
        return

    sub = db.get_subscription(str(interaction.guild_id))
    if not sub:
        await interaction.response.send_message(
            "No digest channel set. Run `/setup` first.", ephemeral=True
        )
        return

    if hours < 1 or hours > 168:
        await interaction.response.send_message(
            "Interval must be between 1 and 168 hours (1 week).", ephemeral=True
        )
        return

    cats = json.loads(sub["categories"])
    db.upsert_subscription(str(interaction.guild_id), sub["channel_id"], categories=cats, interval_hours=hours)
    await interaction.response.send_message(
        f"Digest will now post every **{hours} hour{'s' if hours != 1 else ''}**.", ephemeral=True
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def run():
    bot.run(DISCORD_TOKEN)
