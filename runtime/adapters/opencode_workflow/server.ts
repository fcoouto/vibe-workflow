import { Plugin } from "@opencode/plugin"
import { createWorkflowPlugin } from "./index.ts"
import { createStopForms } from "./connector.ts"
import { StopReview } from "./rpc.ts"
import type { Submission } from "./stop.ts"

export default Plugin.define({
  id: "vibe-workflow",
  async setup(ctx) {
    return createWorkflowPlugin({
      stopForms: createStopForms(ctx.location.directory),
      registerStopOwner: async (owner) => {
        const registration = await ctx.rpc.register(StopReview, {
          pending: async (input: any) => owner.pending(input.sessionID),
          open: async (input: any) => owner.open(input.sessionID, input.formID, input.viewID),
          begin: (input: any) => owner.begin(input as Submission),
          complete: async (input: any) => { owner.complete(input.ticket, input.outcome); return null },
          abandon: async (input: any) => { owner.abandon(input.attemptID, input.viewID); return null },
        })
        const unsubscribe = owner.observe((notice) => { void registration.events.emit("changed", notice).catch(() => {}) })
        return () => { unsubscribe(); void registration.dispose() }
      },
    }).setup(ctx)
  },
})
