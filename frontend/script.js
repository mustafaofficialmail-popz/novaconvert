const API_BASE = "http://127.0.0.1:5000";

const form = document.getElementById("lookup-form");
const input = document.getElementById("youtube-url");
const errorBox = document.getElementById("error");
const loading = document.getElementById("loading");
const result = document.getElementById("result");
const button = document.getElementById("lookup-btn");

form.addEventListener("submit", async (event) => {
    event.preventDefault();

    errorBox.hidden = true;
    result.hidden = true;
    loading.hidden = false;
    button.disabled = true;

    try {
        const url = input.value.trim();
        const response = await fetch(
            `${API_BASE}/api/video-info?url=${encodeURIComponent(url)}`
        );
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Could not fetch video details.");
        }

        show(data);
    } catch (error) {
        showError(error.message);
    } finally {
        loading.hidden = true;
        button.disabled = false;
    }
});


function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
}


function makeSelect(labelText, options, defaultValue) {
    const wrapper = document.createElement("label");
    wrapper.className = "nc-control";

    const label = document.createElement("span");
    label.textContent = labelText;

    const select = document.createElement("select");
    options.forEach(option => {
        const element = document.createElement("option");
        element.value = option.value;
        element.textContent = option.label;
        select.append(element);
    });
    select.value = defaultValue;

    wrapper.append(label, select);
    return { wrapper, select };
}


function makeDownloadControls(url) {
    const panel = document.createElement("div");
    panel.className = "nc-download-panel";

    const heading = document.createElement("strong");
    heading.textContent = "Download options";

    const mp4 = makeSelect(
        "MP4 video quality",
        [
            { value: "144", label: "144p" },
            { value: "240", label: "240p" },
            { value: "360", label: "360p" },
            { value: "480", label: "480p" },
            { value: "720", label: "720p" },
            { value: "1080", label: "1080p" }
        ],
        "360"
    );

    const mp3 = makeSelect(
        "MP3 audio bitrate",
        [
            { value: "96", label: "96 kbps" },
            { value: "128", label: "128 kbps" },
            { value: "192", label: "192 kbps" },
            { value: "256", label: "256 kbps" },
            { value: "320", label: "320 kbps" }
        ],
        "192"
    );

    const mp4Button = document.createElement("button");
    mp4Button.type = "button";
    mp4Button.className = "download-btn";
    mp4Button.textContent = "Download MP4";

    const mp3Button = document.createElement("button");
    mp3Button.type = "button";
    mp3Button.className = "download-btn";
    mp3Button.textContent = "Download MP3";

    const progressArea = document.createElement("div");
    progressArea.className = "nc-progress-area";
    progressArea.hidden = true;

    const progressText = document.createElement("p");
    progressText.className = "nc-progress-text";
    progressText.textContent = "Preparing download: 0%";

    const progressTrack = document.createElement("div");
    progressTrack.className = "nc-progress-track";

    const progressFill = document.createElement("div");
    progressFill.className = "nc-progress-fill";
    progressTrack.append(progressFill);

    progressArea.append(progressText, progressTrack);

    mp4Button.addEventListener("click", () => {
        runDownload(url, "mp4", mp4.select.value, [
            mp4Button, mp3Button
        ], progressArea, progressText, progressFill);
    });

    mp3Button.addEventListener("click", () => {
        runDownload(url, "mp3", mp3.select.value, [
            mp4Button, mp3Button
        ], progressArea, progressText, progressFill);
    });

    const buttons = document.createElement("div");
    buttons.className = "nc-download-buttons";
    buttons.append(mp4Button, mp3Button);

    panel.append(
        heading,
        mp4.wrapper,
        mp3.wrapper,
        buttons,
        progressArea
    );

    return panel;
}


async function runDownload(
    url,
    format,
    quality,
    buttons,
    progressArea,
    progressText,
    progressFill
) {
    errorBox.hidden = true;
    buttons.forEach(item => item.disabled = true);
    progressArea.hidden = false;
    progressFill.style.width = "0%";
    progressText.textContent = "Preparing download: 0%";

    try {
        const startResponse = await fetch(`${API_BASE}/api/download`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                url,
                format,
                quality
            })
        });

        const startData = await startResponse.json();

        if (!startResponse.ok) {
            throw new Error(startData.error || "Could not start download.");
        }

        const jobId = startData.job_id;
        let completed = false;

        while (!completed) {
            await new Promise(resolve => setTimeout(resolve, 1000));

            const statusResponse = await fetch(
                `${API_BASE}/api/download-status/${encodeURIComponent(jobId)}`
            );
            const statusData = await statusResponse.json();

            if (!statusResponse.ok) {
                throw new Error(statusData.error || "Could not check download progress.");
            }

            const percent = Math.max(
                0,
                Math.min(100, Number(statusData.progress) || 0)
            );

            progressFill.style.width = `${percent}%`;

            if (statusData.status === "queued" || statusData.status === "starting") {
                progressText.textContent = `Preparing download: ${percent}%`;
            } else if (statusData.status === "downloading") {
                progressText.textContent = `Downloading: ${percent}%`;
            } else if (statusData.status === "processing") {
                progressText.textContent = "Processing file: 99%";
            } else if (statusData.status === "error") {
                throw new Error(statusData.error || "Download failed.");
            } else if (statusData.status === "complete") {
                progressFill.style.width = "100%";
                progressText.textContent = "Download ready: 100%";

                const link = document.createElement("a");
                link.href = `${API_BASE}/api/download-file/${encodeURIComponent(jobId)}`;
                link.style.display = "none";
                document.body.append(link);
                link.click();
                link.remove();

                completed = true;
            }
        }
    } catch (error) {
        showError(error.message);
        progressText.textContent = "Download failed.";
    } finally {
        buttons.forEach(item => item.disabled = false);
    }
}


function show(data) {
    result.replaceChildren();

    if (data.type === "video") {
        const wrap = document.createElement("div");
        wrap.className = "video";

        const image = document.createElement("img");
        image.className = "thumb";
        image.src = data.thumbnail;
        image.alt = "Video thumbnail";

        const info = document.createElement("div");
        const title = document.createElement("h2");
        const meta = document.createElement("p");
        const link = document.createElement("a");

        title.textContent = data.title;
        meta.className = "meta";
        meta.textContent =
            `Channel: ${data.channel} · Published: ${data.publishedAt} · Duration: ${data.duration}`;

        link.href = data.url;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = "Open on YouTube ↗";

        info.append(title, meta, link, makeDownloadControls(data.url));
        wrap.append(image, info);
        result.append(wrap);
    } else {
        const title = document.createElement("h2");
        const meta = document.createElement("p");

        title.textContent = data.title;
        meta.className = "meta";
        meta.textContent = `Playlist · ${data.itemCount} videos`;

        result.append(title, meta);

        data.items.forEach(video => {
            const row = document.createElement("div");
            row.className = "item";

            const image = document.createElement("img");
            image.src = video.thumbnail;
            image.alt = "Video thumbnail";

            const details = document.createElement("div");
            const name = document.createElement("span");
            name.textContent = video.title;
            details.append(name);

            if (video.videoId) {
                const videoUrl =
                    `https://www.youtube.com/watch?v=${video.videoId}`;
                details.append(makeDownloadControls(videoUrl));
            }

            row.append(image, details);
            result.append(row);
        });
    }

    result.hidden = false;
}
