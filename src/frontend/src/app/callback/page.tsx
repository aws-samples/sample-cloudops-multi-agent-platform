// Next.js dev-mode routing reaches this file when Cognito redirects to
// /callback/?code=.... In production (static export with `trailingSlash: true`)
// every path serves index.html and the callback logic in MyRuntimeProvider
// runs against whatever URL is loaded — no dedicated route file is needed.
//
// This file exists purely so `next dev` doesn't 404 on /callback/. Re-exports
// the root page so MyRuntimeProvider's ?code= handler kicks in.
export { default } from "../page";
