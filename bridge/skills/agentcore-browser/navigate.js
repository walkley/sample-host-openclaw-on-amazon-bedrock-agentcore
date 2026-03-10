"use strict";
const { connectBrowser, truncateContent, CONTENT_TRUNCATE_CHARS, NAV_TIMEOUT_MS } = require("./common");

async function browserNavigate(args) {
  const { url } = args;
  if (!url) return JSON.stringify({ error: "url is required" });

  try {
    const { page } = await connectBrowser();
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT_MS });
    const title = await page.title();

    // Extract readable text — remove script/style noise
    const content = await page.evaluate(() => {
      const clone = document.cloneNode(true);
      clone.querySelectorAll("script, style, noscript, iframe").forEach(el => el.remove());
      return clone.body?.innerText || clone.body?.textContent || "";
    });

    return JSON.stringify({
      url: page.url(),
      title,
      content: truncateContent(content.replace(/\s+/g, " ").trim(), CONTENT_TRUNCATE_CHARS),
    });
  } catch (err) {
    if (err.message.includes("Browser session not available")) {
      return JSON.stringify({ error: "Browser is not available. The enable_browser feature must be enabled in CDK configuration." });
    }
    return JSON.stringify({ error: `Navigation failed: ${err.message}` });
  }
}

// CLI entrypoint (called by OpenClaw)
const args = JSON.parse(process.argv[2] || "{}");
browserNavigate(args).then(console.log).catch(err => console.log(JSON.stringify({ error: err.message })));
