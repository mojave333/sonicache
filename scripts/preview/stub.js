(() => {
  const params = new URLSearchParams(location.search);
  const settings = { folder: "C:/Music", format: "mp3", threads: 3, theme: params.get("theme") || "dark", dry_run: false,
    cookies_browser: "", rate_limit: 0, proxy: "", language: "ru", track_name: "auto", folder_name: "flat", sidebar: 64,
    replaygain: false, lyrics: true, relay: "", tray: true, notify: true, profiles: [], library_folders: [],
    library_view: "grid", volume: 0.8, shuffle: false, repeat: "off" };
  const LRC = "[00:09.80]First line of the verse\n[00:13.00]Second line, a little longer than the first\n[00:16.20]Third line\n[00:19.40]Fourth line, then a pause\n[00:22.60]\n[00:28.60]The chorus starts here\n[00:31.80]And goes on for a while\n[00:35.00]Until it stops\n[00:38.20]\n[00:44.20]First line of the verse\n[00:47.40]Second line, a little longer than the first\n[00:50.60]Third line\n[00:53.80]Fourth line, then a pause\n[00:57.00]\n[01:03.00]The chorus starts here\n[01:06.20]And goes on for a while\n[01:09.40]Until it stops\n[01:12.60]\n[01:18.60]First line of the verse\n[01:21.80]Second line, a little longer than the first\n[01:25.00]Third line\n[01:28.20]Fourth line, then a pause\n[01:31.40]\n[01:37.40]The chorus starts here\n[01:40.60]And goes on for a while\n[01:43.80]Until it stops\n[01:47.00]\n";
  const now = Date.now() / 1000;
  const items = [{ link: "", expected: 0, album: true, title: "In Rainbows", artist: "Radiohead", year: "2007",
    path: "C:/Music/In Rainbows", tracks: 16, size: 9e7, modified: now, cover: true, genre: "Art Rock; Alternative Rock" },
    { link: "", expected: 0, album: true, title: "Discovery", artist: "Daft Punk", year: "2001",
    path: "C:/Music/Discovery", tracks: 14, size: 8e7, modified: now - 50, cover: true, genre: "House; Electronic" }];
  const base = {
    init: async () => ({ settings, problems: [], watched: [], language: "ru", version: "1.5.1", history: [], tray: true }),
    poll: async () => [],
    library: async () => items,
    library_cached: async () => items,
    tracks: async () => ["Sonic Youth", "Slowdive", "Pixies", "Deftones"].flatMap((artist) => Array.from({ length: 6 }, (_, i) => ({
      title: `${artist} ${i + 1}`, artists: artist, album_artist: artist, album: "A", number: i + 1, disc: 1, year: "2000",
      genre: "Rock", duration: 200, bitrate: 320, format: "mp3", path: `C:/Music/${artist}/${i + 1}.mp3`, entry: `C:/Music/${artist}`,
      cover: true, modified: 0 }))),
    delete: async (paths) => { await new Promise((r) => setTimeout(r, 1500)); return { deleted: paths.length, failed: [] }; },
    tag_gaps: async () => items.map((item) => item.path),
    search: async () => ({ service: "Deezer", artists: [], tracks: [{ title: "15 Step", artist: "Radiohead", album: "In Rainbows", duration: 238, link: "t1", cover: "cover.svg" }],
      albums: [{ title: "In Rainbows (Deluxe Edition)", artist: "Radiohead", year: "2007", type: "album", tracks: 18, link: "a1", cover: "cover.svg" },
               { title: "Kid A", artist: "Radiohead", year: "2000", type: "album", tracks: 10, link: "a2", cover: "cover.svg" }] }),
    catalogue_home: async () => ({ service: "Deezer",
      tracks: ["Dracula", "Boston", "Choosin' Texas", "Golden", "Ordinary", "Manchild", "Die With a Smile", "APT.", "Abracadabra", "Birds of a Feather", "Espresso", "Pink Pony Club"].map((title, i) => ({ kind: "track", title, artist: ["Tame Impala", "Augusta", "Ella Langley", "HUNTR/X"][i % 4], album: title, duration: 180 + i * 7, link: "t" + i, cover: "cover.svg" })),
      artists: ["Ares", "Isac Elliot", "Lauri Haav", "Tame Impala", "Sabrina Carpenter", "Billie Eilish", "Kendrick Lamar", "Charli xcx", "Lady Gaga", "Rosé"].map((name, i) => ({ kind: "artist", name, albums: 0, fans: 0, picture: "", link: "a" + i })),
      picks: ["Midwestern", "WE ARE ALL DEMONS", "Mount Zero", "I Killed Your Dog", "Songs In The Key Of Life - The EP", "The Source", "South"].map((title, i) => ({ kind: "album", title, artist: ["Belles", "Gothminister", "Swapmeet", "L'Rain"][i % 4], year: "", type: "album", tracks: 10, link: "k" + i, cover: "cover.svg" })),
      fresh: ["Hail to the Thief (Live Recordings)", "Homework (25th Anniversary)", "Kid A Mnesia", "Alive 2007", "The King of Limbs", "TRON: Legacy", "A Moon Shaped Pool", "Random Access Memories"].map((title, i) => ({ title, artist: i % 2 ? "Daft Punk" : "Radiohead", year: "2026", type: i % 3 ? "album" : "single", tracks: 10 + i, link: "f" + i, cover: "cover.svg" })),
      popular: ["Brat", "Short n' Sweet", "Hit Me Hard and Soft", "GNX", "Chromakopia", "Debí Tirar Más Fotos", "Mayhem", "Swag", "Man's Best Friend", "The Life of a Showgirl", "Songs of a Lost World", "Imaginal Disk"].map((title, i) => ({ title, artist: ["Charli xcx", "Sabrina Carpenter", "Billie Eilish", "Kendrick Lamar"][i % 4], year: "2025", type: "album", tracks: 12, link: "p" + i, cover: "cover.svg" })) }),
    playlists: async () => [{ id: "mix", name: "Mix", count: 3, smart: false, duration: 600 }],
    playlist: async (id) => ({ id, name: "Mix", smart: false, count: 3, duration: 600, cover: "cover.svg", custom: window.__custom || false,
      tracks: [1, 2, 3].map((n) => ({ path: `C:/Music/mix${n}.mp3`, title: `Mix ${n}`, artists: "Radiohead", album: "In Rainbows", album_artist: "Radiohead", duration: 200, missing: false })) }),
    set_playlist_cover: async (id) => { window.__custom = true; return { id, cover: "cover.svg?v=2", custom: true }; },
    reset_playlist_cover: async (id) => { window.__custom = false; return { id, cover: "cover.svg", custom: false }; },
    release: async () => ({ title: "In Rainbows (Deluxe Edition)", artist: "Radiohead", year: "2007", kind: "album", cover: "cover.svg", service: "Deezer",
      tracks: ["15 Step", "Bodysnatchers", "Nude", "Weird Fishes/Arpeggi", "All I Need", "Faust Arp", "Reckoner", "House of Cards", "Jigsaw Falling Into Place", "Videotape"].map((title, i) => ({ number: i + 1, disc: 1, title, artists: "Radiohead", duration: 190 + i * 11, link: `t${i}` })) }),
    cover: async () => "cover.svg",
    covers: async (paths) => Object.fromEntries(paths.map((p) => [p, "cover.svg"])),
    album: async () => ({ tracks: Array.from({ length: 16 }, (_, i) => ({ title: `Track ${i + 1}`, artists: "Radiohead",
      album: "In Rainbows", number: i + 1, disc: 1, year: "2007", genre: "Art Rock", duration: 240, bitrate: 320,
      format: "mp3", path: `x${i}` })), missing: [], expected: 0, link: "", service: "" }),
    diagnostics: async () => ({}),
    play: async (path) => ({ title: "15 Step", artists: "Radiohead", album: "In Rainbows", album_artist: "Radiohead",
      year: "2007", duration: 238, bitrate: 320, format: "mp3", sample_rate: 44100, channels: 2, path, url: "track.wav",
      cover: "cover.svg", gain: { track: -8.2, album: -7.4 } }),
    lyrics: async () => ({ synced: LRC, plain: "", source: "file" }),
    download: async (links) => links.map((link, i) => ({ job: 700 + Math.floor(Math.random() * 1000) + i, link })),
    devices: async () => [
      { id: "vol:1", name: "IPOD 5G", root: "E:\\", free: 20e9, total: 30e9, kind: "ipod", note: "", ffmpeg: true, synced: true,
        selection: { all: false, playlists: ["mix"], albums: [], artists: [], transcode: false, bitrate: 256 } },
      { id: "vol:2", name: "WALKMAN", root: "F:\\", free: 5e9, total: 32e9, kind: "drive", note: "", ffmpeg: true, synced: false,
        selection: { all: true, playlists: [], albums: [], artists: [], transcode: false, bitrate: 256 } },
      { id: "mtp:3", name: "Pixel 8 · Внутренняя память", root: "mtp:x|1", free: 0, total: 0, kind: "mtp", note: "Разблокируйте телефон и выберите в уведомлении «Передача файлов»", ffmpeg: true, synced: false,
        selection: { all: false, playlists: [], albums: [], artists: [], transcode: false, bitrate: 256 } }],
    device_plan: async () => ({ tracks: 12, bytes: 9e8, deleted: 0, keep: 3, conflicts: 0, missing: 0, need: 9e8, free: 20e9, fits: true, unplayable: 1 }),
    wifi: async () => ({ on: false, running: false, port: 4533, addresses: ["192.168.1.5"], user: "sonicache", password: "abcd234efg", error: "" }),
    paste: async () => window.__clipboard || "",
  };
  window.pywebview = { api: new Proxy(base, { get: (t, k) => t[k] || (async () => null) }) };
  window.dispatchEvent(new Event("pywebviewready"));
})();
