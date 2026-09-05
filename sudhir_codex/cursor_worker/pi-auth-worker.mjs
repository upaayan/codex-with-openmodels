#!/usr/bin/env node

import path from "node:path";
import { pathToFileURL } from "node:url";

import { createAgentSessionServices } from "@earendil-works/pi-coding-agent";

const runningAsMain = process.argv[1]
  ? import.meta.url === pathToFileURL(process.argv[1]).href
  : false;
const protocolWrite = process.stdout.write.bind(process.stdout);
const GOOGLE_SIGNATURE_PREFIX = "sudhir-google-signature-v1.";

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

function textContent(value) {
  if (typeof value === "string") return value;
  if (!Array.isArray(value)) return "";
  return value
    .filter((part) => part?.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("");
}

function imageContent(value) {
  if (!Array.isArray(value)) return [];
  return value.flatMap((part) => {
    if (part?.type !== "image_url") return [];
    const url = part.image_url?.url;
    if (typeof url !== "string") return [];
    const match = /^data:([^;,]+);base64,([A-Za-z0-9+/=]+)$/.exec(url);
    if (!match) throw new Error("Pi native Google routes require data-URL images");
    return [{ type: "image", mimeType: match[1], data: match[2] }];
  });
}

function decodeSignatureEnvelope(value) {
  if (typeof value !== "string" || !value.startsWith(GOOGLE_SIGNATURE_PREFIX)) {
    return null;
  }
  try {
    const encoded = value.slice(GOOGLE_SIGNATURE_PREFIX.length);
    const envelope = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
    if (envelope?.v !== 1 || !Array.isArray(envelope.parts)) return null;
    return envelope;
  } catch {
    return null;
  }
}

function signatureEnvelopeMatchesModel(envelope, model) {
  return (
    envelope?.v === 1 &&
    envelope.api === model.api &&
    envelope.provider === model.provider &&
    envelope.model === model.id
  );
}

function encodeSignatureEnvelope(message) {
  const parts = message.content.map((block) => {
    if (block.type === "text") {
      return {
        type: "text",
        length: block.text.length,
        ...(block.textSignature ? { signature: block.textSignature } : {}),
      };
    }
    if (block.type === "thinking") {
      return {
        type: "thinking",
        length: block.thinking.length,
        ...(block.thinkingSignature
          ? { signature: block.thinkingSignature }
          : {}),
        ...(block.redacted ? { redacted: true } : {}),
      };
    }
    return {
      type: "toolCall",
      id: block.id,
      ...(block.thoughtSignature ? { signature: block.thoughtSignature } : {}),
    };
  });
  const envelope = {
    v: 1,
    api: message.api,
    provider: message.provider,
    model: message.model,
    parts,
  };
  return `${GOOGLE_SIGNATURE_PREFIX}${Buffer.from(
    JSON.stringify(envelope),
  ).toString("base64url")}`;
}

function assistantContent(message, currentModel) {
  const decodedEnvelope = decodeSignatureEnvelope(message.reasoning_signature);
  const envelope = signatureEnvelopeMatchesModel(decodedEnvelope, currentModel)
    ? decodedEnvelope
    : null;
  const text = textContent(message.content);
  const thinking =
    typeof message.reasoning_content === "string"
      ? message.reasoning_content
      : "";
  const calls = Array.isArray(message.tool_calls) ? message.tool_calls : [];
  const callsById = new Map(calls.map((call) => [call?.id, call]));
  let textOffset = 0;
  let thinkingOffset = 0;
  const content = [];

  for (const part of envelope?.parts ?? []) {
    if (part?.type === "text" && Number.isInteger(part.length)) {
      const value = text.slice(textOffset, textOffset + part.length);
      textOffset += part.length;
      if (value) {
        content.push({
          type: "text",
          text: value,
          ...(typeof part.signature === "string"
            ? { textSignature: part.signature }
            : {}),
        });
      }
    } else if (part?.type === "thinking" && Number.isInteger(part.length)) {
      const value = thinking.slice(thinkingOffset, thinkingOffset + part.length);
      thinkingOffset += part.length;
      if (value) {
        content.push({
          type: "thinking",
          thinking: value,
          ...(typeof part.signature === "string"
            ? { thinkingSignature: part.signature }
            : {}),
          ...(part.redacted === true ? { redacted: true } : {}),
        });
      }
    } else if (part?.type === "toolCall") {
      const call = callsById.get(part.id);
      if (call) {
        content.push(chatToolCall(call, part.signature));
        callsById.delete(part.id);
      }
    }
  }

  if (thinkingOffset < thinking.length) {
    content.push({ type: "thinking", thinking: thinking.slice(thinkingOffset) });
  }
  if (textOffset < text.length) {
    content.push({ type: "text", text: text.slice(textOffset) });
  }
  for (const call of callsById.values()) content.push(chatToolCall(call));

  return {
    role: "assistant",
    content,
    api: envelope?.api ?? "openai-responses",
    provider: envelope?.provider ?? "sudhir-history",
    model: envelope?.model ?? currentModel.id,
    usage: {
      input: 0,
      output: 0,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    },
    stopReason: calls.length ? "toolUse" : "stop",
    timestamp: Date.now(),
  };
}

function chatToolCall(call, signature) {
  const name = call?.function?.name;
  const rawArguments = call?.function?.arguments;
  if (typeof call?.id !== "string" || typeof name !== "string") {
    throw new Error("invalid assistant tool call");
  }
  let args;
  try {
    args = typeof rawArguments === "string" ? JSON.parse(rawArguments) : rawArguments;
  } catch {
    throw new Error("assistant tool call arguments are not valid JSON");
  }
  if (!args || typeof args !== "object" || Array.isArray(args)) args = {};
  return {
    type: "toolCall",
    id: call.id,
    name,
    arguments: args,
    ...(typeof signature === "string" ? { thoughtSignature: signature } : {}),
  };
}

export function chatRequestToPiContext(chatRequest, model) {
  if (!chatRequest || !Array.isArray(chatRequest.messages)) {
    throw new Error("invalid inference request");
  }
  const system = [];
  const messages = [];
  const toolNames = new Map();
  for (const message of chatRequest.messages) {
    if (message?.role === "system") {
      const value = textContent(message.content);
      if (value) system.push(value);
    } else if (message?.role === "user") {
      const text = textContent(message.content);
      const images = imageContent(message.content);
      messages.push({
        role: "user",
        content: images.length ? [{ type: "text", text }, ...images] : text,
        timestamp: Date.now(),
      });
    } else if (message?.role === "assistant") {
      const assistant = assistantContent(message, model);
      for (const block of assistant.content) {
        if (block.type === "toolCall") toolNames.set(block.id, block.name);
      }
      messages.push(assistant);
    } else if (message?.role === "tool") {
      if (typeof message.tool_call_id !== "string") {
        throw new Error("tool result has no call ID");
      }
      messages.push({
        role: "toolResult",
        toolCallId: message.tool_call_id,
        toolName: toolNames.get(message.tool_call_id) ?? "tool",
        content: [{ type: "text", text: textContent(message.content) }],
        isError: false,
        timestamp: Date.now(),
      });
    }
  }
  const tools = Array.isArray(chatRequest.tools)
    ? chatRequest.tools.map((tool) => ({
        name: tool?.function?.name,
        description: tool?.function?.description ?? "",
        parameters: tool?.function?.parameters ?? {
          type: "object",
          properties: {},
        },
      }))
    : undefined;
  return {
    ...(system.length ? { systemPrompt: system.join("\n\n") } : {}),
    messages,
    ...(tools?.length ? { tools } : {}),
  };
}

export function assistantToChatResponse(message) {
  if (message?.stopReason === "error" || message?.stopReason === "aborted") {
    throw new Error(message.errorMessage || "Pi native inference failed");
  }
  const text = message.content
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("");
  const thinking = message.content
    .filter((block) => block.type === "thinking")
    .map((block) => block.thinking)
    .join("");
  const toolCalls = message.content
    .filter((block) => block.type === "toolCall")
    .map((block) => ({
      id: block.id,
      type: "function",
      function: {
        name: block.name,
        arguments: JSON.stringify(block.arguments ?? {}),
      },
    }));
  return {
    choices: [
      {
        message: {
          role: "assistant",
          content: text || null,
          ...(thinking ? { reasoning_content: thinking } : {}),
          reasoning_signature: encodeSignatureEnvelope(message),
          ...(toolCalls.length ? { tool_calls: toolCalls } : {}),
        },
      },
    ],
    usage: {
      prompt_tokens: message.usage?.input ?? 0,
      completion_tokens: message.usage?.output ?? 0,
      total_tokens: message.usage?.totalTokens ?? 0,
      ...(typeof message.usage?.reasoning === "number"
        ? { completion_tokens_details: { reasoning_tokens: message.usage.reasoning } }
        : {}),
    },
  };
}

async function runInference(request) {
  const identity = requestIdentity(request);
  if (!request.chatRequest || typeof request.chatRequest !== "object") {
    throw new Error("invalid inference request");
  }
  const agentDir = process.env.SUDHIR_PI_AGENT_DIR;
  if (!agentDir || !path.isAbsolute(agentDir)) {
    throw new Error("agent directory is unavailable");
  }
  const services = await createAgentSessionServices({ cwd: agentDir, agentDir });
  if (services.diagnostics.some((diagnostic) => diagnostic.type === "error")) {
    throw new Error("Pi provider extensions did not load");
  }
  const model = services.modelRuntime.getModel(identity.provider, identity.model);
  if (!model || model.api !== identity.api) {
    throw new Error("Pi provider route does not match the gateway route");
  }
  const context = chatRequestToPiContext(request.chatRequest, model);
  const reasoning =
    typeof request.reasoning === "string" && request.reasoning !== "none"
      ? request.reasoning
      : undefined;
  const { message, upstream } = await completeWithUpstreamCapture(() =>
    services.modelRuntime.completeSimple(model, context, {
      reasoning,
      timeoutMs: 600_000,
    }),
  );
  if (message.stopReason === "error" || message.stopReason === "aborted") {
    return {
      id: identity.id,
      ok: false,
      code: "pi_inference_failed",
      ...(upstream ? { upstream } : {}),
    };
  }
  return {
    id: identity.id,
    ok: true,
    provider: model.provider,
    model: model.id,
    api: model.api,
    destination:
      model.api === "google-vertex"
        ? "aiplatform.googleapis.com"
        : new URL(model.baseUrl).hostname,
    response: assistantToChatResponse(message),
  };
}

export async function completeWithUpstreamCapture(complete) {
  const originalFetch = globalThis.fetch;
  let upstream = null;
  if (typeof originalFetch === "function") {
    globalThis.fetch = async (input, init) => {
      const response = await originalFetch(input, {
        ...(init ?? {}),
        redirect: "manual",
      });
      if (!response.ok) {
        const headers = {};
        for (const name of ["content-type", "request-id", "x-request-id"]) {
          const value = response.headers.get(name);
          if (value) headers[name] = value;
        }
        upstream = {
          status: response.status,
          headers,
          bodyBase64: Buffer.from(await response.clone().arrayBuffer()).toString(
            "base64",
          ),
        };
      }
      return response;
    };
  }
  try {
    return { message: await complete(), upstream };
  } finally {
    if (typeof originalFetch === "function") globalThis.fetch = originalFetch;
  }
}

async function dispatch(request) {
  const id = typeof request?.id === "string" ? request.id : "unknown";
  try {
    emit(
      request?.op === "infer"
        ? await runInference(request)
        : await resolveAuth(request),
    );
  } catch {
    emit({
      id,
      ok: false,
      code:
        request?.op === "infer"
          ? "pi_inference_failed"
          : "pi_auth_resolution_failed",
    });
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
