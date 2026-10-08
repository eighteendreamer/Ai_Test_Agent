// @vitest-environment node
// Replay native identities from the actual failed iframe removal. Transport is
// controlled here; actual Electron/API/storage acceptance is tested separately.
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import { ElectronRecordingScopes } from "../../electron/recording-scopes.mjs";

const evidence = resolve(process.cwd(), "../docs/upgrade_2026-10-03/p0-recording-lifecycle-electron-diagnostic/frame-remove");
const contexts = readFileSync(resolve(evidence, "native-contexts.jsonl"), "utf8").trim().split("\n").map(JSON.parse);
const bindings = readFileSync(resolve(evidence, "native-binding.jsonl"), "utf8").trim().split("\n").map(JSON.parse);
const created = contexts.find(row => row.method === "Runtime.executionContextCreated" && row.session_id && row.params.context.auxData.isDefault);
const detached = contexts.find(row => row.method === "Target.detachedFromTarget" && row.params.sessionId === created.session_id);
const boundary = bindings.find(row => row.session_id === created.session_id && JSON.parse(row.envelope.payload).__qaRecorderScope === "close");

async function replay() {
  const contents = new EventEmitter();
  contents.isDestroyed = () => false;
  contents.debugger = new EventEmitter();
  contents.debugger.isAttached = () => true;
  contents.debugger.detach = () => {};
  let lateReject;
  contents.debugger.sendCommand = async (method, params) => {
    if (params?.expression === "hold-current") return await new Promise((_resolve, reject) => { lateReject = reject; });
    if (params?.expression?.startsWith("window.__qaRecorderHostReply")) throw new Error("controlled original reply unavailable");
    return { result: { value: true } };
  };
  const errors = [], logs = [];
  const scopes = new ElectronRecordingScopes("", () => {}, error => errors.push(error), (...details) => logs.push(details));
  await scopes.attach(contents);
  contents.debugger.emit("message", {}, created.method, created.params, created.session_id);
  const host = scopes.hosts.get(contents);
  const key = `${created.session_id}:${created.params.context.id}`;
  const context = host.contexts.get(key);
  return { scopes, contents, host, context, key, errors, logs, lateReject: () => lateReject };
}

it.each(["native-detach", "owner-teardown"])("releases pending native evaluation on %s without waiting for the original command", async kind => {
  const fixture = await replay();
  const pending = fixture.scopes.evaluate(fixture.host, fixture.context, "hold-current");
  const rejected = expect(pending).rejects.toThrow("native context departed");
  expect(fixture.context.commands.size).toBe(1);
  if (kind === "native-detach") fixture.contents.debugger.emit("message", {}, detached.method, detached.params, detached.session_id);
  else fixture.scopes.detach();
  await rejected;
  expect(fixture.context.commands.size).toBe(0);
  expect(fixture.host.contexts.has(fixture.key)).toBe(false);
  fixture.lateReject()(new Error("late original target closed"));
  await Promise.resolve();
  expect(fixture.errors).toEqual([]);
});

it("keeps an accepted actual boundary when its reply fails before native destruction is notified", async () => {
  const fixture = await replay();
  const raw = JSON.parse(boundary.envelope.payload);
  fixture.context.documentId = raw.document_id;
  fixture.scopes.documents.set(raw.document_id, {host: fixture.host, context: fixture.context, received: new Set()});
  await fixture.scopes.handleBinding(fixture.host, fixture.context, {payload: JSON.stringify(boundary.envelope)});
  expect(fixture.scopes.documents.get(raw.document_id).closeReceipt).toEqual(raw);
  expect(fixture.host.contexts.has(fixture.key)).toBe(true);
  expect(fixture.errors).toEqual([]);
  expect(fixture.logs.some(row => row[0] === "accepted document reply unavailable")).toBe(true);
});

it("still rejects an actual boundary presented from a foreign document", async () => {
  const fixture = await replay();
  await expect(fixture.scopes.handleBinding(fixture.host, fixture.context, {payload: JSON.stringify(boundary.envelope)}))
    .rejects.toThrow("native source mismatch");
  expect(fixture.scopes.documents.size).toBe(0);
});

it.each([false, true])("reactivation requires the original native realm; foreign=%s", async foreign => {
  const fixture = await replay();
  const raw = JSON.parse(boundary.envelope.payload);
  const originalContext = fixture.context;
  fixture.scopes.documents.set(raw.document_id, {host: fixture.host, context: originalContext, received: new Set(), ended: true});
  const restored = {...originalContext, uniqueId: foreign ? 'different actual realm' : originalContext.uniqueId};
  const payload = JSON.stringify({call_id: 3, payload: JSON.stringify({__qaRecorderScope: 'activate', document_id: raw.document_id})});
  if (foreign) {
    await expect(fixture.scopes.handleBinding(fixture.host, restored, {payload})).rejects.toThrow('native realm mismatch');
    expect(fixture.scopes.documents.get(raw.document_id).ended).toBe(true);
  } else {
    await fixture.scopes.handleBinding(fixture.host, restored, {payload});
    expect(fixture.scopes.documents.get(raw.document_id).context).toBe(restored);
    expect(fixture.scopes.documents.get(raw.document_id).ended).toBe(false);
    expect(restored.documentId).toBe(raw.document_id);
  }
});
