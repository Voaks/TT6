# TT6 Team Add Bot

A basic Discord slash-command bot that links Discord users to SteamID64 values and generates a Steam Community browser script for sending friend requests and setting Steam nicknames.

## Setup

1. Install Python 3.11 or newer.
2. Install dependencies:

   ```powershell
   py -m pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env`.
4. Put your Discord bot token in `.env`:

   ```text
   DISCORD_TOKEN=your_bot_token_here
   ```

5. For faster slash-command updates during setup, add your Discord server ID:

   ```text
   DISCORD_GUILD_ID=123456789012345678
   ```

6. Run the bot:

   ```powershell
   py bot.py
   ```

## Commands

- `/link add steam_id:<steam id or profile URL>` links your Discord account to your SteamID64.
- `/link add steam_id:<steam id or profile URL> user:<member>` links another Discord user. This requires Administrator permission.
- `/link remove` removes your own link.
- `/link remove user:<member>` removes another user's link. This requires Administrator permission.
- `/link list` displays all Discord to Steam links.
- `/link generate` creates `steam-link-friends.js`, a browser console script for Steam Community.
- `/link generate clan_tag:TT6` creates the same script, but prefixes Steam nicknames like `TT6 Spartan`.
- `/team roles` posts the team role selection board. Administrator permission is required.
- `/team roles_reset` clears all team role selections and updates the active board when possible. Administrator permission is required.
- `/task roles` posts the task role selection board. Administrator permission is required.
- `/task roles_reset` clears all task role selections and updates the active board when possible. Administrator permission is required.
- `/roles missing` pings linked players who still need to select a team role, task role, or both. Administrator permission is required.

## Team Roles

The team role board uses green buttons and live-updates the message when members pick or remove a role. Members can only hold one team role at a time. To switch roles, they click their current role button again to unassign, then choose a new role.

Current roles:

- Build Team: max 2
- Farm Base and Clones: max 1
- Furnace Base: max 2
- Electricity and Industrial: max 2
- Monument Team: max 6
- Farm/Roam Team: no cap

## Task Roles

The task role board uses the same live-updating green button behavior as the team role board. Members can only hold one task role at a time. To switch task roles, they click their current task button again to unassign, then choose a new one.

Current task roles:

- Deployables (Doors, Embrasures, Lockers, Etc.): max 8
- Autolockers: max 1
- Nades/Smokes/Seal Mats: max 8
- Bed Placer: max 3

## Missing Role Reminders

Run `/roles missing` to ping linked players who have not selected both required boards. The bot looks for channels named `team-role-select` and `task-role-select`, then tells each missing player where to finish selecting.

## Steam Script

Run `/link generate`, download the generated `steam-link-friends.js`, then:

1. Sign in at `https://steamcommunity.com`.
2. Press `F12`.
3. Open the Console tab.
4. Paste and run the generated script.

The script sends Steam friend requests and tries to set each Steam nickname to the linked Discord username. Steam's browser endpoints are not a stable public API, so if Steam changes the nickname endpoint, the script will still keep going and log the nickname failure in the console.

## Notes

The bot stores links in `data/links.json`, team role selections in `data/team_roles.json`, and task role selections in `data/task_roles.json`. Steam IDs should be SteamID64 values, usually 17 digits and commonly starting with `7656119`.
