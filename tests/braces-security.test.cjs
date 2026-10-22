const assert = require("node:assert/strict");
const { createRequire } = require("node:module");
const path = require("node:path");
const { test } = require("node:test");

// Resolve through both real consumers, not a separate test-only dependency.
const consumers = [
  ["artifacts/api-server", "http-proxy-middleware"],
  ["artifacts/mockup-sandbox", "fast-glob"],
].map(([directory, dependency]) => {
  const consumerRequire = createRequire(path.resolve(directory, "package.json"));
  const dependencyRequire = createRequire(consumerRequire.resolve(dependency));
  const micromatchPath = dependencyRequire.resolve("micromatch");
  const micromatchRequire = createRequire(micromatchPath);
  return {
    directory,
    braces: micromatchRequire("braces"),
    micromatch: dependencyRequire("micromatch"),
    metadata: micromatchRequire("braces/package.json"),
  };
});

for (const { directory, braces, micromatch, metadata } of consumers) {
  test(`${directory}: resolves the pinned security backport`, () => {
    assert.equal(metadata.name, "@dieub/braces-depth-guard");
    assert.equal(metadata.version, "3.0.3-pn.3");
  });

  test(`${directory}: preserves ordinary expansion and glob matching`, () => {
    assert.deepEqual(braces.expand("src/{api,web}/*.{js,ts}"), [
      "src/api/*.js", "src/api/*.ts", "src/web/*.js", "src/web/*.ts",
    ]);
    assert.deepEqual(braces.expand("{1..3}"), ["1", "2", "3"]);
    assert.deepEqual(
      micromatch(["src/api/a.ts", "src/web/b.js", "src/other/c.ts"],
        "src/{api,web}/*.{js,ts}"),
      ["src/api/a.ts", "src/web/b.js"],
    );
  });

  test(`${directory}: rejects deep strings before exhausting the stack`, () => {
    for (const [open, close] of [["{", "}"], ["(", ")"]]) {
      const pattern = open.repeat(4000) + "a,b" + close.repeat(4000);
      for (const operation of ["parse", "compile", "expand", "stringify"]) {
        assert.throws(() => braces[operation](pattern), /exceeds max depth/);
        // An option must not let callers disable the hard security ceiling.
        assert.throws(
          () => braces[operation](pattern, { maxDepth: Infinity }),
          /exceeds max depth/,
        );
      }
    }
  });

  test(`${directory}: guards recursive walkers on caller-supplied ASTs`, () => {
    for (const operation of ["compile", "expand", "stringify"]) {
      let ast = { type: "text", value: "a" };
      for (let i = 0; i < 4000; i++) {
        ast = { type: "brace", nodes: [ast] };
      }
      ast = { type: "root", nodes: [ast] };
      assert.throws(() => braces[operation](ast), /exceeds max depth/);
    }
  });

  test(`${directory}: accepts supported nesting and lower depth limits`, () => {
    const pattern = "{".repeat(100) + "a,b" + "}".repeat(100);
    for (const operation of ["parse", "compile", "expand", "stringify"]) {
      assert.doesNotThrow(() => braces[operation](pattern));
      assert.throws(
        () => braces[operation]("{{a,b},c}", { maxDepth: 1 }),
        /exceeds max depth/,
      );
    }
  });
}
