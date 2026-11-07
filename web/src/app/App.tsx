import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { BrowserRouter } from "react-router";
import { AppRoutes } from "./routes";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    // Polling refreshes state anyway, so failed requests are not retried.
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

export function App() {
  const [client] = useState(makeQueryClient);
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
