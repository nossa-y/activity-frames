/*
 * Nocta replay executor - the deterministic grounding ladder, as real code.
 *
 * Given a compiled routine plan, it locates each target and acts, with NO
 * vision model and NO screenshots. Grounding is tried in tiers:
 *   tier 1 (accessibility): aria-label / role + accessible-name / native control
 *   tier 2 (text):          visible text / placeholder / value match
 *   deopt:                  neither tier found it -> hand back for a vision step
 *
 * Safety is enforced at execution time: elements whose accessible name matches a
 * destructive verb (Send, Post, Publish, Connect, Delete, Pay, ...) are BLOCKED
 * unless {allowDestructive:true} is passed. Replay never sends by default.
 *
 * Pure DOM only - no extension APIs - so it runs identically in a content
 * script and in a headless test (see test/run_headless.mjs).
 */
(function (root) {
  "use strict";

  var DESTRUCTIVE = /\b(send|post|publish|share|connect|delete|remove|pay|buy|submit|confirm|transfer|archive|discard|checkout|purchase|withdraw|tweet)\b/i;

  function isVisible(el) {
    if (!el || !el.getClientRects) return false;
    if (el.getClientRects().length === 0) return false;
    var s = root.getComputedStyle ? root.getComputedStyle(el) : null;
    if (s && (s.visibility === "hidden" || s.display === "none" || parseFloat(s.opacity) === 0)) return false;
    return true;
  }

  function textOf(el) {
    return (el.textContent || "").replace(/\s+/g, " ").trim();
  }

  // Simplified accessible-name computation (WAI-ARIA subset, good enough for v1).
  function accessibleName(el) {
    if (!el) return "";
    var byLabel = el.getAttribute && el.getAttribute("aria-label");
    if (byLabel) return byLabel.trim();
    var labelledby = el.getAttribute && el.getAttribute("aria-labelledby");
    if (labelledby && el.ownerDocument) {
      var parts = labelledby.split(/\s+/).map(function (id) {
        var n = el.ownerDocument.getElementById(id);
        return n ? textOf(n) : "";
      });
      var joined = parts.join(" ").trim();
      if (joined) return joined;
    }
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
      if (el.placeholder) return el.placeholder.trim();
      if (el.name) return el.name.trim();
    }
    if (el.getAttribute && el.getAttribute("title")) return el.getAttribute("title").trim();
    if (el.getAttribute && el.getAttribute("alt")) return el.getAttribute("alt").trim();
    var t = textOf(el);
    return t.length <= 80 ? t : t.slice(0, 80);
  }

  function norm(s) { return (s || "").replace(/\s+/g, " ").trim().toLowerCase(); }

  function eq(a, b) { return norm(a) === norm(b) && norm(a).length > 0; }
  function contains(a, b) {
    a = norm(a); b = norm(b);
    return b.length > 0 && a.indexOf(b) !== -1;
  }

  var INTERACTIVE = "a,button,input,textarea,select,[role],[contenteditable],[tabindex],[onclick]";

  // Tier 1: accessibility grounding.
  function tier1(doc, target, role) {
    // 1a. exact aria-label
    var byAria = Array.prototype.slice.call(doc.querySelectorAll("[aria-label]"))
      .filter(function (el) { return isVisible(el) && eq(el.getAttribute("aria-label"), target); });
    if (byAria.length) return { el: byAria[0], method: "aria-label-exact" };
    // 1b. role (aria role or native) + accessible name match
    var cands = Array.prototype.slice.call(doc.querySelectorAll(INTERACTIVE))
      .filter(isVisible);
    var roleMatch = cands.filter(function (el) {
      var r = (el.getAttribute && el.getAttribute("role")) || nativeRole(el);
      var nameOk = eq(accessibleName(el), target);
      var roleOk = !role || norm(r) === norm(role) || norm(r) === norm(ariaFromNative(role));
      return nameOk && roleOk;
    });
    if (roleMatch.length) return { el: roleMatch[0], method: "role+name" };
    // 1c. accessible name exact, any interactive
    var nameOnly = cands.filter(function (el) { return eq(accessibleName(el), target); });
    if (nameOnly.length) return { el: nameOnly[0], method: "acc-name" };
    // 1d. aria-label contains
    var ariaContains = Array.prototype.slice.call(doc.querySelectorAll("[aria-label]"))
      .filter(function (el) { return isVisible(el) && contains(el.getAttribute("aria-label"), target); });
    if (ariaContains.length) return { el: ariaContains[0], method: "aria-label-contains" };
    return null;
  }

  // Tier 2: visible-text grounding (the OCR-equivalent inside the DOM).
  function tier2(doc, target) {
    var cands = Array.prototype.slice.call(doc.querySelectorAll(INTERACTIVE)).filter(isVisible);
    var exact = cands.filter(function (el) { return eq(textOf(el), target) || eq(el.value, target) || eq(el.placeholder, target); });
    if (exact.length) return { el: exact[0], method: "text-exact" };
    var partial = cands
      .filter(function (el) { return contains(textOf(el), target); })
      .sort(function (a, b) { return textOf(a).length - textOf(b).length; }); // smallest container wins
    if (partial.length) return { el: partial[0], method: "text-contains" };
    return null;
  }

  function nativeRole(el) {
    switch (el.tagName) {
      case "A": return "link";
      case "BUTTON": return "button";
      case "INPUT": return (el.type === "text" || el.type === "search" || el.type === "email") ? "textbox" : el.type;
      case "TEXTAREA": return "textbox";
      case "SELECT": return "combobox";
      default: return el.isContentEditable ? "textbox" : "";
    }
  }
  function ariaFromNative(r) {
    var m = { AXButton: "button", AXTextField: "textbox", AXStaticText: "text", AXLink: "link" };
    return m[r] || r;
  }

  function locate(doc, step) {
    var target = step.target || (step.guard && step.guard.expect_element) || "";
    var role = step.role || (step.guard && step.guard.expect_role) || "";
    var t1 = tier1(doc, target, role);
    if (t1) return { el: t1.el, tier: 1, method: t1.method };
    var t2 = tier2(doc, target);
    if (t2) return { el: t2.el, tier: 2, method: t2.method };
    return { el: null, tier: 0, method: "not-found" };
  }

  function typeInto(el, value) {
    el.focus();
    if (el.isContentEditable) {
      el.textContent = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      var proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      var setter = Object.getOwnPropertyDescriptor(proto, "value");
      if (setter && setter.set) setter.set.call(el, value); else el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function clickEl(el) {
    el.scrollIntoView && el.scrollIntoView({ block: "center" });
    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach(function (type) {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: root }));
    });
  }

  // Run a whole plan. opts: { allowDestructive, dryRun }
  function runPlan(doc, plan, opts) {
    opts = opts || {};
    var steps = [];
    for (var i = 0; i < plan.length; i++) {
      var step = plan[i];
      var loc = locate(doc, step);
      var name = loc.el ? accessibleName(loc.el) : (step.target || "");
      var rec = { i: i, op: step.op, target: step.target, tier: loc.tier, method: loc.method,
                  found: !!loc.el, ok: false, deopt: false, blocked: false };

      if (!loc.el) { rec.deopt = true; steps.push(rec); continue; }         // -> vision fallback

      var destructive = DESTRUCTIVE.test(name) || (step.op === "click" && DESTRUCTIVE.test(step.target || ""));
      if (destructive && !opts.allowDestructive) {
        rec.blocked = true; rec.reason = "destructive verb blocked by safety (" + name + ")";
        steps.push(rec); continue;
      }
      // guard verification: the located element's role/name should match the plan's guard
      var guard = step.guard || {};
      var nameGuardOk = !guard.expect_element || contains(name, guard.expect_element) || contains(guard.expect_element, name);
      rec.guard_ok = nameGuardOk;

      if (!opts.dryRun) {
        try {
          if (step.op === "type") typeInto(loc.el, step.value != null ? step.value : "");
          else clickEl(loc.el);
          rec.ok = true;
        } catch (e) { rec.error = String(e); }
      } else { rec.ok = nameGuardOk; }
      steps.push(rec);
    }
    var summary = {
      total: steps.length,
      tier1: steps.filter(function (s) { return s.tier === 1; }).length,
      tier2: steps.filter(function (s) { return s.tier === 2; }).length,
      deopt: steps.filter(function (s) { return s.deopt; }).length,
      blocked: steps.filter(function (s) { return s.blocked; }).length,
      acted: steps.filter(function (s) { return s.ok; }).length,
      grounded_no_vision: steps.filter(function (s) { return s.tier === 1 || s.tier === 2; }).length,
    };
    summary.guard_coverage = summary.total ? +(summary.grounded_no_vision / summary.total).toFixed(2) : 0;
    return { steps: steps, summary: summary };
  }

  var api = { runPlan: runPlan, locate: locate, accessibleName: accessibleName, DESTRUCTIVE: DESTRUCTIVE };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.NoctaExecutor = api;
})(typeof window !== "undefined" ? window : globalThis);
