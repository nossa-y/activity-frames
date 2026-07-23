/* Content script: bridges the popup/background to the deterministic executor
 * running in the page. executor.js is injected before this file, so
 * window.NoctaExecutor is available. Nothing runs until a plan is sent. */
(function () {
  "use strict";

  chrome.runtime.onMessage.addListener(function (msg, _sender, sendResponse) {
    if (!msg || msg.type !== "nocta:runPlan") return;
    try {
      var opts = msg.opts || {};
      // Safety default: never allow destructive actions unless the popup explicitly opts in.
      if (opts.allowDestructive !== true) opts.allowDestructive = false;
      var result = window.NoctaExecutor.runPlan(document, msg.plan || [], opts);
      sendResponse({ ok: true, result: result, url: location.href });
    } catch (e) {
      sendResponse({ ok: false, error: String(e && e.stack || e) });
    }
    return true; // async response
  });
})();
