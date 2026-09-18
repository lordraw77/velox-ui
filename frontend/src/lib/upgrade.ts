/**
 * Surviving an upgrade that happens under an open tab.
 *
 * Panels are loaded when they are first opened, from files whose names carry a content
 * hash. Upgrade the server while a tab is open and those names change: the tab still
 * asks for the ones it was built with, the server answers 404, and clicking Models or
 * Providers leaves a dead panel and a "failed to fetch dynamically imported module" in
 * the console. Nothing is wrong with the session — the page is simply older than the
 * server.
 *
 * So a failed chunk load is read as "this page is out of date" and the page is
 * reloaded, once. The guard against a loop is a session flag: if reloading did not fix
 * it, the panel says so instead of reloading forever, because a reload loop is a worse
 * failure than a panel that will not open.
 *
 * Reloading costs nothing that matters now: a turn in flight belongs to the
 * conversation rather than to the page (ADR-0024), so the reply keeps being written
 * and the reloaded page picks it back up.
 */

const RELOAD_FLAG = "velox:reloaded-for-upgrade";
const RELOAD_WINDOW_MS = 30_000;

/** Whether a reload has already been tried for this, recently. */
export function alreadyReloaded(now: number = Date.now()): boolean {
  try {
    const at = Number(sessionStorage.getItem(RELOAD_FLAG) ?? "");
    return Number.isFinite(at) && at > 0 && now - at < RELOAD_WINDOW_MS;
  } catch {
    // Private mode, or storage blocked: treat it as "not yet", and rely on the
    // browser to stop a runaway reload.
    return false;
  }
}

/** Reload the page to pick up the new build, unless that was just tried. */
export function reloadForUpgrade(now: number = Date.now()): boolean {
  if (alreadyReloaded(now)) return false;
  try {
    sessionStorage.setItem(RELOAD_FLAG, String(now));
  } catch {
    // Nothing to remember it with; one reload is still better than a dead panel.
  }
  location.reload();
  return true;
}

/** Whether a rejection is a chunk this page can no longer fetch. */
export function isStaleChunkError(reason: unknown): boolean {
  const message =
    reason instanceof Error ? reason.message : typeof reason === "string" ? reason : "";
  return (
    /dynamically imported module/i.test(message) ||
    /importing a module script failed/i.test(message) ||
    /error loading dynamically imported module/i.test(message)
  );
}

/**
 * Watch for chunks this page can no longer load.
 *
 * `vite:preloadError` is the deliberate signal; the rejection handler catches the same
 * failure in browsers that reach it the other way, which Firefox does.
 */
export function installUpgradeGuard(): void {
  window.addEventListener("vite:preloadError", (event) => {
    event.preventDefault();
    reloadForUpgrade();
  });
  window.addEventListener("unhandledrejection", (event) => {
    if (isStaleChunkError(event.reason)) {
      event.preventDefault();
      reloadForUpgrade();
    }
  });
}
