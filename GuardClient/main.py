import json
from typing import Self
import time
import webbrowser
from threading import Thread
from typing import Callable
from http.server import HTTPServer, BaseHTTPRequestHandler

import dearpygui.dearpygui as dpg
import requests
from loguru import logger

BACKEND_URL = "http://localhost:8000"


class Application:
    WIDTH = 600
    HEIGHT = 500

    instance: Self

    def __init__(self):
        self.running = True
        self.main_window = None

        self.http_server = None
        self.server_thread = None

        self.user_id = None
        self.guilds = []
        self.selected_guild_id = None

        self.setup()
        Application.instance = self

        self.config = Config()

    def setup(self):
        dpg.create_context()

        self.setup_main_window()

        dpg.create_viewport(
            title='Discord Bot Client',
            width=Application.WIDTH, height=Application.HEIGHT,
            decorated=True, disable_close=False
        )
        dpg.set_primary_window(self.main_window, True)
        dpg.setup_dearpygui()
        dpg.show_viewport()

        dpg.set_exit_callback(self.close)

    def setup_main_window(self):
        with dpg.window(label="Main", width=Application.WIDTH, height=Application.HEIGHT) as self.main_window:
            with dpg.group(horizontal=True):
                dpg.add_text(tag="backend_status", default_value="Backand: <UNK>")
                dpg.add_text(tag="bot_status", default_value="Bot: <UNK>")

            dpg.add_text(tag="status", default_value="Status: Unauthorized")

            dpg.add_separator()

            with dpg.group(tag="auth_panel", horizontal=True):
                dpg.add_button(label="Login with Discord", callback=self.login_with_discord)

            dpg.add_separator()

            with dpg.group(tag="guild_panel", show=False):
                dpg.add_text("Select Discord server:")
                dpg.add_combo(tag="guild_combo", width=300, callback=self.on_guild_selected)

            dpg.add_separator()

            with dpg.group(tag="message_panel", show=False):
                dpg.add_input_text(tag="input_text", width=500, hint="Enter message")

                with dpg.group(horizontal=True):
                    dpg.add_button(label="Send", callback=lambda: self.thread_func(self.send_message))
                    dpg.add_button(label="Save", callback=lambda: self.thread_func(self.save_message))
                    dpg.add_button(label="Restore", callback=lambda: self.thread_func(self.reset_message))
                    dpg.add_button(label="Get", callback=lambda: self.thread_func(self.get_message))

            dpg.add_separator()

            dpg.add_button(label="Exit", callback=self.exit_callback)

    def start_auth_server(self):
        if self.http_server:
            return

        def run_server():
            logger.info("🌐 Starting HTTP server...")
            server_address = ('', 3000)
            self.http_server = HTTPServer(server_address, AuthHandler)
            self.http_server.app = self  # Для доступа к приложению из обработчика
            logger.info("Auth server started on port 3000")
            self.http_server.serve_forever()

        self.thread_func(run_server)

    def stop_auth_server(self):
        """Останавливает HTTP-сервер"""
        if self.http_server:
            self.http_server.shutdown()
            self.http_server.server_close()
            self.http_server = None
            logger.info("Auth server stopped")

    def login_with_discord(self):
        self.start_auth_server()

        webbrowser.open(f"{BACKEND_URL}/auth/login")

        dpg.set_value("status", "Status: Check your browser for Discord login")

    def auth_callback(self, state):
        try:
            response = Request(
                Request.Method.Get,
                f"{BACKEND_URL}/user/session?state={state}"
            )
            resp = response.json()
            logger.info(f"Auth callback: {response.status_code}, {type(resp)}, {resp}")

            if response.status_code == 200:
                self.user_id = resp.get("user_id")
                self.guilds = resp.get("guilds", [])

                dpg.configure_item("auth_panel", show=False)
                dpg.configure_item("guild_panel", show=True)

                guild_names = [g['name'] for g in self.guilds]
                dpg.configure_item("guild_combo", items=guild_names)

                dpg.set_value("status", f"Status: Authorized ({resp.get('status', 'err')}) - {self.user_id}")

                self.config.state = state

                self.stop_auth_server()
            else:
                logger.error(f"Failed to get session: {response.status_code}")
                dpg.set_value(
                    "status", f"Status: Auth failed ({response.status_code}): {resp.get('status', 'err')}"
                )
        except Exception as e:
            logger.error(f"Auth check error: {e}")

    def on_guild_selected(self, _, app_data):
        selected_guild_name = app_data
        for guild in self.guilds:
            if guild['name'] == selected_guild_name:
                self.selected_guild_id = guild['id']
                self.config.server_id = guild['id']
                dpg.set_value("status", f"Status: Selected server: {selected_guild_name}")
                dpg.configure_item("message_panel", show=True)
                break

    def save_message(self):
        if not self.user_id or not self.selected_guild_id:
            dpg.set_value("status", "Status: Not authenticated or server not selected")
            return

        text = dpg.get_value("input_text")

        response = Request(
            method=Request.Method.Post,
            url=f"{BACKEND_URL}/message/save",
            data={
                "user_id": self.user_id,
                "server_id": self.selected_guild_id,
                "content": text
            }
        )

        resp = response.json()
        logger.info(f"save: {response.status_code}, {type(resp)}, {resp}")

        if response.status_code == 200:
            dpg.set_value("status", "Status: Message saved")
        else:
            dpg.set_value("status", f"Status: Save failed ({response.status_code})")

    def reset_message(self):
        if not self.user_id or not self.selected_guild_id:
            dpg.set_value("status", "Status: Not authenticated or server not selected")
            return

        response = Request(
            method=Request.Method.Post,
            url=f"{BACKEND_URL}/message/reset",
            data={
                "user_id": self.user_id,
                "server_id": self.selected_guild_id,
            }
        )

        resp = response.json()
        logger.info(f"reset: {response.status_code}, {type(resp)}, {resp}")

        if response.status_code == 200:
            self.get_message()
            dpg.set_value("status", f"Restore: {resp.get('status', 'err')}")
        else:
            dpg.set_value("status", f"Status: Resset failed ({response.status_code})")

    def send_message(self):
        if not self.user_id or not self.selected_guild_id:
            dpg.set_value("status", "Status: Not authenticated or server not selected")
            return

        response = Request(
            method=Request.Method.Post,
            url=f"{BACKEND_URL}/message/send",
            data={
                "user_id": self.user_id,
                "server_id": self.selected_guild_id,
            }
        )

        resp = response.json()
        logger.info(f"send: {response.status_code}, {type(resp)}, {resp}")

        if response.status_code == 200:
            dpg.set_value("status", f"Sending: {resp.get('status', 'err')}")
        else:
            dpg.set_value("status", f"Status: Send failed ({response.status_code})")

    def get_message(self):
        if not self.user_id or not self.selected_guild_id:
            dpg.set_value("status", "Status: Not authenticated or server not selected")
            return

        response = Request(
            method=Request.Method.Get,
            url=f"{BACKEND_URL}/message/get",
            data={
                "user_id": self.user_id,
                "server_id": self.selected_guild_id,
            }
        )

        resp = response.json()
        logger.info(f"get: {response.status_code}, {type(resp)}, {resp}")

        if response.status_code == 200:
            dpg.set_value("input_text", resp.get("content", "err"))
            dpg.set_value("status", f"Get: {resp.get('status', 'err')}")
        else:
            dpg.set_value("status", f"Status: Get failed ({response.status_code})")

    @staticmethod
    def thread_func(func):
        Thread(target=func, daemon=True).start()

    def exit_callback(self):
        logger.info("exit")
        self.running = False

    def run(self):
        self.thread_func(self.status_updater)

        self.config.open()

        if self.config.state:
            self.auth_callback(self.config.state)
            if self.config.server_id:
                guild = None
                for guild in self.guilds:
                    if guild["id"] == self.config.server_id:
                        guild = guild
                        break
                if guild:
                    dpg.configure_item("guild_combo", default_value=guild["name"])

        while self.running:
            dpg.render_dearpygui_frame()

        self.config.save()

        self.close()

    def status_updater(self):
        while self.running:
            self.update_status()
            time.sleep(5)

    @staticmethod
    def update_status():
        try:
            response = requests.get(f"{BACKEND_URL}/test/health")
            backend_status = "OK" if response.status_code == 200 else "Error"
            dpg.set_value("backend_status", f"Backend: {backend_status}")

            if response.status_code == 200:
                response = requests.get(f"{BACKEND_URL}/test/test_bot")
                bot_status = "OK" if response.status_code == 200 else "Error"
                dpg.set_value("bot_status", f"Bot: {bot_status}")
        except:
            dpg.set_value("backend_status", "Backend: Unavailable")
            dpg.set_value("bot_status", "Bot: Unavailable")

    def close(self):
        dpg.destroy_context()
        self.stop_auth_server()


class Config:
    def __init__(self):
        self.state = None
        self.server_id = None

    def open(self):
        with open("cfg.json", "r") as f:
            data = json.load(f)
            self.state = data.get("status")
            self.server_id = data.get("server_id")

    def save(self):
        with open("cfg.json", "w") as f:
            data = {
                "status": self.state,
                "server_id": self.server_id,
            }
            json.dump(data, f)


class Request:
    class Method:
        Get = requests.get
        Post = requests.post
        Put = requests.put

    def __init__(self, method: Callable | Method, url, data={}):
        self.response = None
        try:
            self.response = method(url, json=data)
        except Exception as e:
            logger.exception("error in request", str(e))

    @property
    def status_code(self):
        return self.response.status_code if self.response else 500

    def json(self):
        return self.response.json() if self.response else {}


class AuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/auth-success'):
            # Извлекаем параметры из URL
            params = {}
            if '?' in self.path:
                query = self.path.split('?')[1]
                params = dict(qc.split('=') for qc in query.split('&'))

            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(
                b'<html>'
                b'<body><h1>Authentication successful! You can close this window.</h1></body>'
                b'</html>'
            )

            Application.instance.auth_callback(params.get('state', ''))
        else:
            self.send_error(404, "Not Found")


if __name__ == "__main__":
    app = Application()
    app.run()
    exit(0)
