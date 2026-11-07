import type { FeatureModule } from "../types";
import { SettingsDialog } from "./SettingsDialog";

export const settingsFeature: FeatureModule = {
  id: "settings",
  routes: [{ path: "settings", element: <SettingsDialog /> }],
  nav: { label: "Settings", to: "/settings", order: 3 },
};
