/**
 * Track the visual viewport, for the on-screen keyboard.
 *
 * iOS Safari does not resize the *layout* viewport when the keyboard opens — it
 * scrolls the page instead. A `height: 100dvh` app shell with `overflow: hidden`
 * therefore keeps its full height and the composer ends up underneath the
 * keyboard, unreachable.
 *
 * The `visualViewport` API reports the part of the page actually visible, which
 * is what the shell should be sized to while the keyboard is up. This publishes
 * it as a CSS custom property (`--viewport-height`) rather than wiring it into
 * component state, so the layout stays in CSS and nothing re-renders on a
 * keyboard animation frame.
 *
 * A browser without `visualViewport` (or a desktop one, where this is a no-op)
 * simply never sets the property, and the shell falls back to `100dvh`.
 */

export function trackVisualViewport(): () => void {
  const viewport = window.visualViewport;
  if (!viewport) return () => {};

  const apply = (): void => {
    document.documentElement.style.setProperty(
      "--viewport-height",
      `${viewport.height}px`,
    );
  };

  apply();
  viewport.addEventListener("resize", apply);
  viewport.addEventListener("scroll", apply);
  return () => {
    viewport.removeEventListener("resize", apply);
    viewport.removeEventListener("scroll", apply);
    document.documentElement.style.removeProperty("--viewport-height");
  };
}
