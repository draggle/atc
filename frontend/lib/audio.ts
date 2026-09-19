/**
 * Microphone capture -> PCM16 mono 16 kHz frames.
 * Uses ScriptProcessorNode (deprecated but universally available) and linear decimation.
 */

export const TARGET_RATE = 16000;

export interface Capture {
  stop(): void;
}

function downsample(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate) return input;
  const ratio = fromRate / toRate;
  const outLen = Math.floor(input.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(Math.floor((i + 1) * ratio), input.length);
    let sum = 0;
    let n = 0;
    for (let j = start; j < end; j++) {
      sum += input[j];
      n++;
    }
    out[i] = n > 0 ? sum / n : 0;
  }
  return out;
}

function toPcm16(f32: Float32Array): ArrayBuffer {
  const buf = new ArrayBuffer(f32.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < f32.length; i++) {
    const s = Math.max(-1, Math.min(1, f32[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}

export async function startCapture(onFrame: (pcm: ArrayBuffer, level: number) => void): Promise<Capture> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const ctx = new AudioContext();
  await ctx.resume();
  const source = ctx.createMediaStreamSource(stream);
  const processor = ctx.createScriptProcessor(4096, 1, 1);
  const sink = ctx.createGain();
  sink.gain.value = 0;

  processor.onaudioprocess = (e: AudioProcessingEvent) => {
    const ch = e.inputBuffer.getChannelData(0);
    let peak = 0;
    for (let i = 0; i < ch.length; i++) peak = Math.max(peak, Math.abs(ch[i]));
    const ds = downsample(ch, ctx.sampleRate, TARGET_RATE);
    onFrame(toPcm16(ds), peak);
  };
  source.connect(processor);
  processor.connect(sink);
  sink.connect(ctx.destination);

  return {
    stop() {
      processor.disconnect();
      source.disconnect();
      sink.disconnect();
      for (const t of stream.getTracks()) t.stop();
      void ctx.close();
    },
  };
}
