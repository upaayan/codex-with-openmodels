/** Ambient Cursor settingSources for the Codex worker. Default is none. */

const DISABLED = new Set(["0", "false", "off", "none", "omit", "disabled"]);
const ALL = new Set(["1", "true", "on", "all"]);

export function resolveSudhirCursorSettingSources(
  raw = process.env.SUDHIR_CURSOR_SETTING_SOURCES,
) {
  const trimmed = typeof raw === "string" ? raw.trim() : "";
  if (!trimmed) return undefined;
  const normalized = trimmed.toLowerCase();
  if (DISABLED.has(normalized)) return undefined;
  if (ALL.has(normalized)) return ["all"];
  const sources = trimmed
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
  return sources.length ? sources : undefined;
}
