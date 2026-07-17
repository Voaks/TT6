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

## Steam Script

Run `/link generate`, download the generated `steam-link-friends.js`, then:

1. Sign in at `https://steamcommunity.com`.
2. Press `F12`.
3. Open the Console tab.
4. Paste and run the generated script.

The script sends Steam friend requests and tries to set each Steam nickname to the linked Discord username. Steam's browser endpoints are not a stable public API, so if Steam changes the nickname endpoint, the script will still keep going and log the nickname failure in the console.

## Notes

The bot stores links in `data/links.json`. Steam IDs should be SteamID64 values, usually 17 digits and commonly starting with `7656119`.
