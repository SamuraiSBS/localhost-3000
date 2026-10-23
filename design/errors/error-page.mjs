const escapeHtml = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#39;");

/** Render one static error page from the shared project template. */
export function renderErrorPage({ code, title, message, retry = false }) {
  if (!title || !message) {
    throw new TypeError("An error page needs a title and a message.");
  }

  const safeTitle = escapeHtml(title);
  const safeMessage = escapeHtml(message);
  const status = code
    ? `        <p class="error-page__code">${escapeHtml(code)}</p>\n`
    : "";
  const retryAction = retry
    ? '          <a class="ds-button" href="">Повторить попытку</a>\n'
    : "";
  const pageTitle = code
    ? `${escapeHtml(code)} — ${safeTitle} — localhost3000`
    : `${safeTitle} — localhost3000`;

  return `<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="dark">
    <meta name="description" content="${safeMessage}">
    <title>${pageTitle}</title>
    <link rel="icon" href="data:,">
    <link rel="preload" href="../assets/fonts/pp-neue-machina-inktrap-variable.woff2" as="font" type="font/woff2" crossorigin>
    <link rel="stylesheet" href="../fonts.css">
    <link rel="stylesheet" href="../tokens.css">
    <link rel="stylesheet" href="../foundation.css">
    <link rel="stylesheet" href="./error-page.css">
  </head>
  <body class="ds-root error-document">
    <main class="error-page" aria-labelledby="error-title">
${status}        <h1 id="error-title">${safeTitle}</h1>
        <p class="error-page__message">${safeMessage}</p>
        <div class="error-page__actions">
${retryAction}          <a class="ds-button${retry ? " ds-button--secondary" : ""}" href="../index.html">Вернуться на главную</a>
        </div>
    </main>
  </body>
</html>
`;
}
