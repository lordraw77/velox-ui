/**
 * Microphone capture for voice transcription (phase 10).
 *
 * A thin wrapper over `MediaRecorder`: start opens the microphone and begins
 * recording, stop closes the stream and resolves with the recorded clip. Kept out of
 * `Composer.svelte` so that component stays about the input UI, not media APIs.
 */

export class Recorder {
  #recorder: MediaRecorder | null = null;
  #chunks: Blob[] = [];
  #stream: MediaStream | null = null;

  get recording(): boolean {
    return this.#recorder !== null;
  }

  async start(): Promise<void> {
    this.#stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.#chunks = [];
    this.#recorder = new MediaRecorder(this.#stream);
    this.#recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size > 0) this.#chunks.push(event.data);
    });
    this.#recorder.start();
  }

  /** Stop recording and return the captured audio, or `null` if nothing was recorded. */
  stop(): Promise<Blob | null> {
    const recorder = this.#recorder;
    const stream = this.#stream;
    this.#recorder = null;
    this.#stream = null;
    if (!recorder) return Promise.resolve(null);

    return new Promise((resolve) => {
      recorder.addEventListener(
        "stop",
        () => {
          stream?.getTracks().forEach((track) => track.stop());
          resolve(this.#chunks.length ? new Blob(this.#chunks, { type: recorder.mimeType }) : null);
        },
        { once: true },
      );
      recorder.stop();
    });
  }
}
