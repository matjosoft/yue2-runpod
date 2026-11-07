import type { RouteObject } from "react-router";

// PRD section 7, "Web design". Do not change this shape without updating the PRD.
export interface FeatureModule {
  id: string;
  requires?: string[];                          // operation names from /api/capabilities
  routes: RouteObject[];
  nav?: { label: string; to: string; order: number };
}
