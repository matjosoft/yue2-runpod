import { useLocation, useNavigate } from "react-router";
import type { Generation } from "../api/client";
import { reuseDraft } from "./draft";

export const CREATE_PATH = "/create";

/** "Reuse settings": fill the Create form with the stored request, seed included. */
export function ReuseButton({ generation }: { generation: Generation }) {
  const navigate = useNavigate();
  const location = useLocation();
  return (
    <button
      type="button"
      className="btn-secondary"
      onClick={() => {
        reuseDraft({ title: "", request: generation.request });
        if (location.pathname !== CREATE_PATH) void navigate(CREATE_PATH);
      }}
    >
      Reuse settings
    </button>
  );
}
