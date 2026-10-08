import { createHash, randomUUID } from "node:crypto";

// No upstream counterpart (checked: Python RecordingDocumentScopes, recorder.js,
// Electron Debugger and CDP Runtime): CDP bindings need an explicit reply bridge.
const BRIDGE = `(() => {
  if (window.__qaRecorderHostReply) return;
  const native = window.__qaRecordEmit;
  const calls = new Map(); let next = 0;
  window.__qaRecorderHostReply = (id, value, error) => {
    const call = calls.get(id); if (!call) return;
    calls.delete(id); error ? call.reject(new Error(error)) : call.resolve(value);
  };
  window.__qaRecordEmit = payload => new Promise((resolve, reject) => {
    const id = next++; calls.set(id, {resolve, reject});
    native(JSON.stringify({call_id: id, payload}));
  });
  window.__qaRecorderScopedHost = true;
})();`;

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(
    Object.keys(value).sort().map(key => [key, canonical(value[key])]),
  );
  return value;
}

export class ElectronRecordingScopes {
  constructor(script, onEvent, onError, log) {
    this.script = BRIDGE + "\n" + script;
    this.onEvent = onEvent;
    this.onError = onError;
    this.log = log;
    this.hosts = new Map();
    this.documents = new Map();
    this.events = new Map();
    this.pending = new Set();
    this.windows = new Set();
    this.enabled = false;
    this.nextSeq = 0;
  }

  track(promise) {
    this.pending.add(promise);
    promise.catch(error => this.onError(error)).finally(() => this.pending.delete(promise));
    return promise;
  }

  async settle() {
    while (this.pending.size) await Promise.allSettled([...this.pending]);
  }

  send(host, method, params = {}, sessionId = "") {
    return host.contents.debugger.sendCommand(method, params, sessionId || undefined);
  }

  async evaluate(host, context, expression, awaitPromise = false) {
    const response = await this.send(host, "Runtime.evaluate", {
      expression, contextId: context.id, returnByValue: true, awaitPromise,
    }, context.sessionId);
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.text || "Recording context evaluation failed");
    return response.result?.value;
  }

  async attach(contents) {
    if (this.hosts.has(contents)) return;
    const host = { contents, pageId: randomUUID(), contexts: new Map(), frames: new Map(), sessions: new Set() };
    this.hosts.set(contents, host);
    const debuggerApi = contents.debugger;
    if (!debuggerApi.isAttached()) debuggerApi.attach("1.3");
    host.message = (_event, method, params, sessionId = "") => {
      const key = `${sessionId}:${params.executionContextId ?? params.context?.id}`;
      if (method === "Page.frameAttached") {
        host.frames.set(params.frameId, { parent: params.parentFrameId, frame: { id: params.frameId } });
      } else if (method === "Page.frameNavigated") {
        const existing = host.frames.get(params.frame.id);
        host.frames.set(params.frame.id, { ...existing,
          parent: params.frame.parentId || existing?.parent || null, frame: params.frame });
      } else if (method === "Runtime.executionContextCreated" && params.context.auxData?.isDefault) {
        host.contexts.set(key, { id: params.context.id, sessionId, frameId: params.context.auxData.frameId });
      } else if (method === "Runtime.executionContextDestroyed") {
        this.depart(host, key);
      } else if (method === "Runtime.executionContextsCleared") {
        for (const [name, context] of host.contexts) if (context.sessionId === sessionId) this.depart(host, name);
      } else if (method === "Runtime.bindingCalled" && params.name === "__qaRecordEmit") {
        const context = host.contexts.get(key);
        this.track(this.handleBinding(host, context, params));
      } else if (method === "Target.attachedToTarget") {
        this.track((async () => {
          if (params.targetInfo.type === "iframe") await this.initialize(host, params.sessionId);
          await this.send(host, "Runtime.runIfWaitingForDebugger", {}, params.sessionId);
        })());
      } else if (method === "Target.detachedFromTarget") {
        host.sessions.delete(params.sessionId);
        for (const [name, context] of host.contexts) if (context.sessionId === params.sessionId) this.depart(host, name);
      }
    };
    debuggerApi.on("message", host.message);
    host.popup = window => {
      this.windows.add(window);
      this.track(this.attach(window.webContents));
    };
    contents.on("did-create-window", host.popup);
    contents.once("destroyed", () => {
      for (const key of host.contexts.keys()) this.depart(host, key);
    });
    await this.initialize(host, "");
  }

  depart(host, key) {
    const context = host.contexts.get(key);
    if (context?.documentId) this.documents.get(context.documentId).ended = true;
    host.contexts.delete(key);
  }

  async initialize(host, sessionId) {
    if (host.sessions.has(sessionId)) return;
    host.sessions.add(sessionId);
    await this.send(host, "Page.enable", {}, sessionId);
    await this.send(host, "Runtime.enable", {}, sessionId);
    await this.send(host, "Runtime.addBinding", { name: "__qaRecordEmit" }, sessionId);
    await this.send(host, "Page.addScriptToEvaluateOnNewDocument", { source: this.script }, sessionId);
    await this.send(host, "Target.setAutoAttach", { autoAttach: true, waitForDebuggerOnStart: true, flatten: true }, sessionId);
    for (const context of host.contexts.values()) {
      if (context.sessionId === sessionId) {
        await this.evaluate(host, context, this.script);
        await this.evaluate(host, context, "window.__qaRecorderReady", true);
      }
    }
  }

  async metadata(host, context) {
    // The native frame tree supplies ownership/path; page-supplied IDs are not trusted.
    const visit = (node, parent = null, path = []) => {
      host.frames.set(node.frame.id, { parent, path, frame: node.frame });
      (node.childFrames || []).forEach((child, index) => visit(child, node.frame.id,
        [...path, { cdp_frame_id: child.frame.id, name: child.frame.name || null, src: child.frame.url, index }]));
    };
    const liveSessions = new Set(["", ...[...host.contexts.values()].map(value => value.sessionId)]);
    for (const sessionId of liveSessions) {
      const tree = await this.send(host, "Page.getFrameTree", {}, sessionId);
      const ancestor = host.frames.get(tree.frameTree.frame.id);
      visit(tree.frameTree, ancestor?.parent || null, ancestor?.path || []);
    }
    const frame = host.frames.get(context.frameId);
    if (!frame) throw new Error("Recording native frame absent from owner tree");
    const path = [];
    let current = frame;
    const visited = new Set();
    while (current.parent) {
      if (visited.has(current.frame.id)) throw new Error("Recording native frame ancestry cycle");
      visited.add(current.frame.id);
      path.unshift({ cdp_frame_id: current.frame.id, name: current.frame.name || null, src: current.frame.url || null });
      current = host.frames.get(current.parent);
      if (!current) throw new Error("Recording native parent frame unavailable");
    }
    return { page_id: host.pageId, frame_id: `${host.pageId}:${context.frameId}`,
      parent_frame_id: frame.parent ? `${host.pageId}:${frame.parent}` : null,
      frame_path: path, document_url: frame.frame.url };
  }

  async binding(host, context, params) {
    if (!context) throw new Error("Recording binding has no native default context");
    const envelope = JSON.parse(params.payload);
    const raw = JSON.parse(envelope.payload);
    let reply = true;
    if (raw.__qaRecorderScope === "register") {
      if (!context.documentId) {
        const metadata = await this.metadata(host, context);
        if (host.contexts.get(`${context.sessionId}:${context.id}`) !== context) {
          this.log("registration realm departed before acceptance", context.frameId);
          return;
        }
        context.documentId = randomUUID();
        this.documents.set(context.documentId, { host, context,
          metadata: { document_id: context.documentId, ...metadata }, received: new Set(), ended: false });
      }
      reply = { document_id: context.documentId, enabled: this.enabled };
      this.log("document registered", context.documentId, context.frameId);
    } else {
      const documentId = raw.recording_document_id || raw.document_id;
      const document = this.documents.get(documentId);
      if (!document || document.host !== host || document.context !== context) throw new Error("Recording document native source mismatch");
      if (raw.__qaRecorderScope === "close") {
        document.closeReceipt = raw;
        document.ended = true;
      } else {
        if (!Number.isInteger(raw.seq) || raw.seq < 0 || typeof raw.type !== "string") throw new Error("Recording document event invalid");
        const key = `${documentId}:${raw.seq}`;
        const signature = createHash("sha256").update(JSON.stringify(canonical(raw))).digest("hex");
        const previous = this.events.get(key);
        if (previous && previous !== signature) throw new Error("Recording event identity cannot change");
        if (!previous) {
          this.events.set(key, signature);
          document.received.add(raw.seq);
          const { recording_document_id, ...event } = raw;
          this.onEvent({ ...event, seq: this.nextSeq++,
            page: { ...raw.page, recording_scope: { ...document.metadata, local_seq: raw.seq } },
            target: raw.target && document.metadata.parent_frame_id ? { ...raw.target, in_iframe: true } : raw.target });
        }
      }
    }
    // A departing realm may disappear after its accepted event/boundary. Preserve
    // that evidence; reply failure alone must not turn an accepted event into loss.
    try {
      await this.evaluate(host, context, `window.__qaRecorderHostReply(${JSON.stringify(envelope.call_id)},${JSON.stringify(reply)})`);
    } catch (error) {
      if (host.contexts.get(`${context.sessionId}:${context.id}`) === context) throw error;
      this.log("departed document reply unavailable", context.documentId, error.message);
    }
  }

  async handleBinding(host, context, params) {
    try {
      await this.binding(host, context, params);
    } catch (error) {
      try {
        const envelope = JSON.parse(params.payload);
        await this.evaluate(host, context,
          `window.__qaRecorderHostReply(${JSON.stringify(envelope.call_id)},null,${JSON.stringify(error.message)})`);
      } catch (replyError) {
        this.log("rejected binding reply unavailable", context?.frameId, replyError.message);
      }
      throw error;
    }
  }

  async setEnabled(enabled) {
    this.enabled = enabled;
    await this.settle();
    for (const host of this.hosts.values()) {
      for (const context of host.contexts.values()) {
        await this.evaluate(host, context, `(async()=>{await window.__qaRecorderReady;window.__qaRecorderSetEnabled(${enabled});return true;})()`, true);
      }
    }
  }

  async finish() {
    this.enabled = false;
    await this.settle();
    for (const host of this.hosts.values()) {
      for (const context of host.contexts.values()) {
        const receipt = await this.evaluate(host, context, "window.__qaRecorderFinish()", true);
        if (!receipt || receipt.document_id !== context.documentId) throw new Error("Recording document finish identity mismatch");
        const document = this.documents.get(context.documentId);
        document.receipt = receipt;
        document.ended = false;
      }
    }
    await this.settle();
    const scopes = [...this.documents.values()].map(document => {
      const receipt = document.ended ? document.closeReceipt : document.receipt;
      const next = receipt?.next_seq;
      const confirmed = Number.isInteger(next) && next >= 0 && receipt.emitted_count === next && receipt.pending === 0
        && document.received.size === next && [...document.received].every(seq => seq < next);
      return { ...document.metadata, next_seq: next ?? null, emitted_count: receipt?.emitted_count ?? null,
        pending: receipt?.pending ?? null, received_count: document.received.size, ended: document.ended, confirmed };
    });
    return { protocol: "recording-scopes-v2", next_seq: this.nextSeq, emitted_count: this.nextSeq,
      pending: scopes.reduce((sum, scope) => sum + (scope.pending || 0), 0),
      frames_unconfirmed: scopes.filter(scope => !scope.confirmed).length, scopes };
  }

  detach() {
    for (const host of this.hosts.values()) {
      if (host.contents.isDestroyed()) continue;
      host.contents.removeListener("did-create-window", host.popup);
      host.contents.debugger.removeListener("message", host.message);
      if (host.contents.debugger.isAttached()) host.contents.debugger.detach();
    }
    for (const window of this.windows) if (!window.isDestroyed()) window.close();
  }
}
