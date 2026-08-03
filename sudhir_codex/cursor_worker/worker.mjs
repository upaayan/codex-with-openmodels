#!/usr/bin/env node

import { createHash, randomUUID } from "node:crypto";
import { readFile, mkdir, rm, stat } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { Agent, Cursor, JsonlLocalAgentStore } from "@cursor/sdk";

const ROUTES = new Map([
  [
    "cursor/composer-2.5-fast",
    { alias: "composer-2.5", fast: true },
  ],
  [
    "cursor/composer-2.5-slow",
    { alias: "composer-2.5", fast: false },
  ],
  [
    "cursor/composer-latest-fast",
    { alias: "composer-latest", fast: true },
  ],
  [
    "cursor/composer-latest-slow",
    { alias: "composer-latest", fast: false },
  ],
]);
const MODEL_CACHE_MS = 10 * 60 * 1000;

const runningAsMain = process.argv[1]
  ? import.meta.url === pathToFileURL(process.argv[1]).href
  : false;
const protocolWrite = process.stdout.write.bind(process.stdout);

// Cursor's native runtime may print diagnostics. Keep stdout exclusively for
// the gateway protocol and inherit stderr into the private gateway log.
if (runningAsMain) {
  process.stdout.write = (chunk, ...args) =>
    process.stderr.write(chunk, ...args);
}

process.umask(0o077);

const authPath = process.env.SUDHIR_CURSOR_AUTH_PATH;
const stateDir = process.env.SUDHIR_CURSOR_STATE_DIR;
if (!authPath || !stateDir) {
  throw new Error("Cursor worker paths were not configured");
}

await mkdir(stateDir, { recursive: true, mode: 0o700 });
const workerStateDir = path.join(
  stateDir,
  "workers",
  `${process.pid}-${randomUUID()}`,
);
await mkdir(workerStateDir, { recursive: true, mode: 0o700 });
const store = new JsonlLocalAgentStore(workerStateDir);
Cursor.configure({ local: { store } });

let modelCache = null;

function emit(message) {
  protocolWrite(`${JSON.stringify(message)}\n`);
}

function modelAliases(model) {
  return [model.id, ...(Array.isArray(model.aliases) ? model.aliases : [])]
    .filter(
      (value, index, values) =>
        typeof value === "string" && values.indexOf(value) === index,
    );
}

async function readApiKey() {
  let document;
  try {
    document = JSON.parse(await readFile(authPath, "utf8"));
  } catch {
    throw new Error("Pi Cursor authentication could not be read");
  }
  const cursor = document?.cursor;
  const key =
    typeof cursor?.key === "string"
      ? cursor.key
      : typeof cursor?.apiKey === "string"
        ? cursor.apiKey
        : null;
  if (!key) {
    throw new Error("Pi Cursor authentication is unavailable");
  }
  return key;
}

async function availableModels(apiKey) {
  const keyHash = createHash("sha256").update(apiKey).digest("hex");
  if (
    modelCache &&
    modelCache.keyHash === keyHash &&
    modelCache.expiresAt > Date.now()
  ) {
    return modelCache.models;
  }
  const models = await Cursor.models.list({ apiKey });
  modelCache = {
    keyHash,
    expiresAt: Date.now() + MODEL_CACHE_MS,
    models,
  };
  return models;
}

function resolveSelection(modelId, models) {
  const route = ROUTES.get(modelId);
  if (!route) {
    throw new Error(`Unsupported Cursor route ${JSON.stringify(modelId)}`);
  }
  const model = models.find((candidate) =>
    modelAliases(candidate).includes(route.alias),
  );
  if (!model) {
    throw new Error(
      `Cursor account does not currently expose ${route.alias}`,
    );
  }
  const fastValue = route.fast ? "true" : "false";
  const variant = (model.variants || []).find((candidate) =>
    (candidate.params || []).some(
      (parameter) =>
        parameter.id === "fast" && parameter.value === fastValue,
    ),
  );
  if (variant) {
    return { id: model.id, params: variant.params };
  }
  const fastParameter = (model.parameters || []).find(
    (parameter) => parameter.id === "fast",
  );
  if (
    fastParameter &&
    (fastParameter.values || []).some((value) => value.value === fastValue)
  ) {
    return {
      id: model.id,
      params: [{ id: "fast", value: fastValue }],
    };
  }
  throw new Error(
    `${route.alias} does not currently expose the requested ${
      route.fast ? "fast" : "slow"
    } variant`,
  );
}

function sanitizedError(error, apiKey) {
  let message =
    error instanceof Error && error.message
      ? error.message
      : String(error || "Unknown Cursor SDK error");
  if (apiKey) {
    message = message.split(apiKey).join("[redacted]");
  }
  return message.slice(0, 2000);
}

function usageFrom(value) {
  const usage = value && typeof value === "object" ? value : {};
  return {
    inputTokens:
      Number.isInteger(usage.inputTokens) && usage.inputTokens >= 0
        ? usage.inputTokens
        : 0,
    outputTokens:
      Number.isInteger(usage.outputTokens) && usage.outputTokens >= 0
        ? usage.outputTokens
        : 0,
  };
}

async function handleTurn(request) {
  const id = typeof request?.id === "string" ? request.id : "unknown";
  let apiKey;
  let agent;
  try {
    if (
      typeof request?.model !== "string" ||
      typeof request?.cwd !== "string" ||
      typeof request?.prompt !== "string" ||
      !request.prompt
    ) {
      throw new Error("Cursor SDK request is missing model, cwd, or prompt");
    }
    if (!path.isAbsolute(request.cwd) || !(await stat(request.cwd)).isDirectory()) {
      throw new Error("Cursor SDK request has an invalid working directory");
    }

    apiKey = await readApiKey();
    const models = await availableModels(apiKey);
    const selection = resolveSelection(request.model, models);
    const threadSuffix =
      typeof request.threadId === "string" && request.threadId
        ? ` ${request.threadId.slice(0, 12)}`
        : "";
    agent = await Agent.create({
      apiKey,
      model: selection,
      mode: "agent",
      name: `Sudhir-Codex${threadSuffix}`,
      local: {
        cwd: request.cwd,
        settingSources: ["all"],
        store,
        enableAgentRetries: true,
      },
    });

    const run = await agent.send(request.prompt, {
      model: selection,
      mode: "agent",
    });
    const assistantParts = [];
    const completedTools = new Set();
    let streamedUsage = null;
    let statusError = null;

    for await (const message of run.stream()) {
      if (message?.type === "assistant") {
        for (const block of message.message?.content || []) {
          if (block?.type === "text" && typeof block.text === "string") {
            assistantParts.push(block.text);
          }
        }
      } else if (
        message?.type === "tool_call" &&
        (message.status === "completed" || message.status === "error")
      ) {
        completedTools.add(message.call_id);
      } else if (message?.type === "usage") {
        streamedUsage = message.usage;
      } else if (message?.type === "status" && message.status === "ERROR") {
        statusError = message.message || "Cursor agent reported an error";
      }
    }

    const terminal = await run.wait();
    if (terminal.status !== "finished") {
      throw new Error(
        terminal.error?.message ||
          statusError ||
          `Cursor agent ended with status ${terminal.status}`,
      );
    }
    const text =
      (typeof terminal.result === "string" && terminal.result.trim()
        ? terminal.result
        : assistantParts.join("\n\n")
      ).trim();
    if (!text) {
      throw new Error("Cursor agent returned no assistant text");
    }
    emit({
      id,
      ok: true,
      text,
      usage: usageFrom(terminal.usage || streamedUsage),
      toolCalls: completedTools.size,
      resolvedModel: terminal.model || selection,
    });
  } catch (error) {
    emit({
      id,
      ok: false,
      error: sanitizedError(error, apiKey),
    });
  } finally {
    if (agent) {
      await agent[Symbol.asyncDispose]?.().catch(() => {});
    }
  }
}

async function main() {
  let buffer = "";
  const inFlight = new Set();

  const dispatch = (request) => {
    const task = handleTurn(request);
    inFlight.add(task);
    task.finally(() => inFlight.delete(task)).catch(() => {});
  };

  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => {
    buffer += chunk;
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        dispatch(JSON.parse(line));
      } catch {
        // A malformed request has no trustworthy request id to acknowledge.
      }
    }
  });
  process.stdin.on("end", async () => {
    if (buffer.trim()) {
      try {
        dispatch(JSON.parse(buffer));
      } catch {
        // Ignore a malformed final line.
      }
    }
    await Promise.allSettled([...inFlight]);
    await rm(workerStateDir, { recursive: true, force: true });
  });
}

if (runningAsMain) {
  await main();
}
