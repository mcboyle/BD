#!/usr/bin/env node
// Example node-runtime plugin (v3.66.468, WS1).
//
// A node plugin is any *.js / *.mjs file in the plugin dir. BD discovers it via
// a manifest probe and runs it per-event over a subprocess bridge:
//
//   node <file> --manifest   -> ONE JSON line describing the plugin
//   node <file> <event>      -> event payload as JSON on stdin,
//                               result as JSON on stdout
//
// This example is a `processor`: it runs after a successful download and
// returns a small annotation. Processors receive the download.done payload
// ({site_id, url, filename, path, file_size, ts}) plus a best-effort `path`.
//
// Node plugins honor the same governance as Python plugins: declaring a gated
// capability (e.g. "page_access") keeps the plugin dormant unless the operator
// enables allow_full_access. This example needs no gated capability.

const arg = process.argv[2];

if (arg === "--manifest") {
  process.stdout.write(JSON.stringify({
    api_version: 2,
    kind: "processor",
    name: "node_download_summary",
    priority: 100
    // capabilities: []   // omit -> loads by default, no full-access needed
  }));
  process.exit(0);
}

// Event invocation: read the JSON payload from stdin, write a JSON result.
let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => { buf += d; });
process.stdin.on("end", () => {
  let payload = {};
  try { payload = buf.trim() ? JSON.parse(buf) : {}; } catch (_e) { payload = {}; }

  const result = {
    summary: `node plugin saw ${payload.filename || "a file"} from ${payload.site_id || "?"}`,
    file_size: payload.file_size || null,
    handled_by: "node_download_summary"
  };

  process.stdout.write(JSON.stringify(result));
});
