import { isPlainObject, parseIsoTimestamp, utcNowIso } from "./events.js";
import type { Client } from "./types.js";

type JsonRecord = Record<string, any>;

function datetimeToIso(value: Date | null): string | null {
  return value ? value.toISOString().replace(/\.\d{3}Z$/, "Z") : null;
}

function htmlText(value: any): string {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#x27;");
}

function formatDashboardNumber(value: any): string {
  if (typeof value === "boolean") {
    return String(value);
  }
  if (Number.isInteger(value)) {
    return value.toLocaleString("en-US");
  }
  if (typeof value === "number") {
    return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }
  return String(value);
}

function htmlCount(value: any): string {
  if (value === null || value === undefined) {
    return "n/a";
  }
  return htmlText(formatDashboardNumber(value));
}

function formatDashboardTimestamp(value: any): string {
  if (value === null || value === undefined) {
    return "n/a";
  }
  try {
    const parsed = parseIsoTimestamp(String(value));
    return `${parsed.toLocaleString("en-US", { month: "short", timeZone: "UTC" })} ${parsed.getUTCDate()}, ${parsed.getUTCFullYear()} ${String(parsed.getUTCHours()).padStart(2, "0")}:${String(parsed.getUTCMinutes()).padStart(2, "0")} UTC`;
  } catch {
    return String(value);
  }
}

function dashboardRangeText(report: JsonRecord): string {
  const timestamps: Date[] = [];
  for (const sectionName of ["runs", "reviews", "validation", "gates", "tokens"]) {
    const section = report[sectionName];
    const range = isPlainObject(section) ? section.range ?? {} : {};
    for (const key of ["first_timestamp", "last_timestamp"]) {
      const timestamp = range[key];
      if (timestamp === null || timestamp === undefined) {
        continue;
      }
      try {
        timestamps.push(parseIsoTimestamp(String(timestamp)));
      } catch {
        // Ignore malformed display timestamps.
      }
    }
  }
  if (!timestamps.length) {
    return "no matching events";
  }
  const first = new Date(Math.min(...timestamps.map((item) => item.getTime())));
  const last = new Date(Math.max(...timestamps.map((item) => item.getTime())));
  const firstText = formatDashboardTimestamp(datetimeToIso(first));
  const lastText = formatDashboardTimestamp(datetimeToIso(last));
  return first.getTime() === last.getTime() ? firstText : `${firstText} to ${lastText}`;
}

function htmlStatusBadge(status: any): string {
  const statusText = String(status);
  const statusSlug = statusText.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "unknown";
  return `<span class="status-badge status-${htmlText(statusSlug)}">${htmlText(statusText)}</span>`;
}

function formatDashboardCounterMap(values: JsonRecord): string {
  return Object.entries(values)
    .filter(([, value]) => value)
    .map(([key, value]) => `${key}=${formatDashboardNumber(value)}`)
    .join(", ");
}

function htmlMetricCard(label: string, value: any, detail: string): string {
  return `<section class="metric-card"><span>${htmlText(label)}</span><strong>${htmlCount(value)}</strong><small>${htmlText(detail)}</small></section>`;
}

function htmlTable(headers: string[], rows: string[][], emptyText: string): string {
  const headerCells = headers.map((header) => `<th scope="col">${htmlText(header)}</th>`).join("");
  const body = rows.length
    ? rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`).join("")
    : `<tr><td colspan="${headers.length}" class="empty">${htmlText(emptyText)}</td></tr>`;
  return `<table><thead><tr>${headerCells}</tr></thead><tbody>${body}</tbody></table>`;
}

function htmlTableRow(values: any[]): string[] {
  return values.map((value) => htmlCount(value));
}

function htmlDefinitionList(items: [string, any][]): string {
  if (!items.length) {
    return '<p class="empty">none</p>';
  }
  return `<dl>${items.map(([label, value]) => `<div><dt>${htmlText(label)}</dt><dd>${htmlCount(value)}</dd></div>`).join("")}</dl>`;
}

function dashboardTokenMetric(tokens: JsonRecord): [any, string] {
  if (tokens.exact.row_count) {
    return [tokens.exact.totals.total_tokens, "exact total"];
  }
  if (tokens.estimated.row_count) {
    return [tokens.estimated.totals.total_tokens, "estimated total"];
  }
  if (tokens.unavailable.row_count) {
    return ["n/a", "unavailable totals"];
  }
  return [0, "exact total"];
}

function htmlRunDetailSection(runs: JsonRecord): string {
  const runItems = runs.items ?? [];
  if (!runItems.length) {
    return '<section class="panel run-details"><h2>Run details</h2><p class="empty">no runs matched these filters</p></section>';
  }
  const details = runItems.map((run: JsonRecord) => {
    const validation = run.validation;
    const gates = run.gates;
    const reviews = run.reviews;
    const [tokenValue, tokenDetail] = dashboardTokenMetric(run.tokens);
    const gateCount = Object.values(gates.gate_type_counts).reduce((sum: number, value: any) => sum + Number(value), 0);
    const validationFailures = Object.values(validation.command_kinds).reduce((sum: number, value: any) => sum + Number(value.failure_count), 0);
    const validationRows = Object.entries(validation.command_kinds).map(([kind, summary]: [string, any]) => htmlTableRow([
      kind,
      summary.attempt_count,
      summary.failure_count,
      summary.retry_count,
      summary.final_pass_count,
      summary.final_fail_count,
    ]));
    const gateRows = Object.entries(gates.gate_type_counts).map(([gateType, total]) => htmlTableRow([
      gateType,
      total,
      formatDashboardCounterMap(gates.decision_counts[gateType] ?? {}) || "none",
    ]));
    return [
      '<details class="run-detail">',
      "<summary>",
      `<span class="run-id">${htmlText(run.run_id)}</span>`,
      `<span>${htmlText(run.skill)}</span>`,
      htmlStatusBadge(run.status),
      `<span>${htmlText(formatDuration(run.duration_seconds))}</span>`,
      "</summary>",
      '<div class="run-detail-body">',
      htmlDefinitionList([
        ["first seen", formatDashboardTimestamp(run.first_timestamp)],
        ["last seen", formatDashboardTimestamp(run.last_timestamp)],
        ["validation attempts", validation.attempt_count],
        ["validation failures", validationFailures],
        ["gate decisions", gateCount],
        ["review passes", reviews.review_count],
        ["open questions", reviews.open_question_count],
        ["token total", tokenValue],
        ["token source", tokenDetail],
      ]),
      '<div class="run-detail-grid">',
      "<section><h3>Validation</h3>",
      htmlTable(["Kind", "Attempts", "Failures", "Retries", "Final passes", "Final failures"], validationRows, "no validation attempts"),
      "</section>",
      "<section><h3>Gates</h3>",
      htmlTable(["Gate", "Total", "Decisions"], gateRows, "no gate decisions"),
      "</section>",
      "</div>",
      "</div>",
      "</details>",
    ].join("");
  });
  return `<section class="panel run-details"><h2>Run details</h2>${details.join("")}</section>`;
}

function htmlIncompleteRunSection(runs: JsonRecord): string {
  const items = runs.incomplete_items ?? [];
  if (!items.length) {
    return "";
  }
  const rows = items.map((item: JsonRecord) => htmlTableRow([
    item.run_id,
    item.skill,
    item.reason,
    formatDashboardTimestamp(item.first_timestamp),
    formatDashboardTimestamp(item.last_timestamp),
  ]));
  return `<section class="panel"><h2>Incomplete / ambiguous runs</h2>${htmlTable(["Run", "Skill", "Reason", "First seen", "Last seen"], rows, "no incomplete or ambiguous runs matched these filters")}</section>`;
}

export function renderDashboardHtml(
  report: JsonRecord,
  options: { client: Client; generatedAt?: string | null },
): string {
  const generated = options.generatedAt ?? utcNowIso();
  const runs = report.runs;
  const reviews = report.reviews;
  const validation = report.validation;
  const gates = report.gates;
  const tokens = report.tokens;
  const filters = report.filters;
  const warningCount = Number(report.warning_count ?? 0);
  const warningHtml = warningCount
    ? `<section class="warning"><strong>Warnings</strong><span>skipped ${warningCount} malformed historical lines</span></section>`
    : "";
  const filterText = Object.entries({
    client: options.client,
    repo: filters.repo,
    current_repo: filters.current_repo,
    skill: filters.skill || "all",
    since: filters.since || "beginning",
    until: filters.until || "now",
  })
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([name, value]) => `${name}=${value}`)
    .join(" ");
  const runRows = runs.groups.map((group: JsonRecord) => [
    htmlCount(group.skill),
    htmlStatusBadge(group.status),
    htmlCount(group.run_count),
    htmlCount(formatDuration(group.average_duration_seconds)),
    htmlCount(formatDashboardTimestamp(group.last_timestamp)),
  ]);
  const validationRows = Object.entries(validation.command_kinds).map(([kind, summary]: [string, any]) => htmlTableRow([
    kind,
    summary.attempt_count,
    summary.failure_count,
    summary.retry_count,
    summary.final_pass_count,
    summary.final_fail_count,
  ]));
  const gateRows = Object.entries(gates.gate_type_counts).map(([gateType, total]) => htmlTableRow([
    gateType,
    total,
    formatDashboardCounterMap(gates.decision_counts[gateType] ?? {}) || "none",
  ]));
  const tokenRows = ["exact", "estimated", "unavailable"].map((sourceQuality) => {
    const summary = tokens[sourceQuality];
    return htmlTableRow([
      sourceQuality,
      summary.row_count,
      summary.session_count,
      summary.totals.input_tokens,
      summary.totals.output_tokens,
      summary.totals.cache_read_tokens,
      summary.totals.cache_write_tokens,
      summary.totals.total_tokens,
    ]);
  });
  const [tokenMetricValue, tokenMetricDetail] = dashboardTokenMetric(tokens);
  const validationFailures = Object.values(validation.command_kinds).reduce((sum: number, summary: any) => sum + Number(summary.failure_count), 0);
  const validationFinalFailures = Object.values(validation.command_kinds).reduce((sum: number, summary: any) => sum + Number(summary.final_fail_count), 0);

  return [
    "<!doctype html>",
    '<html lang="en">',
    "<head>",
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    "<title>Dreamers Stats</title>",
    "<style>",
    ":root{color-scheme:light;--ink:#17211b;--muted:#5d6b62;--line:#d8e0da;--paper:#f8faf7;--panel:#ffffff;--accent:#0f6f5f;--warn:#9a5b00;--ok:#176d3b;--hold:#a24b1d;--active:#1f5f99}",
    "body{margin:0;background:linear-gradient(180deg,#eef5ef,#f8faf7 34%);color:var(--ink);font:15px/1.5 ui-sans-serif,system-ui,sans-serif}",
    "main{max-width:1120px;margin:0 auto;padding:32px 20px 48px}",
    "header{margin-bottom:24px}h1{font-size:34px;line-height:1.1;margin:0 0 8px}h2{font-size:18px;margin:0 0 12px}",
    ".filters,.generated,.range,small{color:var(--muted)}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}",
    ".metric-card,section.panel,.warning{border:1px solid var(--line);background:var(--panel);border-radius:8px;box-shadow:0 1px 1px rgba(23,33,27,.04)}",
    ".metric-card{padding:14px}.metric-card span{display:block;color:var(--muted);font-size:12px;text-transform:uppercase}.metric-card strong{display:block;font-size:26px;margin:4px 0}",
    ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}.panel{padding:18px;margin:16px 0;overflow:auto}.warning{display:flex;gap:8px;padding:12px 14px;color:var(--warn);margin:16px 0}",
    "table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid var(--line);padding:8px 6px;vertical-align:top}th{font-size:12px;color:var(--muted);text-transform:uppercase}",
    ".status-badge{display:inline-flex;align-items:center;border-radius:999px;padding:2px 8px;font-size:12px;font-weight:700;background:#eef3f0;color:var(--muted)}.status-completed{background:#e6f4eb;color:var(--ok)}.status-in-progress{background:#e8f1fb;color:var(--active)}.status-halted{background:#fff0e6;color:var(--hold)}",
    "dl{display:grid;gap:8px;margin:0}dl div{display:flex;justify-content:space-between;gap:16px;border-bottom:1px solid var(--line);padding:6px 0}dt{color:var(--muted)}dd{margin:0;text-align:right}.empty{color:var(--muted)}",
    ".run-detail{border:1px solid var(--line);border-radius:8px;margin:10px 0;background:#fbfdfb}.run-detail summary{cursor:pointer;display:grid;grid-template-columns:minmax(180px,1fr) minmax(120px,.7fr) auto auto;gap:12px;align-items:center;padding:12px 14px}.run-id{font-weight:700}.run-detail-body{padding:0 14px 14px}.run-detail-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin-top:14px}.run-detail h3{font-size:14px;margin:0 0 8px;color:var(--muted);text-transform:uppercase}@media(max-width:700px){.run-detail summary{grid-template-columns:1fr}}",
    "</style>",
    "</head>",
    "<body>",
    "<main>",
    "<header>",
    "<h1>Dreamers Stats</h1>",
    `<div class="filters">Filters: ${htmlText(filterText)}</div>`,
    `<div class="generated">Generated: ${htmlText(formatDashboardTimestamp(generated))}</div>`,
    `<div class="range">Data range: ${htmlText(dashboardRangeText(report))}</div>`,
    "</header>",
    warningHtml,
    '<section class="metrics">',
    htmlMetricCard("Runs", runs.run_count, "reliable skill invocations"),
    htmlMetricCard("Validation", validation.attempt_count, `${validationFailures} failed, ${validationFinalFailures} final failures`),
    htmlMetricCard("Reviews", reviews.review_count, `${reviews.blocked_count} blocked, ${reviews.open_question_count} open questions`),
    htmlMetricCard("Gates", Object.values(gates.gate_type_counts).reduce((sum: number, value: any) => sum + Number(value), 0), `${Object.keys(gates.gate_type_counts).length} gate types`),
    htmlMetricCard("Tokens", tokenMetricValue, tokenMetricDetail),
    "</section>",
    '<section class="panel"><h2>Runs by skill</h2>',
    htmlTable(["Skill", "Status", "Runs", "Avg duration", "Last seen"], runRows, "no runs matched these filters"),
    "</section>",
    htmlRunDetailSection(runs),
    htmlIncompleteRunSection(runs),
    '<section class="panel"><h2>Validation</h2>',
    htmlTable(["Kind", "Attempts", "Failures", "Retries", "Final passes", "Final failures"], validationRows, "no validation attempts matched these filters"),
    "</section>",
    '<section class="grid">',
    '<section class="panel"><h2>Reviews</h2>',
    htmlDefinitionList([
      ["initial reviews", reviews.initial_review_count],
      ["rereviews", reviews.rereview_count],
      ["open questions", reviews.open_question_count],
      ["findings", formatCounterMap(reviews.findings_by_severity) || "none"],
      ["artifact mismatches", reviews.artifact_summary.mismatch_count],
    ]),
    "</section>",
    '<section class="panel"><h2>Gates</h2>',
    htmlTable(["Gate", "Total", "Decisions"], gateRows, "no gate decisions matched these filters"),
    "</section>",
    '<section class="panel"><h2>Tokens</h2>',
    htmlTable(["Quality", "Rows", "Sessions", "Input", "Output", "Cache read", "Cache write", "Total"], tokenRows, "no token usage matched these filters"),
    "</section>",
    "</section>",
    "</main>",
    "</body>",
    "</html>",
    "",
  ].join("\n");
}

function formatDuration(seconds: number): string {
  const minutesTotal = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  const hours = Math.floor(minutesTotal / 60);
  const minutes = minutesTotal % 60;
  if (hours) {
    return `${hours}h${minutes}m${remainder}s`;
  }
  if (minutes) {
    return `${minutes}m${remainder}s`;
  }
  return `${remainder}s`;
}

function formatCounterMap(values: JsonRecord): string {
  return Object.entries(values)
    .filter(([, value]) => value)
    .map(([key, value]) => `${key}=${value}`)
    .join(", ");
}
