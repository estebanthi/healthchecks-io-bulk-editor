#!/usr/bin/env python3
"""
hc-bulk — bulk edit Healthchecks.io checks

Usage examples
-------------
# Preview checks tagged "prod" whose name contains "backup", add a "okazo" tag
hc-bulk bulk-update --tags prod --name-re 'backup' --add-tags okazo --dry-run

# Replace tags on all "dev" checks, set cron schedule + tz
hc-bulk bulk-update --tags dev --set-tags 'dev daily' --set-schedule '0 3 * * *' --set-tz 'Europe/Paris' -y

# Pause all checks matching a slug regex
hc-bulk bulk-update --slug-re '^worker-' --pause -y

# List checks quickly
hc-bulk ls --tags prod --name-re 'etl'
"""
from __future__ import annotations

import os
import re
import time
from typing import Iterable, List, Optional

import click
from dotenv import load_dotenv
from loguru import logger
from tqdm import tqdm

from healthchecks_io import (
    Client,
    Check,
    CheckUpdate,
    HCAPIRateLimitError,
    HCAPIAuthError,
    HCAPIError,
)

load_dotenv()

# ---------- Client helpers ----------

def make_client(
    api_key: Optional[str],
    ping_key: Optional[str],
    api_url: Optional[str],
) -> Client:
    api_key = api_key or os.getenv("HC_API_KEY") or os.getenv("HEALTHCHECKS_API_KEY")
    if not api_key:
        raise click.UsageError("Missing API key. Use --api-key or set HC_API_KEY.")
    return Client(
        api_key=api_key,
        ping_key=ping_key or os.getenv("HC_PING_KEY"),
        api_url=(api_url or os.getenv("HC_API_URL") or "https://healthchecks.io/api/"),
    )


def fetch_checks(client: Client, tags: List[str] | None) -> List[Check]:
    # healthchecks-io supports filtering by a single tag per request;
    # the client’s get_checks(tags=[...]) handles multiple (AND semantics).
    return client.get_checks(tags=tags or None)  # type: ignore[arg-type]


# ---------- Filtering ----------

def _match_regex(val: str | None, pattern: Optional[re.Pattern]) -> bool:
    if pattern is None:
        return True
    return bool(val and pattern.search(val))


def _match_status(val: str | None, statuses: set[str] | None) -> bool:
    if not statuses:
        return True
    return (val or "").lower() in statuses


def select_checks(
    checks: Iterable[Check],
    name_re: Optional[re.Pattern],
    slug_re: Optional[re.Pattern],
    statuses: set[str] | None,
) -> List[Check]:
    selected: List[Check] = []
    for c in checks:
        if not _match_regex(c.name, name_re):
            continue
        if not _match_regex(c.slug, slug_re):
            continue
        if not _match_status(c.status, statuses):
            continue
        selected.append(c)
    return selected


# ---------- Tag utilities ----------

def compute_tags(
    current: str | None,
    set_tags: Optional[str],
    add_tags: Optional[str],
    remove_tags: Optional[str],
) -> Optional[str]:
    """Return new tag string or None (no change)."""
    if set_tags is not None:
        return set_tags.strip()

    tags = set((current or "").split())
    if add_tags:
        tags |= set(add_tags.split())
    if remove_tags:
        tags -= set(remove_tags.split())
    # If no change, return None to leave unchanged.
    new = " ".join(sorted(tags)).strip()
    return new if new != (current or "") else None


# ---------- Update application ----------

def build_update(
    check: Check,
    *,
    set_name: Optional[str],
    set_desc: Optional[str],
    set_tags: Optional[str],
    add_tags: Optional[str],
    remove_tags: Optional[str],
    set_timeout: Optional[int],
    set_grace: Optional[int],
    set_schedule: Optional[str],
    set_tz: Optional[str],
    set_methods: Optional[str],
    set_channels: Optional[str],
    manual_resume: Optional[bool],
) -> Optional[CheckUpdate]:
    # tags
    tags_new = compute_tags(check.tags, set_tags, add_tags, remove_tags)
    # Basic payload – only include fields you want to change; omitted ones stay unchanged
    payload = CheckUpdate(
        name=set_name,
        desc=set_desc,
        tags=tags_new,
        timeout=set_timeout,
        grace=set_grace,
        schedule=set_schedule,
        tz=set_tz,
        methods=set_methods,
        channels=set_channels,  # comma-separated integration IDs (string)
        manual_resume=manual_resume,
        unique=None,
    )

    # If pause requested, we don't put it in CheckUpdate (pause is its own API call)
    # Decide whether payload is "empty" (no field set)
    if (
        payload.name is None
        and payload.desc is None
        and payload.tags is None
        and payload.timeout is None
        and payload.grace is None
        and payload.schedule is None
        and payload.tz is None
        and payload.methods is None
        and payload.channels is None
        and payload.manual_resume is None
    ):
        return None
    return payload


def retry_on_ratelimit(func, *, max_sleep: float = 8.0):
    """Simple exponential backoff wrapper for 429s."""
    def wrapper(*args, **kwargs):
        delay = 1.0
        while True:
            try:
                return func(*args, **kwargs)
            except HCAPIRateLimitError as e:
                logger.warning(f"Rate limited: {e}; sleeping {delay:.1f}s")
                time.sleep(delay)
                delay = min(max_sleep, delay * 2)
    return wrapper


# ---------- CLI ----------

@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option()
def cli():
    """Bulk tools for Healthchecks.io."""


@cli.command("ls")
@click.option("--api-key", envvar="HC_API_KEY", help="Full-access API key.")
@click.option("--ping-key", envvar="HC_PING_KEY", help="Project ping key (optional).")
@click.option("--api-url", envvar="HC_API_URL", help="Management API base URL.")
@click.option("--tags", "-t", multiple=True, help="Filter by tag(s). Can repeat.")
@click.option("--name-re", help="Regex filter on check name.")
@click.option("--slug-re", help="Regex filter on check slug.")
@click.option(
    "--status",
    "statuses",
    multiple=True,
    type=click.Choice(["new", "up", "down", "grace", "paused"], case_sensitive=False),
    help="Filter by status (can repeat).",
)
def cmd_ls(api_key, ping_key, api_url, tags, name_re, slug_re, statuses):
    """List checks after applying filters."""
    client = make_client(api_key, ping_key, api_url)
    checks = fetch_checks(client, list(tags) or None)

    name_rx = re.compile(name_re) if name_re else None
    slug_rx = re.compile(slug_re) if slug_re else None
    statuses_set = set(s.lower() for s in statuses) if statuses else None
    selected = select_checks(checks, name_rx, slug_rx, statuses_set)

    click.echo(f"{len(selected)} check(s) matched.")
    for c in selected:
        click.echo(
            f"- {c.name or '(no-name)'}  "
            f"[{c.status}]  tags='{c.tags or ''}'  slug='{c.slug or ''}'  uuid={c.uuid}"
        )


@cli.command("bulk-update")
@click.option("--api-key", envvar="HC_API_KEY", help="Full-access API key.")
@click.option("--ping-key", envvar="HC_PING_KEY", help="Project ping key (optional).")
@click.option("--api-url", envvar="HC_API_URL", help="Management API base URL.")
# Selection
@click.option("--tags", "-t", multiple=True, help="Filter by tag(s). Can repeat.")
@click.option("--name-re", help="Regex filter on check name.")
@click.option("--slug-re", help="Regex filter on check slug.")
@click.option(
    "--status",
    "statuses",
    multiple=True,
    type=click.Choice(["new", "up", "down", "grace", "paused"], case_sensitive=False),
    help="Filter by status (can repeat).",
)
# Updates
@click.option("--set-name")
@click.option("--set-desc")
@click.option("--set-tags", help="Replace tags entirely with this string.")
@click.option("--add-tags", help="Space-separated tags to add.")
@click.option("--remove-tags", help="Space-separated tags to remove.")
@click.option("--set-timeout", type=int, help="Simple schedule period, seconds.")
@click.option("--set-grace", type=int, help="Grace time, seconds.")
@click.option("--set-schedule", help="Cron/OnCalendar expression.")
@click.option("--set-tz", help="IANA timezone for cron schedules, e.g., Europe/Paris.")
@click.option("--set-methods", help="Allowed HTTP methods, e.g., 'POST'.")
@click.option("--set-channels", help="Comma-separated integration IDs to notify.")
@click.option(
    "--manual-resume/--no-manual-resume",
    default=None,
    help="Require manual resume after failure.",
)
@click.option("--pause", is_flag=True, help="Pause matching checks.")
# Safety & UX
@click.option("--dry-run", is_flag=True, help="Show what would change, do nothing.")
@click.option("-y", "--yes", is_flag=True, help="Do not prompt for confirmation.")
@click.option("--progress/--no-progress", default=True, help="Show a progress bar.")
def cmd_bulk_update(
    api_key: Optional[str],
    ping_key: Optional[str],
    api_url: Optional[str],
    tags: Iterable[str],
    name_re: Optional[str],
    slug_re: Optional[str],
    statuses: Iterable[str],
    set_name: Optional[str],
    set_desc: Optional[str],
    set_tags: Optional[str],
    add_tags: Optional[str],
    remove_tags: Optional[str],
    set_timeout: Optional[int],
    set_grace: Optional[int],
    set_schedule: Optional[str],
    set_tz: Optional[str],
    set_methods: Optional[str],
    set_channels: Optional[str],
    manual_resume: Optional[bool],
    pause: bool,
    dry_run: bool,
    yes: bool,
    progress: bool = True,
):
    """Bulk edit checks: select by filters, then apply updates and/or pause."""
    client = make_client(api_key, ping_key, api_url)
    checks = fetch_checks(client, list(tags) or None)

    name_rx = re.compile(name_re) if name_re else None
    slug_rx = re.compile(slug_re) if slug_re else None
    statuses_set = set(s.lower() for s in statuses) if statuses else None
    selected = select_checks(checks, name_rx, slug_rx, statuses_set)

    if not selected:
        click.echo("No checks matched filters.")
        return

    click.echo(f"{len(selected)} check(s) matched. Preview:")
    for c in selected:
        click.echo(
            f"- {c.name or '(no-name)'} [{c.status}] tags='{c.tags or ''}' uuid={c.uuid}"
        )

    # Build per-check update objects (only changed fields are included)
    plan: list[tuple[Check, Optional[CheckUpdate], bool]] = []
    for c in selected:
        upd = build_update(
            c,
            set_name=set_name,
            set_desc=set_desc,
            set_tags=set_tags,
            add_tags=add_tags,
            remove_tags=remove_tags,
            set_timeout=set_timeout,
            set_grace=set_grace,
            set_schedule=set_schedule,
            set_tz=set_tz,
            set_methods=set_methods,
            set_channels=set_channels,
            manual_resume=manual_resume,
        )
        plan.append((c, upd, pause))

    # Summarize planned actions
    to_update = [p for p in plan if p[1] is not None]
    to_pause = [p for p in plan if p[2]]
    click.echo(
        f"\nPlanned: {len(to_update)} update(s)"
        + (f", {len(to_pause)} pause(s)" if pause else "")
        + (" (dry-run)" if dry_run else "")
    )

    if not yes and not dry_run:
        if not click.confirm("Proceed?", default=False):
            click.echo("Aborted.")
            return

    # Execute
    if dry_run:
        return

    do_update = retry_on_ratelimit(client.update_check)
    do_pause = retry_on_ratelimit(client.pause_check)

    iterable = tqdm(plan, disable=not progress, desc="Applying")  # type: ignore[arg-type]
    errors = 0
    for c, upd, want_pause in iterable:
        try:
            if upd is not None:
                _ = do_update(c.uuid, upd)  # returns the updated Check
            if want_pause:
                _ = do_pause(c.uuid)
        except (HCAPIAuthError, HCAPIError) as e:
            errors += 1
            logger.error(f"{c.name or c.uuid}: {e}")

    if errors:
        raise SystemExit(f"Done with {errors} error(s).")
    click.echo("Done.")

# Entrypoint
if __name__ == "__main__":
    cli()
