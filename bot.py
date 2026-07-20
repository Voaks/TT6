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
TEAM_ROLES_FILE = DATA_DIR / "team_roles.json"
TASK_ROLES_FILE = DATA_DIR / "task_roles.json"
RAID_CHECKLIST_FILE = DATA_DIR / "raid_checklist.json"
STEAM_ID_RE = re.compile(r"7656119\d{10}")

TEAM_ROLE_OPTIONS = [
    {"id": "build_team", "label": "Build Team", "max": 2},
    {"id": "farm_base_clones", "label": "Farm Base and Clones", "max": 1},
    {"id": "furnace_base", "label": "Furnace Base", "max": 2},
    {"id": "electricity_industrial", "label": "Electricity and Industrial", "max": 2},
    {"id": "monument_team", "label": "Monument Team", "max": 6},
    {"id": "farm_team", "label": "Farm/Roam Team", "max": None},
]
TEAM_ROLE_BY_ID = {role["id"]: role for role in TEAM_ROLE_OPTIONS}

TASK_ROLE_OPTIONS = [
    {
        "id": "deployables",
        "label": "Deployables (Doors, Embrasures, Lockers, Etc.)",
        "max": 8,
    },
    {"id": "autolockers", "label": "Autolockers", "max": 1},
    {"id": "nades_smokes_seal_mats", "label": "Nades/Smokes/Seal Mats", "max": 8},
    {"id": "bed_placer", "label": "Bed Placer", "max": 3},
]

RAID_CHECKLIST_OPTIONS = [
    {"id": "ladders", "label": "Ladders", "max": None},
    {"id": "rocketers", "label": "Rocketers", "max": None},
    {"id": "hv_rockets", "label": "HV Rockets", "max": None},
    {"id": "incendiary_rockets", "label": "Incendiary Rockets", "max": None},
    {"id": "fob_mats", "label": "Fob Mats", "max": None},
    {"id": "turrets", "label": "Turrets", "max": None},
    {"id": "adsr", "label": "ADSr", "max": None},
    {"id": "u_wall_cargo_mats", "label": "U Wall/Cargo Mats", "max": None},
    {"id": "med_mats", "label": "Med Mats", "max": None},
    {"id": "doors", "label": "Doors", "max": None},
    {"id": "t2_rekits", "label": "T2 Rekits", "max": None},
]


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


class TeamRoleStore:
    def __init__(
        self,
        path: Path,
        role_options: list[dict] | None = None,
        selection_label: str = "team role",
    ):
        self.path = path
        self.role_options = role_options or TEAM_ROLE_OPTIONS
        self.role_by_id = {role["id"]: role for role in self.role_options}
        self.selection_label = selection_label
        self._lock = asyncio.Lock()

    async def get_guild(self, guild_id: int) -> dict:
        async with self._lock:
            data = self._read_unlocked()
            return self._guild_unlocked(data, guild_id).copy()

    async def set_board(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        config: dict | None = None,
    ) -> dict:
        async with self._lock:
            data = self._read_unlocked()
            guild_data = self._guild_unlocked(data, guild_id)
            guild_data["channel_id"] = str(channel_id)
            guild_data["message_id"] = str(message_id)
            if config is not None:
                guild_data["config"] = config
            guild_data["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._write_unlocked(data)
            return guild_data.copy()

    async def reset_guild(self, guild_id: int) -> dict:
        async with self._lock:
            data = self._read_unlocked()
            guild_data = self._guild_unlocked(data, guild_id)
            guild_data["assignments"] = self._empty_assignments()
            guild_data["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._write_unlocked(data)
            return guild_data.copy()

    async def toggle_assignment(
        self,
        guild_id: int,
        user: discord.abc.User,
        role_id: str,
    ) -> tuple[str, str, dict]:
        async with self._lock:
            data = self._read_unlocked()
            guild_data = self._guild_unlocked(data, guild_id)
            assignments = guild_data["assignments"]
            user_id = str(user.id)
            selected_role_id = self._selected_role_id(assignments, user_id)

            if role_id not in self.role_by_id:
                message = f"That {self.selection_label} is not available anymore."
                return "invalid", message, guild_data.copy()

            if selected_role_id == role_id:
                assignments[role_id] = [
                    assigned_id for assigned_id in assignments[role_id] if assigned_id != user_id
                ]
                guild_data["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._write_unlocked(data)
                return "removed", f"Removed your {self.selection_label}.", guild_data.copy()

            if selected_role_id is not None:
                selected_label = self.role_by_id[selected_role_id]["label"]
                return (
                    "already_assigned",
                    f"You are already assigned to {selected_label}. Click that button again first to unassign.",
                    guild_data.copy(),
                )

            role = self.role_by_id[role_id]
            max_members = role["max"]
            if max_members is not None and len(assignments[role_id]) >= max_members:
                return "full", f"{role['label']} is full.", guild_data.copy()

            assignments[role_id].append(user_id)
            guild_data["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._write_unlocked(data)
            return "assigned", f"Assigned you to {role['label']}.", guild_data.copy()

    def _read_unlocked(self) -> dict:
        if not self.path.exists():
            return {"guilds": {}}

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup = self.path.with_suffix(".broken.json")
            self.path.replace(backup)
            return {"guilds": {}}

        if not isinstance(data, dict) or not isinstance(data.get("guilds"), dict):
            return {"guilds": {}}

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

    def _guild_unlocked(self, data: dict, guild_id: int) -> dict:
        guilds = data.setdefault("guilds", {})
        guild_data = guilds.setdefault(str(guild_id), {})
        assignments = guild_data.get("assignments")

        if not isinstance(assignments, dict):
            assignments = self._empty_assignments()

        guild_data["assignments"] = self._clean_assignments(assignments)
        return guild_data

    def _empty_assignments(self) -> dict[str, list[str]]:
        return {role["id"]: [] for role in self.role_options}

    def _clean_assignments(self, assignments: dict) -> dict[str, list[str]]:
        cleaned = self._empty_assignments()
        already_seen: set[str] = set()

        for role in self.role_options:
            role_id = role["id"]
            raw_user_ids = assignments.get(role_id, [])
            if not isinstance(raw_user_ids, list):
                continue

            for raw_user_id in raw_user_ids:
                user_id = str(raw_user_id)
                if not user_id.isdigit() or user_id in already_seen:
                    continue

                max_members = role["max"]
                if max_members is not None and len(cleaned[role_id]) >= max_members:
                    continue

                cleaned[role_id].append(user_id)
                already_seen.add(user_id)

        return cleaned

    def _selected_role_id(self, assignments: dict[str, list[str]], user_id: str) -> str | None:
        for role_id, user_ids in assignments.items():
            if user_id in user_ids:
                return role_id

        return None


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


def assigned_user_ids(guild_data: dict) -> set[str]:
    assignments = guild_data.get("assignments")
    if not isinstance(assignments, dict):
        return set()

    user_ids: set[str] = set()
    for raw_role_user_ids in assignments.values():
        if not isinstance(raw_role_user_ids, list):
            continue
        user_ids.update(str(user_id) for user_id in raw_role_user_ids)

    return user_ids


def find_text_channel_by_name(guild: discord.Guild, name: str) -> discord.TextChannel | None:
    normalized_name = name.lower()
    for channel in guild.text_channels:
        if channel.name.lower() == normalized_name:
            return channel

    return None


def channel_reference(guild: discord.Guild, name: str) -> str:
    channel = find_text_channel_by_name(guild, name)
    return channel.mention if channel else f"#{name}"


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


def build_role_board_embed(
    guild_data: dict,
    role_options: list[dict],
    title: str,
    selection_label: str,
    footer: str,
) -> discord.Embed:
    assignments = guild_data.get("assignments") or {}
    embed = discord.Embed(
        title=title,
        description=(
            f"Choose one {selection_label}.\n\n"
            "Click your current selection again to remove yourself, then choose a new one."
        ),
        color=discord.Color.green(),
    )

    for role in role_options:
        role_id = role["id"]
        user_ids = assignments.get(role_id, [])
        cap_text = "no cap" if role["max"] is None else str(role["max"])
        value = "\n".join(f"<@{user_id}>" for user_id in user_ids) if user_ids else "None"
        embed.add_field(
            name=f"{role['label']} ({len(user_ids)}/{cap_text})",
            value=value,
            inline=False,
        )

    embed.set_footer(text=footer)
    return embed


def build_team_roles_embed(guild_data: dict) -> discord.Embed:
    return build_role_board_embed(
        guild_data,
        TEAM_ROLE_OPTIONS,
        "Team Role Selection",
        "team role",
        "Team role board",
    )


def build_task_roles_embed(guild_data: dict) -> discord.Embed:
    return build_role_board_embed(
        guild_data,
        TASK_ROLE_OPTIONS,
        "Task Role Selection",
        "task role",
        "Task role board",
    )


def raid_checklist_config(
    ladders: int,
    rocketers: int,
    rockets_each: int,
    hv_rockets: int,
    incendiary_rockets: int,
    turrets: int,
    adsr: int,
) -> dict:
    return {
        "ladders": ladders,
        "rocketers": rocketers,
        "rockets_each": rockets_each,
        "hv_rockets": hv_rockets,
        "incendiary_rockets": incendiary_rockets,
        "turrets": turrets,
        "adsr": adsr,
    }


def build_raid_checklist_options(guild_data: dict) -> list[dict]:
    config = guild_data.get("config") if isinstance(guild_data.get("config"), dict) else {}
    ladders = config.get("ladders", 5)
    rocketers = config.get("rocketers", 1)
    rockets_each = config.get("rockets_each", 1)
    hv_rockets = config.get("hv_rockets", 0)
    incendiary_rockets = config.get("incendiary_rockets", 0)
    turrets = config.get("turrets", 4)
    adsr = config.get("adsr", 1)

    labels = [
        ("ladders", f"{ladders} Ladders"),
        ("rocketers", f"{rocketers} Rocketers ({rockets_each} rockets each)"),
        ("hv_rockets", f"{hv_rockets} HV Rockets"),
        ("incendiary_rockets", f"{incendiary_rockets} Incendiary Rockets"),
        ("fob_mats", "Fob Mats (20-24k Metal + 12k Wood)"),
        (
            "turrets",
            f"{turrets} Turrets, Battery, wiring tool, branches/splitters, chain links",
        ),
        ("adsr", f"{adsr} ADSr"),
        ("u_wall_cargo_mats", "U Wall/Cargo Mats"),
        ("med_mats", "Med Mats (T2 + 3k Cloth + 1k Low Grade + 4k Metal Frags)"),
        ("doors", "Doors (2 armored/sheet double doors)"),
        ("t2_rekits", "T2 Rekits (6-9 tommys + hazmats + pistol bullets)"),
    ]

    return [
        {
            "id": role_id,
            "label": f"{index}. {label}",
            "max": None,
            "button_label": f"{index}. I Will Do This",
        }
        for index, (role_id, label) in enumerate(labels, start=2)
    ]


def build_raid_checklist_embed(guild_data: dict) -> discord.Embed:
    assignments = guild_data.get("assignments") or {}
    embed = discord.Embed(
        title="Raid Checklist",
        description=(
            "Choose one thing to handle.\n\n"
            "Click your current selection again to remove yourself, then choose a new one."
        ),
        color=discord.Color.green(),
    )
    embed.add_field(name="1. Bed", value="EVERYONE", inline=False)

    for role in build_raid_checklist_options(guild_data):
        user_ids = assignments.get(role["id"], [])
        value = "\n".join(f"<@{user_id}>" for user_id in user_ids) if user_ids else "None"
        embed.add_field(name=role["label"], value=value, inline=False)

    embed.set_footer(text="Raid checklist")
    return embed


class TeamRoleButton(discord.ui.Button):
    def __init__(self, role: dict, custom_id_prefix: str):
        super().__init__(
            label=role.get("button_label") or role["label"],
            style=discord.ButtonStyle.success,
            custom_id=f"{custom_id_prefix}:{role['id']}",
        )
        self.role_id = role["id"]

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if interaction.guild is None:
            await interaction.response.send_message(
                f"{view.selection_label.title()}s can only be selected in a server."
                if isinstance(view, TeamRoleView)
                else "Roles can only be selected in a server.",
                ephemeral=True,
            )
            return

        if not isinstance(view, TeamRoleView):
            await interaction.response.send_message(
                "This role board needs to be reposted.",
                ephemeral=True,
            )
            return

        if view.link_store is not None:
            link_data = await view.link_store.load()
            if str(interaction.user.id) not in link_data["links"]:
                instructions = channel_reference(interaction.guild, "instructions")
                await interaction.response.send_message(
                    f"You need to be on the linked list before selecting a role. "
                    f"Please go to {instructions} for the linking steps.",
                    ephemeral=True,
                )
                return

        status, message, guild_data = await view.store.toggle_assignment(
            interaction.guild.id,
            interaction.user,
            self.role_id,
        )

        if status in {"assigned", "removed"}:
            await interaction.response.edit_message(
                embed=view.build_embed(guild_data),
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await interaction.response.send_message(message, ephemeral=True)


class TeamRoleView(discord.ui.View):
    def __init__(
        self,
        store: TeamRoleStore,
        *,
        role_options: list[dict] | None = None,
        custom_id_prefix: str = "team_role",
        build_embed=build_team_roles_embed,
        repost_command: str = "/team roles",
        selection_label: str = "team role",
        link_store: LinkStore | None = None,
    ):
        super().__init__(timeout=None)
        self.store = store
        self.role_options = role_options or TEAM_ROLE_OPTIONS
        self.build_embed = build_embed
        self.repost_command = repost_command
        self.selection_label = selection_label
        self.link_store = link_store

        for index, role in enumerate(self.role_options):
            button = TeamRoleButton(role, custom_id_prefix)
            button.row = index // 5
            self.add_item(button)


def build_team_role_view(store: TeamRoleStore, link_store: LinkStore | None = None) -> TeamRoleView:
    return TeamRoleView(store, link_store=link_store)


def build_task_role_view(
    store: TeamRoleStore,
    link_store: LinkStore | None = None,
) -> TeamRoleView:
    return TeamRoleView(
        store,
        role_options=TASK_ROLE_OPTIONS,
        custom_id_prefix="task_role",
        build_embed=build_task_roles_embed,
        repost_command="/task roles",
        selection_label="task role",
        link_store=link_store,
    )


def build_raid_checklist_view(store: TeamRoleStore) -> TeamRoleView:
    return TeamRoleView(
        store,
        role_options=build_raid_checklist_options({}),
        custom_id_prefix="raid_checklist",
        build_embed=build_raid_checklist_embed,
        repost_command="/raid checklist",
        selection_label="raid checklist item",
    )


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


class TeamCog(commands.Cog):
    team = app_commands.Group(name="team", description="Manage team role selections.")

    def __init__(self, bot: commands.Bot, store: TeamRoleStore, link_store: LinkStore):
        self.bot = bot
        self.store = store
        self.link_store = link_store

    @team.command(name="roles", description="Post the team role selection board.")
    async def roles(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Team roles can only be posted in a server channel.",
                ephemeral=True,
            )
            return

        if not is_admin(interaction):
            await interaction.response.send_message(
                "Only an administrator can post the team role board.",
                ephemeral=True,
            )
            return

        guild_data = await self.store.get_guild(interaction.guild.id)
        await interaction.response.send_message(
            embed=build_team_roles_embed(guild_data),
            view=build_team_role_view(self.store, self.link_store),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        message = await interaction.original_response()
        await self.store.set_board(interaction.guild.id, interaction.channel.id, message.id)

    @team.command(name="roles_reset", description="Reset every team role assignment.")
    async def roles_reset(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Team roles can only be reset in a server.",
                ephemeral=True,
            )
            return

        if not is_admin(interaction):
            await interaction.response.send_message(
                "Only an administrator can reset team roles.",
                ephemeral=True,
            )
            return

        guild_data = await self.store.reset_guild(interaction.guild.id)
        board_updated = await self._edit_active_board(interaction, guild_data)
        suffix = " The active board was updated." if board_updated else " Run `/team roles` to post a board."
        await interaction.response.send_message(f"Team roles reset.{suffix}", ephemeral=True)

    async def _edit_active_board(self, interaction: discord.Interaction, guild_data: dict) -> bool:
        channel_id = guild_data.get("channel_id")
        message_id = guild_data.get("message_id")

        if not channel_id or not message_id:
            return False

        try:
            channel = interaction.client.get_channel(int(channel_id))
            if channel is None:
                channel = await interaction.client.fetch_channel(int(channel_id))
            if not isinstance(channel, discord.abc.Messageable):
                return False
            message = await channel.fetch_message(int(message_id))
            await message.edit(
                embed=build_team_roles_embed(guild_data),
                view=build_team_role_view(self.store, self.link_store),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.DiscordException, ValueError):
            return False

        return True


class TaskCog(commands.Cog):
    task = app_commands.Group(name="task", description="Manage task role selections.")

    def __init__(self, bot: commands.Bot, store: TeamRoleStore, link_store: LinkStore):
        self.bot = bot
        self.store = store
        self.link_store = link_store

    @task.command(name="roles", description="Post the task role selection board.")
    async def roles(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Task roles can only be posted in a server channel.",
                ephemeral=True,
            )
            return

        if not is_admin(interaction):
            await interaction.response.send_message(
                "Only an administrator can post the task role board.",
                ephemeral=True,
            )
            return

        guild_data = await self.store.get_guild(interaction.guild.id)
        await interaction.response.send_message(
            embed=build_task_roles_embed(guild_data),
            view=build_task_role_view(self.store, self.link_store),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        message = await interaction.original_response()
        await self.store.set_board(interaction.guild.id, interaction.channel.id, message.id)

    @task.command(name="roles_reset", description="Reset every task role assignment.")
    async def roles_reset(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Task roles can only be reset in a server.",
                ephemeral=True,
            )
            return

        if not is_admin(interaction):
            await interaction.response.send_message(
                "Only an administrator can reset task roles.",
                ephemeral=True,
            )
            return

        guild_data = await self.store.reset_guild(interaction.guild.id)
        board_updated = await self._edit_active_board(interaction, guild_data)
        suffix = " The active board was updated." if board_updated else " Run `/task roles` to post a board."
        await interaction.response.send_message(f"Task roles reset.{suffix}", ephemeral=True)

    async def _edit_active_board(self, interaction: discord.Interaction, guild_data: dict) -> bool:
        channel_id = guild_data.get("channel_id")
        message_id = guild_data.get("message_id")

        if not channel_id or not message_id:
            return False

        try:
            channel = interaction.client.get_channel(int(channel_id))
            if channel is None:
                channel = await interaction.client.fetch_channel(int(channel_id))
            if not isinstance(channel, discord.abc.Messageable):
                return False
            message = await channel.fetch_message(int(message_id))
            await message.edit(
                embed=build_task_roles_embed(guild_data),
                view=build_task_role_view(self.store, self.link_store),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.DiscordException, ValueError):
            return False

        return True


class RaidCog(commands.Cog):
    raid = app_commands.Group(name="raid", description="Manage raid prep checklists.")

    def __init__(self, bot: commands.Bot, store: TeamRoleStore):
        self.bot = bot
        self.store = store

    @raid.command(name="checklist", description="Post the raid checklist assignment board.")
    @app_commands.describe(
        ladders="How many ladders are needed.",
        rocketers="How many rocketers are needed.",
        rockets_each="How many rockets each rocketer should bring.",
        hv_rockets="How many HV rockets are needed.",
        incendiary_rockets="How many incendiary rockets are needed.",
        turrets="How many turrets are needed.",
        adsr="How many ADSr are needed.",
    )
    async def checklist(
        self,
        interaction: discord.Interaction,
        ladders: int,
        rocketers: int,
        rockets_each: int,
        hv_rockets: int,
        incendiary_rockets: int,
        turrets: int,
        adsr: int,
    ) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Raid checklists can only be posted in a server channel.",
                ephemeral=True,
            )
            return

        if not is_admin(interaction):
            await interaction.response.send_message(
                "Only an administrator can post the raid checklist.",
                ephemeral=True,
            )
            return

        amounts = {
            "ladders": ladders,
            "rocketers": rocketers,
            "rockets each": rockets_each,
            "hv_rockets": hv_rockets,
            "incendiary_rockets": incendiary_rockets,
            "turrets": turrets,
            "adsr": adsr,
        }
        invalid_amounts = [
            name.replace("_", " ")
            for name, amount in amounts.items()
            if amount < 1
        ]
        if invalid_amounts:
            await interaction.response.send_message(
                f"These amounts must be at least 1: {', '.join(invalid_amounts)}.",
                ephemeral=True,
            )
            return

        config = raid_checklist_config(
            ladders,
            rocketers,
            rockets_each,
            hv_rockets,
            incendiary_rockets,
            turrets,
            adsr,
        )
        guild_data = await self.store.get_guild(interaction.guild.id)
        guild_data["config"] = config
        await interaction.response.send_message(
            embed=build_raid_checklist_embed(guild_data),
            view=build_raid_checklist_view(self.store),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        message = await interaction.original_response()
        await self.store.set_board(
            interaction.guild.id,
            interaction.channel.id,
            message.id,
            config=config,
        )


class RolesCog(commands.Cog):
    roles = app_commands.Group(name="roles", description="Check linked player role selections.")

    def __init__(
        self,
        bot: commands.Bot,
        link_store: LinkStore,
        team_store: TeamRoleStore,
        task_store: TeamRoleStore,
    ):
        self.bot = bot
        self.link_store = link_store
        self.team_store = team_store
        self.task_store = task_store

    @roles.command(
        name="missing",
        description="Ping linked players who still need a team role or task role.",
    )
    async def missing(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Missing role reminders can only be sent in a server.",
                ephemeral=True,
            )
            return

        if not is_admin(interaction):
            await interaction.response.send_message(
                "Only an administrator can ping missing role reminders.",
                ephemeral=True,
            )
            return

        team_channel = find_text_channel_by_name(interaction.guild, "team-role-select")
        task_channel = find_text_channel_by_name(interaction.guild, "task-role-select")
        missing_channels = []
        if team_channel is None:
            missing_channels.append("#team-role-select")
        if task_channel is None:
            missing_channels.append("#task-role-select")
        if missing_channels:
            await interaction.response.send_message(
                f"I couldn't find these channels: {', '.join(missing_channels)}.",
                ephemeral=True,
            )
            return

        link_data = await self.link_store.load()
        links = link_data["links"]
        if not links:
            await interaction.response.send_message(
                "No players are linked yet, so there is nobody to remind.",
                ephemeral=True,
            )
            return

        team_data = await self.team_store.get_guild(interaction.guild.id)
        task_data = await self.task_store.get_guild(interaction.guild.id)
        team_user_ids = assigned_user_ids(team_data)
        task_user_ids = assigned_user_ids(task_data)

        reminder_lines: list[str] = []
        for discord_id, record in sorted(
            links.items(),
            key=lambda item: (item[1].get("discord_name") or item[0]).lower(),
        ):
            has_team_role = discord_id in team_user_ids
            has_task_role = discord_id in task_user_ids

            if has_team_role and has_task_role:
                continue

            if has_team_role:
                reminder_lines.append(
                    f"<@{discord_id}> please choose one task role in {task_channel.mention}."
                )
            elif has_task_role:
                reminder_lines.append(
                    f"<@{discord_id}> please choose one team role in {team_channel.mention}."
                )
            else:
                reminder_lines.append(
                    f"<@{discord_id}> please choose one team role in {team_channel.mention} "
                    f"and one task role in {task_channel.mention}."
                )

        if not reminder_lines:
            await interaction.response.send_message(
                "Everyone in the linked list has selected both a team role and a task role.",
                ephemeral=True,
            )
            return

        chunks = chunk_lines(reminder_lines)
        allowed_mentions = discord.AllowedMentions(
            everyone=False,
            users=True,
            roles=False,
        )
        await interaction.response.send_message(
            f"Missing role selections ({len(reminder_lines)}):\n{chunks[0]}",
            allowed_mentions=allowed_mentions,
        )

        for chunk in chunks[1:]:
            await interaction.followup.send(
                chunk,
                allowed_mentions=allowed_mentions,
            )


class LinkBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.store = LinkStore(LINKS_FILE)
        self.team_store = TeamRoleStore(TEAM_ROLES_FILE)
        self.task_store = TeamRoleStore(
            TASK_ROLES_FILE,
            role_options=TASK_ROLE_OPTIONS,
            selection_label="task role",
        )
        self.raid_store = TeamRoleStore(
            RAID_CHECKLIST_FILE,
            role_options=RAID_CHECKLIST_OPTIONS,
            selection_label="raid checklist item",
        )

    async def setup_hook(self) -> None:
        self.add_view(build_team_role_view(self.team_store, self.store))
        self.add_view(build_task_role_view(self.task_store, self.store))
        self.add_view(build_raid_checklist_view(self.raid_store))
        await self.add_cog(LinkCog(self, self.store))
        await self.add_cog(TeamCog(self, self.team_store, self.store))
        await self.add_cog(TaskCog(self, self.task_store, self.store))
        await self.add_cog(RaidCog(self, self.raid_store))
        await self.add_cog(RolesCog(self, self.store, self.team_store, self.task_store))
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
