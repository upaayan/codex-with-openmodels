import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const worker = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "pi-auth-worker.mjs",
);

test("resolves an arbitrary OAuth provider through its Pi extension", async () => {
  const agentDir = await mkdtemp(path.join(os.tmpdir(), "sudhir-pi-auth-"));
  const extensionsDir = path.join(agentDir, "extensions");
  await mkdir(extensionsDir, { recursive: true });
  await writeFile(
    path.join(agentDir, "models.json"),
    JSON.stringify({ providers: {} }),
  );
  await writeFile(
    path.join(agentDir, "auth.json"),
    JSON.stringify({
      "future-oauth": {
        type: "oauth",
        access: "extension-access",
        refresh: "extension-refresh",
        expires: 9_999_999_999_999,
      },
    }),
    { mode: 0o600 },
  );
  await writeFile(
    path.join(extensionsDir, "future-oauth.js"),
    `export default function (pi) {
      pi.registerProvider("future-oauth", {
        name: "Future OAuth",
        baseUrl: "https://future.example/v1",
        api: "openai-responses",
        models: [{
          id: "future-model",
          name: "Future Model",
          api: "openai-responses",
          reasoning: true,
          input: ["text"],
          contextWindow: 128000,
          maxTokens: 32000
        }],
        oauth: {
          name: "Future OAuth",
          login: async () => { throw new Error("login is not used"); },
          refreshToken: async (credentials) => credentials,
          getApiKey: (credentials) => credentials.access
        }
      });
    }\n`,
  );

  const request = {
    id: "request-1",
    provider: "future-oauth",
    model: "future-model",
    api: "openai-responses",
  };
  const result = spawnSync(process.execPath, [worker], {
    cwd: agentDir,
    env: { ...process.env, SUDHIR_PI_AGENT_DIR: agentDir },
    input: `${JSON.stringify(request)}\n`,
    encoding: "utf8",
  });
  const response = result.stdout
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line))
    .find((value) => value.id === request.id);

  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(response, {
    id: request.id,
    ok: true,
    provider: "future-oauth",
    model: "future-model",
    api: "openai-responses",
    apiKey: "extension-access",
    headers: {},
    baseUrl: null,
  });
});
