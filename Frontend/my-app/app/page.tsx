'use client'; // needed for useState and interactivity in the App Router

import { useState } from 'react';

interface PlaceResult {
  name: string;
  distance_km: number;
}

interface ScanResult {
  anchor: {
    name: string;
    address: string | null;
  };
  products_received: string[];
  opportunities: Record<string, PlaceResult[]>;
  competitors: Record<string, PlaceResult[]>;
}

const PRODUCT_CATEGORIES = [
  'Mouthguards',
  'Skateboards',
  'Scooters',
  'Knee & Elbow Pads',
  'Skate/Scooter/Bike Helmets',
];

const pillStyle: React.CSSProperties = {
  width: '100%',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  backgroundColor: '#111111',
  color: '#ffffff',
  border: 'none',
  borderRadius: '20px',
  padding: '14px 44px', // extra horizontal padding so text doesn't collide with the chevron
  cursor: 'pointer',
  textAlign: 'center',
  position: 'relative',
};

// NEW: splits a label like "Skate Shops (Skateboards, Scooters & Helmets)"
// into { title: "Skate Shops", detail: "Skateboards, Scooters & Helmets" }.
// Falls back to treating the whole label as the title if no bracket is found,
// so this never breaks on a label without a bracketed section.
function parseLabel(label: string): { title: string; detail: string | null } {
  const match = label.match(/^(.+?)\s*\(([\s\S]*)\)\s*$/);
  if (match) {
    return { title: match[1].trim(), detail: match[2].trim() };
  }
  return { title: label.trim(), detail: null };
}

export default function SearchPage() {
  const [query, setQuery] = useState<string>('');
  const [data, setData] = useState<ScanResult | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openDropdowns, setOpenDropdowns] = useState<Record<string, boolean>>({});

  function toggleDropdown(key: string) {
    setOpenDropdowns((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    setQuery(e.target.value);
  }

  async function performSearch() {
    if (!query.trim()) return;

    setIsLoading(true);
    setError(null);
    setData(null);
    setOpenDropdowns({});

    try {
      const productsParam = encodeURIComponent(PRODUCT_CATEGORIES.join(','));
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL}/api/scan?business_name=${encodeURIComponent(query)}&products=${productsParam}`
      );

      if (!res.ok) {
        const errBody = await res.json().catch(() => null);
        throw new Error(errBody?.detail || `Request failed with status ${res.status}`);
      }

      const result: ScanResult = await res.json();
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setIsLoading(false);
    }
  }

  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    performSearch();
  }

  function renderListItem(place: PlaceResult, index: number) {
    return (
      <li key={index}>
        <span className="place-name">{place.name}</span>{' '}
        <span className="place-distance">{place.distance_km} km</span>
      </li>
    );
  }

  function renderDropdown(stateKey: string, label: string, items: PlaceResult[]) {
    const isOpen = openDropdowns[stateKey];
    const { title, detail } = parseLabel(label);

    return (
      <div className="dropdown" key={stateKey}>
        <button
          type="button"
          className="dropdown-heading"
          style={pillStyle}
          onClick={() => toggleDropdown(stateKey)}
          aria-expanded={isOpen}
        >
          <span className="pill-title">{items.length} {title}</span>
          {detail && <span className="pill-detail">({detail})</span>}
          <span className={`chevron ${isOpen ? 'chevron-open' : ''}`}>▾</span>
        </button>
        {isOpen && (
          <div className="dropdown-panel">
            {items.length > 0 ? (
              <ul className="item-list">{items.map(renderListItem)}</ul>
            ) : (
              <p className="empty-note">None found nearby.</p>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="page">
      <div className="content">
        <div className="product-chips">
          {PRODUCT_CATEGORIES.map((product) => (
            <span key={product} className="chip">
              {product}
            </span>
          ))}
        </div>

        <form onSubmit={handleSubmit} className="search-form">
          <input
            type="text"
            value={query}
            onChange={handleInputChange}
            placeholder="Search a company..."
            className="search-input"
          />
          <button type="submit" className="search-button" disabled={isLoading}>
            {isLoading ? 'Searching...' : 'Search'}
          </button>
        </form>

        {error && <p className="error-message">{error}</p>}

        {data && (
          <div className="anchor-block">
            <h1 className="company-name">{data.anchor.name}</h1>
            <p className="location">{data.anchor.address ?? 'Address unavailable'}</p>
          </div>
        )}
      </div>

      {data && (
        <div className="results-layout">
          <div className="competitors-column">
            <h2 className="column-heading">Local Competitors</h2>
            <div className="dropdown-list">
              {Object.entries(data.competitors).map(([label, items]) =>
                renderDropdown(`competitor:${label}`, label, items)
              )}
              {Object.keys(data.competitors).length === 0 && (
                <p className="empty-note">None found nearby.</p>
              )}
            </div>
          </div>

          <div className="opportunities-column">
            <h2 className="column-heading">Local Opportunities</h2>
            <div className="dropdown-list">
              {Object.entries(data.opportunities).map(([label, items]) =>
                renderDropdown(`opportunity:${label}`, label, items)
              )}
              {Object.keys(data.opportunities).length === 0 && (
                <p className="empty-note">None found nearby.</p>
              )}
            </div>
          </div>
        </div>
      )}

      <style jsx>{`
        .page {
          background: #fafafa;
          min-height: 100vh;
          width: 100%;
          color: #1a1a1a;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        }

        .content {
          width: 100%;
          max-width: 560px;
          margin: 0 auto;
          padding: 60px 24px 0;
        }

        .product-chips {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-bottom: 16px;
        }

        .chip {
          background: #ffffff;
          border: 1px solid rgba(0, 0, 0, 0.15);
          border-radius: 999px;
          padding: 6px 14px;
          font-size: 13px;
          color: #333333;
        }

        .search-form {
          display: flex;
          align-items: center;
          width: 100%;
          background: #ececf2;
          border: none;
          border-radius: 999px;
          overflow: hidden;
        }

        .search-input {
          flex: 1;
          background: transparent;
          border: none;
          outline: none;
          padding: 14px 20px;
          font-size: 15px;
          color: #111111;
        }

        .search-input::placeholder {
          color: rgba(0, 0, 0, 0.4);
        }

        .search-button {
          background: transparent;
          border: none;
          color: #333333;
          padding: 14px 20px;
          font-size: 15px;
          cursor: pointer;
        }

        .search-button:disabled {
          color: rgba(0, 0, 0, 0.3);
          cursor: not-allowed;
        }

        .error-message {
          margin-top: 16px;
          padding: 12px 16px;
          background: #fde8e8;
          color: #9b1c1c;
          border-radius: 8px;
          font-size: 14px;
        }

        .anchor-block {
          margin-top: 24px;
        }

        .company-name {
          font-size: 30px;
          font-weight: 800;
          margin-bottom: 4px;
          color: #111111;
        }

        .location {
          font-size: 14px;
          color: rgba(0, 0, 0, 0.5);
          margin: 0;
        }

        .results-layout {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 60px;
          max-width: 900px;
          margin: 48px auto 60px;
          padding: 0 40px;
          align-items: start;
        }

        .competitors-column {
          justify-self: start;
          width: 100%;
          max-width: 280px;
        }

        .opportunities-column {
          justify-self: center;
          width: 100%;
          max-width: 280px;
        }

        .column-heading {
          font-size: 17px;
          font-weight: 700;
          color: #111111;
          margin-bottom: 16px;
          text-align: left;
        }

        .opportunities-column .column-heading {
          text-align: center;
        }

        /* NEW: real gap between pills via flex, rather than relying on
           per-item margins that can end up looking like they're touching. */
        .dropdown-list {
          display: flex;
          flex-direction: column;
          gap: 14px;
        }

        .dropdown {
          width: 100%;
        }

        /* NEW: title line — bigger + bolder than the detail line below it */
        .pill-title {
          font-size: 15px;
          font-weight: 700;
          line-height: 1.3;
        }

        /* NEW: bracketed detail line — smaller, dimmer, sits under the title */
        .pill-detail {
          font-size: 12px;
          font-weight: 500;
          color: rgba(255, 255, 255, 0.65);
          line-height: 1.3;
          margin-top: 2px;
        }

        .dropdown-heading:hover {
          background: #2a2a2a;
        }

        /* NEW: chevron floats on the right instead of sitting inline,
           since the pill content is now a centered two-line stack. */
        .chevron {
          position: absolute;
          right: 18px;
          top: 50%;
          transform: translateY(-50%);
          transition: transform 0.15s ease;
          font-size: 12px;
        }

        .chevron-open {
          transform: translateY(-50%) rotate(180deg);
        }

        .dropdown-panel {
          background: #ffffff;
          border: 1px solid rgba(0, 0, 0, 0.1);
          border-radius: 16px;
          margin-top: 8px;
          overflow: hidden;
          box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
        }

        .item-list {
          list-style: none;
          padding: 0;
          margin: 0;
        }

        .item-list li {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 12px 16px;
          border-bottom: 1px solid rgba(0, 0, 0, 0.08);
          font-size: 14px;
          color: #1a1a1a;
        }

        .place-name {
          font-weight: 600;
        }

        .place-distance {
          flex-shrink: 0;
          font-size: 12px;
          font-weight: 500;
          color: rgba(0, 0, 0, 0.45);
        }

        .item-list li:last-child {
          border-bottom: none;
        }

        .empty-note {
          font-size: 14px;
          color: rgba(0, 0, 0, 0.4);
          font-style: italic;
          padding: 12px 16px;
          margin: 0;
        }

        @media (max-width: 700px) {
          .results-layout {
            grid-template-columns: 1fr;
            gap: 32px;
          }
          .competitors-column,
          .opportunities-column {
            justify-self: stretch;
            max-width: none;
          }
          .opportunities-column .column-heading {
            text-align: left;
          }
        }
      `}</style>
    </div>
  );
}