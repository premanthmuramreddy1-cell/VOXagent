// Downsamples the microphone to 16 kHz mono 16-bit PCM and posts ~100 ms chunks with their loudness.
class PcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.ratio = sampleRate / 16000
    this.out = new Int16Array(1600)
    this.filled = 0
    this.position = 0
    this.energy = 0
  }
  process(inputs) {
    const input = inputs[0][0]
    if (!input) return true
    for (; this.position < input.length; this.position += this.ratio) {
      const sample = Math.max(-1, Math.min(1, input[Math.floor(this.position)]))
      this.out[this.filled++] = sample * 32767
      this.energy += sample * sample
      if (this.filled === this.out.length) {
        const chunk = this.out.slice()
        this.port.postMessage({ pcm: chunk.buffer, rms: Math.sqrt(this.energy / chunk.length) }, [chunk.buffer])
        this.filled = 0
        this.energy = 0
      }
    }
    this.position -= input.length
    return true
  }
}
registerProcessor('pcm', PcmProcessor)
