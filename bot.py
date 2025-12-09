import asyncio
import discord
from discord.ext import commands
from settings import DISCORD_TOKEN
from utils.server_manager import ServerManager

# Bot Setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Server Manager Instance
server_manager = ServerManager()
# Attach to bot so cogs can access it if needed (though we inject it)
bot.server_manager = server_manager

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

async def main():
    # Load Extensions
    initial_extensions = [
        'cogs.basic_control',
        'cogs.backup_system'
    ]

    for extension in initial_extensions:
        await bot.load_extension(extension)

    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
