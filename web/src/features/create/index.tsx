import type { FeatureModule } from "../types";
import { CreatePage } from "./CreatePage";

export const createFeature: FeatureModule = {
  id: "create",
  requires: ["song.generate"],
  routes: [{ path: "create", element: <CreatePage /> }],
  nav: { label: "Create", to: "/create", order: 1 },
};
