// request is supplied as inert JSON by the Python adapter.
const mediaHost = /^https:\/\/(?:www\.|m\.)?youtube\.com\//;
const videoIdPattern = /^[A-Za-z0-9_-]{11}$/;
const playerOperations = new Set(["pause", "play", "mute", "unmute", "volume", "seek", "status"]);

function tabIdentity(tab) {
    if (typeof tab?.targetId !== "string" || typeof tab?.url !== "string" ||
        !mediaHost.test(tab.url) || tab.url.length > 4096) return null;
    const match = tab.url.match(/^https:\/\/[^/]+([^?#]*)(?:\?([^#]*))?/);
    const path = match[1];
    const videoId = path === "/watch"
        ? new URLSearchParams(match[2]).get("v")
        : path.match(/^\/(?:shorts|live|embed)\/([A-Za-z0-9_-]{11})$/)?.[1];
    return videoIdPattern.test(videoId ?? "") ? {targetId: tab.targetId, url: tab.url, videoId} : null;
}

function sameTarget(left, right) {
    return left && right && left.targetId === right.targetId &&
        left.url === right.url && left.videoId === right.videoId;
}

function safeTitle(title) {
    return typeof title === "string" ? title.replace(/\s*[-–]\s*YouTube\s*$/i, "").slice(0, 180) : "";
}

async function observe(page, target, op, value) {
    return page.evaluate(async ({target, op, value}) => {
        const path = new URL(target.url).pathname;
        const shortsPage = path.startsWith("/shorts/");
        const embedPage = path.startsWith("/embed/");

        function activeVideo() {
            const selector = ".html5-video-player video.html5-main-video";
            const players = [...document.querySelectorAll(selector)].filter(video => {
                const {width, height} = video.getBoundingClientRect();
                if (!width || !height || video.closest("#inline-preview-player") ||
                    video.checkVisibility?.({visibilityProperty: true}) === false) return false;
                if (shortsPage) return video.closest("ytd-reel-video-renderer[is-active]");
                return video.closest("#movie_player") &&
                    (embedPage || video.closest("ytd-watch-flexy, ytm-watch"));
            });
            return players.length === 1 ? players[0] : null;
        }

        function state(video) {
            if (location.href !== target.url) return {error: "changed"};
            if (!video || video.readyState < 1 ||
                video.closest(".ad-showing, .ad-interrupting")) return {error: "unloaded"};
            const player = video.closest(".html5-video-player");
            const playingId = player?.getVideoData?.()?.video_id;
            const owner = video.closest("ytd-watch-flexy[video-id], ytd-reel-video-renderer[video-id]");
            const ownerId = owner?.getAttribute("video-id");
            const observedId = playingId || ownerId;
            if (!observedId) return {error: "unloaded"};
            if (observedId !== target.videoId || (ownerId && ownerId !== observedId)) {
                return {error: "changed"};
            }
            return {
                ...target,
                videoId: observedId,
                title: document.title.replace(/\s*[-–]\s*YouTube\s*$/i, "").slice(0, 180),
                paused: video.paused,
                muted: video.muted,
                volume: video.volume,
                currentTime: video.currentTime,
                duration: Number.isFinite(video.duration) ? video.duration : null,
            };
        }

        const video = activeVideo();
        const before = state(video);
        if (before.error) return before;
        switch (op) {
            case "pause":
                video.pause();
                break;
            case "play":
                if (video.paused) {
                    let timer;
                    try {
                        await Promise.race([
                            video.play(),
                            new Promise((resolve, reject) => {
                                timer = setTimeout(() => reject(new Error("play timeout")), 2500);
                            }),
                        ]);
                    } catch {
                        return {error: "play"};
                    } finally {
                        clearTimeout(timer);
                    }
                }
                break;
            case "mute":
                video.muted = true;
                break;
            case "unmute":
                video.muted = false;
                break;
            case "volume":
                video.volume = value / 100;
                break;
            case "seek":
                if (before.duration === null) return {error: "unloaded"};
                video.currentTime = Math.max(0, Math.min(before.duration, before.currentTime + value));
                if (video.seeking) {
                    await new Promise(resolve => {
                        function finish() {
                            clearTimeout(timer);
                            video.removeEventListener("seeked", finish);
                            resolve();
                        }
                        const timer = setTimeout(finish, 1500);
                        video.addEventListener("seeked", finish);
                    });
                    if (video.seeking) return {error: "seek"};
                }
                break;
        }
        if (activeVideo() !== video) return {error: "changed"};
        const after = state(video);
        return after.error ? after : {before, after};
    }, {target, op, value});
}

async function observeBorrowed(target, op, value) {
    const page = await attachBrowserTab(target.targetId);
    try {
        return await observe(page, target, op, value);
    } finally {
        try {
            // Aside detaches a borrowed tab and leaves the original browser tab open.
            await closeTab(page);
        } catch {
            // Cleanup failure cannot change the observed player result.
        }
    }
}

async function getVideos() {
    const tabs = (await listBrowserTabs()).flatMap(tab => {
        const identity = tabIdentity(tab);
        return identity ? [{...identity, title: safeTitle(tab.title)}] : [];
    });
    if (tabs.length > 40) return {ok: false, error: "too-many"};
    const videos = [];
    for (const tab of tabs) {
        try {
            const result = await observeBorrowed(tab, "status");
            videos.push(result.after ?? tab);
        } catch {
            videos.push(tab);
        }
    }
    return {ok: true, videos};
}

async function applyToVideo({op, target, value}) {
    if (!target || !sameTarget(tabIdentity(target), target)) return {ok: false, error: "invalid"};
    if (op === "volume" && (!Number.isInteger(value) || value < 0 || value > 100)) {
        return {ok: false, error: "invalid"};
    }
    if (op === "seek" && (!Number.isInteger(value) || Math.abs(value) < 1 || Math.abs(value) > 300)) {
        return {ok: false, error: "invalid"};
    }
    const tabs = await listBrowserTabs();
    const tab = tabs.find(tab => tab.targetId === target.targetId);
    if (!tab || !sameTarget(tabIdentity(tab), target)) {
        return {ok: false, error: tab ? "changed" : "missing"};
    }
    const result = await observeBorrowed(target, op, value);
    if (result.error) return {ok: false, error: result.error};
    const current = (await listBrowserTabs()).find(tab => tab.targetId === target.targetId);
    if (!current || !sameTarget(tabIdentity(current), target)) {
        return {ok: false, error: current ? "changed" : "missing"};
    }
    return {ok: true, ...result};
}

async function dispatch(command) {
    if (command.op === "open" || command.op === "search") {
        const valid = command.op === "open"
            ? command.url === "https://www.youtube.com/"
            : /^https:\/\/www\.youtube\.com\/results\?search_query=[A-Za-z0-9%+_.~-]+$/.test(command.url);
        if (!valid) return {ok: false, error: "invalid"};
        const opened = await openTab(command.url);
        return {ok: true, url: opened.url()};
    }
    if (command.op === "list") return getVideos();
    if (playerOperations.has(command.op)) return applyToVideo(command);
    return {ok: false, error: "invalid"};
}

let result;
try {
    result = await dispatch(request);
} catch {
    result = {ok: false, error: "browser"};
}
console.log(JSON.stringify({atticusMedia: {op: request.op, ...result}}));
