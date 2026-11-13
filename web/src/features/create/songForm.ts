// Form values for song.generate and their mapping to the request the server stores.
import type { GenerationCreate } from "../../api/client";
import type { Draft } from "../../components/draft";

export const OPERATION = "song.generate";
export const COT_MODES = ["full", "melody", "off"] as const;
export type CotMode = (typeof COT_MODES)[number];
/** The planning modes that take a score: full reads it as chord-annotated, melody as
 * melody only (yue2-api-notes section 3). "off" cannot take one. */
export const SCORE_TYPES = [
  { cot: "full", label: "Full score (melody and chords)" },
  { cot: "melody", label: "Melody only" },
] as const satisfies readonly { cot: CotMode; label: string }[];

export interface SongFormValues {
  title: string;
  style: string;
  lyrics: string;
  seed: string;
  cot: CotMode;
  cfgScale: string;
  /** An ABC score to sing instead of planning one. Blank means the model plans it. */
  abc: string;
}

export const EMPTY_FORM: SongFormValues = {
  title: "",
  style: "",
  lyrics: "",
  seed: "",
  cot: "full",
  cfgScale: "",
  abc: "",
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
    abc: text(r["abc"]),
  };
}

/** The draft keeps raw text, so a half-typed number survives a reload. */
export function toDraft(v: SongFormValues): Draft {
  return {
    title: v.title,
    request: { style: v.style, lyrics: v.lyrics, seed: v.seed, cot: v.cot, cfg_scale: v.cfgScale, abc: v.abc },
  };
}

export type FieldErrors = Partial<Record<"title" | "style" | "lyrics" | "seed" | "cot" | "cfg_scale" | "abc", string>>;

export const FORM_FIELDS: readonly (keyof FieldErrors)[] = ["title", "style", "lyrics", "seed", "cot", "cfg_scale", "abc"];

/** Validate and build the POST body. Blank optional fields are left out: the server fills
 * a random seed, an absent cfg_scale means the model default, and an absent score means
 * the model plans one. A score is sent verbatim, whitespace included. */
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
  if (v.abc.trim()) {
    if (v.cot === "off") errors.abc = "Choose whether the score is a full score or a melody only.";
    else request["abc"] = v.abc;
  }
  if (Object.keys(errors).length > 0) return { body: null, errors };
  const body: GenerationCreate = { operation: OPERATION, request };
  if (v.title.trim()) body.title = v.title.trim();
  return { body, errors };
}
