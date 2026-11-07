import type { FeatureModule } from "../types";
import { GenerationDetail } from "./GenerationDetail";
import { LibraryPage } from "./LibraryPage";

export const libraryFeature: FeatureModule = {
  id: "library",
  routes: [
    { path: "library", element: <LibraryPage /> },
    { path: "library/:id", element: <GenerationDetail /> },
  ],
  nav: { label: "Library", to: "/library", order: 2 },
};
