document.getElementById("run").addEventListener("click", async function () {
  var out = document.getElementById("out");
  var plan;
  try {
    plan = JSON.parse(document.getElementById("plan").value);
    if (!Array.isArray(plan)) throw new Error("plan must be a JSON array of steps");
  } catch (e) {
    out.textContent = "Invalid plan JSON: " + e.message;
    return;
  }
  var opts = {
    dryRun: document.getElementById("dry").checked,
    allowDestructive: document.getElementById("allow").checked,
  };
  var [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  chrome.tabs.sendMessage(tab.id, { type: "nocta:runPlan", plan: plan, opts: opts }, function (resp) {
    if (chrome.runtime.lastError) {
      out.textContent = "No content script on this tab (try reloading the page). " +
        chrome.runtime.lastError.message;
      return;
    }
    if (!resp || !resp.ok) { out.textContent = "Error: " + (resp && resp.error); return; }
    var s = resp.result.summary;
    out.textContent =
      (opts.dryRun ? "[DRY RUN - nothing clicked]\n" : "[EXECUTED]\n") +
      "url: " + resp.url + "\n" +
      "steps: " + s.total + "  grounded-no-vision: " + s.grounded_no_vision +
      " (tier1 " + s.tier1 + ", tier2 " + s.tier2 + ")\n" +
      "deopt (needs vision): " + s.deopt + "   blocked (safety): " + s.blocked +
      "   acted: " + s.acted + "\n" +
      "guard coverage: " + s.guard_coverage + "\n\n" +
      resp.result.steps.map(function (st) {
        return "#" + st.i + " " + st.op + " '" + (st.target || "") + "' -> " +
          (st.deopt ? "DEOPT" : st.blocked ? "BLOCKED(" + (st.reason || "") + ")" :
            "tier" + st.tier + " " + st.method + (st.ok ? " OK" : "")) ;
      }).join("\n");
  });
});
