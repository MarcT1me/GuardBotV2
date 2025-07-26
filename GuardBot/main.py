import os
from pprint import pformat

import discord
from discord.ext import commands
from aiohttp import web
import requests
from loguru import logger
import dotenv

dotenv.load_dotenv(dotenv_path="../.env")
API_URL = os.getenv("API_URL")


class GuardBotCog(commands.Cog):
    def __init__(self, b):
        self.bot = b

    @discord.app_commands.command(name="ping")
    async def ping_command(self, interaction: discord.Interaction):
        await interaction.response.send_message("Pong!")  # type: ignore

    @discord.app_commands.command(name="msg")
    async def msg_command(self, interaction: discord.Interaction):
        try:
            request = requests.get(
                API_URL + "/message/get",
                json={
                    "user_id": interaction.user.id,
                    "server_id": interaction.guild.id
                }
            ).json()

            if request.status_code != 200:
                raise Exception(f"Any error occurred: {request.status_code}")

            content = request.get('content')
            server = interaction.guild

            channel = server.system_channel
            user = interaction.user

            embed = discord.Embed(
                title="Сообщение от пользователя",
                description=content,
            )
            embed.footer.icon_url = user.display_icon.url
            embed.footer.text = f"owner: {user}"
            await channel.send(embed=embed)

            return web.json_response({"success": "sent"})

        except Exception as e:
            logger.error(f"msg command exception: {e}")
            return web.json_response({"error": str(e)}, status=500)


class GuardBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.messages = True
        intents.guilds = True
        super().__init__(command_prefix="/", intents=intents)

        self.site = None
        self.runner = None
        self.app = web.Application()

    async def setup_hook(self):
        await self.add_cog(GuardBotCog(self))

        logger.info("🌐 Starting HTTP server...")
        self.app.router.add_get('/health', self.health_check)
        self.app.router.add_post('/send_message', self.handle_send)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '0.0.0.0', 5000)
        await self.site.start()
        logger.info("🌐 HTTP server started on port 5000")

    @commands.Cog.listener()
    async def on_ready(self):
        await self.change_presence(activity=discord.CustomActivity("Ready to work!"))
        await self.tree.sync()
        logger.info(f"🤖 Bot {self.user} is ready!")

        guilds = {}
        for guild in self.guilds:
            members = {}
            for member in guild.members:
                members[member.id] = member.name
            guilds[guild.id] = {
                "name": guild.name,
                "members": members
            }

        logger.info(f"Guilds:\n{pformat(guilds)}")

    @staticmethod
    async def health_check(request):
        logger.info(f"🌐 Health check request: {request}")
        return web.json_response({"status": "ok"}, status=200)

    async def handle_send(self, request):
        logger.info(f"🌐 message send request: {request}")

        try:
            data = await request.json()
            logger.info(f"request data:\n{pformat(data)}")
            user_id = int(data["user_id"])
            server_id = int(data["server_id"])
            content = data["content"]

            server = self.get_guild(server_id)
            if not server:
                logger.warning(f"server not found")
                return web.json_response({"status": "error", "error": "Server not found"}, status=404)

            channel = server.system_channel
            if not channel:
                logger.warning(f"system_channel not found")
                return web.json_response({"status": "error", "error": "System channel not found"}, status=404)

            user = server.get_member(user_id)
            if not user:
                logger.warning(f"member not found")
                return web.json_response({"status": "error", "error": "User not found"}, status=404)

            embed = discord.Embed(
                title="Сообщение от пользователя",
                description=content,
                color=discord.Color.blue()
            )
            embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
            await channel.send(embed=embed)

            return web.json_response({"success": "sent"}, status=200)

        except Exception as e:
            logger.error(f"bot exception: {e}")
            return web.json_response({"status": "error", "error": str(e)}, status=500)

    async def close(self):
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        if self.app:
            await self.app.shutdown()
            self.app.clear()
        logger.info("🛑 HTTP server stopped")
        await super().close()


if __name__ == "__main__":
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    GuardBot().run(BOT_TOKEN)
