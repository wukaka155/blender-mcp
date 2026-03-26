// node 18+
// node test_one_tool.mjs
const base = "http://127.0.0.1:1134";
const toolReq = {
  jsonrpc: "2.0",
  id: 2,
  method: "tools/call",
  params: {
    name: "generator_model_build",
    arguments: { width: 20, length: 20, floor: 5, model_name: "MiniTower" },
  },
};

const sse = await fetch(`${base}/sse`, {
  headers: { Accept: "text/event-stream" },
});
const reader = sse.body.getReader();
const dec = new TextDecoder();

let buf = "",
  sid = null;
while (!sid) {
  const { value, done } = await reader.read();
  if (done) throw new Error("sse closed");
  buf += dec.decode(value, { stream: true });
  const m = buf.match(/session_id=([a-zA-Z0-9]+)/);
  if (m) sid = m[1];
}
const msg = `${base}/messages/?session_id=${sid}`;

const post = (p) =>
  fetch(msg, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(p),
  });

await post({
  jsonrpc: "2.0",
  id: 1,
  method: "initialize",
  params: {
    protocolVersion: "2025-03-26",
    capabilities: {},
    clientInfo: { name: "mini", version: "1" },
  },
});
await post({ jsonrpc: "2.0", method: "notifications/initialized", params: {} });
await post(toolReq);

let lines = [];
for (;;) {
  const { value, done } = await reader.read();
  if (done) throw new Error("no tool result");
  const chunk = dec.decode(value, { stream: true });
  for (const raw of chunk.split(/\r?\n/)) {
    const line = raw.trimEnd();
    if (line.startsWith("data:")) lines.push(line.slice(5).trimStart());
    if (line === "" && lines.length) {
      const txt = lines.join("\n");
      lines = [];
      try {
        const obj = JSON.parse(txt);
        if (obj.id === 2) {
          console.log(JSON.stringify(obj, null, 2));
          process.exit(0);
        }
      } catch {}
    }
  }
}
