// The published page may be behind Naver's multiply-encoded iframe redirect.
export function publishedLocationReady(state) {
  const current = state || {
    url: location.href,
    editorPresent: !!document.querySelector('textarea[placeholder="제목을 입력해 주세요."]'),
  };
  let url = current.url;
  for (let n = 0; n < 4; n++) {
    try { url = decodeURIComponent(url); } catch (_) { break; }
  }
  return !current.editorPresent && /\/articles\/\d+|\/westudyssat\/\d+|articleid=\d+/.test(url);
}
