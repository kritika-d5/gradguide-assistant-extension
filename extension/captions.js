// Caption listener for Google Meet. Opt in: does nothing until the counsellor turns on
// "Listen to captions" in the side panel.
//
// It only *reads* the live captions Meet already shows the counsellor (turn them on with CC or
// the "c" key). It never changes the Meet page, never joins the call, and makes no network
// requests itself: values found by the in browser rules and the caption lines go to the side
// panel, where values wait as pending chips for the counsellor to accept. Caption text reaches
// the backend's model only if the counsellor also turns on "AI reading" in the panel.
//
// Meet's class names are obfuscated and change often, so the captions area is found by its ARIA
// role and label instead. If Meet changes that, the panel shows "waiting for captions" and manual
// entry keeps working.

(() => {
  "use strict";

  const POLL_MS = 1000; // how often to look for the captions area
  const QUIET_MS = 1200; // wait for Meet to finish revising a line before reading it
  const MAX_SEEN = 2000;

  const extractor = globalThis.GGExtract;
  let enabled = false;
  let region = null;
  let regionObserver = null;
  let pollTimer = null;
  let quietTimer = null;
  let status = "off";
  const seen = new Set(); // caption lines already processed
  const sent = new Set(); // field + value pairs already proposed

  function findRegion() {
    return (
      document.querySelector('[role="region"][aria-label*="caption" i]') ||
      document.querySelector('[aria-live][aria-label*="caption" i]')
    );
  }

  function setStatus(next) {
    if (next === status) return;
    status = next;
    chrome.storage.local.set({ ggCaptionStatus: { state: next, at: Date.now() } }).catch(() => {});
  }

  function propose(item) {
    const key = item.field + JSON.stringify(item.value);
    if (sent.has(key)) return;
    sent.add(key);
    chrome.runtime
      .sendMessage({ type: "gg:propose", field: item.field, value: item.value, evidence: item.evidence, note: item.note, source: "caption" })
      .catch(() => sent.delete(key)); // panel closed: allow a retry when the line is heard again
  }

  /** Read the captions area as lines, skipping anything already processed. */
  function readNewLines() {
    if (!region) return [];
    const lines = region.innerText.split("\n").map((l) => l.trim()).filter((l) => l.length > 2);
    const fresh = lines.filter((l) => !seen.has(l));
    if (seen.size > MAX_SEEN) seen.clear();
    fresh.forEach((l) => seen.add(l));
    return fresh;
  }

  let recent = []; // last few lines, so a question and its answer are read together

  function processCaptions() {
    const fresh = readNewLines();
    if (!fresh.length) return;
    // Values already proposed are not sent twice, so re-reading recent lines is harmless.
    for (const item of extractor.extract([...recent, ...fresh].join("\n"))) propose(item);
    recent = [...recent, ...fresh].slice(-3);
    // The panel keeps a rolling transcript for the optional model assisted pass. It only leaves
    // the browser if the counsellor turns on "AI reading" in the panel.
    chrome.runtime.sendMessage({ type: "gg:transcript", lines: fresh }).catch(() => {});
  }

  function attach(found) {
    detach();
    region = found;
    regionObserver = new MutationObserver(() => {
      clearTimeout(quietTimer);
      quietTimer = setTimeout(processCaptions, QUIET_MS);
    });
    regionObserver.observe(region, { childList: true, subtree: true, characterData: true });
    setStatus("listening");
    processCaptions();
  }

  function detach() {
    regionObserver?.disconnect();
    regionObserver = null;
    region = null;
    clearTimeout(quietTimer);
  }

  function poll() {
    if (!enabled) return;
    if (region && region.isConnected) return;
    const found = findRegion();
    if (found) attach(found);
    else {
      detach();
      setStatus("waiting");
    }
  }

  function start() {
    if (enabled || !extractor) return;
    enabled = true;
    setStatus("waiting");
    poll();
    pollTimer = setInterval(poll, POLL_MS);
  }

  function stop() {
    enabled = false;
    clearInterval(pollTimer);
    detach();
    setStatus("off");
  }

  chrome.storage.local.get("ggListen").then(({ ggListen }) => (ggListen ? start() : setStatus("off")));
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes.ggListen) return;
    changes.ggListen.newValue ? start() : stop();
  });
  addEventListener("pagehide", () => setStatus("off"));
})();
