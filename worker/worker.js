// Recibe un tema del formulario, lo pasa tal cual al borrador "buzon" y avisa al Action.
// Nunca abre el archivo: quien valida es el Action. Secretos: GITHUB_TOKEN y TURNSTILE_SECRET.
const REPO = "MarckDP/dowp-temas";
const MAX_BYTES = 20 * 1024 * 1024;
const ORIGINS = ["https://marckdp.github.io", "http://localhost:8000"];

function reply(request, body, status = 200) {
  const origin = request.headers.get("Origin");
  const headers = { "Content-Type": "application/json" };
  if (ORIGINS.includes(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
    headers["Vary"] = "Origin";
  }
  return new Response(JSON.stringify(body), { status, headers });
}

async function humanCheck(token, ip, secret) {
  const form = new FormData();
  form.append("secret", secret);
  form.append("response", token);
  if (ip) form.append("remoteip", ip);
  const answer = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", { method: "POST", body: form });
  return (await answer.json()).success === true;
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
          "Access-Control-Allow-Headers": "Content-Type, X-Turnstile",
          "Access-Control-Max-Age": "86400",
          "Vary": "Origin",
        },
      });
    }
    if (request.method !== "POST") return reply(request, { error: "metodo" }, 405);
    if (!ORIGINS.includes(origin)) return reply(request, { error: "origen" }, 403);

    const size = Number(request.headers.get("Content-Length") || 0);
    if (!size) return reply(request, { error: "vacio" }, 400);
    if (size > MAX_BYTES) return reply(request, { error: "grande" }, 413);

    const token = request.headers.get("X-Turnstile");
    if (!token || !(await humanCheck(token, request.headers.get("CF-Connecting-IP"), env.TURNSTILE_SECRET))) {
      return reply(request, { error: "antibots" }, 403);
    }

    const github = {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "dowp-temas-worker",
    };
    const list = await fetch(`https://api.github.com/repos/${REPO}/releases`, { headers: github });
    if (!list.ok) return reply(request, { error: "buzon" }, 502);
    const inbox = (await list.json()).find((r) => r.tag_name === "buzon" && r.draft);
    if (!inbox) return reply(request, { error: "buzon" }, 502);

    const name = `tema-${Date.now()}-${crypto.randomUUID().slice(0, 8)}.bin`;
    const upload = await fetch(`https://uploads.github.com/repos/${REPO}/releases/${inbox.id}/assets?name=${name}`, {
      method: "POST",
      headers: { ...github, "Content-Type": "application/octet-stream", "Content-Length": String(size) },
      body: request.body,
    });
    if (!upload.ok) return reply(request, { error: "subida" }, 502);

    const notify = await fetch(`https://api.github.com/repos/${REPO}/dispatches`, {
      method: "POST",
      headers: { ...github, "Content-Type": "application/json" },
      body: JSON.stringify({ event_type: "tema-subido", client_payload: { archivo: name } }),
    });
    if (!notify.ok) return reply(request, { error: "aviso" }, 502);
    return reply(request, { ok: true });
  },
};
