// Trackhound's Spotify relay, a Cloudflare Worker.
//
// Spotify keeps the pages of its releases from the countries it does not work
// in, Russia among them: the program there gets "not currently available"
// where everyone else gets the tracklist. This worker reads the same public
// pages from Cloudflare, outside those countries, and hands the program back
// only what it uses, as a small JSON: Russian providers cut connections to
// Cloudflare after about 16 KB, and a playlist's page is ten times that.
//
//   GET /?kind=album&id=<Spotify id>  ->  {"entity": {...}, "meta": [[key, value], ...]}
//
// kind is album, track or playlist and id the 22 characters of a Spotify id;
// nothing else is fetched, so the worker is no open proxy. An answer is kept
// at the edge for an hour, a refusal for a minute.

const KINDS = new Set(["album", "track", "playlist"]);
const ID = /^[A-Za-z0-9]{22}$/;
const BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
  + "(KHTML, like Gecko) Chrome/140.0 Safari/537.36";
// Spotify writes its meta tags only for the link-preview bots of messengers
const PREVIEW_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)";
const NEXT_DATA = /<script id="__NEXT_DATA__" type="application\/json">([\s\S]*?)<\/script>/;
const META_TAG = /<meta\s[^>]*>/gi;
const ATTR = /([\w:-]+)="([^"]*)"/g;
// The meta tags the program reads. The page also names every country the
// release is allowed in, some 185 tags of them, which it does not need.
const META_KEYS = new Set([
  "og:title", "og:description", "og:image", "music:release_date", "music:song", "music:song:disc",
  "music:song:track", "music:song_count", "music:album", "music:album:track", "music:duration",
  "music:musician_description",
]);
const ENTITY_KEYS = ["name", "title", "subtitle", "duration", "isExplicit", "releaseDate"];
const TRACK_KEYS = ["uri", "title", "subtitle", "duration", "isExplicit"];

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const kind = url.searchParams.get("kind") || "";
    const id = url.searchParams.get("id") || "";
    if (request.method !== "GET" || !KINDS.has(kind) || !ID.test(id)) {
      return answer({ error: "expected ?kind=album|track|playlist&id=<Spotify id>" }, 400, 0);
    }
    const cache = globalThis.caches?.default;
    const key = new Request(`https://relay.invalid/${kind}/${id}`);
    const kept = cache && await cache.match(key);
    if (kept) return kept;

    const [embed, preview] = await Promise.all([
      read(`https://open.spotify.com/embed/${kind}/${id}`, BROWSER_UA),
      read(`https://open.spotify.com/${kind}/${id}`, PREVIEW_UA),
    ]);
    const props = pageProps(embed);
    const entity = trim(props?.state?.data?.entity);
    const body = { entity, meta: metaTags(preview) };
    // Refused here too: say what Spotify answered, so the program can log it
    if (!entity) body.refused = { status: props?.status ?? null, title: props?.title ?? null };
    const response = answer(body, 200, entity ? 3600 : 60);
    if (cache) ctx.waitUntil(cache.put(key, response.clone()));
    return response;
  },
};

async function read(url, userAgent) {
  try {
    const response = await fetch(url, { headers: { "User-Agent": userAgent, "Accept-Language": "en" } });
    return response.ok ? await response.text() : "";
  } catch {
    return "";
  }
}

function pageProps(page) {
  const found = NEXT_DATA.exec(page);
  if (!found) return null;
  try {
    return JSON.parse(found[1])?.props?.pageProps ?? null;
  } catch {
    return null;
  }
}

// The release's data without what the program never reads: colours, preview
// audio, share links, the relations of every track
function trim(entity) {
  if (!entity) return null;
  const kept = pick(entity, ENTITY_KEYS);
  if (Array.isArray(entity.artists)) kept.artists = entity.artists.map((artist) => ({ name: artist?.name }));
  if (Array.isArray(entity.trackList)) kept.trackList = entity.trackList.map((track) => pick(track, TRACK_KEYS));
  const images = entity.visualIdentity?.image;
  if (Array.isArray(images)) {
    kept.visualIdentity = { image: images.map((image) => pick(image, ["url", "maxWidth"])) };
  }
  return kept;
}

function pick(source, keys) {
  const kept = {};
  for (const key of keys) if (source?.[key] !== undefined) kept[key] = source[key];
  return kept;
}

function metaTags(page) {
  const tags = [];
  for (const tag of page.match(META_TAG) || []) {
    const attrs = Object.fromEntries([...tag.matchAll(ATTR)].map((match) => [match[1], match[2]]));
    const name = attrs.property || attrs.name;
    if (META_KEYS.has(name) && attrs.content !== undefined) tags.push([name, unescape(attrs.content)]);
  }
  return tags;
}

function unescape(text) {
  return text.replace(/&(?:#(\d+)|#x([0-9a-f]+)|(amp|lt|gt|quot|apos));/gi, (whole, dec, hex, named) => {
    if (dec) return String.fromCodePoint(Number(dec));
    if (hex) return String.fromCodePoint(parseInt(hex, 16));
    return { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" }[named.toLowerCase()];
  });
}

function answer(body, status, maxAge) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": maxAge ? `public, max-age=${maxAge}` : "no-store",
    },
  });
}
