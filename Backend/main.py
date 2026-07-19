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

PLACE_FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.location"


# ---------------------------------------------------------------------------
# Pydantic schemas for the search plan
# ---------------------------------------------------------------------------

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
            "X-Goog-FieldMask": PLACE_FIELD_MASK + ",places.types",
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
            "maxResultCount": 10,
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
# Claude call — generates the dynamic search plan
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


# ---------------------------------------------------------------------------
# NEW: collapse plan entries that resolve to an identical Google Places
# search, so the same real-world venue type never gets shown twice under
# two separately-labelled dropdowns.
# ---------------------------------------------------------------------------
#
# Claude sometimes assigns two different source_products (e.g. "Skateboards"
# and "Scooters") to what is, in Google's eyes, the exact same search
# (method="nearby_type", value="skateboard_park"). Since each entry's label
# gets a different bracketed suffix per source_product (e.g. "Skateparks
# (Skateboards)" vs "Skateparks (Scooters)"), the two entries end up as
# different dict keys downstream and both get shown — as an identical
# duplicate dropdown, since the underlying search and results are the same.
# This merges any entries with the same (category, method, value) into one,
# combining their bracketed product lists rather than silently dropping one.
# NOTE: this only catches EXACT search duplicates. For entries that use
# genuinely different search methods/values but still return mostly the
# same real-world places (e.g. "Skateparks" vs "Scooter Parks" — see
# merge_overlapping_result_entries below), this alone isn't enough.

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

    # Merge entries that resolve to an identical Google Places search
    # (same category/method/value) so they never show up as a duplicate
    # dropdown with identical results under two different labels.
    plan.entries = merge_duplicate_entries(plan.entries)

    # TEMP DEBUG — remove once you're done inspecting
    print(f"[classification] {plan.business_classification!r}")
    print(f"[relevant_products] {plan.relevant_products!r}")
    for entry in plan.entries:
        print(f"[{entry.category}] {entry.label!r} -> method={entry.method}, value={entry.value!r}, source_product={entry.source_product!r}")

    return plan


# ---------------------------------------------------------------------------
# NEW: merge entries whose RESULTS overlap heavily, even if their search
# method/value were different.
# ---------------------------------------------------------------------------
#
# This catches cases like "Skateparks" (nearby_type=skateboard_park) and
# "Scooter Parks" (a separate text_search) which are different searches on
# paper, but in practice return mostly the same physical parks — because a
# skatepark and a "scooter park" are usually the same real-world venue.
# Rather than trying to predict this from the search plan alone (which
# hasn't reliably worked, even with explicit prompt instructions telling
# Claude not to split these), this runs AFTER the actual Google results are
# in and merges any two same-category entries whose result sets overlap by
# at least `overlap_threshold` of the smaller entry's item count.

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

        # Pick the label of whichever entry in the group has the most
        # items as the "winning" title — usually the broader/more
        # complete search — and fold every other label's bracketed
        # detail into it rather than discarding it.
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
# NEW: Claude call — post-search relevance filter + cross-entry dedup
# ---------------------------------------------------------------------------
#
# This runs AFTER all Google Places results have been gathered. It sees
# every entry's results side-by-side, so it can:
#   1. Drop items that are obviously irrelevant to their entry's label
#      (e.g. a firearms store under "Sports Stores").
#   2. Catch the same business appearing under multiple entries (e.g. a
#      bike shop showing up under both "Bike Shops" AND "Sports Stores")
#      and keep it in ONLY the single most specific entry.
#
# We deliberately send indices, not full place_ids, in the prompt — it's
# far cheaper token-wise, and we map the indices back to the real result
# dicts in Python afterwards, so nothing relies on Claude echoing IDs back
# correctly.

FILTER_TOOL = {
    "name": "filter_search_results",
    "description": "Decide which results to keep in each entry, removing irrelevant results and cross-entry duplicates.",
    "input_schema": {
        "type": "object",
        "properties": {
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entry_index": {
                            "type": "integer",
                            "description": "The 0-based index of the entry, matching the ENTRY numbering given in the prompt."
                        },
                        "keep_item_indices": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "0-based indices of items (local to this entry's own [n] numbering) to KEEP. Omit an index if that item should be removed — either because it's irrelevant to this entry, or because it's a duplicate being kept in a different, better-fitting entry instead."
                        }
                    },
                    "required": ["entry_index", "keep_item_indices"],
                },
                "description": "Must include exactly one object per entry index, covering every entry from 0 to the last one shown in the prompt, even if keep_item_indices is empty."
            }
        },
        "required": ["entries"],
    },
}


def build_filter_prompt(business_name: str, indexed_entries: list[dict]) -> str:
    lines = [
        f'You are reviewing a set of nearby business search results gathered for '
        f'"{business_name}", a real business we represent for B2B sales purposes.',
        'Below is a numbered list of ENTRIES (search categories). Each entry contains '
        'a numbered list of ITEMS (real businesses found nearby under that search).',
        '',
    ]

    for idx, entry in enumerate(indexed_entries):
        lines.append(f'ENTRY {idx} — "{entry["label"]}" ({entry["category"]}):')
        if not entry["items"]:
            lines.append('  (no items)')
        for item_idx, item in enumerate(entry["items"]):
            lines.append(f'  [{item_idx}] {item["name"]}')
        lines.append('')

    lines.append("""Your job has two parts:

1. RELEVANCE FILTERING: For each entry, review its items and decide which ones
are genuinely relevant to that entry's label and category, given the business
we represent. Remove items that are obviously, clearly unrelated to the entry
— for example a firearms store or a lawn bowls pro shop showing up under a
general "Sports Stores" entry, or a swimming pool supplier showing up under a
"Skate Shops" entry. If you are UNSURE whether an item is relevant — for
example a surf shop showing up under "Skate Shops", where surf shops commonly
also sell skateboards — always err on the side of KEEPING the item rather
than removing it. Only remove items that are clearly, obviously unrelated.

2. CROSS-ENTRY DEDUPLICATION: The same real business may appear as an item in
more than one entry (e.g. a bike shop that also gets picked up by a more
general search like "Sports Stores"). If the same business name appears in
more than one entry, it should only be kept in the SINGLE entry that best and
most specifically matches what that business actually is (e.g. keep a
dedicated bike shop under "Bike Shops", not also under a more generic "Sports
Stores" entry). Remove it from every other, less specific entry it appears in.

Return, for EVERY entry index from 0 to the last one shown above, the list of
item indices (using the local [n] numbering shown for that entry) that should
be KEPT. Omit an item's index if it should be removed for either reason
above. It is fine for an entry's keep_item_indices to be an empty list if
every item in it should be removed.""")

    return "\n".join(lines)


def apply_relevance_filter(
    business_name: str,
    opportunities: dict[str, list[dict]],
    competitors: dict[str, list[dict]],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    # Build one flat, ordered list of entries so we can map Claude's
    # indices back to the right dict + label afterwards.
    indexed_entries = []
    for label, items in competitors.items():
        indexed_entries.append({"origin": "competitor", "label": label, "category": "competitor", "items": items})
    for label, items in opportunities.items():
        indexed_entries.append({"origin": "opportunity", "label": label, "category": "opportunity", "items": items})

    # Nothing to filter — skip the extra API call entirely.
    if not indexed_entries or not any(e["items"] for e in indexed_entries):
        return opportunities, competitors

    prompt = build_filter_prompt(business_name, indexed_entries)

    try:
        message = claude_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            tools=[FILTER_TOOL],
            tool_choice={"type": "tool", "name": "filter_search_results"},
            messages=[{"role": "user", "content": prompt}],
        )
        tool_use_block = next((b for b in message.content if b.type == "tool_use"), None)
        if tool_use_block is None:
            raise ValueError("Claude did not return a tool_use block")
        result_entries = tool_use_block.input.get("entries", [])
    except Exception as e:
        # Fail-open: if this call breaks for any reason (network error,
        # bad response, rate limit, etc.), return the ORIGINAL unfiltered
        # results rather than losing the whole scan over a filtering step.
        print(f"[relevance filter] failed, skipping filter: {e!r}")
        return opportunities, competitors

    keep_map: dict[int, set[int]] = {}
    for r in result_entries:
        try:
            entry_index = int(r["entry_index"])
            keep_indices = {int(i) for i in r.get("keep_item_indices", [])}
            keep_map[entry_index] = keep_indices
        except (KeyError, TypeError, ValueError):
            continue

    new_opportunities: dict[str, list[dict]] = {}
    new_competitors: dict[str, list[dict]] = {}

    for idx, entry in enumerate(indexed_entries):
        keep_indices = keep_map.get(idx)
        if keep_indices is None:
            # This entry was missing from Claude's response — fail-open
            # and keep everything for it, rather than silently dropping
            # an entire entry's results due to an incomplete response.
            filtered_items = entry["items"]
        else:
            filtered_items = [
                item for item_idx, item in enumerate(entry["items"])
                if item_idx in keep_indices
            ]

        removed_count = len(entry["items"]) - len(filtered_items)
        if removed_count:
            print(f"[relevance filter] {entry['label']!r}: removed {removed_count} item(s)")

        if entry["origin"] == "competitor":
            new_competitors[entry["label"]] = filtered_items
        else:
            new_opportunities[entry["label"]] = filtered_items

    return new_opportunities, new_competitors


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
        google_types = anchor.get("types", [])
        anchor_id = anchor.get("id")

        plan = get_search_plan(business_name, google_types, product_list)

        opportunities: dict[str, list[dict]] = {}
        competitors: dict[str, list[dict]] = {}

        for entry in plan.entries:
            radius = COMPETITOR_RADIUS_METERS if entry.category == "competitor" else OPPORTUNITY_RADIUS_METERS

            if entry.method == "nearby_type":
                try:
                    raw_places = await nearby_search(client, lat, lng, entry.value, radius)
                except UnsupportedTypeError:
                    fallback_query = entry.value.replace("_", " ")
                    raw_places = await text_search_nearby(client, fallback_query, lat, lng, radius)
            else:
                raw_places = await text_search_nearby(client, entry.value, lat, lng, radius)

            radius_km = radius / 1000

            results_with_distance = []
            seen_names = set()
            for p in raw_places:
                if p.get("id") == anchor_id:
                    continue

                p_lat = p["location"]["latitude"]
                p_lng = p["location"]["longitude"]
                distance_km = round(haversine_km(lat, lng, p_lat, p_lng), 1)

                if distance_km > radius_km:
                    continue

                name = p["displayName"]["text"]
                place_id = p.get("id", "")
                results_with_distance.append({
                    "name": name,
                    "distance_km": distance_km,
                    "place_id": place_id,
                })

            results_with_distance.sort(key=lambda r: r["distance_km"])

            deduped_results = []
            for r in results_with_distance:
                key = r["name"].strip().lower()
                if key in seen_names:
                    continue
                seen_names.add(key)
                deduped_results.append(r)

            if entry.category == "opportunity":
                opportunities[entry.label] = deduped_results
            else:
                competitors[entry.label] = deduped_results

        # NEW: merge entries whose actual results overlap heavily, even if
        # their search method/value on paper were different (e.g.
        # "Skateparks" and "Scooter Parks" usually returning the same
        # physical parks). This runs before the relevance filter so we
        # don't waste a filter pass on entries we're about to merge anyway.
        opportunities = merge_overlapping_result_entries(opportunities)
        competitors = merge_overlapping_result_entries(competitors)

        # Relevance + cross-entry dedup pass, using everything we've
        # gathered so far across all entries.
        opportunities, competitors = apply_relevance_filter(business_name, opportunities, competitors)

    return {
        "anchor": {
            "name": anchor["displayName"]["text"],
            "address": anchor.get("formattedAddress"),
        },
        "products_received": product_list,
        "opportunities": opportunities,
        "competitors": competitors,
    }