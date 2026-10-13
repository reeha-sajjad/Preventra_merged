// Shared-login token handling.
//
// This app does not sign anyone in. The portal does that, then redirects here
// with the token attached. All this module does is catch it on arrival, keep it
// for the session, and hand it to the API client.
//
// The token arrives in the URL *fragment* (`#token=...`), not the query string.
// A fragment is never sent to the server, so it stays out of access logs, proxy
// logs and Referer headers. It is stripped from the address bar immediately, so
// it cannot be copied off a shared screen or saved into a bookmark.

const STORAGE_KEY = 'shared_auth_token';

// The portal is the sign-in surface for both products. When it is configured,
// this app has somewhere to send people who are not signed in - and somewhere
// to send them back to on the way out. Without it (local development against a
// bare dashboard) the app behaves as it always did.
const PORTAL_URL = (import.meta.env.VITE_PORTAL_URL || '').replace(/\/$/, '');
const GLP1_URL = (import.meta.env.VITE_GLP1_URL || '').replace(/\/$/, '');
/**
 * Called once before the app renders, before requireSession(). Checks for an
 * incoming `#signout=1&chain=...` handoff from another app in the shared
 * logout relay. Clears this app's own token, then either forwards the
 * remaining chain to the next app, or returns to the portal if the chain is
 * empty. Returns true when it has taken over navigation, so main.jsx can skip
 * rendering (and skip requireSession(), which would otherwise redirect too).
 */
export function receiveSignout() {
  const hash = window.location.hash || '';
  if (!hash.includes('signout=')) return false;

  const params = new URLSearchParams(hash.replace(/^#/, ''));
  if (!params.get('signout')) return false;

  window.history.replaceState(null, '', window.location.pathname);
  clearToken();

  const chain = (params.get('chain') || '').split(',').filter(Boolean);
  if (chain.length > 0) {
    const [nextKey, ...rest] = chain;
    const nextUrl = nextKey === 'glp1' ? GLP1_URL : null;
    if (nextUrl) {
      window.location.replace(
        `${nextUrl}/#signout=1${rest.length ? `&chain=${rest.join(',')}` : ''}`
      );
      return true;
    }
  }
  window.location.replace(PORTAL_URL || window.location.pathname);
  return true;
}
// sessionStorage, not localStorage: the token dies with the tab. A demo laptop
// left open does not stay signed in, and there is no sign-out flow yet to undo
// it if it did.
const store = () => {
  try {
    return window.sessionStorage;
  } catch {
    return null; // private mode, or a blocked origin - degrade, do not throw
  }
};

/** Read a `token` value out of the URL fragment, if one is there. */
function tokenFromFragment() {
  const hash = window.location.hash || '';
  if (!hash.includes('token=')) return null;
  const value = new URLSearchParams(hash.replace(/^#/, '')).get('token');
  return value && value.trim() ? value.trim() : null;
}

/**
 * Capture a token handed over by the portal. Call once, before the app renders.
 *
 * Returns true when a token was captured on this load, so a caller can tell a
 * fresh hand-off from an existing session if it ever needs to.
 */
export function captureTokenFromUrl() {
  const token = tokenFromFragment();
  if (!token) return false;

  store()?.setItem(STORAGE_KEY, token);

  // Drop the fragment without adding a history entry, so Back does not walk the
  // user onto a URL that still carries their credential.
  const { pathname, search } = window.location;
  window.history.replaceState(null, '', `${pathname}${search}`);
  return true;
}

/** The token for this session, or null when nobody arrived through the portal. */
export function getToken() {
  return store()?.getItem(STORAGE_KEY) || null;
}

/** Swap in a fresh token for this session - after a password change, the
 *  auth service hands back one carrying the updated account. Same sign-in,
 *  same expiry; only the claims are newer. */
export function replaceToken(token) {
  if (token) store()?.setItem(STORAGE_KEY, token);
}

export function clearToken() {
  store()?.removeItem(STORAGE_KEY);
}

/** Has this token passed its expiry? A token the backend will refuse is not a
 *  session, and treating it as one is what produces a dashboard that renders
 *  and then 401s on every request. */
function isExpired(claims) {
  const exp = claims?.exp;
  return typeof exp === 'number' && exp * 1000 <= Date.now();
}

/**
 * Sign out: drop the token and go back to the portal.
 *
 * Deliberately NOT the app-switcher hand-off - no `#token=` on the URL. The
 * point of signing out is to arrive at the portal without a session.
 *
 * With no portal configured there is nowhere to go, so reload instead: the
 * token is gone either way, and the reload makes that visible rather than
 * leaving a dashboard on screen that looks signed in.
 *
 * `replace()`, not a normal navigation: a normal navigation pushes a new
 * history entry and leaves the dashboard as the previous one, so Back returns
 * to it - often served from bfcache without re-running the session check,
 * which looks exactly like sign-out having failed. `replace()` overwrites the
 * dashboard entry instead, so Back skips past it.
 */
export function signOut() {
  clearToken();
  // `#signout` tells the portal to drop its own copy of the session. It keeps a
  // separate one per tab, so without this you land on the tile screen still
  // signed in - which reads as the sign-out having done nothing.
  window.location.replace(PORTAL_URL ? `${PORTAL_URL}/#signout=1&chain=glp1` : window.location.pathname);
}

/**
 * Called once before the app renders. Sends anyone without a usable session to
 * the portal, and returns true when it has done so, so the caller can skip
 * rendering a screen that is about to be navigated away from.
 *
 * This is a routing convenience, not a security control. The data is protected
 * by the API key and, on the protected routes, by the token signature the
 * backend checks - never by whether this function ran.
 */
export function requireSession() {
  if (!PORTAL_URL) return false;          // nowhere to send anyone; carry on

  // A pending account, or one still on a temporary password, also goes back:
  // the portal shows it the "waiting for approval" or "set a new password"
  // screen. The backend refuses it data either way.
  const claims = readClaims();
  if (claims && !isExpired(claims) && claims.status !== 'pending'
      && !claims.must_change_password) return false;

  clearToken();                            // expired tokens do not linger
  window.location.replace(PORTAL_URL);     // replace: Back must not return here
  return true;
}

/**
 * Claims carried by the current token, without verifying it.
 *
 * Read-only convenience for the UI - to greet someone by name, or to hide a
 * control they cannot use. It proves nothing: the payload is base64, not
 * ciphertext, and anyone can edit it. Every decision that matters is made by
 * the backend, which checks the signature. Never gate anything real on this.
 */
export function readClaims() {
  const token = getToken();
  if (!token) return null;
  try {
    const payload = token.split('.')[1];
    return JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')));
  } catch {
    return null;
  }
}