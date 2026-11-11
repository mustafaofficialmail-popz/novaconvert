// NovaConvert frontend
// YouTube links provide metadata only. Downloads use direct media-file URLs
// that you own or are authorized to download.
const API_BASE = "https://novaconvert.onrender.com";

const form = document.getElementById("lookup-form");
const input = document.getElementById("youtube-url");
const errorBox = document.getElementById("error");
const loading = document.getElementById("loading");
const result = document.getElementById("result");
const button = document.getElementById("lookup-btn");

// Keep the direct-media tool available even before a YouTube lookup.
const directRoot = document.createElement("div");
if (result?.parentNode) {
    result.parentNode.insertBefore(directRoot, result.nextSibling);
    addDirectDownloader(directRoot);
}

form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    hideError();
    result.hidden = true;
    loading.hidden = false;
    button.disabled = true;
    try {
        const url = input.value.trim();
        const response = await fetch(
            `${API_BASE}/api/video-info?url=${encodeURIComponent(url)}`
        );
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Could not fetch video details.");
        show(data);
    } catch (error) {
        showError(error.message || "Something went wrong.");
    } finally {
        loading.hidden = true;
        button.disabled = false;
    }
});

function hideError() {
    errorBox.hidden = true;
    errorBox.textContent = "";
}
function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
}
function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}
function makeSelect(labelText, options, defaultValue) {
    const wrapper = el("label", "nc-control");
    wrapper.append(el("span", "", labelText));
    const select = document.createElement("select");
    options.forEach(option => {
        const item = document.createElement("option");
        item.value = option.value;
        item.textContent = option.label;
        select.append(item);
    });
    select.value = defaultValue;
    wrapper.append(select);
    return { wrapper, select };
}

function addDirectDownloader(container) {
    const panel = el("section", "nc-download-panel nc-direct-panel");
    panel.append(
        el("strong", "", "Direct media downloader"),
        el("p", "nc-direct-note",
            "Paste a direct MP4, MOV, WebM, MP3, WAV or other supported media-file URL. Use files you own or are authorized to download. A normal webpage or YouTube watch link will not work here.")
    );

    const urlLabel = el("label", "nc-control");
    urlLabel.append(el("span", "", "Direct media file URL"));
    const urlInput = document.createElement("input");
    urlInput.type = "url";
    urlInput.placeholder = "https://example.com/video.mp4";
    urlInput.autocomplete = "url";
    urlLabel.append(urlInput);

    const format = makeSelect("Output format", [
        { value: "mp4", label: "MP4 video" },
        { value: "mp3", label: "MP3 audio" }
    ], "mp4");
    const bitrate = makeSelect("MP3 bitrate", [
        { value: "96", label: "96 kbps" },
        { value: "128", label: "128 kbps" },
        { value: "192", label: "192 kbps" },
        { value: "256", label: "256 kbps" },
        { value: "320", label: "320 kbps" }
    ], "192");
    bitrate.wrapper.hidden = true;
    format.select.addEventListener("change", () => {
        bitrate.wrapper.hidden = format.select.value !== "mp3";
    });

    const start = el("button", "download-btn", "Download file");
    start.type = "button";
    const progress = el("div", "nc-progress-area");
    progress.hidden = true;
    const progressText = el("p", "nc-progress-text", "Preparing: 0%");
    const track = el("div", "nc-progress-track");
    const fill = el("div", "nc-progress-fill");
    fill.style.width = "0%";
    track.append(fill);
    progress.append(progressText, track);
    const status = el("p", "nc-direct-status", "");
    status.setAttribute("role", "status");

    start.addEventListener("click", async () => {
        if (!urlInput.value.trim()) {
            status.textContent = "Enter a direct media-file URL.";
            return;
        }
        await runDirectDownload({
            url: urlInput.value.trim(),
            format: format.select.value,
            quality: bitrate.select.value,
            button: start, progress, progressText, fill, status
        });
    });

    panel.append(urlLabel, format.wrapper, bitrate.wrapper, start, progress, status);
    container.append(panel);
}

async function runDirectDownload(o) {
    hideError();
    o.button.disabled = true;
    o.progress.hidden = false;
    o.fill.style.width = "0%";
    o.progressText.textContent = "Starting: 0%";
    o.status.textContent = "";

    try {
        const startResponse = await fetch(`${API_BASE}/api/direct-download`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: o.url, format: o.format, quality: o.quality })
        });
        const startData = await startResponse.json();
        if (!startResponse.ok) throw new Error(startData.error || "Could not start download.");

        const jobId = startData.job_id;
        const deadline = Date.now() + 60 * 60 * 1000;
        while (Date.now() < deadline) {
            await new Promise(resolve => setTimeout(resolve, 1200));
            const response = await fetch(
                `${API_BASE}/api/download-status/${encodeURIComponent(jobId)}`
            );
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Could not check download status.");

            const percent = Math.max(0, Math.min(100, Number(data.progress) || 0));
            o.fill.style.width = `${percent}%`;

            if (data.status === "queued" || data.status === "starting") {
                o.progressText.textContent = `Preparing: ${percent}%`;
            } else if (data.status === "downloading") {
                o.progressText.textContent = `Downloading: ${percent}%`;
            } else if (data.status === "processing") {
                o.progressText.textContent = "Converting media: please wait";
            } else if (data.status === "error") {
                throw new Error(data.error || "Download failed.");
            } else if (data.status === "complete") {
                o.fill.style.width = "100%";
                o.progressText.textContent = "Ready: 100%";
                const link = document.createElement("a");
                link.href = `${API_BASE}/api/download-file/${encodeURIComponent(jobId)}`;
                link.style.display = "none";
                document.body.append(link);
                link.click();
                link.remove();
                o.status.textContent = "Your file is ready. Check your browser downloads.";
                return;
            }
        }
        throw new Error("This download took too long. Try a smaller file.");
    } catch (error) {
        o.progressText.textContent = "Download failed.";
        o.status.textContent = error.message || "Download failed.";
    } finally {
        o.button.disabled = false;
    }
}

function show(data) {
    result.replaceChildren();
    if (data.type === "video") {
        const wrap = el("div", "video");
        const image = document.createElement("img");
        image.className = "thumb";
        image.src = data.thumbnail;
        image.alt = "YouTube video thumbnail";

        const info = document.createElement("div");
        const title = el("h2", "", data.title);
        const meta = el("p", "meta",
            `Channel: ${data.channel} · Published: ${data.publishedAt} · Duration: ${data.duration}`);
        const link = document.createElement("a");
        link.href = data.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Open on YouTube ↗";
        info.append(title, meta, link, el("p", "nc-direct-note",
            "YouTube information is shown above. Use the direct media downloader below for files you are authorized to download."));
        wrap.append(image, info);
        result.append(wrap);
    } else if (data.type === "playlist") {
        result.append(
            el("h2", "", data.title),
            el("p", "meta", `Playlist · ${data.itemCount} videos`)
        );
        (data.items || []).forEach(video => {
            const row = el("div", "item");
            const image = document.createElement("img");
            image.src = video.thumbnail;
            image.alt = "Playlist video thumbnail";
            const details = document.createElement("div");
            details.append(el("span", "", video.title));
            if (video.videoId) {
                const link = document.createElement("a");
                link.href = `https://www.youtube.com/watch?v=${encodeURIComponent(video.videoId)}`;
                link.target = "_blank";
                link.rel = "noopener noreferrer";
                link.textContent = "Open on YouTube ↗";
                details.append(link);
            }
            row.append(image, details);
            result.append(row);
        });
    }
    result.hidden = false;
}
