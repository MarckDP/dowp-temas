// Recibe un tema del formulario, lo pasa tal cual al borrador "buzon" y avisa al Action.
// Nunca abre el archivo: quien valida es el Action. Secretos: GITHUB_TOKEN y TURNSTILE_SECRET.
// GET /estado/CODIGO dice en qué va un tema.
const REPO = "MarckDP/dowp-temas";
// El tema (20 MB) y hasta 3 capturas (8 MB cada una); el Action comprueba cada parte.
const MAX_BYTES = 45 * 1024 * 1024;
const ORIGINS = ["https://marckdp.github.io"];
// Por dirección: un freno simple. Vive en la memoria del Worker, así que puede reiniciarse.
const IP_LIMIT = 5;
const IP_WINDOW_MS = 60 * 60 * 1000;
const uploadsByIp = new Map();
// El freno firme: con tantos temas por revisar o en proceso, no se aceptan más.
const MAX_OPEN_REVIEWS = 25;
const MAX_PENDING_FILES = 10;

function reply(request, body, status = 200) {
  const origin = request.headers.get("Origin");
  const headers = { "Content-Type": "application/json" };
  if (ORIGINS.includes(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
    headers["Vary"] = "Origin";
  }
  return new Response(JSON.stringify(body), { status, headers });
}

function recentUploads(ip, now) {
  const recent = (uploadsByIp.get(ip) || []).filter((time) => now - time < IP_WINDOW_MS);
  uploadsByIp.set(ip, recent);
  return recent;
}

async function reviewQueueFull(inbox, github) {
  if (inbox.assets.filter((a) => a.name.startsWith("tema-")).length >= MAX_PENDING_FILES) return true;
  const open = await fetch(`https://api.github.com/repos/${REPO}/pulls?state=open&per_page=${MAX_OPEN_REVIEWS}`, { headers: github });
  return !open.ok || (await open.json()).length >= MAX_OPEN_REVIEWS;
}

async function humanCheck(token, ip, secret) {
  const form = new FormData();
  form.append("secret", secret);
  form.append("response", token);
  if (ip) form.append("remoteip", ip);
  const answer = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", { method: "POST", body: form });
  return (await answer.json()).success === true;
}

function githubHeaders(env) {
  return {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: "application/vnd.github+json",
    "User-Agent": "dowp-temas-worker",
  };
}

async function findInbox(github) {
  const list = await fetch(`https://api.github.com/repos/${REPO}/releases`, { headers: github });
  if (!list.ok) return null;
  return (await list.json()).find((r) => r.tag_name === "buzon" && r.draft) || null;
}

async function readAsset(asset, github) {
  // GitHub redirige a un almacén que no acepta el token: la segunda petición va sin él.
  const first = await fetch(asset.url, { headers: { ...github, Accept: "application/octet-stream" }, redirect: "manual" });
  const final = first.status >= 300 && first.status < 400 ? await fetch(first.headers.get("Location")) : first;
  return final.ok ? final.json() : null;
}

async function themeState(request, env, code) {
  const github = githubHeaders(env);
  const inbox = await findInbox(github);
  if (!inbox) return reply(request, { error: "buzon" }, 502);
  const state = inbox.assets.find((a) => a.name === `estado-${code}.json`);
  if (!state) {
    const pending = inbox.assets.some((a) => a.name === `tema-${code}.bin`);
    return reply(request, { estado: pending ? "procesando" : "desconocido" });
  }
  const data = await readAsset(state, github);
  if (!data) return reply(request, { error: "buzon" }, 502);
  if (data.estado !== "revision") return reply(request, { estado: data.estado, motivo: data.motivo || "" });
  const owner = REPO.split("/")[0];
  const pulls = await fetch(`https://api.github.com/repos/${REPO}/pulls?state=all&head=${owner}:tema/${data.tema}`, { headers: github });
  if (!pulls.ok) return reply(request, { estado: "revision" });
  const pull = (await pulls.json())[0];
  if (pull && pull.merged_at) return reply(request, { estado: "publicado" });
  if (pull && pull.state === "closed") return reply(request, { estado: "rechazado" });
  return reply(request, { estado: "revision" });
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin");
    if (request.method === "OPTIONS") {
      if (!ORIGINS.includes(origin)) return new Response(null, { status: 403 });
      return new Response(null, {
        status: 204,
        headers: {
          "Access-Control-Allow-Origin": origin,
          "Access-Control-Allow-Methods": "POST",
          "Access-Control-Allow-Headers": "Content-Type, X-Turnstile, X-Nick",
          "Access-Control-Max-Age": "86400",
          "Vary": "Origin",
        },
      });
    }
    if (request.method === "GET") {
      const match = new URL(request.url).pathname.match(/^\/estado\/([A-F0-9]{8})$/);
      return match ? themeState(request, env, match[1]) : reply(request, { error: "ruta" }, 404);
    }
    if (request.method !== "POST") return reply(request, { error: "metodo" }, 405);
    if (!ORIGINS.includes(origin)) return reply(request, { error: "origen" }, 403);

    const size = Number(request.headers.get("Content-Length") || 0);
    if (!size) return reply(request, { error: "vacio" }, 400);
    if (size > MAX_BYTES) return reply(request, { error: "grande" }, 413);

    const ip = request.headers.get("CF-Connecting-IP") || "";
    const now = Date.now();
    if (recentUploads(ip, now).length >= IP_LIMIT) return reply(request, { error: "limite" }, 429);

    const token = request.headers.get("X-Turnstile");
    if (!token || !(await humanCheck(token, ip, env.TURNSTILE_SECRET))) {
      return reply(request, { error: "antibots" }, 403);
    }

    const github = githubHeaders(env);
    const inbox = await findInbox(github);
    if (!inbox) return reply(request, { error: "buzon" }, 502);
    if (await reviewQueueFull(inbox, github)) return reply(request, { error: "ocupado" }, 503);

    const code = crypto.randomUUID().replaceAll("-", "").slice(0, 8).toUpperCase();
    const name = `tema-${code}.bin`;
    let nick = "";
    try {
      nick = decodeURIComponent(request.headers.get("X-Nick") || "").slice(0, 30);
    } catch {}
    const upload = await fetch(`https://uploads.github.com/repos/${REPO}/releases/${inbox.id}/assets?name=${name}`, {
      method: "POST",
      headers: { ...github, "Content-Type": "application/octet-stream", "Content-Length": String(size) },
      body: request.body,
    });
    if (!upload.ok) return reply(request, { error: "subida" }, 502);
    recentUploads(ip, now).push(now);

    const notify = await fetch(`https://api.github.com/repos/${REPO}/dispatches`, {
      method: "POST",
      headers: { ...github, "Content-Type": "application/json" },
      body: JSON.stringify({ event_type: "tema-subido", client_payload: { archivo: name, nick } }),
    });
    if (!notify.ok) return reply(request, { error: "aviso" }, 502);
    return reply(request, { ok: true, codigo: code });
  },
};
