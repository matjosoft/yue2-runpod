import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { makeQueryClient } from "../src/app/App";
import { AppRoutes } from "../src/app/routes";
import type { FeatureModule } from "../src/features/types";

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

/** Render the whole app at ``path`` with a fresh query client and an in-memory router. */
export function renderApp(path: string, options: { features?: readonly FeatureModule[] } = {}) {
  const client = makeQueryClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes {...(options.features ? { features: options.features } : {})} />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}
