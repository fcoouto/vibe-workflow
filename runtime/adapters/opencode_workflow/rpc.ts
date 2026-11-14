import { Rpc } from "@opencode/plugin/rpc"

const id = { type: "string", minLength: 1, maxLength: 256 } as const
const text = (maxLength: number) => ({ type: "string", maxLength })
const object = (properties: Record<string, unknown>) => ({ type: "object" as const, properties,
  required: Object.keys(properties), additionalProperties: false })
const notice = object({ sessionID: id, rootID: id, formID: id, attemptID: id,
  state: { enum: ["reviewing", "accepted", "completed", "failed", "cancelled"] } })

export const StopReview = Rpc.define({
  id: "vibe-workflow.stop-review",
  methods: {
    pending: { input: object({ sessionID: id }), output: { type: "array", maxItems: 256, items: notice } },
    open: { input: object({ sessionID: id, formID: id, viewID: id }), output: object({
      generation: id, attemptID: id, sessionID: id, rootID: id, formID: id,
      file: text(4096), reason: text(1024), patch: text(65536),
      contentDigest: { type: "string", pattern: "^[a-f0-9]{64}$" },
      remainingMS: { type: "number", exclusiveMinimum: 0, maximum: 110000 },
    }) },
    begin: { input: object({ attemptID: id, viewID: id, submissionID: id,
      contentDigest: { type: "string", pattern: "^[a-f0-9]{64}$" }, decision: { enum: ["approve", "reject"] } }), output: id },
    complete: { input: object({ ticket: id, outcome: { enum: ["settled", "failed"] } }), output: { type: "null" } },
    abandon: { input: object({ attemptID: id, viewID: id }), output: { type: "null" } },
  },
  events: { changed: { schema: notice } },
})
