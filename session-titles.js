export const MARK = "￻"
export const PREFIX = /^￻?[▰▱]{5} (?:WAITING · )?/
export const DEFAULT_TITLE = /^New session - \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/

export const strip = (title) => title.replace(PREFIX, "")

export const compose = (topic, cells, waiting) =>
  `${MARK}${"▰".repeat(cells)}${"▱".repeat(5 - cells)} ${waiting ? "WAITING · " : ""}${topic}`

export function cellsFor({ changed, declared, pr, exists, cleanupOn, primary }) {
  const superseded = pr?.state === "CLOSED" && pr.title.startsWith("[SUPERSEDED]")
  const cleaned = !cleanupOn || primary || !exists || superseded
  if ((pr?.state === "MERGED" || superseded) && cleaned) return 5
  const done = declared.filter((path) => changed.includes(path)).length
  if (pr || (declared.length > 0 && done === declared.length)) return 4
  if (declared.length > 0 && done >= Math.ceil(declared.length / 2)) return 3
  if (changed.some((path) => !path.startsWith("docs/"))) return 2
  return 1
}
