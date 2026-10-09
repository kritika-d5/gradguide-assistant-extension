// Clicking the toolbar icon opens the private side panel; the student never sees it. Caption
// listening (opt in) is the content script captions.js, which sends
// {type: "gg:propose", field, value, evidence} messages that the panel shows as pending chips.
chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((err) => console.error("GradGuide: could not set side panel behaviour", err));

// Caption listening is opt in per session: switch it off whenever the browser starts.
chrome.runtime.onStartup.addListener(() => {
  chrome.storage.local.set({ ggListen: false, ggCaptionStatus: { state: "off", at: Date.now() } });
});
