import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
import datetime
import os

# Initialize SQLite database for persistent candidate dossiers and trial history logs
DB_FILE = "scout_history.db"

def init_db():
    """Initializes the SQLite database with required tables for candidate history, dockets, and match stats."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Table for candidate evaluations and historical dossier
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL,
            candidate_name TEXT NOT NULL,
            scout_id TEXT NOT NULL,
            scout_name TEXT NOT NULL,
            position_group TEXT NOT NULL,
            score REAL NOT NULL,
            notes TEXT,
            timestamp TEXT NOT NULL
        )
    ''')
    
    # Table for active dockets/control panels
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS active_dockets (
            channel_id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            last_updated TEXT NOT NULL
        )
    ''')
    
    # Table for automated Friday app match recap performance metrics
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS match_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_handle TEXT NOT NULL,
            discord_id TEXT,
            match_date TEXT NOT NULL,
            rating REAL,
            goals INTEGER,
            assists INTEGER,
            passing_acc REAL,
            tackles INTEGER,
            raw_summary TEXT
        )
    ''')

    # Table for mapping EA/Friday account handles to Discord members
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS player_mappings (
            ea_handle TEXT PRIMARY KEY,
            discord_id TEXT NOT NULL,
            discord_name TEXT NOT NULL
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# Configure bot intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

class ScoutBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Sync slash commands globally or to a test guild
        await self.tree.sync()
        print(f"Synced slash commands for {self.user}")

bot = ScoutBot()


class ScorecardModal(discord.ui.Modal):
    def __init__(self, candidate: discord.Member, position_group: str):
        super().__init__(title=f"Scout Evaluation: {position_group}")
        self.candidate = candidate
        self.position_group = position_group

        self.rating_input = discord.ui.TextInput(
            label="Overall Rating (1.0 - 10.0)",
            placeholder="Enter score (e.g. 6.0, 8.5) - Note: 7.0 is disabled",
            min_length=1,
            max_length=4,
            required=True
        )
        
        self.notes_input = discord.ui.TextInput(
            label="Scout Notes & Feedback",
            style=discord.TextStyle.paragraph,
            placeholder="Detail performance, tactical awareness, and recommendations...",
            required=True,
            max_length=1000
        )

        self.add_item(self.rating_input)
        self.add_item(self.notes_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            score = float(self.rating_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ **Invalid Input:** Please enter a valid numerical rating between 1.0 and 10.0.",
                ephemeral=True
            )
            return

        # Enforce the "No-7.0" Scoring Rule (rejects 6.8 to 7.2 or exactly 7.0)
        if 6.8 <= score <= 7.2 or score == 7.0:
            await interaction.response.send_message(
                "⚠️ **Input Rejected:** Ratings of 7.0 are disabled. "
                "Decide if the candidate is below standard (6.0 or lower) or trial-ready (8.0 or higher).",
                ephemeral=True
            )
            return

        if not (1.0 <= score <= 10.0):
            await interaction.response.send_message(
                "❌ **Out of Range:** Rating must be between 1.0 and 10.0.",
                ephemeral=True
            )
            return

        notes = self.notes_input.value
        timestamp = datetime.datetime.utcnow().isoformat()

        # Save evaluation to SQLite database
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO evaluations (candidate_id, candidate_name, scout_id, scout_name, position_group, score, notes, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (str(self.candidate.id), self.candidate.display_name, str(interaction.user.id), interaction.user.display_name, self.position_group, score, notes, timestamp))
            conn.commit()
            conn.close()
        except Exception as e:
            await interaction.response.send_message(f"❌ Database error: {e}", ephemeral=True)
            return

        # Determine tier tag based on score
        status_tag = "🟢 Trial-Ready Standard" if score >= 8.0 else "🔴 Developmental Standard"

        embed = discord.Embed(
            title="📊 Candidate Evaluation Logged",
            description=f"Successfully recorded trial evaluation for **{self.candidate.mention}**.",
            color=discord.Color.green() if score >= 8.0 else discord.Color.orange()
        )
        embed.add_field(name="Candidate", value=self.candidate.mention, inline=True)
        embed.add_field(name="Position Group", value=self.position_group, inline=True)
        embed.add_field(name="Composite Score", value=f"⭐ **{score:.1f} / 10.0** ({status_tag})", inline=False)
        embed.add_field(name="Scout Notes", value=notes, inline=False)
        embed.set_footer(text=f"Evaluated by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        embed.timestamp = datetime.datetime.utcnow()

        await interaction.response.send_message(embed=embed, ephemeral=False)


class PositionSelectView(discord.ui.View):
    def __init__(self, candidate: discord.Member):
        super().__init__(timeout=60)
        self.candidate = candidate

    @discord.ui.button(label="Defender", style=discord.ButtonStyle.primary, emoji="🛡️")
    async def defender_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScorecardModal(self.candidate, "Defender"))

    @discord.ui.button(label="Midfielder", style=discord.ButtonStyle.primary, emoji="🧠")
    async def midfielder_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScorecardModal(self.candidate, "Midfielder"))

    @discord.ui.button(label="Attacker", style=discord.ButtonStyle.primary, emoji="🎯")
    async def attacker_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScorecardModal(self.candidate, "Attacker"))


class DocketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Refresh Docket", style=discord.ButtonStyle.secondary, custom_id="refresh_docket", emoji="🔄")
    async def refresh_docket(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT candidate_name, position_group, score, timestamp FROM evaluations ORDER BY id DESC LIMIT 5')
        recent_evals = cursor.fetchall()
        conn.close()

        embed = discord.Embed(
            title="📋 Active FC Pro Clubs 27 Trial Docket",
            description="Live overview of recent candidate evaluations and scouting dossiers.",
            color=discord.Color.blurple()
        )
        
        if recent_evals:
            for name, pos, score, ts in recent_evals:
                parsed_time = ts.split("T")[0]
                embed.add_field(
                    name=f"{name} ({pos})",
                    value=f"Score: **{score}/10** | Date: {parsed_time}",
                    inline=False
                )
        else:
            embed.add_field(name="No Evaluations", value="No trial evaluations recorded yet.", inline=False)

        embed.set_footer(text="Refreshed live via /scout docket")
        await interaction.response.edit_message(embed=embed, view=self)


scout_group = app_commands.Group(name="scout", description="Unified FC Pro Clubs 27 scouting and recruitment pipeline")

@scout_group.command(name="evaluate", description="Open candidate evaluation workflow & position selector")
@app_commands.describe(candidate="The player candidate undergoing trial")
async def scout_evaluate(interaction: discord.Interaction, candidate: discord.Member):
    view = PositionSelectView(candidate)
    embed = discord.Embed(
        title="🎯 Candidate Position Selection",
        description=f"Select the positional group for **{candidate.mention}** to open the scorecard modal.",
        color=discord.Color.dark_theme()
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@scout_group.command(name="history", description="View past trial logs & scout notes for a player")
@app_commands.describe(candidate="The player whose history you wish to review")
async def scout_history(interaction: discord.Interaction, candidate: discord.Member):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT position_group, score, scout_name, notes, timestamp 
            FROM evaluations WHERE candidate_id = ? ORDER BY id DESC
        ''', (str(candidate.id),))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        await interaction.response.send_message(f"❌ Database query error: {e}", ephemeral=True)
        return

    if not rows:
        await interaction.response.send_message(f"📁 No historical trial dossiers found for **{candidate.display_name}**.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"📂 Trial Dossier: {candidate.display_name}",
        description=f"Showing historical performance evaluations ({len(rows)} total sessions).",
        color=discord.Color.gold()
    )

    for pos, score, scout, notes, ts in rows[:5]: # Show last 5 entries
        date_str = ts.split("T")[0]
        embed.add_field(
            name=f"{pos} — Score: {score}/10 (By {scout})",
            value=f"**Notes:** {notes}\n*Date: {date_str}*",
            inline=False
        )

    embed.set_thumbnail(url=candidate.display_avatar.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@scout_group.command(name="assign", description="Manage recruitment stage roles (@Trialist, @Academia Prospect)")
@app_commands.describe(candidate="The candidate", role="The recruitment role to assign")
@app_commands.choices(role=[
    app_commands.Choice(name="Trialist", value="Trialist"),
    app_commands.Choice(name="Academia Prospect", value="Academia Prospect"),
    app_commands.Choice(name="First Team Roster", value="First Team Roster")
])
async def scout_assign(interaction: discord.Interaction, candidate: discord.Member, role: str):
    guild = interaction.guild
    discord_role = discord.utils.get(guild.roles, name=role)
    
    if not discord_role:
        await interaction.response.send_message(f"❌ Role `{role}` could not be found in this server. Please create it first.", ephemeral=True)
        return

    try:
        await candidate.add_roles(discord_role)
        await interaction.response.send_message(f"✅ Successfully assigned **{discord_role.name}** to {candidate.mention}.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to assign role due to permissions error: {e}", ephemeral=True)


@scout_group.command(name="docket", description="Create or refresh active player dossier control panel in staff channel")
async def scout_docket(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 Active FC Pro Clubs 27 Trial Docket",
        description="Live overview of recent candidate evaluations and scouting dossiers.",
        color=discord.Color.blurple()
    )
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT candidate_name, position_group, score, timestamp FROM evaluations ORDER BY id DESC LIMIT 5')
    recent_evals = cursor.fetchall()
    conn.close()

    if recent_evals:
        for name, pos, score, ts in recent_evals:
            parsed_time = ts.split("T")[0]
            embed.add_field(
                name=f"{name} ({pos})",
                value=f"Score: **{score}/10** | Date: {parsed_time}",
                inline=False
            )
    else:
        embed.add_field(name="No Evaluations", value="No trial evaluations recorded yet.", inline=False)

    view = DocketControlView()
    await interaction.response.send_message(embed=embed, view=view)


@scout_group.command(name="link", description="Map an EA/Friday account handle to a Discord member for automated stats")
@app_commands.describe(ea_handle="The player's exact EA account ID or handle from Friday app", member="The Discord member")
async def scout_link(interaction: discord.Interaction, ea_handle: str, member: discord.Member):
    """Links an EA handle to a Discord member in the database for automatic match log attribution."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO player_mappings (ea_handle, discord_id, discord_name)
            VALUES (?, ?, ?)
        ''', (ea_handle.strip(), str(member.id), member.display_name))
        conn.commit()
        conn.close()
        
        await interaction.response.send_message(
            f"🔗 **Successfully Linked:** EA Handle `{ea_handle}` is now mapped to {member.mention}.",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(f"❌ Database error linking player: {e}", ephemeral=True)


@scout_group.command(name="stats", description="View automated match performance stats & match history")
@app_commands.describe(candidate="The player whose automated match logs you wish to review")
async def scout_stats(interaction: discord.Interaction, candidate: discord.Member):
    """Retrieves automated match recap stats logged from Friday app embeds."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT match_date, rating, goals, assists, passing_acc, tackles, raw_summary 
            FROM match_stats WHERE discord_id = ? ORDER BY id DESC LIMIT 5
        ''', (str(candidate.id),))
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        await interaction.response.send_message(f"❌ Database query error: {e}", ephemeral=True)
        return

    if not rows:
        await interaction.response.send_message(f"📊 No automated Friday match statistics found for **{candidate.display_name}**. Make sure their EA handle is linked using `/scout link`.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"📈 Automated Match Performance: {candidate.display_name}",
        description=f"Showing recent match tracking data ingested from Friday app recaps.",
        color=discord.Color.teal()
    )

    for match_date, rating, goals, assists, passing_acc, tackles, summary in rows:
        date_parsed = match_date.split("T")[0]
        stats_text = summary if summary else f"⭐ Rating: {rating} | ⚽ Goals: {goals} | 🎯 Assists: {assists} | 📦 Pass Acc: {passing_acc}% | 🛡️ Tackles: {tackles}"
        embed.add_field(
            name=f"Match Date: {date_parsed}",
            value=stats_text,
            inline=False
        )

    embed.set_thumbnail(url=candidate.display_avatar.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(scout_group)


@bot.event
async def on_message(message):
    # Ignore messages sent by the bot itself
    if message.author == bot.user:
        return

    # Automatically parse embeds from the Friday app in designated recap channels
    if message.embeds:
        for embed in message.embeds:
            title_text = embed.title.lower() if embed.title else ""
            footer_text = embed.footer.text.lower() if embed.footer and embed.footer.text else ""
            
            # Check if embed indicates a Friday App Match Recap
            if "match recap" in title_text or "friday" in footer_text or "pro clubs" in title_text:
                print(f"[{datetime.datetime.utcnow()}] Ingesting Friday Match Recap in channel #{message.channel.name}")
                
                match_timestamp = datetime.datetime.utcnow().isoformat()
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                
                # Parse structured embed fields where Friday app posts player match summaries
                for field in embed.fields:
                    field_value = field.value
                    lines = field_value.split("\n")
                    for line in lines:
                        # Attempt to match player handles and stats in Friday app embed output
                        # Example expected line format or structured block
                        if "|" in line:
                            parts = [p.strip() for p in line.split("|")]
                            if len(parts) >= 2:
                                ea_handle = parts[0]
                                raw_summary = line
                                
                                # Check if this EA handle is mapped to a Discord ID
                                cursor.execute('SELECT discord_id FROM player_mappings WHERE ea_handle = ?', (ea_handle,))
                                mapping = cursor.fetchone()
                                discord_id = mapping[0] if mapping else None
                                
                                # Insert parsed match stats record
                                cursor.execute('''
                                    INSERT INTO match_stats (player_handle, discord_id, match_date, rating, goals, assists, passing_acc, tackles, raw_summary)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ''', (ea_handle, discord_id, match_timestamp, 0.0, 0, 0, 0.0, 0, raw_summary))
                                
                # Also inspect embed description if present for team sheet / player performance summaries
                if embed.description:
                    desc_lines = embed.description.split("\n")
                    for line in desc_lines:
                        if len(line.strip()) > 3:
                            # Log general recap line or perform secondary parsing if needed
                            pass

                conn.commit()
                conn.close()
                print(f"[{datetime.datetime.utcnow()}] Successfully processed and stored Friday match recap data.")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("FC Pro Clubs 27 Scouting & Match Ingestion Pipeline is active and ready.")

# Run the bot (Replace 'YOUR_BOT_TOKEN' with your actual Discord Bot Token or use environment variables)
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")
    if TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("⚠️ Warning: Please set your Discord Bot Token before running.")
    bot.run(TOKEN)