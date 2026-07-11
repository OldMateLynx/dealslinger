'use client'; // needed for useState and interactivity in the App Router

import { useState } from 'react';

// Defines the shape of a search result so TypeScript knows what "data" looks like
interface SearchResult {
  companyName: string;
  location: string;
  description: string;
  competitors: string[];
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
          margin-bottom: 20px;
        }

        /* Description block — subtle contrast against the light page */
        .description {
          background: #f0f0f0;
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
          color: rgba(0, 0, 0, 0.45);
          margin-bottom: 12px;
        }

        .competitor-list {
          list-style: none;
          padding: 0;
          margin: 0;
        }

        .competitor-list li {
          padding: 14px 16px;
          border-bottom: 1px solid rgba(0, 0, 0, 0.08);
          font-size: 15px;
          color: #1a1a1a;
        }
      `}</style>
    </div>
  );
}