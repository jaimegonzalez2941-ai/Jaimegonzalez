import assert from "node:assert/strict";
import test from "node:test";
import { isSameOriginMutation } from "../artifacts/api-server/src/lib/browserOrigin.ts";

const browser = {origin: "https://club.example", host: "club.example", fetchSite: "same-origin"};
test("bodyless generated link request is permitted only for same origin", () => {
  assert.equal(isSameOriginMutation({...browser, path: "/link"}), true);
  assert.equal(isSameOriginMutation({...browser, path: "/link", origin: "https://other.example"}), false);
  assert.equal(isSameOriginMutation({...browser, path: "/link", fetchSite: "cross-site"}), false);
  assert.equal(isSameOriginMutation({path: "/link", host: browser.host}), false);
});
test("data-bearing actions reject forms, malformed origins and cross-site JSON", () => {
  assert.equal(isSameOriginMutation({...browser, path: "/action", contentType: "application/json"}), true);
  assert.equal(isSameOriginMutation({...browser, path: "/action", contentType: "application/json; charset=utf-8"}), true);
  for (const contentType of [undefined, "text/plain", "application/x-www-form-urlencoded", "application/jsonp"]) {
    assert.equal(isSameOriginMutation({...browser, path: "/action", contentType}), false);
  }
  assert.equal(isSameOriginMutation({...browser, path: "/action", contentType: "application/json", origin: "invalid"}), false);
  assert.equal(isSameOriginMutation({...browser, path: "/action", contentType: "application/json", origin: "javascript://club.example"}), false);
});
