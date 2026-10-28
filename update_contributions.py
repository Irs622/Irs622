#!/usr/bin/env python3
"""Update the contribution squares in profile-banner.svg from GitHub GraphQL."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SVG_PATH = ROOT / "profile-banner.svg"
USERNAME = os.getenv("GITHUB_USERNAME", "Irs622")
TOKEN = os.getenv("CONTRIBUTION_TOKEN")

LEVEL_COLORS = {
    "NONE": "#161B22",
    "FIRST_QUARTILE": "#0E4429",
    "SECOND_QUARTILE": "#006D32",
    "THIRD_QUARTILE": "#26A641",
    "FOURTH_QUARTILE": "#39D353",
}

QUERY = """
query ContributionBanner($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
            contributionLevel
          }
        }
      }
    }
  }
}
"""


def graphql_contributions() -> dict:
    if not TOKEN:
        raise RuntimeError(
            "Missing CONTRIBUTION_TOKEN. Add a GitHub Actions secret named "
            "CONTRIBUTION_TOKEN with a token that can read user profile data."
        )

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=365)
    variables = {
        "login": USERNAME,
        "from": start.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "to": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    payload = json.dumps({"query": QUERY, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Irs622-contribution-banner",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL request failed ({exc.code}): {body}") from exc

    if result.get("errors"):
        raise RuntimeError("GitHub GraphQL error: " + json.dumps(result["errors"]))
    user = result.get("data", {}).get("user")
    if not user:
        raise RuntimeError(f"GitHub user {USERNAME!r} was not found or is inaccessible.")
    return user["contributionsCollection"]["contributionCalendar"]


def render_grid(calendar: dict) -> tuple[str, str, str]:
    # GitHub normally returns 53 columns for a rolling year. Keep the newest 53,
    # padding older weeks if needed so the layout remains consistent.
    weeks = calendar.get("weeks", [])[-53:]
    while len(weeks) < 53:
        weeks.insert(0, {"contributionDays": []})

    x0, y0 = 560.0, 158.0
    step_x, step_y, cell = 7.0, 7.0, 5.4
    rects: list[str] = []
    month_labels: list[str] = []
    seen_months: set[str] = set()

    for col, week in enumerate(weeks):
        days = week.get("contributionDays", [])
        if not days:
            days = [{"date": "", "contributionCount": 0, "contributionLevel": "NONE"} for _ in range(7)]
        days = sorted(days, key=lambda day: day.get("date", ""))

        for row in range(7):
            day = days[row] if row < len(days) else {
                "date": "", "contributionCount": 0, "contributionLevel": "NONE"
            }
            date = day.get("date", "")
            level = day.get("contributionLevel", "NONE")
            color = LEVEL_COLORS.get(level, LEVEL_COLORS["NONE"])
            count = int(day.get("contributionCount", 0))
            title = f'{date}: {count} contribution' + ('' if count == 1 else 's')
            # XML-escape title characters (dates/counts are safe, but keep this robust).
            title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            rects.append(
                f'<rect x="{x0 + col * step_x:.1f}" y="{y0 + row * step_y:.1f}" '
                f'width="{cell:.1f}" height="{cell:.1f}" rx="1.2" fill="{color}" '
                f'data-date="{date}" data-count="{count}"><title>{title}</title></rect>'
            )

            # Mark the start of each month above the corresponding week.
            if date:
                try:
                    parsed = datetime.strptime(date, "%Y-%m-%d")
                    month_key = parsed.strftime("%Y-%m")
                    if parsed.day == 1 and month_key not in seen_months:
                        seen_months.add(month_key)
                        label = parsed.strftime("%b")
                        month_labels.append(
                            f'<text x="{x0 + col * step_x:.1f}" y="146" '
                            f'fill="#8B949E">{label}</text>'
                        )
                except ValueError:
                    pass

    # Count the exact days rendered in the visible grid, so the number and squares match.
    visible_total = sum(
        int(day.get("contributionCount", 0))
        for week in weeks
        for day in week.get("contributionDays", [])
    )
    total_label = f'{visible_total:,} contributions / last 12 months'
    return "\n        ".join(rects), "\n        ".join(month_labels), total_label


def replace_between(source: str, start_marker: str, end_marker: str, replacement: str) -> str:
    pattern = re.compile(re.escape(start_marker) + r".*?" + re.escape(end_marker), re.DOTALL)
    if not pattern.search(source):
        raise RuntimeError(f"Could not find SVG markers: {start_marker} / {end_marker}")
    return pattern.sub(start_marker + "\n        " + replacement + "\n        " + end_marker, source, count=1)


def main() -> None:
    calendar = graphql_contributions()
    grid, month_labels, total_label = render_grid(calendar)
    svg = SVG_PATH.read_text(encoding="utf-8")
    svg = replace_between(svg, "<!-- CONTRIBUTION_GRID_START -->", "<!-- CONTRIBUTION_GRID_END -->", grid)
    svg = replace_between(svg, "<!-- MONTH_LABELS_START -->", "<!-- MONTH_LABELS_END -->", month_labels)
    svg = re.sub(
        r'(<text id="contribution-total"[^>]*>).*?(</text>)',
        lambda match: match.group(1) + total_label + match.group(2),
        svg,
        count=1,
        flags=re.DOTALL,
    )
    SVG_PATH.write_text(svg, encoding="utf-8")
    print(f"Updated {SVG_PATH.name} for @{USERNAME}: {total_label}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # let Actions report a clear failure
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
