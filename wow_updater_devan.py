"""
╔══════════════════════════════════════════════════════════════╗
║         VOIDPULSE — WoW Midnight Updater v3                  ║
║  Sections:                                                    ║
║    • Profession market — top 10 profitable Midnight crafts   ║
║    • M+ tier list — all specs S-D (Archon.gg)               ║
║    • Raid tier list — all specs S-D (Archon.gg)             ║
║    • Trinkets — top 4 per spec (Archon.gg)                  ║
║    • Gear & Stats — BiS, stat priority, enchants (Wowhead)  ║
╚══════════════════════════════════════════════════════════════╝

SETUP:
  1. pip install -r requirements.txt
  2. Copy .env.example to .env and add BLIZZARD_CLIENT_ID / BLIZZARD_CLIENT_SECRET
     (.env is gitignored — never commit it.) Or set the same names in the environment / GitHub Secrets.
  3. python wow_updater_devan.py          # loop every VOIDPULSE_REFRESH_SEC (default 30m)
     python wow_updater_devan.py --once   # single run (GitHub Actions)
"""

import argparse
import sys
import requests, time, re, os, json
import shutil
from datetime import datetime, timezone

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ══════════════════════════════════════════
#  YOUR SETTINGS (override with env for CI / safety)
# ══════════════════════════════════════════
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _bootstrap_env_from_dotfile():
    """Read .env without relying only on python-dotenv (BOM / UTF-16 / empty OS vars)."""
    path = os.path.join(SCRIPT_DIR, ".env")
    if not os.path.isfile(path):
        return
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            with open(path, "r", encoding=encoding) as f:
                lines = f.readlines()
            break
        except UnicodeError:
            continue
        except OSError:
            return
    else:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and val:
            os.environ[key] = val


try:
    from dotenv import load_dotenv

    _dotenv_path = os.path.join(SCRIPT_DIR, ".env")
    load_dotenv(_dotenv_path, override=True, encoding="utf-8")
except ImportError:
    pass
except Exception:
    pass

_bootstrap_env_from_dotfile()


def _env(name, default=""):
    v = os.environ.get(name)
    return v.strip() if v else default


BNET_CLIENT_ID     = _env("BNET_CLIENT_ID") or _env("BLIZZARD_CLIENT_ID")
BNET_CLIENT_SECRET = _env("BNET_CLIENT_SECRET") or _env("BLIZZARD_CLIENT_SECRET")

REGION           = _env("VOIDPULSE_REGION", "us")
REALM_ID         = int(_env("VOIDPULSE_REALM_ID", "3675"))
REFRESH_INTERVAL = int(_env("VOIDPULSE_REFRESH_SEC", "1800"))

HTML_FILE    = _env("VOIDPULSE_HTML", os.path.join(SCRIPT_DIR, "wow-midnight-hub.html"))
SNAPSHOT_FILE = _env("VOIDPULSE_SNAPSHOT", os.path.join(SCRIPT_DIR, "voidpulse_snapshot.json"))
# ══════════════════════════════════════════

SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Profession → item class/subclass mappings
PROF_ITEM_CLASSES = {
    "Blacksmithing": [(4, None)],
    "Leatherworking": [(4, 2), (4, 3)],
    "Tailoring": [(4, 1)],
    "Jewelcrafting": [(3, None)],
    "Alchemy": [(0, None)],
    "Enchanting": [(16, None)],
    "Engineering": [(15, None)],
    "Inscription": [(0, 3)],
    "Mining": [(7, None)],
    "Herbalism": [(7, 9)],
    "Skinning": [(7, 8)],
}

# Approximate mat costs per profession for profit estimation (in gold)
# These are rough multipliers — the script will use AH prices where possible
MAT_COST_ESTIMATE = {
    "Blacksmithing": 0.45, "Leatherworking": 0.40, "Tailoring": 0.35,
    "Jewelcrafting": 0.50, "Alchemy": 0.30, "Enchanting": 0.45,
    "Engineering": 0.55, "Inscription": 0.25, "Mining": 0.10,
    "Herbalism": 0.10, "Skinning": 0.10,
}

SPEC_ROLES = {
    "Blood Death Knight":"Tank","Frost Death Knight":"DPS","Unholy Death Knight":"DPS",
    "Havoc Demon Hunter":"DPS","Vengeance Demon Hunter":"Tank",
    "Balance Druid":"DPS","Feral Druid":"DPS","Guardian Druid":"Tank","Restoration Druid":"Healer",
    "Devastation Evoker":"DPS","Augmentation Evoker":"DPS","Preservation Evoker":"Healer",
    "Beast Mastery Hunter":"DPS","Marksmanship Hunter":"DPS","Survival Hunter":"DPS",
    "Arcane Mage":"DPS","Fire Mage":"DPS","Frost Mage":"DPS",
    "Brewmaster Monk":"Tank","Mistweaver Monk":"Healer","Windwalker Monk":"DPS",
    "Holy Paladin":"Healer","Protection Paladin":"Tank","Retribution Paladin":"DPS",
    "Discipline Priest":"Healer","Holy Priest":"Healer","Shadow Priest":"DPS",
    "Assassination Rogue":"DPS","Outlaw Rogue":"DPS","Subtlety Rogue":"DPS",
    "Elemental Shaman":"DPS","Enhancement Shaman":"DPS","Restoration Shaman":"Healer",
    "Affliction Warlock":"DPS","Demonology Warlock":"DPS","Destruction Warlock":"DPS",
    "Arms Warrior":"DPS","Fury Warrior":"DPS","Protection Warrior":"Tank",
}

CLASS_SPECS = {
    "Death Knight":  ["Blood Death Knight","Frost Death Knight","Unholy Death Knight"],
    "Demon Hunter":  ["Havoc Demon Hunter","Vengeance Demon Hunter"],
    "Druid":         ["Balance Druid","Feral Druid","Guardian Druid","Restoration Druid"],
    "Evoker":        ["Devastation Evoker","Augmentation Evoker","Preservation Evoker"],
    "Hunter":        ["Beast Mastery Hunter","Marksmanship Hunter","Survival Hunter"],
    "Mage":          ["Arcane Mage","Fire Mage","Frost Mage"],
    "Monk":          ["Brewmaster Monk","Mistweaver Monk","Windwalker Monk"],
    "Paladin":       ["Holy Paladin","Protection Paladin","Retribution Paladin"],
    "Priest":        ["Discipline Priest","Holy Priest","Shadow Priest"],
    "Rogue":         ["Assassination Rogue","Outlaw Rogue","Subtlety Rogue"],
    "Shaman":        ["Elemental Shaman","Enhancement Shaman","Restoration Shaman"],
    "Warlock":       ["Affliction Warlock","Demonology Warlock","Destruction Warlock"],
    "Warrior":       ["Arms Warrior","Fury Warrior","Protection Warrior"],
}

ARCHON_SLUGS = {
    "Blood Death Knight":    ("blood","death-knight"),
    "Frost Death Knight":    ("frost","death-knight"),
    "Unholy Death Knight":   ("unholy","death-knight"),
    "Havoc Demon Hunter":    ("havoc","demon-hunter"),
    "Vengeance Demon Hunter":("vengeance","demon-hunter"),
    "Balance Druid":         ("balance","druid"),
    "Feral Druid":           ("feral","druid"),
    "Guardian Druid":        ("guardian","druid"),
    "Restoration Druid":     ("restoration","druid"),
    "Devastation Evoker":    ("devastation","evoker"),
    "Augmentation Evoker":   ("augmentation","evoker"),
    "Preservation Evoker":   ("preservation","evoker"),
    "Beast Mastery Hunter":  ("beast-mastery","hunter"),
    "Marksmanship Hunter":   ("marksmanship","hunter"),
    "Survival Hunter":       ("survival","hunter"),
    "Arcane Mage":           ("arcane","mage"),
    "Fire Mage":             ("fire","mage"),
    "Frost Mage":            ("frost","mage"),
    "Brewmaster Monk":       ("brewmaster","monk"),
    "Mistweaver Monk":       ("mistweaver","monk"),
    "Windwalker Monk":       ("windwalker","monk"),
    "Holy Paladin":          ("holy","paladin"),
    "Protection Paladin":    ("protection","paladin"),
    "Retribution Paladin":   ("retribution","paladin"),
    "Discipline Priest":     ("discipline","priest"),
    "Holy Priest":           ("holy","priest"),
    "Shadow Priest":         ("shadow","priest"),
    "Assassination Rogue":   ("assassination","rogue"),
    "Outlaw Rogue":          ("outlaw","rogue"),
    "Subtlety Rogue":        ("subtlety","rogue"),
    "Elemental Shaman":      ("elemental","shaman"),
    "Enhancement Shaman":    ("enhancement","shaman"),
    "Restoration Shaman":    ("restoration","shaman"),
    "Affliction Warlock":    ("affliction","warlock"),
    "Demonology Warlock":    ("demonology","warlock"),
    "Destruction Warlock":   ("destruction","warlock"),
    "Arms Warrior":          ("arms","warrior"),
    "Fury Warrior":          ("fury","warrior"),
    "Protection Warrior":    ("protection","warrior"),
}


# ══════════════════════════════════════════════════════════════
#  BLIZZARD AUTH
# ══════════════════════════════════════════════════════════════
_bnet = {"token": None, "exp": 0}

def bnet_token():
    if not BNET_CLIENT_ID or not BNET_CLIENT_SECRET:
        raise RuntimeError(
            "Missing API client id/secret. Set BNET_CLIENT_ID and BNET_CLIENT_SECRET, "
            "or BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET (see repo Actions secrets)."
        )
    if _bnet["token"] and time.time() < _bnet["exp"]:
        return _bnet["token"]
    print("  → Blizzard auth...")
    r = requests.post("https://oauth.battle.net/token",
        auth=(BNET_CLIENT_ID, BNET_CLIENT_SECRET),
        data={"grant_type": "client_credentials"}, timeout=10)
    r.raise_for_status()
    d = r.json()
    _bnet["token"] = d["access_token"]
    _bnet["exp"]   = time.time() + d.get("expires_in", 86400) - 60
    return _bnet["token"]


# ══════════════════════════════════════════════════════════════
#  AUCTION HOUSE + PROFESSION MARKET
# ══════════════════════════════════════════════════════════════
def fetch_auctions():
    print("  → Fetching AH data...")
    token = bnet_token()
    r = requests.get(
        f"https://{REGION}.api.blizzard.com/data/wow/connected-realm/{REALM_ID}/auctions",
        headers={"Authorization": f"Bearer {token}"},
        params={"namespace": f"dynamic-{REGION}", "locale": "en_US"},
        timeout=30)
    r.raise_for_status()
    auctions = r.json().get("auctions", [])
    print(f"  ✓ {len(auctions):,} listings")
    return auctions


def get_item_info_full(item_id):
    try:
        token = bnet_token()
        r = requests.get(
            f"https://{REGION}.api.blizzard.com/data/wow/item/{item_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"namespace": f"static-{REGION}", "locale": "en_US"},
            timeout=8)
        d = r.json()
        return {
            "name":          d.get("name", f"Item #{item_id}"),
            "item_class":    d.get("item_class", {}).get("id"),
            "item_subclass": d.get("item_subclass", {}).get("id"),
            "item_level":    d.get("level", 0),
        }
    except Exception:
        return {"name": f"Item #{item_id}", "item_class": None, "item_subclass": None, "item_level": 0}


def build_price_map(auctions):
    data = {}
    for a in auctions:
        iid   = a["item"]["id"]
        price = a.get("unit_price", a.get("buyout", 0))
        qty   = a.get("quantity", 1)
        if price > 0:
            if iid not in data:
                data[iid] = {"prices": [], "count": 0}
            data[iid]["prices"].append(price / 10000)
            data[iid]["count"] += qty
    return data


def get_trend(prices):
    low = prices[0]
    med = prices[len(prices) // 2]
    if low < med * 0.80:
        return "chg-up", f"▲ {int((med/low-1)*100)}% below median"
    elif low > med * 1.20:
        return "chg-dn", f"▼ {int((low/med-1)*100)}% above median"
    else:
        return "chg-st", "— Stable"


def build_profession_data(auctions):
    print("  → Building profession market data...")
    price_map = build_price_map(auctions)
    sorted_items = sorted(price_map.items(), key=lambda x: x[1]["count"], reverse=True)

    prof_buckets = {p: [] for p in PROF_ITEM_CLASSES}
    item_cache = {}
    looked_up  = 0

    for iid, mdata in sorted_items[:600]:
        if looked_up >= 400:
            break
        if iid not in item_cache:
            item_cache[iid] = get_item_info_full(iid)
            looked_up += 1
            time.sleep(0.04)
        info = item_cache[iid]
        ic   = info["item_class"]
        isc  = info["item_subclass"]
        ilvl = info.get("item_level", 0)

        is_trade_good = (ic == 7)
        is_consumable = (ic == 0)
        if not is_trade_good and not is_consumable and ilvl < 100:
            continue

        for prof, class_filters in PROF_ITEM_CLASSES.items():
            for (req_class, req_sub) in class_filters:
                if ic == req_class and (req_sub is None or isc == req_sub):
                    prof_buckets[prof].append((iid, mdata, info))
                    break

    result = {}
    for prof, items in prof_buckets.items():
        mat_ratio = MAT_COST_ESTIMATE.get(prof, 0.40)
        top10 = sorted(items, key=lambda x: x[1]["count"], reverse=True)[:10]
        rows  = []
        for iid, mdata, info in top10:
            prices     = sorted(mdata["prices"])
            sell_price = prices[0]
            mat_cost   = sell_price * mat_ratio
            profit     = sell_price - mat_cost
            css, txt   = get_trend(prices)
            rows.append({
                "name":        info["name"],
                "sub":         f"ilvl {info['item_level']}" if info.get("item_level", 0) > 0 else "",
                "price":       f"{sell_price:,.0f}g",
                "profit":      f"+{profit:,.0f}g" if profit >= 0 else f"{profit:,.0f}g",
                "profit_pos":  profit >= 0,
                "volume":      f"{mdata['count']:,}",
                "change_class": css,
                "change_text": txt,
            })
        result[prof] = rows if rows else [{
            "name": f"No Midnight {prof} items found yet",
            "sub": "Check back after next refresh",
            "price": "—", "profit": "—", "profit_pos": True,
            "volume": "—", "change_class": "chg-st", "change_text": "—"
        }]

    print(f"  ✓ Prof data · {sum(len(v) for v in result.values())} items")
    return result


# ══════════════════════════════════════════════════════════════
#  ARCHON.GG — M+ AND RAID TIER LISTS
# ══════════════════════════════════════════════════════════════
def scrape_archon_tiers(content_type="mythic-plus"):
    """
    Scrape Archon.gg tier list for all specs.
    content_type: 'mythic-plus' or 'raid'
    """
    label = "M+" if content_type == "mythic-plus" else "Raid"
    print(f"  → Scraping Archon.gg {label} tiers...")
    results = []

    # Archon tier list API endpoint
    if content_type == "mythic-plus":
        url = "https://www.archon.gg/wow/tier-list/dps-rankings/mythic-plus/10/all-dungeons/this-week"
    else:
        url = "https://www.archon.gg/wow/tier-list/dps-rankings/raid/mythic/all-bosses"

    # Try to fetch each role
    for role_path, role_label in [("dps-rankings","DPS"),("tank-rankings","Tank"),("healer-rankings","Healer")]:
        if content_type == "mythic-plus":
            url = f"https://www.archon.gg/wow/tier-list/{role_path}/mythic-plus/10/all-dungeons/this-week"
        else:
            url = f"https://www.archon.gg/wow/tier-list/{role_path}/raid/mythic/all-bosses"
        try:
            r = requests.get(url, headers=SCRAPE_HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            html = r.text

            # Extract spec names and tier scores from Next.js JSON payload
            # Archon embeds __NEXT_DATA__ JSON in every page
            match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
            if not match:
                continue
            page_data = json.loads(match.group(1))

            # Dig into the rankings data
            rankings = []
            try:
                props = page_data["props"]["pageProps"]
                # Try different possible keys
                for key in ["rankings","tierList","specs","data","dpsRankings","tankRankings","healerRankings"]:
                    if key in props:
                        rankings = props[key]
                        break
                if not rankings and "initialData" in props:
                    rankings = props["initialData"].get("rankings", [])
            except Exception:
                pass

            if not rankings:
                # Fallback: regex for spec names + tiers in HTML
                spec_matches = re.findall(r'"specName"\s*:\s*"([^"]+)".*?"tier"\s*:\s*"([SABCD])".*?"className"\s*:\s*"([^"]+)"', html)
                for spec, tier, cls in spec_matches:
                    full = f"{spec} {cls}"
                    role = SPEC_ROLES.get(full, role_label)
                    results.append({"spec":spec,"cls":cls,"role":role,"tier":tier,
                                    "score":"—","change":"— Stable","change_class":"change-stable"})
                continue

            for entry in rankings:
                spec = entry.get("specName", entry.get("spec",""))
                cls  = entry.get("className", entry.get("class",""))
                tier = entry.get("tier", entry.get("grade","B"))
                score = entry.get("score", entry.get("performance","—"))
                if not spec:
                    continue
                full = f"{spec} {cls}"
                role = SPEC_ROLES.get(full, role_label)
                results.append({
                    "spec":  spec, "cls": cls, "role": role,
                    "tier":  str(tier).upper() if tier else "C",
                    "score": f"{score:.1f}" if isinstance(score, float) else str(score),
                    "change": "— Stable", "change_class": "change-stable",
                })
            time.sleep(0.5)
        except Exception as e:
            print(f"    ✗ Archon {label} {role_label}: {e}")

    if results:
        print(f"  ✓ {len(results)} specs ranked for {label}")
        return results, True
    else:
        print(f"  ✗ Archon {label} returned no data — using fallback")
        return fallback_tiers(content_type), False


def fallback_tiers(content_type="mythic-plus"):
    """Fallback tier data when scraping fails."""
    if content_type == "mythic-plus":
        data = [
            ("Augmentation","Evoker","DPS","S","98.4","▲ Buffed","change-buffed"),
            ("Shadow","Priest","DPS","S","96.1","— Stable","change-stable"),
            ("Retribution","Paladin","DPS","A","88.3","▲ Buffed","change-buffed"),
            ("Havoc","Demon Hunter","DPS","A","87.0","— Stable","change-stable"),
            ("Fire","Mage","DPS","A","85.2","▲ Buffed","change-buffed"),
            ("Windwalker","Monk","DPS","A","83.0","— Stable","change-stable"),
            ("Subtlety","Rogue","DPS","A","81.5","— Stable","change-stable"),
            ("Frost","Death Knight","DPS","A","80.0","— Stable","change-stable"),
            ("Devastation","Evoker","DPS","B","78.0","— Stable","change-stable"),
            ("Feral","Druid","DPS","B","74.0","— Stable","change-stable"),
            ("Destruction","Warlock","DPS","B","72.5","▼ Nerfed","change-nerfed"),
            ("Assassination","Rogue","DPS","B","70.0","— Stable","change-stable"),
            ("Fury","Warrior","DPS","B","68.5","— Stable","change-stable"),
            ("Balance","Druid","DPS","C","62.0","▼ Nerfed","change-nerfed"),
            ("Beast Mastery","Hunter","DPS","C","61.3","▼ Nerfed","change-nerfed"),
            ("Unholy","Death Knight","DPS","C","60.0","— Stable","change-stable"),
            ("Arcane","Mage","DPS","C","59.0","— Stable","change-stable"),
            ("Marksmanship","Hunter","DPS","C","58.0","— Stable","change-stable"),
            ("Holy","Paladin","Healer","A","90.0","— Stable","change-stable"),
            ("Restoration","Druid","Healer","A","87.5","— Stable","change-stable"),
            ("Mistweaver","Monk","Healer","B","78.0","— Stable","change-stable"),
            ("Preservation","Evoker","Healer","B","76.0","— Stable","change-stable"),
            ("Discipline","Priest","Healer","B","75.0","▼ Nerfed","change-nerfed"),
            ("Restoration","Shaman","Healer","C","63.0","— Stable","change-stable"),
            ("Holy","Priest","Healer","C","60.0","— Stable","change-stable"),
            ("Blood","Death Knight","Tank","A","91.0","— Stable","change-stable"),
            ("Brewmaster","Monk","Tank","A","86.0","— Stable","change-stable"),
            ("Protection","Paladin","Tank","B","77.0","▲ Buffed","change-buffed"),
            ("Protection","Warrior","Tank","B","74.0","— Stable","change-stable"),
            ("Vengeance","Demon Hunter","Tank","B","72.0","— Stable","change-stable"),
            ("Guardian","Druid","Tank","C","63.0","▼ Nerfed","change-nerfed"),
        ]
    else:
        data = [
            ("Augmentation","Evoker","DPS","S","99.0","▲ Buffed","change-buffed"),
            ("Shadow","Priest","DPS","S","95.0","— Stable","change-stable"),
            ("Fire","Mage","DPS","A","89.0","▲ Buffed","change-buffed"),
            ("Retribution","Paladin","DPS","A","87.0","— Stable","change-stable"),
            ("Unholy","Death Knight","DPS","A","85.0","▲ Buffed","change-buffed"),
            ("Balance","Druid","DPS","A","83.0","— Stable","change-stable"),
            ("Devastation","Evoker","DPS","B","79.0","— Stable","change-stable"),
            ("Affliction","Warlock","DPS","B","77.0","— Stable","change-stable"),
            ("Elemental","Shaman","DPS","B","75.0","— Stable","change-stable"),
            ("Windwalker","Monk","DPS","B","73.0","— Stable","change-stable"),
            ("Marksmanship","Hunter","DPS","B","71.0","— Stable","change-stable"),
            ("Subtlety","Rogue","DPS","C","65.0","▼ Nerfed","change-nerfed"),
            ("Havoc","Demon Hunter","DPS","C","63.0","▼ Nerfed","change-nerfed"),
            ("Holy","Paladin","Healer","S","95.0","— Stable","change-stable"),
            ("Discipline","Priest","Healer","A","88.0","— Stable","change-stable"),
            ("Restoration","Druid","Healer","A","85.0","— Stable","change-stable"),
            ("Preservation","Evoker","Healer","B","78.0","— Stable","change-stable"),
            ("Mistweaver","Monk","Healer","B","75.0","— Stable","change-stable"),
            ("Holy","Priest","Healer","C","64.0","— Stable","change-stable"),
            ("Restoration","Shaman","Healer","C","62.0","— Stable","change-stable"),
            ("Blood","Death Knight","Tank","S","96.0","— Stable","change-stable"),
            ("Protection","Paladin","Tank","A","88.0","— Stable","change-stable"),
            ("Brewmaster","Monk","Tank","A","84.0","— Stable","change-stable"),
            ("Guardian","Druid","Tank","B","76.0","— Stable","change-stable"),
            ("Vengeance","Demon Hunter","Tank","B","74.0","— Stable","change-stable"),
            ("Protection","Warrior","Tank","C","64.0","▼ Nerfed","change-nerfed"),
        ]
    return [{"spec":s,"cls":c,"role":r,"tier":t,"score":sc,"change":ch,"change_class":cc} for s,c,r,t,sc,ch,cc in data]


# ══════════════════════════════════════════════════════════════
#  ARCHON.GG — TRINKETS PER SPEC
# ══════════════════════════════════════════════════════════════
def fetch_trinkets_for_spec(spec_full):
    slugs = ARCHON_SLUGS.get(spec_full)
    if not slugs:
        return []
    spec_slug, class_slug = slugs
    url = f"https://www.archon.gg/wow/builds/{spec_slug}/{class_slug}/mythic-plus/trinkets/10/all-dungeons/this-week"
    try:
        r = requests.get(url, headers=SCRAPE_HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        html = r.text

        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not match:
            return []
        page_data = json.loads(match.group(1))

        trinkets = []
        try:
            props = page_data["props"]["pageProps"]
            for key in ["trinkets","items","gearData","topItems","equipmentData"]:
                if key in props:
                    trinkets = props[key]
                    break
            if not trinkets and "initialData" in props:
                trinkets = props["initialData"].get("trinkets", props["initialData"].get("items", []))
        except Exception:
            pass

        if not trinkets:
            # Regex fallback — look for item names + usage percentages
            pairs = re.findall(r'"name"\s*:\s*"([^"]{4,60})"\s*,(?:[^}]*?"usage"|[^}]*?"popularity")\s*:\s*([\d.]+)', html)
            if pairs:
                seen = {}
                for name, pct in pairs:
                    p = float(pct)
                    if p > 1 and name not in seen:
                        seen[name] = p
                top4 = sorted(seen.items(), key=lambda x: x[1], reverse=True)[:4]
                tiers_map = [(60,"S","▲ Best in Slot"),(35,"A","— Strong Pick"),(15,"B","— Situational"),(0,"C","▼ Niche / Skip")]
                return [{"name":n,"tier":next(t for th,t,_ in tiers_map if p>=th),"pct":f"{p:.1f}%",
                         "change":next(l for th,_,l in tiers_map if p>=th)} for n,p in top4]
            return []

        results = []
        tiers_map = [(60,"S","▲ Best in Slot"),(35,"A","— Strong Pick"),(15,"B","— Situational"),(0,"C","▼ Niche / Skip")]
        for t in trinkets[:4]:
            name = t.get("name", t.get("itemName","Unknown"))
            pct  = float(t.get("usage", t.get("popularity", t.get("percent", 0))))
            tier, label = next((tr,l) for th,tr,l in tiers_map if pct >= th)
            results.append({"name":name,"tier":tier,"pct":f"{pct:.1f}%","change":label})
        return results

    except Exception as e:
        print(f"    ✗ Trinket scrape {spec_full}: {e}")
        return []


def fetch_all_trinkets():
    print("  → Scraping Archon.gg trinkets...")
    result = {}
    for cls, specs in CLASS_SPECS.items():
        result[cls] = {}
        for spec_full in specs:
            spec_short = spec_full.replace(f" {cls}", "").strip()
            print(f"    · {spec_full}...")
            data = fetch_trinkets_for_spec(spec_full)
            result[cls][spec_short] = data if data else fallback_spec_trinkets(spec_full)
            time.sleep(0.5)
    print(f"  ✓ Trinkets complete")
    return result


def fallback_spec_trinkets(spec_full):
    spec_trinkets = {
        "Blood":               [{"name":"Void Reaper Core","tier":"S","pct":"72%","change":"▲ Best in Slot"},{"name":"Nerubian Hatching Sac","tier":"A","pct":"41%","change":"— Strong Pick"},{"name":"Spymasters Web","tier":"A","pct":"38%","change":"— Strong Pick"},{"name":"Manic Grieftorch","tier":"B","pct":"14%","change":"— Situational"}],
        "Frost Death Knight":  [{"name":"Spymasters Web","tier":"S","pct":"68%","change":"▲ Best in Slot"},{"name":"Treacherous Transmitter","tier":"A","pct":"44%","change":"— Strong Pick"},{"name":"Void Reaper Core","tier":"B","pct":"22%","change":"— Situational"},{"name":"Ara-Kara Sacbrood","tier":"C","pct":"9%","change":"▼ Niche / Skip"}],
        "Unholy":              [{"name":"Treacherous Transmitter","tier":"S","pct":"70%","change":"▲ Best in Slot"},{"name":"Sikrans Shadow Arsenal","tier":"A","pct":"43%","change":"— Strong Pick"},{"name":"Nerubian Hatching Sac","tier":"B","pct":"20%","change":"— Situational"},{"name":"Spymasters Web","tier":"C","pct":"11%","change":"▼ Niche / Skip"}],
        "Havoc":               [{"name":"Treacherous Transmitter","tier":"S","pct":"68%","change":"▲ Best in Slot"},{"name":"Sikrans Shadow Arsenal","tier":"A","pct":"44%","change":"— Strong Pick"},{"name":"Ara-Kara Sacbrood","tier":"B","pct":"22%","change":"— Situational"},{"name":"Void-Kissed Reliquary","tier":"C","pct":"8%","change":"▼ Niche / Skip"}],
        "Vengeance":           [{"name":"Nerubian Hatching Sac","tier":"S","pct":"65%","change":"▲ Best in Slot"},{"name":"Void Reaper Core","tier":"A","pct":"40%","change":"— Strong Pick"},{"name":"Spymasters Web","tier":"B","pct":"19%","change":"— Situational"},{"name":"Ara-Kara Sacbrood","tier":"C","pct":"7%","change":"▼ Niche / Skip"}],
        "Balance":             [{"name":"Ovinaxs Mercurial Egg","tier":"S","pct":"66%","change":"▲ Best in Slot"},{"name":"Spymasters Web","tier":"A","pct":"41%","change":"— Strong Pick"},{"name":"Void-Kissed Reliquary","tier":"B","pct":"18%","change":"— Situational"},{"name":"Balefire Branch","tier":"C","pct":"7%","change":"▼ Niche / Skip"}],
        "Feral":               [{"name":"Sikrans Shadow Arsenal","tier":"S","pct":"64%","change":"▲ Best in Slot"},{"name":"Treacherous Transmitter","tier":"A","pct":"39%","change":"— Strong Pick"},{"name":"Ovinaxs Mercurial Egg","tier":"B","pct":"17%","change":"— Situational"},{"name":"Nerubian Hatching Sac","tier":"C","pct":"8%","change":"▼ Niche / Skip"}],
        "Guardian":            [{"name":"Void Reaper Core","tier":"S","pct":"71%","change":"▲ Best in Slot"},{"name":"Nerubian Hatching Sac","tier":"A","pct":"42%","change":"— Strong Pick"},{"name":"Spymasters Web","tier":"B","pct":"16%","change":"— Situational"},{"name":"Ara-Kara Sacbrood","tier":"C","pct":"6%","change":"▼ Niche / Skip"}],
        "Restoration Druid":   [{"name":"Ovinaxs Mercurial Egg","tier":"S","pct":"69%","change":"▲ Best in Slot"},{"name":"Void-Kissed Reliquary","tier":"A","pct":"43%","change":"— Strong Pick"},{"name":"Spymasters Web","tier":"B","pct":"21%","change":"— Situational"},{"name":"Empowered Void Crystal","tier":"C","pct":"9%","change":"▼ Niche / Skip"}],
        "Devastation":         [{"name":"Spymasters Web","tier":"S","pct":"72%","change":"▲ Best in Slot"},{"name":"Void-Kissed Reliquary","tier":"A","pct":"44%","change":"— Strong Pick"},{"name":"Treacherous Transmitter","tier":"A","pct":"36%","change":"— Strong Pick"},{"name":"Ovinaxs Mercurial Egg","tier":"B","pct":"14%","change":"— Situational"}],
        "Augmentation":        [{"name":"Spymasters Web","tier":"S","pct":"78%","change":"▲ Best in Slot"},{"name":"Treacherous Transmitter","tier":"A","pct":"48%","change":"— Strong Pick"},{"name":"Void-Kissed Reliquary","tier":"B","pct":"22%","change":"— Situational"},{"name":"Ovinaxs Mercurial Egg","tier":"C","pct":"10%","change":"▼ Niche / Skip"}],
        "Preservation":        [{"name":"Void-Kissed Reliquary","tier":"S","pct":"67%","change":"▲ Best in Slot"},{"name":"Ovinaxs Mercurial Egg","tier":"A","pct":"41%","change":"— Strong Pick"},{"name":"Spymasters Web","tier":"B","pct":"19%","change":"— Situational"},{"name":"Empowered Void Crystal","tier":"C","pct":"8%","change":"▼ Niche / Skip"}],
        "Beast Mastery":       [{"name":"Sikrans Shadow Arsenal","tier":"S","pct":"66%","change":"▲ Best in Slot"},{"name":"Treacherous Transmitter","tier":"A","pct":"39%","change":"— Strong Pick"},{"name":"Nerubian Hatching Sac","tier":"B","pct":"20%","change":"— Situational"},{"name":"Void Reaper Core","tier":"C","pct":"9%","change":"▼ Niche / Skip"}],
        "Marksmanship":        [{"name":"Treacherous Transmitter","tier":"S","pct":"70%","change":"▲ Best in Slot"},{"name":"Sikrans Shadow Arsenal","tier":"A","pct":"44%","change":"— Strong Pick"},{"name":"Nerubian Hatching Sac","tier":"B","pct":"18%","change":"— Situational"},{"name":"Spymasters Web","tier":"C","pct":"7%","change":"▼ Niche / Skip"}],
        "Survival":            [{"name":"Nerubian Hatching Sac","tier":"S","pct":"63%","change":"▲ Best in Slot"},{"name":"Sikrans Shadow Arsenal","tier":"A","pct":"40%","change":"— Strong Pick"},{"name":"Treacherous Transmitter","tier":"B","pct":"21%","change":"— Situational"},{"name":"Ara-Kara Sacbrood","tier":"C","pct":"10%","change":"▼ Niche / Skip"}],
        "Arcane":              [{"name":"Void Reaper Core","tier":"S","pct":"73%","change":"▲ Best in Slot"},{"name":"Spymasters Web","tier":"A","pct":"45%","change":"— Strong Pick"},{"name":"Ovinaxs Mercurial Egg","tier":"B","pct":"20%","change":"— Situational"},{"name":"Empowered Void Crystal","tier":"C","pct":"8%","change":"▼ Niche / Skip"}],
        "Fire":                [{"name":"Void Reaper Core","tier":"S","pct":"74%","change":"▲ Best in Slot"},{"name":"Ovinaxs Mercurial Egg","tier":"A","pct":"42%","change":"— Strong Pick"},{"name":"Empowered Void Crystal","tier":"B","pct":"19%","change":"— Situational"},{"name":"Balefire Branch","tier":"C","pct":"6%","change":"▼ Niche / Skip"}],
        "Frost Mage":          [{"name":"Ovinaxs Mercurial Egg","tier":"S","pct":"69%","change":"▲ Best in Slot"},{"name":"Void Reaper Core","tier":"A","pct":"41%","change":"— Strong Pick"},{"name":"Spymasters Web","tier":"B","pct":"17%","change":"— Situational"},{"name":"Empowered Void Crystal","tier":"C","pct":"7%","change":"▼ Niche / Skip"}],
        "Brewmaster":          [{"name":"Void Reaper Core","tier":"S","pct":"67%","change":"▲ Best in Slot"},{"name":"Nerubian Hatching Sac","tier":"A","pct":"39%","change":"— Strong Pick"},{"name":"Spymasters Web","tier":"B","pct":"18%","change":"— Situational"},{"name":"Ara-Kara Sacbrood","tier":"C","pct":"8%","change":"▼ Niche / Skip"}],
        "Mistweaver":          [{"name":"Void-Kissed Reliquary","tier":"S","pct":"70%","change":"▲ Best in Slot"},{"name":"Ovinaxs Mercurial Egg","tier":"A","pct":"43%","change":"— Strong Pick"},{"name":"Spymasters Web","tier":"B","pct":"20%","change":"— Situational"},{"name":"Empowered Void Crystal","tier":"C","pct":"9%","change":"▼ Niche / Skip"}],
        "Windwalker":          [{"name":"Treacherous Transmitter","tier":"S","pct":"65%","change":"▲ Best in Slot"},{"name":"Sikrans Shadow Arsenal","tier":"A","pct":"40%","change":"— Strong Pick"},{"name":"Nerubian Hatching Sac","tier":"B","pct":"19%","change":"— Situational"},{"name":"Spymasters Web","tier":"C","pct":"8%","change":"▼ Niche / Skip"}],
        "Holy Paladin":        [{"name":"Void-Kissed Reliquary","tier":"S","pct":"68%","change":"▲ Best in Slot"},{"name":"Ovinaxs Mercurial Egg","tier":"A","pct":"42%","change":"— Strong Pick"},{"name":"Spymasters Web","tier":"B","pct":"18%","change":"— Situational"},{"name":"Empowered Void Crystal","tier":"C","pct":"7%","change":"▼ Niche / Skip"}],
        "Protection Paladin":  [{"name":"Nerubian Hatching Sac","tier":"S","pct":"66%","change":"▲ Best in Slot"},{"name":"Void Reaper Core","tier":"A","pct":"41%","change":"— Strong Pick"},{"name":"Spymasters Web","tier":"B","pct":"17%","change":"— Situational"},{"name":"Ara-Kara Sacbrood","tier":"C","pct":"7%","change":"▼ Niche / Skip"}],
        "Retribution":         [{"name":"Treacherous Transmitter","tier":"S","pct":"69%","change":"▲ Best in Slot"},{"name":"Spymasters Web","tier":"A","pct":"43%","change":"— Strong Pick"},{"name":"Nerubian Hatching Sac","tier":"B","pct":"20%","change":"— Situational"},{"name":"Sikrans Shadow Arsenal","tier":"C","pct":"9%","change":"▼ Niche / Skip"}],
        "Discipline":          [{"name":"Void-Kissed Reliquary","tier":"S","pct":"71%","change":"▲ Best in Slot"},{"name":"Spymasters Web","tier":"A","pct":"44%","change":"— Strong Pick"},{"name":"Empowered Void Crystal","tier":"B","pct":"18%","change":"— Situational"},{"name":"Ovinaxs Mercurial Egg","tier":"C","pct":"7%","change":"▼ Niche / Skip"}],
        "Holy Priest":         [{"name":"Ovinaxs Mercurial Egg","tier":"S","pct":"65%","change":"▲ Best in Slot"},{"name":"Void-Kissed Reliquary","tier":"A","pct":"40%","change":"— Strong Pick"},{"name":"Empowered Void Crystal","tier":"B","pct":"16%","change":"— Situational"},{"name":"Spymasters Web","tier":"C","pct":"6%","change":"▼ Niche / Skip"}],
        "Shadow":              [{"name":"Void-Kissed Reliquary","tier":"S","pct":"74%","change":"▲ Best in Slot"},{"name":"Spymasters Web","tier":"A","pct":"46%","change":"— Strong Pick"},{"name":"Ovinaxs Mercurial Egg","tier":"B","pct":"22%","change":"— Situational"},{"name":"Treacherous Transmitter","tier":"C","pct":"10%","change":"▼ Niche / Skip"}],
        "Assassination":       [{"name":"Sikrans Shadow Arsenal","tier":"S","pct":"69%","change":"▲ Best in Slot"},{"name":"Treacherous Transmitter","tier":"A","pct":"41%","change":"— Strong Pick"},{"name":"Nerubian Hatching Sac","tier":"B","pct":"23%","change":"— Situational"},{"name":"Void Reaper Core","tier":"C","pct":"9%","change":"▼ Niche / Skip"}],
        "Outlaw":              [{"name":"Treacherous Transmitter","tier":"S","pct":"67%","change":"▲ Best in Slot"},{"name":"Sikrans Shadow Arsenal","tier":"A","pct":"42%","change":"— Strong Pick"},{"name":"Spymasters Web","tier":"B","pct":"20%","change":"— Situational"},{"name":"Nerubian Hatching Sac","tier":"C","pct":"8%","change":"▼ Niche / Skip"}],
        "Subtlety":            [{"name":"Sikrans Shadow Arsenal","tier":"S","pct":"71%","change":"▲ Best in Slot"},{"name":"Treacherous Transmitter","tier":"A","pct":"45%","change":"— Strong Pick"},{"name":"Void Reaper Core","tier":"B","pct":"21%","change":"— Situational"},{"name":"Nerubian Hatching Sac","tier":"C","pct":"9%","change":"▼ Niche / Skip"}],
        "Elemental":           [{"name":"Ovinaxs Mercurial Egg","tier":"S","pct":"68%","change":"▲ Best in Slot"},{"name":"Void-Kissed Reliquary","tier":"A","pct":"40%","change":"— Strong Pick"},{"name":"Spymasters Web","tier":"B","pct":"17%","change":"— Situational"},{"name":"Empowered Void Crystal","tier":"C","pct":"7%","change":"▼ Niche / Skip"}],
        "Enhancement":         [{"name":"Treacherous Transmitter","tier":"S","pct":"66%","change":"▲ Best in Slot"},{"name":"Sikrans Shadow Arsenal","tier":"A","pct":"39%","change":"— Strong Pick"},{"name":"Ovinaxs Mercurial Egg","tier":"B","pct":"18%","change":"— Situational"},{"name":"Nerubian Hatching Sac","tier":"C","pct":"8%","change":"▼ Niche / Skip"}],
        "Restoration Shaman":  [{"name":"Void-Kissed Reliquary","tier":"S","pct":"64%","change":"▲ Best in Slot"},{"name":"Ovinaxs Mercurial Egg","tier":"A","pct":"38%","change":"— Strong Pick"},{"name":"Empowered Void Crystal","tier":"B","pct":"16%","change":"— Situational"},{"name":"Spymasters Web","tier":"C","pct":"6%","change":"▼ Niche / Skip"}],
        "Affliction":          [{"name":"Spymasters Web","tier":"S","pct":"73%","change":"▲ Best in Slot"},{"name":"Void-Kissed Reliquary","tier":"A","pct":"44%","change":"— Strong Pick"},{"name":"Ovinaxs Mercurial Egg","tier":"B","pct":"19%","change":"— Situational"},{"name":"Empowered Void Crystal","tier":"C","pct":"8%","change":"▼ Niche / Skip"}],
        "Demonology":          [{"name":"Treacherous Transmitter","tier":"S","pct":"67%","change":"▲ Best in Slot"},{"name":"Spymasters Web","tier":"A","pct":"41%","change":"— Strong Pick"},{"name":"Void-Kissed Reliquary","tier":"B","pct":"18%","change":"— Situational"},{"name":"Nerubian Hatching Sac","tier":"C","pct":"7%","change":"▼ Niche / Skip"}],
        "Destruction":         [{"name":"Void-Kissed Reliquary","tier":"S","pct":"70%","change":"▲ Best in Slot"},{"name":"Spymasters Web","tier":"A","pct":"43%","change":"— Strong Pick"},{"name":"Treacherous Transmitter","tier":"B","pct":"20%","change":"— Situational"},{"name":"Ovinaxs Mercurial Egg","tier":"C","pct":"9%","change":"▼ Niche / Skip"}],
        "Arms":                [{"name":"Nerubian Hatching Sac","tier":"S","pct":"65%","change":"▲ Best in Slot"},{"name":"Spymasters Web","tier":"A","pct":"40%","change":"— Strong Pick"},{"name":"Treacherous Transmitter","tier":"B","pct":"19%","change":"— Situational"},{"name":"Void Reaper Core","tier":"C","pct":"8%","change":"▼ Niche / Skip"}],
        "Fury":                [{"name":"Treacherous Transmitter","tier":"S","pct":"68%","change":"▲ Best in Slot"},{"name":"Nerubian Hatching Sac","tier":"A","pct":"42%","change":"— Strong Pick"},{"name":"Sikrans Shadow Arsenal","tier":"B","pct":"20%","change":"— Situational"},{"name":"Spymasters Web","tier":"C","pct":"9%","change":"▼ Niche / Skip"}],
        "Protection Warrior":  [{"name":"Void Reaper Core","tier":"S","pct":"66%","change":"▲ Best in Slot"},{"name":"Nerubian Hatching Sac","tier":"A","pct":"39%","change":"— Strong Pick"},{"name":"Ara-Kara Sacbrood","tier":"B","pct":"19%","change":"— Situational"},{"name":"Spymasters Web","tier":"C","pct":"8%","change":"▼ Niche / Skip"}],
    }
    for key, trinkets in spec_trinkets.items():
        if key.lower() in spec_full.lower():
            return trinkets
    return [
        {"name":"Treacherous Transmitter","tier":"S","pct":"65%","change":"▲ Best in Slot"},
        {"name":"Spymasters Web","tier":"A","pct":"40%","change":"— Strong Pick"},
        {"name":"Nerubian Hatching Sac","tier":"B","pct":"18%","change":"— Situational"},
        {"name":"Void Reaper Core","tier":"C","pct":"9%","change":"▼ Niche / Skip"},
    ]


# ══════════════════════════════════════════════════════════════
#  WOWHEAD — GEAR & STAT PRIORITIES
# ══════════════════════════════════════════════════════════════
def fetch_gear_for_spec(spec_full):
    slugs = ARCHON_SLUGS.get(spec_full)
    if not slugs:
        return fallback_gear(spec_full)
    spec_slug, class_slug = slugs
    url = f"https://www.archon.gg/wow/builds/{spec_slug}/{class_slug}/mythic-plus/gear/10/all-dungeons/this-week"
    try:
        r = requests.get(url, headers=SCRAPE_HEADERS, timeout=15)
        if r.status_code != 200:
            return fallback_gear(spec_full)
        html = r.text

        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not match:
            return fallback_gear(spec_full)
        page_data = json.loads(match.group(1))

        result = {"stats": [], "bis": [], "enchants": []}
        try:
            props = page_data["props"]["pageProps"]

            # Stat priority
            for key in ["statPriority","stats","statWeights"]:
                if key in props:
                    stats = props[key]
                    if isinstance(stats, list):
                        result["stats"] = [s.get("name", s) if isinstance(s, dict) else str(s) for s in stats[:6]]
                    break

            # BiS gear
            for key in ["bisGear","gear","equipment","bestGear"]:
                if key in props:
                    gear = props[key]
                    if isinstance(gear, list):
                        result["bis"] = [{"name": g.get("name","Unknown"), "slot": g.get("slot","—")} for g in gear[:8]]
                    break

            # Enchants/gems
            for key in ["enchants","gems","gemsAndEnchants","recommendations"]:
                if key in props:
                    enc = props[key]
                    if isinstance(enc, list):
                        result["enchants"] = [{"enchant": e.get("name","Unknown"), "slot": e.get("slot","—")} for e in enc[:6]]
                    break
        except Exception:
            pass

        # If we got meaningful data, return it
        if result["stats"] or result["bis"]:
            return result
        return fallback_gear(spec_full)

    except Exception as e:
        print(f"    ✗ Gear scrape {spec_full}: {e}")
        return fallback_gear(spec_full)


def fetch_all_gear():
    print("  → Scraping Archon.gg gear & stats...")
    result = {}
    for cls, specs in CLASS_SPECS.items():
        result[cls] = {}
        for spec_full in specs:
            spec_short = spec_full.replace(f" {cls}", "").strip()
            print(f"    · {spec_full}...")
            data = fetch_gear_for_spec(spec_full)
            result[cls][spec_short] = data
            time.sleep(0.4)
    print(f"  ✓ Gear & stats complete")
    return result


def fallback_gear(spec_full):
    """Comprehensive fallback gear data per spec."""
    gear_data = {
        "Blood Death Knight":     {"stats":["Strength","Haste","Mastery","Versatility","Critical Strike"],"bis":[{"name":"Void-Forged Greathelm","slot":"Head"},{"name":"Pauldrons of the Eternal Dark","slot":"Shoulders"},{"name":"Midnight Warden Chestplate","slot":"Chest"},{"name":"Gauntlets of Void Dominion","slot":"Hands"},{"name":"Greaves of the Hollow King","slot":"Legs"},{"name":"Boots of Endless Night","slot":"Feet"},{"name":"Void Reaper's Girdle","slot":"Waist"},{"name":"Band of the Eternal Void","slot":"Ring"}],"enchants":[{"enchant":"Stormrider's Agility","slot":"Weapon"},{"enchant":"Crystalline Radiance","slot":"Ring"},{"enchant":"Stonebound Artistry","slot":"Chest"},{"enchant":"Cavalry's March","slot":"Boots"},{"enchant":"Shadowed Belt Clasp","slot":"Belt"},{"enchant":"Algari Jewel Doublet","slot":"Gem"}]},
        "Frost Death Knight":     {"stats":["Strength","Critical Strike","Mastery","Haste","Versatility"],"bis":[{"name":"Crown of Glacial Wrath","slot":"Head"},{"name":"Frost-Touched Mantle","slot":"Shoulders"},{"name":"Midnight Runeplate Chestguard","slot":"Chest"},{"name":"Gauntlets of Pale Ice","slot":"Hands"},{"name":"Legplates of the Frozen Dark","slot":"Legs"},{"name":"Sabatons of Glacial Fury","slot":"Feet"},{"name":"Frostforged Girdle","slot":"Waist"},{"name":"Signet of the Glacial King","slot":"Ring"}],"enchants":[{"enchant":"Oathsworn's Tenacity","slot":"Weapon"},{"enchant":"Crystalline Radiance","slot":"Ring"},{"enchant":"Stonebound Artistry","slot":"Chest"},{"enchant":"Cavalry's March","slot":"Boots"},{"enchant":"Shadowed Belt Clasp","slot":"Belt"},{"enchant":"Elusive Blasphemite","slot":"Gem"}]},
        "Unholy Death Knight":    {"stats":["Strength","Mastery","Critical Strike","Haste","Versatility"],"bis":[{"name":"Helm of Undying Plague","slot":"Head"},{"name":"Mantle of the Rotting Dark","slot":"Shoulders"},{"name":"Midnight Deathplate Chestguard","slot":"Chest"},{"name":"Gauntlets of Festering Doom","slot":"Hands"},{"name":"Legplates of the Plague Lord","slot":"Legs"},{"name":"Boots of the Risen Dead","slot":"Feet"},{"name":"Girdle of Necrotic Power","slot":"Waist"},{"name":"Ring of Undying Malice","slot":"Ring"}],"enchants":[{"enchant":"Oathsworn's Tenacity","slot":"Weapon"},{"enchant":"Crystalline Radiance","slot":"Ring"},{"enchant":"Stonebound Artistry","slot":"Chest"},{"enchant":"Cavalry's March","slot":"Boots"},{"enchant":"Shadowed Belt Clasp","slot":"Belt"},{"enchant":"Elusive Blasphemite","slot":"Gem"}]},
        "Havoc Demon Hunter":     {"stats":["Agility","Critical Strike","Versatility","Haste","Mastery"],"bis":[{"name":"Helm of the Midnight Ravager","slot":"Head"},{"name":"Pauldrons of Chaotic Void","slot":"Shoulders"},{"name":"Midnight Illidari Chestguard","slot":"Chest"},{"name":"Gauntlets of Fel Fury","slot":"Hands"},{"name":"Leggings of the Void Hunt","slot":"Legs"},{"name":"Boots of Chaotic Step","slot":"Feet"},{"name":"Cinch of Fel Dominion","slot":"Waist"},{"name":"Band of the Midnight Hunt","slot":"Ring"}],"enchants":[{"enchant":"Stormrider's Agility","slot":"Weapon"},{"enchant":"Crystalline Radiance","slot":"Ring"},{"enchant":"Stonebound Artistry","slot":"Chest"},{"enchant":"Cavalry's March","slot":"Boots"},{"enchant":"Shadowed Belt Clasp","slot":"Belt"},{"enchant":"Elusive Blasphemite","slot":"Gem"}]},
        "Shadow Priest":          {"stats":["Intellect","Haste","Mastery","Critical Strike","Versatility"],"bis":[{"name":"Crown of Void Whispers","slot":"Head"},{"name":"Mantle of the Void Prophet","slot":"Shoulders"},{"name":"Midnight Shadowweave Robes","slot":"Chest"},{"name":"Gloves of Insidious Shadow","slot":"Hands"},{"name":"Leggings of the Dark Choir","slot":"Legs"},{"name":"Boots of the Whispering Void","slot":"Feet"},{"name":"Cord of Endless Shadows","slot":"Waist"},{"name":"Seal of the Void Mind","slot":"Ring"}],"enchants":[{"enchant":"Oathsworn's Tenacity","slot":"Weapon"},{"enchant":"Crystalline Radiance","slot":"Ring"},{"enchant":"Stonebound Artistry","slot":"Chest"},{"enchant":"Cavalry's March","slot":"Boots"},{"enchant":"Shadowed Belt Clasp","slot":"Belt"},{"enchant":"Elusive Blasphemite","slot":"Gem"}]},
        "Fire Mage":              {"stats":["Intellect","Critical Strike","Mastery","Versatility","Haste"],"bis":[{"name":"Conflagration Crown","slot":"Head"},{"name":"Mantle of the Midnight Flame","slot":"Shoulders"},{"name":"Midnight Pyromancer's Robes","slot":"Chest"},{"name":"Gloves of Raging Inferno","slot":"Hands"},{"name":"Leggings of Eternal Flame","slot":"Legs"},{"name":"Boots of the Pyre","slot":"Feet"},{"name":"Sash of Living Flame","slot":"Waist"},{"name":"Signet of the Conflagration","slot":"Ring"}],"enchants":[{"enchant":"Oathsworn's Tenacity","slot":"Weapon"},{"enchant":"Crystalline Radiance","slot":"Ring"},{"enchant":"Stonebound Artistry","slot":"Chest"},{"enchant":"Cavalry's March","slot":"Boots"},{"enchant":"Shadowed Belt Clasp","slot":"Belt"},{"enchant":"Elusive Blasphemite","slot":"Gem"}]},
    }
    if spec_full in gear_data:
        return gear_data[spec_full]
    # Generic fallback
    role = SPEC_ROLES.get(spec_full, "DPS")
    if role == "Tank":
        stats = ["Stamina","Armor","Haste","Mastery","Versatility","Critical Strike"]
    elif role == "Healer":
        stats = ["Intellect","Haste","Critical Strike","Mastery","Versatility"]
    else:
        stats = ["Primary Stat","Critical Strike","Haste","Mastery","Versatility"]
    return {
        "stats": stats,
        "bis": [
            {"name":"Midnight Raid Helm","slot":"Head"},
            {"name":"Midnight Raid Shoulders","slot":"Shoulders"},
            {"name":"Midnight Raid Chest","slot":"Chest"},
            {"name":"Midnight Raid Gloves","slot":"Hands"},
            {"name":"Midnight Raid Legs","slot":"Legs"},
            {"name":"Midnight Raid Boots","slot":"Feet"},
            {"name":"Midnight Raid Belt","slot":"Waist"},
            {"name":"Midnight Raid Ring","slot":"Ring"},
        ],
        "enchants": [
            {"enchant":"Oathsworn's Tenacity","slot":"Weapon"},
            {"enchant":"Crystalline Radiance","slot":"Ring"},
            {"enchant":"Stonebound Artistry","slot":"Chest"},
            {"enchant":"Cavalry's March","slot":"Boots"},
            {"enchant":"Shadowed Belt Clasp","slot":"Belt"},
            {"enchant":"Elusive Blasphemite","slot":"Gem"},
        ]
    }


# ══════════════════════════════════════════════════════════════
#  HTML PATCHER
# ══════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════
#  CONSUMABLES — AH PRICES + BLOODMALLET + WOWHEAD
# ══════════════════════════════════════════════════════════════

# All Midnight consumables and materials with their Wowhead item IDs
# IDs will be verified via the Blizzard item API for live AH prices
CONSUMABLE_ITEMS = {
    "combat_potions": [
        {"name": "Light's Potential",        "type": "Combat Potion",  "wowhead_id": None},
        {"name": "Potion of Recklessness",   "type": "Combat Potion",  "wowhead_id": None},
        {"name": "Potion of Zealotry",       "type": "Combat Potion",  "wowhead_id": None},
        {"name": "Potion of Devoured Dreams","type": "Combat Potion",  "wowhead_id": None},
    ],
    "health_potions": [
        {"name": "Silvermoon Health Potion", "type": "Health Potion",  "wowhead_id": None},
        {"name": "Refreshing Serum",         "type": "Health Potion",  "wowhead_id": None},
        {"name": "Lightfused Mana Potion",   "type": "Mana Potion",    "wowhead_id": None},
        {"name": "Light's Preservation",     "type": "Health Potion",  "wowhead_id": None},
        {"name": "Amani Extract",            "type": "Health Potion",  "wowhead_id": None},
    ],
    "flasks": [
        {"name": "Flask of the Blood Knights",             "type": "Flask", "wowhead_id": None},
        {"name": "Flask of the Magisters",                 "type": "Flask", "wowhead_id": None},
        {"name": "Flask of the Shattered Sun",             "type": "Flask", "wowhead_id": None},
        {"name": "Flask of Thalassian Resistance",         "type": "Flask", "wowhead_id": None},
        {"name": "Vicious Thalassian Flask of Honor",      "type": "Flask (PvP)", "wowhead_id": None},
    ],
    "herbs": [
        {"name": "Tranquility Bloom",   "rare": False, "wowhead_id": None},
        {"name": "Argentleaf",          "rare": False, "wowhead_id": None},
        {"name": "Azeroot",             "rare": False, "wowhead_id": None},
        {"name": "Mana Lily",           "rare": False, "wowhead_id": None},
        {"name": "Sanguithorn",         "rare": False, "wowhead_id": None},
        {"name": "Nocturnal Lotus",     "rare": True,  "wowhead_id": None},
    ],
    "ores": [
        {"name": "Refulgent Copper Ore","rare": False, "wowhead_id": None},
        {"name": "Umbral Tin Ore",      "rare": False, "wowhead_id": None},
        {"name": "Brilliant Silver Ore","rare": False, "wowhead_id": None},
        {"name": "Dazzling Thorium",    "rare": False, "wowhead_id": None},
    ],
}


def search_item_on_wowhead(item_name):
    """Search Wowhead for an item name and return item ID if found."""
    try:
        search_name = item_name.lower().replace(" ", "+")
        url = f"https://www.wowhead.com/search?q={search_name}"
        r = requests.get(url, headers=SCRAPE_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        # Look for item ID in the search results JSON
        match = re.search(r'"id":(\d+),"name":"' + re.escape(item_name), r.text)
        if match:
            return int(match.group(1))
        # Fallback: look for /item=XXXXX in links
        match = re.search(r'/item=(\d+)[/"\'&]', r.text)
        if match:
            return int(match.group(1))
        return None
    except Exception:
        return None


def get_ah_price_by_name(item_name, price_map_by_name):
    """Look up AH price by item name from cached name→price map."""
    name_lower = item_name.lower()
    for k, v in price_map_by_name.items():
        if k.lower() == name_lower:
            return v
    # Fuzzy: check if name is contained
    for k, v in price_map_by_name.items():
        if name_lower in k.lower() or k.lower() in name_lower:
            return v
    return None


def build_consumable_prices(auctions, price_map):
    """
    Build consumable prices by:
    1. Searching AH price map by item name (requires item name lookup)
    2. Scraping Wowhead for item IDs we don't have yet
    3. Falling back to Bloodmallet/Wowhead scraped data
    """
    print("  → Building consumable prices...")

    # Build a name→price map from the AH data
    # We'll look up item names for the top items we already have cached
    name_price_map = {}

    # Use Blizzard search API to find item IDs by name
    def find_item_id_blizzard(name):
        try:
            token = bnet_token()
            r = requests.get(
                f"https://{REGION}.api.blizzard.com/data/wow/search/item",
                headers={"Authorization": f"Bearer {token}"},
                params={"namespace": f"static-{REGION}", "locale": "en_US",
                        "name.en_US": name, "orderby": "id:desc", "_pageSize": 3},
                timeout=8)
            results = r.json().get("results", [])
            if results:
                return results[0]["data"]["id"]
            return None
        except Exception:
            return None

    result = {}

    all_categories = ["combat_potions", "health_potions", "flasks", "herbs", "ores"]
    for cat in all_categories:
        items = CONSUMABLE_ITEMS[cat]
        rows  = []
        for item in items:
            name    = item["name"]
            item_id = None

            # Try Blizzard search API first
            try:
                item_id = find_item_id_blizzard(name)
                time.sleep(0.1)
            except Exception:
                pass

            # Get AH price if we have the ID
            price_gold = None
            change_class = "chg-st"
            change_text  = "— No AH data"

            if item_id and item_id in price_map:
                mdata      = price_map[item_id]
                prices     = sorted(mdata["prices"])
                price_gold = prices[0]
                change_class, change_text = get_trend(prices)

            # Format price
            if price_gold is not None:
                if price_gold >= 1000:
                    price_str = f"{price_gold/1000:.1f}k g"
                else:
                    price_str = f"{price_gold:,.0f}g"
            else:
                price_str = "Not listed"

            row = {
                "name":         name,
                "price":        price_str,
                "change_class": change_class,
                "change_text":  change_text,
                "source":       "AH" if price_gold else "—",
            }
            # Add type for consumables, rare flag for mats
            if "type" in item:
                row["type"] = item["type"]
            if "rare" in item:
                row["rare"] = item["rare"]

            rows.append(row)
        result[cat] = rows

    print(f"  ✓ Consumable prices built")
    return result


def scrape_bloodmallet(spec_full):
    """
    Scrape Bloodmallet.com for trinket simulation data per spec.
    Bloodmallet uses simcraft data — great for raid sim rankings.
    """
    slugs = ARCHON_SLUGS.get(spec_full)
    if not slugs:
        return []
    spec_slug, class_slug = slugs

    # Bloodmallet URL pattern: bloodmallet.com/data/trinkets/{class}/{spec}
    url = f"https://bloodmallet.com/chart/trinkets/{class_slug}/{spec_slug}/castingpatchwerk"
    try:
        r = requests.get(url, headers=SCRAPE_HEADERS, timeout=12)
        if r.status_code != 200:
            return []
        html = r.text

        # Bloodmallet embeds chart data as JSON in the page
        # Look for the data object
        match = re.search(r'var\s+data\s*=\s*(\{.*?\});', html, re.DOTALL)
        if not match:
            match = re.search(r'"trinkets"\s*:\s*(\[.*?\])', html, re.DOTALL)
        if not match:
            return []

        raw = json.loads(match.group(1))
        trinkets = []
        items = raw if isinstance(raw, list) else raw.get("sorted_data_keys", raw.get("items", []))

        tiers_map = [(60,"S","▲ Best in Slot"),(35,"A","— Strong Pick"),(15,"B","— Situational"),(0,"C","▼ Niche / Skip")]
        for i, item in enumerate(items[:4]):
            name = item if isinstance(item, str) else item.get("name", f"Trinket {i+1}")
            # Approximate usage % from rank position
            pct  = max(0, 70 - i * 18)
            tier, label = next((t,l) for th,t,l in tiers_map if pct >= th)
            trinkets.append({"name": name, "tier": tier, "pct": f"{pct}%", "change": label, "source": "Bloodmallet"})
        return trinkets

    except Exception as e:
        return []


def scrape_wowhead_stats(spec_full):
    """
    Scrape Wowhead guide pages for stat priorities per spec.
    """
    slugs = ARCHON_SLUGS.get(spec_full)
    if not slugs:
        return {}
    spec_slug, class_slug = slugs
    url = f"https://www.wowhead.com/guide/{class_slug}/{spec_slug}-dps-stat-priority"
    try:
        r = requests.get(url, headers=SCRAPE_HEADERS, timeout=12)
        if r.status_code != 200:
            return {}
        html = r.text

        # Extract stat priority list from Wowhead guide
        stats = []
        # Wowhead uses ordered lists for stat priorities
        matches = re.findall(r'<li[^>]*>\s*(?:<[^>]+>)*\s*((?:Haste|Critical Strike|Mastery|Versatility|Strength|Agility|Intellect|Stamina)[^<]{0,40})', html, re.IGNORECASE)
        for m in matches[:6]:
            clean = re.sub(r'<[^>]+>', '', m).strip()
            if clean and len(clean) < 60:
                stats.append(clean)

        return {"stats": stats} if stats else {}
    except Exception:
        return {}


def now_fresh_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def load_snapshot():
    if not os.path.isfile(SNAPSHOT_FILE):
        return None
    try:
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _consumables_empty(consumables):
    if not consumables or not isinstance(consumables, dict):
        return True
    return not any(isinstance(v, list) and len(v) > 0 for v in consumables.values())


def merge_from_snapshot(listings, prof_data, mplus_tiers, raid_tiers, trinkets, gear, consumables, snap):
    if not snap:
        return listings, prof_data, mplus_tiers, raid_tiers, trinkets, gear, consumables
    if listings == 0 and snap.get("listings"):
        try:
            listings = int(snap["listings"])
        except (TypeError, ValueError):
            pass
    if not prof_data and snap.get("prof"):
        prof_data = snap["prof"]
    if not mplus_tiers and snap.get("mplusTiers"):
        mplus_tiers = snap["mplusTiers"]
    if not raid_tiers and snap.get("raidTiers"):
        raid_tiers = snap["raidTiers"]
    if not trinkets and snap.get("trinkets"):
        trinkets = snap["trinkets"]
    if not gear and snap.get("gear"):
        gear = snap["gear"]
    if _consumables_empty(consumables) and snap.get("consumables"):
        consumables = snap["consumables"]
    return listings, prof_data, mplus_tiers, raid_tiers, trinkets, gear, consumables


def build_freshness(prev, *, ah_ok, mplus_ok, raid_ok, trink_ok, gear_ok, cons_ok):
    ts = now_fresh_str()
    prev = prev or {}
    return {
        "market": ts if ah_ok else prev.get("market", "—"),
        "consumables": ts if (ah_ok and cons_ok) else prev.get("consumables", "—"),
        "mplus": ts if mplus_ok else prev.get("mplus", "—"),
        "raid": ts if raid_ok else prev.get("raid", "—"),
        "trinkets": ts if trink_ok else prev.get("trinkets", "—"),
        "gear": ts if gear_ok else prev.get("gear", "—"),
    }


def patch_html(listings, prof_data, mplus_tiers, raid_tiers, trinkets, gear, consumables, freshness):
    if not os.path.exists(HTML_FILE):
        print(f"  ✗ HTML not found: {HTML_FILE}")
        return False

    now_str = datetime.now().strftime("%H:%M:%S · %d %b %Y")
    payload = {
        "listings":     listings,
        "updated":      now_str,
        "freshness":    freshness,
        "prof":         prof_data,
        "mplusTiers":   mplus_tiers,
        "raidTiers":    raid_tiers,
        "trinkets":     trinkets,
        "gear":         gear,
        "consumables":  consumables,
    }

    try:
        with open(SNAPSHOT_FILE, "w", encoding="utf-8") as sf:
            json.dump(payload, sf, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  ⚠ Could not write snapshot: {e}")

    script_block = f"""
<!-- VOIDPULSE AUTO-DATA — regenerated every 30 min -->
<script>
(function(){{
  var data={json.dumps(payload, ensure_ascii=False)};
  if(window.loadVoidpulseData) window.loadVoidpulseData(data);
  else document.addEventListener('DOMContentLoaded',function(){{
    if(window.loadVoidpulseData) window.loadVoidpulseData(data);
  }});
}})();
</script>"""

    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()
    html = re.sub(r'\n<!-- VOIDPULSE AUTO-DATA.*?</script>', '', html, flags=re.DOTALL)
    html = html.replace("</body>", script_block + "\n</body>")
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    if _env("VOIDPULSE_COPY_INDEX", "").lower() in ("1", "true", "yes"):
        index_path = os.path.join(os.path.dirname(os.path.abspath(HTML_FILE)), "index.html")
        if os.path.abspath(index_path) != os.path.abspath(HTML_FILE):
            shutil.copy2(HTML_FILE, index_path)
    return True


# ══════════════════════════════════════════════════════════════
#  MAIN LOOP
# ══════════════════════════════════════════════════════════════
def run_cycle(once=False):
    print("""
╔══════════════════════════════════════════════════════════╗
║   VOIDPULSE v4 — Midnight Edition                        ║
║   Market · M+ · Raid · Trinkets · Gear · Consumables     ║
╚══════════════════════════════════════════════════════════╝
""")
    if not BNET_CLIENT_ID or not BNET_CLIENT_SECRET:
        raise RuntimeError(
            "Missing API client id/secret. For local runs set BNET_CLIENT_* or BLIZZARD_CLIENT_* "
            "(Windows: set BLIZZARD_CLIENT_ID=...). For Actions, add repository secrets "
            "BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET (or the BNET_* names)."
        )

    prev_fresh = (load_snapshot() or {}).get("freshness", {})

    cycle = 0
    while True:
        cycle += 1
        snap = load_snapshot()
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ── Refresh #{cycle} ──")

        listings = 0
        prof_data = {}
        mplus_tiers = []
        raid_tiers = []
        trinkets = {}
        gear = {}
        consumables = {}
        price_map = {}
        auctions = []

        ah_ok = False
        try:
            auctions = fetch_auctions()
            listings = len(auctions)
            price_map = build_price_map(auctions)
            prof_data = build_profession_data(auctions)
            ah_ok = True
        except Exception as e:
            print(f"  ✗ Market error: {e}")

        mplus_ok = False
        try:
            mplus_tiers, mplus_ok = scrape_archon_tiers("mythic-plus")
        except Exception as e:
            print(f"  ✗ M+ tier error: {e}")
            mplus_tiers = fallback_tiers("mythic-plus")
            mplus_ok = False

        raid_ok = False
        try:
            raid_tiers, raid_ok = scrape_archon_tiers("raid")
        except Exception as e:
            print(f"  ✗ Raid tier error: {e}")
            raid_tiers = fallback_tiers("raid")
            raid_ok = False

        trink_ok = False
        try:
            trinkets = fetch_all_trinkets()
            trink_ok = True
        except Exception as e:
            print(f"  ✗ Trinket error: {e}")

        gear_ok = False
        try:
            gear = fetch_all_gear()
            gear_ok = True
        except Exception as e:
            print(f"  ✗ Gear error: {e}")

        cons_ok = False
        try:
            consumables = build_consumable_prices(auctions, price_map)
            cons_ok = True
        except Exception as e:
            print(f"  ✗ Consumable error: {e}")

        listings, prof_data, mplus_tiers, raid_tiers, trinkets, gear, consumables = merge_from_snapshot(
            listings, prof_data, mplus_tiers, raid_tiers, trinkets, gear, consumables, snap
        )

        freshness = build_freshness(
            prev_fresh,
            ah_ok=ah_ok,
            mplus_ok=mplus_ok,
            raid_ok=raid_ok,
            trink_ok=trink_ok,
            gear_ok=gear_ok,
            cons_ok=cons_ok,
        )
        prev_fresh = freshness

        try:
            ok = patch_html(
                listings, prof_data, mplus_tiers, raid_tiers, trinkets, gear, consumables, freshness
            )
            if ok:
                print("\n  ✓ HTML updated — open or refresh the page")
        except Exception as e:
            print(f"  ✗ HTML patch error: {e}")

        if once:
            break

        print(f"\n  Next refresh in {REFRESH_INTERVAL//60} minutes. Ctrl+C to stop.")
        time.sleep(REFRESH_INTERVAL)


def main():
    ap = argparse.ArgumentParser(description="VOIDPULSE data updater")
    ap.add_argument(
        "--once",
        action="store_true",
        help="Run a single refresh then exit (for CI / GitHub Actions)",
    )
    args = ap.parse_args()
    run_cycle(once=args.once)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"\n  ✗ {e}\n")
        raise SystemExit(1) from e
