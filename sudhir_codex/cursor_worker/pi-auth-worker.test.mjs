import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  assistantToChatResponse,
  chatRequestToPiContext,
  completeWithUpstreamCapture,
} from "./pi-auth-worker.mjs";

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

test("round-trips Google thought signatures on their original content blocks", () => {
  const model = {
    id: "gemini-3.7-flash",
    provider: "google",
    api: "google-generative-ai",
  };
  const assistant = {
    role: "assistant",
    api: model.api,
    provider: model.provider,
    model: model.id,
    content: [
      {
        type: "thinking",
        thinking: "check tools",
        thinkingSignature: "dGhpbmtpbmc=",
      },
      {
        type: "toolCall",
        id: "call-1",
        name: "shell_command",
        arguments: { command: "pwd" },
        thoughtSignature: "dG9vbA==",
      },
      { type: "text", text: "done", textSignature: "dGV4dA==" },
    ],
    usage: {
      input: 8,
      output: 5,
      cacheRead: 0,
      cacheWrite: 0,
      reasoning: 2,
      totalTokens: 13,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    },
    stopReason: "toolUse",
    timestamp: 1,
  };

  const response = assistantToChatResponse(assistant);
  const chatMessage = response.choices[0].message;
  const context = chatRequestToPiContext(
    {
      messages: [
        { role: "assistant", ...chatMessage },
        { role: "tool", tool_call_id: "call-1", content: "ok" },
      ],
      tools: [
        {
          type: "function",
          function: {
            name: "shell_command",
            description: "Run a command",
            parameters: { type: "object", properties: {} },
          },
        },
      ],
    },
    model,
  );

  assert.deepEqual(context.messages[0].content, assistant.content);
  assert.equal(context.messages[0].provider, model.provider);
  assert.equal(context.messages[0].model, model.id);
  assert.equal(context.messages[1].toolName, "shell_command");
  assert.deepEqual(response.usage.completion_tokens_details, {
    reasoning_tokens: 2,
  });
});

test("captures native Google HTTP failures for gateway passthrough", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response('{"error":{"message":"quota exceeded"}}', {
      status: 429,
      headers: {
        "content-type": "application/problem+json",
        "x-request-id": "google-request-429",
        "set-cookie": "must-not-cross-worker",
      },
    });
  try {
    const result = await completeWithUpstreamCapture(async () => {
      await globalThis.fetch("https://generativelanguage.googleapis.com/test");
      return { stopReason: "error" };
    });
    assert.deepEqual(result, {
      message: { stopReason: "error" },
      upstream: {
        status: 429,
        headers: {
          "content-type": "application/problem+json",
          "x-request-id": "google-request-429",
        },
        bodyBase64: Buffer.from(
          '{"error":{"message":"quota exceeded"}}',
        ).toString("base64"),
      },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("disables redirects for native Google fetches", async () => {
  const originalFetch = globalThis.fetch;
  let redirect;
  globalThis.fetch = async (_input, init) => {
    redirect = init?.redirect;
    return new Response(null, { status: 302 });
  };
  try {
    const result = await completeWithUpstreamCapture(async () => {
      await globalThis.fetch("https://generativelanguage.googleapis.com/test");
      return { stopReason: "error" };
    });
    assert.equal(redirect, "manual");
    assert.equal(result.upstream.status, 302);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
