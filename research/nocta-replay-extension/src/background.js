/* Service worker. Minimal for v0.1: the popup talks to the content script
 * directly via tabs.sendMessage. This worker is where a future version would
 * hold the compiled-routine store, receive plans from the Nocta app over native
 * messaging, and route deopt steps to an LLM for the vision fallback. */
chrome.runtime.onInstalled.addListener(function () {
  console.log("[Nocta Replay] installed. Load a plan from the popup to replay a routine.");
});
