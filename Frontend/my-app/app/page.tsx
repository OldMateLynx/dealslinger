'use client'; // needed for useState and interactivity in the App Router

import { useState } from 'react';

<<<<<<< Updated upstream
// Defines the shape of a search result so TypeScript knows what "data" looks like
interface SearchResult {
  companyName: string;
  location: string;
  description: string;
  competitors: string[];
=======
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
  webScanTest: string;

  
}

const PRODUCT_CATEGORIES = [
  'Mouthguards',
  'Skateboards',
  'Scooters',
  'Knee & Elbow Pads',
  'Skate/Scooter/Bike Helmets',
];

const PRODUCT_DISPLAY_NAMES: Record<string, string> = {
  'Skate/Scooter/Bike Helmets': 'Helmets',
}

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
>>>>>>> Stashed changes
}

export default function SearchPage() {
  // Tracks what the user types into the search box
  const [query, setQuery] = useState<string>('');

  // Holds the data to display after a search; null means "no search yet"
  const [data, setData] = useState<SearchResult | null>(null);

  // Updates query state as the user types
  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    setQuery(e.target.value);
  }

  // Runs the search logic and populates the results below the search bar
  function performSearch() {
    if (!query.trim()) return;

    // Placeholder result — replace this with a real API call later
    const mockResult: SearchResult = {
      companyName: query,
      location: 'Sydney, NSW, Australia',
      description:
        'A brief summary of what this company does, its market position, and key offerings will appear here once connected to a real data source.',
      competitors: [
        'Competitor One Pty Ltd',
        'Competitor Two Group',
        'Competitor Three Holdings',
        'Competitor Four & Co',
        'Competitor Five Enterprises',
      ],
    };

    setData(mockResult);
  }

  // Handles form submission (covers both Enter key and button click)
  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault(); // stop the page from reloading
    performSearch();
  }

  // Renders a single competitor list item
  function renderCompetitor(name: string, index: number) {
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
          <button type="submit" className="search-button">
            Search
          </button>
        </form>

        {/* Results render directly below the search bar once a search has run */}
        {data && (
          <div className="results">
            <h1 className="company-name">{data.companyName}</h1>
            <p className="location">{data.location}</p>

            <p className="description">{data.description}</p>

            <h2 className="section-label">Local Competitors</h2>
            <ul className="competitor-list">
              {data.competitors.map(renderCompetitor)}
            </ul>
          </div>
        )}
      </div>

<<<<<<< Updated upstream
      <style jsx>{`
        /* Page wrapper — transparent, full height, centers content horizontally */
        .page {
          background: transparent;
          min-height: 100vh;
          width: 100%;
          color: #e5e5e5;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          display: flex;
          justify-content: center;
        }
=======



     {/* website analysis feature insert */}
      {data && (
        <div
          style={{
            width: '100%',
            maxWidth: '600px',
            margin: '24px auto',
            backgroundColor: '#111111',
            color: '#ffffff',
            borderRadius: '14px',
            padding: '24px',
            boxSizing: 'border-box',
          }}
        >
          <h2 style={{ margin: 0, fontSize: '16px', fontWeight: 700 }}>
            Website Analysis
          </h2>
          <p style={{ margin: '8px 0 0', fontSize: '13px', color: '#a3a3a3' }}>
            {data.webScanTest}
          </p>
        </div>
      )}



      {data && (
        <div className={styles.resultsLayout}>
          <div className={styles.competitorsColumn}>
              <h2 className={styles.columnHeading} style={{ marginLeft: '100px'}}>Local Competitors</h2>
            <div className={styles.dropdownList}>
              {Object.entries(data.competitors).map(([label, items]) =>
                renderDropdown(`competitor:${label}`, label, items)
              )}
              {Object.keys(data.competitors).length === 0 && (
                <p className={styles.emptyNote}>None found nearby.</p>
              )}
            </div>
          </div>
>>>>>>> Stashed changes

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
          background: rgba(255, 255, 255, 0.06);
          border: 1px solid rgba(255, 255, 255, 0.15);
          border-radius: 16px;
          overflow: hidden;
          backdrop-filter: blur(6px);
        }

        .search-input {
          flex: 1;
          background: transparent;
          border: none;
          outline: none;
          padding: 16px 20px;
          font-size: 16px;
          color: #f0f0f0;
        }

        .search-input::placeholder {
          color: rgba(255, 255, 255, 0.4);
        }

        .search-button {
          background: rgba(255, 255, 255, 0.1);
          border: none;
          border-left: 1px solid rgba(255, 255, 255, 0.15);
          color: #f0f0f0;
          padding: 16px 22px;
          font-size: 15px;
          cursor: pointer;
          transition: background 0.15s ease;
        }

        .search-button:hover {
          background: rgba(255, 255, 255, 0.18);
        }

        /* Results block — spaced below the search bar */
        .results {
          margin-top: 40px;
        }

        .company-name {
          font-size: 28px;
          font-weight: 600;
          margin-bottom: 4px;
          color: #ffffff;
        }

        .location {
          font-size: 15px;
          color: rgba(255, 255, 255, 0.5);
          margin-bottom: 20px;
        }

        /* Contrasting description text — lighter background block against the monotone page */
        .description {
          background: rgba(255, 255, 255, 0.9);
          color: #111111;
          padding: 16px 18px;
          border-radius: 12px;
          font-size: 15px;
          line-height: 1.5;
          margin-bottom: 28px;
        }

        .section-label {
          font-size: 13px;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          color: rgba(255, 255, 255, 0.4);
          margin-bottom: 12px;
        }

        .competitor-list {
          list-style: none;
          padding: 0;
          margin: 0;
        }

        .competitor-list li {
          padding: 14px 16px;
          border-bottom: 1px solid rgba(255, 255, 255, 0.08);
          font-size: 15px;
        }
      `}</style>
    </div>
  );
}