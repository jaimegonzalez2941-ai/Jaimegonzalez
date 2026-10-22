import assert from "node:assert/strict";
import test from "node:test";
import {
  GetPlayerAccessResponse,
  GetPlayerDevelopmentQueryParams,
  GetPlayerDevelopmentResponse,
} from "../lib/api-zod/src/generated/api.ts";

test("player reads accept no subject, player, staff screen or snapshot overrides", () => {
  const query = GetPlayerDevelopmentQueryParams.strict();
  const valid = { guild: "111111111111111111", page: 1 };
  assert.equal(query.safeParse(valid).success, true);
  for (const field of ["subject", "actor_id", "member", "player", "search", "screen", "snapshot", "operation"]) {
    assert.equal(query.safeParse({ ...valid, [field]: "private" }).success, false);
  }
  for (const page of [0, 10001, 1.5]) {
    assert.equal(query.safeParse({ ...valid, page }).success, false);
  }
});

test("player response schema strips accidentally supplied staff fields", () => {
  const result = GetPlayerDevelopmentResponse.parse({
    goals: [{
      id: 1, text: "Shared action", status: "active", dueAt: "2026-11-01T20:00:00Z", timezone: "UTC",
      description: "SECRET", criteria: "SECRET", outcome: "SECRET",
      member_id: "SECRET", owner_id: "SECRET", completed_by: "SECRET",
    }],
    page: 1, hasNext: false, operations: ["SECRET"], drafts: ["SECRET"], blocks: ["SECRET"],
  });
  assert.equal(JSON.stringify(result).includes("SECRET"), false);
  assert.deepEqual(Object.keys(result.goals[0]).sort(), ["dueAt", "id", "status", "text", "timezone"]);
  const access = GetPlayerAccessResponse.parse({
    linked: true, servers: [{ id: "111111111111111111", name: "My club", manager: true, operations: ["SECRET"] }],
    staff: true,
  });
  assert.deepEqual(access, { linked: true, servers: [{ id: "111111111111111111", name: "My club" }] });
});
