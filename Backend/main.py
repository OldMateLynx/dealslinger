import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx

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
BASE_URL = "https://places.googleapis.com/v1/places"


async def geocode_business(client: httpx.AsyncClient, name: str) -> dict:
    """Step 1: turn a business name into a lat/lng anchor point."""
    resp = await client.post(
        f"{BASE_URL}:searchText",
        headers={
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
        },
        json={"textQuery": name},
    )
    data = resp.json()
    if not data.get("places"):
        raise HTTPException(status_code=404, detail=f"Could not find '{name}'")
    return data["places"][0]  # first/best match


async def nearby_search(client: httpx.AsyncClient, lat: float, lng: float, included_type: str, radius: int = 10000):
    """Nearby Search filtered by a specific place type."""
    resp = await client.post(
        f"{BASE_URL}:searchNearby",
        headers={
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
        },
        json={
            "includedTypes": [included_type],
            "maxResultCount": 10,
            "locationRestriction": {
                "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius}
            },
        },
    )
    return resp.json().get("places", [])


async def text_search_nearby(client: httpx.AsyncClient, query: str, lat: float, lng: float, radius: int = 15000):
    """Text Search biased to an area — for categories with no clean place type (e.g. 'skate shop')."""
    resp = await client.post(
        f"{BASE_URL}:searchText",
        headers={
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
        },
        json={
            "textQuery": query,
            "locationRestriction": {
                "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius}
            },
        },
    )
    return resp.json().get("places", [])


@app.get("/api/scan")
async def scan_location(business_name: str = Query(..., min_length=1)):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API key not configured")

    async with httpx.AsyncClient() as client:
        anchor = await geocode_business(client, business_name)
        lat = anchor["location"]["latitude"]
        lng = anchor["location"]["longitude"]

        skateparks = await nearby_search(client, lat, lng, "skateboard_park")
        footy_fields = await text_search_nearby(client, "oval", lat, lng)
        skate_shops = await text_search_nearby(client, "skate shop", lat, lng)

    return {
        "anchor": {
            "name": anchor["displayName"]["text"],
            "address": anchor.get("formattedAddress"),
        },
        "opportunities": {
            "skateparks": [p["displayName"]["text"] for p in skateparks],
            "footy_fields": [p["displayName"]["text"] for p in footy_fields],
        },
        "competitors": {
            "skate_shops": [p["displayName"]["text"] for p in skate_shops],
        },
    }