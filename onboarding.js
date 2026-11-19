import { readFile, realpath } from "node:fs/promises"
import { relative, sep } from "node:path"
import { fileURLToPath } from "node:url"

const name = "vibe-onboard-project"
const toolName = "vibe_workflow_onboard"
const blocked = (reason) => ({ state: "blocked", reason })
const output = (value) => ({ content: JSON.stringify({ client: "opencode", ...value }) })
const snapshotPattern = /^[a-f0-9]{64}$/

export async function registerSkill(ctx, skill) {
  const path = fileURLToPath(new URL(`./skills/${skill}/SKILL.md`, import.meta.url))
  const [, description, content] = new RegExp(`^---\\nname: ${skill}\\ndescription: ([^\\n]+)\\n---\\n([\\s\\S]+)$`)
    .exec(await readFile(path, "utf8"))
  await ctx.skill.transform((editor) => editor.add({ id: skill, name: skill, path, description, content }))
}

const features = ["smart_router", "pull_requests"]

export async function registerOnboarding(ctx, projects, checkSetup, prGuard) {
  await registerSkill(ctx, name)
  await ctx.tool.transform((editor) => editor.add({
    name: toolName,
    description: `Preview or save a confirmed project preference: Smart Router (default) or pull request guidance ` +
      `(feature "pull_requests"). Follow the ${name} skill.`,
    input: {
      type: "object", additionalProperties: false, required: ["v", "op"],
      properties: {
        v: { type: "integer", const: 1 }, op: { enum: ["preview", "apply"] }, feature: { enum: features },
        enabled: { type: "boolean" }, agent_merge: { type: "boolean" },
        expected_snapshot: { type: "string", pattern: snapshotPattern.source },
      },
    },
    execute: async (request, invocation) => {
      const feature = request?.feature ?? "smart_router", prefs = feature === "pull_requests"
      if (!request || request.v !== 1 || !["preview", "apply"].includes(request.op) || !features.includes(feature) ||
          Object.keys(request).some((key) => !["v", "op", "feature", "enabled", "agent_merge", "expected_snapshot"].includes(key)) ||
          ("agent_merge" in request && !prefs) ||
          ["enabled", "agent_merge"].some((key) => key in request && typeof request[key] !== "boolean")) {
        return output(blocked("invalid_request"))
      }
      const { signal } = invocation, project = projects[feature]
      const values = prefs ? Object.fromEntries(["enabled", "agent_merge"].filter((key) => key in request)
        .map((key) => [key, request[key]])) : request.enabled
      const ready = async (enabled) => !enabled ? { state: "off", reason: null } : prefs ? prGuard() : checkSetup(signal)
      if (signal?.aborted) return output(blocked("project_settings_cancelled"))
      const proposed = await project.preview(values)
      if (proposed.state === "blocked") return output(proposed)
      if (request.op === "preview") {
        if ("expected_snapshot" in request) return output(blocked("invalid_request"))
        return output({ ...proposed, readiness: await ready(proposed.enabled) })
      }
      if (typeof request.enabled !== "boolean" || (prefs && typeof request.agent_merge !== "boolean") ||
          typeof request.expected_snapshot !== "string" ||
          !snapshotPattern.test(request.expected_snapshot)) return output(blocked("invalid_request"))
      if (request.expected_snapshot !== proposed.snapshot) return output(blocked("project_settings_changed"))
      if (proposed.change !== "none") {
        if (typeof ctx.permission?.assert !== "function" ||
            ![invocation.sessionID, invocation.messageID, invocation.id].every((item) => typeof item === "string" && item)) {
          return output(blocked("project_permission_unavailable"))
        }
        const path = relative(await realpath(ctx.location.directory), proposed.path).split(sep).join("/")
        await ctx.permission.assert({
          sessionID: invocation.sessionID, agent: invocation.agent,
          source: { type: "tool", messageID: invocation.messageID, id: invocation.id },
          action: "edit", resources: [path], save: [path],
        })
      }
      const applied = await project.apply(values, request.expected_snapshot, { signal })
      if (applied.state === "blocked") return output(applied)
      return output({ ...applied, readiness: await ready(applied.enabled) })
    },
  }))
}
