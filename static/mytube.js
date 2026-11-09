/* MyTube — browser-local state only. No account, nothing is uploaded.
 *
 * localStorage keys:
 *   mytube_history — [{id, title, channel, channel_id, thumbnail, watched_at}] (max 50)
 *   mytube_subs    — {channelId: {name, thumbnail}}
 *   mytube_likes   — {videoId: 1 | -1}
 */
(function () {
  "use strict";

  var HISTORY_KEY = "mytube_history";
  var SUBS_KEY = "mytube_subs";
  var LIKES_KEY = "mytube_likes";
  var MAX_HISTORY = 50;

  function readJSON(key, fallback) {
    try {
      var value = JSON.parse(localStorage.getItem(key) || "null");
      return value == null ? fallback : value;
    } catch (e) {
      return fallback;
    }
  }

  function writeJSON(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
      /* storage full or blocked — local features just degrade */
    }
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function timeAgo(timestamp) {
    var seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
    if (seconds < 3600) return Math.max(1, Math.floor(seconds / 60)) + " min ago";
    if (seconds < 86400) return Math.floor(seconds / 3600) + " hours ago";
    if (seconds < 86400 * 30) return Math.floor(seconds / 86400) + " days ago";
    return Math.floor(seconds / (86400 * 30)) + " months ago";
  }

  /* ---- history ---- */

  function getHistory() {
    var history = readJSON(HISTORY_KEY, []);
    return Array.isArray(history) ? history : [];
  }

  function recordWatch(video) {
    if (!video || !video.id) return;
    var history = getHistory().filter(function (item) { return item.id !== video.id; });
    history.unshift({
      id: video.id,
      title: video.title || "",
      channel: video.channel || "",
      channel_id: video.channel_id || "",
      thumbnail: video.thumbnail || "",
      watched_at: Date.now(),
    });
    writeJSON(HISTORY_KEY, history.slice(0, MAX_HISTORY));
  }

  /* ---- subscriptions ---- */

  function getSubs() {
    var subs = readJSON(SUBS_KEY, {});
    return subs && typeof subs === "object" ? subs : {};
  }

  function isSubscribed(channelId) {
    return Boolean(channelId && getSubs()[channelId]);
  }

  function toggleSubscribe(channelId, name, thumbnail) {
    var subs = getSubs();
    if (subs[channelId]) {
      delete subs[channelId];
    } else {
      subs[channelId] = { name: name || "", thumbnail: thumbnail || "" };
    }
    writeJSON(SUBS_KEY, subs);
    return Boolean(subs[channelId]);
  }

  function paintSubscribeButton(button) {
    if (!button) return;
    var subscribed = isSubscribed(button.getAttribute("data-channel-id"));
    button.textContent = subscribed ? "Subscribed" : "Subscribe";
    button.classList.toggle("subscribed", subscribed);
  }

  function wireSubscribeButton(button) {
    if (!button) return;
    paintSubscribeButton(button);
    button.addEventListener("click", function () {
      var subscribed = toggleSubscribe(
        button.getAttribute("data-channel-id"),
        button.getAttribute("data-channel-name"),
        button.getAttribute("data-channel-thumb")
      );
      button.textContent = subscribed ? "Subscribed" : "Subscribe";
      button.classList.toggle("subscribed", subscribed);
    });
  }

  /* ---- likes ---- */

  function wireWatchActions() {
    var likeBtn = document.getElementById("like-btn");
    var dislikeBtn = document.getElementById("dislike-btn");
    if (!likeBtn || !dislikeBtn) return;
    var videoId = likeBtn.getAttribute("data-video-id");
    var likes = readJSON(LIKES_KEY, {});
    var current = likes[videoId] || 0;

    function paint() {
      likeBtn.classList.toggle("active", current === 1);
      dislikeBtn.classList.toggle("active", current === -1);
    }
    function setVote(vote) {
      current = current === vote ? 0 : vote;
      if (current === 0) delete likes[videoId];
      else likes[videoId] = current;
      writeJSON(LIKES_KEY, likes);
      paint();
    }
    likeBtn.addEventListener("click", function () { setVote(1); });
    dislikeBtn.addEventListener("click", function () { setVote(-1); });
    paint();
  }

  /* ---- autoplay next (YouTube IFrame API) ---- */

  var nextVideoId = "";
  var autoplayPlayer = null;

  function setupAutoplayNext(firstRelatedId) {
    nextVideoId = firstRelatedId || "";
    if (!nextVideoId) return;
    // The IFrame API calls onYouTubeIframeAPIReady globally when loaded.
    window.onYouTubeIframeAPIReady = function () {
      var iframe = document.getElementById("mt-player");
      if (!iframe || !window.YT) return;
      autoplayPlayer = new window.YT.Player(iframe, {
        events: {
          onStateChange: function (event) {
            var checkbox = document.getElementById("autoplay-next");
            if (event.data === window.YT.PlayerState.ENDED && checkbox && checkbox.checked) {
              window.location.href = "/mytube/watch?v=" + encodeURIComponent(nextVideoId);
            }
          },
        },
      });
    };
  }

  /* ---- home recommendations ---- */

  function mostWatchedChannel(history) {
    var counts = {};
    var firstSeen = {};
    history.forEach(function (item, index) {
      if (!item.channel_id) return;
      counts[item.channel_id] = (counts[item.channel_id] || 0) + 1;
      if (firstSeen[item.channel_id] === undefined) firstSeen[item.channel_id] = index;
    });
    var best = null;
    Object.keys(counts).forEach(function (channelId) {
      if (!best || counts[channelId] > counts[best] ||
          (counts[channelId] === counts[best] && firstSeen[channelId] < firstSeen[best])) {
        best = channelId;
      }
    });
    if (!best) return null;
    var entry = history.find(function (item) { return item.channel_id === best; });
    return { channel_id: best, channel: entry ? entry.channel : "" };
  }

  function cardHtml(video) {
    var meta = [video.views_label, video.age].filter(Boolean).join(" · ");
    return (
      '<article class="mt-card" data-channel-id="' + escapeHtml(video.channel_id || "") + '">' +
        '<a class="mt-thumb" href="/mytube/watch?v=' + encodeURIComponent(video.id) + '">' +
          '<img src="' + escapeHtml(video.thumbnail || "") + '" alt="" loading="lazy">' +
          (video.duration_label ? '<span class="mt-duration">' + escapeHtml(video.duration_label) + "</span>" : "") +
        "</a>" +
        '<div class="mt-card-body">' +
          '<a class="mt-card-title" href="/mytube/watch?v=' + encodeURIComponent(video.id) + '">' + escapeHtml(video.title) + "</a>" +
          (video.channel_id
            ? '<a class="mt-card-channel" href="/mytube/channel/' + encodeURIComponent(video.channel_id) + '">' + escapeHtml(video.channel) + "</a>"
            : '<span class="mt-card-channel">' + escapeHtml(video.channel) + "</span>") +
          '<p class="mt-card-meta">' + escapeHtml(meta) + "</p>" +
        "</div>" +
      "</article>"
    );
  }

  function homeRecommendations() {
    var history = getHistory();
    if (!history.length) return;
    var watchedChannels = {};
    history.forEach(function (item) { if (item.channel_id) watchedChannels[item.channel_id] = true; });

    // Boost: move cards from watched channels to the front of the grid.
    var grid = document.getElementById("home-grid");
    if (grid) {
      var cards = Array.prototype.slice.call(grid.querySelectorAll(".mt-card"));
      var boosted = cards.filter(function (card) { return watchedChannels[card.getAttribute("data-channel-id")]; });
      var rest = cards.filter(function (card) { return !watchedChannels[card.getAttribute("data-channel-id")]; });
      boosted.concat(rest).forEach(function (card) { grid.appendChild(card); });
    }

    // "Because you watched X" row: latest uploads of the most-watched channel.
    var top = mostWatchedChannel(history);
    var row = document.getElementById("because-row");
    var rowGrid = document.getElementById("because-grid");
    var rowTitle = document.getElementById("because-title");
    if (!top || !row || !rowGrid) return;
    fetch("/mytube/api/channel-videos?channel_id=" + encodeURIComponent(top.channel_id))
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (!data || !data.videos || !data.videos.length) return;
        if (rowTitle) rowTitle.textContent = "Because you watched " + top.channel;
        rowGrid.innerHTML = data.videos.slice(0, 8).map(cardHtml).join("");
        row.classList.remove("hidden");
      })
      .catch(function () { /* row stays hidden; recommendations degrade quietly */ });
  }

  /* ---- history / subscriptions pages ---- */

  function renderHistoryPage() {
    var list = document.getElementById("history-list");
    if (!list) return;
    var history = getHistory();
    if (!history.length) {
      list.innerHTML = '<p class="status">No watch history yet. Watch a video and it will show up here.</p>';
    } else {
      list.innerHTML = history.map(function (item) {
        return (
          '<div class="mt-history-item">' +
            '<a href="/mytube/watch?v=' + encodeURIComponent(item.id) + '"><img src="' + escapeHtml(item.thumbnail || "") + '" alt=""></a>' +
            "<div>" +
              '<a href="/mytube/watch?v=' + encodeURIComponent(item.id) + '">' + escapeHtml(item.title) + "</a>" +
              "<p>" + escapeHtml(item.channel) + " &middot; " + escapeHtml(timeAgo(item.watched_at)) + "</p>" +
            "</div>" +
          "</div>"
        );
      }).join("");
    }
    var clearBtn = document.getElementById("clear-history");
    if (clearBtn) {
      clearBtn.addEventListener("click", function () {
        writeJSON(HISTORY_KEY, []);
        list.innerHTML = '<p class="status">History cleared.</p>';
      });
    }
  }

  function renderSubscriptionsPage() {
    var list = document.getElementById("subs-list");
    if (!list) return;
    var subs = getSubs();
    var ids = Object.keys(subs);
    if (!ids.length) {
      list.innerHTML = '<p class="status">No subscriptions yet. Hit Subscribe on any channel and it will show up here.</p>';
      return;
    }
    list.innerHTML = ids.map(function (channelId) {
      var sub = subs[channelId] || {};
      return (
        '<div class="mt-sub-card">' +
          (sub.thumbnail ? '<img src="' + escapeHtml(sub.thumbnail) + '" alt="">' : "") +
          '<div><a href="/mytube/channel/' + encodeURIComponent(channelId) + '">' + escapeHtml(sub.name || channelId) + "</a></div>" +
          '<button class="mt-clear-btn" type="button" data-unsub="' + escapeHtml(channelId) + '">Unsubscribe</button>' +
        "</div>"
      );
    }).join("");
    list.addEventListener("click", function (event) {
      var button = event.target.closest("[data-unsub]");
      if (!button) return;
      toggleSubscribe(button.getAttribute("data-unsub"), "", "");
      renderSubscriptionsPage();
    });
  }

  window.MT = {
    recordWatch: recordWatch,
    wireWatchActions: wireWatchActions,
    wireSubscribeButton: wireSubscribeButton,
    setupAutoplayNext: setupAutoplayNext,
    homeRecommendations: homeRecommendations,
    renderHistoryPage: renderHistoryPage,
    renderSubscriptionsPage: renderSubscriptionsPage,
  };
})();
