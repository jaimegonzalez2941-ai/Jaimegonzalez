/** Cookie-authenticated writes must come from the same browser origin.
 * Linking has no request body; all data-bearing actions additionally require JSON.
 */
export function isSameOriginMutation(input: {
  origin?: string; host?: string; fetchSite?: string; contentType?: string; path: string;
}): boolean {
  if (!input.origin || !input.host || input.fetchSite === "cross-site") return false;
  try {
    if (new URL(input.origin).host !== input.host || !/^https?:$/.test(new URL(input.origin).protocol)) return false;
  } catch { return false; }
  return input.path === "/link" || /^application\/json(?:;|$)/i.test(input.contentType ?? "");
}
