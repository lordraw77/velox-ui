/**
 * Telling someone a reply finished while they were looking elsewhere.
 *
 * Two channels, because one of them is not always available. A system notification
 * needs a secure context — HTTPS, or localhost — so on a plain `http://192.168.x.x`
 * instance the browser offers no Notification API at all. The in-app channel (a
 * notice, and the tab title) works everywhere, so it is the one that always runs; the
 * system notification is added when the browser allows it. Putting HTTPS in front of
 * velox-ui later turns the second one on with no other change.
 */

import { app } from "$lib/stores/app.svelte";

let titleRestore: string | null = null;
let titleTimer: number | null = null;

/** Whether the browser will even consider a system notification here. */
export function systemNotificationsPossible(): boolean {
  return typeof window !== "undefined" && window.isSecureContext && "Notification" in window;
}

/**
 * Ask for permission, once, at a moment that is about to produce a notification.
 *
 * Never on load: a permission prompt before the person has done anything is the
 * pattern browsers now punish and readers dismiss.
 */
export async function askToNotify(): Promise<void> {
  if (!systemNotificationsPossible() || Notification.permission !== "default") return;
  try {
    await Notification.requestPermission();
  } catch {
    // Older browsers reject the promise form; nothing to do but stay in-app.
  }
}

/** Announce that a conversation's reply is ready. */
export function announceReply(title: string, chatId: string): void {
  app.notify(app.t("chat.finishedNotice", { title }), chatId);
  flashTitle(app.t("chat.finishedTitle", { title }));

  if (!systemNotificationsPossible() || Notification.permission !== "granted") return;
  try {
    const notification = new Notification(app.t("chat.finishedTitle", { title }), {
      body: app.t("chat.finishedBody"),
      tag: `velox-turn-${chatId}`,
    });
    notification.onclick = () => {
      window.focus();
      location.hash = `#/chat/${chatId}`;
      notification.close();
    };
  } catch {
    // A browser that has the API but refuses the constructor still got the notice.
  }
}

/** Put a line in the tab title until the reader comes back to the page. */
function flashTitle(text: string): void {
  if (typeof document === "undefined") return;
  if (titleRestore === null) titleRestore = document.title;
  document.title = text;

  const restore = (): void => {
    if (titleRestore !== null) document.title = titleRestore;
    titleRestore = null;
    if (titleTimer !== null) window.clearTimeout(titleTimer);
    titleTimer = null;
    document.removeEventListener("visibilitychange", onVisible);
  };
  const onVisible = (): void => {
    if (!document.hidden) restore();
  };

  document.addEventListener("visibilitychange", onVisible);
  if (titleTimer !== null) window.clearTimeout(titleTimer);
  // A cap, so a tab left in the background does not keep the line forever.
  titleTimer = window.setTimeout(restore, 60_000);
}
