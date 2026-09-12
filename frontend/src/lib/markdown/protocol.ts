/** Messages exchanged with the markdown worker. */

export interface RenderRequest {
  /** Correlates a response with its request; the message id works well. */
  id: string;
  /** The markdown source to render. */
  text: string;
  /** Whether the stream that produced this text has finished. */
  complete: boolean;
  /** Monotonic sequence number, so a late reply cannot overwrite a newer one. */
  seq: number;
}

export interface RenderResponse {
  id: string;
  seq: number;
  /** Rendered HTML for the stable prefix. */
  html: string;
  /** Plain text of the block still being written, to append unformatted. */
  tail: string;
}
