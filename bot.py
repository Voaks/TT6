from __future__ import annotations

import asyncio
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LINKS_FILE = DATA_DIR / "links.json"
STEAM_ID_RE = re.compile(r"7656119\d{10}")


def load_dotenv() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


class LinkStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()

    async def load(self) -> dict:
        async with self._lock:
            return self._read_unlocked()

    async def set_link(
        self,
        discord_user: discord.abc.User,
        steam_id: str,
        updated_by: discord.abc.User,
    ) -> None:
        async with self._lock:
            data = self._read_unlocked()
            data["links"][str(discord_user.id)] = {
                "discord_name": str(discord_user),
                "steam_id": steam_id,
                "updated_by": str(updated_by.id),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write_unlocked(data)

    async def remove_link(self, discord_user: discord.abc.User) -> dict | None:
        async with self._lock:
            data = self._read_unlocked()
            removed = data["links"].pop(str(discord_user.id), None)
            self._write_unlocked(data)
            return removed

    def _read_unlocked(self) -> dict:
        if not self.path.exists():
            return {"links": {}}

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup = self.path.with_suffix(".broken.json")
            self.path.replace(backup)
            return {"links": {}}

        if not isinstance(data, dict) or not isinstance(data.get("links"), dict):
            return {"links": {}}

        return data

    def _write_unlocked(self, data: dict) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=DATA_DIR,
            delete=False,
            suffix=".tmp",
        ) as temp_file:
            json.dump(data, temp_file, indent=2, sort_keys=True)
            temp_file.write("\n")
            temp_path = Path(temp_file.name)

        temp_path.replace(self.path)


def normalize_steam_id(value: str) -> str | None:
    match = STEAM_ID_RE.search(value.strip())
    return match.group(0) if match else None


def is_admin(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def target_requires_admin(
    interaction: discord.Interaction,
    target_user: discord.abc.User,
) -> bool:
    return target_user.id != interaction.user.id and not is_admin(interaction)


def chunk_lines(lines: list[str], max_length: int = 1900) -> list[str]:
    chunks: list[str] = []
    current = ""

    for line in lines:
        addition = f"{line}\n"
        if len(current) + len(addition) > max_length and current:
            chunks.append(current.rstrip())
            current = ""
        current += addition

    if current:
        chunks.append(current.rstrip())

    return chunks


def build_steam_script(links: dict[str, dict], clan_tag: str) -> str:
    clan_tag = clan_tag.strip()
    players = [
        {
            "discordId": discord_id,
            "discordName": record.get("discord_name") or discord_id,
            "steamId": record["steam_id"],
        }
        for discord_id, record in sorted(
            links.items(),
            key=lambda item: (item[1].get("discord_name") or item[0]).lower(),
        )
        if record.get("steam_id")
    ]

    players_json = json.dumps(players, indent=2)

    return f"""(async () => {{
  const players = {players_json};
  const clanTag = {json.dumps(clan_tag)};
  const delayMs = 1800;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const sessionId =
    window.g_sessionID ||
    document.cookie.match(/(?:^|; )sessionid=([^;]+)/)?.[1];

  if (!location.hostname.endsWith("steamcommunity.com")) {{
    console.error("Run this from a steamcommunity.com page.");
    return;
  }}

  if (!sessionId) {{
    console.error("Steam session ID was not found. Sign in to Steam Community and reload the page.");
    return;
  }}

  async function postForm(url, fields) {{
    const body = new URLSearchParams({{ sessionID: sessionId, ...fields }});
    const response = await fetch(url, {{
      method: "POST",
      credentials: "include",
      headers: {{
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
      }},
      body,
    }});

    const text = await response.text();
    let parsed = text;
    try {{
      parsed = JSON.parse(text);
    }} catch {{
      // Steam sometimes returns plain text for these older endpoints.
    }}

    if (!response.ok) {{
      throw new Error(`${{response.status}} ${{response.statusText}}: ${{text.slice(0, 160)}}`);
    }}

    return parsed;
  }}

  async function getSteamName(steamId) {{
    const response = await fetch("/profiles/" + steamId + "/?xml=1", {{
      credentials: "include",
    }});
    const text = await response.text();

    if (!response.ok) {{
      throw new Error(`${{response.status}} ${{response.statusText}}: ${{text.slice(0, 160)}}`);
    }}

    const profileXml = new DOMParser().parseFromString(text, "application/xml");
    const parserError = profileXml.querySelector("parsererror");
    if (parserError) {{
      throw new Error("Steam profile XML could not be parsed.");
    }}

    const steamName = profileXml.querySelector("steamID")?.textContent?.trim();
    if (!steamName) {{
      throw new Error("Steam profile name was not found.");
    }}

    return steamName;
  }}

  async function addFriend(steamId) {{
    return postForm("/actions/AddFriendAjax", {{
      steamid: steamId,
      accept_invite: "0",
    }});
  }}

  async function setNickname(steamId, nickname) {{
    const endpoints = [
      ["/profiles/" + steamId + "/ajaxsetnickname/", {{ sessionid: sessionId, nickname }}],
      ["/actions/AliasFriend", {{ steamid: steamId, alias: nickname }}],
    ];

    const errors = [];
    for (let endpointIndex = 0; endpointIndex < endpoints.length; endpointIndex++) {{
      const url = endpoints[endpointIndex][0];
      const fields = endpoints[endpointIndex][1];
      try {{
        return await postForm(url, fields);
      }} catch (error) {{
        errors.push(`${{url}} -> ${{error.message}}`);
      }}
    }}

    throw new Error(errors.join(" | "));
  }}

  console.log(`Starting ${{players.length}} Steam friend/nickname updates...`);

  for (let index = 0; index < players.length; index++) {{
    const player = players[index];
    const label = `${{player.discordName}} (${{player.discordId}})`;
    try {{
      await addFriend(player.steamId);
      console.log(`[${{index + 1}}/${{players.length}}] Friend request sent: ${{label}} -> ${{player.steamId}}`);
    }} catch (error) {{
      console.warn(`[${{index + 1}}/${{players.length}}] Friend request failed: ${{label}} -> ${{player.steamId}}`, error);
    }}

    await sleep(delayMs);

    try {{
      const steamName = await getSteamName(player.steamId);
      const nickname = `${{clanTag}} ${{steamName}}`;
      await setNickname(player.steamId, nickname);
      console.log(`[${{index + 1}}/${{players.length}}] Nickname set: ${{player.steamId}} -> ${{nickname}}`);
    }} catch (error) {{
      console.warn(`[${{index + 1}}/${{players.length}}] Nickname failed: ${{player.steamId}}`, error);
      console.warn("Steam changes nickname endpoints occasionally. If this happens, add friends first, then nickname from Steam's friends UI.");
    }}

    await sleep(delayMs);
  }}

  console.log("Done.");
}})();
"""


class LinkCog(commands.Cog):
    link = app_commands.Group(name="link", description="Manage Discord to Steam links.")

    def __init__(self, bot: commands.Bot, store: LinkStore):
        self.bot = bot
        self.store = store

    @link.command(name="add", description="Link a Discord user to a SteamID64.")
    @app_commands.describe(
        steam_id="The user's 17-digit SteamID64 or a Steam profile URL.",
        user="Admin only: link this Steam ID to another Discord user.",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        steam_id: str,
        user: discord.Member | None = None,
    ) -> None:
        target_user = user or interaction.user

        if target_requires_admin(interaction, target_user):
            await interaction.response.send_message(
                "You can only link your own Steam ID. An admin can link another Discord user.",
                ephemeral=True,
            )
            return

        normalized = normalize_steam_id(steam_id)
        if normalized is None:
            await interaction.response.send_message(
                "Please provide a valid 17-digit SteamID64, like `76561198#########`, or a profile URL containing one.",
                ephemeral=True,
            )
            return

        await self.store.set_link(target_user, normalized, interaction.user)

        if target_user.id == interaction.user.id:
            message = f"Linked your Discord account to Steam ID `{normalized}`."
        else:
            message = f"Linked {target_user.mention} to Steam ID `{normalized}`."

        await interaction.response.send_message(message, ephemeral=True)

    @link.command(name="remove", description="Remove a Discord to Steam link.")
    @app_commands.describe(user="Admin only: remove another user's link.")
    async def remove(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        target_user = user or interaction.user

        if target_requires_admin(interaction, target_user):
            await interaction.response.send_message(
                "You can only remove your own link. An admin can remove another Discord user's link.",
                ephemeral=True,
            )
            return

        removed = await self.store.remove_link(target_user)
        if not removed:
            await interaction.response.send_message(
                f"No link exists for {target_user.mention}.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Removed the Steam link for {target_user.mention}.",
            ephemeral=True,
        )

    @link.command(name="list", description="Show every Discord to Steam link.")
    async def list_links(self, interaction: discord.Interaction) -> None:
        data = await self.store.load()
        links = data["links"]

        if not links:
            await interaction.response.send_message("No players are linked yet.")
            return

        lines = [
            f"<@{discord_id}> -> `{record.get('steam_id', 'missing')}`"
            for discord_id, record in sorted(
                links.items(),
                key=lambda item: (item[1].get("discord_name") or item[0]).lower(),
            )
        ]

        chunks = chunk_lines(lines)
        await interaction.response.send_message(
            f"Linked players ({len(lines)}):\n{chunks[0]}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

        for chunk in chunks[1:]:
            await interaction.followup.send(
                chunk,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @link.command(
        name="generate",
        description="Generate a Steam Community browser script for all linked players.",
    )
    @app_commands.describe(
        clan_tag="Clan Tag",
    )
    async def generate(
        self,
        interaction: discord.Interaction,
        clan_tag: str,
    ) -> None:
        clan_tag = clan_tag.strip()
        if not clan_tag:
            await interaction.response.send_message(
                "Please enter a clan tag before generating the Steam script.",
                ephemeral=True,
            )
            return

        data = await self.store.load()
        links = data["links"]

        if not links:
            await interaction.response.send_message(
                "No players are linked yet, so there is no Steam script to generate.",
                ephemeral=True,
            )
            return

        script = build_steam_script(links, clan_tag)
        file = discord.File(
            io.BytesIO(script.encode("utf-8")),
            filename="steam-link-friends.js",
        )

        await interaction.response.send_message(
            f"Generated the Steam Community console script with clan tag `{clan_tag}`. Open `steamcommunity.com`, press `F12`, choose the Console tab, and paste/run the script.",
            file=file,
            ephemeral=True,
        )


class LinkBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.store = LinkStore(LINKS_FILE)

    async def setup_hook(self) -> None:
        await self.add_cog(LinkCog(self, self.store))
        guild_id = os.getenv("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        await self.change_presence(activity=discord.Game(name="/link"))
        if self.user:
            print(f"Logged in as {self.user} ({self.user.id})")


def main() -> None:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is missing. Add it to .env or your environment.")

    bot = LinkBot()
    bot.run(token)


if __name__ == "__main__":
    main()
