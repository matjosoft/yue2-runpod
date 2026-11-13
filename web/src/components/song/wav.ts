// Turns the stored song audio (FLAC) into a 24-bit PCM WAV in the browser, for "Download WAV".
// The server and worker keep FLAC; only the downloaded file is WAV.

/** The rate the worker writes (PRD W-9, section 7); used when the file does not say. */
export const DEFAULT_SAMPLE_RATE = 48000;

/** Sample rate from a FLAC file's STREAMINFO block, or undefined when it is not a FLAC. */
export function flacSampleRate(bytes: Uint8Array): number | undefined {
  if (bytes.length < 21) return undefined;
  if (String.fromCharCode(bytes[0] ?? 0, bytes[1] ?? 0, bytes[2] ?? 0, bytes[3] ?? 0) !== "fLaC") return undefined;
  if ((bytes[4] ?? 0xff) & 0x7f) return undefined; // the first block must be STREAMINFO
  const b = (i: number) => bytes[i] ?? 0;
  const rate = (b(18) << 12) | (b(19) << 4) | (b(20) >> 4);
  return rate > 0 ? rate : undefined;
}

/** A RIFF/WAVE file of interleaved little-endian 24-bit PCM. Samples are floats in [-1, 1];
 * 24-bit audio decoded to float32 comes back bit-exact. */
export function encodeWav24(channels: Float32Array[], sampleRate: number): Uint8Array {
  const numChannels = channels.length;
  const frames = channels[0]?.length ?? 0;
  const bytesPerSample = 3;
  const blockAlign = numChannels * bytesPerSample;
  const dataSize = frames * blockAlign;
  const out = new Uint8Array(44 + dataSize);
  const view = new DataView(out.buffer);
  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) out[offset + i] = text.charCodeAt(i);
  };
  ascii(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bytesPerSample * 8, true);
  ascii(36, "data");
  view.setUint32(40, dataSize, true);

  let p = 44;
  for (let f = 0; f < frames; f++) {
    for (const channel of channels) {
      let s = Math.round((channel[f] ?? 0) * 8388608);
      if (s > 8388607) s = 8388607;
      else if (s < -8388608) s = -8388608;
      out[p] = s & 0xff;
      out[p + 1] = (s >> 8) & 0xff;
      out[p + 2] = (s >> 16) & 0xff;
      p += 3;
    }
  }
  return out;
}

/** Decode audio bytes (FLAC) at their own sample rate and encode them as a 24-bit WAV. */
export async function audioToWav(bytes: ArrayBuffer): Promise<Uint8Array> {
  // Decoding resamples to the context's rate, so use the file's own rate. Read it first:
  // decodeAudioData detaches the buffer.
  const sampleRate = flacSampleRate(new Uint8Array(bytes)) ?? DEFAULT_SAMPLE_RATE;
  const context = new OfflineAudioContext(1, 1, sampleRate);
  const decoded = await context.decodeAudioData(bytes);
  const channels: Float32Array[] = [];
  for (let c = 0; c < decoded.numberOfChannels; c++) channels.push(decoded.getChannelData(c));
  return encodeWav24(channels, decoded.sampleRate);
}

/** "audio.flac" or "stems/audio.flac" -> "audio.wav". */
export function wavFileName(name: string): string {
  const base = name.split("/").pop() ?? name;
  return `${base.replace(/\.[^.]*$/, "")}.wav`;
}
