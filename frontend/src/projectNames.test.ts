import assert from "node:assert/strict";
import test from "node:test";

import { extractProjectName, normalizeProjectName } from "./projectNames.ts";

test("extracts a project name from current and legacy filenames", () => {
  assert.equal(extractProjectName("workstation_posti_2.1.py"), "workstation");
  assert.equal(extractProjectName("legacy_v1.0.py"), "legacy");
});

test("normalizes a project name for safe and predictable artifacts", () => {
  assert.equal(normalizeProjectName("  Office workstation  "), "Office-workstation");
  assert.equal(normalizeProjectName("../../unsafe"), "unsafe");
});
