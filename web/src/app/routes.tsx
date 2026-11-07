import { Link, Navigate, useRoutes, type RouteObject } from "react-router";
import { useCapabilities } from "../api/queries";
import { FEATURES } from "../features";
import type { FeatureModule } from "../features/types";
import { Shell, type NavItem } from "./Shell";

/** Modules whose ``requires`` the worker offers. ``operations`` is null while the
 * capabilities are unknown (loading, or no worker yet); then every module is shown, so a
 * first run can still reach Create and Settings (docs/decisions.md, M3). */
export function visibleFeatures(
  features: readonly FeatureModule[],
  operations: ReadonlySet<string> | null,
): FeatureModule[] {
  if (operations === null) return [...features];
  return features.filter((f) => (f.requires ?? []).every((op) => operations.has(op)));
}

export function navItems(features: readonly FeatureModule[]): NavItem[] {
  return features.flatMap((f) => (f.nav ? [f.nav] : [])).sort((a, b) => a.order - b.order);
}

function NotFound() {
  return (
    <div className="space-y-2">
      <p>This page isn't available.</p>
      <Link className="underline" to="/">
        Go to the start page
      </Link>
    </div>
  );
}

export function buildRoutes(features: readonly FeatureModule[]): RouteObject[] {
  const nav = navItems(features);
  const home = nav[0]?.to;
  return [
    {
      path: "/",
      element: <Shell nav={nav} />,
      children: [
        { index: true, element: home && home !== "/" ? <Navigate to={home} replace /> : <NotFound /> },
        ...features.flatMap((f) => f.routes),
        { path: "*", element: <NotFound /> },
      ],
    },
  ];
}

export function AppRoutes({ features = FEATURES }: { features?: readonly FeatureModule[] }) {
  const capabilities = useCapabilities();
  const operations = capabilities.data
    ? new Set(capabilities.data.operations.map((op) => op.name))
    : null;
  return useRoutes(buildRoutes(visibleFeatures(features, operations)));
}
