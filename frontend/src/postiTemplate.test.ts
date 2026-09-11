import assert from "node:assert/strict";
import test from "node:test";

import { buildScript, extractProfilesFromScript } from "./postiTemplate.ts";
import type { SerializedProfiles } from "./postiTemplate.ts";


test("profile data containing triple quotes remains a safe string literal", () => {
  const payload: SerializedProfiles = {
    test: {
      label: "Triple quotes",
      description: "A value with \"\"\" inside",
      steps: [
        {
          title: "Safe embedding",
          command: "printf '\"\"\"'",
          enabled: true,
        },
      ],
    },
  };

  const generated = buildScript(payload, '2.1.0"quoted');
  const extracted = extractProfilesFromScript(generated);

  assert.ok(extracted);
  assert.deepEqual(extracted.payload, payload);
  assert.equal(extracted.version, '2.1.0"quoted');
  assert.match(generated, /PROFILE_DATA_JSON = "\{/);
  assert.doesNotMatch(generated, /PROFILE_DATA_JSON = r?"""/);
});


test("legacy triple-quoted projects remain importable", () => {
  const generated = buildScript({}, "1.0").replace(
    /PROFILE_DATA_JSON = "(?:\\.|[^"\\])*"/s,
    'PROFILE_DATA_JSON = r"""{}"""'
  );

  const extracted = extractProfilesFromScript(generated);
  assert.ok(extracted);
  assert.deepEqual(extracted.payload, {});
});
