import subprocess
import os
import tempfile
import textwrap


def test_help_output():
    result = subprocess.run(["python", "mecapsule.py", "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()


def test_env_override():
    env = os.environ.copy()
    env["MODE"] = "bot"
    env["IRC_SERVER"] = "irc.testserver.net"
    result = subprocess.run(["python", "mecapsule.py", "--help"], capture_output=True, text=True, env=env)
    assert result.returncode == 0  # Just checking it runs with env vars set


def test_config_override():
    with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
        tmp.write(textwrap.dedent("""
            [DEFAULT]
            mode = bot
            irc_server = irc.example.com
        """))
        tmp_path = tmp.name

    result = subprocess.run(["python", "mecapsule.py", "--config", tmp_path, "--help"], capture_output=True, text=True)
    os.unlink(tmp_path)
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
