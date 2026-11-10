const SPEECH = 0.03 // loudness that counts as the user talking
const QUIET = 0.015
const END_OF_SPEECH_MS = 900
const MAX_MS = 30_000
const CHUNK_MS = 100

/** Plays WAV clips from the server one after another. stop() is the barge-in cut. */
export class Player {
  private ctx?: AudioContext
  private queue: ArrayBuffer[] = []
  private current?: AudioBufferSourceNode
  private busy = false
  onChange: (speaking: boolean) => void = () => {}

  get speaking() {
    return this.busy
  }

  enqueue(wav: ArrayBuffer) {
    this.queue.push(wav)
    if (!this.busy) this.next()
  }

  private async next() {
    const wav = this.queue.shift()
    if (!wav) {
      this.busy = false
      return this.onChange(false)
    }
    this.busy = true
    this.onChange(true)
    this.ctx ??= new AudioContext()
    try {
      await this.ctx.resume()
      const source = this.ctx.createBufferSource()
      source.buffer = await this.ctx.decodeAudioData(wav)
      source.connect(this.ctx.destination)
      source.onended = () => {
        if (this.current === source) this.next()
      }
      this.current = source
      if (this.busy) source.start()
    } catch {
      this.next() // skip a clip that will not decode
    }
  }

  stop() {
    this.queue = []
    const playing = this.current
    this.current = undefined
    this.busy = false
    try {
      playing?.stop()
    } catch {
      // it had not started yet
    }
    this.onChange(false)
  }
}

type MicHandlers = {
  onStart: () => void
  onChunk: (pcm: ArrayBuffer) => void
  onStop: () => void
}

/** Microphone capture as 16 kHz PCM, with push-to-talk and hands-free end-of-speech detection. */
export class Mic {
  recording = false
  handsFree = false
  private ctx?: AudioContext
  private stream?: MediaStream
  private preRoll: ArrayBuffer[] = []
  private loud = 0
  private quietMs = 0
  private elapsedMs = 0
  private handlers: MicHandlers

  constructor(handlers: MicHandlers) {
    this.handlers = handlers
  }

  /** Throws if the user or browser blocks the microphone. */
  async open() {
    if (this.ctx) return this.ctx.resume()
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    })
    this.ctx = new AudioContext()
    await this.ctx.audioWorklet.addModule('/pcm-worklet.js')
    const node = new AudioWorkletNode(this.ctx, 'pcm')
    this.ctx.createMediaStreamSource(this.stream).connect(node)
    node.port.onmessage = (event) => this.onData(event.data.pcm, event.data.rms)
  }

  start() {
    if (this.recording) return
    this.recording = true
    this.quietMs = this.elapsedMs = 0
    this.handlers.onStart()
    this.preRoll.forEach(this.handlers.onChunk) // keep the first syllable
    this.preRoll = []
  }

  stop() {
    if (!this.recording) return
    this.recording = false
    this.handlers.onStop()
  }

  private onData(pcm: ArrayBuffer, rms: number) {
    if (this.recording) {
      this.handlers.onChunk(pcm)
      this.elapsedMs += CHUNK_MS
      this.quietMs = rms < QUIET ? this.quietMs + CHUNK_MS : 0
      if ((this.handsFree && this.quietMs >= END_OF_SPEECH_MS) || this.elapsedMs >= MAX_MS) this.stop()
      return
    }
    this.preRoll.push(pcm)
    if (this.preRoll.length > 3) this.preRoll.shift()
    if (!this.handsFree) return
    // shortcut: loudness threshold, swap in a trained voice-activity model if noisy rooms trigger it.
    this.loud = rms > SPEECH ? this.loud + 1 : 0
    if (this.loud >= 2) {
      this.loud = 0
      this.start()
    }
  }

  close() {
    this.stream?.getTracks().forEach((track) => track.stop())
    this.ctx?.close()
    this.ctx = undefined
    this.recording = false
  }
}
