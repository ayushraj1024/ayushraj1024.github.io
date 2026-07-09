(function () {
  function extractPostId(entryId) {
    var match = String(entryId || "").match(/\.post-(\d+)$/);
    return match ? match[1] : "";
  }

  function getAlternateLink(entry) {
    var links = entry.link || [];
    for (var i = 0; i < links.length; i += 1) {
      if (links[i].rel === "alternate") {
        return links[i].href;
      }
    }
    return "";
  }

  function stripHtml(html) {
    var tmp = document.createElement("div");
    tmp.innerHTML = html || "";
    return (tmp.textContent || tmp.innerText || "").replace(/\s+/g, " ").trim();
  }

  function formatDate(isoDate) {
    if (!isoDate) {
      return "";
    }
    return new Date(isoDate).toLocaleDateString(undefined, {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  }

  function jsonp(url) {
    return new Promise(function (resolve, reject) {
      var callbackName = "bloggerJsonp_" + Date.now() + "_" + Math.floor(Math.random() * 100000);
      var script = document.createElement("script");
      var timer = window.setTimeout(function () {
        cleanup();
        reject(new Error("Blogger feed request timed out."));
      }, 20000);

      function cleanup() {
        window.clearTimeout(timer);
        delete window[callbackName];
        if (script.parentNode) {
          script.parentNode.removeChild(script);
        }
      }

      window[callbackName] = function (payload) {
        cleanup();
        resolve(payload);
      };

      script.onerror = function () {
        cleanup();
        reject(new Error("Failed to load Blogger feed."));
      };

      var separator = url.indexOf("?") >= 0 ? "&" : "?";
      script.src = url + separator + "alt=json&callback=" + encodeURIComponent(callbackName);
      document.head.appendChild(script);
    });
  }

  function normalizeEntry(entry) {
    var contentHtml = (entry.content && entry.content.$t) || (entry.summary && entry.summary.$t) || "";
    var labels = (entry.category || []).map(function (category) {
      return category.term;
    });

    return {
      id: extractPostId(entry.id && entry.id.$t),
      title: (entry.title && entry.title.$t) || "Untitled",
      published: (entry.published && entry.published.$t) || "",
      updated: (entry.updated && entry.updated.$t) || "",
      url: getAlternateLink(entry),
      contentHtml: contentHtml,
      excerpt: stripHtml(contentHtml).slice(0, 220),
      labels: labels,
    };
  }

  function fetchPosts(options) {
    var config = window.BLOG_CONFIG;
    var params = new URLSearchParams({
      "max-results": String(options.maxResults || config.postsPerPage),
      "start-index": String(options.startIndex || 1),
    });

    return jsonp(config.feedBase + "?" + params.toString()).then(function (payload) {
      var feed = payload.feed || {};
      var entries = feed.entry || [];
      if (!Array.isArray(entries)) {
        entries = entries ? [entries] : [];
      }

      return {
        posts: entries.map(normalizeEntry),
        total: Number((feed.openSearch$totalResults && feed.openSearch$totalResults.$t) || 0),
        startIndex: Number((feed.openSearch$startIndex && feed.openSearch$startIndex.$t) || options.startIndex || 1),
        itemsPerPage: Number((feed.openSearch$itemsPerPage && feed.openSearch$itemsPerPage.$t) || options.maxResults || config.postsPerPage),
      };
    });
  }

  function fetchPostById(postId) {
    var config = window.BLOG_CONFIG;
    return jsonp(config.feedBase + "/" + encodeURIComponent(postId)).then(function (payload) {
      if (!payload.entry) {
        throw new Error("Post not found.");
      }
      return normalizeEntry(payload.entry);
    });
  }

  function getPageFromQuery(defaultPage) {
    var params = new URLSearchParams(window.location.search);
    var page = parseInt(params.get("page") || String(defaultPage || 1), 10);
    return Number.isFinite(page) && page > 0 ? page : 1;
  }

  function buildPostUrl(postId) {
    return "post.html?id=" + encodeURIComponent(postId);
  }

  function buildPageUrl(page) {
    return page <= 1 ? "index.html" : "index.html?page=" + page;
  }

  window.BloggerFeed = {
    fetchPosts: fetchPosts,
    fetchPostById: fetchPostById,
    getPageFromQuery: getPageFromQuery,
    buildPostUrl: buildPostUrl,
    buildPageUrl: buildPageUrl,
    formatDate: formatDate,
  };
})();
