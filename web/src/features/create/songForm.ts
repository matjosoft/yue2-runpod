// Form values for song.generate and their mapping to the request the server stores.
import type { GenerationCreate } from "../../api/client";
import type { Draft } from "../../components/draft";

export const OPERATION = "song.generate";
export const COT_MODES = ["full", "melody", "off"] as const;
export type CotMode = (typeof COT_MODES)[number];

export interface SongFormValues {
  title: string;
  style: string;
  lyrics: string;
  seed: string;
  cot: CotMode;
  cfgScale: string;
}

export const EMPTY_FORM: SongFormValues = {
  title: "",
  style: "",
  lyrics: "",
  seed: "",
  cot: "full",
  cfgScale: "",
};

// PRD section 9. The style line is from the YuE2 README; the lyrics are original. The
// section tags match yue2-api-notes OQ-1 (the package passes lyrics through verbatim).
export const EXAMPLE = {
  style: "English, jazz-funk, warm lead vocal, Rhodes, bass and drums",
  lyrics: `[Verse]
Streetlights hum along the avenue
Coffee steam and a rhythm just for two
We walk slow, we've got nowhere to be
The city plays a tune for you and me

[Chorus]
Tonight we're awake, tonight we're alive
The bass line carries us home
Tonight we're awake, we'll dance till light
And never feel alone`,
};

function text(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "";
}

export function fromDraft(draft: Draft | null): SongFormValues {
  if (!draft) return EMPTY_FORM;
  const r = draft.request;
  const cot = COT_MODES.find((m) => m === r["cot"]) ?? "full";
  return {
    title: draft.title,
    style: text(r["style"]),
    lyrics: text(r["lyrics"]),
    seed: text(r["seed"]),
    cot,
    cfgScale: text(r["cfg_scale"]),
  };
}

/** The draft keeps raw text, so a half-typed number survives a reload. */
export function toDraft(v: SongFormValues): Draft {
  return {
    title: v.title,
    request: { style: v.style, lyrics: v.lyrics, seed: v.seed, cot: v.cot, cfg_scale: v.cfgScale },
  };
}

export type FieldErrors = Partial<Record<"title" | "style" | "lyrics" | "seed" | "cot" | "cfg_scale", string>>;

export const FORM_FIELDS: readonly (keyof FieldErrors)[] = ["title", "style", "lyrics", "seed", "cot", "cfg_scale"];

/** Validate and build the POST body. Blank optional fields are left out: the server fills
 * a random seed, and an absent cfg_scale means the model default. */
export function buildCreate(v: SongFormValues): { body: GenerationCreate | null; errors: FieldErrors } {
  const errors: FieldErrors = {};
  if (!v.style.trim()) errors.style = "Style is required.";
  if (!v.lyrics.trim()) errors.lyrics = "Lyrics are required.";
  const request: Record<string, unknown> = { style: v.style, lyrics: v.lyrics, cot: v.cot };
  const seed = v.seed.trim();
  if (seed) {
    if (/^\d+$/.test(seed)) request["seed"] = Number(seed);
    else errors.seed = "Seed must be a whole number, or blank for random.";
  }
  const cfg = v.cfgScale.trim();
  if (cfg) {
    const n = Number(cfg);
    if (Number.isFinite(n)) request["cfg_scale"] = n;
    else errors.cfg_scale = "CFG scale must be a number, or blank for the default.";
  }
  if (Object.keys(errors).length > 0) return { body: null, errors };
  const body: GenerationCreate = { operation: OPERATION, request };
  if (v.title.trim()) body.title = v.title.trim();
  return { body, errors };
}
