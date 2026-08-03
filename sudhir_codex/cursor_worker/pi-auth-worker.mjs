#!/usr/bin/env node

import path from "node:path";
import { pathToFileURL } from "node:url";

import { createAgentSessionServices } from "@earendil-works/pi-coding-agent";

const runningAsMain = process.argv[1]
  ? import.meta.url === pathToFileURL(process.argv[1]).href
  : false;
const protocolWrite = process.stdout.write.bind(process.stdout);

if (runningAsMain) {
  process.stdout.write = (chunk, ...args) =>
    process.stderr.write(chunk, ...args);
}

process.umask(0o077);

function emit(message) {
  protocolWrite(`${JSON.stringify(message)}\n`);
}

function requestIdentity(request) {
  const id = typeof request?.id === "string" ? request.id : "unknown";
  if (
    typeof request?.provider !== "string" ||
    !request.provider ||
    typeof request?.model !== "string" ||
    !request.model ||
    typeof request?.api !== "string" ||
    !request.api
  ) {
    throw new Error("invalid request");
  }
  return {
    id,
    provider: request.provider,
    model: request.model,
    api: request.api,
  };
}

function stringHeaders(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return Object.fromEntries(
    Object.entries(value).filter(
      ([name, header]) =>
        typeof name === "string" &&
        name.length > 0 &&
        typeof header === "string",
    ),
  );
}

async function resolveAuth(request) {
  const identity = requestIdentity(request);
  const agentDir = process.env.SUDHIR_PI_AGENT_DIR;
  if (!agentDir || !path.isAbsolute(agentDir)) {
    throw new Error("agent directory is unavailable");
  }

  const services = await createAgentSessionServices({
    cwd: agentDir,
    agentDir,
  });
  if (services.diagnostics.some((diagnostic) => diagnostic.type === "error")) {
    throw new Error("Pi provider extensions did not load");
  }
  const model = services.modelRuntime.getModel(
    identity.provider,
    identity.model,
  );
  if (!model || model.api !== identity.api) {
    throw new Error("Pi provider route does not match the gateway route");
  }
  const resolution = await services.modelRuntime.getAuth(model);
  if (!resolution?.auth) {
    throw new Error("Pi provider authentication is unavailable");
  }

  return {
    id: identity.id,
    ok: true,
    provider: model.provider,
    model: model.id,
    api: model.api,
    apiKey:
      typeof resolution.auth.apiKey === "string" && resolution.auth.apiKey
        ? resolution.auth.apiKey
        : null,
    headers: stringHeaders(resolution.auth.headers),
    baseUrl:
      typeof resolution.auth.baseUrl === "string" && resolution.auth.baseUrl
        ? resolution.auth.baseUrl
        : null,
  };
}

async function dispatch(request) {
  const id = typeof request?.id === "string" ? request.id : "unknown";
  try {
    emit(await resolveAuth(request));
  } catch {
    emit({ id, ok: false, code: "pi_auth_resolution_failed" });
  }
}

async function main() {
  let buffer = "";
  const inFlight = new Set();
  const enqueue = (request) => {
    const task = dispatch(request);
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
        enqueue(JSON.parse(line));
      } catch {
        emit({ id: "unknown", ok: false, code: "pi_auth_request_invalid" });
      }
    }
  });
  process.stdin.on("end", async () => {
    if (buffer.trim()) {
      try {
        enqueue(JSON.parse(buffer));
      } catch {
        emit({ id: "unknown", ok: false, code: "pi_auth_request_invalid" });
      }
    }
    await Promise.allSettled([...inFlight]);
  });
}

if (runningAsMain) {
  await main();
}
