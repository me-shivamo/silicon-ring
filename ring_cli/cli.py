"""silicon-ring CLI — call Carbons from the terminal.

Usage:
    silicon-ring config set endpoint https://ring.example.com
    silicon-ring config set api-key scs_live_...
    silicon-ring call shivam
    silicon-ring call shivam --message "want to discuss deploy"
    silicon-ring call shivam --async
    silicon-ring status <call_id>
    silicon-ring transcript <call_id>

Config is stored in ~/.silicon-ring/config.toml.
Environment variable overrides: SILICON_RING_ENDPOINT, SILICON_RING_API_KEY.
"""
from __future__ import annotations

import json
import os
import sys
import time
import tomllib
from pathlib import Path

import click
import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path.home() / ".silicon-ring"
_CONFIG_FILE = _CONFIG_DIR / "config.toml"
_CONFIG_TOML_TEMPLATE = 'endpoint = ""\napi_key = ""\n'


def _load_config() -> dict:
    cfg = {
        "endpoint": os.environ.get("SILICON_RING_ENDPOINT", ""),
        "api_key": os.environ.get("SILICON_RING_API_KEY", ""),
    }
    if _CONFIG_FILE.exists():
        with open(_CONFIG_FILE, "rb") as f:
            file_cfg = tomllib.load(f)
        # Env vars override file
        cfg["endpoint"] = cfg["endpoint"] or file_cfg.get("endpoint", "")
        cfg["api_key"] = cfg["api_key"] or file_cfg.get("api_key", "")
    return cfg


def _save_config(key: str, value: str) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if _CONFIG_FILE.exists():
        with open(_CONFIG_FILE, "rb") as f:
            existing = tomllib.load(f)
    existing[key] = value
    with open(_CONFIG_FILE, "w") as f:
        for k, v in existing.items():
            f.write(f'{k} = "{v}"\n')


def _get_headers(cfg: dict) -> dict:
    return {"X-Silicon-Key": cfg["api_key"]}


def _base_url(cfg: dict) -> str:
    return cfg["endpoint"].rstrip("/")


def _require_config(cfg: dict) -> None:
    if not cfg.get("endpoint"):
        click.echo(
            "Error: endpoint not configured.\n"
            "Run: silicon-ring config set endpoint https://ring.yourdomain.com",
            err=True,
        )
        sys.exit(1)
    if not cfg.get("api_key"):
        click.echo(
            "Error: api_key not configured.\n"
            "Run: silicon-ring config set api-key scs_live_...",
            err=True,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """silicon-ring — call Carbons from any Silicon agent terminal."""


# --- config ---

@cli.group()
def config():
    """Manage silicon-ring CLI configuration."""


@config.command("set")
@click.argument("key", metavar="KEY")
@click.argument("value", metavar="VALUE")
def config_set(key: str, value: str):
    """Set a config value (endpoint or api-key)."""
    normalized = key.replace("-", "_")
    if normalized not in ("endpoint", "api_key"):
        click.echo(f"Unknown config key '{key}'. Valid keys: endpoint, api-key", err=True)
        sys.exit(1)
    _save_config(normalized, value)
    click.echo(f"Set {normalized} = {value!r}")


@config.command("show")
def config_show():
    """Show current configuration."""
    cfg = _load_config()
    key = cfg.get("api_key", "")
    masked = key[:12] + "..." if len(key) > 12 else key
    click.echo(f"endpoint: {cfg.get('endpoint', '(not set)')}")
    click.echo(f"api_key:  {masked or '(not set)'}")


# --- call ---

@cli.command("call")
@click.argument("carbon_username")
@click.option("--message", "-m", default="", help="Context/agenda passed to the AI.")
@click.option("--async", "async_mode", is_flag=True, help="Return immediately (don't wait for call to end).")
@click.option("--poll-interval", default=3, show_default=True, help="Seconds between status polls.")
@click.option("--timeout", default=600, show_default=True, help="Max seconds to wait for call to end.")
def call_carbon(carbon_username: str, message: str, async_mode: bool, poll_interval: int, timeout: int):
    """Initiate a voice call to CARBON_USERNAME.

    By default, blocks until the call ends and prints the transcript.
    Use --async to return immediately with the call_id.
    """
    cfg = _load_config()
    _require_config(cfg)

    # Initiate
    try:
        resp = httpx.post(
            f"{_base_url(cfg)}/api/v1/calls/initiate",
            json={"carbon_username": carbon_username, "message": message},
            headers=_get_headers(cfg),
            timeout=15,
        )
    except httpx.RequestError as exc:
        click.echo(f"Error: cannot reach silicon-ring: {exc}", err=True)
        sys.exit(1)

    if resp.status_code == 404:
        data = resp.json()
        click.echo(f"Error: {data.get('detail', 'Carbon not found.')}", err=True)
        sys.exit(1)

    if resp.status_code not in (200, 202):
        click.echo(f"Error: silicon-ring returned HTTP {resp.status_code}: {resp.text[:300]}", err=True)
        sys.exit(1)

    data = resp.json()
    call_id = data["call_id"]
    click.echo(f"Call initiated. call_id={call_id}  status=ringing")

    if async_mode:
        return

    # Blocking mode: poll until ended/missed/failed, then print transcript
    click.echo(f"Waiting for {carbon_username} to answer...")
    deadline = time.time() + timeout
    last_status = "ringing"

    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            st_resp = httpx.get(
                f"{_base_url(cfg)}/api/v1/calls/{call_id}",
                headers=_get_headers(cfg),
                timeout=10,
            )
        except httpx.RequestError:
            continue

        if st_resp.status_code != 200:
            continue

        st = st_resp.json()
        status = st.get("status", "")

        if status != last_status:
            click.echo(f"  → {status}")
            last_status = status

        if status in ("ended", "missed", "failed"):
            break
    else:
        click.echo("Timeout waiting for call to finish. Use `silicon-ring transcript <call_id>` later.")
        return

    # Fetch and print transcript
    _print_transcript(call_id, cfg)


# --- status ---

@cli.command("status")
@click.argument("call_id")
def call_status(call_id: str):
    """Show the status of a call."""
    cfg = _load_config()
    _require_config(cfg)

    try:
        resp = httpx.get(
            f"{_base_url(cfg)}/api/v1/calls/{call_id}",
            headers=_get_headers(cfg),
            timeout=10,
        )
    except httpx.RequestError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if resp.status_code == 404:
        click.echo("Call not found.", err=True)
        sys.exit(1)

    data = resp.json()
    click.echo(f"call_id:   {data['call_id']}")
    click.echo(f"status:    {data['status']}")
    click.echo(f"carbon:    {data['carbon_username']}")
    click.echo(f"initiated: {data['initiated_at']}")
    if data.get("answered_at"):
        click.echo(f"answered:  {data['answered_at']}")
    if data.get("ended_at"):
        click.echo(f"ended:     {data['ended_at']}")


# --- transcript ---

@cli.command("transcript")
@click.argument("call_id")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def call_transcript(call_id: str, as_json: bool):
    """Print the transcript for a completed call."""
    cfg = _load_config()
    _require_config(cfg)
    _print_transcript(call_id, cfg, as_json=as_json)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_transcript(call_id: str, cfg: dict, as_json: bool = False) -> None:
    try:
        resp = httpx.get(
            f"{_base_url(cfg)}/api/v1/calls/{call_id}/transcript",
            headers=_get_headers(cfg),
            timeout=10,
        )
    except httpx.RequestError as exc:
        click.echo(f"Error fetching transcript: {exc}", err=True)
        return

    if resp.status_code == 404:
        click.echo("Transcript not found (call may still be active).", err=True)
        return

    if resp.status_code != 200:
        click.echo(f"Error: HTTP {resp.status_code}", err=True)
        return

    data = resp.json()

    if as_json:
        click.echo(json.dumps(data, indent=2))
        return

    click.echo(f"\n── Call transcript ── {call_id}")
    click.echo(f"Status:  {data.get('status', '?')}")
    if data.get("summary"):
        click.echo(f"Summary: {data['summary']}")
    click.echo("")

    turns = data.get("turns", [])
    if not turns:
        click.echo("(no transcript recorded)")
        return

    for turn in turns:
        speaker = turn["speaker"].upper()
        text = turn["text"]
        click.echo(f"[{speaker}] {text}")

    click.echo("")


def main():
    cli()


if __name__ == "__main__":
    main()
