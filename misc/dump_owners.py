#!/usr/bin/env python3
import os, json
from espn_api.football import League

LEAGUE_ID = 1154146516
SEASON_YEAR = 2025

league = League(
    league_id=LEAGUE_ID,
    year=SEASON_YEAR,
    espn_s2=os.environ["ESPN_S2"],
    swid=os.environ["ESPN_SWID"],
)

rows = []
owner_id_to_name = {}
team_id_to_owner_ids = {}

for t in league.teams:
    owners = getattr(t, "owners", []) or []
    owner_ids = []

    for o in owners:
        # On your league, each owner is a dict
        oid = None
        dname = None
        fname = None
        lname = None

        if isinstance(o, dict):
            oid = o.get("id")
            dname = o.get("displayName")
            fname = o.get("firstName")
            lname = o.get("lastName")

        if oid:
            owner_ids.append(oid)
            pretty = " ".join(x for x in [fname, lname] if x) or dname or oid
            owner_id_to_name[oid] = pretty

        rows.append({
            "team_id": t.team_id,
            "team_name": t.team_name,
            "owner_id": oid,
            "owner_displayName": dname,
            "owner_firstName": fname,
            "owner_lastName": lname,
        })

    team_id_to_owner_ids[str(t.team_id)] = owner_ids

print("\n=== TEAM -> OWNER_IDs ===")
for tid, oids in team_id_to_owner_ids.items():
    print(tid, "=>", oids)

out = {
    "team_id_to_owner_ids": team_id_to_owner_ids,
    "owner_id_to_name": owner_id_to_name,
    "rows": rows,
}

with open("owner_map.json", "w") as f:
    json.dump(out, f, indent=2)

print("\nWrote owner_map.json")