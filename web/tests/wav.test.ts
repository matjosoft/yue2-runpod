import { describe, expect, test } from "vitest";
import { encodeWav24, flacSampleRate, wavFileName } from "../src/components/song/wav";

/** The first 21 bytes of a FLAC whose STREAMINFO says ``rate`` Hz, stereo, 24-bit. */
function flacHeader(rate: number): Uint8Array {
  const b = new Uint8Array(42);
  b.set([0x66, 0x4c, 0x61, 0x43, 0x00, 0x00, 0x00, 0x22]);
  b[18] = (rate >> 12) & 0xff;
  b[19] = (rate >> 4) & 0xff;
  b[20] = ((rate & 0x0f) << 4) | (1 << 1); // channels - 1 = 1
  return b;
}

describe("wav", () => {
  test("reads the sample rate from FLAC STREAMINFO", () => {
    expect(flacSampleRate(flacHeader(48000))).toBe(48000);
    expect(flacSampleRate(flacHeader(44100))).toBe(44100);
    expect(flacSampleRate(new TextEncoder().encode("RIFF not a flac at all...."))).toBeUndefined();
    expect(flacSampleRate(new Uint8Array(4))).toBeUndefined();
  });

  test("writes a 24-bit PCM stereo WAV header and interleaved samples", () => {
    const left = new Float32Array([0, 1, -1]);
    const right = new Float32Array([0.5, -0.5, 2]); // 2 clips to full scale
    const wav = encodeWav24([left, right], 48000);
    const view = new DataView(wav.buffer);
    const text = (o: number, n: number) => String.fromCharCode(...wav.slice(o, o + n));
    expect(text(0, 4)).toBe("RIFF");
    expect(view.getUint32(4, true)).toBe(wav.length - 8);
    expect(text(8, 8)).toBe("WAVEfmt ");
    expect(view.getUint16(20, true)).toBe(1); // PCM
    expect(view.getUint16(22, true)).toBe(2);
    expect(view.getUint32(24, true)).toBe(48000);
    expect(view.getUint32(28, true)).toBe(48000 * 6);
    expect(view.getUint16(32, true)).toBe(6);
    expect(view.getUint16(34, true)).toBe(24);
    expect(text(36, 4)).toBe("data");
    expect(view.getUint32(40, true)).toBe(3 * 6);

    const sample = (i: number) => {
      const o = 44 + i * 3;
      const v = (wav[o] ?? 0) | ((wav[o + 1] ?? 0) << 8) | ((wav[o + 2] ?? 0) << 16);
      return v & 0x800000 ? v - 0x1000000 : v;
    };
    expect([0, 1, 2, 3, 4, 5].map(sample)).toEqual([0, 4194304, 8388607, -4194304, -8388608, 8388607]);
  });

  test("names the file after the stored audio", () => {
    expect(wavFileName("audio.flac")).toBe("audio.wav");
    expect(wavFileName("stems/vocals.flac")).toBe("vocals.wav");
  });
});
