import { readFile, realpath } from "node:fs/promises"
import { relative, sep } from "node:path"
import { fileURLToPath } from "node:url"

const name = "vibe-onboard-project"
const skillPath = fileURLToPath(new URL(`./skills/${name}/SKILL.md`, import.meta.url))
const toolName = "vibe_workflow_onboard"
const blocked = (reason) => ({ state: "blocked", reason })
const output = (value) => ({ content: JSON.stringify({ client: "opencode", ...value }) })
const snapshotPattern = /^[a-f0-9]{64}$/

export async function registerOnboarding(ctx, project, checkSetup) {
  const text = await readFile(skillPath, "utf8")
  const [, description, content] = /^---\nname: vibe-onboard-project\ndescription: ([^\n]+)\n---\n([\s\S]+)$/.exec(text)
  await ctx.skill.transform((editor) => editor.add({ id: name, name, path: skillPath, description, content }))
  await ctx.tool.transform((editor) => editor.add({
    name: toolName,
    description: `Preview or save a confirmed Smart Router project preference. Follow the ${name} skill.`,
    input: {
      type: "object", additionalProperties: false, required: ["v", "op"],
      properties: {
        v: { type: "integer", const: 1 }, op: { enum: ["preview", "apply"] },
        enabled: { type: "boolean" }, expected_snapshot: { type: "string", pattern: snapshotPattern.source },
      },
    },
    execute: async (request, invocation) => {
      if (!request || request.v !== 1 || !["preview", "apply"].includes(request.op) ||
          Object.keys(request).some((key) => !["v", "op", "enabled", "expected_snapshot"].includes(key)) ||
          ("enabled" in request && typeof request.enabled !== "boolean")) return output(blocked("invalid_request"))
      const { signal } = invocation
      if (signal?.aborted) return output(blocked("project_settings_cancelled"))
      const proposed = await project.preview(request.enabled)
      if (proposed.state === "blocked") return output(proposed)
      if (request.op === "preview") {
        if ("expected_snapshot" in request) return output(blocked("invalid_request"))
        return output({ ...proposed, readiness: proposed.enabled ? await checkSetup(signal) : { state: "off", reason: null } })
      }
      if (typeof request.enabled !== "boolean" || typeof request.expected_snapshot !== "string" ||
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
      const applied = await project.apply(request.enabled, request.expected_snapshot, { signal })
      if (applied.state === "blocked") return output(applied)
      return output({ ...applied, readiness: applied.enabled ? await checkSetup(signal) : { state: "off", reason: null } })
    },
  }))
}
