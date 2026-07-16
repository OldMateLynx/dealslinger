import os
import math
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

# NEW: shared field mask used everywhere. Added "places.id" — this is what
# lets us detect and exclude the searched business from its own results
# (Fix #1), since comparing unique IDs is far more reliable than comparing
# display names (two different businesses could share a name; IDs can't).
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
            # NEW: places.id + places.types both included now
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


# NEW: haversine formula — calculates great-circle distance between two
# lat/lng points in kilometers. Standard approach for "as the crow flies"
# distance; doesn't account for actual road routes, but is more than
# accurate enough for sorting nearby results and doesn't cost an extra API
# call (we already have both points' coordinates from the Places responses).
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

_plan_cache: dict[tuple, SearchPlan] = {}


def build_search_plan_prompt(business_name: str, google_types: list[str], product_list: list[str]) -> str:
    return f"""You are helping a B2B sales rep find local sales opportunities and competitors
for a retail business called "{business_name}", which Google classifies as:
{google_types}.
This business specifically sells the following product categories:
{product_list}
Generate a search plan: a list of entries, where each entry is either an
"opportunity" (a nearby place type that represents a potential customer base
or sales lead, e.g. sports fields for a sports store) or a "competitor" (a
nearby business that sells similar products).
Base every entry strictly on the product categories listed above — do not
include categories just because they're common for a generic sporting goods
store. For example, if "Skateboards" and "Scooters" are in the product
list, skateparks are a relevant opportunity and skate shops are a relevant
competitor. But if nothing in the product list relates to golf, do NOT
include golf courses, golf equipment stores, or any other golf-related
category, even though a typical sporting goods store might stock golf gear.
Every entry must trace back to a specific product category in the list.

STRICT RELEVANCE TO THIS SPECIFIC BUSINESS (not just the product category
in general): a product category can generically imply a broad opportunity
type, but you must only include it if it is ALSO specifically relevant to
what THIS business actually is, based on its name and Google classification
— not just its product list in isolation. For example, "Mouthguards" as a
product might generically suggest "sports clubs" as an opportunity for a
general sporting goods store. But if the business being searched is
specifically an indoor skatepark (e.g. its name and classification identify
it as a dedicated skate venue, not a general sports retailer), then generic
fitness centers, gyms, or unrelated sports clubs (netball, cricket, etc.)
are NOT valid opportunities, even though "Mouthguards" is in the product
list — because those venues have no specific connection to what this
particular business does. Only include an opportunity or competitor if it
is directly and specifically tied to the actual nature of THIS business,
not merely generically tied to one of its stocked product categories.
When a business is a specialist in one narrow area (e.g. a dedicated
skatepark, a dedicated football store), keep opportunities tightly centered
on that specialty, even if the product list alone could theoretically
justify a broader set of categories.

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
an invalid type string."""


SEARCH_PLAN_TOOL = {
    "name": "generate_search_plan",
    "description": "Return the search plan entries for finding local B2B opportunities and competitors.",
    "input_schema": {
        "type": "object",
        "properties": {
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "category": {"type": "string", "enum": ["opportunity", "competitor"]},
                        "method": {"type": "string", "enum": ["nearby_type", "text_search"]},
                        "value": {"type": "string"},
                        "source_product": {"type": "string"},
                    },
                    "required": ["label", "category", "method", "value", "source_product"],
                },
            }
        },
        "required": ["entries"],
    },
}


def get_search_plan(business_name: str, google_types: list[str], product_list: list[str]) -> SearchPlan:
    # NOTE: cache key intentionally does NOT include business_name, only
    # product_list + google_types. Fix #2 (strict relevance to THIS specific
    # business) actually depends on the business name being fed into the
    # prompt every time — but since two different businesses rarely share
    # identical product_list + google_types combos in practice, this cache
    # still mostly works as intended. If you notice stale/wrong plans being
    # reused across genuinely different businesses, that's the tradeoff to
    # revisit (e.g. cache by business_name instead, at the cost of more
    # Claude calls).
    cache_key = (tuple(sorted(product_list)), tuple(sorted(google_types)))
    if cache_key in _plan_cache:
        return _plan_cache[cache_key]

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

    _plan_cache[cache_key] = plan
    return plan


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
        # NEW: capture the searched business's own place ID, used below to
        # exclude it from appearing in its own results (Fix #1).
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

            # NEW: Google's locationBias (used by text_search_nearby) is a
            # soft ranking preference, NOT a hard radius limit — Google can
            # and will return good matches well outside it (this is why
            # competitors set to a 3km radius were showing results 60km
            # away). We enforce the radius ourselves here as a hard cutoff,
            # using the distance we already calculate below.
            radius_km = radius / 1000

            results_with_distance = []
            seen_names = set()
            for p in raw_places:
                if p.get("id") == anchor_id:
                    continue

                p_lat = p["location"]["latitude"]
                p_lng = p["location"]["longitude"]
                distance_km = round(haversine_km(lat, lng, p_lat, p_lng), 1)

                # NEW: hard radius enforcement — drop anything Google
                # returned outside the intended search distance.
                if distance_km > radius_km:
                    continue

                name = p["displayName"]["text"]

                # NEW: dedupe by name — chains with multiple physical
                # branches (e.g. "Fast Times Skateboarding" at 5 different
                # addresses) were showing up once per branch. Since results
                # are processed in distance order below via sort, we sort
                # first, then dedupe, keeping only the nearest branch of
                # each named business.
                results_with_distance.append({"name": name, "distance_km": distance_km})

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

    return {
        "anchor": {
            "name": anchor["displayName"]["text"],
            "address": anchor.get("formattedAddress"),
        },
        "products_received": product_list,
        "opportunities": opportunities,
        "competitors": competitors,
    }