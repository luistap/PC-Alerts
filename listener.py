# activity_listener_server.py
#
# ESPN league activity "listener" (poller) + Pushcut sender.
# Now tuned for: ADDS ONLY (so you can test easily).
#
# What it does:
# - Polls league.recent_activity() every N seconds
# - Detects NEW events (deduped with a local file)
# - For ADD events:
#     - Prints to console
#     - Sends Pushcut payload with JSON "input" dict containing text + image URLs
#
# Requirements:
#   python3 -m pip install fastapi uvicorn espn_api requests
#
# Env vars:
#   export ESPN_S2='...'
#   export ESPN_SWID='{...}'
#   export PUSHCUT_URL='https://api.pushcut.io/<secret>/execute?shortcut=Send%20Fantasy%20Alert'
#     (recommended if you run Pushcut Automation Server)
#
# If you're still using /notifications/<name> that also works, but then your
# iMessage sending depends on your notification automation setup.
#
# Run:
#   python3 activity_listener_server.py
#
# FastAPI:
#   http://127.0.0.1:8000/health
#   http://127.0.0.1:8000/last


# other imports

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import asyncio
import json
import os
import time
import requests
import hashlib
import shutil
import image_constructors.add_or_drop as ad_handler

# ESPN API import
from espn_api.football import League

# FastAPI imports

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import FileResponse

LATEST_CARD_PATH = "out_add.png"   # whatever your renderer writes
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL")  # https://xxxx.trycloudflare.com

LEAGUE_ID = 1154146516
SEASON_YEAR = 2025

POLL_SECONDS = 10
SEEN_STATE_FILE = "seen_activity.json"

# === We only do ADDS    for now (as requested) ===
ONLY_ACTIONS: set[str] = {"ADDED", "DROPPED"}  # change later to {"ADDED","DROPPED","TRADED"} etc

# Pushcut URL
# Recommended (Automation Server execute endpoint):
#   https://api.pushcut.io/<secret>/execute?shortcut=Send%20Fantasy%20Alert
# Alternate (notification trigger):
#   https://api.pushcut.io/<secret>/notifications/Fantasy
PUSHCUT_URL: Optional[str] = os.environ.get("PUSHCUT_URL")
PUSHCUT_TITLE_DEFAULT = "Fantasy"
PUSHCUT_TEXT_DEFAULT = "New ESPN activity"

owner_id_to_name = {}

app = FastAPI()

league: Optional[League] = None
last_poll_ts: Optional[float] = None

seen_keys: set[str] = set()
last_new_events: List[Dict[str, Any]] = []

CARDS_DIR = "cards"
os.makedirs(CARDS_DIR, exist_ok=True)

@app.get("/cards/{card_id}.png")
def card_by_id(card_id: str):
    path = os.path.join(CARDS_DIR, f"{card_id}.png")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Missing card")
    return FileResponse(path, media_type="image/png")


@app.get("/cards/latest.png")
def latest_card():
    if not os.path.exists(LATEST_CARD_PATH):
        raise HTTPException(status_code=404, detail=f"Missing {LATEST_CARD_PATH}")
    return FileResponse(LATEST_CARD_PATH, media_type="image/png")


# ------------------- persistence -------------------

def load_seen_state() -> None:
    global seen_keys
    if not os.path.exists(SEEN_STATE_FILE):
        return
    try:
        with open(SEEN_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        keys = data.get("seen_keys", [])
        if isinstance(keys, list):
            seen_keys = set(str(k) for k in keys)
    except Exception:
        pass


def save_seen_state() -> None:
    try:
        with open(SEEN_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"seen_keys": sorted(seen_keys), "updated_at": time.time()},
                f,
                indent=2,
            )
    except Exception:
        pass


# ------------------- ESPN setup -------------------

def init_league() -> League:
    espn_s2 = os.environ.get("ESPN_S2")
    swid = os.environ.get("ESPN_SWID")

    if not espn_s2 or not swid:
        raise RuntimeError(
            "Missing ESPN_S2 / ESPN_SWID env vars. recent_activity() needs cookies."
        )

    return League(
        league_id=LEAGUE_ID,
        year=SEASON_YEAR,
        espn_s2=espn_s2,
        swid=swid,
    )


def normalize_action(action_raw: Any) -> str:
    a = str(action_raw).strip().upper()
    if "ADDED" in a:
        return "ADDED"
    if "DROPPED" in a:
        return "DROPPED"
    if "TRADED" in a:
        return "TRADED"
    return a


def get_team_name(team_obj: Any) -> str:
    name = getattr(team_obj, "team_name", None)
    if name:
        return str(name)
    # fallback
    return f"Team {getattr(team_obj, 'team_id', '?')}"


def get_team_logo_url(team_obj: Any) -> Optional[str]:
    # espn_api Team objects vary a bit by sport/version, so try common fields
    for attr in ("logo_url", "logoUrl", "logo", "team_logo", "teamLogo"):
        val = getattr(team_obj, attr, None)
        if isinstance(val, str) and val.startswith("http"):
            return val
    # sometimes stored in a dict-ish attribute
    for attr in ("data", "raw", "_team", "_raw"):
        d = getattr(team_obj, attr, None)
        if isinstance(d, dict):
            for key in ("logo", "logoUrl", "logo_url"):
                v = d.get(key)
                if isinstance(v, str) and v.startswith("http"):
                    return v
    return None


def get_player_name_and_id(player_raw: Any) -> Tuple[str, Optional[int]]:
    # Name
    name = getattr(player_raw, "name", None) or getattr(player_raw, "fullName", None)
    if not name:
        name = str(player_raw)

    # ID
    pid: Optional[int] = None
    for attr in ("playerId", "player_id", "id"):
        v = getattr(player_raw, attr, None)
        if isinstance(v, int):
            pid = v
            break
        # sometimes string id
        if isinstance(v, str) and v.isdigit():
            pid = int(v)
            break

    return str(name), pid


def get_owner_id(team_obj: Any) -> Optional[str]:
    owners = getattr(team_obj, "owners", None)
    if isinstance(owners, list) and owners:
        oid = owners[0].get("id") if isinstance(owners[0], dict) else None
        if isinstance(oid, str):
            return oid
    return None


def player_headshot_url(player_id: Optional[int]) -> Optional[str]:
    # ESPN headshot pattern (works for many NFL players).
    # If player_id is missing (or negative like DST), return None.
    if not player_id or player_id <= 0:
        return None
    return f"https://a.espncdn.com/i/headshots/nfl/players/full/{player_id}.png"


def normalize_actions(activity: Any) -> List[Dict[str, Any]]:
    """
    Convert activity.actions -> list of normalized action dicts:
      {teamName, action, playerName, playerId, teamLogoUrl, playerImageUrl}
    """
    out: List[Dict[str, Any]] = []

    for entry in getattr(activity, "actions", []) or []:
        if not isinstance(entry, (tuple, list)) or len(entry) < 3:
            continue

        team_obj = entry[0]
        action_raw = entry[1]
        player_raw = entry[2]

        action = normalize_action(action_raw)
        if action not in ONLY_ACTIONS:
            continue

        team_name = get_team_name(team_obj)
        team_logo = get_team_logo_url(team_obj)
        owner_id = get_owner_id(team_obj)


        p_name, p_id = get_player_name_and_id(player_raw)
        p_img = player_headshot_url(p_id)

        out.append(
            {
                "teamName": team_name,
                "ownerId": owner_id,  
                "action": action,
                "playerName": p_name,
                "playerId": p_id,
                "teamLogoUrl": team_logo,
                "playerImageUrl": p_img,
            }
        )

    return out


# ------------------- dedupe + formatting -------------------

def make_event_key(actions: List[Dict[str, Any]]) -> str:
    parts = []
    for a in actions:
        parts.append(f"{a.get('teamName')}|{a.get('action')}|{a.get('playerName')}|{a.get('playerId')}")
    parts.sort()
    return " || ".join(parts)


def format_add_message(actions: List[Dict[str, Any]]) -> str:
    # Because we only handle ADDED right now, keep it simple.
    lines = ["➕ ADD"]
    for a in actions:
        lines.append(f"✅ {a['teamName']} added: {a['playerName']}")
    return "\n".join(lines)


# ------------------- Pushcut -------------------

def send_to_pushcut(input_obj: Dict[str, Any]) -> None:
    """
    Send Pushcut request with JSON 'input' as a DICTIONARY.
    In your Shortcut: use "Get Dictionary from Input".
    """
    if not PUSHCUT_URL:
        print("[pushcut] PUSHCUT_URL not set; skipping pushcut send.")
        return

    try:
        resp = requests.post(
            PUSHCUT_URL,
            json={
                "title": PUSHCUT_TITLE_DEFAULT,
                "text": PUSHCUT_TEXT_DEFAULT,
                "input": input_obj,  # <-- Pushcut supports JSON object input
            },
            timeout=10,
        )
        if resp.status_code >= 400:
            print(f"[pushcut] HTTP {resp.status_code}: {resp.text[:250]}")
    except Exception as e:
        print(f"[pushcut] error: {repr(e)}")


# ------------------- polling -------------------

def poll_once(size: int = 50) -> List[Dict[str, Any]]:
    assert league is not None

    activities = league.recent_activity(size=size)
    new_items: List[Dict[str, Any]] = []

    for activity in activities:
        actions = normalize_actions(activity)
        if not actions:
            continue

        # ✅ split into individual transactions
        for a in actions:
            key = make_event_key([a])  # key per action, not per whole activity
            if key in seen_keys:
                continue

            seen_keys.add(key)
            new_items.append({"key": key, "actions": [a]})

    if new_items:
        save_seen_state()

    return new_items


@app.on_event("startup")
async def on_startup() -> None:
    global league, last_poll_ts, last_new_events

    load_seen_state()
    league = init_league()

    # Warm-up so we don't blast old history
    _ = poll_once(size=50)
    last_new_events = []
    last_poll_ts = time.time()

    async def loop() -> None:
        global last_poll_ts, last_new_events
        while True:
            try:
                last_poll_ts = time.time()
                new_items = poll_once(size=50)
                if new_items:
                    last_new_events = new_items

                for item in new_items:
                    msg = format_add_message(item["actions"])
                    print("\n" + msg + "\n")

                    transaction = item["actions"][0]

                    # ✅ unique id per transaction (stable, short)
                    card_id = hashlib.sha1(item["key"].encode("utf-8")).hexdigest()[:12]
                    card_path = os.path.join(CARDS_DIR, f"{card_id}.png")

                    # ✅ freeze whatever your renderer wrote (out_add.png) into a unique file
                    if os.path.exists(LATEST_CARD_PATH):
                        shutil.copyfile(LATEST_CARD_PATH, card_path)

                    # what transaction type do we have?
                    img = None
                    if transaction.get("action") == "ADD" or transaction.get("action") == "DROP":
                        img = ad_handler.construct_image_adds_or_drops(transaction.get("playerName"), transaction.get("playerImageUrl"),
                                    owner_id_to_name[transaction.get("ownerId")], transaction.get("action"))

                    img_url = None
                    if PUBLIC_BASE_URL:
                        img_url = f"{PUBLIC_BASE_URL}/cards/{card_id}.png?ts={int(time.time())}"

                        input_obj = {
                            "type": "ADD",
                            "text": msg,
                            "teamName": transaction.get("teamName"),
                            "teamLogoUrl": transaction.get("teamLogoUrl"),
                            "playerName": transaction.get("playerName"),
                            "playerId": transaction.get("playerId"),
                            "playerImageUrl": transaction.get("playerImageUrl"),
                            "imageUrl": img_url,  
                            "rawActions": item["actions"],
                        }

                        send_to_pushcut(input_obj)

            except Exception as e:
                print(f"[poll error] {repr(e)}")

            await asyncio.sleep(POLL_SECONDS)

    asyncio.create_task(loop())

# server start
if __name__ == "__main__":
    import uvicorn
    
    # initialize owner_id_to_name dictionary for image construction
    OWNER_MAP_PATH = os.environ.get("OWNER_MAP_PATH", "owner_map.json")
    if os.path.exists(OWNER_MAP_PATH):
        with open(OWNER_MAP_PATH, "r", encoding="utf-8") as f:
            owner_id_to_name = json.load(f).get("owner_id_to_name", {})
    
    # serve the app
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)