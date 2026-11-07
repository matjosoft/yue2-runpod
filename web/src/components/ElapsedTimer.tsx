import { useEffect, useState } from "react";
import { formatElapsed } from "./format";

/** Time since ``since`` (an ISO timestamp), so it survives a page reload. */
export function ElapsedTimer({ since }: { since: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, []);
  return (
    <p className="text-sm text-slate-500">
      Elapsed <span data-testid="elapsed">{formatElapsed((now - Date.parse(since)) / 1000)}</span>
    </p>
  );
}
