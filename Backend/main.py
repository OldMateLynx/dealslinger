import os
import math
import re
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
from pydantic import BaseModel, ValidationError
from typing import Literal
from anthropic import Anthropic

load_dotenv()
app = FastAPI()

origins = [
    "http://localhost:3000",   # Next.js dev server
    "http://127.0.0.1:3000",
    "https://dealslinger.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
BASE_URL = "https://places.googleapis.com/v1/places"

claude_client = Anthropic(api_key=ANTHROPIC_API_KEY)
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

COMPETITOR_RADIUS_METERS = 10000
OPPORTUNITY_RADIUS_METERS = 10000


#product sets that much be searched before any ai ambiguous/creative search reasoning
HARDCODED_SEARCHES = {
    "Mouthguards": {
        "Competitors": ["Sports Store", "Pharmacy"],
        "Opportunities": ["Oval", "Rugby Club", "AFL Club", "Cricket Club", "Martial Arts Gym", "Boxing Gym"]
    },

    "Skateboards": {
        "Competitors": ["Skate Shop", "Surf Shop", "Sports Store"],
        "Opportunities": ["Skatepark"]
    },

    "Scooters": {
        "Competitors": ["Bike Shop", "Skate Shop", "Surf Shop", "Sports Store"],
        "Opportunities": ["Skatepark"]
    },

    "Knee_ElbowPads": {
        "Competitors": ["Bike Shop", "Skate Shop", "Sports Store"],
        "Opportunities": ["Skatepark", "BMX Track"]
    },

    "Skate_Scooter_Bike_Helmets": {
        "Competitors": ["Skate Shop", "Bike Shop", "Sports Store"],
        "Opportunities": ["Skatepark", "BMX Track"]
    },
}


#lookup method and term for hardcoded opportunities and competitors
SEARCH_TERM_LOOKUP: dict[str, tuple[str, str]] = {
    "Sports Store": ("nearby_type", "sporting_goods_store"),
    "Pharmacy": ("text_search", "pharmacy"),
    "Oval": ("text_search", "ovals"),
    "Rugby Club": ("text_search", "rugby club"),
    "AFL Club": ("text_search", "AFL club"),
    "Cricket Club": ("text_search", "cricket club"),
    "Martial Arts Gym": ("text_search", "martial arts gym"),
    "Boxing Gym": ("text_search", "boxing gym"),
    "Skate Shop": ("text_search", "skate shop"),
    "Surf Shop": ("text_search", "surf shop"),
    "Bike Shop": ("nearby_type", "bicycle_store"),
    "Skatepark": ("nearby_type", "skateboard_park"),
    "BMX Track": ("text_search", "BMX track"),
}

# ---------------------------------------------------------------------------
# Deterministic type/name filter — DISABLED (was too aggressive, dropping
# genuinely correct results like "Bracken Ridge Skate Plaza" from
# "Skate Shop" just because its name contains a venue-ish word). Left
# defined in case it's useful again later in a softer form, but no longer
# called from scan_location.
# ---------------------------------------------------------------------------

CATEGORY_TYPE_BUCKETS: dict[str, str] = {
    "Sports Store": "store",
    "Pharmacy": "store",
    "Skate Shop": "store",
    "Surf Shop": "store",
    "Bike Shop": "store",
    "Oval": "venue",
    "Rugby Club": "venue",
    "AFL Club": "venue",
    "Cricket Club": "venue",
    "Martial Arts Gym": "venue",
    "Boxing Gym": "venue",
    "Skatepark": "venue",
    "BMX Track": "venue",
}

STORE_TYPES = {
    "store", "sporting_goods_store", "bicycle_store", "clothing_store",
    "shoe_store", "pharmacy", "drugstore", "sportswear_store",
    "shopping_mall", "department_store",
}
VENUE_TYPES = {
    "park", "skateboard_park", "playground", "sports_complex",
    "sports_club", "sports_activity_location", "stadium", "gym",
    "fitness_center", "arena", "amusement_center", "athletic_field",
    "sports_coaching", "sports_school",
}

NAME_VENUE_WORDS = ("park", "reserve", "plaza", "skatepark", "track", "field", "oval", "club", "gym", "complex", "ground")
NAME_STORE_WORDS = ("shop", "store", "cycles", "cycle", "pharmacy", "chemist", "bikes")


def actual_bucket(place_types: list[str]) -> str | None:
    types_set = set(place_types)
    if types_set & STORE_TYPES:
        return "store"
    if types_set & VENUE_TYPES:
        return "venue"
    return None


def resolve_category(place: dict, category: str) -> Literal["keep", "drop", "ambiguous"]:
    expected_bucket = CATEGORY_TYPE_BUCKETS.get(category)
    if expected_bucket is None:
        return "ambiguous"

    actual = actual_bucket(place.get("types", []))
    if actual is not None:
        return "keep" if actual == expected_bucket else "drop"

    name_lower = place["name"].lower()
    looks_like_venue = any(w in name_lower for w in NAME_VENUE_WORDS)
    looks_like_store = any(w in name_lower for w in NAME_STORE_WORDS)

    if looks_like_venue and not looks_like_store:
        return "keep" if expected_bucket == "venue" else "drop"
    if looks_like_store and not looks_like_venue:
        return "keep" if expected_bucket == "store" else "drop"

    return "ambiguous"


def apply_type_and_name_filter(
    competitors: dict[str, list[dict]],
    opportunities: dict[str, list[dict]],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    def filter_dict(source: dict[str, list[dict]]) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        for category, items in source.items():
            kept = [item for item in items if resolve_category(item, category) != "drop"]
            result[category] = kept
        return result

    return filter_dict(competitors), filter_dict(opportunities)


PLACE_FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.location,places.types"


# Pydantic schemas for the search plan

class SearchPlanEntry(BaseModel):
    label: str
    category: Literal["opportunity", "competitor"]
    method: Literal["nearby_type", "text_search"]
    value: str
    source_product: str


class SearchPlan(BaseModel):
    business_classification: Literal["specialist", "generalist"]
    relevant_products: list[str]
    entries: list[SearchPlanEntry]


# ---------------------------------------------------------------------------
# Google Places calls
# ---------------------------------------------------------------------------

async def geocode_business(client: httpx.AsyncClient, name: str) -> dict:
    """Step 1: turn a business name into a lat/lng anchor point + Google's own type classification."""
    resp = await client.post(
        f"{BASE_URL}:searchText",
        headers={
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": PLACE_FIELD_MASK,
        },
        json={"textQuery": name},
    )
    data = resp.json()
    if "error" in data:
        raise HTTPException(status_code=502, detail=f"Places geocode failed: {data['error'].get('message')}")
    if not data.get("places"):
        raise HTTPException(status_code=404, detail=f"Could not find '{name}'")
    return data["places"][0]


class UnsupportedTypeError(Exception):
    """Raised when Google rejects an includedTypes value — usually means
    Claude hallucinated a plausible-but-nonexistent Google Places type."""
    pass


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371  # Earth's radius in km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


async def nearby_search(client: httpx.AsyncClient, lat: float, lng: float, included_type: str, radius: int):
    resp = await client.post(
        f"{BASE_URL}:searchNearby",
        headers={
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": PLACE_FIELD_MASK,
        },
        json={
            "includedTypes": [included_type],
            "maxResultCount": 20,
            "locationRestriction": {
                "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius}
            },
        },
    )
    data = resp.json()
    if "error" in data:
        message = data["error"].get("message", "")
        if "Unsupported types" in message or "invalid" in message.lower():
            raise UnsupportedTypeError(message)
        raise HTTPException(status_code=502, detail=f"Nearby Search failed: {message}")
    return data.get("places", [])


async def text_search_nearby(client: httpx.AsyncClient, query: str, lat: float, lng: float, radius: int):
    resp = await client.post(
        f"{BASE_URL}:searchText",
        headers={
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": PLACE_FIELD_MASK,
        },
        json={
            "textQuery": query,
            "locationBias": {
                "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius}
            },
        },
    )
    data = resp.json()
    if "error" in data:
        raise HTTPException(status_code=502, detail=f"Text Search failed: {data['error'].get('message')}")
    return data.get("places", [])


# ---------------------------------------------------------------------------
# Claude call — generates the dynamic search plan (currently unused, kept
# for future use if the hardcoded plan is dropped in favor of AI planning
# again)
# ---------------------------------------------------------------------------


def build_search_plan_prompt(business_name: str, google_types: list[str], product_list: list[str]) -> str:
    return f"""You are a B2B sales rep trying to find local sales opportunities and competitors
for a business called "{business_name}", which Google classifies as:
{google_types}.
This business you represent specifically sells the set of following product categories:
{product_list}

You must fill out THREE things in this exact order: business_classification,
relevant_products, and entries. Do not skip straight to entries — the first
two fields are mandatory checkpoints that entries must be built on top of.

STEP 1 — business_classification:
Decide whether "{business_name}" is a "specialist" (a narrow, niche business
that only stocks products related to one specific activity/category) or a
"generalist" (a broad business that stocks a wide variety of unrelated
product categories). Base this on the business name and its Google
classification: {google_types}.
Tip: An easy way to tell is if it has a specific product or niche in its
title. "Jim's Electrical" is likely a specialist electrical store. "Westend
Skate Shop" is likely a specialist skate shop. "Intersport Nowra" is likely
a generalist sport shop. "Sportspower Newcastle City" is likely a
generalist sport shop.

STEP 2 — relevant_products:
From the full product list {product_list}, output ONLY the subset that this
SPECIFIC business ("{business_name}") would actually stock, given your
business_classification from Step 1. You must actively exclude any product
from the input list that does not fit this specific business, even if that
product is a plausible item for the general category. This is the single
most important filtering step — every entry you generate afterwards must
trace back to something in this filtered relevant_products list, never to
the full original list if something was excluded.
Example: if the full product list is (mouthguards, skateboards, helmets)
and the business is "Westend Skate Shop" (a specialist skate shop), then
relevant_products = (skateboards, helmets). Mouthguards must be EXCLUDED
here because a dedicated skate shop does not sell mouthguards, even though
mouthguards is a real product in the input list.
If business_classification is "generalist", relevant_products will usually
be closer to the full input list, since generalist stores plausibly stock
a wider spread of categories.

STEP 3 — entries:
Generate a list of entries, where each entry is either an "opportunity" (a
nearby place type that represents a potential customer base or sales lead,
e.g. sports fields for a sports store, skateparks for a skate shop) or a
"competitor" (a nearby business that sells similar products). Every single
entry's source_product MUST be one of the items in relevant_products from
Step 2 — never use a product that Step 2 excluded, even if it seems
tempting given the entry type.

An example high level search flow in perspective of YOU the B2B sales rep is
(this is just an example, apply the same principles to all categories of
businesses and products):
1. The set of product categories the business that I represent wholesales is productSet = (mouthguards, skateboards, helmets).
2. We are trying to find opportunities and competitors for a business called "Westend Skate Shop".
3. business_classification = "specialist". relevant_products = (skateboards, helmets) — mouthguards is excluded because a dedicated skate shop wouldn't sell it.
4. We then generate the "opportunities" strictly related to both the business "Westend Skate Shop" and relevant_products. An example of a valid opportunity is "skateparks" as skate shops would sell skateboards to people who visit skateparks. An example of an invalid opportunity would be "golf courses" as nothing in relevant_products would supply to golf courses AND "Westend Skate Shop" is a skate shop which would not sell to golf courses. Opportunities are specifically nearby places that represent a relevant customer base or sales opportunities for this specific business we are targeting to sell the products we stock.
5. We then generate the "competitors" which are businesses likely to stock similar products related to the business "Westend Skate Shop" and relevant_products. The most obvious valid competitors for "Westend Skate Shop" are other Skate Shops since they are the same business. Examples of other valid competitors are "Bike Shops" as they likely stock helmets, or a more general "Sports Store" as general sport stores like rebel sport or sportspower tend to also stock skateboards and helmets. Competitors are any nearby places that sell similar products to our target business that we also stock, e.g. if we are stocking "scooters" or "helmets" as a product, and we are targeting a "skate shop" a bike shop is a competitor as bike shop's sell both of those items.

During generation, you must understand certain nuances:
- Be specific in your Entry generations. Give clear, direct Searches like "Sports Clubs" or "Skate Shops" or "Sports Store".
- Make sure searches are clearly seperated, e.g. do not search seperately for "skatepark" and "scooterpark" and "indoor skatepark" as they are practically the same places in real life, search only for the most relevant being "skatepark". However, "Skatepark" and "BMX Track" are completely different places and should be different Entries in the example event you are searching for opportunities for a "Bike Shop" that sells Helmets.
- Searching "opportunities" and "competitors" that are actually real. E.g. If you're wholesaling a product "Scooters", dedicated "Scooter Shops" don't really exist. "Skate Shops" and "Bike shops" and "Sports Stores" sell scooters. Similarly "skateboard clubs" aren't a real thing practically in real life that people visit so just "Skateparks" is fine.
- Don't search places that are practically the exact same, e.g. "sporting goods stores" and "sports stores", these are NOT seperate entries, just use a single Entry "sports store"
- With generalist businesses (business_classification = "generalist"), each relevant product MUST generate AT LEAST 2-3 separate, concrete opportunity entries representing genuinely distinct real-world venue types — never settle for one vague catch-all category when more specific real venues exist. For example, for "mouthguards" do NOT stop at a single generic "Sports Clubs" entry — instead generate the specific, distinct venue types separately, such as: "Ovals" (the common Australian/local term for footy, rugby, and cricket fields — a strong, concrete mouthguard/protective-gear opportunity in its own right and always worth considering for any contact-sport-adjacent product), "Football Clubs", "Martial Arts Studios", "Basketball Courts", etc. Always think in terms of the SPECIFIC local, real-world venue a customer for that product would actually go to, not an umbrella term that quietly absorbs several different venue types into one entry.
- With specialist businesses (business_classification = "specialist"), tighten up the Entries and make them more hyper-relevant to the stores' specialty. E.g. For a dedicated Skate Store, choose skate adjacent "opportunity" Entries only, even if that means fewer total entries.

Never include schools, school grounds, or school sporting facilities as an
opportunity or competitor, under any circumstances — even if a search term
like "oval" or "athletic_field" would technically surface one. Schools are
not viable B2B sales leads for this purpose and must be excluded from every
category, regardless of product type.

For EACH entry, decide the best way to search Google Places:
- "nearby_type": use this ONLY if the category matches one of these EXACT,
  verified Google Places API type strings — do not use any type string not
  in this list, even if it seems plausible:
  arena, athletic_field, fishing_charter, fishing_pier, fishing_pond,
  fitness_center, golf_course, gym, ice_skating_rink, indoor_golf_course,
  playground, race_course, ski_resort, sports_activity_location,
  sports_club, sports_coaching, sports_complex, sports_school, stadium,
  swimming_pool, tennis_court, skateboard_park, bowling_alley,
  amusement_center, dog_park, cycling_park, go_karting_venue, hiking_area,
  miniature_golf_course, paintball_center, water_park, sporting_goods_store,
  bicycle_store, shoe_store, clothing_store, sportswear_store
  Set "value" to the exact string from this list, character for character.
- "text_search": use this for ANY category not covered by the exact list
  above, or if a plain-language local search term would return more
  accurate results than Google's built-in category (e.g. Australian sports
  fields are commonly called "ovals" locally, and Google's "athletic_field"
  type returns too many irrelevant results like golf courses and school
  grounds). Set "value" to the exact search phrase to use.
When in doubt, or if the category isn't an exact match to the list above,
always use "text_search" — it is the safer default and never fails due to
an invalid type string.

Finally for each entry in "entries", the "value" is the actual entry we are searching for competitors and opportunities.

Regardless on whatever the "value" is, the "label" should be a human readable and straightforward version of it. Sometimes the "label" needs to be different text to the "value", other times it can be the same. For example:
"value" = "bicycle_store", "label" = "Bike Shop",
"value" = "skateboard_park", "label" = "Skateparks",
"value" = "Skate Shops", "label" = "Skate Shops",
"value" = "Sports Stores", "label" = "Sports Stores",

additionally:
- place in brackets () in the label at the end next to the Entry name, the items you believe these "competitors" will stock that made you come to the decision to include this Entry as a competitor seperated by an " & " if there's more than 1
- place in brackets () in the label at the end next to the Entry name, the items you believe these "opportunities" will purchase that made you come to the decision to include this Entry as an "opportunity" seperated by an " & " if there's more than 1

Reminder: every bracketed item in every label must come from relevant_products
(Step 2), never from an excluded product.
"""


SEARCH_PLAN_TOOL = {
    "name": "generate_search_plan",
    "description": "Return the search plan entries for finding local B2B opportunities and competitors.",
    "input_schema": {
        "type": "object",
        "properties": {
            "business_classification": {
                "type": "string",
                "enum": ["specialist", "generalist"],
                "description": "Whether this business specializes in a narrow product niche (e.g. a dedicated skate shop) or sells a broad general variety (e.g. a general sporting goods store). Must be filled out first, before relevant_products and entries."
            },
            "relevant_products": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The filtered subset of the input product list that this SPECIFIC business would actually stock, based on its name and business_classification. Exclude any product from the input list that doesn't fit this business, even if it's a plausible product for the general category. Must be filled out before entries, and every entry's source_product must come from this list."
            },
            "entries": {
                "type": "array",
                "description": "For generalist businesses, generate multiple distinct entries per relevant product where multiple real, separately-searchable venue types exist. Do not consolidate several genuinely different venue types into one generic umbrella entry.",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "category": {"type": "string", "enum": ["opportunity", "competitor"]},
                        "method": {"type": "string", "enum": ["nearby_type", "text_search"]},
                        "value": {"type": "string"},
                        "source_product": {
                            "type": "string",
                            "description": "Must be one of the products listed in relevant_products, never an excluded product."
                        },
                    },
                    "required": ["label", "category", "method", "value", "source_product"],
                },
            }
        },
        "required": ["business_classification", "relevant_products", "entries"],
    },
}


# ---------------------------------------------------------------------------
# Helpers for parsing/merging labels of the form "Title (detail & detail)"
# ---------------------------------------------------------------------------

_LABEL_RE = re.compile(r'^(.+?)\s*\(([\s\S]*)\)\s*$')


def _split_label(label: str) -> tuple[str, str | None]:
    match = _LABEL_RE.match(label)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return label.strip(), None


def _merge_details(labels: list[str]) -> list[str]:
    details: list[str] = []
    for label in labels:
        _, detail = _split_label(label)
        if detail:
            details.extend(d.strip() for d in detail.split('&'))
    return list(dict.fromkeys(d for d in details if d))


def merge_duplicate_entries(entries: list[SearchPlanEntry]) -> list[SearchPlanEntry]:
    merged: dict[tuple[str, str, str], SearchPlanEntry] = {}
    order: list[tuple[str, str, str]] = []

    for entry in entries:
        key = (entry.category, entry.method, entry.value.strip().lower())

        if key not in merged:
            merged[key] = entry
            order.append(key)
            continue

        existing = merged[key]
        existing_title, _ = _split_label(existing.label)
        deduped_details = _merge_details([existing.label, entry.label])

        new_label = existing_title if not deduped_details else f"{existing_title} ({' & '.join(deduped_details)})"
        merged[key] = existing.model_copy(update={"label": new_label})

    return [merged[k] for k in order]


def get_search_plan(business_name: str, google_types: list[str], product_list: list[str]) -> SearchPlan:
    prompt = build_search_plan_prompt(business_name, google_types, product_list)

    message = claude_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        tools=[SEARCH_PLAN_TOOL],
        tool_choice={"type": "tool", "name": "generate_search_plan"},
        messages=[{"role": "user", "content": prompt}],
    )

    tool_use_block = next((b for b in message.content if b.type == "tool_use"), None)
    if tool_use_block is None:
        raise HTTPException(status_code=502, detail="Claude did not return a valid search plan")

    try:
        plan = SearchPlan.model_validate(tool_use_block.input)
    except ValidationError as e:
        raise HTTPException(status_code=502, detail=f"Search plan validation failed: {e}")

    plan.entries = [
        entry for entry in plan.entries
        if "school" not in entry.label.lower() and "school" not in entry.value.lower()
    ]

    relevant_lower = {p.strip().lower() for p in plan.relevant_products}
    plan.entries = [
        entry for entry in plan.entries
        if entry.source_product.strip().lower() in relevant_lower
    ]

    plan.entries = merge_duplicate_entries(plan.entries)

    print(f"[classification] {plan.business_classification!r}")
    print(f"[relevant_products] {plan.relevant_products!r}")
    for entry in plan.entries:
        print(f"[{entry.category}] {entry.label!r} -> method={entry.method}, value={entry.value!r}, source_product={entry.source_product!r}")

    return plan


def merge_overlapping_result_entries(
    entries: dict[str, list[dict]],
    overlap_threshold: float = 0.4,
) -> dict[str, list[dict]]:
    labels = list(entries.keys())
    n = len(labels)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    def keyset(label: str) -> set[str]:
        return {item.get("place_id") or item["name"].strip().lower() for item in entries[label]}

    keysets = [keyset(label) for label in labels]

    for i in range(n):
        for j in range(i + 1, n):
            a, b = keysets[i], keysets[j]
            if not a or not b:
                continue
            overlap = len(a & b)
            smaller = min(len(a), len(b))
            if smaller and (overlap / smaller) >= overlap_threshold:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    new_entries: dict[str, list[dict]] = {}
    for group in groups.values():
        if len(group) == 1:
            idx = group[0]
            new_entries[labels[idx]] = entries[labels[idx]]
            continue

        group_sorted = sorted(group, key=lambda i: len(entries[labels[i]]), reverse=True)
        winner_idx = group_sorted[0]
        winner_title, _ = _split_label(labels[winner_idx])
        deduped_details = _merge_details([labels[idx] for idx in group_sorted])

        merged_label = winner_title if not deduped_details else f"{winner_title} ({' & '.join(deduped_details)})"

        seen_keys: set[str] = set()
        merged_items: list[dict] = []
        for idx in group:
            for item in entries[labels[idx]]:
                key = item.get("place_id") or item["name"].strip().lower()
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                merged_items.append(item)
        merged_items.sort(key=lambda r: r["distance_km"])

        new_entries[merged_label] = merged_items

    return new_entries


# ---------------------------------------------------------------------------
# Claude call — global category re-assignment
# ---------------------------------------------------------------------------
#
# This works on the UNIQUE set of places found across every category
# combined. Each unique place is shown once (with which categories it
# originally matched, for context). Crucially, Claude is only asked to
# report EXCEPTIONS — removals, and items whose category needs to change —
# rather than an explicit assignment for every single item. Any item Claude
# doesn't mention keeps its original (single) category untouched.
#
# Why: the old version made Claude output one assignment object per item.
# For a generalist business with a dozen+ search categories that's easily
# 100+ items, which blew past max_tokens and got the response truncated
# mid-JSON — silently producing an empty assignments list and wiping every
# result. Only asking for exceptions keeps the output small (most items are
# fine where they are) and is far less likely to ever hit the token limit.
# An explicit stop_reason check is also a safety net if it ever does.

def build_reassignment_tool(valid_categories: list[str]) -> dict:
    return {
        "name": "assign_categories",
        "description": (
            "Report only the exceptions. 'removals' = items irrelevant to every "
            "category. 'moves' = items whose single best category must be set "
            "explicitly — this is REQUIRED for every [MULTI] item (found under "
            "more than one category) and OPTIONAL for single-category items (only "
            "include if it should move out of the category it was found under). "
            "Any item you don't mention in either list is left exactly where it "
            "was found."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "removals": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Indices of items irrelevant to every category — drop entirely.",
                },
                "moves": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_index": {"type": "integer"},
                            "category": {"type": "string", "enum": valid_categories},
                        },
                        "required": ["item_index", "category"],
                    },
                    "description": "The single best category for an item. Required for every [MULTI] item.",
                },
            },
            "required": ["removals", "moves"],
        },
    }


def build_reassignment_prompt(business_name: str, valid_categories: list[str], items: list[dict]) -> str:
    lines = [
        f'Clean up local place search results for "{business_name}".',
        'Categories: ' + ', '.join(valid_categories),
        '',
        'Places found (each listed once):',
    ]
    for idx, item in enumerate(items):
        found = ', '.join(item["found_in"])
        marker = ' [MULTI]' if len(item["found_in"]) > 1 else ''
        lines.append(f'[{idx}] {item["name"]} — found under: {found}{marker}')
    lines.append('')
    lines.append(
        "Rules:\n"
        "- Totally irrelevant to every category (e.g. a firearms store) -> add its index to removals.\n"
        "- Every [MULTI] item MUST appear in moves with the single category it fits best "
        "(dropped from the rest automatically).\n"
        "- A single-category item only needs to appear in moves if it's a clearly better fit "
        "elsewhere (e.g. a surf shop found under 'Skate Shop' should move to 'Surf Shop').\n"
        "- If genuinely torn between two categories, default to 'Sports Store'.\n"
        "- Don't mention items that are fine staying exactly where they are."
    )
    return "\n".join(lines)


def apply_relevance_filter(
    business_name: str,
    opportunities: dict[str, list[dict]],
    competitors: dict[str, list[dict]],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    label_side: dict[str, str] = {}
    for label in competitors:
        label_side[label] = "competitor"
    for label in opportunities:
        label_side[label] = "opportunity"

    if not label_side:
        return opportunities, competitors

    # Merge every place into a single de-duplicated list, remembering
    # which category label(s) it was originally found under.
    unique_items: dict[str, dict] = {}
    order: list[str] = []

    def collect(source: dict[str, list[dict]]) -> None:
        for label, items in source.items():
            for item in items:
                key = item.get("place_id") or item["name"].strip().lower()
                if key not in unique_items:
                    unique_items[key] = {**item, "found_in": []}
                    order.append(key)
                if label not in unique_items[key]["found_in"]:
                    unique_items[key]["found_in"].append(label)

    collect(competitors)
    collect(opportunities)

    if not order:
        return opportunities, competitors

    valid_categories = list(label_side.keys())
    items_list = [unique_items[k] for k in order]
    prompt = build_reassignment_prompt(business_name, valid_categories, items_list)
    tool = build_reassignment_tool(valid_categories)

    try:
        message = claude_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            tools=[tool],
            tool_choice={"type": "tool", "name": "assign_categories"},
            messages=[{"role": "user", "content": prompt}],
        )

        if message.stop_reason == "max_tokens":
            # Response got cut off mid-JSON — do NOT trust whatever partial
            # input came through, it will under-report and wipe results.
            raise ValueError("relevance filter response was truncated (max_tokens)")

        tool_use_block = next((b for b in message.content if b.type == "tool_use"), None)
        if tool_use_block is None:
            raise ValueError("Claude did not return a tool_use block")

        removals = {int(i) for i in tool_use_block.input.get("removals", [])}

        moves: dict[int, str] = {}
        for m in tool_use_block.input.get("moves", []):
            try:
                moves[int(m["item_index"])] = m["category"]
            except (KeyError, TypeError, ValueError):
                continue
    except Exception as e:
        # Fail-open: on any failure, keep everything exactly as it was
        # rather than losing the whole scan over a filtering step.
        print(f"[relevance filter] failed, skipping filter: {e!r}")
        return opportunities, competitors

    new_competitors: dict[str, list[dict]] = {label: [] for label in competitors}
    new_opportunities: dict[str, list[dict]] = {label: [] for label in opportunities}

    for idx, key in enumerate(order):
        if idx in removals:
            continue

        item = unique_items[key]
        clean_item = {k: v for k, v in item.items() if k != "found_in"}
        found_in = item["found_in"]

        if idx in moves:
            category = moves[idx]
            if category not in label_side:
                continue
        elif len(found_in) > 1:
            # A [MULTI] item Claude didn't resolve as instructed — fail-safe
            # default per the original spec, rather than dropping or
            # duplicating it across every category it was found under.
            category = "Sports Store" if "Sports Store" in label_side else found_in[0]
        else:
            category = found_in[0]

        if label_side[category] == "competitor":
            new_competitors[category].append(clean_item)
        else:
            new_opportunities[category].append(clean_item)

    for label in new_competitors:
        new_competitors[label].sort(key=lambda r: r["distance_km"])
    for label in new_opportunities:
        new_opportunities[label].sort(key=lambda r: r["distance_km"])

    return new_opportunities, new_competitors


# ---------------------------------------------------------------------------
# Deterministic post-filter — name-keyword category override
# ---------------------------------------------------------------------------
#
# The AI relevance filter is good but not infallible. Two concrete cases
# this covers, purely on name-substring matching, no AI judgement call:
#
# 1. An entry like "99 Bikes Ryde" sitting in "Sports Store" instead of
#    "Bike Shop" — competitor/shop side.
# 2. An entry like "Jim Lawrie Oval" getting swallowed into "Rugby Club"
#    instead of "Oval" because it also matched that search — opportunity
#    side. Since "oval" doesn't collide with any other opportunity
#    category's naming pattern, a straight substring swap is reliable here.
#
# Competitor-side and opportunity-side keyword lists are applied
# independently (never cross the two), because unlike "oval", keywords
# like "bike" or "skate" DO show up constantly in venue names too (e.g.
# "Boronia Bike Track", "Parramatta Skate Park") — mixing sides would wrongly
# reclassify skateparks as skate shops. See the bike/skate/surf/sport list
# below, which stays competitor-only for that reason.

NAME_KEYWORD_CATEGORY_TARGETS: list[tuple[tuple[str, ...], str]] = [
    (("bike", "bicycle"), "Bike Shop"),
    (("skate",), "Skate Shop"),
    (("surf",), "Surf Shop"),
    (("sport",), "Sports Store"),
]

NAME_KEYWORD_CATEGORY_TARGETS_OPPORTUNITIES: list[tuple[tuple[str, ...], str]] = [
    (("oval",), "Oval"),
]


def _apply_keyword_targets(
    source: dict[str, list[dict]],
    keyword_targets: list[tuple[tuple[str, ...], str]],
) -> dict[str, list[dict]]:
    valid_labels = set(source.keys())
    targets = [(kws, cat) for kws, cat in keyword_targets if cat in valid_labels]

    new_source: dict[str, list[dict]] = {label: [] for label in source}
    seen_per_category: dict[str, set[str]] = {label: set() for label in source}

    for label, items in source.items():
        for item in items:
            name_lower = item["name"].strip().lower()
            key = item.get("place_id") or name_lower

            forced_category = None
            for keywords, cat in targets:
                if any(kw in name_lower for kw in keywords):
                    forced_category = cat
                    break

            destination = forced_category if forced_category else label

            if key in seen_per_category[destination]:
                continue
            seen_per_category[destination].add(key)
            new_source[destination].append(item)

    for label in new_source:
        new_source[label].sort(key=lambda r: r["distance_km"])

    return new_source


def apply_name_keyword_override(
    opportunities: dict[str, list[dict]],
    competitors: dict[str, list[dict]],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    new_competitors = _apply_keyword_targets(competitors, NAME_KEYWORD_CATEGORY_TARGETS)
    new_opportunities = _apply_keyword_targets(opportunities, NAME_KEYWORD_CATEGORY_TARGETS_OPPORTUNITIES)
    return new_opportunities, new_competitors

def prioritize_skateparks(opportunities: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Force 'Skatepark' to the front of the opportunities dict if present.
    Leaves everything else in whatever order it was already in."""
    if "Skatepark" not in opportunities:
        return opportunities
    return {"Skatepark": opportunities["Skatepark"], **{k: v for k, v in opportunities.items() if k != "Skatepark"}}


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------

@app.get("/api/scan")
async def scan_location(
    business_name: str = Query(..., min_length=1),
    products: str = Query(default=""),
):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="Places API key not configured")
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Anthropic API key not configured")

    product_list = [p.strip() for p in products.split(",") if p.strip()]
    if not product_list:
        raise HTTPException(status_code=400, detail="At least one product category is required")

    async with httpx.AsyncClient() as client:
        anchor = await geocode_business(client, business_name)
        lat = anchor["location"]["latitude"]
        lng = anchor["location"]["longitude"]
        anchor_id = anchor.get("id")

        product_stock = set()

        lowBusinessName = anchor["displayName"]["text"].lower()

        #add searched business name first  to get maps full same, e.g. someone searched "slam factory" but the google result gives full name "slam factory indoor skatepark", skate keyword needed for below
        if any(w in lowBusinessName for w in ("bike", "bicycle")):
            product_stock.add("Skate_Scooter_Bike_Helmets")
            product_stock.add("Knee_ElbowPads")
            product_stock.add("Scooters")

        if "skate" in lowBusinessName:
            product_stock.add("Skateboards")
            product_stock.add("Scooters")
            product_stock.add("Knee_ElbowPads")
            product_stock.add("Skate_Scooter_Bike_Helmets")

        if "scooter" in lowBusinessName:
            product_stock.add("Skateboards")
            product_stock.add("Scooters")
            product_stock.add("Knee_ElbowPads")
            product_stock.add("Skate_Scooter_Bike_Helmets")

        if "surf" in lowBusinessName:
            product_stock.add("Skateboards")
            product_stock.add("Scooters")
            product_stock.add("Knee_ElbowPads")
            product_stock.add("Skate_Scooter_Bike_Helmets")

        if "snow" in lowBusinessName:
            product_stock.add("Skateboards")
            product_stock.add("Knee_ElbowPads")
            product_stock.add("Skate_Scooter_Bike_Helmets")

        if (any(w in lowBusinessName for w in ("sport", "fitness", "rebel", "anaconda"))) and not (any(w in lowBusinessName for w in ("bike", "bicycle", "skate", "scooter", "surf", "snow"))):
            product_stock.add("Skateboards")
            product_stock.add("Scooters")
            product_stock.add("Knee_ElbowPads")
            product_stock.add("Skate_Scooter_Bike_Helmets")
            product_stock.add("Mouthguards")

        if not any(w in lowBusinessName for w in ("bike", "bicycle", "skate", "scooter", "surf", "snow", "sport", "fitness", "rebel", "anaconda")):
            product_stock.add("Skateboards")
            product_stock.add("Scooters")
            product_stock.add("Knee_ElbowPads")
            product_stock.add("Skate_Scooter_Bike_Helmets")
            product_stock.add("Mouthguards")

        competitors: dict[str, list[dict]] = {}
        opportunities: dict[str, list[dict]] = {}

        for product in product_stock:
            competitorsCategories = HARDCODED_SEARCHES[product]["Competitors"]
            opportunityCategories = HARDCODED_SEARCHES[product]["Opportunities"]

            for competitorCategory in competitorsCategories:
                method, searchTerm = SEARCH_TERM_LOOKUP[competitorCategory]

                if method == "nearby_type":
                    try:
                        raw_competitors = await nearby_search(client, lat, lng, searchTerm, COMPETITOR_RADIUS_METERS)
                    except UnsupportedTypeError:
                        fallback_query = searchTerm.replace("_", " ")
                        raw_competitors = await text_search_nearby(client, fallback_query, lat, lng, COMPETITOR_RADIUS_METERS)
                else:
                    raw_competitors = await text_search_nearby(client, searchTerm, lat, lng, COMPETITOR_RADIUS_METERS)

                competitor_radius_km = COMPETITOR_RADIUS_METERS / 1000
                category_results = []
                for comp in raw_competitors:
                    if comp.get("id") == anchor_id:
                        continue
                    comp_lat = comp["location"]["latitude"]
                    comp_lng = comp["location"]["longitude"]
                    distance_km = round(haversine_km(lat, lng, comp_lat, comp_lng), 1)
                    if distance_km > competitor_radius_km:
                        continue
                    category_results.append({
                        "name": comp["displayName"]["text"],
                        "distance_km": distance_km,
                        "place_id": comp.get("id", ""),
                        "types": comp.get("types", []),
                    })

                category_results.sort(key=lambda r: r["distance_km"])

                seen = set()
                deduped = []
                for r in category_results:
                    key = r["name"].strip().lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(r)

                if competitorCategory in competitors:
                    existing_keys = {r["name"].strip().lower() for r in competitors[competitorCategory]}
                    competitors[competitorCategory].extend(r for r in deduped if r["name"].strip().lower() not in existing_keys)
                else:
                    competitors[competitorCategory] = deduped

            for opportunityCategory in opportunityCategories:
                method, searchTerm = SEARCH_TERM_LOOKUP[opportunityCategory]

                if method == "nearby_type":
                    try:
                        raw_opportunities = await nearby_search(client, lat, lng, searchTerm, OPPORTUNITY_RADIUS_METERS)
                    except UnsupportedTypeError:
                        fallback_query = searchTerm.replace("_", " ")
                        raw_opportunities = await text_search_nearby(client, fallback_query, lat, lng, OPPORTUNITY_RADIUS_METERS)
                else:
                    raw_opportunities = await text_search_nearby(client, searchTerm, lat, lng, OPPORTUNITY_RADIUS_METERS)

                opportunity_radius_km = OPPORTUNITY_RADIUS_METERS / 1000
                category_results = []
                for opp in raw_opportunities:
                    if opp.get("id") == anchor_id:
                        continue
                    opp_lat = opp["location"]["latitude"]
                    opp_lng = opp["location"]["longitude"]
                    distance_km = round(haversine_km(lat, lng, opp_lat, opp_lng), 1)
                    if distance_km > opportunity_radius_km:
                        continue
                    category_results.append({
                        "name": opp["displayName"]["text"],
                        "distance_km": distance_km,
                        "place_id": opp.get("id", ""),
                        "types": opp.get("types", []),
                    })

                category_results.sort(key=lambda r: r["distance_km"])

                seen = set()
                deduped = []
                for r in category_results:
                    key = r["name"].strip().lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(r)

                if opportunityCategory in opportunities:
                    existing_keys = {r["name"].strip().lower() for r in opportunities[opportunityCategory]}
                    opportunities[opportunityCategory].extend(r for r in deduped if r["name"].strip().lower() not in existing_keys)
                else:
                    opportunities[opportunityCategory] = deduped

        # Deterministic type/name filter — disabled, was too aggressive.
        # competitors, opportunities = apply_type_and_name_filter(competitors, opportunities)

        # Claude-based global category re-assignment — can move a place
        # between categories, remove it entirely, or leave it as-is.
        opportunities, competitors = apply_relevance_filter(business_name, opportunities, competitors)

        # Deterministic safety net: force entries whose name contains a
        # bike/skate/surf/sport keyword into the matching category,
        # overriding whatever the AI filter decided.
        opportunities, competitors = apply_name_keyword_override(opportunities, competitors)

        # Always surface Skateparks first in the opportunities list, when present.
        opportunities = prioritize_skateparks(opportunities)

    return {
        "anchor": {
            "name": anchor["displayName"]["text"],
            "address": anchor.get("formattedAddress"),
        },
        "products_received": product_list,
        "opportunities": opportunities,
        "competitors": competitors,
    }