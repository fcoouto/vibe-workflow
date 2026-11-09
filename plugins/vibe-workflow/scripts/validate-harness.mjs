import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { test } from "node:test";

assert(process.argv[2], "Usage: node scripts/validate-harness.mjs <disposable-fixture-directory>");
const directory = path.resolve(process.argv[2]);
const { canReadRecord } = await import(pathToFileURL(path.join(directory, "access.mjs")));
const actor = { id: "user-1", tenantId: "tenant-a" };
const record = { ownerId: "user-1", tenantId: "tenant-a" };

test("allows the owner in the same tenant", () => {
  assert.equal(canReadRecord(actor, record), true);
});

test("denies another user in the same tenant", () => {
  assert.equal(canReadRecord({ ...actor, id: "user-2" }, record), false);
});

test("denies matching user IDs across tenants", () => {
  assert.equal(canReadRecord({ ...actor, tenantId: "tenant-b" }, record), false);
});

test("denies absent actors and records", () => {
  assert.equal(canReadRecord(null, record), false);
  assert.equal(canReadRecord(actor, undefined), false);
});

test("denies missing, empty, or non-string identities", () => {
  for (const value of [undefined, null, "", 0, false, 1, true, {}, []]) {
    assert.equal(canReadRecord({ ...actor, id: value }, { ...record, ownerId: value }), false);
    assert.equal(canReadRecord({ ...actor, tenantId: value }, { ...record, tenantId: value }), false);
  }
});

test("does not mutate its inputs", () => {
  const frozenActor = Object.freeze({ ...actor });
  const frozenRecord = Object.freeze({ ...record });
  assert.equal(canReadRecord(frozenActor, frozenRecord), true);
  assert.deepEqual(frozenActor, actor);
  assert.deepEqual(frozenRecord, record);
});

test("preserves the local customization", async () => {
  assert.equal(await readFile(path.join(directory, "local-notes.txt"), "utf8"),
    await readFile(new URL("../assets/harness-evaluation/boundary/local-notes.txt", import.meta.url), "utf8"));
});
