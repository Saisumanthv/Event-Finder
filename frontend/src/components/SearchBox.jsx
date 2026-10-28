import { useState } from "react";

export default function SearchBox({ onSearch, disabled }) {
  const [query, setQuery] = useState("");

  function handleSubmit(e) {
    e.preventDefault();
    if (query.trim()) onSearch(query.trim());
  }

  return (
    <form className="search" onSubmit={handleSubmit}>
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Try: free weekend hackathons for beginners, online"
        maxLength={300}
        disabled={disabled}
      />
      <button type="submit" disabled={disabled || !query.trim()}>
        Search
      </button>
    </form>
  );
}
