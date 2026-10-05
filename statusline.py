#!/usr/bin/env python3
"""Kimi Code custom status line: replicates the default footer and appends
5h / 7d quota bars at the end.

- Normal mode (stdin = JSON snapshot from the TUI): prints one status line.
  Quota and git status are read from local caches; stale entries trigger a
  background refresh so the 300ms script budget is never blown.
- --refresh [--cwd PATH]: updates the caches. Quota comes from
  https://api.kimi.com/coding/v1/usages with the OAuth token from
  ~/.kimi-code/credentials/kimi-code.json; git status is collected with the
  exact same git commands the TUI uses for its built-in badge.

Colors replicate the built-in footer: the TUI wraps the custom line in its
`text` color but inner ANSI escapes pass through, so we emit palette colors
ourselves (dark/light palette chosen from tui.toml's `theme`).

Slots the stdin snapshot does not provide (goal badge, background-task
badges, swarm/tower mode, thinking-effort suffix, right-side rotating tips)
cannot be replicated and are omitted.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

KIMI_HOME = os.environ.get("KIMI_CODE_HOME", os.path.expanduser("~/.kimi-code"))
CRED_FILE = os.path.join(KIMI_HOME, "credentials", "kimi-code.json")
CACHE_FILE = os.path.join(KIMI_HOME, "cache", "statusline-quota.json")
GIT_CACHE_FILE = os.path.join(KIMI_HOME, "cache", "statusline-git.json")
DEBUG_STDIN = os.path.join(KIMI_HOME, "cache", "statusline-stdin.json")
TUI_FILE = os.path.join(KIMI_HOME, "tui.toml")
USAGES_URL = "https://api.kimi.com/coding/v1/usages"

CACHE_TTL = 180        # seconds before a quota background refresh is triggered
ERR_CACHE_TTL = 60     # shorter TTL after a failed quota fetch
GIT_TTL = 15           # seconds before git status is refreshed (TUI uses 15s)
FETCH_TIMEOUT = 8      # network timeout, only used by the background refresher
GIT_TIMEOUT = 0.5      # per git command, same as the TUI's 500ms
BAR_WIDTH = 10
BAR_GLYPH = "▬"        # vertically centered rectangle; "▄" hugs the baseline, "■" is square

# Built-in palettes (from the official themes documentation)
PALETTES = {
    "dark": {
        "text": "#E0E0E0", "textDim": "#888888", "textMuted": "#6B6B6B",
        "primary": "#4FA8FF", "accent": "#5BC0BE", "warning": "#E8A838",
        "success": "#4EC87E", "error": "#E85454",
    },
    "light": {
        "text": "#1A1A1A", "textDim": "#454545", "textMuted": "#5F5F5F",
        "primary": "#1565C0", "accent": "#00838F", "warning": "#92660A",
        "success": "#0E7A38", "error": "#B91C1C",
    },
}


def current_palette():
    try:
        with open(TUI_FILE) as f:
            for line in f:
                s = line.strip()
                if s.startswith("theme") and "=" in s:
                    theme = s.split("=", 1)[1].split("#")[0].strip().strip('"')
                    if theme in PALETTES:
                        return PALETTES[theme]
                    break
    except Exception:
        pass
    return PALETTES["dark"]  # auto/custom -> dark base, like the TUI fallback


def paint(text, hex_color, bold=False):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    out = f"\x1b[38;2;{r};{g};{b}m"
    if bold:
        out += "\x1b[1m"
    end = "\x1b[22m\x1b[39m" if bold else "\x1b[39m"
    return f"{out}{text}{end}"


# ---------------------------------------------------------------- quota ----

def load_token():
    with open(CRED_FILE) as f:
        return json.load(f)["access_token"]


def refresh_quota():
    now = time.time()
    try:
        req = urllib.request.Request(
            USAGES_URL,
            headers={"Authorization": "Bearer " + load_token(),
                     "User-Agent": "kimi-code-statusline"},
        )
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            data = json.load(r)
        u5 = data.get("usages", {}).get("limit_5h", {})
        u7 = data.get("usages", {}).get("limit_7d", {})
        out = {
            "fetched_at": now,
            "ok": True,
            "pct_5h": round(float(u5.get("used_ratio", 0)) * 100),
            "reset_5h": u5.get("reset_time"),
            "pct_7d": round(float(u7.get("used_ratio", 0)) * 100),
            "reset_7d": u7.get("reset_time"),
        }
    except urllib.error.HTTPError as e:
        out = {"fetched_at": now, "ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        out = {"fetched_at": now, "ok": False, "error": str(e)[:80]}
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f)
    os.replace(tmp, CACHE_FILE)


def read_quota_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def quota_fresh(cache):
    if not cache:
        return False
    ttl = CACHE_TTL if cache.get("ok") else ERR_CACHE_TTL
    return time.time() - cache["fetched_at"] <= ttl


def fmt_reset(iso, with_date=False):
    if not iso:
        return "?"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%m-%d %H:%M") if with_date else dt.strftime("%H:%M")
    except Exception:
        return "?"


def usage_color(pct, pal):
    if pct >= 80:
        return pal["error"]
    if pct >= 50:
        return pal["warning"]
    return pal["success"]


def low_bar(pct, pal, width=BAR_WIDTH):
    """Low-profile bar: filled cells in the usage color, empty cells as a dim track."""
    filled = max(0, min(width, round(pct * width / 100)))
    return (paint(BAR_GLYPH * filled, usage_color(pct, pal))
            + paint(BAR_GLYPH * (width - filled), pal["textMuted"]))


def quota_segment(pal, cache):
    dim = lambda s: paint(s, pal["textDim"])
    if not cache:
        return dim(f"5h {BAR_GLYPH * BAR_WIDTH} ?%  7d {BAR_GLYPH * BAR_WIDTH} ?%")
    if not cache.get("ok"):
        if cache.get("error") == "HTTP 401":
            return paint("quota: login expired", pal["warning"])
        return dim("quota: unavailable")
    segs = []
    for label, pct, reset, with_date in (
        ("5h", cache["pct_5h"], cache["reset_5h"], False),
        ("7d", cache["pct_7d"], cache["reset_7d"], True),
    ):
        segs.append(
            dim(label) + " "
            + low_bar(pct, pal)
            + paint(f" {pct}%", usage_color(pct, pal))
            + dim(f" {fmt_reset(reset, with_date)}")
        )
    return "  ".join(segs)


# ------------------------------------------------------------------ git ----

AHEAD_BEHIND_RE = re.compile(r"\[(?:ahead (\d+))?(?:, )?(?:behind (\d+))?\]")


def run_git(cwd, args):
    r = subprocess.run(
        ["git", "-C", cwd] + args,
        capture_output=True, timeout=GIT_TIMEOUT,
    )
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace")


def collect_git(cwd):
    """Same commands as the TUI's built-in git badge."""
    try:
        branch = run_git(cwd, ["branch", "--show-current"])
        if branch is None:
            return None
        branch = branch.strip()
        if not branch:
            return None
        dirty = False
        ahead = behind = added = deleted = 0
        out = run_git(cwd, ["status", "--porcelain"])
        if out is not None:
            for line in out.split("\n"):
                if line.startswith("## "):
                    m = AHEAD_BEHIND_RE.search(line)
                    if m:
                        ahead = int(m.group(1) or 0)
                        behind = int(m.group(2) or 0)
                elif line.strip():
                    dirty = True
        if dirty:
            out = run_git(cwd, ["diff", "--numstat", "HEAD"])
            if out:
                for line in out.split("\n"):
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        if parts[0].isdigit():
                            added += int(parts[0])
                        if parts[1].isdigit():
                            deleted += int(parts[1])
        return {"branch": branch, "dirty": dirty, "ahead": ahead,
                "behind": behind, "added": added, "deleted": deleted,
                "fetched_at": time.time()}
    except Exception:
        return None


def read_git_cache():
    try:
        with open(GIT_CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def refresh_git(cwd):
    st = collect_git(cwd)
    cache = read_git_cache()
    if st is None:
        st = {"branch": None, "fetched_at": time.time()}
    cache[cwd] = st
    # prune entries not touched for a day
    cutoff = time.time() - 86400
    cache = {k: v for k, v in cache.items()
             if v.get("fetched_at", 0) > cutoff}
    os.makedirs(os.path.dirname(GIT_CACHE_FILE), exist_ok=True)
    tmp = GIT_CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f)
    os.replace(tmp, GIT_CACHE_FILE)


def git_fresh(entry):
    return bool(entry) and time.time() - entry.get("fetched_at", 0) <= GIT_TTL


def git_badge(st, pal):
    """Replicates the TUI's formatGitBadgeBase: branch [+A -D ↑N ↓M], ± when dirty."""
    parts = []
    diff = []
    if st.get("added", 0) > 0:
        diff.append(f"+{st['added']}")
    if st.get("deleted", 0) > 0:
        diff.append(f"-{st['deleted']}")
    if diff:
        parts.append(" ".join(diff))
    elif st.get("dirty"):
        parts.append("±")
    sync = ""
    if st.get("ahead", 0) > 0:
        sync += f"↑{st['ahead']}"
    if st.get("behind", 0) > 0:
        sync += f"↓{st['behind']}"
    if sync:
        parts.append(sync)
    branch = st["branch"]
    text = branch if not parts else f"{branch} [{' '.join(parts)}]"
    return paint(text, pal["textDim"])


# ----------------------------------------------------------------- main ----

MODE_LABELS = {
    "yolo": "Ask When Needed",
    "auto": "Never Ask",
    # "manual" shows no badge in the default footer
}


def shorten_cwd(cwd):
    """Mirror the TUI's shortenCwd: ~ for home, keep the last three segments."""
    home = os.path.expanduser("~")
    work = cwd
    if home and cwd == home:
        return "~"
    if home and cwd.startswith(home + "/"):
        work = "~" + cwd[len(home):]
    segs = [s for s in work.split("/") if s]
    if len(segs) <= 3:
        return work
    return "…/" + "/".join(segs[-3:])


def spawn_refresh(cwd):
    args = [sys.executable, os.path.abspath(__file__), "--refresh"]
    if cwd:
        args += ["--cwd", cwd]
    subprocess.Popen(
        args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )


def main():
    raw = sys.stdin.read()
    try:
        snap = json.loads(raw) if raw.strip() else {}
        with open(DEBUG_STDIN, "w") as f:  # keep one sample for key discovery
            json.dump(snap, f, indent=2, ensure_ascii=False)
    except Exception:
        snap = {}

    pal = current_palette()
    cwd = snap.get("cwd")
    quota_cache = read_quota_cache()
    git_entry = read_git_cache().get(str(cwd)) if cwd else None

    if not quota_fresh(quota_cache) or (cwd and not git_fresh(git_entry)):
        # fire-and-forget background refresh; display current caches this round
        spawn_refresh(str(cwd) if cwd else None)

    parts = []

    # mode slot (default footer shows badges only for yolo/auto/plan)
    mode = snap.get("permissionMode")
    if mode in MODE_LABELS:
        parts.append(paint(MODE_LABELS[mode], pal["warning"], bold=True))
    if snap.get("planMode"):
        parts.append(paint("plan", pal["primary"], bold=True))

    # model slot
    if snap.get("model"):
        parts.append(paint(str(snap["model"]), pal["text"]))

    # cwd slot
    if cwd:
        parts.append(paint(shorten_cwd(str(cwd)), pal["textDim"]))

    # git slot: cached full status (branch + diff stats), like the TUI badge
    if git_entry and git_entry.get("branch"):
        parts.append(git_badge(git_entry, pal))
    elif snap.get("gitBranch"):
        parts.append(paint(str(snap["gitBranch"]), pal["textDim"]))

    # quota bars, appended at the end
    parts.append(quota_segment(pal, quota_cache))

    print("  ".join(parts))


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        quota_cache = read_quota_cache()
        if not quota_fresh(quota_cache):
            refresh_quota()
        if "--cwd" in sys.argv:
            refresh_git(sys.argv[sys.argv.index("--cwd") + 1])
    else:
        main()
