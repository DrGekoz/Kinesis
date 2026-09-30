/**
 * The Gesture-Map marketplace API.
 *
 * Public, read-mostly: anyone can list maps, fetch one, or upload one. Nothing here can execute
 * anything - a map is JSON that the desktop app validates against its own vocabulary before use, and
 * the only action types it can contain press keys, click, or minimise a window.
 *
 *   GET  /maps                     list, newest and most downloaded first
 *   GET  /maps/:slug               one map, including the map itself
 *   GET  /maps/:slug/download      increments the counter and returns the map
 *   POST /maps                     publish one
 *   GET  /health                   is it up, and how many maps does it have
 */

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };
const MAX_BODY = 256 * 1024;                 // a gesture map is a few KB; this is generous

// Same vocabulary the desktop app validates against, so the marketplace cannot accept a map the
// client would then refuse to import.
const SCHEMA = "kinesis.gesture-map";
const POSES = new Set([
  "open", "fist", "point", "peace", "three", "four", "pinky", "shaka",
  "pinch_index", "pinch_middle", "pinch_ring", "pinch_pinky",
]);
const ACTIONS = new Set(["keys", "hold_keys", "mouse", "system", "none"]);
const MODIFIERS = new Set(["ctrl", "shift", "alt", "win"]);

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

function slugify(text) {
  return String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "").slice(0, 60) || "map";
}

function uuid() {
  return crypto.randomUUID();
}

/** Everything wrong with a submitted map, in plain words. Empty = acceptable. */
function checkMap(map) {
  const problems = [];
  if (!map || typeof map !== "object") return ["the map is not an object"];
  if (map.schema !== SCHEMA) problems.push(`not a Kinesis Gesture-Map (schema ${map.schema})`);
  if (!Number.isFinite(Number(map.version)) || Number(map.version) < 1) {
    problems.push("missing version");
  }
  if (!Array.isArray(map.bindings)) return problems.concat("no bindings array");
  map.bindings.forEach((binding, i) => {
    const n = i + 1;
    const gesture = binding?.gesture ?? {};
    const action = binding?.action ?? {};
    const twoHand = Boolean(gesture.left || gesture.right);
    if (!twoHand && !POSES.has(String(gesture.pose ?? ""))) {
      problems.push(`binding ${n}: ${gesture.pose} is not a gesture Kinesis can see`);
    }
    for (const side of ["left", "right"]) {
      const value = String(gesture[side] ?? "");
      if (value && !POSES.has(value)) problems.push(`binding ${n}: ${side} hand ${value} unknown`);
    }
    const kind = String(action.type ?? "none");
    if (!ACTIONS.has(kind)) problems.push(`binding ${n}: unknown action type ${kind}`);
    if (kind === "keys" || kind === "hold_keys") {
      if (!Array.isArray(action.keys) || action.keys.length === 0) {
        problems.push(`binding ${n}: a key action needs a key`);
      }
      for (const mod of action.modifiers ?? []) {
        if (!MODIFIERS.has(String(mod))) problems.push(`binding ${n}: unknown modifier ${mod}`);
      }
    }
  });
  return problems;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    const method = request.method.toUpperCase();

    const cors = {
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET, POST, OPTIONS",
      "access-control-allow-headers": "content-type",
    };
    if (method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    const respond = (body, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { ...JSON_HEADERS, ...cors } });

    try {
      if (path === "/health") {
        const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM gesture_maps").first();
        return respond({ ok: true, maps: row?.n ?? 0 });
      }

      if (method === "GET" && path === "/maps") {
        const limit = Math.min(Math.max(Number(url.searchParams.get("limit")) || 100, 1), 200);
        const q = String(url.searchParams.get("q") ?? "").trim();
        const rows = q
          ? await env.DB.prepare(
              `SELECT id, slug, title, description, author_name, github_url, binding_count,
                      downloads, featured, created_at
                 FROM gesture_maps
                WHERE title LIKE ?1 OR description LIKE ?1 OR author_name LIKE ?1
                ORDER BY featured DESC, downloads DESC, created_at DESC LIMIT ?2`).bind(`%${q}%`, limit).all()
          : await env.DB.prepare(
              `SELECT id, slug, title, description, author_name, github_url, binding_count,
                      downloads, featured, created_at
                 FROM gesture_maps
                ORDER BY featured DESC, downloads DESC, created_at DESC LIMIT ?1`).bind(limit).all();
        return respond({ maps: rows.results ?? [] });
      }

      const download = path.match(/^\/maps\/([^/]+)\/download$/);
      if (method === "GET" && download) {
        const slug = decodeURIComponent(download[1]);
        const row = await env.DB.prepare("SELECT * FROM gesture_maps WHERE slug = ?1").bind(slug).first();
        if (!row) return respond({ error: "no such map" }, 404);
        await env.DB.batch([
          env.DB.prepare("UPDATE gesture_maps SET downloads = downloads + 1 WHERE id = ?1").bind(row.id),
          env.DB.prepare("INSERT INTO download_log (map_id, at) VALUES (?1, ?2)")
            .bind(row.id, new Date().toISOString()),
        ]);
        return respond({ map: JSON.parse(row.map_json), downloads: row.downloads + 1 });
      }

      const one = path.match(/^\/maps\/([^/]+)$/);
      if (method === "GET" && one) {
        const slug = decodeURIComponent(one[1]);
        const row = await env.DB.prepare("SELECT * FROM gesture_maps WHERE slug = ?1").bind(slug).first();
        if (!row) return respond({ error: "no such map" }, 404);
        return respond({ map: JSON.parse(row.map_json) });
      }

      if (method === "POST" && path === "/maps") {
        const raw = await request.text();
        if (raw.length > MAX_BODY) return respond({ error: "that file is too large" }, 413);
        let body;
        try {
          body = JSON.parse(raw);
        } catch {
          return respond({ error: "the request was not valid JSON" }, 400);
        }
        const title = String(body.title ?? "").trim();
        const description = String(body.description ?? "").trim();
        const author = String(body.author_name ?? "").trim();
        const github = String(body.github_url ?? "").trim();
        const map = body.map;
        const problems = [];
        if (!title) problems.push("a title is required");
        if (title.length > 80) problems.push("keep the title under 80 characters");
        if (!author) problems.push("your name is required");
        if (!github) problems.push("your GitHub link is required");
        else if (!/^https?:\/\/(www\.)?github\.com\/[^/\s]+\/?/i.test(github)) {
          problems.push("that does not look like a GitHub link");
        }
        if (!map) problems.push("the gesture map file is required");
        if (problems.length) return respond({ error: problems.join("; "), problems }, 400);
        const mapProblems = checkMap(map);
        if (mapProblems.length) return respond({ error: "that file is not a Gesture-Map", problems: mapProblems }, 400);

        const bindings = map.bindings.length;
        let slug = slugify(title);
        const clash = await env.DB.prepare("SELECT slug FROM gesture_maps WHERE slug = ?1").bind(slug).first();
        if (clash) slug = `${slug}-${Math.random().toString(36).slice(2, 6)}`;
        const id = uuid();
        const created = new Date().toISOString();
        await env.DB.prepare(
          `INSERT INTO gesture_maps
             (id, slug, title, description, author_name, github_url, map_json, binding_count,
              downloads, featured, created_at)
           VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 0, 0, ?9)`)
          .bind(id, slug, title, description, author, github, JSON.stringify(map), bindings, created).run();
        return respond({ ok: true, slug, id, bindings }, 201);
      }

      return respond({ error: "not found" }, 404);
    } catch (err) {
      return respond({ error: "server error", detail: String(err?.message ?? err) }, 500);
    }
  },
};
