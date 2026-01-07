import asyncio
import discord
from discord.ext import commands
from settings import DISCORD_TOKEN, SERVERS_CONFIG
from utils.server_manager import MultiServerManager

# Bot Class Definition
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        # Prefix commands are disabled as per user request (Slash commands only)
        super().__init__(command_prefix=[], intents=intents)
        self.server_manager = MultiServerManager(SERVERS_CONFIG)

    async def setup_hook(self):
        # Load Extensions
        initial_extensions = [
            'cogs.basic_control',
            'cogs.backup_system',
            'cogs.plugin_system'
        ]
        
        for extension in initial_extensions:
            await self.load_extension(extension)
            
        # Sync Slash Commands
        # Note: In production, it's better to sync to a specific guild for faster updates during dev,
        # or use a command to sync globally. For now, we sync globally on startup.
        print("Syncing commands...")
        await self.tree.sync()
        print("Commands synced.")

    async def on_ready(self):
        print(f'Logged in as {self.user}')

    async def on_message(self, message):
        # Disable prefix commands by not calling process_commands
        pass

# Bot Setup
bot = MyBot()

async def main():
    async with bot:
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
