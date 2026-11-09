(() => {
  const $ = (s) => document.querySelector(s);
  const form = $("#form"), input = $("#url"), goBtn = $("#goBtn"), pasteBtn = $("#pasteBtn");
  const errorBox = $("#error"), skeleton = $("#skeleton"), result = $("#result");
  const videoOpts = $("#videoOpts"), audioOpts = $("#audioOpts"), queue = $("#queue");
  let current = null;

  /* ---------------- theme ---------------- */
  const root = document.documentElement;
  try { const t = localStorage.getItem("vf-theme"); if (t) root.dataset.theme = t; } catch {}
  $("#themeToggle").addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "light" ? "dark" : "light";
    try { localStorage.setItem("vf-theme", root.dataset.theme); } catch {}
  });

  /* ---------------- helpers ---------------- */
  const toast = (msg) => {
    const t = $("#toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2600);
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDur = (s) => {
    if (!s) return "";
    s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
  };
  const fmtSpeed = (b) => !b ? "" : b > 1048576 ? (b / 1048576).toFixed(1) + " MB/s" : (b / 1024).toFixed(0) + " KB/s";
  const fmtEta = (s) => (s == null ? "" : s > 60 ? `${Math.floor(s / 60)}m ${s % 60}s left` : `${s}s left`);
  const fmtViews = (n) => !n ? "" : n >= 1e9 ? (n / 1e9).toFixed(1) + "B views" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M views" : n >= 1e3 ? (n / 1e3).toFixed(1) + "K views" : n + " views";
  const DL_ICON = '<svg viewBox="0 0 24 24"><path d="M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 20h14"/></svg>';

  const detect = (url) => {
    const u = url.toLowerCase();
    if (/youtu\.?be/.test(u)) return "youtube";
    if (u.includes("instagram")) return "instagram";
    if (u.includes("tiktok")) return "tiktok";
    if (/(twitter|x)\.com/.test(u)) return "x";
    if (u.includes("facebook") || u.includes("fb.watch")) return "facebook";
    if (u.includes("vimeo")) return "vimeo";
    if (u.includes("reddit")) return "reddit";
    if (u.includes("soundcloud")) return "soundcloud";
    return null;
  };
  input.addEventListener("input", () => {
    const p = detect(input.value);
    document.querySelectorAll("#platformChips span").forEach((s) => s.classList.toggle("active", s.dataset.p === p));
  });

  const api = async (path, body) => {
    let r;
    try {
      r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
    } catch {
      throw new Error("Can't reach the VidFetch server. Make sure `python app.py` is running and open http://localhost:8000");
    }
    const data = await r.json().catch(() => null);
    if (!data) {
      // Not JSON: the page is being served by something other than app.py (Live Server, file://, static hosting)
      throw new Error([404, 405, 501].includes(r.status)
        ? `The download server isn't running here (HTTP ${r.status}). Start it with \`python app.py\` and open http://localhost:8000 — don't open index.html directly or with Live Server.`
        : `Server error (HTTP ${r.status}). Check the terminal running app.py for details.`);
    }
    if (!r.ok) {
      const d = data.detail;
      throw new Error(Array.isArray(d) ? d.map((x) => x.msg).join(", ") : d || `Request failed (HTTP ${r.status}).`);
    }
    return data;
  };

  /* ---------------- paste ---------------- */
  pasteBtn.addEventListener("click", async () => {
    try {
      const t = await navigator.clipboard.readText();
      if (t) { input.value = t.trim(); input.dispatchEvent(new Event("input")); form.requestSubmit(); }
    } catch { input.focus(); toast("Press Ctrl+V / ⌘V to paste"); }
  });
  // auto-start when a link is pasted into the box
  input.addEventListener("paste", () => setTimeout(() => { if (/^https?:\/\//i.test(input.value.trim())) form.requestSubmit(); }, 0));

  /* ---------------- fetch info ---------------- */
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = input.value.trim();
    if (!url || goBtn.classList.contains("loading")) return;
    errorBox.hidden = true; result.hidden = true; skeleton.hidden = false;
    goBtn.classList.add("loading");
    try {
      current = await api("/api/info", { url });
      render(current);
    } catch (err) {
      errorBox.textContent = err.message; errorBox.hidden = false;
    } finally {
      goBtn.classList.remove("loading"); skeleton.hidden = true;
    }
  });

  function render(info) {
    $("#thumb").src = info.thumbnail || "";
    $("#thumb").hidden = !info.thumbnail;
    $("#thumb").onerror = () => { $("#thumb").hidden = true; };
    $("#title").textContent = info.title;
    $("#uploader").textContent = [info.uploader, fmtViews(info.view_count)].filter(Boolean).join(" · ");
    $("#duration").textContent = fmtDur(info.duration);
    $("#duration").hidden = !info.duration;
    $("#platform").textContent = info.platform;

    videoOpts.innerHTML = info.video.length
      ? info.video.map((v, i) => `
        <button class="opt" data-kind="video" data-q="${v.quality}" style="animation-delay:${i * 45}ms">
          <div><div class="q">${esc(v.label)}${v.fps ? `<small>${v.fps}fps</small>` : ""}</div>
          <div class="s">MP4${v.size ? " · ~" + esc(v.size) : ""}</div><span class="tag">${esc(v.badge)}</span></div>
          <span class="dl">${DL_ICON}</span></button>`).join("")
      : `<p class="empty-opt">No video stream found — try the Audio tab.</p>`;

    audioOpts.innerHTML = info.audio.length
      ? info.audio.map((a, i) => `
        <button class="opt" data-kind="audio" data-f="${a.format}" data-b="${a.bitrate}" style="animation-delay:${i * 45}ms">
          <div><div class="q">${esc(a.format.toUpperCase())}${a.bitrate ? `<small>${a.bitrate}k</small>` : ""}</div>
          <div class="s">${esc(a.label)}${a.size ? " · ~" + esc(a.size) : ""}</div></div>
          <span class="dl">${DL_ICON}</span></button>`).join("")
      : `<p class="empty-opt">No audio track available.</p>`;

    result.hidden = false;
    result.style.animation = "none"; result.offsetHeight; result.style.animation = "";
    setTab(info.video.length ? "video" : "audio");
    setTimeout(() => result.scrollIntoView({ behavior: "smooth", block: "center" }), 80);
  }

  /* ---------------- tabs ---------------- */
  const ink = document.querySelector(".tab-ink");
  function setTab(name) {
    document.querySelectorAll(".tab").forEach((t) => {
      const on = t.dataset.tab === name;
      t.classList.toggle("active", on);
      if (on) { ink.style.width = t.offsetWidth + "px"; ink.style.transform = `translateX(${t.offsetLeft - 5}px)`; }
    });
    videoOpts.classList.toggle("show", name === "video");
    audioOpts.classList.toggle("show", name === "audio");
  }
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setTab(t.dataset.tab)));
  window.addEventListener("resize", () => { const a = document.querySelector(".tab.active"); if (a && !result.hidden) setTab(a.dataset.tab); });

  /* ---------------- downloads ---------------- */
  result.addEventListener("click", (e) => {
    const b = e.target.closest(".opt");
    if (!b || !current) return;
    const body = { url: current.url, kind: b.dataset.kind };
    let label;
    if (body.kind === "video") { body.quality = +b.dataset.q; label = (b.querySelector(".q").textContent) + " MP4"; }
    else { body.format = b.dataset.f; body.bitrate = +b.dataset.b; label = body.format.toUpperCase() + (body.bitrate ? ` ${body.bitrate}kbps` : ""); }
    startJob(body, label, current);
  });

  async function startJob(body, label, info) {
    const el = document.createElement("div");
    el.className = "job";
    el.innerHTML = `
      <div class="jthumb">${info.thumbnail ? `<img src="${esc(info.thumbnail)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()" />` : ""}</div>
      <div style="min-width:0">
        <div class="jt">${esc(info.title)}</div>
        <div class="js"><span class="st">Queued · ${esc(label)}</span><span class="sp"></span><span class="eta"></span></div>
        <div class="bar"><i></i></div>
      </div>
      <span class="act pct">0%</span>`;
    queue.prepend(el);
    toast("Download started");
    const st = el.querySelector(".st"), sp = el.querySelector(".sp"), eta = el.querySelector(".eta"),
      bar = el.querySelector(".bar i"), act = el.querySelector(".act");

    let id;
    try { ({ job_id: id } = await api("/api/download", body)); }
    catch (err) { el.classList.add("error"); st.textContent = err.message; act.textContent = "✕"; return; }

    const poll = async () => {
      let j;
      try { j = await api(`/api/progress/${id}`); } catch { return setTimeout(poll, 1500); }
      bar.style.width = (j.progress || 0) + "%";
      if (j.status === "downloading" || j.status === "starting" || j.status === "queued") {
        st.textContent = j.status === "downloading" ? `Downloading · ${label}` : `Preparing · ${label}`;
        sp.textContent = fmtSpeed(j.speed); eta.textContent = fmtEta(j.eta);
        act.textContent = Math.floor(j.progress || 0) + "%";
        setTimeout(poll, 700);
      } else if (j.status === "processing") {
        el.classList.add("processing");
        st.textContent = body.kind === "audio" ? "Converting audio…" : "Merging video & audio…";
        sp.textContent = eta.textContent = ""; act.textContent = "⋯";
        setTimeout(poll, 900);
      } else if (j.status === "done") {
        el.classList.remove("processing"); el.classList.add("done");
        st.textContent = `Ready · ${label}`; sp.textContent = j.size || ""; eta.textContent = "";
        act.innerHTML = DL_ICON; act.title = "Save again";
        const href = `/api/file/${id}`;
        act.onclick = () => save(href);
        save(href);
        toast("✓ Download complete");
      } else if (j.status === "error") {
        el.classList.add("error"); st.textContent = j.error || "Download failed"; act.textContent = "✕";
        sp.textContent = eta.textContent = "";
      }
    };
    poll();
  }

  function save(href) {
    const a = document.createElement("a");
    a.href = href; a.download = ""; document.body.appendChild(a); a.click(); a.remove();
  }
})();
