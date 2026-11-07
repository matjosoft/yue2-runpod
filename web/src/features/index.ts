// The web feature registry (PRD section 7). The shell builds routes and navigation from
// this list and hides modules whose `requires` the worker does not offer.
import { createFeature } from "./create";
import { libraryFeature } from "./library";
import { settingsFeature } from "./settings";
import type { FeatureModule } from "./types";

export const FEATURES: readonly FeatureModule[] = [createFeature, libraryFeature, settingsFeature];
