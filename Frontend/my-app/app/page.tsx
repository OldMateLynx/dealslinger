'use client'; // needed for useState and interactivity in the App Router

import { useState } from 'react';

// CHANGED: This shape now matches exactly what your FastAPI /api/scan endpoint
// returns (see Backend/main.py). It replaces the old mock "companyName/description/
// competitors" shape, since we're no longer inventing fake data.
interface ScanResult {
  anchor: {
    name: string;
    address: string | null;
  };
  opportunities: {
    skateparks: string[];
    footy_fields: string[];
  };
  competitors: {
    skate_shops: string[];
  };
}

export default function SearchPage() {
  // Tracks what the user types into the search box
  const [query, setQuery] = useState<string>('');

  // Holds the data to display after a search; null means "no search yet"
  const [data, setData] = useState<ScanResult | null>(null);

  // NEW: Tracks whether a search is currently in flight, so we can show a
  // loading state instead of the page looking frozen while we wait on the
  // backend (which itself is waiting on 3 Google API calls).
  const [isLoading, setIsLoading] = useState(false);

  // NEW: Tracks any error message (e.g. business not found, backend down)
  // so we can show something useful instead of failing silently.
  const [error, setError] = useState<string | null>(null);

  // Updates query state as the user types
  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    setQuery(e.target.value);
  }

  // CHANGED: performSearch is now async and calls your real FastAPI backend
  // instead of building a fake mockResult.
  async function performSearch() {
    if (!query.trim()) return;

    setIsLoading(true);
    setError(null);
    setData(null); // clear old results while the new search runs

    try {
      // NEXT_PUBLIC_API_URL should be set in Frontend/.env.local, e.g.:
      // NEXT_PUBLIC_API_URL=http://localhost:8000
      // It's safe to expose (NEXT_PUBLIC_) because it's just a URL, not a secret —
      // your actual Google API key stays server-side in the Backend .env file.
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL}/api/scan?business_name=${encodeURIComponent(query)}`
      );

      // FastAPI returns a non-200 status (e.g. 404 if the business name
      // wasn't found) with a JSON body containing a "detail" message.
      if (!res.ok) {
        const errBody = await res.json().catch(() => null);
        throw new Error(errBody?.detail || `Request failed with status ${res.status}`);
      }

      const result: ScanResult = await res.json();
      setData(result);
    } catch (err) {
      // Covers both network failures (backend not running) and the
      // thrown Error above (business not found, etc.)
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setIsLoading(false);
    }
  }

  // Handles form submission (covers both Enter key and button click)
  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault(); // stop the page from reloading
    performSearch();
  }

  // CHANGED: renamed from renderCompetitor to renderListItem since we now
  // reuse this for opportunities too (skateparks, footy fields) as well as
  // competitors (skate shops) — same list-item shape, different data.
  function renderListItem(name: string, index: number) {
    return <li key={index}>{name}</li>;
  }

  return (
    <div className="page">
      <div className="content">
        {/* Search bar — stays at the top, always visible */}
        <form onSubmit={handleSubmit} className="search-form">
          <input
            type="text"
            value={query}
            onChange={handleInputChange}
            placeholder="Search a company..."
            className="search-input"
          />
          {/* NEW: button disables while loading and label changes, so the
              user gets feedback that something is happening (Google calls
              can take a second or two since we're chaining 3 requests). */}
          <button type="submit" className="search-button" disabled={isLoading}>
            {isLoading ? 'Searching...' : 'Search'}
          </button>
        </form>

        {/* NEW: error state — shown if the backend call fails or the
            business name can't be found by Google Places. */}
        {error && <p className="error-message">{error}</p>}

        {/* Results render directly below the search bar once a search has run */}
        {data && (
          <div className="results">
            <h1 className="company-name">{data.anchor.name}</h1>
            <p className="location">{data.anchor.address ?? 'Address unavailable'}</p>

            {/* NEW: Opportunities section — nearby skateparks and footy fields.
                This replaces the old static "description" paragraph, since that
                was just mock filler text. */}
            <h2 className="section-label">Opportunities — Skateparks</h2>
            {data.opportunities.skateparks.length > 0 ? (
              <ul className="item-list">
                {data.opportunities.skateparks.map(renderListItem)}
              </ul>
            ) : (
              <p className="empty-note">None found nearby.</p>
            )}

            <h2 className="section-label">Opportunities — Footy Fields</h2>
            {data.opportunities.footy_fields.length > 0 ? (
              <ul className="item-list">
                {data.opportunities.footy_fields.map(renderListItem)}
              </ul>
            ) : (
              <p className="empty-note">None found nearby.</p>
            )}

            <h2 className="section-label">Competitors — Skate Shops</h2>
            {data.competitors.skate_shops.length > 0 ? (
              <ul className="item-list">
                {data.competitors.skate_shops.map(renderListItem)}
              </ul>
            ) : (
              <p className="empty-note">None found nearby.</p>
            )}
          </div>
        )}
      </div>

      <style jsx>{`
        /* Page wrapper — light background, full height, centers content horizontally */
        .page {
          background: #fafafa;
          min-height: 100vh;
          width: 100%;
          color: #1a1a1a;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          display: flex;
          justify-content: center;
        }

        /* Single centered column holding search bar + results */
        .content {
          width: 100%;
          max-width: 560px;
          padding: 80px 24px 60px;
        }

        .search-form {
          display: flex;
          align-items: center;
          width: 100%;
          background: #ffffff;
          border: 1px solid rgba(0, 0, 0, 0.12);
          border-radius: 16px;
          overflow: hidden;
          box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
        }

        .search-input {
          flex: 1;
          background: transparent;
          border: none;
          outline: none;
          padding: 16px 20px;
          font-size: 16px;
          color: #111111;
        }

        .search-input::placeholder {
          color: rgba(0, 0, 0, 0.35);
        }

        .search-button {
          background: #111111;
          border: none;
          color: #ffffff;
          padding: 16px 22px;
          font-size: 15px;
          cursor: pointer;
          transition: background 0.15s ease;
        }

        .search-button:hover {
          background: #333333;
        }

        /* NEW: dimmed look while a search is in flight */
        .search-button:disabled {
          background: #666666;
          cursor: not-allowed;
        }

        /* NEW: error message styling */
        .error-message {
          margin-top: 16px;
          padding: 12px 16px;
          background: #fde8e8;
          color: #9b1c1c;
          border-radius: 8px;
          font-size: 14px;
        }

        /* Results block — spaced below the search bar */
        .results {
          margin-top: 40px;
        }

        .company-name {
          font-size: 28px;
          font-weight: 600;
          margin-bottom: 4px;
          color: #111111;
        }

        .location {
          font-size: 15px;
          color: rgba(0, 0, 0, 0.5);
          margin-bottom: 28px;
        }

        .section-label {
          font-size: 13px;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          color: rgba(0, 0, 0, 0.45);
          margin-bottom: 12px;
          margin-top: 24px;
        }

        /* RENAMED from .competitor-list — now shared by opportunities and
           competitors since they're the same visual list style. */
        .item-list {
          list-style: none;
          padding: 0;
          margin: 0;
        }

        .item-list li {
          padding: 14px 16px;
          border-bottom: 1px solid rgba(0, 0, 0, 0.08);
          font-size: 15px;
          color: #1a1a1a;
        }

        /* NEW: shown when a category has zero results nearby */
        .empty-note {
          font-size: 14px;
          color: rgba(0, 0, 0, 0.4);
          font-style: italic;
          margin: 0 0 8px 0;
        }
      `}</style>
    </div>
  );
}