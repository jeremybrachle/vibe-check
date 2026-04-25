const byId = (id) => document.getElementById(id);
let pageMode = "data";
let allSnapshotIds = [];
let currentSnapIdx = 0;
let activeDashboard = "hn";
let activeSignalSource = "hackernews";
let activeResearchTab = "local";
let currentProvider = "none";
let currentDigest = null;
let topStoriesExpanded = false;
let metricTimeseriesPoints = [];
let runProgressTimer = null;
let runProgressState = null;
let queueProgressTimer = null;
let queueProgressState = null;
let clockTimer = null;
let schedulerWatchTimer = null;
let scheduledRunWatch = null;
let schedulerOverviewTimer = null;
let llmVibeLoadToken = 0;
const llmVibeCache = {};

const RUN_ESTIMATE_KEY = "vibe-check-run-estimates-ms";
const CLOCK_TZ_KEY = "vibe-check-clock-tz";
const SCHED_OVERVIEW_HIDDEN_KEY = "vibe-check-scheduler-overview-hidden";
const SCHED_PANEL_VISIBLE_KEY = "vibe-check-scheduler-panel-visible";
const DOTS_HIDDEN_KEY = "vibe-check-dots-hidden";
const AI_AUTO_KEY = "vibe-check-ai-auto-mode";
const APP_VERSION = "v1.0.0";
const CHANGELOG_ENTRIES = [
  {
    version: "v1.0.0",
    date: "2026-04-25",
    notes: [
      "Strict refresh cadence: every 2 hours + 9:01 AM ET + 5:01 PM PT.",
      "Manual user refresh removed from main dashboards.",
      "Local-only admin override added in Settings.",
    ],
  },
];

function labelRunOrigin(origin) {
  if (origin === "scheduled") return "scheduled";
  if (origin === "super_manual") return "admin override";
  if (origin === "queued_manual") return "queued manual";
  return "manual";
}

function updateVersionMeta(digest = null) {
  const el = byId("versionMeta");
  if (!el) {
    return;
  }
  if (!digest) {
    el.textContent = `${APP_VERSION} • snapshot unavailable • waiting for first scheduled run`;
    return;
  }
  const refreshed = formatInSelectedTimezone(digest.created_at, { withZone: true });
  const versionTag = `${digest.kind || "regular"} #${digest.id || "-"}`;
  el.textContent = `${APP_VERSION} • snapshot ${versionTag} • last refreshed ${refreshed}`;
}

function renderChangelog() {
  const list = byId("changelogList");
  if (!list) {
    return;
  }
  list.innerHTML = "";
  for (const entry of CHANGELOG_ENTRIES) {
    const li = document.createElement("li");
    const notes = (entry.notes || []).join(" ");
    li.textContent = `${entry.version} (${entry.date}) — ${notes}`;
    list.appendChild(li);
  }
}

function getActiveSource() {
  return activeSignalSource;
}

function sourceQueryParam(source = null) {
  const value = source || getActiveSource();
  return `source=${encodeURIComponent(value)}`;
}

function signalDashboardActive() {
  return activeDashboard === "hn" || activeDashboard === "reddit";
}

function refreshButtonDefaultLabel() {
  if (activeDashboard === "research") return "Refresh";
  if (activeDashboard === "settings") return "Refresh status";
  return "Queue refresh";
}

function setHeaderRefreshVisible(visible) {
  const refreshBtn = byId("refreshBtn");
  if (!refreshBtn) {
    return;
  }
  refreshBtn.hidden = !visible;
}

function setCancelRefreshVisible(visible) {
  const btn = byId("cancelRefreshBtn");
  if (!btn) {
    return;
  }
  btn.hidden = !visible;
}

function setQueueRefreshLabel(text) {
  const el = byId("queueRefreshLabel");
  if (!el) {
    return;
  }
  const value = String(text || "").trim();
  el.hidden = !value;
  el.textContent = value;
}

function clearQueueProgress() {
  if (queueProgressTimer) {
    clearInterval(queueProgressTimer);
    queueProgressTimer = null;
  }
  queueProgressState = null;
}

function showQueuedRefreshProgress(secondsUntilDue, queueWindowMinutes = 20, clickCount = 0, threshold = 10) {
  const wrap = byId("runProgressWrap");
  const bar = byId("runProgressBar");
  const label = byId("runProgressLabel");
  const stage = byId("runProgressStage");
  const eta = byId("runProgressEta");
  if (!wrap || !bar || !label || !stage || !eta) {
    return;
  }

  const totalSec = Math.max(1, Number(queueWindowMinutes || 20) * 60);
  const remaining = Math.max(0, Number(secondsUntilDue || 0));
  queueProgressState = {
    totalSec,
    remainingSec: remaining,
    clickCount: Number(clickCount) || 0,
    threshold: Number(threshold) || 10,
  };

  const render = () => {
    if (!queueProgressState) {
      return;
    }
    const elapsed = Math.max(0, queueProgressState.totalSec - queueProgressState.remainingSec);
    const pct = Math.min(100, Math.max(0, Math.floor((elapsed / queueProgressState.totalSec) * 100)));
    wrap.hidden = false;
    label.textContent = "Queued refresh window";
    const cnt = queueProgressState.clickCount || 0;
    const thr = queueProgressState.threshold || 10;
    const needed = thr - cnt;
    if (cnt > 0 && needed > 0) {
      stage.textContent = `${cnt}/${thr} — click ${needed} more time${needed === 1 ? "" : "s"} to skip the queue now`;
    } else {
      stage.textContent = `Waiting — click ${thr} times total to skip immediately`;
    }
    eta.classList.remove("overrun");
    eta.textContent = `starts in ~${formatWaitLabel(queueProgressState.remainingSec)}`;
    bar.style.width = `${pct}%`;
  };

  render();
  if (queueProgressTimer) {
    clearInterval(queueProgressTimer);
  }
  queueProgressTimer = setInterval(() => {
    if (!queueProgressState) {
      return;
    }
    queueProgressState.remainingSec = Math.max(0, queueProgressState.remainingSec - 1);
    render();
    if (queueProgressState.remainingSec <= 0) {
      clearQueueProgress();
    }
  }, 1000);
}

function formatWaitLabel(seconds) {
  const total = Number(seconds) || 0;
  if (total < 60) {
    return `${total}s`;
  }
  const mins = Math.floor(total / 60);
  const rem = total % 60;
  if (mins < 60) {
    return `${mins}m ${rem}s`;
  }
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${hours}h ${remMins}m`;
}

async function syncManualQueueUi() {
  const showForPage = signalDashboardActive();
  if (!showForPage) {
    setCancelRefreshVisible(false);
    setQueueRefreshLabel("");
    clearQueueProgress();
    return;
  }
  try {
    const overview = await fetchJson("/api/v1/admin/scheduler/overview");
    const queue = overview.manual_queue || {};
    const pending = Boolean(queue.pending);
    setCancelRefreshVisible(pending);

    if (pending) {
      const due = formatWaitLabel(queue.seconds_until_due || 0);
      setQueueRefreshLabel(`Queued refresh: ${queue.trigger_count || 0}/${queue.threshold || 10} • starts in ~${due}`);
      showQueuedRefreshProgress(queue.seconds_until_due || 0, queue.window_minutes || 20, queue.trigger_count || 0, queue.threshold || 10);
      return;
    }

    if (queue.running) {
      setQueueRefreshLabel("Queued refresh started and is running.");
      clearQueueProgress();
      return;
    }

    const count = queue.trigger_count || 0;
    const threshold = queue.threshold || 10;
    if (count > 0 && count < threshold) {
      setQueueRefreshLabel(`${count}/${threshold} toward override — click Refresh to continue`);
      clearQueueProgress();
      return;
    }

    if (queue.used_refreshes >= queue.max_refreshes && queue.seconds_until_next_window > 0) {
      setQueueRefreshLabel(`Manual refresh limit active: next window in ~${formatWaitLabel(queue.seconds_until_next_window)}.`);
      clearQueueProgress();
      return;
    }

    setQueueRefreshLabel("");
    clearQueueProgress();
  } catch {
    setCancelRefreshVisible(false);
    setQueueRefreshLabel("");
    clearQueueProgress();
  }
}

async function cancelQueuedRefresh() {
  const btn = byId("cancelRefreshBtn");
  if (!btn || btn.disabled) {
    return;
  }
  btn.disabled = true;
  try {
    const result = await fetchJson("/api/v1/admin/refresh/queue/cancel", { method: "POST" });
    if (result.status === "canceled") {
      clearLoadingState();
      setCancelRefreshVisible(false);
    } else if (result.status === "already_running") {
      setLoadingState("Refresh already started. It cannot be canceled and still counts toward the 2-hour limit.");
      setCancelRefreshVisible(false);
    } else {
      setLoadingState("No queued refresh to cancel.");
      setCancelRefreshVisible(false);
    }
  } catch (error) {
    setLoadingState(`Cancel failed: ${error.message}`);
  } finally {
    btn.disabled = false;
    await syncManualQueueUi();
  }
}

function setOutsideCronBadge(id, isScheduled) {
  const el = byId(id);
  if (!el) {
    return;
  }
  el.classList.toggle("scheduled", isScheduled);
}

function onClick(id, handler) {
  const el = byId(id);
  if (!el) {
    return;
  }
  el.addEventListener("click", handler);
}

function setText(id, value) {
  const el = byId(id);
  if (!el) {
    return;
  }
  el.textContent = value;
}

function pickTheme() {
  return localStorage.getItem("vibe-check-theme") || "light";
}

function applyTheme(theme) {
  document.body.setAttribute("data-theme", theme);
  byId("themeBtn").textContent = theme === "dark" ? "Light mode" : "Dark mode";
}

function toggleTheme() {
  const next = pickTheme() === "dark" ? "light" : "dark";
  localStorage.setItem("vibe-check-theme", next);
  applyTheme(next);
}


async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const text = await response.text();
    const err = new Error(`${response.status}: ${text}`);
    err.status = response.status;
    throw err;
  }
  return response.json();
}

function renderList(element, items, mapper) {
  element.innerHTML = "";
  if (!items || items.length === 0) {
    element.innerHTML = "<li>No data yet.</li>";
    return;
  }
  for (const item of items) {
    const li = document.createElement("li");
    li.textContent = mapper(item);
    element.appendChild(li);
  }
}

function decodeHtmlEntities(value) {
  if (!value) {
    return "";
  }
  const txt = document.createElement("textarea");
  txt.innerHTML = value;
  return txt.value;
}

function renderLinks(element, items) {
  element.innerHTML = "";
  if (!items || items.length === 0) {
    element.innerHTML = "No links yet.";
    return;
  }

  for (const item of items) {
    const wrapper = document.createElement("div");
    wrapper.className = "link-item";

    const titleRow = document.createElement("div");
    titleRow.className = "link-title-row";
    const titleText = document.createElement("span");
    titleText.className = "link-title-text";
    titleText.textContent = `"${item.title}"`;
    const scoreMeta = document.createElement("span");
    scoreMeta.className = "link-score-meta";
    scoreMeta.textContent = ` \u2014 ${item.score}\u00a0pts \u00b7 ${item.comments}\u00a0comments`;
    titleRow.appendChild(titleText);
    titleRow.appendChild(scoreMeta);
    wrapper.appendChild(titleRow);

    const ul = document.createElement("ul");
    ul.className = "link-bullets";

    if (item.reason) {
      const li = document.createElement("li");
      li.className = "link-reason";
      li.textContent = decodeHtmlEntities(item.reason);
      ul.appendChild(li);
    }

    const showAiArticle = currentProvider !== "none";
    const articleText = showAiArticle
      ? decodeHtmlEntities(item.article_summary_ai || item.article_summary)
      : decodeHtmlEntities(item.article_summary);
    if (articleText) {
      const li = document.createElement("li");
      li.className = "link-summary";
      li.textContent = showAiArticle && item.article_summary_ai ? `Article (AI): ${articleText}` : `Article: ${articleText}`;
      ul.appendChild(li);
    }

    const commentsText = decodeHtmlEntities(item.comments_summary);
    if (commentsText) {
      const li = document.createElement("li");
      li.className = "link-summary";
      li.textContent = `Discussion: ${commentsText}`;
      ul.appendChild(li);
    }

    const readLi = document.createElement("li");
    readLi.className = "link-read";
    const readA = document.createElement("a");
    readA.href = item.url || "https://news.ycombinator.com/";
    readA.target = "_blank";
    readA.rel = "noreferrer noopener";
    readA.textContent = "\u2192 read article";
    readLi.appendChild(readA);
    ul.appendChild(readLi);

    wrapper.appendChild(ul);
    element.appendChild(wrapper);
  }
}

function storyKey(item) {
  const url = String(item?.url || "").trim().toLowerCase();
  const title = String(item?.title || "").trim().toLowerCase();
  return url || title;
}

function renderTopStories(element, stories, rabbitCandidates) {
  element.innerHTML = "";
  if (!stories || !stories.length) {
    element.innerHTML = "No links yet.";
    return;
  }

  const rabbitSet = new Set((rabbitCandidates || []).map((x) => storyKey(x)).filter(Boolean));
  const visibleStories = topStoriesExpanded ? stories : stories.slice(0, 3);

  for (const item of visibleStories) {
    const wrapper = document.createElement("div");
    wrapper.className = "link-item";

    const titleRow = document.createElement("div");
    titleRow.className = "link-title-row";
    const titleText = document.createElement("span");
    titleText.className = "link-title-text";
    titleText.textContent = `"${item.title}"`;
    titleRow.appendChild(titleText);

    if (rabbitSet.has(storyKey(item))) {
      const badge = document.createElement("span");
      badge.className = "story-badge";
      badge.textContent = "Rabbit-hole candidate";
      badge.title = "A story likely to open many tabs. High comment-to-score ratio suggests deep community interest and branching discussions — worth more than a skim.";
      titleRow.appendChild(badge);
    }

    const scoreMeta = document.createElement("span");
    scoreMeta.className = "link-score-meta";
    const comments = item.comments ?? item.comment_count ?? 0;
    scoreMeta.textContent = ` — ${item.score ?? 0} pts · ${comments} comments`;
    titleRow.appendChild(scoreMeta);
    wrapper.appendChild(titleRow);

    const ul = document.createElement("ul");
    ul.className = "link-bullets";

    const articleText = currentProvider !== "none"
      ? decodeHtmlEntities(item.article_summary_ai || item.article_summary)
      : decodeHtmlEntities(item.article_summary);
    if (articleText) {
      const li = document.createElement("li");
      li.className = "link-summary";
      li.textContent = articleText;
      ul.appendChild(li);
    }

    const readLi = document.createElement("li");
    readLi.className = "link-read";
    const readA = document.createElement("a");
    readA.href = item.url || "https://news.ycombinator.com/";
    readA.target = "_blank";
    readA.rel = "noreferrer noopener";
    readA.textContent = "→ read article";
    readLi.appendChild(readA);
    ul.appendChild(readLi);

    wrapper.appendChild(ul);
    element.appendChild(wrapper);
  }
}

function renderSnapshotArchive(element, items) {
  element.innerHTML = "";
  if (!items || items.length === 0) {
    element.innerHTML = "<li>No data yet.</li>";
    return;
  }

  for (const item of items) {
    const li = document.createElement("li");

    const chipRow = document.createElement("div");
    chipRow.className = "snapshot-chip-row";

    const chip = document.createElement("span");
    chip.className = `snapshot-chip ${item.kind || "regular"}`;
    chip.textContent = item.kind || "regular";

    const provider = document.createElement("span");
    provider.className = "scheduler-kind";
    provider.textContent = `${item.llm_provider || "none"} • ${labelRunOrigin(item.run_origin)}`;

    chipRow.appendChild(chip);
    chipRow.appendChild(provider);

    const when = document.createElement("div");
    when.className = "snapshot-when";
    when.textContent = `${formatInSelectedTimezone(item.created_at, { withZone: true })} • ${item.item_count} items`;

    li.title = `Open full snapshot #${item.id}`;
    li.addEventListener("click", () => {
      openSnapshotDetailsModal(item.id).catch((error) => {
        setLoadingState(`Failed to load snapshot details: ${error.message}`);
      });
    });

    li.appendChild(chipRow);
    li.appendChild(when);
    element.appendChild(li);
  }
}

function closeSnapshotDetailsModal() {
  const modal = byId("snapshotModal");
  if (!modal) {
    return;
  }
  modal.hidden = true;
}

function formatSnapshotDetailText(payload) {
  const data = payload?.data || {};
  const lines = [];
  lines.push(`Snapshot #${payload.id} (${payload.kind})`);
  lines.push(`Created: ${formatInSelectedTimezone(payload.created_at, { withZone: true })}`);
  lines.push(`Provider: ${payload.llm_provider || "none"}`);
  lines.push(`Origin: ${payload.run_origin || "manual"}`);
  lines.push(`Sources: ${(payload.sources || []).join(", ") || "none"}`);
  lines.push(`Items: ${payload.item_count ?? 0}`);
  lines.push("");

  const summary = normalizeDisplayText(payload.ai_summary || "");
  lines.push("AI summary:");
  lines.push(summary || "(none)");
  lines.push("");

  lines.push("Top stories:");
  for (const story of (payload.top_links || [])) {
    lines.push(`- ${story.title} (${story.score} pts, ${story.comments} comments)`);
  }
  if (!(payload.top_links || []).length) {
    lines.push("- none");
  }
  lines.push("");

  lines.push("Themes:");
  for (const theme of (payload.today_themes || [])) {
    lines.push(`- ${theme.topic} (${theme.count})`);
  }
  if (!(payload.today_themes || []).length) {
    lines.push("- none");
  }
  lines.push("");

  lines.push("Tools:");
  for (const tool of (payload.most_mentioned_tools || [])) {
    lines.push(`- ${tool.name} (${tool.count})`);
  }
  if (!(payload.most_mentioned_tools || []).length) {
    lines.push("- none");
  }
  lines.push("");

  lines.push("Raw payload JSON:");
  lines.push(JSON.stringify(data, null, 2));
  return lines.join("\n");
}

async function openSnapshotDetailsModal(snapshotId) {
  const modal = byId("snapshotModal");
  const meta = byId("snapshotModalMeta");
  const body = byId("snapshotModalBody");
  if (!modal || !meta || !body) {
    return;
  }

  modal.hidden = false;
  meta.textContent = `Loading snapshot #${snapshotId}...`;
  body.textContent = "Loading...";

  try {
    const payload = await fetchJson(`/api/v1/digest/${snapshotId}/full`);
    meta.textContent = `Snapshot #${payload.id} • ${payload.kind} • ${formatInSelectedTimezone(payload.created_at, { withZone: true })}`;
    body.textContent = formatSnapshotDetailText(payload);
  } catch (error) {
    meta.textContent = `Failed to load snapshot #${snapshotId}`;
    body.textContent = `Error: ${error.message}`;
  }
}

function renderEndpointLinks(element, lines) {
  element.innerHTML = "";
  for (const line of lines) {
    const row = document.createElement("a");
    row.href = line.url;
    row.textContent = line.label;
    row.target = "_blank";
    row.rel = "noreferrer noopener";
    element.appendChild(row);
  }
}

function setLoadingState(text) {
  if (activeDashboard !== "settings") {
    return;
  }
  const line = byId("statusLine");
  if (!line) {
    return;
  }
  const value = String(text || "").trim();
  line.hidden = !value;
  line.textContent = value;
}

function clearLoadingState() {
  const line = byId("statusLine");
  if (!line) {
    return;
  }
  line.hidden = true;
  line.textContent = "";
}

function getSelectedTimezone() {
  return byId("clockTzSelect")?.value || pickClockTimezone();
}

function parseServerDate(value) {
  if (value instanceof Date) {
    return value;
  }
  const raw = String(value || "").trim();
  if (!raw) {
    return new Date(NaN);
  }

  // Backend timestamps are UTC but may omit timezone offset.
  const hasZone = /[zZ]|[+-]\d{2}:\d{2}$/.test(raw);
  return new Date(hasZone ? raw : `${raw}Z`);
}

function formatInSelectedTimezone(value, options = {}) {
  const date = parseServerDate(value);
  if (Number.isNaN(date.getTime())) {
    return "-";
  }
  return new Intl.DateTimeFormat("en-US", {
    timeZone: getSelectedTimezone(),
    year: options.dateOnly ? "numeric" : undefined,
    month: options.dateOnly ? "2-digit" : undefined,
    day: options.dateOnly ? "2-digit" : undefined,
    hour: options.dateOnly ? undefined : "2-digit",
    minute: options.dateOnly ? undefined : "2-digit",
    second: options.withSeconds ? "2-digit" : undefined,
    timeZoneName: options.withZone ? "short" : undefined,
    hour12: options.dateOnly ? undefined : true,
  }).format(date);
}

function formatStoryCount(value) {
  const count = Number(value);
  if (!Number.isFinite(count) || count < 0) {
    return "";
  }
  return `${count} ${count === 1 ? "story" : "stories"}`;
}

function formatSnapshotMeta(digest, prefix = "Updated") {
  if (!digest) {
    return "No data yet.";
  }

  const parts = [`${prefix} ${formatInSelectedTimezone(digest.created_at)}`];
  const countLabel = formatStoryCount(digest.item_count);
  if (countLabel) {
    parts.push(countLabel);
  }
  return parts.join(" • ");
}

function normalizeDisplayText(value) {
  const text = (value || "").trim();
  if (!text) {
    return "";
  }
  return text
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/__(.*?)__/g, "$1")
    .replace(/`(.*?)`/g, "$1")
    .replace(/^[\-*]\s+/gm, "")
    .trim();
}

function summaryFallbackText(digestLike) {
  if (!digestLike || !digestLike.llm_provider || digestLike.llm_provider === "none") {
    return "AI summary is off for this run.";
  }
  return "No summary for this snapshot.";
}

function getProviderPlaceholder(provider) {
  if (provider === "none") {
    return "No AI selected";
  }
  return null;
}

function shouldShowPlaceholder(provider) {
  return provider === "none";
}

function providerMatchesSelection(selection, snapshotProvider) {
  if (selection === "none") {
    return snapshotProvider === "none";
  }
  return snapshotProvider && snapshotProvider !== "none";
}

function normalizeProviderMode(provider) {
  if (provider === "none" || provider === "heuristic") return "none";
  if (provider === "ollama") return "ollama";
  if (provider === "openai" || provider === "cloud") return "openai";
  if (provider === "auto") return getAutoTargetProvider();
  return "none";
}

async function findLatestDigestForProvider(provider, kind = null) {
  const kindQuery = kind ? `&kind=${kind}` : "";
  const items = await fetchJson(`/api/v1/digest?limit=120&${sourceQueryParam()}${kindQuery}`);
  const match = items.find((item) => providerMatchesSelection(provider, item.llm_provider));
  if (!match) {
    return null;
  }
  return fetchJson(`/api/v1/digest/${match.id}`);
}

function formatEta(msLeft) {
  const sec = Math.max(0, Math.ceil(msLeft / 1000));
  if (sec < 60) {
    return `~${sec}s remaining`;
  }
  const min = Math.floor(sec / 60);
  const remSec = sec % 60;
  return `~${min}m ${remSec}s remaining`;
}

function formatOverrun(ms) {
  const sec = Math.max(0, Math.ceil(ms / 1000));
  if (sec < 60) {
    return `${sec}s`;
  }
  const min = Math.floor(sec / 60);
  const remSec = sec % 60;
  return `${min}m ${remSec}s`;
}

function readRunEstimateMap() {
  try {
    const raw = localStorage.getItem(RUN_ESTIMATE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function writeRunEstimateMap(map) {
  localStorage.setItem(RUN_ESTIMATE_KEY, JSON.stringify(map));
}

function getRunEstimateMs(kindKey, fallbackMs) {
  const map = readRunEstimateMap();
  const value = Number(map[kindKey]);
  if (!Number.isFinite(value) || value < 5000) {
    return fallbackMs;
  }
  return value;
}

function rememberRunDuration(kindKey, durationMs, fallbackMs) {
  const map = readRunEstimateMap();
  const previous = Number(map[kindKey]);
  const base = Number.isFinite(previous) && previous > 0 ? previous : fallbackMs;
  // Exponential smoothing so estimates adapt without oscillating wildly.
  const next = Math.round((base * 0.65) + (durationMs * 0.35));
  map[kindKey] = Math.max(5000, next);
  writeRunEstimateMap(map);
}

function startRunProgress(kindKey, kindLabel, fallbackEstimateMs = 45000) {
  const wrap = byId("runProgressWrap");
  const bar = byId("runProgressBar");
  const label = byId("runProgressLabel");
  const stage = byId("runProgressStage");
  const eta = byId("runProgressEta");
  if (!wrap || !bar || !label || !stage || !eta) {
    return;
  }

  clearQueueProgress();

  const estimateMs = getRunEstimateMs(kindKey, fallbackEstimateMs);
  const startedAt = Date.now();
  const stages = [
    { cutoff: 0.18, text: "Pulling source feeds" },
    { cutoff: 0.45, text: "Scoring stories and building digest" },
    { cutoff: 0.78, text: "Generating provider summary" },
    { cutoff: 0.98, text: "Saving snapshot and finalizing" },
  ];

  label.textContent = `${kindLabel} in progress`;
  wrap.hidden = false;
  bar.style.width = "0%";
  eta.classList.remove("overrun");
  runProgressState = { kindKey, startedAt, estimateMs, fallbackEstimateMs };

  if (runProgressTimer) {
    clearInterval(runProgressTimer);
  }

  runProgressTimer = setInterval(() => {
    const elapsed = Date.now() - startedAt;
    const progress = Math.min(0.96, elapsed / estimateMs);
    bar.style.width = `${Math.floor(progress * 100)}%`;
    const currentStage = stages.find((x) => progress <= x.cutoff) || stages[stages.length - 1];
    stage.textContent = currentStage.text;
    if (elapsed <= estimateMs) {
      eta.textContent = formatEta(estimateMs - elapsed);
      eta.classList.remove("overrun");
    } else {
      eta.textContent = `+${formatOverrun(elapsed - estimateMs)} over`;
      eta.classList.add("overrun");
    }
  }, 300);
}

function finishRunProgress(message = "Completed") {
  const wrap = byId("runProgressWrap");
  const bar = byId("runProgressBar");
  const stage = byId("runProgressStage");
  const eta = byId("runProgressEta");
  if (!wrap || !bar || !stage || !eta) {
    return;
  }

  if (runProgressTimer) {
    clearInterval(runProgressTimer);
    runProgressTimer = null;
  }

  if (runProgressState) {
    const actualMs = Date.now() - runProgressState.startedAt;
    rememberRunDuration(runProgressState.kindKey, actualMs, runProgressState.fallbackEstimateMs);
    runProgressState = null;
  }

  bar.style.width = "100%";
  stage.textContent = message;
  eta.textContent = "done";
  eta.classList.remove("overrun");
  setTimeout(() => {
    wrap.hidden = true;
    syncManualQueueUi().catch(() => {});
  }, 1200);
}

function failRunProgress(message = "Failed") {
  const wrap = byId("runProgressWrap");
  const stage = byId("runProgressStage");
  const eta = byId("runProgressEta");
  if (!wrap || !stage || !eta) {
    return;
  }

  if (runProgressTimer) {
    clearInterval(runProgressTimer);
    runProgressTimer = null;
  }

  if (runProgressState) {
    const actualMs = Date.now() - runProgressState.startedAt;
    rememberRunDuration(runProgressState.kindKey, actualMs, runProgressState.fallbackEstimateMs);
    runProgressState = null;
  }

  stage.textContent = message;
  eta.textContent = "error";
  eta.classList.remove("overrun");
}

function resolveSummaryForProvider(digestLike, selectedProvider, actionHint = "Refresh now") {
  const provider = selectedProvider || currentProvider;
  if (shouldShowPlaceholder(provider)) {
    return {
      text: getProviderPlaceholder(provider),
      isPlaceholder: true,
    };
  }

  const snapshotProvider = digestLike?.llm_provider || "none";
  if (!providerMatchesSelection(provider, snapshotProvider)) {
    return {
      text: `AI mode is on. Click ${actionHint} to generate a new AI snapshot.`,
      isPlaceholder: true,
    };
  }

  if (digestLike?.ai_summary) {
    return {
      text: normalizeDisplayText(digestLike.ai_summary),
      isPlaceholder: false,
    };
  }

  return {
    text: summaryFallbackText(digestLike),
    isPlaceholder: true,
  };
}

function describeRelativeScore(label, score, values) {
  if (!Number.isFinite(score)) {
    return `${label}: no score available yet.`;
  }

  const sorted = (values || []).filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!sorted.length) {
    return `${label}: ${score.toFixed(2)}. Relative baseline builds as more snapshots arrive.`;
  }

  let rankCount = 0;
  for (const value of sorted) {
    if (value <= score) {
      rankCount += 1;
    }
  }
  const percentile = Math.round((rankCount / sorted.length) * 100);
  const min = sorted[0];
  const max = sorted[sorted.length - 1];
  return `${label}: ${score.toFixed(2)} (about ${percentile}th percentile, out of recent ${min.toFixed(2)}-${max.toFixed(2)} range).`;
}

async function updateMetricNotes(currentExcitement, currentSkepticism) {
  const exciteNote = byId("exciteNote");
  const skepticNote = byId("skepticNote");
  if (!exciteNote || !skepticNote) {
    return;
  }

  try {
    const ts = await fetchJson("/api/v1/metrics/timeseries?limit=40");
    const points = ts.points || [];
    metricTimeseriesPoints = points;
    const excitementValues = points.map((p) => p.excitement_score);
    const skepticismValues = points.map((p) => p.skepticism_score);

    exciteNote.dataset.detail = describeRelativeScore("Optimism signal", Number(currentExcitement), excitementValues);
    skepticNote.dataset.detail = describeRelativeScore("Skepticism signal", Number(currentSkepticism), skepticismValues);
    exciteNote.textContent = "Hover the score for trend context.";
    skepticNote.textContent = "Hover the score for trend context.";
  } catch {
    metricTimeseriesPoints = [];
    exciteNote.dataset.detail = `Optimism signal: ${Number(currentExcitement).toFixed(2)}. Relative baseline unavailable.`;
    skepticNote.dataset.detail = `Skepticism signal: ${Number(currentSkepticism).toFixed(2)}. Relative baseline unavailable.`;
    exciteNote.textContent = "Hover the score for trend context.";
    skepticNote.textContent = "Hover the score for trend context.";
  }
}

function renderPreviewSummary(summaryId, text, note = "") {
  const el = byId(summaryId);
  if (!el) {
    return;
  }
  const raw = normalizeDisplayText(text);
  if (!raw) {
    el.textContent = "";
    return;
  }

  const lines = raw.split("\n").map((line) => line.trim()).filter(Boolean);
  const predictionLines = lines.filter((line) => /^\d+\./.test(line));
  const intro = lines.find((line) => !/^\d+\./.test(line)) || "";

  if (!predictionLines.length) {
    el.textContent = note ? `${raw}\n\n${note}` : raw;
    return;
  }

  let html = "";
  if (intro) {
    html += `<p class=\"preview-update\">${intro}</p>`;
  }
  html += "<ul class=\"preview-list\">";
  for (const line of predictionLines) {
    html += `<li>${line.replace(/^\d+\.\s*/, "")}</li>`;
  }
  html += "</ul>";
  if (note) {
    html += `<p class="preview-note">${normalizeDisplayText(note)}</p>`;
  }
  el.innerHTML = html;
}


// ---------------------------------------------------------------------------
// Tag popover (topics + tools hover menus)
// ---------------------------------------------------------------------------

let _popoverCloseTimer = null;
let _scorePopoverCloseTimer = null;

function _allDigestLinks(digest) {
  if (!digest) return [];
  const seen = new Set();
  const out = [];
  for (const item of [
    ...(digest.top_links || []),
    ...(digest.excited_about || []),
    ...(digest.skeptical_about || []),
    ...(digest.best_rabbit_holes || []),
  ]) {
    const key = item.url || item.title;
    if (!seen.has(key)) {
      seen.add(key);
      out.push(item);
    }
  }
  return out;
}

function showTagPopover(anchorEl, title, links) {
  const pop = byId("tagPopover");
  if (!pop) return;

  if (_popoverCloseTimer) {
    clearTimeout(_popoverCloseTimer);
    _popoverCloseTimer = null;
  }

  pop.innerHTML = "";
  const heading = document.createElement("p");
  heading.className = "tag-popover-title";
  heading.textContent = title;
  pop.appendChild(heading);

  const ul = document.createElement("ul");
  ul.className = "tag-popover-list";

  if (!links.length) {
    const li = document.createElement("li");
    li.style.color = "color-mix(in srgb, var(--ink) 55%, transparent)";
    li.style.fontStyle = "italic";
    li.textContent = "No linked stories in this snapshot.";
    ul.appendChild(li);
  } else {
    for (const link of links) {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = link.url || "https://news.ycombinator.com/";
      a.target = "_blank";
      a.rel = "noreferrer noopener";
      a.textContent = link.title;
      li.appendChild(a);
      ul.appendChild(li);
    }
  }
  pop.appendChild(ul);

  // Position near anchor
  const rect = anchorEl.getBoundingClientRect();
  const spaceBelow = window.innerHeight - rect.bottom;
  pop.hidden = false;
  const popH = pop.offsetHeight;
  const popW = pop.offsetWidth;

  let top = spaceBelow >= popH + 8 ? rect.bottom + 6 : rect.top - popH - 6;
  let left = Math.min(rect.left, window.innerWidth - popW - 12);
  left = Math.max(8, left);
  top = Math.max(8, top);

  pop.style.top = `${top}px`;
  pop.style.left = `${left}px`;

  // Keep open while hovering the popover itself
  pop.onmouseenter = () => {
    if (_popoverCloseTimer) {
      clearTimeout(_popoverCloseTimer);
      _popoverCloseTimer = null;
    }
  };
  pop.onmouseleave = () => schedulePopoverClose();
}

function schedulePopoverClose() {
  _popoverCloseTimer = setTimeout(() => {
    const pop = byId("tagPopover");
    if (pop) pop.hidden = true;
    _popoverCloseTimer = null;
  }, 180);
}

function scoreSparklineSvg(values, color) {
  const width = 280;
  const height = 80;
  if (!values.length) {
    return `<svg viewBox="0 0 ${width} ${height}" width="100%" height="80" role="img" aria-label="No data"><text x="10" y="42" fill="currentColor" opacity="0.7" font-size="12">No trend data yet.</text></svg>`;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = values.map((v, idx) => {
    const x = (idx / Math.max(1, values.length - 1)) * (width - 16) + 8;
    const y = height - (((v - min) / span) * (height - 16) + 8);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");

  return `<svg viewBox="0 0 ${width} ${height}" width="100%" height="80" role="img" aria-label="Score trend"><rect x="0" y="0" width="${width}" height="${height}" fill="transparent" /><polyline fill="none" stroke="${color}" stroke-width="2.5" points="${points}"/></svg>`;
}

function showScorePopover(anchorEl, kind) {
  const pop = byId("scorePopover");
  if (!pop) return;

  if (_scorePopoverCloseTimer) {
    clearTimeout(_scorePopoverCloseTimer);
    _scorePopoverCloseTimer = null;
  }

  const label = kind === "excite" ? "Excitement score" : "Skepticism score";
  const color = kind === "excite" ? "var(--accent-2)" : "var(--accent)";
  const key = kind === "excite" ? "excitement_score" : "skepticism_score";
  const value = Number(currentDigest?.[key] ?? 0);
  const note = byId(kind === "excite" ? "exciteNote" : "skepticNote")?.dataset?.detail || "";
  const values = metricTimeseriesPoints.map((p) => Number(p[key])).filter((v) => Number.isFinite(v));

  pop.innerHTML = "";
  const heading = document.createElement("p");
  heading.className = "score-popover-title";
  heading.textContent = `${label}: ${value.toFixed(2)}`;
  pop.appendChild(heading);

  const noteEl = document.createElement("p");
  noteEl.className = "score-popover-note";
  noteEl.textContent = note || "Hover the score for trend context.";
  pop.appendChild(noteEl);

  const chart = document.createElement("div");
  chart.className = "score-popover-chart";
  chart.innerHTML = scoreSparklineSvg(values, color);
  pop.appendChild(chart);

  pop.hidden = false;

  const rect = anchorEl.getBoundingClientRect();
  const popH = pop.offsetHeight;
  const popW = pop.offsetWidth;
  let top = rect.bottom + 8;
  if (top + popH > window.innerHeight - 10) {
    top = rect.top - popH - 8;
  }
  let left = rect.left - (popW / 2) + (rect.width / 2);
  left = Math.max(8, Math.min(left, window.innerWidth - popW - 8));

  pop.style.top = `${Math.max(8, top)}px`;
  pop.style.left = `${left}px`;

  pop.onmouseenter = () => {
    if (_scorePopoverCloseTimer) {
      clearTimeout(_scorePopoverCloseTimer);
      _scorePopoverCloseTimer = null;
    }
  };
  pop.onmouseleave = () => scheduleScorePopoverClose();
}

function scheduleScorePopoverClose() {
  _scorePopoverCloseTimer = setTimeout(() => {
    const pop = byId("scorePopover");
    if (pop) pop.hidden = true;
    _scorePopoverCloseTimer = null;
  }, 180);
}

function wireScorePillEvents() {
  const excite = byId("exciteScore");
  const skeptic = byId("skepticScore");
  if (excite) {
    excite.onmouseenter = () => showScorePopover(excite, "excite");
    excite.onmouseleave = () => scheduleScorePopoverClose();
    excite.onfocus = () => showScorePopover(excite, "excite");
    excite.onblur = () => scheduleScorePopoverClose();
  }
  if (skeptic) {
    skeptic.onmouseenter = () => showScorePopover(skeptic, "skeptic");
    skeptic.onmouseleave = () => scheduleScorePopoverClose();
    skeptic.onfocus = () => showScorePopover(skeptic, "skeptic");
    skeptic.onblur = () => scheduleScorePopoverClose();
  }
}

function renderTopicList(element, topics) {
  element.innerHTML = "";
  if (!topics || !topics.length) {
    element.innerHTML = "<li>No data yet.</li>";
    return;
  }
  const allLinks = _allDigestLinks(currentDigest);
  for (const topic of topics) {
    const li = document.createElement("li");

    const label = document.createElement("span");
    label.textContent = `${topic.topic}: `;
    li.appendChild(label);

    // Find stories whose headlines mention this topic
    const headlines = new Set((topic.headlines || []).map((h) => h.toLowerCase()));
    const matched = allLinks.filter((lk) =>
      headlines.has(lk.title.toLowerCase()) ||
      (topic.topic && lk.title.toLowerCase().includes(topic.topic.toLowerCase()))
    );

    const countBtn = document.createElement("span");
    countBtn.className = "tag-count";
    countBtn.textContent = `${topic.count} stories`;
    countBtn.setAttribute("tabindex", "0");
    countBtn.setAttribute("title", "See related stories");

    countBtn.addEventListener("mouseenter", () => {
      showTagPopover(countBtn, topic.topic, matched);
    });
    countBtn.addEventListener("mouseleave", schedulePopoverClose);
    countBtn.addEventListener("focus", () => showTagPopover(countBtn, topic.topic, matched));
    countBtn.addEventListener("blur", schedulePopoverClose);

    li.appendChild(countBtn);
    element.appendChild(li);
  }
}

function renderToolList(element, tools) {
  element.innerHTML = "";
  if (!tools || !tools.length) {
    element.innerHTML = "<li>No data yet.</li>";
    return;
  }
  const allLinks = _allDigestLinks(currentDigest);
  for (const tool of tools) {
    const li = document.createElement("li");

    const label = document.createElement("span");
    label.textContent = `${tool.name} `;
    li.appendChild(label);

    const matched = allLinks.filter((lk) =>
      lk.title.toLowerCase().includes(tool.name.toLowerCase())
    );

    const countBtn = document.createElement("span");
    countBtn.className = "tag-count";
    countBtn.textContent = `(${tool.count})`;
    countBtn.setAttribute("tabindex", "0");
    countBtn.setAttribute("title", "See related stories");

    countBtn.addEventListener("mouseenter", () => {
      showTagPopover(countBtn, tool.name, matched);
    });
    countBtn.addEventListener("mouseleave", schedulePopoverClose);
    countBtn.addEventListener("focus", () => showTagPopover(countBtn, tool.name, matched));
    countBtn.addEventListener("blur", schedulePopoverClose);

    li.appendChild(countBtn);
    element.appendChild(li);
  }
}

function renderPageFlipApiView() {
  byId("meta").textContent = "Page flip mode: this view maps each widget to its API endpoint.";
  byId("pageFlipBtn").textContent = "Page flip: Data";

  byId("aiSummaryBadge").textContent = "API";
  byId("aiSummary").textContent = "GET /api/v1/digest/latest -> ai_summary";
  byId("aiSummary").className = "summary-empty";

  const excite = byId("exciteScore");
  excite.classList.add("endpoint-text");
  excite.textContent = "GET /api/v1/digest/latest -> excitement_score";

  const skeptic = byId("skepticScore");
  skeptic.classList.add("endpoint-text");
  skeptic.textContent = "GET /api/v1/digest/latest -> skepticism_score";

  renderList(byId("themes"), ["GET /api/v1/digest/latest -> today_themes (topic, count, headlines)"], (x) => x);
  renderList(byId("tools"), ["GET /api/v1/digest/latest -> most_mentioned_tools (name, count)"], (x) => x);

  renderEndpointLinks(byId("excited"), [{ label: "GET /api/v1/digest/latest -> excited_about", url: "/api/v1/digest/latest" }]);
  renderEndpointLinks(byId("skeptical"), [{ label: "GET /api/v1/digest/latest -> skeptical_about", url: "/api/v1/digest/latest" }]);
  renderEndpointLinks(byId("topStories"), [{ label: "GET /api/v1/digest/latest -> top_links", url: "/api/v1/digest/latest" }]);
  const topStoriesBtn = byId("toggleTopStoriesBtn");
  if (topStoriesBtn) {
    topStoriesBtn.textContent = "Show more";
    topStoriesBtn.disabled = true;
  }

  renderList(byId("snapshots"), ["GET /api/v1/digest?limit=10"], (x) => x);

  byId("previewMeta").textContent = "GET /api/v1/digest/daily-preview/latest";
  byId("previewSummary").textContent = "Field: latest.ai_summary, latest.item_count, latest.created_at";
  byId("summaryMeta").textContent = "GET /api/v1/digest/daily-summary/latest";
  byId("summarySummary").textContent = "Field: latest.ai_summary, latest.item_count, latest.created_at";

  byId("architectureNote").textContent = "Use this mode as a widget-to-endpoint integration guide for any frontend.";
}

function setDataModeStyles() {
  byId("pageFlipBtn").textContent = "Page flip: API";
  byId("exciteScore").classList.remove("endpoint-text");
  byId("skepticScore").classList.remove("endpoint-text");
  const topStoriesBtn = byId("toggleTopStoriesBtn");
  if (topStoriesBtn) {
    topStoriesBtn.disabled = false;
    topStoriesBtn.textContent = topStoriesExpanded ? "Show less" : "Show more";
  }
}

async function togglePageFlip() {
  pageMode = pageMode === "data" ? "api" : "data";
  if (pageMode === "api") {
    renderPageFlipApiView();
  } else {
    setDataModeStyles();
    await loadLatest();
  }
}

async function loadDailySections() {
  const preview = await fetchJson(`/api/v1/digest/daily-preview/latest?${sourceQueryParam()}`);
  const summary = await fetchJson(`/api/v1/digest/daily-summary/latest?${sourceQueryParam()}`);
  const selectedProvider = currentProvider;
  let previewDigest = preview.latest;
  let summaryDigest = summary.latest;

  if (!shouldShowPlaceholder(selectedProvider)) {
    if (previewDigest && !providerMatchesSelection(selectedProvider, previewDigest.llm_provider)) {
      previewDigest = await findLatestDigestForProvider(selectedProvider, "daily_preview") || previewDigest;
    }
    if (summaryDigest && !providerMatchesSelection(selectedProvider, summaryDigest.llm_provider)) {
      summaryDigest = await findLatestDigestForProvider(selectedProvider, "daily_summary") || summaryDigest;
    }
  }

  if (previewDigest) {
    byId("previewMeta").textContent = formatSnapshotMeta(previewDigest, "Last run");
    const previewResolved = resolveSummaryForProvider(previewDigest, selectedProvider, "Run now");
    renderPreviewSummary("previewSummary", previewResolved.text);
    byId("previewSummary").className = previewResolved.isPlaceholder ? "summary-empty" : "";
    setOutsideCronBadge("previewCronBadge", previewDigest.run_origin === "scheduled");
  } else {
    byId("previewMeta").textContent = "No data yet.";
    byId("previewSummary").textContent = "";
    byId("previewSummary").className = "summary-empty";
    setOutsideCronBadge("previewCronBadge", false);
  }

  if (summaryDigest) {
    byId("summaryMeta").textContent = formatSnapshotMeta(summaryDigest, "Last run");
    const summaryResolved = resolveSummaryForProvider(summaryDigest, selectedProvider, "Run now");
    byId("summarySummary").textContent = normalizeDisplayText(summaryResolved.text);
    byId("summarySummary").className = summaryResolved.isPlaceholder ? "summary-empty" : "";
    setOutsideCronBadge("summaryCronBadge", summaryDigest.run_origin === "scheduled");
  } else {
    byId("summaryMeta").textContent = "No data yet.";
    byId("summarySummary").textContent = "";
    byId("summarySummary").className = "summary-empty";
    setOutsideCronBadge("summaryCronBadge", false);
  }
}

async function loadLatest() {
  if (!signalDashboardActive()) {
    return;
  }

  if (pageMode === "api") {
    renderPageFlipApiView();
    return;
  }

  setLoadingState("Loading latest digest...");

  let digest;
  try {
    digest = await fetchJson(`/api/v1/digest/latest?${sourceQueryParam()}`);
  } catch (err) {
    if (err.status === 404) {
      setLoadingState("No snapshot yet. Waiting for the next scheduled run.");
      byId("aiSummary").textContent = "";
      byId("aiSummaryBadge").textContent = "";
      updateVersionMeta(null);
      return;
    }
    throw err;
  }

  const history = await fetchJson(`/api/v1/digest?limit=10&${sourceQueryParam()}`);
  if (!shouldShowPlaceholder(currentProvider) && !providerMatchesSelection(currentProvider, digest.llm_provider)) {
    const persisted = await findLatestDigestForProvider(currentProvider, "regular");
    if (persisted) {
      digest = persisted;
    }
  }

  updateVersionMeta(digest);

  clearLoadingState();

  const summaryEl = byId("aiSummary");
  const badgeEl = byId("aiSummaryBadge");
  const selectedProvider = currentProvider;
  const mainResolved = resolveSummaryForProvider(digest, selectedProvider, "Refresh now");
  badgeEl.textContent = selectedProvider === "none" ? "off" : (digest.llm_provider || "ai-on");
  summaryEl.textContent = mainResolved.text;
  summaryEl.className = mainResolved.isPlaceholder ? "summary-empty" : "";
  byId("exciteScore").textContent = String(digest.excitement_score ?? "-");
  byId("skepticScore").textContent = String(digest.skepticism_score ?? "-");
  await updateMetricNotes(digest.excitement_score ?? 0, digest.skepticism_score ?? 0);
  wireScorePillEvents();

  currentDigest = digest;
  renderTopicList(byId("themes"), digest.today_themes);
  renderToolList(byId("tools"), digest.most_mentioned_tools);
  renderLinks(byId("excited"), digest.excited_about);
  renderLinks(byId("skeptical"), digest.skeptical_about);
  renderSnapshotArchive(byId("snapshots"), history);

  renderTopStories(byId("topStories"), digest.top_links, digest.best_rabbit_holes);
  const topStoriesBtn = byId("toggleTopStoriesBtn");
  if (topStoriesBtn) {
    topStoriesBtn.disabled = !digest.top_links || digest.top_links.length <= 3;
    topStoriesBtn.textContent = topStoriesExpanded ? "Show less" : "Show more";
  }

  byId("architectureNote").textContent = digest.note || "";

  await loadDailySections();
  await initSnapshotNav();
}

async function refreshNow() {
  const btn = byId("refreshBtn");
  if (!btn || btn.disabled) return;

  // Research page: refresh LLM ranking
  if (activeDashboard === "research") {
    btn.disabled = true;
    btn.textContent = "Refreshing...";
    startRunProgress("research-refresh", "Live ranking refresh", 22000);
    setLoadingState("Refreshing live LLM ranking...");
    try {
      await loadLiveLlmRanking(true);
      finishRunProgress("Ranking refresh complete");
      setLoadingState("Ranking refresh complete.");
    } catch (e) {
      setText("llmRankMeta", `Failed to refresh: ${e.message}`);
      failRunProgress(`Ranking refresh failed: ${e.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = refreshButtonDefaultLabel();
    }
    return;
  }

  // Settings page: refresh scheduler overview and countdowns
  if (activeDashboard === "settings") {
    btn.disabled = true;
    btn.textContent = "Refreshing...";
    setLoadingState("Refreshing scheduler status...");
    try {
      await Promise.all([
        updateCountdowns(),
        isSchedulerPanelVisible() ? loadSchedulerOverview() : Promise.resolve(),
      ]);
      setLoadingState("Scheduler status refreshed.");
    } finally {
      btn.disabled = false;
      btn.textContent = refreshButtonDefaultLabel();
    }
    return;
  }

  // Signal pages (HN / Reddit): queue manual refresh with limit controls.
  if (activeDashboard === "hn" || activeDashboard === "reddit") {
    setLoadingState("Manual refresh is disabled. Showing the latest scheduled snapshot.");
    await loadLatest();
    return;
  }

  // Legacy queue path (kept for backward compatibility in local-only admin mode)
  btn.disabled = true;
  btn.textContent = "Queueing...";
  try {
    const result = await fetchJson("/api/v1/admin/refresh/queue", { method: "POST" });
    const banner = byId("impatienceBanner");

    if (result.status === "auto_triggered") {
      if (banner) {
        banner.textContent = result.message || "Super duper manual trigger activated.";
        banner.hidden = false;
      }
      setCancelRefreshVisible(false);
      await loadLatest();
      setLoadingState(result.message || "Super duper manual trigger activated.");
    } else if (result.status === "queued") {
      if (banner) banner.hidden = true;
      setCancelRefreshVisible(true);
      const dueText = result.seconds_until_due != null ? ` Starts in ~${formatWaitLabel(result.seconds_until_due)}.` : "";
      setLoadingState(`${result.message} ${result.trigger_count}/${result.threshold}${dueText}`.trim());
      // Immediately reflect the new click count in the progress bar without waiting for poll
      if (queueProgressState) {
        queueProgressState.clickCount = result.trigger_count || 0;
        queueProgressState.threshold = result.threshold || 10;
      } else {
        showQueuedRefreshProgress(result.seconds_until_due || 0, 20, result.trigger_count || 0, result.threshold || 10);
      }
    } else if (result.status === "rate_limited") {
      if (banner) banner.hidden = true;
      setCancelRefreshVisible(false);
      const wait = formatWaitLabel(result.seconds_until_next_window || 0);
      setLoadingState(`Manual refresh limit hit (2 per 2 hours). You can't refresh until the next window (~${wait}) and it must be done manually.`);
    } else if (result.status === "in_progress") {
      if (banner) banner.hidden = true;
      setCancelRefreshVisible(false);
      setLoadingState("A queued refresh already started and is running. It counts toward the 2-hour limit.");
    } else if (result.status === "cooldown") {
      if (banner) banner.hidden = true;
      const remaining = formatWaitLabel(result.cooldown_seconds_remaining || 0);
      setLoadingState(`${result.message} Cooldown remaining: ${remaining}.`);
    } else {
      if (banner) banner.hidden = true;
      setLoadingState(result.message || "Refresh request received.");
    }

    await Promise.all([loadSchedulerOverview(), syncManualQueueUi()]);
  } catch (error) {
    setLoadingState(`Queue refresh failed: ${error.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = refreshButtonDefaultLabel();
  }
}

async function initSnapshotNav() {
  try {
    allSnapshotIds = await fetchJson(`/api/v1/digest?limit=100&${sourceQueryParam()}`);
    currentSnapIdx = 0;
  } catch {
    allSnapshotIds = [];
  }
  updateSnapNavUI();
}

function updateSnapNavUI() {
  const nav = byId("snapshotNav");
  if (!allSnapshotIds.length) {
    nav.style.display = "none";
    return;
  }
  nav.style.display = "";
  byId("snapCounter").textContent = `${currentSnapIdx + 1} / ${allSnapshotIds.length}`;
  byId("snapPrevBtn").disabled = currentSnapIdx >= allSnapshotIds.length - 1;
  byId("snapNextBtn").disabled = currentSnapIdx <= 0;
}

async function navigateSnapshot(delta) {
  const newIdx = currentSnapIdx + delta;
  if (newIdx < 0 || newIdx >= allSnapshotIds.length) return;
  currentSnapIdx = newIdx;
  const snap = allSnapshotIds[currentSnapIdx];
  try {
    const digest = await fetchJson(`/api/v1/digest/${snap.id}`);
    const summaryEl = byId("aiSummary");
    const badgeEl = byId("aiSummaryBadge");
    const label = `${digest.llm_provider} • ${formatInSelectedTimezone(digest.created_at, { dateOnly: true })} • ${digest.kind}`;

    const resolved = resolveSummaryForProvider(digest, currentProvider, "Refresh now");
    summaryEl.textContent = resolved.text;
    summaryEl.className = resolved.isPlaceholder ? "summary-empty" : "";
    badgeEl.textContent = label;
  } catch (e) {
    byId("aiSummary").textContent = `Failed to load: ${e.message}`;
  }
  updateSnapNavUI();
}

async function refreshSnapshotHistoryAndVersion() {
  let digest;
  try {
    digest = await fetchJson(`/api/v1/digest/latest?${sourceQueryParam()}`);
  } catch (err) {
    if (err.status === 404) {
      updateVersionMeta(null);
      renderSnapshotArchive(byId("snapshots"), []);
      return;
    }
    throw err;
  }

  const history = await fetchJson(`/api/v1/digest?limit=10&${sourceQueryParam()}`);
  updateVersionMeta(digest);
  renderSnapshotArchive(byId("snapshots"), history);
}

async function runDailyKind(kind, metaId, summaryId, btnId) {
  const btn = byId(btnId);
  btn.disabled = true;
  btn.textContent = "Running...";
  startRunProgress(
    kind,
    kind === "daily_preview" ? "9:01 preview run" : "5:01 summary run",
    55000,
  );
  try {
    const result = await fetchJson(`/api/v1/admin/refresh?kind=${kind}&${sourceQueryParam()}`, { method: "POST" });
    const summaryEl = byId(summaryId);
    const adHocAt = formatInSelectedTimezone(new Date());
    const nextScheduled = kind === "daily_preview"
      ? formatInSelectedTimezone(nextScheduledTime(9, 1, "America/New_York"))
      : formatInSelectedTimezone(nextScheduledTime(17, 1, "America/Los_Angeles"));
    const modeLabel = kind === "daily_preview" ? "9:01 AM ET preview" : "5:01 PM PT summary";
    const note = `This is just an ad-hoc generated response at ${adHocAt}. Next scheduled response is at ${nextScheduled} (${modeLabel}).`;

    const resolved = resolveSummaryForProvider(result, currentProvider, "Run now");
    if (kind === "daily_preview") {
      renderPreviewSummary(summaryId, resolved.text, note);
    } else {
      summaryEl.textContent = `${normalizeDisplayText(resolved.text)}\n\n${normalizeDisplayText(note)}`;
    }
    summaryEl.className = resolved.isPlaceholder ? "summary-empty" : "";
    finishRunProgress(kind === "daily_preview" ? "9:01 preview complete" : "5:01 summary complete");
  } catch (e) {
    byId(metaId).textContent = `Error: ${e.message}`;
    failRunProgress(`Run failed: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Run now";
  }
}

async function renderDailyHistory(kind, div, btn) {
  const items = await fetchJson(`/api/v1/digest?kind=${kind}&limit=40&${sourceQueryParam()}`);
  div.innerHTML = "";
  if (!items.length) {
    div.innerHTML = "<p class='meta'>No history yet.</p>";
    return;
  }

  const header = document.createElement("div");
  header.className = "history-grid history-grid-header";
  ["Created", "Items", "Provider", "Origin"].forEach((name) => {
    const span = document.createElement("span");
    span.textContent = name;
    header.appendChild(span);
  });
  div.appendChild(header);

  for (const item of items) {
    const entry = document.createElement("div");
    entry.className = "history-entry";
    const meta = document.createElement("button");
    meta.className = "history-grid history-grid-row history-entry-meta";

    const created = document.createElement("span");
    created.textContent = formatInSelectedTimezone(item.created_at);
    const count = document.createElement("span");
    count.textContent = `${item.item_count}`;
    const provider = document.createElement("span");
    provider.textContent = item.llm_provider || "none";
    const origin = document.createElement("span");
    if (item.run_origin === "scheduled") {
      origin.innerHTML = "scheduled <span class='cron-star'>★</span> <span class='cron-pill'>CRON</span>";
    } else {
      origin.textContent = item.run_origin || "manual";
    }

    meta.appendChild(created);
    meta.appendChild(count);
    meta.appendChild(provider);
    meta.appendChild(origin);

    const body = document.createElement("div");
    body.className = "history-entry-body";
    body.hidden = true;
    meta.addEventListener("click", async () => {
      if (!body.hidden) { body.hidden = true; return; }
      if (body.dataset.loaded) { body.hidden = false; return; }
      body.textContent = "Loading...";
      body.hidden = false;
      try {
        const full = await fetchJson(`/api/v1/digest/${item.id}`);
        const resolved = resolveSummaryForProvider(full, currentProvider, "Run now");
        body.textContent = normalizeDisplayText(resolved.text);
        body.dataset.loaded = "1";
      } catch (e) {
        body.textContent = `Error: ${e.message}`;
      }
    });

    entry.appendChild(meta);
    entry.appendChild(body);
    div.appendChild(entry);
  }
}

async function toggleDailyHistory(kind, historyDivId, btnId) {
  const div = byId(historyDivId);
  const btn = byId(btnId);
  if (div.dataset.open === "1") {
    div.style.display = "none";
    div.hidden = true;
    div.dataset.open = "0";
    btn.textContent = "History";
    return;
  }

  div.style.display = "";
  div.hidden = false;
  div.dataset.open = "1";
  btn.textContent = "Loading...";
  btn.disabled = true;
  try {
    await renderDailyHistory(kind, div, btn);
    div.style.display = "";
    div.hidden = false;
    btn.textContent = "Hide history";
  } catch (e) {
    div.innerHTML = `<p class='meta'>Failed: ${e.message}</p>`;
    div.style.display = "";
    div.hidden = false;
    btn.textContent = "History";
  } finally {
    btn.disabled = false;
  }
}

function setActiveDashboard(page) {
  activeDashboard = page;
  const signalActive = page === "hn" || page === "reddit";
  if (page === "hn") {
    activeSignalSource = "hackernews";
  } else if (page === "reddit") {
    activeSignalSource = "reddit";
  }

  const hnPage = byId("hnPage");
  const researchPage = byId("researchPage");
  const settingsPage = byId("settingsPage");
  const navHnBtn = byId("navHnBtn");
  const navRedditBtn = byId("navRedditBtn");
  const navResearchBtn = byId("navResearchBtn");
  const navSettingsBtn = byId("navSettingsBtn");
  const pageFlipBtn = byId("pageFlipBtn");
  const refreshBtn = byId("refreshBtn");
  const countdownRow = byId("countdownRow");
  const schedulerPanel = byId("schedulerOverviewPanel");

  if (hnPage) hnPage.hidden = !signalActive;
  if (researchPage) researchPage.hidden = page !== "research";
  if (settingsPage) settingsPage.hidden = page !== "settings";
  if (navHnBtn) navHnBtn.classList.toggle("active", page === "hn");
  if (navRedditBtn) navRedditBtn.classList.toggle("active", page === "reddit");
  if (navResearchBtn) navResearchBtn.classList.toggle("active", page === "research");
  if (navSettingsBtn) navSettingsBtn.classList.toggle("active", page === "settings");
  if (pageFlipBtn) pageFlipBtn.style.display = signalActive ? "" : "none";
  if (refreshBtn) {
    refreshBtn.style.display = "";
    refreshBtn.textContent = refreshButtonDefaultLabel();
    setHeaderRefreshVisible(false);
  }
  setCancelRefreshVisible(false);
  setQueueRefreshLabel("");
  clearQueueProgress();
  if (countdownRow) countdownRow.style.display = page === "settings" ? "" : "none";
  // Scheduler visibility depends on page, AI mode, and status toggle state.
  if (schedulerPanel) applySchedulerOverviewVisibility();

  if (signalActive) {
    if (page === "hn") {
      applySchedulerOverviewVisibility();
    }
    loadLatest().catch((error) => setLoadingState(`Error: ${error.message}`));
  } else if (page === "research") {
    clearLoadingState();
    loadLiveLlmRanking().catch((error) => {
      byId("llmRankMeta").textContent = `Failed to load ranking: ${error.message}`;
    });
  } else {
    clearLoadingState();
    updateCountdowns().catch(() => {});
    refreshSnapshotHistoryAndVersion().catch(() => {});
    applySchedulerOverviewVisibility();
    if (isSchedulerPanelVisible()) {
      loadSchedulerOverview().catch(() => {});
    }
  }
}

function renderLiveLocalLlmItems(items) {
  const list = byId("llmRankList");
  list.innerHTML = "";

  if (!items || items.length === 0) {
    list.innerHTML = "<p class='meta'>No ranking data returned yet.</p>";
    return;
  }

  for (const item of items) {
    const row = document.createElement("article");
    row.className = "rank-card";

    const header = document.createElement("div");
    header.className = "rank-card-head";

    const rankName = document.createElement("h4");
    rankName.className = "rank-title";
    rankName.textContent = `#${item.rank} \u2014 ${item.model_name}`;

    const score = document.createElement("span");
    score.className = "rank-score-pill";
    score.textContent = `${item.qualitative_score}`;

    header.appendChild(rankName);
    header.appendChild(score);

    const scoreExplain = document.createElement("p");
    scoreExplain.className = "score-explain";
    scoreExplain.textContent = `Score\u00a0${item.qualitative_score}\u00a0\u2014 relative rank within this set (100\u00a0= top model)`;

    // Signals row
    const signals = item.signals || {};
    const sigRow = document.createElement("div");
    sigRow.className = "rank-signals-row";
    const sigParts = [];
    if (signals.downloads) sigParts.push(`${Number(signals.downloads).toLocaleString()} dl`);
    if (signals.likes) sigParts.push(`${Number(signals.likes).toLocaleString()} \u2665`);
    if (signals.freshness_score >= 1.2) sigParts.push("recently updated");
    if (signals.local_fit_score >= 1.5) sigParts.push("local-ready");
    sigRow.textContent = sigParts.join(" \u00b7 ");

    const bullets = document.createElement("ul");
    bullets.className = "rank-bullets";

    const why = document.createElement("li");
    why.className = "rank-rationale";
    why.textContent = decodeHtmlEntities(item.rationale || "No rationale available.");
    bullets.appendChild(why);

    // All source links
    for (const ref of (item.sources || [])) {
      const srcLi = document.createElement("li");
      srcLi.className = "rank-source-line";
      const span = document.createElement("span");
      span.className = "fake-link";
      span.textContent = `\u2192 ${ref.label}`;
      span.addEventListener("click", () => window.open(ref.url, "_blank", "noreferrer"));
      srcLi.appendChild(span);
      bullets.appendChild(srcLi);
    }

    row.appendChild(header);
    row.appendChild(scoreExplain);
    if (sigParts.length) row.appendChild(sigRow);
    row.appendChild(bullets);
    list.appendChild(row);
  }
}

function setResearchTab(tab) {
  activeResearchTab = tab;
  byId("tabLocalLlmsBtn")?.classList.toggle("active", tab === "local");
  byId("tabCloudLlmsBtn")?.classList.toggle("active", tab === "cloud");

  const title = byId("researchTitle");
  if (title) {
    title.textContent = tab === "cloud" ? "Live Cloud LLM Ranking" : "Live Local LLM Ranking";
  }
}

async function loadLlmVibeCheck(forceRefresh = false) {
  const scope = activeResearchTab === "cloud" ? "cloud" : "local";
  const summaryEl = byId("llmVibeSummary");
  const badgeEl = byId("llmVibeBadge");
  const boxEl = document.querySelector(".llm-vibe-box");
  if (!summaryEl || !badgeEl) {
    return;
  }

  const token = ++llmVibeLoadToken;
  const cached = llmVibeCache[scope];

  if (cached && !forceRefresh) {
    summaryEl.textContent = cached.summary;
    summaryEl.className = "ai-scroll-box";
    badgeEl.textContent = `${cached.provider} • updating`;
    boxEl?.classList.add("background-loading");
  } else {
    summaryEl.textContent = "Loading AI vibe check...";
    summaryEl.className = "summary-empty ai-scroll-box";
    badgeEl.textContent = "loading";
    boxEl?.classList.remove("background-loading");
  }

  try {
    const vibe = await fetchJson(`/api/v1/research/llm-vibe-check?scope=${scope}&force_refresh=${forceRefresh ? "true" : "false"}`);
    if (token !== llmVibeLoadToken) {
      return;
    }
    const summary = normalizeDisplayText(vibe.ai_summary || "No vibe check was generated.");
    llmVibeCache[scope] = {
      summary,
      provider: vibe.llm_provider || "none",
    };
    summaryEl.textContent = summary;
    summaryEl.className = "ai-scroll-box";
    badgeEl.textContent = vibe.llm_provider || "none";
    boxEl?.classList.remove("background-loading");
  } catch (error) {
    if (token !== llmVibeLoadToken) {
      return;
    }
    if (cached) {
      summaryEl.textContent = cached.summary;
      summaryEl.className = "ai-scroll-box";
      badgeEl.textContent = `${cached.provider} • stale`;
      boxEl?.classList.remove("background-loading");
      return;
    }
    summaryEl.textContent = `Vibe check unavailable: ${error.message || "unknown error"}`;
    summaryEl.className = "summary-empty ai-scroll-box";
    badgeEl.textContent = "error";
    boxEl?.classList.remove("background-loading");
    throw error;
  }
}

async function loadLiveLlmRanking(forceRefresh = false) {
  byId("llmRankMeta").textContent = "Loading live ranking...";
  const path = activeResearchTab === "cloud"
    ? "/api/v1/research/cloud-llms/live-ranking"
    : "/api/v1/research/local-llms/live-ranking";
  const payload = await fetchJson(`${path}?force_refresh=${forceRefresh ? "true" : "false"}`);

  const scope = activeResearchTab === "cloud" ? "cloud" : "local";
  const hasCachedVibe = Boolean(llmVibeCache[scope]?.summary);
  loadLlmVibeCheck(forceRefresh).catch(() => {});
  if (hasCachedVibe) {
    document.querySelector(".llm-vibe-box")?.classList.add("background-loading");
  }

  const generated = formatInSelectedTimezone(payload.generated_at);
  byId("llmRankMeta").textContent = `Updated ${generated} from public model metadata.`;
  renderLiveLocalLlmItems(payload.items || []);

  renderList(byId("llmMethodology"), payload.methodology || [], (x) => x);
  renderList(byId("llmLegalEthics"), payload.legal_ethics || [], (x) => x);
}

function isLocalRuntimeHost() {
  const host = (window.location.hostname || "").toLowerCase();
  return host === "localhost" || host === "127.0.0.1" || host === "::1" || host.endsWith(".local") || host.includes("wsl.localhost");
}

function getAutoTargetProvider() {
  return isLocalRuntimeHost() ? "ollama" : "openai";
}

function isAiAutoEnabled() {
  return localStorage.getItem(AI_AUTO_KEY) === "1";
}

function setProviderButtonsDisabled(disabled) {
  for (const btn of document.querySelectorAll("[data-provider]")) {
    btn.disabled = disabled;
  }
}

function updateAiAutoButtonVisualState() {
  const btn = byId("aiAutoBtn");
  if (!btn) {
    return;
  }

  const autoEnabled = isAiAutoEnabled();
  btn.textContent = "AI";
  btn.classList.toggle("active", autoEnabled);

  if (autoEnabled) {
    btn.dataset.mode = "auto";
    return;
  }

  if (currentProvider !== "none") {
    btn.dataset.mode = "manual-ai";
  } else {
    btn.dataset.mode = "off";
  }
}

function configureProviderButtonsForRuntime() {
  const localRuntime = isLocalRuntimeHost();
  const ollamaBtn = byId("providerOllamaBtn");
  const cloudBtn = byId("providerCloudBtn");

  if (ollamaBtn) {
    ollamaBtn.style.display = localRuntime ? "" : "none";
  }
  if (cloudBtn) {
    cloudBtn.disabled = true;
    cloudBtn.classList.add("soon-btn");
    cloudBtn.title = "Cloud LLM — coming soon";
  }

  if (!localRuntime && currentProvider === "ollama") {
    highlightProviderBtn("openai");
  }
}

async function applyAutoProviderIfEnabled() {
  if (!isAiAutoEnabled()) {
    return;
  }
  const target = getAutoTargetProvider();
  if (currentProvider === target) {
    return;
  }
  await switchProvider(target);
}

function initAutoModeControls() {
  const btn = byId("aiAutoBtn");
  if (!btn) {
    return;
  }
  const active = isAiAutoEnabled();
  updateAiAutoButtonVisualState();
  setProviderButtonsDisabled(active);

  btn.addEventListener("click", async () => {
    const nowActive = !isAiAutoEnabled();
    localStorage.setItem(AI_AUTO_KEY, nowActive ? "1" : "0");
    updateAiAutoButtonVisualState();
    setProviderButtonsDisabled(nowActive);
    if (nowActive) {
      await applyAutoProviderIfEnabled();
    } else {
      // AI auto off → fall back to heuristic mode and hide AI panels
      await switchProvider("none");
    }
  });
}

async function loadProvider() {
  try {
    const data = await fetchJson("/api/v1/admin/provider");
    highlightProviderBtn(data.provider);
    applyProviderPlaceholder(data.provider);
    configureProviderButtonsForRuntime();
    await applyAutoProviderIfEnabled();
  } catch {
    // ignore — provider toggle is best-effort
  }
}

function getCurrentProvider() {
  for (const btn of document.querySelectorAll("[data-provider]")) {
    if (btn.classList.contains("active")) {
      return btn.dataset.provider;
    }
  }
  return "auto";
}

function highlightProviderBtn(active) {
  const mode = normalizeProviderMode(active);
  for (const btn of document.querySelectorAll("[data-provider]")) {
    if (btn.dataset.provider === mode) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  }
  currentProvider = mode;
  updateAiAutoButtonVisualState();
  applyAiVisibility();
}

function applyAiVisibility() {
  const isHeuristic = currentProvider === "none";
  const aiPanel = byId("aiSummaryPanel");
  const dailyPanel = byId("dailyAiSection");
  if (aiPanel) aiPanel.hidden = isHeuristic;
  if (dailyPanel) dailyPanel.hidden = isHeuristic;

  // Scheduler is only meaningful when AI is active on the HN dashboard.
  applySchedulerOverviewVisibility();

  // LLM vibe check in the Research dashboard
  const vibeBox = document.querySelector(".llm-vibe-box");
  if (vibeBox) vibeBox.hidden = isHeuristic;

  setHeaderRefreshVisible(false);
}

function configureAdminOverrideControls() {
  const btn = byId("adminOverrideRefreshBtn");
  if (!btn) {
    return;
  }
  btn.hidden = !isLocalRuntimeHost();
}

async function runAdminOverrideRefresh() {
  const btn = byId("adminOverrideRefreshBtn");
  if (!btn || btn.disabled) {
    return;
  }
  if (!isLocalRuntimeHost()) {
    setLoadingState("Admin override refresh is local-only.");
    return;
  }

  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = "Running...";
  startRunProgress("admin-override-refresh", "Admin override refresh", 55000);
  let runningPollTimer = null;
  try {
    const request = fetchJson(`/api/v1/admin/refresh/override?${sourceQueryParam()}`, { method: "POST" });
    if (isSchedulerPanelVisible()) {
      loadSchedulerOverview().catch(() => {});
      runningPollTimer = setInterval(() => {
        loadSchedulerOverview().catch(() => {});
      }, 1500);
    }

    const result = await request;
    finishRunProgress("Admin override complete");
    setLoadingState(`Admin override complete: snapshot #${result.id} (${result.kind}).`);
    await Promise.all([
      refreshSnapshotHistoryAndVersion(),
      loadLatest(),
      updateCountdowns(),
      isSchedulerPanelVisible() ? loadSchedulerOverview() : Promise.resolve(),
    ]);
  } catch (error) {
    failRunProgress(`Admin override failed: ${error.message}`);
    setLoadingState(`Admin override failed: ${error.message}`);
  } finally {
    if (runningPollTimer) {
      clearInterval(runningPollTimer);
    }
    btn.disabled = false;
    btn.textContent = prev || "Admin override refresh";
  }
}

function applyProviderPlaceholder(provider) {
  const badge = byId("aiSummaryBadge");
  const summary = byId("aiSummary");
  const preview = byId("previewSummary");
  const dailySummary = byId("summarySummary");
  if (!badge || !summary) {
    return;
  }

  const mode = normalizeProviderMode(provider);
  badge.textContent = mode === "none" ? "off" : "ai-on";

  if (mode === "none") {
    const placeholder = getProviderPlaceholder(mode);
    summary.textContent = placeholder;
    summary.className = "summary-empty";

    if (preview) {
      renderPreviewSummary("previewSummary", placeholder);
      preview.className = "summary-empty";
    }
    if (dailySummary) {
      dailySummary.textContent = placeholder;
      dailySummary.className = "summary-empty";
    }
    return;
  }

  summary.textContent = "AI mode is on. Scheduled snapshots include AI output automatically.";
  summary.className = "summary-empty";
  if (preview) {
    renderPreviewSummary("previewSummary", "AI mode is on. Scheduled snapshots include AI output automatically.");
    preview.className = "summary-empty";
  }
  if (dailySummary) {
    dailySummary.textContent = "AI mode is on. Scheduled snapshots include AI output automatically.";
    dailySummary.className = "summary-empty";
  }
}

async function switchProvider(provider) {
  try {
    await fetchJson(`/api/v1/admin/provider?provider=${provider}`, { method: "POST" });
    highlightProviderBtn(provider);
    applyProviderPlaceholder(provider);
    await loadLatest();
  } catch (e) {
    setLoadingState(`Failed to set provider: ${e.message}`);
  }
}

function nextScheduledTime(targetHour, targetMinute, tz) {
  const now = new Date();
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

  function tzSecondsOfDay(date) {
    const parts = fmt.formatToParts(date);
    const p = {};
    parts.forEach((x) => { p[x.type] = x.value; });
    return parseInt(p.hour) * 3600 + parseInt(p.minute) * 60 + parseInt(p.second);
  }

  const targetSec = targetHour * 3600 + targetMinute * 60;
  const nowSec = tzSecondsOfDay(now);
  let diffSec = targetSec - nowSec;
  if (diffSec <= 60) diffSec += 86400; // already passed today, push to tomorrow
  return new Date(now.getTime() + diffSec * 1000);
}

function formatCountdown(ms) {
  if (ms <= 0) return "now";
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  if (h > 0) return `in ${h}h ${m}m`;
  if (m > 0) return `in ${m}m`;
  return "< 1m";
}

function formatClockForTimezone(date, timeZone) {
  return new Intl.DateTimeFormat("en-US", {
    timeZone,
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZoneName: "short",
  }).format(date);
}

async function updateCountdowns() {
  const labelEl = byId("countdownNextLabel");
  const valueEl = byId("countdownNext");
  if (!labelEl || !valueEl) {
    return;
  }

  const nowMs = Date.now();
  try {
    const jobsPayload = await fetchJson("/api/v1/admin/scheduler/jobs");
    const jobs = (jobsPayload.jobs || [])
      .map((j) => ({ ...j, nextRunMs: parseIsoToMs(j.next_run_time) }))
      .filter((j) => j.nextRunMs > nowMs)
      .sort((a, b) => a.nextRunMs - b.nextRunMs);

    const nextJob = jobs[0];
    if (!nextJob) {
      labelEl.textContent = "Next scheduled snapshot";
      valueEl.textContent = "none";
      valueEl.classList.remove("imminent");
      return;
    }

    const kindLabel = nextJob.kind === "daily_summary" ? "daily summary" : nextJob.kind;
    const nextDate = new Date(nextJob.nextRunMs);
    const tz = byId("clockTzSelect")?.value || "America/New_York";
    const msLeft = nextJob.nextRunMs - nowMs;

    labelEl.textContent = `Next scheduled snapshot (${kindLabel})`;
    valueEl.textContent = `${formatCountdown(msLeft)} • ${formatClockForTimezone(nextDate, tz)}`;
    valueEl.classList.toggle("imminent", msLeft < 300000);
  } catch {
    // Fallback so the countdown never goes blank if scheduler endpoint is unavailable.
    const nextSummary = nextScheduledTime(17, 1, "America/Los_Angeles");
    const msLeft = nextSummary.getTime() - nowMs;
    labelEl.textContent = "Next scheduled snapshot (fallback)";
    valueEl.textContent = formatCountdown(msLeft);
    valueEl.classList.toggle("imminent", msLeft < 300000);
  }
}

function startCountdowns() {
  updateCountdowns().catch(() => {});
  setInterval(() => {
    updateCountdowns().catch(() => {});
  }, 30000);
}

function parseIsoToMs(value) {
  const ms = parseServerDate(value).getTime();
  return Number.isFinite(ms) ? ms : 0;
}

function isSchedulerOverviewHidden() {
  return localStorage.getItem(SCHED_OVERVIEW_HIDDEN_KEY) !== "0";
}

function isSchedulerPanelVisible() {
  return localStorage.getItem(SCHED_PANEL_VISIBLE_KEY) === "1";
}

function applySchedulerOverviewVisibility() {
  const panel = byId("schedulerOverviewPanel");
  const refreshBtn = byId("refreshSchedulerOverviewBtn");
  const toggleBtn = byId("toggleSchedulerPanelBtn");
  if (!panel || !toggleBtn) {
    return;
  }

  const hidden = false;
  const allowed = activeDashboard === "settings";
  const visible = allowed && isSchedulerPanelVisible();

  panel.hidden = !visible;
  toggleBtn.style.display = allowed ? "" : "none";
  toggleBtn.textContent = visible ? "Hide scheduler overview" : "Show scheduler overview";

  panel.classList.toggle("scheduler-overview-collapsed", hidden);
  if (refreshBtn) {
    refreshBtn.style.display = hidden ? "none" : "";
  }
  const actions = refreshBtn?.parentElement;
  if (actions) {
    actions.style.flexDirection = hidden ? "row" : "row-reverse";
  }
}

function toggleSchedulerOverview() {
  const visible = isSchedulerPanelVisible();
  localStorage.setItem(SCHED_PANEL_VISIBLE_KEY, visible ? "0" : "1");
  applySchedulerOverviewVisibility();
  if (visible) {
    return;
  }
  loadSchedulerOverview().catch(() => {});
}

function renderSchedulerOverview(overview) {
  const recentEl = byId("schedulerRecentList");
  const upcomingEl = byId("schedulerUpcomingList");
  const metaEl = byId("schedulerOverviewMeta");
  const manualMetaEl = byId("manualQueueMeta");
  if (!recentEl || !upcomingEl || !metaEl || !manualMetaEl) {
    return;
  }

  metaEl.textContent = overview.running ? "Scheduler is running." : "Scheduler is not running.";

  const recent = overview.recent_snapshots || [];
  if (!recent.length) {
    recentEl.classList.remove("scheduler-lane");
    recentEl.innerHTML = "<li>No snapshots yet.</li>";
  } else {
    recentEl.classList.add("scheduler-lane");
    recentEl.innerHTML = "";
    for (const item of recent) {
      const li = document.createElement("li");
      li.className = "scheduler-node recent";
      li.innerHTML = `
        <div class="scheduler-chip-row">
          <span class="scheduler-chip recent">recent</span>
          <span class="scheduler-kind">#${item.id} • ${item.kind}</span>
        </div>
        <div class="scheduler-time">${formatInSelectedTimezone(item.created_at, { withZone: true })}</div>
      `;
      recentEl.appendChild(li);
    }
  }

  const upcoming = overview.upcoming_snapshots || [];
  if (!upcoming.length) {
    upcomingEl.classList.remove("scheduler-lane");
    upcomingEl.innerHTML = "<li>No upcoming jobs.</li>";
  } else {
    upcomingEl.classList.add("scheduler-lane");
    upcomingEl.innerHTML = "";
    for (const item of upcoming) {
      const kindLabel = item.kind === "admin_override_running" ? "admin override (running)" : item.kind;
      const chipLabel = item.kind === "admin_override_running" ? "running" : "upcoming";
      const li = document.createElement("li");
      li.className = "scheduler-node upcoming";
      li.innerHTML = `
        <div class="scheduler-chip-row">
          <span class="scheduler-chip upcoming">${chipLabel}</span>
          <span class="scheduler-kind">${kindLabel}</span>
        </div>
        <div class="scheduler-time">${formatInSelectedTimezone(item.next_run_time, { withZone: true })}</div>
      `;
      upcomingEl.appendChild(li);
    }
  }

  manualMetaEl.textContent = "Strict cadence active: regular every 2 hours, plus daily preview at 9:01 AM ET and daily summary at 5:01 PM PT.";
}

async function loadSchedulerOverview() {
  const metaEl = byId("schedulerOverviewMeta");
  if (activeDashboard !== "settings" || !isSchedulerPanelVisible()) {
    return;
  }
  try {
    const overview = await fetchJson("/api/v1/admin/scheduler/overview");
    renderSchedulerOverview(overview);
  } catch (e) {
    if (metaEl) {
      metaEl.textContent = `Failed to load scheduler overview: ${e.message}`;
    }
  }
}

function startSchedulerOverviewPolling() {
  if (schedulerOverviewTimer) {
    clearInterval(schedulerOverviewTimer);
  }
  applySchedulerOverviewVisibility();
  if (isSchedulerPanelVisible()) {
    loadSchedulerOverview().catch(() => {});
  }
  schedulerOverviewTimer = setInterval(() => {
    loadSchedulerOverview().catch(() => {});
  }, 20000);
}

async function pollScheduledRunState() {
  if (activeDashboard !== "hn") {
    return;
  }

  let jobsPayload;
  try {
    jobsPayload = await fetchJson("/api/v1/admin/scheduler/jobs");
  } catch {
    return;
  }
  if (!jobsPayload?.running) {
    return;
  }

  const nowMs = Date.now();
  const jobs = jobsPayload.jobs || [];
  const watched = jobs.filter((j) => j.kind === "daily_summary" || j.kind === "daily_preview");

  if (!scheduledRunWatch) {
    const dueSoon = watched.find((j) => {
      const nextRunMs = parseIsoToMs(j.next_run_time);
      if (!nextRunMs) {
        return false;
      }
      return nowMs >= (nextRunMs - 60000) && nowMs <= (nextRunMs + 90000);
    });

    if (dueSoon) {
      const label = dueSoon.kind === "daily_summary" ? "Scheduled 5:01 summary run" : "Scheduled 9:01 preview run";
      startRunProgress(`scheduled-${dueSoon.id}`, label, 55000);
      scheduledRunWatch = {
        jobId: dueSoon.id,
        kind: dueSoon.kind,
        startedAt: nowMs,
      };
    }
    return;
  }

  const latest = await fetchJson(`/api/v1/digest?kind=${scheduledRunWatch.kind}&limit=1`);
  const latestItem = (latest || [])[0];
  if (latestItem && latestItem.run_origin === "scheduled") {
    const createdMs = parseIsoToMs(latestItem.created_at);
    if (createdMs && createdMs >= (scheduledRunWatch.startedAt - 120000)) {
      finishRunProgress(`${scheduledRunWatch.kind} scheduled run complete`);
      scheduledRunWatch = null;
      await loadLatest();
      await loadSchedulerOverview();
      return;
    }
  }

  // Avoid hanging forever if scheduler run fails.
  if (nowMs - scheduledRunWatch.startedAt > 300000) {
    failRunProgress("Scheduled run timed out");
    scheduledRunWatch = null;
  }
}

function startSchedulerWatcher() {
  if (schedulerWatchTimer) {
    clearInterval(schedulerWatchTimer);
  }
  pollScheduledRunState().catch(() => {});
  schedulerWatchTimer = setInterval(() => {
    pollScheduledRunState().catch(() => {});
  }, 10000);
}

function pickClockTimezone() {
  return localStorage.getItem(CLOCK_TZ_KEY) || "America/New_York";
}

function formatClockNow(timeZone) {
  const now = new Date();
  return new Intl.DateTimeFormat("en-US", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  }).format(now);
}

function updateClockNow() {
  const clockEl = byId("clockNow");
  const selectEl = byId("clockTzSelect");
  if (!clockEl || !selectEl) {
    return;
  }
  clockEl.textContent = formatClockNow(selectEl.value);
}

function initClockWidget() {
  const selectEl = byId("clockTzSelect");
  if (!selectEl) {
    return;
  }

  const tz = pickClockTimezone();
  selectEl.value = tz;
  selectEl.addEventListener("change", () => {
    localStorage.setItem(CLOCK_TZ_KEY, selectEl.value);
    updateClockNow();
    updateCountdowns().catch(() => {});
    if (signalDashboardActive()) {
      loadLatest().catch(() => {});
    } else {
      loadLiveLlmRanking().catch(() => {});
    }
  });

  updateClockNow();
  if (clockTimer) {
    clearInterval(clockTimer);
  }
  clockTimer = setInterval(updateClockNow, 1000);
}

function isDotsHidden() {
  return localStorage.getItem(DOTS_HIDDEN_KEY) === "1";
}

function applyDotsVisibility() {
  const noise = document.querySelector(".noise");
  const btn = byId("dotsToggleBtn");
  const hidden = isDotsHidden();
  if (noise) noise.classList.toggle("dots-off", hidden);
  if (btn) btn.textContent = hidden ? "Dots: off" : "Dots: on";
}

function toggleDots() {
  localStorage.setItem(DOTS_HIDDEN_KEY, isDotsHidden() ? "0" : "1");
  applyDotsVisibility();
}

function toggleTopStories() {
  topStoriesExpanded = !topStoriesExpanded;
  const btn = byId("toggleTopStoriesBtn");
  if (btn) {
    btn.textContent = topStoriesExpanded ? "Show less" : "Show more";
  }
  if (currentDigest) {
    renderTopStories(byId("topStories"), currentDigest.top_links, currentDigest.best_rabbit_holes);
  }
}

applyTheme(pickTheme());
applyDotsVisibility();
initAutoModeControls();
renderChangelog();
updateVersionMeta(null);
configureAdminOverrideControls();
onClick("themeBtn", toggleTheme);
onClick("dotsToggleBtn", toggleDots);
onClick("pageFlipBtn", () => {
  togglePageFlip().catch((error) => setLoadingState(`Error: ${error.message}`));
});
onClick("refreshBtn", refreshNow);
onClick("cancelRefreshBtn", () => {
  cancelQueuedRefresh().catch((error) => setLoadingState(`Cancel failed: ${error.message}`));
});
onClick("toggleTopStoriesBtn", toggleTopStories);
onClick("toggleSchedulerPanelBtn", toggleSchedulerOverview);
onClick("refreshSchedulerOverviewBtn", () => {
  loadSchedulerOverview().catch(() => {});
});
onClick("adminOverrideRefreshBtn", () => {
  runAdminOverrideRefresh().catch((error) => setLoadingState(`Admin override failed: ${error.message}`));
});
onClick("navHnBtn", () => setActiveDashboard("hn"));
onClick("navRedditBtn", () => setActiveDashboard("reddit"));
onClick("navResearchBtn", () => setActiveDashboard("research"));
onClick("navSettingsBtn", () => setActiveDashboard("settings"));
onClick("tabLocalLlmsBtn", () => {
  setResearchTab("local");
  if (activeDashboard === "research") {
    loadLiveLlmRanking().catch((error) => setText("llmRankMeta", `Failed to load ranking: ${error.message}`));
  }
});
onClick("tabCloudLlmsBtn", () => {
  setResearchTab("cloud");
  if (activeDashboard === "research") {
    loadLiveLlmRanking().catch((error) => setText("llmRankMeta", `Failed to load ranking: ${error.message}`));
  }
});
onClick("snapPrevBtn", () => navigateSnapshot(1));
onClick("snapNextBtn", () => navigateSnapshot(-1));
onClick("runPreviewBtn", () =>
  runDailyKind("daily_preview", "previewMeta", "previewSummary", "runPreviewBtn"));
onClick("runSummaryBtn", () =>
  runDailyKind("daily_summary", "summaryMeta", "summarySummary", "runSummaryBtn"));
onClick("togglePreviewHistoryBtn", () =>
  toggleDailyHistory("daily_preview", "previewHistory", "togglePreviewHistoryBtn"));
onClick("toggleSummaryHistoryBtn", () =>
  toggleDailyHistory("daily_summary", "summaryHistory", "toggleSummaryHistoryBtn"));
onClick("snapshotModalCloseBtn", closeSnapshotDetailsModal);
onClick("snapshotModalBackdrop", closeSnapshotDetailsModal);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeSnapshotDetailsModal();
  }
});
for (const btn of document.querySelectorAll("[data-provider]")) {
  btn.addEventListener("click", () => {
    if (isAiAutoEnabled()) {
      setLoadingState("AI auto mode is enabled. Turn it off in the header to select a provider manually.");
      return;
    }
    switchProvider(btn.dataset.provider);
  });
}
loadProvider();
startCountdowns();
initClockWidget();
startSchedulerWatcher();
startSchedulerOverviewPolling();
setResearchTab("local");
setActiveDashboard("hn");
