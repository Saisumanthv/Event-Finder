import { useEffect, useState } from "react";

export default function Loader() {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const start = Date.now();
    const id = setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 250);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="loader" role="status" aria-label="Loading">
      <div className="spinner" />
      <span className="loader-time" aria-hidden="true">
        {seconds}s
      </span>
    </div>
  );
}
