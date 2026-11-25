const $ = (id) => document.getElementById(id);

const PLATFORM_LABELS = {
  youtube: "▶ YouTube",
  youtube_music: "🎵 YouTube Music",
  spotify: "🟢 Spotify",
  apple_music: "🍎 Apple Music",
  shazam: "🔵 Shazam",
  deezer: "🟣 Deezer",
};

const PLATFORM_CLASSES = {
  youtube: "yt",
  youtube_music: "yt",
  spotify: "sp",
  apple_music: "am",
};

const STORAGE_GEMINI = "song_finder_gemini_key";
const STORAGE_AUDD = "song_finder_audd_token";

let selectedAudioFile = null;
let selectedAudioUrl = null;
let mediaRecorder = null;
let recordedChunks = [];
let recordInterval = null;
let recordTimeout = null;
let recordSeconds = 0;

// Settings management
function loadSettings() {
  $("geminiKeyInput").value = localStorage.getItem(STORAGE_GEMINI) || "";
  $("auddTokenInput").value = localStorage.getItem(STORAGE_AUDD) || "";
}

$("toggleSettingsBtn").addEventListener("click", () => {
  const panel = $("settingsPanel");
  const isHidden = panel.classList.toggle("hidden");
  $("toggleSettingsBtn").setAttribute("aria-expanded", String(!isHidden));
});

$("saveSettingsBtn").addEventListener("click", () => {
  localStorage.setItem(STORAGE_GEMINI, $("geminiKeyInput").value.trim());
  localStorage.setItem(STORAGE_AUDD, $("auddTokenInput").value.trim());
  $("settingsSavedMsg").classList.remove("hidden");
  setTimeout(() => $("settingsSavedMsg").classList.add("hidden"), 2500);
});

$("clearSettingsBtn").addEventListener("click", () => {
  localStorage.removeItem(STORAGE_GEMINI);
  localStorage.removeItem(STORAGE_AUDD);
  $("geminiKeyInput").value = "";
  $("auddTokenInput").value = "";
  $("settingsSavedMsg").textContent = "已清除本機金鑰";
  $("settingsSavedMsg").classList.remove("hidden");
  setTimeout(() => {
    $("settingsSavedMsg").classList.add("hidden");
    $("settingsSavedMsg").textContent = "已儲存於本機瀏覽器";
  }, 2500);
});

// Tabs
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const target = tab.dataset.tab;
    document.querySelectorAll(".tab").forEach((t) => {
      const active = t.dataset.tab === target;
      t.classList.toggle("active", active);
      t.setAttribute("aria-selected", String(active));
    });
    $("audioPanel").classList.toggle("active", target === "audio");
    $("lyricsPanel").classList.toggle("active", target === "lyrics");
  });
});

// Audio file selection & drag-and-drop
function setAudioFile(file) {
  if (!file) return;
  if (selectedAudioUrl) URL.revokeObjectURL(selectedAudioUrl);
  selectedAudioFile = file;
  selectedAudioUrl = URL.createObjectURL(file);
  $("audioFileName").textContent = `${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
  $("localAudioPlayer").src = selectedAudioUrl;
  $("audioPreviewBox").classList.remove("hidden");
  $("identifyAudioBtn").disabled = false;
}

function clearAudioSelection() {
  if (selectedAudioUrl) URL.revokeObjectURL(selectedAudioUrl);
  selectedAudioFile = null;
  selectedAudioUrl = null;
  $("audioFileInput").value = "";
  $("localAudioPlayer").removeAttribute("src");
  $("audioPreviewBox").classList.add("hidden");
  $("identifyAudioBtn").disabled = true;
}

$("pickFileBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  $("audioFileInput").click();
});

$("dropZone").addEventListener("click", () => $("audioFileInput").click());
$("dropZone").addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    $("audioFileInput").click();
  }
});

$("audioFileInput").addEventListener("change", (e) => {
  const file = e.target.files?.[0];
  if (file) setAudioFile(file);
});

$("dropZone").addEventListener("dragover", (e) => {
  e.preventDefault();
  $("dropZone").classList.add("dragover");
});
$("dropZone").addEventListener("dragleave", () => $("dropZone").classList.remove("dragover"));
$("dropZone").addEventListener("drop", (e) => {
  e.preventDefault();
  $("dropZone").classList.remove("dragover");
  const file = e.dataTransfer?.files?.[0];
  if (file) setAudioFile(file);
});

$("clearAudioBtn").addEventListener("click", clearAudioSelection);

// Microphone recording
function stopRecordingUI() {
  clearInterval(recordInterval);
  clearTimeout(recordTimeout);
  $("recordBtn").classList.remove("recording");
  $("recordBtnText").textContent = "開始錄音（最長 15 秒）";
  $("recordTimer").classList.add("hidden");
}

$("recordBtn").addEventListener("click", async () => {
  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
    stopRecordingUI();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    showNotice("目前瀏覽器環境不支援麥克風錄音，請直接上傳音檔。");
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordedChunks = [];
    const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";
    mediaRecorder = new MediaRecorder(stream, { mimeType });

    mediaRecorder.ondataavailable = (ev) => {
      if (ev.data && ev.data.size > 0) recordedChunks.push(ev.data);
    };

    mediaRecorder.onstop = () => {
      stream.getTracks().forEach((tr) => tr.stop());
      const blob = new Blob(recordedChunks, { type: mimeType });
      const file = new File([blob], `mic-recording-${Date.now()}.webm`, { type: mimeType });
      setAudioFile(file);
      identifyAudio();
    };

    mediaRecorder.start();
    recordSeconds = 0;
    $("recordBtn").classList.add("recording");
    $("recordBtnText").textContent = "停止錄音並立即辨識";
    $("recordTimer").textContent = "00:00 / 00:15";
    $("recordTimer").classList.remove("hidden");

    recordInterval = setInterval(() => {
      recordSeconds += 1;
      const sec = String(recordSeconds).padStart(2, "0");
      $("recordTimer").textContent = `00:${sec} / 00:15`;
    }, 1000);

    recordTimeout = setTimeout(() => {
      if (mediaRecorder && mediaRecorder.state === "recording") {
        mediaRecorder.stop();
        stopRecordingUI();
      }
    }, 15000);
  } catch (err) {
    showNotice(`無法存取麥克風：${err.message || "請確認瀏覽器麥克風權限"}`);
  }
});

// Example lyric chips
document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    $("lyricsInput").value = chip.dataset.lyrics || "";
    identifyLyrics();
  });
});

$("lyricsInput").addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    identifyLyrics();
  }
});

$("identifyAudioBtn").addEventListener("click", identifyAudio);
$("identifyLyricsBtn").addEventListener("click", identifyLyrics);

// Helpers for UI state
function setLoading(isLoading, message = "正在搜尋與比對歌曲...") {
  $("loadingText").textContent = message;
  $("loadingBox").classList.toggle("hidden", !isLoading);
  if (isLoading) {
    $("noticeBox").classList.add("hidden");
    $("resultsSection").classList.add("hidden");
  }
}

function showNotice(text) {
  $("noticeBox").textContent = text;
  $("noticeBox").classList.remove("hidden");
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderLinks(links) {
  if (!links) return "";
  const order = ["youtube", "youtube_music", "spotify", "apple_music", "shazam", "deezer"];
  return order
    .filter((key) => Boolean(links[key]))
    .map((key) => {
      const cls = PLATFORM_CLASSES[key] || "";
      const label = PLATFORM_LABELS[key] || key;
      return `<a class="platform-link ${cls}" href="${escapeHtml(links[key])}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    })
    .join("");
}

function renderBestMatch(song) {
  const coverHtml = song.cover_url
    ? `<img class="cover-img" src="${escapeHtml(song.cover_url)}" alt="${escapeHtml(song.title)} 封面" loading="lazy" />`
    : `<div class="cover-placeholder" aria-hidden="true">🎵</div>`;

  const albumHtml = song.album ? `<p class="song-album">專輯：${escapeHtml(song.album)}</p>` : "";
  const reasonHtml = song.match_reason
    ? `<p class="match-reason">比對依據：${escapeHtml(song.match_reason)}（來源：${escapeHtml(song.source)}）</p>`
    : "";
  const snippetHtml = song.lyric_snippet
    ? `<blockquote class="lyric-quote">「${escapeHtml(song.lyric_snippet)}」</blockquote>`
    : "";
  const previewHtml = song.preview_url
    ? `<div class="preview-audio"><audio controls preload="none" src="${escapeHtml(song.preview_url)}"></audio></div>`
    : "";

  return `
    <article class="best-card">
      <div class="best-top-row">
        <span class="best-badge">🏆 最符合的選項 (#1)</span>
        <span class="confidence-pill">符合度 ${song.confidence}%</span>
      </div>
      <div class="song-hero">
        ${coverHtml}
        <div class="song-info">
          <h2 class="song-title">${escapeHtml(song.title)}</h2>
          <p class="song-artist">${escapeHtml(song.artist)}</p>
          ${albumHtml}
          ${reasonHtml}
          ${snippetHtml}
          ${previewHtml}
          <div class="links-row">${renderLinks(song.links)}</div>
        </div>
      </div>
    </article>
  `;
}

function renderPossibleCard(song, rank) {
  const coverHtml = song.cover_url
    ? `<img class="alt-cover" src="${escapeHtml(song.cover_url)}" alt="${escapeHtml(song.title)} 封面" loading="lazy" />`
    : `<div class="alt-cover-placeholder" aria-hidden="true">🎶</div>`;

  const previewHtml = song.preview_url
    ? `<audio controls preload="none" src="${escapeHtml(song.preview_url)}"></audio>`
    : "";

  return `
    <article class="alt-card">
      <div>
        <div class="alt-header">
          ${coverHtml}
          <div class="song-info">
            <span class="alt-rank">可能選項 #${rank} · 符合度 ${song.confidence}%</span>
            <h3 class="alt-title">${escapeHtml(song.title)}</h3>
            <p class="alt-artist">${escapeHtml(song.artist)}</p>
            <p class="alt-reason">${escapeHtml(song.match_reason || song.source)}</p>
          </div>
        </div>
      </div>
      <div>
        ${previewHtml}
        <div class="links-row">${renderLinks(song.links)}</div>
      </div>
    </article>
  `;
}

function renderResults(data) {
  if (data.notes) {
    showNotice(data.notes);
  }

  if (!data.best_match) {
    $("resultsSection").classList.add("hidden");
    if (!data.notes) showNotice("未找到符合的歌曲，請嘗試更換片段或歌詞。");
    return;
  }

  $("enginesBadge").textContent = `使用引擎：${(data.engines_used || []).join(" · ")}（共整理出 ${Math.min(5, data.total_found)} 個推薦選項）`;
  $("bestMatchContainer").innerHTML = renderBestMatch(data.best_match);

  const possibles = data.possible_matches || [];
  if (possibles.length > 0) {
    $("possibleMatchesGrid").innerHTML = possibles
      .map((song, idx) => renderPossibleCard(song, idx + 2))
      .join("");
    $("possibleMatchesBlock").classList.remove("hidden");
  } else {
    $("possibleMatchesBlock").classList.add("hidden");
  }

  $("resultsSection").classList.remove("hidden");
}

async function identifyAudio() {
  if (!selectedAudioFile) {
    showNotice("請先選擇或錄製一段音檔。");
    return;
  }
  setLoading(true, "正在分析音訊指紋與尋找相關曲目...");
  try {
    const form = new FormData();
    form.append("file", selectedAudioFile);
    const geminiKey = localStorage.getItem(STORAGE_GEMINI) || "";
    const auddToken = localStorage.getItem(STORAGE_AUDD) || "";
    if (geminiKey) form.append("gemini_api_key", geminiKey);
    if (auddToken) form.append("audd_token", auddToken);

    const resp = await fetch("/api/identify/audio", { method: "POST", body: form });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "辨識請求失敗");
    renderResults(data);
  } catch (err) {
    showNotice(`辨識失敗：${err.message}`);
  } finally {
    setLoading(false);
  }
}

async function identifyLyrics() {
  const lyrics = $("lyricsInput").value.trim();
  if (lyrics.length < 2) {
    showNotice("請輸入至少 2 個字元的歌詞片段。");
    return;
  }
  setLoading(true, "正在比對歌詞資料庫與音樂曲庫...");
  try {
    const geminiKey = localStorage.getItem(STORAGE_GEMINI) || "";
    const resp = await fetch("/api/identify/lyrics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lyrics,
        gemini_api_key: geminiKey || null,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "搜尋請求失敗");
    renderResults(data);
  } catch (err) {
    showNotice(`搜尋失敗：${err.message}`);
  } finally {
    setLoading(false);
  }
}

loadSettings();
