/**
 * A local stand-in for the Cloudflare Worker, so the marketplace can be exercised without deploying.
 *
 * It runs the REAL cloudflare/worker.js - same validation, same SQL, same responses - against an
 * in-memory D1 stub. A tiny HTTP server on localhost wraps it so the Python client talks to it over
 * the same urllib path it uses in production.
 *
 *   node tools/marketplace_stub.mjs [port]
 *
 * The stub implements only what worker.js actually calls: DB.prepare(...).bind(...).first()/.all()
 * /.run(), and DB.batch([...]). The SQL is matched loosely - the point under test is the Worker's
 * validation and response shape, not SQLite.
 */
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const workerSource = readFileSync(join(here, "..", "cloudflare", "worker.js"), "utf8");
const worker = await import(
  "data:text/javascript;base64," + Buffer.from(workerSource).toString("base64")
);

// ---------------------------------------------------------------- in-memory D1
const tables = { gesture_maps: [], download_log: [] };

function matches(row, sql, params) {
  const where = /where\s+(\w+)\s*=\s*\?1/i.exec(sql);
  const like = /(title|description|author_name)\s+LIKE\s*\?1/i.exec(sql);
  const limit = /limit\s*\?(\d)/i.exec(sql);
  if (like) {
    const needle = String(params[0] ?? "").replace(/%/g, "").toLowerCase();
    const fields = sql.slice(sql.toLowerCase().indexOf("where"), sql.toLowerCase().indexOf("order by"));
    return fields.split(/\s+OR\s+/i).some((clause) => {
      const col = /\s*(\w+)\s+LIKE/i.exec(clause)?.[1];
      return col ? String(row[col] ?? "").toLowerCase().includes(needle) : false;
    });
  }
  if (where) {
    const value = params[0];
    return String(row[where[1]]) === String(value);
  }
  return true;
}

const db = {
  prepare(sql) {
    let bound = [];
    const stmt = {
      bind(...values) { bound = values; return stmt; },
      async first() {
        const hit = tables.gesture_maps.filter((r) => matches(r, sql, bound));
        return hit.length ? { ...hit[0] } : null;
      },
      async all() {
        let rows = tables.gesture_maps.filter((r) => matches(r, sql, bound));
        const limit = /limit\s*\?(\d)/i.exec(sql);
        if (limit) rows = rows.slice(0, Number(bound[Number(limit[1]) - 1]) || rows.length);
        rows = [...rows].sort((a, b) => {
          if (b.featured !== a.featured) return b.featured - a.featured;
          if (b.downloads !== a.downloads) return b.downloads - a.downloads;
          return String(b.created_at).localeCompare(String(a.created_at));
        });
        return { results: rows.map((r) => ({ ...r })) };
      },
      async run() {
        const insert = /insert\s+into\s+gesture_maps/i.test(sql);
        if (insert) {
          const [id, slug, title, description, author_name, github_url, map_json,
                 binding_count, , created_at] = bound;
          tables.gesture_maps.push({
            id, slug, title, description, author_name, github_url, map_json,
            binding_count, downloads: 0, featured: 0, created_at,
          });
        }
        const update = /update\s+gesture_maps\s+set\s+downloads/i.test(sql);
        if (update) {
          const row = tables.gesture_maps.find((r) => r.id === bound[0]);
          if (row) row.downloads += 1;
        }
        const log = /insert\s+into\s+download_log/i.test(sql);
        if (log) tables.download_log.push({ map_id: bound[0], at: bound[1] });
        return { success: true };
      },
    };
    return stmt;
  },
  async batch(statements) {
    const out = [];
    for (const s of statements) out.push(await s.run());
    return out;
  },
};

// ---------------------------------------------------------------- http wrapper
const port = Number(process.argv[2] || 8787);
const server = createServer(async (req, res) => {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = chunks.length ? Buffer.concat(chunks) : undefined;
  const request = new Request(`http://127.0.0.1:${port}${req.url}`, {
    method: req.method,
    headers: Object.fromEntries(
      Object.entries(req.headers).map(([k, v]) => [k, Array.isArray(v) ? v.join(",") : v])
    ),
    body: req.method === "GET" || req.method === "HEAD" ? undefined : body,
  });
  try {
    const response = await worker.default.fetch(request, { DB: db });
    const text = await response.text();
    res.writeHead(response.status, Object.fromEntries(response.headers));
    res.end(text);
  } catch (err) {
    res.writeHead(500, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: String(err?.message ?? err) }));
  }
});

server.listen(port, "127.0.0.1", () => {
  console.log(`[stub] marketplace worker listening on http://127.0.0.1:${port}`);
  console.log(`[stub] running the real cloudflare/worker.js against an in-memory D1`);
});
