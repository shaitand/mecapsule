#############################
# mecapsule - Identity-aware agent Copyright (C) 2025 Michael R. Fread
#
# This program is free software: you can redistribute it and/or modify it under 
#the terms of the GNU General Public License as published by the Free Software 
#Foundation, either version 3 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY 
#WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A 
#PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
#You should have received a copy of the GNU General Public License along with this 
#program. If not, see https://www.gnu.org/licenses/. */
#############################
import argparse
import asyncio
import configparser
import logging
import os
import random
import socket
import sys
import time
import atexit
import string
from pathlib import Path

from irc3 import IrcBot, event, rfc, plugin
import irc3.plugins.command
from kademlia.network import Server
from miniupnpc import UPnP

DEFAULT_CONFIG_FILE = 'config.ini'
NODELIST_FILE = 'nodelist.txt'
DEFAULT_IRC_SERVER = 'irc.libera.chat'
DEFAULT_CHANNEL = '#soulfire'
DEFAULT_MODE = 'bot'
DEFAULT_BOOTSTRAP_COMMAND = '!bootstrap'
PORT_RANGE = (50000, 60000)
DEFAULT_IRC_PORT = 6697

parser = argparse.ArgumentParser(description="Ash DHT IRC Bot", add_help=False)
parser.add_argument('--config', default=DEFAULT_CONFIG_FILE, help='Path to config.ini file')
parser.add_argument('--irc_server', help='IRC server to connect to')
parser.add_argument('--channel', help='IRC channel to join')
parser.add_argument('--mode', choices=['bot', 'client'], help='Mode to run: bot or client')
parser.add_argument('--port', type=int, help='Port for DHT and UPnP')
parser.add_argument('--bootstrap_command', help='Command for bootstrap requests')
parser.add_argument('--debug', action='store_true', help='Enable debug logging')
parser.add_argument('--help', action='store_true', help='Show configuration environment variables and INI options')
args = parser.parse_args()

if args.help:
    print("""
Configuration Options:
----------------------
Command-Line Arguments:
  --config              Path to config.ini file
  --irc_server          IRC server to connect to
  --channel             IRC channel to join
  --mode                Mode to run: bot or client
  --port                Port for DHT and UPnP
  --bootstrap_command   Command for bootstrap requests
  --debug               Enable debug logging

Environment Variables:
  IRC_SERVER
  IRC_CHANNEL
  MODE
  PORT
  BOOTSTRAP_COMMAND
  DEBUG

Config File (INI) [default section expected]:
  irc_server
  channel
  mode
  port
  bootstrap_command
  debug
""")
    sys.exit(0)

config = configparser.ConfigParser()

if not os.path.exists(args.config):
    config['DEFAULT'] = {
        'irc_server': DEFAULT_IRC_SERVER,
        'channel': DEFAULT_CHANNEL,
        'mode': DEFAULT_MODE,
        'bootstrap_command': DEFAULT_BOOTSTRAP_COMMAND,
        'port': str(random.randint(*PORT_RANGE)),
        'debug': 'false'
    }
    with open(args.config, 'w') as f:
        config.write(f)

config.read(args.config)

def get_config_value(key, default):
    return (
        getattr(args, key, None)
        or os.getenv(key.upper())
        or config['DEFAULT'].get(key, default)
    )

irc_server = get_config_value('irc_server', DEFAULT_IRC_SERVER)
channel = get_config_value('channel', DEFAULT_CHANNEL)
mode = get_config_value('mode', DEFAULT_MODE)
bootstrap_command = get_config_value('bootstrap_command', DEFAULT_BOOTSTRAP_COMMAND)
port = int(get_config_value('port', random.randint(*PORT_RANGE)))
debug = args.debug or os.getenv('DEBUG', config['DEFAULT'].get('debug', 'false')).lower() in ['1', 'true', 'yes']

if debug:
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger('irc3').setLevel(logging.DEBUG)
    logging.getLogger('asyncio').setLevel(logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)

loop = asyncio.get_event_loop()
dht_server = Server()
upnp = UPnP()
upnp_success = False

def generate_safe_nick():
    safe_chars = string.ascii_letters + string.digits + "-_[]\\`^{}"
    first_char = random.choice(string.ascii_letters)
    remaining = ''.join(random.choices(safe_chars, k=11))
    return first_char + remaining

nick = generate_safe_nick()

def open_port(port):
    try:
        upnp.discover()
        upnp.selectigd()
        upnp.addportmapping(port, 'UDP', upnp.lanaddr, port, 'Ash DHT', '')
        print(f"Successfully opened port {port} via UPnP")
        return True
    except Exception as e:
        print(f"UPnP port mapping failed: {e}")
        return False

def close_port(port):
    try:
        upnp.deleteportmapping(port, 'UDP')
        print(f"Closed port {port} via UPnP")
    except Exception as e:
        print(f"Error closing port: {e}")

atexit.register(lambda: close_port(port))

async def get_public_ip():
    try:
        reader, writer = await asyncio.open_connection('api.ipify.org', 80)
        request = (
            "GET / HTTP/1.1\r\n"
            "Host: api.ipify.org\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        writer.write(request.encode())
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
        return response.decode().split('\r\n')[-1].strip()
    except Exception:
        return '0.0.0.0'

@plugin
class BootstrapBot:
    def __init__(self, bot):
        self.bot = bot

    @event(rfc.CONNECTED)
    def connected(self, **kw):
        self.bot.log.info("Connected to IRC, joining channel...")
        self.bot.join(channel)

    @event(rfc.JOIN)
    def joined(self, mask=None, channel=None, **kw):
        self.bot.log.info(f"Joined channel {channel}")

    @event(rfc.PRIVMSG)
    def on_message(self, mask=None, event=None, target=None, data=None, **kw):
        if data.strip() == bootstrap_command:
            asyncio.create_task(self.reply_bootstrap(mask.nick))

    async def reply_bootstrap(self, nick):
        retries = 0
        while dht_server.protocol is None and retries < 5:
            await asyncio.sleep(1)
            retries += 1

        if dht_server.protocol is None:
            self.bot.log.warning("DHT protocol still not initialized after wait.")
            return

        nodes = []
        for bucket in dht_server.protocol.router.buckets:
            for node in bucket.nodes:
                nodes.append((node.ip, node.port))
        if len(nodes) > 19:
            nodes = random.sample(nodes, 19)
        ip_port_list = [f"{ip}:{port}" for ip, port in nodes]
        public_ip = await get_public_ip()
        ip_port_list.append(f"{public_ip}:{port}")
        self.bot.privmsg(nick, bootstrap_command + ' ' + ' '.join(ip_port_list))

@plugin
class BootstrapClient:
    def __init__(self, bot):
        self.bot = bot
        self.future = loop.create_future()

    @event(rfc.CONNECTED)
    def connected(self, **kw):
        self.bot.log.info("Connected to IRC (Client), joining channel...")
        self.bot.join(channel)

    @event(rfc.JOIN)
    def joined(self, mask=None, channel=None, **kw):
        self.bot.log.info("Requesting bootstrap nodes via IRC")
        self.bot.privmsg(channel, bootstrap_command)

    @event(rfc.PRIVMSG)
    def on_message(self, mask=None, event=None, target=None, data=None, **kw):
        if data.startswith(bootstrap_command):
            ip_port_list = data.strip().split()[1:]
            with open(NODELIST_FILE, 'w') as f:
                for entry in ip_port_list:
                    f.write(entry + '\n')
            if not self.future.done():
                self.future.set_result(True)
            self.bot.quit("DHT bootstrap complete")
            self.bot.protocol.transport.close()

async def start_dht():
    await dht_server.listen(port)
    print(f"DHT node started on UDP port {port}")

async def update_nodelist():
    while True:
        if dht_server.protocol:
            nodes = []
            for bucket in dht_server.protocol.router.buckets:
                for node in bucket.nodes:
                    nodes.append((node.ip, node.port))
            if nodes:
                with open(NODELIST_FILE, 'w') as f:
                    for ip, port in nodes:
                        f.write(f"{ip}:{port}\n")
        await asyncio.sleep(300)

async def bootstrap_from_file():
    if not Path(NODELIST_FILE).exists():
        return False
    with open(NODELIST_FILE) as f:
        nodes = [line.strip().split(':') for line in f if ':' in line]
    for ip, port in nodes:
        try:
            await dht_server.bootstrap([(ip, int(port))])
            return True
        except Exception:
            continue
    return False

async def request_bootstrap_via_irc():
    retries = 0
    while retries < 5:
        bot = IrcBot(
            nick=nick,
            autojoins=[channel],
            host=irc_server,
            port=DEFAULT_IRC_PORT,
            includes=[__name__],
            verbose=debug,
            ssl=True,
            username="AshDHTClient",
            realname="Ash IRC Bootstrap",
            loop=loop,
            timeout=30,
            plugins=[BootstrapClient]
        )
        bot.run(forever=False)
        plugin = bot.get_plugin(BootstrapClient)
        try:
            await asyncio.wait_for(plugin.future, timeout=30)
            return True
        except asyncio.TimeoutError:
            print("❌ IRC connection timed out.")
            retries += 1
            await asyncio.sleep(min(5 * 2 ** retries, 300))
    return False

async def client_mode():
    retries = 0
    open_port(port)
    print("Failed to connect using nodelist, requesting via IRC")

    success = await bootstrap_from_file()
    if not success:
        success = await request_bootstrap_via_irc()

    if success:
        print("✅ Bootstrap successful!")
        await start_dht()
        await update_nodelist()
    else:
        while not success:
            retries += 1
            delay = min(5 * 2 ** retries, 300)
            print(f"⏳ Retry in {delay} seconds...")
            await asyncio.sleep(delay)
            success = await bootstrap_from_file()
            if not success:
                success = await request_bootstrap_via_irc()
            if success:
                print("✅ Bootstrap successful!")
                await start_dht()
                await update_nodelist()

async def bot_mode():
    open_port(port)
    await start_dht()
    bot = IrcBot(
        nick=nick,
        autojoins=[channel],
        host=irc_server,
        port=DEFAULT_IRC_PORT,
        includes=[__name__],
        verbose=debug,
        ssl=True,
        username="AshDHTBot",
        realname="Ash IRC Bot",
        loop=loop,
        timeout=30,
        plugins=[BootstrapBot, 'irc3.plugins.autojoins', 'irc3.plugins.command']
    )
    await asyncio.gather(
        asyncio.to_thread(bot.run, forever=False),
        update_nodelist()
    )

if __name__ == '__main__':
    if mode == 'bot':
        loop.run_until_complete(bot_mode())
    else:
        loop.run_until_complete(client_mode())
