'use client';

import { useState } from 'react';
import styles from './SearchPage.module.css';

interface PlaceResult {
  name: string;
  distance_km: number;
  place_id: string;
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
  alignItems: 'center',
  justifyContent: 'flex-start',
  backgroundColor: '#111111',
  color: '#ffffff',
  border: 'none',
  borderRadius: '999px',
  padding: '14px 20px',
  fontSize: '14px',
  fontWeight: 700,
  cursor: 'pointer',
  textAlign: 'left',
  position: 'relative',
};

function buildMapsUrl(place: PlaceResult): string {
  const query = encodeURIComponent(place.name);
  if (!place.place_id) {
    return `https://www.google.com/maps/search/?api=1&query=${query}`;
  }
  return `https://www.google.com/maps/search/?api=1&query=${query}&query_place_id=${place.place_id}`;
}

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
  const [openDetails, setOpenDetails] = useState<Record<string, boolean>>({});

  function toggleDropdown(key: string) {
    setOpenDropdowns((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  function toggleDetail(e: React.MouseEvent | React.KeyboardEvent, key: string) {
    e.stopPropagation();
    setOpenDetails((prev) => ({ ...prev, [key]: !prev[key] }));
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
    setOpenDetails({});

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
      <li key={index} className={styles.placeItem}>
        
          <a href={buildMapsUrl(place)}
          target="_blank"
          rel="noopener noreferrer"
          className={styles.placeLink}
        >
          <span className={styles.placeBullet} aria-hidden="true" />
          <span className={styles.placeName}>{place.name}</span>
          <span className={styles.placeDistance}>{place.distance_km} km</span>
        </a>
      </li>
    );
  }

  function renderDropdown(stateKey: string, label: string, items: PlaceResult[]) {
    const isOpen = openDropdowns[stateKey];
    const isDetailOpen = openDetails[stateKey];
    const { title, detail } = parseLabel(label);

    return (
      <div className={styles.dropdown} key={stateKey} style={{ position: 'relative' }}>
        <button
          type="button"
          className={styles.dropdownHeading}
          style={pillStyle}
          onClick={() => toggleDropdown(stateKey)}
          aria-expanded={isOpen}
        >
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
            <span className={styles.pillTitle}>{items.length} {title}</span>
            <span className={`${styles.chevron} ${isOpen ? styles.chevronOpen : ''}`}>▾</span>
          </span>
        </button>

        {detail && (
          <div
            role="button"
            tabIndex={0}
            aria-label="Show details"
            onClick={(e) => toggleDetail(e, stateKey)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') toggleDetail(e, stateKey);
            }}
            style={{
              position: 'absolute',
              top: '-8px',
              right: '-8px',
              width: '28px',
              height: '28px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              userSelect: 'none',
              zIndex: 20,
            }}
          >
            <span
              style={{
                width: '18px',
                height: '18px',
                borderRadius: '50%',
                backgroundColor: '#ffffff',
                border: '1.5px solid #dc2626',
                color: '#dc2626',
                fontSize: '11px',
                fontWeight: 800,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                lineHeight: 1,
              }}
            >
              ?
            </span>
          </div>
        )}

        {detail && isDetailOpen && (
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'absolute',
              top: '0',
              right: 'calc(100% + 10px)',
              backgroundColor: '#ffffff',
              color: '#1a1a1a',
              border: '1.5px solid #111111',
              borderRadius: '10px',
              padding: '8px 12px',
              fontSize: '12px',
              fontWeight: 500,
              lineHeight: 1.4,
              width: 'max-content',
              maxWidth: '220px',
              zIndex: 30,
              boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
            }}
          >
            ({detail})
          </div>
        )}

        {isOpen && (
          <div className={styles.dropdownPanel}>
            {items.length > 0 ? (
              <ul className={styles.itemList}>{items.map(renderListItem)}</ul>
            ) : (
              <p className={styles.emptyNote}>None found nearby.</p>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.content}>
        <div className={styles.productChips}>
          {PRODUCT_CATEGORIES.map((product) => (
            <span key={product} className={styles.chip}>
              {product}
            </span>
          ))}
        </div>

        <form onSubmit={handleSubmit} className={styles.searchForm}>
          <input
            type="text"
            value={query}
            onChange={handleInputChange}
            placeholder="Search a company..."
            className={styles.searchInput}
          />
          <button type="submit" className={styles.searchButton} disabled={isLoading}>
            {isLoading ? 'Searching...' : 'Search'}
          </button>
        </form>

        {error && <p className={styles.errorMessage}>{error}</p>}

        {data && (
          <div className={styles.anchorBlock}>
            <h1 className={styles.companyName}>{data.anchor.name}</h1>
            <p className={styles.location}>{data.anchor.address ?? 'Address unavailable'}</p>
          </div>
        )}
      </div>

      {data && (
        <div className={styles.resultsLayout}>
          <div className={styles.competitorsColumn}>
            <h2 className={styles.columnHeading}>Local Competitors</h2>
            <div className={styles.dropdownList}>
              {Object.entries(data.competitors).map(([label, items]) =>
                renderDropdown(`competitor:${label}`, label, items)
              )}
              {Object.keys(data.competitors).length === 0 && (
                <p className={styles.emptyNote}>None found nearby.</p>
              )}
            </div>
          </div>

          <div className={styles.opportunitiesColumn}>
            <h2 className={styles.columnHeading}>Local Opportunities</h2>
            <div className={styles.dropdownList}>
              {Object.entries(data.opportunities).map(([label, items]) =>
                renderDropdown(`opportunity:${label}`, label, items)
              )}
              {Object.keys(data.opportunities).length === 0 && (
                <p className={styles.emptyNote}>None found nearby.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}