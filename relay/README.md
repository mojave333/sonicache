# Spotify relay

Spotify keeps the pages of its releases from the countries it does not work in, Russia
among them. It goes by where the request comes from, so a Trackhound there gets "Sorry,
that's not currently available" for a link that opens fine for everyone else. The relay
reads the same public pages from abroad and hands the program back only what it uses, as
a small JSON. It is one file, [`worker.js`](worker.js), for a free Cloudflare Worker.

The program asks the relay only when Spotify refuses a page, and only for the release in
the link: `?kind=album|track|playlist&id=<Spotify id>`. The worker fetches nothing else,
so it is no open proxy. Answers are kept at Cloudflare's edge for an hour.

## Putting it up

No command line is needed.

1. Sign up at [dash.cloudflare.com](https://dash.cloudflare.com); the free plan is enough
   (100,000 requests a day, where an album takes one or two).
2. **Workers & Pages → Create → Create Worker**, name it `trackhound-relay`, and
   **Deploy** the "Hello World" it offers.
3. **Edit code**, replace everything with the contents of `worker.js`, and **Deploy**.
4. In the worker's settings, set **Placement** to **Smart**. Cloudflare then tends to run
   it near Spotify's servers rather than near the person asking, which makes it much less
   likely that Spotify sees the request coming from the country it refuses.
5. Open `https://trackhound-relay.<your-subdomain>.workers.dev/?kind=album&id=2noRn2Aes5aoNVsU6iWThc`.
   A JSON with `"entity"` and Daft Punk's *Discovery* in it means it works.

With Node.js the same takes one command in this folder, with [`wrangler.toml`](wrangler.toml)
already set: `npx wrangler deploy`.

## Pointing the program at it

- **For everyone who installs the program:** put the address into `SPOTIFY_RELAY` in
  `trackhound/__init__.py` and release. An empty "Spotify relay" setting means this one.
- **For one computer:** Settings → Spotify relay, or `--relay <address>` on the command
  line. `off` turns the relay off.

Settings → Diagnostics → "Check" says whether the relay answers, and so does
`trackhound --check`.

## Where Cloudflare itself is slowed down

Russian providers have been cutting connections to Cloudflare after about 16 KB. The
relay's answers stay under that: an album is about 1 KB and a playlist of 100 tracks about
5 KB, compressed. If `workers.dev` addresses are blocked outright, a domain of your own
attached to the worker (Settings → Domains & Routes) usually is not. The file also runs on
other hosts that speak the same Fetch API, such as Deno Deploy:

```js
import worker from "./worker.js";
Deno.serve((request) => worker.fetch(request, {}, { waitUntil() {} }));
```
